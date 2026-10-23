import glob
import os
import re
import sys

from .. import git
from ..argv import Spec, read
from ..store import count, dispatched_brief, event, field, fill_template, header_field, load, peek, note_id, text_at, pair_file, pause_notice, read_text, steer_notice, suffix_number, unit_name, write_text, new_from_template, newest
from ..errors import Die, PairError, PromptFailed
from ..panes import agent_prompt, agent_status, DEVNULL
from ..prime import prime_steer
from ..replies import sent_epoch
from ..review import noted_through, open_notes, require_filled, review_base
from ..steplog import StepLog


def blocking_lines(path):
    """The lines under the note's `## Blocking` heading."""
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
        raise Die(f'usage: {ctx.prog} notes <store>')
    store = args[0]
    pair_file(store)
    brief = dispatched_brief(store)
    if not brief:
        print('notes: none open')
        return 0
    sys.stdout.write(pause_notice(store) + steer_notice(ctx, store))
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
    """The "- " bullets under "## Follow-ups" except "- none", tagged."""
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
        raise Die(f'usage: {ctx.prog} note <store> <note-draft-path>')
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
    with open(f'{store}/followups.md', 'a', encoding='utf-8') as f:
        f.write(''.join(lines))
    event(store, 'master', 'note', unit, f'{k}:{status}')
    try:
        delivery = deliver_blocking_note(ctx, store, path) if status == 'blocking' and os.path.isfile(f'{store}/pair.json') else ''
    except PairError:
        # the note is out; the master must also know it went undelivered
        print(f'published {path} ({status})')
        raise
    print(f'published {path} ({status}){delivery}')
    if status == 'blocking':
        warn_after_report(ctx, store, unit)
    return 0


def warn_after_report(ctx, store, unit):
    """A blocking note on a unit whose report the master was already given reaches no one: the sidekick has finished with the unit. Says
    so, and the route that does reach it."""
    report = f'{store}/reports/{unit}.md'
    if not os.path.isfile(report):
        return
    sent = field(store, 'sent', 'file')
    if sent and re.sub(r'-a[0-9]+$', '', unit_name(sent)) == unit and int(os.lstat(report).st_mtime) < sent_epoch(store):
        return
    route = f' and verify it with --covers {unit[:3]}' if 'verifier' in ctx.roles else ''
    sys.stderr.write(f'{ctx.prog}: note: unit {unit[:3]} already reported {header_field(report, "status")}; the sidekick will not read this note: write a fix brief{route}\n')


def deliver_blocking_note(ctx, store, path):
    """A blocking note reaches a working sidekick at once, the way a steer does: a Devin keeps a message sent mid-turn parked as queued, so
    its next `pair.sh notes` (every step boundary) lists the note; Prime takes it through its daemon; every other kind through a prompt.
    The text to append to the published line, empty when the sidekick is not working."""
    from .steer import park_for_devin
    name = field(store, 'sidekick', 'name')
    if agent_status(name) != 'working':
        return ''
    message = f'{ctx.skill} NOTE {path}'
    kind = field(store, 'sidekick', 'kind')
    if kind == 'devin':
        park_for_devin(store, path, 'notice_notes')
        return f"; left for {name}: it lands at the sidekick's next {ctx.prog} step, notes, or progress, between tool calls"
    if kind == 'prime-agent':
        if not prime_steer(store, message):
            raise PromptFailed(f'Prime delivery failed for {name}')
    elif agent_prompt(name, message, stdout=DEVNULL, stderr=None).returncode != 0:
        raise PromptFailed(f'herdr prompt failed for {name}')
    return f'; sent to {name}'


def new_note(ctx, args):
    if len(args) < 2:
        raise Die(f'usage: {ctx.prog} new-note <store> <NNN> [--through K]')
    store, seq = args[0], args[1]
    through = read(args[2:], Spec(valued=('--through',))).last('--through', '')
    pair_file(store)
    briefs = sorted(glob.glob(os.path.join(glob.escape(store), 'briefs', glob.escape(seq) + '-*.md')))
    if not briefs:
        raise Die(f'no brief for unit {seq}')
    unit = unit_name(briefs[0])
    log = StepLog(store, unit)
    recorded = len(log.rows())
    if not recorded:
        if dispatched_brief(store) and unit_name(dispatched_brief(store)) == unit and through in ('', '0'):
            if os.path.exists(f'{store}/notes/{unit}-n0.md'):
                raise Die(f'unit {seq} already has a published n0; wait for its first step')
            return new_first_note(ctx, store, unit)
        raise Die(f'unit {seq} has no steps recorded')
    done = noted_through(store, unit)
    if not through:
        shown = count(peek(load(store), 'shown'), unit)
        through = str(shown) if shown > done else ''
    k = through or str(recorded)
    if not (re.fullmatch(r'[0-9]+', k) and int(k) <= recorded):
        raise Die(f'unit {seq} has no step {k}')
    if int(k) <= done:
        raise Die(f'unit {seq} is reviewed through step {k} already; wait for the next steps')
    base = review_base(store, unit)
    step = log.at(int(k))
    to = step.sha if step else ''
    os.makedirs(f'{store}/notes', exist_ok=True)
    path = f'{store}/notes/{unit}-n{k}.md.draft'
    write_text(path, fill_template(f'{ctx.skill_root}/references/note-template.md',
                                   {'UNIT': unit, 'K': k, 'RANGE': f'{base[:9]}..{to[:9]}'}))
    print(path)
    return 0


def new_first_note(ctx, store, unit):
    """n0: a note on the dispatched unit before its first step, over the range from the unit's base to the tree's HEAD (through: step 0). A
    published n0 is never overwritten; the notes after it keep their step ranges."""
    pair = load(store)
    head = git.out(text_at(pair, 'git_root') or text_at(pair, 'cwd'), 'rev-parse', '--verify', '--quiet', 'HEAD') or ''
    os.makedirs(f'{store}/notes', exist_ok=True)
    path = f'{store}/notes/{unit}-n0.md.draft'
    write_text(path, fill_template(f'{ctx.skill_root}/references/note-template.md',
                                   {'UNIT': unit, 'K': '0', 'RANGE': f'{review_base(store, unit)[:9]}..{head[:9]}'}))
    print(path)
    return 0


def new_plan(ctx, args):
    if len(args) != 2:
        raise Die(f'usage: {ctx.prog} new-plan <store> <slug>')
    print(new_from_template(ctx, args[0], args[1], 'plans', 'plan-template.md'))
    return 0


def new_brief(ctx, args):
    if len(args) != 2:
        raise Die(f'usage: {ctx.prog} new-brief <store> <slug>')
    print(new_from_template(ctx, args[0], args[1], 'briefs', 'brief-template.md'))
    return 0


def new_steer(ctx, args):
    """Fresh steers on a unit are capped at two; one that supersedes an objected steer is a discussion round."""
    if len(args) < 2:
        raise Die(f'usage: {ctx.prog} new-steer <store> <NNN> [--supersedes STEER | --force]')
    store, seq = args[0], args[1]
    opts = read(args[2:], Spec(valued=('--supersedes',), flags=('--force',)))
    force = opts.present('--force')
    supersedes = os.path.realpath(opts.last('--supersedes')) if opts.present('--supersedes') else 'none'
    pair_file(store)
    briefs = sorted(glob.glob(os.path.join(glob.escape(store), 'briefs', glob.escape(seq) + '-*.md')))
    if not briefs:
        raise Die(f'no brief for unit {seq}')
    slug = unit_name(briefs[0])[4:]
    # a dangling symlink is no steer: the glob alone lists one
    existing = [f for f in glob.glob(os.path.join(glob.escape(store), 'steers', f'{glob.escape(seq)}-{glob.escape(slug)}-s[0-9]*.md')) if os.path.exists(f)]
    k = len(existing) + 1
    fresh = sum(1 for f in existing if header_field(f, 'supersedes') == 'none')
    if supersedes != 'none':
        if not os.path.isfile(supersedes):
            raise Die(f'superseded steer not found: {supersedes}')
        reports = glob.glob(os.path.join(glob.escape(store), 'reports', f'{glob.escape(seq)}-{glob.escape(slug)}-s[0-9]*.md'))
        if header_field(newest(reports) or '', 'status') != 'object':
            raise Die(f'no open objection on unit {seq} to answer; send a fresh steer instead')
    elif fresh >= 2 and not force:
        raise Die(f'unit {seq} already has two fresh steers; stop the unit and re-brief, or pass --force', 7)
    os.makedirs(f'{store}/steers', exist_ok=True)
    path = f'{store}/steers/{seq}-{slug}-s{k}.md'
    write_text(path, fill_template(f'{ctx.skill_root}/references/steer-template.md',
                                   {'SEQ': seq, 'SLUG': slug, 'K': str(k), 'STORE': store, 'SUPERSEDES': supersedes}))
    print(path)
    return 0
