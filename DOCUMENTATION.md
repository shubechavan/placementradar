# PlacementRadar — Complete Project Documentation
### Built From Zero to Production — Full Technical Reference

**Developer:** Shubham Chavan  
**Live App:** https://placementradar.onrender.com  
**GitHub:** https://github.com/shubechavan/placementradar  
**Total Monthly Cost:** $0  

---

## Table of Contents

1. [What Is PlacementRadar](#1-what-is-placementradar)
2. [The Problem We Solved](#2-the-problem-we-solved)
3. [Tech Stack — With Justification](#3-tech-stack--with-justification)
4. [Project Structure](#4-project-structure)
5. [Database Design](#5-database-design)
6. [Backend — Flask API](#6-backend--flask-api)
7. [Job Scraping Engine](#7-job-scraping-engine)
8. [Contact Enrichment System](#8-contact-enrichment-system)
9. [UI Features](#9-ui-features)
10. [Email Outreach System](#10-email-outreach-system)
11. [DevOps — Docker](#11-devops--docker)
12. [DevOps — CI/CD Pipeline](#12-devops--cicd-pipeline)
13. [DevOps — GitHub Actions as Scraper](#13-devops--github-actions-as-scraper)
14. [Deployment — Render](#14-deployment--render)
15. [Complete User Journey](#15-complete-user-journey)
16. [CRM / Team Workspace](#16-crm--team-workspace)
17. [Bugs We Fixed](#17-bugs-we-fixed)
18. [System Architecture Diagram](#18-system-architecture-diagram)
19. [Results](#19-results)
20. [Skills Demonstrated](#20-skills-demonstrated)
21. [Interview Q&A](#21-interview-qa)

---

## 1. What Is PlacementRadar

PlacementRadar is a full-stack web application that automates the entire campus placement outreach pipeline — from discovering jobs across multiple portals to finding HR contact details to sending bulk emails — in one dashboard.

**Who uses it:** College placement committees and placement officers who need to contact 50-100 companies per week to arrange campus drives.

**Version history:**
- v1: Local Python script, SQLite database, manual run
- v2: Flask web UI added, CSV export
- v3: MongoDB migration, contact enrichment added
- v4: Full DevOps pipeline, GitHub Actions scraping offload, production deployment, grade badges, bulk copy
- v5: Team CRM — logins/roles, a separate auth database, activity logging, Kanban pipeline, and a leaderboard for the placement head

---

## 2. The Problem We Solved

### Before PlacementRadar
A placement officer's daily workflow:
```
9:00 AM  — Open Naukri, search "software engineer hiring"
9:15 AM  — Copy company names into Excel manually
9:30 AM  — Open LinkedIn, repeat
9:45 AM  — Open Internshala, repeat
10:00 AM — Google each company one by one to find HR email
11:00 AM — Type and send 10 emails manually
```
**Result: 4-6 hours of work. 10-15 companies contacted.**

### After PlacementRadar
```
9:00 AM  — Type "software engineer" + click Start Scraping
9:05 AM  — 40 jobs found across 6 portals simultaneously
9:06 AM  — HR phones + emails auto-discovered from 8 sources
9:07 AM  — Click "Copy All Phones" — paste into WhatsApp
9:08 AM  — Click "Bulk Email" — all companies emailed
```
**Result: 8 minutes. 40+ companies contacted.**

---

## 3. Tech Stack — With Justification

### Backend

| Tool | Version | Why This, Not Something Else |
|------|---------|------------------------------|
| Python | 3.11 | Best scraping/parsing ecosystem (requests, BS4, jobspy) |
| Flask | 3.0+ | Lightweight — no ORM overhead, no config ceremony. Django was overkill for a JSON API |
| Gunicorn | 21.2+ | Production WSGI — Flask's built-in dev server is single-threaded |
| pymongo | 4.6+ | Official MongoDB driver, supports bulk_write for efficient upserts |
| requests | 2.31+ | HTTP client for web scraping — simpler than httpx for sync use |
| BeautifulSoup4 | 4.12+ | HTML parsing — more forgiving than lxml alone for broken markup |
| lxml | 5.0+ | Fast C-based parser backend for BeautifulSoup |
| python-jobspy | 1.1+ | Handles multi-board scraping boilerplate (Naukri, Indeed, etc.) |
| phonenumbers | 8.13+ | Google's libphonenumber — handles every Indian number format |
| email-validator | 2.1+ | RFC-compliant email validation without DNS lookup |
| pandas | 2.0+ | CSV export with proper encoding (utf-8-sig for Excel compatibility) |

### Frontend

| Tool | Why |
|------|-----|
| Vanilla JavaScript ES6+ | No build step, no node_modules, no bundler. Single HTML file deploys instantly |
| Fetch API | Modern AJAX — native browser, no jQuery needed |
| CSS variables | Theme switching and consistent color system without a framework |
| Chart.js | Dashboard bar/pie charts — CDN loaded, zero install |

> **Why no React/Vue?** The app is server-rendered HTML with dynamic updates via AJAX. A JS framework would add build complexity without any benefit for this use case.

### Database

| Tool | Why |
|------|-----|
| MongoDB Atlas M0 | Free cloud database. Flexible schema handles varying fields across job boards. Aggregation pipeline for dashboard stats. Survives app restarts unlike SQLite |
| pymongo | Official driver. `bulk_write` with `UpdateOne` handles thousands of upserts efficiently |

> **Why not PostgreSQL?** Job data has inconsistent fields across sources (some jobs have salary, some don't; some have company_url, some don't). MongoDB's flexible schema handles this without nullable column gymnastics.

### DevOps

| Tool | Why |
|------|-----|
| Docker | Reproducible environment — eliminates "works on my machine" |
| docker-compose | Local development with env variables loaded from .env |
| GitHub Actions | Free CI/CD (2000 min/month) + free compute for scraping (2 CPU, 7GB RAM) |
| GitHub Container Registry (ghcr.io) | Free Docker image hosting |
| Render | Free web hosting with Docker support and auto-deploy |

---

## 4. Project Structure

```
placementradar/
│
├── app.py               # Flask app — jobs, outreach, and CRM API routes
├── auth.py              # CRM auth — login/session, role decorators, admin bootstrap
├── database.py          # MongoDB layer — jobs, contacts, users, activities, pipeline
├── job_scraper.py       # Multi-board scraping engine
├── contact_finder.py    # 8-source HR contact discovery
├── scrape_worker.py     # GitHub Actions entry point
├── email_sender.py      # Gmail SMTP email sending
├── hr_database_lookup.py# CSV-based HR contact seed data
│
├── hr_database.csv      # 130+ verified Indian HR contacts
├── requirements.txt     # Python dependencies
│
├── Dockerfile           # Container build recipe
├── .dockerignore        # Exclude .env, __pycache__, .git from image
├── docker-compose.yml   # Local dev setup
├── render.yaml          # Render deployment as code
├── .env.example         # Template (MONGO_URI placeholder)
├── .gitignore           # Exclude .env, logs, pycache
│
├── templates/
│   └── index.html       # Entire Single Page App (~1400 lines)
│
└── .github/workflows/
    ├── deploy.yml       # CI/CD: lint → Docker build → Render deploy
    ├── scrape.yml       # On-demand: triggered per search
    └── keepalive.yml    # Cron: pings app every 10 minutes
```

---

## 5. Database Design

### Why MongoDB Over SQLite

The app started with SQLite (v1). Problems:
- SQLite file lives on the server — Render's free tier uses ephemeral disk (file wiped on restart)
- Schema migrations needed for every new field
- No cloud access — GitHub Actions runner can't talk to a local SQLite file

MongoDB Atlas M0 solves all three.

### Collections

#### `jobs` — Job Listings
```json
{
  "_id": "uuid-string",
  "search_id": "uuid-string",
  "title": "Software Engineer",
  "company": "Tata Consultancy Services",
  "location": "Bangalore, Karnataka",
  "job_type": "Full Time",
  "salary": "6-8 LPA",
  "job_url": "https://naukri.com/job/...",
  "site": "naukri",
  "date_posted": "2026-05-14",
  "company_url": "https://tcs.com",
  "description": "...",
  "emails": "hr@tcs.com, recruitment@tcs.com",
  "phone": "+91 80 6724 5000",
  "hr_contact": "hr@tcs.com",
  "email_source": "Job Page",
  "status": "new",
  "notes": "",
  "score": 0,
  "created_at": "2026-05-14T09:00:00"
}
```

#### `searches` — Search History
```json
{
  "_id": "uuid-string",
  "search_term": "software engineer",
  "location": "Bangalore",
  "sites": ["naukri", "internshala", "google_jobs"],
  "job_count": 40,
  "created_at": "2026-05-14T09:00:00"
}
```

#### `outreach` — Emails Sent Log
```json
{
  "_id": "uuid-string",
  "job_id": "uuid-string",
  "company": "TCS",
  "hr_email": "hr@tcs.com",
  "subject": "Campus Hiring — KJSIM × TCS",
  "body": "Dear HR Team...",
  "sent_at": "2026-05-14T09:07:00",
  "status": "sent",
  "reply_received": 0
}
```

#### `settings` — App Configuration (key-value)
```json
{ "key": "gmail_email",        "value": "committee@college.edu" }
{ "key": "serper_api_key",     "value": "abc123..." }
{ "key": "outreach_template",  "value": "Dear HR Team..." }
{ "key": "college_name",       "value": "KJSIM" }
```

#### `hr_contacts` — Auto-Growing HR Contact Database
```json
{
  "company": "Infosys",
  "company_norm": "infosys",
  "hr_email": "careers@infosys.com",
  "designation": "HR",
  "city": "Bangalore",
  "source": "Auto:Job Page",
  "added_at": "2026-05-14T09:00:00"
}
```

#### `scrape_jobs` — GitHub Actions Job Tracking
```json
{
  "_id": "uuid-string",
  "search_id": "uuid-string",
  "status": "running",
  "progress": 55,
  "message": "Finding HR contacts: 12/40 — Infosys",
  "stats": { "total": 40, "with_email": 14, "with_phone": 22 },
  "count": 40,
  "created_at": "2026-05-14T09:00:00"
}
```

#### `users` — CRM Team Accounts *(stored in a separate `signup` database)*
```json
{
  "_id": "uuid-string",
  "name": "Noel sir",
  "email": "noel@college.edu",
  "password": "pbkdf2:sha256:...",   // Werkzeug hash — plaintext never stored
  "role": "admin",                    // "admin" or "member"
  "active": true,
  "created_at": "2026-08-04T21:00:00"
}
```

#### `activities` — CRM Outreach Log (who contacted which company)
```json
{
  "_id": "uuid-string",
  "job_id": "uuid-string",            // which company/lead
  "user_id": "uuid-string",           // which member did it
  "user_name": "Aryan Dalvi",
  "channels": ["call", "email"],      // multi-channel; one entry = one touch
  "channel": "call",                  // legacy scalar kept for old rows
  "outcome": "interested",            // contacted | interested | drive_scheduled | reply_received
  "remarks": "HR asked for the brochure, will call back Tuesday",
  "created_at": "2026-08-05T11:20:00"
}
```

Pipeline ownership and stage are stored on the `jobs` document itself (an `owner`
field for the assigned member and the existing `status` field, which the Kanban
stages map onto), so no extra collection is needed for the board.

### Why a Separate `signup` Database for Users

Login accounts live in their own database (`AUTH_MONGO_URI`, e.g. the same Atlas
cluster with the db name `signup`), while all job and CRM-activity data stays in the
main `jobs` database. The split is deliberate:

- **Isolation of identity from content** — password hashes and accounts are a
  different security domain than scraped public job listings. A mistake or export on
  the jobs side can't touch credentials, and vice-versa.
- **Falls back gracefully** — `AUTH_MONGO_URI` defaults to `MONGO_URI` when unset, so
  a simple single-database deployment still works with zero extra config.
- **Activities stay with jobs** — an activity references a `job_id`, so it lives in
  the same database as the jobs it points to; only the `users` collection moves.

```python
# database.py — one client per database, users routed to the auth db
AUTH_MONGO_URI = os.environ.get('AUTH_MONGO_URI', '') or MONGO_URI

def _get_auth_db():
    # reuses the main client when the URI is the same cluster; otherwise its own
    ...
    return _auth_client[_db_name_from_uri(AUTH_MONGO_URI)]   # e.g. "signup"
```

### Key MongoDB Patterns

**Upsert by URL — prevents duplicate jobs:**
```python
UpdateOne(
    {'job_url': url},
    {
        '$set': doc,                              # always update fields
        '$setOnInsert': {                         # only on first insert
            '_id': str(uuid.uuid4()),
            'status': 'new',
            'notes': '',
            'score': 0,
            'created_at': now,
        }
    },
    upsert=True
)
```

**Aggregation pipeline — jobs with contacts always sorted first:**
```python
pipeline = [
    {'$match': query},
    {'$addFields': {
        '_has_contact': {'$cond': [has_email_or_phone, 0, 1]}
    }},
    {'$sort': {'_has_contact': 1, 'created_at': -1}},
    {'$project': {'description': 0, '_has_contact': 0}},
]
```

**Indexes created:**
```python
db.jobs.create_index('job_url', sparse=True)
db.jobs.create_index([('company', 1), ('title', 1)])
db.jobs.create_index('created_at')
db.hr_contacts.create_index('hr_email', unique=True)
db.activities.create_index([('created_at', -1)])   # recent-first activity feed
db.activities.create_index('user_id')              # leaderboard grouping
db.activities.create_index('job_id')               # a lead's history
auth_db.users.create_index('email', unique=True)   # login key (on the signup db)
```

---

## 6. Backend — Flask API

### App Startup
```python
app = Flask(__name__)
init_db()   # connects MongoDB, creates indexes, seeds default settings
```

### All API Endpoints

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Serves the Single Page App |
| GET | `/api/dashboard` | Stats: total jobs, emails, by site, top companies |
| GET | `/api/settings` | Get all config (masks passwords) |
| POST | `/api/settings` | Save config |
| POST | `/api/settings/test-smtp` | Verify Gmail credentials |
| POST | `/api/scrape` | Start scrape (GitHub Actions or local thread) |
| GET | `/api/status/<job_id>` | Poll scrape progress |
| GET | `/api/results/<job_id>` | Get scrape results |
| GET | `/api/jobs` | All saved jobs (filterable by status/site/contact) |
| PATCH | `/api/jobs/<id>` | Update status/score/notes |
| POST | `/api/jobs/enrich` | Re-run contact finder on jobs without contacts |
| POST | `/api/outreach/send` | Send single email |
| POST | `/api/outreach/bulk` | Bulk email selected jobs |
| GET | `/api/outreach/log` | Email sent history |
| GET | `/api/outreach/followups` | Companies due for a follow-up (no reply after N days) |
| POST | `/api/outreach/followups/send` | Send follow-up template to selected companies |
| GET | `/api/hr-database` | Browse HR contacts |
| POST | `/api/hr-database/add` | Add contact manually |
| GET | `/api/custom-sites` | User-defined job sites |
| POST | `/api/custom-sites` | Add custom site |
| DELETE | `/api/custom-sites/<id>` | Remove custom site |
| POST | `/api/custom-sites/test` | Test a custom site config |
| GET | `/api/export` | Download CSV (board or search results) |
| POST | `/api/auth/login` | Log in with email + password, sets the session cookie |
| POST | `/api/auth/logout` | Clear the session |
| GET | `/api/auth/me` | Who am I (current user + role), for the UI to gate views |
| GET / POST | `/api/users` | Admin only — list / create committee members |
| PATCH / DELETE | `/api/users/<id>` | Admin only — activate/deactivate or remove a member |
| POST | `/api/activities` | Log an outreach touch (multi-channel, outcome, remarks) |
| GET | `/api/jobs/<id>/activities` | A single lead's activity history |
| GET | `/api/leaderboard` | Admin only — weighted per-member scores (week / all-time) |
| GET | `/api/pipeline` | Kanban board data grouped by stage |
| POST | `/api/jobs/<id>/stage` | Move a lead to a pipeline stage |
| POST | `/api/jobs/<id>/assign` | Admin only — assign a lead's owner |
| POST | `/api/jobs/<id>/remarks` | Save free-text remarks on a lead |

CRM routes are guarded by `auth.login_required` / `auth.admin_required`; the settings
write routes are admin-only, while the settings read route stays open but masks
secrets. In local dev (no `SECRET_KEY` set) the guards no-op so the app is usable
without logging in.

### Scrape Flow — Dual Path Design
```python
@app.route('/api/scrape', methods=['POST'])
def start_scrape():
    job_id    = str(uuid.uuid4())
    search_id = str(uuid.uuid4())

    # Path A: GitHub Actions (production — GH_PAT is set)
    if _trigger_github_scrape(job_id, search_id, data):
        create_scrape_job(job_id, search_id, data)   # write to MongoDB
        return jsonify({'job_id': job_id})            # respond in <1s

    # Path B: Local thread (dev — no GH_PAT)
    scrape_jobs[job_id] = {'status': 'running', 'progress': 0, ...}
    threading.Thread(target=run, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    return jsonify({'job_id': job_id})
```

### GitHub API Trigger
```python
def _trigger_github_scrape(job_id, search_id, data):
    token = os.environ.get('GH_PAT', '')
    if not token:
        return False

    resp = requests.post(
        'https://api.github.com/repos/shubechavan/placementradar'
        '/actions/workflows/scrape.yml/dispatches',
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json',
        },
        json={
            'ref': 'main',
            'inputs': {
                'job_id':      job_id,
                'params_json': json.dumps(params),
            }
        },
        timeout=10,
    )
    return resp.status_code == 204   # 204 = dispatch accepted
```

### Watchdog Thread (Local Dev Mode)
A second thread monitors the scrape and force-stops it if:
- Hard limit: 6 minutes total (prevents zombie threads)
- Stall detection: no progress for 120 seconds (handles hung HTTP requests)

Both cases save partial results to DB rather than returning an error.

---

## 7. Job Scraping Engine

### `job_scraper.py`

Aggregates jobs from multiple boards simultaneously using `python-jobspy`.

**Supported Sources (25+):**

*Indian Boards:* Naukri, Internshala, Shine, Foundit, Apna  
*International:* LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs  
*Remote:* Remotive, RemoteOK, WeWorkRemotely, Remote.co, FlexJobs  
*Tech/Startup:* Wellfound, YC Startups, BuiltIn, HN Hiring, Turing, Himalayas  
*Freelance:* Toptal, Upwork, Contra  
*User-defined:* any site configured with CSS selectors  

**Core flow:**
```python
def scrape_jobs(data, progress_callback=None):
    sites    = data.get('sites', [])
    term     = data.get('search_term', '')
    location = data.get('location', '')
    count    = data.get('results_wanted', 40)

    # Standard boards via python-jobspy
    results = scrape_standard_boards(term, location, sites, count)

    # User-defined custom sites via BeautifulSoup
    for site in data.get('custom_sites', []):
        if site.get('enabled'):
            results += scrape_custom_site(site, term, location, count=5)

    return deduplicate(results)
```

**Tech Only filter** — removes non-tech jobs (sales, BPO, HR, accounts):
```python
NON_TECH_RE = re.compile(
    r'\b(sales|bpo|call.?cent|telecall|tele.?market|field.?exec|'
    r'business.?dev|pre.?sales|loan|insurance|banking|finance|'
    r'accountant|clerk|receptionist|data.?entry)\b', re.I)
```

**Internshala badge cleaning** — Internshala embeds badge text inside company name:
```python
def _clean_company_name(el):
    clone = BeautifulSoup(str(el), 'lxml').find()
    for badge in clone.select('[class*="pill"],[class*="badge"],[class*="tag"]'):
        badge.decompose()   # remove badge from DOM clone
    text = clone.get_text(' ', strip=True)
    text = _IS_BADGE_RE.sub('', text)   # regex fallback
    return re.sub(r'\s{2,}', ' ', text).strip()
```

---

## 8. Contact Enrichment System

### `contact_finder.py` — 8 Parallel Sources

For each job, 8 sources are queried simultaneously using `ThreadPoolExecutor(max_workers=8)`. Each source has a timeout so slow ones don't block the others.

```
Job: "Software Engineer at Infosys, Bangalore"
              │
    ┌─────────┼─────────────────────┐
    │         │                     │
    ▼         ▼                     ▼
Source 1    Source 2          Source 3
Company     Zauba Corp        JustDial
Website     (govt registry)   (directory)
    │         │                     │
    ▼         ▼                     ▼
Source 4    Source 5          Source 6
IndiaMART   Sulekha           TradeIndia
(B2B dir)   (directory)       (B2B dir)
    │         │                     │
    ▼         ▼                     ▼
Source 7              Source 8
Photon Deep           Serper API
Crawl                 (Google Search)
              │
              ▼
    Merge all results
    Validate emails (email-validator)
    Validate phones (libphonenumber)
    Rank: HR emails first, generic last
    Auto-save new contacts to HR database
    Return best contacts
```

### Source Details

| Source | What It Does | Finds |
|--------|-------------|-------|
| Company Website | Scrapes /contact, /careers, /about, /team — extracts mailto: links and phone numbers | Email + Phone |
| Photon Deep Crawl | Follows internal links (up to 12 pages), prioritises contact/hr/team pages | Phone + Email |
| Zauba Corp | Indian government company registry — all registered Pvt Ltd and Ltd companies | Email + Phone |
| JustDial | Indian business directory — data-phone attributes | Phone |
| IndiaMART | B2B marketplace directory — tel: links and data attributes | Phone |
| Sulekha | Indian business directory (parallel to JustDial, separate database) | Phone |
| TradeIndia | B2B directory — follows company detail page for second-pass fetch | Phone |
| Google Serper | Runs 2 parallel Google searches: phone query + HR email query | Email + Phone |

### Parallel Execution Code
```python
sub = concurrent.futures.ThreadPoolExecutor(max_workers=8)
fetch_futs = {}

if base_url:
    fetch_futs['site']       = sub.submit(_scrape_company_site, base_url, domain)
    fetch_futs['photon']     = sub.submit(_photon_crawl, base_url, domain)
if company:
    fetch_futs['jd']         = sub.submit(_scrape_justdial, company, location)
    fetch_futs['im']         = sub.submit(_scrape_indiamart, company)
    fetch_futs['sulekha']    = sub.submit(_scrape_sulekha, company, location)
    fetch_futs['tradeindia'] = sub.submit(_scrape_tradeindia, company)
if needs_zauba:
    fetch_futs['zauba']      = sub.submit(_scrape_zauba, company)
if serper_key:
    fetch_futs['serp']       = sub.submit(_search_serper, company, location, key)

def _get(key, default):
    if key not in fetch_futs:
        return default
    timeout = 20 if key == 'photon' else 15 if key == 'serp' else 12
    try:
        return fetch_futs[key].result(timeout=timeout) or default
    except Exception:
        return default

# Collect all results
site_data       = _get('site',       {'emails': [], 'phones': []})
photon_data     = _get('photon',     {'emails': [], 'phones': []})
zauba_data      = _get('zauba',      {'emails': [], 'phones': []})
jd_data         = _get('jd',         {'phones': []})
im_data         = _get('im',         {'phones': []})
sulekha_data    = _get('sulekha',    {'phones': []})
tradeindia_data = _get('tradeindia', {'phones': []})
serp_data       = _get('serp',       {'phones': [], 'emails': []})

sub.shutdown(wait=False)   # release without blocking outer threads
```

### Phone Merge Priority
```python
# Higher confidence sources first
for src_phones in [
    jd_data.get('phones') or [],         # JustDial — highest confidence
    im_data.get('phones') or [],         # IndiaMART
    sulekha_data.get('phones') or [],    # Sulekha
    tradeindia_data.get('phones') or [], # TradeIndia
    serp_data.get('phones') or [],       # Google
    site_data.get('phones') or [],       # Company website
    photon_data.get('phones') or [],     # Deep crawl
]:
    for p in src_phones:
        if p not in phones:
            phones.append(p)
```

### Phone Validation (Google libphonenumber)
```python
import phonenumbers
from phonenumbers import PhoneNumberMatcher, PhoneNumberFormat

def _extract_phones_from_text(text):
    phones = []
    for match in PhoneNumberMatcher(text, "IN"):
        num = match.number
        if not phonenumbers.is_valid_number(num): continue
        if num.country_code != 91: continue
        e164   = phonenumbers.format_number(num, PhoneNumberFormat.E164)
        digits = e164[3:]   # strip +91
        if _valid_india_mobile(digits):
            phones.append(digits)
    return phones
```

Also handles: WhatsApp URLs (`wa.me/91XXXXXXXXXX`), `tel:` href links, JSON-LD schema.org telephone.

### Email Validation
```python
from email_validator import validate_email

JUNK_DOMAINS = {'noreply.com', 'example.com', 'naukri.com', 'linkedin.com', ...}

def _is_valid_email(email):
    dom = email.lower().split('@')[1]
    if dom in JUNK_DOMAINS: return False
    try:
        validate_email(email, check_deliverability=False)
        return True
    except: return False
```

### Outer Parallelism — 4 Jobs at Once
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(_process, jobs))
```
4 jobs enriched simultaneously, each firing 8 sources = up to 32 parallel HTTP requests.

### Auto-Growing HR Database
Every new HR email discovered is automatically saved to `hr_contacts` collection:
```python
if company and 'HR Database' not in source:
    for email in ranked[:2]:
        if not db_search(email):
            db_add(company=company, hr_email=email, source=f'Auto:{source}')
```

### Photon Deep Crawl (Inspired by s0md3v/Photon)
Unlike the fixed-path scraper (`/contact`, `/careers`), Photon follows discovered links:
```python
def _photon_crawl(base_url, domain, max_pages=12):
    hi_queue, lo_queue = [], [base_url]   # priority + normal queue
    visited = set()

    PRIORITY_KW = ('contact', 'about', 'team', 'hr', 'career', 'people')

    while len(visited) < max_pages:
        url = (hi_queue or lo_queue).pop(0)
        soup = fetch_and_parse(url)
        extract_phones_and_emails(soup)

        for link in soup.find_all('a', href=True):
            if any(k in link['href'].lower() for k in PRIORITY_KW):
                hi_queue.append(link)   # contact/about pages get priority
            else:
                lo_queue.append(link)

        if len(result['phones']) >= 2:
            break   # found enough, stop crawling
```

---

## 9. UI Features

### Single Page App Architecture
All frontend is in one file: `templates/index.html`. Views are toggled:
```javascript
function showView(name) {
    document.querySelectorAll('[id^="view-"]')
        .forEach(el => el.style.display = 'none');
    document.getElementById(`view-${name}`).style.display = 'block';
}
```

### Views

| View | What It Shows |
|------|--------------|
| Dashboard | Total jobs, email/phone counts, bar chart by source, top companies, recent searches |
| Job Search | Search form, source chips, progress bar, live results table |
| Job Board | All saved jobs, filters, status tracking, bulk email |
| Outreach | Email template editor, recipients, sent log |
| HR Database | Browse + search + add HR contacts |
| Settings | Gmail config, API keys, custom templates |
| Custom Sites | Add sites with CSS selectors for non-standard job boards |

### Grade Badge System

Every job row shows an automatic A/B/C/D/F grade badge based on contact quality:

```javascript
function contactGrade(r) {
    let score = 0;
    if (r.phone && r.phone.trim() && r.phone !== '—') score += 35;
    if (r.emails && r.emails.trim() && r.emails !== '—') score += 35;
    if (r.company_url && r.company_url.trim()) score += 15;
    if (r.emails && /hr|recruit|talent|hiring|placement/i.test(r.emails)) score += 15;

    if (score >= 80) return { g: 'A', cls: 'grade-A' };   // green
    if (score >= 55) return { g: 'B', cls: 'grade-B' };   // blue
    if (score >= 35) return { g: 'C', cls: 'grade-C' };   // yellow
    if (score >= 15) return { g: 'D', cls: 'grade-D' };   // orange
    return { g: 'F', cls: 'grade-F' };                    // red
}
```

Grade criteria:
- **A (80+):** Has phone + HR email (e.g., `hr@company.com`)
- **B (55-79):** Has phone + any email
- **C (35-54):** Has either phone or email + company URL
- **D (15-34):** Has only phone or only email
- **F (0-14):** No contact info found

This lets placement officers instantly see which leads are actionable without reading each row.

### Contacts Summary Bar

A green bar appears above results showing bulk contact totals:

```
📞 22 phones  |  📧 14 emails  |  [📋 Copy All Phones]  [📋 Copy All Emails]
```

```javascript
function copyContacts(src, type) {
    const arr = src === 's' ? filteredS : filteredB;
    const items = type === 'phone' ? _uniqPhones(arr) : _uniqEmails(arr);
    navigator.clipboard.writeText(items.join('\n'))
        .then(() => toast(`Copied ${items.length} ${type}s!`, 'success'));
}

function _uniqPhones(arr) {
    return [...new Set(
        arr.flatMap(r => r.phone ? r.phone.split(', ').map(p => p.trim()) : [])
    )];
}
```

Key behaviour:
- Updates live as filters change — filter to "Mumbai only", count updates immediately
- Deduplicates — same phone from multiple jobs counted once
- Falls back to `execCommand('copy')` for older browsers
- Works in both Search results view and Job Board view

### Date/Time Column ("Added")

Every job row shows when the job entered the database, rendered as a compact
date-over-time stack (e.g. `08 Apr` / `5:18 pm`) with a relative-time tooltip
(`today`, `yesterday`, `3d ago`):

```javascript
function fmtDT(iso){
  const dash='<span style="color:var(--dim)">—</span>';
  if(!iso) return dash;
  const d = new Date(iso);
  if(isNaN(d.getTime())) return dash;              // malformed → dash, never crashes
  const day = d.toLocaleDateString('en-IN',{day:'2-digit',month:'short'});
  const tm  = d.toLocaleTimeString('en-IN',{hour:'numeric',minute:'2-digit',hour12:true});
  const rel = Math.floor((Date.now()-d.getTime())/864e5);
  const ago = rel<=0?'today':rel===1?'yesterday':`${rel}d ago`;
  return `<div class="dt-d" title="${ago}">${day}</div><div class="dt-t">${tm}</div>`;
}
```

It binds to `created_at` (when the scraper *found* the job) rather than
`date_posted`, because `date_posted` is empty on the saved jobs — so the column
is honestly labelled **"Added"**, not "Posted", to avoid implying data the app
doesn't have. Empty, null, and unparseable timestamps all fall back to a dash
instead of rendering `Invalid Date`.

### Real-Time Progress Polling
```javascript
const poll = setInterval(async () => {
    const status = await fetch(`/api/status/${currentJobId}`).then(r => r.json());
    updateProgressBar(status.progress, status.message);

    if (status.status === 'done') {
        clearInterval(poll);
        const results = await fetch(`/api/results/${currentJobId}`).then(r => r.json());
        renderResultsTable(results.results);
    }
}, 1800);   // 1.8 second poll interval
```

---

## 10. Email Outreach System

### Gmail SMTP
```python
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def send_outreach_email(to_email, subject, body, settings):
    msg = MIMEMultipart()
    msg['From']    = settings['gmail_email']
    msg['To']      = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(settings['gmail_email'], settings['gmail_app_password'])
        server.sendmail(settings['gmail_email'], to_email, msg.as_string())
```

Requires a Gmail App Password (not the account password) — generated from Google Account → Security → 2FA → App Passwords.

### Template System
```
Dear HR Team,

I am writing from the Placement Committee of {{college_name}}.
We have highly qualified final-year students graduating in 2025.

We would love to explore campus hiring with {{company}}.

Best regards,
{{committee_name}}
{{college_name}}
{{committee_email}}
```

Placeholders replaced at send time:
```python
def _fill_template(template, job_data, settings):
    return template.replace('{{company}}', job_data.get('company', '')) \
                   .replace('{{college_name}}', settings.get('college_name', '')) \
                   .replace('{{committee_name}}', settings.get('committee_name', ''))
```

### Bulk Send Flow
```
Select jobs with HR emails (or all jobs with contacts)
        ↓
Load email template from settings
        ↓
Replace {{placeholders}} per company
        ↓
Send via Gmail SMTP (one connection per email)
        ↓
Log each to MongoDB outreach collection
        ↓
Update job.status → "contacted"
```

### Follow-up Engine

Most outreach replies come from follow-ups, not the first email — so the app
tracks which contacted companies never replied and queues them automatically.

**Eligibility rule** (computed from the `outreach` log, no new state needed):
- Job status is still `contacted` (a reply would have moved it to `replied`)
- Last email was sent ≥ N days ago (user picks 3/5/7 in the UI)
- Fewer than 2 follow-ups already sent (then it stops — no infinite nagging)

```python
def get_followups_due(days=3, max_followups=2):
    agg = db.outreach.aggregate([
        {'$match': {'job_id': {'$nin': ['', None]}, 'status': 'sent'}},
        {'$group': {'_id': '$job_id',
                    'last_sent': {'$max': '$sent_at'},
                    'sends':     {'$sum': 1}}},
    ])
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    due = {r['_id']: r for r in agg
           if r['last_sent'] <= cutoff and (r['sends'] - 1) < max_followups}
    # join against jobs still in status 'contacted'
```

The Outreach tab shows a **"Follow-ups Due"** card with the eligible companies
pre-checked; one click sends the `followup_template` to all of them (subject is
prefixed `Re:` so it threads with the original email in the recipient's inbox).
Each send is logged back to `outreach`, which is what makes the 2-follow-up cap
self-enforcing — the count is derived from the log itself.

---

## 11. DevOps — Docker

### Why Docker?
Without Docker: different Python versions, missing system libraries (lxml needs `libxml2-dev`), broken on server.  
With Docker: one image runs identically on developer laptop, CI, and Render.

### `Dockerfile` — Explained
```dockerfile
# Slim image = smaller = faster deploys
FROM python:3.11-slim

WORKDIR /app

# System libs required by lxml (HTML parser) — must be installed before pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*   # clean up to keep image small

# Copy requirements FIRST — Docker caches this layer
# If only app.py changed, this layer is reused → faster rebuild
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Never run as root in production — security best practice
RUN adduser --disabled-password --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# 1 worker (shared memory) + 8 threads (concurrency) + 360s timeout (long scrapes)
CMD ["gunicorn", "--bind", "0.0.0.0:5000",
     "--workers", "1", "--threads", "8",
     "--timeout", "360", "--keep-alive", "5",
     "--access-logfile", "-", "--error-logfile", "-",
     "app:app"]
```

### Why 1 Worker + 8 Threads?
Multiple Gunicorn workers = multiple separate Python processes = separate memory spaces.  
The `scrape_jobs` in-memory dictionary would not be shared between workers.  
A status poll to worker-2 would return 404 for a job created by worker-1.  

**Solution:** 1 worker (shared memory) + 8 threads (handles concurrent requests).

### `.dockerignore`
```
__pycache__/    # compiled Python — regenerated inside container
*.pyc
.env            # NEVER bake secrets into the image
*.log
.git/           # git history — large, not needed at runtime
.claude/        # IDE config
```

---

## 12. DevOps — CI/CD Pipeline

### `deploy.yml` — Three Jobs, Sequential

Every `git push` to `main` triggers automatically:

```
git push main
      │
      ▼
┌─── Job 1: Lint (10s) ────────────────────────┐
│  flake8 . --select=E9,F63,F7,F82             │
│  Only fails on: syntax errors, undefined names│
│  Not style/formatting                         │
└──────────────── ✓ ──────────────────────────┘
      │
      ▼
┌─── Job 2: Build Docker Image (38s) ──────────┐
│  docker login → ghcr.io                       │
│  docker build (with GHA cache)                │
│  docker push → ghcr.io/shubechavan/... :latest│
└──────────────── ✓ ──────────────────────────┘
      │
      ▼
┌─── Job 3: Deploy + Health Check (96s) ────────┐
│  curl "$RENDER_DEPLOY_HOOK"  → triggers deploy │
│  sleep 90  → wait for container restart        │
│  curl /api/dashboard → must return 200         │
└──────────────── ✓ ──────────────────────────┘
```

Total: ~3 minutes from push to live.

### `keepalive.yml` — Prevents Render Sleep
Render free tier sleeps after 15 minutes of no traffic:
```yaml
on:
  schedule:
    - cron: "*/10 * * * *"   # every 10 minutes, 24/7

jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping app
        run: |
          STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            "${{ secrets.RENDER_APP_URL }}/api/dashboard")
          echo "Ping returned HTTP $STATUS"
```

### GitHub Secrets

| Secret | What It Is | Used By |
|--------|-----------|---------|
| `MONGO_URI` | MongoDB Atlas connection string | deploy.yml, scrape.yml |
| `GH_PAT` | Personal Access Token (workflow scope) | Render app → trigger scrape.yml |
| `RENDER_DEPLOY_HOOK` | Render webhook URL | deploy.yml |
| `RENDER_APP_URL` | `https://placementradar.onrender.com` | deploy.yml, keepalive.yml |

> **Why `GH_PAT` not `GITHUB_PAT`?** GitHub reserves the `GITHUB_` prefix for its own built-in secrets. Any secret named `GITHUB_*` is silently rejected.

---

## 13. DevOps — GitHub Actions as Scraper

### The Problem
Render free tier: **0.1 CPU, 512MB RAM**  
Our scraper: 4 parallel jobs × 8 HTTP sources each = 32 concurrent requests + HTML parsing  
On Render free: a 40-job scrape takes 15-20 minutes and risks OOM kill.

### The Solution
Use GitHub Actions runners as free compute:
- **2 CPU, 7 GB RAM** — 20× more powerful than Render free
- **Free** — 2000 minutes/month included in every GitHub account
- **Clean architecture** — web server stays lightweight, compute is separate

### End-to-End Flow

**1.** User fills search form, clicks "Start Scraping"

**2.** Browser sends `POST /api/scrape` to Flask on Render

**3.** Flask calls GitHub API:
```python
requests.post(
    'https://api.github.com/repos/shubechavan/placementradar'
    '/actions/workflows/scrape.yml/dispatches',
    json={
        'ref': 'main',
        'inputs': {
            'job_id':      'abc-123',
            'params_json': '{"search_term":"python developer","location":"Mumbai"}'
        }
    }
)
```

**4.** Flask creates a `scrape_jobs` record in MongoDB with status `queued`, returns `{"job_id": "abc-123"}` in <1 second

**5.** `scrape.yml` workflow starts on a fresh GitHub Ubuntu runner:
```yaml
- run: python scrape_worker.py
  env:
    MONGO_URI:   ${{ secrets.MONGO_URI }}
    JOB_ID:      ${{ inputs.job_id }}
    PARAMS_JSON: ${{ inputs.params_json }}
```

**6.** `scrape_worker.py` runs on GitHub's machine:
```python
init_db()
update_scrape_job(job_id, status='running', progress=5)
results = do_scrape(params)         # 2 CPU, 7GB RAM — full speed
results = enrich_with_contacts(results)  # 8 sources × 4 parallel jobs
save_jobs(results, search_id)
update_scrape_job(job_id, status='done', progress=100, count=len(results))
```

**7.** Meanwhile, browser polls every 1.8 seconds:
`GET /api/status/abc-123` → Flask reads from MongoDB → returns progress

**8.** When status = `done`, browser fetches results:
`GET /api/results/abc-123` → Flask reads jobs from MongoDB → renders table

### Why This Design Is Smart
- Render is never blocked. It just reads/writes MongoDB.
- GitHub runner is ephemeral — starts fresh, does the work, disappears.
- MongoDB is the message bus between the two services.
- Works at $0 cost with no external queue service (SQS, Redis, etc.).

---

## 14. Deployment — Render

### `render.yaml` — Infrastructure as Code
```yaml
services:
  - type: web
    name: placementradar
    runtime: docker
    dockerfilePath: ./Dockerfile
    plan: free
    branch: main
    autoDeploy: true
    healthCheckPath: /api/dashboard
    envVars:
      - key: MONGO_URI
        sync: false       # must be set in dashboard — never in git
      - key: GH_PAT
        sync: false
```

### Free Tier Limitations and Fixes

| Limitation | Impact | Fix |
|-----------|--------|-----|
| Sleeps after 15 min | Users see 30s cold start | GitHub Actions keepalive ping every 10 min |
| 0.1 CPU | Scraping would time out | Scraping offloaded to GitHub Actions (2 CPU) |
| 512 MB RAM | Risk of OOM during scraping | Only Flask serves from Render — lightweight |
| Ephemeral disk | Files deleted on restart | MongoDB Atlas — data persists independently |

---

## 15. Complete User Journey

This is what happens when a placement officer runs a search end-to-end:

```
USER ACTION          SYSTEM ACTION
────────────         ─────────────────────────────────────────
Type "ML Engineer"   Browser builds search params JSON
Select Naukri +
Internshala +
Google Jobs
Click "Start"        POST /api/scrape → Flask
                     Flask calls GitHub API (workflow_dispatch)
                     GitHub accepts → 204 response
                     Flask creates scrape_jobs doc: status=queued
                     Returns {job_id: "abc-123"} in <1s
Progress bar         Browser polls /api/status/abc-123 every 1.8s
appears at 0%        MongoDB returns {status: queued, progress: 0}

~30 seconds later    GitHub runner boots, checks out code, installs deps
Progress → 5%        scrape_worker.py: update_scrape_job(status=running)
                     MongoDB updates progress → browser sees it

Progress 5→50%       do_scrape() runs: python-jobspy hits all 3 boards
"Scraping Naukri..." 40 jobs found, raw results saved to MongoDB

Progress → 52%       "Found 40 jobs. Finding HR contacts..."
                     enrich_with_contacts() starts: 4 jobs × 8 sources
"Finding contacts:
 12/40 — Infosys"    MongoDB updated every few seconds

Progress → 100%      All contacts found, final save_jobs() called
"Done! 40 jobs,      update_scrape_job(status=done, count=40)
 22 phones,
 14 emails"

Browser loads        GET /api/results/abc-123
results table        Flask reads from MongoDB → 40 rows returned
                     Table renders with grade badges (A/B/C/D/F)

Green summary bar    JS counts unique phones/emails across all rows
"22 phones, 14       "Copy All Phones" button appears
emails"

Click               22 phone numbers copied to clipboard as text
"Copy All Phones"    One number per line, ready to paste in WhatsApp

Click               Send email modal opens
"Email" button       Template pre-filled with company name
Click "Send"         POST /api/outreach/send → Flask → Gmail SMTP
                     Email sent, outreach logged to MongoDB
                     Job status → "contacted"
```

---

## 16. CRM / Team Workspace

Once more than one person uses the tool, "which companies have we already called?"
becomes the real problem. Phase 1 of the CRM answers it: team logins, per-member
activity logging, a Kanban pipeline, and a leaderboard for the placement head.

### Roles and Authentication

Two roles, enforced on the server with decorators in `auth.py`:

| Role | Can do |
|------|--------|
| **admin** (placement head) | Everything a member can, **plus** view the leaderboard, manage members, assign leads, and edit any lead |
| **member** | Log activity, move the pipeline, and edit their own or unassigned leads |

Passwords are stored only as Werkzeug PBKDF2 hashes — plaintext never touches the
database. Login issues a Flask **signed session cookie**; the cookie is hardened
(`HttpOnly`, `SameSite`, and `Secure` in production via `SESSION_COOKIE_SECURE`).

```python
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if auth_disabled():            # dev mode (no SECRET_KEY) → guards no-op
            return f(*a, **kw)
        if not current_user():
            return jsonify({'error': 'auth required'}), 401
        return f(*a, **kw)
    return wrapper

def admin_required(f):
    # as above, but also requires current_user()['role'] == 'admin'
    ...
```

**First-admin bootstrap:** on startup, if no users exist yet, the placement head's
account is seeded from `ADMIN_EMAIL` / `ADMIN_PASSWORD`. In production a missing or
too-short admin password prints a loud fatal banner rather than silently starting
open.

### Separate Login Database

User accounts live in their own database (`AUTH_MONGO_URI`, e.g. the same Atlas
cluster with db name `signup`), isolated from scraped job data. It falls back to
`MONGO_URI` when unset, so single-database setups still work. See
[Database Design](#5-database-design) for the rationale and the `_get_auth_db()` code.

### Activity Logging — One Touch, Many Channels

A committee member often calls **and** emails a company in the same sitting. The log
models this as a `channels` array so one entry can credit multiple channels while
still counting as a **single touch**:

```json
{ "job_id": "...", "user_id": "...", "channels": ["call", "email"],
  "outcome": "interested", "remarks": "will revert Tuesday" }
```

The leaderboard aggregation normalises legacy single-channel rows into the same array
shape so both old and new records score identically:

```python
{'$addFields': {'_ch': {'$ifNull': ['$channels', ['$channel']]}}},
# a channel scores if PRESENT in _ch; `total` still increments once per entry
'calls':  {'$sum': {'$cond': [{'$in': ['call',  '$_ch']}, 1, 0]}},
'emails': {'$sum': {'$cond': [{'$in': ['email', '$_ch']}, 1, 0]}},
```

### Leaderboard — Weighted Scoring

Counting raw calls would reward noise. The score weights **outcomes** above
**effort**, so a member who books a drive outranks one who made 20 low-value calls:

```python
LEADERBOARD_WEIGHTS = {
    'reply_received':  5,   # a company replied
    'drive_scheduled': 5,   # a campus drive booked
    'interested':      3,   # company expressed interest
    'call':            1,   # per logged call
    'email':           1,   # per logged email
    'whatsapp':        1,   # per logged WhatsApp
    'company':         2,   # per distinct company touched (breadth)
}
```

The admin sees a **weekly** and an **all-time** view. Active members with zero
activity are still listed (score 0) so the head sees everyone, not just the active
ones — read from the auth database so the roster is complete.

### Kanban Pipeline

Leads move across six stages; dropping a card writes the mapped `status` back onto the
job, so the pipeline and the job board stay in sync:

| Stage (column) | Job status written |
|----------------|--------------------|
| To Contact | `new` |
| Contacted | `contacted` |
| Interested | `replied` |
| Drive Scheduled | `drive_scheduled` |
| Placed (won) | `placed` |
| Rejected (lost) | `rejected` |

```python
STAGE_TO_STATUS = {
    'to_contact': 'new', 'contacted': 'contacted', 'interested': 'replied',
    'drive_scheduled': 'drive_scheduled',
    'closed_won': 'placed', 'closed_lost': 'rejected',
}
```

### Ownership Rule

A lead can be assigned to a member. The edit check keeps members in their lane while
letting the head act on anything:

```python
def _can_edit_job(job_id):
    user = current_user()
    if user['role'] == 'admin':
        return True                     # admin edits anything
    owner = get_job_owner(job_id)
    return owner in (None, user['id'])  # member: own or unassigned only
```

---

## 17. Bugs We Fixed

### Bug 1: Job Board Showed "0 of 0 Jobs" (416 Jobs in Database)

**Symptom:** Job Board rendered empty despite jobs clearly in MongoDB.

**Root Cause:** Some MongoDB documents had `float('nan')` in fields like `notes`. Python's `json` module serializes `float('nan')` as bare `NaN` — which is **invalid JSON** (JSON spec doesn't include NaN). The browser's `response.json()` threw `SyntaxError: Unexpected token 'N'` before any data was parsed.

**Discovery Process:**
1. API returned HTTP 200 — so it wasn't a server error
2. Added `try-catch` to `loadBoard()` with `alert(err)` — revealed the SyntaxError
3. `console.log(await resp.text())` showed `"notes":NaN` in the response body

**Fix:**
```python
def _sanitize_doc(obj):
    """Recursively replace NaN/Infinity floats with None."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_doc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_doc(v) for v in obj]
    return obj

# Applied to every document returned from MongoDB
out.append(_sanitize_doc(r))
```

Also added a one-time cleanup in `init_db()` to fix existing documents.

---

### Bug 2: MongoDB Error Code 40 — ConflictingOperationInUpdate

**Symptom:** `BulkWriteError: Updating the path 'phone' would create a conflict at 'phone'`

**Root Cause:** MongoDB 5.0+ rejects upsert operations where the same field appears in both `$set` and `$setOnInsert`. Our original code had `phone` and `emails` in `$setOnInsert` as empty strings, and also conditionally in `$set` when contacts were found.

**Fix:**
```python
# BROKEN: phone appears in both operators
'$setOnInsert': {'_id': uuid, 'status': 'new', 'emails': '', 'phone': ''}

# FIXED: contact fields ONLY in $set, never in $setOnInsert
'$setOnInsert': {'_id': uuid, 'status': 'new', 'notes': '', 'score': 0}
# $set gets phone/emails only when actually found (conditional)
```

---

### Bug 3: ThreadPoolExecutor Blocked All Threads for 110 Seconds

**Symptom:** Contact enrichment for 40 jobs took 110+ seconds instead of expected 20s.

**Root Cause:** Used `with ThreadPoolExecutor() as sub:` (context manager). The `with` block calls `shutdown(wait=True)` on exit — which waits for ALL submitted futures, including ones that already returned via `.result(timeout=12)`. Timed-out futures still held running threads.

**Fix:**
```python
# BROKEN: context manager blocks for all running futures
with ThreadPoolExecutor(max_workers=5) as sub:
    futs = {sub.submit(task): task for task in sources}
    results = [f.result(timeout=12) for f in futs]
    # shutdown(wait=True) called here — waits for timed-out threads

# FIXED: explicit non-blocking shutdown
sub = ThreadPoolExecutor(max_workers=5)
futs = {sub.submit(task): task for task in sources}
results = [_get(k, default) for k in futs]  # each with timeout
sub.shutdown(wait=False)   # release without waiting
```

---

### Bug 4: Race Condition — BulkWriteError During Enrichment

**Symptom:** Intermittent `BulkWriteError: E11000 duplicate key` during contact enrichment.

**Root Cause:** 4 outer ThreadPoolExecutor workers each called `save_jobs()` from their progress callbacks simultaneously — trying to upsert the same documents at the same time.

**Fix:** Never write to DB from progress callbacks. Write once, after all workers complete:
```python
# BROKEN: 4 threads calling save_jobs concurrently
def _enrich_cb(p, m):
    scrape_jobs[job_id]['results'] = results
    save_jobs(results, search_id)   # ← race condition

# FIXED: callback only updates in-memory state
def _enrich_cb(p, m):
    scrape_jobs[job_id].update({'progress': p, 'message': m, 'results': results})
# Single DB write after all enrichment completes:
save_jobs(results, search_id)   # ← called once, safely
```

---

### Bug 5: UnicodeEncodeError — Emoji Crash on Windows

**Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f50d'`

**Root Cause:** Windows terminal uses `cp1252` encoding. Emoji characters (🔍, 📄) in print statements crashed the server on startup.

**Fix:**
```python
# Replace emoji in startup messages with ASCII
print('\n[*] PlacementRadar v4  ->  http://localhost:5000\n')

# Encode tracebacks safely before printing
print(f'[ERROR] {tb}'.encode('ascii', errors='replace').decode('ascii'))
```

---

### Bug 6: Internshala Company Names Contained Badge Text

**Symptom:** Company shown as `"TCS Actively hiring Verified"` instead of `"TCS"`

**Root Cause:** Internshala's HTML puts badge text (`"Actively hiring"`, `"Verified"`) as child elements of the company name container. `get_text()` concatenated all child text.

**Fix:** Clone the element, remove badge children, then extract text:
```python
def _clean_company_name(el):
    clone = BeautifulSoup(str(el), 'lxml').find()
    for badge in clone.select('[class*="pill"],[class*="badge"],[class*="tag"]'):
        badge.decompose()
    text = clone.get_text(' ', strip=True)
    text = _IS_BADGE_RE.sub('', text)
    return re.sub(r'\s{2,}', ' ', text).strip()
```

---

### Bug 7: Job Board Double-Load Race Condition

**Symptom:** Board showed "0 of X jobs" because second load reset results before first load finished.

**Root Cause:** `loadBoard()` called on page init AND on tab click. Second call's response sometimes arrived before first, resetting `boardResults = []`.

**Fix:**
```javascript
async function loadBoard() {
    const fresh = d.jobs || [];
    if (!fresh.length && boardResults.length) return;  // don't overwrite good data
    boardResults = fresh;
}

// Tab click: only load if board is empty
onclick="showView('board'); if(!boardResults.length) loadBoard()"
```

---

### Bug 8: CI/CD — Render Deploy Hook Returned Exit Code 22

**Symptom:** GitHub Actions deploy job failed with `curl: (22) The requested URL returned error: 400`.

**Root Cause:** Used `curl -f -X POST` with the Render deploy hook. Render deploy hooks use `GET`, not `POST`. The `-f` flag makes curl fail on HTTP error codes.

**Fix:**
```bash
# BROKEN: POST + -f flag
curl -f -X POST "${{ secrets.RENDER_DEPLOY_HOOK }}"

# FIXED: GET request, check HTTP status manually
RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" "${{ secrets.RENDER_DEPLOY_HOOK }}")
HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d: -f2)
test "$HTTP_STATUS" = "200" -o "$HTTP_STATUS" = "201" -o "$HTTP_STATUS" = "202"
```

---

### Bug 9: `GITHUB_PAT` Secret Silently Rejected

**Symptom:** Scrape trigger always fell back to local thread mode even after adding the secret.

**Root Cause:** GitHub silently rejects any secret named with the `GITHUB_` prefix — it's reserved for GitHub's built-in secrets.

**Fix:** Rename to `GH_PAT` everywhere — in GitHub Secrets, Render environment variables, and `os.environ.get('GH_PAT', '')` in code.

---

### Bug 10: Connection Layer Ignored the Database Name in the URI

**Symptom:** Pointing `MONGO_URI` at a different database name (e.g. `.../jobs_test`
for an isolated test) still read and wrote the real `jobs` database. Test isolation
wasn't actually isolated.

**Root Cause:** `_get_db()` hardcoded `_client['jobs']` — it selected the database by
a literal name and **ignored whatever database was named in the URI**. Every
connection resolved to `jobs` no matter what the URI said, so any operation intended
for a throwaway database silently hit production data.

**Fix:** Parse and honour the database name from the URI, defaulting to `jobs` only
when the URI omits one. A plain regex is used rather than `pymongo.uri_parser.parse_uri`
because the latter performs a live DNS lookup for `mongodb+srv://` URIs, which fails
offline and in tests:

```python
def _db_name_from_uri(uri):
    m = re.search(r'^mongodb(?:\+srv)?://[^/]+/([^/?]+)', uri.strip(), re.I)
    return m.group(1) if (m and m.group(1)) else _DEFAULT_DB_NAME

_db = _client[_db_name_from_uri(MONGO_URI)]     # honour the URI's db name
print(f'[MongoDB] Connected OK  (database: {_db.name})')   # prints the real target
```

**Lesson:** a connection helper must resolve to exactly the target the caller asked
for, and it should announce that target on connect. The startup line now prints the
resolved database name, so it is always obvious which database the app is talking to.
This also unlocked the separate `signup` auth database, which relies on the URL's db
name being honoured per connection.

---

## 18. System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    PRODUCTION ARCHITECTURE                        │
│                                                                   │
│  Browser (User)                                                   │
│       │                                                           │
│       │  HTTPS                                                    │
│       ▼                                                           │
│  ┌────────────────────────────────────┐                          │
│  │  RENDER (Free Tier)                │                          │
│  │  Docker Container                  │                          │
│  │  Flask + Gunicorn                  │                          │
│  │  1 worker, 8 threads               │                          │
│  │  512MB RAM, 0.1 CPU                │                          │
│  │                                    │                          │
│  │  Serves: UI + all API endpoints    │                          │
│  │  Does NOT run scraping             │                          │
│  └──────────┬─────────────────────────┘                          │
│             │ workflow_dispatch API call                          │
│             │ (when user runs a search)                           │
│    ┌────────┼────────┐                                           │
│    │                 │                                           │
│    ▼                 ▼                                           │
│  ┌──────────┐  ┌──────────────────────────────────────┐        │
│  │ MongoDB  │  │  GITHUB ACTIONS                        │        │
│  │ Atlas M0 │  │  ubuntu-latest runner                  │        │
│  │ (Free)   │  │  2 CPU, 7 GB RAM, free                 │        │
│  │          │◀─│                                        │        │
│  │ jobs     │  │  1. Checks out code                    │        │
│  │ searches │  │  2. pip install -r requirements.txt    │        │
│  │ outreach │  │  3. python scrape_worker.py            │        │
│  │ settings │  │     → scrapes 6+ job boards            │        │
│  │ hr_cont. │  │     → finds HR contacts (8 sources)   │        │
│  │ scrape_  │  │     → saves to MongoDB                 │        │
│  │ jobs     │  │     → updates progress in MongoDB      │        │
│  └──────────┘  └──────────────────────────────────────┘        │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  GITHUB (Free)                                            │   │
│  │  ├── Source code (git)                                    │   │
│  │  ├── deploy.yml  — lint → build → deploy on every push   │   │
│  │  ├── scrape.yml  — triggered per search (workflow_disp.) │   │
│  │  ├── keepalive.yml — ping every 10 minutes               │   │
│  │  └── ghcr.io — Docker image registry                     │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**CRM data plane:** the same MongoDB Atlas cluster also holds the CRM `activities`
collection (alongside `jobs`), while the `users`/login accounts live in a separate
`signup` database on that cluster (`AUTH_MONGO_URI`) — identity isolated from content.

---

## 19. Results

| Metric | Value |
|--------|-------|
| Jobs in database | 540+ |
| HR emails discovered | 95+ |
| Phone numbers found | 80+ |
| Job boards integrated | 25+ |
| Contact enrichment sources | 8 parallel |
| API endpoints | 35+ (incl. CRM + auth) |
| CRM: roles / databases | 2 roles (admin, member) / 2 databases (jobs, signup) |
| CRM: pipeline stages | 6 (To Contact → Placed / Rejected) |
| GitHub Actions workflows | 3 |
| Monthly infrastructure cost | $0 |
| CI/CD pipeline time | ~3 minutes |
| Scrape time (GitHub Actions) | 4-6 minutes |
| Scrape time (Render free — before fix) | 15-20 minutes |

---

## 20. Skills Demonstrated

### Programming
- Python 3.11 — ThreadPoolExecutor, context managers, generators, regex
- JavaScript ES6+ — async/await, Fetch API, Clipboard API, DOM manipulation
- HTML5 + CSS3 — CSS variables, dark theme, responsive grid, animations
- MongoDB aggregation pipeline thinking (GROUP BY equivalent)
- Regex — phone extraction, company name cleaning, email parsing

### Backend Engineering
- REST API design — correct HTTP methods, status codes, error handling
- Authentication & authorization — signed session cookies, PBKDF2 password hashing, role-based access control (admin/member) via decorators
- Multithreading — race condition identification and resolution
- Web scraping — BeautifulSoup4, lxml, requests, anti-blocking headers, timeouts
- Email — SMTP, MIME multipart, Gmail App Password authentication
- Data validation — Google libphonenumber, RFC email validator
- NaN/Infinity sanitization in serialization pipeline
- Multi-database design — isolating auth/identity from content, with graceful single-DB fallback

### Database
- MongoDB schema design — flexible document model for heterogeneous data
- Indexes — compound, sparse, unique for performance
- Aggregation pipeline — computed sort fields, conditional expressions
- Upsert patterns — `$setOnInsert` vs `$set` distinction (and the MongoDB 5.0 conflict bug)
- NaN sanitization between Python and JSON serialization

### DevOps
- Docker — multi-stage thinking, layer caching, non-root user, .dockerignore
- GitHub Actions — CI/CD workflows, scheduled cron, `workflow_dispatch` API
- Cloud deployment — Render, environment variables, health checks, deploy hooks
- Secrets management — never in code, GitHub Secrets + Render env vars
- Infrastructure as Code — render.yaml, docker-compose.yml
- Container registry — ghcr.io image tagging and rotation

### System Design
- Microservices split — web server (Render) vs compute worker (GitHub Actions)
- Resource optimization — offloaded CPU-heavy work to free, powerful runners
- Fault tolerance — fallback from GitHub Actions to local thread on missing PAT
- Real-time updates — polling pattern with MongoDB as message bus
- Cost architecture — $0/month using genuine free tiers, no trials

---

## 21. Interview Q&A

**Q: Walk me through the architecture in 60 seconds.**  
> Render hosts a Flask web app in Docker — it only serves the UI and API. When a user searches for jobs, Flask calls GitHub's API to trigger a `workflow_dispatch` event on `scrape.yml`. A GitHub Actions runner with 2 CPU and 7GB RAM does the heavy scraping and contact enrichment, saving everything to MongoDB Atlas. The browser polls `/api/status` every 1.8 seconds — Flask reads job progress from MongoDB. When done, the browser fetches results and renders them with grade badges. Total cost: $0/month.

---

**Q: Why offload scraping to GitHub Actions instead of running it on Render?**  
> Render's free tier has 0.1 CPU — which is effectively a shared fraction of one core. Running 8 parallel HTTP requests per job × 4 jobs simultaneously would peg the CPU, take 15-20 minutes, and risk OOM kill. GitHub Actions runners have 2 full CPU cores and 7GB RAM, are free (2000 min/month), and spin up in ~30 seconds. The architecture is also cleaner — web server is stateless, compute is separate. MongoDB acts as the message bus between them — no need for Redis or SQS.

---

**Q: What was the hardest bug to fix?**  
> The NaN JSON bug. The board showed "0 of 0 jobs" despite 416 in the database. The API returned 200, JavaScript executed without errors, but nothing rendered. I added `try-catch` to the frontend render function, which surfaced `SyntaxError: Unexpected token 'N'`. Then `console.log(await resp.text())` showed `"notes":NaN` in the raw response. Python's `json` module writes `float('nan')` as bare `NaN` — which is invalid JSON per spec. Fixed with a recursive sanitizer applied to every document before serialization.

---

**Q: How do you handle concurrent requests when scraping is running?**  
> Single Gunicorn worker, 8 threads. Multiple workers would have separate process memory — the `scrape_jobs` in-memory dictionary wouldn't be shared, so status polling from a different worker would return 404. One worker preserves shared state. 8 threads gives concurrency for the API calls while the scrape runs in background. For the GitHub Actions path, scraping is fully external so Render is never blocked at all.

---

**Q: What's the MongoDB indexing strategy?**  
> `job_url` has a sparse index — it's the upsert key, so lookups must be fast. `(company, title)` compound index for deduplication checks. `created_at` descending index for the default sort. `hr_email` unique index on the contacts collection — prevents the auto-save loop from creating duplicates. The `description` field is explicitly excluded from all query projections because it's large text and never displayed in the table.

---

**Q: How does the grade badge work?**  
> It's computed entirely in JavaScript from data already in the row — no extra API call. The scoring: phone found = +35, email found = +35, company URL present = +15, email prefix contains "hr/recruit/talent" = +15 bonus. A is 80+, B is 55-79, C is 35-54, D is 15-34, F is 0-14. A placement officer can instantly see which leads have full contact info (green A) vs nothing (red F) without reading each row.

---

**Q: Explain the bulk copy feature.**  
> When search results or the board loads, a JavaScript function collects all phone numbers from the current filtered view, deduplicates them into a `Set`, and joins with newlines. The Clipboard API (`navigator.clipboard.writeText`) puts them all on the clipboard in one click. Falls back to `execCommand('copy')` for older browsers. The count updates live as filters change — so if you filter to "Mumbai only" and click copy, you get only Mumbai phones. A placement officer can paste directly into WhatsApp or a phone dialer.

---

**Q: How do you prevent secrets from leaking into git?**  
> Secrets live in three places: GitHub repository Secrets (for CI/CD and scrape workflows), Render environment variables (for the running container), and `.env` file locally (never committed — excluded by `.gitignore`). The `.dockerignore` excludes `.env` from the Docker image. `render.yaml` has `sync: false` for secret variables — they must be set manually in the Render dashboard, never in the YAML file. `MONGO_URI` has never appeared in any commit.

---

**Q: Why didn't you use React or another frontend framework?**  
> This is a single HTML file with about 1400 lines. It loads instantly with no build step, no `node_modules`, no bundler, no npm. Deploying is just copying the file. Vanilla JavaScript with Fetch API handles async perfectly well for this scale. React would add significant build complexity — a Webpack config, a Node.js install step in Docker, and a separate build stage — for zero functional benefit at this feature set.

---

**Q: How does the CI/CD pipeline know the deployment succeeded?**  
> After triggering the Render deploy hook, the pipeline sleeps 90 seconds (Render's container restart time), then hits `/api/dashboard` with curl. If it gets HTTP 200, the deploy succeeded and the job passes. If it gets anything else (503, 500, timeout), the job fails and the team is notified. This is a basic smoke test — the endpoint connects to MongoDB and returns stats, so a 200 means the full stack is functional.

---

**Q: What would you change if you were scaling this to 100 users?**  
> First, move from the in-memory `scrape_jobs` dict to MongoDB exclusively — this makes the app stateless and allows multiple Render instances. Second, add a proper job queue (Redis + Celery or BullMQ) instead of the GitHub Actions trigger — lower latency, better error handling, retry logic. Third, add rate limiting on the scrape endpoint so users can't trigger unlimited GitHub Actions jobs. Fourth, move to a paid Render tier for always-on hosting. The database layer and scraping engine wouldn't need changes.

---

**Q: How does the follow-up engine decide who to email?**
> It derives everything from the outreach log — no extra state to keep in sync. An aggregation groups sent emails by job, taking the latest send date and total count. A company is "due" when its job status is still `contacted` (a reply changes the status, which removes it), the last email is older than the threshold, and fewer than 2 follow-ups have gone out. Because each follow-up is logged to the same collection, the cap enforces itself: the third send makes the count exceed the limit and the company drops out of the queue. The subject line is prefixed "Re:" so Gmail threads it under the original email — recipients see it as a continuation, not a new cold email.

---

**Q: How did you handle the GITHUB_PAT naming issue?**  
> GitHub silently rejects any repository secret with the `GITHUB_` prefix — it's reserved for GitHub's own built-in secrets like `GITHUB_TOKEN`. If you create a secret named `GITHUB_PAT`, it appears saved but the value is never injected into the workflow environment. The fix is to name it `GH_PAT` and update both the Render environment variable and the `os.environ.get()` call in `app.py`. Took about 30 minutes to debug because GitHub gives no error message — the secret just appears empty.

---

**Q: Why store the CRM user accounts in a separate database from the jobs?**
> Identity and content are different security domains. Password hashes and login
> accounts don't belong in the same database as scraped public job listings — a bad
> export, migration, or cleanup on the jobs side should never be able to touch
> credentials. So users live in their own `signup` database via `AUTH_MONGO_URI`,
> while activities — which reference a `job_id` — stay next to the jobs they point to.
> I made it degrade gracefully: `AUTH_MONGO_URI` defaults to `MONGO_URI` when unset, so
> a simple deployment still runs on one database with zero extra config. When both URIs
> point at the same cluster, the code reuses one connection pool and just selects the
> other db name, so the isolation costs nothing at runtime.

---

**Q: How does the leaderboard avoid rewarding busywork?**
> Raw call counts would incentivise noise, so the score weights outcomes over effort:
> a reply received or a campus drive booked is worth 5, an "interested" is 3, and each
> individual call/email/WhatsApp is worth 1, plus 2 per distinct company touched to
> reward breadth. One logged activity can span multiple channels (call *and* email in
> one sitting) but still counts as a single touch — the channels are stored as an array
> and the aggregation credits each channel while only incrementing the touch count once.
> The head gets a weekly and an all-time view, and members with zero activity still
> appear at 0 so everyone is visible, not just the active ones.

---

*PlacementRadar — Built by Shubham Chavan*  
*Live: https://placementradar.onrender.com*  
*GitHub: https://github.com/shubechavan/placementradar*
