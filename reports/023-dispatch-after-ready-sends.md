# Report 023: dispatch-after-ready-sends

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/023-dispatch-after-ready-sends.md
playbook: bug-fix
branch: kitchen-followups
head: 4bca7862ab6374879d0d0bc6d946448118446d36
tree: clean

## Changed

One commit on top of unit 005's head b1ca49f (checkout from kitchen-code/kitchen-followups/sidekick-g4):

```
 skills/pstack-pair/scripts/pstack_core/send.py | 20 ++++++++++++++
 tests/SPEC.md                                  | 28 +++++++++----------
 tests/test_pstack_kitchen.py                   | 37 ++++++++++++++++++++++++++
 3 files changed, 71 insertions(+), 14 deletions(-)
```

## What was broken, the cause, the fix

Broken: after a rotation and its READY sync, the first `dispatch <brief> --send-only` archived the old thread and sent nothing; an identical second call sent and recorded the brief.

Cause (confirmed by running it): `send.send_message` read the new sidekick's status once. The replacement's READY file reaches the store a moment before the turn that wrote it ends, and under Amp the status is the thread's lastKnownAgentState, so it read `working`. send_message then raised AgentBusy. By then `rotate_if_due` had already printed `archived T-... (sidekick): a brief is being sent`. The exit code of that first call is 5, and the reason (`kitchen.sh: sidekick demo-sidekick is working; run: pair.sh wait <store>`) goes to stderr only, so a caller that read stdout saw just the archive lines. Nothing was recorded (`.dispatch` unchanged) because `record_dispatch` runs after the status check. The second call came after the turn had ended.

Not the cause: `rotate_if_due`, the settled-READY branch of `require_fresh_session`, and the `--send-only` return all behave. The `bootstrap_pending` branch of `rotate_if_due` returns early on purpose. Unit 005 alone does not fix this: the new test fails at b1ca49f (005's head) with the output below.

Fix (send.py): if the sidekick is `working` and its generation was never dispatched a brief (`.dispatch.generation != .sidekick.generation`), `send_message` waits up to PAIR_ROTATE_READY_S (60) for the READY turn to end, polling every PAIR_READY_POLL_S (2), before refusing. A working sidekick that holds a brief of its generation is still refused at once. If the wait runs out it is refused as before: exit 5, the `is working` line on stderr, nothing sent or recorded. So one call archives, sends and records, or exits non-zero with a reason.

## Ran

Failing test at the base (the fix stashed, so the code is 005's head b1ca49f), `test_the_first_send_only_dispatch_after_ready_waits_out_the_ready_turn_and_sends` (AmpKitchenTests; the fake amp reports thread 2 as working for its first reads):

```
archived T-...0001 (sidekick): a brief is being sent
archive: 0 archived, 0 kept, 1 in use
kitchen.sh: sidekick demo-sidekick is working; run: pair.sh wait <store>
FAILED (failures=1)
```

The same test with the fix: `Ran 1 test ... OK`. It asserts three things:
- With PAIR_ROTATE_READY_S=0 and the thread still working, the call exits 5, prints `sidekick demo-sidekick is working`, sends nothing and leaves no brief in thread 2.
- Once the fake reports idle on the 6th export, the next `--send-only` call prints `sent: BRIEF ...`, exits 0, records `.dispatch.brief` as 002 and leaves exactly one brief in thread 2.
- The thread is archived in the first call only.

Verify commands (the finish step reruns them):

```
python3 tests/run.py -k dispatch   -> 74 tests, 0 failed
python3 tests/run.py -k rotat      -> 65 tests, 0 failed
```

Gates: `kitchen.sh step` passed (pstack, pstack-docs) and recorded step 1 at 4bca7862a. Before it: `ruff format` unchanged, `ruff check` passed, `uv check --frozen` passed, `spec_gen.py --check` current (SPEC.md regenerated in the same commit).

## Self-check

figures: every number comes from the commands above
acceptance: after a rotation and READY, one `dispatch --send-only` prints `sent: BRIEF` and `.dispatch.brief` names the new brief (test's second call); a dispatch that cannot send exits 5 with the `is working` line (test's first call)
callers: `send_message` is the one place both `dispatch` and `discuss` send through; the two new helpers have one caller each. Only a generation with no brief of its own waits, so a busy kept session is still refused at once
fresh: the test ran fresh in a new process under unittest and again via tests/run.py
gate: format, lint and typecheck pass on the commit (L012: no Optional stdlib results added)

## Deviations

- The test sits in AmpKitchenTests; it shims `amp` on PATH to flip the fake's state after the Nth export. The fake in tests/pstack-pair/bin is outside the Scope, and the count is how the test models "the turn ends during the wait" without a sleep.
- The Amp-multiplexer `wait` is a no-op, so the wait is a poll with time.sleep (as submit_typed does), not mux.wait. With PAIR_READY_POLL_S=0 the test makes no real sleep.
- rotate.py and dispatch.py in the Scope needed no change.

## Questions

none

## Follow-ups

- `rotate.require_idle_session` has the same 60 s wait for a rotation that succeeds in-process; under Amp the mux wait is a no-op there, so it refuses a working fresh session at once. It only runs when the rotation's READY is already in, so it was not on this path. Fold it into the same poll if it ever bites.
- Under Amp the AgentBusy reason goes only to stderr; a caller that keeps only stdout sees no reason. Left alone.
