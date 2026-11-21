# Report 026: dispatch-after-ready-sends-fix

status: done
brief: briefs/026-dispatch-after-ready-sends-fix.md
playbook: review-fix
branch: kitchen-followups
head: b6b68a410bae7153de115c72b752b1226b6331aa
tree: clean

## Changed

- cd4e197 test(tests): no AmpKitchenTests test inherits the 60 s READY-turn wait. `AmpKitchenTests.mux_env` (tests/test_pstack_kitchen.py) now sets `PAIR_ROTATE_READY_S='0'`. AmpGitKitchenTests inherits it through `super().mux_env()`. The unit 023 test that exercises the wait still sets its own window (`PAIR_READY_POLL_S='0'`, `PAIR_ROTATE_READY_S` '0' then '30'), which overrides the default.
- b6b68a4 test(tests): `test_a_working_sidekick_that_already_took_a_brief_of_its_generation_is_refused_without_polling`, plus the generated tests/SPEC.md lines. No source file changed (send.py untouched).

## Ran

Finding 1 (verdict 023-v1), slowest five of `python3 tests/run.py -k AmpKitchenTests`, 4 jobs. Before is at cd4e197's parent 4bca786, after is at cd4e197.

```
before (4bca786)                                                after (cd4e197)
62.1s test_a_thread_in_a_turn_is_working_and_a_brief_is_not...   16.2s test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event
15.5s test_every_rotation_under_amp_says_why_in_a_line_...       11.7s test_threads_archive_archives_an_idle_role_with_no_open_work...
12.1s test_threads_archive_archives_an_idle_role_...              7.4s test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone
 8.9s test_a_late_reply_after_a_newer_message_...                 6.9s test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation
 7.5s test_a_sidekick_on_its_own_orb_keeps_its_thread_...         4.3s test_a_retired_sidekick_thread_is_kept_until_the_new_ready_then_archived_and_logged
134 tests, 0 failed, 106s wall           134 tests, 0 failed, 87s wall
```

The named test alone: `python3 tests/run.py -k test_a_thread_in_a_turn_is_working` -> 1.3s (was 62.1s). Brief acceptance ("under 10 s") holds.

Finding 2 (joo 023 low): the new case sets `.dispatch.generation == .sidekick.generation`, the thread `working`, `PAIR_ROTATE_READY_S=30`, `PAIR_READY_POLL_S=0`, and a fake amp that turns the thread idle on its 3rd `threads export`. The dispatch must exit 5 with `is working`, no brief sent, and fewer than 3 exports.

```
$ python3 tests/run.py -k test_a_working_sidekick_that_already_took
1 tests, 0 failed  (1.5s)
# scratch mutation: send.py took_a_brief body replaced by `return False`
$ python3 tests/run.py -k test_a_working_sidekick_that_already_took
sent: BRIEF .../briefs/002-util.md; follow it with the wait command
FAILED (failures=1)
# send.py restored from a copy; git status clean
```

Gates: step 1 `gates: pass (pstack,pstack-docs)`, step 2 `gates: pass (pstack)`. `uv run --frozen ruff format --check`, `ruff check`, `uv check --frozen` pass; `python3 tests/spec_gen.py --check` -> `ok: tests/SPEC.md is current, 0 missing`.

## Self-check

figures: timings above are from `tests/run.py` logs in this orb (/tmp/before.log for before). The after table was taken at cd4e197, before the step-2 test was added; that test takes 1.5 s.
Lessons: L012 held (no Optional stdlib result added; `uv check --frozen` passes).
No time.sleep or elapsed-time assert in tests; the poll is bounded by export counts. `send_message`'s idle behaviour is unchanged (send.py not touched).

## Deviations

1. Acceptance says no AmpKitchenTests test takes over 15 s. `test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event` takes 15.6 s alone (16.2 s under 4 jobs). It took 15.5 s before this unit, so the READY wait does not cause it (that wait would have made it over 60 s); it runs several rotations. I left it, as the brief scopes only the READY wait.
2. Step 2 asserts fewer than 3 exports, not "no more than a single status read". The refusing dispatch reads status twice (the rotation check in the dispatch and `send_message`'s own read), so one read is not attainable; a poll would add a third read and flip the thread idle at it.
3. The run's pinned build (6aaa6770ea89df2b) lacks 507870a (a role's command skips the skills-build stamp under the git store). With it `kitchen.sh step` from ~/.agents/skills failed: `pair.json: belongs to the master ... not to this thread`. I ran `step` and `notes` from this checkout's `skills/pstack-kitchen/scripts/kitchen.sh`, which contains 507870a.
4. I edited tracked files while the step 1 gate was running, which invalidated that run; I reverted to the clean commit, kept the step 2 edit as a patch in .amp/in/orb-backup/step2.patch, and reran step 1 clean before applying it.

## Questions

none

## Follow-ups

- The master's build for the run (6aaa6770ea89df2b) should include 507870a, or roles on a git store cannot `step` from it (Deviation 3).
- `test_every_rotation_under_amp_says_why...` at about 15.5 s is the slowest AmpKitchenTests test; it may be worth a lighter setup.
