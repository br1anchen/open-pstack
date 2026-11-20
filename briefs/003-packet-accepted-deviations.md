# Brief 003: packet-accepted-deviations

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/003-packet-accepted-deviations.md

## Goal

A verify packet carries, for each unit it names (own and covered), the `## Amendments` of that unit's review note when one exists, so a verifier judges the Acceptance as the master amended it and does not reject on a deviation the master already accepted.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/verify_packet.py
- skills/pstack-kitchen/references/master-verify.md
- skills/pstack-kitchen/references/verifier.md
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/references/review-template.md
- tests/pstack-pair/golden/**

## Steps

1. **The packet shows a named unit's accepted amendments.** In `skills/pstack-kitchen/scripts/kitchen_core/verify_packet.py`, `unit_sections` (about L434-470) loops over `p.units` (own and covered units) and appends each unit's Goal and Acceptance. Right after a unit's Goal/Acceptance lines, when `reviews/<NNN>-<slug>.md` exists for that unit (same name as its brief) and its `## Amendments` section is not empty or `none`, append a block:

   ```
   Accepted by the master (reviews/<name>.md), whatever the Acceptance above says: prove the Acceptance as these amend it, and do not reject on a departure listed here.

   <the Amendments lines>
   ```

   Read the section with `section_lines(review, r'^## Amendments', False)` as `context_section` (L414-431) does; reuse its extraction rather than copying it (a small helper both call is fine). Put the rule text in a module constant beside `CONTEXT_RULE`. A unit with no review note, or Amendments `none`, gets nothing (no empty block). Tests in `tests/test_pstack_kitchen.py`, beside `test_a_covers_packet_lists_the_unit_between_with_its_accepted_exception_as_context` (about L8806) and using the `covers_packet` helper (about L8802): one table-driven test over (a) a `--covers` packet whose covered unit has a review note with an amendment: the packet's `## Unit <covered>` section holds the rule line and the amendment text; (b) a review note with Amendments `none`: no rule line; (c) a unit with no review note: no rule line. Commit, `kitchen.sh step`.
2. **Docs.** In `skills/pstack-kitchen/references/master-verify.md`, where a reject goes to `revise` and the fix is verified with `--covers` (and in the units-between paragraph about L99-107), add two sentences: a deviation the master accepts goes in the unit's review note under `## Amendments` before the fix or re-verification is sent, and the packet then gives it to the verifier as accepted. In `skills/pstack-kitchen/references/verifier.md`, one sentence where it says what to prove: an `Accepted by the master` block amends that unit's Acceptance; prove the amended line and do not reject on the listed departure. Run `python3 tests/spec_gen.py` if SPEC.md line citations move and commit the regenerated tests/SPEC.md in the same step. Commit, step.

## Context

- Start from 6ac18e8 (units 001 and 002), which this orb may not hold: `git fetch origin kitchen-code/kitchen-followups/master-g1` and move `main` to it (`git checkout main && git merge --ff-only FETCH_HEAD`; your checkout is at a90bb41 with no commits of its own). Report the head you started from.
- Gap 7 of run sidekick-ab2 (`notes/kitchen-gaps.md` in this store): verdicts 041-v1 and 045-v1 rejected solely on Acceptance lines whose premise the master's review had already corrected; the only paths were revise or an overrule.
- The review template's `## Amendments` holds objections the master adopted (`agreed` only); it is the record of an accepted change to a unit. Do not add a section to the template.

## Acceptance

- A `--covers` packet for a fix of unit P, where `reviews/P-*.md` has a non-empty Amendments section, shows in `## Unit P` the `Accepted by the master` rule line followed by the amendment text.
- A unit whose review note has Amendments `none`, or no review note, gets no such block; packets without review notes are byte-identical to before.
- master-verify.md and verifier.md each describe the block in one or two sentences, and `python3 scripts/check-pstack.py` passes.

## Verify

```bash
python3 tests/run.py -k packet
python3 tests/run.py -k covers
python3 tests/spec_gen.py --check
```

## Forbidden

- Do not include the report's `## Deviations` in the packet; only the master's review decides what is accepted.

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
