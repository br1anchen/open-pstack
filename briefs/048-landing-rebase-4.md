# Brief 048: landing-rebase-4

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/048-landing-rebase-4.md

## Goal

One branch `kitchen-followups-landing-4` on top of current origin/main that holds the accepted unit 040 (`eb8228f9..6b2551f9`, 4 commits) and then the accepted unit 046 (`699837d8..11d2874b`, 4 commits), with the full suite green, ready for Brian's approved push. Unit 047 is not part of this landing.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/**
- skills/pstack-pair/SKILL.md
- skills/pstack-kitchen/scripts/**
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/**
- tests/**

## Steps

1. `git fetch origin && git checkout -B kitchen-followups-landing-4 origin/main`, then `git cherry-pick eb8228f9a7dc6466e40f8674913b3bea3e8c52eb..6b2551f9b05531c632a2d3e958731fb76f5c4b87` and `git cherry-pick 699837d853a868ed785fa843cb167ee3e559c3bc..11d2874b62ef48ef9305c3e772e388e2cf66e2e6`. Resolve `tests/SPEC.md` conflicts by regenerating it (`python3 tests/spec_gen.py`). Resolve any other conflict by keeping both sides' behaviour, and name each in the report with the file and what each side did. 040 was written before the snooze and roster stacks reached main, so expect overlap in mux/amp.py (`record_started` prints nest lines, `assess` keeps a nest-pending thread; main added `pending_to`, `require_awake`, `exports_cached`) and in the tests' helpers (040's `nest_all`, `send_from_the_master` reading the last stdout line). Record one step at the cherry-picked head.
2. Fix anything the full suite finds, one commit per fix, each recorded. A snooze or roster test that now meets a nest-pending row or printed nest lines gets the same treatment 040 gave its neighbours (`nest_all`, last-line reads); no behaviour change beyond combining 040 and 046 with main.

## Context

- Accepted: reviews/040-nest-every-start.md (verdict 040-v1 clean), reviews/046-snooze-stop-and-done.md (verdict 046-v1 clean).
- Main is at 699837d8 or later (it may hold 047 by the time you start only if Brian approved it; it does not now).

## Acceptance

- `git log origin/main..HEAD` holds the 8 commits by subject (4 from 040, 4 from 046) plus any step 2 fix commits, none twice.
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head.

## Verify

```bash
test "$(git rev-list --count origin/main..HEAD)" -ge 8
test -z "$(git log --format=%s origin/main..HEAD | sort | uniq -d)"
python3 tests/run.py -k nest
python3 tests/run.py -k snooze
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
