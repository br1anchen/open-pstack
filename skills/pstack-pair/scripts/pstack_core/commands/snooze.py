"""Under Amp threads the master snoozes a role thread once it has no open work and its turn is settled, and unsnoozes it before the kitchen sends it
work, so the thread list shows the active role threads and the master threads and nothing else. The amp CLI cannot snooze, so the master does it
with its own Amp thread tool, and the kitchen records what it did in snooze.tsv, as nest.py does for parents. A master thread and a thread blocked
on a question or an error are never snoozed; PSTACK_SNOOZE_ROLES=0 turns the snoozing off."""

import os
import sys
import time
from dataclasses import dataclass

from .. import mux
from ..errors import Die, SnoozeRefused
from ..store import event, load, pair_file

TSV = 'snooze.tsv'
SNOOZED, AWAKE = 'snoozed', 'awake'
SNOOZE = 'PSTACK_SNOOZE_ROLES'


@dataclass(frozen=True)
class Row:
    thread: str
    role: str
    state: str


def enabled():
    """Snoozing is on unless PSTACK_SNOOZE_ROLES is 0."""
    return os.environ.get(SNOOZE) != '0'


def rows(store):
    """The rows of snooze.tsv (time, thread, role, state), in the order written. The file only grows, so the union of lines a bundle merge makes of two
    copies keeps every change."""
    found = []
    try:
        with open(f'{store}/{TSV}', encoding='utf-8') as f:
            for line in f:
                cells = line.rstrip('\n').split('\t')
                if len(cells) == 4:
                    found.append(Row(*cells[1:]))
    except OSError:
        pass
    return found


def state_of(store, thread):
    """The newest recorded state of the thread, '' when it has none."""
    return next((r.state for r in reversed(rows(store)) if r.thread == thread), '')


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def why_not(store, thread, role):
    """Why the thread may not be snoozed now, '' when it may: it is a master; its turn is not settled (working, in error, cancelled or unknown);
    a message or relay is pending to it; or, when it is a live role, the role has work open: the sidekick's dispatched unit is not done and
    reviewed, a consult waits on the consultant, a verification is open."""
    from ..mux import amp

    if role == 'master':
        return 'a master thread'
    state, since = amp.thread_state(amp.export(thread))
    if state != 'idle':
        return f'its state is {state or "unknown"}'
    if why := amp.pending_to(store, thread, since):
        return why
    doc = amp.load_json(f'{store}/pair.json')
    live = amp.live_threads(doc)
    if thread not in live:
        return ''
    role = amp.role_of(live[thread])
    if role == 'master':
        return 'a master thread'
    if role == 'sidekick':
        brief = (doc.get('dispatch') or {}).get('brief') or ''
        stem = os.path.basename(brief).removesuffix('.md')
        if not amp.role_is_idle(store, doc, 'sidekick'):
            return 'its unit is not done'
        if not stem or not os.path.isfile(f'{store}/reviews/{stem}.md'):
            return 'its unit has no review note'
    elif role == 'consultant' and not amp.role_is_idle(store, doc, 'consultant'):
        return 'a consult waits for its advice'
    elif role == 'verifier' and doc.get('verifying'):
        return 'a verification is open'
    return ''


def candidates(store, rows=None):
    """[(thread, role)] of the role threads the master may snooze now and has not recorded snoozed: the rows `threads` lists (`rows`, when the caller
    has them), minus those with a `no store recorded` row, those already recorded snoozed, and any why_not refuses (so masters too). Empty with
    snoozing off. The rows come from `thread_rows`, which is empty unless the caller runs in the master's own thread."""
    if not enabled():
        return []
    rows = mux.get().thread_rows(store) if rows is None else rows
    return [
        (row.thread, row.role)
        for row in rows
        if row.why != 'no store recorded'
        and state_of(store, row.thread) != SNOOZED
        and not why_not(store, row.thread, row.role)
    ]


def status_lines(store):
    """The lines `status` adds: `snooze-due:` while threads wait to be snoozed, and a `needs-human:` line for each live role thread in error, which
    only the human can settle (whatever the switch says). Nothing outside an Amp master."""
    from ..mux import amp

    rows = mux.get().thread_rows(store)
    if not rows:
        return ''
    out = ''
    with amp.exports_cached():
        due = candidates(store, rows)
        if due:
            out += f'snooze-due: {len(due)} role thread{"" if len(due) == 1 else "s"}; run kitchen.sh snooze {store}\n'
        for thread, name in amp.live_threads(load(store)).items():
            role = amp.role_of(name)
            if role != 'master' and amp.thread_state(amp.export(thread))[0] == 'error':
                out += f'needs-human: {role} {thread} (error); relay it to the human from the master thread\n'
    return out


def snooze(ctx, args):
    """snooze <store> [--done <thread>... | --wake <thread>...]: the role threads to snooze with update_thread(thread=<T>, snooze="indefinite");
    --done records them snoozed, --wake awake (after update_thread(..., snooze="off")). Each thread is exported once for the whole command."""
    from ..mux import amp

    usage = (
        f'usage: {ctx.prog} snooze <store> [--done <thread>... | --wake <thread>...]'
    )
    if not args:
        raise Die(usage)
    store, rest = args[0], args[1:]
    pair_file(store)
    with amp.exports_cached():
        if rest and rest[0] in ('--done', '--wake') and len(rest) > 1:
            roles = known_roles(store)
            unknown = [t for t in rest[1:] if t not in roles]
            if unknown:
                raise Die(f'not recorded for snoozing: {" ".join(unknown)}')
            return record(store, rest[0] == '--done', rest[1:], roles)
        if rest:
            raise Die(usage)
        if not enabled():
            sys.stdout.write(f'snooze: off ({SNOOZE}=0)\n')
            return 0
        todo = candidates(store)
    if not todo:
        sys.stdout.write('snooze: nothing to snooze\n')
        return 0
    for thread, role in todo:
        sys.stdout.write(f'snooze: {thread} ({role})\n')
    sys.stdout.write(
        f'snooze: call update_thread(thread=<T>, snooze="indefinite") for each, then {ctx.prog} snooze {store} --done <thread>...\n'
    )
    return 0


def known_roles(store):
    """{thread: role} of the threads the store's registry names or an earlier row records."""
    roles = {row.thread: row.role for row in mux.get().thread_rows(store)}
    roles.update({row.thread: row.role for row in rows(store)})
    return roles


def record(store, snoozing, threads, roles):
    """Appends a snoozed (or awake) row for each of threads and logs a `snooze` (or `wake`) event; a thread already in that state, or one that
    was never snoozed and is woken, is left as it is. Snoozing a master, or a thread that has work open or a turn that is not settled, is refused
    before any row is written; waking is never checked."""
    want, word = (SNOOZED, 'snoozed') if snoozing else (AWAKE, 'awake')
    for thread in dict.fromkeys(threads) if snoozing else ():
        if state_of(store, thread) == SNOOZED:
            continue
        undo = f'; if you already called update_thread(... snooze="indefinite"), undo it: update_thread(thread={thread}, snooze="off")'
        if roles[thread] == 'master':
            raise SnoozeRefused(
                f'snooze: {thread} is a master thread; masters are never snoozed{undo}'
            )
        if reason := why_not(store, thread, roles[thread]):
            raise SnoozeRefused(
                f'snooze: {thread} is not snoozable now ({reason}){undo}'
            )
    for thread in dict.fromkeys(threads):
        if state_of(store, thread) == want or (
            want == AWAKE and not state_of(store, thread)
        ):
            sys.stdout.write(f'{word}: {thread} (already)\n')
            continue
        with open(f'{store}/{TSV}', 'a', encoding='utf-8') as f:
            f.write(f'{stamp()}\t{thread}\t{roles[thread]}\t{want}\n')
        event(store, 'master', 'snooze' if snoozing else 'wake', '-', thread)
        sys.stdout.write(f'{word}: {thread}\n')
    return 0
