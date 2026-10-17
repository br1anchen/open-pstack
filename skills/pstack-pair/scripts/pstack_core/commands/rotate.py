import os
import re
import subprocess
import sys
import time

from ..dispatch import fresh_per_brief
from ..errors import Die
from ..exits import exit_command, exit_deadline, exit_poll, stopped
from ..gates import require_not_paused
from ..herdr import agent_status
from ..spawn import field, mapfile_args, section, spawn_role
from ..review import jq_raw
from ..store import alt, event, pair_file, pair_json
from ..wait import die_with_the_shell
from .report import in_herdr

def inside_substitution():
    """True when this process is not the shell the command was started from: `pycore --exec` replaces that shell, so a rotate run as `$(cmd_rotate ...)`
    (kitchen.sh rotate_if_due) is a subshell, where bash clears errexit, and a direct `pair.sh rotate` keeps the shell's pid."""
    return os.getpid() != int(os.environ.get('PAIR_SHELL_PID') or os.getpid())


TOLERATED = re.compile('agent_prompt_stalled|agent_not_found|not_found')


def rotate(ctx, args):
    """Exits the idle sidekick and reuses its pane with the same native arguments. It preserves the store and never deletes a Devin session."""
    die_with_the_shell()
    in_herdr()
    if len(args) != 1:
        raise Die('usage: pair.sh rotate <store>')
    store = args[0]
    pair_file(store)
    require_not_paused(store)
    errexit_off = inside_substitution()
    kind = field(store, 'sidekick', 'kind', check=not errexit_off)
    if not fresh_per_brief(kind):
        raise Die(f'rotate is for a sidekick that starts fresh per brief (devin, pi, prime-agent), not {kind}', 5)
    sidekick = section(pair_json(store), 'sidekick')
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
    start_args = mapfile_args(section(pair_json(store), 'sidekick').get('start_args'))
    event(store, 'master', 'rotate', '-', f'generation:{field(store, "sidekick", "generation")}')
    return spawn_role(ctx, store, 'sidekick', kind, 'none', field(store, 'master', 'pane_id'), '60000', '', field(store, 'sidekick', 'pane_id'), start_args,
                      errexit_off=errexit_off)
