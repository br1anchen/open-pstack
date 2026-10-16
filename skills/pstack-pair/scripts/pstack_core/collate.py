import _locale
import os

_key = None


def collate_key():
    """A sort key that orders names as the shell's globs, ls and sort do under the user's LC_COLLATE."""
    global _key
    if _key is None:
        try:
            _locale.setlocale(_locale.LC_COLLATE, '')
        except _locale.Error:
            pass
        _key = _locale.strxfrm
    return _key


def glob_sorted(paths):
    return sorted(paths, key=collate_key())


def newest(paths):
    """ls -t | head -1: newest first, ties by collated name; None when there are none."""
    key = collate_key()
    return min(paths, key=lambda p: (-os.stat(p).st_mtime_ns, key(os.path.basename(p))), default=None)
