# Brief 025: reply-deletes-no-master-state

playbook: review-fix
timebox: 90
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/021-kept-sidekick-session-r2.md
risk: escalated
profiles: pstack, pstack-docs
verify: unit by escalated verifier
review-engine: joo
steps: gates
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/025-reply-deletes-no-master-state.md

## Goal

On a role-to-master local-store sync, a role's reply changes or deletes only the paths its own role owns: every other existing file (master-owned, another role's report or step log, the master's `.seen` markers in `progress/`) keeps the master's copy and is never deleted by omission; and unit 005's rotation diagnostics read the context once and cover a manual `rotate`.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/mux/base.py
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/pstack-pair/golden/**

## Steps

1. **A reply changes only what its sender owns** (review 005 blocking 1; chenchess issue 3). In `bundle.merge_store` under `protect`, resolve the sender's role from the receiver's pre-merge state (`from_name` against pair.json `.sidekick.name`, the consultant's and verifier's names). For a path `gitstore.classify` gives to that role: merge as today. For every other path: a file absent locally is added; an existing local file keeps the master's bytes (no update, no rollback); a file absent from the reply is never deleted. The shared logs (`events.tsv`, `gates.tsv`) keep their union. An unknown sender owns nothing (add-only). List any role-side deletion of store paths you find (grep) in the report. Commit with its tests, `kitchen.sh step`.
2. **Tests** (review 005 blocking 2). Extend the two-orb regression (tests/test_pstack_kitchen.py about L16255-16268) to assert `reports/002-unit002.md` and `steps/002-unit002.tsv` survive the stale verifier reply byte for byte. `BundleMergeTests` rows: a sidekick reply built before them leaves the master's `progress/025-x.md.seen` and a new `steers/025-x-s2.md` in place while its own new progress lines arrive; a verifier's stale copy of a sidekick report does not roll it back; a sidekick's own report update merges; an unknown sender's new file is added and its omission deletes nothing. Commit, step.
3. **One context observation, and a manual rotate says why** (review 005 follow-ups). Read `amp threads context` once per decision: one mux call returning (used, compacted) shared by `session_decision`, keeping `context_used`'s return for its callers. `kitchen.sh rotate` records `reason:manual` in its event and prints `rotate: manual`. Drop `keeps_session`'s unused `reasons=None` branch. Commit, step.
4. **Docs.** SKILL.md L290: say a reply changes only its own role's files and adds new ones, logs union; L437: every rotation, manual included, says why. Regenerate tests/SPEC.md. Commit, step.

## Context

- Start from unit 005's head b1ca49f: `git fetch origin kitchen-code/kitchen-followups/sidekick-g4 && git checkout -B kitchen-followups FETCH_HEAD` (or on top of 023 if it has landed on the run's code ref; the master names it at dispatch). Do not rebase onto origin/main.
- Review reviews/005-kept-sidekick-session.md; joo run in the master's orb `.amp/in/orb-backup/joo/005-run`.
- `notes/s4-ledger-1252-issues.md` issue 3.

## Acceptance

- A stale role reply neither deletes nor rolls back another role's report or step log, a master steer, or a `.seen` marker; the replying role's own files and new files arrive.
- The two-orb regression asserts reports/002 and steps/002 survive.
- A routine Amp dispatch runs `amp threads context` once; a manual `rotate` event carries `reason:manual`.
- Unit 005's Acceptance still holds at this head; goldens unchanged.

## Verify

```bash
python3 tests/run.py -k BundleMerge
python3 tests/run.py -k keeps_the_sidekick
python3 tests/run.py -k rotat
python3 tests/spec_gen.py --check
```

## Forbidden

- No change to RULES or the bundle wire format.

## Playbook: review-fix (your parts)

You own the working tree.

Your parts:
- review-fix.intro-1: **Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdi
- review-fix.step-1: 1. Read each finding, the code it names, and the parent unit's brief and report. A finding you canno
- review-fix.step-2: Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the 
- review-fix.step-3: 3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks 
- review-fix.reply: In the report, under Ran and the sections the template names: each finding with its commit, the test

### review-fix.intro-1

**Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdict or a landing review already found each one; the unit that raised them already settled the design.

### review-fix.step-1

1. Read each finding, the code it names, and the parent unit's brief and report. A finding you cannot place, or one whose fix would change the parent's design, goes in the report under Questions; do not redesign around it.

### review-fix.step-2

Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the test that pins it, in the same commit, recorded with kitchen.sh step. A finding with no testable behaviour (a comment, a name, a log line) says so in its commit message.

### review-fix.step-3

3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks for a failing test committed before its fix.

### review-fix.reply

In the report, under Ran and the sections the template names: each finding with its commit, the test that pins it, and the command output that shows it fixed. A finding left open says why. Every claim carries its command output or a file pointer.

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
