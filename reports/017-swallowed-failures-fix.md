# Report 017: swallowed-failures-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/017-swallowed-failures-fix.md
playbook: review-fix
branch: main
head: a9b9132645dcfbed28d4d68f7c936ec1e996a27b
tree: clean

## Changed

Started from ff65476 (unit 003's head, this checkout).

```
$ git diff --stat ff65476..HEAD
 .../scripts/pstack_core/commands/rotate.py         | 17 ++++++------
 skills/pstack-pair/scripts/pstack_core/gitstore.py | 29 ++++++++++++++++----
 tests/SPEC.md                                      | 32 +++++++++++-----------
 tests/test_pstack_kitchen.py                       | 32 +++++++++++++++++++---
```

- Finding 1 (rotate.py), 36ffab68d: `rotate` keeps the spawn's return code (None when it raised) and passes it to `publish_rotation`. A failing publish raises only after a return of 0; after a raise it still warns `the rollback could not be published`, after a returned 3/4 it warns `the rotation could not be published` and the command ends with the spawn's code. Pinned by `test_a_rotation_whose_spawn_returned_4_keeps_that_exit_code_when_its_final_publish_fails` (exit 4, warning) beside the existing return-0 row (`test_a_rotation_that_spawned_still_raises_the_error_of_its_final_publish`, exit 1) and the raise row (`..._rollback_that_cannot_be_published_leaves_the_spawn_error_standing`).
- Finding 2 (gitstore.py), a9b913264: `_fetch_trunk` still fetches the locally named trunk first; when that fetch fails it reads origin's own HEAD (`_origin_head_branch`, `ls-remote --symref origin HEAD`) and fetches that branch (`_fetch_branch`). Returns '' only when origin cannot be read, names no HEAD, or its HEAD is the same branch that already failed. Pinned by the existing fetch_code test, extended: stale `origin/HEAD`=gone with origin's HEAD pointing at an existing `trunk` branch -> `fetch_code` True and sync prints no warning; the earlier row (origin/HEAD=gone, origin HEAD names a branch it does not have) still returns False with the warning.

## Ran

```
$ python3 tests/run.py -k fetch_code -> 1 tests, 0 failed
$ python3 tests/run.py -k rollback   -> 2 tests, 0 failed
$ python3 tests/run.py -k rotation   -> 18 tests, 0 failed
$ python3 tests/spec_gen.py --check  -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format / ruff check / uv check --frozen -> all passed
```

Each new assertion fails without its source change (stashed skills/: `whose_spawn_returned_4` FAIL; extended fetch_code test FAIL). Gates: both steps `gates: pass (pstack,pstack-docs)` and recorded.

## Self-check

figures: every value above comes from the commands shown
acceptance: (1) spawn returns 4 + final publish refused -> exit 4 with `the rotation could not be published`; return 0 + refused still raises (exit 1, `was not pushed`, no warning). (2) stale origin/HEAD with readable origin and an existing HEAD branch -> fetch_code True, no sync warning; trunk unfetchable with origin's HEAD naming a missing branch -> False and the warning. (3) unit 001's tests in the rollback/rotation/fetch_code selections pass unchanged except the two rows the brief names.
callers: `publish_rotation` has one caller (`rotate`); `_fetch_trunk` has two (`fetch_code`, `publish_code`), and the latter only tests the result for truthiness, which the fallback keeps.
fresh: tests ran in this checkout, no cache
gate: format, ruff check and uv check --frozen pass; lesson L012: `re.search` result is checked (`found[1] if found else ''`)
forbidden: gitstore.UNKNOWN_FRESHNESS and commands/sync.py untouched

## Deviations

- I replaced unit 001's single return-0 test by two named tests plus a helper (`rotate_with_the_final_publish_refused`) instead of one table, since the two rows need different exit codes and `run_sh` asserts the code per call; the raise row already existed as a separate test.
- I renamed unit 001's fetch_code test to add the stale-origin/HEAD case.
- Still running `kitchen.sh` through .amp/in/orb-backup/kitchen-nostamp.py (git-store startup-stamp bug from report 000-ready-g3).

## Questions

none

## Follow-ups

- Each step's gates take 8 to 10 minutes of repeated `step` reruns; not investigated.
