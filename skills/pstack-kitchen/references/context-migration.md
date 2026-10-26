# Context migration

`kitchen.py context` splits a long Markdown document into modules from a reviewed migration file, and proves the result. It moves sections only: no text is rewritten, and every old heading anchor still resolves. The tool lives in `scripts/context.py`; its commands are rendered by `scripts/kitchen.py` (`context_command`). When to run it is [pstack-kitchen-setup section 0](../../pstack-kitchen-setup/SKILL.md).

## The owner's sequence

1. `kitchen.py context inventory` lists the repo's Markdown documents, their sections, who references them and what the scanner cannot see. It reads git and writes nothing.
2. `kitchen.py context inventory --draft <file>` prints a migration skeleton for one document: the root keys, its `[[file]]` entry and one commented `[[move]]` per top-level section (`context.draft`). It writes to stdout; redirect it yourself.
3. Write `.agents/migrations/<id>.toml` from the skeleton: fill in `to` and `writes`, add `[[rewrite]]` and `[[keep]]` entries.
4. `kitchen.py context preview <migration>` prints the diff of every file the migration would write, and a line for each reference to a moved section that nothing covers. The owner approves this diff.
5. `kitchen.py context apply <migration>` writes the files.
6. Re-stamp the `reviewed` digest of each overlay part whose source moved. No command writes a digest: `check` prints each overlay part as `reviewed X, now Y`, and `kitchen.py validate` reports `digest mismatch (<select>): reviewed X, current Y`. Copy the new value into the overlay by hand. A moved section's heading level is shifted, so its selection's digest changes (on the fixture, `heading:Typed evidence` went from `8240ebd8e301` to `f8a9219f93e3`).
7. `kitchen.py context check <migration>` proves the working tree.
8. Commit the migration and its result together.
9. Make the AGENTS.md routing edit as a separate, reviewed change, then run `check` again.

## The migration file

`.agents/migrations/<id>.toml`, schema 1, committed with its result. `context.parse_migration` reads it and refuses anything else. The root keys (`schema`, `id`, `base`, `writes`) come before the first array table; after one, TOML makes them keys of that table and the loader says so. The sample below is `tests/fixtures/context/sample.toml` (named, not linked: installed skills cannot resolve links into `tests/`); a test pins the two byte for byte and loads it.

```toml
schema = 1
id = "standards-split"
base = "0123456789abcdef0123456789abcdef01234567"
writes = [
  "STANDARDS.md",
  "docs/standards/typed-evidence.md",
  "docs/standards/rust.md",
  "docs/standards/testing-notes.md",
  ".agents/kitchen.toml",
]

[[file]]
path = "STANDARDS.md"
sha256 = "0000000000000000000000000000000000000000000000000000000000000001"

[[file]]
path = ".agents/kitchen.toml"
sha256 = "0000000000000000000000000000000000000000000000000000000000000002"

[[move]]
source = "STANDARDS.md"
ancestry = ["Coding standards", "Typed evidence"]
to = "docs/standards/typed-evidence.md"

[[move]]
source = "STANDARDS.md"
ancestry = ["Coding standards", "Typed evidence", "Rust (Engine)"]
to = "docs/standards/rust.md"
requires = ["docs/standards/typed-evidence.md"]

[[move]]
source = "STANDARDS.md"
ancestry = ["Coding standards", "Testing", "Notes"]
to = "docs/standards/testing-notes.md"

[[rewrite]]
path = ".agents/kitchen.toml"
from = "STANDARDS.md#typed-evidence"
to = "docs/standards/typed-evidence.md#typed-evidence"

[[keep]]
path = "docs/adr/0007-standards.md"
ref = "../../STANDARDS.md#typed-evidence"
reason = "ADRs are historical records"
```

- **Root keys.** `id` matches `[A-Za-z0-9][A-Za-z0-9._-]*`. `base` is the commit every input is read from (`ROOT_KEYS`). Paths are repo-relative with no `..`, no leading `/` and no backslash (`check_path`).
- **`writes`.** Exact paths, no globs, none twice, and equal to the set of sources, destinations and rewrite targets (`check_writes`).
- **`[[file]]`** (`FILE_KEYS`: `path`, `sha256`). One per existing file the migration reads or changes: every move source and rewrite target. The file is read with `git cat-file` at `base` and refused when its sha256 differs (`read_base`).
- **`[[move]]`** (`MOVE_KEYS`: `source`, `ancestry`, `occurrence`, `to`, `requires`). `ancestry` is the heading's full path and must equal it exactly. `occurrence` is 1-based and needed only when several headings share that ancestry; an ambiguous ancestry without it, or one out of range, is refused (`find`). `requires` lists files the destination needs read first; each must be a destination of this migration or a file at base, and a cycle is refused (`check_requires`). A move whose section nests inside another move's section becomes a nested move: the parent keeps its heading and a pointer.
- **`[[rewrite]]`** (`REWRITE_KEYS`: `path`, `from`, `to`, `count`). Replaces `from` with `to` in a file the migration does not cut. `from` must occur exactly `count` times (default 1); `to` must name an existing file or a destination, and its section must exist in the output (`rewritten`).
- **`[[keep]]`** (`KEEP_KEYS`: `path`, `ref`, `reason`). Names a reference left pointing at the index on purpose. It covers the references in `path` whose written target equals `ref`; they are listed, never failed (`unresolved`). A keep whose `ref` is the written target of no reference in its `path` (a target copied from `--json` instead of the text as written) is reported by `preview` as `unused keep: <path> <ref>` and makes it exit 1 (`unused_keeps`); `apply` refuses it too, before any write. Use it for records that must not change, such as ADRs.

## Commands

All four take `--repo` (default: the current directory) and `--json`. The base is the migration's `base` key; there is no flag for it.

| Command | Writes | Prints |
| --- | --- | --- |
| `context inventory [--draft PATH]` | nothing | documents, sections, inbound references and coverage; or the skeleton |
| `context preview <migration>` | nothing | the diff of each planned file, then `unresolved:`, `unused keep:` and `index-local:` lines |
| `context apply <migration>` | the planned files | `wrote: <path>` per file, `index-local: N`, or `no change` |
| `context check <migration>` | nothing | a line per block, findings, consumers, `proven against:`, then `ok` or `failed: N findings` |

Under `--json` any command that ends with an error prints one object on stdout and nothing on stderr: `{"ok": false, "exit": <code>, "error": "<message>"}`, the message as the text mode prints it after `kitchen: `, with the same exit code. The cases are a migration, base or source that cannot be used (exit 2), a bad `KITCHEN_CONTEXT_STOP_AFTER` (exit 2), a `--repo` that is not a git repository (exit 1), and a refused or stopped `apply` (exit 1). A `preview` with unresolved references or an unused keep and a `check` with findings, or run before `apply`, exit 1 and print their normal JSON instead: `preview` has no `ok` and carries `unresolved` and `unused_keeps`, `check` has `ok` false, and neither has `exit` or `error`. A refused or stopped `apply` adds `written`, the files it wrote before it ended. `check --json` has `walked`, false when the line accounting failed; each consumer that would be `ok` then has `ok` null, not judged, as the text says `not judged (line accounting failed)`.

Only `apply` writes, and it never commits. Exit codes are as `kitchen.py` prints them: 0 done; 1 findings (`check`), unresolved references or an unused keep (`preview`), or a refusal or a stop (`apply`); 2 a migration, base or source that cannot be used.

`KITCHEN_CONTEXT_STOP_AFTER=N` ends an `apply` after N writes. It exists for tests of the interrupted-apply path; owners do not use it.

### What the tool refuses

- A source with an unclosed fence, a carriage return, a setext heading, or a heading indented 1 to 3 spaces (`read`). Schema 1 reads LF files with `#` headings at the start of a line.
- A move whose source has no `[[file]]`, a section moved twice, a destination named twice, a destination that is also a source, or a destination that exists at base (`cuts`).
- A base that does not resolve, a file missing at base, a sha256 mismatch (`resolve_base`, `read_base`).
- `apply` with a planned file at neither its base nor its output state, a planned path under a symbolic link, an unresolved reference, an unused keep, or a plan that does not prove (`apply`). Nothing is written in each case.

### What it renders

- A moved section lands at the top of its destination, its heading shifted to level 1 and its subsections shifted with it (`shift_line`). The blank lines after a section move with it; do not tidy them.
- The source keeps every heading line of moved text, in order, each followed by `Moved to [<path>](<link>#<anchor>).` So every old anchor still resolves.
- A nested move leaves its heading and a pointer in the parent's destination.
- A destination with `requires` gets `Read first: [<path>](<link>).` under its heading.
- A reference link in moved text is rebased to resolve from the destination, through the heading-to-heading anchor map (`anchor_map`, `rebased_target`). Reference definitions used only by moved text move with it; those also used by staying text are copied to the destination (`definition_patches`). A definition whose target is on the line after its label moves only with its own text; a migration that would copy it, or split it from text that uses it, is refused until it is written on one line.

### What `check` proves

Every output line is exactly one of: an original line in its place, changed only by a heading shift, a rebased link target, or an added final newline; a generated line (`Read first:`, a pointer, a kept or nested heading, a blank around them); or a copied reference definition. Every source line is accounted for once (`prove`, `unaccounted`). Then the anchors and references: every moved heading keeps its anchor in the index and lands on a heading of its destination, and a reference that resolved at base still does (`link_proof`).

`check` and `apply`'s own proof read the planned files, `kitchen.toml` and the overlays as they are on disk, and every other file as at base. So editing an overlay, then running `check` again, is the intended sequence.

A planned file edited by hand is foreign: `apply` refuses and `check` reports the line it cannot account for. Restore each foreign file to its base state (for example `git checkout <base> -- <path>`) and apply again, or to its output state. Only if the repo moved on after the migration was written, update `base` and each `[[file]]` sha256, or redraft with `inventory --draft`.

### Consumers

`check` judges the repo's own configuration (`consumer_proof`). Every `path` or `path#anchor` string in `.agents/kitchen.toml` and in `.agents/playbooks/*.toml`, and each overlay part whose repo source is a migrated file, must still name the content: a reference that lands on an index pointer, or whose `select` hits none or several headings, fails unless a `[[rewrite]]` fixes it. What was already broken at base is listed as `pre-existing`, not failed. Each overlay part that holds prints `reviewed X, now Y`. Drafts under `.agents/playbooks/drafts/` are not read.

### Inventory coverage

`inventory` scans tracked Markdown, TOML and text files at HEAD. It recognizes Markdown inline links and images, reference definitions (in blockquotes too), bare `#fragment` targets, and `path` or `path#anchor` strings in TOML and text (`references`); link text, a destination or a definition's target may sit on the next line of the paragraph, and a target's backslash escapes are removed before it resolves. It lists broken references already present and what it cannot see (globs, generated paths, external links, frontmatter, and tracked files of other types). The `candidate` flag (three or more top-level sections, over 2000 tokens, references from two or more files) is advice.

## Known limits

Current behavior, not promises.

- A `kitchen.toml` `source` that names a migrated file with no anchor passes `check` though the file is now an index.
- A reference in a source's own staying text to a section it moved is listed as `index-local:` and does not fail `preview` or `apply`: the tool cannot rewrite a move source, and the index still resolves it.
- `check` with any planned file still at its base state stops before line accounting and exits 1.
