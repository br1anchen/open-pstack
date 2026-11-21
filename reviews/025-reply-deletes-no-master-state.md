# Review 025: reply-deletes-no-master-state

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/025-reply-deletes-no-master-state.md
head: 6a83f500bc5d5ed07276c49035859a9b8322e879
engine: joo 2026-11-21T04-01-13-643Z-e86be53c

## Amendments

none

## Checked

- Verdict verdicts/025-reply-deletes-no-master-state-v1.md (Opus, covers 005 and 025): reject, on one finding. The Acceptance said "a routine Amp dispatch runs `amp threads context` once". The decision itself reads it once, but the message path reads it twice more (3 calls on the sidekick's thread, down from 4). I meant once per dispatch, so this goes to a fix.
- Every other Acceptance line of 005 and 025 holds. Stale replies no longer delete or roll back other roles' files, steers or `.seen` markers.
- joo found two lows, both the same point: the sync line calls another role's kept file "the master's copy". This is folded into the fix.
- Deviations accepted. A manual rotate's reason is recorded without changing the Herdr goldens. A master-owned file the master lacks is still not added, which was the behaviour before too.

## Next

Fix brief 030 (escalated, plan 021). Verify it with `--covers 025`, which also covers 005.
