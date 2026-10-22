import importlib.util
import os
import sys
import zlib
from pathlib import Path

from pstack_core import consult
from pstack_core.review import jq_raw
from pstack_core.store import dispatched_brief, expected_report, get, header_field
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
    spec = importlib.util.spec_from_file_location('kitchen_core', KITCHEN_CORE_DIR / '__init__.py', submodule_search_locations=[str(KITCHEN_CORE_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['kitchen_core'] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules['kitchen_core']
        raise
    return module



def risk_of(brief):
    """The first word of a brief's risk: header, which the master may follow with the reason."""
    words = header_field(brief, 'risk').split()
    return words[0] if words else ''


def verify_block(brief):
    """awk '/^## Verify/{p=1; next} /^## /{p=0} p' on the brief, as bytes (awk ends every record with a newline)."""
    try:
        with open(brief, 'rb') as f:
            records = f.read().split(b'\n')
    except OSError as e:
        sys.stderr.write(f"awk: fatal: cannot open file `{brief}' for reading: {os.strerror(e.errno)}\n")
        return b''
    if records[-1] == b'':
        records.pop()
    out, show = [], False
    for line in records:
        if line.startswith(b'## Verify'):
            show = True
        elif line.startswith(b'## '):
            show = False
        elif show:
            out.append(line + b'\n')
    return b''.join(out)


def verify_job(store, brief, head):
    """Where finish keeps the run of the brief's Verify block at commit head: one per head and block, so an amended block runs again. The block's
    key is its crc32; a job directory under the old POSIX cksum key is never read, so the first finish after the switch runs a fresh Verify."""
    unit = os.path.basename(brief)
    unit = unit[:-3] if unit.endswith('.md') and unit != '.md' else unit
    return f'{store}/steps/jobs/{unit}-verify-{head[:12]}-{zlib.crc32(verify_block(brief))}'


class KitchenVariant(TrioVariant):
    # Its own table, as every variant has one: stop is the trio's, which loops over the kitchen's roles (the verifier too). Spawn
    # stays the pair's, under kitchen.sh's wrapper.
    commands = {'stop': TrioVariant.commands['stop']}
    verify_job = staticmethod(verify_job)  # finish (kitchen_core.step) keeps its Verify run where report_ready looks for it

    def require_agreed_plan(self, ctx, store, brief):
        """Routine units run on the brief alone; an escalated one needs the agreed plan with the consultant's advice, as in the trio. A
        routine brief that names a plan still needs it agreed. The gate to apply is an argument, as the bash passed it in a global."""
        if risk_of(brief) == 'escalated':
            require_agreed_plan(ctx, store, brief, lambda *args: TrioVariant.plan_gate_extra(self, *args))
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
        head = commit_of(jq_raw(get(store, 'git_root')), header_field(report, 'head'))
        if head is None:
            return ReportReadiness(False, '', 'its head: is not a commit')
        try:
            with open(f'{verify_job(store, brief, head)}/rc', 'rb') as f:
                rc = f.read().decode('utf-8', 'surrogateescape').rstrip('\n')
        except OSError:
            rc = ''
        if rc == '0':
            return base
        return ReportReadiness(False, '', f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)")



def _kitchen_command(module, function):
    """A command of kitchen_core, loaded when it runs: the package is imported for a kitchen command that needs it and not before."""
    def run(ctx, args):
        load_kitchen_core()
        import importlib
        return getattr(importlib.import_module(f'kitchen_core.{module}'), function)(ctx, args)
    run.__name__ = function
    return run


KitchenVariant.commands = {**KitchenVariant.commands, 'new-consult': consult.new_consult, 'consult': consult.consult, 'advice': consult.advice,
                           'catch': _kitchen_command('ledger', 'catch'), 'retro': _kitchen_command('ledger', 'retro'),
                           'new-handoff': _kitchen_command('handoff', 'new_handoff'), 'feedback': _kitchen_command('feedback', 'feedback'),
                           'maintainer': _kitchen_command('feedback', 'maintainer'), 'close': _kitchen_command('master', 'close'),
                           'rotate-master': _kitchen_command('master', 'rotate_master'),
                           'take-master': _kitchen_command('master', 'take_master'),
                           'land-check': _kitchen_command('landing', 'land_check_command'), 'revise': _kitchen_command('landing', 'revise'),
                           'verify': _kitchen_command('verify', 'verify_command'), 'classify': _kitchen_command('classify', 'classify'),
                           'queue': _kitchen_command('discuss', 'queue'), 'discuss': _kitchen_command('discuss', 'discuss'),
                           'init': _kitchen_command('setup', 'init'), 'spawn': _kitchen_command('setup', 'spawn'),
                           'consultant': _kitchen_command('setup', 'consultant'), 'fleet': _kitchen_command('setup', 'fleet'),
                           'wait': _kitchen_command('wait', 'wait'),
                           'dispatch': _kitchen_command('dispatch', 'dispatch'),
                           '__record_skill_build': _kitchen_command('setup', 'record_skill_build_entry'),
                           'step': _kitchen_command('step', 'step'), 'finish': _kitchen_command('step', 'finish')}

VARIANT = KitchenVariant
