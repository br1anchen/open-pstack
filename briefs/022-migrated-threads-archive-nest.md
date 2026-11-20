# Brief 022: migrated-threads-archive-nest

playbook: bug-fix
timebox: 75
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/022-migrated-threads-archive-nest.md

## Goal

A run migrated with `store-to-git` can archive and nest the role threads it started before the migration: `threads --archive` judges a thread with no request on the store branch that was retired or last sent work before the migration by the local-store rule instead of keeping it for a missing drain receipt, and `nest <store> --discover` records the store's known threads as nest-pending and sets `.anchor` (the oldest master thread) when pair.json has none.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/mux/amp_routing.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/commands/store_to_git.py
- skills/pstack-pair/scripts/pstack_core/commands/nest.py
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/pstack-pair/golden/**

## Steps

1. **Pre-migration threads archive by the local rule.** `store_to_git` (commands/store_to_git.py L205+) records the migration time in pair.json `.store_backend.migrated_at` (one write, in the phase that records `.store_backend`). In `mux/amp_routing.py` `undrained` (about L454-476), when the thread's record holds no request (today `no drain receipt (the store branch records no request to it)`) and the store has `migrated_at` and the thread started before it, return '' so the remaining checks (`role_is_idle`, pending relays, unsynced files, report head) decide as under the local store. A thread started after the migration with no request keeps today's answer. Test in AmpGitKitchenTests: a local run with an idle done sidekick g1 and an answered consultant, store-to-git, a new sidekick g2 started after; `threads --archive` archives g1 and the consultant, keeps g2 (no request) and an old thread still working. Commit, `kitchen.sh step`.
2. **`nest --discover`.** In commands/nest.py, add `--discover`: record as nest-pending every thread the store's registry and `threads --discover` sources know for this store (reuse `Amp.discover`, mux/amp.py about L951, rather than a second search) that is not already nested or pending; when pair.json has no `.anchor`, set it to the oldest master thread it knows (the `.master.sessions` records, else the current master); print the same `update_thread` lines `nest` prints. Repeat runs add nothing. Test: a store with no anchor and three older threads → anchor = oldest master, three pending rows; a second run → `nest: nothing pending` after `--done`. Commit, step.
3. **Docs.** SKILL.md \"Amp threads\": one sentence each for the migrated-thread rule and `nest --discover`. Commit, step.

## Context

- Issues 1 and 2 in `notes/s4-ledger-1252-issues.md`. The same `no drain receipt` keep hit this run's GLM sidekick after store-to-git (decisions.tsv `archive`).

## Acceptance

- After store-to-git, a role thread started before the migration with no request on the branch is archived when the local-store rule allows; one started after keeps `no drain receipt`.
- `nest --discover` on a store with no `.anchor` sets it to the oldest master thread and lists the store's unnested threads; a rerun adds nothing.

## Verify

```bash
python3 tests/run.py -k archive
python3 tests/run.py -k nest
python3 tests/spec_gen.py --check
```

## Forbidden

- No change to the drain-receipt rule for threads started after the migration.

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
