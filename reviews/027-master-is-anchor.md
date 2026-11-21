# Review 027: master-is-anchor

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/027-master-is-anchor.md
head: a0eb1b71ea08de94621e9df7936996335f3ffdf3
engine: joo 2026-11-21T01-40-33-374Z-5da3f1dd

## Amendments

none

## Checked

- Verdict verdicts/027-master-is-anchor-v1.md: clean, no findings.
- Deviations accepted. The old anchor test was replaced because its rule changed. `pending()` now takes the newest pending row per thread, so a live role can re-nest; joo's row-order note on it can't occur, because `add_pending` refuses a second row for the same parent. `take_master` was split into helpers. Steps 3 and 4 were recorded as one step.
- joo (quick-correctness, low, all of them blocking for the PR):
  - `anchor_title` uses `setdefault`, which keeps the `''` from init, so the title is never saved.
  - `take_anchor` can append an empty `old` to `.anchor.previous`.
  - The master branch of `record_started` lost its `pair.json` check.
  - The `anchor:` line claims the title even when the rename failed.

## Next

Fix brief 029 on branch master-is-anchor. It gets its own verifier, with `--covers 027`. After a clean verdict, the master pushes the branch and opens the PR, then replies to T-01a1ea49.
