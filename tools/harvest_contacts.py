"""
tools/harvest_contacts.py — theHarvester as ONE MORE contact source.

ADDITIVE, NOT A REPLACEMENT. This does not touch the CSV corpus, the sitemap
discoveries, or the hrdb. `upsert_company_contacts()` is append-only and dedups by
`(type, value_norm)`, so anything theHarvester finds that another source already
found merges into the existing contact instead of duplicating it. Sources overlap by
design; that overlap is how a contact earns corroboration.

WHY A SUBPROCESS AND NOT AN IMPORT — this is a licensing constraint, not a style
preference. theHarvester is **GPL-2.0**. Importing or vendoring its code would make
PlacementRadar a derivative work and force the whole project to GPL. Invoking it as a
separate executable and reading its JSON output keeps the two at arm's length, which
is the normal way to use a GPL tool from a non-GPL project.

INSTALL (not performed automatically — nothing here installs anything):

    pipx install git+https://github.com/laramies/theHarvester

  * The PyPI package named `theHarvester` is a STALE PLACEHOLDER (v0.0.1, 2019).
    `pip install theHarvester` gets the wrong software. Install from the repo.
  * Upstream states Python **3.14**; this project runs 3.13. Install it into its own
    interpreter (pipx does this) and point `--binary` at it. It is an external tool,
    not a library of ours, so its Python version need not match ours.

SCOPE — only the companies that already have a CONFIRMED domain. theHarvester takes a
domain as input, so it can contribute nothing for the `key:`-identity companies; those
still need human lookup. Measured: 186 of 314 documents qualify.

SOURCE SELECTION — passive, keyless, and no breach data:
  * Breach modules are DISABLED explicitly. Sourcing recruiter contacts from breach
    dumps is not defensible for a placement committee, and `-b all` would include them.
  * Only keyless sources are used by default, so the probe costs nothing and needs no
    account.

DRY-RUN BY DEFAULT. Nothing is written without --run.

    python tools/harvest_contacts.py --n 20            # probe 20 companies
    python tools/harvest_contacts.py --n 20 --run
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('MONGO_URI', 'mongodb://127.0.0.1:27017/placementradar_dev')
os.environ.pop('GH_PAT', None)

import database as D  # noqa: E402
import db_guard  # noqa: E402
import domain_utils as DU  # noqa: E402
import source_metrics as SM  # noqa: E402

SOURCE = 'theharvester'

# Keyless, passive sources. Explicitly NOT 'all' — that pulls in breach providers
# (haveibeenpwned et al.), which this project will not use for outreach contacts.
DEFAULT_SOURCES = 'crtsh,certspotter,duckduckgo,hackertarget,otx,rapiddns,urlscan'

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


def find_binary(explicit=None):
    """Locate the theHarvester executable, or return None. Never installs."""
    for cand in ([explicit] if explicit else []) + ['theHarvester', 'theharvester']:
        if not cand:
            continue
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


def run_harvester(binary, domain, limit, sources, timeout):
    """Invoke theHarvester for one domain. Returns (emails, error_or_None).

    Emits a clear error rather than an empty list when the tool fails — a source that
    is broken must never be indistinguishable from a source that found nothing. That
    ambiguity is what Phase G existed to remove.
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'h'
        cmd = [binary, '-d', domain, '-l', str(limit), '-b', sources, '-f', str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return [], f'timeout after {timeout}s'
        except Exception as e:
            return [], f'{type(e).__name__}: {e}'

        emails = set()
        for suffix in ('.json', ''):
            f = Path(str(out) + suffix)
            if f.exists():
                try:
                    blob = json.loads(f.read_text(encoding='utf-8', errors='replace'))
                    for e in (blob.get('emails') or []):
                        emails.add(str(e).strip().lower())
                except Exception:
                    pass
        # Fall back to scraping stdout — the JSON filename convention has moved
        # between releases, and losing results to a filename change would be silent.
        #
        # The banner is cut first: theHarvester prints its author's address
        # (cmartorella@edge-security.com) in the ASCII art header, and a naive scrape
        # reports it as a find for EVERY domain queried. Caught in testing because the
        # off-domain gate flagged the same address against three unrelated companies.
        if not emails and proc.stdout:
            body = proc.stdout
            if '***' in body:
                body = body[body.rindex('***') + 3:]      # everything after the banner
            emails |= {m.group(0).lower() for m in EMAIL_RE.finditer(body)
                       if not m.group(0).lower().endswith('@edge-security.com')}

        # ---- PER-SOURCE STATUS, and why this matters more than the email list ----
        # theHarvester exits 0 and prints "[*] No emails found." even when EVERY
        # source failed at the transport layer. Verified on this machine: all 7
        # sources returned `status: failed, stop_reason: transport-error` while the
        # CLI reported a clean, successful-looking zero — the exact ambiguity Phase G
        # was built to remove, arriving through a new source.
        #
        # The machine-readable `.jsonl` summary carries the truth, so it is read and
        # a total-failure run is reported as an ERROR, never as "found nothing".
        failed, total = [], 0
        jl = Path(str(out) + '.jsonl')
        if jl.exists():
            try:
                for line in jl.read_text(encoding='utf-8', errors='replace').splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    for se in (rec.get('source_executions') or []):
                        total += 1
                        if se.get('status') != 'success':
                            failed.append(f"{se.get('source')}:{se.get('stop_reason') or se.get('status')}")
            except Exception:
                pass
        if total and len(failed) == total and not emails:
            return [], f'ALL {total} sources failed — {", ".join(sorted(set(failed))[:4])}'

        if not emails and proc.returncode != 0:
            err = (proc.stderr or proc.stdout or '').strip().splitlines()
            return [], f'exit {proc.returncode}: {err[-1][:160] if err else "no output"}'
        if failed:
            print(f'      (partial: {len(failed)}/{total} sources failed — '
                  f'{", ".join(sorted(set(failed))[:3])})')
        return sorted(emails), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--n', type=int, default=20, help='companies to process')
    ap.add_argument('--limit', type=int, default=100, help='results per source')
    ap.add_argument('--sources', default=DEFAULT_SOURCES)
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--binary', default=None)
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write contact data inside the repo.')
        return 2

    binary = find_binary(args.binary)
    print(f'\n{"="*76}\nHARVEST — theHarvester as an additional source\n{"="*76}')
    if not binary:
        print('  theHarvester: NOT INSTALLED — nothing was run and nothing was written.')
        print('\n  Install (into its own interpreter, it needs Python 3.14):')
        print('      pipx install git+https://github.com/laramies/theHarvester')
        print('  NOTE: the PyPI package `theHarvester` is a stale 2019 placeholder —')
        print('        `pip install theHarvester` installs the wrong software.')
        print('\n  This tool is GPL-2.0 and is invoked as a SUBPROCESS on purpose, so')
        print('  PlacementRadar does not become a derivative work.')
        return 1
    print(f'  binary        : {binary}')
    print(f'  sources       : {args.sources}')
    print(f'  breach sources: DISABLED (never used for outreach contacts)')

    db = D._get_db()
    docs = [d for d in db[args.collection].find({}) if d.get('identity_type') == 'domain']
    docs.sort(key=lambda d: d['_id'])
    sample = docs[:args.n]
    print(f'  collection    : {args.collection}')
    print(f'  domain-identity companies: {len(docs)}   processing: {len(sample)}')

    stats = Counter()
    planned, rows = {}, []
    for i, d in enumerate(sample, 1):
        dom = (d.get('domain') or '').lower()
        existing = {c.get('value_norm') for c in (d.get('contacts') or [])}
        t0 = time.time()
        SM.record_attempt(SOURCE)
        emails, err = run_harvester(binary, dom, args.limit, args.sources, args.timeout)
        SM.record_latency(SOURCE, (time.time() - t0) * 1000)
        if err:
            SM.record_failure(SOURCE, RuntimeError(err))
            stats['failed'] += 1
            print(f'  [{i:3}/{len(sample)}] {dom:34} FAILED: {err[:60]}')
            continue
        SM.record_success(SOURCE, len(emails))

        own, off, junk, dupe, fresh = 0, 0, 0, 0, []
        for e in emails:
            edom = e.split('@')[-1]
            if DU.is_junk_contact_domain(e):
                junk += 1; continue
            if not (edom == dom or edom.endswith('.' + dom)):
                # Off-domain goes to the attribution queue, never straight to storage —
                # the contact->domain edge is exactly what 23.5% of the corpus got wrong.
                off += 1
                rows.append({'company': d.get('company_name'), 'doc_id': d['_id'],
                             'confirmed_domain': dom, 'email': e,
                             'email_domain': edom, 'source': SOURCE,
                             'decision': '', 'corrected_domain': '', 'notes': ''})
                continue
            own += 1
            if e in existing:
                dupe += 1; continue
            c = D.build_contact('email', e, SOURCE, company_domain=dom,
                                source_domain=dom, source_url=f'theharvester:{dom}')
            if c:
                fresh.append(c)
        stats['own'] += own; stats['offdomain'] += off
        stats['junk'] += junk; stats['already_had'] += dupe; stats['new'] += len(fresh)
        if fresh:
            planned[d['_id']] = {'domain': dom, 'contacts': fresh}
            stats['companies_with_new'] += 1
        print(f'  [{i:3}/{len(sample)}] {dom:34} found={len(emails):3} '
              f'own={own:2} new={len(fresh):2} off={off:2} junk={junk:2}')

    print(f'\n{"-"*76}')
    print(f'  companies processed        : {len(sample)}   failed: {stats["failed"]}')
    print(f'  own-domain emails found    : {stats["own"]}')
    print(f'    already in the store     : {stats["already_had"]}  (dedup — additive, not duplicated)')
    print(f'    NEW                      : {stats["new"]}  across {stats["companies_with_new"]} companies')
    print(f'  off-domain -> review queue : {stats["offdomain"]}  (never auto-stored)')
    print(f'  junk/provider filtered     : {stats["junk"]}')
    yield_pc = stats['new'] / max(len(sample), 1)
    print(f'  NEW contacts per company   : {yield_pc:.2f}')

    if rows:
        import csv
        p = out_dir / 'harvester_offdomain_queue.csv'
        with p.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f'  off-domain queue written   : {p}')

    if not args.run:
        print('\nDRY RUN — nothing written. Re-run with --run once approved.')
        return 0

    if not planned:
        print('\nNothing new to write.')
        return 0

    wdb = D._get_write_db()
    db_guard.assert_expected_collection(D.COMPANIES_COLLECTION, args.collection,
                                        operation='append harvester contacts')
    db_guard.dump_collections(wdb, [args.collection], args.out_dir, label='pre-harvester')
    before = sum(len(x.get('contacts') or [])
                 for x in wdb[args.collection].find({}, {'contacts': 1}))
    added = 0
    for _id, v in planned.items():
        _, n = D.upsert_company_contacts(v.get('company_name', _id), v['contacts'],
                                         args.collection, domain=v['domain'],
                                         db=wdb, doc_id=_id)
        added += n
    after = sum(len(x.get('contacts') or [])
                for x in wdb[args.collection].find({}, {'contacts': 1}))
    print(f'\n  contacts {before} -> {after}  (+{added})')
    bad = D.assert_identity_shape(args.collection, db=wdb)
    print(f'  identity shape check       : {len(bad)} malformed _id(s)')
    SM.flush(wdb)
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
