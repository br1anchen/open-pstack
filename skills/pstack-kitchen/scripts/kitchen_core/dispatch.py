"""dispatch, queue and discuss: send a brief and wait quietly, hold the next brief, and run a plan round. The plan round with the consultant is pstack_core.consult's.

The core's dispatch_brief runs quiet and returns a DispatchResult. When its wait returned on a check-in with nothing flagged, the quiet wait's
status is the command's status, and the check-in is neither printed nor recorded as a wake."""

import contextlib
import io
import os
import sys

from pstack_core import send
from pstack_core.argv import Spec, read
from pstack_core.commands.rotate import queue as core_queue, rotate_if_due
from pstack_core.consult import discuss_with_consultant
from pstack_core.panes import agent_status
from pstack_core.store import field, pair_file, risk_of, whole_number
from pstack_core.commands.report import require_pair
from pstack_core.errors import Die, MissingStore, translate
from pstack_core.wait import default_signals

from .classify import classify_brief
from .landing import is_landing, land_check
from .units import captured, has_pair
from .wait import block, log_checkin_wake, quiet_wait, refused


def land_check_quietly(ctx, store):
    """land-check's verdict without the command's usage and pair.json checks; its stdout is dropped."""
    return captured(ctx, lambda: land_check(store))[0]


NUMBER_FLAGS = {'--timeout': 'milliseconds', '--every': 'minutes', '--max': 'minutes'}
DISPATCH_SPEC = Spec(valued=tuple(NUMBER_FLAGS), unknown='collect')


def dispatch(ctx, args):
    default_signals()
    if len(args) < 2:
        raise Die('usage: kitchen.sh dispatch <store> <brief-path> [--timeout MS | --every MIN] [--max MIN]')
    store, brief = args[0], args[1]
    # every number flag is read before the command changes anything
    opts = read(args[2:], DISPATCH_SPEC)
    for flag, value in opts.occurrences:
        whole_number(value, flag, NUMBER_FLAGS[flag])
    # A brief path may be given relative to the store (briefs/NNN-x.md).
    if not os.path.isfile(brief) and os.path.isfile(f'{store}/{brief}'):
        brief = f'{store}/{brief}'
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    if is_landing(brief):
        if land_check_quietly(ctx, store) != 0:
            raise Die(f'landing refused: kitchen.sh land-check {store} fails', 2)
    else:
        require_pair(ctx, store)
        classify_brief(store, brief)
    # a landing brief that land-check passed on a store with no pair.json goes on to the core dispatch, which refuses it
    if has_pair(store):
        rotate_if_due(ctx, store, 'brief')
    # --max belongs to the quiet wait, not to the pair's dispatch.
    held = []
    try:
        request = send.dispatch_request(ctx, [store, brief, *opts.stripped('--max')])
        result = send.dispatch_brief(ctx, request.store, request.brief, timeout=request.timeout, send_only=request.send_only, quiet=True, out=held.append)
    except Exception as e:
        return refused(ctx, e, held)
    outcome = result.outcome
    if outcome is not None and outcome.kind == 'checkin' and outcome.checkin is not None:
        if not outcome.flagged:
            sys.stdout.write(block(''.join(held) + ''.join(outcome.checkin.notices)))
            return quiet_wait(ctx, store, list(args[2:]))
        log_checkin_wake(ctx, store, outcome)
    sys.stdout.write(block(''.join(result.sent_lines) + ''.join(held) + ('' if outcome is None else ''.join(outcome.digest))))
    return result.status


def queue(ctx, args):
    args = list(args)
    # A brief path may be given relative to the store (briefs/NNN-x.md), as for dispatch.
    if len(args) >= 2:
        pair_file(args[0])
    if len(args) >= 2 and args[1] != '--clear' and not os.path.isfile(args[1]) and os.path.isfile(f'{args[0]}/{args[1]}'):
        args[1] = f'{args[0]}/{args[1]}'
    if len(args) >= 2 and args[1] != '--clear' and os.path.isfile(args[1]) and not is_landing(args[1]):
        with contextlib.redirect_stdout(io.StringIO()):
            classify_brief(args[0], args[1])
    return core_queue(ctx, args)


def discuss(ctx, args):
    if len(args) < 2:
        raise Die('usage: kitchen.sh discuss <store> <plan-path> [--timeout MS]')
    store, plan = args[0], args[1]
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    if risk_of(plan) not in ('routine', 'escalated'):
        raise Die(f'{plan}: risk: must start with routine or escalated, as kitchen.sh classify prints it', 2)
    escalated = risk_of(plan) == 'escalated'
    if escalated and not has_pair(store):
        sys.stderr.write(translate(MissingStore(store), ctx.prog)[0])
    if escalated and not (has_pair(store) and agent_status(field(store, 'consultant', 'name')) != 'absent'):
        raise Die(f'an escalated plan goes to the consultant too; start it: kitchen.sh consultant {store} --reason TEXT', 5)
    if not os.path.isfile(f'{store}/pair.json'):
        return send.discuss(ctx, list(args))
    rotate_if_due(ctx, store, 'plan')
    if escalated:
        return discuss_with_consultant(ctx, args, one_wake=True)
    return send.discuss(ctx, list(args))
