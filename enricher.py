"""
enricher.py — Free company data enrichment
- Clearbit Logo API (free, no key needed)
- Company size/industry via public sources
"""
import requests, re
from urllib.parse import urlparse

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PlacementRadar/1.0)'}

def get_logo_url(company_name, company_url=''):
    """Return a logo URL using Clearbit (free, no API key)."""
    domain = _get_domain(company_url) or _guess_domain(company_name)
    if domain:
        return f"https://logo.clearbit.com/{domain}"
    return ''


def enrich_company(company_name, company_url=''):
    """Get basic company info. Returns dict with logo, domain."""
    domain = _get_domain(company_url) or _guess_domain(company_name)
    return {
        'logo':   f"https://logo.clearbit.com/{domain}" if domain else '',
        'domain': domain,
    }


def _get_domain(url):
    if not url: return ''
    try:
        if not url.startswith('http'): url = 'https://' + url
        host = urlparse(url).netloc.lower()
        for p in ('www.','careers.','jobs.'): host = host.removeprefix(p)
        return host if '.' in host else ''
    except: return ''


def _guess_domain(company):
    if not company: return ''
    name = re.sub(
        r'\s+(pvt\.?|ltd\.?|limited|inc\.?|llc|corp\.?|private|'
        r'technologies|tech|solutions|services|india|group|foundation)$',
        '', company.strip(), flags=re.I)
    slug = re.sub(r'[^a-z0-9]', '', name.lower())
    return f"{slug}.com" if slug else ''
