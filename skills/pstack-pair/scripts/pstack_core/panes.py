"""Panes for the roles: where a new one goes, the folder-trust dialog that stops an agent at startup, an agent's status as the roles read it,
and the wait for a file an agent writes. The multiplexer underneath is mux.get()."""

import json
import os
import re
import time

from . import mux
from .errors import NoPaneId, PaneSizeUnreadable, TrustPrompt
from .mux import TRUST_RE
from .numbers import env_int
from .store import load, peek, text_at


def require_mux(what=None):
    """Ends with the caller's message (by default, that the command must run inside the multiplexer) when not inside it."""
    mux.get().require(what)


def pick_direction(pane):
    """Cells are about twice as tall as wide: a pane is visually wider than tall when width >= 2 * height. Split wide panes right and tall
    panes down."""
    size = mux.get().pane_size(pane)
    if size is None:
        return 'right'
    width, height = size
    if not (
        isinstance(width, int)
        and not isinstance(width, bool)
        and isinstance(height, int)
        and not isinstance(height, bool)
    ):
        raise PaneSizeUnreadable(
            f'pane {pane} has a size that is not a number: {width} x {height}'
        )
    return 'right' if width >= 2 * height else 'down'


def new_pane(store, role, anchor, direction, cwd, env=()):
    """A new pane for a role: a split beside the anchor, or, with placement tab (spawn --tab, or PSTACK_PLACEMENT=tab), a tab of its own in the
    master's workspace, labelled with the role; each KEY=VALUE of env is an --env for the launched process. Prints where it went and returns its id."""
    pair = load(store)
    slug = text_at(pair, 'slug')
    if pair.get('placement') == 'tab':
        master = text_at(peek(pair, 'master'), 'pane_id')
        pane = mux.get().tab(master, f'{slug}-{role}', cwd, env)
        if not pane:
            raise NoPaneId('tab create returned no pane id')
        print(f'tab {slug}-{role} -> {pane}')
        return pane
    direction = direction or pick_direction(anchor)
    pane = mux.get().split(anchor, direction, cwd, env)
    if not pane:
        raise NoPaneId('pane split returned no pane id')
    print(f'split {anchor} {direction} -> {pane}')
    return pane


def refuse_untrusted(pane, kind, directory, close=False, then=None):
    """A dialog asking to trust the working folder (Claude, Codex) blocks an agent at startup. Trusting a repo is the human's decision, never the
    script's: decline it, which returns the pane to its shell, and stop with what to do. Claude's dialog defaults to "No, exit", so an Enter
    would quit. `then` is the advice after the refusal; by default, trust the folder and rerun the spawn."""
    m = mux.get()
    if not TRUST_RE.search(re.sub(r'[\n\t ]+', ' ', m.pane_screen(pane, 40)[1])):
        return
    m.pane_key(pane, 'esc')
    # A pane split for this start holds nothing else; a retry opens its own.
    if close:
        m.pane_close(pane)
    raise TrustPrompt(
        f'{kind} asks whether to trust {directory}, and that decision is yours: '
        + (
            then
            or f'open {kind} there once and trust the folder (or pick another kind), then rerun the spawn'
        )
    )


def await_file(path, timeout_ms):
    """Poll for a file every PAIR_FILE_POLL_MS (5000 by default) until it exists or timeout_ms of polling has passed."""
    if not mux.get().waits:
        return os.path.isfile(path)
    poll = env_int('PAIR_FILE_POLL_MS', 5000)
    waited = 0
    while not os.path.isfile(path) and waited < timeout_ms:
        time.sleep(poll / 1000)
        waited += poll
    return os.path.isfile(path)


BUSY = re.compile(
    r'esc (twice )?to interrupt|guide devin while it works|send queued messages now|↵ send now|ctrl\+enter to send now',
    re.IGNORECASE,
)


def pane_text(name):
    """The pane's visible screen as one line."""
    return re.sub(
        r'[\n\t ]+',
        ' ',
        mux.get().screen(name, 40)[1],
    )


def pane_busy(name):
    return BUSY.search(pane_text(name)) is not None


def parse(reply):
    """The one JSON document a herdr or daemon reply holds, or None when it is not JSON."""
    try:
        return json.loads(reply)
    except ValueError:
        return None


def agent_status(name):
    """idle|working|blocked|done|unknown, or absent when the multiplexer has no such agent. An idle or done agent whose screen shows it busy is
    working."""
    agent = mux.get().agent(name)
    if agent is None:
        return 'absent'
    state = agent.status or 'unknown'
    if state in ('idle', 'done') and pane_busy(name):
        return 'working'
    return state


def agent_kind(name):
    """The kind of the agent hosting name (a name or pane id), or empty."""
    agent = mux.get().agent(name)
    return agent.kind if agent else ''


def agent_session(agent):
    """The value of the session the multiplexer tracks for an Agent (None for none), '' when it tracks none."""
    session = agent.session if agent else None
    value = session.get('value') if isinstance(session, dict) else None
    return value if isinstance(value, str) else ''
