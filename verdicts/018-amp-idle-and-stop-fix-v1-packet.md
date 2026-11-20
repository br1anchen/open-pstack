# Verify 018: amp-idle-and-stop-fix

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/018-amp-idle-and-stop-fix-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/018-amp-idle-and-stop-fix-verify
range: 7fca27105380693abb8741e0af8347d63c413f10..7616b0e559437724fd528d85e35da3cd5610c36c
risk: routine
units: 002 018
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small
verifier-class: routine
verifier-outcome: clean
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

Unit 018 fixes unit 002: prove every unit's Acceptance at this one head.

## Unit 002

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/002-amp-idle-and-stop.md

## Goal

Under Amp threads, `threads --archive` keeps a sidekick whose unit is still being verified, and `kitchen.sh stop` neither starts a fresh thread for a role whose thread was archived nor queues STOP behind a working role's turn: it relays STOP the way `steer` relays a steer.

## Acceptance

- `threads <store> --archive` keeps a sidekick whose unit report is done while `pair.json` `.verifying` exists, and archives it once `.verifying` is cleared (the existing done-and-idle row still archives).
- After an idle consultant was archived, `kitchen.sh stop <store>` prints `stop: kitchen-...-consultant has no thread; nothing to stop` (or the role's name) and starts no thread.
- `kitchen.sh stop` to a sidekick whose Amp thread is in a turn prints `relay-pending: <id> for <name>` and the three `relay:` lines and queues nothing; to an idle sidekick it sends STOP by the CLI as before.
- Herdr and Rex stop behaviour and the trio goldens are unchanged.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 002: review-fix

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
python3 tests/run.py -k threads_archive
```

Command 2:

```bash
python3 tests/run.py -k stop
```

Command 3:

```bash
python3 tests/run.py -k relay
```

## Unit 018

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/018-amp-idle-and-stop-fix.md

## Goal

Fix the two joo findings on unit 002: a relay already sent into a thread's current turn is never relayed a second time just because the thread changed state within that turn, while a relay whose turn has ended still counts as not delivered; and a relayed STOP's event names its role.

## Acceptance

- A relay that was sent (relay-done) is answered `delivered` while the thread stays in that turn, including after a state change within it; after the thread ended that turn (settled, or a later turn began), the same text is relayed anew.
- A relayed STOP writes an events.tsv row naming its role.
- A second `stop` while a stop relay is pending makes no new record or row; a stop whose relay cannot be made falls back to the queued send.
- SKILL.md states the `.verifying` keep and the stop relay; relay messages no longer say `steer` for a relayed STOP.
- Unit 002's Acceptance still holds at this head.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 018: review-fix

### review-fix.intro-1 (also unit 002)

### review-fix.step-2 (also unit 002)

### review-fix.reply (also unit 002)

Brief commands:

Run each command in a fresh shell from the scratch root.

Command 1:

```bash
python3 tests/run.py -k relay
```

Command 2:

```bash
python3 tests/run.py -k stop
```

Command 3:

```bash
python3 scripts/check-pstack.py
```

Command 4:

```bash
python3 tests/spec_gen.py --check
```

## Context: units between

These are decisions a review accepted; do not judge them again. Judge only the named units' Acceptance.

unit 003-packet-accepted-deviations: accepted (reviews/003-packet-accepted-deviations.md)

Amendments:

none

unit 017-swallowed-failures-fix: accepted (reviews/017-swallowed-failures-fix.md)

Amendments:

none

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/002-amp-idle-and-stop.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/018-amp-idle-and-stop-fix.md: read it; do not write it

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

## Changed

```
 skills/pstack-kitchen/SKILL.md                     |   3 +-
 skills/pstack-kitchen/references/master-verify.md  |   6 +
 skills/pstack-kitchen/references/verifier.md       |   3 +
 .../scripts/kitchen_core/verify_packet.py          |  23 +-
 .../scripts/pstack_core/commands/relay.py          |  34 ++-
 .../scripts/pstack_core/commands/rotate.py         |  17 +-
 .../scripts/pstack_core/commands/steer.py          |  24 +-
 .../scripts/pstack_core/commands/stop.py           |  27 ++-
 skills/pstack-pair/scripts/pstack_core/gitstore.py |  29 ++-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  45 +++-
 .../scripts/pstack_core/mux/amp_routing.py         |   2 +-
 skills/pstack-pair/scripts/pstack_core/mux/base.py |   2 +
 skills/pstack-trio/scripts/variant.py              |  26 ++-
 tests/SPEC.md                                      | 100 ++++-----
 tests/pstack-pair/bin/amp                          |   8 +-
 tests/test_pstack_kitchen.py                       | 246 ++++++++++++++++++++-
 16 files changed, 476 insertions(+), 119 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/018-amp-idle-and-stop-fix-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/018-amp-idle-and-stop-fix-verify gate pstack behavioral --role verifier --base 7fca27105380693abb8741e0af8347d63c413f10 # unmeasured
```

feature map: none listed
verification skill: 
