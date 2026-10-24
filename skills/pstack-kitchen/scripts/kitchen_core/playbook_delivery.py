"""Delivery of the stamped playbook composition: the sidekick's parts go into the brief at dispatch, and a pending read is resolved from the stored composition, never from the live source."""

import json
import os
import sys

from pstack_core.errors import Die, Exit, PairError
from pstack_core.store import read_text, write_text

from . import kpy
from .classify import brief_kitchen, composition_path, unit_number
from .landing import playbooks

BEFORE = ('## Report', '## Lessons')
PLAN_SECTION = '## Playbook (plan blocks)'
PLAN_BEFORE = (
    '## Verification',
    '## Risks',
    '## Open questions',
    '## Disagreements resolved',
)
ROLES = ('master', 'sidekick', 'consultant', 'verifier')


def read_composition(store, unit):
    """The stamped composition of a unit, or None when classify never stamped one."""
    path = composition_path(store, unit)
    if not path.is_file():
        return None
    return json.loads(read_text(path))


def put_section(text, section, prefix='## Playbook:', before=BEFORE):
    """The brief text with its `## Playbook:` section replaced by `section` (removed when empty). A new section goes before `## Report`, so the sidekick reads its parts before the report instructions, else before `## Lessons`, else at the end. A plan names its own heading `prefix` and the headings `before` which a new section goes."""
    lines = text.split('\n')
    starts = playbooks().section_headings(lines)
    own = next((i for i in starts if lines[i].startswith(prefix)), None)
    body = section.rstrip('\n').split('\n') + [''] if section else []
    if own is not None:
        end = next((i for i in starts if i > own), len(lines))
        tail = lines[end:]
        return '\n'.join(lines[:own] + (body if tail else body[:-1]) + tail)
    if not section:
        return text
    for title in before:
        at = next((i for i in starts if lines[i] == title), None)
        if at is not None:
            return '\n'.join(lines[:at] + body + lines[at:])
    return text.rstrip('\n') + '\n\n' + section


def write_if_changed(path, text):
    """Writes through a temporary file and a rename, and not at all when the bytes are already there."""
    if read_text(path) == text:
        return False
    tmp = f'{path}.tmp'
    write_text(tmp, text)
    os.replace(tmp, path)
    return True


def target_tokens(store, brief):
    """`[playbooks].target_tokens` of the kitchen the brief was classified against."""
    cfg = brief_kitchen(brief)
    return kpy.call(
        kpy.kitchen_py.playbook_target_tokens,
        kpy.invocation(store, config=cfg[1] if cfg else None),
    )


class OpenFence(PairError):
    code = 2


def refuse_open_fences(composition, roles=('sidekick', 'verifier')):
    """Dies when a part delivered to one of `roles` leaves a code fence open: it would hide the sections after it."""
    pb = playbooks()
    for role in roles:
        for part in composition['roles'].get(role) or []:
            if pb.scan_fences(part['text'].split('\n'))[1] is not None:
                raise OpenFence(
                    f'playbook part {part["id"]} ({role}) leaves a code fence open; close it in the part or its kitchen text'
                )


def deliver(store, brief):
    """Puts the sidekick's rendering of the unit's composition into the brief; returns (parts, tokens, pending) or None when the unit has no composition."""
    unit = unit_number(brief)
    composition = read_composition(store, unit)
    if composition is None:
        write_if_changed(brief, put_section(read_text(brief), ''))
        return None
    pb = playbooks()
    refuse_open_fences(composition)
    section, pending = pb.render_role(
        composition, 'sidekick', target_tokens(store, brief), store=store, unit=unit
    )
    write_if_changed(brief, put_section(read_text(brief), section))
    return (
        len(composition['roles'].get('sidekick') or []),
        pb.estimate_tokens(section),
        len(pending),
    )


def plan_context(store):
    """The overlays and source roots a plan's `with:` ids resolve against: the kitchen's own when the store has a run, else none."""
    pb = playbooks()
    if not os.path.isfile(f'{store}/pair.json'):
        return [], pb.default_roots()
    return kpy.call(kpy.kitchen_py.kitchen_overlays, kpy.invocation(store))


def render_plan_blocks(blocks, compositions):
    """The `## Playbook (plan blocks)` section: per block the consultant's and then the master's parts, each part once with the roles it is for, its text whole."""
    pb = playbooks()
    lines = [
        PLAN_SECTION,
        '',
        "Written by `kitchen.sh discuss` from each Steps block's `playbook:` and `with:`; edit those lines, not this section.",
        '',
    ]
    for block, composition in zip(blocks, compositions, strict=True):
        ids = [o['id'] for o in composition['overlays']['selected']]
        lines += [f'### Block {block.number}: {" + ".join([block.playbook, *ids])}', '']
        shown: dict[tuple[str, str], list] = {}
        for role in ('consultant', 'master'):
            for part in composition['roles'].get(role) or []:
                entry = shown.setdefault((part['id'], part['text']), [[], part])
                entry[0].append(role)
        if not shown:
            lines += ['No consultant or master parts.', '']
        for roles, part in shown.values():
            lines += [
                f'#### {part["id"]} ({", ".join(roles)})',
                '',
                pb.demote_headings(part['text']).strip(),
                '',
            ]
    return '\n'.join(lines)


def deliver_plan_blocks(store, plan):
    """Puts the consultant's and master's parts of a contract-2 plan's blocks into the plan, writing only when the bytes change. Returns (blocks, tokens), or None for a plan without `contract: 2` (a warning, no section). Dies with status 2, a line per problem, for a block that names no playbook or an unmapped one."""
    pb = playbooks()
    text = read_text(plan)
    if not pb.plan_contract(text):
        sys.stdout.write('warn: plan has no contract: 2; no playbook section\n')
        return None
    blocks = pb.plan_blocks(text)
    data = pb.load_map(pb.MAP_PATH)
    if problems := pb.plan_problems(blocks, sorted(data['playbook'])):
        raise Exit(2, ''.join(f'kitchen.sh: {plan}: {p}\n' for p in problems))
    overlays, roots = plan_context(store)
    compositions = []
    for block in blocks:
        try:
            composition = pb.compose_plan(data, overlays, roots, block)
        except pb.PlaybookError as e:
            raise Die(f'{plan}: {e}', 2) from None
        refuse_open_fences(composition, ('consultant', 'master'))
        compositions.append(composition)
    section = render_plan_blocks(blocks, compositions) if blocks else ''
    write_if_changed(plan, put_section(text, section, PLAN_SECTION, PLAN_BEFORE))
    return len(blocks), pb.estimate_tokens(section)


def provenance(part):
    src = part['source']
    origins = ', '.join(dict.fromkeys(x['by'] for x in part.get('from') or []))
    return (
        f'from {origins}, {src["root"]}:{src["path"]} {src["select"]} {src["digest"]}'
    )


def playbook_part(ctx, args):
    """kitchen.sh playbook-part <store> <NNN> <role> <part-id>: the stored effective text of one part and where it came from."""
    if len(args) != 4:
        raise Die('usage: kitchen.sh playbook-part <store> <NNN> <role> <part-id>')
    store, unit, role, part_id = args
    composition = read_composition(store, unit)
    if composition is None:
        raise Die(f'playbook-part: unit {unit} has no stored composition', 2)
    if role not in ROLES:
        raise Die(f'playbook-part: unknown role {role} ({", ".join(ROLES)})', 2)
    parts = composition['roles'].get(role) or []
    part = next((p for p in parts if p['id'] == part_id), None)
    if part is None:
        have = ', '.join(p['id'] for p in parts) or 'none'
        raise Die(
            f'playbook-part: {role} has no part {part_id} in unit {unit} (parts: {have})',
            2,
        )
    print(part['text'].rstrip('\n'))
    print()
    print(provenance(part))
    return 0
