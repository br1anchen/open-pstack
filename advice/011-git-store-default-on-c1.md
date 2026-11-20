# Advice 011-c1: git-store-default-on

status: answered
consult: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/consults/011-git-store-default-on-c1.md
kind: design
head: a90bb4130393b864176dbeded9bb216fcb5f255c
tree: clean
scratch: /home/user/workspace/repo/.amp/in/orb-backup/consult-scratch/runs-kitchen-followups/011-git-store-default-on-c1, removed before writing this advice

## Answer

Make git the default for new Amp kitchens, with an explicit `PSTACK_STORE=bundle` opt-out, but enable that default after the build and handoff changes. Refuse an unavailable git store instead of silently creating a local run. Keep existing stores on their current backend. Install a clean, remotely fetchable pstack commit by its full object ID and retain the archive fallback for builds that cannot be reproduced from that commit. Unit 013 needs an application-code handoff contract, not just smaller attachments. Today a git-store takeover can complete without the previous master's application commit, and even a fetched commit does not move the new master's checkout. The probes below reproduce both cases.

I recommend the order **008, 004, 006, 010, 012, 013, 011**. Fold 009 into 011. Units 004 and 006 keep their bounded bundle changes. Their underlying problems do not disappear with git. Unit 010 applies to both backends. The new facts and the recommended boundaries follow.

### Unit 011 should finish a durable initialization

Select the default at the kitchen initialization boundary, using the resolved multiplexer. The existing `remote_roles` capability identifies Amp threads here. Checking only the literal `PSTACK_MUX` environment value misses Amp's registry-based detection. Pass the selection to core initialization as data rather than temporarily changing `os.environ`. Keep Herdr, Rex, subagents, and other variants' defaults unchanged. Preserve their existing explicit git selection. Sources G1 and G2 identify these boundaries.

The selection rules should be:

- A new Amp kitchen with an unset or empty `PSTACK_STORE` selects git.
- Explicit `git` selects git. Explicit `bundle` selects the local store. Reject other nonempty values instead of accepting a typo as local.
- An existing local store stays local, including an old store with no `store_backend`. Print that it was retained and name `store-to-git` for an intentional migration.
- An existing git store stays git regardless of the new-run default. A partially created git worktree also stays git. Missing or damaged checkout metadata must not turn a recorded git store into a local one.
- Do not reinterpret an existing run because the current shell happens to set a different backend. Backend migration remains the explicit command.

Without `origin`, refuse before registration or publication, with the explicit bundle alternative. If the read remote is reachable but the push is refused, retain the pending local git state, return nonzero, and make the same `init` resume publication after access is repaired. Never switch backends after an ambiguous push. The remote may already hold the branch. P1 shows that today's init returns success on both the initial failed push and its retry, while the remote still lacks the run.

Move the first publication to the end of initialization. Core initialization should prepare the store. The command that owns initialization should finish its writes and then publish. For the kitchen this includes the skill stamp, alternate selection, and added standing orders. The plain pair command can finalize after its own writes. Use the existing publish and ownership machinery, including on retries. Do not leave two competing initialization publishers. P2 proves that the current core publication precedes the kitchen's own data.

Scan the complete initial snapshot and any unsent history before the first push. Today `gitstore.create` first pushes a generated `.gitignore`; `publish_first` later scans the initial run files through `publish`. That first push is not evidence of a secret leak, but it occurs before the complete run passes its scan. Deferring the first push gives the stronger, testable contract that a rejected initial snapshot creates no remote run branch. Retain the same scan for resumed publication.

Before that first publication, print the selected backend, a credential-free repository identifier, the branch, and its audience. Suggested meaning is: "This run publishes briefs, reports, thread IDs, decisions, and explicitly included handoff files to kitchen/<slug> on origin. Everyone who can read that repository can read these files; in a public repository anyone can. Use PSTACK_STORE=bundle for a new local run." The successful completion line should name the confirmed published commit. A disclosure is not a grant of permission to publish to another repository.

Pin existing bundle fixtures in `AmpKitchenTests.mux_env`, not in `tests/run.py` or the process environment. `inherited_env` deliberately strips `PSTACK_STORE`, so a runner-wide setting does not reliably reach fixtures and would hide tests of the new default. `AmpGitKitchenTests` keeps its explicit override. New default tests omit the override deliberately and supply a disposable bare origin. Change the migration fixture at `tests/test_pstack_kitchen.py:17388` from removing the variable to setting `bundle`. Also fix `ready_path` at lines 14189-14202 to derive its filename from the actual store, not the environment. Otherwise a correctly defaulted git run is assigned a local READY filename by the test helper.

Acceptance should cover the unset default, explicit bundle, unchanged non-Amp defaults, legacy local retention, an unknown value, no origin, push refusal followed by successful retry, a secret rejected before publication, and a fresh reader seeing the complete initialization data immediately after success. A returned exit code alone is insufficient; inspect the bare origin's tree and the store's backend.

### Unit 012 should pin the executable build, not the application checkout

Use two build descriptions with different required fields:

- A git build names the pstack source repository, a full commit ID, a fetchable ref containing it, and the build content identity.
- An archive build names the captured content identity and its attachment. It does not claim to be an installed clean Git commit.

Resolve the source from the executing skills directory and its installer, as `SKILLS` and `INSTALLER` already do. The application's origin is not necessarily open-pstack's origin. A role installed from a cached archive may have no Git provenance at all. It must keep using the archive route unless provenance can be established. For a Git-backed cache, retain its verified source description outside the shipped skills tree so the next master can identify what it runs.

Eligibility for the git route requires both matching source bytes and remote availability. Check the paths the build actually ships, `skills/` and `scripts/pstack-sync.py`, including relevant untracked additions and deletions. A dirty README outside that set need not force an archive. A modified skill, a changed installer, or an added shipped script does. Preserve the existing exclusions for caches. A dirty build gets a new content-addressed archive; do not label it with the clean HEAD and discard the edits. Source G3 describes the present inventory.

Neither proposed availability shortcut is sufficient alone. `ls-remote` lists tips, so an ancestor commit need not appear. An old `branch -r --contains` can claim that a deleted branch still contains the commit. P6 demonstrates both. Use a fresh advertised ref and a containment check against its freshly fetched tip. Fetch in a separate pstack source cache, not by moving the application's worktree. For a new or changed build, choose and validate a ref that contains the pin. Reuse the existing delivered-build record for unchanged sends to the same recipient. Revalidate the ref when preparing a bootstrap for a new recipient or when its advertised tip changes; an old positive result is not durable proof.

The receiver fetches the named ref, resolves the exact pinned commit, and installs that commit into the build cache with `git archive` or a detached worktree. It verifies the commit and the shipped content before linking skills. Use a completed-install marker written only after a successful extraction; the existence of `scripts/pstack-sync.py` alone is not proof that a partially extracted cache is complete. A moved or deleted source ref must produce a visible failure or a sender-supplied archive retry, never an installation of latest main. Fetch-by-SHA alone is not portable proof that a server will serve an unadvertised object.

`kitchen-code/*` refs are branches under `refs/heads/`. A pin reachable through one is eligible if it belongs to the pstack source repository and the receiver can fetch it. It need not be on main. In contrast, a commit stored only inside `code/*.bundle` is a blob's contents, not a remotely advertised commit. It takes the archive fallback in 012. Do not push a new branch merely to manufacture an installable pin. In this run the application and pstack source repositories coincide, but that is not a general contract.

Re-pin when the build the master actually executes changes. Advancing the application's HEAD does not change an executable cached under `pstack-builds`. If this run installs an updated pstack implementation, switch the master's installed source at that verified boundary, then deliver the new build description. Keep the build identity separate from `.sidekick.delivered`, which describes brief sections. Existing `seen.build` controls build delivery.

Keep 012 in the Amp build preparation, bootstrap, and delivery code. Preserve bundle store transport. Tests should execute the generated installer against a local bare source repository, with a different application repository. Cover an ancestor pin, a stale tracking ref, a code-branch pin, a bundle-only commit, dirty shipped files, an unrelated dirty file, an incomplete cache, and a build change in a kept role session. Assert installed bytes and links, not just prompt wording. No real model calls are needed.

### Unit 013 needs fetched code and a confirmed takeover

Ordinary git-store master rotation already omits the store tarball. It still attaches `pstack-build.tgz` for a new master, emits a branch-sync command, and stores its handoff note under `sessions/`. There is no general mechanism that collects arbitrary external scripts. P3 inspected the outgoing attachments and found only `1-pstack-build.tgz`. Unit 012 removes that attachment when the git build route is available.

Record the application HEAD, its branch, and the application branch inventory in the handoff data. Make those objects durable before publishing the handoff that names them. Preserve the unpushed non-HEAD branches required by 004 as well as HEAD. Exclude the generated run-store branches from the application inventory; their source is the separate store branch. A manifest of object IDs without objects is insufficient.

Reuse 004's conservative import rules rather than inventing another branch-conflict policy. The git transport may use approved code refs or a Git object bundle committed inside the run branch. Neither requires a store snapshot attached to the message. If a code bundle is used, the receiver needs the complete dependency set or a baseline it actually fetches. The current writer-ref and single-head bundle implementation is not already an all-branch transport. Source G5 names its limits.

The handoff note, explicitly selected scripts, and 010's roster snapshot should live in master-owned `sessions/` paths before publication. Name those paths in the handoff record and message. Do not discover scripts by copying a home directory, arbitrary `.amp/in` content, or untracked application files. A script outside the store must be explicitly selected, copied into the handoff, and scanned before publication. The message can then carry references rather than duplicate authoritative files in attachments.

Bootstrap order should be:

1. Install the exact pstack build from 012 using self-contained bootstrap commands. This comes first because `pair.sh sync` may not exist yet, or the orb's version may be too old.
2. Fetch and attach the run branch. Require the announced store commit to be present in the fetched history. A later descendant is acceptable; resetting to an older announced tip is not. Confirm the pending generation and receiving thread before acting on its handoff.
3. Fetch the application objects and verify every required object. Import the application branches conservatively and restore the intended active checkout. An unresolved active-checkout conflict must be reported, not counted as a successful takeover at an unrelated base.
4. Read the handoff files and restore the missing-only roster through 010. Confirm that required scripts are present.
5. Complete `take-master` and publish its acceptance before retiring the old master. A fetch, restore, or publication failure must leave a resumable handoff rather than closing the old master first.

P3 shows `sync --branch` and `take-master` returning 0 while the new orb lacks the old master's unpushed commit. P4 shows both commands returning 0 after fetching that commit while leaving HEAD at the old base. The probe did not archive the old master, so it does not claim archival loss. It did clear `master.previous`, which is already a false completion signal. The present `take_master` code attempts retirement before its final publication. Source G4 supports the recommended ordering.

Do not make every role's ordinary branch sync check out the latest code ref. Different roles can legitimately work at different commits. Put application checkout restoration in the explicit handoff path. The branch sync needed for a takeover must return failure when required fetching fails; its current warning with exit 0 cannot serve as the readiness gate.

There is one other path to include in 013. `Amp.relay` unconditionally packs a store snapshot for a busy-thread steer, even with a git store. P5 opened that archive and found `store/pair.json`. Give git relays the same publish-then-branch-sync semantics as ordinary git messages. Preserve relay deduplication and request tracking. Leave bundle relays unchanged. Test both the busy relay and ordinary message paths so the "branch is the source" claim covers both.

Acceptance must use a fresh clone that lacks the handed-off objects, including two unpushed non-HEAD branches. Create that clone before the unpushed work. The existing `fresh_orb` helper pushes the master's HEAD to main before cloning, which masks the missing-code case if called afterward. Also test missing code, fetch failure, a published code bundle, a receiver branch conflict, roster restore failure, duplicate takeover, and no store snapshot on a git relay. No failure should clear the pending takeover or retire the old master prematurely.

### Existing units and delivery order

| Unit | Recommendation |
| --- | --- |
| 008 | Keep first. Its ownership guard repairs current bundle runs and the explicit opt-out. Its rotation diagnostics apply to both backends. Amend the claim that git never uses the bundle merge; busy relays still do until 013 changes them. The 008 role-to-master guard does not itself fix a git master-to-role relay. |
| 004 | Keep the bundle-only transport and guarded recovery recipe. Unit 013 needs the same application branch-preservation contract for git, with a different transport. Reuse its branch inventory and conservative import behavior where that removes duplication. |
| 006 | Keep after 004 and 008 as already scoped. Local evidence refs protect objects from local GC, and its missing-evidence diagnostics also matter under git. Keep its explicit deferral of durable git evidence unless the master changes that decision. Mutable writer code refs and replaceable code bundles still do not preserve every old verification object. The default change must not be described as solving that deferred problem. |
| 010 | Keep for both backends and before 013. `sessions/` is already master-owned. Git changes how its snapshot travels, not the missing-roster problem. Add a fresh git-store receiver to its absent, identical, and different-roster cases. |
| 009 | Fold into 011. Its instruction that an unset variable selects local contradicts the new default. Remove that clause. Keep this repository's publication authorization separate from the general skill's backend selection rules. |
| 012 | Implement and prove both installer routes while git is still opt-in. This can run independently of the store-default change. |
| 013 | Implement after 004, 010, and 012. Cover code preservation, readiness, and the git relay exception before relying on branch-only handoffs. |
| 011 | Enable the default last, together with initialization failure/retry semantics, fixture pins, and the folded documentation. Treat 011, 012, and 013 as escalated contract changes. |

This order does not require undoing the owner's decision to migrate the current run explicitly. It keeps the broad new-run default from preceding the handoff safety it depends on.

## Grounding

I read the consult, standing orders, plan 008, briefs 004, 006, 009, and 010, and my earlier advice 004, 005, and 006. The shared checkout stayed clean at the header commit. I used one consultant session, with no subagents, no shared-tree builds or tests, and no real remote writes.

The source references below are at that commit:

- G1. `skills/pstack-pair/scripts/pstack_core/commands/init.py:170-271` selects git only for a new pair.json, warns on failed publication, and does not republish on re-registration. `gitstore.py:480-543` creates and pushes the initial orphan store. `gitstore.py`'s `publish` path scans staged content and unsent history. `commands/store_to_git.py` provides the existing explicit migration.
- G2. `skills/pstack-kitchen/scripts/kitchen_core/setup.py:116-144` adds the kitchen stamp, alternate choice, and standing orders after core init. `pstack_core/mux/__init__.py:57-85` resolves the actual backend. `mux/amp.py:671-690` declares Amp's remote-role capability. `tests/test_pstack_core.py:103-121` scrubs the inherited store setting. `tests/test_pstack_kitchen.py:14171-14202`, `17206-17207`, and `17388` hold the affected fixture choices.
- G3. `skills/pstack-pair/scripts/pstack_core/mux/amp.py:593-627` inventories, hashes, archives, and installs the current build. Lines 1074-1112 choose first or changed-build delivery and omit store archives under git. `.agents/setup:61` links this checkout's skills. These are separate from the application's code refs.
- G4. `skills/pstack-kitchen/scripts/kitchen_core/master.py:475-525` publishes the handoff and launches the next master. Lines 630-706 perform takeover, retirement, and then final publication. `pstack_core/commands/sync.py:81-104` fetches the git store and code, warns on failure, and returns 0 without moving the application checkout.
- G5. `skills/pstack-pair/scripts/pstack_core/gitstore.py:78-136` owns `sessions/` and per-generation code bundles. Lines 953-1052 publish one report head to a mutable writer code ref or a bundle. Lines 1165-1208 fetch trunk, run code refs, and those bundles. `mux/amp.py:1209-1260` packs a snapshot unconditionally for a relay.
- G6. `tests/test_pstack_kitchen.py:17496-17510` defines `fresh_orb`, including its push of HEAD to main. The existing git master-takeover test near line 18430 checks routing and metadata but uses that helper after rotation. It does not prove that unpushed application work survives.

I ran `PYTHONDONTWRITEBYTECODE=1 python3 consult_probe.py` in the scratch named in the header. It exercised the unmodified scripts through `GitStoreTests` and `AmpGitKitchenTests`, their fake multiplexers, and disposable local bare repositories. The final run exited 0 and printed:

```text
INIT_RETRY first_exit=0 retry_exit=0 local_pair=True remote_branch_after_retry=False
INIT_SNAPSHOT local_skill_stamp=True remote_skill_stamp=False local_landing_rule=True remote_landing_rule=False
HANDOFF sync_exit=0 take_exit=0 unpushed_head_present=False checkout_at_old_base=True previous_cleared=True old_master_archived=False
HANDOFF_ATTACHMENTS 1-pstack-build.tgz
FETCHABLE_HANDOFF head_object_present=True take_exit=0 checkout_at_old_base=True
GIT_RELAY store_snapshot=True includes_pair_json=True branch_sync=False
PIN ls_remote_exact_tip=False ancestor_fetchable=True cached_contains_after_remote_delete=True fresh_contains_after_prune=False
```

The probe cases were:

1. P1 gave `GitStoreTests.run_init` a readable local origin with a nonexistent push destination, restored the push destination, and reran the same init. Both calls succeeded without creating the remote branch.
2. P2 suppressed `spawn_roles` in `AmpGitKitchenTests` and compared the local pair.json and standing orders with `git show kitchen/demo:<path>` immediately after init. Later spawn publication could not hide the difference.
3. P3 created the new clone before committing a master-only file. It then ran rotate-master, branch sync, and take-master in that clone. It checked object existence, HEAD, pair.json, and the fake thread's attachment files.
4. P4 repeated P3 with the application commit explicitly pushed to a local `kitchen-code/demo/master-g1` ref. Sync fetched the object but takeover left HEAD at the base.
5. P5 called the fixture's `relay_of` with the sidekick in `tool_use` and read the resulting archive and sync instruction.
6. P6 pushed a descendant of the pin to a local code branch, compared advertised tips with commit ancestry, deleted the branch on the bare origin, and compared cached containment before and after `fetch --prune`.

An initial probe assertion expected an archive-marker file that this fixture did not create. I corrected that observation to report `old_master_archived=False`, then reran all cases successfully. No product code was changed to obtain these results. I removed the scratch, including the probe source, as the consult requires. This is bounded design evidence, not a full-suite result or a test of live GitHub permissions.

The Fix Root Causes principle shaped the handoff recommendation. Persisting an application-head requirement and confirming it during takeover addresses the missing state. Merely removing an attachment or adding another warning would still let the handoff complete without the application code.

## Options

| Choice | Benefit | Cost or risk | Recommendation |
| --- | --- | --- | --- |
| Fail an unavailable git init and resume the same pending store | A successful defaulted run has the promised remote source. An uncertain push cannot split the run across backends. | Offline users must explicitly choose bundle before creating a run. | Choose. |
| Automatically fall back to bundle | More initial commands succeed. | Run durability depends on a transient failure; a later command can mistake local success for remote publication. Ambiguous pushes are unsafe to reinterpret. | Reject. |
| Change defaults only for new Amp kitchens | Fixes the separate-orb workflow without surprising shared-checkout users or migrating old runs. | Two documented store backends remain. | Choose. |
| Change every variant and runtime by default | One selection rule. | Starts publishing previously local workflows without a task requirement. | Reject. |
| Use a verified git pin with a content-addressed archive fallback | Removes routine build attachments while preserving dirty and unpushed builds exactly. | Adds source provenance and receiver-side verification. | Choose. |
| Require the build to be on main | Simple fetch target. | Couples experimentation to main publication and prevents the approved unpushed workflow. | Reject. |
| Make 013 only change prompt text and attachments | Small diff. | Leaves both reproduced false-success takeover cases and the relay snapshot path. | Reject. |

## Risks

- A git store preserves its recorded files, not every historical application commit. Keep 006's deferred git-evidence limitation explicit. This advice does not reopen the separately deferred exact-final-tip landing invariant.
- A published ref can disappear between the sender's check and receiver fetch. Exact receiver verification and an explicit archive retry are still necessary. Cached availability cannot eliminate that race.
- Roster snapshots and explicitly included scripts become readable by the repository's audience under git. Secret scanning does not make private content safe to publish.
- Unit 011 changes the previous success-on-push-failure behavior. Update the existing init tests intentionally. Do not merely change expected output while leaving the remote empty.
- Unit 013 is larger than "notes and scripts" suggests. Keep its commits separated by observable behavior and retain the application-work preservation tests as its completion gate.

## Questions

No design question blocks writing the plans. There is one authorization boundary for the master to verify before live publication. Standing order 25 names `kitchen/*` on br1anchen/open-pstack. `kitchen-code/*` is a different namespace, and `publish_code` currently force-updates those refs. Do not infer permission for new code-ref writes from the run-branch wording alone. If that permission is not already established in the master thread, obtain it or keep handoff code in a bundle on the approved run branch. Unit 012 needs only reads and must not push a build pin to bypass this boundary.
