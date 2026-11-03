"""No multiplexer: every role is a subagent of the one coding-agent session the master runs in. Nothing here runs a program. A pane is a
name in pstack's registry, a prompt is a line on stdout (and in the role's inbox file) that the master hands to a subagent with its host's
subagent tool, and nothing waits: the master runs the subagent, then the next command, which reads the files the subagent wrote."""

import fcntl
import hashlib
import json
import os
import sys

from .. import procinfo
from .base import DEVNULL, PIPE, Agent, Mux, Reply

NOT_FOUND = 'agent_not_found'
# What ends an agent's session; to a subagent role it means the role is over, and the next prompt goes to a fresh subagent.
EXITS = ('/exit', '/quit')
MASTER = 'subagent:master'
SHELLS = ('bash', 'sh', 'zsh', 'dash', 'fish')


def host_session():
    """The pid of the coding-agent session this command runs under: the first ancestor that is not a shell, '' when none is read."""
    root = procinfo.proc_root()
    pid, depth = os.getppid(), 0
    while pid > 1 and depth < 12:
        proc = procinfo.read(pid, root)
        if proc is None:
            return ''
        if os.path.basename(proc.comm).lstrip('-') not in SHELLS:
            return str(pid)
        pid, depth = proc.ppid, depth + 1
    return ''


def master_pane():
    """The pane of the master: one per checkout and host session, so two kitchens in one checkout, each in a session of its own, keep
    their masters apart."""
    digest = hashlib.sha1(os.path.realpath(os.getcwd()).encode()).hexdigest()[:8]
    host = host_session()
    return f'{MASTER}:{digest}' + (f':{host}' if host else '')


def is_master(pane):
    return pane == MASTER or pane.startswith(f'{MASTER}:')


def state_dir():
    state = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(state, 'pstack', 'subagents')


def registry_path():
    return os.path.join(state_dir(), 'registry.json')


def inbox(name):
    """The file that holds every prompt sent to the role, one per line."""
    return os.path.join(state_dir(), 'inbox', f'{name}.txt')


def load():
    """{'next': the next pane number, 'agents': {name: {'pane', 'kind'}}, 'panes': [ids]}."""
    try:
        with open(registry_path(), encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    agents = doc.get('agents')
    agents = agents if isinstance(agents, dict) else {}
    panes = doc.get('panes')
    panes = panes if isinstance(panes, list) else []
    nxt = doc.get('next')
    nxt = nxt if isinstance(nxt, int) else 1
    return {
        'next': nxt,
        'agents': {
            k: v
            for k, v in agents.items()
            if isinstance(v, dict) and isinstance(v.get('pane'), str)
        },
        'panes': [p for p in panes if isinstance(p, str)],
    }


def change(apply):
    """Applies apply(doc) to the registry under its lock; apply's value."""
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load()
        value = apply(doc)
        with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(f'{path}.tmp', path)
    return value


class Subagents(Mux):
    program = ''
    label = 'subagents'
    failure = 'subagent_error'
    pane_var = ''
    inside = 'a single session'
    tracks_sessions = False
    starts_master = False
    # Nothing in this process can wait for a subagent: the master runs it after the command returns.
    waits = False

    def require(self, what=None):
        """A single session needs nothing."""

    def own_pane(self):
        return master_pane()

    def location(self):
        return '', ''

    def pane_env(self, pane, env):
        # No process carries this, so /proc names none of the subagent's work: the files it writes are its trail.
        return [f'PSTACK_SUBAGENT_PANE={pane}']

    def outside_env(self):
        return {}

    def pane_exists(self, pane):
        return is_master(pane) or pane in load()['panes']

    def pane_close(self, pane):
        def drop(doc):
            doc['panes'] = [p for p in doc['panes'] if p != pane]
            for name in [n for n, e in doc['agents'].items() if e['pane'] == pane]:
                del doc['agents'][name]

        change(drop)
        return True

    def pane_screen(self, pane, lines):
        return 0, ''

    def pane_key(self, pane, key):
        return True

    def pane_size(self, pane):
        return 200, 50

    def new(self):
        def add(doc):
            pane = f'subagent:{doc["next"]}'
            doc['next'] += 1
            doc['panes'].append(pane)
            return pane

        return change(add)

    def split(self, anchor, direction, cwd, env=()):
        return self.new()

    def tab(self, near, label, cwd, env=()):
        return self.new()

    def processes(self, pane):
        return None

    def entry(self, name):
        """(name, entry) of the agent called `name` or living in pane `name`, or None."""
        agents = load()['agents']
        if name in agents:
            return name, agents[name]
        return next(((n, e) for n, e in agents.items() if e['pane'] == name), None)

    def agent(self, name):
        found = self.entry(name)
        if found is None:
            return None
        known, e = found
        return Agent(known, e.get('kind') or '', 'idle', e['pane'], '', None)

    def screen(self, name, lines):
        return 0, ''

    def key(self, name, key, stderr=DEVNULL):
        if key == 'ctrl+d':
            self.end(name)
        return True

    def end(self, name):
        """The role's subagent is done: its name leaves the registry, so the role reads absent until it starts again."""
        found = self.entry(name)
        if found is not None:
            change(lambda doc: doc['agents'].pop(found[0], None))

    def rename(self, target, name):
        found = self.entry(target)
        pane = found[1]['pane'] if found else target
        if not self.pane_exists(pane):
            return False

        def move(doc):
            kind = next(
                (
                    e.get('kind', '')
                    for e in doc['agents'].values()
                    if e['pane'] == pane
                ),
                '',
            )
            for old in [
                n for n, e in doc['agents'].items() if e['pane'] == pane or n == name
            ]:
                del doc['agents'][old]
            doc['agents'][name] = {'pane': pane, 'kind': kind}

        change(move)
        return True

    def start(
        self, name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE
    ):
        """Names the role; its subagent starts with the first prompt it is sent, on the host's own model ({kind} is the roster's wish)."""

        def claim(doc):
            for old in [
                n for n, e in doc['agents'].items() if e['pane'] == pane or n == name
            ]:
                del doc['agents'][old]
            doc['agents'][name] = {'pane': pane, 'kind': kind}

        change(claim)
        return Reply(0, '' if stdout == PIPE else None, '' if stderr == PIPE else None)

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
        """Hands the prompt to the master: one `subagent NAME (KIND): TEXT` line on stdout, kept in the role's inbox file too. A prompt to the
        master itself (a report's notice) is one `master: TEXT` line."""
        if until and not wait:
            raise ValueError('prompt: until needs wait')
        found = self.entry(name)
        if found is None:
            text_err = f'subagents: {NOT_FOUND}: no role {name}\n'
            if stderr is None:
                sys.stderr.write(text_err)
            return Reply(1, '', text_err if stderr == PIPE else None, NOT_FOUND)
        known, e = found
        if text in EXITS and not is_master(e['pane']):
            self.end(known)
            return Reply(
                0, '' if stdout == PIPE else None, '' if stderr == PIPE else None
            )
        if is_master(e['pane']):
            line = f'master: {text}\n'
        else:
            line = f'subagent {known} ({e.get("kind") or "agent"}): {text}\n'
            os.makedirs(os.path.dirname(inbox(known)), exist_ok=True)
            with open(inbox(known), 'a', encoding='utf-8') as f:
                f.write(text + '\n')
        # The master reads this line whatever the caller does with stdout (some commands capture their own output).
        sys.stdout.flush()
        out = sys.__stdout__ or sys.stdout
        out.write(line)
        out.flush()
        return Reply(
            0,
            '' if stdout == PIPE else None,
            '' if stderr == PIPE else None,
            '',
            until or 'idle',
        )

    def wait(self, name, until=(), timeout_ms=None, stdout=PIPE, stderr=PIPE):
        if self.entry(name) is None:
            return Reply(1, '', '', NOT_FOUND)
        return Reply(0, '', '', '', 'idle')

    def notify(self, title, body):
        return False

    def inspect_agent(self, name, lines=None):
        return f'cat {inbox(name)}'

    def inspect_pane(self, pane):
        return f'cat {registry_path()}'

    def list_agents(self):
        return f'cat {registry_path()}'

    def list_kinds(self):
        return 'command -v claude codex pi devin muse'
