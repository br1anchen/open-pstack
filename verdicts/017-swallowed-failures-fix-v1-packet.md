# Verify 017: swallowed-failures-fix

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/017-swallowed-failures-fix-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/017-swallowed-failures-fix-verify
range: a90bb4130393b864176dbeded9bb216fcb5f255c..a9b9132645dcfbed28d4d68f7c936ec1e996a27b
risk: escalated
units: 001 017
verifier-agent: amp --model anthropic/claude-opus-5-5 --orb-size a1.large
verifier-class: escalated
verifier-outcome: clean
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

Unit 017 fixes unit 001: prove every unit's Acceptance at this one head.

## Unit 001

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/001-swallowed-failures.md

## Goal

Three places that swallow a failure report it instead: `gitstore.fetch_code` returns False when the trunk fetch fails, the master hand-over prints the unknown-freshness warning and keeps the original launch or spawn error when a rollback publish also fails, and `tests/run.py -k` exits non-zero when a test module fails to import.

## Acceptance

- With origin's trunk fetch failing and the code ref fetchable, `gitstore.fetch_code` returns False and `pair.sh sync --branch` prints `warning: origin or a published bundle could not be read`; with both fetchable it returns True as before.
- `rotate-master`, `take-master` and `rotate_if_due` each print `gitstore.UNKNOWN_FRESHNESS` on stderr when `refreshed` returns False, and carry on as before.
- When rotate-master's launch fails and its rollback publish fails too, the command's error names the launch failure and stderr has a `the rollback could not be published` warning; the same holds for a sidekick rotation whose spawn fails and whose publish fails.
- A successful rotation whose final publish fails still raises that publish error (unchanged).
- `python3 tests/run.py -k anything no_such_module_xyz` exits non-zero and reports the failed import; `python3 tests/run.py -k template_tells_a_role` still selects and passes the same tests as before.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 001: review-fix

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
python3 tests/run.py -k fetch_code
```

Command 2:

```bash
python3 tests/run.py -k rollback
```

Command 3:

```bash
! python3 tests/run.py -k anything no_such_module_xyz
```

Command 4:

```bash
python3 tests/run.py -k template_tells_a_role
```

## Unit 017

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/017-swallowed-failures-fix.md

## Goal

Fix the two joo findings on unit 001: a sidekick rotation whose spawn returns 3 or 4 keeps that exit code when the final publish fails, and `fetch_code` does not report a failed origin read when only a stale local `origin/HEAD` names a branch origin no longer has.

## Acceptance

- A rotation whose spawn returns 4 and whose final publish fails exits 4 and prints `the rotation could not be published`; one whose spawn returns 0 and publish fails still raises the publish error.
- With a stale local `origin/HEAD` and a readable origin whose HEAD names an existing branch, `fetch_code` returns True and sync prints no warning; with the trunk unfetchable from origin it returns False and sync prints the warning.
- Unit 001's Acceptance still holds at this head.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 017: review-fix

### review-fix.intro-1 (also unit 001)

### review-fix.step-2 (also unit 001)

### review-fix.reply (also unit 001)

Brief commands:

Run each command in a fresh shell from the scratch root.

Command 1:

```bash
python3 tests/run.py -k fetch_code
```

Command 2:

```bash
python3 tests/run.py -k rollback
```

Command 3:

```bash
python3 tests/run.py -k rotation
```

## Context: units between

These are decisions a review accepted; do not judge them again. Judge only the named units' Acceptance.

unit 002-amp-idle-and-stop: no accepted review

unit 003-packet-accepted-deviations: accepted (reviews/003-packet-accepted-deviations.md)

Amendments:

none

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/001-swallowed-failures.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/017-swallowed-failures-fix.md: read it; do not write it

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
 skills/pstack-kitchen/references/master-verify.md  |   6 +
 skills/pstack-kitchen/references/verifier.md       |   3 +
 .../pstack-kitchen/scripts/kitchen_core/master.py  |  11 +-
 .../scripts/kitchen_core/verify_packet.py          |  23 +-
 .../scripts/pstack_core/commands/relay.py          |  32 ++-
 .../scripts/pstack_core/commands/rotate.py         |  29 ++-
 .../scripts/pstack_core/commands/steer.py          |  24 +-
 .../scripts/pstack_core/commands/stop.py           |  25 +-
 skills/pstack-pair/scripts/pstack_core/gitstore.py |  39 ++-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  24 +-
 skills/pstack-pair/scripts/pstack_core/mux/base.py |   2 +
 skills/pstack-trio/scripts/variant.py              |  26 +-
 tests/SPEC.md                                      | 106 ++++----
 tests/run.py                                       |   5 +-
 tests/test_pstack_kitchen.py                       | 280 ++++++++++++++++++++-
 15 files changed, 517 insertions(+), 118 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/017-swallowed-failures-fix-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/017-swallowed-failures-fix-verify gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c # unmeasured
```

feature map: none listed
verification skill: 
