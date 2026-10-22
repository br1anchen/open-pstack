"""feedback and maintainer: the host feedback inbox every kitchen on this host files into, and the maintainer who is told. The inbox is shared with every kitchen on this host, so the lock, the file names and the formats are fixed."""

import glob
import json
import os
import re
import tempfile
import sys
import time

from pstack_core.checkin import iso_epoch
from pstack_core.store import Die, Exit, prog, read_text, write_text, damaged, field, event, header_field, json_type, listing, load, pair_file, text_at
from pstack_core.panes import herdr, reply_at
from pstack_core.replies import file_mtime

from .ledger import counters, retro_line

USAGE_FILE = 'usage: kitchen.sh feedback <store> <file>'
USAGE_FEEDBACK = 'usage: kitchen.sh feedback <store> <file> | --inbox | --close <id> <commit|none> <note>'
USAGE_CLOSE = 'usage: kitchen.sh feedback --close <id> <commit|none> <note>'
USAGE_MAINTAINER = 'usage: kitchen.sh maintainer on [--pane ID] | off | status'
LOCK_TRIES = 100
LOCK_DELAY = 0.1
STALE_MINUTES = 1


def state_home():
    return os.environ.get('XDG_STATE_HOME') or f'{os.environ.get("HOME", "")}/.local/state'


def fb_root():
    return f'{state_home()}/pstack/feedback'


def build_rev(path):
    """The build revision recorded for a skills directory in builds.tsv (the last row naming it), or unknown."""
    rev = ''
    try:
        for line in read_text(f'{state_home()}/pstack/builds.tsv').split('\n'):
            fields = line.split('\t')
            if len(fields) >= 3 and fields[2] == path:
                rev = fields[1]
    except OSError:
        pass
    return rev or 'unknown'


def stale(lock, now=None):
    """find -maxdepth 0 -mmin +1: a lock whose age, in whole minutes, is over one."""
    try:
        age = (time.time() if now is None else now) - os.stat(lock).st_mtime
    except OSError:
        return False
    return int(age / 60) > STALE_MINUTES


def fb_lock(fb, tries=LOCK_TRIES, delay=LOCK_DELAY, sleep=time.sleep):
    """Take the lock directory <fb>/.lock: a lock left by a killed command (over a minute old) is removed and the mkdir retried at once;
    otherwise wait `delay` and try again, and give up on the `tries`-th failure."""
    lock = f'{fb}/.lock'
    attempts = 0
    while True:
        try:
            os.mkdir(lock)
            return
        except OSError:
            pass
        if stale(lock):
            try:
                os.rmdir(lock)
            except OSError:
                pass
            continue
        attempts += 1
        if attempts >= tries:
            raise Die(f'the feedback inbox is locked ({lock})')
        sleep(delay)


def fb_unlock(fb):
    try:
        os.rmdir(f'{fb}/.lock')
    except OSError:
        pass


def scratch_file(directory, prefix):
    """An empty file of mode 0600 in directory, named prefix and random characters, returned by path."""
    fd, path = tempfile.mkstemp(prefix=prefix, dir=directory)
    os.close(fd)
    return path


def move_into_place(src, dst):
    """Move src to dst: a failure prints its message and ends the command with status 1 (any lock held stays held)."""
    try:
        os.replace(src, dst)
    except OSError as e:
        raise Exit(1, f'{prog()}: cannot move {src} to {dst}: {os.strerror(e.errno)}\n') from None


NOT_READ = object()


def maintainer_record(path):
    """maintainer.json, the object `maintainer on` wrote; None, after one stderr line, for a file that is not one."""
    try:
        doc = json.loads(read_text(path))
        problem = '' if isinstance(doc, dict) else f'is {json_type(doc)}, not an object'
    except ValueError as e:
        problem = f'is not JSON: {e}'
    if problem:
        sys.stdout.flush()
        sys.stderr.write(f'{path} {problem}\n')
        sys.stderr.flush()
        return None
    return doc


def registered(record, key):
    """The text of a maintainer.json field; empty for a file that could not be read or a field that is no string."""
    value = (record or {}).get(key)
    return value if isinstance(value, str) else ''


def first_build(store):
    """The path of the first skills build pair.json lists, empty for a run from before the stamps."""
    builds = listing(load(store), 'skill_builds')
    first = builds[0] if builds else None
    if first is not None and not isinstance(first, dict):
        raise damaged(f'skill_builds holds {json_type(first)}, not an object')
    return text_at(first or {}, 'path')


def rm_f(path):
    """Remove path: a missing file is fine; any other failure prints its message and ends the command with status 1."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise Exit(1, f'{prog()}: cannot remove {path}: {os.strerror(e.errno)}\n') from None


def read_lines(path):
    return read_text(path).split('\n')


def age_text(minutes):
    if minutes >= 2880:
        return f'{minutes // 1440}d'
    if minutes >= 120:
        return f'{minutes // 60}h'
    return f'{minutes}m'


def aligned(rows):
    """Each row's cells joined in columns: every cell but the last padded to its column's widest cell plus two spaces."""
    widths = {}
    for row in rows:
        for n, cell in enumerate(row):
            widths[n] = max(widths.get(n, 0), len(cell))
    out = []
    for row in rows:
        out.append(''.join(cell.ljust(widths[n] + 2) if n < len(row) - 1 else cell for n, cell in enumerate(row)))
    return out


def inbox():
    fb = fb_root()
    now = int(time.time())
    rows = []
    for f in sorted(glob.glob(f'{glob.escape(fb)}/inbox/*.md')):
        if not os.path.exists(f) or os.path.exists(f'{fb}/done/{os.path.basename(f)}'):
            continue
        filed = header_field(f, 'filed')
        stamp = iso_epoch(filed)
        secs = stamp if stamp is not None else file_mtime(f)
        lines = read_lines(f)
        if lines and lines[-1] == '':
            lines.pop()
        run = next((m.group(1) for m in (re.match(r'run: ([^ ]*) .*', line) for line in lines) if m), '')
        repo = (re.match(r'^.*\((.*)\)$', lines[0]) or [None, ''])[1] if lines else ''
        build = header_field(f, 'build').split(' ')[0]
        gaps = sum(1 for line in lines if line.startswith('### '))
        if gaps == 0:
            # a report with no gap ends the listing with status 1
            raise Exit(1)
        rows.append((secs, [header_field(f, 'id'), run, repo, build, str(gaps), age_text((now - secs) // 60)]))
    if not rows:
        sys.stdout.write('feedback inbox: empty\n')
        return 0
    rows.sort(key=lambda r: (r[0], f'{r[0]}\t' + '\t'.join(r[1])))
    for line in aligned([r[1] for r in rows]):
        sys.stdout.write(line + '\n')
    return 0


def close(args):
    if len(args) != 3:
        raise Die(USAGE_CLOSE)
    fb = fb_root()
    id_ = args[0][:-3] if args[0].endswith('.md') else args[0]
    commit, note = args[1], args[2]
    inbox_file, done_file = f'{fb}/inbox/{id_}.md', f'{fb}/done/{id_}.md'
    if not os.path.isfile(inbox_file) and not os.path.isfile(done_file):
        raise Die(f'no feedback report {id_} in {fb}')
    fb_lock(fb)
    if os.path.isfile(inbox_file) and not os.path.isfile(done_file):
        tmp = scratch_file(fb, '.closing-')
        body = read_text(inbox_file)
        body = re.sub(r'(?m)^status: open$', 'status: closed', body)
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        body += f'\n## Closed\n\nclosed: {stamp}\ncommit: {commit}\nnote: {note.replace(chr(10), " ")}\n'
        write_text(tmp, body)
        move_into_place(tmp, done_file)
        rm_f(inbox_file)
        fb_unlock(fb)
        sys.stdout.write(f'closed: {id_} (commit {commit})\n')
        return 0
    fb_unlock(fb)
    rm_f(inbox_file)
    sys.stdout.write(f'already closed: {id_}\n')
    lines = read_lines(done_file)
    start = next((n for n, line in enumerate(lines) if line.startswith('## Closed')), None)
    if start is not None:
        text = '\n'.join(lines[start:])
        sys.stdout.write(text if text.endswith('\n') else text + '\n')
    return 0


def maintainer_problem(fb, record=NOT_READ):
    """Why the registered maintainer cannot be told, or '' when it can. A herdr answer that is not what it expects gives one stderr line and an
    empty value."""
    file = f'{fb}/maintainer.json'
    if not os.path.isfile(file):
        return 'no maintainer is registered'
    record = maintainer_record(file) if record is NOT_READ else record
    name, pane, session = registered(record, 'name'), registered(record, 'pane'), registered(record, 'session')
    code, info = herdr('agent', 'get', name)
    if code != 0:
        return f'agent {name} is gone'
    info = info.rstrip('\n')
    if (reply_at(info, 'result', 'agent', 'pane_id') or '') != pane:
        return f'agent {name} moved off pane {pane}'
    if session and (reply_at(info, 'result', 'agent', 'agent_session', 'value') or '') != session:
        return f'agent {name} is a different session than the one registered'
    return f'agent {name} is blocked on a dialog' if reply_at(info, 'result', 'agent', 'agent_status') == 'blocked' else ''


def gap_found(path):
    """Whether a `### ` entry sits under the `## Gaps` heading."""
    g = found = False
    for line in read_lines(path):
        if line.startswith('## Gaps'):
            g = True
            continue
        if line.startswith('## '):
            g = False
        if g and line.startswith('### '):
            found = True
    return found


def file_report(ctx, args):
    if len(args) != 2:
        raise Die(USAGE_FILE)
    store, src = args
    pair_file(store)
    if not os.path.isfile(src):
        raise Die(f'no such file: {src}')
    skills_dir = os.path.dirname(ctx.skill_root)
    if not gap_found(src):
        raise Die(f'{src} has no gap: a `### <n>. <title>` heading under `## Gaps` (see {ctx.skill_root}/references/feedback-template.md)')
    fb = fb_root()
    os.makedirs(f'{fb}/inbox', exist_ok=True)
    os.makedirs(f'{fb}/done', exist_ok=True)
    pair = load(store)
    first = first_build(store)
    run = text_at(pair, 'slug')
    counts = counters(store)
    repo = text_at(counts, 'repo')
    git_root = text_at(pair, 'git_root')
    if not repo:
        repo = os.path.basename(git_root.rstrip('/')) if git_root.rstrip('/') else '/'
    retro = retro_line(store, counts)
    stem = f'{time.strftime("%Y%m%d", time.gmtime())}-{repo}-{run}'
    tmp = scratch_file(fb, '.filing-')
    fb_lock(fb)
    k, name = 1, stem
    while os.path.exists(f'{fb}/inbox/{name}.md') or os.path.exists(f'{fb}/done/{name}.md'):
        k += 1
        name = f'{stem}-{k}'
    out = open(tmp, 'w', encoding='utf-8', newline='')

    def put(text):
        out.write(text)
        out.flush()

    master_name, master_pane = field(store, 'master', 'name', default='unknown'), field(store, 'master', 'pane_id', default='unknown')
    put(f'# Kitchen feedback: {run} ({repo})\nid: {name}\n')
    put(f'run: {run}   store: {store}   repo: {git_root}   master: {master_name} ({master_pane})\n')
    put(f'build: {build_rev(first) if first else "unknown"} ({first or "unknown"})\n')
    if first and first != skills_dir:
        put(f'filed with: {build_rev(skills_dir)} ({skills_dir})\n')
    put(f'retro: {retro}\nfiled: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}\nstatus: open\n\n')
    out.write(read_text(src))
    out.close()
    move_into_place(tmp, f'{fb}/inbox/{name}.md')
    fb_unlock(fb)
    path = f'{fb}/inbox/{name}.md'
    event(store, 'master', 'feedback', '-', name)
    sys.stdout.write(f'filed: {path}\n')
    sys.stdout.flush()
    record = maintainer_record(f'{fb}/maintainer.json') if os.path.isfile(f'{fb}/maintainer.json') else None
    problem = maintainer_problem(fb, record)
    if not problem:
        if herdr('agent', 'prompt', registered(record, 'name'), f'pstack-kitchen FEEDBACK {path}')[0] == 0:
            sys.stdout.write(f'delivered: to maintainer {registered(record, "name")}\n')
            return 0
    problem = problem or 'the prompt to the maintainer failed'
    herdr('notification', 'show', 'pstack-kitchen: feedback filed', '--body', path, '--sound', 'request')
    sys.stdout.write(f'delivered: notification for the human ({problem}); read it with: cat {path}\n')
    return 0


def feedback(ctx, args):
    if not args:
        raise Die(USAGE_FEEDBACK)
    if args[0] == '--inbox':
        return inbox()
    if args[0] == '--close':
        return close(args[1:])
    return file_report(ctx, args)


def maintainer(ctx, args):
    if not args:
        raise Die(USAGE_MAINTAINER)
    sub, rest = args[0], args[1:]
    fb = fb_root()
    file = f'{fb}/maintainer.json'
    if sub == 'on':
        pane = ''
        while rest:
            if rest[0] == '--pane':
                if len(rest) < 2:
                    raise Die('--pane needs a value')
                pane, rest = rest[1], rest[2:]
            else:
                raise Die(f'unknown option {rest[0]}')
        if os.environ.get('HERDR_ENV') != '1':
            raise Die('maintainer on needs Herdr (HERDR_ENV=1)')
        pane = pane or os.environ.get('HERDR_PANE_ID', '')
        if not pane:
            raise Die('no pane: pass --pane ID')
        code, info = herdr('agent', 'get', pane)
        if code != 0:
            raise Die(f'herdr has no agent on pane {pane}')
        os.makedirs(fb, exist_ok=True)
        tmp = scratch_file(fb, '.maintainer-')
        agent = reply_at(info, 'result', 'agent')
        agent = agent if isinstance(agent, dict) else {}
        session = agent.get('agent_session')
        record = {'name': agent.get('name'), 'pane': agent.get('pane_id'), 'session': session.get('value') if isinstance(session, dict) else None,
                  'since': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        write_text(tmp, json.dumps(record, indent=2, ensure_ascii=False) + '\n')
        if not record['name']:
            rm_f(tmp)
            raise Die(f'herdr reports no name for the agent on pane {pane}; name it first')
        move_into_place(tmp, file)
        sys.stdout.write(f'maintainer: {record["name"]} on {record["pane"] or ""}\n')
        return 0
    if sub == 'off':
        rm_f(file)
        sys.stdout.write('maintainer: none\n')
        return 0
    if sub == 'status':
        if not os.path.isfile(file):
            sys.stdout.write('maintainer: none\n')
            return 0
        record = maintainer_record(file)
        problem = maintainer_problem(fb, record)
        sys.stdout.write(f'maintainer: {registered(record, "name")} on {registered(record, "pane")} since {registered(record, "since")}: {problem or "live"}\n')
        return 0
    raise Die(USAGE_MAINTAINER)
