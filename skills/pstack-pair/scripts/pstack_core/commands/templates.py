import glob
import os

from ..errors import Die
from ..collate import glob_sorted, newest
from ..store import fill_template, header_field, new_from_template, pair_file, readlink_f, unit_name, write_text


def new_plan(ctx, args):
    if len(args) != 2:
        raise Die('usage: pair.sh new-plan <store> <slug>')
    print(new_from_template(ctx, args[0], args[1], 'plans', 'plan-template.md'))
    return 0


def new_brief(ctx, args):
    if len(args) != 2:
        raise Die('usage: pair.sh new-brief <store> <slug>')
    print(new_from_template(ctx, args[0], args[1], 'briefs', 'brief-template.md'))
    return 0


def new_steer(ctx, args):
    """Fresh steers on a unit are capped at two; one that supersedes an objected steer is a discussion round."""
    if len(args) < 2:
        raise Die('usage: pair.sh new-steer <store> <NNN> [--supersedes STEER | --force]')
    store, seq, rest = args[0], args[1], args[2:]
    force, supersedes = False, 'none'
    while rest:
        if rest[0] == '--force':
            force, rest = True, rest[1:]
        elif rest[0] == '--supersedes':
            if len(rest) < 2:
                raise Die('$2: unbound variable')
            supersedes, rest = readlink_f(rest[1]), rest[2:]
        else:
            raise Die(f'unknown option {rest[0]}')
    pair_file(store)
    briefs = glob_sorted(glob.glob(os.path.join(glob.escape(store), 'briefs', glob.escape(seq) + '-*.md')))
    if not briefs:
        raise Die(f'no brief for unit {seq}')
    slug = unit_name(briefs[0])[4:]
    existing = glob.glob(os.path.join(glob.escape(store), 'steers', f'{glob.escape(seq)}-{glob.escape(slug)}-s[0-9]*.md'))
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
