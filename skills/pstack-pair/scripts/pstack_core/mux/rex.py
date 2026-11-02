"""Rex (superlogical.com/rex) as the multiplexer: every call is one `rex` command, read here and nowhere else. Rex has blocks, not agents:
an agent is a name pstack gives a block, kept in its own registry, and an agent's status is the state the agent reports to its block
(`program_status`, OSC 7501), or, for an agent that reports none, whether its screen still changes."""

import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time

from .. import procinfo
from ..errors import NotInMux
from ..numbers import env_float
from ..store import TEXT
from .base import (
    DEVNULL,
    MERGED,
    NOT_READY,
    PANE_BUSY,
    PIPE,
    STALLED,
    TIMEOUT,
    TRUST_RE,
    Agent,
    Mux,
    Process,
    Reply,
)

# Rex's program states, in pstack's words: an agent that reports an error waits for the human, as a blocked one does.
STATES = {
    'idle': 'idle',
    'working': 'working',
    'done': 'done',
    'blocked': 'blocked',
    'error': 'blocked',
}
SETTLED = ('idle', 'done', 'blocked')
# Rex's names for the keys pstack presses.
KEYS = {'esc': 'escape'}
# Polls of an unchanged screen after which an agent that reports no state counts as idle.
QUIET = 3
# A prompt's wait when the caller names none.
WAIT_MS = 30000
NOT_FOUND = 'agent_not_found'
START_FAILED = 'agent_start_failed'


def run(args, stdout=PIPE, stderr=PIPE):
    """The one place rex is run: its CompletedProcess, each stream as the caller asked."""
    return subprocess.run(
        ['rex', *args], stdout=stdout, stderr=stderr, check=False, **TEXT
    )


def parse(reply):
    try:
        return json.loads(reply or '')
    except ValueError:
        return None


def field(doc, key, kind=str):
    value = doc.get(key) if isinstance(doc, dict) else None
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def call(block, method):
    """The parsed result of a block method, or None when the block is gone or the reply is not JSON."""
    r = run(['block', 'call', '-b', block, method])
    return parse(r.stdout) if r.returncode == 0 else None


def registry_path():
    state = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(state, 'pstack', 'rex-agents.json')


def registry():
    """{name: {'block', 'kind'}} of the agents pstack named; {} when there are none."""
    try:
        with open(registry_path(), encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return {
        k: v
        for k, v in (doc.items() if isinstance(doc, dict) else ())
        if isinstance(v, dict) and isinstance(v.get('block'), str)
    }


def register(change):
    """Applies change(agents) to the registry under its lock and writes it back whole."""
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f'{path}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        agents = registry()
        change(agents)
        with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
            json.dump(agents, f, indent=1, sort_keys=True)
        os.replace(f'{path}.tmp', path)


def owner(record):
    return field(field(record, 'owner', dict), 'pid', int)


def kind_of(app):
    """pstack's kind for the app a block reports (`claude-code` is claude)."""
    return 'claude' if app == 'claude-code' else app


class Pane:
    """What one look at a block shows: its foreground program and shell, and the state its program reports."""

    def __init__(self, process, status):
        foreground = field(process, 'foreground', dict) or {}
        chain = [foreground] + [
            a for a in field(process, 'ancestors', list) or [] if isinstance(a, dict)
        ]
        while len(chain) > 1 and chain[-1].get('name') == 'login':
            chain.pop()
        self.pid = field(foreground, 'pid', int)
        self.name = field(foreground, 'name') or ''
        self.cwd = field(foreground, 'cwd') or ''
        self.shell = field(chain[-1], 'pid', int)
        # A record left by an earlier program in the block (done and error outlive it) is not this agent's: only one owned by the
        # foreground program or a process between it and the shell counts.
        mine = {field(p, 'pid', int) for p in chain[:-1]} - {None}
        records = [
            r for r in field(status, 'records', list) or [] if isinstance(r, dict)
        ]
        own = [r for r in records if owner(r) in mine]
        self.record = own[-1] if own else None
        # Records of other owners, which may be the agent's own children (a hook that reports for it).
        self.others = [r for r in records if r not in own]

    @property
    def running(self):
        """An agent, not the shell, holds the foreground."""
        return self.pid is not None and self.pid != self.shell

    @property
    def state(self):
        """The agent's reported status, '' when it reports none."""
        return STATES.get(field(self.record, 'state') or '', '')

    @property
    def stamp(self):
        """When the agent last reported, so a turn too quick to be seen working still shows."""
        return field(self.record, 'updated_at') or ''

    @property
    def app(self):
        return kind_of(field(self.record, 'app') or '') or self.name


class Rex(Mux):
    program = 'rex'
    label = 'Rex'
    failure = 'rex_error'
    pane_var = 'REX_BLOCK'
    inside = 'REX_BLOCK set'

    # The calling block.
    def require(self, what=None):
        if not os.environ.get('REX_BLOCK'):
            raise NotInMux(what or 'not running inside Rex (REX_BLOCK unset)')

    def own_pane(self):
        return os.environ.get('REX_BLOCK', '')

    def location(self):
        return os.environ.get('REX_SESSION', ''), ''

    def pane_env(self, pane, env):
        return [f'REX_BLOCK={pane}']

    def outside_env(self):
        return {'REX_BLOCK': '', 'REX_SESSION': ''}

    # Blocks.
    def look(self, block):
        """The block's Pane, or None when it is gone."""
        process = call(block, 'process')
        if process is None:
            return None
        pane = Pane(process, call(block, 'program_status'))
        if pane.record is None and pane.others and pane.pid is not None:
            below = procinfo.descendants(procinfo.from_ps(), {pane.pid}, set())
            mine = [r for r in pane.others if owner(r) in below]
            pane.record = mine[-1] if mine else None
        return pane

    def pane_exists(self, pane):
        return run(['block', 'inspect', pane, '--json']).returncode == 0

    def pane_close(self, pane):
        """Closes the block and forgets the names it held."""
        if run(['block', 'close', pane]).returncode != 0:
            return False

        def forget(agents):
            for name in [n for n, e in agents.items() if e['block'] == pane]:
                del agents[name]

        register(forget)
        return True

    def pane_screen(self, pane, lines):
        r = run(['capture', '-b', pane, '--trim'])
        return r.returncode, ''.join(r.stdout.splitlines(True)[-lines:])

    def pane_key(self, pane, key, stderr=PIPE):
        return (
            run(
                ['send-key', '-b', pane, KEYS.get(key, key)], DEVNULL, stderr
            ).returncode
            == 0
        )

    def pane_size(self, pane):
        size = call(pane, 'size')
        return (
            (size.get('columns'), size.get('rows')) if isinstance(size, dict) else None
        )

    def shell(self, env):
        """The command a new block runs: the login shell, carrying env, or none (Rex's own) when there is no env."""
        if not env:
            return []
        return [
            '--shell',
            'none',
            '--',
            'env',
            *env,
            os.environ.get('SHELL') or '/bin/sh',
            '-l',
        ]

    def made(self, args):
        block = parse(run(args, stderr=None).stdout)
        ids = field(block, 'block_ids', list) or []
        return ids[0] if ids and isinstance(ids[0], str) else ''

    def split(self, anchor, direction, cwd, env=()):
        side = 'below' if direction == 'down' else direction
        return self.made(
            [
                'split',
                '-b',
                anchor,
                '--split',
                side,
                '--focus=false',
                '-c',
                cwd,
                '--json',
                *self.shell(env),
            ]
        )

    def tab(self, near, label, cwd, env=()):
        session = field(
            parse(run(['block', 'inspect', near, '--json']).stdout), 'session_id'
        )
        scope = ['-s', session] if session else []
        return self.made(
            [
                'window',
                'new',
                *scope,
                label,
                '--focus=false',
                '-c',
                cwd,
                '--json',
                *self.shell(env),
            ]
        )

    def processes(self, pane):
        pane = self.look(pane)
        if pane is None:
            return None
        return Process(pane.shell, pane.pid, None if pane.pid is None else (pane.pid,))

    # Agents.
    def block_of(self, name):
        """The block of the agent called `name`, or `name` itself when it names a block and no agent."""
        entry = registry().get(name)
        return entry['block'] if entry else name

    def named(self, block):
        """(name, kind) pstack gave the agent in the block, ('', '') when none."""
        for name, entry in registry().items():
            if entry['block'] == block:
                return name, entry.get('kind') or ''
        return '', ''

    def agent(self, name):
        block = self.block_of(name)
        pane = self.look(block)
        if pane is None or not pane.running:
            return None
        known, kind = self.named(block)
        # An agent pstack never named goes by its block id, which every call here takes as a name.
        return Agent(
            known or block,
            kind or pane.app,
            pane.state or self.screen_status(block),
            block,
            pane.cwd,
            None,
        )

    def screen_status(self, block):
        """An agent that reports no state, by two looks at its screen one poll apart: working when it changed, else idle (panes.agent_status's
        busy-screen check still applies)."""
        first = self.pane_screen(block, 200)[1]
        time.sleep(env_float('PAIR_REX_POLL_S', 0.5))
        return 'working' if self.pane_screen(block, 200)[1] != first else 'idle'

    def screen(self, name, lines):
        return self.pane_screen(self.block_of(name), lines)

    def key(self, name, key, stderr=DEVNULL):
        return self.pane_key(self.block_of(name), key, stderr)

    def rename(self, target, name):
        """Names the agent in the block; a name another block's live agent holds is not taken, as Herdr's names are unique."""
        block = self.block_of(target)
        if not self.pane_exists(block):
            return False
        held = registry().get(name)
        if held and held['block'] != block:
            other = self.look(held['block'])
            if other is not None and other.running:
                return False

        def move(agents):
            kind = next(
                (e.get('kind', '') for e in agents.values() if e['block'] == block), ''
            )
            for old in [
                n for n, e in agents.items() if e['block'] == block or n == name
            ]:
                del agents[old]
            agents[name] = {'block': block, 'kind': kind}

        register(move)
        run(['block', 'rename', block, name])
        return True

    def type(self, block, text):
        return (
            run(['send', '-b', block, '--', text]).returncode == 0
            and run(['send-key', '-b', block, 'enter']).returncode == 0
        )

    def answer(self, code, error='', status='', message='', stdout=PIPE, stderr=PIPE):
        """The Reply of an emulated call; its failure's text goes where the caller asked for stderr."""
        text = f'rex: {error}: {message}\n' if error else ''
        err = text if stderr == PIPE else None
        if stderr is None and text:
            sys.stderr.write(text)
        out = text if stderr == MERGED else ('' if stdout == PIPE else None)
        return Reply(code, out, err, error, status)

    def poll(self, block, timeout_ms, done, screen=None):
        """Looks at the block every PAIR_REX_POLL_S until done(pane, status) holds or timeout_ms (None: no limit) passes. The status is the
        reported one, or, for an agent that reports none, read from its screen: working once it has changed, idle after QUIET looks in a row
        at an unchanged one, unknown before either, counted from `screen` when the caller read it before acting. (pane, status, held) at the
        last look; pane None when the block is gone."""
        limit = float('inf') if timeout_ms is None else float(timeout_ms) / 1000
        deadline = time.monotonic() + limit
        quiet = 0
        while True:
            pane = self.look(block)
            if pane is None:
                return None, '', False
            status = pane.state
            if not status:
                now = self.pane_screen(block, 200)[1]
                changed = screen is not None and now != screen
                quiet = quiet + 1 if screen is not None and not changed else 0
                screen = now
                status = (
                    'working' if changed else 'idle' if quiet >= QUIET else 'unknown'
                )
            if done(pane, status):
                return pane, status, True
            if time.monotonic() >= deadline:
                return pane, status, False
            time.sleep(env_float('PAIR_REX_POLL_S', 0.5))

    def start(
        self, name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE
    ):
        """Types the agent's command line into the block's shell and waits for the agent to settle. A block whose foreground is not its
        shell is busy; an agent that opens blocked (a dialog) is not ready."""
        say = lambda code, error='', message='': self.answer(
            code, error, '', message, stdout, stderr
        )
        look = self.look(pane)
        if look is None:
            return say(1, NOT_FOUND, message=f'no block {pane}')
        if look.running:
            return say(1, PANE_BUSY, f'{look.name} holds the foreground of {pane}')

        def claim(agents):
            for old in [n for n, e in agents.items() if e['block'] == pane]:
                del agents[old]
            agents[name] = {'block': pane, 'kind': kind}

        if not self.type(pane, shlex.join([kind, *args])):
            return say(1, self.failure, message=f'cannot type into {pane}')
        register(claim)
        run(['block', 'rename', pane, name])
        stall = env_float('PAIR_REX_STALL_S', 15) * 1000
        up, _, held = self.poll(
            pane, min(stall, float(timeout_ms)), lambda p, s: p.running
        )
        if up is None or not held:
            return say(1, START_FAILED, f'{kind} never took the foreground of {pane}')
        last, status, held = self.poll(
            pane, timeout_ms, lambda p, s: not p.running or s in SETTLED
        )
        if last is None or not last.running:
            return say(1, START_FAILED, f'{kind} exited in {pane}')
        if not held:
            return say(1, TIMEOUT, f'{kind} did not settle in {timeout_ms}ms')
        # An agent that reports no state shows its folder-trust dialog only on screen.
        dialog = not last.state and TRUST_RE.search(
            re.sub(r'[\n\t ]+', ' ', self.pane_screen(pane, 40)[1])
        )
        if status == 'blocked' or dialog:
            return say(1, NOT_READY, 'blocked during startup')
        return say(0)

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
        if until and not wait:
            raise ValueError('prompt: until needs wait')
        say = lambda code, error='', status='', message='': self.answer(
            code, error, status, message, stdout, stderr
        )
        block = self.block_of(name)
        before = self.look(block)
        if before is None:
            return say(1, NOT_FOUND, message=f'no agent {name}')
        if not before.running:
            return say(
                1, NOT_READY, message=f'{name} is no longer the foreground of {block}'
            )
        # The screen of an agent that reports no state is read once the text is typed and before Enter submits it, so the echo of the
        # text does not count as the agent taking it; the echo gets one poll interval to land.
        typed = run(['send', '-b', block, '--', text]).returncode == 0
        screen = None
        if not before.state:
            time.sleep(env_float('PAIR_REX_POLL_S', 0.5))
            screen = self.pane_screen(block, 200)[1]
        if not (typed and run(['send-key', '-b', block, 'enter']).returncode == 0):
            return say(1, self.failure, message=f'cannot type into {block}')
        if not wait:
            return say(0, status=before.state or 'idle')
        timeout_ms = WAIT_MS if timeout_ms is None else timeout_ms
        if until:
            # A turn that went working and back between two looks still stamped its report.
            seen = lambda p, s: (
                s == until
                or (
                    until == 'working'
                    and p.stamp != before.stamp
                    and s in ('working', 'idle', 'done')
                )
            )
            last, _, held = self.poll(block, timeout_ms, seen, screen)
            if last is None:
                return say(1, NOT_FOUND, message=f'{name} is gone')
            return (
                say(0, status=until)
                if held
                else say(1, TIMEOUT, '', f'{name} not {until} in {timeout_ms}ms')
            )
        stall = env_float('PAIR_REX_STALL_S', 15) * 1000
        took = lambda p, s: p.stamp != before.stamp or s in ('working', 'blocked')
        last, _, held = self.poll(block, min(stall, float(timeout_ms)), took, screen)
        if last is None:
            return say(1, NOT_FOUND, message=f'{name} is gone')
        if not held:
            return say(1, STALLED, message=f'{name} did not take the prompt')
        last, status, held = self.poll(
            block, timeout_ms, lambda p, s: not p.running or s in SETTLED
        )
        if last is None or not last.running:
            return say(1, NOT_FOUND, message=f'{name} is gone')
        if not held:
            return say(
                1, TIMEOUT, status, f'{name} still {status} after {timeout_ms}ms'
            )
        return say(0, status=status)

    def wait(self, name, until=(), timeout_ms=None, stdout=PIPE, stderr=PIPE):
        say = lambda code, error='', status='', message='': self.answer(
            code, error, status, message, stdout, stderr
        )
        block = self.block_of(name)
        first = self.look(block)
        if first is None or not first.running:
            return say(1, NOT_FOUND, message=f'no agent {name}')
        target = until or SETTLED
        last, status, held = self.poll(
            block, timeout_ms, lambda p, s: not p.running or s in target
        )
        if last is None or not last.running:
            return say(1, NOT_FOUND, message=f'{name} is gone')
        return (
            say(0, status=status)
            if held
            else say(1, TIMEOUT, status, f'{name} still {status} after {timeout_ms}ms')
        )

    def notify(self, title, body):
        """Rex shows no notification a program outside the block can raise."""
        return False

    # What a message tells the human to run.
    def inspect_agent(self, name, lines=None):
        return f'rex capture -b {self.block_of(name)} --trim' + (
            f' | tail -n {lines}' if lines else ''
        )

    def inspect_pane(self, pane):
        return f'rex capture -b {pane} --trim'

    def list_agents(self):
        return 'rex block ls'

    def list_kinds(self):
        return 'command -v claude codex pi devin muse prime-agent'
