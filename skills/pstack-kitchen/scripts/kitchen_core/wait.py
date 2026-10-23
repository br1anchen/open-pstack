"""wait: the quiet wait.

A routine check-in is a wake with nothing to act on, so a kitchen wait stays quiet through it: it returns for a report, a steps wake, a blocked
sidekick, or a check-in that carries a flag (a stale log, a write outside Scope, an objection, an open blocking note, a timebox overrun, a pause), or
after --max minutes (KITCHEN_QUIET_MAX_S; default an hour) with the latest digest. KITCHEN_QUIET_CLOCK, a file holding epoch seconds, replaces the wall
clock for that --max check; tests move it at a fixed point instead of waiting.

The core's wait_for runs quiet, under a WaitOwner that is this process's pid, and says in its WaitOutcome how it ended."""

import os
import sys
import time

from pstack_core.argv import Spec, read
from pstack_core.store import wake, env_float, env_int, field, int_or_none, read_text, whole_number
from pstack_core.errors import BadNumber, Die, PathFailed, translate
from pstack_core.wait import NoStoreToWait, WaitOwner, default_signals, release, wait_for, wait_target


def poll_sleep(seconds):
    sys.stdout.flush()
    time.sleep(seconds)


def quiet_now():
    """The epoch seconds: KITCHEN_QUIET_CLOCK, a file holding a whole number, when it is set and readable, else the clock."""
    clock = os.environ.get('KITCHEN_QUIET_CLOCK')
    if clock and os.access(clock, os.R_OK):
        try:
            text = read_text(clock)
        except OSError as e:
            raise PathFailed(clock, e) from None
        now = int_or_none(text)
        if now is None:
            raise BadNumber(f'{clock} must hold a whole number of seconds')
        return now
    return int(time.time())


def block(text):
    """Text as the kitchen prints a command's stdout: with exactly one newline at its end."""
    return text.rstrip('\n') + '\n'


def log_checkin_wake(ctx, store, outcome):
    """The one wake row for a wait or dispatch that returns on a check-in: the core records none for a quiet check-in return."""
    wake(ctx, store, field(store, 'dispatch', 'brief'), 'report:unready' if outcome.unready else 'checkin')


def refused(ctx, error):
    """The status a refused wait or dispatch ends with, after the message the core gives the error. What it leaves on stdout is an empty line, or
    `state: ` for a store with no pair.json."""
    text, status = translate(error, ctx.prog)
    sys.stderr.write(text)
    sys.stdout.write(error.stdout if isinstance(error, NoStoreToWait) else '\n')
    return status


def quiet_wait(ctx, store, rest):
    """Returns the exit status the wait ends with."""
    write = sys.stdout.write
    opts = read(rest, Spec(valued=('--max',), unknown='collect'))
    limit = None
    for value in opts.all('--max'):
        limit = whole_number(value, '--max', 'minutes') * 60
    args = list(opts.leftovers)
    start = quiet_now()
    # One wait owns the store under this process's pid: the first wait claims it and the later ones only check it. A claim that could not be made
    # leaves the rest of this run without ownership, so its later waits must not read the missing claim as a newer owner.
    owner = WaitOwner(str(os.getpid()))

    def under_max():
        return quiet_now() - start < (limit if limit is not None else env_int('KITCHEN_QUIET_MAX_S', 3600))

    try:
        _, timeout, interval = wait_target(ctx, [store, *args])
        while True:
            outcome = wait_for(ctx, store, timeout=timeout, interval=interval, owner=owner, quiet=True)
            owner = owner.after(outcome)
            checkin = outcome.kind == 'checkin' and outcome.checkin is not None
            if checkin and not outcome.flagged:
                if under_max():
                    poll_sleep(env_float('KITCHEN_QUIET_POLL_S', 1))
                    continue
                write(f"quiet: {(quiet_now() - start) // 60}m with nothing flagged; the sidekick works on\n")
            if checkin:
                log_checkin_wake(ctx, store, outcome)
            write(block(outcome.stdout))
            return outcome.status
    except Exception as e:
        return refused(ctx, e)
    finally:
        release(store, owner)


def wait(ctx, args):
    default_signals()
    if len(args) < 1:
        raise Die('usage: kitchen.sh wait <store> [--timeout MS | --every MIN] [--max MIN]')
    return quiet_wait(ctx, args[0], args[1:])
