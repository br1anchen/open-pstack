# Report 034: land-check-gone-head-fix

status: done
brief: briefs/034-land-check-gone-head-fix.md
playbook: review-fix
branch: kitchen-followups
head: 70ed949c28672d61c83073bc0f16d2878ebe4e18
tree: clean

## Changed

- c3bce56d step 1: pack carries only the keep refs of its own run (keep_tips takes the store's slug; a store with no slug carries none) and fetches origin only for a hand-over (origin_baseline runs only when branches=True); an ordinary send is cut with --remotes as before 006. bundle.py, tests/test_pstack_core.py, tests/SPEC.md. The BundleRepoCase fixture's pair.json now names its run (run1).
- 6598293c step 2: import_git requires evidence commits only for a hand-over. An ordinary message with a missing evidence commit prints one line, "evidence: not kept, the commit was not carried and origin lacks it: <ref>", and syncs on (unkept_evidence; one origin fetch). The old raise test became an ordinary-message test; a new hand-over test keeps the strict rule.
- 868a0093 step 3: missing_evidence names a gone landing-verdict head only when that verdict's packet lists the unit (### Unit <seq>). recovery_command suggests only a unit whose head holds the lost unit's change (carries_change: inclusion by ancestry or patch-id against the stack ending at that head); with none, unverified_line says so. landing.py, tests/test_pstack_kitchen.py.
- 70ed949c step 4: git.pin writes one "warn:" line on stderr when it cannot make the ref; a gates-mode verification calls pin_evidence for its base, head and the units' heads and bases; the dead NoBrief skip in pin_evidence is gone. verify.py, verify_launch.py, git.py.

## Ran

Each step's new test, with its source file reverted to the parent (scratch revert), then restored:

```
step 1: -k carries_only_its_own   fix reverted: 1 tests, 1 failed    fixed: passes (-k bundle: 30 tests, 0 failed)
step 2: -k evidence_commit_is_missing   reverted: 1 tests, 1 failed  fixed: passes; -k hand_over_whose_evidence: 1 tests, 0 failed
step 3: -k names_only_the_landing   reverted: 1 tests, 1 failed      fixed: -k land: 90 tests, 0 failed
step 4: -k pin_that_cannot reverted: 1 failed; -k gates_mode_verifies reverted: 1 failed; both pass fixed
```

Step 1's test records subprocess.run and shows no fetch call and no baseline in the manifest. Step 3's table test has four cases: the covering verdict gone (blamed), an unrelated verdict gone (not blamed), a carrier that holds the change (named), a carrier that does not (the higher-numbered unit 004 is not named). Gates: each step recorded with "gates: pass (pstack,pstack-docs)". ruff format, ruff check, uv check --frozen and spec_gen --check clean before each commit. finish reruns the brief's Verify list.

## Self-check

figures: every number above is from the commands shown
scope: only bundle.py, git.py, landing.py, verify.py, verify_launch.py, tests/test_pstack_core.py, tests/test_pstack_kitchen.py and tests/SPEC.md changed; step.py was not touched
steer 034-s1: applied (reran step 1 to a record, then did steps 2 to 4 in the same turn)

## Deviations

- Steer 034-s1 (info): applied as written.
- Two existing land-check tests encoded the old carrier rule (any highest-numbered unit whose head resolves). I rewrote them around the new rule: test_land_check_says_which_evidence... became test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit, and the recovery-command test now builds a unit that really carries the lost one.

## Questions

none

## Follow-ups

- Removing keep refs after a run closes needs a retention policy (as the brief says).
- git.pin now warns for every caller, including keep_evidence on the receiving side; its names are validated first, so that warn should be rare.
