# Brief 053: landing-rebase-6

playbook: bug-fix
timebox: 60
commit: yes, the cherry-picked commits plus one commit per conflict fix, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/053-landing-rebase-6.md

## Goal

Build one branch, `kitchen-followups-landing-6`, on current origin/main (29cc53c2 or later). It holds the 21 commits of `699837d85..f117480b` (units 047, 049, 051 and 052, as verdict 052-relay-keeps-bundle-id-v1 verified them) and the full suite passes at its head. That stack leaves main at 699837d8, before units 040 and 046 landed (rebased by 050 into 1842e862..29cc53c2), so it is not a fast-forward.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/**
- skills/pstack-pair/SKILL.md
- skills/pstack-kitchen/scripts/**
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/**
- tests/**

## Steps

1. `git fetch origin && git checkout -B kitchen-followups-landing-6 origin/main`, then `git cherry-pick 699837d853a868ed785fa843cb167ee3e559c3bc..f117480b83a23ccbceccdf5d394e552470e89391`. At each conflict, regenerate `tests/SPEC.md` with `python3 tests/spec_gen.py`.
   - Files main changed through units 040 and 046 that this stack also touches: skills/pstack-pair/scripts/pstack_core/mux/amp.py (046 added the snooze checks and nest-on-start; 047 to 052 changed `role_is_idle`, `took_a_brief`, `keep_sent_bundle` and `keep_bundle_by_id`), skills/pstack-kitchen/scripts/kitchen_core/dispatch.py, skills/pstack-kitchen/SKILL.md, skills/pstack-kitchen/references/master.md, tests/test_pstack_core.py, tests/test_pstack_kitchen.py and tests/test_pstack_pair.py.
   - Keep both sides' behaviour.
   - In the report, name each conflict with its file and what each side did.
   - Record one step at the cherry-picked head.
2. Fix anything the full suite finds, one commit per fix, each recorded. Change no behaviour beyond combining 047 to 052 with main.

## Context

- reviews/047-*.md, 049-*.md, 051-*.md and 052-*.md: all accept.
- verdicts/052-relay-keeps-bundle-id-v1.md: clean for 047, 049, 051 and 052, on 699837d85..f117480b8.
- briefs/050-landing-rebase-5.md and reports/050-landing-rebase-5.md: the previous landing of this kind, which met the same files.

## Acceptance

- `git log origin/main..HEAD` holds the 21 commits by subject, plus any step 2 fixes, and no subject appears twice.
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head.

## Verify

```bash
test "$(git rev-list --count origin/main..HEAD)" -ge 21
test -z "$(git log --format=%s origin/main..HEAD | sort | uniq -d)"
python3 tests/run.py -k bundle
python3 tests/run.py -k relay
python3 tests/run.py -k snooze
python3 tests/run.py -k archive
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv lock --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No push to main and no PR.
- Never end your turn while `kitchen.sh step` or `finish` prints `still running`; rerun it in the foreground.
- No `time.sleep` in tests, and no elapsed-time asserts.

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
- bug-fix.step-5: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 053 sidekick bug-fix.step-5
- bug-fix.step-6: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 053 sidekick bug-fix.step-6
- bug-fix.reply: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 053 sidekick bug-fix.reply

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
