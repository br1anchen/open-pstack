"""queue and discuss: the master's next brief held for the sidekick, and a plan round with it (and with the consultant for an escalated plan).
Ported from kitchen.sh at b632713 (cmd_queue, cmd_discuss, rotate_if_due). The plan round with the consultant is pstack_core.consult's.

The bash wrappers called the core's queue and discuss in a child process; here they call the core functions directly."""

import os
import subprocess
import sys

from pstack_core.commands import queue as core_queue
from pstack_core.commands import rotate as rotate_command
from pstack_core.commands import send as core_send
from pstack_core.consult import discuss_with_consultant
from pstack_core.dispatch import fresh_per_brief
from pstack_core.errors import Die, Exit
from pstack_core.herdr import agent_status
from pstack_core.store import field

from .classify import classify_brief, risk_of
from .landing import is_landing
from .units import has_pair
from .verify import in_subshell


def devnull_stdout(call):
    """`call >/dev/null`: stdout of this process and of what it starts goes nowhere for the call; stderr stays."""
    sys.stdout.flush()
    saved = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        return call()
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)
        os.close(null)


def queue(ctx, args):
    args = list(args)
    # A brief path may be given relative to the store (briefs/NNN-x.md), as for dispatch.
    if len(args) >= 2 and not has_pair(args[0]):
        raise Exit(1)
    if len(args) >= 2 and args[1] != '--clear' and not os.path.isfile(args[1]) and os.path.isfile(f'{args[0]}/{args[1]}'):
        args[1] = f'{args[0]}/{args[1]}'
    if len(args) >= 2 and args[1] != '--clear' and os.path.isfile(args[1]) and not is_landing(args[1]):
        devnull_stdout(lambda: classify_brief(args[0], args[1]))
    return core_queue.queue(ctx, args)


def rotate_if_due(ctx, store, what):
    """A sidekick that starts fresh per brief (pi, prime-agent, devin) is rotated before the next brief or plan once its last unit is done, so
    the rotation costs the master no extra wake. Partial or blocked work never sets rotation_required and keeps its session."""
    if not (fresh_per_brief(field(store, 'sidekick', 'kind')) and field(store, 'sidekick', 'rotation_required', default='false') == 'true'):
        return
    # The report lands a moment before the sidekick's turn ends.
    subprocess.run(['herdr', 'agent', 'wait', field(store, 'sidekick', 'name'), '--until', 'idle', '--until', 'done', '--timeout', '60000'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def rotate_in_the_substitution():
        code = rotate_command.rotate(ctx, [store])
        if code:
            raise Exit(code)

    rc, out = in_subshell(rotate_in_the_substitution)
    if rc != 0:
        raise Die(f'rotating the sidekick before this {what} failed:\n{out}', rc)
    sys.stdout.write(f"rotated: the {field(store, 'sidekick', 'kind')} sidekick starts this {what} in a fresh session\n")


def discuss(ctx, args):
    if len(args) < 2:
        raise Die('usage: kitchen.sh discuss <store> <plan-path> [--timeout MS]')
    store, plan = args[0], args[1]
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    if risk_of(plan) not in ('routine', 'escalated'):
        raise Die(f'{plan}: risk: must start with routine or escalated, as kitchen.sh classify prints it', 2)
    escalated = risk_of(plan) == 'escalated'
    if escalated and not (has_pair(store) and agent_status(field(store, 'consultant', 'name')) != 'absent'):
        raise Die(f'an escalated plan goes to the consultant too; start it: kitchen.sh consultant {store} --reason TEXT', 5)
    if not os.path.isfile(f'{store}/pair.json'):
        return core_send.discuss(ctx, list(args))
    rotate_if_due(ctx, store, 'plan')
    if escalated:
        return discuss_with_consultant(ctx, args, one_wake=True)
    return core_send.discuss(ctx, list(args))
