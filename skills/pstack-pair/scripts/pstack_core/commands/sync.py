"""sync: the first move of an Amp thread that a prompt reached (mux/amp.py). It merges the bundle the prompt carries (bundle.py) into this
orb: the store's files, the sender's commits and the scratch worktrees the store links, and the agents the sender knows. A prompt for an agent
whose name now means another thread (a master that handed over) is forwarded there."""

import json
import os
import subprocess
import tempfile

from .. import bundle, git, gitstore
from ..errors import BundleUnreadable, Die
from ..mux import amp
from ..mux.subagents import change, load
from ..store import TEXT


def fetch(source, into):
    """The local path of the bundle: source itself, or the Amp attachment at that URL downloaded into `into`."""
    if not source.startswith(('http://', 'https://')):
        if not os.path.isfile(source):
            raise BundleUnreadable(f'no bundle at {source}')
        return source
    r = subprocess.run(
        ['amp', 'files', 'get', source, '-o', into],
        capture_output=True,
        check=False,
        **TEXT,
    )
    got = [os.path.join(into, n) for n in os.listdir(into)]
    if r.returncode != 0 or len(got) != 1:
        raise BundleUnreadable(
            f'amp files get {source} failed: {(r.stderr or r.stdout).strip()}'
        )
    return got[0]


def learn(manifest):
    """Merges the agents the sender knows into this orb's registry. The sender's own entry is the sender's word; any other fills only a
    name this side lacks or knows no thread for; an entry whose thread is retired, archived or older than the one that holds the name is
    ignored (supersedes). The agent the bundle is for is this thread, unless the name already means another thread here. Returns that other thread, '' when the bundle is this thread's."""
    me = amp.own_thread()
    to = manifest.get('to') if isinstance(manifest.get('to'), dict) else {}
    target = to.get('name') or ''
    sender = manifest.get('from_name') or ''
    incoming = (
        manifest.get('agents') if isinstance(manifest.get('agents'), dict) else {}
    )
    path = amp.registry_path()
    known = load(path)['agents'].get(target, {}).get('thread') or ''
    elsewhere = known if known and me and known != me and target != sender else ''

    def merge(doc):
        for name, entry in incoming.items():
            if not isinstance(entry, dict) or not isinstance(entry.get('pane'), str):
                continue
            mine = doc['agents'].get(name)
            # A thread retired, archived or started before the one that holds the name does not take it back: its bundle is late.
            late = amp.supersedes(
                (mine or {}).get('thread') or '', entry.get('thread') or ''
            )
            if not late and (name == sender or mine is None or not mine.get('thread')):
                kept = mine or {}
                doc['agents'][name] = {
                    'pane': entry['pane'],
                    'kind': entry.get('kind') or kept.get('kind') or '',
                    'args': kept.get('args') or [],
                    'thread': entry.get('thread') or kept.get('thread') or '',
                    **({'store': kept['store']} if kept.get('store') else {}),
                }
            if entry['pane'] not in doc['panes']:
                doc['panes'].append(entry['pane'])
        if target and me and not elsewhere:
            entry = doc['agents'].setdefault(target, {'kind': 'amp', 'args': []})
            entry['thread'] = me
            entry['pane'] = to.get('pane') or entry.get('pane') or amp.thread_pane(me)
        doc['store'] = manifest.get('store') or doc.get('store') or ''

    change(merge, path)
    return elsewhere


def sync_branch(store, branch=''):
    """The sync of a git-backed store, with no bundle: the store worktree is checked out from origin when this orb has none (the first prompt), then
    refreshed, and the code the writers announced is fetched. The branch is the one the message announced (`store: kitchen/<slug> at <sha>`); a
    store that is already checked out keeps its own. The master is registered from pair.json, so the role can send it files."""
    if not gitstore.is_git(store):
        if not branch.startswith(gitstore.BRANCH_PREFIX):
            raise gitstore.StoreSetupError(
                f'{store} is not checked out here, so name the branch the message announced: sync --branch {store} {gitstore.BRANCH_PREFIX}<slug>'
            )
        repo = git.out(os.getcwd(), 'rev-parse', '--show-toplevel') or ''
        gitstore.attach(store, repo, branch)
    fresh = gitstore.refresh(store)
    fetched = gitstore.fetch_code(store)
    print(f'store: {gitstore.branch_of(store)} at {gitstore.head_of(store)[:9]}')
    if not (fresh and fetched):
        print(
            'warning: origin or a published bundle could not be read; what this orb holds may be older than the branch'
        )
    learn_master(store)
    return 0


def learn_master(store):
    """The master's name and thread from the store's pair.json into this orb's registry, when the master is an Amp thread."""
    try:
        with open(f'{store}/pair.json', encoding='utf-8') as f:
            master = (json.load(f).get('master')) or {}
    except (OSError, ValueError):
        return
    pane, name = master.get('pane_id') or '', master.get('name') or ''
    if not (name and pane.startswith('amp:T-')):
        return

    def add(doc):
        doc['agents'].setdefault(name, {'kind': 'amp', 'args': []}).update(
            pane=pane, thread=pane[len('amp:') :]
        )
        doc['store'] = store

    change(add, amp.registry_path())


def sync(ctx, args):
    if len(args) in (2, 3) and args[0] == '--branch':
        return sync_branch(os.path.realpath(args[1]), *args[2:])
    if len(args) != 1:
        raise Die(
            f'usage: {ctx.prog} sync <bundle-path-or-attachment-url> | sync --branch <store> kitchen/<slug>'
        )
    with tempfile.TemporaryDirectory() as tmp:
        path = fetch(args[0], tmp)
        try:
            manifest = bundle.read_bundle(path)[0]
            sent = amp.sent_path(manifest.get('from') or '')
            manifest, lines = bundle.apply(path, sent)
        except (OSError, ValueError, KeyError) as e:
            raise Die(f'{args[0]}: {e}') from None
    for line in lines:
        print(line)
    elsewhere = learn(manifest)
    target = (manifest.get('to') or {}).get('name') or ''
    if elsewhere:
        thread, err = amp.Amp().send(
            target,
            manifest.get('text') or '',
            elsewhere,
            store=manifest.get('store') or '',
        )
        if not thread:
            raise Die(
                f'{target} is now {elsewhere}, and forwarding to it failed: {err}', 2
            )
        print(
            f'forwarded to {target} ({elsewhere}): this thread no longer holds {target}; end your turn'
        )
        return 0
    print(
        f'synced: {manifest.get("store")} from {manifest.get("from_name") or manifest.get("from") or "the sender"}'
    )
    other = os.environ.get('PSTACK_MUX', '')
    if other not in ('', 'amp'):
        print(
            f'warning: this orb sets PSTACK_MUX={other}; run finish and notify here as PSTACK_MUX=amp kitchen.sh '
            '(or pair.sh) so they reach the master'
        )
    return 0
