# Brief 017: swallowed-failures-fix

playbook: review-fix
timebox: 45
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/017-swallowed-failures-fix.md

## Goal

Fix the two joo findings on unit 001: a sidekick rotation whose spawn returns 3 or 4 keeps that exit code when the final publish fails, and `fetch_code` does not report a failed origin read when only a stale local `origin/HEAD` names a branch origin no longer has.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **A returned spawn failure is not hidden by a failing publish** (joo 001, rotate.py:123, low). In `rotate` the flag `spawned` is set True whenever `spawn_role`/`fail_back` returns, so a returned 3 (bootstrap did not settle) or 4 (no ready file) followed by a failing `publish_role` raises the publish error and the master loses the 3 or 4. Treat a returned non-zero code like a raised spawn failure in `publish_rotation`: warn `the rotation could not be published: <error>` and return the spawn's code. Keep a return of 0 followed by a failing publish raising, as now. Extend unit 001's rollback test (the one for the rotation's spawn and publish) into a table: spawn raises, spawn returns 4, spawn returns 0, each with a failing publish; expected stderr and exit code per row. Commit, `kitchen.sh step`.
2. **A stale origin/HEAD is not an unreadable origin** (joo 001, gitstore.py:1191, low). `_fetch_trunk` asks origin with `ls-remote --symref origin HEAD` only when there is no local `refs/remotes/origin/HEAD`; with a stale one (a renamed or deleted default branch) the fetch fails and `fetch_code` now returns False although origin is readable. When the fetch of the locally named trunk fails, resolve origin's HEAD with `ls-remote` and fetch that; return '' only when origin itself cannot be read or names no HEAD. Update unit 001's fetch_code test, which today sets origin/HEAD to `gone` and asserts the warning: that row must now return True with no warning, and a row with origin unreachable for the trunk (while the code ref still fetches, as 001's first version did) keeps asserting False and the warning. Commit, step.

## Context

- Unit 001's brief and report: `briefs/001-swallowed-failures.md`, `reports/001-swallowed-failures.md` (head 7fca271).
- joo run: `.amp/in/orb-backup/joo/001-run/executions/*.result.v1.json` in the master's orb; findings quoted above.

## Acceptance

- A rotation whose spawn returns 4 and whose final publish fails exits 4 and prints `the rotation could not be published`; one whose spawn returns 0 and publish fails still raises the publish error.
- With a stale local `origin/HEAD` and a readable origin whose HEAD names an existing branch, `fetch_code` returns True and sync prints no warning; with the trunk unfetchable from origin it returns False and sync prints the warning.
- Unit 001's Acceptance still holds at this head.

## Verify

```bash
python3 tests/run.py -k fetch_code
python3 tests/run.py -k rollback
python3 tests/run.py -k rotation
```

## Forbidden

- Do not change the text of `gitstore.UNKNOWN_FRESHNESS` or `commands/sync.py`.

## Playbook: review-fix (your parts)

You own the working tree.

Your parts:
- review-fix.intro-1: **Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdi
- review-fix.step-1: 1. Read each finding, the code it names, and the parent unit's brief and report. A finding you canno
- review-fix.step-2: Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the 
- review-fix.step-3: 3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks 
- review-fix.reply: In the report, under Ran and the sections the template names: each finding with its commit, the test

### review-fix.intro-1

**Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdict or a landing review already found each one; the unit that raised them already settled the design.

### review-fix.step-1

1. Read each finding, the code it names, and the parent unit's brief and report. A finding you cannot place, or one whose fix would change the parent's design, goes in the report under Questions; do not redesign around it.

### review-fix.step-2

Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the test that pins it, in the same commit, recorded with kitchen.sh step. A finding with no testable behaviour (a comment, a name, a log line) says so in its commit message.

### review-fix.step-3

3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks for a failing test committed before its fix.

### review-fix.reply

In the report, under Ran and the sections the template names: each finding with its commit, the test that pins it, and the command output that shows it fixed. A finding left open says why. Every claim carries its command output or a file pointer.

## Report

Work the Steps in order. End each in a commit and record it with
`kitchen.sh step /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <sha> "<what it does>"`: it runs the touched
profiles' fast gates and the repo's policy check, and records the step only
when they pass. When it exits 4 with `gates: still running`, the step is not
recorded: run the same command again at once, in the foreground, and keep
going until it records; never end your turn on it. On a failure, fix it in a
new commit and run `kitchen.sh step` again with that commit; it tells you when
two failures in a row make this a blocked report. At each step boundary run `kitchen.sh notes /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups` and fix
any open blocking note first, as a fixup commit recorded with `--resolves`.
Append one progress line per change of approach with
`kitchen.sh progress /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups "<line>"`. When any `kitchen.sh` command prints
`PAUSE:`, stop at that safe point: commit what is verified and report
`partial` with the next step. Before reporting done, run the review skills the
kitchen's `review.self` names on your diff. Write the report file named above
using the pstack-pair report template, then run
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh finish /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <report path>`
and do what it prints. For a done report, finish first reruns the Verify
commands above at the report's head:, with the checkout at that commit and
nothing uncommitted, and sends the report only when every one exits 0; exit 4
means they are still running, so run the same finish again at once.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
