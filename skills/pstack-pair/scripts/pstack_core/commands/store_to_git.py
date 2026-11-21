"""store-to-git (A6): moves a running local store to the branch `kitchen/<slug>` of the work repository. The snapshot is made in a repository of its
own, so the store directory is not touched until origin holds the branch; then the directory becomes a worktree of the work repository in place
(its `.git` is moved in), and pair.json, which now says git, is published. A rerun continues from what the facts show and a finished one
changes nothing."""

import json
import os
import shutil
import sys
import tempfile
import time

from .. import git, gitstore, secrets
from ..errors import Die, PairError, SecretFound
from ..store import event, field, load, member, pair_file, peek, text_at, update

RECORD = '.store-to-git.json'


class MigrationRefused(PairError):
    """The branch is on origin and this migration did not put it there: it is not overwritten."""


def work_repo(store):
    repo = text_at(load(store), 'git_root') or git.toplevel()
    if not repo or not git.ok(repo, 'remote', 'get-url', 'origin'):
        raise gitstore.StoreSetupError(
            'store-to-git needs the work repository (pair.json git_root) with a remote named origin'
        )
    return repo


def recorded_tip(store):
    try:
        with open(f'{store}/{RECORD}', encoding='utf-8') as f:
            tip = json.load(f).get('tip')
    except (OSError, ValueError, AttributeError):
        return ''
    return tip if isinstance(tip, str) else ''


def record_tip(store, branch, tip):
    """The snapshot commit about to be pushed, written before the push: a crash between the push and anything after it leaves origin's branch at
    a commit this store named, which a rerun takes for its own."""
    with open(f'{store}/{RECORD}', 'w', encoding='utf-8') as f:
        json.dump({'branch': branch, 'tip': tip}, f)
        f.write('\n')


def scan(store, rels):
    """Stops on a credential in any file the branch would hold, before anything is pushed: SecretFound names each file and kind."""
    exempt = []
    found = secrets.scan(rels, root=store, exempt=exempt)
    for e in exempt:
        sys.stderr.write(
            f'secret: {e.path}:{e.line}: {e.kind} allowed by pstack-allow-secret ({e.reason})\n'
        )
    if found:
        raise SecretFound(found)


def push_snapshot(store, repo, branch, rels):
    """One commit of the store's files (and the branch's .gitignore) in a repository made for it, pushed to origin as `branch`. Returns the commit."""
    url = git.out(repo, 'remote', 'get-url', '--push', 'origin') or ''
    if os.path.isdir(os.path.join(repo, url)):
        url = os.path.abspath(os.path.join(repo, url))
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, 'snapshot')
        git.run(None, 'init', '-q', snap, quiet=True)
        for rel in rels:
            target = os.path.join(snap, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(store, rel), target)
        with open(f'{snap}/.gitignore', 'w', encoding='utf-8') as f:
            f.write(gitstore.gitignore_text())
        added = gitstore.run_git(snap, 'add', '-A')
        done = gitstore.run_git(
            snap, 'commit', '-q', '-m', f'kitchen: store of {branch} (store-to-git)'
        )
        if added.returncode != 0 or done.returncode != 0:
            raise gitstore.StoreSetupError(
                f'cannot commit the snapshot of {store}: {(added.stderr + done.stderr).strip()}'
            )
        tip = gitstore.head_of(snap)
        record_tip(store, branch, tip)
        pushed = gitstore.run_git(snap, 'push', url, f'{tip}:refs/heads/{branch}')
        if pushed.returncode != 0:
            raise gitstore.PublishFailed(
                f'{branch} was not pushed ({pushed.stderr.strip()}); the store is unchanged, run store-to-git again'
            )
    return tip


def convert(store, repo, branch):
    """The store directory becomes a worktree of the work repository on the branch, where it is: a worktree is made beside it with no files, its
    `.git` link moves in, and git is told where it went. The files stay as they are, so a role's open file or a local-only file is untouched."""
    remote = f'refs/remotes/origin/{branch}'
    fetched = gitstore.run_git(
        repo, 'fetch', '--quiet', 'origin', f'+refs/heads/{branch}:{remote}'
    )
    if fetched.returncode != 0:
        raise gitstore.StoreSetupError(
            f'cannot fetch {branch} from origin: {fetched.stderr.strip()}'
        )
    beside = f'{store}.migrating'
    if os.path.exists(beside):
        gitstore.run_git(repo, 'worktree', 'remove', '--force', beside)
        shutil.rmtree(beside, ignore_errors=True)
        gitstore.run_git(repo, 'worktree', 'prune')
    added = gitstore.run_git(
        repo, 'worktree', 'add', '--no-checkout', '-B', branch, beside, remote
    )
    if added.returncode != 0:
        raise gitstore.StoreSetupError(
            f'cannot make the worktree for {branch}: {added.stderr.strip()}'
        )
    shutil.move(f'{beside}/.git', f'{store}/.git')
    os.rmdir(beside)


def settle_worktree(store, repo):
    """The links, the index and the .gitignore of a store that is a worktree: repaired and read from HEAD, which changes nothing that is already right."""
    gitstore.run_git(repo, 'worktree', 'repair', store)
    gitstore.run_git(repo, 'worktree', 'lock', '--reason', 'pstack run store', store)
    gitstore.run_git(store, 'reset', '--quiet')
    gitstore.run_git(store, 'checkout', '--quiet', 'HEAD', '--', '.gitignore')


def live_roles(store, roles):
    """(role, name, pane) of each role that is started and not closed: each one's thread holds a copy of the old store."""
    live = []
    for role in roles:
        name, pane = field(store, role, 'name'), field(store, role, 'pane_id')
        if name and pane and peek(load(store), role).get('closed') is not True:
            live.append((role, name, pane))
    return live


def finish(ctx, store, repo, branch):
    """The store is on the branch: pair.json says so and is published, and each live role is told how to move over. A second run of it writes
    and publishes nothing."""
    settle_worktree(store, repo)
    shutil.rmtree(f'{store}.migrating', ignore_errors=True)
    moved = gitstore.backend_record(store).get('backend') != 'git'
    if moved:

        def mutate(doc):
            record = member(doc, 'store_backend')
            record['backend'] = 'git'
            record['branch'] = branch
            record['migrated_at'] = int(time.time())

        update(store, mutate)
        event(store, 'master', 'store-to-git', '-', branch)
    published = gitstore.publish_role(
        store, 'master', message=f'kitchen: store-to-git moves {branch} here'
    )
    if not (moved or published):
        sys.stdout.write(
            f'store: {store} is already on {branch} at {gitstore.head_of(store)[:9]}; nothing changed\n'
        )
        return
    sys.stdout.write(
        f'store: {store} is now the worktree of {branch} at {gitstore.head_of(store)[:9]}\n'
    )
    for role, name, pane in live_roles(store, ctx.roles):
        sys.stdout.write(
            f'live {role} {name} ({pane}): send it `pair.sh sync --branch {store} {branch}`\n'
        )


def migrate(ctx, store):
    pair_file(store)
    if not gitstore.caller_is_master(store):
        raise gitstore.OwnershipError('store-to-git runs from the master only')
    repo = work_repo(store)
    slug = text_at(load(store), 'slug') or os.path.basename(store)
    branch = f'{gitstore.BRANCH_PREFIX}{slug}'
    if gitstore.is_git(store):
        if gitstore.branch_of(store) != branch:
            raise MigrationRefused(
                f'{store} is a worktree of {gitstore.branch_of(store)}, not of {branch}'
            )
        return finish(ctx, store, repo, branch)
    here = gitstore.remote_tip(repo, f'refs/heads/{branch}')
    if here is None:
        raise gitstore.StoreSetupError(
            f'origin cannot be read, so {branch} is not pushed; run store-to-git again when origin answers'
        )
    if here and here != recorded_tip(store):
        raise MigrationRefused(
            f'{branch} is already on origin ({here[:9]}) and this store did not put it there; it is not overwritten'
        )
    if not here:
        rels = gitstore.store_files(store)
        scan(store, rels)
        tip = push_snapshot(store, repo, branch, rels)
        if gitstore.remote_tip(repo, f'refs/heads/{branch}') != tip:
            raise gitstore.PublishFailed(
                f'origin does not list {branch} at {tip[:9]} after the push; the store is unchanged'
            )
        sys.stdout.write(f'store-to-git: {branch} is on origin at {tip[:9]}\n')
    convert(store, repo, branch)
    finish(ctx, store, repo, branch)


def store_to_git(ctx, args):
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} store-to-git <store>')
    migrate(ctx, args[0])
    return 0
