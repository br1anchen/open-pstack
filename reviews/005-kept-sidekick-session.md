# Review 005: kept-sidekick-session

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/005-kept-sidekick-session.md
head: b1ca49f1426ba03ccb493f8b1756e9edd4959b7b
engine: joo 2026-11-20T22-24-14-302Z-c2f8a713

## Amendments

none

## Checked

- Verdict verdicts/005-kept-sidekick-session-v1.md: clean (Opus, escalated); fail-then-pass shown by running the head's tests against c5cf250 source.
- joo-dev --range c5cf250..b1ca49f (stdin /dev/null), run 2026-11-20T22-24-14-302Z-c2f8a713: 1 high, 1 medium, 4 lows (deduplicated).
- Deviations (Design 4 narrowed to non-waiting backends, steer s1; sync line suffixes): accepted.

## Blocking

- skills/pstack-pair/scripts/pstack_core/bundle.py:379 merge_store protects only master-owned paths; a stale reply still deletes or rolls back files another role owns (reports/NNN, steps/NNN.tsv) when the base is newer than the replying role's snapshot.
- tests/test_pstack_kitchen.py:16255-16268 the two-orb regression sets up reports/002 and steps/002 in the newer base but never asserts they survive.

## Follow-ups

- rotate.py:193 / mux/amp.py:722 compacted() and context_used() read the context twice (two subprocesses, two observations) against the docstring.
- rotate.py:53 a manual `kitchen.sh rotate` records no reason; SKILL.md:437 says every rotation does.
- rotate.py:203 keeps_session's unused reasons=None branch.
- SKILL.md:290 "only the shared logs take the union" overstates; role-owned .tsv such as steps/ still union.

## Next

Brief 025, widened to these findings, verified with --covers 005.
