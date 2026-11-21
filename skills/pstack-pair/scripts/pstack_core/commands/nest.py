"""Under Amp threads a run reads as one tree in the thread list: the run's first master thread is the anchor, and every role thread and later
master generation is a child of it, never of the previous generation. The amp CLI cannot set a parent, so the kitchen records each thread it
starts as nest-pending, and the master sets the parents with its own Amp thread tool (`nest`)."""

import os
import sys
import time
from dataclasses import dataclass

from ..errors import Die
from ..store import event, listing, load, member, pair_file, peek, text_at, update

TSV = 'nest-pending.tsv'
PENDING, NESTED = 'pending', 'nested'
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
    """The rows still to nest, in the order they were recorded: a thread with a pending row and no nested one."""
    every = rows(store)
    nested = {r.thread for r in every if r.state == NESTED}
    todo = {}
    for r in every:
        if r.state == PENDING and r.thread not in nested:
            todo.setdefault(r.thread, r)
    return list(todo.values())


def anchor(store, besides=''):
    """The run's anchor thread, `pair.json` `.anchor.thread`, set once and never changed. `init` records the first master's own thread; a store from
    before that takes the oldest master thread recorded for the run other than `besides`, else the master's own thread."""
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


def oldest_master(store):
    """The oldest master thread the run knows: the lowest generation of `.master.sessions` that names a thread, else the earliest started master
    threads.json holds for the run, else the current master's own thread. '' when none is known."""
    from ..mux import amp

    doc = load(store)
    master = peek(doc, 'master')
    sessions = sorted(
        (
            (int(e.get('generation') or 0), e['session'])
            for e in listing(master, 'sessions')
            if isinstance(e, dict) and str(e.get('session') or '').startswith('T-')
        ),
    )
    if sessions:
        return sessions[0][1]
    path = os.path.realpath(store)
    started = sorted(
        (int(e.get('started') or 0), t)
        for t, e in amp.load_json(amp.threads_path()).items()
        if e.get('role') == 'master' and e.get('store') == path and e.get('started')
    )
    if started:
        return started[0][1]
    pane = text_at(master, 'pane_id')
    return pane[len('amp:') :] if pane.startswith('amp:T-') else ''


def run_threads(store):
    """{thread: role} of every unarchived thread the run's records name: threads.json entries for this store, the sidekick threads and master
    sessions of pair.json, and the registry's agents titled for the run."""
    from ..mux import amp

    doc = load(store)
    path = os.path.realpath(store)
    found = {
        t: e.get('role') or amp.role_of(e.get('name') or '')
        for t, e in amp.load_json(amp.threads_path()).items()
        if e.get('store') == path and not e.get('archived')
    }
    for by_model in (peek(doc, 'sidekick_threads') or {}).values():
        found.update({t: 'sidekick' for t in by_model if isinstance(t, str)})
    master = peek(doc, 'master')
    for e in listing(master, 'sessions'):
        if isinstance(e, dict) and str(e.get('session') or '').startswith('T-'):
            found.setdefault(e['session'], 'master')
    pane = text_at(master, 'pane_id')
    if pane.startswith('amp:T-'):
        found.setdefault(pane[len('amp:') :], 'master')
    ours = amp.run_titles(text_at(doc, 'slug'))
    for name, e in amp.load(amp.Amp().path())['agents'].items():
        if e.get('thread') and ours.fullmatch(name):
            found.setdefault(e['thread'], amp.role_of(name))
    return found


def discover(store):
    """Records every thread the run knows (the same search `threads --discover` makes, then run_threads) that has no row yet as nest-pending, and sets
    the anchor to the oldest master thread when pair.json has none. A second run records nothing."""
    from ..mux import amp

    amp.Amp().discover(store)
    if not text_at(peek(load(store), 'anchor'), 'thread') and (
        first := oldest_master(store)
    ):

        def record(doc):
            member(doc, 'anchor').setdefault('thread', first)

        update(store, record)
    for thread, role in sorted(run_threads(store).items()):
        record_pending(store, thread, role)


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def record_pending(store, thread, role):
    """A thread the kitchen started for the run: one pending row and a `nest-pending` event, unless it is the anchor or already has a row."""
    if not os.path.isfile(f'{store}/pair.json'):
        return
    here = anchor(store, thread)
    if not here or thread == here or any(r.thread == thread for r in rows(store)):
        return
    with open(f'{store}/{TSV}', 'a', encoding='utf-8') as f:
        f.write(f'{stamp()}\t{thread}\t{role}\t{here}\t{PENDING}\n')
    event(store, 'master', 'nest-pending', '-', f'{thread} under {here}')


def status_line(store):
    """`nest-pending: N threads` for status, '' when none waits."""
    count = len(pending(store))
    return f'nest-pending: {count} thread{"" if count == 1 else "s"}\n' if count else ''


def nest(ctx, args):
    """nest <store> [--discover | --done <thread>...]: the threads waiting for a parent and how to set it; --discover first records the run's
    threads that have no row (a run migrated or started before nesting) and its anchor; --done marks threads nested."""
    usage = f'usage: {ctx.prog} nest <store> [--discover | --done <thread>...]'
    if not args:
        raise Die(usage)
    store, rest = args[0], args[1:]
    pair_file(store)
    if rest and rest[0] == '--done' and len(rest) > 1:
        unknown = [t for t in rest[1:] if t not in {r.thread for r in rows(store)}]
        if unknown:
            raise Die(f'not recorded for nesting: {" ".join(unknown)}')
        return done(store, rest[1:])
    if rest == ['--discover']:
        discover(store)
    elif rest:
        raise Die(usage)
    todo = pending(store)
    if not todo:
        sys.stdout.write('nest: nothing pending\n')
        return 0
    for r in todo:
        sys.stdout.write(f'nest-pending: {r.thread} ({r.role}) under {r.anchor}\n')
    who = f'ask {os.environ[VIA]} to ' if os.environ.get(VIA) else ''
    sys.stdout.write(
        f'nest: {who}call update_thread(thread=<T>, parent_thread=<anchor>) for each, then {ctx.prog} nest {store} --done <thread>...\n'
    )
    return 0


def done(store, threads):
    """Marks each of threads (all recorded for the run) nested; one that is nested already is left as it is."""
    known = {r.thread: r for r in rows(store)}
    waiting = {r.thread for r in pending(store)}
    for thread in dict.fromkeys(threads):
        r = known[thread]
        if thread not in waiting:
            sys.stdout.write(f'nested: {thread} (already)\n')
            continue
        with open(f'{store}/{TSV}', 'a', encoding='utf-8') as f:
            f.write(f'{stamp()}\t{thread}\t{r.role}\t{r.anchor}\t{NESTED}\n')
        event(store, 'master', 'nest', '-', f'{thread} under {r.anchor}')
        sys.stdout.write(f'nested: {thread} under {r.anchor}\n')
    return 0
