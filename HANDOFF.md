# Claude session handoff — 2026-10-25

Continue the context migration tool in `open-pstack`. The starting code is
`e1850be917498b34faa3a4bfb2b63c0fd80ae956` on `main`. The working tree was clean
before this documentation import, and the local `origin/main` tracking ref pointed
at that commit. No remote fetch was performed.

## Resume here

1. Read [brief 021](handoffs/2026-10-25-claude-context-migration/brief-021.md)
   and [amendments C1–C13](handoffs/2026-10-25-claude-context-migration/amendments-019.md).
   The amendments override the older schema and wording in
   [plan 019](handoffs/2026-10-25-claude-context-migration/plan-019.md).
2. Review scanner commit `8fb34a5..e1850be` before building on it. The master
   requested probes for nested brackets in link text, parentheses in targets,
   escaped brackets, multiline links, and `./` and `../` paths. That review
   stopped at the Claude usage limit; there is no completed scanner verdict.
3. Complete brief 021 steps 2 and 3: anchor correspondence and reference rebasing,
   reference-definition ownership, explicit rewrites, unresolved-reference output,
   and `inventory` with JSON, coverage reporting and a migration draft.
   Use the brief's exact fixtures and acceptance cases.
4. Verify and review the completed unit. Then implement plan 019 step 2
   (`apply` and `check`, intended unit 022), followed by step 3 (setup skill and
   command/schema docs). Those later briefs had not been written.

The imported artifacts are historical evidence, not commands to run against the
original host. Work from this checkout and its `AGENTS.md`; the remote agent does
not need the old kitchen store, Claude transcripts, pane IDs or host configuration.
The original brief's `step`, `notes`, `finish`, report paths and timebox refer to
that local run. Record equivalent commits, checks and review evidence in the
remote environment. Start a fresh workflow if orchestration is needed.

## Completed and remaining work

The larger run, `feedback-1114`, fixed branch-switch scope detection and advisory
rotation at landing boundaries. It then implemented composed playbooks delivered
to each kitchen role, repository overlays, role delegation documentation, and
token auditing with child-agent attribution. That code is already in history.
The remaining project is plan 019's deterministic document migration engine.
The later ChenChess migration pilot belongs to a separate run in that repository.

Unit 020 was accepted and pushed at `8fb34a51b91732332e08613ee3cd521c1ff93d66`.
It provides the schema-1 loader, immutable Git baseline and SHA-256 validation,
Markdown guards, section selection/cutting, heading slugs, index/destination
rendering and a read-only `context preview`. See its
[report](handoffs/2026-10-25-claude-context-migration/report-020.md) and
[acceptance review](handoffs/2026-10-25-claude-context-migration/review-020.md).

Unit 021 step 1 is committed as `e1850be`. It adds `Ref`, `references` and
`resolve`, scanner fixtures, and scanner/resolution tests. The local step ledger
records this commit as step 1. Brief 021 has no final report or acceptance review.
The scanner exists, but link rebasing, explicit rewrite application, unresolved
reporting, inventory, apply and check remain unfinished. Preview currently leaves
rewrite targets unchanged.

## Contracts and code pointers

- Engine: `skills/pstack-kitchen/scripts/context.py`; CLI adapter:
  `skills/pstack-kitchen/scripts/kitchen.py`. Reuse `playbooks.py` readers;
  brief 021 keeps that file and `skills/pstack-pair/**` outside its write scope.
- Tests: `tests/test_pstack_context.py`, `tests/fixtures/context/`, and
  `tests/SPEC.md`. Tests compare rendered fixture bytes exactly.
- Inputs come from the recorded base commit. Every existing input/change has a
  `[[file]]` hash. Root `writes` enumerates exact output paths. Select by full
  `ancestry` plus an optional 1-based `occurrence`; the old plan's `[[source]]`,
  `select`, and globbed writes are superseded by the amendments.
- Old and new anchors have document-scoped duplicate suffixes. Rebase through
  resolved targets, and preserve all old index anchors in order. Index pointer
  links are relative to the index directory.
- Apply must recognize exact pre-images and post-images before writing, recover
  an interrupted partial apply, and make the second apply a no-op. Check must
  account for original, generated and copied-definition lines and validate policy
  and overlay consumers. Read C4, C6 and C10 before implementing these commands.
- [Consultant advice](handoffs/2026-10-25-claude-context-migration/advice-019.md)
  explains the immutable baseline, byte provenance, anchor map and coverage limits.

## Known follow-ups

Unit 020's accepted review left these small issues open: setext headings inside
list items can evade refusal; thematic breaks after lists can be refused as
setext; slug handling differs from GitHub for HTML entities and combining marks;
reader refusals use inline bytes rather than fixture files; no test pins index
pointer paths from a subdirectory. Brief 021 explicitly includes the missing test
for `requires` naming a migration source. These are follow-ups, not completed fixes.

Tests must be deterministic, use the fake herdr for workflow drives, and give
every test subprocess a timeout. Runtime dependencies remain Python stdlib only,
Python 3.11+. Use the repository's required checks before committing; `uv check`
requires uv 0.12.3 or later.

## Import provenance and validation

The source was the most recently modified top-level Claude transcript for this
repository, master session `a39f9bf0-61e1-430d-98ab-25013d978a3d`, corroborated by
sidekick session `61c7f9f7-0a48-4cd2-8e41-edf731ebcb74`. Work stopped around
2026-10-25 01:07 UTC at the Claude usage limit. Later transcript modification times
do not represent later implementation progress. The existing host-local
`session-handoff.md` and `status.md` still described unit 014 and were stale.

Supporting documents were imported from the `feedback-1114` store. References
between included artifacts were made portable; other local paths remain only as
historical context. Raw transcripts, host configuration and credentials are not
part of this import.

Import validation: all 1,314 tests passed on the host; the sandbox run's sole
failure was its process-ownership check, which also passed individually on the
host. Inventory/link validation, lock consistency, ruff formatting and lint,
type checking, and `tests/spec_gen.py --check` passed. The uv checks used uv
0.12.24, offline, with a temporary writable cache. `tests/SPEC.md` was regenerated
to refresh a stale test-reference count and its generated commit marker; runtime
code is unchanged by this handoff import.
