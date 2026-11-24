# Verdict 052: relay-keeps-bundle-id

status: clean
units: 047 049 051 052
range: 699837d853a868ed785fa843cb167ee3e559c3bc..f117480b83a23ccbceccdf5d394e552470e89391
verifier: amp thread, claude opus 5.5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes (not findings):
- The new relay row in `BundleReplySyncTests` calls `relay.settle` directly, not the CLI. I drove the real CLI path in a throwaway probe: `kitchen.sh steer` to a working sidekick, then `relay-done`, then a sidekick reply that names the relayed bundle and deletes its own report, then the master's `kitchen.sh sync`. It fails at b131308e and passes at the head (Evidence E3).
- The report's deviation holds. At b131308e only the "the master changed it too" subtest fails. The "only the role changed it" subtest already passes there, because `merge_file` with an empty base was silent (E2). Acceptance line 2 holds at the head either way.
- Units 047 to 052 put each test in the same commit as its fix, as AGENTS.md requires ("Commit a source change in the same commit as the test that pins it"). The bug-fix step-5 "repro lands before fix" rule therefore cannot apply as a separate commit; I checked each fail-before by running the head's tests against older source instead.

## Evidence

E1. The Prove block: setup and the pstack behavioral gate (full suite) at f117480b, in the scratch, with the verifier role exported.

```
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261124T174114-5002.log)
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 699837d8...
pstack behavioral: pass
  exit 0 629.5s python3 tests/run.py  log: .../logs/20261124T174115/pstack-behavioral-0.log
```

E2. Fail-before for 052's new rows. A worktree at b131308e (unit 051's head) and one at 31bf16d5 (049's old `str()` comparison), each with `git checkout f117480b -- tests/`.

```
$ (b131308e + head tests) python3 tests/run.py -k BundleReplySync
FAIL: ...[b: the master relayed the bundle the role synced]
AssertionError: True != False : merge: the reply's base 01795542192539309146-07cbe4405286 is not kept; files it lacks are kept
FAIL: test_an_unknown_base_takes_a_file_only_the_role_changed_silently_and_names_a_file_both_changed ... [the master changed it too]
AssertionError: False != True : merge: the reply's base bogus-id is not kept; files it lacks are kept
3 tests, 2 failed

$ (b131308e + head tests) python3 tests/run.py -k test_threads_archive
FAIL: ...[a sidekick with a string generation]
AssertionError: 1 != 0 : kitchen.sh: pair.json is damaged: generation is string, not an integer
1 tests, 1 failed

$ (31bf16d5 + head tests) python3 tests/run.py -k test_threads_archive
FAIL: ...[a done sidekick with no generation and dispatch generation 0]
AssertionError: 'archive: 0 archived, 0 kept, 2 in use' != 'archive: 1 archived, 0 kept, 1 in use'
1 tests, 1 failed
```

E3. A CLI drive of a relayed steer, from a throwaway probe (an `AmpKitchenTests` subclass in a scratch copy, then deleted). It runs `kitchen.sh steer` against a sidekick in a turn and then `kitchen.sh relay-done`. The sidekick packs a reply that names the relayed bundle's id as `synced` and lacks its own report. The master still has the report and runs `kitchen.sh sync <reply>`.

```
# at f117480b
relayed bundle id 01795542718951697485-002deeee94b4
relayed: 7a9090396912 to demo-sidekick (T-00000000-0000-4000-8000-000000000001)
kept by id: ['01795542717831083479-...tgz', '01795542718419812351-...tgz', '01795542718951697485-002deeee94b4.tgz']
--- master sync output
store: 0 updated, 1 removed
checkout: at 1f3d25e93, as demo-sidekick
synced: /tmp/.../runs/demo from demo-sidekick
report on master after sync: False
'is not kept' printed: False

# the same probe at b131308e
relayed bundle id 01795542726656411319-7c9213a3e7d5
kept by id: ['01795542725417065098-...tgz', '01795542726043056200-...tgz']   (relayed bundle missing)
merge: the reply's base 01795542726656411319-7c9213a3e7d5 is not kept; files it lacks are kept
store: 0 updated, 0 removed
report on master after sync: True
'is not kept' printed: True
```

E4. Brief commands at f117480b, from the scratch root.

```
$ python3 tests/run.py -k bundle
31 tests, 0 failed, 11s wall with 4 jobs
$ python3 tests/run.py -k relay
13 tests, 0 failed, 8s wall with 4 jobs
$ python3 tests/run.py -k archive
24 tests, 0 failed, 32s wall with 4 jobs
$ python3 tests/run.py -k rotat
75 tests, 0 failed, 42s wall with 4 jobs
$ python3 tests/run.py -k spawn
23 tests, 0 failed, 10s wall with 4 jobs
$ python3 tests/run.py            (see E1, exit 0)
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check
| total | 437 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check
112 files already formatted
$ uv run --frozen ruff check
All checks passed!
$ uv check --frozen
All checks passed!
```

E5. 047's late-reply test is unchanged since 051, and 052's diff touches no earlier test.

```
$ git log --format=%h -S'test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot' 699837d8..f117480b -- tests/
31bf16d5
89fd0096
3e3b1df3
$ git diff b131308e f117480b -- tests/ | grep -c late_reply_never
0
$ git diff b131308e f117480b -- tests/test_pstack_core.py tests/test_pstack_kitchen.py
(only additions: relay_to_role helper, one row in the reply table, a new unknown-base test, two archive rows and their fixtures; no removed or edited assertion)
```

E6. The code diff b131308e..f117480b, read against the brief's steps.

```
mux/amp.py: new keep_bundle_by_id(thread, path), copy by id + prune newest PSTACK_BUNDLE_KEEP with contextlib.suppress(FileNotFoundError);
            keep_sent_bundle calls it (step 1, 3)
commands/relay.py settle: amp.keep_bundle_by_id(thread, record['bundle']) before os.remove, whether or not a newer send holds sent_path (step 1)
bundle.py apply(path, base, reply, sent=None): unknown base -> base_files = read_bundle(sent)[1] if sent exists else {} (step 2; `based` kept, still gates deletions)
commands/sync.py: bundle.apply(path, base, reply, amp.sent_path(sender)) (step 2)
mux/amp.py role_is_idle: try took_a_brief(doc) except DamagedStore: return False (step 4)
SKILL.md: reply-merge sentence names RULES, "synced, sent or relayed", relay-done keeping, unknown base (step 5)
```

## Flows driven

- pstack: the CLI relay round trip (`kitchen.sh steer`, `relay-done`, a role reply, then the master's `kitchen.sh sync`) under `PSTACK_MUX=amp` against the repo's fake amp (E3). The `threads --archive` sweep ran through the archive table's CLI rows (E2, E4).
- pstack-docs: the SKILL.md sentence read against the code (E6). `check-pstack` and `spec_gen --check` pass (E4).

## Folded Acceptance

Unit 047:
- Steps 1-5 each have a test that fails on main and passes at the head: held at the head, since every 047 test passes (E1, E4). The fail-on-main half was shown in 047's report and in verdict 051-v1, and I did not rerun it (see Not covered).
- A late reply keeps a master-written verdict; a bundle packed with origin down applies without origin; an ordinary send keeps local-only evidence; a bootstrap without READY can be rotated again and spawn after close dispatches after READY; spawn with no roster starts the recorded entry: held (rows a of the reply table, -k bundle, -k rotat and -k spawn all pass; E1, E4, E5).
- Full suite, check-pstack, spec_gen --check: held (E1, E4).

Unit 049:
- After a rotation with READY and no dispatch, the idle sweep keeps the sidekick: held (archive row "a fresh generation not yet dispatched to"; -k archive passes; E4).
- Ordinary and evidence sends make no fetch, a hand-over makes one, and local-only evidence survives: held (-k bundle passes; E4).
- A reply removes its own role's deleted file and keeps a later master file: held (rows a and b; E3, E4).
- The unshallow fallback has its own timeout; RotateRefused and the setup.py lows: held (-k bundle and -k rotat pass; E4).
- SKILL.md matches pack; 047's tests pass except the one step 3 replaces; check-pstack and spec_gen: held (E1, E4).

Unit 051:
- Rows (a)-(d) hold: held at the head (E4). The fail at 648f31bf/97366025 is shown in verdict 051-v1 and was not rerun here.
- A reply with no or an unknown `synced` id deletes nothing and says so: held (rows c pass; E4, E2 output shows the line).
- 047's late-reply verifier subtest is back unchanged and passes: held (E5, E1).
- 047's and 049's other tests, check-pstack, spec_gen: held (E1, E4).

Unit 052 (the unit verified):
- After a relayed steer, the reply's own deletion reaches the master and no `is not kept` line prints; the new row fails at b131308e: held (E2, E3).
- With an unknown base, an own-role file only the role changed comes through with no conflict line, and nothing is deleted: held (new unknown-base test and rows c pass; E4).
- Archive rows of step 4 hold; generation-0 fails with the old str() comparison: held (E2, E4).
- 047's, 049's and 051's tests pass unchanged; check-pstack and spec_gen: held (E1, E4, E5).

## Not covered

- The fail-on-main and fail-at-648f31bf/97366025 history for units 047, 049 and 051 was not rerun in this pass. Verdict 051-v1 proved it at b131308e, and the code from b131308e to f117480b only adds the 052 changes, with no earlier test edited (E5).
- Pruning under a real concurrent removal (step 3) is not exercised. The brief asked for no concurrency test, and I read the `suppress(FileNotFoundError)` in the code (E6).
- No real Amp threads or orbs were used. The relay path ran against the repo's fake amp.

Standing test check: the new tests carry no masks. `sync_reply` runs in process. The archive rows go through `run_sh`, which has a 120 s timeout. Every subprocess is bounded.

## Lessons

L012: held (`uv check --frozen` passes; E4)
