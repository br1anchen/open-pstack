# Verdict 018: amp-idle-and-stop-fix

status: clean
units: 002 018
range: 7fca27105380693abb8741e0af8347d63c413f10..7616b0e559437724fd528d85e35da3cd5610c36c
verifier: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small

## Findings

none

## Evidence

Scratch: a git worktree at 7616b0e (HEAD verified with `git rev-parse HEAD`; `git diff --stat 7fca271 HEAD` = 16 files changed, 476 insertions, 119 deletions, matching the packet). `kitchen.py setup` printed `setup: ok (1 commands ...)`. All commands ran with PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups.

Unit 002 brief commands and unit 018 brief commands:

```
$ python3 tests/run.py -k threads_archive
1 tests, 0 failed, 23s wall with 2 jobs        (exit 0)
$ python3 tests/run.py -k stop
41 tests, 0 failed, 79s wall with 2 jobs       (exit 0)
$ python3 tests/run.py -k relay
13 tests, 0 failed, 52s wall with 2 jobs       (exit 0)
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered   (exit 0)
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing        (exit 0)
```

Extra targeted runs of the Acceptance tests:

```
$ python3 tests/run.py -k archive
23 tests, 0 failed, 90s wall with 2 jobs
$ python3 tests/run.py -k no_thread        (test_stop_names_a_role_whose_entry_holds_no_thread_and_starts_no_new_one)
1 tests, 0 failed, 4s wall with 2 jobs
$ python3 tests/run.py -k archives_an_idle_role   (test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use)
1 tests, 0 failed, 16s wall with 2 jobs
```

Repo gate (packet's Prove block):

```
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 7fca271...
pstack behavioral: pass
  exit 0 1349.7s python3 tests/run.py
(log) 1937 tests, 0 failed, 1349s wall with 2 jobs
```

Code reading of the diff against each Acceptance line (the tests above assert the same behaviours through the fake amp/herdr surface; no real Amp thread in a turn is available to drive here):

```
unit 002 / 1  mux/amp.py role_is_idle: `and not doc.get('verifying')` added to the sidekick branch; `assess` now routes both roles through role_is_idle.
              Test docstring: "A sidekick whose last unit's report is done while a verifier still holds its verdict open (pair.json .verifying ...)";
              the test sets pair['verifying'] and expects kept, then archived once cleared; the done-and-idle row (archives_an_idle_role) passes.
unit 002 / 2  commands/stop.py: `if mux.get().starts_on_prompt and not agent_session(...)`: print(f'stop: {name} has no thread; nothing to stop'); return 0.
              Test asserts 'stop: demo-consultant has no thread; nothing to stop' in out, the old thread in amp-archived, and amp.log unchanged (no thread started).
unit 002 / 3  stop.py relay_stop -> print_relay: prints `relay-pending: <id> for <name> (<thread>)`, the three `relay:` lines, and sends nothing.
              Test asserts relay-pending line, `relay: then run kitchen.sh relay-done <store> <id>`, thread_messages unchanged, 'sidekick_state: working', no 'report:'.
unit 002 / 4  No herdr/rex file in the diff; steers_running/relays guards keep them out; tests/pstack-pair/golden untouched; full suite (1937 tests incl. golden transcripts) passes.
unit 018 / 1  Amp.in_turn_at: delivered = state known, not settled, and no turn_end_epochs(export) >= done_at. The fake's export gained assistant end_turn messages
              from $FAKE/amp-turn-ends/<T>. Test rows: same turn -> [done, 'delivered']; same turn with a state change after done_at -> 'delivered', no new relay;
              a turn end after the send -> 'made' with a new id (assertNotEqual); settled -> 'made'.
unit 018 / 2  stop.py relay_stop logs event(store,'master','send-stop','-', f'{role} relay-pending'); tests assert events.count('send-stop\t-\tsidekick relay-pending') == 1 in both kitchen.sh and pair.sh stop tests.
unit 018 / 3  A second stop prints '(already pending; not made again)', leaves one relay record, events.tsv unchanged (assertEqual on the file text), thread messages unchanged.
              test_a_stop_whose_relay_cannot_be_made_falls_back_to_the_queued_send: no-.git and too-large subtests print no 'relay-pending:', write no relay record, and the thread gets exactly one STOP via the CLI.
unit 018 / 4  SKILL.md diff: "its unit is under verification (`.verifying` in pair.json)" and "`stop` to a working Amp role is relayed the same way and needs the same `relay-done`."
              Messages changed: 'a relay to it is still pending' (amp.py), 'it was relayed again' (relay.py); `git grep -n "steer was relayed again\|steer relay to it"` has no hit in skills/ (checked below).
```

```
$ git grep -n "steer was relayed again\|a steer relay to it is still pending" -- skills tests | head
(no output)
```

Turn-boundary field check: the report states the real export's assistant messages carry `state.stopReason` ('tool_use' mid-turn, 'end_turn' at turn end). I confirmed the shape on this verifier thread's own native Amp export: assistant messages have `state: {type: 'complete', stopReason: 'tool_use'}` (8 of 8 so far, no end_turn yet because the turn was still open), consistent with turn_end_epochs treating only a non-tool_use string as a turn end.

## Flows driven

pstack: ran the repo's behavioral gate (`gate pstack behavioral`, 1937 tests, pass) and drove the stop/relay/archive flows through the test suite's fake amp and herdr binaries (Evidence blocks above). pstack-docs: `scripts/check-pstack.py` and `tests/spec_gen.py --check` pass; SKILL.md hunks read.

## Folded Acceptance

none

## Not covered

- A live Amp thread in a turn was not driven; the relay/stop paths ran through the fake `amp` (tests/pstack-pair/bin/amp). The report itself notes a native Amp thread whose turn end carries a different `state` was not inspected; my own thread's export has the same `state.stopReason` shape for assistant messages, but I saw no `end_turn` there.

## Lessons

none
