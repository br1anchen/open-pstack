#!/usr/bin/env python3
"""Move sections of a repo's Markdown documents into new modules from a reviewed migration file.

This module holds the migration file's model and loader, the reader over a source's original bytes, the references a
file makes to others, the cuts a migration makes and their rendering. Everything but `load` and the git readers is a
pure function over bytes and typed data; nothing here writes a file.

A migration reads every input from its recorded base commit, never from the working tree, so a preview, an apply and
a second apply all reconstruct the same outputs from the same inputs.

Stdlib only, and independent of kitchen_core. Headings and fences come from playbooks.py.
"""

import dataclasses
import difflib
import functools
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from urllib.parse import quote, unquote

SCHEMA = 1
ROOT_KEYS = ('schema', 'id', 'base', 'writes')
TABLES = ('file', 'move', 'rewrite', 'keep')
FILE_KEYS = ('path', 'sha256')
MOVE_KEYS = ('source', 'ancestry', 'occurrence', 'to', 'requires')
REWRITE_KEYS = ('path', 'from', 'to', 'count')
KEEP_KEYS = ('path', 'ref', 'reason')
TABLE_KEYS = {
    'file': FILE_KEYS,
    'move': MOVE_KEYS,
    'rewrite': REWRITE_KEYS,
    'keep': KEEP_KEYS,
}
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
SHA256 = re.compile(r'[0-9a-f]{64}')
SETEXT = re.compile(r'(=+|-+)[ \t]*')
INDENTED_ATX = re.compile(r'#{1,6}(?:[ \t]|$)')
THEMATIC = re.compile(r'(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})')
GIT_TIMEOUT_S = 60
CODE_INDENT = 4
REPO_ENV = (
    'GIT_DIR',
    'GIT_WORK_TREE',
    'GIT_INDEX_FILE',
    'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    'GIT_NAMESPACE',
)


class ContextError(Exception):
    """A migration, a source or a base that cannot be used; the message says which and why."""


@functools.cache
def playbooks_module() -> ModuleType:
    """playbooks.py, loaded by path: it sits beside this file and is not a package."""
    spec = importlib.util.spec_from_file_location(
        'playbooks', Path(__file__).with_name('playbooks.py')
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str


@dataclass(frozen=True)
class Move:
    source: str
    ancestry: tuple[str, ...]
    occurrence: int | None
    to: str
    requires: tuple[str, ...]


@dataclass(frozen=True)
class Rewrite:
    path: str
    from_: str
    to: str
    count: int = 1  # how many times `from` occurs in the file; all are replaced


@dataclass(frozen=True)
class Keep:
    path: str
    ref: str
    reason: str


@dataclass(frozen=True)
class Migration:
    schema: int
    id: str
    base: str
    writes: tuple[str, ...]
    files: tuple[FileEntry, ...]
    moves: tuple[Move, ...]
    rewrites: tuple[Rewrite, ...]
    keeps: tuple[Keep, ...]


def check_path(path: object, where: str) -> str:
    """A repo-relative path: no `..`, no leading `/`, no backslash, no empty or `.` part."""
    if not isinstance(path, str) or not path:
        raise ContextError(f'{where}: a path must be a non-empty string')
    parts = path.split('/')
    if path.startswith('/'):
        raise ContextError(f'{where}: {path!r} must not start with `/`')
    if '\\' in path:
        raise ContextError(f'{where}: {path!r} must not contain a backslash')
    if '..' in parts:
        raise ContextError(f'{where}: {path!r} must not contain a `..` part')
    if '.' in parts:
        raise ContextError(f'{where}: {path!r} must not contain a `.` part')
    if '' in parts:
        raise ContextError(f'{where}: {path!r} must not have an empty path part')
    return path


def string(table: dict, key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ContextError(f'{where}: `{key}` must be a non-empty string')
    return value


def strings(table: dict, key: str, where: str, optional: bool = False) -> list[str]:
    if key not in table and optional:
        return []
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ContextError(f'{where}: `{key}` must be a list of non-empty strings')
    return value


def rewrite_count(table: dict, where: str) -> int:
    count = table.get('count', 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ContextError(f'{where}: `count` must be a whole number from 1')
    return count


def entries(data: dict, name: str) -> list[dict]:
    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ContextError(f'`{name}` must be an array of tables: write [[{name}]]')
    return value


def check_keys(table: dict, allowed: tuple[str, ...], where: str, after: str) -> None:
    for key in table:
        if key in ROOT_KEYS:
            raise ContextError(
                f'{where}: `{key}` is not a root key here: it follows {after}, so TOML makes it a key of that table. '
                'Put schema, id, base and writes before the first array table'
            )
        if key not in allowed:
            raise ContextError(f'{where}: unknown key `{key}`')


def parse_migration(text: str) -> Migration:
    """A schema-1 migration from its TOML text; ContextError on any schema problem."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ContextError(f'not valid TOML: {e}') from e
    for key in data:
        if key not in ROOT_KEYS and key not in TABLES:
            raise ContextError(f'unknown root key `{key}`')
    tables = {name: entries(data, name) for name in TABLES}
    for name in TABLES:
        allowed = TABLE_KEYS[name]
        for n, table in enumerate(tables[name], 1):
            check_keys(table, allowed, f'[[{name}]] {n}', f'[[{name}]] {n}')
    schema = data.get('schema')
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != SCHEMA:
        raise ContextError(f'`schema` must be {SCHEMA}')
    migration_id = string(data, 'id', 'root')
    if not ID.fullmatch(migration_id):
        raise ContextError(f'root: `id` {migration_id!r} must match {ID.pattern}')
    base = string(data, 'base', 'root')
    if base.startswith('-'):
        raise ContextError(f'root: `base` {base!r} must be a commit, not an option')
    writes = strings(data, 'writes', 'root')
    files = []
    for n, t in enumerate(tables['file'], 1):
        where = f'[[file]] {n}'
        sha = string(t, 'sha256', where)
        if not SHA256.fullmatch(sha):
            raise ContextError(f'{where}: `sha256` must be 64 lowercase hex digits')
        files.append(FileEntry(check_path(t.get('path'), where), sha))
    moves = []
    for n, t in enumerate(tables['move'], 1):
        where = f'[[move]] {n}'
        occurrence = t.get('occurrence')
        if occurrence is not None and (
            not isinstance(occurrence, int)
            or isinstance(occurrence, bool)
            or occurrence < 1
        ):
            raise ContextError(f'{where}: `occurrence` must be a whole number from 1')
        ancestry = strings(t, 'ancestry', where)
        if not ancestry:
            raise ContextError(f'{where}: `ancestry` must name at least one heading')
        moves.append(
            Move(
                check_path(t.get('source'), where),
                tuple(ancestry),
                occurrence,
                check_path(t.get('to'), where),
                tuple(
                    check_path(p, where)
                    for p in strings(t, 'requires', where, optional=True)
                ),
            )
        )
    rewrites = []
    for n, t in enumerate(tables['rewrite'], 1):
        where = f'[[rewrite]] {n}'
        r = Rewrite(
            check_path(t.get('path'), where),
            string(t, 'from', where),
            string(t, 'to', where),
            rewrite_count(t, where),
        )
        for key, text in (('from', r.from_), ('to', r.to)):
            if '\n' in text:
                raise ContextError(f'{where}: `{key}` must not contain a newline')
        rewrites.append(r)
    keeps = [
        Keep(
            check_path(t.get('path'), f'[[keep]] {n}'),
            string(t, 'ref', f'[[keep]] {n}'),
            string(t, 'reason', f'[[keep]] {n}'),
        )
        for n, t in enumerate(tables['keep'], 1)
    ]
    migration = Migration(
        SCHEMA,
        migration_id,
        base,
        tuple(writes),
        tuple(files),
        tuple(moves),
        tuple(rewrites),
        tuple(keeps),
    )
    check_writes(migration)
    return migration


def check_writes(m: Migration) -> None:
    """`writes` is exact paths, unique, and equal to the sources, destinations and rewrite targets."""
    for w in m.writes:
        check_path(w, 'writes')
        if any(c in w for c in '*?['):
            raise ContextError(f'writes: {w!r} is a glob; list exact paths')
    seen: set[str] = set()
    for w in m.writes:
        if w in seen:
            raise ContextError(f'writes: {w!r} is listed twice')
        seen.add(w)
    planned = {mv.source for mv in m.moves} | {mv.to for mv in m.moves}
    planned |= {r.path for r in m.rewrites}
    for path in sorted(planned - seen):
        raise ContextError(
            f'writes: {path!r} is a source, destination or rewrite target but is not in writes'
        )
    for path in sorted(seen - planned):
        raise ContextError(
            f'writes: {path!r} is not a source, destination or rewrite target of any move or rewrite'
        )
    names = [f.path for f in m.files]
    for r in m.rewrites:
        if r.path not in names:
            raise ContextError(
                f'[[rewrite]]: target {r.path!r} has no [[file]] entry; every existing file a migration changes needs one'
            )
    for path in names:
        if names.count(path) > 1:
            raise ContextError(f'[[file]]: {path!r} is listed twice')


def load(path: Path) -> Migration:
    """The migration file at `path`; ContextError when it cannot be read or breaks the schema."""
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise ContextError(f'cannot read migration {path}: {e}') from e
    return parse_migration(text)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if k not in REPO_ENV}
    try:
        return subprocess.run(
            ['git', '-C', str(repo), *args],
            env=env,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ContextError(f'git {args[0]} did not run: {e}') from e


def resolve_base(repo: Path, migration: Migration) -> str:
    done = git(repo, 'rev-parse', '--verify', f'{migration.base}^{{commit}}')
    if done.returncode:
        raise ContextError(
            f'base {migration.base!r} does not resolve to a commit in {repo}'
        )
    return done.stdout.decode().strip()


def read_base(repo: Path, migration: Migration) -> dict[str, bytes]:
    """The bytes of every `[[file]]` at the migration's base commit, each checked against its sha256."""
    commit = resolve_base(repo, migration)
    found: dict[str, bytes] = {}
    for entry in migration.files:
        done = git(repo, 'cat-file', 'blob', f'{commit}:{entry.path}')
        if done.returncode:
            raise ContextError(f'{entry.path}: not a file at base {migration.base!r}')
        digest = hashlib.sha256(done.stdout).hexdigest()
        if digest != entry.sha256:
            raise ContextError(
                f'{entry.path}: sha256 {digest} at base {migration.base!r} differs from the migration'
                f' ({entry.sha256}); the file changed after the migration was written'
            )
        found[entry.path] = done.stdout
    return found


def repo_files(repo: Path, migration: Migration) -> frozenset[str]:
    """Every file of the repo at the migration's base commit."""
    return tracked(repo, resolve_base(repo, migration))


def base_files_at(
    repo: Path, migration: Migration, paths: Collection[str]
) -> frozenset[str]:
    """Which of `paths` exist as files at the migration's base commit."""
    commit = resolve_base(repo, migration)
    return frozenset(
        p for p in paths if git(repo, 'cat-file', '-e', f'{commit}:{p}').returncode == 0
    )


# ---- the reader ------------------------------------------------------------


def lines_of(data: bytes) -> list[bytes]:
    """The lines of `data`, each keeping its line ending; the last has none when the data does not end in one."""
    parts = data.split(b'\n')
    lines = [p + b'\n' for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def decode(lines: list[bytes], where: str) -> list[str]:
    """The lines as text without their endings."""
    try:
        return [line.removesuffix(b'\n').decode() for line in lines]
    except UnicodeDecodeError as e:
        raise ContextError(f'{where}: not valid UTF-8: {e}') from e


@dataclass(frozen=True)
class Section:
    """A heading and the lines it owns: from its line up to the next heading of the same or a higher level (untrimmed)."""

    level: int
    raw: str  # the heading text as written, without its `#` markers
    plain: str  # the text with inline markup rendered away
    path: tuple[str, ...]  # `raw` of every ancestor and of this heading
    start: int  # the heading's line index
    end: int  # exclusive


@dataclass(frozen=True)
class Reading:
    lines: list[bytes]
    sections: list[Section]
    body_start: int  # lines before this index are frontmatter
    fenced: frozenset[int]


def indent_of(line: str) -> int:
    """The leading spaces of a line, or CODE_INDENT for a tab."""
    if line.startswith('\t'):
        return CODE_INDENT
    return len(line) - len(line.lstrip(' '))


def setext_line(text: list[str], i: int, fenced: frozenset[int], start: int) -> bool:
    """Whether line `i` underlines a setext heading: an underline of `=` or `-` indented up to 3 spaces under a
    paragraph line that is itself indented up to 3 spaces."""
    pb = playbooks_module()
    line = text[i]
    if i - 1 < start or i in fenced or i - 1 in fenced:
        return False
    if indent_of(line) > 3 or not SETEXT.fullmatch(line.lstrip(' ')):
        return False
    prev = text[i - 1]
    body = prev.lstrip(' ')
    return not (
        not prev.strip()
        or indent_of(prev) > 3
        or pb.HEADING.match(body)
        or pb.LIST_MARK.match(body)
        or body[0] in '>|'
        or THEMATIC.fullmatch(body)
    )


def indented_heading(line: str) -> bool:
    """Whether a line is an ATX heading to a Markdown renderer that playbooks.py cannot see: indented 1 to 3 spaces."""
    return 1 <= indent_of(line) <= 3 and bool(INDENTED_ATX.match(line.lstrip(' ')))


def headings_of(
    text: list[str], fenced: frozenset[int], start: int
) -> list[tuple[int, int, str]]:
    """Every ATX heading outside fences and frontmatter as (line index, level, raw text)."""
    pb = playbooks_module()
    found = []
    for i in range(start, len(text)):
        m = None if i in fenced else pb.HEADING.match(text[i])
        if m:
            found.append((i, len(m.group(1)), m.group(2)))
    return found


def read(data: bytes, where: str) -> Reading:
    """The headings of a source, from its original bytes. Refuses what schema 1 cannot cut safely: a `\\r` byte, an
    unclosed fence, a setext heading."""
    pb = playbooks_module()
    if b'\r' in data:
        raise ContextError(
            f'{where}: contains a carriage return; schema 1 takes LF files only'
        )
    lines = lines_of(data)
    text = decode(lines, where)
    start = pb.frontmatter_end(text)
    inside, unclosed = pb.scan_fences(text[start:])
    if unclosed is not None:
        raise ContextError(
            f'{where}: the fence opened at line {start + unclosed + 1} never closes'
        )
    fenced = frozenset(start + i for i in inside)
    for i in range(start, len(text)):
        if i in fenced:
            continue
        if setext_line(text, i, fenced, start):
            raise ContextError(
                f'{where}: line {i + 1} underlines a setext heading; schema 1 reads `#` headings only'
            )
        if indented_heading(text[i]):
            raise ContextError(
                f'{where}: line {i + 1} is a heading indented 1 to 3 spaces; schema 1 reads `#` headings at the start of a line only'
            )
    found = headings_of(text, fenced, start)
    out: list[Section] = []
    stack: list[tuple[int, str]] = []
    for k, (i, level, raw) in enumerate(found):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, raw))
        end = next((j for j, lv, _ in found[k + 1 :] if lv <= level), len(text))
        out.append(Section(level, raw, plain(raw), tuple(t for _, t in stack), i, end))
    return Reading(lines, out, start, fenced)


def sections(data: bytes, where: str = 'source') -> list[Section]:
    return read(data, where).sections


def find(
    found: list[Section], ancestry: tuple[str, ...], occurrence: int | None
) -> Section:
    """The section whose full path equals `ancestry`; `occurrence` (1-based, document order) picks among several."""
    named = ' > '.join(ancestry)
    same = [s for s in found if s.path == ancestry]
    if not same:
        raise ContextError(f'no heading with ancestry {named}')
    if occurrence is None:
        if len(same) > 1:
            where = ', '.join(str(s.start + 1) for s in same)
            raise ContextError(
                f'{len(same)} headings share the ancestry {named} (lines {where}); add `occurrence`'
            )
        return same[0]
    if occurrence > len(same):
        raise ContextError(
            f'occurrence {occurrence} is out of range: {len(same)} heading(s) have the ancestry {named}'
        )
    return same[occurrence - 1]


# ---- slugs -----------------------------------------------------------------

CODE_SPAN = re.compile(r'(`+)(.+?)\1')
IMAGE = re.compile(r'!\[([^\]]*)\]\([^)]*\)')
LINK = re.compile(r'\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])')
STRONG = re.compile(r'(\*\*|__)(.+?)\1')
EMPHASIS = re.compile(r'\*(.+?)\*|(?<!\w)_(.+?)_(?!\w)')
HTML_TAG = re.compile(r'</?[A-Za-z][^>]*>')


def render_inline(piece: str) -> str:
    piece = IMAGE.sub(r'\1', piece)
    piece = LINK.sub(r'\1', piece)
    piece = HTML_TAG.sub('', piece)
    piece = STRONG.sub(r'\2', piece)
    return EMPHASIS.sub(lambda m: m.group(1) or m.group(2), piece)


def plain(raw: str) -> str:
    """A heading's text with code spans, links, images, emphasis and HTML tags rendered to their text."""
    out, at = [], 0
    for m in CODE_SPAN.finditer(raw):
        out.append(render_inline(raw[at : m.start()]))
        out.append(m.group(2))
        at = m.end()
    out.append(render_inline(raw[at:]))
    return ''.join(out).strip()


def slug(text: str, seen: dict[str, int]) -> str:
    """GitHub's anchor for a heading: the rendered text lowercased, characters other than letters, digits, spaces,
    `-` and `_` dropped, spaces to `-`, then a document-scoped `-1`, `-2` suffix for a repeat. `seen` carries the
    anchors the document has already used."""
    base = ''.join(c for c in plain(text).lower() if c.isalnum() or c in ' -_').replace(
        ' ', '-'
    )
    result = base
    while result in seen:
        seen[base] = seen.get(base, 0) + 1
        result = f'{base}-{seen[base]}'
    seen[result] = 0
    return result


def anchors(found: list[Section]) -> dict[int, str]:
    """Each heading's anchor in its document, by the heading's line index."""
    seen: dict[str, int] = {}
    return {s.start: slug(s.raw, seen) for s in found}


# ---- references ------------------------------------------------------------

SCHEME = re.compile(r'(?:[A-Za-z][A-Za-z0-9+.-]*:|//)')
DEFINITION = re.compile(r' {0,3}\[((?:[^\]\\]|\\.)+)\]:[ \t]*(<[^>\n]*>|\S+)')
CODE_RUN = re.compile(r'(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)')
TOKEN = re.compile(r"""[^\s"'`()<>\[\]{},=;|*]+""")
MARKDOWN = ('.md', '.markdown')
SCANNED = (*MARKDOWN, '.toml', '.txt')
MIGRATIONS = '.agents/migrations/'


@dataclass(frozen=True)
class Ref:
    """A reference to a file, or to a section of one, found in a file's bytes."""

    path: str  # the file that holds the reference
    line: int  # 1-based
    start: int  # the target's columns in the line (characters, end exclusive)
    end: int
    kind: (
        str  # inline, image, definition, or text (a path string in TOML or plain text)
    )
    target: str  # the path as written, query included; empty for a bare `#fragment`
    fragment: str  # as written, without the `#`
    raw: str  # the whole construct as written


def split_target(written: str) -> tuple[str, str]:
    target, _, fragment = written.partition('#')
    return target, fragment


def destination_at(line: str, i: int) -> tuple[int, int, bool, int] | None:
    """The link destination that starts at `line[i]`, just after a `(`: its start, its end, whether it is written in
    angle brackets, and the index of the closing `)`. None when the parenthesis never closes the link."""
    n = len(line)
    while i < n and line[i] in ' \t':
        i += 1
    if i >= n:
        return None
    if line[i] == '<':
        stop = line.find('>', i + 1)
        if stop < 0:
            return None
        start, bracketed, j = i + 1, True, stop + 1
    else:
        depth, j = 0, i
        while j < n:
            c = line[j]
            if c == '\\' and j + 1 < n:
                j += 2
                continue
            if c in ' \t':
                break
            if c == '(':
                depth += 1
            elif c == ')':
                if depth == 0:
                    break
                depth -= 1
            j += 1
        start, stop, bracketed = i, j, False
    k = j
    while k < n and line[k] in ' \t':
        k += 1
    if k < n and line[k] != ')' and k > j and line[k] in '"\'(':
        end = line.find(')' if line[k] == '(' else line[k], k + 1)
        if end < 0:
            return None
        k = end + 1
        while k < n and line[k] in ' \t':
            k += 1
    if k >= n or line[k] != ')':
        return None
    return start, stop, bracketed, k


def opener_of(line: str, close: int) -> int | None:
    """The `[` that matches the `]` at `close`, skipping nested brackets."""
    depth = 0
    for i in range(close - 1, -1, -1):
        if line[i] in '[]' and (i == 0 or line[i - 1] != '\\'):
            if line[i] == ']':
                depth += 1
            elif depth == 0:
                return i
            else:
                depth -= 1
    return None


def paragraph_before(
    masked: list[str], fenced: set[int], first: int, i: int
) -> list[str]:
    """The masked lines of the paragraph that line `i` continues: back to a blank line, a fence, a heading or the
    start of the body."""
    pb = playbooks_module()
    j = i
    while (
        j > first
        and (before := masked[j - 1]).strip()
        and j - 1 not in fenced
        and not pb.HEADING.match(before)
    ):
        j -= 1
    return masked[j:i]


def markdown_refs(path: str, lines: list[str]) -> list[Ref]:
    start = playbooks_module().frontmatter_end(lines)
    inside, _ = playbooks_module().scan_fences(lines[start:])
    fenced = {start + i for i in inside}
    masked_lines = [CODE_RUN.sub(lambda m: ' ' * len(m.group()), x) for x in lines]
    found: list[Ref] = []
    for i in range(start, len(lines)):
        if i in fenced:
            continue
        line = lines[i]
        masked = masked_lines[i]
        defined = DEFINITION.match(masked)
        if defined and not defined.group(1).startswith('^'):
            written = defined.group(2)
            at = defined.start(2)
            if written.startswith('<'):
                written, at = written[1:-1], at + 1
            target, fragment = split_target(written)
            if (target or fragment) and not SCHEME.match(target):
                found.append(
                    Ref(
                        path,
                        i + 1,
                        at,
                        at + len(written),
                        'definition',
                        target,
                        fragment,
                        line.strip(),
                    )
                )
            continue
        before = paragraph_before(masked_lines, fenced, start, i)
        for m in re.finditer(r'\]\(', masked):
            close = m.start()
            if masked[close - 1 : close] == '\\':
                continue
            dest = destination_at(masked, m.end())
            if dest is None:
                continue
            at, stop, _, paren = dest
            written = line[at:stop]
            target, fragment = split_target(written)
            if not (target or fragment) or SCHEME.match(target):
                continue
            opener = opener_of(masked, close)
            if opener is not None:
                image = opener > 0 and masked[opener - 1] == '!'
                first = opener - 1 if image else opener
                raw = line[first : paren + 1]
            else:
                # the link text opened on an earlier line of this paragraph
                held = len('\n'.join(before)) + 1 if before else 0
                text = '\n'.join([*before, masked[:close]])
                opener = opener_of(text, len(text)) if before else None
                if opener is None:
                    continue
                image = opener > 0 and text[opener - 1] == '!'
                first = opener - 1 if image else opener
                whole = '\n'.join([*lines[i - len(before) : i], line])
                raw = whole[first : held + paren + 1]
            found.append(
                Ref(
                    path,
                    i + 1,
                    at,
                    stop,
                    'image' if image else 'inline',
                    target,
                    fragment,
                    raw,
                )
            )
    return found


def text_refs(path: str, lines: list[str], files: Collection[str]) -> list[Ref]:
    found: list[Ref] = []
    for i, line in enumerate(lines):
        for m in TOKEN.finditer(line):
            token = m.group().rstrip('.,:;!?')
            target, fragment = split_target(token)
            if target in files:
                found.append(
                    Ref(
                        path,
                        i + 1,
                        m.start(),
                        m.start() + len(token),
                        'text',
                        target,
                        fragment,
                        token,
                    )
                )
    return found


def references(path: str, data: bytes, files: Collection[str] = ()) -> list[Ref]:
    """Every reference `data` makes, in order. Markdown (`.md`, `.markdown`): inline links and images, reference
    definitions and bare `#fragment` targets, outside fences, frontmatter and code spans; a URL with a scheme is not
    a local reference. Any other file: each `path` or `path#anchor` string that exactly names a file of `files`. A
    file that is not UTF-8 makes none."""
    try:
        lines = data.decode().split('\n')
    except UnicodeDecodeError:
        return []
    if path.endswith(MARKDOWN):
        return markdown_refs(path, lines)
    return text_refs(path, lines, files)


def target_path(ref: Ref) -> str | None:
    """The repo path a reference names: relative to the referring file's directory in Markdown (`/` starts at the
    repo root), to the repo root in TOML and text. None when it leaves the repo or names a directory (a trailing `/`)."""
    written = unquote(ref.target.partition('?')[0])
    if not written:
        return ref.path
    if written.endswith('/'):
        return None
    base = ''
    if ref.kind != 'text' and not written.startswith('/'):
        base = posixpath.dirname(ref.path)
    found = posixpath.normpath(posixpath.join(base, written.lstrip('/')))
    return None if found == '.' or found.startswith('..') else found


def resolve(ref: Ref, files: Collection[str]) -> tuple[str, str] | None:
    """The file a reference names and the anchor in it (lowercased, empty for the file itself); None when no such
    file is among `files`."""
    found = target_path(ref)
    if found is None or found not in files:
        return None
    return found, unquote(ref.fragment).lower()


def target_of(ref: Ref) -> str:
    """The `path#anchor` a reference names, as `resolve` reads it but for a file that need not exist."""
    return f'{target_path(ref)}#{unquote(ref.fragment).lower()}'.removesuffix('#')


# ---- cuts ------------------------------------------------------------------


@dataclass(frozen=True)
class Cut:
    move: Move
    section: Section
    parent: (
        int | None
    )  # the index of the tightest cut that contains this one, in the same source


def contains(outer: Section, inner: Section) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def cuts(
    migration: Migration,
    found: dict[str, list[Section]],
    existing: Collection[str] = (),
) -> list[Cut]:
    """The cut each move makes, in the migration's order. Refuses a move whose source has no
    `[[file]]`, a section moved twice, a destination named twice, and a destination that is a source or exists at
    the base (`existing`). Two heading sections either nest or lie apart, so a section moved twice is the only
    overlap that is not nesting."""
    declared = {f.path for f in migration.files}
    sources = {mv.source for mv in migration.moves}
    destinations: set[str] = set()
    picked: list[tuple[Move, Section]] = []
    for mv in migration.moves:
        if mv.source not in declared:
            raise ContextError(f'move source {mv.source!r} has no [[file]] entry')
        if mv.to in destinations:
            raise ContextError(f'destination {mv.to!r} is named by two moves')
        destinations.add(mv.to)
        if mv.to in sources:
            raise ContextError(f'destination {mv.to!r} is also a move source')
        if mv.to in existing:
            raise ContextError(
                f'destination {mv.to!r} already exists at base {migration.base!r}'
            )
        picked.append((mv, find(found[mv.source], mv.ancestry, mv.occurrence)))
    out: list[Cut] = []
    for i, (mv, a) in enumerate(picked):
        parent = None
        for j, (other, b) in enumerate(picked):
            if i == j or other.source != mv.source:
                continue
            if (a.start, a.end) == (b.start, b.end):
                raise ContextError(
                    f'the section {" > ".join(a.path)} of {mv.source} is moved twice'
                )
            if contains(b, a):
                if parent is None or picked[parent][1].start < b.start:
                    parent = j
        out.append(Cut(mv, a, parent))
    return out


# ---- anchor map ------------------------------------------------------------


def anchor_map(
    migration: Migration,
    base_files: dict[str, bytes],
    outputs: dict[str, bytes],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Where each moved heading's anchor went: (old path, old anchor) to (new path, new anchor), for the moved
    heading and every heading under it. The old anchor is the heading's in its source at the base and the new one its
    anchor in the destination `outputs` holds, each with its own document's duplicate suffix. A heading inside a
    nested move points at the inner destination, though the outer destination keeps its heading line too."""
    sources = {mv.source for mv in migration.moves}
    readings = {p: read(d, p) for p, d in base_files.items() if p in sources}
    made = cuts(migration, {p: r.sections for p, r in readings.items()})
    old = {p: anchors(r.sections) for p, r in readings.items()}
    new = {
        c.move.to: anchors(read(outputs[c.move.to], c.move.to).sections) for c in made
    }
    mapped: dict[tuple[str, str], tuple[str, str]] = {}
    for i, cut in enumerate(made):
        kids = [k.section for k in made if k.parent == i]
        shown = [
            s
            for s in readings[cut.move.source].sections
            if contains(cut.section, s)
            and not any(contains(k, s) and k.start != s.start for k in kids)
        ]
        landed = read(outputs[cut.move.to], cut.move.to).sections
        if len(shown) != len(landed):
            raise ContextError(
                f'{cut.move.to}: holds {len(landed)} headings, but {len(shown)} come from {cut.move.source}'
            )
        tops = {k.start for k in kids}
        for s, there in zip(shown, landed, strict=True):
            if s.start in tops:
                continue
            mapped[(cut.move.source, old[cut.move.source][s.start])] = (
                cut.move.to,
                new[cut.move.to][there.start],
            )
    return mapped


# ---- render ----------------------------------------------------------------


def shift_line(line: bytes, by: int) -> bytes:
    """A heading line with `by` fewer `#` markers."""
    level = len(line) - len(line.lstrip(b'#'))
    return b'#' * (level - by) + line[level:]


def relative(from_path: str, to_path: str) -> str:
    return posixpath.relpath(to_path, posixpath.dirname(from_path) or '.')


def pointer(from_path: str, dest: str, anchor: str) -> bytes:
    return f'Moved to [{dest}]({relative(from_path, dest)}#{anchor}).\n'.encode()


def read_first(m: Migration, mv: Move, present: Collection[str]) -> bytes:
    """The `Read first:` line of a destination whose move names prerequisites."""
    destinations = {x.to for x in m.moves}
    links = []
    for path in mv.requires:
        if path not in destinations and path not in present:
            raise ContextError(
                f'{mv.to}: requires {path!r}, which is neither a destination of this migration nor a file at base'
            )
        links.append(f'[{path}]({relative(mv.to, path)})')
    return f'Read first: {", ".join(links)}.\n'.encode()


def check_requires(m: Migration) -> None:
    """Refuses a `requires` cycle among the migration's own destinations."""
    graph = {mv.to: mv.requires for mv in m.moves}
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 2 or node not in graph:
            return
        if state.get(node) == 1:
            ring = ' -> '.join([*trail[trail.index(node) :], node])
            raise ContextError(f'requires form a cycle: {ring}')
        state[node] = 1
        for nxt in graph[node]:
            visit(nxt, [*trail, node])
        state[node] = 2

    for node in graph:
        visit(node, [])


def with_newline(line: bytes) -> bytes:
    return line if line.endswith(b'\n') else line + b'\n'


@dataclass
class Patches:
    """What step two of `render` changes in the text the cuts copy: the lines whose reference targets are rebased,
    the definitions appended to a destination, and the definition lines that leave an index."""

    lines: dict[tuple[int, int], bytes]  # (cut index, source line) -> the rebased line
    tail: dict[
        int, list[bytes]
    ]  # cut index -> definition lines for the end of its destination
    drop: dict[str, set[int]]  # source -> definition lines that moved out of the index


def own_lines(made: list[Cut], i: int) -> list[int]:
    """The source lines cut `i` copies as its own: its section, less what a nested cut takes, but with the nested
    cut's heading line (the parent keeps it as a stub)."""
    sec = made[i].section
    inner = [k.section for k in made if k.parent == i]
    return [
        n
        for n in range(sec.start, sec.end)
        if not any(k.start < n < k.end for k in inner)
    ]


def rebased_target(
    ref: Ref,
    dest: str,
    known: Collection[str],
    amap: dict[tuple[str, str], tuple[str, str]],
    anchored: dict[str, set[str]],
) -> str | None:
    """What a reference in text moved to `dest` should say in place of its target, or None to leave it byte for
    byte: when it does not resolve, or already says it. A fragment to a moved section goes through `amap`; a path
    is rewritten from the destination's directory."""
    found = resolve(ref, known)
    if found is None:
        return None
    path, anchor = found
    written = ref.target.partition('?')[0]
    query = ref.target[len(written) :]
    absolute = written.startswith('/')
    if ref.fragment and (path, anchor) in amap:
        new_path, new_anchor = amap[(path, anchor)]
        if new_path == dest and not query:
            made = f'#{new_anchor}'
        else:
            to = f'/{new_path}' if absolute else relative(dest, new_path)
            made = f'{to}{query}#{new_anchor}'
    else:
        if path in anchored and ref.fragment and anchor not in anchored[path]:
            return None
        if absolute:
            return None
        to = relative(dest, path)
        if '%' in written:
            to = quote(to, safe='/')
        made = f'{to}{query}' + (f'#{ref.fragment}' if ref.fragment else '')
    written_now = ref.target + (f'#{ref.fragment}' if ref.fragment else '')
    return None if made == written_now else made


def patched_line(line: bytes, edits: list[tuple[int, int, str]]) -> bytes:
    """`line` with each (start, end, text) edit applied, columns counted in characters."""
    ending = b'\n' if line.endswith(b'\n') else b''
    text = line.removesuffix(b'\n').decode()
    for start, end, made in sorted(edits, reverse=True):
        text = text[:start] + made + text[end:]
    return text.encode() + ending


def label_of(text: str) -> str:
    return ' '.join(text.split()).lower()


def definition_lines(reading: Reading) -> dict[str, int]:
    """The reference definitions of a source, by normalized label to the line index; the first of a label wins."""
    found: dict[str, int] = {}
    for n in range(reading.body_start, len(reading.lines)):
        if n in reading.fenced:
            continue
        masked = CODE_RUN.sub(lambda m: ' ' * len(m.group()), reading.lines[n].decode())
        m = DEFINITION.match(masked)
        if m and not m.group(1).startswith('^'):
            found.setdefault(label_of(m.group(1)), n)
    return found


BRACKETS = re.compile(r'\[((?:[^\[\]\\]|\\.)*)\]')


def used_labels(line: str, defined: Collection[str]) -> set[str]:
    """The defined labels a line's reference-style links and images use: `[text][label]`, `[label][]` and `[label]`."""
    used: set[str] = set()
    at = 0
    while m := BRACKETS.search(line, at):
        at = m.end()
        following = line[m.end() : m.end() + 1]
        if following == '(':
            continue
        label = m.group(1)
        if following == '[' and (second := BRACKETS.match(line, m.end())):
            at = second.end()
            label = second.group(1) or label
        if (found := label_of(label)) in defined:
            used.add(found)
    return used


def definition_patches(
    reading: Reading,
    path: str,
    made: list[Cut],
    mine: list[int],
    fix: Callable[[int, int, bytes], bytes],
    patches: Patches,
) -> None:
    """Reference definitions (C6 c). One used only by moved text moves with it, one used by moved text and by
    staying text stays and is copied to the end of each destination that uses it, one used by nothing stays."""
    defined = definition_lines(reading)
    if not defined:
        return
    owners: dict[int, list[int]] = {}
    for i in mine:
        for n in own_lines(made, i):
            owners.setdefault(n, []).append(i)
    top = [i for i in mine if made[i].parent is None]
    inside = {n for i in top for n in range(made[i].section.start, made[i].section.end)}
    kept = {
        s.start for i in top for s in reading.sections if contains(made[i].section, s)
    }
    where = {n: label for label, n in defined.items()}
    wanted: dict[str, set[int]] = {label: set() for label in defined}
    staying: set[str] = set()
    for n in range(reading.body_start, len(reading.lines)):
        if n in reading.fenced or n in where:
            continue
        masked = CODE_RUN.sub(lambda m: ' ' * len(m.group()), reading.lines[n].decode())
        for label in used_labels(masked, defined):
            wanted[label].update(owners.get(n, []))
            if n not in inside or n in kept:
                staying.add(label)
    for label, n in defined.items():
        home = set(owners.get(n, []))
        if home and label in staying:
            raise ContextError(
                f'{path}:{n + 1}: the definition [{label}] is inside moved text and used by staying text'
            )
        for i in sorted(wanted[label] - home):
            patches.tail.setdefault(i, []).append(
                fix(i, n, with_newline(reading.lines[n]))
            )
        if not home and wanted[label] and label not in staying:
            patches.drop.setdefault(path, set()).add(n)


def rebase(
    readings: dict[str, Reading],
    made: list[Cut],
    known: Collection[str],
    amap: dict[tuple[str, str], tuple[str, str]],
) -> Patches:
    """The edits that make the text each cut copies resolve from its destination."""
    anchored = {p: set(anchors(r.sections).values()) for p, r in readings.items()}
    patches = Patches({}, {}, {})
    for path, reading in readings.items():
        refs: dict[int, list[Ref]] = {}
        for ref in references(path, b''.join(reading.lines), known):
            refs.setdefault(ref.line - 1, []).append(ref)
        mine = [i for i, c in enumerate(made) if c.move.source == path]

        def fix(i: int, n: int, line: bytes, refs=refs) -> bytes:
            edits = []
            for ref in refs.get(n, []):
                new = rebased_target(ref, made[i].move.to, known, amap, anchored)
                if new is not None:
                    edits.append((ref.start, ref.end, new))
            return patched_line(line, edits) if edits else line

        for i in mine:
            for n in own_lines(made, i):
                line = reading.lines[n]
                if (fixed := fix(i, n, line)) != line:
                    patches.lines[(i, n)] = fixed
        definition_patches(reading, path, made, mine, fix, patches)
    return patches


def assemble(
    migration: Migration,
    readings: dict[str, Reading],
    made: list[Cut],
    patches: Patches,
    present: Collection[str],
) -> dict[str, bytes]:
    """The destinations and the indexes the cuts make, with `patches` applied to the text the cuts copy."""
    outputs: dict[str, bytes] = {}
    landed: dict[
        tuple[str, int], tuple[str, str]
    ] = {}  # (source, heading line) -> (destination, anchor)
    built: set[int] = set()

    def build(i: int) -> None:
        if i in built:
            return
        kids = {made[j].section.start: j for j, c in enumerate(made) if c.parent == i}
        for j in kids.values():
            build(j)
        cut = made[i]
        reading = readings[cut.move.source]
        sec = cut.section
        by = sec.level - 1
        headings = {s.start for s in reading.sections}

        def copied(n: int) -> bytes:
            return patches.lines.get((i, n), reading.lines[n])

        out: list[bytes] = []
        origin: list[
            int | None
        ] = []  # the source line of each output line; None when generated
        line = sec.start
        while line < sec.end:
            if line in kids:
                kid = made[kids[line]]
                anchor = landed[(kid.move.source, line)][1]
                out += [
                    shift_line(with_newline(copied(line)), by),
                    b'\n',
                    pointer(cut.move.to, kid.move.to, anchor),
                    b'\n',
                ]
                origin += [None] * 4
                line = kid.section.end
                continue
            out.append(
                shift_line(copied(line), by) if line in headings else copied(line)
            )
            origin.append(line)
            if line == sec.start and cut.move.requires:
                out[-1] = with_newline(out[-1])
                extra = [b'\n', read_first(migration, cut.move, present)]
                if line + 1 < sec.end and reading.lines[line + 1].strip():
                    extra.append(b'\n')
                out += extra
                origin += [None] * len(extra)
            line += 1
        data = b''.join(out)
        rendered = read(data, cut.move.to)
        slugs = anchors(rendered.sections)
        for s in rendered.sections:
            if (src := origin[s.start]) is not None:
                landed[(cut.move.source, src)] = (cut.move.to, slugs[s.start])
        if tail := patches.tail.get(i):
            data = with_newline(data)
            if data.splitlines()[-1].strip():
                data += b'\n'
            data += b''.join(tail)
        outputs[cut.move.to] = data
        built.add(i)

    for i in range(len(made)):
        build(i)

    for path, reading in readings.items():
        dropped = patches.drop.get(path, set())
        tops = sorted(
            (c.section.start, c.section.end)
            for c in made
            if c.move.source == path and c.parent is None
        )
        out: list[bytes] = []
        line = 0
        for start, end in tops:
            out += [
                x
                for n, x in enumerate(reading.lines[line:start], line)
                if n not in dropped
            ]
            for s in reading.sections:
                if start <= s.start < end:
                    dest, anchor = landed[(path, s.start)]
                    out += [
                        with_newline(reading.lines[s.start]),
                        b'\n',
                        pointer(path, dest, anchor),
                        b'\n',
                    ]
            line = end
        out += [x for n, x in enumerate(reading.lines[line:], line) if n not in dropped]
        if tops and tops[-1][1] == len(reading.lines) and out[-1:] == [b'\n']:
            out.pop()
        outputs[path] = b''.join(out)
    return outputs


def render(
    migration: Migration,
    base_files: dict[str, bytes],
    present: Collection[str] = (),
    files: Collection[str] = (),
) -> dict[str, bytes]:
    """The bytes of every file the moves write: each destination, and each source as an index. `present` is the
    paths at base among the destinations and the `requires` of the moves: a destination there is refused, a
    `requires` there is accepted. `files` is every file of the repo at base: a reference resolves only to one of
    them, to a file the migration reads, or to a destination, and one that does not is left as written. Pure:
    nothing is read or written."""
    check_requires(migration)
    sources = {mv.source for mv in migration.moves}
    readings = {p: read(d, p) for p, d in base_files.items() if p in sources}
    made = cuts(migration, {p: r.sections for p, r in readings.items()}, present)
    held = {*present, *base_files}
    first = assemble(migration, readings, made, Patches({}, {}, {}), held)
    amap = anchor_map(migration, base_files, first)
    known = {*files, *base_files, *present, *first}
    patches = rebase(readings, made, known, amap)
    outputs = assemble(migration, readings, made, patches, held)
    return outputs | rewritten(migration, base_files, outputs, known)


def rewritten(
    migration: Migration,
    base_files: dict[str, bytes],
    outputs: dict[str, bytes],
    known: Collection[str],
) -> dict[str, bytes]:
    """Each `[[rewrite]]` target's base bytes with `from` replaced by `to`. `from` must occur exactly `count` times
    (once by default) in the file as the earlier rewrites left it, and all of them are replaced;
    `to` must name a file the repo, the migration or its outputs hold, and the section of it that `to` names must
    exist in the output."""
    moved = {mv.source for mv in migration.moves} | {mv.to for mv in migration.moves}
    done: dict[str, bytes] = {}
    for r in migration.rewrites:
        if r.path in moved:
            raise ContextError(
                f'{r.path}: a rewrite target cannot also be a move source or destination'
            )
        if r.path not in base_files:
            raise ContextError(f'{r.path}: rewrite target is not among the base files')
        data = done.get(r.path, base_files[r.path])
        count = data.count(r.from_.encode())
        if count != r.count:
            raise ContextError(
                f'{r.path}: `from` {r.from_!r} occurs {count} times; it must occur exactly {r.count}'
            )
        target, fragment = split_target(r.to)
        ref = Ref(
            r.path,
            1,
            0,
            len(r.to),
            'inline' if r.path.endswith(MARKDOWN) else 'text',
            target,
            fragment,
            r.to,
        )
        found = resolve(ref, known)
        if found is None:
            raise ContextError(f'{r.path}: rewrite `to` {r.to!r} resolves to nothing')
        path, anchor = found
        if anchor and path in outputs and path.endswith(MARKDOWN):
            there = set(anchors(read(outputs[path], path).sections).values())
            if anchor not in there:
                raise ContextError(
                    f'{r.path}: rewrite `to` {r.to!r} names no heading of {path} after the move'
                )
        done[r.path] = data.replace(r.from_.encode(), r.to.encode())
    return done


# ---- unresolved ------------------------------------------------------------


def scanned(path: str) -> bool:
    """Whether the scanner reads a tracked file: a Markdown, TOML or text file that is not a migration."""
    return path.endswith(SCANNED) and not path.startswith(MIGRATIONS)


def read_blobs(repo: Path, commit: str, paths: Collection[str]) -> dict[str, bytes]:
    """The bytes of each of `paths` at `commit`, in one `git cat-file --batch`; anything that is not a blob is left out."""
    env = {k: v for k, v in os.environ.items() if k not in REPO_ENV}
    names = sorted(paths)
    if bad := next((n for n in names if '\n' in n), None):
        raise ContextError(f'{bad!r}: a path must not contain a newline')
    request = ''.join(f'{commit}:{p}\n' for p in names).encode()
    try:
        done = subprocess.run(
            ['git', '-C', str(repo), 'cat-file', '--batch'],
            input=request,
            env=env,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ContextError(f'git cat-file did not run: {e}') from e
    if done.returncode:
        raise ContextError(f'cannot read the files at {commit}')
    out: dict[str, bytes] = {}
    data, at = done.stdout, 0
    for name in names:
        end = data.index(b'\n', at)
        header = data[at:end].split()
        at = end + 1
        if len(header) == 3 and header[1] == b'blob':
            size = int(header[2])
            out[name] = data[at : at + size]
            at += size + 1
    return out


def sources(migration: Migration) -> set[str]:
    return {mv.source for mv in migration.moves}


def read_scanned(
    repo: Path, commit: str, files: Collection[str], also: Collection[str] = ()
) -> dict[str, bytes]:
    """Every file the scanner reads, from `files` (the repo's paths at `commit`), and the `also` paths that exist,
    as their bytes at that commit."""
    return read_blobs(repo, commit, [p for p in files if scanned(p) or (p in also)])


def written_target(ref: Ref) -> str:
    return ref.target + (f'#{ref.fragment}' if ref.fragment else '')


def line_offsets(text: str) -> list[int]:
    offsets, at = [], 0
    for line in text.split('\n'):
        offsets.append(at)
        at += len(line) + 1
    return offsets


def rewrite_spans(migration: Migration, path: str, text: str) -> list[tuple[int, int]]:
    """Where in `text` each `[[rewrite]]` of `path` changes it: the span of each occurrence of its `from`, when it
    occurs exactly `count` times."""
    spans = []
    for r in migration.rewrites:
        if r.path == path and text.count(r.from_) == r.count:
            at = -1
            for _ in range(r.count):
                at = text.index(r.from_, at + len(r.from_) if at >= 0 else 0)
                spans.append((at, at + len(r.from_)))
    return spans


def unresolved(
    migration: Migration, base_files: dict[str, bytes], files: Collection[str]
) -> list[Ref]:
    """Every reference, in a Markdown, TOML or text file of `base_files` and outside the moved text, whose target is
    a moved section and that no `[[rewrite]]` or `[[keep]]` covers. A rewrite covers the references its `from` span
    holds; a keep covers the references of its file whose written target is its `ref`. Ordered by path and line."""
    mine = {f.path: base_files[f.path] for f in migration.files if f.path in base_files}
    amap = anchor_map(migration, mine, render(migration, mine, (), files))
    made = cuts(
        migration,
        {p: read(d, p).sections for p, d in mine.items() if p in sources(migration)},
    )
    moved: dict[str, list[tuple[int, int]]] = {}
    for c in made:
        moved.setdefault(c.move.source, []).append((c.section.start, c.section.end))
    found: list[Ref] = []
    for path in sorted(base_files):
        if not scanned(path):
            continue
        data = base_files[path]
        try:
            text = data.decode()
        except UnicodeDecodeError:
            continue
        offsets = line_offsets(text)
        spans = rewrite_spans(migration, path, text)
        kept = {k.ref for k in migration.keeps if k.path == path}
        for ref in references(path, data, files):
            hit = resolve(ref, files)
            if hit is None or hit not in amap:
                continue
            if any(a <= ref.line - 1 < b for a, b in moved.get(path, [])):
                continue
            where = offsets[ref.line - 1]
            if written_target(ref) in kept or any(
                a <= where + ref.start and where + ref.end <= b for a, b in spans
            ):
                continue
            found.append(ref)
    return sorted(found, key=lambda r: (r.path, r.line, r.start))


def plan_unresolved(repo: Path, migration: Migration) -> list[Ref]:
    """`unresolved` over the tracked files of the repo at the migration's base commit."""
    commit = resolve_base(repo, migration)
    files = repo_files(repo, migration)
    declared = [f.path for f in migration.files]
    return unresolved(migration, read_scanned(repo, commit, files, declared), files)


# ---- proof -----------------------------------------------------------------
#
# `prove` accounts for every line of every planned output against the migration's base, without asking the writers
# (`assemble`, `rebase`, `rewritten`, `render`, `anchor_map`) what they wrote. It uses the readers only: `read`,
# `cuts`, `find`, `anchors`, `references`, `resolve`, `lines_of`. The walk follows the section line ranges the reader
# gives at base, in cut order; it never finds an output line by searching for its content, except to say what went
# wrong once a line has already failed.


@dataclass(frozen=True)
class Original:
    """An output line that is a source line (1-based) in its place. `edit` is `none`, or the edits it carries joined
    by `+` in this order: `shift` (a heading's `#` count moved by its cut), `target` (a reference's target changed),
    `newline` (a final newline added where the source line had none)."""

    source: str
    line: int
    edit: str


@dataclass(frozen=True)
class Generated:
    """An output line no source line stands behind: `read-first`, `read-first-blank`, `pointer`, `kept-heading` (a
    heading line an index keeps), `nested-heading` (a nested move's heading in its parent), `stub-blank` and
    `tail-blank`. `source` and `line` name the source heading it stands for, when it stands for one."""

    kind: str
    source: str = ''
    line: int = 0


@dataclass(frozen=True)
class Copied:
    """A reference definition appended to a destination, copied from the source line (1-based)."""

    source: str
    line: int


Origin = Original | Generated | Copied


@dataclass(frozen=True)
class Finding:
    """One thing `prove` could not account for. `line` is the output line, 1-based; 0 when the finding is about the
    whole file."""

    path: str
    line: int
    kind: str  # absent, dropped, reordered, duplicated, unexplained, rewrite
    message: str


@dataclass(frozen=True)
class Block:
    """One cut: its lines of the source at base (1-based, inclusive) and the destination it became."""

    source: str
    start: int
    end: int
    dest: str
    lines: int  # the destination's line count, 0 when it is absent


@dataclass(frozen=True)
class Consumer:
    """A file outside the move that names a migrated one: a kitchen.toml reference or an overlay part. `ok` is
    whether what it names is still the content; `detail` is the failure or, for an overlay part, the `reviewed`
    digest it had and the one its selection has now."""

    path: str
    what: str
    target: str
    ok: bool
    detail: str


@dataclass
class Proof:
    origins: dict[str, list[Origin]]  # every planned output, one origin per line
    blocks: list[Block]
    findings: list[Finding]
    pre_existing: list[Finding] = field(
        default_factory=list
    )  # references already broken at base: listed, not counted
    consumers: list[Consumer] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


EDITS = ('shift', 'target', 'newline')


def ref_spans(path: str, data: bytes) -> dict[int, list[tuple[int, int]]]:
    """The target columns of every reference `data` makes, by 0-based line."""
    found: dict[int, list[tuple[int, int]]] = {}
    for ref in references(path, data):
        found.setdefault(ref.line - 1, []).append((ref.start, ref.end))
    return found


def skeleton(body: bytes, spans: list[tuple[int, int]]) -> str | None:
    """A line without its reference targets, a NUL where each was; None when it is not UTF-8."""
    try:
        text = body.decode()
    except UnicodeDecodeError:
        return None
    pieces, at = [], 0
    for a, b in sorted(spans):
        pieces.append(text[at:a])
        at = b
    pieces.append(text[at:])
    return '\0'.join(pieces)


def line_edit(
    base: bytes,
    out: bytes,
    by: int,
    base_spans: list[tuple[int, int]],
    out_spans: list[tuple[int, int]],
    newline_ok: bool,
) -> str | None:
    """How `out` differs from the source line `base` once its heading is shifted by `by`: `none` or the edits
    joined by `+`; None when it differs in any other way."""
    edits: list[str] = []
    ends = base.endswith(b'\n'), out.endswith(b'\n')
    if ends[0] != ends[1]:
        if not (newline_ok and ends[1]):
            return None
        edits.append('newline')
    want = base.removesuffix(b'\n')
    got = out.removesuffix(b'\n')
    if by and want.startswith(b'#'):
        want = shift_line(want, by)
        edits.append('shift')
    if want != got:
        a = skeleton(base.removesuffix(b'\n'), base_spans)
        b = skeleton(got, out_spans)
        if a is None or b is None:
            return None
        if by and a.startswith('#'):
            a = shift_line(a.encode(), by).decode()
        if a != b:
            return None
        edits.append('target')
    return '+'.join(e for e in EDITS if e in edits) or 'none'


@dataclass
class Slot:
    """What the walk expects at the next output line: `match` returns the line's origin or None."""

    label: str
    match: Callable[[int, bytes], Origin | None]
    optional: bool = False
    original: int | None = None  # the source line this slot accounts for (0-based)


def exact(label: str, want: bytes, origin: Origin, optional: bool = False) -> Slot:
    return Slot(label, lambda i, line: origin if line == want else None, optional)


def walk(
    path: str, lines: list[bytes], slots: list[Slot], found: list[Finding]
) -> tuple[list[Origin], int, bool, list[Slot]]:
    """Match `lines` against `slots` in order. Returns the origins, the index of the first output line no slot
    claimed, whether every slot was consumed, and the optional slots that were absent. A line that fails is reported
    as dropped (a later slot takes it), reordered (a dropped slot takes it), duplicated (the slot just taken takes it
    again) or unexplained; the search that tells them apart runs only after a failure."""
    origins: list[Origin] = []
    absent: list[Slot] = []
    lost: list[Slot] = []
    i = k = 0
    while k < len(slots) and i < len(lines):
        slot = slots[k]
        got = slot.match(i, lines[i])
        if got is not None:
            origins.append(got)
            i += 1
            k += 1
            continue
        if slot.optional:
            absent.append(slot)
            k += 1
            continue
        ahead = next(
            (j for j in range(k + 1, len(slots)) if slots[j].match(i, lines[i])), None
        )
        if ahead is not None:
            names = ', '.join(s.label for s in slots[k:ahead] if not s.optional)
            found.append(
                Finding(path, i + 1, 'dropped', f'missing before this line: {names}')
            )
            lost += [s for s in slots[k:ahead] if not s.optional]
            k = ahead
            continue
        again = next((s for s in lost if s.match(i, lines[i])), None)
        kind, why = 'unexplained', f'expected {slot.label}'
        if again is not None:
            kind, why = 'reordered', f'{again.label} was expected earlier'
        elif k and slots[k - 1].match(i, lines[i]):
            kind, why = 'duplicated', f'repeats {slots[k - 1].label}'
        found.append(Finding(path, i + 1, kind, why))
        origins.append(Generated(kind))
        if (
            kind == 'unexplained'
            and i + 1 < len(lines)
            and slots[k + 1 :]
            and slots[k + 1].match(i + 1, lines[i + 1])
        ):
            k += 1  # the line stands where the slot's own line should be
        i += 1
    while k >= len(slots) and i < len(lines) and lost:
        again = next((s for s in lost if s.match(i, lines[i])), None)
        if again is None:
            break
        found.append(
            Finding(path, i + 1, 'reordered', f'{again.label} was expected earlier')
        )
        origins.append(Generated('reordered'))
        i += 1
    if k < len(slots):
        rest = [s for s in slots[k:] if not s.optional]
        absent += [s for s in slots[k:] if s.optional]
        if rest:
            names = ', '.join(s.label for s in rest)
            found.append(
                Finding(path, len(lines) + 1, 'dropped', f'missing at the end: {names}')
            )
    return origins, i, k >= len(slots), absent


POINTER = re.compile(rb'Moved to \[([^\]]+)\]\(([^)#]*)#([^)]*)\)\.\n')


def original_slot(
    source: str,
    n: int,
    base: bytes,
    by: int,
    base_spans: dict[int, list[tuple[int, int]]],
    out_spans: dict[int, list[tuple[int, int]]],
    newline_ok: bool,
    optional: bool = False,
) -> Slot:
    def match(i: int, line: bytes) -> Origin | None:
        edit = line_edit(
            base, line, by, base_spans.get(n, []), out_spans.get(i, []), newline_ok
        )
        return None if edit is None else Original(source, n + 1, edit)

    return Slot(f'{source}:{n + 1}', match, optional, n)


def pointer_slot(source: str, n: int, from_path: str, dest: str) -> Slot:
    def match(i: int, line: bytes) -> Origin | None:
        m = POINTER.fullmatch(line)
        if (
            m
            and m.group(1) == dest.encode()
            and m.group(2) == relative(from_path, dest).encode()
        ):
            return Generated('pointer', source, n + 1)
        return None

    return Slot(f'the pointer for {source}:{n + 1} to {dest}', match)


def blank_slot(kind: str = 'stub-blank', optional: bool = False) -> Slot:
    return exact(f'a blank line ({kind})', b'\n', Generated(kind), optional)


def owner(made: list[Cut], source: str, n: int) -> Cut:
    """The innermost cut of `source` that holds line `n`."""
    return max(
        (
            c
            for c in made
            if c.move.source == source and c.section.start <= n < c.section.end
        ),
        key=lambda c: c.section.start,
    )


def destination_slots(
    migration: Migration,
    made: list[Cut],
    readings: dict[str, Reading],
    i: int,
    base_spans: dict[int, list[tuple[int, int]]],
    out_spans: dict[int, list[tuple[int, int]]],
) -> list[Slot]:
    """What cut `i`'s destination holds, in order: its own lines, a `Read first:` block after the heading when the
    move names prerequisites, and for each nested cut a heading, a pointer and the blanks around them."""
    cut = made[i]
    source = cut.move.source
    reading = readings[source]
    sec = cut.section
    by = sec.level - 1
    headings = {s.start for s in reading.sections}
    kids = {made[j].section.start: j for j, c in enumerate(made) if c.parent == i}
    own = [
        n
        for n in range(sec.start, sec.end)
        if n in kids or not any(k < n < made[j].section.end for k, j in kids.items())
    ]
    last = max((n for n in own if n not in kids), default=sec.start)
    slots: list[Slot] = []
    n = sec.start
    while n < sec.end:
        if n in kids:
            kid = made[kids[n]]

            def nested(i: int, line: bytes, n: int = n) -> Origin | None:
                edit = line_edit(
                    reading.lines[n],
                    line,
                    by,
                    base_spans.get(n, []),
                    out_spans.get(i, []),
                    True,
                )
                return (
                    None if edit is None else Generated('nested-heading', source, n + 1)
                )

            slots += [
                Slot(
                    f'the heading of the nested move {kid.move.to} ({source}:{n + 1})',
                    nested,
                ),
                blank_slot(),
                pointer_slot(source, n, cut.move.to, kid.move.to),
                blank_slot(),
            ]
            n = kid.section.end
            continue
        first = n == sec.start and bool(cut.move.requires)
        slots.append(
            original_slot(
                source,
                n,
                reading.lines[n],
                by if n in headings else 0,
                base_spans,
                out_spans,
                first or n == last,
            )
        )
        if first:
            links = ', '.join(
                f'[{p}]({relative(cut.move.to, p)})' for p in cut.move.requires
            )
            slots += [
                blank_slot('read-first-blank'),
                exact(
                    'the Read first line',
                    f'Read first: {links}.\n'.encode(),
                    Generated('read-first'),
                ),
            ]
            if n + 1 < sec.end and reading.lines[n + 1].strip():
                slots.append(blank_slot('read-first-blank'))
        n += 1
    return slots


def index_slots(
    source: str,
    reading: Reading,
    made: list[Cut],
    base_spans: dict[int, list[tuple[int, int]]],
    out_spans: dict[int, list[tuple[int, int]]],
) -> list[Slot]:
    """What a source holds once its moved sections are cut out: every line outside them, and for each heading in
    them the heading line, a pointer to where it went and the blanks around them. A definition line may be absent."""
    tops = sorted(
        (c for c in made if c.move.source == source and c.parent is None),
        key=lambda c: c.section.start,
    )
    definitions = set(definition_lines(reading).values())
    slots: list[Slot] = []

    def outside(a: int, b: int) -> None:
        for n in range(a, b):
            slots.append(
                original_slot(
                    source,
                    n,
                    reading.lines[n],
                    0,
                    base_spans,
                    out_spans,
                    False,
                    n in definitions,
                )
            )

    at = 0
    for top in tops:
        outside(at, top.section.start)
        for s in reading.sections:
            if top.section.start <= s.start < top.section.end:
                kept = with_newline(reading.lines[s.start])
                slots += [
                    exact(
                        f'the heading of {source}:{s.start + 1} kept in the index',
                        kept,
                        Generated('kept-heading', source, s.start + 1),
                    ),
                    blank_slot(),
                    pointer_slot(
                        source, s.start, source, owner(made, source, s.start).move.to
                    ),
                    blank_slot(),
                ]
        at = top.section.end
    outside(at, len(reading.lines))
    if tops and at == len(reading.lines):
        slots[-1].optional = True
    return slots


def tail_origins(
    path: str,
    lines: list[bytes],
    at: int,
    source: str,
    reading: Reading,
    base_spans: dict[int, list[tuple[int, int]]],
    out_spans: dict[int, list[tuple[int, int]]],
    found: list[Finding],
    held: Collection[int] = (),
) -> list[Origin]:
    """The lines of a destination after its last own line: a blank when the text before is not blank, then the
    reference definitions copied from the source."""
    defined = definition_lines(reading)
    origins: list[Origin] = []
    for i in range(at, len(lines)):
        line = lines[i]
        if i == at and line == b'\n' and at and lines[at - 1].strip():
            origins.append(Generated('tail-blank'))
            continue
        masked = CODE_RUN.sub(
            lambda m: ' ' * len(m.group()), line.decode(errors='replace')
        )
        m = DEFINITION.match(masked)
        n = defined.get(label_of(m.group(1))) if m else None
        edit = (
            None
            if n is None
            else line_edit(
                reading.lines[n],
                line,
                0,
                base_spans.get(n, []),
                out_spans.get(i, []),
                True,
            )
        )
        if n is None or edit is None:
            found.append(
                Finding(
                    path,
                    i + 1,
                    'unexplained',
                    'a line after the destination text that is no copied definition',
                )
            )
            origins.append(Generated('unexplained'))
        elif n in held or any(
            isinstance(o, Copied) and o.line == n + 1 for o in origins
        ):
            found.append(
                Finding(
                    path,
                    i + 1,
                    'duplicated',
                    f'the definition at {source}:{n + 1} is already in this destination',
                )
            )
            origins.append(Generated('duplicated'))
        else:
            origins.append(Copied(source, n + 1))
    return origins


def unused_copies(
    lines: list[bytes], origins: list[Origin], reading: Reading
) -> list[tuple[int, str]]:
    """The copied definitions of a destination whose label no other line of it uses: (line index, label)."""
    defined = definition_lines(reading)
    used: set[str] = set()
    for line in lines:
        masked = CODE_RUN.sub(
            lambda m: ' ' * len(m.group()), line.decode(errors='replace')
        )
        if not DEFINITION.match(masked):
            used |= used_labels(masked, defined)
    out = []
    for i, o in enumerate(origins):
        if isinstance(o, Copied):
            label = next(k for k, n in defined.items() if n == o.line - 1)
            if label not in used:
                out.append((i, label))
    return out


def unaccounted(
    source: str,
    reading: Reading,
    paths: list[str],
    origins: dict[str, list[Origin]],
    gone: set[int],
) -> list[Finding]:
    """C6's second count: every line of `source` is an Original exactly once across its index and destinations,
    but for a definition line that left the index."""
    seen: dict[int, int] = {}
    for path in paths:
        for o in origins.get(path, []):
            if isinstance(o, Original) and o.source == source:
                seen[o.line - 1] = seen.get(o.line - 1, 0) + 1
    found = []
    for n in range(len(reading.lines)):
        count = seen.get(n, 0)
        if count == 0 and n not in gone:
            found.append(
                Finding(source, 0, 'dropped', f'{source}:{n + 1} appears in no output')
            )
        elif count > 1:
            found.append(
                Finding(
                    source, 0, 'duplicated', f'{source}:{n + 1} appears {count} times'
                )
            )
    return found


def rewrite_origins(
    path: str,
    base: bytes,
    out: bytes,
    migration: Migration,
    found: list[Finding],
) -> list[Origin]:
    """A rewrite target is its base bytes with each `from` replaced by its `to`, each `from` occurring `count`
    times, and nothing else."""
    want = base
    for r in migration.rewrites:
        if r.path != path:
            continue
        count = want.count(r.from_.encode())
        if count != r.count:
            found.append(
                Finding(
                    path,
                    0,
                    'rewrite',
                    f'`from` {r.from_!r} occurs {count} times in the base; the migration says {r.count}',
                )
            )
            return []
        want = want.replace(r.from_.encode(), r.to.encode())
    origins: list[Origin] = []
    have, expect = lines_of(out), lines_of(want)
    original = lines_of(base)
    for i, line in enumerate(have):
        if i < len(expect) and line == expect[i]:
            same = i < len(original) and line == original[i]
            origins.append(Original(path, i + 1, 'none' if same else 'target'))
            continue
        found.append(
            Finding(
                path,
                i + 1,
                'unexplained',
                "differs from the base with the migration's rewrites applied",
            )
        )
        return origins
    if len(have) < len(expect):
        found.append(
            Finding(
                path,
                len(have) + 1,
                'dropped',
                'the file ends before the rewritten base does',
            )
        )
    return origins


class Tree:
    """One side of a migration, base or output: the bytes of each file, the references each makes and the heading
    anchors of each Markdown file, read when first asked for."""

    def __init__(self, data: dict[str, bytes | None], files: Collection[str]):
        self.data = data
        self.files = frozenset(files)
        self.refs_cache: dict[str, dict[int, list[Ref]]] = {}
        self.anchor_cache: dict[str, dict[int, str] | None] = {}

    def refs(self, path: str) -> dict[int, list[Ref]]:
        """The references of a file by 0-based line."""
        if path not in self.refs_cache:
            found: dict[int, list[Ref]] = {}
            data = self.data.get(path)
            if data is not None and scanned(path):
                for ref in references(path, data, self.files):
                    found.setdefault(ref.line - 1, []).append(ref)
            self.refs_cache[path] = found
        return self.refs_cache[path]

    def heading_anchors(self, path: str) -> dict[int, str] | None:
        """The anchor of each heading of a Markdown file by line index; None for a file that is not Markdown, is
        absent or cannot be read."""
        if path not in self.anchor_cache:
            data = self.data.get(path)
            found = None
            if data is not None and path.endswith(MARKDOWN):
                try:
                    found = anchors(read(data, path).sections)
                except ContextError:
                    found = None
            self.anchor_cache[path] = found
        return self.anchor_cache[path]

    def resolved(self, ref: Ref) -> tuple[str, str] | None:
        """What a reference names: its file and anchor, when both exist here."""
        found = resolve(ref, self.files)
        if found is None:
            return None
        known = self.heading_anchors(found[0])
        if found[1] and known is not None and found[1] not in set(known.values()):
            return None
        return found


def heading_text(reading: Reading, n: int) -> str:
    return next((s.plain for s in reading.sections if s.start == n), '')


def link_proof(
    base: Tree,
    out: Tree,
    migration: Migration,
    readings: dict[str, Reading],
    made: list[Cut],
    origins: dict[str, list[Origin]],
    skip: Collection[str],
) -> tuple[list[Finding], list[Finding], dict[tuple[str, str], tuple[str, str]]]:
    """C8's anchors and the references, by (path, anchor): every heading of a source keeps its anchor in its index,
    every moved heading lands on a heading of its destination, every pointer names that heading, and a reference
    that resolved at base resolves after to the heading the walk mapped it to (the same place, for text that stays).
    Heading text appears in messages only. Returns the findings and the references already broken at base."""
    found: list[Finding] = []
    broken: list[Finding] = []
    old: dict[tuple[str, int], str] = {}
    for source, reading in readings.items():
        table = base.heading_anchors(source) or {}
        for s in reading.sections:
            old[(source, s.start)] = table.get(s.start, '')
    landed: dict[tuple[str, int], tuple[str, str]] = {}
    moved: dict[tuple[str, str], tuple[str, str]] = {}
    for source, reading in readings.items():
        index = out.heading_anchors(source) or {}
        for j, o in enumerate(origins.get(source, [])):
            kept = isinstance(o, Original) or (
                isinstance(o, Generated) and o.kind == 'kept-heading'
            )
            if not kept or o.source != source or (source, o.line - 1) not in old:
                continue
            n = o.line - 1
            if index.get(j) != old[(source, n)]:
                found.append(
                    Finding(
                        source,
                        j + 1,
                        'anchor',
                        f'the heading {heading_text(reading, n)!r} (#{old[(source, n)]}) is #{index.get(j)} in the index',
                    )
                )
    for cut in made:
        dest = cut.move.to
        table = out.heading_anchors(dest)
        for j, o in enumerate(origins.get(dest, [])):
            if not (isinstance(o, Original) and o.source == cut.move.source):
                continue
            key = (o.source, o.line - 1)
            if key not in old:
                continue
            anchor = None if table is None else table.get(j)
            if anchor is None:
                found.append(
                    Finding(
                        dest,
                        j + 1,
                        'anchor',
                        f'the heading {heading_text(readings[o.source], o.line - 1)!r} of {o.source}:{o.line} is no heading here',
                    )
                )
                continue
            landed[key] = (dest, anchor)
            moved[(o.source, old[key])] = (dest, anchor)

    def expected(ref: Ref, mode: str) -> tuple[str, str] | None:
        here = base.resolved(ref)
        if here is None:
            return None
        if mode == 'rewrite' and not here[1]:
            return here  # the file a rewrite names without a heading is the author's to choose
        return moved.get(here, here) if mode != 'stay' and here[1] else here

    def pair(path: str, j: int, source: str, n: int, mode: str) -> None:
        was = base.refs(source).get(n, [])
        now = out.refs(path).get(j, [])
        if len(was) != len(now):
            found.append(
                Finding(
                    path,
                    j + 1,
                    'link',
                    f'holds {len(now)} references; {source}:{n + 1} holds {len(was)}',
                )
            )
            return
        for a, b in zip(was, now, strict=True):
            want = expected(a, mode)
            if want is None:
                broken.append(
                    Finding(
                        source,
                        n + 1,
                        'link',
                        f'{" ".join(a.raw.split())} (broken at base)',
                    )
                )
                continue
            got = out.resolved(b)
            if mode == 'rewrite' and not want[1]:
                want = got if got is not None else want
            if got != want:
                found.append(
                    Finding(
                        path,
                        j + 1,
                        'link',
                        f'{" ".join(b.raw.split())} resolves to {got and "#".join(x for x in got if x)}; '
                        f'{" ".join(a.raw.split())} at {source}:{n + 1} is expected to land on {"#".join(x for x in want if x)}',
                    )
                )

    for path in sorted(origins):
        lines = lines_of(out.data[path] or b'')
        for j, o in enumerate(origins[path]):
            moved_text = path in {c.move.to for c in made}
            if isinstance(o, Original):
                rewritten = 'target' in o.edit.split('+')
                mode = 'moved' if moved_text else 'rewrite' if rewritten else 'stay'
                pair(path, j, o.source, o.line - 1, mode)
            elif isinstance(o, Copied):
                pair(path, j, o.source, o.line - 1, 'moved')
            elif isinstance(o, Generated) and o.kind == 'nested-heading':
                pair(path, j, o.source, o.line - 1, 'moved')
            elif isinstance(o, Generated) and o.kind == 'kept-heading':
                pair(path, j, o.source, o.line - 1, 'stay')
            elif isinstance(o, Generated) and o.kind == 'pointer':
                m = POINTER.fullmatch(lines[j])
                want = landed.get((o.source, o.line - 1))
                if m is None or want is None:
                    found.append(
                        Finding(
                            path,
                            j + 1,
                            'link',
                            f'a pointer for {o.source}:{o.line}, whose heading landed nowhere',
                        )
                    )
                elif m.group(3).decode() != want[1]:
                    found.append(
                        Finding(
                            path,
                            j + 1,
                            'link',
                            f'points at #{m.group(3).decode()}; {o.source}:{o.line} is #{want[1]} in {want[0]}',
                        )
                    )
            elif isinstance(o, Generated) and o.kind == 'read-first':
                for ref in out.refs(path).get(j, []):
                    if out.resolved(ref) is None:
                        found.append(
                            Finding(
                                path, j + 1, 'link', f'{ref.raw} resolves to no file'
                            )
                        )
    for path in sorted(base.data):
        if path in skip or not scanned(path):
            continue
        for n, refs in sorted(base.refs(path).items()):
            for ref in refs:
                if base.resolved(ref) is None:
                    broken.append(
                        Finding(
                            path,
                            n + 1,
                            'link',
                            f'{" ".join(ref.raw.split())} (broken at base)',
                        )
                    )
                elif out.resolved(ref) != base.resolved(ref):
                    found.append(
                        Finding(
                            path,
                            n + 1,
                            'link',
                            f'{" ".join(ref.raw.split())} no longer resolves',
                        )
                    )
    return found, broken, moved


STUBS = ('kept-heading', 'nested-heading')
KITCHEN_TOML = '.agents/kitchen.toml'
OVERLAYS = '.agents/playbooks/'


def keyed_parts(data: dict) -> tuple[dict[tuple, dict], list[object]]:
    """An overlay's parts by (id) or, with no id, by position among the id-less ones; the ids that repeat are
    returned apart and keep no part."""
    keyed: dict[tuple, dict] = {}
    repeated: list[object] = []
    anon = 0
    for part in data.get('part', []):
        if not isinstance(part, dict):
            continue
        pid = part.get('id')
        if pid is None:
            key = ('', anon)
            anon += 1
        else:
            key = (pid,)
        if key in keyed:
            if pid not in repeated:
                repeated.append(pid)
        else:
            keyed[key] = part
    for pid in repeated:
        del keyed[(pid,)]
    return keyed, repeated


def consumer_proof(
    base: Tree,
    out: Tree,
    consumers: Collection[str],
    origins: dict[str, list[Origin]],
    moved: dict[tuple[str, str], tuple[str, str]],
) -> tuple[list[Finding], list[Consumer], list[Finding]]:
    """C10: what the repo's own configuration names must still be the content. A kitchen.toml reference to a moved
    heading by its old anchor lands on the index pointer. An overlay part with a repo-root source that is a migrated
    file resolves with the playbooks' `select` on the output: selected in a destination or a staying section it
    holds, selected on a heading an index or a parent only points from, or on none or several, it fails. Each part
    that holds prints the digest its selection has now beside the one it was reviewed at. Every `path` or `path#anchor`
    string of kitchen.toml or an overlay is judged the same way. What resolved at base and no longer does fails, as
    does a string that is new in the working tree and names nothing ("resolves to nothing"). What was already broken
    at base is listed, not failed: a string that did not resolve at base, or a part whose base copy (found by its id,
    or by its position among the id-less parts; a repeated id keeps none and is a finding) has the same source and
    select and matched no single part there. A part with no base copy, or whose source or select changed, is judged
    on the output tree."""
    pb = playbooks_module()
    found: list[Finding] = []
    seen: list[Consumer] = []
    broken: list[Finding] = []

    def fail(path: str, line: int, what: str, target: str, why: str) -> None:
        found.append(Finding(path, line, 'consumer', f'{what}: {why}'))
        seen.append(Consumer(path, what, target, False, why))

    for path in sorted(consumers):
        overlay = path.startswith(OVERLAYS) and path.endswith('.toml')
        if path == KITCHEN_TOML or overlay:
            at_base = {
                written_target(r): base.resolved(r) is not None
                for refs in base.refs(path).values()
                for r in refs
            }
            for n, refs in sorted(out.refs(path).items()):
                for ref in refs:
                    now = out.resolved(ref)
                    what = f'line {n + 1}'
                    target = written_target(ref)
                    if now is None:
                        if at_base.get(target) is False:
                            broken.append(
                                Finding(
                                    path, n + 1, 'link', f'{target} (broken at base)'
                                )
                            )
                        else:
                            fail(
                                path,
                                n + 1,
                                what,
                                target,
                                f'{target} no longer resolves'
                                if at_base.get(target)
                                else f'{target} resolves to nothing',
                            )
                        continue
                    if now[0] not in origins:
                        continue
                    if now in moved:
                        fail(
                            path,
                            n + 1,
                            what,
                            target,
                            f'{target} lands on the pointer {now[0]} keeps for a section that moved to {moved[now][0]}',
                        )
                    elif not overlay:
                        seen.append(Consumer(path, what, target, True, ''))
        if overlay:
            try:
                data = tomllib.loads((out.data.get(path) or b'').decode())
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
                fail(path, 0, 'overlay', '', f'cannot read it: {e}')
                continue
            try:
                base_data = tomllib.loads((base.data.get(path) or b'').decode())
            except (tomllib.TOMLDecodeError, UnicodeDecodeError):
                base_data = {}
            base_parts, _ = keyed_parts(base_data)
            keyed, repeated = keyed_parts(data)
            for pid in repeated:
                fail(path, 0, f'part {pid}', '', 'duplicate part id')
            for key, part in keyed.items():
                source = part.get('source')
                target = source.get('path') if isinstance(source, dict) else None
                if (
                    not isinstance(source, dict)
                    or source.get('root') != 'repo'
                    or target not in origins
                ):
                    continue
                what = f'part {part.get("id")}'
                selector = str(part.get('select'))
                was = base_parts.get(key)
                was_source = was.get('source') if was else None
                was_target = (
                    was_source.get('path') if isinstance(was_source, dict) else None
                )
                was_select = str(was.get('select')) if was else None
                if (
                    was_target == target
                    and was_select == selector
                    and target in base.data
                ):
                    try:
                        pb.select(
                            pb.parse((base.data[target] or b'').decode()), selector
                        )
                    except pb.PlaybookError:
                        broken.append(
                            Finding(
                                path, 0, 'link', f'{target} {selector} (broken at base)'
                            )
                        )
                        continue
                try:
                    doc = pb.parse((out.data.get(target) or b'').decode())
                    (start, end) = pb.select(doc, selector)[0]
                except pb.PlaybookError as e:
                    fail(path, 0, what, f'{target} {selector}', str(e))
                    continue
                at = origins[target][start] if start < len(origins[target]) else None
                if isinstance(at, Generated) and at.kind in STUBS:
                    fail(
                        path,
                        0,
                        what,
                        f'{target} {selector}',
                        f'{selector} selects a heading of {target} that only points to where its section moved',
                    )
                    continue
                now = pb.digest(pb.text_of(doc, [(start, end)]))
                seen.append(
                    Consumer(
                        path,
                        what,
                        f'{target} {selector}',
                        True,
                        f'reviewed {part.get("reviewed")}, now {now}',
                    )
                )
    return found, seen, broken


def prove(
    base_files: dict[str, bytes],
    migration: Migration,
    outputs: dict[str, bytes | None],
    files: Collection[str] = (),
    consumers: dict[str, bytes] | None = None,
) -> Proof:
    """Account for every line of every planned output (None for one that is absent) against the base: lines, then
    anchors and references, then C10's consumers. `files` is every path of the repo at base; the paths of
    `base_files` and `outputs` are always in it. `consumers` holds the bytes of the kitchen.toml and overlays to
    judge (the working tree's for `check`, the base's for apply's proof in memory); a consumer the migration writes
    is judged as the output. Pure."""
    sources = sorted({mv.source for mv in migration.moves})
    for path in sources:
        if path not in base_files:
            raise ContextError(f'{path}: not among the base files')
    for path in sorted({r.path for r in migration.rewrites}):
        if path not in base_files:
            raise ContextError(f'{path}: rewrite target is not among the base files')
    for path in sorted(files):
        if scanned(path) and path not in base_files:
            raise ContextError(
                f'{path}: a scanned file of the repo has no bytes among the base files'
            )
    readings = {p: read(base_files[p], p) for p in sources}
    made = cuts(migration, {p: r.sections for p, r in readings.items()})
    base_spans = {p: ref_spans(p, base_files[p]) for p in sources}
    found: list[Finding] = []
    origins: dict[str, list[Origin]] = {}
    copied: dict[str, set[int]] = {p: set() for p in sources}
    dropped: dict[str, set[int]] = {p: set() for p in sources}
    clean: dict[str, bool] = {}

    def lines_for(path: str) -> list[bytes] | None:
        data = outputs.get(path)
        if data is None:
            found.append(Finding(path, 0, 'absent', 'the planned file is absent'))
            return None
        return lines_of(data)

    for i, cut in enumerate(made):
        path = cut.move.to
        lines = lines_for(path)
        if lines is None:
            continue
        source = cut.move.source
        spans = ref_spans(path, b''.join(lines))
        before = len(found)
        slots = destination_slots(
            migration, made, readings, i, base_spans[source], spans
        )
        got, at, done, _ = walk(path, lines, slots, found)
        if done:
            got += tail_origins(
                path,
                lines,
                at,
                source,
                readings[source],
                base_spans[source],
                spans,
                found,
                {o.line - 1 for o in got if isinstance(o, Original)},
            )
        else:
            got += [Generated('unexplained')] * (len(lines) - at)
        origins[path] = got
        # render adds a final newline only where text follows; D2 would also allow one at the end, this refuses it
        if (
            got
            and isinstance(got[-1], Original)
            and 'newline' in got[-1].edit.split('+')
        ):
            found.append(
                Finding(
                    path,
                    len(lines),
                    'unexplained',
                    'ends in a newline the source line lacks, with nothing after it',
                )
            )
        copied[source] |= {o.line - 1 for o in got if isinstance(o, Copied)}
        if any(isinstance(o, Copied) for o in got):
            unused = unused_copies(lines, got, readings[source])
            found += [
                Finding(
                    path,
                    n + 1,
                    'unexplained',
                    f'copies the definition [{label}], which the text never uses',
                )
                for n, label in unused
            ]
        clean[path] = len(found) == before
    for source in sources:
        lines = lines_for(source)
        if lines is None:
            continue
        spans = ref_spans(source, b''.join(lines))
        before = len(found)
        slots = index_slots(source, readings[source], made, base_spans[source], spans)
        got, at, done, gone = walk(source, lines, slots, found)
        if done and at < len(lines):
            found.append(
                Finding(
                    source, at + 1, 'unexplained', 'a line after the end of the source'
                )
            )
        origins[source] = got + [Generated('unexplained')] * (len(lines) - at)
        for slot in gone:
            if slot.original is not None:
                dropped[source].add(slot.original)
        clean[source] = len(found) == before
    for source, lost in dropped.items():
        for n in sorted(lost):
            if n not in copied[source]:
                found.append(
                    Finding(
                        source,
                        0,
                        'dropped',
                        f'{source}:{n + 1} left the index and no destination copies it',
                    )
                )
    for path in sorted({r.path for r in migration.rewrites}):
        out = outputs.get(path)
        if out is None:
            found.append(Finding(path, 0, 'absent', 'the planned file is absent'))
        else:
            origins[path] = rewrite_origins(
                path, base_files[path], out, migration, found
            )
    for source in sources:
        paths = [source, *(c.move.to for c in made if c.move.source == source)]
        if all(clean.get(p) for p in paths):
            found += unaccounted(
                source, readings[source], paths, origins, dropped[source]
            )
    blocks = [
        Block(
            c.move.source,
            c.section.start + 1,
            c.section.end,
            c.move.to,
            len(lines_of(outputs.get(c.move.to) or b'')),
        )
        for c in made
    ]
    tracked = {*files, *base_files, *(p for p, d in outputs.items() if d is not None)}
    base_tree = Tree(dict(base_files), {*files, *base_files})
    judged = {p: d for p, d in (consumers or {}).items() if p not in migration.writes}
    out_tree = Tree({**base_files, **judged, **outputs}, tracked)
    more, broken, moved = link_proof(
        base_tree,
        out_tree,
        migration,
        readings,
        made,
        origins,
        {*migration.writes, *judged},
    )
    named = [p for p in (consumers or {}) if p in migration.writes or p in judged]
    held, seen, gone = consumer_proof(base_tree, out_tree, named, origins, moved)
    if found:  # the origin table a failed walk leaves is incomplete: its links are not trusted and the consumers it judged are shown as not judged; their findings count
        return Proof(origins, blocks, found + held, [], seen)
    return Proof(origins, blocks, more + held, broken + gone, seen)


def working_bytes(repo: Path, path: str) -> bytes | None:
    """The bytes of a file in the working tree, None when it is absent."""
    try:
        return (repo / path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ContextError(f'{path}: cannot be read: {e}') from e


def consumer_paths(repo: Path) -> list[str]:
    """The repo's kitchen.toml and overlays that exist in the working tree."""
    found = [KITCHEN_TOML] if (repo / KITCHEN_TOML).is_file() else []
    folder = repo / OVERLAYS
    if folder.is_dir():
        found += [f'{OVERLAYS}{p.name}' for p in sorted(folder.glob('*.toml'))]
    return found


def index_local(ref: Ref) -> bool:
    """Whether a reference names a section of the file it is written in. Among the unresolved ones that is a source's
    own staying text pointing at a heading it moved: the tool cannot rewrite it, because a rewrite target may not be a
    move source, and the index keeps the heading, so it still resolves."""
    return target_path(ref) == ref.path


STATES = ('pre', 'post', 'foreign')


def state_of(repo: Path, w: 'Write') -> str:
    """Where one planned file stands in the working tree: `post` when it equals the migration's output, `pre` when it
    equals the base (or is absent where the base has none), `foreign` otherwise."""
    now = working_bytes(repo, w.path)
    return 'post' if now == w.after else 'pre' if now == w.before else 'foreign'


def states(repo: Path, migration: Migration) -> list[tuple[str, str]]:
    """Where each planned file stands in the working tree, in `writes` order."""
    return [(w.path, state_of(repo, w)) for w in plan(repo, migration)]


def origin_data(o: Origin) -> dict:
    return {'origin': type(o).__name__.lower(), **dataclasses.asdict(o)}


def proof_data(proof: Proof) -> dict:
    """The proof as plain data: the origin of every output line, the blocks, the findings, the references broken at
    base and the consumers."""
    return {
        'ok': proof.ok,
        'blocks': [dataclasses.asdict(b) for b in proof.blocks],
        'findings': [dataclasses.asdict(f) for f in proof.findings],
        'pre_existing': [dataclasses.asdict(f) for f in proof.pre_existing],
        'consumers': [dataclasses.asdict(x) for x in proof.consumers],
        'origins': {
            p: [origin_data(o) for o in os_] for p, os_ in proof.origins.items()
        },
    }


def check(repo: Path, migration_path: Path) -> Proof:
    """`prove` over the working tree: each planned path as it is on disk, the kitchen.toml and overlays as they are
    on disk, every other file as it was at the migration's base. Reads; writes nothing."""
    migration = load(migration_path)
    commit = resolve_base(repo, migration)
    files = repo_files(repo, migration)
    declared = [f.path for f in migration.files]
    base = read_scanned(repo, commit, files, declared) | read_base(repo, migration)
    outputs = {p: working_bytes(repo, p) for p in migration.writes}
    consumers = {
        p: data
        for p in consumer_paths(repo)
        if (data := working_bytes(repo, p)) is not None
    }
    return prove(base, migration, outputs, files, consumers)


# ---- inventory -------------------------------------------------------------

CANDIDATE_SECTIONS = 3
CANDIDATE_TOKENS = 2000
CANDIDATE_REFERRERS = 2
SYNTAXES = (
    'Markdown inline links and images',
    'Markdown reference definitions',
    'Markdown link text wrapped over lines',
    '`path` and `path#anchor` strings in TOML and plain text',
)
LIMITS = (
    'globs',
    'generated paths',
    'external links',
    'references inside HTML comments and 4-space indented code (scanned as text, may be false positives)',
    'reference-style uses whose `[text]` and `[label]` wrap across lines (not seen)',
    'reference definitions with the title on the next line',
    'an empty `#` fragment (dropped on rebase)',
)


def tokens_of(text: str) -> int:
    return len(text) // 4


@dataclass(frozen=True)
class Heading:
    """A top-level section of a document and its estimated tokens."""

    raw: str
    tokens: int


@dataclass(frozen=True)
class Inbound:
    """The references one file makes to a document other than itself."""

    path: str
    refs: tuple[Ref, ...]


@dataclass(frozen=True)
class Document:
    path: str
    bytes: int
    tokens: int
    level: int | None  # the shallowest heading level present
    sections: tuple[Heading, ...]  # the headings of that level
    inbound: tuple[Inbound, ...]
    candidate: bool
    error: str | None  # why the headings could not be read, when they could not


@dataclass(frozen=True)
class Inventory:
    commit: str
    documents: tuple[Document, ...]
    scanned: int  # files the scanner read
    broken: tuple[Ref, ...]  # recognized references that name no file or anchor


def doc_anchors(path: str, data: bytes) -> frozenset[str] | None:
    """The anchors of a Markdown document, or None when it cannot be read as one."""
    try:
        return frozenset(anchors(sections(data, path)).values())
    except ContextError:
        return None


def is_broken(
    ref: Ref, files: Collection[str], heads: dict[str, frozenset[str] | None]
) -> bool:
    """A recognized reference to a file the repo lacks (a directory is not lacking), or to an anchor its Markdown
    target lacks."""
    found = target_path(ref)
    if found is None:
        return False
    if found not in files:
        return not any(f.startswith(found + '/') for f in files)
    anchor = unquote(ref.fragment).lower()
    known = heads.get(found)
    return bool(anchor) and known is not None and anchor not in known


def top_level(found: list[Section]) -> list[Section]:
    """The sections an author reads as the document's top level: the shallowest level's headings, or, while that
    level holds a single heading (a title) and a deeper level exists, the headings one level below it."""
    level = min(s.level for s in found)
    while sum(s.level == level for s in found) == 1:
        deeper = [s.level for s in found if s.level > level]
        if not deeper:
            break
        level = min(deeper)
    return [s for s in found if s.level == level]


def top_sections(data: bytes, where: str) -> tuple[int | None, tuple[Heading, ...]]:
    reading = read(data, where)
    if not reading.sections:
        return None, ()
    top = top_level(reading.sections)
    text = decode(reading.lines, where)
    return top[0].level, tuple(
        Heading(s.raw, tokens_of('\n'.join(text[s.start : s.end]))) for s in top
    )


def inventory_of(commit: str, base_files: dict[str, bytes]) -> Inventory:
    """The inventory of a repo from its files at a commit: `base_files` holds every file the scanner reads. Pure."""
    files = frozenset(base_files)
    docs = {p: d for p, d in base_files.items() if p.endswith(MARKDOWN)}
    heads = {p: doc_anchors(p, d) for p, d in docs.items()}
    inbound: dict[str, dict[str, list[Ref]]] = {}
    broken: list[Ref] = []
    for path in sorted(base_files):
        if not scanned(path):
            continue
        for ref in references(path, base_files[path], files):
            if is_broken(ref, files, heads):
                broken.append(ref)
                continue
            hit = target_path(ref)
            if hit in docs and hit != path:
                inbound.setdefault(hit, {}).setdefault(path, []).append(ref)
    found = []
    for path, data in sorted(docs.items()):
        text = data.decode(errors='replace')
        try:
            level, tops = top_sections(data, path)
            error = None
        except ContextError as e:
            level, tops, error = None, (), str(e)
        refs = inbound.get(path, {})
        by_section = {f for f, rs in refs.items() if any(r.fragment for r in rs)}
        found.append(
            Document(
                path,
                len(data),
                tokens_of(text),
                level,
                tops,
                tuple(Inbound(f, tuple(rs)) for f, rs in sorted(refs.items())),
                len(tops) >= CANDIDATE_SECTIONS
                and tokens_of(text) > CANDIDATE_TOKENS
                and len(by_section) >= CANDIDATE_REFERRERS,
                error,
            )
        )
    return Inventory(commit, tuple(found), len(base_files), tuple(broken))


def inventory_data(inv: Inventory) -> dict:
    """The inventory as plain data: what `inventory --json` prints and what its text is rendered from."""

    def ref(r: Ref) -> dict:
        return {'path': r.path, 'line': r.line, 'raw': r.raw, 'target': target_of(r)}

    return {
        'commit': inv.commit,
        'documents': [
            {
                'path': d.path,
                'bytes': d.bytes,
                'tokens': d.tokens,
                'level': d.level,
                'sections': [
                    {'heading': h.raw, 'tokens': h.tokens} for h in d.sections
                ],
                'inbound': [
                    {'from': i.path, 'refs': [ref(r) for r in i.refs]}
                    for i in d.inbound
                ],
                'candidate': d.candidate,
                'error': d.error,
            }
            for d in inv.documents
        ],
        'coverage': {
            'syntaxes': list(SYNTAXES),
            'file_types': list(SCANNED),
            'scanned': inv.scanned,
            'broken': {'count': len(inv.broken), 'refs': [ref(r) for r in inv.broken]},
            'limits': list(LIMITS),
        },
    }


def head_commit(repo: Path) -> str:
    done = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}')
    if done.returncode:
        raise ContextError(f'{repo} has no commit at HEAD')
    return done.stdout.decode().strip()


def tracked(repo: Path, commit: str) -> frozenset[str]:
    done = git(repo, 'ls-tree', '-r', '--name-only', '-z', commit)
    if done.returncode:
        raise ContextError(f'cannot list the files at {commit}')
    return frozenset(p for p in done.stdout.decode(errors='replace').split('\0') if p)


def inventory(repo: Path) -> Inventory:
    """The inventory of the repo's tracked files at HEAD. Reads git only; writes nothing."""
    commit = head_commit(repo)
    return inventory_of(commit, read_scanned(repo, commit, tracked(repo, commit)))


def json_string(text: str) -> str:
    """`text` as a TOML basic string."""
    return json.dumps(text, ensure_ascii=False)


def draft_id(path: str) -> str:
    stem = posixpath.splitext(posixpath.basename(path))[0]
    name = re.sub(r'[^A-Za-z0-9._-]', '-', stem).lstrip('._-')
    return name or 'migration'


def draft(repo: Path, path: str) -> str:
    """A schema-1 migration skeleton for the Markdown file `path` at HEAD: the root keys, its `[[file]]`, and one
    commented `[[move]]` for each top-level section. Prints text only; the writer fills `to` and `writes`."""
    check_path(path, 'draft')
    commit = head_commit(repo)
    data = read_blobs(repo, commit, [path]).get(path)
    if data is None:
        raise ContextError(f'{path}: not a file at HEAD')
    found = sections(data, path)
    if not found:
        raise ContextError(f'{path}: has no headings to move')
    out = [
        'schema = 1',
        f'id = {json_string(draft_id(path))}',
        f'base = "{commit}"',
        f'writes = [\n  {json_string(path)},\n]',
        '',
        '[[file]]',
        f'path = {json_string(path)}',
        f'sha256 = "{hashlib.sha256(data).hexdigest()}"',
    ]
    for s in top_level(found):
        same = [t for t in found if t.path == s.path]
        block = [
            '',
            '# [[move]]',
            f'# source = {json_string(path)}',
            f'# ancestry = [{", ".join(json_string(x) for x in s.path)}]',
        ]
        if len(same) > 1:
            block.append(f'# occurrence = {same.index(s) + 1}')
        block.append('# to = "TODO"')
        out += block
    return '\n'.join(out) + '\n'


# ---- preview ---------------------------------------------------------------


@dataclass(frozen=True)
class Write:
    """One planned write: the file at base (None for a new file) and what the migration leaves there."""

    path: str
    before: bytes | None
    after: bytes


def plan(repo: Path, migration: Migration) -> list[Write]:
    """Every file in `writes`, in order, as the migration would leave it. Reads the base commit only."""
    base = read_base(repo, migration)
    named = {mv.to for mv in migration.moves}
    named |= {p for mv in migration.moves for p in mv.requires}
    after = render(
        migration,
        base,
        base_files_at(repo, migration, named),
        repo_files(repo, migration),
    )
    return [
        Write(path, base.get(path), after.get(path, base.get(path, b'')))
        for path in migration.writes
    ]


def unified_diff(write: Write) -> str:
    """The unified diff of one planned write, as `git diff` names its sides; empty when nothing changes."""
    if write.before == write.after:
        return ''
    before = write.before.decode().splitlines(keepends=True) if write.before else []
    after = write.after.decode().splitlines(keepends=True)
    lines = difflib.unified_diff(
        before,
        after,
        '/dev/null' if write.before is None else f'a/{write.path}',
        f'b/{write.path}',
    )
    out = []
    for line in lines:
        out.append(
            line if line.endswith('\n') else f'{line}\n\\ No newline at end of file\n'
        )
    return ''.join(out)


# ---- apply -----------------------------------------------------------------


class ApplyRefused(ContextError):
    """`apply` declined or stopped: the tree holds what it had written so far (`written`), and the message says why."""

    def __init__(self, message: str, written: tuple[str, ...] = ()):
        super().__init__(message)
        self.written = written


@dataclass(frozen=True)
class ApplyResult:
    written: tuple[
        str, ...
    ]  # the paths this run wrote, in order; empty for `no change`
    index_local: tuple[
        Ref, ...
    ]  # references the tool leaves to the index, as preview lists them


def proof_at_base(repo: Path, migration: Migration, planned: list[Write]) -> Proof:
    """`prove` over the outputs the migration plans, in memory: the files and the kitchen.toml and overlays as they
    are at the base commit."""
    commit = resolve_base(repo, migration)
    files = repo_files(repo, migration)
    declared = [f.path for f in migration.files]
    base = read_scanned(repo, commit, files, declared) | read_base(repo, migration)
    consumers = read_blobs(
        repo,
        commit,
        [
            p
            for p in files
            if p == KITCHEN_TOML or p.startswith(OVERLAYS) and p.endswith('.toml')
        ],
    )
    return prove(base, migration, {w.path: w.after for w in planned}, files, consumers)


def refusal(proof: Proof) -> str:
    return '\n'.join(
        f'{f.path}:{f.line}: {f.kind}: {f.message}'
        if f.line
        else f'{f.path}: {f.kind}: {f.message}'
        for f in proof.findings
    )


def write_file(repo: Path, w: Write) -> None:
    """Write one planned file beside itself and move it into place; an existing file keeps its mode, a new one gets
    the umask default."""
    target = repo / w.path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f'.{target.name}.')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(w.after)
        if w.before is None:
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(temp, 0o666 & ~mask)
        else:
            shutil.copymode(target, temp)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def apply(
    repo: Path, migration_path: Path, stop_after: int | None = None
) -> ApplyResult:
    """Write a migration's planned files: only after `prove` passes on them in memory, only the ones still at their
    pre-image, and nothing when any planned file is neither image. Completes a partial apply; a second run finds
    every file at its post-image and changes nothing. `stop_after` ends the run when it has made that many writes and
    another remains; when exactly that many remain, they are all written and the run ends normally."""
    migration = load(migration_path)
    planned = plan(repo, migration)
    open_refs = plan_unresolved(repo, migration)
    local = tuple(r for r in open_refs if index_local(r))
    loose = [r for r in open_refs if not index_local(r)]
    if loose:
        raise ApplyRefused(
            '\n'.join(
                f'unresolved: {r.path}:{r.line}: {" ".join(r.raw.split())}'
                for r in loose
            )
            + '\nrewrite or keep each, then apply again'
        )
    where = [(w, state_of(repo, w)) for w in planned]
    foreign = [w.path for w, state in where if state == 'foreign']
    if foreign:
        raise ApplyRefused(
            '\n'.join(
                f'foreign: {p}: at neither the base nor the output' for p in foreign
            )
            + '\nnothing written; update `base` and each `[[file]]` sha256 to the current commit,'
            ' or redraft with `inventory --draft`'
        )
    todo = [w for w, state in where if state == 'pre']
    if not todo:
        return ApplyResult((), local)
    proof = proof_at_base(repo, migration, planned)
    if not proof.ok:
        raise ApplyRefused(
            f'{refusal(proof)}\nnothing written: the plan does not prove'
        )
    written: list[str] = []
    for w in todo:
        if stop_after is not None and len(written) >= stop_after:
            raise ApplyRefused(f'stopped after {len(written)} writes', tuple(written))
        write_file(repo, w)
        written.append(w.path)
    proof = check(repo, migration_path)
    if not proof.ok:
        raise ApplyRefused(
            f'{refusal(proof)}\nwritten, and the tree does not prove', tuple(written)
        )
    return ApplyResult(tuple(written), local)
