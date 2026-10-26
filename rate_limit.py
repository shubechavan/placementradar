"""
rate_limit.py — sliding-window throttle for authentication endpoints (Phase H).

THE VULNERABILITY. `/api/auth/login` sits in `_OPEN_PATHS` with no throttle, and
`verify_password` is PBKDF2 — deliberately slow. That makes the endpoint two things at
once:

  * a **brute-force** target, because nothing limits guesses, and
  * a **denial-of-service** amplifier, because every unauthenticated request costs the
    server a full PBKDF2 round. The expensive hash that protects stored passwords is
    exactly what lets a stranger burn the CPU.

Both are closed by the same limit, so it has to count attempts BEFORE the hash runs.

DESIGN NOTES:

* **Per-IP and per-email, both enforced.** Per-IP alone lets a botnet spread guesses
  across addresses against one account; per-email alone lets one host walk a user list.
  An attempt must pass both.

* **Failures count; a success clears the counter.** Rate limiting successful logins
  would lock out a legitimate user who simply signs in from several devices.

* **In-memory, per-process.** Deliberate: a database-backed counter would add a Mongo
  round-trip to every unauthenticated request, which re-creates the DoS vector it is
  meant to close. LIMITATION, stated rather than hidden: under multiple gunicorn
  workers each process keeps its own window, so the effective limit is
  `LIMIT x workers`. For this deployment (a small committee tool, 1-2 workers) that is
  acceptable; a shared store would be needed before it is not.

* **Proxy-aware, but does not blindly trust `X-Forwarded-For`.** A spoofed header
  would otherwise defeat per-IP limiting entirely. The header is consulted only when
  `TRUST_PROXY_HOPS` is set, and then only at the hop our own proxy wrote.

Contains no database access and no destructive operation.
"""

import os
import threading
import time
from collections import defaultdict, deque

MAX_ATTEMPTS = int(os.environ.get('LOGIN_MAX_ATTEMPTS', '5'))
WINDOW_SECONDS = int(os.environ.get('LOGIN_WINDOW_SECONDS', str(15 * 60)))
TRUST_PROXY_HOPS = int(os.environ.get('TRUST_PROXY_HOPS', '0'))

_LOCK = threading.Lock()
_ATTEMPTS = defaultdict(deque)          # bucket key -> deque[timestamp]
_MAX_BUCKETS = 10000                    # bound memory against key-space flooding


def client_ip(request):
    """The caller's IP, trusting `X-Forwarded-For` only as far as configured.

    With `TRUST_PROXY_HOPS=1` (one reverse proxy, e.g. Render), the client address is
    the LAST entry in the header — the one our proxy appended. Entries to its left are
    attacker-supplied and must never be used.
    """
    if TRUST_PROXY_HOPS > 0:
        xff = (request.headers.get('X-Forwarded-For') or '').split(',')
        parts = [p.strip() for p in xff if p.strip()]
        if parts:
            idx = max(0, len(parts) - TRUST_PROXY_HOPS)
            return parts[idx]
    return request.remote_addr or 'unknown'


def _prune(dq, now):
    cutoff = now - WINDOW_SECONDS
    while dq and dq[0] < cutoff:
        dq.popleft()


def _sweep(now):
    """Drop empty buckets so a flood of distinct keys cannot grow the table forever."""
    if len(_ATTEMPTS) <= _MAX_BUCKETS:
        return
    for k in [k for k, dq in _ATTEMPTS.items() if not dq or dq[-1] < now - WINDOW_SECONDS]:
        _ATTEMPTS.pop(k, None)


def check(keys):
    """(allowed, retry_after_seconds). Read-only — records nothing.

    Call BEFORE the password hash so a blocked request costs no PBKDF2.
    """
    now = time.time()
    worst = 0
    with _LOCK:
        for k in keys:
            dq = _ATTEMPTS.get(k)
            if not dq:
                continue
            _prune(dq, now)
            if len(dq) >= MAX_ATTEMPTS:
                worst = max(worst, int(dq[0] + WINDOW_SECONDS - now) + 1)
    return (worst == 0), worst


def record_failure(keys):
    now = time.time()
    with _LOCK:
        for k in keys:
            dq = _ATTEMPTS[k]
            _prune(dq, now)
            dq.append(now)
        _sweep(now)


def clear(keys):
    """A successful login clears that identity's budget."""
    with _LOCK:
        for k in keys:
            _ATTEMPTS.pop(k, None)


def login_keys(request, email):
    """Both buckets an attempt is charged against."""
    return (f'ip:{client_ip(request)}',
            f'email:{(email or "").strip().lower()}')


def reset_all():
    """Test hook."""
    with _LOCK:
        _ATTEMPTS.clear()
