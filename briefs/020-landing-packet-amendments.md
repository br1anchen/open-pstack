# Brief 020: landing-packet-amendments

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/020-landing-packet-amendments.md

## Goal

A landing verify packet gives each unit it folds in the accepted amendments from that unit's review note, as unit and `--covers` packets do since unit 003.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/verify_packet.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**

## Steps

1. In `skills/pstack-kitchen/scripts/kitchen_core/verify_packet.py`, `landing_sections` (about L354-395) prints each folded unit's `## Acceptance` (about L384-389) but never calls `accepted_amendments(store, brief)` (added by unit 003, about L406). Append its block after each folded unit's Acceptance, as `unit_sections` does. Extend unit 003's table test (tests/test_pstack_kitchen.py, the test added in e57fe39) with a landing-packet row: a folded unit whose review has an amendment shows the `Accepted by the master` line and the text; a folded unit with Amendments `none` shows nothing. Commit, `kitchen.sh step`.

## Context

- Unit 003: `briefs/003-packet-accepted-deviations.md`, review `reviews/003-packet-accepted-deviations.md` (joo low, verify_packet.py:468).

## Acceptance

- A `verify --landing` packet that folds in a unit whose review note has a non-empty Amendments section shows that unit's `Accepted by the master` block; packets for units without amendments are unchanged.
- Unit 003's Acceptance still holds at this head.

## Verify

```bash
python3 tests/run.py -k packet
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
