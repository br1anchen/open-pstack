"""classify: stamps a brief's risk, profiles, verify mode and kitchen from its Scope.

Every kitchen.py call here (`snapshot`, `--json classify` and `lessons stamp`) goes through kpy.run, in process. None of them writes os.environ or
reads INVOCATION: those belong to `gate` and to `brief-verify` without --list. test_classify_kpy_calls_leave_the_environment_alone pins that."""

import json
import os
import re
import sys
from pathlib import Path

from pstack_core.checkin import scope_entries
from pstack_core.store import field, header_field, pair_file, read_text, risk_of, write_text
from pstack_core.errors import Die, EmptyScope, KitchenPyFailed
from pstack_core.panes import agent_status

from . import kpy
from .kpy import Kitchen

CLASSIFY_FAILED = 'kitchen.py classify failed; run kitchen.py validate in the repo'


def set_header(path, key, value):
    """Replaces the key's header line (the first one), or adds it after the plan: line (the first one); a file with neither line is left as it was."""
    lines = re.findall(r'[^\n]*\n|[^\n]+', read_text(path))
    exists = any(line.startswith(f'{key}:') for line in lines)
    done, out = False, []
    for line in lines:
        if not done and exists and line.startswith(f'{key}:'):
            line, done = f'{key}: {value}' + ('\n' if line.endswith('\n') else ''), True
        elif not done and not exists and line.startswith('plan:'):
            line, done = f'{line}{key}: {value}\n', True
        out.append(line)
    write_text(path, ''.join(out))


def brief_escalations(brief):
    """Why a brief is escalated beyond its paths, one line each: the master wrote risk: escalated on it, or its plan is escalated. Classify only raises
    a brief's risk from these, never lowers it."""
    reasons = []
    if risk_of(brief) == 'escalated':
        reasons.append('the brief says risk: escalated')
    plan = header_field(brief, 'plan')
    if plan not in ('', 'none') and os.path.isfile(plan) and risk_of(plan) == 'escalated':
        reasons.append(f'escalated plan: {os.path.basename(plan)}')
    return reasons


def kitchen_snapshot(store, root=None):
    """A host kitchen has no history, so a brief is classified, stepped and verified against the copy taken when it was classified (kitchen: in its
    header), the way a repo kitchen is read at the unit's commit. The copy's path, or nothing for a repo kitchen."""
    try:
        return kpy.kitchen_py.snapshot(kpy.invocation(store, root), Path(f'{store}/kitchens'))
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed('kitchen.py snapshot failed; run kitchen.py validate in the repo') from None


def brief_kitchen(brief):
    """--config <snapshot> for the brief, as a list; empty for a repo kitchen."""
    snap = header_field(brief, 'kitchen')
    return [] if snap in ('', 'repo') else ['--config', snap]


def classify_brief(store, brief):
    """Stamps risk, profiles, verify and kitchen on the brief and prints the summary."""
    kit = Kitchen(store)
    globs = scope_entries(brief, 'may')
    if not globs:
        raise EmptyScope(f"{brief} lists nothing under Scope's may write:")
    esc = []
    for reason in brief_escalations(brief):
        esc += ['--escalate', reason]
    snap = kitchen_snapshot(store)
    if snap:
        set_header(brief, 'kitchen', snap)
    elif header_field(brief, 'kitchen'):
        set_header(brief, 'kitchen', 'repo')
    cfg = brief_kitchen(brief)
    status, classified = kit.soft([*cfg, '--json', 'classify', '--paths', *globs, *esc])
    if status != 0:
        raise KitchenPyFailed(CLASSIFY_FAILED)
    data = json.loads(classified)
    risk, verify = data.get('risk') or '', data['verify']
    profiles, escalate = data['profiles'], data['escalate']
    verify_line = f'{verify["mode"]} by {verify["kind"]} verifier'
    # The master may have written a reason after the class (`escalated (contract change)`): rewrite the line only when the class changes.
    if risk_of(brief) != risk:
        set_header(brief, 'risk', risk)
    set_header(brief, 'profiles', ", ".join(profiles) or 'none')
    set_header(brief, 'verify', verify_line)
    sys.stdout.write(f'risk: {risk}\n')
    status, _ = kit.soft([*cfg, 'lessons', 'stamp', brief], show=True)
    if status != 0:
        raise KitchenPyFailed('kitchen.py lessons stamp failed; run kitchen.py lessons in the repo')
    lines = [f'profiles: {", ".join(profiles)}', f'verify: {verify_line}', *(f'escalate: {reason}' for reason in escalate)]
    lines += [f'warn: {warning}' for warning in data.get('warnings') or []]
    if data.get('bookkeeping'):
        lines.append('bookkeeping: the Scope is outside the repo; gates only, nothing to escalate')
    sys.stdout.write(''.join(f'{line}\n' for line in lines))
    first_reason = escalate[0] if escalate else ''
    if risk == 'escalated' and agent_status(field(store, 'consultant', 'name')) == 'absent':
        sys.stdout.write(f'next: kitchen.sh consultant {store} --reason "{first_reason}", then a plan round with it\n')


def classify(ctx, args):
    if len(args) != 2:
        raise Die('usage: kitchen.sh classify <store> <brief-path>')
    pair_file(args[0])
    if not os.path.isfile(args[1]):
        raise Die(f'brief not found: {args[1]}')
    classify_brief(args[0], args[1])
    return 0
