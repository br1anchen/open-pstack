# Brief 043: snooze-role-threads-fix

playbook: review-fix
timebox: 75
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/043-snooze-role-threads-fix.md

## Goal

Unit 038's snooze works under the git store and never leaves a half-sent plan round: `snooze.tsv` is one of the master's published files, every command that sends to several roles checks them all awake before it sends to any, `snooze --done` refuses a master or a thread that is not snoozable, `status` exports each thread once, and master.md says the off switch stops new snoozing only.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- skills/pstack-pair/scripts/pstack_core/commands/snooze.py
- skills/pstack-pair/scripts/pstack_core/commands/status.py
- skills/pstack-pair/scripts/pstack_core/consult.py
- skills/pstack-pair/scripts/pstack_core/send.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-kitchen/references/master.md
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- anything else

## Steps

Start from unit 038's head: `git checkout -B kitchen-followups-038 bee0dc78bf5e357ee0c008b70ba54e1c35ad6a1b` (on origin as kitchen-code/kitchen-followups/unit-038).

1. `gitstore.py` RULES: add `snooze\.tsv` to the master's files rule, next to `nest-pending.tsv` (gitstore.py ~110-111). Tests: the RULES test classes `snooze.tsv` as MASTER; an `AmpGitKitchenTests` test runs `snooze --done` then a master publish and asserts the fake origin's branch holds `snooze.tsv` (assert on the pushed tree). Targeted check: `python3 tests/run.py -k snooze` and `python3 tests/run.py -k gitstore`.
2. Check every recipient before any send. In `consult.send_plan` (consult.py ~160-165), call `amp.require_awake` for each role's thread before the loop delivers to the first; do the same anywhere else a command sends to more than one role in one call (search `send_plan` and `deliver_plan` callers). Test: a plan round with an awake sidekick and a snoozed consultant refuses before any `send-plan` event is logged and before the sidekick receives anything; after `--wake` it sends to both. Targeted check: `python3 tests/run.py -k snooze` and `python3 tests/run.py -k discuss`.
3. `snooze.py` `record` with `--done`: refuse, before writing any row, a thread whose role is `master` (`snooze: <T> is a master thread; masters are never snoozed`) or that `snoozable()` rejects (`snooze: <T> is not snoozable now (<reason>)`, where the reason is the state or the open work). `--wake` stays unchecked. One export per thread per command: `candidates()` and the `needs-human:` loop in status share one state lookup per thread (pass the states in, or cache per call; no module-level cache that outlives the command). Tests: extend 038's table with `--done` on a master row and on a working thread (refused, no row); a status test counts the fake amp's export calls and asserts one per thread. Targeted check: `python3 tests/run.py -k snooze` and `python3 tests/run.py -k status`.
4. Docs: master.md (~line 99) and SKILL.md say `PSTACK_SNOOZE_ROLES=0` stops new snoozing, and a thread already recorded snoozed still needs `update_thread(snooze="off")` and `snooze --wake` before work reaches it; add `snooze.tsv` to the git-store ownership sentence where `nest-pending.tsv` is named. Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Review: reviews/038-snooze-role-threads.md (verdict revise). joo run 2026-11-23T20-50-50-476Z-d1e0c6db on daf51dc0..bee0dc78; findings with file:line in the review note.
- This run uses the git store, so step 1 is what makes 038 usable here.

## Acceptance

- Under the git store, a master publish after `snooze --done` succeeds and the branch holds `snooze.tsv`.
- A plan round to an awake sidekick and a snoozed consultant refuses with nothing sent and no event; after `--wake` both receive it.
- `snooze --done` refuses a master thread and a non-snoozable thread and writes no row.
- `status` exports each role thread at most once.
- The docs state the off switch's narrower meaning; 038's tests pass; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k snooze
python3 tests/run.py -k gitstore
python3 tests/run.py -k discuss
python3 tests/run.py -k status
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No change to nest.py.
- No `time.sleep` in tests, and no elapsed-time asserts.
- Never end your turn while `kitchen.sh step` or `finish` prints `still running`; rerun it in the foreground.
- No push to main and no PR.

## Playbook: review-fix (your parts)

Unchanged from briefs/042-roster-relay-path.md, which this session already read; reread it there.

## Report

Unchanged from briefs/042-roster-relay-path.md, which this session already read; reread it there.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
