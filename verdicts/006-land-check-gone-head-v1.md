# Verdict 006: land-check-gone-head

status: clean
units: 006
range: 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb..6921d9f0870a08ee19d9bac49a6c0c4696e13321
verifier: amp claude-code claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

Notes, none of them findings:
- The first behavioral gate run (`tests/run.py`) ended 2 failed of 2006 (`test_a_child_killed_by_a_signal_ends_the_command_with_128_plus_the_signal`, `test_with_timeout_gives_the_commands_exit_124_when_it_runs_out_and_127_when_it_is_missing`, both 0 != 129). I had started that gate with `nohup`, which makes children ignore SIGHUP (Evidence 9 shows `kill -HUP` exiting 0 under nohup, 129 without). A rerun of the same command in a normal shell is 2006 tests, 0 failed (Evidence 2). Neither test touches this unit.
- Bug-fix playbook step 5 (repro lands before the fix): each of the three commits carries its fix and its tests together, as the repo's rule requires ("a source change in the same commit as the test that pins it"). The failing repro is shown by running the head's tests against the parent's source (Evidence 3), not by history order.
- The unit changes one existing test (`test_the_bundle_carries_the_sidekicks_commit_to_a_checkout_that_lacks_it`, deleting keep refs before its gc, as the report's Deviations say). It is not one of the rebased, revert or landed tests Acceptance 4 names.
- Standing test check on the range: the new tests have no sleeps or elapsed-time asserts, every subprocess in them has `timeout=60`, and they carry no mask or normalisation.

## Evidence

Setup: this orb had no scratch for the unit, so I made it with `git worktree add --detach <scratch> 6921d9f0` (all commits arrived via sync) and ran `kitchen.py ... setup` (`setup: ok (1 commands ...)`, exit 0). `PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups` exported throughout.

1. Brief commands, each in a fresh shell from the scratch root:

```
$ python3 tests/run.py -k land_check
19 tests, 0 failed, 27s wall with 4 jobs
exit 0
$ python3 tests/run.py -k bundle
30 tests, 0 failed, 30s wall with 4 jobs
exit 0
$ python3 tests/run.py -k evidence
8 tests, 0 failed, 11s wall with 4 jobs
exit 0
```

2. Behavioral gate command (`tests/run.py`, the pstack gate's only command), clean rerun at the head, and the other repo checks:

```
$ python3 tests/run.py
2006 tests, 0 failed, 574s wall with 4 jobs
exit 0
$ python3 scripts/check-pstack.py            -> 65 skills checked; all 48 previous skills covered   (exit 0)
$ python3 tests/spec_gen.py --check          -> ok: tests/SPEC.md is current, 0 missing            (exit 0)
$ uv lock --check                            -> Resolved 3 packages                                 (exit 0)
$ uv run --frozen ruff format --check        -> 111 files already formatted                         (exit 0)
$ uv run --frozen ruff check                 -> All checks passed!                                  (exit 0)
$ uv check --frozen                          -> All checks passed!                                  (exit 0)
```

3. Original failure reproduced at the unit's parent (second scratch at 1e105784 with the head's two test files copied over; source is the parent's):

```
$ python3 tests/run.py -k survive_a_rewrite     -> 1 tests, 1 failed
$ python3 tests/run.py -k evidence              -> test_a_message_whose_evidence_cannot_be_imported_raises_and_keeps_nothing, test_evidence_of_two_successive_messages_reaches_a_fresh_receiver_and_survives_its_gc, test_an_evidence_entry_whose_name_is_not_its_own_id_is_not_imported, test_evidence_commits_of_a_step_a_finish_and_a_verification_survive_a_rewrite_of_the_branch_and_gc: FAILED
$ python3 tests/run.py -k says_which_evidence   -> FAILED (failures=3). Parent output for unit head / dispatch base / landing head gone:
unverified: unit 001 has no clean verdict (kitchen.sh verify .../runs/demo 001)
unverified: unit 002 has no clean verdict (kitchen.sh verify .../runs/demo 002)
unverified: unit 003 has no clean verdict (kitchen.sh verify .../runs/demo 003)
$ python3 tests/run.py -k recovery_command_land -> 1 tests, 1 failed
```

At the head the same tests pass (Evidence 1: 8 tests, 0 failed for `evidence`; `land_check` 19, 0 failed).

4. Acceptance 2, driven on two real, separate git repos joined only by bundle files (probe `v6probe/probe_bundle.py`; the receiver is a clone at the base commit with its remote removed, and its manifest is retargeted at the receiver's path). The sender pins c1 and c2, packs message 1, rewrites its branch and runs `gc --prune=now`, pins c3, and packs message 2:

```
sender: c1,c2 survive rewrite+gc: OK
(the receiver lacks all three objects before applying: `git cat-file -e` fails for each)
apply msg1: ['evidence: 2 commits kept under refs/pstack/keep']
apply msg2: ['evidence: 1 commits kept under refs/pstack/keep']
1e894440... commit  refs/pstack/keep/run/1e894440...
21bcc218... commit  refs/pstack/keep/run/21bcc218...
d4111453... commit  refs/pstack/keep/run/d4111453...
receiver: all three evidence commits held by keep refs and survive gc after refs/pstack/in removed: OK
re-apply msg2 evidence lines: []
```

(Message 2 reported 1 new commit because it also re-carries c1 and c2, which the receiver already held.)

5. Acceptance 2, failure path: message 2 with its git part removed, applied to a second fresh receiver:

```
negative raises ValueError: evidence: 1e894440b was not carried and origin lacks it
receiver2 keep refs after failed apply: ''
$ pair.sh sync nogit.tgz
pair.sh: .../nogit.tgz: evidence: 1e894440b was not carried and origin lacks it
sync nogit exit=1
$ pair.sh sync msg2.tgz
synced: .../receiver-store from m
sync msg2 exit=0
```

6. Acceptance 1, driven through the real kitchen CLI (probe `v6probe/probe_landcheck.py`, run at the head and at the parent): `step`, `finish`, `verify 001` (status clean), then `reset --hard` to the base, a different commit, `reflog expire` and `gc --prune=now`, then `land-check`:

```
at the head:
VERIFY: ['status: clean']
OLD COMMITS GONE AFTER GC: 0 of 2
LAND-CHECK: (0, '... land-check: pass (review engine: none)')
at the parent:
VERIFY: ['status: clean']
OLD COMMITS GONE AFTER GC: 2 of 2
LAND-CHECK: (0, '... land-check: pass (review engine: none)')
```

The unit's step and report head stay reachable at the head and are collected at the parent; land-check passes at both because the unit has its own clean verdict (as before). The refs also arrive on the receiving side (Evidence 4).

7. Acceptance 3, driven through the real CLI with a variant the unit's tests do not use (probe `v6probe/probe_recover.py`): units 001 and 002 lose their heads, unit 003 is intact:

```
1) EXIT 2 OUTPUT:
unverified: unit 001: its evidence is missing (unit head: f03fb8b2cd7e...); restore it from the bundle that carried it, or prove its Acceptance again with kitchen.sh verify .../runs/demo 003 --covers 001 --base 1c5dba539d85...
unverified: unit 002: its evidence is missing (unit head: 3a49c0528302...); restore it from the bundle that carried it, or prove its Acceptance again with kitchen.sh verify .../runs/demo 003 --covers 002 --base 1c5dba539d85...
unverified: unit 003 has no clean verdict (kitchen.sh verify .../runs/demo 003)
2) VERIFY --covers 001: ['status: clean']
3) AFTER RECOVERING 001 (still exit 2):
unverified: unit 002: its evidence is missing (unit head: 3a49c0528302...); ...--covers 002 --base 1c5dba539d85...
4) AFTER RECOVERING BOTH (exit 0):
land-check: pass (review engine: none)
```

The `--base` value (1c5dba53...) is the repository's initial commit. The missing-head line goes to units with missing heads only, unit 003 with its heads present keeps `has no clean verdict`, and each recovery clears only its own unit. The unit's own table-driven test also covers a missing dispatch base and a missing landing-verdict head (Evidence 1 and 3).

8. Diff check against the mechanism (bug-fix step 3): `git.pin` is create-only (`update-ref <ref> <oid> ''`) so a ref is never moved; `step` pins after gates and policy pass, `finish` pins the report head, `verify_launch.pin_evidence` pins base, head and each unit's recorded head and base before the verifier starts; `bundle.pack` adds every `refs/pstack/keep/*` tip to the git bundle, cut against `origin_baseline`; `valid_keep` accepts an entry only when its name is `<run>/<its own full commit id>` and a valid ref name (Evidence 5 shows bad names skipped in the unit's test); `land_check` only changes the text of the problem line and keeps exit 2 and the coverage rules. Every changed line traces to one of the four Acceptance lines.

9. The nohup artifact behind the first gate's two failures:

```
$ nohup sh -c 'sh -c "kill -HUP \$\$"; echo "exit under nohup: $?"'
exit under nohup: 0
$ sh -c 'sh -c "kill -HUP \$\$"; echo "exit normal: $?"'
exit normal: 129
```

## Flows driven

- pstack (skills, the only profile touched): `kitchen.sh step`, `finish`, `verify`, `verify --covers` and `land-check` through the real CLI against the harness's fake herdr (Evidence 6 and 7); bundle `pack` and `apply` between two separate git repos, and `pair.sh sync` exit status on good and bad messages (Evidence 4 and 5).
- pstack-docs (docs/tests/SPEC.md): no user-facing flow; `spec_gen --check` and `check-pstack` pass (Evidence 2).

## Folded Acceptance

none

## Not covered

- A real Amp orb-to-orb hand-over (the actual `amp threads continue` transport). The bundle path was driven by file between two real repos; the transport itself is outside this unit.
- Retention of keep refs (nothing removes them); the report lists it as a follow-up and the brief puts it out of scope.
- With the git store (`PSTACK_STORE=git`) the evidence durability is out of scope, as the brief says.

## Lessons

none
