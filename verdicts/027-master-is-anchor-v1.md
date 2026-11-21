# Verdict 027: master-is-anchor

status: clean
units: 027
range: 7218f3112db91d4448b20d708b5c92117b6543db..a0eb1b71ea08de94621e9df7936996335f3ffdf3
verifier: amp claude-code claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

Observations (not findings): (1) the new test `test_nest_lists_a_top_level_row_...` slices `splitlines()[3:]` without a comment saying the first three lines are the `three_pending` rows; it is a positional slice, not a mask. (2) The old take-master archive line prints the old master's thread, as before; no behaviour change.

## Evidence

Acceptance, line by line.

**E1. Brief commands 1-4 (focused suites), scratch at a0eb1b7, PSTACK_KITCHEN_ROLE=verifier.**
```
$ python3 tests/run.py -k nest          -> 13 tests, 0 failed
$ python3 tests/run.py -k anchor        -> 17 tests, 0 failed
$ python3 tests/run.py -k take_master   -> 11 tests, 0 failed
$ python3 tests/run.py -k rotate_master -> 12 tests, 0 failed
```

**E2. Commands 5-10.**
```
$ python3 tests/run.py                          -> 1930 tests, 0 failed, 660s wall with 4 jobs (exit 0)
$ python3 tests/spec_gen.py --check             -> ok: tests/SPEC.md is current, 0 missing
$ python3 scripts/check-pstack.py               -> 65 skills checked; all 48 previous skills covered (exit 0)
$ uv run --frozen ruff format --check           -> 111 files already formatted
$ uv run --frozen ruff check                    -> All checks passed!
$ uv check --frozen                             -> All checks passed!
```
The Prove block's `kitchen.py ... setup` printed `setup: ok (1 commands, ...)`. Its `gate pstack behavioral` line is `python3 tests/run.py`: it ran and printed `pstack behavioral: pass` / `exit 0 543.5s python3 tests/run.py`.

**E3. Independent drive of init + rotate-master + take-master against the fake amp** (a probe subclass of AmpKitchenTests in .amp/in/orb-backup/v027_probe.py, written outside the scratch; a verification is open, so the verifier is a live role thread). Excerpts, store shortened:
```
--- nest after rotate (before take):
nest-pending: T-...01 (sidekick) under T-aaaaaaaa-...000      <- no row for the next master (T-...03)
nest-pending: T-...02 (verifier) under T-aaaaaaaa-...000
--- take-master:
archived T-aaaaaaaa-...000 (master): take-master has run
closed the old master demo-master (amp:T-aaaaaaaa-...000)
master: demo-master in amp:T-...03 (generation 2)
anchor: T-...03 is the run's top-level anchor "fake title of T-aaaaaaaa-...000"; run kitchen.sh nest <store>
--- anchor: {"thread": "T-...03", "title": "fake title of T-aaaaaaaa-...000", "previous": ["T-aaaaaaaa-...000"]}
--- renamed: T-...03	fake title of T-aaaaaaaa-...000
--- archived: T-aaaaaaaa-...000
--- nest:
nest-pending: T-...03 (master) top-level
nest-pending: T-...01 (sidekick) under T-...03
nest-pending: T-...02 (verifier) under T-...03
nest: call update_thread(thread=<T>, parent_thread=<anchor>) for each, then kitchen.sh nest <store> --done <thread>...
nest: call update_thread(thread=<T>, parent_thread=null) for each top-level row, then kitchen.sh nest <store> --done <thread>...
--- nest --done <all three>:
nested: T-...03 top-level / nested: T-...01 under T-...03 / nested: T-...02 under T-...03
nest: nothing pending
```
This shows: `.anchor.thread` is the new master and `.anchor.previous` holds the old one; the fake recorded the rename to the anchor title; `nest` lists the new master `top-level` with the `parent_thread=null` instruction, and the live sidekick (and open verifier) under the new master; no row nests the new master under the old master; the old master is archived.

**E4. init records {thread, title}.** `test_init_records_the_masters_own_thread_and_its_title_as_the_runs_anchor` passes (E1, -k anchor) and asserts `{'thread': AMP_MASTER, 'title': 'fake title of <master>'}`. Real surface: `amp threads export <thread>` on this orb returns a `title` key (`'kitchen-followups-verifier'`), and `amp threads rename --help` prints `amp threads rename [options] <thread> <newName>`, the argument order `mux/amp.py rename_thread` uses. The `update_thread` tool's schema documents `parent_thread: string | null` ("or null to make it top-level"), so the instruction line is a callable form.

**E5. Scratch reverts** (a second scratch worktree at a0eb1b7, one line changed, `git diff` shown, restored and `git diff --quiet` clean afterwards; script .amp/in/orb-backup/v027-mutate.sh):
```
mutation A: nest.py:117  -            holder['thread'] = thread   +            pass
  FAIL test_take_master_moves_the_anchor_to_the_new_master_and_renames_and_lists_it_top_level
  AssertionError: 'T-aaaaaaaa-0000-4000-8000-000000000000' != 'T-00000000-0000-4000-8000-000000000002'
  1 tests, 1 failed

mutation B: nest.py:127  -    add_pending(store, thread, 'master', TOP_LEVEL)   +    add_pending(store, thread, 'master', old)
  FAIL test_take_master_moves_the_anchor_... AssertionError: 'nest-pending: T-...02 (master) top-level' not found in
  ['nest-pending: T-...02 (master) under T-aaaaaaaa-...000', 'nest-pending: T-...01 (sidekick) under T-...02', ...]
  1 tests, 1 failed

mutation C: mux/amp.py:320  -    if role_of(name) == 'master':   +    if role_of(name) == 'never':   (a master is nested again under the anchor)
  FAIL test_the_first_rotation_of_a_store_without_an_anchor_anchors_on_the_old_master
  FAIL test_take_master_moves_the_anchor_... ("('T-...02', 'master', 'T-aaaaaaaa-...000', 'pending') unexpectedly found in [...]")
restored: clean (all three)
```
A fails if take-master leaves `.anchor.thread` on the old master; B and C fail if the new master gets a row under the old anchor.

**E6. Failed rename.** `test_take_master_whose_rename_fails_warns_and_still_takes_over` passes (E1, -k take_master): with `$FAKE/amp-rename-fail` set it asserts `warn: amp threads rename <master2> failed` in the output, anchor moved, generation 2, and the harness `as_thread` call asserts exit 0.

**E7. Existing tests unchanged.** `git diff 7218f31..a0eb1b7 -- tests/test_pstack_kitchen.py`: besides the new cases, three existing tests changed: the init test (the anchor-shape assertion the brief names), `test_every_thread_the_kitchen_starts_is_nest_pending_...` (renamed; it no longer expects a `master` row under the anchor, which is the brief's step 2) and `test_a_rotation_keeps_the_anchor_through_every_generation` (replaced by `test_a_rotation_moves_the_anchor_only_when_the_next_master_takes_over`; the old one asserted the rule this unit reverses), plus `test_the_first_rotation_of_a_store_without_an_anchor_anchors_on_the_old_master` (its master-row assertion became an assertNotIn). All other nest and take-master tests pass untouched (E1, E2). The report names the second and third as Deviation 2; the second and the fourth are consequences of Acceptance bullets "No row nests the new master under the old master". I count them as within the brief's intent, not a regression.

**E8. Standing check on tests.** The new tests add no regex mask, filter or tolerance; they run `kitchen.sh` through the existing `run_sh`/`as_thread` helpers, which bound each subprocess. The fake `amp threads rename` returns at once. No `time.sleep` or elapsed-time assert was added in the range (`git diff 7218f31..a0eb1b7 -- tests | grep -c '^+.*sleep'` is 0).

## Flows driven

- profile pstack: init, rotate-master, take-master, nest and nest --done driven on the fake amp through the repo's own harness (E3), and the repo's full suite (E2).
- profile pstack-docs: `scripts/check-pstack.py`, `tests/spec_gen.py --check` (E2); I read the SKILL.md bullet and `references/master.md` hunks: they state that the anchor is the current master, top-level, with the anchor title, that `take-master` moves it, and that the master runs `nest`, makes the calls including `parent_thread: null`, then `nest --done`.

## Folded Acceptance

none

## Not covered

- A rotation against a real Amp thread tree (real `amp threads rename` on a real master, real `update_thread(parent_thread=null)`): not run, since a rotation would rename and re-parent real threads of this run. I checked only that the CLI `rename` and the tool's null parent exist with the shapes the code uses (E4).
- The stale-title case (the old anchor's thread archived and `amp threads export` failing when a store without a title takes over): the report lists it as a follow-up; I did not drive it.

## Lessons

L012: held (the diff adds `text_at`/`peek` reads and `r.returncode`; no stdlib Optional result is used unchecked, and `uv check --frozen` passes).
