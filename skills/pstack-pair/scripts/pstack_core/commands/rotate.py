import contextlib
import io
import os
import subprocess
import sys
import time

from ..argv import Spec, read
from ..checkin import sidekick_busy
from ..errors import Die, RotationFailed, translate
from ..exits import end_agent, exit_deadline, stop_prime_role
from ..gates import check_scope, require_not_paused
from ..numbers import env_float, env_int
from ..panes import agent_status, herdr, require_herdr
from ..replies import queued_brief
from ..review import require_filled
from ..send import store_relative
from ..spawn import SpawnRequest, record_failover, spawn_role
from ..store import (
    event,
    expected_report,
    field,
    load,
    names,
    pair_file,
    peek,
    read_flag,
    text_at,
    who,
)
from ..wait import end_with_parent


def rotate(ctx, args):
    """Exits the idle sidekick and reuses its pane with the same native arguments. It preserves the store and never deletes a Devin session."""
    end_with_parent()
    require_herdr()
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} rotate <store>')
    store = args[0]
    pair_file(store)
    require_not_paused(store)
    kind = field(store, 'sidekick', 'kind')
    sidekick = peek(load(store), 'sidekick')
    if sidekick.get('rotation_required') is not True:
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
    start_args = names(sidekick, 'start_args')
    event(
        store,
        'master',
        'rotate',
        '-',
        f'generation:{field(store, "sidekick", "generation")}',
    )
    return spawn_role(
        ctx,
        store,
        'sidekick',
        SpawnRequest(
            kind,
            'none',
            field(store, 'master', 'pane_id'),
            pane=field(store, 'sidekick', 'pane_id'),
            args=tuple(start_args),
        ),
    )


def rotate_if_due(ctx, store, what):
    """The sidekick is rotated before the next brief or plan once its last unit is done, so the rotation costs the master no extra wake. Partial
    or blocked work never sets rotation_required and keeps its session. A replacement whose READY is still pending is left to its bootstrap:
    send refuses until it is ready."""
    if (
        not os.path.isfile(f'{store}/pair.json')
        or read_flag(store, 'sidekick.rotation_required') is not True
    ):
        return
    if read_flag(store, 'sidekick.bootstrap_pending') is True:
        return
    # The report lands a moment before the sidekick's turn ends.
    subprocess.run(
        [
            'herdr',
            'agent',
            'wait',
            field(store, 'sidekick', 'name'),
            '--until',
            'idle',
            '--until',
            'done',
            '--timeout',
            '60000',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    said = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        try:
            code = rotate(ctx, [store])
        except Exception as e:
            text, code = translate(e, ctx.prog)
            said.write(text)
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
        subprocess.run(
            [
                'herdr',
                'agent',
                'wait',
                name,
                '--until',
                'idle',
                '--until',
                'done',
                '--until',
                'blocked',
                '--timeout',
                str(env_int('PAIR_ROTATE_READY_S', 60) * 1000),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
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
    """Replaces a failing sidekick with its recorded fallback in the same pane. The new agent picks up from the store: the step log, progress,
    and the brief it was on; every committed step survives. A run stays on the fallback, since failover never switches back."""
    end_with_parent()
    require_herdr()
    if not args:
        raise Die(f'usage: {ctx.prog} failover <store> [--reason TEXT] [--force]')
    store = args[0]
    opts = read(args[1:], Spec(valued=('--reason',), flags=('--force',)))
    reason, force = opts.last('--reason', 'requested'), opts.present('--force')
    pair_file(store)
    kind = field(store, 'sidekick', 'kind')
    recorded = peek(peek(load(store), 'sidekick'), 'fallback')
    fallback = text_at(recorded, 'kind')
    if not fallback:
        raise Die('no fallback recorded; spawn the sidekick with --fallback KIND', 5)
    if kind == fallback:
        raise Die(f'the sidekick already runs the fallback ({fallback})', 5)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    if status == 'working':
        if not force:
            raise Die(
                f'{who("sidekick", name)} is working; pair.sh stop {store} first, or pass --force to interrupt it',
                5,
            )
        herdr('agent', 'send-keys', name, 'esc')
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
    record_failover(store, kind, fallback, reason)
    rc = spawn_role(
        ctx,
        store,
        'sidekick',
        SpawnRequest(
            fallback,
            anchor=field(store, 'master', 'pane_id'),
            pane=field(store, 'sidekick', 'pane_id'),
            args=tuple(names(recorded, 'args')),
        ),
    )
    running = field(store, 'dispatch', 'brief')
    if rc == 0 and running and not os.path.isfile(expected_report(store, running)):
        print(
            f'next: re-dispatch {running} with "resumed after failover from {kind}" in its Context'
        )
    return rc
