"""Prime's daemon from the pair: the management commands that must use the launched client's socket, the live session a role owns, and
the steer that reaches it. stop_prime_role (exits.py) uses them."""
import json
import os
import shutil
import subprocess
import sys

from .providers import record_prime_session
from .review import jq_raw
from .store import alt, load
from .wait import as_text, json_values

PERL_TIMEOUT = ('my $s = shift; my $p = fork // die "fork: $!\n";\n'
                'unless ($p) { exec { $ARGV[0] } @ARGV or exit 127 }\n'
                'local $SIG{ALRM} = sub { kill "TERM", $p; waitpid $p, 0; exit 124 };\n'
                'alarm $s; waitpid $p, 0; exit($? & 127 ? 128 + ($? & 127) : $? >> 8)')


def with_timeout(seconds, argv, **kwargs):
    """with_timeout: timeout(1) where coreutils has it, else perl with the same contract (exit 124 when the command runs out). Keyword
    arguments go to subprocess.run; stdout is a pipe unless given."""
    kwargs.setdefault('stdout', subprocess.PIPE)
    if shutil.which('timeout'):
        return subprocess.run(['timeout', seconds, *argv], check=False, **kwargs)
    return subprocess.run(['perl', '-e', PERL_TIMEOUT, seconds, *argv], check=False, **kwargs)


def section(pair, role):
    value = pair.get(role)
    return value if isinstance(value, dict) else {}


def daemon_socket(store, role):
    """The --daemon-socket the client was launched with, made absolute against its launch directory; '' when it had none."""
    pair = load(store)
    start_args = section(pair, role).get('start_args')
    if isinstance(start_args, dict):
        start_args = list(start_args.values())
    socket, take = '', False
    for element in start_args if isinstance(start_args, list) else []:
        for arg in as_text(element).split('\n'):
            if take:
                socket, take = arg, False
            elif arg == '--daemon-socket':
                take = True
            elif arg.startswith('--daemon-socket='):
                socket = arg.split('=', 1)[1]
    if socket and not socket.startswith('/'):
        base = section(pair, role).get('launch_cwd')
        socket = f'{jq_raw(base if base not in (None, False) else pair.get("cwd"))}/{socket}'
    return socket


def prime_command(store, role, *args):
    """prime-agent ARGS with the role's socket, under PAIR_PRIME_COMMAND_TIMEOUT seconds: (exit code, stdout as a substitution leaves it)."""
    socket = daemon_socket(store, role)
    r = with_timeout(os.environ.get('PAIR_PRIME_COMMAND_TIMEOUT') or '30', ['prime-agent', *args, *(['--socket', socket] if socket else [])])
    return r.returncode, r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')


def prime_sessions(store, role):
    """(failed, sessions): the daemon's session list. A reply that holds no `sessions` array, or cannot be read, fails."""
    code, out = prime_command(store, role, 'list', '--json')
    if code != 0:
        return True, None
    try:
        reply = json.loads(out)
    except ValueError:
        sys.stderr.write(f'Prime session list for {role} is not JSON\n')
        return True, None
    sessions = reply.get('sessions') if isinstance(reply, dict) else None
    return (False, sessions) if isinstance(sessions, list) else (True, None)


def prime_active_session(store, role):
    """The live daemon id of the role's recorded session, by exact path or id (no prefix or name matching: another kitchen or the human may
    have a similar session name): '' when none is live, None, with the reason on stderr, when the answer is unknown or ambiguous."""
    session = section(load(store), role).get('session')
    if session is None or session is False or (isinstance(session, str) and session == ''):
        sys.stderr.write(f'no recorded Prime session for {role}; inspect its pane before cleanup\n')
        return None
    if not isinstance(session, dict):
        sys.stderr.write(f'recorded Prime session for {role} is not an object; inspect its pane before cleanup\n')
        return None
    failed, sessions = prime_sessions(store, role)
    if failed:
        return None
    matches = set()
    for each in sessions or []:
        live = each.get('activeSessionId') if isinstance(each, dict) else None
        if not (isinstance(live, str) and live):
            continue
        if session.get('kind') == 'path':
            hit = each.get('sessionFile') == session.get('value')
        else:
            hit = each.get('sessionId') == session.get('value') or live == session.get('value')
        if hit:
            matches.add(live)
    if len(matches) > 1:
        sys.stderr.write(f'multiple live Prime sessions match {as_text(session)}; none was stopped\n')
        return None
    return matches.pop() if matches else ''


def steer_confirmed(receipt, active):
    """Whether a `prime-agent send --json` receipt confirms the steer lane for the session `active`. A reply of several JSON values is judged
    by its last one. A receipt that cannot be judged says why on stderr."""
    values, ok = json_values(receipt)
    if not ok:
        sys.stderr.write('Prime steer receipt is not JSON\n')
        return False
    if not values:
        sys.stderr.write('Prime steer receipt is empty\n')
        return False
    last = values[-1]
    if not isinstance(last, dict):
        sys.stderr.write('Prime steer receipt is not an object\n')
        return False
    target = last.get('target')
    if target is not None and not isinstance(target, dict):
        sys.stderr.write('Prime steer receipt has a target that is not an object\n')
        return False
    return (last.get('deliveryMode') == 'steer' and (target or {}).get('activeSessionId') == active
            and last.get('deliveryStatus') in ('queued', 'delivered'))


def prime_steer(store, text):
    """Steer the sidekick's Prime session. Prime 0.10.0 advertises --steer but its Rust CLI rejects that flag; plain send defaults to
    steering, so a receipt that confirms that lane is required."""
    if not record_prime_session(store, 'sidekick'):
        return False
    active = prime_active_session(store, 'sidekick')
    if not active:
        return False
    code, out = prime_command(store, 'sidekick', 'send', '--json', active, text)
    if code != 0:
        return False
    return steer_confirmed(out, active)
