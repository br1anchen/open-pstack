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

from .. import bundle, git, gitstore
from ..argv import Spec, read
from ..errors import PairError
from ..numbers import env_int
from ..secrets import REDACT_RULE
from . import amp_routing, subagents
from ..store import TEXT, expected_report, header_field, ready_file, revisions
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
    'the turn. ' + REDACT_RULE
)
MASTER_RE = re.compile(r'-master(-g[0-9]+)?$')
ROLES = ('sidekick', 'verifier', 'consultant', 'master')
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
    amp_routing.mirror_relay(record)


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


def record_given(thread, compactions, build, rules):
    """Only what a message carried is recorded as given to the thread: its compaction count (a new thread's is read now), the build when it was
    attached, and the full send-back rules when they went in it."""
    if compactions is None:
        compactions = thread_compactions(thread)
    gave = {} if compactions is None else {'compactions': compactions}
    if build:
        gave['build'] = build
    if rules:
        gave['rules'] = True
    if gave:
        record_delivered(thread, gave)


SKILL_RE = re.compile(r'^(pstack-[a-z-]+) [A-Z]+\b')


def reorientation(name, text, store, fresh=False):
    """What a role that compacted since its last message reads first: its role's skill file and the store, which its summary may have lost; a fresh
    thread that is sent a bare `pstack-<skill> VERB` message (a consult after its consultant was archived) has no bootstrap, so it reads the same
    and is told it is new. '' when the message names no skill (it is no message to a role)."""
    found = SKILL_RE.match(text)
    if not found or MASTER_RE.search(name):
        return ''
    skill, role = found.group(1), name.rsplit('-', 1)[-1]
    held = f', the store {store} (standing-orders.md, pair.json),' if store else ''
    lead = (
        'You are a new thread for this role. Read'
        if fresh
        else 'You compacted since the last message. Re-read'
    )
    return (
        f'{lead} ~/.agents/skills/{skill}/SKILL.md (your role: {role}){held} '
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
    amp_routing.mirror(thread, doc[thread])


def keep_sent_bundle(thread, attach):
    """The bundle a message to thread carried (attach is its `--attach FILE` pair, empty when it carried none), kept as what was sent to it."""
    if attach:
        os.makedirs(os.path.dirname(sent_path(thread)), exist_ok=True)
        shutil.copyfile(attach[1], sent_path(thread))


def record_started(thread, name, store, created):
    """A thread the kitchen started for the role `name` of the run `store`: recorded at once, so the run's sweep lists it even if the registry
    later loses its entry. `created` is whether the send made the thread: a send to an existing thread, or one with no store, records nothing,
    for current_threads reads the newest `started` as the role's thread."""
    if not (created and store):
        return
    from ..commands import nest

    nest.record_pending(store, thread, role_of(name))
    record_thread(
        thread,
        {
            'name': name,
            'role': role_of(name),
            'store': os.path.realpath(store),
            'started': int(time.time()),
        },
    )


def supersedes(current, thread):
    """Whether `current`, the thread a registry name holds now (empty when it holds none), has the name instead of `thread`: thread is recorded
    retired or archived, or current was started after it. A late bundle from such a thread must not take the name back."""
    known = load_json(threads_path())
    mine = known.get(thread) or {}
    if mine.get('at') or mine.get('archived'):
        return True
    newer = int((known.get(current) or {}).get('started') or 0)
    return bool(current and current != thread and newer > int(mine.get('started') or 0))


def repo_filter(url):
    """`host/owner/name` of a git remote URL, for amp's `repo:` search filter, whatever its form: https with credentials, ssh://, scp-like
    `git@host:owner/name`, with or without `.git`. '' for no remote, or one that has no host (a local path)."""
    url = (url or '').strip()
    scp = re.fullmatch(r'(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)', url)
    full = re.fullmatch(
        r'[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?([^/:]+)(?::[0-9]+)?/(.+)', url
    )
    found = full or scp
    if not found:
        return ''
    host, path = found.groups()
    return f'{host}/{path.strip("/").removesuffix(".git")}'


def run_titles(slug):
    """The titles of a run's role threads: `<slug>-<role>`, and `-g<n>` after a master."""
    return re.compile(
        re.escape(slug or '\0')
        + r'-(?:sidekick|verifier|consultant|master(?:-g[0-9]+)?)'
    )


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
    order = revisions(reports)
    found = re.search(
        r'^head:\s*([0-9a-f]{7,40})\s*$',
        open(max(reports, key=lambda p: order[p].order), encoding='utf-8').read(),
        re.M,
    )
    head = found.group(1) if found else ''
    return (
        head
        if head and not git.ok(repo, 'cat-file', '-e', f'{head}^{{commit}}')
        else ''
    )


def live_threads(doc):
    """{thread: role name} of the threads pair.json's roles run in now, through the registry and the master's pane. A verifier is live only while a
    verification is open (.verifying): its entry stays in pair.json afterwards, but its thread is done once the verdict is recorded."""
    agents = load(registry_path())['agents']
    live = {}
    for role in ('sidekick', 'consultant', 'verifier', 'master'):
        if role == 'verifier' and not doc.get('verifying'):
            continue
        entry = doc.get(role) if isinstance(doc.get(role), dict) else {}
        name = entry.get('name') or ''
        thread = (agents.get(name) or {}).get('thread') or ''
        if role == 'master' and not thread:
            pane = entry.get('pane_id') or ''
            thread = pane[len('amp:') :] if pane.startswith('amp:T-') else ''
        if thread:
            live[thread] = name
    return {**current_threads(doc), **live}


def current_threads(doc):
    """{thread: name} of the newest thread recorded for each role name of pair.json, while it is neither retired nor archived: the registry may\n    have lost its entry, and a role's current thread must still read as in use. A verifier counts only while a verification is open."""
    names = {}
    for role in ('sidekick', 'consultant', 'verifier', 'master'):
        entry = doc.get(role) if isinstance(doc.get(role), dict) else {}
        if entry.get('name') and (role != 'verifier' or doc.get('verifying')):
            names[entry['name']] = role
    newest = {}
    for thread, e in load_json(threads_path()).items():
        if e.get('name') in names and e.get('started'):
            if int(e['started']) >= int(newest.get(e['name'], ('', 0))[1]):
                newest[e['name']] = (thread, int(e['started']))
    return {
        t: n
        for n, (t, _) in newest.items()
        if not load_json(threads_path())[t].get('at')
        and not load_json(threads_path())[t].get('archived')
    }


def open_consults(store):
    """The consults and plan rounds the master sent the consultant that have no advice file yet: the units (NNN-slug[-cK]) of its send-consult and
    send-plan events whose advice/<unit>.md is missing."""
    try:
        with open(os.path.join(store, 'events.tsv'), encoding='utf-8') as f:
            rows = [line.rstrip('\n').split('\t') for line in f]
    except OSError:
        return []
    units = [
        row[3]
        for row in rows
        if len(row) >= 5
        and (
            row[2] == 'send-consult' or (row[2], row[4]) == ('send-plan', 'consultant')
        )
    ]
    return [
        u for u in units if not os.path.isfile(os.path.join(store, 'advice', f'{u}.md'))
    ]


def role_is_idle(store, doc, role):
    """Whether the live sidekick or consultant has nothing open in this run: the sidekick's last unit is done (rotation_required, and the report of
    the brief dispatched to it says done, so an open dispatch stays) and no brief is queued for it; the consultant has no consult or plan round
    waiting on its advice."""
    if role == 'sidekick':
        queue = os.path.join(store, 'queue')
        queued = os.path.isfile(queue) and os.path.getsize(queue) > 0
        brief = (doc.get('dispatch') or {}).get('brief') or ''
        return (
            (doc.get('sidekick') or {}).get('rotation_required') is True
            and not queued
            and bool(brief)
            and header_field(expected_report(store, brief), 'status') == 'done'
        )
    return role == 'consultant' and not open_consults(store)


def assess(thread, store, role, idle_roles=False, reconcile=False):
    """(the thread's state, why it must not be archived yet; with `reconcile`, the master first settles the store branch's pending requests and
    relays for the thread against its export, amp_routing.reconcile): '' when the store no longer uses it and nothing it did is unread. A thread in a turn,
    one with a message or steer relay still pending to it, one whose file the master's store lacks, one holding commits the master lacks, one
    whose replacement has not sent READY and an old master whose hand-over is open each stay. A live sidekick or consultant is in use, unless
    idle_roles says the sweep may take one that is settled and has nothing open (role_is_idle)."""
    doc = load_json(os.path.join(store, 'pair.json'))
    live = live_threads(doc)
    exported = None
    if (
        reconcile
        and amp_routing.master_store(store)
        and amp_routing.pending_work(store, thread)
    ):
        exported = export(thread)
        amp_routing.reconcile(store, thread, exported)
    in_use = ('in use', f'in use by {live.get(thread) or "this master"}')
    if thread == own_thread():
        return in_use
    if thread in live and not (
        idle_roles
        and role in ('sidekick', 'consultant')
        and role_is_idle(store, doc, role)
    ):
        return in_use
    state, since = thread_state(export(thread) if exported is None else exported)
    if thread in live and state not in SETTLED:
        return in_use
    if state == '':
        return 'unknown', 'state unknown (export failed or gave none)'
    if state not in SETTLED:
        return state, f'working ({state}): it is in a turn'
    if why := amp_routing.kept_by_branch(store, thread, role):
        return state, why
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
    if (
        role in ('sidekick', 'consultant')
        and role_doc.get('bootstrap_pending') is True
        and not os.path.isfile(os.path.join(store, ready_file(store, role)))
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


def deliver(thread, flags, title, attach, message, cwd, msg_id):
    """(thread, error text) of one message sent by the CLI: to `thread`, or, with none, to a new thread on `flags`. An upload that breaks off is
    retried (PSTACK_AMP_SEND_TRIES) unless the thread already holds the message's id."""
    tries = max(1, env_int('PSTACK_AMP_SEND_TRIES', 3))
    for attempt in range(tries):
        if thread:
            # -x reads its message from the token right after it, so `-ox MESSAGE` comes last.
            r = run(
                ['amp', 'threads', 'continue', thread, *attach, '-ox', message], cwd
            )
        else:
            r = run(['amp', *flags, '--title', title, *attach, '-ox', message], cwd)
        found = THREAD_RE.search(r.stdout or '')
        if r.returncode == 0 and found:
            return found.group(0), ''
        err = (r.stderr or r.stdout or f'amp exited {r.returncode}').strip()
        # A failed send may still have reached the thread: a retry that resent it would deliver it twice.
        if thread and holds(export(thread), msg_id):
            return thread, ''
        if UPLOAD_FAILED not in err or attempt + 1 == tries:
            return thread, err
        time.sleep(env_int('PSTACK_AMP_RETRY_S', 10))
    return thread, 'no attempt was made'


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
    archives_threads = True
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
        root = gitstore.root_of(path)
        if root:
            # Under git the message announces a commit of the store branch; the report is here once that commit is.
            at = re.search(r'store: \S+ at ([0-9a-f]{40})', text)
            held = at and git.ok(
                root, 'merge-base', '--is-ancestor', at.group(1), 'HEAD'
            )
            return (
                f'announced {at.group(1)[:9]} not yet fetched'
                if at and not held
                else None
            )
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
            pane = subagents.next_pane(doc, 'amp')
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
        amp_routing.routing(store)
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
        named = {}
        for e in (doc.get('sidekick_threads') or {}).values():
            named.update({t: 'sidekick' for t in e if isinstance(t, str)})
        for e in master.get('sessions') or []:
            if isinstance(e, dict) and (e.get('session') or '').startswith('T-'):
                named.setdefault(e['session'], 'master')
        ours = run_titles(doc.get('slug'))
        for name, e in agents.items():
            if e.get('thread') and ours.fullmatch(name):
                named.setdefault(e['thread'], role_of(name))
        # A thread recorded with no store, which this store's own records name, is this store's in every sweep.
        unrecorded = {u.thread for u in unscoped}
        found.update(
            {t: r for t, r in named.items() if not retired_only or t in unrecorded}
        )
        rows = [
            ThreadRow(thread, role, *assess(
                    thread,
                    store,
                    role,
                    idle_roles=not retired_only,
                    reconcile=not retired_only,
                )
            )
            for thread, role in found.items()
            if not (known.get(thread) or {}).get('archived')
        ]
        return rows + (
            [] if retired_only else [u for u in unscoped if u.thread not in found]
        )

    def register(self, thread, role, store):
        """Records thread as a helper of the run at `store`; a thread recorded for another run is refused."""
        other = (load_json(threads_path()).get(thread) or {}).get('store') or ''
        if other and other != os.path.realpath(store):
            raise PairError(f'{thread} is recorded for {other}; not registered')
        record_thread(
            thread,
            {
                'name': f'{role}-registered',
                'role': role,
                'store': os.path.realpath(store),
                'started': int(time.time()),
                'registered': True,
            },
        )
        return True

    def discover(self, store):
        """Records, for the run at `store`, each unarchived thread that a search on its path or on its `<slug>-` title words finds: a thread
        titled exactly `<slug>-<role>` keeps that role, any other is a helper. The title search stays in the run's repository when it has an
        origin. This thread, a thread any run recorded and a thread titled for another run are left out."""
        doc = load_json(os.path.join(store, 'pair.json'))
        slug = doc.get('slug') or ''
        here, known = os.path.realpath(store), load_json(threads_path())
        origin = repo_filter(
            git.out(
                doc.get('git_root') or doc.get('cwd'), 'remote', 'get-url', 'origin'
            )
        )
        ours = run_titles(slug)
        found = []
        for query, by_title in (
            (f'archived:false "{here}"', False),
            (f'archived:false {slug}- ' + (f'repo:{origin}' if origin else ''), True),
        ):
            r = run(['amp', 'threads', 'search', '--json', '-n', '100', query.strip()])
            try:
                hits = json.loads(r.stdout) if r.returncode == 0 else []
            except ValueError:
                hits = []
            for hit in hits if isinstance(hits, list) else []:
                thread, title = hit.get('id') or '', hit.get('title') or ''
                titled = bool(slug) and bool(ours.fullmatch(title))
                if (
                    not thread
                    or thread == own_thread()
                    or (by_title and not titled)
                    or (known.get(thread) or {}).get('store')
                ):
                    continue
                role = role_of(title) if titled else 'helper'
                record_thread(
                    thread,
                    {
                        'name': title or f'{role}-discovered',
                        'role': role,
                        'store': here,
                    },
                )
                known[thread] = {'store': here}
                found.append((thread, role))
        return found

    def archive(self, row):
        if run(['amp', 'threads', 'archive', row.thread]).returncode:
            return False
        record_thread(row.thread, {'role': row.role, 'archived': int(time.time())})
        # A role's thread that is archived must not take its next prompt, so that prompt starts a fresh thread. The consultant's entry stays with its
        # model flags and an empty thread (as right after start), since a consult and a plan round need it live; another role's name leaves.
        for name, e in load(self.path())['agents'].items():
            if e.get('thread') == row.thread:
                if role_of(name) == 'consultant':
                    change(
                        lambda doc, n=name: doc['agents'][n].__setitem__('thread', ''),
                        self.path(),
                    )
                else:
                    self.end(name)
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
        on_branch = gitstore.is_git(store)
        attach = []
        if not on_branch:
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
        if on_branch:
            branch = f'{gitstore.BRANCH_PREFIX}{gitstore.slug_of(store)}'
            announce = f'\n\nstore: {branch} at {gitstore.head_of(store)}'
            sync = f'{SYNC} --branch {store} {branch}`: it refreshes the store branch and fetches the code it announces.'
        else:
            announce = ''
            sync = f'{SYNC} <the pstack-{name}.tgz attachment URL below>`: it merges the {store} store and its commits into your orb.'
        message = (
            f'{text}{announce}\n\npstack: before you act on this message, run {install}`{sync}'
            + ('' if MASTER_RE.search(name) else rules)
        )
        return attach, message, build

    def send(self, name, text, thread='', flags=(), store='', title=''):
        """Sends text to thread (a new thread on flags when ''), with the bundle of store attached. (thread, error text)."""
        self.skipped = ''
        created = not thread
        store = store or store_in(text) or load(self.path()).get('store') or ''
        if store:
            self.remember_store(name, store)
        message = text
        # A message to a role is the master's: what the master wrote since its last publication is on the branch before the role is told of it.
        if store and gitstore.is_git(store) and not MASTER_RE.search(name):
            gitstore.publish_role(store, 'master')
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
        digest = hashlib.sha256(f'{name}\n{text}'.encode()).hexdigest()
        if thread and self.pending(thread, digest):
            self.skipped = thread
            return thread, ''
        # Under git the master publishes the request before the external send, and a pending one left by a master that died is reused.
        msg_id, resumed = amp_routing.intend(store, name, thread, digest)
        if resumed and not thread:
            return '', (
                f'an earlier launch of {name} (request {msg_id}) was published and never settled, so it may have started a thread; '
                f'find that thread, or remove {amp_routing.launch_rel(msg_id)} from the store branch to launch again'
            )
        if resumed and thread and holds(export(thread), msg_id):
            amp_routing.settle(store, name, thread, msg_id, amp_routing.SENT, False)
            record_sent(thread, {'hash': digest, 'id': msg_id, 'at': int(time.time())})
            self.skipped = thread
            return thread, ''
        build, carried, called = '', False, False
        with tempfile.TemporaryDirectory() as tmp:
            attach = []
            try:
                if store and os.path.isfile(os.path.join(store, 'pair.json')):
                    carried = True
                    attach, message, build = self.with_bundle(
                        name, text, store, thread, seen, full_rules, tmp
                    )
                if compacted or created:
                    message = (
                        reorientation(name, text, store, created) + message
                    )
                cwd = (bundle.repo_of(store) if store else '') or None
                message = f'{message}\n\n{MSG_ID} {msg_id}'
                called = True
                thread, err = deliver(
                    thread, flags, title or name, attach, message, cwd, msg_id
                )
            finally:
                # No `amp` call was made, so the request cannot have reached the thread: left pending it would block every relaunch of the role.
                if not called:
                    amp_routing.settle(
                        store, name, thread, msg_id, amp_routing.FAILED, False
                    )
            if err:
                amp_routing.settle(
                    store, name, thread, msg_id, amp_routing.FAILED, False
                )
                return '', err
            record_sent(thread, {'hash': digest, 'id': msg_id, 'at': int(time.time())})
            record_started(thread, name, store, created)
            amp_routing.settle(store, name, thread, msg_id, amp_routing.SENT, created)
            record_given(
                thread,
                compactions,
                build,
                carried and full_rules and not MASTER_RE.search(name),
            )
            keep_sent_bundle(thread, attach if not gitstore.is_git(store or '') else [])
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
    registry's), so the master's sweep archives it once that is safe, whichever command made the write. A name still in the registry, held by
    another thread, was replaced, not ended: the thread that is its role's newest started one for the run is left alone then, as a stale
    write is the likeliest reason for the swap."""
    if path != registry_path():
        return
    held = load(path)['agents']
    known = load_json(threads_path())
    for name, entry in lost:
        run_store = entry.get('store') or store
        newest = max(
            (
                (int(e.get('started') or 0), t)
                for t, e in known.items()
                if e.get('name') == name
                and e.get('store') == (os.path.realpath(run_store) if run_store else '')
                and e.get('started')
            ),
            default=(0, ''),
        )[1]
        if name in held and newest == entry['thread']:
            continue
        Amp().retire(entry['thread'], name, run_store)


subagents.on_threads_lost = retire_lost
