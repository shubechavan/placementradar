"""Test setup. These tests never touch a database or the network: scrapers are fed
canned responses, and the session fails if `database` gets imported."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop('GH_PAT', None)          # never trigger the GitHub scrape worker


@pytest.fixture(autouse=True)
def _no_database():
    yield
    assert 'database' not in sys.modules, 'a test imported database'


@pytest.fixture(autouse=True)
def _fresh_metrics():
    import source_metrics
    source_metrics.reset()
    yield
    source_metrics.reset()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text='', headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.url = ''

    def json(self):
        return self._payload


@pytest.fixture
def fake_get(monkeypatch):
    """Route requests.get to a function of (url, params) returning a FakeResponse."""
    import requests

    def install(handler):
        def _get(url, params=None, **kw):
            return handler(url, params or {})
        monkeypatch.setattr(requests, 'get', _get)
    return install
