# Review 049: bundle-and-rotation-fixes-2

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/049-bundle-and-rotation-fixes-2.md
head: 648f31bfbc25a86d5a5b5c5106ce4d56e60778b0
engine: joo 2026-11-24T15-21-10-554Z-4a5793fb

## Amendments

none

## Checked

- joo 2026-11-24T15-21-10-554Z-4a5793fb (97366025..648f31bf): 1 medium, 1 low. verdicts/049-bundle-and-rotation-fixes-2-v1.md: reject, one finding, the same late-reply deletion (047's verifier subtest fails at 648f31bf); the fix (051) is verified with --covers 047 049.

## Blocking

- bundle.py:527 (medium, regression of 047's step 1): the base is the bundle the master last sent the role, which can be newer than the reply's snapshot. A file the master wrote after the role's snapshot (a gates-mode verdict) and put in that newer base has local == base, so the late reply deletes it again. The local-vs-base test cannot tell "the role deleted it" from "the role never had it". Fix brief 051: the reply names the bundle it last synced.

## Non-blocking (fixed in 051)

- amp.py:549: reuse send.took_a_brief instead of a second str() generation comparison.
