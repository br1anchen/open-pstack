# Verify landing: the stack at 29cc53c2e

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/landing-v5.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/000-landing-verify
range: 3b9514449d2a1f11c47e7388b76ae83876fb33aa..29cc53c2e8a65114086f9ec00042f5dd64d4b469
risk: landing
units: landing
verifier-agent: claude --model claude-opus-5-5
verifier-class: landing
verifier-outcome: running
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

## Stack

Verified on their own: none.
Folded into this landing, with no verdict of their own: 050-landing-rebase-5; their Acceptance is below, and you prove it here.

Prove that together, at this tip, they still hold: run every Prove command, then drive the main flows the
briefs describe, and try the seams between units. Read a brief only for
the flow you are driving.

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/050-landing-rebase-5.md

## Goals

### Unit 050 landing-rebase-5

Redo landing 048 on today's main: one branch `kitchen-followups-landing-5` on top of current origin/main (3b951444 or later) holding the 8 commits of `699837d8..fa6cd2a6` (units 040 and 046, as landing-v4 verified them), with the full suite green, for Brian's approved push. Main gained 13 commits by Brian after 699837d8 (dispatch warnings, landing_base, check-in digest, master.md rewraps), so the push of fa6cd2a6 was refused as not a fast-forward.

## Acceptance: unit 050-landing-rebase-5 (plan none)

- `git log origin/main..HEAD` holds the 8 commits by subject plus any step 2 fixes, none twice.
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head.

## Playbook (what to prove)

[landing].mode is commit: the kitchen never merges; prove only what this mode allows.

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 050: bug-fix + pstack-code

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

### shipping.step-1

Prove the unit the way a user would on the matching surface, parent against head, with the repo's verification skill, and write `PASS`, `PASS+NOTES` or `FAIL` as the verdict. You did not write the code, which is why your verdict counts. Green CI and an approving bot review are not verdicts.

from workflow:shipping, skills:poteto-mode/playbooks/shipping.md step:1 761770e00861

### shipping.reply

The evidence standard for the brief's Acceptance, not new criteria: the verdict for each unit, with who produced it and the evidence. A verdict line cites the command output that shows it.

from workflow:shipping, skills:poteto-mode/playbooks/shipping.md reply af588d2d5535

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/050-landing-rebase-5.md: read it; do not write it

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
 skills/pstack-kitchen/SKILL.md                     |  44 +++-
 skills/pstack-kitchen/references/master.md         |  33 ++-
 .../scripts/kitchen_core/dispatch.py               |  74 +++++-
 .../pstack-kitchen/scripts/kitchen_core/master.py  |  21 +-
 skills/pstack-kitchen/scripts/variant.py           |   4 +
 .../scripts/pstack_core/commands/nest.py           | 101 +++++---
 .../scripts/pstack_core/commands/snooze.py         |  14 +-
 .../scripts/pstack_core/commands/stop.py           |  16 +-
 skills/pstack-pair/scripts/pstack_core/errors.py   |   5 +
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  37 ++-
 tests/SPEC.md                                      | 169 +++++++-------
 tests/test_pstack_core.py                          |  32 ++-
 tests/test_pstack_kitchen.py                       | 254 ++++++++++++++++++++-
 tests/test_pstack_pair.py                          |   3 +
 14 files changed, 645 insertions(+), 162 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/000-landing-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/000-landing-verify gate pstack behavioral --role verifier --base 3b9514449d2a1f11c47e7388b76ae83876fb33aa # unmeasured
```

feature map: none listed
verification skill: 
