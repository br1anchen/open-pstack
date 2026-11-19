"""The secret scanner behind the git store's write path.

No secret or credential may land in a kitchen store: the store branch lives in the work repository, which may be public. `scan` reads files or
texts and returns where each credential-shaped line is, never what it says. The built-in rules are the check; when `gitleaks` is on PATH its
findings are merged in. `scan_store` walks a whole store, for `store-to-git` (plan 021 block 3) to call before its first push.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import NamedTuple

# The one sentence every role prompt, template and reference carries, so the write path's refusal is rarely the first anyone hears of it.
REDACT_RULE = (
    'Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; '
    'write `[REDACTED: <kind>]`.'
)
ALLOW = re.compile(r'pstack-allow-secret:\s*(\S.*)')


class Finding(NamedTuple):
    """Where a credential-shaped line is. The matched text is never kept."""

    path: str
    line: int
    kind: str


class Exemption(NamedTuple):
    """A line a `pstack-allow-secret: <reason>` marker let through, for the review to read."""

    path: str
    line: int
    kind: str
    reason: str


class Rule(NamedTuple):
    kind: str
    pattern: re.Pattern
    # group `v` holds the part a placeholder replaces; a rule without it matches only a literal credential.


def _rule(kind, regex):
    return Rule(kind, re.compile(regex))


_VALUE = r'(?P<v>[^\s\'"`<>(){}\[\],;]*)'
_KEY_NAME = r'(?<![A-Za-z0-9_])(?:[A-Z][A-Z0-9]*_)*(?:KEY|TOKEN|SECRET|PASSWORD)'

RULES = (
    _rule('private-key', r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    _rule('github-token', r'\bgh[pousr]_(?P<v>[A-Za-z0-9]{36,})'),
    _rule('github-token', r'\bgithub_pat_(?P<v>[A-Za-z0-9_]{22,})'),
    _rule(
        'aws-key-id', r'\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)(?P<v>[A-Z0-9]{16})\b'
    ),
    _rule('slack-token', r'\bxox[abpr]-(?P<v>[A-Za-z0-9-]{10,})'),
    _rule('stripe-key', r'\b[rs]k_live_(?P<v>[A-Za-z0-9]{16,})'),
    _rule('anthropic-key', r'\bsk-ant-(?P<v>[A-Za-z0-9_-]{20,})'),
    _rule('openai-key', r'\bsk-(?!ant-)(?P<v>(?:proj-)?[A-Za-z0-9_-]{32,})'),
    _rule('google-api-key', r'\bAIza(?P<v>[0-9A-Za-z_-]{35})\b'),
    _rule('gcp-service-account', r'"private_key"\s*:\s*"(?P<v>[^"]*)"'),
    _rule('railway-token', r'\bRAILWAY_(?:API_)?TOKEN\s*=\s*[\'"]?' + _VALUE),
    _rule(
        'jwt',
        r'\beyJ[A-Za-z0-9_-]{5,}\.(?P<v>[A-Za-z0-9_-]{5,})\.[A-Za-z0-9_-]{5,}',
    ),
    _rule(
        'authorization-header',
        r'\bAuthorization\s*[:=]\s*[\'"]?(?:(?:Bearer|Basic|Token)\s+)?(?P<v>[A-Za-z0-9._~+/=-]{8,})',
    ),
    _rule('bearer-token', r'\bBearer\s+(?P<v>[A-Za-z0-9._~+/=-]{16,})'),
    _rule('url-credentials', r'\b[a-z][a-z0-9+.-]*://[^\s/:@<>$]+:(?P<v>[^\s/@]+)@'),
    # `KEY=value` as a .env file writes it; with spaces around the `=` or a colon, only a quoted value counts, so code like `TOKEN = re.compile(...)` passes.
    _rule(
        'env-secret', _KEY_NAME + r'(?:=\s*[\'"]?|\s+=\s*[\'"]|\s*:\s*[\'"])' + _VALUE
    ),
)

# A value that is not a credential: a redaction, an angle-bracket or shell placeholder, a mask, or nothing.
_PLACEHOLDER = re.compile(r'(?:\[REDACTED|<|\$|\*\*\*|xxx)', re.I)
_WORD = re.compile(
    r'value|secret|token|password|pass|key|changeme|example|redacted|none|null|true|false',
    re.I,
)
_MASK = re.compile(r'[x*.-]*', re.I)

# Rules whose value is not a credential unless it carries a digit (a word like `Bearer authentication` is prose).
_NEEDS_DIGIT = {'authorization-header', 'bearer-token'}


def _placeholder(value):
    value = value.strip().strip('\'"`')
    return (
        not value
        or bool(_PLACEHOLDER.match(value))
        or bool(_MASK.fullmatch(value))
        or bool(_WORD.fullmatch(value))
    )


def _kinds(line):
    """The kinds of credential on a line, in rule order."""
    found = []
    for rule in RULES:
        for match in rule.pattern.finditer(line):
            value = match.groupdict().get('v')
            if value is not None and _placeholder(value):
                continue
            if rule.kind in _NEEDS_DIGIT and not re.search(r'\d', value or ''):
                continue
            if rule.kind not in found:
                found.append(rule.kind)
            break
    return found


def _read(root, path):
    full = os.path.join(root, path) if root else path
    try:
        with open(full, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def _gitleaks(root, labels):
    """gitleaks' findings in the files named by labels (paths relative to root), or [] without gitleaks or when it fails: the built-in rules stay the check."""
    exe = shutil.which('gitleaks')
    if not exe or not root or not labels:
        return []
    wanted = set(labels)
    with tempfile.TemporaryDirectory() as tmp:
        report = f'{tmp}/report.json'
        try:
            subprocess.run(
                [
                    exe,
                    'detect',
                    '--no-git',
                    '--no-banner',
                    '--source',
                    root,
                    '--report-format',
                    'json',
                    '--report-path',
                    report,
                    '--exit-code',
                    '0',
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
            with open(report, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError, subprocess.SubprocessError):
            return []
    found = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        rel = os.path.relpath(str(item.get('File', '')), root)
        if rel in wanted:
            found.append(
                Finding(
                    rel,
                    int(item.get('StartLine') or 0),
                    f'gitleaks-{item.get("RuleID", "secret")}',
                )
            )
    return found


def scan(items, root=None, exempt=None):
    """The Findings in items, in the order the items and their lines come. An item is a path (relative to root when root is given) or a
    (label, text) pair. A line holding `pstack-allow-secret: <reason>` is exempt; each exemption is appended to the list `exempt` when one is
    given, and the review reads them. With gitleaks on PATH and a root, its findings in the same files are merged in."""
    found, allowed, labels = [], set(), []
    for item in items:
        label, text = item if isinstance(item, tuple) else (item, _read(root, item))
        if text is None:
            continue
        labels.append(label)
        for number, line in enumerate(text.splitlines(), 1):
            kinds = _kinds(line)
            marker = ALLOW.search(line)
            if kinds and marker:
                allowed.add((label, number))
                if exempt is not None:
                    exempt.extend(
                        Exemption(label, number, kind, marker[1].strip())
                        for kind in kinds
                    )
            else:
                found.extend(Finding(label, number, kind) for kind in kinds)
    seen = {(f.path, f.line) for f in found}
    found.extend(
        f
        for f in _gitleaks(root, labels)
        if (f.path, f.line) not in seen and (f.path, f.line) not in allowed
    )
    return found


def scan_store(store, exempt=None):
    """The Findings in every file of a store that a publish would send: all but the files that stay on one machine. The migration to the git store
    (`store-to-git`) calls this before its first push and stops on a match."""
    from . import gitstore

    rels = []
    for directory, names, files in os.walk(store):
        names[:] = sorted(n for n in names if n != '.git')
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(directory, name), store)
            rule = gitstore.classify(rel)
            if (rule is None or rule.kind != gitstore.LOCAL) and os.path.isfile(
                os.path.join(directory, name)
            ):
                rels.append(rel)
    return scan(rels, root=store, exempt=exempt)
