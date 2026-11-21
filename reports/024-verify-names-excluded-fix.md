# Report 024: verify-names-excluded-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/024-verify-names-excluded-fix.md
playbook: bug-fix
branch: kitchen-followups
head: 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb
tree: clean

## Changed

Started from unit 032's head 08666a79 (fetched from origin kitchen-code/kitchen-followups/unit-032). Two commits:

| step | commit | what |
| --- | --- | --- |
| 1 | ce815fa07 | verify_packet.py: `verify --covers` prints one `excluded: unit NNN (in the range, no accepted review); name it with --covers, or verify --landing covers it` line per intervening unit with no accepted review; the packet's context line carries the same remedy. tests/test_pstack_kitchen.py, tests/SPEC.md. |
| 2 | 1e105784a | references/master-verify.md: one sentence on the excluded line in the units-between paragraph. |

- Root cause: `context_section` wrote `unit NNN: no accepted review` into the packet only, so nothing told the master (or the verifier's reader) that the unit was left out of the proof or how to include it.
- Fix: `not_accepted(p, name)` (one verdict check, shared), `excluded_notice(p)` (the printed lines), `REMEDY` (the shared text). `write_packet` prints the notice after writing the packet when the verification has `--covers`; it is on stdout of `verify`. The unit's Acceptance is still not proved (Forbidden respected). Unit number in the line is the NNN prefix, as the brief's example shows.

## Ran

Without the fix (verify_packet.py at HEAD~, scratch copy restored after): `python3 tests/run.py -k covers` -> 40 tests, 1 failed (test_a_covers_packet_says_a_unit_between_has_no_accepted_review).

At the head (1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb):

```
$ python3 tests/run.py -k covers      -> 40 tests, 0 failed
$ python3 scripts/check-pstack.py     -> 65 skills checked; all 48 previous skills covered
```

Tests: test_a_covers_packet_says_a_unit_between_has_no_accepted_review now asserts the packet line names the remedy and that `verify` prints exactly one `excluded: unit 002 ...` line; test_a_covers_packet_lists_the_unit_between_with_its_accepted_exception_as_context asserts no `excluded:` line when the unit between is accepted. The full-suite gates of pstack and pstack-docs passed at step 1 (ce815fa07) and pstack-docs at step 2 (1e105784a).

## Self-check

figures: counts are tails of the commands shown; gate results are the "step N recorded" lines.
acceptance: a covers verification whose range holds a unit with no accepted review prints the excluded line naming both remedies (test above); a range without one prints none (the accepted-unit test; test_a_packet_without_covers_has_no_units_between_context also passes).
callers: `context_section`: one caller (unit_sections, p.covered only); `units_between`: two callers, context_section and excluded_notice (grep); the `no accepted review` string is asserted only in the updated test.
fresh: gates run in this checkout with no cache; -k runs are fresh processes.
gate: ruff format --check, ruff check, uv check --frozen, spec_gen --check pass on step 1's commit; L012 held.

## Deviations

- Step 2 was written in a scratch worktree while step 1's gate ran, then fast-forwarded and recorded; the worktree and branch are removed.
- The timebox of 45 minutes passed during the full-suite gate (about 13 minutes) and the finish rerun.
- The notice is printed from `write_packet` (verify_packet.py), not verify_launch.py: the packet is written there and verify_launch.py needed no change.

## Questions

none

## Follow-ups

none
