# Verdict 049: bundle-and-rotation-fixes-2

status: reject
units: 047 049
range: 699837d853a868ed785fa843cb167ee3e559c3bc..648f31bfbc25a86d5a5b5c5106ce4d56e60778b0
verifier: amp (claude-opus-5-5)
verifier-agent: claude --model claude-opus-5-5

## Findings

1. Unit 047 Acceptance "A late reply keeps a master-written verdict" is broken at the head (and so is unit 049's step-3 rule as briefed). Unit 047's own step-1 test, run unchanged at 648f31bf, fails for the verifier reply: the reply was packed before the master wrote a gates-mode verdict, the master then sent the role a newer bundle (the base) that holds the verdict, and the late reply deletes the verdict (`AssertionError: ... verifier reply deleted the master-written verdict`). The same test passes at unit 047's head 97366025 and fails on main 699837d8 (Evidence A).
   - The cause is in `bundle.py` `merge_store`. The new branch keeps this side's copy only when it differs from the base or the base lacks it. Brief 049 step 3 asked for a third condition: "...or the master owns the path". The report's Self-check says the `is_master_owned` branch already covers that. It does not cover `verdicts/`: gitstore.RULES gives `verdicts/[^/]+` kind ROLE with roles `['verifier', 'master']` ("gates-mode verdicts are the master's"), and `is_master_owned` needs kind MASTER. So a verifier reply falls through to `merge_file(base=local, local, None)`, which deletes the file (Evidence B).
   - Brief 049 step 3 told the sidekick to replace only 047's *sidekick* subtest, "which passes without the fix (verdicts/ is not sidekick-owned)". The diff removes the whole 047 test `test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot`, verifier subtest included (Evidence C). The report says so ("the old verifier subtest of 047 ... now removes the file ... not in the replacement test"). So Acceptance "047's tests pass except the one step 3 replaces" is also broken: the dropped verifier subtest was not the one step 3 replaces, and it fails at the head.
   - The smallest fix: in that branch, also keep this side's copy when the master co-owns the path (`'master' in rule.roles` for a ROLE rule such as `verdicts/`). Restore 047's verifier subtest beside the new reports/ test.

Every other Acceptance line of 047 and 049 holds (Evidence D-G).

## Evidence

### A. 047's step-1 test (verifier and sidekick replies), verbatim, at three heads

Probe file (kept out of the tree at /tmp/v049/probe/test_probe_late_verdict.py): a subclass of `BundleMergeTests` with 047's test body, run by path.

```
$ (at 648f31bf, unit 049 head) python3 .verify-probe/test_probe_late_verdict.py
  test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot (...) (role='verifier') ... FAIL
AssertionError: False is not true : verifier reply deleted the master-written verdict
Ran 1 test in 0.008s
FAILED (failures=1)

$ (at 97366025, unit 047 head) python3 <probe>
Ran 1 test in 0.006s
OK

$ (at 699837d8, main) python3 <probe>
Ran 1 test in 0.008s
FAILED (failures=1)
```

### B. Ownership of verdicts/ and the merge_store branch at the head

```
$ sed -n 127,133p skills/pstack-pair/scripts/pstack_core/gitstore.py
    _rule('verifier packets', r'verdicts/[^/]+-packet\.md', MASTER, ['master']),
    _rule(
        "verdicts (a verifier writes its own, gates-mode verdicts are the master's)",
        r'verdicts/[^/]+',
        ROLE,
        ['verifier', 'master'],
    ),
$ sed -n 371,376p skills/pstack-pair/scripts/pstack_core/bundle.py
def is_master_owned(rel):
    ...
    return rel not in SHARED_LOGS and rule is not None and rule.kind == gitstore.MASTER
$ (bundle.py merge_store, new branch)
        elif (
            reply is not None
            and rel not in files
            and rel in local
            and local[rel] != base_files.get(rel, MISSING)
        ):
```

### C. Tests removed and added in the range

```
$ git diff 699837d8..HEAD -- tests/ | grep -E '^-\s+def test_'
test_a_hand_over_bundle_is_cut_at_the_tracking_ref_when_origin_cannot_be_fetched
test_the_origin_fetch_of_a_hand_over_bundle_has_no_prompt_a_timeout_and_a_timed_out_one_falls_back_to_the_tracking_ref
test_an_ordinary_send_carries_only_its_own_runs_evidence_and_fetches_nothing
$ git show 3e3b1df3 -- tests/test_pstack_core.py   (047 step 1 added)
+    def test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot(
+        for role in ('verifier', 'sidekick'):
$ grep -n 'def test_a_late_reply_never_deletes' tests/test_pstack_core.py   (at head)
(no match)
```

### D. Each new test, at the head, at 047's head and on main (tests/ of the head copied into each base tree)

```
=== 648f31bf (head)
test_a_reply_deletes_a_file_its_own_role_deleted: 1 tests, 0 failed
test_a_hand_over_bundle_carries_every_commit_when_origin_cannot_be_fetched: 1 tests, 0 failed
test_the_origin_fetch_of_a_hand_over_bundle_has_no_prompt_a_timeout_and_a_timed_out_one_carries_every_commit: 1 tests, 0 failed
test_the_receivers_fallback_fetch_of_origin_has_no_prompt: 1 tests, 0 failed
test_the_receivers_unshallow_fetch_has_a_timeout: 1 tests, 0 failed
test_an_ordinary_send_carries_only_its_own_runs_evidence_and_is_cut: 1 tests, 0 failed
test_an_ordinary_send_keeps_local_only_evidence: 1 tests, 0 failed
test_only_a_hand_over_fetches_origin: 1 tests, 0 failed
test_the_rotation_flag_goes_with_the_new_generations_ready: 1 tests, 0 failed
test_spawn_on_an_orb_with_no_host_roster: 1 tests, 0 failed
test_threads_archive_archives_an_idle_role_with_no_open_work: 1 tests, 0 failed
=== 97366025 (unit 047 head)
test_a_reply_deletes_a_file_its_own_role_deleted: failed
test_the_receivers_unshallow_fetch_has_a_timeout: failed
test_only_a_hand_over_fetches_origin: failed
test_spawn_on_an_orb_with_no_host_roster: failed            (049 step 5 row)
test_threads_archive_archives_an_idle_role_with_no_open_work: failed   (049 step 1 row)
(the other six: 0 failed)
=== 699837d8 (main)
all eleven: failed
```

So each of 047 steps 1-5 has a test that fails on main and passes at the head (step 1's replacement included). Each of 049 steps 1-5 has a test that fails at 047's head and passes at 049's. 047's step-1 verifier case is the exception (finding 1).

### E. The fetch counts and the unshallow timeout (tests at the head, read)

`test_only_a_hand_over_fetches_origin_and_an_evidence_send_is_cut_at_the_tracking_ref` wraps `subprocess.run` and asserts `fetches == []` after a plain send, `fetches == []` and `baseline == self.base` after an evidence send, and `len(fetches) == 1` with `baseline == ''` after a hand-over to an unreachable origin. `test_the_receivers_unshallow_fetch_has_a_timeout_of_its_own` asserts timeouts 11, None (0) and 600 (default) for `--unshallow`, with PSTACK_BUNDLE_FETCH_TIMEOUT_S=9 set. Both pass at the head (D).

### F. Full suite (the packet's gate) and the static checks

```
$ python3 kitchen.py --repo <scratch> setup
setup: ok (1 commands, ...)
$ python3 kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 699837d8...
pstack behavioral: pass
  exit 0 677.4s python3 tests/run.py
  ... 2049 tests, 0 failed, 676s wall with 4 jobs
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check
112 files already formatted
$ uv run --frozen ruff check
All checks passed!
$ uv check --frozen
All checks passed!
```

(The first ruff run flagged one file: my probe in `.verify-probe/`. I moved the probe out of the tree, and the runs above are the clean reruns.)

### G. Lows and docs (diff at the head)

```
$ git diff 97366025..HEAD -- skills/pstack-pair/scripts/pstack_core/errors.py
-    """The master cannot hand over now: ..."""
+    """A rotation is refused before it changes anything: a master hand-over that cannot start (...), or a role's rotation that cannot (...)."""
$ git diff 97366025..HEAD -- skills/pstack-kitchen/scripts/kitchen_core/setup.py
+        unrostered = not native
         kind, native = recorded_start(store, 'sidekick', native)
-        unrostered = True
```

SKILL.md (~273-288) now says: a hand-over fetches origin (PSTACK_BUNDLE_FETCH_TIMEOUT_S, 30) and carries every commit when the fetch fails; an evidence send is cut against origin's default branch as last fetched, with no fetch; any other send is cut against the remote-tracking refs, with no fetch; and the receiver fetches origin, bounded, with PSTACK_BUNDLE_UNSHALLOW_TIMEOUT_S (600) when shallow. That matches `pack` / `fetch` in the bundle.py diff. SKILL.md also states the reply-deletion rule as the code implements it, without the master-owned exception (finding 1).

## Flows driven

- pstack (behavioral): no user-facing flow beyond the CLI. I drove `bundle.apply` with 047's late-reply case directly at three heads (Evidence A), each new test at three heads (D), and the full suite through the kitchen gate (F).
- pstack-docs: check-pstack and spec_gen --check (F), and the SKILL.md text read against `pack` (G).

## Folded Acceptance

none

## Not covered

- The scratch worktree the packet names did not exist on this orb. I created it with `git worktree add --detach <path> 648f31bf` and ran everything there. I created the 047 and main comparison trees as separate worktrees and removed them afterwards.
- bug-fix step 5 (repro before fix in history): brief 047 asked for each failing test in the same commit as its fix. I showed the failures by running the head's tests against the earlier trees (D), not by commit order.
- I did not drive a live Amp-thread rotation or spawn. The kitchen tests drive them through the fake mux.

## Lessons

none

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
