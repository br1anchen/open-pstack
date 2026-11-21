# Brief 035: branch-ahead-not-diverged

playbook: bug-fix
timebox: 45
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/035-branch-ahead-not-diverged.md

## Goal

When a hand-over arrives with the receiver's checkout on branch X and the receiver's copy of the sender's branch Y only ahead of the sender's tip (the sent head is an ancestor of local Y), the sync reports Y as "diverged" and prints a recovery recipe. Y is ahead, not diverged: say so, print no recipe, and keep reporting Y once. Brian asked for this before landing (verdict 033-v1 observation 2).

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **Ahead is not diverged.** In `bundle.py` `move_checkout`, the branch case (`mine = f'refs/heads/{branch}'` ... `own_commits(root, mine, head)`) treats any commit Y holds past the sent head as divergence. Before calling `diverged`, check `git merge-base --is-ancestor <head> refs/heads/<branch>`: when true, return one line, `checkout: kept <local>: <on> stays, and branch <Y> here is ahead of <sender> (<head short>)`, with `branch` as the reported branch, so apply_branches prints no second Y line, and no recipe. A real divergence (neither side an ancestor) keeps today's line and recipe. Add one table-driven test over the receiver-on-X cases: Y ahead (one line, says ahead, no `kitchen-recovery`), Y diverged (one line, says diverged, has the recipe), Y behind (fast-forwarded as today). Show the ahead row fails without the fix by scratch revert.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-034 && git checkout -B kitchen-followups FETCH_HEAD` (unit 034's head, which the master pushes when 034 reports; on top of 033's 179a195). Do not rebase onto origin/main. If unit-034 is not on origin, report blocked.
- Verdict `verdicts/033-master-bundle-branches-fix-v1.md`, observation 2 and Evidence D1 (ahead-elsewhere). Closest test: `test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here`.

## Acceptance

- The three-row test passes at the head; its ahead row fails without the fix.
- 033's Acceptance still holds and every bundle, sync and take_master test passes.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k sync
python3 tests/run.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push and no PR.

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
