"""Sending a message to the sidekick and, for a brief or a plan, waiting for the reply: the helpers behind dispatch and discuss, and
send_and_wait itself. The commands built on them are in commands/."""
import glob
import os
import signal
import subprocess
import sys
import time

from .dispatch import fresh_per_brief, record_dispatch
from .errors import Die, Exit
from .gates import check_scope
from .herdr import agent_kind, agent_status
from .replies import first3, pending, queued_brief
from .review import jq_raw, require_filled
from .store import alt, env_float, event, expected_report, get, load, update
from .commands.report import MISSING_PAIR
from .wait import error_code, owned_wait


def section(pair, name):
    value = pair.get(name)
    return value if isinstance(value, dict) else {}


def prune_pending(store):
    """Before the master dispatches, the sidekick is idle and every earlier brief has ended: drop those whose report or review exists,
    since the master read them by some path, and only a brief still owed a report stays pending."""
    keep = []
    for brief in pending(store):
        if os.path.isfile(expected_report(store, brief)):
            continue
        if glob.glob(os.path.join(glob.escape(store), 'reviews', glob.escape(first3(brief)) + '-*.md')):
            continue
        keep.append(brief)
    update(store, lambda doc: doc.__setitem__('pending', keep))


def record_sent(store, file, kind, now=None):
    """The message the sidekick is answering: its report is the one a wait looks for, even when a newer brief already sits in the queue."""
    stamp = time.time() if now is None else now
    at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stamp))
    update(store, lambda doc: doc.__setitem__('sent', {'file': file, 'kind': kind, 'at': at, 'epoch': int(stamp)}))


def require_fresh_session(store):
    if not isinstance(get(store, 'sidekick'), (dict, type(None))):
        # jq's message and its status end the command
        from .store import field
        field(store, 'sidekick', 'kind')
    sidekick = section(load(store), 'sidekick')
    kind = jq_raw(sidekick.get('kind'))
    if fresh_per_brief(kind) and (alt(sidekick.get('rotation_required'), False) is not False or alt(sidekick.get('bootstrap_pending'), False) is not False):
        raise Die(f'the {kind} sidekick needs a fresh session or a successful bootstrap before another task; inspect READY and run pair.sh rotate {store} '
                  'after a completed task. The queue is preserved.', 5)


def visible_without_blanks(name):
    """herdr agent read ... | tr -d '\\n │': tr works on bytes, so the three bytes of the bar are deleted one by one."""
    r = subprocess.run(['herdr', 'agent', 'read', name, '--source', 'visible', '--lines', '12'], capture_output=True, check=False)
    return r.stdout.translate(None, delete=b'\n \xe2\x94\x82')


def submit_typed(name, marker):
    """Herdr can report a fresh agent working while a prompt still sits typed in its input, so the pane is read too: while its last lines
    still show the message's marker, Enter submits it, up to three times. A working Devin is left alone, since there Enter cancels the
    running command. Returns the number of Enters sent."""
    submitted = 0
    kind = agent_kind(name)
    tries = 0
    while tries < 3:
        time.sleep(env_float('PAIR_SUBMIT_CHECK_S', 5))
        if marker.encode('utf-8', 'surrogateescape') not in visible_without_blanks(name):
            return submitted
        if kind == 'devin' and agent_status(name) == 'working':
            return submitted
        subprocess.run(['herdr', 'agent', 'send-keys', name, 'enter'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        tries += 1
        submitted = tries
    return submitted


def prompt_sidekick(name, text):
    """Sends one message and confirms the sidekick took it. A narrow pane can hide the working state, so a timeout or stall here is not an
    error; the wait that follows looks for the reply file either way."""
    r = subprocess.run(['herdr', 'agent', 'prompt', name, text, '--wait', '--until', 'working', '--timeout', '30000'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
    if r.returncode != 0:
        err = r.stderr.decode('utf-8', 'surrogateescape')
        code = error_code(err)
        if code not in ('timeout', 'agent_prompt_stalled'):
            sys.stderr.write(err)
            raise Die(f'herdr prompt failed for {name}', 2)
    return submit_typed(name, os.path.basename(text.rsplit(' ', 1)[-1].rstrip('/') or text.rsplit(' ', 1)[-1]))


def send_and_wait(ctx, store, file, kind, timeout, interval, send_only, now=None):
    """Shared by discuss, dispatch and answer: refuse unfilled files and a sidekick that cannot take input, then prompt and wait."""
    require_filled(ctx, file)
    if not os.path.isfile(f'{store}/pair.json'):
        # field() runs pair_file in a substitution, so its die only ends that subshell and jq then fails on an empty file name: a pair of
        # error lines each time. A brief reads the repo root first (check_scope then warns about every entry against an empty root), and the
        # fresh-session check reads the kind: the second pair, with status 2. A plan gets only the second.
        text = MISSING_PAIR.format(store=store)
        if kind == 'BRIEF':
            sys.stderr.write(text)
            check_scope(file, '')
        raise Exit(2, text)
    pair = load(store)
    if kind == 'BRIEF':
        check_scope(file, jq_raw(alt(pair.get('git_root'), pair.get('cwd'))))
    require_fresh_session(store)
    name = jq_raw(section(load(store), 'sidekick').get('name'))
    status = agent_status(name)
    if status not in ('idle', 'done'):
        if status == 'absent':
            raise Die(f'sidekick {name} is not live; run: pair.sh spawn {store} --kind <kind>', 2)
        if status == 'blocked':
            raise Die(f'sidekick {name} is blocked; inspect: herdr agent read {name} --source visible --lines 60', 3)
        raise Die(f'sidekick {name} is {status}; run: pair.sh wait {store}', 5)
    # Delivery is not interruptible: a dispatch wrapped in a short timeout was killed after recording a brief and before sending it, and
    # the store waited on a sidekick that never got it. TERM, INT and HUP wait until the message is in; the wait that follows may be cut
    # short safely.
    held = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        if kind == 'BRIEF':
            if queued_brief(store) == file:
                try:
                    os.unlink(f'{store}/queue')
                except OSError:
                    pass
            prune_pending(store)
            record_dispatch(store, file)
        else:
            record_sent(store, file, kind, now)
        event(store, 'master', f'send-{kind.lower()}', file)
        prompt_sidekick(name, f'{ctx.skill} {kind} {file}')
    finally:
        # `trap - TERM INT HUP`, as decided by running the bash: TERM and HUP go back to what the shell inherited (an ignored one stays
        # ignored), but INT goes to its default even if it was inherited as ignored.
        for number, previous in held.items():
            signal.signal(number, signal.SIG_DFL if number == signal.SIGINT else previous)
    if send_only:
        print(f'sent: {kind} {file}; follow it with the wait command')
        return 0
    return owned_wait(ctx, store, name, timeout, interval, now)
