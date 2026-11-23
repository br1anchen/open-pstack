# Review 038: snooze-role-threads

verdict: revise
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/038-snooze-role-threads.md
head: bee0dc78bf5e357ee0c008b70ba54e1c35ad6a1b
engine: joo 2026-11-23T20-50-50-476Z-d1e0c6db

## Amendments

none

## Checked

- joo 2026-11-23T20-50-50-476Z-d1e0c6db (daf51dc0..bee0dc78): 1 high, 1 medium, 3 lows. verdicts/038-snooze-role-threads-v1.md: reject, one finding, the same half-sent plan round as joo's amp.py:1192 medium; everything else in Acceptance held. Fix 043 step 2 covers it; 043 is verified with --covers 038.

## Blocking

- snooze.py:168 (high, confirmed by reading gitstore RULES): snooze.tsv has no ownership class, so under the git store (this run's backend) the first `snooze --done` makes every later master publish fail with OwnershipError. Fix brief 043.
- amp.py:1192 (medium): discuss sends the plan to an awake sidekick, then refuses on a snoozed consultant, leaving a half-sent round. Fix brief 043.

## Non-blocking (fixed in 043)

- snooze.py:128: --done accepts a master or a working thread.
- snooze.py:105: status exports each role thread up to twice.
- master.md:99: PSTACK_SNOOZE_ROLES=0 stops new snoozing only; a snoozed thread still needs --wake.
