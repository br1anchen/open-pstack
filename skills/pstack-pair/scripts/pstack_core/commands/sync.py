"""sync: the first move of an Amp thread that a prompt reached (mux/amp.py). It merges the bundle the prompt carries (bundle.py) into this
orb: the store's files, the sender's commits and the scratch worktrees the store links, and the agents the sender knows. A prompt for an agent
whose name now means another thread (a master that handed over) is forwarded there."""

import os
import subprocess
import tempfile

from .. import bundle
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


def sync(ctx, args):
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} sync <bundle-path-or-attachment-url>')
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
