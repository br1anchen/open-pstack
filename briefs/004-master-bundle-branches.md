# Brief 004: master-bundle-branches

playbook: bug-fix
timebox: 120
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/004-master-bundle-branches.md

## Goal

A master hand-over under the local store (`rotate-master` under `PSTACK_MUX=amp`) carries every local branch name and tip to the next master, which creates or fast-forwards those branches safely; and a sync that finds the checkout diverged from the sender (a role rebased the branch) keeps it and prints a guarded recovery recipe instead of a bare "kept".

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md
- skills/pstack-pair/scripts/pstack_core/gitstore.py

## Steps

The design is the consultant's advice, `advice/004-master-bundle-branches-c1.md` in the store, sections "Branch transport and application" (points 1-6) and "Divergent and rebased checkouts". Read both first; this brief summarises them and the advice wins on any detail it settles.

1. **Pack every local head on a master hand-over only.** Add an explicit bundle mode (a keyword such as `branches=True`) from `Amp.launch_master` (mux/amp.py about L1351-1366) through `send`/`with_bundle` to `bundle.pack` (bundle.py L130-176). Ordinary role messages keep today's HEAD-plus-scratch payload. In that mode, record a manifest map `git.branches: {name: full_oid}` of every `refs/heads/*` (including branches origin already holds) and pack their tips into the same one git bundle via the existing ref map in `git_bundle` (L110-127). Cut only against a baseline the receiver fetches: origin's default branch as freshly fetched, else a self-contained bundle; never `--not --remotes` in this mode (a sender-only remote would empty it). Old manifests without `branches` keep their meaning. Test: a fresh receiver lacking the objects gets two unpushed non-HEAD branches (one with a `/` in its name) after rotate-master, sync and take-master; an ordinary role message's bundle does not carry them. Commit, `kitchen.sh step`.
2. **Apply branches conservatively.** In `apply_git` (L326-360): fetch the incoming tips into a per-bundle side namespace (`refs/pstack/in/<tag>/<bundle-id>/...`, not replaced by the next send). Validate each name as a head ref (`git check-ref-format --branch`). For each recorded branch: absent locally → create; present, not checked out in any worktree (`git worktree list --porcelain`), and its tip an ancestor of incoming → update with an expected-old-OID `update-ref`; ahead or divergent → keep and print both tips and the side ref; checked out in another worktree → keep with a line saying so; the current worktree's branch goes through `move_checkout` as today. Never delete or force-update a receiver branch. An object import failure exits non-zero before take-master treats the hand-over as complete; a ref conflict does not. One table-driven test over: absent, fast-forward, local ahead, divergent, checked out in a second worktree, repeat apply (idempotent). Commit, step.
3. **A diverged checkout gets a recovery recipe.** In `move_checkout` (L298-323), keep the refusal. When it reports `kept ...: it diverged`, also print the current branch, the old OID, the incoming OID and its durable side ref, and a recipe: check you are on <branch> at <old-oid> with no local changes, `git branch kitchen-recovery/<old-oid> <old-oid>` (no force), then `git reset --keep <full-incoming-oid>`. Shell-quote names. No automatic reset, no patch-id test (the advice's two counterexamples show patch-id containment can lose wanted content). Test: a role rebases the branch, sync prints the recipe and leaves HEAD and the branch unchanged; following the recipe moves it and keeps the recovery branch. Commit, step.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-025 && git checkout -B kitchen-followups FETCH_HEAD` (unit 025's head 6a83f50, on top of the run's stack). Do not rebase onto origin/main, and leave the `master-is-anchor` branch (PR #13) alone.
- Gap 1 and gap 4 of run sidekick-ab2: `notes/kitchen-gaps.md` in the store.
- Consultant probes: `advice/004-master-bundle-branches-c1-probe.py` (counterexamples; run as its header says, not part of the suite).
- Closest tests: `test_the_bundle_carries_the_sidekicks_commit_to_a_checkout_that_lacks_it` (tests/test_pstack_kitchen.py about L17059), `test_take_master_in_the_new_thread_from_the_bundle_it_received` (about L16439), `BundleMergeTests` (tests/test_pstack_core.py about L20643).
- The host roster's hand-over is a separate unit; do not touch it here.

## Acceptance

- After rotate-master, sync and take-master into a checkout lacking them, every local branch of the old master (including two unpushed non-HEAD branches, one named with a `/`) exists at the same tip.
- An ordinary role message (a report or steer bundle) carries no extra branches.
- A local branch ahead of or diverged from the incoming tip, or checked out in another worktree, is left unchanged and named in the output; an absent one is created and an ancestor one fast-forwarded; a second apply changes nothing.
- A sync onto a checkout a role rebased leaves HEAD and the branch unchanged and prints the recovery recipe with full OIDs and the `kitchen-recovery/<old-oid>` branch; no automatic reset happens.
- A failed object import makes the hand-over's sync exit non-zero.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k take_master
python3 tests/run.py -k rotate_master
```

## Forbidden

- No automatic reset or branch move based on patch-ids.
- pstack_core imports nothing from kitchen_core.

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
- bug-fix.step-5: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 004 sidekick bug-fix.step-5
- bug-fix.step-6: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 004 sidekick bug-fix.step-6
- bug-fix.reply: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 004 sidekick bug-fix.reply

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
