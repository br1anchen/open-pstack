# Report 027: master-is-anchor

status: done
brief: briefs/027-master-is-anchor.md
playbook: feature
branch: master-is-anchor
head: a0eb1b71ea08de94621e9df7936996335f3ffdf3
tree: clean

## Changed

Based on origin/main (7218f31), as the brief says. Four commits, all recorded; steps 1 and 2 as their own, step 3 covers the last three commits.

- 8b4566c: `.anchor` is `{thread, title}`. `init` records the master's thread and its title (`mux/amp.py thread_title`, from `amp threads export` .title, empty when export fails). `nest.py`: a row whose anchor cell is `-` is top-level (`TOP_LEVEL`); `nest` prints `nest-pending: <T> (master) top-level` and an `update_thread(thread=<T>, parent_thread=null)` line; rows with an anchor print exactly as before. `pending()` takes a thread's newest pending row and a nested row closes only its own (thread, anchor) pair, so a role that moves to a new anchor is pending again. `anchor_title()` reads a missing title lazily. Fake amp: `threads export` has a `.title`, `threads rename` is recorded in $FAKE/amp-renamed (fails when $FAKE/amp-rename-fail exists).
- a445f19: `record_started` records no nest row for a master (a store with no anchor yet settles it on the old master there).
- 8c861ee, then a0eb1b7: `take-master` (kitchen_core/master.py `take_anchor`, `nest.take_anchor`) moves `.anchor.thread` to the caller, appends the old one to `.anchor.previous`, renames the thread to the title (`mux/amp.py rename_thread`), records a `-` row for it and a fresh row under it for each live role thread (sidekick, consultant, open verifier, from `live_threads`), and prints `anchor: <T> is the run's top-level anchor "<title>"; run kitchen.sh nest <store>`. A failed rename prints `warn:` on stderr, exit 0. It runs before `archive_retired`, whose rule is unchanged.
- 7e48a37: docs. SKILL.md "A run reads as one tree" bullet and the `.anchor` store-table row; master.md nest paragraph (`parent_thread: null`) and the `MASTER` section; SPEC.md regenerated.
- a0eb1b7: `take_master` was 82 body lines, over the repo's 80-line guard (FunctionSizeGuardTests), after my edit; the old-pane close moved into `close_old_pane`, the anchor part into `take_anchor`. Also, `test_a_rotation_keeps_the_anchor_through_every_generation` asserted the old rule; it is now `test_a_rotation_moves_the_anchor_only_when_the_next_master_takes_over`.

## Ran

New and changed tests, all in AmpKitchenTests: init records `{thread, title}`; nest lists a `top-level` row with the null-parent line and `--done` closes it; a thread nested under one anchor is pending again under the next; the every-thread test now expects no row for the next master; take-master moves the anchor, renames, lists the new master top-level and the sidekick under it, archives the old master, and no row nests the new master under the old; a failing rename warns and exits 0.

Scratch reverts (each restored, `git diff` clean afterwards):
```
mutation A (take_anchor no longer sets holder['thread']):     test_take_master_moves_the_anchor_... FAIL
mutation B (new master's row is under the old anchor, not -): test_take_master_moves_the_anchor_... FAIL
mutation C (record_started nests a master):                   test_every_role_thread_the_kitchen_starts_... FAIL, and the first-rotation test FAIL
```
Gates: step 1 pass (pstack,pstack-docs), step 2 pass, step 3 pass at a0eb1b7 (after a first run that failed on the two items above). ruff format --check, ruff check, uv check --frozen, spec_gen --check, check-pstack.py pass.

## Self-check

Lessons: L012 held (no Optional stdlib result added; uv check --frozen passes).
The run's pinned build still lacks 507870a, so I ran step/notes from a worktree of kitchen-followups (.amp/in/orb-backup/kf-tools) as in unit 026. A worktree at that path stays in this orb; remove with `git worktree remove`.
No time.sleep or elapsed-time assert added. No push, no PR.

## Deviations

1. Step 3 and step 4 (docs) share one recorded step, since `step` requires the sha to be HEAD and I had committed both; commits are separate.
2. A pre-existing test, `test_a_rotation_keeps_the_anchor_through_every_generation`, asserted the opposite of the brief and was replaced (not just the init test the brief names).
3. `take_master` was refactored into two helpers to stay under the size guard; behavior is unchanged.
4. `pending()` no longer reports a thread whose only row is nested under a different anchor than its newest; this is needed for role threads to re-nest, and the existing nest tests pass unchanged.

## Questions

none

## Follow-ups

- A store whose anchor had no title when take-master runs reads it lazily from the old master's thread, which may already be archived; if export then fails, the new master is not renamed and a warning says so.
- Role threads archived by a rotation keep rows under old anchors; they are ignored once nested or retired.
