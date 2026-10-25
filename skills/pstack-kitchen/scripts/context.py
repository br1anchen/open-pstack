#!/usr/bin/env python3
"""Move sections of a repo's Markdown documents into new modules from a reviewed migration file.

This module holds the migration file's model and loader (`Migration`, `load`), the reader over the
original bytes of a source (`lines_of`, `sections`, `find`), the cuts a migration makes (`cuts`) and
their rendering (`render`). Everything but `load` and the two `git` readers (`read_base`,
`base_files_at`) is a pure function over bytes and typed data; nothing here writes a file.

A migration reads every input from its recorded base commit, never from the working tree, so a
preview, an apply and a second apply all reconstruct the same outputs from the same inputs.

Stdlib only, and independent of kitchen_core. Headings and fences come from playbooks.py.
"""

import functools
import hashlib
import importlib.util
import re
import subprocess
import tomllib
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

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
THEMATIC = re.compile(r'(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})')
GIT_TIMEOUT_S = 60


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
    if path.startswith('/') or '\\' in path or any(p in ('', '.', '..') for p in parts):
        raise ContextError(
            f'{where}: {path!r} must be a repo-relative path with no `..`, no leading `/` and no backslash'
        )
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
    if data.get('schema') != SCHEMA or isinstance(data.get('schema'), bool):
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
    try:
        return subprocess.run(
            ['git', '-C', str(repo), *args],
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
        done = git(repo, 'show', f'{commit}:{entry.path}')
        if done.returncode:
            raise ContextError(f'{entry.path}: not present at base {migration.base!r}')
        digest = hashlib.sha256(done.stdout).hexdigest()
        if digest != entry.sha256:
            raise ContextError(
                f'{entry.path}: sha256 {digest} at base {migration.base!r} differs from the migration'
                f' ({entry.sha256}); the file changed after the migration was written'
            )
        found[entry.path] = done.stdout
    return found


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


def setext_line(text: list[str], i: int, fenced: frozenset[int], start: int) -> bool:
    """Whether line `i` is the underline of a setext heading."""
    pb = playbooks_module()
    line = text[i]
    if i - 1 < start or i in fenced or i - 1 in fenced or not SETEXT.fullmatch(line):
        return False
    prev = text[i - 1]
    if (
        not prev.strip()
        or pb.HEADING.match(prev)
        or pb.LIST_MARK.match(prev)
        or prev[0] in ' \t>|'
        or THEMATIC.fullmatch(prev)
    ):
        return False
    return True


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
        if setext_line(text, i, fenced, start):
            raise ContextError(
                f'{where}: line {i} is a setext heading; schema 1 reads `#` headings only'
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
    """The cut each move makes, in the migration's order. Refuses a move whose source or a rewrite whose target has
    no `[[file]]`, a section moved twice, a destination named twice, a destination that is a source or exists at the
    base (`existing`), and any overlap that is not nesting."""
    declared = {f.path for f in migration.files}
    for rewrite in migration.rewrites:
        if rewrite.path not in declared:
            raise ContextError(f'rewrite target {rewrite.path!r} has no [[file]] entry')
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
            elif not contains(a, b) and a.start < b.end and b.start < a.end:
                raise ContextError(
                    f'moves of {" > ".join(a.path)} and {" > ".join(b.path)} overlap without nesting'
                )
        out.append(Cut(mv, a, parent))
    return out
