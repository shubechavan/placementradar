import time

import pytest

import contact_finder as C
import net_throttle as NT
import source_metrics as SM
from conftest import FakeResponse


@pytest.mark.parametrize('raw, expected', [
    ('+91 98765 43210', '9876543210'),
    ('0091 9876543210', '9876543210'),
    ('wa.me/919876543210', '9876543210'),
    ('022-2659 1234', '2226591234'),          # Mumbai office landline is kept
    ('12345', ''),
])
def test_parse_phone_raw(raw, expected):
    assert C._parse_phone_raw(raw) == expected


def test_filter_emails_drops_noreply_and_image_names():
    kept = C._filter_emails(['hr@acme.com', 'noreply@acme.com', 'careers@acme.com', 'image@2x.png'])
    assert kept == ['hr@acme.com', 'careers@acme.com']


def test_is_hr():
    assert C._is_hr('careers@acme.com') and not C._is_hr('ceo@acme.com')


@pytest.mark.parametrize('url, domain', [
    ('https://in.indeed.com/jobs?q=x', 'indeed.com'),
    ('www.tradeindia.com', 'tradeindia.com'),
    ('https://dir.example.co.in/a', 'example.co.in'),
])
def test_registrable_domain(url, domain):
    assert NT._registrable(url) == domain


def test_dir_get_spaces_requests_and_records_status(fake_get, monkeypatch):
    monkeypatch.setattr(NT, 'MIN_GAP_SECONDS', 0.2)
    stamps = []

    def handler(url, params):
        stamps.append(time.time())
        return FakeResponse(403)
    fake_get(handler)
    for _ in range(3):
        assert C._dir_get('sulekha', 'https://www.sulekha.com/x').status_code == 403
    assert min(b - a for a, b in zip(stamps, stamps[1:])) >= 0.19
    assert SM.snapshot()['sulekha'].get('http_403') == 3      # a block is visible, and not retried
