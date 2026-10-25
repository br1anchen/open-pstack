> Historical session artifact, imported 2026-10-25. Read [the handoff](../../HANDOFF.md) first for current status and remote execution instructions. Host-local paths and workflow commands below describe the original run.

# Plan 019: context-migration

round: 1
supersedes: none
contract: 2
store: /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114
risk: escalated: a new kitchen.py command family and a repo-writing migration engine (plan 006 amendment A10)

## Goal

`kitchen.py context inventory | preview | apply | check` moves sections of a repo's Markdown documents into new modules from a reviewed, versioned migration file, keeps every old anchor reachable from the original file (now an index), rewrites the references the file names, and proves the result block by block. Proven when the tool's fixtures pass (including a second apply that changes nothing) and pstack-kitchen-setup's maintain path says when and how to run it.

## Design

Amendment A10 and advice/006-playbook-roles-r3-c1.md (adopted whole) fix the policy; this plan fixes the shape.

**Module.** `skills/pstack-kitchen/scripts/context.py`, stdlib-only, imported by path like playbooks.py. It reuses playbooks.py's Markdown reader (`parse`, `select`, `scan_fences`, `heading_tree`, the fence-aware section readers) and `resolve_source`; it adds no second Markdown parser. kitchen.py gets one `context` subcommand family that parses arguments, calls context.py functions that return data and raise `KitchenError`, and renders text or `--json`, as `playbooks_command` does.

**The migration file** is TOML (read with tomllib; nothing in the tool writes TOML except `inventory --draft`, which prints a skeleton as text). It lives in the repo at `.agents/migrations/<id>.toml` and is committed with the migration it describes.

```toml
schema = 1
id = "standards-split"

[[source]]
path = "CODING_STANDARDS.md"          # repo-relative, resolved like an overlay source
sha256 = "<64 hex of the file bytes>"  # stale -> refused

[[move]]
source = "CODING_STANDARDS.md"
select = "heading:Typed evidence"      # a playbooks selector; heading selectors only in schema 1
ancestry = ["Coding standards", "Typed evidence"]   # the selected heading's full path, checked
to = "docs/standards/typed-evidence.md"
requires = []                           # destination paths whose rules this one depends on

[[move]]
source = "CODING_STANDARDS.md"
select = "heading:Typed evidence/Rust (Engine)"
ancestry = ["Coding standards", "Typed evidence", "Rust (Engine)"]
to = "docs/standards/rust.md"
requires = ["docs/standards/typed-evidence.md"]

[[rewrite]]                             # a reference outside the moved text
path = ".agents/kitchen.toml"
from = "CODING_STANDARDS.md#rust-coach-engine"
to = "docs/standards/rust.md#rust-coach-engine"

[[keep]]                                # a reference left pointing at the index on purpose
path = "docs/adr/0007-standards.md"
ref = "CODING_STANDARDS.md#typed-evidence"
reason = "ADRs are historical records"

writes = ["CODING_STANDARDS.md", "docs/standards/*.md", ".agents/kitchen.toml"]
```

**Cuts.** Each move selects one heading section (the heading line to the next heading of the same or higher level). Two moves overlap only by nesting: when one section contains another, the inner one is cut out of the outer's text and the outer keeps, where the inner stood, its heading line and a pointer line. Any other overlap, a destination named by two sources, a destination that exists and is not this migration's earlier output, a path that escapes the repo, a stale sha256, an ancestry mismatch, or a write outside `writes` is refused before anything is rendered.

**Rendering.** A destination file is the moved section's bytes with its heading levels shifted so its top heading is `#`. That shift is the one transformation the proof allows besides link rewrites. A `requires` list renders as one line under the title: `Read first: [a](a), [b](b).` Relative links, images and reference definitions inside moved text are recomputed from the new directory. A reference definition used by moved text and by text that stays is copied, not moved. Each such rewrite is listed. The source becomes an index: its preamble and every section not moved stay byte for byte. Every heading of moved text stays as a heading line (so its anchor, including a duplicate suffix, resolves as before), followed by one line `Moved to [<dest>](<dest>#<anchor>).` Anchors use GitHub's slug rules, one function in context.py.

**Commands.**
- `inventory [--draft]`: tracked Markdown files (git ls-files) with bytes, est. tokens, top-level sections and sizes, and inbound references from tracked files and the host kitchen (path or path#anchor in Markdown links, TOML strings, plain text). It flags a candidate when a file holds three or more top-level sections over 2k tokens in total and inbound references to its sections come from different files. `--draft` prints a migration skeleton for one file. Writes nothing.
- `preview <migration>`: validates, renders every output in memory, and prints the unified diff of each planned write, then every reference to a moved anchor that no `[[rewrite]]` or `[[keep]]` covers (`unresolved:`). Exit 1 when anything is unresolved. Writes nothing.
- `apply <migration>`: preview's checks, then stages every output in a temporary directory, runs `check` against the staged tree, and only then writes the planned files (temp file plus rename each). When every planned file already holds its rendered bytes, it prints `no change` and exits 0. When some do and others hold the source state, it completes the rest. When a planned file holds neither (edited by hand, or uncommitted work), it refuses and names the file. It never commits.
- `check <migration>`: against the working tree, where the migration's sources now hold the index, writes a proof report (`--json`): each original block (from the source as committed at the migration's sha256, read with `git show` by blob hash or from a `--base REV`) mapped to its destination and line range, equal byte for byte after the allowed transformations; order and ancestry kept; every local reference that resolved before resolves after, and every old anchor resolves in the index; references that were broken before are listed as `pre-existing`, not counted; every overlay source and kitchen.toml policy source still resolves (kitchen.py's overlay checks). Exit 1 on any failure.

**Skill.** pstack-kitchen-setup's section 0 (maintain) gains a context step: run `inventory` only when asked or when it flags a candidate; split only a document of independently owned rules for different readers; index coherent or small documents in place; never split ADRs; never migrate tracked team docs from a host kitchen. The human approves the preview. The AGENTS.md routing edit and overlay drafts (unactivated: `applies = []`) are the skill's work, reviewed as semantic changes, not the tool's.

## Alternatives and trade-offs

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| Migration file in TOML, committed under .agents/migrations | reviewable, same reader as overlays, a record of what moved | the tool cannot round-trip TOML; skeleton printed as text | yes |
| JSON migration file | the tool can write it | harder to review and edit by hand | |
| Selectors of every playbooks kind | finer cuts | non-heading cuts leave no anchor to preserve; more proof cases | no (heading only in schema 1) |
| Nested moves allowed, other overlaps refused | rust.md out of typed-evidence, as A10 asks | one more rendering rule | yes |
| Keep moved text verbatim (no heading shift) | byte identity | destination files open at `###` | no |
| Engine inside kitchen.py | one file | kitchen.py is already 4k lines; A10 asks for a focused module | no |

The TOML file keeps the migration reviewable and committed beside its result. Heading-only cuts keep the anchor guarantee checkable. A single allowed transformation, the heading shift, keeps the byte proof strict.

## Steps

1. Reader, migration file and preview: context.py with the migration loader and validator, the slug function, cut and render (index plus destinations, nested moves, relative-link rewrite), reference scan, `preview`; kitchen.py `context preview` and `inventory`. Fixtures under tests/fixtures/context/: a repo with nested headings, duplicate headings, a fenced heading lookalike, relative links, an image, reference definitions shared and not shared, a kitchen.toml and an overlay naming an anchor. Tests: each refusal (stale sha, ancestry, overlap, collision, escape, outside writes), the rendered bytes of index and destinations, unresolved references. Commit per sub-step.
   playbook: feature
2. Apply and check: staging, check before write, no-op rerun, completing partial output, refusing a hand-edited planned file; `check` and its proof report (block map, link and anchor resolution, pre-existing broken links, overlay and policy sources). Tests: apply twice, partial output, hand-edited refusal, check failing on a dropped block, a reordered block and a broken anchor. Commit per sub-step.
   playbook: feature
3. Skill and docs: pstack-kitchen-setup section 0's context step and a reference page for the migration file and commands; kitchen-toml.md's pointer to it; SKILL.md's command list. check-pstack.py passes.
   playbook: refactoring

## Verification

The fixture repo is migrated end to end in a test: preview, apply, check, a second apply that says `no change`, and kitchen.py validate on the result. The ChenChess pilot (plan 006 step 8) is the real artifact and runs in that repo as a separate run.

## Risks

- Moved rules that lose the context of their parent section. `requires` and the kept heading path only partly cover this; the skill's review of the routing is the remaining guard.
- Reference scanning in plain text and TOML finds what it can match. Anything it cannot match goes to `unresolved` for a human, never into a global replace.
- GitHub slug rules differ in edge cases (emoji, punctuation). The fixtures pin the cases ChenChess uses.

## Open questions

- Sidekick: does playbooks.py's `heading_tree` give each heading's end at the next heading of the same or higher level, and its duplicate-free path, as the cut needs? Say what must change there, if anything (a change there is in scope for step 1).
- Consultant: is a byte proof with the heading shift as the only allowed transformation strict enough, or does it need one more allowed transformation that I have missed?

## Disagreements resolved

none
