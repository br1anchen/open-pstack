"""Delivery of the stamped playbook composition: the sidekick's parts go into the brief at dispatch, and a pending read is resolved from the stored composition, never from the live source."""

import hashlib
import json
import os
import re
import sys

from pstack_core import mux
from pstack_core.errors import Die, PairError
from pstack_core.store import (
    field,
    load,
    member,
    peek,
    read_text,
    text_at,
    unit_name,
    update,
    write_text,
)

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
        return '\n'.join(lines[:own] + body + tail)
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


class PlanRefused(PairError):
    """A contract-2 plan with a block that cannot be composed; the message is one line per problem."""

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


POINTER = re.compile(
    r'^Unchanged from briefs/(\S+\.md), which this session already read; reread it there\.$'
)


def section_span(lines, prefix):
    """(first line, line after the last) of the brief's section whose heading starts with `prefix`, None when it has none."""
    starts = playbooks().section_headings(lines)
    own = next((i for i in starts if lines[i].startswith(prefix)), None)
    if own is None:
        return None
    return own, next((i for i in starts if i > own), len(lines))


def restore_report(store, brief):
    """A brief whose `## Report` section was made a pointer to the brief that carried it (lean_sections) has the section back in full: dispatch
    renders the brief afresh each time, and a session that started over needs the whole text."""
    lines = read_text(brief).split('\n')
    span = section_span(lines, '## Report')
    found = span and POINTER.match(lines[span[0] + 2] if span[0] + 2 < span[1] else '')
    carrier = f'{store}/briefs/{found.group(1)}' if found and span else ''
    if not (span and os.path.isfile(carrier)):
        return
    full = read_text(carrier).split('\n')
    carried = section_span(full, '## Report')
    if carried:
        lines[span[0] : span[1]] = full[carried[0] : carried[1]]
        write_if_changed(brief, '\n'.join(lines))


def deliver(store, brief):
    """Puts the sidekick's rendering of the unit's composition into the brief; returns (parts, tokens, pending) or None when the unit has no composition."""
    restore_report(store, brief)
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


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def lean_sections(store, brief):
    """Where a role keeps its conversation across units (Amp threads), a brief whose playbook and report sections the sidekick's session already
    holds carries a pointer to the brief that carried them in place of both: `.sidekick.delivered` is the generation, the digests of the two
    sections and the brief that carried them in full, and the compaction count then. A new generation (a spawn, a rotation or a failover), a
    changed composition, a compaction since, or the carrier itself being sent again gets the sections in full, and records them. Returns the
    pointed-to brief, or ''."""
    m = mux.get()
    if not m.reuses_sessions:
        return ''
    lines = read_text(brief).split('\n')
    spans = {k: section_span(lines, k) for k in ('## Playbook:', '## Report')}
    if not all(spans.values()):
        return ''
    # a pending read names the unit's own number; the composition is the same for any unit
    unit = unit_number(brief)
    digest = {
        'playbook': sha(
            '\n'.join(lines[slice(*spans['## Playbook:'])]).replace(
                f'playbook-part {store} {unit} ', 'playbook-part STORE NNN '
            )
        ),
        'report': sha('\n'.join(lines[slice(*spans['## Report'])])),
    }
    sidekick = peek(load(store), 'sidekick')
    name, unit_id = text_at(sidekick, 'name'), unit_name(brief)
    compactions = m.compactions(name)
    held = peek(sidekick, 'delivered')
    carrier = text_at(held, 'brief')
    if (
        held.get('generation') == field(store, 'sidekick', 'generation')
        and all(held.get(k) == v for k, v in digest.items())
        and carrier
        and carrier != unit_id
        and os.path.isfile(f'{store}/briefs/{carrier}.md')
        and compactions is not None
        and compactions <= (held.get('compactions') or 0)
    ):
        pointer = f'Unchanged from briefs/{carrier}.md, which this session already read; reread it there.'
        for key in ('## Report', '## Playbook:'):
            first, end = spans[key]
            lines[first:end] = [lines[first], '', pointer, '']
            spans = {k: section_span(lines, k) for k in spans}
        write_if_changed(brief, '\n'.join(lines))
        return carrier

    def mutate(doc):
        # the delivered sections: .sidekick.delivered
        member(doc, 'sidekick')['delivered'] = {
            'generation': field(store, 'sidekick', 'generation'),
            **digest,
            'brief': unit_id,
            'compactions': compactions or 0,
        }

    update(store, mutate)
    return ''


def plan_context(store):
    """The overlays and source roots a plan's `with:` ids resolve against: the kitchen's own when the store has a run, else none."""
    pb = playbooks()
    if not os.path.isfile(f'{store}/pair.json'):
        return [], pb.default_roots()
    return kpy.call(kpy.kitchen_py.kitchen_overlays, kpy.invocation(store))


def part_lines(shown, labelled=True):
    """The `####` entries of parts given as (roles, part) pairs, each titled with the roles it is for unless `labelled` is off."""
    lines = []
    for roles, part in shown:
        title = part['id'] + (f' ({", ".join(roles)})' if labelled else '')
        lines += [
            f'#### {title}',
            '',
            playbooks().demote_headings(part['text']).strip(),
            '',
        ]
    return lines


def shared_parts(composition, roles):
    """The parts of `roles` in order, each once with the roles that are given it: a part with the same id and text for two roles is one entry."""
    shown: dict[tuple[str, str], list] = {}
    for role in roles:
        for part in composition['roles'].get(role) or []:
            entry = shown.setdefault((part['id'], part['text']), [[], part])
            entry[0].append(role)
    return [tuple(entry) for entry in shown.values()]


def render_plan_blocks(
    blocks, compositions, roles=('consultant', 'master'), heading=PLAN_SECTION
):
    """A `heading` section for a plan: per block the parts of `roles`, each part once with the roles it is for, its text whole."""
    names = ' and '.join(roles)
    lines = [
        heading,
        '',
        "Written by `kitchen.sh` from each Steps block's `playbook:` and `with:`; edit those lines, not this section.",
        '',
    ]
    for block, composition in zip(blocks, compositions, strict=True):
        ids = [o['id'] for o in composition['overlays']['selected']]
        lines += [f'### Block {block.number}: {" + ".join([block.playbook, *ids])}', '']
        shown = shared_parts(composition, roles)
        lines += (
            part_lines(shown, len(roles) > 1) if shown else [f'No {names} parts.', '']
        )
    return '\n'.join(lines)


def plan_compositions(store, plan, text):
    """The blocks of a contract-2 plan and the composition of each. Raises PlanRefused (status 2), a line per problem, for a block that names no playbook or an unmapped one, or that does not compose."""
    pb = playbooks()
    blocks = pb.plan_blocks(text)
    data = pb.load_map(pb.MAP_PATH)
    if problems := pb.plan_problems(blocks, sorted(data['playbook'])):
        raise PlanRefused('\n'.join(f'{plan}: {p}' for p in problems))
    overlays, roots = plan_context(store)
    compositions = []
    for block in blocks:
        try:
            composition = pb.compose_plan(data, overlays, roots, block)
        except pb.PlaybookError as e:
            raise PlanRefused(f'{plan}: {e}') from None
        refuse_open_fences(composition, ('consultant', 'master'))
        compositions.append(composition)
    return blocks, compositions


def deliver_plan_blocks(store, plan):
    """Puts the consultant's and master's parts of a contract-2 plan's blocks into the plan, writing only when the bytes change. Returns (blocks, tokens), or None for a plan without `contract: 2` (a warning, no section)."""
    pb = playbooks()
    text = read_text(plan)
    if not pb.plan_contract(text):
        sys.stdout.write('warn: plan has no contract: 2; no playbook section\n')
        return None
    blocks, compositions = plan_compositions(store, plan, text)
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
