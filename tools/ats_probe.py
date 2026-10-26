"""
tools/ats_probe.py — Pre-Phase-D reality check (DETECTION ONLY).

Phase D of CLAUDE_CODE_BRIEF assumes Indian mid-market employers publish through
Greenhouse / Lever / Ashby / Workable / SmartRecruiters / Recruitee. That holds for
US-funded startups and may be near-zero here. This probe measures actual penetration
across all unique companies BEFORE any ATS client is written.

Read-only: HTTP GET only. No database of any kind is opened, no writes, no clients
built. Reuses the 2xx-only disk cache from tools/bench.py.

Note: CLAUDE_CODE_BRIEF.md is kept OUTSIDE the repo at ../placementradar-bench/
until Phase H ships, because it documents unpatched vulnerabilities.

    python tools/ats_probe.py                 # all companies
    python tools/ats_probe.py --limit 40      # quick look
"""

import argparse
import json
import re
import sys
import threading
import concurrent.futures as cf
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import pandas as pd  # noqa: E402
from tools.bench import (MANIFEST, _cell, CachingRequests, Recorder, _tls)  # noqa: E402
import contact_finder as cf_mod  # noqa: E402

# Host patterns -> (ats, slug). Cover both the public board host and the API host,
# since many sites embed the API/board URL rather than redirecting to it.
ATS_PATTERNS = [
    ('greenhouse',      r'(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)'),
    ('greenhouse',      r'boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)'),
    ('lever',           r'jobs\.lever\.co/([A-Za-z0-9_-]+)'),
    ('lever',           r'api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)'),
    ('ashby',           r'jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)'),
    ('ashby',           r'api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_-]+)'),
    ('workable',        r'apply\.workable\.com/(?:api/v1/widget/accounts/)?([A-Za-z0-9_-]+)'),
    ('smartrecruiters', r'(?:jobs|careers|api)\.smartrecruiters\.com/(?:v1/companies/)?([A-Za-z0-9_-]+)'),
    ('recruitee',       r'([A-Za-z0-9_-]+)\.recruitee\.com'),
]
# Embed fingerprints that appear even when the board URL is built by JS.
EMBED_HINTS = [
    ('greenhouse',      r'grnhse|greenhouse\.io'),
    ('lever',           r'lever-jobs|lever\.co'),
    ('ashby',           r'ashby_embed|ashbyhq'),
    ('workable',        r'whr_embed|workable\.com'),
    ('smartrecruiters', r'smartrecruiters'),
    ('recruitee',       r'recruitee'),
]
PATHS = ['/careers', '/jobs', '']          # most likely first; short-circuit on hit

_rec = Recorder()
_print_lock = threading.Lock()


def unique_companies():
    df = pd.concat([pd.read_csv(REPO_ROOT / r, on_bad_lines='skip', low_memory=False)
                    for r, _ in MANIFEST], ignore_index=True)
    best = {}
    for rec in df.to_dict('records'):
        c = _cell(rec.get('company'))
        if not c:
            continue
        url = _cell(rec.get('company_url'))
        if c not in best or (not best[c] and url):
            best[c] = url
    return best


def resolve_domain(company, company_url):
    d = cf_mod._get_domain(company_url) if company_url else ''
    return d or cf_mod._guess_domain(company), bool(d)


def detect(text, final_url):
    """Return (ats, slug, evidence) or (None, None, None)."""
    hay = f'{final_url}\n{text}'
    for ats, pat in ATS_PATTERNS:
        m = re.search(pat, hay, re.I)
        if m:
            slug = m.group(1)
            if slug.lower() in ('www', 'api', 'jobs', 'boards', 'embed', 'v1'):
                continue
            return ats, slug, 'url_or_link'
    for ats, pat in EMBED_HINTS:
        if re.search(pat, hay, re.I):
            return ats, None, 'embed_hint'
    return None, None, None


def probe_one(session, company, company_url):
    _tls.source = 'ats'
    _tls.company = company
    domain, had_url = resolve_domain(company, company_url)
    row = {'company': company, 'domain': domain, 'domain_from_csv': had_url,
           'ats': None, 'slug': None, 'evidence': None, 'reachable': False}
    if not domain:
        row['error'] = 'no_domain'
        return row
    base = f'https://{domain}'
    for path in PATHS:
        try:
            r = session.get(base + path, headers=cf_mod.HEADERS, timeout=6,
                            allow_redirects=True)
        except Exception:
            continue
        if r.status_code < 200 or r.status_code >= 300:
            continue
        row['reachable'] = True
        ats, slug, ev = detect(r.text or '', str(getattr(r, 'url', '') or ''))
        if ats:
            row.update({'ats': ats, 'slug': slug, 'evidence': ev, 'found_at': path or '/'})
            return row
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir == REPO_ROOT or REPO_ROOT in out_dir.parents:
        print(f'FATAL: --out-dir resolves inside the repo ({out_dir}).')
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    import requests as _real
    session = CachingRequests(_real, out_dir / 'http_cache', _rec)

    companies = unique_companies()
    items = sorted(companies.items())
    if args.limit:
        items = items[:args.limit]
    print(f'Probing {len(items)} unique companies for ATS boards '
          f'({args.workers} workers, read-only HTTP, 2xx cached)\n')

    rows, done = [], [0]

    def work(pair):
        r = probe_one(session, pair[0], pair[1])
        with _print_lock:
            done[0] += 1
            if done[0] % 25 == 0 or r['ats']:
                tag = f'{r["ats"]}:{r["slug"]}' if r['ats'] else '-'
                print(f'  [{done[0]:>3}/{len(items)}] {r["company"][:34]:<36}'
                      f'{r["domain"][:28]:<30}{tag}', flush=True)
        return r

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(work, items))

    n = len(rows)
    by_ats = Counter(r['ats'] for r in rows if r['ats'])
    total_ats = sum(by_ats.values())
    reachable = sum(1 for r in rows if r['reachable'])
    from_csv = sum(1 for r in rows if r['domain_from_csv'])

    print('\n' + '=' * 74)
    print('ATS PENETRATION')
    print('=' * 74)
    print(f'companies probed         : {n}')
    print(f'  domain came from CSV   : {from_csv}  ({from_csv/n*100:.1f}%)')
    print(f'  domain guessed         : {n-from_csv}  ({(n-from_csv)/n*100:.1f}%)')
    print(f'  site reachable (2xx)   : {reachable}  ({reachable/n*100:.1f}%)')
    print()
    for ats in ('greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'recruitee'):
        c = by_ats.get(ats, 0)
        print(f'  {ats:<18}{c:>5}   {c/n*100:>5.1f}%')
    print('-' * 74)
    print(f'  {"ANY ATS":<18}{total_ats:>5}   {total_ats/n*100:>5.1f}%')
    print(f'  {"NONE":<18}{n-total_ats:>5}   {(n-total_ats)/n*100:>5.1f}%')

    verdict = ('BELOW 20% — Phase D assumption does not hold; reorder so Phase E '
               '(sitemap + crt.sh) is the main effort'
               if total_ats / n * 100 < 20 else
               'at/above 20% — Phase D worth building for the ATSs that appear')
    print(f'\nVERDICT: {total_ats/n*100:.1f}% combined penetration -> {verdict}')

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'companies_probed': n,
        'domain_from_csv': from_csv,
        'reachable': reachable,
        'by_ats': dict(by_ats),
        'any_ats': total_ats,
        'any_ats_pct': round(total_ats / n * 100, 1),
        'none_pct': round((n - total_ats) / n * 100, 1),
        'verdict': verdict,
        'http': dict(_rec.http.get('ats', {})),
        'rows': rows,
    }
    dest = out_dir / 'ats_probe.json'
    dest.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(f'\nFull per-company results (incl. domain list for Phase E): {dest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
