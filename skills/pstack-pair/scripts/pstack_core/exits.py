"""Ending an agent: the command that makes each kind exit, and the cleanup of a Prime role's daemon session and pane (stop_prime_role).
The deadlines are 15 s and 1 s; PAIR_EXIT_DEADLINE_S and PAIR_EXIT_POLL_S let a test shorten them."""

import re
import sys
import time

from . import mux
from .mux import MERGED, NOT_READY
from .numbers import env_float, env_int
from .panes import agent_status, pane_text
from .prime import prime_active_session, prime_command, prime_sessions
from .providers import record_prime_session
from .store import member, update


def exit_command(kind):
    return '/quit' if kind in ('pi', 'prime-agent', 'codex', 'muse') else '/exit'


def exit_deadline():
    """The epoch second an exit may last until: now plus 15 s (PAIR_EXIT_DEADLINE_S)."""
    return int(time.time()) + env_int('PAIR_EXIT_DEADLINE_S', 15)


def exit_poll():
    """The pause between checks that an agent has gone: 1 s (PAIR_EXIT_POLL_S)."""
    time.sleep(env_float('PAIR_EXIT_POLL_S', 1))


TOLERATED = re.compile('agent_prompt_stalled|agent_not_found|not_found')

# Claude's dialog when background work is running at exit, by the text it shows and the keys that stop the tasks and exit.
EXIT_DIALOGS = (
    ('exit and stop tasks', ('enter',)),
    ('move to background and exit', ('1', 'enter')),
)


def answer_exit_dialog(name):
    """Reads the pane and answers Claude's background-work exit dialog with the option that stops the tasks."""
    screen = pane_text(name).lower()
    for text, keys in EXIT_DIALOGS:
        if text in screen:
            for key in keys:
                mux.get().key(name, key)
            return


def end_agent(name, kind, deadline):
    """Sends the kind's exit command and waits until the agent is absent or the epoch second `deadline` passes, answering Claude's exit dialog
    meanwhile. (error, gone): error is the text of an exit command the multiplexer refused ('' for one it took or whose agent was already gone), and an
    agent that was refused is not waited on. A refusal with agent_not_ready means the pane is already back at its shell and the multiplexer has not
    dropped the agent yet: the wait goes on, and the agent counts as exited only once it no longer lists it."""
    r = mux.get().prompt(name, exit_command(kind), stderr=MERGED)
    err = r.out.rstrip('\n')
    if not r.ok and not TOLERATED.search(err) and r.error != NOT_READY:
        return err, False
    while agent_status(name) != 'absent':
        if int(time.time()) >= deadline:
            return '', False
        answer_exit_dialog(name)
        exit_poll()
    return '', True


def shell_owns_foreground(pane):
    """True when the pane's agent is gone and its shell owns the foreground process group; None when the multiplexer cannot say."""
    process = mux.get().processes(pane)
    if process is None:
        return None
    if agent_status(pane) != 'absent':
        return False
    shell = process.shell_pid
    return (
        isinstance(shell, (int, float))
        and not isinstance(shell, bool)
        and process.foreground_group == shell
    )


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
        sys.stderr.write(
            f'no live daemon session matches {name} in its recorded socket; inspect the client before cleanup\n'
        )
        return False
    # Detach while the client is still an active Herdr target. A blocked client cannot take /quit through agent.prompt; Ctrl+D exits its
    # empty editor. Stopping the worker alone leaves the TUI displaying a session-closed error.
    if status in ('idle', 'done'):
        mux.get().prompt(name, '/quit')
    elif status == 'blocked':
        mux.get().key(name, 'ctrl+d')
    if active:
        if prime_command(store, role, 'stop', active, '--json')[0] != 0:
            return False
        failed, sessions = prime_sessions(store, role)
        if failed or not all(
            each is None or isinstance(each, dict) for each in sessions
        ):
            return False
        if any(
            each is not None and each.get('activeSessionId') == active
            for each in sessions
        ):
            sys.stderr.write(
                f'Prime session {active} is still active; its pane and scratch stay\n'
            )
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
            sys.stderr.write(
                f'Prime client has not exited in {pane}; inspect it before cleanup\n'
            )
            return False
        exit_poll()

    def mutate(doc):
        member(doc, role)['session_stopped'] = True

    update(store, mutate)
    return True
