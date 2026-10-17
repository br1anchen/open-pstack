import sys

from . import agents, panes, permission, wait
from .commands import init, log, metrics, notes, pause, progress, queue, report, scratch, send, status, step, steer, stop, templates
from .context import Context
from .errors import Die, Exit

COMMANDS = {'init': init.run, 'log': log.run, 'new-plan': templates.new_plan, 'new-brief': templates.new_brief, 'new-steer': templates.new_steer,
            'pause': pause.pause, 'resume': pause.resume, 'progress': progress.run,
            'notes': notes.notes, 'note': notes.note, 'new-note': notes.new_note, 'status': status.run, 'scratch': scratch.run, 'metrics': metrics.run,
            'dispatch': send.dispatch, 'discuss': send.discuss, 'queue': queue.queue, 'step': step.step, 'wait': wait.wait, 'finish': report.finish, 'notify': report.notify, 'report': report.report, 'next': report.next_brief,
            'permission': permission.permission, 'steer': steer.steer, 'stop': stop.stop, '__detect_permission_mode': permission.helper,
            '__refuse_untrusted': panes.entry_refuse_untrusted,
            **agents.COMMANDS}

# The commands a variant may replace whole. Only these names ask the variant (loading it), so every other command runs without it, and an
# internal entry (a name starting with __) never goes through it.
OVERRIDABLE = ('answer', 'spawn', 'stop')


def resolve(ctx, cmd):
    """The function that runs cmd: the variant's own for an overridable name it defines, else the core's."""
    if cmd in OVERRIDABLE:
        command = ctx.variant.commands.get(cmd)
        if command is not None:
            return command
    return COMMANDS.get(cmd)


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
    sys.stdout.reconfigure(errors='surrogateescape', line_buffering=True)
    sys.stderr.reconfigure(errors='surrogateescape')
    try:
        ctx, cmd, args = parse(argv)
        command = resolve(ctx, cmd)
        if command is None:
            raise Die(f'unknown command: {cmd}')
        return command(ctx, args)
    except Exit as e:
        sys.stderr.write(e.stderr)
        return e.code
    except Die as e:
        sys.stderr.write(f'pair.sh: {e.message}\n')
        return e.code
    except (OSError, ValueError) as e:
        sys.stderr.write(f'pair.sh: {e}\n')
        return 1
