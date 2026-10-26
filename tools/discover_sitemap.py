"""
tools/discover_sitemap.py — Phase E step 1: sitemap/robots contact discovery.

Replaces contact_finder.PHONE_PATHS / EMAIL_PATHS — a hardcoded guess list of 23
paths — with the URLs a site actually publishes about itself.

Scope: the 120 companies in companies_v3 that have a CONFIRMED domain and ZERO
contacts. They are the cleanest test of whether sitemap enumeration works: a good
domain and nothing extracted. If it yields nothing here it yields nothing anywhere.

MEASUREMENT ONLY — opens the database read-only and writes nothing to it.

Design notes
------------
* Volume forces a cap. A spike measured ~1,000 URLs per domain, so contact-ish
  URLs are ranked by path keyword and capped per domain. Discards are counted, so
  it is visible whether the cap costs yield.
* Attribution is a review decision, not a heuristic. An email on the company's own
  domain is auto-accepted; an email on ANY other domain (a partner, an agency)
  goes to a review queue as `attribution: unverified`.
* Phones record `source_domain` from the start. Historical phones are unauditable
  because nothing recorded where they came from (the Phase G gap). Here we know
  the exact page, so we store it rather than repeat the mistake.
* Parsing is reused from contact_finder, not reimplemented.

    python tools/discover_sitemap.py --limit 10   # smoke
    python tools/discover_sitemap.py              # all 120
"""

import argparse
import csv
import json
import os
import re
import sys
import threading
import concurrent.futures as cf
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('MONGO_URI', 'mongodb://127.0.0.1:27017/placementradar_dev')
os.environ.pop('GH_PAT', None)

from bs4 import BeautifulSoup  # noqa: E402
import database as D  # noqa: E402
import domain_utils as DU  # noqa: E402
import contact_finder as CF  # noqa: E402
from tools.bench import CachingRequests, Recorder, _tls  # noqa: E402

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    'Accept-Language': 'en-IN,en;q=0.9',
}
# Whole-PATH-SEGMENT matching, not substring. A substring filter admitted 20% junk
# (3,095 of 15,486 URLs): `hr` matched greythr.com's own name so ALL 624 of its URLs
# qualified, and `thr`eat / `thr`ough / health`car`e pulled in job postings. Because
# only the top N are fetched, that junk DISPLACED real contact pages — the filter was
# the problem, not the cap.
CONTACT_WORDS = ('contact', 'about', 'team', 'people', 'career', 'hr', 'reach')
_SEG_SPLIT = re.compile(r'[/\-_.]+')

# Priority measured, not guessed (20-domain subsample, yield per keyword):
#   contact 67% hit rate / 13 contacts · career 50% / 6 · about 44% / 9 · team 67% / 2
#   people, reach, hr: ~0 pages and 0 contacts.
# /contact beats /careers here — an SME careers page is usually a job list, while a
# contact page carries a real phone number.
PRIORITY = (('contact', 0), ('about', 1), ('career', 1), ('team', 2),
            ('people', 3), ('hr', 3), ('reach', 3))


def _segments(url):
    try:
        path = urlparse(url).path
    except Exception:
        return set()
    return {s.lower() for s in _SEG_SPLIT.split(path) if s}


def is_contactish(url):
    """True when a contact word appears as a WHOLE path segment (or its plural)."""
    segs = _segments(url)
    return any(w in segs or w + 's' in segs for w in CONTACT_WORDS)
# Default placed from the measured first-hit rank distribution, not guessed.
# Cumulative share of all recoveries by rank: 1 -> 56%, 2 -> 81%, 6 -> 92%,
# 12 -> 96%, 20 -> 98%. Cost per recovered company: cap2 3.7 fetches, cap6 8.7,
# cap12 14.6, cap20 21.0. Going 6 -> 12 buys 2 companies for 288 extra fetches
# (144 each); 12 -> 20 buys 1 for 319. cap=6 therefore takes 94% of cap-20's
# yield for 39% of its fetch budget, and a re-run pays that budget every time.
# The recorded Phase E headline (51/120 = 42.5%) was measured at cap=20.
MAX_PAGES_PER_DOMAIN = 6   # overridable via --max-pages
MAX_SUB_SITEMAPS = 3

_rec = Recorder()
_lock = threading.Lock()


def _rank(url):
    segs = _segments(url)
    for kw, score in PRIORITY:
        if kw in segs or kw + 's' in segs:
            return score
    return 9


def _get(session, url, timeout=10):
    """Fetch and classify. Never swallows silently — failures are recorded."""
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except Exception as e:
        return None, type(e).__name__
    if not (200 <= r.status_code < 300):
        return None, f'http_{r.status_code}'
    return r, ''


def _parse_sitemap(text):
    """(urls, is_index). An index's <loc>s are sub-sitemaps, not pages."""
    if not text or '<' not in text[:400]:
        return [], False
    is_index = '<sitemapindex' in text[:2000].lower()
    locs = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', text, re.I)
    return locs, is_index


def enumerate_urls(session, domain, stats):
    """robots.txt Sitemap: directive -> /sitemap.xml -> /sitemap_index.xml."""
    _tls.source = 'sitemap'
    candidates, how = [], None

    r, err = _get(session, f'https://{domain}/robots.txt')
    if r is not None:
        stats['robots_2xx'] += 1
        found = re.findall(r'(?im)^\s*sitemap:\s*(\S+)', r.text or '')
        if found:
            candidates = found[:3]
            how = 'robots_directive'
    else:
        stats[f'robots_fail'] += 1

    if not candidates:
        candidates = [f'https://{domain}/sitemap.xml', f'https://{domain}/sitemap_index.xml']
        how = 'well_known'

    urls = []
    for cand in candidates:
        r, err = _get(session, cand)
        if r is None:
            continue
        locs, is_index = _parse_sitemap(r.text or '')
        if not locs:
            continue
        if is_index:
            stats['sitemap_index'] += 1
            for sub in locs[:MAX_SUB_SITEMAPS]:
                r2, _ = _get(session, sub)
                if r2 is None:
                    continue
                sub_locs, _ = _parse_sitemap(r2.text or '')
                urls.extend(sub_locs)
        else:
            urls.extend(locs)
        if urls:
            return urls, how
    return urls, how


def harvest(session, company, domain, stats):
    """Enumerate, filter, cap, fetch, extract. Returns a per-company record."""
    _tls.company = company
    rec = {'company': company, 'domain': domain, 'sitemap_source': None,
           'urls_total': 0, 'contactish': 0, 'capped_out': 0, 'fetched': 0,
           'pages_2xx': 0, 'own_domain_emails': [], 'offdomain_emails': [],
           'phones': [], 'errors': []}

    urls, how = enumerate_urls(session, domain, stats)
    rec['sitemap_source'] = how if urls else None
    rec['urls_total'] = len(urls)
    if not urls:
        stats['no_sitemap'] += 1
        return rec
    stats['sitemap_ok'] += 1

    hits = [u for u in urls if is_contactish(u)]
    rec['contactish'] = len(hits)
    if not hits:
        stats['sitemap_but_no_contactish'] += 1
        return rec
    hits.sort(key=lambda u: (_rank(u), len(u)))
    chosen = hits[:MAX_PAGES_PER_DOMAIN]
    rec['capped_out'] = max(0, len(hits) - len(chosen))

    for u in chosen:
        rec['fetched'] += 1
        r, err = _get(session, u, timeout=10)
        if r is None:
            rec['errors'].append(f'{u[:70]}::{err}')
            continue
        rec['pages_2xx'] += 1
        html = r.text or ''
        try:
            soup = BeautifulSoup(html, 'lxml')
            for tag in soup(['script', 'style']):
                tag.decompose()
            text = soup.get_text(' ')
        except Exception as e:
            rec['errors'].append(f'{u[:70]}::parse_{type(e).__name__}')
            continue

        emails, _ = CF._extract_from_desc(text)          # reuse, don't reimplement
        for e in emails:
            n = D.normalize_contact_value('email', e)
            if not n or DU.is_junk_contact_domain(n):
                continue
            edom = n.split('@')[-1]
            entry = {'value': n, 'source_url': u, 'source_domain': domain}
            if edom == domain or edom.endswith('.' + domain):
                if n not in [x['value'] for x in rec['own_domain_emails']]:
                    rec['own_domain_emails'].append(entry)
            else:
                # Attribution decision -> review queue, never auto-accepted.
                entry['attribution'] = 'unverified'
                entry['email_domain'] = edom
                if n not in [x['value'] for x in rec['offdomain_emails']]:
                    rec['offdomain_emails'].append(entry)

        try:
            for p in CF._extract_phones_from_page(soup):
                n = D.normalize_contact_value('phone', p)
                # source_domain recorded from the start — the Phase G gap, closed here.
                if n and n not in [x['value'] for x in rec['phones']]:
                    rec['phones'].append({'value': n, 'source_url': u,
                                          'source_domain': domain})
        except Exception as e:
            rec['errors'].append(f'{u[:70]}::phone_{type(e).__name__}')
    return rec


def main():
    global MAX_PAGES_PER_DOMAIN          # must precede any use of the name
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--max-pages', type=int, default=6,
                    help='contact-ish pages fetched per domain (cap)')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    # Read the shared constant rather than a local literal. This file hardcoded
    # `companies_v3` while the store moved on — the same drift that put 214 contacts
    # into companies_v2.
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--mode', choices=('empty', 'no-own-phone'), default='empty',
                    help="empty = companies with NO contacts (the original Phase E "
                         "scope, kept so that result stays reproducible); "
                         "no-own-phone = companies with a confirmed domain but no "
                         "phone taken from their OWN website")
    ap.add_argument('--out-name', default=None,
                    help='output filename; defaults per mode so runs do not overwrite')
    args = ap.parse_args()
    MAX_PAGES_PER_DOMAIN = args.max_pages
    out_dir = Path(args.out_dir)

    db = D._get_db()          # READ ONLY — nothing is written to the database here
    docs = [d for d in db[args.collection].find({'identity_type': 'domain'})
            if d.get('domain')]

    if args.mode == 'empty':
        pick = [d for d in docs if not (d.get('contacts') or [])]
        label = 'empty domain-identified companies'
    else:
        # A company can hold CSV contacts and still have never had its own website
        # scanned. Phase E only ever ran on companies with zero contacts, so every
        # company that arrived with a listing-page phone was skipped — and a
        # listing-page phone is precisely the kind that gets a call cut.
        pick = [d for d in docs
                if not any(c.get('type') == 'phone' and c.get('source') == 'sitemap'
                           for c in (d.get('contacts') or []))]
        label = 'domain-identified companies with no phone from their own website'

    targets = sorted((d['company_name'], d['domain']) for d in pick)
    if args.limit:
        targets = targets[:args.limit]
    print(f'Sitemap discovery — mode={args.mode} — over {len(targets)} {label}')
    print(f'  collection: {args.collection}   max_pages={MAX_PAGES_PER_DOMAIN}\n')

    session = CachingRequests(__import__('requests'), out_dir / 'http_cache', _rec)
    stats = Counter()
    done = [0]

    def work(pair):
        r = harvest(session, pair[0], pair[1], stats)
        with _lock:
            done[0] += 1
            got = len(r['own_domain_emails']) + len(r['phones'])
            if got or done[0] % 20 == 0:
                print(f'  [{done[0]:>3}/{len(targets)}] {r["company"][:26]:<28}'
                      f'{r["domain"][:24]:<26}'
                      f'{len(r["own_domain_emails"])}e/{len(r["phones"])}p', flush=True)
        return r

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(work, targets))

    n = len(rows)
    with_any = [r for r in rows if r['own_domain_emails'] or r['phones']]
    with_email = [r for r in rows if r['own_domain_emails']]
    with_phone = [r for r in rows if r['phones']]
    all_emails = [e for r in rows for e in r['own_domain_emails']]
    all_phones = [p for r in rows for p in r['phones']]
    offdomain = [e for r in rows for e in r['offdomain_emails']]

    print(f'\n{"="*74}\nPHASE E STEP 1 — SITEMAP DISCOVERY\n{"="*74}')
    print(f'  HEADLINE: companies 0 contacts -> >=1 : {len(with_any)} / {n} '
          f'= {len(with_any)/max(n,1)*100:.1f}%')
    print(f'    with >=1 own-domain email           : {len(with_email)}')
    print(f'    with >=1 phone                      : {len(with_phone)}')

    print(f'\n  FUNNEL (drop-off visible at each step)')
    print(f'    domains attempted                   : {n}')
    print(f'    robots.txt 2xx                      : {stats["robots_2xx"]}')
    print(f'    sitemap parsed                      : {stats["sitemap_ok"]}  '
          f'(indexes followed: {stats["sitemap_index"]})')
    print(f'    no sitemap at all                   : {stats["no_sitemap"]}')
    print(f'    sitemap but ZERO contact-ish URLs   : {stats["sitemap_but_no_contactish"]}')
    print(f'    URLs enumerated                     : {sum(r["urls_total"] for r in rows)}')
    print(f'    contact-ish after filter            : {sum(r["contactish"] for r in rows)}')
    print(f'    discarded by the {MAX_PAGES_PER_DOMAIN}-per-domain cap : '
          f'{sum(r["capped_out"] for r in rows)}')
    print(f'    pages fetched                       : {sum(r["fetched"] for r in rows)}')
    print(f'    pages 2xx                           : {sum(r["pages_2xx"] for r in rows)}')

    print(f'\n  CONTACTS FOUND')
    print(f'    own-domain emails                   : {len(all_emails)}')
    print(f'    phones (source_domain recorded)     : {len(all_phones)}')
    print(f'    off-domain emails -> review queue   : {len(offdomain)}')

    cls = [D.classify_email(e['value']) for e in all_emails]
    hr = sum(1 for c in cls if c['is_hr_family'])
    gen = sum(1 for c in cls if c['is_generic_role'])
    # database.classify_email() exposes hr_family / generic_role but not a
    # person_like key — person-like is simply "neither of those".
    per = sum(1 for c in cls if not c['is_hr_family'] and not c['is_generic_role'])
    print(f'\n  EMAIL QUALITY (of {len(all_emails)})')
    print(f'    hr@-family                          : {hr}')
    print(f'    generic role (info@/sales@)         : {gen}')
    print(f'    person-like                         : {per}')
    conf = [D.build_contact('email', e['value'], 'sitemap', company_domain=e['source_domain'])
            for e in all_emails]
    nz = sum(1 for c in conf if c and (c.get('confidence') or 0) > 0)
    print(f'    confidence > 0                      : {nz}  '
          f'({nz/max(len(all_emails),1)*100:.0f}%)')

    src = _rec.http.get('sitemap', Counter())
    tot_http = sum(src.get(k, 0) for k in
                   ('http_2xx', 'http_403', 'http_429', 'http_5xx', 'http_other',
                    'timeouts', 'exceptions'))
    non200 = tot_http - src.get('http_2xx', 0)
    print(f'\n  HTTP (source=sitemap)')
    for k in ('http_2xx', 'http_403', 'http_429', 'http_5xx', 'http_other',
              'timeouts', 'exceptions', 'cache_hit'):
        print(f'    {k:<36}{src.get(k, 0)}')
    rate = non200 / tot_http if tot_http else 0
    print(f'    non-200 rate                        : {rate:.0%}'
          f'{"   !! BLOCKING SUSPECTED (>30%)" if rate > 0.30 else ""}')

    out_dir.mkdir(parents=True, exist_ok=True)
    # Distinct filename per mode — a no-own-phone run must not overwrite the recorded
    # Phase E cap-20 result, which the v5 builder and later phases read.
    _stem = args.out_name or (f'sitemap_discovery_cap{MAX_PAGES_PER_DOMAIN}.json'
                              if args.mode == 'empty'
                              else f'sitemap_phones_cap{MAX_PAGES_PER_DOMAIN}.json')
    (out_dir / _stem).write_text(json.dumps({
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'companies': n, 'with_any_contact': len(with_any),
        'stats': dict(stats), 'http': dict(src), 'rows': rows,
    }, indent=2, default=str), encoding='utf-8')

    q = out_dir / 'sitemap_review_queue.csv'
    with q.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['company', 'company_domain', 'email', 'email_domain',
                    'found_on', 'decision(APPROVE/REJECT)'])
        for r in rows:
            for e in r['offdomain_emails']:
                w.writerow([r['company'], r['domain'], e['value'],
                            e['email_domain'], e['source_url'], ''])
    print(f'\n  detail : {out_dir / _stem}')
    print(f'  queue  : {q}  ({len(offdomain)} rows)')
    print('\n  NOTE: nothing was written to the database. Persisting is a separate')
    print('        dry-run + approval under the standing DB rules.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
