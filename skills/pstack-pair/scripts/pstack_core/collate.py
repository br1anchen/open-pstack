import _locale

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
