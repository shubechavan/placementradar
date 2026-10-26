"""
job_scraper.py
Scrapes jobs from:
  - LinkedIn, Indeed, Glassdoor, ZipRecruiter  → via JobSpy
  - Internshala                                → HTML scraper
  - Naukri                                     → JSON API + HTML fallback
  - Foundit                                    → JSON API + HTML fallback
  - Shine                                      → JSON-LD + HTML fallback
  - Apna                                       → JSON API + HTML fallback
"""

import requests, json, re, time
from bs4 import BeautifulSoup
from urllib.parse import quote

# Instrumentation (brief §G). Imports no database, so bench isolation holds.
import source_metrics as _SM
# 200-with-wrong-content guard (see response_guard.py). No database import.
import response_guard

JOBSPY_SITES = {'linkedin', 'indeed', 'glassdoor', 'zip_recruiter'}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-IN,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

JSON_HEADERS = {
    **HEADERS,
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json',
}

# ── Tech role filtering ───────────────────────────────────────────────────────
TECH_TITLE_KEYWORDS = {
    'engineer','developer','dev','programmer','coder','architect',
    'software','frontend','backend','fullstack','full stack','full-stack',
    'data','analyst','analytics','machine learning','ml ',' ml','ai ',
    'deep learning','nlp','computer vision','llm','data science',
    'data engineer','etl','bi developer','business intelligence',
    'devops','sre','site reliability','cloud','aws','gcp','azure',
    'kubernetes','docker','infrastructure','platform engineer',
    'system admin','sysadmin','network engineer','security engineer',
    'qa engineer','quality assurance','test engineer','automation engineer',
    'sdet','performance engineer',
    'product manager','product owner','scrum master','agile',
    'ui developer','ux engineer',
    'android','ios','flutter','react native','mobile developer',
    'embedded','firmware','hardware engineer','vlsi','fpga',
    'cybersecurity','security analyst','penetration','ethical hack',
    'it support','technical support','helpdesk','it admin',
    'python','java ','javascript','typescript','golang','rust',
    'node.js','react','angular','vue','django','spring','dotnet','.net',
    'blockchain','web3','salesforce','erp','sap','oracle developer',
    'database admin','dba','sql developer',
    'graduate engineer',
    # Design roles are tech roles here — a placement committee places them. These were
    # being DROPPED: "UX/UI Designer" matched neither 'ui developer' nor 'ux engineer'.
    # Bare 'ui'/'ux' are NOT listed: as substrings they match "building", "guide",
    # "luxury". Every entry below is long enough to be unambiguous.
    'designer','ux/ui','ui/ux','ux design','ui design','product design',
    'web design','interaction design','visual design','user experience',
    'user interface','figma',
    # REMOVED: 'intern', 'trainee', 'fresher'.
    # Those are SENIORITY words, not domain words, and they sat in a domain list — so
    # "Management Trainee - Marketing & Sales" was classified as a tech role while a
    # "UX/UI Designer" was discarded. Nothing is lost by removing them: a genuine
    # "Software Engineering Intern" still matches on 'software' and 'engineer'.
}

NON_TECH_TITLE_KEYWORDS = {
    'sales executive','sales manager','business development',
    'telecaller','telesales','bpo','voice process','non-voice',
    'accountant','accounts executive','finance executive','ca ','chartered',
    'hr executive','hr manager','recruiter','talent acquisition',
    'marketing executive','digital marketing','seo executive','content writer',
    'graphic designer','video editor',
    # Non-tech design disciplines. Listed explicitly because 'designer' is now a tech
    # keyword, and the deny-list is evaluated FIRST — so these still resolve to False.
    'fashion design','interior design','jewellery design','jewelry design',
    'textile design','set design',
    # NOT added: bare 'sales' / 'marketing'. Matching is substring-based, so 'sales'
    # would reject "Salesforce Developer" — and the deny-list wins. Once 'trainee' is
    # out of the tech list, "Management Trainee - Marketing & Sales" matches no tech
    # keyword at all and is dropped without needing a deny entry.
    'logistics','supply chain','warehouse','delivery',
    'teacher','faculty','professor','counsellor',
    'customer service','customer care','relationship manager',
    'field sales','field executive','area manager',
    'medical','nurse','pharmacist','doctor',
}

def _is_tech_role(title: str) -> bool:
    t = title.lower().strip()
    for kw in NON_TECH_TITLE_KEYWORDS:
        if kw in t:
            return False
    for kw in TECH_TITLE_KEYWORDS:
        if kw in t:
            return True
    return False


def _matches_terms(search_term: str, *fields) -> bool:
    """True when every word of the search appears somewhere in the fields (word order
    and phrasing free), for boards whose own search ignores the query."""
    text = ' '.join(f or '' for f in fields).lower()
    words = [w for w in re.findall(r'[a-z0-9+#.]+', (search_term or '').lower()) if len(w) > 1]
    return all(w in text for w in words)


def scrape_jobs(params, progress_callback=None):
    results     = []
    sites       = params.get('sites', ['linkedin', 'indeed'])
    search_term = params.get('search_term', 'software engineer')
    location    = params.get('location', 'India')
    results_wanted = int(params.get('results_wanted', 30))
    tech_only   = params.get('tech_only', False)

    # google_jobs PAUSED: its fallback path returns aggregator landing pages
    # ("29811 Data Analyst Jobs - Naukri") with no company/contact — pure noise.
    # The scrape_google_jobs() function is kept intact; it's just not run.
    # To re-enable, change this line back to: 'google_jobs' in sites
    GOOGLE_JOBS_PAUSED = True
    google_jobs_on   = ('google_jobs' in sites) and not GOOGLE_JOBS_PAUSED
    DEDICATED_SITES  = {'remotive','remoteok','weworkremotely','himalayas','wellfound','yc_startup','builtin','remoteco','toptal','contra','flexjobs','hn_hiring','turing','upwork'}
    jobspy_sites     = [s for s in sites if s in JOBSPY_SITES]
    dedicated_sites  = [s for s in sites if s in DEDICATED_SITES]
    indian_sites     = [s for s in sites if s not in JOBSPY_SITES
                        and s not in DEDICATED_SITES and s != 'google_jobs']
    custom_sites     = params.get('custom_sites', [])   # list of site config dicts
    # Serper key: from params (Settings page) OR env var
    serper_key = params.get('serper_api_key') or _SERPER_KEY

    def cb(pct, msg):
        if progress_callback: progress_callback(pct, msg)

    # ════════════════════════════════════════════════════════════════════════
    # PARALLEL SCRAPING — all sources run simultaneously, max 60s total wall time
    # Sequential scraping meant 10 sites × 15-25s = 150-250s → always timed out
    # ════════════════════════════════════════════════════════════════════════
    import concurrent.futures as _cfp
    import threading as _thr

    WALL_TIMEOUT   = 60   # seconds — all sources must finish within this window
    per_site_count = max(results_wanted // max(len(sites), 1), 10)

    # Build the full task list: (label, callable)
    tasks = {}

    # Google Jobs
    if google_jobs_on:
        tasks['google_jobs'] = lambda: scrape_google_jobs(
            search_term, location, api_key=serper_key, count=per_site_count)

    # JobSpy (LinkedIn/Indeed/Glassdoor/ZipRecruiter): one task per board, so a slow or
    # blocked board neither delays nor hides the others, and each gets its own health row.
    for js_site in jobspy_sites:
        tasks[js_site] = lambda s=js_site: scrape_jobspy(s, search_term, location, per_site_count)

    # Indian sites
    indian_fns = {
        'internshala': scrape_internshala,
        'naukri':      scrape_naukri,
        'shine':       scrape_shine,
        'foundit':     scrape_foundit,
        'apna':        scrape_apna,
    }
    for site in indian_sites:
        if site in indian_fns:
            fn = indian_fns[site]
            tasks[site] = lambda f=fn: f(search_term, location, per_site_count)

    # Dedicated remote/startup sites
    dedicated_fns = {
        'remotive':       scrape_remotive,
        'remoteok':       scrape_remoteok,
        'weworkremotely': scrape_weworkremotely,
        'himalayas':      scrape_himalayas,
        'wellfound':      scrape_wellfound,
        'yc_startup':     scrape_yc_startup,
        'builtin':        scrape_builtin,
        'remoteco':       scrape_remoteco,
        'toptal':         scrape_toptal,
        'contra':         scrape_contra,
        'flexjobs':       scrape_flexjobs,
        'hn_hiring':      scrape_hn_hiring,
        'turing':         scrape_turing,
        'upwork':         scrape_upwork,
    }
    for site in dedicated_sites:
        if site in dedicated_fns:
            fn = dedicated_fns[site]
            tasks[site] = lambda f=fn: f(search_term, location, per_site_count)

    # Custom sites
    enabled_custom = [s for s in custom_sites if s.get('enabled', True)]
    for cs in enabled_custom:
        name = cs.get('name', 'custom')
        tasks[f'custom:{name}'] = lambda c=cs: scrape_custom_site(
            c, search_term, location, per_site_count)

    # Every board reports to source health. A board that returns nothing is recorded as
    # a failure, not a success: "0 jobs, no error" is how 14 of 19 boards died unnoticed.
    tasks = {label: _tracked_board(label, fn) for label, fn in tasks.items()}

    # ── Run all tasks in parallel ──────────────────────────────────────────
    total_tasks = len(tasks)
    done_count  = [0]
    lock        = _thr.Lock()
    cb(5, f'Starting {total_tasks} sources in parallel...')

    max_workers = min(total_tasks, 12)   # cap at 12 concurrent threads
    with _cfp.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_label = {
            executor.submit(fn): label
            for label, fn in tasks.items()
        }
        for future in _cfp.as_completed(future_to_label, timeout=WALL_TIMEOUT):
            label = future_to_label[future]
            try:
                new = future.result(timeout=1)   # result already ready
                if new:
                    results.extend(new)
                with lock:
                    done_count[0] += 1
                    pct = 10 + int(done_count[0] / total_tasks * 45)
                    cb(pct, f'{label.replace("_"," ").title()}: {len(new or [])} jobs '
                            f'({done_count[0]}/{total_tasks} done)')
            except _cfp.TimeoutError:
                cb(50, f'{label} timed out — skipping')
            except Exception as e:
                with lock:
                    done_count[0] += 1
                cb(50, f'{label} error: {str(e)[:60]}')

    cb(55, f'Done — {len(results)} total jobs')

    # ── Tech role filter ──────────────────────────────────────────────────────
    if tech_only:
        before  = len(results)
        results = [r for r in results if _is_tech_role(r.get('title',''))]
        cb(55, f'Tech filter: kept {len(results)}/{before} tech roles')

    return results


def _tracked_board(label, fn):
    """Wrap one board's scrape so source health sees it: attempt, latency, and either a
    success (with the job count) or a failure. Exceptions are recorded and re-raised."""
    sid = 'board:' + label
    def run():
        _SM.record_attempt(sid)
        t0 = time.time()
        try:
            rows = fn() or []
        except Exception as e:
            _SM.record_failure(sid, e)
            raise
        finally:
            _SM.record_latency(sid, (time.time() - t0) * 1000.0)
        if rows:
            _SM.record_success(sid, len(rows))
        else:
            _SM.record_failure(sid, RuntimeError('no jobs returned'))
        return rows
    return run


# ── JOBSPY (LinkedIn / Indeed / Glassdoor / ZipRecruiter) ─────────────────────
def scrape_jobspy(site, search_term, location, count=20):
    """One board through python-jobspy. Errors propagate so the caller can report them;
    the old version printed and returned [], which looked exactly like "no jobs".

    Measured 26 Oct 2026 (3 searches, Mumbai/Pune/Bangalore): Indeed returned 25 jobs
    with full descriptions in about 1 s; LinkedIn 25 jobs without descriptions in about
    12 s. LinkedIn descriptions stay off: they cost about 1 s per job and the 30 fetched
    in testing held one email between them."""
    from jobspy import scrape_jobs as jobspy_scrape
    df = jobspy_scrape(
        site_name=[site],
        search_term=search_term,
        location=location,
        results_wanted=count,
        hours_old=168,
        country_indeed='India',
        linkedin_fetch_description=False,
        verbose=0,
    )
    out = []
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            sal = ''
            if row.get('min_amount') and str(row['min_amount']) not in ('nan', ''):
                sal = f"{row.get('min_amount','')}-{row.get('max_amount','')}"
            desc = row.get('description', '')
            out.append(_job(
                str(row.get('title', '') or ''),
                str(row.get('company', '') or ''),
                str(row.get('location', '') or ''),
                str(row.get('job_type', '') or ''),
                sal,
                '' if str(desc) == 'nan' else str(desc or ''),
                str(row.get('job_url', '') or ''),
                str(row.get('site', '') or site),
                str(row.get('company_url', '') or ''),
                date_posted=_iso_date(row.get('date_posted')),
            ))
    return out


def _iso_date(value) -> str:
    """'YYYY-MM-DD' for the day a board says it posted a job, '' when it gave none.
    JobSpy returns a date, or NaN/NaT/None for a listing that showed no date."""
    if value is None or value != value:          # NaN and NaT are not equal to themselves
        return ''
    s = value.isoformat() if hasattr(value, 'isoformat') else str(value)
    return s[:10] if re.match(r'\d{4}-\d{2}-\d{2}', s) else ''


# ── INTERNSHALA ───────────────────────────────────────────────────────────────
_IS_BADGE_RE = re.compile(
    r'(Actively\s*hiring|Urgently\s*hiring|Featured|Verified|Premium|New|Hot|'
    r'Sponsored|Promoted|Top\s*Recruiter|Actively\s*recruiting)', re.I)

def _clean_company_name(el):
    """Extract company name from an element, stripping badge/pill child elements."""
    if not el: return ''
    clone = BeautifulSoup(str(el), 'lxml').find()
    if not clone: return el.get_text(strip=True)
    for badge in clone.select('[class*="pill"], [class*="badge"], [class*="tag"], '
                              '[class*="active"], [class*="verified"], [class*="chip"]'):
        badge.decompose()
    text = clone.get_text(' ', strip=True)
    text = _IS_BADGE_RE.sub('', text).strip()
    return re.sub(r'\s{2,}', ' ', text).strip()

def scrape_internshala(search_term, location, count=20):
    """Internshala jobs/internships.

    TWO GATES, because this source answers 200 to a question it did not read.

    `internshala.com/jobs/<slug>-jobs` exists only when `<slug>` is a real Internshala
    category. When it is not, the site **redirects to `/jobs/`** — the generic
    16,141-job listing — and serves it at HTTP 200. The scraper parsed that page and
    returned "Sales Associate" and "Business Development Manager" for a search of
    "software engineer". `data-scientist-jobs` IS a category, so the same code looked
    fine whenever the term happened to match one.

    Worse, the candidate URLs were tried in order and the loop stopped at the first
    that produced rows — so the broken `-jobs` slug, "succeeding" with garbage,
    preempted `keywords-<slug>`, which always honours the term.

    ORDER: the category slug is still tried FIRST, because when the category exists it
    is strictly more precise — measured across five terms, category vs keywords scored
    100%/100%/69% against 77%/46%/62% relevance. Reordering to put `keywords-` first
    fixes "software engineer" but degrades every term whose category does exist
    ("data scientist" fell from 6 clean hits to 50% relevance). The gates below are
    what make category-first safe: when the slug is not a real category the redirect is
    caught and the loop falls through to `keywords-`, which is always present.
    """
    results = []
    try:
        slug = re.sub(r'[^a-z0-9]+', '-', search_term.strip().lower()).strip('-')

        # LOCATION WAS BEING DISCARDED. `location` was accepted, used only as a
        # fallback display value, and never reached a URL — so a committee in Mumbai
        # searching "Mumbai" got the same all-India list as everyone else. Measured
        # consequence: of 314 companies in the corpus, THREE were Mumbai-based.
        #
        # Internshala does support it: `/jobs/<slug>-jobs-in-mumbai` returns
        # "40 Software Engineer Jobs in Mumbai" at 200 with no redirect. City-scoped
        # URLs are tried FIRST; the redirect and relevance gates below handle the case
        # where a city or term has no such page and the site falls back to a generic
        # listing. `india` is skipped — it is the whole-country default, not a city.
        loc = re.sub(r'[^a-z0-9]+', '-', (location or '').strip().lower()).strip('-')
        loc = loc.split('-')[0] if loc else ''      # "mumbai-maharashtra" -> "mumbai"
        candidates = []
        if loc and loc not in ('india', 'remote', 'anywhere', ''):
            candidates.append(f"https://internshala.com/jobs/{slug}-jobs-in-{loc}")
        candidates += [
            f"https://internshala.com/jobs/{slug}-jobs",
            f"https://internshala.com/jobs/keywords-{slug}",
            f"https://internshala.com/internships/{slug}-internship",
        ]
        for url in candidates:
            resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code != 200: continue
            # GATE 1 — did the server quietly move us to a catch-all listing?
            is_generic, why = response_guard.redirected_to_generic(
                url, getattr(resp, 'url', url),
                generic_paths=('/jobs', '/internships'))
            if is_generic:
                _SM.record_failure('internshala', RuntimeError(f'off-query: {why}'), url=url)
                continue
            soup = BeautifulSoup(resp.text, 'lxml')
            cards = soup.select('.individual_internship')
            if not cards: cards = soup.select('[class*="internship_meta"]')
            page_rows = []
            for card in cards[:count]:
                title   = (_text(card, '.job-internship-name, .heading_4_5') or
                           _text(card, 'h3, [class*="heading"]'))
                # Clean company name — strip "Actively hiring" and other badge texts
                co_el   = (card.select_one('.company-name, .heading_6') or
                           card.select_one('[class*="company"]'))
                company = _clean_company_name(co_el)
                loc_t   = _text(card, '[id*="location_names"], .location_link') or location
                stipend = _text(card, '.stipend, .salary')
                link    = card.select_one('a.view_detail_button, a[href*="/job/"], a[href*="/internship/"]')
                href    = ''
                if link and link.get('href'):
                    href = link['href']
                    if href.startswith('/'): href = 'https://internshala.com' + href
                if title and company:
                    page_rows.append(_job(title, company, loc_t, 'Internship/FT', stipend, '', href, 'internshala'))
            # GATE 2 — does this page actually answer the query? A generic listing
            # that slipped past gate 1 still scores ~0% relevance and is discarded
            # here rather than returned as a result.
            bad, detail = response_guard.off_query(
                [r.get('title', '') for r in page_rows], search_term)
            if bad:
                _SM.record_failure('internshala',
                                   RuntimeError(f'off-query content: {detail}'), url=url)
                continue
            results.extend(page_rows)
            if results: break
    except Exception as _e:
        _SM.record_failure('internshala', _e)
    return results[:count]


# ── NAUKRI ────────────────────────────────────────────────────────────────────
def scrape_naukri(search_term, location, count=20):
    results = []
    try:
        kw_slug  = re.sub(r'[^a-z0-9]+', '-', search_term.strip().lower()).strip('-')
        loc_slug = re.sub(r'[^a-z0-9]+', '-', (location or 'india').strip().lower()).strip('-')

        # Method 1: Naukri REST API
        r = requests.get(
            'https://www.naukri.com/jobapi/v3/search',
            params={
                'noOfResults': min(count, 20),
                'urlType': 'search_by_keyword',
                'searchType': 'adv',
                'keyword': search_term,
                'location': location,
                'pageNo': 1,
            },
            headers={
                **JSON_HEADERS,
                'appid': '109',
                'systemid': '109',
                'x-requested-with': 'XMLHttpRequest',
                'Referer': f'https://www.naukri.com/{kw_slug}-jobs-in-{loc_slug}',
                'Origin': 'https://www.naukri.com',
            },
            timeout=15
        )
        if r.status_code != 200:
            # 406 "recaptcha required" since at least Oct 2026: record it, so health
            # shows Naukri as blocked instead of empty.
            _SM.record_http('board:naukri', r.status_code)
        if r.status_code == 200:
            jobs = r.json().get('jobDetails', [])
            for j in jobs[:count]:
                ph  = j.get('placeholders', [])
                loc_t = next((p['label'] for p in ph if p.get('type') == 'location'), location)
                sal   = next((p['label'] for p in ph if p.get('type') == 'salary'), '')
                results.append(_job(
                    j.get('title',''), j.get('companyName',''),
                    loc_t, 'Full-time', sal,
                    j.get('jobDescription',''),
                    'https://www.naukri.com' + j.get('jdURL',''), 'naukri'
                ))

        # Method 2+3: Parse HTML page (NEXT_DATA → CSS selectors fallback)
        if not results:
            r2   = requests.get(
                f'https://www.naukri.com/{kw_slug}-jobs-in-{loc_slug}',
                headers=HEADERS, timeout=15
            )
            soup = BeautifulSoup(r2.text, 'lxml')

            script = soup.find('script', id='__NEXT_DATA__')
            if script:
                nd   = json.loads(script.string)
                jobs = (nd.get('props',{}).get('pageProps',{})
                          .get('jobDetails',{}).get('jobDetails',[]))
                for j in jobs[:count]:
                    ph  = j.get('placeholders', [])
                    loc_t = next((p.get('label','') for p in ph if p.get('type')=='location'), location)
                    results.append(_job(
                        j.get('title',''), j.get('companyName',''),
                        loc_t, 'Full-time', '',
                        j.get('jobDescription',''),
                        'https://www.naukri.com' + j.get('jdURL',''), 'naukri'
                    ))

            # CSS selector fallback — reuse same soup (no extra HTTP request)
            if not results:
                for sel in ['.srp-jobtuple-wrapper', 'article.jobTuple', '.jobTuple']:
                    cards = soup.select(sel)
                    if cards:
                        for card in cards[:count]:
                            title   = _text(card, 'a.title, .title')
                            company = _text(card, 'a.comp-name, .comp-name, .subTitle')
                            loc_t   = _text(card, '.locWdth, .location, .loc') or location
                            link    = card.select_one('a.title, a[href*="naukri.com"]')
                            href    = link['href'] if link else ''
                            if title:
                                results.append(_job(title, company, loc_t, 'Full-time', '', '', href, 'naukri'))
                        break

    except Exception as e:
        _SM.record_failure('board:naukri', e)
    return results[:count]


# ── FOUNDIT ───────────────────────────────────────────────────────────────────
def scrape_foundit(search_term, location, count=20):
    results = []
    try:
        # Foundit JSON API
        r = requests.post(
            'https://www.foundit.in/middleware/jobsearch/v1/search',
            json={'query': search_term, 'location': location or 'India',
                  'limit': min(count, 25), 'offset': 0, 'sort': 1},
            headers={**JSON_HEADERS, 'Origin': 'https://www.foundit.in',
                     'Referer': 'https://www.foundit.in/'},
            timeout=15
        )
        if r.status_code == 200:
            jobs = r.json().get('jobSearchResponse', {}).get('data', [])
            for j in jobs[:count]:
                results.append(_job(
                    j.get('designationName',''), j.get('companyName',''),
                    j.get('locationName', location), j.get('jobTypeDisplay','Full-time'),
                    str(j.get('minSalary','')),
                    j.get('jobDesc',''),
                    f"https://www.foundit.in/job/{j.get('id','')}", 'foundit'
                ))

        if not results:
            url  = f"https://www.foundit.in/srp/results?query={quote(search_term)}&location={quote(location or 'India')}"
            r2   = requests.get(url, headers=HEADERS, timeout=8)
            soup = BeautifulSoup(r2.text, 'lxml')
            for card in soup.select('[class*="jobCard"], [class*="card-body"], .job-listing')[:count]:
                title   = _text(card, '[class*="title"], h3, h2')
                company = _text(card, '[class*="company"]')
                loc_t   = _text(card, '[class*="location"]') or location
                link    = card.select_one('a[href]')
                href    = link['href'] if link else ''
                if href and not href.startswith('http'): href = 'https://www.foundit.in' + href
                if title:
                    results.append(_job(title, company, loc_t, 'Full-time', '', '', href, 'foundit'))

    except Exception: pass
    return results[:count]


# ── SHINE ─────────────────────────────────────────────────────────────────────
def scrape_shine(search_term, location, count=20):
    results = []
    try:
        slug = search_term.strip().lower().replace(' ', '-')
        url  = f"https://www.shine.com/job-search/{quote(slug)}-jobs"
        r    = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, 'lxml')

        # Try JSON-LD (most reliable)
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '{}')
                if data.get('@type') == 'ItemList':
                    for item in data.get('itemListElement', [])[:count]:
                        j = item.get('item', {})
                        results.append(_job(
                            j.get('title',''),
                            j.get('hiringOrganization',{}).get('name',''),
                            j.get('jobLocation',{}).get('address',{}).get('addressLocality', location),
                            j.get('employmentType','Full-time'),
                            '', j.get('description',''),
                            j.get('url',''), 'shine'
                        ))
                    break
            except Exception: continue

        if not results:
            for card in soup.select('.jobCard, .job-listing, [class*="job_card"]')[:count]:
                title   = _text(card, 'h3, .jobTitle, [class*="title"]')
                company = _text(card, '.companyName, [class*="company"]')
                loc_t   = _text(card, '.location, [class*="location"]') or location
                link    = card.select_one('a[href]')
                href    = link['href'] if link else ''
                if href and not href.startswith('http'): href = 'https://www.shine.com' + href
                if title:
                    results.append(_job(title, company, loc_t, 'Full-time', '', '', href, 'shine'))

    except Exception: pass
    return results[:count]


# ── APNA ──────────────────────────────────────────────────────────────────────
def scrape_apna(search_term, location, count=20):
    results = []
    try:
        r = requests.post(
            'https://apna.co/api/job-portal/v1/public/jobs/search',
            json={'searchQuery': search_term, 'city': location or 'India',
                  'pageSize': min(count, 20), 'pageNumber': 1},
            headers={**JSON_HEADERS, 'Origin': 'https://apna.co',
                     'Referer': 'https://apna.co/'},
            timeout=15
        )
        if r.status_code == 200:
            jobs = r.json().get('data', {}).get('jobs', [])
            for j in jobs[:count]:
                results.append(_job(
                    j.get('jobTitle',''), j.get('orgName',''),
                    j.get('city', location), j.get('jobType','Full-time'),
                    f"{j.get('minSalary','')}-{j.get('maxSalary','')}",
                    j.get('description',''),
                    f"https://apna.co/job/{j.get('jobId','')}", 'apna'
                ))

        if not results:
            url  = f"https://apna.co/jobs?q={quote(search_term)}&city={quote(location or 'India')}"
            r2   = requests.get(url, headers=HEADERS, timeout=8)
            soup = BeautifulSoup(r2.text, 'lxml')
            for card in soup.select('[class*="jobCard"], [class*="JobCard"]')[:count]:
                title   = _text(card, '[class*="title"], h3, h4')
                company = _text(card, '[class*="company"]')
                loc_t   = _text(card, '[class*="location"], [class*="city"]') or location
                link    = card.select_one('a[href]')
                href    = link['href'] if link else ''
                if href and not href.startswith('http'): href = 'https://apna.co' + href
                if title:
                    results.append(_job(title, company, loc_t, 'Full-time', '', '', href, 'apna'))

    except Exception: pass
    return results[:count]


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _text(el, selector):
    f = el.select_one(selector)
    return f.get_text(strip=True) if f else ''

# ── Serper.dev API key (set via Settings page or env var) ────────────────────
import os as _os
_SERPER_KEY = _os.environ.get('SERPER_API_KEY', '')


# ── Google Jobs scraper via Serper.dev ────────────────────────────────────────
def scrape_google_jobs(search_term, location='India', api_key='', count=20):
    """
    Scrapes Google Jobs via Serper.dev API.
    Google Jobs aggregates from LinkedIn, Indeed, Naukri, Glassdoor, Internshala
    and hundreds of other boards — all in one request, no blocking.

    Serper.dev free tier: 2,500 searches/month.
    Sign up at https://serper.dev to get an API key.
    """
    results = []
    if not api_key:
        return results

    try:
        # Map location to country code for gl= param
        loc_lower = location.lower()
        gl = 'in'  # default India
        if any(x in loc_lower for x in ['usa', 'united states', 'us']): gl = 'us'
        elif any(x in loc_lower for x in ['uk', 'united kingdom', 'britain']): gl = 'gb'
        elif 'canada' in loc_lower: gl = 'ca'
        elif 'australia' in loc_lower: gl = 'au'
        elif 'singapore' in loc_lower: gl = 'sg'
        elif 'uae' in loc_lower or 'dubai' in loc_lower: gl = 'ae'

        # Serper.dev — use /search endpoint with jobs query modifier
        # /jobs endpoint doesn't exist on serper.dev; correct endpoint is /search
        # Appending "jobs" to query triggers Google's jobs carousel in organic results
        jobs_query = f'{search_term} jobs {location}'.strip()

        resp = requests.post(
            'https://google.serper.dev/search',
            headers={
                'X-API-KEY': api_key,
                'Content-Type': 'application/json',
            },
            json={
                'q':        jobs_query,
                'gl':       gl,
                'hl':       'en',
                'num':      10,
                'autocorrect': True,
            },
            timeout=10,
        )

        if resp.status_code != 200:
            print(f'[Serper] Error {resp.status_code}: {resp.text[:200]}')
            return results

        data = resp.json()
        credits = data.get('credits', '?')
        print(f'[Serper] status OK, {credits} credits remaining')

        # Google Jobs carousel comes back in data['jobs'] on /search too
        job_list = data.get('jobs', [])

        # Fallback: parse organic results if jobs carousel empty
        if not job_list:
            for item in data.get('organic', []):
                title   = item.get('title', '').replace(' - Google Jobs', '').strip()
                link    = item.get('link', '')
                snippet = item.get('snippet', '')
                # Only include if it looks like an actual job posting
                if title and any(kw in title.lower() for kw in
                                 ['engineer', 'developer', 'analyst', 'manager',
                                  'designer', 'scientist', 'intern', 'consultant']):
                    results.append(_job(title, '', location, '', '', snippet, link, 'google_jobs'))
            return results[:count]

        for j in job_list:
            title    = j.get('title', '')
            company  = j.get('companyName', '')
            loc_t    = j.get('location', location)
            desc     = j.get('description', '')
            salary   = ''
            job_type = ''

            exts = j.get('extensions', [])
            for ext in exts:
                ext_s = str(ext)
                if any(sym in ext_s for sym in ['₹','$','£','LPA','lakh','per year','per month']):
                    salary = ext_s
                if 'full' in ext_s.lower():    job_type = 'fulltime'
                elif 'part' in ext_s.lower():  job_type = 'parttime'
                elif 'contract' in ext_s.lower(): job_type = 'contract'
                elif 'intern' in ext_s.lower(): job_type = 'internship'

            job_url     = ''
            company_url = ''
            JB = {'linkedin.com','naukri.com','indeed.com','internshala.com','glassdoor.com','google.com'}
            for rl in j.get('relatedLinks', []):
                u = rl.get('link', '')
                if u and not job_url:
                    job_url = u
                if u and not company_url and not any(jb in u for jb in JB):
                    company_url = u

            if title and company:
                results.append(_job(title, company, loc_t, job_type, salary,
                                    desc, job_url, 'google_jobs', company_url))

    except Exception as e:
        print(f'[Serper] Exception: {e}')

    return results[:count]


def _job(title, company, location, job_type, salary, desc, url, site, company_url='',
         date_posted=''):
    return {
        'title':        (title or '').strip(),
        'company':      (company or '').strip(),
        'location':     (location or '').strip(),
        'job_type':     (job_type or '').strip(),
        'salary':       (salary or '').strip(),
        'description':  (desc or '').strip(),
        'job_url':      (url or '').strip(),
        'site':         site,
        'date_posted':  date_posted or '',
        'company_url':  (company_url or '').strip(),
        'emails':       '',
        'phone':        '',
        'hr_contact':   '',
        'email_source': '',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSAL CUSTOM SITE SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════
def scrape_custom_site(site_cfg: dict, search_term: str, location: str, count: int = 20):
    """
    Generic scraper for any job site added via the Custom Sites UI.

    Supports two modes:
      A) User-defined CSS selectors  — most accurate when configured
      B) Auto-detect mode            — heuristic scraping when no selectors given

    The search URL supports placeholders:
      {query}    → URL-encoded search term (e.g. "software+engineer")
      {location} → URL-encoded location
      {count}    → number of results wanted
    """
    results  = []
    name     = site_cfg.get('name', 'custom')
    site_key = re.sub(r'[^a-z0-9]', '_', name.lower())

    try:
        # ── Build search URL ──────────────────────────────────────────────────
        search_url = site_cfg.get('search_url', '').strip()
        if not search_url:
            return results

        q   = quote(search_term)
        loc = quote(location)
        url = (search_url
               .replace('{query}',    q)
               .replace('{location}', loc)
               .replace('{count}',    str(count)))

        r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            print(f'[Custom:{name}] HTTP {r.status_code}')
            return results

        soup = BeautifulSoup(r.text, 'lxml')

        # ── Mode A: user-defined selectors ────────────────────────────────────
        card_sel = site_cfg.get('card_sel', '').strip()
        if card_sel:
            cards = soup.select(card_sel)[:count]
            for card in cards:
                title   = _sel_text(card, site_cfg.get('title_sel'))
                company = _sel_text(card, site_cfg.get('company_sel'))
                loc_t   = _sel_text(card, site_cfg.get('location_sel')) or location
                salary  = _sel_text(card, site_cfg.get('salary_sel'))

                # Link — try selector first, then any <a> in the card
                link_sel = site_cfg.get('link_sel', '').strip()
                href = ''
                if link_sel:
                    a = card.select_one(link_sel)
                    href = a['href'] if a and a.get('href') else ''
                if not href:
                    a = card.select_one('a[href]')
                    href = a['href'] if a else ''
                if href and not href.startswith('http'):
                    base = site_cfg.get('base_url', '').rstrip('/')
                    href = base + ('/' if not href.startswith('/') else '') + href

                if title:
                    results.append(_job(title, company, loc_t, '', salary, '', href, site_key))
            return results[:count]

        # ── Mode B: auto-detect (no selectors configured) ─────────────────────
        # Heuristic: look for repeated block elements that contain a job title
        results = _autodetect_jobs(soup, site_cfg, search_term, location, site_key, count)

    except Exception as e:
        print(f'[Custom:{name}] Error: {e}')

    return results[:count]


def _sel_text(el, selector):
    """Safe select + getText with empty-string fallback."""
    if not selector or not el:
        return ''
    try:
        found = el.select_one(selector.strip())
        return found.get_text(strip=True) if found else ''
    except Exception:
        return ''


def _autodetect_jobs(soup, site_cfg, search_term, location, site_key, count):
    """
    Heuristic job extraction when no CSS selectors are provided.
    Strategy:
      1. Find the most-repeated block element (div/li/article) — likely job cards
      2. Within each block, pick the largest <a> text as title
      3. Extract any nearby company/location text
      4. Deduplicate by href
    """
    results = []
    base    = site_cfg.get('base_url', '').rstrip('/')

    # Try common job card patterns in order of specificity
    CARD_PATTERNS = [
        'article',
        '[class*="job-card"]', '[class*="jobCard"]', '[class*="job_card"]',
        '[class*="job-item"]', '[class*="jobItem"]',
        '[class*="job-listing"]', '[class*="jobListing"]',
        '[class*="job-result"]', '[class*="result-item"]',
        '[class*="vacancy"]', '[class*="position"]',
        '[class*="opening"]', '[class*="listing"]',
        'li[class*="job"]', 'div[class*="job"]',
    ]

    cards = []
    for pat in CARD_PATTERNS:
        try:
            cards = soup.select(pat)
            if len(cards) >= 3:   # at least 3 = likely a listing page
                break
        except Exception:
            continue

    if not cards:
        # Last resort: all <li> with an <a> inside
        cards = [li for li in soup.select('li') if li.select_one('a') and len(li.get_text()) > 20]

    seen_hrefs = set()
    for card in cards[:count * 2]:   # fetch extra, dedup later
        # Title: the longest <a> text in the card or biggest heading
        title = ''
        href  = ''
        best_a = None
        for a in card.select('a[href]'):
            txt = a.get_text(strip=True)
            if len(txt) > len(title) and len(txt) < 120:
                title  = txt
                best_a = a
                href   = a.get('href', '')

        if not title:
            for h in card.select('h1,h2,h3,h4,h5'):
                t = h.get_text(strip=True)
                if t and len(t) < 120:
                    title = t
                    break

        if not title or len(title) < 3:
            continue

        # Normalise URL
        if href and not href.startswith('http'):
            href = base + ('/' if not href.startswith('/') else '') + href
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        # Company: second largest text block or [class*=company] descendant
        company = ''
        for sel in ['[class*="company"]', '[class*="employer"]', '[class*="org"]']:
            el = card.select_one(sel)
            if el:
                company = el.get_text(strip=True)
                break

        # Location
        loc_t = location
        for sel in ['[class*="location"]', '[class*="city"]', '[class*="place"]']:
            el = card.select_one(sel)
            if el:
                loc_t = el.get_text(strip=True)
                break

        results.append(_job(title, company, loc_t, '', '', '', href, site_key))
        if len(results) >= count:
            break

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# DEDICATED SCRAPERS — Remote & Startup Job Boards
# Each uses the best available method: public API > RSS > HTML
# ═══════════════════════════════════════════════════════════════════════════════

# ── Remotive — Free public JSON API (no auth needed) ─────────────────────────
def scrape_remotive(search_term, location='', count=20):
    """
    Uses Remotive's official free public API.
    https://remotive.com/api/remote-jobs?search=<keyword>
    Returns hand-screened remote jobs with salary + geo requirements.
    """
    results = []
    try:
        r = requests.get(
            'https://remotive.com/api/remote-jobs',
            params={'search': search_term, 'limit': min(count, 50)},
            headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return results
        jobs = r.json().get('jobs', [])
        # The API ignores `search` (Oct 2026: the same 10 jobs for any term), so keep
        # only jobs whose title or tags carry every word of the search.
        jobs = [j for j in jobs
                if _matches_terms(search_term, j.get('title', ''), ' '.join(j.get('tags') or []))]
        for j in jobs[:count]:
            geo = j.get('candidate_required_location', 'Worldwide')
            results.append(_job(
                j.get('title', ''),
                j.get('company_name', ''),
                geo,
                j.get('job_type', ''),
                j.get('salary', ''),
                j.get('description', '')[:800],
                j.get('url', ''),
                'remotive',
                # No company_url: the only company link here is the LOGO image, which was
                # being stored as the website (inflating the grade, misleading the crawler).
            ))
    except Exception as e:
        print(f'[Remotive] {e}')
    return results[:count]


# ── RemoteOK — Free public JSON API ──────────────────────────────────────────
def scrape_remoteok(search_term, location='', count=20):
    """
    Uses RemoteOK's public API endpoint.
    https://remoteok.com/api — returns all jobs, we filter by keyword.
    """
    results = []
    try:
        # The `?tags=` form answered 200 with only the legal notice, so the fallback never
        # ran and every search returned 0. Fetch the full feed and filter it here.
        r = requests.get('https://remoteok.com/api',
                         headers={**HEADERS, 'Accept': 'application/json'}, timeout=12)
        if r.status_code != 200:
            _SM.record_http('board:remoteok', r.status_code)
            return results

        data = r.json()
        # First item is always a legal notice, skip it
        jobs = [j for j in data if isinstance(j, dict) and j.get('position')]

        # Every word of the search must appear in the title or tags. The old test looked
        # for the whole phrase ("software engineer") and missed "Senior Engineer, Software".
        matched = [j for j in jobs
                   if _matches_terms(search_term, j.get('position', ''), ' '.join(j.get('tags') or []))]

        for j in matched[:count]:
            salary = ''
            if j.get('salary_min'):
                salary = f"${j['salary_min']:,}–${j.get('salary_max', j['salary_min']):,}/yr"
            results.append(_job(
                j.get('position', ''),
                j.get('company', ''),
                j.get('location', 'Worldwide'),
                '',
                salary,
                (j.get('description') or '')[:800],
                f"https://remoteok.com/remote-jobs/{j.get('slug', '')}",
                'remoteok',
                # company_logo is an image, not the company's website; leave the URL empty.
            ))
    except Exception as e:
        print(f'[RemoteOK] {e}')
    return results[:count]


# ── We Work Remotely — RSS Feed (most reliable, no blocking) ──────────────────
def scrape_weworkremotely(search_term, location='', count=20):
    """
    Uses WWR's RSS feeds — one per category, no auth, no blocking.
    Searches: programming, devops-sysadmin, product, design, finance-legal
    """
    import xml.etree.ElementTree as ET

    results  = []
    kw       = search_term.lower()

    # Map common search terms to WWR's category RSS endpoints
    CATEGORY_MAP = {
        'programming':     ['python','java','javascript','node','react','angular',
                            'software','developer','engineer','backend','frontend',
                            'fullstack','full-stack','php','ruby','golang','rust'],
        'devops-sysadmin': ['devops','cloud','aws','gcp','azure','kubernetes',
                            'docker','infrastructure','sre','platform'],
        'product':         ['product','scrum','agile','project manager','pm'],
        'design':          ['designer','ui','ux','figma','design'],
        'data-science':    ['data','machine learning','ml','ai','analyst','nlp'],
        'security':        ['security','cybersecurity','penetration','soc'],
        'qa':              ['qa','quality assurance','testing','sdet','automation'],
    }

    # Pick relevant categories
    selected_cats = []
    for cat, kws in CATEGORY_MAP.items():
        if any(k in kw for k in kws):
            selected_cats.append(cat)
    if not selected_cats:
        selected_cats = ['programming']  # sensible default for tech roles

    seen = set()
    for cat in selected_cats[:2]:   # max 2 categories per search
        try:
            rss_url = f'https://weworkremotely.com/categories/remote-{cat}-jobs.rss'
            r = requests.get(rss_url, headers=HEADERS, timeout=8)
            if r.status_code != 200:
                continue

            root = ET.fromstring(r.content)
            ns   = ''
            for item in root.findall('.//item'):
                title_el   = item.find('title')
                company_el = item.find('author') or item.find('dc:creator',
                             {'dc': 'http://purl.org/dc/elements/1.1/'})
                link_el    = item.find('link')
                region_el  = item.find('region')
                desc_el    = item.find('description')

                title   = title_el.text   if title_el   else ''
                company = company_el.text if company_el else ''
                link    = link_el.text    if link_el    else ''
                region  = region_el.text  if region_el  else 'Worldwide'
                desc    = desc_el.text    if desc_el    else ''

                # WWR title format: "Company: Job Title at Company"
                if ': ' in title:
                    parts   = title.split(': ', 1)
                    company = parts[0].strip()
                    title   = parts[1].strip()

                # Filter by keyword
                combined = f'{title} {desc}'.lower()
                if kw and kw not in combined:
                    continue

                key = link or title
                if key in seen:
                    continue
                seen.add(key)

                results.append(_job(
                    title, company, region, '', '', desc[:600],
                    link, 'weworkremotely',
                ))

                if len(results) >= count:
                    break

        except Exception as e:
            print(f'[WWR:{cat}] {e}')

        if len(results) >= count:
            break

    return results[:count]


# ── Himalayas — Free public API ───────────────────────────────────────────────
def scrape_himalayas(search_term, location='', count=20):
    """
    Uses Himalayas' undocumented but stable public JSON API.
    No auth required. Returns rich structured data.
    """
    results = []
    try:
        # `/jobs/api` ignores `q` and returns the latest jobs; `/jobs/api/search` searches.
        r = requests.get(
            'https://himalayas.app/jobs/api/search',
            params={'q': search_term, 'limit': min(count, 30)},
            headers={**HEADERS, 'Accept': 'application/json'},
            timeout=15,   # the search endpoint is slower than the feed
        )
        if r.status_code != 200:
            _SM.record_http('board:himalayas', r.status_code)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get('jobs', [])
            for j in jobs[:count]:
                # The API returns flat `companyName`; reading `company.name` left every
                # company blank (Oct 2026 audit: 20 jobs, 0 companies).
                company = j.get('companyName', '')
                salary  = ''
                if j.get('minSalary'):
                    salary = f"${j['minSalary']:,}–${j.get('maxSalary', j['minSalary']):,}"
                locs = j.get('locationRestrictions') or []
                results.append(_job(
                    j.get('title', ''),
                    company,
                    ', '.join(locs) if isinstance(locs, list) and locs else 'Remote',
                    j.get('employmentType', ''),
                    salary,
                    (j.get('description') or '')[:800],
                    j.get('applicationLink') or f"https://himalayas.app/companies/{j.get('companySlug','')}",
                    'himalayas',
                ))
    except Exception as e:
        print(f'[Himalayas] {e}')
    return results[:count]


# ── Wellfound (AngelList Talent) — HTML scraper ───────────────────────────────
def scrape_wellfound(search_term, location='', count=20):
    """
    Scrapes Wellfound job listings using their role-based URL structure.
    Shows salary + equity upfront. Great for startup roles.
    """
    results = []
    try:
        slug = re.sub(r'[^a-z0-9]+', '-', search_term.lower()).strip('-')
        urls = [
            f'https://wellfound.com/role/r/{slug}',
            f'https://wellfound.com/jobs?q={quote(search_term)}',
        ]
        for url in urls:
            r = requests.get(url, headers={
                **HEADERS,
                'Accept': 'text/html,application/xhtml+xml',
            }, timeout=8)
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, 'lxml')

            # Try JSON-LD structured data first
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string or '{}')
                    if isinstance(data, list):
                        items = data
                    elif data.get('@type') == 'JobPosting':
                        items = [data]
                    else:
                        items = data.get('@graph', [])

                    for item in items:
                        if item.get('@type') != 'JobPosting':
                            continue
                        org      = item.get('hiringOrganization', {})
                        company  = org.get('name', '')
                        co_url   = org.get('url', '')
                        loc      = (item.get('jobLocation') or {}).get('address', {}).get('addressLocality', 'Remote')
                        salary_d = item.get('baseSalary', {}).get('value', {})
                        salary   = f"${salary_d.get('minValue','?')}–${salary_d.get('maxValue','?')}" if salary_d else ''
                        results.append(_job(
                            item.get('title', ''),
                            company,
                            loc, '', salary,
                            item.get('description', '')[:600],
                            item.get('url', url),
                            'wellfound', co_url,
                        ))
                        if len(results) >= count:
                            break
                except Exception as _e:
                    _SM.record_failure('wellfound', _e)

            if results:
                break

            # HTML fallback
            cards = (soup.select('[data-test="StartupResult"]') or
                     soup.select('.job-listing') or
                     soup.select('[class*="JobResult"]'))
            for card in cards[:count]:
                title   = _sel_text(card, '[data-test="JobTitle"], h3, .job-title')
                company = _sel_text(card, '[data-test="CompanyName"], .company-name')
                loc_t   = _sel_text(card, '[data-test="Location"], .location') or 'Remote'
                salary  = _sel_text(card, '[data-test="CompensationText"], .salary')
                a       = card.select_one('a[href*="/jobs/"], a[href*="/role/"]')
                href    = ''
                if a:
                    href = a.get('href', '')
                    if href.startswith('/'):
                        href = 'https://wellfound.com' + href
                if title:
                    results.append(_job(title, company, loc_t, '', salary, '', href, 'wellfound'))

            if results:
                break

    except Exception as e:
        print(f'[Wellfound] {e}')
    return results[:count]


# ── Y Combinator Work at a Startup ───────────────────────────────────────────
def scrape_yc_startup(search_term, location='', count=20):
    """
    Scrapes YC's Work at a Startup board.
    Apply to hundreds of YC-backed startups with one profile.
    """
    results = []
    try:
        r = requests.get(
            'https://www.workatastartup.com/jobs',
            params={'q': search_term},
            headers={**HEADERS, 'Accept': 'text/html'},
            timeout=8,
        )
        if r.status_code != 200:
            return results

        soup = BeautifulSoup(r.text, 'lxml')

        # YC renders via React — try JSON in __NEXT_DATA__ or __SSR_DATA__
        for script_id in ['__NEXT_DATA__', '__SSR_DATA__']:
            script = soup.find('script', id=script_id)
            if not script:
                continue
            try:
                nd   = json.loads(script.string)
                jobs = []
                # Traverse the JSON tree to find job listings
                def _find_jobs(obj, depth=0):
                    if depth > 8: return
                    if isinstance(obj, list):
                        for item in obj:
                            if isinstance(item, dict) and item.get('title') and item.get('company'):
                                jobs.append(item)
                            else:
                                _find_jobs(item, depth+1)
                    elif isinstance(obj, dict):
                        for v in obj.values():
                            _find_jobs(v, depth+1)
                _find_jobs(nd)

                for j in jobs[:count]:
                    co   = j.get('company', {}) if isinstance(j.get('company'), dict) else {}
                    name = co.get('name', '') or str(j.get('company', ''))
                    results.append(_job(
                        j.get('title', ''), name,
                        j.get('location', 'Remote'), j.get('job_type', ''),
                        j.get('compensation', ''),
                        j.get('description', '')[:600],
                        f"https://www.workatastartup.com/jobs/{j.get('id','')}",
                        'yc_startup',
                    ))
                if results:
                    break
            except Exception as _e:
                _SM.record_failure('yc_startup', _e)

        # HTML fallback
        if not results:
            cards = soup.select('.job-name, [class*="JobRow"], .company-jobs-table tr')
            for card in cards[:count]:
                title   = _sel_text(card, '.job-name, h3, td:first-child')
                company = _sel_text(card, '.company-name, .name, td:nth-child(2)')
                loc_t   = _sel_text(card, '.location, td:nth-child(3)') or 'Remote'
                a = card.select_one('a[href*="/jobs/"]')
                href = ('https://www.workatastartup.com' + a['href']) if a and a.get('href','').startswith('/') else (a['href'] if a else '')
                if title:
                    results.append(_job(title, company, loc_t, '', '', '', href, 'yc_startup'))

    except Exception as e:
        print(f'[YC Startup] {e}')
    return results[:count]


# ── Built In — Tech company culture + jobs ────────────────────────────────────
def scrape_builtin(search_term, location='', count=20):
    """
    Scrapes Built In's remote job listings.
    Great for company culture + tech stack visibility.
    """
    results = []
    try:
        slug = re.sub(r'\s+', '-', search_term.strip().lower())
        urls = [
            f'https://builtin.com/jobs/remote/{quote(search_term)}',
            f'https://builtin.com/jobs?search={quote(search_term)}&remote=true',
        ]
        for url in urls:
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'lxml')

            cards = (soup.select('[data-id="job-card"]') or
                     soup.select('.job-card') or
                     soup.select('article[class*="job"]'))

            for card in cards[:count]:
                title   = _sel_text(card, 'h2, h3, [data-id="job-title"]')
                company = _sel_text(card, '[data-id="company-title"], .company')
                loc_t   = _sel_text(card, '[data-id="job-location"], .location') or 'Remote'
                salary  = _sel_text(card, '[data-id="job-salary"], .salary')
                a       = card.select_one('a[href*="/job/"]')
                href    = ''
                if a:
                    href = a.get('href', '')
                    if href.startswith('/'):
                        href = 'https://builtin.com' + href
                if title:
                    results.append(_job(title, company, loc_t, '', salary, '', href, 'builtin'))
            if results:
                break
    except Exception as e:
        print(f'[BuiltIn] {e}')
    return results[:count]


# ── Remote.co — Curated remote jobs ──────────────────────────────────────────
def scrape_remoteco(search_term, location='', count=20):
    """
    Scrapes Remote.co's curated remote job listings.
    """
    results = []
    try:
        r = requests.get(
            f'https://remote.co/remote-jobs/search/?search_keywords={quote(search_term)}',
            headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return results
        soup = BeautifulSoup(r.text, 'lxml')
        cards = soup.select('.job_listings .job_listing, .card.m-b-sm')
        for card in cards[:count]:
            title   = _sel_text(card, '.position h2, h3.card-title, .job_listing-title')
            company = _sel_text(card, '.company, .company_name, .card-subtitle')
            loc_t   = _sel_text(card, '.location, .job_listing-location') or 'Remote'
            a = card.select_one('a[href*="/remote-jobs/"]')
            href = ''
            if a:
                href = a.get('href', '')
                if href.startswith('/'):
                    href = 'https://remote.co' + href
            if title:
                results.append(_job(title, company, loc_t, '', '', '', href, 'remoteco'))
    except Exception as e:
        print(f'[Remote.co] {e}')
    return results[:count]


# ── Toptal — Elite freelance network ─────────────────────────────────────────
def scrape_toptal(search_term, location='', count=20):
    """
    Scrapes Toptal's open job listings.
    Top 3% vetting — high-paying corporate clients.
    """
    results = []
    try:
        r = requests.get(
            f'https://www.toptal.com/freelance-jobs#text={quote(search_term)}',
            headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            r = requests.get('https://www.toptal.com/freelance-jobs', headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return results
        soup = BeautifulSoup(r.text, 'lxml')
        cards = (soup.select('[class*="JobCard"]') or
                 soup.select('.job-listing-item') or
                 soup.select('li[class*="job"]'))
        kw = search_term.lower()
        for card in cards[:count * 2]:
            title   = _sel_text(card, 'h2, h3, [class*="title"]')
            if not title or kw not in title.lower():
                continue
            company = _sel_text(card, '[class*="company"], [class*="client"]') or 'Toptal Client'
            loc_t   = _sel_text(card, '[class*="location"]') or 'Remote'
            salary  = _sel_text(card, '[class*="rate"], [class*="salary"]')
            a = card.select_one('a[href]')
            href = ''
            if a:
                href = a.get('href', '')
                if href.startswith('/'):
                    href = 'https://www.toptal.com' + href
            results.append(_job(title, company, loc_t, 'contract', salary, '', href, 'toptal'))
            if len(results) >= count:
                break
    except Exception as e:
        print(f'[Toptal] {e}')
    return results[:count]


# ── Contra — Commission-free freelance platform ───────────────────────────────
def scrape_contra(search_term, location='', count=20):
    """
    Scrapes Contra's independent work listings.
    Portfolio-led, commission-free, modern clients.
    """
    results = []
    try:
        r = requests.get(
            f'https://contra.com/jobs?q={quote(search_term)}',
            headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return results
        soup = BeautifulSoup(r.text, 'lxml')
        cards = (soup.select('[class*="OpportunityCard"]') or
                 soup.select('[class*="job-card"]') or
                 soup.select('article'))
        for card in cards[:count]:
            title   = _sel_text(card, 'h2, h3, [class*="title"]')
            company = _sel_text(card, '[class*="company"], [class*="client"]')
            loc_t   = _sel_text(card, '[class*="location"]') or 'Remote'
            salary  = _sel_text(card, '[class*="rate"], [class*="pay"]')
            a = card.select_one('a[href]')
            href = ''
            if a:
                href = a.get('href', '')
                if href.startswith('/'):
                    href = 'https://contra.com' + href
            if title:
                results.append(_job(title, company, loc_t, 'contract', salary, '', href, 'contra'))
    except Exception as e:
        print(f'[Contra] {e}')
    return results[:count]


# ── FLEXJOBS ──────────────────────────────────────────────────────────────────
def scrape_flexjobs(search_term, location='', count=20):
    """FlexJobs — every posting hand-verified, no scams. Scrapes public search."""
    results = []
    try:
        slug = re.sub(r'[^a-z0-9]+', '-', search_term.lower()).strip('-')
        urls = [
            f'https://www.flexjobs.com/search?search={quote(search_term)}&location=Anywhere',
            f'https://www.flexjobs.com/remote-jobs/keywords-{slug}',
        ]
        for url in urls:
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, 'lxml')
            cards = (soup.select('li[data-job-id]') or
                     soup.select('[class*="job-list-item"]') or
                     soup.select('article.job'))
            for card in cards[:count]:
                title   = _text(card, 'h2, h3, [class*="title"]') or _text(card, 'a')
                company = _text(card, '[class*="company"]')
                loc_t   = _text(card, '[class*="location"]') or 'Remote'
                salary  = _text(card, '[class*="salary"]')
                a       = card.select_one('a[href*="/job/"]')
                href    = ''
                if a:
                    href = a['href'] if a['href'].startswith('http') else 'https://www.flexjobs.com' + a['href']
                if title:
                    results.append(_job(title, company, loc_t, 'remote', salary, '', href, 'flexjobs'))
            if results: break
    except Exception as e:
        print(f'[FlexJobs] {e}')
    return results[:count]


# ── HN HIRING (Hacker News "Who is Hiring?") ──────────────────────────────────
def scrape_hn_hiring(search_term, location='', count=20):
    """
    Scrapes the monthly HN 'Ask HN: Who is Hiring?' thread via Algolia API.
    Companies post plain-text with direct engineer/CTO contact emails.
    This is a goldmine for remote roles with direct contact info.
    """
    results = []
    try:
        # Step 1: get the latest "Who is Hiring" thread ID via Algolia
        r = requests.get(
            'https://hn.algolia.com/api/v1/search',
            params={
                'query':       'Ask HN: Who is hiring?',
                'tags':        'ask_hn',
                'hitsPerPage': 1,
            },
            headers=HEADERS, timeout=12
        )
        if r.status_code != 200: return results
        hits = r.json().get('hits', [])
        if not hits: return results
        thread_id = hits[0]['objectID']

        # Step 2: get top-level comments from that thread (each = one job post)
        r2 = requests.get(
            'https://hn.algolia.com/api/v1/search',
            params={
                'tags':        f'comment,story_{thread_id}',
                'hitsPerPage': min(count * 3, 60),   # fetch extra to filter by keyword
            },
            headers=HEADERS, timeout=12
        )
        if r2.status_code != 200: return results

        kw = search_term.lower()
        for hit in r2.json().get('hits', []):
            text = hit.get('comment_text', '') or ''
            if not text: continue
            # Filter by keyword relevance
            if kw and kw not in text.lower(): continue

            soup = BeautifulSoup(text, 'lxml')
            plain = soup.get_text(' ').strip()

            # First line is typically: "Company | Role | Location | Remote/Onsite | Salary"
            lines  = [l.strip() for l in plain.split('\n') if l.strip()]
            first  = lines[0] if lines else plain[:120]
            parts  = [p.strip() for p in re.split(r'\s*\|\s*', first)]

            company = parts[0] if parts else ''
            title   = parts[1] if len(parts) > 1 else search_term
            loc_t   = parts[2] if len(parts) > 2 else ('Remote' if 'remote' in plain.lower() else location)
            salary  = next((p for p in parts if any(c in p for c in ['$','£','€','k','K','LPA'])), '')

            # HN job post URL
            hn_id  = hit.get('objectID', '')
            href   = f'https://news.ycombinator.com/item?id={hn_id}' if hn_id else ''

            # Description = full comment text (truncated)
            desc = plain[:800]

            if company or title:
                results.append(_job(title or 'Role', company, loc_t, 'fulltime', salary, desc, href, 'hn_hiring'))
            if len(results) >= count: break

    except Exception as e:
        print(f'[HN Hiring] {e}')
    return results[:count]


# ── TURING ────────────────────────────────────────────────────────────────────
def scrape_turing(search_term, location='', count=20):
    """Turing — remote developer jobs from US companies via JSON API."""
    results = []
    try:
        # Turing has an internal search API
        r = requests.get(
            'https://www.turing.com/jobs',
            params={'query': search_term},
            headers={**HEADERS,
                     'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
            timeout=15
        )
        if r.status_code != 200: return results

        soup = BeautifulSoup(r.text, 'lxml')

        # Try JSON embedded in __NEXT_DATA__
        script = soup.find('script', id='__NEXT_DATA__')
        if script:
            try:
                nd   = json.loads(script.string)
                jobs_data = (nd.get('props', {}).get('pageProps', {})
                              .get('jobs', nd.get('props', {}).get('pageProps', {})
                                   .get('initialJobs', [])))
                for j in jobs_data[:count]:
                    title   = j.get('title', '') or j.get('name', '')
                    company = j.get('company', '') or 'Via Turing'
                    loc_t   = j.get('location', 'Remote')
                    salary  = j.get('salary', '') or j.get('compensation', '')
                    jid     = j.get('id', '') or j.get('slug', '')
                    href    = f'https://www.turing.com/jobs/{jid}' if jid else ''
                    if title:
                        results.append(_job(title, company, loc_t, 'remote', str(salary), '', href, 'turing'))
                if results: return results[:count]
            except Exception as _e:
                _SM.record_failure('turing', _e)

        # Fallback: HTML card scraping
        cards = (soup.select('[class*="job-card"]') or
                 soup.select('[class*="JobCard"]') or
                 soup.select('article') or
                 soup.select('[class*="job-item"]'))
        for card in cards[:count]:
            title   = _text(card, 'h2, h3, [class*="title"], [class*="role"]')
            company = _text(card, '[class*="company"]') or 'Via Turing'
            loc_t   = _text(card, '[class*="location"]') or 'Remote'
            salary  = _text(card, '[class*="salary"], [class*="compensation"]')
            a       = card.select_one('a[href]')
            href    = ''
            if a:
                href = a['href'] if a['href'].startswith('http') else 'https://www.turing.com' + a['href']
            if title:
                results.append(_job(title, company, loc_t, 'remote', salary, '', href, 'turing'))

    except Exception as e:
        print(f'[Turing] {e}')
    return results[:count]


# ── UPWORK ────────────────────────────────────────────────────────────────────
def scrape_upwork(search_term, location='', count=20):
    """
    Upwork — largest freelance marketplace. Scrapes public job feed.
    Note: Upwork heavily protects their site. This uses their public
    search endpoint with browser-like headers.
    """
    results = []
    try:
        r = requests.get(
            'https://www.upwork.com/search/jobs/',
            params={
                'q':        search_term,
                'sort':     'recency',
                'per_page': min(count, 50),
            },
            headers={
                **HEADERS,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Referer': 'https://www.upwork.com/',
            },
            timeout=18
        )
        if r.status_code != 200: return results

        soup = BeautifulSoup(r.text, 'lxml')

        # Try JSON-LD or __NEXT_DATA__
        script = soup.find('script', id='__NEXT_DATA__')
        if script:
            try:
                nd   = json.loads(script.string)
                jobs_raw = (nd.get('props', {}).get('pageProps', {})
                              .get('results', {}).get('jobs', []))
                for j in jobs_raw[:count]:
                    title   = j.get('title', '')
                    company = j.get('client', {}).get('company', '') or 'Upwork Client'
                    loc_t   = j.get('client', {}).get('location', {}).get('country', 'Remote')
                    salary  = ''
                    budget  = j.get('budget') or j.get('hourlyRate')
                    if budget: salary = str(budget)
                    uid     = j.get('uid', '') or j.get('id', '')
                    href    = f'https://www.upwork.com/jobs/{uid}' if uid else ''
                    desc    = j.get('description', '')[:500]
                    if title:
                        results.append(_job(title, company, loc_t, 'contract', salary, desc, href, 'upwork'))
                if results: return results[:count]
            except Exception as _e:
                _SM.record_failure('upwork', _e)

        # HTML fallback
        cards = (soup.select('article[data-test="JobTile"]') or
                 soup.select('[class*="job-tile"]') or
                 soup.select('section[data-job-uid]') or
                 soup.select('[data-qa="job-tile"]'))
        for card in cards[:count]:
            title   = _text(card, 'h2, h3, [data-qa="job-title"], [class*="title"]')
            company = 'Upwork Client'
            loc_t   = _text(card, '[data-qa="client-location"], [class*="location"]') or 'Remote'
            salary  = _text(card, '[data-qa="salary"], [class*="budget"], [class*="rate"]')
            a       = card.select_one('a[href*="/jobs/"]')
            href    = ''
            if a:
                href = a['href'] if a['href'].startswith('http') else 'https://www.upwork.com' + a['href']
            if title:
                results.append(_job(title, company, loc_t, 'contract', salary, '', href, 'upwork'))

    except Exception as e:
        print(f'[Upwork] {e}')
    return results[:count]
