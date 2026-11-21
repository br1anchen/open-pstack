# Brief 030: reply-deletes-no-master-state-fix

playbook: review-fix
timebox: 90
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/plans/021-kept-sidekick-session-r2.md
risk: escalated
profiles: pstack, pstack-docs
verify: unit by escalated verifier
review-engine: joo
steps: gates
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/030-reply-deletes-no-master-state-fix.md

## Goal

Fix every finding in the verifier's verdict /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/025-reply-deletes-no-master-state-v1.md, so a fresh verification of the same acceptance passes.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/mux/base.py
- skills/pstack-kitchen/scripts/kitchen_core/playbook_delivery.py
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/pstack-pair/golden/**

## Steps

1. **One dispatch reads each thread's context once** (verdict 025-v1 finding 1, Evidence E5). A routine Amp dispatch that keeps the sidekick runs `amp threads context` 3 times on the sidekick's thread: once in `session_decision` (rotate.py:197, `m.context(name)`), once in `playbook_delivery.py:186` (`m.compactions(name)`) and once in `mux/amp.py:1143` (`thread_compactions(thread)`). Memoize the observation in `mux/amp.py`, per thread, for the life of one command, so every reader in that command shares one `amp threads context` call. Drop a thread's entry after any send to it, because its context changes then. A new rotated thread has a new id, so its first read is fresh. Add `skills/pstack-kitchen/scripts/kitchen_core/playbook_delivery.py` to what you touch only if its call has to change to reach the memo (it is in Scope below). Extend the call-counting test from unit 025 (or AmpContextTests): one routine dispatch that keeps the sidekick logs exactly 1 `threads context` call on the sidekick's thread in the fake's call log. Show it fails with the memo removed.
2. **The sync line names whose copy it kept** (joo 025, both lanes, low). The reply-merge's sync output lists another role's kept file under "kept the master's copy of". List those separately, for example `kept <role>'s copy of: <paths>`, keeping the existing line for the master's own files, and make the SKILL.md sentence that describes the line match. Update the test that asserts the line.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-025 && git checkout -B kitchen-followups FETCH_HEAD` (025's head 6a83f50). Do not rebase onto origin/main. Unit 004 (same base) may sit in your checkout: commit or stash it first, and touch nothing outside Scope.
- joo run on 025: 2026-11-21T04-01-13-643Z-e86be53c (two lows, both covered by step 2).
- verdict: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/verdicts/025-reply-deletes-no-master-state-v1.md
- original brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/025-reply-deletes-no-master-state.md

## Acceptance

- A stale role reply neither deletes nor rolls back another role's report or step log, a master steer, or a `.seen` marker; the replying role's own files and new files arrive.
- The two-orb regression asserts reports/002 and steps/002 survive.
- A routine Amp dispatch that keeps the sidekick runs `amp threads context` once on the sidekick's thread (the fake's call log); the sync line names whose copy it kept; a manual `rotate` event carries `reason:manual`.
- Unit 005's Acceptance still holds at this head; goldens unchanged.

## Verify

```bash
python3 tests/run.py -k BundleMerge
python3 tests/run.py -k keeps_the_sidekick
python3 tests/run.py -k rotat
python3 tests/spec_gen.py --check
```

## Forbidden

- No change to RULES or the bundle wire format.

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
