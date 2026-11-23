# Review 010: roster-handoff

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/010-roster-handoff.md
head: c40b7b0cd3989064a31295d45eb8cc880164f64e
engine: joo 2026-11-21T14-32-50-440Z-e4569ce0

## Amendments

none

## Checked

- joo 2026-11-21T14-32-50-440Z-e4569ce0 (84ea957f..c40b7b0c), copy at .amp/in/orb-backup/joo/010.md: one medium, three lows.
- verdicts/010-roster-handoff-v1.md: clean (Acceptance proven at c40b7b0c; no findings). It does not cover the git-store publish path, which the brief did not ask about. The fix (039) is verified with --covers 010.

## Blocking

- master.py:520 (joo medium, confirmed): under the git store, snapshot_roster writes sessions/master-g<n>-roster.toml into a path gitstore RULES publishes, so publish_master pushes the host roster to kitchen/<slug> on origin. br1anchen/open-pstack is PUBLIC, and AGENTS.md keeps the host roster on the host. This run is on the git store, so 010 would publish Brian's roster at its first rotation. Fix brief 039.

## Non-blocking (fixed in 039 too, all small)

- master.py:544: a snapshot written before publish_master/new_master is orphaned when either raises.
- master.py:563: the rollback unlink can raise and hide the launch error; use missing_ok.
- master.py:560: a dangling roster symlink gives a traceback in take-master instead of ToolFailed.

## Resolved

- The blocking finding (host roster published under the git store) is fixed by 039 (fc6d096b), verified clean with 010 in verdicts/039-roster-handoff-fix-v1.md. Lands only with 042.
