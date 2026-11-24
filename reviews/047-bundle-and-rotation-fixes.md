# Review 047: bundle-and-rotation-fixes

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/047-bundle-and-rotation-fixes.md
head: 973660250d7aa41552d66f9f0e837c38f6006699
engine: joo 2026-11-24T13-17-52-337Z-54331eaa

## Amendments

none

## Checked

- joo 2026-11-24T13-17-52-337Z-54331eaa (699837d8..97366025): 3 medium, 7 low. Verifier 047-v1 open; the fix (049) is verified with --covers 047.

## Blocking

- spawn.py:222 (medium, regression): rotation_required now stays set on a fresh generation until a dispatch, so role_is_idle reads the new READY sidekick as idle and the idle-roles sweep or the snooze listing can archive or snooze it.
- bundle.py:266 (medium, regression): almost every send carries keep refs, so almost every send and reply now runs a blocking origin fetch, and with origin down bundles the whole history.
- SKILL.md:277 (medium): the new text says every prompt fetches origin; only hand-overs and evidence sends do.
- bundle.py:515 (low, regression): a reply can no longer delete a file its role owns.

## Non-blocking (fixed in 049 too)

- bundle.py:558: the --unshallow fallback fetch is cut off at the 30 s sync timeout.
- test_pstack_core.py:20748: the sidekick subtest passes without the fix (verdicts/ is not sidekick-owned).
- rotate.py:62 / errors.py: RotateRefused's docstring names only the master.
- setup.py:248: permission 'none' forced even when the caller passed its own args.
