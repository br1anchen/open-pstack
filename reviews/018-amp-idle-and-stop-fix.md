# Review 018: amp-idle-and-stop-fix

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/018-amp-idle-and-stop-fix.md
head: 7616b0e559437724fd528d85e35da3cd5610c36c
engine: joo 2026-11-20T19-09-38-306Z-687c5088

## Amendments

none

## Checked

- Verdict verdicts/018-amp-idle-and-stop-fix-v1.md: clean, units 002 018 (--covers 002).
- joo-dev --range a9b9132..7616b0e (stdin /dev/null), run 2026-11-20T19-09-38-306Z-687c5088: no findings.
- Deviations (same-second turn end counts as ended, as turn_ended_since; one STOP across the two fallback rows): accepted.

## Blocking

none

## Follow-ups

none

## Next

Fixes review 002's blocking finding; 002 and 018 land with the run's stack.
