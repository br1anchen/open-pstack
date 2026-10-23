"""A unit's git position, as three things (plan 021, A1): the facts the store records (`RecordedUnit`), the target stack's commits and patch-ids
(`StackIndex`), and the answer for one against the other (`Inclusion`). `Verdict` reads a verdict file.

A failing git command is an empty answer and the function goes on; a git result is read for what it prints. A git stderr that is not dropped
(quiet) stays on stderr."""

import glob
import os
import re
import sys

from pstack_core.store import expected_report, read_text, header_field, load, peek, read, text_at
from pstack_core.errors import BadLandingBase, BadRevision, HeadNotCarried, NoBrief, UnitNotCheckable, translate
from pstack_core import git
from pstack_core.review import range_floor, trunk_ref
from pstack_core.steplog import StepLog
from pstack_core.variant import commit_of



def has_pair(store):
    """Whether the store has a pair.json."""
    return os.path.isfile(f'{store}/pair.json')


def repo_root(store):
    """`.git_root` of pair.json; a store with none is a MissingStore."""
    return read(store, 'git_root')


def recorded_head(store, unit):
    """A unit's recorded base: heads[unit], empty when there is none."""
    return text_at(peek(load(store), 'heads'), unit)


def is_ancestor(root, older, newer):
    """`git merge-base --is-ancestor older newer 2>/dev/null`: an empty or unknown name is not an ancestor."""
    return git.ok(root, 'merge-base', '--is-ancestor', older, newer)


def patch_id(root, commit, inverse=False, u0=False):
    """The patch-id of a commit's own change (or, inverse, of the diff from the commit to its parent: not `diff-tree -R`, which swaps the a/ and b/
    prefixes patch-id hashes). u0 is the same patch with no context lines, which a later edit near the change cannot alter."""
    args = ['diff-tree', '-p', '--binary', '--full-index', *(['-U0'] if u0 else []), commit, *([f'{commit}^'] if inverse else [])]
    return first_field(git.run(root, *args).stdout)


def first_field(diff):
    """The patch-id (`git patch-id --stable`) of a diff, without trailing newlines."""
    out = git.run(None, 'patch-id', '--stable', stdin=diff).stdout
    return '\n'.join(line.split(' ')[0] for line in out.split('\n')).rstrip('\n')


def dispatch_head(store, brief):
    """The base recorded for the brief's unit; a heads that is not an object is damage."""
    name = os.path.basename(brief)
    return recorded_head(store, name[:-3] if name.endswith('.md') else name)


def unit_head(store, brief):
    """A unit's head: its report's head:, else its last recorded step; '' when neither names a commit."""
    root = repo_root(store)
    report = expected_report(store, brief)
    head = ''
    if os.path.isfile(report):
        head = commit_of(root, header_field(report, 'head')) or ''
    if not head:
        last = StepLog(store, brief).last()
        if last:
            head = commit_of(root, last.sha) or ''
    return head


def unit_brief(store, nnn):
    """The unit's brief path (the first by `ls`), or NoBrief."""
    found = sorted(glob.glob(f'{glob.escape(store)}/briefs/{glob.escape(nnn)}-*.md'))
    if not found:
        raise NoBrief(f'no brief for unit {nnn}')
    return found[0]


class RecordedUnit:
    """What the store records of a unit: its brief, the repo, and its head (`unit_head`). The dispatch head is read on each call: a `.heads` that is not an object
    is damage every time."""

    def __init__(self, store, brief):
        self.store = store
        self.brief = brief
        self.id = os.path.basename(brief)[:-3] if brief.endswith('.md') else os.path.basename(brief)
        self.root = repo_root(store)
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
        listed = git.run(self.root, 'rev-list', '--reverse', '--no-merges', f'{self.base}..{self.head}').stdout
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
    a later stack commit, undoes them), 'merge' (its range holds a merge, which patch-ids do not cover) or 'missing'. reason is
    '', 'merge' or 'reverted'."""

    def __init__(self, kind, sha='', by='', reason=''):
        self.kind, self.sha, self.by, self.reason = kind, sha, by, reason

    @property
    def covered(self):
        return self.kind in ('direct', 'rebased')


def rebased_in(unit, index):
    """Whether the unit's commits were rebased into the stack. Every commit of the unit's range (its dispatch head to its head)
    has a patch-id among the stack's, and no stack commit after the one matching the unit's head reverts the unit: has, with no context lines, the
    patch-id of the inverse of the unit's net change or of one of its commits. A range that holds a merge is not matched; a unit with commits but
    no net change is covered by its patch-ids alone."""
    root, base, head = unit.root, unit.base(), unit.head
    if not base or not index.base:
        return Inclusion('missing')
    if not git.ok(root, 'rev-parse', '--verify', '--quiet', f'{base}^{{commit}}'):
        return Inclusion('missing')
    if any(git.run(root, 'rev-list', '--merges', f'{base}..{head}').stdout.split('\n')[:1]):
        return Inclusion('merge', reason='merge')
    index.load()
    undo, tip_id, ids = set(), '', 0
    for commit in (c for c in git.run(root, 'rev-list', '--no-merges', f'{base}..{head}').stdout.split('\n') if c):
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
    net = first_field(git.run(root, 'diff-tree', '-p', '--binary', '--full-index', '-U0', head, base).stdout)
    if net:
        undo.add(net)
    sha = index.by_id[tip_id]
    for later in index.commits[index.position[sha] + 1:]:
        if index.id0_of[later] and index.id0_of[later] in undo:
            return Inclusion('reverted', by=later, reason='reverted')
    return Inclusion('rebased', sha=sha)


def inclusion(unit, index):
    """Whether a unit is covered by a landing range: its head is in base..head, or its commits were rebased into it."""
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
    root = repo_root(store)
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
        found = rebased_in(RecordedUnit(store, brief), StackIndex.build(root, git.run(root, 'merge-base', trunk, head, quiet=True).stdout.rstrip('\n'), head))
        if found.kind == 'rebased':
            sys.stderr.write(f"note: unit {unit}'s head is not in {head[:9]}; its commits match by patch-id (rebased as {found.sha[:9]})\n")
        elif found.reason == 'reverted':
            raise HeadNotCarried(f"unit {unit}'s commits match by patch-id but {found.by[:9]} reverts them in the checked-out head {head[:9]} (unit {last})")
        else:
            raise HeadNotCarried(f"unit {unit}'s head {uhead[:9]} is not in the checked-out head {head[:9]} (unit {last}); name the unit that carries all the others last")


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
            lines = read_text(self.path).split('\n')
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
    the command. parent_override and parent_label are this call's `--parent` ('none' clears the recorded
    one); range_base is `--base`, which leaves the recorded parent unread."""
    parent, label = parent_override, parent_label
    if not parent and label != 'none':
        parent = read(store, 'landing.parent')
        label = read(store, 'landing.parent_ref')
    if parent and not range_base:
        found = commit_of(root, parent) or ''
        if found and found != head and is_ancestor(root, found, head):
            sys.stdout.write(f'landing base: recorded parent {label} ({found[:9]})\n')
            return found
        if parent_override:
            raise BadLandingBase(f'--parent {label} ({parent[:9]}) must be an ancestor of {head[:9]} and not the same commit; name where the stack starts')
        sys.stdout.write(f'landing base: recorded parent {label} ({parent[:9]}) is no longer an ancestor of {head[:9]}; using the trunk merge-base\n')
    trunk = trunk_ref(root) or ''
    if not trunk:
        raise BadLandingBase("cannot tell this repo's trunk; pass --base <trunk ref or commit>")
    merged = git.run(root, 'merge-base', trunk, head)
    base = merged.stdout.rstrip('\n')
    if merged.returncode != 0:
        raise BadLandingBase(f'no merge-base between {trunk} and HEAD; pass --base')
    name = trunk.removeprefix('refs/')
    if base == head:
        raise BadLandingBase(f'HEAD is already on {name}, so the landing range is empty; pass --base <where the stack starts>')
    sys.stdout.write(f'landing base: merge-base of {name} and HEAD ({base[:9]})\n')
    return base


def brief_or_nothing(ctx, store, nnn):
    """The unit's brief: a missing brief says so on stderr and gives an empty path, and the command around it goes on."""
    try:
        return unit_brief(store, nnn)
    except NoBrief as e:
        sys.stdout.flush()
        sys.stderr.write(translate(e, ctx.prog)[0])
        return ''


def oldest_base(ctx, store, units):
    """The oldest dispatch head among the units by git ancestry, whatever order they are named in. A unit never dispatched ends the command when it
    is the first; later ones without a recorded base are left out. Two heads neither of which is an ancestor of the other have no order."""
    root = repo_root(store)
    oldest, owner = '', ''
    for unit in units:
        base = dispatch_head(store, brief_or_nothing(ctx, store, unit))
        if not base:
            if not owner and unit == units[0]:
                raise UnitNotCheckable(f'unit {unit} was never dispatched')
            continue
        if not owner or is_ancestor(root, base, oldest):
            oldest, owner = base, unit
        elif not is_ancestor(root, oldest, base):
            raise UnitNotCheckable(f'units {owner} and {unit} have no common order: pass --base REV')
    return oldest


def unit_range(ctx, store, units, covered=(), range_base=''):
    """(base, head) for the units from their dispatch heads and done reports: the base is the oldest unit's dispatch head, the head the last unit's
    report head. A unit named in covered needs no done report: a later
    fix unit carries it. A partial, blocked or failed unit can still be checked at the head its report names (a note says it is not done). The
    base moves up to where the unit's branch leaves trunk when trunk has replaced the recorded one (a note says so), unless range_base is given."""
    first, last = units[0], units[-1]
    base = oldest_base(ctx, store, units) if not range_base else ''
    if range_base and not dispatch_head(store, brief_or_nothing(ctx, store, first)):
        raise UnitNotCheckable(f'unit {first} was never dispatched')
    for unit in units:
        if unit in covered:
            continue
        report = expected_report(store, brief_or_nothing(ctx, store, unit))
        if not os.path.isfile(report):
            raise UnitNotCheckable(f'unit {unit} has no report')
        status = header_field(report, 'status')
        if status == 'done':
            continue
        if status in ('partial', 'blocked', 'failed'):
            sys.stdout.flush()
            sys.stderr.write(f"note: unit {unit}'s report is {status}; checking the head it names\n")
        elif status == '':
            raise UnitNotCheckable(f"unit {unit}'s report has no status:")
        else:
            raise UnitNotCheckable(f"unit {unit}'s report is {status}, not done, partial, blocked, or failed")
    report = expected_report(store, brief_or_nothing(ctx, store, last))
    if not os.path.exists(report):
        sys.stdout.flush()
        sys.stderr.write(f'{ctx.prog}: cannot read {report}: No such file or directory\n')
    head = header_field(report, 'head')
    if not head:
        raise UnitNotCheckable(f"unit {last}'s report has no head:")
    root = repo_root(store)
    resolved = commit_of(root, head)
    if resolved is None:
        raise UnitNotCheckable(f"unit {last}'s head is not a commit")
    head = resolved
    if range_base:
        base = commit_of(root, range_base)
        if base is None:
            raise BadRevision(f'--base {range_base} is not a commit')
    else:
        base, note = range_floor(root, base, head)
        if note:
            sys.stdout.flush()
            sys.stderr.write(f"note: unit {first}'s {note}\n")
    return base, head
