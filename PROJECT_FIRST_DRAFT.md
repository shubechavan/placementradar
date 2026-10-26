# PlacementRadar
### Job Intelligence and Outreach Automation for College Placement Cells
#### Semester III Mini Project — First Draft

**Student:** Shubham Chavan
**Institution:** K. J. Somaiya Institute of Management
**Submission:** First Draft — 25th September 2026
**Live Application:** https://placementradar.onrender.com
**Repository:** https://github.com/shubechavan/placementradar

---

## 1. Introduction

PlacementRadar is a full-stack web application that automates the campus placement
outreach pipeline — discovering job openings across recruitment portals, identifying and
verifying HR contact details for each company, scoring every contact by reliability, and
providing the placement committee with a shared CRM to manage outreach.

Campus placement work is a sales funnel operated by student volunteers. A committee must
find hiring companies, identify a contactable person at each, reach out, follow up, and
maintain a shared record. Each step is typically manual, performed in spreadsheets, by
students who also have coursework.

This project treats that funnel as an engineering problem. The system runs at **zero
monthly infrastructure cost** on free tiers of Render, MongoDB Atlas and GitHub Actions —
a deliberate constraint, since a tool a student committee cannot afford to keep running
will not survive the academic year.

**Current status:** the core pipeline is implemented end-to-end and deployed. This draft
reports what is built, what was measured, and what remains.

---

## 2. Problem Statement

The placement workflow breaks at four points.

**Job discovery is fragmented.** Relevant openings are scattered across Naukri,
Internshala, LinkedIn, Indeed, Foundit, Shine, Apna and a long tail of startup boards. No
single portal covers the field.

**Finding the HR contact is the bottleneck.** Job listings rarely include a recruiter's
email or phone. That information exists publicly, but locating it means manual search per
company. At roughly five minutes each, 300 companies is 25 hours before a single email is
sent.

**Contact data decays and is frequently mis-attributed.** Addresses bounce and numbers
disconnect. More seriously, a contact genuinely belonging to one employer is often filed
under another. This project measured that error in its own corpus at **23.5%** — an error
invisible to any summary count, because the totals look identical either way.

**There is no shared institutional memory.** Without a common record, two members contact
the same company in the same week, follow-ups lapse, and when the committee changes hands
annually the relationship history leaves with it.

The underlying problem: the pipeline is **manual, unverified, and unrecorded** — so effort
does not compound and quality cannot be measured.

---

## 3. Objectives

1. Aggregate job openings from multiple portals into one deduplicated store.
2. Automatically discover HR contact details for each company.
3. **Verify** contacts rather than merely collecting them — deliverability, validity, and
   correct attribution to the right employer.
4. Score contacts so committee effort is directed at the most reliable ones.
5. Provide a shared CRM giving accountability and institutional continuity.
6. Operate at zero infrastructure cost.
7. **Measure the system's own output quality** and report it honestly, including failures.

---

## 4. Proposed Solution and System Architecture

### 4.1 Architecture

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   │  Job Boards  │     │   Company    │     │  Directories │
   │   (20+)      │     │   Websites   │     │  & Registries│
   └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               ▼
                   ┌───────────────────────┐
                   │   SCRAPING ENGINE     │   parallel, per-source
                   │  (response guards)    │   instrumentation
                   └───────────┬───────────┘
                               ▼
                   ┌───────────────────────┐
                   │  IDENTITY RESOLUTION  │   company → confirmed domain
                   │  (corroboration gate) │   + human review queue
                   └───────────┬───────────┘
                               ▼
                   ┌───────────────────────┐
                   │  CONTACT ENRICHMENT   │   sitemap crawl, contact pages
                   └───────────┬───────────┘
                               ▼
                   ┌───────────────────────┐
                   │  VERIFICATION LAYER   │   MX lookup, libphonenumber,
                   │                       │   attribution checks
                   └───────────┬───────────┘
                               ▼
                   ┌───────────────────────┐
                   │  CONFIDENCE SCORING   │   weighted evidence → 0-1
                   └───────────┬───────────┘
                               ▼
        ┌──────────────────────┴──────────────────────┐
        ▼                                             ▼
┌───────────────┐                          ┌────────────────────┐
│  MongoDB      │◄────────────────────────►│  Flask Web App     │
│  Atlas        │                          │  Dashboard + CRM   │
└───────────────┘                          └────────────────────┘
```

Heavy scraping runs on **GitHub Actions** (2 CPU / 7 GB, free tier) so the web dyno stays
lightweight; **MongoDB Atlas** serves as both the shared datastore and the message bus
between the two.

### 4.2 The design principle

**Anything producing an identity or attribution decision gets a review queue, not a
heuristic.**

Deciding whether `contact@simulab.in` belongs to `simulab.com` is not solvable by string
matching — `advice@rethink.org` and `rethink.com` are *different organisations* with
identical surface similarity. Where the data cannot settle the question, the system
surfaces the decision to a human with the evidence attached rather than guessing and
recording the guess as fact.

This is what makes the output safe to act on. A committee emailing a stranger on the
system's recommendation is staking the institute's reputation on it.

---

## 5. Methodology

### Stage 1 — Scrape
Sources run in parallel under a wall-clock budget. Each response passes a **response
guard** that verifies the page actually answers the query, rather than trusting HTTP 200.

### Stage 2 — Identity resolution
A company name cannot identify a company — "Genesis" covers three different firms in this
corpus. Each company is resolved to a domain, and the domain is accepted only when the
page independently corroborates the company through its `<title>`, `og:site_name` or
footer copyright. **Tautological evidence is rejected** (a page whose only "proof" is
echoing back the domain that was guessed from the name).

Documents are keyed `domain:<domain>` when confirmed, `key:<company_key>` otherwise.

### Stage 3 — Contact enrichment
For companies with a confirmed domain, the system crawls sitemaps and prioritises
contact-ish pages (contact, about, careers, team) by path segment, extracting emails and
phone numbers with their source URL recorded.

### Stage 4 — Verification
- **Email:** RFC syntax validation, then a live **DNS MX lookup**
- **Phone:** Google's libphonenumber for validity and line type
- **Attribution:** does the contact's domain match the company's confirmed domain?

### Stage 5 — Scoring and storage
Contacts are scored on independent evidence and stored **append-only**, deduplicated by
`(type, value)`. Re-scraping can only grow the set. Contacts failing attribution are
**quarantined, not deleted**, so a false negative is recoverable.

---

## 6. Implementation Status

| Module | Status |
|---|---|
| Multi-source job scraping (20+ portals) | ✅ Implemented |
| Location-scoped discovery | ✅ Implemented |
| Response guards (redirect + relevance) | ✅ Implemented |
| Company identity resolution + corroboration gate | ✅ Implemented |
| Human review queue | ✅ Implemented |
| Sitemap-based contact discovery | ✅ Implemented |
| Email MX verification | ✅ Implemented |
| Phone validation (libphonenumber) | ✅ Implemented |
| Confidence scoring | ✅ Implemented, **calibration pending** |
| Append-only contact store | ✅ Implemented |
| Per-source instrumentation + health API | ✅ Implemented |
| Team CRM (pipeline, activity log, leaderboard) | ✅ Implemented |
| Email outreach + automated follow-up | ✅ Implemented |
| Security hardening (XSS, rate limiting, secrets) | ✅ Implemented |
| Threshold calibration from labelled data | ⏳ In progress |
| Contact attribution resolution | ⏳ In progress |
| Analytics module for placement head | ⏳ Planned |

---

## 7. Results and Measurements

All figures are measured from the system's own corpus.

### 7.1 Contact store

| Metric | Value |
|---|---|
| Companies tracked | **314** |
| Resolved to a confirmed domain | **186 (59.2%)** |
| Total contacts | **501** (223 emails, 278 phones) |
| Emails verified deliverable (MX) | **220 / 223 (98.7%)** |
| Phones with recorded provenance | **278 / 278 (100%)** |
| Contacts ranked usable for outreach | **407** |

### 7.2 Identity resolution improvement

| Stage | Companies with a usable domain |
|---|---|
| Baseline | 21.2% |
| After automated resolution | 53.3% |
| After human review integration | **61.4%** |

### 7.3 Contact discovery

Sitemap-based crawling recovered contacts for **51 of 120 (42.5%)** companies that
previously had none. A second pass over companies never covered by the first scope
produced a further 22 phone numbers and 30 email addresses.

### 7.4 Location-scoped discovery

A defect was found in which the `location` parameter was accepted by the scraper but never
used in a request URL. The corpus contained **3 Mumbai-based companies out of 314**. After
the fix, a single location-scoped run returned **153 distinct Mumbai companies**.

### 7.5 Source reliability

Per-source instrumentation distinguishes *blocked* from *found nothing* — previously
indistinguishable, since a swallowed exception and an empty result looked identical.
Measurement showed **5 of 10 contact sources returning zero**: two served soft blocks at
HTTP 200, three were hard-blocked (403 / 5xx).

---

## 8. Key Findings

The most instructive results were methodological.

**Aggregate metrics concealed record-level errors.** Every summary statistic indicated the
system was working. Reading individual records showed otherwise: two companies merged
under one identity, contacts filed against the wrong employer, and a scraper returning an
unrelated page at HTTP 200. **None moved a total enough to notice.**

**HTTP 200 does not mean the server answered the question asked.** This occurred three
times in different forms — a directory serving a soft block with an empty body, a job
board redirecting an unknown search term to a generic listing, and a parked domain
answering as though it were a company site. Status codes cannot detect this; content
verification can.

**A helper that predates a schema change is a latent bug.** A storage function written
before the identity scheme changed was reused against the new scheme and silently created
51 malformed duplicate documents. The fix was structural: explicit assertions on every
write, and a post-write shape check.

**Collection is the easy half; verification is the hard half.** Three separate attempts to
add new data sources were abandoned after measurement showed the assumption behind each
did not hold — ATS API penetration measured 1.6%, automated name extraction achieved 0%
precision, and a third-party OSINT tool failed on every source. Measuring before building
prevented three features that would not have worked.

---

## 9. Social Cause

### Equalising access to placement opportunities

Campus recruitment is structurally unequal, and the inequality is not primarily about
student ability. Well-resourced institutions have salaried placement offices, decades of
accumulated corporate relationships, and recruiters who arrive unprompted. Tier-2 and
tier-3 colleges run placements on a volunteer student committee with a spreadsheet and no
inherited contact list.

The gap is one of **reach, not merit**. A capable student at an under-resourced institute
is less likely to be placed not because they are less prepared, but because no one from
their college contacted the company that would have hired them. The bottleneck is
administrative capacity — which is what software can supply.

**How the project addresses it:**

- **Replaces inherited relationships with discoverable ones** — a committee with no alumni
  network can still find and reach hiring companies.
- **Makes work survive committee turnover** — a shared CRM turns individual effort into an
  institutional asset the next cohort inherits.
- **Costs nothing to run** — a social design decision, not only an engineering one. A tool
  requiring a budget line excludes precisely the institutions that most need it.
- **Returns student time to studying** — automating the mechanical portion is a direct
  transfer of time back to education.

---

## 10. AI Implementation

### 10.1 Implemented — deterministic decision systems

The intelligence currently in the system is **algorithmic and rule-based rather than a
trained model**, and for the identity and attribution decisions at its core this is a
deliberate choice. These decisions must be **auditable**: when the system claims an email
belongs to a company, a committee member must be able to inspect the evidence before
contacting a stranger. A deterministic rule can be inspected, tested and corrected; a
model's confidence score cannot be argued with.

- **Weighted confidence scoring** — a pure function combining own-domain match, MX
  verification, named person, HR role and cross-company spread into a 0–1 score.
- **Entity resolution with a corroboration gate** — a classic entity-resolution problem,
  solved with independent page evidence and explicit rejection of circular signals.
- **Content-relevance verification** — statistical comparison of returned content against
  the query, which caught a job board silently serving generic listings.
- **Classification** — technical vs non-technical roles, role-account detection, parked
  page detection, backed by libphonenumber and DNS for ground truth.

### 10.2 Planned — where machine learning genuinely applies

- **Threshold derivation from labelled data.** Analysis showed the current weights are
  uncalibrated: identity resolution dominates the ranking roughly sevenfold over contact
  quality. The correct fix is supervised — label a stratified sample, compute precision at
  each cutoff, derive the threshold from measured outcomes. A labelling sheet of 103
  stratified samples has been prepared.
- **LLM-assisted attribution review.** Deciding whether `signify.com` legitimately serves
  `philips.com` requires world knowledge (Signify is the former Philips Lighting) that no
  string metric encodes.
- **Semantic role matching** using embeddings, to replace keyword classification.
- **Reply classification** to drive follow-up automatically.

---

## 11. Work Remaining

| Item | Target milestone |
|---|---|
| Confidence threshold calibration from the labelled sample | Second draft (16 Oct) |
| Resolution of 43 outstanding contact-attribution cases | Second draft |
| Domain resolution for 128 companies requiring human lookup | Second draft |
| Mumbai-scoped enrichment pipeline (discovery complete; enrichment pending) | Second draft |
| Analytics module for the placement head | Final (22 Oct) |
| Complete evaluation metrics and documentation | Final (22 Oct) |

---

## 12. Technologies Used

**Backend:** Python 3.13, Flask, PyMongo, BeautifulSoup4 + lxml, Requests,
concurrent.futures

**Data & Validation:** MongoDB Atlas, libphonenumber (`phonenumbers`), email-validator,
dnspython, pandas

**Frontend:** HTML5 / CSS3 / Vanilla JavaScript (no framework dependency), Chart.js,
drag-and-drop Kanban

**Security:** Werkzeug PBKDF2, signed session cookies, sliding-window rate limiting,
environment-only secret storage

**DevOps:** Docker, GitHub Actions (CI/CD *and* as a free distributed scraping worker),
Render, Gmail SMTP

**Engineering practices:** benchmark harness with isolated HTTP cache, database write
guards with restore points, append-only storage, rebuild-over-mutate corrections,
per-source instrumentation

---

## 13. Future Scope

**Near term:** complete threshold calibration; resolve outstanding attributions; finish
human domain resolution for the remaining companies.

**Medium term:** student-facing portal with profile-matched opportunities; resume-to-role
matching using embeddings; analytics for the placement head; WhatsApp Business API
integration.

**Long term:** multi-institution deployment with isolated tenant data; an opt-in **shared
verified-contact commons** where participating institutions contribute verification
outcomes, so data quality improves for everyone as more colleges join — a network effect
that favours smaller institutions most; an alumni-network graph surfacing which companies
employ the institute's alumni.

---

## 14. Conclusion

PlacementRadar began as an automation exercise and became a demonstration that in data
pipelines, **collection is the easy half and verification is the hard half.**

The system discovers jobs across 20+ portals, maintains a company contact store with
domain-first identity resolution, verifies email deliverability, scores contacts on
independent evidence, and gives a placement committee a shared CRM with accountability —
at zero infrastructure cost.

The most valuable outcome has been methodological. Aggregate metrics consistently
indicated success while individual records were wrong, and every significant defect was
found by reading records rather than summaries. Correcting them required building
verification into the pipeline rather than adding it afterwards — corroboration gates,
relevance assertions, quarantine over deletion, and review queues wherever the data could
not settle a question.

A contact list that is 80% correct is not 80% as useful as a correct one. It is actively
harmful, because there is no way to tell which 80%. That principle is what the remaining
work is directed at.

---

*First draft submitted 25th September 2026. All figures are measured from the project's
own corpus, not estimated.*
