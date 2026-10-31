"""classify: stamps a brief's risk, profiles, verify mode, review engine and kitchen from its Scope.

Every kitchen.py query here (`snapshot`, `classify_change` and `lessons_stamp`) is a function of kpy.kitchen_py that returns data. None of them writes
os.environ or builds a child's command line (`child_args`, `child_env`): those belong to `gate` and to `brief-verify` without --list. test_classify_kpy_calls_leave_the_environment_alone pins that."""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from pstack_core.checkin import scope_entries
from pstack_core.errors import Die, EmptyScope, KitchenPyFailed, Unfilled
from pstack_core.panes import agent_status
from pstack_core.review import placeholder_line
from pstack_core.store import (
    field,
    header_field,
    pair_file,
    read,
    read_text,
    risk_of,
    write_text,
)

from . import kpy
from .landing import playbooks, section

ACCEPTANCE_WORDS = ('git', 'python3', 'test', 'grep', 'awk', 'uv', 'bash', 'rg')

CLASSIFY_FAILED = 'kitchen.py classify failed; run kitchen.py validate in the repo'


def set_header(path, key, value):
    """Replaces the key's header line (the first one), or adds it after the plan: line (the first one); a file with neither line is left as it was."""
    lines = re.findall(r'[^\n]*\n|[^\n]+', read_text(path))
    exists = any(line.startswith(f'{key}:') for line in lines)
    done, out = False, []
    for line in lines:
        if not done and exists and line.startswith(f'{key}:'):
            line, done = f'{key}: {value}' + ('\n' if line.endswith('\n') else ''), True
        elif not done and not exists and line.startswith('plan:'):
            line, done = f'{line}{key}: {value}\n', True
        out.append(line)
    write_text(path, ''.join(out))


def brief_escalations(brief):
    """Why a brief is escalated beyond its paths, one line each: the master wrote risk: escalated on it, or its plan is escalated. Classify only raises
    a brief's risk from these, never lowers it."""
    reasons = []
    if risk_of(brief) == 'escalated':
        reasons.append('the brief says risk: escalated')
    plan = header_field(brief, 'plan')
    if (
        plan not in ('', 'none')
        and os.path.isfile(plan)
        and risk_of(plan) == 'escalated'
    ):
        reasons.append(f'escalated plan: {os.path.basename(plan)}')
    return reasons


def steps_reader(escalate, current, master_said, risk):
    """Who reads an escalated unit's steps: `master` when its own paths escalate it (a reason that is neither the plan's nor the brief's
    risk line), the master wrote risk: escalated before the first classify, or `steps: master` is already there; otherwise `gates`.
    A unit that is not escalated has nothing to read."""
    if risk != 'escalated':
        return 'gates'
    own = [
        r
        for r in escalate
        if not r.startswith('escalated plan:') and r != 'the brief says risk: escalated'
    ]
    return 'master' if own or master_said or current == 'master' else 'gates'


def stamp_steps(brief, escalate, risk, current, risk_before):
    """Stamps the brief's steps: header from `steps_reader` and returns the classify line for an escalated unit (none for a routine one). `current`
    and `risk_before` are the brief's steps: and risk: as classify found them, before it rewrote risk:."""
    master_said = risk_before == 'escalated' and current not in ('master', 'gates')
    steps = steps_reader(escalate, current, master_said, risk)
    set_header(brief, 'steps', steps)
    if risk != 'escalated':
        return []
    if steps == 'master':
        return ['steps: master']
    return ['steps: gates (escalated only by its plan; its gates prove each step)']


def kitchen_snapshot(store, root=None):
    """A host kitchen has no history, so a brief is classified, stepped and verified against the copy taken when it was classified (kitchen: in its
    header), the way a repo kitchen is read at the unit's commit. The copy's path, or nothing for a repo kitchen."""
    try:
        return kpy.kitchen_py.snapshot(
            kpy.invocation(store, root), Path(f'{store}/kitchens')
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(
            'kitchen.py snapshot failed; run kitchen.py validate in the repo'
        ) from None


def brief_kitchen(brief):
    """--config <snapshot> for the brief, as a list; empty for a repo kitchen."""
    snap = header_field(brief, 'kitchen')
    return [] if snap in ('', 'repo') else ['--config', snap]


def normal_form(command):
    return ' '.join(command.replace('git diff --exit-code', 'git diff').split())


def runs_in(command, verify):
    """Whether `command` occurs in `verify` whole, with a command boundary on each side."""
    return bool(
        re.search(
            r'(?<![^\s(;|&"\'])' + re.escape(command) + r'(?![^\s);|&"\'])', verify
        )
    )


def acceptance_command(gates):
    """The pattern for a backquoted command in Acceptance: it starts with one of the fixed words, the first word of a touched gate, or `./`."""
    words = {*ACCEPTANCE_WORDS, *(g.split()[0] for g in gates if g.split())}
    alternatives = '|'.join(re.escape(w) for w in sorted(words))
    return re.compile(rf'`((?:(?:{alternatives}) |\./)[^`\n]+)`')


def acceptance_warnings(text, gates):
    """One warning per backquoted command in Acceptance that neither Verify nor a gate runs, so `finish` would not rerun it."""
    verify = normal_form(section(text, 'Verify'))
    covered = {normal_form(g) for g in gates}
    warned = {}
    for command in acceptance_command(gates).findall(section(text, 'Acceptance')):
        normal = normal_form(command)
        if normal in covered or runs_in(normal, verify):
            continue
        warned.setdefault(
            normal,
            f'Acceptance runs {command}, which Verify does not; add it to Verify (wrap a "prints nothing" check as test -z "$(...)")',
        )
    return list(warned.values())


PROBE_WORDS = frozenset({'rg', 'grep', 'wc', 'sort', 'head', 'tail', 'cut'})
PROBE_GIT = frozenset({'grep', 'ls-files', 'diff'})
PROBE_WRITERS = ('--output', '--pre', '-o')
PROBE_TEST = re.compile(r'(!\s+)?test\s+-[zn]\s+"\$\((.*)\)"', re.DOTALL)
PROBE_SECONDS = 10
PROBE_SHOWN = 120


def probe_words(command):
    """The command's words with `|` kept as its own token, or None when it holds anything else shell-shaped: a newline, a backtick, `;`, `&`, a redirection, a parenthesis."""
    if '\n' in command or '`' in command:
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def read_only_stage(stage):
    """Whether one pipeline stage starts with a read-only word and writes nothing (`--output`, `--pre`, `sort -o`)."""
    if not stage or stage[0].startswith(('--output', '--pre')):
        return False
    if stage[0] == 'git':
        return len(stage) > 1 and stage[1] in PROBE_GIT
    return stage[0] in PROBE_WORDS and not (stage[0] == 'sort' and '-o' in stage)


def read_only_pipeline(command):
    """Whether `command` is one pipeline of read-only stages: each starts with rg, grep, wc, sort, head, tail or cut, or with git grep, git ls-files or git diff."""
    words = probe_words(command)
    if not words or '$(' in command:
        return False
    if words[0] == '!':
        words = words[1:]
    stage = []
    for word in [*words, '|']:
        if word != '|':
            if set(word) <= set('();<>&') or word.startswith(('--output', '--pre')):
                return False
            stage.append(word)
        elif read_only_stage(stage):
            stage = []
        else:
            return False
    return True


def is_probe(command):
    """Whether a Verify command only reads: a read-only pipeline, `test -e|-f|-d|-s <path>`, or `test -z|-n "$(<such a pipeline>)"`, each with an optional leading `!`."""
    if inner := PROBE_TEST.fullmatch(command):
        return command.count('$(') == 1 and read_only_pipeline(inner.group(2))
    words = probe_words(command)
    if words and words[0] == '!':
        words = words[1:]
    if words and words[0] == 'test':
        return (
            len(words) == 3
            and words[1] in ('-e', '-f', '-d', '-s')
            and '$' not in words[2]
            and words[2] not in ('|', '(')
        )
    return read_only_pipeline(command)


def run_probe(command, root):
    """Runs one probe at the tree and returns its classify lines: `probe: passes now` or `probe: fails now`, then a `warn:` when it errored (stderr, or exit 2 or more; for `test -z "$(...)"` that stderr is the inner pipeline's)."""
    shown = command[:PROBE_SHOWN]
    try:
        done = subprocess.run(
            ['bash', '-c', command],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=PROBE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [f'warn: Verify probe {shown} did not end in {PROBE_SECONDS}s']
    lines = [f'probe: {"passes" if done.returncode == 0 else "fails"} now: {shown}']
    if done.stderr.strip() or done.returncode >= 2:
        first = done.stderr.strip().split('\n')[0]
        lines.append(
            f'warn: Verify probe {shown} errored (exit {done.returncode}): {first}'
        )
    return lines


def root_anchored_globs(command):
    """The rg `-g`/`--glob` values in `command` that negate a path with a `/` but do not start with `!**/` or `!/`."""
    words = probe_words(command)
    if not words or 'rg' not in words:
        return []
    values = []
    for i, word in enumerate(words):
        if word in ('-g', '--glob') and i + 1 < len(words):
            values.append(words[i + 1])
        elif word.startswith('--glob='):
            values.append(word[len('--glob=') :])
    return [
        v
        for v in values
        if v.startswith('!') and '/' in v and not v.startswith(('!**/', '!/'))
    ]


def verify_probes(brief, root):
    """Classify lines for the brief's Verify block: each read-only line run once at the current tree, and a warning for each root-anchored negated rg glob in any line. Nothing here stops classify."""
    try:
        commands = kpy.kitchen_py.brief_verify_commands(Path(brief))
    except kpy.KitchenError:
        return []
    lines = []
    for command in commands:
        if '\n' not in command and is_probe(command):
            lines += run_probe(command, root)
    globs = dict.fromkeys(g for c in commands for g in root_anchored_globs(c))
    lines += [
        f"warn: Verify glob '{g}' is matched from the search root; write '!**/{g[1:]}' to exclude it at any depth"
        for g in globs
    ]
    return lines


def header_values(brief, key):
    """Every `key: value` line in the brief's header (above its first `## ` section), in order."""
    out = []
    for line in read_text(brief).split('\n'):
        if line.startswith('## '):
            break
        if line.startswith(f'{key}:'):
            out.append(line[len(key) + 1 :].strip())
    return out


def overlay_directives(brief):
    """The brief's `with:` ids and its `without:` (id, reason) pairs; the reason follows an em dash or `--`."""
    with_ids = tuple(
        i.strip()
        for v in header_values(brief, 'with')
        for i in v.split(',')
        if i.strip()
    )
    without = []
    for v in header_values(brief, 'without'):
        parts = re.split(r'\s+(?:—|--)\s+', v, maxsplit=1)
        oid, reason = parts[0], parts[1] if len(parts) > 1 else ''
        without.append((oid.strip(), reason.strip()))
    return with_ids, tuple(without)


def compose_playbooks(store, brief, globs, config):
    """The brief's composition from kitchen.py, or None when the brief names no playbook. An unmapped `playbook:` or a bad overlay directive refuses the classify here; no other reader re-selects."""
    workflow = header_field(brief, 'playbook').strip()
    if workflow in ('', 'none'):
        return None
    text = read_text(brief)
    if (n := placeholder_line(text)) is not None:
        raise Unfilled(
            f'{brief} still has unfilled placeholders: line {n}: {text.split(chr(10))[n - 1].strip()}'
        )
    with_ids, without = overlay_directives(brief)
    try:
        return kpy.kitchen_py.compose(
            kpy.invocation(store, config=config),
            workflow,
            globs,
            with_ids=with_ids,
            without=without,
        ) | {'with': list(with_ids), 'without': [list(w) for w in without]}
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(
            'kitchen.py compose failed; fix the brief header (playbook:, with:, without:)'
        ) from None


COMPOSITION_VERSION = 1


def unit_number(brief):
    number = re.match(r'\d+', os.path.basename(brief))
    return number.group(0) if number else os.path.splitext(os.path.basename(brief))[0]


def composition_path(store, unit):
    return Path(store) / 'compositions' / f'{unit}.json'


def unstamp_composition(store, brief):
    """Removes a stamp an earlier classify left for a brief that no longer composes a playbook."""
    composition_path(store, unit_number(brief)).unlink(missing_ok=True)


def stamp_composition(store, brief, composition):
    """Writes `<store>/compositions/<NNN>.json` for the brief and returns whether the bytes changed. Later readers use this file and never select again."""
    path = composition_path(store, unit_number(brief))
    text = (
        json.dumps(
            {'version': COMPOSITION_VERSION, **composition}, indent=2, sort_keys=True
        )
        + '\n'
    )
    if path.is_file() and read_text(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    write_text(tmp, text)
    os.replace(tmp, path)
    return True


def composition_lines(composition):
    """The classify output for a composition: the workflow and overlays, each exclusion, then the master's parts."""
    ids = [o['id'] for o in composition['overlays']['selected']]
    lines = [f'playbooks: {" + ".join([composition["workflow"], *ids])}']
    lines += [
        f'excluded: {o["id"]} ({o["reason"]})'
        for o in composition['overlays']['excluded']
    ]
    for part in composition['roles']['master']:
        first = next((x.strip() for x in part['text'].split('\n') if x.strip()), '')
        first = first.lstrip('#').strip()
        lines.append(f'master: {part["id"]} {first[:100]}')
    return lines


def fence_opening(lines, at):
    """The index of the line that opens the closed code fence containing line `at`, or None when no fence does."""
    pb = playbooks()
    i = 0
    while i < len(lines):
        opened = pb.FENCE.match(lines[i].lstrip())
        close = opened and next(
            (
                j
                for j in range(i + 1, len(lines))
                if pb.fence_close(lines[j].lstrip(), opened.group(1))
            ),
            None,
        )
        if close is None:
            i += 1
        elif i <= at <= close:
            return i
        else:
            i = close + 1
    return None


def swallowed_headings(text):
    """A `warn:` line for each of Acceptance and Verify that reads empty because the heading the reader takes (the last one) sits inside a code fence, naming the heading's line and the line the fence opens on."""
    lines = text.split('\n')
    found = []
    for name in ('Acceptance', 'Verify'):
        at = next(
            (i for i in reversed(range(len(lines))) if lines[i] == f'## {name}'), None
        )
        if at is None or section(text, name).strip():
            continue
        opener = fence_opening(lines, at)
        if opener is not None:
            found.append(
                f'warn: {name} reads empty: its heading at line {at + 1} sits inside the code fence opened at line {opener + 1}; close or remove that fence'
            )
    return found


def classify_brief(store, brief):
    """Stamps risk, profiles, verify, review-engine and kitchen on the brief and prints the summary."""
    globs = scope_entries(brief, 'may')
    if not globs:
        raise EmptyScope(f"{brief} lists nothing under Scope's may write:")
    snap = kitchen_snapshot(store)
    if snap:
        set_header(brief, 'kitchen', snap)
    elif header_field(brief, 'kitchen'):
        set_header(brief, 'kitchen', 'repo')
    cfg = brief_kitchen(brief)
    before = header_field(brief, 'steps'), risk_of(brief)
    composition = compose_playbooks(store, brief, globs, cfg[1] if cfg else None)
    try:
        data = kpy.kitchen_py.classify_change(
            kpy.invocation(store, config=cfg[1] if cfg else None),
            paths=globs,
            escalate=tuple(brief_escalations(brief)),
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(CLASSIFY_FAILED) from None
    risk, verify = data.get('risk') or '', data['verify']
    profiles, escalate = data['profiles'], data['escalate']
    verify_line = f'{verify["mode"]} by {verify["kind"]} verifier'
    # The master may have written a reason after the class (`escalated (contract change)`): rewrite the line only when the class changes.
    if risk_of(brief) != risk:
        set_header(brief, 'risk', risk)
    set_header(brief, 'profiles', ', '.join(profiles) or 'none')
    set_header(brief, 'verify', verify_line)
    set_header(brief, 'review-engine', data.get('review_engine') or 'none')
    steps_lines = stamp_steps(brief, escalate, risk, *before)
    sys.stdout.write(f'risk: {risk}\n')
    for line in swallowed_headings(read_text(brief)):
        sys.stdout.write(f'{line}\n')
    try:
        picked = kpy.kitchen_py.lessons_stamp(
            kpy.invocation(store, config=cfg[1] if cfg else None), Path(brief)
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise KitchenPyFailed(
            'kitchen.py lessons stamp failed; run kitchen.py lessons in the repo'
        ) from None
    if picked:
        sys.stdout.write(f'lessons: {" ".join(x.id for x in picked)}\n')
    lines = [
        f'profiles: {", ".join(profiles)}',
        f'verify: {verify_line}',
        *steps_lines,
        *(f'escalate: {reason}' for reason in escalate),
    ]
    lines += [f'warn: {warning}' for warning in data.get('warnings') or []]
    lines += [
        f'warn: {warning}'
        for warning in acceptance_warnings(read_text(brief), data.get('gates') or [])
    ]
    lines += verify_probes(brief, read(store, 'git_root'))
    if data.get('bookkeeping'):
        lines.append(
            'bookkeeping: the Scope is outside the repo; gates only, nothing to escalate'
        )
    sys.stdout.write(''.join(f'{line}\n' for line in lines))
    if not composition:
        unstamp_composition(store, brief)
    if composition:
        stamp_composition(store, brief, composition)
        sys.stdout.write(
            ''.join(f'{line}\n' for line in composition_lines(composition))
        )
    first_reason = escalate[0] if escalate else ''
    if (
        risk == 'escalated'
        and agent_status(field(store, 'consultant', 'name')) == 'absent'
    ):
        sys.stdout.write(
            f'next: kitchen.sh consultant {store} --reason "{first_reason}", then a plan round with it\n'
        )


def classify(ctx, args):
    if len(args) != 2:
        raise Die('usage: kitchen.sh classify <store> <brief-path>')
    pair_file(args[0])
    if not os.path.isfile(args[1]):
        raise Die(f'brief not found: {args[1]}')
    classify_brief(args[0], args[1])
    return 0
