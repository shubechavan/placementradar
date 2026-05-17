# PlacementRadar v4 — Project Documentation

**Developer:** Shubham Chavan
**Type:** Full-Stack Web App + DevOps
**Live App:** https://placementradar.onrender.com
**GitHub:** https://github.com/shubechavan/placementradar
**Cost:** $0/month

---

## What Is This?

PlacementRadar is an AI-powered placement intelligence platform built for college placement committees. It automates the entire placement outreach pipeline — from finding jobs to discovering HR contacts to sending bulk emails.

**Before PlacementRadar:** Placement teams manually searched 5–6 job portals, copy-pasted data into spreadsheets, and spent hours finding HR contact info. This took 4–6 hours per search cycle.

**After PlacementRadar:** The entire process takes 5 minutes.

---

## The Pipeline

```
Search Query → Aggregate Jobs (6 boards) → Find HR Contacts → Email Outreach → Track Responses
```

---

## Results

| Metric | Value |
|--------|-------|
| Jobs Aggregated | 416+ |
| HR Emails Found | 95 |
| Job Boards Scraped | 6 |
| Monthly Infrastructure Cost | $0 |

---

## Tech Stack

### Backend
- **Python 3.11** + **Flask** — REST API server
- **Gunicorn** — production WSGI server (1 worker, 8 threads)
- **pymongo** — MongoDB driver

### Frontend
- **Vanilla JavaScript** — Single Page App, no framework
- **Chart.js** — dashboard analytics
- **Dark UI** — custom CSS

### Database
- **MongoDB Atlas** (free M0 tier)
- Collections: jobs, searches, outreach, settings, hr_contacts, scrape_jobs

### Scraping & Enrichment
- **python-jobspy** — multi-board job aggregation
- **BeautifulSoup4 + lxml** — HTML parsing
- **Google Serper API** — HR contact discovery via Google Search
- **Google libphonenumber** — phone number extraction and validation
- **email-validator** — RFC-compliant email validation
- **ThreadPoolExecutor** — parallel enrichment (4 jobs × 5 sources)

### DevOps
- **Docker** — containerization
- **GitHub Actions** — CI/CD pipeline + scraping compute
- **GitHub Container Registry (ghcr.io)** — Docker image storage
- **Render** — cloud hosting (free tier)

---

## System Architecture

```
User (Browser)
      │
      ▼
Render — Flask App (serves UI + API)
      │
      ├── GET /api/jobs          → MongoDB (read jobs)
      ├── GET /api/dashboard     → MongoDB (aggregation stats)
      ├── POST /api/scrape       → triggers GitHub Actions via API
      └── GET /api/status/{id}  → MongoDB (poll scrape progress)

GitHub Actions Runner (2 CPU, 7GB RAM)
      │
      ├── Scrapes 6 job boards in parallel
      ├── Finds HR contacts (email + phone)
      └── Saves results to MongoDB Atlas

MongoDB Atlas
      └── Single source of truth for all data
```

**Key design decision:** Render free tier has 0.1 CPU — too weak for scraping. The Flask app only serves the UI. All heavy scraping/enrichment is offloaded to GitHub Actions runners (2 CPU, 7GB RAM, free).

---

## Features

| Feature | Technology |
|---------|-----------|
| Multi-board job aggregation | python-jobspy, BeautifulSoup4 |
| HR email discovery | Serper API, company website scraping |
| Phone number extraction | Google libphonenumber |
| Email validation | email-validator (RFC-compliant) |
| Parallel contact enrichment | ThreadPoolExecutor (4 × 5 sources) |
| Bulk email outreach | Gmail SMTP, Jinja2 templates |
| Real-time progress tracking | Long-polling, MongoDB job state |
| Dashboard analytics | Chart.js, MongoDB aggregation pipeline |
| CSV export | pandas |
| Job status tracking | New → Contacted → Replied → Drive Scheduled |
| Custom job sites | User-defined CSS selectors |
| HR contact database | MongoDB-backed, auto-populated |

---

## DevOps Pipeline

Every `git push` to `main` triggers this pipeline automatically:

```
git push
   │
   ▼
Step 1 — Lint (10s)
   flake8 checks for syntax errors
   │
   ▼
Step 2 — Build Docker Image (38s)
   Builds image, pushes to ghcr.io
   │
   ▼
Step 3 — Deploy to Render (1m 36s)
   Triggers Render deploy hook
   Waits 90s for restart
   Health check → /api/dashboard
   │
   ▼
App is live at placementradar.onrender.com
```

### Infrastructure (All Free)

| Layer | Tool | Purpose |
|-------|------|---------|
| Source Control | GitHub | Code hosting |
| CI/CD | GitHub Actions | Lint → Build → Deploy |
| Container Registry | ghcr.io | Docker image storage |
| Hosting | Render | Flask app serving |
| Scraping Compute | GitHub Actions | 2 CPU / 7GB RAM workers |
| Database | MongoDB Atlas M0 | All persistent data |
| Keep-alive | GitHub Actions cron | Pings app every 10 min |
| **Total Cost** | | **$0/month** |

---

## Key Engineering Problems Solved

### 1. NaN JSON Serialization Bug
**Problem:** Python's `json` module writes `float('nan')` as bare `NaN` — invalid JSON. The browser's `response.json()` threw a `SyntaxError`, showing 0 jobs despite 416 in the database.

**Fix:** Added recursive `_sanitize_doc()` function in `database.py` that converts all NaN/Infinity floats to `None` before returning data from MongoDB.

---

### 2. MongoDB Error Code 40 — ConflictingOperationInUpdate
**Problem:** MongoDB 5.0+ rejects upserts where the same field appears in both `$set` and `$setOnInsert` operators simultaneously.

**Fix:** Removed contact fields (`emails`, `phone`, `hr_contact`) from `$setOnInsert`. Only truly immutable fields (UUID, `created_at`, `status`) go in `$setOnInsert`.

---

### 3. ThreadPoolExecutor Blocking Bug
**Problem:** Using `with ThreadPoolExecutor() as sub:` (context manager) calls `shutdown(wait=True)` on exit, blocking all outer threads for up to 110 seconds even after individual `.result(timeout=12)` calls returned.

**Fix:** Replaced `with sub:` with explicit `sub.shutdown(wait=False)` after collecting all futures.

---

### 4. Race Condition in Parallel Enrichment
**Problem:** 4 parallel workers all calling `save_jobs()` simultaneously caused `BulkWriteError` — multiple threads upserting the same document at the same instant.

**Fix:** Removed DB writes from progress callbacks entirely. Single atomic write at job completion only.

---

### 5. Render Free Tier CPU Limitation
**Problem:** 0.1 CPU on Render free tier makes scraping extremely slow (15–20 min per search).

**Fix:** Flask calls GitHub REST API (`workflow_dispatch`) to trigger a GitHub Actions runner (2 CPU, 7GB RAM). Render only serves the UI. Total scrape time reduced to 4–5 minutes.

---

## File Structure

```
placementradar/
├── app.py                  — Flask backend, all API routes
├── database.py             — MongoDB operations, sanitization
├── job_scraper.py          — Multi-board scraping logic
├── contact_finder.py       — HR email + phone discovery
├── email_sender.py         — Gmail SMTP outreach
├── enricher.py             — Company logo + data enrichment
├── hr_database_lookup.py   — CSV-based HR contact lookup
├── scrape_worker.py        — GitHub Actions scraping worker
├── hr_database.csv         — Seed HR contacts database
├── requirements.txt        — Python dependencies
├── Dockerfile              — Container build recipe
├── docker-compose.yml      — Local development setup
├── render.yaml             — Render deployment config
├── templates/
│   └── index.html          — Single Page App frontend
└── .github/
    └── workflows/
        ├── deploy.yml      — CI/CD: lint → build → deploy
        ├── scrape.yml      — On-demand scraping worker
        └── keepalive.yml   — Cron ping every 10 minutes
```

---

## How to Run Locally

```bash
# 1. Clone
git clone https://github.com/shubechavan/placementradar.git
cd placementradar

# 2. Set environment variable
set MONGO_URI=mongodb+srv://your_connection_string

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python app.py

# App available at http://localhost:5000
```

### With Docker

```bash
cp .env.example .env       # add MONGO_URI
docker compose up --build  # runs at http://localhost:5000
```

---

## Skills Demonstrated

**Backend:** Python, Flask, REST API Design, Multithreading, Concurrency, Web Scraping, BeautifulSoup4, pandas, Gmail SMTP

**Frontend:** JavaScript (ES6+), HTML5, CSS3, Chart.js, Single Page Application

**Database:** MongoDB, Aggregation Pipelines, Upsert patterns, Index design

**DevOps:** Docker, GitHub Actions, CI/CD, Cloud Deployment, Gunicorn, Linux, Secrets Management, Infrastructure as Code

---

## Interview Talking Points

**Q: Why MongoDB over SQL?**
Flexible schema for varied job data fields across different job boards. Built-in aggregation pipeline for dashboard stats. Atlas free tier for zero-cost cloud hosting.

**Q: How do you handle the free tier CPU limitation?**
Offloaded all heavy computation to GitHub Actions runners via `workflow_dispatch` API. Render only serves the lightweight UI — GitHub provides 2 CPU / 7GB RAM for free.

**Q: What was the hardest bug?**
NaN JSON serialization — Python wrote `float('nan')` as invalid JSON, breaking the frontend silently. Diagnosed by adding try-catch with visible error reporting in the UI, then traced to stale MongoDB documents with NaN values.

**Q: How does your CI/CD pipeline work?**
GitHub Actions: flake8 lint → Docker build → push to ghcr.io → Render deploy hook → 90s wait → health check on `/api/dashboard`. Full pipeline runs in under 3 minutes.

**Q: How did you design for zero cost?**
GitHub (free) for source + CI/CD + container registry. Render (free) for hosting. MongoDB Atlas M0 (free) for database. GitHub Actions cron job (free) as keepalive to prevent Render sleep. Total: $0/month.
