"""A unit's git position, as three things (plan 021, A1): the facts the store records (`RecordedUnit`), the target stack's commits and patch-ids
(`StackIndex`), and the answer for one against the other (`Inclusion`). `Verdict` reads a verdict file. Ported from kitchen.sh at b632713: unit_head,
unit_brief, commit_patch_id, stack_patch_ids, unit_rebased_in, unit_heads_in, unit_in_landing_range, verdict_range and verdict_problem.

The bash ran these where `set -e` is off (inside `if` and `||`), so a failing git command is an empty answer and the function goes on; here a
git result is read for what it prints. A git stderr the bash did not redirect stays on stderr."""

import glob
import os
import re
import subprocess
import sys

from pstack_core.collate import glob_sorted
from pstack_core.commands.report import MISSING_PAIR
from pstack_core.errors import Die, Exit
from pstack_core.review import range_floor, steps_file, trunk_ref
from pstack_core.store import alt, expected_report, get, header_field, jq_raw, last_line, load, peek, read
from pstack_core.variant import commit_of
from pstack_core.wait import as_text



def _text(data):
    return data.decode('utf-8', 'surrogateescape')


def exit_status(returncode):
    """The status a shell reports for a child: 128 plus the signal number when a signal killed it (subprocess and waitpid give -N)."""
    return 128 - returncode if returncode < 0 else returncode


TOLD = set()


def has_pair(store, check=False, quiet=False):
    """Whether the store has a pair.json. A command calls this once, before its first read; a store without one gets the two lines a missing
    pair.json gets (quiet leaves them out) and, with check, the command ends with 2. The message is told once for a store in one process, however
    many reads follow."""
    if os.path.isfile(f'{store}/pair.json'):
        return True
    if not quiet and os.path.realpath(store) not in TOLD:
        TOLD.add(os.path.realpath(store))
        sys.stdout.flush()
        sys.stderr.write(MISSING_PAIR.format(store=store))
        sys.stderr.flush()
    if check:
        raise Exit(2)
    return False


def git_root(store, quiet=False, check=False):
    """`.git_root` of pair.json; empty for a store with no pair.json, after has_pair's message."""
    if not has_pair(store, check, quiet):
        return ''
    return jq_raw(get(store, 'git_root'))


def recorded_head(store, unit):
    """A unit's recorded base: heads[unit], empty when there is none."""
    return as_text(alt(peek(load(store), 'heads').get(unit), ''))


def git(root, *args, quiet=False, stdin=None):
    """(status, stdout) of `git -C root ...`; stderr is dropped when quiet (the bash's 2>/dev/null) and left on stderr otherwise."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['git', '-C', root, *args], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if quiet else None, check=False)
    return exit_status(r.returncode), _text(r.stdout)


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
    """The base recorded for the brief's unit; a heads that is not an object is damage."""
    name = os.path.basename(brief)
    return recorded_head(store, name[:-3] if name.endswith('.md') else name)


def unit_head(store, brief):
    """A unit's head: its report's head:, else its last recorded step; '' when neither names a commit."""
    root = git_root(store)
    report = expected_report(store, brief)
    head = ''
    if os.path.isfile(report):
        head = commit_of(root, header_field(report, 'head')) or ''
    if not head:
        steps = steps_file(store, brief)
        if os.path.isfile(steps) and os.path.getsize(steps) > 0:
            row = last_line(steps)
            cols = row.split('\t')
            head = commit_of(root, cols[1] if len(cols) > 1 else row) or ''
    return head


def unit_brief(store, nnn):
    """The unit's brief path (the first by `ls`), or the command's death."""
    found = glob_sorted(glob.glob(f'{glob.escape(store)}/briefs/{glob.escape(nnn)}-*.md'))
    if not found:
        raise Die(f'no brief for unit {nnn}')
    return found[0]


class RecordedUnit:
    """What the store records of a unit: its brief, the repo, and its head (`unit_head`). The dispatch head is read on each call: a `.heads` that is not an object
    is damage every time."""

    def __init__(self, store, brief):
        self.store = store
        self.brief = brief
        self.id = os.path.basename(brief)[:-3] if brief.endswith('.md') else os.path.basename(brief)
        self.root = git_root(store)
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
    root = git_root(store)
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


def landing_base(store, root, head, parent_override='', parent_label='', range_base=''):
    """Where a stack at head leaves trunk, or at the parent recorded in the store (the one `verify --landing --parent` recorded earlier, else the
    override of this call), while that parent is an ancestor of head. Prints the `landing base:` line and returns the revision; its refusals end
    the command. parent_override and parent_label are the bash's `landing_parent_override` and `landing_parent_label` ('none' clears the recorded
    one); range_base is `--base`, which leaves the recorded parent unread."""
    parent, label = parent_override, parent_label
    if not parent and label != 'none':
        parent = read(store, 'landing.parent', empty=True)
        label = read(store, 'landing.parent_ref', empty=True)
    if parent and not range_base:
        found = commit_of(root, parent) or ''
        if found and found != head and is_ancestor(root, found, head):
            sys.stdout.write(f'landing base: recorded parent {label} ({found[:9]})\n')
            return found
        if parent_override:
            raise Die(f'--parent {label} ({parent[:9]}) must be an ancestor of {head[:9]} and not the same commit; name where the stack starts')
        sys.stdout.write(f'landing base: recorded parent {label} ({parent[:9]}) is no longer an ancestor of {head[:9]}; using the trunk merge-base\n')
    trunk = trunk_ref(root) or ''
    if not trunk:
        raise Die("cannot tell this repo's trunk; pass --base <trunk ref or commit>")
    status, out = git(root, 'merge-base', trunk, head)
    base = out.rstrip('\n')
    if status != 0:
        raise Die(f'no merge-base between {trunk} and HEAD; pass --base')
    name = trunk.removeprefix('refs/')
    if base == head:
        raise Die(f'HEAD is already on {name}, so the landing range is empty; pass --base <where the stack starts>')
    sys.stdout.write(f'landing base: merge-base of {name} and HEAD ({base[:9]})\n')
    return base


def brief_or_nothing(store, nnn):
    """`$(unit_brief "$store" NNN)` inside another command's arguments: a missing brief says so on stderr and gives an empty path, and the command
    around it goes on."""
    try:
        return unit_brief(store, nnn)
    except Die as e:
        sys.stdout.flush()
        sys.stderr.write(f'pair.sh: {e.message}\n')
        return ''


def unit_range(store, units, covered=(), range_base=''):
    """(base, head) for the units, first to last, from their dispatch heads and done reports. A unit named in covered needs no done report: a later
    fix unit carries it. A partial, blocked or failed unit can still be checked at the head its report names (a note says it is not done). The
    base moves up to where the unit's branch leaves trunk when trunk has replaced the recorded one (a note says so), unless range_base is given."""
    first, last = units[0], units[-1]
    brief = brief_or_nothing(store, first)
    base = dispatch_head(store, brief)
    if not base:
        raise Die(f'unit {first} was never dispatched')
    for unit in units:
        if unit in covered:
            continue
        report = expected_report(store, brief_or_nothing(store, unit))
        if not os.path.isfile(report):
            raise Die(f'unit {unit} has no report')
        status = header_field(report, 'status')
        if status == 'done':
            continue
        if status in ('partial', 'blocked', 'failed'):
            sys.stdout.flush()
            sys.stderr.write(f"note: unit {unit}'s report is {status}; checking the head it names\n")
        elif status == '':
            raise Die(f"unit {unit}'s report has no status:")
        else:
            raise Die(f"unit {unit}'s report is {status}, not done, partial, blocked, or failed")
    report = expected_report(store, brief_or_nothing(store, last))
    if not os.path.exists(report):  # the bash reads the head with grep, which says so
        sys.stdout.flush()
        sys.stderr.write(f'grep: {report}: No such file or directory\n')
    head = header_field(report, 'head')
    if not head:
        raise Die(f"unit {last}'s report has no head:")
    root = git_root(store)
    resolved = commit_of(root, head)
    if resolved is None:
        raise Die(f"unit {last}'s head is not a commit")
    head = resolved
    if range_base:
        base = commit_of(root, range_base)
        if base is None:
            raise Die(f'--base {range_base} is not a commit')
    else:
        base, note = range_floor(root, base, head)
        if note:
            sys.stdout.flush()
            sys.stderr.write(f"note: unit {first}'s {note}\n")
    return base, head
