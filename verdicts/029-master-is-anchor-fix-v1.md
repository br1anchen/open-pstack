# Verdict 029: master-is-anchor-fix

status: clean
units: 027 029
range: 7218f3112db91d4448b20d708b5c92117b6543db..7df08b7b4826868f574f0601610638a4f2ae7aaa
verifier: claude-code, claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

Notes (not findings):
- `kitchen.sh scratch` refused from this orb's thread (`pair.json: belongs to the master`), so the scratch was a plain `git worktree add --detach` at the head; reverts ran in a second detached worktree at the same head. Neither touched the store.
- Step 2's `doc.setdefault('anchor', {})` guard has no test of its own; `nest.anchor` runs first in `take_anchor` and creates the anchor, so it is not reachable through the CLI (the report says the same under Deviations). The empty-previous test and the bare-store test pin the other two parts of step 2.

## Evidence

Brief commands at the head (env: PSTACK_KITCHEN_ROLE=verifier, PSTACK_KITCHEN_RUN=kitchen-followups, PSTACK_MUX unset; the tests scrub their own):

```
$ python3 tests/run.py -k nest          -> 15 tests, 0 failed
$ python3 tests/run.py -k anchor        -> 20 tests, 0 failed
$ python3 tests/run.py -k take_master   -> 15 tests, 0 failed
$ python3 tests/run.py -k rotate_master -> 12 tests, 0 failed
$ python3 tests/run.py                  -> 1935 tests, 0 failed, 592s wall with 4 jobs   (exit=0)
$ python3 tests/spec_gen.py --check     -> ok: tests/SPEC.md is current, 0 missing
$ python3 scripts/check-pstack.py       -> 65 skills checked; all 48 previous skills covered
$ uv run --frozen ruff format --check   -> 111 files already formatted
$ uv run --frozen ruff check            -> All checks passed!
$ uv check --frozen                     -> All checks passed!
$ python3 -I kitchen.py --repo <scratch> setup -> setup: ok (1 commands, ...)
```
The packet's `gate pstack behavioral` is `python3 tests/run.py` (.agents/kitchen.toml line 29), which is the full-suite line above.

Scratch revert of each unit-029 step (source of that commit reverted with `git diff C~1 C -- skills | git apply -R`, new test kept; restored afterwards, `git status` clean):

```
step 1, 56c0896 (anchor_title setdefault -> update), -k saves_the_anchor_title
FAIL test_take_master_saves_the_anchor_title_init_could_not_read
AssertionError: '' != 'Run anchor'            1 tests, 1 failed

step 2, 2b4bf50 (previous guard), -k empty_previous
FAIL test_take_master_from_an_anchor_with_no_thread_records_no_empty_previous_anchor
AssertionError: {... 'title': 'Run anchor', 'previous': ['']} != {... 'title': 'Run anchor'}   1 tests, 1 failed

step 2, 2b4bf50 (record_started pair.json check), -k is_only_recorded
FAIL test_a_master_thread_started_for_a_store_with_no_pair_json_is_only_recorded
pstack_core.errors.MissingStore: no pair.json in /tmp/.../bare-store     (raised by nest.anchor from record_started)   1 tests, 1 failed

step 3, 7c2897e (anchor line after a failed rename), -k rename_fails
FAIL test_take_master_whose_rename_fails_warns_and_still_takes_over
AssertionError: "anchor: T-...0002 is the run's top-level anchor; run kitchen.sh nest ..." not found in '...anchor: T-...0002 is the run\'s top-level anchor "fake title of T-aaaa..."; ...warn: amp threads rename ... failed: Error: rename failed\n'   1 tests, 1 failed
```
Each reverted file restored with `git checkout -- skills`; with the fixes the same tests pass (the -k runs above).

Unit 027 Acceptance, scratch reverts (take_master tests, 15 tests):

```
revert A: nest.take_anchor `holder['thread'] = thread` -> `pass`
FAIL test_take_master_from_an_anchor_with_no_thread_records_no_empty_previous_anchor
FAIL test_take_master_moves_the_anchor_to_the_new_master_and_renames_and_lists_it_top_level
FAIL test_take_master_whose_rename_fails_warns_and_still_takes_over
15 tests, 3 failed

revert B: add_pending(store, thread, 'master', TOP_LEVEL) -> add_pending(store, thread, 'master', old)   (new master gets a row under the old anchor)
FAIL test_take_master_moves_the_anchor_to_the_new_master_and_renames_and_lists_it_top_level
15 tests, 1 failed
```

Code read at the head (`git diff 7218f31 HEAD -- skills/*/scripts`): `init.py` writes `.anchor = {thread, title}`; `nest.take_anchor` sets `.anchor.thread` to the new master, appends the old one to `.anchor.previous`, calls `amp.rename_thread(thread, title)`, adds a `top-level` row for the new master and nest-pending rows for each live non-master thread under it; `kitchen_core/master.take_anchor` writes `warn: ...` to stderr on a failed rename and take-master still returns normally (the rename-fails test asserts exit 0, the `warn:` line and the title-free `anchor:` line); `archive_retired` still archives the old master. `nest` lists `top-level` rows with `update_thread(... parent_thread=null)`.

Test hygiene (standing check): `git diff` of tests for 56c0896~1..7df08b7 contains no `sleep`, timeout assert, regex mask or new subprocess; the new tests drive the existing fake `amp` via `kitchen.sh`/in-process calls and assert on observable output and pair.json.

## Flows driven

- pstack (Amp threads mux, fake amp): the rotation flow `init` -> `rotate-master` -> `take-master` -> `nest` is driven by `test_take_master_moves_the_anchor_to_the_new_master_and_renames_and_lists_it_top_level`, `test_take_master_nests_a_live_consultant_under_the_new_master`, `test_a_second_take_master_adds_no_nest_rows_and_leaves_the_anchor` and `test_take_master_whose_rename_fails_warns_and_still_takes_over` (all in the `-k take_master` and full runs above). A real Amp thread rename and re-parent was not driven; only the fake `amp` was available to the tests.
- pstack-docs: no user-facing flow in the range's docs; check-pstack.py and spec_gen --check pass.

## Folded Acceptance

none (the packet has no folded-unit sections; 027 and 029 are both named units)

## Not covered

- Unit 027 Acceptance "`.anchor` is `{thread, title}` after init" and "old master archived, as today": proved by the existing init/take-master tests passing at the head (the init anchor-shape assertion was the one allowed change), not by a separate drive.
- A real `amp threads rename` against live Amp (fake only).

## Lessons

L012: held (`uv check --frozen` passes at the head; the nest.py/amp.py changes add no unchecked Optional use).
