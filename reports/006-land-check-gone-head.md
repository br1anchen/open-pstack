# Report 006: land-check-gone-head

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/006-land-check-gone-head.md
playbook: bug-fix
branch: kitchen-followups
head: 6921d9f0870a08ee19d9bac49a6c0c4696e13321
tree: clean

## Changed

Started from unit 024's head 1e105784 (fetched from origin kitchen-code/kitchen-followups/unit-024). Three commits, one per step, tests/SPEC.md regenerated in each:

| step | commit | what |
| --- | --- | --- |
| 1 | 78ecd2c98 | `git.pin(root, run, oid)` makes the create-only ref `refs/pstack/keep/<run>/<full oid>`; `step` pins the step's commit, `finish` the report's head, `verify` (verify_launch.pin_evidence, before the verifier starts) the packet's base and head and each named unit's recorded head and dispatch base |
| 2 | 0c21a3180 | `bundle.pack` adds every `refs/pstack/keep/*` tip to the git bundle of every send, cut against `origin_baseline` (never `--not --remotes`) and lists them in `manifest.git.keep`; `import_git` / `move_git` import them into the same names, create-only, never deleted; a bundle whose evidence cannot be imported raises ValueError, so `sync` exits non-zero |
| 3 | 6921d9f08 | `land_check` prints `unverified: unit NNN: its evidence is missing (<which>: <sha>); restore it from the bundle that carried it, or prove its Acceptance again with kitchen.sh verify <store> <current-unit> --covers NNN --base <live-base>` (`missing_evidence`, `recovery_command`, `unverified_line` in landing.py); `has no clean verdict` stays for a unit whose heads resolve; exit 2 and coverage rules unchanged |

Files: skills/pstack-pair/scripts/pstack_core/git.py, bundle.py; skills/pstack-kitchen/scripts/kitchen_core/step.py, verify_launch.py, landing.py; tests/test_pstack_kitchen.py, tests/test_pstack_core.py, tests/SPEC.md. report.py and units.py needed no change: finish's head is resolved in step.py's `finish`, and `RecordedUnit` already gives pin_evidence each unit's head and base.

- Step 1: `git.pin` returns True for an existing ref that already holds the id (no-op); a ref is never moved. `step` pins after the gates and policy pass, just before the step is recorded.
- Step 2: the cut. A send that carries evidence (or a hand-over) uses the baseline origin_baseline gives (origin fetched with the unit-031 timeout, tracking-ref fallback); a send with neither keeps the old `--remotes` cut. The receiver's refs also arrive under `refs/pstack/in/<sender>/<bundle id>/keep/...` (pruned with that set); the `refs/pstack/keep` copies are the lasting ones. An entry is imported only when its name is `<run>/<id>` with the id a full commit id equal to the last part (valid_keep); others are skipped.
- Step 3: which evidence is missing, in order: the unit's report head (else last step) when it does not resolve (`unit head`), the dispatch base (`dispatch base`), a clean landing verdict whose range head does not resolve (`landing verdict head`). `<current-unit>` is filled with the highest-numbered other accepted unit whose head resolves, `<live-base>` with that unit's dispatch base when it resolves; otherwise the placeholder stays in the line. Patch-id membership on trunk (`landed_as`) is untouched and covers nothing.

## Ran

Each new test fails without its fix (the fix reverted in a scratch copy, restored after):

```
step 1: step.py, verify_launch.py at HEAD~ -> -k survive_a_rewrite: 1 tests, 1 failed
step 2: bundle.py at HEAD~                 -> -k evidence: 7 tests, 3 failed (two successive messages, import failure, bad names)
step 3: landing.py at HEAD~                -> -k says_which_evidence: 1 failed; -k recovery_command_land: 1 failed
```

At the head (6921d9f0870a08ee19d9bac49a6c0c4696e13321):

```
$ python3 tests/run.py -k land_check -> 19 tests, 0 failed
$ python3 tests/run.py -k bundle     -> 30 tests, 0 failed
$ python3 tests/run.py -k evidence   -> 8 tests, 0 failed
```

The fast gates of pstack and pstack-docs (full tests/run.py, spec_gen --check, ruff format and check, uv check --frozen, check-pstack) passed at each recorded step: 1 at 78ecd2c98, 2 at 0c21a3180, 3 at 6921d9f08. `kitchen.sh finish` reruns the Verify block at this head.

Acceptance, by test:
- rewrite plus `gc --prune=now` keeps a unit's step, finish and verification commits: test_evidence_commits_of_a_step_a_finish_and_a_verification_survive_a_rewrite_of_the_branch_and_gc (KitchenScriptTests).
- a fresh receiver with none of the old objects holds two successive bundles' evidence, also after its GC (and a remote only the sender has does not empty the bundle): test_evidence_of_two_successive_messages_reaches_a_fresh_receiver_and_survives_its_gc; a re-apply keeps nothing new; a bundle whose git part is gone raises and changes no ref: test_a_message_whose_evidence_cannot_be_imported_raises_and_keeps_nothing; malformed names are not imported: test_an_evidence_entry_whose_name_is_not_its_own_id_is_not_imported (BundleBranchApplyTests).
- land-check exits 2 with the missing-evidence line for (unit head gone, dispatch base gone, landing head gone) and keeps `has no clean verdict` for present heads (table-driven test); the printed `--covers` command run with the fake verifier gives `status: clean` and land-check then passes: test_the_recovery_command_land_check_prints_proves_the_lost_unit_again_and_clears_it.
- the existing rebased, revert and landed tests (L12427-12645, L12953-13040) pass unchanged: `-k land_check` 19 tests, 0 failed.

## Self-check

figures: counts are tails of the commands shown; gate results are the "step N recorded" lines.
acceptance: each line mapped to a test above; the revert runs show each fails without its fix.
callers: `git.pin`: 4 calls (step.py 2, verify_launch.py 1, bundle.keep_evidence 1). `import_git` / `move_git`: one caller each (apply). `unverified_line`: one caller (land_check); the `has no clean verdict` text is now built only there and asserted by the existing tests. `apply_git` was already gone (unit 031).
fresh: gates ran in this checkout with no cache; the -k runs are fresh processes.
gate: ruff format --check, ruff check, uv check --frozen, function-size guard, spec_gen --check pass at the head; L012 held.

## Deviations

- Step 1's first gate failed test_the_bundle_carries_the_sidekicks_commit_to_a_checkout_that_lacks_it: it fakes a master orb that never saw the sidekick's commit with a gc, and finish now pins the head in that shared repo. The test now also deletes the keep refs before the gc (it models an orb without them). I had put the change in step 2's commit; I amended it into step 1 and rebased steps 2 and 3 (the hashes above are after that), then reran step 1's gate.
- Steps 2 and 3 were written in a scratch worktree while earlier gates ran, then fast-forwarded and recorded in order; the worktree and branch are removed.
- Step 3 fills `<current-unit>` and `<live-base>` in the printed command when it can; the brief's text shows them as placeholders.
- A send with evidence now runs origin_baseline (a bounded `git fetch origin`, 30 s default) like a hand-over does; with no reachable origin the bundle is cut at a tracking ref or carries all history.
- The timebox of 120 minutes passed during the gate runs (about 15 minutes each, plus one failed run).

## Questions

none

## Follow-ups

- Keep refs are GC roots and nothing removes them (the advice's Risks: removal after close needs a retention policy).
- The git store's evidence durability (gitstore.py code refs are mutable) and a final-tip landing invariant stay out of scope, as the brief says.
- Each send now carries every keep ref of the run; a long run's bundles grow with its evidence (git dedupes objects; the baseline cut limits the rest).
