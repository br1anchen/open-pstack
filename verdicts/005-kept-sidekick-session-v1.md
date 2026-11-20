# Verdict 005: kept-sidekick-session

status: clean
units: 005
range: c5cf25045919b5390bebab589b40d39e79a036c0..b1ca49f1426ba03ccb493f8b1756e9edd4959b7b
verifier: amp, anthropic/claude-opus-5-5
verifier-agent: amp --model anthropic/claude-opus-5-5 --orb-size a1.large

## Findings

none.

One note, not a reject: bug-fix step 5 asks that the failing repro land before the fix in history. Each regression lands in the same commit as its fix (b5893cb, d4a195e, 0f1913e). AGENTS.md requires that ("Commit a source change in the same commit as the test that pins it"), so the repo rule wins. To show fail-then-pass, I ran the head's tests against the c5cf250 source (E2).

## Evidence

E1. Brief commands 1 to 5 at the head b1ca49f, in the scratch, each in a fresh shell with PSTACK_KITCHEN_ROLE=verifier:

```
$ python3 tests/run.py -k BundleMerge
2 tests, 0 failed, 1s wall with 8 jobs
$ python3 tests/run.py -k keeps_the_sidekick
    11.1s  test_pstack_kitchen.AmpKitchenTests.test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread
2 tests, 0 failed, 13s wall with 8 jobs
$ python3 tests/run.py -k rotat
65 tests, 0 failed, 59s wall with 8 jobs
$ python3 tests/run.py -k take_master
10 tests, 0 failed, 14s wall with 8 jobs
$ python3 tests/spec_gen.py --check
| total | 431 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
```

E2. Repro at the parent. I created a second scratch at c5cf250 (base source) and copied the head's tests/test_pstack_core.py and tests/test_pstack_kitchen.py into it:

```
$ python3 tests/run.py -k late_reply_after_a_newer_message      # base source
FAIL test_pstack_kitchen.AmpKitchenTests.test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone (9.0s)
AssertionError: {'bri[58 chars]fs/001-unit001.md', 'at': '2026-11-20T22:34:42[102 chars]5.3'} != {'bri[58 chars]fs/002-unit002.md', 'at': '2026-11-20T22:34:46[102 chars]5.3'}
1 tests, 1 failed
$ python3 tests/run.py -k dispatch_after_rotate_and              # base source
AssertionError: True is not False
1 tests, 1 failed
$ python3 tests/run.py -k dispatch_after_a_failover_that         # base source
AssertionError: True is not False
1 tests, 1 failed
$ python3 tests/run.py -k every_rotation_under_amp_says_why      # base source
AssertionError: 'rotate: profiles app vs docs\n' not found in "... rotated: a new sidekick thread is starting; ..."
AssertionError: 'rotate: context 50% >= 50%\n' not found in "..."
FAIL ... [compacted]
$ python3 tests/run.py -k RoleReplyMerge                         # base source
AttributeError: module 'pstack_core.bundle' has no attribute 'role_reply'
$ python3 tests/run.py -k old_masters_bundle_is_a_hand_over      # base source (non-regression guard)
1 tests, 0 failed
```

At the head, all of these pass (E1 `-k rotat`/`take_master`, and the full suite in E4). The late-reply test asserts .dispatch, .consult, a master key added after the snapshot, the sidekick generation, the appended standing order "99.", the existence of briefs/002-unit002.md, delivery of the verdict, and that docs unit 003 prints "keeps its session" with no "rotated:" (test_pstack_kitchen.py L16218-16277).

E3. A probe through the real merge path at the head (scratch script `.amp/in/orb-backup/v005/probe.py`, calling `bundle.role_reply` and `bundle.merge_store` directly). The master store holds sidekick gen 3, dispatch brief 002 gen 3, consult c2, newkey, decisions.tsv, sessions/s.tsv, events.tsv, gates.tsv, standing-orders.md and briefs/002.md:

```
## verifier stale reply, deletes master files, lacks logs: reply=Reply(from_sidekick=False)
  ["store: 1 updated, 0 removed; kept the master's copy of: briefs/002.md, decisions.tsv, sessions/s.tsv, standing-orders.md"]
  dispatch={'brief': 'briefs/002.md', 'generation': 3} consult={'file': 'c2'} newkey=m rot=None
  files= {'decisions.tsv': b'a\nb\n', 'sessions/s.tsv': b'x\n', 'events.tsv': b'e1\n', 'gates.tsv': b'g1\n', 'standing-orders.md': b'new\n', 'briefs/002.md': b'b2\n', 'verdicts/v.md': b'clean\n'}
## current sidekick completion + event row: reply=Reply(from_sidekick=True)
  ["store: 2 updated, 0 removed; kept the master's copy of: briefs/002.md, decisions.tsv, sessions/s.tsv, standing-orders.md; took the sidekick's completion"]
  dispatch={'brief': 'briefs/002.md', 'generation': 3} consult={'file': 'c2'} newkey=m rot=True
  files= {'decisions.tsv': b'a\nb\n', ... 'events.tsv': b'e1\ne2-sidekick\n', ...}
## stale sidekick completion: reply=Reply(from_sidekick=True)          -> rot=None
## verifier carrying matching true (A1): reply=Reply(from_sidekick=False) -> rot=None
## empty from_name: reply=Reply(from_sidekick=False)                  -> rot=None, master files kept
## completion without dispatch generation (A5): reply=Reply(from_sidekick=True) -> rot=None
## hand-over from previous master: reply= None
## first sync (no pair.json): reply= None
## message to a role: reply= None
```

(Lines after the first two cases are trimmed to the rot= field and the classification. Each case kept all the master values shown in case 1.)

E4. Behavioral gate from the packet (full suite):

```
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base c5cf25045919b5390bebab589b40d39e79a036c0
pstack behavioral: pass
  exit 0 363.0s python3 tests/run.py  log: .../logs/20261120T223233/pstack-behavioral-0.log
$ tail -1 .../pstack-behavioral-0.log
1962 tests, 0 failed, 362s wall with 8 jobs
```

E5. Herdr goldens unchanged:

```
$ git diff --stat c5cf250 HEAD -- tests/pstack-pair/golden; git diff c5cf250 HEAD -- tests/pstack-pair/golden | wc -l
0
```

PairScriptTests (test_pair, test_guided, test_trio) pass in E4. rotation_reason returns '' when `not mux.get().reuses_sessions` (rotate.py diff), so Herdr and Rex print no `rotate:` line.

E6. Test hygiene in the range. The added test lines contain no sleep, time.time, monotonic, os.environ, PSTACK_MUX, HERDR_ or AMP_THREAD references, and no re.sub masks:

```
$ git diff c5cf250 HEAD -- tests/test_pstack_core.py tests/test_pstack_kitchen.py | grep -E "^\+" | grep -nE "re\.sub|re\.compile|subprocess|Popen|\.run\(|sleep|time\.time|monotonic|environ|PSTACK_MUX|HERDR_|AMP_THREAD"
(no output)
```

The new helpers use the bounded `run_sh` (timeout=120, test_pstack_kitchen.py L3976) and a subprocess call with timeout=60 (L16145). `spec_gen --check` reports 0 missing (E1).

## Flows driven

- pstack (pstack_core bundle/sync/rotate/spawn): the two-orb late-reply flow ran through `pair.sh sync` and `kitchen.sh notify`/`dispatch` against the fake Amp (E2, E4). The merge was also driven directly over nine sender cases (E3).
- pstack-docs (SKILL.md): no user-facing flow. The docs gates passed as part of the step gates (sidekick report), and E1 shows `spec_gen --check` is current.

## Folded Acceptance

none

## Not covered

- I did not exercise a live two-orb exchange between real Amp threads. The plan states the run is on the git store, so the fake-Amp regression is the proof (plan 021 Risks). E2 and E4 cover that path.
- I did not rerun `ruff`, `uv check` or `check-pstack.py`. They are outside the packet's commands, and the sidekick reports they pass.

## Lessons

none
