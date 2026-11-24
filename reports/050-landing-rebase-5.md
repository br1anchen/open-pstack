# Report 050: landing-rebase-5

status: done
brief: briefs/050-landing-rebase-5.md
playbook: bug-fix
branch: kitchen-followups-landing-5
head: 29cc53c2e8a65114086f9ec00042f5dd64d4b469
tree: clean

## Changed

Branch `kitchen-followups-landing-5` is origin/main (3b951444) plus the 8 commits of 699837d8..fa6cd2a6, none twice (`git log --format=%s origin/main..HEAD | sort | uniq -d` prints nothing; `git rev-list --count origin/main..HEAD` is 8):

Unit 040: e143a155 (start prints nest instructions), c00cad62 (sweep keeps a nest-pending thread; nest lists archived ones), 1a74dee3 (take-master nests the whole run), 47edd469 (docs).
Unit 046: b3a95126 (stop and close refuse on a snoozed role thread), 1842e862 (dispatch refuses a snoozed sidekick before classify, unless it rotates), 267e408c (snooze --done refusals, SnoozeRefused), 29cc53c2 (docs).

No step 2 commits: the full suite found nothing to fix.

## Conflicts

- tests/SPEC.md: conflicted in 7 of the 8 picks (every one but the dispatch commit's own, which also had dispatch.py). Each time I took one side, ran `python3 tests/spec_gen.py` and added the result. `spec_gen.py --check` at the head: ok, 0 missing.
- skills/pstack-kitchen/scripts/kitchen_core/dispatch.py (1842e862, 046): main moved the escalated-with-no-plan warning out of `dispatch()` into `warn_unplanned_escalation(brief)` (imports `PLAN_PLAYBOOKS` from pstack_core.gates, `header_field` and `risk_of` from store); 046 added `keeps_session_for`, `rotates_before_sending` and `require_sidekick_awake` (imports mux, amp, snooze, tempfile, Path, session_decision, scope_entries, kpy and more store names) and calls the last one before `rotate_gate`. Kept both: one merged import list (`PLAN_PLAYBOOKS`, `header_field` plus 046's store names, sorted), both helper groups side by side, `dispatch()` calls `require_sidekick_awake` first, then `rotate_gate`, and `warn_unplanned_escalation(brief)` after `classify_brief`. ruff format, ruff check and uv check pass on it.
- skills/pstack-kitchen/references/master.md, tests/test_pstack_core.py, tests/test_pstack_kitchen.py, tests/test_pstack_pair.py: the brief expected conflicts; git merged them without any (the picks went in cleanly), and the suite passes.

## Ran

```
$ python3 tests/run.py -k dispatch   -> 79 tests, 0 failed
$ python3 scripts/check-pstack.py    -> 65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check  -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check / ruff check / uv check --frozen -> pass
$ kitchen.sh step <store> 29cc53c2e ... (runs the full suite) -> gates: pass (pstack,pstack-docs); step 1 recorded at 29cc53c2e
```

## Self-check

figures: counts come from the commands shown
review.self: none named by the kitchen; the diff is cherry-picks plus one conflict resolution
scope: no push, no PR; origin/main untouched

## Deviations

none

## Questions

none

## Follow-ups

none
