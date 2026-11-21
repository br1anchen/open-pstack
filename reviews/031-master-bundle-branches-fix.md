# Review 031: master-bundle-branches-fix

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/031-master-bundle-branches-fix.md
head: 6dcc8e38a288ade2e10d725d007d811dbc7f201d
engine: joo 2026-11-21T07-02-41-670Z-3f3b8542

## Amendments

none

## Checked

- verdicts/031-master-bundle-branches-fix-v1.md (covers 004 and 031): clean. All five of 004's medium findings are fixed with tests. The verifier's observations are leftover import refs after a rejected hand-over, orphaned transports on timeout, legacy id ordering, and one source-only commit.
- joo, blocking (medium): pre-031 uuid bundle ids sort above the new clock-prefixed ids, so after an upgrade pruning deletes the newest sets. Low, blocking: pruning can drop a ref that a printed recipe names, which brief 031 forbade; a detached sender still gets a reset recipe; the diverged line misnames the branch; the current branch can be reported twice; settings are validated late; a timeout of 0 means an instant timeout.
- joo, not blocking: `env` replacing the environment is safe today because the one caller merges os.environ (docstring only, folded in). Cutting at a stale tracking ref on fetch failure was my brief's design; the receiver fetches origin itself. SIGKILL locks and receiving-side timeouts go to follow-ups.
- Deviations accepted: steps were prepared in a scratch worktree but gated and recorded at their own commits; an extra commit for the size guard; the timebox overran on 13-minute gates.

## Next

Fix brief 032 on top of 022, verified with `--covers 031`. 004 and 031 are accepted on 032's clean verdict.
