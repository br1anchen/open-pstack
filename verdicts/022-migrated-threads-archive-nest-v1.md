# Verdict 022: migrated-threads-archive-nest

status: clean
units: 022
range: 6dcc8e38a288ade2e10d725d007d811dbc7f201d..ece578550c5019b3e36b6d0b37a25039d12859b7
verifier: amp claude-sonnet-5-5 (claude-code mode)
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

Notes (not findings): the fixes and the tests that pin them land in the same commits (28b1cb0, fe439ae), not test-first; allowed by the repo rule "source change in the same commit as its test". 9ececb6 and ece5785 only regenerate SPEC.md (and docs in ece5785). The unit's tests exercise the real `kitchen.sh` CLI against the fake amp (`run_sh`), so they are the repo's own surface for this behaviour; no real Amp threads or a real origin were driven.

## Evidence

E1. Brief commands at the head (scratch at ece5785, PSTACK_KITCHEN_ROLE=verifier, after `kitchen.py setup` -> `setup: ok (1 commands ...)`):

```
$ python3 tests/run.py -k archive
24 tests, 0 failed, 57s wall with 4 jobs
$ python3 tests/run.py -k nest
12 tests, 0 failed, 13s wall with 4 jobs
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
```

E2. Behavioral gate from the Prove block:

```
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 6dcc8e38a288ade2e10d725d007d811dbc7f201d
pstack behavioral: pass
  exit 0 478.3s python3 tests/run.py  log: .../logs/20261121T075354/pstack-behavioral-0.log
exit 0
```

E3. Acceptance 1, failing at the parent's code. A second scratch (head tests, `skills/pstack-pair/scripts` checked out from 6dcc8e3, so no migrated_at, no before_migration):

```
$ python3 tests/run.py -k after_store_to_git
ERROR: test_after_store_to_git_a_thread_from_before_it_with_no_request_archives_by_the_local_rule_and_one_from_after_it_keeps_no_drain_receipt
KeyError: 'migrated_at'
```

Then with only store_to_git.py restored to the head (migrated_at recorded, but amp_routing.py still the parent's: pre-migration thread still kept for "no drain receipt"):

```
$ python3 tests/run.py -k after_store_to_git
FileNotFoundError: [Errno 2] No such file or directory: '.../fake/amp-archived'   (nothing was archived)
1 tests, 1 failed
```

At the head the same test passes (E1, within the 24 `archive` tests): the test asserts archived == [old sidekick, consultant], `kept <new sidekick>: no drain receipt (the store branch records no request to it)`, and the pre-migration thread still in a turn is `kept ...: working`.

E4. Acceptance 2, failing at the parent's code (same second scratch):

```
$ python3 tests/run.py -k nest_discover
AssertionError: 1 != 0 : kitchen.sh: usage: kitchen.sh nest <store> [--done <thread>...]
1 tests, 1 failed
```

At the head `test_nest_discover_sets_the_oldest_master_as_anchor_and_records_the_threads_without_a_row_once` passes (E1, `-k nest`, 3.7s): with `.anchor` cleared it sets `{'thread': <oldest master>}` (master started=10 vs sidekick 20), records the current master, setUp's sidekick, the threads.json sidekick, and a thread found by search as consultant, all pending under the oldest master; a second `--discover` leaves rows and anchor unchanged; after `--done` on all, `--discover` prints `nest: nothing pending`.

E5. Diff vs the evidence: `before_migration` only returns '' when pair.json holds an integer `migrated_at` and the thread's `started`/`at` is >0 and < migrated_at, and only inside the `not requests` branch of `undrained`; the receipt rule for a thread with requests is untouched; the new-thread path keeps the old reason (tested). `nest.discover` reuses `Amp.discover` and `record_pending`. `migrated_at` is written inside the same `update` as the backend record; the second `store-to-git` leaves it unchanged (test asserts it). The test_pstack_core.py edit is one assertion that allowed the new key.

E6. Test standing check: the new tests have no regex masks, filters or tolerances (the only normalisation is `sorted`/`assertCountEqual` on set-like output, and `# fmt: skip` is formatting); every subprocess goes through the existing `run_sh` / fake amp harness. No sleeps or elapsed-time asserts. Tests run with the harness's scrubbed environment; I exported PSTACK_KITCHEN_ROLE/RUN only as the packet says.

## Flows driven

- pstack (behavioral): the `kitchen.sh threads --archive` and `kitchen.sh nest --discover` commands, driven by the unit's tests against the fake amp and a bare-repo origin (E1, E3, E4); the full behavioral gate (E2).
- pstack-docs: SKILL.md sentences read (migrated-thread rule, `nest --discover`, `migrated_at` in the store-additions table); `spec_gen --check` and the gate pass (E1, E2).

## Folded Acceptance

none

## Not covered

No drive against real Amp threads or a real migrated store; the follow-up in the report stands (a thread with no recorded `started`/`at` keeps the receipt rule until `threads --discover` records it).

## Lessons

none
