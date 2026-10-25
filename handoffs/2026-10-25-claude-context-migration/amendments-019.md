> Historical session artifact, imported 2026-10-25. Read [the handoff](../../HANDOFF.md) first for current status and remote execution instructions. Host-local paths and workflow commands below describe the original run.

# Review 019: context-migration

verdict: agreed
report: advice-019.md
head: 8b3d4b3f6c46a7ace805186216fc07652b8efac9

## Amendments

Plan 019 holds with these amendments; where they differ from the plan, they win.

C1. Reader guards, in context.py (playbooks.py unchanged): refuse a source with an unclosed fence (naming its line), with a `\r` byte (schema 1), or with a setext heading (a text line followed by a line of `=` or `-` only, outside fences and frontmatter). Frontmatter is skipped for headings (scan from `frontmatter_end`) and kept in the index byte for byte.

C2. Selection: a move names its section by `ancestry` (exact equality with the heading's full path) and, when several headings share that path, `occurrence = N` (1-based, document order); an ambiguous ancestry without `occurrence`, or an `occurrence` out of range, is refused. The `select` key is dropped from schema 1.

C3. Baseline: the migration records `base = "<commit>"`, and a `[[file]]` entry with `path` and `sha256` for every existing file it reads or changes (sources and rewrite targets). Every input is read with `git show <base>:<path>` and its sha256 checked; every output is rendered from those inputs alone, never from the working tree. preview, apply, a second apply and check all reconstruct from the same base. Refused with a clear message: no `base`, a base that does not resolve, a path missing at the base, a sha256 mismatch.

C4. Apply states: each planned file has a pre-image (its base blob, or absent for a new destination) and a post-image (the render). Before any write, every planned file must equal its pre-image, its post-image, or be absent where its pre-image is absent; otherwise apply refuses and names the file, writing nothing. It then writes only the files not yet at their post-image (temp file plus rename each); all at post-image prints `no change`. A multi-file apply is recoverable, not atomic: an interrupted apply leaves files at pre- or post-image, and a rerun completes it. Files outside `writes` are never touched. Tested with a fixed failure hook (an environment variable that stops apply after N writes), no sleeps.

C5. Byte fidelity: render from the original bytes, split keeping line endings, never from `text_of`. A moved section owns its lines from its heading line to the line before the next heading of the same or higher level, untrimmed, so the blank lines after it move with it. The final newline state of each slice is kept.

C6. Proof categories: every line of every output is exactly one of (a) an original line, at its canonical location, changed only by an enumerated edit (a parsed heading's `#` count, a listed link-target rewrite); (b) generated: the `Read first:` line, the pointer line under each kept heading, the stub left in a nesting parent; (c) a copied reference definition with its source line. Every original line is accounted for once in category (a). Anything else fails `check`.

C7. `requires`: rendered as links relative to the destination; each path must be a destination of this migration or a file at the base; a cycle is refused.

C8. Anchors: the tool computes, from the source at the base, each heading's old anchor (GitHub slug rules over the rendered heading text, with the document-scoped duplicate suffix) and, after rendering, its new anchor in its destination. References are rebased by their resolved target through that map, not by directory arithmetic: links inside moved text (including a `#fragment` to another moved section), and every `[[rewrite]]`. The index keeps every heading line of moved text in its original order, so every old anchor, suffixes included, still resolves; `check` verifies it.

C9. Schema: root keys (`schema`, `id`, `base`, `writes`) come before the array tables. `writes` is exact paths, no globs, and must equal the set of sources, destinations and rewrite targets. The documented sample is loaded by a test.

C10. Consumers: `check` resolves every kitchen.toml policy or review source and every overlay source that names a migrated file through the anchor map, and fails one that lands on an index pointer, unless the migration rewrites it.

C11. Inventory coverage: it says which syntaxes it recognizes (Markdown inline links and images, reference definitions, `path` and `path#anchor` strings in TOML and plain text) and which files it scanned (tracked Markdown and TOML, and the host kitchen), lists recognized but ambiguous references, and states what it cannot see (globs, generated paths, external links) as coverage limits. The candidate flag is advisory.

C12. Skill: `inventory` is read-only and may run in any maintain pass. A migration runs when the owner asks for it or accepts the concrete candidate proposal in the session; an owner who already approved that exact migration is not asked again. Overlay drafts go to `.agents/playbooks/drafts/<id>.toml`, which `load_overlays` does not read (it globs `*.toml` in the directory itself); step 3 pins that with a test. The routing edit to AGENTS.md is a separate, reviewed semantic change after the mechanical move, followed by `check` again.

C13. Units: plan step 1 lands as two briefs: (1a) loader, guards, validation, cut and render as pure functions over a base file map, and `context preview` (the diff); (1b) the reference scanner, the anchor map and rebasing, `unresolved:` in preview, and `inventory`. Step 2 (apply, check) and step 3 (skill, docs) as planned.

## Checked

- Sidekick response reports/019-context-migration.md (object: unclosed fence, frontmatter, CRLF, `/` and duplicate headings, unlisted generated lines, sha256 is no git locator) and consultant advice advice/019-context-migration.md (object: immutable baseline, apply pre/post images, proof categories, anchor map, schema order, consumer check, inventory coverage, skill wording). Both say the master can carry them as amendments; the two agree, and none changes the plan's direction (focused module, TOML migration, heading-only cuts, staged apply, block proof). No round 2.

## Blocking

## Follow-ups

- The ChenChess pilot (plan 006 step 8) runs the tool on CODING_STANDARDS.md in that repo as a separate run.
