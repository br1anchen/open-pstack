# Brief 039: roster-handoff-fix

playbook: review-fix
timebox: 90
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/039-roster-handoff-fix.md

## Goal

Under the git store (`PSTACK_STORE=git`) the roster snapshot that unit 010 added never reaches the published `kitchen/<slug>` branch: it stays a local-only store file that the old master hands to the next master with `upload_thread_file`, and take-master continues with a warning when it never arrives. Fix joo's three lows on the same code.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- anything else

## Steps

Start from unit 010's head: `git checkout -B kitchen-followups-010 c40b7b0cd3989064a31295d45eb8cc880164f64e` (it is on origin as kitchen-code/kitchen-followups/unit-010). Do not build on 037's rebased stack; 010 and this fix are rebased onto main together later.

1. `gitstore.py` RULES: add a LOCAL rule for the roster snapshots, `sessions/master-g<n>-roster.toml` (regex `sessions/master-g\d+-roster\.toml`, ignore line `sessions/*-roster.toml`), placed before the rule that gives `sessions/` to the master, so the publish path refuses or skips it and `gitignore_text()` lists it. The local store's bundle must still carry the file to the next master (local bundles are private attachments): if the local bundle uses the same LOCAL rules to leave files home, carry this file explicitly in the local-store hand-over and say how in the report. Tests: in tests/test_pstack_core.py beside the existing RULES tests, the snapshot path is local and `gitignore_text()` holds the ignore line; in tests/test_pstack_kitchen.py, a rotate-master under the git store with a host roster leaves the roster out of the branch the fake origin holds (assert on the pushed tree, not on the rule), and a rotate-master under the local store still delivers it (extend 010's existing absent-roster subtest). Targeted check: `python3 tests/run.py -k roster` and `python3 tests/run.py -k gitstore`.
2. `master.py` `hand_to_next_thread`: under the git store, after the next master thread starts, print `relay: upload_thread_file path=<abs snapshot> destination=<abs snapshot> thread=<next master thread>` and `relay: upload it before you end this turn; take-master on the next master waits for your turn to end`. Under the local store print nothing new. `restore_roster` in `take_master` runs after `wait_for_old_master` (move the call if it is before it now). When `.master.previous.roster` names a snapshot that is not on this orb after that wait: under the git store print `roster: <path> did not arrive from the previous master; this orb keeps its own roster` and continue (no failure, the hand-over completes); under the local store keep 010's behaviour. Test: one table over (git store, local store) x (snapshot present, missing) -> output line and whether the roster was installed and take-master exited 0. Targeted check: `python3 tests/run.py -k take_master`.
3. The lows, each with one assertion added to an existing 010 test where it fits: (a) the snapshot is removed when `publish_master` or `new_master` raises after `snapshot_roster` ran (write the snapshot inside the same `try` as the rollback, or widen it); (b) the rollback's unlink is `missing_ok=True` and cannot hide the launch error; (c) a dangling roster symlink, and an `OSError` from `target.parent.mkdir`, end take-master with `ToolFailed` and its message, no traceback. Targeted check: `python3 tests/run.py -k roster`.
4. Docs: SKILL.md's Amp threads bullet on master rotation and master.md's Rotate yourself section: the roster snapshot, that under the git store it is local-only and the old master uploads it with the printed relay line before ending its turn, and that take-master continues with its own roster when it does not arrive. Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Review: reviews/010-roster-handoff.md (verdict revise). joo run 2026-11-21T14-32-50-440Z-e4569ce0 on 84ea957f..c40b7b0c, artifact in the master's orb; findings quoted in the review note with file:line.
- br1anchen/open-pstack is public; AGENTS.md: host-specific settings (the roster) stay on the host. This run uses the git store.
- The relay-line pattern to copy: `steer`'s `relay: upload_thread_file path=... destination=... thread=...` lines (search `relay: upload_thread_file` under skills/pstack-pair/scripts/pstack_core).

## Acceptance

- Under the git store, a rotate-master with a host roster pushes no `sessions/*-roster.toml` to the store branch (the fake origin's tree), and the branch's .gitignore lists it.
- Under the git store rotate-master prints the upload relay line naming the next master thread; under the local store the roster still reaches the next master's orb as in 010.
- take-master under the git store with the snapshot missing prints `did not arrive` and exits 0 with the hand-over done; with it present it installs it as in 010.
- The three lows hold as step 3 says; 010's existing tests pass.
- Docs describe the git-store hand-over; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k roster
python3 tests/run.py -k take_master
python3 tests/run.py -k gitstore
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- The roster's contents never appear in output, events, decisions or the branch.
- No `time.sleep` in tests, and no elapsed-time asserts.
- No push to main and no PR.

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
