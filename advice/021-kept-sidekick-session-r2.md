# Advice 021: kept-sidekick-session-r2 (plan response)

status: object
plan: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/021-kept-sidekick-session-r2.md
head: c5cf25045919b5390bebab589b40d39e79a036c0 (read-only source already published on the run's code branch)
round: 2
scratch: 021-plan-review, removed before writing advice

## Position

The revision resolves both round-1 objections. Keeping master pair.json whole and accepting only positive, validated sidekick completion is the right contract. Keep the generation reset and diagnostics design. Two bounded corrections remain before implementation: replace the blanket TSV exception with explicit append-log handling, and recognize actual master handoffs rather than assuming their sender and recipient names match. Both findings came from executing the existing code in scratch at c5cf250.

## Design critique

Design 2 protects only non-TSV master paths and says TSV files retain their union merge. That union exists only when incoming content is present. `bundle.merge_file:205-232` falls through to the generic deletion path when the incoming file is absent. With the master's latest outbound copy as the base, `local == base` then deletes the local file. The extension exception therefore permits deletion of `decisions.tsv`, a master-owned TSV under `sessions/`, and even the legacy `events.tsv` log.

Some local logs genuinely require union semantics. `kitchen_core/step.py:327-340` appends sidekick gate results to `gates.tsv`, and local role events share `events.tsv`. Preserve these known shared logs explicitly rather than treating the TSV extension as permission to edit master state. Under protection, an omitted shared log means no new rows, not deletion. For master-owned paths without a demonstrated shared writer, preserve the receiver's bytes regardless of extension. The same explicit shared-log list should govern the documentation exception. The proposed docs statement that role replies never change master files currently contradicts the blanket TSV exemption.

Design 1's handoff assumption does not match the wire format. `kitchen_core/master.py:499-515` creates `demo-master-g2` and sends it a bootstrap before `Amp.hand_over` changes the old master's registry entry. The actual bundle has `from_name=demo-master` and `to.name=demo-master-g2`. A genuinely empty receiver avoids protection because it has no local pair.json. After the first sync, however, its pending master is `demo-master-g2`; the proposed name predicate classifies another transfer from the old master as a role reply. That changes the documented master-to-master behavior. A test using identical master names would miss it.

Retain classification before apply and learn, but use the local pending-handoff identity, including the previous master, to distinguish that master transfer. Where thread IDs are available, match the local recorded identity as well as the name. Do not infer the exception from the incoming registry. Keep a first bootstrap with no local store unprotected, and keep unknown senders conservative when the receiver is established as master. Use an actual emitted handoff bundle as the control, both for first sync and for a receiver already holding its pending handoff.

The remaining design is suitable. READY remains a file, skill-build telemetry does not replace the master history, and a successful new-generation update clears the old local flag without clearing bootstrap_pending. The shared decision function should be evaluated once per rotation decision so the printed reason and the action use the same observation. Separate wrappers can preserve existing callers without making rotate_if_due query changing context twice.

## Trade-offs

| Choice | Recommendation |
| --- | --- |
| Exempt every TSV from ownership protection | Reject. Extension does not establish a shared writer, and the current merge can delete an omitted TSV. |
| Protect master paths, with explicit append-only local-log exceptions | Choose. Preserves required role event and gate delivery without admitting arbitrary master TSV changes. Missing incoming logs keep local bytes. |
| Treat unequal master names as a role reply | Reject as the handoff rule. Real handoffs use generation-specific recipient names. |
| Recognize a pending master transfer from pre-merge local identity | Choose. Handles the existing naming scheme without changing the wire format or trusting the incoming registry. |

## Objections

1. **Design 2, TSV exemption.** Restrict union behavior to known shared append logs and make absence a no-op for them. Apply RULES protection to other master-owned TSV paths. Test `decisions.tsv` and a new TSV under `sessions/`, with absent incoming content and a newer outbound base, plus shared-log row delivery and absence controls.
2. **Design 1, same-name handoff assumption.** Replace it with recognition of the locally recorded pending master transfer. Test the unequal names from a real rotate-master bundle, including a receiver that already synced the pending handoff. Unknown role replies must remain protected.

## Risks

- Keep the sidekick completion exception narrower than general transfer recognition. A known consultant or previous master is not the current sidekick. Missing generation or brief values must not match accidentally.
- Design 3 makes the incoming dispatch generation optional. The current finish path sets completion only when dispatch generation matches sidekick generation. Prefer requiring it for the exception unless an actual supported legacy fixture proves that it is absent. At minimum, a recorded mismatch on either side must refuse the exception. This is a boundary clarification, not a request for another merge mechanism.
- Shared append logs retain their pre-existing row-union limitations. This unit need not introduce a general conflict protocol for them.
- This is a design review with bounded probes, not evidence that the unimplemented replacement passes the full suite.

## Answers

There are no open questions in the revised plan. Both round-1 objections are resolved. Neither new correction requires changing the reuse policy, adding a wire-format version, or abandoning the ownership approach.

## Evidence

I installed sender build a68465145cf5c4dd, synced the branch, and read the plan and standing orders at store commit 3bc9d6a1d806d92b7a04496338aa8ffb09c0b8f4. That history contains the announced 3edd4e4d8860894e536b51668b5559ed36b90733. The consultant remains generation 1, pane amp:2. The updated build's kitchen scratch command worked without the previous role-stamping failure.

In `kitchen.sh scratch ... 021-plan-review --at c5cf250`, I ran a Python probe against the actual `merge_file` and `gitstore.classify`. For each listed master TSV, it passed identical current/base bytes and absent incoming bytes. Each result was None, which `merge_store` interprets as deletion. It also confirmed that present incoming decisions rows are appended. Then the existing `AmpKitchenTests` fixture ran `filled_handoff` and `rotate-master` against fake Amp. I read its real attached bundle and evaluated the plan's classification predicate against the pair.json that the first sync installs. Assertions passed and printed:

```text
TSV_MISSING: decisions.tsv deleted under round-2 extension exception
TSV_MISSING: sessions/master-inventory.tsv deleted under round-2 extension exception
TSV_MISSING: events.tsv deleted under round-2 extension exception
TSV_PRESENT: decisions.tsv imports incoming master-owned row
HANDOFF: from=demo-master to=demo-master-g2 repeated_pending_handoff_protect=True
```

The final line proves misclassification under the stated predicate, not data loss from replaying an identical bundle. The TSV cases prove the deletion behavior of the merge that the plan would leave enabled. All fixture data stayed in scratch, which was removed before this advice. No product source changed, no live roles were started, and no shared-checkout tests ran.
