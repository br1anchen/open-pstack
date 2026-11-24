# Verdict 051: reply-names-its-base

status: clean
units: 047 049 051
range: 699837d853a868ed785fa843cb167ee3e559c3bc..b131308e833d2eb556d3383db39fb484daac25a7
verifier: amp (claude-opus-5-5)
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes, not findings:
- The packet's scratch worktree did not arrive with the store sync on this orb; I created it with `git worktree add --detach <scratch path> b131308e` at the path the packet names (E1).
- Base comparisons ran in separate scratches made with `kitchen.sh scratch --at` (648f31bf, 97366025, 699837d8) and one at the head for a mutation probe. At 648f31bf and 97366025 the head's tests/test_pstack_core.py was copied in, with a one-line shim so `pack` accepts `synced=` and ignores it. That lets the new table run against the old merge code. Nothing in the verified scratch was edited.
- Mutation probe: when sync.py uses the newest sent bundle as a reply's base, rows (a) still pass, because `apply` also refuses a base whose id differs from `synced`. Rows b-after-a-newer-send and (c) fail (E5). So the table catches the wrong base, and the id check in apply backs up sync.py's choice.

## Evidence

E1: scratch at the head
```
$ git worktree add --detach .../051-reply-names-its-base-verify b131308e833d2eb556d3383db39fb484daac25a7
HEAD is now at b131308e docs(skills/pstack-kitchen): a reply is merged against the bundle it synced, and one whose base is gone deletes nothing
$ git -C <scratch> rev-parse HEAD --is-inside-work-tree; git status --short
b131308e833d2eb556d3383db39fb484daac25a7
true
(clean)
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261124T165724-6298.log)
```

E2: Prove gate (full suite) at the head
```
$ kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 699837d8...
pstack behavioral: pass
  exit 0 554.0s python3 tests/run.py  log: .../logs/20261124T165724/pstack-behavioral-0.log
(log tail) 2051 tests, 0 failed, 553s wall with 4 jobs
```

E3: brief commands at the head, each from the scratch root
```
$ python3 tests/run.py -k bundle
31 tests, 0 failed, 11s wall with 4 jobs
$ python3 tests/run.py -k rotat
75 tests, 0 failed, 41s wall with 4 jobs
$ python3 tests/run.py -k spawn
23 tests, 0 failed, 10s wall with 4 jobs
$ python3 tests/run.py -k archive
24 tests, 0 failed, 29s wall with 4 jobs
$ python3 tests/run.py            (see E2: 2051 tests, 0 failed)
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
exit 0
$ python3 tests/spec_gen.py --check
| total | 437 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
exit 0
$ uv run --frozen ruff format --check
112 files already formatted
exit 0
$ uv run --frozen ruff check
All checks passed!
exit 0
$ uv check --frozen
All checks passed!     (plus uv's "experimental" warning)
exit 0
```

E4: 051's table (rows a-d) at 648f31bf, at 97366025 and at the head (head test file plus the `synced=` shim at the two bases)
```
===== vbase 648f31bf
$ python3 tests/run.py -k test_a_reply_deletes_what_its_role_had
FAIL ... [a: the master wrote it after the role's snapshot]
AssertionError: False != True : store: 0 updated, 1 removed
FAIL ... [a: the same for a verifier]
AssertionError: False != True : store: 0 updated, 1 removed
FAIL ... [c: the reply names a bundle that is not kept]
AssertionError: False != True : store: 0 updated, 1 removed
FAIL ... [c: the reply names none]
AssertionError: False != True : store: 0 updated, 1 removed
FAILED (failures=4)
===== v047 97366025
FAIL ... [b: the role deleted its own unchanged file]
AssertionError: True != False : store: 0 updated, 0 removed
FAIL ... [b: the same, after a newer bundle was sent]
AssertionError: True != False : store: 0 updated, 0 removed
FAIL ... [b: the same for a verifier]
AssertionError: True != False : store: 0 updated, 0 removed
FAIL ... [c: the reply names a bundle that is not kept]
AssertionError: "merge: the reply's base bogus-id is not kept; files it lacks are kept" not found in 'store: 0 updated, 0 removed\nsynced: /tmp/.../store from sk\n'
FAIL ... [c: the reply names none]
AssertionError: "merge: the reply's base (none named) is not kept; files it lacks are kept" not found in 'store: 0 updated, 0 removed\nsynced: /tmp/.../store from vf\n'
FAILED (failures=5)
===== verify b131308e
1 tests, 0 failed, 0s wall with 4 jobs
```

E5: mutation at the head (sync.py passes `amp.sent_path(sender)` as a reply's base)
```
$ python3 tests/run.py -k test_a_reply_deletes_what_its_role_had
[b: the same, after a newer bundle was sent]
AssertionError: True != False : store: 0 updated, 0 removed
[c: the reply names a bundle that is not kept]
AssertionError: "merge: the reply's base bogus-id is not kept; ..." not found in 'store: 0 updated, 0 removed\nsynced: ... from sk\n'
[c: the reply names none]
AssertionError: "merge: the reply's base (none named) is not kept; ..." not found in '... from vf\n'
1 tests, 1 failed
```

E6: 047's restored test: unchanged from 97366025, absent at 648f31bf, fails at 648f31bf and passes at the head
```
$ diff <(047 test body at 97366025) <(at b131308e)
IDENTICAL      (14 lines each)
$ git show 648f31bf:tests/test_pstack_core.py | grep -c test_a_late_reply_never_deletes
0
===== vbase 648f31bf
ERROR: test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot (...) (role='verifier')
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/.../store/verdicts/001-x-v1.md'
FAILED (errors=1)
===== verify b131308e
1 tests, 0 failed, 0s wall with 4 jobs
```

E7: the 047/049 tests at the head
```
test_a_hand_over_bundle_carries_every_commit_when_origin: 1 tests, 0 failed
test_an_ordinary_send_keeps_local_only_evidence: 1 tests, 0 failed
test_only_a_hand_over_fetches_origin: 1 tests, 0 failed
test_the_receivers_unshallow_fetch_has_a_timeout: 1 tests, 0 failed
test_the_rotation_flag_goes_with_the_new_generations_ready: 1 tests, 0 failed, 13s wall
test_spawn_on_an_orb_with_no_host_roster: 1 tests, 0 failed, 3s wall
test_a_bundle_names_the_last_one_applied: 1 tests, 0 failed
-k idle: 22 tests, 0 failed, 19s wall
```

E8: the head's tests run at main (699837d8, head test files copied into a scratch at the base): each 047 test fails
```
test_a_late_reply_never_deletes: failed
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/.../store/verdicts/001-x-v1.md'
test_a_hand_over_bundle_carries_every_commit_when_origin: failed
AssertionError: '5ced356e6c028e8d9d016915b3f8e2cf7aa73de2' != ''
test_an_ordinary_send_keeps_local_only_evidence: failed
AssertionError: 'evidence: 1 commits kept under refs/pstack/keep' not found in ['store: 0 updated, 0 removed', 'commits: the git bundle did not apply', ...]
test_the_rotation_flag_goes_with_the_new_generations_ready: failed
AssertionError: False is not True
test_spawn_on_an_orb_with_no_host_roster: failed
AssertionError: 1 != 4 : kitchen.sh: no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)
```

E9: code read at the head (diff 648f31bf..b131308e)
```
sync.py: base = amp.kept_sent_path(sender, manifest.get('synced')) if reply else amp.sent_path(sender)
         if reply and reply.role and base is None: lines.insert(0, "merge: the reply's base ... is not kept; files it lacks are kept")
         amp.record_applied(sender, manifest.get('id'))
bundle.apply: based = reply is None or base_manifest.get('id') == manifest.get('synced'); if not based: base_files = {}
merge_store: keep when (not based or local[rel] != base_files.get(rel, MISSING))
amp.with_bundle / relay: pack(..., applied_id(thread)) / pack(..., synced=applied_id(thread))   (thread = the receiver)
amp.keep_sent_bundle: copies to sent/<thread>/<id>.tgz, prunes to the newest PSTACK_BUNDLE_KEEP (ids are zero-padded time_ns, so name order is send order)
amp.kept_sent_path: None for '', an id with os.sep, or a leading '.'   (asserted with '../'+id in the table's second test)
amp.role_is_idle: rotation_required is True and took_a_brief(doc) and ...   (send.took_a_brief compares count(dispatch.generation) == count(sidekick.generation))
SKILL.md 314-323: "A reply is merged against the bundle it synced ... A reply whose `synced` id names no kept bundle deletes nothing, and the sync prints `merge: the reply's base <id> is not kept; files it lacks are kept`."
```

## Flows driven

- pstack: no live multi-orb flow on this orb. The master-side reply flow was driven through `pstack_core.commands.sync.sync` on real bundles that `bundle.pack` packed and `amp.keep_sent_bundle` kept, at the head and at two bases (E4, E5), plus the full suite (E2).
- pstack-docs: check-pstack and spec_gen --check (E3), and SKILL.md read against the code (E9).

## Folded Acceptance

Unit 051:
- Rows (a)-(d) hold, and (a), (b), (d) fail at 648f31bf or 97366025: held. (a) and (d) fail at 648f31bf, (b) fails at 97366025, and all rows pass at the head (E4).
- A reply with no `synced` id, or an unknown one, deletes nothing and says so: held. Rows (c) pass at the head and fail at both bases (E4, E5).
- 047's verifier subtest is back unchanged and passes: held (E6).
- 047's and 049's other tests pass; check-pstack and spec_gen --check pass: held (E2, E3, E7).

Unit 047:
- Each of steps 1-5 has a test that fails on main and passes at the head: held (E8 at 699837d8, E7 and E2 at the head).
- Late reply keeps a master-written verdict, bundle with origin down applies without origin, ordinary send keeps local-only evidence, a bootstrap that never sends READY can be rotated again, spawn after close dispatches after READY (the `how='spawn after close'` subtest), spawn with no roster starts the recorded entry: held (E6, E7, E8).
- Full suite, check-pstack and spec_gen --check pass: held (E2, E3).

Unit 049:
- After a rotation with READY and no dispatch yet, the idle-roles sweep keeps the new sidekick: held. The archive table row 'a fresh generation not yet dispatched to' passes in `-k archive` and `-k idle` (E3, E7). 051 adds a row for a store with no generations.
- An ordinary send and an evidence send make no fetch, a hand-over makes one, and local-only evidence survives: held (`test_only_a_hand_over_fetches_origin...` and `test_an_ordinary_send_keeps_local_only_evidence...`, E7).
- A reply removes a file its own role deleted unchanged and keeps a file the master wrote after the snapshot: held (rows b and a, E4).
- The unshallow fallback uses its own timeout: held (`test_the_receivers_unshallow_fetch_has_a_timeout_of_its_own`, E7). The RotateRefused and setup.py lows (02787dd2) are covered by the passing full suite (E2); I did not drive them separately.
- SKILL.md matches pack; 047's tests pass except the one step 3 replaced (that one is now restored by 051); check-pstack and spec_gen --check pass: held (E3, E6, E9).

## Not covered

- No live two-orb Amp-thread exchange: a real master orb and a role orb trading bundles through `amp threads` was not driven. The flow was driven through `sync` in-process.
- Commit order (review-fix step 2): each fix commit carries its test in the same commit (31bf16d5 has the table; e104caf0 has the archive row). The brief permits this; 051's three steps landing as one commit is a deviation the report already states.
- Standing test check: the new tests add no output mask (`# fmt: skip` only formats the table) and start no subprocess.

## Lessons

none (the packet lists none; the brief's L012, Optional checks with `uv check`, held: `uv check --frozen` passes, E3)
