#!/usr/bin/env python3
"""Parse a pstack playbook's Markdown into addressable parts.

A document is a title (frontmatter and the first heading line), then blocks.
Blocks before the title are `preamble:N`. After it, the blocks before the first
step, lead-in or section are the intro; a numbered list at the top level gives
the steps; a paragraph that opens with a bold one-to-four word lead-in such as
`**Worktree.**` starts a lead, which keeps only the lists directly after it;
blocks after the steps are the tail; any other block after a lead is
`after:<Lead>/N`; `**Reply:**` starts the reply, wherever it sits; a heading
starts a section that runs to the next heading of the same or a higher level.

Selectors address the parts: `preamble:N`, `intro:N`, `lead:<Word>`, `step:N`,
`step:<Section>/N`, `section:<Heading>`, `tail:N`, `after:<Lead>/N`, `reply`
and `heading:<path>` (a hierarchical heading path). Numbers inside a step's own
nested lists and fences are part of that step, not steps of their own.

Stdlib only, and independent of kitchen_core.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

HEADING = re.compile(r'(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$')
LIST_MARK = re.compile(r'(?:[-*+]|\d{1,9}[.)])[ \t]+\S')
FENCE = re.compile(r'(`{3,}|~{3,})')
LEAD = re.compile(r'\*\*([^*]+?)\.\*\*')
REPLY = re.compile(r'\*\*Reply:\*\*')
LEAD_WORDS = 4
KINDS = ('preamble', 'intro', 'lead', 'step', 'section', 'tail', 'after', 'reply')
LISTS = ('numbered', 'bullets')


class PlaybookError(Exception):
    """A source or selector that cannot be used."""


@dataclass(frozen=True)
class Block:
    kind: str  # heading | paragraph | fence | numbered | bullets
    start: int
    end: int  # exclusive; trailing blank lines are not part of a block
    level: int = 0
    text: str = ''
    items: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Atom:
    """The smallest part a map must select or exclude."""

    selector: str
    start: int
    end: int
    kind: str = ''  # '' for a selectable part, else why it cannot be addressed


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    path: tuple[str, ...]
    start: int
    end: int


@dataclass
class Doc:
    lines: list[str]
    atoms: list[Atom]
    headings: list[Heading]
    title_end: int  # lines before this index are the implicit title
    named: dict[str, list[tuple[int, int]]]  # selector -> ranges, for select()


def fence_close(stripped: str, opener: str) -> bool:
    m = FENCE.match(stripped)
    return bool(m) and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener)


def starts_block(line: str) -> bool:
    return bool(
        HEADING.match(line) or LIST_MARK.match(line) or FENCE.match(line.lstrip())
    )


def read_fence(lines: list[str], i: int) -> int:
    m = FENCE.match(lines[i].lstrip())
    opener = m.group(1) if m else '```'
    j = i + 1
    while j < len(lines) and not fence_close(lines[j].lstrip(), opener):
        j += 1
    return min(j + 1, len(lines))


def read_item(lines: list[str], i: int) -> int:
    """The end of the list item starting at `i`: indented lines, blank lines
    before more indented lines, and whole fences."""
    n, j, opener = len(lines), i + 1, None
    while j < n:
        line = lines[j]
        if opener:
            if fence_close(line.lstrip(), opener):
                opener = None
            j += 1
        elif not line.strip():
            k = j
            while k < n and not lines[k].strip():
                k += 1
            if k < n and lines[k][0] in ' \t':
                j = k
            else:
                break
        elif line[0] in ' \t':
            m = FENCE.match(line.lstrip())
            opener = m.group(1) if m else None
            j += 1
        else:
            break
    while j > i + 1 and not lines[j - 1].strip():
        j -= 1
    return j


def read_list(lines: list[str], i: int) -> Block:
    numbered = lines[i][0].isdigit()
    items, j = [], i
    while True:
        end = read_item(lines, j)
        items.append((j, end))
        k = end
        while k < len(lines) and not lines[k].strip():
            k += 1
        if (
            k < len(lines)
            and LIST_MARK.match(lines[k])
            and lines[k][0].isdigit() == numbered
        ):
            j = k
        else:
            return Block(
                'numbered' if numbered else 'bullets', i, end, items=tuple(items)
            )


def scan(lines: list[str]) -> list[Block]:
    blocks, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
        elif m := HEADING.match(line):
            blocks.append(Block('heading', i, i + 1, len(m.group(1)), m.group(2)))
            i += 1
        elif FENCE.match(line.lstrip()):
            end = read_fence(lines, i)
            blocks.append(Block('fence', i, end))
            i = end
        elif LIST_MARK.match(line):
            blocks.append(read_list(lines, i))
            i = blocks[-1].end
        else:
            j = i + 1
            while j < n and lines[j].strip() and not starts_block(lines[j]):
                j += 1
            blocks.append(Block('paragraph', i, j))
            i = j
    return blocks


def frontmatter_end(lines: list[str]) -> int:
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                return i + 1
    return 0


def lead_name(line: str) -> str | None:
    m = LEAD.match(line)
    if m and '.' not in m.group(1) and len(m.group(1).split()) <= LEAD_WORDS:
        return m.group(1)
    return None


def trimmed(lines: list[str], start: int, end: int) -> int:
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def heading_tree(lines: list[str], blocks: list[Block]) -> list[Heading]:
    found = [b for b in blocks if b.kind == 'heading']
    out, stack = [], []
    for k, b in enumerate(found):
        while stack and stack[-1][0] >= b.level:
            stack.pop()
        stack.append((b.level, b.text))
        end = len(lines)
        for later in found[k + 1 :]:
            if later.level <= b.level:
                end = later.start
                break
        path = tuple(t for _, t in stack)
        out.append(
            Heading(b.level, b.text, path, b.start, trimmed(lines, b.start, end))
        )
    return out


def is_reply(lines: list[str], b: Block) -> bool:
    return b.kind == 'paragraph' and bool(REPLY.match(lines[b.start]))


def section_atoms(
    lines: list[str], blocks: list[Block], at: int, end: int
) -> tuple[list[Atom], list[tuple[str, int, int]]]:
    """The atoms of the section whose heading block is `blocks[at]`: one per
    step when it holds nothing but a numbered list (then the whole section is
    also addressable, as an alias), else the section whole. A reply line in
    the section is its own atom, and must close the section."""
    head = blocks[at]
    inner = [b for b in blocks[at + 1 :] if b.start < end]
    end = trimmed(lines, head.start, end)
    out: list[Atom] = []
    cut = next((k for k, b in enumerate(inner) if is_reply(lines, b)), None)
    if cut is not None:
        reply, after = inner[cut], inner[cut + 1 :]
        reply_end = reply.end
        while after and after[0].kind in LISTS:
            reply_end, after = after[0].end, after[1:]
        out.append(Atom('reply', reply.start, reply_end))
        if after:
            out.append(
                Atom('block after the reply', after[0].start, end, 'after reply')
            )
        inner, end = inner[:cut], trimmed(lines, head.start, reply.start)
    if len(inner) == 1 and inner[0].kind == 'numbered':
        steps = [
            Atom(f'step:{head.text}/{k}', s, e)
            for k, (s, e) in enumerate(inner[0].items, 1)
        ]
        return steps + out, [(f'section:{head.text}', head.start, end)]
    return [Atom(f'section:{head.text}', head.start, end)] + out, []


def build_atoms(
    lines: list[str], blocks: list[Block]
) -> tuple[list[Atom], list[tuple[str, int, int]], int]:
    """The atoms of a document. Blocks before the title heading are
    `preamble:N`; after it, `intro`, `step`, `tail`, leads and the reply. A
    lead keeps only the lists that directly follow it; any other block after a
    lead or the reply is `after:<Word>/N` (`Reply` for the reply)."""
    atoms: list[Atom] = []
    aliases: list[tuple[str, int, int]] = []
    first = next((k for k, b in enumerate(blocks) if b.kind == 'heading'), None)
    if first is None:
        title_end, k = frontmatter_end(lines), 0
    else:
        title_end, k = blocks[first].end, first + 1
        pre = [b for b in blocks[:first] if b.start >= frontmatter_end(lines)]
        atoms.extend(
            Atom(f'preamble:{n}', b.start, b.end) for n, b in enumerate(pre, 1)
        )
    steps_seen, intro, tail, after = False, 0, 0, 0
    anchor: str | None = None  # the lead or reply the next blocks follow
    absorb = False  # a list right after the anchor belongs to it
    while k < len(blocks):
        b = blocks[k]
        if b.kind == 'heading':
            end = len(lines)
            for later in blocks[k + 1 :]:
                if later.kind == 'heading' and later.level <= b.level:
                    end = later.start
                    break
            found, alias = section_atoms(lines, blocks, k, end)
            atoms.extend(found)
            aliases.extend(alias)
            while k + 1 < len(blocks) and blocks[k + 1].start < end:
                k += 1
            anchor, absorb = None, False
        elif absorb and b.kind in LISTS:
            old = atoms[-1]
            atoms[-1] = Atom(old.selector, old.start, b.end)
        elif is_reply(lines, b):
            atoms.append(Atom('reply', b.start, b.end))
            anchor, absorb, after = 'Reply', True, 0
        elif b.kind == 'paragraph' and (name := lead_name(lines[b.start])):
            atoms.append(Atom(f'lead:{name}', b.start, b.end))
            anchor, absorb, after = name, True, 0
        elif b.kind == 'numbered' and anchor is None and not steps_seen:
            steps_seen = True
            atoms.extend(Atom(f'step:{n}', s, e) for n, (s, e) in enumerate(b.items, 1))
        elif b.kind == 'numbered' and anchor is None:
            atoms.append(Atom('numbered list', b.start, b.end, 'second list'))
        elif anchor is not None:
            after += 1
            absorb = False
            atoms.append(Atom(f'after:{anchor}/{after}', b.start, b.end))
        elif steps_seen:
            tail += 1
            atoms.append(Atom(f'tail:{tail}', b.start, b.end))
        else:
            intro += 1
            atoms.append(Atom(f'intro:{intro}', b.start, b.end))
        k += 1
    return atoms, aliases, title_end


def parse(text: str) -> Doc:
    lines = text.split('\n')
    blocks = scan(lines)
    atoms, aliases, title_end = build_atoms(lines, blocks)
    named: dict[str, list[tuple[int, int]]] = {}
    for a in atoms:
        if not a.kind:
            named.setdefault(a.selector, []).append((a.start, a.end))
    for selector, start, end in aliases:
        named.setdefault(selector, []).append((start, end))
    return Doc(lines, atoms, heading_tree(lines, blocks), title_end, named)


def canonical(selector: str) -> str:
    """`step:03` is `step:3`; anything else is taken as written."""
    kind, _, arg = selector.partition(':')
    head, slash, number = arg.rpartition('/')
    if kind in ('preamble', 'intro', 'tail', 'step') and not slash and arg.isdigit():
        return f'{kind}:{int(arg)}'
    if kind in ('step', 'after') and slash and number.isdigit():
        return f'{kind}:{head}/{int(number)}'
    return selector


def select(doc: Doc, selector: str) -> list[tuple[int, int]]:
    """The line ranges `selector` addresses; PlaybookError if none or several."""
    kind, _, arg = selector.partition(':')
    if kind == 'heading' and arg:
        path = tuple(arg.split('/'))
        found = [h for h in doc.headings if h.path[-len(path) :] == path]
        return [one(selector, [(h.start, h.end) for h in found])]
    if kind not in KINDS or bool(arg) == (kind == 'reply'):
        raise PlaybookError(f'unsupported selector {selector!r}')
    return [one(selector, doc.named.get(canonical(selector), []))]


def one(selector: str, found: list[tuple[int, int]]) -> tuple[int, int]:
    if not found:
        raise PlaybookError(f'{selector}: no such part')
    if len(found) > 1:
        raise PlaybookError(f'{selector}: ambiguous, {len(found)} parts match')
    return found[0]


def text_of(doc: Doc, ranges: list[tuple[int, int]]) -> str:
    return '\n'.join('\n'.join(doc.lines[s:e]) for s, e in ranges)


def normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.split('\n')]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return '\n'.join(out).strip()


def digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()[:12]


def default_roots() -> dict[str, Path]:
    """`skills` is the directory holding this skill, in the checkout or in an
    installed copy; the link to this skill's own directory is not followed."""
    return {'skills': Path(os.path.abspath(__file__)).parents[2]}


def resolve_source(roots: dict[str, Path], root: str, path: str) -> Path:
    """A source file under a named root. Symlinks are resolved and must stay in
    the root; the `skills` root also admits the target of a skill directory's
    own link (a directory), which is how an installed copy is laid out."""
    if root not in roots:
        raise PlaybookError(f'unknown source root {root!r}')
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or '\\' in path or '..' in pure.parts:
        raise PlaybookError(f'source path {path!r} must be relative inside {root}')
    base = roots[root]
    inside = [base.resolve()]
    if root == 'skills' and len(pure.parts) > 1 and (base / pure.parts[0]).is_dir():
        inside.append((base / pure.parts[0]).resolve())
    full = (base / path).resolve()
    if not any(full.is_relative_to(i) for i in inside):
        raise PlaybookError(f'source path {path!r} leaves the {root} root')
    if not full.is_file():
        raise PlaybookError(f'source {root}:{path} is not a file')
    return full


OWNERS = ('master', 'sidekick', 'consultant', 'verifier', 'kitchen')
AGENT_ROLES = ('master', 'sidekick', 'consultant', 'verifier')


def load_doc(roots: dict[str, Path], source: object) -> Doc:
    if not isinstance(source, dict):
        raise PlaybookError('source must be { root = ..., path = ... }')
    path = resolve_source(roots, str(source.get('root')), str(source.get('path')))
    return parse(path.read_text())


def part_problems(name: str, doc: Doc, part: dict) -> list[str]:
    pid = part.get('id') or '(no id)'
    where = f'{name}: part {pid}'
    out = []
    owners = part.get('owners')
    if not isinstance(owners, list) or not owners:
        out.append(f'{where}: owners must be a non-empty list')
        owners = []
    out += [f'{where}: unknown owner {o!r}' for o in owners if o not in OWNERS]
    kitchen = part.get('kitchen', {})
    if not isinstance(kitchen, dict):
        out.append(f'{where}: kitchen must be a table of role texts')
        kitchen = {}
    for role, text in kitchen.items():
        if role not in AGENT_ROLES:
            out.append(f'{where}: kitchen text for {role!r}, which is not a role')
        elif role not in owners:
            out.append(f'{where}: kitchen text for {role}, which is not an owner')
        if not str(text).strip():
            out.append(f'{where}: empty kitchen text for {role}')
    try:
        found = select(doc, str(part.get('select')))
    except PlaybookError as e:
        return out + [f'{where}: {e}']
    expected = part.get('reviewed')
    current = digest(text_of(doc, found))
    if expected != current:
        out.append(
            f'{where}: digest mismatch ({part["select"]}): reviewed {expected}, current {current}'
        )
    return out


def lines_of(ranges: list[tuple[int, int]]) -> set[int]:
    return {i for s, e in ranges for i in range(s, e)}


def playbook_problems(name: str, doc: Doc, entry: dict) -> list[str]:
    out: list[str] = []
    parts = entry.get('part', [])
    excludes = entry.get('exclude', [])
    claims: list[tuple[str, set[int]]] = []
    seen: set[str] = set()
    for part in parts:
        out += part_problems(name, doc, part)
    for label, item in [(f'part {p.get("id")}', p) for p in parts] + [
        (f'exclude {e.get("select")}', e) for e in excludes
    ]:
        selector = str(item.get('select'))
        if canonical(selector) in seen:
            out.append(f'{name}: duplicate selector {selector}')
        seen.add(canonical(selector))
        if label.startswith('exclude') and not str(item.get('reason', '')).strip():
            out.append(f'{name}: {label} has no reason')
        try:
            claims.append((label, lines_of(select(doc, selector))))
        except PlaybookError as e:
            if label.startswith('exclude'):
                out.append(f'{name}: {label}: {e}')
    for k, (a, mine) in enumerate(claims):
        for b, theirs in claims[k + 1 :]:
            if mine & theirs:
                out.append(f'{name}: overlapping selections: {a} and {b}')
    covered = set().union(*(lines for _, lines in claims)) if claims else set()
    for atom in doc.atoms:
        if lines_of([(atom.start, atom.end)]) <= covered:
            continue
        if atom.kind:
            out.append(
                f'{name}: unsupported shape at line {atom.start + 1}: {atom.kind}'
            )
        else:
            out.append(
                f'{name}: {atom.selector} (line {atom.start + 1}) is neither selected nor excluded'
            )
    return out


def validate_map(data: dict, roots: dict[str, Path] | None = None) -> list[str]:
    """Every problem in a playbook-roles map; an empty list means it is sound."""
    roots = roots or default_roots()
    out: list[str] = []
    ids: set[str] = set()
    for name, entry in sorted(data.get('playbook', {}).items()):
        try:
            doc = load_doc(roots, entry.get('source'))
        except PlaybookError as e:
            out.append(f'{name}: {e}')
            continue
        for part in entry.get('part', []):
            pid = part.get('id')
            if not pid:
                out.append(f'{name}: a part has no id')
            elif pid in ids:
                out.append(f'{name}: duplicate part id {pid}')
            ids.add(str(pid))
        out += playbook_problems(name, doc, entry)
    return out


MAP_PATH = Path(__file__).resolve().parents[1] / 'references/playbook-roles.toml'


def load_map(path: Path) -> dict:
    import tomllib

    with path.open('rb') as f:
        return tomllib.load(f)


def last_map_commit(path: Path) -> str | None:
    """The last commit that changed the map file, or None outside git."""
    import subprocess

    done = subprocess.run(
        ['git', 'log', '-1', '--format=%h', '--', path.name],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip() or None if done.returncode == 0 else None


def review(data: dict, name: str, roots: dict[str, Path]) -> list[dict]:
    """The parts of playbook `name` whose source no longer matches the digest
    they were reviewed at: expected and current digest and the current text,
    or the error that stops the part resolving."""
    entry = data.get('playbook', {}).get(name)
    if entry is None:
        raise PlaybookError(f'no playbook {name!r} in the map')
    doc = load_doc(roots, entry.get('source'))
    changed = []
    for part in entry.get('part', []):
        found = {'id': part['id'], 'select': part['select']}
        try:
            text = text_of(doc, select(doc, part['select']))
        except PlaybookError as e:
            changed.append({**found, 'expected': part['reviewed'], 'error': str(e)})
            continue
        if digest(text) != part['reviewed']:
            changed.append(
                {
                    **found,
                    'expected': part['reviewed'],
                    'current': digest(text),
                    'text': text,
                }
            )
    return changed
