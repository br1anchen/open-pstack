"""The check-in digest and what it reads: the sidekick's tree, its processes, and the brief's Scope."""
import calendar
import glob
import os
import re
import subprocess
import time

from .collate import collate_key, glob_sorted
from .replies import dispatched, file_mtime
from .review import jq_raw, noted_through, open_notes, range_floor, steps_file
from .store import alt, cut_chars, int_or_none, header_field, load, progress_path, read_text

SHELLS = ('bash', 'sh', 'zsh', 'dash', 'fish')
CLASSES = {'alpha': 'A-Za-z', 'digit': '0-9', 'alnum': '0-9A-Za-z', 'upper': 'A-Z', 'lower': 'a-z', 'space': ' \t\n\r\f\v',
           'blank': ' \t', 'punct': '!-/:-@\\[-`{-~', 'print': ' -~', 'graph': '!-~', 'cntrl': '\x00-\x1f\x7f', 'xdigit': '0-9A-Fa-f'}


def tdiv(a, b):
    """Shell arithmetic division: truncates toward zero."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def git_out(root, *args):
    """stdout of git -C root, rstripped of newlines as a command substitution does, or None when git fails."""
    r = subprocess.run(['git', '-C', root, *args], capture_output=True, check=False)
    return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n') if r.returncode == 0 else None


def status_entries(root):
    """(status, path) of each entry git status lists, NUL-delimited; a rename or copy counts at its new path."""
    r = subprocess.run(['git', '-C', root, 'status', '--porcelain=v1', '-z', '--untracked-files=all'], capture_output=True, check=False)
    raw = r.stdout.decode('utf-8', 'surrogateescape').split('\0')
    if raw and raw[-1] == '':
        raw.pop()
    entries, skip = [], False
    for entry in raw:
        if skip:
            skip = False
            continue
        if entry[:1] in ('R', 'C'):
            skip = True
        entries.append((entry[:2], entry[3:]))
    return entries


def status_paths(root):
    return [path for _, path in status_entries(root)]


def tree_activity(cwd, head, now):
    """The newest sign of work in the sidekick's tree as an epoch, or None when the tree is quiet."""
    newest = 0
    for _, path in status_entries(cwd):
        try:
            t = file_mtime(f'{cwd}/{path}')
        except OSError:
            continue
        if t > now + 60:
            continue
        t = min(t, now)
        newest = max(newest, t)
    commits = 0
    if head:
        counted = git_out(cwd, 'rev-list', '--count', f'{head}..HEAD')
        commits = int(counted) if counted is not None and counted.isdigit() else 0
    if commits > 0:
        stamp = git_out(cwd, 'log', '-1', '--format=%ct')
        t = int(stamp) if stamp is not None and stamp.isdigit() else 0
        newest = max(newest, min(t, now))
    return newest or None


def iso_epoch(stamp):
    """jq fromdateiso8601, or None where jq fails: C strptime("%Y-%m-%dT%H:%M:%SZ") with its leading blanks, one or two digits
    a field and day 31 allowed in any month, then timegm, whose result -1 reads as an error. Anything after the Z must start with a blank."""
    m = re.fullmatch(r'\s*(\d{1,4})-\s*(\d{1,2})-\s*(\d{1,2})T\s*(\d{1,2}):\s*(\d{1,2}):\s*(\d{1,2})Z(?:\s.*)?', stamp, re.DOTALL)
    if not m:
        return None
    year, month, day, hour, minute, second = map(int, m.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59 and second <= 61):
        return None
    epoch = calendar.timegm((year, month, day, hour, minute, second))
    return None if epoch == -1 else epoch


def busy_command(pane, since, now, env=None):
    """The longest-running process carrying the pane's HERDR_PANE_ID that started since the dispatch, preferring a
    command over the shell that runs it, as "<command> for Nm"; or ''. Linux only."""
    env = os.environ if env is None else env
    if not pane or not os.path.isdir('/proc'):
        return ''
    sock = env.get('HERDR_SOCKET_PATH', '')
    r = subprocess.run(['ps', '-u', str(os.getuid()), '-o', 'pid=,etimes=,comm='], capture_output=True, check=False)
    best, best_et, shell, shell_et = '', -1, '', -1
    for line in r.stdout.decode('utf-8', 'surrogateescape').split('\n'):
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, et, comm = parts[0], int(parts[1]), parts[2]
        if not now - et > since:
            continue
        try:
            with open(f'/proc/{pid}/environ', 'rb') as f:
                lines = f.read().decode('utf-8', 'surrogateescape').replace('\0', '\n').split('\n')
        except OSError:
            continue
        if f'HERDR_PANE_ID={pane}' not in lines:
            continue
        if sock and f'HERDR_SOCKET_PATH={sock}' not in lines:
            continue
        if comm in SHELLS:
            if et > shell_et:
                shell_et, shell = et, comm
        elif et > best_et:
            best_et, best = et, comm
    if not best:
        best, best_et = shell, shell_et
    return f'{best} for {tdiv(best_et, 60)}m' if best else ''


def sidekick_activity(store, now):
    """The longest-running process in the sidekick's pane started since the current dispatch, or ''."""
    pair = load(store)
    sidekick, dispatch = alt(pair.get('sidekick'), {}), alt(pair.get('dispatch'), {})
    pane = jq_raw(alt(sidekick.get('pane_id') if isinstance(sidekick, dict) else None, ''))
    at = jq_raw(alt(dispatch.get('at') if isinstance(dispatch, dict) else None, ''))
    if not pane or not at:
        return ''
    since = iso_epoch(at)
    return '' if since is None else busy_command(pane, since, now)


def scope_entries(brief, want):
    """A brief's Scope entries for 'may' or 'mustnot': the text after "- " without an annotation or a trailing
    "(...)" note; one pair of backquotes is removed and what is inside kept whole."""
    wanted, found = False, []
    try:
        lines = read_text(brief).removesuffix('\n').split('\n')
    except OSError:
        return found
    for line in lines:
        if line.startswith('may write:'):
            wanted = want == 'may'
            continue
        if line.startswith('must not write:'):
            wanted = want == 'mustnot'
            continue
        if line.startswith('## '):
            wanted = False
        if wanted and line.startswith('- '):
            entry = line[2:]
            if entry[:1] == '`':
                entry = entry[1:]
                end = entry.find('`')
                if end >= 0:
                    entry = entry[:end]
            else:
                entry = re.sub(r' +(?:—|–|--) .*$', '', entry, count=1)
                entry = re.sub(r' +\(.*\)$', '', entry, count=1)
            if entry:
                found.append(entry)
    return found


def steer_scope_entries(store, seq, errexit=True):
    """The paths a unit's live steers allow beyond its brief: the `scope effect: may write ...` line of each steer
    that is not a withdraw and that no later steer withdraws by `supersedes:`. A line with a quote and no quoted
    string fails the bash grep pipeline, which ends the function there when set -e is on (as in the digest)."""
    files = glob_sorted(glob.glob(os.path.join(glob.escape(store), 'steers', glob.escape(seq) + '-*-s[0-9]*.md')))
    withdrawn = ' '
    for f in files:
        if os.path.isfile(f) and header_field(f, 'kind') == 'withdraw':
            sup = header_field(f, 'supersedes').split(' ')[0]
            if sup != 'none':
                withdrawn += os.path.basename(sup.rstrip('/') or sup) + ' '
    found = []
    for f in files:
        if not os.path.isfile(f) or header_field(f, 'kind') == 'withdraw':
            continue
        if f' {os.path.basename(f)} ' in withdrawn:
            continue
        line = header_field(f, 'scope effect')
        if not line.startswith('may write'):
            continue
        # the paths come before a note (` \u2014 <note>`), which may quote words of its own: quoted or not is decided by what precedes it
        line = line.split(' \u2014 ')[0]
        if '"' in line:
            quoted = re.findall(r'"[^"]*"', line)
            if not quoted and errexit:
                break
            found.extend(q.replace('"', '').replace('`', '') for q in quoted)
        else:
            rest = line[len('may write'):]
            rest = rest[1:] if rest.startswith(':') else rest
            rest = rest[1:] if rest.startswith(' ') else rest
            rest = rest.split(' ')[0].replace('`', '')
            if rest:
                found.append(rest)
    return found


def case_pattern(pattern):
    """A compiled regex for the shell's case pattern: * and ? match any character including /, [..] sets with
    ! or ^ negation, ranges and [:class:], and backslash quoting."""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == '\\':
            out.append(re.escape(pattern[i + 1]) if i + 1 < n else re.escape('\\'))
            i += 2
        elif c == '*':
            out.append('.*')
            i += 1
        elif c == '?':
            out.append('.')
            i += 1
        elif c == '[':
            j, parts, negate = i + 1, [], False
            if j < n and pattern[j] in '!^':
                negate, j = True, j + 1
            first = True
            closed = False
            while j < n:
                d = pattern[j]
                if d == ']' and not first:
                    closed = True
                    break
                first = False
                if d == '[' and pattern[j + 1:j + 2] == ':' and ':]' in pattern[j + 2:]:
                    name = pattern[j + 2:pattern.index(':]', j + 2)]
                    parts.append(CLASSES.get(name, ''))
                    j = pattern.index(':]', j + 2) + 2
                    continue
                if d == '\\' and j + 1 < n:
                    d, j = pattern[j + 1], j + 1
                if pattern[j + 1:j + 2] == '-' and j + 2 < n and pattern[j + 2] != ']':
                    hi = pattern[j + 2]
                    if hi == '\\' and j + 3 < n:
                        hi, j = pattern[j + 3], j + 1
                    parts.append(f'{re.escape(d)}-{re.escape(hi)}')
                    j += 3
                    continue
                parts.append(re.escape(d))
                j += 1
            if closed:
                body = ''.join(parts)
                out.append(('[^' if negate else '[') + body + ']' if body else ('(?s:.)' if negate else '(?!)'))
                i = j + 1
            else:
                out.append(re.escape('['))
                i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile(''.join(out), re.DOTALL)


def scope_pattern(pattern):
    """case_pattern for a Scope entry: `**/` stands for zero or more directories, so `d/**/x` matches `d/x` as well as `d/a/x`. Everything
    else is the shell's case pattern."""
    return re.compile('(?:.*/)?'.join(case_pattern(piece).pattern for piece in pattern.split('**/')), re.DOTALL)


def case_match(pattern, text):
    return case_pattern(pattern).fullmatch(text) is not None


def sort_u(lines):
    """sort -u: collation order, lines that compare equal count once."""
    key = collate_key()
    out, last = [], None
    for line in sorted(lines, key=key):
        if last is None or key(line) != last:
            out.append(line)
        last = key(line)
    return out


def checkin(ctx, store, interval=9, now=None):
    """The check-in digest for a sidekick still working at the interval, bounded so the master can poll it cheaply."""
    now = int(time.time()) if now is None else now
    out = []
    pair = load(store)
    brief = dispatched(store)
    if not brief or not os.path.isfile(brief):
        return ''
    dispatch = alt(pair.get('dispatch'), {})
    at = jq_raw(alt(dispatch.get('at'), ''))
    head = jq_raw(alt(dispatch.get('head'), ''))
    cwd = jq_raw(pair.get('cwd'))
    floor_note = ''
    tip = git_out(cwd, 'rev-parse', '--verify', '--quiet', 'HEAD') or ''
    if head and tip:
        head, floor_note = range_floor(cwd, head, tip, allow_head=True)
    base = os.path.basename(brief.rstrip('/') or brief)
    seq = cut_chars(base, 3)
    m = re.match(r'[0-9]+', header_field(brief, 'timebox'))
    timebox = m.group(0) if m else ''
    since = iso_epoch(at)
    elapsed = '?' if since is None else str(tdiv(now - since, 60))
    out.append(f'check-in: {base}  elapsed {elapsed}m of {timebox or "?"}m\n')
    run = sidekick_activity(store, now)
    prog = progress_path(store)
    stale = ''
    if os.path.isfile(prog):
        text = read_text(prog)
        total = text.count('\n')
        seen = 0
        if os.path.isfile(f'{prog}.seen'):
            seen = int_or_none(read_text(f'{prog}.seen')) or 0
        if seen > total:
            seen = 0
        new = total - seen
        age_m = tdiv(now - file_mtime(prog), 60)
        limit = interval
        if age_m >= limit:
            if run:
                stale = f'  running: {run}'
            else:
                active = tree_activity(cwd, head, now)
                if active and now - active < (limit if limit > 0 else 1) * 60:
                    stale = f'  editing: last change {tdiv(now - active, 60)}m ago'
                else:
                    stale = '  STALE'
        out.append(f'progress: +{new} lines (last {age_m}m ago){stale}\n')
        lines = text.split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        out.extend(f'  {line}\n' for line in lines[seen:][-20:])
        with open(f'{prog}.seen', 'w') as f:
            f.write(f'{total}\n')
    else:
        out.append('progress: none yet\n')
    if run and 'running:' not in stale:
        out.append(f'running: {run}\n')
    touched = status_paths(cwd)
    if head:
        diff = git_out(cwd, 'diff', '--name-only', f'{head}..HEAD')
        touched += diff.split('\n') if diff else []
    touched = sort_u(touched)
    commits = 0
    if head:
        counted = git_out(cwd, 'rev-list', '--count', f'{head}..HEAD')
        commits = int(counted) if counted is not None and counted.isdigit() else 0
    if floor_note:
        out.append(f'note: {floor_note}\n')
    out.append(f'touched: {len(touched)} files, {commits} commits since dispatch\n')
    out.extend(f'  {f}\n' for f in touched[:30])
    may = [re.sub(r'/$', '/*', p) for p in [*scope_entries(brief, 'may'), *steer_scope_entries(store, seq)]]
    patterns = [scope_pattern(p) for p in may]
    # The master commits the lessons file in the shared tree (kitchen.py lessons add), whatever the brief's Scope says.
    outside = [f for f in touched if f != '.agents/LESSONS.md' and not any(p.fullmatch(f) for p in patterns)]
    if outside:
        out.append(f'outside scope: {len(outside)}\n')
        out.extend(f'  {f}\n' for f in outside[:30])
    sent = len(glob.glob(os.path.join(glob.escape(store), 'steers', glob.escape(seq) + '-*-s[0-9]*.md')))
    objected = len(glob.glob(os.path.join(glob.escape(store), 'reports', glob.escape(seq) + '-*-s[0-9]*.md')))
    applied = 0
    if os.path.isfile(prog):
        applied = sum(1 for line in read_text(prog).split('\n') if re.search(r' steer s[0-9]+ applied', line))
    out.append(f'steers: {sent} sent, {applied} applied, {objected} objected\n')
    sfile = steps_file(store, brief)
    if os.path.isfile(sfile) and os.path.getsize(sfile) > 0:
        committed = sum(1 for line in read_text(sfile).split('\n') if line)
        out.append(f'steps: {committed} committed, reviewed through {noted_through(store, brief)}, {len(open_notes(store, brief))} blocking notes open\n')
    return ''.join(out)
