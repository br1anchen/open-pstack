"""The gates in front of a brief: the pause, the Scope, and the agreed plan (whose variant part is the plan_gate_extra hook)."""
import glob
import os
import re
import subprocess
import sys

from .checkin import case_pattern, scope_entries
from .collate import glob_sorted
from .errors import Die
from .store import cut_chars, header_field, paused_reason, read_text

PLAN_PLAYBOOKS = ('feature', 'bug-fix', 'refactoring', 'perf-issue', 'pstack-tdd')


def require_not_paused(store):
    reason = paused_reason(store)
    if reason is not None:
        raise Die(f'the store is paused ({reason}): start nothing new; record where the work stands and end your turn, or run pair.sh resume {store}', 5)


def require_agreed_plan(ctx, store, brief, extra):
    """Implementation playbooks may only run against a plan the sidekick agreed to and the master marked agreed. Exit 6 names what
    is missing. extra(ctx, store, plan, review, seq) is the variant's gate on an agreed plan; the condition is an argument, not a
    state of the instance."""
    playbook = header_field(brief, 'playbook')
    if playbook not in PLAN_PLAYBOOKS:
        return
    plan = header_field(brief, 'plan')
    if not plan or plan == 'none':
        raise Die(f'playbook {playbook} needs an agreed plan; brief has plan: {plan or "<missing>"}', 6)
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}', 6)
    seq = cut_chars(os.path.basename(plan.rstrip('/') or plan), 3)
    reviews = glob_sorted(glob.glob(os.path.join(glob.escape(store), 'reviews', glob.escape(seq) + '-*.md')))
    if not reviews:
        raise Die(f'plan {plan} has no review; run pair.sh discuss and write reviews/{seq}-<slug>.md with verdict: agreed', 6)
    review = reviews[0]
    verdict = header_field(review, 'verdict')
    if verdict != 'agreed':
        raise Die(f"plan {plan} review verdict is '{verdict or 'missing'}', not agreed", 6)
    extra(ctx, store, plan, review, seq)


def no_gate(ctx, store, plan, review, seq):
    """plan_gate_extra() of the base pair: nothing more is required."""


def gnu_dirname(path):
    stripped = path.rstrip('/')
    if not stripped:
        return '/' if path else '.'
    head = stripped.rpartition('/')[0]
    if '/' not in stripped:
        return '.'
    return head.rstrip('/') or '/'


def scope_entry_exists(root, entry, tracked):
    """Whether a may-write entry names something that exists, or a new file in a directory that does. A glob matches when a file
    does or its fixed directory prefix exists."""
    # An absolute entry names that path itself, not a path under the repo root.
    at = (lambda path: path) if entry.startswith('/') else (lambda path: f'{root}/{path}')
    if any(c in entry for c in '*?['):
        prefix = re.split(r'[*?\[]', entry, maxsplit=1)[0]
        directory = prefix.rpartition('/')[0] if '/' in prefix else ''
        if entry.startswith('/') and not directory:
            directory = '/'
        if directory and not os.path.isdir(at(directory)):
            return False
        patterns = [case_pattern(entry), case_pattern(entry.removeprefix('**/'))]
        for line in (tracked.split('\n') if tracked else ['']):
            if any(p.fullmatch(line) for p in patterns):
                return True
        return bool(directory)
    if entry.endswith('/'):
        return os.path.isdir(at(entry)) or os.path.isdir(at(gnu_dirname(entry[:-1])))
    return os.path.exists(at(entry)) or os.path.isdir(at(gnu_dirname(entry)))


def scope_lists_several(brief):
    """The unquoted Scope entries that list several paths: "a, b", or "a.ts and b/c.ts" (prose like "README and docs" is not)."""
    found, inside = [], False
    for line in read_text(brief).removesuffix('\n').split('\n'):
        if re.match(r'may write:|must not write:', line):
            inside = True
            continue
        if line.startswith('## '):
            inside = False
        if inside and line.startswith('- '):
            entry = line[2:]
            if entry[:1] == '`':
                continue
            entry = re.sub(r' +(?:—|–|--) .*$', '', entry, count=1)
            entry = re.sub(r' +\(.*\)$', '', entry, count=1)
            if ', ' in entry:
                found.append(f'- {entry}')
                continue
            pieces = re.split(r' and ', entry)
            if len(pieces) > 1 and all('/' in w or re.search(r'\.[A-Za-z0-9]+$', w) for w in pieces):
                found.append(f'- {entry}')
    return found


def check_scope(brief, root):
    """Refuses a brief whose Scope has an unquoted entry listing several paths: a guessed split could widen the Scope silently. Warns,
    without refusing, on an entry that matches nothing in the repo, the usual sign of a path written relative to its neighbour."""
    bad = scope_lists_several(brief)
    if bad:
        raise Die(f'{os.path.basename(brief)}: Scope takes one path or glob per line; split these (a path with a comma goes in backquotes):\n' + '\n'.join(bad))
    r = subprocess.run(['git', '-C', root, 'ls-files', '-co', '--exclude-standard'], capture_output=True, check=False)
    tracked = r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n') if r.returncode == 0 else ''
    for entry in scope_entries(brief, 'may'):
        if entry and not scope_entry_exists(root, entry, tracked):
            sys.stderr.write(f'warning: Scope entry "{entry}" matches no file or directory in {root}; is it relative to a neighbouring entry?\n')
