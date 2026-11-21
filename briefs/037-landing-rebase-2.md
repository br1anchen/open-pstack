# Brief 037: landing-rebase-2

playbook: bug-fix
timebox: 60
commit: yes, the rebased commits plus one commit per resolution step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/037-landing-rebase-2.md

## Goal

Rebase the run's accepted stack onto main so Brian's approved second push can land. The stack is `c5cf2504..84ea957f` (46 commits, units 005, 022-026, 028, 030-036, 004, 006); main is `be882968`, which already holds units 001-003 and 017-020 (rebased) and PR #13 (units 027 and 029, master-is-anchor). The master's trial `git rebase --onto be882968 c5cf2504 84ea957f` stops at commit 26 of 46, `fe439aee` (unit 022, `nest --discover`), with a conflict in `skills/pstack-pair/scripts/pstack_core/commands/nest.py`: PR #13 added `anchor_title` and `take_anchor` where 022 added `oldest_master`, `run_threads` and `discover`. Earlier commits conflict only in `tests/SPEC.md`.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/**
- skills/pstack-kitchen/scripts/**
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/**
- tests/**

## Steps

1. **Rebase.** `git rebase --onto be88296826fbc6e1c7ab0ebf8a1dc26467c573cf c5cf2504 84ea957f`. Where only `tests/SPEC.md` conflicts, run `python3 tests/spec_gen.py` and `git add tests/SPEC.md`. In `nest.py` keep both sides: PR #13's `anchor_title`/`take_anchor` and 022's `oldest_master`/`run_threads`/`discover`, reconciling helper names (`add_pending` on main against `record_pending` in 022) so one helper remains. Resolve any later conflict the same way: keep both units' behaviour. Keep every commit's message; record the rebased stack with one `kitchen.sh step` naming the old and new heads.
2. **discover follows master-is-anchor.** Under PR #13 the current master is the run's top-level anchor and takes it over on rotation (`take_anchor`). 022's `discover` sets a missing anchor to the *oldest* master, which on a rotated run is an archived generation. Make a missing anchor default to the current master's thread (`pair.json` `.master.pane_id` `amp:T-...`, else the newest `.master.sessions` entry), and record that thread as nest-pending `TOP_LEVEL` the way `take_anchor` does, not under itself. Drop `oldest_master` if nothing else uses it. Update 022's test so it asserts the current master, with a rotated run (two master sessions) where oldest and current differ; show it fails before this step.
3. **Whole suite at the rebased head.** Fix whatever the combination breaks, one commit per fix with its test.

## Context

- Start: `git fetch origin main kitchen-code/kitchen-followups/unit-036 && git checkout -B kitchen-followups-landing 84ea957f4c38312234be77534684fff3d7e8ff82`. Do not touch the branch you used for 010.
- Unit 022's brief and review: `briefs/022-migrated-threads-archive-nest.md`, `reviews/022-*.md`. PR #13: https://github.com/br1anchen/open-pstack/pull/13 (`take_anchor`, standing order 27).
- Report the new head; the master pushes it to `kitchen-code/kitchen-followups/unit-037` and runs `verify --landing`.

## Acceptance

- `git log be88296..HEAD` holds every one of the 46 commits (by subject) plus the step 2 and step 3 commits, and nothing from 001-003/017-020 twice.
- `discover` on a rotated run with no anchor sets the current master, recorded top-level; the test fails without step 2.
- 022's and PR #13's tests pass, and so does the whole suite.

## Verify

```bash
python3 tests/run.py -k nest
python3 tests/run.py -k anchor
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv lock --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push to main and no PR.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
