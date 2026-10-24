"""The open verification's state and its close: who may write `.verifying`, who owns the close, and what closing does (verifier, pane, scratch).

This module alone writes `.verifying` and `.verifying.closing`. A parsed snapshot of `.verifying` is never authority: each write goes through
`update_verifying`, which re-reads pair.json under the lock and acts only while `.verifying.verdict` is still the verdict the caller holds (the
compare-and-set), and the claim of a close is the token `<host>:<pid>:<n>` (host from `$HOSTNAME`; pid that of the running
command's process, forked children included; n a random number below 32768) written by the first closer only."""

import contextlib
import io
import os
import random
import re
import socket
import sys
import time
import zlib

from pstack_core.commands.scratch import remove_scratch
from pstack_core.errors import (
    DamagedStore,
    NoVerification,
    PrimeFailed,
    ToolFailed,
    VerificationTaken,
    translate,
)
from pstack_core.exits import end_agent, stop_prime_role
from pstack_core.numbers import env_int, int_or_none
from pstack_core.panes import agent_prompt, herdr
from pstack_core.send import submit_typed
from pstack_core.store import (
    count,
    event,
    header_field,
    json_type,
    member,
    peek,
    read,
    read_int,
    read_text,
    update,
    write_text,
)


def hostname():
    return os.environ.get('HOSTNAME') or socket.gethostname() or 'localhost'


def names_verdict(doc, verdict):
    """Whether `.verifying` names the verification with verdict: false for no verification, damage for a `.verifying` that is not an object."""
    return peek(doc, 'verifying').get('verdict') == verdict


def update_verifying(store, verdict, apply):
    """json_update that applies `apply(verifying)` only while the store still names the verification with verdict. A write that lands after
    another call closed it changes nothing."""

    def mutate(doc):
        if names_verdict(doc, verdict):
            apply(doc['verifying'])

    update(store, mutate)


def drop_verifying(store, verdict):
    """Clears `.verifying` only while it still names the verification with verdict: another call may have closed it and started the next."""

    def mutate(doc):
        if names_verdict(doc, verdict):
            del doc['verifying']

    update(store, mutate)


def alive(pid):
    """`kill -0 $pid 2>/dev/null`: a process this user may signal. A process of another user (EPERM) and a pid that is no number read as gone."""
    number = int_or_none(pid)
    if number is None:
        return False
    try:
        os.kill(number, 0)
    except (OverflowError, OSError):
        return False
    return True


def claim_gone(token):
    """Whether the process a claim token names is gone on this host. A token from another host is never gone, whatever its pid."""
    host, _, rest = token.partition(':')
    if host != hostname():
        return False
    return not alive(rest.partition(':')[0])


def claim_close(store, verdict):
    """Claims the close of the verification with verdict: one atomic update of pair.json that succeeds for the first caller only. The claim names
    this host and process; a claim whose process is gone on this host is taken over, one from another host is never touched, a live one is
    respected. False when the verification is gone or another call holds the claim."""
    token = f'{hostname()}:{os.getpid()}:{random.randrange(32768)}'
    current = read(store, 'verifying.closing.token')
    if current and not claim_gone(current):
        return False

    def apply(verifying):
        if (alt_token(verifying.get('closing')) or '') == current:
            verifying['closing'] = {'token': token}

    update_verifying(store, verdict, apply)
    return read(store, 'verifying.closing.token') == token


def alt_token(closing):
    """The `.closing.token` of a value the document holds, null and false counting as none: a string token, or None."""
    if isinstance(closing, dict):
        token = closing.get('token')
        return token if token not in (None, False) else None
    if closing is None:
        return None
    raise DamagedStore(f'closing is {json_type(closing)}, not an object')


def release_close(store, verdict, token):
    """Gives up the claim token on the verification with verdict, when it is still the holder's."""

    def apply(verifying):
        closing = verifying.get('closing')
        if isinstance(closing, dict):
            if closing.get('token') == token:
                del verifying['closing']
        elif closing is not None:
            raise DamagedStore(f'closing is {json_type(closing)}, not an object')

    with contextlib.redirect_stderr(io.StringIO()):
        update_verifying(store, verdict, apply)


def my_claim(store):
    """Whether the claim in the store is this process's (the token names this host and pid)."""
    return read(store, 'verifying.closing.token').startswith(
        f'{hostname()}:{os.getpid()}:'
    )


CLOSED_ELSEWHERE = 'verification closed elsewhere\n'


def still_my_claim(store):
    """After close_verifier: the claim must still be this closer's before anything is written."""
    if not my_claim(store):
        raise VerificationTaken()


def verification_still_open(store, verdict):
    """A wait ends, touching nothing, when another call closed the verification it started on."""
    if read(store, 'verifying.verdict') != verdict:
        raise VerificationTaken()


def set_member(doc, role, key, value):
    """`.role.key = value` over a document: the role created when it is null, damage when it is not an object."""
    member(doc, role)[key] = value


def close_verifier(ctx, store, name, kind, pane, verdict):
    """Stops the open verification's verifier, closes its pane and removes its scratch, unless `.verifying` no longer names the verification with
    verdict or another call claimed it. Returns 70 then, else 0; a failure is raised."""
    if not claim_close(store, verdict):
        return 70
    scratch = read(store, 'verifying.scratch')
    if kind == 'prime-agent':
        if not stop_prime_role(store, 'verifier'):
            raise PrimeFailed(
                'Prime verifier cleanup failed; its pane and scratch stay for inspection'
            )
    else:
        refused, _ = end_agent(
            name, kind, int(time.time()) + env_int('KITCHEN_VERIFIER_EXIT_S', 15)
        )
        if refused:
            sys.stderr.write(f'verifier exit refused: {refused}\n')
    herdr('pane', 'close', pane)
    if kind == 'prime-agent':
        update(store, lambda doc: set_member(doc, 'verifier', 'closed', True))
    if scratch:
        remove_scratch(ctx, store, scratch)
    return 0


def run_close(ctx, store, name, kind, pane, verdict, err=None):
    """Runs close_verifier so that a failure inside it (the Prime cleanup, the scratch removal) cannot leave this process's claim behind: a failure
    the command ends with is said to `err` (default: standard error) and ends with its status, a bug propagates, and either way the claim this
    process holds is given back. close_verifier's own 70 is "not ours to close" (False, closed elsewhere); any other nonzero status ends the command."""
    closed = False
    try:
        try:
            rc = close_verifier(ctx, store, name, kind, pane, verdict)
        except Exception as e:
            text, rc = translate(e, ctx.prog)
            (err or sys.stderr.write)(text)
        if rc in (0, 70):
            closed = True
            return rc == 0
        raise ToolFailed(rc)
    finally:
        if not closed and my_claim(store):
            release_close(store, verdict, read(store, 'verifying.closing.token'))


def set_outcome(packet, value):
    """The packet's host-written outcome for this attempt: prepared, running, then one of clean, reject, inconclusive, invalid, provider, missing,
    stalled, cancelled, start-failed. `perl -i -pe 's/^verifier-outcome:.*/verifier-outcome: VALUE/'`, every such line."""
    if not os.path.isfile(packet):
        return
    text = read_text(packet)
    text = re.sub(
        r'(?m)^verifier-outcome:.*', lambda m: f'verifier-outcome: {value}', text
    )
    write_text(packet, text)


def verify_prompt(ctx, packet):
    return f'Load the {ctx.skill} skill from ~/.agents/skills/{ctx.skill}/SKILL.md and take the verifier role. {ctx.skill} VERIFY {packet}'


def redeliver_packet(ctx, store, why, name='', packet='', brief='', kind='', say=None):
    """Sends the open verification's packet once more. The name, packet, brief and kind are the verification the caller captured (a wait does);
    an empty one is read from the store."""
    name = name or read(store, 'verifier.name')
    packet = packet or read(store, 'verifying.packet')
    brief = brief or read(store, 'verifying.brief')
    kind = kind or read(store, 'verifier.kind')
    event(store, 'master', 'verify-redeliver', brief, kind)
    (say or sys.stdout.write)(f'redelivered: {why}; sent it again\n')
    sys.stdout.flush()
    agent_prompt(
        name, verify_prompt(ctx, packet), wait=True, until='working', timeout_ms=30000
    )
    submit_typed(name, os.path.basename(packet))


def audit_due(unit, sample):
    """A stable draw per unit: the crc32 of the unit id, modulo 1000, below sample * 1000. The draw changed once from POSIX cksum to crc32 (plan 043 g)."""
    return (zlib.crc32(unit.encode('utf-8')) % 1000) < sample * 1000


def verdict_problem(verdict):
    """Empty when a verdict is well formed, else what is wrong."""
    from .units import Verdict

    return Verdict.parse(verdict).problem


def handoff_due(store, say=None):
    """Every clean verdict, a landing verdict included, counts toward the master's next handoff; once the count reaches KITCHEN_MASTER_ROTATE_UNITS
    (default 5) each clean verdict says to rotate, until rotate-master resets the count. The line goes to `say` (default: standard output)."""
    every = max(env_int('KITCHEN_MASTER_ROTATE_UNITS', 5), 1)

    def bump(doc):
        master = member(doc, 'master')
        master['units_since_handoff'] = count(master, 'units_since_handoff') + 1

    update(store, bump)
    n = read_int(store, 'master.units_since_handoff')
    if n < every:
        return
    (say or sys.stdout.write)(
        f'master: {n} units verified since your last handoff; rotate at this boundary: kitchen.sh new-handoff {store}, fill it, '
        f'kitchen.sh rotate-master {store}\n'
    )


def verify_cancel(ctx, store):
    """Cancels the open verification that has no verdict yet: its verifier and pane stop, its scratch goes, and `.verifying` is cleared."""
    verdict = read(store, 'verifying.verdict')
    if not verdict:
        raise NoVerification('no verification open')
    if os.path.isfile(verdict) and header_field(verdict, 'status'):
        raise NoVerification(
            f'the verdict is written ({verdict}); kitchen.sh verify {store} --wait reads it'
        )
    units = read(store, 'verifying.units')
    brief = read(store, 'verifying.brief')
    packet = read(store, 'verifying.packet')
    name = read(store, 'verifier.name')
    kind = read(store, 'verifier.kind')
    if not run_close(ctx, store, name, kind, read(store, 'verifier.pane_id'), verdict):
        raise NoVerification('no verification open (another call closed it)')
    try:
        still_my_claim(store)
    except VerificationTaken:
        sys.stdout.write(CLOSED_ELSEWHERE)
        raise
    set_outcome(packet, 'cancelled')
    drop_verifying(store, verdict)
    event(store, 'master', 'verify-cancel', brief, units)
    sys.stdout.write(f'cancelled: {units}\n')
    return 0
