# Verdict 004: master-bundle-branches

status: clean
units: 004
range: 6a83f500bc5d5ed07276c49035859a9b8322e879..550a7d760223b1e44d1ba85bbd0ba2203be9f353
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Observations (none of them breaks Acceptance; they're listed for the review):

- Every synced bundle now leaves its tips under `refs/pstack/in/<sender>/<bundle id>/` and nothing prunes them. This is the report's first deviation. These refs pin objects forever, so `gc` never reclaims them.
- When a hand-over's git import fails, `apply` has already merged the store files before it raises. The sync exits 1 and take-master never runs (Evidence E5 shows that `.master.previous` stays), but the store files on that orb are already the sender's.
- bug-fix step 5: each test lands in the same commit as the source it pins, as AGENTS.md requires. The failing-then-passing proof is therefore the base-vs-head probe (E3/E4), not commit order.
- The packet's scratch worktree didn't exist on this orb. I created it at 550a7d7 with `git worktree add --detach` and ran setup there (E0).

## Evidence

E0: scratch at the unit's head, then setup.
```
$ git worktree add --detach <scratch> 550a7d760223b1e44d1ba85bbd0ba2203be9f353 && git -C <scratch> rev-parse HEAD --is-inside-work-tree
550a7d760223b1e44d1ba85bbd0ba2203be9f353
true
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, ...)
```

E1: brief commands, each run in a fresh shell from the scratch root.
```
$ python3 tests/run.py -k bundle
27 tests, 0 failed, 21s wall with 4 jobs
exit=0
$ python3 tests/run.py -k take_master
11 tests, 0 failed, 25s wall with 4 jobs
exit=0
$ python3 tests/run.py -k rotate_master
13 tests, 0 failed, 26s wall with 4 jobs
exit=0
```

E2: behavioral gate (full suite) and spec check.
```
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 6a83f500...
pstack behavioral: pass
  exit 0 606.9s python3 tests/run.py
1985 tests, 0 failed, 607s wall with 4 jobs
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
```

E3: my own probe, `.verify-probe/probe_004.py` in the scratch. It drives the real `kitchen.sh rotate-master`, then `kitchen.sh sync <hand-over bundle>` as the next master's thread, in the repo's fake-amp harness, and runs unchanged against each worktree's own skills. At HEAD (550a7d7):
```
test_p1_three_unpushed_branches_arrive ... ok
test_p2_receiver_table ... ok
test_p3_rebased_checkout_through_sync ... ok
Ran 3 tests in 5.625s
OK

--- p1 sync rc 0        (feat/one, side, deep/a/b deleted and objects pruned before sync)
checkout: at 6b5746185, as demo-master
branch deep/a/b: created at d46854855, as demo-master
branch feat/one: created at 0c570a0cb, as demo-master
branch side: created at 7b6d9e53b, as demo-master
p1 expected {'feat/one': '0c570a0cb7...', 'side': '7b6d9e53b2...', 'deep/a/b': 'd46854855e...'}
p1 got      {'feat/one': '0c570a0cb7...', 'side': '7b6d9e53b2...', 'deep/a/b': 'd46854855e...'}
--- p1 second sync rc 0
checkout: at 6b5746185, as demo-master        (no branch line: nothing changed)

--- p2 sync rc 0        (receiver: ff an ancestor, ahead +1 own commit, div diverged, wt checked out in a 2nd worktree)
branch ahead: kept a8844e0b5, ahead of demo-master (d6105b749)
branch div: kept 0385fc7e9: it diverged from demo-master; demo-master is at 55b59bf6f257567026ad9ee435c3a73417ebdca5 (kept as refs/pstack/in/T-aaaa.../3a6dd9ba.../br/div)
branch ff: fast-forwarded 704c517fd to 39e33e009, as demo-master
branch wt: kept 0413cb366: checked out in /tmp/tmpnlzwafhm/second-wt; demo-master is at e5032cc9426d82404919fb40ad2587a21765f491 (kept as .../br/wt)
p2 after first sync {'ff': '39e33e00...'(=sender tip), 'ahead': 'a8844e0b...'(=local), 'div': '0385fc7e...'(=local), 'wt': '0413cb36...'(=base)}
--- p2 second sync rc 0   (same kept lines, no created/fast-forwarded line; tips asserted identical)

--- p3 sync rc 0        (receiver on master at old 04787aa5c; sender's master rebased to 1f59ac586)
checkout: kept 04787aa5c: it diverged from demo-master (1f59ac586)
  master is at 04787aa5c1e47d71b0f9c88d06bc39f271f227f3; demo-master is at 1f59ac5863b4e6ad6573624184de9f7eedf434b8 (kept as refs/pstack/in/T-aaaa.../7fc36abe.../head)
  to take demo-master's commits and keep this side's under kitchen-recovery/04787aa5c1e47d71b0f9c88d06bc39f271f227f3, run (it changes nothing unless this checkout is still as described):
    cd /tmp/tmpenvitewl/repo && [ "$(git symbolic-ref -q --short HEAD)" = master ] && [ "$(git rev-parse HEAD)" = 04787aa5c1e4... ] && [ -z "$(git status --porcelain --untracked-files=no)" ] && { git branch kitchen-recovery/04787aa5c1e4... 04787aa5c1e4... || [ ... ]; } && git reset --keep 1f59ac5863b4e6ad6573624184de9f7eedf434b8
(asserted after sync: HEAD and refs/heads/master still 04787aa5c..., no automatic reset)
p3 recipe rc 0          (afterward: HEAD = 1f59ac586..., kitchen-recovery/04787aa5c... = 04787aa5c...)
```

E4: the same probe at the unit's parent (6a83f50, in a second scratch). It reproduces the original failure.
```
test_p1_three_unpushed_branches_arrive ... FAIL
test_p2_receiver_table ... FAIL
test_p3_rebased_checkout_through_sync ... FAIL
--- p1 sync rc 0
checkout: at f4d2de1de, as demo-master
p1 got      {'feat/one': None, 'side': None, 'deep/a/b': None}
--- p2 sync rc 0
AssertionError: '1ab66d8d...' != 'b8a3a873...'   (ff not fast-forwarded: no branches carried)
--- p3 sync rc 0
checkout: kept abc7c20dd: it diverged from demo-master (ee248a630)
AssertionError: 'abc7c20dd15b...' not found in output   (bare "kept", no full OIDs, no recipe)
FAILED (failures=3)
```

E5: a failed import and an ordinary role message, from the unit's own end-to-end tests. They ran in E1, and I read them in the diff:
- `test_a_hand_over_bundle_whose_commits_cannot_be_imported_makes_sync_exit_non_zero` strips `git.bundle` from the real rotate-master bundle and prunes the branches. It asserts `sync` exit 1, `was not carried and origin lacks it`, and that `.master.previous` remains (take-master did not complete).
- `test_a_hand_over_bundle_carries_every_local_branch_and_a_role_message_does_not` opens every `*-pstack-demo-sidekick.tgz` sent by a real dispatch. It asserts that the manifest has no `branches` key and that `git bundle list-heads` shows no `/br/` ref.
- `test_a_hand_over_bundle_is_cut_against_origins_default_branch_not_the_remotes_this_side_has`: when a remote only the sender has holds the branches, the bundle still carries them.
Both passed: `-k bundle` 27 tests, 0 failed (E1).

E6: test hygiene in the range.
```
$ git diff 6a83f50..550a7d7 -- tests/*.py | grep -c '^+.*subprocess.run('   -> 7
$ git diff 6a83f50..550a7d7 -- tests/*.py | grep -c 'timeout='             -> 7
$ git diff ... | grep '^+.*\(sleep\|time\.time\|re\.sub\)'                -> (no output)
```

## Flows driven

- pstack: drove `kitchen.sh rotate-master` and then `kitchen.sh sync <bundle>` as the next master's thread (E3; the fake-amp harness is the repo's only surface for Amp threads). The flows were the branch hand-over, the receiver-side branch table, idempotent re-sync, and the rebased-checkout recipe, which I executed. Each one fails at the parent (E4).
- pstack-docs: no user-facing flow. The range touches tests/SPEC.md only, which `spec_gen.py --check` confirms is current (E2).

## Folded Acceptance

none

## Not covered

- I didn't run a real cross-orb hand-over on Amp (two orbs, attachment upload). The harness runs both threads on one machine, and E3 simulates the receiver lacking the objects by deleting the branches and pruning.
- The recipe's non-checked-out-branch variant (`git checkout -q -B`) was proven only by the unit's test `test_a_bundle_onto_a_diverged_branch_that_is_not_checked_out_gets_a_recipe_that_checks_it_out_anew` (E1), not by my probe.

## Lessons

L012: held. `uv check --frozen` passes in the full gate (E2), and bundle.py guards the Optionals it reads from git (`git.out(...) or ''`, `local is None`).
