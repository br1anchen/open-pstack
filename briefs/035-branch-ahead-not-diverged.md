# Brief 035: branch-ahead-not-diverged

playbook: bug-fix
timebox: 45
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/035-branch-ahead-not-diverged.md

## Goal

When a hand-over arrives with the receiver's checkout on branch X and the receiver's copy of the sender's branch Y only ahead of the sender's tip (the sent head is an ancestor of local Y), the sync reports Y as "diverged" and prints a recovery recipe. Y is ahead, not diverged: say so, print no recipe, and keep reporting Y once. Brian asked for this before landing (verdict 033-v1 observation 2).

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **Ahead is not diverged.** In `bundle.py` `move_checkout`, the branch case (`mine = f'refs/heads/{branch}'` ... `own_commits(root, mine, head)`) treats any commit Y holds past the sent head as divergence. Before calling `diverged`, check `git merge-base --is-ancestor <head> refs/heads/<branch>`: when true, return one line, `checkout: kept <local>: <on> stays, and branch <Y> here is ahead of <sender> (<head short>)`, with `branch` as the reported branch, so apply_branches prints no second Y line, and no recipe. A real divergence (neither side an ancestor) keeps today's line and recipe. Add one table-driven test over the receiver-on-X cases: Y ahead (one line, says ahead, no `kitchen-recovery`), Y diverged (one line, says diverged, has the recipe), Y behind (fast-forwarded as today). Show the ahead row fails without the fix by scratch revert.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-034 && git checkout -B kitchen-followups FETCH_HEAD` (unit 034's head, which the master pushes when 034 reports; on top of 033's 179a195). Do not rebase onto origin/main. If unit-034 is not on origin, report blocked.
- Verdict `verdicts/033-master-bundle-branches-fix-v1.md`, observation 2 and Evidence D1 (ahead-elsewhere). Closest test: `test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here`.

## Acceptance

- The three-row test passes at the head; its ahead row fails without the fix.
- 033's Acceptance still holds and every bundle, sync and take_master test passes.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k sync
python3 tests/run.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push and no PR.

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
- bug-fix.step-5: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 035 sidekick bug-fix.step-5
- bug-fix.step-6: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 035 sidekick bug-fix.step-6
- bug-fix.reply: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 035 sidekick bug-fix.reply

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
