# Report 004: master-bundle-branches

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/004-master-bundle-branches.md
playbook: bug-fix
branch: kitchen-followups
head: 550a7d760223b1e44d1ba85bbd0ba2203be9f353
tree: clean

## Changed

Four commits on top of unit 025's head 6a83f50 (steps 1, 2, a test-only fixup for step 2, step 3):

- f66ec5369 hand-over bundle carries every local branch (bundle.py, mux/amp.py, kitchen test)
- 4709c7dc1 + 58f2261fb apply hand-over branches conservatively (bundle.py, core and kitchen tests; the second commit only adds `timeout=` to my new subprocess calls, which test_every_subprocess_call_in_the_test_modules_is_bounded required)
- 550a7d760 diverged checkout gets a guarded recovery recipe (bundle.py, core tests)
- tests/SPEC.md regenerated in each commit. Files touched: skills/pstack-pair/scripts/pstack_core/bundle.py, mux/amp.py, tests/test_pstack_core.py, tests/test_pstack_kitchen.py, tests/SPEC.md. commands/sync.py and kitchen_core/master.py needed no change (sync already turns a ValueError from apply into a non-zero exit).

## Ran

Steps recorded by `kitchen.sh step`: 1 at f66ec5369, 2 at 58f2261fb, 3 at 550a7d760 (fast gates of pstack and pstack-docs, policy clean; the first step-2 run failed `tests/run.py` on the unbounded subprocess guard, fixed in 58f2261fb).

Local checks at the head:

```
$ python3 tests/run.py -k bundle         -> 27 tests, 0 failed
$ python3 tests/run.py -k take_master    -> 11 tests, 0 failed
$ python3 tests/run.py -k rotate_master  -> 13 tests, 0 failed
$ python3 tests/spec_gen.py --check      -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check / ruff check / uv check --frozen -> all pass
```

Mutations (each makes a new test fail, then reverted): `exclude = ('--remotes',)` in hand-over mode (baseline test), dropping the checked-out-worktree rule, dropping the local-ahead rule, dropping the recipe's clean-tree guard, dropping the recovery-branch step.

## Self-check

figures: every count above is the tail of the command shown
acceptance:
- every local branch arrives at the same tip after rotate-master, sync and take-master, two unpushed non-HEAD branches, one `feat/one`: test_the_next_master_gets_every_local_branch_at_the_same_tip_after_sync_and_take_master, and test_a_hand_over_bundle_carries_every_local_branch_and_a_role_message_does_not (the manifest's git.branches and the bundle's `refs/pstack/out/br/<name>` refs)
- a role message carries no extra branches: the same test (its manifest has no `branches`, the bundle has no `br/` refs)
- ahead, divergent and worktree-occupied branches are left unchanged and named; absent is created, an ancestor one fast-forwarded; a second apply changes nothing: BundleBranchApplyTests.test_each_kind_of_receiver_branch_of_a_hand_over_bundle_is_created_moved_or_kept_and_a_second_apply_changes_nothing (the occupied worktree's HEAD is also checked)
- a sync onto a checkout a role rebased leaves HEAD and the branch unchanged and prints the recipe with full OIDs and `kitchen-recovery/<old-oid>`; the recipe then moves it, refuses on a tracked change, and refuses on a second run: BundleRebasedCheckoutTests (two tests: the checked-out branch, and a diverged branch that is not checked out)
- a failed object import exits non-zero: test_a_hand_over_bundle_whose_commits_cannot_be_imported_makes_sync_exit_non_zero (sync exits 1) and the core test that apply raises ValueError and creates no ref
callers: `bundle.pack` (mux/amp.py with_bundle, and the relay path, which keeps the default `branches=False`), `git_bundle` (pack only), `move_checkout` (apply_git only; signature gained `ref`), `fetch` (apply_git only). Search: `grep -rn "move_checkout\|bundle.fetch\|git_bundle(\|apply_git\|\.pack(" skills` finds only bundle.py and the two pack sites in mux/amp.py. `refs/pstack/in/` is read nowhere else.
fresh: the full `python3 tests/run.py` ran in each step's gate, not on cached results
gate: format, lint and `uv check --frozen` pass at the head (lesson L012: the Optionals bundle.py reads from git calls are checked; uv check --frozen ran before each step)

## Deviations

- Every bundle's tips now live under `refs/pstack/in/<sender tag>/<bundle id>/`, ordinary role messages included, not only hand-overs. The brief asked for per-bundle side refs for the branch case; one rule is simpler than two, and it makes the diverged recipe's "incoming ref" durable too. Cost: a few refs per synced message, never deleted.
- A hand-over's baseline is origin's default branch after a fresh `git fetch origin` on the sender, recorded as `git.baseline` in the manifest; with no fetchable default branch the bundle is self-contained (tested both ways: no origin, and origin plus a sender-only remote).
- A tip the sender's bundle omitted because origin's default branch already holds it is pinned at its side ref by the receiver, so recipes and kept-branch lines can always name a durable ref.
- Strictness (raise on a missing commit) applies only to a bundle whose manifest has `git.branches`, i.e. a hand-over; an ordinary message still prints `commits: the git bundle did not apply` and goes on.
- The sender's own HEAD branch is left to `move_checkout`, as the brief says; if the receiver is on a different branch, the old `checkout -B` behaviour still applies.

## Questions

none

## Follow-ups

- This orb's installed build (6aaa6770ea89df2b) cannot run `finish` or `notify` as a role thread under the git store: `record_skill_build` (kitchen_core/setup.py) writes a skill_builds row to pair.json, which belongs to the master, so it fails with an ownership error. I ran the repo checkout's `skills/pstack-kitchen/scripts/kitchen.sh` instead (already the last stamped build). Worth its own unit: a role thread should not stamp pair.json.
- Branches a role orb created and abandoned there are not carried (as the advice's options table says); a master hand-over carries the master's own branches only.
- The recipe is not run by any command; automatic rebase adoption would need the separate provenance-based design the advice describes.
