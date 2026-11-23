# Report 039: roster-handoff-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/039-roster-handoff-fix.md
playbook: review-fix
branch: kitchen-followups-010
head: fc6d096b77343386ce9f5ee5b45320ab6abb35f7
tree: clean

## Changed

Base is unit 010's head c40b7b0c. Three commits:

- 484b957b fix(pstack-pair): `gitstore.py` RULES gets a LOCAL rule for `sessions/master-g<n>-roster.toml` (ignore line `sessions/*-roster.toml`) before the master's `sessions/` rule, so a publish skips it and `gitignore_text()` lists it. The local store's bundle packs by its own `bundle.skipped()` (dotfiles, .pid, .tmp, .lock), not by RULES, so it still carries the snapshot to the next master; a test now says so (the extracted bundle holds the roster bytes).
- f0bfd92a fix(pstack-kitchen), steps 2 and 3 in one commit: `master.py`
  - `relay_roster`: under the git store, after the next master thread starts, `rotate-master` prints `relay: upload_thread_file path=<abs snapshot> destination=<abs snapshot> thread=<next master thread>` and the line saying to upload before ending the turn. Nothing new is printed under the local store.
  - `take_master` calls `restore_roster` after `wait_for_old_master`. A snapshot missing after that wait: under the git store `roster: <path> did not arrive from the previous master; this orb keeps its own roster` and exit 0 with the hand-over completed; under the local store 010's `ToolFailed` is kept.
  - (a) the snapshot is removed when `publish_master`, `new_master` or `record_handover` raises after `snapshot_roster` (the calls moved inside a `try`).
  - (b) the rollback unlink is `Path.unlink(missing_ok=True)` (`drop_snapshot`) and cannot hide the launch error.
  - (c) a dangling roster link and an `OSError` from `target.parent.mkdir` end take-master with `ToolFailed` and its message (`held_roster`, `install_roster`).
- fc6d096b docs: SKILL.md Amp threads bullet and master.md Rotate yourself describe the snapshot, its local-only status under the git store, the upload relay and the "did not arrive" fallback. `tests/SPEC.md` regenerated.

No other file touched. The roster's contents appear in no output, event, decision or branch file.

## Ran

```
$ python3 tests/run.py -k roster       -> 25 tests, 0 failed (worktree check before commit; Verify reruns at the head)
$ python3 tests/run.py -k take_master  -> 15 tests, 0 failed
$ python3 tests/run.py -k gitstore     -> 56 tests, 0 failed (at 484b957b)
$ python3 scripts/check-pstack.py      -> 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check    -> ok: tests/SPEC.md is current, 0 missing
$ kitchen.sh step ... fc6d096b         -> gates: pass (pstack,pstack-docs); step recorded
```

Tests fail without the code (checked by restoring the old `master.py` / `gitstore.py`):
- `test_the_host_roster_snapshot_stays_off_the_branch_and_the_branch_ignores_it` (git store; asserts on the fake origin's tree and `git log -- <path>`, not on the rule) and `test_gitstore_ignores_exactly_the_local_class` fail without the RULES change.
- The (git store, local store) x (snapshot present, missing) table `test_take_master_installs_a_snapshot_that_is_there_and_a_missing_one_ends_only_the_local_store`, the dangling-link and mkdir subtests in 010's table, `test_a_publish_that_fails_after_the_snapshot_leaves_no_snapshot_behind` and `test_a_launch_failure_with_the_snapshot_already_gone_keeps_the_launch_error` fail without the `master.py` changes.
- The local store still delivers the roster: 010's `test_rotate_master_snapshots_the_roster_it_reads_into_the_store` now reads the roster out of the sent bundle.

## Self-check

figures: counts come from the commands shown
acceptance: no `sessions/*-roster.toml` pushed to the branch and the branch .gitignore lists it (the git-store test); the relay line names the next master thread under the git store; local store still delivers the roster (bundle test); take-master under the git store with the snapshot missing prints `did not arrive`, exits 0 and completes the hand-over, with it present installs it (table); the three lows by the tests above
callers: `record_handover` unchanged since 010; `restore_roster` has one caller (`take_master`); `gitstore.classify` gains one rule, and its other callers (`store_files`, `secrets.scan_store`, `check_owned`) treat LOCAL as before
fresh: the gate at fc6d096b ran the whole suite
gate: format, lint and typecheck pass on every commit

## Deviations

- Steps 2 and 3 are in one commit (f0bfd92a), since the tests cover both in the same table.
- Steer 039-s1: my turn ended while the step for fc6d096b was still running. I reran `kitchen.sh step` in the foreground until it recorded.
- Two earlier step checks failed before the final step recorded. 484b957b's gate failed two git-store tests (`test_a_master_rotated_on_the_branch_is_taken_over...` and `test_a_relay_and_a_request_are_pending...`) because take-master errored once the snapshot was no longer on the branch; f0bfd92a fixes exactly that. f0bfd92a's own check then failed `spec_gen --check` (stale SPEC, generated with the docs edit not yet committed); fc6d096b commits both together and passes.
- No review skill is named by `review.self` in `.agents/kitchen.toml`, so none was run on the diff.

## Questions

none

## Follow-ups

- Under the git store the old master must make the printed upload before ending its turn; if the upload tool call is forgotten the next master simply keeps its own roster.
