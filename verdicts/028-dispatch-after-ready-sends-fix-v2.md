# Verdict 028: dispatch-after-ready-sends-fix

status: clean
units: 026 023 028
range: b1ca49f1426ba03ccb493f8b1756e9edd4959b7b..7a0a233bfab9268ded17184c9716e26d6a0d521f
verifier: amp claude-sonnet-5-5 (claude-code mode)
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

Finding 1 of verdict 026-v1 (the six rotation-reason cases in one 17 to 24 s test) is fixed: they are six generated tests, each its own process, and the slowest AmpKitchenTests test is 11.8 s on this orb, which was the slower one in the earlier verification (Evidence 1, 2).

## Evidence

Evidence 1: AmpKitchenTests, whole class, at the head (scratch at 7a0a233, `PSTACK_KITCHEN_ROLE=verifier`, 4 cores, 4 jobs). Unit 026 line 1, second half: no test over 15 s.

```
$ python3 tests/run.py -k AmpKitchenTests
slowest:
    11.8s  ...AmpKitchenTests.test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use
     7.8s  ...test_a_brief_sent_again_after_a_pointer_has_its_sections_back
     7.7s  ...test_a_changed_build_is_attached_again_to_a_thread_that_has_the_old_one
     7.7s  ...test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone
     7.2s  ...test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation
     ...
140 tests, 0 failed, 93s wall with 4 jobs
exit 0
```

Evidence 2: the generated rotation tests (rotat run, fresh shell). They are 5 more tests than the 65 of the previous head, as the table has six rows for one test.

```
$ python3 tests/run.py -k rotat
70 tests, 0 failed, 83s wall with 4 jobs
exit 0
(slowest AmpKitchenTests row: 11.2 s test_a_rotated_sidekick_whose_replies_reached_the_master_by_sync_is_archived;
 the AmpGitKitchenTests rows over 15 s are a different class, not named by the Acceptance, and not in this range)
```

Evidence 3: the named test, unit 026 line 1 first half.

```
$ python3 tests/run.py -k test_a_thread_in_a_turn_is_working
     1.3s  test_pstack_kitchen.AmpKitchenTests.test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it
1 tests, 0 failed, 1s wall with 4 jobs
```

Evidence 4: unit 026 line 2, the step-2 case passes at the head and fails with `took_a_brief` forced to False (mutation in a separate scratch: send.py line 181 `not took_a_brief(pair)` replaced by `not False`; reverted, tree clean, scratch removed).

```
$ python3 tests/run.py -k test_a_working_sidekick_that_already_took     # head
     1.0s  ...test_a_working_sidekick_that_already_took_a_brief_of_its_generation_is_refused_without_polling
1 tests, 0 failed, 1s wall with 4 jobs
$ sed -i 's/not took_a_brief(pair)/not False/' send.py ; python3 tests/run.py -k test_a_working_sidekick_that_already_took
FAILED (failures=1)
1 tests, 1 failed, 2s wall with 4 jobs
failed: ...test_a_working_sidekick_that_already_took_a_brief_of_its_generation_is_refused_without_polling
```

Evidence 5: unit 023's Acceptance still holds at the head, and its repro fails on the parent's code (the head's test file run against b1ca49f in a second scratch, restored after).

```
$ python3 tests/run.py -k test_the_first_send_only_dispatch_after_ready     # head
     3.7s  ...test_the_first_send_only_dispatch_after_ready_waits_out_the_ready_turn_and_sends
1 tests, 0 failed, 4s wall with 4 jobs
$ (same test, parent code b1ca49f)
FAILED (failures=1)
1 tests, 1 failed, 4s wall with 4 jobs
```

The test asserts: after a rotation and READY, one `dispatch --send-only` exits 0, prints `sent: BRIEF <b>`, `.dispatch.brief == <b>` and one brief is in the new thread (023 line 1); with a zero wait window it exits 5 with `sidekick demo-sidekick is working` and sends nothing (023 line 2).

Evidence 6: brief commands 1 and 4, each in a fresh shell at the head.

```
$ python3 tests/run.py -k dispatch
74 tests, 0 failed, 64s wall with 4 jobs
exit 0
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
exit 0
```

Evidence 7: the range and the testing standard.

```
$ git log --oneline b1ca49f..HEAD
7a0a233 test(tests): one generated test per rotation-reason case so no AmpKitchenTests test runs over 15 s
b6b68a4 test(tests): a working sidekick that already took a brief of its generation is refused without polling
cd4e197 test(tests): no AmpKitchenTests test inherits the 60 s READY-turn wait
4bca786 fix(skills/pstack-pair): the first dispatch after a replacement's READY waits out the turn that wrote it
$ git diff --stat b1ca49f..HEAD
 skills/pstack-pair/scripts/pstack_core/send.py |  20 +++
 tests/SPEC.md                                  |  32 ++---
 tests/test_pstack_kitchen.py                   | 164 +++++++++++++++++++------
```

send.py changed only in 4bca786, with its test and SPEC lines. The new tests use no sleep and no elapsed-time assert; the step-2 case bounds its poll by export count and `PAIR_READY_POLL_S=0`, its fake `amp` shim returns at once. The rotation table's comment states why each row is its own test. Source is unchanged in 7a0a233 (tests and SPEC only).

## Flows driven

- pstack: `kitchen.sh dispatch --send-only` through the fake amp mux, by the tests of Evidence 4 and 5 (rotation, READY sync, first send-only dispatch; refusal of a working sidekick that took a brief). No real Amp thread was started.
- pstack-docs: no user-facing flow (tests/SPEC.md is generated; `spec_gen.py --check` in Evidence 6).

## Folded Acceptance

none

## Not covered

- A real Amp orb rotation under `PSTACK_MUX=amp`; only the repo's fakes were driven.
- The packet's `kitchen.py ... gate pstack behavioral` line: not run; its commands are the brief commands, run above (Evidence 1, 2, 6).
- Timing is from this 4-core orb, as in verdict 026-v1.

## Lessons

none
