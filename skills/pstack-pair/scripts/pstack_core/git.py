"""Every git call the cores make: `git -C root ...`, run once here with the stream and exit-code choices named."""

import subprocess
import sys

from .store import TEXT

DEVNULL, PIPE = subprocess.DEVNULL, subprocess.PIPE


def run(root, *args, quiet=False, stdin=None, stdout=PIPE, stderr=None, binary=False):
    """The CompletedProcess of `git -C root args` (no -C for a root of None). stdout is captured unless the caller sends it elsewhere; stderr is
    left on the terminal, dropped when quiet, or sent where the caller says. stdin and the captured output are text, or bytes with binary."""
    sys.stdout.flush()
    sys.stderr.flush()
    argv = ['git', *(['-C', root] if root is not None else []), *args]
    return subprocess.run(
        argv,
        input=stdin,
        stdout=stdout,
        stderr=DEVNULL if quiet else stderr,
        check=False,
        **({} if binary else TEXT),
    )


def ok(root, *args, stdout=DEVNULL):
    """Whether git succeeded, with stderr dropped and stdout dropped or sent to the open file the caller gives."""
    return run(root, *args, quiet=True, stdout=stdout).returncode == 0


def out(root, *args):
    """stdout of `git -C root args` without its trailing newlines, or None when git fails; stderr is dropped."""
    r = run(root, *args, quiet=True)
    return r.stdout.rstrip('\n') if r.returncode == 0 else None


def range_commits(root, base, tip):
    """(full sha, subject) of each non-merge commit in base..tip, oldest first, or None when git cannot list the range."""
    text = out(
        root, 'log', '--reverse', '--no-merges', '--format=%H%x09%s', f'{base}..{tip}'
    )
    if text is None:
        return None
    return [tuple(line.split('\t', 1)) for line in text.splitlines() if '\t' in line]


def toplevel(path=None):
    """The top of the repository that holds path (the current directory without one), or '' outside a repository or without git."""
    try:
        return out(path, 'rev-parse', '--show-toplevel') or ''
    except OSError:
        return ''
