# Report 043: snooze-role-threads-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/043-snooze-role-threads-fix.md
playbook: review-fix
branch: kitchen-followups-038
head: e82b720a9dad0c9a9a991a9c92737db9a67d4923
tree: clean

## Changed

Base is unit 038's head bee0dc78. Two commits: de00010d, `fix(skills/pstack-pair)` (all of the below), and e82b720a, which moves the two `--done` refusals in `record` from `Die` to `ToolFailed` (the EndsItself guard failed the first gate on them) and puts the `snooze` command's export cache in one `with` block inside `snooze` itself:

- `gitstore.py`: `snooze\.tsv` is in the master's files rule. Under the git store a master publish now puts `snooze.tsv` on the branch.
- `consult.py` `send_plan`: every recipient is checked with `amp.require_awake` before the first one gets the plan, so a snoozed consultant no longer leaves the round half sent.
- `commands/snooze.py`: `snoozable` became `why_not`, which returns the reason. `--done` refuses a master (`snooze: <T> is a master thread; masters are never snoozed`) or a thread that is not snoozable now (`snooze: <T> is not snoozable now (<reason>)`) before it writes any row; `--wake` is unchecked. A thread already recorded snoozed is skipped.
- `mux/amp.py`: `exports_cached()` scopes a per-command cache of `amp threads export`. It lives for the `with` block only. `snooze`, `snooze.status_lines` and `status` run inside it, so each thread is exported once per command.
- Docs: SKILL.md and master.md say `PSTACK_SNOOZE_ROLES=0` stops new snoozing only and a thread snoozed earlier still needs `update_thread(snooze="off")` plus `snooze --wake`; SKILL.md names `snooze.tsv` with `nest-pending.tsv` as published master files and the `--done` refusal. tests/SPEC.md regenerated.

## Ran

```
$ python3 tests/run.py -k snooze -k gitstore   -> 56 tests, 0 failed  (after the new tests)
$ python3 tests/run.py -k snooze -k status     -> 23 tests, 0 failed
$ python3 scripts/check-pstack.py              -> 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check            -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check skills tests -> 106 files already formatted
$ uv run --frozen ruff check                   -> All checks passed!
$ uv check --frozen                            -> All checks passed!
$ python3 tests/run.py -k snooze -k status -k EndsItself -k FunctionSize -k discuss -k gitstore -> 56 tests, 0 failed
$ kitchen.sh step ... e82b720a                 -> gates run the whole suite (see Deviations)
```

Tests:
- `test_gitstore_ownership_table_classifies_every_path_of_a_real_store_layout` has `snooze.tsv` in the layout and asserts its class is MASTER.
- `AmpGitKitchenTests.test_a_snooze_done_and_a_master_publish_put_snooze_tsv_on_the_branch`: `snooze --done`, `--wake`, a dispatch, then the fake origin's branch holds `snooze.tsv` with the snoozed row.
- `test_a_plan_round_to_an_awake_sidekick_and_a_snoozed_consultant_sends_nothing_until_it_is_woken`: exit 5, no message to the sidekick, no `send-plan` event; after `--wake` both receive the PLAN.
- 038's `--done`/`--wake` test now refuses a master, a working thread and a unit with no review note, with no row written.
- `test_status_exports_each_role_thread_once` counts the fake amp's export calls per thread.

Mutation checks (not committed): without `snooze\.tsv` in the rule the git-store test fails with `'snooze.tsv' not found in {...}`; without the `require_awake` loop the plan-round test fails with `2 != 1` sidekick messages.

## Self-check

figures: counts come from the commands shown
acceptance: all four steps have a test that fails without the change (steps 1 and 2 mutation-checked)
callers: `snoozable` had callers only in `snooze.py`; they use `why_not`. `export` behaves as before outside `exports_cached`.
fresh: the gate at e82b720a ran the whole suite
gate: format, lint and typecheck pass; no change to nest.py, no sleeps in tests

## Deviations

- One step at the final head e82b720a. The step at de00010d failed one gate (EndsItself guard on `Die` raised from `record`); e82b720a fixes it, and I recorded no step for de00010d.
- `status` as a whole runs inside `exports_cached` (status.py is in Scope), since `thread_rows`, `assess` and the snooze lines each exported the same threads.
- No `review.self` skill is named in `.agents/kitchen.toml`, so none was run on the diff.

## Questions

none

## Follow-ups

none
