# Report 019: role-stamp-under-git

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/019-role-stamp-under-git.md
playbook: bug-fix
branch: main
head: 507870ac8d8026fca6a879bb2f53c0caff45e483
tree: clean

## Changed

Started from 7616b0e (unit 018's head, this checkout).

```
$ git diff --stat 7616b0e..HEAD
 .../pstack-kitchen/scripts/kitchen_core/setup.py   |  6 ++--
 tests/SPEC.md                                      | 18 ++++++------
 tests/test_pstack_kitchen.py                       | 34 ++++++++++++++++++++++
```

- What was broken: under the git store a role thread could not run any `kitchen.sh <cmd> <store>` when its skills directory differed from the last `skill_builds` row; it failed with `pair.json: belongs to the master (...), not to this thread (...)`.
- Root cause: the kitchen variant's `startup` hook calls `record_skill_build`, which calls `update(store, ...)`; `update` calls `gitstore.require_master`, which refuses a role. Seen in a traceback through variant.py:229 -> setup.py:68 -> store.py:534 -> gitstore.py:277.
- Fix (507870ac8, kitchen_core/setup.py): `record_skill_build` returns without writing when `gitstore.is_git(store)` and not `gitstore.caller_is_master(store)`. The master's commands still append the row; the local-store path is untouched.
- Regression test, same commit: `test_a_role_from_another_skill_build_runs_kitchen_commands_and_a_master_still_stamps_its_build` (AmpGitKitchenTests). The sidekick's `finish` with the last row naming `/old/skills` succeeds, pair.json on the branch is byte-identical afterwards, the report is on the branch, and a master `status` from the new directory appends its row.

## Ran

```
$ python3 tests/run.py -k skill_build   -> 2 tests, 0 failed
$ python3 tests/spec_gen.py --check      -> ok: tests/SPEC.md is current, 0 missing
$ python3 tests/run.py -k stamps         -> 10 tests, 0 failed (the local-store stamp tests, unchanged)
$ uv run --frozen ruff format / ruff check / uv check --frozen -> all passed
```

Without the fix (skills/ stashed) the new test FAILS with the `belongs to the master` error, which is the repro; with it, it passes. Step 1 recorded with `gates: pass (pstack,pstack-docs)`.

## Self-check

figures: every value above comes from the commands shown
acceptance: (1) a sidekick's finish under git with a differing last row succeeds and pair.json on the branch is unchanged (asserted via `git show kitchen/demo:pair.json` before and after). (2) a master command from a new directory still appends a row (asserted).
callers: `record_skill_build` has one caller, the kitchen variant's startup; `caller_is_master` and `is_git` are used as they are.
fresh: tests ran in this checkout, no cache
gate: format, ruff check and uv check --frozen pass; lesson L012: no Optional stdlib result added
forbidden: gitstore's ownership table untouched

## Deviations

- The installed build I run under (~/.agents/skills, the master's pinned build) does not hold this fix, so I still ran `kitchen.sh` through .amp/in/orb-backup/kitchen-nostamp.py, including this report's finish. The wrapper can go once a build with 507870ac8 is installed.
- Test name uses `skill_build` (not `skills_build`) so the brief's `-k skill_build` selects it.

## Questions

none

## Follow-ups

- Each step's gates take about 7 to 10 minutes of repeated `step` reruns; not investigated.
