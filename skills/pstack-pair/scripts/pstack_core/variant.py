import importlib.util
import os
import re
import subprocess

from .review import git, jq_raw, noted_through, open_notes, steps_file
from .store import alt, count, get, header_field, last_line, load, unit_name


class ReportReadiness:
    """Whether a report may be shown to the master as a reply. open is the newline-joined open note paths
    (bash report_open), why the reason it is withheld (bash report_why)."""

    __slots__ = ('ready', 'open', 'why')

    def __init__(self, ready, open='', why=''):
        self.ready = ready
        self.open = open
        self.why = why


def commit_of(root, rev):
    return git(root, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}')


PAIR_USAGE = """usage: pair.sh <command> [args]

  init <slug> [--store DIR]                 create or re-register the pair store; prints its path
  spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [--direction right|down] [--pane ID] [--timeout MS] [-- agent-args...]
                                            split from the master pane, start and bootstrap the sidekick;
                                            Devin defaults to bypass, other kinds inherit the master's mode;
                                            a pi or Prime sidekick first proves its model answers; on failure the
                                            --fallback kind starts instead; --tab gives the sidekick its own tab
                                            instead of a split (PSTACK_PLACEMENT=tab sets the default)
  rotate <store>                            exit an idle Devin, pi, or Prime sidekick after a done brief, respawn with recorded
                                            arguments, and bootstrap a fresh session; preserve the queue and history
  failover <store> [--reason TEXT] [--force]
                                            replace a failing sidekick with its recorded fallback in the same pane;
                                            the fallback picks up from the store, and the run stays on it
  permission                                print the master's detected permission mode
  new-plan <store> <slug>                   create plans/NNN-<slug>.md from the template; prints its path
  discuss <store> <plan-path> [--timeout MS]
                                            send PLAN to the sidekick and wait for its agree/object response
  new-brief <store> <slug>                  create briefs/NNN-<slug>.md from the template; prints its path
  dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]
                                            send BRIEF to the sidekick and wait for it to settle;
                                            implementation playbooks require an agreed plan
  wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path
                                            (and any queued or now-running brief), or a check-in digest when the
                                            interval passes first
  queue <store> <brief-path> [--replace]    hold the next brief for a working sidekick; it takes it the moment its
                                            current report is written (Devin, pi, and Prime wait for rotation and dispatch). One slot. queue <store> --clear empties it
  step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]
                                            sidekick: record a committed step of the running brief and go on;
                                            the master's wait picks it up for review
  notes <store>                             sidekick, at each step boundary: print blocking review notes still open
  new-note <store> <NNN>                    create the draft note reviewing unit NNN's steps since the last note
  note <store> <note-draft-path>            publish a filled note to the sidekick; its follow-ups go to followups.md
  finish <store> <report-path>              sidekick, after writing any report: notify the master, then print
                                            the queued brief to start for other kinds, or the REPORT line; a done Devin, pi, or Prime brief requires rotation
  next <store>                              sidekick: take the queued brief after writing a report; exit 4 when empty
  progress <store> <text>                   sidekick: append one timestamped line to the running brief's progress log
  new-steer <store> <NNN> [--supersedes STEER | --force]
                                            create steers/NNN-<slug>-s<k>.md; prints its path. --supersedes answers
                                            an objection; two fresh steers per brief
  steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]
                                            send STEER: to a working sidekick, return at once (its harness hands it
                                            over between tool calls; --interrupt cancels the running one first);
                                            to one paused on an objection, wait for it to settle
  report <store> [NNN]                      print the latest (or NNN) report path
  notify <store> <report-path>              sidekick -> master: prompt the master if it is idle
  stop <store> [--timeout MS]               send STOP; the sidekick pauses safely and reports
  scratch <store> <id> [--at SHA] [--remove]
                                            a throwaway worktree under <store>/scratch/<id>: at HEAD with the live
                                            diff applied, or exactly at SHA to recheck a unit while the sidekick works
  pause <store> [--reason TEXT]             pause at the next safe point: the sidekick stops at its next step or
                                            progress boundary; dispatch, queue, discuss, consult, answer refuse
  resume <store>                            lift the pause; re-dispatch the paused brief to continue it
  status <store>                            table of briefs, reports, reviews, and live agent states
  log <store> <phase> <decision> <why> <evidence> <result>
                                            append a decisions.tsv row (show-me-your-work format)
  metrics <store>                           where the time went, from events.tsv: busy and idle per agent,
                                            master wakes, review latency, verdicts

exit codes: 0 ok, 1 usage or precondition, 2 herdr error, 3 sidekick blocked, 4 no report yet or queue empty, 5 sidekick in the wrong state or queue taken, 6 plan not agreed, 7 steer cap reached
"""


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
        """The pair's report readiness (report_ready in the bash before phase 3). Only a done report is judged, and only the unit's own report:
        the -sN and -qN reports answer a steer or a consult. Open blocking notes withhold it, and so does
        a head that predates the unit's last recorded step. A unit with no steps, or a head that does not
        resolve (older stores, fixtures), is judged on notes alone."""
        if header_field(report, 'status') != 'done':
            return ReportReadiness(True)
        base = unit_name(report)
        if re.search(r'-[sq][0-9]', base):
            return ReportReadiness(True)
        opened = open_notes(store, report)
        if opened:
            names = ','.join(re.sub(r'.*-(n[0-9]+)\.md$', r'\1', p) for p in opened)
            return ReportReadiness(False, '\n'.join(opened), f'open blocking notes {names}')
        file = steps_file(store, base)
        try:
            if os.path.getsize(file) == 0:
                return ReportReadiness(True)
        except OSError:
            return ReportReadiness(True)
        m = re.match(r'[0-9a-f]{7,40}', header_field(report, 'head'))
        if not m:
            return ReportReadiness(True)
        head = m.group(0)
        pair = load(store)
        root = jq_raw(alt(pair.get('git_root'), pair.get('cwd')))
        if commit_of(root, head) is None:
            return ReportReadiness(True)
        row = last_line(file)
        cols = row.split('\t')
        k, last = cols[0], cols[1] if len(cols) > 1 else row
        if commit_of(root, last) is None:
            return ReportReadiness(True)
        if subprocess.run(['git', '-C', root, 'merge-base', '--is-ancestor', last, head],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
            return ReportReadiness(True)
        return ReportReadiness(False, '', f"the report's head {head[:9]} predates step {k} {last[:9]}")


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
    """Whether a step landed (steps_due in the bash before phase 3): a step landed that neither a note nor an earlier steps wake has covered."""
    from .replies import unreviewed_steps
    if unreviewed_steps(ctx, store) is None:
        return False
    shown = count(alt(get(store, 'shown'), {}), unit_name(brief))
    file = steps_file(store, brief)
    with open(file, 'rb') as f:
        last = sum(1 for line in f.read().split(b'\n') if line)
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
