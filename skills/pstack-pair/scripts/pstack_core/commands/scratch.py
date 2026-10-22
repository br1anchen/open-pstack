import os
import re
import shutil
import subprocess
import sys

from ..store import Die, basename, prog, TEXT, load, names, pair_file, text_at, update


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
    common = subprocess.run(['git', '-C', cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            stdout=subprocess.PIPE, check=False, **TEXT).stdout.rstrip('\n')
    path = f'{common}/pstack-scratch/{basename(os.path.dirname(store))}-{basename(store)}/{ident}'
    if not os.path.islink(link) and os.path.isdir(link):
        path = link
    jj = os.path.isdir(f'{cwd}/.jj')
    if remove:
        if jj:
            run_quiet('jj', '-R', cwd, 'workspace', 'forget', f'scratch-{ident}')
            remove_tree(path)
        else:
            if run_quiet('git', '-C', cwd, 'worktree', 'remove', '--force', path) != 0:
                remove_tree(path)
            run_quiet('git', '-C', cwd, 'worktree', 'prune')
        if os.path.islink(link):
            os.unlink(link)
        try:
            os.rmdir(os.path.dirname(path))
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
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as e:
        sys.stderr.write(f'{prog()}: cannot create {e.filename}: {os.strerror(e.errno)}\n')
        return 1
    if os.path.islink(link) or os.path.exists(link):
        os.unlink(link)
    os.symlink(path, link)
    devnull = subprocess.DEVNULL
    if at:
        if subprocess.run(['git', '-C', cwd, 'rev-parse', '--verify', '--quiet', f'{at}^{{commit}}'], stdout=devnull, check=False).returncode != 0:
            raise Die(f'not a commit: {at}')
        if jj:
            argv, failure = ['jj', '-R', cwd, 'workspace', 'add', '--name', f'scratch-{ident}', '-r', at, path], 'jj workspace add failed'
        else:
            argv, failure = ['git', '-C', cwd, 'worktree', 'add', '--detach', path, at], 'git worktree add failed'
        if subprocess.run(argv, stdout=devnull, check=False).returncode != 0:
            raise Die(failure, 2)
    elif jj:
        if subprocess.run(['jj', '-R', cwd, 'workspace', 'add', '--name', f'scratch-{ident}', path], stdout=devnull, check=False).returncode != 0:
            raise Die('jj workspace add failed', 2)
    else:
        if subprocess.run(['git', '-C', cwd, 'worktree', 'add', '--detach', path, 'HEAD'], stdout=devnull, check=False).returncode != 0:
            raise Die('git worktree add failed', 2)
        diff = subprocess.run(['git', '-C', cwd, 'diff', 'HEAD', '--binary'], stdout=subprocess.PIPE, check=False).stdout
        if diff:
            applied = subprocess.run(['git', '-C', path, 'apply', '--index', '-'], input=diff, stderr=devnull, check=False)
            if applied.returncode != 0:
                sys.stderr.write('live diff did not apply cleanly; scratch is at HEAD only\n')
                run_quiet('git', '-C', path, 'checkout', '--', '.')
        untracked = subprocess.run(['git', '-C', cwd, 'ls-files', '--others', '--exclude-standard', '-z'], stdout=subprocess.PIPE, check=False).stdout
        for raw in untracked.split(b'\0'):
            if raw:
                f = os.fsdecode(raw)
                os.makedirs(f'{path}/{os.path.dirname(f)}', exist_ok=True)
                shutil.copy2(f'{cwd}/{f}', f'{path}/{f}')
    update(store, lambda doc: doc.update(scratch=sorted({*names(doc, 'scratch'), ident})))
    print(path)
    return 0
