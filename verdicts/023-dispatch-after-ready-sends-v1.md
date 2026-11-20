# Verdict 023: dispatch-after-ready-sends

status: reject
units: 023
range: c5cf25045919b5390bebab589b40d39e79a036c0..4bca7862ab6374879d0d0bc6d946448118446d36
verifier: amp, anthropic/claude-opus-5-5
verifier-agent: amp --model anthropic/claude-opus-5-5 --orb-size a1.large

## Findings

Both Acceptance lines hold (Evidence A, B, C). One regression against the testing standard:

1. An existing test now makes about 60 s of real sleep. `test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it` (AmpKitchenTests) took 2.5 s at b1ca49f and takes 63.8 s at 4bca786 (Evidence D), and it is the slowest test in the full suite at 62.5 s (Evidence E). Its sidekick is `working` and its generation never got a brief, so the new `send_message` branch calls `wait_out_ready_turn`. That function polls with `time.sleep` for the default PAIR_ROTATE_READY_S=60 at PAIR_READY_POLL_S=2, because the test sets neither variable. The test still passes, but it now sleeps for real. That breaks "Tests are deterministic: no sleeps", and the 64 s run sits halfway to `run_sh`'s 120 s timeout. I inferred the mechanism from the diff and the timing; I did not instrument it. Smallest fix: set `PAIR_ROTATE_READY_S='0'` (or `PAIR_READY_POLL_S='0'` with a short window) in that test's env, or in the AmpKitchenTests env defaults, so that no test inherits the 60 s wait. Also check that every other AmpKitchenTests test that dispatches to a working, never-briefed sidekick has the same setting.

Notes (not findings):
- The packet's scratch worktree did not exist on this orb, so I created it myself with `git worktree add --detach` at 4bca786. I ran the base comparison in a separate worktree at b1ca49f, unit 005's head and the unit's real parent.
- bug-fix.step-5: the repro test and the fix are in one commit (4bca786). The brief allows this ("or with the fix in the same commit"). My probe and the sidekick's test both fail at b1ca49f (Evidence A).
- The new test's shim is bounded: it runs under `run_sh` with timeout=120, and the sidekick's test sets PAIR_READY_POLL_S=0, so it makes no real sleep. The test has no output mask.

## Evidence

A. Repro at the parent b1ca49f, with a verifier probe added to a throwaway copy of tests/test_pstack_kitchen.py. The probe: rotation, READY synced, new thread `working` until its 3rd export, then one `dispatch --send-only` expecting exit 0:

```
$ (023-base) python3 tests/run.py -k verifier_probe
FAIL test_pstack_kitchen.AmpKitchenTests.test_verifier_probe_one_call_archives_sends_records (7.4s)
AssertionError: 5 != 0 : ...
archived T-00000000-0000-4000-8000-000000000001 (sidekick): a brief is being sent

archive: 1 archived, 0 kept, 1 in use
kitchen.sh: sidekick demo-sidekick is working; run: pair.sh wait /tmp/tmpp10f87yu/state/pstack/kitchen/runs/demo
2 tests, 1 failed, 7s wall with 8 jobs
```

B. The same probe at the head 4bca786: one call archives, sends and records:

```
$ (023-head-probe) python3 -m unittest -v ...test_verifier_probe_one_call_archives_sends_records ...test_verifier_probe_never_idle_exits_nonzero_with_reason
... ok
... ok
Ran 2 tests in 8.735s
OK
--- probe one-call output ---
archived T-00000000-0000-4000-8000-000000000001 (sidekick): a brief is being sent
amp demo-sidekick (T-00000000-0000-4000-8000-000000000002): pstack-kitchen BRIEF /tmp/tmpefxkjc2i/state/pstack/kitchen/runs/demo/briefs/002-guide.md
sent: BRIEF /tmp/tmpefxkjc2i/state/pstack/kitchen/runs/demo/briefs/002-guide.md; follow it with the wait command
archive: 1 archived, 0 kept, 1 in use
--- dispatch.brief=/tmp/tmpefxkjc2i/state/pstack/kitchen/runs/demo/briefs/002-guide.md briefs_in_t2=1
```

C. A dispatch that cannot send (the thread never goes idle, PAIR_ROTATE_READY_S=0) exits 5 with a reason and records nothing. The probe asserts `code=5`:

```
--- probe never-idle output ---
archived T-00000000-0000-4000-8000-000000000001 (sidekick): a brief is being sent

archive: 1 archived, 0 kept, 1 in use
kitchen.sh: sidekick demo-sidekick is working; run: pair.sh wait /tmp/tmprdctnmtx/state/pstack/kitchen/runs/demo

--- dispatch.brief=/tmp/tmprdctnmtx/state/pstack/kitchen/runs/demo/briefs/001-one.md
```

D. The existing test's duration, before and after:

```
$ (023-base) python3 tests/run.py -k test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued
     2.5s  test_pstack_kitchen.AmpKitchenTests.test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it
1 tests, 0 failed, 2s wall with 8 jobs
$ (023-head-probe) python3 tests/run.py -k test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued
    63.8s  test_pstack_kitchen.AmpKitchenTests.test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it
1 tests, 0 failed, 64s wall with 8 jobs
```

E. Prove block, in the scratch at 4bca786 with PSTACK_KITCHEN_ROLE=verifier:

```
$ python3 kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261120T230122-6256.log)
$ python3 kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base c5cf250...
pstack behavioral: pass
  exit 0 402.5s python3 tests/run.py  log: .../logs/20261120T230123/pstack-behavioral-0.log
(log tail)
slowest:
    62.5s  test_pstack_kitchen.AmpKitchenTests.test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it
    51.9s  test_spec_gen.SpecGenCheckTests.test_check_fails_on_a_stale_spec_and_leaves_it_as_it_was
    ...
1963 tests, 0 failed, 401s wall with 8 jobs
```

F. Brief commands, run fresh from the scratch root:

```
$ python3 tests/run.py -k dispatch
     6.2s  test_pstack_kitchen.AmpKitchenTests.test_the_first_send_only_dispatch_after_ready_waits_out_the_ready_turn_and_sends
74 tests, 0 failed, 28s wall with 8 jobs
exit 0
$ python3 tests/run.py -k rotat
65 tests, 0 failed, 44s wall with 8 jobs
exit 0
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
exit 0
```

## Flows driven

- pstack: `kitchen.sh dispatch <store> <brief> --send-only` after a rotation and READY sync, driven through the real kitchen.sh against the fake amp, with a shim that ends the READY turn during the wait (Evidence A, B), and a never-ending turn (Evidence C).
- pstack-docs: the range touches no doc in this unit (4bca786 changes send.py, SPEC.md and the test only); `spec_gen --check` is current (Evidence F).

## Folded Acceptance

none

## Not covered

- A real Amp thread rotation (live orbs) was not driven. The fake amp stands in for the thread's lastKnownAgentState.

## Lessons

L012: held (no Optional stdlib results added; the gate's full suite passes, Evidence E)
