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
from collections import defaultdict
from datetime import datetime, timedelta
from pymongo import MongoClient, DESCENDING, UpdateOne
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

import db_guard


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

# ── Auth / users connection ───────────────────────────────────────────────────
# User login accounts live in a SEPARATE database (default name 'signup') so
# login data is isolated from scraped job data. If AUTH_MONGO_URI is unset we
# fall back to the main MONGO_URI — so existing single-DB setups keep working
# unchanged (users then live in whatever db MONGO_URI names).
AUTH_MONGO_URI = os.environ.get('AUTH_MONGO_URI', '') or MONGO_URI
_auth_client = None
_auth_db     = None

# Default database name if the URI doesn't specify one. Kept as 'jobs' so the
# existing production database is unchanged.
_DEFAULT_DB_NAME = 'jobs'

def _db_name_from_uri(uri):
    """Extract the database name from a MongoDB URI's path, if present.

    Previously this module hardcoded _client['jobs'], which IGNORED the database
    named in the URI — so pointing MONGO_URI at a '..../jobs_authtest' test DB
    silently still used the real 'jobs' database. That caused real data loss.
    Honouring the URI's db name makes test databases actually isolated.

    Parsed with a plain regex (no pymongo.parse_uri) because parse_uri performs a
    live DNS lookup for mongodb+srv:// URIs, which would fail offline/in tests.
    """
    # Grab the path segment between the host's first '/' and the '?' query, e.g.
    # mongodb+srv://user:pass@host/<dbname>?opts  ->  <dbname>
    m = re.search(r'^mongodb(?:\+srv)?://[^/]+/([^/?]+)', uri.strip(), re.I)
    if m and m.group(1):
        return m.group(1)
    return _DEFAULT_DB_NAME

def _get_db():
    global _client, _db
    if _db is not None:
        return _db
    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        _client.admin.command('ping')
        _db = _client[_db_name_from_uri(MONGO_URI)]   # honour the URI's db name
        _ensure_indexes(_db)
        print(f'[MongoDB] Connected OK  (database: {_db.name})')
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        print(f'[MongoDB] Connection failed: {e}')
        raise
    return _db


# ── Write guard ───────────────────────────────────────────────────────────────
# Every write path goes through _get_write_db(), which routes through the single
# shared gate in db_guard.py. The gate pins BOTH host and database name: this
# machine's local mongod also hosts unrelated projects, so "is it localhost?" is
# not sufficient isolation — that is precisely how the production wipe happened
# (right host, wrong database).
_EXPECTED_DEV_DB = os.environ.get('DEV_DB_NAME', db_guard.DEV_DB_NAME)
_verified_write_target = None


def _get_write_db():
    """_get_db() plus the safety gate. Verified once — the handle is a singleton."""
    global _verified_write_target
    db = _get_db()
    if _verified_write_target is None:
        _verified_write_target = db_guard.assert_local_write(
            db, expected_db=_EXPECTED_DEV_DB, operation='write')
    return db


def _get_auth_db():
    """Connection for the users/login database (default 'signup').

    Reuses the main client when AUTH_MONGO_URI == MONGO_URI (same cluster) so we
    don't open a redundant pool; otherwise opens its own. Ensures the unique
    email index exists on whichever database users live in.
    """
    global _auth_client, _auth_db
    if _auth_db is not None:
        return _auth_db
    if AUTH_MONGO_URI == MONGO_URI:
        # Same cluster/URI — reuse the main client, just select the auth db name.
        _get_db()  # ensures _client is connected
        _auth_client = _client
    else:
        try:
            _auth_client = MongoClient(AUTH_MONGO_URI, serverSelectionTimeoutMS=10000)
            _auth_client.admin.command('ping')
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            print(f'[MongoDB/auth] Connection failed: {e}')
            raise
    _auth_db = _auth_client[_db_name_from_uri(AUTH_MONGO_URI)]
    try:
        _auth_db.users.create_index('email', unique=True)
    except Exception:
        pass
    print(f'[MongoDB] Auth/users database: {_auth_db.name}')
    return _auth_db



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
    try:
        db.users.create_index('email', unique=True)
    except Exception:
        pass
    db.activities.create_index([('created_at', -1)])
    db.activities.create_index('user_id')
    db.activities.create_index('job_id')
    # Phase B — company-level contact store
    db[COMPANIES_COLLECTION].create_index('company_key')
    db[COMPANIES_COLLECTION].create_index('domain', sparse=True)
    db[COMPANIES_COLLECTION].create_index('contacts.value_norm')


# ── Users (CRM Phase 1 — team accounts) ───────────────────────────────────────
# Passwords are stored ONLY as PBKDF2 hashes (werkzeug). Plaintext never touches
# the database. Email is the unique login key and is always lower-cased.
def create_user(name, email, password_hash, role='member'):
    """Insert a new user. Returns the new user's id, or None if the email exists."""
    from pymongo.errors import DuplicateKeyError
    email = (email or '').strip().lower()
    if not email or not password_hash:
        return None
    uid = str(uuid.uuid4())
    try:
        _get_auth_db().users.insert_one({
            '_id':        uid,
            'name':       (name or '').strip() or email,
            'email':      email,
            'password':   password_hash,     # PBKDF2 hash, never plaintext
            'role':       role if role in ('admin', 'member') else 'member',
            'active':     True,
            'created_at': datetime.now().isoformat(),
        })
        return uid
    except DuplicateKeyError:
        return None


def get_user_by_email(email):
    return _get_auth_db().users.find_one({'email': (email or '').strip().lower()})


def get_user_by_id(uid):
    return _get_auth_db().users.find_one({'_id': uid})


def count_users():
    return _get_auth_db().users.count_documents({})


def list_users():
    """All users without password hashes — safe to send to the admin panel."""
    out = []
    for u in _get_auth_db().users.find({}, {'password': 0}).sort('created_at', 1):
        u['id'] = str(u.pop('_id'))
        out.append(_sanitize_doc(u))
    return out


def set_user_active(uid, active):
    _get_auth_db().users.update_one({'_id': uid}, {'$set': {'active': bool(active)}})


def delete_user(uid):
    _get_auth_db().users.delete_one({'_id': uid})


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
# Credentials that live in the ENVIRONMENT ONLY and are never read from, nor written
# to, the database (Phase H).
#
# An env overlay already existed, but it only took precedence *when the variable was
# set* — the settings form still wrote the Gmail app password straight into the
# `settings` collection, so anyone with database read access had it in plaintext. A
# precedence rule is not containment: the value has to stop being persisted at all.
#
# Both directions are closed:
#   read  — the stored value is IGNORED even if present; only the env var is consulted
#   write — these keys are stripped before any write reaches Mongo
SECRET_ENV_ONLY = {
    'gmail_app_password': 'GMAIL_APP_PASSWORD',
    'serper_api_key':     'SERPER_API_KEY',
}


def get_settings():
    db   = _get_db()
    rows = db.settings.find({}, {'_id': 0})
    out  = {r['key']: r['value'] for r in rows}
    # Env-only secrets: overwrite unconditionally, so a legacy value still sitting in
    # the collection can never be used. It is left in place rather than deleted (no
    # destructive operations) — it is inert, but it should be rotated and removed by
    # hand, because "unused" is not "not leaked".
    for setting_key, env_key in SECRET_ENV_ONLY.items():
        out[setting_key] = os.environ.get(env_key, '')
    return out


def save_settings(data: dict):
    db  = _get_db()
    rejected = [k for k in data if k in SECRET_ENV_ONLY]
    if rejected:
        print(f'[DB] refusing to persist env-only secret(s): {rejected} — '
              f'set {[SECRET_ENV_ONLY[k] for k in rejected]} in the environment instead')
    ops = [UpdateOne({'key': k}, {'$set': {'value': v}}, upsert=True)
           for k, v in data.items() if k not in SECRET_ENV_ONLY]
    if ops:
        db.settings.bulk_write(ops)


def stored_secret_leftovers():
    """Env-only secrets still sitting in the `settings` collection, for operator
    warning. Reports; never deletes."""
    try:
        db = _get_db()
        return sorted(r['key'] for r in db.settings.find({}, {'_id': 0, 'key': 1,
                                                              'value': 1})
                      if r.get('key') in SECRET_ENV_ONLY and r.get('value'))
    except Exception:
        return []


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
def _store_discovered_contacts(job, discovered, db):
    """Fan a job's structured contacts into the company store (append-only).

    IDENTITY POLICY — this path may never mint a `domain:` identity.

    `job['company_url']` is a raw scraped value that has passed no confirmation
    gate, yet it was previously handed straight to `upsert_company_contacts(domain=)`,
    so every live scrape minted `domain:<whatever-the-listing-said>`. Phase D-new
    established that only a CONFIRMED domain may carry identity; this path had been
    quietly exempt from that rule since Phase B.

    Of the two available fixes — run the confirmation gate here, or refuse to mint —
    this takes the second, deliberately:

      * The gate needs to fetch and parse the company's homepage. That puts network
        I/O inside the database write path of a live scrape, where a timeout would
        silently change a company's identity. Wrong layer, and it fails unsafely.
      * Identity promotion already has a proper home: the offline resolver, with its
        review queue and its human-correction path.
      * The two errors are not symmetric. A `key:` document can be promoted to
        `domain:` later at no cost; a wrongly minted `domain:` document is a
        wrong-attribution bug that hides until someone reads the records. Prefer the
        recoverable failure.

    The observed host is not discarded — it is recorded as `observed_domains` for the
    resolver to adjudicate later.
    """
    company = _clean(job.get('company'))
    if not company:
        return 0
    observed = clean_company_domain(_clean(job.get('company_url')))
    envelopes = []
    for d in discovered:
        value = d.get('value')
        if d.get('type') == 'email' and is_junk_contact_domain(value):
            continue
        # `company_domain=` is left unset: the +0.30 own-domain bonus is corroboration,
        # and an unconfirmed host scraped from the same listing cannot corroborate the
        # contact that came with it. `source_domain` keeps the provenance instead.
        env = build_contact(d.get('type'), value, d.get('source') or 'unknown',
                            source_domain=observed or None)
        if env:
            envelopes.append(env)
    if not envelopes:
        return 0
    # Explicit target: the live scrape path writes to the active company store.
    # domain=None — never mint identity from an unconfirmed host (see above).
    _, added = upsert_company_contacts(company, envelopes, COMPANIES_COLLECTION,
                                       domain=None, observed_domain=observed or None,
                                       db=db)
    return added


def save_jobs(jobs: list, search_id: str):
    """
    Upsert by job_url — same URL always updates contact info, never duplicates.
    New jobs get a fresh UUID; returning jobs keep their status/notes/score.
    Existing contacts are never overwritten with empty — only improved.
    """
    db  = _get_write_db()
    now = datetime.now().isoformat()
    ops = []
    for j in jobs:
        url = (j.get('job_url') or '').strip()
        # Phase B: fan the enricher's structured contacts out to the company
        # store before the job doc is written. contact_finder attaches these in
        # memory only (it imports no DB module); we consume and strip the key so
        # it never reaches the jobs collection.
        _discovered = j.pop('_discovered_contacts', None)
        if _discovered:
            try:
                _store_discovered_contacts(j, _discovered, db)
            except Exception as e:
                print(f'[DB] company-store write skipped for '
                      f'{j.get("company","?")!r}: {type(e).__name__}: {e}')
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

    # Persist per-source counters gathered during this batch's enrichment (brief §G).
    # Imported here rather than at module scope purely for symmetry with the rule in
    # source_metrics itself; it is safe either way, since that module imports no DB.
    try:
        import source_metrics as _SM
        _SM.flush(db)
    except Exception as e:
        print(f'[DB] source_metrics flush skipped: {type(e).__name__}: {e}')


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


def get_job_owner(job_id):
    """Return (assigned_to, exists) for a job — used for ownership checks."""
    j = _get_db().jobs.find_one({'_id': job_id}, {'assigned_to': 1})
    if not j:
        return None, False
    return (j.get('assigned_to') or ''), True


# ── Pipeline / Kanban (CRM) ───────────────────────────────────────────────────
# The 5 board columns map onto the existing `status` values, so no migration is
# needed and the board stays in sync with the job table's status column.
# 6 columns. "Closed" is split into Won vs Lost so a company that HIRED (won) is
# never mislabelled as rejected — the two terminal outcomes are tracked apart,
# which is standard CRM practice and keeps the sir's placement numbers honest.
#   Closed–Won  -> status 'placed'   (they will hire / agreed to a drive outcome)
#   Closed–Lost -> status 'rejected' (declined) — also shows legacy 'not_interested'
PIPELINE_STAGES = {
    'to_contact':      ['new'],
    'contacted':       ['contacted'],
    'interested':      ['replied'],
    'drive_scheduled': ['drive_scheduled'],
    'closed_won':      ['placed'],
    'closed_lost':     ['rejected', 'not_interested'],
}
# When a card is dropped into a column, this is the status it gets written as.
STAGE_TO_STATUS = {
    'to_contact': 'new', 'contacted': 'contacted', 'interested': 'replied',
    'drive_scheduled': 'drive_scheduled',
    'closed_won': 'placed', 'closed_lost': 'rejected',
}
_HAS_CONTACT = {'$or': [
    {'phone':  {'$nin': ['', 'nan', None]}},
    {'emails': {'$nin': ['', 'nan', None]}},
]}

def get_pipeline(assigned_to=None, search='', first_limit=40):
    """Return cards grouped by stage.

    Only companies that have a contact appear. The 'to_contact' column is capped
    at `first_limit` (with a total count so the UI can show "+N more"); the
    working columns return in full since they only hold active cards.
    """
    db = _get_db()
    proj = {'company': 1, 'title': 1, 'location': 1, 'phone': 1, 'emails': 1,
            'hr_contact': 1, 'company_url': 1, 'status': 1, 'notes': 1,
            'assigned_to': 1, 'assigned_name': 1, 'created_at': 1}
    base = dict(_HAS_CONTACT)
    and_clauses = [_HAS_CONTACT]
    if assigned_to:
        and_clauses.append({'assigned_to': assigned_to})
    if search:
        s = re.escape(search)
        and_clauses.append({'$or': [
            {'company':  {'$regex': s, '$options': 'i'}},
            {'title':    {'$regex': s, '$options': 'i'}},
            {'location': {'$regex': s, '$options': 'i'}},
        ]})
    query = {'$and': and_clauses} if len(and_clauses) > 1 else _HAS_CONTACT

    out = {stage: [] for stage in PIPELINE_STAGES}
    counts = {}
    for stage, statuses in PIPELINE_STAGES.items():
        q = dict(query)
        q['status'] = {'$in': statuses}
        total = db.jobs.count_documents(q)
        counts[stage] = total
        cur = db.jobs.find(q, proj).sort('created_at', -1)
        if stage == 'to_contact':
            cur = cur.limit(first_limit)     # cap the big column
        for r in cur:
            r['id'] = str(r.pop('_id'))
            out[stage].append(_sanitize_doc(r))
    return {'columns': out, 'counts': counts}


def assign_job(job_id, user_id, user_name):
    """Set (or clear) the committee member who owns a company card."""
    _get_db().jobs.update_one(
        {'_id': job_id},
        {'$set': {'assigned_to': user_id or '', 'assigned_name': _clean(user_name)}})


def set_job_remarks(job_id, remarks):
    _get_db().jobs.update_one(
        {'_id': job_id}, {'$set': {'notes': _clean(remarks)[:2000]}})


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


# ── Activities (CRM Phase 2 — who did what) ───────────────────────────────────
# Every touch a committee member makes on a company is one activity row, stamped
# with the logged-in user. The leaderboard is a pure aggregation over this table,
# so counts can never drift from the underlying log.
VALID_CHANNELS = {'call', 'email', 'whatsapp', 'note'}
VALID_OUTCOMES = {'interested', 'not_interested', 'callback', 'no_answer',
                  'reply_received', 'drive_scheduled', 'contacted', ''}

def log_activity(job_id, company, user_id, user_name, channel=None,
                 outcome='', note='', next_followup='', channels=None):
    """Insert ONE activity that may span multiple channels (e.g. call + email).

    Accepts either `channel` (single, legacy) or `channels` (list). Stored as a
    normalised `channels` list; `channel` is kept as the first one for backward
    compatibility with older rows and simple queries. Counts as one touch — the
    leaderboard credits each channel present, but the row is a single activity.
    """
    # Normalise inputs into a de-duplicated, validated channel list.
    raw = list(channels) if channels else ([channel] if channel else [])
    chans = []
    for c in raw:
        c = (c or '').strip().lower()
        if c in VALID_CHANNELS and c not in chans:
            chans.append(c)
    if not chans:
        return None
    outcome = (outcome or '').strip().lower()
    if outcome not in VALID_OUTCOMES:
        outcome = ''
    aid = str(uuid.uuid4())
    _get_db().activities.insert_one({
        '_id':           aid,
        'job_id':        job_id or '',
        'company':       _clean(company),
        'user_id':       user_id or '',
        'user_name':     _clean(user_name),
        'channels':      chans,        # multi-channel list
        'channel':       chans[0],     # legacy single field (first channel)
        'outcome':       outcome,
        'note':          _clean(note)[:1000],
        'next_followup': _clean(next_followup),
        'created_at':    datetime.now().isoformat(),
    })
    return aid


def get_activities_for_job(job_id):
    """Full activity timeline for one company, newest first."""
    out = []
    for a in _get_db().activities.find({'job_id': job_id}).sort('created_at', -1):
        a['id'] = str(a.pop('_id'))
        out.append(_sanitize_doc(a))
    return out


# Scoring weights — results are worth more than raw activity. Kept in one place
# so the leaderboard and the UI legend never disagree.
LEADERBOARD_WEIGHTS = {
    'reply_received':  5,
    'drive_scheduled': 5,
    'interested':      3,
    'call':            1,   # per logged call
    'email':           1,   # per logged email
    'whatsapp':        1,   # per logged whatsapp
    'company':         2,   # per distinct company touched
}

def get_leaderboard(since_iso=None):
    """Per-user activity breakdown + weighted score.

    since_iso: ISO timestamp lower bound (for the weekly view), or None = all-time.
    Returns a list of member rows sorted by score, plus the weights used.
    Only counts users that still exist (a deleted member drops off).
    """
    db = _get_db()
    match = {} if not since_iso else {'created_at': {'$gte': since_iso}}

    # 1) channel + outcome counts per user.
    # A channel counts if it is PRESENT in the activity's channels array, so a
    # single call+email activity credits both Calls and Emails — but `total`
    # increments once (one touch). `_ch` normalises legacy rows (that only had a
    # scalar `channel`) into an array so both shapes aggregate the same way.
    rows = db.activities.aggregate([
        {'$match': match},
        {'$addFields': {'_ch': {'$ifNull': ['$channels', ['$channel']]}}},
        {'$group': {
            '_id': '$user_id',
            'user_name': {'$last': '$user_name'},
            'calls':     {'$sum': {'$cond': [{'$in': ['call',     '$_ch']}, 1, 0]}},
            'emails':    {'$sum': {'$cond': [{'$in': ['email',    '$_ch']}, 1, 0]}},
            'whatsapps': {'$sum': {'$cond': [{'$in': ['whatsapp', '$_ch']}, 1, 0]}},
            'replies':   {'$sum': {'$cond': [{'$eq': ['$outcome', 'reply_received']}, 1, 0]}},
            'drives':    {'$sum': {'$cond': [{'$eq': ['$outcome', 'drive_scheduled']}, 1, 0]}},
            'interested':{'$sum': {'$cond': [{'$eq': ['$outcome', 'interested']}, 1, 0]}},
            'total':     {'$sum': 1},
            'companies_set': {'$addToSet': '$job_id'},
        }},
    ])
    by_user = {}
    for r in rows:
        uid = r['_id']
        if not uid:
            continue
        companies = len([c for c in (r.get('companies_set') or []) if c])
        w = LEADERBOARD_WEIGHTS
        score = (r['replies'] * w['reply_received'] + r['drives'] * w['drive_scheduled']
                 + r['interested'] * w['interested'] + r['calls'] * w['call']
                 + r['emails'] * w['email'] + r['whatsapps'] * w['whatsapp']
                 + companies * w['company'])
        by_user[uid] = {
            'user_id': uid, 'user_name': r.get('user_name') or '—',
            'calls': r['calls'], 'emails': r['emails'], 'whatsapps': r['whatsapps'],
            'companies': companies, 'replies': r['replies'], 'drives': r['drives'],
            'interested': r['interested'], 'total': r['total'], 'score': score,
        }

    # 2) include active members with zero activity so the sir sees everyone.
    # Users live in the auth db (may differ from the jobs db), so read from there.
    for u in _get_auth_db().users.find({'active': True}, {'password': 0}):
        uid = str(u['_id'])
        if uid not in by_user:
            by_user[uid] = {
                'user_id': uid, 'user_name': u.get('name') or u.get('email'),
                'calls': 0, 'emails': 0, 'whatsapps': 0, 'companies': 0,
                'replies': 0, 'drives': 0, 'interested': 0, 'total': 0, 'score': 0,
            }
        elif by_user[uid]['user_name'] in ('—', '', None):
            by_user[uid]['user_name'] = u.get('name') or u.get('email')

    board = sorted(by_user.values(), key=lambda x: (-x['score'], -x['companies'], x['user_name']))
    return {'board': board, 'weights': LEADERBOARD_WEIGHTS}


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


# ═════════════════════════════════════════════════════════════════════════════
# COMPANY-LEVEL CONTACT STORE  (Phase B)
#
# The old schema kept ONE `emails` string and ONE `phone` string per job, so a
# company could not carry several independently sourced, scored contacts. These
# helpers back the `companies` collection, which can.
#
# NOTE: company_key() is deliberately NOT a change to _normalize_company() above.
# That function's output is already persisted as hr_contacts.company_norm, so
# altering its semantics would silently reinterpret existing rows.
# ═════════════════════════════════════════════════════════════════════════════

# The active company store. `companies` (v1) was written before the job-board-domain
# and listing-site-junk filters existed; rather than mutate it with $unset/$pull, the
# corpus was deterministically rebuilt into a fresh collection and v1 left on disk
# untouched as a reference. Rebuild-over-mutate needs no destructive operation at all.
# v4 is current. Keep this in step with the phase actually being worked on: leaving
# the constant one version behind, while a tool hardcoded the newer name, is precisely
# what put 214 contacts into companies_v2 (§7). `companies_v2` is POLLUTED by that
# write and must never be used as a comparison baseline.
COMPANIES_COLLECTION = os.environ.get('COMPANIES_COLLECTION', 'companies_v5')

# Listing-site badge text that leaked into stored company names (e.g.
# "AaptattActively hiring"). Stripped BEFORE keying: on the real corpus this
# merges 5 contaminated names with their clean twins and causes zero false
# merges. Leaving it to a later phase would create orphaned duplicate documents.
_BADGE_RE = re.compile(
    r'\s*(actively\s+hiring|be\s+an\s+early\s+applicant|early\s+applicant|'
    r'verified|promoted|featured|trending|hiring\s+challenge)\s*$', re.I)

# Legal/filler suffixes per brief §3. Applied repeatedly because they stack:
# "X Technologies Pvt Ltd" -> "X".
_KEY_SUFFIX_RE = re.compile(
    r'[\s,\.]+(pvt|pvt\.|private|ltd|limited|llp|inc|corp|technologies|tech|'
    r'solutions|services|india|group)\.?\s*$', re.I)

HR_FAMILY_PREFIXES = {
    'hr', 'hrd', 'hrteam', 'careers', 'career', 'jobs', 'job', 'recruitment',
    'recruiting', 'recruiter', 'hiring', 'apply', 'placement', 'placements',
    'campus', 'talent', 'people', 'staffing',
}
GENERIC_ROLE_PREFIXES = {
    'info', 'contact', 'contactus', 'sales', 'support', 'admin', 'office',
    'enquiry', 'enquiries', 'inquiry', 'inquiries', 'mail', 'email', 'help',
    'hello', 'general', 'reception', 'marketing', 'business', 'bd', 'service',
    'services', 'customercare', 'care', 'team', 'query', 'queries',
}
PERSONAL_PROVIDER_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.in', 'yahoo.co.in',
    'ymail.com', 'outlook.com', 'hotmail.com', 'hotmail.co.uk', 'live.com',
    'msn.com', 'rediffmail.com', 'rediff.com', 'icloud.com', 'me.com',
    'aol.com', 'protonmail.com', 'proton.me', 'zoho.com', 'mail.com',
}
# Sources whose contacts are structurally low-trust (brief §4: -0.25).
#
# Widened in Phase G. The original set covered only the directory aggregators, and
# then matched ZERO of 500 contacts because the v3 rebuild flattened every historical
# contact's provenance to `csv-backfill` — the penalty was live code that could never
# fire. Measuring where those contacts actually came from showed 95% were scraped off
# **Internshala listing pages**, with builtin and indeed making up the rest, so the
# job boards belong here too: a phone on a listing page is no more the company's own
# number than one on JustDial.
AGGREGATOR_SOURCES = {'jd', 'justdial', 'im', 'indiamart', 'sulekha', 'tradeindia',
                      'internshala', 'builtin', 'indeed'}

# Penalty for a contact seen on more than one company (a shared switchboard).
#
# INTERIM CORRECTION OF A GUESSED WEIGHT — not a derived one. The brief's -0.40 was
# a veto, not a penalty: the maximum achievable positive score on this corpus is
# 0.35 (own_domain 0.30 + mx_ok 0.05), so -0.40 forced ANY contact seen twice to
# exactly 0.0 regardless of every other piece of evidence. It was zeroing correct
# contacts — a named person on their own company's confirmed domain with verified MX.
#
# -0.15 keeps the ordering signal (a shared address still ranks below a unique one)
# without overriding all positive evidence. It is still a guess; the whole weight set
# needs re-deriving from the measured distribution (Phase G), and this value should be
# replaced then rather than defended.
SPREAD_PENALTY = 0.15

# Hosts that are never a company's own site. contact_finder._get_domain() only
# compares the bare host, so `in.linkedin.com` and `in.indeed.com` slipped through
# and were stored as company domains. Matching includes subdomains.
JOB_BOARD_HOSTS = {
    'linkedin.com', 'indeed.com', 'naukri.com', 'internshala.com', 'shine.com',
    'foundit.in', 'monsterindia.com', 'monster.com', 'apna.co', 'glassdoor.com',
    'glassdoor.co.in', 'ziprecruiter.com', 'timesjobs.com', 'freshersworld.com',
    'instahyre.com', 'wellfound.com', 'angel.co', 'hirist.tech', 'iimjobs.com',
    'cutshort.io', 'simplyhired.com', 'jooble.org', 'careerjet.co.in',
    'workindia.in', 'quikr.com', 'olx.in', 'upwork.com', 'freelancer.com',
    'indiamart.com', 'justdial.com', 'sulekha.com', 'tradeindia.com',
}


def is_job_board_host(host):
    """True when `host` is a job board / listing site, including subdomains."""
    h = (host or '').strip().lower().rstrip('.')
    if not h:
        return False
    if h.startswith('www.'):
        h = h[4:]
    return any(h == b or h.endswith('.' + b) for b in JOB_BOARD_HOSTS)


def clean_company_domain(url_or_host):
    """Extract a company domain from a URL, or '' if it isn't one.

    Returns '' for job boards, so a listing URL can never be recorded as the
    company's own domain.
    """
    raw = (url_or_host or '').strip()
    if not raw:
        return ''
    try:
        from urllib.parse import urlparse
        host = urlparse(raw if '://' in raw else 'https://' + raw).netloc.lower()
    except Exception:
        return ''
    host = host.split('@')[-1].split(':')[0].rstrip('.')
    if host.startswith('www.'):
        host = host[4:]
    if not host or '.' not in host or is_job_board_host(host):
        return ''
    return host


def is_junk_contact_domain(value):
    """True for listing-site / placeholder addresses that are never company HR.

    e.g. complaints@internshala.com was attached to 13 different companies, and
    first.lastname@example.com is a template, not a person.
    """
    v = (value or '').strip().lower()
    if '@' not in v:
        return False
    local, _, dom = v.partition('@')
    if is_job_board_host(dom):
        return True
    if local in {'first.lastname', 'firstname.lastname', 'first.last',
                 'name.surname', 'yourname', 'email', 'someone'}:
        return True
    return False


def strip_company_badges(name):
    """Remove trailing listing-site badge text, repeatedly (they can stack)."""
    s = (name or '').strip()
    prev = None
    while prev != s:
        prev = s
        s = _BADGE_RE.sub('', s).strip()
    return s


def company_key(name):
    """Stable, normalized identity for a company. '' when unkeyable.

    Validated against all 321 companies in the corpus: 315 distinct keys,
    6 collisions, every one a genuine duplicate (zero false merges).
    """
    s = strip_company_badges(name)
    if not s:
        return ''
    s = s.lower()
    s = re.sub(r'\(.*?\)', ' ', s)          # drop parentheticals
    prev = None
    while prev != s:
        prev = s
        s = _KEY_SUFFIX_RE.sub('', s)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def normalize_contact_value(ctype, value):
    """Canonical form used for dedup. '' means 'not a usable contact'."""
    v = (value or '').strip()
    if not v:
        return ''
    if ctype == 'email':
        v = v.lower()
        return v if re.match(r'^[^@\s]+@[^@\s]+\.[a-z]{2,}$', v) else ''
    if ctype == 'phone':
        d = re.sub(r'\D', '', v)
        if len(d) == 12 and d.startswith('91'):
            d = d[2:]
        elif len(d) == 13 and d.startswith('091'):
            d = d[3:]
        elif len(d) == 11 and d.startswith('0'):
            d = d[1:]
        if len(d) != 10:
            return ''
        if d[0] in '6789':
            return d
        # Indian FIXED LINES. This returned mobiles only, so every office switchboard
        # was rejected — 21 of 50 valid phones from OpenStreetMap's Mumbai offices,
        # including Bain, BCG and the BSE, all 022 landlines. The output format is
        # unchanged (10 national digits), so every key already stored still matches.
        return d if _is_india_landline(d) else ''
    return v


def _is_india_landline(digits):
    """10 national digits recognised by libphonenumber as an Indian fixed line.
    Toll-free / premium / shared-cost numbers are customer-care lines and excluded."""
    try:
        import phonenumbers
        num = phonenumbers.parse('+91' + digits, None)
    except Exception:
        return False
    return (phonenumbers.is_valid_number(num) and
            phonenumbers.number_type(num) in (phonenumbers.PhoneNumberType.FIXED_LINE,
                                              phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE))


def classify_email(email):
    local, _, domain = (email or '').lower().partition('@')
    hr_family = local in HR_FAMILY_PREFIXES
    generic = local in GENERIC_ROLE_PREFIXES
    return {
        'is_role_account': hr_family or generic,
        'is_hr_family': hr_family,
        'is_generic_role': generic,
        'is_personal_provider': domain in PERSONAL_PROVIDER_DOMAINS,
    }


def confidence(contact, company_domain=None, seen_on_n_companies=1):
    """Score a contact 0.0-1.0. Pure — tunable without touching any scraper.

    Weights are brief §4's starting values. Deliberately strict: a bare
    company-domain address scores 0.30, so only well-attested contacts clear the
    0.6 bulk-send threshold.
    """
    score = 0.0
    ctype = contact.get('type')
    value = (contact.get('value') or '').lower()
    source = (contact.get('source') or '').lower()
    person = (contact.get('person_name') or '').strip()
    role = (contact.get('role_title') or '').lower()
    verif = contact.get('verification') or {}

    if ctype == 'email' and company_domain and '@' in value:
        dom = value.split('@')[-1]
        if dom == company_domain.lower() or dom.endswith('.' + company_domain.lower()):
            score += 0.30
    elif ctype == 'phone' and source.startswith('ats:'):
        score += 0.30

    if source.startswith('ats:'):
        score += 0.25
    if person:
        score += 0.20
    # 'human resource' and 'staffing' were missing: 559 imported contacts titled
    # "Head of Human Resources" (and 28 "Staffing" heads) got no HR-role credit while
    # "Head HR" did — same job, 0.15 apart, purely on wording. Found by reading
    # records after the PDF import, not from the totals.
    if any(k in role for k in ('hr', 'human resource', 'talent', 'recruit', 'campus',
                                'people', 'hiring', 'staffing', 'personnel')):
        score += 0.15

    if verif.get('smtp_ok') is True:
        score += 0.20
    elif verif.get('mx_ok') is True:
        score += 0.05

    cls = classify_email(value) if ctype == 'email' else {}
    if cls.get('is_generic_role'):
        score -= 0.15
    if cls.get('is_personal_provider') and not person:
        score -= 0.20
    if source in AGGREGATOR_SOURCES:
        score -= 0.25
    if seen_on_n_companies > 1:
        score -= SPREAD_PENALTY

    return round(max(0.0, min(1.0, score)), 3)


def build_contact(ctype, value, source, person_name=None, role_title=None,
                  company_domain=None, discovered_at=None, verification=None,
                  seen_on_n_companies=1, source_domain=None, source_url=None):
    """Build one contact envelope (brief §2). Returns None if unusable.

    `source_domain` / `source_url` record WHERE a contact came from. Historical
    phones have neither, which is why their provenance can never be audited (the
    Phase G gap); everything discovered from Phase E onward carries them.
    """
    norm = normalize_contact_value(ctype, value)
    if not norm:
        return None
    cls = classify_email(norm) if ctype == 'email' else {}
    doc = {
        'type':                 ctype,
        'value':                (value or '').strip(),
        'value_norm':           norm,
        'person_name':          person_name or None,
        'role_title':           role_title or None,
        'source':               source,
        'source_domain':        source_domain or None,
        'source_url':           source_url or None,
        'discovered_at':        discovered_at or datetime.now().isoformat(),
        'is_role_account':      bool(cls.get('is_role_account', False)),
        'is_personal_provider': bool(cls.get('is_personal_provider', False)),
        'verification':         verification or {
            'syntax_ok': True, 'mx_ok': None, 'smtp_ok': None,
            'catch_all': None, 'checked_at': None,
        },
    }
    doc['confidence'] = confidence(doc, company_domain, seen_on_n_companies)
    return doc


def resolve_company_doc_id(company_name, domain, collection, db):
    """The `_id` a company's document should have under the v3+ identity scheme.

    `domain:<domain>` when a CONFIRMED domain is known, else `key:<company_key>` —
    matching an EXISTING document first so a write updates it rather than creating
    a twin.

    `domain` must be a domain that has passed the confirmation gate. Callers that
    only have an *observed* domain (a scraped `company_url`, say) must pass None:
    minting `domain:` identity from an unverified host is how a staffing agency
    ended up identified by its client's domain.

    Returns '' when the company is unkeyable, or when the lookup is AMBIGUOUS —
    two documents sharing a `company_key`. Guessing between them would be an
    identity decision made by a coin flip, so the write is refused instead.

    This exists because `upsert_company_contacts()` originally keyed on a bare
    `company_key`. That predated the domain:/key: scheme and, reused against it,
    silently created 51 duplicate bare-key documents.
    """
    key = company_key(company_name)
    if not key:
        return ''
    coll = db[collection]
    if domain and coll.find_one({'_id': f'domain:{domain}'}, {'_id': 1}):
        return f'domain:{domain}'
    if coll.find_one({'_id': f'key:{key}'}, {'_id': 1}):
        return f'key:{key}'

    # No domain supplied, but this company may already hold a CONFIRMED
    # domain-identity document. Attaching to it is the whole point of resolving
    # against existing state — without this lookup, every live scrape of an
    # already-resolved company would mint a `key:` twin beside its `domain:` doc,
    # which is the 51-document bug wearing a different hat.
    existing = list(coll.find({'company_key': key}, {'_id': 1}).limit(2))
    if len(existing) == 1:
        return existing[0]['_id']
    if len(existing) > 1:
        print(f'[DB] refusing to write {company_name!r}: company_key {key!r} matches '
              f'{len(existing)} documents — ambiguous identity, needs review.')
        return ''

    return f'domain:{domain}' if domain else f'key:{key}'


def upsert_company_contacts(company_name, contacts, collection, domain=None,
                            db=None, doc_id=None, observed_domain=None):
    """Append contacts to a company, deduped by (type, value_norm).

    APPEND-ONLY by construction: existing contacts are never removed or
    overwritten, so re-scraping can only grow the set. Returns (doc_id, n_added).

    `collection` is REQUIRED and has no default. A module-level default is exactly
    what caused 214 contacts to land in the superseded `companies_v2` — the
    constant and a caller's hardcoded literal drifted apart and nothing objected.
    A write with no stated target must fail loudly rather than pick one.

    TWO KINDS OF DOMAIN, and the distinction is load-bearing:

      `domain`          a CONFIRMED domain. The only thing that may mint a
                        `domain:` identity. Pass this only when the confirmation
                        gate has passed.
      `observed_domain` a domain merely seen next to the company (a scraped
                        `company_url`). Recorded on the document as evidence for a
                        later gated resolution pass; NEVER used as identity.

    Uses no delete/drop/remove/$pull operation of any kind.
    """
    if not collection:
        raise ValueError(
            'upsert_company_contacts: `collection` is required — state the write '
            'target explicitly; never rely on a module-level default.')
    key = company_key(company_name)
    if not key:
        return '', 0
    db = db if db is not None else _get_write_db()
    now = datetime.now().isoformat()

    _id = doc_id or resolve_company_doc_id(company_name, domain, collection, db)
    if not _id:
        return '', 0

    existing = db[collection].find_one({'_id': _id}, {'contacts.value_norm': 1,
                                                      'contacts.type': 1,
                                                      'domain': 1})
    seen = set()
    if existing:
        for c in (existing.get('contacts') or []):
            seen.add((c.get('type'), c.get('value_norm')))

    fresh = []
    for c in contacts or []:
        if not c:
            continue
        ident = (c.get('type'), c.get('value_norm'))
        if ident in seen:
            continue
        seen.add(ident)
        fresh.append(c)

    set_fields = {'company_name': strip_company_badges(company_name).strip(),
                  'company_key': key,
                  'identity_type': 'domain' if _id.startswith('domain:') else 'key',
                  'updated_at': now}
    # Never clobber a known domain with an empty one.
    if domain:
        set_fields['domain'] = domain

    update = {
        '$set': set_fields,
        '$setOnInsert': {'created_at': now},
    }
    if fresh:
        update['$push'] = {'contacts': {'$each': fresh}}
    # Evidence, not identity: `$addToSet` so repeated scrapes accumulate the hosts
    # a company has been seen at without any of them becoming its `_id`.
    if observed_domain and observed_domain != domain:
        update.setdefault('$addToSet', {})['observed_domains'] = observed_domain

    db[collection].update_one({'_id': _id}, update, upsert=True)
    return _id, len(fresh)


def identity_groups(docs):
    """Map each document `_id` to a group id, unioning documents that are provably
    the same company (shared `company_key`, or a shared alias once normalised).

    Used so the spread penalty counts DISTINCT COMPANIES rather than distinct
    documents: `domain:amazonweb.com` and `key:amazon` are one company, and a contact
    on both should not be treated as a shared switchboard.

    MEASURED EFFECT ON TODAY'S DATA: none. No pair of documents sharing a contact
    also shares a `company_key` or a normalised alias — not even Amazon/AWS
    (`amazon-web` vs `amazon`; "Amazon Web Services" vs "Amazon"). This rescues 0 of
    the 21 spread-zeroed contacts. It is kept as a forward-looking guard and because
    it is correct in principle, but it is NOT the fix for that problem — the real
    cause there is one contact wrongly attached to several unrelated companies, which
    is an attribution defect and belongs in the review queue, not in the scorer.

    Deliberately conservative. Merging on a *loose* similarity would collapse the
    three different `genesis` firms into one and hide a genuinely shared contact;
    under-counting spread is the safer error now that the penalty is no longer a veto.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_token = defaultdict(list)
    for d in docs:
        _id = d['_id']
        find(_id)
        tokens = {d.get('company_key') or ''}
        for a in (d.get('aliases') or []):
            tokens.add(company_key(a))
        for t in tokens:
            if t:
                by_token[t].append(_id)

    for ids in by_token.values():
        for other in ids[1:]:
            union(ids[0], other)
    return {d['_id']: find(d['_id']) for d in docs}


def contact_spread(docs):
    """(type, value_norm) -> number of DISTINCT COMPANIES carrying that contact.

    Counts identity groups, not documents. See `identity_groups()` for why that
    currently changes nothing and is kept anyway.
    """
    groups = identity_groups(docs)
    spread = defaultdict(set)
    for d in docs:
        for c in (d.get('contacts') or []):
            spread[(c.get('type'), c.get('value_norm'))].add(groups[d['_id']])
    return {k: len(v) for k, v in spread.items()}


def assert_identity_shape(collection, db=None):
    """Every `_id` must carry a `domain:` or `key:` prefix. Returns the bad ones.

    A post-write shape check. This single assertion would have caught the 51
    malformed bare-key documents at write time instead of after they existed.
    """
    db = db if db is not None else _get_db()
    bad = [d['_id'] for d in db[collection].find({}, {'_id': 1})
           if not str(d['_id']).startswith(('domain:', 'key:'))]
    return bad


def get_company(key_or_name, collection, db=None):
    """Fetch a company document. `collection` is required — see upsert()."""
    if not collection:
        raise ValueError('get_company: `collection` is required.')
    db = db if db is not None else _get_db()
    if str(key_or_name).startswith(('domain:', 'key:')):
        return db[collection].find_one({'_id': key_or_name})
    key = company_key(key_or_name)
    return (db[collection].find_one({'_id': f'key:{key}'})
            or db[collection].find_one({'company_key': key}))


def best_contacts(company_doc, limit_emails=4, limit_phones=3):
    """Highest-confidence contacts, for the denormalized view on `jobs`.

    The full set always lives on the company document; this is only what the
    existing UI reads. Personal-provider addresses are KEPT here (brief §2) —
    they are excluded from bulk-send paths, not from storage or display.
    """
    contacts = (company_doc or {}).get('contacts') or []
    emails = sorted([c for c in contacts if c.get('type') == 'email'],
                    key=lambda c: (-(c.get('confidence') or 0), c.get('value_norm', '')))
    phones = sorted([c for c in contacts if c.get('type') == 'phone'],
                    key=lambda c: (-(c.get('confidence') or 0), c.get('value_norm', '')))
    return emails[:limit_emails], phones[:limit_phones]


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
