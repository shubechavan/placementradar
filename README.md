# 📡 PlacementRadar

A full-stack job-intelligence and outreach tool for college placement cells.
It scrapes jobs across 25+ boards, auto-discovers HR **phone numbers and emails**
from 8 parallel sources, sends (and follows up on) outreach emails, and now includes
a **team CRM** so the placement committee can track who contacted which company —
all from one dashboard.

**Live:** https://placementradar.onrender.com
**Full technical docs:** see [DOCUMENTATION.md](DOCUMENTATION.md)

---

## What it does

1. **Scrape** — pulls jobs from Naukri, Internshala, LinkedIn, Indeed, Google Jobs,
   and 20+ other boards (plus user-defined custom sites).
2. **Enrich** — for each company, 8 sources run in parallel to find contact details:
   company website + deep crawl, Zauba Corp, JustDial, IndiaMART, Sulekha,
   TradeIndia, and Google (Serper). Phones are validated with Google's
   libphonenumber; emails with an RFC validator.
3. **Act** — bulk-copy all phones/emails, grade each lead A–F by contact quality,
   send templated outreach over Gmail, and auto-queue no-reply companies for
   follow-ups.
4. **Track (CRM)** — committee members log in, record every call/email/WhatsApp
   against a company, move leads across a Kanban pipeline, and the placement head
   sees a live leaderboard of who did what.

Heavy scraping runs on **GitHub Actions** (2 CPU / 7 GB, free) so the web app stays
lightweight; **MongoDB Atlas** is the shared store and message bus. Total infra
cost: **$0/month**.

---

## Quick start (local)

**1. Requirements:** Python 3.10+

**2. Install:**
```bash
pip install -r requirements.txt
```

**3. Configure** — copy `.env.example` to `.env` and set at least `MONGO_URI`
(a free MongoDB Atlas M0 cluster works). The app refuses to start without it.
```bash
cp .env.example .env
# then edit .env
```

**4. Run:**
```bash
python app.py
```

**5. Open** → http://localhost:5000

> **Running offline on Windows:** the app reads config from environment variables,
> not from the `.env` file directly. `run_local.ps1` loads a local `placementradar.env`
> into the environment and starts the app in one step:
> ```powershell
> Set-ExecutionPolicy -Scope Process -Bypass   # once, if scripts are blocked
> .\run_local.ps1
> ```
> For local http, keep `SESSION_COOKIE_SECURE=0` so the login cookie works.

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGO_URI` | **Yes** | MongoDB Atlas connection string. App won't boot without it. The database name in the URI (e.g. `.../jobs`) is honoured. |
| `AUTH_MONGO_URI` | Optional | Separate connection for the **CRM login accounts** (e.g. same cluster, `.../signup`). Keeps user/login data isolated from scraped job data. If unset, users are stored in the `MONGO_URI` database. |
| `SECRET_KEY` | **Yes (prod)** | Signs the login session cookie. Required once the CRM is in use, or logins reset on every restart. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `SESSION_COOKIE_SECURE` | Prod only | Set to `1` on the live HTTPS site so the cookie is `Secure`. **Must be `0`/unset for local `http://`**, or the browser drops the cookie and login fails. |
| `ADMIN_NAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Optional | One-time seed for the first admin account — used only when no users exist yet. Remove after the first login. |
| `APP_API_KEY` | Recommended | When set, machine/CI calls to `/api` routes use an `X-API-Key` header. Browser users authenticate with CRM logins instead. |
| `SERPER_API_KEY` | Optional | Google Serper key for the search-based enrichment source. Can also be set in the Settings UI. |
| `GMAIL_APP_PASSWORD` | Optional | Gmail App Password for outreach; overrides the value in Settings so the secret stays out of the DB. |
| `GH_PAT` | Optional | GitHub PAT (workflow scope) — enables offloading scrapes to GitHub Actions. Without it, scraping runs in a local thread. |

Secrets are **never** hardcoded — all come from the environment. Never commit `.env` or any `*.env` file (both are gitignored).

---

## How to use

1. Enter a **job role** and **location**, pick which **sites** to scrape.
2. Keep **contact enrichment** on to auto-find phones/emails.
3. Click **Start Scraping** and watch live progress.
4. On the **Job Board**: filter, sort, see each lead's **A–F grade** and when it was
   **Added**, then **Copy All Phones / Emails** or hit **Email**.
5. On **Outreach**: send templated emails, and use **Follow-ups Due** to nudge
   companies that never replied (max 2 follow-ups, auto-capped).
6. **Export CSV** anytime.

---

## Team CRM

Once the committee grows past one person, the CRM turns the tool into a shared
workspace. It has two roles:

- **Admin** (the placement head / "sir") — sees the **leaderboard** of all members'
  activity, manages members, and can act on any lead.
- **Member** — logs their own outreach and works their own (or unassigned) leads.

What members do:

- **Log activity** on any company — one entry can cover multiple channels at once
  (Call + Email + WhatsApp) and still counts as a single touch, with an outcome
  (contacted / interested / drive scheduled / reply received) and free-text **remarks**.
- **Kanban pipeline** — drag a company across stages: To Contact → Contacted →
  Interested → Drive Scheduled → Placed / Rejected. Moving a card updates the job's
  status automatically.
- **Ownership** — a lead can be assigned to a member; members can edit their own or
  unassigned leads, admins can edit anything.

What the admin sees:

- **Leaderboard** — a weighted score per member (a reply or scheduled drive is worth
  more than a raw call), with weekly and all-time views, so effort is visible and fair.

**Where the data lives:** login accounts are stored in a **separate `signup`
database** (via `AUTH_MONGO_URI`) so identity is isolated from job data; all the CRM
activity (logs, pipeline, remarks) lives alongside the jobs in the main database.

---

## Project structure

```
placement_tool/
├── app.py                 ← Flask API (jobs, outreach, CRM) + GitHub Actions trigger
├── auth.py                ← CRM auth: login/session, role decorators, first-admin bootstrap
├── database.py            ← MongoDB layer (jobs, contacts, users, activities, pipeline)
├── job_scraper.py         ← Multi-board scraping engine
├── contact_finder.py      ← 8-source parallel contact enrichment
├── email_sender.py        ← Gmail SMTP outreach + follow-ups
├── scrape_worker.py       ← Entry point run by GitHub Actions
├── hr_database_lookup.py  ← CSV-backed HR contact seed data
├── hr_database.csv        ← Verified Indian HR contacts
├── requirements.txt
├── Dockerfile             ← Container build
├── render.yaml            ← Render deploy config
├── .env.example           ← Env var template
├── templates/
│   └── index.html         ← Single-page dashboard UI
└── .github/workflows/     ← deploy.yml (CI/CD), scrape.yml, keepalive.yml
```

---

## Deployment

Dockerized and deployed on **Render** (free tier). Every push to `main` triggers
`deploy.yml`: lint → build image → push to ghcr.io → deploy → health check.
See [DOCUMENTATION.md](DOCUMENTATION.md) for the full architecture and the
GitHub-Actions-as-scraper design.

---

## Notes on data

- **Phone numbers:** many postings omit them by design; Naukri and Internshala
  recruiters list them most often. The 8-source enrichment fills the gaps from
  business directories and company sites.
- **Anti-bot:** if you get blocked, lower Max Results to 20–30, wait a few minutes,
  and prefer Internshala/Naukri/Google Jobs over LinkedIn (most aggressive).

---

Built for placement committees 🎓
