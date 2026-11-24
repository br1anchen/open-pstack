# Report 047: bundle-and-rotation-fixes

status: done
brief: briefs/047-bundle-and-rotation-fixes.md
playbook: bug-fix
branch: kitchen-followups-047
head: 973660250d7aa41552d66f9f0e837c38f6006699
tree: clean

## Changed

Branch `kitchen-followups-047` from origin/main 699837d8, eight commits, each recorded with `kitchen.sh step` (gates pass). The earlier blocked report is superseded.

- 3e3b1df3 (brief step 1) bundle.py `merge_store`: under a reply, a file this side has that the reply lacks is kept. Test `BundleMergeTests.test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot` (verifier and sidekick replies).
- dabf4638 (step 2) bundle.py: `fetch_origin` (no prompt, PSTACK_BUNDLE_FETCH_TIMEOUT_S) is the one bounded origin fetch, used by `origin_baseline`, the receiver's fallback fetches in `fetch` and `import_git`, and `unkept_evidence`; `origin_baseline` returns '' when the fetch fails, so the bundle carries every commit. Tests: the two hand-over tests now expect baseline '' and a bundle that applies in an empty checkout; new test for the receiver's fallback fetch (timeout, GIT_TERMINAL_PROMPT=0).
- 42f676e9 (step 3) bundle.py `pack`: a send that carries keep refs is cut against `origin_baseline` as a hand-over is; only a send with neither uses `--remotes`. New test with a commit only the sender's remotes hold. The old test `..._carries_only_its_own_runs_evidence_and_fetches_nothing` pinned the second-landing behavior this step reverses; it is renamed `..._and_is_cut_like_a_hand_over` and expects `baseline == ''`.
- 1e15329c, 42b6e081, ce25a435 (step 4, recorded at ce25a435) spawn.py `record_started` no longer clears `rotation_required`; READY clears it (`mark_ready`; send.py `require_fresh_session` settles a pending bootstrap on READY whatever the flag says); rotate.py `require_rotatable` lets a rotation through while `bootstrap_pending` with no READY (`rotate: the new sidekick never sent READY; rotating again`, reason `no READY`), and raises `RotateRefused` (code 5) where `rotate` raised `Die(..., 5)`; the `clear_rotation` in `rotate_if_due`'s exit-4 path is gone. Test `test_the_rotation_flag_goes_with_the_new_generations_ready_and_a_bootstrap_without_one_can_be_rotated_again`: (rotation, spawn after close) x (READY, none). Three existing tests asserted the flag False before READY; they now assert True.
- a1a7cca9 (step 5) kitchen_core/setup.py `recorded_start`: with no `--kind` and no roster kind, `spawn` and `consultant` start the entry pair.json records (`kind`, `start_args`, current no-mcp path), permission `none` as `rotate` does, and print `spawn: no host roster here; starting the run's recorded <role> (<label>)`; a store recording none keeps today's errors. Test `test_spawn_on_an_orb_with_no_host_roster_starts_the_runs_recorded_sidekick_and_consultant`. Two core behaviour expectations changed (`an empty roster and no --kind` in `test_spawn_behaviour` and `test_consultant_behaviour`): their stores record a claude sidekick and a codex consultant, so they now start it.
- 97366025 (step 6) SKILL.md: rotation flag and READY, rotate after a never-READY bootstrap, the bundle cut with origin down, replies deleting nothing, spawn's fallback. tests/SPEC.md regenerated in each commit.

## Ran

How each test was shown failing before its fix (source stashed, the test kept):
- step 1: `1 tests, 1 failed` (the verdict was deleted).
- step 2: `30 tests, 2 failed` on the two hand-over tests; the fallback-fetch test `1 tests, 1 failed`.
- step 3: `1 tests, 1 failed` (evidence not kept).
- step 4: both never-READY rows failed on main (`FAILED (failures=2)`), READY rows passed on main already.
- step 5: `1 tests, 1 failed` (`no --kind and no [sidekick] kind in the host roster`).

Gate history: step 4 failed twice before it recorded (rotate() over the 80-line limit, then `Die` in a helper); the master's steer s1 chose `RotateRefused`; the third run passed. Every other step passed the first time: `gates: pass (pstack,pstack-docs)`, including the full `python3 tests/run.py`.

`kitchen.sh finish` reruns the whole Verify block at this head.

## Self-check

figures: every value above is from commands run in this session
scope: bundle.py, spawn.py, send.py, commands/rotate.py, kitchen_core/setup.py, SKILL.md, tests/test_pstack_core.py, tests/test_pstack_kitchen.py, tests/SPEC.md; nothing else
L012: no unchecked Optional; `uv check --frozen` passed before each step

## Deviations

- Steer s1 (redirect: `require_rotatable` raises `RotateRefused`) applied as written.
- skills/pstack-pair/SKILL.md (in Scope) needed no change; the behaviour is in the kitchen's SKILL.md.
- Step 4's test list beyond the brief: the three existing tests that asserted `rotation_required` False before READY were updated.

## Questions

none

## Follow-ups

- Under the git store `rotation_required` is derived from the report and the dispatch generation, so it was never affected by `record_started`; only the local store changed.
