# Plan 021: kept-sidekick-session-r2

round: 2
supersedes: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/008-kept-sidekick-session.md
contract: 2
store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
risk: escalated (bundle merge semantics of pair.json and master files on role-to-master sync)

## Goal

A delayed role reply can no longer roll back or delete the master's state on a local-store sync: pair.json is kept whole from the master and only validated role facts are applied, and master-owned files are never changed or removed by a role's bundle; a new sidekick generation starts with `rotation_required` cleared; and every rotation decision under a session-reusing mux says why. Proven by the consultant's two-orb reproduction turned into a regression test (with `.consult` and a freshly added master key among the asserted fields), which fails at c5cf250.

## Design

Start from c5cf250 (`git fetch origin kitchen-code/kitchen-followups/sidekick-g3 && git checkout -B kitchen-followups FETCH_HEAD`); do not rebase onto origin/main.

1. **Classify the transfer before applying it.** In `commands/sync.py`, before `bundle.apply` and before `sync.learn` touches the registry, decide from the receiver's own pre-merge state: the receiver is the master when local pair.json `.master.name` equals the manifest's `to.name`; the transfer is a role reply when the receiver is the master and `from_name` differs from that name or is empty (an old build). Pass `protect=True` to `bundle.apply` only then. Master-to-role delivery, a role's own bootstrap (empty receiving registry) and master-to-master hand-over (same master name) keep today's merge.
2. **Files (protect=True).** Before the generic merge, deletion and TSV union, keep the receiver's copy of every non-`.tsv` path `gitstore.classify` (RULES) says is master-owned: never update or delete it, including with a missing base or an incoming deletion. Import gitstore inside the function (gitstore imports bundle). `.tsv` files keep their union merge; role-owned outputs (reports, verdicts, advice, steps, progress) merge as today. The sync's summary line names the protected paths it refused.
3. **pair.json (protect=True).** Keep the receiver's document whole. Apply one allowlisted role fact, from a constant beside the merge: `sidekick.rotation_required`, accepted only when the incoming value is `true`, the incoming `sidekick.generation` and `dispatch.brief` are present and equal the receiver's, and the incoming dispatch generation (if recorded) matches that sidekick generation. An incoming `false`, an absent value, a mismatch or an unrecognised sender leaves the receiver's flag. `bootstrap_pending` and `skill_builds` are not role facts (READY settlement is the master's; skill-build telemetry from a role is dropped under protect).
4. **New generation clears the flag.** In `spawn.record_started` (the successful new-generation update), set `rotation_required = False` beside `bootstrap_pending = True`; this covers rotate, failover and rotate_if_due. A start that never records a generation leaves it.
5. **Rotation reason.** Keep `keeps_session` returning its kept string (callers and the core mock at test_pstack_core.py about L21974 unchanged). Add `rotation_reason(store, brief, what)` sharing one decision function with it: '' when the mux does not reuse sessions (Herdr, Rex: goldens unchanged); otherwise `previous brief missing`, `risk <prev>/<next>`, `profiles <a> vs <b>`, `context unreadable`, `context N% >= limit`, `compacted`, `failed over`, `plan`. Distinguish compaction from 100% usage without changing `Amp.context_used`'s return for existing callers (expose compaction separately). `rotate_if_due` prints `rotate: <reason>` before the rotation and passes it to `rotate(..., reason=...)`, whose `rotate` event detail becomes `generation:N reason:<reason>` with N the generation being replaced.

## Alternatives and trade-offs

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| Keep master pair.json whole, apply allowlisted role facts; protect master-owned files by RULES | Fixes `.consult` and every future master key; matches the git store's ownership split | Each new role fact needs an explicit entry | yes |
| Denylist of master keys (round 1) | Smaller diff | Leaves `.consult`, `.sent`, `.pending`, `.sidekick.delivered` rolling back (consultant probe) | no |
| Exact consumed-snapshot merge base per reply | General fix for every file | Wire-format change, retained snapshots, old-client fallback | no (follow-up) |
| Clear the flag on sync / raise the threshold | Trivial | Suppresses policy; rollback stays | no |

## Steps

1. Ownership guard on role-to-master sync, with the regression.
   playbook: bug-fix
   - Move the probe's two-orb helpers from `advice/005-kept-sidekick-session-c1-probe.py` into `AmpKitchenTests` and add the newer-outbound/older-reply test: after the delayed verifier reply, the master keeps `.dispatch`, `.sidekick` generation, `.consult`, a freshly added master key, an edited standing-orders.md and a newly created brief; the old verdict is delivered; docs 003 keeps the sidekick at 10%. Keep the probe's `two_orbs_keep_after_rotation` case; drop the other two controls (covered elsewhere).
   - `BundleMergeTests` (test_pstack_core.py) table over the pair.json rule: current positive completion accepted; incoming false with matching generation and brief ignored; old generation ignored; missing fields on both sides ignored; unknown sender ignored; missing base keeps the master's pair.json; master-to-role delivery and master hand-over unchanged.
   - Commit each with its tests; `kitchen.sh step`.
2. New generation clears rotation_required.
   playbook: bug-fix
   - Regression: `kitchen.sh rotate`, READY, then `dispatch` of the next brief is sent (fails today with FreshSessionRequired); a failover row too.
3. Rotation reason.
   playbook: bug-fix
   - One table-driven test over the reasons (extending the half-window and compacted tests about L16380-16387), asserting the printed `rotate:` line and the event detail; a Herdr run prints none; no golden changes (say so in the report).
4. Docs.
   playbook: docs
   - SKILL.md \"Amp threads\" bullet on the local-store merge: a role's reply never changes the master's files or pair.json except a sidekick's current completion; and the `rotate: <reason>` line. tests/SPEC.md.

## Playbook (plan blocks)

Written by `kitchen.sh` from each Steps block's `playbook:` and `with:`; edit those lines, not this section.

### Block 1: bug-fix

#### bug-fix.intro-1 (master)

You own the plan and the review. Hand the investigation and the fix to the sidekick through the brief, and stay in the lead: plan, review, verify.

#### bug-fix.intro-2 (master)

Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" is a hypothesis, not a fix. It does not ship. When evidence refutes a hypothesis, revert what it motivated. The smallest change the evidence justifies ships, nothing more.

#### bug-fix.step-6 (master)

Open a PR only as far as the kitchen's [landing].mode allows, and only when the brief says so; the kitchen never merges, and the master stops at the mode.

### Block 2: bug-fix

#### bug-fix.intro-1 (master)

You own the plan and the review. Hand the investigation and the fix to the sidekick through the brief, and stay in the lead: plan, review, verify.

#### bug-fix.intro-2 (master)

Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" is a hypothesis, not a fix. It does not ship. When evidence refutes a hypothesis, revert what it motivated. The smallest change the evidence justifies ships, nothing more.

#### bug-fix.step-6 (master)

Open a PR only as far as the kitchen's [landing].mode allows, and only when the brief says so; the kitchen never merges, and the master stops at the mode.

### Block 3: bug-fix

#### bug-fix.intro-1 (master)

You own the plan and the review. Hand the investigation and the fix to the sidekick through the brief, and stay in the lead: plan, review, verify.

#### bug-fix.intro-2 (master)

Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" is a hypothesis, not a fix. It does not ship. When evidence refutes a hypothesis, revert what it motivated. The smallest change the evidence justifies ships, nothing more.

#### bug-fix.step-6 (master)

Open a PR only as far as the kitchen's [landing].mode allows, and only when the brief says so; the kitchen never merges, and the master stops at the mode.

### Block 4: docs

#### docs.intro-1 (master)

**You own the prose. Say what holds, and show where it holds.**

#### docs.intro-2 (master)

A docs change edits prose only: a README, a guide, a reference page, an ADR's wording. It changes no behavior, so it needs no plan round and no new test.

#### docs.step-2 (master)

A code change the text needs comes back as a Question; route it through a new brief.

## Verification

The two-orb regression fails at c5cf250 and passes at the head; full suite passes; goldens unchanged.

## Risks

- A legitimate role write to a master path would be silently dropped; the sync line names refused paths, and the sidekick lists every pair.json writer it finds in the report.
- Unclassified files (host-roster.toml) keep the stale-base hazard: a follow-up, not this unit.
- This run is on the git store, so live dispatches check the reason diagnostics but not the merge fix; the fake-Amp regression is the proof.

## Open questions

none

## Disagreements resolved

- Sidekick 1 and consultant 1 (denylist leaves `.consult` and others): adopted; pair.json is kept whole with an allowlist of role facts.
- Consultant 2 (matching identity admits a stale false): adopted; only a positive value from a present, matching generation and brief is applied, and unknown senders get no exception.
- Sidekick 2 (`keeps_session` return type is mocked; the event is written in `rotate()`): adopted; separate `rotation_reason` sharing one decision, passed into `rotate(reason=)`.
- Sidekick 3 (reason noise under Herdr/Rex, goldens): adopted; silent where sessions are not reused.
- Consultant risks (classify before apply and learn; compaction vs 100%; failover and plan branches; reason recorded with the replaced generation): adopted in Design 1 and 5.
- Sidekick alternative 4 / consultant (new generation clears the flag, including failover): adopted as Design 4.
