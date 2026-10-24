"""The verifier's packet: what a fresh verifier is told, written from the units' briefs, the range's diff and the kitchen's gates."""

import glob
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pstack_core import git
from pstack_core.store import expected_report, header_field, read, read_text, write_text

from . import kpy
from .landing import clean_verdict_for, is_landing, playbooks
from .playbook_delivery import provenance, read_composition
from .units import RecordedUnit, StackIndex, inclusion, unit_brief


def fence_for(text):
    """A code fence for text: three backticks, or one more than the longest run of them in the text."""
    longest = max((len(m) for m in re.findall(r'`+', text)), default=0)
    return '`' * ((longest if longest >= 3 else 2) + 1)


def names_path(text, path):
    """Whether command text names path as a whole path: after a leading ~/, $HOME/ or ${HOME}/ is spelled out, path appears not inside a longer path and
    is followed by /, space, a quote, ), ;, &, | or the end."""
    home = os.environ.get('HOME', '')
    text = re.sub(
        r"""(^|[\s=:"'(])(~|\$HOME|\$\{HOME\}|"\$HOME"|"\$\{HOME\}")/""",
        lambda m: m.group(1) + home + '/',
        text,
        flags=re.M,
    )
    return (
        re.search(
            r"""(^|[^A-Za-z0-9_./-])""" + re.escape(path) + r"""(/|[\s"'`);&|]|$)""",
            text,
            flags=re.M,
        )
        is not None
    )


def brief_commands(store, root, brief, unit):
    """The brief's Verify commands for a verifier's packet, each in a block of its own, run from the scratch root. A command that ends in
    `# master-only` (on its first or last line) writes the store, and one that names the main checkout or the store cannot run in the scratch; both are
    listed apart, in full, each with its reason. A Verify block the kitchen refuses is said so in the packet and warned of, not skipped."""
    try:
        commands = kpy.kitchen_py.brief_verify_list(
            kpy.invocation(store, root), Path(brief)
        )
    except kpy.KitchenError as e:
        first = f'kitchen: {e}'.split('\n')[0]
        sys.stdout.flush()
        sys.stderr.write(f'warning: unit {unit or "?"}: brief-verify: {first}\n')
        return f'Brief commands: none (brief-verify: {first})\n\n'
    runnable, apart = partition(commands, store, root)
    text = ''
    for n, command in enumerate(runnable, 1):
        fence = fence_for(command)
        if n == 1:
            text += 'Brief commands:\n\nRun each command in a fresh shell from the scratch root.\n\n'
        text += f'Command {n}:\n\n{fence}bash\n{command}\n{fence}\n\n'
    return text + apart_text(apart)


def partition(commands, store, root):
    """The commands a verifier can run in the scratch, and the others as (reason, command): one ending in `# master-only` (on its first or last line)
    writes the store, and one naming the main checkout or the store cannot run in the scratch."""
    runnable, apart = [], []
    for command in commands:
        parts = command.split('\n')
        if re.search(r'# master-only[ \t\n\r\f\v]*$', parts[0]) or re.search(
            r'# master-only[ \t\n\r\f\v]*$', parts[-1]
        ):
            apart.append(('master-only', command))
        elif names_path(command, root) or names_path(command, store):
            apart.append(('absolute path', command))
        else:
            runnable.append(command)
    return runnable, apart


def apart_text(apart):
    """The commands partition set apart, each in full with its reason; '' when none."""
    if not apart:
        return ''
    return 'not for the verifier:\n\n' + ''.join(
        f'({why})\n{fence_for(c)}bash\n{c}\n{fence_for(c)}\n\n' for why, c in apart
    )


LIST_MARKER = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+')
PLACEHOLDER = re.compile(r'(?<!<)<[A-Za-z_][\w .|-]*>')
SHELL_FENCES = ('bash', 'sh', 'shell')


def plan_verification(plan):
    """A plan's `## Verification` split into (commands, obligations, problem). A command is a fenced block whose info string is bash, sh or shell, holds
    a non-comment line, and has no `<placeholder>`; the person wrote it as shell. Every other non-blank line, a fence of any other kind in full, and
    every inline code span is an obligation with its line number in the plan; no command is built from prose. problem is `no such file` or
    `no ## Verification section` when the plan gives nothing to read, else ''."""
    try:
        lines = read_text(plan).split('\n')
    except OSError:
        return [], [], 'no such file'
    commands, obligations, fenced, block, inside = [], [], False, [], False
    for number, line in enumerate(lines, 1):
        if not inside:
            inside = line.strip() == '## Verification'
            continue
        if line.startswith('## ') and not fenced:
            break
        if line.startswith('```') and not fenced:
            fenced, block = True, [(number, line)]
        elif fenced:
            block.append((number, line))
            if line.startswith('```'):
                fenced = False
                words = block[0][1][3:].split()
                body = [x for _, x in block[1:-1]]
                if (
                    words[:1]
                    and words[0].lower() in SHELL_FENCES
                    and any(x.strip() and not x.lstrip().startswith('#') for x in body)
                    and not PLACEHOLDER.search('\n'.join(body))
                ):
                    commands.append('\n'.join(body).strip())
                else:
                    obligations += [(n, x.strip()) for n, x in block if x.strip()]
        elif line.strip():
            obligations.append((number, LIST_MARKER.sub('', line.strip())))
    return commands, obligations, '' if inside else 'no ## Verification section'


def landing_plans(p):
    """For each distinct plan the landing's covered briefs name: (plan, runnable commands, apart, obligations, problem), in order of first mention."""
    found, seen = [], set()
    for brief in landing_covered_briefs(p.store, p.root, p.base, p.head):
        plan = header_field(brief, 'plan')
        if plan in ('', 'none') or plan in seen:
            continue
        seen.add(plan)
        commands, obligations, problem = plan_verification(plan)
        runnable, apart = partition(commands, p.store, p.root)
        found.append((plan, runnable, apart, obligations, problem))
    return found


def plan_sections(p):
    """The landing packet's `## Plan verification` section for each plan: the commands set apart, and the lines that are the master's to report on."""
    text = ''
    for plan, runnable, apart, obligations, problem in landing_plans(p):
        if problem:
            sys.stdout.flush()
            sys.stderr.write(f'warning: plan {plan}: {problem}\n')
            text += (
                f'## Plan verification: {plan}\n\nwarning: plan {plan}: {problem}\n\n'
            )
            continue
        text += f'## Plan verification: {plan}\n\n{len(runnable)} command{"" if len(runnable) == 1 else "s"} from it run in the Prove block below.\n\n'
        text += apart_text(apart)
        if obligations:
            text += 'Master obligations, not yours: report what evidence you see, or write "pending"\n\n'
            text += ''.join(f'- line {n}: {t}\n' for n, t in obligations) + '\n'
    return text


def brief_without_verify(brief):
    """The brief's text outside its `## Verify` section: the commands there are partitioned, not read."""
    lines = read_text(brief).split('\n')
    headings = set(playbooks().section_headings(lines))
    kept, skip = [], False
    for i, line in enumerate(lines):
        if i in headings:
            skip = line.strip() == '## Verify'
        if not skip:
            kept.append(line)
    return '\n'.join(kept)


def inputs_section(store, briefs):
    """`## Inputs (read-only)`: each existing path under the store that the briefs name outside their Verify section, once, in order of first mention.
    A path followed by a `<placeholder>` is cut short and left out, a trailing `:<line>` is dropped, and a glob counts when it matches a file.
    '' when they name none."""
    found = {}
    for brief in briefs:
        try:
            text = brief_without_verify(brief)
        except OSError:
            continue
        for m in re.finditer(re.escape(store) + r'/[^\s`\'"()<>\[\],;]+', text):
            if text[m.end() : m.end() + 1] in ('<', '>'):
                continue
            path = re.sub(r':\d+(:\d+)?$', '', m.group(0).rstrip('.:'))
            if glob.glob(path):
                found.setdefault(path, None)
    if not found:
        return ''
    return (
        '## Inputs (read-only)\n\n'
        + ''.join(f'- {path}: read it; do not write it\n' for path in found)
        + '\n'
    )


def landing_covered_briefs(store, root, base, head):
    """The done unit briefs a landing range holds: those whose head is in base..head, or whose commits were rebased into it."""
    covered, index = [], StackIndex.build(root, base, head)
    for brief in sorted(glob.glob(f'{glob.escape(store)}/briefs/[0-9][0-9][0-9]-*.md')):
        if (
            not os.path.exists(brief)
            or header_field(expected_report(store, brief), 'status') != 'done'
            or is_landing(brief)
        ):
            continue
        if inclusion(RecordedUnit(store, brief), index).covered:
            covered.append(brief)
    return covered


def tail_lines(text, count):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines[-count:])


def section_lines(path, pattern, keep_heading):
    """The lines under the brief's `## Goal` and `## Acceptance` headings, headings included with keep_heading, else the Goal section without its heading."""
    try:
        lines = read_text(path).split('\n')
    except OSError:
        return []
    if lines and lines[-1] == '':
        lines.pop()
    return playbooks().section_lines(lines, pattern, keep_heading)


PLAYBOOK_HEADING = '## Playbook (what to prove)'
EVIDENCE_STANDARD = (
    'The evidence standard for the Acceptance above; it adds no criteria. '
    'Answer Acceptance line by line, citing evidence of the kind these parts name.'
)


def composition_name(composition):
    ids = [o['id'] for o in composition['overlays']['selected']]
    return ' + '.join([composition['workflow'], *ids])


def verifier_parts(composition):
    return composition['roles'].get('verifier') or []


def parts_text(parts, shown, owner):
    """Each part whole under its id with its provenance line; a part already shown for another unit in this packet is named, not repeated."""
    text = ''
    for part in parts:
        if part['id'] in shown:
            text += f'### {part["id"]} (also {shown[part["id"]]})\n\n'
            continue
        shown[part['id']] = owner
        body = playbooks().demote_headings(part['text']).strip()
        text += f'### {part["id"]}\n\n{body}\n\n{provenance(part)}\n\n'
    return text


def unit_playbook_section(store, brief, unit, shown):
    """The unit's `## Playbook (what to prove)` section: its verifier parts whole, or for a unit with no stored composition its mapped label ('' when it has none)."""
    composition = read_composition(store, unit)
    if composition is None:
        label = header_field(brief, 'playbook').strip()
        if not label or label.startswith('{{'):
            return ''
        return f'{PLAYBOOK_HEADING}\n\nplaybook: {label} (not mapped)\n\n'
    parts = verifier_parts(composition)
    if not parts:
        return ''
    return (
        f'{PLAYBOOK_HEADING}\n\n{EVIDENCE_STANDARD}\n\n'
        f'unit {unit}: {composition_name(composition)}\n\n'
        + parts_text(parts, shown, f'unit {unit}')
    )


@dataclass(frozen=True)
class Packet:
    """What the verifier's packet says: where the repo and the kitchen's scripts are, the range, the units, and the verifier it is for. `units` are all
    the units proved (the fix's covered ones first), `own` those named as the fix; `config` is the kitchen config the diff was classified under."""

    template: str
    tool_dir: str
    root: str
    store: str
    verdict: str
    scratch: str
    base: str
    head: str
    identity: str
    vclass: str
    timeout_m: int
    risk: str
    units: tuple
    own: tuple
    covered: tuple
    landing: bool
    last: str
    slug: str
    data: dict
    config: str


PACKET_RULE = 'Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.'


def landing_sections(p, parts):
    """Adds the packet's opening for a landing to parts: the stack's header with the units verified on their own and those folded in, each covered unit's
    goal, the Acceptance of each folded unit, and the Verification of the plans the briefs name."""
    parts += [
        f'# Verify landing: the stack at {p.head[:9]}\n\nverdict: {p.verdict}\ntemplate: {p.template}\nscratch: {p.scratch}\n'
        f'range: {p.base}..{p.head}\nrisk: landing\nunits: landing\nverifier-agent: {p.identity}\nverifier-class: {p.vclass}\nverifier-outcome: prepared\n'
        f'deadline: {p.timeout_m}m from now\n\n',
        f'{PACKET_RULE}\n\n',
        '## Stack\n\n',
    ]
    stacked = landing_covered_briefs(p.store, p.root, p.base, p.head)
    names = {b: os.path.basename(b)[:-3] for b in stacked}
    folded = [b for b in stacked if not clean_verdict_for(p.store, names[b][:3])]
    verified = ', '.join(names[b] for b in stacked if b not in folded) or 'none'
    parts.append(
        f'Verified on their own: {verified}.\nFolded into this landing, with no verdict of their own: '
        + (
            f'{", ".join(names[b] for b in folded)}; their Acceptance is below, and you prove it here.'
            if folded
            else 'none.'
        )
        + '\n\nProve that together, at this tip, they still hold: run every Prove command, then drive the main flows the\n'
        'briefs describe, and try the seams between units. Read a brief only for\nthe flow you are driving.\n\n'
    )
    parts += [f'- {b}\n' for b in stacked]
    parts.append('\n## Goals\n\n')
    for b in stacked:
        parts.append(f'### Unit {names[b][:3]} {names[b][4:]}\n\n')
        goal = section_lines(b, r'^## Goal', False)
        while goal and goal[0] == '':
            goal.pop(0)
        parts.append('\n'.join(goal).rstrip('\n') + '\n\n')
    for b in folded:
        parts.append(
            f'## Acceptance: unit {names[b]} (plan {header_field(b, "plan") or "none"})\n\n'
            + '\n'.join(section_lines(b, r'^## Acceptance', False)).strip('\n')
            + '\n\n'
        )
    parts.append(plan_sections(p))
    parts.append(inputs_section(p.store, stacked))


CONTEXT_RULE = "These are decisions a review accepted; do not judge them again. Judge only the named units' Acceptance."


def units_between(p):
    """The unit briefs not named whose recorded head lies in the packet's base..head, oldest name first."""
    index, found = StackIndex.build(p.root, p.base, p.head), []
    for brief in sorted(
        glob.glob(f'{glob.escape(p.store)}/briefs/[0-9][0-9][0-9]-*.md')
    ):
        name = os.path.basename(brief)[:-3]
        if name[:3] in p.units or is_landing(brief):
            continue
        if inclusion(RecordedUnit(p.store, brief), index).covered:
            found.append(name)
    return found


def context_section(p):
    """The `## Context: units between` section of a covers packet: each intervening unit with its accepted review's Amendments, as context the
    verifier does not judge. '' when no unit lies between."""
    between = units_between(p)
    if not between:
        return ''
    text = f'## Context: units between\n\n{CONTEXT_RULE}\n\n'
    for name in between:
        review = f'{p.store}/reviews/{name}.md'
        if header_field(review, 'verdict') != 'accept':
            text += f'unit {name}: no accepted review\n\n'
            continue
        amendments = (
            '\n'.join(section_lines(review, r'^## Amendments', False)).strip('\n')
            or 'none'
        )
        text += f'unit {name}: accepted (reviews/{name}.md)\n\nAmendments:\n\n{amendments}\n\n'
    return text


def unit_sections(p, parts):
    """Adds the packet's opening for named units to parts: the header, then each unit's Goal and Acceptance, its commands, and the lessons that apply to the briefs."""
    parts += [
        f'# Verify {p.last}: {p.slug}\n\nverdict: {p.verdict}\ntemplate: {p.template}\nscratch: {p.scratch}\nrange: {p.base}..{p.head}\n'
        f'risk: {p.risk}\nunits: {" ".join(p.units)}\nverifier-agent: {p.identity}\nverifier-class: {p.vclass}\nverifier-outcome: prepared\n'
        f'deadline: {p.timeout_m}m from now\n\n',
        f'{PACKET_RULE}\n\n',
    ]
    if p.covered:
        parts.append(
            f"Unit {' '.join(p.own)} fixes unit {' '.join(p.covered)}: prove every unit's Acceptance at this one head.\n\n"
        )
    shown = {}
    for u in p.units:
        b = unit_brief(p.store, u)
        parts.append(f'## Unit {u}\n\nbrief: {b}\n\n')
        parts += [
            line + '\n' for line in section_lines(b, r'^## (Goal|Acceptance)', True)
        ]
        parts.append('\n')
        parts.append(unit_playbook_section(p.store, b, u, shown))
        parts.append(brief_commands(p.store, p.root, b, u))
    if p.covered:
        parts.append(context_section(p))
    parts.append(inputs_section(p.store, [unit_brief(p.store, u) for u in p.units]))
    briefs = []
    for u in p.units:
        found = sorted(
            glob.glob(f'{glob.escape(p.store)}/briefs/{glob.escape(u)}-*.md')
        )
        briefs += found if found else [f'{p.store}/briefs/{u}-*.md']
    lessons = kpy.kitchen_py.lesson_packet([Path(b) for b in briefs]).rstrip('\n')
    if lessons:
        parts.append(f'{lessons}\n\n')


BACKGROUND_SECONDS = 8 * 60


def duration(seconds):
    """Whole seconds as `6m 12s`, or `45s` under a minute."""
    seconds = round(seconds)
    return f'{seconds // 60}m {seconds % 60}s' if seconds >= 60 else f'{seconds}s'


def gate_lines(p, profile, stage, merge_base, tool):
    """A gate's Prove line with its median time as a trailing comment (`unmeasured` below three passing runs), preceded by the line telling the verifier
    to run it in the background when the median is eight minutes or more."""
    median = kpy.call(
        kpy.kitchen_py.median_timing, kpy.invocation(p.store, p.root), [profile], stage
    )['profiles'].get(profile)
    note = (
        f'median {duration(median["seconds"])} over {median["runs"]} runs'
        if median
        else 'unmeasured'
    )
    line = f'python3 {tool} --repo {p.scratch} gate {profile} {stage} --role verifier --base {merge_base} # {note}\n'
    if median and median['seconds'] >= BACKGROUND_SECONDS:
        line = (
            '# over 8 minutes: run it in the background, wait for it to finish, then check its exit status\n'
            + line
        )
    return line


def prove_section(p, parts):
    """Adds the packet's Prove block to parts: the gates the verifier runs in the scratch worktree, then the feature map and the verification skills."""
    parts += [
        "## Prove\n\nYou share this machine with the sidekick. Run every repo command with\n`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you\n"
        'your own ports, emulators, and data, and stop everything you start before\nyou end. Run these in the scratch worktree, then drive what they cannot reach:\n\n'
        '```bash\nexport PSTACK_KITCHEN_ROLE=verifier'
    ]
    parts.append(f' PSTACK_KITCHEN_RUN={read(p.store, "slug")}\n')
    tool = f'{p.tool_dir}/kitchen.py' + (f' --config {p.config}' if p.config else '')
    if p.data.get('scratch_setup') or []:
        parts.append(f'python3 {tool} --repo {p.scratch} setup\n')
    forked = git.run(p.root, 'merge-base', p.base, p.head, quiet=True)
    merge_base = forked.stdout.rstrip('\n') if forked.returncode == 0 else p.base
    for profile in p.data.get('behavioral') or []:
        parts.append(gate_lines(p, profile, 'behavioral', merge_base, tool))
    # A landing verification also runs the touched profiles' landing commands.
    if p.risk == 'landing':
        for profile in p.data.get('landing') or []:
            parts.append(gate_lines(p, profile, 'landing', merge_base, tool))
        for plan, runnable, *_ in landing_plans(p):
            for n, command in enumerate(runnable, 1):
                parts.append(
                    f'# plan {plan}: Verification command {n}\n(set -e\ncd {p.scratch}\n{command}\n)\n'
                )
    features = p.data.get('features') or []
    parts.append(
        '```\n\nfeature map: '
        + ('none listed' if len(features) == 0 else ', '.join(features))
        + '\n'
    )
    skills = sorted(
        [
            d + '/'
            for d in glob.glob(f'{glob.escape(p.root)}/.agents/skills/verify-*')
            if os.path.isdir(d)
        ]
    )
    parts.append('verification skill: ' + ''.join(s + ' ' for s in skills) + '\n')


def write_packet(path, packet):
    """The verifier's packet, written even when a step fails midway, so what was gathered stays."""
    p, parts = packet, []
    try:
        (landing_sections if p.landing else unit_sections)(p, parts)
        if os.path.isfile(f'{p.store}/standing-orders.md'):
            orders = read_text(f'{p.store}/standing-orders.md').rstrip('\n')
            if orders.startswith('# '):
                orders = orders.partition('\n')[2].lstrip('\n')
            parts.append(
                f"## Standing orders\n\nFrom {p.store}/standing-orders.md. An order about the verifier, or about running the repo's commands and tests, "
                f"binds you; one about the sidekick's scope, steps, commits or reports does not.\n\n{orders}\n\n"
            )
        diff = git.run(p.root, 'diff', '--stat', f'{p.base}...{p.head}').stdout
        parts.append(f'## Changed\n\n```\n{tail_lines(diff, 40)}\n```\n\n')
        prove_section(p, parts)
    finally:
        write_text(path, ''.join(parts))
