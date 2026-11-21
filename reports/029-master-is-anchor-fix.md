# Report 029: master-is-anchor-fix

status: done
brief: briefs/029-master-is-anchor-fix.md
playbook: review-fix
branch: master-is-anchor
head: 7df08b7b4826868f574f0601610638a4f2ae7aaa
tree: clean

## Changed

Branch `master-is-anchor` is unit 027's head a0eb1b7 (`git fetch origin kitchen-code/kitchen-followups/unit-027 && git checkout -B master-is-anchor FETCH_HEAD`), not rebased; four commits on top, each recorded with `kitchen.sh step`.

- 56c0896 (step 1) `nest.anchor_title` assigns the title (`doc['anchor'].update(title=title)`) instead of `setdefault`, so an empty stored title is replaced once the thread can say one. Test `test_take_master_saves_the_anchor_title_init_could_not_read`: pair.json `.anchor.title` set to `''` (what init writes when export has no title), the fake export then gives `Run anchor`, take-master renames the new master to it and pair.json holds it.
- 2b4bf50 (step 2) `nest.take_anchor`: `doc.setdefault('anchor', {})`, and the old anchor is appended to `.anchor.previous` only when non-empty and different from the new thread. `mux/amp.py` `record_started`: the master branch calls `nest.anchor` only when `<store>/pair.json` exists, as `record_pending` does. Tests: `test_take_master_from_an_anchor_with_no_thread_records_no_empty_previous_anchor` (anchor `{thread: ''}` before take-master; after it `.anchor == {thread: master2, title}` with no `previous`), and `test_a_master_thread_started_for_a_store_with_no_pair_json_is_only_recorded` (in-process `amp.record_started` on a bare store; threads.json records it as master and no nest-pending.tsv is written).
- 7c2897e (step 3) `master.take_anchor` returns `anchor: <T> is the run's top-level anchor; run kitchen.sh nest <store>` (no title) when the rename warned; the `warn:` line still goes to stderr. The failed-rename test now asserts that line and that no `top-level anchor "` text appears.
- 7df08b7 (step 4, tests only) `test_take_master_nests_a_live_consultant_under_the_new_master` (consultant started before the rotation: pending under the old master, then `nest-pending: <consultant> (consultant) under <master2>`, sidekick too), and `test_a_second_take_master_adds_no_nest_rows_and_leaves_the_anchor` (second take-master prints `no hand-over open`, exit 0, nest rows and `.anchor` equal).

tests/SPEC.md regenerated in each commit that adds a test.

## Ran

Scratch revert (the source files of each step stashed, the new test kept), each fails without its fix and passes with it:

```
$ git stash -- skills; python3 tests/run.py -k saves_the_anchor_title   -> 1 tests, 1 failed ; with fix: 1 tests, 0 failed
$ (same)           -k empty_previous                                    -> 1 failed ; with fix: 0 failed
$ (same)           -k is_only_recorded                                  -> 1 failed ; with fix: 0 failed
$ (same)           -k rename_fails                                      -> 1 failed ; with fix: 0 failed
```

Steps 1 to 4 each recorded (gates pass: pstack, pstack-docs; step 4 pstack). After step 4:

```
$ python3 tests/run.py -k take_master -> 15 tests, 0 failed
$ python3 tests/run.py -k anchor      -> 20 tests, 0 failed
$ python3 tests/run.py -k nest        -> 15 tests, 0 failed
$ uv run --frozen ruff format / ruff check / uv check --frozen -> clean
```

The full Verify block is run by `kitchen.sh finish` at this head.

## Deviations

- The brief's step 1 test says "init with a fake export that has no title". setUp has already run init with a title, so the test sets pair.json `.anchor.title` to `''` (the state init leaves) and writes the fake's `amp-title-<thread>` file; the take-master path under test is the same.
- Step 2's two guards (no old anchor, no `doc['anchor']`) are not reachable through the CLI when the anchor holds a thread, so the test uses `.anchor.thread == ''`, which `nest.anchor` cannot repair (`setdefault` keeps `''`); that same `setdefault('thread', ...)` in `nest.anchor` is left as is (outside the brief).

## Questions

none

## Self-check

figures: every result above is from a command run in this checkout
Acceptance: each of steps 1 to 3 has a test shown failing without its fix; the take_master, anchor and nest tests pass; full suite and the rest of Verify run by finish
no push, no PR, no sleeps or timing asserts added; no files outside Scope written (nest.py, mux/amp.py, master.py, tests/test_pstack_kitchen.py, tests/SPEC.md)
note: the installed build refuses `finish` from ~/.agents/skills under the git store (it stamps pair.json); I ran kitchen.sh from this checkout's skills/ instead
