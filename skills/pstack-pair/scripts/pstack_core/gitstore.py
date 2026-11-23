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

from . import bundle, git, secrets
from .errors import PairError, SecretFound
from .numbers import env_float, env_int
from .store import acquire_lock, event, header_field

BRANCH_PREFIX = 'kitchen/'
CODE_PREFIX = 'kitchen-code/'
LOCK = '.publish.lock'
UNPUBLISHED = 1 << 62
LOCAL, MASTER, ROLE, SHARED = 'local', 'master', 'role', 'shared-append'


class OwnershipError(PairError):
    """A write to a path the writer does not own, or one no class covers."""


class FencedWriter(PairError):
    """The writer's generation is no longer the active one in the store's pair.json."""


class StoreClosing(FencedWriter):
    """The master is closing the run: pair.json marks the store closed to every writer but the master, so nothing a role writes is published."""


class PublishFailed(PairError):
    """The write is committed here and is not on the remote; the next publish sends it."""


class StoreSetupError(PairError):
    """The store branch cannot be created or reached."""


class CodePending(PairError):
    """The commit a report names is on no ref of origin and in no bundle: the publication stays pending and nothing is sent."""


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
        'drain receipts of one writer generation (one file for each request it drained)',
        r'(?P<dir>drains)/(?P<role>[a-z]+)-g(?P<gen>\d+)/(?P<request>[0-9a-f]+)\.json',
        SHARED,
    ),
    _rule(
        'code bundles of one writer generation',
        r'(?P<dir>code)/(?P<role>[a-z]+)-g(?P<gen>\d+)\.bundle',
        SHARED,
    ),
    _rule(
        "the master's files",
        r'(?:pair\.json|standing-orders\.md|queue|decisions\.tsv|followups\.md|session-handoff\.md|status\.md|gates\.md|paused'
        r'|nest-pending\.tsv|snooze\.tsv|events\.tsv|gates\.tsv)',
        MASTER,
        ['master'],
    ),
    _rule(
        "the master's directories",
        r'(?:briefs|plans|consults|steers|reviews|compositions|sessions|notes|answers|kitchens|threads)/.+',
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


def store_files(store):
    """The store-relative path of every file of a local store that the branch holds: all but those that stay on one machine (the .git of a store
    that is a worktree already included). A link is refused, as a publish refuses one."""
    found = []
    for directory, names, files in os.walk(store):
        names[:] = sorted(n for n in names if n != '.git')
        for name in names:
            rel = posixpath.relpath(os.path.join(directory, name), store)
            if os.path.islink(os.path.join(store, rel)):
                rule = classify(rel)
                if rule is None or rule.kind != LOCAL:
                    raise OwnershipError(f'{rel}: a link is never published')
        for name in sorted(files):
            rel = posixpath.relpath(os.path.join(directory, name), store)
            rule = classify(rel)
            if rule is not None and rule.kind == LOCAL:
                continue
            if os.path.islink(os.path.join(store, rel)):
                raise OwnershipError(f'{rel}: a link is never published')
            found.append(rel)
    return found


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
            home = f'{found["dir"]}/{mine}' + (
                '/' if rel.count('/') > 1 else posixpath.splitext(rel)[1]
            )
            raise OwnershipError(f'{rel}: each writer appends to {home} only')
    elif writer.role not in rule.roles:
        raise OwnershipError(
            f'{rel}: {rule.label} belong to {" or ".join(rule.roles)}, not to {writer.role}'
        )


def owns(rel, writer):
    """Whether the writer may publish rel (check_owned without the refusal)."""
    try:
        check_owned(rel, writer)
    except OwnershipError:
        return False
    return True


def owner_role(rel):
    """The role that writes rel: the first role of its rule ('' for a path no rule covers, a local file or a shared-append file of any writer)."""
    rule = classify(rel)
    return rule.roles[0] if rule and rule.roles else ''


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


def caller_is_master(store):
    """Whether this process may write the master's files. A thread that pair.json does not name as the master, or as the master handing over
    (.master.previous, which still writes until the next one takes over), is a role's; with no thread, or a master pane that is not a thread's
    yet (a counter pane, before the next master's thread started), nothing says it is not the master."""
    from .mux.amp import own_thread

    thread = own_thread()
    try:
        with open(f'{store}/pair.json', encoding='utf-8') as f:
            master = json.load(f).get('master')
    except (OSError, ValueError, AttributeError):
        return True
    if not (thread and isinstance(master, dict)):
        return True
    previous = master.get('previous')
    panes = [master.get('pane_id')] + [
        previous.get('pane_id') if isinstance(previous, dict) else None
    ]
    if not str(panes[0] or '').startswith('amp:T-'):
        return True
    return f'amp:{thread}' in panes


def require_master(store):
    """Under git pair.json is the master's alone: raises OwnershipError for a role's thread. Nothing under another backend."""
    if is_git(store) and not caller_is_master(store):
        from .mux.amp import own_thread

        with open(f'{store}/pair.json', encoding='utf-8') as f:
            owner = json.load(f)['master'].get('pane_id')
        raise OwnershipError(
            f'pair.json: belongs to the master ({owner}), not to this thread (amp:{own_thread()})'
        )


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
        root,
        '-c',
        'core.quotepath=off',
        'status',
        '--porcelain=v1',
        '--no-renames',
        '--untracked-files=all',
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


run_git = _run  # the git call every module of the git store makes: committer filled in, stderr captured


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


def remote_tip(root, ref):
    """The object origin lists for ref (`refs/tags/<tag>^{}` asks for the commit an annotated tag points at): '' when origin lists none, None
    when origin cannot be read."""
    listed = _run(root, 'ls-remote', 'origin', ref)
    if listed.returncode != 0:
        return None
    for line in listed.stdout.splitlines():
        sha, _, name = line.partition('\t')
        if name == ref:
            return sha
    return ''


def _count(store, spec):
    out = git.out(store, 'rev-list', '--count', spec)
    return int(out) if out and out.isdigit() else 0


def unpublished(store, branch):
    """How many commits here the remote branch lacks."""
    remote = _remote_ref(branch)
    if git.ok(store, 'show-ref', '--verify', '--quiet', remote):
        return _count(store, f'{remote}..HEAD')
    return _count(store, 'HEAD')


def holds_event_row(store, actor, name, file, detail):
    """Whether the commit that last changed this file, or one after it, already holds the event row `event()` would write for it, and the file is
    still what HEAD holds. A refused push kept that commit, so the retry has nothing to write again, whether or not another publication has sent
    it since. A file rewritten after its row was written is a new send and gets its own row."""
    rel = checked_rel(store, file)
    if not git.ok(store, 'cat-file', '-e', f'HEAD:{rel}') or not git.ok(
        store, 'diff', '--quiet', 'HEAD', '--', rel
    ):
        return False
    last = git.out(store, 'log', '-1', '--format=%H', '--', rel)
    if not last:
        return False
    span = (
        f'{last}^..HEAD'
        if git.ok(store, 'rev-parse', '--quiet', '--verify', f'{last}^')
        else 'HEAD'
    )
    unit = os.path.basename(file).removesuffix('.md')
    log = os.path.relpath(event_file(store, actor), store)
    added = git.out(store, 'log', '-p', '--format=', '--no-color', span, '--', log)
    return any(
        line.startswith('+') and line[1:].split('\t')[2:] == [actor, name, unit, detail]
        for line in (added or '').splitlines()
    )


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


def backend_record(store):
    """The `store_backend` object of pair.json ({} when it holds none or cannot be read): where a close or a migration keeps its phases."""
    try:
        with open(f'{store}/pair.json', encoding='utf-8') as f:
            record = json.load(f).get('store_backend')
    except (OSError, ValueError, AttributeError):
        return {}
    return record if isinstance(record, dict) else {}


def fence(store, writer):
    """Raises StoreClosing for every writer, the master included, once `close` has deleted the branch (pair.json `.store_backend.close.done`
    holds `delete`), since a publish would put the branch back on origin; for a writer other than the master while `close` has the store
    fenced (`.store_backend.closing`); and FencedWriter unless the writer's generation is the active one in the store's pair.json as it is now."""
    record = backend_record(store)
    close = record.get('close')
    if isinstance(close, dict) and 'delete' in (close.get('done') or []):
        archive = close.get('tag')
        raise StoreClosing(
            f'{BRANCH_PREFIX}{slug_of(store)} is closed and deleted on origin'
            + (f' (archived as {archive})' if archive else '')
            + '; nothing is published'
        )
    if writer.role != 'master' and record.get('closing') is True:
        raise StoreClosing(
            f'the run is being closed (kitchen.sh close fenced {BRANCH_PREFIX}{slug_of(store)}); nothing is published'
        )
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
    reset = _run(store, 'reset', '--quiet')
    added = _run(store, 'add', '-A', '--', *rels) if reset.returncode == 0 else reset
    if added.returncode != 0:
        _run(store, 'reset', '--quiet')
        raise PublishFailed(
            f'cannot stage {", ".join(rels)} in {store}: {added.stderr.strip()}; nothing is committed'
        )
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


def _refusal(label, rel, writer):
    """The line for a flagged path an unpushed commit holds that this publish does not name: another role's file is that role's to publish."""
    owner = owner_role(rel)
    if owner and not owns(rel, writer):
        return f"secret: {label} is in an unpushed commit; {rel} is {owner}'s; it must publish it"
    return f'secret: {label} is in an unpushed commit; redact it and publish that path (it is not part of this publish)'


def _fold_unsent(store, branch, message, found, rels, writer):
    """Replaces the unpushed commits with one commit of the committed files, when every file a finding names that HEAD still holds is a path of this
    publish (`rels`, which publish has already checked the writer owns) and is clean in the working tree: the redaction a SecretFound asked for,
    made after the secret was committed. The commit has the remote tip as its parent (none when the branch is new) and the tree of HEAD with only
    those paths restaged from the working tree, so every other path keeps its committed blob (a half-written edit of it is not published), no blob
    that held the secret is in it and the commits that did stay only in the reflog. A flagged path HEAD no longer holds needs nothing: the fold
    drops it. Returns '' when a flagged path is still dirty in the working tree, and nothing was changed. A flagged path HEAD holds outside `rels` is
    refused once it is clean, one line per path and nothing changed: PublishFailed. Returns the previous HEAD otherwise. PublishFailed, with HEAD and the index as they were, when git cannot do it."""
    labels = {}
    for f in found:
        labels.setdefault(f.path.rsplit('@', 1)[0], f.path)
    held = [p for p in labels if git.ok(store, 'cat-file', '-e', f'HEAD:{p}')]
    if secrets.scan(held, root=store):
        return ''
    refused = [p for p in held if p not in rels]
    if refused:
        raise PublishFailed('\n'.join(_refusal(labels[p], p, writer) for p in refused))
    old = head_of(store)
    remote = _remote_ref(branch)
    parent = (
        ['-p', remote]
        if git.ok(store, 'show-ref', '--verify', '--quiet', remote)
        else []
    )

    def go(*args):
        done = _run(store, *args)
        if done.returncode != 0:
            _run(store, 'reset', '--quiet', old)
            raise PublishFailed(
                f'cannot fold the unpushed commits of {branch} into one clean commit; nothing is pushed'
            )
        return done.stdout.strip()

    go('reset', '--quiet')
    if held:
        go('add', '-A', '--', *held)
    tree = go('write-tree')
    go('reset', '--soft', go('commit-tree', tree, *parent, '-m', message))
    return old


def _refuse_unsent_secrets(store, branch, message, rels, writer):
    """Raises SecretFound, with nothing pushed, when a blob the push would send holds a credential: a commit made here outside publish, or one an
    earlier publish refused to push, is scanned as the files of this publish are. The finding names the commit; once every flagged file is clean
    in the working tree and part of this publish, the unpushed commits are folded into one new commit and scanned again, so redacting the file
    and publishing it clears the refusal and no blob that held the secret reaches the remote. A line a `pstack-allow-secret:` marker
    exempts goes through (publish logged it when it made the commit)."""
    found = secrets.scan(_unsent_texts(store, branch), root=store)
    if not found:
        return
    old = _fold_unsent(store, branch, message, found, rels, writer)
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


def discard(store, rels):
    """Puts each store-relative path back as HEAD holds it, or removes it when HEAD has none: a write that was never committed, undone."""
    for rel in rels:
        if git.ok(store, 'cat-file', '-e', f'HEAD:{rel}'):
            git.ok(store, 'checkout', 'HEAD', '--', rel)
        elif os.path.exists(f'{store}/{rel}'):
            os.unlink(f'{store}/{rel}')


def owned_dirty(store, writer):
    """The store-relative paths with changes not yet committed that the writer owns: what its command wrote since its last publication."""
    return sorted(rel for rel in dirty(store) if owns(rel, writer))


def publish_role(store, role, files=(), message='', sleep=time.sleep):
    """The one publication of a role's command: the files it names and every other path of the role with changes not yet committed (its events,
    progress and step logs; for the master its briefs, steers, reviews and pair.json), as the generation pair.json holds for the role."""
    writer = writer_of(store, role)
    rels = sorted(
        {checked_rel(store, f) for f in files} | set(owned_dirty(store, writer))
    )
    return publish(
        store,
        rels,
        message or f'kitchen: {role} publishes {len(rels)} files',
        writer,
        sleep,
    )


def slug_of(store):
    return branch_of(store).removeprefix(BRANCH_PREFIX)


def code_ref(store, writer):
    """The branch of origin a writer's code goes to: kitchen-code/<slug>/<role>-g<generation>."""
    return f'{CODE_PREFIX}{slug_of(store)}/{writer.role}-g{writer.generation}'


def code_bundle(writer):
    """The store-relative path of the git bundle that stands in for the code ref when origin refuses it."""
    return f'code/{writer.role}-g{writer.generation}.bundle'


def _on_run_code_ref(store, full):
    """Whether origin holds the commit on a code ref of this run, the refs fetch_code brings in. Origin is asked now and refs it dropped are
    pruned, so a stale remote-tracking ref counts for nothing; false when origin cannot be read."""
    prefix = f'{CODE_PREFIX}{slug_of(store)}/'
    fetched = _run(
        store,
        'fetch',
        '--quiet',
        '--prune',
        'origin',
        f'+refs/heads/{prefix}*:refs/remotes/origin/{prefix}*',
    )
    return fetched.returncode == 0 and bool(
        git.out(
            store, 'branch', '-r', '--contains', full, '--list', f'origin/{prefix}*'
        )
    )


def _bundle_head(store, full, path, trunk):
    """Cuts the head into a bundle less the run's code refs, the refs the master fetches, and trunk (origin's default branch, which fetch_code
    fetches too), so a bundle never carries trunk's history when origin refused the code ref. A cut less every remote-tracking ref could rest on
    a commit that sits only on another origin branch, which the master never fetches. False when there is nothing to carry."""
    out = f'{bundle.OUT}/code'
    git.ok(store, 'update-ref', out, full)
    try:
        return git.ok(
            store,
            'bundle',
            'create',
            '-q',
            path,
            out,
            '--not',
            f'--remotes=origin/{CODE_PREFIX}{slug_of(store)}/*',
            *([trunk] if trunk else []),
        )
    finally:
        git.ok(store, 'update-ref', '-d', out)


def publish_code(store, writer, head):
    """Makes a commit fetchable before the report that names it is announced (A5): pushed to the writer's code ref, else, when origin refuses
    that (a protected namespace, no permission), cut into a git bundle in the store. The store-relative paths to publish with the report: none for
    a push, the bundle for the fallback (PSTACK_CODE_BUNDLE=0 turns the fallback off). CodePending when neither works, which sends nothing."""
    full = git.out(store, 'rev-parse', '--verify', '--quiet', f'{head}^{{commit}}')
    if not full:
        raise CodePending(
            f'{head} is not a commit in this checkout, so it cannot be published; the report stays pending and nothing is sent'
        )
    ref = code_ref(store, writer)
    pushed = _run(store, 'push', '--force', 'origin', f'{full}:refs/heads/{ref}')
    rel = code_bundle(writer)
    published = pushed.returncode == 0 or _on_run_code_ref(store, full)
    trunk = ''
    if not published:
        trunk = _fetch_trunk(store)
        published = bool(trunk) and git.ok(
            store, 'merge-base', '--is-ancestor', full, trunk
        )
    if published:
        # A head already on one of the run's code refs or on origin's default branch is fetched from there, so a refused ref (and a bundle with
        # nothing to carry) is no reason to hold the report, and an older bundle has nothing left to carry either.
        if os.path.exists(f'{store}/{rel}'):
            os.unlink(f'{store}/{rel}')
        return []
    os.makedirs(f'{store}/code', exist_ok=True)
    if env_int('PSTACK_CODE_BUNDLE', 1) and _bundle_head(
        store, full, f'{store}/{rel}', trunk
    ):
        return [rel]
    raise CodePending(
        f'{head[:9]} was not pushed to {ref} ({pushed.stderr.strip() or pushed.stdout.strip()}) and no bundle was made; '
        'the report stays pending and nothing is sent'
    )


def report_code(store, role, file):
    """The code of a role's report or verdict, published before the file goes out (A5): the commit its head: names is pushed (or bundled).
    (the store-relative paths to publish with the report, the full shas of the commits it published)."""
    head = header_field(file, 'head')
    if not re.fullmatch(r'[0-9a-f]{7,40}', head):
        return [], []
    extra = publish_code(store, writer_of(store, role), head)
    return extra, [
        git.out(store, 'rev-parse', '--verify', f'{head}^{{commit}}') or head
    ]


def sha256_of(path):
    """The full sha256 of a file's bytes, '' when it cannot be read."""
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ''


def write_drains(store, writer, run, thread, requests, outputs, code):
    """A drain receipt (A7) for each request of `requests` that has no valid one yet: drains/<role>-g<generation>/<request>.json, which names the
    outputs (store-relative path and sha256) and code heads of the terminal publication it goes out with. A receipt whose outputs still match the
    files here is kept as it is; one whose output changed is written again. The store-relative paths written."""
    sums = [{'path': o, 'sha256': sha256_of(f'{store}/{o}')} for o in outputs]
    written = []
    for request in requests:
        rel = f'drains/{writer.role}-g{writer.generation}/{request}.json'
        path = f'{store}/{rel}'
        doc = {
            'run': run,
            'role': writer.role,
            'generation': writer.generation,
            'thread': thread,
            'request': request,
            'outputs': sums,
            'code': list(code),
        }
        try:
            with open(path, encoding='utf-8') as f:
                have = json.load(f)
        except (OSError, ValueError):
            have = None
        if (
            isinstance(have, dict)
            and have.get('thread') == thread
            and all(
                isinstance(o, dict)
                and sha256_of(f'{store}/{o.get("path")}') == o.get('sha256')
                for o in have.get('outputs') or []
            )
        ):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write('\n')
        written.append(rel)
    return written


def fetch_tip(store):
    """The commit origin's store branch holds now, fetched without touching the worktree; '' when origin cannot be read or has no such branch."""
    branch = branch_of(store)
    remote = _remote_ref(branch)
    try:
        lock = acquire_lock(store, LOCK)
    except PairError:
        return ''
    try:
        fetched = _run(
            store, 'fetch', '--quiet', 'origin', f'+refs/heads/{branch}:{remote}'
        )
        if fetched.returncode != 0:
            return ''
        return git.out(store, 'rev-parse', '--verify', '--quiet', remote) or ''
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _blob(store, tip, rel):
    """The bytes of a path of the tree at tip, None when it is not there."""
    got = git.run(store, 'show', f'{tip}:{rel}', quiet=True, binary=True)
    return got.stdout if got.returncode == 0 else None


def drained(store, tip, record, request):
    """Whether the tree at `tip` (a fetched tip of origin's store branch) holds a valid drain receipt for the request of a thread's control record:
    from the record's role and generation, naming the record's run and thread, with each listed output matching its sha256 in the commit that last
    wrote the receipt (a successor that rewrites the path later does not undo it). Reading the receipt from the tip is what makes it reachable from
    the remote: a receipt that only a role's local commit holds is not there."""
    role, generation = record.get('role'), record.get('generation')
    if not (isinstance(role, str) and isinstance(generation, int) and generation > 0):
        return False
    rel = f'drains/{role}-g{generation}/{request}.json'
    text = _blob(store, tip, rel)
    try:
        doc = json.loads(text) if text is not None else None
    except ValueError:
        return False
    if not isinstance(doc, dict) or any(
        doc.get(k) != record.get(k) for k in ('run', 'role', 'generation', 'thread')
    ):
        return False
    outputs = doc.get('outputs')
    if doc.get('request') != request or not isinstance(outputs, list) or not outputs:
        return False
    wrote = git.out(store, 'log', '-1', '--format=%H', tip, '--', rel)
    if not wrote:
        return False
    for out in outputs:
        path = out.get('path') if isinstance(out, dict) else None
        if not isinstance(path, str) or path.startswith('/') or '..' in path.split('/'):
            return False
        blob = _blob(store, wrote, path)
        if blob is None or hashlib.sha256(blob).hexdigest() != out.get('sha256'):
            return False
    return True


def _fetch_branch(store, branch):
    """Fetches origin's branch into its remote-tracking ref and returns that ref, '' when the fetch fails."""
    ref = _remote_ref(branch)
    fetched = _run(store, 'fetch', '--quiet', 'origin', f'+refs/heads/{branch}:{ref}')
    return ref if fetched.returncode == 0 else ''


def _origin_head_branch(store):
    """The branch origin's HEAD names, read from origin itself; '' when origin cannot be read or names none."""
    listing = git.out(store, 'ls-remote', '--symref', 'origin', 'HEAD') or ''
    found = re.search(r'^ref: refs/heads/(\S+)\tHEAD$', listing, re.MULTILINE)
    return found[1] if found else ''


def _fetch_trunk(store):
    """Fetches origin's default branch (the target of origin/HEAD, else origin's main, else master) into its remote-tracking ref and returns that
    ref, so the writer's cut and the master's fetch rest on the same trunk and a stale ref counts for nothing. A local origin/HEAD whose branch
    origin no longer has is stale: origin's own HEAD is fetched instead. Empty when the fetch of a trunk that resolved failed (origin cannot be
    read, or names no HEAD to fall back to); None when no trunk resolves (a repository with no origin/HEAD whose trunk is neither main nor master,
    or an origin that cannot be listed)."""
    head = git.out(store, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD') or ''
    branch = head.removeprefix('refs/remotes/origin/')
    if not branch:
        heads = git.out(store, 'ls-remote', '--heads', 'origin', 'main', 'master') or ''
        held = {line.split('refs/heads/')[-1] for line in heads.splitlines()}
        branch = next((name for name in ('main', 'master') if name in held), '')
    if not branch:
        return None
    fetched = _fetch_branch(store, branch)
    if fetched:
        return fetched
    actual = _origin_head_branch(store)
    return _fetch_branch(store, actual) if actual and actual != branch else ''


def fetch_code(store):
    """Brings the commits the writers announced into this checkout: every code ref of the run, and each bundle in the store. False when origin
    could not be read or a bundle could not be fetched (said on stderr)."""
    slug = slug_of(store)
    spec = (
        f'+refs/heads/{CODE_PREFIX}{slug}/*:refs/remotes/origin/{CODE_PREFIX}{slug}/*'
    )
    ok = _run(store, 'fetch', '--quiet', 'origin', spec).returncode == 0
    if _fetch_trunk(store) == '':
        ok = False
    for name in (
        sorted(os.listdir(f'{store}/code')) if os.path.isdir(f'{store}/code') else []
    ):
        # Forced: a role that amended its head rewrites the bundle, and the new head does not descend from the old one.
        got = _run(
            store,
            'fetch',
            '--quiet',
            f'{store}/code/{name}',
            f'+refs/pstack/out/code:refs/pstack/code/{name}',
        )
        if got.returncode != 0:
            sys.stderr.write(
                f'warning: code/{name} could not be fetched: {got.stderr.strip()}\n'
            )
            ok = False
    return ok


UNKNOWN_FRESHNESS = 'freshness: unknown; origin could not be read, so a report may be on the branch that this store does not hold yet (not a missing report)\n'


def refreshed(store):
    """What a reader calls before it judges the files of a store: the branch is fetched and the checkout brought up to it (under the publish lock,
    so no publish of this checkout is half done). True when it is fresh or the store is not git-backed. False when freshness is unknown: origin
    cannot be read or the lock stays held. A reader says that, and never reports it as a missing file. A branch that diverged (StoreDiverged) is a real
    ownership conflict, not an unknown: it propagates and ends the reading command."""
    if not is_git(store):
        return True
    try:
        lock = acquire_lock(store, LOCK)
    except PairError:
        return False
    try:
        return refresh(store)
    except StoreDiverged:
        raise
    except PairError:
        return False
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def refresh_saying_when_unknown(store):
    """refreshed, for a command that goes on whatever the answer: when freshness is unknown it says so on stderr (UNKNOWN_FRESHNESS)."""
    if not refreshed(store):
        sys.stderr.write(UNKNOWN_FRESHNESS)


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
        _refuse_unsent_secrets(store, branch, message, rels, writer)
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
