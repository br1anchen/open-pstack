# kitchen.toml

A repo's kitchen rules, committed at `.agents/kitchen.toml`, or kept on the
host when the repo's team has not agreed to commit them (see Where it lives). They say which of
the repo's own commands prove which part of it, what makes a change risky, and
which lines must never land. `scripts/kitchen.py` reads them as data; no agent
invents a verification recipe or judges risk from prose. A unit is checked
against the kitchen at its own commit (`kitchen.py --at <sha>`), so changing
the rules never changes a verdict already given.

A worked example: [kitchen-example.toml](kitchen-example.toml).

## Where it lives

`kitchen.py` takes the first kitchen it finds:

1. `--config <path>`: relative to the repo, or absolute.
2. The repo's `.agents/kitchen.toml`, at `--at <rev>` when given, else the
   working copy.
3. A host kitchen, for a repo whose team has not agreed to commit one: the
   `file` of the first `[[kitchen]]` entry in the host roster whose `match`
   glob covers the checkout or its main checkout, else
   `~/.config/pstack/kitchens/<host>__<owner>__<repo>.toml`, named from the
   `origin` remote. Without an origin, the name is the main checkout's
   basename and a hash of its path.

```toml
# ~/.config/pstack/kitchen.toml
[[kitchen]]
match = "~/src/<repo>/**"      # the checkout, or a bare repo with its worktrees
file = "<repo>.toml"          # relative paths resolve against ~/.config/pstack/kitchens/
```

Every clone, worktree, and verifier scratch of a repo resolves to the same
host file. `kitchen.py where` prints the kitchen in use and the host path;
`kitchen.py where --host` prints only the host path, whether it exists or not.
`validate` and `doctor` name the source. A committed kitchen shadows the host
one, and an uncommitted `.agents/kitchen.toml` fails `doctor`: this checkout
reads it, but verify and the verifier's scratch read commits and never see it.

A host kitchen has no history, so the rule that a unit is checked against
the kitchen at its own commit holds through a snapshot instead: when
`kitchen.sh` classifies a brief, it copies the host file into the run store
(`kitchens/<hash>.toml`, `kitchen.py snapshot <dir>`) and writes `kitchen:
<copy>` into the brief's header. That brief's steps, its verifier's gates,
and its verdict all read the copy, so editing the host file changes the
next brief, not one already dispatched. A landing verify takes a fresh copy.

## Keys

Unknown keys are errors, so a typo fails `kitchen.py validate` instead of
silently doing nothing.

| Key | Default | Meaning |
| --- | --- | --- |
| `version` | required | `1` |
| `[profile.<name>]` | at least one | an area of the repo and its proof; name is `[a-z][a-z0-9-]*` |
| `.paths` | required | globs the profile covers; a file may sit in several profiles |
| `.fast` | `[]` | commands `kitchen.py gate <p> fast` runs at every step: format, lint, typecheck, targeted tests; minutes, not tens of minutes |
| `.behavioral` | `[]` | commands the verifier runs at the unit's commit: e2e, journeys, the app driven for real |
| `.landing` | `[]` | the full battery, run once, in the landing unit: `verify --landing` puts `gate <profile> landing` in the verifier's packet for each touched profile that declares it |
| `.features` | `[]` | feature-map files of the repo's verification skill that the verifier reads for this profile |
| `.tests` | `[]` | globs of this profile's test files |
| `.require_tests` | `false` | a change to the profile's files needs a change to a `tests` file (`kitchen.py policy` reports `test-touch`) |
| `.verify` | `"batch"` | routine verification: `gates` (deterministic gates only, no verifier), `batch` (one verifier per landing stack), `unit` (one per unit) |
| `.sample` | `0.1` | share of clean routine units the master audits in full |
| `.heavy` | `false` | `true`, or a list of stages from `fast`, `behavioral`, `landing`: those stages' gates take one of `resources.max_parallel_heavy` machine-wide slots, so a verifier's build never races the sidekick's. `true` means all three. A 30-second fast gate should not queue behind a verifier's behavioral build, so a repo that builds only there opts in with `heavy = ["behavioral", "landing"]`. A heavy gate takes its heavy slot before its machine gate slot |
| `.wrap` | `[run].wrap` | this profile's wrap; `""` runs it unwrapped |
| `[playbooks].target_tokens` | `1500` | the token target (characters divided by four) for the `## Playbook:` section dispatch writes into a routine or escalated brief: the sidekick's parts of the unit's stamped composition. It is a target, not a cap. The role's authority and the inventory of every part always go in; the text of each part follows in composition order while the estimate stays within the target, and every part past it is listed under `Pending reads` with the `kitchen.sh playbook-part <store> <NNN> <role> <part-id>` call that prints it from the stored composition. `validate` rejects a value that is not an integer of 1 or more |
| `[escalate]` | | what turns a routine change into an escalated one: per-unit verification by the master's kind, full sampling, the escalated review settings, and the consultant |
| `.paths` | `[]` | globs whose change is architectural: contracts, schemas, migrations, auth |
| `.manifests` | `[]` | dependency manifests; changing one adds or moves a dependency |
| `.max_profiles` | none | escalate when a change touches more profiles than this (a cross-boundary change) |
| `.max_diff_lines` | `600` | escalate a larger diff |
| `.unmapped` | `true` | escalate a change to files no profile or `coverage.ignore` covers, since no gate proves them |
| `[[policy.forbid]]` | | a rule for added lines |
| `.id` | required | unique; a line opts out with `kitchen-allow: <id>`; several ids are a comma list (`kitchen-allow: a, b`), and the rest of the line is the reason |
| `.pattern` | required | Python regular expression, searched in each added line |
| `.message` | required | what to do instead; the sidekick reads only this |
| `.paths` | `["**"]` | globs the rule applies to |
| `.source` | `""` | the rule's origin, such as `AGENTS.md#rust` |
| `[[generated]]` | | files generated from sources; `classify` warns when a Scope covers a source but none of its outputs |
| `.sources` | required | non-empty list of globs for the files the output is generated from |
| `.outputs` | required | non-empty list of globs for the generated files |
| `.command` | `""` | how to regenerate them; shown in the warning |
| `[review]` | | |
| `.self` | `[]` | skills the sidekick runs on its diff before reporting done |
| `.standards` | `[]` | doc sections (`path#anchor`) the review holds the diff to; `context check` fails a `.standards` or policy `.source` reference whose anchor a [context migration](context-migration.md) moves, unless a `[[rewrite]]` entry rewrites it; a migration rewrites only the references its `[[rewrite]]` entries list, an anchorless `.source` (a whole file) is not caught ([known limits](context-migration.md#known-limits)), and only the repo's own `.agents/kitchen.toml` is read, never a host kitchen |
| `.max_batch_diff` | `800` | a batch over this many diff lines verifies per unit instead |
| `.engine` | unset | a review tool the owner requires on every unit, such as `"joo"`. The kitchen does not run it; the master does, and land-check refuses an accepted unit whose review note has no `engine: <name> <run id or artifact path>` line. `doctor` checks that `<name>-dev` or `<name>` is on PATH |
| `.routine`, `.escalated`, `.landing` | unset | tuned a reviewer the kitchen no longer runs; still parsed, ignored, and `validate` and `doctor` warn while the file sets them |
| `[verify]` | | |
| `.routine_kind`, `.escalated_kind` | unset | moved to the host roster's `[verifier]` and `[verifier.escalated]`; still parsed, ignored, and `validate` and `doctor` warn on stderr while the file sets them |
| `[landing].mode` | `"commit"` | how far the kitchen goes: `commit`, `branch` (push), `stack` (a linear stack of PRs), `pr`. It never merges, and a landing brief that runs `shipping` stops before that playbook's merge step (merge bottom-up, then freeze writes) whatever the playbook says. A stack landed by a push that rewrites its commits (a squash; `jj checked-push` of an spr stack, whose PRs sit on synthetic merge heads) leaves its PRs open: the landing brief closes each with a pointer to its landed commit |
| `[coverage].ignore` | `[]` | globs `doctor` and `classify` treat as covered without a gate |
| `[resources].max_parallel_heavy` | `1` | heavy gates running at once on this machine |
| `[scratch].setup` | `[]` | commands that prepare a bare worktree, run in order by `kitchen.py setup` (first in every verifier packet when set, and once by `kitchen.sh init` as the sidekick when the run's checkout is a linked worktree), inside `[run].wrap`, from the repo root, as the verifier. They run inside the wrap, so do not repeat it: with `wrap = "nix develop --impure --command"` the line is `bun install --frozen-lockfile`. `doctor` warns when a root lockfile suggests the repo needs one |
| `[run].wrap` | `""` | a prefix the gates run inside, such as `nix develop --impure --command`, entered once per gate |

## Playbook overlays

An overlay adds a repo's own standards to the playbook a brief names. It is a
`<id>.toml` file in `.agents/playbooks/` for a repo kitchen, or in
`<kitchen-file-stem>.playbooks/` beside the host kitchen file for a host
kitchen, so each host kitchen has overlays of its own. A `<id>.md` beside it is
narrative and is never read. A [context migration](context-migration.md) checks each part whose source it moves. The example is `rust-engine` from
`tests/fixtures/playbooks/repo/.agents/playbooks/`, cut to its first part and
with a `kitchen` text that stands on its own:

```toml
schema = 1
id = "rust-engine"
title = "Rust engine"
kind = "language"
applies = ["engine/**"]
tags = ["rust"]
combines = ["bug-fix", "feature", "refactoring"]

[[part]]
id = "error-model"
source = { root = "repo", path = "CODING_STANDARDS.md" }
select = "heading:Rust (Engine)/Errors"
owners = ["sidekick", "verifier"]
reviewed = "6b7c36b4f3ca"
kitchen = { verifier = "Check that every error path in the diff follows the repo's Rust error model. This is the evidence standard for the verdict, not new Acceptance." }
```

Here the sidekick receives the selected section of `CODING_STANDARDS.md`
as written, and the verifier receives the `kitchen` text in its place.

| Key | Meaning |
| --- | --- |
| `schema` | required, `1` |
| `id` | required, equal to the file name without `.toml` |
| `title` | required, not empty |
| `kind` | required: `domain`, `language`, `area` or `workflow` |
| `applies` | list of globs; the overlay is selected when one matches a file in the brief's Scope. Default `[]` (selected only by `with:`) |
| `tags` | strings, for the index. Default `[]` |
| `combines` | workflow ids the overlay may combine with; each must be a workflow the map defines. Default `[]`, which means every workflow |
| `[[part]]` | the texts it contributes, in order |
| `.id` | required, not empty, unique in the overlay |
| `.source` | required: `{ root, path }`. `root` is `repo` (the repo root), `skills` (the skills directory) or `host` (the overlay directory of a host kitchen; a repo kitchen has no `host` root and reports `unknown source root`). `path` is relative, with no `..` and no backslash, and a symlink must resolve inside its root |
| `.select` | required: which section of the source, by exactly one match. `heading:A/B` matches the heading whose path of headings ends in `A/B` (`heading:Errors` alone matches any heading with that title). The other kinds are `preamble:N`, `intro:N`, `lead:<Word>`, `step:N` or `step:<Section>/N`, `section:<Heading>`, `tail:N`, `after:<Lead>/N` and `reply` (no argument; every other kind needs one). A selector that matches nothing or several is an error |
| `.owners` | required, non-empty, from `master`, `sidekick`, `consultant`, `verifier` and `kitchen`. The roles that receive the part; `compose` delivers to the four agent roles only |
| `.reviewed` | required in practice: the first 12 hex characters of the sha256 of the selected text, normalised (trailing spaces cut, blank runs folded to one, ends trimmed). A missing or stale value is a digest mismatch, and the message prints the current value to copy; `kitchen.py playbooks index` lists the recorded values |
| `.kitchen` | optional table keyed by agent role (`master`, `sidekick`, `consultant` or `verifier`), each key also an owner and each text not empty. For a role named there, the text replaces the selected source text in what that role receives; every other agent-role owner receives the source text (an owner `kitchen` receives nothing) |

Any other key in an overlay or a part is an error.

A brief gets the overlays whose `applies` matches its Scope, plus those it
names. Two optional header lines follow `playbook:` in the brief:

- `with: <id>, <id>` adds the named overlays whatever their `applies`.
- `without: <id> — <reason>` removes that overlay alone; `--` also separates the
  reason, and a missing reason is an error.

Naming an unknown id, naming the same id in `with:` and `without:`, or naming
with `with:` an overlay whose TOML cannot be read, whose `applies` is not a
list of strings, or whose `combines` leaves out the brief's workflow is an
error, and `classify` refuses the brief. Without `with:`, such an overlay is
left out and reported `unusable` on every brief, matched by path or not. An
overlay selected by path whose `combines` leaves out the brief's workflow is
left out and reported `inapplicable`, not an error. Any other problem in
a selected overlay (a stale `reviewed`, a bad selector, kind or owner, an
unknown key, an unreadable source) makes `classify` refuse the brief, however
the overlay was selected. Part ids share one namespace with the workflow's own
parts and the other selected overlays: the same id with the same definition
merges into one part that keeps every origin, and the same id with a different
definition is an error. `classify` resolves the selection once and stamps it in
`<store>/compositions/<NNN>.json`.

`kitchen.py validate`, `kitchen.py playbooks validate` and `doctor` report every
structural problem of every overlay (schema, id, kind, lists, unknown keys,
owners, combines, sources, selectors and digests); `doctor` also warns when an
`applies` glob matches no tracked file. `kitchen.py playbooks index` lists the
overlays with their parts, owners, sources and digests.

## Lanes

Every command the kitchen runs for a role sees two variables: `PSTACK_KITCHEN_ROLE`
(`sidekick` or `verifier`) and `PSTACK_KITCHEN_RUN` (the run's slug). The role
keeps the sidekick and its verifier apart; the run keeps two kitchens on one
machine apart. A repo's scripts should derive ports, data directories and
emulator names from the (run, role) pair, since either one alone can collide
(two runs share the role `verifier`). `kitchen.py` passes both through to the
wrap, `setup` and gate commands without inferring or clearing them; the kitchen
allocates no ports. The verifier's pane itself starts with both variables set
(`--env` on its `herdr pane split` or `tab create`), so a harness that starts a
fresh shell for each command still has them; `init` refuses under
`PSTACK_KITCHEN_ROLE=verifier`, because it would rename the verifier's pane.

## Globs

Paths are repo-relative. `**` crosses directories; `*` and `?` stay inside
one. A pattern never matches a basename alone: `*.md` is top-level only,
`**/*.md` is everywhere.

## Generated files

A change to a source usually regenerates files elsewhere in the repo, and a
brief whose Scope forbids those files makes the sidekick block on them
mid-unit. Declare the pairs:

```toml
[[generated]]
sources = ["services/api/contract/**"]
outputs = ["packages/sdk/src/generated/**"]
command = "bun run generate:sdk"
```

When a brief's Scope (`classify --paths`, so `kitchen.sh classify` and
`dispatch`) matches a source file, and no Scope glob matches any file that
matches the entry's `outputs`, `classify` prints `warn: Scope covers <source>
but none of its generated outputs (...); add them to may write, or say why
not` and lists it under `warnings` in `--json`. An entry whose outputs match
no tracked file yet is skipped. A warning never changes the risk class or the
exit code, and a kitchen with no `[[generated]]` entry classifies as before,
without a `warnings` key.

## Commands

Each command runs with `bash -c` from the repo root; its output goes to a log
under `${XDG_STATE_HOME:-~/.local/state}/pstack/kitchen/repos/<repo>/logs/`,
and a failure prints the log's last 20 lines. A gate stops at its first
failure. A command that fails while the one-minute load is over one per core
runs once more: a pass then counts, marked `flaky under load` in the step's
output and counted by `retro` as `gate-flaky`, so a suite that only fails on
a loaded machine shows up as that rather than as broken changes.
`PSTACK_KITCHEN_RETRY_LOAD` sets the load per core (default `1.0`; `off` never
reruns). Each gate also runs with its own short `TMPDIR` under `/tmp`, removed
when it ends (`PSTACK_KITCHEN_GATE_TMP` moves it). Every command sees `PSTACK_KITCHEN_STEP_BASE`, the commit the step or unit
is checked against, so an affected-only command covers every commit of a
multi-commit step (`turbo run test --filter=...[$PSTACK_KITCHEN_STEP_BASE]`;
give a fallback such as `${PSTACK_KITCHEN_STEP_BASE:-HEAD^1}` for runs
outside a step). Ranges are measured from the merge-base, so a squash or a
rebased step still covers everything it carries. Every command also sees
`PSTACK_KITCHEN_ROLE` (`sidekick` or `verifier`):
the verifier runs at the same time as the sidekick, so a repo's scripts that
start servers, emulators, or databases should pick their ports and data from
it. State (logs, the baseline, the ledger) is kept per repository, shared by
its worktrees. With a wrap, `kitchen.py` re-runs itself inside it once per gate,
so a shell that takes seconds to enter costs that once, not per command, and
`doctor` checks each program inside the wrap it runs in. Use the repo's own entry points
(package scripts, `just`, `make`, wrappers the AGENTS file requires) so the
kitchen never drifts from how humans run the same checks.

## kitchen.py

```bash
kitchen.py validate                                   # parse and check; names the source
kitchen.py where [--host]                             # the kitchen in use, and the host path
kitchen.py snapshot <dir>                             # copy a host kitchen into dir by content hash; prints it
kitchen.py classify --base <sha> [--head <sha>]       # a unit's commits
kitchen.py classify --working-tree                    # uncommitted work
kitchen.py classify --paths 'apps/web/**' src/x.ts    # a brief's Scope, before work starts
kitchen.py gate <profile> fast|behavioral|landing     # exit 2 on failure; --keep-going, --only N
kitchen.py policy --base <sha> [--head <sha>]         # forbid rules and test-touch; exit 2 on findings
kitchen.py history [--last 20]                        # how each recent commit classifies, and its policy findings
kitchen.py doctor [--run]                             # coverage, commands, features; --run records a baseline
kitchen.py lessons [--all]                            # staged lessons with held/hit counts, then the top candidates
kitchen.py lessons add <kind> <class> <area> [--match M] "<rule>" # stage an evidence-backed lesson (proposed)
kitchen.py lessons dismiss <kind> <class> <area> [--match M] --note "<why>" # hide a candidate with no one cause
kitchen.py lessons set <id> <status> [--rule] [--note] # proposed | active | promoted | retired
kitchen.py lessons promote <id> [--out <file>]        # the promotion proposal for the human to decide
kitchen.py ledger-backfill <ledger> [--runs <dir>]    # give rows from before area and match both, from the run stores
```

Lessons live in `.agents/LESSONS.md` beside a repo kitchen, or beside a host
kitchen as `<key>.lessons.md`; they are not a kitchen.toml key. The file holds
each rule, its status and evidence, and any notes a human adds (a rewrite
keeps them); held and hit counts stay in the host ledger, which also hands out
lesson ids, so runs in parallel worktrees never stage the same one. A
lesson's match is the trouble within its class: the stage and check a gate
failed (`fast lint`), the policy rule (`test-touch`, class `policy`), or the
steer's kind.

`--json` gives each result as data, `--at <rev>` reads the kitchen (and, for
`--paths`, the file list) at a revision, and `--repo` points at another
checkout. `classify` prints the risk class with every escalation reason, the
touched profiles, files no profile covers, the verification mode and kind, the
sample rate, the review settings, and the feature files the verifier needs.
