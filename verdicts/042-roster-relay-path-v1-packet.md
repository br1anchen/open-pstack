# Verify 042: roster-relay-path

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/042-roster-relay-path-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/042-roster-relay-path-verify
range: be88296826fbc6e1c7ab0ebf8a1dc26467c573cf..e9dc6fe01a2c069c49f9e30fc59c5c036061158d
risk: escalated
units: 042
verifier-agent: claude --model claude-opus-5-5
verifier-class: escalated
verifier-outcome: running
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

## Unit 042

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/042-roster-relay-path.md

## Goal

The roster relay that unit 039 prints under the git store names a repo-relative upload destination, as `steer`'s relay does, and take-master on the next master finds the roster there whatever the store's absolute path on that orb is; the snapshot's `.gitignore` line matches exactly the paths the LOCAL rule classifies.

## Acceptance

- Under the git store rotate-master's relay line names `destination=.git/pstack-roster-g<n>.toml`.
- take-master under the git store installs a roster found only at `.git/pstack-roster-g<n>.toml`, with the store at a different absolute path than on the old orb, and removes the `.git` file; with neither present it prints `did not arrive` and exits 0.
- The `.gitignore` line matches exactly the LOCAL rule's paths.
- A committed test asserts the git-store relay line (the verdict 039-v1 note: none did), and the docs and that line state the bounded take-over wait.
- 010's and 039's tests pass; check-pstack and spec_gen --check pass.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 042: review-fix

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

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/042-roster-relay-path.md: read it; do not write it

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
 skills/pstack-kitchen/SKILL.md                     |   59 +-
 skills/pstack-kitchen/references/master-verify.md  |   11 +-
 skills/pstack-kitchen/references/master.md         |   11 +
 skills/pstack-kitchen/references/verifier.md       |    3 +
 .../pstack-kitchen/scripts/kitchen_core/landing.py |  100 +-
 .../pstack-kitchen/scripts/kitchen_core/master.py  |  227 ++-
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
 tests/test_pstack_core.py                          | 1359 +++++++++++++++-
 tests/test_pstack_kitchen.py                       | 1625 +++++++++++++++++++-
 32 files changed, 4615 insertions(+), 347 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/042-roster-relay-path-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/042-roster-relay-path-verify gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c # unmeasured
```

feature map: none listed
verification skill: 
