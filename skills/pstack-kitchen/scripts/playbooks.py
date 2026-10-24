#!/usr/bin/env python3
"""Parse a pstack playbook's Markdown into addressable parts.

A document is a title (frontmatter, any preamble and the first heading line),
then blocks. The blocks before the first step, lead-in or section are the
intro; a numbered list at the top level gives the steps; a paragraph that
opens with a bold one-to-four word lead-in such as `**Worktree.**` starts a
lead, which keeps the blocks that follow it up to the next lead; blocks after
the steps are the tail; `**Reply:**` starts the reply; a heading starts a
section that runs to the next heading of the same or a higher level.

Selectors address the parts: `intro:N`, `lead:<Word>`, `step:N`,
`step:<Section>/N`, `section:<Heading>`, `tail:N`, `reply` and
`heading:<path>` (a hierarchical heading path). Numbers inside a step's own
nested lists and fences are part of that step, not steps of their own.

Stdlib only, and independent of kitchen_core.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

HEADING = re.compile(r'(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$')
LIST_MARK = re.compile(r'(?:[-*+]|\d{1,9}[.)])[ \t]+\S')
FENCE = re.compile(r'(`{3,}|~{3,})')
LEAD = re.compile(r'\*\*([^*]+?)\.\*\*')
REPLY = re.compile(r'\*\*Reply:\*\*')
LEAD_WORDS = 4
KINDS = ('intro', 'lead', 'step', 'section', 'tail', 'reply')


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


def section_atoms(
    lines: list[str], blocks: list[Block], at: int, end: int
) -> tuple[list[Atom], list[tuple[str, int, int]]]:
    """The atoms of the section whose heading block is `blocks[at]`: one per
    step when it holds nothing but a numbered list (then the whole section is
    also addressable, as an alias), else the section whole."""
    head = blocks[at]
    inner = [b for b in blocks[at + 1 :] if b.start < end]
    end = trimmed(lines, head.start, end)
    if len(inner) == 1 and inner[0].kind == 'numbered':
        steps = [
            Atom(f'step:{head.text}/{k}', s, e)
            for k, (s, e) in enumerate(inner[0].items, 1)
        ]
        return steps, [(f'section:{head.text}', head.start, end)]
    return [Atom(f'section:{head.text}', head.start, end)], []


def build_atoms(
    lines: list[str], blocks: list[Block]
) -> tuple[list[Atom], list[tuple[str, int, int]], int]:
    atoms: list[Atom] = []
    aliases: list[tuple[str, int, int]] = []
    first = next((k for k, b in enumerate(blocks) if b.kind == 'heading'), None)
    title_end = blocks[first].end if first is not None else frontmatter_end(lines)
    k = first + 1 if first is not None else 0
    steps_seen, intro, tail = False, 0, 0
    current: int | None = None  # the lead or reply that absorbs the next blocks
    while k < len(blocks):
        b = blocks[k]
        first_line = lines[b.start]
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
            current = None
        elif b.kind == 'numbered' and current is None:
            if steps_seen:
                atoms.append(Atom('numbered list', b.start, b.end, 'second list'))
            else:
                steps_seen = True
                atoms.extend(
                    Atom(f'step:{n}', s, e) for n, (s, e) in enumerate(b.items, 1)
                )
        elif b.kind == 'paragraph' and REPLY.match(first_line):
            atoms.append(Atom('reply', b.start, b.end))
            current = len(atoms) - 1
        elif b.kind == 'paragraph' and (name := lead_name(first_line)):
            atoms.append(Atom(f'lead:{name}', b.start, b.end))
            current = len(atoms) - 1
        elif current is not None:
            old = atoms[current]
            atoms[current] = Atom(old.selector, old.start, b.end)
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
    if kind in ('intro', 'tail', 'step') and not slash and arg.isdigit():
        return f'{kind}:{int(arg)}'
    if kind == 'step' and slash and number.isdigit():
        return f'step:{head}/{int(number)}'
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
    installed copy."""
    return {'skills': Path(__file__).resolve().parents[2]}


def resolve_source(roots: dict[str, Path], root: str, path: str) -> Path:
    if root not in roots:
        raise PlaybookError(f'unknown source root {root!r}')
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or '\\' in path or '..' in pure.parts:
        raise PlaybookError(f'source path {path!r} must be relative inside {root}')
    base = roots[root].resolve()
    full = (base / path).resolve()
    if not full.is_relative_to(base):
        raise PlaybookError(f'source path {path!r} leaves the {root} root')
    if not full.is_file():
        raise PlaybookError(f'source {root}:{path} is not a file')
    return full
