"""classify: stamps a brief's risk, profiles, verify mode and kitchen from its Scope. Ported from kitchen.sh at b632713 (brief_escalations,
kitchen_snapshot, brief_kitchen, set_header, classify_brief and cmd_classify).

Every kitchen.py call here (`snapshot`, `--json classify` and `lessons stamp`) goes through kpy.run, in process. None of them writes os.environ or
reads INVOCATION: those belong to `gate` and to `brief-verify` without --list. test_classify_kpy_calls_leave_the_environment_alone pins that."""

import os
import re
import subprocess
import sys

from pstack_core.checkin import scope_entries
from pstack_core.errors import Die, Exit
from pstack_core.herdr import agent_status
from pstack_core.store import header_field, pair_file

from .jqx import field
from .kpy import Kitchen

CLASSIFY_FAILED = 'kitchen.py classify failed; run kitchen.py validate in the repo'


def text_of(data):
    return data.decode('utf-8', 'surrogateescape')


def risk_of(brief):
    """A brief's or plan's risk class: the first word of its risk: header, which a master may follow with the reason."""
    words = [w for w in re.split(r'[ \t\n]+', header_field(brief, 'risk')) if w]
    return words[0] if words else ''


def jq(text, program, *args):
    """`jq -r PROGRAM <<<TEXT`: stdout without its trailing newlines; jq's own errors on stderr."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['jq', '-r', *args, program], input=(text + '\n').encode('utf-8', 'surrogateescape'), stdout=subprocess.PIPE, check=False)
    return text_of(r.stdout).rstrip('\n')


def jq_lines(text, program):
    """`jq -r PROGRAM <<<TEXT` with its output as it was printed: every line, each ending in a newline."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['jq', '-r', program], input=(text + '\n').encode('utf-8', 'surrogateescape'), stdout=subprocess.PIPE, check=False)
    return text_of(r.stdout)


def set_header(path, key, value):
    """Replaces the key's header line (the first one), or adds it after the plan: line (the first one): the bash's perl -i -pe. Bytes, as perl read
    them; a file with neither line is left as it was."""
    with open(path, 'rb') as f:
        lines = re.findall(rb'[^\n]*\n|[^\n]+', f.read())
    k, v = key.encode('utf-8', 'surrogateescape'), value.encode('utf-8', 'surrogateescape')
    exists = any(line.startswith(k + b':') for line in lines)
    done, out = False, []
    for line in lines:
        if not done and exists and line.startswith(k + b':'):
            line, done = k + b': ' + v + (b'\n' if line.endswith(b'\n') else b''), True
        elif not done and not exists and line.startswith(b'plan:'):
            line, done = line + k + b': ' + v + b'\n', True
        out.append(line)
    with open(path, 'wb') as f:
        f.write(b''.join(out))


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
    status, found = Kitchen(store, root).soft(['snapshot', f'{store}/kitchens'])
    if status != 0:
        raise Die('kitchen.py snapshot failed; run kitchen.py validate in the repo')
    return found.rstrip('\n')


def brief_kitchen(brief):
    """--config <snapshot> for the brief, as a list; empty for a repo kitchen."""
    snap = header_field(brief, 'kitchen')
    return [] if snap in ('', 'repo') else ['--config', snap]


def classify_brief(store, brief):
    """Stamps risk, profiles, verify and kitchen on the brief and prints the summary."""
    kit = Kitchen(store)
    globs = scope_entries(brief, 'may')
    if not globs:
        raise Die(f"{brief} lists nothing under Scope's may write:")
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
        raise Die(CLASSIFY_FAILED)
    risk = jq(classified, '.risk')
    # The master may have written a reason after the class (`escalated (contract change)`): rewrite the line only when the class changes.
    if risk_of(brief) != risk:
        set_header(brief, 'risk', risk)
    set_header(brief, 'profiles', jq(classified, '.profiles | join(", ") | if . == "" then "none" else . end'))
    set_header(brief, 'verify', jq(classified, '"\\(.verify.mode) by \\(.verify.kind) verifier"'))
    sys.stdout.write(f'risk: {risk}\n')
    status, _ = kit.soft([*cfg, 'lessons', 'stamp', brief], show=True)
    if status != 0:
        raise Die('kitchen.py lessons stamp failed; run kitchen.py lessons in the repo')
    sys.stdout.write(jq_lines(classified, '"profiles: \\(.profiles | join(", "))", "verify: \\(.verify.mode) by \\(.verify.kind) verifier", (.escalate[] | "escalate: \\(.)"), '
                                         '(.warnings[]? | "warn: \\(.)"), (if .bookkeeping then "bookkeeping: the Scope is outside the repo; gates only, nothing to escalate" else empty end)'))
    if risk == 'escalated' and agent_status(field(store, 'consultant', 'name')) == 'absent':
        sys.stdout.write(f'next: kitchen.sh consultant {store} --reason "{jq(classified, ".escalate[0]")}", then a plan round with it\n')


def classify(ctx, args):
    if len(args) != 2:
        raise Die('usage: kitchen.sh classify <store> <brief-path>')
    pair_file(args[0])
    if not os.path.isfile(args[1]):
        raise Die(f'brief not found: {args[1]}')
    classify_brief(args[0], args[1])
    return 0
