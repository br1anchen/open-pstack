# Verdict 044: roster-relay-repo

status: clean
units: 042 044
range: be88296826fbc6e1c7ab0ebf8a1dc26467c573cf..57bf35b89495b4f9822cb2606fdc321ca29541f6
verifier: claude (Amp thread), claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Note: the packet's scratch worktree did not exist on this orb; I created it at the packet's head with `kitchen.sh scratch <store> 044-roster-relay-repo-verify --at 57bf35b8` (Evidence E0). A second scratch at 042's head (`044-base-probe`) was used for the base comparison and removed.

## Evidence

E0: scratch at the unit's head, setup
```
$ kitchen.sh scratch <store> 044-roster-relay-repo-verify --at 57bf35b89495b4f9822cb2606fdc321ca29541f6
Preparing worktree (detached HEAD 57bf35b8)
$ git rev-parse HEAD
57bf35b89495b4f9822cb2606fdc321ca29541f6
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261123T224144-5842.log)
```

E1: Prove gate
```
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c
pstack behavioral: pass
  exit 0 900.3s python3 tests/run.py  log: .../logs/20261123T224145/pstack-behavioral-0.log
(log tail) 2023 tests, 0 failed, 899s wall with 4 jobs
```

E2: Brief commands 1-3
```
$ python3 tests/run.py -k roster
26 tests, 0 failed, 34s wall with 4 jobs
$ python3 tests/run.py -k take_master
17 tests, 0 failed, 36s wall with 4 jobs
$ python3 tests/run.py -k gitstore
56 tests, 0 failed, 32s wall with 4 jobs
```

E3: Brief command 4 (full suite, separate run)
```
$ python3 tests/run.py
2023 tests, 0 failed, 845s wall with 4 jobs
exit=0
```

E4: Brief commands 5-9
```
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check
| total | 435 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check
111 files already formatted
$ uv run --frozen ruff check
All checks passed!
$ uv check --frozen
All checks passed!
(each exit=0)
```

E5: the new tests fail on 042's head (e9dc6fe0) with 044's tests copied in, so they pin the fixes
```
$ python3 tests/run.py -k finds_the_upload_in_its_own_repo   # in 044-base-probe
FAIL ... (old='old-master-checkout')
AssertionError: 'roster: installed .../orb2-roster.toml from .../sessions/master-g2-roster.toml' not found in 'roster: .../sessions/master-g2-roster.toml did not arrive from the previous master; this orb keeps its own roster ...'
FAIL ... (old='other-repo')
AssertionError: b'not the roster\n' != b'<roster fixture bytes elided>'
1 tests, 1 failed
$ python3 tests/run.py -k test_gitstore_ignores_exactly_the_local_class   # in 044-base-probe
FAIL ... (table='sessions/master-gx-roster.toml')
FAIL ... (table='sessions/master-g-roster.toml')
1 tests, 1 failed
```
At 044's head both pass (inside E2/E3).

E6: the 044 fix, from the range
```
$ git log --oneline e9dc6fe01a2c..HEAD
57bf35b8 fix(skills/pstack-pair): the roster snapshot's LOCAL rule matches exactly the paths its ignore line ignores
4a1eeef3 fix(skills/pstack-kitchen): take-master finds the uploaded roster in this orb's own repository, not the old master's paths in pair.json
$ git diff e9dc6fe01a2c..HEAD -- skills/   (excerpt)
-    root = bundle.repo_of(store)
-    uploaded = Path(root, relay_name(rel)) if root else None
+    common = git.out(store, 'rev-parse', '--path-format=absolute', '--git-common-dir')
+    uploaded = Path(common).parent / relay_name(rel) if common else None
-        r'sessions/master-g\d+-roster\.toml',
+        r'sessions/master-g[^/]*-roster\.toml',
```
Each fix commit carries its test (test_pstack_kitchen.py:19882, test_pstack_core.py:22327).

E7: break attempt, classify vs git check-ignore over the brief's table plus 10 extra edge paths, against gitignore_text() in a fresh temp repo (probe script in the scratch, run with python3 -I, then deleted)
```
ok  local=True  ignored=True  sessions/master-g2-roster.toml
ok  local=True  ignored=True  sessions/master-g12-roster.toml
ok  local=True  ignored=True  sessions/master-gx-roster.toml
ok  local=True  ignored=True  sessions/master-g-roster.toml
ok  local=False ignored=False sessions/other-roster.toml
ok  local=False ignored=False sessions/master-g2-handoff.md
ok  local=False ignored=False sessions/a/master-g2-roster.toml
ok  local=False ignored=False sessions/master-g2-roster.tomlx
ok  local=False ignored=False sessions/master-g2-roster.toml.bak
ok  local=False ignored=False xsessions/master-g2-roster.toml
ok  local=False ignored=False a/sessions/master-g2-roster.toml
ok  local=True  ignored=True  sessions/master-g2-x-roster.toml
ok  local=True  ignored=True  sessions/master-g2-roster-roster.toml
ok  local=True  ignored=True  sessions/master-g 2-roster.toml
ok  local=False ignored=False sessions/master-g2-prompt.md
ok  local=True  ignored=True  sessions/master-g2-roster.toml.tmp
ok  local=False ignored=False briefs/master-g2-roster.toml
mismatches 0
```

E8: the real surface the fix relies on: the live run store on this orb is a worktree of this orb's repo, and its git-common-dir is that repo's .git
```
$ git -C <store> rev-parse --path-format=absolute --git-common-dir
/home/user/workspace/repo/.git
$ git -C <store> rev-parse --abbrev-ref HEAD
kitchen/kitchen-followups
```

E9: unit 042's relay line and docs
```
$ grep -n "destination=.git/pstack-roster-g" tests/*.py
tests/test_pstack_kitchen.py:18578: f'relay: upload_thread_file path={self.store}/{snapshot} destination=.git/pstack-roster-g2.toml thread={amp_thread(2)}\n',
(test continues: assertIn 'take-master on the next master waits at most 30 s for your turn to end', and assertNotIn snapshot in the branch tree)
$ grep -n "KITCHEN_TAKEOVER_WAIT_S\|waits at most" skills/pstack-kitchen/SKILL.md skills/pstack-kitchen/references/master.md
SKILL.md:489:    its turn. `take-master` waits at most `KITCHEN_TAKEOVER_WAIT_S` (30 s by
references/master.md:301:  end your turn. `take-master` waits at most `KITCHEN_TAKEOVER_WAIT_S` (30 s by
$ grep -n "pstack-roster-g" SKILL.md master.md
SKILL.md:487:    repo-relative `.git/pstack-roster-g<n>.toml` (the next orb keeps its store
master.md:299:  path=... destination=.git/pstack-roster-g<n>.toml thread=...`; make that
```
042's take-master row (`test_take_master_under_the_git_store_installs_a_roster_uploaded_to_the_repos_git_dir`) asserts install from `.git/pstack-roster-g2.toml`, the upload removed, and `did not arrive from the previous master` with neither present; it passes in E2/E3.

E10: standing test check on the range's new tests
The new test's only subprocess is `subprocess.run(['git', 'init', ...], check=True, timeout=60)`; the gitstore table uses `git.ok(...)` and no output mask or normalisation. No `time.sleep`.

## Flows driven

- pstack: take-master under the git store with pair.json naming the old master's paths (missing path, and a decoy repo) driven through `kitchen.sh take-master` on the fake amp in the repo's tests, compared against 042's head (E5, E2); the store-to-repo lookup checked on this orb's live store (E8); classify vs git check-ignore driven directly (E7).
- pstack-docs: SKILL.md and master.md relay and wait lines read (E9); check-pstack passes (E4).

## Folded Acceptance

none

Unit 042:
- git-store relay line names `destination=.git/pstack-roster-g<n>.toml`: held (E9).
- take-master installs a roster found only at `.git/pstack-roster-g<n>.toml` with the store at a different absolute path, removes the upload; neither present prints `did not arrive` and exits 0: held (E9, E2, E5).
- `.gitignore` line matches exactly the LOCAL rule's paths: held (E7, E5).
- committed test asserts the git-store relay line; docs and line state the bounded wait: held (E9).
- 010's and 039's tests pass; check-pstack and spec_gen --check pass: held (E1, E3, E4).

Unit 044:
- with pair.json naming the old master's checkout path, take-master installs the upload from this orb's repo `.git` and removes it: held (E5, E2, E8).
- for every path in step 2's table, classify is LOCAL exactly when git ignores it: held (E7).
- 010's, 039's and 042's tests pass; spec_gen --check passes: held (E3, E4).

## Not covered

A real two-orb master hand-over (upload_thread_file into a second orb) was not driven; it is covered by the fake-amp tests and the live-store git-common-dir check (E8).

## Lessons

L012: held (`uv check --frozen` passes, E4; `relay_name` asserts its re.search result is not None; `git.out` result checked before use).
