"""new-handoff: session-handoff.md from the template, with every unit's state.."""

import glob
import os
import sys
import time

from pstack_core.errors import Die
from pstack_core.replies import queued_brief
from pstack_core.store import (
    basename,
    field,
    fill_template,
    header_field,
    newest,
    pair_file,
    read,
    write_text,
)


def newest_matching(pattern, skip_packets=False):
    """`ls -t <pattern> | [grep -v packet] | head -1`: the newest match by mtime, ties by name; '' when none."""
    paths = [p for p in glob.glob(pattern) if os.path.lexists(p)]
    if skip_packets:
        paths = [p for p in paths if 'packet' not in p]
    return newest(paths) or ''


def handoff_units(store):
    """The table of units and what is known of each, then what is dispatched, being verified and queued."""
    out = ['| unit | report | verdict | review |', '|---|---|---|---|']
    esc = glob.escape(store)
    for brief in sorted(glob.glob(f'{esc}/briefs/[0-9][0-9][0-9]-*.md')):
        if not os.path.exists(brief):
            continue
        name = os.path.basename(brief)
        seq, slug = name[:3], name[:-3][4:]
        report = newest_matching(f'{esc}/reports/{seq}-*.md')
        rstatus = header_field(report, 'status') if report else '-'
        verdict = newest_matching(
            f'{esc}/verdicts/{seq}-*-v[0-9]*.md', skip_packets=True
        )
        vstatus = header_field(verdict, 'status') if verdict else '-'
        review = f'{store}/reviews/{seq}-{slug}.md'
        rv = header_field(review, 'verdict') if os.path.isfile(review) else '-'
        out.append(
            f'| {seq} {slug} | {rstatus or "-"} | {vstatus or "-"} | {rv or "-"} |'
        )
    text = '\n'.join(out) + '\n'
    # a .dispatch or .verifying that is not an object is damage, found where it is read
    brief = field(store, 'dispatch', 'brief')
    if brief:
        text += f'\ndispatched: {basename(brief)}\n'
    unit = field(store, 'verifying', 'unit')
    if unit:
        text += f'verifying: {unit}\n'
    queued = queued_brief(store)
    if queued:
        text += f'queued: {basename(queued)}\n'
    return text


def new_handoff(ctx, args):
    if len(args) != 1:
        raise Die('usage: kitchen.sh new-handoff <store>')
    store = args[0]
    pair_file(store)
    path = f'{store}/session-handoff.md'
    # a .master that is not an object ends the command before any file is written
    generation = field(store, 'master', 'generation', default='1')
    units = handoff_units(store).rstrip('\n')
    write_text(
        path,
        fill_template(
            f'{ctx.skill_root}/references/handoff-template.md',
            {
                'SLUG': read(store, 'slug'),
                'STORE': store,
                'GENERATION': generation,
                'WRITTEN': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'UNITS': units,
            },
        ),
    )
    sys.stdout.write(path + '\n')
    sys.stdout.write(
        f'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master {store}\n'
    )
    return 0
