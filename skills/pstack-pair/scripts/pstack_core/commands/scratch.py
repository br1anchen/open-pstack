import os
import re
import shutil
import subprocess
import sys

from .. import git
from ..store import Die, basename, prog, load, names, pair_file, text_at, update


def parent_dir(path):
    """The directory holding path, ignoring a trailing slash: '.' for a bare name, '/' for a top-level one."""
    stripped = path.rstrip('/')
    return os.path.dirname(stripped) or ('/' if path.startswith('/') else '.')


def run_quiet(*argv):
    """A command with stdout and stderr dropped: its exit code."""
    return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode


def remove_tree(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def run(ctx, args):
    if len(args) < 2:
        raise Die(f'usage: {prog()} scratch <store> <id> [--at SHA] [--remove]')
    store, ident, rest = args[0], args[1], args[2:]
    remove, at = False, ''
    while rest:
        if rest[0] == '--remove':
            remove, rest = True, rest[1:]
        elif rest[0] == '--at':
            if len(rest) < 2:
                raise Die('--at needs a value')
            at, rest = rest[1], rest[2:]
        else:
            raise Die(f'unknown option {rest[0]}')
    pair_file(store)
    if not re.fullmatch(r'[0-9]{3}-[a-z0-9_-]+', ident):
        raise Die('scratch id must look like NNN-<slug>, e.g. NNN-<slug>-c<k> or NNN-<slug>-review')
    cwd = text_at(load(store), 'git_root')
    if not cwd:
        raise Die("the store's cwd is not inside a git repository")
    link = f'{store}/scratch/{ident}'
    common = git.run(cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.rstrip('\n')
    path = f'{common}/pstack-scratch/{basename(parent_dir(store))}-{basename(store)}/{ident}'
    if not os.path.islink(link) and os.path.isdir(link):
        path = link
    jj = os.path.isdir(f'{cwd}/.jj')
    if remove:
        if jj:
            run_quiet('jj', '-R', cwd, 'workspace', 'forget', f'scratch-{ident}')
            remove_tree(path)
        else:
            if not git.ok(cwd, 'worktree', 'remove', '--force', path):
                remove_tree(path)
            git.ok(cwd, 'worktree', 'prune')
        if os.path.islink(link):
            os.unlink(link)
        try:
            os.rmdir(parent_dir(path))
        except OSError:
            pass
        update(store, lambda doc: doc.update(scratch=[x for x in names(doc, 'scratch') if x != ident]))
        print(f'removed {path}')
        return 0
    if os.path.isdir(path):
        print(path)
        return 0
    try:
        os.makedirs(f'{store}/scratch', exist_ok=True)
        os.makedirs(parent_dir(path), exist_ok=True)
    except OSError as e:
        sys.stderr.write(f'{prog()}: cannot create {e.filename}: {os.strerror(e.errno)}\n')
        return 1
    if os.path.islink(link) or os.path.exists(link):
        os.unlink(link)
    os.symlink(path, link)
    if at and git.run(cwd, 'rev-parse', '--verify', '--quiet', f'{at}^{{commit}}', stdout=git.DEVNULL).returncode != 0:
        raise Die(f'not a commit: {at}')
    if jj:
        added = subprocess.run(['jj', '-R', cwd, 'workspace', 'add', '--name', f'scratch-{ident}', *(['-r', at] if at else []), path],
                               stdout=subprocess.DEVNULL, check=False)
        if added.returncode != 0:
            raise Die('jj workspace add failed', 2)
    elif git.run(cwd, 'worktree', 'add', '--detach', path, at or 'HEAD', stdout=git.DEVNULL).returncode != 0:
        raise Die('git worktree add failed', 2)
    if not at and not jj:
        diff = git.run(cwd, 'diff', 'HEAD', '--binary', binary=True).stdout
        if diff:
            if git.run(path, 'apply', '--index', '-', stdin=diff, stdout=None, quiet=True, binary=True).returncode != 0:
                sys.stderr.write('live diff did not apply cleanly; scratch is at HEAD only\n')
                git.ok(path, 'checkout', '--', '.')
        untracked = git.run(cwd, 'ls-files', '--others', '--exclude-standard', '-z', binary=True).stdout
        for raw in untracked.split(b'\0'):
            if raw:
                f = os.fsdecode(raw)
                os.makedirs(f'{path}/{parent_dir(f)}', exist_ok=True)
                shutil.copy2(f'{cwd}/{f}', f'{path}/{f}')
    update(store, lambda doc: doc.update(scratch=sorted({*names(doc, 'scratch'), ident})))
    print(path)
    return 0
