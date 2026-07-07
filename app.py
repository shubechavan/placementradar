"""
app.py — PlacementRadar v4 Flask backend
"""
from flask import Flask, render_template, request, jsonify, send_file
import threading, uuid, io, os, json
import requests as _req
import pandas as pd
from database import (init_db, get_settings, save_settings, save_search,
                      get_custom_sites, save_custom_site, delete_custom_site, toggle_custom_site,
                      update_search_count, get_searches, save_jobs,
                      get_all_jobs, update_job, get_dashboard_stats,
                      save_outreach, get_outreach,
                      get_jobs_without_contacts, update_job_contacts,
                      create_scrape_job, update_scrape_job, get_scrape_job,
                      get_scrape_results_by_search)

app = Flask(__name__)
init_db()

# ── Auth ──────────────────────────────────────────────────────────────────────
# When APP_API_KEY is set (production), every /api route requires the
# X-API-Key header. /api/dashboard stays open — it is the deploy health check
# and keepalive ping target, and exposes only aggregate counts.
APP_API_KEY = os.environ.get('APP_API_KEY', '')
if not APP_API_KEY:
    print('[WARN] APP_API_KEY not set — API is UNAUTHENTICATED (dev mode only)')

@app.before_request
def _require_api_key():
    if not APP_API_KEY:
        return                                    # dev mode — auth disabled
    if not request.path.startswith('/api'):
        return                                    # the SPA page itself
    if request.path == '/api/dashboard':
        return                                    # health check / keepalive
    if request.headers.get('X-API-Key') != APP_API_KEY:
        return jsonify({'error': 'unauthorized'}), 401

# In-memory tracker — used as fallback when GITHUB_PAT not set (local dev)
scrape_jobs = {}

GITHUB_OWNER = 'shubechavan'
GITHUB_REPO  = 'placementradar'

def _trigger_github_scrape(job_id, search_id, data):
    """Dispatch the scrape.yml workflow on GitHub Actions. Returns True on success."""
    token = os.environ.get('GH_PAT', '')
    if not token:
        return False  # no PAT → fall back to local thread
    params = {
        'search_id':          search_id,
        'search_term':        data.get('search_term', ''),
        'location':           data.get('location', ''),
        'sites':              data.get('sites', []),
        'results_wanted':     data.get('results_wanted', 20),
        'fetch_contacts':     data.get('fetch_contacts', True),
        'priority_locations': data.get('priority_locations', ''),
    }
    try:
        resp = _req.post(
            f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
            f'/actions/workflows/scrape.yml/dispatches',
            headers={
                'Authorization': f'token {token}',
                'Accept':        'application/vnd.github.v3+json',
            },
            json={'ref': 'main', 'inputs': {
                'job_id':      job_id,
                'params_json': json.dumps(params),
            }},
            timeout=10,
        )
        return resp.status_code == 204
    except Exception as e:
        print(f'[GitHub API] trigger failed: {e}')
        return False

# ── Pages ──────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.route('/api/dashboard')
def dashboard():
    return jsonify(get_dashboard_stats())

# ── Settings ───────────────────────────────────────────────────────────────────
SECRET_SETTINGS = ('gmail_app_password', 'serper_api_key')

@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    s = get_settings()
    # Mask every secret, not just the Gmail password
    for key in SECRET_SETTINGS:
        if s.get(key):
            s[key] = '••••••••'
    return jsonify(s)

@app.route('/api/settings', methods=['POST'])
def api_save_settings():
    data = request.json or {}
    # Don't overwrite secrets with their masked placeholder
    for key in SECRET_SETTINGS:
        if data.get(key) == '••••••••':
            data.pop(key)
    save_settings(data)
    return jsonify({'success': True})

@app.route('/api/settings/test-smtp', methods=['POST'])
def test_smtp():
    from email_sender import test_smtp as _test
    s = get_settings()
    # Use submitted values if provided
    body = request.json or {}
    if body.get('gmail_email'):    s['gmail_email']        = body['gmail_email']
    if body.get('gmail_app_password') and body['gmail_app_password'] != '••••••••':
        s['gmail_app_password'] = body['gmail_app_password']
    return jsonify(_test(s))

# ── Scraping ───────────────────────────────────────────────────────────────────
@app.route('/api/scrape', methods=['POST'])
def start_scrape():
    data      = request.json
    job_id    = str(uuid.uuid4())
    search_id = str(uuid.uuid4())

    # ── GitHub Actions path (production) ─────────────────────────────────────
    if _trigger_github_scrape(job_id, search_id, data):
        create_scrape_job(job_id, search_id, data)
        return jsonify({'job_id': job_id})

    # ── Local thread fallback (no GITHUB_PAT set — dev mode) ─────────────────
    scrape_jobs[job_id] = {'status':'running','progress':0,
                           'results':[],'message':'Initializing...','stats':{}}

    def run():
        from job_scraper import scrape_jobs as do_scrape
        from contact_finder import enrich_with_contacts
        settings = get_settings()

        def prog(p, m):
            scrape_jobs[job_id]['progress'] = p
            scrape_jobs[job_id]['message']  = m

        try:
            prog(5, 'Starting scraper...')
            save_search(search_id, data.get('search_term',''),
                        data.get('location',''), data.get('sites',[]))
            scrape_jobs[job_id]['search_id'] = search_id   # watchdog needs this

            import traceback as _tb
            # Inject Serper API key + enabled custom sites into scrape params
            settings = get_settings()
            if settings.get('serper_api_key'):
                data['serper_api_key'] = settings['serper_api_key']
            data['custom_sites'] = get_custom_sites()   # all enabled custom sites
            results = do_scrape(data, progress_callback=prog)
            # Store raw results immediately — watchdog can save these if timeout hits
            scrape_jobs[job_id]['results'] = results
            scrape_jobs[job_id]['debug']   = f'Scraped {len(results)} raw jobs'
            # Also persist raw jobs to DB right away as a safety net
            if results:
                save_jobs(results, search_id)

            if data.get('fetch_contacts', True) and results:
                prog(55, f'Found {len(results)} jobs. Finding contacts...')
                def _enrich_cb(p, m):
                    # Update in-memory only — multiple parallel threads call this
                    # concurrently so no DB write here (race condition → BulkWriteError)
                    scrape_jobs[job_id].update({
                        'progress': 55 + int(p * 0.43),
                        'message': m,
                        'results': results,
                    })
                results = enrich_with_contacts(
                    results, settings=settings,
                    progress_callback=_enrich_cb)


            # ── Location priority sort ────────────────────────────────
            priority_locs = [l.strip().lower() for l in
                             data.get('priority_locations', '').split(',') if l.strip()]
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
                for j in results:
                    rank = loc_score(j)
                    j['location_priority'] = f"#{rank+1}" if rank < len(priority_locs) else ''

            # Persist to DB
            save_jobs(results, search_id)
            update_search_count(search_id, len(results))
            scrape_jobs[job_id]['results'] = results
            scrape_jobs[job_id]['stats'] = {
                'total':      len(results),
                'with_email': sum(1 for r in results if r.get('emails')),
                'with_phone': sum(1 for r in results if r.get('phone')),
                'sites':      list(set(r.get('site','') for r in results))
            }
            scrape_jobs[job_id]['status']   = 'done'
            scrape_jobs[job_id]['progress'] = 100
            scrape_jobs[job_id]['message']  = f'Done! {len(results)} jobs saved.'
        except Exception as e:
            scrape_jobs[job_id]['status']  = 'error'
            tb = _tb.format_exc()
            scrape_jobs[job_id]['message'] = f'Error: {str(e)}'
            scrape_jobs[job_id]['traceback'] = tb
            print(f'[SCRAPE ERROR] {tb}'.encode('ascii', errors='replace').decode('ascii'))

    def watchdog():
        import time as _t
        start   = _t.time()
        last_p  = -1
        stall   = 0
        HARD_LIMIT  = 360  # 6 min: ~60s scrape + ~90s enrich (4 parallel workers) + buffer
        STALL_LIMIT = 120  # 2 min no-progress before aborting (allows slow enrichment batches)
        while scrape_jobs[job_id]['status'] == 'running':
            _t.sleep(10)
            elapsed = _t.time() - start
            cur     = scrape_jobs[job_id]['progress']

            # Hard wall — save whatever we have and mark done (not error)
            if elapsed > HARD_LIMIT:
                partial = scrape_jobs[job_id].get('results', [])
                if partial:
                    # Save to DB and surface as done with partial results
                    try:
                        save_jobs(partial, scrape_jobs[job_id].get('search_id', ''))
                    except Exception:
                        pass
                    scrape_jobs[job_id]['status']   = 'done'
                    scrape_jobs[job_id]['progress'] = 100
                    scrape_jobs[job_id]['count']    = len(partial)
                    scrape_jobs[job_id]['message']  = (
                        f'Partial results ({len(partial)} jobs) — '
                        'one or more sources took too long and were skipped.')
                    scrape_jobs[job_id]['stats'] = {
                        'total':      len(partial),
                        'with_email': sum(1 for r in partial if r.get('emails')),
                        'with_phone': sum(1 for r in partial if r.get('phone')),
                        'sites':      list(set(r.get('site','') for r in partial)),
                    }
                else:
                    scrape_jobs[job_id]['status']  = 'error'
                    scrape_jobs[job_id]['message'] = (
                        'No results yet — sources are slow or blocking. '
                        'Try: Google Jobs + Internshala + Naukri only.')
                break

            # Stall detection — no progress for 90s
            if cur == last_p:
                stall += 10
                if stall >= STALL_LIMIT:
                    # Don't error — save whatever results we have so far
                    partial = scrape_jobs[job_id].get('results', [])
                    if partial:
                        scrape_jobs[job_id]['status']  = 'done'
                        scrape_jobs[job_id]['message'] = (
                            f'Partial results — one source timed out. '
                            f'{len(partial)} jobs found.')
                        scrape_jobs[job_id]['count']   = len(partial)
                    else:
                        scrape_jobs[job_id]['status']  = 'error'
                        scrape_jobs[job_id]['message'] = (
                            'A job site is blocking or slow. '
                            'Try: deselect LinkedIn/Indeed, use only Internshala + Naukri + Google Jobs.')
                    break
            else:
                stall  = 0
                last_p = cur

    threading.Thread(target=run,      daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/status/<job_id>')
def get_status(job_id):
    # Check MongoDB first (GitHub Actions jobs)
    doc = get_scrape_job(job_id)
    if doc:
        return jsonify({
            'status':   doc.get('status', 'queued'),
            'progress': doc.get('progress', 0),
            'message':  doc.get('message', ''),
            'count':    doc.get('count', 0),
            'stats':    doc.get('stats', {}),
        })
    # Fall back to in-memory (local dev thread mode)
    j = scrape_jobs.get(job_id)
    if not j: return jsonify({'error': 'Not found'}), 404
    return jsonify({'status':j['status'],'progress':j['progress'],
                    'message':j['message'],'count':len(j.get('results',[])),
                    'stats':j.get('stats',{})})

@app.route('/api/results/<job_id>')
def get_results(job_id):
    # Check MongoDB first (GitHub Actions jobs)
    doc = get_scrape_job(job_id)
    if doc:
        results = get_scrape_results_by_search(doc.get('search_id', ''))
        return jsonify({'results': results})
    # Fall back to in-memory (local dev)
    j = scrape_jobs.get(job_id)
    if not j: return jsonify({'error': 'Not found'}), 404
    return jsonify({'results': j.get('results', [])})

@app.route('/api/debug/<job_id>')
def debug_job(job_id):
    # Tracebacks leak file paths and internals — never serve them in production
    if APP_API_KEY and os.environ.get('FLASK_ENV') != 'development':
        return jsonify({'error': 'Not found'}), 404
    j = scrape_jobs.get(job_id)
    if not j: return jsonify({'error':'Not found'}), 404
    return jsonify({
        'status':    j.get('status'),
        'message':   j.get('message'),
        'debug':     j.get('debug',''),
        'traceback': j.get('traceback',''),
        'count':     len(j.get('results',[])),
        'stats':     j.get('stats',{}),
    })

# ── Custom Sites ──────────────────────────────────────────────────────────────
@app.route('/api/custom-sites', methods=['GET'])
def api_get_custom_sites():
    return jsonify({'sites': get_custom_sites()})

@app.route('/api/custom-sites', methods=['POST'])
def api_save_custom_site():
    site = request.json or {}
    for url_field in ('base_url', 'search_url'):
        u = (site.get(url_field) or '').strip()
        if u and not _is_safe_url(u):
            return jsonify({'success': False,
                            'error': f'{url_field} must be a public http(s) URL'}), 400
    sid  = save_custom_site(site)
    return jsonify({'success': True, 'id': sid})

@app.route('/api/custom-sites/<site_id>', methods=['DELETE'])
def api_delete_custom_site(site_id):
    delete_custom_site(site_id)
    return jsonify({'success': True})

@app.route('/api/custom-sites/<site_id>/toggle', methods=['POST'])
def api_toggle_custom_site(site_id):
    enabled = (request.json or {}).get('enabled', True)
    toggle_custom_site(site_id, enabled)
    return jsonify({'success': True})

def _is_safe_url(url):
    """Block SSRF — only allow public http(s) hosts, never private/loopback IPs."""
    from urllib.parse import urlparse
    import ipaddress, socket
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https') or not p.hostname:
            return False
        ip = ipaddress.ip_address(socket.gethostbyname(p.hostname))
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except Exception:
        return False

@app.route('/api/custom-sites/test', methods=['POST'])
def api_test_custom_site():
    """Test a custom site config by scraping 3 jobs and returning them."""
    from job_scraper import scrape_custom_site
    cfg   = request.json or {}
    query = cfg.pop('test_query', 'software engineer')
    loc   = cfg.pop('test_location', 'India')
    for url_field in ('base_url', 'search_url'):
        u = (cfg.get(url_field) or '').strip()
        if u and not _is_safe_url(u):
            return jsonify({'success': False,
                            'error': f'{url_field} must be a public http(s) URL'})
    try:
        jobs = scrape_custom_site(cfg, query, loc, count=5)
        return jsonify({'success': True, 'count': len(jobs),
                        'preview': jobs[:3]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ── Job Board ──────────────────────────────────────────────────────────────────
@app.route('/api/jobs')
def api_jobs():
    filters = {
        'status':    request.args.get('status',''),
        'site':      request.args.get('site',''),
        'has_email': request.args.get('has_email',''),
        'search':    request.args.get('search',''),
    }
    filters = {k:v for k,v in filters.items() if v}
    return jsonify({'jobs': get_all_jobs(filters)})

@app.route('/api/jobs/<job_id>', methods=['PATCH'])
def patch_job(job_id):
    data = request.json or {}
    for field in ('status','notes','score'):
        if field in data:
            update_job(job_id, field, data[field])
    return jsonify({'success': True})

# ── Outreach ───────────────────────────────────────────────────────────────────
@app.route('/api/outreach/send', methods=['POST'])
def send_email():
    from email_sender import send_outreach_email, build_subject, _fill_template
    data     = request.json or {}
    settings = get_settings()

    job_data = {'company': data.get('company',''), 'title': data.get('title','')}
    template_key = data.get('template', 'outreach_template')
    body_tpl = settings.get(template_key, settings.get('outreach_template',''))

    subject = data.get('subject') or build_subject(data.get('company',''), settings)
    body    = data.get('body')    or _fill_template(body_tpl, job_data, settings)

    result  = send_outreach_email(data.get('email',''), subject, body, settings)

    if result.get('success'):
        save_outreach(data.get('job_id',''), data.get('company',''),
                      data.get('email',''), subject, body)
        update_job(data.get('job_id',''), 'status', 'contacted')

    return jsonify(result)

@app.route('/api/outreach/bulk', methods=['POST'])
def bulk_send():
    from email_sender import send_bulk_emails, build_subject
    data      = request.json or {}
    settings  = get_settings()
    recipients= data.get('recipients', [])
    template  = data.get('template', settings.get('outreach_template',''))
    results   = send_bulk_emails(recipients,
                                 'Campus Hiring — {{college_name}}',
                                 template, settings)
    for r in results:
        if r.get('success'):
            save_outreach(r.get('job_id',''), r.get('company',''),
                          r.get('email',''), '', '')
            if r.get('job_id'): update_job(r['job_id'], 'status', 'contacted')
    return jsonify({'results': results})

@app.route('/api/outreach/log')
def outreach_log():
    return jsonify({'outreach': get_outreach(100)})

# ── Re-enrich existing jobs ───────────────────────────────────────────────────
@app.route('/api/jobs/enrich', methods=['POST'])
def enrich_existing_jobs():
    """Find contacts for DB jobs that currently have no email and no phone."""
    job_id = str(uuid.uuid4())
    scrape_jobs[job_id] = {'status': 'running', 'progress': 0,
                           'message': 'Loading jobs without contacts...', 'results': [], 'stats': {}}

    def run():
        from contact_finder import enrich_with_contacts
        import traceback as _tb

        def prog(p, m):
            scrape_jobs[job_id]['progress'] = p
            scrape_jobs[job_id]['message']  = m

        try:
            jobs = get_jobs_without_contacts(limit=300)
            if not jobs:
                scrape_jobs[job_id].update({
                    'status': 'done', 'progress': 100,
                    'message': 'All jobs already have contacts — nothing to do.',
                })
                return

            prog(5, f'Running enrichment on {len(jobs)} jobs without contacts...')
            enriched = enrich_with_contacts(jobs, settings=get_settings(), progress_callback=prog)

            found = 0
            for j in enriched:
                jid = j.get('id', '')
                em  = (j.get('emails') or '').strip()
                ph  = (j.get('phone') or '').strip()
                if em or ph:
                    update_job_contacts(jid, em, ph,
                                        j.get('hr_contact', ''),
                                        j.get('email_source', ''))
                    found += 1

            scrape_jobs[job_id].update({
                'status':   'done',
                'progress': 100,
                'message':  f'Found contacts for {found} of {len(jobs)} jobs.',
                'stats':    {'total': len(jobs), 'found': found},
            })
        except Exception as e:
            scrape_jobs[job_id].update({
                'status':    'error',
                'message':   f'Enrichment error: {str(e)}',
                'traceback': _tb.format_exc(),
            })
            print(f'[ENRICH ERROR] {_tb.format_exc()}')

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


# ── Export ────────────────────────────────────────────────────────────────────
@app.route('/api/export')
def export_csv():
    source = request.args.get('source', 'board')  # board or scrape
    job_id = request.args.get('job_id', '')

    if source == 'scrape' and job_id and job_id in scrape_jobs:
        rows = scrape_jobs[job_id].get('results', [])
    else:
        rows = get_all_jobs()

    if not rows:
        return jsonify({'error': 'No data'}), 400

    df = pd.DataFrame(rows)
    preferred = ['title','company','location','job_type','salary','site',
                 'date_posted','emails','hr_contact','email_source',
                 'phone','status','notes','score','job_url','company_url','description']
    cols = [c for c in preferred if c in df.columns] + \
           [c for c in df.columns if c not in preferred]
    df   = df[[c for c in cols if c in df.columns]]

    out = io.BytesIO()
    df.to_csv(out, index=False, encoding='utf-8-sig')
    out.seek(0)
    return send_file(out, mimetype='text/csv', as_attachment=True,
                     download_name='placement_jobs.csv')

# ── HR Database ───────────────────────────────────────────────────────────────
@app.route('/api/hr-database')
def hr_database():
    from hr_database_lookup import get_all, stats, search
    q = request.args.get('q','').strip()
    if q:
        rows = search(q)
    else:
        rows = get_all()
    return jsonify({'contacts': rows, 'stats': stats()})

@app.route('/api/hr-database/add', methods=['POST'])
def hr_database_add():
    from hr_database_lookup import add_entry
    data = request.json or {}
    ok   = add_entry(
        data.get('company',''), data.get('hr_email',''),
        data.get('hr_name',''), data.get('designation','HR'),
        data.get('city',''), 'Manual'
    )
    return jsonify({'success': ok})


if __name__ == '__main__':
    print('\n[*] PlacementRadar v4  ->  http://localhost:5000\n')
    app.run(debug=False, port=5000, threaded=True, use_reloader=False)
