"""The git backend of a run store.

Under `.store_backend.backend = git` the store directory is a worktree of the work repository, checked out on the orphan branch `kitchen/<slug>`.
Every command keeps reading files from that directory. What changes is who may write which file (the ownership table), how a file's age is told
(a commit, never a modification time), where events go (one file per writer generation) and how a write reaches the other orbs (publish: one
fenced commit, pushed, with the rejection and the lost reply recovered).
"""

import hashlib
import json
import os
import posixpath
import re
import sys
import time
from typing import NamedTuple

from . import git, secrets
from .errors import PairError, SecretFound
from .numbers import env_float, env_int
from .store import acquire_lock, event

BRANCH_PREFIX = 'kitchen/'
LOCK = '.publish.lock'
UNPUBLISHED = 1 << 62
LOCAL, MASTER, ROLE, SHARED = 'local', 'master', 'role', 'shared-append'


class OwnershipError(PairError):
    """A write to a path the writer does not own, or one no class covers."""


class FencedWriter(PairError):
    """The writer's generation is no longer the active one in the store's pair.json."""


class PublishFailed(PairError):
    """The write is committed here and is not on the remote; the next publish sends it."""


class StoreSetupError(PairError):
    """The store branch cannot be created or reached."""


class StoreDiverged(PairError):
    """The remote branch cannot be combined with the commits here: a writer wrote a path it does not own."""


class Writer(NamedTuple):
    """Who publishes. generation is the one pair.json holds for the role, 0 when it holds none yet (a master before its first rotation)."""

    role: str
    generation: int


class Rule(NamedTuple):
    label: str
    pattern: re.Pattern
    kind: str
    roles: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()


def _rule(label, regex, kind, roles=(), ignore=()):
    return Rule(label, re.compile(regex), kind, tuple(roles), tuple(ignore))


# The one table the write path, the .gitignore and the tests share. The first rule that matches a path decides, so the narrow rules come first.
RULES = (
    _rule('.gitignore', r'\.gitignore', MASTER, ['master']),
    _rule(
        'dotfiles (locks, git)', r'(?:.*/)?\.[^/]+', LOCAL, ignore=['.*', '!.gitignore']
    ),
    _rule(
        'pid, tmp, lock and bundle files',
        r'.*\.(?:pid|tmp|lock|tgz)',
        LOCAL,
        ignore=['*.pid', '*.tmp', '*.lock', '*.tgz'],
    ),
    _rule('scratch worktrees', r'scratch(?:/.*)?', LOCAL, ignore=['scratch/']),
    _rule(
        'gate and Verify job logs', r'steps/jobs(?:/.*)?', LOCAL, ignore=['steps/jobs/']
    ),
    _rule(
        'per-writer-generation append files',
        r'(?P<dir>events|gates|acks)/(?P<role>[a-z]+)-g(?P<gen>\d+)\.tsv',
        SHARED,
    ),
    _rule(
        "the master's files",
        r'(?:pair\.json|standing-orders\.md|queue|decisions\.tsv|followups\.md|session-handoff\.md|status\.md|gates\.md|paused'
        r'|events\.tsv|gates\.tsv)',
        MASTER,
        ['master'],
    ),
    _rule(
        "the master's directories",
        r'(?:briefs|plans|consults|steers|reviews|compositions|sessions|notes|answers|kitchens)/.+',
        MASTER,
        ['master'],
    ),
    _rule('verifier packets', r'verdicts/[^/]+-packet\.md', MASTER, ['master']),
    _rule(
        "verdicts (a verifier writes its own, gates-mode verdicts are the master's)",
        r'verdicts/[^/]+',
        ROLE,
        ['verifier', 'master'],
    ),
    _rule('sidekick reports', r'reports/[^/]+', ROLE, ['sidekick']),
    _rule(
        'sidekick step logs (steplog names the files)',
        r'steps/[^/]+',
        ROLE,
        ['sidekick'],
    ),
    _rule('sidekick progress', r'progress/[^/]+', ROLE, ['sidekick']),
    _rule('consultant advice', r'advice/[^/]+', ROLE, ['consultant']),
)


def classify(rel):
    """The first Rule that covers a store-relative path, or None."""
    return next((r for r in RULES if r.pattern.fullmatch(rel)), None)


def gitignore_text():
    """The .gitignore of the branch: the ignore lines of every local rule, which keeps a local-only file out of `git add`."""
    lines = [line for rule in RULES for line in rule.ignore]
    return '\n'.join(dict.fromkeys(lines)) + '\n'


def check_owned(rel, writer):
    """Raises OwnershipError unless the writer may publish rel."""
    rule = classify(rel)
    if rule is None:
        raise OwnershipError(f'{rel}: no class in the store ownership table covers it')
    if rule.kind == LOCAL:
        raise OwnershipError(
            f'{rel}: {rule.label} stay on one machine and are never published'
        )
    if rule.kind == SHARED:
        found = rule.pattern.fullmatch(rel)
        assert found is not None
        generation = int(found['gen'])
        if found['role'] != writer.role or writer.generation != generation:
            mine = f'{writer.role}-g{writer.generation}'
            raise OwnershipError(
                f'{rel}: each writer appends to {found["dir"]}/{mine}.tsv only'
            )
    elif writer.role not in rule.roles:
        raise OwnershipError(
            f'{rel}: {rule.label} belong to {" or ".join(rule.roles)}, not to {writer.role}'
        )


def is_git(store):
    return os.path.exists(f'{store}/.git')


def root_of(path):
    """The store root of a git-backed store that holds path, or None: path sits at most three directories below a directory with a pair.json."""
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(4):
        if os.path.isfile(f'{d}/pair.json'):
            return d if is_git(d) else None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def generation_of(store, role):
    """The generation pair.json holds for role, or None when it holds none or cannot be read."""
    try:
        with open(f'{store}/pair.json', encoding='utf-8') as f:
            member = json.load(f).get(role)
    except (OSError, ValueError, AttributeError):
        return None
    generation = member.get('generation') if isinstance(member, dict) else None
    return generation if isinstance(generation, int) else None


def writer_of(store, role):
    """The Writer for role as pair.json names it now: what a command publishes as."""
    return Writer(role, generation_of(store, role) or 0)


def event_file(store, actor):
    """The file the actor's events go to under git: events/<actor>-g<generation>.tsv."""
    os.makedirs(f'{store}/events', exist_ok=True)
    return f'{store}/events/{actor}-g{generation_of(store, actor) or 0}.tsv'


def _epoch(line):
    fields = line.split('\t')
    return int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0


def event_rows(store):
    """Every event row of a git-backed store: events.tsv (rows from before the move, with no writer) and events/<writer>.tsv, ordered by epoch,
    then writer, then line. Two identical rows from different writers are two rows."""
    found = []
    legacy = f'{store}/events.tsv'
    sources = [('', legacy)] if os.path.isfile(legacy) else []
    if os.path.isdir(f'{store}/events'):
        sources += [
            (n, f'{store}/events/{n}')
            for n in sorted(os.listdir(f'{store}/events'))
            if n.endswith('.tsv')
        ]
    for writer, path in sources:
        with open(path, encoding='utf-8', errors='replace', newline='') as f:
            lines = f.read().splitlines()
        for n, line in enumerate(lines):
            if not (writer == '' and n == 0 and line.startswith('ts\t')):
                found.append((_epoch(line), writer, n, line))
    return [line for *_, line in sorted(found)]


def ack_file(store, role='sidekick'):
    os.makedirs(f'{store}/acks', exist_ok=True)
    return f'{store}/acks/{role}-g{generation_of(store, role) or 0}.tsv'


def acknowledge(store, paths, role='sidekick'):
    """Records that the role printed these parked notes, in the role's own acks file for its generation."""
    with open(ack_file(store, role), 'a', encoding='utf-8') as f:
        f.write(''.join(f'{p}\n' for p in paths))


def acknowledged(store):
    """Every path any writer acknowledged."""
    done = set()
    if os.path.isdir(f'{store}/acks'):
        for name in os.listdir(f'{store}/acks'):
            with open(f'{store}/acks/{name}', encoding='utf-8', errors='replace') as f:
                done.update(f.read().split('\n'))
    return done


class Published(NamedTuple):
    seq: int
    commit: str
    stamp: int


_HISTORY = {}


def history(root):
    """{store-relative path: Published} of the last commit that wrote each path: seq counts commits from the first (publication order), stamp is
    the commit time. Kept per HEAD, so a run of readers costs one `git log`."""
    key = (root, head_of(root))
    if key not in _HISTORY:
        _HISTORY.clear()
        _HISTORY[key] = _history(root)
    return _HISTORY[key]


def dirty(root):
    """The store-relative paths with changes not yet committed (modified or new, ignored files left out)."""
    text = git.out(
        root, '-c', 'core.quotepath=off', 'status', '--porcelain=v1', '--no-renames'
    )
    return {line[3:] for line in (text or '').splitlines() if len(line) > 3}


def _history(root):
    text = git.out(
        root,
        '-c',
        'core.quotepath=off',
        'log',
        '--first-parent',
        '--format=%x00%H %ct',
        '--name-only',
    )
    chunks = [c for c in (text or '').split('\x00') if c.strip()]
    found = {}
    for i, chunk in enumerate(chunks):
        head, *names = chunk.splitlines()
        commit, stamp = head.split()
        for name in names:
            if name:
                found.setdefault(name, Published(len(chunks) - i, commit, int(stamp)))
    return found


def digest_of(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return ''


def revision_fields(root, path, published, changed=frozenset()):
    """(stamp, order, commit, digest) of path in a git-backed store: published files by commit, so a pull that gives every file the same
    modification time still orders them; a file not yet published, or changed since, is newer than any that is, among those by modification time."""
    st = os.lstat(path)
    rel = posixpath.normpath(os.path.relpath(os.path.abspath(path), root))
    found = published.get(rel)
    digest = digest_of(path)
    if found is None or rel in changed:
        return int(st.st_mtime), UNPUBLISHED | st.st_mtime_ns, '', digest
    return found.stamp, found.seq, found.commit, digest


def _ident(store):
    """The `-c` options that give git a committer when the machine has none."""
    out = []
    for key, value in (('user.name', 'pstack'), ('user.email', 'pstack@localhost')):
        if not git.out(store, 'config', key):
            out += ['-c', f'{key}={value}']
    return out


def _run(store, *args):
    return git.run(store, *_ident(store), *args, stderr=git.PIPE)


def git_version():
    text = git.out(None, '--version') or ''
    found = re.search(r'(\d+)\.(\d+)', text)
    return (int(found[1]), int(found[2])) if found else (0, 0)


def branch_of(store):
    return git.out(store, 'symbolic-ref', '--short', 'HEAD') or ''


def head_of(store):
    return git.out(store, 'rev-parse', 'HEAD') or ''


def _remote_ref(branch):
    return f'refs/remotes/origin/{branch}'


def attach(store, repo, branch):
    """Checks the existing remote branch out as the worktree store, for a role's orb or a new master: the same branch the creator pushed."""
    if not repo or not git.ok(repo, 'remote', 'get-url', 'origin'):
        raise StoreSetupError(
            'the store branch needs a work repository with a remote named origin'
        )
    remote = _remote_ref(branch)
    fetched = _run(repo, 'fetch', '--quiet', 'origin', f'+refs/heads/{branch}:{remote}')
    if fetched.returncode != 0:
        raise StoreSetupError(
            f'cannot fetch {branch} from origin: {fetched.stderr.strip()}'
        )
    added = _run(repo, 'worktree', 'add', '-B', branch, store, remote)
    if added.returncode != 0:
        raise StoreSetupError(
            f'cannot check {branch} out at {store}: {added.stderr.strip()}'
        )
    _run(repo, 'worktree', 'lock', '--reason', 'pstack run store', store)


def _branch_places(repo, branch):
    """(here, on origin): whether the store branch is in the work repository, and whether origin lists it: True, False when origin answers that
    it has no such branch (ls-remote --exit-code exits 2), None when origin cannot be reached (any other failure)."""
    here = git.ok(repo, 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}')
    listed = _run(repo, 'ls-remote', '--exit-code', '--heads', 'origin', branch)
    return here, (
        True if listed.returncode == 0 else False if listed.returncode == 2 else None
    )


def _require_origin(repo):
    if not repo or not git.ok(repo, 'remote', 'get-url', 'origin'):
        raise StoreSetupError(
            'PSTACK_STORE=git needs a work repository with a remote named origin'
        )


def create(store, repo, branch):
    """The orphan branch `branch` as a worktree at store, with a .gitignore, pushed to origin. The worktree is locked, so a prune of the work
    repository cannot drop it while its directory is away. PublishFailed after the commit: the store is made and the next publish sends it."""
    _require_origin(repo)
    if any(_branch_places(repo, branch)):
        raise StoreSetupError(
            f'the branch {branch} already exists; attach to it instead of creating the store'
        )
    if git_version() >= (2, 42):
        added = _run(repo, 'worktree', 'add', '--orphan', '-b', branch, store)
    else:
        added = _run(
            repo, 'worktree', 'add', '--no-checkout', '--detach', store, 'HEAD'
        )
        if added.returncode == 0:
            added = _run(store, 'checkout', '--orphan', branch)
    if added.returncode != 0:
        raise StoreSetupError(
            f'cannot create the worktree {store} on {branch}: {added.stderr.strip()}'
        )
    with open(f'{store}/.gitignore', 'w', encoding='utf-8') as f:
        f.write(gitignore_text())
    _run(store, 'add', '.gitignore')
    done = _run(store, 'commit', '-m', f'kitchen: store for {branch}')
    if done.returncode != 0:
        raise StoreSetupError(f'cannot commit the store: {done.stderr.strip()}')
    _run(repo, 'worktree', 'lock', '--reason', 'pstack run store', store)
    pushed = push(store, branch)
    if pushed.status != 'ok':
        raise PublishFailed(
            f'{branch} is committed in {store} and not on origin yet ({pushed.detail}); the next publish sends it'
        )


def open_store(store, repo, branch):
    """The store of an init that may already have started: the worktree is already on the branch (a rerun after a later step failed), the
    branch is on origin or only here (checked out at store), or neither (created); an origin that cannot be reached with no branch here is
    refused, since the branch may be there. PublishFailed as create raises it."""
    if is_git(store) and branch_of(store) == branch:
        return
    _require_origin(repo)
    here, remote = _branch_places(repo, branch)
    if remote:
        attach(store, repo, branch)
    elif here:
        added = _run(repo, 'worktree', 'add', store, branch)
        if added.returncode != 0:
            raise StoreSetupError(
                f'cannot check {branch} out at {store}: {added.stderr.strip()}'
            )
        _run(repo, 'worktree', 'lock', '--reason', 'pstack run store', store)
    elif remote is None:
        raise StoreSetupError(
            f'origin is unreachable; cannot tell whether {branch} exists. Rerun init when origin answers'
        )
    else:
        create(store, repo, branch)


class Push(NamedTuple):
    status: str
    detail: str


def push(store, branch):
    """One push of HEAD to the branch: 'ok'; 'rejected' when the remote moved (or refused the ref update for a moment); else 'failed' with the
    reason, which a network or credential problem is."""
    done = _run(store, 'push', '--porcelain', 'origin', f'HEAD:refs/heads/{branch}')
    if done.returncode == 0:
        return Push('ok', '')
    text = f'{done.stdout}\n{done.stderr}'
    if re.search(r'\[rejected\]|failed to update ref|cannot lock ref', text):
        return Push('rejected', text.strip())
    return Push('failed', text.strip())


def _count(store, spec):
    out = git.out(store, 'rev-list', '--count', spec)
    return int(out) if out and out.isdigit() else 0


def unpublished(store, branch):
    """How many commits here the remote branch lacks."""
    remote = _remote_ref(branch)
    if git.ok(store, 'show-ref', '--verify', '--quiet', remote):
        return _count(store, f'{remote}..HEAD')
    return _count(store, 'HEAD')


def refresh(store):
    """Fetches the branch and brings the checkout up to it: a fast-forward, or the commits here put on top of it. False when the remote cannot
    be read, which means the files may be stale and is not a statement about what is missing. A branch origin does not have yet (a store whose
    first push failed) is nothing to fetch: True, and every commit here is unpublished."""
    branch = branch_of(store)
    remote = _remote_ref(branch)
    fetched = _run(
        store, 'fetch', '--quiet', 'origin', f'+refs/heads/{branch}:{remote}'
    )
    if fetched.returncode != 0:
        listed = _run(store, 'ls-remote', '--exit-code', '--heads', 'origin', branch)
        return listed.returncode == 2
    if _count(store, f'HEAD..{remote}') == 0:
        return True
    if unpublished(store, branch) == 0:
        moved = _run(store, 'merge', '--ff-only', remote)
        if moved.returncode != 0:
            raise StoreDiverged(
                f'cannot fast-forward {store} to {branch}: {moved.stderr.strip()}'
            )
        return True
    replay(store, remote)
    return True


def replay(store, remote):
    """Puts the unpublished commits on top of remote. A reset that keeps local changes and a cherry-pick stand in for a rebase, which refuses
    to run while pair.json, rewritten by the master and not yet published, differs from the index."""
    ours = (git.out(store, 'rev-list', '--reverse', f'{remote}..HEAD') or '').split()
    before = head_of(store)
    moved = _run(store, 'reset', '--keep', remote)
    picked = (
        _run(store, 'cherry-pick', '--keep-redundant-commits', *ours)
        if moved.returncode == 0
        else moved
    )
    if picked.returncode != 0:
        _run(store, 'cherry-pick', '--abort')
        _run(store, 'reset', '--keep', before)
        raise StoreDiverged(
            f'the commits here do not combine with {remote}: a writer wrote a path it does not own ({picked.stderr.strip()})'
        )


def fence(store, writer):
    """Raises FencedWriter unless the writer's generation is the active one in the store's pair.json as it is now."""
    active = generation_of(store, writer.role) or 0
    if active != writer.generation:
        raise FencedWriter(
            f'{writer.role} generation {writer.generation} is not the active one (pair.json says {active}); nothing is published'
        )


def checked_rel(store, path):
    """The store-relative path of a file to publish. A path that leaves the store, goes through a link, or names a directory is refused."""
    root = os.path.realpath(store)
    full = os.path.join(root, path) if not os.path.isabs(path) else path
    parent = os.path.realpath(os.path.dirname(full))
    if os.path.commonpath([root, parent]) != root:
        raise OwnershipError(f'{path}: leaves the store')
    if os.path.islink(full) or os.path.isdir(full):
        raise OwnershipError(f'{path}: a link or a directory is never published')
    return posixpath.normpath(
        os.path.relpath(os.path.join(parent, os.path.basename(full)), root)
    )


def _staged_texts(store):
    """(path, text) of every file staged for the commit (a deletion needs no scan), read from the index, so the scan sees exactly the blobs the commit will hold.
    PublishFailed when git cannot list or read one: a file that cannot be read is never taken for a clean one."""
    listed = git.run(
        store, 'diff', '--cached', '--name-only', '-z', '--diff-filter=d', quiet=True
    )
    if listed.returncode != 0:
        raise PublishFailed(
            f'cannot list the staged files in {store}; nothing is committed'
        )
    items = []
    for rel in filter(None, listed.stdout.split('\0')):
        shown = git.run(store, 'show', f':{rel}', quiet=True)
        if shown.returncode != 0:
            raise PublishFailed(
                f'cannot read the staged {rel} to scan it; nothing is committed'
            )
        items.append((rel, shown.stdout))
    return items


def _refuse_secrets(store, writer):
    """Raises SecretFound, with nothing left staged, when a staged file holds a credential, and PublishFailed, likewise, when one cannot be read.
    A line a `pstack-allow-secret: <reason>` marker exempts goes through and is logged: a line on stderr and an event of the writer, which the
    review reads."""
    exempt = []
    try:
        found = secrets.scan(_staged_texts(store), root=store, exempt=exempt)
    except PublishFailed:
        _run(store, 'reset', '--quiet')
        raise
    if found:
        _run(store, 'reset', '--quiet')
        raise SecretFound(found)
    for e in exempt:
        sys.stderr.write(
            f'secret: {e.path}:{e.line}: {e.kind} allowed by pstack-allow-secret ({e.reason})\n'
        )
        event(
            store,
            writer.role,
            'secret-allowed',
            e.path,
            f'line {e.line} {e.kind}: {e.reason}',
        )


def _commit(store, rels, message, writer):
    """One commit of exactly these paths, as the scan read them from the index; 0 when nothing in them changed. SecretFound, with the paths
    unstaged and nothing committed, when a changed file holds a credential. The commit takes the index and never rereads the working tree, so
    a file edited after the scan cannot land unscanned."""
    _run(store, 'reset', '--quiet')
    _run(store, 'add', '-A', '--', *rels)
    if _run(store, 'diff', '--cached', '--quiet').returncode == 0:
        return 0
    _refuse_secrets(store, writer)
    done = _run(store, 'commit', '-m', message)
    if done.returncode != 0:
        raise PublishFailed(f'cannot commit in {store}: {done.stderr.strip()}')
    return 1


def _unsent_texts(store, branch):
    """(label, text) of every blob a push of HEAD would send that the remote branch lacks: the blobs of all the commits not on it, the ones a later
    commit removed again included. A label is `<path>@<short commit>`, the first commit that holds the blob, so two versions of one path never
    share a label (the scan keys its allow markers on it and gitleaks writes a file under it). PublishFailed when git cannot list or read one."""
    remote = _remote_ref(branch)
    spec = (
        f'{remote}..HEAD'
        if git.ok(store, 'show-ref', '--verify', '--quiet', remote)
        else 'HEAD'
    )
    listed = git.run(store, 'rev-list', '--objects', spec, quiet=True)
    typed = git.run(
        store,
        'cat-file',
        '--batch-check=%(objecttype) %(objectname) %(rest)',
        stdin=listed.stdout,
        quiet=True,
    )
    history = git.run(
        store,
        'log',
        '--reverse',
        '--raw',
        '--no-abbrev',
        '--no-renames',
        '-m',
        '--root',
        '--format=commit %H',
        spec,
        quiet=True,
    )
    if listed.returncode != 0 or typed.returncode != 0 or history.returncode != 0:
        raise PublishFailed(
            f'cannot list what a push of {branch} would send; nothing is pushed'
        )
    blobs = {}
    for line in typed.stdout.splitlines():
        kind, sha, path = (line.split(' ', 2) + ['', ''])[:3]
        if kind == 'blob':
            blobs.setdefault(sha, path or sha)
    introduced, commit = {}, ''
    for line in history.stdout.splitlines():
        if line.startswith('commit '):
            commit = line[7:14]
        elif line.startswith(':'):
            sha = line.split('\t')[0].split()[3]
            introduced.setdefault(sha, commit)
    items, labels = [], set()
    for sha, path in blobs.items():
        shown = git.run(store, 'cat-file', 'blob', sha, quiet=True)
        if shown.returncode != 0:
            raise PublishFailed(f'cannot read {path} to scan it; nothing is pushed')
        label = f'{path}@{introduced.get(sha, sha[:7])}'
        if label in labels:
            label = f'{label}:{sha[:7]}'
        labels.add(label)
        items.append((label, shown.stdout))
    return items


def _fold_unsent(store, branch, message, found):
    """Replaces the unpushed commits with one commit of the current files, when every file a finding names is clean in the working tree: the
    redaction a SecretFound asked for, made after the secret was committed. The commit has the remote tip as its parent (none when the branch is
    new) and the tree of HEAD with the flagged paths restaged from the working tree, so no blob that held the secret is in it and the commits
    that did stay only in the reflog. Returns the previous HEAD, or '' when a flagged file is still dirty and nothing was changed. PublishFailed,
    with HEAD and the index as they were, when git cannot do it."""
    paths = sorted({f.path.rsplit('@', 1)[0] for f in found})
    if secrets.scan(paths, root=store):
        return ''
    old = head_of(store)
    remote = _remote_ref(branch)
    parent = (
        ['-p', remote]
        if git.ok(store, 'show-ref', '--verify', '--quiet', remote)
        else []
    )
    here = [p for p in paths if os.path.lexists(os.path.join(store, p))]
    gone = [p for p in paths if p not in here]

    def go(*args):
        done = _run(store, *args)
        if done.returncode != 0:
            _run(store, 'reset', '--quiet', old)
            raise PublishFailed(
                f'cannot fold the unpushed commits of {branch} into one clean commit; nothing is pushed'
            )
        return done.stdout.strip()

    go('reset', '--quiet')
    if here:
        go('add', '-A', '--', *here)
    if gone:
        go('rm', '-q', '--cached', '--ignore-unmatch', '--', *gone)
    tree = go('write-tree')
    go('reset', '--soft', go('commit-tree', tree, *parent, '-m', message))
    return old


def _refuse_unsent_secrets(store, branch, message):
    """Raises SecretFound, with nothing pushed, when a blob the push would send holds a credential: a commit made here outside publish, or one an
    earlier publish refused to push, is scanned as the files of this publish are. The finding names the commit; once every flagged file is clean
    in the working tree, the unpushed commits are folded into one new commit of the current files and scanned again, so redacting the file and
    running the command again clears the refusal and no blob that held the secret reaches the remote. A line a `pstack-allow-secret:` marker
    exempts goes through (publish logged it when it made the commit)."""
    found = secrets.scan(_unsent_texts(store, branch), root=store)
    if not found:
        return
    old = _fold_unsent(store, branch, message, found)
    if not old:
        raise SecretFound(found)
    again = secrets.scan(_unsent_texts(store, branch), root=store)
    if again:
        _run(store, 'reset', '--quiet', old)
        raise SecretFound(again)


def _on_remote(store, branch):
    """Whether HEAD is on the remote branch, after a reply that did not say: the answer to an ambiguous push."""
    if not refresh(store):
        return False
    return git.ok(store, 'merge-base', '--is-ancestor', 'HEAD', _remote_ref(branch))


def publish(store, paths, message, writer, sleep=time.sleep):
    """Commits the given store files and pushes them: the one publication of a command. Under a lock for this checkout it refreshes, checks the
    writer's generation against the refreshed pair.json before the commit and before every retry, commits only those paths and pushes. A remote
    that moved is met with a replay and a retry (PSTACK_STORE_PUSH_TRIES, 5; PSTACK_STORE_PUSH_BACKOFF_S between them); a failure of the network
    or the credentials stops with the commit kept, and the next publish sends it. Returns the published HEAD, or '' when there was nothing."""
    rels = [checked_rel(store, p) for p in paths]
    for rel in rels:
        check_owned(rel, writer)
    lock = acquire_lock(store, LOCK)
    try:
        return _publish(store, rels, message, writer, sleep)
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _changed(store, rels):
    """Whether any of these paths differs from HEAD (modified, new or removed)."""
    return bool(rels) and bool(git.out(store, 'status', '--porcelain', '--', *rels))


def _publish(store, rels, message, writer, sleep):
    branch = branch_of(store)
    if not _changed(store, rels) and unpublished(store, branch) == 0:
        return ''
    tries = env_int('PSTACK_STORE_PUSH_TRIES', 5)
    backoff = env_float('PSTACK_STORE_PUSH_BACKOFF_S', 0.2)
    made = 0
    for attempt in range(tries):
        fresh = refresh(store)
        try:
            fence(store, writer)
        except FencedWriter:
            if made:
                _run(store, 'reset', '--mixed', 'HEAD~1')
            raise
        if attempt == 0 and rels:
            made = _commit(store, rels, message, writer)
        if not fresh:
            raise PublishFailed(
                f'{branch} could not be fetched; the commit stays here for the next publish'
            )
        if not made and unpublished(store, branch) == 0:
            return ''
        _refuse_unsent_secrets(store, branch, message)
        pushed = push(store, branch)
        if pushed.status == 'ok':
            return head_of(store)
        if pushed.status == 'failed':
            if _on_remote(store, branch):
                return head_of(store)
            raise PublishFailed(
                f'{branch} was not pushed ({pushed.detail}); the commit stays here for the next publish'
            )
        sleep(backoff * 2**attempt)
    raise PublishFailed(
        f'{branch} was rejected {tries} times; the commit stays here for the next publish'
    )
