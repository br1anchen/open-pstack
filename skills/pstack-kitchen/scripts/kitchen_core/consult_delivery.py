"""consult: the core's consult, after the consultant's parts of the unit's playbook are written into the consult file. A brief's parts come from its stamped composition, a contract-2 plan's from its blocks; a unit with neither sends the file as it is."""

import os
import re

from pstack_core.consult import consult as core_consult
from pstack_core.store import header_field, read_text

from .classify import unit_number
from .playbook_delivery import (
    part_lines,
    plan_compositions,
    playbooks,
    put_section,
    read_composition,
    refuse_open_fences,
    render_plan_blocks,
    shared_parts,
    write_if_changed,
)

CONSULT_SECTION = '## Playbook (your parts)'
CONSULT_BEFORE = ('## Reply',)


def brief_section(store, unit):
    """The consultant's parts of a brief's stamped composition, or '' when it has none."""
    composition = read_composition(store, unit_number(unit))
    if composition is None:
        return ''
    refuse_open_fences(composition, ('consultant',))
    shown = shared_parts(composition, ('consultant',))
    if not shown:
        return ''
    ids = [o['id'] for o in composition['overlays']['selected']]
    head = ' + '.join([composition['workflow'], *ids])
    lines = [CONSULT_SECTION, '', f"{head}, from the unit's stamped composition.", '']
    return '\n'.join(lines + part_lines(shown, False))


def plan_section(store, unit):
    """The consultant's parts per block of a contract-2 plan, or '' for a plan without that contract."""
    text = read_text(unit)
    if not playbooks().plan_contract(text):
        return ''
    blocks, compositions = plan_compositions(store, unit, text)
    if not blocks:
        return ''
    return render_plan_blocks(blocks, compositions, ('consultant',), CONSULT_SECTION)


def section_for(store, unit):
    """The section a consult on `unit` gets: a brief's, a plan's, or '' for any other path."""
    if not os.path.isfile(unit) or not re.match(r'\d', os.path.basename(unit)):
        return ''
    kind = os.path.basename(os.path.dirname(unit))
    if kind == 'briefs':
        return brief_section(store, unit)
    if kind == 'plans':
        return plan_section(store, unit)
    return ''


def deliver_consult_parts(store, path):
    """Writes the section into a consult file that has a `## Question` and a `## Reply`, and nothing into any other file; only when the bytes change."""
    try:
        text = read_text(path)
    except OSError:
        return
    pb = playbooks()
    starts = [text.split('\n')[i] for i in pb.section_headings(text.split('\n'))]
    if '## Question' not in starts or '## Reply' not in starts:
        return
    unit = header_field(path, 'unit').strip()
    section = section_for(store, unit) if unit else ''
    write_if_changed(path, put_section(text, section, CONSULT_SECTION, CONSULT_BEFORE))


def consult(ctx, args):
    if len(args) >= 2:
        deliver_consult_parts(args[0], os.path.realpath(args[1]))
    return core_consult(ctx, args)
