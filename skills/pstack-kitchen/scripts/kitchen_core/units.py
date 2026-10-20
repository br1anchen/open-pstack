"""A unit's git position, as three things (plan 021, A1): the facts the store records (`RecordedUnit`), the target stack's commits and patch-ids
(`StackIndex`), and the answer for one against the other (`Inclusion`). `Verdict` reads a verdict file. Ported from kitchen.sh at b632713: unit_head,
unit_brief, commit_patch_id, stack_patch_ids, unit_rebased_in, unit_heads_in, unit_in_landing_range, verdict_range and verdict_problem.

The bash ran these where `set -e` is off (inside `if` and `||`), so a failing git or jq command is an empty answer and the function goes on; here a
git result is read for what it prints. A git or jq stderr the bash did not redirect stays on stderr."""

import glob
import os
import re
import subprocess
import sys

from pstack_core.collate import glob_sorted
from pstack_core.errors import Die
from pstack_core.review import steps_file, trunk_ref
from pstack_core.store import expected_report, header_field, jq_raw, pair_json
from pstack_core.variant import commit_of


def _text(data):
    return data.decode('utf-8', 'surrogateescape')


def git(root, *args, quiet=False, stdin=None):
    """(status, stdout) of `git -C root ...`; stderr is dropped when quiet (the bash's 2>/dev/null) and left on stderr otherwise."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['git', '-C', root, *args], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if quiet else None, check=False)
    return r.returncode, _text(r.stdout)


def is_ancestor(root, older, newer):
    """`git merge-base --is-ancestor older newer 2>/dev/null`: an empty or unknown name is not an ancestor."""
    return git(root, 'merge-base', '--is-ancestor', older, newer, quiet=True)[0] == 0


def patch_id(root, commit, inverse=False, u0=False):
    """The patch-id of a commit's own change (or, inverse, of the diff from the commit to its parent: not `diff-tree -R`, which swaps the a/ and b/
    prefixes patch-id hashes). u0 is the same patch with no context lines, which a later edit near the change cannot alter."""
    args = ['diff-tree', '-p', '--binary', '--full-index', *(['-U0'] if u0 else []), commit, *([f'{commit}^'] if inverse else [])]
    return first_field(git(root, *args)[1])


def first_field(diff):
    """`git patch-id --stable | cut -d' ' -f1` over a diff, with the trailing newlines `$(...)` drops."""
    sys.stdout.flush()
    sys.stderr.flush()
    out = subprocess.run(['git', 'patch-id', '--stable'], input=diff.encode('utf-8', 'surrogateescape'), stdout=subprocess.PIPE, check=False).stdout
    return '\n'.join(line.split(' ')[0] for line in _text(out).split('\n')).rstrip('\n')


def dispatch_head(store, brief):
    """`jq -r --arg u <brief name> '.heads[$u] // empty' pair.json`: jq's own error for a heads that is not an object, on every call."""
    sys.stdout.flush()
    sys.stderr.flush()
    name = os.path.basename(brief)
    name = name[:-3] if name.endswith('.md') else name
    r = subprocess.run(['jq', '-r', '--arg', 'u', name, '.heads[$u] // empty', f'{store}/pair.json'], stdout=subprocess.PIPE, check=False)
    return _text(r.stdout).rstrip('\n')


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
                last = _text(f.read()).rstrip('\n').split('\n')[-1].split('\t')
            head = commit_of(root, last[1] if len(last) > 1 else '') or ''
    return head


def unit_brief(store, nnn):
    """The unit's brief path (the first by `ls`), or the command's death."""
    found = glob_sorted(glob.glob(f'{glob.escape(store)}/briefs/{glob.escape(nnn)}-*.md'))
    if not found:
        raise Die(f'no brief for unit {nnn}')
    return found[0]


class RecordedUnit:
    """What the store records of a unit: its brief, the repo, and its head (`unit_head`). The dispatch head is read on each call, as the bash ran its
    jq on each call: a `.heads` that is not an object makes jq print its error every time."""

    def __init__(self, store, brief):
        self.store = store
        self.brief = brief
        self.id = os.path.basename(brief)[:-3] if brief.endswith('.md') else os.path.basename(brief)
        self.root = jq_raw(pair_json(store).get('git_root'))
        self.head = unit_head(store, brief)

    def base(self):
        """The head the unit was dispatched at ('' when none)."""
        return dispatch_head(self.store, self.brief)

    @property
    def status(self):
        return header_field(expected_report(self.store, self.brief), 'status')


class StackIndex:
    """The commits of <base>..<head> (merges left out), oldest first, with each one's patch-id at the default context and with none. It reads git
    once, on first use, and caches only git's answers. A patch-id that two commits share belongs to the newer one."""

    def __init__(self, root, base, head):
        self.root, self.base, self.head = root, base, head
        self._loaded = False
        self.commits, self.position, self.id_of, self.id0_of, self.by_id = [], {}, {}, {}, {}

    @classmethod
    def build(cls, root, base, head):
        return cls(root, base, head)

    def load(self):
        if self._loaded:
            return self
        self._loaded = True
        listed = git(self.root, 'rev-list', '--reverse', '--no-merges', f'{self.base}..{self.head}')[1]
        for n, commit in enumerate(c for c in listed.split('\n') if c):
            ident = patch_id(self.root, commit)
            self.commits.append(commit)
            self.position[commit] = n
            self.id_of[commit] = ident
            self.id0_of[commit] = patch_id(self.root, commit, u0=True)
            if ident:
                self.by_id[ident] = commit
        return self


class Inclusion:
    """Where one unit stands against one stack. kind: 'direct' (its head is in the stack head and not in the base), 'ancestor' (its head is in the
    base: landed before it), 'rebased' (every commit matches by patch-id; sha is the stack commit carrying its tip), 'reverted' (they match, and by,
    a later stack commit, undoes them), 'merge' (its range holds a merge, which patch-ids do not cover) or 'missing'. reason is the bash's
    rebased_why: '', 'merge' or 'reverted'."""

    def __init__(self, kind, sha='', by='', reason=''):
        self.kind, self.sha, self.by, self.reason = kind, sha, by, reason

    @property
    def covered(self):
        return self.kind in ('direct', 'rebased')


def rebased_in(unit, index):
    """unit_rebased_in: whether the unit's commits were rebased into the stack. Every commit of the unit's range (its dispatch head to its head)
    has a patch-id among the stack's, and no stack commit after the one matching the unit's head reverts the unit: has, with no context lines, the
    patch-id of the inverse of the unit's net change or of one of its commits. A range that holds a merge is not matched; a unit with commits but
    no net change is covered by its patch-ids alone."""
    root, base, head = unit.root, unit.base(), unit.head
    if not base or not index.base:
        return Inclusion('missing')
    if git(root, 'rev-parse', '--verify', '--quiet', f'{base}^{{commit}}', quiet=True)[0] != 0:
        return Inclusion('missing')
    if any(git(root, 'rev-list', '--merges', f'{base}..{head}')[1].split('\n')[:1]):
        return Inclusion('merge', reason='merge')
    index.load()
    undo, tip_id, ids = set(), '', 0
    for commit in (c for c in git(root, 'rev-list', '--no-merges', f'{base}..{head}')[1].split('\n') if c):
        ident = patch_id(root, commit)
        if not ident:
            continue
        if not index.by_id.get(ident):
            return Inclusion('missing')
        ids += 1
        tip_id = tip_id or ident
        inverted = patch_id(root, commit, inverse=True, u0=True)
        if inverted:
            undo.add(inverted)
    if ids == 0:
        return Inclusion('missing')
    net = first_field(git(root, 'diff-tree', '-p', '--binary', '--full-index', '-U0', head, base)[1])
    if net:
        undo.add(net)
    sha = index.by_id[tip_id]
    for later in index.commits[index.position[sha] + 1:]:
        if index.id0_of[later] and index.id0_of[later] in undo:
            return Inclusion('reverted', by=later, reason='reverted')
    return Inclusion('rebased', sha=sha)


def inclusion(unit, index):
    """unit_in_landing_range: whether a unit is covered by a landing range: its head is in base..head, or its commits were rebased into it."""
    if not unit.head:
        return Inclusion('missing')
    if is_ancestor(unit.root, unit.head, index.head):
        return Inclusion('ancestor') if is_ancestor(unit.root, unit.head, index.base) else Inclusion('direct')
    return rebased_in(unit, index)


def heads_in(store, head, units):
    """unit_heads_in: every unit this verification names must be in the head it checks out, the last unit's. A unit is judged at its head, or for
    a covered unit with none, at its last recorded step; one with no usable head passes with a note. Notes go to stderr; a unit that is not in
    the head ends the command with exit 2."""
    last = units[-1]
    root = jq_raw(pair_json(store).get('git_root'))
    for unit in units:
        if unit == last:
            continue
        brief = unit_brief(store, unit)
        uhead = unit_head(store, brief)
        if not uhead:
            sys.stderr.write(f'note: unit {unit} has no usable head; its inclusion was not checked\n')
            continue
        if is_ancestor(root, uhead, head):
            continue
        trunk = trunk_ref(root) or ''
        found = rebased_in(RecordedUnit(store, brief), StackIndex.build(root, git(root, 'merge-base', trunk, head, quiet=True)[1].rstrip('\n'), head))
        if found.kind == 'rebased':
            sys.stderr.write(f"note: unit {unit}'s head is not in {head[:9]}; its commits match by patch-id (rebased as {found.sha[:9]})\n")
        elif found.reason == 'reverted':
            raise Die(f"unit {unit}'s commits match by patch-id but {found.by[:9]} reverts them in the checked-out head {head[:9]} (unit {last})", 2)
        else:
            raise Die(f"unit {unit}'s head {uhead[:9]} is not in the checked-out head {head[:9]} (unit {last}); name the unit that carries all the others last", 2)


class Verdict:
    """A verdict file: its status, the units it names, the range it was made over (the packet's, which the kitchen wrote, else the one the verifier
    copied into the verdict) and whether it is a landing verdict. A malformed field is read lazily: `problem` says what is wrong, and nothing else
    looks at it."""

    def __init__(self, path, status, units, range_base, range_head):
        self.path, self.status, self.units = path, status, units
        self.range_base, self.range_head = range_base, range_head

    @classmethod
    def parse(cls, path):
        packet = (path[:-3] if path.endswith('.md') else path) + '-packet.md'
        span = header_field(packet, 'range') or header_field(path, 'range')
        span = re.split(r'[ \t\n\v\f\r]', span, maxsplit=1)[0]
        return cls(path, header_field(path, 'status'), header_field(path, 'units'), span.split('..', 1)[0], span.rsplit('..', 1)[-1])

    @property
    def landing(self):
        return self.units.startswith('landing')

    def refs(self):
        """`read -r base head <<<"<base> <head>"`: an empty base shifts the head into the first name."""
        parts = f'{self.range_base} {self.range_head}'.split(None, 1)
        return (parts[0] if parts else ''), (parts[1].strip() if len(parts) > 1 else '')

    @property
    def problem(self):
        """Empty when the verdict is well formed, else what is wrong."""
        if self.status not in ('clean', 'reject', 'inconclusive'):
            return 'status is not clean, reject, or inconclusive'
        fences, evidence = 0, False
        try:
            with open(self.path, 'rb') as f:
                lines = _text(f.read()).split('\n')
        except OSError:
            return 'no command output under Evidence'
        for line in lines:
            if line.startswith('## Evidence'):
                evidence = True
                continue
            if line.startswith('## '):
                evidence = False
            if evidence and line.startswith('```'):
                fences += 1
        return '' if fences >= 2 else 'no command output under Evidence'
