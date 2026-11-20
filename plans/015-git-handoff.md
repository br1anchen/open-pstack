# Plan 015: git-handoff

round: 1
supersedes: none
contract: 2
store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
risk: escalated (master hand-over contract under the git store)

## Goal

Under the git store, rotate-master's hand-over carries no store files: the new master installs the pinned build (plan 014), fetches the run branch, fetches and verifies the application commits the hand-over names (HEAD, its branch and every local branch), restores the active checkout, and only then takes over and retires the old master; and a busy-thread relay publishes and names a branch sync instead of packing a store snapshot. Proven in a fresh clone created before the unpushed work, which lacks the handed-off objects.

## Design

Authoritative detail: `advice/011-git-store-default-on-c1.md` section "Unit 013 needs fetched code and a confirmed takeover" (bootstrap order 1-5 and the relay paragraph). Summary:

- Hand-over record (master-owned `sessions/master-g<n>-handoff.json` beside the handoff note): application HEAD OID, its branch, the branch inventory `{name: oid}` (run-store branches excluded), the paths of the handoff note, the roster snapshot (brief 010) and explicitly selected scripts (each copied into `sessions/` and secret-scanned before publication; nothing discovered from home or .amp/in). The objects are made durable before the record is published: pushed to the approved `kitchen-code/<slug>/master-g<n>` ref, or a git bundle committed in the run branch with a fetchable baseline.
- Receiver: `take-master` (kitchen_core/master.py L630-706) runs, in order: confirm the pending generation and this thread; require the announced store commit in the fetched branch history; fetch and verify every recorded object; import branches with brief 004's conservative rules (reuse its code); restore the active checkout (clean tree; a conflict is reported and the take-over stays pending); restore the roster (brief 010); confirm the scripts; publish acceptance; only then retire the old master and clear `.master.previous`. Any failure leaves a resumable hand-over and exits non-zero. `sync --branch` used as the readiness gate returns non-zero on a failed required fetch.
- Relay: `Amp.relay` (mux/amp.py L1209-1260) under git publishes and sends the branch-sync line, no snapshot; dedup and request tracking unchanged; bundle relays unchanged.
- Ordinary role syncs do not move checkouts.

## Alternatives and trade-offs

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| Hand-over record + durable code + ordered, confirmed take-over | Closes the consultant's P3/P4 false-success take-overs | Larger unit | yes |
| Only drop attachments and change prompt text | Small | Leaves both reproduced code-loss take-overs and the relay snapshot | no |

## Steps

1. Hand-over record and durable code on the old master.
   playbook: feature
   - rotate-master under git writes and publishes the record after pushing the code ref or committing the bundle; tests: record contents, objects fetchable from a fresh clone made before the unpushed work (do not use `fresh_orb` after rotation: it pushes HEAD to main), two unpushed non-HEAD branches.
2. Ordered, confirmed take-over.
   playbook: feature
   - take-master steps 1-5 above; one table-driven test over: success (HEAD and branches restored), missing code object, fetch failure, published code bundle, receiver branch conflict, roster restore failure, duplicate take-over; none clears the pending take-over or retires the old master early.
3. Git relay without a snapshot.
   playbook: bug-fix
   - test the busy relay and the ordinary message under git: no store archive attached, the branch-sync line present, dedup unchanged.
4. Docs.
   playbook: docs
   - skills/pstack-kitchen/references/master.md "Rotate yourself" and "On pstack-kitchen MASTER", SKILL.md "Amp threads" bullets on rotation and relays, handoff-template.md if it lists carried files; tests/SPEC.md.

## Verification

In a fresh clone lacking the objects, rotate-master then take-master leaves HEAD and both branches at the old master's tips and `.master.previous` cleared only after acceptance is published; a failure case leaves it pending. Full suite passes.

## Risks

- Depends on briefs 004 (branch import) and 010 (roster) and plan 014 (build install): run after them.
- Scripts copied into the store become readable by the repository's audience.

## Open questions

- Code ref vs committed bundle for the hand-over: which one does gitstore's existing publish path make simplest, given the run's approved `kitchen-code/*` namespace?

## Disagreements resolved

none
