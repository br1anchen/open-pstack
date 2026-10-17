import os
import time

from ..errors import Die, Exit
from ..exits import exit_command, exit_deadline, exit_poll, stopped
from ..herdr import agent_status, herdr
from ..review import jq_raw
from ..spawn import fallback_args, field, record_failover, section, spawn_role
from ..store import alt, expected_report, pair_file, pair_json
from ..wait import bash_sleep, core_line, die_with_the_shell
from .report import in_herdr


def failover(ctx, args):
    """Replaces a failing sidekick with its recorded fallback in the same pane. The new agent picks up from the store: the step log, progress,
    and the brief it was on; every committed step survives. A run stays on the fallback, since failover never switches back."""
    die_with_the_shell()
    in_herdr()
    if not args:
        raise Die('usage: pair.sh failover <store> [--reason TEXT] [--force]')
    store, rest = args[0], list(args[1:])
    reason, force = 'requested', False
    while rest:
        if rest[0] == '--reason':
            if len(rest) < 2:
                path, number = core_line('--reason) ', 'cmd_failover() {')
                raise Exit(1, f'{path}: line {number}: $2: unbound variable\n')
            reason, rest = rest[1], rest[2:]
        elif rest[0] == '--force':
            force, rest = True, rest[1:]
        else:
            raise Die(f'unknown option {rest[0]}')
    pair_file(store)
    kind = field(store, 'sidekick', 'kind')
    recorded = section(pair_json(store), 'sidekick').get('fallback')
    fallback = jq_raw(alt(recorded.get('kind'), '')) if isinstance(recorded, dict) else ''
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
        bash_sleep(os.environ.get('PAIR_ESC_WAIT_S') or '2')
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
    record_failover(store, kind, fallback, reason, errexit_off=False)
    rc = spawn_role(ctx, store, 'sidekick', fallback, '', field(store, 'master', 'pane_id'), '60000', '', field(store, 'sidekick', 'pane_id'),
                    fallback_args(section(pair_json(store), 'sidekick').get('fallback')))
    running = jq_raw(alt(section(pair_json(store), 'dispatch').get('brief'), ''))
    if rc == 0 and running and not os.path.isfile(expected_report(store, running)):
        print(f'next: re-dispatch {running} with "resumed after failover from {kind}" in its Context')
    return rc
