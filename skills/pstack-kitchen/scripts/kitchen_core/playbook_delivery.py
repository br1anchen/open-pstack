"""Delivery of the stamped playbook composition: the sidekick's parts go into the brief at dispatch, and a pending read is resolved from the stored composition, never from the live source."""

import json
import os
import re
from pathlib import Path

from pstack_core.errors import Die
from pstack_core.store import read_text, write_text

from . import kpy
from .classify import brief_kitchen

SECTION = re.compile(r'^## Playbook:.*\n(?:(?!## ).*\n?)*', re.M)
LESSONS = re.compile(r'^## Lessons', re.M)
ROLES = ('master', 'sidekick', 'consultant', 'verifier')


def unit_number(brief):
    number = re.match(r'\d+', os.path.basename(brief))
    return number.group(0) if number else os.path.splitext(os.path.basename(brief))[0]


def composition_path(store, unit):
    return Path(store) / 'compositions' / f'{unit}.json'


def read_composition(store, unit):
    """The stamped composition of a unit, or None when classify never stamped one."""
    path = composition_path(store, unit)
    if not path.is_file():
        return None
    return json.loads(read_text(path))


def put_section(text, section):
    """The brief text with its `## Playbook:` section replaced by `section` (removed when empty). A new section goes before `## Lessons`, which stays last for the lessons stamp, else at the end."""
    found = SECTION.search(text)
    if found:
        tail = text[found.end() :]
        body = section + '\n' if section and tail else section
        return text[: found.start()] + body + tail
    if not section:
        return text
    lessons = LESSONS.search(text)
    if lessons:
        return text[: lessons.start()] + section + '\n' + text[lessons.start() :]
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


def deliver(store, brief):
    """Puts the sidekick's rendering of the unit's composition into the brief; returns (parts, tokens, pending) or None when the unit has no composition."""
    unit = unit_number(brief)
    composition = read_composition(store, unit)
    if composition is None:
        return None
    pb = kpy.kitchen_py.playbooks_module()
    section, pending = pb.render_role(
        composition, 'sidekick', target_tokens(store, brief), store=store, unit=unit
    )
    write_if_changed(brief, put_section(read_text(brief), section))
    return (
        len(composition['roles'].get('sidekick') or []),
        pb.estimate_tokens(section),
        len(pending),
    )


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
