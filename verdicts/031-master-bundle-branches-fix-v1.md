# Verdict 031: master-bundle-branches-fix

status: clean
units: 004 031
range: 6a83f500bc5d5ed07276c49035859a9b8322e879..6dcc8e38a288ade2e10d725d007d811dbc7f201d
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Observations, not findings (no Acceptance line rests on them):

1. Step 1, a failed hand-over leaves its bundle-import refs behind. `import_git` fetches the git bundle into `refs/pstack/in/<sender>/<id>/` and only then checks the branch tips. The raise therefore leaves that one ref set (the brief's "raise with nothing changed" holds for branches, HEAD, the checkout and the store files; the test compares `refs/heads` only). The refs only keep the sender's commits reachable, and a later successful apply prunes them with the rest (Evidence A).
2. Step 4: the fetch timeout kills `git fetch` but not the transport it started. With a hanging ssh stand-in, the `sh -c 'sleep 40; :'` children outlived the 2 s timeout and ran on as orphans, though pack had returned in 2 s (Evidence C). A real ssh would likewise linger until its own connect timeout. Nothing waits on them, because ok() sends stdout and stderr to DEVNULL.
3. Step 5: ids from before this unit (bare uuid4 hex) sort by text against the new `<time_ns>-<hex>` ids, so after an upgrade one sender's old sets can count as "newest" for a while. The number kept is still capped at PSTACK_BUNDLE_KEEP.
4. 5647fa0 (step 4's size-guard fix) changes source with no test file. It reshapes git.run's signature only; 6cbfaf7's tests pin the behaviour and pass at the head.

## Evidence

### Brief commands at 6dcc8e38a (fresh shell each, from the scratch root, PSTACK_KITCHEN_ROLE=verifier)

```
$ python3 tests/run.py -k bundle          -> exit 0: 29 tests, 0 failed, 23s wall with 4 jobs
$ python3 tests/run.py -k sync            -> exit 0: 21 tests, 0 failed, 26s wall with 4 jobs
$ python3 tests/run.py -k take_master     -> exit 0: 11 tests, 0 failed, 35s wall with 4 jobs
$ python3 tests/run.py -k rotate_master   -> exit 0: 13 tests, 0 failed, 34s wall with 4 jobs   (unit 004's Command 3)
$ python3 tests/spec_gen.py --check       -> exit 0: ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check     -> exit 0: 111 files already formatted
$ uv run --frozen ruff check              -> exit 0: All checks passed!
$ uv check --frozen                       -> exit 0: All checks passed!
$ python3 tests/run.py                    -> exit 0: 1993 tests, 0 failed, 626s wall with 4 jobs
```

### Prove gate

```
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> setup
setup: ok (1 commands)
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 6a83f500bc5d5ed07276c49035859a9b8322e879
pstack behavioral: pass
  exit 0 570.7s python3 tests/run.py
```

### Scratch reverts: each step's test fails without its fix (separate worktrees at each step's commit, only the source files that commit changed taken back to its parent, tests kept; script .amp/in/orb-backup/v031/reverts.sh)

```
== step1: at b18310c, source reverted to b10896e: bundle.py
1 tests, 1 failed  failed: ...test_a_hand_over_with_one_missing_branch_tip_changes_no_ref_no_checkout_and_no_store_file
-- with the fix: 1 tests, 0 failed
== step2: at 6536f9b, source reverted to b18310c: bundle.py
1 tests, 1 failed  failed: ...test_the_senders_working_branch_is_created_here_when_the_checkout_kept_its_own_state
-- with the fix: 1 tests, 0 failed
== step3: at 8fa4fa1, source reverted to 6536f9b: bundle.py
2 tests, 2 failed  failed: ...test_a_checkout_on_another_branch_with_its_own_commits_stays_put_and_the_senders_branch_is_created
                   failed: ...test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here
-- with the fix: 2 tests, 0 failed
== step4: at 5647fa0, source reverted to 8fa4fa1: bundle.py git.py   (-k origin)
25 tests, 2 failed  failed: ...test_a_hand_over_bundle_is_cut_at_the_tracking_ref_when_origin_cannot_be_fetched
                    failed: ...test_the_origin_fetch_of_a_hand_over_bundle_has_no_prompt_a_timeout_and_a_timed_out_one_falls_back_to_the_tracking_ref
-- with the fix: 25 tests, 0 failed
== step5: at 6dcc8e3, source reverted to 5647fa0: bundle.py
1 tests, 1 failed  failed: ...test_older_ref_sets_of_one_sender_are_pruned_to_the_newest_few_and_the_applied_one_stays
-- with the fix: 1 tests, 0 failed
== step5b: at 6dcc8e3 with only the prune_sets(root, side) call removed (id format kept)
 bundle.py | 1 -
1 tests, 1 failed  failed: ...test_older_ref_sets_of_one_sender_are_pruned_to_the_newest_few_and_the_applied_one_stays
```

Commit layout (review-fix step 2): each finding's commit carries its fix and test together.

```
b18310c bundle.py tests/SPEC.md tests/test_pstack_core.py   (step 1)
6536f9b bundle.py tests/SPEC.md tests/test_pstack_core.py   (step 2)
8fa4fa1 bundle.py tests/SPEC.md tests/test_pstack_core.py   (step 3)
6cbfaf7 bundle.py git.py tests/SPEC.md tests/test_pstack_core.py (step 4)
5647fa0 bundle.py git.py                                    (step 4 size-guard reshape, no new behaviour)
6dcc8e3 bundle.py tests/SPEC.md tests/test_pstack_core.py   (step 5)
```

### A. Step 1 through `pair.sh sync`: a hand-over with one missing tip (script A-missing-tip.sh; sender and receiver at one path, receiver a commit behind, without feat/a and without the store's reports/001.md, objects pruned)

```
--- before
refs/heads/main 994a54c
994a54c
pair.sh: /tmp/v031sync.FE0z/b.tgz: branch zzz/missing: fffffffff was not carried and origin lacks it
sync exit=1
--- after
refs/heads/main 994a54c
refs/pstack/in/T-sender/01795245796084152128-64760d5b5060/br/feat/a 93c48be
refs/pstack/in/T-sender/01795245796084152128-64760d5b5060/br/main 57fb1c9
refs/pstack/in/T-sender/01795245796084152128-64760d5b5060/head 57fb1c9
994a54c
```
(`ls .st/reports` prints nothing before or after: the store file was not written; feat/a was not created; HEAD unchanged; the import refs are observation 1.)

### B. Steps 2 and 3, and 004's "ordinary message carries no branches", through `pair.sh sync` (script B-other-branch.sh): sender on Y; receiver on X with a commit of its own, Y absent

```
X=73f60f329dce91982d764eacaabd2f4eed7be46b Ysent=d793a782c66d9858cec04f6ac4f4853331973a26
=== ordinary role bundle (no branches)
checkout: kept 73f60f329: X has commits here that master lacks; master is on Y at d793a782c (kept as refs/pstack/in/T-sender/01795245122425757115-78774771ec5c/head)
sync exit=0
X 73f60f3
main 5541723
=== hand-over
checkout: kept 73f60f329: X has commits here that master lacks; master is on Y at d793a782c (kept as refs/pstack/in/T-sender/01795245122285446025-8c06678cce07/head)
branch Y: created at d793a782c, as master
sync exit=0
--- after
X 73f60f3
Y d793a78
main 5541723
HEAD X 73f60f3
=== second apply
checkout: kept 73f60f329: X has commits here that master lacks; ...
X 73f60f3
Y d793a78
main 5541723
```
No recipe and no reset names X. The sender's working branch Y is created while the checkout is kept. A second apply changes nothing. The ordinary bundle created no branch.

### C. Step 4 for real: origin hangs (GIT_SSH_COMMAND a 40 s sleep), tracking ref origin/main at the base, PSTACK_BUNDLE_FETCH_TIMEOUT_S=2 (script C-slow-origin.sh)

```
base=bf62be123302ec79f0b6b110bacd0c5c4c56b87d
packed 01795245130501259319-4b410a2d47cd baseline bf62be123302ec79f0b6b110bacd0c5c4c56b87d branches [('feat/a', 'a2eff5e4...'), ('main', 'bf62be12...')]
pack took 2s (knob 2s, origin hangs 40s)
# v2 git bundle
-bf62be123302ec79f0b6b110bacd0c5c4c56b87d base
a2eff5e429fd414764827b8a78cea3717444647f refs/pstack/out/hea
--- no tracking ref
packed 01795245132907029859-1d140d103482 baseline  branches [...]
$ ps -eo pid,etimes,args | grep '[s]leep 40'
  38323      13 sleep 40          (orphaned transport; observation 2)
```

### D. Step 5 through `pair.sh sync`: 10 syncs from one sender, default keep 8 (script D-prune.sh)

```
packed 01795245144327060259-197ab02ce3c6
packed 01795245144615144613-9490a6a78f24
packed 01795245144869236317-9d4c11939449
... (7 more)
sets left (default keep 8, 10 syncs):
01795245144869236317-9d4c11939449
01795245145121273900-ae42db060dce
01795245145426916295-503ebcd9fceb
01795245145789337009-25360601257b
01795245146162229546-a025b3b80c05
01795245146455309006-0f2982a28e76
01795245146731029763-9880bcf56bb4
01795245146897590412-3a62b19bdf37
```
The two oldest sets are gone and the newest 8 remain.

### E. 004's rebased-checkout recipe still holds (script E-rebased.sh): receiver on `work` with its own commit; the sender's `work` is a rebase

```
mine=18c4c842a365e4d68a277147d8733f03a432c910 sent=ee00d6b6c41f8189ac3b0ab93f04ae0078c2b130
checkout: kept 18c4c842a: it diverged from master (ee00d6b6c)
  work is at 18c4c842a365e4d68a277147d8733f03a432c910; master is at ee00d6b6c41f8189ac3b0ab93f04ae0078c2b130 (kept as refs/pstack/in/T-sender/.../head)
  to take master's commits and keep this side's under kitchen-recovery/18c4c842a365e4d68a277147d8733f03a432c910, run (it changes nothing unless this checkout is still as described):
    cd ... && [ "$(git symbolic-ref -q --short HEAD)" = work ] && [ "$(git rev-parse HEAD)" = 18c4c842a... ] && [ -z "$(git status --porcelain --untracked-files=no)" ] && { git branch kitchen-recovery/18c4c842a... 18c4c842a... || [ ... ]; } && git reset --keep ee00d6b6c41f8189ac3b0ab93f04ae0078c2b130
sync exit=0
HEAD work 18c4c842a365e4d68a277147d8733f03a432c910
```

### Standing check on the tests in the range

BundleRepoCase.git runs each git call with `timeout=60`. The step 4 timeout test replaces subprocess.run for `fetch` with a fake that raises TimeoutExpired, so nothing hangs and there are no sleeps. The new tests add no output masks or normalisations.

## Flows driven

- pstack: `pair.sh sync <bundle>` with hand-over bundles packed by the head's bundle.pack. Covered: a missing tip (A), a receiver on another branch with an ordinary or a hand-over bundle and a second apply (B), a hanging origin at pack time (C), ref-set pruning over 10 syncs (D), and a rebased checkout's recipe (E). The full suite (1993 tests) covers rotate-master and take-master end to end.
- pstack-docs: no user-facing flow. The unit 031 range changes no docs; SKILL.md changed in unit 004, and spec_gen --check passes.

## Folded Acceptance

none

## Not covered

- I drove rotate-master followed by take-master across two real Amp orbs only through the suite's tests (test_the_next_master_gets_every_local_branch_at_the_same_tip_after_sync_and_take_master and the rest of -k take_master and -k rotate_master), not with live threads.

## Lessons

- L012: held (uv check --frozen passes at the head; the new code adds no unchecked stdlib Optional: `re.fullmatch(...) is not None` in valid_tip).
