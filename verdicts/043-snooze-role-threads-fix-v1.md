# Verdict 043: snooze-role-threads-fix

status: clean
units: 038 043
range: daf51dc09eeffc59c136c169e371c8641fcf8a99..e82b720a9dad0c9a9a991a9c92737db9a67d4923
verifier: amp thread, claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Observation, not a finding: a `dispatch` to a snoozed sidekick still prints the classify/playbook lines and runs the archive sweep (`archive: 0 archived, 0 kept, 1 in use`) before the refusal. It leaves events.tsv and pair.json unchanged (E6 probe, and 038's test asserts `self.state() == pair`), so "refuses before recording anything" holds.

Setup note: the packet's scratch path did not exist on this orb. I created it with `git worktree add --detach <scratch> e82b720a` (HEAD verified e82b720a9dad0c9a9a991a9c92737db9a67d4923), and the worktree was clean at the end.

## Evidence

E1: repo setup and behavioral gate (the full suite, brief Command 5 for both units)
```
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, ...)
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base daf51dc0...
pstack behavioral: pass
  exit 0 729.8s python3 tests/run.py
(log tail) 2028 tests, 0 failed, 729s wall with 4 jobs
```

E2: targeted brief commands (each in a fresh `bash -c` from the scratch root)
```
$ python3 tests/run.py -k snooze    -> exit 0, 5 tests, 0 failed
$ python3 tests/run.py -k dispatch  -> exit 0, 76 tests, 0 failed
$ python3 tests/run.py -k status    -> exit 0, 23 tests, 0 failed
$ python3 tests/run.py -k archive   -> exit 0, 24 tests, 0 failed
$ python3 tests/run.py -k gitstore  -> exit 0, 56 tests, 0 failed
$ python3 tests/run.py -k discuss   -> exit 0, 13 tests, 0 failed
```

E3: static checks
```
$ python3 scripts/check-pstack.py        -> 65 skills checked; all 48 previous skills covered (exit 0)
$ python3 tests/spec_gen.py --check      -> ok: tests/SPEC.md is current, 0 missing (exit 0)
$ uv run --frozen ruff format --check    -> 112 files already formatted (exit 0)
$ uv run --frozen ruff check             -> All checks passed! (exit 0)
$ uv check --frozen                      -> All checks passed! (exit 0)
```

E4: the tests that pin each Acceptance line, all in the passing runs of E1/E2 (tests/test_pstack_kitchen.py):
```
test_snooze_lists_exactly_the_role_threads_that_are_idle_with_nothing_open          (038 L1: verifier while verifying, sidekick working/error/cancelled/unreviewed, consult open, old master never listed; PSTACK_SNOOZE_ROLES=0 -> "snooze: off (PSTACK_SNOOZE_ROLES=0)")
test_snooze_done_and_wake_record_each_change_log_it_and_refuse_a_thread_the_store_never_named  (038 L2 + 043 L3: "not recorded for snoozing: T-nope"; "is a master thread; masters are never snoozed"; "is not snoozable now (its state is tool_use)"; "(its unit has no review note)"; no snooze.tsv after refusals; rows snoozed/awake; events snooze/wake)
test_a_snoozed_role_thread_refuses_dispatch_consult_and_steer_until_it_is_woken      (038 L3: exit 5, message names update_thread(thread=<T>, snooze="off"), pair.json and queue unchanged; with PSTACK_SNOOZE_ROLES=0 still refused; after --wake dispatch sends)
test_status_names_the_role_threads_due_for_snoozing_and_a_role_in_error_for_the_human (038 L4: snooze-due: / needs-human: lines)
test_a_plan_round_to_an_awake_sidekick_and_a_snoozed_consultant_sends_nothing_until_it_is_woken (043 L2: exit 5, sidekick message count unchanged, no send-plan event; after --wake both get PLAN)
test_status_exports_each_role_thread_once                                            (043 L4: counts fake amp `threads export` per thread == 1)
AmpGitKitchenTests.test_a_snooze_done_and_a_master_publish_put_snooze_tsv_on_the_branch (043 L1)
test_pstack_core: ownership table classes snooze.tsv as MASTER (043 L1)
```

E5: code at the head (diff daf51dc0..e82b720a)
```
gitstore.py RULES master files: r'|nest-pending\.tsv|snooze\.tsv|events\.tsv|gates\.tsv)'
consult.send_plan: "for role in round_.send: amp.require_awake(...)" runs before the delivery loop
snooze.record: the refusal loop over every --done thread runs before the write loop (raises ToolFailed)
status.run: lines() inside amp.exports_cached(); the cache is reset to the outer value on exit (no module cache outlives the command)
amp.assess: the inline outbox/relay check moved to pending_to(); equivalent there because state is in SETTLED at that point (non-settled returns earlier)
```

E6: break probe, a temporary test in the scratch that drove kitchen.sh against the fake amp. I deleted it afterwards and `git status` was clean.
```
$ python3 -m unittest -v -k test_probe test_zz_verify043_probe
test_probe_mixed_done_list_writes_no_row ... ok
test_probe_snoozed_sidekick_queue_and_dispatch ... ok
Ran 2 tests ... OK
MIXED: kitchen.sh: snooze: T-...0009 is a master thread; masters are never snoozed   (--done <valid idle reviewed sidekick> <master>: no snooze.tsv, no snooze event)
DONE: snoozed: T-...0001
LIST: snooze: nothing to snooze          (a snoozed thread is not listed again)
WAKE1: awake: T-...0001
WAKE2: awake: T-...0001 (already)
ROWS: 2
DISPATCH: ... archive: 0 archived, 0 kept, 1 in use
kitchen.sh: snoozed: T-...0001 is snoozed; call update_thread(thread=T-...0001, snooze="off"), then kitchen.sh snooze <store> --wake T-...0001, then run this again
(exit 5; events.tsv byte-identical before/after; no brief reached the sidekick)
```

E7: docs (git diff of SKILL.md and master.md)
```
SKILL.md: "under the git store `nest-pending.tsv` and `snooze.tsv` are published as master files."
SKILL.md: "`PSTACK_SNOOZE_ROLES=0` stops new snoozing only: a thread snoozed earlier still needs `update_thread(..., snooze="off")` and `kitchen.sh snooze <store> --wake <T>`. It is on by default."
SKILL.md: store table row `snooze.tsv | kitchen.sh snooze --done, --wake | ...`
master.md: snooze-due flow, the wake on a `snoozed: <T> is snoozed` refusal, the relay duty ("relayed to the human from your own thread"), and "`PSTACK_SNOOZE_ROLES=0` stops new snoozing only; a thread snoozed earlier still needs ..."
```

E8: testing standard over the range's tests
```
$ git diff daf51dc0..HEAD -- tests/*.py | grep '^+' | grep -nE "sleep|time\.time|monotonic|elapsed"   -> no match (exit 1)
run_sh(self, *args, code=0, timeout=120) bounds every kitchen.sh subprocess; discuss is run with --timeout 1
No output masks added: SNOOZE_LINE regex parses the listed rows, and the events.tsv filter is fixture setup (it removes a send-consult line between subtests)
Each source change is committed with its test: 5e82657f (snooze.py, amp.py, ... + test_pstack_kitchen.py), de00010d (fixes + tests), e82b720a (snooze.py refactor + SPEC.md, behaviour pinned by de00010d tests)
spec_gen --check: 0 missing (E3)
```

## Flows driven

- pstack: kitchen.sh `snooze`, `snooze --done/--wake`, `dispatch`, `discuss`, `steer`, `consult` and `status` against the repo's fake amp, which is the repo's real CLI surface for Amp threads (E4, E6). The git-store publish was driven through AmpGitKitchenTests against a fake origin (E4).
- pstack-docs: SKILL.md and master.md read at the head; check-pstack and spec_gen pass (E3, E7).

## Folded Acceptance

none

## Not covered

- Real Amp `update_thread` snoozing. The amp CLI cannot snooze and the master does it with its own tool, so the kitchen side was proved against the fake amp only.

## Lessons

- L012: held (uv check --frozen passes at the head, E3)
