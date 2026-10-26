# PlacementRadar
### Job Intelligence and Outreach Automation for College Placement Cells
#### Semester III Mini Project — Second Draft

**Student:** Shubham Chavan
**Roll No:** 17030925005
**Institution:** K. J. Somaiya Institute of Management
**Submission:** Second Draft — 18th October 2026
**Live Application:** https://placementradar.onrender.com
**Repository:** https://github.com/shubechavan/placementradar

---

## 1. Introduction

PlacementRadar is a full-stack web application that automates the campus placement outreach pipeline. It discovers job openings across recruitment portals, identifies and verifies HR contact details for each company, scores every contact by reliability, and gives the placement committee a shared CRM to manage outreach.

The first draft (25th September) described the architecture and the core pipeline, and closed with a list of work remaining. This second draft reports what was done against that list, what new problems were found in the running system and how they were fixed, and what remains for the final submission.

The work in this phase falls into three areas:

1. **Finding why searches returned so few contacts**, and fixing the causes.
2. **Scaling up the contact store**, from 314 companies and 501 contacts to 1,732 companies and 2,324 contacts, with location data for the Mumbai region.
3. **Rebuilding the committee's pipeline board** as a React interface modelled on professional project-tracking tools.

**Current status:** the changes described here run in the development build against a local database. They will be deployed to the live application with the final submission.

---

## 2. Progress Against the First Draft

The first draft committed to four items for this milestone.

| First-draft commitment | What was done | Status |
|---|---|---|
| Mumbai-scoped enrichment | HR contact directory imported with office locations; office discovery for Mumbai, Pune and Bengaluru through OpenStreetMap | ✅ Done (office merge awaiting review) |
| Domain resolution for 128 companies needing human lookup | Three automated fallbacks added to the resolver; remaining cases stay in the human review queue | ⏳ In progress |
| Resolution of 43 contact-attribution cases | Review sheet built with the evidence for each case; decisions pending | ⏳ In progress |
| Confidence threshold calibration | 103-row stratified labelling sheet prepared; two scoring corrections applied in the meantime | ⏳ In progress — awaiting labels |

Work that was not planned for this milestone but turned out to be necessary:

| New work | Status |
|---|---|
| Diagnosis of low contact yield from searches | ✅ Done |
| Office landline numbers accepted by phone validation | ✅ Done |
| Stricter company matching in the HR directory lookup | ✅ Done |
| Ranked outreach list for the committee | ✅ Done |
| Pipeline board rebuilt in React | ✅ Done |
| Email-format inference — feasibility measured | ✅ Measured, feature planned |

---

## 3. Problem Statement (recap)

The placement workflow breaks at four points: job openings are scattered across many portals; HR contact details are missing from job listings and take about five minutes per company to find by hand; contact data decays and is often filed under the wrong employer (measured at **23.5%** in this project's own data); and without a shared record, committee members duplicate work and the history is lost when the committee changes each year.

The pipeline is **manual, unverified and unrecorded**, so effort does not compound and quality cannot be measured. This draft adds a fifth observation from running the system: **data a pipeline silently throws away is invisible.** The committee cannot ask for contacts it never knew existed, so a rule that discards good data does more harm than one that fails loudly.

---

## 4. System Architecture

```
  ┌────────────┐  ┌────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Job Boards │  │  Company   │  │ HR Contact   │  │ OpenStreetMap│
  │   (20+)    │  │  Websites  │  │ Directory    │  │  + Wikidata  │
  └─────┬──────┘  └─────┬──────┘  └──────┬───────┘  └──────┬───────┘
        └───────────────┴────────┬───────┴─────────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │     SCRAPING ENGINE     │  response guards:
                    │  (per-source metrics)   │  redirect + relevance
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │   IDENTITY RESOLUTION   │  company → confirmed domain
                    │   (corroboration gate)  │  + human review queue
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │ ENRICHMENT + VERIFY     │  sitemap crawl, MX lookup,
                    │                         │  mobile + landline validation
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │   CONFIDENCE SCORING    │  weighted evidence → 0-1
                    └────────────┬────────────┘
                                 ▼
          ┌──────────────────────┴───────────────────────┐
          ▼                                              ▼
  ┌───────────────┐                         ┌──────────────────────────┐
  │   MongoDB     │◄───────────────────────►│  Flask API               │
  │ company store │                         │  Dashboard · Outreach    │
  │ + auth store  │                         │  React pipeline board    │
  └───────────────┘                         └──────────────────────────┘
```

Compared with the first draft's diagram, the **HR contact directory** and **OpenStreetMap / Wikidata** are new inputs. The diagram also now shows that user accounts are kept in a **separate authentication database**, so a fault in the company data can never expose or damage login records.

The design principle is unchanged: **anything producing an identity or attribution decision gets a review queue, not a heuristic.** Every new input in this phase was routed through the same identity resolution and conflict checks as the existing scrapers.

---

## 5. Work Completed in This Phase

### 5.1 Why searches returned so few contacts

**The symptom.** Searches were returning jobs, but very few of them came with a phone number or email the committee could use.

**The method.** Before changing any code, each stage of contact discovery was traced for a sample of real searches: was the company's website found, were contacts found on it, and were they kept or rejected — and why. Three independent causes emerged, none of which raised an error:

- **Company websites were not being found.** The resolver guessed only `.com` addresses, and a bug limited it to a single guess. Without a confirmed website there is nothing to search for contacts (Section 5.2).
- **Office numbers were being thrown away.** Phone validation accepted only mobile numbers, so every company switchboard and office line was discarded after being found (Section 5.3).
- **Contacts were being attached to the wrong company.** The lookup against the committee's HR directory matched on single words, so two unrelated companies could share contacts (Section 5.6).

**The result.** After the three fixes, for "software developer, Mumbai", five of the first ten jobs carry a phone number and four an email address — every one on the company's own domain.

### 5.2 Company identity resolution

The first draft resolved 59.2% of companies to a confirmed website. Three fallbacks were added for the remainder, all passing through the same corroboration gate:

- **Multiple domain suffixes.** The resolver previously guessed only `.com`, and a bug limited it to a single suffix. Indian companies frequently use `.in` or `.co.in`.
- **Short-name candidates.** A company listed under a long legal name often uses a domain built from its first distinctive word alone. Candidates must be at least six characters, must not be a common business word, and the page must show an Indian presence.
- **Wikidata lookup.** Wikidata records an official website (property P856) for many companies. A label-match guard prevents accepting a different company with a similar name. An early test reported almost no websites found; the cause was Wikidata refusing anonymous requests, which the test had counted as "no website". With a proper identifying header, 10 of 40 test companies resolved, up from 1.

A defect in the existing gate was found and fixed along the way: **parked-domain detection** matched marker words as substrings, so the domain-marketplace marker `dan.com` matched `jordan.com`. Markers now match only as whole words, and more marketplace markers were added.

These fallbacks are in the resolver and apply to every new search. They have not yet been re-run over the companies already stored, which is why the confirmed-domain count in Section 6.1 has not moved.

### 5.3 Phone validation now accepts office landlines

Phone validation accepted only Indian mobile numbers. Every office landline was silently discarded — including company switchboards, which are often the most reliable route to an HR department. The rule now accepts valid Indian fixed-line numbers, but only when written in a phone-like format, so that order numbers and dates are not mistaken for phones. All 278 previously stored numbers were re-checked and none changed.

### 5.4 Importing the HR contact directory

A company-wise HR contact directory available to the committee was parsed (1,838 rows) and imported through the same identity checks as scraped data:

- **Idempotent:** running the import twice adds nothing the second time.
- **Conflict-safe:** 9 records disagreed with existing data — for example, a company already linked to a different website. These were held back for human review rather than overwritten.
- **Location-tiered:** each company was classified by evidence of a Mumbai office. **125 companies** have a confirmed Mumbai office, and **88 HR contacts** can be paired with a Mumbai office line.

Before the import, a database restore point was taken and the target was asserted to be the local database — the standing safety rule for every bulk write in this project.

### 5.5 Office discovery through OpenStreetMap

Job boards rarely list office phone numbers. OpenStreetMap records offices with addresses and often phone numbers, and its Overpass API is free. A bounding-box query per city found:

| City | Offices found | With a phone number |
|---|---|---|
| Mumbai | 285 | 45 |
| Pune | 288 | 59 |
| Bengaluru | 1,881 | 218 |

These are held in a review file. They will be merged into the company store only after their matches to existing companies have been reviewed, because a wrong office-to-company match would put the committee on the phone with the wrong organisation.

### 5.6 Company matching in the HR directory lookup

The lookup that matches a scraped company against the HR directory compared individual words. The word "Private", from "Private Limited", was enough to match two unrelated companies. Matching now requires the same normalised company key, or a match on *distinctive* words only. Names made entirely of generic words must match exactly.

### 5.7 Scoring correction

The phrase **"human resource" was missing from the HR-role vocabulary**, so contacts titled, for example, "Human Resource Executive" were not recognised as HR. The gap mattered once the HR directory was imported. After adding the phrase, along with *staffing* and *personnel*, 565 company records were rescored.

This is an interim correction to weights originally set by judgement. The proper fix — deriving the threshold from labelled outcomes — is described in Section 8.

### 5.8 Ranked outreach list

The committee needs a call list, not a database. An export now ranks every usable contact into five tiers, best first:

| Tier | Contact |
|---|---|
| A+ | A named HR person, email verified (MX) |
| A | An HR address on the company's own domain, verified |
| B | The committee's hand-compiled HR database, verified |
| C | A general address on the company's own domain (for example `info@`) — ask to be put through to HR |
| D | A phone number published on the company's own website — usually a switchboard |

Within each tier, companies with a confirmed Mumbai office come first. The current list holds **2,181 usable contacts**.

### 5.9 Committee CRM and the rebuilt pipeline board

The CRM described in the first draft is unchanged in function:

- **Accounts and roles.** Members and admins sign in with hashed passwords. Admins assign companies to members; a card can be moved only by its owner or an admin.
- **Activity tracking.** Calls, emails and WhatsApp messages are logged with an outcome and a note, and appear in a timeline on each company.
- **Leaderboard.** Admins see a weekly score per member, weighted towards outcomes (replies and drives) rather than raw activity.

In this phase the **pipeline board** was rebuilt in **React 18**, modelled on the board layout of professional project-tracking tools such as Atlassian Jira:

- Six stages — *To contact, Contacted, Interested, Drive scheduled, Won, Lost* — with drag-and-drop between them.
- Each card shows the company, role, city, whether an email and/or phone is available, a short reference key (for example `PR-3A1F`) for referring to a company aloud, days since added, and the owner's avatar.
- Cards waiting 14 days or more in *To contact* or *Contacted* are **flagged**, so stalled follow-ups are visible at a glance.
- A **detail panel** shows the stage, owner, contacts (with copy, call and email links), notes, a "Log call / email" action and the activity history.
- Filters by owner, city and contact type; grouping into rows by owner or city; and an insights strip showing the conversion funnel.
- Moves update the screen immediately and **roll back** if the server rejects them, so the board never shows a state that was not saved.

React is loaded directly in the page with no build step, keeping the deployment as simple as before.

---

## 6. Results and Measurements

All figures are measured from the project's own data, not estimated.

### 6.1 Contact store

| Metric | First draft | Second draft |
|---|---|---|
| Companies tracked | 314 | **1,732** |
| Companies with at least one contact | — | **1,573** |
| Resolved to a confirmed domain | 186 | 186 |
| Total contacts | 501 | **2,324** |
| — emails | 223 | 2,046 |
| — phones | 278 | 278 |
| Emails verified deliverable (MX) | 220 / 223 (98.7%) | **1,994 / 2,046 (97.5%)** |
| Contacts naming a specific person | — | **1,823** |
| Contacts ranked usable for outreach | 407 | **2,181** |

The share of companies with a confirmed domain fell from 59% to 11%. This is expected and deliberate: directory companies enter the store keyed by name, and are promoted to a confirmed domain only after passing the corroboration gate — not because an email address happens to share a domain with them.

### 6.2 Contact yield of a live search

| "Software developer, Mumbai" — first 10 jobs | Count |
|---|---|
| Jobs with a phone number | 5 |
| Jobs with an email address | 4 |
| Contacts found on the company's own domain | All |

### 6.3 Source reliability

A measurement across 19 job boards found **14 returning zero results without raising any error**. Before per-source instrumentation, "this site blocked us" and "this site has no matching jobs" were indistinguishable. Google Jobs was paused after measurement showed it returned mostly re-posted listings from other aggregators.

### 6.4 Email-format inference (feasibility)

Commercial tools such as RocketReach and SignalHire predict a person's work email from the company's address format (for example, `firstname.lastname@`). This was tested on the directory's named contacts: the format was learned from a company's other addresses, then used to predict a held-out address.

| Method | Exact match |
|---|---|
| Baseline — always use the most common format overall | 59% |
| Per-company learned format | **72%** |

This is a large enough gain to justify building the feature for the final submission. Predicted addresses will be labelled as predicted and verified by MX lookup, never presented as found.

---

## 7. Key Findings

**Validation rules can silently discard the most valuable data.** The mobile-only phone rule rejected every office landline, and the stored data looked complete because rejected numbers leave no trace. This is survivorship bias inside a pipeline: you can only audit what was kept. An earlier audit of the stored phone numbers had passed for exactly this reason.

**Common words make strangers look alike.** One shared word — "Private", from "Private Limited" — was enough to give one company another's HR contacts. Matching has to rely on what is distinctive about a name, not what is common to all of them.

**Measure a feature's premise before building it.** Email-format inference is the core of commercial contact-finder tools. Measuring it on the project's own data first (72% against a 59% baseline) shows both that it is worth building and how often it will be wrong — which decides how its output must be labelled.

**A field must mean one thing.** The column recorded as a phone number's "source" turned out to hold the job board the listing came from, not where the number was found. This mislabelled the provenance of 157 phone numbers. A correction is prepared and awaits approval, since it rewrites stored data.

**Code that exists is not code that runs.** A per-domain request-throttling module was written earlier and assumed to be active. A search of the code showed it is never called. This is the same class of error as reporting a run without reading its log, and it is recorded as open work rather than as a feature.

---

## 8. AI Implementation

### 8.1 Implemented — auditable decision systems

The core decisions remain rule-based and inspectable, for the reason given in the first draft: before the committee contacts a stranger on the system's recommendation, a member must be able to see why.

- **Content-relevance scoring** — token overlap with stemming between the query and the returned jobs, used to reject pages that do not answer the query.
- **Entity resolution** — company-to-domain matching with a corroboration gate, distinctive-token matching and explicit rejection of generic words.
- **Weighted confidence scoring** — own-domain match, MX verification, named person, HR role and cross-company spread combined into a 0–1 score.
- **Classification** — technical versus non-technical roles, role accounts versus named people, parked domains, and mobile versus landline numbers.

### 8.2 In progress — learning from labelled data

- **Threshold calibration.** The earlier proposal of a fixed 0.6 cut-off for bulk outreach was shown to be unusable with the current weights. A 103-row stratified sample has been prepared, in which a human labels each contact *good*, *wrong company*, *dead* or *unsure*. Precision will be computed at each candidate cut-off, and the threshold will be set from those measured outcomes rather than chosen by hand.
- **Email-format inference** (Section 6.4) — a model of each company's address format, learned from its known addresses.

### 8.3 Planned

- **LLM-assisted attribution review** for cases that need world knowledge, such as one company's email legitimately serving its former parent company.
- **Semantic role matching** using embeddings, to replace keyword classification.
- **Reply classification** to drive follow-ups automatically.

---

## 9. Social Cause

Campus recruitment is unequal in **reach, not merit**. Well-resourced institutions have salaried placement offices and decades of corporate relationships; most colleges run placements on a volunteer student committee with a spreadsheet. A capable student is less likely to be placed simply because nobody from their college contacted the company that would have hired them.

This phase moved the project further towards closing that gap:

- The HR directory import and office discovery give a committee **without an inherited contact list** a ranked list of 2,181 usable contacts.
- The CRM and pipeline board make the committee's work **survive its annual turnover**: the next cohort inherits every company, owner, note and call history.
- The system still runs at **zero infrastructure cost**, so it remains usable by exactly the institutions that need it most.

---

## 10. Work Remaining for the Final Submission

| Item | Notes |
|---|---|
| Analytics dashboard for the placement head | Faculty-facing view of pipeline progress and member activity |
| Alumni-to-company mapping | Design complete; awaiting the alumni list from the college (Section 12) |
| Confidence threshold calibration | Needs the completed 103-row labelling sheet |
| Email-format inference feature | Feasibility measured at 72% (Section 6.4) |
| 43 attribution decisions and outstanding domain lookups | Review sheets prepared |
| Merge of OpenStreetMap offices | After the matches are reviewed |
| Provenance correction for 157 phone numbers | Prepared; awaits approval |
| Wire request throttling; extend instrumentation to all 22 sources | See Section 7 |
| Deployment of this phase's changes, evaluation and final documentation | Final milestone |

---

## 11. Technologies Used

**Backend:** Python 3.13, Flask, PyMongo, BeautifulSoup4 + lxml, Requests, concurrent.futures

**Data & validation:** MongoDB (separate company and authentication databases), libphonenumber (`phonenumbers`), email-validator, dnspython, pandas

**Open data sources:** OpenStreetMap Overpass API, Wikidata API

**Frontend:** HTML5, CSS3, JavaScript; **React 18** with htm (no build step) for the pipeline board; Chart.js; Atlassian design tokens for the board's visual language

**Security:** Werkzeug PBKDF2 password hashing, signed session cookies, login rate limiting with timing-safe responses, environment-only secrets, output escaping against XSS

**DevOps:** Docker, GitHub Actions (CI/CD and a free scraping worker), Render, Gmail SMTP

**Engineering practices:** database write guards that assert the target and take a restore point before every bulk write; append-only contact storage; rebuild-over-mutate corrections; dry runs before every write; per-source instrumentation; review queues for identity decisions

---

## 12. Future Scope

**Alumni-to-company mapping (next phase).** The college will provide a list of alumni with their current employers. Each employer name will be resolved through the same identity resolution as scraped companies, so every company card can show how many of the institute's alumni work there — the "people from your school" view a professional network offers. Alumni data will be stored separately from scraped data, visible only to admins, and used only for placement outreach, in line with India's Digital Personal Data Protection Act, 2023.

**Medium term:** a student-facing portal with profile-matched opportunities; resume-to-role matching using embeddings; WhatsApp Business API integration.

**Long term:** multi-institution deployment with isolated data per institution, and an opt-in **shared verified-contact commons** in which participating colleges contribute verification outcomes, so data quality improves for every member as more colleges join — a network effect that favours smaller institutions most.

---

## 13. Conclusion

The first draft concluded that in data pipelines, collection is the easy half and verification is the hard half. This phase added a corollary: **verification rules need verifying too.** The phone rule that discarded every office line, and the word match that gave one company another's contacts, were both checks — and both failed silently, in opposite directions.

The response was the same as before: trace the pipeline stage by stage before changing anything, fix the cause rather than the symptom, and measure the result. The contact store has grown more than fourfold, every new source passed through the same identity and conflict checks as the old ones, and the committee now has a ranked call list and a professional pipeline board to work from.

What remains for the final submission is the part that turns a tool into an institutional system: an analytics view for the placement head, alumni mapping, and a confidence threshold derived from measured outcomes rather than judgement.

---

*Second draft submitted 18th October 2026. All figures are measured from the project's own data, not estimated.*
