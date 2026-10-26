"""
tools/resolve_domains.py — company domain resolution with a confirmation gate.

The rule this tool exists to enforce
------------------------------------
"Resolves and returns 2xx" does NOT mean "is this company's website". genesis.com,
apex.com and infinity.in all return 2xx and belong to strangers. Because the domain
becomes the company document's `_id`, an unconfirmed guess files another company's
contacts under it — corruption by identity collision.

So every GUESSED domain must survive, in order:
  1. parked / for-sale screening   (domain_utils.is_parked_page)
  2. corroboration                 (domain_utils.confirms_company)
and generic single-word names are never guessed at all
                                   (domain_utils.should_skip_guessing).

Read-only HTTP. Opens no database. Reuses the 2xx-only disk cache from tools/bench.py
so blocked hosts are re-attempted rather than frozen in.

    python tools/resolve_domains.py --audit-baseline   # step 0: re-audit the existing 103
"""

import argparse
import json
import random
import sys
import threading
import concurrent.futures as cf
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import domain_utils as DU  # noqa: E402
from tools.bench import CachingRequests, Recorder, _tls  # noqa: E402

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    'Accept-Language': 'en-IN,en;q=0.9',
}
_rec = Recorder()
_lock = threading.Lock()


def check_domain(session, company, domain):
    """Fetch a candidate and run it through parked-screening then corroboration."""
    _tls.source = 'domain'
    _tls.company = company
    row = {'company': company, 'domain': domain, 'status': None,
           'verdict': None, 'reason': '', 'evidence': ''}
    try:
        r = session.get(f'https://{domain}', headers=HEADERS, timeout=8,
                        allow_redirects=True)
    except Exception as e:
        row.update(verdict='unreachable', reason=type(e).__name__)
        return row

    row['status'] = r.status_code
    if not (200 <= r.status_code < 300):
        row.update(verdict='unreachable', reason=f'http_{r.status_code}')
        return row

    html = r.text or ''
    final_url = str(getattr(r, 'url', '') or '')
    row['final_url'] = final_url

    parked, why = DU.is_parked_page(html, final_url)
    if parked:
        row.update(verdict='parked', reason=why)
        return row

    # An empty body is NOT parking — it is a soft-block or JS shell, so we cannot
    # verify either way. Recorded separately so it never counts as a confirmation
    # and never masquerades as proof the domain is wrong.
    empty, why = DU.is_empty_shell(html)
    if empty:
        row.update(verdict='unverifiable', reason=why)
        return row

    ok, evidence = DU.confirms_company(company, html, final_url)
    if not ok:
        row.update(verdict='unconfirmed', reason=evidence)
        return row

    # Corroboration passed — but for a generic dictionary word it is circular
    # (genesis.com's title says "Genesis"), and for a short single token it is
    # suggestive rather than conclusive. Grade accordingly.
    level, why = DU.guess_risk(company)
    title, og_site, og_title, _ = DU._extract_signals(html)
    row['title'] = (title or og_site or og_title or '').strip()[:100]
    if level == 'reject':
        row.update(verdict='rejected_generic', reason=why, evidence=evidence)
    elif level == 'weak':
        row.update(verdict='confirmed_weak', reason=why, evidence=evidence)
    else:
        row.update(verdict='confirmed', reason='', evidence=evidence)
    return row


def audit_baseline(args):
    """Step 0 — re-audit the domains previously counted as 'reachable'."""
    probe_path = Path(args.out_dir) / 'ats_probe.json'
    if not probe_path.exists():
        print(f'FATAL: {probe_path} not found (run tools/ats_probe.py first).')
        return 2
    rows = json.loads(probe_path.read_text(encoding='utf-8'))['rows']
    total_companies = len(rows)

    # The population previously counted toward "32.1% reachable".
    candidates = [(r['company'], r['domain']) for r in rows
                  if r.get('reachable') and r.get('domain')
                  and not DU.is_job_board_host(r['domain'])]

    print(f'companies in corpus                 : {total_companies}')
    print(f'previously counted as reachable     : {len(candidates)}  '
          f'({len(candidates)/total_companies*100:.1f}%)')
    print(f'now re-checked through the gate (parked -> corroboration)\n')

    session = CachingRequests(__import__('requests'),
                              Path(args.out_dir) / 'http_cache', _rec)
    results, done = [], [0]

    def work(pair):
        res = check_domain(session, *pair)
        with _lock:
            done[0] += 1
            if done[0] % 20 == 0:
                print(f'  ...{done[0]}/{len(candidates)}', flush=True)
        return res

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(work, candidates))

    verdicts = Counter(r['verdict'] for r in results)
    confirmed = [r for r in results if r['verdict'] == 'confirmed']
    n = total_companies

    print(f'\n{"="*74}\nSTEP 0 — RE-AUDIT OF THE EXISTING BASELINE\n{"="*74}')
    for v in ('confirmed', 'confirmed_weak', 'unconfirmed', 'unverifiable', 'parked', 'rejected_generic', 'unreachable'):
        c = verdicts.get(v, 0)
        print(f'  {v:<14}{c:>5}   {c/max(len(candidates),1)*100:>5.1f}% of re-checked')
    print('-'*74)
    print(f'  raw reachable      : {len(candidates):>4} / {n}  = {len(candidates)/n*100:.1f}%'
          f'   (the old headline)')
    print(f'  CONFIRMED reachable: {len(confirmed):>4} / {n}  = {len(confirmed)/n*100:.1f}%'
          f'   <-- TRUE BASELINE')
    drop = len(candidates) - len(confirmed)
    if candidates:
        print(f'  precision of the old number: {len(confirmed)/len(candidates)*100:.1f}% '
              f'({drop} of {len(candidates)} were NOT this company)')

    print(f'\n{"="*74}\n20 RANDOM CONFIRMED (strong) DOMAINS — eyeball these\n{"="*74}')
    sample = random.Random(42).sample(confirmed, min(20, len(confirmed)))
    for r in sorted(sample, key=lambda x: x['company'].lower()):
        print(f'  {r["company"][:32]:<34}{r["domain"][:30]:<32}{r["evidence"][:46]}')

    # --- human review queue -------------------------------------------------
    weak = sorted([r for r in results if r['verdict'] == 'confirmed_weak'],
                  key=lambda x: x['company'].lower())
    print(f'\n{"="*74}\nREVIEW QUEUE — {len(weak)} weak confirmations\n{"="*74}')
    print('These are NOT used as `_id`. Until approved, these companies fall back to')
    print('key: identity. Mark each approve/reject and hand back.\n')
    for r in weak:
        print(f'  [ ] {r["company"][:26]:<28}{r["domain"][:26]:<28}{r.get("title","")[:52]}')
    queue = Path(args.out_dir) / 'domain_review_queue.txt'
    queue.write_text(
        '# Domain review queue — mark each line APPROVE or REJECT and hand back.\n'
        '# These weak confirmations are NOT used as _id until approved; the company\n'
        '# falls back to key: identity meanwhile.\n'
        '# company | domain | page title\n\n' +
        '\n'.join(f'[ ] {r["company"]} | {r["domain"]} | {r.get("title","")}'
                  for r in weak) + '\n', encoding='utf-8')

    rejected = [r for r in results
                if r['verdict'] in ('unconfirmed', 'parked', 'unverifiable', 'rejected_generic')]
    print(f'\n{"="*74}\n15 REJECTED (why the old number was wrong)\n{"="*74}')
    for r in random.Random(42).sample(rejected, min(15, len(rejected))):
        print(f'  {r["company"][:30]:<32}{r["domain"][:28]:<30}'
              f'{r["verdict"]:<18}{r["reason"][:26]}')
    print(f'\nreview queue written: {queue}')

    out = Path(args.out_dir) / 'domain_audit_baseline.json'
    out.write_text(json.dumps({
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'companies': n,
        'raw_reachable': len(candidates),
        'raw_reachable_pct': round(len(candidates)/n*100, 1),
        'confirmed': len(confirmed),
        'confirmed_pct': round(len(confirmed)/n*100, 1),
        'precision_pct': round(len(confirmed)/max(len(candidates),1)*100, 1),
        'verdicts': dict(verdicts),
        'rows': results,
    }, indent=2), encoding='utf-8')
    print(f'\nfull results: {out}')
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# Full resolution — four strategies, first CONFIRMED wins
# ═════════════════════════════════════════════════════════════════════════════
def _corpus():
    """(company -> {company_url, job_urls[], csv_emails[]}) from the CSV manifest."""
    import pandas as pd
    from tools.bench import MANIFEST, _cell
    df = pd.concat([pd.read_csv(REPO_ROOT / r, on_bad_lines='skip', low_memory=False)
                    for r, _ in MANIFEST], ignore_index=True)
    out = {}
    for rec in df.to_dict('records'):
        c = _cell(rec.get('company'))
        if not c:
            continue
        e = out.setdefault(c, {'company_url': '', 'job_urls': [], 'csv_emails': []})
        if not e['company_url']:
            e['company_url'] = _cell(rec.get('company_url'))
        ju = _cell(rec.get('job_url'))
        if ju and ju not in e['job_urls']:
            e['job_urls'].append(ju)
        for em in _cell(rec.get('emails')).split(','):
            em = em.strip().lower()
            if em and '@' in em and em not in e['csv_emails']:
                e['csv_emails'].append(em)
    return out


def _mine_listing(session, company, job_urls, max_pages=2):
    """Outbound company link from a listing page. Returns (domain, host_of_listing)."""
    from bs4 import BeautifulSoup
    for ju in job_urls[:max_pages]:
        listing_host = DU._host_of(ju)
        try:
            r = session.get(ju, headers=HEADERS, timeout=8, allow_redirects=True)
        except Exception:
            continue
        if not (200 <= r.status_code < 300):
            continue
        try:
            soup = BeautifulSoup(r.text or '', 'lxml')
        except Exception:
            continue
        cands = []
        for a in soup.find_all('a', href=True):
            h = DU._host_of(a['href'])
            if not h or '.' not in h:
                continue
            if DU.is_job_board_host(h) or DU.is_non_company_host(h):
                continue
            cands.append(h)
        if not cands:
            continue
        key = DU.company_key(company).replace('-', '')
        # prefer a host whose slug resembles the company name
        for h in cands:
            stem = h.split('.')[0].replace('-', '')
            if key and (key in stem or stem in key):
                return h, listing_host
        return cands[0], listing_host
    return '', ''


def _infer_from_emails(csv_emails):
    """Company domain from CSV/hrdb emails only — NEVER from scraped contacts.

    Circularity guard: this reads the CSV corpus and the bundled hrdb, both of
    which predate any guessing done here. A contact scraped off a guessed domain
    can never reach this function, so a wrong guess cannot reinforce itself.
    """
    from collections import Counter, defaultdict
    doms = Counter()
    for e in csv_emails:
        if DU.is_junk_contact_domain(e):
            continue
        d = e.split('@')[-1].strip().lower()
        if not d:
            continue
        if d in PERSONAL_PROVIDERS or DU.is_job_board_host(d) or DU.is_non_company_host(d):
            continue
        doms[d] += 1
    return doms.most_common(1)[0][0] if doms else ''


PERSONAL_PROVIDERS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.in', 'yahoo.co.in',
    'ymail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
    'rediffmail.com', 'rediff.com', 'icloud.com', 'aol.com', 'protonmail.com',
    'proton.me', 'zoho.com', 'mail.com',
}


def resolve_company(session, company, info):
    """Run the strategies in order; first CONFIRMED (or weak) wins."""
    attempts = []

    def _try(domain, strategy):
        if not domain:
            return None
        res = check_domain(session, company, domain)
        res['strategy'] = strategy
        attempts.append(res)
        return res

    # 1. CSV company_url — user-supplied, still parked-screened
    r = _try(DU.clean_company_domain(info.get('company_url', '')), 'csv_url')
    if r and r['verdict'] in ('confirmed', 'confirmed_weak'):
        return r, attempts

    # 2. guessed slug across TLDs — only when the name is guessable.
    #
    # NEGATIVE EVIDENCE RULE: if the .com candidate is actively WRONG (parked, a
    # broker page, or a different company), that is evidence the company does not
    # own an obvious slug domain at all — so stop guessing rather than falling
    # through to .org/.in. Observed: rejecting meril.com (broker) promoted
    # meril.org, a Michigan non-profit; rejecting msi.co.in promoted msi.org, a
    # different MSI. Falling through finds a stranger more often than the company.
    # "No domain" beats "a stranger's domain" when the domain is the document _id.
    #
    # `unreachable` and `unverifiable` do NOT stop the search: a .com that does not
    # resolve, or that soft-blocks us, says nothing about who owns the .in.
    # Only POSITIVE evidence of wrongness stops the search: a parked/broker page,
    # or a confirmed different company. `unconfirmed` means corroboration FAILED,
    # which is a different thing — aurolab.com is a real site whose <title> is
    # just "Home". Including it cost PSK Infra its genuine pskinfraprojects.in
    # while saving nothing, so it is excluded.
    NEGATIVE = {'parked', 'rejected_generic'}
    level, why = DU.guess_risk(company)
    if level != 'reject':
        for i, cand in enumerate(DU.candidate_domains(company)):
            r = _try(cand, 'guess_tld')
            if r and r['verdict'] in ('confirmed', 'confirmed_weak'):
                return r, attempts
            if i == 0 and r and r['verdict'] in NEGATIVE:
                r['stopped_tld_search'] = True
                break

    # 3. listing-page mining
    mined, listing_host = _mine_listing(session, company, info.get('job_urls') or [])
    if mined:
        r = _try(mined, 'listing_mine')
        if r:
            r['listing_host'] = listing_host
            if r['verdict'] in ('confirmed', 'confirmed_weak'):
                return r, attempts

    # 4. email-domain inference (CSV/hrdb only — non-circular)
    r = _try(_infer_from_emails(info.get('csv_emails') or []), 'email_infer')
    if r and r['verdict'] in ('confirmed', 'confirmed_weak'):
        return r, attempts

    best = attempts[-1] if attempts else {'company': company, 'domain': '',
                                          'verdict': 'none', 'reason': 'no_candidate',
                                          'strategy': 'none'}
    return best, attempts


def resolve_all(args):
    corpus = _corpus()
    names = sorted(corpus)
    if args.limit:
        names = names[:args.limit]
    print(f'Resolving {len(names)} companies (4 strategies, confirmation gate on)\n')

    session = CachingRequests(__import__('requests'),
                              Path(args.out_dir) / 'http_cache', _rec)
    done = [0]

    def work(name):
        best, attempts = resolve_company(session, name, corpus[name])
        with _lock:
            done[0] += 1
            if done[0] % 25 == 0:
                print(f'  ...{done[0]}/{len(names)}', flush=True)
        return {'company': name, 'best': best, 'attempts': attempts}

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(work, names))

    n = len(rows)
    strong = [r for r in rows if r['best'].get('verdict') == 'confirmed']
    weak = [r for r in rows if r['best'].get('verdict') == 'confirmed_weak']
    by_strategy = Counter(r['best'].get('strategy') for r in strong)
    weak_by_strategy = Counter(r['best'].get('strategy') for r in weak)

    print(f'\n{"="*74}\nFULL RESOLUTION — CONFIRMED DOMAIN COVERAGE\n{"="*74}')
    print(f'  companies                 : {n}')
    print(f'  BEFORE (step 0 baseline)  : 68 / 321 = 21.2%   [strong-confirmed]')
    print(f'  AFTER  strong-confirmed   : {len(strong)} / {n} = {len(strong)/n*100:.1f}%')
    print(f'  AFTER  + weak (if approved): {len(strong)+len(weak)} / {n} = '
          f'{(len(strong)+len(weak))/n*100:.1f}%')

    print(f'\n{"per strategy":<20}{"strong":>8}{"weak":>8}')
    print('-'*40)
    for s in ('csv_url', 'guess_tld', 'listing_mine', 'email_infer'):
        print(f'  {s:<18}{by_strategy.get(s,0):>8}{weak_by_strategy.get(s,0):>8}')

    # per-host mining yield
    mine_att = [a for r in rows for a in r['attempts'] if a.get('strategy') == 'listing_mine']
    hosts = defaultdict(lambda: [0, 0])
    for r in rows:
        for a in r['attempts']:
            if a.get('strategy') != 'listing_mine':
                continue
            h = a.get('listing_host') or '?'
            hosts[h][0] += 1
            if a['verdict'] in ('confirmed', 'confirmed_weak'):
                hosts[h][1] += 1
    print(f'\n{"listing host":<28}{"mined":>8}{"usable":>8}')
    print('-'*46)
    for h, (tot, ok) in sorted(hosts.items(), key=lambda x: -x[1][0]):
        print(f'  {h[:26]:<26}{tot:>8}{ok:>8}')
    if not hosts:
        print('  (no listing pages yielded an outbound company link)')

    out = Path(args.out_dir) / 'domain_resolution.json'
    out.write_text(json.dumps({
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'companies': n,
        'baseline_strong': 68, 'baseline_pct': 21.2,
        'strong': len(strong), 'strong_pct': round(len(strong)/n*100, 1),
        'weak': len(weak),
        'by_strategy_strong': dict(by_strategy),
        'by_strategy_weak': dict(weak_by_strategy),
        'mining_by_host': {h: {'mined': t, 'usable': u} for h, (t, u) in hosts.items()},
        'rows': rows,
    }, indent=2, default=str), encoding='utf-8')
    print(f'\nfull results: {out}')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit-baseline', action='store_true',
                    help='step 0: re-audit the previously-reachable domains')
    ap.add_argument('--resolve-all', action='store_true',
                    help='full resolution across all companies')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    args = ap.parse_args()
    if args.audit_baseline:
        return audit_baseline(args)
    if args.resolve_all:
        return resolve_all(args)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
