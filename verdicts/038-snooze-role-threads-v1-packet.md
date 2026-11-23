# Verify 038: snooze-role-threads

verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/038-snooze-role-threads-v1.md
template: /home/user/workspace/repo/skills/pstack-kitchen/references/verdict-template.md
scratch: /home/user/.local/state/pstack/scratch/runs-kitchen-followups/038-snooze-role-threads-verify
range: daf51dc09eeffc59c136c169e371c8641fcf8a99..bee0dc78bf5e357ee0c008b70ba54e1c35ad6a1b
risk: routine
units: 038
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5
verifier-class: routine
verifier-outcome: running
deadline: 45m from now

Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.

## Unit 038

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/038-snooze-role-threads.md

## Goal

Under Amp threads the master snoozes a role thread (sidekick, consultant, verifier, helper) only once it has no open work and its turn is settled, and unsnoozes it before the kitchen sends it new work, so Brian sees active role threads and master threads and nothing else; master threads and threads blocked on a question or an error are never snoozed, and `PSTACK_SNOOZE_ROLES=0` turns the snoozing off (on by default).

## Acceptance

- `kitchen.sh snooze <store>` lists exactly the role threads that are idle with no open work (sidekick reviewed, consultant answered, verifier not verifying, nothing pending to it), and never a master, a working thread or a thread in `error`; `PSTACK_SNOOZE_ROLES=0` lists none.
- `--done` and `--wake` record the state and log `snooze`/`wake` events; an unknown thread is refused.
- A dispatch (and a discuss, consult or steer) to a thread recorded snoozed refuses before recording anything and names `update_thread(... snooze="off")`; after `--wake` it sends.
- `kitchen.sh status` prints `snooze-due:` and `needs-human:` lines as step 3 says.
- `amp.assess` behaves as before (the existing threads/archive tests pass unchanged).
- SKILL.md and master.md describe the snooze, the wake, the off switch and the relay duty; check-pstack and spec_gen --check pass.


## Playbook (what to prove)

The evidence standard for the Acceptance above; it adds no criteria. Answer Acceptance line by line, citing evidence of the kind these parts name.

unit 038: feature + pstack-code

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
python3 tests/run.py -k snooze
```

Command 2:

```bash
python3 tests/run.py -k dispatch
```

Command 3:

```bash
python3 tests/run.py -k status
```

Command 4:

```bash
python3 tests/run.py -k archive
```

Command 5:

```bash
python3 tests/run.py
```

Command 6:

```bash
python3 scripts/check-pstack.py
```

Command 7:

```bash
python3 tests/spec_gen.py --check
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

## Inputs (read-only)

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md: read it; do not write it
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/038-snooze-role-threads.md: read it; do not write it

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
 skills/pstack-kitchen/SKILL.md                     |  20 +++
 skills/pstack-kitchen/references/master.md         |  13 +-
 skills/pstack-pair/scripts/pstack_core/cli.py      |   2 +
 .../scripts/pstack_core/commands/snooze.py         | 171 ++++++++++++++++++
 .../scripts/pstack_core/commands/status.py         |   3 +-
 .../scripts/pstack_core/commands/steer.py          |   3 +-
 skills/pstack-pair/scripts/pstack_core/consult.py  |   3 +-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  |  50 ++++--
 skills/pstack-pair/scripts/pstack_core/send.py     |   5 +-
 tests/SPEC.md                                      |  87 +++++-----
 tests/test_pstack_kitchen.py                       | 193 +++++++++++++++++++++
 11 files changed, 491 insertions(+), 59 deletions(-)
```

## Prove

You share this machine with the sidekick. Run every repo command with
`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo's scripts give you
your own ports, emulators, and data, and stop everything you start before
you end. Run these in the scratch worktree, then drive what they cannot reach:

```bash
export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/038-snooze-role-threads-verify setup
python3 /home/user/workspace/repo/skills/pstack-kitchen/scripts/kitchen.py --repo /home/user/.local/state/pstack/scratch/runs-kitchen-followups/038-snooze-role-threads-verify gate pstack behavioral --role verifier --base daf51dc09eeffc59c136c169e371c8641fcf8a99 # unmeasured
```

feature map: none listed
verification skill: 
