import importlib.util
import os
import sys
import zlib
from pathlib import Path

from pstack_core import consult
from pstack_core.store import (
    dispatched_brief,
    expected_report,
    header_field,
    read,
    read_text,
    risk_of,
)
from pstack_core.gates import no_gate, require_agreed_plan
from pstack_core.variant import ReportReadiness, commit_of, load_skill_variant

TrioVariant = load_skill_variant(Path(__file__).resolve().parents[2], 'pstack-trio')

KITCHEN_CORE_DIR = Path(__file__).resolve().parent / 'kitchen_core'


def load_kitchen_core():
    """The kitchen_core package, registered in sys.modules as a real package (search locations set) so relative imports and dataclasses work,
    loaded by path from the directory beside this file: whatever the working directory, and only for a command that needs it."""
    module = sys.modules.get('kitchen_core')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(
        'kitchen_core',
        KITCHEN_CORE_DIR / '__init__.py',
        submodule_search_locations=[str(KITCHEN_CORE_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules['kitchen_core'] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules['kitchen_core']
        raise
    return module


def verify_block(ctx, brief):
    """The lines of the brief's Verify section, each ending in a newline."""
    try:
        records = read_text(brief).split('\n')
    except OSError as e:
        sys.stderr.write(f'{ctx.prog}: cannot read {brief}: {os.strerror(e.errno)}\n')
        return ''
    if records[-1] == '':
        records.pop()
    out, show = [], False
    for line in records:
        if line.startswith('## Verify'):
            show = True
        elif line.startswith('## '):
            show = False
        elif show:
            out.append(line + '\n')
    return ''.join(out)


def verify_job(ctx, store, brief, head):
    """Where finish keeps the run of the brief's Verify block at commit head: one per head and block, so an amended block runs again. The block's
    key is its crc32; a job directory under the old POSIX cksum key is never read, so the first finish after the switch runs a fresh Verify."""
    unit = os.path.basename(brief)
    unit = unit[:-3] if unit.endswith('.md') and unit != '.md' else unit
    key = zlib.crc32(verify_block(ctx, brief).encode('utf-8'))
    return f'{store}/steps/jobs/{unit}-verify-{head[:12]}-{key}'


USAGE = """usage: kitchen.sh <command> [args]

The pair's commands (pstack-pair's pair.sh help) work unchanged: init, rotate, failover, permission,
new-plan, new-brief, wait, queue, notes, new-note, note, finish, next, progress, new-steer, steer,
report, notify, stop, scratch, pause, resume, status, log, metrics, and the consultant's new-consult,
consult, advice. These differ or are new:

  log <store> <phase> <decision> <why> <evidence> <result>
                                            append one row to decisions.tsv and a log event for the master

  spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [-- args...]
                                            start the sidekick; kind, args, and fallback default to the
                                            host roster (~/.config/pstack/kitchen.toml); --tab gives every role
                                            (sidekick, consultant, verifier) its own tab instead of a split
  consultant <store> --reason TEXT [--kind KIND] [-- args...]
                                            start the consultant for an escalated unit (roster kind by default)
  classify <store> <brief-path>             map the brief's may-write Scope to profiles and a risk class and
                                            stamp risk:, profiles:, verify: into its header
  discuss <store> <plan-path>               a routine plan goes to the sidekick; risk: escalated to both
  dispatch <store> <brief-path> [...] [--max MIN] [--send-only]
                                            classify, then the pair's dispatch; escalated units need an agreed
                                            plan with advice, landing units need land-check to pass; waits quietly,
                                            or returns once the brief is delivered with --send-only; a due
                                            rotation runs first, up to a minute plus the READY wait
  wait <store> [...] [--max MIN]            the pair's wait, quiet through check-ins with nothing flagged; returns
                                            for a report, steps, a block, a flagged check-in, or after --max (60)
  step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]
                                            sidekick: run the touched profiles' fast gates and policy on the
                                            step's commits, then record it; exit 2 when they fail. The checks run
                                            detached: exit 4 "still running" after ~2 minutes, run it again to wait
  finish <store> <report>                   the pair's finish; a done report first reruns the brief's Verify
                                            commands as written at its head: (the checkout, clean), and goes to
                                            the master only when every one exits 0 (exit 2 otherwise, 4 while
                                            they run: run it again to wait)
  verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND]
                                            verify one unit, or several as a batch, or a fix with the unit it
                                            fixes (--covers): gates only, or a fresh verifier in a scratch
                                            worktree at the head; returns at the interval with its progress
                                            (exit 4; --until-verdict never returns at the interval, only on
                                            the verdict, the deadline, a stall or a provider error), and with
                                            the verdict and whether to audit when it lands.
                                            A verifier stopped by its provider is retried on the sidekick's
                                            fallback; the deadline defaults to 3x the measured gate time
  verify <store> --landing [--base REV] [--parent REF|none] [--head REV]
                                            a fresh verifier at the stack tip: every behavioral gate the stack
                                            touches, from where it leaves trunk, or from the parent --parent
                                            records for later landings (--parent none clears it; --base wins
                                            for one call and may be combined with --parent);
                                            --head REV proves that commit or branch instead of HEAD
  verify <store> --wait [--every MIN | --until-verdict]
                                            wait again for the open verification: exit 4 while it runs, 5 when none
                                            is open
  verify <store> --cancel                   stop the open verification that has no verdict yet: its verifier, pane
                                            and scratch go (exit 5 when none is open); a verdict written but
                                            unread is closed by the next verify (`closed:`)
  revise <store> <verdict-path>             draft the fix brief for a rejected verdict
  land-check <store>                        every accepted unit verified
  catch <store> <layer> <text> [<unit>]     record a miss found later (audit, human review): layer is
                                            gate, policy, self, verify, review, triage, or escalation;
                                            the unit (e.g. 003) gives the ledger row its area
  new-handoff <store>                       write session-handoff.md from the template, with every unit's state
  rotate-master <store> [--model M]         record the filled handoff and this master's session, and start the
                                            next generation in a pane beside this one (MCP-free, remote control
                                            on, same permission mode and model); nudged by verify after
                                            KITCHEN_MASTER_ROTATE_UNITS (5) verified units
  take-master <store>                       the new master's first command: close the old master's pane and
                                            take back the run's master name
  close <store> [--force]                   end a finished run's sidekick, consultant and verifier and close
                                            their panes; refuses while a verification is open or the sidekick
                                            works (--force overrides); the master's own pane stays
  retro <store>                             where the master was needed, and what repeated, from this run
                                            and the repo's ledger
  feedback <store> <file>                   file the master's kitchen gaps (see references/feedback-template.md)
                                            into the host inbox; tells the registered maintainer, else the human
  feedback --inbox                          open reports, oldest first
  feedback --close <id> <commit|none> <note>
                                            retire a report (closing twice is harmless)
  maintainer on [--pane ID] | off | status  register this session to receive FEEDBACK pointers
  fleet [--since YYYY-MM-DD] [--runs DIR] [--json]
                                            one row per run on the host (verified units, wakes, failovers,
                                            provider stops, catches, open feedback), then one per skills build;
                                            read-only, no store needed

exit codes: the pair's, plus 2 for a failed gate, a rejected or invalid verdict, or a failed land-check
"""


class KitchenVariant(TrioVariant):
    PAIR_SKILL = 'pstack-kitchen'
    PAIR_PROG = 'kitchen.sh'
    STATE_DIR = 'kitchen/runs'
    store_label = 'Kitchen'
    roles = ('sidekick', 'verifier', 'consultant')
    store_dirs = (
        'plans',
        'briefs',
        'reports',
        'reviews',
        'progress',
        'steers',
        'consults',
        'advice',
        'scratch',
        'verdicts',
    )
    event_dedupe = True
    usage = USAGE
    # Its own table, as every variant has one: stop is the trio's, which loops over the kitchen's roles (the verifier too). Spawn
    # stays the pair's, under kitchen.sh's wrapper.
    commands = {'stop': TrioVariant.commands['stop']}

    def startup(self, ctx, words):
        """Every command word stamps the skills build on a store named as its second word: a run keeps the list of skills directories its
        commands ran from."""
        if len(words) >= 2 and os.path.isfile(f'{words[1]}/pair.json'):
            load_kitchen_core()
            from kitchen_core.setup import record_skill_build

            record_skill_build(ctx, words[1])

    verify_job = staticmethod(
        verify_job
    )  # finish (kitchen_core.step) keeps its Verify run where report_ready looks for it

    def require_agreed_plan(self, ctx, store, brief):
        """Routine units run on the brief alone; an escalated one needs the agreed plan with the consultant's advice, as in the trio. A
        routine brief that names a plan still needs it agreed. The gate to apply is an argument, as the bash passed it in a global."""
        if risk_of(brief) == 'escalated':
            require_agreed_plan(
                ctx,
                store,
                brief,
                lambda *args: TrioVariant.plan_gate_extra(self, *args),
            )
            return
        if header_field(brief, 'plan') in ('', 'none'):
            return
        require_agreed_plan(ctx, store, brief, no_gate)

    def steps_due(self, ctx, store, brief):
        """The master reads every step of an escalated unit; a routine unit's steps are proved by their gates."""
        if not brief or risk_of(brief) != 'escalated':
            return False
        return super().steps_due(ctx, store, brief)

    def report_ready(self, ctx, store, report):
        """A done report for the dispatched unit also needs the brief's Verify commands to have passed at its head,
        rerun by the kitchen: a wait never shows the master a done report that finish refused."""
        base = super().report_ready(ctx, store, report)
        if not base.ready:
            return base
        if header_field(report, 'status') != 'done':
            return base
        brief = dispatched_brief(store)
        if not brief or os.path.realpath(report) != expected_report(store, brief):
            return base
        head = commit_of(read(store, 'git_root'), header_field(report, 'head'))
        if head is None:
            return ReportReadiness(False, '', 'its head: is not a commit')
        try:
            rc = read_text(f'{verify_job(ctx, store, brief, head)}/rc').rstrip('\n')
        except OSError:
            rc = ''
        if rc == '0':
            return base
        return ReportReadiness(
            False,
            '',
            f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)",
        )


def _kitchen_command(module, function):
    """A command of kitchen_core, loaded when it runs: the package is imported for a kitchen command that needs it and not before."""

    def run(ctx, args):
        load_kitchen_core()
        import importlib

        return getattr(importlib.import_module(f'kitchen_core.{module}'), function)(
            ctx, args
        )

    run.__name__ = function
    run.command = (module, function)
    return run


KitchenVariant.commands = {
    **KitchenVariant.commands,
    'new-consult': consult.new_consult,
    'consult': consult.consult,
    'advice': consult.advice,
    'catch': _kitchen_command('ledger', 'catch'),
    'retro': _kitchen_command('ledger', 'retro'),
    'new-handoff': _kitchen_command('handoff', 'new_handoff'),
    'feedback': _kitchen_command('feedback', 'feedback'),
    'maintainer': _kitchen_command('feedback', 'maintainer'),
    'close': _kitchen_command('master', 'close'),
    'rotate-master': _kitchen_command('master', 'rotate_master'),
    'take-master': _kitchen_command('master', 'take_master'),
    'land-check': _kitchen_command('landing', 'land_check_command'),
    'revise': _kitchen_command('landing', 'revise'),
    'verify': _kitchen_command('verify', 'verify'),
    'classify': _kitchen_command('classify', 'classify'),
    'queue': _kitchen_command('dispatch', 'queue'),
    'discuss': _kitchen_command('dispatch', 'discuss'),
    'init': _kitchen_command('setup', 'init'),
    'spawn': _kitchen_command('setup', 'spawn'),
    'consultant': _kitchen_command('setup', 'consultant'),
    'fleet': _kitchen_command('setup', 'fleet'),
    'wait': _kitchen_command('wait', 'wait'),
    'dispatch': _kitchen_command('dispatch', 'dispatch'),
    'step': _kitchen_command('step', 'step'),
    'finish': _kitchen_command('step', 'finish'),
}

VARIANT = KitchenVariant
