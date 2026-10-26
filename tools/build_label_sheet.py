"""
tools/build_label_sheet.py — Phase G, track 3: the hand-labelling sheet that makes a
confidence threshold derivable.

WHY THIS EXISTS. The brief's 0.6 bulk-send cutoff is unreachable: the highest score any
contact in this corpus achieves is 0.35, so the gate does not filter the list, it
empties it. 0.6 was a guessed number resting on guessed weights. A threshold is only
meaningful against labelled outcomes — without them, deriving a cutoff from percentiles
just relabels the existing ordering, and if the ordering is wrong (and
`campus@olacabs.com` filed under `colaninfotech.com` says it partly is) the percentiles
inherit the error.

SAMPLE DESIGN, and why it is not a random 100:

  * **All 43 contact->domain mismatches are included.** They have to be adjudicated
    anyway for the attribution queue, so labelling them serves both purposes and costs
    nothing extra. They are also where wrong-company errors concentrate — a random
    sample would catch only a handful.

  * **The rest is STRATIFIED across the confidence bands** (0.35 / 0.20 / 0.05 / 0.00),
    not sampled proportionally. Proportional sampling would return mostly zeros (336 of
    501 contacts score 0) and leave the top band with too few labels to measure
    precision where it actually matters. Equal strata let precision be computed AT EACH
    CUTOFF rather than only in aggregate.

  * Deterministic (`random.seed(20260914)`), so the sheet can be regenerated and
    compared, and so a second labeller gets the same rows.

The sheet carries real contact data and is written OUTSIDE the repo.

    python tools/build_label_sheet.py
    python tools/build_label_sheet.py --per-band 20
"""

import argparse
import csv
import os
import random
import sys
from collections import Counter, defaultdict
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

SEED = 20260914
LABELS = 'good | wrong-company | dead | unsure'

FIELDS = ['label', 'type', 'value', 'company_name', 'doc_id', 'identity_type',
          'company_domain', 'value_domain', 'source', 'confidence', 'band',
          'mx_ok', 'in_attribution_queue', 'mismatch_class', 'notes']

# Prepended as `#` comment lines. Spreadsheets show them as leading rows; a plain-text
# editor shows them as a preamble. Either way the instructions travel WITH the file,
# because a sheet that outlives the conversation that produced it needs to explain
# itself.
HEADER_BLOCK = """\
# PLACEMENTRADAR — CONTACT LABEL SHEET
#
# Fill the `label` column. One of four values:
#
#   good            This contact plausibly reaches this company. A recruiter, an HR
#                   alias, a switchboard — anything a committee member could actually
#                   use to reach THIS employer.
#   wrong-company   The contact is real but belongs to a DIFFERENT company than the
#                   row's. This is the error the scoring cannot currently see.
#                   Example: campus@olacabs.com filed under Colan Infotech.
#   dead            Right company, but the address or number is not live — bounced,
#                   defunct domain, disconnected line.
#   unsure          You cannot tell from the row without going and checking.
#
# `unsure` IS A REAL ANSWER, NOT A FAILURE TO DECIDE. It is the correct label whenever
# the evidence in the row does not settle the question, and it is used as its own
# category — rows marked `unsure` are excluded from the precision calculation rather
# than counted as either right or wrong. Guessing to avoid it is the one thing that
# would corrupt the result: a coin-flip recorded as `good` is indistinguishable from a
# verified `good`, and the whole point of this sheet is to stop trusting unverified
# ordering. Leave it `unsure` and move on.
#
# What this is for: the brief's bulk-send threshold of 0.6 is unreachable — nothing in
# the corpus scores above 0.35 — because 0.6 and the weights under it were guesses.
# Once labelled: precision(>=t) = good / (good + wrong-company + dead), computed at
# each cutoff, which is what replaces the guess.
#
# Rows 1-43 are the contact->domain mismatches (in_attribution_queue = yes); labelling
# them also resolves the attribution queue. The remaining rows are sampled equally
# across confidence bands so precision can be measured AT each cutoff, not just overall.
#
# `notes` is free text and always optional.
#
"""


def band_of(conf):
    c = round(conf or 0.0, 2)
    if c >= 0.30:
        return '0.35'
    if c >= 0.15:
        return '0.20'
    if c > 0:
        return '0.05'
    return '0.00'


def row_for(d, c, in_queue=False, mismatch_class=''):
    val = c.get('value_norm') or c.get('value') or ''
    vdom = val.split('@')[-1] if c.get('type') == 'email' and '@' in val else ''
    return {
        'label': '',
        'type': c.get('type'),
        'value': val,
        'company_name': d.get('company_name') or '',
        'doc_id': d['_id'],
        'identity_type': d.get('identity_type'),
        'company_domain': d.get('domain') or '',
        'value_domain': vdom,
        'source': c.get('source') or '',
        'confidence': c.get('confidence'),
        'band': band_of(c.get('confidence')),
        'mx_ok': (c.get('verification') or {}).get('mx_ok'),
        'in_attribution_queue': 'yes' if in_queue else '',
        'mismatch_class': mismatch_class,
        'notes': '',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    ap.add_argument('--per-band', type=int, default=15,
                    help='rows sampled per confidence band, on top of the 43 mismatches')
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if out_dir.resolve() == REPO_ROOT or REPO_ROOT in out_dir.resolve().parents:
        print('FATAL: refusing to write contact data inside the repo.')
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    db = D._get_db()
    docs = list(db[args.collection].find({}))

    # 1. every contact->domain mismatch (the attribution queue)
    sys.path.insert(0, str(REPO_ROOT / 'tools'))
    from tools.build_contact_review_queue import classify

    rows, taken = [], set()
    for d in docs:
        dom = (d.get('domain') or '').lower()
        if not dom:
            continue
        for c in (d.get('contacts') or []):
            if c.get('type') != 'email':
                continue
            ed = (c.get('value_norm') or '').split('@')[-1]
            if ed == dom or ed.endswith('.' + dom):
                continue
            rows.append(row_for(d, c, True, classify(ed, dom)))
            taken.add((d['_id'], c.get('type'), c.get('value_norm')))

    n_queue = len(rows)

    # 2. stratified remainder
    pools = defaultdict(list)
    for d in docs:
        for c in (d.get('contacts') or []):
            k = (d['_id'], c.get('type'), c.get('value_norm'))
            if k in taken:
                continue
            pools[band_of(c.get('confidence'))].append((d, c))

    rng = random.Random(SEED)
    for band in ('0.35', '0.20', '0.05', '0.00'):
        pool = sorted(pools.get(band, []), key=lambda x: (x[0]['_id'], x[1].get('value_norm') or ''))
        pick = pool if len(pool) <= args.per_band else rng.sample(pool, args.per_band)
        for d, c in pick:
            rows.append(row_for(d, c))

    path = out_dir / 'label_sheet.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        f.write(HEADER_BLOCK)
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f'\n{"="*74}\nPHASE G TRACK 3 — LABEL SHEET\n{"="*74}')
    print(f'  collection                   : {args.collection}')
    print(f'  rows                         : {len(rows)}')
    print(f'    from the attribution queue : {n_queue}  (all 43 mismatches)')
    print(f'    stratified sample          : {len(rows) - n_queue}')
    print(f'\n  by band (available -> sampled):')
    bands = Counter(r['band'] for r in rows)
    for b in ('0.35', '0.20', '0.05', '0.00'):
        print(f'    {b:6} {len(pools.get(b, [])) + sum(1 for r in rows if r["band"] == b and r["in_attribution_queue"]):5} available'
              f'  ->  {bands.get(b, 0):3} in sheet')
    print(f'\n  by type   : {dict(Counter(r["type"] for r in rows))}')
    print(f'  label column accepts: {LABELS}')
    print(f'\n  written: {path}   (outside the repo — real contact data)')
    print(f'\n  Precision at each cutoff becomes computable once `label` is filled:')
    print(f'    precision(>=t) = good / (good + wrong-company + dead)  over rows with confidence >= t')
    return 0


if __name__ == '__main__':
    sys.exit(main())
