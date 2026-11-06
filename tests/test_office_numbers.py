"""tools/find_office_numbers.py: which websites to visit for the HR database, and which
numbers found on them are safe to call a company's office line."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('find_office_numbers', os.path.join(ROOT, 'tools', 'find_office_numbers.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


def test_company_domains_groups_hr_contacts_by_their_company_email_domain():
    rows = [{'company': 'Acme Analytics', 'hr_email': 'Priya@acme.example'},
            {'company': 'Acme Analytics Pvt Ltd', 'hr_email': 'ravi@acme.example'},
            {'company': 'Solo Recruiter', 'hr_email': 'someone@gmail.com'},          # personal mailbox
            {'company': 'Listed Co', 'hr_email': 'jobs@naukri.com'},                  # a job board's address
            {'company': 'No Email', 'hr_email': ''}]
    got = F.company_domains(rows, personal_domains={'gmail.com'})
    assert list(got) == ['acme.example']
    assert got['acme.example'] == {'company': 'Acme Analytics', 'emails': ['priya@acme.example', 'ravi@acme.example']}


def test_pick_numbers_prefers_the_office_landline_over_a_mobile():
    # Contact pages often list a salesperson's mobile before the office line.
    chosen, _ = F.pick_numbers({'acme.example': ['+91 88005 92601', '+91 120 499 8925']})
    assert chosen == {'acme.example': '+91 120 499 8925'}


def test_pick_numbers_keeps_each_sites_first_number_and_drops_shared_ones():
    found = {
        'acme.example': ['+91 22 5550 0101', '+91 22 5550 0102'],
        'beta.example': ['+91 80 5550 0909', '+91 22 5550 0101'],   # 0101 is on two sites
        'gamma.example': ['+91 80 5550 0909'],                       # ...and so is 0909
        'delta.example': [],
    }
    chosen, shared = F.pick_numbers(found)
    assert chosen == {'acme.example': '+91 22 5550 0102'}
    assert shared == {'2255500101': ['acme.example', 'beta.example'],
                      '8055500909': ['beta.example', 'gamma.example']}
