"""The consultant's commands, shared by pstack-trio and pstack-kitchen: a plan round that reaches the sidekick and the consultant, and the
consult files (new-consult, consult, advice). kitchen_core.dispatch wraps discuss_with_consultant for escalated plans (one wake) and the trio
registers it as its own discuss."""

import glob
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from . import argv, git
from .commands.rotate import rotate_if_due
from .commands.status import latest
from .errors import AgentBlocked, AgentBusy, AgentNotLive, Die, PromptFailed, Unfilled
from .gates import require_not_paused
from .panes import (
    DEVNULL,
    agent_prompt,
    agent_state,
    agent_status,
    await_file,
    error_code,
    error_state,
    require_herdr,
)
from .review import require_filled
from .send import require_fresh_session, submit_typed
from .store import (
    TEXT,
    event,
    field,
    fill_template,
    header_field,
    newest_first,
    now,
    pair_file,
    read,
    read_text,
    update,
    wake,
    who,
    write_text,
)
from .wait import TIMEOUT_ONLY, end_with_parent, timeout_ms

CONSULT_KINDS = ('design', 'finding', 'objection', 'review', 'glance')


def plan_answer(store, role, seq, plan):
    """The role's answer to this plan round: its latest report or advice for the plan's number, when it was written after the plan file; else ''."""
    if role == 'sidekick':
        reports = seq_files_newest_first(store, 'reports', seq)
        found = reports[0] if reports else None
    else:
        found = latest_plan_advice(store, seq)
    if (
        found
        and os.path.exists(found)
        and (
            not os.path.exists(plan)
            or os.stat(found).st_mtime_ns > os.stat(plan).st_mtime_ns
        )
    ):
        return found
    return ''


def advice_is_stop(path):
    """Whether an advice file is the consultant leaving, not an answer: its kind: is stop, or, with no kind: line, it is named *-stop.md."""
    kind = header_field(path, 'kind')
    return kind == 'stop' if kind else path.endswith('-stop.md')


def latest_plan_advice(store, seq):
    """The newest advice for NNN that is not a stop (ls -t order), or None."""
    for path in seq_files_newest_first(store, 'advice', seq):
        if not advice_is_stop(path):
            return path
    return None


def seq_files_newest_first(store, directory, seq):
    """The store's DIRECTORY/SEQ-*.md paths, newest first, spelled from the store as given (a trailing slash keeps its double slash)."""
    paths = glob.glob(f'{glob.escape(store)}/{directory}/{glob.escape(seq)}-*.md')
    return newest_first(paths)


def deliver_plan(ctx, store, role, name, seq, plan):
    """A plan prompt must be seen working: an agent that only just went idle (the last turn of its bootstrap) can drop it whole. One never seen
    working, with nothing typed and no answer, still idle, gets the plan once more."""
    text = f'{ctx.skill} PLAN {plan}'
    sys.stdout.flush()
    r = agent_prompt(
        name, text, wait=True, until='working', timeout_ms=30000, stdout=DEVNULL
    )
    took = 1 if r.returncode == 0 else 0
    if not took:
        err = r.stderr
        code = error_code(err)
        if code not in ('timeout', 'agent_prompt_stalled'):
            sys.stderr.write(err)
            raise PromptFailed(f'herdr prompt failed for {name}')
    submitted = submit_typed(name, os.path.basename(plan))
    if took or submitted or plan_answer(store, role, seq, plan):
        return
    if agent_status(name) in ('idle', 'done'):
        event(store, 'master', 'plan-redeliver', plan, role)
        sys.stdout.write(
            f'redelivered: {role} {name} sat idle without the plan; sent it again\n'
        )
        sys.stdout.flush()
        agent_prompt(
            name,
            text,
            wait=True,
            until='working',
            timeout_ms=30000,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        submit_typed(name, os.path.basename(plan))


@dataclass(frozen=True)
class PlanRound:
    """Who a plan round reaches: the roles to send the plan now, the roles to wait on (those sent and those that had already answered), those that
    had answered, and the sidekick when it is busy on a unit and its half is deferred."""

    send: tuple
    wait_on: tuple
    answered: tuple
    deferred: str


def plan_round_for(store, seq, plan):
    """The PlanRound of a plan: a role whose answer is newer than the plan file is not asked again, an idle or done role gets the plan, a role that is
    absent or blocked ends the round, and a busy sidekick is deferred while a busy consultant ends it."""
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
            raise AgentNotLive(
                f'{who(role, name)} is not live; run: pair.sh spawn {store} --only {role} --{role} <kind>'
            )
        elif status == 'blocked':
            raise AgentBlocked(
                f'{who(role, name)} is blocked; inspect: herdr agent read {name} --source visible --lines 60'
            )
        else:
            if role != 'sidekick':
                raise AgentBusy(
                    f'{who(role, name)} is {status}; wait for it before a plan round'
                )
            deferred = 'sidekick'
    if deferred and 'consultant' not in send and 'consultant' not in answered:
        raise AgentBusy(
            f'sidekick {field(store, "sidekick", "name")} is working and the consultant has nothing to answer; wait for the sidekick before a plan round'
        )
    return PlanRound(tuple(send), tuple(wait_on), tuple(answered), deferred)


def send_plan(ctx, store, plan, seq, round_):
    """Sends the plan to each role of the round that has not answered it; their answers are waited on with the roles that had."""
    wait_on = list(round_.wait_on)
    if 'sidekick' in round_.send:
        require_fresh_session(store)
    for role in round_.send:
        name = field(store, role, 'name')
        deliver_plan(ctx, store, role, name, seq, plan)
        event(store, 'master', 'send-plan', plan, role)
        sys.stdout.write(f'sent PLAN {os.path.basename(plan)} to {role} {name}\n')
        wait_on.append(role)
    return wait_on


def await_answer(store, role, seq, slug, plan, timeout):
    """(answer file, state) of a role the plan was sent to now: the wait for its agent to settle, then the file; a role that settled without a response
    is polled for its file. An empty answer is a missing one."""
    name = field(store, role, 'name')
    sys.stdout.flush()
    r = subprocess.run(
        ['herdr', 'agent', 'wait', name, '--timeout', str(timeout)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        **TEXT,
    )
    status = agent_state(r.stdout, 'settled')
    if r.returncode != 0:
        status = agent_status(name)
    found = plan_answer(store, role, seq, plan)
    if not found and status != 'blocked':
        sys.stdout.write(f'{role} settled without a response; polling for the file\n')
        sys.stdout.flush()
        reply = (
            f'{store}/reports/{seq}-{slug}.md'
            if role == 'sidekick'
            else f'{store}/advice/{seq}-{slug}.md'
        )
        if not os.path.isfile(reply):
            await_file(reply, timeout)
        found = plan_answer(store, role, seq, plan)
    return found, status


def discuss_with_consultant(ctx, args, one_wake=False):
    """A plan round goes to the sidekick and the consultant at once; each answers in its own file and the master merges. A sidekick busy on a unit
    does not hold the round up: the consultant gets the plan now and the sidekick's half waits (exit 4, sidekick_response: deferred) until discuss
    runs again once it reports. A role whose answer is newer than the plan file is not asked again, so the second discuss sends only the deferred
    half and returns both answers. Returns the exit status."""
    write = sys.stdout.write
    require_herdr()
    if len(args) < 2:
        raise Die(f'usage: {ctx.prog} discuss <store> <plan-path> [--timeout MS]')
    store, plan, timeout = args[0], args[1], 540000
    opts = argv.read(args[2:], TIMEOUT_ONLY)
    timeout = timeout_ms(opts, timeout)
    opts.refuse_leftovers()
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    plan = os.path.realpath(plan)
    end_with_parent()
    rotate_if_due(ctx, store, 'plan')
    require_not_paused(store)
    require_filled(ctx, plan)
    base = os.path.basename(plan)
    seq, slug = base[:3], base[:-3][4:] if base.endswith('.md') else base[4:]
    round_ = plan_round_for(store, seq, plan)
    wait_on = send_plan(ctx, store, plan, seq, round_)
    rc, woke = 0, []
    for role in ('sidekick', 'consultant'):
        if role == round_.deferred:
            write(
                'sidekick_response: deferred (it is working on a unit); run discuss again once it reports, to send it this plan\n'
            )
            event(store, 'master', 'defer-plan', plan, 'sidekick')
            rc = 4
            continue
        if role not in wait_on:
            continue
        # Only a role that had answered before this call skips the wait; one sent the plan now is waited on, however fast its answer lands.
        if role in round_.answered:
            found, status = plan_answer(store, role, seq, plan), 'answered'
        else:
            found, status = await_answer(store, role, seq, slug, plan, timeout)
        write(f'{role}_state: {status}\n')
        if found:
            write(f'{role}_response: {found}\n')
            write(f'{role}_status: {header_field(found, "status")}\n')
            if status != 'answered':
                if one_wake:
                    woke.append(f'{role}:{header_field(found, "status")}')
                else:
                    wake(ctx, store, plan, f'{role}:{header_field(found, "status")}')
        else:
            write(f'{role}_response: missing\n')
            rc = 4
        if status == 'blocked':
            rc = 3
    if woke:
        wake(ctx, store, plan, ','.join(woke))
    return rc


def new_consult(ctx, args):
    """Consults on a unit are capped at three; a fourth means the design is wrong and belongs in a plan round. A glance, the short diff read at a
    check-in, sits outside that cap, one per unit."""
    if len(args) < 2:
        raise Die(
            f'usage: {ctx.prog} new-consult <store> <NNN> --kind design|finding|objection|review|glance [--force]'
        )
    store, seq = args[0], args[1]
    opts = argv.read(args[2:], argv.Spec(valued=('--kind',), flags=('--force',)))
    kind, force = opts.last('--kind', ''), opts.present('--force')
    pair_file(store)
    if kind not in CONSULT_KINDS:
        raise Die('--kind must be design, finding, objection, review, or glance')
    base = glob.escape(store)
    units = sorted(glob.glob(f'{base}/briefs/{glob.escape(seq)}-*.md')) + sorted(
        glob.glob(f'{base}/plans/{glob.escape(seq)}-*.md')
    )
    if not units:
        raise Die(f'no plan or brief for unit {seq}')
    unit = units[0]
    slug = os.path.basename(unit)[4:-3]
    glances = counted = 0
    for consult in sorted(
        glob.glob(f'{base}/consults/{glob.escape(seq)}-{glob.escape(slug)}-c[0-9]*.md')
    ):
        if os.path.exists(consult):
            if header_field(consult, 'kind') == 'glance':
                glances += 1
            else:
                counted += 1
    if kind == 'glance':
        if glances and not force:
            raise Die(
                f'unit {seq} already had its glance; send a finding consult, or pass --force',
                7,
            )
    elif counted >= 3 and not force:
        raise Die(
            f'unit {seq} already has three consults; take the question to a plan round, or pass --force',
            7,
        )
    os.makedirs(f'{store}/consults', exist_ok=True)
    k = glances + counted + 1
    path = f'{store}/consults/{seq}-{slug}-c{k}.md'
    text = fill_template(
        f'{ctx.skill_root}/references/consult-template.md',
        {
            'SEQ': seq,
            'SLUG': slug,
            'K': str(k),
            'STORE': store,
            'KIND': kind,
            'UNIT': unit,
        },
    )
    if kind == 'glance':
        text = re.sub(r'^timebox: .*', 'timebox: 5', text, flags=re.M)
    write_text(path, text)
    print(path)
    return 0


def stamp_send_state(store, path):
    """Writes the one header line the script owns into the consult file: the sidekick's state and the head at send time, so the consultant knows
    how live the tree it reads is. A file with a placeholder left in it is refused after the write. Returns (the store's cwd, the sidekick's state)."""
    sidekick_state = agent_status(field(store, 'sidekick', 'name'))
    cwd = read(store, 'cwd')
    state_line = f'sidekick state at send: {sidekick_state} at {git.out(cwd, "rev-parse", "--short", "HEAD") or "?"}'
    text = re.sub(
        r'^sidekick state at send:.*', lambda _: state_line, read_text(path), flags=re.M
    )
    write_text(path, text)
    if '{{' in text or re.search(r'^kind: .*\|', text, re.M):
        raise Unfilled(f'{path} still has unfilled placeholders')
    return cwd, sidekick_state


def require_consultant_idle(store):
    """The consultant's name, once it is live and idle or done; a consult to one that is absent, blocked or working is refused with its own exit code."""
    name = field(store, 'consultant', 'name')
    status = agent_status(name)
    if status == 'absent':
        raise AgentNotLive(
            f'{who("consultant", name)} is not live; run: pair.sh spawn {store} --only consultant --consultant <kind>'
        )
    if status == 'blocked':
        raise AgentBlocked(
            f'{who("consultant", name)} is blocked; inspect: herdr agent read {name} --source visible --lines 60'
        )
    if status not in ('idle', 'done'):
        raise AgentBusy(
            f'{who("consultant", name)} is {status}; wait for its current advice first'
        )
    return name


def consult(ctx, args):
    """A consult reaches the consultant at any time, the sidekick working or not. The header records the sidekick's state and HEAD at send; the
    consultant reads committed work at that head, or a scratch snapshot, never a tree that moves under it."""
    require_herdr()
    if len(args) < 2:
        raise Die(f'usage: {ctx.prog} consult <store> <consult-path> [--timeout MS]')
    store, path = args[0], os.path.realpath(args[1])
    opts = argv.read(args[2:], TIMEOUT_ONLY)
    timeout = timeout_ms(opts, 900000)
    opts.refuse_leftovers()
    end_with_parent()
    if not os.path.isfile(path):
        raise Die(f'consult not found: {path}')
    require_not_paused(store)
    if not re.search(r'^## Question', read_text(path), re.M):
        raise Die(f'not a consult file: {path}')
    named = re.fullmatch(r'(\d{3})-(.+)-c(\d+)\.md', os.path.basename(path))
    if not named:
        raise Die(
            f'{path}: the file name must be NNN-<slug>-c<k>.md, as new-consult makes it'
        )
    seq, slug, k = named.groups()
    kind = header_field(path, 'kind')
    cwd, sidekick_state = stamp_send_state(store, path)
    name = require_consultant_idle(store)
    head = git.out(cwd, 'rev-parse', 'HEAD') or ''
    update(
        store,
        lambda doc: doc.update(
            consult={
                'file': path,
                'at': now(),
                'head': head,
                'sidekick_state': sidekick_state,
            }
        ),
    )
    event(store, 'master', 'send-consult', path, kind)
    sys.stdout.flush()
    r = agent_prompt(name, f'{ctx.skill} CONSULT {path}', wait=True, timeout_ms=timeout)
    out, err = r.stdout, r.stderr
    if r.returncode != 0:
        status = error_state(err)
        sys.stderr.write(err.rstrip('\n') + '\n')
    else:
        status = agent_state(out, 'settled')
    print(f'state: {status}')
    advice = f'{store}/advice/{seq}-{slug}-c{k}.md'
    if not os.path.isfile(advice) and status != 'blocked':
        print('consultant settled without advice; polling for the file')
        sys.stdout.flush()
        await_file(advice, timeout)
    if os.path.isfile(advice):
        print(f'advice: {advice}')
        print(f'advice_status: {header_field(advice, "status")}')
        wake(ctx, store, advice, f'advice:{header_field(advice, "status")}')
        if status == 'blocked':
            return 3
        return 0
    if status == 'blocked':
        print('advice: missing')
        return 3
    print('advice: missing')
    return 4


def advice(ctx, args):
    if not args:
        raise Die(f'usage: {ctx.prog} advice <store> [NNN]')
    found = latest(args[0], 'advice', args[1] if len(args) > 1 else None)
    if not found:
        raise Die('no advice found', 4)
    print(found)
    return 0
