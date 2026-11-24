# Report 053: landing-rebase-6

status: done
brief: briefs/053-landing-rebase-6.md
playbook: bug-fix
branch: kitchen-followups-landing-6
head: d7f3a4c99de4f15ae162744ae278faa8fe7b3cfc
tree: clean

## Changed

Branch `kitchen-followups-landing-6` is origin/main (29cc53c2, which holds the rebased units 040 and 046) plus the 21 commits of 699837d85..f117480b (units 047, 049, 051, 052), none twice (`git log --format=%s origin/main..HEAD | sort | uniq -d` prints nothing; `git rev-list --count origin/main..HEAD` is 21). No step 2 commits: the full suite found nothing to fix. The new shas, oldest first:

- db17c937 fix(skills/pstack-pair): a reply never deletes a file this side has that the reply merely lacks
- a74aa886 fix(skills/pstack-pair): a bundle packed while origin is unreachable carries every commit; every origin fetch of a sync is bounded and prom
- d92eb5ab fix(skills/pstack-pair): a send that carries evidence is cut like a hand-over, so local-only evidence applies at a receiver that lacks the 
- 538cf3a8 fix(skills/pstack-pair): the rotation flag is cleared by the new generation's READY, and a replacement that never sent READY can be rotated
- 320c5c6f refactor(skills/pstack-pair): rotate's state checks move to require_rotatable, under the function size limit
- 8c1b8ba1 fix(skills/pstack-pair): require_rotatable refuses with RotateRefused, as a helper may
- b22793ef fix(skills/pstack-kitchen): spawn and consultant on an orb with no host roster start the run's recorded entry
- 7f8c992c docs(skills/pstack-kitchen): READY clears the rotation flag, an unready bootstrap rotates again, the bundle cut when origin is down, replie
- b940050a fix(skills/pstack-pair): a sidekick generation not yet dispatched to is in use, not idle, even with the rotation flag set
- ccc9b35c test(tests): the snooze tests' dispatched unit names the sidekick's generation, as a dispatch records it
- dd514aa8 fix(skills/pstack-pair): only a master's hand-over fetches origin; an evidence send is cut at the tracking ref and every other send at the 
- 88bc773b fix(skills/pstack-pair): a reply deletes a file of its own role that this side still has as the base had it, and keeps one this side wrote 
- fe37618b fix(skills/pstack-pair): the unshallow fallback fetch has PSTACK_BUNDLE_UNSHALLOW_TIMEOUT_S of its own, 600 seconds
- e872ba3c fix(skills/pstack-kitchen): spawn forces permission none only for the run's recorded start arguments, not for a caller's own; RotateRefused
- 2e716a3d docs(skills/pstack-kitchen): the bundle cut by kind of send, the receiver's bounded fetch, and a reply's deletion of its own role's file
- f1bdf5a3 fix(skills/pstack-pair): a reply is merged against the bundle it synced, kept by id, and deletes nothing when that bundle is not kept
- 512d17bd refactor(skills/pstack-pair): role_is_idle reads send.took_a_brief for the generation check; the archive table gains a store that records n
- cf4c6f8c docs(skills/pstack-kitchen): a reply is merged against the bundle it synced, and one whose base is gone deletes nothing
- 3a1ee425 fix(skills/pstack-pair): a settled relay bundle is kept by id like a sent one, an unknown reply base stops deletions only, and the kept-bun
- 1370ba9f fix(skills/pstack-pair): role_is_idle reports a sidekick with a damaged generation field in use instead of stopping the sweep; the archive 
- d7f3a4c9 docs(skills/pstack-kitchen): a reply changes only its own role's files by the ownership rules and is merged against the bundle it synced, s

## Conflicts

- tests/SPEC.md: the only conflicting file, in 20 of the 21 picks. Each time I took one side, ran `python3 tests/spec_gen.py` and added the result. `spec_gen.py --check` at the head: ok, 0 missing.
- skills/pstack-pair/scripts/pstack_core/mux/amp.py, kitchen_core/dispatch.py, pstack-kitchen/SKILL.md, references/master.md, tests/test_pstack_core.py, test_pstack_kitchen.py, test_pstack_pair.py: the brief expected conflicts; git merged them all without any (046's snooze checks and nest-on-start sit in other hunks than 047-052's changes to `role_is_idle`, `took_a_brief`, `keep_sent_bundle` and `keep_bundle_by_id`). Both sides' behaviour is therefore as each unit wrote it. No conflict markers in any file I touched (test_pstack_upstream.py holds marker strings that origin/main already has).

## Ran

```
$ git fetch origin && git checkout -B kitchen-followups-landing-6 origin/main; git cherry-pick 699837d85..f117480b8
$ python3 tests/run.py -k bundle  -> 31 tests, 0 failed
$ python3 tests/run.py -k relay   -> 13 tests, 0 failed
$ python3 tests/run.py -k snooze  -> 10 tests, 0 failed
$ python3 tests/run.py -k archive -> 26 tests, 0 failed
$ python3 scripts/check-pstack.py -> 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check / ruff check / uv check --frozen -> pass
$ kitchen.sh step <store> d7f3a4c99 (full suite) -> gates: pass (pstack,pstack-docs); step 1 recorded at d7f3a4c99
```

## Self-check

figures: counts come from the commands shown
review.self: none named by the kitchen; the diff is cherry-picks plus regenerated SPEC.md
scope: no push, no PR; origin/main untouched

## Deviations

none

## Questions

none

## Follow-ups

none
