# Verdict 039: roster-handoff-fix

status: clean
units: 010 039
range: 7218f3112db91d4448b20d708b5c92117b6543db..fc6d096b77343386ce9f5ee5b45320ab6abb35f7
verifier: amp (claude-opus-5-5), escalated
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes. None of these breaks Acceptance:

- No committed test asserts the git-store relay line (`rg "relay: upload_thread_file" tests/` finds only steer's relay, at lines 17750 and 17781). I proved the line with my own probe (Evidence E4). Step 2 of the brief asked for only the (store x snapshot) table test, so this is not a finding. A later change could still drop the line without any test failing.
- The docs and the relay's second line say "take-master waits for your turn to end". `wait_for_old_master` waits at most `KITCHEN_TAKEOVER_WAIT_S` (30 s by default; `master.py` lines 782-786). If the old master uploads after that, the next master keeps its own roster and prints `did not arrive`. The brief fixed this wording.
- The packet's scratch worktree did not exist on this orb. I created it with `git worktree add --detach <scratch> fc6d096b` and checked it (E0).

## Evidence

E0: the scratch is a git work tree at the head
```
$ git worktree add --detach .../039-roster-handoff-fix-verify fc6d096b77343386ce9f5ee5b45320ab6abb35f7
HEAD is now at fc6d096b docs(skills/pstack-kitchen): the roster snapshot of a master hand-over and its upload under the git store
$ git rev-parse HEAD; git status --short
fc6d096b77343386ce9f5ee5b45320ab6abb35f7
(clean)
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261123T201533-4456.log)
```

E1: brief commands, each run at the head in a fresh shell with PSTACK_KITCHEN_ROLE=verifier and PSTACK_KITCHEN_RUN=kitchen-followups
```
$ python3 tests/run.py -k roster
25 tests, 0 failed, 32s wall with 4 jobs
$ python3 tests/run.py -k take_master
15 tests, 0 failed, 39s wall with 4 jobs
$ python3 tests/run.py -k gitstore
56 tests, 0 failed, 61s wall with 4 jobs
$ python3 scripts/check-pstack.py -> exit 0: 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check -> exit 0: ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check -> exit 0: 111 files already formatted
$ uv run --frozen ruff check -> exit 0: All checks passed!
$ uv check --frozen -> exit 0: All checks passed!
```
The tests selected by -k roster and -k take_master include 010's tests: test_rotate_master_snapshots_the_roster_it_reads_into_the_store, test_rotate_master_refuses_a_roster_it_cannot_snapshot, test_take_master_installs_the_snapshotted_roster_only_where_the_orb_has_none, test_take_master_without_a_roster_on_the_old_master_installs_nothing and test_take_master_in_the_new_thread_from_the_bundle_it_received. They also include 039's tests: test_the_host_roster_snapshot_stays_off_the_branch_and_the_branch_ignores_it and the take_master table, under both AmpKitchenTests and AmpGitKitchenTests.

E2: the Prove gate, which runs the full suite (brief command 4)
```
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c
pstack behavioral: pass
  exit 0 707.1s python3 tests/run.py  log: .../logs/20261123T201535/pstack-behavioral-0.log
(log tail) 2021 tests, 0 failed, 706s wall with 4 jobs
```

E3: failing at the parent, passing at the head. I made a second scratch at c40b7b0c (unit 010's head, the parent of 039's fix), put the head's tests/test_pstack_kitchen.py and tests/test_pstack_core.py in it, and changed nothing else.
```
$ python3 tests/run.py -k test_the_host_roster_snapshot_stays_off        (at c40b7b0c) exit=1
AssertionError: 'sessions/master-g2-roster.toml' unexpectedly found in {'pair.json', ..., '.gitignore', 'sessions/master-g2-roster.toml', 'events/master-g0.tsv', ...}
$ python3 tests/run.py -k test_gitstore_ignores_exactly_the_local_class   (at c40b7b0c) exit=1
AssertionError: 'sessions/*-roster.toml' not found in ['.*', '!.gitignore', '*.pid', '*.tmp', '*.lock', '*.tgz', 'scratch/', 'steps/jobs/']
$ python3 tests/run.py -k test_take_master_installs_a_snapshot_that_is_there   (at c40b7b0c) exit=1
FAIL: ...AmpGitKitchenTests.test_take_master_installs_a_snapshot_that_is_there_and_a_missing_one_ends_only_the_local_store [missing]
AssertionError: 1 != 0 : kitchen.sh: take-master: cannot read the roster snapshot .../sessions/master-g2-roster.toml: No such file or directory
$ python3 tests/run.py -k test_a_publish_that_fails_after_the_snapshot   (at c40b7b0c) exit=1
AssertionError: True is not false        (the snapshot is left behind)
$ python3 tests/run.py -k test_a_launch_failure_with_the_snapshot_already_gone   (at c40b7b0c) exit=1
AssertionError: 'the next master thread did not start: boom launch' not found in "kitchen.sh: [Errno 2] No such file or directory: '.../sessions/master-g2-roster.toml'\n"
$ python3 tests/run.py -k test_take_master_installs_the_snapshotted_roster_only_where   (at c40b7b0c) exit=1
FAIL: ... [a link to a roster that is not there]
AssertionError: 'take-master: cannot read the roster .../orb2-roster.toml' not found in "kitchen.sh: [Errno 2] No such file or directory: '.../orb2/orb2-roster.toml'\n"
FAIL: ... [a roster directory that cannot be made]
AssertionError: 'take-master: cannot create .../orb2/linkdir' not found in "kitchen.sh: [Errno 17] File exists: '.../orb2/linkdir'\n"
$ python3 tests/run.py -k test_take_master_installs_the_snapshotted_roster_only_where   (at fc6d096b)
1 tests, 0 failed, 3s wall with 4 jobs
```
All of these pass at the head (E1, E2).

E4: probe of the relay line. The script is .amp/in/orb-backup/v039/probe_relay.py, outside the repo. It reuses AmpKitchenTests and AmpGitKitchenTests and calls kitchen.sh rotate-master through the fake amp.
```
$ python3 .amp/in/orb-backup/v039/probe_relay.py <scratch>/tests
test_probe_local_roster ... ok
test_probe_git_no_roster ... ok
test_probe_git_roster ... ok
Ran 3 tests in 7.051s  OK

--- store=local roster=True
    next master: demo-master-g2 (generation 2) in thread T-00000000-0000-4000-8000-000000000002; it runs take-master there
    next: end your turn now, with no further commands: ...
   relay lines: 0 | ROSTER text in output: False
--- store=git roster=False
    next master: demo-master-g2 (generation 2) in thread T-00000000-0000-4000-8000-000000000002; ...
   relay lines: 0 | ROSTER text in output: False
--- store=git roster=True
    next master: demo-master-g2 (generation 2) in thread T-00000000-0000-4000-8000-000000000002; it runs take-master there
    relay: upload_thread_file path=/tmp/tmp_9gqpw_a/state/pstack/kitchen/runs/demo/sessions/master-g2-roster.toml destination=/tmp/tmp_9gqpw_a/state/pstack/kitchen/runs/demo/sessions/master-g2-roster.toml thread=T-00000000-0000-4000-8000-000000000002
    relay: upload it before you end this turn; take-master on the next master waits for your turn to end
    next: end your turn now, with no further commands: ...
   relay lines: 2 | ROSTER text in output: False
```
The probe asserts three things under the git store with a roster: the first relay line equals `relay: upload_thread_file path=<abs snapshot> destination=<abs snapshot> thread=<amp_thread(2)>`, the second line says to upload before ending the turn, and both come before the `next: end` line. It asserts no relay line under the local store and none under the git store without a roster.

E5: the diff matches the brief's mechanism (`git diff c40b7b0c..fc6d096b`, 7 files, all in Scope)
```
 skills/pstack-kitchen/SKILL.md                     |  12 ++
 skills/pstack-kitchen/references/master.md         |  10 ++
 .../pstack-kitchen/scripts/kitchen_core/master.py  | 120 +++++++++++-----
 skills/pstack-pair/scripts/pstack_core/gitstore.py |   6 +
 tests/SPEC.md                                      |  80 +++++------
 tests/test_pstack_core.py                          |   2 +
 tests/test_pstack_kitchen.py                       | 158 +++++++++++++++++++++
gitstore.py: +_rule('roster snapshots of a master hand-over ...', r'sessions/master-g\d+-roster\.toml', LOCAL, ignore=['sessions/*-roster.toml'])  (placed before the per-writer-generation rules and the master's sessions/ rule)
master.py hand_to_next_thread: publish_master/new_master/record_handover moved inside `try: ... except BaseException: drop_snapshot(store, roster); raise`
master.py drop_snapshot: Path(...).unlink(missing_ok=True); the launch-failure rollback calls drop_snapshot in place of os.unlink
master.py take_master: wait_for_old_master(prev) now precedes restore_roster(store)
master.py restore_roster: FileNotFoundError and gitstore.is_git(store) -> "roster: <snapshot> did not arrive from the previous master; this orb keeps its own roster", return; otherwise ToolFailed as in 010
master.py held_roster/install_roster: a dangling link -> ToolFailed "cannot read the roster <target>: a link to a file that is not there"; mkdir OSError -> ToolFailed "cannot create <parent> for the roster"
```

E6: test hygiene in the range's test diff
```
$ git diff c40b7b0c..fc6d096b -- tests/test_pstack_core.py tests/test_pstack_kitchen.py tests/pstack-pair tests/run.py | grep -nE "^\+.*(sleep|re\.sub|timeout|subprocess|time\.time|assertLess|replace\()"
(no output)
```
The added tests call no sleep, assert no elapsed time and mask no output. They start processes only through the existing run_sh and as_thread helpers, which are bounded (run_sh has timeout=120). The wrapper `amp` script in test_a_launch_failure_... runs `exec` into the fake amp, which returns. Each source change is in the same commit as its test: 484b957b has gitstore.py with both test files, and f0bfd92a has master.py with test_pstack_kitchen.py. spec_gen --check is current (E1).

E7: the docs (fc6d096b)
```
SKILL.md (Amp threads, master rotation bullet): "... under the git store the snapshot is a local-only file the branch ignores: `rotate-master` prints a `relay: upload_thread_file` line, the old master uploads the file to the next master's thread before ending its turn, and `take-master` continues with its own roster, saying `did not arrive`, when it is not there. Under the local store the bundle carries it."
master.md (Rotate yourself): "... Under the git store the snapshot is a local-only file the branch never holds ... `rotate-master` prints `relay: upload_thread_file path=... destination=... thread=...`; make that upload ... before you end your turn ... When it does not arrive, `take-master` continues with the next orb's own roster."
```

## Flows driven

- pstack: I drove the kitchen.sh rotate-master and take-master commands through the fake amp, under both the local and the git store (E1, E3, E4), and ran the full behavioral gate (E2).
- pstack-docs: check-pstack.py and spec_gen --check pass, and I read the two doc sections (E1, E7). The docs have no user flow beyond that.

## Folded Acceptance

none

Unit 010 Acceptance at fc6d096b:
- A rotate-master and take-master onto an orb with no roster leave the old master's roster byte for byte at `roster_path()`: held. The take_master table's `present` case asserts `mine.read_bytes() == kept` under both stores, and 010's test_take_master_installs_the_snapshotted_roster_only_where_the_orb_has_none passes (E1, E2).
- A roster already on the receiving orb is never overwritten, and a different one is reported by path: held. 010's table, which includes "a rerun keeps what it installed and the user then edited", passes (E1). The code path `roster: kept <target>; it differs from <snapshot>` is unchanged (E5).
- A roster that fails the secret scan or the TOML parse refuses the rotation and writes no snapshot: held. test_rotate_master_refuses_a_roster_it_cannot_snapshot passes (E1).
- A store with no recorded snapshot takes over as before, with the notice line: held. test_take_master_without_a_roster_on_the_old_master_installs_nothing passes (E1, E2).

Unit 039 Acceptance at fc6d096b:
- Under the git store, a rotate-master with a host roster pushes no `sessions/*-roster.toml` to the store branch, and the branch's .gitignore lists it: held. The test checks the fake origin's tree, `git log -- <path>` and `kitchen/demo:.gitignore`. It passes at the head and fails at the parent (E1, E3).
- Under the git store, rotate-master prints the upload relay line naming the next master thread: held (E4). Under the local store the roster still reaches the next master's orb: held. The bundle-content assertion in test_rotate_master_snapshots_the_roster_it_reads_into_the_store passes (E1), and the probe shows no relay line under the local store (E4).
- take-master under the git store with the snapshot missing prints `did not arrive` and exits 0 with the hand-over done, and with the snapshot present installs it: held. The table asserts the line, exit 0 and that `previous` is cleared. It passes at the head and fails at the parent (E1, E3).
- The three lows hold: held. (a) test_a_publish_that_fails_after_the_snapshot_leaves_no_snapshot_behind; (b) test_a_launch_failure_with_the_snapshot_already_gone_keeps_the_launch_error; (c) the dangling-link and mkdir subtests of 010's table, both ToolFailed with no traceback. Each fails at the parent and passes at the head (E3). 010's existing tests pass (E1, E2).
- The docs describe the git-store hand-over, and check-pstack and spec_gen --check pass: held (E1, E7).

## Not covered

- I made no real `upload_thread_file` between two live Amp threads. The fake amp covers the rotate-master and take-master surface. The upload itself is a tool call the master makes, not kitchen code.

## Lessons

- L012: held. `uv check --frozen` passes (E1). The new code adds no unchecked Optional: `re.match` and `spec_from_file_location` are not used in the diff (E5).
