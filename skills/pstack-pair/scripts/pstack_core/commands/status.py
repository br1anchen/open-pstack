import glob
import json
import os
import re
import subprocess
import sys
import time

from ..collate import collate_key, glob_sorted, newest
from ..errors import Die
from ..herdr import agent_status
from ..review import jq_raw
from ..store import alt, header_field, pair_json, paused_reason, read_text, suffix_number, unit_name


class NoPairJson(Exception):
    """bash's field() runs pair_file in a command substitution, so its die only ends that subshell
    and jq then fails on an empty file name: exit 2, after both messages."""


class Tee:
    """The pipe into tee: every line goes to stdout and to status.md as it is produced."""

    def __init__(self, path):
        self.failed = False
        try:
            self.file = open(path, 'wb')
        except OSError as e:
            sys.stderr.write(f'tee: {path}: {os.strerror(e.errno)}\n')
            sys.stderr.flush()
            self.file, self.failed = None, True

    def write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()
        if self.file:
            self.file.write(text.encode('utf-8', 'surrogateescape'))
            self.file.flush()

    def close(self):
        if self.file:
            self.file.close()


def latest(store, directory, seq=None):
    """latest_report and latest_advice: the newest NNN-*.md in the directory, optionally for one NNN."""
    pattern = f'{glob.escape(seq)}-*.md' if seq else '[0-9][0-9][0-9]-*.md'
    return newest(glob.glob(os.path.join(glob.escape(store), directory, pattern)))


def role_field(pair, role, key):
    section = pair.get(role)
    return section.get(key) if isinstance(section, dict) else None


def lines(ctx, store, out):
    consultant = 'consultant' in ctx.roles
    out.write(f'# {ctx.store_label} status\n\n')
    out.write(f'generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}\n')
    if not os.path.isfile(f'{store}/pair.json'):
        raise NoPairJson(store)
    pair = pair_json(store)
    name = jq_raw(role_field(pair, 'master', 'name'))
    out.write(f'master: {name} ({jq_raw(role_field(pair, "master", "pane_id"))}) {agent_status(name)}\n')
    for role in ctx.roles:
        name = jq_raw(role_field(pair, role, 'name'))
        pane = jq_raw(alt(role_field(pair, role, 'pane_id'), 'unassigned'))
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
    key = collate_key()
    for entry in sorted(units, key=key):
        f = entry.split('\t', 1)[1]
        base = os.path.basename(f)
        seq, slug = base[:3], base.removesuffix('.md')[4:]
        kind = re.sub(r's$', '', os.path.basename(os.path.dirname(f)))
        rstatus = 'none'
        report = latest(store, 'reports', seq)
        if report:
            rstatus = header_field(report, 'status') or 'unlabelled'
        verdict = 'none'
        reviews = glob_sorted(glob.glob(os.path.join(glob.escape(store), 'reviews', glob.escape(seq) + '-*.md')))
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
    scratch = alt(pair.get('scratch'), [])
    if isinstance(scratch, list):
        text = '\n'.join(jq_raw(v) if not isinstance(v, (dict, list)) else json.dumps(v, separators=(',', ':')) for v in scratch).rstrip('\n')
        if text:
            words = [w for w in re.split(r'[ \t\n]+', text) if w]
            out.write('\nopen scratch worktrees (remove with pair.sh scratch <store> <id> --remove):\n')
            for w in words or ['']:
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
        raise Die('usage: pair.sh status <store>')
    store = args[0]
    out = Tee(f'{store}/status.md')
    code = 0
    try:
        lines(ctx, store, out)
    except NoPairJson:
        sys.stderr.write(f'pair.sh: no pair.json in {store}; run: pair.sh init <slug>\n')
        sys.stderr.flush()
        code = subprocess.run(['jq', '-r', '.master.name', ''], check=False).returncode
    finally:
        out.close()
    return 1 if out.failed else code
