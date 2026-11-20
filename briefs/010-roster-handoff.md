# Brief 010: roster-handoff

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/010-roster-handoff.md

## Goal

`rotate-master` snapshots the host roster into the store, and `take-master` installs it on the next master's orb when that orb has no roster, so verifiers and new roles keep their models after a hand-over.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- skills/pstack-kitchen/scripts/kitchen_core/kpy.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/**
- skills/**/references/**
- skills/**/SKILL.md

## Steps

The design is the consultant's advice, `advice/004-master-bundle-branches-c1.md` in the store, section "Roster snapshot and restore". Read it first; it wins on any detail it settles.

1. **Snapshot at hand-over.** In `kitchen_core/master.py`, `hand_to_next_thread` (about L482-519), before `launch_master` packs the store: read the effective roster bytes from `roster_path()` (skills/pstack-kitchen/scripts/kitchen.py L459-463, through `kpy.kitchen_py`; it honours `PSTACK_KITCHEN_ROSTER`), parse them as TOML (an invalid roster refuses the rotation with the parse error), scan them with `pstack_core.secrets.scan` (a finding refuses with file and kind only, nothing written), and write them to `<store>/sessions/master-g<next>-roster.toml`; name that path in the hand-over record in pair.json (`.master.previous` or the session record, wherever `record_handover` keeps the generation's files). No roster on this orb: write nothing and record that. Commit with its test, `kitchen.sh step`.
2. **Restore at take-over, missing-only.** In `take_master` (about L626-660), after the caller is confirmed as the pending next master and before the old master is closed or `.master.previous` is cleared: with a snapshot recorded and no file at this orb's `roster_path()`, install it atomically (write a temp file beside it, then `os.link` or an exclusive create, so a concurrently created file is never replaced) and print `roster: installed <path> from <snapshot>`. Identical file: print nothing extra. A different file: keep it and print `roster: kept <path>; it differs from <snapshot>` (no contents). An unreadable target is an error, not "absent". A store from before this change (no snapshot recorded): print `roster: none recorded by the previous master`. A failed restore leaves take-master retryable. Commit with its test, step.
3. **Tests.** One table-driven test in `tests/test_pstack_kitchen.py` `AmpKitchenTests`, beside `test_take_master_in_the_new_thread_from_the_bundle_it_received` (about L16439), over the receiver's roster: absent (installed, bytes equal including `[[sidekick.alternate]]` and fallback chains), identical (unchanged), different (kept, line printed), no snapshot (notice). Plus: `PSTACK_KITCHEN_ROSTER` on both sides is honoured; a roster holding a fake token is refused at rotate-master with file and kind named and no snapshot written (the fixture line carries `kitchen-allow: literal-secret` and why); a second take-master does not replace a file it installed and the user then edited. Fold these into the table where they fit. Commit, step.

## Context

- Gap 2 of run sidekick-ab2: `notes/kitchen-gaps.md` in the store.
- The roster is a kitchen concept: no pstack_core change.

## Acceptance

- After rotate-master and take-master on an orb with no roster, the orb's `roster_path()` holds the old master's roster byte for byte.
- An existing roster on the receiving orb is never overwritten; a different one is reported by path.
- A roster that fails the secret scan or TOML parse refuses the rotation and writes no snapshot.
- A store with no recorded snapshot takes over as before, with the notice line.

## Verify

```bash
python3 tests/run.py -k take_master
python3 tests/run.py -k roster
```

## Forbidden

- No change to pstack_core.
- Never print roster contents.

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
