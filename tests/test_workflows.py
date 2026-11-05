"""The GitHub Actions scraper writes to the production database on purpose, so it must opt
past db_guard with ALLOW_REMOTE_DB=1. Without it every search dispatched from the live site
failed with "set ALLOW_REMOTE_DB=1 explicitly", and no jobs were saved."""
import os

import pytest

yaml = pytest.importorskip('yaml')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scraper_step():
    wf = yaml.safe_load(open(os.path.join(ROOT, '.github/workflows/scrape.yml'), encoding='utf-8'))
    for job in wf['jobs'].values():
        for step in job.get('steps', []):
            if 'scrape_worker.py' in str(step.get('run', '')):
                return step
    raise AssertionError('no step runs scrape_worker.py')


def test_ci_runs_the_tests_before_building_the_image():
    jobs = yaml.safe_load(open(os.path.join(ROOT, '.github/workflows/deploy.yml'), encoding='utf-8'))['jobs']
    testers = [n for n, j in jobs.items() if any('pytest' in str(s.get('run', '')) for s in j.get('steps', []))]
    needs = jobs['build'].get('needs', [])
    assert testers and set(testers) <= set([needs] if isinstance(needs, str) else needs)


def test_scraper_step_allows_its_production_writes():
    env = scraper_step().get('env') or {}
    assert str(env.get('ALLOW_REMOTE_DB')) == '1'


def test_scraper_step_saves_india_time():
    # Jobs saved here get their Added time from this clock; see test_dockerfile.
    env = scraper_step().get('env') or {}
    assert env.get('TZ') == 'IST-5:30'


def test_scraper_step_reads_the_database_from_a_secret():
    env = scraper_step().get('env') or {}
    assert '${{ secrets.MONGO_URI }}' in str(env.get('MONGO_URI'))
