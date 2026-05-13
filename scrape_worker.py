"""
scrape_worker.py — executed by GitHub Actions runner (2 CPU, 7 GB RAM)

Reads JOB_ID + PARAMS_JSON from environment variables.
Runs scraper + enrichment, saves results to MongoDB, updates job status.
"""
import os, json, sys, traceback

# ── Bootstrap ─────────────────────────────────────────────────────────────────
# MONGO_URI must be set before any database import
if not os.environ.get('MONGO_URI'):
    print('[ERROR] MONGO_URI not set')
    sys.exit(1)

from database import (init_db, get_settings, save_search, save_jobs,
                      update_search_count, get_custom_sites, update_scrape_job)
from job_scraper import scrape_jobs as do_scrape
from contact_finder import enrich_with_contacts


def _prog(job_id, pct, msg):
    update_scrape_job(job_id, progress=pct, message=msg)
    print(f'[{pct:3d}%] {msg}')


def main():
    job_id = os.environ.get('JOB_ID', '')
    raw    = os.environ.get('PARAMS_JSON', '{}')

    if not job_id:
        print('[ERROR] JOB_ID not set')
        sys.exit(1)

    params = json.loads(raw)
    search_id = params.get('search_id', '')

    init_db()
    settings = get_settings()

    try:
        update_scrape_job(job_id, status='running', progress=5,
                          message='GitHub Actions runner started. Scraping job boards...')

        save_search(search_id,
                    params.get('search_term', ''),
                    params.get('location', ''),
                    params.get('sites', []))

        # Inject live settings (serper key, custom sites)
        if settings.get('serper_api_key'):
            params['serper_api_key'] = settings['serper_api_key']
        params['custom_sites'] = get_custom_sites()

        def scrape_prog(p, m):
            _prog(job_id, 5 + int(p * 0.45), m)

        results = do_scrape(params, progress_callback=scrape_prog)
        print(f'[SCRAPE] {len(results)} raw jobs found')

        # Save raw results immediately as a safety net
        if results:
            save_jobs(results, search_id)

        _prog(job_id, 52, f'Found {len(results)} jobs. Finding HR contacts...')

        if params.get('fetch_contacts', True) and results:
            def enrich_prog(p, m):
                _prog(job_id, 52 + int(p * 0.44), m)
            results = enrich_with_contacts(results, settings=settings,
                                           progress_callback=enrich_prog)

        # Location priority sort
        priority_locs = [l.strip().lower()
                         for l in params.get('priority_locations', '').split(',')
                         if l.strip()]
        if priority_locs:
            def loc_score(job):
                loc = (job.get('location') or '').lower()
                for i, city in enumerate(priority_locs):
                    if city in loc:
                        return i
                return len(priority_locs)
            results.sort(key=lambda j: (
                loc_score(j),
                0 if j.get('emails') else 1,
                0 if j.get('phone') else 1,
            ))

        # Final save with enriched contacts
        save_jobs(results, search_id)
        update_search_count(search_id, len(results))

        stats = {
            'total':      len(results),
            'with_email': sum(1 for r in results if r.get('emails')),
            'with_phone': sum(1 for r in results if r.get('phone')),
            'sites':      list(set(r.get('site', '') for r in results)),
        }
        update_scrape_job(job_id,
                          status='done',
                          progress=100,
                          message=f'Done! {len(results)} jobs saved. '
                                  f'{stats["with_email"]} emails, {stats["with_phone"]} phones found.',
                          stats=stats,
                          count=len(results))

        print(f'[DONE] {len(results)} jobs | '
              f'{stats["with_email"]} emails | {stats["with_phone"]} phones')

    except Exception:
        tb = traceback.format_exc()
        print(f'[ERROR]\n{tb}')
        update_scrape_job(job_id, status='error',
                          message=tb.splitlines()[-1])
        sys.exit(1)


if __name__ == '__main__':
    main()
