# Brief 047: bundle-and-rotation-fixes

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/047-bundle-and-rotation-fixes.md

## Goal

The four medium findings joo raised on the second landing and the s4-hero master's gaps 5 and 6 are fixed on top of main, each with a test that fails without it: a late reply never deletes a file its sender did not write; a bundle packed while origin is unreachable is self-contained again; an ordinary send keeps local-only evidence the receiver lacks; a rotation or a spawn after `close` whose new thread never sends READY can be rotated again; and spawn on an orb with no host roster starts the role from the run's recorded entry.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/spawn.py
- skills/pstack-pair/scripts/pstack_core/send.py
- skills/pstack-pair/scripts/pstack_core/store.py
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- skills/pstack-kitchen/scripts/kitchen_core/setup.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-pair/SKILL.md
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py and commands/snooze.py (units 040 and 046 are not on main yet)
- anything else

## Steps

Start from main (`git fetch origin main && git checkout -B kitchen-followups-047 origin/main`). Each step adds the failing test first, in the same commit as its fix.

1. A reply never deletes what its sender did not write (bundle.py `merge_store` ~479 / `merge_file` ~438). Today a reply merged against a newer base deletes a file the master wrote after the role's snapshot (base == local, incoming None -> None), for example a gates-mode verdict under `verdicts/`. In `merge_store`, when `reply` is set and the reply's file list lacks a path, keep the local copy (no conflict). Test: local store under Amp; the master writes `verdicts/00X-...-v1.md` (gates mode) after the role's snapshot, sends the role a newer bundle, then the role's older reply arrives: the verdict is still there.
2. Self-contained bundle when origin is down (bundle.py `origin_baseline` ~191). Before the second landing a failed `git fetch origin` returned '' and the bundle carried every commit; now it cuts at a stale tracking ref. Restore that: when the bounded fetch fails, return ''. Also bound the receiver's fallback `git fetch origin` in `fetch` (~540): no prompt (`GIT_TERMINAL_PROMPT=0`) and the same timeout the bounded fetch helper uses (find it; the 036 follow-up named one helper for it, reuse it if it exists). Test: fake origin unreachable at pack: the bundle applies in a clone that lacks origin's tracking commit.
3. Evidence survives an ordinary send (bundle.py `pack` ~218/~258). Since the second landing an ordinary send with keep refs is cut against the sender's `--remotes`, which the receiver may not have, and the bundle fails to apply. Cut a send that carries keep refs (evidence) against `origin_baseline` as before; keep `--remotes` only for sends with no keep refs. Test: sender's refs/remotes hold a commit the receiver lacks, the send carries a local-only evidence commit: the receiver keeps it (`import_git` applies, `unkept_evidence` empty).
4. A failed bootstrap can be rotated again (spawn.py `record_started` ~209; s4-hero gap 5). `record_started` clears `rotation_required` as soon as the new generation is recorded, before READY, so a new thread that never boots leaves dispatch (FreshSessionRequired) and rotate (exit 5, "partial or blocked work keeps its session") refusing each other; spawn after `close` instead keeps the old generation's `rotation_required=true` and dispatch refuses after READY. One rule: `rotation_required` is cleared when the new generation's READY settles (`store.mark_ready` / `settle_ready`), never at start, and `rotate` allows a rotation while `bootstrap_pending` is true with no READY (say `rotate: the new sidekick never sent READY; rotating again`). Tests: one table over (rotation, spawn after close) x (READY arrives, never arrives) -> dispatch after READY sends; with no READY, rotate rotates again and dispatch refuses with FreshSessionRequired naming rotate.
5. Spawn without a host roster (kitchen_core/setup.py ~220 and ~298; s4-hero gap 6). When the orb has no roster entry for the role and no `--kind`, fall back to the run's recorded entry: `pair.json .sidekick` (`kind`, `start_args`, or `.sidekick_choice`/`.sidekick.chain` if present) for the sidekick, `.consultant` for the consultant, and print `spawn: no host roster here; starting the run's recorded <role> (<label>)`. Keep today's error when the store records none. Test: no roster file, a store with a recorded sidekick: spawn starts it with that kind and args.
6. Docs: SKILL.md lines for each behaviour that changed (READY clears the rotation flag; spawn's fallback to the recorded entry). Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- joo 2026-11-21T15-08-59-362Z-0cf5d75c on be882968..daf51dc0 (second landing): bundle.py:379, bundle.py:173, bundle.py:258, spawn.py:226 mediums, full text in followups.md ("joo on the landing stack").
- s4-hero-1258 master feedback (T-01a1feab-6ada-7110-87cc-63c074d88bde), items 5 and 6, followups.md; it worked around item 5 with `store.settle_ready(store, 'sidekick')`.
- Line numbers above are from that run; find the functions by name on main.

## Acceptance

- Each of steps 1-5 has a test that fails on main and passes at the head (say in the report how each was shown failing).
- A late reply keeps a master-written verdict; a bundle packed with origin down applies without origin; an ordinary send keeps local-only evidence; a bootstrap that never sends READY can be rotated again and spawn after close dispatches after READY; spawn with no roster starts the recorded entry.
- The full suite, check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k rotat
python3 tests/run.py -k spawn
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No change to nest.py or snooze.py.
- No `time.sleep` in tests, and no elapsed-time asserts.
- Never end your turn while `kitchen.sh step` or `finish` prints `still running`; rerun it in the foreground.
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
- bug-fix.step-5: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 047 sidekick bug-fix.step-5
- bug-fix.step-6: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 047 sidekick bug-fix.step-6
- bug-fix.reply: kitchen.sh playbook-part /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups 047 sidekick bug-fix.reply

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
