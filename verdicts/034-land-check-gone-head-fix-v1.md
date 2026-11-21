# Verdict 034: land-check-gone-head-fix

status: reject
units: 006 034
range: 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb..70ed949c28672d61c83073bc0f16d2878ebe4e18
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

Every Acceptance line of 006 and 034 holds (Evidence 1 to 6). One regression of the behaviour step 2 specifies:

1. Brief 034 step 2 says an ordinary message whose evidence commit is missing applies, "print[s] one line naming the evidence ref it could not keep, and go[es] on". At the head it prints two lines for the one ref: the `evidence: not kept, the commit was not carried and origin lacks it: run1/<oid>` line, and on stderr `warn: could not keep <oid> under refs/pstack/keep/run1/<oid>`. The second line comes from step 4 (70ed949c): `git.pin` now warns when it cannot make a ref, and `move_git` → `keep_evidence` still calls `git.pin` for every evidence entry, including the one `unkept_evidence` already reported as missing. The report's Follow-ups say this warn "should be rare", but it fires on exactly this path, every time. With only step 4's `git.pin` hunk reversed, the same test prints no warn line (Evidence 7). The step 2 test asserts only the `evidence:` lines of `apply`'s result and never reads stderr, so the suite does not catch it. Smallest fix: `keep_evidence` pins only the entries whose commit is here (`has_commit`), and the step 2 test asserts that stderr is empty.

Notes, none of them findings:
- Step 3's carrier rule cannot show a carrier when the lost unit's own head is gone (`carries_change` needs the lost head's commits). The line then says "no other accepted unit's head carries its change" and leaves `<current-unit>`/`<live-base>` as placeholders (the test's first case). The brief asks for that ("with none, say so"), but the wording claims more than land-check can know: a later unit may hold a rebased copy that no longer resolves to show it. "none can be shown to carry its change" would be exact.
- Step 1 cuts an ordinary send against `--remotes` again, as the brief asks. A pinned commit that only a sender-side remote holds is then not carried, and the receiver gets it only from its own `git fetch origin` on a miss. That is the pre-006 trade-off the brief chose.
- `unkept_evidence` runs `git fetch -q origin` with no timeout and no `GIT_TERMINAL_PROMPT=0`, unlike `origin_baseline`. `require_tips` already did the same for hand-overs. It runs only when an evidence commit is missing.
- The step 3 test dropped its precondition assert that the gone head is really gone. Its expected `unit head: <gone_head>` line still shows that.
- Standing test check: the range adds no sleep or elapsed-time assert. The new tests start no subprocess without a bound (the one `subprocess.run` they record is the code's own git calls, with `timeout=60` in the fixture). They add no mask or normalisation. Each source change sits in the same commit as its test, and `spec_gen --check` is current.
- Outside this unit, for the master: on this git-backed store, `PSTACK_MUX=amp kitchen.sh notify <store> <verdict>` from the prompt's installed build (`~/.local/share/pstack-builds/6aaa6770ea89df2b`) exits 1 with `pair.json: belongs to the master (...)`. The kitchen's `startup` hook (`record_skill_build`) appends a `skill_builds` row whenever the caller's skills directory differs from the last one stamped (`/home/user/workspace/repo/skills`), and `store.update` refuses that write for a role thread. Every role thread whose build path differs from the master's last stamp hits this. I sent this verdict with this checkout's `skills/pstack-kitchen/scripts/kitchen.sh`, whose path matches the stamp, so nothing is written.
- This orb had no scratch for the unit. I made it with `git worktree add --detach <scratch> 70ed949c` (every commit arrived by sync) and ran the packet's `setup` (`setup: ok (1 commands ...)`, exit 0). `PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups` was exported throughout. A second worktree at the head (`034-revert`) held the scratch reverts and was restored after each one.

## Evidence

1. Brief commands (006 and 034), each in a fresh shell from the scratch root, plus the repo's other pre-commit checks:

```
$ python3 tests/run.py -k land_check
19 tests, 0 failed, 18s wall with 4 jobs
exit 0
$ python3 tests/run.py -k bundle
30 tests, 0 failed, 24s wall with 4 jobs
exit 0
$ python3 tests/run.py -k evidence
9 tests, 0 failed, 5s wall with 4 jobs
exit 0
$ python3 tests/run.py -k sync
22 tests, 0 failed, 17s wall with 4 jobs
exit 0
$ python3 tests/run.py -k land
90 tests, 0 failed, 66s wall with 4 jobs
exit 0
$ python3 tests/run.py -k verify
86 tests, 0 failed, 77s wall with 4 jobs
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
Resolved 3 packages in 0.75ms
exit 0
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
exit 0
```

2. Brief 034 command 4, the full suite at the head:

```
$ python3 tests/run.py
2010 tests, 0 failed, 710s wall with 4 jobs
exit=0
```

3. Behavioral gate from the Prove block:

```
$ python3 kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb
pstack behavioral: pass
  exit 0 533.9s python3 tests/run.py  log: .../repos/repo-f328a3c3/logs/20261121T121203/pstack-behavioral-0.log
(log tail) 2010 tests, 0 failed, 534s wall with 4 jobs
exit=0
```

4. Acceptance 034-1, each step's tests with that commit's source hunks reversed on the head (`git diff <c>^ <c> -- <source> | git apply -R` in the second worktree), then restored (per-test timing lines omitted):

```
== step c3bce56d: reverse bundle.py (7 insertions(+), 16 deletions(-))
-- reverted: -k carries_only_its_own
1 tests, 1 failed
failed: test_pstack_core.BundleBranchApplyTests.test_an_ordinary_send_carries_only_its_own_runs_evidence_and_fetches_nothing
-- head: -k carries_only_its_own
1 tests, 0 failed
== step 6598293c: reverse bundle.py (5 insertions(+), 24 deletions(-))
-- reverted: -k evidence_commit_is_missing
1 tests, 1 failed
failed: test_pstack_core.BundleBranchApplyTests.test_an_ordinary_message_whose_evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref
-- reverted: -k hand_over_whose_evidence        (the strict hand-over rule, unchanged by the step)
1 tests, 0 failed
-- head: -k evidence_commit_is_missing  -> 1 tests, 0 failed
-- head: -k hand_over_whose_evidence    -> 1 tests, 0 failed
== step 868a0093: reverse landing.py (12 insertions(+), 54 deletions(-))
-- reverted: -k names_only_the_landing
1 tests, 1 failed
failed: test_pstack_kitchen.KitchenScriptTests.test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit
-- head: -k names_only_the_landing
1 tests, 0 failed
== step 70ed949c: reverse git.py, verify.py, verify_launch.py
-- reverted: -k pin_that_cannot
1 tests, 1 failed
failed: test_pstack_core.BundleBranchApplyTests.test_a_pin_that_cannot_be_made_says_so_in_one_warn_line_and_a_good_one_is_silent
-- reverted: -k gates_mode_verifies
1 tests, 1 failed
failed: test_pstack_kitchen.KitchenScriptTests.test_gates_mode_verifies_without_a_verifier
-- head: -k pin_that_cannot      -> 1 tests, 0 failed
-- head: -k gates_mode_verifies  -> 1 tests, 0 failed
```

5. Acceptance 006-2 and 034 step 1, driven with real git on two separate repos joined only by bundle files (probe `.amp/in/orb-backup/v034/probe_bundle.py` on the verifier orb, run with the head's `pstack_core`). The sender has a reachable `origin` holding only the base. It pins c1 under run1 and another commit under `other-run`, and packs message 1. Then it rewrites its branch and runs `gc --prune=now`, pins c2 and packs message 2. The receiver is a fresh `--no-local` clone of origin at the same path. `GIT_TRACE` records every git call of each pack:

```
msg1: keep=['run1/5dd70e7c...'] baseline=False fetch calls=0
sender after rewrite+gc: c1 held=True other held=True
msg2: keep=['run1/5dd70e7c...', 'run1/c0d5405d...'] baseline=False fetch calls=0
receiver before: c1=False c2=False other=False
apply msg1.tgz: ['evidence: 1 commits kept under refs/pstack/keep']
apply msg2.tgz: ['evidence: 1 commits kept under refs/pstack/keep']
receiver keep refs after gc:
refs/pstack/keep/run1/5dd70e7ceb2879d0b47e4e02ef9a549a0d4c06b1
refs/pstack/keep/run1/c0d5405d40df505e3637ec9b92bdb7e6745561e4
receiver after gc: c1=True c2=True other=False
```

Control (`probe_control.py`): a hand-over pack (`branches=True`) of the same sender, under the same trace, does fetch. So a fetch would have shown in the ordinary packs' traces:

```
hand-over pack: git calls traced = 20 ; fetch calls = 1
(msg1's trace: 9 git calls, 0 fetch)
```

6. Acceptance 006-1, 006-3 and 006-4 at the head. These tests are in Evidence 1's `-k evidence` (9, 0 failed) and `-k land_check` (19, 0 failed) runs:
- `test_evidence_commits_of_a_step_a_finish_and_a_verification_survive_a_rewrite_of_the_branch_and_gc` (006-1).
- `test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit` asserts exit 2 with `its evidence is missing (unit head: …)`, `(dispatch base: …)` and `(landing verdict head: …)`, each with its `--covers` command. `test_the_recovery_command_land_check_prints_proves_the_lost_unit_again_and_clears_it` runs the printed `kitchen.sh verify <store> 002 --covers 001 --base <init>` and gets `status: clean`, and land-check then prints `land-check: pass` with exit 0 (006-3).
- In the range, the `tests/test_pstack_kitchen.py` hunks touch only the gates-mode test (L6911) and 006's own evidence tests (L12852 on). `git diff 1e105784..70ed949c` removes no `def test_` other than 006's own renamed test. So the rebased, revert and landed tests are unchanged and pass (006-4).
- The real landing packet writes `### Unit {nnn} {slug}` (verify_packet.py:381). That is the heading `packet_lists_unit` matches with `^### Unit {seq} `, so the new blame rule reads real packets, not only the test's.

7. Finding 1: the step 2 test at the head, run by itself so its stderr shows. Then the same run with only step 4's `git.pin` hunk reversed (second worktree, restored after):

```
$ cd tests && python3 -m unittest test_pstack_core.BundleBranchApplyTests.test_an_ordinary_message_whose_evidence_commit_is_missing_still_syncs_its_store_and_names_the_ref
-- head:
warn: could not keep 1ce024add562269c338fafe0c38309a10cb5d6f7 under refs/pstack/keep/run1/1ce024add562269c338fafe0c38309a10cb5d6f7
.
----------------------------------------------------------------------
-- step 4's git.pin warn reversed:
.
----------------------------------------------------------------------
Ran 1 test in 0.167s
```

The test passes in both runs. It asserts that the `evidence:` lines of `apply` are exactly `['evidence: not kept, the commit was not carried and origin lacks it: run1/<oid>']`, and it never reads stderr.

## Flows driven

- pstack: the bundle send and sync between two separate repos (Evidence 5: pack, rewrite and gc, a fresh receiver, apply, gc), the ordinary missing-evidence sync (Evidence 7), land-check and the `--covers` recovery through `kitchen.sh` against the fake herdr in the suite (Evidence 6), and the behavioral gate (Evidence 3).
- pstack-docs: brief 034 touches no docs path. `check-pstack.py` and `spec_gen --check` pass (Evidence 1).

## Folded Acceptance

none

## Not covered

- I drove land-check's missing-evidence and recovery path only through the suite's `kitchen.sh` tests against the fake herdr, not on a live store, because the repo allows `kitchen.sh` only against the fake herdr.
- An unreachable or hanging `origin` during `unkept_evidence`'s fetch was not driven (see Notes).

## Lessons

L012: held (`uv check --frozen`: All checks passed!, Evidence 1).
