"""Amp threads as the roles: every role is an Amp thread of its own, on the model its roster entry names, each in an orb of its own. A pane is
a name in pstack's registry, as with subagents; a role's thread starts with the first prompt it is sent (`amp -ox`), and every later prompt
continues it (`amp threads continue -ox`). Each orb has its own checkout and its own copy of the store, so every prompt carries a bundle
(pstack_core.bundle: the store's files and the commits the receiver may lack) as an attachment, and the receiver's first move is
`PSTACK_MUX=amp pair.sh sync <attachment>`. Nothing waits in this process: a role's reply is a prompt to the master's thread, carrying the
role's bundle the same way, and the master acts on it in its next turn."""

import calendar
import fcntl
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

from .. import bundle
from ..argv import Spec, read
from ..numbers import env_int
from ..store import TEXT
from .base import DEVNULL, PIPE, Agent, Reply
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
    # The thread send() left alone because the same text to it is still pending ('' when it sent).
    skipped = ''
    # `amp threads continue -ox` queues a message behind a running turn; the CLI has no way to steer one.
    steers_running = False

    def path(self):
        return registry_path()

    def context_used(self, name):
        found = self.entry(name)
        thread = found[1].get('thread') if found else ''
        if not thread:
            return None
        r = run(['amp', 'threads', 'context', thread])
        try:
            doc = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            return None
        doc = doc if isinstance(doc, dict) else {}
        used, limit = doc.get('totalTokens'), doc.get('maxContextTokens')
        compacted = doc.get('compactionCount') or 0
        if not (isinstance(used, int) and isinstance(limit, int) and limit > 0):
            return None
        return 1.0 if compacted else used / limit

    def role_thread(self, name):
        found = self.entry(name)
        return (found[1].get('thread') or '') if found else ''

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
        """The role's thread is done: it is archived and its name leaves the registry, so its next prompt starts a fresh thread."""
        found = self.entry(name)
        if found is None:
            return
        thread = found[1].get('thread') or ''
        if thread and thread != own_thread():
            run(['amp', 'threads', 'archive', thread])
        change(lambda doc: doc['agents'].pop(found[0], None), self.path())

    def rename(self, target, name):
        """Names the agent in the pane (or of the name) `target`, keeping its thread and model flags; a pane named by its thread (the
        master's at init) records that thread."""
        found = self.entry(target)
        pane = found[1]['pane'] if found else target
        if not self.pane_exists(pane):
            return False

        def move(doc):
            old = next((e for e in doc['agents'].values() if e['pane'] == pane), {})
            for gone in [
                n for n, e in doc['agents'].items() if e['pane'] == pane or n == name
            ]:
                del doc['agents'][gone]
            doc['agents'][name] = {
                'pane': pane,
                'kind': old.get('kind') or 'amp',
                'args': old.get('args') or [],
                'thread': pane[len('amp:') :]
                if pane.startswith('amp:T-')
                else old.get('thread') or '',
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

    def send(self, name, text, thread='', flags=(), store='', title=''):
        """Sends text to thread (a new thread on flags when ''), with the bundle of store attached. (thread, error text)."""
        self.skipped = ''
        store = store or store_in(text) or load(self.path()).get('store') or ''
        if store:
            change(lambda doc: doc.__setitem__('store', store), self.path())
        message = text
        with tempfile.TemporaryDirectory() as tmp:
            attach = []
            if store and os.path.isfile(os.path.join(store, 'pair.json')):
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
                build = (
                    '' if thread and MASTER_RE.search(name) else pack_build(build_path)
                )
                install = ''
                if build:
                    attach += ['--attach', build_path]
                    install = f'`{install_line(build)}` (it installs the pstack build the sender runs, the same on every orb), then '
                message = (
                    f'{text}\n\npstack: before you act on this message, run {install}`{SYNC} <the pstack-{name}.tgz attachment URL below>`: '
                    f'it merges the {store} store and its commits into your orb.'
                    + ('' if MASTER_RE.search(name) else RETURN)
                )
            cwd = bundle.repo_of(store) if store else None
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
            if attach:
                os.makedirs(os.path.dirname(sent_path(thread)), exist_ok=True)
                shutil.copyfile(attach[1], sent_path(thread))
        return thread, ''

    def pending(self, thread, digest):
        """Whether the same text went to thread last and the thread has not ended a turn since: sending it again would only queue a copy."""
        sent = load_json(outbox_path()).get(thread)
        if not isinstance(sent, dict) or sent.get('hash') != digest:
            return False
        state, since = thread_state(export(thread))
        if state in SETTLED and since >= int(sent.get('at') or 0):
            return False
        return state != ''

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
        change(
            lambda doc: doc['agents'][name].__setitem__('thread', thread), self.path()
        )
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
