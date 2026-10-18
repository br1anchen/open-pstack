import glob
import os
import re
import subprocess
import sys
import time

from ..collate import newest
from ..errors import Die
from ..herdr import agent_status
from ..prime import prime_steer
from ..review import has_placeholder
from ..send import prompt_sidekick
from ..store import cut_chars, event, header_field, readlink_f, unit_name, update_or_jq
from ..wait import die_with_the_shell, owned_wait, wait_opts
from .report import in_herdr, require_pair
from .stop import role_value

USAGE = 'usage: pair.sh steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]'


def herdr_ok(*args):
    """herdr with stdout dropped and stderr left on the terminal, as `herdr ... >/dev/null` runs: whether it succeeded."""
    return subprocess.run(['herdr', *args], stdout=subprocess.DEVNULL, check=False).returncode == 0


def park_for_devin(store, path, key='notice_steers'):
    """The list `key` gains the path once, sorted: Devin's next pair.sh step, notes or progress prints it (a message sent mid-turn is parked as
    queued, and the Enter that would deliver it cancels the running command)."""
    def mutate(doc):
        current = doc.get(key)
        if current in (None, False):
            current = []
        if not isinstance(current, list):
            raise ValueError(f'{key} is not an array')
        doc[key] = sorted(set(current + [path]))
    update_or_jq(store, mutate, f'.{key} = ((.{key} // []) + [$s] | unique)', '--arg', 's', path)


def leave_for_devin(store, steer):
    """.notice_steers gains the steer once, sorted: Devin's next pair.sh step, notes or progress prints it."""
    park_for_devin(store, steer)


def open_the_objection_round(store, epoch):
    """The reply to this steer is any report on the unit written from now: .sent.epoch = now and the seen mark gone."""
    def mutate(doc):
        sent = doc.get('sent')
        if sent is None:
            sent = doc['sent'] = {}
        if not isinstance(sent, dict):
            raise ValueError('sent is not an object')
        sent['epoch'] = epoch
        sent.pop('seen', None)
    update_or_jq(store, mutate, '.sent.epoch = $epoch | del(.sent.seen)', '--argjson', 'epoch', str(epoch))


def steer(ctx, args):
    in_herdr()
    die_with_the_shell()
    if len(args) < 2:
        raise Die(USAGE)
    store, steer_path = args[0], readlink_f(args[1])
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
        with open(steer_path, 'rb') as f:
            text = f.read().decode('utf-8', 'surrogateescape')
    except OSError as e:
        # grep says why it could not read the file, then the check fails
        sys.stderr.write(f'grep: {steer_path}: {e.strerror}\n')
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
    name = role_value(store, 'sidekick', 'name')
    status = agent_status(name)
    message = f'{ctx.skill} STEER {steer_path}'
    if status == 'working':
        if interrupt:
            # Esc cancels the running tool call so the steer is read now, not after it.
            if not herdr_ok('agent', 'send-keys', name, 'esc'):
                raise Die(f'herdr send-keys failed for {name}', 2)
            if not herdr_ok('agent', 'wait', name, '--until', 'idle', '--until', 'done', '--until', 'blocked', '--timeout', '15000'):
                raise Die(f'sidekick {name} did not settle after esc; inspect: herdr agent read {name} --source visible --lines 60', 3)
        event(store, 'master', 'send-steer', steer_path, 'working')
        kind = role_value(store, 'sidekick', 'kind')
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
            if not herdr_ok('agent', 'prompt', name, message):
                raise Die(f'herdr prompt failed for {name}', 2)
            print(f'steered {name} with {steer_path}; its harness hands it over between tool calls and the ack lands in the progress log')
    elif status in ('idle', 'done'):
        seq = cut_chars(os.path.basename(steer_path), 3)
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
            raise Die(f'sidekick {name} is {status} and no objection is open on unit {seq}; {advice}', 5)
        if header_field(steer_path, 'supersedes') == 'none':
            raise Die(f'{steer_path} answers {objection} but its supersedes: line says none')
        event(store, 'master', 'send-steer', steer_path, 'objection')
        # The reply to this steer is any report on the unit written from now.
        open_the_objection_round(store, int(time.time()))
        prompt_sidekick(name, message)
        return owned_wait(ctx, store, name, timeout, interval)
    elif status == 'unknown':
        if not force:
            raise Die(f'sidekick {name} is unknown; read the pane, then pass --force to steer anyway', 5)
        if not herdr_ok('agent', 'prompt', name, message):
            raise Die(f'herdr prompt failed for {name}', 2)
        print(f'steered {name} with {steer_path}')
    elif status == 'absent':
        raise Die(f'sidekick {name} is not live', 2)
    elif status == 'blocked':
        raise Die(f'sidekick {name} is blocked; inspect: herdr agent read {name} --source visible --lines 60', 3)
    return 0
