# Report 032: master-bundle-branches-fix2

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/032-master-bundle-branches-fix2.md
playbook: review-fix
branch: kitchen-followups
head: 08666a79896199e85d01238ecb5ce445d876b67a
tree: clean

## Changed

Started from unit 022's head ece5785 (fetched from origin kitchen-code/kitchen-followups/unit-022). Three commits, one per step; tests/SPEC.md regenerated in each. Files: skills/pstack-pair/scripts/pstack_core/bundle.py, git.py, tests/test_pstack_core.py, tests/SPEC.md. sync.py and tests/test_pstack_kitchen.py needed no change.

| step | commit | finding | pinned by (tests/test_pstack_core.py, BundleBranchApplyTests) |
| --- | --- | --- | --- |
| 1 | 7f33eb23a | pruning order and recipe refs | test_pruning_keeps_the_newest_sets_when_older_ids_are_bare_uuids_that_sort_above_the_new_ones, test_pruning_keeps_a_set_that_a_line_of_the_apply_names |
| 2 | 74e65ab99 | detached-sender recipe, misleading diverged line | test_a_detached_sender_gets_no_reset_recipe_for_the_receivers_branch_and_the_line_names_the_incoming_ref, test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here (extended) |
| 3 | 08666a798 | duplicate branch line, settings, git.run keywords | test_a_branch_the_checkout_is_on_is_reported_once_by_the_checkout_line, test_the_bundle_settings_are_checked_before_apply_changes_anything_and_a_zero_timeout_is_no_timeout |

- Step 1: `set_age(bundle_id)` orders sets: an id that is 20 digits, a dash and hex sorts by its clock; any other id (a bare uuid from before 031) is older than every clocked id. `prune_sets` sorts by it and never deletes a set whose `refs/pstack/in/<sender>/<id>/` prefix occurs in the lines of this apply (`printed`); move_git passes the lines it has built, which include every recipe and "kept as <ref>" mention.
- Step 2: `move_checkout` treats a checkout as elsewhere when it is on a branch other than the sender's, which now includes a detached sender (branch ''). An elsewhere checkout with commits of its own prints `checkout: kept <sha>: X has commits here that <sender> lacks; <sender> is detached at <sha> (kept as <ref>)` (or `is on Y at`) and no reset recipe. `diverged` takes the checkout's own HEAD (`kept`) as well as the diverged branch's tip, so when Y diverged while the checkout is on X the first line is `checkout: kept <X tip>: X stays, and branch Y here diverged from <sender> (...)`; the recipe is Y's, as before. A detached receiver is unchanged.
- Step 3: `move_git` hands `apply_branches` the sender's branch as `reported` when the checkout is on it after `move_checkout`, so that branch gets its checkout line only. `apply()` reads `PSTACK_BUNDLE_KEEP` first (a bad value raises BadNumber before the store, a ref or the checkout changes) and passes it to `move_git` and `prune_sets`. `origin_baseline` uses `env_int(...) or None`, so `PSTACK_BUNDLE_FETCH_TIMEOUT_S=0` is no timeout. `git.run` keeps `**extra` but raises TypeError for any key other than `timeout` and `env`; its docstring says `env` replaces the environment (the one caller merges os.environ). A keyword list in the signature would take it to 9 parameters, over the function-size guard's 8.

## Ran

Each step's tests fail without its fix (fix reverted in a scratch copy, restored after):

```
step 1: bundle.py at HEAD~  -> -k pruning_keeps:           2 tests, 2 failed
step 2: bundle.py at HEAD~  -> -k another_branch:          2 tests, 1 failed (the diverged-line test)
                              -k detached_sender:         1 tests, 1 failed
step 3: bundle.py, git.py at HEAD~ -> -k reported_once:    1 tests, 1 failed
                              -k bundle_settings_are_checked: 1 tests, 1 failed
```

At the head (08666a79896199e85d01238ecb5ce445d876b67a):

```
$ python3 tests/run.py -k bundle       -> 30 tests, 0 failed
$ python3 tests/run.py -k sync         -> 21 tests, 0 failed
$ python3 tests/run.py -k take_master  -> 11 tests, 0 failed
$ python3 tests/spec_gen.py --check    -> ok: tests/SPEC.md is current, 0 missing
```

The fast gates of pstack and pstack-docs (full tests/run.py, spec_gen --check, ruff format and check, uv check --frozen, check-pstack) passed at each recorded step: 1 at 7f33eb23a, 2 at 74e65ab99, 3 at 08666a798. `kitchen.sh finish` reruns the Verify block at this head.

## Self-check

figures: counts above are tails of the commands shown; gate results are the "step N recorded" lines.
acceptance: 004's and 031's Acceptance tests (every local branch arrives, role message carries none, cut against origin's default, one missing tip changes nothing, working branch created, no branch moved onto another's head, bounded fetch with tracking-ref fallback, pruning to N) all pass in -k bundle, -k sync and -k take_master. Each step has its failing-without-fix run above.
callers: `diverged`: one definition, 3 calls, all in move_checkout (grep). `apply_branches`: one caller (move_git). `prune_sets`: one caller (move_git) plus its tests. `move_git`: one caller (apply). `git.run` and `git.ok`: callers pass no keyword other than timeout and env beyond the existing ones (grep of `git.run(` and `git.ok(` for `**`/unknown keys: none; the TypeError would show in the full suite).
fresh: gates run in this checkout with no cache; -k runs are fresh processes.
gate: ruff format --check, ruff check, uv check --frozen, function-size guard and spec_gen --check pass at the head. L012 held (uv check --frozen before each step; `clock`/`partition` results used without Optional).

## Deviations

- Steps 2 and 3 were developed in a scratch git worktree while the previous step's gate ran (about 13 to 15 minutes each), then fast-forwarded into this checkout and recorded in order; the worktree and branch are removed.
- Step 3's brief asks for one table-driven test over the two env settings plus one test for the branch line: the timeout settings (0 and 7) are table-driven in one test together with the KEEP validation check (an inline case), because both need the same hand-over fixture.
- The timebox of 75 minutes passed during the gate runs.

## Questions

none

## Follow-ups

- The joo lows left out of the brief stay open (rejected hand-over's import refs, SIGKILL on fetch timeout, stale-ref cut, receiving-side fetch timeouts).
- `PSTACK_BUNDLE_KEEP` and `PSTACK_BUNDLE_FETCH_TIMEOUT_S` are still undocumented (SKILL.md and references are outside this brief's Scope).
