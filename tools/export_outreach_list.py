"""
tools/export_outreach_list.py — a ranked, usable contact list for TODAY.

WHY THIS EXISTS. The committee reported that phone numbers were "directly getting
cut". The store explains it: of 278 phones, **157 were scraped off job-listing pages**
(internshala 153, builtin 3, indeed 1) and only **121 came from a company's own
website**. A listing-page number belongs to whoever posted the ad, or to the portal —
not to the company's HR desk. Dialling those in list order produces exactly the
observed result.

This tool does not find anything new. It RANKS what is already verified, so effort
goes to the contacts most likely to answer, and the known-bad ones stop wasting calls.

TIERS, best first:

  A  HR-family address on the company's own confirmed domain, MX verified.
     (campus@, hr@, careers@, talent@ ...) — addresses a company publishes precisely
     so people can write to them about hiring.
  B  hr_database.csv rows marked verified. Hand-compiled and MX-checked. Phase A
     measured this file out-producing all ten live scrapers combined.
  C  Other own-domain addresses, MX verified (info@, contact@) — real company mail,
     less targeted; ask to be forwarded to HR.
  D  Phones taken from the company's OWN website. Genuine switchboards.

  EXCLUDED, and why:
     * 157 listing-page phones — not the company's number. This is the cut-call set.
     *  43 off-domain emails — the contact-to-domain attribution is unresolved; some
       belong to a different company entirely. Sending to these risks emailing the
       wrong employer under the college's name.

Read-only. No database writes. Written OUTSIDE the repo — it is full of real contacts.

    python tools/export_outreach_list.py
"""

import argparse
import csv
import os
import sys
from collections import Counter
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

FIELDS = ['tier', 'why', 'channel', 'contact', 'person', 'title', 'company',
          'company_domain', 'mumbai', 'office_line', 'verified', 'source', 'source_url',
          'confidence', 'status', 'notes']

# Tier order. 'A+' is a NAMED HR person from the company HR-contact list — the only
# contacts in the store with a real person and title attached. Sorted with a rank map,
# not by string: as strings 'A' sorts before 'A+'.
TIER_RANK = {'A+': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4}
MUMBAI_RANK = {'A_confirmed_mumbai_office': 0, 'B_only_city_named_is_mumbai': 1,
               'C_mumbai_listed_among_others': 2}

LISTING_SOURCES = {'internshala', 'builtin', 'indeed', 'jd', 'justdial', 'im',
                   'indiamart', 'sulekha', 'tradeindia'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write contacts inside the repo.')
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    db = D._get_db()
    docs = list(db[args.collection].find({}))
    rows, excluded = [], Counter()

    for d in docs:
        dom = (d.get('domain') or '').lower()
        name = d.get('company_name') or ''
        for c in (d.get('contacts') or []):
            v = c.get('value_norm') or ''
            src = (c.get('source') or '').lower()
            mx = (c.get('verification') or {}).get('mx_ok')
            conf = c.get('confidence')

            if c.get('type') == 'email':
                # Named HR people from the HR-contact list. Their companies hold `key:`
                # identity (no confirmed website yet), which the own-domain rule below
                # would exclude wholesale — so all 1,823 imported contacts were invisible
                # here. The person, title and work email arrive TOGETHER from the list,
                # so the attribution comes from the source, not from a domain guess.
                if src == 'hr_pdf':
                    if mx is not True:
                        excluded['named_hr_domain_takes_no_mail'] += 1
                        continue
                    loc = d.get('location') or {}
                    rows.append({'tier': 'A+',
                                 'why': 'named HR person (company HR list), MX verified',
                                 'channel': 'email', 'contact': v,
                                 'person': c.get('person_name') or '',
                                 'title': c.get('role_title') or '', 'company': name,
                                 'company_domain': dom or ', '.join(d.get('observed_domains') or []),
                                 'mumbai': loc.get('mumbai') or '',
                                 'office_line': ' ; '.join(loc.get('mumbai_office_lines') or []),
                                 'verified': 'MX ok', 'source': 'hr_pdf',
                                 'source_url': c.get('source_url') or '',
                                 'confidence': conf, 'status': '', 'notes': ''})
                    continue
                if not dom:
                    excluded['email_no_confirmed_domain'] += 1
                    continue
                ed = v.split('@')[-1]
                own = (ed == dom or ed.endswith('.' + dom))
                if not own:
                    excluded['email_offdomain_unresolved'] += 1
                    continue
                if mx is not True:
                    excluded['email_mx_unverified'] += 1
                    continue
                cls = D.classify_email(v)
                if cls['is_hr_family']:
                    tier, why = 'A', 'HR address on the company own domain, MX verified'
                else:
                    tier, why = 'C', 'company own domain, MX verified — ask for HR'
                rows.append({'tier': tier, 'why': why, 'channel': 'email',
                             'contact': v, 'company': name, 'company_domain': dom,
                             'verified': 'MX ok', 'source': c.get('source') or '',
                             'source_url': c.get('source_url') or '',
                             'confidence': conf, 'status': '', 'notes': ''})
            else:
                if src in LISTING_SOURCES:
                    excluded['phone_from_listing_page'] += 1
                    continue
                rows.append({'tier': 'D', 'why': 'phone published on the company own website',
                             'channel': 'phone', 'contact': c.get('value') or v,
                             'company': name, 'company_domain': dom,
                             'verified': 'on own site', 'source': c.get('source') or '',
                             'source_url': c.get('source_url') or '',
                             'confidence': conf, 'status': '', 'notes': ''})

    # Tier B — the hand-compiled file, which Phase A measured as the best single source.
    hrdb = REPO_ROOT / 'hr_database.csv'
    if hrdb.exists():
        with hrdb.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if (r.get('status') or '').strip().lower() != 'verified':
                    excluded['hrdb_not_verified'] += 1
                    continue
                mail = (r.get('hr_email') or '').strip()
                if not mail:
                    continue
                rows.append({'tier': 'B', 'why': 'hand-compiled HR database, MX verified',
                             'channel': 'email', 'contact': mail.lower(),
                             'company': (r.get('company') or '').strip(),
                             'company_domain': mail.split('@')[-1].lower(),
                             'verified': f"MX ok · {r.get('last_verified','')}",
                             'source': 'hrdb', 'source_url': '',
                             'confidence': '', 'status': '', 'notes': ''})

    # dedupe on (channel, contact); keep the best tier
    best = {}
    for r in rows:
        k = (r['channel'], r['contact'])
        if k not in best or TIER_RANK[r['tier']] < TIER_RANK[best[k]['tier']]:
            best[k] = r
    rows = sorted(best.values(), key=lambda r: (TIER_RANK[r['tier']],
                                              MUMBAI_RANK.get(r.get('mumbai', ''), 9),
                                              r['company'].lower()))

    path = out_dir / 'outreach_list.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)

    tiers = Counter(r['tier'] for r in rows)
    print(f'\n{"="*72}\nRANKED OUTREACH LIST\n{"="*72}')
    print(f'  A+ NAMED HR person, MX ok          : {tiers.get("A+",0):4}   <- start here')
    print(f'       of which confirmed Mumbai office: '
          f'{sum(1 for r in rows if r["tier"] == "A+" and r.get("mumbai") == "A_confirmed_mumbai_office"):4}')
    print(f'  A  HR address, own domain, MX ok   : {tiers.get("A",0):4}')
    print(f'  B  hand-compiled hrdb, verified    : {tiers.get("B",0):4}')
    print(f'  C  own domain, MX ok (info@ etc.)  : {tiers.get("C",0):4}')
    print(f'  D  phone from the company website  : {tiers.get("D",0):4}')
    print(f'     ------------------------------------')
    print(f'     TOTAL USABLE                    : {len(rows):4}')
    print(f'\n  EXCLUDED (and why):')
    for k, v in excluded.most_common():
        print(f'    {v:4}  {k}')
    print(f'\n  written: {path}')
    print(f'  (outside the repo — real contact data)')
    print(f'\n  Email tiers A-C first. Phones (D) are switchboards: ask for the HR or')
    print(f'  campus-hiring desk by name, do not pitch reception.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
