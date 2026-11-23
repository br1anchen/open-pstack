# Verify 039: roster-handoff-fix

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/039-roster-handoff-fix-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/039-roster-handoff-fix-verify
range: 7218f3112db91d4448b20d708b5c92117b6543db..fc6d096b77343386ce9f5ee5b45320ab6abb35f7
risk: escalated
units: 010 039
verifier-agent: claude --model claude-opus-5-5
verifier-class: escalated
verifier-outcome: running
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

Unit 039 fixes unit 010: prove every unit's Acceptance at this one head.

## Unit 010

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/010-roster-handoff.md

## Goal

`rotate-master` snapshots the host roster into the store, and `take-master` installs it on the next master's orb when that orb has no roster, so verifiers and new roles keep their models after a hand-over.

## Acceptance

- After rotate-master and take-master on an orb with no roster, the orb's `roster_path()` holds the old master's roster byte for byte.
- An existing roster on the receiving orb is never overwritten; a different one is reported by path.
- A roster that fails the secret scan or TOML parse refuses the rotation and writes no snapshot.
- A store with no recorded snapshot takes over as before, with the notice line.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 010: bug-fix + pstack-code

### bug-fix.intro-1

You own the proof, not the fix. Prove the brief's Acceptance on the real surface at the unit's head. You may delegate to a subagent on your roster model, within your role's limits, for independent reads and probes only.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md intro:1 244bab33ef86

### bug-fix.intro-2

Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" is a hypothesis, not a fix. It does not ship. When evidence refutes a hypothesis, revert what it motivated. The smallest change the evidence justifies ships, nothing more.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md intro:2 6a4d6e5f2d16

### bug-fix.step-1

Reproduce the original failure yourself on the matching surface at the unit's parent, then show it gone at the unit's head. A surface you cannot drive is reported inconclusive in the verdict with the specific reason; you never ask the human.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md step:1 537cb8336044

### bug-fix.step-3

The fix is the sidekick's. Check the diff against the mechanism the evidence supports; a change the evidence does not justify is a finding.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md step:3 254702d1f198

### bug-fix.step-4

4. Verify on the same surface. The original repro now passes. "Inconclusive" or wrong-surface is not a pass. Flag it. Unit tests show branch behavior, not bug absence.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md step:4 2bda1ead9cea

### bug-fix.step-5

Check from the unit's commit range that the failing repro or test lands before the fix in history; where it does not, say so as a finding. Do not restage or edit commits.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md step:5 842691b3d4d5

### bug-fix.reply

The evidence standard for the brief's Acceptance, not new criteria: the failing-then-passing repro output, verbatim. A verdict line cites the command output that shows it.

from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md reply 2b41ae474b55

### testing

Check the unit's tests against this: deterministic, with no sleep and no elapsed-time assert; hermetic of PSTACK_KITCHEN_*, KITCHEN_*, PAIR_*, HERDR_*, REX_*, PSTACK_MUX, AMP_THREAD_ID and AMP_CURRENT_THREAD_ID; each source change in the same commit as the test that pins it; every tests/SPEC.md line naming its test. This is the evidence standard for the verdict, not new Acceptance.

from overlay:pstack-code, repo:AGENTS.md heading:Testing Guidelines 5be47ddb4e70

Brief commands:

Run each command in a fresh shell from the scratch root.

Command 1:

```bash
python3 tests/run.py -k take_master
```

Command 2:

```bash
python3 tests/run.py -k roster
```

## Unit 039

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/039-roster-handoff-fix.md

## Goal

Under the git store (`PSTACK_STORE=git`) the roster snapshot that unit 010 added never reaches the published `kitchen/<slug>` branch: it stays a local-only store file that the old master hands to the next master with `upload_thread_file`, and take-master continues with a warning when it never arrives. Fix joo's three lows on the same code.

## Acceptance

- Under the git store, a rotate-master with a host roster pushes no `sessions/*-roster.toml` to the store branch (the fake origin's tree), and the branch's .gitignore lists it.
- Under the git store rotate-master prints the upload relay line naming the next master thread; under the local store the roster still reaches the next master's orb as in 010.
- take-master under the git store with the snapshot missing prints `did not arrive` and exits 0 with the hand-over done; with it present it installs it as in 010.
- The three lows hold as step 3 says; 010's existing tests pass.
- Docs describe the git-store hand-over; check-pstack and spec_gen --check pass.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 039: review-fix

### review-fix.intro-1

You own the proof, not the fixes. Prove the brief's Acceptance, finding by finding, on the real surface at the unit's head.

from workflow:review-fix, skills:pstack-kitchen/references/review-fix.md intro:1 ed5060cf2bab

### review-fix.step-2

Check from the unit's commit range that each listed finding has its commit and the test that pins it, or a commit message saying it has no testable behaviour. A fix brief has no repro-first rule: a test in the same commit as its fix is no finding. Do not write or edit commits.

from workflow:review-fix, skills:pstack-kitchen/references/review-fix.md step:2 433ae2931bf7

### review-fix.reply

The evidence standard for the brief's Acceptance, not new criteria: per finding, the command output that shows it fixed at the unit's head. A verdict line cites that output.

from workflow:review-fix, skills:pstack-kitchen/references/review-fix.md reply bfd6669fe262

Brief commands:

Run each command in a fresh shell from the scratch root.

Command 1:

```bash
python3 tests/run.py -k roster
```

Command 2:

```bash
python3 tests/run.py -k take_master
```

Command 3:

```bash
python3 tests/run.py -k gitstore
```

Command 4:

```bash
python3 tests/run.py
```

Command 5:

```bash
python3 scripts/check-pstack.py
```

Command 6:

```bash
python3 tests/spec_gen.py --check
```

Command 7:

```bash
uv run --frozen ruff format --check
```

Command 8:

```bash
uv run --frozen ruff check
```

Command 9:

```bash
uv check --frozen
```

## Context: units between

These are decisions a review accepted; do not judge them again. Judge only the named units' Acceptance.

unit 001-swallowed-failures: accepted (reviews/001-swallowed-failures.md)

Amendments:

none

unit 002-amp-idle-and-stop: accepted (reviews/002-amp-idle-and-stop.md)

Amendments:

none

unit 003-packet-accepted-deviations: accepted (reviews/003-packet-accepted-deviations.md)

Amendments:

none

unit 004-master-bundle-branches: accepted (reviews/004-master-bundle-branches.md)

Amendments:

none

unit 005-kept-sidekick-session: accepted (reviews/005-kept-sidekick-session.md)

Amendments:

none

unit 006-land-check-gone-head: accepted (reviews/006-land-check-gone-head.md)

Amendments:

none

unit 017-swallowed-failures-fix: accepted (reviews/017-swallowed-failures-fix.md)

Amendments:

none

unit 018-amp-idle-and-stop-fix: accepted (reviews/018-amp-idle-and-stop-fix.md)

Amendments:

none

unit 019-role-stamp-under-git: accepted (reviews/019-role-stamp-under-git.md)

Amendments:

none

unit 020-landing-packet-amendments: accepted (reviews/020-landing-packet-amendments.md)

Amendments:

none

unit 022-migrated-threads-archive-nest: accepted (reviews/022-migrated-threads-archive-nest.md)

Amendments:

none

unit 023-dispatch-after-ready-sends: accepted (reviews/023-dispatch-after-ready-sends.md)

Amendments:

none

unit 024-verify-names-excluded-fix: accepted (reviews/024-verify-names-excluded-fix.md)

Amendments:

none

unit 025-reply-deletes-no-master-state: accepted (reviews/025-reply-deletes-no-master-state.md)

Amendments:

none

unit 026-dispatch-after-ready-sends-fix: accepted (reviews/026-dispatch-after-ready-sends-fix.md)

Amendments:

none

unit 028-dispatch-after-ready-sends-fix: accepted (reviews/028-dispatch-after-ready-sends-fix.md)

Amendments:

none

unit 030-reply-deletes-no-master-state-fix: accepted (reviews/030-reply-deletes-no-master-state-fix.md)

Amendments:

none

unit 031-master-bundle-branches-fix: accepted (reviews/031-master-bundle-branches-fix.md)

Amendments:

none

unit 032-master-bundle-branches-fix2: accepted (reviews/032-master-bundle-branches-fix2.md)

Amendments:

none

unit 033-master-bundle-branches-fix: accepted (reviews/033-master-bundle-branches-fix.md)

Amendments:

none

unit 034-land-check-gone-head-fix: accepted (reviews/034-land-check-gone-head-fix.md)

Amendments:

none

unit 035-branch-ahead-not-diverged: accepted (reviews/035-branch-ahead-not-diverged.md)

Amendments:

none

unit 036-land-check-gone-head-fix: accepted (reviews/036-land-check-gone-head-fix.md)

Amendments:

none

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/010-roster-handoff.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/039-roster-handoff-fix.md: read it; do not write it

## Standing orders

From /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md. An order about the verifier, or about running the repo's commands and tests, binds you; one about the sidekick's scope, steps, commits or reports does not.

Numbered lines, one constraint each. The master pastes the path of this file
into every brief. Append a line the moment you catch yourself repeating an
instruction (the encode-lessons-in-structure principle).

1. Sidekick writes only inside the brief's SCOPE. Anything else is a follow-up in the report.
2. Every claim in a report carries its command output or file pointer in the same section.
3. No push, no PR, no force-push, no rebase of shared branches unless the brief says so.
4. On timebox expiry, stop, write a partial report, end the turn.
5. A question for the human goes in the report under Questions with status `blocked`; the master owns the human.
6. Approval dialogs: the master reads them and asks the human. Neither agent answers the other's dialogs.
7. Devin defaults to bypass permissions; other kinds inherit the master's permission mode. Explicit permission overrides take precedence. Neither agent changes its own mode.
8. Sidekick progress log: one line per completed todolist step and per change of approach, via `kitchen.sh progress`. A STEER is read the moment it arrives, between tool calls, and agreed or objected to there; two fresh steers per brief, two rounds each.
9. Consultant never writes a non-ignored path in the shared tree and never builds, tests, or installs there. Prototypes and builds run in `kitchen.sh scratch`, removed before the advice is sent.
10. Consultant advice is advisory: the master decides and records an overrule in the review. Three consults per unit; a fourth is a plan round.
11. Sidekick and consultant never message each other. Both read the store; only the master writes plans, briefs, steers, consults, reviews.
12. Sidekick records every step with `kitchen.sh step`; a step whose gates fail is fixed in a new commit, never skipped or recorded by hand.
13. A repo policy rule is opted out of only on the line, with `kitchen-allow: <id>` and the reason beside it; the review reads every such line.
14. The verifier writes only its verdict file and the scratch worktree it was given, and never edits the code it verifies.
15. Landing never merges; it goes as far as `[landing].mode` in the repo's kitchen.toml, and only after `kitchen.sh land-check` passes.
16. Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
17. Landing goes as far as `commit` (kitchen.toml landing.mode) and never merges.

## Brian's standing rules (kitchen-followups)

18. The master writes briefs and reviews and never edits code.
19. joo runs on every unit: `joo-dev` with stdin from /dev/null; results in runs/*/executions. The review note cites the run.
20. The master rotates only above 60% context or after a compaction; the "N units verified; rotate" advice is ignored.
21. Role chains are subscription-first (Claude subscription, ChatGPT subscription, then Amp-billed).
22. Every push to main needs Brian's explicit authorization in the thread. The master asks before landing.
23. No secrets in a store.
24. Each new role thread is nested under the run's anchor with update_thread, as `kitchen.sh nest` says.
25. Git store (Brian, 2026-11-20): this run moves to `PSTACK_STORE=git`; pushing `kitchen/*` run branches to br1anchen/open-pstack is allowed without asking. Pushes to main still need Brian's explicit authorization (order 22).
26. Sidekick A/B (Brian, 2026-11-20): routine units of similar size alternate GLM-5.3 (Amp-billed, a small number of units) and Sonnet 5.5 on Claude Code; escalated units run on Sonnet. Each unit's model is recorded (`pair.json` `.sidekick_models`) and each retro appends Run log rows to the comparison artifact (Weekly summary untouched).
27. Master is the anchor (Brian, 2026-11-21): the current master is the run's top-level anchor thread, titled `kitchen-followups master`; every role thread nests under it. On rotation, until unit 027 lands, the new master by hand makes itself top-level (update_thread parent_thread null) with the anchor title, re-nests the live role threads under itself, and sets pair.json `.anchor.thread` to itself.
28. GLM trial end (Brian, 2026-11-21, supersedes the GLM part of 26): no more Amp credits. Alternate GLM-5.3 and Sonnet on routine units only while Amp credits work; a GLM start or unit that fails on credits is logged (`kitchen.sh log`), rerun on Sonnet (Claude Code) and not counted as a GLM unit. End the trial and put every sidekick on Sonnet at about 20 units per model, or sooner when the comparison artifact's decision rule is clear. Keep appending Run log rows to the comparison artifact. Every handoff carries this; when the trial ends, reply to Puck T-01a1eafa-f54b-71ff-890f-07428ab9c3e3 with the units on each model.
29. Snoozed role threads (Brian, 2026-11-21, revised): role threads are not snoozed at start; the master snoozes one (update_thread snooze="indefinite") only once it has no open work (unit accepted or rejected, consult or verification answered) and its state is settled, unsnoozes it (snooze="off") before giving it new work, and never snoozes a master or a thread blocked on a question or error. A role blocked on a question for Brian or an error only he can fix is relayed to him from the master thread.

## Changed

```
 skills/pstack-kitchen/SKILL.md                     |   55 +-
 skills/pstack-kitchen/references/master-verify.md  |   11 +-
 skills/pstack-kitchen/references/master.md         |   10 +
 skills/pstack-kitchen/references/verifier.md       |    3 +
 .../pstack-kitchen/scripts/kitchen_core/landing.py |  100 +-
 .../pstack-kitchen/scripts/kitchen_core/master.py  |  192 ++-
 .../pstack-kitchen/scripts/kitchen_core/setup.py   |    6 +-
 skills/pstack-kitchen/scripts/kitchen_core/step.py |    2 +
 .../pstack-kitchen/scripts/kitchen_core/verify.py  |    4 +
 .../scripts/kitchen_core/verify_launch.py          |   16 +-
 .../scripts/kitchen_core/verify_packet.py          |   45 +-
 skills/pstack-pair/scripts/pstack_core/bundle.py   |  630 +++++++-
 .../scripts/pstack_core/commands/nest.py           |   85 +-
 .../scripts/pstack_core/commands/relay.py          |   34 +-
 .../scripts/pstack_core/commands/rotate.py         |  118 +-
 .../scripts/pstack_core/commands/steer.py          |   24 +-
 .../scripts/pstack_core/commands/stop.py           |   27 +-
 .../scripts/pstack_core/commands/store_to_git.py   |    2 +
 .../scripts/pstack_core/commands/sync.py           |    8 +-
 skills/pstack-pair/scripts/pstack_core/git.py      |   62 +-
 skills/pstack-pair/scripts/pstack_core/gitstore.py |   45 +-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  100 +-
 .../scripts/pstack_core/mux/amp_routing.py         |   13 +-
 skills/pstack-pair/scripts/pstack_core/mux/base.py |    7 +
 skills/pstack-pair/scripts/pstack_core/send.py     |   20 +
 skills/pstack-pair/scripts/pstack_core/spawn.py    |    4 +
 skills/pstack-trio/scripts/variant.py              |   26 +-
 tests/SPEC.md                                      |  276 ++--
 tests/pstack-pair/bin/amp                          |    8 +-
 tests/run.py                                       |    5 +-
 tests/test_pstack_core.py                          | 1352 ++++++++++++++++-
 tests/test_pstack_kitchen.py                       | 1576 +++++++++++++++++++-
 32 files changed, 4519 insertions(+), 347 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/039-roster-handoff-fix-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/039-roster-handoff-fix-verify gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c # unmeasured
```

feature map: none listed
verification skill: 
