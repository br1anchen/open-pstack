# Report 021: kept-sidekick-session-r2 (plan response)

status: agree
plan: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/021-kept-sidekick-session-r2.md
head: c5cf25045919b5390bebab589b40d39e79a036c0

## Position

Agree: every step is executable as written and its check would prove it. Round 1's three objections are all adopted. I add two refinements to Design 3 and 5 under Risks (the sender of an accepted `rotation_required` should be the sidekick; the compaction signal needs a small `Amp` accessor), neither of which changes the steps.

## Grounding

Read at c5cf250 (checkout is branch kitchen-followups), read-only:

- `bundle.py` `merge_file` L173-200 and `merge_store` L225-250: the `.tsv` union branch runs before the `incoming == base` / `local == base` checks, so protect has to be applied before the generic merge, as Design 2 says. `apply` L362-375 is the one place the base is read; `protect` is a new keyword there.
- `commands/sync.py` `sync` L125-168: `bundle.apply` runs before `learn(manifest)`, so the receiver's registry and pair.json are still pre-merge when the classification is made (Design 1). `learn` only touches the registry.
- `init.py` L233: `.master.name` is set at init and stays the master's name across generations; `to.name` in a manifest is the recipient's registry name (`bundle.pack`). So equality `.master.name == to.name` identifies "the receiver is the master" with no registry lookup, and a master-to-master hand-over (same name) is not a role reply.
- `gitstore.classify`/`RULES` L78-125: MASTER covers pair.json, standing-orders.md, the master's directories, packets. `verdicts/*` (non-packet) and `reports/`, `steps/`, `progress/`, `advice/` are ROLE, so they merge as today. `scratch/` is LOCAL (links travel today and keep doing so).
- Only role write to pair.json under the local store is `report.py` L241 (`sidekick.rotation_required = True`, from `finish`, current dispatch and generation only). `skill_builds` is the one other role-side write: `record_skill_build` (kitchen_core/setup.py L48-70) from a role's command; under protect it is simply not taken from the sender (fine; telemetry).
- `record_started` (spawn.py L212-227) is the single place a new generation is recorded for rotate, failover (start_chain) and rotate_if_due; setting `rotation_required = False` there is right, and under git (`rotation_required` derived from the report and `rotation_cleared`) the extra key is inert.
- `Amp.context_used` (mux/amp.py L708-720) returns 1.0 when compacted, so today `keeps_session` cannot tell compaction from 100% use; the only caller is `keeps_session` (rotate.py L181) and the Herdr/Rex base (mux/base.py L151).
- `keeps_session` is mocked with a string at test_pstack_core.py L21974, consistent with the plan keeping its return type.

## Trade-offs

Agree with the chosen option. Whole-document keep plus an allowlist cannot go stale as master keys are added, which is exactly the class `.consult` belonged to. Option 2 (exact consumed base) stays a follow-up. A small cost of the allowlist: any future role-written pair.json key must be added to the constant; the sync summary naming refused paths makes a missed one visible.

## Objections

none

## Alternatives

none

## Risks

- Accepted `rotation_required` should also require that the sender is the sidekick (`from_name` equals the receiver's `.sidekick.name`), not only that generation and brief match. A verifier's or consultant's copy of pair.json is the master's snapshot: if it was taken after the sidekick's completion (flag true, same generation and brief) and replies late, matching generation and brief alone would accept its stale `true`. The window is narrow (clear_rotation precedes record_dispatch), but the sender check is one condition and closes it. I will add a `BundleMergeTests` row for it (a verifier's stale true with matching generation and brief is ignored) and keep the plan's "unknown sender gets no exception" row.
- Compaction signal: I will add a separate accessor on the mux (`compacted(name)` beside `context_used`, base returns False) rather than change `context_used`'s return, as Design 5 asks; `rotation_reason` uses both.
- Unprotected role-owned paths that the master also writes: gates-mode verdicts (`verdicts/NNN-*.md` are ROLE in RULES, written by the master in gates mode) keep the stale-base hazard, like the unclassified `host-roster.toml`. Not a regression; I will list both under Follow-ups.
- The role's `skill_builds` row is dropped on a role-to-master sync; the master's own commands stamp it.
- This checkout's pstack build is a68465145cf5c4dd; the git-store role-stamp fix (507870a) is in it, so `kitchen.sh notify` should work without my scratch wrapper now.

## Answers

none (the plan has no open questions)
