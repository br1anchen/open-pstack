import contextlib
import io
import os
import re
import sys
import time
from dataclasses import replace

from .. import gitstore, mux
from ..agents import current_no_mcp, model_label
from ..argv import Spec, read
from ..checkin import sidekick_busy
from ..errors import Die, PairError, RotationFailed, translate
from ..exits import end_agent, exit_deadline, stop_prime_role
from ..gates import check_scope, require_not_paused
from ..gitstore import is_git
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
    ready_file,
    risk_of,
    rotation_required,
    text_at,
    who,
)
from ..wait import end_with_parent
from .status import log


def require_rotatable(store, sidekick, reason):
    """Refuses a rotation the sidekick's state does not allow, and returns the reason the rotation records. A completed task asks for one; so does
    a replacement that never sent READY, which is no session to keep and may be replaced again whatever the unit before it did (said in a line,
    and recorded as `no READY` when the caller gave no reason)."""
    unready = sidekick.get('bootstrap_pending') is True and not os.path.isfile(
        f'{store}/{ready_file(store, "sidekick")}'
    )
    if not rotation_required(store) and not unready:
        raise Die(
            'rotation requires a completed task; partial or blocked work keeps its session',
            5,
        )
    if unready and reason is None:
        reason = 'no READY'
        print('rotate: the new sidekick never sent READY; rotating again')
    if not isinstance(sidekick.get('start_args'), list):
        raise Die(
            'no recorded startup arguments; exit the idle sidekick and spawn it with its original model and permission flags',
            5,
        )
    return reason


def rotate(ctx, args, reason=None):
    """Exits the idle sidekick and reuses its pane with the same native arguments. It preserves the store and never deletes a Devin session.
    Under git the store is refreshed before pair.json is read, and the master's changes are published when the rotation is over. `reason` is why
    rotate_if_due rotates, which the rotate event records; the command itself (None) is `manual`, said only where a fresh session costs minutes
    (mux reuses_sessions), like every rotation reason."""
    end_with_parent()
    require_mux()
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} rotate <store>')
    store = args[0]
    pair_file(store)
    gitstore.refresh_saying_when_unknown(store)
    require_not_paused(store)
    kind = field(store, 'sidekick', 'kind')
    sidekick = peek(load(store), 'sidekick')
    reason = require_rotatable(store, sidekick, reason)
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
    if reason is None:
        reason = 'manual' if mux.get().reuses_sessions else ''
        if reason:
            print(f'rotate: {reason}')
    code = None
    try:
        event(
            store,
            'master',
            'rotate',
            '-',
            f'generation:{field(store, "sidekick", "generation")}'
            + (f' reason:{reason}' if reason else ''),
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
        code = (
            spawn_role(ctx, store, 'sidekick', request)
            if index == 0
            else fail_back(ctx, store, request, chain, index)
        )
        return code
    finally:
        # A spawn that dies still leaves the rotate event, which the branch must hold.
        publish_rotation(store, code)


def publish_rotation(store, code):
    """Under git, the master's changes (the rotate event) go to the branch when a rotation is over. `code` is what the spawn returned, None when
    it raised. A failing publish raises after a spawn that worked (0); after one that raised or returned a failure it is a warning, so the
    spawn's error or exit code is the one the command ends with."""
    if not is_git(store):
        return
    try:
        gitstore.publish_role(store, 'master')
    except PairError as err:
        if code == 0:
            raise
        what = 'rollback' if code is None else 'rotation'
        sys.stderr.write(f'warning: the {what} could not be published: {err}\n')


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


def profiles_label(profiles):
    return ', '.join(sorted(profiles)) or '-'


def session_decision(store, brief):
    """(kept, why) of the sidekick's session for `brief`: the profiles and context ('app, context 10%') it keeps for, or '' and the reason it rotates.
    The session is kept only where a fresh one costs minutes (mux reuses_sessions, Amp threads), and only when the unit it finished and this one
    are both routine, in the same profiles, and its context is under PAIR_REUSE_CONTEXT_PCT (50) percent of the window and never compacted. A
    context the backend cannot read rotates. The context is read here once, so the reason and the action come from one observation."""
    m = mux.get()
    if not m.reuses_sessions:
        return '', ''
    previous = field(store, 'dispatch', 'brief')
    if not (brief and previous and os.path.isfile(previous)):
        return '', 'previous brief missing'
    before, after = risk_of(previous), risk_of(brief)
    if before != 'routine' or after != 'routine':
        return '', f'risk {before}/{after}'
    area, earlier = profiles_of(brief), profiles_of(previous)
    if not area or area != earlier:
        return '', f'profiles {profiles_label(earlier)} vs {profiles_label(area)}'
    name = field(store, 'sidekick', 'name')
    used, compacted = m.context(name)
    if compacted:
        return '', 'compacted'
    if used is None:
        return '', 'context unreadable'
    limit = env_int('PAIR_REUSE_CONTEXT_PCT', 50)
    if used * 100 >= limit:
        return '', f'context {round(used * 100)}% >= {limit}%'
    return f'{", ".join(sorted(area))}, context {round(used * 100)}%', ''


def keeps_session(store, brief, reasons):
    """Why the sidekick keeps its session for `brief` ('' when it rotates, and then session_decision's reason is appended to `reasons`)."""
    kept, why = session_decision(store, brief)
    if not kept:
        reasons.append(why)
    return kept


def rotation_reason(what, failed_over, reasons):
    """Why the sidekick starts fresh, for the `rotate:` line and the rotate event: '' where a fresh session costs little (a mux that does not
    reuse sessions rotates every time, so there is nothing to say). `reasons` is what keeps_session collected."""
    if not mux.get().reuses_sessions:
        return ''
    if failed_over:
        return 'failed over'
    if what != 'brief':
        return what
    return reasons[0] if reasons else ''


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


def archive_logged(ctx, store, row, trigger):
    """Archives the row's thread (mux archive) and logs it in decisions.tsv and events.tsv with its thread id, then says `archived <thread>
    (<role>): <trigger>`. Whether amp archived it."""
    if not mux.get().archive(row):
        return False
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
    return True


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
        elif archive_logged(ctx, store, row, trigger):
            archived.append(row)
            outcomes[row.thread] = 'archived'
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
    gitstore.refresh_saying_when_unknown(store)
    archive_retired(ctx, store, f'a {what} is being sent')
    if not rotation_required(store):
        return
    if read_flag(store, 'sidekick.bootstrap_pending') is True:
        return
    failed_over = count(peek(load(store), 'sidekick'), 'chain_index') > 0
    reasons = []
    kept = (
        ''
        if failed_over
        else keeps_session(store, brief, reasons)
        if what == 'brief'
        else ''
    )
    if kept:
        clear_rotation(store)
        if is_git(store):
            event(store, 'master', 'reuse', brief, f'sidekick: {kept}')
            gitstore.publish_role(store, 'master')
        else:
            event(store, 'sidekick', 'reuse', brief, kept)
        sys.stdout.write(
            f'kept: the {field(store, "sidekick", "kind")} sidekick keeps its session for this routine unit ({kept})\n'
        )
        return
    why = rotation_reason(what, failed_over, reasons)
    if why:
        sys.stdout.write(f'rotate: {why}\n')
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
            code = rotate(ctx, [store], reason=why)
        except Exception as e:
            text, code = translate(e, ctx.prog)
            said.write(text)
    if code == 4 and not mux.get().waits:
        # The new thread bootstraps on its own after rotate returns: this was a fresh session, and its READY settles the bootstrap.
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
