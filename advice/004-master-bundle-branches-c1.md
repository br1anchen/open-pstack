# Advice 004-c1: Master handoff Git state and roster

status: answered
consult: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/consults/004-master-bundle-branches-c1.md
kind: design
head: a90bb4130393b864176dbeded9bb216fcb5f255c
tree: clean
scratch: /home/user/workspace/repo/.amp/in/orb-backup/consult-scratch/runs-kitchen-followups/004-master-bundle-branches-c1 (removed before sending)

## Answer

Split into two units. Make 004 preserve all local branch names and tips on master handoff, apply refs conservatively, and give an actionable divergence message. Make 007 preserve the kitchen roster through a store snapshot and missing-only restore. Reject automatic checkout moves based on patch-ID containment. Two real Git counterexamples show that predicate can discard or corrupt wanted content.

Carry the extra branches only on master handoff, not every role message. Use the existing `Amp.launch_master` boundary to request that bundle mode explicitly. Keep normal role sends at their current HEAD-plus-scratch scope. This preserves the master's otherwise stranded work without repeatedly carrying unrelated branch histories in every report and steer.

The Boundary Discipline principle determines placement. The generic bundle handles refs and Git objects. `kitchen_core/master.py` handles roster snapshot and restore through `kpy`; the pair core never imports kitchen code or discovers kitchen host files.

## Grounding

Read `notes/kitchen-gaps.md` gaps 1, 2 and 4, the named source sections, and the existing bundle and master-handoff tests. Executed `advice/004-master-bundle-branches-c1-probe.py` in scratch at the head above. It uses real disposable Git repositories, the current `bundle.pack` and `bundle.git_bundle`, and the same `diff-tree --binary --full-index | patch-id --stable` calculation as `kitchen_core/units.py:57-75`.

Run it from another scratch checkout with:

```sh
GIT_CONFIG_NOSYSTEM=1 PROBE_ROOT="$scratch" python3 "$store/advice/004-master-bundle-branches-c1-probe.py"
```

It exited 0 after asserting all six observations below. These are counterexamples and mechanism checks, not tests of an implemented fix.

```text
PATCH CONTAINMENT: every old patch matches incoming history, but its final tree reverted the wanted change
PATCH EQUALITY: different Python indentation and different trees have the same stable patch ID
CURRENT HANDOFF: unpushed non-HEAD old branch commit is absent from receiver
EXPLICIT BRANCH TIPS: one bundle preserves unpushed non-HEAD branch objects and side refs
REMOTE CUT: a tip known only on a sender-only remote makes the requested handoff bundle empty
CHECKED-OUT BRANCH: update-ref changes another worktree HEAD without updating its files; receiver must not do this
```

The first probe starts with an old commit changing `f.txt` from zero to one. Incoming history cherry-picks that commit onto a new base and then reverts it. Every old patch ID is in incoming history, but incoming's final file says zero. The second probe compares adding `if True:` with an indented body against adding it with an unindented body. Their stable patch IDs match despite different trees and valid versus invalid Python. Patch IDs are useful review evidence, not authorization to move a branch.

### Branch transport and application

1. **Record every local head in a master-handoff manifest.** Use a branch-name-to-full-OID mapping from `refs/heads/`, including branches already reachable from origin. Git objects and branch names are separate state. A branch may need no new objects but its local name still needs restoration. Do not scrape prose for branch names or silently omit branches because some remote-tracking ref contains them.
2. **Pack their tips into one Git bundle.** Existing `bundle.pack` only lists HEAD and scratch tips (`bundle.py:130-176`), which reproduces gap 1. Reuse the existing ref-map mechanism and one pack, rather than one bundle per branch. Branch names containing `/` are valid; validate imported names as head refs before constructing destinations.
3. **Exclude only a common, fetchable baseline.** `git_bundle` currently uses `--not --remotes` (`bundle.py:110-127`), while its receiver fetches only origin (`bundle.py:279-286,343-347`). A sender-only remote can therefore remove required objects. For the new handoff mode, cut against a freshly confirmed origin default-branch baseline that the receiver explicitly fetches, or send a self-contained bundle when no such baseline is available. Record the prerequisite. Never call an omitted object transferred. `gitstore.py:983-1002,1165-1180` already documents the same failure and implements a trunk-limited cut for its different protocol; borrow the principle, not its store-specific code-ref policy. If a formerly fetchable prerequisite has disappeared, fail with missing-commit diagnostics instead of treating a partial transfer as a successful handoff.
4. **Fetch into per-handoff side refs first.** Keep the incoming tips reachable under a namespace containing the bundle ID, not just the sender's thread tag. The current `refs/pstack/in/<sender>/*` is replaced by later sends. A conflict's only recovery ref must not disappear on the next report.
5. **Create missing local branches; fast-forward safe existing ones; keep everything else.** For an existing unoccupied branch, update only when its old tip is an ancestor of incoming, using an expected-old-OID update. If local is ahead, keep it. If divergent, keep local and print both tips plus the durable incoming ref. Do not force-update or delete receiver branches because they were absent from the sender's snapshot.
6. **Handle occupied branches separately.** Enumerate `git worktree list --porcelain`. Never advance a branch ref directly while any worktree has it checked out. The probe shows this changes that worktree's HEAD but leaves its index/files behind. The current worktree goes through normal clean-checkout handling; another worktree's branch stays put with an explanation. Dirty receiver state and branch-name conflicts preserve local state and the incoming side ref.

The all-branch handoff mode belongs in the call from `Amp.launch_master` (`mux/amp.py:1351-1366`) through `send`/`with_bundle` to `bundle.pack`. An explicit parameter keeps ordinary role messages unchanged and avoids interpreting a title containing the word master as authority to snapshot every branch. Old manifests without the branch map retain their current meaning.

Object import failure must return nonzero before the new master treats the handoff as complete. Merely printing a warning and proceeding to `take-master` can archive the only orb still holding the missing branch. Ref conflicts are different: imported objects remain durable, local refs remain intact, and the master gets the conflict to resolve.

### Roster snapshot and restore

Use a store snapshot, not a new generic tar member or a second attachment. The store already travels, and the roster is small. `kitchen.py:459-463` is the source of truth for its path, including `PSTACK_KITCHEN_ROSTER`.

- Snapshot the effective roster immediately before master handoff, before `launch_master` packs the store (`kitchen_core/master.py:493-510`). An init-only snapshot becomes stale if the user changes a model or fallback chain during a run, and does not help existing runs. Init may seed the snapshot, but the handoff must refresh it from the source it actually uses.
- Keep the snapshot generation-specific under `sessions/`, such as `master-g2-roster.toml`, and name it in the handoff record. That avoids confusing a manually maintained `host-roster.toml` or a delayed role bundle with the snapshot for this takeover. Old runs with no snapshot continue with an explicit notice; do not invent a default roster and present it as the previous master's choice.
- Keep the actual TOML bytes so role arguments, alternates, nested verifier entries and fallback ordering survive. Parse/validate before launch and scan the bytes before putting them into the store, using the existing `pstack_core.secrets.scan` surface. On a finding, report file and kind only. This copies the roster, not the files referenced by host-specific `[[kitchen]]` entries, nor credentials from the environment. Host-local paths are not made portable by copying their text.
- In `take_master`, first verify the caller owns the pending handoff (`master.py:640-650`). Restore before closing the old master or clearing `master.previous`. Use the receiver's current `roster_path()`, including its override, rather than writing to the sender's absolute path. If the target does not exist, install the complete snapshot atomically without replacing a concurrently created file. A failed required restore leaves takeover retryable.
- If the target exists and is identical, do nothing. If different, keep it and say that the receiving orb's roster remains authoritative; name both paths without dumping either file. An unreadable or invalid target must not be silently treated as absent. Never overwrite a different roster automatically. The same command rerun must not replace a file it installed or a later user edit.

Raw roster copying also preserves `[machine]` and host kitchen mappings, not only role models. For this small unit, state that limitation rather than add a general configuration migration system. If the destination already has machine-specific settings, its existing roster wins. A later role-only run configuration would be a different design, not an implicit rewrite of this host file.

### Divergent and rebased checkouts

Keep divergence refusal in `move_checkout` (`bundle.py:298-323`). A clean worktree says there are no uncommitted tracked edits; it says nothing about whether incoming history retained the old branch's intended result. `--keep` protects working-tree edits during a reset, not the semantics of the commit history being replaced.

When divergence is detected, leave HEAD and the current branch unchanged and print the current branch, old OID, incoming OID and incoming recovery ref. A recovery recipe should verify it is still on that branch at that old OID, check for local changes, create a recovery branch at the old OID, and only then show the exact `git reset --keep <full-incoming-oid>` command. Do not present a bare reset as safe proof of a rebase. Shell-quote generated paths and ref names.

A recovery branch such as `kitchen-recovery/<full-old-oid>`, created without force, is preferable here to a mutable `refs/pstack/prerebase/<branch>` slot. It survives Git GC, is not overwritten by the next reset recipe, and the proposed all-local-head handoff carries it to the next master. If choosing private prerebase refs instead, make them immutable per operation and include that namespace in later handoffs; otherwise the backup disappears with the old orb.

No patch-ID helper needs to move into the pair core for this design. If automatic rebase adoption remains a requirement, make it a separate unit with explicit old-base/new-base provenance and exact tree-equivalence evidence from replay. Patch IDs alone, even with a recovery ref, are not that proof.

## Options

| Choice | Cost, risk, and what it forecloses |
| --- | --- |
| **All local heads, master handoff only** | Recommended. Complete branch inventory at the point the owning orb retires; rare extra object transfer. Does not preserve arbitrary side branches created and abandoned exclusively in a role orb. |
| All local heads on every send | Simple selection rule, but repeatedly ships unrelated unpublished history on reports, steers and verifier messages. Attachment growth and wider disclosure buy nothing for the stated master-handoff failure. |
| Only branches named in briefs/reports | Lower payload, but prose is not a reliable Git inventory. Renames, abbreviations and a branch mentioned only in a handoff reintroduce data loss. |
| Only branches not reachable from any remote | Reject as the selection rule. It conflates object availability with local ref names and assumes receiver remotes match sender remotes. |
| **Store roster snapshot, missing-only restore** | Recommended. Kitchen owns both ends; no new transport format. Refresh at handoff prevents an init-only snapshot from drifting. Existing receiver settings remain authoritative. |
| Host-file tar member or roster attachment | Adds a special transport/install path and makes core code aware of kitchen host policy without improving this use case. |
| **Keep divergence and provide guarded recovery** | Recommended. Preserves work and makes the operator's decision explicit. Does not promise automatic rebase recognition. |
| Automatic stable-patch-ID reset | Reject. Both the revert and whitespace probes satisfy its gate while changing or removing wanted results. A backup improves recovery, not correctness. |

## Risks and unit boundaries

**004, Git handoff.** Branch-map transport, explicit master-handoff selection, fetchable bundle prerequisites, safe ref import, and divergence recovery messaging belong together because they share the import contract. Tests should use a fresh receiver lacking the unpushed objects; include two non-HEAD branches, an already-published branch name, slash names, repeat apply, fast-forward, ahead/divergent local branches, dirty state, a branch checked out in a second worktree, and an unavailable sender-only remote. Preserve ordinary role-message payload selection. Keep the two patch-ID counterexamples as acceptance against adding an unsafe automatic reset, not as assertions about a private helper.

**007, roster handoff.** Snapshot/restore stays in kitchen master logic. Test override source/target paths, a roster changed after init, absent/identical/different receiver files, missing old-run snapshot, invalid or unreadable input, scan refusal, repeated takeover, and preserving role arguments/alternates/fallback chains. No pair-core import of kitchen modules and no automatic replacement of an existing host file.

These are independent contracts and can be verified separately. The 600-line escalation threshold is not a reason to put destructive checkout adoption in the same unit. The proposed changes were not implemented in the shared checkout; only the disposable probes ran. Git remained clean and scratch was removed.

## Questions

None for the human. The master can write the two briefs from these recommendations. Automatic rebase adoption would need a new design decision rather than the proposed patch-ID shortcut.
