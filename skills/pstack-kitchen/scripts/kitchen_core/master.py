"""close, rotate-master and take-master: the end of a run, and the hand-over between master generations. Ported from kitchen.sh at 9f0f6e5."""

import os
import re
import shlex
import shutil
import sys
import time

from pstack_core import procinfo
from pstack_core.agents import mcp_args, permission_args
from pstack_core.argv import Spec
from pstack_core.argv import read as read_options
from pstack_core.errors import (
    DamagedStore,
    Die,
    Exit,
    HandoffNotReady,
    NextMasterNotStarted,
    RotateRefused,
)
from pstack_core.exits import end_agent, exit_deadline, stop_prime_role
from pstack_core.numbers import env_float, env_int
from pstack_core.panes import (
    agent_kind,
    agent_prompt,
    agent_start,
    agent_status,
    error_state,
    herdr,
    new_pane,
    refuse_untrusted,
    reply_at,
    require_herdr,
)
from pstack_core.permission import caller_pid, mode_of_this_shell
from pstack_core.review import require_filled
from pstack_core.send import submit_typed
from pstack_core.store import (
    event,
    field,
    json_type,
    listing,
    load,
    member,
    pair_file,
    peek,
    read,
    read_flag,
    read_text,
    text_at,
    update,
    who,
)

from .verify_wait import warn_unreviewed


def close_role(store, role):
    """Ends a role's agent and closes its pane, for a run that is over: the exit command, then up to fifteen seconds for the agent to go (a
    Claude asking what to do with its background tasks is told to stop them and exit). The pane closes only once the agent is gone; an agent
    that stays keeps its pane and the function returns False. A role never spawned, or already closed, is skipped (True)."""
    name = field(store, role, 'name')
    pane = field(store, role, 'pane_id')
    if not name or not pane:
        return True
    if read_flag(store, f'{role}.closed') is True:
        return True
    kind = field(store, role, 'kind')
    if kind == 'prime-agent':
        if not stop_prime_role(store, role):
            return False
    if agent_status(name) != 'absent':
        end_agent(name, kind, exit_deadline())
    if agent_status(name) != 'absent':
        sys.stderr.write(f'{role} {name} did not exit; its pane {pane} stays open\n')
        return False
    herdr('pane', 'close', pane)
    update(store, lambda doc: member(doc, role).__setitem__('closed', True))
    event(store, 'master', 'close', '-', f'{role}:{kind}')
    sys.stdout.write(f'closed {role} {name} ({pane})\n')
    sys.stdout.flush()
    return True


def close(ctx, args):
    require_herdr()
    if not args:
        raise Die('usage: kitchen.sh close <store> [--force]')
    store = args[0]
    force = read_options(
        args[1:], Spec(flags=('--force',), slots={'--force': 0}, unknown='collect')
    ).present('--force')
    pair_file(store)
    if not force:
        if field(store, 'verifying', 'verdict'):
            raise Die(
                f'a verification is open ({field(store, "verifying", "units")}); finish it (kitchen.sh verify {store} --wait) or pass --force',
                5,
            )
        if agent_status(field(store, 'sidekick', 'name')) == 'working':
            raise Die(
                f'the sidekick is working; kitchen.sh stop {store} first, or pass --force',
                5,
            )
    failed = False
    for role in ('verifier', 'consultant', 'sidekick'):
        if not close_role(store, role):
            failed = True
    if failed:
        raise Exit(5)
    return 0


def detect_model_arg():
    """The --model of the Claude session this shell runs under, found by walking up the process tree (at most twelve parents): the parent,
    name and arguments for each pid; node or bun running a claude script count as claude. '' when none."""
    pid, depth = caller_pid(), 0
    while pid > 1 and depth < 12:
        proc = procinfo.read(pid, procinfo.proc_root())
        if proc is None:
            break
        comm, args = os.path.basename(proc.comm), proc.args
        if comm in ('node', 'bun') and 'claude' in args:
            comm = 'claude'
        if comm == 'claude':
            m = re.match(r'.*--model[= ]([^ ]+).*', args)
            return m.group(1) if m else ''
        pid, depth = proc.ppid, depth + 1
    return ''


def section_has_text(path, heading):
    """Whether the section under the `## <heading>` line holds a line with any text."""
    inside = found = False
    lines = read_text(path).split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    for line in lines:
        if line == heading:
            inside = True
            continue
        if line.startswith('## '):
            inside = False
        if inside and line.split():
            found = True
    return found


def herdr_session(name):
    """the session value of `herdr agent get NAME`, a reply that cannot be read ignored."""
    return (
        reply_at(
            herdr('agent', 'get', name)[1],
            'result',
            'agent',
            'agent_session',
            'value',
            quiet=True,
        )
        or ''
    )


def check_handoff(ctx, store):
    """The handoff the next master reads: written from the template, filled, with text under Goal and Next. Returns its path."""
    handoff = f'{store}/session-handoff.md'
    if not os.path.isfile(handoff):
        raise HandoffNotReady(
            f'no handoff; write one first: kitchen.sh new-handoff {store}'
        )
    require_filled(ctx, handoff)
    for heading in ('Goal', 'Next'):
        if not section_has_text(handoff, f'## {heading}'):
            raise HandoffNotReady(f'{handoff}: the {heading} section is empty')
    return handoff


def rotatable_master(store):
    """(name, pane) of the master that hands over: no hand-over may be open, and it must be a live Claude."""
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    if read(store, 'master.previous.name'):
        raise RotateRefused(
            f'a hand-over is open: {name} has not taken over yet; it runs kitchen.sh take-master {store} first'
        )
    if agent_status(name) == 'absent':
        raise RotateRefused(
            f'{who("master", name)} is not live'
            + (f' in {pane}' if pane else '')
            + "; run this from the master's own session"
        )
    kind = agent_kind(name)
    if kind != 'claude':
        raise RotateRefused(f'rotate-master restarts a Claude master; {name} is {kind}')
    return name, pane


def master_generation(store, session):
    """The generation handing over. A rotation that never finished already recorded this session; it keeps that generation instead of skipping one."""
    gen = read(store, 'master.generation', '1')
    sessions = listing(peek(load(store), 'master'), 'sessions')
    if sessions and not isinstance(sessions[-1], dict):
        raise DamagedStore(
            f'master.sessions holds {json_type(sessions[-1])}, not an object'
        )
    if session and sessions and session == text_at(sessions[-1], 'session'):
        gen = text_at(sessions[-1], 'generation')
    return int(gen)


def next_master_argv(store, temp, model):
    """(argv, whether remote control is on) the next Claude master starts with: the master's permission, the model, the MCP opt-out and, unless
    KITCHEN_MASTER_REMOTE_CONTROL is 0, remote control under the generation's name."""
    permission = read(store, 'master.permission_mode') or mode_of_this_shell()
    argv = list(permission_args('claude', permission))
    if model:
        argv += ['--model', model]
    argv += mcp_args('claude')
    remote = os.environ.get('KITCHEN_MASTER_REMOTE_CONTROL', '1') != '0'
    if remote:
        argv += ['--remote-control', temp]
    return argv, remote


def write_start_failure_log(store, next_gen, argv, err, pane):
    """sessions/master-g<n>-start-failed.log: the launch command, herdr's error and the pane's screen, which a closed pane or a read that fails
    says in place of raising. Returns its path."""
    code, screen = herdr('pane', 'read', pane, '--source', 'visible', '--lines', '80')
    screen = (
        screen if code == 0 else f'(the pane could not be read: herdr exit {code})\n'
    )
    os.makedirs(f'{store}/sessions', exist_ok=True)
    path = f'{store}/sessions/master-g{next_gen}-start-failed.log'
    with open(path, 'w') as f:
        f.write(
            f'## launch\n\n{shlex.join(argv)}\n\n## herdr\n\n{err}\n\n## pane screen\n\n{screen}'
        )
    return path


def start_next_master(store, pane, temp, argv, gen_n):
    """The next master's pane beside the old one, with its agent started. A start that fails leaves the old master in charge and the new pane open,
    with sessions/master-g<n>-start-failed.log saying why; a folder-trust dialog closes the pane."""
    cwd = read(store, 'cwd')
    new_pane_id = new_pane(store, 'master', pane, '', cwd)
    sys.stdout.flush()
    started = agent_start(temp, 'claude', new_pane_id, argv)
    if started.returncode == 0:
        return new_pane_id
    err = started.stderr or ''
    next_gen = gen_n + 1
    log = write_start_failure_log(store, next_gen, argv, err, new_pane_id)
    event(
        store,
        'master',
        'rotate-failed',
        '-',
        f'generation:{next_gen}:{error_state(err)}',
    )
    refuse_untrusted(new_pane_id, 'claude', cwd, True)
    raise NextMasterNotStarted(
        f'the next master did not start in {new_pane_id}; its pane stays open and the log is {log}; '
        f'you stay the master (generation {gen_n}), so carry on and rotate at a later boundary'
    )


def record_handover(store, handoff, gen_n, session, old, new):
    """Keeps the handoff under sessions/ and records the hand-over in pair.json: .master.previous is the old master (name, pane), .master the new
    one at the next generation, and the closed generation joins .master.sessions. `old` and `new` are (name, pane) pairs. Returns the copy's path."""
    os.makedirs(f'{store}/sessions', exist_ok=True)
    copy = f'{store}/sessions/master-{gen_n}-handoff.md'
    shutil.copyfile(handoff, copy)
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def mutate(doc):
        # the hand-over: .master.previous, .master.name, .master.pane_id, .master.registered_at, .master.generation, .master.units_since_handoff, .master.sessions
        master = member(doc, 'master')
        listed = listing(master, 'sessions')
        for each in listed:
            if not isinstance(each, dict):
                raise DamagedStore(f'sessions holds {json_type(each)}, not an object')
        kept = [e for e in listed if session == '' or e.get('session') != session]
        master['previous'] = {'name': old[0], 'pane_id': old[1], 'session': session}
        master['name'], master['pane_id'], master['registered_at'] = new[0], new[1], now
        master['generation'] = gen_n + 1
        master['units_since_handoff'] = 0
        master['sessions'] = kept + [
            {'generation': gen_n, 'session': session, 'handoff': copy, 'at': now}
        ]

    update(store, mutate)
    return copy


def rotate_master(ctx, args):
    require_herdr()
    if not args:
        raise Die('usage: kitchen.sh rotate-master <store> [--model MODEL]')
    store = args[0]
    model = read_options(args[1:], Spec(valued=('--model',))).last('--model', '')
    pair_file(store)
    handoff = check_handoff(ctx, store)
    name, pane = rotatable_master(store)
    model = model or detect_model_arg()
    session = herdr_session(name)
    gen_n = master_generation(store, session)
    next_gen = gen_n + 1
    # Herdr names are unique, so the new master holds a generation name until take-master frees the run's own.
    temp = f'{read(store, "slug")}-master-g{next_gen}'
    argv, remote = next_master_argv(store, temp, model)
    new_pane_id = start_next_master(store, pane, temp, argv, gen_n)
    copy = record_handover(
        store, handoff, gen_n, session, (name, pane), (temp, new_pane_id)
    )
    event(store, 'master', 'handoff', '-', f'generation:{gen_n}')
    event(store, 'master', 'spawn', '-', f'claude:generation:{next_gen}')
    skill = ctx.skill
    agent_prompt(
        temp,
        f'Load the {skill} skill from ~/.agents/skills/{skill}/SKILL.md and take the master role: {skill} MASTER {store} (generation {next_gen}). '
        f'Run ~/.agents/skills/{skill}/scripts/kitchen.sh take-master {store} first, then read {store}/session-handoff.md.',
        wait=True,
        until='working',
        timeout_ms=30000,
    )
    submit_typed(temp, 'take-master')
    out = sys.stdout
    out.write(
        f'handoff recorded: {copy} (generation {gen_n}, session {session or "unknown"})\n'
    )
    out.write(
        f'next master: {temp} (generation {next_gen}) in {new_pane_id}{", remote control on" if remote else ""}; it closes this pane with take-master\n'
    )
    out.write(
        f'next: end your turn now, with no further commands, and act on no later prompt or task notification: generation {next_gen} owns the run. '
        f'Resume this session later with: claude --resume {session or "<id>"}\n'
    )
    return 0


def take_master(ctx, args):
    require_herdr()
    if len(args) != 1:
        raise Die('usage: kitchen.sh take-master <store>')
    store = args[0]
    pair_file(store)
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    prev = read(store, 'master.previous.name')
    if not prev:
        sys.stdout.write(f'no hand-over open; {name} is the master in {pane}\n')
        return 0
    here = os.environ.get('HERDR_PANE_ID', '')
    if here != pane:
        raise Die(
            f"take-master runs from the new master's pane {pane}, not {here or 'an unknown pane'}",
            5,
        )
    prev_pane = read(store, 'master.previous.pane_id')
    deadline = int(time.time()) + env_int('KITCHEN_TAKEOVER_WAIT_S', 30)
    while agent_status(prev) == 'working' and int(time.time()) < deadline:
        time.sleep(env_float('KITCHEN_ROTATE_POLL_S', 2))
    # Close the pane only while it still holds the old master, or nothing.
    host = (
        reply_at(
            herdr('agent', 'get', prev_pane)[1], 'result', 'agent', 'name', quiet=True
        )
        or ''
    )
    closed = 'kept'
    if herdr('pane', 'get', prev_pane)[0] != 0:
        closed = 'gone'
    elif not host or host == prev:
        if herdr('pane', 'close', prev_pane)[0] == 0:
            closed = 'closed'
    base = f'{read(store, "slug")}-master'
    if name != base and herdr('agent', 'rename', pane, base)[0] == 0:
        name = base

    def mutate(doc):
        master = member(doc, 'master')
        master['name'] = name
        master.pop('previous', None)

    update(store, mutate)
    event(
        store,
        'master',
        'takeover',
        '-',
        f'generation:{field(store, "master", "generation")}',
    )
    out = sys.stdout
    if closed == 'closed':
        out.write(f'closed the old master {prev} ({prev_pane})\n')
    elif closed == 'gone':
        out.write(f'the old master pane {prev_pane} is already gone\n')
    else:
        out.write(
            f'left pane {prev_pane} open: it now holds {host}, not the old master {prev}\n'
        )
    out.write(
        f'master: {name} in {pane} (generation {field(store, "master", "generation")})\n'
    )
    out.flush()
    warn_unreviewed(store)
    return 0
