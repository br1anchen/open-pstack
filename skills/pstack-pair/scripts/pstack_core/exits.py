"""Ending an agent: the command that makes each kind exit, and the cleanup of a Prime role's daemon session and pane (stop_prime_role).
The deadlines are the bash's 15 s and 1 s; PAIR_EXIT_DEADLINE_S and PAIR_EXIT_POLL_S let a test shorten them."""
import sys
import time

from .store import Exit, env_float, env_int, member, update
from .panes import agent_status, dig, herdr, parse
from .prime import prime_active_session, prime_command, prime_sessions
from .providers import record_prime_session


def exit_command(kind):
    return '/quit' if kind in ('pi', 'prime-agent', 'codex', 'muse') else '/exit'


def exit_deadline():
    """The epoch second an exit may last until: now plus 15 s (PAIR_EXIT_DEADLINE_S)."""
    return int(time.time()) + env_int('PAIR_EXIT_DEADLINE_S', 15)


def exit_poll():
    """The pause between checks that an agent has gone: 1 s (PAIR_EXIT_POLL_S)."""
    time.sleep(env_float('PAIR_EXIT_POLL_S', 1))


def shell_owns_foreground(pane):
    """True when the pane's agent is gone and its shell owns the foreground process group; None when herdr cannot say."""
    code, info = herdr('pane', 'process-info', '--pane', pane)
    if code != 0:
        return None
    if agent_status(pane) != 'absent':
        return False
    process = dig(parse(info), 'result', 'process_info')
    shell = process.get('shell_pid') if isinstance(process, dict) else None
    return isinstance(shell, (int, float)) and not isinstance(shell, bool) and process.get('foreground_process_group_id') == shell


def stop_prime_role(store, role):
    """End a Prime role's daemon session and wait for its client to leave the pane: True when the session is stopped and recorded, False (the
    reason on stderr) when the role cannot be cleaned up and its pane must stay for inspection."""
    from .store import field
    name = field(store, role, 'name')
    status = agent_status(name)
    if status != 'absent' and not record_prime_session(store, role):
        return False
    active = prime_active_session(store, role)
    if active is None:
        return False
    if status != 'absent' and not active:
        sys.stderr.write(f'no live daemon session matches {name} in its recorded socket; inspect the client before cleanup\n')
        return False
    # Detach while the client is still an active Herdr target. A blocked client cannot take /quit through agent.prompt; Ctrl+D exits its
    # empty editor. Stopping the worker alone leaves the TUI displaying a session-closed error.
    if status in ('idle', 'done'):
        herdr('agent', 'prompt', name, '/quit')
    elif status == 'blocked':
        herdr('agent', 'send-keys', name, 'ctrl+d')
    if active:
        if prime_command(store, role, 'stop', active, '--json')[0] != 0:
            return False
        failed, sessions = prime_sessions(store, role)
        if failed or not all(each is None or isinstance(each, dict) for each in sessions):
            return False
        if any(each is not None and each.get("activeSessionId") == active for each in sessions):
            sys.stderr.write(f'Prime session {active} is still active; its pane and scratch stay\n')
            return False
    # The reporter releases on stop. Wait for the actual shell as well: killing a worker does not by itself prove the TUI has exited or
    # released its PTY.
    pane = field(store, role, 'pane_id')
    deadline = exit_deadline()
    while True:
        owns = shell_owns_foreground(pane)
        if owns is None:
            return False
        if owns:
            break
        if int(time.time()) >= deadline:
            sys.stderr.write(f'Prime client has not exited in {pane}; inspect it before cleanup\n')
            return False
        exit_poll()

    def mutate(doc):
        member(doc, role)['session_stopped'] = True
    update(store, mutate)
    return True


def stopped(store, role):
    """stop_prime_role as a caller tests it with `||`: a failed jq write is the answer "no", its message printed; a die ends the command."""
    try:
        return stop_prime_role(store, role)
    except Exit as e:
        sys.stderr.write(e.stderr)
        return False
