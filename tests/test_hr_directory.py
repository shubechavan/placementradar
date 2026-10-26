"""The HR Database page reads the hr_contacts collection through hr_directory. These tests use
a small in-memory stand-in for the collection, so no database is touched. All people,
companies and addresses below are made up."""
from types import SimpleNamespace

import hr_directory as HD


class FakeCollection:
    """The slice of a pymongo collection hr_directory uses, with MongoDB's semantics for it:
    an upsert whose filter matches leaves the existing document untouched ($setOnInsert)."""

    def __init__(self, docs=()):
        self.docs = [dict(d, _id=i) for i, d in enumerate(docs, start=1)]

    def find(self, filt=None, projection=None):
        hide_id = bool(projection) and projection.get('_id') == 0
        return [{k: v for k, v in d.items() if not (hide_id and k == '_id')} for d in self.docs]

    def update_one(self, filt, update, upsert=False):
        (key, value), = filt.items()
        if any(d.get(key) == value for d in self.docs):
            return SimpleNamespace(matched_count=1, modified_count=0, upserted_id=None)
        new_id = len(self.docs) + 1
        self.docs.append(dict(update.get('$setOnInsert', {}), _id=new_id))
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=new_id)


IMPORTED = {'company': 'Initech Systems', 'company_norm': 'initech systems', 'hr_email': 'jane.doe@initech.example',
            'hr_name': 'Jane Doe', 'designation': 'Head of Talent', 'city': '', 'source': 'HR directory PDF',
            'imported_from': 'CompanyWise_HR_contact.pdf', 'import_batch': 'hr-import-2026-10-26'}
FOUND = {'company': 'Globex Corp', 'hr_email': 'careers@globex.example', 'designation': 'HR', 'city': 'Bangalore',
         'source': 'auto:naukri'}


def test_all_contacts_returns_the_six_page_fields_with_blanks_for_missing():
    rows = HD.all_contacts(FakeCollection([IMPORTED, FOUND]))
    assert rows[1] == {'company': 'Globex Corp', 'hr_email': 'careers@globex.example', 'hr_name': '',
                       'designation': 'HR', 'city': 'Bangalore', 'source': 'auto:naukri'}
    assert all(set(r) == set(HD.FIELDS) for r in rows)


def test_matching_searches_company_email_name_and_city_ignoring_case():
    rows = HD.all_contacts(FakeCollection([IMPORTED, FOUND]))
    assert [r['company'] for r in HD.matching(rows, 'bangalore')] == ['Globex Corp']
    assert [r['company'] for r in HD.matching(rows, 'DOE')] == ['Initech Systems']
    assert [r['company'] for r in HD.matching(rows, 'initech.example')] == ['Initech Systems']
    assert HD.matching(rows, '  ') == rows


def test_stats_count_contacts_distinct_companies_and_cities():
    rows = HD.all_contacts(FakeCollection([IMPORTED, FOUND, dict(FOUND, hr_email='hr@globex.example',
                                                                company='globex corp', city='bangalore')]))
    assert HD.stats(rows) == {'total_contacts': 3, 'total_companies': 2, 'total_cities': 1}


def test_add_inserts_a_new_contact_and_reports_success():
    coll = FakeCollection([IMPORTED])
    assert HD.add(coll, ' Umbrella Labs ', ' Campus@Umbrella.example ', hr_name='Sam Roe', city='Pune') is True
    new = coll.docs[-1]
    assert new['hr_email'] == 'campus@umbrella.example' and new['company'] == 'Umbrella Labs'
    assert new['company_norm'] == 'umbrella labs' and new['source'] == 'Manual' and new['designation'] == 'HR'


def test_add_refuses_an_email_already_present_and_leaves_it_unchanged():
    coll = FakeCollection([IMPORTED])
    assert HD.add(coll, 'Someone Else', 'JANE.DOE@initech.example', hr_name='Different') is False
    assert len(coll.docs) == 1 and coll.docs[0]['hr_name'] == 'Jane Doe'


def test_add_refuses_a_missing_company_or_a_malformed_email():
    coll = FakeCollection()
    assert HD.add(coll, '', 'hr@acme.example') is False
    assert HD.add(coll, 'Acme', 'not-an-email') is False
    assert coll.docs == []
