# Verify 010: roster-handoff

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/010-roster-handoff-v1.md
template: /home/user/.local/share/pstack-builds/6aaa6770ea89df2b/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/010-roster-handoff-verify
range: 7218f3112db91d4448b20d708b5c92117b6543db..c40b7b0cd3989064a31295d45eb8cc880164f64e
risk: escalated
units: 010
verifier-agent: claude --model claude-opus-5-5
verifier-class: escalated
verifier-outcome: clean
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

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

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/010-roster-handoff.md: read it; do not write it

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
29. Snoozed role threads (Brian, 2026-11-21): role threads are snoozed and hidden from Brian; when a role is blocked on a question for him or errors in a way only he can fix, the master relays it to him from the master thread.

## Changed

```
 skills/pstack-kitchen/SKILL.md                     |   43 +-
 skills/pstack-kitchen/references/master-verify.md  |   11 +-
 skills/pstack-kitchen/references/verifier.md       |    3 +
 .../pstack-kitchen/scripts/kitchen_core/landing.py |  100 +-
 .../pstack-kitchen/scripts/kitchen_core/master.py  |  134 +-
 .../pstack-kitchen/scripts/kitchen_core/setup.py   |    6 +-
 skills/pstack-kitchen/scripts/kitchen_core/step.py |    2 +
 .../pstack-kitchen/scripts/kitchen_core/verify.py  |    4 +
 .../scripts/kitchen_core/verify_launch.py          |   16 +-
 .../scripts/kitchen_core/verify_packet.py          |   45 +-
 skills/pstack-pair/scripts/pstack_core/bundle.py   |  630 ++++++++-
 .../scripts/pstack_core/commands/nest.py           |   85 +-
 .../scripts/pstack_core/commands/relay.py          |   34 +-
 .../scripts/pstack_core/commands/rotate.py         |  118 +-
 .../scripts/pstack_core/commands/steer.py          |   24 +-
 .../scripts/pstack_core/commands/stop.py           |   27 +-
 .../scripts/pstack_core/commands/store_to_git.py   |    2 +
 .../scripts/pstack_core/commands/sync.py           |    8 +-
 skills/pstack-pair/scripts/pstack_core/git.py      |   62 +-
 skills/pstack-pair/scripts/pstack_core/gitstore.py |   39 +-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  100 +-
 .../scripts/pstack_core/mux/amp_routing.py         |   13 +-
 skills/pstack-pair/scripts/pstack_core/mux/base.py |    7 +
 skills/pstack-pair/scripts/pstack_core/send.py     |   20 +
 skills/pstack-pair/scripts/pstack_core/spawn.py    |    4 +
 skills/pstack-trio/scripts/variant.py              |   26 +-
 tests/SPEC.md                                      |  274 ++--
 tests/pstack-pair/bin/amp                          |    8 +-
 tests/run.py                                       |    5 +-
 tests/test_pstack_core.py                          | 1350 ++++++++++++++++++-
 tests/test_pstack_kitchen.py                       | 1414 +++++++++++++++++++-
 31 files changed, 4274 insertions(+), 340 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/.local/share/pstack-builds/6aaa6770ea89df2b/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/010-roster-handoff-verify setup
python3 /home/user/.local/share/pstack-builds/6aaa6770ea89df2b/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/010-roster-handoff-verify gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c # unmeasured
```

feature map: none listed
verification skill: 
