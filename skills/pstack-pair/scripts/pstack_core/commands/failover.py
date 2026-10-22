import os
import time

from ..errors import Die
from ..exits import exit_command, exit_deadline, exit_poll, stopped
from ..herdr import agent_status, herdr
from ..spawn import record_failover, spawn_role
from ..store import env_float, expected_report, field, load, names, pair_file, peek, text_at
from ..wait import end_with_parent
from .report import in_herdr


def failover(ctx, args):
    """Replaces a failing sidekick with its recorded fallback in the same pane. The new agent picks up from the store: the step log, progress,
    and the brief it was on; every committed step survives. A run stays on the fallback, since failover never switches back."""
    end_with_parent()
    in_herdr()
    if not args:
        raise Die('usage: pair.sh failover <store> [--reason TEXT] [--force]')
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
            raise Die(f'sidekick {name} is working; pair.sh stop {store} first, or pass --force to interrupt it', 5)
        herdr('agent', 'send-keys', name, 'esc')
        time.sleep(env_float('PAIR_ESC_WAIT_S', 2))
    if kind == 'prime-agent':
        if not stopped(store, 'sidekick'):
            raise Die('Prime session cleanup failed; no fallback was started', 5)
    elif status != 'absent':
        herdr('agent', 'prompt', name, exit_command(kind))
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
