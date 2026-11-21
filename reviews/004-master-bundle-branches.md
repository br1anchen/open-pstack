# Review 004: master-bundle-branches

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/004-master-bundle-branches.md
head: 550a7d760223b1e44d1ba85bbd0ba2203be9f353
engine: joo 2026-11-21T05-01-52-966Z-2c2b1e65

## Amendments

none

## Checked

- Verdict verdicts/004-master-bundle-branches-v1.md: clean, no findings. Its observations (unpruned per-bundle refs, store merged before a failed import raises) match joo's.
- Deviations accepted: per-bundle side refs for every bundle, strictness only for hand-overs, baseline from a fresh fetch of origin's default branch.
- joo, blocking (medium): a failed hand-over leaves a half-applied checkout, branches and store; the sender's working branch is dropped when the checkout is kept; the diverged recipe can reset the receiver's branch X onto the sender's branch Y; a failed fetch makes the hand-over bundle carry the whole history.
- joo, folded in (low): no timeout or prompt guard on the hand-over fetch; per-bundle refs never pruned; old-head resolution should use refs/heads/<branch>.
- joo notes left as follow-ups: move_checkout could pass "is checked out" instead of asking git again; `exclude` could be a required argument.

## Next

Fix brief 031 on top of 030, verified with `--covers 004`. 004 is accepted on 031's clean verdict.

## Accepted

verdicts/033-master-bundle-branches-fix-v1.md is clean and covers this unit. Fixes 031, 032 and 033 resolved the findings above.
