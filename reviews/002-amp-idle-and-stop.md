# Review 002: amp-idle-and-stop

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/002-amp-idle-and-stop.md
head: 6ac18e8e557c12a97f8ff88524d57b4e19630bcd
engine: joo 2026-11-20T17-31-45-056Z-4868ce51

## Amendments

none

## Checked

- Verdict verdicts/002-amp-idle-and-stop-v2.md: clean (v1 was inconclusive: the code was not on origin until the master published kitchen-code/kitchen-followups/master-g1).
- joo-dev --range 7fca271..6ac18e8 (stdin /dev/null), run 2026-11-20T17-31-45-056Z-4868ce51: 1 medium, 1 low.
- Audit (audit: yes): whole-diff read by a read-only subagent; no blocking; 4 lows (stop relay pending-again and fallback untested, pair's own no-thread path untested, SKILL.md silent and relay messages say steer for a STOP).
- Report Deviations (relay_stop helper, mux/base.py attribute line outside Scope but named by the brief, owner-scoped trailing-line suppression): accepted.

## Blocking

- skills/pstack-pair/scripts/pstack_core/mux/amp.py:1210 in_turn_at: uses lastKnownAgentState updatedAt as the turn start, so a state change within the same turn after relay-done makes relay() relay the same steer or STOP twice into one turn.

## Follow-ups

- commands/stop.py:112 a relayed STOP's event row lacks the role (joo low); in brief 018.
- Audit lows 1, 3, 4 added to brief 018; low 2 (pair stop no-thread path untested) goes to followups.md.

## Next

Brief 018 fixed both (review 018, verdict 018-v1 clean covering 002); the blocking finding is resolved there, so 002 is accepted and lands with 018.
