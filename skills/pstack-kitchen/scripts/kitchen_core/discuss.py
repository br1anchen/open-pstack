"""queue and discuss: the master's next brief held for the sidekick, and a plan round with it (and with the consultant for an escalated plan).
Ported from kitchen.sh at b632713 (cmd_queue, cmd_discuss, rotate_if_due). The plan round with the consultant is pstack_core.consult's.

The bash wrappers called the core's queue and discuss in a child process; here they call the core functions directly."""

import contextlib
import io
import os

from pstack_core import send as core_send
from pstack_core.commands import rotate as core_queue
from pstack_core.commands.rotate import rotate_if_due
from pstack_core.consult import discuss_with_consultant
from pstack_core.store import Die, Exit, field
from pstack_core.panes import agent_status

from .classify import classify_brief, risk_of
from .landing import is_landing
from .units import has_pair


def queue(ctx, args):
    args = list(args)
    # A brief path may be given relative to the store (briefs/NNN-x.md), as for dispatch.
    if len(args) >= 2 and not has_pair(args[0]):
        raise Exit(1)
    if len(args) >= 2 and args[1] != '--clear' and not os.path.isfile(args[1]) and os.path.isfile(f'{args[0]}/{args[1]}'):
        args[1] = f'{args[0]}/{args[1]}'
    if len(args) >= 2 and args[1] != '--clear' and os.path.isfile(args[1]) and not is_landing(args[1]):
        with contextlib.redirect_stdout(io.StringIO()):
            classify_brief(args[0], args[1])
    return core_queue.queue(ctx, args)


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
