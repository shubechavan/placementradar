# 📡 PlacementRadar — Job Intelligence Tool

A web-based job scraping tool built for placement committees.
Scrapes jobs from **LinkedIn, Indeed, Glassdoor, ZipRecruiter, Internshala, Naukri, Shine, Foundit, Apna**
and attempts to enrich each listing with **HR emails & phone numbers**.

---

## ⚡ Quick Setup (5 minutes)

### 1. Install Python 3.10+ (if not already installed)
Download from https://python.org

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the tool
```bash
python app.py
```

### 4. Open your browser
Go to → **http://localhost:5000**

---

## 🔧 How to Use

1. Enter the **job role** (e.g. "Software Engineer", "Data Analyst", "MBA Finance")
2. Enter **location** (e.g. "Bangalore", "Mumbai", "India")
3. Select **job sites** to scrape
4. Toggle **auto-fetch emails & phones** ON for contact enrichment
5. Click **Start Scraping**
6. Wait for results — use **filters** to narrow down
7. Click **Export CSV** to download

---

## 📊 CSV Output Columns

| Column       | Description                              |
|--------------|------------------------------------------|
| title        | Job role/title                           |
| company      | Company name                             |
| location     | Job location                             |
| job_type     | Full-time / Internship / Contract        |
| salary       | Salary range (if available)              |
| site         | Source site (linkedin, naukri, etc.)     |
| date_posted  | When the job was posted                  |
| emails       | All emails found (comma-separated)       |
| hr_contact   | Best HR/recruiter email (highlighted)    |
| phone        | Phone numbers found                      |
| job_url      | Direct link to job posting               |
| company_url  | Company website URL                      |
| description  | First 600 chars of job description       |

---

## ⚠️ Notes on Phone Numbers

Most job postings **intentionally don't list phone numbers** — companies
prefer candidates apply through their system. The tool will:
- Extract phones from job description text (if any)
- Scrape company "Contact Us" pages
- Try Google search for HR contact pages

For phone numbers, **Naukri** and **Internshala** have the most because
Indian recruiters often post them.

---

## 🛡️ Anti-Bot / Rate Limiting

If you get blocked:
- Reduce "Max Results" to 20–30
- Wait a few minutes before re-scraping
- LinkedIn is most aggressive — use Indeed/Naukri/Internshala first

---

## 📁 Project Structure

```
placement_tool/
├── app.py              ← Flask server (run this)
├── job_scraper.py      ← JobSpy + Indian site scrapers
├── contact_finder.py   ← Email/phone extraction
├── requirements.txt    ← Python dependencies
├── README.md           ← This file
└── templates/
    └── index.html      ← Web UI
```

---

Built for placement committees 🎓
