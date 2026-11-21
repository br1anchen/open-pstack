"""Routing under the git store: which Amp thread plays which role of a run, and what was sent to it, is the master's, so the master records it on
the store branch as one control record per thread, `threads/<thread-id>.json`, and a master in a fresh clone with an empty state home rebuilds its
caches from them (`routing`). Under the local backend nothing here writes and `routing` reads the caches as they are.

A record is {run, role, generation, thread, name, state (started | retired | archived), started, at, archived, pane, kind, args, requests, relays}.
`requests` are the sends the master made to the thread, {id, seq, at, hash, state (pending | sent | failed)}; `relays` are its steer relays,
{id, seq, at, state (pending | done | stale)}. A request is published pending before the external send and settled after it, so a master that died
in between leaves a pending request that `reconcile` settles against the thread's own export (the message carries the request's id). A creation has no thread yet: its request sits in
`threads/launch-<request id>.json` until the thread's own record takes it over. `run` is the run identity in pair.json (`run_id`), never the slug
or an absolute path, so records an earlier run of the same name left on a branch are not imported. `seq` counts across the whole run: requests
and relays are ordered by it, never by the clock. Only the master writes records (`master_store`); a role's notify touches none.
"""

import fcntl
import json
import os
import re
import sys
import time
import uuid
from typing import Any

from .. import gitstore
from ..errors import PairError
from ..store import update

DIR = 'threads'
LAUNCH = 'launch-'
STARTED, RETIRED, ARCHIVED = 'started', 'retired', 'archived'
ORDER = {STARTED: 0, RETIRED: 1, ARCHIVED: 2}
PENDING, SENT, FAILED = 'pending', 'sent', 'failed'
ROLES = ('sidekick', 'consultant', 'verifier')
DRAINED = ('sidekick', 'consultant')
REQUEST = 'pstack-request:'
NO_DRAIN = 'no drain receipt'


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


def _num(rec, key):
    """rec[key] as a whole number, 0 when it is absent. A value on the branch that is not a number is skipped with a warning, not a crash: any
    clone can write the branch, and one bad field must not stop the master from reading the rest."""
    value = rec.get(key)
    if value in (None, '', False):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        who = rec.get('thread') or rec.get('id') or '?'
        print(
            f'routing: skipping non-numeric {key} {value!r} in the record of {who}',
            file=sys.stderr,
        )
        return 0


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


def skeleton(store, run, name, thread, current=True):
    """A new record for `name` on `thread`: its role, the generation pair.json holds for it now and what the registry knows of its pane, kind and
    arguments, so a master that rebuilds its registry from the branch has them. Those describe the thread the name holds now: a record made for a
    thread that is already retired (`current` false) takes none of them, for they would be its replacement's."""
    amp = _amp()
    role = amp.role_of(name)
    entry = (amp.load(amp.registry_path())['agents'].get(name) or {}) if current else {}
    return {
        'run': run,
        'role': role,
        'generation': (gitstore.generation_of(store, role) or 0) if current else 0,
        'thread': thread,
        'name': name,
        'state': STARTED,
        'pane': entry.get('pane') or '',
        'kind': entry.get('kind') or '',
        'args': entry.get('args') or [],
        'requests': [],
        'relays': [],
    }


def thread_record(store, name, thread, current=True):
    """(rel, the thread's record as it is on disk, or a new one)."""
    run = ensure_run(store)
    rel = rel_of(thread)
    doc = _read(f'{store}/{rel}')
    if doc.get('run') != run:
        doc = skeleton(store, run, name, thread, current)
    doc['requests'], doc['relays'] = _items(doc, 'requests'), _items(doc, 'relays')
    return rel, doc


def lifecycle(store, thread, entry):
    """The thread's record follows the lifecycle threads.json holds for it (started, retired, archived): published only when it differs. The state
    only moves forward, so a stale entry never takes a record back; a record first written once the thread is retired has no pane, kind, args or
    generation of its own to take from the registry."""
    state = (
        ARCHIVED if entry.get('archived') else RETIRED if entry.get('at') else STARTED
    )
    rel, doc = thread_record(
        store, entry.get('name') or '', thread, current=state == STARTED
    )
    want: dict[str, Any] = {
        'state': max(
            (state, doc.get('state') or STARTED), key=lambda s: ORDER.get(s, 0)
        )
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


def mirror_relay(record):
    """Called after a relay record was written locally: its id and state go into the thread's record. The relay's bundle and message stay on the
    orb that made them; a relay another orb made is only ever read here as pending, never delivered."""
    store = record.get('store') or ''
    thread = record.get('thread') or ''
    if not (thread and master_store(store)):
        return
    state = str(record.get('state') or '').removeprefix('relay-')
    try:
        rel, doc = thread_record(store, record.get('name') or '', thread)
        known = next((r for r in doc['relays'] if r.get('id') == record['id']), None)
        if known is None:
            known = {
                'id': record['id'],
                'seq': next_seq(records(store)),
                'at': int(record.get('at') or 0),
            }
            doc['relays'].append(known)
        elif known.get('state') == state:
            return
        known['state'] = state
        put(store, {rel: doc})
    except PairError:
        pass


def _pending(recs, thread, name, key):
    """The pending request for this text on `thread` (a creation: on a launch record of `name`), or None."""
    for rel, doc in recs.items():
        mine = (
            doc.get('thread') == thread
            if thread
            else rel.startswith(f'{DIR}/{LAUNCH}') and doc.get('name') == name
        )
        for request in _items(doc, 'requests') if mine else []:
            if request.get('state') == PENDING and request.get('hash') == key:
                return request
    return None


def intend(store, name, thread, digest):
    """(request id, resumed) of a send of the text with this digest to `thread` ('' for a creation). The master publishes the request pending
    before the external send, so a send whose result was never recorded is found again: the same text sent again takes that request's id and
    `resumed` is true (the caller asks the thread whether it holds it). A role's send, or none under the local backend, only gets an id."""
    if not master_store(store):
        return uuid.uuid4().hex[:12], False
    recs = records(store)
    found = _pending(recs, thread, name, digest)
    if found:
        return found['id'], True
    request = {
        'id': uuid.uuid4().hex[:12],
        'seq': next_seq(recs),
        'at': int(time.time()),
        'hash': digest,
        'state': PENDING,
    }
    if thread:
        rel, doc = thread_record(store, name, thread)
    else:
        rel, doc = (
            launch_rel(request['id']),
            skeleton(store, ensure_run(store), name, ''),
        )
    doc['requests'].append(request)
    put(store, {rel: doc})
    return request['id'], False


def settle(store, name, thread, request, state, created):
    """The result of a request: in the thread's record, made now when the send started the thread (the launch record of a creation is removed
    then), else in the launch record. Nothing when the master did not make the request."""
    if not master_store(store):
        return
    try:
        recs = records(store)
        launch = next(
            (
                rel
                for rel, d in recs.items()
                if rel.startswith(f'{DIR}/{LAUNCH}')
                and any(r.get('id') == request for r in _items(d, 'requests'))
            ),
            '',
        )
        if not thread:
            doc = recs.get(launch)
            if doc:
                _items(doc, 'requests')[0]['state'] = state
                put(store, {launch: doc})
            return
        rel, doc = thread_record(store, name, thread)
        if created and not doc.get('started'):
            doc['started'] = int(time.time())
        mine = next((r for r in doc['requests'] if r.get('id') == request), None)
        if mine is None:
            mine = next(
                (
                    r
                    for r in _items(recs.get(launch) or {}, 'requests')
                    if r.get('id') == request
                ),
                {'id': request, 'seq': next_seq(recs)},
            )
            doc['requests'].append(mine)
        mine['state'] = state
        put(store, {rel: doc}, [launch] if launch else [])
    except PairError:
        pass


def pending_work(store, thread):
    """Why the store branch says `thread` has work the master sent that is not settled ('' when it says none): a request still pending, or a relay."""
    if not gitstore.is_git(store):
        return ''
    doc = records(store).get(rel_of(thread)) or {}
    if any(r.get('state') == PENDING for r in _items(doc, 'requests')):
        return 'a message to it is still pending (its request on the store branch is not settled)'
    if any(r.get('state') == PENDING for r in _items(doc, 'relays')):
        return 'a relay to it is still pending (on the store branch)'
    return ''


def reconcile(store, thread, exported):
    """Settles what the store branch holds as pending for `thread` against the thread itself (`exported`, its `amp threads export`): a request
    whose `pstack-msg:` id the thread holds is sent; one it does not hold, when the thread has ended a turn since the request was made, never
    arrived and is failed; a relay the thread has ended a turn since is stale, as under the local backend. A thread still in a turn, or one the
    export could not read, settles nothing. Nothing is written unless the master made the records."""
    if not (master_store(store) and exported):
        return
    amp = _amp()
    doc = records(store).get(rel_of(thread))
    if not doc:
        return
    state, since = amp.thread_state(exported)
    ended = state in amp.SETTLED
    changed = False
    for request in _items(doc, 'requests'):
        if request.get('state') != PENDING:
            continue
        if amp.holds(exported, str(request.get('id'))):
            request['state'] = SENT
            if _num(amp.load_json(amp.outbox_path()).get(thread) or {}, 'at') < _num(
                request, 'at'
            ):
                amp.record_sent(
                    thread,
                    {
                        'hash': request.get('hash') or '',
                        'id': request['id'],
                        'at': _num(request, 'at'),
                    },
                )
        elif ended and since >= _num(request, 'at'):
            request['state'] = FAILED
        else:
            continue
        changed = True
    for relay in _items(doc, 'relays'):
        if (
            relay.get('state') == PENDING
            and isinstance(relay.get('at'), int)
            and ended
            and since >= relay['at']
        ):
            relay['state'] = 'stale'
            changed = True
    if changed:
        try:
            put(store, {rel_of(thread): doc})
        except PairError:
            pass


def request_line(store, request):
    """The line a master's message carries beside its `pstack-msg:` id, so the role can name the request it answers in its drain receipt. A send
    that is not a record of the master's (a role's, or any under the local backend) has none."""
    return f'\n{REQUEST} {request}' if master_store(store) else ''


def held_requests(thread):
    """The ids of the requests the master sent the thread, oldest first, read from the thread itself: what a role drains."""
    found = []
    for message in _amp().export(thread).get('messages') or []:
        if isinstance(message, dict) and message.get('role') == 'user':
            for request in re.findall(f'{REQUEST} ([0-9a-f]+)', json.dumps(message)):
                if request not in found:
                    found.append(request)
    return found


def drain(store, role, file, extra, code):
    """The drain receipts (A7) that go out in the terminal publication of a sidekick's or consultant's report or advice (`file`, with the `extra`
    store-relative paths published with it and the `code` heads already published): one for each request its thread holds. The store-relative
    paths written. None when the thread is not the one the master's record names for this generation, which never writes a receipt as another
    generation, or when the thread cannot be read."""
    thread = _amp().own_thread()
    if not (thread and role in DRAINED):
        return []
    writer = gitstore.writer_of(store, role)
    held = _read(f'{store}/{rel_of(thread)}')
    if held and _num(held, 'generation') != writer.generation:
        print(
            f'drain: generation {_num(held, "generation")} wrote nothing as generation {writer.generation}',
            file=sys.stderr,
        )
        return []
    return gitstore.write_drains(
        store,
        writer,
        run_identity(store),
        thread,
        held_requests(thread),
        [os.path.relpath(file, store), *extra],
        code,
    )


def kept_by_branch(store, thread, role, tip=None):
    """Why the store branch keeps `thread` ('' when it says nothing against archiving it): work the master sent that is unsettled, and, for a
    sidekick or consultant, a request with no drain receipt. `tip` is a callable giving the fetched tip of origin's store branch, which a pass
    over many threads shares so origin is fetched once."""
    return pending_work(store, thread) or undrained(store, thread, role, tip)


def before_migration(store, thread):
    """Whether `thread` began before the store moved to git (pair.json `.store_backend.migrated_at`): the master's requests to it were never on the
    branch, so a missing receipt says nothing and the local-store checks decide. A thread with no recorded start or retirement time does not count."""
    migrated = gitstore.backend_record(store).get('migrated_at')
    entry = _amp().load_json(_amp().threads_path()).get(thread) or {}
    began = _num(entry, 'started') or _num(entry, 'at')
    return isinstance(migrated, int) and 0 < began < migrated


def undrained(store, thread, role, tip=None):
    """Why a git-store sidekick or consultant thread is kept for want of its drain receipts: every request of its record (pending or sent) needs a
    receipt of its own generation that is on origin's store branch as fetched now. A request newer than the last receipt keeps it; so does
    a fetch that fails (freshness unknown) keeps it too."""
    if not (gitstore.is_git(store) and role in DRAINED):
        return ''
    record = records(store).get(rel_of(thread)) or {}
    requests = [
        str(r.get('id'))
        for r in _items(record, 'requests')
        if r.get('state') in (PENDING, SENT)
    ]
    if not requests:
        if before_migration(store, thread):
            return ''
        return f'{NO_DRAIN} (the store branch records no request to it)'
    tip = (tip or (lambda: gitstore.fetch_tip(store)))()
    if not tip:
        return "freshness unknown (origin's store branch could not be fetched)"
    for request in requests:
        if not gitstore.drained(store, tip, record, request):
            return f'{NO_DRAIN} for request {request}'
    return ''


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
                value = max(_num(rec, key), _num(have, key))
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
        started = _num(rec, 'started')
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


def _fill_outbox(found):
    """What was last sent to each thread that this orb's outbox lacks, from the settled requests: the same text is not sent to a thread in a turn."""
    amp = _amp()
    have = amp.load_json(amp.outbox_path())
    for thread, rec in found.items():
        sent = [
            r
            for r in _items(rec, 'requests')
            if r.get('state') == SENT and r.get('hash')
        ]
        if sent and thread not in have:
            last = max(sent, key=lambda r: _num(r, 'seq'))
            amp.record_sent(
                thread,
                {
                    'hash': last['hash'],
                    'id': last['id'],
                    'at': _num(last, 'at'),
                },
            )


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
            _fill_outbox(found)
    return {
        'threads': amp.load_json(amp.threads_path()),
        'outbox': amp.load_json(amp.outbox_path()),
    }
