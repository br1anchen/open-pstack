"""The multiplexer this process runs under, chosen once: the rest of pstack asks `mux.get()` and names no backend. PSTACK_MUX (herdr or
rex) picks one; otherwise Herdr when HERDR_ENV=1, Rex when REX_BLOCK is set (Herdr wins inside both), and Herdr, which refuses, inside
neither."""

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
    'choose',
    'get',
]

_current = None


def get():
    """The backend of this process."""
    global _current
    if _current is None:
        _current = choose(os.environ)
    return _current


def choose(env):
    """The backend env selects."""
    want = env.get('PSTACK_MUX', '')
    if want not in ('', 'herdr', 'rex'):
        from ..errors import PairError

        raise PairError(f'PSTACK_MUX must be herdr or rex, not {want}')
    if want == 'rex' or (
        not want and env.get('HERDR_ENV') != '1' and env.get('REX_BLOCK')
    ):
        from .rex import Rex

        return Rex()
    from .herdr import Herdr

    return Herdr()
