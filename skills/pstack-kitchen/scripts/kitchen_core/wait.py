"""wait: the quiet wait.

A routine check-in is a wake with nothing to act on, so a kitchen wait stays quiet through it: it returns for a report, a steps wake, a blocked
sidekick, or a check-in that carries a flag (a stale log, a write outside Scope, an objection, an open blocking note, a timebox overrun, a pause), or
after --max minutes (KITCHEN_QUIET_MAX_S; default an hour) with the latest digest. KITCHEN_QUIET_CLOCK, a file holding epoch seconds, replaces the wall
clock for that --max check; tests move it at a fixed point instead of waiting.

The core's wait is called as a function with its stdout captured (units.captured) and PAIR_WAIT_OWNER (this process's pid), PAIR_WAIT_CLAIM,
PAIR_WAIT_UNOWNED and PAIR_QUIET_WAIT=1 set for the call."""

import os
import re
import sys
import time

from pstack_core import wait as core_wait
from pstack_core.argv import Spec, read
from pstack_core.store import Die, Exit, env_float, env_int, event, field, int_or_none, read_text, whole_number
from pstack_core.wait import default_signals

from .units import captured


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
    event(store, 'master', 'wake', field(store, 'dispatch', 'brief'), 'report:unready' if 'written but not ready' in out else 'checkin')


def inner_wait(ctx, store, args, claim, unowned):
    """The core's wait for args, owned by this process and quiet: (status, stdout)."""
    owner = str(os.getpid())

    def run():
        code = core_wait.wait(ctx, [store, *args])
        if code:
            raise Exit(code)

    return captured(run, env={'PAIR_WAIT_OWNER': owner, 'PAIR_WAIT_CLAIM': str(claim), 'PAIR_WAIT_UNOWNED': str(unowned), 'PAIR_QUIET_WAIT': '1'})


def release(ctx, store):
    """The wait claim given back; whatever the release prints or fails with is dropped."""
    owner = str(os.getpid())

    def run():
        code = core_wait.wait(ctx, [store, '--release'])
        if code:
            raise Exit(code)

    captured(run, merge_stderr=True, env={'PAIR_WAIT_OWNER': owner})


def quiet_wait(ctx, store, rest):
    """Returns the exit status the wait ends with."""
    write = sys.stdout.write
    opts = read(rest, Spec(valued=('--max',), unknown='collect'))
    limit = None
    for value in opts.all('--max'):
        limit = whole_number(value, '--max', 'minutes') * 60
    args = list(opts.leftovers)
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
    default_signals()
    if len(args) < 1:
        raise Die('usage: kitchen.sh wait <store> [--timeout MS | --every MIN] [--max MIN]')
    return quiet_wait(ctx, args[0], args[1:])
