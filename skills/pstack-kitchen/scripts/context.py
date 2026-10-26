#!/usr/bin/env python3
"""Move sections of a repo's Markdown documents into new modules from a reviewed migration file.

This module holds the migration file's model and loader, the reader over a source's original bytes, the references a
file makes to others, the cuts a migration makes and their rendering. Everything but `load` and the git readers is a
pure function over bytes and typed data; nothing here writes a file.

A migration reads every input from its recorded base commit, never from the working tree, so a preview, an apply and
a second apply all reconstruct the same outputs from the same inputs.

Stdlib only, and independent of kitchen_core. Headings and fences come from playbooks.py.
"""

import difflib
import functools
import hashlib
import importlib.util
import os
import posixpath
import re
import subprocess
import tomllib
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from urllib.parse import quote, unquote

SCHEMA = 1
ROOT_KEYS = ('schema', 'id', 'base', 'writes')
TABLES = ('file', 'move', 'rewrite', 'keep')
FILE_KEYS = ('path', 'sha256')
MOVE_KEYS = ('source', 'ancestry', 'occurrence', 'to', 'requires')
REWRITE_KEYS = ('path', 'from', 'to')
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
    rewrites = [
        Rewrite(
            check_path(t.get('path'), f'[[rewrite]] {n}'),
            string(t, 'from', f'[[rewrite]] {n}'),
            string(t, 'to', f'[[rewrite]] {n}'),
        )
        for n, t in enumerate(tables['rewrite'], 1)
    ]
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
    commit = resolve_base(repo, migration)
    done = git(repo, 'ls-tree', '-r', '--name-only', '-z', commit)
    if done.returncode:
        raise ContextError(f'cannot list the files at base {migration.base!r}')
    return frozenset(p for p in done.stdout.decode(errors='replace').split('\0') if p)


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
    """Each `[[rewrite]]` target's base bytes with `from` replaced by `to`. `from` must occur exactly once in the
    file as the earlier rewrites left it; `to` must name a file the repo, the migration or its outputs hold, and the
    section of it that `to` names must exist in the output."""
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
        if count != 1:
            raise ContextError(
                f'{r.path}: `from` {r.from_!r} occurs {count} times; it must occur exactly once'
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
