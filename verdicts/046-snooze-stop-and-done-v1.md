# Verdict 046: snooze-stop-and-done

status: clean
units: 046
range: 699837d853a868ed785fa843cb167ee3e559c3bc..11d2874b62ef48ef9305c3e772e388e2cf66e2e6
verifier: amp claude-code claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

## Evidence

All commands ran from the scratch worktree at head 11d2874b with PSTACK_KITCHEN_ROLE=verifier and PSTACK_KITCHEN_RUN=kitchen-followups. `kitchen.py setup` printed `setup: ok (1 commands, ...)`.

Block 1, brief commands 1 to 3 (038's and 043's snooze tests, stop, dispatch):
```
$ python3 tests/run.py -k snooze
10 tests, 0 failed, 28s wall with 4 jobs
$ python3 tests/run.py -k stop
44 tests, 0 failed, 61s wall with 4 jobs
$ python3 tests/run.py -k dispatch
77 tests, 0 failed, 29s wall with 4 jobs
```

Block 2, brief command 4. The full suite is also the Prove block's `gate pstack behavioral`, since kitchen.toml sets `behavioral = ["python3 tests/run.py"]`:
```
$ python3 tests/run.py
2046 tests, 0 failed, 732s wall with 4 jobs
exit 0
```

Block 3, brief commands 5 to 9:
```
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check
112 files already formatted
$ uv run --frozen ruff check
All checks passed!
$ uv check --frozen
All checks passed!
(all exit 0)
```

Block 4, `kitchen.sh help` lists snooze:
```
$ skills/pstack-kitchen/scripts/kitchen.sh help | grep -n -B1 -A2 snooze
23:  snooze <store> [--done <thread>... | --wake <thread>...]
24:                                            Amp threads: the idle role threads with nothing open to snooze (update_thread);
25:                                            --done records threads snoozed, --wake awake
```

Block 5, my own probes through the repo's AmpKitchenTests fake-amp harness (a subclass in a scratch file outside the worktree, run with `python3 -I`). Each refusal left the pair.json, events.tsv, queue and amp-archived snapshot identical:
```
P1 stop (consultant snoozed, sidekick awake): kitchen.sh: snoozed: T-...0002 is snoozed; call update_thread(thread=T-...0002, snooze="off"), then kitchen.sh snooze <store> --wake T-...0002, then run this again   [exit 5, snapshot unchanged, queue file kept]
P2 stop --only consultant: same refusal naming T-...0002   [exit 5, snapshot unchanged]
P3 stop --only sidekick (consultant still snoozed): not refused; "amp demo-sidekick (T-...0001): pstack-kitchen STOP ..." and a send-stop event logged
P4 snooze --done <master thread>: kitchen.sh: snooze: T-aaaaaaaa-... is a master thread; masters are never snoozed; if you already called update_thread(... snooze="indefinite"), undo it: update_thread(thread=T-aaaaaaaa-..., snooze="off")   [exit 1, no snooze.tsv written]
P5 dispatch to snoozed sidekick (non-rotating): the single line "kitchen.sh: snoozed: T-...0001 is snoozed; call update_thread(...), then kitchen.sh snooze <store> --wake T-...0001, then run this again"   [exit 5]
P6 after `snooze --wake`, the same dispatch: briefs delivered = 1
```

Block 6, the unit's committed tests at the head, read in the range. They pin each Acceptance line:
- `test_stop_to_a_snoozed_sidekick_refuses_before_it_clears_the_queue_or_logs_anything` asserts the snapshot is unchanged, then asserts stop runs, logs send-stop and clears the queue after `--wake`.
- `test_close_refuses_with_every_snoozed_role_named_and_stops_none_until_they_are_woken` covers one snoozed role, then two named together, then close running after the wake.
- `test_dispatch_to_a_snoozed_sidekick_prints_only_the_refusal_and_archives_nothing` asserts the output is exactly one line, and that a rotating dispatch is not refused.
- `test_snooze_refusals_have_their_own_class_and_keep_their_exit_codes` asserts SnoozeRefused, code 1, and the undo text.
- `test_snooze_done_and_wake_record_each_change_...` asserts the undo text on each `--done` refusal.
- `test_kitchen_runs_the_trios_stop_after_its_own_snooze_check_...` asserts the kitchen stop is `('master','stop')`.
- Commits in the range: b2d218d4 (stop and close), bbbdf939 (dispatch), 43e07850 (SnoozeRefused), 11d2874b (docs and help). Each fix commit carries its test or doc change.
- The SKILL.md and master.md hunks in 11d2874b say that dispatch, discuss, consult, steer, stop and close refuse every snoozed thread, and that a refused `--done` prints the `update_thread(thread=<T>, snooze="off")` undo.

## Flows driven

- pstack-kitchen CLI, `stop`, `close`, `dispatch`, `snooze --done` and `snooze --wake` through `kitchen.sh` under PSTACK_MUX=amp with the fake amp: Evidence blocks 1, 2 and 5.
- `kitchen.sh help`: Evidence block 4.
- No visual UI is touched.

## Folded Acceptance

none (single unit, not a landing)

## Not covered

- A live `update_thread(... snooze=...)` call on a real Amp thread. The refusals and the printed calls are driven against the fake amp only, since the kitchen CLI cannot snooze.
- The Herdr and Rex `stop` paths with a snooze row present. `role_thread` is amp-only, and the full suite (2046 tests, including the Herdr and Rex stop tests) passes.

## Lessons

none
