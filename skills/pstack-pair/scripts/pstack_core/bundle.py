"""A store and the commits behind it, carried between agents that run on machines of their own (Amp threads, each on its own orb). pack()
writes one .tgz: the store's files under store/, a git bundle of the commits the receiver may lack (HEAD and the commit of each scratch
worktree the store links), and a manifest. apply() merges one into this machine at the same absolute paths, as every orb has them.

The merge is three-way, against the bundle this side last sent to the sender (base): a file the sender did not change since then keeps
this side's copy, one only the sender changed takes the sender's, and one both changed takes the sender's and is named as a conflict.
pair.json, which master and roles both update, merges key by key the same way; a .tsv file, which every command appends to, takes the
union of its lines. Without a base (first contact) the sender's copy wins wherever the two differ.

A role's reply to the master is merged against the bundle the master sent that role *last*, which can be newer than the snapshot the role worked
from, so the three-way rule would take the role's older copy of anything the master changed in between. A reply therefore never changes
what the master or another role owns (role_reply, owned_by): the master's pair.json is kept whole, and only the current sidekick's completion
(SIDEKICK_FACT) is taken from it; every other file is kept as this side has it unless the sender's own role owns it (gitstore.RULES), a file
this side lacks is added (a master-owned one is not), and only the shared append logs (SHARED_LOGS) take the union of their lines."""

import io
import json
import os
import shutil
import tarfile
import tempfile
import uuid
from typing import NamedTuple

from . import git

MANIFEST = 'manifest.json'
GIT_BUNDLE = 'git.bundle'
# A file bigger than this (a session export, a long gate log) stays on the machine that wrote it.
MAX_FILE = 1 << 20
LINK = b'symlink:'
OUT = 'refs/pstack/out'
MISSING = object()
# The one pair.json fact a role sets under the local store (commands/report.py record_rotation_required), and the append logs every role writes.
SIDEKICK_FACT = 'rotation_required'
SHARED_LOGS = ('events.tsv', 'gates.tsv')
ROLES = ('sidekick', 'consultant', 'verifier')


class Reply(NamedTuple):
    """A transfer that is a role's reply to the master of an established run; role is the role the receiver's pair.json gives the sender's name
    ('sidekick', 'consultant' or 'verifier'), or '' for a sender it does not name."""

    role: str

    @property
    def from_sidekick(self):
        return self.role == 'sidekick'


def skipped(rel):
    """Files that belong to one machine: dotfiles (locks), pid files and half-written temporaries."""
    parts = rel.split('/')
    return any(p.startswith('.') for p in parts) or rel.endswith(
        ('.pid', '.tmp', '.lock')
    )


def store_files(store):
    """({relative path: content} of the files that travel, every relative path this side holds). A symlink's content is LINK + its target."""
    files, listed = {}, set()
    for top, dirs, names in os.walk(store):
        for d in [d for d in dirs if os.path.islink(os.path.join(top, d))]:
            dirs.remove(d)
            names.append(d)
        for name in names:
            path = os.path.join(top, name)
            rel = os.path.relpath(path, store)
            if skipped(rel):
                continue
            listed.add(rel)
            if os.path.islink(path):
                files[rel] = LINK + os.readlink(path).encode()
            elif os.path.isfile(path) and os.path.getsize(path) <= MAX_FILE:
                with open(path, 'rb') as f:
                    files[rel] = f.read()
    return files, listed


def member_bytes(tar, member):
    f = tar.extractfile(member)
    return f.read() if f is not None else b''


def read_bundle(path):
    """(manifest, {relative path: content}, git bundle bytes or None) of a bundle file."""
    files, manifest, gitdata = {}, None, None
    with tarfile.open(path, 'r:gz') as tar:
        for member in tar.getmembers():
            if member.name == MANIFEST:
                manifest = json.loads(member_bytes(tar, member))
            elif member.name == GIT_BUNDLE:
                gitdata = member_bytes(tar, member)
            elif member.name.startswith('store/'):
                rel = member.name[len('store/') :]
                if member.issym():
                    files[rel] = LINK + member.linkname.encode()
                elif member.isfile():
                    files[rel] = member_bytes(tar, member)
    if not isinstance(manifest, dict):
        raise ValueError(f'{path} is not a pstack bundle: it has no manifest')
    return manifest, files, gitdata


def repo_of(store):
    """The checkout the store's run works in (pair.json's git_root, else its cwd), '' when unknown."""
    try:
        with open(os.path.join(store, 'pair.json'), encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return ''
    doc = doc if isinstance(doc, dict) else {}
    root = doc.get('git_root') or doc.get('cwd') or ''
    return root if isinstance(root, str) and os.path.isdir(root) else ''


def worktrees(store):
    """[{path, commit}] of each scratch worktree the store links (scratch/<id>), at its HEAD."""
    found = []
    scratch = os.path.join(store, 'scratch')
    for name in sorted(os.listdir(scratch)) if os.path.isdir(scratch) else []:
        link = os.path.join(scratch, name)
        if not os.path.islink(link):
            continue
        target = os.path.realpath(link)
        commit = git.out(target, 'rev-parse', 'HEAD') if os.path.isdir(target) else None
        if commit:
            found.append({'path': target, 'commit': commit})
    return found


def git_bundle(root, refs, path, exclude=('--remotes',)):
    """Writes a git bundle of refs ({name: commit}) less what `exclude` (revision arguments after --not; none carries everything) already
    holds, the remotes by default; whether there was anything to carry."""
    for name, commit in refs.items():
        git.ok(root, 'update-ref', f'{OUT}/{name}', commit)
    try:
        return git.ok(
            root,
            'bundle',
            'create',
            '-q',
            path,
            *[f'{OUT}/{n}' for n in refs],
            *(['--not', *exclude] if exclude else []),
        )
    finally:
        for name in refs:
            git.ok(root, 'update-ref', '-d', f'{OUT}/{name}')


def local_branches(root):
    """{name: full commit id} of every local branch (refs/heads/), those origin already holds too: a branch name is state the objects do not
    carry."""
    text = git.out(
        root, 'for-each-ref', '--format=%(refname)%09%(objectname)', 'refs/heads/'
    )
    found = {}
    for line in (text or '').splitlines():
        ref, _, oid = line.partition('\t')
        found[ref[len('refs/heads/') :]] = oid
    return found


def origin_baseline(root):
    """The commit of origin's default branch as it stands after a fresh fetch, which the receiver can fetch too: the one thing a hand-over's
    bundle may leave out. '' when origin cannot be fetched or names no default branch, and the bundle then carries everything."""
    if not git.ok(root, 'fetch', '-q', 'origin'):
        return ''
    ref = git.out(root, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD')
    if not ref:
        ref = next(
            (
                r
                for r in ('refs/remotes/origin/main', 'refs/remotes/origin/master')
                if git.ok(root, 'rev-parse', '--verify', '-q', r)
            ),
            '',
        )
    return (
        git.out(root, 'rev-parse', '--verify', '-q', f'{ref}^{{commit}}') if ref else ''
    ) or ''


def pack(store, path, sender, to, agents, text='', branches=False):
    """Writes the bundle of store to path, from `sender` ({name, thread}) to the agent `to` ({name, pane}), carrying the agents this side
    knows ({name: {pane, thread}}) and the text of the message it travels with. Returns its manifest. A master's hand-over (`branches`) also
    carries every local branch: its name and tip in the manifest (git.branches), its commits in the same git bundle, cut only against
    origin's default branch (git.baseline), never against the remotes this side happens to have."""
    files, listed = store_files(store)
    root = repo_of(store)
    head = git.out(root, 'rev-parse', 'HEAD') if root else None
    trees = worktrees(store) if root else []
    manifest = {
        'version': 1,
        'id': uuid.uuid4().hex,
        'from': sender.get('thread', ''),
        'from_name': sender.get('name', ''),
        'to': to,
        'store': store,
        'agents': agents,
        'text': text,
        'files': sorted(listed),
        'git': {
            'root': root,
            'head': head or '',
            'branch': (git.out(root, 'symbolic-ref', '--short', '-q', 'HEAD') or '')
            if head
            else '',
            'worktrees': trees,
        },
    }
    refs = {'head': head, **{f'wt{i}': t['commit'] for i, t in enumerate(trees)}}
    exclude = ('--remotes',)
    if branches and head:
        tips = local_branches(root)
        baseline = origin_baseline(root)
        manifest['git'].update(branches=tips, baseline=baseline)
        refs.update({f'br/{name}': oid for name, oid in tips.items()})
        exclude = (baseline,) if baseline else ()
    with tempfile.TemporaryDirectory() as tmp:
        carried = bool(head) and git_bundle(
            root, refs, os.path.join(tmp, GIT_BUNDLE), exclude
        )
        with tarfile.open(path, 'w:gz') as tar:
            add_bytes(tar, MANIFEST, json.dumps(manifest, indent=1).encode())
            for rel, data in sorted(files.items()):
                if data.startswith(LINK):
                    info = tarfile.TarInfo(f'store/{rel}')
                    info.type, info.linkname = (
                        tarfile.SYMTYPE,
                        data[len(LINK) :].decode(),
                    )
                    tar.addfile(info)
                else:
                    add_bytes(tar, f'store/{rel}', data)
            if carried:
                tar.add(os.path.join(tmp, GIT_BUNDLE), GIT_BUNDLE)
    return manifest


def add_bytes(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def merge_json(base, local, incoming):
    """The three-way merge of two JSON values against their base, key by key in objects; MISSING is a key one side lacks."""
    if incoming == base:
        return local
    if local == base:
        return incoming
    if all(isinstance(v, dict) for v in (base, local, incoming)):
        merged = {}
        for key in [*local, *[k for k in incoming if k not in local]]:
            value = merge_json(
                base.get(key, MISSING),
                local.get(key, MISSING),
                incoming.get(key, MISSING),
            )
            if value is not MISSING:
                merged[key] = value
        return merged
    return incoming


def object_at(doc, key):
    value = doc.get(key) if isinstance(doc, dict) else None
    return value if isinstance(value, dict) else {}


def local_pair(store):
    try:
        with open(os.path.join(store, 'pair.json'), encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def sent_by(manifest, name, thread, recorded):
    """Whether the bundle came from the agent called `name` at the receiver: the name matches, and so does the thread where one is recorded
    (`thread`, else what the receiver's registry holds for the name: recorded(name))."""
    sender, sent_from = manifest.get('from_name') or '', manifest.get('from') or ''
    known = thread or recorded(name)
    return bool(name) and sender == name and (not known or known == sent_from)


def role_reply(manifest, recorded):
    """The Reply this transfer is, or None when it is not a role's reply to an established master (and is merged as a store always was): the
    first sync of a store this side lacks, a message to a role, and one master's hand-over to the next. The receiver is the master when it has
    a pair.json and the bundle is addressed to that master's name or to the one it hands over from; a bundle from the master (or the previous
    master, by name and recorded thread) is not a reply. Judged from this side's own store before the merge, never from the bundle's registry.
    `recorded(name)` is the thread this side's registry holds for an agent name."""
    pair = local_pair(manifest.get('store') or '')
    master = object_at(pair, 'master')
    previous = object_at(master, 'previous')
    to = object_at(manifest, 'to').get('name') or ''
    if not (pair and to and to in (master.get('name'), previous.get('name'))):
        return None
    pane = previous.get('pane_id') or ''
    old_thread = pane[len('amp:') :] if pane.startswith('amp:T-') else ''
    if sent_by(manifest, master.get('name') or '', '', recorded) or sent_by(
        manifest, previous.get('name') or '', old_thread, recorded
    ):
        return None
    for role in ROLES:
        name = object_at(pair, role).get('name') or ''
        if sent_by(manifest, name, '', recorded):
            return Reply(role)
    return Reply('')


def is_master_owned(rel):
    """Whether the master owns the path (gitstore.RULES); only the shared append logs are not, though they are listed there too."""
    from . import gitstore

    rule = gitstore.classify(rel)
    return rel not in SHARED_LOGS and rule is not None and rule.kind == gitstore.MASTER


def owned_by(rel, role):
    """Whether the role named by a reply owns the path (gitstore.RULES). A `.seen` marker is the master's even where progress/ is the sidekick's,
    and a sender that is no role owns nothing."""
    from . import gitstore

    rule = gitstore.classify(rel)
    return (
        bool(role)
        and not rel.endswith('.seen')
        and rule is not None
        and rule.kind == gitstore.ROLE
        and role in rule.roles
    )


def completion(local, incoming):
    """Whether the incoming pair.json holds the current sidekick's completion: rotation_required true, with the sidekick's generation, the
    dispatched brief and the dispatch's generation present and equal on both sides."""
    sides = []
    for doc in (local, incoming):
        sidekick, dispatch = object_at(doc, 'sidekick'), object_at(doc, 'dispatch')
        sides.append(
            (
                sidekick.get('generation'),
                dispatch.get('generation'),
                dispatch.get('brief'),
                sidekick.get(SIDEKICK_FACT),
            )
        )
    (gen, dispatch_gen, brief, _), (got_gen, got_dispatch_gen, got_brief, fact) = sides
    return (
        fact is True
        and brief
        and isinstance(gen, int)
        and not isinstance(gen, bool)
        and (got_gen, got_dispatch_gen, got_brief) == (gen, gen, brief)
        and dispatch_gen == gen
    )


def merge_reply_pair(local, incoming, reply):
    """(pair.json to keep, whether the sidekick's completion was taken): the receiver's own document, with SIDEKICK_FACT set when a reply from the
    current sidekick carries its completion."""
    if local is None:
        return None, False
    try:
        mine = json.loads(local)
        theirs = json.loads(incoming) if incoming is not None else None
    except ValueError:
        return local, False
    if not (
        isinstance(mine, dict) and isinstance(theirs, dict) and reply.from_sidekick
    ):
        return local, False
    sidekick = object_at(mine, 'sidekick')
    if sidekick.get(SIDEKICK_FACT) is True or not completion(mine, theirs):
        return local, False
    sidekick[SIDEKICK_FACT] = True
    return (json.dumps(mine, indent=2) + '\n').encode(), True


def merge_file(rel, base, local, incoming):
    """(content to keep or None to delete, whether both sides changed it) of one file; base, local, incoming are bytes or None."""
    if local == incoming:
        return local, False
    if rel.endswith('.tsv') and incoming is not None:
        if local is None:
            return incoming, False
        have = set(local.split(b'\n'))
        extra = [line for line in incoming.split(b'\n') if line and line not in have]
        joined = local if local.endswith(b'\n') or not local else local + b'\n'
        return joined + b''.join(line + b'\n' for line in extra), False
    if incoming == base:
        return local, False
    if local == base:
        return incoming, False
    if rel == 'pair.json' and None not in (base, local, incoming):
        try:
            b, l, i = (json.loads(x) for x in (base, local, incoming))
        except ValueError:
            return incoming, True
        if isinstance(b, dict) and isinstance(l, dict) and isinstance(i, dict):
            return (json.dumps(merge_json(b, l, i), indent=2) + '\n').encode(), False
    return incoming, base is not None


def write_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.islink(path) or (data.startswith(LINK) and os.path.exists(path)):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    if data.startswith(LINK):
        os.symlink(data[len(LINK) :].decode(), path)
        return
    tmp = f'{path}.pstack-sync.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def merge_store(store, files, listed, base_files, reply=None):
    """Merges the sender's files into store; (lines to print). A reply (role_reply) changes only the paths its sender's role owns (owned_by): pair.json takes only the
    sidekick's completion, any other file stays as this side has it (a file this side lacks is added, except a master-owned one), nothing is
    deleted by omission, and a shared log the reply lacks stays as it is."""
    local, _ = store_files(store)
    changed, removed, conflicts, refused, completed = 0, 0, [], [], False
    for rel in sorted(set(local) | set(files) | set(base_files)):
        path = os.path.join(store, rel)
        if rel not in files and rel in listed:
            continue  # the sender has it but kept it home (too big): leave ours alone
        if reply is not None and rel == 'pair.json':
            keep, completed = merge_reply_pair(local.get(rel), files.get(rel), reply)
            conflict = False
        elif reply is not None and is_master_owned(rel):
            keep, conflict = local.get(rel), False
            if files.get(rel) != keep:
                refused.append(rel)
        elif reply is not None and rel in SHARED_LOGS and rel not in files:
            keep, conflict = local.get(rel), False
        elif (
            reply is not None
            and rel not in SHARED_LOGS
            and not owned_by(rel, reply.role)
        ):
            # Another role's file, or the master's marker in a role's directory: this side's copy stays, a file it lacks is added, none is deleted.
            keep, conflict = local.get(rel, files.get(rel)), False
            if rel in local and rel in files and files[rel] != keep:
                refused.append(rel)
        else:
            keep, conflict = merge_file(
                rel, base_files.get(rel), local.get(rel), files.get(rel)
            )
        if conflict:
            conflicts.append(rel)
        if keep == local.get(rel):
            continue
        if keep is None:
            if os.path.lexists(path):
                os.unlink(path)
                removed += 1
            continue
        write_file(path, keep)
        changed += 1
    line = f'store: {changed} updated, {removed} removed'
    if conflicts:
        line += f'; conflicts, took theirs: {", ".join(conflicts)}'
    if refused:
        line += f"; kept the master's copy of: {', '.join(refused)}"
    if completed:
        line += "; took the sidekick's completion"
    return [line]


def tag_of(thread):
    return ''.join(c if c.isalnum() or c in '-_' else '-' for c in thread) or 'peer'


def fetch(root, bundle, tag):
    """Fetches the bundle's refs under refs/pstack/in/<tag>/; a shallow or stale clone fetches its remote first and tries again."""
    spec = f'+{OUT}/*:refs/pstack/in/{tag}/*'
    if git.ok(root, 'fetch', '-q', bundle, spec):
        return True
    shallow = git.out(root, 'rev-parse', '--is-shallow-repository') == 'true'
    git.ok(root, 'fetch', '-q', *(['--unshallow'] if shallow else []), 'origin')
    return git.ok(root, 'fetch', '-q', bundle, spec)


def has_commit(root, rev):
    return bool(rev) and git.ok(root, 'cat-file', '-e', f'{rev}^{{commit}}')


def own_commits(root, rev, head):
    """Whether rev holds commits that neither the remotes nor head hold: work only this side has."""
    return bool(git.out(root, 'rev-list', '-n1', rev, '--not', '--remotes', head))


def move_checkout(root, head, branch, sender):
    """Takes the sender's HEAD into this checkout when that loses nothing: a clean tree whose commits the sender's head (or the remotes)
    already hold. One line saying what happened."""
    short = head[:9]
    local = git.out(root, 'rev-parse', 'HEAD') or ''
    if local == head:
        return f'checkout: at {short}, as {sender}'
    if local and git.ok(root, 'merge-base', '--is-ancestor', head, local):
        return f'checkout: kept {local[:9]}, ahead of {sender} ({short})'
    dirty = git.out(root, 'status', '--porcelain', '--untracked-files=no')
    if dirty is None or dirty:
        return (
            f'checkout: kept {local[:9]}: tracked changes here; {sender} is at {short}'
        )
    if local and own_commits(root, local, head):
        return f'checkout: kept {local[:9]}: it diverged from {sender} ({short})'
    if branch and has_commit(root, branch) and own_commits(root, branch, head):
        return f'checkout: kept {local[:9]}: branch {branch} here diverged from {sender} ({short})'
    moved = (
        git.ok(root, 'checkout', '-q', '-B', branch, head)
        if branch
        else git.ok(root, 'checkout', '-q', '--detach', head)
    )
    if not moved:
        return f'checkout: kept {local[:9]}: git could not check out {short}'
    return f'checkout: {branch or "detached HEAD"} at {short}, as {sender}'


def apply_git(manifest, gitdata, sender):
    """Lines saying how the sender's commits and scratch worktrees came over."""
    info = manifest.get('git') if isinstance(manifest.get('git'), dict) else {}
    root, head = info.get('root') or '', info.get('head') or ''
    if not head:
        return []
    if not os.path.isdir(root):
        return [
            f'checkout: {root or "no checkout"} is not on this machine; commits not applied'
        ]
    lines = []
    if gitdata is not None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, GIT_BUNDLE)
            with open(path, 'wb') as f:
                f.write(gitdata)
            if not fetch(root, path, tag_of(manifest.get('from') or '')):
                lines.append('commits: the git bundle did not apply')
    if not has_commit(root, head):
        git.ok(root, 'fetch', '-q', 'origin')
    if not has_commit(root, head):
        return [*lines, f'checkout: {head[:9]} was not carried and the remote lacks it']
    lines.append(move_checkout(root, head, info.get('branch') or '', sender))
    for tree in info.get('worktrees') or []:
        path, commit = tree.get('path') or '', tree.get('commit') or ''
        if not path or os.path.exists(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if has_commit(root, commit) and git.ok(
            root, 'worktree', 'add', '-q', '--detach', path, commit
        ):
            lines.append(f'worktree: {path} at {commit[:9]}')
        else:
            lines.append(f'worktree: {path}: {commit[:9]} could not be checked out')
    return lines


def apply(path, base=None, reply=None):
    """Merges the bundle at path into this machine, against the bundle this side last sent to its sender (base, a path or None), as a role's
    reply to the master when `reply` (role_reply) says so. (manifest, lines to print)."""
    manifest, files, gitdata = read_bundle(path)
    base_files = read_bundle(base)[1] if base and os.path.isfile(base) else {}
    store = manifest.get('store') or ''
    if not os.path.isabs(store):
        raise ValueError(f'{path}: the manifest names no store')
    os.makedirs(store, exist_ok=True)
    sender = manifest.get('from_name') or manifest.get('from') or 'the sender'
    lines = merge_store(
        store, files, set(manifest.get('files') or ()), base_files, reply
    )
    lines += apply_git(manifest, gitdata, sender)
    return manifest, lines
