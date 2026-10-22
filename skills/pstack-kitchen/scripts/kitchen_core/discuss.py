"""queue and discuss: the master's next brief held for the sidekick, and a plan round with it (and with the consultant for an escalated plan).
Ported from kitchen.sh at b632713 (cmd_queue, cmd_discuss, rotate_if_due) and from pstack-trio's consultant-core.sh (discuss_with_consultant,
deliver_plan, latest_plan_advice, plan_answer). The trio keeps its bash copy of those: its pair.sh still runs it.

The bash wrappers called the core's queue and discuss through pycore; here they call the core functions directly."""

import glob
import os
import subprocess
import sys

from pstack_core.collate import collate_key
from pstack_core.commands import queue as core_queue
from pstack_core.commands import rotate as rotate_command
from pstack_core.commands import send as core_send
from pstack_core.commands.report import in_herdr
from pstack_core.dispatch import fresh_per_brief
from pstack_core.errors import Die, Exit
from pstack_core.gates import require_not_paused
from pstack_core.herdr import agent_status
from pstack_core.panes import await_file
from pstack_core.review import require_filled
from pstack_core.send import require_fresh_session, submit_typed
from pstack_core.store import event, field, header_field, readlink_f, whole_number
from pstack_core.wait import agent_state, end_with_parent, error_code

from .classify import classify_brief, risk_of
from .landing import is_landing
from .units import has_pair
from .verify import in_subshell


def devnull_stdout(call):
    """`call >/dev/null`: stdout of this process and of what it starts goes nowhere for the call; stderr stays."""
    sys.stdout.flush()
    saved = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        return call()
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)
        os.close(null)


def queue(ctx, args):
    args = list(args)
    # A brief path may be given relative to the store (briefs/NNN-x.md), as for dispatch.
    if len(args) >= 2 and not has_pair(args[0]):
        raise Exit(1)
    if len(args) >= 2 and args[1] != '--clear' and not os.path.isfile(args[1]) and os.path.isfile(f'{args[0]}/{args[1]}'):
        args[1] = f'{args[0]}/{args[1]}'
    if len(args) >= 2 and args[1] != '--clear' and os.path.isfile(args[1]) and not is_landing(args[1]):
        devnull_stdout(lambda: classify_brief(args[0], args[1]))
    return core_queue.queue(ctx, args)


def rotate_if_due(ctx, store, what):
    """A sidekick that starts fresh per brief (pi, prime-agent, devin) is rotated before the next brief or plan once its last unit is done, so
    the rotation costs the master no extra wake. Partial or blocked work never sets rotation_required and keeps its session."""
    if not (fresh_per_brief(field(store, 'sidekick', 'kind')) and field(store, 'sidekick', 'rotation_required', default='false') == 'true'):
        return
    # The report lands a moment before the sidekick's turn ends.
    subprocess.run(['herdr', 'agent', 'wait', field(store, 'sidekick', 'name'), '--until', 'idle', '--until', 'done', '--timeout', '60000'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def rotate_in_the_substitution():
        code = rotate_command.rotate(ctx, [store])
        if code:
            raise Exit(code)

    rc, out = in_subshell(rotate_in_the_substitution)
    if rc != 0:
        raise Die(f'rotating the sidekick before this {what} failed:\n{out}', rc)
    sys.stdout.write(f"rotated: the {field(store, 'sidekick', 'kind')} sidekick starts this {what} in a fresh session\n")


def discuss(ctx, args):
    if len(args) < 2:
        raise Die('usage: kitchen.sh discuss <store> <plan-path> [--timeout MS]')
    store, plan = args[0], args[1]
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    if risk_of(plan) not in ('routine', 'escalated'):
        raise Die(f'{plan}: risk: must start with routine or escalated, as kitchen.sh classify prints it', 2)
    escalated = risk_of(plan) == 'escalated'
    if escalated and not (has_pair(store) and agent_status(field(store, 'consultant', 'name')) != 'absent'):
        raise Die(f'an escalated plan goes to the consultant too; start it: kitchen.sh consultant {store} --reason TEXT', 5)
    if not os.path.isfile(f'{store}/pair.json'):
        return core_send.discuss(ctx, list(args))
    rotate_if_due(ctx, store, 'plan')
    if escalated:
        return discuss_with_consultant(ctx, args, one_wake=True)
    return core_send.discuss(ctx, list(args))


def plan_answer(store, role, seq, plan):
    """The role's answer to this plan round: its latest report or advice for the plan's number, when it was written after the plan file; else ''."""
    if role == 'sidekick':
        reports = newest_first(store, 'reports', seq)
        found = reports[0] if reports else None
    else:
        found = latest_plan_advice(store, seq)
    if found and os.path.exists(found) and (not os.path.exists(plan) or os.stat(found).st_mtime_ns > os.stat(plan).st_mtime_ns):
        return found
    return ''


def advice_is_stop(path):
    """Whether an advice file is the consultant leaving, not an answer: its kind: is stop, or, with no kind: line, it is named *-stop.md."""
    kind = header_field(path, 'kind')
    return kind == 'stop' if kind else path.endswith('-stop.md')


def latest_plan_advice(store, seq):
    """The newest advice for NNN that is not a stop (ls -t order), or None."""
    key = collate_key()
    for path in newest_first(store, 'advice', seq):
        if not advice_is_stop(path):
            return path
    return None


def newest_first(store, directory, seq):
    """`ls -t "$store"/DIRECTORY/SEQ-*.md`: the paths spelled as the bash spells them (a store given with a trailing slash keeps its double slash),
    newest first, ties by collated name."""
    key = collate_key()
    paths = glob.glob(f'{glob.escape(store)}/{directory}/{glob.escape(seq)}-*.md')
    return sorted(paths, key=lambda p: (-os.lstat(p).st_mtime_ns, key(os.path.basename(p))))


def deliver_plan(ctx, store, role, name, seq, plan):
    """A plan prompt must be seen working: an agent that only just went idle (the last turn of its bootstrap) can drop it whole. One never seen
    working, with nothing typed and no answer, still idle, gets the plan once more."""
    text = f'{ctx.skill} PLAN {plan}'
    sys.stdout.flush()
    r = subprocess.run(['herdr', 'agent', 'prompt', name, text, '--wait', '--until', 'working', '--timeout', '30000'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
    took = 1 if r.returncode == 0 else 0
    if not took:
        err = r.stderr.decode('utf-8', 'surrogateescape')
        code = error_code(err)
        if code not in ('timeout', 'agent_prompt_stalled'):
            sys.stderr.write(err)
            raise Die(f'herdr prompt failed for {name}', 2)
    submitted = submit_typed(name, os.path.basename(plan))
    if took or submitted or plan_answer(store, role, seq, plan):
        return
    if agent_status(name) in ('idle', 'done'):
        event(store, 'master', 'plan-redeliver', plan, role)
        sys.stdout.write(f'redelivered: {role} {name} sat idle without the plan; sent it again\n')
        sys.stdout.flush()
        subprocess.run(['herdr', 'agent', 'prompt', name, text, '--wait', '--until', 'working', '--timeout', '30000'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        submit_typed(name, os.path.basename(plan))


def discuss_with_consultant(ctx, args, one_wake=False):
    """A plan round goes to the sidekick and the consultant at once; each answers in its own file and the master merges. A sidekick busy on a unit
    does not hold the round up: the consultant gets the plan now and the sidekick's half waits (exit 4, sidekick_response: deferred) until discuss
    runs again once it reports. A role whose answer is newer than the plan file is not asked again, so the second discuss sends only the deferred
    half and returns both answers. Returns the exit status."""
    write = sys.stdout.write
    in_herdr()
    if len(args) < 2:
        raise Die('usage: pair.sh discuss <store> <plan-path> [--timeout MS]')
    store, plan, timeout = args[0], args[1], 540000
    rest = list(args[2:])
    while rest:
        if rest[0] == '--timeout':
            if len(rest) < 2:
                raise Die('--timeout needs a value')
            timeout = whole_number(rest[1], '--timeout', 'milliseconds')
        elif rest[0] == '--every':
            if len(rest) < 2:
                raise Die('--every needs a value')
            timeout = whole_number(rest[1], '--every', 'minutes') * 60000
        else:
            raise Die(f'unknown option {rest[0]}')
        rest = rest[2:]
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    plan = readlink_f(plan)
    end_with_parent()
    require_not_paused(store)
    require_filled(ctx, plan)
    base = os.path.basename(plan)
    seq, slug = base[:3], base[:-3][4:] if base.endswith('.md') else base[4:]
    send, wait_on, deferred, answered = [], [], '', []
    for role in ('sidekick', 'consultant'):
        name = field(store, role, 'name')
        if plan_answer(store, role, seq, plan):
            answered.append(role)
            wait_on.append(role)
            continue
        status = agent_status(name)
        if status in ('idle', 'done'):
            send.append(role)
        elif status == 'absent':
            raise Die(f'{role} {name} is not live; run: pair.sh spawn {store} --only {role} --{role} <kind>', 2)
        elif status == 'blocked':
            raise Die(f'{role} {name} is blocked; inspect: herdr agent read {name} --source visible --lines 60', 3)
        else:
            if role != 'sidekick':
                raise Die(f'{role} {name} is {status}; wait for it before a plan round', 5)
            deferred = 'sidekick'
    if deferred and 'consultant' not in send and 'consultant' not in answered:
        raise Die(f"sidekick {field(store, 'sidekick', 'name')} is working and the consultant has nothing to answer; wait for the sidekick before a plan round", 5)
    if 'sidekick' in send:
        require_fresh_session(store)
    for role in send:
        name = field(store, role, 'name')
        deliver_plan(ctx, store, role, name, seq, plan)
        event(store, 'master', 'send-plan', plan, role)
        write(f'sent PLAN {base} to {role} {name}\n')
        wait_on.append(role)
    rc, woke = 0, []
    for role in ('sidekick', 'consultant'):
        if role == deferred:
            write('sidekick_response: deferred (it is working on a unit); run discuss again once it reports, to send it this plan\n')
            event(store, 'master', 'defer-plan', plan, 'sidekick')
            rc = 4
            continue
        if role not in wait_on:
            continue
        name = field(store, role, 'name')
        # Only a role that had answered before this call skips the wait; one sent the plan now is waited on, however fast its answer lands.
        found, status = '', 'answered'
        if role in answered:
            found = plan_answer(store, role, seq, plan)
        else:
            sys.stdout.flush()
            r = subprocess.run(['herdr', 'agent', 'wait', name, '--timeout', str(timeout)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            status = agent_state(r.stdout.decode('utf-8', 'replace'), 'settled')
            if r.returncode != 0:
                status = agent_status(name)
            found = plan_answer(store, role, seq, plan)
            if not found and status != 'blocked':
                write(f'{role} settled without a response; polling for the file\n')
                sys.stdout.flush()
                reply = f'{store}/reports/{seq}-{slug}.md' if role == 'sidekick' else f'{store}/advice/{seq}-{slug}.md'
                if not os.path.isfile(reply):
                    await_file(reply, timeout)
                found = plan_answer(store, role, seq, plan)
        write(f'{role}_state: {status}\n')
        if found:
            write(f'{role}_response: {found}\n')
            write(f"{role}_status: {header_field(found, 'status')}\n")
            if status != 'answered':
                if one_wake:
                    woke.append(f"{role}:{header_field(found, 'status')}")
                else:
                    event(store, 'master', 'wake', plan, f"{role}:{header_field(found, 'status')}")
        else:
            write(f'{role}_response: missing\n')
            rc = 4
        if status == 'blocked':
            rc = 3
    if woke:
        event(store, 'master', 'wake', plan, ','.join(woke))
    return rc
