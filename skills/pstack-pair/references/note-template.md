# Note {{UNIT}} n{{K}}

unit: {{UNIT}}
through: step {{K}}
range: {{RANGE}}
status: {{blocking | clear}}
scope effect: none

<Leave `scope effect: none` unless this note authorizes an edit outside the
unit's Scope: `may write: <path or glob>`, or several as `may write "<a>" and
"<b>"`. The check-in then counts those paths as Scope, so they are no longer
reported outside scope. A narrowing (`must not write`) goes through a steer.>

<Review the range with `git diff {{RANGE}}`, or `git show` per step. Blocking
is only what would make the unit wrong to land: wrong behaviour, a missed
consumer of a changed value, data or schema safety, a rollback hazard, a
broken contract, or a test that does not test its claim. Everything else is a
follow-up. `status: clear` with Blocking "- none" when nothing blocks.>

## Blocking

- {{`file:line`, what is wrong, why it blocks landing, the expected behaviour; or none}}

## Follow-ups

- {{worth doing but not worth blocking this unit: naming, docs wording, one-owner cleanups, extra tests; or none. pair.sh note copies these to followups.md}}
