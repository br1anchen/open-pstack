# Report 046: snooze-stop-and-done

status: done
brief: briefs/046-snooze-stop-and-done.md
playbook: review-fix
branch: kitchen-followups-046
head: 11d2874b62ef48ef9305c3e772e388e2cf66e2e6
tree: clean

## Changed

Branch `kitchen-followups-046` from origin/main 699837d8; four commits, each recorded with `kitchen.sh step`.

- b2d218d4 (step 1) `amp.require_awake(store, *threads)` now takes several threads and names every snoozed one with its `update_thread(thread=<T>, snooze="off")` call and one `kitchen.sh snooze <store> --wake <T>...` line. `commands/stop.py` gains `require_roles_awake`. The kitchen's `stop` was the trio's (`TrioVariant.commands['stop']`, in pstack-trio, outside Scope), so I added `kitchen_core.master.stop`, which runs `require_roles_awake` and then the trio's stop, and registered it in `skills/pstack-kitchen/scripts/variant.py`. `close` calls `amp.require_awake` for the verifier, consultant and sidekick threads before ending any role. The check in `Amp.send` stays as the last guard.
- bbbdf939 (step 2) `kitchen_core/dispatch.py` `require_sidekick_awake` runs before `rotate_gate`, classify and the sweep. A dispatch that will rotate to a fresh thread is not refused. Whether it rotates depends on the brief's profiles and risk, which only classify stamps, so `keeps_session_for` classifies the Scope read-only (`kpy.kitchen_py.classify_change`) and feeds `session_decision` a temporary header; nothing is written to the brief.
- 43e07850 (step 3) `SnoozeRefused(PairError)` in errors.py (exit 1, as before); `snooze.record` raises it for both refusals and appends `; if you already called update_thread(... snooze="indefinite"), undo it: update_thread(thread=<T>, snooze="off")`. `amp.Snoozed` keeps exit 5. `candidates()` docstring now says what it checks; I chose the docstring, because `thread_rows` already returns nothing unless the caller is the master's own thread and `why_not` refuses masters.
- 11d2874b (step 4) `kitchen.sh help` lists `snooze` next to `nest`; SKILL.md and master.md say every send or relay refuses (dispatch, discuss, consult, steer, stop, close) and that `--done` refusing after an Amp snooze means calling the printed undo. tests/SPEC.md regenerated in each commit.

## Ran

Tests, each fails without the source change and passes with it (source stashed for the check):
- `test_stop_to_a_snoozed_sidekick_refuses_before_it_clears_the_queue_or_logs_anything`: exit 5; queue, events.tsv, pair.json and archive state unchanged; after `--wake` stop runs.
- `test_close_refuses_with_every_snoozed_role_named_and_stops_none_until_they_are_woken`: a snoozed consultant with an awake sidekick refuses with nothing closed; with both snoozed both are named; after waking both close.
- `test_dispatch_to_a_snoozed_sidekick_prints_only_the_refusal_and_archives_nothing`: output is the single refusal line; the store snapshot is unchanged; a dispatch whose profiles differ (a rotation) is not refused.
- 043's refusal rows now assert the undo text; `test_snooze_refusals_have_their_own_class_and_keep_their_exit_codes` (core) asserts SnoozeRefused code 1 and Snoozed code 5.

```
$ python3 tests/run.py -k snooze -k stop -k close   -> 67 tests, 0 failed
$ python3 tests/run.py -k snooze -k dispatch        -> 77 tests, 0 failed
$ python3 tests/spec_gen.py --check                 -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check / ruff check / uv check --frozen -> clean
$ kitchen.sh step ... for each commit               -> gates: pass (pstack,pstack-docs)
```
`kitchen.sh finish` reruns the whole Verify block at this head.

## Self-check

figures: every output above is from commands run in this session
L012: no unchecked Optional; `uv check --frozen` passed before each step
review.self: the kitchen configures none; I read the diff myself

## Deviations

- Outside the brief's Scope, flagged: `tests/test_pstack_pair.py` (the `HELP['kitchen']` golden gets the same `snooze` lines, since it holds help byte for byte; without it three EntryScriptTests fail). Also the `kitchen.sh stop` change needed a new function in `kitchen_core/master.py` and an entry in `skills/pstack-kitchen/scripts/variant.py` (both in Scope) rather than a change to `commands/stop.py` alone, because the kitchen's stop is the trio's.
- `test_kitchen_runs_the_trios_stop_from_a_table_of_its_own_and_has_no_spawn` (test_pstack_core.py, in Scope) was renamed and now asserts the kitchen's stop is `kitchen_core.master.stop`.
- `queue` and `verify` are not guarded: queue only holds a brief and verify starts a fresh thread; the docs say so.

## Questions

none

## Follow-ups

- `Amp.relay` (steer) and `send_message` still call `require_awake` per thread; they already refuse before changing anything (043).
