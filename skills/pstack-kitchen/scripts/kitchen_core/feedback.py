"""feedback and maintainer: the host feedback inbox every kitchen on this host files into, and the maintainer who is told. Ported from
kitchen.sh at 9f0f6e5. The inbox is shared with kitchens that still run the bash, so the lock, the file names and the formats are the bash's."""

import glob
import json
import os
import random
import re
import string
import subprocess
import sys
import time

from pstack_core.checkin import iso_epoch
from pstack_core.collate import collate_key, glob_sorted
from pstack_core.errors import Die, Exit, unbound_arg
from pstack_core.herdr import herdr
from pstack_core.replies import file_mtime
from pstack_core.store import alt, event, header_field, jq_raw, pair_file, pair_json

from . import kpy
from .ledger import counters, retro_line, slug_of

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
        with open(f'{state_home()}/pstack/builds.tsv', 'rb') as f:
            for line in f.read().decode('utf-8', 'surrogateescape').split('\n'):
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
    """mkdir <fb>/.lock, as the bash does: a lock left by a killed command (over a minute old) is removed and the mkdir retried at once;
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


def mktemp(directory, prefix):
    """mktemp "<directory>/<prefix>XXXXXX": an empty file of mode 0600 with six random characters, returned by path."""
    alphabet = string.ascii_letters + string.digits
    while True:
        path = f'{directory}/{prefix}' + ''.join(random.choice(alphabet) for _ in range(6))
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return path
        except FileExistsError:
            continue


def mv(src, dst):
    """mv src dst: a failure is mv's message and, under set -e, the end of the command with status 1 (any lock held stays held)."""
    try:
        os.replace(src, dst)
    except OSError as e:
        raise Exit(1, f"mv: cannot move '{src}' to '{dst}': {os.strerror(e.errno)}\n") from None


def read_lines(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'surrogateescape').split('\n')


def age_text(minutes):
    if minutes >= 2880:
        return f'{minutes // 1440}d'
    if minutes >= 120:
        return f'{minutes // 60}h'
    return f'{minutes}m'


def column_t(rows):
    """column -t -s TAB: every cell but the last of a row padded to its column's widest cell plus two spaces."""
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
    for f in glob_sorted(glob.glob(f'{glob.escape(fb)}/inbox/*.md')):
        if not os.path.lexists(f) or os.path.lexists(f'{fb}/done/{os.path.basename(f)}'):
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
        rows.append((secs, [header_field(f, 'id'), run, repo, build, str(gaps), age_text((now - secs) // 60)]))
    if not rows:
        sys.stdout.write('feedback inbox: empty\n')
        return 0
    collate = collate_key()
    rows.sort(key=lambda r: (r[0], collate(f'{r[0]}\t' + '\t'.join(r[1]))))
    for line in column_t([r[1] for r in rows]):
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
        tmp = mktemp(fb, '.closing-')
        with open(inbox_file, 'rb') as f:
            body = f.read().decode('utf-8', 'surrogateescape')
        body = re.sub(r'(?m)^status: open$', 'status: closed', body)
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        body += f'\n## Closed\n\nclosed: {stamp}\ncommit: {commit}\nnote: {note.replace(chr(10), " ")}\n'
        with open(tmp, 'wb') as f:
            f.write(body.encode('utf-8', 'surrogateescape'))
        mv(tmp, done_file)
        try:
            os.unlink(inbox_file)
        except OSError:
            pass
        fb_unlock(fb)
        sys.stdout.write(f'closed: {id_} (commit {commit})\n')
        return 0
    fb_unlock(fb)
    try:
        os.unlink(inbox_file)
    except OSError:
        pass
    sys.stdout.write(f'already closed: {id_}\n')
    lines = read_lines(done_file)
    start = next((n for n, line in enumerate(lines) if line.startswith('## Closed')), None)
    if start is not None:
        text = '\n'.join(lines[start:])
        sys.stdout.write(text if text.endswith('\n') else text + '\n')
    return 0


def maintainer_problem(fb):
    """Why the registered maintainer cannot be told, or '' when it can."""
    file = f'{fb}/maintainer.json'
    if not os.path.isfile(file):
        return 'no maintainer is registered'
    with open(file, 'rb') as f:
        entry = json.load(f)
    name, pane, session = jq_raw(entry.get('name')), jq_raw(entry.get('pane')), alt(entry.get('session'), '')
    code, out = herdr('agent', 'get', name)
    if code != 0:
        return f'agent {name} is gone'
    try:
        agent = json.loads(out)['result']['agent']
    except (ValueError, KeyError, TypeError):
        agent = {}
    if alt(agent.get('pane_id'), '') != pane:
        return f'agent {name} moved off pane {pane}'
    value = agent.get('agent_session')
    value = alt(value.get('value') if isinstance(value, dict) else None, '')
    if session and value != session:
        return f'agent {name} is a different session than the one registered'
    return f'agent {name} is blocked on a dialog' if alt(agent.get('agent_status'), '') == 'blocked' else ''


def gap_found(path):
    """awk '/^## Gaps/ {g = 1; next} /^## / {g = 0} g && /^### / {found = 1} END {exit !found}'."""
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
    pair = pair_json(store)
    run = jq_raw(pair.get('slug'))
    counts = counters(store)
    repo = jq_raw(counts.get('repo'))
    git_root = jq_raw(pair.get('git_root'))
    if not repo:
        repo = os.path.basename(git_root.rstrip('/')) if git_root.rstrip('/') else '/'
    retro = retro_line(store, counts)
    stem = f'{time.strftime("%Y%m%d", time.gmtime())}-{repo}-{run}'
    tmp = mktemp(fb, '.filing-')
    fb_lock(fb)
    k, name = 1, stem
    while os.path.lexists(f'{fb}/inbox/{name}.md') or os.path.lexists(f'{fb}/done/{name}.md'):
        k += 1
        name = f'{stem}-{k}'
    master = pair.get('master')
    master = master if isinstance(master, dict) else {}
    builds = pair.get('skill_builds')
    first_entry = builds[0] if isinstance(builds, list) and builds else None
    first = jq_raw(alt(first_entry.get('path') if isinstance(first_entry, dict) else None, ''))
    head = [f'# Kitchen feedback: {run} ({repo})', f'id: {name}',
            f'run: {run}   store: {store}   repo: {git_root}   master: {jq_raw(alt(master.get("name"), "unknown"))} ({jq_raw(alt(master.get("pane_id"), "unknown"))})',
            f'build: {build_rev(first) if first else "unknown"} ({first or "unknown"})']
    if first and first != skills_dir:
        head.append(f'filed with: {build_rev(skills_dir)} ({skills_dir})')
    head += [f'retro: {retro}', f'filed: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}', 'status: open', '']
    with open(src, 'rb') as f:
        body = f.read()
    with open(tmp, 'wb') as f:
        f.write(('\n'.join(head) + '\n').encode('utf-8', 'surrogateescape') + body)
    mv(tmp, f'{fb}/inbox/{name}.md')
    fb_unlock(fb)
    path = f'{fb}/inbox/{name}.md'
    event(store, 'master', 'feedback', '-', name)
    sys.stdout.write(f'filed: {path}\n')
    sys.stdout.flush()
    problem = maintainer_problem(fb)
    if not problem:
        with open(f'{fb}/maintainer.json', 'rb') as f:
            maintainer = jq_raw(json.load(f).get('name'))
        if herdr('agent', 'prompt', maintainer, f'pstack-kitchen FEEDBACK {path}')[0] == 0:
            sys.stdout.write(f'delivered: to maintainer {maintainer}\n')
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
                    raise unbound_arg()
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
        tmp = mktemp(fb, '.maintainer-')
        program = '{name: $i.result.agent.name, pane: $i.result.agent.pane_id, session: ($i.result.agent.agent_session.value // null), since: $since}'
        with open(tmp, 'wb') as out:
            r = subprocess.run(['jq', '-n', '--argjson', 'i', info.rstrip('\n'), '--arg', 'since', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), program],
                               stdout=out, check=False)
        if r.returncode != 0:
            raise Exit(r.returncode)
        with open(tmp, 'rb') as f:
            entry = json.load(f)
        if not alt(entry.get('name'), ''):
            os.unlink(tmp)
            raise Die(f'herdr reports no name for the agent on pane {pane}; name it first')
        os.replace(tmp, file)
        sys.stdout.write(f'maintainer: {jq_raw(entry.get("name"))} on {jq_raw(entry.get("pane"))}\n')
        return 0
    if sub == 'off':
        try:
            os.unlink(file)
        except OSError:
            pass
        sys.stdout.write('maintainer: none\n')
        return 0
    if sub == 'status':
        if not os.path.isfile(file):
            sys.stdout.write('maintainer: none\n')
            return 0
        problem = maintainer_problem(fb)
        with open(file, 'rb') as f:
            entry = json.load(f)
        sys.stdout.write(f'maintainer: {jq_raw(entry.get("name"))} on {jq_raw(entry.get("pane"))} since {jq_raw(entry.get("since"))}: {problem or "live"}\n')
        return 0
    raise Die(USAGE_MAINTAINER)
