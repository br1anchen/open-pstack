> Historical session artifact, imported 2026-10-25. Read [the handoff](../../HANDOFF.md) first for current status and remote execution instructions. Host-local paths and workflow commands below describe the original run.

# Brief 021: context-references

playbook: feature
timebox: 180
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: plan-019.md
risk: escalated
profiles: pstack, pstack-docs
verify: unit by escalated verifier
standing: /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114/standing-orders.md
report: /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114/reports/021-context-references.md

## Goal

A migration's references follow the sections they point at: links inside moved text and every `[[rewrite]]` are rebased through the old-to-new anchor map, `preview` lists every reference to a moved section that nothing covers as `unresolved:`, and `kitchen.py context inventory` reports a repo's documents, their inbound references and its coverage limits. This is plan 019's step 1b (amendments C8, C11, C13).

## Scope

may write:
- skills/pstack-kitchen/scripts/context.py
- skills/pstack-kitchen/scripts/kitchen.py — the `context` subcommand: `inventory`, and preview's `unresolved:` output
- tests/test_pstack_context.py
- tests/fixtures/context/**
- tests/SPEC.md

must not write:
- skills/pstack-kitchen/scripts/playbooks.py
- skills/pstack-pair/**
- tests/pstack-pair/golden/**

## Steps

The contract is plan 019 with the amendments in reviews/019-context-migration.md; read C8 and C11 first. Unit 020 (context.py at HEAD) built the loader, guards, cuts, `slug`, `render` and `preview`; reuse them.

1. References. In context.py, a fence- and inline-code-aware scanner over a file's bytes, `references(path, data) -> list[Ref]`. `Ref` is a dataclass: path, 1-based line, column span, kind (`inline`, `image`, `definition`, `text`), target path, fragment, and raw text. It recognizes these in Markdown: inline links `[t](target)` and images `![a](target)` (with optional `"title"` and angle-bracket targets), reference definitions `[id]: target`, and bare `#fragment` targets. In TOML and plain text it recognizes `path` and `path#anchor` strings naming a tracked file (exact path match only). URLs with a scheme (`http:`, `mailto:` and others) are not local references and are skipped. A reference inside a fence or an inline code span is skipped in Markdown. Resolution: `resolve(ref, files) -> (path, anchor) | None`, relative to the referring file's directory for Markdown and to the repo root for TOML and text. Tests on a fixture file holding every recognized form plus a fenced and a code-span lookalike, and a URL. Commit.
2. Anchor map and rebasing. `anchor_map(migration, base_files, outputs) -> dict[(old_path, old_anchor), (new_path, new_anchor)]` from the source at the base and the rendered destinations (C8: document-scoped suffixes on both sides; a heading kept in the index keeps its old anchor there, but the map points at its new home). In `render`, rebase every reference inside moved text by its resolved target through the map, so it resolves from the destination's directory: a relative link to a file outside the move, a `#fragment` to a section that stays in the source (now `<index>#anchor`), and a `#fragment` to another moved section (its destination). Reference definitions (C6 category c): a definition used only by moved text moves with that text; one used by moved text and by text that stays is copied to the end of the destination; one used by nothing stays. Apply each `[[rewrite]]` to its target file's base bytes: exactly the `from` text is replaced by `to`, it must occur exactly once (refuse otherwise, naming the file and the count), and the `to` target must resolve through the map or to a file in the result. Tests: expected files under tests/fixtures/context/expected/ updated and pinned byte for byte, with a link out of a moved section, a fragment to a staying section, a fragment between two moved sections, the three definition cases, and a rewrite in a TOML file; a rewrite whose `from` occurs twice or never is refused. Also add the test from unit 020's note n4: a `requires` entry naming a migration source is accepted. Commit.
3. Unresolved and inventory. `unresolved(migration, base_files, files) -> list[Ref]`: every reference in a tracked file (Markdown, TOML and text files at the base, outside the moved text) whose target is a moved section or a moved file's anchor, and that no `[[rewrite]]` (by path and `from`) or `[[keep]]` (by path and `ref`) covers. `preview` prints `unresolved: <path>:<line>: <raw>` after the diffs, one per reference, and exits 1 when there is any. `--json` adds `"unresolved": [...]`. `inventory [--repo DIR] [--json]`: for each tracked Markdown file, its bytes, est. tokens (chars/4), top-level sections with est. tokens, and inbound references grouped by referring file. It flags `candidate` when a file has three or more top-level sections, over 2000 est. tokens in total, and inbound references to its sections from at least two different files (advisory). A `coverage:` block says which syntaxes were recognized, which file types were scanned, the number of recognized references that resolve to nothing (listed as `broken`, pre-existing), and what is not seen (globs, generated paths, external links) (C11). `inventory --draft <path>` prints a schema-1 migration skeleton for that file: root keys, `base` = HEAD, one `[[file]]` with its sha256, and one commented `[[move]]` per top-level section with its `ancestry`. It writes nothing. Tests: preview's `unresolved:` lines and exit 1 on the fixture without its rewrites, exit 0 with them; inventory text and JSON on the fixture repo; the candidate flag both ways; the draft loads with `load` once its moves are uncommented. Commit.

## Context

- Plan and amendments: plan-019.md, amendments-019.md; consultant advice/019-context-migration.md ("Anchor correspondence", "Risks" on inventory coverage).
- Unit 020's notes (n1-n4) in the store's notes/: the index pointer link is relative to the index's directory; keep that.
- Standing order 58: every subprocess a test starts passes a timeout.

## Acceptance

- `python3 tests/run.py -k context` passes, covering every case named in Steps 1-3, with the expected files compared byte for byte.
- On the fixture repo, `kitchen.py context preview` exits 1 and lists each uncovered reference without the migration's `[[rewrite]]` and `[[keep]]` entries, and exits 0 with them.
- `kitchen.py context inventory` writes nothing (a test in `-k context` checks the fixture repo's status is clean after it).
- playbooks.py unchanged: `git diff --quiet origin/main HEAD -- skills/pstack-kitchen/scripts/playbooks.py`.
- `python3 tests/run.py -k bounded` passes; `python3 tests/spec_gen.py --check` exits 0.

## Verify

```bash
python3 tests/run.py -k context
python3 tests/run.py -k bounded
python3 tests/spec_gen.py --check
git diff --quiet origin/main HEAD -- skills/pstack-kitchen/scripts/playbooks.py
```

## Forbidden

- Writing any repo file (apply is unit 022).
- A global search-and-replace of a file name: a reference is rebased only through its resolved target.
- Estimating or guessing a reference the scanner cannot resolve: it is listed, never rewritten.

## Report

Work the Steps in order. End each in a commit and record it with
`kitchen.sh step /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114 <sha> "<what it does>"`: it runs the touched
profiles' fast gates and the repo's policy check, and records the step only
when they pass. When it exits 4 with `gates: still running`, the step is not
recorded: run the same command again at once, in the foreground, and keep
going until it records; never end your turn on it. On a failure, fix it in a
new commit and run `kitchen.sh step` again with that commit; it tells you when
two failures in a row make this a blocked report. At each step boundary run `kitchen.sh notes /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114` and fix
any open blocking note first, as a fixup commit recorded with `--resolves`.
Append one progress line per change of approach with
`kitchen.sh progress /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114 "<line>"`. When any `kitchen.sh` command prints
`PAUSE:`, stop at that safe point: commit what is verified and report
`partial` with the next step. Before reporting done, run the review skills the
kitchen's `review.self` names on your diff. Write the report file named above
using the pstack-pair report template, then run
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh finish /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114 <report path>`
and do what it prints. For a done report, finish first reruns the Verify
commands above at the report's head:, with the checkout at that commit and
nothing uncommitted, and sends the report only when every one exits 0; exit 4
means they are still running, so run the same finish again at once.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L011 (gate-flaky pstack, layer gate, in skills, active 2026-10-24T09:30:12Z): A gate that fails under load and passes on rerun hides a race: write polled files via temp+rename, wait on a pidfd or fifo before checking a process; never accept the rerun.
