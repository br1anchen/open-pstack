"""wait: the timing options, the loop that waits for the sidekick's reply, and finish_wait, which prints the outcome. WaitResult carries code,
out, err, ready and steps_due."""

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace

from . import procinfo
from .argv import Spec, read
from .checkin import CheckIn, checkin, sidekick_activity
from .errors import BadStepRow, Die, MissingStore, PairError, refuse_as
from .numbers import env_float, env_int, exit_code, tdiv, whole_number
from .panes import (
    agent_state,
    agent_status,
    error_code,
    error_state,
    herdr_get,
    pane_busy,
    require_herdr,
)
from .providers import provider_hint
from .replies import (
    dispatched,
    file_mtime,
    fresh_reply,
    latest_report,
    mark_seen,
    pending_report,
    queue_note,
    review_delta,
    seen_reply,
    unreviewed_steps,
    withheld_report,
)
from .review import review_base
from .store import (
    TEXT,
    acquire_lock,
    field,
    header_field,
    load,
    member,
    now_s,
    paused_reason,
    peek,
    read_text,
    text_at,
    unit_name,
    update,
    wake,
)


class WaitResult:
    __slots__ = ('code', 'out', 'err', 'ready', 'steps_due', 'superseded')

    def __init__(
        self, code=0, out='', err='', ready='', steps_due=False, superseded=False
    ):
        self.superseded = superseded
        self.code = code
        self.out = out
        self.err = err
        self.ready = ready
        self.steps_due = steps_due


@dataclass(frozen=True)
class WaitOutcome:
    """How a wait ended. `status` is the exit code the wait command ends with and `kind` says why it returned: report (a report is ready), steps
    (unreviewed steps), idle (the reply was already shown), blocked (the sidekick is blocked and no report is ready), checkin (the sidekick is
    still working), missing (no report and the sidekick is not working) or superseded (a newer wait took the store). The stdout the command prints
    goes to the wait's `out` as it is produced; a return on a check-in adds its `digest`, newline-ended chunks for printing only, which the caller
    prints after what `out` received. The rest are the facts a caller branches on."""

    status: int
    kind: str
    checkin: CheckIn | None = None
    digest: tuple[str, ...] = ()
    unready: bool = False
    paused: bool = False
    claim_failed: bool = False

    @property
    def flagged(self):
        """Whether the outcome carries something the master must see even when the sidekick is only working on: a pause, or a flagged digest."""
        return self.paused or (self.checkin is not None and self.checkin.flagged)


@dataclass(frozen=True)
class WaitOwner:
    """The ownership of wait.pid for a caller that runs several waits as one. `token` is what wait.pid holds; `claim` is whether the next wait takes
    the store; `checked` is whether it ends superseded when another token holds it. A claim that failed leaves the owner unchecked."""

    token: str
    claim: bool = True
    checked: bool = True

    def after(self, outcome):
        """The owner for the wait that follows `outcome`: the claim is made once, and a claim that failed stays failed."""
        return replace(
            self, claim=False, checked=self.checked and not outcome.claim_failed
        )


TIMING = Spec(
    valued=('--timeout', '--every'), flags=('--send-only',), unknown='collect'
)
TIMEOUT_ONLY = Spec(valued=('--timeout', '--every'), unknown='collect')


def timeout_ms(parsed, default=540000):
    """The wait's timeout in milliseconds: --timeout takes milliseconds and --every minutes, both whole numbers, and the last of them wins."""
    timeout = default
    for flag, value in parsed.occurrences:
        if flag == '--timeout':
            timeout = whole_number(value, '--timeout', 'milliseconds')
        elif flag == '--every':
            timeout = whole_number(value, '--every', 'minutes') * 60000
    return timeout


def reply_mode(result):
    return 'steps' if not result.ready and result.steps_due else 'reply'


def state_line(store, result, write):
    """Writes the `state:` line of a wait that returned and returns the state: the agent's own, or for a failed wait what its stderr names (a timeout
    reads the agent's status now, any other failure is also written to stderr)."""
    if result.code != 0:
        state = error_state(result.err)
        if state == 'timeout':
            state = agent_status(field(store, 'sidekick', 'name'))
        else:
            sys.stderr.write(f'{result.err}\n')
    else:
        state = agent_state(result.out, 'settled')
    write(f'state: {state}\n')
    return state


def report_returned(ctx, store, report, state, paused, write):
    """The outcome of a wait that has a report: the report is marked seen (and the sent entry too when the report answers it), written with its status,
    recorded as a wake and followed by the queue note and the review delta. A blocked agent makes it exit 3."""
    mark_seen(store, report)
    sent = sent_file(store)
    if sent is None:
        # basename '---' reads the word as an option, prints its error and gives nothing to compare.
        sys.stderr.write(
            "basename: unrecognized option '---'\nTry 'basename --help' for more information.\n"
        )
    if (
        sent is not None
        and os.path.basename(report)[:3]
        == os.path.basename(sent.rstrip('/') or sent)[:3]
    ):
        try:
            stamp = str(file_mtime(report))
        except OSError:
            sys.stderr.write(
                "stat: cannot read file system information for '%m': No such file or directory\n"
            )
            sys.stderr.write(
                f"stat: cannot read file system information for '{report}': No such file or directory\n"
            )
            stamp = ''
        seen = f'{report}:{stamp}'
        update(store, lambda doc: set_sent_seen(doc, seen))
    write(f'report: {report}\n')
    write(f'report_status: {status_of(ctx, report)}\n')
    wake(ctx, store, report, f'report:{status_of(ctx, report)}')
    write(queue_note(store, report))
    write(review_delta(store, report))
    return WaitOutcome(3 if state == 'blocked' else 0, 'report', paused=paused)


def steps_returned(ctx, store, running, paused, write):
    """The outcome of a wait for steps: the steps not yet reviewed, their range and what to do next, marked shown. Exit 1 when the step log is gone."""
    rows = unreviewed_steps(ctx, store)
    if rows is None:
        return WaitOutcome(1, 'steps', paused=paused)
    first = rows[0].split('\t')[0]
    write(f'steps: {len(rows)} to review on {unit_name(running)}\n')
    for row in rows:
        cols = row.split('\t')
        write(
            f'  {cols[0]} {(cols[1] if len(cols) > 1 else "")[:9]} {cols[2] if len(cols) > 2 else ""}\n'
        )
    last = rows[-1].split('\t')
    write(
        f'range: {review_base(store, running)[:9]}..{(last[1] if len(last) > 1 else "")[:9]}\n'
    )
    write(
        f'next: review the range, then pair.sh new-note {store} {os.path.basename(running.rstrip("/") or running)[:3]}\n'
    )
    try:
        k = json.loads(last[0])
    except ValueError:
        raise BadStepRow(
            f'the last step row does not start with a JSON step number: {last[0]!r}'
        ) from None
    update(store, lambda doc: set_shown(doc, unit_name(running), k))
    wake(ctx, store, running, f'steps:{first}')
    return WaitOutcome(0, 'steps', paused=paused)


def idle_returned(ctx, store, shown, paused, write):
    """The outcome of a wait whose reply the master was already shown: nothing new, so it says so and the master sends the next message."""
    sent = sent_file(store)
    sent = '-' if sent is None else sent
    write(
        f'idle: the reply to {os.path.basename(sent.rstrip("/") or sent)} was already shown ({shown}); send the next message\n'
    )
    wake(ctx, store, sent, 'idle')
    # Since notify marks a report it announced, this is the normal path to a report the master already has by message: show what the
    # report path would add. A plan response or advice carries neither.
    if peek(load(store), 'sent').get('kind') == 'BRIEF':
        write(queue_note(store, shown))
        write(review_delta(store, shown))
    return WaitOutcome(4, 'idle', paused=paused)


def finish_wait(
    ctx, store, interval, result, mode=None, now=None, quiet=False, out=None
):
    """The outcome of a wait that returned. Its stderr is written as it happens and its stdout goes to `out` (default: standard output) as it
    is produced, so what a failure interrupts has already been seen. A quiet caller records no wake for a check-in return; it records it itself
    when it returns the outcome."""
    write = sys.stdout.write if out is None else out
    report = result.ready
    mode = reply_mode(result) if mode is None else mode
    clock = now_s if now is None else (lambda: now)
    name = lambda: field(store, 'sidekick', 'name')
    state = state_line(store, result, write)
    reason = paused_reason(store)
    paused = reason is not None
    if paused:
        write(
            f'paused: {reason} (start nothing new; record the next step in gates.md and end your turn)\n'
        )
    if not report and mode == 'any':
        report = latest_report(store) or ''
    if report:
        return report_returned(ctx, store, report, state, paused, write)
    running = dispatched(store)
    if mode == 'steps':
        return steps_returned(ctx, store, running, paused, write)
    withheld = withheld_report(ctx, store)
    activity = sidekick_activity(store, clock())
    shown = seen_reply(store)
    if (
        shown
        and not withheld
        and not activity
        and result.code == 0
        and agent_status(name()) != 'working'
    ):
        return idle_returned(ctx, store, shown, paused, write)
    why, missing = '', 'report: missing\n'
    if withheld:
        write(f'report: {withheld[0]} written but not ready: {withheld[1]}\n')
        why, missing = 'report:unready', ''
    if state == 'blocked':
        write(missing)
        wake(ctx, store, running, why or 'blocked')
        write(provider_hint(store))
        return WaitOutcome(3, 'blocked', unready=bool(withheld), paused=paused)
    if agent_status(name()) == 'working' or activity:
        if not quiet:
            wake(ctx, store, running, why or 'checkin')
        digest = checkin(ctx, store, interval, clock())
        return WaitOutcome(
            4,
            'checkin',
            digest,
            tuple(
                line for line in (missing, *(digest.lines if digest else ())) if line
            ),
            bool(withheld),
            paused,
        )
    write(missing)
    wake(ctx, store, running, why or 'missing')
    write(provider_hint(store))
    return WaitOutcome(4, 'missing', unready=bool(withheld), paused=paused)


def sent_file(store):
    """The file the last send named, None when it recorded none; an empty string stays an empty string."""
    return text_at(peek(load(store), 'sent'), 'file', None)


def status_of(ctx, report):
    """header_field "$report" status, whose grep complains on stderr when the file is gone."""
    if not os.path.isfile(report):
        sys.stderr.write(
            f'{ctx.prog}: cannot read {report}: No such file or directory\n'
        )
    return header_field(report, 'status')


def mark_report_seen(store, report):
    """mark_seen plus the sent entry's seen mark, as finish_wait leaves a report it returned: no later wait returns it again."""
    mark_seen(store, report)
    sent = sent_file(store)
    if (
        sent
        and os.path.basename(report)[:3]
        == os.path.basename(sent.rstrip('/') or sent)[:3]
        and os.path.isfile(report)
    ):
        seen = f'{report}:{file_mtime(report)}'
        update(store, lambda doc: set_sent_seen(doc, seen))


def set_sent_seen(doc, seen):
    member(doc, 'sent')['seen'] = seen


def set_shown(doc, unit, k):
    member(doc, 'shown')[unit] = k


SUPERSEDED = 'superseded: a newer wait owns this store; this one ends'


def wait_owner_path(store):
    return f'{store}/wait.pid'


def read_wait_owner(store):
    """The token in <store>/wait.pid, or None when the file is absent."""
    try:
        return read_text(wait_owner_path(store)).strip()
    except OSError:
        return None


def claim_wait(store, token):
    """Take the store: write the token to wait.pid under the store lock, replacing whatever is there. The older wait ends at its next poll."""
    lock = acquire_lock(store)
    try:
        path = wait_owner_path(store)
        with open(f'{path}.tmp', 'w') as f:
            f.write(f'{token}\n')
        os.replace(f'{path}.tmp', path)
    finally:
        os.rmdir(lock)


def release_wait(store, token):
    """Remove wait.pid when it still holds this token, reading and unlinking inside one hold of the store lock: a newer owner's file stays."""
    lock = acquire_lock(store)
    try:
        if read_wait_owner(store) == token:
            os.unlink(wait_owner_path(store))
    finally:
        os.rmdir(lock)


def superseded(store, token):
    """True when wait.pid does not hold this token. A missing file counts: only a newer owner's release (or claim) removes or replaces it."""
    return read_wait_owner(store) != token


def wait_for_reply(ctx, store, name, timeout, now=None, token=None):
    """wait_for_reply(): block until the sidekick's reply lands or the interval ends, in slices so a report is seen the moment
    it is written. A settle from herdr is a hint, not proof: only one that holds for PAIR_SETTLE_HOLD seconds ends the wait."""
    result = WaitResult()
    settle_hold = env_int('PAIR_SETTLE_HOLD', 60)
    busy_poll = env_float('PAIR_BUSY_POLL', 10)
    settle_poll = env_float('PAIR_SETTLE_POLL_S', 5)
    clock = now_s if now is None else (lambda: now)
    deadline = clock() + tdiv(timeout, 1000)
    settled_at = None
    # what the top of the loop looks for: a report written in the last slice must not read as missing because the deadline ended the wait
    arrived = lambda: bool(
        pending_report(ctx, store)
        or fresh_reply(ctx, store)
        or ctx.variant.steps_due(ctx, store, dispatched(store))
    )
    while True:
        if token is not None and superseded(store, token):
            result.superseded = True
            return result
        ready = pending_report(ctx, store) or fresh_reply(ctx, store)
        if ready:
            result.ready, result.code, result.err = ready, 0, ''
            result.out = herdr_get(name)
            break
        result.ready = ''
        if ctx.variant.steps_due(ctx, store, dispatched(store)):
            result.steps_due, result.code, result.err = True, 0, ''
            result.out = herdr_get(name)
            break
        slice_ms = (deadline - clock()) * 1000
        slice_ms = min(slice_ms, 30000)
        slice_ms = max(slice_ms, 1000)
        r = subprocess.run(
            ['herdr', 'agent', 'wait', name, '--timeout', str(slice_ms)],
            capture_output=True,
            check=False,
            **TEXT,
        )
        result.code = exit_code(r.returncode)
        result.out = r.stdout.rstrip('\n')
        result.err = r.stderr.rstrip('\n')
        if result.code == 0:
            state = agent_state(result.out, None)
            if state == 'blocked':
                break
            if (
                pending_report(ctx, store)
                or fresh_reply(ctx, store)
                or ctx.variant.steps_due(ctx, store, dispatched(store))
            ):
                continue
            if (
                not pane_busy(name)
                and not sidekick_activity(store, clock())
                and seen_reply(store)
            ):
                break
            if pane_busy(name) or sidekick_activity(store, clock()):
                settled_at = None
                time.sleep(busy_poll)
                if not clock() < deadline:
                    break
                continue
            if settled_at is None:
                settled_at = clock()
            if not clock() - settled_at < settle_hold:
                break
            if not clock() < deadline:
                break
            time.sleep(settle_poll)
            continue
        settled_at = None
        code = error_code(result.err)
        if code != 'timeout':
            break
        if not clock() < deadline:
            if arrived():
                continue
            break
    return result


PR_SET_PDEATHSIG = 1


def default_signals():
    """Restore default handling of INT, TERM and HUP, so a command killed by one of them ends without a Python traceback and the signal is its
    exit status. A signal the parent ignored (a background job's SIGINT) stays ignored. kitchen step, finish, dispatch and wait call this and not
    end_with_parent: they start detached work (gates, Verify commands, a delivery) that must outlive the caller, so they must not end with it."""
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(number) is not signal.SIG_IGN:
            signal.signal(number, signal.SIG_DFL)


def end_with_parent():
    """Restore default handling of INT, TERM and HUP (an ignored signal stays ignored) so a wait killed by a signal ends without a traceback, and
    make a background wait end when its parent does: PR_SET_PDEATHSIG on Linux, a kqueue watch of the parent on macOS."""
    default_signals()
    if sys.platform == 'darwin':
        procinfo.end_when_parent_exits()
        return
    try:
        import ctypes

        ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, int(signal.SIGTERM))
    except (OSError, AttributeError):
        # best effort: a host with no Linux prctl keeps the wait, which then ends at its own timeout
        pass


def take_ownership(ctx, store, owner):
    """(token, claim_failed) for a wait: the token it checks for supersession (None checks nothing) and whether its own claim failed. Without an owner
    the wait claims the store under this pid; with one it claims only when the owner says so, and a lock that stays held must not fail a wait whose
    work is done (a dispatch has delivered its brief): it waits without ownership."""
    if owner is not None and not owner.checked:
        return None, False
    token = str(os.getpid()) if owner is None else owner.token
    if owner is None or owner.claim:
        try:
            claim_wait(store, token)
        except PairError as e:
            sys.stderr.write(
                f'{ctx.prog}: wait: could not claim wait.pid ({e.message}); waiting without ownership\n'
            )
            return None, True
    return token, False


def wait_for(
    ctx, store, *, timeout, owner=None, quiet=False, name=None, now=None, out=None
):
    """Wait for the sidekick's reply and say how the wait ended; its stdout goes to `out` as finish_wait describes. It ends superseded when a
    newer wait took the store. With no owner it owns the store under this pid and gives the claim back; with one the caller keeps the claim and
    releases it (release). quiet leaves the wake for a check-in return to the caller."""
    write = sys.stdout.write if out is None else out
    name = field(store, 'sidekick', 'name') if name is None else name
    token, claim_failed = take_ownership(ctx, store, owner)
    try:
        result = wait_for_reply(ctx, store, name, timeout, now, token=token)
        if result.superseded:
            write(f'{SUPERSEDED}\n')
            outcome = WaitOutcome(4, 'superseded')
        else:
            outcome = finish_wait(
                ctx, store, tdiv(timeout, 60000), result, now=now, quiet=quiet, out=out
            )
    finally:
        if owner is None and token is not None:
            try:
                release_wait(store, token)
            except PairError as e:
                sys.stderr.write(
                    f'{ctx.prog}: wait: could not release wait.pid ({e.message})\n'
                )
    return replace(outcome, claim_failed=claim_failed)


def release(store, owner):
    """Give the store back when it still holds the owner's token; a store with no pair.json has nothing to give back, and a release that
    cannot take the lock is dropped."""
    if os.path.isfile(f'{store}/pair.json'):
        try:
            release_wait(store, owner.token)
        except (PairError, OSError):
            pass


def print_outcome(outcome):
    """Print what follows the stdout a wait streamed to its `out` and return the exit status the command ends with."""
    sys.stdout.write(''.join(outcome.digest))
    return outcome.status


def wait_target(ctx, args, out=None):
    """(store, timeout in milliseconds) of a wait's arguments, `<store> [--timeout MS | --every MIN]`, for a store that exists; a store with no
    pair.json is refused after `state: ` goes to `out`."""
    write = sys.stdout.write if out is None else out
    require_herdr()
    end_with_parent()
    if len(args) < 1:
        raise Die(f'usage: {ctx.prog} wait <store> [--timeout MS | --every MIN]')
    store = args[0]
    opts = read(args[1:], TIMING)
    timeout = timeout_ms(opts)
    opts.refuse_leftovers()
    if not os.path.isfile(f'{store}/pair.json'):
        write('state: \n')
        raise refuse_as(MissingStore(store), ctx.prog, 2)
    return store, timeout


def wait(ctx, args):
    if len(args) == 2 and args[1] == '--release':
        # internal: gives back a claim of this process
        if not os.path.isfile(f'{args[0]}/pair.json'):
            sys.stdout.write('state: \n')
            raise refuse_as(MissingStore(args[0]), ctx.prog, 2)
        release_wait(args[0], str(os.getpid()))
        return 0
    store, timeout = wait_target(ctx, args)
    return print_outcome(wait_for(ctx, store, timeout=timeout))
