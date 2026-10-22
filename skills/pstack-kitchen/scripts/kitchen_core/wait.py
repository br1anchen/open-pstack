"""wait: the quiet wait. Ported from kitchen.sh at b632713 (quiet_now, quiet_flagged, log_checkin_wake, quiet_wait and cmd_wait).

A routine check-in is a wake with nothing to act on, so a kitchen wait stays quiet through it: it returns for a report, a steps wake, a blocked
sidekick, or a check-in that carries a flag (a stale log, a write outside Scope, an objection, an open blocking note, a timebox overrun, a pause), or
after --max minutes (KITCHEN_QUIET_MAX_S; default an hour) with the latest digest. KITCHEN_QUIET_CLOCK, a file holding epoch seconds, replaces the wall
clock for that --max check; tests move it at a fixed point instead of waiting.

The bash ran the pair's wait as `core_wait` inside `$( )`: a child process with its stdout on a pipe and its stderr left alone, under the environment
PAIR_WAIT_OWNER (the shell's pid), PAIR_WAIT_CLAIM, PAIR_WAIT_UNOWNED and PAIR_QUIET_WAIT=1. Here the core's wait is called as a function in a forked
child (verify.in_subshell, stdout only) with the same four variables set in the child; PAIR_WAIT_OWNER is this process's pid, the token the old shell's
`$$` was, so a bash waiter and this one read each other's wait.pid."""

import os
import re
import signal
import sys
import time

from pstack_core import wait as core_wait
from pstack_core.errors import Die, Exit, unbound_arg
from pstack_core.store import env_float, env_int, event, field, int_or_none, whole_number

from .verify import in_subshell


def plain_signals():
    """What a bash script's own signals are: a signal ends it, with no Python traceback and the signal as the status, and one the parent ignored (a
    background job's SIGINT) stays ignored. Unlike pstack_core's die_with_the_shell it sets no parent-death signal: kitchen.sh's step, finish, dispatch
    and wait were bash, and a bash script does not end with its parent. The core commands they call (the dispatch, the inner waits) still do, in the
    children that run them."""
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(number) is not signal.SIG_IGN:
            signal.signal(number, signal.SIG_DFL)


def poll_sleep(seconds):
    sys.stdout.flush()
    time.sleep(seconds)


def quiet_now():
    """The epoch seconds: KITCHEN_QUIET_CLOCK, a file holding a whole number, when it is set and readable, else the clock."""
    clock = os.environ.get('KITCHEN_QUIET_CLOCK')
    if clock and os.access(clock, os.R_OK):
        try:
            with open(clock, 'rb') as f:
                text = f.read().decode('utf-8', 'surrogateescape')
        except OSError as e:
            raise Die(f'{clock}: {os.strerror(e.errno)}', 1)
        now = int_or_none(text)
        if now is None:
            raise Die(f'{clock} must hold a whole number of seconds', 1)
        return now
    return int(time.time())


FLAGGED = re.compile(r'STALE|^outside scope: [1-9]|[1-9][0-9]* objected|[1-9][0-9]* blocking notes open|^paused:', re.M)


def quiet_flagged(text):
    """Whether a check-in carries something the master must see: a flag, or a timebox overrun."""
    if FLAGGED.search(text):
        return True
    spent = box = None
    for line in text.split('\n'):
        m = re.match(r'check-in: .*elapsed ([0-9]+)m of ([0-9]+)m', line)
        if m:
            spent, box = m.group(1), m.group(2)
            break
    if spent is None or box is None:
        return False
    return int(spent) > int(box)


def has_checkin(text):
    return any(line.startswith('check-in:') for line in text.split('\n'))


def log_checkin_wake(store, out):
    """The one wake row for a wait or dispatch that returns on a check-in: the pair core logs none of its own under PAIR_QUIET_WAIT."""
    event(store, 'master', 'wake', field(store, 'dispatch', 'brief', empty=True), 'report:unready' if 'written but not ready' in out else 'checkin')


def inner_wait(ctx, store, args, claim, unowned):
    """`$(PAIR_WAIT_OWNER=$$ PAIR_WAIT_CLAIM=.. PAIR_WAIT_UNOWNED=.. PAIR_QUIET_WAIT=1 core_wait store args)`: (status, stdout)."""
    owner = str(os.getpid())

    def run():
        os.environ.update({'PAIR_WAIT_OWNER': owner, 'PAIR_WAIT_CLAIM': str(claim), 'PAIR_WAIT_UNOWNED': str(unowned), 'PAIR_QUIET_WAIT': '1'})
        code = core_wait.wait(ctx, [store, *args])
        if code:
            raise Exit(code)

    return in_subshell(run, merge=False)


def release(ctx, store):
    """`: "$(PAIR_WAIT_OWNER=$$ core_wait "$store" --release 2>&1)" || true`: the claim given back; whatever it prints or fails with is dropped."""
    owner = str(os.getpid())

    def run():
        os.environ['PAIR_WAIT_OWNER'] = owner
        code = core_wait.wait(ctx, [store, '--release'])
        if code:
            raise Exit(code)

    in_subshell(run)


def quiet_wait(ctx, store, rest):
    """Returns the exit status the wait ends with."""
    write = sys.stdout.write
    limit = None
    args, rest = [], list(rest)
    while rest:
        if rest[0] == '--max':
            if len(rest) < 2:
                raise unbound_arg()
            limit = whole_number(rest[1], '--max', 'minutes') * 60
            rest = rest[2:]
        else:
            args.append(rest.pop(0))
    start = quiet_now()
    # One wait owns the store under this process's pid: the first inner wait claims it, the later ones only check it. A claim that could not be
    # made (the inner wait says `unowned:`) leaves the rest of this run unowned: its later waits must not read the missing claim as a newer owner.
    claim, unowned = 1, 0

    def under_max():
        return quiet_now() - start < (limit if limit is not None else env_int('KITCHEN_QUIET_MAX_S', 3600))

    while True:
        rc, out = inner_wait(ctx, store, args, claim, unowned)
        claim = 0
        if any(line.startswith('unowned:') for line in out.split('\n')):
            unowned = 1
            out = '\n'.join(line for line in out.split('\n') if not line.startswith('unowned:')).rstrip('\n')
        if rc == 4 and has_checkin(out) and not quiet_flagged(out) and under_max():
            poll_sleep(env_float('KITCHEN_QUIET_POLL_S', 1))
            continue
        if rc == 4 and has_checkin(out) and not quiet_flagged(out):
            write(f"quiet: {(quiet_now() - start) // 60}m with nothing flagged; the sidekick works on\n")
        if rc == 4 and has_checkin(out):
            log_checkin_wake(store, out)
        write(out + '\n')
        release(ctx, store)
        return rc


def wait(ctx, args):
    plain_signals()
    if len(args) < 1:
        raise Die('usage: kitchen.sh wait <store> [--timeout MS | --every MIN] [--max MIN]')
    return quiet_wait(ctx, args[0], args[1:])
