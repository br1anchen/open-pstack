import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(os.environ['PROBE_ROOT'])
sys.path.insert(0, str(ROOT / 'skills/pstack-pair/scripts'))
from pstack_core import bundle


def git(repo, *args, stdin=None):
    result = subprocess.run(
        ['git', '-c', 'user.name=probe', '-c', 'user.email=probe@example.invalid',
         '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
        cwd=repo, input=stdin, text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def commit(repo, path, text, message):
    (repo / path).write_text(text)
    git(repo, 'add', path)
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD')


def patch_id(repo, commit):
    patch = git(repo, 'diff-tree', '-p', '--binary', '--full-index', commit)
    return git(repo, 'patch-id', '--stable', stdin=patch + '\n').split()[0]


with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    base = commit(repo, 'f.txt', 'zero\n', 'base')
    git(repo, 'switch', '-qc', 'old')
    old = commit(repo, 'f.txt', 'one\n', 'wanted change')
    git(repo, 'switch', '-qc', 'incoming', base)
    commit(repo, 'upstream.txt', 'upstream\n', 'upstream')
    git(repo, 'cherry-pick', old)
    carried = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'revert', '--no-edit', carried)
    incoming = git(repo, 'rev-parse', 'HEAD')
    common = git(repo, 'merge-base', old, incoming)
    old_ids = {patch_id(repo, c) for c in git(repo, 'rev-list', f'{common}..{old}').split()}
    new_ids = {patch_id(repo, c) for c in git(repo, 'rev-list', f'{common}..{incoming}').split()}
    assert old_ids <= new_ids
    assert git(repo, 'show', f'{old}:f.txt') == 'one'
    assert git(repo, 'show', f'{incoming}:f.txt') == 'zero'
    print('PATCH CONTAINMENT: every old patch matches incoming history, but its final tree reverted the wanted change')

    git(repo, 'switch', '-qc', 'whitespace-old', base)
    whitespace_old = commit(repo, 'program.py', 'if True:\n    print(1)\n', 'indented')
    git(repo, 'switch', '-qc', 'whitespace-new', base)
    whitespace_new = commit(repo, 'program.py', 'if True:\nprint(1)\n', 'unindented')
    assert patch_id(repo, whitespace_old) == patch_id(repo, whitespace_new)
    assert git(repo, 'rev-parse', f'{whitespace_old}^{{tree}}') != git(repo, 'rev-parse', f'{whitespace_new}^{{tree}}')
    print('PATCH EQUALITY: different Python indentation and different trees have the same stable patch ID')

    git(repo, 'switch', '-q', 'main')
    store = Path(tmp) / 'store'
    store.mkdir()
    (store / 'pair.json').write_text(json.dumps({'cwd': str(repo), 'git_root': str(repo)}))
    path = Path(tmp) / 'handoff.tgz'
    manifest = bundle.pack(str(store), str(path), {'thread': 'T-master', 'name': 'demo-master'}, {'name': 'demo-master-g2', 'pane': 'amp:2'}, {})
    manifest, _, gitdata = bundle.read_bundle(path)
    assert gitdata is not None
    receiver = Path(tmp) / 'receiver'
    receiver.mkdir()
    git(receiver, 'init', '-q')
    packed = Path(tmp) / 'git.bundle'
    packed.write_bytes(gitdata)
    git(receiver, 'fetch', '-q', str(packed), '+refs/pstack/out/*:refs/pstack/in/probe/*')
    exists = subprocess.run(['git', 'cat-file', '-e', f'{old}^{{commit}}'], cwd=receiver, capture_output=True).returncode == 0
    assert not exists
    print('CURRENT HANDOFF: unpushed non-HEAD old branch commit is absent from receiver')

    refs = {'head': base, 'branches/old': old, 'branches/incoming': incoming}
    augmented = Path(tmp) / 'augmented.bundle'
    assert bundle.git_bundle(str(repo), refs, str(augmented))
    git(receiver, 'fetch', '-q', str(augmented), '+refs/pstack/out/*:refs/pstack/in/probe/*')
    assert git(receiver, 'rev-parse', 'refs/pstack/in/probe/branches/old') == old
    print('EXPLICIT BRANCH TIPS: one bundle preserves unpushed non-HEAD branch objects and side refs')

    git(repo, 'update-ref', 'refs/remotes/elsewhere/topic', old)
    excluded = Path(tmp) / 'excluded.bundle'
    assert not bundle.git_bundle(str(repo), {'head': base, 'branches/old': old}, str(excluded))
    print('REMOTE CUT: a tip known only on a sender-only remote makes the requested handoff bundle empty')

    other = Path(tmp) / 'other-worktree'
    git(repo, 'worktree', 'add', '-q', str(other), 'old')
    git(repo, 'update-ref', 'refs/heads/old', incoming, old)
    assert git(other, 'rev-parse', 'HEAD') == incoming
    assert (other / 'f.txt').read_text() == 'one\n'
    assert git(other, 'status', '--porcelain')
    print('CHECKED-OUT BRANCH: update-ref changes another worktree HEAD without updating its files; receiver must not do this')
