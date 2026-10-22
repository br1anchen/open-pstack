"""new-handoff: session-handoff.md from the template, with every unit's state. Ported from kitchen.sh at b632713."""

import glob
import os
import re
import sys
import time

from pstack_core.collate import glob_sorted, newest
from pstack_core.errors import Die
from pstack_core.store import field
from pstack_core.store import alt, header_field, jq_raw, pair_file, load


def basename(path):
    return os.path.basename(path.rstrip('/')) if path.rstrip('/') else '/'


def newest_matching(pattern, skip_packets=False):
    """`ls -t <pattern> | [grep -v packet] | head -1`: the newest match by mtime, ties by collated name; '' when none."""
    paths = [p for p in glob.glob(pattern) if os.path.lexists(p)]
    if skip_packets:
        paths = [p for p in paths if 'packet' not in p]
    return newest(paths) or ''


def queued_brief(store):
    try:
        with open(f'{store}/queue', 'rb') as f:
            return f.read().decode('utf-8', 'surrogateescape').rstrip('\n')
    except OSError:
        return ''


def handoff_units(store):
    """The table of units and what is known of each, then what is dispatched, being verified and queued."""
    out = ['| unit | report | verdict | review |', '|---|---|---|---|']
    esc = glob.escape(store)
    for brief in glob_sorted(glob.glob(f'{esc}/briefs/[0-9][0-9][0-9]-*.md')):
        if not os.path.exists(brief):
            continue
        name = os.path.basename(brief)
        seq, slug = name[:3], name[:-3][4:]
        report = newest_matching(f'{esc}/reports/{seq}-*.md')
        rstatus = header_field(report, 'status') if report else '-'
        verdict = newest_matching(f'{esc}/verdicts/{seq}-*-v[0-9]*.md', skip_packets=True)
        vstatus = header_field(verdict, 'status') if verdict else '-'
        review = f'{store}/reviews/{seq}-{slug}.md'
        rv = header_field(review, 'verdict') if os.path.isfile(review) else '-'
        out.append(f'| {seq} {slug} | {rstatus or "-"} | {vstatus or "-"} | {rv or "-"} |')
    text = '\n'.join(out) + '\n'
    # jq answers for a .dispatch or .verifying that is not an object: its error on stderr, an empty value, and the command goes on
    brief = field(store, 'dispatch', 'brief', empty=True)
    if brief:
        text += f'\ndispatched: {basename(brief)}\n'
    unit = field(store, 'verifying', 'unit', empty=True)
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
    pair = load(store)
    path = f'{store}/session-handoff.md'
    # a plain assignment: a .master that is not an object ends the command with jq's status and error, before any file is written
    generation = field(store, 'master', 'generation', default='1')
    units = handoff_units(store).rstrip('\n')
    with open(f'{ctx.skill_root}/references/handoff-template.md', encoding='utf-8', errors='surrogateescape') as f:
        text = f.read()
    for key, value in {'SLUG': jq_raw(pair.get('slug')), 'STORE': store, 'GENERATION': generation,
                       'WRITTEN': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'UNITS': units}.items():
        text = text.replace('{{' + key + '}}', value)
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(text)
    sys.stdout.write(path + '\n')
    sys.stdout.write(f'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master {store}\n')
    return 0
