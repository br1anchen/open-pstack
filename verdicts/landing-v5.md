# Verdict landing: landing-v5

status: clean
units: landing
range: 3b9514449d2a1f11c47e7388b76ae83876fb33aa..29cc53c2e8a65114086f9ec00042f5dd64d4b469
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes (not findings):
- This orb had no scratch worktree at the packet's path; I created it myself with `git worktree add --detach <scratch> 29cc53c2e8a6`, and git sees it as a work tree at that head (E1). For the parent repro I made a second scratch, `000-landing-base`, at 3b951444 (E4).
- The range-diff against the verified landing-v4 range (699837d8..fa6cd2a6) differs only in tests/SPEC.md (regenerated line numbers) and in the import list and hunk placement of dispatch.py, as the report says (E2). The merged `dispatch()` keeps main's `warn_unplanned_escalation(brief)` right after `classify_brief`, where main had it, and puts 046's `require_sidekick_awake` before `rotate_gate` (E3).
- bug-fix.step-5: each fix commit carries its own tests, as landing-v4 verified, so no failing test sits ahead of its fix in history. This landing is cherry-picks and does not restage them.

## Evidence

E1: scratch at the head
```
$ git worktree add --detach .../000-landing-verify 29cc53c2e8a65114086f9ec00042f5dd64d4b469
HEAD is now at 29cc53c2 docs(skills/pstack-kitchen): help lists snooze; ...
$ git rev-parse HEAD; git rev-parse --is-inside-work-tree
29cc53c2e8a65114086f9ec00042f5dd64d4b469
true
$ git fetch -q origin main && git rev-parse origin/main
3b9514449d2a1f11c47e7388b76ae83876fb33aa
$ git log --oneline 3b951444..29cc53c2e
29cc53c2 docs(skills/pstack-kitchen): help lists snooze; every send to a snoozed thread refuses, and a refused --done says to undo the Amp snooze
267e408c fix(skills/pstack-pair): snooze --done refusals name the update_thread undo and raise SnoozeRefused
1842e862 fix(skills/pstack-kitchen): dispatch refuses a snoozed sidekick before classify, the playbook and the archive sweep, unless it rotates to a fresh thread
b3a95126 fix(skills/pstack-pair): stop and close refuse before they change anything when a role thread is snoozed, naming every snoozed thread
47edd469 docs(skills/pstack-kitchen): starts print the nest instructions; a pending row keeps a thread from the sweep; take-master nests the whole run; --done does not check the parent
1a74dee3 fix(skills/pstack-pair): take-master nests every thread of the run, archived ones and earlier masters included, under the new anchor
c00cad62 fix(skills/pstack-pair): the sweep keeps a thread whose nest row is pending; nest lists an archived one with the unarchive, parent, re-archive calls
e143a155 fix(skills/pstack-pair): a start under Amp threads prints the nest instructions of the thread it started
```

E2: range-diff against the landing-v4 range
```
$ git range-diff 699837d8..fa6cd2a6 3b951444..29cc53c2e   (406 lines; pairing lines and changed-hunk headers below)
1:  5d054e1e ! 1:  e143a155 fix(skills/pstack-pair): a start under Amp threads prints the nest instructions ...
2:  3b74f174 ! 2:  c00cad62 fix(skills/pstack-pair): the sweep keeps a thread whose nest row is pending; ...
3:  942fe43f ! 3:  1a74dee3 fix(skills/pstack-pair): take-master nests every thread of the run, ...
4:  e4235601 ! 4:  47edd469 docs(skills/pstack-kitchen): starts print the nest instructions; ...
5:  f46f7c86 ! 5:  b3a95126 fix(skills/pstack-pair): stop and close refuse before they change anything ...
6:  c8d46d48 ! 6:  1842e862 fix(skills/pstack-kitchen): dispatch refuses a snoozed sidekick before classify, ...
7:  ec82fa34 ! 7:  267e408c fix(skills/pstack-pair): snooze --done refusals name the update_thread undo ...
8:  fa6cd2a6 ! 8:  29cc53c2 docs(skills/pstack-kitchen): help lists snooze; ...
$ grep -E '^    (@@|##)' range-diff | sort -u by file
    @@ tests/SPEC.md ...                                             (25 hunks, all line-number shifts)
    @@ skills/pstack-kitchen/scripts/kitchen_core/dispatch.py: import contextlib   (2 hunks, commit 6 only)
dispatch.py delta in commit 6:
    + from pstack_core.gates import PLAN_PLAYBOOKS
    ++    header_field,
    -@@ ... def rotate_gate(store):
    +@@ ... def warn_unplanned_escalation(brief):
```

E3: the merged dispatch order, head against main
```
$ git show 3b951444:skills/pstack-kitchen/scripts/kitchen_core/dispatch.py | grep -nE 'rotate_gate|warn_unplanned_escalation|classify_brief|^def dispatch'
73:def dispatch(ctx, args):
89:    if refused_status := rotate_gate(store):
97:        classify_brief(store, brief)
98:        warn_unplanned_escalation(brief)
$ sed -n 153,164p skills/pstack-kitchen/scripts/kitchen_core/dispatch.py   (head)
    if has_pair(store):
        require_sidekick_awake(store, brief)
    if refused_status := rotate_gate(store):
        return refused_status
    landing = is_landing(brief)
    ...
        require_pair(ctx, store)
        classify_brief(store, brief)
        warn_unplanned_escalation(brief)
```

E4: parent repro. On origin/main 3b951444, with the head's three test files checked out over it, the new tests fail.
```
$ cd 000-landing-base (3b951444 + git checkout 29cc53c2e -- tests/test_pstack_{kitchen,core,pair}.py)
$ python3 tests/run.py -k snooze      -> exit 1
FAIL test_pstack_core.CoreTests.test_kitchen_runs_the_trios_stop_after_its_own_snooze_check_from_a_table_of_its_own_and_has_no_spawn
FAIL test_pstack_core.CoreTests.test_snooze_refusals_have_their_own_class_and_keep_their_exit_codes
FAIL test_pstack_kitchen.AmpKitchenTests.test_close_refuses_with_every_snoozed_role_named_and_stops_none_until_they_are_woken
FAIL test_pstack_kitchen.AmpKitchenTests.test_dispatch_to_a_snoozed_sidekick_prints_only_the_refusal_and_archives_nothing
FAIL test_pstack_kitchen.AmpKitchenTests.test_snooze_done_and_wake_record_each_change_log_it_and_refuse_a_thread_the_store_never_named
FAIL test_pstack_kitchen.AmpKitchenTests.test_stop_to_a_snoozed_sidekick_refuses_before_it_clears_the_queue_or_logs_anything
$ python3 tests/run.py -k nest        -> exit 1
FAIL test_pstack_kitchen.AmpKitchenTests.test_every_command_that_starts_a_thread_prints_its_nest_instructions (thread=1..4: sidekick, verifier, consultant, sidekick)
FAIL test_pstack_kitchen.AmpKitchenTests.test_nest_lists_an_archived_pending_thread_with_the_unarchive_parent_rearchive_calls
FAIL test_pstack_kitchen.AmpKitchenTests.test_the_old_master_thread_is_archived_once_take_master_has_run_and_it_is_nested
FAIL test_pstack_kitchen.AmpKitchenTests.test_the_sweep_keeps_a_thread_with_a_pending_nest_row_until_nest_done
```

E5: the brief's Verify commands at the head, in the scratch, with PSTACK_KITCHEN_ROLE=verifier (last lines of each)
```
$ test "$(git rev-list --count origin/main..HEAD)" -ge 8        count 8, exit 0
$ test -z "$(git log --format=%s origin/main..HEAD | sort | uniq -d)"   dups [], exit 0
$ python3 tests/run.py -k nest
21 tests, 0 failed, 18s wall with 4 jobs                          exit 0
$ python3 tests/run.py -k snooze
10 tests, 0 failed, 11s wall with 4 jobs                          exit 0
$ python3 tests/run.py -k dispatch
79 tests, 0 failed, 47s wall with 4 jobs                          exit 0
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered                 exit 0
$ python3 tests/spec_gen.py --check
| total | 438 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing                           exit 0
$ uv lock --check
Resolved 3 packages in 47ms                                       exit 0
$ uv run --frozen ruff format --check
112 files already formatted                                       exit 0
$ uv run --frozen ruff check
All checks passed!                                                exit 0
$ uv check --frozen
All checks passed!                                                exit 0
```

E6: Prove block (setup, then the behavioral gate: the full suite)
```
$ python3 .../kitchen.py --repo .../000-landing-verify setup
setup: ok (1 commands, log .../repos/repo-f328a3c3/logs/setup-20261124T154652-6140.log)
$ python3 .../kitchen.py --repo .../000-landing-verify gate pstack behavioral --role verifier --base 3b9514449d2a1f11c47e7388b76ae83876fb33aa
pstack behavioral: pass
  exit 0 666.3s python3 tests/run.py  log: .../repos/repo-f328a3c3/logs/20261124T154654/pstack-behavioral-0.log
$ tail -1 pstack-behavioral-0.log
2054 tests, 0 failed, 665s wall with 4 jobs
```

E7: testing standing check on the range's tests
```
$ git diff 3b951444..HEAD -- tests/*.py | grep -E '^\+' | grep -nE 'sleep|time\.time|monotonic|subprocess\.(run|Popen|call)|re\.sub|assertAlmost'
(no output)
$ git diff --stat 3b951444..HEAD -- tests | tail -1
 4 files changed, 363 insertions(+), 95 deletions(-)
```

## Flows driven

- pstack: the snooze and nest flows of units 046 and 040, run through `kitchen.sh` subprocesses against the repo's fake `amp` (tests/pstack-pair/bin). Dispatch, stop and close to a snoozed sidekick refuse with the `update_thread(... snooze="off")` line and change nothing, every start prints its nest instructions, the sweep keeps a nest-pending thread, and take-master nests the whole run. Each flow fails at the parent (E4) and passes at the head (E5), and the merged dispatch seam (main's warn plus 046's awake check) passes the 79 dispatch tests (E3, E5). No real Amp thread was started; the fake amp is the repo's declared surface for these commands.
- pstack-docs: skills/pstack-kitchen/SKILL.md and master.md were checked by check-pstack (inventory, frontmatter, links) and by spec_gen --check (E5).

## Folded Acceptance

Unit 050-landing-rebase-5:
- `git log origin/main..HEAD` holds the 8 commits by subject plus any step 2 fixes, none twice: held. The head holds 8 commits on origin/main 3b951444, each pairing one-to-one with landing-v4's 8, with no duplicate subjects and no step 2 commits (E1, E2, E5).
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head: held. 2054 tests, 0 failed (E6); check-pstack, spec_gen --check, uv lock --check, ruff format --check, ruff check and uv check all exit 0 (E5).

## Not covered

- No live Amp thread was snoozed, dispatched to or nested. The flows ran against the fake amp only, since driving real threads would change shared state outside the scratch.

## Lessons

none
