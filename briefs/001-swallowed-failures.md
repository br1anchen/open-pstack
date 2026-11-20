# Brief 001: swallowed-failures

playbook: review-fix
timebox: 75
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/001-swallowed-failures.md

## Goal

Three places that swallow a failure report it instead: `gitstore.fetch_code` returns False when the trunk fetch fails, the master hand-over prints the unknown-freshness warning and keeps the original launch or spawn error when a rollback publish also fails, and `tests/run.py -k` exits non-zero when a test module fails to import.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- tests/run.py
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **fetch_code reports a failed trunk fetch** (item 8). In `skills/pstack-pair/scripts/pstack_core/gitstore.py`, `fetch_code` (about L1182-1207) calls `_fetch_trunk(store)` (L1165-1179) and discards its result; `_fetch_trunk` returns `''` on failure. Set `ok = False` when it returns `''`, so the docstring's "False when origin could not be read" holds. The caller, `commands/sync.py` L94-100, already prints `warning: origin or a published bundle could not be read; ...` on False; leave sync.py alone. Add one test in `tests/test_pstack_kitchen.py` beside the helper `master_fetches_the_announced_code` (about L17449) in the AmpGit tests: make origin's trunk fetch fail while the code ref fetch still succeeds (for example rename or delete origin's default branch in the fixture's bare origin, or point `_fetch_trunk`'s ref at a missing one through the fixture), then assert `gitstore.fetch_code(...)` is False and `pair.sh sync --branch ...` prints the warning line. Commit, `kitchen.sh step`.
2. **The master hand-over says when freshness is unknown** (item 9a). In `skills/pstack-kitchen/scripts/kitchen_core/master.py`, `hand_to_next_thread` (call at about L487) and `take_master` (about L632) call `gitstore.refreshed(store)` and ignore a False return. Copy the pattern in `pstack_core/commands/rotate.py` L62-63: `if not gitstore.refreshed(store): sys.stderr.write(gitstore.UNKNOWN_FRESHNESS)`. Do the same for the call in `rotate_if_due` (rotate.py about L309). Do not change what happens next (the command continues, as rotate does). One test per command is not needed: add one table-driven test in `tests/test_pstack_kitchen.py` (AmpGit tests) over the three commands (rotate-master, take-master, a dispatch that runs rotate_if_due) with origin unreadable for the refresh, asserting the UNKNOWN_FRESHNESS text on stderr for each. If one of the three cannot be driven to that call without heavy setup, say so in the report and cover the other two. Commit, step.
3. **A rollback publish never hides the original error** (item 9b). In `master.py` about L503-519 the `except PairError:` branch restores pair.json and calls `publish_master(store)` before `raise`; if that publish raises, its error replaces the launch error. Wrap that one rollback publish: on `PairError` write `warning: the rollback could not be published: <error>` to stderr and fall through to the bare `raise` of the original. In `pstack_core/commands/rotate.py` about L100-126 the `finally:` publish has the same flaw: restructure so that when `spawn_role`/`fail_back` raised, a failing `gitstore.publish_role(store, 'master')` only prints the same kind of warning and the spawn error propagates; when the spawn succeeded, the publish runs as now and its error still raises (keep that behaviour). One test in `tests/test_pstack_kitchen.py` (AmpGit tests) per site: make the launch (rotate-master) or the spawn (rotation) fail and the publish fail too, and assert the command's stderr names the launch or spawn error, plus the warning line. Commit, step.
4. **`tests/run.py -k` fails on an import error** (item 11). `test_ids` (tests/run.py about L86-99) turns a module that fails to import into an id under `unittest.loader._FailedTest.`; the `-k` filter at about L154 drops it because the keyword is not in that id, so the run prints `0 tests, 0 failed` and exits 0. Keep every `_FailedTest` id in the selection whatever the keyword, so it runs and fails. No new committed test: the Verify line below proves it. Commit, step.

## Context

- Follow-ups from run sidekick-ab2: `gitstore.fetch_code ignores a failed trunk fetch and returns True, though a head on trunk now ships with no bundle` (joo 039); `kitchen_core/master.py hand_to_next_thread and take_master ignore a False from gitstore.refreshed (041 handles rotate only)`; `master.py:513 / rotate.py:124-126: a failing publish in the rollback or finally hides the launch or spawn error` (joo 041); `tests/run.py -k <text> prints '0 tests, 0 failed' and exits 0 when the test module fails to import (verdict 049-v1)`.
- `gitstore.refreshed` is at gitstore.py about L1213-1234 and returns False when freshness is unknown (lock held or origin unreadable).

## Acceptance

- With origin's trunk fetch failing and the code ref fetchable, `gitstore.fetch_code` returns False and `pair.sh sync --branch` prints `warning: origin or a published bundle could not be read`; with both fetchable it returns True as before.
- `rotate-master`, `take-master` and `rotate_if_due` each print `gitstore.UNKNOWN_FRESHNESS` on stderr when `refreshed` returns False, and carry on as before.
- When rotate-master's launch fails and its rollback publish fails too, the command's error names the launch failure and stderr has a `the rollback could not be published` warning; the same holds for a sidekick rotation whose spawn fails and whose publish fails.
- A successful rotation whose final publish fails still raises that publish error (unchanged).
- `python3 tests/run.py -k anything no_such_module_xyz` exits non-zero and reports the failed import; `python3 tests/run.py -k template_tells_a_role` still selects and passes the same tests as before.

## Verify

```bash
python3 tests/run.py -k fetch_code
python3 tests/run.py -k rollback
! python3 tests/run.py -k anything no_such_module_xyz
python3 tests/run.py -k template_tells_a_role
```

## Forbidden

- Do not change `commands/sync.py` or the text of `gitstore.UNKNOWN_FRESHNESS`.
- Do not catch broader than `PairError` in the rollback paths.

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
