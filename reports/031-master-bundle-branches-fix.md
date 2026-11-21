# Report 031: master-bundle-branches-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/031-master-bundle-branches-fix.md
playbook: review-fix
branch: kitchen-followups
head: 6dcc8e38a288ade2e10d725d007d811dbc7f201d
tree: clean

## Changed

Started from unit 030's head b10896e (fetched from origin kitchen-code/kitchen-followups/unit-030). Six commits, steps 1 to 5 plus one gate fix; tests/SPEC.md regenerated in each. Files: skills/pstack-pair/scripts/pstack_core/bundle.py, git.py, tests/test_pstack_core.py, tests/SPEC.md. sync.py and mux/amp.py needed no change.

| step | commit | finding | pinned by (tests/test_pstack_core.py, BundleBranchApplyTests) |
| --- | --- | --- | --- |
| 1 | b18310c86 | all or nothing | test_a_hand_over_with_one_missing_branch_tip_changes_no_ref_no_checkout_and_no_store_file |
| 2 | 6536f9ba3 | sender's working branch dropped | test_the_senders_working_branch_is_created_here_when_the_checkout_kept_its_own_state |
| 3 | 8fa4fa15d | recipe moves one branch onto another's head | test_a_checkout_on_another_branch_with_its_own_commits_stays_put_and_the_senders_branch_is_created, test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here |
| 4 | 6cbfaf78d + 5647fa09d | bundle unbounded when origin is down | test_a_hand_over_bundle_is_cut_at_the_tracking_ref_when_origin_cannot_be_fetched, test_the_origin_fetch_of_a_hand_over_bundle_has_no_prompt_a_timeout_and_a_timed_out_one_falls_back_to_the_tracking_ref |
| 5 | 6dcc8e38a | ref sets never pruned | test_older_ref_sets_of_one_sender_are_pruned_to_the_newest_few_and_the_applied_one_stays |

- Step 1: apply() split apply_git into import_git (fetches the bundle, then checks HEAD and every branch tip resolve, raising ValueError with nothing changed) and move_git (checkout, branches, worktrees). apply() runs import_git before os.makedirs and merge_store, so no store file, ref or checkout changes on a missing tip. apply_git is gone; the one test that called it now rewrites the bundle's manifest (new helper BundleRepoCase.with_manifest) and calls apply.
- Step 2: apply_branches lost its name == current skip and its current parameter. A branch that is checked out here is still reported kept by the held-in-a-worktree rule; one the checkout moved to has local == oid and is skipped.
- Step 3: move_checkout compares the receiver's branch with the sender's branch. A receiver on another branch with commits of its own is left where it is (line "kept <sha>: X has commits here that <sender> lacks; <sender> is on Y at <sha>"), and Y is handled by the branch rules (apply_branches creates or fast-forwards it; a Y that diverged here gets the recipe checkout -q -B Y <new> from move_checkout). A detached receiver or a detached sender keep the old behaviour. Y's old head resolves as refs/heads/<Y> in both checks and the recipe's old value.
- Step 4: origin_baseline runs git fetch origin with GIT_TERMINAL_PROMPT=0 and a timeout (env knob PSTACK_BUNDLE_FETCH_TIMEOUT_S, default 30, through numbers.env_int) and ignores its result: the existing lookup of refs/remotes/origin/HEAD, origin/main, origin/master is the cut, and '' (self-contained bundle) only when none exists. git.run and git.ok forward **popen to subprocess.run and report TIMED_OUT (124) for an expired call.
- Step 5: apply deletes older refs/pstack/in/<sender>/<id>/ sets, keeping PSTACK_BUNDLE_KEEP (default 8) in all, always including the one being applied. "Newest" needed an order the receiver can read from the refs, so a bundle id is now <time_ns, 20 digits>-<12 hex of a uuid4> (the sender's clock) instead of a bare uuid4 hex; the id is only used as a ref directory name and a manifest field. Every ref a printed line or recipe of an apply names is under that apply's own set, which is never pruned.

## Ran

Each new test fails without its fix (the fix reverted in a scratch copy, then restored):

```
step 1: git checkout HEAD~ -- bundle.py ; python3 tests/run.py -k one_missing_branch_tip
  1 tests, 1 failed   (the refs/heads comparison differs: main moved before the raise)
step 2: -k senders_working_branch               -> 1 tests, 1 failed
step 3: -k another_branch                       -> 2 tests, 2 failed
step 4: -k tracking_ref_when_origin             -> 1 tests, 1 failed
        -k origin_fetch_of_a_hand_over          -> 1 tests, 1 failed
step 5: the prune_sets(root, side) call removed -> -k pruned_to_the_newest: 1 tests, 1 failed (AssertionError: sets 0..4 remain)
```

At the head (6dcc8e38a288ade2e10d725d007d811dbc7f201d):

```
$ python3 tests/run.py -k bundle        -> 29 tests, 0 failed
$ python3 tests/run.py -k sync          -> 21 tests, 0 failed
$ python3 tests/run.py -k take_master   -> 11 tests, 0 failed
```

The fast gates of pstack and pstack-docs (kitchen.sh step; the full tests/run.py, spec_gen --check, ruff format and check, uv check --frozen, uv lock --check) passed at each recorded step: 1 at b18310c86, 2 at 6536f9ba3, 3 at 8fa4fa15d, 4 at 5647fa09d, 5 at 6dcc8e38a. kitchen.sh finish reruns the Verify block at this head; its result is the line it prints to the master.

## Self-check

figures: counts above are the tail of the commands shown; the gate results are the "step N recorded" lines of kitchen.sh step.
acceptance: each of steps 1 to 5 has a test and a revert showing it fail (Ran, first block); 004's Acceptance tests (test_the_next_master_gets_every_local_branch_at_the_same_tip_after_sync_and_take_master, test_a_hand_over_bundle_carries_every_local_branch_and_a_role_message_does_not, test_a_hand_over_bundle_is_cut_against_origins_default_branch_not_the_remotes_this_side_has) and every bundle, sync and take_master test pass (Ran, second block).
callers: apply_git (removed): grep found one caller, the test I changed. apply_branches: one caller, move_git. git.run, git.ok: callers pass no new arguments, so unchanged. Bundle id format: grep for the manifest's id found only side_of (ref directory) and test code; no parser of the old uuid form.
fresh: the gates run in this checkout with no cache; the targeted -k runs are fresh processes.
gate: ruff format --check, ruff check, uv check --frozen and the function-size guard pass on the head; step 4's first gate failed the size guard (git.run took 9 parameters, limit 8), fixed in 5647fa09d.
lessons: L012 held (uv check --frozen run before each step; no Optional from a stdlib call went unchecked).

## Deviations

- Steps 3 to 5 were written in a scratch git worktree while the previous step's gate ran (a gate takes about 13 minutes), then fast-forwarded into this checkout one commit at a time and recorded in order, so each step's gate ran at its own commit. The worktree and its branch are removed.
- Step 4 needed a second commit (5647fa09d) because its first gate failed the function-size guard; step 5 was rebased on it.
- Step 5 changed the bundle id format (see above) to give "newest" a meaning without local state. The alternative, a per-sender index file in the git dir, was rejected as extra state to keep consistent.
- The timebox of 90 minutes passed during the last gate runs; every gate at 13 minutes each accounts for most of it.

## Questions

none

## Follow-ups

- PSTACK_BUNDLE_FETCH_TIMEOUT_S and PSTACK_BUNDLE_KEEP are not in the skills' docs (SKILL.md and references are outside this brief's scope).
- The receiving side's fetches (fetch() fallback, import_git's origin fetches) still run unbounded and with prompts enabled; only the pack side was in scope.
- The new test for step 4's timeout patches subprocess.run (no real hang, no sleep).
