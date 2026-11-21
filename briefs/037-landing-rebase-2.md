# Brief 037: landing-rebase-2

playbook: bug-fix
timebox: 60
commit: yes, the rebased commits plus one commit per resolution step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/037-landing-rebase-2.md

## Goal

Rebase the run's accepted stack onto main so Brian's approved second push can land. The stack is `c5cf2504..84ea957f` (46 commits, units 005, 022-026, 028, 030-036, 004, 006); main is `be882968`, which already holds units 001-003 and 017-020 (rebased) and PR #13 (units 027 and 029, master-is-anchor). The master's trial `git rebase --onto be882968 c5cf2504 84ea957f` stops at commit 26 of 46, `fe439aee` (unit 022, `nest --discover`), with a conflict in `skills/pstack-pair/scripts/pstack_core/commands/nest.py`: PR #13 added `anchor_title` and `take_anchor` where 022 added `oldest_master`, `run_threads` and `discover`. Earlier commits conflict only in `tests/SPEC.md`.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/**
- skills/pstack-kitchen/scripts/**
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/**
- tests/**

## Steps

1. **Rebase.** `git rebase --onto be88296826fbc6e1c7ab0ebf8a1dc26467c573cf c5cf2504 84ea957f`. Where only `tests/SPEC.md` conflicts, run `python3 tests/spec_gen.py` and `git add tests/SPEC.md`. In `nest.py` keep both sides: PR #13's `anchor_title`/`take_anchor` and 022's `oldest_master`/`run_threads`/`discover`, reconciling helper names (`add_pending` on main against `record_pending` in 022) so one helper remains. Resolve any later conflict the same way: keep both units' behaviour. Keep every commit's message; record the rebased stack with one `kitchen.sh step` naming the old and new heads.
2. **discover follows master-is-anchor.** Under PR #13 the current master is the run's top-level anchor and takes it over on rotation (`take_anchor`). 022's `discover` sets a missing anchor to the *oldest* master, which on a rotated run is an archived generation. Make a missing anchor default to the current master's thread (`pair.json` `.master.pane_id` `amp:T-...`, else the newest `.master.sessions` entry), and record that thread as nest-pending `TOP_LEVEL` the way `take_anchor` does, not under itself. Drop `oldest_master` if nothing else uses it. Update 022's test so it asserts the current master, with a rotated run (two master sessions) where oldest and current differ; show it fails before this step.
3. **Whole suite at the rebased head.** Fix whatever the combination breaks, one commit per fix with its test.

## Context

- Start: `git fetch origin main kitchen-code/kitchen-followups/unit-036 && git checkout -B kitchen-followups-landing 84ea957f4c38312234be77534684fff3d7e8ff82`. Do not touch the branch you used for 010.
- Unit 022's brief and review: `briefs/022-migrated-threads-archive-nest.md`, `reviews/022-*.md`. PR #13: https://github.com/br1anchen/open-pstack/pull/13 (`take_anchor`, standing order 27).
- Report the new head; the master pushes it to `kitchen-code/kitchen-followups/unit-037` and runs `verify --landing`.

## Acceptance

- `git log be88296..HEAD` holds every one of the 46 commits (by subject) plus the step 2 and step 3 commits, and nothing from 001-003/017-020 twice.
- `discover` on a rotated run with no anchor sets the current master, recorded top-level; the test fails without step 2.
- 022's and PR #13's tests pass, and so does the whole suite.

## Verify

```bash
test "$(git rev-list --count be88296..HEAD)" -ge 47
python3 tests/run.py -k nest
python3 tests/run.py -k anchor
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv lock --check
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
- bug-fix.step-5: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 037 sidekick bug-fix.step-5
- bug-fix.step-6: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 037 sidekick bug-fix.step-6
- bug-fix.reply: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 037 sidekick bug-fix.reply

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
