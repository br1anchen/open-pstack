# Report 038: snooze-role-threads

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/038-snooze-role-threads.md
playbook: feature
branch: kitchen-followups-038
head: bee0dc78bf5e357ee0c008b70ba54e1c35ad6a1b
tree: clean

## Changed

Base is main daf51dc0. Two commits, both recorded with `kitchen.sh step` (gates pass at each):

- 5e82657f feat(pstack-pair): code, tests, SPEC. Steps 1 to 3 of the brief are in this one commit, since each part's tests exercise the others.
  - `commands/snooze.py` (new), modelled on `nest.py`: `snooze.tsv` rows (time, thread, role, state), `enabled()` (on unless `PSTACK_SNOOZE_ROLES=0`), `snoozable()`, `candidates()`, `status_lines()`, and the `snooze <store> [--done|--wake <thread>...]` command (an unknown thread is refused before any row is written; a repeat prints `(already)`). `--done` and `--wake` log `snooze` and `wake` events. Registered in `cli.py`.
  - `mux/amp.py`: the outbox and relay checks of `assess` move into `pending_to(store, thread, since)`, which `assess` and `snoozable` both use (assess behaves as before). `require_awake(store, thread)` raises `Snoozed` (exit 5, a PairError) with the brief's message. It is called in `Amp.send` and `Amp.relay`, and, so a refusal happens before anything is recorded, at the top of `send.send_message` (dispatch, discuss, answer; before `require_fresh_session`), `consult.require_consultant_idle` and `steer.steer`.
  - `commands/status.py`: `snooze-due:` and `needs-human:` lines beside the nest line.
- bee0dc78 docs: SKILL.md Amp threads bullet and Store additions row; master.md step 5 (run snooze at `snooze-due:`, unsnooze on refusal, relay duty for `needs-human:`, blocked-with-Questions and approval dialogs). tests/SPEC.md regenerated.

No change to nest.py, no snooze at thread start, no new amp CLI calls beyond `export`. I did not edit `tests/pstack-pair/bin/amp`: the existing fake already gives each thread a state.

## Ran

```
$ python3 tests/run.py -k snooze    -> 3 tests, 0 failed
$ python3 tests/run.py -k status    -> 22 tests, 0 failed
$ python3 tests/run.py -k archive   -> 24 tests, 0 failed (assess unchanged)
$ python3 tests/run.py -k EndsItself / FunctionSize -> pass
$ python3 scripts/check-pstack.py   -> 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
$ kitchen.sh step ... 5e82657f / bee0dc78 -> gates: pass (pstack,pstack-docs); the gate runs the whole suite
```

Mutation check (not committed): with `require_awake` disabled, `test_a_snoozed_role_thread_refuses_dispatch_consult_and_steer_until_it_is_woken` fails in all its subtests.

Tests (tests/test_pstack_kitchen.py, AmpKitchenTests):
- `test_snooze_lists_exactly_the_role_threads_that_are_idle_with_nothing_open`: a table over fake-amp state (idle, working, error, cancelled) and role (sidekick reviewed or not, consultant with an open consult or answered, verifier open or done, a master thread that is idle), plus `PSTACK_SNOOZE_ROLES=0`.
- `test_snooze_done_and_wake_record_each_change_log_it_and_refuse_a_thread_the_store_never_named`
- `test_a_snoozed_role_thread_refuses_dispatch_consult_and_steer_until_it_is_woken`: each exits 5, names `snooze="off"`, leaves pair.json, the queue and the thread's messages as they were; after `--wake` the same dispatch sends; the refusal holds with `PSTACK_SNOOZE_ROLES=0`.
- `test_status_names_the_role_threads_due_for_snoozing_and_a_role_in_error_for_the_human`

## Self-check

figures: counts come from the commands shown
acceptance: the listing, `--done`/`--wake` with events and refusal, the refusals before any record, `status` lines, `assess` unchanged and the docs are each shown by the tests and gates above
callers: `pending_to` has two users (`assess`, `snoozable`); `require_awake` has five call sites (`Amp.send`, `Amp.relay`, `send_message`, `require_consultant_idle`, `steer`)
fresh: the gate at each step ran the whole suite
gate: format, lint and typecheck pass

## Deviations

- Steps 1 to 3 of the brief are one commit.
- `tests/pstack-pair/bin/amp` was not changed.
- No `review.self` skill is named in `.agents/kitchen.toml`, so none was run on the diff.
- A verifier counts as a role thread only while its verification is open, as `live_threads` already defines it; a finished verifier thread is snoozable like a helper.

## Questions

none

## Follow-ups

- `snoozable` calls `amp threads export` once per candidate, and `status` does so again for `needs-human:`; fine for a few role threads.
