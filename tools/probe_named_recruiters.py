"""
tools/probe_named_recruiters.py — FEASIBILITY PROBE, not a builder.

QUESTION: can we produce verified, named-recruiter WORK email addresses for the
domain-confirmed companies — activating the two scoring terms that are currently
dead (`person_name` +0.20, `role_title` +0.15)?

WHY A PROBE FIRST. The ATS probe measured 1.6% penetration and saved Phase D from
being built on an assumption that did not hold. Same discipline here: measure the
yield before writing the feature.

METHOD — uses only data already on disk. No LinkedIn, no new scraping, no ToS issue:

  1. PATTERN INFERENCE. An existing named address on a company's own domain reveals
     that company's convention: `jane.doe@example.com` says `first.last@`. The
     pattern is derived from the company's OWN verified mail, so it is not circular.
  2. NAME MINING. Phase E cached ~2,100 company pages (contact/about/careers/team).
     Those pages are where companies publish their own staff. Names are extracted
     only NEAR an HR/recruiting role word, so a random capitalised phrase does not
     qualify.
  3. CANDIDATE + VERIFY. name x pattern -> candidate address; the domain's MX is
     already verified in companies_v5.

SCOPE — WORK addresses on the company's own confirmed domain, only. This probe does
not touch personal providers, breach data, or people-search aggregators, and it
cannot: it reads company-published pages and the company's own mail convention.

READ-ONLY. No database writes. Output goes OUTSIDE the repo (it carries real names).

    python tools/probe_named_recruiters.py --n 20
"""

import argparse
import json
import os
import re
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

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

# Role words that make a nearby name plausibly a recruiting contact.
HR_ROLE_RE = re.compile(
    r'\b(hr|human\s+resources?|talent\s+acquisition|talent|recruit(?:er|ment|ing)?|'
    r'people\s+(?:ops|operations|team)|campus|hiring\s+manager|staffing|'
    r'chief\s+people|head\s+of\s+people|hr\s+manager|hr\s+head|hr\s+executive)\b', re.I)

# A person name: two or three capitalised tokens. Deliberately strict.
NAME_RE = re.compile(r'\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,15})(?:\s+([A-Z][a-z]{1,15}))?\b')

# Capitalised words that are not people.
NOT_A_NAME = {
    'privacy', 'policy', 'terms', 'conditions', 'contact', 'about', 'careers',
    'career', 'home', 'services', 'solutions', 'products', 'company', 'team',
    'human', 'resources', 'talent', 'acquisition', 'read', 'more', 'learn',
    'get', 'started', 'sign', 'log', 'view', 'all', 'our', 'the', 'and', 'for',
    'private', 'limited', 'india', 'pvt', 'ltd', 'llp', 'inc', 'corp', 'group',
    'united', 'states', 'new', 'delhi', 'mumbai', 'bangalore', 'bengaluru',
    'chennai', 'hyderabad', 'pune', 'kolkata', 'noida', 'gurgaon', 'gurugram',
    'copyright', 'rights', 'reserved', 'follow', 'us', 'email', 'phone', 'call',
    'send', 'message', 'submit', 'apply', 'now', 'job', 'jobs', 'open', 'roles',
    'management', 'manager', 'director', 'officer', 'executive', 'head', 'lead',
    'senior', 'junior', 'chief', 'vice', 'president', 'board', 'members',
    'monday', 'friday', 'january', 'february', 'march', 'april', 'june', 'july',
    'august', 'september', 'october', 'november', 'december',
}


def local_shape(local, ):
    """Classify an email local-part into a reusable pattern."""
    l = (local or '').lower()
    if not l:
        return None
    if l in D.HR_FAMILY_PREFIXES or l in D.GENERIC_ROLE_PREFIXES:
        return 'role'
    if re.fullmatch(r'[a-z]+\.[a-z]+', l):
        return 'first.last'
    if re.fullmatch(r'[a-z]+_[a-z]+', l):
        return 'first_last'
    if re.fullmatch(r'[a-z]\.[a-z]+', l):
        return 'f.last'
    if re.fullmatch(r'[a-z]+', l) and len(l) <= 12:
        return 'first_or_last'
    return 'other'


def render(pattern, first, last):
    f, l = first.lower(), last.lower()
    return {'first.last': f'{f}.{l}', 'first_last': f'{f}_{l}',
            'f.last': f'{f[0]}.{l}', 'first_or_last': f, 'flast': f'{f[0]}{l}',
            'firstlast': f'{f}{l}'}.get(pattern)


def mine_names(html, limit=40):
    """Person names appearing within 160 chars of an HR/recruiting role word."""
    if not html:
        return []
    if BeautifulSoup is not None:
        try:
            text = BeautifulSoup(html, 'lxml').get_text(' ', strip=True)
        except Exception:
            text = re.sub(r'<[^>]+>', ' ', html)
    else:
        text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s{2,}', ' ', text)

    out = []
    for m in HR_ROLE_RE.finditer(text):
        lo, hi = max(0, m.start() - 160), min(len(text), m.end() + 160)
        window = text[lo:hi]
        for nm in NAME_RE.finditer(window):
            toks = [t for t in nm.groups() if t]
            if any(t.lower() in NOT_A_NAME for t in toks):
                continue
            out.append((toks[0], toks[-1], m.group(0).strip(), window.strip()[:110]))
            if len(out) >= limit:
                return out
    return out


def load_cache_by_host(cache_dir):
    by_host = defaultdict(list)
    n = 0
    for f in Path(cache_dir).glob('*.json'):
        try:
            b = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        u, t = b.get('url') or '', b.get('text') or ''
        if not u or not t:
            continue
        n += 1
        h = u.split('//', 1)[-1].split('/', 1)[0].lower()
        if h.startswith('www.'):
            h = h[4:]
        by_host[h].append((u, t))
    return by_host, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=20)
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write names inside the repo.')
        return 2

    db = D._get_db()
    docs = [d for d in db[args.collection].find({}) if d.get('identity_type') == 'domain']
    docs.sort(key=lambda d: d['_id'])
    sample = docs[:args.n]

    cache_dir = out_dir / 'http_cache'
    by_host, n_cached = load_cache_by_host(cache_dir)

    print(f'\n{"="*78}\nPROBE — NAMED-RECRUITER WORK EMAIL FEASIBILITY\n{"="*78}')
    print(f'  collection            : {args.collection}')
    print(f'  domain-identity docs  : {len(docs)}   sampled: {len(sample)}')
    print(f'  cached page bodies    : {n_cached} across {len(by_host)} hosts')

    # ---- CHECK 1: how many companies reveal their own email pattern? ----
    print(f'\n--- CHECK 1: email pattern derivable from the company\'s OWN mail ---')
    shapes_all = Counter()
    with_pattern = {}
    for d in docs:
        dom = (d.get('domain') or '').lower()
        pats = Counter()
        for c in (d.get('contacts') or []):
            if c.get('type') != 'email':
                continue
            v = (c.get('value_norm') or '')
            if not v.endswith('@' + dom):
                continue
            sh = local_shape(v.split('@')[0])
            if sh:
                pats[sh] += 1
                shapes_all[sh] += 1
        person = {k: v for k, v in pats.items() if k not in ('role', 'other')}
        if person:
            with_pattern[d['_id']] = max(person, key=person.get)
    print(f'  local-part shapes across all own-domain mail: {dict(shapes_all)}')
    print(f'  companies with a PERSON-shaped address (pattern derivable): '
          f'{len(with_pattern)}/{len(docs)}  ({len(with_pattern)/max(len(docs),1)*100:.1f}%)')
    if with_pattern:
        print(f'  pattern distribution: {dict(Counter(with_pattern.values()))}')

    # ---- CHECK 2: can we mine names from cached company pages? ----
    print(f'\n--- CHECK 2: HR-adjacent person names in cached pages (sample of {len(sample)}) ---')
    rows, hit_companies = [], 0
    no_cache = 0
    for d in sample:
        dom = (d.get('domain') or '').lower()
        pages = by_host.get(dom, [])
        if not pages:
            no_cache += 1
            continue
        found = []
        for u, t in pages[:12]:
            for first, last, role, ctx in mine_names(t):
                found.append((first, last, role, u, ctx))
        # dedupe by name
        seen, uniq = set(), []
        for f, l, r, u, c in found:
            k = (f.lower(), l.lower())
            if k in seen:
                continue
            seen.add(k)
            uniq.append((f, l, r, u, c))
        if uniq:
            hit_companies += 1
        for f, l, r, u, c in uniq[:4]:
            pat = with_pattern.get(d['_id'])
            cand = f'{render(pat, f, l)}@{dom}' if pat else ''
            rows.append({'company': d.get('company_name'), 'doc_id': d['_id'],
                         'domain': dom, 'first': f, 'last': l, 'role_hint': r,
                         'pattern': pat or '', 'candidate_email': cand,
                         'source_url': u, 'context': c})
    print(f'  companies with cached pages   : {len(sample)-no_cache}/{len(sample)}  '
          f'(no cache: {no_cache})')
    print(f'  companies yielding >=1 name   : {hit_companies}')
    print(f'  candidate name rows           : {len(rows)}')
    print(f'  of those, with a known pattern: {sum(1 for r in rows if r["pattern"])}')

    print(f'\n--- SAMPLE ROWS (read these, do not trust the count) ---')
    for r in rows[:12]:
        print(f'\n  {r["first"]} {r["last"]}   @{r["domain"]}   role_hint={r["role_hint"]!r}')
        print(f'    candidate : {r["candidate_email"] or "(no pattern known)"}')
        print(f'    context   : ...{r["context"][:96]}...')

    if rows:
        p = out_dir / 'probe_named_recruiters.csv'
        import csv
        with p.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f'\n  rows written: {p}  (outside the repo — contains real names)')

    print(f'\n{"="*78}\nVERDICT INPUTS\n{"="*78}')
    print(f'  pattern derivable for      : {len(with_pattern)}/{len(docs)} companies')
    print(f'  names minable (sampled)    : {hit_companies}/{len(sample)} companies')
    print(f'  BOTH (name x pattern)      : {sum(1 for r in rows if r["pattern"])} candidate addresses')
    print(f'\n  Read the sample rows above before believing any of these numbers.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
