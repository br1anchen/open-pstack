# Plan 008: kept-sidekick-session

round: 1
supersedes: none
contract: 2
store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
risk: escalated (bundle merge semantics of pair.json on role-to-master sync)

## Goal

A delayed role reply can no longer roll the master's scheduling state in pair.json backwards, so a routine unit after a routine unit in the same profiles keeps the Amp sidekick's thread; and every rotation decision says why, so a rotation in a real run can be attributed. Proven by the consultant's reproduction (advice/005-kept-sidekick-session-c1-probe.py, last case) turned into a regression test that keeps the session.

## Design

Data: `pair.json` holds master-owned scheduling state (`.dispatch` and the `.sidekick` identity fields: `name`, `generation`, `pane_id`, `kind`, `start_args`, `chain`, `chain_index`, `bootstrap_pending`) beside role-owned facts (a sidekick's done report sets `.sidekick.rotation_required`, through `commands/report.py` L206-247, only for the current dispatch and generation).

Today `bundle.py` merges pair.json key by key against the base `commands/sync.py` L135-137 picks: the latest bundle the master sent that peer (`mux/amp.py` L78-80, L292-296, L1189-1190). When the master sent the peer a newer bundle after the snapshot the peer actually consumed, `local == base` holds for keys the master has not changed since, and the peer's older values win (`bundle.py` L185-202); with no base, `merge_file` takes the sender's whole pair.json (L220-227).

Live evidence from this run (decisions.tsv `incident`): the consultant's 005 advice reached the master after the master had sent consult 004. The sync merged it against the 004 bundle, rolled `pair.json` `.consult` back from 004 to 005, reverted `standing-orders.md` (two lines the master appended after the 005 consult) and deleted `briefs/007-git-store-default.md`, which the master created after it. So the rollback is not limited to pair.json keys: every master-owned file the role did not change can be reverted or deleted.

The fix therefore applies the ownership split to files as well as keys: on a role-to-master sync, a path the master owns is never changed or removed by the incoming bundle. Reuse the git store's ownership table, `RULES` in `skills/pstack-pair/scripts/pstack_core/gitstore.py`, rather than writing a second list: it already says which store paths belong to the master (`pair.json`, `standing-orders.md`, `briefs/`, `plans/`, `steers/`, `reviews/`, `consults/` and the rest) and which to a role. pair.json itself is master-owned in that table, but a role legitimately sets a few keys in it (the sidekick's `rotation_required`, ready/bootstrap settlement, and whatever else the sidekick finds); those keys are the only ones taken from a role's pair.json, under the generation and brief check below. List every role write to pair.json in the report.

Change, in one place: the pair.json merge in `bundle.py` takes a set of master-owned key paths that are kept from the receiver when the receiver is the master and the sender is a role (the roles are known: sync knows the sender's name from the bundle manifest and the receiver from the local registry, `commands/sync.py` L52-72). Master-owned paths: `dispatch`, `verifying`, `sidekick.name`, `sidekick.generation`, `sidekick.pane_id`, `sidekick.kind`, `sidekick.start_args`, `sidekick.chain`, `sidekick.chain_index`, `sidekick.bootstrap_pending`, `master`, `anchor`. `sidekick.rotation_required` from a sidekick is accepted only when the sender's `sidekick.generation` equals the receiver's and the sender's `dispatch.brief` equals the receiver's; from any other role it is kept from the receiver. A master-to-role sync and a master-to-master hand-over (`take-master`) are unchanged. Put the path set in one constant beside the merge (as gitstore's `RULES` owns the git store's split), not spread over callers.

Diagnostics: `keeps_session` (`commands/rotate.py` L153-170) returns a reason for the rotation as well as for keeping: `previous brief missing`, `risk <prev>/<next>`, `profiles <a> vs <b>`, `context unreadable`, `context N% >= limit`, `compacted`. `rotate_if_due` prints `rotate: <reason>` before the replacement starts and records it in the `rotate` event's detail. The fail-closed behaviour (unreadable context rotates) stays.

## Alternatives and trade-offs

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| Master-owned keys stay authoritative on role-to-master pair.json merges | Small, local to the merge; matches the git store's ownership split; fixes the reproduced rollback | Not a general fix for other files with a stale base | yes |
| Exact acknowledged merge base per reply (manifest names the snapshot consumed, retained base snapshots) | General fix for every file | Larger: retained snapshots, manifest field, old-client handling | no |
| Clear `rotation_required` on sync, or raise the context threshold | Trivial | Suppresses the policy, leaves the rollback | no |
| Diagnostic only | Smallest | Leaves a reproduced defect open | no (diagnostic is folded into the chosen option) |

The ownership fix removes the demonstrated failure at its root (the merge accepts stale master state) without touching the reuse policy, and the rotation reason makes the next real incident attributable whatever its cause.

## Steps

1. Master-owned scheduling keys survive a role's reply.
   playbook: bug-fix
   - Turn the consultant's probe (`advice/005-kept-sidekick-session-c1-probe.py`) into a regression test in `tests/test_pstack_kitchen.py` `AmpKitchenTests`, beside `test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread` (about L15949): the newer-outbound/older-verifier-reply sequence, expected to keep the docs sidekick for 003 and to deliver the old verdict. Keep the probe's three control cases as rows of one table-driven test only if they are not already covered by existing tests; say which in the report. The test fails at a90bb41.
   - Add a second regression case: a delayed role reply after a newer outbound bundle neither reverts a master-owned file the master edited (standing-orders.md) nor deletes one it created (a brief). Then implement the master-owned paths and keys in `skills/pstack-pair/scripts/pstack_core/bundle.py` (the pair.json merge) with the receiver/sender roles passed from `commands/sync.py`. Unit-level rows in `tests/test_pstack_core.py` `BundleMergeTests` (about L20643): a stale role value for each master-owned path is kept from the receiver; a sidekick's `rotation_required` for the current generation and brief is accepted; one for an older generation is not; a master-to-role merge still takes the master's values.
   - Commit each with its test; check `python3 tests/run.py -k keeps_the_sidekick` and `-k BundleMerge`.
2. Every rotation decision says why.
   playbook: bug-fix
   - `keeps_session` returns (kept, reason); `rotate_if_due` prints `rotate: <reason>` and puts it in the `rotate` event detail. One table-driven test over the reasons, extending `test_half_a_context_window_rotates_the_sidekick_even_in_the_same_area` (about L16281) and `test_a_compacted_sidekick_rotates_even_in_the_same_area` (L16288). Goldens change only if a golden transcript covers a rotation; regenerate only those, with `PSTACK_PAIR_UPDATE=1`, and name them in the report.

## Verification

The regression test fails on a90bb41 and passes at the head; the full suite passes; a real kept dispatch in this run (units 002 then 003 are routine, same profiles) prints `kept:` or a `rotate: <reason>` that names a model switch the master made on purpose.

## Risks

- A master-owned path that a role legitimately writes would stop reaching the master. The sidekick's report sets `rotation_required` (handled); check whether a sidekick or verifier writes any other listed path (`verifying` is written by the master's `verify`, cleared by `verify --wait` on the master). The sidekick should list every writer it finds of each path.
- Old builds on role orbs send bundles without a role name in the manifest: treat an unknown sender as a role when the receiver is the master.
- Under the git store this merge is not used; the git store has its own ownership table. No change there.

## Open questions

- Does the bundle manifest already carry the sender's role, or only its agent name? If only the name, how does the master map it to a role (registry)?
- Does any role legitimately write a listed path (see Risks)?

## Disagreements resolved

none

## Master note (after unit 002)

A second cause seen live: `kitchen.sh rotate` (pstack_core/commands/rotate.py `rotate`) leaves `.sidekick.rotation_required` true after starting the new generation (generations 2 and 3 of this run, both right after rotate). `require_fresh_session` (send.py about L87-104) then refuses the next dispatch with FreshSessionRequired even after READY, because it settles a pending bootstrap only when rotation is not required. The master cleared it by hand (decisions.tsv `incident`). Step 1 adds a regression row: after `rotate` and READY, `dispatch` of the next brief is sent; and the fix clears rotation_required when a new generation starts.
