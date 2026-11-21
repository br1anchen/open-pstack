# Brief 024: verify-names-excluded-fix

playbook: bug-fix
timebox: 45
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/024-verify-names-excluded-fix.md

## Goal

`verify <store> NNN --covers PPP` says which unit in its range it leaves out and why, and that a landing verification will cover it, instead of listing it only as `no accepted review` in the packet's context.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/verify_packet.py
- skills/pstack-kitchen/scripts/kitchen_core/verify_launch.py
- skills/pstack-kitchen/references/master-verify.md
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- tests/pstack-pair/golden/**

## Steps

1. In `verify_packet.py` `context_section` / `units_between` (about L397-445), a unit between whose review is not `accept` is today written into the packet as `unit NNN: no accepted review` and its Acceptance is not proved. Keep that behaviour (the verifier does not judge an unreviewed unit) and make `verify` print, once per such unit, `excluded: unit NNN (in the range, no accepted review); name it with --covers, or verify --landing covers it` on stdout (verify_launch.py, where the packet is written). Add the same note to the packet line. Test beside `test_a_covers_packet_says_a_unit_between_has_no_accepted_review`: the line is printed and the packet note names the remedy. Commit, `kitchen.sh step`.
2. master-verify.md, in the units-between paragraph: one sentence on the excluded line. Commit, step.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-025 && git checkout -B kitchen-followups FETCH_HEAD` (unit 025's head 6a83f50, on top of the run's stack). Do not rebase onto origin/main, and leave the `master-is-anchor` branch (PR #13) alone.
- `notes/s4-ledger-1252-issues.md` issue 5: fix unit 026 inside 025..027 was left out silently and waited for `verify --landing`.

## Acceptance

- A covers verification whose range holds a unit with no accepted review prints `excluded: unit NNN ...` naming both remedies; a range without one prints no such line.

## Verify

```bash
python3 tests/run.py -k covers
python3 scripts/check-pstack.py
```

## Forbidden

- Do not start including unreviewed units' Acceptance in the packet.

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
