# Verdict 030: reply-deletes-no-master-state-fix

status: clean
units: 025 005 030
range: c5cf25045919b5390bebab589b40d39e79a036c0..b10896e56cc1c5c5ed19ed8b0681926f75abb8fe
verifier: amp, claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Both findings of verdict 025-v1 are fixed at b10896e:

- Finding 1 (three `amp threads context` reads per dispatch) is fixed by 527c8f9. One routine dispatch that keeps the sidekick now adds exactly 1 read on the sidekick's thread (E3).
- The joo lows (the sync line put another role's kept file under "the master's copy") are fixed by b10896e (E3, E5).

Each fix's test is in the same commit as the fix. Each test fails at the unit's parent 550a7d7 and passes at the head.

Every Acceptance line of units 005, 025 and 030 holds at the head (E1 to E7). The range's new test adds no mask or normalisation, starts no unbounded subprocess (it drives `run_sh` against the fake amp), and adds no sleep.

## Evidence

E1. Brief commands, in the scratch at b10896e with PSTACK_KITCHEN_ROLE=verifier and PSTACK_KITCHEN_RUN=kitchen-followups. `kitchen.py setup` ran first and printed `setup: ok (1 commands, ...)`.

```
$ git rev-parse HEAD
b10896e56cc1c5c5ed19ed8b0681926f75abb8fe
$ python3 tests/run.py -k BundleMerge
2 tests, 0 failed, 1s wall with 4 jobs
$ python3 tests/run.py -k keeps_the_sidekick
    12.0s  test_pstack_kitchen.AmpKitchenTests.test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread
    10.0s  test_pstack_kitchen.AmpKitchenTests.test_a_dispatch_that_keeps_the_sidekick_reads_its_context_once
3 tests, 0 failed, 13s wall with 4 jobs
$ python3 tests/run.py -k rotat
71 tests, 0 failed, 123s wall with 4 jobs
$ python3 tests/run.py -k take_master
11 tests, 0 failed, 31s wall with 4 jobs
$ python3 tests/spec_gen.py --check
| total | 431 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
```

E2. Behavioral gate (the whole suite):

```
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base c5cf25045919b5390bebab589b40d39e79a036c0
pstack behavioral: pass
  exit 0 660.7s python3 tests/run.py  log: .../logs/20261121T053835/pstack-behavioral-0.log
(log tail) 1986 tests, 0 failed, 659s wall with 4 jobs
```

E3. Unit 030's two fixes, failing at its parent 550a7d7. I ran the head's test files in a second scratch, then the same tests at the head:

```
$ python3 tests/run.py -k reads_its_context_once   (@550a7d7, head tests)
AssertionError: 3 != 1
1 tests, 1 failed, 9s wall with 4 jobs
failed: test_pstack_kitchen.AmpKitchenTests.test_a_dispatch_that_keeps_the_sidekick_reads_its_context_once
$ python3 tests/run.py -k test_a_verifiers_stale_copy   (@550a7d7, head tests)
AssertionError: Lists differ: ["store: 1 updated, 0 removed; kept the master's copy of: reports/001-a.md"] != ["store: 1 updated, 0 removed; kept sidekick's copy of: reports/001-a.md"]
1 tests, 1 failed, 1s wall with 4 jobs
--- at head b10896e ---
$ python3 tests/run.py -k keeps_the_sidekick     -> 3 tests, 0 failed (includes ...reads_its_context_once)
$ python3 tests/run.py -k RoleReplyMergeTests    -> 17 tests, 0 failed, 2s wall with 4 jobs
```

The context-once test (tests/test_pstack_kitchen.py:16065) counts the lines `threads context <sidekick thread>` in the fake amp's `amp.log` before and after one `dispatch` of routine unit 002. The dispatch prints `kept: the amp sidekick keeps its session`, and the test asserts a delta of 1.

E4. Unit 005 repro, failing at its parent c5cf250 (head test files in a second scratch), then passing at the head:

```
$ python3 tests/run.py -k test_a_late_reply_after_a_newer  (@c5cf250, head tests)
AssertionError: {'bri[58 chars]fs/001-unit001.md', ...} != {'bri[58 chars]fs/002-unit002.md', ...}
1 tests, 1 failed, 9s wall with 4 jobs
$ python3 tests/run.py -k test_a_dispatch_after_rotate_and_the_new  (@c5cf250, head tests)
AssertionError: True is not False
1 tests, 1 failed, 3s wall with 4 jobs
$ python3 tests/run.py -k test_a_dispatch_after_a_failover_that_followed  (@c5cf250, head tests)
AssertionError: True is not False
1 tests, 1 failed, 3s wall with 4 jobs
--- at head b10896e ---
$ python3 tests/run.py -k test_a_late_reply_after_a_newer                 -> 1 tests, 0 failed, 18s wall
$ python3 tests/run.py -k test_a_dispatch_after_rotate_and_the_new        -> 1 tests, 0 failed, 3s wall
$ python3 tests/run.py -k test_a_dispatch_after_a_failover_that_followed  -> 1 tests, 0 failed, 3s wall
$ python3 tests/run.py -k test_the_old_masters_bundle_is_a_hand_over      -> 1 tests, 0 failed, 5s wall
$ python3 tests/run.py -k test_a_master_owned_log_gets_no_rows            -> 1 tests, 0 failed
$ python3 tests/run.py -k AmpContextTests                                 -> 2 tests, 0 failed
```

E5. Unit 025 repro, failing at its parent 7a0a233 (head test files in a second scratch):

```
$ python3 tests/run.py -k RoleReplyMergeTests  (@7a0a233, head tests)
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/.../store/progress/025-x.md.seen'
AssertionError: Lists differ: ['store: 2 updated, 2 removed'] != ["store: 1 updated, 0 removed; kept sidekick's copy of: reports/001-a.md"]
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/.../store/reports/001-a.md'
17 tests, 3 failed, 1s wall with 4 jobs
$ python3 tests/run.py -k test_a_late_reply_after_a_newer  (@7a0a233, head tests)
FileNotFoundError: [Errno 2] No such file or directory: '.../runs/demo/reports/002-unit002.md'
1 tests, 1 failed, 10s wall with 4 jobs
$ python3 tests/run.py -k test_a_manual_rotate_says  (@7a0a233, head tests)
AssertionError: 'rotate: manual\n' not found in "reused pane amp:1\nsidekick demo-sidekick (amp) started in amp:1 ..."
1 tests, 1 failed, 3s wall with 4 jobs
--- at head b10896e ---
$ python3 tests/run.py -k RoleReplyMergeTests              -> 17 tests, 0 failed
$ python3 tests/run.py -k test_a_late_reply_after_a_newer  -> 1 tests, 0 failed
$ python3 tests/run.py -k test_a_manual_rotate_says        -> 1 tests, 0 failed, 6s wall
```

The two-orb regression asserts that both files survive byte for byte (tests/test_pstack_kitchen.py:16271-16276, `for rel in ('reports/002-unit002.md', 'steps/002-unit002.tsv')`). The manual rotate test asserts `rotate: manual` and the event detail (tests/test_pstack_kitchen.py:16604-16605):

```
self.assertIn('rotate: manual\n', out)
self.assertEqual(self.rotate_events(), ['generation:1 reason:manual'])
```

E6. Break attempt: one stale verifier reply that touches files from several owners at once. I ran it as a scratch-only probe module that subclasses RoleReplyMergeTests, and deleted it afterwards. The reply rolls back standing-orders.md, the sidekick's report and step log, a `.seen` marker and a master steer. It also adds a verdict and a new `notes/` file.

```
$ python3 -m unittest test_zz_verifier_probe.ProbeMixedOwners.test_probe_mixed_owner_line   (@b10896e)
PROBE LINES: ["store: 1 updated, 0 removed; kept the master's copy of: notes/new-from-vf.md, progress/001-a.md.seen, standing-orders.md, steers/001-a-s1.md; kept sidekick's copy of: reports/001-a.md, steps/001-a.tsv"]
PROBE standing-orders.md '1. first\n2. second\n'
PROBE reports/001-a.md 'old report\n'
PROBE steps/001-a.tsv '1\tabc\tstep\n'
PROBE progress/001-a.md.seen '3\n'
PROBE steers/001-a-s1.md 'steer\n'
PROBE verdicts/001-a-v1.md 'status: clean\n'
PROBE notes/new-from-vf.md MISSING
Ran 1 test ... OK
```

Every other role's file and every master file keeps the master's copy, and the verifier's own verdict arrives. The sync line puts each kept path under its owner. The new `notes/` file is refused because `notes/` is master-owned in gitstore.RULES (gitstore.py:117 lists `...|notes|...` under MASTER). SKILL.md documents that refusal: "a file this side lacks is added (a master-owned one is not)". It is not a finding.

E7. Goldens unchanged, and the memo's readers and writer:

```
$ git diff c5cf250 b10896e -- tests/pstack-pair/golden | wc -l
0
$ rg -n "thread_context|thread_compactions|\.compactions\(|\.context\(|CONTEXT_MEMO" skills | grep -v "def "
skills/pstack-kitchen/scripts/kitchen_core/playbook_delivery.py:186:    compactions = m.compactions(name)
skills/pstack-pair/scripts/pstack_core/mux/amp.py:128:CONTEXT_MEMO = {}
skills/pstack-pair/scripts/pstack_core/mux/amp.py:145:    doc = thread_context(thread) if thread else None
skills/pstack-pair/scripts/pstack_core/mux/amp.py:172:        compactions = thread_compactions(thread)
skills/pstack-pair/scripts/pstack_core/mux/amp.py:667:    CONTEXT_MEMO.pop(thread, None)
skills/pstack-pair/scripts/pstack_core/mux/amp.py:721:        doc = thread_context(thread) if thread else None
skills/pstack-pair/scripts/pstack_core/mux/amp.py:1156:        compactions = thread_compactions(thread) if thread else None
skills/pstack-pair/scripts/pstack_core/commands/rotate.py:197:    used, compacted = m.context(name)
```

No long-running loop (`wait.py`, the verdict wait) reads a thread's context, so the per-process memo cannot freeze a value that a poll depends on. The one sender, `deliver` (the only `amp threads continue` call, amp.py:672), drops the thread's entry before it sends.

## Flows driven

- pstack (pstack-pair core): I drove these through the real `kitchen.sh`/`pair.sh` CLI against the fake amp, as the repo's own tests do:
  - one routine `dispatch`, counting its amp call log (E3);
  - the two-orb stale reply, at each parent and at the head (E4, E5);
  - rotate, then READY, then dispatch, and the same after a failover (E4);
  - a manual `rotate` (E5).

  I drove the reply merge directly through `bundle.apply` with a mixed-owner reply (E6).
- pstack-docs (SKILL.md): no user-facing flow. spec_gen --check and the gate cover it (E1, E2).

## Folded Acceptance

none

## Not covered

- I drove no live two-orb Amp run with real threads. The fake amp fixture stands in for it, as in the repo's own tests.
- An observation, not a finding: the memo also caches a failed read (None) for the rest of the command, where each reader used to retry. Every caller already treats None as "amp cannot say".

## Lessons

none (the packet lists none for the verifier; brief lesson L012 is the sidekick's, and `thread_context` still returns None on unparseable output, E7)
