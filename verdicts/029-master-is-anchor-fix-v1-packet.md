# Verify 029: master-is-anchor-fix

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/029-master-is-anchor-fix-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/029-master-is-anchor-fix-verify
range: 7218f3112db91d4448b20d708b5c92117b6543db..7df08b7b4826868f574f0601610638a4f2ae7aaa
risk: routine
units: 027 029
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5
verifier-class: routine
verifier-outcome: running
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

Unit 029 fixes unit 027: prove every unit's Acceptance at this one head.

## Unit 027

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/027-master-is-anchor.md

## Goal

Under `PSTACK_MUX=amp`, the current master is the run's anchor: a top-level thread that holds the run's anchor title, with every role thread of the run (sidekick, consultant, verifier) nested under it. On a master rotation the anchor moves to the new master. The new master ends up top-level, takes the anchor title, and new role threads nest under it. The previous master and the role threads the run no longer uses are archived. Today the anchor is fixed at the first master, and a rotated master nests under the thread that started it. When that thread is archived, the live master disappears from the threads panel (Brian, ChenChess #1257; this run's g2 master had the same problem and was fixed by hand, see standing order 27).

## Acceptance

- After `init` under Amp threads, `pair.json` `.anchor` is `{thread: <master>, title: <its title>}`.
- After `rotate-master` then `take-master` in the new thread:
  - `.anchor.thread` is the new master and `.anchor.previous` holds the old one.
  - The fake recorded a rename of the new master to the anchor title.
  - `kitchen.sh nest` lists the new master as `top-level` with a `parent_thread=null` instruction, and the live sidekick under the new master.
  - No row nests the new master under the old master.
  - The old master is archived, as today.
- A test fails if take-master leaves `.anchor.thread` on the old master, and a test fails if the new master gets a row under the old anchor. Show each by a scratch revert of the line that makes the test pass.
- A take-master whose rename fails prints `warn:` and exits 0.
- Every existing nest and take-master test passes unchanged, apart from the anchor-shape assertion in the init test.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 027: feature + pstack-code

### feature.step-5

5. Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.

from workflow:feature, skills:poteto-mode/playbooks/feature.md step:5 763ff5742085

### feature.reply

The evidence standard for the brief's Acceptance, not new criteria: the built behavior shown on the matching surface, with the output. A verdict line cites the command output that shows it.

from workflow:feature, skills:poteto-mode/playbooks/feature.md reply a4759e2087fa

### testing

Check the unit's tests against this: deterministic, with no sleep and no elapsed-time assert; hermetic of PSTACK_KITCHEN_*, KITCHEN_*, PAIR_*, HERDR_*, REX_*, PSTACK_MUX, AMP_THREAD_ID and AMP_CURRENT_THREAD_ID; each source change in the same commit as the test that pins it; every tests/SPEC.md line naming its test. This is the evidence standard for the verdict, not new Acceptance.

from overlay:pstack-code, repo:AGENTS.md heading:Testing Guidelines 5be47ddb4e70

Brief commands:

Run each command in a fresh shell from the scratch root.

Command 1:

```bash
python3 tests/run.py -k nest
```

Command 2:

```bash
python3 tests/run.py -k anchor
```

Command 3:

```bash
python3 tests/run.py -k take_master
```

Command 4:

```bash
python3 tests/run.py -k rotate_master
```

Command 5:

```bash
python3 tests/run.py
```

Command 6:

```bash
python3 tests/spec_gen.py --check
```

Command 7:

```bash
python3 scripts/check-pstack.py
```

Command 8:

```bash
uv run --frozen ruff format --check
```

Command 9:

```bash
uv run --frozen ruff check
```

Command 10:

```bash
uv check --frozen
```

## Unit 029

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/029-master-is-anchor-fix.md

## Goal

Fix joo's findings on unit 027 (the joo run on origin/main..a0eb1b7, quick-correctness lane, all low). These go in before 027 ships as a PR.

## Acceptance

- Each of the three steps has a test that fails without its fix (show this with a scratch revert).
- Every nest, anchor, take_master and rotate_master test passes, and so does the full suite.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 029: review-fix

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
python3 tests/run.py -k nest
```

Command 2:

```bash
python3 tests/run.py -k anchor
```

Command 3:

```bash
python3 tests/run.py -k take_master
```

Command 4:

```bash
python3 tests/run.py
```

Command 5:

```bash
python3 tests/spec_gen.py --check
```

Command 6:

```bash
python3 scripts/check-pstack.py
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
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/027-master-is-anchor.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/029-master-is-anchor-fix.md: read it; do not write it

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
 skills/pstack-kitchen/SKILL.md                     |  28 +--
 skills/pstack-kitchen/references/master.md         |  10 +-
 .../pstack-kitchen/scripts/kitchen_core/master.py  |  45 +++--
 .../scripts/pstack_core/commands/init.py           |   6 +-
 .../scripts/pstack_core/commands/nest.py           | 113 ++++++++++---
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  19 ++-
 tests/SPEC.md                                      |  74 ++++----
 tests/pstack-pair/bin/amp                          |  12 +-
 tests/test_pstack_kitchen.py                       | 187 +++++++++++++++++++--
 9 files changed, 393 insertions(+), 101 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/029-master-is-anchor-fix-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/029-master-is-anchor-fix-verify gate pstack behavioral --role verifier --base 7218f3112db91d4448b20d708b5c92117b6543db # unmeasured
```

feature map: none listed
verification skill: 
