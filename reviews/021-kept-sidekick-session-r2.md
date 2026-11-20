# Review 021: kept-sidekick-session-r2

verdict: agreed
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/021-kept-sidekick-session-r2.md
head: c5cf25045919b5390bebab589b40d39e79a036c0

## Amendments

A1. (sidekick) The pair.json completion exception applies only when the sender is the current sidekick: manifest `from_name` equals the receiver's pre-merge `.sidekick.name` (and its thread, where recorded). A verifier's or consultant's stale `true` is ignored.
A2. (sidekick) Compaction is read through a separate mux accessor `compacted(name)` (base: False); `context_used` keeps its return. The shared decision function reads context once per rotation decision, so the printed reason and the action use the same observation.
A3. (consultant 1) No blanket `.tsv` exemption. Only the known shared append logs (`events.tsv`, `gates.tsv`, and any other the sidekick finds a role appending to under the local store, listed in the report) keep the union, and under protect an absent incoming shared log is a no-op, never a deletion. Every other master-owned path, `.tsv` or not (`decisions.tsv`, `nest-pending.tsv`, anything under `sessions/`), keeps the receiver's bytes. The docs state the same list.
A4. (consultant 2) A master hand-over is recognised from the receiver's pre-merge local identity, not by equal names: a transfer whose `from_name` (and thread, where recorded) is the receiver's recorded previous or pending master (`.master.previous` / the pending hand-over record) is master-to-master and unprotected. A receiver with no local pair.json is unprotected. Any other sender to an established master is a role reply. Tests use a real rotate-master bundle (`from_name=<slug>-master`, `to.name=<slug>-master-g2`), both at first sync and with the pending hand-over already synced.
A5. (consultant risk) The completion exception requires the incoming dispatch generation to be present and equal to the sidekick generation on both sides; any missing or mismatched value refuses it.

## Checked

- Sidekick response reports/021: agree, two refinements (A1, A2).
- Consultant advice/021: object, two bounded corrections from scratch execution at c5cf250 (A3: merge_file deletes an omitted TSV when local == base; A4: real hand-over bundles carry generation-specific recipient names). Adopted without a new round; neither changes the steps' shape.

## Blocking

none

## Follow-ups

- Gates-mode verdicts (master-written, ROLE in RULES) and unclassified host-roster.toml keep the stale-base hazard.
- Exact consumed-base merging (option 2) as a separate design.

## Next

Brief 005 implements plan 021 with A1-A5.
