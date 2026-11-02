"""The multiplexer this process runs under, chosen once: the rest of pstack asks `mux.get()` and names no backend. PSTACK_MUX (herdr, rex
or subagents) picks one; otherwise Herdr when HERDR_ENV=1, Rex when REX_BLOCK is set (Herdr wins inside both), and inside neither the
single-session subagents when the skill allows them (allow_subagents), else Herdr, which refuses."""

import os

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

__all__ = [
    'DEVNULL',
    'MERGED',
    'NOT_READY',
    'PANE_BUSY',
    'PIPE',
    'STALLED',
    'TIMEOUT',
    'TRUST_RE',
    'Agent',
    'Mux',
    'Process',
    'Reply',
    'allow_subagents',
    'choose',
    'get',
]

_current = None
_subagents = False


def allow_subagents(allowed=True):
    """Lets a skill whose roles can run as subagents of one session (the kitchen) fall back to them when no multiplexer is found."""
    global _subagents, _current
    _subagents, _current = allowed, None


def get():
    """The backend of this process."""
    global _current
    if _current is None:
        _current = choose(os.environ, _subagents)
    return _current


def choose(env, subagents=False):
    """The backend env selects; `subagents` allows the single-session fallback."""
    want = env.get('PSTACK_MUX', '')
    if want not in ('', 'herdr', 'rex', 'subagents'):
        from ..errors import PairError

        raise PairError(f'PSTACK_MUX must be herdr, rex or subagents, not {want}')
    found = env.get('HERDR_ENV') == '1' or env.get('REX_BLOCK')
    if want == 'subagents' or (not want and not found and subagents):
        from .subagents import Subagents

        return Subagents()
    if want == 'rex' or (
        not want and env.get('HERDR_ENV') != '1' and env.get('REX_BLOCK')
    ):
        from .rex import Rex

        return Rex()
    from .herdr import Herdr

    return Herdr()
