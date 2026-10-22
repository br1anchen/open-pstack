import os
import subprocess

from ..store import (
    Die, Exit, prog, event, missing_pair, expected_report, field, header_field, load, member, open_steers, peek, steer_number, text_at,
    unit_name, update,
)
from ..panes import agent_status
from ..wait import mark_report_seen, wait_owner_path
from .status import latest

# What a role runs finish on declares one of these: a report (done, partial, blocked, failed: report-template), a steer or plan objection
# (object: steer-response-template, plan-response-template), a plan agreement (agree: plan-response-template), a guided ask (asking:
# ask-template). Advice (answered) goes through notify, not finish.
FINISH_STATUSES = ('done', 'partial', 'blocked', 'failed', 'object', 'agree', 'asking')

def in_herdr():
    if os.environ.get('HERDR_ENV') != '1':
        raise Die('not running inside Herdr (HERDR_ENV != 1)')


def require_pair(store, times=1):
    """field() on a store without pair.json: pair_file's message, then jq's, and exit 2 from jq."""
    if not os.path.isfile(f'{store}/pair.json'):
        raise Exit(2, missing_pair(store) * times)


def report(ctx, args):
    if len(args) < 1:
        raise Die(f'usage: {prog()} report <store> [NNN]')
    path = latest(args[0], 'reports', args[1] if len(args) > 1 and args[1] else None)
    if path is None:
        raise Die('no report found', 4)
    print(path)
    return 0


def notify(ctx, args):
    in_herdr()
    if len(args) != 2:
        raise Die(f'usage: {prog()} notify <store> <report-or-advice-path>')
    store = os.path.realpath(args[0])
    file = os.path.realpath(args[1])
    if not os.path.isfile(file):
        raise Die(f'file not found: {file}')
    word = 'ADVICE' if file.startswith(f'{store}/advice/') else 'REPORT'
    require_pair(store)
    event(store, 'consultant' if word == 'ADVICE' else 'sidekick', word.lower(), file, header_field(file, 'status'))
    master = field(store, 'master', 'name')
    status = agent_status(master)
    if status in ('idle', 'done'):
        code = subprocess.run(['herdr', 'agent', 'prompt', master, f'{ctx.skill} {word} {file}'], stdout=subprocess.DEVNULL, check=False).returncode
        if code:
            return code
        print(f'prompted master {master} (was {status}) with {word} {file}')
        # The master has the message. A wait that owns the store (wait.pid present, whatever it holds) will return the report itself; with none the
        # next wait would return it again. A file a killed wait left behind costs one duplicate wake, never a missed report.
        if word == 'REPORT' and not os.path.exists(wait_owner_path(store)):
            mark_report_seen(store, file)
    else:
        print(f'master {master} is {status}; left {file} for its next wait or status check')
    return 0


def next_brief(ctx, args):
    if len(args) != 1:
        raise Die(f'usage: {prog()} next <store>')
    pair = load(args[0])
    kind = text_at(peek(pair, 'sidekick'), 'kind')
    raise Die(f'a {kind} sidekick leaves the queue for the master to dispatch after a fresh session', 5)


def finish(ctx, args):
    in_herdr()
    if len(args) != 2:
        raise Die(f'usage: {prog()} finish <store> <report-path>')
    store = os.path.realpath(args[0])
    report = os.path.realpath(args[1])
    if not os.path.isfile(report):
        raise Die(f'report not found: {report}; write it first')
    status = header_field(report, 'status')
    if status not in FINISH_STATUSES:
        raise Die(f'{report} has no valid status: line (one of {", ".join(FINISH_STATUSES)})', 2)
    if not os.path.isfile(f'{store}/pair.json'):
        raise Exit(2, missing_pair(store) * (2 if status == 'done' else 1))
    pair = load(store)
    dispatched = text_at(peek(pair, 'dispatch'), 'brief', '-')
    if status == 'done' and report == expected_report(store, dispatched):
        head = header_field(report, 'head')
        if head == '' or head.startswith('<'):
            raise Die(f'{os.path.basename(report)}: a done report needs the report template\'s header, with head: <the unit\'s commit> '
                      '(HEAD when the brief says commit: no); add it and run finish again', 1)
    if status == 'done':
        ready = ctx.variant.report_ready(ctx, store, report)
        if not ready.ready:
            if ready.open:
                raise Die(f'blocking review notes are still open on {unit_name(report)}:\n{ready.open}\n'
                          f'Resolve each as a fixup commit, record it with pair.sh step {store} <sha> <summary> --resolves <n-ids>, then finish again', 7)
            raise Die(f'{unit_name(report)}: {ready.why}; rewrite the report at the unit\'s last commit (head:), then finish again', 7)
    pair = load(store)
    sidekick, dispatch = peek(pair, 'sidekick'), peek(pair, 'dispatch')
    current = text_at(dispatch, 'brief')
    generation = text_at(sidekick, 'generation', '0')
    if status == 'done' and current and report == expected_report(store, current) and text_at(dispatch, 'generation', '0') == generation:
        if sidekick.get('rotation_required') is not True:
            update(store, lambda doc: member(doc, 'sidekick').__setitem__('rotation_required', True))
            event(store, 'sidekick', 'rotation-required', report, f'generation:{generation}')
    code = notify(ctx, [store, report])
    if code:
        return code
    for late in open_steers(store):
        if late:
            print(f'late steer: {late} arrived after this report; record pair.sh progress {store} "steer s{steer_number(late)} late: reported" '
                  'and the master folds it into the next brief')
    pair = load(store)
    if status == 'done' and peek(pair, 'sidekick').get('rotation_required') is True:
        print(f'rotation: required before the next task; leave the queue for the master to dispatch after pair.sh rotate {store}')
    print(f'next: end the turn with the single line: {ctx.skill} REPORT {report}')
    return 0


def pair_report_ready(ctx, args):
    """pair_report_ready store report: 0 when the pair's own checks let the report through, 1 when they withhold it. The base hook, not the
    variant's: kitchen.sh finish asks it before running its own Verify rerun."""
    from ..variant import Variant
    if len(args) != 2:
        raise Die('usage: pair_report_ready <store> <report>')
    return 0 if Variant().report_ready(ctx, args[0], args[1]).ready else 1
