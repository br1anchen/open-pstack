"""Amp threads as the roles: every role is an Amp thread of its own, on the model its roster entry names, each in an orb of its own. A pane is
a name in pstack's registry, as with subagents; a role's thread starts with the first prompt it is sent (`amp -ox`), and every later prompt
continues it (`amp threads continue -ox`). Each orb has its own checkout and its own copy of the store, so every prompt carries a bundle
(pstack_core.bundle: the store's files and the commits the receiver may lack) as an attachment, and the receiver's first move is
`PSTACK_MUX=amp pair.sh sync <attachment>`. Nothing waits in this process: a role's reply is a prompt to the master's thread, carrying the
role's bundle the same way, and the master acts on it in its next turn."""

import calendar
import contextlib
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from .. import bundle, git
from ..argv import Spec, read
from ..numbers import env_int
from ..store import TEXT
from . import subagents
from .base import DEVNULL, PIPE, Agent, Reply, ThreadRow
from .subagents import EXITS, NOT_FOUND, Subagents, change, load

THREAD_RE = re.compile(
    r'T-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
)
# The command a receiving thread runs first. sync is a core command, so pstack-pair's entry serves every variant.
SYNC = 'PSTACK_MUX=amp ~/.agents/skills/pstack-pair/scripts/pair.sh sync'
# A role's orb holds a copy of the store: what it writes for the master travels only with a message to the master's thread.
RETURN = (
    ' This orb holds a copy of the store, so a file you write for the master (the ready file, a report, a plan or steer response, advice, '
    'a verdict) reaches it only when you send it: `PSTACK_MUX=amp kitchen.sh finish` or `PSTACK_MUX=amp kitchen.sh notify <store> <file>` '
    'in a kitchen, `pair.sh` in a pair or trio (the prefix wins over a PSTACK_MUX the orb sets). Run one for each such file before you end '
    'the turn.'
)
MASTER_RE = re.compile(r'-master(-g[0-9]+)?$')
# The skills tree this pstack runs from, and the installer beside it (an open-pstack checkout or clone has both).
SKILLS = os.path.realpath(os.path.join(os.path.dirname(__file__), *['..'] * 4))
INSTALLER = os.path.join(os.path.dirname(SKILLS), 'scripts', 'pstack-sync.py')
# The flags of a role's arguments that choose the thread's model and its orb's size; the rest belong to the pane agents.
MODEL_SPEC = Spec(
    valued=(
        '--model',
        '-m',
        '--mode',
        '--effort',
        '--features',
        '--settings-file',
        '--orb-size',
    ),
    flags=('--fast',),
    unknown='collect',
    dangling='drop',
)


def state_dir():
    state = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(state, 'pstack', 'amp')


def registry_path():
    return os.path.join(state_dir(), 'registry.json')


def sent_path(thread):
    """The last bundle this side sent to thread: the base its reply is merged against."""
    return os.path.join(state_dir(), 'sent', f'{bundle.tag_of(thread)}.tgz')


def outbox_path():
    """What this side sent to each thread: {thread: {hash, id, at}}, so a repeated send of the same text is not delivered twice."""
    return os.path.join(state_dir(), 'outbox.json')


def relays_dir():
    """The steers handed to the master to send: relays/<id>.json is each record, relays/<id>.md the message the master sends."""
    return os.path.join(state_dir(), 'relays')


# The most upload_thread_file takes.
RELAY_LIMIT = 4 * 1024 * 1024
PENDING, DONE, STALE = 'relay-pending', 'relay-done', 'stale'


def relay_records(store=None):
    """The relay records (of one store), newest first."""
    found = []
    directory = relays_dir()
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        doc = load_json(os.path.join(directory, name)) if name.endswith('.json') else {}
        if doc.get('id') and (store is None or doc.get('store') == store):
            found.append(doc)
    return sorted(found, key=lambda doc: int(doc.get('at') or 0), reverse=True)


@contextlib.contextmanager
def relay_lock():
    """The lock the relay records are read and written under, beside them, as the registry and the outbox have theirs."""
    os.makedirs(relays_dir(), exist_ok=True)
    with open(os.path.join(relays_dir(), 'lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_relay(record):
    path = os.path.join(relays_dir(), f'{record["id"]}.json')
    with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=1, sort_keys=True)
    os.replace(f'{path}.tmp', path)


def thread_context(thread):
    """`amp threads context` of the thread as an object, None when amp cannot say."""
    r = run(['amp', 'threads', 'context', thread])
    try:
        doc = json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else {}


def thread_compactions(thread):
    """How many times the thread compacted, None when amp cannot say."""
    doc = thread_context(thread) if thread else None
    count = doc.get('compactionCount') if doc else None
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def delivered_path():
    """What this side already gave each thread: {thread: {build, compactions}}, so a later message carries only what changed."""
    return os.path.join(state_dir(), 'delivered.json')


def record_delivered(thread, update):
    """delivered.json's entry for thread, with `update` merged into it, under a lock beside it."""
    path = delivered_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(path)
        doc[thread] = {**(doc.get(thread) or {}), **update}
        with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(f'{path}.tmp', path)


SKILL_RE = re.compile(r'^(pstack-[a-z-]+) [A-Z]+\b')


def reorientation(name, text, store):
    """What a role that compacted since its last message reads first: its role's skill file and the store, which its summary may have lost.
    '' when the message names no skill (it is no message to a role)."""
    found = SKILL_RE.match(text)
    if not found or MASTER_RE.search(name):
        return ''
    skill, role = found.group(1), name.rsplit('-', 1)[-1]
    held = f', the store {store} (standing-orders.md, pair.json),' if store else ''
    return (
        f'You compacted since the last message. Re-read ~/.agents/skills/{skill}/SKILL.md (your role: {role}){held} '
        'and the file this message names before you act.\n\n'
    )


# What a message to a thread that already has the workflow setup says about sending files back, in place of RETURN.
RETURN_SHORT = ' Send files for the master back with PSTACK_MUX=amp kitchen.sh finish or notify (pair.sh in a pair or trio).'

# The error the CLI prints when a bundle's upload breaks off; the message may or may not have gone out.
UPLOAD_FAILED = 'Attachment upload did not complete'
# How a message names itself, so a retry can tell it already reached the thread.
MSG_ID = 'pstack-msg:'
# lastKnownAgentState values of a thread whose turn has ended.
SETTLED = ('idle', 'error', 'cancelled')


def iso_epoch(text):
    """The epoch of an ISO-8601 UTC time such as 2026-11-17T22:08:04.497Z, or 0."""
    try:
        return calendar.timegm(time.strptime(text[:19], '%Y-%m-%dT%H:%M:%S'))
    except (TypeError, ValueError):
        return 0


def export(thread):
    """The thread as `amp threads export` prints it, {} when amp cannot say."""
    r = run(['amp', 'threads', 'export', thread])
    try:
        doc = json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def thread_state(doc):
    """(state, since) of an exported thread: its lastKnownAgentState and the epoch it took it; ('', 0) when the export has none."""
    meta = doc.get('meta') if isinstance(doc.get('meta'), dict) else {}
    known = meta.get('lastKnownAgentState')
    if not isinstance(known, dict) or not isinstance(known.get('state'), str):
        return '', 0
    return known['state'], iso_epoch(known.get('updatedAt') or '')


def holds(doc, msg_id):
    """Whether the exported thread holds a message naming msg_id."""
    return f'{MSG_ID} {msg_id}' in json.dumps(doc.get('messages') or [])


def file_digest(path):
    """The first twelve hex digits of the sha256 of the file's bytes, '' when it cannot be read: what notify quotes of the file it sends."""
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return ''


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def record_sent(thread, entry):
    """outbox.json's entry for thread, under a lock beside it."""
    path = outbox_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(path)
        doc[thread] = entry
        with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(f'{path}.tmp', path)


def threads_path():
    """What this side knows of each role thread it retired or archived: {thread: {name, role, at, archived}}, so a thread that left the registry is
    still listed and archived once that is safe."""
    return os.path.join(state_dir(), 'threads.json')


def record_thread(thread, update):
    """threads.json's entry for thread, with `update` merged into it, under a lock beside it."""
    path = threads_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(path)
        doc[thread] = {**(doc.get(thread) or {}), **update}
        with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(f'{path}.tmp', path)


def role_of(name):
    """The role a registry name stands for: master (any generation), else its last word (sidekick, verifier, consultant)."""
    return 'master' if MASTER_RE.search(name) else name.rsplit('-', 1)[-1]


def message_texts(message):
    """The text of a thread message as `amp threads export` holds it: a string, or the text of each block."""
    content = message.get('content')
    if isinstance(content, str):
        return content
    return '\n'.join(
        b.get('text') or ''
        for b in content or []
        if isinstance(b, dict) and isinstance(b.get('text'), str)
    )


ANNOUNCE_RE = re.compile(
    r'^pstack-[a-z-]+ (?:REPORT|ADVICE) (/\S+)\n(?:[^\n]+\n)*?sha256: ([0-9a-f]{12})',
    re.M,
)
# The directory of a store a role's files go to, by role.
ROLE_DIRS = {'sidekick': 'reports', 'verifier': 'verdicts', 'consultant': 'advice'}


def unsynced_files(role):
    """The files of `role` the master's thread was sent whose latest announcement quotes a digest this orb's copy does not hold (a file still
    to sync, or one rewritten since)."""
    latest = {}
    for message in export(own_thread()).get('messages') or []:
        if message.get('role') == 'user':
            for path, digest in ANNOUNCE_RE.findall(message_texts(message)):
                latest[path] = digest
    return [
        path
        for path, digest in latest.items()
        if os.path.basename(os.path.dirname(path)) == ROLE_DIRS.get(role)
        and file_digest(path) != digest
    ]


def head_missing(store):
    """The head: of the newest sidekick report when the master's checkout lacks that commit (the work is only in the sidekick's orb), else ''."""
    reports = glob.glob(
        os.path.join(glob.escape(store), 'reports', '[0-9][0-9][0-9]-*.md')
    )
    repo = bundle.repo_of(store)
    if not reports or not repo:
        return ''
    found = re.search(
        r'^head:\s*([0-9a-f]{7,40})\s*$',
        open(max(reports, key=os.path.getmtime), encoding='utf-8').read(),
        re.M,
    )
    head = found.group(1) if found else ''
    return (
        head
        if head and not git.ok(repo, 'cat-file', '-e', f'{head}^{{commit}}')
        else ''
    )


def live_threads(doc):
    """{thread: role name} of the threads pair.json's roles run in now, through the registry and the master's pane."""
    agents = load(registry_path())['agents']
    live = {}
    for role in ('sidekick', 'consultant', 'verifier', 'master'):
        entry = doc.get(role) if isinstance(doc.get(role), dict) else {}
        name = entry.get('name') or ''
        thread = (agents.get(name) or {}).get('thread') or ''
        if role == 'master' and not thread:
            pane = entry.get('pane_id') or ''
            thread = pane[len('amp:') :] if pane.startswith('amp:T-') else ''
        if thread:
            live[thread] = name
    return live


def assess(thread, store, role):
    """(the thread's state, why it must not be archived yet): '' when the store no longer uses it and nothing it did is unread. A thread in a turn,
    one with a message or steer relay still pending to it, one whose file the master's store lacks, one holding commits the master lacks, one
    whose replacement has not sent READY and an old master whose hand-over is open each stay."""
    doc = load_json(os.path.join(store, 'pair.json'))
    live = live_threads(doc)
    if thread == own_thread() or thread in live:
        return 'in use', f'in use by {live.get(thread) or "this master"}'
    state, since = thread_state(export(thread))
    if state == '':
        return 'unknown', 'state unknown (export failed or gave none)'
    if state not in SETTLED:
        return state, f'working ({state}): it is in a turn'
    sent = load_json(outbox_path()).get(thread)
    if isinstance(sent, dict) and not since >= int(sent.get('at') or 0):
        return state, 'a message to it is still pending (its turn has not ended since)'
    for record in relay_records(store):
        if (
            record.get('thread') == thread
            and record.get('state') == PENDING
            and not (state in SETTLED and since >= int(record.get('at') or 0))
        ):
            return state, 'a steer relay to it is still pending'
    files = unsynced_files(role)
    if files:
        return state, f'it sent {files[0]}, which the master store does not hold yet'
    if role == 'sidekick' and (head := head_missing(store)):
        return state, f'its report head {head[:9]} is not in the master checkout'
    role_doc = doc.get(role) if isinstance(doc.get(role), dict) else {}
    ready = 'advice' if role == 'consultant' else 'reports'
    if (
        role in ('sidekick', 'consultant')
        and role_doc.get('bootstrap_pending') is True
        and not os.path.isfile(os.path.join(store, ready, '000-ready.md'))
    ):
        return state, f'the new {role} has not sent READY yet'
    if role == 'master' and (doc.get('master') or {}).get('previous'):
        return state, 'take-master has not run yet'
    return state, ''


def archivable(thread, store, role=''):
    """'' when the thread may be archived, else why not (assess)."""
    return assess(thread, store, role)[1]


def own_thread():
    return (
        os.environ.get('AMP_THREAD_ID') or os.environ.get('AMP_CURRENT_THREAD_ID') or ''
    )


def thread_pane(thread):
    """The pane of a thread pstack did not start (the master's, at init): its thread id."""
    return f'amp:{thread}'


def model_args(kind, args):
    """(the amp flags that choose the role's model, a warning or ''): a role of kind amp keeps its model flags; any other kind runs on Amp's
    default mode, since its arguments are another program's."""
    if kind != 'amp':
        return (
            [],
            f'amp: kind {kind} runs as an Amp thread on the default mode; set kind = "amp" with --model to choose its model',
        )
    parsed = read(args, MODEL_SPEC)
    return [t for pair in parsed.occurrences for t in pair if t is not None], ''


def store_in(text):
    """The store a prompt is about: the nearest directory holding pair.json above an absolute path the text names, '' when none."""
    for token in re.findall(r'/[^\s`\'"<>(),;]+', text):
        path = token.rstrip('.:')
        while path not in ('', '/'):
            if os.path.isfile(os.path.join(path, 'pair.json')):
                return path
            path = os.path.dirname(path)
    return ''


def build_files():
    """(relative path, absolute path) of every file of this pstack build: the skills tree and its installer, without caches."""
    files = [('scripts/pstack-sync.py', INSTALLER)]
    for top, dirs, names in os.walk(SKILLS):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(names):
            path = os.path.join(top, name)
            files.append((os.path.join('skills', os.path.relpath(path, SKILLS)), path))
    return files


def pack_build(path):
    """Writes this pstack build to path as a .tgz; its id (a digest of its files), or '' when this pstack is no full checkout (no installer
    beside its skills), so a role installs nothing and runs the build its orb has."""
    if not os.path.isfile(INSTALLER):
        return ''
    digest = hashlib.sha256()
    files = build_files()
    for rel, full in files:
        digest.update(rel.encode() + b'\0')
        if not os.path.islink(full):
            with open(full, 'rb') as f:
                digest.update(f.read())
    with tarfile.open(path, 'w:gz') as tar:
        for rel, full in files:
            tar.add(full, rel, recursive=False)
    return digest.hexdigest()[:16]


def install_line(build):
    """The shell line that installs the build attached as pstack-build.tgz, once per orb: unpacked under ~/.local/share/pstack-builds/<id>
    and linked into the agents' skill roots by its own installer."""
    d = f'~/.local/share/pstack-builds/{build}'
    return (
        f'd={d}; [ -f $d/scripts/pstack-sync.py ] || {{ mkdir -p $d && amp files get <the pstack-build.tgz attachment URL below> '
        f'-o $d/build.tgz && tar -xzf $d/build.tgz -C $d; }} && python3 $d/scripts/pstack-sync.py --source $d/skills --apply'
    )


def run(argv, cwd=None):
    sys.stdout.flush()
    return subprocess.run(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        **TEXT,
    )


class Amp(Subagents):
    program = 'amp'
    label = 'Amp threads'
    failure = 'amp_error'
    pane_var = 'AMP_THREAD_ID'
    inside = 'an Amp thread (AMP_THREAD_ID)'
    tracks_sessions = False
    starts_master = False
    # A new master starts as a thread of its own (start_master); the human starts nothing.
    master_threads = True
    waits = False
    handoff_next = "next: end your turn; the role's thread writes the ready file, and its reply arrives here as a message that names the sync to run first\n"

    # A new role thread costs minutes (orb start, setup, the build install, sync, the joo build), so a sidekick keeps its thread across
    # routine units in one area until its context fills.
    reuses_sessions = True
    # `amp threads continue -ox` queues a message behind a running turn; the CLI has no way to steer one.
    steers_running = False
    # A steer for a thread in a turn is handed to the master to send with its own tools (relay).
    relays = True
    remote_roles = True

    def path(self):
        return registry_path()

    def context_used(self, name):
        found = self.entry(name)
        thread = found[1].get('thread') if found else ''
        if not thread:
            return None
        doc = thread_context(thread)
        if doc is None:
            return None
        used, limit = doc.get('totalTokens'), doc.get('maxContextTokens')
        compacted = doc.get('compactionCount') or 0
        if not (isinstance(used, int) and isinstance(limit, int) and limit > 0):
            return None
        return 1.0 if compacted else used / limit

    def mode_reads_skill_file(self, mode):
        return mode == 'claude-code'

    def reads_skill_file(self, name):
        found = self.entry(name)
        args = (found[1].get('args') or []) if found else []
        return any(
            flag in ('--mode', '-m') and self.mode_reads_skill_file(mode)
            for flag, mode in zip(args, args[1:], strict=False)
        )

    def compactions(self, name):
        return thread_compactions(self.role_thread(name))

    def role_thread(self, name):
        found = self.entry(name)
        return (found[1].get('thread') or '') if found else ''

    def own_thread(self):
        return own_thread()

    def sent_to_master(self, skill, path, since=0):
        announce = f'{skill} REPORT {path}\\n'
        latest = None
        for message in export(own_thread()).get('messages') or []:
            text = json.dumps(message.get('content'))
            if message.get('role') != 'user' or announce not in text:
                continue
            made = message.get('createdAt') or ''
            if made and iso_epoch(made) < since:
                continue
            latest = (made, text)
        if latest is None:
            return None
        made, text = latest
        quoted = re.search(r'sha256: ([0-9a-f]{12})', text.split(announce, 1)[1])
        if quoted and file_digest(path) == quoted.group(1):
            return None
        return made or 'an unknown time'

    def thread_pane(self, thread):
        return thread_pane(thread)

    def thread_of_pane(self, pane):
        return pane[len('amp:') :] if pane.startswith('amp:T-') else ''

    def thread_cost(self, thread):
        r = run(['amp', 'threads', 'usage', thread])
        m = re.search(r'^Cost: \$([0-9]+(?:\.[0-9]+)?)', r.stdout, re.M)
        return float(m.group(1)) if r.returncode == 0 and m else None

    def require(self, what=None):
        if not own_thread():
            from ..errors import NotInMux

            raise NotInMux(
                what or 'not running inside an Amp thread (AMP_THREAD_ID is unset)'
            )

    def own_pane(self):
        thread = own_thread()
        if not thread:
            return ''
        found = next(
            (
                e['pane']
                for e in load(self.path())['agents'].values()
                if e.get('thread') == thread
            ),
            '',
        )
        return found or thread_pane(thread)

    def pane_env(self, pane, env):
        return [f'PSTACK_AMP_PANE={pane}']

    def pane_exists(self, pane):
        return pane.startswith('amp:T-') or pane in load(self.path())['panes']

    def new(self):
        def add(doc):
            pane = f'amp:{doc["next"]}'
            doc['next'] += 1
            doc['panes'].append(pane)
            return pane

        return change(add, self.path())

    def agent(self, name):
        found = self.entry(name)
        if found is None:
            return None
        known, e = found
        thread = e.get('thread') or ''
        # A thread whose turn runs is working: a message sent now waits behind that turn.
        state = thread_state(export(thread))[0] if thread else ''
        return Agent(
            known,
            e.get('kind') or '',
            'idle' if state in ('', *SETTLED) else 'working',
            e['pane'],
            '',
            {'kind': 'id', 'value': thread} if thread else None,
        )

    def end(self, name):
        """The role's thread is done: its name leaves the registry, so its next prompt starts a fresh thread, and the thread is retired (as every
        registry write that drops a thread retires it, retire_lost): it is archived by a later sweep, once archivable says it is safe."""
        found = self.entry(name)
        if found is not None:
            change(lambda doc: doc['agents'].pop(found[0], None), self.path())

    def remember_store(self, name, store):
        """The registry's store is the last one sent to, and the role's entry remembers the run it works for, so that retiring its thread later is
        scoped to that store."""

        def record(doc):
            doc['store'] = store
            if name in doc['agents']:
                doc['agents'][name]['store'] = store

        change(record, self.path())

    def retire(self, thread, name, store=''):
        if thread and thread != own_thread():
            found = self.entry(name) or (name, {})
            store = store or found[1].get('store') or load(self.path()).get('store')
            record_thread(
                thread,
                {
                    'name': name,
                    'role': role_of(name),
                    'at': int(time.time()),
                    'store': os.path.realpath(store) if store else '',
                },
            )

    def thread_rows(self, store, retired_only=False):
        doc = load_json(os.path.join(store, 'pair.json'))
        master = doc.get('master') if isinstance(doc.get('master'), dict) else {}
        pane = master.get('pane_id') or ''
        agents = load(self.path())['agents']
        mine = (agents.get(master.get('name') or '') or {}).get('thread') or (
            pane[len('amp:') :] if pane.startswith('amp:T-') else ''
        )
        if not own_thread() or own_thread() != mine:
            return []
        here = os.path.realpath(store)
        known = load_json(threads_path())
        found, unscoped = {}, []
        for t, e in known.items():
            if e.get('archived'):
                continue
            if not e.get('store'):
                unscoped.append(
                    ThreadRow(
                        t,
                        e.get('role') or role_of(e.get('name') or ''),
                        '',
                        'no store recorded',
                    )
                )
            elif os.path.realpath(e['store']) == here:
                found[t] = e.get('role') or role_of(e.get('name') or '')
        if not retired_only:
            for e in (doc.get('sidekick_threads') or {}).values():
                found.update({t: 'sidekick' for t in e if isinstance(t, str)})
            for e in master.get('sessions') or []:
                if isinstance(e, dict) and (e.get('session') or '').startswith('T-'):
                    found.setdefault(e['session'], 'master')
            ours = re.compile(
                re.escape(doc.get('slug') or '\0')
                + r'-(?:sidekick|verifier|consultant|master(?:-g[0-9]+)?)'
            )
            for name, e in agents.items():
                if e.get('thread') and ours.fullmatch(name):
                    found.setdefault(e['thread'], role_of(name))
        rows = [
            ThreadRow(thread, role, *assess(thread, store, role))
            for thread, role in found.items()
            if not (known.get(thread) or {}).get('archived')
        ]
        return rows + ([] if retired_only else unscoped)

    def archive(self, row):
        if run(['amp', 'threads', 'archive', row.thread]).returncode:
            return False
        record_thread(row.thread, {'role': row.role, 'archived': int(time.time())})
        return True

    def rename(self, target, name):
        """Names the agent in the pane (or of the name) `target`, keeping its thread and model flags; a pane named by its thread (the
        master's at init) records that thread."""
        found = self.entry(target)
        pane = found[1]['pane'] if found else target
        if not self.pane_exists(pane):
            return False
        thread = self.thread_of_pane(pane)

        def holds(e):
            return e['pane'] == pane or bool(thread and e.get('thread') == thread)

        def move(doc):
            old = next((e for e in doc['agents'].values() if holds(e)), {})
            for gone in [n for n, e in doc['agents'].items() if holds(e) or n == name]:
                del doc['agents'][gone]
            doc['agents'][name] = {
                'pane': pane,
                'kind': old.get('kind') or 'amp',
                'args': old.get('args') or [],
                'thread': thread or old.get('thread') or '',
            }

        change(move, self.path())
        return True

    def start(
        self, name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE
    ):
        """Names the role and keeps its model flags; its thread starts with the first prompt it is sent."""
        flags, warning = model_args(kind, args)

        def claim(doc):
            for old in [
                n for n, e in doc['agents'].items() if e['pane'] == pane or n == name
            ]:
                del doc['agents'][old]
            doc['agents'][name] = {
                'pane': pane,
                'kind': kind,
                'args': flags,
                'thread': '',
            }

        change(claim, self.path())
        if warning:
            sys.stderr.write(warning + '\n')
        return Reply(0, '' if stdout == PIPE else None, '' if stderr == PIPE else None)

    def sender(self):
        """{name, thread} of this side, as a bundle names its sender."""
        thread = own_thread()
        name = next(
            (
                n
                for n, e in load(self.path())['agents'].items()
                if e.get('thread') == thread
            ),
            '',
        )
        return {'name': name, 'thread': thread}

    def agents(self):
        """{name: {pane, kind, thread}} this side knows, as a bundle carries them."""
        return {
            n: {
                'pane': e['pane'],
                'kind': e.get('kind') or '',
                'thread': e.get('thread') or '',
            }
            for n, e in load(self.path())['agents'].items()
        }

    def with_bundle(self, name, text, store, thread, seen, full_rules, tmp):
        """(attachments, message, build) of a message to `name`: the store's bundle always, and the pstack build with the line that installs it
        when `seen` (what the thread was already given) lacks this build, as the message text after `text`. build is the id to record, '' when
        none was attached; full_rules gives the send-back rules in full."""
        path = os.path.join(tmp, f'pstack-{name}.tgz')
        pane = (self.entry(name) or (name, {'pane': ''}))[1]['pane']
        bundle.pack(
            store,
            path,
            self.sender(),
            {'name': name, 'pane': pane},
            self.agents(),
            text,
        )
        attach = ['--attach', path]
        build_path = os.path.join(tmp, 'pstack-build.tgz')
        # The master's build is the run's: a reply to its thread carries none, a new master's thread gets this one.
        build = '' if thread and MASTER_RE.search(name) else pack_build(build_path)
        install = ''
        if build and (not seen or seen.get('build') != build):
            attach += ['--attach', build_path]
            install = f'`{install_line(build)}` (it installs the pstack build the sender runs, the same on every orb), then '
        else:
            build = ''
        rules = RETURN if full_rules else RETURN_SHORT
        message = (
            f'{text}\n\npstack: before you act on this message, run {install}`{SYNC} <the pstack-{name}.tgz attachment URL below>`: '
            f'it merges the {store} store and its commits into your orb.'
            + ('' if MASTER_RE.search(name) else rules)
        )
        return attach, message, build

    def send(self, name, text, thread='', flags=(), store='', title=''):
        """Sends text to thread (a new thread on flags when ''), with the bundle of store attached. (thread, error text)."""
        self.skipped = ''
        store = store or store_in(text) or load(self.path()).get('store') or ''
        if store:
            self.remember_store(name, store)
        message = text
        # What this thread was already given: a message to it carries the build, the send-back rules and the re-orientation only when it needs them.
        seen = load_json(delivered_path()).get(thread) if thread else None
        compactions = thread_compactions(thread) if thread else None
        compacted = (
            bool(seen)
            and compactions is not None
            and compactions > int(seen.get('compactions') or 0)
        )
        # The send-back rules go in full until a message that carried them has been sent, and again after a compaction.
        full_rules = not (seen and seen.get('rules')) or compacted
        build = ''
        with tempfile.TemporaryDirectory() as tmp:
            attach = []
            if store and os.path.isfile(os.path.join(store, 'pair.json')):
                attach, message, build = self.with_bundle(
                    name, text, store, thread, seen, full_rules, tmp
                )
            if compacted:
                message = reorientation(name, text, store) + message
            cwd = (bundle.repo_of(store) if store else '') or None
            digest = hashlib.sha256(f'{name}\n{text}'.encode()).hexdigest()
            if thread and self.pending(thread, digest):
                self.skipped = thread
                return thread, ''
            msg_id = uuid.uuid4().hex[:12]
            message = f'{message}\n\n{MSG_ID} {msg_id}'
            tries = max(1, env_int('PSTACK_AMP_SEND_TRIES', 3))
            for attempt in range(tries):
                if thread:
                    # -x reads its message from the token right after it, so `-ox MESSAGE` comes last.
                    r = run(
                        ['amp', 'threads', 'continue', thread, *attach, '-ox', message],
                        cwd,
                    )
                else:
                    r = run(
                        [
                            'amp',
                            *flags,
                            '--title',
                            title or name,
                            *attach,
                            '-ox',
                            message,
                        ],
                        cwd,
                    )
                found = THREAD_RE.search(r.stdout or '')
                if r.returncode == 0 and found:
                    thread = found.group(0)
                    break
                err = (r.stderr or r.stdout or f'amp exited {r.returncode}').strip()
                # A failed send may still have reached the thread: a retry that resent it would deliver it twice.
                if thread and holds(export(thread), msg_id):
                    break
                if UPLOAD_FAILED not in err or attempt + 1 == tries:
                    return '', err
                time.sleep(env_int('PSTACK_AMP_RETRY_S', 10))
            record_sent(thread, {'hash': digest, 'id': msg_id, 'at': int(time.time())})
            # Only what this message carried is recorded: the thread's compaction count (a new thread's is read now), the build when it was
            # attached, and the full send-back rules when they went in it.
            if compactions is None:
                compactions = thread_compactions(thread)
            gave = {} if compactions is None else {'compactions': compactions}
            if build:
                gave['build'] = build
            if attach and full_rules and not MASTER_RE.search(name):
                gave['rules'] = True
            if gave:
                record_delivered(thread, gave)
            if attach:
                os.makedirs(os.path.dirname(sent_path(thread)), exist_ok=True)
                shutil.copyfile(attach[1], sent_path(thread))
        return thread, ''

    def pending(self, thread, digest):
        """Whether the same text went to thread last and the thread has not ended a turn since: sending it again would only queue a copy."""
        sent = load_json(outbox_path()).get(thread)
        if not isinstance(sent, dict) or sent.get('hash') != digest:
            return False
        return self.in_turn_since(thread, int(sent.get('at') or 0))

    def in_turn_since(self, thread, at):
        """Whether the thread has not ended a turn since `at`."""
        state, since = thread_state(export(thread))
        if state in SETTLED and since >= at:
            return False
        return state != ''

    def turn_ended_since(self, thread, at):
        """Whether the thread has ended a turn since `at` (an unknown state is not an end)."""
        state, since = thread_state(export(thread))
        return state in SETTLED and since >= at

    def relay_is_stale(self, record):
        """A pending relay whose thread has ended a turn since it was made: the master can no longer steer that turn."""
        return record.get('state') == PENDING and self.turn_ended_since(
            record['thread'], int(record.get('at') or 0)
        )

    def relay(self, name, text, store, steer=''):
        """A steer for the thread of role `name`, which is in a turn, as a relay the master sends with its own tools (a CLI message would only queue
        behind the turn): the bundle is packed under the master's .git, where upload_thread_file reaches it and no store bundle carries it, and the
        message and the record are written under relays/. (record, how) with how 'made', 'pending' (the same text is still waiting to be relayed) or
        'delivered' (it was relayed and the thread has not ended a turn since). A pending relay whose thread has ended a turn since is marked stale and
        made anew. ({'size': bytes}, 'too-large') when the bundle is over upload_thread_file's limit; (None, '') when there is nothing to relay with."""
        found = self.entry(name)
        thread = (found[1].get('thread') or '') if found else ''
        repo = bundle.repo_of(store) if store else ''
        if not thread or not repo or not os.path.isdir(os.path.join(repo, '.git')):
            return None, ''
        with relay_lock():
            digest = hashlib.sha256(f'{name}\n{text}'.encode()).hexdigest()
            same = next(
                (
                    r
                    for r in relay_records(store)
                    if r.get('thread') == thread and r.get('hash') == digest
                ),
                None,
            )
            if same and same.get('state') == PENDING:
                if not self.relay_is_stale(same):
                    return same, 'pending'
                write_relay({**same, 'state': STALE})
                same = None
            if same and self.in_turn_since(thread, int(same.get('done_at') or 0)):
                return same, 'delivered'
            msg_id = uuid.uuid4().hex[:12]
            path = os.path.join(repo, '.git', 'pstack-relay', f'{msg_id}.tgz')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            bundle.pack(
                store,
                path,
                self.sender(),
                {'name': name, 'pane': found[1]['pane']},
                self.agents(),
                text,
            )
            size = os.path.getsize(path)
            if size > RELAY_LIMIT:
                os.remove(path)
                return {'size': size}, 'too-large'
            upload_to = f'.git/pstack-relay-{msg_id}.tgz'
            message = (
                f'{text}\n\npstack: before you act on this message, run `{SYNC} {repo}/{upload_to}`: '
                f'it merges the {store} store and its commits into your orb.'
                + ('' if MASTER_RE.search(name) else RETURN)
                + f'\n\n{MSG_ID} {msg_id}'
            )
            os.makedirs(relays_dir(), exist_ok=True)
            message_path = os.path.join(relays_dir(), f'{msg_id}.md')
            with open(message_path, 'w', encoding='utf-8') as f:
                f.write(message)
            record = {
                'id': msg_id,
                'name': name,
                'thread': thread,
                'store': store,
                'steer': steer,
                'hash': digest,
                'bundle': path,
                'message': message_path,
                'upload_to': upload_to,
                'state': PENDING,
                'at': int(time.time()),
            }
            write_relay(record)
            return record, 'made'

    def prompt(
        self,
        name,
        text,
        wait=False,
        until=None,
        timeout_ms=None,
        stdout=PIPE,
        stderr=PIPE,
    ):
        """Sends the prompt to the role's thread, starting it on the role's model when it has none yet, and prints one
        `amp NAME (THREAD): TEXT` line. Nothing waits: the role's reply arrives in the master's thread as a message."""
        if until and not wait:
            raise ValueError('prompt: until needs wait')
        found = self.entry(name)
        if found is None:
            text_err = f'amp: {NOT_FOUND}: no role {name}\n'
            if stderr is None:
                sys.stderr.write(text_err)
            return Reply(1, '', text_err if stderr == PIPE else None, NOT_FOUND)
        known, e = found
        if text in EXITS:
            self.end(known)
            return Reply(
                0, '' if stdout == PIPE else None, '' if stderr == PIPE else None
            )
        thread, err = self.send(known, text, e.get('thread') or '', e.get('args') or [])
        if not thread:
            text_err = f'amp: {self.failure}: {err}\n'
            if stderr is None:
                sys.stderr.write(text_err)
            return Reply(1, '', text_err if stderr == PIPE else None, self.failure)
        if thread != e.get('thread'):
            change(
                lambda doc: doc['agents'][known].__setitem__('thread', thread),
                self.path(),
            )
        out = sys.__stdout__ or sys.stdout
        if self.skipped:
            out.write(
                f'amp {known} ({thread}): not sent again: the same message is still pending there (its turn has not ended since): {text}\n'
            )
        else:
            out.write(f'amp {known} ({thread}): {text}\n')
        out.flush()
        return Reply(
            0,
            '' if stdout == PIPE else None,
            '' if stderr == PIPE else None,
            '',
            until or 'idle',
        )

    def new_master(self, name):
        """A pane for the next master, registered as `name` with no thread yet; its id."""
        pane = self.new()
        change(
            lambda doc: doc['agents'].__setitem__(
                name, {'pane': pane, 'kind': 'amp', 'args': [], 'thread': ''}
            ),
            self.path(),
        )
        return pane

    def launch_master(self, store, name, prompt, model=''):
        """Starts the master registered as `name` as a thread of its own, on model (by default this thread's mode), with the store's bundle.
        Its thread id; a start that fails drops the name and raises with amp's error."""
        from ..errors import NextMasterNotStarted

        model = model or self.thread_mode()
        thread, err = self.send(
            name, prompt, '', ['-m', model] if model else [], store, name
        )
        if not thread:
            change(lambda doc: doc['agents'].pop(name, None), self.path())
            raise NextMasterNotStarted(f'the next master thread did not start: {err}')

        def register(doc):
            doc['agents'][name].update({'thread': thread, 'pane': thread_pane(thread)})

        change(register, self.path())
        return thread

    def hand_over(self, name, to):
        """From now on the name `name` (the run's master) means the agent `to`: a reply a role still sends here is forwarded to it (sync)."""

        def point(doc):
            doc['agents'][name] = doc['agents'].pop(to)

        change(point, self.path())

    def thread_mode(self):
        """The agent mode this thread runs (a mode key or provider/model), '' when amp cannot say."""
        r = run(['amp', 'threads', 'export', own_thread()]) if own_thread() else None
        try:
            mode = (
                json.loads(r.stdout).get('agentMode') if r and r.returncode == 0 else ''
            )
        except (ValueError, AttributeError):
            mode = ''
        return mode if isinstance(mode, str) else ''

    def notify(self, title, body):
        return False

    def inspect_agent(self, name, lines=None):
        found = self.entry(name)
        thread = found[1].get('thread') if found else ''
        return f'amp threads markdown {thread}' if thread else f'cat {registry_path()}'

    def inspect_pane(self, pane):
        return f'cat {registry_path()}'

    def list_agents(self):
        return f'cat {registry_path()}'

    def list_kinds(self):
        return 'amp (any model: pass --model provider/name in the role arguments)'


def retire_lost(path, lost, store):
    """A write of this backend's registry dropped these entries' threads: each is retired for the run its entry worked for (else the
    registry's), so the master's sweep archives it once that is safe, whichever command made the write."""
    if path == registry_path():
        for name, entry in lost:
            Amp().retire(entry['thread'], name, entry.get('store') or store)


subagents.on_threads_lost = retire_lost
