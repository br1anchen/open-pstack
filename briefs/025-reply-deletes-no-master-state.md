# Brief 025: reply-deletes-no-master-state

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/025-reply-deletes-no-master-state.md

## Goal

On a role-to-master local-store sync (unit 005's `protect`), a role's reply deletes nothing the master wrote: in particular the master's check-in marker `progress/<unit>.md.seen` (written by pstack_core/checkin.py L389, in the sidekick-owned `progress/` directory) survives a reply built before it existed.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py

## Steps

1. Start from unit 005's head b1ca49f (origin kitchen-code/kitchen-followups/sidekick-g4 or the run's code ref). Under `protect`, a path absent from the incoming bundle and present locally is kept, whatever its owner: a role never deletes store files by omission (roles delete nothing in the store by design; confirm with a grep and list any role-side `os.unlink`/`remove` of store paths in the report, and keep deletion for those exact paths only). Add a `BundleMergeTests` row: the master's `progress/025-x.md.seen` and a new `steers/025-x-s2.md` survive a sidekick reply built before them, while the reply's own new progress lines arrive. Commit, `kitchen.sh step`.

## Context

- `notes/s4-ledger-1252-issues.md` issue 3 (seen on build 73e425b2aba71e5a): the sync deleted `steers/025-...-s2.md` (master-owned: 005 covers it) and `progress/025-...md.seen` (master-written in a role directory: 005 does not).

## Acceptance

- A delayed sidekick reply neither deletes a `.seen` marker nor a steer the master wrote after the snapshot the reply was built from.

## Verify

```bash
python3 tests/run.py -k BundleMerge
```

## Forbidden

- No change to RULES.

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
