# Report 008: kept-sidekick-session (plan response)

status: object
plan: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/008-kept-sidekick-session.md
head: c5cf25045919b5390bebab589b40d39e79a036c0

## Position

The cause is right (bundle.py merges a role's reply against the latest bundle the master sent, not the one the role consumed) and the ownership approach is the right size. Two things in it do not survive the code: a key denylist for pair.json still lets the stale reply roll back master state (`.consult`, `.sent`, `.heads`, `.pending`, ...), so pair.json should take an allowlist from a role instead; and `keeps_session` is mocked with a string return in an existing core test, so changing its return type needs that test updated. Everything else is executable as written.

## Grounding

Read at c5cf250 (checked out as branch kitchen-followups), read-only:

- `pstack_core/bundle.py` `merge_json` L152-170, `merge_file` L173-200, `merge_store` L225-250, `apply` L362-375. Stale-reply mechanism confirmed: the reply was built from snapshot A, the base is the later bundle B. For a key the master changed A->B and not since, `local == base` and `incoming != base`, so `merge_json`/`merge_file` return `incoming` (the A value). A file the master created after A is absent from `incoming` and present in `local`/`base`, so `merge_file` returns `(incoming=None)` and `merge_store` unlinks it (L237-241). That is the live standing-orders.md revert and the briefs/007 deletion. Without a base (L220-227) the sender's whole pair.json wins.
- `commands/sync.py` L135-137: base is `amp.sent_path(manifest['from'])`, one file per peer, `mux/amp.py` L78-80, overwritten by `record_sent` on every send. The manifest has `from_name` (bundle.py `pack`, L129) and `to.name`, so sender and receiver are both known at apply time; the master's name is `pair.json` `.master.name`.
- Every non-master writer of pair.json (grep of `update(`/`update_role(` over pstack_core and kitchen_core): only `commands/report.py` L241 (`sidekick.rotation_required`, from `finish`). The other `update` callers are master commands (dispatch, consult L380, steer, checkin L490, scratch (role skips it only under git), verify_*, playbook_delivery, rotate, spawn, close, nest, init). `kitchen_core/step.py` and `worker.py` write no pair.json key.
- `gitstore.RULES` L78-125 and `classify` L142: kind MASTER covers pair.json, standing-orders.md, briefs/plans/consults/steers/reviews/compositions/sessions/notes/answers/kitchens/threads, the packets, and also `events.tsv`, `gates.tsv`, `decisions.tsv`, `nest-pending.tsv`, which a sidekick's `step`/`finish` append to under the local store. `merge_file` handles `.tsv` first (union), so protecting MASTER paths must skip `.tsv`.
- `gitstore.py` L18 imports `bundle`; bundle.py cannot import gitstore at module level (cycle). `store.py` already imports it lazily inside functions.
- `commands/rotate.py` `keeps_session` L153-170, `rotate_if_due` L318-370, `rotate` L66-110 (the `rotate` event row, detail `generation:N`, L103-108). `store.py` `rotation_required` L586, `clear_rotation` L611-624, `mark_ready` L637. `spawn.py` `record_started` L212-227 sets `bootstrap_pending=True` and bumps the generation but never touches `rotation_required`. `send.py` `require_fresh_session` L87-104 settles a pending bootstrap only when `not rotation_required`. This confirms the master note: after a bare `kitchen.sh rotate` under Amp (spawn returns 4, nothing clears the flag) a READY-ed new generation is still refused. Only `rotate_if_due`'s `code == 4 and not waits` branch (rotate.py L359-363) clears it.
- Probe `advice/005-...-c1-probe.py`: the failing case (`test_newer_outbound_exposes_stale_base`) uses helpers defined on the probe class (`bundle_for`, `save_store`, `load_store`, `role`, `ready_remote`, `remote_unit`, ~60 lines), not on `AmpKitchenTests`. The failing sender there is a verifier, so its stale `dispatch` and `sidekick` come back to the master.
- Existing tests: `test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread` (test_pstack_kitchen.py L16048), `test_half_a_context_window_rotates...` L16380, `test_a_compacted_sidekick_rotates...` L16387, `BundleMergeTests` test_pstack_core.py L20643. `test_pstack_core.py` L21974 mocks `keeps_session` with `return_value='pstack, context 10%'`. Goldens contain `rotated: the devin sidekick ...` (pair/guided/trio.txt), Herdr runs.

## Trade-offs

- Option 1 (master-owned keys authoritative): right idea, but as a denylist it is incomplete. The live incident rolled `pair.json` `.consult` back and `.consult` is not in the plan's list; `.sent`, `.heads`, `.pending`, `.announced_notes`, `.sidekick_models`, `.sidekick_threads`, `.escalations`, `.skill_builds`, `.verifier`, `.sidekick.delivered`, `.sidekick.session` and anything added later are master-written too. A role writes exactly one pair.json key. So an allowlist (take from a role's pair.json only `sidekick.rotation_required`, under the generation and brief check) is smaller and cannot go stale as keys are added.
- The same inversion for files: a role-to-master merge keeps the master's copy of every non-`.tsv` path that `gitstore.classify` says is MASTER, and neither updates nor deletes it. A path `classify` returns None for keeps today's merge. The one live write to check: no role writes into a MASTER directory under local (I found none), but run a grep for `notes/` and `verdicts/` gates-mode writes when implementing.
- Option 2 (exact consumed base per reply): fixes every file by construction, including role-owned ones and unclassified ones, and needs no ownership table. Cost: retained snapshots (`sent/<tag>/<id>.tgz`), a `base` id in the manifest the role fills from the last bundle it synced, and a fallback for old role orbs. It is larger and changes the wire format; I agree with deferring it. Option 1 covers the demonstrated failure and the git store has its own table anyway.
- Options 3 and 4: agree with the plan, no.
- Pick: option 1 with the allowlist and the file rule, with the rotation-reason diagnostic.
- Missed by the table: `rotation_required` stale re-set. A delayed done report from generation N reaching the master after a rotation would set the flag on N+1. The generation check already in the plan handles it; the allowlist test should include it.

## Objections

1. Step 1 / Design, pair.json key list: a denylist leaves `.consult` (rolled back in the live incident), `.sent`, `.heads`, `.pending` and others open. Evidence: grounding, pair.json writers; plan's list vs the live rollback.
2. Step 2: `keeps_session` returns a str that `test_pstack_core.py` L21974 mocks as a string; and the `rotate` event is written in `rotate()` (L103-108), not in `rotate_if_due`, so the reason cannot reach "the `rotate` event's detail" without a parameter.
3. Step 2, printing `rotate: <reason>` unconditionally: a Herdr/Rex run (`mux.reuses_sessions` False, the goldens) rotates every time by design; a reason there is noise and changes three goldens.

## Alternatives

1. pair.json from a role: `merge_json` is not used for role-to-master at all; take local, then set `sidekick.rotation_required` from incoming only when incoming `sidekick.generation` == local's and incoming `dispatch.brief` == local's. The constant (allowed paths) sits beside the merge in bundle.py; MASTER-path file protection uses `gitstore.classify` via a function-level import. `apply(path, base, protect=False)`; sync.py passes `protect` = receiver is the master (`to.name` == local `.master.name`) and sender is not (`from_name` != that name, or empty, from an old build). Master-to-master (take-master) has the same master name, so it is unchanged.
2. Keep `keeps_session` returning the kept string for the call sites and mocks, and add `rotation_reason(store, brief)` that returns '' (nothing to say) when the mux does not reuse sessions, else the reason (`previous brief missing`, `risk <prev>/<next>`, `profiles <a> vs <b>`, `context unreadable`, `context N% >= limit`, `compacted`, `failed over`). Both share one decision function so they cannot disagree. `rotate_if_due` prints `rotate: <reason>` before the rotation and passes it to `rotate(ctx, args, reason='')` for the event detail (`generation:N reason:<reason>`).
3. See alternative 2: silent where sessions are not reused, so the goldens stay unchanged; I expect no golden to regenerate and will say so in the report.
4. Master note (rotation_required left true after `rotate`): one line in `spawn.record_started`: `entry['rotation_required'] = False` beside `bootstrap_pending = True`, since a new generation is what the flag asks for. Regression row: `rotate` then READY then `dispatch` of the next brief is sent. Under git it is a harmless extra key (rotation_required derives).

## Risks

- Old role builds send no `from_name`: treated as a role when the receiver is the master; a master-to-master hand-over by an old build then also reads as a role and keeps the new master's pair.json, which is what take-master wants since it syncs first. Test one such row.
- File protection hides a legitimate role edit to a MASTER path; none found, and the first live one would show as a conflict-free silent keep. The merge line (`store: N updated, M removed`) should name the protected paths it refused, so it is visible.
- Unclassified files (`host-roster.toml`) keep the stale-base hazard; say so in the report's Follow-ups.
- The probe helpers (~60 lines) must move into the test class to make the regression test; the three control cases pass today, so keep only `test_two_orbs_keep_after_rotation` (it guards the accepted `rotation_required` path) and drop the other two controls.

## Answers

- Does the manifest carry the sender's role? Only names: `from_name` and `to.name` (bundle.py pack L129-134). The master is identified by `pair.json` `.master.name` in the receiver's own store; no registry lookup is needed.
- Does any role write a listed path? Of the plan's list none, except `sidekick.rotation_required` (report.py L241, `finish`); `verifying` is master-only (verify_launch/verify_state, master side). The sidekick's `.sidekick` identity fields are written by master's spawn/rotate/failover. Other role writes to the store are files: reports, steps/, progress/, advice/, verdicts/, and appends to `events.tsv`/`gates.tsv` (`.tsv` union, so not protected).
