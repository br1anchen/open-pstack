# Report 040: nest-every-start

status: done
brief: briefs/040-nest-every-start.md
playbook: bug-fix
branch: kitchen-followups-040
head: 6b2551f9b05531c632a2d3e958731fb76f5c4b87
tree: clean

## Changed

Branch `kitchen-followups-040` from origin/main eb8228f9; four commits, each recorded with `kitchen.sh step` (the kitchen counts them 1-4: brief steps 1, 2, 3 and 5); brief step 4 needed no code.

- b6ff274e (step 1) `nest.instructions(store, prog)` returns the `nest-pending:` lines and the update_thread instruction; `nest()` prints it unchanged. `amp.record_started` prints it right after a start adds a new pending row (`record_pending`/`add_pending` now return whether a row was added). `nest.process_prog()` reads the command name (kitchen.sh or pair.sh) from the process's `--skill-root`, since no Context reaches `mux/amp.py`.
- b48455eb (step 2) `amp.assess` returns `kept: nest pending (nest it first; update_thread cannot set a parent on an archived thread)` for a thread with a pending row, before the archive decisions. `nest` lists a pending thread the registry marks archived as `(<role>, archived)` and adds `update_thread(thread=<T>, archived=false)`, then its parent call, then `update_thread(thread=<T>, archived=true)`.
- 1b8c37d1 (step 3) `nest.take_anchor` adds a row under the new anchor for every thread of the run: live roles, `run_threads(store, archived=True)` (new parameter) and every previous master (`.anchor.previous`, `.master.sessions`).
- 6b2551f9 (step 5) SKILL.md nesting bullet and master.md (step 5 sentence, take-master sentence).
- Tests: tests/test_pstack_kitchen.py, tests/SPEC.md regenerated in each commit.

## Ran

Step 1, `test_every_command_that_starts_a_thread_prints_its_nest_instructions` (spawn, verify, consultant, a rotation inside dispatch under the fake amp): before the fix `FAILED (failures=4)`, one per start; after: `python3 tests/run.py -k nest` -> `18 tests, 0 failed`.
Step 2, `test_the_sweep_keeps_a_thread_with_a_pending_nest_row_until_nest_done` (kept, then archived after `--done`) and `test_nest_lists_an_archived_pending_thread_with_the_unarchive_parent_rearchive_calls`: `-k nest` and `-k archiv` -> `20 tests, 0 failed`, `26 tests, 0 failed`.
Step 3, `test_take_master_re_parents_every_thread_of_the_run_to_the_new_master_and_none_stays_under_an_old_one` (two earlier masters, two verifiers, an archived sidekick, setUp's sidekick): before the fix `FAILED (failures=7)`; after: passes, and `-k take_master`, `-k anchor` pass. `test_a_newer_pending_row_supersedes_an_older_pending_one_for_the_same_thread` shows `pending()` keeps only the newest row per thread (it passed at once: `pending()` already did this).
Whole suite at step 2 (before recording): `2026 tests, 0 failed, 565s wall with 4 jobs`.
Each recorded step: `gates: pass (pstack,pstack-docs)`.

Step 4, can `--done` check the parent? No code change. Evidence on this orb:
```
$ amp threads export <thread> | keys     -> agentMode, created, creatorUserID, env, id, messages, meta, multiplayerExpiresAt, pinned, title, updatedAt, v
$ ... | meta keys                        -> features, executorType, customAgentDisplay, lastKnownAgentState, deleted, createdOnServer, lastUserMessageAt, workspaceID, projectID, visibility, sharedGroupIDs, sharedUserIDs, agentMode, usesDtw, usesThreadActors   (no parent)
$ amp threads list --json                -> id, title, updated, tree, messageCount            (no parent)
get_thread_status (amp module) result    -> agentMode, agentState, metadata{archived,labels,pinned,sharedUserIDs,snooze,threadID,title,url,viewerOwnsThread,visibility}, preview   (no parent)
```
The fake amp's export (`tests/pstack-pair/bin/amp`) has no parent either. So the docs say to mark a thread done only when update_thread returned without an error and showed the expected parent. `update_thread`'s declared result type is `unknown`, so I could not confirm the field is named `parentThreadID`; the docs do not name it. I did not call update_thread (it changes shared thread state).

## Self-check

figures: every output above is from commands run in this session
scope: only skills/pstack-pair/scripts/pstack_core/commands/nest.py, skills/pstack-pair/scripts/pstack_core/mux/amp.py, skills/pstack-kitchen/SKILL.md, skills/pstack-kitchen/references/master.md, tests/test_pstack_kitchen.py, tests/SPEC.md changed
L012: no Optional from a stdlib call was used unchecked; `uv check --frozen` passed before each step
review.self: the kitchen configures none; I read the diff myself

## Deviations

- Behavior change the master should know: the old master is now pending under the new anchor (step 3 asks for rows for every previous master), so by step 2's rule take-master no longer archives it (`kept <old master>: nest pending`); a later sweep archives it after `nest --done`. `test_the_old_master_thread_is_archived_by_take_master_and_not_before` became `..._archived_once_take_master_has_run_and_it_is_nested`, and one assertion in `test_take_master_moves_the_anchor_...` changed from `archived` to `kept ... nest pending`.
- Step 2 also changed test helpers: `nest_all()` (marks pending rows nested) is called in `finish_unit`, `consultant_with`, `drained_old_thread` and four verifier/consultant tests whose subject is archiving, since every started thread now has a pending row. It adds only `nested` rows.
- Step 1 changed `send_from_the_master` (a test helper) to read the thread id from the last stdout line, since `Amp.send` now prints nest lines too.
- The nest text printed after a start is every pending row, not only the new one.

## Questions

none

## Follow-ups

- `nest.process_prog()` finds the command name by loading the variant from `sys.argv`; a Context handed to `mux/amp.py` would be cleaner but is outside this brief's Scope.
