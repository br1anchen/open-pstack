"""Routing under the git store: which Amp thread plays which role of a run, and what was sent to it, is the master's, so the master records it on
the store branch as one control record per thread, `threads/<thread-id>.json`, and a master in a fresh clone with an empty state home rebuilds its
caches from them (`routing`). Under the local backend nothing here writes and `routing` reads the caches as they are.

A record is {run, role, generation, thread, name, state (started | retired | archived), started, at, archived, pane, kind, args, requests, relays}.
`requests` are the sends the master made to the thread, {id, seq, at, hash, state (pending | sent | failed)}; `relays` are its steer relays,
{id, seq, state (pending | done | stale)}. A request is published pending before the external send and settled after it, so a master that died
in between leaves a pending request that the next send of the same text reconciles by its id. A creation has no thread yet: its request sits in
`threads/launch-<request id>.json` until the thread's own record takes it over. `run` is the run identity in pair.json (`run_id`), never the slug
or an absolute path, so records an earlier run of the same name left on a branch are not imported. `seq` counts across the whole run: requests
and relays are ordered by it, never by the clock. Only the master writes records (`master_store`); a role's notify touches none.
"""

import fcntl
import json
import os
import uuid
from typing import Any

from .. import gitstore
from ..errors import PairError
from ..store import update

DIR = 'threads'
LAUNCH = 'launch-'
STARTED, RETIRED, ARCHIVED = 'started', 'retired', 'archived'
ROLES = ('sidekick', 'consultant', 'verifier')


def _amp():
    """mux/amp.py, which imports this module: its state paths and cache readers are needed only when a function runs."""
    from . import amp

    return amp


def master_store(store):
    """Whether this process writes control records for the store: the master of a git-backed store that has a pair.json."""
    return bool(
        store
        and os.path.isfile(f'{store}/pair.json')
        and gitstore.is_git(store)
        and gitstore.caller_is_master(store)
    )


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(f'{path}.tmp', path)


def _items(doc, key):
    items = doc.get(key)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def run_identity(store):
    """The run identity pair.json holds, '' when it holds none yet."""
    run = _read(f'{store}/pair.json').get('run_id')
    return run if isinstance(run, str) else ''


def ensure_run(store):
    """The run identity; the master gives a run that has none one at its first record (put publishes pair.json with that record)."""
    if not run_identity(store):
        made = uuid.uuid4().hex

        def add(doc):
            doc.setdefault('run_id', made)

        update(store, add)
    return run_identity(store)


def rel_of(thread):
    return f'{DIR}/{thread}.json'


def launch_rel(request):
    return f'{DIR}/{LAUNCH}{request}.json'


def records(store):
    """{store-relative path: record} of this run's control records on disk, in path order. A record of another run identity is left out."""
    run = run_identity(store)
    directory = f'{store}/{DIR}'
    found = {}
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        doc = _read(f'{directory}/{name}') if name.endswith('.json') else {}
        if run and doc.get('run') == run:
            found[f'{DIR}/{name}'] = doc
    return found


def next_seq(recs):
    """The sequence number after every request and relay of the run."""
    seqs = [
        e['seq']
        for d in recs.values()
        for e in (*_items(d, 'requests'), *_items(d, 'relays'))
        if isinstance(e.get('seq'), int)
    ]
    return max(seqs, default=0) + 1


def put(store, writes, drop=()):
    """Writes each {rel: record} and removes each path of drop, then publishes them as the master, with pair.json (which may hold the run identity
    just made): a record is on the branch before the command that made it goes on."""
    for rel, doc in writes.items():
        _write(f'{store}/{rel}', doc)
    for rel in drop:
        if os.path.exists(f'{store}/{rel}'):
            os.unlink(f'{store}/{rel}')
    gitstore.publish(
        store,
        [*writes, *drop, 'pair.json'],
        f'kitchen: master records {len(writes) + len(drop)} routing records',
        gitstore.writer_of(store, 'master'),
    )


def skeleton(store, run, name, thread):
    """A new record for `name` on `thread`: its role, the generation pair.json holds for it now and what the registry knows of its pane, kind and
    arguments, so a master that rebuilds its registry from the branch has them."""
    amp = _amp()
    role = amp.role_of(name)
    entry = amp.load(amp.registry_path())['agents'].get(name) or {}
    return {
        'run': run,
        'role': role,
        'generation': gitstore.generation_of(store, role) or 0,
        'thread': thread,
        'name': name,
        'state': STARTED,
        'pane': entry.get('pane') or '',
        'kind': entry.get('kind') or '',
        'args': entry.get('args') or [],
        'requests': [],
        'relays': [],
    }


def thread_record(store, name, thread):
    """(rel, the thread's record as it is on disk, or a new one)."""
    run = ensure_run(store)
    rel = rel_of(thread)
    doc = _read(f'{store}/{rel}')
    if doc.get('run') != run:
        doc = skeleton(store, run, name, thread)
    doc['requests'], doc['relays'] = _items(doc, 'requests'), _items(doc, 'relays')
    return rel, doc


def lifecycle(store, thread, entry):
    """The thread's record follows the lifecycle threads.json holds for it (started, retired, archived): published only when it differs."""
    rel, doc = thread_record(store, entry.get('name') or '', thread)
    want: dict[str, Any] = {
        'state': ARCHIVED
        if entry.get('archived')
        else RETIRED
        if entry.get('at')
        else STARTED
    }
    want.update({k: entry[k] for k in ('at', 'archived', 'role') if entry.get(k)})
    if entry.get('started') and not doc.get('started'):
        want['started'] = entry['started']
    if os.path.exists(f'{store}/{rel}') and all(
        doc.get(k) == v for k, v in want.items()
    ):
        return
    put(store, {rel: {**doc, **want}})


def mirror(thread, entry):
    """Called after threads.json took `entry` for thread: under the git store the master records it. A publication that fails stays committed
    here and goes out with the next one."""
    store = entry.get('store') or ''
    if not master_store(store):
        return
    try:
        lifecycle(store, thread, entry)
    except PairError:
        pass


def _cache(path, mutate):
    """Applies mutate(doc) to the JSON cache at path under its lock, as the amp state files are written."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = _amp().load_json(path)
        mutate(doc)
        _write(path, doc)


def _fill_threads(store, found):
    here = os.path.realpath(store)

    def merge(doc):
        for thread, rec in found.items():
            have = doc.get(thread) or {}
            merged = {
                **have,
                'name': rec.get('name') or have.get('name') or '',
                'role': rec.get('role') or have.get('role') or '',
                'store': have.get('store') or here,
            }
            for key in ('started', 'at', 'archived'):
                value = max(int(rec.get(key) or 0), int(have.get(key) or 0))
                if value:
                    merged[key] = value
            doc[thread] = merged

    _cache(_amp().threads_path(), merge)


def _fill_registry(store, found):
    """The current thread of each role of pair.json that the registry has no thread for: the newest started record of its name and generation."""
    amp = _amp()
    pair = _read(f'{store}/pair.json')
    want = {}
    for role in ROLES:
        member = pair.get(role) if isinstance(pair.get(role), dict) else {}
        if member.get('name'):
            want[member['name']] = member.get('generation')
    newest = {}
    for thread, rec in found.items():
        name = rec.get('name')
        started = int(rec.get('started') or 0)
        if (
            name in want
            and rec.get('state') == STARTED
            and want[name] in (None, rec.get('generation'))
            and started >= newest.get(name, ('', -1))[1]
        ):
            newest[name] = (thread, started)

    def add(doc):
        for name, (thread, _) in newest.items():
            if (doc['agents'].get(name) or {}).get('thread'):
                continue
            rec = found[thread]
            pane = rec.get('pane') or amp.thread_pane(thread)
            doc['agents'][name] = {
                'pane': pane,
                'kind': rec.get('kind') or 'amp',
                'args': rec.get('args') or [],
                'thread': thread,
            }
            if pane not in doc['panes']:
                doc['panes'].append(pane)

    if newest:
        amp.change(add, amp.registry_path())


def routing(store):
    """The routing caches of this orb ({threads, outbox}), rebuilt first from the store's control records when it is git-backed: a fresh clone with
    an empty state home gets the active role-to-thread mapping, the retired threads and what was sent to each, of this run identity, whatever its
    absolute store path. What this orb already holds is kept. Nothing is published, so a role may call it. Under the local backend the caches are
    returned as they are."""
    amp = _amp()
    if gitstore.is_git(store):
        found = {d['thread']: d for d in records(store).values() if d.get('thread')}
        if found:
            _fill_threads(store, found)
            _fill_registry(store, found)
    return {
        'threads': amp.load_json(amp.threads_path()),
        'outbox': amp.load_json(amp.outbox_path()),
    }
