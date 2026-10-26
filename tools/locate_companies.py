"""
tools/locate_companies.py — which city is each company in? Read-only.

The HR-contact PDF (1,838 named HR people, 1,451 company email domains) has NO
location column. The location has to be read from each company's own website, and
the HR person's email domain points straight at it.

THREE KINDS OF EVIDENCE, strongest first — and they are reported separately, because
they mean different things:

  landline  an office phone whose AREA CODE geolocates to a city (022 = Mumbai).
            A landline is a physical office line, so this is the strongest signal.
  pincode   a postal PIN code in a city's range (400xxx = Mumbai region). An address.
  mention   the city — or one of its business districts (Andheri, Powai, BKC...)
            — named on the page. Weakest: it may be one office in a list of ten.

WHY "MUMBAI + OTHERS" IS KEPT SEPARATE. A measured probe found companies like SCIKEY
naming ten cities on one page. Picking the "top" city there invents a precision the
data does not have, so a company is labelled:

  Mumbai only      Mumbai is the only Indian city found
  Mumbai + others  Mumbai is one of several (with which evidence, so a real 022
                   office line is distinguishable from a name in a list)
  Not Mumbai       other cities found, Mumbai not among them
  No location      the site was read but named no city
  Unreachable      no page could be read (blocked, down, or no website)

No database writes. Output (outside the repo — it contains real names):
  ../placementradar-bench/company_locations.csv   one row per company
  ../placementradar-bench/mumbai_hr_contacts.csv  every HR person at a Mumbai company

    python tools/locate_companies.py
    python tools/locate_companies.py --limit 50      # quick sample
"""

import argparse
import concurrent.futures as cf
import csv
import json
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import warnings  # noqa: E402
warnings.filterwarnings('ignore')

import requests  # noqa: E402
import phonenumbers  # noqa: E402
from phonenumbers import geocoder  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
import contact_finder as CF  # noqa: E402   (landline-aware phone extraction)
import domain_utils as DU  # noqa: E402

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
      'Accept-Language': 'en-IN,en;q=0.9'}

# Pages where companies publish their address. Tried in order; stop once enough read.
PATHS = ('', '/contact-us', '/contact', '/contactus', '/about-us', '/about',
         '/locations', '/offices', '/company/contact')
MAX_PAGES = 4

# Business districts that, on their own, place an office in the Mumbai region.
MUMBAI_AREAS = (
    r'mumbai|bombay|navi\s*mumbai|thane|andheri|bandra|bkc|bandra\s*kurla|powai|'
    r'goregaon|malad|vikhroli|lower\s*parel|worli|kurla|ghatkopar|chembur|borivali|'
    r'kandivali|dadar|nariman\s*point|vashi|airoli|belapur|cbd\s*belapur|ghansoli|'
    r'turbhe|mahape|juhu|santacruz|vile\s*parle|marol|saki\s*naka|sakinaka|mulund|'
    r'bhandup|kanjurmarg|prabhadevi|parel|colaba|churchgate|kalina|jogeshwari|'
    r'dahisar|mira\s*road|kharghar|panvel|rabale|seawoods|kopar\s*khairane')
OTHER_CITIES = {
    'Pune': r'pune|hinjewadi|kharadi|magarpatta|baner|viman\s*nagar|hadapsar',
    'Bangalore': r'bengaluru|bangalore|whitefield|koramangala|electronic\s*city|'
                 r'indiranagar|marathahalli|hsr\s*layout',
    'Delhi NCR': r'new\s*delhi|delhi|noida|gurugram|gurgaon|greater\s*noida|faridabad',
    'Hyderabad': r'hyderabad|hitec\s*city|gachibowli|madhapur|secunderabad',
    'Chennai': r'chennai|guindy|sholinganallur|perungudi|tidel',
    'Kolkata': r'kolkata|salt\s*lake|sector\s*v',
    'Ahmedabad': r'ahmedabad|gandhinagar|gift\s*city',
    'Kochi': r'kochi|cochin|infopark|kakkanad',
    'Jaipur': r'jaipur', 'Indore': r'indore', 'Chandigarh': r'chandigarh|mohali',
    'Coimbatore': r'coimbatore', 'Nagpur': r'nagpur', 'Trivandrum': r'trivandrum|technopark',
    'Vadodara': r'vadodara|baroda', 'Surat': r'surat', 'Nashik': r'nashik',
}
CITY_RES = {'Mumbai': re.compile(r'\b(' + MUMBAI_AREAS + r')\b', re.I)}
CITY_RES.update({c: re.compile(r'\b(' + p + r')\b', re.I) for c, p in OTHER_CITIES.items()})

# PIN code prefixes. Matched only where an address is plausible (see _pincodes).
PIN_PREFIX = {'400': 'Mumbai', '401': 'Mumbai', '410': 'Mumbai', '411': 'Pune',
              '412': 'Pune', '560': 'Bangalore', '110': 'Delhi NCR', '201': 'Delhi NCR',
              '122': 'Delhi NCR', '500': 'Hyderabad', '600': 'Chennai', '700': 'Kolkata',
              '380': 'Ahmedabad', '682': 'Kochi', '302': 'Jaipur', '452': 'Indore',
              '160': 'Chandigarh', '641': 'Coimbatore', '440': 'Nagpur'}
_PIN_RE = re.compile(r'(?:pin(?:code)?|pin\s*code|[-–,]\s*|\s)(\d{3})\s?(\d{3})\b', re.I)
_STATE_RE = re.compile(r'maharashtra|karnataka|telangana|tamil\s*nadu|delhi|haryana|'
                       r'uttar\s*pradesh|west\s*bengal|gujarat|kerala|rajasthan|india',
                       re.I)

GEO_CITY = {'Mumbai': 'Mumbai', 'Pune': 'Pune', 'Bangalore': 'Bangalore',
            'Bengaluru': 'Bangalore', 'Delhi': 'Delhi NCR', 'New Delhi': 'Delhi NCR',
            'Gurgaon': 'Delhi NCR', 'Gurugram': 'Delhi NCR', 'Noida': 'Delhi NCR',
            'Hyderabad': 'Hyderabad', 'Chennai': 'Chennai', 'Kolkata': 'Kolkata',
            'Ahmedabad': 'Ahmedabad', 'Thane': 'Mumbai', 'Navi Mumbai': 'Mumbai'}


def _pincodes(text):
    """PIN codes, counted only near an address-like context (a state or 'India')."""
    out = []
    for m in _PIN_RE.finditer(text):
        city = PIN_PREFIX.get(m.group(1))
        if not city:
            continue
        window = text[max(0, m.start() - 80): m.end() + 40]
        if _STATE_RE.search(window):
            out.append(city)
    return out


def _landline_cities(text):
    out = []
    for d in CF._extract_phones_from_text(text):
        if d[0] in '6789' and not d.startswith(('80', '79')):
            continue          # mobiles carry no location
        try:
            num = phonenumbers.parse('+91' + d, None)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.number_type(num) != phonenumbers.PhoneNumberType.FIXED_LINE:
            continue
        place = geocoder.description_for_number(num, 'en') or ''
        for k, v in GEO_CITY.items():
            if k.lower() in place.lower():
                out.append((v, '+91' + d))
                break
    return out


def locate(domain):
    ev = {'mention': Counter(), 'pincode': Counter(), 'landline': Counter()}
    landline_numbers = defaultdict(list)
    read, base = 0, None
    for scheme_host in (f'https://{domain}', f'https://www.{domain}'):
        try:
            r = requests.get(scheme_host, headers=UA, timeout=8, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 500:
                base = r.url.rstrip('/')
                break
        except Exception:
            continue
    if not base:
        return {'status': 'Unreachable', 'read': 0, 'ev': ev, 'numbers': {}}
    root = re.match(r'https?://[^/]+', base).group(0)
    for p in PATHS:
        if read >= MAX_PAGES:
            break
        url = base if p == '' else root + p
        try:
            r = requests.get(url, headers=UA, timeout=8, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or len(r.text) < 500:
            continue
        if DU.is_parked_page(r.text, r.url)[0]:
            return {'status': 'Unreachable', 'read': 0, 'ev': ev, 'numbers': {},
                    'note': 'parked/for-sale page'}
        try:
            soup = BeautifulSoup(r.text, 'lxml')
            for t in soup(['script', 'style', 'noscript']):
                t.decompose()
            text = soup.get_text(' ')
        except Exception:
            continue
        read += 1
        for city, rx in CITY_RES.items():
            n = len(rx.findall(text))
            if n:
                ev['mention'][city] += n
        for city in _pincodes(text):
            ev['pincode'][city] += 1
        for city, num in _landline_cities(text):
            ev['landline'][city] += 1
            if num not in landline_numbers[city]:
                landline_numbers[city].append(num)
    cities = set(ev['mention']) | set(ev['pincode']) | set(ev['landline'])
    if not read:
        status = 'Unreachable'
    elif not cities:
        status = 'No location'
    elif 'Mumbai' in cities:
        status = 'Mumbai only' if cities == {'Mumbai'} else 'Mumbai + others'
    else:
        status = 'Not Mumbai'
    return {'status': status, 'read': read, 'ev': ev, 'numbers': dict(landline_numbers)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=str(REPO_ROOT.parent / 'placementradar-bench' /
                                            'hrcontact_pdf_parsed.json'))
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write contact data inside the repo.')
        return 2

    people = json.load(open(args.source, encoding='utf-8'))
    by_dom = defaultdict(list)
    for p in people:
        dom = p['email'].split('@')[-1].lower()
        if DU.is_junk_contact_domain(p['email']):
            continue
        by_dom[dom].append(p)
    domains = sorted(by_dom)
    if args.limit:
        domains = domains[:args.limit]
    print(f'Locating {len(domains)} companies ({sum(len(by_dom[d]) for d in domains)} HR '
          f'contacts), {args.workers} in parallel, up to {MAX_PAGES} pages each...\n',
          flush=True)

    results, lock, done = {}, threading.Lock(), [0]
    t0 = time.time()

    def work(dom):
        res = locate(dom)
        with lock:
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == len(domains):
                print(f'  {done[0]:>5}/{len(domains)}  {time.time() - t0:5.0f}s', flush=True)
        return dom, res

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for dom, res in ex.map(work, domains):
            results[dom] = res

    loc_path = out_dir / 'company_locations.csv'
    mum_path = out_dir / 'mumbai_hr_contacts.csv'
    status_n = Counter()
    with loc_path.open('w', encoding='utf-8', newline='') as f, \
            mum_path.open('w', encoding='utf-8', newline='') as g:
        w = csv.writer(f)
        w.writerow(['company', 'domain', 'status', 'cities_found', 'mumbai_evidence',
                    'mumbai_office_lines', 'hr_contacts', 'pages_read'])
        m = csv.writer(g)
        m.writerow(['status', 'company', 'hr_name', 'hr_title', 'hr_email',
                    'mumbai_evidence', 'mumbai_office_line', 'other_cities'])
        order = {'Mumbai only': 0, 'Mumbai + others': 1, 'Not Mumbai': 2,
                 'No location': 3, 'Unreachable': 4}
        for dom in sorted(domains, key=lambda d: (order[results[d]['status']], d)):
            r = results[dom]
            status_n[r['status']] += 1
            ev = r['ev']
            cities = sorted(set(ev['mention']) | set(ev['pincode']) | set(ev['landline']))
            mev = [k for k in ('landline', 'pincode', 'mention') if ev[k].get('Mumbai')]
            lines = ' ; '.join(r['numbers'].get('Mumbai', []))
            company = by_dom[dom][0]['company']
            w.writerow([company, dom, r['status'], ', '.join(cities), '+'.join(mev), lines,
                        len(by_dom[dom]), r['read']])
            if r['status'] in ('Mumbai only', 'Mumbai + others'):
                for p in by_dom[dom]:
                    m.writerow([r['status'], p['company'], p['name'], p['title'], p['email'],
                                '+'.join(mev), lines,
                                ', '.join(c for c in cities if c != 'Mumbai')])

    mumbai_people = sum(len(by_dom[d]) for d in domains
                        if results[d]['status'] in ('Mumbai only', 'Mumbai + others'))
    strong = sum(1 for d in domains if results[d]['status'] in ('Mumbai only', 'Mumbai + others')
                 and (results[d]['ev']['landline'].get('Mumbai') or
                      results[d]['ev']['pincode'].get('Mumbai')))
    print(f'\n{"=" * 70}\nCOMPANY LOCATIONS — {len(domains)} companies, {time.time() - t0:.0f}s\n{"=" * 70}')
    for s in ('Mumbai only', 'Mumbai + others', 'Not Mumbai', 'No location', 'Unreachable'):
        print(f'  {s:16} {status_n[s]:5}')
    print(f'\n  Mumbai companies with an ADDRESS-grade signal (022 line or 400xxx PIN): {strong}')
    print(f'  HR contacts at Mumbai companies: {mumbai_people}')
    print(f'\n  {loc_path}\n  {mum_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
