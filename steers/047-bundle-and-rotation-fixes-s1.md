# Steer 047-s1: bundle-and-rotation-fixes

brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/047-bundle-and-rotation-fixes.md
supersedes: none
kind: redirect
scope effect: none

## Direction

Take your first option: `require_rotatable` raises `RotateRefused` (imported from errors.py, which stays unedited) in place of both `Die(..., 5)`. Run the EndsItself and FunctionSize guards, commit, and record step 4 at that head. Then do steps 5 and 6 as the brief says, recording each, and finish done.

## Keep

Steps 1-3 as recorded (3e3b1df3, dabf4638, 42f676e9) and step 4's two commits (1e15329c, 42b6e081).

## Resolved

none

## Ack

Read this the moment it reaches you, between tool calls, not at the end of
the step. Agree: append
`kitchen.sh progress /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups "steer s1 applied: <what changed>"` and continue
the brief. Object: write `/home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/047-bundle-and-rotation-fixes-s1.md` from the
steer response template, run `kitchen.sh finish`, and end the turn; the master's answer arrives
as the next STEER. List this steer under Deviations in the final report.
