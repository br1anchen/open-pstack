import re
import shutil
import sys

from . import gates, permission, send, spawn, wait
from .commands import init, metrics, notes, report, rotate, scratch, status, step, steer, stop
from .store import Context
from .errors import Die, translate
from .variant import load_variant_class

COMMANDS = {'init': init.run, 'log': status.log, 'new-plan': notes.new_plan, 'new-brief': notes.new_brief, 'new-steer': notes.new_steer,
            'pause': gates.pause, 'resume': gates.resume, 'progress': status.progress,
            'notes': notes.notes, 'note': notes.note, 'new-note': notes.new_note, 'status': status.run, 'scratch': scratch.run, 'metrics': metrics.run,
            'dispatch': send.dispatch, 'discuss': send.discuss, 'queue': rotate.queue, 'step': step.step, 'wait': wait.wait, 'finish': report.finish, 'notify': report.notify, 'report': report.report, 'next': report.next_brief,
            'permission': permission.permission, 'steer': steer.steer, 'spawn': spawn.spawn, 'rotate': rotate.rotate, 'failover': rotate.failover, 'stop': stop.stop}

# The commands a variant may replace whole. Only these names, and a name the core lacks, ask the variant, so every other command is the core's.
OVERRIDABLE = ('answer', 'discuss', 'dispatch', 'finish', 'init', 'queue', 'spawn', 'step', 'stop', 'wait')


def resolve(ctx, cmd):
    """The function that runs cmd: the variant's own for an overridable name it defines, else the core's; for a name the core does not
    define at all, the variant's, so a variant can add commands (the kitchen's own)."""
    if cmd in OVERRIDABLE:
        command = ctx.variant.commands.get(cmd)
        if command is not None:
            return command
    command = COMMANDS.get(cmd)
    if command is None:
        return ctx.variant.commands.get(cmd)
    return command


def parse(argv):
    """`--skill-root DIR` then the command words: (skill root, words)."""
    if len(argv) < 2 or argv[0] != '--skill-root':
        raise Die('usage: pair.py --skill-root DIR [command [args]]')
    return argv[1], argv[2:]


COMMAND_WORD = re.compile(r'[a-z][a-z-]*')


def build(skill_root):
    """The variant of the skill at skill_root, and the Context its attributes give."""
    variant = load_variant_class(skill_root)()
    state_root = variant.state_root()
    ctx = Context(variant.PAIR_SKILL, skill_root, state_root, variant.store_label, tuple(variant.roles), tuple(variant.store_dirs),
                  variant.slug_max, variant.unfilled_re, variant.PAIR_PROG, variant.event_dedupe)
    ctx._variant = variant
    return variant, ctx


def main(argv):
    sys.stdout.reconfigure(line_buffering=True)
    prog = 'pair.sh'
    try:
        skill_root, words = parse(argv)
        variant, ctx = build(skill_root)
        prog = ctx.prog
        if shutil.which('herdr') is None:
            raise Die('herdr is required on PATH')
        variant.startup(ctx, words)
        if not words:
            sys.stdout.write(variant.usage)
            return 1
        cmd = words[0]
        if cmd in ('help', '-h', '--help'):
            sys.stdout.write(variant.usage)
            return 0
        command = resolve(ctx, cmd) if COMMAND_WORD.fullmatch(cmd) else None
        if command is None:
            sys.stdout.write(variant.usage)
            raise Die(f'unknown command: {cmd}')
        return command(ctx, words[1:])
    except Exception as e:
        text, code = translate(e, prog)
        sys.stderr.write(text)
        return code
