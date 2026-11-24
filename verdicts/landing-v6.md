# Verdict landing: landing-v6

status: clean
units: landing
range: 29cc53c2e8a65114086f9ec00042f5dd64d4b469..d7f3a4c99de4f15ae162744ae278faa8fe7b3cfc
verifier: amp thread, claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

## Evidence

### E1: scratch worktree at the head (git sees a work tree)

```
$ kitchen.sh scratch <store> 000-landing-verify --at d7f3a4c99de4f15ae162744ae278faa8fe7b3cfc
Preparing worktree (detached HEAD d7f3a4c9)
/home/user/.local/state/pstack/scratch/runs-kitchen-followups/000-landing-verify
$ git rev-parse HEAD; git rev-parse --is-inside-work-tree
d7f3a4c99de4f15ae162744ae278faa8fe7b3cfc
true
```

### E2: setup and the packet's behavioral gate (full suite)

```
$ python3 kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261124T182433-4200.log)
$ python3 kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 29cc53c2e8a65114086f9ec00042f5dd64d4b469
pstack behavioral: pass
  exit 0 690.8s python3 tests/run.py  log: .../logs/20261124T182434/pstack-behavioral-0.log
exit=0
$ tail -1 .../pstack-behavioral-0.log
2064 tests, 0 failed, 690s wall with 4 jobs
```

### E3: the remaining Verify gates at the head

```
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
exit=0
$ python3 tests/spec_gen.py --check
| total | 438 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
exit=0
$ uv lock --check
Resolved 3 packages in 66ms
exit=0
$ uv run --frozen ruff format --check
112 files already formatted
exit=0
$ uv run --frozen ruff check
All checks passed!
exit=0
$ uv check --frozen
warning: `uv check` is experimental ... (1 line omitted)
All checks passed!
exit=0
```

### E4: commit list: 21 commits, same subjects and order as the source range, no duplicates, no merges

```
$ git rev-list --count 29cc53c2..d7f3a4c9
count: 21
$ git log --format=%s 29cc53c2..d7f3a4c9 | sort | uniq -d
dupes: []
$ git log --reverse --format=%s 699837d85..f117480b8 > src; git log --reverse --format=%s 29cc53c2..d7f3a4c9 > dst; wc -l < src; diff src dst
src count: 21
subjects identical, same order
$ git merge-base 29cc53c2 d7f3a4c9
29cc53c2e8a65114086f9ec00042f5dd64d4b469
$ git rev-list --merges 29cc53c2..d7f3a4c9 | wc -l
0
```

### E5: both sides kept: the combination is exact outside tests/SPEC.md

Main's change (699837d85 -> 29cc53c2, units 040 and 046) compared with the head's difference from the old stack tip (f117480b -> d7f3a4c9), and the stack's change (699837d85 -> f117480b) compared with the landing's own change (29cc53c2 -> d7f3a4c9). Both use `git diff -U0` with SPEC.md, index and hunk-header lines left out.

```
$ f(){ git diff -U0 $1 $2 -- . ':!tests/SPEC.md' | grep -vE '^(index |@@)'; }
$ f f117480b d7f3a4c9 > a; f 699837d85 29cc53c2 > b; cmp a b
main side carried exactly
$ f 699837d85 f117480b > c; f 29cc53c2 d7f3a4c9 > d; cmp c d
stack side carried exactly
$ git grep -nE '^(<<<<<<<|>>>>>>>|=======)( |$)' d7f3a4c9 -- . | grep -v test_pstack_upstream
(no output)
```

### E6: before and after: the old stack is not a fast-forward onto main, and the landing head is

```
$ git merge-base --is-ancestor 29cc53c2 f117480b8
origin/main ancestor of old stack tip f117480b: exit=1
$ git merge-base --is-ancestor 29cc53c2 d7f3a4c99
origin/main ancestor of landing head d7f3a4c9: exit=0
```

### E7: seam selections from the brief's Verify

```
$ python3 tests/run.py -k bundle
31 tests, 0 failed, 13s wall with 4 jobs
$ python3 tests/run.py -k relay
13 tests, 0 failed, 10s wall with 4 jobs
$ python3 tests/run.py -k snooze
10 tests, 0 failed, 7s wall with 4 jobs
$ python3 tests/run.py -k archive
26 tests, 0 failed, 40s wall with 4 jobs
```

### E8: the amp.py seam (046 snooze code next to the stack's role_is_idle)

```
$ git diff -U0 29cc53c2 d7f3a4c9 -- skills/pstack-pair/scripts/pstack_core/mux/amp.py | grep -cE '^[-+].*(snooze|nest)'
0
$ grep -nE 'def role_is_idle|class Snoozed|def require_awake|def nest_pending' mux/amp.py
590:def role_is_idle(store, doc, role)        # reads send.took_a_brief; DamagedStore -> in use (False)
632:class Snoozed(PairError)                  # 046, unchanged
638:def require_awake(store, *threads)        # 046, unchanged
713:def nest_pending(store)                   # 046, unchanged
```

### E9: standing test checks on the range's tests (subprocess bounds, sleeps, env)

```
$ git diff -U0 29cc53c2 d7f3a4c9 -- tests/*.py | grep '^+' | grep -E 'subprocess|time\.sleep|sleep\(|time\.time|monotonic|timeout'
+        subprocess.run(['git', 'init', '-q', str(empty)], check=True, timeout=60)
(the other hits are mock.patch.object(subprocess, 'run', ...) fakes that return, and asserts on the recorded timeout; no sleep, no elapsed-time assert)
$ grep -nE 'def (git|run_sh)\(' -A8 tests/test_pstack_core.py tests/test_pstack_kitchen.py | grep -E 'def |timeout'
tests/test_pstack_core.py:20206:    def git(self, *args):        timeout=60
tests/test_pstack_core.py:21071:    def git(self, *args):        timeout=60
tests/test_pstack_kitchen.py:148:    def git(self, *args):        timeout=60
tests/test_pstack_kitchen.py:3945:    def git(self, *args):        timeout=60
tests/test_pstack_kitchen.py:3976:    def run_sh(self, *args, code=0, timeout=120):
$ git diff 29cc53c2 d7f3a4c9 -- tests/*.py | grep -E '^\+.*environ'
(only mock.patch.dict(os.environ, {...}) of XDG_STATE_HOME, PSTACK_BUNDLE_KEEP and PSTACK_BUNDLE_FETCH_TIMEOUT_S; none of the scrubbed prefixes)
```

## Flows driven

- pstack: the full behavioral suite at the head (E2) and the brief's bundle, relay, snooze and archive seams that drive pair.sh and kitchen.sh against the fake herdr and the Amp backend (E7), with the parent-against-head fast-forward check (E6) and the exact both-sides combination check (E5).
- pstack-docs: check-pstack.py resolves every skill link and frontmatter at the head (E3); the doc commits are carried over unchanged (E5). There is no other user-facing flow.

## Folded Acceptance

### unit 053-landing-rebase-6

- `git log origin/main..HEAD` holds the 21 commits by subject, plus any step 2 fixes, and no subject appears twice: held. There are 21 commits whose subjects match 699837d85..f117480b8 one for one and in order. There are no step 2 fixes, no duplicate subjects and no merges (E4). Outside SPEC.md, both sides' content is kept exactly (E5).
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head: held. The suite ran 2064 tests with 0 failed (E2). The other checks are all exit 0 (E3).

## Not covered

- The bug-fix playbook's history check (step 5: the failing repro lands before the fix) does not apply. This unit only cherry-picks commits whose order verdict 052-relay-keeps-bundle-id-v1 already judged, and E4 shows that order is unchanged.
- tests/SPEC.md was regenerated rather than carried over line for line. It is proven only by `spec_gen.py --check` (E3).
- Nothing was pushed, which is correct for `[landing].mode = commit`.

## Lessons

- L012: held. `uv check --frozen` passes at the head (E3).

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
