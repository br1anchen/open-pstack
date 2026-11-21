# Report 035: branch-ahead-not-diverged

status: done
brief: briefs/035-branch-ahead-not-diverged.md
playbook: bug-fix
branch: kitchen-followups
head: 875c8712c05f800972022bf1ccbadc2541c08b4e
tree: clean

## Changed

- 875c8712 step 1: in bundle.py move_checkout, the branch case checks `git merge-base --is-ancestor <sent head> refs/heads/<branch>` before calling `diverged`. When it holds, it returns one line, "checkout: kept <local>: <on> stays, and branch <Y> here is ahead of <sender> (<head short>)", with Y as the reported branch (apply_branches prints no second Y line) and no recipe. A real divergence keeps its line and recipe. tests/test_pstack_core.py, tests/SPEC.md.

## Ran

What was broken: receiver on X, its copy of the sender's branch Y one commit past the sent head: the sync said "diverged" and printed a kitchen-recovery recipe that would have reset Y onto the sender's older tip. Root cause: that branch of move_checkout asked only own_commits (Y holds commits the sent head lacks), which is also true for a branch that is simply ahead.

New table test test_the_senders_branch_here_is_reported_once_as_ahead_diverged_or_fast_forwarded_by_where_it_stands, receiver on X with its own commit:

```
ahead:    one line naming Y, says "ahead of m", no kitchen-recovery, Y stays at its own tip
diverged: one line naming Y, says "diverged from m", has the kitchen-recovery recipe, Y unchanged
behind:   "branch Y: fast-forwarded", Y at the sent tip
```

Scratch revert of bundle.py to the parent:

```
$ git checkout bundle.py; python3 tests/run.py -k as_ahead_diverged
FAIL ... [ahead]   1 tests, 1 failed
(fix restored)     1 tests, 0 failed
```

Only the ahead row fails without the fix; the diverged and behind rows pass either way, as they pin today's behavior. -k bundle: 30 tests, 0 failed; -k sync: 22 tests, 0 failed. Step recorded with "gates: pass (pstack,pstack-docs)". ruff format, ruff check, uv check --frozen and spec_gen --check clean before the commit. finish reruns the brief's Verify list.

## Self-check

figures: every number above is from the commands shown
scope: only bundle.py, tests/test_pstack_core.py, tests/SPEC.md changed
brief mentions test_pstack_kitchen.py in Scope; nothing there needed a change

## Deviations

- The behind row does not assert "reported once": today's behavior there is the checkout line plus "branch Y: fast-forwarded" (two lines), which the brief says stays as it is.
- The brief started from unit-034's head; the checkout was already there (70ed949c, matched origin's unit-034 ref).

## Questions

none

## Follow-ups

- The ahead line is the same when the checkout is detached ("detached HEAD stays, and branch Y here is ahead of ..."); not separately tested.
