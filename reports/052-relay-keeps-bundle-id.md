# Report 052: relay-keeps-bundle-id

status: done
brief: briefs/052-relay-keeps-bundle-id.md
playbook: review-fix
branch: kitchen-followups-047
head: f117480b83a23ccbceccdf5d394e552470e89391
tree: clean

## Changed

Branch `kitchen-followups-047` from unit 051's head b131308e, three commits:

1. 8554390d (steps 1, 2, 3)
   - mux/amp.py: new `keep_bundle_by_id(thread, path)` holds the by-id copy and the prune (newest PSTACK_BUNDLE_KEEP, 8; a file already removed is ignored with `contextlib.suppress(FileNotFoundError)`; its docstring says the bound is the same as the evidence refs'). `keep_sent_bundle` calls it. commands/relay.py `settle` calls it too, whether or not a newer send holds the legacy `sent_path` base, since the role synced that bundle and its reply names it.
   - bundle.py `apply(path, base, reply, sent=None)`: with an unknown reply base, `base_files` is the last bundle this side sent that sender (`sent`), or empty when none exists; `merge_store` still gets `based=False` and keeps every file the reply lacks. So an own-role file only the role changed is taken without a conflict line; one the master also changed since its last send is taken and named `conflicts, took theirs`. sync.py passes `amp.sent_path(sender)` as `sent`. I kept `based`: it still decides whether a missing file is deleted.
   - tests/test_pstack_core.py `BundleReplySyncTests`: new row b "the master relayed the bundle the role synced" (through `relay.settle`, then `sync`: report deleted, no `is not kept` line); new test `test_an_unknown_base_takes_a_file_only_the_role_changed_silently_and_names_a_file_both_changed`.
2. 2cef31c6 (step 4): `role_is_idle` catches DamagedStore from `took_a_brief` and returns False (in use). Archive table: new rows "no generation and dispatch generation 0" (archivable) and "string generation" (in use, sweep continues).
3. f117480b (step 5): pstack-kitchen/SKILL.md reply-merge sentence: ownership rules (RULES), merged against the bundle it synced (sent or relayed), relayed bundles kept at relay-done, unknown base deletes nothing and conflicts only where this side changed the file since the last send. tests/SPEC.md regenerated in each commit.

## Ran

```
$ kitchen.sh step ... at f117480b8 (full suite)  -> gates: pass (pstack,pstack-docs); step 1 recorded
targeted at each commit: -k bundle 31 tests 0 failed; -k relay 13 tests 0 failed; -k test_threads_archive_archives 0 failed
check-pstack, spec_gen --check, ruff format/check, uv check --frozen -> pass
```
Failing before, passing after (with skills/ reset to b131308e, the new tests kept):
- relay row: `AssertionError: True != False : merge: the reply's base <id> is not kept; files it lacks are kept` (the relayed bundle was not kept by id).
- both-changed conflict case: `AssertionError: False != True` (no conflict line named).
- string generation row: `AssertionError: 1 != 0 : kitchen.sh: pair.json is damaged: generation is string, not an integer`.
- the generation-0 row passes at b131308e too (took_a_brief already compares counts there); it fails with 049's old str() comparison, which is what the brief meant, and it pins that the count-based check stays.

## Self-check

figures: results come from the gate and targeted runs shown
047's, 049's and 051's tests pass unchanged; nest.py and snooze.py untouched; no push, no PR
review.self: none named by the kitchen

## Deviations

- The five steps are three commits and one recorded step at the head (the gate runs the same full suite on the whole tree); steps 1, 2 and 3 share a commit, as in unit 051.
- The brief expected unknown-base replies to report false conflicts; with an empty base `merge_file` already stayed silent, so the visible change is the opposite case: a file both sides changed is now named as a conflict, as the brief's rule says.

## Questions

none

## Follow-ups

none
