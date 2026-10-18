import glob
import os
import re
import subprocess

from .errors import Die
from .collate import glob_sorted
from .store import alt, header_field, jq_raw, last_line, note_id, pair_json, read_text, suffix_number, unit_name


def steps_file(store, unit):
    return f'{store}/steps/{unit_name(unit)}.tsv'


def note_files(store, unit):
    """notes/<unit>-n<k>.md in glob order; a .md.draft is not published."""
    return glob_sorted(f for f in glob.glob(os.path.join(glob.escape(store), 'notes', glob.escape(unit_name(unit)) + '-n[0-9]*.md'))
                       if os.path.exists(f))


def note_number(path):
    k = suffix_number(unit_name(path), 'n')
    return int(k) if k else None


def noted_through(store, unit):
    return max([n for n in map(note_number, note_files(store, unit)) if n is not None], default=0)


def open_notes(store, brief):
    unit = unit_name(brief)
    file = steps_file(store, unit)
    resolved = set()
    if os.path.isfile(file):
        for row in read_text(file).split('\n'):
            cols = row.split('\t')
            if len(cols) > 3:
                resolved.update(cols[3].split(','))
    found = []
    for f in note_files(store, unit):
        if header_field(f, 'status') != 'blocking':
            continue
        if note_id(f) not in resolved:
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


def awk_equal(field, k):
    """awk's $1 == k with both sides numeric strings: numeric when both look numeric, else text."""
    try:
        return float(field) == float(k)
    except ValueError:
        return field == str(k)


def review_base(store, unit):
    unit = unit_name(unit)
    through = noted_through(store, unit)
    file = steps_file(store, unit)
    if through > 0 and os.path.isfile(file):
        rows = (r.split('\t') for r in read_text(file).split('\n') if r)
        return '\n'.join(c[1] if len(c) > 1 else '' for c in rows if awk_equal(c[0], through))
    pair = pair_json(store)
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


def prose_lines(text):
    """The lines of text outside fenced code blocks (a line of three backticks opens one and the next closes it), with backtick spans removed:
    code may hold a double opening brace (a shell expansion, a template literal) and is not a placeholder."""
    lines, fenced = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        elif not fenced:
            lines.append(re.sub(r'`[^`]*`', '', line))
    return lines


def has_placeholder(text):
    """A complete placeholder pair (two opening braces, text without a closing brace, two closing braces) in the prose of text."""
    return re.search(r'\x7b\x7b[^}]*\x7d\x7d', '\n'.join(prose_lines(text))) is not None


def require_filled(ctx, path):
    try:
        text = read_text(path)
    except OSError:
        return
    if has_placeholder(text) or (ctx.unfilled_re and any(re.search(ctx.unfilled_re, line) for line in prose_lines(text))):
        raise Die(f'{path} still has unfilled placeholders')
