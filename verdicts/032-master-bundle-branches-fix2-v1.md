# Verdict 032: master-bundle-branches-fix2

status: reject
units: 031 004 032
range: 6a83f500bc5d5ed07276c49035859a9b8322e879..08666a79896199e85d01238ecb5ce445d876b67a
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

1. Unit 032 step 3, "Each branch is reported once" (the finding: "the sender's current branch can be reported by both move_checkout and apply_branches; report it once"), is fixed for one case only. When the checkout is on the sender's branch (`current == here` in move_git), the branch gets one line. When the checkout is on another branch X and the sender's branch Y diverged here, Y is still reported twice in one apply: once by move_checkout (`checkout: kept <X>: X stays, and branch Y here diverged from master (...)` with Y's recipe) and again by apply_branches (`branch Y: kept <Y>: it diverged from master; master is at ... (kept as .../br/Y)`). 031's head prints the same pair, so 032 left this case as it was. The step's test (`test_a_branch_the_checkout_is_on_is_reported_once_by_the_checkout_line`) covers only the receiver-on-Y case, and `test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here` does not assert a single Y line. Evidence D2(b) and D2-031. Severity is low (both lines are true, and the recipe appears once), but the step's stated behaviour does not hold for this reachable case. Smallest fix: in move_git, pass `current` as `reported` to apply_branches whenever move_checkout's line already named it (the diverged-elsewhere return), and assert one `Y` line in the diverged-elsewhere test.

Observations, not findings (no Acceptance line rests on them):

2. Step 1's second guard ("a set a line of this apply names stays") protects nothing an apply can reach today. Every ref an apply's lines name is under the set being applied (`refs/pstack/in/<sender>/<this id>/head` or `/br/<name>`), which prune_sets never deletes anyway, and the recipe itself names commit ids and a `kitchen-recovery/` branch, not pstack refs (Evidence D1, last block: at PSTACK_BUNDLE_KEEP=1, a diverged apply's output names only the applied set). Its test calls prune_sets directly with a hand-written line, and without the fix it fails on the signature (`errors=1`, a TypeError), not on a pruned ref. The brief asked for "a diverged apply whose recipe names an incoming ref keeps that ref while it prunes"; such a test would pass without the guard.
3. Step 3's `git.run` keeps `**extra` and raises TypeError at run time for any key other than `timeout` and `env` (the report says a keyword list would break the 8-parameter function-size guard). The behaviour the brief asks for holds (Evidence G); a type checker does not see it.
4. With `PSTACK_BUNDLE_FETCH_TIMEOUT_S=0` and an origin that answers after 4 s, pack took 8 s (two 4 s waits; I did not trace why); with 1 it took 1 s. 0 is no limit, as the brief asks.

## Evidence

### Brief commands at 08666a798, fresh shell each, from the scratch root, PSTACK_KITCHEN_ROLE=verifier

```
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261121T084337-4795.log)
$ python3 tests/run.py -k bundle          -> exit 0: 30 tests, 0 failed, 44s wall with 4 jobs
$ python3 tests/run.py -k sync            -> exit 0: 21 tests, 0 failed, 31s wall with 4 jobs
$ python3 tests/run.py -k take_master     -> exit 0: 11 tests, 0 failed, 29s wall with 4 jobs
$ python3 tests/run.py -k rotate_master   -> exit 0: 13 tests, 0 failed, 15s wall with 4 jobs   (unit 004's Command 3)
$ python3 tests/spec_gen.py --check       -> exit 0: ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check     -> exit 0: 111 files already formatted
$ uv run --frozen ruff check              -> exit 0: All checks passed!
$ uv check --frozen                       -> exit 0: All checks passed!
$ python3 scripts/check-pstack.py         -> exit 0: 65 skills checked; all 48 previous skills covered
$ uv lock --check                         -> exit 0: Resolved 3 packages
```

Full suite (Command 4), through the Prove gate, which runs `python3 tests/run.py`:

```
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 6a83f500bc5d5ed07276c49035859a9b8322e879
kitchen: queued 1 of 1 for 1 machine gate slot
kitchen: waited 10s for one of 1 machine gate slots
pstack behavioral: pass
  exit 0 574.3s python3 tests/run.py  log: .../logs/20261121T085810/pstack-behavioral-0.log
(log tail) 2000 tests, 0 failed, 574s wall with 4 jobs
```

An earlier full run of mine was started under `nohup`, which makes every child ignore SIGHUP (SigIgn 0x7 against 0x6 in a plain shell). Two signal tests failed there (`test_a_child_killed_by_a_signal_ends_the_command_with_128_plus_the_signal`, `test_with_timeout_gives_the_commands_exit_124_...`: `kill -HUP $$` returned 0, not 129). Both pass in a plain shell (`1 tests, 0 failed` each), and the gate run above, without nohup, passed all 2000. That was a harness artifact, not this unit.

### R1. Unit 032: each step's tests fail without its fix (a worktree at each step's commit, only the source files that commit changed taken back to its parent, tests kept; script .amp/in/orb-backup/v032/reverts.sh on this orb)

```
== s032-1: at 7f33eb2, source reverted to ece5785: bundle.py
-- without the fix, -k pruning_keeps_the_newest_sets: 1 tests, 1 failed (FAILED (failures=1)) test_pruning_keeps_the_newest_sets_when_older_ids_are_bare_uuids_that_sort_above_the_new_ones
-- without the fix, -k pruning_keeps_a_set_that:     1 tests, 1 failed (FAILED (errors=1))   test_pruning_keeps_a_set_that_a_line_of_the_apply_names
-- with the fix: 1 tests, 0 failed / 1 tests, 0 failed
== s032-2: at 74e65ab, source reverted to 7f33eb2: bundle.py
-- without the fix, -k detached_sender_gets_no_reset:  1 tests, 1 failed  test_a_detached_sender_gets_no_reset_recipe_for_the_receivers_branch_and_the_line_names_the_incoming_ref
-- without the fix, -k another_branch_gets_the_recipe: 1 tests, 1 failed  test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here
-- with the fix: 1 tests, 0 failed / 1 tests, 0 failed
== s032-3: at 08666a7, source reverted to 74e65ab: bundle.py git.py
-- without the fix, -k reported_once_by_the_checkout: 1 tests, 1 failed  test_a_branch_the_checkout_is_on_is_reported_once_by_the_checkout_line
-- without the fix, -k bundle_settings_are_checked:  1 tests, 1 failed  test_the_bundle_settings_are_checked_before_apply_changes_anything_and_a_zero_timeout_is_no_timeout
-- with the fix: 1 tests, 0 failed / 1 tests, 0 failed
== head-s032-2: 74e65ab's source diff reverse-applied at 08666a798: 1 file changed, 12 insertions(+), 17 deletions(-)
-- without the fix, at head: both step-2 tests fail (1 tests, 1 failed each)
== head-s032-1: 7f33eb2's diff does not reverse-apply at the head (conflict in bundle.py); shown at its commit above
```

Commit layout (review-fix step 2): each step is one commit with its fix, its tests and SPEC.md.

```
7f33eb2 bundle.py tests/SPEC.md tests/test_pstack_core.py            (step 1)
74e65ab bundle.py tests/SPEC.md tests/test_pstack_core.py            (step 2)
08666a7 bundle.py git.py tests/SPEC.md tests/test_pstack_core.py     (step 3)
```

### R2. Unit 031: each of steps 1 to 5 still has a failing-without-fix test (same method; the 031 diffs conflict when reverse-applied at the head, so each runs at its own commit)

```
== s031-1: at b18310c, reverted to b10896e: -k one_missing_branch_tip              without: 1 tests, 1 failed   with: 1 tests, 0 failed
== s031-2: at 6536f9b, reverted to b18310c: -k senders_working_branch_is_created   without: 1 tests, 1 failed   with: 1 tests, 0 failed
== s031-3: at 8fa4fa1, reverted to 6536f9b: -k another_branch_with_its_own_commits without: 1 tests, 1 failed   with: 1 tests, 0 failed
                                            -k another_branch_gets_the_recipe     without: 1 tests, 1 failed   with: 1 tests, 0 failed
== s031-4: at 6cbfaf7, reverted to 8fa4fa1 (bundle.py git.py): -k cannot_be_fetched without: 2 tests, 1 failed  with: 2 tests, 0 failed
                                            -k timed_out_one_falls_back           without: 1 tests, 1 failed   with: 1 tests, 0 failed
== s031-5: at 6dcc8e3, reverted to 5647fa0: -k older_ref_sets_of_one_sender       without: 1 tests, 1 failed   with: 1 tests, 0 failed
```
All of these tests pass at the head inside `-k bundle` (30 tests, 0 failed).

### D1. Step 1 through `pair.sh sync` at the head (script d1-prune.sh): three legacy sets `f000…0..2`, then 4 hand-over syncs at PSTACK_BUNDLE_KEEP=3

```
legacy sets:
f0000000000000000000000000000000
f0000000000000000000000000000001
f0000000000000000000000000000002
packed 01795250720881303615-84bed172948f
packed 01795250721255784946-5d95f5810c7b
packed 01795250721702774837-22b30a99f4f2
packed 01795250722158982005-72d4c5a962e7
sets left (keep 3):
01795250721255784946-5d95f5810c7b
01795250721702774837-22b30a99f4f2
01795250722158982005-72d4c5a962e7
--- a diverged apply at keep 1 with older sets: which sets do its lines name?
checkout: kept 07649c764: it diverged from master (501178fa4)
  work is at 07649c76...; master is at 501178fa... (kept as refs/pstack/in/T-sender/01795250724964060816-7b9fe1bf549b/head)
  to take master's commits ... run (...): cd ... && [ ... = work ] && ... && git reset --keep 501178fa41c4970a7de946211401ec53c2dd2b4d
sync exit=0
sets named in the output:
refs/pstack/in/T-sender/01795250724964060816-7b9fe1bf549b
sets left:
01795250724964060816-7b9fe1bf549b
applied id 01795250724964060816-7b9fe1bf549b
```
The newest three new sets stay and the legacy sets go. The lines name only the applied set (observation 2).

### D2. Step 2 through `pair.sh sync` at the head (script d2-recipes.sh)

```
=== (a) detached sender
head=45c86eeb92bac852ca3ff870ffe18b325d833a89 X=a5d49da8e05e460bc2cb39de732dcf1b903d69d0
checkout: kept a5d49da8e: X has commits here that master lacks; master is detached at 45c86eeb9 (kept as refs/pstack/in/T-sender/01795250745040986364-81811584dce6/head)
sync exit=0
HEAD X a5d49da8e05e460bc2cb39de732dcf1b903d69d0; X a5d49da8e05e460bc2cb39de732dcf1b903d69d0
=== (b) sender on Y; receiver on X with its own commit, local Y diverged
X=a5d49da8e... Ymine=3ca3e1313f8f05d05de821d62be11e21665f8f19 Ysent=23d79fc8fe8ea57e61d4f0b08c9e09e6b9bf0a73
checkout: kept a5d49da8e: X stays, and branch Y here diverged from master (23d79fc8f)
  Y is at 3ca3e1313f8f...; master is at 23d79fc8fe8e... (kept as refs/pstack/in/T-sender/01795250745758728886-954e5624bd94/head)
  to take master's commits and keep this side's under kitchen-recovery/3ca3e1313f8f..., run (...):
    cd ... && true && [ "$(git rev-parse refs/heads/Y)" = 3ca3e1313f8f... ] && [ -z "$(git status ...)" ] && { git branch kitchen-recovery/3ca3e131... ... } && git checkout -q -B Y 23d79fc8fe8ea57e61d4f0b08c9e09e6b9bf0a73
branch Y: kept 3ca3e1313: it diverged from master; master is at 23d79fc8fe8e... (kept as refs/pstack/in/T-sender/01795250745758728886-954e5624bd94/br/Y)
sync exit=0
HEAD X a5d49da8e...; X a5d49da8e...; Y 3ca3e1313f8f...
```
(a): no reset recipe and no kitchen-recovery line for a detached sender; X and HEAD unchanged. (b): the first line names X's tip and says X stays; no recipe moves X. Y is reported twice, once by the checkout line and once by `branch Y:` (finding 1).

### D2-031. The same scenario (b) at 031's head 6dcc8e3 (a worktree there, same script)

```
checkout: kept 4da0adadd: branch Y here diverged from master (2bdaf2925)
branch Y: kept 4da0adadd: it diverged from master; master is at 2bdaf2925527... (kept as refs/pstack/in/T-sender/.../br/Y)
```
The double report predates 032 and 032 leaves it.

### D3. Step 3 through `pair.sh sync` and bundle.pack at the head (script d3-settings.sh)

```
=== (a) receiver on Y ahead
checkout: kept fdbd146fb, ahead of master (699448226)
branch feat/a: created at db4bac295, as master
sync exit=0
lines naming Y: 1
=== (b) PSTACK_BUNDLE_KEEP=many on a hand-over that would create feat/b and write reports/001.md
pair.sh: PSTACK_BUNDLE_KEEP must be a whole number
sync exit=1
refs and HEAD unchanged
--- the same bundle with the setting unset
store: 1 updated, 0 removed
branch feat/b: created at db4bac295, as master
001.md
=== (c) fetch timeout knob, origin over ssh that answers after 4 s and fails
PSTACK_BUNDLE_FETCH_TIMEOUT_S=0: pack took 8s
PSTACK_BUNDLE_FETCH_TIMEOUT_S=1: pack took 1s
baseline f7aca90e9b5c82764ceb996ef16095d8cbd3ea96
base=f7aca90e9b5c82764ceb996ef16095d8cbd3ea96
```
(b): `ls $S/reports` printed nothing after the rejected apply (only the line after the unset run shows `001.md`).

### D4. 004's Acceptance through `pair.sh sync` at the head (script d4-004.sh): sender with unpushed non-HEAD branches feat/x and plain2; receiver lacks them, has `up` one behind, `ahead` past, `busy` behind and checked out in a second worktree

```
h.tgz branches: ['ahead', 'busy', 'feat/x', 'main', 'plain2', 'up']
o.tgz branches: []
=== ordinary bundle
checkout: at a655d919f, as master
sync exit=0
feat/x+plain2 present: 0
=== hand-over
checkout: at a655d919f, as master
branch ahead: kept 440ab5b2a, ahead of master (ae4d9ae44)
branch busy: kept 1d5df4925: checked out in /tmp/v032drive.C0uN/busy; master is at 01da4fda6eea... (kept as refs/pstack/in/T-sender/.../br/busy)
branch feat/x: created at 980796607, as master
branch plain2: created at fab0e7a2d, as master
branch up: fast-forwarded 1d5df4925 to 7b604593b, as master
sync exit=0
feat/x 980796607ff58cb988f62afd2938633d0f7ca0c2
plain2 fab0e7a2d0775e0bb8071fbc585ee242d8c5be12
up 7b604593b84cdf5b7585383351fb390994f489df
ahead 440ab5b2ab504b9c58f76b7d4423dcad30b70577
busy 1d5df49253be6e2666f8ee414737cf7c823926cb
main a655d919fecb0b7742eb0a6fbb604077bb12ed78
expect feat/x 980796607ff5... plain2 fab0e7a2d077... up 7b604593b84c... ahead 440ab5b2ab50... busy 1d5df49253be... main a655d919fecb...
=== second apply
created or moved lines: 0
refs/heads and HEAD unchanged
=== failed import: a hand-over naming a tip it does not carry
pair.sh: /tmp/v032drive.C0uN/bad.tgz: branch zzz/missing: fffffffff was not carried and origin lacks it
sync exit=1
```
The rebased-checkout recipe line of 004 (full OIDs, `kitchen-recovery/<old-oid>`, HEAD unchanged, no automatic reset) is shown in D1's diverged apply. take-master and rotate-master end to end: `-k take_master` 11/0 and `-k rotate_master` 13/0 above.

### G. git.run accepts only timeout and env

```
$ python3 -I -c "... git.run('.', 'status', cwd='/') ...; git.ok('.', 'rev-parse', 'HEAD', timeout=5, env={'PATH': ...})"
TypeError: git.run got unexpected keyword arguments ['cwd']
timeout+env ok: True
```

### Standing check on the tests in the range

The new tests run git through `BundleRepoCase.git` (`timeout=60`) or through bundle.apply/pack on local repos. The settings test's origin is a nonexistent local path, which fails at once, including for the `'0'` (no limit) row. The step-4 timeout test fakes `TimeoutExpired`. There are no sleeps, no elapsed-time asserts and no output masks. The env patches touch only `PSTACK_BUNDLE_*`.

## Flows driven

- pstack: `pair.sh sync <bundle>` with bundles packed by the head's bundle.pack. Covered: legacy and new ref sets pruned at KEEP=3, and the sets a diverged apply names (D1); a detached sender and a receiver on another branch with a diverged sender branch (D2); a receiver on the sender's branch, KEEP=many rejected before any change, and the fetch-timeout knob at 0 and 1 against a slow ssh origin (D3); 004's create, fast-forward and keep rules, the ordinary bundle, the second apply and the failed import (D4). The full suite (2000 tests) covers rotate-master and take-master end to end.
- pstack-docs: no user-facing flow. 032 changes no docs, and spec_gen --check and check-pstack pass.

## Folded Acceptance

none

## Not covered

- I did not drive rotate-master followed by take-master across two live Amp orbs. They are covered by the suite (`-k take_master`, `-k rotate_master`) and by D4's sync of a hand-over bundle.
- Command 4 (`python3 tests/run.py`) passed through the Prove gate's run. My own standalone run was under nohup (see Brief commands) and was superseded.

## Lessons

- L012: held (uv check --frozen passes at the head; set_age uses `str.partition`, which returns no Optional; no new re.match).
