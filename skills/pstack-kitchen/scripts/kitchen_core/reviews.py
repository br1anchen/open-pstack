"""The review notes the kitchen requires an engine run in: which accepted units still lack one. Ported from kitchen.sh at 9f0f6e5 (warn_unreviewed,
engine_met, cites_engine, unit_adds_no_lines and the unit_head they read); the bash copies stay until the verify commands that still call them move."""

import glob
import os
import re
import subprocess
import sys
import tempfile

from pstack_core.collate import glob_sorted
from pstack_core.review import steps_file
from pstack_core.spawn import field
from pstack_core.store import expected_report, header_field, jq_raw, pair_json
from pstack_core.variant import commit_of

from . import kpy


def unit_head(store, brief):
    """A unit's head: its report's head:, else its last recorded step; '' when neither names a commit."""
    root = jq_raw(pair_json(store).get('git_root'))
    report = expected_report(store, brief)
    head = ''
    if os.path.isfile(report):
        head = commit_of(root, header_field(report, 'head')) or ''
    if not head:
        steps = steps_file(store, brief)
        if os.path.isfile(steps) and os.path.getsize(steps) > 0:
            with open(steps, 'rb') as f:
                last = f.read().decode('utf-8', 'surrogateescape').rstrip('\n').split('\n')[-1].split('\t')
            head = commit_of(root, last[1] if len(last) > 1 else '') or ''
    return head


def dispatch_head(store, brief):
    """`jq -r --arg u <brief name> '.heads[$u] // empty' pair.json`: jq's own error for a heads that is not an object."""
    sys.stdout.flush()
    sys.stderr.flush()
    name = os.path.basename(brief)
    name = name[:-3] if name.endswith('.md') else name
    r = subprocess.run(['jq', '-r', '--arg', 'u', name, '.heads[$u] // empty', f'{store}/pair.json'], stdout=subprocess.PIPE, check=False)
    return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')


def git_ok(root, *args, output=None):
    return subprocess.run(['git', '-C', root, *args], stdout=output if output is not None else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0


def unit_adds_no_lines(store, brief):
    """Whether the unit's range only deletes: every changed path is deleted, or modified by removing lines only, with its mode unchanged. It reads
    `git diff --raw -z --no-renames` and `--numstat -z` from the merge-base of the dispatch head and the unit head, NUL-separated so a path is never
    quoted; a rename is a delete plus an add; a modified binary is a change; any failing git command refuses."""
    root = jq_raw(pair_json(store).get('git_root'))
    base, head = dispatch_head(store, brief), unit_head(store, brief)
    if not base or not head:
        return False
    if not git_ok(root, 'rev-parse', '--verify', '--quiet', f'{base}^{{commit}}'):
        return False
    with tempfile.TemporaryFile() as names, tempfile.TemporaryFile() as nums:
        if not (git_ok(root, 'diff', '--raw', '-z', '--no-renames', f'{base}...{head}', output=names)
                and git_ok(root, 'diff', '--numstat', '-z', '--no-renames', f'{base}...{head}', output=nums)):
            return False
        names.seek(0)
        nums.seek(0)
        raw, numstat = names.read().decode('utf-8', 'surrogateescape'), nums.read().decode('utf-8', 'surrogateescape')
    ok, modified, counted = True, set(), set()
    parts = raw.split('\0')
    for i in range(0, len(parts) - 1, 2):
        record, path = parts[i], parts[i + 1]
        fields = record.lstrip(':').split(' ')
        old_mode, new_mode, status = (fields + ['', '', '', '', ''])[0], (fields + ['', '', '', '', ''])[1], (fields + ['', '', '', '', ''])[4]
        if status == 'D':
            continue
        if status == 'M':
            if old_mode != new_mode:
                ok = False
            modified.add(path)
        else:
            ok = False
    for record in numstat.split('\0'):
        if not record:
            continue
        added, _, rest = record.partition('\t')
        deleted, _, path = rest.partition('\t')
        if path not in modified:
            continue
        counted.add(path)
        if not (added.isdigit() and deleted.isdigit() and int(added) == 0 and int(deleted) > 0):
            ok = False
    return ok and modified <= counted


def cites_engine(review, engine):
    cited = header_field(review, 'engine')
    first, _, rest = cited.partition(' ')
    return first == engine and ' ' in cited


def engine_met(store, brief, review, engine):
    """(met, gap): the review cites a run of the engine, or says `engine: skipped (deletion-only, proof: ...)` over a range that only deletes."""
    if cites_engine(review, engine):
        return True, ''
    if re.fullmatch(r'skipped \(deletion-only, proof: .+\)', header_field(review, 'engine')):
        if unit_adds_no_lines(store, brief):
            return True, ''
        return False, '; skipped is only for a deletion-only range'
    return False, ''


def is_landing(brief):
    return header_field(brief, 'playbook') in ('opening-a-pr', 'shipping') or header_field(brief, 'landing') == 'yes'


def review_engine(store):
    """`kpy validate | jq -r '.review_engine // empty'`, stderr dropped and any failure read as no engine."""
    rc, out, err = kpy.run(jq_raw(pair_json(store).get('git_root')), ['--json', 'validate'])
    try:
        import json
        value = json.loads(out).get('review_engine')
    except (ValueError, AttributeError):
        return ''
    return '' if value is None or value is False else jq_raw(value)


def warn_unreviewed(store):
    """A `warning: unreviewed:` line for each accepted unit whose review note lacks the run the kitchen's [review].engine requires; nothing when no
    engine is set."""
    engine = review_engine(store)
    if not engine:
        return
    for review in glob_sorted(glob.glob(f'{glob.escape(store)}/reviews/[0-9][0-9][0-9]-*.md')):
        if not os.path.exists(review) or header_field(review, 'verdict') != 'accept':
            continue
        seq = os.path.basename(review)[:3]
        briefs = glob_sorted(glob.glob(f'{glob.escape(store)}/briefs/{seq}-*.md'))
        brief = briefs[0] if briefs else ''
        if not brief or header_field(brief, 'commit')[:3] != 'yes' or is_landing(brief):
            continue
        met, gap = engine_met(store, brief, review, engine)
        if not met:
            sys.stdout.write(f'warning: unreviewed: unit {seq} cites no {engine} run; review its range with {engine} and add "engine: {engine} <run id or artifact path>" to {review}{gap}\n')
