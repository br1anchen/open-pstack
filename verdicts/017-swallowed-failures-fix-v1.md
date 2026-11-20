# Verdict 017: swallowed-failures-fix

status: clean
units: 001 017
range: a90bb4130393b864176dbeded9bb216fcb5f255c..a9b9132645dcfbed28d4d68f7c936ec1e996a27b
verifier: amp, anthropic/claude-opus-5-5
verifier-agent: amp --model anthropic/claude-opus-5-5 --orb-size a1.large

## Findings

none

The packet's scratch worktree did not exist on this orb. I created it with `git worktree add --detach` at a9b9132 (E1), and git sees it as a work tree. I also checked out a second scratch at the base a90bb41 for the comparison runs (E6, E7) and removed it afterwards.

## Evidence

E1. The scratch worktree is at the unit head.

```
$ git worktree add --detach .../017-swallowed-failures-fix-verify a9b9132645dcfbed28d4d68f7c936ec1e996a27b
HEAD is now at a9b9132 fix(skills/pstack-pair): fetch_code reads origin's HEAD when the local origin/HEAD is stale
$ git rev-parse HEAD --is-inside-work-tree
a9b9132645dcfbed28d4d68f7c936ec1e996a27b
true
```

E2. Prove block: setup and the behavioral gate, run with PSTACK_KITCHEN_ROLE=verifier.

```
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261120T183214-4207.log)
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base a90bb41...
pstack behavioral: pass
  exit 0 364.0s python3 tests/run.py  log: .../logs/20261120T183214/pstack-behavioral-0.log
(log tail) 1936 tests, 0 failed, 363s wall with 8 jobs
```

E3. Brief commands at the head. Each ran in a fresh shell from the scratch root.

```
$ python3 tests/run.py -k fetch_code          -> exit 0
  test_fetch_code_is_false_and_sync_warns_when_a_trunk_that_resolves_cannot_be_fetched_but_not_when_none_resolves_or_origins_head_replaces_a_stale_one
  1 tests, 0 failed
$ python3 tests/run.py -k rollback            -> exit 0
  test_a_sidekick_rotation_rollback_that_cannot_be_published_leaves_the_spawn_error_standing
  test_a_master_rollback_that_cannot_be_published_leaves_the_launch_error_standing
  2 tests, 0 failed
$ python3 tests/run.py -k rotation            -> exit 0
  18 tests, 0 failed
$ python3 tests/run.py -k template_tells_a_role -> exit 0
  test_pstack_core.CoreTests.test_finish_and_notify_take_every_status_a_template_tells_a_role_to_write
  1 tests, 0 failed
$ ! python3 tests/run.py -k anything no_such_module_xyz   (run.py itself exit=1)
FAILED (errors=1)
     0.0s  unittest.loader._FailedTest.no_such_module_xyz
1 tests, 1 failed, 0s wall with 8 jobs
failed: unittest.loader._FailedTest.no_such_module_xyz
```

E4. The freshness test at the head. It runs dispatch (rotate_if_due), take-master and rotate-master against an unreachable origin, and asserts that gitstore.UNKNOWN_FRESHNESS is in the output of each.

```
$ python3 tests/run.py -k say_when_freshness_is_unknown
     2.2s  test_pstack_kitchen.AmpGitKitchenTests.test_rotate_master_take_master_and_a_dispatch_say_when_freshness_is_unknown
1 tests, 0 failed
```

E5. An independent probe of `gitstore._fetch_trunk` at the head, using a throwaway bare origin and clone (scratch script probe_fetch_trunk.sh).

```
case A, healthy origin/HEAD -> main: 'refs/remotes/origin/main'
case B, stale local origin/HEAD, origin HEAD -> trunk (exists): 'refs/remotes/origin/trunk'
case C, stale local origin/HEAD, origin HEAD dangling: ''
case D, stale local origin/HEAD, origin unreachable: ''
case E, no origin/HEAD, origin unreachable: None
```

`fetch_code` sets ok=False only when `_fetch_trunk(...) == ''` (diff of gitstore.py). That makes B True, C and D False, and E (no trunk resolves) not a failure.

E6. The head's tests run on the base a90bb41 (tests/test_pstack_kitchen.py copied into the base scratch). They fail there, so they pin the fix.

```
base test_fetch_code_is_false exit=1             1 tests, 1 failed
base rollback_that_cannot_be_published exit=1    2 tests, 2 failed
base rotation_whose_spawn_returned_4 exit=1      1 tests, 1 failed
base rotation_that_spawned_still_raises exit=0   1 tests, 0 failed   (unchanged behaviour: still raises)
base say_when_freshness_is_unknown exit=1        1 tests, 1 failed
```

E7. On the base, run.py swallowed the failed import. The head reports it (E3).

```
base$ python3 tests/run.py -k anything no_such_module_xyz   -> exit 0
0 tests, 0 failed, 0s wall with 8 jobs
base$ python3 tests/run.py -k template_tells_a_role
     3.4s  test_pstack_core.CoreTests.test_finish_and_notify_take_every_status_a_template_tells_a_role_to_write
1 tests, 0 failed
```

The head selects the same single test (E3).

E8. Rotation tests at the head, from tests/test_pstack_kitchen.py (passed in E3 `-k rotation`).

```
test_a_rotation_whose_spawn_returned_4_keeps_that_exit_code_when_its_final_publish_fails:
    out = self.rotate_with_the_final_publish_refused(4)        # run_sh(..., code=4) asserts the exit status
    assertIn('warning: the rotation could not be published:', out)
test_a_rotation_that_spawned_still_raises_the_error_of_its_final_publish:
    out = self.rotate_with_the_final_publish_refused(1, ready=True)
    assertIn('was not pushed', out); assertNotIn('could not be published', out)
```

rotate.py `publish_rotation`: it re-raises when code == 0. For code None it warns "rollback"; for any other code it warns "rotation".

E9. Standing check on the tests in the range. The only new subprocess is bounded.

```
$ git diff a90bb41..HEAD -- tests/test_pstack_kitchen.py | grep -nE "^\+.*(re\.sub|subprocess|Popen|timeout|replace\(|splitlines\(\)\[)"
169:+        r = subprocess.run(
176:+            timeout=120,
```

The diff adds no output masks or normalisations. run_sh has `timeout=120`. The commit range has one fix commit per finding, and each carries its test (git log a90bb41..a9b9132: 8c739eb, 508e336, 5a7fd20, 5120713, b1eb2e2, 7fca271, 36ffab6, a9b9132, plus the tests/SPEC.md changes).

## Flows driven

- pstack (behavioral): the repo's whole suite through the kitchen gate (E2). The `sync --branch` warning, rotate, rotate-master, take-master and dispatch were driven as real `kitchen.sh` and `pair.sh` processes against a git origin in the tests named in E3, E4 and E8. `_fetch_trunk` was probed directly against real git repositories (E5). `tests/run.py -k` was driven at the head and the base (E3, E7).

## Folded Acceptance

none

Unit 001's Acceptance, answered for this `--covers` packet:

- fetch_code False plus the sync warning when the trunk fetch fails and the code ref is fetchable; True when both are fetchable: held (E3 fetch_code, E5, E6).
- rotate-master, take-master and rotate_if_due print UNKNOWN_FRESHNESS and carry on: held (E4, E6).
- When the launch or spawn fails and the rollback publish fails, the error names the launch or spawn failure and stderr has the rollback warning: held (E3 rollback, E6).
- A successful rotation whose final publish fails still raises: held (E8, E6 unchanged on the base).
- run.py -k with a missing module exits non-zero; template_tells_a_role selects the same tests: held (E3, E7).

Unit 017's Acceptance:

- When spawn returns 4 and the final publish fails, the command exits 4 with the rotation warning; when spawn returns 0, the publish error is raised: held (E8, E6).
- With a stale local origin/HEAD and a readable origin, fetch_code is True with no warning; with the trunk unfetchable, it is False with the warning: held (E3 fetch_code test, E5 cases B, C and D).
- Unit 001 still holds at this head: held (above, E2).

## Not covered

Spawn exit code 3 was not exercised separately. The code treats every non-zero code the same way, and the Acceptance names 4.

## Lessons

none
