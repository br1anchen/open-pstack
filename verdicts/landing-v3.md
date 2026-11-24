# Verdict landing: landing-rebase-3

status: clean
units: landing
range: eb8228f9a7dc6466e40f8674913b3bea3e8c52eb..699837d853a868ed785fa843cb167ee3e559c3bc
verifier: amp (claude-opus-5-5)
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes (not findings, outside this landing's Acceptance):
- `kitchen.sh help` does not list the `snooze` command, although it dispatches (Evidence E7). The source stack head e82b720a has the same help text, so the rebase did not cause this; it is a possible follow-up for the snooze docs.
- The packet's scratch worktree did not exist on this orb (the packet was written on the master's orb). I created it with `kitchen.sh scratch <store> 000-landing-verify --at 699837d8` at the packet's path (Evidence E1).

## Evidence

E1: scratch at the head; git sees it as a work tree; setup
```
$ kitchen.sh scratch <store> 000-landing-verify --at 699837d853a868ed785fa843cb167ee3e559c3bc
Preparing worktree (detached HEAD 699837d8)
/home/user/.local/state/pstack/scratch/runs-kitchen-followups/000-landing-verify
$ git rev-parse HEAD; git rev-parse --is-inside-work-tree
699837d853a868ed785fa843cb167ee3e559c3bc
true
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261124T004139-4744.log)
```

E2: the 15 commits, in order, none twice, none already on main
```
$ git rev-parse origin/main
eb8228f9a7dc6466e40f8674913b3bea3e8c52eb
$ git rev-list --count eb8228f9..HEAD
15
$ diff <(git log --reverse --format=%s daf51dc0..e82b720a; git log --reverse --format=%s 84ea957f..57bf35b8) <(git log --reverse --format=%s eb8228f9..699837d8) && echo SUBJECTS-MATCH-IN-ORDER
SUBJECTS-MATCH-IN-ORDER
$ git log --format=%s eb8228f9..699837d8 | sort | uniq -d     # duplicates
(no output)
$ for each commit in range: grep its subject in `git log --format=%s origin/main`
(no "ALREADY ON MAIN" lines)
```
Subjects at the head (oldest last):
```
699837d8 fix(skills/pstack-pair): the roster snapshot's LOCAL rule matches exactly the paths its ignore line ignores
d0eb76ca fix(skills/pstack-kitchen): take-master finds the uploaded roster in this orb's own repository, not the old master's paths in pair.json
e708a38c docs(skills/pstack-kitchen): the roster upload names .git/pstack-roster-g<n>.toml and the bounded take-over wait
29a861d7 fix(skills/pstack-kitchen): the git-store roster relay uploads to a repo-relative path take-master finds
9cf71075 fix(skills/pstack-pair): the roster snapshot's ignore line matches exactly the paths the LOCAL rule classifies
e1b06d80 docs(skills/pstack-kitchen): the roster snapshot of a master hand-over and its upload under the git store
71c1ae9b fix(skills/pstack-kitchen): the git-store roster snapshot is relayed by upload, a missing one does not end take-master, and the rollback is safe
fe86c99b fix(skills/pstack-pair): the roster snapshot of a master hand-over is a local-only store file
77d6e9b1 fix(skills/pstack-kitchen): restore_roster fails with ToolFailed and take_master stays under the function size limit
6dd80ebb feat(skills/pstack-kitchen): take-master installs the snapshotted roster where the next master's orb has none
8c6a3b56 feat(skills/pstack-kitchen): rotate-master snapshots the host roster into the store for the next master
c1ed8932 fix(skills/pstack-pair): snooze refusals are PairErrors raised from record, one command holds the export cache
d50b5eee fix(skills/pstack-pair): snooze review fixes: publish snooze.tsv, check every plan recipient, refuse unsnoozable --done, export once
6634ee28 docs(skills/pstack-kitchen): the snooze of idle role threads, its wake and the relay duty
315d5736 feat(skills/pstack-pair): the master snoozes idle role threads and wakes them before work
```

E3: content of each rebased commit equals its source, apart from tests/SPEC.md
```
$ git range-diff daf51dc0..e82b720a eb8228f9..c1ed8932 -- ':!tests/SPEC.md'
1:  5e82657f = 1:  315d5736 feat(skills/pstack-pair): the master snoozes idle role threads ...
2:  bee0dc78 = 2:  6634ee28 docs(skills/pstack-kitchen): the snooze of idle role threads ...
3:  de00010d = 3:  d50b5eee fix(skills/pstack-pair): snooze review fixes ...
4:  e82b720a = 4:  c1ed8932 fix(skills/pstack-pair): snooze refusals are PairErrors ...
$ git range-diff 84ea957f..57bf35b8 c1ed8932..699837d8 -- ':!tests/SPEC.md'
 1:  fd05036e =  1:  8c6a3b56 ...   2:  63ed7b84 =  2:  6dd80ebb ...
 3:  c40b7b0c !  3:  77d6e9b1 ...   4:  484b957b !  4:  fe86c99b ...   5:  f0bfd92a !  5:  71c1ae9b ...
 6..11: all "="
(the "!" entries differ only in hunk context lines, for example:
    -@@ ...master.py: def new_session_takes_run(store, here, pane):
    +@@ ...master.py: def close_old_pane(prev, prev_pane, pane):
    -     # Close the pane only while it still holds the old master, or nothing.
    -         self.assertIsNone(gitstore.classify('reports/a/b.md'))
    +         self.assertEqual(gitstore.classify('snooze.tsv').kind, gitstore.MASTER)
 no +/- line of any commit's own change differs; 61 lines total)
```

E4: the master.py conflict resolution keeps both sides
```
$ git grep -n -E 'def (take_master|wait_for_old_master|restore_roster|close_old_pane|take_anchor)|wait_for_old_master\(prev\)|restore_roster\(store\)|close_old_pane\(prev' 699837d8 -- skills/pstack-kitchen/scripts/kitchen_core/master.py
master.py:579:def restore_roster(store):
master.py:819:def take_anchor(store):
master.py:832:def close_old_pane(prev, prev_pane, pane):
master.py:848:def wait_for_old_master(prev):
master.py:855:def take_master(ctx, args):
master.py:881:    wait_for_old_master(prev)
master.py:882:    restore_roster(store)
master.py:884:    closed, host = close_old_pane(prev, prev_pane, pane)
```

E5: Prove gate, the full suite at the head
```
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base eb8228f9a7dc6466e40f8674913b3bea3e8c52eb
pstack behavioral: pass
  exit 0 546.7s python3 tests/run.py  log: .../logs/20261124T004152/pstack-behavioral-0.log
(log tail) 2042 tests, 0 failed, 545s wall with 4 jobs
```

E6: the brief's Verify commands, each run from the scratch root
```
$ test "$(git rev-list --count origin/main..HEAD)" -ge 15
exit=0
$ python3 tests/run.py -k snooze
5 tests, 0 failed, 6s wall with 4 jobs
$ python3 tests/run.py -k roster
26 tests, 0 failed, 7s wall with 4 jobs
$ python3 tests/run.py -k take_master
23 tests, 0 failed, 12s wall with 4 jobs
$ python3 tests/run.py
(E5) 2042 tests, 0 failed
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
exit=0
$ python3 tests/spec_gen.py --check
| total | 437 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
exit=0
$ uv lock --check
Resolved 3 packages in 16ms
exit=0
$ uv run --frozen ruff format --check
112 files already formatted
exit=0
$ uv run --frozen ruff check
All checks passed!
exit=0
$ uv check --frozen
All checks passed!
exit=0
```

E7: `kitchen.sh snooze` driven by hand at the head, against a copy of the run store inside the scratch with its git backend removed. Under PSTACK_MUX=amp it read real thread states; from this non-master thread it lists none.
```
$ kitchen.sh snooze <copy>
snooze: nothing to snooze
exit=0
$ kitchen.sh snooze <copy> --done T-nope
kitchen.sh: not recorded for snoozing: T-nope
exit=1
$ kitchen.sh snooze <copy> --bogus
kitchen.sh: usage: kitchen.sh snooze <store> [--done <thread>... | --wake <thread>...]
exit=1
$ PSTACK_SNOOZE_ROLES=0 kitchen.sh snooze <copy>
snooze: off (PSTACK_SNOOZE_ROLES=0)
exit=0
$ kitchen.sh help | grep -n -i -E 'snooze|take-master|rotate-master'
69:  rotate-master <store> [--model M]   record the filled handoff and this master's session, and start the
73:  take-master <store>                 the new master's first command: close the old master's pane and
(no snooze line; at e82b720a cli.py registers 'snooze' the same way and help is the same)
```

E8: before (base eb8228f9): neither stack is on main
```
$ git ls-tree -r --name-only eb8228f9 skills/pstack-pair/scripts/pstack_core/commands | grep -c snooze
0
$ git grep -c restore_roster eb8228f9 -- skills/pstack-kitchen/scripts/kitchen_core/master.py
exit=1
```

E9: testing standard on the range's added test lines
```
$ git diff eb8228f9..699837d8 -- tests/test_pstack_core.py tests/test_pstack_kitchen.py | grep '^+' | grep -nE 'time\.sleep|sleep\(|elapsed|time\.time\(\)|re\.sub|subprocess\.(run|Popen|check)|timeout'
192:+            re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL)
266:+        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1', code=None)
681:+        subprocess.run(['git', 'init', '-q', str(other)], check=True, timeout=60)
$ ... | grep '^+' | grep -nE 'AMP_THREAD_ID|PSTACK_MUX|HERDR_|environ'
(no output)
```
No sleep and no elapsed-time assert. The one re.sub fills the consult template's placeholders to build input; it does not mask output under test. The subprocesses are bounded (timeout=60, --timeout 1).

## Flows driven

- pstack: `kitchen.sh snooze` list, `--done` for an unknown thread, a bad flag, and the off switch, at the head against a copy of the live store (E7). The rotate-master roster snapshot and take-master restore need a two-master hand-over, which I could not drive from one verifier thread without touching the live run. Those flows are proved through the fake-amp kitchen.sh tests (`-k roster` 26, `-k take_master` 23, all pass, E6), and the seam with eb8228f9's take_master structure is shown in E4.
- pstack-docs: check-pstack and spec_gen --check pass (E6); no user-facing flow beyond the docs.

## Folded Acceptance

- `git log origin/main..HEAD` holds the 15 commits by subject (4 snooze, 11 roster) plus any step 2 fix commits, and none twice: held (E2, E3). There are no step 2 fix commits, and each commit's own change equals its source outside tests/SPEC.md.
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head: held (E5, E6).

## Not covered

- A live rotate-master to take-master hand-over with a roster snapshot on real Amp threads. It would start a master thread on the live run, so it is covered by the fake-amp tests only (E6).
- `snooze --done` / `--wake` on a real registered thread. Recording rows needs a master thread and settled role threads; the copy-store drive covered only the list, the error and the off paths (E7). The tests cover the rest (`-k snooze`, 5 pass).

## Lessons

L012: held (`uv check --frozen` passes at the head, E6)
