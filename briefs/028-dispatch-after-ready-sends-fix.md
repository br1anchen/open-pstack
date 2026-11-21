# Brief 028: dispatch-after-ready-sends-fix

playbook: review-fix
timebox: 30
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/028-dispatch-after-ready-sends-fix.md

## Goal

Fix every finding in the verifier's verdict /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/026-dispatch-after-ready-sends-fix-v1.md, so a fresh verification of the same acceptance passes.

## Scope

may write:
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **`test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event` under 15 s** (verdict 026-v1 finding 1). It runs six subtests in one process, each with a full `setUp()`, `finish_unit` and a dispatch, so it takes 17 to 24 s, as it already did before 023. Split it so `tests/run.py` runs each case as its own test process. Keep the one `cases` table, as module- or class-level data, and generate one test method per row from it (copy an existing generated-test pattern in tests/ if there is one; otherwise a small loop that `setattr`s methods on the class is fine). Every case keeps its exact assertions, and no case is dropped. Then regenerate tests/SPEC.md and run `python3 tests/run.py -k AmpKitchenTests`. Report the slowest five, and show that none is over 15 s.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/sidekick-g6 && git checkout -B kitchen-followups FETCH_HEAD` (026's head b6b68a4). Do not rebase onto origin/main. Unit 027 is on its own branch (`master-is-anchor`). Commit it or leave it untouched, and switch back to it afterwards.
- The verifier's own numbers: 17.5 s alone, 24.0 s in the 4-job class run (verdict Evidence 1 to 3).
- verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/026-dispatch-after-ready-sends-fix-v1.md
- original brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/026-dispatch-after-ready-sends-fix.md

## Acceptance

- `test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it` runs in under 10 s, and no AmpKitchenTests test takes over 15 s.
- The new step-2 case passes at the head and fails with `took_a_brief` forced to False.
- Unit 023's Acceptance still holds: one `dispatch --send-only` after READY archives, sends and records.

## Verify

```bash
python3 tests/run.py -k dispatch
python3 tests/run.py -k rotat
python3 tests/run.py -k AmpKitchenTests
python3 tests/spec_gen.py --check
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts. Bound the poll by export counts.
- Do not change `send_message`'s behaviour for a sidekick that is idle.

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
