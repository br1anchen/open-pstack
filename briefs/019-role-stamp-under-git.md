# Brief 019: role-stamp-under-git

playbook: bug-fix
timebox: 40
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/019-role-stamp-under-git.md

## Goal

Under the git store, a role thread whose skills directory differs from the last `skill_builds` row can run every `kitchen.sh` command (`finish`, `notify`, `step`); the kitchen's startup stamp no longer writes the master-owned `pair.json` from a role.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/setup.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/**
- skills/**/references/**
- skills/**/SKILL.md

## Steps

1. **Regression test first, then the fix.** In `tests/test_pstack_kitchen.py`, in the `AmpGitKitchenTests` class, add a test: a git-store run whose `pair.json` `skill_builds` last row names another skills directory; a role (the sidekick, as the fixture's other git-store role tests run one) runs `kitchen.sh finish` on a done report; today it fails with `pair.json: belongs to the master` (the variant's startup hook, `kitchen_core/setup.py` `record_skill_build` L48-68, calls `update(store, ...)`, which the git store's ownership check refuses for a role). Fix in `record_skill_build`: under the git store (`gitstore.is_git(store)` or the helper this module already uses), when the caller is not the master (`gitstore.caller_is_master(store)`, gitstore.py L247), return without writing. The master's commands still add the row. The same test asserts the finish succeeds and that a master command from a new skills directory still records its row. Commit, `kitchen.sh step`.

## Context

- Found by the sidekick at bootstrap (store `reports/000-ready-g3.md`, the `git-store bug:` line); it has worked around it with `.amp/in/orb-backup/kitchen-nostamp.py` since. After this lands, stop using that wrapper.
- Under the local store a role's pair.json write is merged by the bundle, so leave that path as it is.

## Acceptance

- Under the git store, a sidekick's `kitchen.sh finish` succeeds when its skills directory differs from the last `skill_builds` row, and pair.json on the branch is not changed by it.
- A master command from a new skills directory still appends a `skill_builds` row.

## Verify

```bash
python3 tests/run.py -k skill_build
```

## Forbidden

- No change to gitstore's ownership table.

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
