# Report 048: landing-rebase-4

status: done
brief: briefs/048-landing-rebase-4.md
playbook: bug-fix
branch: kitchen-followups-landing-4
head: fa6cd2a6f85c8329ad5dd52da01e64352daeb2dc
tree: clean

## Changed

Branch `kitchen-followups-landing-4` is origin/main (699837d8) plus 8 commits, none twice (`git log --format=%s origin/main..HEAD | sort | uniq -d` prints nothing):

Unit 040 (eb8228f9..6b2551f9):
- 5d054e1e a start under Amp threads prints the nest instructions of the thread it started
- 3b74f174 the sweep keeps a thread whose nest row is pending; nest lists an archived one with the unarchive, parent, re-archive calls
- 942fe43f take-master nests every thread of the run under the new anchor
- e4235601 docs: starts print the nest instructions; pending row keeps a thread from the sweep; take-master nests the whole run; --done does not check the parent

Unit 046 (699837d8..11d2874b):
- f46f7c86 stop and close refuse when a role thread is snoozed
- c8d46d48 dispatch refuses a snoozed sidekick before classify, unless it rotates
- ec82fa34 snooze --done refusals name the update_thread undo and raise SnoozeRefused
- fa6cd2a6 docs: help lists snooze; every send to a snoozed thread refuses

No step 2 fix commits: the full suite found nothing to fix.

## Conflicts

- tests/SPEC.md: conflicted in 7 of the 8 picks (040's first three commits and all four of 046's). Each time I took one side, ran `python3 tests/spec_gen.py`, and added the result. `spec_gen.py --check` at the head: "tests/SPEC.md is current, 0 missing", 438 lines.
- skills/pstack-kitchen/references/master.md (040's docs commit e4235601): main's side had the nest sentence followed by the snooze-due paragraph (046 text); 040's side rewrote the nest sentence (starts print the instruction; archived rows need unarchive/parent/re-archive; --done cannot check the parent; a pending row keeps a thread from the sweep). Kept both: 040's nest sentence, then main's snooze-due paragraph unchanged, with "Under Amp threads a kept sidekick session" still following.
- mux/amp.py and the tests' helpers (`nest_all`, last-line reads, `pending_to`, `require_awake`, `exports_cached`): git merged them with no conflict, and the suite passes, so no test needed the step 2 treatment.

## Ran

```
$ git fetch origin && git checkout -B kitchen-followups-landing-4 origin/main; two git cherry-pick ranges as written in the brief
$ python3 tests/run.py
2051 tests, 0 failed, 532s wall with 4 jobs
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
$ python3 scripts/check-pstack.py   -> 65 skills checked; all 48 previous skills covered
$ uv lock --check                   -> Resolved 3 packages
$ uv run --frozen ruff format --check -> 112 files already formatted
$ uv run --frozen ruff check        -> All checks passed!
$ uv check --frozen                 -> All checks passed!
$ kitchen.sh step <store> fa6cd2a6f ...
gates: pass (pstack,pstack-docs)
step 1 recorded at fa6cd2a6f
```

## Self-check

figures: every number above comes from the command shown with it
review.self: the kitchen names none (only [review] standards and the joo engine, which is the master's step); this diff holds only cherry-picked commits and conflict resolutions, no new code
lessons: L012 held; no Python written, uv check --frozen passes
scope: no push, no PR; origin/main untouched

## Deviations

none

## Questions

none

## Follow-ups

none
