# Steer 034-s1: land-check-gone-head-fix

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/034-land-check-gone-head-fix.md
supersedes: none
kind: info
scope effect: none

## Direction

Continue the brief as written: your last turn ended while step 1\'s gate still ran (c3bce56d not recorded). Run `kitchen.sh step` for c3bce56d again in the foreground until it records, then do steps 2 to 4 in the same turn. Never end a turn on a running gate; the brief says to rerun at once.

## Keep

All of it: step 1 (c3bce56d) stands.

## Resolved

none

## Ack

Read this the moment it reaches you, between tool calls, not at the end of
the step. Agree: append
`kitchen.sh progress /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups "steer s1 applied: <what changed>"` and continue
the brief. Object: write `/home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/034-land-check-gone-head-fix-s1.md` from the
steer response template, run `kitchen.sh finish`, and end the turn; the master's answer arrives
as the next STEER. List this steer under Deviations in the final report.
