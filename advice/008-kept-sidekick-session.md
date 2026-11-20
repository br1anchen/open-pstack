# Advice 008: kept-sidekick-session (plan response)

status: object
plan: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/008-kept-sidekick-session.md
head: c5cf25045919b5390bebab589b40d39e79a036c0 (read-only source already on the run's code branch)
round: 1
scratch: 008-plan-review, removed before writing advice

## Position

Keep the ownership-based fix, rotation diagnostics, and new-generation flag reset. Revise the pair.json rule before implementation. The plan says that pair.json belongs to the master except for named role facts, but implements the opposite rule: merge every field except a protected list. That list omits `.consult`, the exact field that rolled back in this run. Preserve the master's entire document and apply only validated role facts. This is a smaller contract and remains correct when the master adds fields.

## Design critique

The file-level guard is correct. Use `gitstore.RULES` to keep master-owned files on a role-to-master sync, before generic merge, deletion, and TSV-union handling. Protect pair.json even with a missing base or an incoming deletion. Handle its allowed role facts separately. Ordinary master-to-role delivery and master handoff should retain their existing behavior.

The pair.json protected list is incomplete by construction. Besides `.consult`, it omits `.pending`, `.sent`, `.sidekick.delivered`, consultant state, and future master fields. At c5cf250, `consult.py:380-389` writes `.consult`, `send.py:65-85` writes pending and sent state, and `kitchen_core/playbook_delivery.py:219-221` writes delivered brief sections. None should return from an older role snapshot. A constant containing allowed role updates is easier to audit than a list of every master-owned key.

For the known sidekick completion fact, accept an incoming boolean `true` only from the sidekick generation and dispatch it describes. Require present, matching generation and brief values, including a dispatch generation matching that sidekick generation. Resolve sender identity against the receiver's pre-merge state, not the incoming registry or incoming pair.json. An unknown sender can deliver its advice or report, but must not supply a sidekick completion fact. Preserve the receiver's flag on incoming `false`, absence, or an invalid match. The sidekick's `record_rotation_required` writes only true; clearing is a master decision. A matching brief and generation alone do not make an old false value current.

Keep READY as the role's output file. The master's `spawn.report_live`, `store.settle_ready`, and `send.require_fresh_session` settle bootstrap state from that file. Do not automatically grant a role permission to replace `.bootstrap_pending` because the plan mentions "ready/bootstrap settlement." Inventory actual role write paths first. `record_skill_build` is another local role write today, but a stale copy of the entire build history is not a role-owned replacement for the master's list. If retaining that telemetry is required, define a separate append-only update rather than allowing whole-document merge.

The master note identifies a separate valid fault. `spawn.record_started:209-227` advances the generation without clearing the previous local `rotation_required`. `send.require_fresh_session:87-104` then refuses to settle READY while that flag remains true. Clear the sidekick's generation-local flag in the successful new-generation state update, before bootstrap delivery. This should also cover failover, not only the `rotate` command. A failed start that never records a new generation must not clear it. Keep `bootstrap_pending` true until READY arrives. Under git, `store.rotation_required:586-611` derives completion from the report and dispatch generation, so it is not necessary to copy the local flag model into git.

Rotation diagnostics need one additional boundary decision. `Amp.context_used:708-720` converts any compaction to the synthetic value 1.0. The caller cannot distinguish that from genuine 100% usage. Preserve that API's current behavior for existing callers, but expose enough context information for the diagnostic to say `compacted` rather than misreporting usage. Also cover branches that bypass `keeps_session`: failback from a fallback model and a plan rather than a brief. Keep their current rotation policy. Record the reason with the generation being replaced, not only with the replacement generation after spawn.

## Trade-offs

| Option | Assessment |
| --- | --- |
| Preserve pair.json, apply allowed role facts | Recommended. Matches the stated ownership model, fixes `.consult` without another field list, and protects future master fields. Requires an explicit decision for each legitimate role fact. |
| Merge everything except the listed master fields | Reject as written. Leaves the motivating `.consult` rollback unfixed and requires maintenance whenever the master gains state. |
| Exact consumed-snapshot merge bases | Still broader than this unit. Worth a separate design if arbitrary jointly edited files require lossless merging. Ownership guards do not claim to solve that general problem. |
| Clear the flag on every sync or relax the reuse threshold | Still reject. Suppresses legitimate completion and rotation policy rather than repairing ownership. |

Update the alternatives table's claim that the chosen option does not fix other files. The plan now includes the file ownership guard, so that description no longer matches the design.

## Objections

1. **Design, protected pair.json paths.** The list omits `.consult` despite the motivating incident. Preserve local pair.json wholesale and apply a small explicit list of role facts instead of adding more protected keys.
2. **Design, sidekick.rotation_required acceptance.** Matching generation and brief permits stale false values unless the rule constrains the value too. Accept only a validated positive completion fact. Missing fields must not match each other accidentally, and an unrecognized sender must not get the sidekick exception.

## Risks

- Classify the transfer before `bundle.apply` changes files and before `sync.learn` changes the registry. The current manifest has `from_name`, `from`, `to`, and `agents`, not a trusted role field. Initial role bootstrap and master-to-master handoff may have an empty receiving registry. Preserve those paths with explicit control cases; do not treat every unknown incoming transfer as a role reply.
- `gitstore.py` imports `bundle.py`. Reusing RULES through an unconditional reverse module-level import risks a cycle. A local import at the ownership check is sufficient unless the implementation finds a better existing boundary.
- Preserve delivered reports, verdicts, and role event rows while ignoring their copies of master files. Do not broaden this unit into enforcing all role-to-role file ownership without evidence and a separate contract.
- Two delayed replies for the same brief can carry different completion values. Include the same-generation stale-false case, as well as the old-generation case already planned.
- The run is now git-backed. A successful kept dispatch in this run checks the policy and diagnostics, but cannot prove the bundle merge fix. The fake-Amp two-orb regression remains the required proof. Units 002 and 003 already ran before this implementation, so the plan's live example needs a later representative transition rather than a historical claim.

## Answers

The manifest carries names and thread IDs, not a role. `bundle.pack:130-154` writes `from_name`, `from`, and `to`; `Amp.sender` returns a name and thread. Determine the receiver and known sender from local state before applying the bundle. The incoming agent map is synchronization data, not the authority to classify its own transfer. When the receiver is established as master, an unrecognized sender can take the conservative role-reply path without receiving any pair.json exception.

The concrete role-owned pair.json update found is local sidekick completion in `commands/report.py:235-251`. READY settlement is performed by the master command paths noted above. Kitchen startup can append skill-build telemetry under the local backend. The sidekick should still complete the writer inventory, as requested by the plan; this review does not claim an exhaustive caller audit.

## Evidence

I read the plan at store commit 44ce60b091fa260bd2e8a35297d2bda27ea09d76, which includes the announced commit 7325a7f2746453f2d2c05d46f1fd1cea67543068. Generation 1 and pane amp:2 match the consultant record. I read committed source at c5cf250, not the shared checkout's older a90bb41 tree. No product files changed and no full suite ran.

In a scratch worktree at c5cf250 I called the actual `bundle.merge_json` on an older incoming document with `local == base`, then restored exactly the protected paths listed in the plan. Assertions confirmed that `.consult`, `.pending`, `.sent`, and `.sidekick.delivered` still rolled back. A second control constructed matching brief and generation data with local true and incoming false. It showed why identity equality alone cannot authorize replacing the completion flag. Output was:

```text
PLAN_DENYLIST: consult, pending, sent, sidekick.delivered still roll back
FLAG_CONTROL: same brief and generation admit stale false unless updates are positive-only
```

These are bounded checks of the proposed merge rule, not a prototype of the full replacement design. Extend the existing end-to-end regression with `.consult` and a newly introduced master field to distinguish the recommended rule from another incomplete protected list. Add controls for missing bases, unknown senders, current positive completion, stale false, stale generation, master-to-role bootstrap, and master handoff.

Two bootstrap issues occurred before this review. Branch sync could not attach over this orb's existing local store directory. I preserved that directory at `.amp/in/orb-backup/kitchen-followups-pre-git-14ee938f4f06`, then attached the authoritative branch at the requested path. No old snapshot was merged back into it. The supplied build e6400ac119341ef7 also lacks the role guard that c5cf250 adds to `record_skill_build`, so its `kitchen.sh scratch` failed on master-owned pair.json before creating a worktree. I used its core `pair.sh scratch`, which checks the same scratch command without the kitchen startup hook, for creation and removal. The master should refresh its executable build before sending further git-role work. For advice delivery, this orb's repository `kitchen.sh` uses the already-recorded skills path and avoids that obsolete startup write; no role override or master identity substitution is needed.
