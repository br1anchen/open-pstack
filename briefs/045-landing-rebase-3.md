# Brief 045: landing-rebase-3

playbook: bug-fix
timebox: 60
commit: yes, the rebased commits plus one commit per conflict fix, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/045-landing-rebase-3.md

## Goal

One branch `kitchen-followups-landing-3` on top of current origin/main (eb8228f9 or later) that holds the accepted snooze stack (units 038, 043: `daf51dc0..e82b720a`, 4 commits) and then the accepted roster stack (units 010, 039, 042, 044: `84ea957f..57bf35b8`, 11 commits), with the full suite green, ready for Brian's approved push.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/**
- skills/pstack-kitchen/scripts/**
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/**
- tests/**

## Steps

1. `git fetch origin && git checkout -B kitchen-followups-landing-3 origin/main`, then `git cherry-pick daf51dc09eeffc59c136c169e371c8641fcf8a99..e82b720a9dad0c9a9a991a9c92737db9a67d4923` and `git cherry-pick 84ea957f4c38312234be77534684fff3d7e8ff82..57bf35b89495b4f9822cb2606fdc321ca29541f6` (84ea957f is already on main as part of daf51dc0's rebase, so only the roster commits come over). Resolve `tests/SPEC.md` conflicts by regenerating it (`python3 tests/spec_gen.py`). Resolve any other conflict by keeping both sides' behaviour, and name each in the report with the file and what each side did (eb8228f9 changed verifier handling: one live verifier per run). Record one step at the cherry-picked head.
2. Fix anything the full suite finds, one commit per fix, each recorded. No behaviour change beyond combining the two stacks with eb8228f9.

## Context

- Accepted: reviews/038, 043, 010, 039, 042, 044; verdicts 043-snooze-role-threads-fix-v1 (038+043) and 039-roster-handoff-fix-v1 (010+039), 044-roster-relay-repo-v1 (042+044).
- origin/main gained eb8228f9 (outside this run) after daf51dc0.

## Acceptance

- `git log origin/main..HEAD` holds the 15 commits by subject (4 snooze, 11 roster) plus any step 2 fix commits, and none twice.
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head.

## Verify

```bash
test "$(git rev-list --count origin/main..HEAD)" -ge 15
python3 tests/run.py -k snooze
python3 tests/run.py -k roster
python3 tests/run.py -k take_master
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv lock --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No push to main and no PR.
- Never end your turn while `kitchen.sh step` or `finish` prints `still running`; rerun it in the foreground.
- No `time.sleep` in tests, and no elapsed-time asserts.

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
