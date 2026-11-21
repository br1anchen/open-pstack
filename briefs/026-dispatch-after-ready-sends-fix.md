# Brief 026: dispatch-after-ready-sends-fix

playbook: review-fix
timebox: 30
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/026-dispatch-after-ready-sends-fix.md

## Goal

Fix verdict 023-v1 (reject): unit 023's READY-turn wait in `send_message` makes existing tests sleep for real (one went from 2.5 s to 63.8 s). Also pin the branch that refuses at once, which no test covers (joo low).

## Scope

may write:
- tests/test_pstack_kitchen.py
- tests/SPEC.md
- skills/pstack-pair/scripts/pstack_core/send.py (only if a step below needs it)

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **No test inherits the 60 s READY wait** (verdict 023-v1 finding 1). Set `PAIR_ROTATE_READY_S='0'` in the AmpKitchenTests env defaults, so every test that dispatches to a working, never-briefed sidekick refuses at once. The unit 023 test that exercises the wait sets its own window and `PAIR_READY_POLL_S='0'`. Then run every AmpKitchenTests test and check that none takes over 15 s. List the slowest five in the report, with their times before and after.
2. **A working sidekick that already took a brief of its generation is refused without polling** (joo 023, tests:17054, low). Add one case: `.dispatch.generation == .sidekick.generation`, the thread `working`, and a large `PAIR_ROTATE_READY_S` with a fake that would turn idle after a few exports. The dispatch must exit 5 at once and print `is working`, and the fake must record no more exports than a single status read. The case has to fail if `took_a_brief` always returns False: show that by flipping it in a scratch edit and reverting.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/sidekick-g5 && git checkout -B kitchen-followups FETCH_HEAD`, which is unit 023's head 4bca786. Do not rebase onto origin/main.
- Verdict: `verdicts/023-dispatch-after-ready-sends-v1.md` (Findings 1, Evidence D and E). Unit 023's brief and report: `briefs/023-…`, `reports/023-…`.
- joo run on 023: `.amp/in/orb-backup/joo/023-run` in the master's orb. Finding quoted in step 2.

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
