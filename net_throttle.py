"""
net_throttle.py — per-registrable-domain politeness: one request at a time, a minimum
gap, exponential backoff with jitter, and a rotating User-Agent pool. (brief §G)

WHY PER DOMAIN AND NOT GLOBAL. The pipeline fans out ~8 sources concurrently, but those
sources hit *different* hosts. A global limiter would serialise the whole pipeline to
fix a problem that only exists per host. The unit that gets you blocked is the
registrable domain, so that is the unit that gets the lock.

`in.indeed.com` and `indeed.com` are ONE domain for this purpose — subdomain-level
limiting is how a scraper accidentally sends 5 concurrent requests to the same
operator. `_registrable()` collapses them, using the same multi-part-TLD handling as
the rest of the project.

THE USER-AGENT POOL IS NOT EVASION. The repo currently sends a single hardcoded
Chrome 124 string on every request from every source; that string is now old enough to
be a fingerprint in itself. The pool is a small set of current, realistic desktop
browsers, rotated per domain (not per request, which is itself a detectable pattern).
Combined with the gap and backoff, the goal is to behave like an ordinary client and
to back off when asked — `Retry-After` is honoured when present.

Contains no database import and no network call of its own; it wraps a caller's
request function, so it stays usable from the benchmark harness.
"""

import random
import threading
import time

_LOCK = threading.Lock()
_DOMAIN_LOCKS = {}
_LAST_REQUEST = {}
_DOMAIN_UA = {}

MIN_GAP_SECONDS = 1.0
MAX_CONCURRENT_PER_DOMAIN = 1
MAX_RETRIES = 3
BACKOFF_BASE = 2.0
BACKOFF_CAP = 30.0

MULTI_TLDS = ('co.in', 'com.au', 'co.uk', 'co.jp', 'com.sg', 'ac.in', 'edu.in',
              'org.in', 'net.in', 'gov.in', 'com.br', 'co.za')

USER_AGENTS = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/140.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/140.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) '
    'Version/18.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/140.0.0.0 Safari/537.36',
)


def _registrable(url_or_host):
    """Collapse to the registrable domain: `in.indeed.com` -> `indeed.com`."""
    h = (url_or_host or '').strip().lower()
    if '//' in h:
        h = h.split('//', 1)[1]
    h = h.split('/', 1)[0].split('?', 1)[0].split('@')[-1].split(':')[0].rstrip('.')
    if not h:
        return ''
    for t in MULTI_TLDS:
        if h.endswith('.' + t):
            parts = h[:-(len(t) + 1)].split('.')
            return f'{parts[-1]}.{t}' if parts else h
    parts = h.split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else h


def _domain_lock(domain):
    with _LOCK:
        s = _DOMAIN_LOCKS.get(domain)
        if s is None:
            s = _DOMAIN_LOCKS[domain] = threading.Semaphore(MAX_CONCURRENT_PER_DOMAIN)
        return s


def user_agent_for(url_or_host):
    """Stable per domain — a UA that changes every request is its own signature."""
    d = _registrable(url_or_host)
    with _LOCK:
        ua = _DOMAIN_UA.get(d)
        if ua is None:
            ua = _DOMAIN_UA[d] = random.choice(USER_AGENTS)
        return ua


def _wait_turn(domain):
    """Hold the minimum gap since this domain's previous request."""
    while True:
        with _LOCK:
            last = _LAST_REQUEST.get(domain, 0.0)
            now = time.time()
            wait = (last + MIN_GAP_SECONDS) - now
            if wait <= 0:
                _LAST_REQUEST[domain] = now
                return
        time.sleep(min(wait, MIN_GAP_SECONDS))


def _retry_after(resp):
    try:
        v = (resp.headers or {}).get('Retry-After')
        return min(float(v), BACKOFF_CAP) if v else None
    except Exception:
        return None


def polite_request(do_request, url, source_id=None, max_retries=MAX_RETRIES):
    """Run `do_request(url)` under this domain's semaphore, gap and backoff.

    `do_request` must return a response exposing `.status_code`, or raise. Retries on
    429 / 5xx with exponential backoff plus jitter, honouring `Retry-After`. A 403 is
    NOT retried — it is a decision, not congestion, and hammering it converts a refusal
    into a ban. Metrics are recorded per attempt when `source_id` is given.
    """
    try:
        import source_metrics as SM
    except Exception:
        SM = None

    domain = _registrable(url)
    sem = _domain_lock(domain)
    delay = BACKOFF_BASE
    last_exc = None

    for attempt in range(max_retries):
        sem.acquire()
        try:
            _wait_turn(domain)
            t0 = time.time()
            try:
                resp = do_request(url)
            except Exception as e:
                last_exc = e
                if SM and source_id:
                    SM.record_failure(source_id, e, url=url)
                resp = None
        finally:
            sem.release()

        if resp is not None:
            status = getattr(resp, 'status_code', None)
            if SM and source_id:
                SM.record_latency(source_id, (time.time() - t0) * 1000.0)
                if status is not None:
                    SM.record_http(source_id, status)
            if status == 403:
                return resp                     # refusal — do not retry
            if status is None or not (status == 429 or 500 <= status < 600):
                return resp
            wait = _retry_after(resp) or min(delay, BACKOFF_CAP)
        else:
            wait = min(delay, BACKOFF_CAP)

        if attempt == max_retries - 1:
            break
        time.sleep(wait + random.uniform(0, wait * 0.3))   # jitter
        delay *= BACKOFF_BASE

    if resp is not None:
        return resp
    if last_exc:
        raise last_exc
    return None
