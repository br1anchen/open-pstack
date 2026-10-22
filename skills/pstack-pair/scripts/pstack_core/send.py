"""Sending a message to the sidekick and, for a brief or a plan, waiting for the reply: the helpers behind dispatch and discuss, and
send_and_wait itself. The commands built on them are in commands/."""

import glob
import os
import re
import signal
import subprocess
import sys
import time

from .store import Die, Exit, TEXT, missing_pair, env_float, event, expected_report, load, peek, text_at, update, count, member, names, prog, who
from .gates import check_scope, require_not_paused
from .panes import DEVNULL, agent_kind, agent_prompt, agent_status, error_code, require_herdr
from .replies import first3, pending, queued_brief
from .review import require_filled
from .wait import owned_wait, end_with_parent, wait_opts


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
    sidekick = peek(load(store), 'sidekick')
    kind = text_at(sidekick, 'kind')
    if sidekick.get('rotation_required') is True or sidekick.get('bootstrap_pending') is True:
        raise Die(f'the {kind} sidekick needs a fresh session or a successful bootstrap before another task; inspect READY and run pair.sh rotate {store} '
                  'after a completed task. The queue is preserved.', 5)


def visible_without_blanks(name):
    """The last 12 visible lines of the pane with its newlines, spaces and box bars removed."""
    r = subprocess.run(['herdr', 'agent', 'read', name, '--source', 'visible', '--lines', '12'], capture_output=True, check=False, **TEXT)
    return re.sub('[\n │]', '', r.stdout)


def submit_typed(name, marker):
    """Herdr can report a fresh agent working while a prompt still sits typed in its input, so the pane is read too: while its last lines
    still show the message's marker, Enter submits it, up to three times. A working Devin is left alone, since there Enter cancels the
    running command. Returns the number of Enters sent."""
    submitted = 0
    kind = agent_kind(name)
    tries = 0
    while tries < 3:
        time.sleep(env_float('PAIR_SUBMIT_CHECK_S', 5))
        if marker not in visible_without_blanks(name):
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
    r = agent_prompt(name, text, wait=True, until='working', timeout_ms=30000, stdout=DEVNULL)
    if r.returncode != 0:
        err = r.stderr
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
        missing = missing_pair(store)
        if kind == 'BRIEF':
            sys.stderr.write(missing)
            check_scope(file, '')
        raise Exit(2, missing)
    pair = load(store)
    if kind == 'BRIEF':
        check_scope(file, text_at(pair, 'git_root') or text_at(pair, 'cwd'))
    require_fresh_session(store)
    name = text_at(peek(pair, 'sidekick'), 'name')
    status = agent_status(name)
    if status not in ('idle', 'done'):
        if status == 'absent':
            raise Die(f'{who("sidekick", name)} is not live; run: pair.sh spawn {store} --kind <kind>', 2)
        if status == 'blocked':
            raise Die(f'{who("sidekick", name)} is blocked; inspect: herdr agent read {name} --source visible --lines 60', 3)
        raise Die(f'{who("sidekick", name)} is {status}; run: pair.sh wait {store}', 5)
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
        # TERM and HUP go back to what the process inherited (an ignored one stays ignored), but INT goes to its default even if it was inherited
        # as ignored.
        for number, previous in held.items():
            signal.signal(number, signal.SIG_DFL if number == signal.SIGINT else previous)
    if send_only:
        print(f'sent: {kind} {file}; follow it with the wait command')
        return 0
    return owned_wait(ctx, store, name, timeout, interval, now)


def fresh_kind(kind):
    """Sidekick kinds whose sessions cannot be resumed, so spawn refuses a resume flag for them."""
    return kind in ('devin', 'pi', 'prime-agent')


def record_dispatch(store, brief, now=None):
    """The dispatch record: .dispatch (with .sidekick.generation), .sent (with .sent.seen when a report already exists), the unit's base head, and the pending list."""
    old = expected_report(store, brief)
    seen = f'{old}:{int(os.stat(old).st_mtime)}' if os.path.isfile(old) else ''
    pair = load(store)
    cwd = text_at(pair, 'cwd')
    head = subprocess.run(['git', '-C', cwd, 'rev-parse', 'HEAD'], capture_output=True, check=False, **TEXT).stdout.rstrip('\n') if cwd else ''
    stamp = time.time() if now is None else now
    at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stamp))
    epoch = int(stamp)

    def mutate(doc):
        generation = count(peek(doc, 'sidekick'), 'generation')
        doc['dispatch'] = {'brief': brief, 'at': at, 'head': head, 'generation': generation}
        doc['sent'] = {'file': brief, 'kind': 'BRIEF', 'at': at, 'epoch': epoch}
        if seen:
            doc['sent']['seen'] = seen
        heads = member(doc, 'heads')
        key = brief.split('/')[-1]
        key = key[:-3] if key.endswith('.md') else key
        if heads.get(key) is None:
            heads[key] = head
        doc['pending'] = sorted({*names(doc, 'pending'), *([] if seen else [brief])})

    update(store, mutate)


def discuss(ctx, args):
    require_herdr()
    if len(args) < 2:
        raise Die(f'usage: {prog()} discuss <store> <plan-path> [--timeout MS]')
    store, plan = args[0], args[1]
    end_with_parent()
    timeout, interval, send_only, rest = wait_opts(args[2:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    require_not_paused(store)
    from .commands.rotate import rotate_if_due
    rotate_if_due(ctx, store, 'plan')
    return send_and_wait(ctx, store, os.path.realpath(plan), 'PLAN', timeout, interval, send_only)


def store_relative(store, path):
    """A brief path as given when it exists, else the same path under the store (briefs/NNN-x.md), else as given."""
    return f'{store}/{path}' if not os.path.isfile(path) and os.path.isfile(f'{store}/{path}') else path


def dispatch(ctx, args):
    require_herdr()
    if len(args) < 2:
        raise Die(f'usage: {prog()} dispatch <store> <brief-path> [--timeout MS | --every MIN]')
    store, brief = args[0], store_relative(args[0], args[1])
    end_with_parent()
    timeout, interval, send_only, rest = wait_opts(args[2:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    brief = os.path.realpath(brief)
    require_not_paused(store)
    ctx.variant.require_agreed_plan(ctx, store, brief)
    queued = queued_brief(store)
    if queued and queued != brief:
        raise Die(f'the queue holds {queued}; dispatch that first, or: pair.sh queue {store} --clear', 5)
    from .commands.rotate import rotate_if_due
    rotate_if_due(ctx, store, 'brief')
    return send_and_wait(ctx, store, brief, 'BRIEF', timeout, interval, send_only)
