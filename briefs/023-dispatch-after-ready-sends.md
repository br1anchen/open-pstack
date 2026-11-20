# Brief 023: dispatch-after-ready-sends

playbook: bug-fix
timebox: 75
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/023-dispatch-after-ready-sends.md

## Goal

The first `dispatch --send-only` after a replacement sidekick's READY sends and records the brief (or exits non-zero naming why), instead of archiving the old thread and returning with nothing sent.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- skills/pstack-pair/scripts/pstack_core/send.py
- skills/pstack-kitchen/scripts/kitchen_core/dispatch.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- tests/pstack-pair/golden/**

## Steps

1. **Reproduce first.** In AmpKitchenTests, modelled on `test_a_dispatch_after_a_rotation_is_sent_once_the_new_thread_is_ready`: unit done, dispatch rotates, the new thread's READY is synced, then `dispatch --send-only` of the next brief. Observed in a real run (`notes/s4-ledger-1252-issues.md` issue 4, build a90bb41): output was only `archived T-... (sidekick): a brief is being sent` and `archive: 1 archived, 0 kept, 1 in use`, no `sent:`, `.dispatch` unchanged; a second identical call sent it. Find the path: `rotate_if_due` (commands/rotate.py about L318) calls `archive_retired` (\"a brief is being sent\") and returns; then which check returns before the send without printing (the settled-READY path in `send.require_fresh_session` L87-104, the `--send-only` return in kitchen_core/dispatch.py, a swallowed exit code)? Note in the report the exit code of the first call. The test must fail at c5cf250. Commit with the test once it fails for the right reason, or with the fix in the same commit.
2. **Fix** at the place found, so one call archives, sends and records; if sending is impossible it exits non-zero with a line saying why. Commit, `kitchen.sh step`.

## Context

- `notes/s4-ledger-1252-issues.md` issue 4. This run cleared a stale `rotation_required` by hand twice before a dispatch after READY (decisions.tsv `incident`); unit 005 changes `spawn.record_started` to clear it. Start from 005's head (the master names it at dispatch) so the fix is not duplicated, and say whether 005 alone fixes this case.

## Acceptance

- After a rotation and its READY sync, one `dispatch --send-only` prints `sent: BRIEF ...` and pair.json `.dispatch.brief` names the new brief.
- A dispatch that cannot send exits non-zero with a reason.

## Verify

```bash
python3 tests/run.py -k dispatch
python3 tests/run.py -k rotat
```

## Forbidden

- none beyond the standing orders.

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
