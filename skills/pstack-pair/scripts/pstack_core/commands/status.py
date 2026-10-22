import glob
import os
import re
import subprocess
import sys
import time

from ..store import (
    Die, prog, header_field, load, names, newest, paused_reason, peek, read_text, suffix_number, text_at, unit_name, event, pair_file,
    pause_notice, progress_path, steer_notice,
)
from ..panes import agent_status


class NoPairJson(Exception):
    """bash's field() runs pair_file in a command substitution, so its die only ends that subshell
    and jq then fails on an empty file name: exit 2, after both messages."""


class Tee:
    """The pipe into tee: every line goes to stdout and to status.md as it is produced."""

    def __init__(self, path):
        self.failed = False
        try:
            self.file = open(path, 'w', encoding='utf-8')
        except OSError as e:
            sys.stderr.write(f'tee: {path}: {os.strerror(e.errno)}\n')
            sys.stderr.flush()
            self.file, self.failed = None, True

    def write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()
        if self.file:
            self.file.write(text)
            self.file.flush()

    def close(self):
        if self.file:
            self.file.close()


def latest(store, directory, seq=None):
    """latest_report and latest_advice: the newest NNN-*.md in the directory, optionally for one NNN."""
    pattern = f'{glob.escape(seq)}-*.md' if seq else '[0-9][0-9][0-9]-*.md'
    return newest(glob.glob(os.path.join(glob.escape(store), directory, pattern)))


def lines(ctx, store, out):
    consultant = 'consultant' in ctx.roles
    out.write(f'# {ctx.store_label} status\n\n')
    out.write(f'generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}\n')
    if not os.path.isfile(f'{store}/pair.json'):
        raise NoPairJson(store)
    pair = load(store)
    master = peek(pair, 'master')
    name = text_at(master, 'name')
    out.write(f'master: {name} ({text_at(master, "pane_id")}) {agent_status(name)}\n')
    for role in ctx.roles:
        name = text_at(peek(pair, role), 'name')
        pane = text_at(peek(pair, role), 'pane_id', 'unassigned')
        out.write(f'{role}: {name} ({pane}) {agent_status(name)}\n')
    out.write('\n')
    if consultant:
        out.write('| seq | kind | unit | report | advice | review |\n|---|---|---|---|---|---|\n')
    else:
        out.write('| seq | kind | unit | report | review |\n|---|---|---|---|---|\n')
    units = []
    for d in ('plans', 'briefs'):
        for f in glob.glob(os.path.join(glob.escape(store), d, '[0-9][0-9][0-9]-*.md')):
            if os.path.exists(f) and not f.endswith('.direction.md'):
                units.append(f'{os.path.basename(f)}\t{f}')
    for entry in sorted(units):
        f = entry.split('\t', 1)[1]
        base = os.path.basename(f)
        seq, slug = base[:3], base.removesuffix('.md')[4:]
        kind = re.sub(r's$', '', os.path.basename(os.path.dirname(f)))
        rstatus = 'none'
        report = latest(store, 'reports', seq)
        if report:
            rstatus = header_field(report, 'status') or 'unlabelled'
        verdict = 'none'
        reviews = sorted(glob.glob(os.path.join(glob.escape(store), 'reviews', glob.escape(seq) + '-*.md')))
        if reviews:
            verdict = header_field(reviews[0], 'verdict') or 'unlabelled'
        if consultant:
            astatus = 'none'
            advice = latest(store, 'advice', seq)
            if advice:
                astatus = header_field(advice, 'status') or 'unlabelled'
                n = len(glob.glob(os.path.join(glob.escape(store), 'advice', glob.escape(seq) + '-*-c[0-9]*.md')))
                if n > 0:
                    astatus = f'{astatus} (c{n})'
            out.write(f'| {seq} | {kind} | {slug} | {rstatus} | {astatus} | {verdict} |\n')
        else:
            out.write(f'| {seq} | {kind} | {slug} | {rstatus} | {verdict} |\n')
    report = latest(store, 'reports')
    if report:
        out.write(f'\nlatest report: {report}\n')
    if consultant:
        advice = latest(store, 'advice')
        if advice:
            out.write(f'latest advice: {advice}\n')
    if os.path.isfile(f'{store}/queue'):
        out.write(f'queued: {read_text(f"{store}/queue").rstrip(chr(10))}\n')
    if os.path.isfile(f'{store}/paused'):
        out.write(f'paused: {paused_reason(store)}\n')
    scratch = [w for line in names(pair, 'scratch') for w in line.split()]
    if scratch:
        out.write('\nopen scratch worktrees (remove with pair.sh scratch <store> <id> --remove):\n')
        for w in scratch:
            out.write(f'  {w}\n')
    if os.path.isdir(f'{store}/answers'):
        asks = 0
        for f in glob.glob(os.path.join(glob.escape(store), 'reports', '[0-9][0-9][0-9]-*-q[0-9]*.md')):
            if not os.path.exists(f):
                continue
            base = os.path.basename(f)
            k = suffix_number(unit_name(f), 'q') or unit_name(f)
            if not glob.glob(os.path.join(glob.escape(store), 'answers', f'{glob.escape(base[:3])}-*-a{glob.escape(k)}.md')):
                asks += 1
        if asks > 0:
            out.write(f'unanswered asks: {asks}\n')
    gates = f'{store}/gates.md'
    if os.path.isfile(gates) and os.path.getsize(gates) > 0:
        open_gates = sum(1 for line in read_text(gates).split('\n') if line.startswith('- '))
        if open_gates:
            out.write(f'\nopen gates: {open_gates}\n')


def run(ctx, args):
    if len(args) != 1:
        raise Die(f'usage: {prog()} status <store>')
    store = args[0]
    out = Tee(f'{store}/status.md')
    code = 0
    try:
        lines(ctx, store, out)
    except NoPairJson:
        sys.stderr.write(f'{prog()}: no pair.json in {store}; run: pair.sh init <slug>\n')
        sys.stderr.flush()
        code = 2
    finally:
        out.close()
    return 1 if out.failed else code


def log(ctx, args):
    if len(args) != 6:
        raise Die(f'usage: {prog()} log <store> <phase> <decision> <why> <evidence> <result>')
    store, phase, decision, *rest = args
    helper = f'{ctx.skill_root}/../show-me-your-work/scripts/log.sh'
    if not (os.path.isfile(helper) and os.access(helper, os.X_OK)):
        raise Die(f'show-me-your-work log helper not found at {helper}')
    code = subprocess.run([helper, f'{store}/decisions.tsv', phase, decision, *rest], check=False).returncode
    if code:
        raise SystemExit(code)
    event(store, 'master', 'log', '-', f'{phase}: {decision}')
    return 0


def progress(ctx, args):
    if len(args) != 2:
        raise Die(f'usage: {prog()} progress <store> <text>')
    store, text = args
    pair_file(store)
    path = progress_path(store)
    if path is None:
        raise Die('no brief dispatched yet; progress belongs to a running brief')
    os.makedirs(f'{store}/progress', exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%H:%M")} {text}\n')
    event(store, 'sidekick', 'progress', path)
    sys.stdout.write(f'{path}\n{pause_notice(store)}{steer_notice(store)}')
    return 0
