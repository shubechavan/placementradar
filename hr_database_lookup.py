"""
hr_database.py — Local Indian HR contacts database
Bundled CSV lookup — works offline, zero API calls, instant results.

Sources compiled from:
- Avinash Singh's viral Substack post (50+ companies, Jul 2024)
- Scribd "HR Email IDs of Top 500 Indian Firms"
- Public LinkedIn posts and placement community shares
- Community-contributed entries

Add your own: drop more rows into hr_database.csv
Format: company, hr_email, hr_name, designation, city, source
"""
import csv, re, os
import domain_utils as _DU   # pure module, no DB import — bench isolation holds

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hr_database.csv')

# Cache in memory after first load
_db = None

def _load():
    global _db
    if _db is not None:
        return _db
    _db = []
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                _db.append({k: v.strip() for k, v in row.items()})
        print(f"[HR DB] Loaded {len(_db)} contacts from {DB_PATH}")
    except Exception as e:
        print(f"[HR DB] ERROR loading {DB_PATH}: {e}")
        _db = []
    return _db


def lookup(company_name):
    """
    Look up HR contacts for a company by name.
    Returns list of dicts: {hr_email, hr_name, designation, city, source}
    Uses fuzzy matching — 'Flipkart India Pvt Ltd' will match 'Flipkart'.
    """
    if not company_name:
        return []

    db      = _load()
    clean   = _normalize(company_name)
    results = []
    seen    = set()

    for row in db:
        # Strict same-company test — see _same_company() for why the old
        # contains/any-token rule was replaced.
        if _same_company(company_name, row.get('company', '')):
            email = row.get('hr_email', '').strip()
            if email and email not in seen:
                seen.add(email)
                results.append({
                    'hr_email':      email,
                    'hr_name':       row.get('hr_name', ''),
                    'designation':   row.get('designation', 'HR'),
                    'city':          row.get('city', ''),
                    'source':        '🗂️ HR Database',
                    # Provenance/staleness (D4) — Phase F consumes these.
                    'origin':        row.get('source', ''),
                    'date_added':    row.get('date_added', ''),
                    'verified_by':   row.get('verified_by', ''),
                    'last_verified': row.get('last_verified', ''),
                    'status':        row.get('status', 'unverified'),
                })

    return results


def get_all():
    """Return full database as list of dicts."""
    return _load()


# Phase B (D4): hrdb is a first-class curated store, not a dumb seed list, so every
# row carries provenance and a staleness marker. Order MUST match the CSV header —
# csv.writer writes positionally, so appending with the old 6-column row would
# silently shift every value one field left.
FIELDS = ['company', 'hr_email', 'hr_name', 'designation', 'city', 'source',
          'date_added', 'verified_by', 'last_verified', 'status']


def add_entry(company, hr_email, hr_name='', designation='HR', city='', source='Manual',
              date_added=None, verified_by='', last_verified='', status='unverified'):
    """Add a new entry to the database CSV — skips if email already exists."""
    try:
        if not company or not hr_email: return False
        # Check for duplicate email (any company)
        db = _load()
        for row in db:
            if row.get('hr_email','').lower() == hr_email.lower():
                return False  # already exists
        from datetime import datetime as _dt
        row = {
            'company': company, 'hr_email': hr_email, 'hr_name': hr_name,
            'designation': designation, 'city': city, 'source': source,
            'date_added': date_added or _dt.now().strftime('%Y-%m-%d'),
            'verified_by': verified_by, 'last_verified': last_verified,
            'status': status,
        }
        with open(DB_PATH, 'a', encoding='utf-8', newline='') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
        global _db
        _db = None  # Bust cache so next lookup sees new entry
        return True
    except Exception:
        return False


def search(query):
    """Search database by company name, email, or city."""
    db = _load()
    q  = query.lower().strip()
    return [r for r in db if q in r.get('company','').lower()
                              or q in r.get('hr_email','').lower()
                              or q in r.get('city','').lower()]

def email_exists(email):
    """Fast check — is this exact email already in the DB?"""
    db = _load()
    el = email.lower().strip()
    return any(r.get('hr_email','').lower() == el for r in db)


def stats():
    db = _load()
    companies = set(r.get('company','') for r in db)
    cities    = set(r.get('city','') for r in db if r.get('city'))
    return {
        'total_contacts': len(db),
        'total_companies': len(companies),
        'total_cities': len(cities),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize(name):
    """Lowercase, strip common suffixes, remove special chars."""
    name = name.lower().strip()
    name = re.sub(r'\s+(pvt\.?|ltd\.?|limited|inc\.?|llc|corp\.?|private|'
                  r'technologies|tech|solutions|services|india|group|'
                  r'foundation|ventures|digital|global)\.?\s*$', '', name)
    name = re.sub(r'[^a-z0-9\s]', '', name).strip()
    return name


def _same_company(a, b):
    """True only when two names plausibly denote the SAME company.

    THE BUG THIS REPLACES. The old rule matched if either normalised name contained
    the other, or if they shared ANY token of 4+ characters. `_normalize()` strips
    only ONE trailing suffix, so "Quantasis Private Limited" became
    "quantasis private" — and the surviving word "private" matched
    "TG Levels Private Limited". Every Pvt Ltd company in a scrape was handed
    contact@tglevels.com. "tech" matched Tech Mahindra; "consultancy" matched TCS.
    Measured on a live run: 6 of 6 HR-database hits were the wrong company, and the
    store's 43 mis-attributed contacts trace back to this function.

    NOW: identical `company_key()` (which strips suffixes iteratively), OR every
    DISTINCTIVE token of one name appears in the other. Boilerplate — private,
    limited, tech, consultancy, solutions — is never evidence, because
    `distinctive_tokens()` excludes it. A name made only of generic words
    ("Genesis") must match exactly; three different Genesis firms exist.
    """
    ka, kb = _DU.company_key(a), _DU.company_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = set(_DU.distinctive_tokens(a)), set(_DU.distinctive_tokens(b))
    if not ta or not tb:
        return False
    small = ta if len(ta) <= len(tb) else tb
    if small <= _DU.GENERIC_NAME_WORDS:
        return False
    return ta <= tb or tb <= ta


def _token_match(a, b):
    """True if any token in a matches any token in b (min 4 chars)."""
    ta = set(t for t in a.split() if len(t) >= 4)
    tb = set(t for t in b.split() if len(t) >= 4)
    return bool(ta & tb)
