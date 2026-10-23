#!/usr/bin/env python3
"""Check the vendored suite's discovery, inventory, links, and runtime entrypoints."""

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'sources.json').read_text())
skills = root / 'skills'
expected = set(manifest['skills'])
actual = {path.parent.name for path in skills.glob('*/SKILL.md')}
for path in skills.rglob('*'):
    if path.is_file() and re.search(
        r'^<<<<<<< local', path.read_bytes().decode(errors='replace'), re.M
    ):
        raise AssertionError(f'{path}: unresolved merge conflict marker')
assert expected == actual, f'skill inventory mismatch: {expected ^ actual}'
assert set(manifest['previous_install']) <= actual, 'previously installed skills lost'
assert len(list(skills.rglob('SKILL.md'))) == len(actual), (
    'nested skills would be discovered twice'
)
assert (skills / 'pstack/LICENSE').is_file()
for name in sorted(actual):
    path = skills / name / 'SKILL.md'
    text = path.read_text()
    assert text.startswith('---\n'), path
    frontmatter, body = text.split('---', 2)[1:]
    assert f'\nname: {name}\n' in frontmatter, path
    assert re.search(r'^description: .+', frontmatter, re.M), path
    assert 'runtime.md)' in body, f'missing runtime adapter: {path}'
    assert not re.search(
        r'^(mode|icon|color|reminder|disable-model-invocation):', frontmatter, re.M
    ), path
assert 'pending' not in manifest['upstream'], (
    'sources.json holds an unresolved upstream update (upstream.pending)'
)


def slug(heading):
    heading = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', heading)
    return re.sub(
        r'[^\w\- ]', '', re.sub(r'[`*]', '', heading).strip().lower()
    ).replace(' ', '-')


def anchors(text):
    """GitHub's heading slugs (repeats get -1, -2) and explicit <a id>/<a name> anchors, outside code fences."""
    found, seen, fence = set(), {}, None
    for line in text.split('\n'):
        opening = re.match(r' {0,3}(`{3,}|~{3,})', line)
        if opening:
            fence = (
                None if fence == opening.group(1)[0] else fence or opening.group(1)[0]
            )
            continue
        if fence:
            continue
        heading = re.match(r'#{1,6}\s+(.*?)(?:\s+#+)?\s*$', line)
        if heading:
            name = slug(heading.group(1))
            found.add(name if name not in seen else f'{name}-{seen[name]}')
            seen[name] = seen.get(name, 0) + 1
        found.update(
            a.lower()
            for a in re.findall(r'<a\s+(?:id|name)=[\"\']([^\"\']+)[\"\']', line)
        )
    return found


for path in skills.rglob('*.md'):
    for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if re.match(r'\w+://|mailto:', target) or any(
            char in target for char in '<>*` '
        ):
            continue
        if target == 'url':  # Citation template in why's synthesizer contract.
            continue
        file, _, fragment = target.partition('#')
        linked = path.parent / file if file else path
        assert linked.exists(), f'{path}: missing {target}'
        if fragment and linked.suffix == '.md':
            assert unquote(fragment).lower() in anchors(linked.read_text()), (
                f'{path}: no heading for {target}'
            )
print(
    f'{len(actual)} skills checked; all {len(manifest["previous_install"])} previous skills covered'
)
