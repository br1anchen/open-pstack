import os
import re
import shutil
import subprocess
import sys

from .. import git
from ..argv import Spec, read
from ..errors import BadScratch, Die, FileFailed, ScratchNotMade
from ..store import basename, load, names, pair_file, text_at, update


def parent_dir(path):
    """The directory holding path, ignoring a trailing slash: '.' for a bare name, '/' for a top-level one."""
    stripped = path.rstrip('/')
    return os.path.dirname(stripped) or ('/' if path.startswith('/') else '.')


def run_quiet(*argv):
    """A command with stdout and stderr dropped: its exit code."""
    return subprocess.run(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    ).returncode


def remove_tree(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def drop_worktree(cwd, path):
    """Unregisters the git worktree at `path` and deletes the directory, which is removed by hand when git does not know it."""
    if not git.ok(cwd, 'worktree', 'remove', '--force', path):
        remove_tree(path)
    git.ok(cwd, 'worktree', 'prune')


def locate(store, ident):
    """(cwd, link, path) of the scratch `ident` for a store: the repository the store works in, the link under the store and the worktree it names.
    The store must exist, the id must look like NNN-<slug> and the store's cwd must be a git root."""
    pair_file(store)
    if not re.fullmatch(r'[0-9]{3}-[a-z0-9_-]+', ident):
        raise BadScratch(
            'scratch id must look like NNN-<slug>, e.g. NNN-<slug>-c<k> or NNN-<slug>-review'
        )
    cwd = text_at(load(store), 'git_root')
    if not cwd:
        raise BadScratch("the store's cwd is not inside a git repository")
    link = f'{store}/scratch/{ident}'
    found = git.run(
        cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir', stderr=git.PIPE
    )
    sys.stderr.write(found.stderr)
    common = found.stdout.rstrip('\n')
    path = f'{common}/pstack-scratch/{basename(parent_dir(store))}-{basename(store)}/{ident}'
    if not os.path.islink(link) and os.path.isdir(link):
        path = link
    return cwd, link, path


def remove_scratch(ctx, store, ident):
    """Removes the scratch worktree (or jj workspace) `ident` and its record in the store; returns the path it was at."""
    cwd, link, path = locate(store, ident)
    if os.path.isdir(f'{cwd}/.jj'):
        run_quiet('jj', '-R', cwd, 'workspace', 'forget', f'scratch-{ident}')
    drop_worktree(cwd, path)
    if os.path.islink(link):
        os.unlink(link)
    try:
        os.rmdir(parent_dir(path))
    except OSError:
        pass
    update(
        store,
        lambda doc: doc.update(
            scratch=[x for x in names(doc, 'scratch') if x != ident]
        ),
    )
    return path


def copy_live_changes(cwd, path):
    """Brings the uncommitted work of `cwd` into the new git worktree at `path`: the tracked diff applied to the index, then each untracked file copied.
    A diff that does not apply is said on stderr and leaves the worktree at HEAD."""
    diff = git.run(cwd, 'diff', 'HEAD', '--binary', binary=True).stdout
    if diff:
        if (
            git.run(
                path,
                'apply',
                '--index',
                '-',
                stdin=diff,
                stdout=None,
                quiet=True,
                binary=True,
            ).returncode
            != 0
        ):
            sys.stderr.write(
                'live diff did not apply cleanly; scratch is at HEAD only\n'
            )
            git.ok(path, 'checkout', '--', '.')
    untracked = git.run(
        cwd, 'ls-files', '--others', '--exclude-standard', '-z', binary=True
    ).stdout
    for raw in untracked.split(b'\0'):
        if raw:
            f = os.fsdecode(raw)
            os.makedirs(f'{path}/{parent_dir(f)}', exist_ok=True)
            shutil.copy2(f'{cwd}/{f}', f'{path}/{f}')


def add_jj_scratch(cwd, ident, path, at):
    """A jj workspace that is also a linked git worktree at the same commit. A jj workspace alone has no .git, and git finds only the
    git directory under .git/, so every git, flake and gate command there fails. The worktree is added first with no checkout, its .git
    link set aside while jj fills the empty directory, then put back and its index read from HEAD."""
    commit = at or git.out(cwd, 'rev-parse', 'HEAD') or 'HEAD'
    if not git.ok(cwd, 'worktree', 'add', '--no-checkout', '--detach', path, commit):
        raise ScratchNotMade('git worktree add failed')
    link = f'{path}.gitlink'
    os.replace(f'{path}/.git', link)
    added = subprocess.run(
        [
            'jj',
            '-R',
            cwd,
            'workspace',
            'add',
            '--name',
            f'scratch-{ident}',
            *(['-r', at] if at else []),
            path,
        ],
        stdout=subprocess.DEVNULL,
        check=False,
    )
    if added.returncode != 0:
        os.unlink(link)
        drop_worktree(cwd, path)
        raise ScratchNotMade('jj workspace add failed')
    os.replace(link, f'{path}/.git')
    ignore = f'{path}/.jj/.gitignore'
    if not os.path.exists(ignore):
        with open(ignore, 'w') as f:
            f.write('/*\n')
    top = git.out(path, 'rev-parse', '--show-toplevel')
    failure = None
    if not git.ok(path, 'reset', '-q'):
        failure = f'git reset in {path} failed'
    elif top is None or os.path.realpath(top) != os.path.realpath(path):
        failure = f'scratch {path} is not a git work tree'
    if failure:
        run_quiet('jj', '-R', cwd, 'workspace', 'forget', f'scratch-{ident}')
        drop_worktree(cwd, path)
        raise ScratchNotMade(failure)


def make_scratch(ctx, store, ident, *, at=''):
    """The path of the scratch worktree `ident`, made at the commit `at` (HEAD with the live diff when empty) unless it is there already. A
    live diff that does not apply is said on stderr and leaves the scratch at HEAD."""
    cwd, link, path = locate(store, ident)
    if os.path.isdir(path):
        return path
    try:
        os.makedirs(f'{store}/scratch', exist_ok=True)
        os.makedirs(parent_dir(path), exist_ok=True)
    except OSError as e:
        raise FileFailed('create', e.filename, e) from None
    if os.path.islink(link) or os.path.exists(link):
        os.unlink(link)
    os.symlink(path, link)
    if (
        at
        and git.run(
            cwd,
            'rev-parse',
            '--verify',
            '--quiet',
            f'{at}^{{commit}}',
            stdout=git.DEVNULL,
        ).returncode
        != 0
    ):
        raise BadScratch(f'not a commit: {at}')
    jj = os.path.isdir(f'{cwd}/.jj')
    if jj:
        add_jj_scratch(cwd, ident, path, at)
    elif (
        git.run(
            cwd, 'worktree', 'add', '--detach', path, at or 'HEAD', stdout=git.DEVNULL
        ).returncode
        != 0
    ):
        raise ScratchNotMade('git worktree add failed')
    if not at and not jj:
        copy_live_changes(cwd, path)
    update(
        store, lambda doc: doc.update(scratch=sorted({*names(doc, 'scratch'), ident}))
    )
    return path


def run(ctx, args):
    if len(args) < 2:
        raise Die(f'usage: {ctx.prog} scratch <store> <id> [--at SHA] [--remove]')
    store, ident = args[0], args[1]
    opts = read(args[2:], Spec(valued=('--at',), flags=('--remove',)))
    if opts.present('--remove'):
        print(f'removed {remove_scratch(ctx, store, ident)}')
    else:
        print(make_scratch(ctx, store, ident, at=opts.last('--at', '')))
    return 0
