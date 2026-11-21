"""Every git call the cores make: `git -C root ...`, run once here with the stream and exit-code choices named."""

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
    **extra,
):
    """The CompletedProcess of `git -C root args` (no -C for a root of None). stdout is captured unless the caller sends it elsewhere; stderr is
    left on the terminal, dropped when quiet, or sent where the caller says. stdin and the captured output are text, or bytes with binary. `extra`
    holds only `timeout` (seconds; a call that outlives it is killed and reports TIMED_OUT) and `env`, which REPLACES git's whole environment, so
    a caller that adds to it merges os.environ itself."""
    if unknown := extra.keys() - {'timeout', 'env'}:
        raise TypeError(f'git.run got unexpected keyword arguments {sorted(unknown)}')
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
            **extra,
            **({} if binary else TEXT),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, TIMED_OUT, b'' if binary else '')


def ok(root, *args, stdout=DEVNULL, **extra):
    """Whether git succeeded, with stderr dropped and stdout dropped or sent to the open file the caller gives; timeout and env as in run."""
    return run(root, *args, quiet=True, stdout=stdout, **extra).returncode == 0


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


KEEP = 'refs/pstack/keep'


def pin(root, run, oid):
    """Keeps the commit `oid` (a full id) reachable under the immutable ref refs/pstack/keep/<run>/<oid>: created only, so a ref that holds the same
    id is left as it is and the evidence of an earlier head is never replaced by a later one. Whether the ref holds oid afterwards; a ref that cannot
    be made (a run name that is no ref, a commit that is not here) says so in one `warn:` line on stderr."""
    ref = f'{KEEP}/{run}/{oid}'
    if out(root, 'rev-parse', '--verify', '-q', ref) == oid:
        return True
    if ok(root, 'update-ref', ref, oid, ''):
        return True
    sys.stderr.write(f'warn: could not keep {oid} under {ref}\n')
    return False


def toplevel(path=None):
    """The top of the repository that holds path (the current directory without one), or '' outside a repository or without git."""
    try:
        return out(path, 'rev-parse', '--show-toplevel') or ''
    except OSError:
        return ''
