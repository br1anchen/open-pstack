#!/usr/bin/env python3
"""Take an upstream pstack release (cursor/plugins) into this repo's skills/.

audit  lists every local customization, local-only skill and file, and watched skill.
plan   prints one row per upstream change between the pinned revision and --to; writes nothing.
apply  writes the changes, then moves the pin. A conflict leaves sources.json
       `upstream.pending` set (exit 3); resolve the markers and rerun the same --to.

The script never fetches: --upstream is a checkout the caller has fetched.
Exit codes: 0 done or nothing to do, 2 refused or tool failure (nothing written),
3 conflicts to resolve, 4 judge rows to decide in the manifest.
"""
import argparse
from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

CURSOR_KEYS = ('disable-model-invocation', 'mode', 'icon', 'color', 'reminder')
ADAPTER = ('Read [the runtime adapter](../pstack/references/runtime.md) before following this workflow. '
           'Its runtime mappings apply to all referenced playbooks and scripts.')
SKILL_ROOTS = ('pstack/skills', 'pstack/automations/benny/skills')
MARKER = '<<<<<<< local'
ACTIONABLE = ('add', 'fast-forward', 'merge', 'conflict', 'remove')
EXIT_ERROR, EXIT_CONFLICT, EXIT_JUDGE = 2, 3, 4


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def adapt(local_name, rel, data):
    """The mechanical difference between an upstream file and our copy of it."""
    if rel != 'SKILL.md':
        return data
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return data
    match = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not match:
        return data
    kept, dropping = [], False
    for line in match.group(1).split('\n'):
        key = re.match(r'([A-Za-z0-9_-]+):', line)
        if key:
            dropping = key.group(1) in CURSOR_KEYS
            if key.group(1) == 'name':
                line = f'name: {local_name}'
        if not dropping:
            kept.append(line)
    body = text[match.end():]
    if local_name != 'pstack':
        body = f'\n{ADAPTER}\n\n{body}'
    return ('---\n' + '\n'.join(kept) + '\n---\n' + body).encode()


def run_git(cwd, *args, input=None):
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, input=input)
    if result.returncode:
        raise Stop(EXIT_ERROR, f'git {" ".join(args)} failed: {result.stderr.decode().strip()}')
    return result.stdout


def skill_of(path):
    """(upstream skill dir, dir name, path inside it) for a file under a skills root."""
    for root in SKILL_ROOTS:
        if path.startswith(root + '/'):
            name, _, rel = path[len(root) + 1:].partition('/')
            if rel:
                return f'{root}/{name}', name, rel
    return None


@dataclass
class Delta:
    status: str
    old_path: str
    new_path: str
    old_mode: str
    new_mode: str
    old_sha: str
    new_sha: str


class Upstream:
    def __init__(self, checkout):
        self.checkout = str(checkout)

    def git(self, *args, input=None):
        return run_git(self.checkout, *args, input=input)

    def commit(self, rev):
        return self.git('rev-parse', '--verify', f'{rev}^{{commit}}').decode().strip()

    def tree(self, rev):
        out = self.git('ls-tree', '-r', '-z', '--full-tree', rev, '--', *SKILL_ROOTS)
        files = {}
        for entry in filter(None, out.split(b'\0')):
            meta, _, path = entry.decode().partition('\t')
            mode, kind, sha = meta.split()
            if kind == 'blob':
                files[path] = (mode, sha)
        return files

    def blob(self, sha):
        return self.git('cat-file', 'blob', sha)

    def deltas(self, base, to):
        tokens = self.git('diff', '-z', '-M', '--raw', '--no-abbrev', base, to, '--', *SKILL_ROOTS).decode().split('\0')
        found, i = [], 0
        while i < len(tokens) and tokens[i]:
            old_mode, new_mode, old_sha, new_sha, status = tokens[i][1:].split()
            status = status[0]
            if status in 'RC':
                found.append(Delta(status, tokens[i + 1], tokens[i + 2], old_mode, new_mode, old_sha, new_sha))
                i += 3
            else:
                found.append(Delta(status, tokens[i + 1], tokens[i + 1], old_mode, new_mode, old_sha, new_sha))
                i += 2
        return found

    def version(self, rev):
        try:
            return json.loads(self.git('show', f'{rev}:pstack/plugin.json'))['version']
        except (Stop, KeyError, ValueError):
            return None

    def stat(self, base, to, path):
        return self.git('diff', '--stat', base, to, '--', path).decode().rstrip()


@dataclass
class Row:
    disposition: str
    path: str
    note: str = ''
    data: bytes = b''
    mode: str = '100644'
    new_skill: tuple = None


class Tree:
    """This repo: sources.json and skills/."""

    def __init__(self, root):
        self.root = Path(root)
        self.manifest_path = self.root / 'sources.json'
        self.manifest = json.loads(self.manifest_path.read_text())
        self.skills = self.manifest['skills']
        self.excluded = self.manifest.get('excluded', {})
        self.watch = self.manifest.get('watch', {})
        self.inverse = {v: k for k, v in self.skills.items() if v.startswith('pstack/')}

    def read(self, path):
        file = self.root / path
        return file.read_bytes() if file.is_file() else None

    def classify(self, upstream_dir, name, base_dirs):
        if upstream_dir in self.watch.values():
            return 'watched', None
        if name in self.excluded:
            return 'excluded', None
        if upstream_dir in self.inverse:
            return 'mapped', self.inverse[upstream_dir]
        if upstream_dir not in base_dirs:
            return 'new', name
        return 'unmapped', None

    def collides(self, name):
        return name in self.skills or name in self.watch or (self.root / 'skills' / name).exists()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False) + '\n')


def merge_files(ours, base, theirs, base_rev, to_rev):
    """git merge-file with diff3 markers on three paths. Returns (bytes, conflict count)."""
    cmd = ['git', 'merge-file', '-p', '--diff3', '-L', 'local', '-L', f'upstream {base_rev[:7]}',
           '-L', f'upstream {to_rev[:7]}', ours, base, theirs]
    try:
        result = subprocess.run(cmd, capture_output=True)
    except OSError as error:
        raise Stop(EXIT_ERROR, f'git merge-file did not run: {error}')
    # 0 is clean and 1-127 is the conflict count; anything else, a signal included, is a tool failure.
    if 0 <= result.returncode <= 127:
        return result.stdout, result.returncode
    raise Stop(EXIT_ERROR, f'git merge-file failed with exit {result.returncode}: {result.stderr.decode().strip()}')


def merge3(ours, base, theirs, base_rev, to_rev):
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, data in (('ours', ours), ('base', base), ('theirs', theirs)):
            path = Path(tmp) / name
            path.write_bytes(data)
            paths.append(str(path))
        return merge_files(*paths, base_rev, to_rev)


def is_binary(*blobs):
    return any(b'\0' in blob for blob in blobs)


def build(tree, up, base, to):
    """Every row for base..to, computed in memory. Writes nothing."""
    base_tree = up.tree(base)
    base_dirs = {skill_of(p)[0] for p in base_tree if skill_of(p)}
    rows, collided, mapped_new = [], set(), {}

    def target(path):
        found = skill_of(path)
        kind, local = tree.classify(found[0], found[1], base_dirs)
        return kind, local, found

    for delta in up.deltas(base, to):
        path = delta.new_path
        kind, local, (upstream_dir, name, rel) = target(path)
        if kind in ('watched', 'excluded'):
            continue
        if kind == 'unmapped':
            rows.append(Row('skip', path, 'unmapped upstream skill'))
            continue
        if kind == 'new':
            if tree.collides(name):
                if name not in collided:
                    collided.add(name)
                    rows.append(Row('judge', f'skills/{name}', 'new upstream skill collides with a local skill or file'))
                continue
            mapped_new[name] = upstream_dir
        dest = f'skills/{local}/{rel}'
        theirs = adapt(local, rel, up.blob(delta.new_sha)) if delta.status != 'D' else None
        ours = tree.read(dest)
        if delta.status == 'A':
            if ours is None:
                rows.append(Row('add', dest, 'new skill' if kind == 'new' else '', theirs, delta.new_mode))
            elif ours != theirs:
                rows.append(Row('judge', dest, 'added upstream, a different file exists locally'))
        elif delta.status == 'D':
            if ours is not None:
                rows.append(Row('keep', dest, 'deleted upstream'))
        elif delta.status == 'M':
            rows.append(modified(delta, dest, ours, theirs, local, rel, up, base, to))
        elif delta.status == 'R':
            rows.extend(renamed(tree, up, delta, base_dirs, dest, theirs))
        else:
            rows.append(Row('judge', dest, f'upstream changed the file type ({delta.status})'))
    for row in rows:
        if row.disposition == 'add' and row.note == 'new skill':
            row.new_skill = next((n, d) for n, d in mapped_new.items() if row.path.startswith(f'skills/{n}/'))
    return sorted(rows, key=lambda r: (r.path, r.disposition))


def modified(delta, dest, ours, theirs, local, rel, up, base, to):
    if ours is None:
        return Row('judge', dest, 'removed locally, changed upstream')
    if MARKER.encode() in ours:
        return Row('judge', dest, 'holds conflict markers')
    old = adapt(local, rel, up.blob(delta.old_sha))
    mode_changed = delta.old_mode != delta.new_mode
    if ours == theirs and not mode_changed:
        return Row('noop', dest)
    if ours == old or ours == theirs:
        return Row('fast-forward', dest, '', theirs, delta.new_mode)
    if is_binary(ours, old, theirs):
        return Row('judge', dest, 'binary file customized locally')
    merged, conflicts = merge3(ours, old, theirs, base, to)
    if conflicts:
        return Row('conflict', dest, f'{conflicts} conflict(s)', merged, delta.new_mode)
    return Row('merge', dest, '', merged, delta.new_mode)


def renamed(tree, up, delta, base_dirs, dest, theirs):
    old_found = skill_of(delta.old_path)
    old_kind, old_local = tree.classify(old_found[0], old_found[1], base_dirs)
    new_kind, _ = tree.classify(*skill_of(delta.new_path)[:2], base_dirs)
    if old_kind != 'mapped' or new_kind != 'mapped':
        return [Row('judge', dest, 'renamed upstream across skills we do not map')]
    old_dest = f'skills/{old_local}/{old_found[2]}'
    ours_old = tree.read(old_dest)
    ours_new = tree.read(dest)
    if ours_new is not None and ours_new != theirs:
        return [Row('judge', dest, 'renamed upstream onto a different local file')]
    if ours_old is None:
        return [Row('add', dest, f'renamed from {old_dest}', theirs, delta.new_mode)]
    if ours_old != adapt(old_local, old_found[2], up.blob(delta.old_sha)):
        return [Row('judge', old_dest, f'customized file renamed upstream to {dest}')]
    return [Row('remove', old_dest, f'renamed to {dest}'), Row('add', dest, f'renamed from {old_dest}', theirs, delta.new_mode)]


def watched(tree, up, base, to):
    return {name: up.stat(base, to, path) for name, path in tree.watch.items()}


def unmapped_dirs(tree, up, base):
    dirs = {skill_of(p) for p in up.tree(base) if skill_of(p)}
    names = {}
    for upstream_dir, name, _ in dirs:
        kind, _ = tree.classify(upstream_dir, name, {upstream_dir})
        if kind == 'unmapped':
            names[name] = upstream_dir
    return sorted(names)


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row.disposition] = counts.get(row.disposition, 0) + 1
    return counts


def print_rows(rows):
    for row in rows:
        if row.disposition != 'noop':
            print(f'{row.disposition:<13} {row.path}' + (f'  ({row.note})' if row.note else ''))


def print_watched(tree, up, base, to):
    if tree.watch:
        print('watched (never written):')
        for name, stat in watched(tree, up, base, to).items():
            print(f'  {name}: ' + (stat.replace(chr(10), chr(10) + "    ") if stat else 'no change'))


def context(args):
    tree = Tree(args.root)
    up = Upstream(args.upstream)
    base = up.commit(tree.manifest['upstream']['revision'])
    to = up.commit(getattr(args, 'to', None) or 'HEAD')
    if base != to and subprocess.run(['git', 'merge-base', '--is-ancestor', base, to], cwd=up.checkout).returncode:
        raise Stop(EXIT_ERROR, f'{base[:7]} is not an ancestor of {to[:7]}')
    return tree, up, base, to


def cmd_plan(args):
    tree, up, base, to = context(args)
    rows = build(tree, up, base, to)
    print(f'upstream {base[:7]} -> {to[:7]}')
    print_rows(rows)
    print_watched(tree, up, base, to)
    names = unmapped_dirs(tree, up, base)
    if names:
        print('unmapped upstream skills (never imported): ' + ', '.join(names))
    counts = summarize(rows)
    todo = sum(counts.get(d, 0) for d in ACTIONABLE + ('judge',))
    print('summary: ' + (', '.join(f'{n} {d}' for d, n in sorted(counts.items())) or 'no changes'))
    pending = tree.manifest['upstream'].get('pending')
    if pending:
        print(f'pending: update to {pending["revision"][:7]} is unresolved')
    if not todo and not pending:
        print('nothing to do')
    return 0


def dirty(root, paths):
    out = run_git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--', *paths)
    return [e[3:] for e in out.decode().split('\0') if e]


def cmd_apply(args):
    tree, up, base, to = context(args)
    upstream = tree.manifest['upstream']
    pending = upstream.get('pending')
    if pending:
        if to != pending['revision']:
            raise Stop(EXIT_ERROR, f'update to {pending["revision"][:7]} is unresolved; rerun with --to {pending["revision"][:7]}')
        return resume(tree, pending)
    rows = build(tree, up, base, to)
    judge = [r for r in rows if r.disposition == 'judge']
    if judge:
        print_rows(judge)
        print('judge rows: decide each in sources.json (excluded) or commit a hand change, then rerun. Nothing was written.')
        return EXIT_JUDGE
    todo = [r for r in rows if r.disposition in ACTIONABLE]
    if not todo and base == to:
        print('nothing to do')
        return 0
    paths = sorted({r.path for r in todo} | {'sources.json'})
    unclean = dirty(tree.root, paths)
    if unclean:
        raise Stop(EXIT_ERROR, 'uncommitted changes in the way (commit or stash them): ' + ', '.join(unclean))
    for row in todo:
        file = tree.root / row.path
        if row.disposition == 'remove':
            file.unlink()
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(row.data)
        os.chmod(file, 0o755 if row.mode == '100755' else 0o644)
    for row in todo:
        if row.new_skill:
            tree.skills[row.new_skill[0]] = row.new_skill[1]
    conflicts = [r.path for r in todo if r.disposition == 'conflict']
    version = up.version(to) or upstream.get('version')
    if conflicts:
        upstream['pending'] = {'revision': to, 'version': version, 'conflicts': conflicts,
                               'written': [r.path for r in todo if r.disposition != 'remove'], 'deleted': []}
    else:
        upstream['revision'], upstream['version'] = to, version
    tree.write_manifest()
    print_rows(todo)
    if conflicts:
        print(f'{len(conflicts)} file(s) hold conflict markers. Resolve them, then rerun with --to {to[:7]}.')
        return EXIT_CONFLICT
    print(f'applied {to[:7]}')
    return 0


def resume(tree, pending):
    unresolved, missing = [], []
    for path in pending['written']:
        data = tree.read(path)
        if data is None:
            if path in pending['conflicts'] and path not in pending['deleted']:
                missing.append(path)
        elif MARKER.encode() in data:
            unresolved.append(path)
    if missing:
        raise Stop(EXIT_ERROR, 'conflicted file(s) missing (restore them, or list them in upstream.pending.deleted): '
                   + ', '.join(missing))
    if unresolved:
        print('\n'.join(f'conflict      {p}' for p in unresolved))
        print('Resolve the markers above, then rerun with the same --to.')
        return EXIT_CONFLICT
    upstream = tree.manifest['upstream']
    upstream['revision'], upstream['version'] = pending['revision'], pending['version'] or upstream.get('version')
    del upstream['pending']
    tree.write_manifest()
    print(f'applied {pending["revision"][:7]}')
    return 0


def diffstat(ours, theirs):
    added = removed = 0
    for line in difflib.unified_diff(theirs.decode(errors='replace').splitlines(), ours.decode(errors='replace').splitlines(), lineterm='', n=0):
        if line.startswith('+') and not line.startswith('+++'):
            added += 1
        elif line.startswith('-') and not line.startswith('---'):
            removed += 1
    return added, removed


def audit(tree, up, rev):
    base_tree = up.tree(rev)
    report = {'customized': [], 'binary': [], 'absent': [], 'local_only_files': [], 'local_only_skills': [],
              'watched': dict(tree.watch), 'unmapped': unmapped_dirs(tree, up, rev)}
    mapped_files = {}
    for path, (_, sha) in sorted(base_tree.items()):
        found = skill_of(path)
        if not found or found[0] not in tree.inverse:
            continue
        local = tree.inverse[found[0]]
        dest = f'skills/{local}/{found[2]}'
        mapped_files.setdefault(local, set()).add(found[2])
        ours = tree.read(dest)
        if ours is None:
            report['absent'].append(dest)
            continue
        theirs = adapt(local, found[2], up.blob(sha))
        if ours == theirs:
            continue
        if is_binary(ours, theirs):
            report['binary'].append(dest)
        else:
            added, removed = diffstat(ours, theirs)
            report['customized'].append((dest, added, removed))
    for local, rels in sorted(mapped_files.items()):
        folder = tree.root / 'skills' / local
        for file in sorted(p for p in folder.rglob('*') if p.is_file()):
            rel = file.relative_to(folder).as_posix()
            if rel not in rels:
                report['local_only_files'].append(f'skills/{local}/{rel}')
    report['local_only_skills'] = sorted(n for n, v in tree.skills.items() if not v.startswith('pstack/'))
    return report


def cmd_audit(args):
    tree = Tree(args.root)
    up = Upstream(args.upstream)
    rev = up.commit(args.at or tree.manifest['upstream']['revision'])
    report = audit(tree, up, rev)
    print(f'audit against upstream {rev[:7]}')
    print(f'customized upstream files ({len(report["customized"])}):')
    for path, added, removed in report['customized']:
        print(f'  {path}  +{added} -{removed}')
    for heading, key in (('binary files that differ', 'binary'), ('upstream files absent locally', 'absent'),
                         ('local-only files in mapped skills (includes kept upstream deletions)', 'local_only_files'),
                         ('local-only skills', 'local_only_skills'), ('unmapped upstream skills', 'unmapped')):
        print(f'{heading} ({len(report[key])}):')
        for item in report[key]:
            print(f'  {item}')
    print(f'watched skills ({len(report["watched"])}):')
    for name, path in report['watched'].items():
        print(f'  {name} -> {path}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help='the repo to update')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('audit', 'plan', 'apply'):
        command = sub.add_parser(name)
        command.add_argument('--upstream', type=Path, required=True, help='a fetched cursor/plugins checkout')
        if name == 'audit':
            command.add_argument('--at', help='revision to compare with; default the pinned one')
        else:
            command.add_argument('--to', help='target revision; default the checkout HEAD')
    args = parser.parse_args(argv)
    try:
        return {'audit': cmd_audit, 'plan': cmd_plan, 'apply': cmd_apply}[args.command](args)
    except Stop as stop:
        print(f'error: {stop}', file=sys.stderr)
        return stop.code


if __name__ == '__main__':
    sys.exit(main())
