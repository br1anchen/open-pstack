"""The terminal multiplexer the roles run in, as one interface: panes to split, read, type into and close, and the agents started in them, each by
name, with a status. A backend implements it on its own program; nothing outside mux/ names one."""

import re
import subprocess
from dataclasses import dataclass

# Where a call's output goes: captured (PIPE), dropped (DEVNULL), stderr merged into stdout (MERGED) or left on the terminal (None).
DEVNULL, PIPE, MERGED = subprocess.DEVNULL, subprocess.PIPE, subprocess.STDOUT

# An agent's folder-trust dialog, as its screen shows it (whitespace folded to single spaces).
TRUST_RE = re.compile(
    'trust this folder|project you created or one you trust|do you trust the (files|contents)',
    re.IGNORECASE,
)

# pstack's words for why an agent call failed; a backend names its own failures with these.
TIMEOUT = 'timeout'
STALLED = 'agent_prompt_stalled'
PANE_BUSY = 'agent_pane_busy'
NOT_READY = 'agent_not_ready'


@dataclass
class Reply:
    """One call's outcome: its exit status, its stdout and stderr as captured (None when not captured), `error` (one of the words above, the
    backend's own code, or its `failure` word when it named none; '' on success) and `status`, the agent status the reply carries ('' when none)."""

    code: int
    out: str | None = ''
    err: str | None = ''
    error: str = ''
    status: str = ''

    @property
    def ok(self):
        return self.code == 0


@dataclass(frozen=True)
class Agent:
    """An agent the multiplexer knows by name: its kind, status (idle, working, blocked, done or unknown), the pane it runs in, its working
    directory, and its session ({'kind': 'path' or 'id', 'value': ...}, or None) as the backend tracks them. Missing facts are ''."""

    name: str
    kind: str
    status: str
    pane: str
    cwd: str
    session: dict | None


@dataclass(frozen=True)
class Process:
    """The processes of a pane: its shell's pid, the foreground process group, and the pids in the foreground, as the backend reports them
    (unchecked); a fact the backend lacks is None."""

    shell_pid: object
    foreground_group: object
    foreground_pids: tuple | None


class Mux:
    """What pstack asks of a multiplexer. `program` is the command on PATH, `label` its name in prose, `failure` the error word of a call
    that failed without naming why `pane_var` the variable that names the calling pane and `inside`
    how a process tells it runs inside the multiplexer. Every method makes the same backend calls each time, in the same order."""

    program = ''
    label = ''
    failure = ''
    pane_var = ''
    inside = ''
    # Whether Agent.session is ever set: a Prime role is cleaned up through the daemon session it names, so it needs a backend that tracks one.
    tracks_sessions = True

    # The calling pane and where it lives.
    def require(self, what=None):
        """Ends with the caller's message (by default, that the command must run inside the multiplexer) when not inside it."""
        raise NotImplementedError

    def own_pane(self):
        """The pane this process runs in, '' when unknown."""
        raise NotImplementedError

    def location(self):
        """(workspace, tab) ids of the calling pane, '' when unknown."""
        raise NotImplementedError

    def pane_env(self, pane, env):
        """The KEY=VALUE lines every process started in the pane carries, given the caller's environment: how /proc tells the pane's work."""
        raise NotImplementedError

    def outside_env(self):
        """The variables that tell a program started from here it runs outside the multiplexer (a one-shot agent preflight, whose agent
        would otherwise report its state to it)."""
        raise NotImplementedError

    # Panes.
    def pane_exists(self, pane):
        raise NotImplementedError

    def pane_close(self, pane):
        """Closes the pane; whether it closed."""
        raise NotImplementedError

    def pane_screen(self, pane, lines):
        """(exit code, text) of the pane's last `lines` visible lines."""
        raise NotImplementedError

    def pane_key(self, pane, key):
        """Presses one key (enter, esc, ctrl+d, ...) in the pane; whether it was sent."""
        raise NotImplementedError

    def pane_size(self, pane):
        """(width, height) in cells as the backend reports them (not checked), or None when the pane is not in the layout."""
        raise NotImplementedError

    def split(self, anchor, direction, cwd, env=()):
        """A new pane beside the anchor (direction right or down), not focused, running a shell in cwd with each KEY=VALUE of env; its id, or ''."""
        raise NotImplementedError

    def tab(self, near, label, cwd, env=()):
        """A new tab labelled `label` beside the pane `near`, not focused; the id of its pane, or ''."""
        raise NotImplementedError

    def processes(self, pane):
        """The pane's Process, or None when the backend cannot say."""
        raise NotImplementedError

    # Agents.
    def agent(self, name):
        """The Agent called `name` (a name or a pane id), or None when there is none."""
        raise NotImplementedError

    def screen(self, name, lines):
        """(exit code, text) of the agent's last `lines` visible lines."""
        raise NotImplementedError

    def key(self, name, key, stderr=DEVNULL):
        """Presses one key in the agent's pane; whether it was sent."""
        raise NotImplementedError

    def rename(self, target, name):
        """Names the agent in the pane or of the name `target`; whether it took."""
        raise NotImplementedError

    def start(
        self, name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE
    ):
        """Starts `args` as an agent of `kind` called `name` in the pane and waits up to timeout_ms for it to be ready. A pane still settling
        (PANE_BUSY) is tried again a few times. The Reply of the last try."""
        raise NotImplementedError

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
        """Types `text` into the agent and submits it; with wait, waits (until a state) up to timeout_ms for its turn to end."""
        raise NotImplementedError

    def wait(self, name, until=(), timeout_ms=None, stdout=PIPE, stderr=PIPE):
        """Waits up to timeout_ms for the agent to be in one of `until` (by default, for its turn to end: idle, done or blocked). Its Reply
        carries the status it ended in."""
        raise NotImplementedError

    def notify(self, title, body):
        """Shows the human a notification; whether it was shown."""
        raise NotImplementedError

    # What a message tells the human to run.
    def inspect_agent(self, name, lines=None):
        raise NotImplementedError

    def inspect_pane(self, pane):
        raise NotImplementedError

    def list_agents(self):
        """The command that lists each agent's pane."""
        raise NotImplementedError

    def list_kinds(self):
        """The command that lists the agent kinds it can start."""
        raise NotImplementedError
