> Historical session artifact, imported 2026-10-25. Read [the handoff](../../HANDOFF.md) first for current status and remote execution instructions. Host-local paths and workflow commands below describe the original run.

# Report 020: context-render

status: done
brief: /home/br1anchen/.local/state/pstack/kitchen/runs/feedback-1114/briefs/020-context-render.md
playbook: feature
branch: main
head: 8fb34a51b91732332e08613ee3cd521c1ff93d66
tree: clean

## Changed

```
git diff --stat origin/main..HEAD
 skills/pstack-kitchen/scripts/context.py (new)
 skills/pstack-kitchen/scripts/kitchen.py             |  68 ++
 tests/SPEC.md                                        |   6 +-
 tests/fixtures/context/expected/{STANDARDS.md, docs/standards/*.md} (new, 4 files)
 tests/fixtures/context/repo/{STANDARDS.md, .agents/kitchen.toml} (new)
 tests/fixtures/context/sample.toml (new)
 tests/run.py                                         |   1 +
 tests/test_pstack_context.py (new)
```

Commits, all recorded with `kitchen.sh step`: a63fd41 (step 1), eaad1eb and dd3fd15 (step 2; dd3fd15 fixes note n1, recorded with `--resolves n1`), 0a4a515 and 94e5aaa (step 3; 94e5aaa fixes note n2, `--resolves n2`), 8fb34a5 (step 4).

## Ran

```
$ python3 tests/run.py -k context      -> 79 tests, 0 failed
$ python3 tests/run.py -k bounded      -> 11 tests, 0 failed
$ python3 tests/spec_gen.py --check    -> ok: tests/SPEC.md is current, 0 missing
$ git diff --quiet origin/main HEAD -- skills/pstack-kitchen/scripts/playbooks.py   -> exit 0
```

Also: `uv run --frozen ruff format --check`, `ruff check` and `uv check --frozen` pass; every step's gates passed (pstack, pstack-docs).
Preview on the fixture repo is driven by PreviewTests in a temp git repo: a diff for every path in `writes`, JSON hashes, `git status --porcelain` empty afterwards, and an edit to the working tree after the base commit leaves the output unchanged.
Mutation check (reverted after): removing the `\r` guard, the unclosed-fence guard, the frontmatter skip, the heading shift, the duplicate-suffix loop, the moved-twice check and the sha256 check each made tests fail; removing the cleaned git environment failed `test_an_inherited_git_environment_does_not_redirect_the_reads`.

## Self-check

figures: every number above is from Ran or git output
acceptance: refusals of steps 1-3 and exact rendered bytes of the fixture are in LoaderTests, BaseTests, ReaderTests, CutTests, SlugTests, RenderTests; preview's diff and no-write check in PreviewTests; playbooks.py unchanged (Ran, last line)
callers: new module and a new `context` subcommand only; no shared symbol changed (kitchen.py gained `context_module`, `context_command`, `context_preview_text`; grep shows no other user)
fresh: the suite runs on fresh temp repos; no cache involved
gate: ruff format, ruff check, uv check pass at head; step gates passed for every commit

## Deviations

- `Read first:` goes under the destination's title line, as plan 019's Design says ("one line under the title"), not before it as brief step 3 reads. A line above the `#` title would make the destination's first line not its heading.
- Sections are matched on the heading text as written (`raw`), with `plain` (markup rendered away) kept beside it for slugs. The brief says "rendered text"; the raw form can be copied from the file.
- `render` takes a third argument, `present` (destinations and `requires` paths that exist at the base), and a new boundary function `base_files_at` supplies it; `cuts` takes the same set. This keeps both pure.
- `plan()`/`unified_diff()`/`Write` live in context.py so kitchen.py only renders. A rewrite target has no change in this unit (rewrites are unit 021), so preview lists it as `unchanged:` with an empty diff.
- The rewrite-target `[[file]]` check is in the loader only (note n1); the non-nesting overlap branch was dropped as unreachable for heading sections (note n2 follow-up); the same-section-twice check remains.
- Guards beyond the brief, from review notes: an indented (1-3 spaces) ATX or setext heading is refused; `[[file]]` blobs are read with `git cat-file blob`; git calls drop `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and related variables.
- Local `--repo` and `--json` may follow `preview`, as well as the global ones before the subcommand.
- Index tail: when a moved slice ends the file, the last pointer's trailing blank line is dropped; the index ends with the pointer line and a newline.
- Local THEMATIC and SETEXT patterns remain, as the setext guard's own; headings, fences and frontmatter come from playbooks.py.
- Notes n1 and n2: all blocking items fixed with a test each; n1's follow-ups on the path-rule assertions and docstring done; n2's follow-ups (1-based line, one rewrite-target check, dead branch) done.

## Questions

none

## Follow-ups

- Destination files end with the blank line(s) the section owns (C5), so each ends in one blank line.
- `context.py` write helpers (apply) and `check` come in unit 022; link rebasing and `unresolved:` in unit 021.
- review.self is not set in this repo's kitchen.toml, so no review skill was named to run; a self-read of the diff found no dead code beyond what notes n1 and n2 covered.
