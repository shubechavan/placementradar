"""
tools/verify_contacts.py — Phase F steps 2-4: make `verification` mean something.

Every contact in this project is currently unverified. A placement committee member
cannot tell a live recruiter address from one scraped off a 2024 blog post, and
`confidence()` is measuring "has a confirmed domain" rather than contact quality
(NOTES §7, calibration). Verification is the first thing that can break that coupling.

WHAT IS AND IS NOT CHECKABLE HERE — measured before building, not assumed:

  * MX lookup      WORKS. ~93% of domains resolve MX.
  * SMTP RCPT TO   PORT 25 IS BLOCKED from this machine. The code path exists and is
                   off by default; it ships UNEXERCISED and this file says so rather
                   than implying coverage.
  * phone filters  EXPECTED NO-OPS on today's data (157/157 already pass the type
                   filter; no number appears on >3 companies). They are forward-looking
                   guards so a listing-site switchboard is caught automatically later.

THE SMTP CONTRACT, which is the whole point of the flag:

    A connect timeout or refusal records  smtp_ok=None, smtp_blocked=True.
    It NEVER records smtp_ok=False.

"We could not check" must never be stored as "the mailbox is bad" — that is the
difference between an unverified contact and a contact wrongly marked dead, and only
one of those is recoverable.

DRY-RUN BY DEFAULT. Contains no delete/drop/remove/$pull of any kind.

    python tools/verify_contacts.py                  # dry run, everything
    python tools/verify_contacts.py --run
    python tools/verify_contacts.py --skip-hrdb --limit 40
    VERIFY_SMTP=1 python tools/verify_contacts.py    # attempts SMTP (blocked here)
"""

import argparse
import csv
import os
import re
import shutil
import socket
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('MONGO_URI', 'mongodb://127.0.0.1:27017/placementradar_dev')
os.environ.pop('GH_PAT', None)

import dns.resolver                      # noqa: E402
import phonenumbers                      # noqa: E402
from phonenumbers import PhoneNumberType  # noqa: E402

import database as D                     # noqa: E402
import db_guard                          # noqa: E402

VERIFIER = 'phase-f-mx'
SMTP_ENABLED = os.environ.get('VERIFY_SMTP') == '1'

# Types that can plausibly reach a human recruiter. FIXED_LINE_OR_MOBILE is the
# "cannot tell from the number alone" bucket and must be kept — discarding it would
# throw away 15 real numbers to remove none.
ALLOWED_PHONE_TYPES = {
    PhoneNumberType.MOBILE,
    PhoneNumberType.FIXED_LINE,
    PhoneNumberType.FIXED_LINE_OR_MOBILE,
}
MAX_COMPANIES_PER_NUMBER = 3

_TYPE_NAMES = {v: k for k, v in vars(PhoneNumberType).items() if isinstance(v, int)}


def type_name(t):
    """`number_type()` returns an INT. Comparing it to enum NAMES silently reports
    every number as rejected — that error produced a '0/157 accepted' figure once."""
    return _TYPE_NAMES.get(t, f'UNKNOWN({t})')


# ── MX ────────────────────────────────────────────────────────────────────────
class MXChecker:
    """Per-domain MX cache. 501 contacts collapse to ~150 distinct domains.

    Timeouts are RETRIED ONCE and reported separately from real failures: in the
    feasibility spike 3 of 60 domains timed out transiently and a single-shot check
    would have recorded them as dead.
    """

    def __init__(self, timeout=5.0):
        self.cache = {}
        self.stats = Counter()
        self.resolver = dns.resolver.Resolver()
        self.resolver.timeout = timeout
        self.resolver.lifetime = timeout

    def check(self, domain):
        d = (domain or '').strip().lower().rstrip('.')
        if not d:
            return None, 'no_domain'
        if d in self.cache:
            self.stats['cache_hit'] += 1
            return self.cache[d]

        result = self._query(d)
        if result[1] == 'timeout':
            self.stats['timeout_retry'] += 1
            result = self._query(d)
        self.cache[d] = result
        self.stats[result[1]] += 1
        return result

    def _query(self, d):
        try:
            ans = self.resolver.resolve(d, 'MX')
            hosts = sorted(str(r.exchange).rstrip('.') for r in ans)
            return True, f'mx:{hosts[0]}' if hosts else (True, 'mx:present')
        except dns.resolver.NXDOMAIN:
            return False, 'nxdomain'
        except dns.resolver.NoAnswer:
            # Domain exists but publishes no MX. RFC 5321 allows falling back to A,
            # so this is "probably not mailable", not proof — hence False, not a
            # separate hard-dead state.
            return False, 'no_mx'
        except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
            return None, 'timeout'
        except Exception as e:
            return None, f'error:{type(e).__name__}'


# ── SMTP (ships unexercised — port 25 is blocked from this machine) ───────────
def smtp_probe(email, mx_host, timeout=8.0):
    """RCPT TO probe. Returns (smtp_ok, blocked, detail).

    smtp_ok is None whenever the answer is unknown. It is NEVER False for a network
    condition — only for an explicit 5xx rejection of the recipient.
    """
    import smtplib
    try:
        srv = smtplib.SMTP(timeout=timeout)
        srv.connect(mx_host, 25)
        srv.helo('placementradar.local')
        srv.mail('verify@placementradar.local')
        code, msg = srv.rcpt(email)
        try:
            srv.quit()
        except Exception:
            pass
        if code in (250, 251):
            return True, False, f'rcpt_{code}'
        if 500 <= code < 600:
            return False, False, f'rcpt_{code}'
        return None, False, f'rcpt_{code}'          # 4xx = greylisting, unknown
    except (socket.timeout, TimeoutError, ConnectionRefusedError, OSError) as e:
        # The blocked case. Unknown, not bad.
        return None, True, f'blocked:{type(e).__name__}'
    except Exception as e:
        return None, True, f'error:{type(e).__name__}'


# ── email + phone verification ───────────────────────────────────────────────
_SYNTAX_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[a-z]{2,}$')


def verify_email(value, mx: MXChecker):
    v = (value or '').strip().lower()
    out = {'syntax_ok': bool(_SYNTAX_RE.match(v)), 'mx_ok': None, 'smtp_ok': None,
           'catch_all': None, 'checked_at': datetime.now().isoformat(),
           'verified_by': VERIFIER}
    if not out['syntax_ok']:
        out['detail'] = 'bad_syntax'
        return out

    domain = v.split('@')[-1]
    ok, detail = mx.check(domain)
    out['mx_ok'] = ok
    out['detail'] = detail

    if not SMTP_ENABLED:
        out['smtp_skipped'] = True
        return out
    if ok is not True:
        out['smtp_skipped'] = 'no_mx'
        return out

    host = detail.split(':', 1)[1] if detail.startswith('mx:') else domain
    smtp_ok, blocked, sdetail = smtp_probe(v, host)
    out['smtp_ok'] = smtp_ok
    out['smtp_detail'] = sdetail
    if blocked:
        out['smtp_blocked'] = True
    return out


def verify_phone(value, seen_on_n_companies=1, region='IN'):
    out = {'checked_at': datetime.now().isoformat(), 'verified_by': VERIFIER,
           'is_valid': None, 'number_type': None, 'rejected': None}
    try:
        p = phonenumbers.parse(value, region)
    except Exception as e:
        out['rejected'] = f'unparseable:{type(e).__name__}'
        return out
    out['is_valid'] = phonenumbers.is_valid_number(p)
    t = phonenumbers.number_type(p)
    out['number_type'] = type_name(t)
    if not out['is_valid']:
        out['rejected'] = 'invalid_number'
    elif t not in ALLOWED_PHONE_TYPES:
        out['rejected'] = f'type_{type_name(t)}'
    elif seen_on_n_companies > MAX_COMPANIES_PER_NUMBER:
        out['rejected'] = f'shared_across_{seen_on_n_companies}_companies'
    return out


# ── step 2+3: the company store ──────────────────────────────────────────────
def verify_store(collection, limit=None):
    """Pure computation over the store. Returns (updates, report). No writes."""
    db = D._get_db()
    docs = list(db[collection].find({}))

    # Spread counts DISTINCT COMPANIES, not documents — two documents for one company
    # must not look like a shared switchboard. See database.identity_groups().
    spread = D.contact_spread(docs)

    mx = MXChecker()
    updates, rep = {}, Counter()
    conf_before, conf_after = [], []
    n_seen = 0

    for d in docs:
        contacts = d.get('contacts') or []
        if not contacts:
            continue
        new_contacts, changed = [], False
        for c in contacts:
            c = dict(c)
            n_seen += 1
            if limit and n_seen > limit:
                new_contacts.append(c)
                continue
            seen_n = spread.get((c.get('type'), c.get('value_norm')), 1)
            conf_before.append(c.get('confidence') or 0.0)

            if c.get('type') == 'email':
                v = verify_email(c.get('value_norm') or c.get('value'), mx)
                c['verification'] = v
                rep['emails_checked'] += 1
                rep[f'mx_{v.get("mx_ok")}'] += 1
                if v.get('smtp_blocked'):
                    rep['smtp_blocked'] += 1
            else:
                v = verify_phone(c.get('value_norm') or c.get('value'), seen_n)
                c['verification'] = v
                rep['phones_checked'] += 1
                rep[f'phone_{v.get("number_type")}'] += 1
                if v.get('rejected'):
                    rep['phone_rejected'] += 1
                    rep[f'phone_reject_{v["rejected"]}'] += 1

            # Step 5 groundwork: rescore with verification present.
            c['confidence'] = D.confidence(c, d.get('domain'), seen_n)
            conf_after.append(c['confidence'])
            changed = True
            new_contacts.append(c)

        if changed:
            updates[d['_id']] = new_contacts

    return updates, {
        'docs': len(docs), 'counters': rep, 'mx_stats': mx.stats,
        'mx_domains': len(mx.cache),
        'conf_before': conf_before, 'conf_after': conf_after,
    }


# ── step 4: hrdb staleness sweep ─────────────────────────────────────────────
def sweep_hrdb(path, mx: MXChecker, out_dir, do_write):
    """MX-check every hrdb row's domain and fill the provenance columns.

    MARK, NEVER DELETE — a dead row keeps its history and can be revived. This file
    is a hand-compiled asset and contributed more email contacts than all ten live
    scrapers combined; losing a row to a transient DNS failure is unacceptable.
    """
    if not path.exists():
        print(f'  hrdb not found at {path} — skipped')
        return None

    with path.open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print('  hrdb is empty — skipped')
        return None

    cols = list(rows[0].keys())
    stats = Counter()
    today = datetime.now().strftime('%Y-%m-%d')

    for r in rows:
        email = (r.get('hr_email') or r.get('email') or '').strip().lower()
        if not email or '@' not in email:
            r['status'] = 'unknown'
            stats['unknown_no_email'] += 1
            continue
        ok, detail = mx.check(email.split('@')[-1])
        if ok is True:
            r['status'] = 'verified'
            stats['verified'] += 1
        elif ok is False:
            r['status'] = 'dead'
            stats[f'dead_{detail}'] += 1
            stats['dead'] += 1
        else:
            # Timeout or resolver error. NOT dead — unknown. A transient DNS failure
            # must never downgrade a row.
            r['status'] = 'unknown'
            stats['unknown_transient'] += 1
        r['last_verified'] = today
        r['verified_by'] = VERIFIER

    if do_write:
        backup_dir = Path(out_dir) / 'dumps'
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = backup_dir / f'{stamp}-hr_database.csv'
        shutil.copy2(path, backup)
        print(f'  backup written: {backup}')
        with path.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f'  hrdb updated in place: {len(rows)} rows (0 removed)')

    return stats, len(rows)


# ── reporting ────────────────────────────────────────────────────────────────
def dist(vals):
    c = Counter(round(v, 2) for v in vals)
    return ', '.join(f'{k}:{c[k]}' for k in sorted(c))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--collection', default=D.COMPANIES_COLLECTION)
    ap.add_argument('--out-dir', default=str(REPO_ROOT.parent / 'placementradar-bench'))
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--skip-hrdb', action='store_true')
    ap.add_argument('--skip-store', action='store_true')
    args = ap.parse_args()

    print(f'\n{"="*74}\nPHASE F — CONTACT VERIFICATION\n{"="*74}')
    print(f'  collection : {args.collection}')
    print(f'  SMTP       : {"ENABLED" if SMTP_ENABLED else "disabled (VERIFY_SMTP=0)"}'
          f'   — port 25 is blocked from this machine; this path is unexercised')

    updates, rep = {}, None
    if not args.skip_store:
        updates, rep = verify_store(args.collection, args.limit)
        c = rep['counters']
        print(f'\n-- STEP 2: EMAIL (MX) ---------------------------------------')
        print(f'  emails checked            : {c["emails_checked"]}')
        print(f'  distinct domains resolved : {rep["mx_domains"]}')
        print(f'    mx_ok = True            : {c["mx_True"]}')
        print(f'    mx_ok = False           : {c["mx_False"]}')
        print(f'    mx_ok = None (unknown)  : {c["mx_None"]}')
        print(f'  resolver outcomes         : {dict(rep["mx_stats"])}')
        print(f'  smtp_blocked recorded     : {c["smtp_blocked"]}   '
              f'(smtp_ok stays None — never False)')

        print(f'\n-- STEP 3: PHONE --------------------------------------------')
        print(f'  phones checked            : {c["phones_checked"]}')
        for k in sorted(k for k in c if k.startswith('phone_') and
                        not k.startswith('phone_reject')):
            print(f'    {k[6:]:26}: {c[k]}')
        print(f'  REJECTED                  : {c["phone_rejected"]}')
        for k in sorted(k for k in c if k.startswith('phone_reject_')):
            print(f'    {k[13:]:26}: {c[k]}')

        print(f'\n-- STEP 5 PREVIEW: CONFIDENCE -------------------------------')
        print(f'  before : {dist(rep["conf_before"])}')
        print(f'  after  : {dist(rep["conf_after"])}')
        b = sum(1 for v in rep['conf_before'] if v > 0)
        a = sum(1 for v in rep['conf_after'] if v > 0)
        n = len(rep['conf_before']) or 1
        print(f'  scoring > 0: {b}/{n} ({b/n*100:.1f}%) -> {a}/{n} ({a/n*100:.1f}%)')
        print(f'  documents to update       : {len(updates)}')

    hr = None
    if not args.skip_hrdb:
        print(f'\n-- STEP 4: hrdb STALENESS SWEEP -----------------------------')
        hr = sweep_hrdb(REPO_ROOT / 'hr_database.csv', MXChecker(),
                        args.out_dir, args.run)
        if hr:
            stats, nrows = hr
            print(f'  rows                      : {nrows}  (0 removed — mark, never delete)')
            print(f'    verified                : {stats["verified"]}')
            print(f'    dead                    : {stats["dead"]}')
            print(f'    unknown (transient/none): '
                  f'{stats["unknown_transient"] + stats["unknown_no_email"]}')
            for k in sorted(k for k in stats if k.startswith('dead_')):
                print(f'      {k[5:]:24}: {stats[k]}')

    if not args.run:
        print('\nDRY RUN — nothing written. Re-run with --run once approved.')
        return 0

    if updates:
        db = D._get_write_db()
        db_guard.assert_expected_collection(D.COMPANIES_COLLECTION, args.collection,
                                            operation='write verification')
        db_guard.dump_collections(db, [args.collection], args.out_dir,
                                  label='pre-verify')
        for _id, contacts in updates.items():
            db[args.collection].update_one(
                {'_id': _id},
                {'$set': {'contacts': contacts,
                          'verified_at': datetime.now().isoformat()}})
        print(f'\n  wrote verification onto {len(updates)} documents in {args.collection}')
        bad = D.assert_identity_shape(args.collection, db=db)
        print(f'  identity shape check      : {len(bad)} malformed _id(s)')
        if bad:
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
