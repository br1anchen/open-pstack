import contextlib
import io
import os
import re
import sys
import time
from dataclasses import replace

from .. import mux
from ..agents import current_no_mcp, model_label
from ..argv import Spec, read
from ..checkin import sidekick_busy
from ..errors import Die, RotationFailed, translate
from ..exits import end_agent, exit_deadline, stop_prime_role
from ..gates import check_scope, require_not_paused
from ..mux import DEVNULL
from ..numbers import env_float, env_int
from ..panes import agent_status, require_mux
from ..replies import queued_brief
from ..review import require_filled
from ..send import store_relative
from ..spawn import (
    SpawnRequest,
    entry_label,
    record_chain,
    record_failover,
    sidekick_chain,
    spawn_role,
    start_chain,
)
from ..store import (
    clear_rotation,
    count,
    event,
    expected_report,
    field,
    header_field,
    load,
    names,
    pair_file,
    peek,
    read_flag,
    risk_of,
    rotation_required,
    text_at,
    who,
)
from ..wait import end_with_parent
from .status import log


def rotate(ctx, args):
    """Exits the idle sidekick and reuses its pane with the same native arguments. It preserves the store and never deletes a Devin session."""
    end_with_parent()
    require_mux()
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} rotate <store>')
    store = args[0]
    pair_file(store)
    require_not_paused(store)
    kind = field(store, 'sidekick', 'kind')
    sidekick = peek(load(store), 'sidekick')
    if not rotation_required(store):
        raise Die(
            'rotation requires a completed task; partial or blocked work keeps its session',
            5,
        )
    if not isinstance(sidekick.get('start_args'), list):
        raise Die(
            'no recorded startup arguments; exit the idle sidekick and spawn it with its original model and permission flags',
            5,
        )
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    if status in ('idle', 'done'):
        if kind == 'prime-agent':
            if not stop_prime_role(store, 'sidekick'):
                raise Die('Prime session cleanup failed; no new session was started', 5)
        else:
            error, gone = end_agent(name, kind, exit_deadline())
            if error:
                raise Die(f'{kind} exit failed: {error}', 2)
            if not gone:
                raise Die(
                    f'{kind} has not exited; inspect {name} before retrying rotate', 5
                )
    elif status == 'absent':
        if kind == 'prime-agent' and not stop_prime_role(store, 'sidekick'):
            raise Die('Prime session cleanup failed; no new session was started', 5)
    else:
        raise Die(
            f'{who("sidekick", name)} is {status}; wait for its report and turn to end before rotating',
            5,
        )
    start_args = current_no_mcp(names(sidekick, 'start_args'))
    event(
        store,
        'master',
        'rotate',
        '-',
        f'generation:{field(store, "sidekick", "generation")}',
    )
    request = SpawnRequest(
        kind,
        'none',
        field(store, 'master', 'pane_id'),
        pane=field(store, 'sidekick', 'pane_id'),
        args=tuple(start_args),
    )
    chain, index = sidekick_chain(
        sidekick, model_label(kind, names(sidekick, 'start_args'))
    )
    if index == 0:
        return spawn_role(ctx, store, 'sidekick', request)
    return fail_back(ctx, store, request, chain, index)


def fail_back(ctx, store, request, chain, index):
    """The rotation of a sidekick that failed over: it starts again from the top of its chain, each entry that still fails its preflight a failover
    event as at spawn. Landing higher than it ran is a failback event and line; the sidekick stays where the walk lands."""
    top = chain[0]
    code = start_chain(
        ctx,
        store,
        replace(request, kind=top['kind'], permission='', args=tuple(top['args'])),
        chain,
        0,
    )
    landed = count(peek(load(store), 'sidekick'), 'chain_index')
    if landed < index:
        source, target = entry_label(chain[index]), entry_label(chain[landed])
        event(store, 'master', 'failback', '-', f'{source}:{target}')
        print(f'failback: the sidekick returns to {target}')
    return code


def profiles_of(brief):
    """The profiles classify wrote in a brief's header, as a set."""
    return {p for p in re.split(r'[\s,]+', header_field(brief, 'profiles')) if p}


def keeps_session(store, brief):
    """Why the sidekick keeps its session for `brief` ('' when it rotates): only where a fresh session costs minutes (mux reuses_sessions,
    Amp threads), and only when the unit it finished and this one are both routine, in the same profiles, and its context is under
    PAIR_REUSE_CONTEXT_PCT (50) percent of the window and never compacted. A context the backend cannot read rotates."""
    m = mux.get()
    previous = field(store, 'dispatch', 'brief')
    if not (m.reuses_sessions and brief and previous and os.path.isfile(previous)):
        return ''
    if risk_of(brief) != 'routine' or risk_of(previous) != 'routine':
        return ''
    area = profiles_of(brief)
    if not area or area != profiles_of(previous):
        return ''
    used = m.context_used(field(store, 'sidekick', 'name'))
    if used is None or used * 100 >= env_int('PAIR_REUSE_CONTEXT_PCT', 50):
        return ''
    return f'{", ".join(sorted(area))}, context {round(used * 100)}%'


class Sweep:
    """What the archive sweeps of one command did, so the command ends with one summary line (summary) however many sweeps it ran. Each thread
    counts in one bucket, the best outcome it reached in the command: archived over kept over in use."""

    RANK = {'in use': 0, 'kept': 1, 'archived': 2}
    ran = False
    buckets = {}
    seen = {}

    @classmethod
    def first(cls, thread, outcome):
        """Whether this command has not yet printed `thread` with this outcome ('in use', or why it is kept): a command that sweeps twice (dispatch
        does, before and after a rotation) prints each thread once, and again only if its outcome changed."""
        if cls.seen.get(thread) == outcome:
            return False
        cls.seen[thread] = outcome
        return True

    @classmethod
    def count(cls, outcomes):
        """Records {thread: 'archived' | 'kept' | 'in use'} of one sweep."""
        cls.ran = True
        for thread, bucket in outcomes.items():
            if cls.RANK[bucket] >= cls.RANK.get(cls.buckets.get(thread, ''), -1):
                cls.buckets[thread] = bucket

    @classmethod
    def summary(cls):
        """The last line a sweeping command prints, or '' when it swept nothing."""
        if not cls.ran:
            return ''
        n = list(cls.buckets.values()).count
        return f'archive: {n("archived")} archived, {n("kept")} kept, {n("in use")} in use\n'


def archive_retired(ctx, store, trigger, retired_only=True):
    """Archives each thread the store no longer uses that the rule allows (mux archivable), logging each in decisions.tsv and events.tsv with its
    thread id, and says `kept <thread>: <reason>` for each it may not archive yet; a thread a live role holds is left out. Returns the rows
    it archived. Only the master's thread archives, and only where roles are threads (Amp). The outcomes go to Sweep.summary."""
    archived, outcomes = [], {}
    m = mux.get()
    for row in m.thread_rows(store, retired_only):
        if row.in_use:
            outcomes[row.thread] = 'in use'
        elif not row.archivable:
            outcomes[row.thread] = 'kept'
            if Sweep.first(row.thread, row.why):
                print(f'kept {row.thread}: {row.why}')
        elif m.archive(row):
            archived.append(row)
            outcomes[row.thread] = 'archived'
            event(store, 'master', 'archive', '-', f'{row.role}:{row.thread}')
            try:
                log(
                    ctx,
                    [
                        store,
                        'archive',
                        f'archive {row.role} {row.thread}',
                        f'no longer used by the store; {trigger}',
                        f'amp threads archive {row.thread}',
                        'archived',
                    ],
                )
            except Die as e:
                sys.stderr.write(f'archive: {row.thread} archived; not logged: {e}\n')
            print(f'archived {row.thread} ({row.role}): {trigger}')
        else:
            outcomes[row.thread] = 'kept'
            if Sweep.first(row.thread, 'amp threads archive failed'):
                print(f'kept {row.thread}: amp threads archive failed')
    if m.archives_threads:
        Sweep.count(outcomes)
    return archived


def threads(ctx, args):
    """Lists every thread the store and the registry named, with its role, its state and whether it is in use, archivable or kept (and why).
    --register T-ID [--role ROLE] records a thread the master started outside the kitchen (a helper); --discover records the unarchived threads
    that name this run, for a store started on an older build; --archive archives each archivable one."""
    usage = f'usage: {ctx.prog} threads <store> [--archive] [--discover] [--register T-ID [--role ROLE]]'
    if not args:
        raise Die(usage)
    store = args[0]
    pair_file(store)
    opts = read(
        args[1:],
        Spec(valued=('--register', '--role'), flags=('--archive', '--discover')),
    )
    do = opts.present('--archive')
    m = mux.get()
    thread = opts.last('--register', '')
    if thread:
        if not re.fullmatch(r'T-[0-9A-Za-z-]+', thread):
            raise Die(f'--register needs a thread id (T-...), not {thread}')
        role = opts.last('--role', 'helper')
        if m.register(thread, role, store):
            print(f'registered {thread} ({role})')
    if opts.present('--discover'):
        for found, role in m.discover(store):
            print(f'discovered {found} ({role})')
    rows = m.thread_rows(store)
    if not rows:
        print(
            'no threads: only the master of an Amp run (PSTACK_MUX=amp) lists and archives them'
        )
        return 0
    for row in rows:
        verdict = (
            'in use'
            if row.in_use
            else 'archivable'
            if row.archivable
            else f'kept: {row.why}'
        )
        print(f'{row.thread}  {row.role}  {row.state or "unknown"}  {verdict}')
    if do:
        archived = archive_retired(ctx, store, 'threads --archive', retired_only=False)
        print(f'archived {len(archived)} thread(s)')
    else:
        Sweep.count(
            {
                r.thread: 'in use' if r.in_use else 'kept'
                for r in rows
                if r.in_use or not r.archivable
            }
        )
    return 0


def rotate_if_due(ctx, store, what, brief=''):
    """The sidekick is rotated before the next brief or plan once its last unit is done, so the rotation costs the master no extra wake. Partial
    or blocked work never sets rotation_required and keeps its session. A replacement whose READY is still pending is left to its bootstrap:
    send refuses until it is ready. Where a fresh session costs minutes, a routine brief in the area of the unit just done keeps the session
    while its context has room (keeps_session)."""
    if not os.path.isfile(f'{store}/pair.json'):
        return
    archive_retired(ctx, store, f'a {what} is being sent')
    if not rotation_required(store):
        return
    if read_flag(store, 'sidekick.bootstrap_pending') is True:
        return
    failed_over = count(peek(load(store), 'sidekick'), 'chain_index') > 0
    kept = '' if failed_over else keeps_session(store, brief) if what == 'brief' else ''
    if kept:
        clear_rotation(store)
        event(store, 'sidekick', 'reuse', brief, kept)
        sys.stdout.write(
            f'kept: the {field(store, "sidekick", "kind")} sidekick keeps its session for this routine unit ({kept})\n'
        )
        return
    # The report lands a moment before the sidekick's turn ends.
    mux.get().wait(
        field(store, 'sidekick', 'name'),
        ('idle', 'done'),
        60000,
        stdout=DEVNULL,
        stderr=DEVNULL,
    )
    said = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        try:
            code = rotate(ctx, [store])
        except Exception as e:
            text, code = translate(e, ctx.prog)
            said.write(text)
    if code == 4 and not mux.get().waits:
        # The new thread bootstraps on its own after rotate returns: this was a fresh session, and its READY settles the bootstrap.
        clear_rotation(store)
        sys.stderr.write(
            f'{said.getvalue()}rotated: a new sidekick thread is starting; {f"run dispatch {brief}" if brief else "send the plan"} again after its READY\n'
        )
        raise RotationFailed(4)
    if code:
        raise RotationFailed(
            code,
            f'rotating the sidekick before this {what} failed:\n{said.getvalue().rstrip(chr(10))}',
        )
    require_idle_session(store, what)
    sys.stdout.write(
        f'rotated: the {field(store, "sidekick", "kind")} sidekick starts this {what} in a fresh session\n'
    )


def require_idle_session(store, what):
    """The fresh session has logged READY while its first turn may still run, and send refuses a working sidekick. Waits up to
    PAIR_ROTATE_READY_S (60) for it to be idle or done; a session that is blocked on a dialog or gone is not waited for, and one that turns blocked during the wait ends it. Anything else sends nothing."""
    name = field(store, 'sidekick', 'name')
    state = agent_status(name)
    if state not in ('idle', 'done', 'blocked', 'absent'):
        mux.get().wait(
            name,
            ('idle', 'done', 'blocked'),
            env_int('PAIR_ROTATE_READY_S', 60) * 1000,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        state = agent_status(name)
    if state not in ('idle', 'done'):
        why = {
            'blocked': 'blocked, probably on an approval dialog',
            'absent': 'gone',
        }.get(state, state)
        raise RotationFailed(
            5,
            f'the fresh {field(store, "sidekick", "kind")} sidekick {name} is {why} after the rotation; nothing was sent for this {what}. '
            f'Inspect it, then dispatch again',
        )


def queue(ctx, args):
    """The master's next brief, held until the master dispatches it, which rotates a due sidekick first. One slot: more than one
    queued brief is a plan."""
    if len(args) < 2:
        raise Die(
            f'usage: {ctx.prog} queue <store> <brief-path> [--replace] | {ctx.prog} queue <store> --clear'
        )
    store, brief = args[0], args[1]
    pair_file(store)
    brief = store_relative(store, brief)
    if brief == '--clear':
        try:
            os.unlink(f'{store}/queue')
        except OSError:
            pass
        event(store, 'master', 'queue', '-', 'cleared')
        print('queue cleared')
        return 0
    replace = read(args[2:], Spec(flags=('--replace',))).present('--replace')
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    brief = os.path.realpath(brief)
    require_not_paused(store)
    require_filled(ctx, brief)
    pair = load(store)
    check_scope(brief, text_at(pair, 'git_root') or text_at(pair, 'cwd'))
    ctx.variant.require_agreed_plan(ctx, store, brief)
    queued = queued_brief(store)
    if queued and queued != brief and not replace:
        raise Die(f'the queue holds {queued}; pass --replace to swap it', 5)
    name = text_at(peek(pair, 'sidekick'), 'name')
    status = agent_status(name)
    busy = sidekick_busy(store, status, time.time())
    if status in ('idle', 'done') and not busy:
        raise Die(
            f'{who("sidekick", name)} is {status}; dispatch instead: pair.sh dispatch {store} {brief}',
            5,
        )
    if not busy:
        raise Die(
            f'{who("sidekick", name)} is {status}; resolve that before queueing', 3
        )
    with open(f'{store}/queue.tmp', 'w', encoding='utf-8') as f:
        f.write(f'{brief}\n')
    os.replace(f'{store}/queue.tmp', f'{store}/queue')
    event(store, 'master', 'queue', brief)
    print(
        f"queued {brief}; it waits for the master's dispatch, which rotates a due sidekick first"
    )
    return 0


def failover(ctx, args):
    """Replaces a failing sidekick with the next agent of its chain in the same pane. The new agent picks up from the store: the step log, progress,
    and the brief it was on; every committed step survives. The sidekick goes back up the chain at its next rotation (rotate_if_due), not here."""
    end_with_parent()
    require_mux()
    if not args:
        raise Die(f'usage: {ctx.prog} failover <store> [--reason TEXT] [--force]')
    store = args[0]
    opts = read(args[1:], Spec(valued=('--reason',), flags=('--force',)))
    reason, force = opts.last('--reason', 'requested'), opts.present('--force')
    pair_file(store)
    sidekick = peek(load(store), 'sidekick')
    kind = field(store, 'sidekick', 'kind')
    running_label = model_label(kind, names(sidekick, 'start_args'))
    chain, index = sidekick_chain(sidekick, running_label)
    if len(chain) < 2:
        raise Die('no fallback recorded; spawn the sidekick with --fallback KIND', 5)
    if index + 1 >= len(chain):
        raise Die(f'the sidekick runs the last of its chain ({running_label})', 5)
    target = chain[index + 1]
    fallback, target_label = target['kind'], entry_label(target)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    if status == 'working':
        if not force:
            raise Die(
                f'{who("sidekick", name)} is working; pair.sh stop {store} first, or pass --force to interrupt it',
                5,
            )
        mux.get().key(name, 'esc')
        time.sleep(env_float('PAIR_ESC_WAIT_S', 2))
    if kind == 'prime-agent':
        if not stop_prime_role(store, 'sidekick'):
            raise Die('Prime session cleanup failed; no fallback was started', 5)
    elif status != 'absent':
        if not end_agent(name, kind, exit_deadline())[1]:
            raise Die(
                f'{kind} sidekick has not exited; close it by hand, then rerun failover',
                5,
            )
    record_failover(store, running_label, target_label, reason)
    record_chain(store, chain, index + 1)
    rc = spawn_role(
        ctx,
        store,
        'sidekick',
        SpawnRequest(
            fallback,
            anchor=field(store, 'master', 'pane_id'),
            pane=field(store, 'sidekick', 'pane_id'),
            args=tuple(target['args']),
        ),
    )
    running = field(store, 'dispatch', 'brief')
    if rc == 0 and running and not os.path.isfile(expected_report(store, running)):
        print(
            f'next: re-dispatch {running} with "resumed after failover from {kind}" in its Context'
        )
    return rc
