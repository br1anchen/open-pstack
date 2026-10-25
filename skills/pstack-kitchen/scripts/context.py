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
