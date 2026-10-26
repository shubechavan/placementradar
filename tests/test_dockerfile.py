"""The Docker image is built from an explicit COPY list, so a new runtime folder ships only
if it is added to that list. When static/ was missing, production served the page shell
with every CSS and JS file 404ing, and the site rendered blank."""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def copied_sources():
    text = open(os.path.join(ROOT, 'Dockerfile'), encoding='utf-8').read()
    sources = []
    for args in re.findall(r'^COPY\s+(.+)$', text, re.M):
        parts = [p for p in args.split() if not p.startswith('--')]
        sources += [p.rstrip('/') for p in parts[:-1]]          # last part is the destination
    return sources


@pytest.mark.parametrize('folder', ['templates', 'static'])
def test_image_ships_folders_flask_serves_from(folder):
    assert os.path.isdir(os.path.join(ROOT, folder))
    assert folder in copied_sources(), f'Dockerfile does not COPY {folder}/ into the image'


def test_image_never_ships_secrets():
    for source in copied_sources():
        assert not re.search(r'(^|/)\.env|\.env$|^\.$', source), f'Dockerfile copies {source}'
