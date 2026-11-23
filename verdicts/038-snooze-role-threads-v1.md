# Verdict 038: snooze-role-threads

status: reject
units: 038
range: daf51dc09eeffc59c136c169e371c8641fcf8a99..bee0dc78bf5e357ee0c008b70ba54e1c35ad6a1b
verifier: amp claude-code, claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

1. Acceptance line 3 ("a dispatch (and a discuss, consult or steer) to a thread recorded snoozed refuses before recording anything") does not hold for a `discuss` plan round that reaches both roles when the consultant is snoozed and the sidekick is awake. The plan goes to the sidekick first (`deliver_plan`), a `send-plan` event is recorded and a message lands in the sidekick's thread; only then does `Amp.send` refuse at the consultant (exit non-zero, message correct). Retrying after `--wake` resends the plan to the sidekick. Cause: `consult.send_plan` calls no `require_awake` before its loop (`require_awake` is only in `send_message`, `require_consultant_idle`, `steer` and `Amp.send`/`Amp.relay`). Evidence: Evidence 9. A discuss to a snoozed sidekick alone does refuse cleanly (Evidence 8), so the gap is the two-role round. The unit's tests cover dispatch, steer and consult only, not discuss. Smallest fix: check `require_awake` for every role of the round before the first `deliver_plan`, plus a test of a discuss with a snoozed consultant that asserts no event and no message to the sidekick.

Everything else in the Acceptance held (Evidence 1 to 7, 10).

## Evidence

Evidence 1 (brief commands 1 to 4, fresh scratch at bee0dc78, PSTACK_KITCHEN_ROLE=verifier):
```
$ python3 tests/run.py -k snooze     -> 3 tests, 0 failed, 9s wall with 4 jobs
$ python3 tests/run.py -k dispatch   -> 76 tests, 0 failed, 90s wall with 4 jobs
$ python3 tests/run.py -k status     -> 22 tests, 0 failed, 8s wall with 4 jobs
$ python3 tests/run.py -k archive    -> 24 tests, 0 failed, 35s wall with 4 jobs
```

Evidence 2 (command 5, whole suite; assess/archive tests unchanged and green):
```
$ python3 tests/run.py
2025 tests, 0 failed, 565s wall with 4 jobs
exit 0
```

Evidence 3 (commands 6 to 10):
```
$ python3 scripts/check-pstack.py            -> 65 skills checked; all 48 previous skills covered   (exit 0)
$ python3 tests/spec_gen.py --check          -> ok: tests/SPEC.md is current, 0 missing            (exit 0)
$ uv run --frozen ruff format --check        -> 112 files already formatted                         (exit 0)
$ uv run --frozen ruff check                 -> All checks passed!                                  (exit 0)
$ uv check --frozen                          -> All checks passed!                                  (exit 0)
$ kitchen.py --repo <scratch> setup          -> setup: ok (1 commands, ...)
```
The `gate pstack behavioral` line of the Prove block names the same commands (the repo's whole-suite gate); I ran those commands directly rather than through the gate.

Evidence 4 (Acceptance 1, the listing table; read test `test_snooze_lists_exactly_the_role_threads_that_are_idle_with_nothing_open` in the diff, then mutated the code in a second scratch at the same commit; each mutation made that test fail, each reverted with git checkout):
```
M1 snoozable: `if role == 'master':` -> `if False:`            -> 1 tests, 1 failed (snooze_lists...)
M2 drop the reviews/<unit>.md requirement of the sidekick     -> 1 tests, 1 failed (snooze_lists...)
M4 drop the `state != 'idle'` check                           -> 1 tests, 1 failed (snooze_lists...)
```
The table covers states idle/working/error/cancelled, sidekick with/without review, consultant with an open consult / answered / in error, verifier verifying / done, an idle master, and `PSTACK_SNOOZE_ROLES=0` -> `snooze: off (PSTACK_SNOOZE_ROLES=0)`.

Evidence 5 (Acceptance 2, `--done`/`--wake`, events, unknown thread): test `test_snooze_done_and_wake_record_each_change_log_it_and_refuse_a_thread_the_store_never_named` passes (Evidence 1). It asserts `not recorded for snoozing: T-nope` with exit 1 and no snooze.tsv written, rows `snoozed` then `awake`, and events `snooze` then `wake`. Code read: `snooze.record` writes exactly 4 tab cells and calls `event(...)`; `--done` with no thread falls through to the usage Die.

Evidence 6 (Acceptance 3, dispatch, steer, consult): test `test_a_snoozed_role_thread_refuses_dispatch_consult_and_steer_until_it_is_woken` passes (Evidence 1): each exits 5 with `snoozed: <T> is snoozed; call update_thread(thread=<T>, snooze="off"), then kitchen.sh snooze <store> --wake <T>, then run this again`, pair.json and the queue unchanged, no brief in the thread; after `--wake` the same dispatch sends; the refusal holds with `PSTACK_SNOOZE_ROLES=0`.

Evidence 7 (Acceptance 4, status):
```
test_status_names_the_role_threads_due_for_snoozing_and_a_role_in_error_for_the_human: passed (Evidence 1)
M3 (needs-human state check `== 'error'` -> `== 'zzz'`) -> 2 tests, 1 failed: ...test_status_names_the_role_threads_due_for_snoozing_and_a_role_in_error_for_the_human
```
It asserts `snooze-due: 1 role thread; run kitchen.sh snooze <store>` and `needs-human: consultant <T> (error); relay it to the human from the master thread`; with `PSTACK_SNOOZE_ROLES=0` the `snooze-due:` line is gone and `needs-human:` stays.

Evidence 8 (probe in the second scratch, a test method added only there: discuss to a snoozed sidekick, plan file `risk: routine`):
```
PROBE-OUT ... kitchen.sh: snoozed: T-...0001 is snoozed; call update_thread(thread=T-...0001, snooze="off"), then kitchen.sh snooze <store> --wake T-...0001, then run this again
PROBE-SAME True     (pair.json, events.tsv and the thread's message count unchanged)
PROBE-AFTER-WAKE ... amp demo-sidekick (T-...0001): pstack-kitchen PLAN .../plans/001-p.md   (sent after --wake)
```

Evidence 9 (probe in the second scratch: a consultant spawned and snoozed, the sidekick awake, plan file `risk: escalated`, `kitchen.sh discuss`):
```
PROBE-OUT warn: plan has no contract: 2; no playbook section
amp demo-sidekick (T-...0001): pstack-kitchen PLAN .../plans/001-p.md
sent PLAN 001-p.md to sidekick demo-sidekick
archive: 0 archived, 0 kept, 2 in use
kitchen.sh: snoozed: T-...0002 is snoozed; call update_thread(thread=T-...0002, snooze="off"), then kitchen.sh snooze <store> --wake T-...0002, then run this again
PROBE-SAME False   sidekick messages 1 -> 2, consultant 1 -> 1
PROBE-EVENTS-DIFF  ...  master  send-plan  001-p  sidekick
```
The refusal names the right thing, but the sidekick already got the plan and `send-plan` is on record.

Evidence 10 (Acceptance 5 and 6): `assess` diff read: the outbox and relay checks move into `pending_to(store, thread, since)`. At that point in `assess` the state is already in SETTLED (`working` and `unknown` return earlier), so dropping the old `state in SETTLED` clause in the relay check changes nothing; the archive tests (24) and the whole suite pass unchanged. Docs diff read: SKILL.md Amp threads bullet (when, never master or working/cancelled/error, `snooze`/`--done`/`--wake`, refusal, off switch, default on, `status` lines), the Store additions row for `snooze.tsv`, and master.md step 5 (run at `snooze-due:`, unsnooze on refusal, relay duty for `needs-human:`, a blocked report with Questions and approval dialogs, off switch) are present; check-pstack and spec_gen --check pass (Evidence 3).

## Flows driven

pstack: the kitchen CLI against the fake amp (the repo's fake-amp tests, Evidence 1 and 4 to 7) plus hand-written probes of `kitchen.sh snooze` and `discuss` there (Evidence 8, 9). No other user-facing flow.
pstack-docs: SKILL.md and master.md diff read against the Acceptance; check-pstack passes (Evidence 3, 10).

## Folded Acceptance

none

## Not covered

- Nothing was run against a real amp or a real `update_thread`; the fake amp is the only surface, as the brief says the amp CLI cannot snooze.
- The consultant `deliver_plan` path was probed only in the two-role case above; `consult` and `steer` to a snoozed thread are covered by the unit's own test, which I read and mutation-checked only through the snooze listing and status tests, not through `require_awake` (the sidekick's report records that mutation).
- The `gate pstack behavioral` line was not run through the gate wrapper; its commands were run directly.

## Lessons

L012: held (uv check --frozen passes; the new Optional returns in snooze.py are not stdlib None-returning calls)

Test hygiene (standing check): the unit's tests use `subprocess.run(..., timeout=timeout)` through `run_sh` (bounded). The filter of `send-consult` lines out of events.tsv and the `re.sub(r'\{\{[^}]*\}\}', 'x', ...)` that fills a consult template state no reason of their own in the new tests; the second pattern already appears in four other tests in the file. Minor, not a finding on its own.
