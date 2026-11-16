"""The multiplexer this process runs under, chosen once: the rest of pstack asks `mux.get()` and names no backend. PSTACK_MUX (herdr, rex,
herdr-core or subagents) picks one; otherwise Herdr when HERDR_ENV=1, Rex when REX_BLOCK is set, herdr-core when HC_TERMINAL_ID is set (in
that order, so a nest of them picks as it did before herdr-core), and inside none the single-session subagents when the skill allows them
(allow_subagents), else Herdr, which refuses."""

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
    if want not in ('', 'herdr', 'rex', 'herdr-core', 'subagents'):
        from ..errors import PairError

        raise PairError(
            f'PSTACK_MUX must be herdr, rex, herdr-core or subagents, not {want}'
        )
    herdr = env.get('HERDR_ENV') == '1'
    found = herdr or env.get('REX_BLOCK') or env.get('HC_TERMINAL_ID')
    if want == 'subagents' or (not want and not found and subagents):
        from .subagents import Subagents

        return Subagents()
    if want == 'rex' or (not want and not herdr and env.get('REX_BLOCK')):
        from .rex import Rex

        return Rex()
    if want == 'herdr-core' or (
        not want
        and not herdr
        and not env.get('REX_BLOCK')
        and env.get('HC_TERMINAL_ID')
    ):
        from .herdr_core import HerdrCore

        return HerdrCore()
    from .herdr import Herdr

    return Herdr()
