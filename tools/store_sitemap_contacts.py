"""
tools/store_sitemap_contacts.py — Phase F step 1: persist Phase E's discoveries.

Phase E found 93 own-domain emails and 121 phones, but they live only in
sitemap_discovery_cap20.json — they must be in companies_v3 before they can be
verified. This is the gated write that puts them there.

Append-only: reuses database.upsert_company_contacts(), which dedups by
(type, value_norm) and never overwrites. Contains no delete/drop/remove/$pull.

Off-domain emails are NOT stored — they are an unresolved attribution decision and
stay in sitemap_review_queue.csv until a human rules on them.

DRY-RUN BY DEFAULT.

    python tools/store_sitemap_contacts.py            # dry run
    python tools/store_sitemap_contacts.py --run
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('MONGO_URI', 'mongodb://127.0.0.1:27017/placementradar_dev')
os.environ.pop('GH_PAT', None)

import database as D  # noqa: E402
import db_guard  # noqa: E402

SOURCE = 'sitemap'


def load_discoveries(out_dir):
    f = Path(out_dir) / 'sitemap_discovery_cap20.json'
    if not f.exists():
        cands = sorted(Path(out_dir).glob('sitemap_discovery*.json'))
        if not cands:
            print('FATAL: no sitemap_discovery*.json found — run tools/discover_sitemap.py')
            return None
        f = cands[-1]
    print(f'reading {f.name}')
    return json.loads(f.read_text(encoding='utf-8'))


def plan(data):
    """Build contact envelopes per company. Pure — no DB access."""
    # A value seen on many companies is a shared switchboard (brief §4: -0.40).
    spread = defaultdict(set)
    for r in data['rows']:
        for e in r['own_domain_emails']:
            spread[('email', e['value'])].add(r['company'])
        for p in r['phones']:
            spread[('phone', p['value'])].add(r['company'])

    planned, stats = {}, Counter()
    for r in data['rows']:
        contacts = []
        for e in r['own_domain_emails']:
            c = D.build_contact('email', e['value'], SOURCE,
                                company_domain=r['domain'],
                                source_domain=e.get('source_domain') or r['domain'],
                                source_url=e.get('source_url'),
                                seen_on_n_companies=len(spread[('email', e['value'])]))
            if c:
                contacts.append(c); stats['emails'] += 1
        for p in r['phones']:
            c = D.build_contact('phone', p['value'], SOURCE,
                                company_domain=r['domain'],
                                source_domain=p.get('source_domain') or r['domain'],
                                source_url=p.get('source_url'),
                                seen_on_n_companies=len(spread[('phone', p['value'])]))
            if c:
                contacts.append(c); stats['phones'] += 1
        if contacts:
            planned[r['company']] = {'domain': r['domain'], 'contacts': contacts}
            stats['companies'] += 1
        stats['offdomain_skipped'] += len(r['offdomain_emails'])
    return planned, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    ap.add_argument('--collection', default='companies_v3',
                    help='intended target collection; asserted before writing')
    args = ap.parse_args()

    data = load_discoveries(args.out_dir)
    if data is None:
        return 2
    planned, stats = plan(data)

    print(f'\n{"="*70}\nPHASE F STEP 1 — PERSIST PHASE E DISCOVERIES\n{"="*70}')
    print(f'  companies to update        : {stats["companies"]}')
    print(f'  emails to append           : {stats["emails"]}')
    print(f'  phones to append           : {stats["phones"]}')
    print(f'  off-domain emails SKIPPED  : {stats["offdomain_skipped"]}  '
          f'(unresolved attribution — stay in the review queue)')
    print(f'  all carry source_domain/url: yes')

    if stats['companies'] == 0 or stats['companies'] > 200:
        print(f'\n  !! ABORT: {stats["companies"]} companies is outside the expected range.')
        return 2

    if not args.run:
        print('\nDRY RUN — nothing written. Re-run with --run once approved.')
        return 0

    db = D._get_write_db()
    # State the intended target explicitly — host+db alone did not stop this tool
    # writing into companies_v2 on its first run.
    db_guard.assert_expected_collection(D.COMPANIES_COLLECTION, args.collection,
                                        operation='append contacts')
    db_guard.dump_collections(db, [D.COMPANIES_COLLECTION], args.out_dir,
                              label='pre-sitemap-contacts')
    before = sum(len(d.get('contacts') or [])
                 for d in db[D.COMPANIES_COLLECTION].find({}, {'contacts': 1}))
    added = 0
    for company, v in planned.items():
        _, n = D.upsert_company_contacts(company, v['contacts'], args.collection,
                                         domain=v['domain'], db=db)
        added += n
    after = sum(len(d.get('contacts') or [])
                for d in db[D.COMPANIES_COLLECTION].find({}, {'contacts': 1}))
    print(f'\n  contacts {before} -> {after}  (+{added} appended)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
