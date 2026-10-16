import os

from ..errors import Die
from ..gates import check_scope, require_not_paused
from ..herdr import agent_status
from ..replies import queued_brief
from ..review import jq_raw, require_filled
from ..store import alt, event, pair_file, pair_json, readlink_f
from .report import section


def queue(ctx, args):
    """The master's next brief, held for the sidekick to take the moment its current report is written. One slot: more than one
    queued brief is a plan."""
    if len(args) < 2:
        raise Die('usage: pair.sh queue <store> <brief-path> [--replace] | pair.sh queue <store> --clear')
    store, brief = args[0], args[1]
    pair_file(store)
    if brief == '--clear':
        try:
            os.unlink(f'{store}/queue')
        except OSError:
            pass
        event(store, 'master', 'queue', '-', 'cleared')
        print('queue cleared')
        return 0
    replace = False
    option = args[2] if len(args) > 2 else ''
    if option == '--replace':
        replace = True
    elif option != '':
        raise Die(f'unknown option {option}')
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    brief = readlink_f(brief)
    require_not_paused(store)
    require_filled(ctx, brief)
    pair = pair_json(store)
    check_scope(brief, jq_raw(alt(pair.get('git_root'), pair.get('cwd'))))
    ctx.variant.require_agreed_plan(ctx, store, brief)
    queued = queued_brief(store)
    if queued and queued != brief and not replace:
        raise Die(f'the queue holds {queued}; pass --replace to swap it', 5)
    name = jq_raw(section(pair, 'sidekick').get('name'))
    status = agent_status(name)
    if status in ('idle', 'done'):
        raise Die(f'sidekick {name} is {status}; dispatch instead: pair.sh dispatch {store} {brief}', 5)
    if status != 'working':
        raise Die(f'sidekick {name} is {status}; resolve that before queueing', 3)
    with open(f'{store}/queue.tmp', 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(f'{brief}\n')
    os.replace(f'{store}/queue.tmp', f'{store}/queue')
    event(store, 'master', 'queue', brief)
    print(f'queued {brief}; the sidekick takes it when its current report is written')
    return 0
