# Report 025: reply-deletes-no-master-state

status: done
brief: briefs/025-reply-deletes-no-master-state.md
playbook: review-fix
branch: kitchen-followups
head: 6a83f500bc5d5ed07276c49035859a9b8322e879
tree: clean

## Changed

Branch `kitchen-followups` is unit 028's head 7a0a233 (fetched from `kitchen-code/kitchen-followups/unit-028`, not rebased); five commits on top, each recorded with `kitchen.sh step` (the SPEC regeneration commit 0aa28ad is part of step 1's record).

- 274890b + 0aa28ad (step 1, review 005 blocking 1; chenchess issue 3) `bundle.py`: `Reply` now carries the sender's `role` (`role_reply` looks the name up in pair.json `.sidekick`, `.consultant`, `.verifier`; an unnamed sender is `''`; `from_sidekick` stays as a property). New `owned_by(rel, role)`: a path is owned when `gitstore.classify` gives it to `ROLE` kind with the role in `rule.roles`, except a `.seen` marker (the master's, though it lives in `progress/`). `merge_store` under a reply: master-owned paths unchanged (kept, never added); shared logs unchanged (union); pair.json unchanged; every other path the sender's role does not own keeps this side's bytes, is added when absent here, and is never deleted by omission; the sync line names a kept file whose bytes differed. The sender's own paths merge three-way as before. No change to RULES or the wire format.
- 274890b (step 1) the two-orb regression `test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone` now writes `steps/002-unit002.tsv` and asserts `reports/002-unit002.md` and it survive the stale verifier reply byte for byte.
- 18b77bd (step 2, review 005 blocking 2) four `RoleReplyMergeTests` rows: a sidekick reply built before the master's `progress/025-x.md.seen` and `steers/025-x-s2.md` keeps both while its own new progress lines and report arrive; a verifier's stale copy neither rolls back the sidekick's report nor deletes its step log or progress, while its own verdict arrives; a sidekick's own report and step-log update merge; an unknown sender's new report is added, its omission of `reports/001-a.md` deletes nothing, and its `briefs/009-new.md` is not added.
- 1f5722a (step 3, review 005 follow-ups) `mux/base.py` `context(name)` returns `(used, compacted)` (default `(context_used(name), False)`); `mux/amp.py` `context` makes one `amp threads context` call, `context_used` returns `context(name)[0]` (same 0-to-1 value, 1.0 once compacted, None when unreadable), `compacted()` is removed (its only caller was `session_decision`). `session_decision` reads `m.context(name)` once. `rotate()` defaults `reason=None`: the command itself records `reason:manual` and prints `rotate: manual`, and `rotate_if_due` still passes its own reason (including `''`). `keeps_session(store, brief, reasons)` drops the `reasons=None` branch. Tests: `AmpContextTests`, an updated `RotationReasonTests` decision test (context called once per decision, compacted reason), and `test_a_manual_rotate_says_it_is_manual_in_a_line_and_in_the_rotate_event`.
- 6a83f50 (step 4) SKILL.md: the reply paragraph says a reply changes only its sender's role's files, adds new ones, never deletes by omission, logs union; the rotation paragraph says every rotation, manual included, says why, and the context is read once. tests/SPEC.md regenerated (`spec_gen.py --check`: ok, 0 missing).

Role-side deletions of store paths (grep `unlink|remove|rmtree` in pstack-pair and kitchen scripts): `commands/report.py:149` removes only the role's own events log when a failed publish is rolled back; `kitchen_core/step.py` `remove_path` and `mux/amp.py:1289` remove local job dirs and a relay bundle; `gitstore.py`, `amp_routing.py` and `commands/relay.py` remove files the same writer owns or local-only files. No role deletes another path of the store; the only deletion by a reply was merge omission, which is closed.

## Ran

Fail without the fix (bundle.py from HEAD~ restored in place, new tests kept):

```
$ two-orb regression, old merge_store -> FileNotFoundError reports/002-unit002.md (1 failed); with the fix: 1 tests, 0 failed
$ python3 tests/run.py -k RoleReply (old merge_store) -> 17 tests, 3 failed (marker+steer, verifier stale copy, unknown sender); with the fix: 17 tests, 0 failed
```

After step 3 (the brief's Verify commands, also rerun by finish):

```
$ python3 tests/run.py -k BundleMerge       -> 2 tests, 0 failed
$ python3 tests/run.py -k keeps_the_sidekick -> 2 tests, 0 failed
$ python3 tests/run.py -k rotat             -> 71 tests, 0 failed
$ python3 tests/run.py -k pstack_pair       -> 132 tests, 0 failed (goldens unchanged: tests/pstack-pair/golden not touched)
$ python3 tests/spec_gen.py --check         -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format / ruff check / uv check --frozen -> clean
```

Steps 1 to 4 each recorded (gates pass: pstack, pstack-docs; step 2 pstack).

## Deviations

- Manual rotate reason: the brief says `kitchen.sh rotate` records `reason:manual` and prints `rotate: manual`. The pair goldens (`tests/pstack-pair/golden/guided.txt`, lines 2122 and 2162: `master rotate - generation:N`) record a manual rotate under Herdr with no reason and the Acceptance line says goldens unchanged, so the reason is recorded and printed only where `mux.reuses_sessions` is true (Amp threads), the same rule `rotation_reason` uses for every other reason; SKILL.md says "Herdr and Rex rotate every time and print nothing". Say if you want it under every mux; that needs a golden regeneration, which the brief forbids.
- A master-owned file the master lacks is not added from a reply (it was never added before either); only non-master, non-sender-owned files are added when absent. The brief's "an existing local file keeps the master's bytes" is met for them too.
- The kitchen.sh of the installed build 6aaa6770 refuses `finish` and `notify` here under the git store (it stamps pair.json); I run kitchen.sh from this checkout's skills/.

## Questions

none

## Self-check

figures: every result above is from a command run in this checkout
Acceptance: stale role reply neither deletes nor rolls back another role's report or step log, a master steer or a .seen marker (four new rows plus the two-orb regression, shown failing on the old merge); the two-orb regression asserts reports/002 and steps/002; a routine Amp dispatch reads `amp threads context` once per decision (AmpContextTests, RotationReasonTests call counts), a manual rotate event carries reason:manual under Amp; unit 005's tests and the goldens pass at this head
callers of changed symbols: `Reply.from_sidekick` (kept as a property), `compacted` (only caller session_decision, removed), `context_used` (kitchen_core/master.py:65 unchanged), `keeps_session` (rotate_if_due, tests), `rotate` (cli.py command table, rotate_if_due)
no push, no PR, no sleeps or timing asserts added; no files outside Scope written
notes: none open
review skills: the kitchen names none under review.self (only the joo engine, which the master runs); I read the full diff against AGENTS.md style and testing guidelines
