# Brief 050: landing-rebase-5

playbook: bug-fix
timebox: 60
commit: yes, the cherry-picked commits plus one commit per conflict fix, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/050-landing-rebase-5.md

## Goal

Redo landing 048 on today's main: one branch `kitchen-followups-landing-5` on top of current origin/main (3b951444 or later) holding the 8 commits of `699837d8..fa6cd2a6` (units 040 and 046, as landing-v4 verified them), with the full suite green, for Brian's approved push. Main gained 13 commits by Brian after 699837d8 (dispatch warnings, landing_base, check-in digest, master.md rewraps), so the push of fa6cd2a6 was refused as not a fast-forward.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/**
- skills/pstack-pair/SKILL.md
- skills/pstack-kitchen/scripts/**
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/**
- tests/**

## Steps

1. `git fetch origin && git checkout -B kitchen-followups-landing-5 origin/main`, then `git cherry-pick 699837d853a868ed785fa843cb167ee3e559c3bc..fa6cd2a6f85c8329ad5dd52da01e64352daeb2dc`. Regenerate `tests/SPEC.md` at each conflict (`python3 tests/spec_gen.py`). Files both sides touched: skills/pstack-kitchen/references/master.md, skills/pstack-kitchen/scripts/kitchen_core/dispatch.py (main: the escalated plan-none warn moved out of `dispatch()` into a helper, delivered-only; 046: `require_sidekick_awake` before `rotate_gate`, classify and the sweep), tests/test_pstack_core.py, tests/test_pstack_kitchen.py, tests/test_pstack_pair.py. Keep both sides' behaviour and name each conflict in the report with the file and what each side did. Record one step at the cherry-picked head.
2. Fix anything the full suite finds, one commit per fix, each recorded; no behaviour change beyond combining 040 and 046 with main.

## Context

- reviews/048-landing-rebase-4.md and verdicts/landing-v4.md (clean on 699837d8..fa6cd2a6). Brian approved pushing 040 + 046 on a clean landing verdict and land-check.

## Acceptance

- `git log origin/main..HEAD` holds the 8 commits by subject plus any step 2 fixes, none twice.
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head.

## Verify

```bash
test "$(git rev-list --count origin/main..HEAD)" -ge 8
test -z "$(git log --format=%s origin/main..HEAD | sort | uniq -d)"
python3 tests/run.py -k nest
python3 tests/run.py -k snooze
python3 tests/run.py -k dispatch
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
