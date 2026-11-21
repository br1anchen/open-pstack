# Brief 034: land-check-gone-head-fix

playbook: review-fix
timebox: 120
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/034-land-check-gone-head-fix.md

## Goal

Fix joo's medium findings on unit 006 (joo run 2026-11-21T09-42-59-674Z-9a98e300; the verifier's verdict 006-v1 is clean). 006 pins evidence under keep refs and carries them in every message. As written, every ordinary message pays an origin fetch, carries every run's keep refs, and refuses the whole store sync on one missing commit; and land-check can blame or credit the wrong unit. An ordinary message must cost what it cost before 006, and land-check must name only verdicts and carriers that really cover the unit.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/git.py
- skills/pstack-kitchen/scripts/kitchen_core/landing.py
- skills/pstack-kitchen/scripts/kitchen_core/verify_launch.py
- skills/pstack-kitchen/scripts/kitchen_core/verify.py
- skills/pstack-kitchen/scripts/kitchen_core/step.py
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/pstack-pair/golden/**

## Steps

1. **An ordinary message carries only this run's evidence and fetches nothing** (joo medium, two lanes). Keep refs of other runs under `refs/pstack/keep` must not ride along: carry only the refs of the store being sent. An ordinary (non-hand-over) send must not run `origin_baseline`'s `git fetch origin`; cut it as it was cut before 006 (`--remotes`, or the existing tracking ref). Only a hand-over fetches. Test: a send with keep refs of two runs carries only its own run's refs, and the fake/real git records no `fetch` for an ordinary send.
2. **Evidence never makes an ordinary sync all-or-nothing** (joo medium, two lanes). A missing evidence commit in an ordinary message must not refuse the store sync: apply the store and the other refs, print one line naming the evidence ref it could not keep, and go on. Hand-overs keep their strict rule from 031. Test: an ordinary bundle whose one evidence tip is missing syncs its store files and prints that line.
3. **land-check blames and credits only what could cover the unit** (joo medium x2, two lanes each). A gone landing-verdict head is named for a unit only when that landing verdict's range could have covered it (its packet lists the unit, or the unit's head is in its range). The carrier unit suggested for a lost unit must be one whose head contains the lost unit's change (by ancestry, or by patch-id as land-check already uses), not just the highest-numbered unit whose head resolves; with none, say so. One table test over the cases: covering verdict gone, unrelated verdict gone, carrier that holds the change, carrier that does not.
4. **Pins are not silent and gates/batch verifications pin too** (joo lows). A failed pin (bad slug, invalid ref) prints one `warn:` line. A gates-mode or batch verification pins its range base and head the same way a launched verifier does. Drop the dead `NoBrief` skip in `pin_evidence`. Test: a gates-mode verification leaves keep refs for its base and head.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-033 && git checkout -B kitchen-followups FETCH_HEAD` (033's head, which the master pushes when 033 reports; on top of 006's 6921d9f). Do not rebase onto origin/main. If unit-033 is not on origin, report blocked.
- 006's brief, report and verdict: `briefs/006-…`, `reports/006-…`, `verdicts/006-land-check-gone-head-v1.md`. joo run copied to the master's `.amp/in/orb-backup/joo/006-run`.
- Follow-up, not in this brief: removing keep refs after a run closes (needs a retention policy).

## Acceptance

- Each step's tests fail without its fix (show by scratch revert).
- 006's Acceptance still holds, and every bundle, sync, land_check and verify test passes.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k sync
python3 tests/run.py -k land
python3 tests/run.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push and no PR.

## Playbook: review-fix (your parts)

Unchanged from briefs/033-master-bundle-branches-fix.md, which this session already read; reread it there.

## Report

Unchanged from briefs/033-master-bundle-branches-fix.md, which this session already read; reread it there.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
