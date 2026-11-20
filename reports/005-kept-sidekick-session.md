# Report 005: kept-sidekick-session

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/005-kept-sidekick-session.md
playbook: bug-fix
branch: kitchen-followups
head: b1ca49f1426ba03ccb493f8b1756e9edd4959b7b
tree: clean

## Changed

Four commits on c5cf250, one per plan step, each recorded with kitchen.sh step (steps 1-4, gates pass pstack,pstack-docs):

```
b5893cb fix(skills/pstack-pair): a role's reply never changes what the master owns on a local-store sync
d4a195e fix(skills/pstack-pair): a new sidekick generation on a backend that does not wait starts with rotation_required cleared
0f1913e feat(skills/pstack-pair): every sidekick rotation under a session-reusing mux says why
b1ca49f docs(skills/pstack-kitchen): say a role's reply leaves the master's state alone and every rotation says why
 10 files changed, 950 insertions(+), 97 deletions(-)
 bundle.py +161, rotate.py +84, sync.py, mux/amp.py, mux/base.py, spawn.py (src); SKILL.md; tests/test_pstack_core.py, test_pstack_kitchen.py, SPEC.md
```

Root cause: a role's reply was merged (bundle.py, three-way) against the bundle the master sent that role *last*, which can be newer than the snapshot the role worked from, so the role's older copy of anything the master changed in between won: pair.json keys (.dispatch, .consult, ...) rolled back, and master files were reverted (standing-orders.md) or deleted (a new brief).

Fix (step 1): `bundle.role_reply(manifest, recorded)` classifies the transfer from the receiver's own pre-merge store (commands/sync.py passes it to `bundle.apply(path, sent, reply)`). It is a reply when the receiver has a pair.json, the bundle is addressed to the master's name (or the previous master's), and the sender is neither the master nor the previous master (name, and thread where recorded). Under a reply: pair.json is kept whole except the sidekick's completion (A1/A5); master-owned paths (gitstore.classify kind MASTER, imported inside the function) keep the receiver's bytes, never updated or deleted; only `events.tsv` and `gates.tsv` (SHARED_LOGS) take the union, and an absent shared log is a no-op; every other path (reports, verdicts, advice, steps, progress, unclassified) merges as before. The sync line names each master path it kept. A first sync (no local pair.json) and a master-to-master hand-over merge as always.

## Ran

```
$ python3 tests/run.py -k BundleMerge          -> 2 tests, 0 failed
$ python3 tests/run.py -k keeps_the_sidekick   -> 2 tests, 0 failed
$ python3 tests/run.py -k rotat                -> 65 tests, 0 failed
$ python3 tests/run.py -k take_master          -> 10 tests, 0 failed
$ python3 tests/spec_gen.py --check            -> ok: tests/SPEC.md is current, 0 missing
$ python3 tests/run.py                          (full suite, before step 1 commit) -> 1955 tests, 0 failed
$ uv run --frozen ruff format --check / ruff check / uv check --frozen -> all pass at HEAD
```

Failing-then-passing regression (AmpKitchenTests.test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone, the consultant's two-orb probe ported into the test class), run with the test in place and bundle.py unfixed (c5cf250 source):

```
AssertionError: {'bri[58 chars]fs/001-unit001.md', ...} != {'bri[58 chars]fs/002-unit002.md', ...} : dispatch
```
(the delayed verifier reply rolled `.dispatch` back from 002 to 001), and after the fix: `1 tests, 0 failed`. The test asserts `.dispatch`, `.consult`, a master key added after the snapshot, the sidekick generation, an edited standing-orders.md and a brief created after the snapshot all survive; the old verdict is delivered; and docs unit 003 prints `keeps its session`, with no `rotated:`. `test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation` is the probe's kept control, passing. The probe's other two controls were dropped (covered by test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread).

Step 2 regressions fail without the one-line change (`AssertionError: True is not False` for rotation_required after `rotate`, and after a failover that followed a done unit) and pass with it: test_a_dispatch_after_rotate_and_the_new_threads_ready_is_sent, test_a_dispatch_after_a_failover_that_followed_a_done_unit_is_sent_once_ready.

Step 3: test_every_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event (table: profiles, context N% >= 50%, compacted, context unreadable, previous brief missing, risk escalated/routine), test_a_failed_over_sidekick_rotates_with_that_reason, test_a_kept_session_prints_no_rotate_line; core: RotationReasonTests. Herdr/Rex print no `rotate:` line; goldens unchanged (`git diff --stat` shows no tests/pstack-pair/golden change; test_pair/guided/trio pass).

New merge tests, RoleReplyMergeTests (tests/test_pstack_core.py): late reply with a newer base keeps every master-owned pair.json key and file (names the refused paths); no-base reply; a master file the master removed stays removed; shared logs union and absent-log no-op; decisions.tsv and sessions/*.tsv get no rows or deletions from a reply (A3); completion accepted only as `true` from the sidekick, refused for older sidekick generation, older dispatch generation, another brief, a verifier's copy (A1), an empty sender, another thread, and missing generation/brief on either side (A5); false/absent flag leaves the master's; message-to-role and first-sync unchanged; hand-over rows (A4). Kitchen: test_the_old_masters_bundle_is_a_hand_over_at_the_first_sync_and_while_it_is_pending uses a real rotate-master bundle (`demo-master` -> `demo-master-g2`) at first sync and with the pending hand-over in place.

## Self-check

figures: every count above is from the commands in Ran
acceptance: late-reply regression -> AmpKitchenTests late-reply test (fails on c5cf250 source, passes at head); decisions.tsv/sessions/ unchanged, events.tsv/gates.tsv rows delivered, absent log deletes nothing -> RoleReplyMergeTests shared-log and master-owned-log rows; rotation_required only as true from the current sidekick with matching generation/brief/dispatch generation -> completion refusal table; real rotate-master bundle is master-to-master -> test_the_old_masters_bundle_is_a_hand_over...; rotate then READY then dispatch sent, same after failover -> the two step-2 tests; `rotate: <reason>` and event detail under Amp, none under Herdr, goldens unchanged -> step-3 tests plus PairScriptTests passing
callers: changed shared symbols: `bundle.apply` (new optional `reply`; callers: commands/sync.py only, tests), `bundle.merge_store` (apply only), `rotate.rotate` (new optional `reason`; callers rotate_if_due, cli), `rotate.keeps_session` (new optional `reasons`; callers rotate_if_due and the core mock at test_pstack_core.py, signature-compatible), `Mux.compacted` (new, base False, Amp override), `spawn.record_started` (every spawn). grep for each done
fresh: the gates ran in-checkout detached with fresh logs; no cache involved
gate: ruff format --check, ruff check, uv check --frozen and check-pstack.py pass at HEAD; the step gates (pstack, pstack-docs) passed for each of the four commits

Every role write to pair.json found under the local store (grep of update()/update_role() over pstack_core and kitchen_core): only `commands/report.py` `record_rotation_required` (`sidekick.rotation_required = true`, from `finish`, for the current dispatch and generation). The rest are master commands (dispatch, consult, steer, checkin, scratch, verify_*, playbook_delivery, rotate, spawn, close, nest, init, master hand-over); `record_skill_build` and `record_scratch` write from a role under the local store, and a role's `skill_builds` row is now dropped on a reply (telemetry, the master stamps its own). Other role writes to the store are files: reports/, steps/, progress/, advice/, verdicts/, and appends to events.tsv and gates.tsv (kitchen step.py), which are the shared logs kept as unions. No role writes decisions.tsv, nest-pending.tsv or sessions/ that I found.

## Deviations

- Design 4 narrowed (steer s1 accepted it): `spawn.record_started` clears `rotation_required` only where the spawn does not wait for READY (`not mux.get().waits`: Amp threads, subagents). Plan 021 said to clear it always; that failed the three Herdr goldens (test_pair, test_guided, test_trio) and test_old_ready_cannot_satisfy_failed_bootstrap, which require a failed bootstrap to keep rotation_required. Where the spawn waits, READY (`mark_ready`) still clears it.
- Steer s1 (kind info, finish b1ca49f and the report): applied; b1ca49f recorded as step 4 and this report written.
- The brief's "before done run review.self": kitchen.toml sets no `[review].self`, so no review skills were named; joo is the master's review engine and is not run here.
- The sync summary line gained `; kept the master's copy of: ...` and `; took the sidekick's completion` suffixes (stated in the plan's Design 2 as "names the protected paths"; the completion suffix is extra).

## Questions

none

## Follow-ups

- Gates-mode verdicts (`verdicts/*` is ROLE in RULES but the master writes them) and unclassified files such as host-roster.toml keep the stale-base hazard (as the review noted).
- The exact consumed-base merge (option 2) remains a separate design.
- A role's `skill_builds` stamp under the local store is now ignored on a reply; the master's own commands stamp it.
