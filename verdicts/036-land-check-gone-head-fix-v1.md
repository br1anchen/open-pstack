# Verdict 036: land-check-gone-head-fix

status: clean
units: 034 006 035 036
range: 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb..84ea957f4c38312234be77534684fff3d7e8ff82
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Every Acceptance line of units 034, 006, 035 and 036 holds at 84ea957f. Verdict 034-v1's finding 1 is fixed: a missing evidence commit now prints one line and nothing on stderr. Both of its notes are addressed: the fetch is bounded and never prompts, and land-check uses the "can be shown to carry" wording.

Notes, none of them findings:
- This orb had no scratch for the unit. I made it with `git worktree add --detach <scratch> 84ea957f` (every commit arrived by sync) and ran the packet's `setup` (`setup: ok (1 commands ...)`, exit 0). `PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups` was exported throughout. Extra worktrees held the reverts and the parent/base comparisons: `036-revert` at the head, `036-parent` at 875c8712, `036-step1` at e569c3d7 and `036-base` at 1e105784. I removed them all before writing this verdict.
- Two 034 steps (6598293c, 868a0093) do not reverse cleanly at the 036 head, because 84ea957f and e569c3d7 edit the same lines. I reversed each at its own commit instead (Evidence 4b).
- The new step 2 test records `subprocess.run` through a pass-through wrapper. It adds no mask or normalisation and no sleep or elapsed-time assert. Its one fetch is bounded (timeout 7 in the test). The fixture's own git calls carry `timeout=60`. Each source change is in the same commit as its test, and `spec_gen --check` is current.

## Evidence

1. Brief commands (034, 006, 035, 036), each from the scratch root at 84ea957f, plus the repo's other pre-commit checks:

```
$ python3 tests/run.py -k bundle
30 tests, 0 failed, 16s wall with 4 jobs
exit 0
$ python3 tests/run.py -k sync
22 tests, 0 failed, 10s wall with 4 jobs
exit 0
$ python3 tests/run.py -k land
90 tests, 0 failed, 37s wall with 4 jobs
exit 0
$ python3 tests/run.py -k land_check
19 tests, 0 failed, 11s wall with 4 jobs
exit 0
$ python3 tests/run.py -k evidence
9 tests, 0 failed, 3s wall with 4 jobs
exit 0
$ python3 tests/run.py -k verify
86 tests, 0 failed, 44s wall with 4 jobs
exit 0
$ python3 tests/run.py -k take_master
11 tests, 0 failed, 19s wall with 4 jobs
exit 0
$ python3 tests/spec_gen.py --check
| total | 431 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
exit 0
$ uv run --frozen ruff format --check
111 files already formatted
exit 0
$ uv run --frozen ruff check
All checks passed!
exit 0
$ uv check --frozen
All checks passed!
exit 0
$ uv lock --check
Resolved 3 packages in 1ms
exit 0
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
exit 0
```

2. Full suite at the head (brief command 4 of 034 and 036, and 035's command 3):

```
$ python3 tests/run.py
(per-test timings omitted)
2011 tests, 0 failed, 696s wall with 4 jobs
exit=0
```

3. Behavioral gate from the Prove block:

```
$ python3 kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb
pstack behavioral: pass
  exit 0 567.6s python3 tests/run.py  log: .../repos/repo-f328a3c3/logs/20261121T135851/pstack-behavioral-0.log
(log tail) 2011 tests, 0 failed, 567s wall with 4 jobs
exit=0
```

4a. Acceptance 034-1, 036-1 and 035-1, scratch reverts at the head (`git diff <c>^ <c> -- skills | git apply -R` in a worktree at 84ea957f, then the pinning tests, then `git checkout -- skills`). Each step's tests fail with its fix reversed and pass at the head (timing columns trimmed):

```
== step c3bce56d (034 step 1)
-- reverted: -k carries_only_its_own   1 tests, 1 failed
failed: test_pstack_core.BundleBranchApplyTests.test_an_ordinary_send_carries_only_its_own_runs_evidence_and_fetches_nothing
-- head:     -k carries_only_its_own   1 tests, 0 failed
== step 70ed949c (034 step 4)
-- reverted: -k pin_that_cannot        1 tests, 1 failed
failed: ...test_a_pin_that_cannot_be_made_says_so_in_one_warn_line_and_a_good_one_is_silent
-- reverted: -k gates_mode_verifies    1 tests, 1 failed
failed: test_pstack_kitchen.KitchenScriptTests.test_gates_mode_verifies_without_a_verifier
-- head: both                          1 tests, 0 failed each
== step 875c8712 (035)
-- reverted: -k reported_once_as_ahead 1 tests, 1 failed
failed: ...test_the_senders_branch_here_is_reported_once_as_ahead_diverged_or_fast_forwarded_by_where_it_stands
-- head:                               1 tests, 0 failed
== step e569c3d7 (036 step 1)
-- reverted: -k evidence_commit_is_missing  1 tests, 1 failed
failed: ...test_an_ordinary_message_whose_evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref
-- head:                                    1 tests, 0 failed
== step 84ea957f (036 step 2)
-- reverted: -k evidence_commit_is_missing  1 tests, 1 failed
-- reverted: -k names_only_the_landing      1 tests, 1 failed
failed: test_pstack_kitchen.KitchenScriptTests.test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit
-- head: both                               1 tests, 0 failed each
```

035's ahead row fails by itself with the fix reversed (`[ahead]` subTest), and it prints the old diverged line and recipe:

```
FAIL: test_the_senders_branch_here_is_reported_once_... [ahead]
AssertionError: 'ahead of m' not found in 'store: 0 updated, 0 removed\ncheckout: kept 7a18cf8dc: X stays, and branch Y here diverged from m (ffc87562d)\n  ... to take m's commits and keep this side's under kitchen-recovery/<oid>, run ...'
FAILED (failures=1)
```

4b. The two 034 steps that cannot reverse at the 036 head, each reversed at its own commit:

```
== at 6598293c itself (034 step 2), reverse 1 file changed, 24 insertions(+), 5 deletions(-)
-- reverted: 1 tests, 1 failed
failed: ...test_an_ordinary_message_whose_evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref
-- at 6598293c: 1 tests, 0 failed
== at 868a0093 itself (034 step 3), reverse 1 file changed, 54 insertions(+), 12 deletions(-)
-- reverted: 1 tests, 1 failed
failed: ...test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit
-- at 868a0093: 1 tests, 0 failed
```

5. Verdict 034-v1's finding 1 and the bounded fetch, driven with real git (probe `.amp/in/orb-backup/v036/probe_missing.py`, run with `python3 -I` against each worktree's `pstack_core`). A sender pins an evidence commit and packs an ordinary message. The message arrives without its git part at a fresh `--no-local` clone of a bare origin at the same path. Mode `lacks`: origin does not hold the commit. Mode `holds`: origin holds it. Mode `hang`: origin is an ssh URL whose transport sleeps 40s, with `PSTACK_BUNDLE_FETCH_TIMEOUT_S=3`.

```
== lacks @ 875c8712 (036 parent)
evidence lines: ['evidence: not kept, the commit was not carried and origin lacks it: run1/1925770...']
stderr: 'warn: could not keep 1925770... under refs/pstack/keep/run1/1925770...\n'
== lacks @ e569c3d7 (036 step 1)
evidence lines: ['evidence: not kept, the commit was not carried and origin lacks it: run1/a4df0b8...']
stderr: ''
== lacks @ 84ea957f (head)
evidence lines: ['evidence: not kept, the commit was not carried and origin lacks it: run1/55b2c9e...']
stderr: ''
store file: 'from the sender\n'
keep refs: (none)
== holds @ 84ea957f (head)        (control: a commit origin holds is fetched and kept)
evidence lines: ['evidence: 1 commits kept under refs/pstack/keep']
stderr: ''
keep refs: refs/pstack/keep/run1/0ed44dd735df3491bb7859d2ae73722b8bf6bb02
== hang @ 875c8712 (036 parent)
apply returned after 80.2s
stderr: 'warn: could not keep a51cd87... under refs/pstack/keep/run1/a51cd87...\n'
== hang @ 84ea957f (head)
apply returned after 3.0s
evidence lines: ['evidence: not kept, the commit was not carried and origin lacks it: run1/3c13580...']
stderr: ''
store file: 'from the sender\n'
keep refs: (none)
```

At the head, the store file still syncs and the missing commit gets exactly one line. The fetch stops at the 3s knob, where the parent hangs. (A first `hang` attempt returned in 0.1s, because ssh appended arguments to `sleep 40`. I reran with `sleep 40; :`, and the output above is from that rerun.)

6. Acceptance 006-1 and 006-3: the original failure at the base, then gone at the head. The head's test files were copied into a worktree at 1e105784:

```
@ 1e105784 $ python3 tests/run.py -k survive_a_rewrite
subprocess.CalledProcessError: Command '['git', 'rev-parse', 'refs/pstack/keep/demo/4d9e0e83...']' returned non-zero exit status 128.
1 tests, 1 failed
@ 1e105784 $ python3 tests/run.py -k evidence
9 tests, 6 failed
@ 1e105784 $ python3 tests/run.py -k recovery_command_land_check   (land-check output printed by a debug line)
LANDCHECK>> unverified: unit 001 has no clean verdict (kitchen.sh verify <store> 001)
unverified: unit 002 has no clean verdict (kitchen.sh verify <store> 002)
1 tests, 1 failed (IndexError: no 'again with ' in the line)
@ 84ea957f $ (same test, same debug line, removed after)
LANDCHECK>> unverified: unit 001: its evidence is missing (dispatch base: 6ce3c36a9f97...); restore it from the bundle that carried it, or prove its Acceptance again with kitchen.sh verify <store> 002 --covers 001 --base cbdfbe9315ed...
OK
@ 84ea957f $ python3 tests/run.py -k recovery_command_land_check
1 tests, 0 failed
```

At the head, that test runs the printed `--covers` command, gets `status: clean`, and then land-check prints `land-check: pass` with exit 0. 006-1 passes at the head in Evidence 1's `-k evidence` run (9, 0 failed). 006-2: two successive bundles reach a fresh receiver and survive its gc, in `test_evidence_of_two_successive_messages_reaches_a_fresh_receiver_and_survives_its_gc` (in `-k evidence` and `-k bundle`, 0 failed), and Evidence 5 `holds` drives a fresh receiver with real git. 006-4: the rebased, revert and landed tests pass in the full suite (Evidence 2), and the range removes no test other than 006's own renamed one (verdict 034-v1 Evidence 6). The 036 diff touches only the two named tests.

7. 036 step 2 wording: `landing.py:604` now reads "no other accepted unit's head can be shown to carry its change". The test's `nothing` expectation carries the same text, and reversing it fails `names_only_the_landing` (Evidence 4a).

## Flows driven

- pstack: an ordinary bundle message applied at a fresh receiver with real git, in three origin states, at the parent, step 1 and the head (Evidence 5). land-check's missing-evidence line and the `--covers` recovery through `kitchen.sh` against the fake herdr, at the base and the head (Evidence 6). The behavioral gate (Evidence 3).
- pstack-docs: no docs path in 036's diff; `check-pstack.py` and `spec_gen --check` pass (Evidence 1).

## Folded Acceptance

none

## Not covered

- land-check's recovery path was driven only through the suite's `kitchen.sh` tests against the fake herdr, not on a live store, because the repo allows `kitchen.sh` only against the fake herdr.

## Lessons

L012: held (`uv check --frozen`: All checks passed!, Evidence 1).
