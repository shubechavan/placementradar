"""
tools/build_companies_v3.py — rebuild the company store with DOMAIN-FIRST identity.

Why v3 exists
-------------
Phase B proved a name cannot identify a company: `genesis` covered three different
firms and only the domain separates them. v2 was keyed on `company_key`, which is
therefore a fallback, not an identity.

    _id = "domain:<domain>"   when the domain is confirmed  <- the real key
    _id = "key:<company_key>" fallback, incl. every unconfirmed guess

Rebuild-over-mutate, as with v2: a fresh collection, `companies_v2` left on disk
untouched. **No delete, drop, remove or $pull anywhere in this file.**

Three-tier handling of contacts sourced from wrong domains (nothing is destroyed —
quarantined contacts keep their reason and can be promoted back):
  * email from a parked / generic-rejected domain -> quarantine, source_domain_parked
  * email from an unconfirmed domain              -> quarantine, source_domain_unconfirmed
                                                     (reviewable: may be a
                                                      corroboration false negative)
  * phone at such a company                       -> KEPT, flagged
                                                     source_domain_suspect, because
                                                     phones carry no source
                                                     attribution at all (Phase G gap)

DRY-RUN BY DEFAULT. Nothing is written without --run.

    python tools/build_companies_v3.py           # dry run
    python tools/build_companies_v3.py --run
"""

import argparse
import json
import os
import sys
from collections import defaultdict, Counter
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
import domain_utils as DU  # noqa: E402
from tools.bench import MANIFEST, _cell  # noqa: E402

# Read from the shared constant rather than a local literal. A local copy here is
# exactly how Phase F's step 1 wrote into companies_v2: this file hardcoded v3 while
# database.COMPANIES_COLLECTION still pointed at v2, so the two disagreed silently.
# Rebuild target. v5, not v4, because the contested-domain fix REMOVES an `_id`
# (`domain:10xgenomics.com`) that v4 already contains. Writing the new plan over v4
# would leave that document behind as a stray — still holding NAVIS's contacts under
# 10x Genomics' identity — and deleting it is forbidden. A fresh collection is the
# project's standing answer to this (v1->v2->v3->v4 each happened for the same
# reason): no delete, no mutate, every previous version left intact on disk.
V3 = 'companies_v5'
SOURCE_ID = 'csv-backfill'
KNOWN_WRONG = {'parked', 'rejected_generic'}
UNCONFIRMED = {'unconfirmed'}


def load_identity(out_dir):
    """company -> {domain, verdict, identity_source} after applying review decisions."""
    res = json.loads((Path(out_dir) / 'domain_resolution.json').read_text(encoding='utf-8'))
    dec_path = Path(out_dir) / 'domain_review_decisions.json'
    dec = json.loads(dec_path.read_text(encoding='utf-8')) if dec_path.exists() else {}

    ident = {}
    for r in res['rows']:
        b = r['best']
        ident[r['company']] = {'domain': b.get('domain') or '',
                               'verdict': b.get('verdict'),
                               'strategy': b.get('strategy'),
                               'identity_source': 'automated'}
    for comp, v in dec.items():
        e = ident.setdefault(comp, {'domain': '', 'verdict': 'none',
                                    'strategy': 'none', 'identity_source': 'automated'})
        if v.get('corrected_status') == 'confirmed_human' and v.get('corrected_domain'):
            # A human-supplied domain is not a guess, so it is not discounted.
            e.update(domain=v['corrected_domain'], verdict='confirmed',
                     strategy='human_correction', identity_source='human_review')
        elif v.get('decision') == 'REJECT':
            e.update(domain='', verdict='rejected_by_review', identity_source='human_review')
        elif v.get('decision') == 'APPROVE':
            e.update(domain=v.get('domain') or e['domain'], verdict='confirmed',
                     identity_source='human_review')
    return ident


def usable_domain(entry):
    return entry['domain'] if entry.get('verdict') == 'confirmed' and entry.get('domain') else ''


def plan(out_dir):
    df = pd.concat([pd.read_csv(REPO_ROOT / r, on_bad_lines='skip', low_memory=False)
                    for r, _ in MANIFEST], ignore_index=True)
    ident = load_identity(out_dir)

    groups = defaultdict(lambda: {'names': set(), 'emails': {}, 'phones': {}})
    for rec in df.to_dict('records'):
        raw = _cell(rec.get('company'))
        key = DU.company_key(raw)
        if not key:
            continue
        g = groups[key]
        g['names'].add(DU.strip_company_badges(raw).strip())
        for e in _cell(rec.get('emails')).split(','):
            n = D.normalize_contact_value('email', e)
            if n and not DU.is_junk_contact_domain(n):
                g['emails'].setdefault(n, e.strip())
        for p in _cell(rec.get('phone')).split(','):
            n = D.normalize_contact_value('phone', p)
            if n:
                g['phones'].setdefault(n, p.strip())

    spread = defaultdict(set)
    for k, g in groups.items():
        for n in g['emails']:
            spread[('email', n)].add(k)
        for n in g['phones']:
            spread[('phone', n)].add(k)

    # Phase E discoveries, keyed the same way so they merge into the right doc.
    sitemap_by_key = {}
    sm = Path(out_dir) / 'sitemap_discovery_cap20.json'
    if sm.exists():
        for r in json.loads(sm.read_text(encoding='utf-8'))['rows']:
            k = DU.company_key(r['company'])
            if not k:
                continue
            b = sitemap_by_key.setdefault(k, {'emails': [], 'phones': []})
            b['emails'].extend(r['own_domain_emails'])
            b['phones'].extend(r['phones'])

    # --- SYMMETRIC IDENTITY CHECK, pass 1 of 2 -----------------------------
    # The builder already detects SPLITS (one company_key -> several confirmed
    # domains). The mirror case — several company_keys -> ONE domain — had no check
    # at all, and `docs[_id] = {...}` silently overwrote, dict order deciding the
    # survivor. That is how "NAVIS Human Resources" (a staffing agency) came to be
    # identified by `10xgenomics.com` (its client): `email_infer` sent both the
    # agency's row and a pasted job ad to the same domain.
    #
    # This is systematic, not a one-off. Agency recruiters mail from the client's
    # domain, so email_infer will keep proposing the client for the agency — and
    # staffing agencies are a large slice of exactly the population this project
    # cares about. The rule: when two keys claim one domain, NOBODY gets it. Both
    # fall back to `key:` identity, which is the honest answer — the agency's own
    # domain is unknown, and its client's domain is certainly not it.
    claimed = defaultdict(set)
    for key, g in groups.items():
        for name in sorted(g['names']):
            e = ident.get(name)
            if e and usable_domain(e):
                claimed[usable_domain(e)].add(key)
                break
    contested = {d: sorted(ks) for d, ks in claimed.items() if len(ks) > 1}

    docs, stats = {}, Counter()
    for key, g in groups.items():
        # identity: prefer any name in this key-group that resolved to a domain
        chosen, dom = None, ''
        for name in sorted(g['names']):
            e = ident.get(name)
            if e and usable_domain(e):
                chosen, dom = e, usable_domain(e)
                break
        # Contested domain -> refuse the merge, demote to key: identity.
        contested_reason = ''
        if dom and dom in contested:
            contested_reason = (f'domain {dom} claimed by {len(contested[dom])} '
                                f'company_keys: {contested[dom]}')
            chosen, dom = None, ''
            stats['contested_demoted'] += 1

        entry = chosen or next((ident[n] for n in sorted(g['names']) if n in ident), None) or {}
        wrong_domains = {ident[n]['domain'].lower() for n in g['names']
                         if n in ident and ident[n].get('verdict') in KNOWN_WRONG
                         and ident[n].get('domain')}
        unconf_domains = {ident[n]['domain'].lower() for n in g['names']
                          if n in ident and ident[n].get('verdict') in UNCONFIRMED
                          and ident[n].get('domain')}

        _id = f'domain:{dom}' if dom else f'key:{key}'
        stats['domain_identity' if dom else 'key_identity'] += 1

        contacts, quarantined = [], []
        for norm, original in g['emails'].items():
            edom = norm.split('@')[-1]
            c = D.build_contact('email', original or norm, SOURCE_ID,
                                company_domain=dom or None,
                                seen_on_n_companies=len(spread[('email', norm)]))
            if not c:
                continue
            if edom in wrong_domains:
                c['quarantine_reason'] = 'source_domain_parked'
                quarantined.append(c); stats['q_parked'] += 1
            elif edom in unconf_domains:
                c['quarantine_reason'] = 'source_domain_unconfirmed'
                c['reviewable'] = True
                quarantined.append(c); stats['q_unconfirmed'] += 1
            else:
                contacts.append(c)
        for norm, original in g['phones'].items():
            c = D.build_contact('phone', original or norm, SOURCE_ID,
                                company_domain=dom or None,
                                seen_on_n_companies=len(spread[('phone', norm)]))
            if not c:
                continue
            if wrong_domains or unconf_domains:
                # Phones carry no source attribution (Phase G gap) — flag, never quarantine.
                c['flag'] = 'source_domain_suspect'
                stats['flag_phone_suspect'] += 1
            contacts.append(c)

        # --- Phase E sitemap discoveries for this company -------------------
        for e in sitemap_by_key.get(key, {}).get('emails', []):
            c = D.build_contact('email', e['value'], 'sitemap',
                                company_domain=dom or None,
                                source_domain=e.get('source_domain'),
                                source_url=e.get('source_url'))
            if c and (c['type'], c['value_norm']) not in {(x['type'], x['value_norm'])
                                                          for x in contacts}:
                contacts.append(c); stats['sitemap_emails'] += 1
        for ph in sitemap_by_key.get(key, {}).get('phones', []):
            c = D.build_contact('phone', ph['value'], 'sitemap',
                                company_domain=dom or None,
                                source_domain=ph.get('source_domain'),
                                source_url=ph.get('source_url'))
            if c and (c['type'], c['value_norm']) not in {(x['type'], x['value_norm'])
                                                          for x in contacts}:
                contacts.append(c); stats['sitemap_phones'] += 1

        # Last line of defence: the contested screen above should make this
        # unreachable, but an _id must never be silently overwritten again.
        if _id in docs:
            raise RuntimeError(
                f'_id collision on {_id!r}: company_key {key!r} would overwrite '
                f'{docs[_id]["company_key"]!r}. Two companies are being merged into '
                f'one document — this is the 10xgenomics failure, uncaught.')

        docs[_id] = {
            '_id': _id,
            'identity_type': 'domain' if dom else 'key',
            'identity_note': contested_reason or None,
            'domain': dom or None,
            'company_key': key,
            'company_name': sorted(g['names'])[0],
            'aliases': sorted(g['names']),
            'identity_source': (entry or {}).get('identity_source', 'automated'),
            'contacts': contacts,
            'quarantined_contacts': quarantined,
            'superseded_by': [],
        }
        stats['contacts'] += len(contacts)
        stats['quarantined'] += len(quarantined)

    # SPLIT detection: one company_key resolving to several confirmed domains.
    by_key = defaultdict(list)
    for _id, d in docs.items():
        if d['identity_type'] == 'domain':
            by_key[d['company_key']].append(_id)
    splits = {k: v for k, v in by_key.items() if len(v) > 1}
    stats['splits'] = len(splits)
    return docs, stats, splits, contested


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    ap.add_argument('--collection', default=V3,
                    help='intended target collection; asserted before writing')
    args = ap.parse_args()

    docs, stats, splits, contested = plan(args.out_dir)
    n = len(docs)
    print(f'\n{"="*72}\nCOMPANIES_V4 PLAN (domain-first identity)\n{"="*72}')
    print(f'  documents to write        : {n}')
    print(f'    domain: identity        : {stats["domain_identity"]}  '
          f'({stats["domain_identity"]/max(n,1)*100:.1f}%)')
    print(f'    key: identity (fallback): {stats["key_identity"]}')
    print(f'  contacts carried          : {stats["contacts"]}')
    print(f'  QUARANTINED               : {stats["quarantined"]}  '
          f'(parked {stats["q_parked"]}, unconfirmed {stats["q_unconfirmed"]})')
    print(f'  phones flagged suspect    : {stats["flag_phone_suspect"]}')
    print(f'  company_key splits        : {stats["splits"]}')
    for k, ids in splits.items():
        print(f'      {k} -> {ids}')
    print(f'  CONTESTED domains (merge refused): {len(contested)}  '
          f'-> {stats["contested_demoted"]} doc(s) demoted to key:')
    for d, ks in contested.items():
        print(f'      {d} claimed by {len(ks)} keys:')
        for k in ks:
            print(f'          {k[:88]}')

    # COUNT RECONCILIATION — the identity-type counters count GROUPS, the dict counts
    # DOCUMENTS. When those disagree, two groups landed on one `_id` and one was
    # silently overwritten. The 10xgenomics merge printed as "188 + 126 = 314" against
    # "documents to write: 313" and was read past by everyone. Same class as the shape
    # check: cheap, and it catches a silent overwrite rather than a malformed one.
    tally = stats['domain_identity'] + stats['key_identity']
    if tally != n:
        print(f'\n  !! ABORT: identity counts {stats["domain_identity"]} + '
              f'{stats["key_identity"]} = {tally}, but {n} documents would be written.')
        print(f'     {tally - n} document(s) silently overwritten — two companies are '
              f'sharing an _id.')
        return 2
    print(f'\n  count reconciliation      : {stats["domain_identity"]} + '
          f'{stats["key_identity"]} = {tally} == {n} documents  OK')

    if n in (3355, 532):
        print(f'\n  !! ABORT: {n} matches a wrong-shape signature.')
        return 2
    if not (300 <= n <= 330):
        print(f'\n  !! ABORT: {n} outside expected 300-330.')
        return 2
    print(f'  OK: {n} within expected 300-330.')

    if not args.run:
        print('\nDRY RUN — nothing written. Re-run with --run once approved.')
        return 0

    db = D._get_write_db()
    # State the intended target explicitly. Host+database alone did not stop Phase F
    # step 1 writing into companies_v2; the collection is the third coordinate.
    db_guard.assert_expected_collection(V3, args.collection, operation='rebuild')
    db_guard.dump_collections(db, [V3], args.out_dir, label='pre-v4')
    before = db[V3].count_documents({})
    for _id, d in docs.items():
        db[V3].update_one({'_id': _id}, {'$set': d}, upsert=True)
    after = db[V3].count_documents({})
    print(f'\nwritten: {before} -> {after} documents in {V3}')

    # Post-write shape check: no `_id` may lack a domain:/key: prefix. This one
    # assertion would have caught all 51 malformed bare-key documents at write time.
    bad = D.assert_identity_shape(V3, db=db)
    print(f'  identity shape check      : {len(bad)} malformed _id(s)')
    if bad:
        print(f'  !! ABORT: malformed _ids present, e.g. {bad[:5]}')
        return 2

    # COMPLETENESS: the collection must hold exactly the plan and nothing else.
    # Writing a plan over a collection that already has documents leaves STRAYS —
    # any `_id` the previous plan created and this one dropped survives, because
    # nothing here deletes. That is how a demoted `domain:` document would linger
    # while its replacement `key:` document sits beside it.
    if after != len(docs):
        strays = [d['_id'] for d in db[V3].find({}, {'_id': 1})
                  if d['_id'] not in docs]
        print(f'  !! ABORT: {after} documents present but {len(docs)} planned — '
              f'{len(strays)} stray(s) from an earlier plan: {strays[:5]}')
        print(f'     Strays cannot be deleted (hard rule). Rebuild into a fresh '
              f'collection instead.')
        return 2
    print(f'  completeness check        : {after} == {len(docs)} planned  OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
