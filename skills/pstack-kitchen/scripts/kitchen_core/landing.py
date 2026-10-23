"""land-check and revise: whether the stack may land, and the brief that fixes a rejected verdict.

A failing read inside land-check is an empty answer and the check goes on."""

import glob
import os
import re
import sys
import tempfile

from pstack_core.store import field, header_field, read_text, new_from_template, pair_file
from pstack_core.numbers import int_or_none
from pstack_core.errors import Die
from pstack_core import git
from pstack_core.review import trunk_ref
from pstack_core.variant import commit_of

from . import kpy
from .units import RecordedUnit, StackIndex, Verdict, dispatch_head, inclusion, has_pair, is_ancestor, repo_root, unit_brief, unit_head


def out(text):
    sys.stdout.write(text)


def is_landing(brief):
    return header_field(brief, 'playbook') in ('opening-a-pr', 'shipping') or header_field(brief, 'landing') == 'yes'


def review_engine(store):
    """The kitchen's [review].engine, stderr dropped and any failure read as no engine."""
    return validated_engine(store)[1]


def validated_engine(store):
    """(ok, engine): the kitchen's [review].engine from `validate`; not ok when kitchen.py fails, which land-check says."""
    try:
        found = kpy.kitchen_py.validate(kpy.invocation(store) if has_pair(store) else kpy.kitchen_py.Invocation())
    except kpy.KitchenError:
        return False, ''
    return True, found.review_engine


def cites_engine(review, engine):
    """Whether a review note cites a run of the required engine: `engine: <name> <run id or artifact path>`."""
    cited = header_field(review, 'engine')
    return cited.partition(' ')[0] == engine and ' ' in cited


def unit_adds_no_lines(store, brief):
    """Whether the unit's range only deletes: every changed path is deleted, or modified by removing lines only, with its mode unchanged. It reads
    `git diff --raw -z --no-renames` and `--numstat -z` from the merge-base of the dispatch head and the unit head, NUL-separated so a path is never
    quoted; a rename is a delete plus an add; a modified binary is a change; any failing git command refuses."""
    root = repo_root(store)
    base, head = dispatch_head(store, brief), unit_head(store, brief)
    if not base or not head:
        return False
    if not git.ok(root, 'rev-parse', '--verify', '--quiet', f'{base}^{{commit}}'):
        return False
    with tempfile.TemporaryFile() as names, tempfile.TemporaryFile() as nums:
        if not (git.ok(root, 'diff', '--raw', '-z', '--no-renames', f'{base}...{head}', stdout=names)
                and git.ok(root, 'diff', '--numstat', '-z', '--no-renames', f'{base}...{head}', stdout=nums)):
            return False
        names.seek(0)
        nums.seek(0)
        raw, numstat = names.read().decode('utf-8', 'replace'), nums.read().decode('utf-8', 'replace')
    ok, modified, counted = True, set(), set()
    parts = raw.split('\0')
    for i in range(0, len(parts) - 1, 2):
        record, path = parts[i], parts[i + 1]
        fields = (record.lstrip(':').split(' ') + ['', '', '', '', ''])
        old_mode, new_mode, status = fields[0], fields[1], fields[4]
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


def engine_met(store, brief, review, engine):
    """(met, gap): the review cites a run of the engine, or says `engine: skipped (deletion-only, proof: ...)` over a range that only deletes."""
    if cites_engine(review, engine):
        return True, ''
    if re.fullmatch(r'skipped \(deletion-only, proof: .+\)', header_field(review, 'engine')):
        if unit_adds_no_lines(store, brief):
            return True, ''
        return False, '; skipped is only for a deletion-only range'
    return False, ''


def manifest_warning(store, verified_base, current_base, head=''):
    """The warning line, or '' for none. When the stack was rebased off the base a landing was verified on, the dependency manifests
    (kitchen.toml escalate.manifests, matched by kitchen.py) that differ between that base and the current one. A verified base above the current
    one and in the head's history (a --base inside the stack) is the stack's own lower commits, not a moved trunk: no warning."""
    if not verified_base or not current_base or verified_base == current_base:
        return ''
    root = repo_root(store)
    if is_ancestor(root, current_base, verified_base) and is_ancestor(root, verified_base, head or 'HEAD'):
        return ''
    listed = git.run(root, 'diff', '--name-only', '-z', '--no-renames', verified_base, current_base, quiet=True).stdout
    try:
        files = ', '.join(kpy.kitchen_py.manifests(kpy.invocation(store, root), listed.split('\0')))
    except kpy.KitchenError:
        files = ''
    return f'warning: dependency manifests changed since the verified base: {files}\n' if files else ''


def landing_field(store, key):
    """`field "$store" '.landing.<key> // empty'`."""
    return field(store, 'landing', key)


def current_landing_base(store):
    """Where the stack at HEAD leaves trunk now (the recorded landing parent, else the merge-base with trunk), or nothing."""
    root = repo_root(store)
    head = git.run(root, 'rev-parse', 'HEAD', quiet=True).stdout.rstrip('\n')
    parent = landing_field(store, 'parent')
    if parent:
        pc = commit_of(root, parent) or ''
        if pc and pc != head and is_ancestor(root, pc, head):
            return pc
        sys.stdout.flush()
        sys.stderr.write(f'landing base: recorded parent {landing_field(store, "parent_ref")} ({parent[:9]}) is no longer an ancestor of '
                         f'{head[:9]}; using the trunk merge-base\n')
    trunk = trunk_ref(root)
    if not trunk:
        return ''
    found = git.run(root, 'merge-base', trunk, 'HEAD', quiet=True)
    return found.stdout.rstrip('\n') if found.returncode == 0 else ''


def landing_unmatched_note(store, seq, brief, uhead, landing_verdict, base, head, found):
    """Why a unit outside a clean landing verdict's range could not be matched by patch-id, as one land-check note line."""
    root = repo_root(store)
    ubase = dispatch_head(store, brief)
    if base and is_ancestor(root, uhead, base):
        note = f'unit {seq} landed before the base of landing verdict {landing_verdict}; verify it on its own'
    elif found.reason == 'merge':
        note = f"unit {seq}'s range holds a merge commit, so patch-ids do not cover it; verify --landing covers it"
    elif found.reason == 'reverted':
        note = f"unit {seq}'s commits match by patch-id but {found.by[:9]} reverts them"
    elif not base:
        note = (f'landing verdict {landing_verdict} has no base that resolves; unit {seq} cannot be matched by patch-id; verify --landing covers it')
    elif ubase and is_ancestor(root, head, ubase):
        note = f'unit {seq} is newer than landing verdict {landing_verdict}; verify --landing again'
    else:
        note = f"unit {seq}'s head is not in the stack and its commits do not match by patch-id (squashed?); verify --landing covers it"
    return f'land-check: note: {note}\n'


def latest_landing_verdict(store):
    """The newest landing verdict file in the store (its number as `[ -le ]` compares it), or ''."""
    best, found = 0, ''
    for f in sorted(glob.glob(f'{glob.escape(store)}/verdicts/landing-v[0-9]*.md')):
        if not os.path.exists(f) or f.endswith('-packet.md'):
            continue
        number = f.rpartition('-v')[2]
        number = number[:-3] if number.endswith('.md') else number
        n = int_or_none(number)
        if n is None or best is None or n > best:
            best, found = n, f
    return found


def accepted_units(store):
    """(review, seq, brief) for each accepted review of a unit that commits and is not a landing brief, in glob order."""
    for review in sorted(glob.glob(f'{glob.escape(store)}/reviews/[0-9][0-9][0-9]-*.md')):
        if not os.path.exists(review) or header_field(review, 'verdict') != 'accept':
            continue
        seq = os.path.basename(review)[:3]
        briefs = sorted(glob.glob(f'{glob.escape(store)}/briefs/{seq}-*.md'))
        brief = briefs[0] if briefs else ''
        if not brief or header_field(brief, 'commit')[:3] != 'yes' or is_landing(brief):
            continue
        yield review, seq, brief


def clean_verdict_for(store, seq):
    """The last clean, non-packet verdict whose units: line names the unit."""
    found = ''
    pattern = re.compile(rf'^units:(.* )?{seq}( |$)')
    for f in sorted(glob.glob(f'{glob.escape(store)}/verdicts/*-v[0-9]*.md')):
        if not os.path.exists(f) or f.endswith('-packet.md'):
            continue
        try:
            named = any(pattern.search(line) for line in read_text(f).split('\n'))
        except OSError:
            named = False
        if named and header_field(f, 'status') == 'clean':
            found = f
    return found


def land_check(store, say=out):
    """Says what it finds line by line and returns the problem lines, none when the stack may land. A unit with no clean verdict of its own is
    covered by the latest landing verdict when that one is clean and its range's head contains the unit's head. A kitchen that names a review
    engine requires each accepted unit's review note to cite a run of it: `engine: <name> <run id or artifact path>`."""
    problems = []
    ok, engine = validated_engine(store)
    if not ok:
        problem = 'kitchen: validate fails (kitchen.py validate shows why)\n'
        say(problem)
        return [problem]
    root = ''
    landing_verdict = latest_landing_verdict(store)
    landing_head = landing_base = verdict_base_rev = verdict_head_rev = ''
    verdict = None
    if landing_verdict:
        verdict = Verdict.parse(landing_verdict)
        base_ref, head_ref = verdict.refs()
        verdict_base_rev = commit_of(repo_root(store), base_ref) or ''
        root = repo_root(store)
        verdict_head_rev = commit_of(root, head_ref) or ''
    if landing_verdict and verdict.status == 'clean':
        landing_head, landing_base = verdict_head_rev, verdict_base_rev
        if not landing_head:
            say(f'land-check: note: landing verdict {landing_verdict} range head does not resolve; no unit is covered by it\n')
    index = None
    for review, seq, brief in accepted_units(store):
        if engine:
            met, gap = engine_met(store, brief, review, engine)
            if not met:
                problems.append(f'unreviewed: unit {seq} cites no {engine} run; review its range with {engine} and add "engine: {engine} <run id or artifact path>" '
                                f'to {review}{gap}\n')
                say(problems[-1])
        covering = clean_verdict_for(store, seq)
        if not covering and landing_head:
            unit = RecordedUnit(store, brief)
            if unit.head:
                if index is None:
                    index = StackIndex.build(root, landing_base, landing_head)
                found = inclusion(unit, index)
                if found.covered:
                    covering = landing_verdict
                    if found.sha:
                        say(f'covered: unit {seq} (rebased as {found.sha[:9]}) by the clean landing verdict {landing_verdict}\n')
                    else:
                        say(f'covered: unit {seq} by the clean landing verdict {landing_verdict}\n')
                else:
                    say(landing_unmatched_note(store, seq, brief, unit.head, landing_verdict, landing_base, landing_head, found))
        if not covering:
            problems.append(f'unverified: unit {seq} has no clean verdict (kitchen.sh verify {store} {seq})\n')
            say(problems[-1])
    # A landing verification is reported, not required.
    if landing_verdict:
        say(manifest_warning(store, verdict_base_rev, current_landing_base(store)))
        say(f'landing verification: {verdict.status} ({landing_verdict})\n')
        if verdict_head_rev:
            if is_ancestor(root, verdict_head_rev, 'HEAD'):
                counted = git.run(root, 'rev-list', '--count', f'{verdict_head_rev}..HEAD', quiet=True).stdout.rstrip('\n')
                behind = counted or '0'
                try:
                    behind_n = int(behind)
                except ValueError:
                    behind_n = 0
                if behind_n != 0:
                    say(f'landing verdict {landing_verdict} at {verdict_head_rev[:9]}, {behind} commit{"" if behind_n == 1 else "s"} behind HEAD\n')
            else:
                say(f"landing verdict {landing_verdict} at {verdict_head_rev[:9]}, not in HEAD's history (rebased or another branch); verify --landing "
                    'again\n')
    else:
        say(f'landing verification: none; kitchen.sh verify {store} --landing proves the stack tip with a fresh verifier\n')
    if problems:
        return problems
    say(f'land-check: pass (review engine: {engine or "none"})\n')
    return problems


def land_check_problems(store):
    """land_check's problem lines with nothing said."""
    return land_check(store, lambda text: None)


def land_check_command(ctx, args):
    if len(args) != 1:
        raise Die('usage: kitchen.sh land-check <store>')
    pair_file(args[0])
    return 2 if land_check(args[0]) else 0


def section(text, name):
    m = re.search(rf'^## {name}\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    return m.group(1) if m else ''


def header(text, key):
    m = re.search(rf'^{key}: (.*)$', text, re.M)
    return m.group(1) if m else ''


def union(lines):
    return list(dict.fromkeys(lines))


def scope(texts):
    may, mustnot = [], []
    for t in texts:
        cur = None
        for line in section(t, 'Scope').splitlines():
            if line.startswith('may write:'):
                cur = may
            elif line.startswith('must not write:'):
                cur = mustnot
            elif cur is not None and line.startswith('- '):
                cur.append(line)
    return '\nmay write:\n' + '\n'.join(union(may)) + '\n\nmust not write:\n' + '\n'.join(union(mustnot) or ['- none']) + '\n\n'


def lines(texts, name):
    body = union(l for t in texts for l in section(t, name).strip('\n').splitlines() if l.strip())
    return '\n' + '\n'.join(body) + '\n\n'


def revise(ctx, args):
    """The fix brief for a rejected verdict: a unit's own, or for a landing verdict one that carries every unit's Scope, Acceptance, Verify and
    Forbidden."""
    if len(args) != 2:
        raise Die('usage: kitchen.sh revise <store> <verdict-path>')
    store, verdict = args
    if not os.path.isfile(verdict):
        raise Die(f'verdict not found: {verdict}')
    if header_field(verdict, 'status') != 'reject':
        raise Die('only a rejected verdict needs a fix brief')
    units = header_field(verdict, 'units')
    landing = units.startswith('landing')
    if landing:
        briefs = [unit_brief(store, u) for u in re.findall(r'[0-9]{3}', units)]
        if not briefs:
            briefs = [b for b in sorted(glob.glob(f'{glob.escape(store)}/briefs/[0-9][0-9][0-9]-*.md')) if os.path.isfile(b) and not is_landing(b)]
        if not briefs:
            raise Die(f'no unit briefs to fix in {store}/briefs')
        slug = 'landing-fix'
    else:
        numbers = re.findall(r'[0-9]{3}', units)
        if not numbers:
            raise Die(f'{verdict} names no unit (units: {units})')
        briefs = [unit_brief(store, numbers[-1])]
        name = os.path.basename(briefs[0])[:-3] if briefs[0].endswith('.md') else os.path.basename(briefs[0])
        slug = re.sub(r'-fix[0-9]*$', '', name[4:]) + '-fix'
    path = new_from_template(ctx, store, slug, 'briefs', 'brief-template.md')
    olds, new = [open(b).read() for b in briefs], open(path).read()
    last = olds[-1]
    new = re.sub(r'^playbook: .*$', 'playbook: bug-fix', new, count=1, flags=re.M)
    for key in ('timebox', 'commit'):
        new = re.sub(rf'^{key}: .*$', f'{key}: {header(last, key)}', new, count=1, flags=re.M)
    new = re.sub(r'^plan: .*$', f'plan: {"none" if landing else header(last, "plan")}', new, count=1, flags=re.M)
    if landing:
        fills = {
            'Goal': f'\nFix every finding in the landing verdict {verdict}, so a fresh `kitchen.sh verify {store} --landing` of the stack passes.\n\n',
            'Steps': '\n1. One commit per finding in the verdict: the change, and the check that shows the finding is gone.\n\n',
            'Context': f'\n- verdict: {verdict}\n' + ''.join(f'- unit brief: {b}\n' for b in briefs) + '\n',
            'Scope': scope(olds), 'Acceptance': lines(olds, 'Acceptance'), 'Forbidden': lines(olds, 'Forbidden'),
            'Verify': '\n' + ''.join(section(t, 'Verify').strip('\n') + '\n\n' for t in olds),
        }
    else:
        fills = {
            'Goal': f'\nFix every finding in the verifier\'s verdict {verdict}, so a fresh verification of the same acceptance passes.\n\n',
            'Steps': '\n1. One commit per finding in the verdict: the change, and the check that shows the finding is gone.\n\n',
            'Context': f'\n- verdict: {verdict}\n- original brief: {briefs[0]}\n\n',
        }
        for name in ('Scope', 'Acceptance', 'Verify', 'Forbidden'):
            fills[name] = section(last, name)
    for name, body in fills.items():
        new = re.sub(rf'(^## {name}\n)(.*?)(?=^## |\Z)', lambda m, b=body: m.group(1) + b, new, count=1, flags=re.M | re.S)
    with open(path, 'w') as f:
        f.write(new)
    out(f'{path}\n')
    return 0
