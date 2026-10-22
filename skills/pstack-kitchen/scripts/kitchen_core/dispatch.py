"""dispatch: send a brief and wait quietly. Ported from kitchen.sh at b632713 (cmd_dispatch).

The bash ran the pair's dispatch (`core_dispatch`, a snapshot of pair-core's command) inside `$( )` with PAIR_QUIET_WAIT=1: a child process with its stdout on
a pipe, its stderr left alone, and errexit cleared. Here the core's dispatch is called as a function in a forked child (verify.in_subshell, stdout only) with
the same variable set in the child and PAIR_SHELL_FLAGS without `e`, as the substitution left them. A nonzero return of the core's dispatch is the child's exit
status, taken as `|| rc=$?` took it. When the digest is a check-in with nothing flagged, the bash's `quiet_wait` ran in the dispatch shell and ended it with
`exit`, so the dispatch's own wake row and its digest were never reached; here the quiet wait's status is the command's status the same way."""

import os
import sys

from pstack_core.commands import send
from pstack_core.errors import Die, Exit

from .classify import classify_brief
from .discuss import rotate_if_due
from .landing import is_landing, land_check
from .units import has_pair
from .verify import in_subshell
from .wait import has_checkin, plain_signals, log_checkin_wake, quiet_flagged, quiet_wait

# grep -v -e '^check-in:' -e '^progress:' -e '^touched:' -e '^  ' -e '^steers:' -e '^steps:' -e '^report: missing': what a quiet dispatch prints of the
# digest it swallowed
SWALLOWED = ('check-in:', 'progress:', 'touched:', '  ', 'steers:', 'steps:', 'report: missing')


def land_check_quietly(ctx, store):
    """`land_check "$1" >/dev/null`: the function, not cmd_land_check, so the command's usage and pair.json checks do not run; its stdout is dropped."""
    def run():
        code = land_check(store)
        if code:
            raise Exit(code)
    return in_subshell(run, merge=False)[0]


def core_dispatch(ctx, args):
    """`$(PAIR_QUIET_WAIT=1 core_dispatch ARGS)`: (status, stdout without its trailing newlines)."""
    def run():
        os.environ['PAIR_QUIET_WAIT'] = '1'
        os.environ['PAIR_SHELL_FLAGS'] = os.environ.get('PAIR_SHELL_FLAGS', '').replace('e', '')
        code = send.dispatch(ctx, list(args))
        if code:
            raise Exit(code)
    return in_subshell(run, merge=False)


def dispatch(ctx, args):
    plain_signals()
    if len(args) < 2:
        raise Die('usage: kitchen.sh dispatch <store> <brief-path> [--timeout MS | --every MIN] [--max MIN]')
    store, brief = args[0], args[1]
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
