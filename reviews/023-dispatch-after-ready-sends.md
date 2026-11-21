# Review 023: dispatch-after-ready-sends

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/023-dispatch-after-ready-sends.md
head: 4bca7862ab6374879d0d0bc6d946448118446d36
engine: joo 2026-11-20T22-53-58-076Z-b7d407ef

## Amendments

none

## Checked

- Verdict verdicts/023-dispatch-after-ready-sends-v1.md: reject. The Acceptance holds: the probe fails at b1ca49f and passes at 4bca786. But an existing test now sleeps about 60 s for real.
- joo-dev (stdin /dev/null): 2 lows. took_a_brief misfires on a discuss PLAN (moved to followups.md), and the refuse-at-once branch is untested (fixed in 026).

## Blocking

- Tests inherit PAIR_ROTATE_READY_S=60 and sleep for real (verdict finding 1). Fixed in brief 026.

## Follow-ups

- send.py:181 took_a_brief keys on a brief, not on the READY turn (followups.md).

## Accepted

verdicts/028-dispatch-after-ready-sends-fix-v2.md is clean and covers this unit. Fix 028 resolved the one finding of verdict 026-v1.
