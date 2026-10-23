import glob
import os
import re
import sys
import time

from ..checkin import live_steer_effects, sidekick_busy
from ..store import event, field, load, peek, text_at, read_text, header_field, member, names, newest, unit_name, update, who
from ..errors import BadSteer, Die, NoObjectionOpen, NotSettled, PromptFailed
from ..panes import agent_prompt, agent_status, DEVNULL, herdr_ok, require_herdr
from ..prime import prime_steer
from ..review import has_placeholder
from ..replies import file_mtime, sent_epoch
from ..send import prompt_sidekick, prune_pending, record_dispatch
from ..argv import Spec, read
from ..wait import TIMING, end_with_parent, print_outcome, timeout_ms, wait_for
from .report import require_pair


def usage(ctx):
    return f'usage: {ctx.prog} steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]'


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


def check_steer_file(ctx, steer_path):
    """A steer file must exist, be readable, carry a Direction, have no unfilled placeholders and name a scope effect in one of the forms
    steer-template.md describes."""
    if not os.path.isfile(steer_path):
        raise BadSteer(f'steer not found: {steer_path}')
    try:
        text = read_text(steer_path)
    except OSError as e:
        sys.stderr.write(f'{ctx.prog}: cannot read {steer_path}: {e.strerror}\n')
        raise BadSteer(f'not a steer file: {steer_path}')
    if not re.search(r'(?m)^## Direction', text):
        raise BadSteer(f'not a steer file: {steer_path}')
    if has_placeholder(text) or re.search(r'(?m)^(kind|scope effect): .*\|', text):
        raise BadSteer(f'{steer_path} still has unfilled placeholders')
    # the forms steer-template.md describes, each optionally followed by ` \u2014 <note>`
    effect = header_field(steer_path, 'scope effect').rstrip().split(' \u2014 ')[0]
    if not re.fullmatch(r'none|may write: \S.*|may write "[^"]+"(?: and "[^"]+")*|must not write: \S.*', effect):
        raise BadSteer(f'{steer_path}: scope effect must be none, "may write: <path or glob>" or "must not write: <path or glob>"')


def steer_working(store, name, steer_path, message, interrupt):
    """A steer for a sidekick in the middle of a turn: handed over between its tool calls, after an Esc with --interrupt. Devin parks a message sent
    mid-turn as queued and the Enter that would deliver it cancels the running command, so its steer stays in the store for its next pair.sh
    step, notes or progress; Prime queues it through its daemon."""
    if interrupt:
        # Esc cancels the running tool call so the steer is read now, not after it.
        if not herdr_ok('agent', 'send-keys', name, 'esc'):
            raise PromptFailed(f'herdr send-keys failed for {name}')
        if not herdr_ok('agent', 'wait', name, '--until', 'idle', '--until', 'done', '--until', 'blocked', '--timeout', '15000'):
            raise NotSettled(f'{who("sidekick", name)} did not settle after esc; inspect: herdr agent read {name} --source visible --lines 60')
    event(store, 'master', 'send-steer', steer_path, 'working')
    kind = field(store, 'sidekick', 'kind')
    if kind == 'devin' and not interrupt:
        leave_for_devin(store, steer_path)
        print(f"steer {steer_path} left for {name}: it lands at the sidekick's next pair.sh step, notes, or progress, between tool calls; "
              'the ack lands in the progress log. Use --interrupt only to cancel the running command.')
    elif kind == 'prime-agent' and not interrupt:
        if not prime_steer(store, message):
            raise PromptFailed(f'Prime steer failed for {name}')
        print(f'steered {name} with {steer_path}; Prime queues it between tool calls without cancelling the running command')
    else:
        if agent_prompt(name, message, stdout=DEVNULL, stderr=None).returncode != 0:
            raise PromptFailed(f'herdr prompt failed for {name}')
        print(f'steered {name} with {steer_path}; its harness hands it over between tool calls and the ack lands in the progress log')


def dispatched_unit(store):
    """The unit (NNN-slug) of the sidekick's current dispatch, or ''."""
    brief = text_at(peek(load(store), 'dispatch'), 'brief')
    return unit_name(brief) if brief else ''


def unit_report_status(store, report, current):
    """The status of the unit's report. For the dispatched unit a report older than .sent.epoch answers an earlier send (the rule replies.fresh_reply
    applies) and does not count, so after a resume the stale blocked report reads as none; an earlier unit's report is read as it stands."""
    if not os.path.isfile(report) or (current and file_mtime(report) < sent_epoch(store)):
        return ''
    return header_field(report, 'status')


def settled_route(ctx, store, name, status, steer_path):
    """How a steer reaches a sidekick that is between turns: 'objection' when the unit's newest `-s<k>` report has status object (the steer answers it),
    'deliver' when the unit is the dispatched one and has no report since the last send (nothing to fold the steer into, so the sidekick takes it
    now), 'resume' when it is the dispatched one and its report is blocked or partial (the steer re-sends the brief). Any other unit report refuses
    the steer with what to do instead, and a steer that answers an objection but supersedes nothing is refused too."""
    seq = os.path.basename(steer_path)[:3]
    slug = re.sub(r'-s[0-9]+$', '', re.sub(r'^[0-9]{3}-', '', unit_name(steer_path)))
    objection = newest(glob.glob(os.path.join(glob.escape(store), 'reports', f'{glob.escape(seq)}-{glob.escape(slug)}-s[0-9]*.md')))
    if not objection or header_field(objection, 'status') != 'object':
        current = dispatched_unit(store) == f'{seq}-{slug}'
        unit_status = unit_report_status(store, f'{store}/reports/{seq}-{slug}.md', current)
        if current and not unit_status:
            return 'deliver'
        if current and unit_status in ('blocked', 'partial'):
            return 'resume'
        if unit_status in ('blocked', 'partial'):
            advice = f'apply its scope effect to the brief and re-dispatch it (dispatch {store} <brief> --send-only)'
        elif unit_status == 'done':
            # only the kitchen has verify --covers
            advice = f'write a fix brief and verify it with --covers {seq}' if ctx.skill == 'pstack-kitchen' else 'write a fix brief'
        else:
            advice = 'fold the steer into the next brief instead'
        raise NoObjectionOpen(f'{who("sidekick", name)} is {status} and no objection is open on unit {seq}; {advice}')
    if header_field(steer_path, 'supersedes') == 'none':
        raise BadSteer(f'{steer_path} answers {objection} but its supersedes: line says none')
    return 'objection'


def apply_scope_effects(store, seq, brief):
    """The unit's live steers' scope effects written into the brief's Scope: each path becomes an entry under `may write:` or `must not write:`,
    unless that list already holds it. A heading the Scope section lacks is added at its end, and a brief with no Scope section gets one."""
    lines = read_text(brief).split('\n')
    for heading, paths in zip(('may write:', 'must not write:'), live_steer_effects(store, seq)):
        for path in paths:
            entry = f'- {path}'
            scope = next((i for i, l in enumerate(lines) if l.startswith('## Scope')), None)
            if scope is None:
                while lines and lines[-1] == '':
                    lines.pop()
                lines += ['', '## Scope']
                scope = len(lines) - 1
            end = next((i for i in range(scope + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
            start = next((i for i in range(scope + 1, end) if lines[i].startswith(heading)), None)
            if start is None:
                at = end
                while at > scope + 1 and lines[at - 1] == '':
                    at -= 1
                lines[at:at] = ['', heading, entry]
                continue
            last = start + 1
            while last < end and lines[last].startswith('- '):
                last += 1
            if entry not in lines[start + 1:last]:
                lines.insert(last, entry)
    with open(brief, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def resume_unit(store, name, steer_path, timeout, ctx):
    """A steer to a sidekick whose unit ended blocked or partial: the steer's scope effect goes into the brief and the brief is sent again, so the
    sidekick resumes it with the steer's Direction. record_dispatch keeps the unit's base (heads) and records the old report as seen, so only a report
    written after this send counts as the unit's answer."""
    seq = os.path.basename(steer_path)[:3]
    slug = re.sub(r'-s[0-9]+$', '', re.sub(r'^[0-9]{3}-', '', unit_name(steer_path)))
    brief = f'{store}/briefs/{seq}-{slug}.md'
    if not os.path.isfile(brief):
        raise NoObjectionOpen(f'{who("sidekick", name)} is idle and unit {seq} has no brief at {brief} to resume')
    apply_scope_effects(store, seq, brief)
    event(store, 'master', 'send-steer', steer_path, 'resume')
    prune_pending(store)
    record_dispatch(store, brief)
    prompt_sidekick(name, f'{ctx.skill} BRIEF {brief} resume after STEER {steer_path}')
    print(f'resumed unit {seq}: sent {brief} again after {steer_path}')
    return print_outcome(wait_for(ctx, store, timeout=timeout, name=name))


def steer(ctx, args):
    require_herdr()
    end_with_parent()
    if len(args) < 2:
        raise Die(usage(ctx))
    store, steer_path = args[0], os.path.realpath(args[1])
    opts = read(args[2:], Spec(valued=TIMING.valued, flags=(*TIMING.flags, '--force', '--interrupt'), unknown='collect'))
    timeout = timeout_ms(opts)
    opts.refuse_leftovers()
    force, interrupt = opts.present('--force'), opts.present('--interrupt')
    check_steer_file(ctx, steer_path)
    require_pair(ctx, store)
    name = field(store, 'sidekick', 'name')
    status = agent_status(name)
    message = f'{ctx.skill} STEER {steer_path}'
    if status == 'working':
        steer_working(store, name, steer_path, message, interrupt)
    elif status in ('idle', 'done'):
        route = settled_route(ctx, store, name, status, steer_path)
        if route == 'resume':
            return resume_unit(store, name, steer_path, timeout, ctx)
        if route == 'deliver':
            if sidekick_busy(store, status, time.time()):
                # the harness reports idle while a command it started since the dispatch still runs
                steer_working(store, name, steer_path, message, interrupt)
                return 0
            event(store, 'master', 'send-steer', steer_path, 'idle')
            prompt_sidekick(name, message)
            print(f'steered {name} with {steer_path}; unit {os.path.basename(steer_path)[:3]} has no report yet, so it reads the steer now')
            return 0
        event(store, 'master', 'send-steer', steer_path, 'objection')
        # The reply to this steer is any report on the unit written from now.
        open_the_objection_round(store, int(time.time()))
        prompt_sidekick(name, message)
        return print_outcome(wait_for(ctx, store, timeout=timeout, name=name))
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
