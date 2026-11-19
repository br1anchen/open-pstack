"""close, rotate-master and take-master: the end of a run, and the hand-over between master generations. Ported from kitchen.sh at 9f0f6e5."""

import os
import re
import shlex
import shutil
import sys
import time

from pstack_core import gitstore, mux, procinfo
from pstack_core.agents import mcp_args, permission_args
from pstack_core.argv import Spec
from pstack_core.argv import read as read_options
from pstack_core.commands.rotate import archive_retired
from pstack_core.errors import (
    DamagedStore,
    Die,
    Exit,
    HandoffNotReady,
    NextMasterNotStarted,
    PairError,
    RotateRefused,
)
from pstack_core.exits import end_agent, exit_deadline, stop_prime_role
from pstack_core.numbers import env_float, env_int
from pstack_core.panes import (
    agent_kind,
    agent_session,
    agent_status,
    new_pane,
    refuse_untrusted,
    require_mux,
)
from pstack_core.permission import caller_pid, mode_of_this_shell
from pstack_core.review import require_filled
from pstack_core.send import submit_typed
from pstack_core.spawn import entry_label, entry_preflight, load_skill, walk_chain
from pstack_core.store import (
    count,
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

from . import kpy
from .landing import playbooks
from .verify_wait import warn_unreviewed


def master_context(store):
    """The share (0 to 1; 1 once it compacted) of its context window the master's own thread has used, or None where the backend cannot say."""
    return mux.get().context_used(field(store, 'master', 'name'))


def master_rotate_reason(store):
    """Why the master must rotate before it hands out the next unit: its thread has compacted (context_used reports 1.0 for any compaction, and a
    fresh master thread starts with none) or has used more than KITCHEN_MASTER_CONTEXT_PCT (60) percent of its window. '' when it need not."""
    used = master_context(store)
    if used is None:
        return ''
    if used >= 1:
        return 'compacted'
    percent = round(used * 100)
    if percent > env_int('KITCHEN_MASTER_CONTEXT_PCT', 60):
        return f'context:{percent}%'
    return ''


ROTATE_DUE_EXIT = 9


def note_rotate_due(store, reason):
    """Records .master.rotate_due ({at, reason, generation}) and logs one rotate-due event per master generation, however often (or at once) the
    kitchen refuses in it: the check and the write are one update, and the event is the writer's. rotate-master clears the record (record_handover);
    take-master does not."""
    set_here = []

    def mutate(doc):
        master = member(doc, 'master')
        generation = count(master, 'generation') or 1
        if count(peek(master, 'rotate_due'), 'generation') == generation:
            return
        master['rotate_due'] = {
            'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'reason': reason,
            'generation': generation,
        }
        set_here.append(True)

    update(store, mutate)
    if set_here:
        event(store, 'master', 'rotate-due', '-', reason)


def rotate_notice(store, record=False, audience='master'):
    """The line about the master's rotation, or '' when it need not rotate; with record, the due state is logged too (note_rotate_due). audience is
    who reads it: the master, whose thread runs the command, or a role, whose thread runs a command that only reports it."""
    reason = master_rotate_reason(store)
    if not reason:
        return ''
    if record:
        note_rotate_due(store, reason)
    return rotate_line(reason, store, audience)


def rotate_line(reason, store, audience='master'):
    """The line the kitchen says when the master is due: what happened to its thread and, for the master, the rotation commands; a role is told only
    that the note is for the master."""
    what = (
        'has compacted'
        if reason == 'compacted'
        else f'has used {reason.removeprefix("context:")} of its context'
    )
    if audience != 'master':
        return f"note for the master (the {audience} does nothing): the master's thread {what}; its next dispatch is refused until it rotates\n"
    return (
        f'master: your thread {what}; rotate before dispatching: kitchen.sh new-handoff {store}, fill it, '
        f'kitchen.sh rotate-master {store} [--model <provider/model>]\n'
    )


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
    mux.get().pane_close(pane)
    update(store, lambda doc: member(doc, role).__setitem__('closed', True))
    event(store, 'master', 'close', '-', f'{role}:{kind}')
    sys.stdout.write(f'closed {role} {name} ({pane})\n')
    sys.stdout.flush()
    return True


def close(ctx, args):
    require_mux()
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
    archive_retired(ctx, store, 'the run is closed', retired_only=False)
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
    headings = set(playbooks().section_headings(lines))
    for i, line in enumerate(lines):
        if i in headings:
            inside = line == heading
            continue
        if inside and line.split():
            found = True
    return found


def tracked_session(name):
    """The session value the multiplexer tracks for the agent NAME, '' when it tracks none."""
    return agent_session(mux.get().agent(name))


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
    """sessions/master-g<n>-start-failed.log: the launch command, the multiplexer's error and the pane's screen, which a closed pane or a read that fails
    says in place of raising. Returns its path."""
    m = mux.get()
    code, screen = m.pane_screen(pane, 80)
    screen = (
        screen
        if code == 0
        else f'(the pane could not be read: {m.program} exit {code})\n'
    )
    os.makedirs(f'{store}/sessions', exist_ok=True)
    path = f'{store}/sessions/master-g{next_gen}-start-failed.log'
    with open(path, 'w') as f:
        f.write(
            f'## launch\n\n{shlex.join(argv)}\n\n## {m.program}\n\n{err}\n\n## pane screen\n\n{screen}'
        )
    return path


def start_next_master(store, pane, temp, argv, gen_n):
    """The next master's pane beside the old one, with its agent started. A start that fails leaves the old master in charge and the new pane open,
    with sessions/master-g<n>-start-failed.log saying why; a folder-trust dialog closes the pane."""
    cwd = read(store, 'cwd')
    new_pane_id = new_pane(store, 'master', pane, '', cwd)
    sys.stdout.flush()
    started = mux.get().start(temp, 'claude', new_pane_id, argv)
    if started.ok:
        return new_pane_id
    err = started.err or ''
    next_gen = gen_n + 1
    log = write_start_failure_log(store, next_gen, argv, err, new_pane_id)
    event(
        store,
        'master',
        'rotate-failed',
        '-',
        f'generation:{next_gen}:{started.error}',
    )
    refuse_untrusted(
        new_pane_id,
        'claude',
        cwd,
        True,
        then=f'open claude there once and trust the folder, then rerun kitchen.sh rotate-master {store}; the log is {log}; you stay the master (generation {gen_n})',
    )
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
        # the hand-over: .master.previous, .master.name, .master.pane_id, .master.registered_at, .master.generation, .master.units_since_handoff, .master.landed_since_handoff, .master.sessions
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
        master['landed_since_handoff'] = False
        master.pop('rotate_due', None)
        master['sessions'] = kept + [
            {'generation': gen_n, 'session': session, 'handoff': copy, 'at': now}
        ]

    update(store, mutate)
    return copy


def master_prompt(ctx, store, gen, as_file=False):
    """The prompt that makes an agent the master of generation `gen`; as_file tells it to read the skill's file (an Amp thread that runs Claude Code)."""
    skill = ctx.skill
    return (
        f'{load_skill(skill, as_file)} and take the master role: {skill} MASTER {store} (generation {gen}). '
        f'Run ~/.agents/skills/{skill}/scripts/kitchen.sh take-master {store} first, then read '
        f'~/.agents/skills/{skill}/references/master.md and {store}/session-handoff.md, '
        'each in a read of its own (a file-read tool, not one cat of several files).'
    )


def hand_to_next_session(ctx, store, handoff):
    """The rotation when the master is the one session (no multiplexer): nothing can start beside it, so the hand-over is recorded with the
    same name and pane, the next generation's prompt is written to sessions/master-g<n>-prompt.md, and the human starts a new session with it."""
    if read(store, 'master.previous.name'):
        start = (
            f'{store}/sessions/master-g{field(store, "master", "generation")}-prompt.md'
        )
        how = f'start it with {start}, or ' if os.path.isfile(start) else ''
        raise RotateRefused(
            f'a hand-over is open: generation {field(store, "master", "generation")} has not taken over yet; '
            f'{how}run kitchen.sh take-master {store} here to keep going'
        )
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    gen_n = master_generation(store, '')
    next_gen = gen_n + 1
    prompt = master_prompt(ctx, store, next_gen)
    os.makedirs(f'{store}/sessions', exist_ok=True)
    start = f'{store}/sessions/master-g{next_gen}-prompt.md'
    with open(start, 'w') as f:
        f.write(prompt + '\n')
    copy = record_handover(store, handoff, gen_n, '', (name, pane), (name, pane))
    event(store, 'master', 'handoff', '-', f'generation:{gen_n}')
    out = sys.stdout
    out.write(f'handoff recorded: {copy} (generation {gen_n})\n')
    out.write(f'next master prompt: {start}\n')
    out.write(
        f'next: end this session with no further commands; the human opens a new coding-agent session in {read(store, "cwd")}, '
        f'outside any multiplexer as this one, and gives it the prompt in {start}: generation {next_gen} owns the run\n'
    )
    return 0


def require_master_thread(store):
    """Where every role is a thread (Amp), only the master's own thread rotates the master: a role that runs rotate-master from its own thread would hand
    the run to a new master while the real one still works. Threads are compared, never counter panes, which mean different agents in different
    orbs; a caller or a master whose thread is not known keeps the command."""
    m = mux.get()
    here = m.own_thread()
    master = m.thread_of_pane(field(store, 'master', 'pane_id')) or m.role_thread(
        field(store, 'master', 'name')
    )
    if here and master and here != master:
        raise RotateRefused(
            f"rotate-master: this thread is not the run's master ({here}); only the master rotates"
        )


def args_model(args):
    """The model the arguments of a roster entry name (the last of --model, -m or --mode), '' when none does."""
    model = ''
    for flag, value in zip(args, args[1:], strict=False):
        if flag in ('--model', '-m', '--mode'):
            model = value
    return model


def next_master_model(store, model):
    """The model the next master thread starts on: --model when given, else the first amp entry (where every role is an Amp thread) of the roster's [master] chain whose preflight passes (each
    entry that fails is a failover event, `master:<from>:<to>`), else '' (this thread's own mode), which is also what a chain with no amp entry gives. An amp chain with
    no passing entry refuses the rotation."""
    if model:
        return model
    entry = kpy.call(kpy.kitchen_py.roster_data)[1].get('master')
    if not entry or not entry.get('kind'):
        return ''
    chain = [{'kind': entry['kind'], 'args': entry['args']}, *entry['fallbacks']]
    # The next master is an Amp thread, so an agent of another kind cannot be it.
    for each in [e for e in chain if e['kind'] != 'amp']:
        sys.stdout.write(f'skip: master {entry_label(each)} is not an amp agent\n')
    chain = [e for e in chain if e['kind'] == 'amp']
    if not chain:
        sys.stdout.write(
            "skip: no amp entry in the master chain; the next master runs on this thread's mode\n"
        )
        return ''
    index, steps = walk_chain(chain, 0, entry_preflight)
    for failed, why in steps:
        if failed + 1 < len(chain):
            source, target = entry_label(chain[failed]), entry_label(chain[failed + 1])
            sys.stdout.write(f'failover: master {source} -> {target} ({why})\n')
            event(store, 'master', 'failover', '-', f'master:{source}:{target}')
    if index is None:
        raise RotateRefused(
            f'every agent of the master chain failed its preflight ({", ".join(map(entry_label, chain))}); pass --model to start the next master anyway'
        )
    return args_model(chain[index]['args'])


def publish_master(store):
    """Under git, what the master wrote since its last publication goes to the branch, so a master or role in a clone with an empty state home
    reads the hand-over from there. Nothing under another backend."""
    if gitstore.is_git(store):
        gitstore.publish_role(store, 'master')


def hand_to_next_thread(ctx, store, handoff, model):
    """The rotation when every role is a thread of its own (Amp): the next master starts as a new thread, on --model or this thread's own
    mode, with the store's bundle and the master prompt; its take-master takes the run over there. The hand-over is recorded before the bundle
    is packed, so the new thread reads it, and put back when the thread does not start. This name then means the new master: a role's reply that
    still reaches this thread is forwarded to it."""
    gitstore.refreshed(store)
    if read(store, 'master.previous.name'):
        raise RotateRefused(
            f'a hand-over is open: generation {field(store, "master", "generation")} has not taken over yet; '
            f'its thread runs kitchen.sh take-master {store} first'
        )
    require_master_thread(store)
    model = next_master_model(store, model)
    m = mux.get()
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    gen_n = master_generation(store, '')
    next_gen = gen_n + 1
    temp = f'{read(store, "slug")}-master-g{next_gen}'
    # The events this master wrote so far are in the file of its generation, which it may publish only until the hand-over changes it.
    publish_master(store)
    before = read_text(f'{store}/pair.json')
    new_pane_id = m.new_master(temp)
    copy = record_handover(store, handoff, gen_n, '', (name, pane), (temp, new_pane_id))
    try:
        publish_master(store)
        as_file = m.mode_reads_skill_file(model or m.thread_mode())
        thread = m.launch_master(
            store, temp, master_prompt(ctx, store, next_gen, as_file), model
        )
    except (NextMasterNotStarted, PairError):
        with open(f'{store}/pair.json', 'w') as f:
            f.write(before)
        event(store, 'master', 'rotate-failed', '-', f'generation:{next_gen}')
        raise
    # The pane the next master was given before its thread existed is an orb-local counter; every orb knows the thread's own pane.
    update(
        store,
        lambda doc: member(doc, 'master').__setitem__('pane_id', m.thread_pane(thread)),
    )
    m.hand_over(name, temp)
    event(store, 'master', 'handoff', '-', f'generation:{gen_n}')
    event(store, 'master', 'spawn', '-', f'amp:generation:{next_gen}')
    publish_master(store)
    out = sys.stdout
    out.write(f'handoff recorded: {copy} (generation {gen_n})\n')
    out.write(
        f'next master: {temp} (generation {next_gen}) in thread {thread}; it runs take-master there\n'
    )
    out.write(
        f'next: end your turn now, with no further commands: generation {next_gen} owns the run, and a role reply that still reaches this '
        'thread names a sync that forwards it there\n'
    )
    return 0


def rotate_master(ctx, args):
    require_mux()
    if not args:
        raise Die('usage: kitchen.sh rotate-master <store> [--model MODEL]')
    store = args[0]
    model = read_options(args[1:], Spec(valued=('--model',))).last('--model', '')
    pair_file(store)
    handoff = check_handoff(ctx, store)
    if mux.get().master_threads:
        return hand_to_next_thread(ctx, store, handoff, model)
    if not mux.get().starts_master:
        return hand_to_next_session(ctx, store, handoff)
    name, pane = rotatable_master(store)
    model = model or detect_model_arg()
    session = tracked_session(name)
    gen_n = master_generation(store, session)
    next_gen = gen_n + 1
    # Agent names are unique, so the new master holds a generation name until take-master frees the run's own.
    temp = f'{read(store, "slug")}-master-g{next_gen}'
    argv, remote = next_master_argv(store, temp, model)
    new_pane_id = start_next_master(store, pane, temp, argv, gen_n)
    copy = record_handover(
        store, handoff, gen_n, session, (name, pane), (temp, new_pane_id)
    )
    event(store, 'master', 'handoff', '-', f'generation:{gen_n}')
    event(store, 'master', 'spawn', '-', f'claude:generation:{next_gen}')
    mux.get().prompt(
        temp,
        master_prompt(ctx, store, next_gen),
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


def own_master_pane():
    """The pane the calling master is in. A master that is a thread is known by it: the registry's own pane for it is a counter of one orb."""
    m = mux.get()
    if m.master_threads and m.own_thread():
        return m.thread_pane(m.own_thread())
    return m.own_pane()


def adopt_thread_pane(store, pane, here):
    """(pane, adopted) the new master of a thread hand-over holds. Its bundle was packed when its thread started, before the old master recorded that thread's
    pane, so the store it synced may still hold the counter pane the next master had until then (the counter of the old master's orb). Only the thread the
    hand-over prompt went to runs take-master with a hand-over open, so a counter pane is that thread's to take: it is recorded as the thread's own pane. The pane
    of another thread is left, and refuses the caller."""
    m = mux.get()
    if (
        not m.master_threads
        or not m.own_thread()
        or pane == here
        or m.thread_of_pane(pane)
    ):
        return pane, False
    update(store, lambda doc: member(doc, 'master').__setitem__('pane_id', here))
    return here, True


def new_session_takes_run(store, here, pane):
    """With no multiplexer the next master is a new session, in a pane of its own: one in the run's checkout takes the run where it is. A master that
    is a thread is placed by its thread, never by where it runs."""
    m = mux.get()
    return (
        not m.starts_master
        and not m.master_threads
        and here != pane
        and bool(read(store, 'cwd'))
        and os.path.realpath(os.getcwd()) == os.path.realpath(read(store, 'cwd'))
    )


def take_master(ctx, args):
    require_mux()
    if len(args) != 1:
        raise Die('usage: kitchen.sh take-master <store>')
    store = args[0]
    pair_file(store)
    gitstore.refreshed(store)
    name = field(store, 'master', 'name')
    pane = field(store, 'master', 'pane_id')
    prev = read(store, 'master.previous.name')
    if not prev:
        sys.stdout.write(f'no hand-over open; {name} is the master in {pane}\n')
        return 0
    here = own_master_pane()
    pane, adopted = adopt_thread_pane(store, pane, here)
    moved = new_session_takes_run(store, here, pane)
    if moved:
        update(store, lambda doc: member(doc, 'master').__setitem__('pane_id', here))
        pane = here
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
    m = mux.get()
    hosted = m.agent(prev_pane)
    host = hosted.name if hosted else ''
    closed = 'kept'
    if prev_pane == pane:
        # One session handed to the next in the same place (no multiplexer): there is no old pane to close.
        closed = 'same'
    elif not m.pane_exists(prev_pane):
        closed = 'gone'
    elif not host or host == prev:
        if m.pane_close(prev_pane):
            closed = 'closed'
    base = f'{read(store, "slug")}-master'
    if (name != base or moved or adopted) and m.rename(pane, base):
        name = base

    def mutate(doc):
        master = member(doc, 'master')
        master['name'] = name
        master.pop('previous', None)

    update(store, mutate)
    m.retire(m.thread_of_pane(prev_pane), prev, store)
    archive_retired(ctx, store, 'take-master has run')
    event(
        store,
        'master',
        'takeover',
        '-',
        f'generation:{field(store, "master", "generation")}',
    )
    out = sys.stdout
    if moved and closed == 'closed':
        out.write(f'took over from {prev}, whose session was {prev_pane}\n')
    elif closed == 'closed':
        out.write(f'closed the old master {prev} ({prev_pane})\n')
    elif closed == 'same':
        out.write(f'took over from {prev} in the same place ({pane})\n')
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
    publish_master(store)
    warn_unreviewed(store)
    return 0
