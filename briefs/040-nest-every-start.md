# Brief 040: nest-every-start

playbook: bug-fix
timebox: 90
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/040-nest-every-start.md

## Goal

Under Amp threads every role thread a run starts ends up nested under the current anchor without the master having to run `status`: each command that starts a thread prints the `nest:` instructions, a thread with a pending nest row is not archived, take-master re-parents every thread of the run (previous masters included) to the new anchor, and `nest` tells the master how to nest a thread that is already archived.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- tests/test_pstack_kitchen.py
- tests/SPEC.md
- tests/pstack-pair/bin/amp

must not write:
- anything else

## Steps

Start from main eb8228f9 (`git fetch origin main && git checkout -B kitchen-followups-040 origin/main`, which is at or after eb8228f9). Each step first adds the failing test that reproduces the report, then the fix.

1. Starts print the nest lines. Every thread the kitchen starts is recorded in one place, `amp.record_started` (mux/amp.py, it calls `nest.record_pending`). Split `nest()`'s listing (the `nest-pending:` lines and the `nest: call update_thread(...)` instruction) into one function `nest.instructions(store, prog)` returning the text, used by `nest()` unchanged, and print it on stdout right after a start records a new pending row. Test: one table over spawn, a rotation inside dispatch, consultant and verify under the fake amp: each command's output ends with the `nest-pending: <T>` line and the `update_thread` instruction for the thread it started. Targeted check: `python3 tests/run.py -k nest`.
2. A pending nest row keeps the thread. In `amp.assess`, a thread with a pending row in `nest.pending(store)` returns `kept: nest pending (nest it first; update_thread cannot set a parent on an archived thread)` before the archive decisions, so the sweep (`threads --archive`, take-master, dispatch, verify --wait, close) never archives it while pending; once `nest --done` closes the row the sweep archives it as today. In `nest()`, a pending thread the thread registry already marks archived (`threads.json` `archived`, as `run_threads` reads it) is listed with `, archived` and the instruction `update_thread(thread=<T>, archived=false)`, then the parent call, then `update_thread(thread=<T>, archived=true)`. Tests: the sweep keeps a verifier with a pending row and archives it after `--done`; an archived pending thread gets the three-call instruction. Targeted check: `python3 tests/run.py -k nest` and `python3 tests/run.py -k archive`.
3. take-master re-parents the whole run. `nest.take_anchor` adds a pending row under the new anchor for every thread of the run, not only live roles: `run_threads(store)` without dropping archived ones, plus every previous master (`.anchor.previous` and `.master.sessions` threads), each with its role. Rows under the old anchor are superseded by the newer row (`pending()` already keeps the newest row per thread; add a test that proves it). Test: a store with two earlier generations (masters, verifiers, an archived sidekick) after take-master lists every one of them under the new master, and none under an old master. Targeted check: `python3 tests/run.py -k take_master` and `python3 tests/run.py -k anchor`.
4. `nest --done` and what it can check: read the fake amp and the real `amp threads export --help` output on your orb: if the export carries a parent field, `--done` refuses a thread whose parent differs from its row's anchor (`not nested yet: <T> has parent <P>, expected <anchor>`); if it does not, change nothing in code and the docs say to mark a thread done only when update_thread's result shows the expected `parentThreadID`. Say in the report which one held, with the command output.
5. Docs: SKILL.md's nesting bullet and master.md step 5's nest sentence: starts print the instructions; a pending row keeps a thread from the sweep; the archived three-call sequence; take-master re-parents every earlier thread and master; how `--done` is checked. Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Feedback from the s4-hero-1258 master (thread T-01a1feab-6ada-7110-87cc-63c074d88bde, chenchess, Amp threads, local store), items 1-4: four threads started after take-master stayed top-level because only `status` shows `nest-pending`; `update_thread(parent_thread=...)` on an archived thread fails with "An internal error occurred" (reproduced on 15 threads; unarchive, parent, re-archive works); take-master listed earlier verifiers under the old archived master; `nest --done` recorded three threads whose update_thread had failed. This run's master saw the same internal error on three idle archived-looking sidekick threads.
- Code: nest.py (`take_anchor`, `record_pending`, `add_pending`, `pending`, `nest`, `done`, `run_threads`, `discover`), mux/amp.py `record_started` (about line 325) and `assess`.

## Acceptance

- spawn, a rotation, consultant and verify under Amp each print the started thread's `nest-pending:` line and the update_thread instruction.
- The archive sweep keeps a thread whose nest row is pending, with `kept: nest pending`, and archives it after `nest --done`.
- `nest` lists an archived pending thread with the unarchive, parent, re-archive calls.
- After take-master every earlier thread of the run, previous masters included, is pending under the new anchor and none under an old one.
- The report states whether `--done` can check the parent, with evidence; code or docs follow it.
- Existing nest, anchor and archive tests pass; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k nest
python3 tests/run.py -k archive
python3 tests/run.py -k take_master
python3 tests/run.py -k anchor
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push to main and no PR.

## Playbook: bug-fix + pstack-code (your parts)

You own the working tree.

Your parts:
- testing: ## Testing Guidelines
- style: ## Style
- bug-fix.intro-1: You own the investigation and the fix inside the brief's Scope. The master plans and reviews, and th
- bug-fix.intro-2: Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" 
- bug-fix.step-1: Reproduce it yourself on the matching surface via the repo's verification skill, even when a debug o
- bug-fix.step-2: Binary-search the cause. Form the candidate hypotheses, then rule them out until one survives. Seed 
- bug-fix.step-3: Plan the fix. If it crosses a function boundary, `architect` first. You implement the fix inside the
- bug-fix.step-4: 4. Verify on the same surface. The original repro now passes. "Inconclusive" or wrong-surface is not
- bug-fix.step-5: 5. Stage the commits so the failing repro lands before the fix in git history. See the **pstack-tdd*
- bug-fix.step-6: Open a PR only when the brief says so; otherwise the last commit recorded with kitchen.sh step is th
- bug-fix.reply: In the report, under Ran and the sections the template names: what was broken, the root cause, the f

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

### bug-fix.intro-1

You own the investigation and the fix inside the brief's Scope. The master plans and reviews, and the verifier proves. You may delegate to a subagent on your roster model, within your role's limits.

### bug-fix.intro-2

Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" is a hypothesis, not a fix. It does not ship. When evidence refutes a hypothesis, revert what it motivated. The smallest change the evidence justifies ships, nothing more.

### bug-fix.step-1

Reproduce it yourself on the matching surface via the repo's verification skill, even when a debug or instrumentation protocol says to ask the user to reproduce. If the control surface cannot reach the target, say why, specifically, in the report under Questions with status blocked, after driving it as far as it goes; the master asks the human. If it won't reproduce directly, synthesize the trigger, tighten conditions, or instrument until it fires.

### bug-fix.step-2

Binary-search the cause. Form the candidate hypotheses, then rule them out until one survives. Seed them with `how` over the affected subsystem and the **why** skill for regression history. Each pass, take the split that cuts the most remaining problem space, get runtime evidence, eliminate. When program state is unclear, add instrumentation or logging and read it as the code runs. Don't guess. Keep a long or stubborn hunt going inside your session and record each change of approach with a progress line. Confirm the surviving *mechanism* with runtime evidence before the step-3 architect fan-out.

### bug-fix.step-3

Plan the fix. If it crosses a function boundary, `architect` first. You implement the fix inside the brief's Scope with a specific scope in mind; you may delegate to a subagent on your roster model, within your role's limits, for disjoint scoped edits that you integrate serially.

### bug-fix.step-4

4. Verify on the same surface. The original repro now passes. "Inconclusive" or wrong-surface is not a pass. Flag it. Unit tests show branch behavior, not bug absence.

Pending reads (load each before acting on that part):
- bug-fix.step-5: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 040 sidekick bug-fix.step-5
- bug-fix.step-6: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 040 sidekick bug-fix.step-6
- bug-fix.reply: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 040 sidekick bug-fix.reply

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
