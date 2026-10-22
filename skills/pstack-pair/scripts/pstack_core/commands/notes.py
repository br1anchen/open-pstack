import glob
import os
import re
import sys

from ..errors import Die, unbound_arg
from ..collate import glob_sorted
from ..herdr import agent_status
from ..prime import prime_steer
from ..review import awk_equal, jq_raw, noted_through, open_notes, require_filled, review_base, steps_file
from ..store import (alt, dispatched_brief, event, fill_template, header_field, note_id, pair_file, load, pause_notice,
                     read_text, steer_notice, suffix_number, unit_name, write_text)


def blocking_lines(path):
    """awk '/^## Blocking/{p=1;next} /^## /{p=0} p'"""
    show, lines = False, []
    text = read_text(path)
    for line in text.removesuffix('\n').split('\n'):
        if line.startswith('## Blocking'):
            show = True
        elif line.startswith('## '):
            show = False
        elif show:
            lines.append(line)
    return lines


def notes(ctx, args):
    if len(args) != 1:
        raise Die('usage: pair.sh notes <store>')
    store = args[0]
    pair_file(store)
    brief = dispatched_brief(store)
    if not brief:
        print('notes: none open')
        return 0
    sys.stdout.write(pause_notice(store) + steer_notice(store))
    open_ = open_notes(store, brief)
    if not open_:
        print('notes: none open; go on with the next step')
        return 0
    print(f'notes: {len(open_)} open. Fix each Blocking item as a fixup commit before the next step, then record it: '
          f'pair.sh step {store} <sha> "<summary>" --resolves <n-ids>')
    for f in open_:
        sys.stdout.write(f'\n=== {note_id(f)} ({f})\n')
        for line in blocking_lines(f):
            sys.stdout.write(line + '\n')
    return 0


def followups(path, tag):
    """awk: the "- " bullets under "## Follow-ups" except "- none", tagged."""
    on, out = False, []
    for line in read_text(path).split('\n'):
        if line.startswith('## Follow-ups'):
            on = True
        elif line.startswith('## '):
            on = False
        elif on and line.startswith('- ') and line != '- none':
            out.append(f'- [{tag}] {line[2:]}\n')
    return out


def note(ctx, args):
    if len(args) != 2:
        raise Die('usage: pair.sh note <store> <note-draft-path>')
    store, draft = args
    if not os.path.isfile(draft):
        raise Die(f'note not found: {draft}')
    if not draft.endswith('.md.draft'):
        raise Die('publish the .md.draft file new-note printed')
    require_filled(ctx, draft)
    status = header_field(draft, 'status')
    if status not in ('blocking', 'clear'):
        raise Die(f'{draft} needs status: blocking or clear')
    path = draft.removesuffix('.draft')
    os.replace(draft, path)
    name = unit_name(path)
    unit = re.sub(r'-n[0-9]+$', '', name)
    k = suffix_number(name, 'n') or name
    lines = followups(path, f'{unit} n{k}')
    with open(f'{store}/followups.md', 'ab') as f:
        f.write(''.join(lines).encode('utf-8', 'surrogateescape'))
    event(store, 'master', 'note', unit, f'{k}:{status}')
    try:
        delivery = deliver_blocking_note(ctx, store, path) if status == 'blocking' and os.path.isfile(f'{store}/pair.json') else ''
    except Die:
        # the note is out; the master must also know it went undelivered
        print(f'published {path} ({status})')
        raise
    print(f'published {path} ({status}){delivery}')
    return 0


def deliver_blocking_note(ctx, store, path):
    """A blocking note reaches a working sidekick at once, the way a steer does: a Devin keeps a message sent mid-turn parked as queued, so
    its next `pair.sh notes` (every step boundary) lists the note; Prime takes it through its daemon; every other kind through a prompt.
    The text to append to the published line, empty when the sidekick is not working."""
    from .. import cli
    from .steer import herdr_ok, park_for_devin
    from .stop import role_value
    name = role_value(store, 'sidekick', 'name')
    if agent_status(name) != 'working':
        return ''
    message = f'{ctx.skill} NOTE {path}'
    kind = role_value(store, 'sidekick', 'kind')
    if kind == 'devin':
        park_for_devin(store, path, 'notice_notes')
        return f"; left for {name}: it lands at the sidekick's next {cli.PROG} step, notes, or progress, between tool calls"
    if kind == 'prime-agent':
        if not prime_steer(store, message):
            raise Die(f'Prime delivery failed for {name}', 2)
    elif not herdr_ok('agent', 'prompt', name, message):
        raise Die(f'herdr prompt failed for {name}', 2)
    return f'; sent to {name}'


def count_lines(path):
    """grep -c . : lines with at least one character."""
    return sum(1 for line in read_text(path).split('\n') if line)


def new_note(ctx, args):
    if len(args) < 2:
        raise Die('usage: pair.sh new-note <store> <NNN> [--through K]')
    store, seq, rest = args[0], args[1], args[2:]
    through = ''
    if rest and rest[0] == '--through':
        if len(rest) < 2:
            raise unbound_arg()
        through = rest[1]
    elif rest and rest[0] != '':
        raise Die(f'unknown option {rest[0]}')
    pair_file(store)
    briefs = glob_sorted(glob.glob(os.path.join(glob.escape(store), 'briefs', glob.escape(seq) + '-*.md')))
    if not briefs:
        raise Die(f'no brief for unit {seq}')
    unit = unit_name(briefs[0])
    file = steps_file(store, unit)
    if not (os.path.isfile(file) and os.path.getsize(file) > 0):
        raise Die(f'unit {seq} has no steps recorded')
    done = noted_through(store, unit)
    if not through:
        shown_map = alt(load(store).get('shown'), {})
        shown = alt(shown_map.get(unit) if isinstance(shown_map, dict) else None, '')
        shown = jq_raw(shown) if shown != '' else ''
        through = shown if re.fullmatch(r'-?[0-9]+', shown) and int(shown) > done else ''
    steps = count_lines(file)
    k = through or str(steps)
    if not (re.fullmatch(r'[0-9]+', k) and int(k) <= steps):
        raise Die(f'unit {seq} has no step {k}')
    if int(k) <= done:
        raise Die(f'unit {seq} is reviewed through step {k} already; wait for the next steps')
    base = review_base(store, unit)
    rows = (r.split('\t') for r in read_text(file).split('\n') if r)
    to = '\n'.join(c[1] if len(c) > 1 else '' for c in rows if awk_equal(c[0], k))
    os.makedirs(f'{store}/notes', exist_ok=True)
    path = f'{store}/notes/{unit}-n{k}.md.draft'
    write_text(path, fill_template(f'{ctx.skill_root}/references/note-template.md',
                                   {'UNIT': unit, 'K': k, 'RANGE': f'{base[:9]}..{to[:9]}'}))
    print(path)
    return 0
