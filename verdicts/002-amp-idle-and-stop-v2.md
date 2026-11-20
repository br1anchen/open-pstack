# Verdict 002: amp-idle-and-stop

status: clean
units: 002
range: 7fca27105380693abb8741e0af8347d63c413f10..6ac18e8e557c12a97f8ff88524d57b4e19630bcd
verifier: amp anthropic/claude-sonnet-5-5 (claude-code actor)
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small

## Findings

none

## Evidence

Scratch: the packet's scratch path did not exist on this orb, so I made a detached worktree of 6ac18e8 at that path (`git worktree add --detach`); `git log --oneline 7fca271..HEAD` lists the seven commits 37c06a9..6ac18e8.

Brief commands (each in a fresh shell, PSTACK_KITCHEN_ROLE=verifier):

```
$ python3 tests/run.py -k threads_archive
1 tests, 0 failed, 20s wall with 2 jobs
$ python3 tests/run.py -k stop
40 tests, 0 failed, 46s wall with 2 jobs
$ python3 tests/run.py -k relay
12 tests, 0 failed, 33s wall with 2 jobs
```

Prove block (setup, then the behavioral gate on the whole suite):

```
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, ...)
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 7fca271...
pstack behavioral: pass
  exit 0 1176.2s python3 tests/run.py
(log tail) 1934 tests, 0 failed, 1176s wall with 2 jobs
```

Acceptance 1 (archive keeps a sidekick under verification): I read `test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use`. Its table has `('a sidekick under verification', self.sidekick_under_verification, False, 2)` (not archived, 2 in use) and `('a done sidekick, idle', ..., True, 1)` (still archived). It passes above. The code is `role_is_idle`: `and not doc.get('verifying')` (mux/amp.py).

Acceptance 2 (stop after an archived consultant): `test_stop_names_a_role_whose_entry_holds_no_thread_and_starts_no_new_one` archives an idle consultant, runs `kitchen.sh stop`, asserts the output has `stop: demo-consultant has no thread; nothing to stop` and that amp.log is unchanged (no thread started). It passes in the `-k stop` run. Code: `starts_on_prompt and not agent_session(...)` guard in variant.py stop and commands/stop.py.

Acceptance 3 (stop to a working sidekick relays; to an idle one it goes by the CLI):
- Working: `test_a_stop_to_a_working_thread_is_relayed_through_the_master_once` asserts `relay-pending: <id> for demo-sidekick (<thread>)`, the `relay: upload_thread_file`, `relay: send_thread_message` and `relay: then run kitchen.sh relay-done` lines, `sidekick_state: working`, no `report:` line, thread messages unchanged, and one `send-stop` event. `test_pair_stop_relays_a_working_sidekick_without_the_wait_report_fallback` asserts the same through pair.sh. Both pass.
- Idle: no committed test pins the idle CLI path, so I drove it with a temporary test in the scratch (removed afterwards; `git status` clean). It dispatched a sidekick, marked its report done, set its thread to idle, and ran `kitchen.sh stop <store> --timeout 20`:

```
PROBE rc=0
OUT=amp demo-sidekick (T-00000000-0000-4000-8000-000000000001): pstack-kitchen STOP /tmp/.../runs/demo
sidekick_state: idle
verifier demo-verifier is not live
consultant demo-consultant is not live
report: /tmp/.../reports/001-util.md (done)
relays=[]
newmsgs=['pstack-kitchen STOP /tmp/.../runs/demo\n\npstack: before you act on this message, run `PSTACK_MUX=amp ...pair.sh sync ...` ...']
```

  The idle sidekick got the STOP as a CLI message, and no relay record was written.

Acceptance 4 (Herdr/Rex stop and trio goldens unchanged): `mux/base.py` sets `relays = False` and `starts_on_prompt = False`, so both new branches are skipped on Herdr/Rex. The behavioral gate ran all 1934 tests, including the trio and pair goldens and the fake-herdr and fake-rex stop tests, with 0 failed. The diff touches no file under tests/pstack-pair/golden.

Standing check on tests in the range: the new tests add no mask or normalisation. The one subprocess a new test starts (`pair.sh stop`) has `timeout=120`.

## Flows driven

profile pstack: stop and threads --archive driven with the fake Amp harness: the stop of a working sidekick (relay), the stop of an idle sidekick (CLI send, probe above), and the stop after an archived consultant (no thread). Evidence blocks: Acceptance 2, 3 and the probe above. This is the fake amp CLI the repo's tests use, not a real Amp thread.

## Folded Acceptance

none

## Not covered

- A real Amp thread in a turn: the fake Amp harness stood in for it, so this verdict does not show real thread-state timing.
- The pair's stop for a sidekick whose entry holds no thread (`stop: <name> has no thread`, commands/stop.py): I read the code and no test pins it. The consultant path through the trio/kitchen loop is pinned.

## Lessons

none
