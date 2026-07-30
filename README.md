# 📡 PlacementRadar

A full-stack job-intelligence and outreach tool for college placement cells.
It scrapes jobs across 25+ boards, auto-discovers HR **phone numbers and emails**
from 8 parallel sources, and sends (and follows up on) outreach emails — all from
one dashboard.

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

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGO_URI` | **Yes** | MongoDB Atlas connection string. App won't boot without it. |
| `APP_API_KEY` | Recommended | When set, all `/api` routes (except `/api/dashboard`) require an `X-API-Key` header. Unset = open (local dev only). |
| `SERPER_API_KEY` | Optional | Google Serper key for the search-based enrichment source. Can also be set in the Settings UI. |
| `GMAIL_APP_PASSWORD` | Optional | Gmail App Password for outreach; overrides the value in Settings so the secret stays out of the DB. |
| `GH_PAT` | Optional | GitHub PAT (workflow scope) — enables offloading scrapes to GitHub Actions. Without it, scraping runs in a local thread. |

Secrets are **never** hardcoded — all come from the environment. Never commit `.env`.

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

## Project structure

```
placement_tool/
├── app.py                 ← Flask API (21 routes) + GitHub Actions trigger
├── database.py            ← MongoDB layer (jobs, searches, outreach, settings, contacts)
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
