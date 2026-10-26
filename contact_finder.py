"""
contact_finder.py — Smart contact extraction for PlacementRadar
================================================================
Libraries integrated:
  • phonenumbers (Google's libphonenumber) — accurate Indian +91 number parsing,
    handles every format: +91, 0, spaced, dashed, WhatsApp URLs, tel: links
  • email-validator — RFC-compliant email format checking (no DNS, instant)

Strategy (priority order for PHONES):
  1. Job description full text — via PhoneNumberMatcher (libphonenumber)
  2. Fetch full job page if description was short
  3. Parallel per-job web fetch:
     a. Company official website  → tel: links, JSON-LD, CSS selectors
     b. JustDial                  → data-phone attributes (always runs)
     c. IndiaMART                 → tel: / data attributes
     d. Zauba Corp                → registered company page
     e. Serper API                → Google Knowledge Graph + Maps snippet

Strategy (priority order for EMAILS):
  1. Job description text
  2. Company website career/contact pages (mailto: links first)
  3. HR Database (CSV) — FALLBACK only, ranked last

Speed: 4 jobs enriched in parallel; within each job all 5 sources fetch
       in parallel. Timeouts are 5-6 s per request.
"""

import requests, re, json as _json
import concurrent.futures, threading
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote_plus

# Instrumentation (brief §G). Neither module imports `database`, so importing them
# here keeps tools/bench.py's isolation assertion intact.
import source_metrics as _SM
import net_throttle as _NT
# Domain resolution with the corroboration gate. Pure module, no DB import.
import domain_utils as _DU

# ── phonenumbers (Google libphonenumber) ──────────────────────────────────────
import phonenumbers
from phonenumbers import PhoneNumberMatcher, PhoneNumberFormat, NumberParseException

# ── email-validator (RFC-compliant, no DNS, instant) ─────────────────────────
try:
    from email_validator import validate_email as _ev, EmailNotValidError as _EVErr
    _EV_OK = True
except ImportError:
    _EV_OK = False

from hr_database_lookup import lookup as db_lookup, add_entry as db_add, search as db_search

# ── Regex (emails only — phones now via libphonenumber) ───────────────────────
EMAIL_RE  = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b')
MAILTO_RE = re.compile(r'mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7})', re.I)

# WhatsApp URL pattern — libphonenumber won't find numbers inside URL paths
_WA_RE = re.compile(r'wa\.me/(?:91)?([6-9]\d{9})', re.I)

# ── Junk filters ──────────────────────────────────────────────────────────────
JUNK_DOMAINS = {
    'example.com','test.com','domain.com','sample.com','email.com','sentry.io',
    'yourcompany.com','company.com','mailinator.com','github.com','w3.org',
    'schemas.microsoft.com','openxmlformats.org','googleapis.com','cloudflare.com',
    'facebook.com','twitter.com','youtube.com','instagram.com','google.com',
    'amazon.com','tempmail.com',
    # Job boards — never HR contact emails
    'internshala.com','naukri.com','shine.com','foundit.in','apna.co',
    'linkedin.com','indeed.com','glassdoor.com','ziprecruiter.com',
    'monster.com','timesjobs.com','freshersworld.com','instahyre.com',
    'wellfound.com','hirist.tech','iimjobs.com','cutshort.io',
}
JUNK_PREFIXES_EXACT = {
    'noreply','no-reply','donotreply','mailer','postmaster','bounce',
    'notifications','alert','abuse','unsubscribe','feedback',
    'privacy','legal','press','support','admin',
    'complaints','grievance','report','safety','trust','moderation',
    'billing','payment','invoice','accounts','finance',
    'marketing','partnership','advertise','sponsor',
    'media','pr','news','events',
}
JUNK_PREFIXES_START = {
    'noreply','no-reply','donotreply','mailer','notifications','bounce',
    'complaints','grievance','report-',
}
GENERIC_PREFIXES = {'careers','career','jobs','job','recruitment','hiring','hr','apply'}

PHONE_BLACKLIST = {
    '8559284470','8448348052','1800102638','9212122423','9717270303',
}

JOB_BOARD_DOMAINS = {
    'internshala.com','naukri.com','shine.com','foundit.in','apna.co',
    'linkedin.com','indeed.com','glassdoor.com','ziprecruiter.com',
    'monster.com','timesjobs.com','freshersworld.com','instahyre.com','wellfound.com',
}

HR_KEYWORDS = ['hr','recruit','talent','career','hiring','placement','people','staffing','hrd','campus']

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

PHONE_PATHS = [
    '/contact', '/contact-us', '/contactus', '/reach-us', '/get-in-touch',
    '/about-us', '/about', '/support', '/help', '/office',
]
EMAIL_PATHS = [
    '/careers', '/jobs', '/career', '/work-with-us', '/join-us',
    '/join-our-team', '/about/careers', '/company/careers',
    '/hiring', '/openings', '/vacancies', '/opportunities', '/hr',
]
PHONE_SELECTORS = [
    'a[href^="tel:"]',
    '[itemprop="telephone"]', '[itemprop="phone"]',
    'address',
    '[class*="phone"]', '[class*="Phone"]',
    '[class*="contact"]', '[class*="Contact"]',
    '[class*="mobile"]', '[class*="Mobile"]',
    '[class*="call"]',
    '[id*="phone"]', '[id*="contact"]', '[id*="mobile"]',
    'footer',
]
JUSTDIAL_CITIES = [
    'mumbai','delhi','bangalore','hyderabad','pune','chennai',
    'kolkata','ahmedabad','surat','jaipur','india',
]
ZAUBA_KEYWORDS = ['pvt','ltd','private','limited','technologies','tech',
                  'corp','enterprise','associates','consulting','group',
                  'services','solutions','india','international']

# Cache sentinels
_MISS = object()
_SKIP = object()


# ═════════════════════════════════════════════════════════════════════════════
# PHONE HELPERS — powered by Google libphonenumber
# ═════════════════════════════════════════════════════════════════════════════
def _extract_phones_from_text(text):
    """
    Find all valid Indian mobile numbers in free text.
    Uses libphonenumber's PhoneNumberMatcher (handles every format).
    Supplements with WhatsApp URL pattern (wa.me/ paths bypass the matcher).
    Returns list of 10-digit strings.
    """
    if not text:
        return []
    phones = []

    # Primary: libphonenumber PhoneNumberMatcher
    try:
        for match in PhoneNumberMatcher(text, "IN"):
            num = match.number
            if not phonenumbers.is_valid_number(num):
                continue
            if num.country_code != 91:
                continue
            e164   = phonenumbers.format_number(num, PhoneNumberFormat.E164)
            digits = e164[3:]                      # strip "+91"
            if digits in phones:
                continue
            if _valid_india_mobile(digits):
                phones.append(digits)
            elif _valid_india_landline(num, digits, match.raw_string):
                phones.append(digits)
    except Exception as _e:
        _SM.record_failure('extract_phone', _e)

    # Supplement: WhatsApp URLs (libphonenumber won't find /91XXXXXXXXXX in a URL)
    for m in _WA_RE.findall(text):
        d = re.sub(r'\D', '', m)
        if _valid_india_mobile(d) and d not in phones:
            phones.append(d)

    return phones


def _valid_india_landline(num, digits, raw):
    """An Indian FIXED-LINE number, written the way people write phone numbers.

    THE BUG THIS FIXES. Phone extraction accepted MOBILES ONLY (`digits[0] in
    '6789'`). Company switchboards are landlines, and Mumbai's all start 022 — so
    "+91 22 6628 9600" (Bain) and "022-25336112" were discarded from every contact
    page the crawler ever read. Given "Call +91 22 6628 9600 or 022-25336112. Mobile
    98765 43210", the old code kept only the mobile.

    Landlines need one guard mobiles don't: a bare 10-digit run such as an order ID
    "2025001234" is technically a valid Pune number (area code 20). So a landline is
    accepted only when the source text is FORMATTED like a phone — a +91 or 0 prefix,
    or a space, hyphen, dot or bracket separator. Toll-free, premium-rate and shared-
    cost numbers are excluded: those are customer-care lines, not company offices.
    """
    if not digits or len(digits) != 10 or digits in PHONE_BLACKLIST:
        return False
    if re.search(r'(\d)\1{6,}', digits):
        return False
    if phonenumbers.number_type(num) not in (phonenumbers.PhoneNumberType.FIXED_LINE,
                                             phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE):
        return False
    r = (raw or '').strip()
    return r.startswith(('+', '0')) or bool(re.search(r'[\s\-.()]', r))


def _is_india_fixed_line(digits):
    """10 national digits that libphonenumber recognises as an Indian fixed line."""
    if not digits or len(digits) != 10 or digits in PHONE_BLACKLIST:
        return False
    if re.search(r'(\d)\1{6,}', digits):
        return False
    try:
        num = phonenumbers.parse('+91' + digits, None)
    except NumberParseException:
        return False
    return (phonenumbers.is_valid_number(num) and
            phonenumbers.number_type(num) in (phonenumbers.PhoneNumberType.FIXED_LINE,
                                              phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE))


def _valid_india_mobile(digits):
    """Quick 10-digit Indian mobile validation (post-libphonenumber parse)."""
    if not digits or len(digits) != 10:      return False
    if digits[0] not in '6789':              return False
    if digits in PHONE_BLACKLIST:            return False
    if digits == digits[0] * 10:            return False
    if re.search(r'(\d)\1{7,}', digits):    return False
    return True


def _parse_phone_raw(raw):
    """
    Parse a raw phone string (from tel: href or data-phone) using libphonenumber.
    Returns 10-digit Indian mobile string or '' on failure.
    """
    raw = raw.strip()
    if not raw:
        return ''
    try:
        num = phonenumbers.parse(raw, "IN")
        if not phonenumbers.is_valid_number(num):
            return ''
        if num.country_code != 91:
            return ''
        e164 = phonenumbers.format_number(num, PhoneNumberFormat.E164)
        digits = e164[3:]
        # tel: links and structured-data `telephone` fields are explicitly phones,
        # and they are where companies publish their office LANDLINE. This returned
        # mobiles only, so `tel:+912266289600` was discarded.
        if _valid_india_mobile(digits) or _valid_india_landline(num, digits, raw):
            return digits
        return ''
    except NumberParseException:
        pass
    # Fallback: strip to digits
    d = re.sub(r'\D', '', raw)
    if len(d) == 12 and d.startswith('91'): d = d[2:]
    if len(d) == 11 and d.startswith('0'):  d = d[1:]
    return d if _valid_india_mobile(d) else ''


def _format_phone(digits):
    """Format 10-digit Indian mobile as '+91 XXXXX XXXXX' using libphonenumber."""
    try:
        num = phonenumbers.parse(f"+91{digits}", None)
        return phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL)
    except Exception:
        return f"+91 {digits[:5]} {digits[5:]}"


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENRICHER — parallel jobs + parallel sources per job
# ═════════════════════════════════════════════════════════════════════════════
def enrich_with_contacts(jobs, settings=None, progress_callback=None):
    settings   = settings or {}
    total      = len(jobs)
    cache      = {}
    cache_lock = threading.Lock()
    completed  = [0]
    comp_lock  = threading.Lock()
    serper_key = settings.get('serper_api_key', '')

    def _process(job):
        _enrich_single_job(job, cache, cache_lock, serper_key)
        with comp_lock:
            completed[0] += 1
            c = completed[0]
        if progress_callback and total > 0:
            progress_callback(
                int(c / total * 100),
                f'Finding contacts: {c}/{total} — {job.get("company","")[:30]}'
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_process, jobs))

    return jobs


def _enrich_single_job(job, cache, cache_lock, serper_key=''):
    """Enrich one job. Thread-safe via cache_lock."""
    try:
        company     = (job.get('company') or '').strip()
        company_url = (job.get('company_url') or '').strip()
        desc        = (job.get('description') or '')
        job_url     = (job.get('job_url') or '').strip()
        location    = (job.get('location') or 'India').strip()

        emails_web = []
        emails_db  = []
        phones     = []
        sources    = {}
        phone_sources = {}

        _listed_domain = _get_domain(company_url)
        _confirmed     = '' if _listed_domain else _resolve_domain(company)
        company_domain = _listed_domain or _confirmed

        # ── STEP 1: Extract from job description ──────────────────────────
        desc_emails, desc_phones = _extract_from_desc(desc)
        for e in desc_emails:
            if e not in emails_web: emails_web.append(e)
            sources.setdefault(e, '📄 Job Description')
        for p in desc_phones:
            if p not in phones: phones.append(p)

        # ── STEP 2: Fetch full job page if description is short ───────────
        if len(desc) < 800 and job_url and _is_job_site(job_url):
            ck = f'page:{job_url}'
            with cache_lock:
                page_data = cache.get(ck)
            if page_data is None:
                page_data = _fetch_job_page(job_url)
                with cache_lock:
                    cache[ck] = page_data
            for e in page_data['emails']:
                if e not in emails_web: emails_web.append(e)
                sources.setdefault(e, '📄 Job Page')
            for p in page_data['phones']:
                if p not in phones: phones.append(p)

        # ── STEP 3+: Parallel web fetches ────────────────────────────────
        # A domain that passed the corroboration gate is at least as trustworthy
        # as a job board's company_url, so it gets STRICT own-domain filtering.
        # Previously a guessed domain ran non-strict — accepting any email on
        # the page — which on a wrong guess is an attribution leak.
        has_real_url = bool(company_url) or bool(_confirmed)
        base_url     = company_url or (f'https://{company_domain}' if company_domain else None)
        needs_zauba  = bool(company and any(kw in company.lower() for kw in ZAUBA_KEYWORDS))

        site_ck  = f'site:{company_domain}' if (company_domain and company_domain not in JOB_BOARD_DOMAINS) else None
        zauba_ck = f'zauba:{company.lower()}' if company else None
        jd_ck    = f'jd:{company.lower()}' if company else None

        with cache_lock:
            site_cached  = cache.get(site_ck,  _MISS) if site_ck  else _SKIP
            zauba_cached = cache.get(zauba_ck, _MISS) if zauba_ck else _SKIP
            jd_cached    = cache.get(jd_ck,    _MISS) if jd_ck    else _SKIP

        _cached_lookup = {'site': site_cached, 'zauba': zauba_cached, 'jd': jd_cached}

        # Submit all sources in parallel without using `with` — so that slow
        # company-site scrapers (up to 22 sequential HTTP pages) don't block
        # the whole job when we already have results from faster sources.
        sub = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        fetch_futs = {}
        if site_cached is _MISS and base_url and site_ck:
            fetch_futs['site']  = sub.submit(_scrape_company_site, base_url, company_domain, has_real_url)
        if zauba_cached is _MISS and needs_zauba:
            fetch_futs['zauba'] = sub.submit(_scrape_zauba, company)
        if jd_cached is _MISS and company:
            fetch_futs['jd']    = sub.submit(_scrape_justdial, company, location)
        if company:
            fetch_futs['im']        = sub.submit(_scrape_indiamart, company)
            fetch_futs['sulekha']   = sub.submit(_scrape_sulekha, company, location)
            fetch_futs['tradeindia'] = sub.submit(_scrape_tradeindia, company)
        if base_url and site_ck:
            fetch_futs['photon'] = sub.submit(_photon_crawl, base_url, company_domain or '')
        if serper_key and company:
            fetch_futs['serp']  = sub.submit(_search_serper, company, location, serper_key)

        def _get(key, default):
            """Collect one source's result.

            THE SILENT FAILURE THIS FIXES (brief §G): this handler used to be
            `except Exception: return default`, which swallowed a source's exception
            AND its timeout and returned an empty result. A source that was fully
            blocked, fully timing out, or raising on every call looked exactly like
            one that found nothing — which is why five of Phase A's ten sources
            reported 0.0% with no way to tell blocking from absence.

            The empty default is still returned (a dead source must not break the
            scrape), but the reason is now recorded instead of discarded.
            """
            if key in fetch_futs:
                t = 15 if key == 'serp' else 20 if key == 'photon' else 12
                _SM.record_attempt(key)
                import time as _time
                _t0 = _time.time()
                try:
                    res = fetch_futs[key].result(timeout=t) or default
                except Exception as e:
                    _SM.record_failure(key, e)
                    return default
                _SM.record_latency(key, (_time.time() - _t0) * 1000.0)
                found = 0
                if isinstance(res, dict):
                    found = len(res.get('emails') or []) + len(res.get('phones') or [])
                _SM.record_success(key, found)
                return res
            v = _cached_lookup.get(key, _SKIP)
            if v not in (_MISS, _SKIP):
                _SM.record_attempt(key)
                _SM.record_success(key, 0)      # cache hit: no network, no yield claim
                return v
            return default

        site_data       = _get('site',       {'emails': [], 'phones': []})
        zauba_data      = _get('zauba',      {'emails': [], 'phones': []})
        jd_data         = _get('jd',         {'phones': []})
        im_data         = _get('im',         {'phones': []})
        sulekha_data    = _get('sulekha',    {'phones': []})
        tradeindia_data = _get('tradeindia', {'phones': []})
        photon_data     = _get('photon',     {'emails': [], 'phones': []})
        serp_data       = _get('serp',       {'phones': [], 'emails': []})

        # Release executor. `cancel_futures=True` drops work that has not STARTED;
        # threads already running still finish (Python cannot interrupt them), so this
        # does not block result collection. Without it, a queued source for an
        # abandoned job stayed pending and kept a worker slot — the pool filled with
        # work whose results nobody would ever read.
        sub.shutdown(wait=False, cancel_futures=True)

        # Write results back to cache
        with cache_lock:
            if site_ck  and 'site'  in fetch_futs: cache[site_ck]  = site_data
            if zauba_ck and 'zauba' in fetch_futs: cache[zauba_ck] = zauba_data
            if jd_ck    and 'jd'    in fetch_futs: cache[jd_ck]    = jd_data

        # Merge emails
        for e in (site_data.get('emails') or []):
            if e not in emails_web: emails_web.append(e)
            sources.setdefault(e, '💼 Company Website')
        for e in (zauba_data.get('emails') or []):
            if e not in emails_web: emails_web.append(e)
            sources.setdefault(e, '🏢 Zauba Corp')
        for e in (photon_data.get('emails') or []):
            if e not in emails_web: emails_web.append(e)
            sources.setdefault(e, '🕷️ Deep Crawl')
        for e in (serp_data.get('emails') or []):
            if e not in emails_web: emails_web.append(e)
            sources.setdefault(e, '🔍 Google Search')

        # Merge phones — directories first (highest confidence), then site, then deep crawl.
        # Source is recorded per number so the company store can score it (brief §4);
        # previously phones carried no attribution at all.
        for _src_id, src_phones in [('jd',         jd_data.get('phones') or []),
                                    ('im',         im_data.get('phones') or []),
                                    ('sulekha',    sulekha_data.get('phones') or []),
                                    ('tradeindia', tradeindia_data.get('phones') or []),
                                    ('serp',       serp_data.get('phones') or []),
                                    ('site',       site_data.get('phones') or []),
                                    ('photon',     photon_data.get('phones') or [])]:
            for p in src_phones:
                if p not in phones:
                    phones.append(p)
                    phone_sources.setdefault(p, _src_id)

        # ── STEP 6: HR Database fallback ──────────────────────────────────
        db_hits = db_lookup(company)
        for hit in db_hits:
            e   = hit['hr_email']
            pfx = e.split('@')[0].lower()
            if pfx in GENERIC_PREFIXES: continue
            if e not in emails_db: emails_db.append(e)
            sources.setdefault(e, '🗂️ HR Database')

        # Rank emails: website first, DB last
        all_emails = list(dict.fromkeys(emails_web + emails_db))
        clean      = _filter_emails(all_emails)
        hr_em      = [e for e in clean if _is_hr(e) and e in emails_web]
        other_web  = [e for e in clean if e not in hr_em and e in emails_web]
        db_em      = [e for e in clean if e in emails_db and e not in emails_web]
        ranked     = hr_em + other_web + db_em

        clean_phones = _filter_phones(phones)

        ranked = [e for e in ranked
                  if not any(jb in e.split('@')[-1] for jb in JOB_BOARD_DOMAINS)]

        # No [:4] / [:3] slicing. Phase A measured 44 contacts discovered vs ~31
        # surviving — the slice silently discarded ~30% of real yield. Everything
        # found is kept; ranking still puts the best first.
        job['emails']       = ', '.join(ranked)
        job['hr_contact']   = ranked[0] if ranked else ''
        job['phone']        = ', '.join(clean_phones)
        job['email_source'] = sources.get(ranked[0], '') if ranked else ''

        # Structured, per-source contacts for the company store. Attached in memory
        # only — contact_finder deliberately imports no database module, so the
        # benchmark harness can run the pipeline with zero DB connections.
        # save_jobs() consumes and strips this key.
        _EMOJI_TO_SOURCE = {
            '📄 Job Description': 'desc', '📄 Job Page': 'page',
            '💼 Company Website': 'site', '🏢 Zauba Corp': 'zauba',
            '🕷️ Deep Crawl': 'photon', '🔍 Google Search': 'serp',
            '🗂️ HR Database': 'hrdb',
        }
        discovered = []
        for e in ranked:
            discovered.append({
                'type': 'email',
                'value': e,
                'source': _EMOJI_TO_SOURCE.get(sources.get(e, ''), 'unknown'),
            })
        for p in clean_phones:
            digits = re.sub(r'\D', '', p)[-10:]
            discovered.append({
                'type': 'phone',
                'value': p,
                'source': phone_sources.get(digits, 'unknown'),
            })
        job['_discovered_contacts'] = discovered

        # Auto-save new contacts to HR database
        src = sources.get(ranked[0], '') if ranked else ''
        if company and 'HR Database' not in src:
            for _e in ranked[:2]:
                pfx = _e.split('@')[0].lower()
                if pfx in GENERIC_PREFIXES: continue
                if not db_search(_e):
                    db_add(company=company, hr_email=_e, hr_name='',
                           designation='HR',
                           city=location.split(',')[0].strip()[:30],
                           source=f'Auto:{src[:20]}')
    except Exception as _e:
        _SM.record_failure('hrdb_write', _e)


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — Extract from description text
# ═════════════════════════════════════════════════════════════════════════════
def _extract_from_desc(text):
    if not text: return [], []

    # Emails
    emails = []
    for e in MAILTO_RE.findall(text):
        if _is_valid_email(e) and e not in emails:
            emails.append(e)
    for e in EMAIL_RE.findall(text):
        if _is_valid_email(e) and e not in emails:
            emails.append(e)

    # Phones — via libphonenumber
    phones = _extract_phones_from_text(text)

    return emails, phones


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — Fetch full job page (for short descriptions)
# ═════════════════════════════════════════════════════════════════════════════
def _fetch_job_page(url):
    result = {'emails': [], 'phones': []}
    try:
        r = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True)
        if r.status_code != 200: return result
        soup = BeautifulSoup(r.text, 'lxml')
        for tag in soup(['script','style','nav','footer','header','meta','link']):
            tag.decompose()
        desc_div = (soup.select_one('div.show-more-less-html__markup') or
                    soup.select_one('div.description__text') or
                    soup.select_one('[class*="description"]') or
                    soup.select_one('article') or soup.body)
        text = desc_div.get_text(' ') if desc_div else soup.get_text(' ')
        em, ph = _extract_from_desc(text)
        result['emails'] = [
            e for e in em
            if _is_valid_email(e)
            and not any(jb in e.split('@')[-1] for jb in JOB_BOARD_DOMAINS)
        ]
        result['phones'] = ph
    except Exception: pass
    return result


def _is_job_site(url):
    try:
        host = urlparse(url).netloc.lower()
        return any(d in host for d in ['linkedin.com','naukri.com','internshala.com',
                                        'indeed.com','foundit.in','shine.com','apna.co'])
    except: return False


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — Scrape company's official website
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_company_site(base_url, expected_domain, strict_domain=True):
    result = {'emails': [], 'phones': []}
    tried_emails = set()

    def _keep_email(e):
        if not _is_valid_email(e): return False
        if strict_domain:
            return _matches_domain(e, expected_domain)
        dom = e.lower().split('@')[1] if '@' in e else ''
        return dom not in JOB_BOARD_DOMAINS and not any(
            dom.endswith('.' + jd) for jd in JOB_BOARD_DOMAINS)

    try:
        if not base_url.startswith('http'): base_url = 'https://' + base_url
        parsed = urlparse(base_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"

        # Phase A: Email scraping — limit to 5 paths max to stay within 12s timeout
        for path in ([''] + EMAIL_PATHS)[:5]:
            url = base + path if path else base
            try:
                r = requests.get(url, headers=HEADERS, timeout=5, allow_redirects=True)
                if r.status_code != 200: continue
                if any(jb in urlparse(r.url).netloc.lower() for jb in JOB_BOARD_DOMAINS):
                    continue
                soup = BeautifulSoup(r.text, 'lxml')
                for tag in soup(['script','style','nav']): tag.decompose()
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if href.startswith('mailto:'):
                        e = href.replace('mailto:','').split('?')[0].strip()
                        if _keep_email(e) and e not in tried_emails:
                            tried_emails.add(e)
                            result['emails'].append(e)
                text = soup.get_text(' ')
                em, _ = _extract_from_desc(text)
                for e in em:
                    if _keep_email(e) and e not in tried_emails:
                        tried_emails.add(e)
                        result['emails'].append(e)
                if result['emails']:
                    break
            except Exception:
                continue

        # Phase B: Phone scraping — limit to 4 paths to stay within 12s timeout
        for path in (PHONE_PATHS + [''])[:4]:
            if len(result['phones']) >= 2: break
            url = base + path if path else base
            try:
                r = requests.get(url, headers=HEADERS, timeout=5, allow_redirects=True)
                if r.status_code != 200: continue
                if any(jb in urlparse(r.url).netloc.lower() for jb in JOB_BOARD_DOMAINS):
                    continue
                soup = BeautifulSoup(r.text, 'lxml')
                for tag in soup(['script','style']): tag.decompose()
                phones = _extract_phones_from_page(soup)
                for p in phones:
                    if p not in result['phones']:
                        result['phones'].append(p)
                # Also grab mailto: on contact page
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if href.startswith('mailto:'):
                        e = href.replace('mailto:','').split('?')[0].strip()
                        if _is_valid_email(e) and _matches_domain(e, expected_domain):
                            if e not in tried_emails:
                                tried_emails.add(e)
                                result['emails'].append(e)
                if result['phones']: break
            except Exception as _e:
                _SM.record_failure('site', _e, url=locals().get('u'))
                continue
    except Exception as _e:
        _SM.record_failure('site', _e, url=base_url)
    return result


def _extract_phones_from_page(soup):
    """
    Multi-strategy phone extraction from a BeautifulSoup object.
    Uses libphonenumber for all parsing.
    """
    phones = []

    # 1. tel: href links — highest confidence
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('tel:'):
            d = _parse_phone_raw(href.replace('tel:', '').strip())
            if d and d not in phones:
                phones.append(d)

    # 2. JSON-LD schema.org telephone
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data  = _json.loads(script.string or '{}')
            items = data.get('@graph', [data]) if isinstance(data, dict) else []
            if isinstance(data, dict): items = data.get('@graph', [data])
            for item in items:
                for key in ('telephone', 'phone', 'faxNumber'):
                    val = item.get(key, '')
                    if val:
                        d = _parse_phone_raw(str(val))
                        if d and d not in phones:
                            phones.append(d)
        except Exception:
            pass

    # 3. CSS selectors (semantic phone elements)
    for selector in PHONE_SELECTORS:
        for el in soup.select(selector):
            text = el.get_text(' ')
            if not re.search(r'\d{7,}', text):
                continue
            for p in _extract_phones_from_text(text):
                if p not in phones:
                    phones.append(p)

    # 4. Full page text fallback
    if not phones:
        for p in _extract_phones_from_text(soup.get_text(' ')):
            if p not in phones:
                phones.append(p)

    return phones


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — Zauba Corp
# ═════════════════════════════════════════════════════════════════════════════
def _dir_get(source_id, url, **kw):
    """GET a shared directory site (Zauba, JustDial, IndiaMART, Sulekha, TradeIndia)
    through net_throttle: one request at a time per domain, a 1 s gap, a User-Agent that
    is stable per domain, and the HTTP status recorded in source health. Every company
    is looked up on the same few directories, 4 companies at a time, which is the
    pattern that gets an address blocked. One retry at most, so a source still fits its
    12 s collection budget in `_get`."""
    headers = {**kw.pop('headers', HEADERS), 'User-Agent': _NT.user_agent_for(url)}
    return _NT.polite_request(lambda u: requests.get(u, headers=headers, **kw),
                              url, source_id=source_id, max_retries=1)


def _scrape_zauba(company_name):
    result = {'emails': [], 'phones': []}
    try:
        clean = re.sub(
            r'\s+(pvt\.?|ltd\.?|limited|private|technologies|tech|solutions|services|india|group)\.?\s*$',
            '', company_name.strip(), flags=re.I).strip()
        r = _dir_get('zauba',
            f'https://www.zaubacorp.com/company-list/p-1/company/{quote_plus(clean)}',
            headers=HEADERS, timeout=6)
        soup = BeautifulSoup(r.text, 'lxml')
        link = soup.select_one('table a[href*="/company/"]')
        if not link: return result
        r2    = _dir_get('zauba', 'https://www.zaubacorp.com' + link['href'], headers=HEADERS, timeout=6)
        soup2 = BeautifulSoup(r2.text, 'lxml')
        text  = soup2.get_text(' ')
        em, ph = _extract_from_desc(text)
        result['emails'] = em
        result['phones'] = ph
    except Exception: pass
    return result


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5a — JustDial
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_justdial(company_name, location='india'):
    result = {'phones': []}
    try:
        clean = re.sub(
            r'\s+(pvt\.?|ltd\.?|limited|private|technologies|tech|solutions|services|india|group)\.?\s*$',
            '', company_name.strip(), flags=re.I).strip()
        city = 'india'
        for c in JUSTDIAL_CITIES:
            if c in location.lower():
                city = c
                break
        r = _dir_get('jd',
            f'https://www.justdial.com/{city}/{quote_plus(clean)}',
            headers={**HEADERS,
                     'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                     'Referer': 'https://www.justdial.com/'},
            timeout=6)
        if r.status_code != 200: return result
        soup = BeautifulSoup(r.text, 'lxml')

        for el in soup.select('[data-phone]'):
            d = _parse_phone_raw(el.get('data-phone', ''))
            if d and d not in result['phones']: result['phones'].append(d)

        for a in soup.select('a[href^="tel:"]'):
            d = _parse_phone_raw(a['href'].replace('tel:', ''))
            if d and d not in result['phones']: result['phones'].append(d)

        for span in soup.select('span[class*="contact"], span[class*="phone"], span[class*="mobilesv"]'):
            for p in _extract_phones_from_text(span.get_text()):
                if p not in result['phones']: result['phones'].append(p)

        if not result['phones']:
            text = soup.get_text(' ')
            name_idx = text.lower().find(clean.lower()[:15])
            if name_idx > -1:
                for p in _extract_phones_from_text(text[name_idx:name_idx + 300]):
                    result['phones'].append(p)

    except Exception: pass
    return result


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5b — IndiaMART
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_indiamart(company_name):
    result = {'phones': []}
    try:
        clean = re.sub(
            r'\s+(pvt\.?|ltd\.?|limited|private|technologies|tech|solutions|services|india|group)\.?\s*$',
            '', company_name.strip(), flags=re.I).strip()
        r = _dir_get('im',
            f'https://dir.indiamart.com/search.mp?ss={quote_plus(clean)}&cattype=cid',
            headers={**HEADERS, 'Referer': 'https://www.indiamart.com/'},
            timeout=6)
        if r.status_code != 200: return result
        soup = BeautifulSoup(r.text, 'lxml')

        for a in soup.select('a[href^="tel:"]'):
            d = _parse_phone_raw(a['href'].replace('tel:', ''))
            if d and d not in result['phones']: result['phones'].append(d)

        for el in soup.select('[data-phone],[data-mob]'):
            raw = el.get('data-phone') or el.get('data-mob') or ''
            d = _parse_phone_raw(raw)
            if d and d not in result['phones']: result['phones'].append(d)

        if not result['phones']:
            cards = soup.select('[class*="company-card"],[class*="compaydet"],[class*="listing"]')[:3]
            for card in cards:
                for p in _extract_phones_from_text(card.get_text(' ')):
                    if p not in result['phones']:
                        result['phones'].append(p)
    except Exception: pass
    return result


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5c — Serper API: Google Knowledge Graph + Maps snippet
#           Extracts BOTH phone numbers and emails from Google search results
# ═════════════════════════════════════════════════════════════════════════════
def _search_serper(company_name, location, serper_key):
    result = {'phones': [], 'emails': []}
    if not serper_key or not company_name: return result

    def _run_query(q):
        try:
            r = requests.post(
                'https://google.serper.dev/search',
                json={'q': q, 'num': 5, 'gl': 'in', 'hl': 'en'},
                headers={'X-API-KEY': serper_key, 'Content-Type': 'application/json'},
                timeout=6)
            if r.status_code != 200: return ''
            data = r.json()
            text = ''
            kg = data.get('knowledgeGraph') or {}
            text += ' ' + (kg.get('description') or '')
            for v in (kg.get('attributes') or {}).values():
                text += ' ' + str(v)
            ab = data.get('answerBox') or {}
            text += ' ' + (ab.get('answer') or '') + ' ' + (ab.get('snippet') or '')
            for item in (data.get('organic') or [])[:5]:
                text += ' ' + (item.get('snippet') or '')
            return text
        except Exception:
            return ''

    try:
        # Run both queries in parallel — each is 6s max, together still ≤6s
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_phone = pool.submit(_run_query,
                f'"{company_name}" contact phone number +91 {location}')
            f_email = pool.submit(_run_query,
                f'"{company_name}" HR email recruitment {location}')
            text_phone = f_phone.result(timeout=8) or ''
            text_email = f_email.result(timeout=8) or ''

        full_text = text_phone + ' ' + text_email
        result['phones'] = _extract_phones_from_text(full_text)
        for e in EMAIL_RE.findall(full_text):
            if _is_valid_email(e) and e not in result['emails']:
                result['emails'].append(e)

    except Exception: pass
    return result


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5d — Photon-style deep site crawl (inspired by s0md3v/Photon)
#           Follows discovered internal links to find tel: links on pages
#           that the fixed-path scraper never checks.
# ═════════════════════════════════════════════════════════════════════════════
def _photon_crawl(base_url, domain, max_pages=12):
    result = {'emails': [], 'phones': []}
    if not base_url: return result
    try:
        if not base_url.startswith('http'):
            base_url = 'https://' + base_url
        parsed      = urlparse(base_url)
        base_domain = parsed.netloc.lower()

        visited = set()
        # High-priority queue (contact/HR pages) + normal queue
        hi_queue, lo_queue = [], [base_url]

        _PRIORITY_KW = ('contact', 'about', 'team', 'hr', 'career', 'people',
                        'reach', 'office', 'location', 'support')

        while len(visited) < max_pages:
            queue = hi_queue or lo_queue
            if not queue: break
            url = (hi_queue if hi_queue else lo_queue).pop(0)
            if url in visited: continue
            visited.add(url)

            try:
                r = requests.get(url, headers=HEADERS, timeout=5, allow_redirects=True)
                if r.status_code != 200: continue
                if urlparse(r.url).netloc.lower() != base_domain: continue

                soup = BeautifulSoup(r.text, 'lxml')

                for p in _extract_phones_from_page(soup):
                    if p not in result['phones']:
                        result['phones'].append(p)

                text = soup.get_text(' ')
                em, _ = _extract_from_desc(text)
                for e in em:
                    if _is_valid_email(e) and (not domain or _matches_domain(e, domain)):
                        if e not in result['emails']:
                            result['emails'].append(e)

                if result['phones'] and len(result['phones']) >= 2:
                    break

                for a in soup.find_all('a', href=True):
                    href = a['href'].split('?')[0].split('#')[0]
                    if href.startswith('/'):
                        full = f"{parsed.scheme}://{base_domain}{href}"
                    elif href.startswith(('http://', 'https://')):
                        if urlparse(href).netloc.lower() != base_domain:
                            continue
                        full = href
                    else:
                        continue
                    if full in visited: continue
                    if any(k in href.lower() for k in _PRIORITY_KW):
                        hi_queue.append(full)
                    else:
                        lo_queue.append(full)

            except Exception as _e:
                _SM.record_failure('photon', _e)
                continue
    except Exception as _e:
        _SM.record_failure('photon', _e, url=base_url)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5e — Sulekha (Indian business directory, parallel to JustDial)
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_sulekha(company_name, location='india'):
    result = {'phones': []}
    try:
        clean = re.sub(
            r'\s+(pvt\.?|ltd\.?|limited|private|technologies|tech|solutions|services|india|group)\.?\s*$',
            '', company_name.strip(), flags=re.I).strip()
        city = location.split(',')[0].strip() or 'india'
        r = _dir_get('sulekha',
            f'https://www.sulekha.com/search-results?searchkey={quote_plus(clean)}'
            f'&cityName={quote_plus(city)}',
            headers={**HEADERS, 'Referer': 'https://www.sulekha.com/'},
            timeout=6)
        if r.status_code != 200: return result
        soup = BeautifulSoup(r.text, 'lxml')

        for a in soup.select('a[href^="tel:"]'):
            d = _parse_phone_raw(a['href'].replace('tel:', ''))
            if d and d not in result['phones']:
                result['phones'].append(d)

        for el in soup.select('[data-phone],[data-mobile],[data-contact]'):
            raw = el.get('data-phone') or el.get('data-mobile') or el.get('data-contact') or ''
            d = _parse_phone_raw(raw)
            if d and d not in result['phones']:
                result['phones'].append(d)

        if not result['phones']:
            # Find first listing card and extract phones from its text block
            card = soup.select_one('[class*="listing"],[class*="card"],[class*="result"]')
            if card:
                for p in _extract_phones_from_text(card.get_text(' ')):
                    if p not in result['phones']:
                        result['phones'].append(p)
    except Exception as _e:
        _SM.record_failure('sulekha', _e)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5f — TradeIndia (B2B directory — reliable for registered Indian companies)
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_tradeindia(company_name):
    result = {'phones': []}
    try:
        clean = re.sub(
            r'\s+(pvt\.?|ltd\.?|limited|private|technologies|tech|solutions|services|india|group)\.?\s*$',
            '', company_name.strip(), flags=re.I).strip()
        r = _dir_get('tradeindia',
            f'https://www.tradeindia.com/search/?search_text={quote_plus(clean)}&category=company',
            headers={**HEADERS, 'Referer': 'https://www.tradeindia.com/'},
            timeout=6)
        if r.status_code != 200: return result
        soup = BeautifulSoup(r.text, 'lxml')

        for a in soup.select('a[href^="tel:"]'):
            d = _parse_phone_raw(a['href'].replace('tel:', ''))
            if d and d not in result['phones']:
                result['phones'].append(d)

        for el in soup.select('[class*="phone"],[class*="contact"],[class*="mobile"],[data-phone]'):
            raw = el.get('data-phone', '')
            if raw:
                d = _parse_phone_raw(raw)
                if d and d not in result['phones']:
                    result['phones'].append(d)
            else:
                for p in _extract_phones_from_text(el.get_text(' ')):
                    if p not in result['phones']:
                        result['phones'].append(p)

        if not result['phones']:
            # Try company detail page if search returned a result
            link = soup.select_one('a[href*="/company/"],a[href*="/profile/"]')
            if link and link.get('href'):
                href = link['href']
                if not href.startswith('http'):
                    href = 'https://www.tradeindia.com' + href
                r2 = _dir_get('tradeindia', href, headers=HEADERS, timeout=5)
                if r2.status_code == 200:
                    soup2 = BeautifulSoup(r2.text, 'lxml')
                    for a in soup2.select('a[href^="tel:"]'):
                        d = _parse_phone_raw(a['href'].replace('tel:', ''))
                        if d and d not in result['phones']:
                            result['phones'].append(d)
                    if not result['phones']:
                        for p in _extract_phones_from_text(soup2.get_text(' ')):
                            if p not in result['phones']:
                                result['phones'].append(p)
    except Exception as _e:
        _SM.record_failure('tradeindia', _e)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATORS
# ═════════════════════════════════════════════════════════════════════════════
def _is_valid_phone(digits):
    """Kept for external callers — delegates to _valid_india_mobile."""
    return _valid_india_mobile(digits)


def _is_valid_email(email):
    if not email or len(email) > 80: return False
    el  = email.lower()
    pfx = el.split('@')[0]
    dom = el.split('@')[1] if '@' in el else ''

    # Fast junk checks (no network)
    if dom in JUNK_DOMAINS: return False
    if any(dom.endswith('.' + jd) for jd in JUNK_DOMAINS): return False
    if pfx in JUNK_PREFIXES_EXACT: return False
    if any(pfx.startswith(j) for j in JUNK_PREFIXES_START if len(j) > 4): return False
    if re.search(r'\.(png|jpg|gif|svg|css|js|woff|ico|xml|pdf)$', el): return False

    # email-validator for RFC-compliant format check (no DNS lookup)
    if _EV_OK:
        try:
            _ev(email, check_deliverability=False)
        except _EVErr:
            return False
    else:
        if not re.match(r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,7}$', el):
            return False

    return True


def _get_domain(url):
    if not url: return ''
    try:
        if not url.startswith('http'): url = 'https://' + url
        host = urlparse(url).netloc.lower()
        for p in ('www.','careers.','jobs.','hr.'): host = host.removeprefix(p)
        if host in JOB_BOARD_DOMAINS: return ''
        return host if '.' in host else ''
    except: return ''


_DOMAIN_CACHE = {}
_DOMAIN_LOCK  = threading.Lock()


def _resolve_domain(company):
    """The company's CONFIRMED domain, or '' — never an unverified guess.

    WHY THIS REPLACES _guess_domain(). Most Internshala jobs arrive with no
    company_url, so enrichment had to guess a website from the name, and the guess
    was wrong in two ways:

      * it stripped only ONE trailing suffix, so "InstaWeb Labs Private Limited"
        became `instaweblabsprivate.com` — a domain that does not exist;
      * it only ever tried `.com`, so `hansinfotech.in` was unreachable by design.

    Every guess then went straight to the website and deep-crawl scrapers. A live run
    measured 24 ConnectionErrors from the website scraper alone and 0 phones across
    8 companies.

    This uses the project's own resolver: iterative suffix stripping, .com/.in/.co.in
    candidates, parked-page screening, and the corroboration gate that requires the
    page to independently name the company. The same machinery confirmed jio.com,
    flipspaces.com and hansinfotech.in in testing. A company whose name is too
    generic to guess safely ("Genesis") is not guessed at all.

    Cached per process: repeated companies cost one resolution, not one per job.
    """
    key = _DU.company_key(company)
    if not key:
        return ''
    with _DOMAIN_LOCK:
        if key in _DOMAIN_CACHE:
            return _DOMAIN_CACHE[key]
    found, strategy = '', ''
    level, _ = _DU.guess_risk(company)
    _SM.record_attempt('resolve')
    # guess_risk() returns 'reject' / 'weak' / 'ok'. This line first tested for
    # 'skip' — a value it never returns — so generic names (Genesis -> genesis.com,
    # i.e. Genesis Motors) were guessed and crawled. Caught by a regression test.
    if level != 'reject':
        full = [(c, 'full') for c in _DU.candidate_domains(company)[:3]]
        short = [(c, 'short') for c in _short_name_candidates(company, key)]
        for cand, kind in full + short:
            found = _check_candidate(company, cand, kind)
            if found:
                strategy = kind
                break
        # Wikidata only after name-guessing fails — it costs two extra requests, and
        # it is what recovers large companies whose site is NOT their name
        # (Tata Consultancy Services -> tcs.com). Same verification gate applies.
        if not found:
            for cand in _wikidata_candidates(company):
                found = _check_candidate(company, cand, 'wikidata')
                if found:
                    strategy = 'wikidata'
                    break
    _SM.record_success('resolve', 1 if found else 0)
    with _DOMAIN_LOCK:
        _DOMAIN_CACHE[key] = found
        _DOMAIN_STRATEGY[key] = strategy
    return found


# Words that commonly open a company name AND are ordinary English. A short-name
# domain built from one of these lands on a stranger (bright.com, smart.com,
# prudent.com, creative.com — all measured). A BLOCKLIST WITH UNKNOWN COVERAGE —
# the Indian-presence check in _resolve_domain is the second, independent layer.
_COMMON_FIRST_WORDS = {
    'smart', 'bright', 'global', 'creative', 'prudent', 'prime', 'premier', 'golden',
    'silver', 'royal', 'united', 'digital', 'future', 'modern', 'classic', 'elite',
    'expert', 'master', 'perfect', 'quality', 'super', 'ultra', 'value', 'vision',
    'active', 'agile', 'brilliant', 'direct', 'dream', 'excel', 'fresh', 'grand',
    'great', 'happy', 'health', 'ideal', 'infinite', 'logic', 'magic', 'matrix',
    'mobile', 'nature', 'noble', 'optimal', 'orange', 'pioneer', 'planet', 'power',
    'precise', 'quest', 'reliable', 'secure', 'simple', 'single', 'sharp', 'smooth',
    'sonic', 'sound', 'spark', 'speed', 'sterling', 'strong', 'summit', 'target',
    'think', 'trust', 'unique', 'universal', 'urban', 'venture', 'victory', 'vivid',
    'world', 'impact', 'insight', 'bridge', 'square', 'circle', 'cross', 'frontier',
    'galaxy', 'horizon', 'innovative', 'intelligent', 'network', 'online', 'option',
    'paramount', 'pinnacle', 'platinum', 'popular', 'positive', 'precision', 'pristine',
    'progress', 'quantum', 'rainbow', 'resource', 'sapphire', 'signature', 'spectrum',
    'standard', 'supreme', 'synergy', 'triumph', 'velocity', 'vertex', 'western',
    'eastern', 'northern', 'southern', 'national', 'international', 'indian',
    'oriental', 'pacific', 'atlantic', 'alliance', 'partner', 'partners', 'service',
    'system', 'design', 'studio', 'stream', 'square', 'source', 'motion', 'lotus',
    'phoenix', 'falcon', 'eagle', 'tiger', 'lion', 'diamond', 'crystal', 'pearl',
}

_INDIA_RE = re.compile(
    r'\+91|\bindia\b|\b(mumbai|navi\s*mumbai|thane|pune|bengaluru|bangalore|delhi|'
    r'noida|gurgaon|gurugram|hyderabad|chennai|kolkata|ahmedabad|jaipur|kochi|'
    r'indore|chandigarh|nagpur|surat|coimbatore|vadodara)\b', re.I)

_DOMAIN_STRATEGY = {}


def _check_candidate(company, cand, kind):
    """Fetch one candidate domain and run every verification on it. '' if it fails."""
    try:
        r = requests.get(f'https://{cand}', headers=HEADERS, timeout=6,
                         allow_redirects=True)
    except Exception:
        # A speculative candidate that does not resolve is a NEGATIVE ANSWER, not a
        # source failure. Recording each one as a failure made the health dashboard
        # show 12 failures against 0 attempts.
        return ''
    if r.status_code != 200:
        return ''
    if _DU.is_parked_page(r.text, r.url)[0]:     # returns (bool, detail)
        return ''
    ok, _ev = _DU.confirms_company(company, r.text, r.url)
    if not ok:
        return ''
    # A short-name match needs a second, independent signal. Measured: on dictionary
    # words the gate alone confirmed prudent.com (a domain broker), bright.com (US
    # event rentals), smart.com (a car brand) and smart.in (a supermarket) — 4 of 5
    # wrong. Every job here comes off an Indian board, so an unrelated foreign
    # namesake is the common failure.
    if kind == 'short' and not _INDIA_RE.search(r.text[:400000]):
        return ''
    return _DU.clean_company_domain(r.url) or cand


# Wikidata's API policy requires a descriptive User-Agent. Without one, requests were
# refused mid-run — and a refusal counted as "no website" made a 25% hit rate look
# like 2%.
_WIKIDATA_API = 'https://www.wikidata.org/w/api.php'
_WIKIDATA_UA = {'User-Agent': 'PlacementRadar/1.0 (college placement outreach tool; '
                              'https://github.com/shubechavan/placementradar)'}
_ORG_DESC_RE = re.compile(
    r'compan|corporat|business|firm|enterprise|software|technolog|bank|consult|'
    r'service|manufactur|conglomerate|subsidiar|startup|organi[sz]ation|brand|retail|'
    r'telecom|insur|airline|group', re.I)


def _wikidata_candidates(company):
    """Official-website candidates from Wikidata (property P856). Free, no key.

    MEASURED against 40 companies whose domain was already confirmed: Wikidata knew a
    website for 10 (25% — essentially large companies only), and 4 of those 10 were
    wrong or a different entity. The worst: ExcelR -> excelra.com, a DIFFERENT company
    with a similar name. So two guards beyond the usual verification gate:

      * the entity must be DESCRIBED as an organisation, not a person or a film;
      * the entity's NAME must match the company — same company_key, or the same
        distinctive words. "Excelra" vs "ExcelR" fails that; "Tata Consultancy
        Services" vs "Tata Consultancy Services Ltd" passes.

    What it fixes: TCS -> tcs.com, Infosys, Capgemini, Jio — large companies whose
    website is NOT their name, which name-guessing can never find.
    """
    want_key = _DU.company_key(company)
    want_tok = set(_DU.distinctive_tokens(company))
    try:
        r = requests.get(_WIKIDATA_API, params={
            'action': 'wbsearchentities', 'search': company, 'language': 'en',
            'type': 'item', 'limit': 5, 'format': 'json'},
            headers=_WIKIDATA_UA, timeout=6)
        if r.status_code != 200 or 'json' not in (r.headers.get('content-type') or ''):
            _SM.record_http('wikidata', r.status_code)
            return []
        hits = r.json().get('search', [])
    except Exception as e:
        _SM.record_failure('wikidata', e)
        return []
    out = []
    for h in hits:
        if not _ORG_DESC_RE.search(h.get('description') or ''):
            continue
        names = {h.get('label') or '', (h.get('match') or {}).get('text') or ''}
        same = any(_DU.company_key(n) == want_key or
                   (want_tok and set(_DU.distinctive_tokens(n)) == want_tok)
                   for n in names if n)
        if not same:
            continue
        try:
            c = requests.get(_WIKIDATA_API, params={
                'action': 'wbgetclaims', 'entity': h['id'], 'property': 'P856',
                'format': 'json'}, headers=_WIKIDATA_UA, timeout=6).json()
        except Exception as e:
            _SM.record_failure('wikidata', e)
            continue
        for claim in c.get('claims', {}).get('P856', [])[:2]:
            dom = _DU.clean_company_domain(
                ((claim.get('mainsnak') or {}).get('datavalue') or {}).get('value') or '')
            if dom and dom not in out:
                out.append(dom)
        if out:
            break
    return out


def _short_name_candidates(company, key):
    """`fyntune.com` for "FynTune Solution Pvt Ltd" — the brand word alone.

    Candidates were built only from the FULL name slug (`fyntunesolution.com`,
    `anvenssaconsultancy.com`), but many real sites use just the brand word:
    fyntune.com and anvenssa.com both exist and both pass corroboration. Offered
    only for distinctive words of 6+ letters that are not common English, and tried
    AFTER the full-slug candidates, so a specific match always wins.
    """
    toks = _DU.distinctive_tokens(company)
    if not toks:
        return []
    t = toks[0].lower()
    if (len(t) < 6 or t in _COMMON_FIRST_WORDS or t in _DU.GENERIC_NAME_WORDS
            or t == key.replace('-', '')):
        return []
    return [f'{t}.com', f'{t}.in']


def _guess_domain(company):
    if not company: return ''
    name = re.sub(
        r'\s+(pvt\.?|ltd\.?|limited|inc\.?|llc|corp\.?|private|'
        r'technologies|tech|solutions|services|india|group|foundation)$',
        '', company.strip(), flags=re.I)
    slug = re.sub(r'[^a-z0-9]', '', name.lower())
    return f"{slug}.com" if slug else ''


def _matches_domain(email, domain):
    try:
        ed = email.lower().split('@')[1]
        d  = domain.lower()
        return ed == d or ed.endswith('.' + d)
    except: return False


def _is_hr(email):
    return any(k in email.lower().split('@')[0] for k in HR_KEYWORDS)


# Addresses that are NEVER a placement contact. Found in a live run: UniAcco's
# ethics@ (a whistleblower hotline) was ranked as an outreach address and auto-saved
# to the HR database labelled "HR". A placement email to an ethics or legal desk is
# worse than no email. sales@ / info@ / contact@ are deliberately NOT here — at a
# small firm they reach a person who forwards.
_NEVER_OUTREACH = {
    'ethics', 'legal', 'privacy', 'abuse', 'compliance', 'grievance', 'grievances',
    'investor', 'investors', 'ir', 'investorrelations', 'press', 'media', 'pr',
    'security', 'dpo', 'dataprotection', 'gdpr', 'webmaster', 'postmaster',
    'hostmaster', 'billing', 'accounts', 'accountspayable', 'procurement', 'vendor',
    'vendors', 'purchase', 'purchasing', 'whistleblower', 'unsubscribe', 'dmca',
    'copyright', 'fraud', 'phishing', 'cookies', 'nodal', 'nodalofficer',
}
_NO_REPLY = ('noreply', 'no-reply', 'no_reply', 'donotreply', 'do-not-reply')


def _never_outreach(email):
    local = (email or '').split('@')[0].lower()
    return local in _NEVER_OUTREACH or local.startswith(_NO_REPLY)


def _filter_emails(emails):
    seen, out = set(), []
    for e in emails:
        el = e.lower()
        if not _is_valid_email(e): continue
        if _never_outreach(e): continue
        if el not in seen:
            seen.add(el)
            out.append(e)
    return out


def _filter_phones(phones):
    """
    Deduplicate raw 10-digit strings and format with libphonenumber.
    Input: list of 10-digit strings (from _extract_phones_from_text).
    Output: list of '+91 XXXXX XXXXX' formatted strings.
    """
    seen, result = set(), []
    for p in phones:
        d = re.sub(r'[^\d]', '', str(p))
        if len(d) == 12 and d.startswith('91'): d = d[2:]
        if len(d) == 13 and d.startswith('091'): d = d[3:]
        if len(d) == 11 and d.startswith('0'): d = d[1:]
        # FINAL gate. It re-applied the mobile-only rule, so a landline accepted at
        # extraction would have been silently dropped here. Landlines reaching this
        # point already passed the formatting guard in _valid_india_landline.
        if not (_valid_india_mobile(d) or _is_india_fixed_line(d)): continue
        if d not in seen:
            seen.add(d)
            result.append(_format_phone(d))
    return result
