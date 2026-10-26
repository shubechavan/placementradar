// Ready-made settings for popular job sites (copied unchanged from the previous version).
export const SITE_PRESETS = {
  "TimesJobs": {
    "base_url": "https://www.timesjobs.com",
    "search_url": "https://www.timesjobs.com/candidate/job-search.html?searchType=personalizedSearch&from=submit&txtKeywords={query}&txtLocation={location}&pDate=I&sequence=1&startPage=1",
    "card_sel": "li.clearfix.job-bx",
    "title_sel": "h2 a",
    "company_sel": ".joblist-comp-name",
    "location_sel": ".srp-skills",
    "salary_sel": "",
    "link_sel": "h2 a"
  },
  "iimjobs": {
    "base_url": "https://www.iimjobs.com",
    "search_url": "https://www.iimjobs.com/j/{query}-jobs",
    "card_sel": ".job-wrap",
    "title_sel": ".job-title a",
    "company_sel": ".company-name",
    "location_sel": ".job-exp-loc",
    "salary_sel": ".job-salary",
    "link_sel": ".job-title a"
  },
  "Hirist": {
    "base_url": "https://www.hirist.tech",
    "search_url": "https://www.hirist.tech/search?q={query}&l={location}",
    "card_sel": ".job-list-item",
    "title_sel": ".job-title",
    "company_sel": ".company-name",
    "location_sel": ".job-location",
    "salary_sel": ".job-salary",
    "link_sel": "a.job-title"
  },
  "Cutshort": {
    "base_url": "https://cutshort.io",
    "search_url": "https://cutshort.io/jobs#?q={query}",
    "card_sel": "",
    "title_sel": "",
    "company_sel": "",
    "location_sel": "",
    "salary_sel": "",
    "link_sel": ""
  },
  "Instahyre": {
    "base_url": "https://www.instahyre.com",
    "search_url": "https://www.instahyre.com/search-jobs/?designation={query}&location={location}",
    "card_sel": ".opportunity-card",
    "title_sel": ".opportunity-role",
    "company_sel": ".company-name",
    "location_sel": ".opportunity-location",
    "salary_sel": "",
    "link_sel": "a.opportunity-card-link"
  },
  "Freshersworld": {
    "base_url": "https://www.freshersworld.com",
    "search_url": "https://www.freshersworld.com/jobs/jobsearch/{query}-jobs-in-{location}",
    "card_sel": ".job-container",
    "title_sel": ".job-title-text",
    "company_sel": ".company-name",
    "location_sel": ".job-location",
    "salary_sel": ".salary",
    "link_sel": "a.job-title-text"
  },
  "HackerEarth": {
    "base_url": "https://www.hackerearth.com",
    "search_url": "https://www.hackerearth.com/jobs/search/?q={query}&location={location}",
    "card_sel": ".job-card",
    "title_sel": ".job-title",
    "company_sel": ".company-name",
    "location_sel": ".job-location",
    "salary_sel": "",
    "link_sel": "a.job-card-link"
  },
  "AngelList": {
    "base_url": "https://wellfound.com",
    "search_url": "https://wellfound.com/jobs?q={query}&l={location}",
    "card_sel": "[data-test=\"StartupResult\"]",
    "title_sel": "[data-test=\"JobTitle\"]",
    "company_sel": "[data-test=\"CompanyName\"]",
    "location_sel": "[data-test=\"Location\"]",
    "salary_sel": "[data-test=\"CompensationText\"]",
    "link_sel": "a[data-test=\"NameLink\"]"
  },
  "Remotive": {
    "base_url": "https://remotive.com",
    "search_url": "https://remotive.com/remote-jobs/{query}",
    "card_sel": "li.job-list-item",
    "title_sel": ".position h2",
    "company_sel": ".company",
    "location_sel": ".job-tag",
    "salary_sel": "",
    "link_sel": "a[href*=\"/remote-jobs/\"]"
  },
  "Turing": {
    "base_url": "https://www.turing.com",
    "search_url": "https://www.turing.com/jobs?query={query}",
    "card_sel": ".job-card",
    "title_sel": ".job-title",
    "company_sel": "",
    "location_sel": ".job-location",
    "salary_sel": ".job-salary",
    "link_sel": "a.job-card"
  }
};
export const PRESET_LABELS = {"HackerEarth": "HackerEarth Jobs", "AngelList": "Wellfound (AngelList)", "Remotive": "Remotive (remote)"};
