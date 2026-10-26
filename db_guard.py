"""
db_guard.py — the single shared safety gate for every MongoDB write.

Why this module exists
----------------------
A previous session wiped this project's production database. The connection layer
hardcoded a database name and ignored the one in the URI, so an operation intended
for a throwaway test database silently hit production instead.

Two lessons are encoded here:

1. **Host alone is not isolation.** The local mongod on this machine also hosts
   unrelated projects (`book_exchange_db` holds real data). Asserting only
   "is this localhost?" would happily write into someone else's collection — the
   exact failure mode as the original incident: right host, wrong database.
   So the guard pins **host AND database name**.

2. **Defaults must fail safe.** The guard is ACTIVE unless `ALLOW_REMOTE_DB=1` is
   explicitly set. Forgetting the variable gives you more protection, not less.

   Production consequence: Render writes to Atlas, so `ALLOW_REMOTE_DB=1` must be
   set in its environment or production writes will abort. That is deliberate —
   remote writes should require a conscious act.

This module performs no destructive operations and contains no delete/drop calls.
"""

import json
import os
from datetime import datetime
from pathlib import Path

LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}
DEV_DB_NAME = 'placementradar_dev'
BYPASS_ENV = 'ALLOW_REMOTE_DB'

_REPO_ROOT = Path(__file__).resolve().parent


class UnsafeWriteTarget(RuntimeError):
    """Raised when a write would land somewhere it must not."""


def _resolve_host(client):
    """Return (host, port) of the node actually connected to, or (None, None).

    `MongoClient.address` RAISES (not returns None) when the topology is unknown —
    e.g. an unreachable remote host. Swallow that so the caller can emit a clear
    "refusing to write" message instead of a bare ServerSelectionTimeoutError.
    Unresolvable host => (None, None) => the guard refuses. Fail safe.
    """
    def _addr():
        try:
            return client.address
        except Exception:
            return None

    addr = _addr()
    if addr is None:
        try:
            client.admin.command('ping')      # force topology discovery
        except Exception:
            pass
        addr = _addr()
    if addr:
        return addr[0], addr[1]

    try:
        nodes = list(client.nodes or [])
    except Exception:
        nodes = []
    if nodes:
        return nodes[0][0], nodes[0][1]
    return None, None


def describe_target(db):
    host, port = _resolve_host(db.client)
    return f'{host}:{port}/{db.name}'


def assert_local_write(db, expected_db=DEV_DB_NAME, operation='write'):
    """Abort unless `db` is a local mongod AND the expected database.

    Prints the resolved target before deciding, so the destination is always
    visible in the log of whatever is about to write.
    """
    host, port = _resolve_host(db.client)
    target = f'{host}:{port}/{db.name}'
    print(f'[db_guard] {operation} target -> {target}')

    if os.environ.get(BYPASS_ENV) == '1':
        print(f'[db_guard] {BYPASS_ENV}=1 set — guard bypassed (remote writes allowed)')
        return target

    if host not in LOCAL_HOSTS:
        raise UnsafeWriteTarget(
            f'Refusing to {operation}: host {host!r} is not local.\n'
            f'  resolved target : {target}\n'
            f'  allowed hosts   : {sorted(LOCAL_HOSTS)}\n'
            f'  If this is genuinely intended, set {BYPASS_ENV}=1 explicitly.'
        )

    if expected_db and db.name != expected_db:
        raise UnsafeWriteTarget(
            f'Refusing to {operation}: database {db.name!r} is not {expected_db!r}.\n'
            f'  resolved target : {target}\n'
            f'  This mongod also hosts unrelated databases; a correct host is not\n'
            f'  enough. Point MONGO_URI at /{expected_db} or pass expected_db=.'
        )

    return target


def assert_expected_collection(actual, expected, operation='write'):
    """Abort unless a write targets the collection the caller intended.

    The host+database assertion is not enough on its own. Phase F step 1 passed it
    cleanly and still wrote 214 contacts into `companies_v2` — the superseded
    reference collection — because `COMPANIES_COLLECTION` still defaulted to v2
    while the v3 builder used a hardcoded literal. Right host, right database,
    wrong collection is the same failure shape as the original wipe, one level down.

    Callers that know their intended target should state it explicitly.
    """
    print(f'[db_guard] {operation} collection -> {actual}')
    if expected and actual != expected:
        raise UnsafeWriteTarget(
            f'Refusing to {operation}: collection {actual!r} is not the expected '
            f'{expected!r}.\n'
            f'  A constant and a local literal have probably drifted apart — check\n'
            f'  database.COMPANIES_COLLECTION against any hardcoded name.'
        )
    return actual


def dump_collections(db, names, out_dir, label='pre-write'):
    """Snapshot collections to disk BEFORE a bulk write, so it is reversible.

    Writes even when a collection is empty or missing — an empty snapshot is still
    a valid restore point and proves the collection's state at that moment.
    Returns the directory, which the caller should surface to the user.
    """
    out_dir = Path(out_dir).expanduser().resolve()
    if out_dir == _REPO_ROOT or _REPO_ROOT in out_dir.parents:
        raise UnsafeWriteTarget(
            f'Refusing to dump inside the repo ({out_dir}). Dumps contain real '
            f'contact data; .gitignore does not protect against `zip -r`.'
        )

    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = out_dir / 'dumps' / f'{ts}-{label}'
    dest.mkdir(parents=True, exist_ok=True)

    counts = {}
    existing = set(db.list_collection_names())
    for name in names:
        docs = list(db[name].find({})) if name in existing else []
        counts[name] = len(docs)
        (dest / f'{name}.json').write_text(
            json.dumps(docs, indent=2, default=str), encoding='utf-8')

    (dest / '_manifest.json').write_text(json.dumps({
        'created_at': datetime.now().isoformat(),
        'target': describe_target(db),
        'label': label,
        'counts': counts,
    }, indent=2), encoding='utf-8')

    total = sum(counts.values())
    print(f'[db_guard] restore point written: {dest}')
    print(f'[db_guard]   {total} document(s) across {len(names)} collection(s): {counts}')
    return dest
