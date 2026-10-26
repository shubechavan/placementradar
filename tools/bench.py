"""
tools/bench.py — Phase A measurement harness for contact discovery.

Measures the CURRENT enrichment pipeline. Changes no pipeline code: every hook
(source instrumentation, serial execution, HTTP caching, write stubbing) is
applied by reassigning attributes on the imported `contact_finder` module at
runtime.

What it does
  1. Reproduces the static baseline from the 11 CSV exports (CLAUDE_CODE_BRIEF §1).
  2. Live-runs `enrich_with_contacts` over a seeded sample of N companies and
     records per-source hit rate, latency, and HTTP failure breakdown.
  3. Writes aggregates to bench_<label>.json (repo root) and one row per
     discovered contact to <out-dir>/bench_<label>_contacts.jsonl (OUTSIDE repo).

Safety (CLAUDE_CODE_BRIEF §0)
  * Never imports database.py / app.py / email_sender.py — asserted at startup.
    The enrichment path touches no MongoDB at all; its only persistent write is
    hr_database_lookup.add_entry(), which is stubbed to a no-op here.
  * GH_PAT is removed from the environment so nothing can dispatch a workflow.
  * Serper is OFF for every acceptance benchmark so all runs share one source set.
  * Contact-bearing artefacts (JSONL, HTTP cache) are written outside the repo,
    because .gitignore does not protect against `zip -r` of the working tree.

Note: CLAUDE_CODE_BRIEF.md (referenced below as §0/§1/§5) is deliberately kept
OUTSIDE the repo at ../placementradar-bench/ until Phase H ships, because it
documents unpatched vulnerabilities.

Usage
    python tools/bench.py --n 5            # smoke run
    python tools/bench.py                  # full run (N=50)
"""

import argparse
import concurrent.futures as _cf
import hashlib
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import types
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Windows consoles default to cp1252; a non-ASCII company name would otherwise
# kill a long run with UnicodeEncodeError. Degrade characters, never crash.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# ── Rule 2: make it impossible to dispatch a GitHub workflow from this process ─
os.environ.pop('GH_PAT', None)

import pandas as pd  # noqa: E402  (after sys.path setup)


# ═════════════════════════════════════════════════════════════════════════════
# CSV manifest — the exact 11 files behind the 3,355 figure (brief §1)
# `placement_jobs.scsv.csv` is intentionally counted twice: the root copy and
# the `restored data/` copy are both part of the stated total.
# ═════════════════════════════════════════════════════════════════════════════
MANIFEST = [
    ('restored data/placement_jobs (1).csv', 100),
    ('restored data/placement_jobs (2).csv', 354),
    ('restored data/placement_jobs (3).csv', 354),
    ('restored data/placement_jobs (4).csv', 52),
    ('restored data/placement_jobs (5).csv', 16),
    ('restored data/placement_jobs (6).csv', 476),
    ('restored data/placement_jobs (7).csv', 481),
    ('restored data/placement_jobs (8).csv', 490),
    ('restored data/placement_jobs.csv', 52),
    ('restored data/placement_jobs.scsv.csv', 490),
    ('placement_jobs.scsv.csv', 490),
]
EXPECTED_TOTAL_ROWS = 3355
EXPECTED_UNIQUE_TRIPLES = 532
EXPECTED_UNIQUE_COMPANIES = 321

# Brief §1 figures, printed beside ours for comparison (context, not targets).
BRIEF_BASELINE = {
    'total_rows': 3355,
    'unique_triples': 532,
    'unique_companies': 321,
    'pct_rows_with_email': 27.3,
    'pct_rows_with_phone': 14.3,
    'distinct_emails': 121,
    'distinct_phones': 148,
    'mean_contacts_per_company': 0.7,
}

# ── Email classification (measurement only; canonical versions land later) ────
# Per brief §2 a "role account" is a non-personal mailbox (hr@, careers@, info@).
# Per brief §5 the quality metric counts hr@-family AND person-like addresses as
# the *good* ones, and only generic mailboxes (info@/sales@) as bad — so we track
# the two families separately rather than collapsing them.
HR_FAMILY_PREFIXES = {
    'hr', 'hrd', 'hrteam', 'careers', 'career', 'jobs', 'job', 'recruitment',
    'recruiting', 'recruiter', 'hiring', 'apply', 'placement', 'placements',
    'campus', 'talent', 'people', 'staffing',
}
GENERIC_ROLE_PREFIXES = {
    'info', 'contact', 'contactus', 'sales', 'support', 'admin', 'office',
    'enquiry', 'enquiries', 'inquiry', 'inquiries', 'mail', 'email', 'help',
    'hello', 'general', 'reception', 'marketing', 'business', 'bd', 'service',
    'services', 'customercare', 'care', 'team', 'query', 'queries',
}
PERSONAL_PROVIDER_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.in', 'yahoo.co.in',
    'ymail.com', 'outlook.com', 'hotmail.com', 'hotmail.co.uk', 'live.com',
    'msn.com', 'rediffmail.com', 'rediff.com', 'icloud.com', 'me.com',
    'aol.com', 'protonmail.com', 'proton.me', 'zoho.com', 'mail.com',
}

# Source ids we instrument, mapped to the contact_finder attribute name.
SOURCE_FUNCS = {
    'desc':       '_extract_from_desc',
    'page':       '_fetch_job_page',
    'site':       '_scrape_company_site',
    'zauba':      '_scrape_zauba',
    'jd':         '_scrape_justdial',
    'im':         '_scrape_indiamart',
    'sulekha':    '_scrape_sulekha',
    'tradeindia': '_scrape_tradeindia',
    'photon':     '_photon_crawl',
    'serp':       '_search_serper',
    'hrdb':       'db_lookup',
}

BLOCKING_THRESHOLD = 0.30   # non-200 rate above this ⇒ measuring blocking, not yield


# ═════════════════════════════════════════════════════════════════════════════
# Context propagation
# Execution is serial, but a source runs in a worker thread (see _LazyFuture),
# and a timed-out straggler keeps running after we move on. Thread-local context
# set *inside* the worker keeps HTTP attribution correct even for stragglers.
# ═════════════════════════════════════════════════════════════════════════════
_tls = threading.local()


def _ctx():
    return getattr(_tls, 'company', None), getattr(_tls, 'source', None)


def _set_ctx(company=None, source=None):
    _tls.company = company
    _tls.source = source


# ═════════════════════════════════════════════════════════════════════════════
# Recorder
# ═════════════════════════════════════════════════════════════════════════════
class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = Counter()
        self.hits = Counter()
        self.emails_found = Counter()
        self.phones_found = Counter()
        self.latencies = defaultdict(list)
        self.http = defaultdict(Counter)     # source_id -> Counter of outcomes
        self.contacts = []                   # discovered contact rows

    def attempt(self, sid):
        with self.lock:
            self.attempts[sid] += 1

    def finish(self, sid, latency_ms, n_emails, n_phones):
        with self.lock:
            self.latencies[sid].append(latency_ms)
            self.emails_found[sid] += n_emails
            self.phones_found[sid] += n_phones
            if n_emails or n_phones:
                self.hits[sid] += 1

    def http_event(self, sid, kind):
        with self.lock:
            self.http[sid][kind] += 1

    def contact(self, company, ctype, value, source):
        with self.lock:
            self.contacts.append({
                'company': company, 'type': ctype,
                'value': value, 'source': source,
            })


REC = Recorder()


# ═════════════════════════════════════════════════════════════════════════════
# HTTP shim — caches 2xx ONLY
# A 403/429/5xx/timeout is never cached, so a rate-limited first run cannot
# freeze itself in as the permanent baseline; those are re-attempted next run.
# ═════════════════════════════════════════════════════════════════════════════
class _CachedResponse:
    """Minimal Response stand-in covering everything contact_finder touches."""

    def __init__(self, status_code, text, url):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = {}
        self.encoding = 'utf-8'

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    @property
    def content(self):
        return (self.text or '').encode('utf-8', errors='replace')

    def json(self):
        return json.loads(self.text or '{}')


class CachingRequests:
    """Stands in for `contact_finder.requests`."""

    def __init__(self, real, cache_dir, rec):
        self._real = real
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._rec = rec
        # Re-export exception classes in case any source references them.
        self.exceptions = real.exceptions
        self.Timeout = real.exceptions.Timeout
        self.RequestException = real.exceptions.RequestException

    # -- key ---------------------------------------------------------------
    def _key(self, method, url, kw):
        payload = {
            'm': method.upper(),
            'u': url,
            'p': sorted((kw.get('params') or {}).items()) if isinstance(kw.get('params'), dict) else None,
            'j': kw.get('json'),
            'd': kw.get('data') if isinstance(kw.get('data'), (str, bytes)) else None,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()

    # -- public ------------------------------------------------------------
    def get(self, url, **kw):
        return self._request('GET', url, **kw)

    def post(self, url, **kw):
        return self._request('POST', url, **kw)

    def request(self, method, url, **kw):
        return self._request(method, url, **kw)

    def _request(self, method, url, **kw):
        _, sid = _ctx()
        sid = sid or 'unknown'
        key = self._key(method, url, kw)
        path = self._dir / f'{key}.json'

        if path.exists():
            try:
                blob = json.loads(path.read_text(encoding='utf-8'))
                self._rec.http_event(sid, 'cache_hit')
                self._rec.http_event(sid, 'http_2xx')
                return _CachedResponse(blob['status'], blob['text'], blob.get('url', url))
            except Exception:
                pass  # corrupt cache entry — fall through and refetch

        try:
            r = self._real.request(method, url, **kw)
        except self._real.exceptions.Timeout:
            self._rec.http_event(sid, 'timeouts')
            raise
        except Exception:
            self._rec.http_event(sid, 'exceptions')
            raise

        sc = r.status_code
        if 200 <= sc < 300:
            self._rec.http_event(sid, 'http_2xx')
            try:
                path.write_text(json.dumps(
                    {'status': sc, 'text': r.text, 'url': r.url},
                ), encoding='utf-8')
            except Exception:
                pass          # cache write failure must never break the bench
        elif sc == 403:
            self._rec.http_event(sid, 'http_403')
        elif sc == 429:
            self._rec.http_event(sid, 'http_429')
        elif 500 <= sc < 600:
            self._rec.http_event(sid, 'http_5xx')
        else:
            self._rec.http_event(sid, 'http_other')
        return r


# ═════════════════════════════════════════════════════════════════════════════
# Serial executor
# `_enrich_single_job` submits ~8 sources up front, then collects each with
# .result(timeout=12/15/20). A real max_workers=1 pool would start that timeout
# clock at .result() while the task sat queued, so sources behind a slow one
# would "time out" without ever running — measuring queue starvation, not yield.
# So submit() is LAZY: the call is deferred until .result(timeout=t), executed
# in its own single-thread executor honouring that timeout. Exactly one request
# is in flight at a time, and the pipeline's real cutoffs still apply.
# ═════════════════════════════════════════════════════════════════════════════
class _LazyFuture:
    def __init__(self, fn, args, kwargs, parent_company):
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self._parent_company = parent_company
        self._done = False
        self._value = None
        self._exc = None

    def _run(self):
        _set_ctx(company=self._parent_company, source=None)
        return self._fn(*self._args, **self._kwargs)

    def result(self, timeout=None):
        if self._done:
            if self._exc:
                raise self._exc
            return self._value
        ex = _cf.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(self._run)
        try:
            self._value = fut.result(timeout=timeout)
            self._done = True
            return self._value
        except _cf.TimeoutError:
            # Production drops this source at the same cutoff; record and move on.
            sid = getattr(self._fn, '_bench_sid', 'unknown')
            REC.http_event(sid, 'timeouts')
            raise
        except Exception as e:
            self._exc = e
            self._done = True
            raise
        finally:
            # Never block on an abandoned straggler (mirrors production).
            ex.shutdown(wait=False)

    def cancel(self):
        return False

    def done(self):
        return self._done


class SerialExecutor:
    def __init__(self, max_workers=None, *a, **kw):
        pass

    def submit(self, fn, *args, **kwargs):
        company, _ = _ctx()
        return _LazyFuture(fn, args, kwargs, company)

    def map(self, fn, iterable, *a, **kw):
        return [fn(x) for x in iterable]

    def shutdown(self, wait=True, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Source instrumentation
# ═════════════════════════════════════════════════════════════════════════════
def _split_result(sid, result):
    """Normalize the differing return shapes of the source functions."""
    if result is None:
        return [], []
    if sid == 'desc' and isinstance(result, tuple) and len(result) == 2:
        return list(result[0] or []), list(result[1] or [])
    if sid == 'hrdb' and isinstance(result, list):
        return [r.get('hr_email', '') for r in result if isinstance(r, dict)], []
    if isinstance(result, dict):
        return list(result.get('emails') or []), list(result.get('phones') or [])
    return [], []


def _norm_email(e):
    return (e or '').strip().lower()


def _norm_phone(p):
    d = re.sub(r'\D', '', str(p or ''))
    if len(d) == 12 and d.startswith('91'):
        d = d[2:]
    if len(d) == 13 and d.startswith('091'):
        d = d[3:]
    if len(d) == 11 and d.startswith('0'):
        d = d[1:]
    return d if len(d) == 10 else ''


def _wrap_source(sid, fn):
    def wrapper(*args, **kwargs):
        company, prev_source = _ctx()

        # Nested call: `_extract_from_desc` is also a helper used *inside*
        # _scrape_company_site / _photon_crawl / _fetch_job_page / _scrape_zauba.
        # Recording it there would both inflate its attempt count and steal
        # credit for contacts the outer source actually found. When a source is
        # already active, pass through and let the outer source own the result.
        if prev_source is not None and prev_source != sid:
            return fn(*args, **kwargs)

        _tls.source = sid
        REC.attempt(sid)
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            REC.finish(sid, (time.perf_counter() - t0) * 1000.0, 0, 0)
            _tls.source = prev_source
            raise
        latency_ms = (time.perf_counter() - t0) * 1000.0
        emails, phones = _split_result(sid, result)
        emails = [_norm_email(e) for e in emails if e]
        phones = [_norm_phone(p) for p in phones]
        phones = [p for p in phones if p]
        REC.finish(sid, latency_ms, len(emails), len(phones))
        for e in dict.fromkeys(emails):
            REC.contact(company, 'email', e, sid)
        for p in dict.fromkeys(phones):
            REC.contact(company, 'phone', p, sid)
        _tls.source = prev_source
        return result

    wrapper._bench_sid = sid
    wrapper.__name__ = getattr(fn, '__name__', sid)
    return wrapper


def install_hooks(cf_mod, cache_dir):
    """Apply every runtime hook to the imported contact_finder module."""
    # 1. Serial execution (replaces the 4x8 fan-out).
    serial_ns = types.SimpleNamespace(ThreadPoolExecutor=SerialExecutor)
    for attr in ('TimeoutError', 'Future', 'as_completed', 'wait'):
        if hasattr(_cf, attr):
            setattr(serial_ns, attr, getattr(_cf, attr))
    cf_mod.concurrent = types.SimpleNamespace(futures=serial_ns)

    # 2. HTTP caching + per-source HTTP accounting.
    cf_mod.requests = CachingRequests(cf_mod.requests, cache_dir, REC)

    # 3. Rule 6 — never append scraped contacts to hr_database.csv.
    def _no_op_add(*a, **kw):
        return False
    cf_mod.db_add = _no_op_add

    # 4. Per-source timing / yield instrumentation.
    for sid, attr in SOURCE_FUNCS.items():
        if hasattr(cf_mod, attr):
            setattr(cf_mod, attr, _wrap_source(sid, getattr(cf_mod, attr)))

    # 5. Company context for the job currently being enriched.
    orig_single = cf_mod._enrich_single_job

    def _tracked_single(job, *a, **kw):
        _set_ctx(company=(job.get('company') or '').strip(), source=None)
        return orig_single(job, *a, **kw)

    cf_mod._enrich_single_job = _tracked_single


# ═════════════════════════════════════════════════════════════════════════════
# Static baseline (brief §1)
# ═════════════════════════════════════════════════════════════════════════════
def load_manifest():
    frames, per_file, mismatches = [], [], []
    for rel, expected in MANIFEST:
        path = REPO_ROOT / rel
        if not path.exists():
            per_file.append({'file': rel, 'expected': expected, 'actual': None,
                             'status': 'MISSING'})
            mismatches.append(rel)
            continue
        df = pd.read_csv(path, on_bad_lines='skip', low_memory=False)
        actual = len(df)
        ok = (actual == expected)
        per_file.append({'file': rel, 'expected': expected, 'actual': actual,
                         'status': 'ok' if ok else 'MISMATCH'})
        if not ok:
            mismatches.append(rel)
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return combined, per_file, mismatches


def _cell(v):
    if v is None:
        return ''
    s = str(v).strip()
    return '' if s.lower() in ('nan', 'none', 'nat') else s


def static_baseline(df):
    """Coverage under all three denominators.

    §1 of the brief reported 27.3% / 14.3% against the 532 deduplicated triples,
    not the 3,355 rows — recomputing on triples here reproduces those figures and
    confirms the denominator. §5 measures "companies with >=1 email", so the
    PRIMARY denominator is unique companies (321). §1 is superseded.
    """
    total_rows = len(df)
    companies, emails_all, phones_all = set(), set(), set()
    rows_email = rows_phone = 0
    per_company = defaultdict(lambda: {'emails': set(), 'phones': set()})
    per_triple = defaultdict(lambda: {'emails': set(), 'phones': set()})

    for rec in df.to_dict('records'):
        company = _cell(rec.get('company'))
        title = _cell(rec.get('title'))
        location = _cell(rec.get('location'))
        triple = (company.lower(), title.lower(), location.lower())
        if company:
            companies.add(company)

        es = [_norm_email(e) for e in _cell(rec.get('emails')).split(',')]
        es = [e for e in es if e and '@' in e]
        ps = [_norm_phone(p) for p in _cell(rec.get('phone')).split(',')]
        ps = [p for p in ps if p]

        if es:
            rows_email += 1
        if ps:
            rows_phone += 1
        emails_all.update(es)
        phones_all.update(ps)

        per_triple[triple]['emails'].update(es)
        per_triple[triple]['phones'].update(ps)
        if company:
            per_company[company]['emails'].update(es)
            per_company[company]['phones'].update(ps)

    n_rows = total_rows
    n_triples = len(per_triple)
    n_companies = len(companies)

    def _cov(buckets, n):
        we = sum(1 for v in buckets.values() if v['emails'])
        wp = sum(1 for v in buckets.values() if v['phones'])
        return {
            'n': n,
            'with_email_n': we,
            'with_email_pct': round(we / n * 100, 1) if n else 0.0,
            'with_phone_n': wp,
            'with_phone_pct': round(wp / n * 100, 1) if n else 0.0,
        }

    mean_per_company = (
        statistics.fmean([len(v['emails']) + len(v['phones']) for v in per_company.values()])
        if per_company else 0.0
    )

    return {
        'primary_denominator': 'unique_companies',
        'denominators': {
            'rows': n_rows,
            'unique_triples': n_triples,
            'unique_companies': n_companies,
        },
        'coverage': {
            'by_rows': {
                'n': n_rows,
                'with_email_n': rows_email,
                'with_email_pct': round(rows_email / n_rows * 100, 1) if n_rows else 0.0,
                'with_phone_n': rows_phone,
                'with_phone_pct': round(rows_phone / n_rows * 100, 1) if n_rows else 0.0,
            },
            'by_triples': _cov(per_triple, n_triples),
            'by_company': _cov(per_company, n_companies),
        },
        'distinct_emails': len(emails_all),
        'distinct_phones': len(phones_all),
        'mean_contacts_per_company': round(mean_per_company, 2),
        'mean_contacts_per_company_global_ratio': (
            round((len(emails_all) + len(phones_all)) / n_companies, 2) if n_companies else 0.0),
    }


def recompute_targets(stats):
    """Re-derive the §5 acceptance targets from the per-company baseline.

    The original §5 targets were written against §1's triple-denominator figures
    (27.3% -> >=60%, 14.3% -> >=35%, 0.7 -> >=3.0). Those "before" numbers are
    superseded, so the targets are re-derived by preserving the improvement
    MULTIPLIER each one implied, applied to the true per-company baseline.
    """
    cov = stats['coverage']['by_company']
    base_email = cov['with_email_pct']
    base_phone = cov['with_phone_pct']
    base_mean = stats['mean_contacts_per_company']

    specs = [
        ('companies_with_email_pct', base_email, 60.0, 60.0 / 27.3, 100.0),
        ('companies_with_phone_pct', base_phone, 35.0, 35.0 / 14.3, 100.0),
        ('mean_contacts_per_company', base_mean, 3.0, 3.0 / 0.7, None),
    ]
    out = {}
    for key, base, absolute, mult, cap in specs:
        derived = base * mult
        if cap is not None:
            derived = min(derived, cap)
        out[key] = {
            'baseline': round(base, 2),
            'target': absolute,                     # THE BAR
            'target_basis': 'absolute (§5)',
            'multiplier_derived_target': round(derived, 1),   # informational only
            'multiplier_from_s5': round(mult, 2),
        }
    out['non_role_email_share_pct'] = {
        'baseline': None,
        'target': 40.0,
        'target_basis': 'absolute (§5)',
        'multiplier_derived_target': None,
        'multiplier_from_s5': None,
        'note': 'A share of emails — unaffected by the denominator fix either way.',
    }
    out['_basis'] = (
        'Coverage uses the per-unique-company denominator; §1 is superseded. '
        'The BAR is §5\'s original ABSOLUTE figures (>=60% email, >=35% phone, '
        '>=3.0 contacts/company, >=40% non-role). The multiplier-derived column is '
        'shown only for comparison: a multiplier anchored to a baseline this broken '
        'is not a meaningful objective.')
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Sampling
# ═════════════════════════════════════════════════════════════════════════════
def build_sample(df, n, seed):
    best = {}
    for rec in df.to_dict('records'):
        company = _cell(rec.get('company'))
        if not company:
            continue
        row = {
            'company': company,
            'company_url': _cell(rec.get('company_url')),
            'location': _cell(rec.get('location')) or 'India',
            'job_url': _cell(rec.get('job_url')),
            'description': _cell(rec.get('description')),
        }
        prev = best.get(company)
        if prev is None or (not prev['company_url'] and row['company_url']):
            best[company] = row
    names = sorted(best)
    rng = random.Random(seed)
    chosen = rng.sample(names, min(n, len(names)))
    return [dict(best[c]) for c in sorted(chosen)]


# ═════════════════════════════════════════════════════════════════════════════
# Classification + metrics
# ═════════════════════════════════════════════════════════════════════════════
def classify_email(email):
    local, _, domain = email.partition('@')
    local = local.lower()
    domain = domain.lower()
    hr_family = local in HR_FAMILY_PREFIXES
    generic = local in GENERIC_ROLE_PREFIXES
    return {
        'is_role_account': hr_family or generic,     # brief §2 definition
        'is_hr_family': hr_family,
        'is_generic_role': generic,
        'is_person_like': not hr_family and not generic,
        'is_personal_provider': domain in PERSONAL_PROVIDER_DOMAINS,
    }


def _pctl(values, p):
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0])
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo))


def per_source_metrics():
    out, flags = {}, []
    sids = set(REC.attempts) | set(REC.http)
    for sid in sorted(sids):
        attempts = REC.attempts[sid]
        h = REC.http[sid]
        non_200 = h['http_403'] + h['http_429'] + h['http_5xx'] + h['http_other'] \
            + h['timeouts'] + h['exceptions']
        http_total = h['http_2xx'] + non_200
        non_200_rate = (non_200 / http_total) if http_total else 0.0
        blocking = non_200_rate > BLOCKING_THRESHOLD and http_total > 0
        lat = REC.latencies[sid]
        row = {
            'attempts': attempts,
            'hits': REC.hits[sid],
            'hit_rate': round(REC.hits[sid] / attempts, 3) if attempts else 0.0,
            'emails_found': REC.emails_found[sid],
            'phones_found': REC.phones_found[sid],
            'p50_latency_ms': _pctl(lat, 0.50),
            'p95_latency_ms': _pctl(lat, 0.95),
            'http_requests': http_total,
            'http_2xx': h['http_2xx'],
            'http_403': h['http_403'],
            'http_429': h['http_429'],
            'http_5xx': h['http_5xx'],
            'http_other': h['http_other'],
            'timeouts': h['timeouts'],
            'exceptions': h['exceptions'],
            'cache_hits': h['cache_hit'],
            'non_200_rate': round(non_200_rate, 3),
            'blocking_suspected': blocking,
        }
        out[sid] = row
        if blocking:
            flags.append(
                f'{sid}: non-200 rate {non_200_rate:.0%} '
                f'(>{BLOCKING_THRESHOLD:.0%}) — this source\'s hit rate is '
                f'measuring blocking, not yield.'
            )
    return out, flags


# ═════════════════════════════════════════════════════════════════════════════
# Reporting
# ═════════════════════════════════════════════════════════════════════════════
def _hr(title):
    print('\n' + '=' * 78)
    print(title)
    print('=' * 78)


def main():
    ap = argparse.ArgumentParser(description='Phase A contact-discovery benchmark.')
    ap.add_argument('--n', type=int, default=50, help='companies to sample (default 50)')
    ap.add_argument('--seed', type=int, default=42, help='sample seed (default 42)')
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'),
                    help='directory for PII artefacts; MUST be outside the repo')
    ap.add_argument('--label', default='baseline', help='output label (default baseline)')
    args = ap.parse_args()

    # ── Guard: PII artefacts must never land inside the working tree ──────────
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir == REPO_ROOT or REPO_ROOT in out_dir.parents:
        print(f'FATAL: --out-dir resolves inside the repo ({out_dir}).')
        print('       Contact data and cached HTML must live outside the working')
        print('       tree — .gitignore does not protect against `zip -r`.')
        return 2
    cache_dir = out_dir / 'http_cache'
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    t_start = time.perf_counter()

    # ── Import the pipeline, then assert nothing forbidden came with it ───────
    import contact_finder as cf
    for forbidden in ('database', 'app', 'email_sender'):
        if forbidden in sys.modules:
            print(f'FATAL: `{forbidden}` was imported — the bench must not touch it.')
            return 2

    install_hooks(cf, cache_dir)

    # ── 1. Static baseline ───────────────────────────────────────────────────
    _hr('1. STATIC BASELINE — CSV manifest')
    combined, per_file, mismatches = load_manifest()
    print(f'{"file":<44}{"expected":>10}{"actual":>10}  status')
    print('-' * 78)
    for r in per_file:
        actual = '—' if r['actual'] is None else r['actual']
        print(f'{r["file"]:<44}{r["expected"]:>10}{actual:>10}  {r["status"]}')
    print('-' * 78)
    total_expected = sum(e for _, e in MANIFEST)
    print(f'{"TOTAL":<44}{total_expected:>10}{len(combined):>10}  '
          f'{"ok" if len(combined) == EXPECTED_TOTAL_ROWS else "MISMATCH"}')
    if mismatches:
        print(f'\n!! {len(mismatches)} file(s) did not match the expected count. '
              f'Reporting actuals, not adjusting.')

    stats = static_baseline(combined)
    targets = recompute_targets(stats)
    d = stats['denominators']
    cov = stats['coverage']

    print(f'\nCounts     rows={d["rows"]}  unique_triples={d["unique_triples"]}  '
          f'unique_companies={d["unique_companies"]}')
    print('\nCoverage under each denominator (all three reported for traceability):')
    print(f'{"denominator":<22}{"n":>7}{">=1 email":>14}{">=1 phone":>14}')
    print('-' * 78)
    for label, key in [('rows', 'by_rows'), ('unique triples', 'by_triples'),
                       ('unique companies', 'by_company')]:
        c = cov[key]
        star = '   <- PRIMARY' if key == 'by_company' else ''
        print(f'{label:<22}{c["n"]:>7}'
              f'{c["with_email_n"]:>7} {c["with_email_pct"]:>5.1f}%'
              f'{c["with_phone_n"]:>7} {c["with_phone_pct"]:>5.1f}%{star}')

    # Confirm the user's diagnosis: §1 was computed on the triple denominator.
    t = cov['by_triples']
    print(f'\n§1 cross-check (SUPERSEDED, kept for traceability):')
    print(f'  brief §1 said       27.3% email / 14.3% phone')
    print(f'  by_triples gives    {t["with_email_pct"]:.1f}% email / '
          f'{t["with_phone_pct"]:.1f}% phone'
          f'   {"<- reproduces §1" if abs(t["with_email_pct"] - 27.3) < 0.6 else "<- still differs"}')

    print(f'\nDistinct emails {stats["distinct_emails"]}   '
          f'distinct phones {stats["distinct_phones"]}   '
          f'mean contacts/company {stats["mean_contacts_per_company"]}')

    print('\nAcceptance targets (per-company denominator). '
          'BAR = absolute §5; multiplier shown for comparison only:')
    print(f'{"metric":<34}{"baseline":>10}{"TARGET":>10}{"(x-derived)":>14}')
    print('-' * 78)
    for key in ('companies_with_email_pct', 'companies_with_phone_pct',
                'mean_contacts_per_company'):
        tg = targets[key]
        print(f'{key:<34}{tg["baseline"]:>10}{tg["target"]:>10}'
              f'{tg["multiplier_derived_target"]:>14}')
    print(f'{"non_role_email_share_pct":<34}{"-":>10}'
          f'{targets["non_role_email_share_pct"]["target"]:>10}{"-":>14}')

    # ── 2. Live bench ────────────────────────────────────────────────────────
    sample = build_sample(combined, args.n, args.seed)
    _hr(f'2. LIVE BENCH — {len(sample)} companies '
        f'(seed={args.seed}, serper=OFF, serial=ON)')
    print('One HTTP request in flight at a time; only 2xx responses are cached.')
    print('First run is slow by design. Progress:\n')

    jobs = [dict(s) for s in sample]
    t_bench = time.perf_counter()
    for i, job in enumerate(jobs, 1):
        cf.enrich_with_contacts([job], settings={'serper_api_key': ''})
        n_em = len([e for e in job.get('emails', '').split(',') if e.strip()])
        n_ph = len([p for p in job.get('phone', '').split(',') if p.strip()])
        print(f'  [{i:>3}/{len(jobs)}] {job["company"][:38]:<40} '
              f'{n_em} email(s), {n_ph} phone(s)  '
              f'({time.perf_counter() - t_bench:,.0f}s elapsed)', flush=True)
    bench_secs = time.perf_counter() - t_bench

    # ── 3. Results ───────────────────────────────────────────────────────────
    final_emails, final_phones = set(), set()
    kept = set()
    per_company_counts = []
    n_comp_email = n_comp_phone = 0
    for job in jobs:
        company = job['company']
        es = {_norm_email(e) for e in job.get('emails', '').split(',') if e.strip()}
        ps = {_norm_phone(p) for p in job.get('phone', '').split(',') if p.strip()}
        es = {e for e in es if e}
        ps = {p for p in ps if p}
        final_emails |= es
        final_phones |= ps
        if es:
            n_comp_email += 1
        if ps:
            n_comp_phone += 1
        per_company_counts.append(len(es) + len(ps))
        for e in es:
            kept.add((company, 'email', e))
        for p in ps:
            kept.add((company, 'phone', p))

    # Deduplicate discovered contacts on (company, type, value, source).
    seen, contact_rows = set(), []
    for c in REC.contacts:
        if not c['company'] or not c['value']:
            continue
        k = (c['company'], c['type'], c['value'], c['source'])
        if k in seen:
            continue
        seen.add(k)
        row = dict(c)
        row['kept_in_final'] = (c['company'], c['type'], c['value']) in kept
        if c['type'] == 'email':
            row.update(classify_email(c['value']))
        contact_rows.append(row)

    def _pct(part, whole):
        return round(part / whole * 100, 1) if whole else 0.0

    fin_cls = [classify_email(e) for e in final_emails]
    n_fin = len(fin_cls)
    disc_emails = {r['value'] for r in contact_rows if r['type'] == 'email'}
    disc_cls = [classify_email(e) for e in disc_emails]

    run = {
        'seed': args.seed,
        'n_companies': len(jobs),
        'serper': False,
        'serial': True,
        'wall_clock_seconds': round(bench_secs, 1),
        'denominator': 'sampled_companies',
        'mean_contacts_per_company': round(statistics.fmean(per_company_counts), 2)
                                     if per_company_counts else 0.0,
        'companies_with_email_n': n_comp_email,
        'companies_with_email_pct': _pct(n_comp_email, len(jobs)),
        'companies_with_phone_n': n_comp_phone,
        'companies_with_phone_pct': _pct(n_comp_phone, len(jobs)),
        'distinct_emails': len(final_emails),
        'distinct_phones': len(final_phones),
        'pct_role_accounts': _pct(sum(c['is_role_account'] for c in fin_cls), n_fin),
        'pct_personal_providers': _pct(sum(c['is_personal_provider'] for c in fin_cls), n_fin),
        'pct_hr_family': _pct(sum(c['is_hr_family'] for c in fin_cls), n_fin),
        'pct_generic_role': _pct(sum(c['is_generic_role'] for c in fin_cls), n_fin),
        'pct_person_like': _pct(sum(c['is_person_like'] for c in fin_cls), n_fin),
        'pct_non_role_per_brief_s5': _pct(
            sum(c['is_hr_family'] or c['is_person_like'] for c in fin_cls), n_fin),
        'discovered_contacts_total': len(contact_rows),
        'discovered_distinct_emails': len(disc_emails),
        'discovered_pct_generic_role': _pct(
            sum(c['is_generic_role'] for c in disc_cls), len(disc_cls)),
    }

    sources, flags = per_source_metrics()

    _hr('3. RESULTS  (denominator: sampled companies)')
    tgt_e = targets['companies_with_email_pct']['target']
    tgt_p = targets['companies_with_phone_pct']['target']
    tgt_m = targets['mean_contacts_per_company']['target']
    print(f'Companies with >=1 email  : {run["companies_with_email_n"]}/{len(jobs)} '
          f'= {run["companies_with_email_pct"]}%   (target {tgt_e}%)')
    print(f'Companies with >=1 phone  : {run["companies_with_phone_n"]}/{len(jobs)} '
          f'= {run["companies_with_phone_pct"]}%   (target {tgt_p}%)')
    print(f'Mean contacts per company : {run["mean_contacts_per_company"]}'
          f'   (target {tgt_m})')
    print(f'Distinct emails (final)   : {run["distinct_emails"]}')
    print(f'Distinct phones (final)   : {run["distinct_phones"]}')
    print(f'Role accounts (§2)        : {run["pct_role_accounts"]}%')
    print(f'  of which hr@-family     : {run["pct_hr_family"]}%')
    print(f'  of which generic (info@) : {run["pct_generic_role"]}%')
    print(f'Person-like addresses     : {run["pct_person_like"]}%')
    print(f'Non-role per §5 target    : {run["pct_non_role_per_brief_s5"]}%  (target >=40%)')
    print(f'Personal providers        : {run["pct_personal_providers"]}%')
    print(f'Contacts discovered       : {run["discovered_contacts_total"]} '
          f'(pre-truncation, incl. dropped)')

    _hr('4. PER-SOURCE')
    hdr = (f'{"source":<12}{"att":>5}{"hit":>5}{"rate":>7}{"em":>5}{"ph":>5}'
           f'{"p50ms":>8}{"p95ms":>8}{"2xx":>6}{"403":>5}{"429":>5}{"5xx":>5}'
           f'{"oth":>5}{"t/o":>5}{"err":>5}{"non2xx":>8}')
    print(hdr)
    print('-' * len(hdr))
    for sid, m in sources.items():
        print(f'{sid:<12}{m["attempts"]:>5}{m["hits"]:>5}{m["hit_rate"]:>7.2f}'
              f'{m["emails_found"]:>5}{m["phones_found"]:>5}'
              f'{(m["p50_latency_ms"] or 0):>8}{(m["p95_latency_ms"] or 0):>8}'
              f'{m["http_2xx"]:>6}{m["http_403"]:>5}{m["http_429"]:>5}'
              f'{m["http_5xx"]:>5}{m["http_other"]:>5}{m["timeouts"]:>5}'
              f'{m["exceptions"]:>5}{m["non_200_rate"]:>8.0%}')
    if flags:
        print('\n!! BLOCKING FLAGS')
        for f in flags:
            print(f'   - {f}')
    else:
        print('\nNo source exceeded the 30% non-200 threshold.')

    # ── 4. Write outputs ─────────────────────────────────────────────────────
    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'label': args.label,
        'note': ('Serper is OFF for all acceptance benchmarks so every run shares one '
                 'source set. These numbers are NOT comparable to CLAUDE_CODE_BRIEF §1, '
                 'which was measured from CSVs where Google Search was the top email '
                 'source. §1 is context; this file is the baseline.'),
        'static_baseline': stats,
        'acceptance_targets': targets,
        'static_baseline_brief_s1_SUPERSEDED': {
            **BRIEF_BASELINE,
            '_note': ('§1 computed coverage against the 532 deduplicated triples, not the '
                      '3,355 rows. Superseded: all coverage metrics and acceptance targets '
                      'now use the per-unique-company denominator. Kept for traceability.'),
        },
        'manifest': per_file,
        'manifest_mismatches': mismatches,
        'run': run,
        'per_source': sources,
        'flags': flags,
    }
    json_path = REPO_ROOT / f'bench_{args.label}.json'
    json_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    jsonl_path = out_dir / f'bench_{args.label}_contacts.jsonl'
    with open(jsonl_path, 'w', encoding='utf-8') as f:
        for row in contact_rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    _hr('5. OUTPUT')
    print(f'Aggregates    : {json_path}')
    print(f'Per-contact   : {jsonl_path}   ({len(contact_rows)} rows, outside repo)')
    print(f'HTTP cache    : {cache_dir}')
    print(f'Total runtime : {time.perf_counter() - t_start:,.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
