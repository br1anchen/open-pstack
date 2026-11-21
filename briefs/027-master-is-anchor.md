# Brief 027: master-is-anchor

playbook: feature
timebox: 120
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/027-master-is-anchor.md

## Goal

Under `PSTACK_MUX=amp`, the current master is the run's anchor: a top-level thread that holds the run's anchor title, with every role thread of the run (sidekick, consultant, verifier) nested under it. On a master rotation the anchor moves to the new master. The new master ends up top-level, takes the anchor title, and new role threads nest under it. The previous master and the role threads the run no longer uses are archived. Today the anchor is fixed at the first master, and a rotated master nests under the thread that started it. When that thread is archived, the live master disappears from the threads panel (Brian, ChenChess #1257; this run's g2 master had the same problem and was fixed by hand, see standing order 27).

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py
- skills/pstack-pair/scripts/pstack_core/commands/init.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- skills/pstack-pair/scripts/pstack_core/cli.py (only the `nest` usage line)
- tests/test_pstack_kitchen.py
- tests/test_pstack_pair.py (only the help golden for `nest`)
- tests/pstack-pair/bin/amp
- tests/pstack-pair/golden/**
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/send.py
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- any other skill

## Steps

1. **The anchor carries a title, and a top-level row.** In `nest.py`, `pair.json` `.anchor` becomes `{thread, title}`. `init` records the master's own thread and that thread's title (from `amp threads export <T>` `.title`, via a helper in `mux/amp.py`; empty when export fails). `anchor()` stays the reader, and a store without `.title` reads its title lazily the same way. A nest-pending row whose anchor cell is `-` means "make this thread top-level". For such a row, `nest` prints `nest-pending: <T> (master) top-level` and the instruction line names `update_thread(thread=<T>, parent_thread=null)`. Rows with an anchor keep their current output exactly. Update the module docstring to the new rule.
2. **A master thread is never nested under a master.** In `mux/amp.py` `record_started` (it calls `nest.record_pending`), a thread whose role is `master` is not recorded under the anchor. Today `rotate-master` (`hand_to_next_thread` in `kitchen_core/master.py`) starts the next master with `amp -ox` from the old master's thread, so it is recorded nest-pending under the old anchor.
3. **take-master moves the anchor.** In `take_master`, under a backend with `master_threads`, after the old master is retired and before `archive_retired` runs, these happen:
   - `.anchor.thread` becomes the caller's thread, the title is kept, and the old anchor is appended to `.anchor.previous`.
   - The new master is renamed to the anchor title with `amp threads rename <T> <title>`, through a helper in `mux/amp.py`. A failed rename prints one `warn:` line and does not fail take-master.
   - A `-` (top-level) nest-pending row is recorded for the new master.
   - Each role thread the run still uses (the current sidekick, consultant and any open verifier, from the thread registry, not archived and not retired) gets a fresh pending row under the new anchor, so it does not vanish with the old master. To allow that, `record_pending`'s "already has a row" check must compare against pending rows for the same anchor, not every row.
   - take-master's output gains one line: `anchor: <T> is the run's top-level anchor "<title>"; run kitchen.sh nest <store>`.
   - `archive_retired` then archives the old master and the replaced role threads exactly as today. Do not change its safety rule.
4. **Docs.** Rewrite the "A run reads as one tree" bullet in `skills/pstack-kitchen/SKILL.md` and its `.anchor.thread` store-table row: the anchor is the current master, top-level, holding the anchor title; rotation moves it; roles nest under it. In `references/master.md` step 5 (the nest paragraph) and in "On `pstack-kitchen MASTER <store>`", say that after `take-master` the new master runs `kitchen.sh nest`, makes the calls it lists (including `parent_thread: null` for itself), then `nest --done`. Regenerate `tests/SPEC.md`.

## Context

- Before you start, base on origin/main, not the run's stack: `git fetch origin main && git checkout -B master-is-anchor origin/main` (7218f31). This unit ships as its own PR, apart from the kitchen-followups stack.
- Existing tests to extend, in `AmpKitchenTests` (tests/test_pstack_kitchen.py): `test_init_records_the_masters_own_thread_as_the_runs_anchor` (~14419), `test_every_thread_the_kitchen_starts_is_nest_pending_under_the_one_anchor` (~14472), the nest output tests just after it, `test_the_old_master_thread_is_archived_by_take_master_and_not_before` (~15897), and `test_rotate_master_starts_the_next_master_as_a_thread_and_a_stale_reply_is_forwarded` (~17089). Add new cases there. Don't open a new class.
- The fake `amp` (tests/pstack-pair/bin/amp) needs `threads rename` (record the call under $FAKE so a test can read it) and a `.title` in `threads export`. Copy how it already records other calls.
- How the run fixed itself by hand: decisions.tsv phase `anchor`, standing order 27.

## Acceptance

- After `init` under Amp threads, `pair.json` `.anchor` is `{thread: <master>, title: <its title>}`.
- After `rotate-master` then `take-master` in the new thread:
  - `.anchor.thread` is the new master and `.anchor.previous` holds the old one.
  - The fake recorded a rename of the new master to the anchor title.
  - `kitchen.sh nest` lists the new master as `top-level` with a `parent_thread=null` instruction, and the live sidekick under the new master.
  - No row nests the new master under the old master.
  - The old master is archived, as today.
- A test fails if take-master leaves `.anchor.thread` on the old master, and a test fails if the new master gets a row under the old anchor. Show each by a scratch revert of the line that makes the test pass.
- A take-master whose rename fails prints `warn:` and exits 0.
- Every existing nest and take-master test passes unchanged, apart from the anchor-shape assertion in the init test.

## Verify

```bash
python3 tests/run.py -k nest
python3 tests/run.py -k anchor
python3 tests/run.py -k take_master
python3 tests/run.py -k rotate_master
python3 tests/run.py
python3 tests/spec_gen.py --check
python3 scripts/check-pstack.py
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- The kitchen never calls update_thread itself (the amp CLI cannot set a parent). It records rows, and the master makes the calls.
- Herdr, Rex and subagents backends record nothing, as today.
- No push and no PR. The master opens the PR after verify and review.

## Playbook: feature + pstack-code (your parts)

You own the working tree.

Your parts:
- testing: ## Testing Guidelines
- style: ## Style
- feature.intro-1: You own the implementation inside the brief's Scope; the master owns the design and the review. You 
- feature.step-1: Read the Scope's files and the code they call before you edit. The design is the brief's: a design q
- feature.step-4: Implement with a specific scope: file paths, the named data shape and its organizing structure per *
- feature.step-5: 5. Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.
- feature.step-6: Commit small, ordered units as new commits, each recorded with kitchen.sh step and green before the 
- feature.reply: In the report, under Ran and the sections the template names: what you built, what you chose and why

### testing

#### Testing Guidelines

- `python3 scripts/check-pstack.py`, `python3 tests/run.py`, `uv lock --check`, `uv run --frozen ruff format --check`, `uv run --frozen ruff check` and `uv check --frozen` must pass before a commit; the suite runs once, on the host's `python3`. `tests/run.py` runs one process per test in parallel; `-k <text>` selects tests by id; `PSTACK_PAIR_UPDATE=1` regenerates the pair goldens, and a golden changes only when the transcript is meant to change.
- Run `git config blame.ignoreRevsFile .git-blame-ignore-revs` once per clone so `git blame` skips the format commit.
- Commit a source change in the same commit as the test that pins it; the kitchen's `test-touch` policy refuses a step under `skills/pstack-pair` that changes source with no file under `tests/`.
- `python3 tests/spec_gen.py --check` builds tests/SPEC.md without writing it and exits 1 when it is stale or a line has no test; a brief's Verify uses it in place of a regenerate.
- Tests are deterministic: no sleeps and no elapsed-time asserts. Trigger a state change at a fixed point with the fake herdr hooks (`$FAKE/once/<subcommand>`, `$FAKE/report-after`) and assert on output.
- Tests are hermetic: they scrub inherited `PSTACK_KITCHEN_*`, `KITCHEN_*`, `PAIR_*`, `HERDR_*`, `REX_*`, `PSTACK_MUX`, `AMP_THREAD_ID` and `AMP_CURRENT_THREAD_ID` variables, so a run inside a live kitchen gives the same result as one outside it.
- Drive pair.sh and kitchen.sh by hand only against the fake herdr: put `tests/pstack-pair/bin` first on PATH and point XDG_STATE_HOME at a temp directory, as `tests/pstack-pair/scenario.sh` does (it unsets `PSTACK_KITCHEN_ROLE` and `PSTACK_KITCHEN_RUN`, which a verifier pane carries). `init`, `spawn`, `stop` and `close` against the real herdr register, rename or close the calling pane, so an agent that runs them there loses its own pane.

### style

#### Style

- The entry scripts stay one exec line and `bash -n` clean; Python is stdlib-only, Python 3.11 or later.
- Python is formatted and linted with the pinned ruff (`uv run --frozen ruff format`, `uv run --frozen ruff check`) and type-checked with `uv check --frozen`, which needs uv 0.12.3 or later. The tools are dev-only, in `pyproject.toml`'s dev group and `uv.lock`; the shipped scripts stay stdlib-only and import nothing from them. The 3.11 floor is held by ruff's `target-version` and ty's `python-version`.
- Skill text: say what holds, in plain sentences; link with relative paths that `check-pstack.py` can resolve.
- Name commits with Conventional Commits and a scope naming the skill or area (`fix(skills/pstack-kitchen): ...`, `test(tests): ...`). Keep commits atomic.

### feature.intro-1

You own the implementation inside the brief's Scope; the master owns the design and the review. You may delegate to a subagent on your roster model, within your role's limits.

### feature.step-1

Read the Scope's files and the code they call before you edit. The design is the brief's: a design question goes in the report under Questions, not into a design exploration of your own.

### feature.step-4

Implement with a specific scope: file paths, the named data shape and its organizing structure per **principle-model-the-domain** (a state machine over scattered booleans, a table or registry over branching, a typed model over repeated shape assumptions), chosen before you write logic, and the brief's success criteria. You may delegate to a subagent on your roster model, within your role's limits, for disjoint scoped edits that you integrate serially. Comments per **Comments**. Surgical edits; re-ground against the source for upstream-derived files. Port shared-primitive improvements to all consumers and verify each. Commit liberally.

### feature.step-5

5. Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.

### feature.step-6

Commit small, ordered units as new commits, each recorded with kitchen.sh step and green before the next. Never rebase or amend commits already recorded as steps unless the brief says so. Use the **sequence-verifiable-units** principle skill, building, verifying, and committing each small unit before the next.

### feature.reply

In the report, under Ran and the sections the template names: what you built, what you chose and why, and open decisions. Tables for design alternatives. Every claim carries its command output or a file pointer.

## Report

Work the Steps in order. End each in a commit and record it with
`kitchen.sh step /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <sha> "<what it does>"`: it runs the touched
profiles' fast gates and the repo's policy check, and records the step only
when they pass. When it exits 4 with `gates: still running`, the step is not
recorded: run the same command again at once, in the foreground, and keep
going until it records; never end your turn on it. On a failure, fix it in a
new commit and run `kitchen.sh step` again with that commit; it tells you when
two failures in a row make this a blocked report. At each step boundary run `kitchen.sh notes /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups` and fix
any open blocking note first, as a fixup commit recorded with `--resolves`.
Append one progress line per change of approach with
`kitchen.sh progress /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups "<line>"`. When any `kitchen.sh` command prints
`PAUSE:`, stop at that safe point: commit what is verified and report
`partial` with the next step. Before reporting done, run the review skills the
kitchen's `review.self` names on your diff. Write the report file named above
using the pstack-pair report template, then run
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh finish /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <report path>`
and do what it prints. For a done report, finish first reruns the Verify
commands above at the report's head:, with the checkout at that commit and
nothing uncommitted, and sends the report only when every one exits 0; exit 4
means they are still running, so run the same finish again at once.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
