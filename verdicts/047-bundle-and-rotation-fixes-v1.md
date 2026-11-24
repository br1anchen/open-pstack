# Verdict 047: bundle-and-rotation-fixes

status: clean
units: 047
range: 699837d853a868ed785fa843cb167ee3e559c3bc..973660250d7aa41552d66f9f0e837c38f6006699
verifier: amp claude-code claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

## Evidence

Setup note: the packet's scratch path did not exist on this orb; I created it with `git worktree add --detach <scratch> 973660250...` (HEAD = 97366025, tree clean, 8 commits over the base). Environment: `PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=kitchen-followups`. `kitchen.py ... setup` printed `setup: ok (1 commands, ...)`.

Acceptance 1 and 2: each fix's test fails on the parent's source and passes at the head. I made a second scratch at the head, ran `git checkout 699837d8 -- skills` (parent source, head tests), and ran each new or changed test:

```
-k a_late_reply_never_deletes      FileNotFoundError: .../store/verdicts/001-x-v1.md   -> 1 tests, 1 failed   (step 1: reply deleted the master-written verdict)
-k a_hand_over (origin not fetchable)  AssertionError: '4193947778c2...' != ''           -> FAILED (failures=1)  (step 2: tracking-ref cut, bundle not self-contained)
-k the_origin_fetch_of_a_hand_over_bundle...carries_every_commit   FAIL                  (step 2)
-k receivers_fallback_fetch        KeyError: 'timeout'                                   -> 1 tests, 1 failed   (step 2: receiver fetch unbounded)
-k carries_only_its_own (ordinary send cut like a hand-over)   KeyError: 'baseline'      -> 1 tests, 1 failed   (step 3)
-k keeps_local_only_evidence       AssertionError: 'evidence: 1 commits kept under refs/pstack/keep' not found in ['store: 0 updated, 0 removed', 'commits: the git bundle did not apply', 'checkout: 57432961e was not carried and the remote lacks it']   (step 3: local-only evidence lost)
-k the_rotation_flag_goes_with     FAIL (how='rotation', ready=False) AssertionError: False is not True; FAIL (how='spawn after close', ready=False) AssertionError: False is not True   -> FAILED (failures=2)  (step 4: a never-READY bootstrap could not be rotated again; the READY rows pass on the parent, as the report says)
-k spawn_on_an_orb_with_no_host_roster   AssertionError: 1 != 4 : kitchen.sh: no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)   -> 1 tests, 1 failed   (step 5)
```

At the head the same tests pass (inside the runs below). Brief commands 1-3, in the head scratch:

```
$ python3 tests/run.py -k bundle
30 tests, 0 failed, 51s wall with 4 jobs
$ python3 tests/run.py -k rotat
75 tests, 0 failed, 177s wall with 4 jobs
$ python3 tests/run.py -k spawn
23 tests, 0 failed, 32s wall with 4 jobs
```

Acceptance 3 (command 4, full suite, run once with nothing else changing the tree):

```
$ python3 tests/run.py
    46.1s  test_pstack_core.CoreTests.test_dispatch_behaviour
    ...
2047 tests, 0 failed, 825s wall with 4 jobs
exit 0
```

Commands 5-9:

```
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered            (exit 0)
$ python3 tests/spec_gen.py --check
| total | 437 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing                       (exit 0)
$ uv run --frozen ruff format --check
112 files already formatted                                   (exit 0)
$ uv run --frozen ruff check
All checks passed!                                            (exit 0)
$ uv check --frozen
All checks passed!                                            (exit 0)
```

Diff against the mechanism (bug-fix step 3): I read `git diff 699837d8..HEAD` for skills/pstack-pair and skills/pstack-kitchen/scripts. Each hunk maps to one brief step: `merge_store` keeps a local file a reply lacks (step 1); `fetch_origin` is the one bounded, prompt-free origin fetch, and `origin_baseline` returns '' when it fails (step 2); `pack` cuts a send that carries keeps like a hand-over (step 3); `record_started` no longer clears `rotation_required`, `require_rotatable` lets an unready bootstrap through (`reason 'no READY'`), `clear_rotation` is gone from the exit-4 path (step 4); `recorded_start` falls back to pair.json's entry only when no kind and no roster kind exist, and a store that records none keeps the old error (step 5). No hunk lacks a matching test.

Standing check on tests: in the range's test diff the only subprocess calls are `git init` with `timeout=60` and a recording wrapper of `subprocess.run`; the `PSTACK_BUNDLE_FETCH_TIMEOUT_S` patch states its purpose in the test name; no sleep, no output mask or normalisation added.

History check (bug-fix step 5): the brief's tests ride in the same commits as their fixes (e.g. 3e3b1df3 holds the step 1 test and fix), so a failing test never lands apart from its fix; I did not restage. The report's failing-then-passing evidence is the source-stash method, and I reproduced it independently above with the parent's source.

## Flows driven

pstack (pstack-pair core, kitchen_core, docs): no separate user-facing flow beyond the CLI's own test fixtures. The tests drive `kitchen.sh spawn`, `consultant`, `rotate`, `dispatch` and the bundle pack/sync through the real scripts against the fake mux, shown in the Evidence blocks above. I did not run `kitchen.py gate pstack behavioral`, since its commands are the Brief commands run above, in full.

## Folded Acceptance

none

## Not covered

- `kitchen.py gate pstack behavioral --base ...`: not run as a wrapper; the same gate content (full suite, check-pstack, spec_gen, ruff, uv check) ran as plain commands and passed.
- A live Amp-thread rotation with a real never-READY orb: only the fake-mux test drives it.

## Lessons

none
