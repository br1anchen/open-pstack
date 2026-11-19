"""Under Amp threads a run reads as one tree in the thread list: the run's first master thread is the anchor, and every role thread and later
master generation is a child of it, never of the previous generation. The amp CLI cannot set a parent, so the kitchen records each thread it
starts as nest-pending, and the master sets the parents with its own Amp thread tool (`nest`)."""

import os
import time
from dataclasses import dataclass

from ..store import event, load, peek, text_at, update

TSV = 'nest-pending.tsv'
PENDING, NESTED = 'pending', 'nested'


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


def anchor(store):
    """The run's anchor thread, `pair.json` `.anchor.thread`: `init` records the first master's own thread. A store from before that takes, the first
    time it needs one, the oldest master thread the kitchen started for the run (threads.json), else the master's own thread, and records it.
    Once set it never changes."""
    found = text_at(peek(load(store), 'anchor'), 'thread')
    if found:
        return found
    from ..mux import amp

    path = os.path.realpath(store)
    started = sorted(
        (int(e.get('started') or 0), t)
        for t, e in amp.load_json(amp.threads_path()).items()
        if e.get('role') == 'master' and e.get('store') == path and e.get('started')
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


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def record_pending(store, thread, role):
    """A thread the kitchen started for the run: one pending row and a `nest-pending` event, unless it is the anchor or already has a row."""
    if not os.path.isfile(f'{store}/pair.json'):
        return
    here = anchor(store)
    if not here or thread == here or any(r.thread == thread for r in rows(store)):
        return
    with open(f'{store}/{TSV}', 'a', encoding='utf-8') as f:
        f.write(f'{stamp()}\t{thread}\t{role}\t{here}\t{PENDING}\n')
    event(store, 'master', 'nest-pending', '-', f'{thread} under {here}')
