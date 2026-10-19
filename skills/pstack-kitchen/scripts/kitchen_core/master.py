"""close, rotate-master and take-master: the end of a run, and the hand-over between master generations. Ported from kitchen.sh at 9f0f6e5."""

import os
import subprocess
import sys
import time

from pstack_core.commands.report import in_herdr
from pstack_core.errors import Die, Exit
from pstack_core.exits import exit_command, stop_prime_role
from pstack_core.herdr import agent_status, herdr, pane_text
from pstack_core.spawn import field
from pstack_core.store import event, pair_file, update


def close_role(store, role):
    """Ends a role's agent and closes its pane, for a run that is over: the exit command, then up to fifteen seconds for the agent to go (a
    Claude asking what to do with its background tasks is told to stop them and exit). The pane closes only once the agent is gone; an agent
    that stays keeps its pane and the function returns False. A role never spawned, or already closed, is skipped (True)."""
    name = field(store, role, 'name', empty=True)
    pane = field(store, role, 'pane_id', empty=True)
    if not name or not pane:
        return True
    if field(store, role, 'closed', default='false') == 'true':
        return True
    kind = field(store, role, 'kind', empty=True)
    if kind == 'prime-agent':
        if not stop_prime_role(store, role):
            return False
    if agent_status(name) != 'absent':
        herdr('agent', 'prompt', name, exit_command(kind))
        deadline = int(time.time()) + 15
        while agent_status(name) != 'absent' and int(time.time()) < deadline:
            if 'move to background and exit' in pane_text(name).lower():
                herdr('agent', 'send-keys', name, '1')
                herdr('agent', 'send-keys', name, 'enter')
            time.sleep(1)
    if agent_status(name) != 'absent':
        sys.stderr.write(f'{role} {name} did not exit; its pane {pane} stays open\n')
        return False
    herdr('pane', 'close', pane)
    update(store, lambda doc: doc[role].__setitem__('closed', True))
    event(store, 'master', 'close', '-', f'{role}:{kind}')
    sys.stdout.write(f'closed {role} {name} ({pane})\n')
    sys.stdout.flush()
    return True


def close(ctx, args):
    in_herdr()
    if not args:
        raise Die('usage: kitchen.sh close <store> [--force]')
    store = args[0]
    force = len(args) > 1 and args[1] == '--force'
    pair_file(store)
    if not force:
        if field(store, 'verifying', 'verdict', empty=True):
            raise Die(f"a verification is open ({field(store, 'verifying', 'units')}); finish it (kitchen.sh verify {store} --wait) or pass --force", 5)
        if agent_status(field(store, 'sidekick', 'name')) == 'working':
            raise Die(f'the sidekick is working; kitchen.sh stop {store} first, or pass --force', 5)
    failed = False
    for role in ('verifier', 'consultant', 'sidekick'):
        if not close_role(store, role):
            failed = True
    if failed:
        raise Exit(5)
    return 0
