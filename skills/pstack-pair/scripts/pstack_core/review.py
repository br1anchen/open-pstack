import functools
import glob
import os
import re

from . import git
from .steplog import StepLog
from .store import (
    header_field,
    note_id,
    load,
    peek,
    read_text,
    suffix_number,
    text_at,
    unit_name,
)
from .errors import Unfilled


def note_files(store, unit):
    """notes/<unit>-n<k>.md in glob order; a .md.draft is not published."""
    return sorted(
        f
        for f in glob.glob(
            os.path.join(
                glob.escape(store),
                'notes',
                glob.escape(unit_name(unit)) + '-n[0-9]*.md',
            )
        )
        if os.path.exists(f)
    )


def note_number(path):
    k = suffix_number(unit_name(path), 'n')
    return int(k) if k else None


def noted_through(store, unit):
    return max(
        [n for n in map(note_number, note_files(store, unit)) if n is not None],
        default=0,
    )


def open_notes(store, brief):
    unit = unit_name(brief)
    resolved, qualified = set(), set()
    # a bare id (n2) resolves this unit's note; a qualified one (001-n2), written by a step on any unit, resolves unit 001's
    for log in StepLog.all(store):
        for step in log.rows():
            for note in step.resolves:
                if re.match(r'[0-9]{3}-', note):
                    qualified.add(note)
                elif log.unit == unit:
                    resolved.add(note)
    seq = unit[:3]
    found = []
    for f in note_files(store, unit):
        if header_field(f, 'status') != 'blocking':
            continue
        if note_id(f) not in resolved and f'{seq}-{note_id(f)}' not in qualified:
            found.append(f)
    return found


def trunk_ref(root):
    current = git.out(root, 'symbolic-ref', '--quiet', 'HEAD') or ''
    origin = git.out(root, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD') or ''
    for ref in [
        *origin.split(),
        'refs/remotes/origin/main',
        'refs/remotes/origin/master',
        'refs/heads/main',
        'refs/heads/master',
    ]:
        if (
            ref != current
            and git.out(root, 'rev-parse', '--verify', '--quiet', ref) is not None
        ):
            return ref
    return None


def range_floor(root, base, tip, allow_head=False):
    """(rev, note): base, or where the unit's branch leaves trunk when the recorded base is behind it."""
    trunk = trunk_ref(root)
    if not trunk:
        return base, ''
    mb = git.out(root, 'merge-base', base, tip)
    if mb is None:
        mb = base
    tmb = git.out(root, 'merge-base', trunk, tip)
    if tmb is None or tmb == mb or (tmb == tip and not allow_head):
        return base, ''
    if not git.ok(root, 'merge-base', '--is-ancestor', mb, tmb):
        return base, ''
    trunk_name = trunk.removeprefix('refs/')
    return (
        tmb,
        f'recorded base {base[:9]} is behind where its branch leaves {trunk_name}; measuring from {tmb[:9]}',
    )


def review_base(store, unit):
    unit = unit_name(unit)
    through = noted_through(store, unit)
    log = StepLog(store, unit)
    if through > 0 and os.path.isfile(log.path):
        step = log.at(through)
        return step.sha if step else ''
    pair = load(store)
    base = text_at(peek(pair, 'heads'), unit, None)
    if base is None:
        base = text_at(peek(pair, 'dispatch'), 'head')
    root = text_at(pair, 'git_root') or text_at(pair, 'cwd')
    last = log.last()
    tip = (
        last.sha
        if last
        else git.out(root, 'rev-parse', '--verify', '--quiet', 'HEAD') or ''
    )
    if base and tip:
        base = range_floor(root, base, tip)[0]
    return base


PAIR = r'\x7b\x7b[^}]*\x7d\x7d'


@functools.cache
def template_pairs():
    """Every complete placeholder pair that occurs in one of the skills' templates (skills/*/references/*-template.md beside this module's
    skills directory), once per process: the exact text a template leaves for its writer to fill."""
    skills = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    found = set()
    for path in glob.glob(
        os.path.join(glob.escape(skills), '*', 'references', '*-template.md')
    ):
        try:
            found.update(re.findall(PAIR, read_text(path)))
        except OSError:
            pass
    return found


def split_code(text):
    """(prose lines, code pieces) of text, each as (text, first line number). A fence opens on a line whose stripped start is three or more
    backticks or tildes and closes on a later line of that character alone, at least as long; an unclosed fence is prose to the end, and a line
    that opens and closes a fence on itself opens nothing. In the prose lines, a backtick span (matched by run length) is cut out and becomes a
    code piece."""
    lines = text.split('\n')
    prose, code, i, unclosed = [], [], 0, False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        m = None if unclosed else re.match(r'(`{3,}|~{3,})', stripped)
        if m and m.group(1)[0] * len(m.group(1)) not in stripped[len(m.group(1)) :]:
            fence = m.group(1)
            close = next(
                (
                    j
                    for j in range(i + 1, len(lines))
                    if re.fullmatch(
                        re.escape(fence[0]) + '{' + str(len(fence)) + ',}',
                        lines[j].strip(),
                    )
                ),
                None,
            )
            if close is not None:
                code.append(('\n'.join(lines[i + 1 : close]), i + 2))
                i = close + 1
                continue
            unclosed = True
        out, pos = [], 0
        while pos < len(line):
            run = re.compile('`+').search(line, pos)
            if not run:
                break
            k = len(run.group())
            end = next(
                (
                    r
                    for r in re.finditer('`+', line[run.end() :])
                    if len(r.group()) == k
                ),
                None,
            )
            if end is None:
                out.append(line[pos : run.end()])
                pos = run.end()
                continue
            out.append(line[pos : run.start()] + ' ')
            code.append((line[run.end() : run.end() + end.start()], i + 1))
            pos = run.end() + end.end()
        out.append(line[pos:])
        prose.append((''.join(out), i + 1))
        i += 1
    return prose, code


def prose_lines(text):
    return [line for line, _ in split_code(text)[0]]


def placeholder_line(text):
    """The number of the first line with an unfilled placeholder, or None: a complete pair in the prose, or, inside a fence or a backtick span,
    a pair whose exact text is one a template leaves to be filled (so code that holds some other double-brace text, a CI matrix expression say,
    is not one)."""
    prose, code = split_code(text)
    found = []
    joined = '\n'.join(line for line, _ in prose)
    m = re.search(PAIR, joined)
    if m:
        found.append(prose[joined[: m.start()].count('\n')][1])
    templates = template_pairs()
    for piece, first in code:
        for m in re.finditer(PAIR, piece):
            if m.group() in templates:
                found.append(first + piece[: m.start()].count('\n'))
                break
    return min(found, default=None)


def has_placeholder(text):
    return placeholder_line(text) is not None


def require_filled(ctx, path):
    try:
        text = read_text(path)
    except OSError:
        return
    found = [placeholder_line(text)] if has_placeholder(text) else []
    if ctx.unfilled_re:
        found += [
            n for line, n in split_code(text)[0] if re.search(ctx.unfilled_re, line)
        ][:1]
    if found:
        n = min(found)
        raise Unfilled(
            f'{path} still has unfilled placeholders: line {n}: {text.split(chr(10))[n - 1].strip()}'
        )
