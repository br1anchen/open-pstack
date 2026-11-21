# Report 036: land-check-gone-head-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/036-land-check-gone-head-fix.md
playbook: review-fix
branch: kitchen-followups
head: 84ea957f4c38312234be77534684fff3d7e8ff82
tree: clean

## Changed

Start: unit 035 head 875c8712 (fetched from kitchen-code/kitchen-followups/unit-035, as the brief says).

```
  .../pstack-kitchen/scripts/kitchen_core/landing.py   |  2 +-
  skills/pstack-pair/scripts/pstack_core/bundle.py     | 20 ++++++++++++++++----
  tests/test_pstack_core.py                            | 19 ++++++++++++++++++-
  tests/test_pstack_kitchen.py                         |  2 +-
  4 files changed, 36 insertions(+), 7 deletions(-)
```

Commits: e569c3d7a (finding 1, step 1 recorded), 84ea957f4 (finding 2, step 2 recorded).

## Ran

Finding 1 (verdict 034-v1 finding 1): keep_evidence pins only entries with has_commit. The step 2 test of unit 034
(test_an_ordinary_message_whose_evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref) now captures stderr and asserts it is empty.

```
$ python3 tests/run.py -k an_ordinary_message_whose_evidence          (with the fix)
1 tests, 0 failed
$ git stash -q -- skills; python3 tests/run.py -k an_ordinary_message_whose_evidence   (scratch revert of bundle.py)
1 tests, 1 failed
failed: ...test_an_ordinary_message_whose_evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref
```

Finding 2 (verdict notes): unkept_evidence's `git fetch origin` now runs with the PSTACK_BUNDLE_FETCH_TIMEOUT_S timeout (default 30, 0 for none) and
GIT_TERMINAL_PROMPT=0, as origin_baseline's does; land-check says "no other accepted unit's head can be shown to carry its change". The same bundle test asserts the fetch's
timeout (7 with the env set) and GIT_TERMINAL_PROMPT; test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit carries the new wording.

```
$ python3 tests/run.py -k evidence                                  (with the fix)
9 tests, 0 failed
$ python3 tests/run.py -k land_check                                (with the fix)
19 tests, 0 failed
$ same two commands with skills/ reverted to the step's parent       (scratch revert, git stash -- skills)
evidence: 9 tests, 1 failed (...evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref)
land_check: 19 tests, 1 failed (test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit)
```

Gates: `kitchen.sh step` printed "gates: pass (pstack,pstack-docs)" for both steps. Before the first step: `python3 tests/spec_gen.py --check` ok (431 tests, 0 missing),
`uv check --frozen` all checks passed. The Verify block is rerun by `finish` at the head.

## Self-check

figures: every number above comes from the commands shown
acceptance: each step's tests fail without its fix (the scratch reverts above); 006's Acceptance and the bundle, sync, land_check and verify tests are in the Verify block that finish reruns
callers: keep_evidence has one caller (move_git, bundle.py:880), unkept_evidence one (bundle.py:825); the old wording appears only in landing.py:604 and tests/test_pstack_kitchen.py (both updated); rg for the old wording found 2 hits before (landing.py:604 and one assertion in tests/test_pstack_kitchen.py), both edited
fresh: tests/run.py runs one process per test
gate: ruff format, ruff check and uv check --frozen pass on both commits (the step gates)
review: joo was not run in this thread (the diff is 36 added lines over four files; the brief names joo as the review engine for the verifier); the diff was read in full
build: this orb runs the installed pstack build, whose kitchen.sh refuses every write to pair.json from a role thread (record_skill_build stamps a skill_builds row); I ran kitchen.sh from this checkout's skills/pstack-kitchen/scripts/kitchen.sh, whose path matches the last stamp, as verifier 034 did

## Deviations

none

## Questions

none

## Follow-ups

- record_skill_build (kitchen startup hook) tries to write pair.json from any role thread whose skills directory differs from the master's last stamp, which fails under the git store; it should skip non-master threads (unit 034's verdict reports the same).
- joo run 7ce4977c's five other lows stay follow-ups, as the brief says.
