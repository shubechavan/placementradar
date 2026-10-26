"""
response_guard.py — "HTTP 200 does not mean the server answered YOUR question."

This is the THIRD instance of the same failure in this project, which is why the check
lives in one reusable place rather than in whichever scraper was fixed last:

  1. **JustDial** served a soft-block at 200 — 46 of 49 deep-path responses had a
     0-byte body, the rest were captcha challenges with an empty `<title>`.
  2. **The domain confirmation gate** (Phase D-new) — a domain that resolves and
     returns 200 is not thereby the company's domain; parked pages and brokers answer
     200 all day.
  3. **Internshala** — `/jobs/software-engineer-jobs` **redirects to `/jobs/`**, the
     generic 16,141-job listing, and returns it at 200. The scraper parsed it happily
     and returned Sales Associate and Business Development Manager for a search of
     "software engineer".

The shape is always identical: a 200, a parseable body, and content that has nothing
to do with what was asked. Status codes cannot catch it and neither can body-size
heuristics. Two checks can:

  * `redirected_to_generic()` — did the server quietly move us to a catch-all page?
  * `off_query()`             — does the content mention what we searched for?

Neither is scraper-specific. Contains no network, no database, no destructive call.
"""

import re

# Tokens too common to prove relevance. A title matching only these matched nothing.
_STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'for', 'in', 'at', 'to', 'with', 'on',
    'job', 'jobs', 'role', 'roles', 'work', 'vacancy', 'vacancies', 'opening',
    'openings', 'hiring', 'career', 'careers', 'india', 'remote', 'intern',
    'internship', 'internships', 'fresher', 'freshers', 'new', 'entry', 'level',
}
MIN_TOKEN_LEN = 3
DEFAULT_RELEVANCE = 0.20


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()


def query_tokens(search_term):
    """Content tokens from a search term. '' -> [] (no assertion possible)."""
    return [t for t in _norm(search_term).split()
            if len(t) >= MIN_TOKEN_LEN and t not in _STOPWORDS]


def _path(url):
    u = re.sub(r'^https?://', '', (url or '').strip().lower())
    u = u.split('?', 1)[0].split('#', 1)[0]
    i = u.find('/')
    return (u[i:] if i >= 0 else '/').rstrip('/') or '/'


def redirected_to_generic(requested_url, final_url, generic_paths=()):
    """True when a request was quietly moved to a catch-all listing page.

    Compares PATHS, so http/https and www differences do not register as redirects.
    A redirect that lands on a *more specific* path is fine; only landing on one of
    `generic_paths` (or on the bare root) counts as a miss.

    Returns (is_generic, detail) so the caller can log WHY it skipped the page.
    """
    req, fin = _path(requested_url), _path(final_url or requested_url)
    if req == fin:
        return False, ''
    generic = {_path(g) for g in generic_paths} | {'/'}
    if fin in generic:
        return True, f'redirected {req} -> {fin} (generic listing)'
    return False, f'redirected {req} -> {fin}'


def _stem(tok):
    """Crude prefix stem so morphology does not cause a false 'off-query'.

    Searching "engineering" returned ZERO results because no title contains that
    exact string — they say "Design Engineer", "Engineer - Mechanical". A literal
    substring test scored 0% relevance and the gate discarded a perfectly good page.
    Comparing on a prefix makes engineer/engineering/engineers one token.

    Only tokens of 5+ characters are truncated; shorter ones ('sap', 'qa', 'data')
    are already short enough that a prefix would over-match.
    """
    return tok[:5] if len(tok) >= 5 else tok


def relevance(titles, search_term):
    """Fraction of `titles` containing at least one content token of the search term.

    Returns (fraction, n_matched, n_total). With no usable tokens the fraction is
    1.0 — an assertion that cannot be made must not fail closed and silently discard
    a source's entire output.
    """
    toks = [_stem(t) for t in query_tokens(search_term)]
    items = [t for t in (titles or []) if (t or '').strip()]
    if not toks or not items:
        return 1.0, len(items), len(items)
    hits = sum(1 for t in items if any(tok in _norm(t) for tok in toks))
    return hits / len(items), hits, len(items)


def off_query(titles, search_term, threshold=DEFAULT_RELEVANCE):
    """True when results look unrelated to what was asked.

    The threshold is deliberately LOW (20%). Job boards legitimately mix adjacent
    roles into a result set, so this is not a precision filter — it is a tripwire for
    "we were served a different page entirely". Internshala's generic listing scores
    0.00 against "software engineer"; its real result page scores 1.00.

    Returns (is_off_query, detail).
    """
    frac, hits, total = relevance(titles, search_term)
    if total == 0:
        return False, 'no titles to judge'
    detail = f'relevance {frac:.0%} ({hits}/{total} titles match {query_tokens(search_term)})'
    return frac < threshold, detail
