"""Under Amp threads the master snoozes a role thread once it has no open work and its turn is settled, and unsnoozes it before the kitchen sends it
work, so the thread list shows the active role threads and the master threads and nothing else. The amp CLI cannot snooze, so the master does it
with its own Amp thread tool, and the kitchen records what it did in snooze.tsv, as nest.py does for parents. A master thread and a thread blocked
on a question or an error are never snoozed; PSTACK_SNOOZE_ROLES=0 turns the snoozing off."""

import os
import sys
import time
from dataclasses import dataclass

from .. import mux
from ..errors import Die
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


def snoozable(store, thread, role):
    """Whether the thread has no open work and a settled turn: not a master; idle (not working, in error, cancelled or unknown); no message or
    relay pending to it; and, when it is a live role, the role has nothing open: the sidekick's dispatched unit is done and reviewed, the
    consultant has no consult waiting, a verifier no verification open."""
    from ..mux import amp

    if role == 'master':
        return False
    state, since = amp.thread_state(amp.export(thread))
    if state != 'idle' or amp.pending_to(store, thread, since):
        return False
    doc = amp.load_json(f'{store}/pair.json')
    live = amp.live_threads(doc)
    if thread not in live:
        return True
    role = amp.role_of(live[thread])
    if role == 'sidekick':
        brief = (doc.get('dispatch') or {}).get('brief') or ''
        stem = os.path.basename(brief).removesuffix('.md')
        return (
            amp.role_is_idle(store, doc, 'sidekick')
            and bool(stem)
            and os.path.isfile(f'{store}/reviews/{stem}.md')
        )
    if role == 'consultant':
        return amp.role_is_idle(store, doc, 'consultant')
    return role != 'verifier' or not doc.get('verifying')


def candidates(store):
    """[(thread, role)] of the role threads the master may snooze now and has not recorded snoozed: the rows `threads` lists, minus masters and
    threads that are not this store's. Empty with snoozing off, and for any thread but the master's."""
    if not enabled():
        return []
    return [
        (row.thread, row.role)
        for row in mux.get().thread_rows(store)
        if row.why != 'no store recorded'
        and state_of(store, row.thread) != SNOOZED
        and snoozable(store, row.thread, row.role)
    ]


def status_lines(store):
    """The lines `status` adds: `snooze-due:` while threads wait to be snoozed, and a `needs-human:` line for each live role thread in error, which
    only the human can settle (whatever the switch says). Nothing outside an Amp master."""
    from ..mux import amp

    out = ''
    if not mux.get().thread_rows(store):
        return out
    due = candidates(store)
    if due:
        out += f'snooze-due: {len(due)} role thread{"" if len(due) == 1 else "s"}; run kitchen.sh snooze {store}\n'
    for thread, name in amp.live_threads(load(store)).items():
        role = amp.role_of(name)
        if role != 'master' and amp.thread_state(amp.export(thread))[0] == 'error':
            out += f'needs-human: {role} {thread} (error); relay it to the human from the master thread\n'
    return out


def snooze(ctx, args):
    """snooze <store> [--done <thread>... | --wake <thread>...]: the role threads to snooze with update_thread(thread=<T>, snooze="indefinite");
    --done records them snoozed, --wake awake (after update_thread(..., snooze="off"))."""
    usage = (
        f'usage: {ctx.prog} snooze <store> [--done <thread>... | --wake <thread>...]'
    )
    if not args:
        raise Die(usage)
    store, rest = args[0], args[1:]
    pair_file(store)
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
    was never snoozed and is woken, is left as it is."""
    want, word = (SNOOZED, 'snoozed') if snoozing else (AWAKE, 'awake')
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
