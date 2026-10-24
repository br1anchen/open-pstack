"""verify --wait: waits for the open verification's verdict and closes it.

warn_unreviewed, the accepted units that still lack the review engine's run, is here too.

Every path ends in a WaiterOutcome or in a failure it raises. The `closing` mode is the one the next `verify` uses to close a verdict nobody read."""

import json
import os
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace

from pstack_core.argv import Spec
from pstack_core.argv import read as read_options
from pstack_core.checkin import busy
from pstack_core.errors import (
    CloseHeld,
    DamagedStore,
    VerdictMissing,
    VerificationTaken,
)
from pstack_core.numbers import env_float, env_int, tdiv
from pstack_core.panes import agent_prompt, agent_status, pane_text
from pstack_core.providers import (
    agent_session_error,
    error_context,
    muse_session_error,
    prime_role_error,
)
from pstack_core.send import submit_typed
from pstack_core.store import (
    event,
    header_field,
    json_type,
    load,
    names,
    now_s,
    peek,
    read,
    read_flag,
    read_int,
    read_text,
    text_at,
    wake,
)

from . import verify_state as vs
from .landing import accepted_units, engine_met, review_engine
from .units import Verdict

NOT_GIVEN = object()


def stall_limit(idle):
    """Whether the verifier has sat idle for KITCHEN_VERIFY_IDLE_S seconds (600 by default)."""
    return idle >= env_int('KITCHEN_VERIFY_IDLE_S', 600)


def verifier_provider_error(store, name, session=NOT_GIVEN):
    """The verifier's latest provider error: from its screen while it is up, else from the error stop its session log recorded. Empty when there is
    none. session is the one of the verification the caller owns, when it captured it."""
    kind = read(store, 'verifier.kind')
    if kind == 'prime-agent':
        return prime_role_error(store, 'verifier')
    line = ''
    if agent_status(name) != 'absent':
        matches = error_context(pane_text(name).rstrip('\n'), 60, 100)
        line = matches[-1] if matches else ''
    if session is NOT_GIVEN:
        session = read(store, 'verifying.session')
    if not line:
        cwd = ''
        if read(store, 'verifier.kind') in ('pi', 'muse'):
            scratch = f'{store}/scratch/{read(store, "verifying.scratch")}'
            cwd = os.path.realpath(scratch) if os.path.isdir(scratch) else ''
        if read(store, 'verifier.kind') == 'muse':
            line = muse_session_error(cwd)
        else:
            line = agent_session_error(session, cwd)
    return line[:240]


def verifier_trail(session, packet, say):
    """What the verifier's transcript says about an attempt that ended without a clean or rejecting verdict: where it is, and whether the packet
    reached it."""
    if not session:
        return
    say(f'transcript: {session}\n')
    if not os.path.isfile(session):
        return
    try:
        seen = f'VERIFY {packet}' in read_text(session)
    except OSError:
        seen = False
    if seen:
        say('packet: the verifier received it\n')
    else:
        say(
            'packet: never reached the verifier; this attempt says nothing about the work, so rerun it\n'
        )


def verifier_shown(store, state):
    """The verifier's state for a progress line: working when its pane runs a command of its own."""
    if state in ('idle', 'done'):
        since = read_int(store, 'verifying.started')
        command = busy(state, read(store, 'verifier.pane_id'), since, now_s())
        if command:
            return f'working ({command})'
    return state


def findings(verdict):
    """The first 20 non-empty lines under the verdict's `## Findings` heading."""
    out, show = [], False
    lines = read_text(verdict).split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    for line in lines:
        if line.startswith('## Findings'):
            show = True
            continue
        if line.startswith('## '):
            show = False
        if show and line:
            out.append(line)
    return out[:20]


def rejected_count(store, unit):
    """How many of the unit's well-formed verdicts (not their packets) say `status: reject`."""
    import glob

    n = 0
    for path in sorted(
        glob.glob(f'{glob.escape(store)}/verdicts/{glob.escape(unit)}-v[0-9]*.md')
    ):
        if 'packet' in path:
            continue
        found = Verdict.parse(path)
        if found.status == 'reject' and not found.problem:
            n += 1
    return n


def delivery_note(store):
    """` (<how the grace prompt went>)`, or nothing when no delivery is recorded."""
    delivery = read(store, 'verifying.grace_delivery')
    return f' ({delivery})' if delivery else ''


CLOSED_BY_ANOTHER_CALL = 'closed by another call'
CANCELLED_BY_ANOTHER_CALL = 'cancelled by another call'

# What a lost close says of an attempt that left no verdict, by the outcome its packet records.
NO_VERDICT = {
    'provider': 'the verifier hit its provider, not the work',
    'stalled': 'the verifier sat idle and wrote no verdict',
    'missing': 'the verifier left no verdict',
}


@dataclass(frozen=True)
class WaiterOutcome:
    """How a wait on the verification ended: the status the command ends with (4 while it still runs, 5 when another call cancelled it) and the
    status a verdict, a provider stop or another call's close gave when the wait said one."""

    rc: int
    status: str = ''


class Waiter:
    """Waits on the open verification. What it prints goes to `out` and what it says on stderr to `err`, one stream's lines as they happen: a
    closing wait gets one sink for both, so the lines of a failure stay where they were said. `status` is the status line of the verdict the wait
    said, set before it is said."""

    def __init__(
        self, ctx, store, every_m, closing=False, retry=None, out=None, err=None
    ):
        self.ctx, self.store, self.closing, self.retry = ctx, store, closing, retry
        self.say, self.err = out or sys.stdout.write, err or sys.stderr.write
        self.every_m = every_m
        self.state = ''
        self.stalled = False
        self.status = ''

    def sleep(self, seconds):
        time.sleep(seconds)

    def update(self, verdict, apply):
        vs.update_verifying(self.store, verdict, apply)

    def drop_idle(self, verdict):
        self.update(verdict, lambda v: v.pop('idle_since', None))

    def grace(self, name, kind, verdict, until, brief, session):
        """At the deadline a live verifier is asked, once, to write an inconclusive verdict with what it proved and what it never reached, and given
        KITCHEN_VERIFY_GRACE_S (default 600) to write it. The grace is part of the attempt: its state is stored before the prompt is delivered, a
        later --wait resumes the stored expiry and never prompts again, and while it runs the scratch and the pane stay. A verdict already written
        wins and a provider error takes the provider path before this runs. pi, claude and codex take the prompt whether idle, done or working; a
        working Devin does not (Enter would cancel its command), so it is only waited on; a blocked or unknown agent gets no prompt and no grace.
        Returns the outcome that ends the wait when the call's interval passes inside the grace, else None."""
        store = self.store
        vs.verification_still_open(store, verdict)
        grace_until = read_int(store, 'verifying.grace_until')
        if grace_until is None:
            state = agent_status(name)
            if state in ('blocked', 'unknown', 'absent'):
                return
            now = now_s()
            grace_until = now + env_int('KITCHEN_VERIFY_GRACE_S', 600)
            delivery = 'prompted'
            if kind == 'devin' and state == 'working':
                delivery = 'skipped:devin-working'

            def stamp(verifying):
                # .verifying.grace_at, .verifying.grace_until and .verifying.grace_delivery
                (
                    verifying['grace_at'],
                    verifying['grace_until'],
                    verifying['grace_delivery'],
                ) = now, grace_until, delivery

            self.update(verdict, stamp)
            if delivery == 'prompted':
                text = (
                    f'Deadline reached: write {verdict} now with status inconclusive, listing what you proved (with its evidence) and what you never '
                    'reached, then end your turn. Stop any process you started first, and write the file complete in one write: create it under a '
                    'temporary name in the same directory, then rename it into place.'
                )
                if agent_prompt(name, text).returncode == 0:
                    submit_typed(name, os.path.basename(verdict))
                else:
                    delivery = 'skipped:prompt-failed'
                    self.update(
                        verdict, lambda v: v.__setitem__('grace_delivery', delivery)
                    )
            event(store, 'master', 'verify-grace', brief, delivery)
        while True:
            if os.path.isfile(verdict) and header_field(verdict, 'status'):
                return
            state = agent_status(name)
            vs.verification_still_open(store, verdict)
            if state == 'absent':
                return
            now = now_s()
            limit = grace_until
            if not now < limit:
                return
            if state in ('idle', 'done', 'blocked') and verifier_provider_error(
                store, name, session
            ):
                return
            if now >= until:
                self.say(
                    f'verifying: units {read(store, "verifying.units")} by {kind}, past its deadline; grace {limit - now}s left'
                    f'{delivery_note(store)}, verifier {verifier_shown(store, state)}\n'
                )
                self.say(f'next: other work, then kitchen.sh verify {store} --wait\n')
                wake(self.ctx, store, brief, 'verify:grace')
                return WaiterOutcome(4)
            self.sleep(env_float('KITCHEN_VERIFY_GRACE_POLL_S', 5))

    def run(self):
        v = self.snapshot()
        try:
            return self.wait(v)
        except VerificationTaken:
            return self.lose(v)

    def lose(self, v):
        """Another call took the close of the verification this wait owns. This wait reports the attempt it took, not whatever `.verifying` names
        by now (a fallback may have started), and repeats none of the close: it waits for the winner to end it, then reads the attempt's packet
        outcome and its verdict. A cancelled attempt has no verdict and ends with 5; any other ends as the verdict says, 0 when clean and 2
        otherwise. A close that does not end within KITCHEN_VERIFY_CLOSE_WAIT_S is a refusal. A closing wait (the next `verify` closing a verdict
        nobody read) says nothing: its caller prints the line."""
        self.await_close(v)
        outcome = header_field(v.packet, 'verifier-outcome')
        present = os.path.isfile(v.verdict)
        if outcome == 'cancelled' or not (present or outcome in NO_VERDICT):
            if not self.closing:
                self.say(vs.CLOSED_ELSEWHERE)
            return WaiterOutcome(5, CANCELLED_BY_ANOTHER_CALL)
        if present:
            problem, status = self.verdict_status(v.verdict)
            self.status = f'{status} ({problem})' if problem else status
        else:
            status = 'inconclusive'
            self.status = f'inconclusive ({NO_VERDICT[outcome]})'
        rc = 0 if status == 'clean' else 2
        if self.closing:
            return WaiterOutcome(rc, CLOSED_BY_ANOTHER_CALL)
        self.say(
            f'verdict: {v.verdict if present else "none"}\nstatus: {self.status} (closed by another verify --wait)\n'
        )
        if status == 'reject':
            for line in findings(v.verdict):
                self.say(line + '\n')
        return WaiterOutcome(rc, self.status)

    def await_close(self, v):
        """Waits while `.verifying` still names the attempt this wait took, for KITCHEN_VERIFY_CLOSE_WAIT_S (120 by default) at most. A closing call that is gone (its claim released, or held by a dead process on this host) ends the wait at once."""
        store = self.store
        since = None
        while read(store, 'verifying.verdict') == v.verdict:
            if since is None:
                since = now_s()
                sys.stdout.write(
                    f'closing {v.verdict}: another call is closing it; waiting for it\n'
                )
                sys.stdout.flush()
            token = read(store, 'verifying.closing.token')
            # Read the verdict after the token: a close that finished between the two reads leaves no token and no verification.
            gone = not token or vs.claim_gone(token)
            if gone and read(store, 'verifying.verdict') == v.verdict:
                raise CloseHeld(
                    f'the call closing {v.verdict} ended without closing it; kitchen.sh verify {store} --wait'
                )
            limit = env_int('KITCHEN_VERIFY_CLOSE_WAIT_S', 120)
            if not now_s() - since < limit:
                raise CloseHeld(
                    f'another call has held the close of {v.verdict} for {limit}s; kitchen.sh verify {store} --wait'
                )
            self.sleep(env_float('KITCHEN_VERIFY_POLL_S', 10))

    @staticmethod
    def verdict_status(verdict):
        """(the verdict's problem, its status): a verdict with a problem is `invalid`, whatever its status line says."""
        problem = vs.verdict_problem(verdict)
        return problem, 'invalid' if problem else header_field(verdict, 'status')

    def wait(self, v):
        self.send_unsent_packet(v)
        state, ended = self.poll(v)
        if ended:
            return ended
        provider, ended = self.settle(v, state)
        if ended:
            return ended
        problem, status = self.record_outcome(v, provider)
        return self.report(v, state, provider, problem, status)

    def snapshot(self):
        """The verification this wait took, read once: after another call closes it and starts the next, these still describe the one this wait owns."""
        store = self.store
        v = SimpleNamespace(
            verdict=read(store, 'verifying.verdict'),
            name=read(store, 'verifier.name'),
            kind=read(store, 'verifier.kind'),
            packet=read(store, 'verifying.packet'),
            session=read(store, 'verifying.session'),
            brief=read(store, 'verifying.brief'),
            risk=read(store, 'verifying.risk'),
        )
        sample = peek(load(store), 'verifying').get('sample')
        v.sample = 0 if sample is None else sample
        if isinstance(v.sample, bool) or not isinstance(v.sample, (int, float)):
            raise DamagedStore(
                f'verifying.sample is {json_type(v.sample)}, not a number'
            )
        v.unit, v.pane = read(store, 'verifying.unit'), read(store, 'verifier.pane_id')
        v.fallback, v.retried, v.argv = (
            self.fallback(),
            read_flag(store, 'verifying.retried') is True,
            self.argv(),
        )
        v.until = now_s() + self.every_m * 60
        return v

    def send_unsent_packet(self, v):
        """A verify stopped between the verifier's start and its packet leaves the verifier idle with nothing to do, until the stall check closes it."""
        store = self.store
        if read_flag(store, 'verifying.delivered') is False and not os.path.isfile(
            v.verdict
        ):
            vs.verification_still_open(store, v.verdict)
            if agent_status(v.name) in ('idle', 'done'):
                vs.redeliver_packet(
                    self.ctx,
                    store,
                    'the verify that started this verifier stopped before sending its packet',
                    v.name,
                    v.packet,
                    v.brief,
                    v.kind,
                    self.say,
                )
            self.update(v.verdict, lambda doc: doc.__setitem__('delivered', True))

    def poll(self, v):
        """Watch the verifier until a verdict is written, the deadline passes, the agent is gone, it ends its turn on a provider error, or it sits idle
        too long: (the agent's last state, None). At the interval it ends the call with a progress digest: (the state, the outcome with status 4)."""
        store, verdict, name = self.store, v.verdict, v.name
        state = ''
        while True:
            if os.path.isfile(verdict) and header_field(verdict, 'status'):
                break
            state = agent_status(name)
            vs.verification_still_open(store, verdict)
            now = now_s()
            deadline = read_int(store, 'verifying.deadline')
            if not (state != 'absent' and deadline is not None and now < deadline):
                break
            if now >= v.until:
                started = read_int(store, 'verifying.started')
                self.say(
                    f'verifying: units {read(store, "verifying.units")} by {v.kind}, {tdiv(now - started, 60)}m of {tdiv(deadline - started, 60)}m, verifier '
                    f'{verifier_shown(store, state)}\n'
                )
                self.say(f'next: other work, then kitchen.sh verify {store} --wait\n')
                wake(self.ctx, store, read(store, 'verifying.brief'), 'verify:progress')
                return state, WaiterOutcome(4)
            # A verifier that ended its turn on a provider error will not write.
            idle_since = read_int(store, 'verifying.idle_since')
            if state == 'blocked':
                if read(
                    store, 'verifier.kind'
                ) == 'prime-agent' and verifier_provider_error(store, name, v.session):
                    break
                if idle_since is not None:
                    self.drop_idle(verdict)
            elif state in ('idle', 'done'):
                if verifier_provider_error(store, name, v.session):
                    break
                started = read_int(store, 'verifying.started')
                if busy(state, read(store, 'verifier.pane_id'), started, now_s()):
                    if idle_since is not None:
                        self.drop_idle(verdict)
                elif idle_since is None:
                    self.update(
                        verdict, lambda doc, at=now: doc.__setitem__('idle_since', at)
                    )
                elif stall_limit(now - idle_since):
                    self.stalled = True
                    break
            elif idle_since is not None:
                self.drop_idle(verdict)
            self.sleep(env_float('KITCHEN_VERIFY_POLL_S', 10))
        return state, None

    def settle(self, v, state):
        """After the poll: a verifier still alive at the deadline is asked once for what it proved, a verdict just written is given a moment, and the
        verification is closed: (the provider's error when the verifier left no verdict because of it, else '', the outcome that ends the wait
        when the interval passes in the grace, else None). Another call's close of the verification raises VerificationTaken."""
        store, verdict, name = self.store, v.verdict, v.name
        self.state = state
        vs.verification_still_open(store, verdict)
        written = os.path.isfile(verdict) and header_field(verdict, 'status')
        deadline = read_int(store, 'verifying.deadline')
        if (
            not written
            and state != 'absent'
            and deadline is not None
            and now_s() >= deadline
            and not verifier_provider_error(store, name, v.session)
        ):
            ended = self.grace(name, v.kind, verdict, v.until, v.brief, v.session)
            if ended:
                return '', ended
            vs.verification_still_open(store, verdict)
        # A verdict just written may still be closing; give the writer a moment.
        if os.path.isfile(verdict):
            self.sleep(env_float('KITCHEN_VERDICT_SETTLE_S', 3))
        vs.verification_still_open(store, verdict)
        provider = (
            ''
            if os.path.isfile(verdict)
            else verifier_provider_error(store, name, v.session)
        )
        if not vs.run_close(self.ctx, store, name, v.kind, v.pane, verdict, self.err):
            raise VerificationTaken()
        # The wait may have been long (the verifier's exit); the claim must still be ours before anything is written.
        vs.still_my_claim(store)
        return provider, None

    def record_outcome(self, v, provider):
        """The attempt's outcome is written to its packet before .verifying goes and before any fallback starts, so fleet can tell attempts apart.
        (the verdict's problem, its status)."""
        problem = status = ''
        if os.path.isfile(v.verdict):
            problem, status = self.verdict_status(v.verdict)
            vs.set_outcome(v.packet, status)
        else:
            vs.set_outcome(
                v.packet,
                'provider' if provider else 'stalled' if self.stalled else 'missing',
            )
        vs.drop_verifying(self.store, v.verdict)
        if status not in ('clean', 'reject'):
            verifier_trail(v.session, v.packet, self.say)
        return problem, status

    def report(self, v, state, provider, problem, status):
        """What the master reads and the outcome: a verdict's status and next step, or why there is none."""
        store, verdict, unit = self.store, v.verdict, v.unit
        say = self.say
        present = os.path.isfile(verdict)
        if not present and provider:
            self.status = 'inconclusive (the verifier hit its provider, not the work)'
            say(f'verdict: none\nstatus: {self.status}\nprovider_error: {provider}\n')
            fallback_kind = text_at(v.fallback, 'kind')
            if (
                self.retry
                and not v.retried
                and v.fallback
                and fallback_kind != v.kind
                and v.argv
            ):
                say(f'retry: verifying again on the fallback, {fallback_kind}\n')
                sys.stdout.flush()
                again = [
                    store,
                    *v.argv,
                    '--retry-entry',
                    json.dumps(v.fallback, ensure_ascii=False, separators=(',', ':')),
                ]
                return WaiterOutcome(
                    self.retry(again), self.status
                )  # verifying again on the fallback, as the exec of kitchen.sh verify did
            wake(self.ctx, store, v.brief, 'verify:provider')
            # A retried call carries its entry; the advice is for the master's own call.
            say(
                f'next: wait for the limit, or kitchen.sh verify {store} {" ".join(self.shown(v.argv))} --kind <another kind>\n'
            )
            return WaiterOutcome(2, self.status)
        if not present and self.stalled:
            idle_limit = env_int('KITCHEN_VERIFY_IDLE_S', 600)
            wake(self.ctx, store, v.brief, 'verify:stalled')
            raise VerdictMissing(
                f'no verdict at {verdict}: the verifier sat idle {idle_limit // 60}m with nothing running; rerun: '
                f'kitchen.sh verify {store} {" ".join(self.shown(v.argv))}'
            )
        if not present:
            wake(self.ctx, store, v.brief, 'verify:missing')
            raise VerdictMissing(
                f'no verdict at {verdict} (verifier {state or "gone"}); read the packet and verify by hand, or rerun'
            )
        event(store, 'master', 'verify', v.brief, status)
        if not self.closing:
            wake(self.ctx, store, v.brief, f'verify:{status.split(" ")[0]}')
        self.status = f'{status} ({problem})' if problem else status
        say(f'verdict: {verdict}\nstatus: {self.status}\n')
        if status == 'clean':
            if v.risk == 'routine' and vs.audit_due(unit, v.sample):
                say(
                    'audit: yes; read the review-delta yourself before accepting, and kitchen.sh catch what the kitchen missed\n'
                )
            else:
                say('audit: no\n')
            warn_unreviewed(store, say)
            vs.handoff_due(store, say)
            return WaiterOutcome(0, self.status)
        if status == 'reject':
            for line in findings(verdict):
                say(line + '\n')
            if unit == 'landing':
                say(
                    f'next: kitchen.sh revise {store} {verdict}, then kitchen.sh verify {store} --landing again\n'
                )
            elif rejected_count(store, unit) >= 2:
                say(
                    f'next: second rejection of {unit}; read the verdict and the unit yourself before another fix\n'
                )
            else:
                say(
                    f'next: kitchen.sh revise {store} {verdict}, then verify the fix with --covers {unit[:3]}\n'
                )
            return WaiterOutcome(2, self.status)
        say(
            "next: read the verdict; an inconclusive or invalid one is the master's to settle\n"
        )
        return WaiterOutcome(2, self.status)

    @staticmethod
    def shown(argv):
        """The arguments a retried call carried, less its --retry-entry and that entry."""
        return read_options(
            argv, Spec(valued=('--retry-entry',), unknown='collect')
        ).stripped('--retry-entry')

    def fallback(self):
        """verifying.entry.fallback as an object, {} when there is none."""
        fallback = peek(peek(load(self.store), 'verifying'), 'entry').get('fallback')
        if fallback is None:
            return {}
        if not isinstance(fallback, dict):
            raise DamagedStore(
                f'verifying.entry.fallback is {json_type(fallback)}, not an object'
            )
        return fallback

    def argv(self):
        """The arguments the verifier was started with, one per element of verifying.argv."""
        return names(peek(load(self.store), 'verifying'), 'argv')


def warn_unreviewed(store, say=None):
    """A `warning: unreviewed:` line for each accepted unit whose review note lacks the run the kitchen's [review].engine requires, said to `say`
    (default: standard output); nothing when no engine is set."""
    say = say or sys.stdout.write
    engine = review_engine(store)
    if not engine:
        return
    for review, seq, brief in accepted_units(store):
        met, gap = engine_met(store, brief, review, engine)
        if not met:
            say(
                f'warning: unreviewed: unit {seq} cites no {engine} run; review its range with {engine} and add "engine: {engine} <run id or artifact path>" to {review}{gap}\n'
            )
