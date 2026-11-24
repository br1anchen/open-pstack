# Report 051: reply-names-its-base

status: done
brief: briefs/051-reply-names-its-base.md
playbook: review-fix
branch: kitchen-followups-047
head: b131308e833d2eb556d3383db39fb484daac25a7
tree: clean

## Changed

Branch `kitchen-followups-047`, started at unit 049's head 648f31bf, three commits on top (steps 1 to 3 are one commit because the reply merge, the kept bundles and the id cannot be tested apart; recorded as steps 1, 2, 3):

1. 31bf16d5 (steps 1-3):
   - bundle.py: `pack(..., synced='')` writes `synced` into the manifest, next to the existing `id` (the existing time_ns + uuid hex id, so no new id scheme). `read_manifest` reads a manifest alone. `apply` treats a reply's base as known only when the base bundle's id equals the reply's `synced`; otherwise the base is dropped and `merge_store(..., based=False)` keeps every local file the reply lacks (no deletion by omission). With the right base, 049's rule stands (keep only when local differs from base or base lacks it).
   - mux/amp.py: `keep_sent_bundle` also keeps each sent bundle under `sent/<thread>/<id>.tgz`, the newest PSTACK_BUNDLE_KEEP (8) per thread (the old `sent/<thread>.tgz` stays as the base for non-replies). `kept_sent_path` (refuses ids with a path separator or leading dot), `record_applied` and `applied_id` keep the id of the last bundle applied from each sender in `applied.json`. `with_bundle` and `relay` pass `applied_id(thread)` to `pack` as `synced`.
   - commands/sync.py: a reply is merged against `amp.kept_sent_path(sender, manifest.synced)`, any other bundle against the last one sent as before; after the apply it records the applied id. A role reply with no kept base prints `merge: the reply's base <id or (none named)> is not kept; files it lacks are kept` first.
   - tests/test_pstack_core.py: new `BundleReplySyncTests`: one table over rows a (master wrote the file after the role's snapshot, newer bundle sent, sidekick and verifier), b (role deleted its own unchanged file, also after a newer send, and for a verifier) and c (unknown id, no id: file kept, line printed), driven through `sync` itself; and one test that a bundle names the last id applied from its receiver and that only the newest PSTACK_BUNDLE_KEEP sent bundles are kept. 047's `test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot` is back as 047 had it, with the verifier and sidekick subtests, and passes; 049's replacement test is gone, since the table covers it.
2. e104caf0 (step 4): mux/amp.py `role_is_idle` uses `send.took_a_brief(doc)` in place of the str() comparison. The archive table gained a row for a store that records no generation (neither side names one: `took_a_brief` reads 0 == 0, so a done sidekick stays archivable). 049's fresh-generation row passes. The `test-touch` policy refused the first try of this step, which had no test file, so the commit was amended with that row and recorded.
3. b131308e (step 5): pstack-kitchen/SKILL.md says that every bundle has an id and names the one its sender last applied (`synced`), that the master keeps the newest 8 sent bundles per thread, that a reply is merged against the bundle it synced, and that a reply whose base is gone deletes nothing and prints the merge line. tests/SPEC.md regenerated at each commit.

## Ran

```
$ kitchen.sh step ... (full suite) at 31bf16d5c  -> gates: pass (pstack,pstack-docs); step 1 recorded
$ kitchen.sh step ... at e104caf04               -> gates: pass; step 2 recorded
$ kitchen.sh step ... at b131308e8               -> gates: pass; step 3 recorded
```
Before step 1's gates passed, the first run failed two older RoleReplyMergeTests because they asserted the exact sync lines and a hand-built reply now also prints the merge line; the line moved from bundle.apply to sync.py (only sync knows the kept bundle), and the first commit was amended.

Failing before, passing after (brief Acceptance):
- Row evidence: with `sync.py` temporarily using the newest sent bundle as the base (what 648f31bf does), rows a (sidekick), a (verifier) and b-after-a-newer-send of the new table fail (`AssertionError` on the kept file and on the merge line); with the synced base they pass.
- At 648f31bf itself the new table cannot run (`TypeError: pack() got an unexpected keyword argument 'synced'`), and 047's restored verifier subtest fails there: `FileNotFoundError: .../store/verdicts/001-x-v1.md` (the file was deleted); it passes on this branch.

## Self-check

figures: results above come from the gate output and the targeted runs
scope: nest.py and snooze.py untouched; no push, no PR; origin/main untouched
review.self: none named by the kitchen
lessons: none in the brief

## Deviations

- The brief's step 3 (c) line is printed by commands/sync.py rather than by bundle.apply, so a direct `apply` call with no named base stays silent and merge_store's behaviour is unchanged for it.
- Steps 1, 2 and 3 are one commit and one recorded step.

## Questions

none

## Follow-ups

- A bundle sent before this change has no `synced`; a reply to it merges as an unknown base (deletes nothing) until the next send carries one.
