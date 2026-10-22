import re
import shutil
import sys

from . import agents, exits, panes, permission, spawn, store, wait
from .commands import init, log, metrics, notes, pause, progress, queue, report, scratch, send, status, step, steer, stop, templates
from .commands.failover import failover
from .commands.rotate import rotate
from .commands.spawn import spawn as spawn_command
from .context import Context
from .errors import Die, Exit
from .variant import load_variant_class

COMMANDS = {'init': init.run, 'log': log.run, 'new-plan': templates.new_plan, 'new-brief': templates.new_brief, 'new-steer': templates.new_steer,
            'pause': pause.pause, 'resume': pause.resume, 'progress': progress.run,
            'notes': notes.notes, 'note': notes.note, 'new-note': notes.new_note, 'status': status.run, 'scratch': scratch.run, 'metrics': metrics.run,
            'dispatch': send.dispatch, 'discuss': send.discuss, 'queue': queue.queue, 'step': step.step, 'wait': wait.wait, 'finish': report.finish, 'notify': report.notify, 'report': report.report, 'next': report.next_brief,
            'permission': permission.permission, 'steer': steer.steer, 'spawn': spawn_command, 'rotate': rotate, 'failover': failover, 'stop': stop.stop, '__detect_permission_mode': permission.helper,
            '__refuse_untrusted': panes.entry_refuse_untrusted, '__pair_report_ready': report.pair_report_ready, '__spawn_role': spawn.entry_spawn_role, '__spawn_consultant': spawn.entry_spawn_consultant,
            **agents.COMMANDS, **exits.COMMANDS}

# The commands a variant may replace whole. Only these names, and a name the core lacks, ask the variant (loading it), so every other
# command runs without it: an internal entry (a name starting with __) the core defines never goes through it, and one it lacks is the variant's.
OVERRIDABLE = ('answer', 'discuss', 'dispatch', 'finish', 'init', 'queue', 'spawn', 'step', 'stop', 'wait')


def resolve(ctx, cmd):
    """The function that runs cmd: the variant's own for an overridable name it defines, else the core's; for a name the core does not
    define at all, the variant's, so a variant can add commands (the kitchen's own) and internal entries (its bridges from bash)."""
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


PROG = 'pair.sh'


class ProgStderr:
    """stderr for a command that runs under another script than pair.sh (kitchen.sh): a line that begins `pair.sh: ` begins with the script's
    own name, and so does the `usage: pair.sh ...` inside it. The shell's own errors (`pair-core.sh: line N: ...`) are not touched."""

    def __init__(self, stream, prog):
        self.stream, self.prog = stream, prog

    def write(self, text):
        text = re.sub(r'(?m)^pair\.sh: usage: pair\.sh ', f'{self.prog}: usage: {self.prog} ', text)
        return self.stream.write(re.sub(r'(?m)^pair\.sh: ', f'{self.prog}: ', text))

    def __getattr__(self, name):
        return getattr(self.stream, name)


COMMAND_WORD = re.compile(r'[a-z][a-z-]*')


def build(skill_root):
    """The variant of the skill at skill_root, and the Context its attributes give."""
    variant = load_variant_class(skill_root)()
    state_root = variant.state_root()
    ctx = Context(variant.PAIR_SKILL, skill_root, state_root, variant.store_label, tuple(variant.roles), tuple(variant.store_dirs),
                  variant.slug_max, variant.unfilled_re)
    ctx._variant = variant
    return variant, ctx


def main(argv):
    global PROG
    sys.stdout.reconfigure(errors='surrogateescape', line_buffering=True)
    sys.stderr.reconfigure(errors='surrogateescape')
    plain = sys.stderr
    try:
        skill_root, words = parse(argv)
        variant, ctx = build(skill_root)
        PROG = variant.PAIR_PROG
        store.dedupe_wakes = variant.event_dedupe
        if PROG != 'pair.sh':
            sys.stderr = ProgStderr(plain, PROG)
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
    except Exit as e:
        sys.stderr.write(e.stderr)
        return e.code
    except Die as e:
        sys.stderr.write(f'pair.sh: {e.message}\n')
        return e.code
    except (OSError, ValueError) as e:
        sys.stderr.write(f'pair.sh: {e}\n')
        return 1
    finally:
        sys.stderr = plain
