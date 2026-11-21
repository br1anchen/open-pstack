# Brief 006: land-check-gone-head

playbook: bug-fix
timebox: 120
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/006-land-check-gone-head.md

## Goal

Under the local store, the commits a unit's step, report and verification name stay reachable and travel between orbs, so land-check never loses a unit to a rebase or a new orb; and when such evidence is missing anyway, land-check says which head is missing and prints a `verify --covers` recovery command that works, instead of "has no clean verdict".

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/step.py
- skills/pstack-kitchen/scripts/kitchen_core/verify_launch.py
- skills/pstack-kitchen/scripts/kitchen_core/landing.py
- skills/pstack-kitchen/scripts/kitchen_core/units.py
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/git.py
- skills/pstack-pair/scripts/pstack_core/commands/report.py
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- skills/**/references/**
- skills/**/SKILL.md

## Steps

The design is the consultant's advice, `advice/006-land-check-gone-head-c1.md` in the store, sections "Keep the original evidence, not only fingerprints" and "Recover an old run with fresh proof". Read them first; they win on any detail they settle. Units 004 (bundle branches) and 008 (merge ownership) also change `bundle.py`; this unit starts from their head, so read what they changed first.

1. **Pin evidence.** Add one helper (in `pstack_core/git.py`, as `pin(root, run, oid)`) that creates the immutable ref `refs/pstack/keep/<run-slug>/<full-oid>` with `git update-ref <ref> <oid> ''` (create-only; an existing ref with the same OID is a no-op). Call it: in `kitchen.sh step` for the step's full commit before the step is recorded (`kitchen_core/step.py`); at `finish` for the resolved report head (`commands/report.py` or the kitchen's finish path, whichever resolves `head:`); and in `verify_launch.py` before the verifier starts, for the packet's base and head and for every unit head and base the packet was built from. Test: after step, finish and verify, the refs exist and survive `git reflog expire --expire=now --all && git gc --prune=now` after the branch is rewritten. Commit, `kitchen.sh step`.
2. **Carry evidence.** In `bundle.pack`, add every `refs/pstack/keep/*` tip to the git bundle on every send (not only master hand-overs); in `apply_git`, import them into the same `refs/pstack/keep/...` names on the receiver (create-only; same OID is a no-op; never delete). Cut against a baseline the receiver fetches, as unit 004 does for hand-over branches (reuse its code), never `--not --remotes`. A failed required import is reported and the sync exits non-zero. Test: a fresh receiver, with the old objects absent and after receiver GC, holds the evidence of two successive bundles from the same sender. Commit, step.
3. **Say what is missing, and how to recover.** In `landing.py` `land_check` (about L516-575): when a unit has no clean unit verdict and its report or step head, its dispatch base, or a landing verdict's range head that names it does not resolve (`unit_head` returns '' at units.py L154-165), print `unverified: unit NNN: its evidence is missing (<which>: <sha>); restore it from the bundle that carried it, or prove its Acceptance again with kitchen.sh verify <store> <current-unit> --covers NNN --base <live-base>` instead of `has no clean verdict`. Keep the exit code (2). Keep `has no clean verdict` for a unit whose heads resolve. Do not count patch-id membership on trunk as coverage, and leave `landed_as` an explanation only. Test: one table-driven test over (unit head gone, landing head gone, base gone, heads present and unverified); plus one test that the printed `--covers` command, run with a fake verifier and a clean verdict, clears the unit. Commit, step.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-031 && git checkout -B kitchen-followups FETCH_HEAD` (unit 031's head, which the master pushes; on top of 030, 004 and 025). Do not rebase onto origin/main, and leave the `master-is-anchor` branch (PR #13) alone.
- Gap 5 of run sidekick-ab2: `notes/kitchen-gaps.md` in the store.
- Consultant probe: `advice/006-land-check-gone-head-c1-probe.py` (its RETAINED REF, TRANSPORT GAP, EXPLICIT TRANSPORT and RECOVERY PATH cases model the tests).
- Closest tests: tests/test_pstack_kitchen.py L12427-12645 and L12953-13040 (rebased coverage, revert checks, landed diagnostics); keep them unchanged.
- Out of scope, by the master's decision: the git store's evidence durability (gitstore.py code refs are mutable) and a final-tip landing invariant (land-check passing only when the exact candidate was verified). Both are follow-ups.

## Acceptance

- After a unit's steps, finish and verification, then a rewrite of its branch and `git gc --prune=now`, land-check covers it as before.
- A fresh receiver orb with none of the old objects holds the evidence refs after sync, and they survive its GC; two successive bundles from one sender both arrive.
- With a unit's evidence missing, land-check exits 2 with the `its evidence is missing (...)` line naming the missing head and the `--covers` recovery command; following it with a clean verdict clears the unit.
- The existing rebased, revert and landed tests pass unchanged.

## Verify

```bash
python3 tests/run.py -k land_check
python3 tests/run.py -k bundle
python3 tests/run.py -k evidence
```

## Forbidden

- No patch-id cache and no new step-log columns.
- No coverage from patch-id membership on trunk.
- pstack_core imports nothing from kitchen_core.

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
