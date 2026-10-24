"""classify: stamps a brief's risk, profiles, verify mode and kitchen from its Scope.

Every kitchen.py query here (`snapshot`, `classify_change` and `lessons_stamp`) is a function of kpy.kitchen_py that returns data. None of them writes
os.environ or builds a child's command line (`child_args`, `child_env`): those belong to `gate` and to `brief-verify` without --list. test_classify_kpy_calls_leave_the_environment_alone pins that."""

import json
import os
import re
import sys
from pathlib import Path

from pstack_core.checkin import scope_entries
from pstack_core.errors import Die, EmptyScope, KitchenPyFailed, Unfilled
from pstack_core.panes import agent_status
from pstack_core.review import placeholder_line
from pstack_core.store import (
    field,
    header_field,
    pair_file,
    read_text,
    risk_of,
    write_text,
)

from . import kpy
from .landing import section

ACCEPTANCE_COMMAND = re.compile(r'`((?:git|python3|test|grep|awk|uv|bash) [^`\n]+)`')

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
    if (
        plan not in ('', 'none')
        and os.path.isfile(plan)
        and risk_of(plan) == 'escalated'
    ):
        reasons.append(f'escalated plan: {os.path.basename(plan)}')
    return reasons


def kitchen_snapshot(store, root=None):
    """A host kitchen has no history, so a brief is classified, stepped and verified against the copy taken when it was classified (kitchen: in its
    header), the way a repo kitchen is read at the unit's commit. The copy's path, or nothing for a repo kitchen."""
    try:
        return kpy.kitchen_py.snapshot(
            kpy.invocation(store, root), Path(f'{store}/kitchens')
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(
            'kitchen.py snapshot failed; run kitchen.py validate in the repo'
        ) from None


def brief_kitchen(brief):
    """--config <snapshot> for the brief, as a list; empty for a repo kitchen."""
    snap = header_field(brief, 'kitchen')
    return [] if snap in ('', 'repo') else ['--config', snap]


def normal_form(command):
    return ' '.join(command.replace('git diff --exit-code', 'git diff').split())


def runs_in(command, verify):
    """Whether `command` occurs in `verify` whole, with a command boundary on each side."""
    return bool(
        re.search(
            r'(?<![^\s(;|&"\'])' + re.escape(command) + r'(?![^\s);|&"\'])', verify
        )
    )


def acceptance_warnings(text, gates):
    """One warning per backquoted command in Acceptance that neither Verify nor a gate runs, so `finish` would not rerun it."""
    verify = normal_form(section(text, 'Verify'))
    covered = {normal_form(g) for g in gates}
    warned = {}
    for command in ACCEPTANCE_COMMAND.findall(section(text, 'Acceptance')):
        normal = normal_form(command)
        if normal in covered or runs_in(normal, verify):
            continue
        warned.setdefault(
            normal,
            f'Acceptance runs {command}, which Verify does not; add it to Verify (wrap a "prints nothing" check as test -z "$(...)")',
        )
    return list(warned.values())


def header_values(brief, key):
    """Every `key: value` line in the brief's header (above its first `## ` section), in order."""
    out = []
    for line in read_text(brief).split('\n'):
        if line.startswith('## '):
            break
        if line.startswith(f'{key}:'):
            out.append(line[len(key) + 1 :].strip())
    return out


def overlay_directives(brief):
    """The brief's `with:` ids and its `without:` (id, reason) pairs; the reason follows an em dash or `--`."""
    with_ids = tuple(
        i.strip()
        for v in header_values(brief, 'with')
        for i in v.split(',')
        if i.strip()
    )
    without = []
    for v in header_values(brief, 'without'):
        parts = re.split(r'\s+(?:—|--)\s+', v, maxsplit=1)
        oid, reason = parts[0], parts[1] if len(parts) > 1 else ''
        without.append((oid.strip(), reason.strip()))
    return with_ids, tuple(without)


def compose_playbooks(store, brief, globs, config):
    """The brief's composition from kitchen.py, or None when the brief names no playbook. An unmapped `playbook:` or a bad overlay directive refuses the classify here; no other reader re-selects."""
    workflow = header_field(brief, 'playbook').strip()
    if workflow in ('', 'none'):
        return None
    text = read_text(brief)
    if (n := placeholder_line(text)) is not None:
        raise Unfilled(
            f'{brief} still has unfilled placeholders: line {n}: {text.split(chr(10))[n - 1].strip()}'
        )
    with_ids, without = overlay_directives(brief)
    try:
        return kpy.kitchen_py.compose(
            kpy.invocation(store, config=config),
            workflow,
            globs,
            with_ids=with_ids,
            without=without,
        ) | {'with': list(with_ids), 'without': [list(w) for w in without]}
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(
            'kitchen.py compose failed; fix the brief header (playbook:, with:, without:)'
        ) from None


COMPOSITION_VERSION = 1


def unit_number(brief):
    number = re.match(r'\d+', os.path.basename(brief))
    return number.group(0) if number else os.path.splitext(os.path.basename(brief))[0]


def composition_path(store, unit):
    return Path(store) / 'compositions' / f'{unit}.json'


def unstamp_composition(store, brief):
    """Removes a stamp an earlier classify left for a brief that no longer composes a playbook."""
    composition_path(store, unit_number(brief)).unlink(missing_ok=True)


def stamp_composition(store, brief, composition):
    """Writes `<store>/compositions/<NNN>.json` for the brief and returns whether the bytes changed. Later readers use this file and never select again."""
    path = composition_path(store, unit_number(brief))
    text = (
        json.dumps(
            {'version': COMPOSITION_VERSION, **composition}, indent=2, sort_keys=True
        )
        + '\n'
    )
    if path.is_file() and read_text(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    write_text(tmp, text)
    os.replace(tmp, path)
    return True


def composition_lines(composition):
    """The classify output for a composition: the workflow and overlays, each exclusion, then the master's parts."""
    ids = [o['id'] for o in composition['overlays']['selected']]
    lines = [f'playbooks: {" + ".join([composition["workflow"], *ids])}']
    lines += [
        f'excluded: {o["id"]} ({o["reason"]})'
        for o in composition['overlays']['excluded']
    ]
    for part in composition['roles']['master']:
        first = next((x.strip() for x in part['text'].split('\n') if x.strip()), '')
        first = first.lstrip('#').strip()
        lines.append(f'master: {part["id"]} {first[:100]}')
    return lines


def classify_brief(store, brief):
    """Stamps risk, profiles, verify and kitchen on the brief and prints the summary."""
    globs = scope_entries(brief, 'may')
    if not globs:
        raise EmptyScope(f"{brief} lists nothing under Scope's may write:")
    snap = kitchen_snapshot(store)
    if snap:
        set_header(brief, 'kitchen', snap)
    elif header_field(brief, 'kitchen'):
        set_header(brief, 'kitchen', 'repo')
    cfg = brief_kitchen(brief)
    composition = compose_playbooks(store, brief, globs, cfg[1] if cfg else None)
    try:
        data = kpy.kitchen_py.classify_change(
            kpy.invocation(store, config=cfg[1] if cfg else None),
            paths=globs,
            escalate=tuple(brief_escalations(brief)),
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(CLASSIFY_FAILED) from None
    risk, verify = data.get('risk') or '', data['verify']
    profiles, escalate = data['profiles'], data['escalate']
    verify_line = f'{verify["mode"]} by {verify["kind"]} verifier'
    # The master may have written a reason after the class (`escalated (contract change)`): rewrite the line only when the class changes.
    if risk_of(brief) != risk:
        set_header(brief, 'risk', risk)
    set_header(brief, 'profiles', ', '.join(profiles) or 'none')
    set_header(brief, 'verify', verify_line)
    sys.stdout.write(f'risk: {risk}\n')
    try:
        picked = kpy.kitchen_py.lessons_stamp(
            kpy.invocation(store, config=cfg[1] if cfg else None), Path(brief)
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(
            'kitchen.py lessons stamp failed; run kitchen.py lessons in the repo'
        ) from None
    if picked:
        sys.stdout.write(f'lessons: {" ".join(x.id for x in picked)}\n')
    lines = [
        f'profiles: {", ".join(profiles)}',
        f'verify: {verify_line}',
        *(f'escalate: {reason}' for reason in escalate),
    ]
    lines += [f'warn: {warning}' for warning in data.get('warnings') or []]
    lines += [
        f'warn: {warning}'
        for warning in acceptance_warnings(read_text(brief), data.get('gates') or [])
    ]
    if data.get('bookkeeping'):
        lines.append(
            'bookkeeping: the Scope is outside the repo; gates only, nothing to escalate'
        )
    sys.stdout.write(''.join(f'{line}\n' for line in lines))
    if not composition:
        unstamp_composition(store, brief)
    if composition:
        stamp_composition(store, brief, composition)
        sys.stdout.write(
            ''.join(f'{line}\n' for line in composition_lines(composition))
        )
    first_reason = escalate[0] if escalate else ''
    if (
        risk == 'escalated'
        and agent_status(field(store, 'consultant', 'name')) == 'absent'
    ):
        sys.stdout.write(
            f'next: kitchen.sh consultant {store} --reason "{first_reason}", then a plan round with it\n'
        )


def classify(ctx, args):
    if len(args) != 2:
        raise Die('usage: kitchen.sh classify <store> <brief-path>')
    pair_file(args[0])
    if not os.path.isfile(args[1]):
        raise Die(f'brief not found: {args[1]}')
    classify_brief(args[0], args[1])
    return 0
