"""verify --wait: waits for the open verification's verdict and closes it. Ported from kitchen.sh at b632713 (verify_wait, verify_grace, verifier_trail,
verifier_provider_error, verifier_shown, and the `closing=1` mode the next `verify` uses to close a verdict nobody read).

Every path of the bash ended in `exit`, in the shell that ran cmd_verify; here each ends in Exit (or Die). In the `closing` mode the bash ran inside
a command substitution, where errexit is off, so a plain command that fails there does not end the command; `Waiter.errexit` says which."""

import json
import os
import re
import subprocess
import sys
import time

from pstack_core.checkin import busy_command
from pstack_core.errors import Die, Exit
from pstack_core.herdr import agent_status, herdr, pane_text
from pstack_core.providers import agent_session_error, grep_o_context, muse_session_error, prime_role_error
from pstack_core.review import jq_raw
from pstack_core.send import submit_typed
from pstack_core.store import bash_int, event, header_field, readlink_f
from pstack_core.wait import bash_sleep

from . import verify_state as vs
from .reviews import warn_unreviewed

NOT_GIVEN = object()


def now_s():
    return int(time.time())


def tdiv(a, b):
    """Bash's integer division, which truncates toward zero."""
    return int(a / b) if abs(a) < 2 ** 52 else (abs(a) // b) * (1 if a >= 0 else -1)


def stall_limit(idle):
    """`[ idle -ge "${KITCHEN_VERIFY_IDLE_S:-600}" ]`: a limit that is no integer is a test error, which is false."""
    limit = bash_int(os.environ.get('KITCHEN_VERIFY_IDLE_S') or '600')
    return limit is not None and idle >= limit


def env_int(name, default):
    """`${NAME:-default}` as bash arithmetic reads it for the cases the kitchen sets: a whole number."""
    value = bash_int(os.environ.get(name) or str(default))
    if value is None:
        raise Exit(1)
    return value


def verifier_provider_error(store, name, session=NOT_GIVEN):
    """The verifier's latest provider error: from its screen while it is up, else from the error stop its session log recorded. Empty when there is
    none. session is the one of the verification the caller owns, when it captured it."""
    kind = vs.field(store, '.verifier.kind')
    if kind == 'prime-agent':
        return prime_role_error(store, 'verifier')
    line = ''
    if agent_status(name) != 'absent':
        matches = grep_o_context(pane_text(name).rstrip('\n'), 60, 100)
        line = matches[-1] if matches else ''
    if session is NOT_GIVEN:
        session = vs.field(store, '.verifying.session // empty')
    if not line:
        cwd = ''
        if vs.field(store, '.verifier.kind') in ('pi', 'muse'):
            try:
                cwd = readlink_f(f'{store}/scratch/{vs.field(store, ".verifying.scratch")}')
            except Exit:  # `readlink -f ... 2>/dev/null || true`
                cwd = ''
        if vs.field(store, '.verifier.kind') == 'muse':
            line = muse_session_error(cwd)
        else:
            line = agent_session_error(session, cwd)
    return line[:240]


def verifier_trail(session, packet):
    """What the verifier's transcript says about an attempt that ended without a clean or rejecting verdict: where it is, and whether the packet
    reached it."""
    if not session:
        return
    sys.stdout.write(f'transcript: {session}\n')
    if not os.path.isfile(session):
        return
    try:
        with open(session, 'rb') as f:
            seen = f'VERIFY {packet}'.encode('utf-8', 'surrogateescape') in f.read()
    except OSError:
        seen = False
    if seen:
        sys.stdout.write('packet: the verifier received it\n')
    else:
        sys.stdout.write('packet: never reached the verifier; this attempt says nothing about the work, so rerun it\n')


def verifier_shown(store, state):
    """The verifier's state for a progress line: working when its pane runs a command of its own."""
    if state in ('idle', 'done'):
        since = bash_int(vs.field(store, '.verifying.started'))
        command = busy_command(vs.field(store, '.verifier.pane_id'), since, now_s()) if since is not None else ''
        if command:
            return f'working ({command})'
    return state


def findings(verdict):
    """`awk '/^## Findings/{p=1; next} /^## /{p=0} p' | sed '/^$/d' | head -20`."""
    out, show = [], False
    with open(verdict, 'rb') as f:
        lines = f.read().decode('utf-8', 'surrogateescape').split('\n')
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
    """`grep -l '^status: reject' "$store/verdicts/$unit"-v[0-9]*.md | grep -vc packet`."""
    import glob
    from pstack_core.collate import glob_sorted
    n = 0
    for path in glob_sorted(glob.glob(f'{glob.escape(store)}/verdicts/{glob.escape(unit)}-v[0-9]*.md')):
        try:
            with open(path, 'rb') as f:
                hit = any(line.startswith('status: reject') for line in f.read().decode('utf-8', 'surrogateescape').split('\n'))
        except OSError:
            continue
        if hit and 'packet' not in path:
            n += 1
    return n


class Waiter:
    def __init__(self, ctx, store, every_m, closing=False, retry=None):
        self.ctx, self.store, self.closing, self.retry = ctx, store, closing, retry
        self.errexit = not closing
        self.every_m = every_m
        self.state = ''
        self.stalled = False

    def rd(self, program):
        """A plain `x="$(field ...)"`: under errexit a failing read ends the command with jq's status."""
        return vs.field(self.store, program, check=self.errexit)

    def sleep(self, text):
        try:
            bash_sleep(text)
        except Exit:
            if self.errexit:
                raise

    def update(self, verdict, body, apply, *jq_args):
        vs.update_verifying(self.store, verdict, body, apply, *jq_args, errexit=self.errexit)

    def drop_idle(self, verdict):
        self.update(verdict, 'del(.verifying.idle_since)', lambda v: v.pop('idle_since', None))

    def grace(self, name, kind, verdict, until, brief, session):
        """At the deadline a live verifier is asked, once, to write an inconclusive verdict with what it proved and what it never reached, and given
        KITCHEN_VERIFY_GRACE_S (default 600) to write it. The grace is part of the attempt: its state is stored before the prompt is delivered, a
        later --wait resumes the stored expiry and never prompts again, and while it runs the scratch and the pane stay. A verdict already written
        wins and a provider error takes the provider path before this runs. pi, claude and codex take the prompt whether idle, done or working; a
        working Devin does not (Enter would cancel its command), so it is only waited on; a blocked or unknown agent gets no prompt and no grace."""
        store = self.store
        vs.verification_still_open(store, verdict)
        grace_until = vs.field(store, '.verifying.grace_until // empty')
        if not grace_until:
            state = agent_status(name)
            if state in ('blocked', 'unknown', 'absent'):
                return
            now = now_s()
            grace_until = str(now + env_int('KITCHEN_VERIFY_GRACE_S', 600))
            delivery = 'prompted'
            if kind == 'devin' and state == 'working':
                delivery = 'skipped:devin-working'

            def stamp(verifying):
                verifying['grace_at'], verifying['grace_until'], verifying['grace_delivery'] = now, int(grace_until), delivery

            self.update(verdict, '.verifying.grace_at = $at | .verifying.grace_until = $until | .verifying.grace_delivery = $d', stamp,
                        '--argjson', 'at', str(now), '--argjson', 'until', grace_until, '--arg', 'd', delivery)
            if delivery == 'prompted':
                text = (f'Deadline reached: write {verdict} now with status inconclusive, listing what you proved (with its evidence) and what you never '
                        'reached, then end your turn. Stop any process you started first, and write the file complete in one write: create it under a '
                        'temporary name in the same directory, then rename it into place.')
                if herdr('agent', 'prompt', name, text)[0] == 0:
                    submit_typed(name, os.path.basename(verdict))
                else:
                    delivery = 'skipped:prompt-failed'
                    self.update(verdict, '.verifying.grace_delivery = $d', lambda v: v.__setitem__('grace_delivery', delivery), '--arg', 'd', delivery)
            event(store, 'master', 'verify-grace', brief, delivery)
        while True:
            if os.path.isfile(verdict) and header_field(verdict, 'status'):
                return
            state = agent_status(name)
            vs.verification_still_open(store, verdict)
            if state == 'absent':
                return
            now = now_s()
            limit = bash_int(grace_until)
            if limit is None or not now < limit:
                return
            if state in ('idle', 'done', 'blocked') and verifier_provider_error(store, name, session):
                return
            if now >= until:
                sys.stdout.write(f'verifying: units {vs.field(store, ".verifying.units")} by {kind}, past its deadline; grace {limit - now}s left '
                                 f'({vs.field(store, ".verifying.grace_delivery")}), verifier {verifier_shown(store, state)}\n')
                sys.stdout.write(f'next: other work, then kitchen.sh verify {store} --wait\n')
                event(store, 'master', 'wake', brief, 'verify:grace')
                raise Exit(4)
            self.sleep(os.environ.get('KITCHEN_VERIFY_GRACE_POLL_S') or '5')

    def run(self):
        ctx, store = self.ctx, self.store
        # The verification this wait took, read once: after another call closes it and starts the next, these still describe the one this wait owns.
        verdict = self.rd('.verifying.verdict')
        name = self.rd('.verifier.name')
        kind = self.rd('.verifier.kind')
        packet = self.rd('.verifying.packet')
        session = self.rd('.verifying.session // empty')
        brief = self.rd('.verifying.brief')
        risk = self.rd('.verifying.risk')
        sample = self.rd('.verifying.sample')
        unit = self.rd('.verifying.unit')
        pane = self.rd('.verifier.pane_id')
        fallback = self.fallback()
        retried = self.rd('.verifying.retried // false')
        argv = self.argv()
        every = self.arithmetic(self.every_m)
        until = now_s() + every * 60
        # A verify stopped between the verifier's start and its packet leaves the verifier idle with nothing to do, until the stall check closes it.
        if vs.field(store, '.verifying.delivered') == 'false' and not os.path.isfile(verdict):
            vs.verification_still_open(store, verdict)
            if agent_status(name) in ('idle', 'done'):
                vs.redeliver_packet(ctx, store, 'the verify that started this verifier stopped before sending its packet', name, packet, brief, kind)
            self.update(verdict, '.verifying.delivered = true', lambda v: v.__setitem__('delivered', True))
        state = ''
        while True:
            if os.path.isfile(verdict) and header_field(verdict, 'status'):
                break
            state = agent_status(name)
            vs.verification_still_open(store, verdict)
            now = now_s()
            deadline = bash_int(vs.field(store, '.verifying.deadline'))
            if not (state != 'absent' and deadline is not None and now < deadline):
                break
            if now >= until:
                started = vs.shell_arithmetic(vs.field(store, '.verifying.started'))
                span = tdiv(deadline - started, 60)
                done = tdiv(now - started, 60)
                sys.stdout.write(f'verifying: units {vs.field(store, ".verifying.units")} by {kind}, {done}m of {span}m, verifier '
                                 f'{verifier_shown(store, state)}\n')
                sys.stdout.write(f'next: other work, then kitchen.sh verify {store} --wait\n')
                event(store, 'master', 'wake', vs.field(store, '.verifying.brief'), 'verify:progress')
                raise Exit(4)
            # A verifier that ended its turn on a provider error will not write.
            idle_since = vs.field(store, '.verifying.idle_since // empty')
            if state == 'blocked':
                if vs.field(store, '.verifier.kind') == 'prime-agent' and verifier_provider_error(store, name, session):
                    break
                if idle_since:
                    self.drop_idle(verdict)
            elif state in ('idle', 'done'):
                if verifier_provider_error(store, name, session):
                    break
                started = bash_int(vs.field(store, '.verifying.started'))
                busy = busy_command(vs.field(store, '.verifier.pane_id'), started, now_s()) if started is not None else ''
                if busy:
                    if idle_since:
                        self.drop_idle(verdict)
                elif not idle_since:
                    self.update(verdict, '.verifying.idle_since = $at', lambda v, at=now: v.__setitem__('idle_since', at), '--argjson', 'at', str(now))
                elif stall_limit(now - vs.shell_arithmetic(idle_since)):
                    self.stalled = True
                    break
            elif idle_since:
                self.drop_idle(verdict)
            self.sleep(os.environ.get('KITCHEN_VERIFY_POLL_S') or '10')
        self.state = state
        vs.verification_still_open(store, verdict)
        # A verifier still alive at the deadline is asked once for what it proved.
        written = os.path.isfile(verdict) and header_field(verdict, 'status')
        deadline = bash_int(vs.field(store, '.verifying.deadline'))
        if (not written and state != 'absent' and deadline is not None and now_s() >= deadline
                and not verifier_provider_error(store, name, session)):
            self.grace(name, kind, verdict, until, brief, session)
            vs.verification_still_open(store, verdict)
        # A verdict just written may still be closing; give the writer a moment.
        if os.path.isfile(verdict):
            self.sleep(os.environ.get('KITCHEN_VERDICT_SETTLE_S') or '3')
        vs.verification_still_open(store, verdict)
        provider = ''
        if not os.path.isfile(verdict):
            provider = verifier_provider_error(store, name, session)
        if not vs.run_close(ctx, store, name, kind, pane, verdict):
            sys.stdout.write('verification closed elsewhere\n')
            raise Exit(5)
        # The wait may have been long (the verifier's exit); the claim must still be ours before anything is written.
        vs.still_my_claim(store)
        # The attempt's outcome is written to its packet before .verifying goes and before any fallback starts, so fleet can tell attempts apart.
        problem = status = ''
        if os.path.isfile(verdict):
            problem = vs.verdict_problem(verdict)
            status = header_field(verdict, 'status')
            if problem:
                status = 'invalid'
            vs.set_outcome(packet, status)
        elif provider:
            vs.set_outcome(packet, 'provider')
        elif self.stalled:
            vs.set_outcome(packet, 'stalled')
        else:
            vs.set_outcome(packet, 'missing')
        vs.drop_verifying(store, verdict, errexit=self.errexit)
        if status not in ('clean', 'reject'):
            verifier_trail(session, packet)
        present = os.path.isfile(verdict)
        if not present and provider:
            sys.stdout.write(f'verdict: none\nstatus: inconclusive (the verifier hit its provider, not the work)\nprovider_error: {provider}\n')
            fallback_kind = self.fallback_kind(fallback)
            if retried == 'false' and fallback and fallback_kind != kind and argv:
                sys.stdout.write(f'retry: verifying again on the fallback, {fallback_kind}\n')
                sys.stdout.flush()
                again = [store, *argv, '--retry-entry', fallback]
                self.retry(again)  # verifying again on the fallback, as the exec of kitchen.sh verify did; it never returns
            event(store, 'master', 'wake', brief, 'verify:provider')
            # A retried call carries its entry; the advice is for the master's own call.
            sys.stdout.write(f'next: wait for the limit, or kitchen.sh verify {store} {" ".join(self.shown(argv))} --kind <another kind>\n')
            raise Exit(2)
        if not present and self.stalled:
            event(store, 'master', 'wake', brief, 'verify:stalled')
            raise Die(f'no verdict at {verdict}: the verifier sat idle {tdiv(vs.shell_arithmetic(os.environ.get("KITCHEN_VERIFY_IDLE_S") or "600"), 60)}m with nothing running; rerun: '
                      f'kitchen.sh verify {store} {" ".join(self.shown(argv))}', 4)
        if not present:
            event(store, 'master', 'wake', brief, 'verify:missing')
            raise Die(f'no verdict at {verdict} (verifier {state or "gone"}); read the packet and verify by hand, or rerun', 4)
        event(store, 'master', 'verify', brief, status)
        if not self.closing:
            event(store, 'master', 'wake', brief, f'verify:{status.split(" ")[0]}')
        sys.stdout.write(f'verdict: {verdict}\nstatus: {status}{f" ({problem})" if problem else ""}\n')
        if status == 'clean':
            if risk == 'routine' and vs.audit_due(unit, sample):
                sys.stdout.write('audit: yes; read the review-delta yourself before accepting, and kitchen.sh catch what the kitchen missed\n')
            else:
                sys.stdout.write('audit: no\n')
            warn_unreviewed(store)
            vs.handoff_due(store, unit, errexit=self.errexit)
            raise Exit(0)
        if status == 'reject':
            for line in findings(verdict):
                sys.stdout.write(line + '\n')
            rejects = rejected_count(store, unit)
            if unit == 'landing':
                sys.stdout.write(f'next: kitchen.sh revise {store} {verdict}, then kitchen.sh verify {store} --landing again\n')
            elif rejects >= 2:
                sys.stdout.write(f'next: second rejection of {unit}; read the verdict and the unit yourself before another fix\n')
            else:
                sys.stdout.write(f'next: kitchen.sh revise {store} {verdict}, then verify the fix with --covers {unit[:3]}\n')
            raise Exit(2)
        sys.stdout.write("next: read the verdict; an inconclusive or invalid one is the master's to settle\n")
        raise Exit(2)

    @staticmethod
    def shown(argv):
        """The arguments a retried call carried, less its --retry-entry and that entry."""
        out, i = [], 0
        while i < len(argv):
            if argv[i] == '--retry-entry':
                i += 2
                continue
            out.append(argv[i])
            i += 1
        return out

    @staticmethod
    def fallback_kind(fallback):
        try:
            value = json.loads(fallback)
        except ValueError:
            return ''
        return jq_raw(value.get('kind')) if isinstance(value, dict) else ''

    def fallback(self):
        """`field "$store" '.verifying.entry.fallback // empty' | jq -c . 2>/dev/null || true`."""
        raw = vs.field(self.store, '.verifying.entry.fallback // empty')
        if not raw:
            return ''
        r = subprocess.run(['jq', '-c', '.'], input=raw.encode('utf-8', 'surrogateescape'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')

    def argv(self):
        """`mapfile -t argv < <(jq -r '.verifying.argv[]?' pair.json)`."""
        sys.stdout.flush()
        sys.stderr.flush()
        r = subprocess.run(['jq', '-r', '.verifying.argv[]?', f'{self.store}/pair.json'], stdout=subprocess.PIPE, check=False)
        text = r.stdout.decode('utf-8', 'surrogateescape')
        return text.removesuffix('\n').split('\n') if text else []

    arithmetic = staticmethod(vs.shell_arithmetic)
