"""
tools/backfill_companies.py — seed the `companies` collection from the CSV corpus.

DRY-RUN BY DEFAULT. Nothing is written without --run.

This is the first bulk write in the project, so it is gated four ways:

  1. Resolved Mongo host + database name are printed, and the shared guard in
     db_guard.py aborts unless the target is 127.0.0.1 AND placementradar_dev.
  2. Target collections are dumped to ../placementradar-bench/dumps/<ts>/ before
     any write — even when empty, so a restore point always exists.
  3. A dry run reports how many company documents and contacts WOULD be created
     and stops. ~315 companies is expected. If it reports 3,355 (per row) or 532
     (per triple), company_key normalization is wrong and the write must not run.
  4. It is idempotent: a second run must add zero contacts (--verify-idempotent).

Contains no delete, drop or remove operation. Appends only.

    python tools/backfill_companies.py                      # dry run
    python tools/backfill_companies.py --run                # write
    python tools/backfill_companies.py --verify-idempotent  # write twice, assert delta 0
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# Must be set before `database` is imported (it raises at import time otherwise).
os.environ.setdefault('MONGO_URI', 'mongodb://127.0.0.1:27017/placementradar_dev')
os.environ.pop('GH_PAT', None)

import pandas as pd  # noqa: E402
import database as D  # noqa: E402
import db_guard  # noqa: E402
from tools.bench import MANIFEST, _cell  # noqa: E402

TARGET_COLLECTIONS = [D.COMPANIES_COLLECTION]
SOURCE_ID = 'csv-backfill'
EXPECTED_MIN, EXPECTED_MAX = 300, 330     # ~315 companies
WRONG_SHAPES = {3355: 'per-ROW (company_key not applied)',
                532: 'per-TRIPLE (company_key not applied)'}


def load_corpus():
    frames = [pd.read_csv(REPO_ROOT / rel, on_bad_lines='skip', low_memory=False)
              for rel, _ in MANIFEST]
    return pd.concat(frames, ignore_index=True)


def plan_backfill(df):
    """Group the corpus by company_key and build contact envelopes. Pure."""
    groups = defaultdict(lambda: {'name': '', 'domain': '', 'emails': {}, 'phones': {}})
    skipped_no_key = 0

    for rec in df.to_dict('records'):
        raw_name = _cell(rec.get('company'))
        key = D.company_key(raw_name)
        if not key:
            skipped_no_key += 1
            continue
        g = groups[key]
        if not g['name']:
            g['name'] = D.strip_company_badges(raw_name).strip()
        if not g['domain']:
            g['domain'] = _cell(rec.get('company_url'))

        for e in _cell(rec.get('emails')).split(','):
            norm = D.normalize_contact_value('email', e)
            # Listing-site support addresses and template placeholders are never
            # company HR contacts (complaints@internshala.com appeared on 13
            # companies; first.lastname@ is a template).
            if norm and not D.is_junk_contact_domain(norm):
                g['emails'].setdefault(norm, e.strip())
        for p in _cell(rec.get('phone')).split(','):
            norm = D.normalize_contact_value('phone', p)
            if norm:
                g['phones'].setdefault(norm, p.strip())

    # A contact value appearing on many companies is a shared switchboard, not a
    # company contact — brief §4 penalises it (-0.40) rather than discarding it.
    spread = defaultdict(set)
    for key, g in groups.items():
        for norm in g['emails']:
            spread[('email', norm)].add(key)
        for norm in g['phones']:
            spread[('phone', norm)].add(key)

    planned = {}
    for key, g in groups.items():
        # Returns '' for job-board hosts, so in.indeed.com / in.linkedin.com can
        # never be recorded as a company's own domain.
        domain = D.clean_company_domain(g['domain'])
        contacts = []
        for norm, original in g['emails'].items():
            c = D.build_contact('email', original or norm, SOURCE_ID,
                                company_domain=domain or None,
                                seen_on_n_companies=len(spread[('email', norm)]))
            if c:
                contacts.append(c)
        for norm, original in g['phones'].items():
            c = D.build_contact('phone', original or norm, SOURCE_ID,
                                company_domain=domain or None,
                                seen_on_n_companies=len(spread[('phone', norm)]))
            if c:
                contacts.append(c)
        planned[key] = {'name': g['name'], 'domain': domain, 'contacts': contacts}

    return planned, skipped_no_key, spread


def report(planned, skipped, spread, df_rows):
    n_companies = len(planned)
    n_contacts = sum(len(v['contacts']) for v in planned.values())
    n_emails = sum(1 for v in planned.values() for c in v['contacts'] if c['type'] == 'email')
    n_phones = n_contacts - n_emails
    with_any = sum(1 for v in planned.values() if v['contacts'])
    shared = sum(1 for k, v in spread.items() if len(v) > 1)

    print(f'\n{"="*72}\nBACKFILL PLAN\n{"="*72}')
    print(f'  corpus rows                : {df_rows}')
    print(f'  rows skipped (no key)      : {skipped}')
    print(f'  company documents to write : {n_companies}')
    print(f'  companies with >=1 contact : {with_any}  ({with_any/max(n_companies,1)*100:.1f}%)')
    print(f'  contacts to write          : {n_contacts}  ({n_emails} email, {n_phones} phone)')
    print(f'  values seen on >1 company  : {shared}  (penalised -0.40, not discarded)')

    if n_companies in WRONG_SHAPES:
        print(f'\n  !! ABORT: {n_companies} matches the {WRONG_SHAPES[n_companies]} shape.')
        print(f'     company_key normalization is wrong. Refusing to write.')
        return False
    if not (EXPECTED_MIN <= n_companies <= EXPECTED_MAX):
        print(f'\n  !! ABORT: {n_companies} is outside the expected '
              f'{EXPECTED_MIN}-{EXPECTED_MAX} range. Refusing to write.')
        return False
    print(f'\n  OK: {n_companies} is within the expected {EXPECTED_MIN}-{EXPECTED_MAX} range.')
    return True


def write(planned, db):
    added_total = 0
    for key, v in planned.items():
        _, added = D.upsert_company_contacts(
            v['name'], v['contacts'], D.COMPANIES_COLLECTION,
            domain=v['domain'] or None, db=db)
        added_total += added
    return added_total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true', help='actually write')
    ap.add_argument('--verify-idempotent', action='store_true',
                    help='write, then write again and assert zero contacts added')
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()
    do_write = args.run or args.verify_idempotent

    print(f'MONGO_URI -> {os.environ["MONGO_URI"]}')
    df = load_corpus()
    planned, skipped, spread = plan_backfill(df)
    safe = report(planned, skipped, spread, len(df))
    if not safe:
        return 2

    if not do_write:
        print('\nDRY RUN — nothing written. Re-run with --run once this number is approved.')
        return 0

    # --- gate 1: resolved target, asserted by the shared guard ---
    db = D._get_write_db()

    # --- gate 2: restore point before any write ---
    db_guard.dump_collections(db, TARGET_COLLECTIONS, args.out_dir, label='pre-backfill')

    before_c = db[D.COMPANIES_COLLECTION].count_documents({})
    print(f'\nwriting... (companies before: {before_c})')
    added1 = write(planned, db)
    after_c = db[D.COMPANIES_COLLECTION].count_documents({})
    print(f'  pass 1: +{added1} contacts, companies {before_c} -> {after_c}')

    if args.verify_idempotent:
        added2 = write(planned, db)
        after2 = db[D.COMPANIES_COLLECTION].count_documents({})
        print(f'  pass 2: +{added2} contacts, companies {after_c} -> {after2}')
        ok = (added2 == 0 and after2 == after_c)
        print(f'\nIDEMPOTENT: {"PASS" if ok else "FAIL"} '
              f'(second pass added {added2} contacts, expected 0)')
        if not ok:
            return 1

    total_contacts = sum(len(d.get('contacts') or [])
                         for d in db[D.COMPANIES_COLLECTION].find({}, {'contacts': 1}))
    print(f'\nfinal: {db[D.COMPANIES_COLLECTION].count_documents({})} companies, '
          f'{total_contacts} contacts')
    return 0


if __name__ == '__main__':
    sys.exit(main())
