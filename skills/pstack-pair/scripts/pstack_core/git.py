"""Every git call the cores make: `git -C root ...`, run once here with the stream and exit-code choices named."""

import os
import subprocess
import sys

from .store import TEXT

DEVNULL, PIPE = subprocess.DEVNULL, subprocess.PIPE
TIMED_OUT = (
    124  # the exit status of a call that outlived its timeout, as timeout(1) reports it
)


def run(
    root,
    *args,
    quiet=False,
    stdin=None,
    stdout=PIPE,
    stderr=None,
    binary=False,
    timeout=None,
    env=None,
):
    """The CompletedProcess of `git -C root args` (no -C for a root of None). stdout is captured unless the caller sends it elsewhere; stderr is
    left on the terminal, dropped when quiet, or sent where the caller says. stdin and the captured output are text, or bytes with binary. A
    call that outlives `timeout` seconds is killed and reports TIMED_OUT; `env` adds to the environment git runs in."""
    sys.stdout.flush()
    sys.stderr.flush()
    argv = ['git', *(['-C', root] if root is not None else []), *args]
    try:
        return subprocess.run(
            argv,
            input=stdin,
            stdout=stdout,
            stderr=DEVNULL if quiet else stderr,
            check=False,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
            **({} if binary else TEXT),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, TIMED_OUT, b'' if binary else '')


def ok(root, *args, stdout=DEVNULL, timeout=None, env=None):
    """Whether git succeeded, with stderr dropped and stdout dropped or sent to the open file the caller gives; timeout and env as in run."""
    return (
        run(root, *args, quiet=True, stdout=stdout, timeout=timeout, env=env).returncode
        == 0
    )


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
