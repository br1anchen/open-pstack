import contextlib
import io
import os
import re
import subprocess
import sys
import time

from ..errors import Die, Exit
from ..exits import exit_command, exit_deadline, exit_poll, stopped
from ..gates import require_not_paused
from ..herdr import agent_status
from ..spawn import mapfile_args, section, spawn_role
from ..review import jq_raw
from ..store import alt, event, field, load, pair_file
from ..wait import end_with_parent
from .report import in_herdr

TOLERATED = re.compile('agent_prompt_stalled|agent_not_found|not_found')


def rotate(ctx, args):
    """Exits the idle sidekick and reuses its pane with the same native arguments. It preserves the store and never deletes a Devin session."""
    end_with_parent()
    in_herdr()
    if len(args) != 1:
        raise Die('usage: pair.sh rotate <store>')
    store = args[0]
    pair_file(store)
    require_not_paused(store)
    kind = field(store, 'sidekick', 'kind')
    sidekick = section(load(store), 'sidekick')
    if jq_raw(alt(sidekick.get('rotation_required'), False)) != 'true':
        raise Die('rotation requires a completed task; partial or blocked work keeps its session', 5)
    if not isinstance(sidekick.get('start_args'), list):
        raise Die('no recorded startup arguments; exit the idle sidekick and spawn it with its original model and permission flags', 5)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    if status in ('idle', 'done'):
        if kind == 'prime-agent':
            if not stopped(store, 'sidekick'):
                raise Die('Prime session cleanup failed; no new session was started', 5)
        else:
            r = subprocess.run(['herdr', 'agent', 'prompt', name, exit_command(kind)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            err = r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')
            if r.returncode != 0 and not TOLERATED.search(err):
                raise Die(f'{kind} exit failed: {err}', 2)
            deadline = exit_deadline()
            while agent_status(name) != 'absent':
                if int(time.time()) >= deadline:
                    raise Die(f'{kind} has not exited; inspect {name} before retrying rotate', 5)
                exit_poll()
    elif status == 'absent':
        if kind == 'prime-agent' and not stopped(store, 'sidekick'):
            raise Die('Prime session cleanup failed; no new session was started', 5)
    else:
        raise Die(f'sidekick {name} is {status}; wait for its report and turn to end before rotating', 5)
    start_args = mapfile_args(section(load(store), 'sidekick').get('start_args'))
    event(store, 'master', 'rotate', '-', f'generation:{field(store, "sidekick", "generation")}')
    return spawn_role(ctx, store, 'sidekick', kind, 'none', field(store, 'master', 'pane_id'), '60000', '', field(store, 'sidekick', 'pane_id'), start_args)


def rotate_if_due(ctx, store, what):
    """The sidekick is rotated before the next brief or plan once its last unit is done, so the rotation costs the master no extra wake. Partial
    or blocked work never sets rotation_required and keeps its session. A replacement whose READY is still pending is left to its bootstrap:
    send refuses until it is ready."""
    if not os.path.isfile(f'{store}/pair.json') or field(store, 'sidekick', 'rotation_required', default='false') != 'true':
        return
    if field(store, 'sidekick', 'bootstrap_pending', default='false') == 'true':
        return
    # The report lands a moment before the sidekick's turn ends.
    subprocess.run(['herdr', 'agent', 'wait', field(store, 'sidekick', 'name'), '--until', 'idle', '--until', 'done', '--timeout', '60000'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    said = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        try:
            code = rotate(ctx, [store])
        except Exit as e:
            said.write(e.stderr)
            code = e.code
        except Die as e:
            said.write(f'pair.sh: {e.message}\n')
            code = e.code
    if code:
        raise Die(f'rotating the sidekick before this {what} failed:\n{said.getvalue().rstrip(chr(10))}', code)
    sys.stdout.write(f"rotated: the {field(store, 'sidekick', 'kind')} sidekick starts this {what} in a fresh session\n")
