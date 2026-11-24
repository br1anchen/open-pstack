# Report 049: bundle-and-rotation-fixes-2

status: done
brief: briefs/049-bundle-and-rotation-fixes-2.md
playbook: review-fix
branch: kitchen-followups-047
head: 648f31bfbc25a86d5a5b5c5106ce4d56e60778b0
tree: clean

## Changed

Branch `kitchen-followups-047`, started at unit 047's head 97366025, with one commit per step (each recorded with kitchen.sh step):

1. 3d1d19c4 and e12a8364 mux/amp.py `role_is_idle`: the sidekick is idle only when `.dispatch.generation` equals `.sidekick.generation` (compared as strings), on top of 047's rotation-flag rule. New row in `test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use` ("a fresh generation not yet dispatched to": done report on file, generation bumped, `archive: 0 archived, 0 kept, 2 in use`); it fails on 047's code. The snooze tests' `review_unit` fixture wrote a dispatch with no generation; it now carries the sidekick's, as a real dispatch does (that was the 5 failures the first gate run found).
2. 9e102b4f bundle.py: `tracking_baseline` (no fetch) split out of `origin_baseline` (fetch, then tracking_baseline, '' on failure). `pack` calls `origin_baseline` only for a hand-over (`branches`) and `tracking_baseline` for an evidence send; a send with neither keeps `--remotes`. New test `test_only_a_hand_over_fetches_origin_and_an_evidence_send_is_cut_at_the_tracking_ref` counts `git fetch` calls: plain 0, evidence 0 (baseline = the tracking ref's commit), hand-over 1; it fails on 047's code. 047's evidence tests pass; one was renamed (`..._is_cut_against_origins_default_branch_alone`) with its docstring.
3. 89fd0096 bundle.py `merge_store`: a reply keeps this side's copy of a path it lacks only when this side's copy differs from the base or the base lacks it; otherwise the deletion goes through merge_file. Replaced 047's subtest with `test_a_reply_deletes_a_file_its_own_role_deleted_and_keeps_one_the_master_wrote_after_its_snapshot` on reports/ (the sidekick's): (a) master changed it after the base -> kept; master wrote a new file after the base -> kept; (b) sidekick deleted its own unchanged file -> removed. The (b) subtest fails on 047's code.
4. 3fe31eae bundle.py: `fetch_origin` takes `timeout_env` and `timeout_default`; the `--unshallow` fallback in `fetch` uses PSTACK_BUNDLE_UNSHALLOW_TIMEOUT_S (600). New test patches the shallow check and asserts 11, no timeout for 0, and 600 by default; it fails on 047's code.
5. 02787dd2 errors.py `RotateRefused` docstring names a master hand-over and a role's rotation. setup.py: `permission='none'` is forced only when no `--` args were passed (the recorded start args are reused). One test row in the no-roster spawn test: recorded start gives permission_mode `native-args`; a call with its own `-- --model other/model` starts that model and does not (it fails on 047's code).
6. 648f31bf SKILL.md (pstack-kitchen) says what `pack` does by kind of send, the receiver's bounded fetch (and the unshallow timeout), and the new reply-deletion rule; tests/SPEC.md regenerated (each commit carries its own regeneration so the spec check passed at every step).

## Ran

```
$ python3 tests/run.py   (the step 6 gate, at head 648f31bf)   -> gates: pass (pstack,pstack-docs)
$ python3 tests/run.py -k bundle / rotat / archive / spawn   (run at each step)   -> 0 failed
$ python3 scripts/check-pstack.py; python3 tests/spec_gen.py --check; ruff format --check; ruff check; uv check --frozen   -> pass at each step
```
Order of events: the first run of the step 1 gates failed with 5 snooze/status tests (a fixture without `dispatch.generation`); fixed in its own commit and recorded.

## Self-check

figures: counts and results come from the commands above and the gates.tsv rows for each step
step 3 note: "or the master owns the path" in the brief is already covered by the earlier branch of merge_store (is_master_owned keeps this side's copy), so it is not repeated in the new condition
verdicts/ is owned by verifier and master in gitstore.RULES, so the old verifier subtest of 047 (base holds the file, reply lacks it) now removes the file; that is case (b) by the rule as the brief states it, and it is not in the replacement test
scope: nest.py and snooze.py untouched; no push, no PR
review.self: none named by the kitchen

## Deviations

none

## Questions

none

## Follow-ups

none
