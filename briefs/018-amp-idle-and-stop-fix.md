# Brief 018: amp-idle-and-stop-fix

playbook: review-fix
timebox: 60
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/018-amp-idle-and-stop-fix.md

## Goal

Fix the two joo findings on unit 002: a relay already sent into a thread's current turn is never relayed a second time just because the thread changed state within that turn, while a relay whose turn has ended still counts as not delivered; and a relayed STOP's event names its role.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/commands/stop.py
- skills/pstack-pair/scripts/pstack_core/mux/amp_routing.py
- skills/pstack-pair/scripts/pstack_core/commands/relay.py
- skills/pstack-kitchen/SKILL.md
- tests/pstack-pair/bin/amp
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- tests/pstack-pair/golden/**

## Steps

1. **Delivered means no turn ended since the send** (joo 002, medium, mux/amp.py about L1205-1210 `in_turn_at`, used by `relay` about L1249). `thread_state` returns `lastKnownAgentState` and its `updatedAt`, the time of the latest state change, not the start of the turn. Commit 7260854 made `delivered` require `since <= done_at`, so any state change in the same turn after the send (tool_use to a streaming state) makes the master relay the same steer or STOP twice into one turn. The case 7260854 meant to fix is real: a turn that ended after the send and a later turn that began, with the current state no longer showing the end. Decide `delivered` from turn boundaries, not from the state's update time: read from `amp threads export` (the real export and the fake in tests/pstack-pair/bin/amp) whether a turn ended after `done_at` (for example the time of the last assistant turn end, or the user messages after `done_at`; use a field the real export has, and say which in the report with an excerpt of a real export's shape, no content). Delivered = the thread is not settled and no turn ended after `done_at`. Extend the fake's export with that field only if the real one has it. Keep the two rows 7260854 pinned (same turn, later turn) and add the row joo describes: same turn, a state change after `done_at`, expected `delivered` and no new relay. If the real export has no turn boundary at all, fall back to `in_turn_since` (no settled state since `done_at`) and say so in the report; do not keep `in_turn_at`. Commit, `kitchen.sh step`.
2. **A relayed STOP names its role** (joo 002, low, commands/stop.py about L112). A relayed stop logs `send-stop - relay-pending`; log `send-stop - <role> relay-pending` (or the field order `stop_role` uses for the CLI path, with the role in the same column), so a trio or kitchen stop relayed to two roles writes two distinguishable rows. Assert it in unit 002's stop relay test. Commit, step.

3. **Audit gaps in 002's tests** (master audit read of 7fca271..6ac18e8). Extend `test_a_stop_to_a_working_thread_is_relayed_through_the_master_once` (tests/test_pstack_kitchen.py about L16857) with a second `stop` while the relay is pending: it prints `(already pending; not made again)` and adds no relay record or `send-stop` row. Add one row each for the stop relay fallback: relay returns `(None, '')` (checkout with no `.git`) and `too-large`; `stop` falls back to the queued CLI send (commands/stop.py about L103-112). Commit, step.
4. **Say what holds.** In skills/pstack-kitchen/SKILL.md "Amp threads": one sentence where `threads --archive` is described (about L386-410) that a sidekick whose unit is being verified (`.verifying`) is kept; one where steer relays are described (about L322) that `stop` to a working Amp role is relayed the same way and needs `relay-done`. Make the relay messages role-neutral where a STOP can be the relayed text: mux/amp.py about L533 `a steer relay to it is still pending`, mux/amp_routing.py about L348 and commands/relay.py about L78 `the steer was relayed again` (say `relay`, not `steer`); update any test that asserts the old text. Regenerate tests/SPEC.md if line citations move. Commit, step.

## Context

- Unit 002: `briefs/002-amp-idle-and-stop.md`, `reports/002-amp-idle-and-stop.md` (head 6ac18e8).
- joo run on 7fca271..6ac18e8: `.amp/in/orb-backup/joo/002-run/executions` in the master's orb; both findings are quoted above.

## Acceptance

- A relay that was sent (relay-done) is answered `delivered` while the thread stays in that turn, including after a state change within it; after the thread ended that turn (settled, or a later turn began), the same text is relayed anew.
- A relayed STOP writes an events.tsv row naming its role.
- A second `stop` while a stop relay is pending makes no new record or row; a stop whose relay cannot be made falls back to the queued send.
- SKILL.md states the `.verifying` keep and the stop relay; relay messages no longer say `steer` for a relayed STOP.
- Unit 002's Acceptance still holds at this head.

## Verify

```bash
python3 tests/run.py -k relay
python3 tests/run.py -k stop
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
```

## Forbidden

- Do not regenerate goldens.

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
