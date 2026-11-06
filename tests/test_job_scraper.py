from datetime import date, datetime

import pandas as pd
import pytest

import job_scraper as J
import source_metrics as SM
from conftest import FakeResponse


@pytest.mark.parametrize('term, fields, expected', [
    ('software engineer', ['Senior Engineer, Software'], True),     # order-free
    ('software engineer', ['Software Developer'], False),           # every word needed
    ('data analyst', ['Analyst', 'data sql'], True),                # tags count
    ('c++ developer', ['C++ Developer'], True),
    ('', ['anything'], True),
])
def test_matches_terms(term, fields, expected):
    assert J._matches_terms(term, *fields) is expected


@pytest.mark.parametrize('title, expected', [
    ('UX/UI Designer', True),
    ('Salesforce Developer', True),              # 'sales' must not reject it
    ('Software Engineering Intern', True),
    ('Fashion Designer', False),                 # deny-list wins over 'designer'
    ('Management Trainee - Marketing & Sales', False),
    ('Telecaller', False),
])
def test_is_tech_role(title, expected):
    assert J._is_tech_role(title) is expected


@pytest.mark.parametrize('value, expected', [
    (date(2026, 10, 24), '2026-10-24'),
    (datetime(2026, 10, 24, 13, 5), '2026-10-24'),
    ('2026-10-24', '2026-10-24'),
    (None, ''),
    (float('nan'), ''),                          # pandas fills a missing date with NaN or NaT
    (pd.NaT, ''),
    ('nan', ''),
    (1793883611, '2026-11-05'),                  # Himalayas sends Unix seconds
])
def test_iso_date(value, expected):
    assert J._iso_date(value) == expected


@pytest.mark.parametrize('label, expected', [
    ('Today', '2026-11-06'),
    ('Few hours ago', '2026-11-06'),
    ('1 day ago', '2026-11-05'),
    ('5 days ago', '2026-11-01'),
    ('3 weeks ago', ''),                         # a date from this would look exact; it isn't
])
def test_internshala_posted_label(label, expected):
    from bs4 import BeautifulSoup
    card = BeautifulSoup('<div class="individual_internship"><div class="status-li">Fresher Job</div>'
                         f'<div class="status-success">{label}</div></div>', 'lxml')
    assert J._internshala_posted(card, today=date(2026, 11, 6)) == expected


def test_jobspy_keeps_the_date_the_board_posted_the_job(monkeypatch):
    import jobspy
    df = pd.DataFrame([
        {'title': 'Data Analyst', 'company': 'Acme', 'job_url': 'https://jobs.example/1',
         'site': 'indeed', 'date_posted': date(2026, 10, 24)},
        {'title': 'Java Developer', 'company': 'Beta', 'job_url': 'https://jobs.example/2',
         'site': 'linkedin', 'date_posted': None},
    ])
    monkeypatch.setattr(jobspy, 'scrape_jobs', lambda **kw: df)
    rows = J.scrape_jobspy('indeed', 'data analyst', 'Mumbai', 5)
    assert [r['date_posted'] for r in rows] == ['2026-10-24', '']


def test_tracked_board_counts_empty_result_as_failure():
    J._tracked_board('demo', lambda: [])()
    row = SM.snapshot()['board:demo']
    assert row.get('attempts') == 1 and row.get('failures') == 1 and not row.get('successes')


def test_tracked_board_records_jobs_and_reraises_errors():
    rows = J._tracked_board('ok', lambda: [{'title': 'x'}] * 3)()
    assert len(rows) == 3 and SM.snapshot()['board:ok']['contacts_found'] == 3

    def boom():
        raise TimeoutError('slow')
    with pytest.raises(TimeoutError):
        J._tracked_board('slow', boom)()
    assert SM.snapshot()['board:slow'].get('timeouts') == 1


def test_remoteok_filters_by_words_and_leaves_company_url_empty(fake_get):
    feed = [
        {'legal': 'notice'},
        {'position': 'Senior Engineer, Software', 'company': 'Acme', 'tags': ['python'],
         'company_logo': 'https://img.example/logo.png', 'slug': 'a', 'date': '2026-11-04T15:09:09+00:00'},
        {'position': 'Account Manager', 'company': 'Other', 'tags': ['sales'], 'slug': 'b'},
    ]
    fake_get(lambda url, params: FakeResponse(200, feed))
    rows = J.scrape_remoteok('software engineer', '', 20)
    assert [r['company'] for r in rows] == ['Acme']
    assert rows[0]['company_url'] == ''          # the logo is not the website
    assert rows[0]['date_posted'] == '2026-11-04'


def test_himalayas_reads_flat_company_name(fake_get):
    payload = {'jobs': [{'title': 'Data Analyst', 'companyName': 'Acme', 'companySlug': 'acme',
                         'locationRestrictions': ['India'], 'employmentType': 'Full Time',
                         'pubDate': 1793883611}]}
    seen = {}

    def handler(url, params):
        seen['url'] = url
        return FakeResponse(200, payload)
    fake_get(handler)
    rows = J.scrape_himalayas('data analyst', '', 20)
    assert seen['url'].endswith('/jobs/api/search')
    assert rows[0]['company'] == 'Acme' and rows[0]['location'] == 'India'
    assert rows[0]['date_posted'] == '2026-11-05'


def test_remotive_drops_jobs_the_api_returned_off_query(fake_get):
    payload = {'jobs': [{'title': 'Data Analyst', 'company_name': 'Acme', 'tags': [],
                         'publication_date': '2026-11-02T20:01:00'},
                        {'title': 'Remote Office Assistant', 'company_name': 'B', 'tags': []}]}
    fake_get(lambda url, params: FakeResponse(200, payload))
    rows = J.scrape_remotive('data analyst', '', 20)
    assert [r['company'] for r in rows] == ['Acme'] and rows[0]['company_url'] == ''
    assert rows[0]['date_posted'] == '2026-11-02'


def test_internshala_drops_cards_with_nothing_in_common_with_the_search(fake_get):
    # Measured live 6 Nov 2026: a "data analyst" search returned 40 cards, including
    # Telecaller, Admission Counsellor and Sales Executive — Internshala's own search
    # mixes in results with no relation to the query, and nothing in the scraper
    # checked each card against the term (only RemoteOK and Remotive did).
    html = '''<div class="individual_internship">
      <div class="job-internship-name">Data Analyst</div><div class="company-name">Acme Analytics</div></div>
    <div class="individual_internship">
      <div class="job-internship-name">Telecaller</div><div class="company-name">CallCo</div></div>'''

    # FakeResponse.url defaults to '', which redirected_to_generic() reads as path '/' —
    # a real generic listing. Set it to the requested URL so gate 1 judges it correctly.
    def handler(url, params):
        resp = FakeResponse(200, text=html)
        resp.url = url
        return resp
    fake_get(handler)
    rows = J.scrape_internshala('data analyst', 'India', 20)
    assert [r['title'] for r in rows] == ['Data Analyst']


def test_internshala_still_falls_through_a_mostly_noise_page_to_a_better_one(fake_get):
    # Regression for a fix that filtered cards BEFORE judging the page: a page with 1
    # relevant card out of 20 looked 100% relevant once the other 19 were stripped, so
    # the scraper settled for it instead of trying the next, much better candidate.
    weak = '<div class="individual_internship"><div class="job-internship-name">Data Analyst</div>' \
          '<div class="company-name">Acme</div></div>' + \
          '<div class="individual_internship"><div class="job-internship-name">Telecaller</div>' \
          '<div class="company-name">CallCo</div></div>' * 19
    strong = ''.join(f'<div class="individual_internship"><div class="job-internship-name">Data Analyst</div>'
                     f'<div class="company-name">Co{i}</div></div>' for i in range(5))

    def handler(url, params):
        resp = FakeResponse(200, text=weak if url.endswith('-jobs') else strong)
        resp.url = url
        return resp
    fake_get(handler)
    rows = J.scrape_internshala('data analyst', 'India', 20)
    assert [r['company'] for r in rows] == [f'Co{i}' for i in range(5)]


def test_naukri_records_captcha_block(fake_get):
    fake_get(lambda url, params: FakeResponse(406, {'message': 'recaptcha required'}, text='<html></html>'))
    assert J.scrape_naukri('java developer', 'Pune', 10) == []
    assert SM.snapshot()['board:naukri'].get('http_406') == 1
