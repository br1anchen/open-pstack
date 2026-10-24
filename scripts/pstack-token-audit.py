#!/usr/bin/env python3
"""Count the tokens each kitchen role spent, and how much of it was skill documents.

    python3 scripts/pstack-token-audit.py STORE... [--projects DIR] [--json]

Reads Claude Code transcripts (`<projects>/<cwd with / and . turned into ->/<session>.jsonl`)
of the sessions that worked for the named kitchen stores, and prints per role three
measures that are not added together:

- billed: the provider-reported usage, each assistant message once (Claude Code writes
  one record per content block, all with the same message id and usage);
- documents: the delivered size of tool results that read a `skills/<name>/...md` file,
  by file, with chars/4 as an estimate of tokens;
- exposure: an estimate of the context those documents occupied, each load counted over
  the assistant messages that followed it (a repeated load is its own load).

A session is assigned to a role by `verifying.session` in pair.json (`id`, the open
verifier only) or by the role its first prompt names (`prompt`). pi and Codex panes leave
no Claude transcript; their roles are printed as `unmeasured`.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROLES = ('master', 'sidekick', 'consultant', 'verifier')
USAGE_KEYS = (
    'input_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
    'output_tokens',
)
INPUT_KEYS = USAGE_KEYS[:3]
SKILL_DOC = re.compile(r'/skills/([^/]+)/(.+\.md)$')
PLAYBOOK = re.compile(r'^(poteto-mode/playbooks/[^/]+\.md|pstack-tdd/SKILL\.md)$')
ROLE_PROMPT = re.compile(r'\btake the (master|sidekick|consultant|verifier) role\b')
ROLE_PROMPT_LOOSE = re.compile(r'\bas (?:the )?(master|sidekick|consultant|verifier)\b')
STORE_NAMED = re.compile(r'/runs/([\w.-]+)')
BASH_READ = re.compile(
    r'\b(?:cat|sed|head|tail|less|bat|nl)\b[^|;&\n]*?/skills/([^/\s]+)/(\S+?\.md)\b'
)
TRUNCATED = ('<persisted-output>', 'Output too large')


def encode_cwd(path):
    return re.sub(r'[/.]', '-', path)


@dataclass
class Store:
    path: Path
    slug: str
    roots: list[str]
    start: float
    end: float
    verifier_session: str
    kinds: dict[str, str]


@dataclass
class Load:
    name: str  # skill/relative path of the document
    chars: int
    truncated: bool
    after: int = 0

    @property
    def tokens(self):
        return self.chars // 4


@dataclass
class Session:
    path: Path
    cwd: str = ''
    prompt: str = ''
    first: float = 0.0
    last: float = 0.0
    usage: dict[str, dict] = field(default_factory=dict)
    loads: list[Load] = field(default_factory=list)


def read_store(path):
    path = Path(path)
    pair = json.loads((path / 'pair.json').read_text())
    epochs = []
    for line in (path / 'events.tsv').read_text().splitlines():
        cols = line.split('\t')
        if len(cols) > 1 and cols[1].isdigit():
            epochs.append(int(cols[1]))
    roots = [pair[k] for k in ('git_root', 'cwd') if pair.get(k)]
    verifying = pair.get('verifying') or {}
    kinds = {'master': (pair.get('master') or {}).get('kind', 'claude')}
    for role in ('sidekick', 'consultant', 'verifier'):
        kind = (pair.get(role) or {}).get('kind')
        if kind:
            kinds[role] = kind
    return Store(
        path=path,
        slug=pair.get('slug') or path.name,
        roots=list(dict.fromkeys(roots)),
        start=min(epochs, default=0),
        end=max(epochs, default=0),
        verifier_session=Path(verifying.get('session') or '').stem,
        kinds=kinds,
    )


def epoch(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def block_text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict))


def document_read(block):
    """(tool, `skill/relative.md`) for a tool_use block that reads a skill document, else None."""
    tool, args = block.get('name'), block.get('input') or {}
    if tool == 'Read':
        found = SKILL_DOC.search(args.get('file_path', ''))
    elif tool == 'Bash':
        found = BASH_READ.search(args.get('command', ''))
    elif tool == 'Skill':
        return tool, f'{args.get("skill", "").rsplit(":", 1)[-1]}/SKILL.md'
    else:
        return None
    return (tool, f'{found.group(1)}/{found.group(2)}') if found else None


def read_session(path):
    session = Session(path)
    documents = {}  # tool_use id -> (tool, document) that call reads
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:  # the line a live session is still writing
            continue
        kind = record.get('type')
        if kind not in ('user', 'assistant'):
            continue
        if stamp := record.get('timestamp'):
            when = epoch(stamp)
            session.first = session.first or when
            session.last = max(session.last, when)
        session.cwd = session.cwd or record.get('cwd', '')
        message = record['message']
        content = message.get('content')
        if kind == 'assistant':
            merged = session.usage.setdefault(message['id'], {})
            for key, value in (message.get('usage') or {}).items():
                if isinstance(value, int):
                    merged[key] = max(merged.get(key, 0), value)
            for block in content if isinstance(content, list) else []:
                if block.get('type') == 'tool_use' and (read := document_read(block)):
                    documents[block['id']] = read
        elif isinstance(content, str):
            if not record.get('isMeta'):
                session.prompt = session.prompt or content
        else:
            delivered = []  # (document, text) pairs this record brings into the context
            if read := documents.get(record.get('sourceToolUseID')):
                delivered.append(
                    (read[1], block_text(content))
                )  # a Skill's injected text
            for block in content:
                read = documents.get(block.get('tool_use_id'))
                if block.get('type') == 'tool_result' and read and read[0] != 'Skill':
                    delivered.append((read[1], block_text(block.get('content', ''))))
            for name, text in delivered:
                truncated = any(m in text for m in TRUNCATED)
                session.loads.append(
                    Load(name, len(text), truncated, len(session.usage))
                )
    for load in session.loads:
        load.after = len(session.usage) - load.after
    return session


def candidate_dirs(store, projects):
    """Project dirs of the store's repo, and of verifier scratch worktrees named for its slug."""
    prefixes = [encode_cwd(root) for root in store.roots]
    scratch = f'pstack-scratch-runs-{encode_cwd(store.slug)}-'
    for directory in sorted(Path(projects).iterdir()):
        if directory.is_dir() and (
            scratch in directory.name
            or any(directory.name.startswith(p) for p in prefixes)
        ):
            yield from sorted(directory.glob('*.jsonl'))


def in_store_tree(store, session):
    """Whether the session ran in the store's repo or in a verifier scratch worktree for its slug."""
    if f'/pstack-scratch/runs-{store.slug}/' in session.cwd:
        return True
    return any(session.cwd == r or session.cwd.startswith(f'{r}/') for r in store.roots)


def prompt_role(prompt):
    found = ROLE_PROMPT.search(prompt) or ROLE_PROMPT_LOOSE.search(prompt)
    return found.group(1) if found else None


def names_store(store, prompt):
    if str(store.path) in prompt:
        return True
    slugs = set(STORE_NAMED.findall(prompt))
    return (
        store.slug in slugs
        or re.search(rf'(?<![\w-]){re.escape(store.slug)}(?![\w-])', prompt) is not None
    )


def names_other_store(store, prompt):
    return any(slug != store.slug for slug in STORE_NAMED.findall(prompt))


def classify(store, session):
    """(role, how) for a counted session, ('other', '') for another store's, else ('ambiguous', reason)."""
    if session.path.stem == store.verifier_session:
        return 'verifier', 'id'
    named = names_store(store, session.prompt)
    if not named and names_other_store(store, session.prompt):
        return 'other', ''
    if not store.start <= session.first <= store.end:
        return 'ambiguous', 'first record outside the store span'
    role = prompt_role(session.prompt)
    if not named:
        return 'ambiguous', 'prompt names no store'
    if role is None:
        return 'ambiguous', 'prompt names no role'
    return role, 'prompt'


def blank_role():
    return {
        'sessions': 0,
        'messages': 0,
        'billed': dict.fromkeys(USAGE_KEYS, 0),
        'documents': {},
        'playbooks': {},
        'exposure_tokens': 0,
        'truncated_loads': 0,
    }


def add_session(entry, session):
    entry['sessions'] += 1
    entry['messages'] += len(session.usage)
    for usage in session.usage.values():
        for key in USAGE_KEYS:
            entry['billed'][key] += usage.get(key, 0)
    for load in session.loads:
        table = entry['playbooks'] if PLAYBOOK.match(load.name) else entry['documents']
        row = table.setdefault(
            load.name, {'loads': 0, 'chars': 0, 'tokens': 0, 'truncated': 0}
        )
        row['loads'] += 1
        row['chars'] += load.chars
        row['tokens'] += load.tokens
        row['truncated'] += load.truncated
        entry['exposure_tokens'] += load.tokens * load.after
        entry['truncated_loads'] += load.truncated


def audit(store_path, projects):
    store = read_store(store_path)
    roles = {}
    assigned, ambiguous, other = [], [], 0
    for path in candidate_dirs(store, projects):
        session = read_session(path)
        if not in_store_tree(store, session):
            continue
        role, how = classify(store, session)
        if role == 'other':
            other += 1
        elif role == 'ambiguous':
            ambiguous.append({'session': path.stem, 'reason': how})
        else:
            assigned.append({'session': path.stem, 'role': role, 'how': how})
            add_session(roles.setdefault(role, blank_role()), session)
    unmeasured = [
        {'role': r, 'kind': k} for r, k in store.kinds.items() if k != 'claude'
    ]
    return {
        'store': str(store.path),
        'sessions': assigned,
        'ambiguous': ambiguous,
        'other_stores': other,
        'roles': {r: roles[r] for r in ROLES if r in roles},
        'unmeasured': unmeasured,
    }


def render_role(role, entry):
    billed = entry['billed']
    input_total = sum(billed[k] for k in INPUT_KEYS)
    lines = [
        f'{role}: {entry["sessions"]} sessions, {entry["messages"]} assistant messages',
        '  billed (provider usage, each message once): '
        f'input {billed["input_tokens"]}, cache creation {billed["cache_creation_input_tokens"]}, '
        f'cache read {billed["cache_read_input_tokens"]}, output {billed["output_tokens"]}',
    ]
    for label, table in (
        ('documents', entry['documents']),
        ('playbooks', entry['playbooks']),
    ):
        if not table:
            lines.append(f'  {label}: none')
            continue
        total = sum(r['tokens'] for r in table.values())
        lines.append(f'  {label} loaded (est. tokens = chars/4): {total}')
        for name, row in sorted(table.items()):
            flag = f', {row["truncated"]} truncated' if row['truncated'] else ''
            lines.append(
                f'    {name}: {row["loads"]} loads, {row["chars"]} chars, ~{row["tokens"]} tokens{flag}'
            )
    share = (
        f'{100 * entry["exposure_tokens"] / input_total:.1f}%' if input_total else 'n/a'
    )
    lines.append(
        f'  exposure (estimate): ~{entry["exposure_tokens"]} token-messages = {share} of input '
        f'(input + cache creation + cache read); truncated loads: {entry["truncated_loads"]}'
    )
    return lines


def render(result):
    lines = [f'store: {result["store"]}']
    for row in result['sessions']:
        lines.append(f'  session {row["session"]}: {row["role"]} (by {row["how"]})')
    for row in result['ambiguous']:
        lines.append(f'  ambiguous {row["session"]}: {row["reason"]}, not counted')
    if result['other_stores']:
        lines.append(f'  {result["other_stores"]} sessions name another store, skipped')
    for role, entry in result['roles'].items():
        lines.extend(render_role(role, entry))
    for row in result['unmeasured']:
        lines.append(f'unmeasured: {row["role"]} ({row["kind"]})')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument(
        'stores', nargs='+', type=Path, help='kitchen store directories'
    )
    parser.add_argument(
        '--projects', type=Path, default=Path.home() / '.claude' / 'projects'
    )
    parser.add_argument(
        '--json', action='store_true', help='print the same data as JSON'
    )
    args = parser.parse_args(argv)
    stores = []
    status = 0
    for store in args.stores:
        if (store / 'pair.json').is_file():
            stores.append(store)
        else:
            print(
                f'pstack-token-audit: {store}: not a kitchen store (no pair.json)',
                file=sys.stderr,
            )
            status = 2
    results = [audit(store, args.projects) for store in stores]
    if args.json:
        print(json.dumps(results, indent=2))
    elif results:
        print('\n\n'.join(render(r) for r in results))
    return status


if __name__ == '__main__':
    sys.exit(main())
