"""
hr_directory.py — the HR Database page's data, read from MongoDB's hr_contacts collection.

WHY NOT hr_database.csv. The page used to read the CSV inside the repo. On Render that
meant it showed only what had been committed, anything added on the live site vanished
at the next deploy, and the 2,054 contacts imported on 26 Oct 2026 (the HR directory PDF
plus the CSV itself) live in MongoDB, where the page never looked.

Every function takes the collection as an argument and this module imports no database
code, so the benchmark's and the tests' no-database isolation still holds.
"""

import re
from datetime import datetime

from hr_database_lookup import _normalize   # the rule that produced hr_contacts.company_norm

FIELDS = ('company', 'hr_email', 'hr_name', 'designation', 'city', 'source')
_SEARCHED = ('company', 'hr_email', 'hr_name', 'city')
_EMAIL = re.compile(r'^[^\s@<>"\']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$')


def all_contacts(coll):
    """Every contact, reduced to the fields the page shows; a missing field reads as ''."""
    return [{k: str(d.get(k) or '') for k in FIELDS} for d in coll.find({}, {'_id': 0})]


def matching(rows, query):
    """Rows whose company, email, name or city contains the query, ignoring case."""
    q = (query or '').strip().lower()
    if not q:
        return rows
    return [r for r in rows if any(q in r[k].lower() for k in _SEARCHED)]


def stats(rows):
    return {
        'total_contacts': len(rows),
        'total_companies': len({r['company'].strip().lower() for r in rows if r['company'].strip()}),
        'total_cities': len({r['city'].strip().lower() for r in rows if r['city'].strip()}),
    }


def add(coll, company, hr_email, hr_name='', designation='HR', city='', source='Manual'):
    """Insert one contact. Returns False, and changes nothing, when the email is already in
    the collection ($setOnInsert never touches an existing record) or the company or email
    is unusable."""
    company, email = (company or '').strip(), (hr_email or '').strip().lower()
    if not company or not _EMAIL.match(email):
        return False
    result = coll.update_one({'hr_email': email}, {'$setOnInsert': {
        'company': company, 'company_norm': _normalize(company), 'hr_email': email,
        'hr_name': (hr_name or '').strip(), 'designation': (designation or '').strip() or 'HR',
        'city': (city or '').strip(), 'source': source, 'added_at': datetime.now().isoformat(),
    }}, upsert=True)
    return result.upserted_id is not None
