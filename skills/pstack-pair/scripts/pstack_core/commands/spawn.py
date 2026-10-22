from ..errors import Die
from ..spawn import record_fallback, set_placement, spawn_sidekick
from ..store import field, pair_file, whole_number
from ..wait import die_with_the_shell
from .report import in_herdr

USAGE = ('usage: pair.sh spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--direction right|down] [--pane ID] [--timeout MS] '
         '[-- agent-args...]')
VALUED = {'--kind': 'kind', '--fallback': 'fallback', '--permission': 'permission', '--direction': 'direction', '--pane': 'pane', '--timeout': 'timeout'}


def spawn(ctx, args):
    """Starts the sidekick (or its recorded fallback) in a pane: 0, 3 (the bootstrap did not settle) or 4 (it settled without the ready file)."""
    die_with_the_shell()
    in_herdr()
    if not args:
        raise Die(USAGE)
    store, rest = args[0], list(args[1:])
    opts = {'kind': '', 'direction': '', 'pane': '', 'timeout': '60000', 'permission': '', 'fallback': '', 'placement': ''}
    fallback_args = []
    while rest:
        flag = rest[0]
        if flag in VALUED or flag == '--fallback-arg':
            if len(rest) < 2:
                raise Die(f'{flag} needs a value')
            if flag == '--fallback-arg':
                fallback_args.append(rest[1])
            else:
                opts[VALUED[flag]] = rest[1]
            rest = rest[2:]
        elif flag in ('--tab', '--split'):
            opts['placement'], rest = flag[2:], rest[1:]
        elif flag == '--':
            rest = rest[1:]
            break
        else:
            raise Die(f'unknown option {flag}')
    whole_number(opts['timeout'], '--timeout', 'milliseconds')
    if not opts['kind']:
        raise Die('--kind is required (run: herdr agent, for the kind list)')
    pair_file(store)
    set_placement(store, opts['placement'])
    record_fallback(store, opts['fallback'], fallback_args)
    return spawn_sidekick(ctx, store, opts['kind'], opts['permission'], field(store, 'master', 'pane_id'), opts['timeout'], opts['direction'], opts['pane'], rest)
