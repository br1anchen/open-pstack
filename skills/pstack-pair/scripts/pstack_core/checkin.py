"""The check-in digest and what it reads: the sidekick's tree, its processes, and the brief's Scope."""
import calendar
import fnmatch
import glob
import os
import re
import time
from dataclasses import dataclass

from . import git, procinfo
from .replies import dispatched, file_mtime
from .review import noted_through, open_notes, range_floor
from .steplog import StepLog
from .store import int_or_none, header_field, load, peek, progress_path, read_text, tdiv, text_at

SHELLS = ('bash', 'sh', 'zsh', 'dash', 'fish')


def status_entries(root):
    """(status, path) of each entry git status lists, NUL-delimited; a rename or copy counts at its new path."""
    raw = git.run(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', quiet=True).stdout.split('\0')
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
        counted = git.out(cwd, 'rev-list', '--count', f'{head}..HEAD')
        commits = int(counted) if counted is not None and counted.isdigit() else 0
    if commits > 0:
        stamp = git.out(cwd, 'log', '-1', '--format=%ct')
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
    root = procinfo.proc_root()
    if not pane or not os.path.isdir(root):
        return ''
    sock = env.get('HERDR_SOCKET_PATH', '')
    best, best_et, shell, shell_et = '', -1, '', -1
    for proc in procinfo.of_user(os.getuid(), root):
        et, comm = proc.elapsed, proc.comm
        if not now - et > since:
            continue
        lines = procinfo.environ(proc.pid, root)
        if lines is None:
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
    pane, at = text_at(peek(pair, 'sidekick'), 'pane_id'), text_at(peek(pair, 'dispatch'), 'at')
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


def steer_scope_entries(store, seq):
    """The paths a unit's live steers allow beyond its brief: the `scope effect: may write ...` line of each steer
    that is not a withdraw and that no later steer withdraws by `supersedes:`. A line with a quote and no quoted
    string adds nothing."""
    files = sorted(glob.glob(os.path.join(glob.escape(store), 'steers', glob.escape(seq) + '-*-s[0-9]*.md')))
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
            found.extend(q.replace('"', '').replace('`', '') for q in quoted)
        else:
            rest = line[len('may write'):]
            rest = rest[1:] if rest.startswith(':') else rest
            rest = rest[1:] if rest.startswith(' ') else rest
            rest = rest.split(' ')[0].replace('`', '')
            if rest:
                found.append(rest)
    return found


def scope_match(pattern, path):
    """Whether path matches a Scope entry: a glob whose * and ? take any character, / included. `**/` stands for zero or more directories, so
    `d/**/x` matches `d/x` as well as `d/a/x`."""
    head, found, rest = pattern.partition('**/')
    if not found:
        return fnmatch.fnmatchcase(path, pattern)
    return scope_match(f'{head}*/{rest}', path) or scope_match(f'{head}{rest}', path)


def sort_u(lines):
    """sort -u: the distinct lines in code-point order."""
    return sorted(set(lines))


@dataclass(frozen=True)
class CheckIn:
    """The check-in digest for a sidekick still working: `lines` are the newline-ended lines to print, `notices` those of them that say what
    the sidekick is running or what the digest assumed (a quiet dispatch still shows them), and the rest are the facts the master is woken for."""
    lines: tuple[str, ...]
    notices: tuple[str, ...]
    stale: bool
    outside_scope: int
    objected: int
    open_notes: int
    elapsed_min: int | None
    timebox_min: int | None

    @property
    def overrun(self):
        return self.elapsed_min is not None and self.timebox_min is not None and self.elapsed_min > self.timebox_min

    @property
    def flagged(self):
        """Whether the digest carries something the master must see: a stale log, a write outside Scope, an objection, an open blocking note
        or a timebox overrun."""
        return self.stale or self.overrun or self.outside_scope > 0 or self.objected > 0 or self.open_notes > 0


def checkin(ctx, store, interval=9, now=None) -> CheckIn | None:
    """The check-in digest for a sidekick still working at the interval, bounded so the master can poll it cheaply; None when no dispatched
    brief is on disk to report on."""
    now = int(time.time()) if now is None else now
    out = []
    pair = load(store)
    brief = dispatched(store)
    if not brief or not os.path.isfile(brief):
        return None
    dispatch = peek(pair, 'dispatch')
    at, head, cwd = text_at(dispatch, 'at'), text_at(dispatch, 'head'), text_at(pair, 'cwd')
    floor_note = ''
    tip = git.out(cwd, 'rev-parse', '--verify', '--quiet', 'HEAD') or ''
    if head and tip:
        head, floor_note = range_floor(cwd, head, tip, allow_head=True)
    base = os.path.basename(brief.rstrip('/') or brief)
    seq = base[:3]
    m = re.match(r'[0-9]+', header_field(brief, 'timebox'))
    timebox = m.group(0) if m else ''
    since = iso_epoch(at)
    elapsed = None if since is None else tdiv(now - since, 60)
    out.append(f'check-in: {base}  elapsed {"?" if elapsed is None else elapsed}m of {timebox or "?"}m\n')
    run = sidekick_activity(store, now)
    prog = progress_path(store)
    stale, tag = False, ''
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
                tag = f'  running: {run}'
            else:
                active = tree_activity(cwd, head, now)
                if active and now - active < (limit if limit > 0 else 1) * 60:
                    tag = f'  editing: last change {tdiv(now - active, 60)}m ago'
                else:
                    stale = True
        out.append(f'progress: +{new} lines (last {age_m}m ago){"  STALE" if stale else tag}\n')
        lines = text.split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        out.extend(f'  {line}\n' for line in lines[seen:][-20:])
        with open(f'{prog}.seen', 'w') as f:
            f.write(f'{total}\n')
    else:
        out.append('progress: none yet\n')
    notices = []
    if run and 'running:' not in tag:
        notices.append(f'running: {run}\n')
        out.append(notices[-1])
    touched = status_paths(cwd)
    if head:
        diff = git.out(cwd, 'diff', '--name-only', f'{head}..HEAD')
        touched += diff.split('\n') if diff else []
    touched = sort_u(touched)
    commits = 0
    if head:
        counted = git.out(cwd, 'rev-list', '--count', f'{head}..HEAD')
        commits = int(counted) if counted is not None and counted.isdigit() else 0
    if floor_note:
        notices.append(f'note: {floor_note}\n')
        out.append(notices[-1])
    out.append(f'touched: {len(touched)} files, {commits} commits since dispatch\n')
    out.extend(f'  {f}\n' for f in touched[:30])
    may = [re.sub(r'/$', '/*', p) for p in [*scope_entries(brief, 'may'), *steer_scope_entries(store, seq)]]
    # The master commits the lessons file in the shared tree (kitchen.py lessons add), whatever the brief's Scope says.
    outside = [f for f in touched if f != '.agents/LESSONS.md' and not any(scope_match(p, f) for p in may)]
    if outside:
        out.append(f'outside scope: {len(outside)}\n')
        out.extend(f'  {f}\n' for f in outside[:30])
    sent = len(glob.glob(os.path.join(glob.escape(store), 'steers', glob.escape(seq) + '-*-s[0-9]*.md')))
    objected = len(glob.glob(os.path.join(glob.escape(store), 'reports', glob.escape(seq) + '-*-s[0-9]*.md')))
    applied = 0
    if os.path.isfile(prog):
        applied = sum(1 for line in read_text(prog).split('\n') if re.search(r' steer s[0-9]+ applied', line))
    out.append(f'steers: {sent} sent, {applied} applied, {objected} objected\n')
    committed = len(StepLog(store, brief).rows())
    blocking = 0
    if committed:
        blocking = len(open_notes(store, brief))
        out.append(f'steps: {committed} committed, reviewed through {noted_through(store, brief)}, {blocking} blocking notes open\n')
    return CheckIn(tuple(out), tuple(notices), stale, len(outside), objected, blocking, elapsed, int(timebox) if timebox else None)
