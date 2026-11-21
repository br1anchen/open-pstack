# Verdict 026: dispatch-after-ready-sends-fix

status: reject
units: 023 026
range: b1ca49f1426ba03ccb493f8b1756e9edd4959b7b..b6b68a410bae7153de115c72b752b1226b6331aa
verifier: amp claude-sonnet-5-5 (claude-code mode)
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

1. Unit 026 Acceptance line 1, second half ("no AmpKitchenTests test takes over 15 s") is not met on this machine, nor on the sidekick's own (its report's Deviation 1 says 15.6 s alone). `test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event` takes 17.5 s alone and 24.0 s inside the 4-job class run (Evidence 1, 2). It is not caused by the READY wait: at the parent b1ca49f, which has no wait, the same test takes 21.1 s on this machine (Evidence 3). The first half of the line ("the named test runs under 10 s") holds: 1.4 s at the head, 63.0 s at 4bca786 (Evidence 4). The sidekick disclosed this as a deviation; accept it, or brief a lighter setup for that test.

Everything else in the Acceptance of both units holds (Evidence 4 to 8). Observation, not a finding: unit 023's fix and its test are in one commit (4bca786), so the failing repro does not land before the fix in history. The 023 brief allowed it ("or with the fix in the same commit"), and the repo's test-touch rule asks for source and test together. I confirmed the repro fails at the parent by running the head's test against the parent's code (Evidence 5).

## Evidence

Evidence 1: AmpKitchenTests, whole class, at the head (scratch at b6b68a4, `PSTACK_KITCHEN_ROLE=verifier`, 4 cores, 4 jobs).

```
$ python3 tests/run.py -k AmpKitchenTests
slowest:
    24.0s  test_pstack_kitchen.AmpKitchenTests.test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event
    22.7s  test_pstack_kitchen.AmpKitchenTests.test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone
    17.5s  test_pstack_kitchen.AmpKitchenTests.test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation
    13.2s  test_pstack_kitchen.AmpKitchenTests.test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use
    ...
135 tests, 0 failed, 177s wall with 4 jobs
exit 0
```

Evidence 2: the three slowest alone at the head (this orb is slower than the sidekick's: the whole class took 177 s here, 87 s in its report).

```
$ python3 tests/run.py -k test_every_rotation_under_amp_says
    17.5s  ...test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event
1 tests, 0 failed, 18s wall with 4 jobs
$ python3 tests/run.py -k test_a_late_reply_after_a_newer_message
     8.9s  ...test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone
$ python3 tests/run.py -k test_a_sidekick_on_its_own_orb_keeps
     8.1s  ...test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation
```

Only the first is over 15 s alone; the other two exceed it only under the class run's contention.

Evidence 3: the same slow test at the parent b1ca49f (before any READY wait).

```
$ python3 tests/run.py -k test_every_rotation_under_amp_says     # at b1ca49f
    21.1s  test_pstack_kitchen.AmpKitchenTests.test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event
1 tests, 0 failed, 21s wall with 4 jobs
```

Evidence 4: the named test, before and after.

```
$ python3 tests/run.py -k test_a_thread_in_a_turn_is_working     # at 4bca786 (unit 023 fix, no mux_env default)
    63.0s  test_pstack_kitchen.AmpKitchenTests.test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it
1 tests, 0 failed, 63s wall with 4 jobs
$ python3 tests/run.py -k test_a_thread_in_a_turn_is_working     # at b6b68a4 (head)
     1.4s  test_pstack_kitchen.AmpKitchenTests.test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it
1 tests, 0 failed, 1s wall with 4 jobs
```

Evidence 5: unit 023's repro. The head's test file run against the parent's code (b1ca49f, in a second scratch; the file copied in, then restored), then at the head.

```
$ python3 tests/run.py -k test_the_first_send_only_dispatch_after_ready     # parent code
archive: 0 archived, 0 kept, 1 in use
kitchen.sh: sidekick demo-sidekick is working; run: pair.sh wait /tmp/.../runs/demo
AssertionError: 5 != 0 : ...
1 tests, 1 failed, 5s wall with 4 jobs
$ python3 tests/run.py -k test_the_first_send_only_dispatch_after_ready     # head
     4.4s  ...test_the_first_send_only_dispatch_after_ready_waits_out_the_ready_turn_and_sends
1 tests, 0 failed, 4s wall with 4 jobs
```

At the head the test asserts: window 0 gives exit 5 with `sidekick demo-sidekick is working` and no brief sent (023 line 2, a dispatch that cannot send exits non-zero with a reason); then, with a 30 s window and the thread idle from its 6th export, exit 0, `sent: BRIEF <b>`, `.dispatch.brief == <b>` and one brief in the thread (023 line 1).

Evidence 6: 026 Acceptance line 2, the step-2 case passes at the head and fails with `took_a_brief` forced to False (mutation in a third scratch, `took_a_brief` body replaced by `return False`, then restored).

```
$ python3 tests/run.py -k test_a_working_sidekick_that_already_took     # mutated
sent: BRIEF /tmp/.../briefs/002-util.md; follow it with the wait command
FAILED (failures=1)
1 tests, 1 failed, 2s wall with 4 jobs
$ python3 tests/run.py -k test_a_working_sidekick_that_already_took     # head
     1.6s  ...test_a_working_sidekick_that_already_took_a_brief_of_its_generation_is_refused_without_polling
1 tests, 0 failed, 2s wall with 4 jobs
```

Evidence 7: the brief commands, each in a fresh shell at the head.

```
$ python3 tests/run.py -k dispatch
74 tests, 0 failed, 32s wall with 4 jobs
$ python3 tests/run.py -k rotat
65 tests, 0 failed, 45s wall with 4 jobs
$ python3 tests/run.py -k AmpKitchenTests
135 tests, 0 failed, 177s wall with 4 jobs   (Evidence 1)
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
```

Evidence 8: the range and the testing standard.

```
$ git log --oneline b1ca49f..HEAD
b6b68a4 test(tests): a working sidekick that already took a brief of its generation is refused without polling
cd4e197 test(tests): no AmpKitchenTests test inherits the 60 s READY-turn wait
4bca786 fix(skills/pstack-pair): the first dispatch after a replacement's READY waits out the turn that wrote it
$ git diff --stat b1ca49f..HEAD
 skills/pstack-pair/scripts/pstack_core/send.py | 20 +++++++
 tests/SPEC.md                                  | 30 +++++------
 tests/test_pstack_kitchen.py                   | 75 +++++++++++++++++++++++++-
```

The new tests use no `time.sleep` and no elapsed-time assert (the poll is bounded by export counts and `PAIR_READY_POLL_S='0'`). The fake `amp` shim is a bounded fake. The `mux_env` default carries a comment saying why it is set. send.py changed only in 4bca786, with its test and SPEC lines; `send.py` is untouched by the two test commits (the diff above). 026's step-2 case is the test that pins the refuse-at-once branch; its commit message b6b68a4 names it.

## Flows driven

- pstack: `kitchen.sh dispatch --send-only` through the fake amp mux, driven by the test of Evidence 5 (rotation, READY sync, first send-only dispatch) and the refusal case of Evidence 6. No real Amp thread was started.
- pstack-docs: no user-facing flow in this range (tests/SPEC.md is generated; `spec_gen.py --check` in Evidence 7).

## Folded Acceptance

none

## Not covered

- A real Amp orb rotation under `PSTACK_MUX=amp`. Only the repo's fakes were driven.
- The packet's `kitchen.py ... gate pstack behavioral` line: not run; its commands are the brief commands run in Evidence 7.

## Lessons

none
