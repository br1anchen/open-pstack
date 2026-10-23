import glob
import os
import re
import sys
import time

from ..store import read_text, event, field, header_field, update, who
from ..numbers import env_float, env_int, tdiv
from ..errors import Die, FileFailed, PairJsonGone
from ..panes import DEVNULL, agent_prompt, agent_status, herdr, pane_text, require_herdr
from ..replies import file_mtime
from ..argv import read
from ..wait import (
    TIMING,
    WaitResult,
    end_with_parent,
    finish_wait,
    print_outcome,
    timeout_ms,
)
from .report import require_pair

DEFAULT_TIMEOUT = 300000


def first_line(path):
    """head -1 of a file, as a command substitution leaves it."""
    return read_text(path).split('\n', 1)[0]


def stop_candidates(store, role):
    """The stop-like reports (or advice) of the role on disk, as "path:mtime" rows."""
    directory = 'advice' if role == 'consultant' else 'reports'
    rows = []
    for f in sorted(
        glob.glob(os.path.join(glob.escape(store), directory, '[0-9][0-9][0-9]-*.md'))
    ):
        if not os.path.isfile(f):
            continue
        if not re.fullmatch(r'[0-9]{3}-stop\.md', os.path.basename(f)):
            if 'STOP' not in first_line(f) and not re.search(
                'stop', header_field(f, 'status'), re.IGNORECASE
            ):
                continue
        rows.append(f'{f}:{file_mtime(f)}')
    return rows


def stop_written(store, role, before):
    """True when the role wrote a stop report or advice since the snapshot `before` that stop_candidates took before the send. The role docs
    name it NNN-stop.md, but Devin also writes NNN-<slug>-stop.md or NNN-<slug>-partial1.md, so any report counts when its heading says STOP in
    capitals or its status mentions a stop; a slug that merely contains "stop" does not. Comparing against the snapshot, not the send time,
    keeps a report from the same second and drops an older one."""
    return any(row not in before for row in stop_candidates(store, role))


def send_enter(name):
    herdr('agent', 'send-keys', name, 'enter')


def devin_send_now(store, role, name, text, before, deadline):
    """Delivers a prompt already typed into a working Devin's input. Devin needs two Enters: one submits the text to its queue, the next sends
    the queue now, cancelling the running command. The pane is watched instead of sleeping once: Enter while the text sits in the input box,
    then Enter once it shows in the queue with the "send now" hint, which an older queued message alone does not satisfy. The watch ends early
    once the stop report exists, and after 20 seconds or the caller's deadline, whichever comes first. When the pane never showed the text, one
    Enter goes as a fallback. Returns False with a reason on stderr when delivery could not be confirmed."""
    end = min(int(time.time()) + env_int('PAIR_DEVIN_WATCH_S', 20), deadline)
    seen = False
    while int(time.time()) < end:
        if stop_written(store, role, before):
            return True
        screen = pane_text(name)
        if f'❭ {text}' in screen:
            seen = True
            send_enter(name)
        elif f'○ {text}' in screen and re.search(
            r'send queued messages now|↵ send now', screen, re.IGNORECASE
        ):
            send_enter(name)
            return True
        time.sleep(1)
    if stop_written(store, role, before):
        return True
    if f'❭ {text}' in pane_text(name):
        sys.stderr.write(
            f'{role}: STOP is still typed in the input box; read it: herdr agent read {name} --source visible\n'
        )
        return False
    if not seen:
        send_enter(name)
        sys.stderr.write(
            f'{role}: STOP never showed in the pane; sent one Enter as a fallback\n'
        )
        return False
    return True


def stop_role(ctx, store, role, timeout):
    """STOP for one role: (code, out, err). An idle agent gets it with a wait. A working one may settle its current turn before it reads STOP,
    so the wait ends on its stop report or advice written after the send, not on a settle. A working Devin parks a mid-turn message as queued
    until Enter is pressed, so Enter follows."""
    name = field(store, role, 'name')
    event(store, 'master', 'send-stop', '-', role)
    text = f'{ctx.skill} STOP {store}'
    if agent_status(name) == 'working':
        epoch = int(time.time())
        before = stop_candidates(store, role)
        r = agent_prompt(name, text, stdout=DEVNULL)
        if r.returncode != 0:
            return 2, '', r.stderr.rstrip('\n')
        deadline = epoch + env_int('PAIR_STOP_DEADLINE_S', tdiv(timeout, 1000))
        code, err = 0, ''
        if field(store, role, 'kind') == 'devin':
            devin_send_now(store, role, name, f'{ctx.skill} STOP', before, deadline)
        while not stop_written(store, role, before):
            if int(time.time()) >= deadline:
                code, err = 1, '{"error":{"code":"timeout"}}'
                break
            time.sleep(env_float('PAIR_STOP_POLL_S', 5))
        return code, herdr('agent', 'get', name)[1].rstrip('\n'), err
    r = agent_prompt(name, text, wait=True, timeout_ms=timeout)
    return r.returncode, r.stdout.rstrip('\n'), r.stderr.rstrip('\n')


def clear_queue(store):
    """Remove the store's queue file and empty pair.json's pending list."""
    try:
        os.remove(f'{store}/queue')
    except FileNotFoundError:
        pass
    except OSError as e:
        raise FileFailed('remove', f'{store}/queue', e) from None
    if not os.path.isfile(f'{store}/pair.json'):
        raise PairJsonGone(store)
    update(store, lambda doc: doc.__setitem__('pending', []))


def stop(ctx, args):
    require_herdr()
    end_with_parent()
    if not args:
        raise Die(f'usage: {ctx.prog} stop <store> [--timeout MS]')
    store = args[0]
    opts = read(args[1:], TIMING)
    timeout = timeout_ms(opts, DEFAULT_TIMEOUT)
    opts.refuse_leftovers()
    interval = tdiv(timeout, 60000)
    require_pair(ctx, store)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    if status == 'absent':
        raise Die(f'{who("sidekick", name)} is not live', 2)
    if status == 'blocked':
        raise Die(f'{who("sidekick", name)} is blocked; inspect it before stopping', 3)
    clear_queue(store)
    code, out, err = stop_role(ctx, store, 'sidekick', timeout)
    return print_outcome(
        finish_wait(ctx, store, interval, WaitResult(code, out, err), 'any')
    )
