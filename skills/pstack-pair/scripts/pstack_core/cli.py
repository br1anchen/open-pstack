import sys

from .commands import log, templates
from .context import Context
from .errors import Die

COMMANDS = {'log': log.run, 'new-plan': templates.new_plan, 'new-brief': templates.new_brief, 'new-steer': templates.new_steer}

OPTIONS = ('--skill', '--skill-root', '--state-root', '--store-label', '--roles', '--store-dirs', '--slug-max', '--unfilled-re')


def parse(argv):
    """The context options, then "--", then the command and its arguments."""
    opts, i = {}, 0
    while i < len(argv) and argv[i] != '--':
        if argv[i] not in OPTIONS or i + 1 >= len(argv):
            raise Die(f'bad core option: {argv[i]}')
        opts[argv[i][2:].replace('-', '_')] = argv[i + 1]
        i += 2
    if sorted(opts) != sorted(o[2:].replace('-', '_') for o in OPTIONS) or i >= len(argv) or i + 1 >= len(argv):
        raise Die('usage: pair.py <context options> -- <command> [args]')
    ctx = Context(opts['skill'], opts['skill_root'], opts['state_root'], opts['store_label'], tuple(opts['roles'].split()),
                  tuple(opts['store_dirs'].split()), int(opts['slug_max']), opts['unfilled_re'])
    return ctx, argv[i + 1], argv[i + 2:]


def main(argv):
    try:
        ctx, cmd, args = parse(argv)
        if cmd not in COMMANDS:
            raise Die(f'unknown command: {cmd}')
        return COMMANDS[cmd](ctx, args)
    except Die as e:
        sys.stderr.write(f'pair.sh: {e.message}\n')
        return e.code
