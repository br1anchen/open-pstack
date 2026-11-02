"""The multiplexer this process runs under, chosen once: the rest of pstack asks `mux.get()` and names no backend."""

from .base import (
    DEVNULL,
    MERGED,
    NOT_READY,
    PANE_BUSY,
    PIPE,
    STALLED,
    TIMEOUT,
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
    'Agent',
    'Mux',
    'Process',
    'Reply',
    'get',
]

_current = None


def get():
    """The backend of this process."""
    global _current
    if _current is None:
        from .herdr import Herdr

        _current = Herdr()
    return _current
