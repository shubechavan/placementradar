"""Searches find HR contacts through hr_database_lookup. Its rows come from MongoDB's
hr_contacts through a loader the app registers, so the CSV of real contacts can stay off
GitHub and out of the Docker image."""
import pytest

import hr_database_lookup as H

ROWS = [{'company': 'Acme Analytics', 'hr_email': 'priya@acme.example', 'hr_name': 'Priya',
         'designation': 'HR Manager', 'city': 'Mumbai', 'source': 'PDF import'}]


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(H, 'DB_PATH', str(tmp_path / 'hr_database.csv'))
    monkeypatch.setattr(H, '_db', None)
    monkeypatch.setattr(H, '_loader', None)


def test_lookup_reads_rows_from_the_registered_loader():
    H.set_loader(lambda: ROWS)
    assert [h['hr_email'] for h in H.lookup('Acme Analytics Pvt Ltd')] == ['priya@acme.example']


def test_auto_saved_contact_keeps_the_loaded_rows():
    H.set_loader(lambda: ROWS)
    assert H.add_entry('Beta Systems', 'hr@beta.example')
    assert H.lookup('Acme Analytics') and H.lookup('Beta Systems')


def test_a_failing_loader_falls_back_to_the_csv(tmp_path):
    (tmp_path / 'hr_database.csv').write_text(
        'company,hr_email,hr_name,designation,city,source\nGamma Labs,hr@gamma.example,,HR,Pune,Manual\n',
        encoding='utf-8')

    def down():
        raise ConnectionError('database unreachable')
    H.set_loader(down)
    assert [h['hr_email'] for h in H.lookup('Gamma Labs')] == ['hr@gamma.example']
