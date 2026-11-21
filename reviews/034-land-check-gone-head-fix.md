# Review 034: land-check-gone-head-fix

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/034-land-check-gone-head-fix.md
head: 70ed949c28672d61c83073bc0f16d2878ebe4e18
engine: joo 2026-11-21T11-58-20-622Z-7ce4977c

## Amendments

none

## Checked

- verdicts/034-land-check-gone-head-fix-v1.md (covers 006 and 034): reject on one finding. Every Acceptance line holds, but a missing evidence commit prints two lines, not one: step 4's new `git.pin` warn fires for the commit step 2 already reported. joo found the same as a low.
- Verifier notes folded into the fix: "none carries" should read "none can be shown to carry"; `unkept_evidence`'s fetch has no timeout or prompt guard. Others are accepted as the brief's chosen trade-offs.
- joo's other lows (dead fallbacks and guards, a silent missing packet, carries_change cost) go to followups.
- The verifier also hit the untangle bug (role stamps pair.json under the git store) with the pinned build. Unit 019 fixes it; it lands with the stack.
- Deviation accepted: two land-check tests were rewritten around the new carrier rule.

## Next

Fix brief 036 on top of 010, verified with `--covers 034`, which covers 006 too.

## Accepted

verdicts/036-land-check-gone-head-fix-v1.md is clean and covers this unit. Fixes 034 and 036 resolved the findings above.
