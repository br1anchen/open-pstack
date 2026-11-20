# Advice 005-c1: Kept sidekick sessions

status: answered
consult: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/consults/005-kept-sidekick-session-c1.md
kind: design
head: a90bb4130393b864176dbeded9bb216fcb5f255c
tree: clean
scratch: /home/user/workspace/repo/.amp/in/orb-backup/consult-scratch/runs-kitchen-followups/005-kept-sidekick-session-c1 (removed before sending)

## Answer

Brief 005 can fix a reproduced stale-reply bug, but the original run's cause remains unproven. Ordinary two-orb finish/sync preserves reuse. A delayed verifier reply rolls the master's dispatch and sidekick metadata backward when a newer message to that verifier has replaced the merge base. I reproduced a same-profile routine dispatch rotating at 10% context through that sequence. Scope the fix to ownership of scheduling metadata on role-to-master sync, and retain the reuse policy. Also record why a dispatch rotates so the original incident can be distinguished from legitimate context-based rotation.

The Fix Root Causes principle changed the recommendation. `rotation_required: true` is normal after every done report, not itself the fault. Clearing that flag during sync would suppress the policy rather than repair the stale state.

## Grounding

### Executed reproduction

The companion `advice/005-kept-sidekick-session-c1-probe.py` subclasses `AmpKitchenTests`. It swaps independent master, sidekick and verifier store copies into the same absolute path and gives every role its own XDG state directory. This preserves the real orb path contract while isolating each registry and sent-bundle cache. Every transfer uses real `sync`, `finish` or `notify` commands and the fake Amp's actual attached tarballs. It does not mock `keeps_session` or the merge. The disposable Git repository is shared by these simulated orbs; Git history transfer is not the variable under test.

Run from a scratch checkout of the head above, with PROBE_ROOT pointing to that scratch checkout:

```sh
GIT_CONFIG_NOSYSTEM=1 PROBE_ROOT="$scratch" python3 "$store/advice/005-kept-sidekick-session-c1-probe.py"
```

`GIT_CONFIG_NOSYSTEM=1` avoids this orb's system signing configuration in the disposable fixture repositories. No host signing configuration was changed.

Four probe cases completed. The counterexample case asserts the current broken result, so `OK` means the reproduction was confirmed, not that the implementation is correct.

| Case | Observed result |
| --- | --- |
| Independent stores, READY, done 001, finish bundle, dispatch 002 and 003 | Both later routine app units kept the same thread at 10% context. |
| Rotate app to docs, new orb READY, finish docs 002, dispatch docs 003 | 003 kept the new sidekick thread at 10%. |
| Verifier snapshots 001, master finishes 002, delayed verifier sends its verdict | Sync preserved current dispatch and sidekick fields; 003 kept the session. |
| Same delay, but master sends the verifier a newer message before syncing its older reply | Sync rolled dispatch from docs 002 to app 001. Dispatch of docs 003 rotated instead of keeping the thread. |

Decisive output at a90bb41:

```text
Ran 4 tests in 33.067s
OK
DELAYED VERIFIER preserved current dispatch and sidekick
NEWER OUTBOUND overwrote merge base; delayed verifier rolled dispatch 002 back to 001; routine docs unit 003 rotated after routine docs unit 002 at 10% context
```

I recovered original build `9df978d8a798c42d` from the prior master's bootstrap attachment and reran the same probes with its kitchen.sh selected through PROBE_SKILLS. The four cases produced the same outcomes (`Ran 4 tests`, `OK`). The probe's helper for sending the verifier its NOTE messages still imports the a90bb41 adapter; dispatch, sync, finish and notify use the selected build. The original build's reuse predicate and context parsing are identical to the relevant current functions. This supports the same mechanism in the old build; it is not a replay of the historical run.

### Source of the confirmed failure

- `skills/pstack-pair/scripts/pstack_core/mux/amp.py:78-80,292-296` keeps only the latest outbound bundle per peer thread. `Amp.send` replaces that file after every successful send at lines 1189-1190.
- `commands/sync.py:135-137` selects that latest outbound bundle as the base for every incoming reply from that peer. There is no identity tying the incoming reply to the actual snapshot the role consumed.
- `bundle.py:185-202` treats `local == base` as permission to accept incoming. After the master has sent a newer snapshot, this is true even when incoming is older than both local and base. Missing bases are worse: `merge_file` falls back to the sender's whole pair.json at lines 220-227.
- `commands/rotate.py:158-168` trusts the resulting `dispatch.brief`, requires both briefs to be routine with the same profile set, and rejects unavailable context or usage at least 50%. Its choice is correct for the stale metadata it receives.
- `send.py:184-194,267-276` records dispatch before packing the outbound brief. Ordinary finish replies therefore have the correct brief and generation. `commands/report.py:206-216,235-247` sets rotation_required only for a done report matching that dispatch/generation.

`verify`, `--covers` and `revise` do not assign dispatch.brief in the sources inspected. `send.record_dispatch` owns that assignment. A verifier can still roll it backward through bundle merge, as the probe demonstrates. `Amp.context_used` reads the thread from the registry entry under the sidekick's name, not from pair.json pane_id (`mux/amp.py:695-707`). `commands/sync.py:52-72` prevents an ordinary verifier bundle from overwriting an already-known sidekick registry entry.

### Original-run evidence and limits

Read the prior master [thread](https://ampcode.com/threads/T-01a1f2c8-c8e8-74c0-b1bf-e6c4d58cad7d) and unit 037 [sidekick thread](https://ampcode.com/threads/T-01a1f2ca-9aab-74d3-836e-8f81e5a1fc49). They show replacements starting before old sidekicks were archived. For 036 to 038, the first dispatch printed `archive: 0 archived, 0 kept, 1 in use`; only the retry after the replacement's READY archived the old thread. This contradicts pre-emptive idle archiving as the cause for that transition.

No original context result or rotation-reason trace was recorded. A live context query on the now-archived 037 thread was refused because it is archived; I did not unarchive it. Its exported final assistant usage records totalInputTokens 140587 and maxInputTokens 872000. Those provider usage fields are not the exact totalTokens/maxContextTokens pair that `keeps_session` consumed, so they do not prove the original percentage. The raw export was removed; only usage metadata was retained locally.

The actual historical interleaving that would prove the stale-base cause has not been recovered. The run's whole store and sent-bundle cache are not in the handoff. The prior master transcript also does not establish a newer verifier message overtaking an older reply at each affected dispatch.

## Options

| Option | Cost, risk, and what it forecloses |
| --- | --- |
| **Recommended scoped fix: master-owned scheduling metadata stays authoritative on role replies** | Preserve the master's dispatch and sidekick identity/generation/startup fields on inbound role-to-master merges. Accept a sidekick's completion flag only for the current generation and dispatched brief. Preserve role report/event delivery. This stops the demonstrated scheduling rollback without changing context policy. Apply only with known sender/receiver roles, so master-to-role sync and master handoff still update metadata. |
| Preserve an exact acknowledged merge-base identity for every reply | Repairs general delayed-message merging, including arbitrary files. Requires retained base snapshots, a manifest field naming the snapshot actually consumed, and handling old clients/missing bases. Broader than the rotation fix. |
| Clear rotation_required on sync or raise the context threshold | Reject. Ordinary completion legitimately sets the flag, and no measurement supports a threshold change. It would leave metadata rollback intact. |
| Diagnostic-only 005 | Smallest option if the master wants to fix only the demonstrated historical incident. Emit the rotation reason and capture one affected real dispatch before changing policy. Leaves the separately reproduced stale-reply defect open. |

The scoped ownership fix is a recommendation, not a patch I tested. Reuse the git store's distinction between master-owned metadata and role-owned outputs as the design precedent; do not make every incoming pair.json master-authoritative, since the receiver may itself be a role.

## Risks and ranked remaining candidates

1. **Confirmed candidate: stale or missing merge base.** It can produce the exact symptom at low context. Prove historical attribution with the incoming bundle, its sender, the actual earlier snapshot it consumed, the receiver's latest sent bundle, and pair.json before/after sync. A stale rotation_required flag alone does not distinguish it.
2. **Context lookup failure.** `thread_context` collapses a failed CLI call into an empty dict and `context_used` turns that into None; reuse then rotates silently (`mux/amp.py:126-133,695-707`). Capture the queried thread ID, subprocess exit code and a sanitized failure category. Do not dump stderr or the environment into the store.
3. **Legitimate policy rejection or altered limit.** Capture both brief paths, existence, parsed risks/profiles, raw token counts, maxContextTokens, compactionCount and the effective PAIR_REUSE_CONTEXT_PCT at the decision. Same profiles alone do not promise reuse. Missing previous files, escalation and context pressure all take the same empty-string branch today.

For a small diagnostic change, replace the opaque rejected result from `keeps_session` with a reason that `rotate_if_due` prints and records before replacement startup. Include the old thread/generation and previous/next brief identifiers. Keep the existing fail-closed behavior when context cannot be read. The missing signal, rather than a guessed policy change, is what would resolve the remaining historical uncertainty.

Regression acceptance for the reproduced bug should require that the newer-outbound/older-reply sequence preserves the master's latest dispatch and generation, delivers the old verdict without losing it, and keeps the same docs sidekick for 003 at 10%. Keep controls for the ordinary round trip, post-rotation READY, and legitimate area/context-triggered rotation. The probe currently expects the wrong result in its final case; invert those expectations when turning it into the regression test.

## Questions

None for the human. The master can choose a scoped stale-reply fix or diagnostic-only 005 without assuming either is the proven cause of all original rotations. No tracked source was changed, no commits were created, and scratch was removed.
