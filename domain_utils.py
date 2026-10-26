"""
domain_utils.py — pure company-identity and domain helpers. NO database import.

Why this module is separate
---------------------------
`contact_finder.py` must never import `database.py`: tools/bench.py asserts that
`database` never enters sys.modules, and that assertion is what keeps the benchmark
able to run the whole enrichment pipeline with zero DB connections. Both modules
need this logic, so it lives here with no Mongo dependency.

The core rule encoded here
--------------------------
"Resolves and returns 2xx" does NOT mean "is this company's website".
genesis.com, apex.com and infinity.in all return 2xx and belong to strangers.
Once the domain becomes the document `_id`, an unconfirmed guess files another
company's contacts under this one — corruption by identity collision. So every
GUESSED domain must be corroborated (see confirms_company) and screened for
parking (see is_parked_page) before it can be used as an identity.
"""

import re
from urllib.parse import urlparse
1
try:
    from bs4 import BeautifulSoup
except ImportError:                      # parsing degrades, module still imports
    BeautifulSoup = None


# ═════════════════════════════════════════════════════════════════════════════
# Job boards — never a company's own site
# ═════════════════════════════════════════════════════════════════════════════
JOB_BOARD_HOSTS = {
    'linkedin.com', 'indeed.com', 'naukri.com', 'internshala.com', 'shine.com',
    'foundit.in', 'monsterindia.com', 'monster.com', 'apna.co', 'glassdoor.com',
    'glassdoor.co.in', 'ziprecruiter.com', 'timesjobs.com', 'freshersworld.com',
    'instahyre.com', 'wellfound.com', 'angel.co', 'hirist.tech', 'iimjobs.com',
    'cutshort.io', 'simplyhired.com', 'jooble.org', 'careerjet.co.in',
    'workindia.in', 'quikr.com', 'olx.in', 'upwork.com', 'freelancer.com',
    'indiamart.com', 'justdial.com', 'sulekha.com', 'tradeindia.com',
    'remotive.com', 'builtin.com', 'ycombinator.com', 'weworkremotely.com',
    'remoteok.com', 'himalayas.app', 'turing.com', 'toptal.com',
}

# Hosts that appear as outbound links everywhere and identify nobody.
NON_COMPANY_HOSTS = {
    'facebook.com', 'twitter.com', 'x.com', 'instagram.com', 'youtube.com',
    'google.com', 'gstatic.com', 'googleapis.com', 'gravatar.com', 'w3.org',
    'schema.org', 'cloudflare.com', 'cloudfront.net', 'amazonaws.com',
    'bit.ly', 'goo.gl', 'wa.me', 'whatsapp.com', 'telegram.me', 't.me',
    'apple.com', 'microsoft.com', 'adobe.com', 'jquery.com', 'bootstrapcdn.com',
    'medium.com', 'github.com', 'play.google.com', 'apps.apple.com',
    'wikipedia.org', 'crunchbase.com', 'glassdoor.co.in', 'ambitionbox.com',
}


def _host_of(url_or_host):
    raw = (url_or_host or '').strip()
    if not raw:
        return ''
    try:
        host = urlparse(raw if '://' in raw else 'https://' + raw).netloc.lower()
    except Exception:
        return ''
    host = host.split('@')[-1].split(':')[0].rstrip('.')
    return host[4:] if host.startswith('www.') else host


def _matches_any(host, hosts):
    h = (host or '').strip().lower().rstrip('.')
    if not h:
        return False
    if h.startswith('www.'):
        h = h[4:]
    return any(h == b or h.endswith('.' + b) for b in hosts)


def is_job_board_host(host):
    """True when `host` is a job board / listing site, INCLUDING subdomains.

    contact_finder._get_domain() compared the bare host for equality, so
    `in.linkedin.com` and `in.indeed.com` slipped through and were stored as
    company domains for 151 of 316 documents in the v1 store.
    """
    return _matches_any(host, JOB_BOARD_HOSTS)


def is_non_company_host(host):
    return _matches_any(host, NON_COMPANY_HOSTS)


def clean_company_domain(url_or_host):
    """Company domain from a URL, or '' if it isn't one (job boards rejected)."""
    host = _host_of(url_or_host)
    if not host or '.' not in host or is_job_board_host(host) or is_non_company_host(host):
        return ''
    return host


def is_junk_contact_domain(value):
    """Listing-site support addresses and template placeholders."""
    v = (value or '').strip().lower()
    if '@' not in v:
        return False
    local, _, dom = v.partition('@')
    if is_job_board_host(dom):
        return True
    return local in {'first.lastname', 'firstname.lastname', 'first.last',
                     'name.surname', 'yourname', 'email', 'someone'}


# ═════════════════════════════════════════════════════════════════════════════
# Company name normalization
# ═════════════════════════════════════════════════════════════════════════════
_BADGE_RE = re.compile(
    r'\s*(actively\s+hiring|be\s+an\s+early\s+applicant|early\s+applicant|'
    r'verified|promoted|featured|trending|hiring\s+challenge)\s*$', re.I)

_KEY_SUFFIX_RE = re.compile(
    r'[\s,\.]+(pvt|pvt\.|private|ltd|limited|llp|inc|corp|technologies|tech|'
    r'solutions|services|india|group)\.?\s*$', re.I)

# TLD tails, stripped ONLY when they follow a literal DOT in the original name
# and leave a stem of >=3 characters.
#
# Dot-only is deliberate. Slugifying first would turn both "amazon.com" and
# "safety-net" into "<word>-<tld>", making them indistinguishable — and stripping
# the second is a FALSE MERGE that corrupts identity, while a missed merge is
# merely recoverable. Measured against the corpus: 5 names have a dot before a
# TLD (Amazon.com, Digiflex.ai, Lemon.io, OffiNeeds.com, Palcode.ai), ZERO have a
# hyphen, and one has a space ("Expertia AI", where AI is part of the brand).
# Names like Cisco, Capco, FICO, Jio, Clario and LinkedIn must never be touched,
# which is why a bare trailing suffix is never stripped.
_TLD_DOT_TAIL_RE = re.compile(
    r'\.(com|co\.in|in|org|net|io|co|ai|dev)\s*$', re.I)
_MIN_TLD_STEM = 3

LEGAL_TOKENS = {
    'pvt', 'private', 'ltd', 'limited', 'llp', 'inc', 'corp', 'corporation',
    'technologies', 'technology', 'tech', 'solutions', 'solution', 'services',
    'service', 'india', 'indian', 'group', 'company', 'co', 'the', 'and',
    'global', 'international', 'systems', 'consulting', 'consultancy',
    'software', 'labs', 'studio', 'studios', 'digital', 'ventures', 'enterprises',
}

# Single-word names too generic to guess a domain for. genesis.com, apex.com and
# infinity.in all resolve and all belong to strangers — a guess here is worse
# than no domain at all, so we skip guessing and fall through to mining.
GENERIC_NAME_WORDS = {
    'genesis', 'apex', 'infinity', 'summit', 'horizon', 'nexus', 'vertex',
    'matrix', 'zenith', 'quantum', 'fusion', 'catalyst', 'spectrum', 'synergy',
    'velocity', 'momentum', 'pinnacle', 'paradigm', 'vision', 'insight',
    'element', 'origin', 'aspire', 'ascend', 'beacon', 'bridge', 'anchor',
    'compass', 'cornerstone', 'crest', 'eclipse', 'elevate', 'emerge', 'endeavor',
    'essence', 'evolve', 'forge', 'frontier', 'harmony', 'impact', 'ignite',
    'infinite', 'integra', 'legacy', 'liberty', 'lumina', 'meridian', 'mosaic',
    'novus', 'oasis', 'odyssey', 'optimum', 'orbit', 'phoenix', 'pioneer',
    'pivot', 'prime', 'prism', 'pulse', 'quest', 'radiant', 'reliance', 'sierra',
    'signal', 'spark', 'sphere', 'stellar', 'strive', 'thrive', 'titan',
    'trinity', 'unity', 'vantage', 'vertex', 'vista', 'vortex', 'zenit',
    'alpha', 'beta', 'delta', 'gamma', 'omega', 'sigma', 'nova', 'aura',
}

# Navigation furniture. These words appear in the header/footer of virtually every
# website, so finding one there corroborates nothing — it is the same circular
# failure as the quadric.in tautology, reached through a generic word instead of a
# self-referential one.
#
# This is not hypothetical. "NAVIS Human Resources" was confirmed against
# `10xgenomics.com` on the evidence
#     footer:token(resources) ~ 'Company About us Investors Careers Contact News …'
# and a staffing agency was given its client's domain as its identity. The failure
# mode is systematic for exactly the population this project cares about: agency
# recruiters mail from the client's domain, so `email_infer` points at the client
# and a nav word then "confirms" it.
#
# `solutions`/`services` are already in LEGAL_TOKENS; they are repeated here so the
# list reads as one complete idea rather than depending on the other set's contents.
SITE_FURNITURE_WORDS = {
    'resources', 'resource', 'solutions', 'services', 'products', 'product',
    'support', 'home', 'contact', 'about', 'careers', 'career', 'blog', 'news',
    'login', 'signin', 'signup', 'register', 'search', 'help', 'faq', 'privacy',
    'terms', 'sitemap', 'team', 'media', 'events', 'press', 'legal', 'overview',
}

MIN_GUESSABLE_LEN = 6


def strip_company_badges(name):
    s = (name or '').strip()
    prev = None
    while prev != s:
        prev = s
        s = _BADGE_RE.sub('', s).strip()
    return s


def company_key(name):
    """Stable normalized identity for a company. '' when unkeyable.

    FALLBACK identity only. A name cannot identify a company — `genesis` covers
    three different firms in this corpus. The real key is the domain.
    """
    s = strip_company_badges(name)
    if not s:
        return ''
    s = s.lower()
    s = re.sub(r'\(.*?\)', ' ', s)

    # Strip a dotted TLD tail BEFORE slugifying — once separators collapse to
    # hyphens, "amazon.com" and "safety-net" look identical and we could no
    # longer tell a TLD from a word. Requires a stem of >=3 chars.
    m = _TLD_DOT_TAIL_RE.search(s)
    if m and len(s[:m.start()].strip(' .-')) >= _MIN_TLD_STEM:
        s = s[:m.start()]

    prev = None
    while prev != s:
        prev = s
        s = _KEY_SUFFIX_RE.sub('', s)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def distinctive_tokens(company_name):
    """Tokens that actually identify the company (>=4 chars, not boilerplate).

    Excludes SITE_FURNITURE_WORDS as well as LEGAL_TOKENS: a token that appears in
    every website's navigation cannot distinguish one company's site from another's,
    so allowing it to corroborate a domain is a guaranteed false positive waiting for
    the right pair of names.

    The `or [key]` fallback still applies — a company genuinely named "Careers" keeps
    its key as a last-resort token rather than becoming unconfirmable.
    """
    key = company_key(company_name)
    toks = [t for t in key.split('-')
            if len(t) >= 4 and t not in LEGAL_TOKENS and t not in SITE_FURNITURE_WORDS]
    return toks or ([key] if len(key) >= 4 else [])


def guess_risk(company_name):
    """How far a guessed domain for this name may be trusted.

    Returns (level, reason) where level is:
      'reject' — never guess. A generic dictionary word: genesis.com belongs to
                 Genesis Motors, not to the Genesis in this corpus. Corroboration
                 cannot save these, because of course genesis.com's title says
                 "Genesis" — the check is circular for generic words.
      'weak'   — guess and corroborate, but the result is NOT trustworthy enough
                 to become a document `_id` without human review. Short single
                 tokens live here: measured against this corpus, ~12 of 14 were
                 correct (Apple, IBM, Jio, Citi, FICO, SLB, UST, WNS, Capco,
                 WebMD, Xylem, ORMAE) and ~2-3 were wrong (ROAST, Acies), so
                 auto-rejecting them all costs far more than it saves.
      'ok'     — normal confirmation rules apply.

    No automated test separates these reliably: "Apple" and "Genesis" are both
    English dictionary words of similar length, one correct and one wrong. Length,
    token count and dictionary membership all fail. Hence the human review queue.
    """
    key = company_key(company_name)
    if not key:
        return 'reject', 'no_key'
    if len(key.split('-')) == 1:
        if key in GENERIC_NAME_WORDS:
            return 'reject', 'generic_word'
        if len(key) < MIN_GUESSABLE_LEN:
            return 'weak', f'single_short_token({len(key)}<{MIN_GUESSABLE_LEN})'
    return 'ok', ''


def should_skip_guessing(company_name):
    """Back-compat wrapper: True only for hard rejects."""
    level, reason = guess_risk(company_name)
    return level == 'reject', reason


def candidate_domains(company_name, tlds=('com', 'in', 'co.in', 'org', 'net')):
    key = company_key(company_name)
    if not key:
        return []
    slug = key.replace('-', '')
    return [f'{slug}.{t}' for t in tlds]


# ═════════════════════════════════════════════════════════════════════════════
# Parked / for-sale screening  (runs BEFORE corroboration)
# ═════════════════════════════════════════════════════════════════════════════
PARKED_HOST_MARKERS = (
    'sedoparking', 'sedo.com', 'afternic', 'dan.com', 'bodis', 'parkingcrew',
    'hugedomains', 'undeveloped.com', 'domainmarket', 'buydomains',
    'parklogic', 'above.com', 'namedrive',
    # prudent.com titled itself "Prudent | NameBrokers" and passed every other
    # check — and the corroboration gate then CONFIRMED it as a real company.
    'namebrokers', 'squadhelp', 'brandbucket',
)
PARKED_TEXT_MARKERS = (
    'domain is for sale', 'this domain is for sale', 'buy this domain',
    'domain for sale', 'domain parking', 'parked domain', 'parked free',
    'may be for sale', 'inquire about this domain', 'checking your browser',
    'future home of something quite cool', 'default web page',
    'if you are the owner of this website', 'website coming soon',
    'under construction', 'account suspended', 'index of /',
    # Broker/reseller phrasings the first pass missed. Found in the review queue:
    # meril.com ("Strategic-Grade domain names for established brands"),
    # msi.co.in ("Buy msi.co.in - Premium Domain Name for Your Brand") and
    # lemon.co.in ("Domain welcome page for"). These matter because a broker page
    # usually ECHOES the domain name, so it can satisfy corroboration — Meril was
    # caught only by the short-name weak grading, not by this screen.
    'premium domain', 'domain name for your', 'domain names for',
    'strategic-grade domain', 'domain welcome page', 'brandable domain',
    'make an offer', 'domain broker', 'this domain is available',
    'the domain name', 'is for sale',
    'namebrokers', 'acquire this domain', 'get this domain', 'own this domain',
)
MIN_REAL_BODY_BYTES = 1024


def is_empty_shell(html):
    """(is_empty, reason) — a 2xx with (almost) no body.

    Deliberately SEPARATE from parking. A parked page means the domain belongs to
    someone else — a real rejection. An empty body means we cannot tell: amazon.com
    returns 0 bytes to this user-agent (a soft-block, same pattern as JustDial in
    Phase A) and is obviously not parked. Conflating the two understates coverage
    and would mark correct domains as wrong.
    """
    n = len((html or '').strip())
    if n < MIN_REAL_BODY_BYTES:
        return True, f'empty_body({n}B)'
    return False, ''


def is_parked_page(html, final_url=''):
    """(is_parked, reason) — positive evidence the domain is parked/for sale."""
    body = html or ''
    hay = (body[:200000] + ' ' + (final_url or '')).lower()
    for m in PARKED_HOST_MARKERS:
        # Whole-hostname match, not substring. A bare `m in hay` flagged any page
        # mentioning "jordan.com" as parked (it contains "dan.com") — and Indian
        # firm names ending in -dan (Chandan, Nandan, Vardan, Madan) made that a
        # real recall bug: a genuine company domain rejected as "for sale".
        if re.search(r'(?<![a-z0-9-])' + re.escape(m) + r'(?![a-z0-9-])', hay):
            return True, f'parking_host:{m}'
    for m in PARKED_TEXT_MARKERS:
        if m in hay:
            return True, f'parking_text:{m}'
    return False, ''


# ═════════════════════════════════════════════════════════════════════════════
# Confirmation gate
# ═════════════════════════════════════════════════════════════════════════════
def _norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


_HOSTNAME_RE = re.compile(r'^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$')


def is_tautological_signal(raw, domain):
    """True when a 'corroborating' signal is just the domain echoed back.

    `quadric.in` had <title>"quadric.in"</title>. That matches the company name
    only because the domain was built FROM the name — it corroborates nothing,
    the same circular failure as genesis.com's title saying "Genesis".

    Deliberately narrow: it fires only when the signal IS a bare hostname. A
    plain company name like "Alemeno" is a substring of "alemeno.com" but is
    genuine corroboration, so a naive containment test would reject it.
    """
    s = (raw or '').strip().lower()
    s = re.sub(r'^https?://', '', s).strip(' /')
    if s.startswith('www.'):
        s = s[4:]
    if not s or not _HOSTNAME_RE.match(s):
        return False
    d = _norm(domain)
    n = _norm(s)
    return bool(d) and (n == d or n in d or d in n)


def site_substance(html):
    """Positive evidence that a page is a real site, not a placeholder.

    The parking screen is a BLOCKLIST, and blocklists have unknown coverage — a
    domain-broker page slipped past it (meril.com) and was caught only by an
    unrelated short-name rule. A positive signal degrades safely: a real company
    site has navigation and many links; a parking page has almost none.

    MUST NOT be used to reject a page that already corroborated itself:
    neuralink.com renders client-side and has links=0, so a link-count gate would
    throw out a real site. JS-rendered SPAs are a large and growing class. Use
    this only to classify pages that FAILED corroboration — distinguishing "real
    site whose markup just doesn't name itself" (an Aurolab) from "placeholder".

    Returns (looks_real: bool, detail: str).
    """
    if BeautifulSoup is None or not html:
        return True, 'unparsed'
    try:
        soup = BeautifulSoup(html, 'lxml')
    except Exception:
        return True, 'unparsed'
    links = soup.find_all('a', href=True)
    internal = [a for a in links if not a['href'].lower().startswith(('http://', 'https://'))]
    navs = soup.find_all(['nav', 'header', 'footer'])
    forms = soup.find_all('form')
    detail = f'links={len(links)} internal={len(internal)} nav={len(navs)} forms={len(forms)}'
    # Placeholder shape: almost no links AND no navigation structure.
    if len(links) < 3 and not navs:
        return False, 'placeholder_shape:' + detail
    return True, detail


def _extract_signals(html):
    """(title, og_site_name, og_title, footer_text) — best effort."""
    title = og_site = og_title = footer = ''
    if BeautifulSoup is None or not html:
        m = re.search(r'<title[^>]*>(.*?)</title>', html or '', re.I | re.S)
        return (m.group(1) if m else ''), '', '', ''
    try:
        soup = BeautifulSoup(html, 'lxml')
        if soup.title and soup.title.string:
            title = soup.title.string
        for prop, var in (('og:site_name', 'og_site'), ('og:title', 'og_title')):
            tag = soup.find('meta', attrs={'property': prop}) or \
                  soup.find('meta', attrs={'name': prop})
            if tag and tag.get('content'):
                if var == 'og_site':
                    og_site = tag['content']
                else:
                    og_title = tag['content']
        parts = []
        for f in soup.find_all('footer'):
            parts.append(f.get_text(' ', strip=True))
        text = soup.get_text(' ', strip=True)
        for m in re.finditer(r'(?:©|&copy;|copyright)\s*[^|\n]{0,120}', text, re.I):
            parts.append(m.group(0))
        footer = ' '.join(parts)[:4000]
    except Exception:
        pass
    return title, og_site, og_title, footer


def confirms_company(company_name, html, final_url=''):
    """Does this page corroborate that it belongs to `company_name`?

    Returns (confirmed: bool, evidence: str). Required for any GUESSED domain
    before it may be used as a document identity.
    """
    title, og_site, og_title, footer = _extract_signals(html)
    key_norm = _norm(company_key(company_name))
    tokens = [_norm(t) for t in distinctive_tokens(company_name)]
    tokens = [t for t in tokens if len(t) >= 4]
    if not key_norm and not tokens:
        return False, 'no_distinctive_token'

    # Strip the literal domain out of every signal before matching. A signal that
    # only contains the company name BECAUSE it contains the domain proves
    # nothing — the domain was built from the name. quadric.in had
    # <title>"quadric.in"</title> AND footer "Copyright © 2026 quadric.in", so a
    # bare-hostname check alone was not enough; removing the domain string
    # generalises it. "Alemeno" (title) survives, because the title is the name,
    # not the hostname.
    host = _host_of(final_url)
    def _strip_domain(raw):
        s = raw or ''
        if host:
            for variant in (host, 'www.' + host, host.split('.')[0] + '.' + host.split('.', 1)[1]
                            if '.' in host else host):
                s = re.sub(re.escape(variant), ' ', s, flags=re.I)
        return s

    signals = [(label, _strip_domain(raw)) for label, raw in
               (('title', title), ('og:site_name', og_site),
                ('og:title', og_title), ('footer', footer))
               if raw and not is_tautological_signal(raw, host)]
    if not any(_norm(r) for _, r in signals):
        return False, 'tautological_signal_only'

    # Short names (IBM, UST, SLB, WNS, Jio) have no >=4-char token, so substring
    # matching can never confirm them — they were all being rejected as
    # "unconfirmed" purely because of their length. Match them on a WORD BOUNDARY
    # in the raw text instead: strict enough that a stray substring won't pass,
    # permissive enough that a real short brand can.
    if key_norm and len(key_norm) < 4:
        word_re = re.compile(r'(?<![a-z0-9])' + re.escape(key_norm) + r'(?![a-z0-9])', re.I)
        for label, raw in signals:
            if raw and word_re.search(re.sub(r'[^A-Za-z0-9]+', ' ', raw)):
                return True, f'{label}:word({key_norm})~{raw.strip()[:60]!r}'
        return False, 'no_corroboration_short_name'

    for label, raw in signals:
        hay = _norm(raw)
        if not hay:
            continue
        if key_norm and len(key_norm) >= 4 and key_norm in hay:
            return True, f'{label}~{(raw or "").strip()[:60]!r}'
        for t in tokens:
            if t in hay:
                return True, f'{label}:token({t})~{(raw or "").strip()[:60]!r}'
    return False, 'no_corroboration'
