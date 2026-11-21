"""Under Amp threads a run reads as one tree in the thread list: the current master thread is the anchor, a top-level thread that holds the
anchor title, and every role thread of the run is its child. A rotation moves the anchor to the new master (take-master), which becomes top-level;
a master is never nested under another master. The amp CLI cannot set a parent, so the kitchen records each thread it starts as nest-pending,
and the master sets the parents with its own Amp thread tool (`nest`). A row whose anchor is `-` says the thread is to be top-level."""

import os
import sys
import time
from dataclasses import dataclass

from ..errors import Die
from ..store import event, load, pair_file, peek, text_at, update

TSV = 'nest-pending.tsv'
PENDING, NESTED = 'pending', 'nested'
TOP_LEVEL = '-'
# Set to a thread id, `nest` tells the master to ask that thread to set the parents instead of calling update_thread itself.
VIA = 'PSTACK_NEST_VIA'


@dataclass(frozen=True)
class Row:
    thread: str
    role: str
    anchor: str
    state: str


def rows(store):
    """The rows of nest-pending.tsv (time, thread, role, anchor, state). The file only grows: a thread is nested by a later `nested` row, so the
    union of lines a bundle merge makes of two copies never brings a pending row back to life."""
    found = []
    try:
        with open(f'{store}/{TSV}', encoding='utf-8') as f:
            for line in f:
                cells = line.rstrip('\n').split('\t')
                if len(cells) == 5:
                    found.append(Row(*cells[1:]))
    except OSError:
        pass
    return found


def pending(store):
    """The rows still to nest, in the order they were recorded: a thread's newest pending row, unless a nested row for the same anchor follows
    it. A thread that moved to a new anchor has a row for each, and only the newest counts."""
    every = rows(store)
    nested = {(r.thread, r.anchor) for r in every if r.state == NESTED}
    todo = {}
    for r in every:
        if r.state == PENDING:
            todo.pop(r.thread, None)
            todo[r.thread] = r
    return [r for r in todo.values() if (r.thread, r.anchor) not in nested]


def anchor(store, besides=''):
    """The run's anchor thread, `pair.json` `.anchor.thread`: the master that holds the run's top-level row. `init` records the first master's own
    thread and its title; take-master moves it to the next master. A store from before that takes the oldest master thread recorded for the run
    other than `besides`, else the master's own thread."""
    found = text_at(peek(load(store), 'anchor'), 'thread')
    if found:
        return found
    from ..mux import amp

    path = os.path.realpath(store)
    started = sorted(
        (int(e.get('started') or 0), t)
        for t, e in amp.load_json(amp.threads_path()).items()
        if e.get('role') == 'master'
        and e.get('store') == path
        and e.get('started')
        and t != besides
    )
    thread = started[0][1] if started else amp.own_thread()
    if not thread:
        return ''

    def record(doc):
        holder = doc.get('anchor')
        if not isinstance(holder, dict):
            holder = doc['anchor'] = {}
        holder.setdefault('thread', thread)

    update(store, record)
    return text_at(peek(load(store), 'anchor'), 'thread')


def anchor_title(store):
    """The anchor thread's title, `pair.json` `.anchor.title`; a store without one reads it from the thread now and keeps it. '' when amp cannot say."""
    holder = peek(load(store), 'anchor')
    title = text_at(holder, 'title')
    thread = text_at(holder, 'thread')
    if title or not thread:
        return title
    from ..mux import amp

    title = amp.thread_title(thread)
    if title:
        update(store, lambda doc: doc['anchor'].update(title=title))
    return title


def take_anchor(store, thread):
    """The next master `thread` takes over the run's anchor: `.anchor.thread` moves to it and the old anchor is added to `.anchor.previous`; the thread
    is renamed to the anchor title and made top-level; each role thread the run still uses is nest-pending under it. (the title, '' when there is
    none; the warning, '' when the rename worked.)"""
    from ..mux import amp

    old = anchor(store, thread)
    title = anchor_title(store)

    def move(doc):
        holder = doc.setdefault('anchor', {})
        if holder.get('thread') != thread:
            if old and old != thread:
                holder['previous'] = [*(holder.get('previous') or []), old]
            holder['thread'] = thread

    update(store, move)
    warning = ''
    if not title:
        warning = f'the anchor has no title; {thread} was not renamed'
    else:
        renamed, why = amp.rename_thread(thread, title)
        if not renamed:
            warning = f'amp threads rename {thread} failed: {why}'
    add_pending(store, thread, 'master', TOP_LEVEL)
    for role_thread, name in amp.live_threads(load(store)).items():
        if amp.role_of(name) != 'master' and role_thread != thread:
            add_pending(store, role_thread, amp.role_of(name), thread)
    return title, warning


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def record_pending(store, thread, role):
    """A thread the kitchen started for the run: one pending row and a `nest-pending` event, unless it is the anchor or already has a row."""
    if not os.path.isfile(f'{store}/pair.json'):
        return
    here = anchor(store, thread)
    if not here or thread == here:
        return
    add_pending(store, thread, role, here)


def add_pending(store, thread, role, parent):
    """A pending row for `thread` under `parent` (TOP_LEVEL for none), unless it has a row for that parent already."""
    if any(r.thread == thread and r.anchor == parent for r in rows(store)):
        return
    with open(f'{store}/{TSV}', 'a', encoding='utf-8') as f:
        f.write(f'{stamp()}\t{thread}\t{role}\t{parent}\t{PENDING}\n')
    event(store, 'master', 'nest-pending', '-', f'{thread} {under(parent)}')


def under(parent):
    return 'top-level' if parent == TOP_LEVEL else f'under {parent}'


def status_line(store):
    """`nest-pending: N threads` for status, '' when none waits."""
    count = len(pending(store))
    return f'nest-pending: {count} thread{"" if count == 1 else "s"}\n' if count else ''


def nest(ctx, args):
    """nest <store> [--done <thread>...]: the threads waiting for a parent and how to set it; --done marks threads nested."""
    usage = f'usage: {ctx.prog} nest <store> [--done <thread>...]'
    if not args:
        raise Die(usage)
    store, rest = args[0], args[1:]
    pair_file(store)
    if rest and rest[0] == '--done' and len(rest) > 1:
        unknown = [t for t in rest[1:] if t not in {r.thread for r in rows(store)}]
        if unknown:
            raise Die(f'not recorded for nesting: {" ".join(unknown)}')
        return done(store, rest[1:])
    if rest:
        raise Die(usage)
    todo = pending(store)
    if not todo:
        sys.stdout.write('nest: nothing pending\n')
        return 0
    for r in todo:
        sys.stdout.write(f'nest-pending: {r.thread} ({r.role}) {under(r.anchor)}\n')
    who = f'ask {os.environ[VIA]} to ' if os.environ.get(VIA) else ''
    then = f'then {ctx.prog} nest {store} --done <thread>...'
    if any(r.anchor != TOP_LEVEL for r in todo):
        sys.stdout.write(
            f'nest: {who}call update_thread(thread=<T>, parent_thread=<anchor>) for each, {then}\n'
        )
    if any(r.anchor == TOP_LEVEL for r in todo):
        sys.stdout.write(
            f'nest: {who}call update_thread(thread=<T>, parent_thread=null) for each top-level row, {then}\n'
        )
    return 0


def done(store, threads):
    """Marks each of threads (all recorded for the run) nested; one that is nested already is left as it is."""
    waiting = {r.thread: r for r in pending(store)}
    for thread in dict.fromkeys(threads):
        r = waiting.get(thread)
        if r is None:
            sys.stdout.write(f'nested: {thread} (already)\n')
            continue
        with open(f'{store}/{TSV}', 'a', encoding='utf-8') as f:
            f.write(f'{stamp()}\t{thread}\t{r.role}\t{r.anchor}\t{NESTED}\n')
        event(store, 'master', 'nest', '-', f'{thread} {under(r.anchor)}')
        sys.stdout.write(f'nested: {thread} {under(r.anchor)}\n')
    return 0
