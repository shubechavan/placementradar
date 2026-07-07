"""
database.py — MongoDB persistence for PlacementRadar
Replaces SQLite. All data survives restarts and app updates.

Collections:
  jobs        — scraped job listings (upserted by job_url to avoid dupes)
  searches    — search run history
  outreach    — emails sent log
  settings    — app config key/value
  hr_contacts — auto-discovered HR contacts (mirrors hr_database.csv logic)
"""

import os, uuid, re, math
from datetime import datetime, timedelta
from pymongo import MongoClient, DESCENDING, UpdateOne
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError


def _sanitize_doc(obj):
    """Replace NaN/Infinity floats with None so jsonify() produces valid JSON."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_doc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_doc(v) for v in obj]
    return obj

# ── Connection ────────────────────────────────────────────────────────────────
# No hardcoded fallback — credentials must come from the environment.
MONGO_URI = os.environ.get('MONGO_URI', '')
if not MONGO_URI:
    raise RuntimeError(
        'MONGO_URI environment variable is not set. '
        'Set it in .env (local), Render dashboard (production), '
        'or GitHub Secrets (Actions worker).'
    )

_client = None
_db     = None

def _get_db():
    global _client, _db
    if _db is not None:
        return _db
    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        _client.admin.command('ping')
        _db = _client['jobs']
        _ensure_indexes(_db)
        print('[MongoDB] Connected OK')
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        print(f'[MongoDB] Connection failed: {e}')
        raise
    return _db



# ── Field sanitizer — prevents "nan"/"None" strings entering MongoDB ──────────
def _clean(val, default=''):
    """Convert NaN, None, float nan, and the string 'nan'/'none' to default."""
    if val is None: return default
    s = str(val).strip()
    return default if s.lower() in ('nan', 'none', 'nat', '') else s

def _clean_int(val, default=0):
    try: return int(val)
    except: return default


def _ensure_indexes(db):
    db.jobs.create_index('job_url', sparse=True)
    db.jobs.create_index([('company', 1), ('title', 1)])
    db.jobs.create_index('created_at')
    db.searches.create_index('created_at')
    db.outreach.create_index('sent_at')
    db.settings.create_index('key', unique=True)
    try:
        db.hr_contacts.create_index('hr_email', unique=True)
    except Exception:
        pass


def init_db():
    db = _get_db()
    defaults = {
        'gmail_email':        '',
        'gmail_app_password': '',
        'college_name':       'Our College',
        'committee_name':     'Placement Committee',
        'committee_email':    '',
        'scheduler_enabled':  'false',
        'scheduler_time':     '09:00',
        'scheduler_email':    '',
        'serper_api_key':    os.environ.get('SERPER_API_KEY', ''),
        'outreach_template': (
            'Dear HR Team,\n\nI am writing from the Placement Committee of {{college_name}}. '
            'We have highly qualified final-year students graduating in 2025 across Engineering, '
            'MBA, and other disciplines.\n\nWe would love to explore campus hiring / referral '
            'opportunities with {{company}}. Please find our brochure attached.\n\n'
            'Looking forward to hearing from you.\n\nBest regards,\n'
            '{{committee_name}}\n{{college_name}}\n{{committee_email}}'
        ),
        'followup_template': (
            'Dear HR Team,\n\nI am following up on my earlier email regarding campus '
            'hiring at {{company}}.\n\nWe have strong candidates in various domains and '
            'would appreciate your consideration. Please let us know if you require any '
            'further information.\n\nBest regards,\n{{committee_name}}\n{{college_name}}'
        ),
    }
    for k, v in defaults.items():
        db.settings.update_one(
            {'key': k},
            {'$setOnInsert': {'key': k, 'value': v}},
            upsert=True
        )

    # One-time fix: scrub NaN float values from text fields in the jobs collection.
    # NaN is invalid JSON, causing the board to fail to load.
    import math as _math
    _nan = float('nan')
    _str_fields = ['notes', 'emails', 'phone', 'hr_contact', 'email_source',
                   'title', 'company', 'location', 'job_type', 'salary', 'site']
    for field in _str_fields:
        try:
            db.jobs.update_many({field: _nan}, {'$set': {field: ''}})
        except Exception:
            pass
    try:
        db.jobs.update_many({'score': _nan}, {'$set': {'score': 0}})
    except Exception:
        pass


# ── Settings ──────────────────────────────────────────────────────────────────
def get_settings():
    db   = _get_db()
    rows = db.settings.find({}, {'_id': 0})
    out  = {r['key']: r['value'] for r in rows}
    # Env vars take precedence over DB-stored secrets so credentials can be
    # kept out of the database entirely in production.
    for env_key, setting_key in (('GMAIL_APP_PASSWORD', 'gmail_app_password'),
                                 ('SERPER_API_KEY',     'serper_api_key')):
        if os.environ.get(env_key):
            out[setting_key] = os.environ[env_key]
    return out


def save_settings(data: dict):
    db  = _get_db()
    ops = [UpdateOne({'key': k}, {'$set': {'value': v}}, upsert=True)
           for k, v in data.items()]
    if ops:
        db.settings.bulk_write(ops)


# ── Searches ──────────────────────────────────────────────────────────────────
def save_search(search_id, term, location, sites):
    db = _get_db()
    db.searches.insert_one({
        '_id':         search_id,
        'search_term': term,
        'location':    location,
        'sites':       sites,
        'created_at':  datetime.now().isoformat(),
        'job_count':   0,
    })


def update_search_count(search_id, count):
    _get_db().searches.update_one({'_id': search_id}, {'$set': {'job_count': count}})


def get_searches(limit=20):
    rows = _get_db().searches.find(
        {}, {'_id': 1, 'search_term': 1, 'location': 1, 'sites': 1,
             'created_at': 1, 'job_count': 1},
        sort=[('created_at', DESCENDING)]
    ).limit(limit)
    out = []
    for r in rows:
        r['id'] = str(r.pop('_id'))
        out.append(r)
    return out


# ── Jobs ──────────────────────────────────────────────────────────────────────
def save_jobs(jobs: list, search_id: str):
    """
    Upsert by job_url — same URL always updates contact info, never duplicates.
    New jobs get a fresh UUID; returning jobs keep their status/notes/score.
    Existing contacts are never overwritten with empty — only improved.
    """
    db  = _get_db()
    now = datetime.now().isoformat()
    ops = []
    for j in jobs:
        url = (j.get('job_url') or '').strip()
        doc = {
            'search_id':    search_id,
            'title':        _clean(j.get('title')),
            'company':      _clean(j.get('company')),
            'location':     _clean(j.get('location')),
            'job_type':     _clean(j.get('job_type')),
            'salary':       _clean(j.get('salary')),
            'description':  _clean(j.get('description')),
            'job_url':      url,
            'site':         _clean(j.get('site')),
            'date_posted':  _clean(j.get('date_posted')),
            'company_url':  _clean(j.get('company_url')),
        }
        # Only overwrite contact fields when the new scrape actually found something.
        # This prevents a re-scrape (with no contacts) from wiping previously saved contacts.
        new_emails = _clean(j.get('emails'))
        new_phone  = _clean(j.get('phone'))
        if new_emails:
            doc['emails']       = new_emails
            doc['hr_contact']   = _clean(j.get('hr_contact'))
            doc['email_source'] = _clean(j.get('email_source'))
        if new_phone:
            doc['phone'] = new_phone

        filter_key = {'job_url': url} if url else {'_id': str(uuid.uuid4())}
        # $setOnInsert must NOT include fields that are also conditionally in $set
        # (emails, phone, hr_contact, email_source) — MongoDB 5.0+ throws error 40
        # (ConflictingOperationInUpdate) when the same path appears in both operators.
        # Contact fields are absent from new docs until enrichment finds them.
        ops.append(UpdateOne(
            filter_key,
            {
                '$set': doc,
                '$setOnInsert': {
                    '_id':        str(uuid.uuid4()),
                    'status':     'new',
                    'notes':      '',
                    'score':      0,
                    'created_at': now,
                }
            },
            upsert=True
        ))
    if ops:
        try:
            db.jobs.bulk_write(ops, ordered=False)
        except Exception as bulk_err:
            print(f'[DB] bulk_write failed, retrying one-by-one')
            for op in ops:
                try:
                    db.jobs.bulk_write([op], ordered=False)
                except Exception as e:
                    print(f'[DB] single write failed: {e}')


def get_all_jobs(filters=None):
    db    = _get_db()
    query = {}
    if filters:
        if filters.get('status'):
            query['status'] = filters['status']
        if filters.get('site'):
            query['site'] = filters['site']
        if filters.get('has_email'):
            query['emails'] = {'$nin': ['', 'nan', None]}
        if filters.get('search'):
            # re.escape — raw user input in $regex allows ReDoS / filter injection
            s = re.escape(filters['search'])
            query['$or'] = [
                {'title':    {'$regex': s, '$options': 'i'}},
                {'company':  {'$regex': s, '$options': 'i'}},
                {'location': {'$regex': s, '$options': 'i'}},
            ]
    # Sort: jobs with contacts always first, then newest first within each group
    # This ensures the board leads with actionable rows
    pipeline = [
        {'$match': query},
        {'$addFields': {
            '_has_contact': {
                '$cond': [
                    {'$or': [
                        {'$and': [{'$ne': ['$emails', '']},  {'$ne': ['$emails', None]},  {'$ne': ['$emails', 'nan']}]},
                        {'$and': [{'$ne': ['$phone',  '']},  {'$ne': ['$phone',  None]},  {'$ne': ['$phone',  'nan']}]},
                    ]},
                    0,   # has contact → sort first
                    1    # no contact  → sort after
                ]
            }
        }},
        {'$sort':    {'_has_contact': 1, 'created_at': -1}},
        {'$project': {'description': 0, '_has_contact': 0}},
    ]
    rows = db.jobs.aggregate(pipeline)
    out = []
    for r in rows:
        r['id'] = str(r.pop('_id'))
        out.append(_sanitize_doc(r))
    return out


def update_job(job_id, field, value):
    _get_db().jobs.update_one({'_id': job_id}, {'$set': {field: value}})


def get_jobs_without_contacts(limit=200):
    """Return jobs that have no email and no phone, including description for enrichment."""
    db = _get_db()
    query = {
        '$and': [
            {'$or': [{'emails': {'$in': ['', 'nan', None]}}, {'emails': {'$exists': False}}]},
            {'$or': [{'phone':  {'$in': ['', 'nan', None]}}, {'phone':  {'$exists': False}}]},
        ]
    }
    rows = db.jobs.find(query, sort=[('created_at', DESCENDING)]).limit(limit)
    out = []
    for r in rows:
        r['id'] = str(r.pop('_id'))
        out.append(r)
    return out


def update_job_contacts(job_id: str, emails: str, phone: str,
                        hr_contact: str = '', email_source: str = ''):
    """Update only the contact fields for an existing job."""
    update = {}
    if emails:
        update['emails']       = emails
        update['hr_contact']   = hr_contact
        update['email_source'] = email_source
    if phone:
        update['phone'] = phone
    if update:
        _get_db().jobs.update_one({'_id': job_id}, {'$set': update})


def get_dashboard_stats():
    db    = _get_db()
    stats = {}
    stats['total_jobs']      = db.jobs.count_documents({})
    stats['with_email']      = db.jobs.count_documents({'emails': {'$nin': ['', 'nan', None]}})
    stats['with_phone']      = db.jobs.count_documents({'phone': {'$nin': ['', 'nan', None]}})
    stats['contacted']       = db.jobs.count_documents({'status': 'contacted'})
    stats['replied']         = db.jobs.count_documents({'status': 'replied'})
    stats['drive_scheduled'] = db.jobs.count_documents({'status': 'drive_scheduled'})
    stats['emails_sent']     = db.outreach.count_documents({'status': 'sent'})

    stats['by_site']   = [{'site': r['_id'], 'count': r['count']} for r in
                           db.jobs.aggregate([{'$group': {'_id': '$site', 'count': {'$sum': 1}}},
                                              {'$sort': {'count': -1}}])]
    stats['by_status'] = [{'status': r['_id'], 'count': r['count']} for r in
                           db.jobs.aggregate([{'$group': {'_id': '$status', 'count': {'$sum': 1}}}])]
    stats['recent_searches'] = get_searches(5)
    stats['top_companies']   = [{'company': r['_id'], 'count': r['count']} for r in
                                 db.jobs.aggregate([{'$group': {'_id': '$company', 'count': {'$sum': 1}}},
                                                    {'$sort': {'count': -1}}, {'$limit': 10}])]
    return stats


# ── Outreach ──────────────────────────────────────────────────────────────────
def save_outreach(job_id, company, hr_email, subject, body, status='sent'):
    _get_db().outreach.insert_one({
        '_id':            str(uuid.uuid4()),
        'job_id':         job_id,
        'company':        company,
        'hr_email':       hr_email,
        'subject':        subject,
        'body':           body,
        'sent_at':        datetime.now().isoformat(),
        'status':         status,
        'reply_received': 0,
    })


def get_followups_due(days=3, max_followups=2):
    """
    Jobs that were emailed >= `days` ago, are still status 'contacted' (no reply),
    and have had fewer than `max_followups` follow-ups sent.
    Returns jobs sorted oldest-contact-first, each with last_sent / sends /
    followup_email fields attached.
    """
    db = _get_db()
    agg = db.outreach.aggregate([
        {'$match': {'job_id': {'$nin': ['', None]}, 'status': 'sent'}},
        {'$group': {'_id':       '$job_id',
                    'last_sent': {'$max': '$sent_at'},
                    'sends':     {'$sum': 1},
                    'hr_email':  {'$last': '$hr_email'}}},
    ])
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    # sends includes the initial email, so follow-ups sent = sends - 1
    due = {r['_id']: r for r in agg
           if r['last_sent'] <= cutoff and (r['sends'] - 1) < max_followups}
    if not due:
        return []
    rows = db.jobs.find({'_id': {'$in': list(due.keys())}, 'status': 'contacted'},
                        {'description': 0})
    out = []
    for j in rows:
        j['id'] = str(j.pop('_id'))
        meta = due[j['id']]
        j['last_sent'] = meta['last_sent']
        j['sends']     = meta['sends']
        j['followup_email'] = (meta.get('hr_email')
                               or j.get('hr_contact')
                               or (j.get('emails') or '').split(', ')[0].strip())
        out.append(_sanitize_doc(j))
    out.sort(key=lambda x: x['last_sent'])
    return out


def get_outreach(limit=100):
    rows = _get_db().outreach.find({}, sort=[('sent_at', DESCENDING)]).limit(limit)
    out  = []
    for r in rows:
        r['id'] = str(r.pop('_id'))
        out.append(r)
    return out


# ── HR Contacts (MongoDB-backed, mirrors csv logic) ───────────────────────────
def mongo_hr_lookup(company_name):
    if not company_name:
        return []
    db    = _get_db()
    clean = _normalize_company(company_name)
    if not clean:
        return []
    rows = db.hr_contacts.find(
        {'company_norm': {'$regex': re.escape(clean[:15]), '$options': 'i'}},
        {'_id': 0}
    ).limit(5)
    return list(rows)


def mongo_hr_save(company, hr_email, hr_name='', designation='HR', city='', source='Auto'):
    if not company or not hr_email:
        return False
    try:
        _get_db().hr_contacts.update_one(
            {'hr_email': hr_email.lower()},
            {'$setOnInsert': {
                'company':      company,
                'company_norm': _normalize_company(company),
                'hr_email':     hr_email.lower(),
                'hr_name':      hr_name,
                'designation':  designation,
                'city':         city,
                'source':       source,
                'added_at':     datetime.now().isoformat(),
            }},
            upsert=True
        )
        return True
    except Exception:
        return False


def _normalize_company(name):
    name = name.lower().strip()
    name = re.sub(
        r'\s+(pvt\.?|ltd\.?|limited|inc\.?|llc|corp\.?|private|'
        r'technologies|tech|solutions|services|india|group|'
        r'foundation|ventures|digital|global)\.?\s*$', '', name)
    return re.sub(r'[^a-z0-9\s]', '', name).strip()


# ── Custom Job Sites ──────────────────────────────────────────────────────────
def get_custom_sites():
    """Return all user-defined custom job sites."""
    db   = _get_db()
    rows = db.custom_sites.find({}, sort=[('name', 1)])
    out  = []
    for r in rows:
        r['id'] = str(r.pop('_id'))
        out.append(r)
    return out


def save_custom_site(site: dict):
    """Upsert a custom site by its base_url."""
    db  = _get_db()
    sid = site.get('id') or str(uuid.uuid4())
    db.custom_sites.update_one(
        {'_id': sid},
        {'$set': {
            'name':        site.get('name', '').strip(),
            'base_url':    site.get('base_url', '').strip(),
            'search_url':  site.get('search_url', '').strip(),
            'card_sel':    site.get('card_sel', '').strip(),
            'title_sel':   site.get('title_sel', '').strip(),
            'company_sel': site.get('company_sel', '').strip(),
            'location_sel':site.get('location_sel', '').strip(),
            'link_sel':    site.get('link_sel', '').strip(),
            'salary_sel':  site.get('salary_sel', '').strip(),
            'enabled':     site.get('enabled', True),
            'updated_at':  datetime.now().isoformat(),
        }},
        upsert=True
    )
    return sid


def delete_custom_site(site_id: str):
    _get_db().custom_sites.delete_one({'_id': site_id})


def toggle_custom_site(site_id: str, enabled: bool):
    _get_db().custom_sites.update_one(
        {'_id': site_id},
        {'$set': {'enabled': enabled}}
    )


# ── Scrape Job Tracking (GitHub Actions worker writes here) ───────────────────
def create_scrape_job(job_id: str, search_id: str, params: dict):
    _get_db().scrape_jobs.insert_one({
        '_id':        job_id,
        'search_id':  search_id,
        'params':     params,
        'status':     'queued',
        'progress':   0,
        'message':    'Queued — waiting for GitHub Actions runner to start...',
        'stats':      {},
        'count':      0,
        'created_at': datetime.now().isoformat(),
    })


def update_scrape_job(job_id: str, **kwargs):
    _get_db().scrape_jobs.update_one({'_id': job_id}, {'$set': kwargs})


def get_scrape_job(job_id: str):
    doc = _get_db().scrape_jobs.find_one({'_id': job_id})
    if doc:
        doc['id'] = str(doc.pop('_id'))
    return doc


def get_scrape_results_by_search(search_id: str):
    """Return jobs saved under a given search_id (populated by the worker)."""
    db  = _get_db()
    out = []
    for r in db.jobs.find({'search_id': search_id}, {'description': 0}):
        r['id'] = str(r.pop('_id'))
        out.append(_sanitize_doc(r))
    return out
