import os
import subprocess

from ..dispatch import fresh_per_brief, take_queued
from ..errors import Die, Exit
from ..herdr import agent_status
from ..review import jq_raw
from ..store import alt, event, expected_report, header_field, open_steers, pair_json, paused_reason, steer_number, unit_name, update
from .status import latest

MISSING_PAIR = 'pair.sh: no pair.json in {store}; run: pair.sh init <slug>\njq: error: Could not open file : No such file or directory\n'


def in_herdr():
    if os.environ.get('HERDR_ENV') != '1':
        raise Die('not running inside Herdr (HERDR_ENV != 1)')


def readlink_f(path):
    """readlink -f: every component but the last must exist. Ends the command silently with 1 when it does not."""
    if path == '':
        raise Exit(1)
    resolved = os.path.realpath(path)
    if not os.path.isdir(os.path.dirname(resolved)):
        raise Exit(1)
    return resolved


def require_pair(store, times=1):
    """field() on a store without pair.json: pair_file's message, then jq's, and exit 2 from jq."""
    if not os.path.isfile(f'{store}/pair.json'):
        raise Exit(2, MISSING_PAIR.format(store=store) * times)


def section(pair, name):
    """pair.<name> as a dict, an empty one when it is null or not an object."""
    value = pair.get(name)
    return value if isinstance(value, dict) else {}


def report(ctx, args):
    if len(args) < 1:
        raise Die('usage: pair.sh report <store> [NNN]')
    path = latest(args[0], 'reports', args[1] if len(args) > 1 and args[1] else None)
    if path is None:
        raise Die('no report found', 4)
    print(path)
    return 0


def notify(ctx, args):
    in_herdr()
    if len(args) != 2:
        raise Die('usage: pair.sh notify <store> <report-or-advice-path>')
    store = args[0]
    file = readlink_f(args[1])
    if not os.path.isfile(file):
        raise Die(f'file not found: {file}')
    word = 'ADVICE' if file.startswith(f'{store}/advice/') else 'REPORT'
    event(store, 'consultant' if word == 'ADVICE' else 'sidekick', word.lower(), file, header_field(file, 'status'))
    require_pair(store)
    master = jq_raw(section(pair_json(store), 'master').get('name'))
    status = agent_status(master)
    if status in ('idle', 'done'):
        code = subprocess.run(['herdr', 'agent', 'prompt', master, f'{ctx.skill} {word} {file}'], stdout=subprocess.DEVNULL, check=False).returncode
        if code:
            return code
        print(f'prompted master {master} (was {status}) with {word} {file}')
    else:
        print(f'master {master} is {status}; left {file} for its next wait or status check')
    return 0


def next_brief(ctx, args):
    if len(args) != 1:
        raise Die('usage: pair.sh next <store>')
    store = args[0]
    pair = pair_json(store)
    kind = jq_raw(section(pair, 'sidekick').get('kind'))
    if fresh_per_brief(kind):
        raise Die(f'a {kind} sidekick leaves the queue for the master to dispatch after a fresh session', 5)
    if paused_reason(store) is not None:
        print('queue: paused')
        return 4
    brief = take_queued(store)
    if brief is None:
        print('queue: empty')
        return 4
    print(f'{ctx.skill} BRIEF {brief}')
    return 0


def finish(ctx, args):
    in_herdr()
    if len(args) != 2:
        raise Die('usage: pair.sh finish <store> <report-path>')
    store = args[0]
    report = readlink_f(args[1])
    if not os.path.isfile(report):
        raise Die(f'report not found: {report}; write it first')
    status = header_field(report, 'status')
    if not os.path.isfile(f'{store}/pair.json'):
        raise Exit(2, MISSING_PAIR.format(store=store) * (2 if status == 'done' else 1))
    pair = pair_json(store)
    dispatch = alt(pair.get('dispatch'), {})
    dispatched = alt(dispatch.get('brief') if isinstance(dispatch, dict) else None, '-')
    if status == 'done' and report == expected_report(store, jq_raw(dispatched)):
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
    pair = pair_json(store)
    sidekick = section(pair, 'sidekick')
    dispatch = alt(pair.get('dispatch'), {})
    current = jq_raw(alt(dispatch.get('brief') if isinstance(dispatch, dict) else None, ''))
    kind = jq_raw(sidekick.get('kind'))
    generation = jq_raw(alt(sidekick.get('generation'), 0))
    if (status == 'done' and fresh_per_brief(kind) and current and report == expected_report(store, current)
            and jq_raw(alt(dispatch.get('generation') if isinstance(dispatch, dict) else None, 0)) == generation):
        if jq_raw(alt(sidekick.get('rotation_required'), False)) != 'true':
            update(store, lambda doc: doc['sidekick'].__setitem__('rotation_required', True))
            event(store, 'sidekick', 'rotation-required', report, f'generation:{generation}')
    code = notify(ctx, [store, report])
    if code:
        return code
    for late in open_steers(store):
        if late:
            print(f'late steer: {late} arrived after this report; record pair.sh progress {store} "steer s{steer_number(late)} late: reported" '
                  'and the master folds it into the next brief')
    pair = pair_json(store)
    sidekick = section(pair, 'sidekick')
    if status == 'done' and fresh_per_brief(jq_raw(sidekick.get('kind'))):
        if jq_raw(alt(sidekick.get('rotation_required'), False)) == 'true':
            print(f'rotation: required before the next task; leave the queue for the master to dispatch after pair.sh rotate {store}')
        print(f'next: end the turn with the single line: {ctx.skill} REPORT {report}')
        return 0
    brief = take_queued(store) if status == 'done' and paused_reason(store) is None else None
    if brief is not None:
        print(f'next: the master queued {ctx.skill} BRIEF {brief}')
        print('Start that brief now, in this turn, as if the message had just arrived.')
    else:
        print(f'next: end the turn with the single line: {ctx.skill} REPORT {report}')
    return 0
