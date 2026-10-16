import os
import re
import sys

from ..errors import Die
from ..review import open_notes, require_filled
from ..store import dispatched_brief, event, header_field, pair_file, pause_notice, read_text, steer_notice, unit_name


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
        m = re.fullmatch(r'.*-(n[0-9]+)', unit_name(f), re.S)
        sys.stdout.write(f'\n=== {m.group(1) if m else unit_name(f)} ({f})\n')
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
    m = re.fullmatch(r'.*-n([0-9]+)', name, re.S)
    k = m.group(1) if m else name
    lines = followups(path, f'{unit} n{k}')
    with open(f'{store}/followups.md', 'ab') as f:
        f.write(''.join(lines).encode('utf-8', 'surrogateescape'))
    event(store, 'master', 'note', unit, f'{k}:{status}')
    print(f'published {path} ({status})')
    return 0
