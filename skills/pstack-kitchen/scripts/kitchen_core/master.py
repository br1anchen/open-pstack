"""close, rotate-master and take-master: the end of a run, and the hand-over between master generations. Ported from kitchen.sh at 9f0f6e5."""

import os
import re
import shutil
import subprocess
import sys
import time

from pstack_core.agents import mcp_args, permission_args
from pstack_core.commands.report import in_herdr
from pstack_core.errors import Die, Exit, unbound_arg
from pstack_core.exits import exit_command, exit_deadline, exit_poll, stop_prime_role
from pstack_core.herdr import agent_kind, agent_status, herdr, pane_text
from pstack_core.panes import new_pane, refuse_untrusted
from pstack_core.permission import caller_pid, mode_of_this_shell
from pstack_core.review import require_filled
from pstack_core.send import submit_typed
from pstack_core.store import field
from pstack_core.store import alt, damaged, event, json_type, listing, load, member, pair_file, peek, update
from pstack_core.wait import as_text

from .jqx import herdr_value, pair_text
from .reviews import warn_unreviewed


def close_role(store, role):
    """Ends a role's agent and closes its pane, for a run that is over: the exit command, then up to fifteen seconds for the agent to go (a
    Claude asking what to do with its background tasks is told to stop them and exit). The pane closes only once the agent is gone; an agent
    that stays keeps its pane and the function returns False. A role never spawned, or already closed, is skipped (True)."""
    name = field(store, role, 'name', empty=True)
    pane = field(store, role, 'pane_id', empty=True)
    if not name or not pane:
        return True
    if field(store, role, 'closed', default='false') == 'true':
        return True
    kind = field(store, role, 'kind', empty=True)
    if kind == 'prime-agent':
        if not stop_prime_role(store, role):
            return False
    if agent_status(name) != 'absent':
        herdr('agent', 'prompt', name, exit_command(kind))
        deadline = exit_deadline()
        while agent_status(name) != 'absent' and int(time.time()) < deadline:
            if 'move to background and exit' in pane_text(name).lower():
                herdr('agent', 'send-keys', name, '1')
                herdr('agent', 'send-keys', name, 'enter')
            exit_poll()
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
    in_herdr()
    if not args:
        raise Die('usage: kitchen.sh close <store> [--force]')
    store = args[0]
    force = len(args) > 1 and args[1] == '--force'
    pair_file(store)
    if not force:
        if field(store, 'verifying', 'verdict', empty=True):
            raise Die(f"a verification is open ({field(store, 'verifying', 'units')}); finish it (kitchen.sh verify {store} --wait) or pass --force", 5)
        if agent_status(field(store, 'sidekick', 'name')) == 'working':
            raise Die(f'the sidekick is working; kitchen.sh stop {store} first, or pass --force', 5)
    failed = False
    for role in ('verifier', 'consultant', 'sidekick'):
        if not close_role(store, role):
            failed = True
    if failed:
        raise Exit(5)
    return 0


def detect_model_arg():
    """The --model of the Claude session this shell runs under, found by walking up the process tree (at most twelve parents): ps's parent,
    name and arguments for each pid; node or bun running a claude script count as claude. '' when none."""
    def ps(name, pid):
        return subprocess.run(['ps', '-o', f'{name}=', '-p', str(pid)], capture_output=True, text=True, check=False).stdout

    pid, depth = caller_pid(), 0
    while pid > 1 and depth < 12:
        ppid = ps('ppid', pid).replace(' ', '').replace('\n', '')
        if not ppid:
            break
        comm = os.path.basename(ps('comm', pid).rstrip('\n'))
        args = ps('args', pid).rstrip('\n')
        if comm in ('node', 'bun') and 'claude' in args:
            comm = 'claude'
        if comm == 'claude':
            m = re.match(r'.*--model[= ]([^ ]+).*', args)
            return m.group(1) if m else ''
        pid, depth = int(ppid), depth + 1
    return ''


def section_has_text(path, heading):
    """awk -v h="## <heading>" '$0 == h {p=1; next} /^## /{p=0} p && NF': whether the section holds a line with a field."""
    inside = found = False
    with open(path, 'rb') as f:
        lines = f.read().decode('utf-8', 'surrogateescape').split('\n')
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
    return herdr_value(herdr('agent', 'get', name)[1], 'result', 'agent', 'agent_session', 'value', quiet=True)


def rotate_master(ctx, args):
    in_herdr()
    if not args:
        raise Die('usage: kitchen.sh rotate-master <store> [--model MODEL]')
    store, rest, model = args[0], args[1:], ''
    while rest:
        if rest[0] == '--model':
            if len(rest) < 2:
                raise unbound_arg()
            model, rest = rest[1], rest[2:]
        else:
            raise Die(f'unknown option {rest[0]}')
    pair_file(store)
    handoff = f'{store}/session-handoff.md'
    if not os.path.isfile(handoff):
        raise Die(f'no handoff; write one first: kitchen.sh new-handoff {store}', 5)
    require_filled(ctx, handoff)
    for heading in ('Goal', 'Next'):
        if not section_has_text(handoff, f'## {heading}'):
            raise Die(f'{handoff}: the {heading} section is empty', 5)
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    if pair_text(store, 'master.previous.name', empty=True):
        raise Die(f'a hand-over is open: {name} has not taken over yet; it runs kitchen.sh take-master {store} first', 5)
    if agent_status(name) == 'absent':
        raise Die(f"master {name} is not live in {pane}; run this from the master's own session", 5)
    kind = agent_kind(name)
    if kind != 'claude':
        raise Die(f'rotate-master restarts a Claude master; {name} is {kind}', 5)
    model = model or detect_model_arg()
    session = herdr_session(name)
    gen = pair_text(store, 'master.generation', default=1)
    # A rotation that never finished already recorded this session; it keeps that generation instead of skipping one.
    sessions = listing(peek(load(store), 'master'), 'sessions')
    if sessions and not isinstance(sessions[-1], dict):
        raise damaged(f'master.sessions holds {json_type(sessions[-1])}, not an object')
    if session and sessions and session == as_text(alt(sessions[-1].get('session'), '')):
        gen = as_text(sessions[-1].get('generation'))
    gen_n = int(gen)
    next_gen = gen_n + 1
    # Herdr names are unique, so the new master holds a generation name until take-master frees the run's own.
    temp = f"{pair_text(store, 'slug', check=True)}-master-g{next_gen}"
    permission = pair_text(store, 'master.permission_mode', empty=True, check=True) or mode_of_this_shell(False)
    argv = list(permission_args('claude', permission))
    if model:
        argv += ['--model', model]
    argv += mcp_args('claude')[0]
    remote = os.environ.get('KITCHEN_MASTER_REMOTE_CONTROL', '1') != '0'
    if remote:
        argv += ['--remote-control', temp]
    cwd = pair_text(store, 'cwd', check=True)
    new_pane_id = new_pane(store, 'master', pane, '', cwd)
    sys.stdout.flush()
    started = subprocess.run(['herdr', 'agent', 'start', temp, '--kind', 'claude', '--pane', new_pane_id, '--timeout', '60000', '--', *argv],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
    if not started:
        refuse_untrusted(new_pane_id, 'claude', cwd, True)
        herdr('pane', 'close', new_pane_id)
        event(store, 'master', 'rotate-failed', '-', f'generation:{next_gen}')
        raise Die(f'the next master did not start in {new_pane_id}; you stay the master (generation {gen}), so carry on and rotate at a later boundary', 2)
    os.makedirs(f'{store}/sessions', exist_ok=True)
    copy = f'{store}/sessions/master-{gen}-handoff.md'
    shutil.copyfile(handoff, copy)
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def mutate(doc):
        # the hand-over: .master.previous, .master.name, .master.pane_id, .master.registered_at, .master.generation, .master.units_since_handoff, .master.sessions
        master = member(doc, 'master')
        listed = listing(master, 'sessions')
        for each in listed:
            if not isinstance(each, dict):
                raise damaged(f'sessions holds {json_type(each)}, not an object')
        kept = [e for e in listed if session == '' or e.get('session') != session]
        master['previous'] = {'name': name, 'pane_id': pane, 'session': session}
        master['name'], master['pane_id'], master['registered_at'] = temp, new_pane_id, now
        master['generation'] = gen_n + 1
        master['units_since_handoff'] = 0
        master['sessions'] = kept + [{'generation': gen_n, 'session': session, 'handoff': copy, 'at': now}]

    update(store, mutate)
    event(store, 'master', 'handoff', '-', f'generation:{gen}')
    event(store, 'master', 'spawn', '-', f'claude:generation:{next_gen}')
    skill = ctx.skill
    herdr('agent', 'prompt', temp, f'Load the {skill} skill from ~/.agents/skills/{skill}/SKILL.md and take the master role: {skill} MASTER {store} (generation {next_gen}). '
          f'Run ~/.agents/skills/{skill}/scripts/kitchen.sh take-master {store} first, then read {store}/session-handoff.md.', '--wait', '--until', 'working',
          '--timeout', '30000')
    submit_typed(temp, 'take-master')
    out = sys.stdout
    out.write(f'handoff recorded: {copy} (generation {gen}, session {session or "unknown"})\n')
    out.write(f'next master: {temp} (generation {next_gen}) in {new_pane_id}{", remote control on" if remote else ""}; it closes this pane with take-master\n')
    out.write(f'next: end your turn now, with no further commands, and act on no later prompt or task notification: generation {next_gen} owns the run. '
              f'Resume this session later with: claude --resume {session or "<id>"}\n')
    return 0


def take_master(ctx, args):
    in_herdr()
    if len(args) != 1:
        raise Die('usage: kitchen.sh take-master <store>')
    store = args[0]
    pair_file(store)
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    prev = pair_text(store, 'master.previous.name', empty=True, check=True)
    if not prev:
        sys.stdout.write(f'no hand-over open; {name} is the master in {pane}\n')
        return 0
    here = os.environ.get('HERDR_PANE_ID', '')
    if here != pane:
        raise Die(f"take-master runs from the new master's pane {pane}, not {here or 'an unknown pane'}", 5)
    prev_pane = pair_text(store, 'master.previous.pane_id', check=True)
    deadline = int(time.time()) + int(os.environ.get('KITCHEN_TAKEOVER_WAIT_S') or 30)
    while agent_status(prev) == 'working' and int(time.time()) < deadline:
        time.sleep(float(os.environ.get('KITCHEN_ROTATE_POLL_S') or 2))
    # Close the pane only while it still holds the old master, or nothing.
    host = herdr_value(herdr('agent', 'get', prev_pane)[1], 'result', 'agent', 'name', quiet=True)
    closed = 'kept'
    if herdr('pane', 'get', prev_pane)[0] != 0:
        closed = 'gone'
    elif not host or host == prev:
        if herdr('pane', 'close', prev_pane)[0] == 0:
            closed = 'closed'
    base = f"{pair_text(store, 'slug', check=True)}-master"
    if name != base and herdr('agent', 'rename', pane, base)[0] == 0:
        name = base

    def mutate(doc):
        master = member(doc, 'master')
        master['name'] = name
        master.pop('previous', None)

    update(store, mutate)
    event(store, 'master', 'takeover', '-', f"generation:{field(store, 'master', 'generation')}")
    out = sys.stdout
    if closed == 'closed':
        out.write(f'closed the old master {prev} ({prev_pane})\n')
    elif closed == 'gone':
        out.write(f'the old master pane {prev_pane} is already gone\n')
    else:
        out.write(f'left pane {prev_pane} open: it now holds {host}, not the old master {prev}\n')
    out.write(f"master: {name} in {pane} (generation {field(store, 'master', 'generation')})\n")
    out.flush()
    warn_unreviewed(store)
    return 0
