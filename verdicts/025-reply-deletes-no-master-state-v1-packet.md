# Verify 025: reply-deletes-no-master-state

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/025-reply-deletes-no-master-state-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/025-reply-deletes-no-master-state-verify
range: c5cf25045919b5390bebab589b40d39e79a036c0..6a83f500bc5d5ed07276c49035859a9b8322e879
risk: escalated
units: 005 025
verifier-agent: claude --model claude-opus-5-5
verifier-class: escalated
verifier-outcome: running
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

Unit 025 fixes unit 005: prove every unit's Acceptance at this one head.

## Unit 005

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/005-kept-sidekick-session.md

## Goal

Implement plan 021 with the amendments A1-A5 of reviews/021-kept-sidekick-session-r2.md: a role's reply on a local-store sync never rolls back or deletes the master's pair.json or master-owned files (only a validated current sidekick completion and the shared append logs come through), a new sidekick generation starts with `rotation_required` cleared, and every rotation under a session-reusing mux prints and logs why.

## Acceptance

- The two-orb newer-outbound/older-reply regression fails at c5cf250 and passes at the head: the master keeps `.dispatch`, `.sidekick` generation, `.consult`, a new master key, standing-orders.md and a new brief; the old verdict is delivered; the routine docs unit keeps the sidekick.
- A role reply never deletes or changes `decisions.tsv` or a `sessions/` file; it adds its rows to `events.tsv` and `gates.tsv`, and an absent shared log deletes nothing.
- `rotation_required` from a reply is applied only as `true` from the current sidekick (A1) with present, matching generation, brief and dispatch generation (A5).
- A real rotate-master bundle (generation-specific recipient name) syncs as master-to-master, at first sync and with its pending hand-over present.
- After `kitchen.sh rotate` and READY, the next `dispatch` is sent; the same after a failover.
- Under Amp a rotation prints `rotate: <reason>` and the `rotate` event detail carries it; Herdr prints none and the goldens are unchanged.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 005: bug-fix + pstack-code

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
python3 tests/run.py -k BundleMerge
```

Command 2:

```bash
python3 tests/run.py -k keeps_the_sidekick
```

Command 3:

```bash
python3 tests/run.py -k rotat
```

Command 4:

```bash
python3 tests/run.py -k take_master
```

Command 5:

```bash
python3 tests/spec_gen.py --check
```

## Unit 025

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/025-reply-deletes-no-master-state.md

## Goal

On a role-to-master local-store sync, a role's reply changes or deletes only the paths its own role owns: every other existing file (master-owned, another role's report or step log, the master's `.seen` markers in `progress/`) keeps the master's copy and is never deleted by omission; and unit 005's rotation diagnostics read the context once and cover a manual `rotate`.

## Acceptance

- A stale role reply neither deletes nor rolls back another role's report or step log, a master steer, or a `.seen` marker; the replying role's own files and new files arrive.
- The two-orb regression asserts reports/002 and steps/002 survive.
- A routine Amp dispatch runs `amp threads context` once; a manual `rotate` event carries `reason:manual`.
- Unit 005's Acceptance still holds at this head; goldens unchanged.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 025: review-fix

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
python3 tests/run.py -k BundleMerge
```

Command 2:

```bash
python3 tests/run.py -k keeps_the_sidekick
```

Command 3:

```bash
python3 tests/run.py -k rotat
```

Command 4:

```bash
python3 tests/spec_gen.py --check
```

## Context: units between

These are decisions a review accepted; do not judge them again. Judge only the named units' Acceptance.

unit 023-dispatch-after-ready-sends: accepted (reviews/023-dispatch-after-ready-sends.md)

Amendments:

none

unit 026-dispatch-after-ready-sends-fix: accepted (reviews/026-dispatch-after-ready-sends-fix.md)

Amendments:

none

unit 028-dispatch-after-ready-sends-fix: accepted (reviews/028-dispatch-after-ready-sends-fix.md)

Amendments:

none

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/021-kept-sidekick-session-r2.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/005-kept-sidekick-session.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/025-reply-deletes-no-master-state.md: read it; do not write it

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

## Changed

```
 skills/pstack-kitchen/SKILL.md                     |  28 +-
 skills/pstack-pair/scripts/pstack_core/bundle.py   | 194 +++++++-
 .../scripts/pstack_core/commands/rotate.py         |  89 +++-
 .../scripts/pstack_core/commands/sync.py           |   8 +-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  18 +-
 skills/pstack-pair/scripts/pstack_core/mux/base.py |   5 +
 skills/pstack-pair/scripts/pstack_core/send.py     |  20 +
 skills/pstack-pair/scripts/pstack_core/spawn.py    |   4 +
 tests/SPEC.md                                      | 138 +++---
 tests/test_pstack_core.py                          | 509 +++++++++++++++++++++
 tests/test_pstack_kitchen.py                       | 362 ++++++++++++++-
 11 files changed, 1260 insertions(+), 115 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/025-reply-deletes-no-master-state-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/025-reply-deletes-no-master-state-verify gate pstack behavioral --role verifier --base c5cf25045919b5390bebab589b40d39e79a036c0 # unmeasured
```

feature map: none listed
verification skill: 
