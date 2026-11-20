# Review 017: swallowed-failures-fix

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/017-swallowed-failures-fix.md
head: a9b9132645dcfbed28d4d68f7c936ec1e996a27b
engine: joo 2026-11-20T18-24-57-011Z-80094552

## Amendments

none

## Checked

- Verdict verdicts/017-swallowed-failures-fix-v1.md: clean, units 001 017 (--covers 001).
- joo-dev --range ff65476..a9b9132 (stdin /dev/null), run 2026-11-20T18-24-57-011Z-80094552: 2 lows on gitstore.py's stale-origin/HEAD fallback, neither blocking.
- Deviations (two named tests instead of one table; renamed 001's fetch_code test): accepted.

## Blocking

none

## Follow-ups

- gitstore.py:1197 the fallback leaves the stale local origin/HEAD in place, so every later fetch_code pays a failed fetch plus an ls-remote (set it with `git remote set-head origin <branch>` once resolved).
- gitstore.py:1196 the fallback runs after any failed fetch, including a transient error on a trunk that still exists, and may then fetch a different trunk; fall back only when ls-remote shows the local trunk is gone.

## Next

Lands with the run's stack; 001 is covered by this verdict.
