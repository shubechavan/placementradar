"""
tools/build_contact_review_queue.py — the contact->domain attribution queue.

Phase F found 43 contacts sitting on a company whose confirmed domain they do not
match. `campus@olacabs.com` was filed under `domain:colaninfotech.com`; every gate
passed it, because the confirmation gate validates the COMPANY -> DOMAIN edge and
nothing ever validated the CONTACT -> DOMAIN edge.

WHY A QUEUE AND NOT A RULE. The same conclusion Genesis and Meril forced for domains,
now one level down. `advice@rethink.org` on `rethink.com` is a different organisation;
`contact@simulab.in` on `simulab.com` is the same one. No string metric separates
those — they are identical in shape. An attribution decision that cannot be made from
the data gets a human, not a heuristic.

TWO SHEETS, because they deserve different review effort:

  contact_review_queue.csv        one row per mismatch, reviewed individually
  contact_review_tldonly.csv      the subset differing ONLY by TLD, for block approval

Both are written OUTSIDE the repo — they carry real contact data, and `.gitignore`
does not protect against `zip -r`.

Read-only against the database. Writes no documents, changes no scores.

    python tools/build_contact_review_queue.py
"""

import argparse
import csv
import os
import sys
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

FIELDS = ['company_name', 'doc_id', 'confirmed_domain', 'email', 'email_domain',
          'source', 'confidence', 'mx_ok', 'mismatch_class', 'decision',
          'corrected_domain', 'notes']

# Public-suffix-ish tails needed to compare "the name part" of two domains. Not a
# full PSL — it only has to be right for the shapes present in this corpus, and a
# wrong split here produces a review row, not a silent decision.
MULTI_TLDS = ('co.in', 'com.au', 'co.uk', 'co.jp', 'com.sg', 'ac.in', 'edu.in',
              'org.in', 'net.in', 'gov.in', 'du.ac.in')


def split_domain(d):
    """(stem, tld). 'simulab.co.in' -> ('simulab', 'co.in')."""
    d = (d or '').lower().strip()
    for t in MULTI_TLDS:
        if d.endswith('.' + t):
            return d[:-(len(t) + 1)], t
    if '.' in d:
        stem, _, tld = d.rpartition('.')
        return stem, tld
    return d, ''


def classify(email_domain, company_domain):
    """How does this email's domain differ from its company's? Descriptive only —
    nothing here decides anything, it only routes the row to the right sheet."""
    es, et = split_domain(email_domain)
    cs, ct = split_domain(company_domain)
    if es == cs and et != ct:
        return 'tld_only'
    if D.classify_email(f'x@{email_domain}')['is_personal_provider']:
        return 'personal_provider'
    if es.startswith(cs) or cs.startswith(es):
        return 'name_prefix'          # hcl / hcltech — related OR unrelated
    return 'unrelated'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write contact data inside the repo.')
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    db = D._get_db()
    docs = list(db[args.collection].find({}))

    rows = []
    matched = 0
    for d in docs:
        dom = (d.get('domain') or '').lower()
        if not dom:
            continue
        for c in (d.get('contacts') or []):
            if c.get('type') != 'email':
                continue
            ed = (c.get('value_norm') or '').split('@')[-1]
            if ed == dom or ed.endswith('.' + dom):
                matched += 1
                continue
            rows.append({
                'company_name': d.get('company_name') or '',
                'doc_id': d['_id'],
                'confirmed_domain': dom,
                'email': c.get('value_norm') or '',
                'email_domain': ed,
                'source': c.get('source') or '',
                'confidence': c.get('confidence'),
                'mx_ok': (c.get('verification') or {}).get('mx_ok'),
                'mismatch_class': classify(ed, dom),
                'decision': '',            # APPROVE / REJECT / CORRECT
                'corrected_domain': '',
                'notes': '',
            })

    rows.sort(key=lambda r: (r['mismatch_class'], r['confirmed_domain']))
    tld_only = [r for r in rows if r['mismatch_class'] == 'tld_only']
    rest = [r for r in rows if r['mismatch_class'] != 'tld_only']

    main_path = out_dir / 'contact_review_queue.csv'
    tld_path = out_dir / 'contact_review_tldonly.csv'
    for path, data in ((main_path, rest), (tld_path, tld_only)):
        with path.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(data)

    print(f'\n{"="*72}\nCONTACT -> DOMAIN ATTRIBUTION QUEUE\n{"="*72}')
    print(f'  collection                  : {args.collection}')
    print(f'  emails on domain: documents : {matched + len(rows)}')
    print(f'    domain matches            : {matched}')
    print(f'    MISMATCH                  : {len(rows)}  '
          f'({len(rows)/max(matched+len(rows),1)*100:.1f}%)')
    print(f'\n  by class: {dict(Counter(r["mismatch_class"] for r in rows))}')
    print(f'\n  {tld_path.name:32} {len(tld_only):3} rows  '
          f'<- TLD differs only; block-approvable')
    for r in tld_only:
        print(f'      {r["email"][:40]:40} vs {r["confirmed_domain"]}')
    print(f'\n  {main_path.name:32} {len(rest):3} rows  <- one by one')
    print(f'\n  both written to {out_dir}  (outside the repo — real contact data)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
