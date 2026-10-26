"""
tools/import_hr_pdf.py — bring the CompanyWise HR-contact PDF into the company store.

WHAT THE PDF IS. 1,842 rows of named HR people — name, work email, title, company —
parsed to ../placementradar-bench/hrcontact_pdf_parsed.json (1,838 usable; 4 had
emails truncated mid-domain in the PDF itself). It is the project's first source of
NAMED HR contacts: every row carries a person and 96% carry an HR/talent title, which
finally activates the two confidence terms that were inert for the whole corpus
(person_name +0.20, role_title +0.15).

VERIFICATION FIRST. Every email's domain is MX-checked before it is stored, reusing
tools/verify_contacts.py's per-domain cache. SMTP is not attempted (port 25 is blocked
from this machine). A contact whose domain has no mail server is still stored — with
mx_ok=False recorded — and the outreach export already excludes it.

IDENTITY — matched on the EMAIL DOMAIN first, never on the company name alone.
`companies_v5` holds a known-wrong document, `domain:datakalp.com`, filed under the
company_key `ntt-data`. A name lookup would attach the PDF's NTT DATA HR people to it
and compound the error. So:

  1. email domain == an existing document's CONFIRMED domain -> that document
  2. a `key:<company_key>` document exists                  -> that document
  3. another document already uses this company_key under a DIFFERENT domain
                                                            -> CONFLICT: reported,
                                                               not merged
  4. otherwise                                              -> new `key:` document

The email domain is recorded as `observed_domains` evidence, not as a `domain:`
identity — a purchased list's assertion is not a confirmation.

NOT WRITTEN TO hr_database.csv. That file lives INSIDE the repo; adding 1,838 named
individuals there would put real personal data where `zip -r` reaches it.

DRY-RUN BY DEFAULT.
    python tools/import_hr_pdf.py
    python tools/import_hr_pdf.py --run
"""

import argparse
import concurrent.futures as cf
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'tools'))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('MONGO_URI', 'mongodb://127.0.0.1:27017/placementradar_dev')
os.environ.pop('GH_PAT', None)

import database as D  # noqa: E402
import db_guard  # noqa: E402
import domain_utils as DU  # noqa: E402
from verify_contacts import MXChecker, verify_email  # noqa: E402

SOURCE = 'hr_pdf'
SOURCE_LABEL = 'CompanyWise HR contact.pdf'

# Location labels, read from tools/locate_companies.py output. Kept as tiers rather
# than a single "city" because the evidence differs in strength: a 022 office line on
# the company's own site is an address; the word "Mumbai" in a list of ten offices is not.
MUMBAI_TIER = {
    'strong':  'A_confirmed_mumbai_office',      # 022 line or 400xxx PIN on own site
    'only':    'B_only_city_named_is_mumbai',
    'listed':  'C_mumbai_listed_among_others',
    'not':     'not_mumbai',
    'none':    'unknown_no_city_on_site',
    'unreach': 'unknown_site_unreachable',
}


def load_locations(path):
    """domain -> location dict, from company_locations.csv (absent file -> {})."""
    import csv
    from datetime import date
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    for r in csv.DictReader(p.open(encoding='utf-8')):
        ev, st = r['mumbai_evidence'], r['status']
        if st in ('Mumbai only', 'Mumbai + others'):
            tier = (MUMBAI_TIER['strong'] if ('landline' in ev or 'pincode' in ev)
                    else MUMBAI_TIER['only'] if st == 'Mumbai only' else MUMBAI_TIER['listed'])
        else:
            tier = {'Not Mumbai': MUMBAI_TIER['not'], 'No location': MUMBAI_TIER['none'],
                    'Unreachable': MUMBAI_TIER['unreach']}.get(st, MUMBAI_TIER['none'])
        out[r['domain'].lower()] = {
            'cities': [c.strip() for c in r['cities_found'].split(',') if c.strip()],
            'mumbai': tier,
            'mumbai_evidence': ev or None,
            'mumbai_office_lines': [x.strip() for x in r['mumbai_office_lines'].split(';')
                                    if x.strip()],
            'source': 'company website (tools/locate_companies.py)',
            'checked_at': date.today().isoformat(),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--source', default=str(REPO_ROOT.parent / 'placementradar-bench' /
                                            'hrcontact_pdf_parsed.json'))
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    people = json.load(open(args.source, encoding='utf-8'))
    junk = [p for p in people if DU.is_junk_contact_domain(p['email'])]
    people = [p for p in people if not DU.is_junk_contact_domain(p['email'])]

    # ---- MX: one DNS lookup per distinct domain, in parallel ----
    mx = MXChecker()
    domains = sorted({p['email'].split('@')[-1].lower() for p in people})
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(mx.check, domains))
    mx_by_dom = {d: mx.check(d) for d in domains}
    mx_stat = Counter('ok' if v[0] is True else ('dead' if v[0] is False else 'unknown')
                      for v in mx_by_dom.values())

    # ---- targets ----
    db = D._get_db()
    docs = list(db[args.collection].find({}, {'company_key': 1, 'domain': 1,
                                              'contacts.value_norm': 1}))
    by_domain = {(d.get('domain') or '').lower(): d for d in docs if d.get('domain')}
    by_keyid = {d['_id']: d for d in docs if str(d['_id']).startswith('key:')}
    keys_in_use = defaultdict(list)
    for d in docs:
        keys_in_use[d.get('company_key')].append(d)

    groups = defaultdict(list)            # target _id -> [contact envelopes]
    names, observed, stats, conflicts = {}, {}, Counter(), []
    for p in people:
        email = p['email'].lower()
        dom = email.split('@')[-1]
        company = DU.strip_company_badges(p['company']).strip(' ,.') or dom
        key = DU.company_key(company)
        ok, detail = mx_by_dom.get(dom, (None, 'unchecked'))
        verif = verify_email(email, mx)
        c = D.build_contact('email', email, SOURCE, person_name=p['name'] or None,
                            role_title=p['title'] or None, source_domain=dom,
                            source_url=SOURCE_LABEL, verification=verif)
        if not c:
            stats['unusable'] += 1
            continue

        if dom in by_domain:                                   # 1. confirmed domain
            target, how = by_domain[dom]['_id'], 'existing_by_domain'
        elif key and f'key:{key}' in by_keyid:                 # 2. key: document
            target, how = f'key:{key}', 'existing_by_key'
        elif key and keys_in_use.get(key):                     # 3. name collision
            other = keys_in_use[key][0]
            conflicts.append((company, email, other['_id']))
            stats['conflict'] += 1
            continue
        elif key:                                              # 4. new
            target, how = f'key:{key}', 'new_company'
        else:
            stats['unkeyable'] += 1
            continue

        have = {x.get('value_norm') for x in
                ((by_domain.get(dom) or by_keyid.get(target) or {}).get('contacts') or [])}
        if c['value_norm'] in have:
            stats['already_stored'] += 1
            continue
        groups[target].append(c)
        names.setdefault(target, company)
        observed.setdefault(target, set()).add(dom)
        stats[how] += 1

    contacts = [c for cs in groups.values() for c in cs]
    conf = Counter(round(c['confidence'], 2) for c in contacts)
    print(f'\n{"=" * 72}\nHR-CONTACT PDF -> {args.collection}  (DRY RUN unless --run)\n{"=" * 72}')
    print(f'  people in PDF (usable)          : {len(people)}   (junk domains dropped: {len(junk)})')
    print(f'  distinct email domains          : {len(domains)}')
    print(f'    MX ok / dead / unknown        : {mx_stat["ok"]} / {mx_stat["dead"]} / {mx_stat["unknown"]}')
    print(f'\n  contacts to add                 : {len(contacts)}')
    print(f'    into EXISTING companies (domain match) : {stats["existing_by_domain"]}')
    print(f'    into EXISTING key: companies           : {stats["existing_by_key"]}')
    print(f'    creating NEW companies                 : {stats["new_company"]}  '
          f'({sum(1 for t in groups if t not in by_keyid and t not in {d["_id"] for d in by_domain.values()})} new documents)')
    print(f'  already stored (skipped)        : {stats["already_stored"]}')
    print(f'  NAME CONFLICTS (not merged)     : {stats["conflict"]}')
    for comp, em, other in conflicts[:8]:
        print(f'      {comp[:30]:32} {em[:36]:38} vs existing {other}')
    locs = load_locations(Path(args.out_dir) / 'company_locations.csv')
    loc_for = {}
    for target, doms in observed.items():
        for d in sorted(doms):
            if d in locs:
                loc_for[target] = locs[d]
                break
    tiers = Counter(v['mumbai'] for v in loc_for.values())
    print(f'\n  location attached to companies  : {len(loc_for)}')
    for k in sorted(tiers):
        print(f'      {k:34} {tiers[k]}')

    print(f'\n  confidence of the new contacts  : {dict(sorted(conf.items()))}')
    print('    (person_name +0.20 and role_title +0.15 fire for the first time in this corpus)')
    print('  hr_database.csv                 : NOT written (it lives inside the repo)')

    if not args.run:
        print('\nDRY RUN — database untouched. Re-run with --run once approved.')
        return 0

    wdb = D._get_write_db()
    db_guard.assert_expected_collection(D.COMPANIES_COLLECTION, args.collection,
                                        operation='import HR-contact PDF')
    dump = db_guard.dump_collections(wdb, [args.collection], args.out_dir, label='pre-hr-pdf')
    added = 0
    for target, cs in groups.items():
        _, n = D.upsert_company_contacts(names[target], cs, args.collection, domain=None,
                                         observed_domain=sorted(observed[target])[0],
                                         db=wdb, doc_id=target)
        added += n
        if target in loc_for:
            # Additive field only — no existing field is removed or overwritten
            # except `location` itself, which this tool owns.
            wdb[args.collection].update_one({'_id': target},
                                            {'$set': {'location': loc_for[target]}})
    bad = D.assert_identity_shape(args.collection, db=wdb)
    print(f'\n  contacts added      : {added}')
    print(f'  restore point       : {dump}')
    print(f'  identity shape check: {len(bad)} malformed _id(s)')
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
