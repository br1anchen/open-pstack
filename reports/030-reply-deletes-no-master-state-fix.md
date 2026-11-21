# Report 030: reply-deletes-no-master-state-fix

status: done
brief: briefs/030-reply-deletes-no-master-state-fix.md
playbook: review-fix
branch: kitchen-followups
head: b10896e56cc1c5c5ed19ed8b0681926f75abb8fe
tree: clean

## Changed

Base 550a7d7 (unit 004 head, per the brief), two commits:

```
 skills/pstack-kitchen/SKILL.md                    |  2 +-
 skills/pstack-pair/scripts/pstack_core/bundle.py  | 21 +++++++++++++++++++--
 skills/pstack-pair/scripts/pstack_core/mux/amp.py | 23 ++++++++++++++++-------
 tests/SPEC.md                                     |  8 ++++----
 tests/test_pstack_core.py                         |  6 +++++-
 tests/test_pstack_kitchen.py                      | 14 ++++++++++++++
```

- 527c8f9 (step 1): `mux/amp.py` `thread_context` memoizes in `CONTEXT_MEMO` per thread for the life of the process (one command); `deliver` drops the thread's entry before it sends. The three readers (`session_decision`'s `m.context`, `playbook_delivery`'s `m.compactions`, `send`'s `thread_compactions`) all go through `thread_context`, so `playbook_delivery.py` needed no change. A new rotated thread has a new id, so its first read is fresh.
- b10896e (step 2): `bundle.py` `copy_owner(rel)` and `merge_store` list kept files per owner: `kept the master's copy of: ...` for master-owned paths and `.seen` markers, `kept <role>'s copy of: ...` (roles from `gitstore.RULES`, e.g. `sidekick`, `sidekick/verifier`) for another role's files. SKILL.md sentence updated.

## Ran

Finding 1 (verdict 025-v1 E5), test `AmpKitchenTests.test_a_dispatch_that_keeps_the_sidekick_reads_its_context_once`: after one finished unit, one routine dispatch that keeps the sidekick adds exactly 1 `threads context <sidekick thread>` line to the fake's amp.log.

```
$ python3 tests/run.py -k keeps_the_sidekick                (with the memo)
3 tests, 0 failed, 7s wall with 4 jobs
$ git checkout skills; python3 tests/run.py -k reads_its_context_once   (memo removed, test kept)
1 tests, 1 failed, 4s wall with 4 jobs
failed: test_pstack_kitchen.AmpKitchenTests.test_a_dispatch_that_keeps_the_sidekick_reads_its_context_once
```

Finding 2 (joo 025 lows), test `RoleReplyMergeTests.test_a_verifiers_stale_copy_...` now asserts the sync line:

```
[store: 1 updated, 0 removed; kept sidekick's copy of: reports/001-a.md]
$ python3 tests/run.py -k RoleReplyMerge
17 tests, 0 failed, 1s wall with 4 jobs
```
The existing master-file test still asserts the `kept the master's copy of: ...` line unchanged.

Gates through `kitchen.sh step`: step 1 `gates: pass (pstack,pstack-docs)`, step 2 `gates: pass (pstack,pstack-docs)`.

Brief Verify commands: run by `finish` at head (output below its own lines).

Also run before committing: `uv run --frozen ruff format skills tests` (clean), `ruff check` (All checks passed), `uv check --frozen` (All checks passed), `python3 tests/spec_gen.py --check` (ok: current, 0 missing; SPEC.md regenerated in each commit).

## Self-check

figures: every value above comes from the commands shown or the step output
acceptance: one dispatch reads the sidekick's context once -> the keeps_the_sidekick tests above, fails without the memo; the sync line names whose copy it kept -> RoleReplyMerge; reply preservation, the two-orb regression (reports/002, steps/002) and the `reason:manual` rotate event are unit 025's tests, unchanged, run by `-k BundleMerge` and `-k rotat`; goldens: `git diff 550a7d7 -- tests/pstack-pair/golden` is empty (not touched)
callers: changed shared symbol `thread_context` is called by `thread_compactions` (from `Amp.compactions`, `record_given`, `Amp.send`) and `Amp.context`; `grep -rn "thread_context\|thread_compactions" skills` shows no other caller. `CONTEXT_MEMO` is read only in `thread_context` and popped only in `deliver`. The memo lives for the process, i.e. one kitchen.sh command; the one writer of a thread's context within a command is `deliver`, which pops. Other callers of `merge_store`'s refused line: only `bundle.apply`; the only test asserting the line text are the two above.
fresh: new tests ran in fresh test processes; no stale cache
gate: ruff format/check and uv check --frozen pass on the head; step gates passed for both commits. `kitchen.toml` has no `review.self`; the master runs joo on the unit.

## Deviations

none. L012 (Optional check): `thread_context` still returns None for unparseable output, and every caller already checks for None (unchanged behaviour); `uv check --frozen` passes.
