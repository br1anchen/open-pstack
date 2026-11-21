# Review 026: dispatch-after-ready-sends-fix

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/026-dispatch-after-ready-sends-fix.md
head: b6b68a410bae7153de115c72b752b1226b6331aa
engine: joo 2026-11-21T00-48-55-529Z-3a36ff37

## Amendments

none

## Checked

- Verdict verdicts/026-dispatch-after-ready-sends-fix-v1.md (covers 023 and 026): reject, on one finding. `test_every_rotation_under_amp_says_why...` takes 17 to 24 s, against the 15 s limit. That test was as slow before 023, so this is the master's over-broad Acceptance line, not a regression. Everything else holds for both units: the READY-wait test went from 63.0 s to 1.4 s, and the new refusal test fails when `took_a_brief` is forced to False.
- joo on 4bca786..b6b68a4: the caller-reality and correctness lanes found nothing. The run is copied to .amp/in/orb-backup/joo/026-run.
- Deviation 2 is accepted: fewer than 3 exports, because a refusing dispatch reads status twice.

## Next

Fix brief 028 splits the slow test into one process per case. It is verified with `--covers 026`, which covers 023 too. 023 and 026 are accepted on a clean verdict.
