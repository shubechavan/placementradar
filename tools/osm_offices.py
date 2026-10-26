"""
tools/osm_offices.py — companies WITH phone numbers, from OpenStreetMap. Free, legal.

WHY THIS SOURCE. Job boards hide employer contacts by design (measured: 0 of 19
boards put a phone or email in a listing or on a job's own page). OpenStreetMap is
an open map whose contributors record offices with their published switchboard
numbers. A measured pull of the Mumbai region found 664 named offices, 105 with a
phone — Bain (+91 22 6628 9600), BCG, McKinsey, BSE, and smaller IT firms.

WHAT IT IS NOT. It is a NEW-COMPANY source, not an enrichment source: of 153 Mumbai
companies scraped from Internshala, 0 appear in OSM. OSM maps established offices;
small firms that post internships are rarely mapped.

LICENCE — OpenStreetMap data is ODbL. Free to use, including commercially, on one
condition: credit "© OpenStreetMap contributors". Every output file carries it.

IDENTITY POLICY — an OSM `website` tag is someone's assertion, not a confirmation, so
it is recorded as `observed_domains` evidence, never as a `domain:` identity. Same
rule the live scrape path follows.

No API key, no install: one HTTP POST to the public Overpass API per city.
DRY-RUN BY DEFAULT for the database merge; the CSV is always written.

    python tools/osm_offices.py --city mumbai
    python tools/osm_offices.py --city pune
    python tools/osm_offices.py --city mumbai --run      # merge into the company store
"""

import argparse
import csv
import os
import re
import sys
import time
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

import requests  # noqa: E402
import phonenumbers  # noqa: E402
import domain_utils as DU  # noqa: E402
import source_metrics as SM  # noqa: E402

SOURCE = 'osm'
ATTRIBUTION = '© OpenStreetMap contributors (ODbL) — https://www.openstreetmap.org/copyright'
UA = {'User-Agent': 'PlacementRadar/1.0 (college placement outreach tool; '
                    'https://github.com/shubechavan/placementradar)'}
ENDPOINTS = ('https://overpass-api.de/api/interpreter',
             'https://overpass.kumi.systems/api/interpreter')

# Bounding boxes (south, west, north, east). A BOX, not an area-by-name: the
# area["name"="Mumbai"] form matched no boundary and returned zero results — which
# reads as "OSM has no offices in Mumbai" when it actually means "query matched
# nothing". A box cannot silently miss.
CITIES = {
    'mumbai':    (18.89, 72.77, 19.30, 73.10),   # incl. Navi Mumbai, Thane
    'pune':      (18.40, 73.70, 18.65, 74.00),
    'bangalore': (12.83, 77.45, 13.14, 77.78),
    'delhi-ncr': (28.40, 76.95, 28.75, 77.45),
    'hyderabad': (17.30, 78.30, 17.55, 78.60),
    'chennai':   (12.90, 80.15, 13.20, 80.32),
    'kolkata':   (22.45, 88.25, 22.65, 88.45),
    'ahmedabad': (22.95, 72.45, 23.12, 72.68),
}

# Office types that are plausible EMPLOYERS of graduates. Government, embassies,
# colleges, estate agents, religious and political offices are excluded.
EMPLOYER_TYPES = {
    'it', 'company', 'consulting', 'software', 'telecommunication', 'financial',
    'employment_agency', 'research', 'engineer', 'advertising_agency', 'insurance',
    'logistics', 'accountant', 'lawyer', 'architect', 'yes',
}
EXCLUDED_TYPES = {'government', 'diplomatic', 'educational_institution', 'estate_agent',
                  'political_party', 'religion', 'association', 'coworking', 'quango',
                  'notary', 'tax_advisor'}


def fetch(bbox):
    """All office/company objects in the box. Raises rather than returning [] on
    failure — an empty list must mean "nothing there", never "the request failed"."""
    s, w, n, e = bbox
    q = (f'[out:json][timeout:150];'
         f'( nwr["office"]({s},{w},{n},{e}); nwr["company"]({s},{w},{n},{e}); );'
         f'out tags center;')
    SM.record_attempt(SOURCE)
    last = None
    for ep in ENDPOINTS:
        t0 = time.time()
        try:
            r = requests.post(ep, data={'data': q}, headers=UA, timeout=180)
        except Exception as ex:
            last = f'{ep}: {type(ex).__name__}'
            SM.record_failure(SOURCE, ex, url=ep)
            continue
        SM.record_latency(SOURCE, (time.time() - t0) * 1000)
        SM.record_http(SOURCE, r.status_code)
        if r.status_code == 200 and r.text.lstrip().startswith('{'):
            return r.json().get('elements', []), ep
        last = f'{ep}: HTTP {r.status_code} {r.text[:120]!r}'
    raise RuntimeError(f'Overpass failed on every endpoint — {last}')


def tag(t, *keys):
    for k in keys:
        if t.get(k):
            return t[k].strip()
    return ''


def phones_of(t):
    """Every valid Indian number on the object, normalised to E.164."""
    raw = ';'.join(x for x in (t.get('phone'), t.get('contact:phone'),
                               t.get('contact:mobile'), t.get('mobile')) if x)
    out = []
    for part in re.split(r'[;,/]', raw):
        part = part.strip()
        if not part:
            continue
        try:
            num = phonenumbers.parse(part, 'IN')
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(num):
            e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in out:
                out.append(e164)
    return out


def build(elements, city):
    seen, rows, skipped = {}, [], Counter()
    for el in elements:
        t = el.get('tags', {})
        name = t.get('name:en') or t.get('name') or ''
        if not name:
            skipped['no_name'] += 1
            continue
        otype = t.get('office') or ('company' if t.get('company') else 'unspecified')
        if otype in EXCLUDED_TYPES:
            skipped[f'excluded:{otype}'] += 1
            continue
        if otype not in EMPLOYER_TYPES:
            skipped[f'other:{otype}'] += 1
            continue
        key = DU.company_key(name)
        if not key:
            continue
        ph = phones_of(t)
        email = tag(t, 'email', 'contact:email').lower()
        site = tag(t, 'website', 'contact:website', 'url')
        dom = DU.clean_company_domain(site) if site else ''
        addr = ', '.join(x for x in (tag(t, 'addr:housenumber'), tag(t, 'addr:street'),
                                     tag(t, 'addr:suburb'), tag(t, 'addr:city'),
                                     tag(t, 'addr:postcode')) if x)
        osm_url = f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
        if key in seen:                       # same company mapped twice: merge
            r = seen[key]
            r['phones'] = list(dict.fromkeys(r['phones'] + ph))
            r['email'] = r['email'] or email
            r['domain'] = r['domain'] or dom
            continue
        r = {'company': name, 'company_key': key, 'office_type': otype, 'phones': ph,
             'email': email, 'website': site, 'domain': dom, 'address': addr,
             'city': city, 'osm_url': osm_url}
        seen[key] = r
        rows.append(r)
    return rows, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--city', default='mumbai', choices=sorted(CITIES))
    ap.add_argument('--run', action='store_true', help='merge into the company store')
    ap.add_argument('--with-phone-only', action='store_true',
                    help='only keep offices that have a phone number')
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write contact data inside the repo.')
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    elements, ep = fetch(CITIES[args.city])
    rows, skipped = build(elements, args.city)
    if args.with_phone_only:
        rows = [r for r in rows if r['phones']]
    SM.record_success(SOURCE, sum(len(r['phones']) + bool(r['email']) for r in rows))

    with_ph = [r for r in rows if r['phones']]
    with_em = [r for r in rows if r['email']]
    print(f'\n{"=" * 74}\nOPENSTREETMAP OFFICES — {args.city.upper()}\n{"=" * 74}')
    print(f'  endpoint            : {ep}')
    print(f'  raw OSM objects     : {len(elements)}')
    print(f'  employer offices    : {len(rows)}  (distinct companies)')
    print(f'    with a phone      : {len(with_ph)}')
    print(f'    with an email     : {len(with_em)}')
    print(f'    with a website    : {sum(1 for r in rows if r["domain"])}')
    print(f'  by office type      : {dict(Counter(r["office_type"] for r in rows).most_common(8))}')
    print(f'  excluded            : {sum(v for k, v in skipped.items() if k.startswith("excluded"))} '
          f'(government, embassies, colleges, estate agents ...)')

    csv_path = out_dir / f'osm_offices_{args.city}.csv'
    fields = ['company', 'office_type', 'phones', 'email', 'website', 'domain',
              'address', 'city', 'osm_url']
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        f.write(f'# Source: {ATTRIBUTION}\n')
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (not r['phones'], r['company'].lower())):
            w.writerow({**{k: r[k] for k in fields}, 'phones': ' ; '.join(r['phones'])})
    print(f'\n  written: {csv_path}')
    print(f'  ({ATTRIBUTION})')

    # ---- merge plan vs the company store ----
    import database as D
    import db_guard
    db = D._get_db()
    coll = D.COMPANIES_COLLECTION
    docs = list(db[coll].find({}, {'company_key': 1, 'domain': 1, 'contacts.value_norm': 1}))
    by_key = {d.get('company_key'): d for d in docs}
    by_dom = {(d.get('domain') or '').lower(): d for d in docs if d.get('domain')}
    plan, new_co, existing_co, new_ph = [], 0, 0, 0
    for r in rows:
        contacts = []
        for p in r['phones']:
            c = D.build_contact('phone', p, SOURCE, source_domain=r['domain'] or None,
                                source_url=r['osm_url'])
            if c:
                contacts.append(c)
        if r['email']:
            c = D.build_contact('email', r['email'], SOURCE,
                                source_domain=r['domain'] or None, source_url=r['osm_url'])
            if c:
                contacts.append(c)
        if not contacts:
            continue
        hit = by_key.get(r['company_key']) or (by_dom.get(r['domain']) if r['domain'] else None)
        have = {c.get('value_norm') for c in ((hit or {}).get('contacts') or [])}
        fresh = [c for c in contacts if c['value_norm'] not in have]
        if not fresh:
            continue
        existing_co += bool(hit)
        new_co += (not hit)
        new_ph += sum(1 for c in fresh if c['type'] == 'phone')
        plan.append((r, fresh, hit))
    print(f'\n  MERGE PLAN into {coll}:')
    print(f'    companies NEW to the store      : {new_co}')
    print(f'    existing companies gaining data : {existing_co}')
    print(f'    new phone numbers               : {new_ph}')
    print('    identity: key: only — OSM website recorded as evidence, not identity')

    if not args.run:
        print('\nDRY RUN — database untouched. Re-run with --run once approved.')
        return 0

    wdb = D._get_write_db()
    db_guard.assert_expected_collection(D.COMPANIES_COLLECTION, coll,
                                        operation='merge OSM offices')
    db_guard.dump_collections(wdb, [coll], args.out_dir, label=f'pre-osm-{args.city}')
    added = 0
    for r, fresh, hit in plan:
        _, n = D.upsert_company_contacts(r['company'], fresh, coll, domain=None,
                                         observed_domain=r['domain'] or None, db=wdb,
                                         doc_id=(hit or {}).get('_id'))
        added += n
    bad = D.assert_identity_shape(coll, db=wdb)
    print(f'\n  contacts added: {added}')
    print(f'  identity shape check: {len(bad)} malformed _id(s)')
    SM.flush(wdb)
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
