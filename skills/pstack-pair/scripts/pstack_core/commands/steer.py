import glob
import os
import re
import sys
import time

from ..store import Die, prog, event, field, read_text, header_field, member, names, newest, unit_name, update, who
from ..panes import agent_prompt, agent_status, DEVNULL, herdr_ok, require_herdr
from ..prime import prime_steer
from ..review import has_placeholder
from ..send import prompt_sidekick
from ..wait import end_with_parent, owned_wait, wait_opts
from .report import require_pair


def usage():
    return f'usage: {prog()} steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]'


def park_for_devin(store, path, key='notice_steers'):
    """The list `key` gains the path once, sorted: Devin's next pair.sh step, notes or progress prints it (a message sent mid-turn is parked as
    queued, and the Enter that would deliver it cancels the running command)."""
    def mutate(doc):
        doc[key] = sorted(set(names(doc, key) + [path]))
    update(store, mutate)


def leave_for_devin(store, steer):
    """.notice_steers gains the steer once, sorted: Devin's next pair.sh step, notes or progress prints it."""
    park_for_devin(store, steer)


def open_the_objection_round(store, epoch):
    """The reply to this steer is any report on the unit written from now: .sent.epoch = now and the seen mark gone."""
    def mutate(doc):
        sent = member(doc, 'sent')
        sent['epoch'] = epoch
        sent.pop('seen', None)
    update(store, mutate)


def steer(ctx, args):
    require_herdr()
    end_with_parent()
    if len(args) < 2:
        raise Die(usage())
    store, steer_path = args[0], os.path.realpath(args[1])
    timeout, interval, _, rest = wait_opts(args[2:])
    force = interrupt = False
    for opt in rest:
        if opt == '--force':
            force = True
        elif opt == '--interrupt':
            interrupt = True
        else:
            raise Die(f'unknown option {opt}')
    if not os.path.isfile(steer_path):
        raise Die(f'steer not found: {steer_path}')
    try:
        text = read_text(steer_path)
    except OSError as e:
        sys.stderr.write(f'{prog()}: cannot read {steer_path}: {e.strerror}\n')
        raise Die(f'not a steer file: {steer_path}')
    if not re.search(r'(?m)^## Direction', text):
        raise Die(f'not a steer file: {steer_path}')
    if has_placeholder(text) or re.search(r'(?m)^(kind|scope effect): .*\|', text):
        raise Die(f'{steer_path} still has unfilled placeholders')
    # the forms steer-template.md describes, each optionally followed by ` \u2014 <note>`
    effect = header_field(steer_path, 'scope effect').rstrip().split(' \u2014 ')[0]
    if not re.fullmatch(r'none|may write: \S.*|may write "[^"]+"(?: and "[^"]+")*|must not write: \S.*', effect):
        raise Die(f'{steer_path}: scope effect must be none, "may write: <path or glob>" or "must not write: <path or glob>"')
    require_pair(store)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    message = f'{ctx.skill} STEER {steer_path}'
    if status == 'working':
        if interrupt:
            # Esc cancels the running tool call so the steer is read now, not after it.
            if not herdr_ok('agent', 'send-keys', name, 'esc'):
                raise Die(f'herdr send-keys failed for {name}', 2)
            if not herdr_ok('agent', 'wait', name, '--until', 'idle', '--until', 'done', '--until', 'blocked', '--timeout', '15000'):
                raise Die(f'{who("sidekick", name)} did not settle after esc; inspect: herdr agent read {name} --source visible --lines 60', 3)
        event(store, 'master', 'send-steer', steer_path, 'working')
        kind = field(store, 'sidekick', 'kind')
        if kind == 'devin' and not interrupt:
            # Devin parks a mid-turn message as queued, and the Enter that would deliver it cancels the running command. The steer stays in
            # the store; the sidekick's next step, notes, or progress prints it.
            leave_for_devin(store, steer_path)
            print(f"steer {steer_path} left for {name}: it lands at the sidekick's next pair.sh step, notes, or progress, between tool calls; "
                  'the ack lands in the progress log. Use --interrupt only to cancel the running command.')
        elif kind == 'prime-agent' and not interrupt:
            if not prime_steer(store, message):
                raise Die(f'Prime steer failed for {name}', 2)
            print(f'steered {name} with {steer_path}; Prime queues it between tool calls without cancelling the running command')
        else:
            if agent_prompt(name, message, stdout=DEVNULL, stderr=None).returncode != 0:
                raise Die(f'herdr prompt failed for {name}', 2)
            print(f'steered {name} with {steer_path}; its harness hands it over between tool calls and the ack lands in the progress log')
    elif status in ('idle', 'done'):
        seq = os.path.basename(steer_path)[:3]
        slug = re.sub(r'-s[0-9]+$', '', re.sub(r'^[0-9]{3}-', '', unit_name(steer_path)))
        objection = newest(glob.glob(os.path.join(glob.escape(store), 'reports', f'{glob.escape(seq)}-{glob.escape(slug)}-s[0-9]*.md')))
        if not objection or header_field(objection, 'status') != 'object':
            report = f'{store}/reports/{seq}-{slug}.md'
            unit_status = header_field(report, 'status') if os.path.isfile(report) else ''
            if unit_status in ('blocked', 'partial'):
                advice = f'apply its scope effect to the brief and re-dispatch it (dispatch {store} <brief> --send-only)'
            elif unit_status == 'done':
                # only the kitchen has verify --covers
                advice = f'write a fix brief and verify it with --covers {seq}' if ctx.skill == 'pstack-kitchen' else 'write a fix brief'
            else:
                advice = 'fold the steer into the next brief instead'
            raise Die(f'{who("sidekick", name)} is {status} and no objection is open on unit {seq}; {advice}', 5)
        if header_field(steer_path, 'supersedes') == 'none':
            raise Die(f'{steer_path} answers {objection} but its supersedes: line says none')
        event(store, 'master', 'send-steer', steer_path, 'objection')
        # The reply to this steer is any report on the unit written from now.
        open_the_objection_round(store, int(time.time()))
        prompt_sidekick(name, message)
        return owned_wait(ctx, store, name, timeout, interval)
    elif status == 'unknown':
        if not force:
            raise Die(f'{who("sidekick", name)} is unknown; read the pane, then pass --force to steer anyway', 5)
        if agent_prompt(name, message, stdout=DEVNULL, stderr=None).returncode != 0:
            raise Die(f'herdr prompt failed for {name}', 2)
        print(f'steered {name} with {steer_path}')
    elif status == 'absent':
        raise Die(f'{who("sidekick", name)} is not live', 2)
    elif status == 'blocked':
        raise Die(f'{who("sidekick", name)} is blocked; inspect: herdr agent read {name} --source visible --lines 60', 3)
    return 0
