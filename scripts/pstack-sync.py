#!/usr/bin/env python3
"""Expose one pstack skill tree to local agents, preserving replaced installs."""

import argparse
import json
import os
import tempfile
from pathlib import Path

# pi reads ~/.agents/skills itself, so it gets no links of its own.
ROOTS = ('.agents/skills', '.claude/skills', '.grok/skills')
# Where earlier installs linked pi's copies; a link there to a pstack skill would show pi each skill twice.
RETIRED = ('.pi/agent/skills',)


def pstack_link(path, skill):
    """Whether `path` is a link an install made for `skill`: to this copy, to another pstack skills tree (one with the pstack skill
    beside it), or to one that is gone."""
    if not path.is_symlink():
        return False
    target = path.resolve()
    if target == skill.resolve() or not target.exists():
        return True
    return target.name == skill.name and (target.parent / 'pstack/SKILL.md').is_file()


def sync(source, home, apply=False):
    skills = sorted(path for path in source.iterdir() if (path / 'SKILL.md').is_file())
    if not skills:
        raise ValueError(f'no skills in {source}')
    backup = None
    changes = []
    seen = set()

    def save(path, root):
        nonlocal backup
        if backup is None:
            backup_root = home / '.local/state/pstack/backups'
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = Path(tempfile.mkdtemp(prefix='install-', dir=backup_root))
        saved = backup / root / path.name
        saved.parent.mkdir(parents=True, exist_ok=True)
        path.rename(saved)

    for root in ROOTS:
        parent = home / root
        if apply:
            parent.mkdir(parents=True, exist_ok=True)
        for skill in skills:
            target = parent / skill.name
            physical = target.parent.resolve() / target.name
            if physical in seen:
                continue
            seen.add(physical)
            if target.is_symlink() and target.resolve() == skill.resolve():
                continue
            changes.append(str(target))
            if not apply:
                continue
            if target.exists() or target.is_symlink():
                save(target, root)
            # A unique temporary link makes replacement atomic for readers.
            with tempfile.TemporaryDirectory(prefix='.pstack-', dir=parent) as staging:
                link = Path(staging) / skill.name
                link.symlink_to(skill.resolve(), target_is_directory=True)
                os.replace(link, target)
    for root in RETIRED:
        for skill in skills:
            old = home / root / skill.name
            # A retired directory that aliases a live one holds the links just made.
            if old.parent.resolve() / old.name in seen:
                continue
            if pstack_link(old, skill):
                changes.append(f'{old} (removed)')
                if apply:
                    save(old, root)
    return {
        'skills': len(skills),
        'changed': changes,
        'backup': str(backup) if backup else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--source', type=Path, default=Path(__file__).resolve().parents[1] / 'skills'
    )
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument(
        '--apply',
        action='store_true',
        help='install links; otherwise report drift only',
    )
    args = parser.parse_args()
    result = sync(args.source.resolve(), args.home.expanduser().absolute(), args.apply)
    print(json.dumps(result, indent=2))
    return 0 if args.apply or not result['changed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
