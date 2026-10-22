"""dispatch, queue and discuss: send a brief and wait quietly, hold the next brief, and run a plan round. Ported from kitchen.sh at b632713
(cmd_dispatch, cmd_queue, cmd_discuss, rotate_if_due). The plan round with the consultant is pstack_core.consult's.

The bash ran the pair's dispatch (`core_dispatch`, a snapshot of the pair's command) inside `$( )` with PAIR_QUIET_WAIT=1: a child process with its stdout on
a pipe and its stderr left alone. Here the core's dispatch is called as a function with its stdout captured (units.captured) and
the same variable set for the call. A nonzero return of the core's dispatch is the child's exit
status, taken as `|| rc=$?` took it. When the digest is a check-in with nothing flagged, the bash's `quiet_wait` ran in the dispatch shell and ended it with
`exit`, so the dispatch's own wake row and its digest were never reached; here the quiet wait's status is the command's status the same way."""

import contextlib
import io
import os
import sys

from pstack_core import send
from pstack_core.commands.rotate import queue as core_queue, rotate_if_due
from pstack_core.consult import discuss_with_consultant
from pstack_core.panes import agent_status
from pstack_core.store import Die, Exit, field, whole_number
from pstack_core.wait import default_signals

from .classify import classify_brief, risk_of
from .landing import is_landing, land_check
from .units import captured, has_pair
from .wait import has_checkin, log_checkin_wake, quiet_flagged, quiet_wait


# grep -v -e '^check-in:' -e '^progress:' -e '^touched:' -e '^  ' -e '^steers:' -e '^steps:' -e '^report: missing': what a quiet dispatch prints of the
# digest it swallowed
SWALLOWED = ('check-in:', 'progress:', 'touched:', '  ', 'steers:', 'steps:', 'report: missing')


def land_check_quietly(ctx, store):
    """`land_check "$1" >/dev/null`: the function, not cmd_land_check, so the command's usage and pair.json checks do not run; its stdout is dropped."""
    def run():
        code = land_check(store)
        if code:
            raise Exit(code)
    return captured(run)[0]


def core_dispatch(ctx, args):
    """`$(PAIR_QUIET_WAIT=1 core_dispatch ARGS)`: (status, stdout without its trailing newlines)."""
    def run():
        code = send.dispatch(ctx, list(args))
        if code:
            raise Exit(code)
    return captured(run, env={'PAIR_QUIET_WAIT': '1'})


NUMBER_FLAGS = {'--timeout': 'milliseconds', '--every': 'minutes', '--max': 'minutes'}


def dispatch(ctx, args):
    default_signals()
    if len(args) < 2:
        raise Die('usage: kitchen.sh dispatch <store> <brief-path> [--timeout MS | --every MIN] [--max MIN]')
    store, brief = args[0], args[1]
    # every number flag is read before the command changes anything
    for i, flag in enumerate(args[2:], 2):
        if flag in NUMBER_FLAGS:
            if i + 1 >= len(args):
                raise Die(f'{flag} needs a value')
            whole_number(args[i + 1], flag, NUMBER_FLAGS[flag])
    # A brief path may be given relative to the store (briefs/NNN-x.md).
    if not os.path.isfile(brief) and os.path.isfile(f'{store}/{brief}'):
        brief = f'{store}/{brief}'
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    if is_landing(brief):
        if land_check_quietly(ctx, store) != 0:
            raise Die(f'landing refused: kitchen.sh land-check {store} fails', 2)
    else:
        has_pair(store, check=True)
        classify_brief(store, brief)
    # a landing brief that land-check passed on a store with no pair.json goes on to the core dispatch, which refuses it
    if has_pair(store, quiet=True):
        rotate_if_due(ctx, store, 'brief')
    wait_args = list(args[2:])
    # --max belongs to the quiet wait, not to the pair's dispatch.
    send_args, i = [], 0
    while i < len(wait_args):
        if wait_args[i] == '--max':
            i += 2
        else:
            send_args.append(wait_args[i])
            i += 1
    rc, out = core_dispatch(ctx, [store, brief, *send_args])
    if rc == 4 and has_checkin(out):
        if not quiet_flagged(out):
            kept = [line for line in out.split('\n') if not line.startswith(SWALLOWED)]
            sys.stdout.write('\n'.join(kept).rstrip('\n') + '\n')
            raise Exit(quiet_wait(ctx, store, wait_args))
        log_checkin_wake(store, out)
    sys.stdout.write(out + '\n')
    return rc


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
    if escalated and not (has_pair(store) and agent_status(field(store, 'consultant', 'name')) != 'absent'):
        raise Die(f'an escalated plan goes to the consultant too; start it: kitchen.sh consultant {store} --reason TEXT', 5)
    if not os.path.isfile(f'{store}/pair.json'):
        return send.discuss(ctx, list(args))
    rotate_if_due(ctx, store, 'plan')
    if escalated:
        return discuss_with_consultant(ctx, args, one_wake=True)
    return send.discuss(ctx, list(args))
