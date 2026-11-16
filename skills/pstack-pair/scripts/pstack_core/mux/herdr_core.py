"""herdr-core (`hc`, herdr.dev) as the multiplexer: every call is one `hc` command, read here and nowhere else. hc's terminals are headless,
with no panes, tabs or layout: a pane is a terminal, by its address (`t3`); a split or a tab is a new terminal running a login shell in the
folder; and an agent starts by `exec` typed into that shell, so the terminal lives exactly as long as the agent. hc names cannot change and
take only 1-32 of a-z0-9-_., so pstack names an agent with the terminal label `pstack.agent` and its kind with `pstack.kind`. An agent's
status is the state it reports to hc (OSC 7501), or, for an agent that reports none, whether its screen still changes.

A prompt is a plain `hc send`, after pstack's own check that the agent is idle or done, as on Herdr. `hc send --if idle` would close the gap
between that check and the send, but hc refuses it on `done`, which Claude reports after every turn (herdr-core #154)."""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid

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
    Reply,
)

# hc's states, in pstack's words: an agent that reports an error waits for the human, as a blocked one does.
STATES = {
    'idle': 'idle',
    'working': 'working',
    'done': 'done',
    'blocked': 'blocked',
    'error': 'blocked',
}
SETTLED = ('idle', 'done', 'blocked')
# The labels pstack keeps on a terminal: the agent's name and kind.
AGENT = 'pstack.agent'
KIND = 'pstack.kind'
# Polls of an unchanged screen after which an agent that reports no state counts as idle.
QUIET = 3
# A prompt's wait when the caller names none.
WAIT_MS = 30000
NOT_FOUND = 'agent_not_found'
START_FAILED = 'agent_start_failed'
NAME_TAKEN = 'agent_name_taken'


def run(args, stdout=PIPE, stderr=PIPE):
    """The one place hc is run: its CompletedProcess, each stream as the caller asked."""
    return subprocess.run(
        ['hc', *args], stdout=stdout, stderr=stderr, check=False, **TEXT
    )


def parse(reply):
    try:
        return json.loads(reply or '')
    except ValueError:
        return None


def field(doc, key, kind=str):
    value = doc.get(key) if isinstance(doc, dict) else None
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def kind_of(app):
    """pstack's kind for the app a terminal reports (`claude-code` is claude)."""
    return 'claude' if app == 'claude-code' else app


class Terminal:
    """One look at an hc terminal: its address, whether it is live, the labels pstack gave it and the status its program reports."""

    def __init__(self, doc):
        self.address = field(doc, 'address') or ''
        self.name = field(doc, 'name') or ''
        self.live = field(doc, 'state') == 'live'
        self.cwd = field(doc, 'cwd') or ''
        self.argv = [a for a in field(doc, 'argv', list) or [] if isinstance(a, str)]
        self.turn = field(doc, 'turn', int)
        labels = field(doc, 'labels', dict) or {}
        self.agent = field(labels, AGENT) or ''
        self.kind = field(labels, KIND) or ''
        status = field(doc, 'status', dict)
        # A terminal whose program never reported (a shell, an agent with no OSC 7501) has a null status.
        self.silent = status is None
        self.state = STATES.get(field(status, 'state') or '', '')
        self.app = kind_of(field(status, 'app') or '')

    @property
    def running(self):
        """An agent runs in it: pstack started one there, or its program reports a status."""
        return bool(self.kind) or not self.silent


class HerdrCore(Mux):
    program = 'hc'
    label = 'herdr-core'
    failure = 'hc_error'
    pane_var = 'HC_TERMINAL_ID'
    inside = 'HC_TERMINAL_ID set'
    tracks_sessions = False

    # The calling terminal.
    def require(self, what=None):
        if not os.environ.get('HC_TERMINAL_ID'):
            raise NotInMux(
                what or 'not running inside herdr-core (HC_TERMINAL_ID unset)'
            )

    def own_pane(self):
        return os.environ.get('HC_TERMINAL_ID', '')

    def location(self):
        """hc has no workspaces or tabs."""
        return '', ''

    def pane_env(self, pane, env):
        return [f'HC_TERMINAL_ID={pane}']

    def outside_env(self):
        return {'HC_TERMINAL_ID': ''}

    # Terminals.
    def get(self, pane):
        """The terminal `pane` names (an address or an hc name), or None when hc knows none."""
        r = run(['get', pane, '--json'])
        doc = parse(r.stdout) if r.returncode == 0 else None
        return Terminal(doc) if isinstance(doc, dict) else None

    def look(self, ref):
        """The live terminal of the agent pstack named `ref`, else the live terminal whose address or hc name is `ref`; None when neither."""
        listed = field(parse(run(['ls', '--json']).stdout), 'terminals', list) or []
        live = [Terminal(t) for t in listed if isinstance(t, dict)]
        live = [t for t in live if t.live]
        return next((t for t in live if t.agent == ref), None) or next(
            (t for t in live if ref in (t.address, t.name)), None
        )

    def target(self, name):
        """The address of the agent called `name`, or `name` itself when no live terminal goes by it."""
        found = self.look(name)
        return found.address if found else name

    def pane_exists(self, pane):
        found = self.get(pane)
        return found is not None and found.live

    def pane_close(self, pane):
        """Kills the terminal (one whose agent exited is finished already) and removes it; whether it was removed."""
        run(['kill', pane])
        return run(['rm', pane]).returncode == 0

    def pane_screen(self, pane, lines):
        r = run(['read', pane])
        return r.returncode, ''.join((r.stdout or '').splitlines(True)[-lines:])

    def pane_key(self, pane, key, stderr=PIPE):
        return run(['keys', pane, key], DEVNULL, stderr).returncode == 0

    def pane_size(self, pane):
        """A headless terminal is in no layout."""
        return None

    def made(self, cwd, env):
        """A new terminal running the login shell in cwd, carrying env: its address, or ''. hc wants a name; pstack never uses it."""
        shell = os.environ.get('SHELL') or '/bin/sh'
        argv = [*(['env', *env] if env else []), shell, '-l']
        r = run(
            [
                'run',
                '--bg',
                '--name',
                f'pstack-{uuid.uuid4().hex[:12]}',
                '--cwd',
                cwd,
                '--json',
                '--',
                *argv,
            ],
            stderr=None,
        )
        return (field(parse(r.stdout), 'address') or '') if r.returncode == 0 else ''

    def split(self, anchor, direction, cwd, env=()):
        return self.made(cwd, env)

    def tab(self, near, label, cwd, env=()):
        return self.made(cwd, env)

    def processes(self, pane):
        """hc reports no process facts of a terminal."""
        return None

    # Agents.
    def agent(self, name):
        found = self.look(name)
        if found is None or not found.running:
            return None
        if found.state:
            status = found.state
        elif found.silent:
            status = self.screen_status(found.address)
        else:
            status = 'unknown'
        return Agent(
            found.agent or found.address,
            found.kind or found.app,
            status,
            found.address,
            found.cwd,
            None,
        )

    def screen_status(self, pane):
        """An agent that reports no state, by two looks at its screen one poll apart: working when it changed, else idle (panes.agent_status's
        busy-screen check still applies)."""
        first = self.pane_screen(pane, 200)[1]
        time.sleep(env_float('PAIR_HC_POLL_S', 0.5))
        return 'working' if self.pane_screen(pane, 200)[1] != first else 'idle'

    def screen(self, name, lines):
        return self.pane_screen(self.target(name), lines)

    def key(self, name, key, stderr=DEVNULL):
        return self.pane_key(self.target(name), key, stderr)

    def held_elsewhere(self, name, pane):
        """The address of another live terminal whose agent is `name`, or '': Herdr's names are unique, so pstack's are too."""
        held = self.look(name)
        return (
            held.address if held and held.agent == name and held.address != pane else ''
        )

    def rename(self, target, name):
        """Names the agent in the terminal or of the name `target`; a name another live terminal's agent holds is not taken."""
        found = self.look(target)
        if found is None or self.held_elsewhere(name, found.address):
            return False
        return run(['label', found.address, f'{AGENT}={name}']).returncode == 0

    def answer(self, code, error='', status='', message='', stdout=PIPE, stderr=PIPE):
        """The Reply of an emulated call; its failure's text goes where the caller asked for stderr."""
        text = f'hc: {error}: {message}\n' if error else ''
        err = text if stderr == PIPE else None
        if stderr is None and text:
            sys.stderr.write(text)
        out = text if stderr == MERGED else ('' if stdout == PIPE else None)
        return Reply(code, out, err, error, status)

    def poll(self, pane, timeout_ms, done, screen=None, quiet_after_ms=0):
        """Looks at the terminal every PAIR_HC_POLL_S until done(terminal, status) holds or timeout_ms (None: no limit) passes. The status is
        the reported one, or, for an agent that has reported nothing for quiet_after_ms, read from its screen: working once it has changed,
        idle after QUIET looks in a row at an unchanged one, unknown before either, counted from `screen` when the caller read it before
        acting. (terminal, status, held) at the last look; terminal None when it is gone or finished."""
        limit = float('inf') if timeout_ms is None else float(timeout_ms) / 1000
        began = time.monotonic()
        quiet = 0
        while True:
            look = self.get(pane)
            if look is None or not look.live:
                return None, '', False
            status = look.state
            if not status:
                status = 'unknown'
                if look.silent and time.monotonic() - began >= quiet_after_ms / 1000:
                    now = self.pane_screen(pane, 200)[1]
                    changed = screen is not None and now != screen
                    quiet = quiet + 1 if screen is not None and not changed else 0
                    screen = now
                    status = (
                        'working'
                        if changed
                        else 'idle'
                        if quiet >= QUIET
                        else 'unknown'
                    )
            if done(look, status):
                return look, status, True
            if time.monotonic() - began >= limit:
                return look, status, False
            time.sleep(env_float('PAIR_HC_POLL_S', 0.5))

    def start(
        self, name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE
    ):
        """Types `exec` and the agent's command line into the terminal's shell, labels the terminal with the agent's name and kind, and waits
        for the agent to settle. A terminal where an agent runs already is busy; an agent that opens blocked (a dialog) is not ready; an agent
        that exits finishes its terminal. An agent counts only its own reports for the first PAIR_HC_STALL_S, while its shell may still start."""
        say = lambda code, error='', message='': self.answer(
            code, error, '', message, stdout, stderr
        )
        look = self.get(pane)
        if look is None or not look.live:
            return say(1, NOT_FOUND, f'no live terminal {pane}')
        if look.running:
            return say(1, PANE_BUSY, f'an agent runs in {pane}')
        holder = self.held_elsewhere(name, look.address)
        if holder:
            return say(1, NAME_TAKEN, f'a live agent in {holder} is {name}')
        line = f'exec {shlex.join([kind, *args])}'
        # zsh's spelling correction (CORRECT, CORRECT_ALL) would stop the line at a "correct X to Y?" question.
        if any(os.path.basename(a).lstrip('-') == 'zsh' for a in look.argv):
            line = f'nocorrect {line}'
        if run(['send', look.address, '--', line]).returncode != 0:
            return say(1, self.failure, f'cannot type into {pane}')
        for label in (f'{AGENT}={name}', f'{KIND}={kind}'):
            if run(['label', look.address, label]).returncode != 0:
                return say(1, self.failure, f'cannot label {pane}')
        stall = env_float('PAIR_HC_STALL_S', 15) * 1000
        last, status, held = self.poll(
            look.address,
            timeout_ms,
            lambda t, s: s in SETTLED,
            quiet_after_ms=min(stall, float(timeout_ms)),
        )
        if last is None:
            return say(1, START_FAILED, f'{kind} exited in {pane}')
        if not held:
            return say(1, TIMEOUT, f'{kind} did not settle in {timeout_ms}ms')
        # An agent that reports no state shows its folder-trust dialog only on screen.
        dialog = last.silent and TRUST_RE.search(
            re.sub(r'[\n\t ]+', ' ', self.pane_screen(look.address, 40)[1])
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
        """Sends `text` with `hc send` (no `--if idle`: see the module's note on #154). A turn counts as taken once hc's turn counter moves or
        the agent reports working or blocked, so a turn too quick to be seen working still counts."""
        if until and not wait:
            raise ValueError('prompt: until needs wait')
        say = lambda code, error='', status='', message='': self.answer(
            code, error, status, message, stdout, stderr
        )
        before = self.look(name)
        if before is None:
            return say(1, NOT_FOUND, message=f'no agent {name}')
        if not before.running:
            return say(1, NOT_READY, message=f'no agent runs in {before.address}')
        pane = before.address
        # The screen of an agent that reports no state, to tell when it takes the prompt.
        screen = self.pane_screen(pane, 200)[1] if before.silent else None
        if run(['send', pane, '--', text]).returncode != 0:
            return say(1, self.failure, message=f'cannot type into {pane}')
        if not wait:
            return say(0, status=before.state or 'idle')
        timeout_ms = WAIT_MS if timeout_ms is None else timeout_ms
        moved = lambda t: t.turn != before.turn
        if until:
            seen = lambda t, s: (
                s == until
                or (
                    until == 'working' and moved(t) and s in ('working', 'idle', 'done')
                )
            )
            last, _, held = self.poll(pane, timeout_ms, seen, screen)
            if last is None:
                return say(1, NOT_FOUND, message=f'{name} is gone')
            return (
                say(0, status=until)
                if held
                else say(1, TIMEOUT, '', f'{name} not {until} in {timeout_ms}ms')
            )
        stall = env_float('PAIR_HC_STALL_S', 15) * 1000
        took = lambda t, s: moved(t) or s in ('working', 'blocked')
        last, _, held = self.poll(pane, min(stall, float(timeout_ms)), took, screen)
        if last is None:
            return say(1, NOT_FOUND, message=f'{name} is gone')
        if not held:
            return say(1, STALLED, message=f'{name} did not take the prompt')
        last, status, held = self.poll(pane, timeout_ms, lambda t, s: s in SETTLED)
        if last is None:
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
        first = self.look(name)
        if first is None or not first.running:
            return say(1, NOT_FOUND, message=f'no agent {name}')
        target = until or SETTLED
        last, status, held = self.poll(
            first.address, timeout_ms, lambda t, s: s in target
        )
        if last is None:
            return say(1, NOT_FOUND, message=f'{name} is gone')
        return (
            say(0, status=status)
            if held
            else say(1, TIMEOUT, status, f'{name} still {status} after {timeout_ms}ms')
        )

    def notify(self, title, body):
        """hc shows no notification."""
        return False

    # What a message tells the human to run.
    def inspect_agent(self, name, lines=None):
        return f'hc read {self.target(name)}' + (f' | tail -n {lines}' if lines else '')

    def inspect_pane(self, pane):
        return f'hc read {pane}'

    def list_agents(self):
        return 'hc ls'

    def list_kinds(self):
        return 'command -v claude codex pi devin muse prime-agent amp'
