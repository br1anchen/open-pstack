import contextlib
import io
import os
import re
import subprocess
import sys
import time

from ..store import Die, Exit, prog, event, field, load, names, pair_file, peek, read_flag, text_at, env_float, expected_report, who
from ..exits import exit_command, exit_deadline, exit_poll, stopped
from ..gates import require_not_paused, check_scope
from ..panes import MERGED, agent_prompt, agent_status, herdr, require_herdr
from ..spawn import spawn_role, record_failover
from ..wait import end_with_parent
from ..replies import queued_brief
from ..review import require_filled
from ..send import store_relative


TOLERATED = re.compile('agent_prompt_stalled|agent_not_found|not_found')


def rotate(ctx, args):
    """Exits the idle sidekick and reuses its pane with the same native arguments. It preserves the store and never deletes a Devin session."""
    end_with_parent()
    require_herdr()
    if len(args) != 1:
        raise Die(f'usage: {prog()} rotate <store>')
    store = args[0]
    pair_file(store)
    require_not_paused(store)
    kind = field(store, 'sidekick', 'kind')
    sidekick = peek(load(store), 'sidekick')
    if sidekick.get('rotation_required') is not True:
        raise Die('rotation requires a completed task; partial or blocked work keeps its session', 5)
    if not isinstance(sidekick.get('start_args'), list):
        raise Die('no recorded startup arguments; exit the idle sidekick and spawn it with its original model and permission flags', 5)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    if status in ('idle', 'done'):
        if kind == 'prime-agent':
            if not stopped(store, 'sidekick'):
                raise Die('Prime session cleanup failed; no new session was started', 5)
        else:
            r = agent_prompt(name, exit_command(kind), stderr=MERGED)
            err = r.stdout.rstrip('\n')
            if r.returncode != 0 and not TOLERATED.search(err):
                raise Die(f'{kind} exit failed: {err}', 2)
            deadline = exit_deadline()
            while agent_status(name) != 'absent':
                if int(time.time()) >= deadline:
                    raise Die(f'{kind} has not exited; inspect {name} before retrying rotate', 5)
                exit_poll()
    elif status == 'absent':
        if kind == 'prime-agent' and not stopped(store, 'sidekick'):
            raise Die('Prime session cleanup failed; no new session was started', 5)
    else:
        raise Die(f'{who("sidekick", name)} is {status}; wait for its report and turn to end before rotating', 5)
    start_args = names(sidekick, 'start_args')
    event(store, 'master', 'rotate', '-', f'generation:{field(store, "sidekick", "generation")}')
    return spawn_role(ctx, store, 'sidekick', kind, 'none', field(store, 'master', 'pane_id'), '60000', '', field(store, 'sidekick', 'pane_id'), start_args)


def rotate_if_due(ctx, store, what):
    """The sidekick is rotated before the next brief or plan once its last unit is done, so the rotation costs the master no extra wake. Partial
    or blocked work never sets rotation_required and keeps its session. A replacement whose READY is still pending is left to its bootstrap:
    send refuses until it is ready."""
    if not os.path.isfile(f'{store}/pair.json') or read_flag(store, 'sidekick.rotation_required') is not True:
        return
    if read_flag(store, 'sidekick.bootstrap_pending') is True:
        return
    # The report lands a moment before the sidekick's turn ends.
    subprocess.run(['herdr', 'agent', 'wait', field(store, 'sidekick', 'name'), '--until', 'idle', '--until', 'done', '--timeout', '60000'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    said = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        try:
            code = rotate(ctx, [store])
        except Exit as e:
            said.write(e.stderr)
            code = e.code
        except Die as e:
            said.write(f'{prog()}: {e.message}\n')
            code = e.code
    if code:
        raise Die(f'rotating the sidekick before this {what} failed:\n{said.getvalue().rstrip(chr(10))}', code)
    sys.stdout.write(f"rotated: the {field(store, 'sidekick', 'kind')} sidekick starts this {what} in a fresh session\n")


def queue(ctx, args):
    """The master's next brief, held until the master dispatches it, which rotates a due sidekick first. One slot: more than one
    queued brief is a plan."""
    if len(args) < 2:
        raise Die(f'usage: {prog()} queue <store> <brief-path> [--replace] | {prog()} queue <store> --clear')
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
    replace = False
    option = args[2] if len(args) > 2 else ''
    if option == '--replace':
        replace = True
    elif option != '':
        raise Die(f'unknown option {option}')
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
    if status in ('idle', 'done'):
        raise Die(f'{who("sidekick", name)} is {status}; dispatch instead: pair.sh dispatch {store} {brief}', 5)
    if status != 'working':
        raise Die(f'{who("sidekick", name)} is {status}; resolve that before queueing', 3)
    with open(f'{store}/queue.tmp', 'w', encoding='utf-8') as f:
        f.write(f'{brief}\n')
    os.replace(f'{store}/queue.tmp', f'{store}/queue')
    event(store, 'master', 'queue', brief)
    print(f"queued {brief}; it waits for the master's dispatch, which rotates a due sidekick first")
    return 0


def failover(ctx, args):
    """Replaces a failing sidekick with its recorded fallback in the same pane. The new agent picks up from the store: the step log, progress,
    and the brief it was on; every committed step survives. A run stays on the fallback, since failover never switches back."""
    end_with_parent()
    require_herdr()
    if not args:
        raise Die(f'usage: {prog()} failover <store> [--reason TEXT] [--force]')
    store, rest = args[0], list(args[1:])
    reason, force = 'requested', False
    while rest:
        if rest[0] == '--reason':
            if len(rest) < 2:
                raise Die('--reason needs a value')
            reason, rest = rest[1], rest[2:]
        elif rest[0] == '--force':
            force, rest = True, rest[1:]
        else:
            raise Die(f'unknown option {rest[0]}')
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
            raise Die(f'{who("sidekick", name)} is working; pair.sh stop {store} first, or pass --force to interrupt it', 5)
        herdr('agent', 'send-keys', name, 'esc')
        time.sleep(env_float('PAIR_ESC_WAIT_S', 2))
    if kind == 'prime-agent':
        if not stopped(store, 'sidekick'):
            raise Die('Prime session cleanup failed; no fallback was started', 5)
    elif status != 'absent':
        agent_prompt(name, exit_command(kind))
        deadline = exit_deadline()
        while agent_status(name) != 'absent':
            if int(time.time()) >= deadline:
                raise Die(f'{kind} sidekick has not exited; close it by hand, then rerun failover', 5)
            exit_poll()
    record_failover(store, kind, fallback, reason)
    rc = spawn_role(ctx, store, 'sidekick', fallback, '', field(store, 'master', 'pane_id'), '60000', '', field(store, 'sidekick', 'pane_id'),
                    names(recorded, 'args'))
    running = field(store, 'dispatch', 'brief')
    if rc == 0 and running and not os.path.isfile(expected_report(store, running)):
        print(f'next: re-dispatch {running} with "resumed after failover from {kind}" in its Context')
    return rc
