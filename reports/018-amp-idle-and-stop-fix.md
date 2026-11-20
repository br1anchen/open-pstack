# Report 018: amp-idle-and-stop-fix

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/018-amp-idle-and-stop-fix.md
playbook: review-fix
branch: main
head: 7616b0e559437724fd528d85e35da3cd5610c36c
tree: clean

## Changed

Started from a9b9132 (unit 017's head, this checkout).

```
$ git diff --stat a9b9132..HEAD
 skills/pstack-kitchen/SKILL.md                     |  3 +-
 .../scripts/pstack_core/commands/relay.py          |  8 +--
 .../scripts/pstack_core/commands/stop.py           |  4 +-
 skills/pstack-pair/scripts/pstack_core/mux/amp.py  | 31 +++++++--
 .../scripts/pstack_core/mux/amp_routing.py         |  2 +-
 tests/SPEC.md                                      | 36 +++++------
 tests/pstack-pair/bin/amp                          |  8 ++-
 tests/test_pstack_kitchen.py                       | 75 ++++++++++++++++++++--
```

- Step 1, 598a041 (joo 002 medium): `Amp.in_turn_at` no longer compares the state's `updatedAt` with the send. `delivered` is now: the thread's state is known and not settled, and `turn_end_epochs(export)` holds no end at or after `done_at`. Turn boundary field (read from a real export of this thread, `amp threads export`, claude-code actor; no content): each exported message has `role`, `createdAt` (ISO), `messageId`, and an assistant message has `state: {type: 'complete', stopReason: 'tool_use' | 'end_turn'}`. In the export's 231 messages, 112 assistant messages stop on `tool_use` (mid-turn) and 3 on `end_turn`, at 17:34:56, 17:59:02 and 18:23:25, each when the thread went idle; so a turn end is an assistant message whose `state.stopReason` is a string other than `tool_use`. The real export has the field, so no `in_turn_since` fallback was needed; `in_turn_at` is kept (rewritten) because `relay` is its only caller and its meaning is unchanged. Fake `tests/pstack-pair/bin/amp`: the export also prints one assistant `end_turn` message per ISO time in $FAKE/amp-turn-ends/<T>. Test `test_a_relay_is_a_record_and_a_bundle_under_git_and_the_same_text_is_relayed_once` rows: same turn (`delivered`), the row joo describes (same turn, a later state change after `done_at`, an earlier turn end only: `delivered`, no new relay), a turn that ended after the send (`made`, new id), and the settled thread (`made`).
- Step 2, f38bb38 (joo 002 low): `print_relay` takes a `detail` (default `relay-pending`, so steer rows are unchanged); `relay_stop` passes `<role> relay-pending`, so the row is `send-stop<TAB>-<TAB>sidekick relay-pending`, the role in the column the CLI path's `send-stop - <role>` row has it. Asserted in the two stop relay tests (trio stop, pair stop).
- Step 3, 03e9abd (audit): `test_a_stop_to_a_working_thread_is_relayed_through_the_master_once` now runs a second `stop` while the relay waits: it prints `(already pending; not made again)`, leaves one relay record, unchanged events.tsv and the thread's messages unchanged. New `test_a_stop_whose_relay_cannot_be_made_falls_back_to_the_queued_send`, two subTests: checkout with no `.git` (relay returns `(None, '')`) and a bundle over 4 MiB (six 900 kB random files in the store, `relay: bundle is ... over upload_thread_file's 4 MiB` printed); each prints no `relay-pending:`, writes no relay record, and the thread gets exactly one STOP through the CLI.
- Step 4, 7616b0e: SKILL.md says a sidekick whose unit is under verification (`.verifying`) is kept by `threads --archive`, and that `stop` to a working Amp role is relayed like a steer and needs `relay-done`. Messages are role-neutral: `a relay to it is still pending` (mux/amp.py, mux/amp_routing.py) and `it was relayed again` (commands/relay.py); no test or golden asserted the old text.

## Ran

```
$ python3 tests/run.py -k relay -> 13 tests, 0 failed
$ python3 tests/run.py -k stop  -> 41 tests, 0 failed
$ python3 scripts/check-pstack.py -> 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format / ruff check / uv check --frozen -> all passed
```

Step 1's changed assertions fail without the source change (stashed skills/: the relay test FAILED). All four steps recorded with `gates: pass`. `-k archive` and `-k threads` (23 and 16 tests) pass after step 4's message change.

## Self-check

figures: every value above comes from the commands shown; the export figures are from `amp threads export` of this sidekick's thread
acceptance: (1) a relay sent with `relay-done` stays `delivered` through a later state change in the same turn and is made anew once a turn end follows the send or the thread settles (step 1 rows). (2) the relayed STOP's row names its role (step 2). (3) second stop makes no record or row; no-.git and too-large relays fall back to the CLI send (step 3). (4) SKILL.md states both; no relay message says steer for a STOP (step 4). Unit 002's tests in the relay, stop, archive and threads selections pass unchanged except the rows named here.
callers: `in_turn_at` has one caller (`Amp.relay`); `print_relay` has two (steer, stop), steer keeps the default detail; `turn_end_epochs` has one caller.
fresh: tests ran in this checkout, no cache
gate: format, ruff check and uv check --frozen pass; lesson L012: no Optional stdlib result added (`state.get('stopReason')` is type-checked as str)
forbidden: no golden regenerated

## Deviations

- The brief says the step 3 test is about L16857 and step 1's in_turn_at about L1205; line numbers had moved, the names matched.
- A turn end counts when its `createdAt` is at or after `done_at` (second resolution), consistent with `turn_ended_since`; a turn that ended in the same second as the send counts as ended.
- Step 3's too-large row finds the second STOP already pending in the thread (same text), so it asserts one STOP in total across both rows, not one per row.
- Still running `kitchen.sh` through .amp/in/orb-backup/kitchen-nostamp.py (git-store startup-stamp bug from report 000-ready-g3); step reruns were looped by .amp/in/orb-backup/step-loop.sh.

## Questions

none

## Follow-ups

- The real export's turn end is read from the claude-code actor's messages; a native Amp thread's export was not inspected, and its assistant messages may carry a different `state`.
- Each step's gates took 8 to 10 minutes of repeated `step` reruns; not investigated.
