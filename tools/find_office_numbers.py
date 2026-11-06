"""
tools/find_office_numbers.py — office phone numbers for the HR database's companies.

Each HR contact's email domain is the company's website (priya@acme.com -> acme.com). For
every such domain this runs the app's own website scraper (contact_finder's own-domain
crawl) and keeps the numbers libphonenumber accepts. The result is the company's OFFICE
line: the committee rings it and asks for the HR person by name. Personal mobiles of
named people are never looked up.

A number found on more than one company's site (a web agency's footer, a group
switchboard) is reported and not used, because it cannot be told whose it is.

Measured before building (6 Nov 2026, 30 random domains): the scraper found an office
number on 5 of 30 sites.

DRY-RUN BY DEFAULT: writes a report to ../placementradar-bench/ and nothing to the
database. --run then sets office_phone on hr_contacts records that have none, after a
dump of the collection; it needs ALLOW_REMOTE_DB=1 for the live database. Resumable: a
domain already in the progress file is not fetched again.

    python tools/find_office_numbers.py --limit 30   # dry run on 30 domains
    python tools/find_office_numbers.py              # dry run, every domain
    python tools/find_office_numbers.py --run        # write, after reviewing the report
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import phonenumbers                      # noqa: E402

import contact_finder as C               # noqa: E402  (no database import)
import domain_utils as DU                # noqa: E402

PROGRESS = 'office_numbers_progress.jsonl'
REPORT = 'office_numbers_report.csv'


def company_domains(rows, personal_domains):
    """{domain: {'company', 'emails'}} for HR contacts whose email is on a company domain."""
    out = {}
    for r in rows:
        email = (r.get('hr_email') or '').strip().lower()
        dom = email.rpartition('@')[2]
        if ('@' not in email or dom in personal_domains or not DU.clean_company_domain(dom)
                or DU.is_junk_contact_domain(email)):
            continue
        entry = out.setdefault(dom, {'company': (r.get('company') or '').strip(), 'emails': []})
        entry['emails'].append(email)
    return out


def _digits(phone):
    return re.sub(r'\D', '', phone)[-10:]


def _is_landline(phone):
    try:
        return phonenumbers.number_type(phonenumbers.parse(phone, 'IN')) == phonenumbers.PhoneNumberType.FIXED_LINE
    except phonenumbers.NumberParseException:
        return False


def pick_numbers(found):
    """found = {domain: [phone, ...]}. Returns ({domain: phone}, {digits: [domains]}): for each
    site, its first landline (else its first number) that appears on no other company's
    site, and the shared numbers. A mobile on a contact page is often one salesperson's."""
    sites = defaultdict(set)
    for dom, phones in found.items():
        for p in phones:
            sites[_digits(p)].add(dom)
    shared = {d: sorted(doms) for d, doms in sites.items() if len(doms) > 1}
    chosen = {}
    for dom, phones in found.items():
        unshared = sorted((p for p in phones if _digits(p) not in shared), key=lambda p: not _is_landline(p))
        if unshared:
            chosen[dom] = unshared[0]
    return chosen, shared


def numbers_on_site(domain):
    res = C._scrape_company_site(f'https://{domain}', domain, True)
    return C._filter_phones(res.get('phones') or [])


def load_progress(path):
    found = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            rec = json.loads(line)
            found[rec['domain']] = rec['phones']
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--workers', type=int, default=8)      # different sites in parallel
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / '.env', override=False)
    import database as D
    import db_guard

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = list(D._get_db().hr_contacts.find({}, {'company': 1, 'hr_email': 1, '_id': 0}))
    domains = company_domains(rows, D.PERSONAL_PROVIDER_DOMAINS)
    todo = sorted(domains)[:args.limit] if args.limit else sorted(domains)
    progress = out / PROGRESS
    found = {d: p for d, p in load_progress(progress).items() if d in domains}
    pending = [d for d in todo if d not in found]
    print(f'{len(rows)} HR contacts, {len(domains)} company domains; {len(found)} already checked, '
          f'{len(pending)} to fetch')

    with progress.open('a', encoding='utf-8') as log, ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(numbers_on_site, d): d for d in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            dom = futures[fut]
            try:
                phones = fut.result()
            except Exception as e:                    # a broken site is a site with no number
                print(f'  {dom}: {type(e).__name__}')
                phones = []
            found[dom] = phones
            log.write(json.dumps({'domain': dom, 'phones': phones}) + '\n')
            log.flush()
            if i % 50 == 0 or i == len(pending):
                print(f'  {i}/{len(pending)} sites checked, {sum(bool(v) for v in found.values())} with a number')

    checked = {d: found[d] for d in todo if d in found}
    chosen, shared = pick_numbers(checked)
    with (out / REPORT).open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['company', 'domain', 'office_phone', 'all_numbers_on_site', 'hr_contacts', 'note'])
        for dom in todo:
            phones = checked.get(dom, [])
            note = ('use' if dom in chosen else 'shared with another site' if phones else 'none found')
            w.writerow([domains[dom]['company'], dom, chosen.get(dom, ''), ' | '.join(phones),
                        len(domains[dom]['emails']), note])
    contacts = sum(len(domains[d]['emails']) for d in chosen)
    print(f'\nsites checked            : {len(checked)}')
    print(f'office number to use      : {len(chosen)} companies, covering {contacts} HR contacts')
    print(f'numbers on several sites  : {len(shared)} (left out)')
    print(f'report                    : {out / REPORT}')

    if not args.run:
        print('\nDRY RUN: nothing written. Review the report, then re-run with --run.')
        return
    db = D._get_write_db()                            # refuses the live database without ALLOW_REMOTE_DB=1
    print('backup:', db_guard.dump_collections(db, ['hr_contacts'], out, label='pre-office-phone'))
    now, updated = datetime.now().isoformat(), 0
    for dom, phone in chosen.items():
        res = db.hr_contacts.update_many(
            {'hr_email': {'$in': domains[dom]['emails']}, 'office_phone': {'$in': [None, '']}},
            {'$set': {'office_phone': phone, 'office_phone_source': f'https://{dom}',
                      'office_phone_found_at': now}})
        updated += res.modified_count
    print(f'office_phone set on {updated} HR contacts (existing numbers left as they were)')


if __name__ == '__main__':
    main()
