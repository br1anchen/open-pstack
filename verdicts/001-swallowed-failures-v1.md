# Verdict 001: swallowed-failures

status: clean
units: 001
range: a90bb4130393b864176dbeded9bb216fcb5f255c..7fca27105380693abb8741e0af8347d63c413f10
verifier: amp anthropic/claude-sonnet-5-5 (a1.small)
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small

## Findings

none

## Evidence

Acceptance 1 (fetch_code False on a failed trunk fetch that resolved, True when both fetch, sync warning) and review-fix finding 1.
The test sets origin/HEAD to a missing branch (trunk resolves, fetch fails) while the code ref is fetchable, asserts fetch_code False and the `warning: origin or a published bundle could not be read` line from `pair.sh sync --branch`; it also asserts True with both fetchable and True when no trunk resolves.
```
$ python3 tests/run.py -k fetch_code
3.8s  test_pstack_kitchen.AmpGitKitchenTests.test_fetch_code_is_false_and_sync_warns_when_a_trunk_that_resolves_cannot_be_fetched_but_not_when_none_resolves
1 tests, 0 failed, 4s wall with 2 jobs        (exit 0)
```

Acceptance 3 and 4 (rollback publish failure keeps the launch or spawn error; a spawned rotation still raises its final publish error).
```
$ python3 tests/run.py -k rollback
test_a_sidekick_rotation_rollback_that_cannot_be_published_leaves_the_spawn_error_standing
test_a_master_rollback_that_cannot_be_published_leaves_the_launch_error_standing
2 tests, 0 failed, 8s wall with 2 jobs        (exit 0)
$ python3 tests/run.py -k final_publish
test_a_rotation_that_spawned_still_raises_the_error_of_its_final_publish
1 tests, 0 failed, 6s wall with 2 jobs
```
The master test asserts `the next master thread did not start: boom launch` and `warning: the rollback could not be published:` with no Traceback, with a pre-receive hook refusing only the rollback push. The sidekick test asserts the preflight-failure error and the warning. The final-publish test asserts `was not pushed` and no rollback warning.

Acceptance 2 (rotate-master, take-master, rotate_if_due print UNKNOWN_FRESHNESS when refreshed is False and carry on). The test moves origin away and asserts the text in the output of dispatch (rotate_if_due), take-master and rotate-master.
```
$ python3 tests/run.py -k freshness_is_unknown
test_rotate_master_take_master_and_a_dispatch_say_when_freshness_is_unknown
test_with_origin_unreachable_wait_and_verify_say_freshness_is_unknown_and_declare_nothing
2 tests, 0 failed, 10s wall with 2 jobs
$ grep -rn refresh_saying_when_unknown skills   (call sites: master.py:487, master.py:635, rotate.py:62, rotate.py:323)
```

Acceptance 5 (run.py -k reports a failed import and exits non-zero; unchanged selection otherwise).
```
$ python3 tests/run.py -k anything no_such_module_xyz
FAIL unittest.loader._FailedTest.no_such_module_xyz (0.1s)
AttributeError: type object '_FailedTest' has no attribute 'no_such_module_xyz'
1 tests, 1 failed, 0s wall with 2 jobs
failed: unittest.loader._FailedTest.no_such_module_xyz
exit 1
$ python3 tests/run.py -k template_tells_a_role
4.3s  test_pstack_core.CoreTests.test_finish_and_notify_take_every_status_a_template_tells_a_role_to_write
1 tests, 0 failed, 4s wall with 2 jobs        (exit 0)
```
Brief commands 1 to 4 ran as written (command 3 with the leading `!`: exit 0 because run.py exited 1).

Mutation check, in a throwaway copy of the scratch (deleted afterwards): reverting the fetch_code trunk check made `-k fetch_code` fail; reverting the master rollback warning made `-k master_rollback` fail; reverting the run.py `_FailedTest` clause made `-k anything no_such_module_xyz` print "0 tests, 0 failed" and exit 0, so only the brief's Verify line pins that one, as its commit message says.

Review-fix step 2, commit range:
```
$ git log --stat a90bb41..HEAD
8c739eb fetch_code reports a failed trunk fetch   tests/test_pstack_kitchen.py +21
508e336 hand-over says when freshness is unknown  tests/test_pstack_kitchen.py +18
5120713 failed rollback publish never hides ...   tests/test_pstack_kitchen.py +63
5a7fd20 refactor: one freshness helper            (no behaviour change; covered by the 508e336 test)
7fca271 fetch_code only fails for a resolved trunk tests/test_pstack_kitchen.py +9
b1eb2e2 run.py -k keeps failed imports            no test; message says the Verify line runs it
```

Full behavioral gate at the head:
```
$ kitchen.py --repo <scratch> setup            -> setup: ok
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base a90bb41...
pstack behavioral: pass
  exit 0 1143.5s python3 tests/run.py
1931 tests, 0 failed, 1143s wall with 2 jobs
gate exit 0
```

Standing check on tests: the added tests use the existing `run_sh` and `remote_git` helpers (bounded). `refuse_master_pushes_where` writes a pre-receive hook; it masks nothing. The new `tests/run.py` clause carries a comment stating its reason. No finding.

## Flows driven

pstack (behavioral): drove `pair.sh sync --branch`, `kitchen.sh rotate-master`, `take-master`, `dispatch` and `pair.sh rotate` against a fake origin in the tests above, and `tests/run.py -k` from the command line (the Evidence blocks above). No other user-facing flow.

## Folded Acceptance

none

## Not covered

No rotation with a real Amp thread; the fake amp in the test harness stands in for it. Otherwise none.

## Lessons

none
