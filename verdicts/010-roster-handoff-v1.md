# Verdict 010: roster-handoff

status: clean
units: 010
range: 7218f3112db91d4448b20d708b5c92117b6543db..c40b7b0cd3989064a31295d45eb8cc880164f64e
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes (not findings):
- The packet's scratch worktree did not exist on this orb, so I created it myself with `git worktree add --detach <scratch> c40b7b0c`. I used `git worktree add` for the base and probe worktrees too, and removed both before writing this verdict.
- bug-fix step 5 (the failing test lands before the fix): it does not land separately. Each test is in the same commit as the source change it pins, which AGENTS.md requires ("Commit a source change in the same commit as the test that pins it"). I reproduced the failure by putting the head's tests on the parent (Evidence B).
- The unit's own diff (84ea957f..c40b7b0c) touches only kitchen_core/master.py, tests/test_pstack_kitchen.py and tests/SPEC.md. That is inside the brief's Scope, and pstack_core is unchanged. The longer packet range covers the stack below 036 and is not judged here.

## Evidence

A. Brief commands at the head (scratch at c40b7b0c, PSTACK_KITCHEN_ROLE=verifier):

```
$ python3 tests/run.py -k take_master
...
13 tests, 0 failed, 40s wall with 4 jobs
exit=0
$ python3 tests/run.py -k roster
...
    6.4s  test_pstack_kitchen.AmpKitchenTests.test_take_master_without_a_roster_on_the_old_master_installs_nothing
24 tests, 0 failed, 31s wall with 4 jobs
exit=0
```

B. Repro at the parent: a worktree at 84ea957f with tests/test_pstack_kitchen.py from c40b7b0c (`git show c40b7b0c:tests/test_pstack_kitchen.py`):

```
$ python3 tests/run.py -k roster
exit=1
FAIL test_pstack_kitchen.AmpKitchenTests.test_rotate_master_snapshots_the_roster_it_reads_into_the_store (5.3s)
FAIL test_pstack_kitchen.AmpKitchenTests.test_rotate_master_refuses_a_roster_it_cannot_snapshot (7.8s)
FAIL: ... [parse]
FAIL: ... [secret]
FAIL: ... [binary]
FAIL: ... [unreadable]
FAIL test_pstack_kitchen.AmpKitchenTests.test_rotate_master_without_a_roster_records_that_there_is_none (4.2s)
FAIL test_pstack_kitchen.AmpKitchenTests.test_take_master_installs_the_snapshotted_roster_only_where_the_orb_has_none (7.1s)
ERROR: ... [no snapshot recorded]
ERROR: ... [a rerun keeps what it installed and the user then edited]
FAIL: ... [absent]
FAIL: ... [different]
FAIL: ... [unreadable]
24 tests, 4 failed, 21s wall with 4 jobs
```

The fifth new test (`..._without_a_roster_on_the_old_master_installs_nothing`) also passes at the parent. That is expected, because it asserts that nothing is printed or installed. At the head, the same 24 tests pass (block A).

C. What the tests drive. They run the real `kitchen.sh rotate-master` and `take-master` as subprocesses against the fake `amp`. take-master runs as the next master's thread, in a different cwd, with its own `PSTACK_KITCHEN_ROSTER`. The assertions mapped to Acceptance (from `git diff 84ea957f..c40b7b0c -- tests/test_pstack_kitchen.py`):

```
[absent]      assertIn(f'roster: installed {mine} from {snapshot}', out)
              assertEqual(mine.read_bytes(), self.ROSTER.encode())    # ROSTER holds [[sidekick.alternate]] x2 and [[verifier.fallback]] x2
              assertEqual(sorted(p.name for p in orb.iterdir()), [mine.name])   # no temp file left
[identical]   assertNotIn('roster:', take(self.ROSTER))
[different]   assertIn(f'roster: kept {mine}; it differs from {snapshot}', out); assertNotIn('codex', out); assertEqual(mine.read_text(), other)
[rerun]       user edits the installed file, the hand-over is replayed: 'roster: kept {mine}', file unchanged
[no snapshot] assertIn('roster: none recorded by the previous master', out)
[unreadable]  code=1, 'take-master: cannot read the roster', 'previous' still in pair.json (retryable)
refusals      parse / secret (token built at runtime) / binary / unreadable: code=5, path named,
              assertNotIn(secret, out), assertFalse(snapshot.exists()), assertNotIn('previous', state['master'])
```

D. Code path read: in `hand_to_next_thread`, `snapshot_roster` runs before `publish_master`, `m.new_master` and `record_handover`, so a refusal opens no hand-over. In `take_master`, `restore_roster(store)` runs after the pane check and before `wait_for_old_master` and the close. The install writes a temp file and then calls `os.link`, so `FileExistsError` keeps a file that appeared meanwhile.

E. Probe (break it): does a failed launch of the next master remove the snapshot? I used a probe worktree at c40b7b0c with one temporary test (removed afterwards). It writes `$FAKE/amp-ox-fail`, runs `rotate-master` with a valid roster, and asserts a non-zero exit, no `sessions/master-g2-roster.toml` and no `.master.previous`:

```
$ python3 tests/run.py -k zprobe
1 tests, 0 failed, 4s wall with 4 jobs
exit=0
```

F. Repo checks at the head:

```
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
exit=0
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, ...)
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c
pstack behavioral: pass
  exit 0 667.3s python3 tests/run.py
2016 tests, 0 failed, 666s wall with 4 jobs
gate=0
```

G. Testing standard: the new tests use no sleeps and no elapsed-time asserts. They apply no output masks or regex rewrites. Every subprocess is bounded: `run_sh(..., timeout=120)` and `as_thread` with `timeout=120`. They are hermetic: `PSTACK_KITCHEN_ROSTER` is set per test, `HOME` and `XDG_STATE_HOME` point at the temp dir, and `inherited_env()` scrubs the PSTACK_KITCHEN_* and related variables. tests/SPEC.md is current (F). Each source commit carries its tests:

```
fd05036e master.py +56, tests/SPEC.md, tests/test_pstack_kitchen.py +89
63ed7b84 master.py +52, tests/SPEC.md, tests/test_pstack_kitchen.py +97
c40b7b0c master.py (refactor/ToolFailed), tests/SPEC.md
```

## Flows driven

- pstack: `kitchen.sh rotate-master` followed by `take-master` from the next master's thread, with its own roster path, through the fake amp CLI. All four receiver states, the no-snapshot notice, the refusal table, and the failed-launch rollback probe were driven (Evidence A, B, C, E). A live Amp thread rotation was not driven. A real rotate-master would hand over this run's master, which is outside what a verifier may do.
- pstack-docs: the unit changes no docs, so no flow applies.

## Folded Acceptance

none

## Not covered

- A rotation between two real Amp orbs (live `amp -ox` and a real second orb's `~/.config/pstack/kitchen.toml`). Running a real rotate-master would rotate this run's live master. The fake-amp drive covers the same code path with separate roster paths for sender and receiver.

## Lessons

none
