# Review 002: amp-idle-and-stop

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/002-amp-idle-and-stop.md
head: 6ac18e8e557c12a97f8ff88524d57b4e19630bcd
engine: joo 2026-11-20T17-31-45-056Z-4868ce51

## Amendments

none

## Checked

- Verdict verdicts/002-amp-idle-and-stop-v2.md: clean (v1 was inconclusive: the code was not on origin until the master published kitchen-code/kitchen-followups/master-g1).
- joo-dev --range 7fca271..6ac18e8 (stdin /dev/null), run 2026-11-20T17-31-45-056Z-4868ce51: 1 medium, 1 low.
- Report Deviations (relay_stop helper, mux/base.py attribute line outside Scope but named by the brief, owner-scoped trailing-line suppression): accepted.

## Blocking

- skills/pstack-pair/scripts/pstack_core/mux/amp.py:1210 in_turn_at: uses lastKnownAgentState updatedAt as the turn start, so a state change within the same turn after relay-done makes relay() relay the same steer or STOP twice into one turn.

## Follow-ups

- commands/stop.py:112 a relayed STOP's event row lacks the role (joo low); in brief 018.

## Next

Brief 018 fixes both, verified with --covers 002.
