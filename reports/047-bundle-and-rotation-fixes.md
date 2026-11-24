# Report 047: bundle-and-rotation-fixes

status: blocked
brief: briefs/047-bundle-and-rotation-fixes.md
playbook: bug-fix
branch: kitchen-followups-047
head: 42b6e081aa0964ab95c25d4dd85e60bb20ac7994
tree: clean

## Changed

Branch `kitchen-followups-047` from origin/main 699837d8. Five commits; steps 1-3 of the brief are recorded with `kitchen.sh step` (gates pass), step 4 is committed twice and not recorded, steps 5 and 6 are not started.

- 3e3b1df3 (recorded) bundle.py `merge_store`: under a reply, a file this side has that the reply lacks is kept (no deletion by omission). Test `BundleMergeTests.test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot` (verifier and sidekick replies).
- dabf4638 (recorded) bundle.py: new `fetch_origin` (no prompt, PSTACK_BUNDLE_FETCH_TIMEOUT_S) used by `origin_baseline`, the receiver's fallback fetches in `fetch` and `import_git`, and `unkept_evidence`; `origin_baseline` returns '' when the fetch fails, so the bundle carries every commit. Tests: the two hand-over tests now expect baseline '' and a bundle that applies in an empty checkout; new test for the receiver's fallback fetch (timeout 9, GIT_TERMINAL_PROMPT=0).
- 42f676e9 (recorded) bundle.py `pack`: a send that carries keep refs (evidence) is cut against `origin_baseline` like a hand-over; only a send with neither is cut against `--remotes`. New test with a commit only the sender's remotes hold; the old test `..._carries_only_its_own_runs_evidence_and_fetches_nothing` is renamed `..._and_is_cut_like_a_hand_over` and now expects `baseline == ''` (it pinned the second-landing behavior this step reverses).
- 1e15329c (NOT recorded: gate failed) spawn.py `record_started` no longer clears `rotation_required`; send.py `require_fresh_session` settles a pending bootstrap on READY whatever the flag says; rotate.py allows a rotation while `bootstrap_pending` with no READY and says `rotate: the new sidekick never sent READY; rotating again`; the `clear_rotation` in `rotate_if_due`'s exit-4 path is gone. Test: `test_the_rotation_flag_goes_with_the_new_generations_ready_and_a_bootstrap_without_one_can_be_rotated_again` (rotation, spawn after close) x (READY, none); it fails on main (both never-READY rows) and passes here. Three existing tests (`test_a_dispatch_after_a_rotation_is_sent_once_the_new_thread_is_ready`, `..._after_rotate_and_the_new_threads_ready_is_sent`, `..._after_a_failover_that_followed_a_done_unit_is_sent_once_ready`) asserted the flag False before READY; they now assert True.
- 42b6e081 (NOT recorded: gate failed) rotate.py: `rotate`'s state checks moved into `require_rotatable`.

## Ran

Step 4 gate failures (two in a row):
```
1e15329c  python3 tests/run.py -> 2046 tests, 1 failed:
  test_pstack_core.FunctionSizeGuardTests.test_no_core_or_variant_function_runs_over_80_body_lines_or_takes_more_than_8_parameters
  (rotate() went over 80 body lines after the unready check)
42b6e081  python3 tests/run.py -> 2046 tests, 1 failed:
  test_pstack_core.EndsItselfGuardTests.test_only_a_command_or_a_named_boundary_ends_itself_with_die_or_exit
  ['pstack-pair/scripts/pstack_core/commands/rotate.py:62 in require_rotatable',
   'pstack-pair/scripts/pstack_core/commands/rotate.py:70 in require_rotatable']
  (my extracted helper raises Die, which only a command may)
```
Logs: steps/jobs/047-bundle-and-rotation-fixes-1e15329ce7a8/out and steps/jobs/047-bundle-and-rotation-fixes-42b6e081aa09/out (log paths inside).
Targeted checks on 42b6e081 pass: `-k FunctionSizeGuard` 2 tests; `-k rotat` 75 tests; `-k failover`; `-k ready`; the new test 1 test. The other 2044 tests of the suite passed in the failing run.

## Self-check

figures: every value above is from commands run in this session
scope: only bundle.py, spawn.py, send.py, commands/rotate.py and the two test files (+ SPEC.md) changed

## Deviations

none

## Questions

Blocked only by the two-failures rule, not by a design conflict. The option I would pick: replace the two `Die(..., 5)` in `require_rotatable` with `RotateRefused` (errors.py has it, code 5, same exit status as `Die(..., 5)`; the guard allows PairError subclasses in helpers; errors.py is not in the brief's Scope, but it is imported only, not edited), run `-k EndsItself -k FunctionSize`, commit, record step 4, then do steps 5 (spawn falls back to the run's recorded sidekick/consultant entry in kitchen_core/setup.py) and 6 (docs). Alternatively keep `Die` in `rotate()` by having `require_rotatable` return the refusal text. Tell me which, or dispatch a fix brief.

## Follow-ups

none
