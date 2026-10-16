import os

from ..errors import Die, unbound_arg
from ..store import event, pair_file


def pause(ctx, args):
    if not args:
        raise Die('usage: pair.sh pause <store> [--reason TEXT]')
    store, rest = args[0], args[1:]
    reason = 'paused by the human'
    if rest and rest[0] == '--reason':
        if len(rest) < 2:
            raise unbound_arg()
        reason = rest[1]
    elif rest and rest[0] != '':
        raise Die(f'unknown option {rest[0]}')
    pair_file(store)
    with open(f'{store}/paused', 'wb') as f:
        f.write(f'{reason}\n'.encode('utf-8', 'surrogateescape'))
    event(store, 'master', 'pause', '-', reason)
    print(f'paused {store}: the sidekick stops at its next step or progress boundary, and dispatch, queue, discuss, '
          'consult, and answer refuse until pair.sh resume')
    return 0


def resume(ctx, args):
    if len(args) != 1:
        raise Die('usage: pair.sh resume <store>')
    store = args[0]
    pair_file(store)
    try:
        os.remove(f'{store}/paused')
    except FileNotFoundError:
        pass
    event(store, 'master', 'resume', '-')
    print(f'resumed {store}')
    return 0
