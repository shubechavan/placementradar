"""
source_metrics.py — per-source instrumentation. "I want to see a source die the day
it dies." (brief §G)

THE PROBLEM THIS SOLVES. Phase A measured 5 of 10 sources returning zero contacts and
could not tell, from the code alone, which were blocked and which were merely finding
nothing. The reason is `contact_finder._get()`:

    try: return fetch_futs[key].result(timeout=t) or default
    except Exception: return default

That single handler swallows a source's exception AND its timeout and returns an empty
result. A source that is 100% blocked, 100% timing out, or raising on every call is
indistinguishable from one that simply found no contacts. Every "0.0% hit rate" in the
baseline was ambiguous for exactly this reason.

DESIGN CONSTRAINTS, both load-bearing:

1. **This module MUST NOT import `database` at module scope.** `contact_finder` imports
   it, and `tools/bench.py` asserts that `database`, `app` and `email_sender` never
   enter `sys.modules` during a benchmark run — that assertion is what keeps the
   harness from touching a real database. The Mongo import is therefore deferred into
   `flush()`, which the benchmark never calls. Same rule `domain_utils` follows.

2. **Recording must never be able to break a scrape.** Every public function here
   swallows its own errors. Instrumentation that can raise is worse than no
   instrumentation — it converts a metrics bug into a lost scrape.

Counters live in memory and are flushed to the `source_metrics` collection explicitly,
so a benchmark or a unit test can read `snapshot()` without any database at all.
"""

import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime

_LOCK = threading.Lock()
_COUNTERS = defaultdict(Counter)          # source_id -> Counter
_LATENCIES = defaultdict(lambda: deque(maxlen=500))
_FAILURES = deque(maxlen=500)             # recent failures, newest last

COLLECTION = 'source_metrics'

# Every source the enricher can dispatch. Listed explicitly so `/api/source-health`
# can render a source that has NEVER been attempted — a source missing from the table
# reads as "fine", which is the ambiguity this whole module exists to remove.
KNOWN_SOURCES = (
    'site', 'photon', 'jd', 'im', 'sulekha', 'tradeindia', 'zauba', 'serp',
    'page', 'hrdb', 'hrdb_write',
)

# Counter keys, named once so the endpoint and the writers cannot drift apart.
ATTEMPTS = 'attempts'
SUCCESSES = 'successes'
CONTACTS = 'contacts_found'
TIMEOUTS = 'timeouts'
FAILURES = 'failures'
HTTP_PREFIX = 'http_'


def _bump(source_id, key, n=1):
    if not source_id:
        return
    try:
        with _LOCK:
            _COUNTERS[source_id][key] += n
    except Exception:
        pass


def record_attempt(source_id):
    _bump(source_id, ATTEMPTS)


def record_success(source_id, contacts_found=0):
    _bump(source_id, SUCCESSES)
    if contacts_found:
        _bump(source_id, CONTACTS, int(contacts_found))


def record_http(source_id, status):
    """Record an HTTP status. 403/429 are the ones that mean 'blocked', not 'empty'."""
    try:
        _bump(source_id, f'{HTTP_PREFIX}{int(status)}')
    except Exception:
        pass


def record_latency(source_id, ms):
    if not source_id:
        return
    try:
        with _LOCK:
            _LATENCIES[source_id].append(float(ms))
    except Exception:
        pass


def record_failure(source_id, exc, url=None, status=None):
    """The replacement for `except Exception: pass`.

    Records the exception CLASS (not just a message), the URL, and the HTTP status
    when one is available — the three things needed to tell blocking apart from
    breakage. A timeout is counted separately because it is the failure mode most
    often mistaken for an empty result.
    """
    try:
        name = type(exc).__name__ if isinstance(exc, BaseException) else str(exc)
        is_timeout = 'timeout' in name.lower() or 'timedout' in name.lower()
        _bump(source_id, TIMEOUTS if is_timeout else FAILURES)
        if status is not None:
            record_http(source_id, status)
        with _LOCK:
            _FAILURES.append({
                'source_id': source_id or 'unknown',
                'error': name,
                'message': str(exc)[:300],
                'url': (url or '')[:400],
                'status': status,
                'at': datetime.now().isoformat(),
            })
    except Exception:
        pass


class track:
    """Context manager: one source attempt, timed.

        with source_metrics.track('jd') as t:
            data = _scrape_justdial(...)
            t.found(len(data['phones']))

    Records the attempt on entry, latency on exit, and routes any exception to
    `record_failure` — then RE-RAISES, because swallowing is the bug being fixed.
    """

    __slots__ = ('source_id', 'url', '_t0', '_found')

    def __init__(self, source_id, url=None):
        self.source_id = source_id
        self.url = url
        self._t0 = None
        self._found = 0

    def found(self, n):
        self._found = int(n or 0)

    def __enter__(self):
        self._t0 = time.time()
        record_attempt(self.source_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            record_latency(self.source_id, (time.time() - self._t0) * 1000.0)
            if exc is None:
                record_success(self.source_id, self._found)
            else:
                record_failure(self.source_id, exc, url=self.url)
        except Exception:
            pass
        return False          # never suppress


def _p50(values):
    v = sorted(values)
    return round(v[len(v) // 2], 1) if v else None


def snapshot():
    """Current counters as a plain dict. No database involved."""
    with _LOCK:
        out = {}
        for sid, c in _COUNTERS.items():
            row = dict(c)
            row['p50_latency_ms'] = _p50(_LATENCIES.get(sid) or [])
            att = row.get(ATTEMPTS, 0)
            row['success_rate'] = round(row.get(SUCCESSES, 0) / att, 3) if att else None
            row['non_200_rate'] = round(
                sum(v for k, v in row.items()
                    if k.startswith(HTTP_PREFIX) and not k[len(HTTP_PREFIX):].startswith('2'))
                / att, 3) if att else None
            out[sid] = row
        return out


def recent_failures(limit=50):
    with _LOCK:
        return list(_FAILURES)[-limit:]


def reset():
    with _LOCK:
        _COUNTERS.clear()
        _LATENCIES.clear()
        _FAILURES.clear()


def flush(db=None):
    """Persist counters into the `source_metrics` collection (cumulative via $inc).

    Imports `database` LAZILY — see the module docstring. Returns the number of
    source rows written, or 0 if persistence was unavailable.
    """
    snap = snapshot()
    if not snap:
        return 0
    try:
        if db is None:
            import database as _D           # deferred on purpose
            db = _D._get_write_db()
        now = datetime.now().isoformat()
        for sid, row in snap.items():
            inc = {k: v for k, v in row.items()
                   if isinstance(v, int) and not k.endswith('_rate')}
            db[COLLECTION].update_one(
                {'_id': sid},
                {'$inc': inc,
                 '$set': {'p50_latency_ms': row.get('p50_latency_ms'),
                          'updated_at': now},
                 '$setOnInsert': {'created_at': now}},
                upsert=True)
        fails = recent_failures(100)
        if fails:
            db[COLLECTION + '_failures'].insert_many(fails)
        reset()
        return len(snap)
    except Exception as e:
        print(f'[source_metrics] flush skipped: {type(e).__name__}: {e}')
        return 0
