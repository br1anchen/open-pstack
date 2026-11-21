# Verdict landing: the stack at daf51dc09

status: clean
units: landing
range: be88296826fbc6e1c7ab0ebf8a1dc26467c573cf..daf51dc09eeffc59c136c169e371c8641fcf8a99
verifier: amp, claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

## Evidence

The scratch named in the packet did not exist on this orb, so I created it at the head (`git worktree add --detach <scratch> daf51dc09`) and ran the Prove block there with `PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups`.

E1, setup and the behavioral gate (whole suite at the head):

```
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261121T152125-3766.log)
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base be88296826fbc6e1c7ab0ebf8a1dc26467c573cf
pstack behavioral: pass
  exit 0 681.7s python3 tests/run.py  log: .../logs/20261121T152133/pstack-behavioral-0.log
$ grep -E "tests, |^failed:" .../pstack-behavioral-0.log
2021 tests, 0 failed, 680s wall with 4 jobs
```

E2, the commit list (46 rebased subjects plus step 2, none twice, none already on main):

```
$ git rev-list --count be88296..daf51dc0          -> count new: 47
$ git rev-list --count c5cf2504..84ea957f         -> count old: 46
--- old subjects missing from new:                (empty)
--- new subjects not in old:
fix(skills/pstack-pair): nest --discover anchors a run with none at its current master, top-level
--- dup subjects in new:                          (empty)
--- new subjects also in main history (be88296):  (empty)
be88296 is ancestor of daf51dc0; merges in range: 0
$ git cherry be88296 daf51dc0 | cut -c1 | sort | uniq -c
     47 +
```

E3, the bug at the parent and the fix at the head. Parent scratch at `daf51dc09^` = 5cfcc1b0 (the rebased stack before step 2), with only the head's test changes applied:

```
$ git show daf51dc09 -- tests/test_pstack_kitchen.py | git apply
$ python3 tests/run.py -k nest_discover
AssertionError: {'thread': 'T-00000000-0000-4000-8000-000000000041'} != {'thread': 'T-aaaaaaaa-0000-4000-8000-000000000000'}
AssertionError: {'thread': 'T-00000000-0000-4000-8000-000000000051'} != {'thread': 'T-00000000-0000-4000-8000-000000000052'}
2 tests, 2 failed, 5s wall with 4 jobs
failed: ...test_nest_discover_takes_the_newest_master_session_when_the_pane_is_no_thread
failed: ...test_nest_discover_sets_the_current_master_as_a_top_level_anchor_and_records_the_threads_without_a_row_once
```

At the head, both pass inside the `-k nest` run (E4) and the whole suite (E1). The head test asserts the anchor is the current master (`AMP_MASTER`), its row is `(AMP_MASTER, 'master', '-', 'pending')` (top-level), roles sit under it, the archived master 41 appears nowhere in the output, and a second `--discover` adds nothing.

E4, 022's and PR #13's tests at the head:

```
$ python3 tests/run.py -k nest
17 tests, 0 failed, 8s wall with 4 jobs
$ python3 tests/run.py -k anchor
21 tests, 0 failed, 7s wall with 4 jobs
```

E5, the brief's static checks at the head:

```
$ python3 scripts/check-pstack.py          65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check        ok: tests/SPEC.md is current, 0 missing
$ uv lock --check                          Resolved 3 packages in 21ms
$ uv run --frozen ruff format --check      111 files already formatted
$ uv run --frozen ruff check               All checks passed!
$ uv check --frozen                        All checks passed!
```

E6, the nest.py seam (PR #13 and unit 022 both kept, one pending-row writer):

```
$ grep -n "^def " skills/pstack-pair/scripts/pstack_core/commands/nest.py
29 rows, 44 pending, 57 anchor, 89 anchor_title, 104 take_anchor, 135 current_master, 150 run_threads,
178 discover, 199 stamp, 203 record_pending, 213 add_pending, 222 under, 226 status_line, 232 nest, 268 done
$ grep -rn "oldest_master" skills/ tests/      (no source hits; only stale .pyc)
$ grep -n "f.write(" nest.py
218: pending row (add_pending)    277: nested row (done)
$ git diff be88296..HEAD -- tests/ | grep -E "^\+.*(time\.sleep|sleep\()"   (empty)
```

E7, edge probes in a throwaway scratch at the head (temporary tests driving `kitchen.sh nest <store> --discover` through the fake amp CLI, failing on purpose to print what they saw):

```
PROBE2 (anchor already set to ...61; pane is AMP_MASTER; one closed session ...60):
  anchor={'thread': '...061'} rows=[('...001', 'sidekick', '...061', 'pending')]
  -> an existing anchor is kept; no master row is added; old master 60 gets no row.
PROBE1 (no anchor, pane 'amp:1', no sessions):
  anchor={'thread': 'T-aaaaaaaa-...'} rows=[('...001', 'sidekick', 'T-aaaaaaaa-...', 'pending')]
  -> current_master finds nothing; record_pending's anchor() fallback (unchanged, pre-anchor stores) sets the caller's thread, with no top-level row.
```

## Flows driven

- pstack (nest --discover on a rotated Amp run): `kitchen.sh nest <store> --discover` through the fake amp CLI, at the parent (fails, E3) and the head (passes, E1/E4), plus two edge probes (E7).
- pstack (the rebased stack as a whole): the full behavioral gate at the head, 2021 tests (E1), and the commit-list checks (E2).
- pstack-docs (SKILL.md `--discover` sentence): `check-pstack.py` and `spec_gen.py --check` pass (E5); no user-facing flow beyond the text.

## Folded Acceptance

- `git log be88296..HEAD` holds every one of the 46 commits (by subject) plus the step 2 and step 3 commits, and nothing from 001-003/017-020 twice: held. There are 47 commits: all 46 subjects once plus step 2. The report says step 3 needed no fix, so it has no commit. No subject is on main already, and `git cherry` shows all 47 as new (E2).
- `discover` on a rotated run with no anchor sets the current master, recorded top-level; the test fails without step 2: held. Both tests fail at 5cfcc1b0, because the anchor became the oldest master (41 and 51), and both pass at daf51dc0 (E3, E4, E1).
- 022's and PR #13's tests pass, and so does the whole suite: held. `-k nest` ran 17 tests and `-k anchor` ran 21, all passing; the whole suite ran 2021 tests with 0 failed (E4, E1).

## Not covered

- bug-fix.step-5 (failing test lands before the fix): the step 2 test and fix are one commit, daf51dc0. That follows AGENTS.md ("Commit a source change in the same commit as the test that pins it"), and E3 shows the failure by applying the test alone at the parent. Not a finding.
- Observation, outside this Acceptance: on a store with no anchor and no master thread on record (PROBE1), the anchor comes from the older `anchor()` fallback and gets no top-level nest row. The report keeps that path on purpose for stores made before anchors existed.
- Real Amp threads and `update_thread` nesting were not driven; the fake amp CLI is the repo's surface for this.

## Lessons

- L012: held. `uv check --frozen` passes at the head (E5).
