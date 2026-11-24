# Verdict 040: nest-every-start

status: clean
units: 040
range: eb8228f9a7dc6466e40f8674913b3bea3e8c52eb..6b2551f9b05531c632a2d3e958731fb76f5c4b87
verifier: amp claude-code claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

(Observations, not findings: the nest text a start prints is every pending row, not only the new one, as the report's Deviations say; the old master is now kept by the sweep until nested, as the report and SKILL.md say; `nest.process_prog()` reads sys.argv, noted by the report as a follow-up.)

## Evidence

Scratch: detached worktree at 6b2551f9 (`git rev-parse HEAD` printed `6b2551f9b05531c632a2d3e958731fb76f5c4b87`); `kitchen.py ... setup` printed `setup: ok (1 commands, ...)`. All commands ran with PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups, each in a fresh shell from the scratch root.

E1, brief commands 1-4 at the head:
```
$ python3 tests/run.py -k nest        -> 21 tests, 0 failed, 34s wall with 4 jobs
$ python3 tests/run.py -k archive     -> 25 tests, 0 failed, 86s wall with 4 jobs
$ python3 tests/run.py -k take_master -> 18 tests, 0 failed, 35s wall with 4 jobs
$ python3 tests/run.py -k anchor      -> 21 tests, 0 failed, 21s wall with 4 jobs
```

E2, brief command 5, whole suite:
```
$ python3 tests/run.py
2028 tests, 0 failed, 712s wall with 4 jobs
exit 0
```

E3, brief commands 6-10:
```
$ python3 scripts/check-pstack.py            -> 65 skills checked; all 48 previous skills covered   (exit 0)
$ python3 tests/spec_gen.py --check          -> ok: tests/SPEC.md is current, 0 missing   (exit 0)
$ uv run --frozen ruff format --check        -> 111 files already formatted   (exit 0)
$ uv run --frozen ruff check                 -> All checks passed!   (exit 0)
$ uv check --frozen                          -> All checks passed!   (exit 0)
```

E4, repro at the parent. A second scratch at eb8228f9 with the head's tests/test_pstack_kitchen.py copied in, the four new behaviour tests run there (each fails at the parent; each passes at the head in E1/E2):
```
== base: test_every_command_that_starts_a_thread_prints_its_nest_instructions
AssertionError: 'nest-pending: T-00000000-0000-4000-8000-000000000004 (sidekick) under T-aaaaaaaa-0000-4000-8000-000000000000' not found in [... 'amp demo-sidekick (T-...04): Load the pstack-kitchen skill ...', 'archive: 0 archived, 0 kept, 3 in use', ...]
FAILED (failures=4)        (spawn, verify, consultant, rotation: one failure per start)
== base: test_the_sweep_keeps_a_thread_with_a_pending_nest_row_until_nest_done
AssertionError: 'kept T-...09: nest pending (nest it first;' not found in '... archived T-...09 (verifier): threads --archive\narchived 1 thread(s)\narchive: 1 archived, 0 kept, 2 in use\n'
FAILED (failures=1)
== base: test_nest_lists_an_archived_pending_thread_with_the_unarchive_parent_rearchive_calls
AssertionError: 'nest-pending: T-...09 (sidekick, archived) under T-aaaaaaaa-...' not found in ['nest-pending: T-...01 (sidekick) under ...', 'nest-pending: T-...09 (sidekick) under ...', 'nest: call update_thread(thread=<T>, parent_thread=<anchor>) for each, then kitchen.sh nest ... --done <thread>...']
FAILED (failures=1)
== base: test_take_master_re_parents_every_thread_of_the_run_to_the_new_master_and_none_stays_under_an_old_one
FAILED (failures=7)
```

E5, acceptance 1, read from the test at the head (tests/test_pstack_kitchen.py `test_every_command_that_starts_a_thread_prints_its_nest_instructions`, lines 14799-14826): it runs the real kitchen.sh (fake amp) for spawn, verify, consultant and a rotation inside dispatch and asserts, for each thread, the line `nest-pending: <T> (<role>) under <anchor>` and, after it, `nest: call update_thread(thread=<T>, parent_thread=<anchor>) for each, then kitchen.sh nest <store> --done <thread>...`. It passes at the head (E1), fails at the parent (E4).

E6, acceptance 2, 3: the sweep test (`kept <verifier>: nest pending (nest it first; ...`, then archived after `nest --done`) and the nest-listing test (`(<role>, archived)` plus the unarchive, parent, re-archive calls) both pass at the head and fail at the parent (E4). The diff (skills/pstack-pair/scripts/pstack_core/mux/amp.py `assess`) returns NEST_PENDING before any archive decision for a thread with a pending row; `nest.instructions` prints `update_thread(thread=<T>, archived=false)`, the parent call, then `archived=true`.

E7, acceptance 4: `test_take_master_re_parents_every_thread_of_the_run_to_the_new_master_and_none_stays_under_an_old_one` (two earlier masters, two verifiers, an archived sidekick, setUp's sidekick) fails at the parent with 7 failures and passes at the head. The diff to `nest.take_anchor` adds a row under the new anchor for live roles, `run_threads(store, archived=True)` and `previous_masters` (`.anchor.previous` and `.master.sessions`).

E8, acceptance 5, the `--done` question. The report gives the keys of `amp threads export`, `amp threads list --json` and `get_thread_status`, none with a parent field; the brief's range holds no code for it and the docs follow it. SKILL.md and master.md (`git diff eb8228f9..HEAD -- skills/pstack-kitchen`): "`--done` cannot check the parent: neither `amp threads export` nor `get_thread_status` carries a parent field, so mark a thread done only when `update_thread` returned without an error and its result shows the expected parent." I did not call update_thread (shared state); I did not re-derive the report's key list from a live amp, so that part rests on the report's output.

E9, testing standard: the new tests contain no sleep and no elapsed-time assert (`git diff ... | grep -nE 'sleep|time\.|timeout'` found only a pre-existing `timeout=60` on the as_thread subprocess, which bounds it); AmpKitchenTests.mux_env sets PSTACK_MUX and AMP_THREAD_ID itself and the run is hermetic under the harness; the source changes (nest.py, amp.py) are in commits with the tests that pin them (b6ff274e, b48455eb, 1b8c37d1 each touch tests/ and tests/SPEC.md); spec_gen --check reports 0 missing. New helper `nest_all` states its reason in its docstring; the `# fmt: skip` markers only keep line layout. Step 5 of the bug-fix playbook: in the range the tests land in the same commits as each fix (no test-first commit), noted below.

## Flows driven

pstack (kitchen under Amp threads, fake amp): spawn, verify, consultant, a rotation in dispatch, the sweep, `nest`, `nest --done`, take-master, driven through the real kitchen.sh by the unit's tests, E1/E2/E4-E7. pstack-docs: check-pstack and the SKILL.md / master.md text, E3/E8. A live Amp (real update_thread) is not driveable from a verifier.

## Folded Acceptance

none

## Not covered

- A real `update_thread` call on a live thread, and whether it returns a parent field, was not exercised (it changes shared thread state); the `--done` docs rest on the report's key listing of the real amp CLI, which I did not repeat.
- Bug-fix playbook step 5 (a failing test lands before the fix in history): each of the four commits carries its test and its fix together; a failing-before-fixing commit order does not exist. Shown instead by running the head's tests at the parent (E4). Say so to the review.

## Lessons

none
