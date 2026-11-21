# Report 028: dispatch-after-ready-sends-fix

status: done
brief: briefs/028-dispatch-after-ready-sends-fix.md
playbook: review-fix
branch: kitchen-followups
head: 7a0a233bfab9268ded17184c9716e26d6a0d521f
tree: clean

## Changed

Based on unit 026's head b6b68a4 (fetched from kitchen-code/kitchen-followups/unit-026), as the brief says. One commit, step 1, recorded.

- 7a0a233 test(tests): `test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event` is gone. Its six cases are now the one `ROTATION_REASON_CASES` table (module level, after AmpKitchenTests) and a loop that `setattr`s one test per row on AmpKitchenTests, `test_a_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event_<slug>`. The assertions moved unchanged into `rotation_says_why`; the three setup helpers (`set_context`, `previous_brief_header`, `previous_brief_gone`) became methods. The per-case `self.setUp()` call is gone, since each test now gets its own. tests/SPEC.md regenerated (header sha and the "+N" counts only). Generated tests stay disabled in AmpGitKitchenTests, as the original was.

## Ran

Finding 1 (verdict 026-v1): the six cases, each its own process.

```
$ python3 tests/run.py -k a_rotation_under_amp
3.5s ..._context_50_50   3.4s ..._previous_brief_missing   3.4s ..._context_unreadable
3.4s ..._compacted       3.2s ..._profiles_app_vs_docs     3.1s ..._risk_escalated_routine
6 tests, 0 failed, 7s wall with 4 jobs      (was 17 to 24 s in one process)
```

Slowest five of the whole class (this orb, 4 jobs):

```
$ python3 tests/run.py -k AmpKitchenTests
11.5s test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use
 7.4s test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone
 6.7s test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation
 4.3s test_a_rotated_sidekick_whose_replies_reached_the_master_by_sync_is_archived
 4.2s test_a_retired_sidekick_thread_is_kept_until_the_new_ready_then_archived_and_logged
140 tests, 0 failed, 85s wall with 4 jobs      (none over 15 s; the max is 11.5 s)
```

Acceptance:

```
$ python3 tests/run.py -k test_a_thread_in_a_turn_is_working        -> 1.2s (under 10 s)
$ python3 tests/run.py -k test_a_working_sidekick_that_already_took -> 1.1s, 0 failed
$ (send.py:181 `not took_a_brief(pair)` -> `not False`, then the same run)
1 tests, 1 failed   (the step-2 case fails with took_a_brief forced False; reverted with git checkout, tree clean)
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
```

Unit 023's Acceptance (one `dispatch --send-only` after READY archives, sends and records): its test is untouched and passes in the class run above.

## Deviations

none. The first dispatch of 028 was blocked because b6b68a4 was on no ref; the master pushed `unit-026` and re-dispatched.

## Self-check

figures: every number above comes from the runs shown (this orb; the verifier's orb is slower, 177 s for the class)
Acceptance lines: each mapped to a command above
callers/consumers: only tests changed; no source file touched; no other reference to the removed test name (grep)
gates: step 1 passed (pstack, pstack-docs); ruff format/check clean
