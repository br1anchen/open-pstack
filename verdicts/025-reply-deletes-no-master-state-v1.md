# Verdict 025: reply-deletes-no-master-state

status: reject
units: 005 025
range: c5cf25045919b5390bebab589b40d39e79a036c0..6a83f500bc5d5ed07276c49035859a9b8322e879
verifier: amp, claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

1. Unit 025 Acceptance "A routine Amp dispatch runs `amp threads context` once" is broken as written. I ran one routine `dispatch` (unit 002, the same profiles as 001) that keeps the sidekick (`kept: ... (app, context 10%)`). The fake amp's call log shows **3** `amp threads context` calls on the sidekick's thread at the head, against 4 at the parent 7a0a233. The master's thread gets 1 more. Only the keep-or-rotate decision now reads the context once (`session_decision` → `m.context(name)`, rotate.py:197; RotationReasonTests and AmpContextTests pin that). The other two calls come from the message path's compaction checks, `playbook_delivery.py:186` (`m.compactions(name)`) and `mux/amp.py:1143` (`thread_compactions(thread)`), and each runs its own `amp threads context`. The brief's Step 3 asks for "once per decision", which holds. Unit 025's report states that narrower claim too ("once per decision"). The master decides which one the Acceptance means. If it means once per dispatch, the fix is to share the one observation with the delivery path. See Evidence E5.

Every other Acceptance line of 005 and 025 holds (Evidence E1 to E4 and E6). The tests in the range add no unexplained mask: the one `re.sub` builds test names from reason strings. They start no unbounded subprocess and add no sleep.

## Evidence

E1. Brief commands at the head (scratch at 6a83f50, PSTACK_KITCHEN_ROLE=verifier, after `kitchen.py setup`: `setup: ok (1 commands ...)`):

```
$ python3 tests/run.py -k BundleMerge
2 tests, 0 failed, 1s wall with 4 jobs
$ python3 tests/run.py -k keeps_the_sidekick
2 tests, 0 failed, 11s wall with 4 jobs
$ python3 tests/run.py -k rotat
71 tests, 0 failed, 176s wall with 4 jobs
$ python3 tests/run.py -k take_master
10 tests, 0 failed, 30s wall with 4 jobs
$ python3 tests/spec_gen.py --check
| total | 431 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
```

E2. Behavioral gate (whole suite):

```
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base c5cf250...
pstack behavioral: pass
  exit 0 872.9s python3 tests/run.py
(log tail) 1976 tests, 0 failed, 871s wall with 4 jobs
```

E3. Unit 005 repro, failing at its parent c5cf250. The head's test files were copied into a second scratch at c5cf250 and each named test was run:

```
$ python3 tests/run.py -k test_a_late_reply_after_a_newer   (025-base @ c5cf250, head tests)
FAIL: test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone
AssertionError: {'bri[58 chars]fs/001-unit001.md', ...} != {'bri[58 chars]fs/002-unit002.md', ...}
1 tests, 1 failed
$ python3 tests/run.py -k test_a_dispatch_after_rotate_and_the_new   (c5cf250)
AssertionError: True is not False
1 tests, 1 failed
$ python3 tests/run.py -k test_a_dispatch_after_a_failover_that_followed   (c5cf250)
AssertionError: True is not False
1 tests, 1 failed
```

The same tests at the head (6a83f50):

```
$ python3 tests/run.py -k test_a_late_reply_after_a_newer                 -> 1 tests, 0 failed
$ python3 tests/run.py -k test_a_dispatch_after_rotate_and_the_new        -> 1 tests, 0 failed
$ python3 tests/run.py -k test_a_dispatch_after_a_failover_that_followed  -> 1 tests, 0 failed
$ python3 tests/run.py -k test_the_old_masters_bundle_is_a_hand_over      -> 1 tests, 0 failed
$ python3 tests/run.py -k RotationReasonTests                             -> 2 tests, 0 failed
```

E4. Unit 025 repro, failing at its parent 7a0a233 (head test files copied into a scratch at 7a0a233), then passing at the head:

```
$ python3 tests/run.py -k test_a_late_reply_after_a_newer   (025-parent @ 7a0a233)
FileNotFoundError: [Errno 2] No such file or directory: '.../runs/demo/reports/002-unit002.md'
1 tests, 1 failed
$ python3 tests/run.py -k RoleReplyMergeTests   (7a0a233)
FAIL ...test_a_sidekick_reply_made_before_the_masters_marker_and_steer_keeps_both_and_brings_its_own_lines
FileNotFoundError: ... store/progress/025-x.md.seen
FAIL ...test_a_verifiers_stale_copy_neither_rolls_back_nor_deletes_the_sidekicks_files_and_its_own_verdict_arrives
AssertionError: 'older report\n' != 'old report\n'
FAIL ...test_an_unknown_senders_new_file_is_added_and_its_omissions_delete_nothing
FileNotFoundError: ... store/reports/001-a.md
$ python3 tests/run.py -k test_a_manual_rotate_says   (7a0a233)
AssertionError: 'rotate: manual\n' not found in "reused pane amp:1\nsidekick demo-sidekick (amp) started ..."
1 tests, 1 failed
$ python3 tests/run.py -k AmpContextTests   (7a0a233)
2 tests, 2 failed
--- at head 6a83f50 ---
$ python3 tests/run.py -k test_a_late_reply_after_a_newer   -> 1 tests, 0 failed
$ python3 tests/run.py -k RoleReplyMergeTests              -> 17 tests, 0 failed
$ python3 tests/run.py -k test_a_manual_rotate_says        -> 1 tests, 0 failed
$ python3 tests/run.py -k AmpContextTests                  -> 2 tests, 0 failed
```

The two-orb regression (tests/test_pstack_kitchen.py:16223) writes `steps/002-unit002.tsv` after the snapshot and asserts that it and `reports/002-unit002.md` survive the stale verifier reply byte for byte. RoleReplyMergeTests includes `test_a_master_owned_log_gets_no_rows_from_a_reply` (decisions.tsv and sessions/) and the shared-log union and absent-log rows, all passing.

E5. Probe (a scratch-only test module, deleted afterwards). After finish_unit 001 it runs one `dispatch` of routine unit 002 in the same profiles through the AmpKitchenTests fixture and counts the lines of the fake amp's `amp.log` that the dispatch adds:

```
== 6a83f50
kept line: ['kept: the amp sidekick keeps its session for this routine unit (app, context 10%)']
dispatch: threads context calls on the sidekick thread: 3
dispatch: all threads context calls: ['threads context T-aaaaaaaa-...-000000000000', 'threads context T-00000000-...-000000000001', 'threads context T-00000000-...-000000000001', 'threads context T-00000000-...-000000000001']
== 7a0a233
kept line: ['kept: the amp sidekick keeps its session for this routine unit (app, context 10%)']
dispatch: threads context calls on the sidekick thread: 4
```

The callers, from the grep at the head:

```
skills/pstack-pair/scripts/pstack_core/commands/rotate.py:197:    used, compacted = m.context(name)
skills/pstack-pair/scripts/pstack_core/mux/amp.py:1143:        compactions = thread_compactions(thread) if thread else None
skills/pstack-kitchen/scripts/kitchen_core/playbook_delivery.py:186:    compactions = m.compactions(name)
```

E6. Goldens unchanged:

```
$ git diff c5cf250 6a83f50 -- tests/pstack-pair/golden | wc -l
0
```

test_pair, test_guided and test_trio pass in the E2 suite. The guided golden is the last test in its log.

## Flows driven

- pstack (pstack-pair core): the two-orb local-store sync driven through the real `pair.sh`/`kitchen.sh` CLI against the fake amp (E3, E4), at the parent and at the head; one routine `dispatch` with its amp call log counted (E5); rotate, then READY, then dispatch, the same after a failover (E3); manual `rotate` (E4).
- pstack-docs (SKILL.md): no user-facing flow; spec_gen --check and the gate cover it (E1, E2).

## Folded Acceptance

none

## Not covered

- I drove no real two-orb Amp run with live threads. The fake amp fixture stands in for it, as in the repo's own tests.

## Lessons

none
