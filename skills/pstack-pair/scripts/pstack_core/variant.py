import importlib.util
import os
import re

from . import git
from .review import noted_through, open_notes
from .steplog import StepLog
from .store import count, header_field, load, peek, text_at, unit_name


class ReportReadiness:
    """Whether a report may be shown to the master as a reply. open is the newline-joined open note paths
    why the reason it is withheld."""

    __slots__ = ('ready', 'open', 'why')

    def __init__(self, ready, open='', why=''):
        self.ready = ready
        self.open = open
        self.why = why


def commit_of(root, rev):
    return git.out(root, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}')


def _entry(text):
    return tuple(text.split('\n'))


# One entry per command, as its lines of the pair's help; a variant's help takes the entries it shares from here (command_table).
PAIR_COMMANDS = {
    'init': _entry('''  init <slug> [--store DIR]                 create or re-register the pair store; prints its path'''),
    'spawn': _entry('''  spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [--direction right|down] [--pane ID] [--timeout MS] [-- agent-args...]
                                            split from the master pane, start and bootstrap the sidekick;
                                            Devin defaults to bypass, other kinds inherit the master's mode;
                                            a pi or Prime sidekick first proves its model answers; on failure the
                                            --fallback kind starts instead; --tab gives the sidekick its own tab
                                            instead of a split (PSTACK_PLACEMENT=tab sets the default)'''),
    'rotate': _entry('''  rotate <store>                            exit an idle sidekick after a done brief, respawn with recorded
                                            arguments, and bootstrap a fresh session; preserve the queue and history'''),
    'failover': _entry('''  failover <store> [--reason TEXT] [--force]
                                            replace a failing sidekick with its recorded fallback in the same pane;
                                            the fallback picks up from the store, and the run stays on it'''),
    'permission': _entry('''  permission                                print the master's detected permission mode'''),
    'new-plan': _entry('''  new-plan <store> <slug>                   create plans/NNN-<slug>.md from the template; prints its path'''),
    'discuss': _entry('''  discuss <store> <plan-path> [--timeout MS]
                                            send PLAN to the sidekick and wait for its agree/object response'''),
    'new-brief': _entry('''  new-brief <store> <slug>                  create briefs/NNN-<slug>.md from the template; prints its path'''),
    'dispatch': _entry('''  dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]
                                            send BRIEF to the sidekick (a due rotation runs first, up to a minute plus
                                            the READY wait, even with --send-only) and wait for it to settle;
                                            implementation playbooks require an agreed plan'''),
    'wait': _entry('''  wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path
                                            (and any queued or now-running brief), or a check-in digest when the
                                            interval passes first'''),
    'queue': _entry('''  queue <store> <brief-path> [--replace]    hold the next brief for a working sidekick; it waits for the master's
                                            dispatch, which rotates a due sidekick first. One slot. queue <store> --clear empties it'''),
    'step': _entry('''  step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]
                                            sidekick: record a committed step of the running brief and go on;
                                            the master's wait picks it up for review'''),
    'notes': _entry('''  notes <store>                             sidekick, at each step boundary: print blocking review notes still open'''),
    'new-note': _entry('''  new-note <store> <NNN>                    create the draft note reviewing unit NNN's steps since the last note'''),
    'note': _entry('''  note <store> <note-draft-path>            publish a filled note to the sidekick; its follow-ups go to followups.md'''),
    'finish': _entry('''  finish <store> <report-path>              sidekick, after writing any report: notify the master, then print
                                            the REPORT line; a done brief requires rotation'''),
    'next': _entry('''  next <store>                              sidekick: refused for every kind (exit 5); the master dispatches the queued brief to a fresh session'''),
    'progress': _entry('''  progress <store> <text>                   sidekick: append one timestamped line to the running brief's progress log'''),
    'new-steer': _entry('''  new-steer <store> <NNN> [--supersedes STEER | --force]
                                            create steers/NNN-<slug>-s<k>.md; prints its path. --supersedes answers
                                            an objection; two fresh steers per brief'''),
    'steer': _entry('''  steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]
                                            send STEER: to a working sidekick, return at once (its harness hands it
                                            over between tool calls; --interrupt cancels the running one first);
                                            to one paused on an objection, wait for it to settle'''),
    'report': _entry('''  report <store> [NNN]                      print the latest (or NNN) report path'''),
    'notify': _entry('''  notify <store> <report-path>              sidekick -> master: prompt the master if it is idle'''),
    'stop': _entry('''  stop <store> [--timeout MS]               send STOP; the sidekick pauses safely and reports'''),
    'scratch': _entry('''  scratch <store> <id> [--at SHA] [--remove]
                                            a throwaway worktree under <store>/scratch/<id>: at HEAD with the live
                                            diff applied, or exactly at SHA to recheck a unit while the sidekick works'''),
    'pause': _entry('''  pause <store> [--reason TEXT]             pause at the next safe point: the sidekick stops at its next step or
                                            progress boundary; dispatch, queue, discuss, consult, answer refuse'''),
    'resume': _entry('''  resume <store>                            lift the pause; re-dispatch the paused brief to continue it'''),
    'status': _entry('''  status <store>                            table of briefs, reports, reviews, and live agent states'''),
    'log': _entry('''  log <store> <phase> <decision> <why> <evidence> <result>
                                            append a decisions.tsv row (show-me-your-work format)'''),
    'metrics': _entry('''  metrics <store>                           where the time went, from events.tsv: busy and idle per agent,
                                            master wakes, review latency, verdicts'''),
}


def command_table(base, drop=(), replace=None, after=None):
    """base's commands in order, without the names in drop, an entry of replace in place of the one with its name, and the entries of
    after[name] following that command."""
    table = {}
    for name, lines in base.items():
        if name not in drop:
            table[name] = (replace or {}).get(name, lines)
            table.update((after or {}).get(name, {}))
    return table


def usage_text(commands, exit_codes):
    """The help a variant prints: the program line, one entry per command, and the exit codes."""
    return 'usage: pair.sh <command> [args]\n\n' + '\n'.join(line for lines in commands.values() for line in lines) + f'\n\n{exit_codes}\n'


PAIR_USAGE = usage_text(PAIR_COMMANDS, 'exit codes: 0 ok, 1 usage or precondition, 2 herdr error, 3 sidekick blocked, 4 no report yet or queue empty, 5 sidekick in the wrong state or queue taken, 6 plan not agreed, 7 steer cap reached')


class Variant:
    """The pair's hooks and configuration. A variant's scripts/variant.py subclasses this and sets VARIANT; the attributes below are pstack-pair's
    and a subclass overrides the ones its skill sets differently. STATE_DIR is the directory under pstack/ in the XDG state home."""

    PAIR_SKILL = 'pstack-pair'
    PAIR_PROG = 'pair.sh'
    STATE_DIR = 'pair'
    store_label = 'Pair'
    roles = ('sidekick',)
    store_dirs = ('plans', 'briefs', 'reports', 'reviews', 'progress', 'steers')
    slug_max = 21
    unfilled_re = ''
    event_dedupe = False
    usage = PAIR_USAGE

    def state_root(self):
        return os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'pstack', self.STATE_DIR)

    def startup(self, ctx, words):
        """Runs once per invocation, after the entry checks and before dispatch, for every command word."""

    # Whole commands this variant runs in place of the core's, by name, for the names in cli.OVERRIDABLE. Every variant defines its own table
    # in its own class body: a table inherited from another variant would give a skill commands it never chose (kitchen's spawn is the pair's,
    # not the trio's).
    commands = {}

    def report_ready(self, ctx, store, report):
        """The pair's report readiness. Only a done report is judged, and only the unit's own report:
        the -sN and -qN reports answer a steer or a consult. Open blocking notes withhold it, and so does
        a head that predates the unit's last recorded step. A unit with no steps, or a head that does not
        resolve (older stores, fixtures), is judged on notes alone.
        Steps or notes that cannot be read withhold it."""
        if header_field(report, 'status') != 'done':
            return ReportReadiness(True)
        base = unit_name(report)
        if re.search(r'-[sq][0-9]', base):
            return ReportReadiness(True)
        try:
            opened = open_notes(store, report)
            tail = StepLog(store, base).last()
        except OSError as e:
            return ReportReadiness(False, '', f'the steps or notes cannot be read: {e.filename}: {os.strerror(e.errno)}')
        if opened:
            names = ','.join(re.sub(r'.*-(n[0-9]+)\.md$', r'\1', p) for p in opened)
            return ReportReadiness(False, '\n'.join(opened), f'open blocking notes {names}')
        if tail is None:
            return ReportReadiness(True)
        m = re.match(r'[0-9a-f]{7,40}', header_field(report, 'head'))
        if not m:
            return ReportReadiness(True)
        head = m.group(0)
        pair = load(store)
        root = text_at(pair, 'git_root') or text_at(pair, 'cwd')
        if commit_of(root, head) is None:
            return ReportReadiness(True)
        if commit_of(root, tail.sha) is None:
            return ReportReadiness(True)
        if git.ok(root, 'merge-base', '--is-ancestor', tail.sha, head):
            return ReportReadiness(True)
        return ReportReadiness(False, '', f"the report's head {head[:9]} predates step {tail.number} {tail.sha[:9]}")


    def steps_due(self, ctx, store, brief):
        """Whether a wait should return to the master for the running brief's steps."""
        return steps_due_in(ctx, store, brief)

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """Further gates on an agreed plan, called with the plan path, its review path and its sequence number. A refusal is a Die
        with exit 6. The base pair requires nothing more."""

    def require_agreed_plan(self, ctx, store, brief):
        """The gate in front of a brief that implements a plan: the agreed plan, and this variant's plan_gate_extra."""
        from .gates import require_agreed_plan
        require_agreed_plan(ctx, store, brief, self.plan_gate_extra)


def steps_due_in(ctx, store, brief):
    """Whether a step landed: a step landed that neither a note nor an earlier steps wake has covered."""
    from .replies import unreviewed_steps
    if unreviewed_steps(ctx, store) is None:
        return False
    shown = count(peek(load(store), 'shown'), unit_name(brief))
    last = len(StepLog(store, brief).rows())
    return last > shown and last > noted_through(store, brief)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_name(skill):
    return 'pstack_variant_' + skill.replace('-', '_')


def load_skill_variant(skills_dir, skill):
    """The VARIANT class of skills_dir/skill/scripts/variant.py, or Variant when that skill has none."""
    path = f'{skills_dir}/{skill}/scripts/variant.py'
    if not os.path.isfile(path):
        return Variant
    return load_module(path, module_name(skill)).VARIANT


def load_variant_class(skill_root):
    """The VARIANT class of skill_root/scripts/variant.py, or Variant when that file does not exist."""
    path = f'{skill_root}/scripts/variant.py'
    if not os.path.isfile(path):
        return Variant
    return load_module(path, module_name(os.path.basename(skill_root))).VARIANT


def load_variant(ctx):
    """The variant of ctx's skill: scripts/variant.py beside its pair.sh, or the base."""
    path = f'{ctx.skill_root}/scripts/variant.py'
    if not os.path.isfile(path):
        return Variant()
    return load_module(path, module_name(ctx.skill)).VARIANT()
