"""
tools/backfill_phone_provenance.py — Phase G, track 2: give the historical phones
their real provenance back.

THIS TRACK DOWNGRADES SCORES. It is still correct.

The original intent was to recover each historical phone's `source_domain` so those
contacts could be scored like Phase E's. Measuring the corpus first killed that
premise:

    phone-bearing CSV rows            332
      carrying a `company_url`          7  (2%)  -- and all 7 point at in.indeed.com
      carrying a `job_url`            332  (100%)
      carrying a `site`               332  (100%)
    site distribution: internshala 315, builtin 10, indeed 7

**The company's own domain cannot be recovered for these phones, because it never
existed.** 95% were scraped off Internshala listing pages, not company websites.
Writing a company domain into `source_domain` would be fabricating provenance to make
a number look better than it is.

So this backfill records what is true — `source_domain: internshala.com`,
`source_url: <job_url>`, `source: internshala` — which moves these contacts INTO
`AGGREGATOR_SOURCES` and applies the -0.25 penalty they always deserved. That penalty
has been matching zero contacts since the v3 rebuild flattened provenance to
`csv-backfill`; this is what revives it.

Append-only in spirit: it fills empty provenance fields and never overwrites a
contact's value. Contains no delete/drop/remove/$pull.

DRY-RUN BY DEFAULT.

    python tools/backfill_phone_provenance.py
    python tools/backfill_phone_provenance.py --run
"""

import argparse
import os
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

import pandas as pd  # noqa: E402
import database as D  # noqa: E402
import db_guard  # noqa: E402
import net_throttle as NT  # noqa: E402
from tools.bench import MANIFEST, _cell  # noqa: E402

# The `site` column holds a short name; these are the hosts those names refer to.
# Anything not listed keeps its short name as the source and gets no source_domain —
# guessing a host would be the same fabrication this tool exists to avoid.
SITE_DOMAINS = {
    'internshala': 'internshala.com',
    'builtin': 'builtin.com',
    'indeed': 'indeed.com',
    'linkedin': 'linkedin.com',
    'naukri': 'naukri.com',
    'foundit': 'foundit.in',
    'shine': 'shine.com',
    'glassdoor': 'glassdoor.co.in',
    'wellfound': 'wellfound.com',
    'timesjobs': 'timesjobs.com',
}


def load_phone_provenance():
    """normalized phone -> {'site', 'domain', 'url'} from the CSV corpus.

    Where a phone appears on rows from several sites, the most common site wins and
    the ambiguity is counted, so it shows up in the report rather than being decided
    silently.
    """
    df = pd.concat([pd.read_csv(REPO_ROOT / r, on_bad_lines='skip', low_memory=False)
                    for r, _ in MANIFEST], ignore_index=True)
    votes = defaultdict(Counter)
    urls = {}
    for rec in df.to_dict('records'):
        raw_phone = _cell(rec.get('phone'))
        if not raw_phone.strip():
            continue
        site = _cell(rec.get('site')).strip().lower()
        url = _cell(rec.get('job_url')).strip()
        for p in raw_phone.split(','):
            n = D.normalize_contact_value('phone', p)
            if not n:
                continue
            votes[n][site] += 1
            if url:
                urls.setdefault((n, site), url)

    out, ambiguous = {}, 0
    for n, c in votes.items():
        if len(c) > 1:
            ambiguous += 1
        site = c.most_common(1)[0][0]
        out[n] = {'site': site,
                  'domain': SITE_DOMAINS.get(site, ''),
                  'url': urls.get((n, site), '')}
    return out, ambiguous, len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()

    prov, ambiguous, nrows = load_phone_provenance()
    db = D._get_db()
    docs = list(db[args.collection].find({}))
    spread = D.contact_spread(docs)

    stats = Counter()
    updates = {}
    conf_before, conf_after = [], []
    site_hits = Counter()

    for d in docs:
        contacts = d.get('contacts') or []
        changed = False
        new_contacts = []
        for c in contacts:
            c = dict(c)
            if c.get('type') == 'phone' and not c.get('source_domain'):
                stats['phones_missing_provenance'] += 1
                p = prov.get(c.get('value_norm'))
                if p and p['domain']:
                    before = c.get('confidence') or 0.0
                    c['source'] = p['site']
                    c['source_domain'] = p['domain']
                    c['source_url'] = p['url'] or None
                    c['provenance_backfilled'] = True
                    seen_n = spread.get(('phone', c.get('value_norm')), 1)
                    c['confidence'] = D.confidence(c, d.get('domain'), seen_n)
                    conf_before.append(before)
                    conf_after.append(c['confidence'])
                    site_hits[p['site']] += 1
                    stats['backfilled'] += 1
                    changed = True
                else:
                    stats['unrecoverable'] += 1
            new_contacts.append(c)
        if changed:
            updates[d['_id']] = new_contacts

    # How many contacts does the revived aggregator penalty now touch?
    after_agg = sum(1 for d in docs for c in (d.get('contacts') or [])
                    if (c.get('source') or '').lower() in D.AGGREGATOR_SOURCES)
    projected_agg = after_agg + stats['backfilled']

    print(f'\n{"="*74}\nPHASE G TRACK 2 — PHONE PROVENANCE BACKFILL\n{"="*74}')
    print(f'  collection                      : {args.collection}')
    print(f'  CSV rows scanned                : {nrows}')
    print(f'  phones with recoverable source  : {len(prov)}')
    print(f'  phones on >1 site (ambiguous)   : {ambiguous}  (most common wins)')
    print(f'\n  phones lacking source_domain    : {stats["phones_missing_provenance"]}')
    print(f'    BACKFILLED                    : {stats["backfilled"]}')
    print(f'    unrecoverable (not in CSV)    : {stats["unrecoverable"]}')
    print(f'\n  by true source site:')
    for s, n in site_hits.most_common():
        print(f'    {s:16} -> {SITE_DOMAINS.get(s, "?"):20} {n}')

    print(f'\n  AGGREGATOR PENALTY (-0.25), which has been matching ZERO:')
    print(f'    contacts matched before       : {after_agg}')
    print(f'    contacts matched after        : {projected_agg}')

    if conf_before:
        import statistics as st
        print(f'\n  CONFIDENCE ON BACKFILLED PHONES (this track LOWERS scores):')
        print(f'    before : mean {st.mean(conf_before):.3f}  '
              f'>0: {sum(1 for v in conf_before if v > 0)}/{len(conf_before)}')
        print(f'    after  : mean {st.mean(conf_after):.3f}  '
              f'>0: {sum(1 for v in conf_after if v > 0)}/{len(conf_after)}')
    print(f'\n  documents to update             : {len(updates)}')

    if not args.run:
        print('\nDRY RUN — nothing written. Re-run with --run once approved.')
        return 0

    wdb = D._get_write_db()
    db_guard.assert_expected_collection(D.COMPANIES_COLLECTION, args.collection,
                                        operation='backfill phone provenance')
    db_guard.dump_collections(wdb, [args.collection], args.out_dir,
                              label='pre-phone-provenance')
    for _id, contacts in updates.items():
        wdb[args.collection].update_one({'_id': _id}, {'$set': {'contacts': contacts}})
    print(f'\n  updated {len(updates)} documents in {args.collection}')
    bad = D.assert_identity_shape(args.collection, db=wdb)
    print(f'  identity shape check            : {len(bad)} malformed _id(s)')
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
