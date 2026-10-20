"""The open verification's state and its close: who may write `.verifying`, who owns the close, and what closing does (verifier, pane, scratch).
Ported from kitchen.sh at b632713 (drop_verifying, update_verifying, claim_close, release_close, my_claim, still_my_claim, run_close, close_verifier,
verification_still_open, verify_cancel and the helpers they share with the wait: set_outcome, verify_prompt, redeliver_packet, audit_due,
verdict_problem and handoff_due).

This module alone writes `.verifying` and `.verifying.closing`. A parsed snapshot of `.verifying` is never authority: each write goes through
`update_verifying`, which re-reads pair.json under the lock and acts only while `.verifying.verdict` is still the verdict the caller holds (the
compare-and-set), and the claim of a close is the token `<host>:<pid>:<n>` (host from `$HOSTNAME`, as the bash reads it; pid the shell's, which
pycore passes as PAIR_SHELL_PID; n a random number below 32768) written by the first closer only."""

import contextlib
import io
import os
import subprocess
import random
import re
import sys

from pstack_core.errors import Die, Exit
from pstack_core.exits import exit_command, stop_prime_role
from pstack_core.herdr import agent_status, herdr
from pstack_core.store import bash_int, event, header_field, update, update_or_jq
from pstack_core.send import submit_typed
from pstack_core.wait import bash_sleep

from .jqx import pair_jq


def hostname():
    return os.environ.get('HOSTNAME') or 'localhost'


def shell_pid():
    return int(os.environ.get('PAIR_SHELL_PID') or os.getpid())


def field(store, program, check=False):
    """`field "$store" PROGRAM`: jq's answer, with its error on stderr; with check the plain assignment's end of the command."""
    return pair_jq(store, program, check=check)


@contextlib.contextmanager
def quiet_stderr():
    """File descriptor 2 pointed at /dev/null, for what the bash ran under `2>/dev/null` (jq's own error included)."""
    sys.stderr.flush()
    saved = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(null)


def names_verdict(doc, verdict):
    """jq's `.verifying.verdict == $v`: false for no verification, an error (TypeError, so jq decides) for a `.verifying` that is not an object."""
    verifying = doc.get('verifying')
    if verifying is None:
        return False
    if isinstance(verifying, dict):
        return verifying.get('verdict') == verdict
    raise TypeError('.verifying is not an object')


def update_verifying(store, verdict, body, apply, *jq_args, errexit=True):
    """json_update with `if .verifying.verdict == $v then (BODY) else . end`: BODY is applied (by `apply(verifying)` natively, by jq where the
    document is one jq reads differently) only while the store still names the verification with verdict. A write that lands after another call
    closed it changes nothing. errexit False is a caller where the bash ran with errexit off: a refused write does not end the command."""
    def mutate(doc):
        if names_verdict(doc, verdict):
            apply(doc['verifying'])

    try:
        update_or_jq(store, mutate, f'if .verifying.verdict == $v then ({body}) else . end', '--arg', 'v', verdict, *jq_args)
    except Exit:
        if errexit:
            raise


def drop_verifying(store, verdict, errexit=True):
    """Clears `.verifying` only while it still names the verification with verdict: another call may have closed it and started the next."""
    def mutate(doc):
        if names_verdict(doc, verdict):
            del doc['verifying']

    try:
        update_or_jq(store, mutate, 'if .verifying.verdict == $v then del(.verifying) else . end', '--arg', 'v', verdict)
    except Exit:
        if errexit:
            raise


def alive(pid):
    """`kill -0 $pid 2>/dev/null`: a process this user may signal. A process of another user (EPERM) and a pid that is no number read as gone."""
    number = bash_int(pid)
    if number is None:
        return False
    try:
        os.kill(number, 0)
    except (OverflowError, OSError):
        return False
    return True


def claim_close(store, verdict):
    """Claims the close of the verification with verdict: one atomic update of pair.json that succeeds for the first caller only. The claim names
    this host and process; a claim whose process is gone on this host is taken over, one from another host is never touched, a live one is
    respected. False when the verification is gone or another call holds the claim."""
    here = hostname()
    token = f'{here}:{shell_pid()}:{random.randrange(32768)}'
    current = field(store, '.verifying.closing.token // empty')
    if current:
        host = current.partition(':')[0]
        rest = current.partition(':')[2] if ':' in current else current
        pid = rest.partition(':')[0]
        if not (host == here and not alive(pid)):
            return False

    def apply(verifying):
        if (alt_token(verifying.get('closing')) or '') == current:
            verifying['closing'] = {'token': token}

    update_verifying(store, verdict, 'if (.verifying.closing.token // "") == $old then .verifying.closing = {token: $t} else . end', apply,
                     '--arg', 't', token, '--arg', 'old', current, errexit=False)
    return field(store, '.verifying.closing.token // empty') == token


def alt_token(closing):
    """jq's `.closing.token // empty` over a value the document holds: a string token, or None."""
    if isinstance(closing, dict):
        token = closing.get('token')
        return token if token not in (None, False) else None
    if closing is None:
        return None
    raise TypeError('.verifying.closing is not an object')


def release_close(store, verdict, token):
    """Gives up the claim token on the verification with verdict, when it is still the holder's."""
    def apply(verifying):
        closing = verifying.get('closing')
        if isinstance(closing, dict):
            if closing.get('token') == token:
                del verifying['closing']
        elif closing is not None:
            raise TypeError('.verifying.closing is not an object')

    with quiet_stderr():
        update_verifying(store, verdict, 'if .verifying.closing.token == $t then del(.verifying.closing) else . end', apply, '--arg', 't', token,
                         errexit=False)


def my_claim(store):
    """Whether the claim in the store is this process's (the token names this host and pid)."""
    return field(store, '.verifying.closing.token // empty').startswith(f'{hostname()}:{shell_pid()}:')


def still_my_claim(store):
    """After close_verifier: the claim must still be this closer's before anything is written."""
    if my_claim(store):
        return
    sys.stdout.write('verification closed elsewhere\n')
    raise Exit(5)


def verification_still_open(store, verdict):
    """A wait ends, touching nothing, when another call closed the verification it started on."""
    if field(store, '.verifying.verdict // empty') == verdict:
        return
    sys.stdout.write('verification closed elsewhere\n')
    raise Exit(5)


def set_member(doc, role, key, value):
    """jq's `.role.key = value` over a document: the role created when it is null, an error (TypeError, so jq decides) when it is not an object."""
    section = doc.get(role)
    if section is None:
        doc[role] = {key: value}
    elif isinstance(section, dict):
        section[key] = value
    else:
        raise TypeError(f'.{role} is not an object')


def close_verifier(ctx, store, name, kind, pane, verdict):
    """Stops the open verification's verifier, closes its pane and removes its scratch, unless `.verifying` no longer names the verification with
    verdict or another call claimed it. Returns 70 then, else the status of the scratch removal (the last command, as in the bash)."""
    if not claim_close(store, verdict):
        return 70
    scratch = field(store, '.verifying.scratch')
    if kind == 'prime-agent':
        if not stop_prime_role(store, 'verifier'):
            raise Die('Prime verifier cleanup failed; its pane and scratch stay for inspection', 5)
    else:
        herdr('agent', 'prompt', name, exit_command(kind))
        import time
        gone = int(time.time()) + shell_arithmetic(os.environ.get('KITCHEN_VERIFIER_EXIT_S') or '15')
        while agent_status(name) != 'absent' and int(time.time()) < gone:
            bash_sleep('1')
    herdr('pane', 'close', pane)
    if kind == 'prime-agent':
        update_or_jq(store, lambda doc: set_member(doc, 'verifier', 'closed', True), '.verifier.closed = true')
    return remove_scratch(ctx, store, scratch)


def pycore_argv(ctx, *command):
    """The words of the bash's pycore: the core run as a process, with the context of this command."""
    from pstack_core import cli
    pair_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__))), 'pair.py')
    return [sys.executable, '-I', pair_py, '--skill', ctx.skill, '--prog', cli.PROG, '--skill-root', ctx.skill_root, '--state-root', ctx.state_root,
            '--store-label', ctx.store_label, '--roles', ' '.join(ctx.roles), '--store-dirs', ' '.join(ctx.store_dirs), '--slug-max', str(ctx.slug_max),
            '--unfilled-re', ctx.unfilled_re, '--', *command]


def scratch_process(ctx, store, scratch, *options, capture=False):
    """`cmd_scratch "$store" "$scratch" OPTIONS` as the bash ran it, a pycore process: (status, stdout when captured). Its errors, a traceback
    included, are its own stderr."""
    sys.stdout.flush()
    sys.stderr.flush()
    env = {**os.environ, 'PAIR_SHELL_PID': str(shell_pid())}
    r = subprocess.run(pycore_argv(ctx, 'scratch', store, scratch, *options), env=env, stdout=subprocess.PIPE if capture else subprocess.DEVNULL, check=False)
    return r.returncode, r.stdout.decode('utf-8', 'surrogateescape') if capture else ''


def remove_scratch(ctx, store, scratch):
    """`cmd_scratch "$store" "$scratch" --remove >/dev/null`: its status."""
    return scratch_process(ctx, store, scratch, '--remove')[0]


def run_close(ctx, store, name, kind, pane, verdict):
    """Runs close_verifier so that a die inside it (the Prime cleanup failing) cannot leave this process's claim behind: the claim is released and
    the same status returned. close_verifier's own 70 is "not ours to close" (False, closed elsewhere); any other nonzero status ends the command."""
    try:
        rc = close_verifier(ctx, store, name, kind, pane, verdict)
    except Die as e:
        sys.stderr.write(f'pair.sh: {e.message}\n')
        rc = e.code
    except Exit as e:
        rc = e.code
    except Exception as e:  # any failure of the close is a nonzero status of its subshell
        sys.stderr.write(f'pair.sh: {e}\n')
        rc = 1
    if rc == 0:
        return True
    if rc == 70:
        return False
    if my_claim(store):
        release_close(store, verdict, field(store, '.verifying.closing.token // empty'))
    raise Exit(rc)


def set_outcome(packet, value):
    """The packet's host-written outcome for this attempt: prepared, running, then one of clean, reject, inconclusive, invalid, provider, missing,
    stalled, cancelled, start-failed. `perl -i -pe 's/^verifier-outcome:.*/verifier-outcome: VALUE/'`, every such line."""
    if not os.path.isfile(packet):
        return
    with open(packet, 'rb') as f:
        text = f.read().decode('utf-8', 'surrogateescape')
    text = re.sub(r'(?m)^verifier-outcome:.*', lambda m: f'verifier-outcome: {value}', text)
    with open(packet, 'wb') as f:
        f.write(text.encode('utf-8', 'surrogateescape'))


def verify_prompt(ctx, packet):
    return f'Load the {ctx.skill} skill from ~/.agents/skills/{ctx.skill}/SKILL.md and take the verifier role. {ctx.skill} VERIFY {packet}'


def redeliver_packet(ctx, store, why, name='', packet='', brief='', kind=''):
    """Sends the open verification's packet once more. The name, packet, brief and kind are the verification the caller captured (a wait does);
    an empty one is read from the store."""
    name = name or field(store, '.verifier.name')
    packet = packet or field(store, '.verifying.packet')
    brief = brief or field(store, '.verifying.brief')
    kind = kind or field(store, '.verifier.kind')
    event(store, 'master', 'verify-redeliver', brief, kind)
    sys.stdout.write(f'redelivered: {why}; sent it again\n')
    sys.stdout.flush()
    herdr('agent', 'prompt', name, verify_prompt(ctx, packet), '--wait', '--until', 'working', '--timeout', '30000')
    submit_typed(name, os.path.basename(packet))


def crc_table():
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        table.append(c)
    return table


CRC = crc_table()


def cksum(data):
    """POSIX cksum of the bytes: the CRC-32 (polynomial 0x04C11DB7, most significant bit first) over the data and then its length, complemented."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC[((crc >> 24) ^ byte) & 0xFF]
    n = len(data)
    while n:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC[((crc >> 24) ^ (n & 0xFF)) & 0xFF]
        n >>= 8
    return ~crc & 0xFFFFFFFF


def awk_number(text):
    """awk's number for a string: its longest numeric prefix, else 0."""
    m = re.match(r'[ \t\n]*([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)', text)
    return float(m.group(1)) if m else 0.0


def audit_due(unit, sample):
    """A stable draw per unit: the cksum of the unit id, modulo 1000, below sample * 1000 (awk's arithmetic)."""
    return (cksum(unit.encode('utf-8', 'surrogateescape')) % 1000) < awk_number(sample) * 1000


def verdict_problem(verdict):
    """Empty when a verdict is well formed, else what is wrong."""
    from .units import Verdict
    return Verdict.parse(verdict).problem


def shell_arithmetic(text):
    """`$(( ... ))` over a word the user or the store gave: a whole number as bash reads it (0x is hexadecimal, a leading 0 octal), or an empty
    word (0). Anything else ends the shell, status 1, with the shell's complaint, which names its file and line in the bash and here only the word: a
    name is an unset variable under `set -u`, a digit string that is not octal is a value too great for its base, the rest a syntax error."""
    word = text.strip(' \t\n')
    if word == '':
        return 0
    m = re.fullmatch(r'([+-]?)(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)', word)
    if m:
        digits = m.group(2)
        value = int(digits, 16) if digits[:2] in ('0x', '0X') else int(digits, 8) if digits.startswith('0') else int(digits)
        return -value if m.group(1) == '-' else value
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', word):
        raise Die(f'{word}: unbound variable', 1)
    if re.fullmatch(r'[+-]?0[0-9]+', word):
        raise Die(f'{word}: value too great for base (error token is "{word}")', 1)
    raise Die(f'{text}: arithmetic syntax error', 1)


def handoff_due(store, unit, errexit=True):
    """Every clean unit verdict counts toward the master's next handoff; after KITCHEN_MASTER_ROTATE_UNITS (default 5) the verdict says to rotate, at
    the threshold and at each multiple of it."""
    if unit == 'landing':
        return
    def count(doc):
        master = doc.get('master')
        seen = 0 if master is None else master.get('units_since_handoff')
        seen = 0 if seen in (None, False) else seen
        if isinstance(seen, bool) or not isinstance(seen, int):
            raise TypeError('units_since_handoff is not an integer')
        set_member(doc, 'master', 'units_since_handoff', seen + 1)

    try:
        update_or_jq(store, count, '.master.units_since_handoff = ((.master.units_since_handoff // 0) + 1)')
    except Exit:
        if errexit:
            raise
    n = field(store, '.master.units_since_handoff')
    thr = bash_int(os.environ.get('KITCHEN_MASTER_ROTATE_UNITS') or '5')
    thr = 1 if thr is None or thr < 1 else thr
    if shell_arithmetic(n) % thr != 0:
        return
    sys.stdout.write(f'master: {n} units verified since your last handoff; rotate at this boundary: kitchen.sh new-handoff {store}, fill it, '
                     f'kitchen.sh rotate-master {store}\n')


def verify_cancel(ctx, store):
    """Cancels the open verification that has no verdict yet: its verifier and pane stop, its scratch goes, and `.verifying` is cleared."""
    verdict = field(store, '.verifying.verdict // empty', check=True)
    if not verdict:
        raise Die('no verification open', 5)
    if os.path.isfile(verdict) and header_field(verdict, 'status'):
        raise Die(f'the verdict is written ({verdict}); kitchen.sh verify {store} --wait reads it', 5)
    units = field(store, '.verifying.units', check=True)
    brief = field(store, '.verifying.brief', check=True)
    packet = field(store, '.verifying.packet', check=True)
    name = field(store, '.verifier.name', check=True)
    kind = field(store, '.verifier.kind', check=True)
    if not run_close(ctx, store, name, kind, field(store, '.verifier.pane_id', check=True), verdict):
        raise Die('no verification open (another call closed it)', 5)
    still_my_claim(store)
    set_outcome(packet, 'cancelled')
    drop_verifying(store, verdict)
    event(store, 'master', 'verify-cancel', brief, units)
    sys.stdout.write(f'cancelled: {units}\n')
    return 0


def verify_cancel_command(ctx, args):
    """pycore __verify_cancel <store>: the bash cmd_verify's `verify_cancel "$store"`, which always ended the command."""
    return verify_cancel(ctx, args[0])
