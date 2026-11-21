# Brief 029: master-is-anchor-fix

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/029-master-is-anchor-fix.md

## Goal

Fix joo's findings on unit 027 (the joo run on origin/main..a0eb1b7, quick-correctness lane, all low). These go in before 027 ships as a PR.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- tests/test_pstack_kitchen.py
- tests/pstack-pair/bin/amp
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **The title is saved when the stored one is empty.** In `nest.anchor_title`, `doc['anchor'].setdefault('title', title)` keeps the `''` that `init` writes when export fails. That means the title is never saved and is re-read on every call. Assign it instead. Add a test: init with a fake export that has no title, then give the export a title, then run `take-master`. The fake must record the rename with that title, and `pair.json` `.anchor.title` must hold it.
2. **No empty previous anchor.** In `nest.take_anchor`, append `old` to `.anchor.previous` only when it is non-empty and differs from `thread`. Guard against `doc['anchor']` being missing (create it). Restore the `pair.json` existence check in `mux/amp.py` `record_started` on the master branch before it calls `nest.anchor`, as `record_pending` has.
3. **Say the truth when the rename fails.** When `take_anchor` returns a warning, take-master's `anchor:` line must not say the thread holds the title. Print `anchor: <T> is the run's top-level anchor; run kitchen.sh nest <store>` with no title, after the `warn:` line. Extend the existing failed-rename test to assert this.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-027 && git checkout -B master-is-anchor FETCH_HEAD` (027's head a0eb1b7, on origin/main). Do not rebase. This is 027's own branch and PR, apart from the run stack. If you hold uncommitted 028 work, commit it first.
- Verdict for 027: `verdicts/027-master-is-anchor-v1.md` (clean). Its report is `reports/027-master-is-anchor.md`.

## Acceptance

- Each of the three steps has a test that fails without its fix (show this with a scratch revert).
- Every nest, anchor, take_master and rotate_master test passes, and so does the full suite.

## Verify

```bash
python3 tests/run.py -k nest
python3 tests/run.py -k anchor
python3 tests/run.py -k take_master
python3 tests/run.py
python3 tests/spec_gen.py --check
python3 scripts/check-pstack.py
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push and no PR.

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
