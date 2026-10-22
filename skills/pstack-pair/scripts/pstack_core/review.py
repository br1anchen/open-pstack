import glob
import os
import re
import subprocess

from .errors import Die
from .store import alt, header_field, int_or_none, jq_raw, last_line, note_id, load, read_text, suffix_number, unit_name


def steps_file(store, unit):
    return f'{store}/steps/{unit_name(unit)}.tsv'


def note_files(store, unit):
    """notes/<unit>-n<k>.md in glob order; a .md.draft is not published."""
    return sorted(f for f in glob.glob(os.path.join(glob.escape(store), 'notes', glob.escape(unit_name(unit)) + '-n[0-9]*.md'))
                       if os.path.exists(f))


def note_number(path):
    k = suffix_number(unit_name(path), 'n')
    return int(k) if k else None


def noted_through(store, unit):
    return max([n for n in map(note_number, note_files(store, unit)) if n is not None], default=0)


def open_notes(store, brief):
    unit = unit_name(brief)
    file = steps_file(store, unit)
    resolved, qualified = set(), set()
    # a bare id (n2) resolves this unit's note; a qualified one (001-n2), written by a step on any unit, resolves unit 001's
    for steps in glob.glob(os.path.join(glob.escape(store), 'steps', '*.tsv')):
        for row in read_text(steps).split('\n'):
            cols = row.split('\t')
            if len(cols) > 3:
                for note in cols[3].split(','):
                    if re.match(r'[0-9]{3}-', note):
                        qualified.add(note)
                    elif os.path.normpath(steps) == os.path.normpath(file):
                        resolved.add(note)
    seq = unit[:3]
    found = []
    for f in note_files(store, unit):
        if header_field(f, 'status') != 'blocking':
            continue
        if note_id(f) not in resolved and f'{seq}-{note_id(f)}' not in qualified:
            found.append(f)
    return found


def git(root, *args):
    """stdout of git -C root, or None when it fails; stderr is dropped."""
    r = subprocess.run(['git', '-C', root, *args], capture_output=True, check=False)
    return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n') if r.returncode == 0 else None


def trunk_ref(root):
    current = git(root, 'symbolic-ref', '--quiet', 'HEAD') or ''
    origin = git(root, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD') or ''
    for ref in [*origin.split(), 'refs/remotes/origin/main', 'refs/remotes/origin/master', 'refs/heads/main', 'refs/heads/master']:
        if ref != current and git(root, 'rev-parse', '--verify', '--quiet', ref) is not None:
            return ref
    return None


def range_floor(root, base, tip, allow_head=False):
    """(rev, note): base, or where the unit's branch leaves trunk when the recorded base is behind it."""
    trunk = trunk_ref(root)
    if not trunk:
        return base, ''
    mb = git(root, 'merge-base', base, tip)
    if mb is None:
        mb = base
    tmb = git(root, 'merge-base', trunk, tip)
    if tmb is None or tmb == mb or (tmb == tip and not allow_head):
        return base, ''
    if subprocess.run(['git', '-C', root, 'merge-base', '--is-ancestor', mb, tmb], capture_output=True, check=False).returncode != 0:
        return base, ''
    trunk_name = trunk.removeprefix('refs/')
    return tmb, f'recorded base {base[:9]} is behind where its branch leaves {trunk_name}; measuring from {tmb[:9]}'


def review_base(store, unit):
    unit = unit_name(unit)
    through = noted_through(store, unit)
    file = steps_file(store, unit)
    if through > 0 and os.path.isfile(file):
        rows = (r.split('\t') for r in read_text(file).split('\n') if r)
        return '\n'.join(c[1] if len(c) > 1 else '' for c in rows if int_or_none(c[0]) == through)
    pair = load(store)
    heads = alt(pair.get('heads'), {})
    dispatch = alt(pair.get('dispatch'), {})
    base = alt(heads.get(unit) if isinstance(heads, dict) else None,
               alt(dispatch.get('head') if isinstance(dispatch, dict) else None, ''))
    base = jq_raw(base)
    root = jq_raw(alt(pair.get('git_root'), pair.get('cwd')))
    if os.path.isfile(file) and os.path.getsize(file) > 0:
        tail = last_line(file)
        tip = tail.split('\t')[1] if '\t' in tail else tail
    else:
        tip = git(root, 'rev-parse', '--verify', '--quiet', 'HEAD') or ''
    if base and tip:
        base = range_floor(root, base, tip)[0]
    return base


PAIR = r'\x7b\x7b[^}]*\x7d\x7d'

_template_pairs = None


def template_pairs():
    """Every complete placeholder pair that occurs in one of the skills' templates (skills/*/references/*-template.md beside this module's
    skills directory), once per process: the exact text a template leaves for its writer to fill."""
    global _template_pairs
    if _template_pairs is None:
        skills = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        found = set()
        for path in glob.glob(os.path.join(glob.escape(skills), '*', 'references', '*-template.md')):
            try:
                found.update(re.findall(PAIR, read_text(path)))
            except OSError:
                pass
        _template_pairs = found
    return _template_pairs


def split_code(text):
    """(prose lines, code pieces) of text. A fence opens on a line whose stripped start is three or more backticks or tildes and closes on a
    later line of that character alone, at least as long; an unclosed fence is prose to the end, and a line that opens and closes a fence on
    itself opens nothing. In the prose lines, a backtick span (matched by run length) is cut out and becomes a code piece."""
    lines = text.split('\n')
    prose, code, i, unclosed = [], [], 0, False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        m = None if unclosed else re.match(r'(`{3,}|~{3,})', stripped)
        if m and m.group(1)[0] * len(m.group(1)) not in stripped[len(m.group(1)):]:
            fence = m.group(1)
            close = next((j for j in range(i + 1, len(lines)) if re.fullmatch(re.escape(fence[0]) + '{' + str(len(fence)) + ',}', lines[j].strip())), None)
            if close is not None:
                code.append('\n'.join(lines[i + 1:close]))
                i = close + 1
                continue
            unclosed = True
        out, pos = [], 0
        while pos < len(line):
            run = re.compile('`+').search(line, pos)
            if not run:
                break
            k = len(run.group())
            end = next((r for r in re.finditer('`+', line[run.end():]) if len(r.group()) == k), None)
            if end is None:
                out.append(line[pos:run.end()])
                pos = run.end()
                continue
            out.append(line[pos:run.start()] + ' ')
            code.append(line[run.end():run.end() + end.start()])
            pos = run.end() + end.end()
        out.append(line[pos:])
        prose.append(''.join(out))
        i += 1
    return prose, code


def prose_lines(text):
    return split_code(text)[0]


def has_placeholder(text):
    """An unfilled placeholder: a complete pair in the prose, or, inside a fence or a backtick span, a pair whose exact text is one a template
    leaves to be filled (so code that holds some other double-brace text, a CI matrix expression say, is not one)."""
    prose, code = split_code(text)
    if re.search(PAIR, '\n'.join(prose)):
        return True
    templates = template_pairs()
    return any(pair in templates for piece in code for pair in re.findall(PAIR, piece))


def require_filled(ctx, path):
    try:
        text = read_text(path)
    except OSError:
        return
    if has_placeholder(text) or (ctx.unfilled_re and any(re.search(ctx.unfilled_re, line) for line in prose_lines(text))):
        raise Die(f'{path} still has unfilled placeholders')
