"""The check-in digest and what it reads: the sidekick's tree, its processes, and the brief's Scope."""

import calendar
import fnmatch
import glob
import os
import re
import time
from dataclasses import dataclass

from . import git, procinfo
from .numbers import int_or_none, tdiv
from .replies import dispatched, file_mtime
from .review import noted_through, open_notes, range_floor
from .steplog import StepLog
from .store import (
    header_field,
    load,
    names,
    peek,
    progress_path,
    read_text,
    text_at,
    update,
)

SHELLS = ('bash', 'sh', 'zsh', 'dash', 'fish')


def status_entries(root):
    """(status, path) of each entry git status lists, NUL-delimited; a rename or copy counts at its new path."""
    raw = git.run(
        root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', quiet=True
    ).stdout.split('\0')
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
    m = re.fullmatch(
        r'\s*(\d{1,4})-\s*(\d{1,2})-\s*(\d{1,2})T\s*(\d{1,2}):\s*(\d{1,2}):\s*(\d{1,2})Z(?:\s.*)?',
        stamp,
        re.DOTALL,
    )
    if not m:
        return None
    year, month, day, hour, minute, second = map(int, m.groups())
    if not (
        1 <= month <= 12
        and 1 <= day <= 31
        and hour <= 23
        and minute <= 59
        and second <= 61
    ):
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


def busy(status, pane, since, now, env=None):
    """What keeps an agent from taking a message between turns, or '': 'working' for herdr's working status, or the command (as busy_command
    names it) that a pane the agent left idle or done has run since `since`. A blocked agent is never busy: it may sit at an approval UI, which a
    caller refuses and does not wait on."""
    if status == 'working':
        return 'working'
    if status in ('idle', 'done') and since is not None:
        return busy_command(pane, since, now, env)
    return ''


def dispatch_facts(store):
    """(pane, dispatch epoch) of the sidekick's current dispatch; the epoch is None where there is no dispatch, no pane or no readable time."""
    pair = load(store)
    pane, at = (
        text_at(peek(pair, 'sidekick'), 'pane_id'),
        text_at(peek(pair, 'dispatch'), 'at'),
    )
    return pane, (iso_epoch(at) if pane and at else None)


def sidekick_activity(store, now):
    """The longest-running process in the sidekick's pane started since the current dispatch, or ''."""
    pane, since = dispatch_facts(store)
    return busy_command(pane, since, now) if since is not None else ''


def sidekick_busy(store, status, now):
    """busy() for the sidekick: the herdr status the caller already holds (steer and queue branch on it, so it is not read twice) and the command its
    pane has run since the current dispatch."""
    pane, since = dispatch_facts(store)
    return busy(status, pane, since, now)


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


def live_steer_effects(store, seq):
    """(may, mustnot): the paths a unit's live steers allow beyond its brief (`scope effect: may write ...`) and forbid (`must not write ...`), in
    steer order. A steer is live when it is not a withdraw and no later steer withdraws it by `supersedes:`. A line with a quote and no quoted
    string adds nothing."""
    files = sorted(
        glob.glob(
            os.path.join(
                glob.escape(store), 'steers', glob.escape(seq) + '-*-s[0-9]*.md'
            )
        )
    )
    withdrawn = ' '
    for f in files:
        if os.path.isfile(f) and header_field(f, 'kind') == 'withdraw':
            sup = header_field(f, 'supersedes').split(' ')[0]
            if sup != 'none':
                withdrawn += os.path.basename(sup.rstrip('/') or sup) + ' '
    found = {'may write': [], 'must not write': []}
    for f in files:
        if not os.path.isfile(f) or header_field(f, 'kind') == 'withdraw':
            continue
        if f' {os.path.basename(f)} ' in withdrawn:
            continue
        # the paths come before a note (` \u2014 <note>`), which may quote words of its own: quoted or not is decided by what precedes it
        line = header_field(f, 'scope effect').split(' \u2014 ')[0]
        for head, paths in found.items():
            if not line.startswith(head):
                continue
            if '"' in line:
                quoted = re.findall(r'"[^"]*"', line)
                paths.extend(q.replace('"', '').replace('`', '') for q in quoted)
            else:
                rest = line[len(head) :]
                rest = rest[1:] if rest.startswith(':') else rest
                rest = rest[1:] if rest.startswith(' ') else rest
                rest = rest.split(' ')[0].replace('`', '')
                if rest:
                    paths.append(rest)
    return found['may write'], found['must not write']


def steer_scope_entries(store, seq):
    """The paths a unit's live steers allow beyond its brief."""
    return live_steer_effects(store, seq)[0]


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
    fresh_notes: int
    elapsed_min: int | None
    timebox_min: int | None

    @property
    def overrun(self):
        return (
            self.elapsed_min is not None
            and self.timebox_min is not None
            and self.elapsed_min > self.timebox_min
        )

    @property
    def flagged(self):
        """Whether the digest carries something the master must see: a stale log, a write outside Scope, an objection, a blocking note not
        announced by an earlier digest or a timebox overrun."""
        return (
            self.stale
            or self.overrun
            or self.outside_scope > 0
            or self.objected > 0
            or self.fresh_notes > 0
        )


def progress_section(store, interval, now, cwd, head, run):
    """(lines, stale, tag) of the progress log's part of the digest: the lines added since the last check-in (at most the last 20), how long ago the
    log was written, and whether that is STALE (older than the interval with nothing running and nothing edited in the tree) or what the sidekick is
    doing instead. The lines are marked seen."""
    prog = progress_path(store)
    if not os.path.isfile(prog):
        return ['progress: none yet\n'], False, ''
    text = read_text(prog)
    total = text.count('\n')
    seen = (
        int_or_none(read_text(f'{prog}.seen')) or 0
        if os.path.isfile(f'{prog}.seen')
        else 0
    )
    if seen > total:
        seen = 0
    age_m = tdiv(now - file_mtime(prog), 60)
    stale, tag = False, ''
    if age_m >= interval:
        if run:
            tag = f'  running: {run}'
        else:
            active = tree_activity(cwd, head, now)
            if active and now - active < (interval if interval > 0 else 1) * 60:
                tag = f'  editing: last change {tdiv(now - active, 60)}m ago'
            else:
                stale = True
    lines = [
        f'progress: +{total - seen} lines (last {age_m}m ago){"  STALE" if stale else tag}\n'
    ]
    logged = text.split('\n')
    if logged and logged[-1] == '':
        logged.pop()
    lines += [f'  {line}\n' for line in logged[seen:][-20:]]
    with open(f'{prog}.seen', 'w') as f:
        f.write(f'{total}\n')
    return lines, stale, tag


def touched_section(store, brief, seq, cwd, head):
    """(lines, outside) of the files changed since dispatch: those in the tree's status or committed since the head, how many commits, and the files
    outside the brief's Scope and the steers' scope effects (the lessons file the master commits in the shared tree is never outside)."""
    touched = status_paths(cwd)
    commits = 0
    if head:
        diff = git.out(cwd, 'diff', '--name-only', f'{head}..HEAD')
        touched += diff.split('\n') if diff else []
        counted = git.out(cwd, 'rev-list', '--count', f'{head}..HEAD')
        commits = int(counted) if counted is not None and counted.isdigit() else 0
    touched = sort_u(touched)
    lines = [f'touched: {len(touched)} files, {commits} commits since dispatch\n']
    lines += [f'  {f}\n' for f in touched[:30]]
    may = [
        re.sub(r'/$', '/*', p)
        for p in [*scope_entries(brief, 'may'), *steer_scope_entries(store, seq)]
    ]
    outside = [
        f
        for f in touched
        if f != '.agents/LESSONS.md' and not any(scope_match(p, f) for p in may)
    ]
    if outside:
        lines.append(f'outside scope: {len(outside)}\n')
        lines += [f'  {f}\n' for f in outside[:30]]
    return lines, len(outside)


def steers_section(store, brief, seq):
    """(lines, objected, blocking) of the steers sent, applied and objected to, and of the steps committed, reviewed through and the blocking
    notes open (their files)."""
    prog = progress_path(store)
    sent = len(
        glob.glob(
            os.path.join(
                glob.escape(store), 'steers', glob.escape(seq) + '-*-s[0-9]*.md'
            )
        )
    )
    objected = len(
        glob.glob(
            os.path.join(
                glob.escape(store), 'reports', glob.escape(seq) + '-*-s[0-9]*.md'
            )
        )
    )
    applied = 0
    if os.path.isfile(prog):
        applied = sum(
            1
            for line in read_text(prog).split('\n')
            if re.search(r' steer s[0-9]+ applied', line)
        )
    lines = [f'steers: {sent} sent, {applied} applied, {objected} objected\n']
    committed = len(StepLog(store, brief).rows())
    blocking = []
    if committed:
        blocking = open_notes(store, brief)
        lines.append(
            f'steps: {committed} committed, reviewed through {noted_through(store, brief)}, {len(blocking)} blocking notes open\n'
        )
    return lines, objected, blocking


def announce_notes(store, open_names):
    """How many of the open blocking notes (file names) no earlier digest announced. The store keeps the names announced and still open
    (pair.json .announced_notes), so separate waits share them and a note that is resolved leaves the list."""
    announced = names(load(store), 'announced_notes')
    fresh = [n for n in open_names if n not in announced]
    if sorted(open_names) != sorted(announced):
        update(
            store, lambda doc: doc.__setitem__('announced_notes', sorted(open_names))
        )
    return len(fresh)


def checkin(ctx, store, interval=9, now=None) -> CheckIn | None:
    """The check-in digest for a sidekick still working at the interval, bounded so the master can poll it cheaply; None when no dispatched
    brief is on disk to report on."""
    now = int(time.time()) if now is None else now
    pair = load(store)
    brief = dispatched(store)
    if not brief or not os.path.isfile(brief):
        return None
    dispatch = peek(pair, 'dispatch')
    at, head, cwd = (
        text_at(dispatch, 'at'),
        text_at(dispatch, 'head'),
        text_at(pair, 'cwd'),
    )
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
    out = [
        f'check-in: {base}  elapsed {"?" if elapsed is None else elapsed}m of {timebox or "?"}m\n'
    ]
    run = sidekick_activity(store, now)
    lines, stale, tag = progress_section(store, interval, now, cwd, head, run)
    out += lines
    notices = []
    if run and 'running:' not in tag:
        notices.append(f'running: {run}\n')
        out.append(notices[-1])
    if floor_note:
        notices.append(f'note: {floor_note}\n')
        out.append(notices[-1])
    lines, outside = touched_section(store, brief, seq, cwd, head)
    out += lines
    lines, objected, blocking = steers_section(store, brief, seq)
    out += lines
    fresh = announce_notes(store, [os.path.basename(f) for f in blocking])
    return CheckIn(
        tuple(out),
        tuple(notices),
        stale,
        outside,
        objected,
        len(blocking),
        fresh,
        elapsed,
        int(timebox) if timebox else None,
    )
