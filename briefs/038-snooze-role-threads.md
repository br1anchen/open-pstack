# Brief 038: snooze-role-threads

playbook: feature
timebox: 75
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/038-snooze-role-threads.md

## Goal

Under Amp threads, every role thread the kitchen starts (sidekick, consultant, verifier, helper) is snoozed indefinitely by the master in the same step that nests it under the run's anchor, so the human sees only master threads; master threads are never snoozed, and `PSTACK_SNOOZE_ROLES=0` turns the snooze off (on by default).

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py
- skills/pstack-pair/scripts/pstack_core/commands/status.py
- skills/pstack-pair/scripts/pstack_core/numbers.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- tests/test_pstack_kitchen.py
- tests/SPEC.md
- tests/pstack-pair/bin/amp

must not write:
- skills/pstack-pair/scripts/pstack_core/mux/amp.py (the CLI cannot snooze; the master's update_thread tool does it, as it does the nesting)
- anything else

## Steps

Start from the rebased stack 037 leaves (main be882968 plus the run's units); `nest.py` there holds `take_anchor`, `record_pending`, `add_pending`, `nest` and `done`.

1. `nest.py`: snooze rides on the existing nest-pending rows; add no column to `nest-pending.tsv` (bundle merges and `rows()` read exactly 5 cells) and no second file. Add `SNOOZE = 'PSTACK_SNOOZE_ROLES'` beside `VIA`, and a function `snooze_roles()` that is True unless `os.environ.get(SNOOZE) == '0'`. A pending row is to be snoozed when `snooze_roles()` and `r.role != 'master'` (a `TOP_LEVEL` row is always a master). In `nest()`'s listing, a row to snooze prints `nest-pending: <T> (<role>) under <anchor>, snooze` (the existing line plus `, snooze`); the instruction line for rows with a parent becomes `nest: call update_thread(thread=<T>, parent_thread=<anchor>, snooze="indefinite") for each row marked snooze and update_thread(thread=<T>, parent_thread=<anchor>) for the rest, then ...` when any row is marked, and stays exactly as today when none is (so `PSTACK_SNOOZE_ROLES=0` gives today's output byte for byte). `--done` marks both: `done()` writes the same `nested` row, and for a row it snoozed it logs one more event, `snooze`, with `<T>` as detail, and prints `nested: <T> under <anchor>, snoozed`. Update the module docstring in one sentence. Test (step 1): extend the existing nest tests in `tests/test_pstack_kitchen.py` around `test_nest_lists_exactly_the_pending_rows_and_says_to_call_update_thread` with one parameterised table over (role, PSTACK_SNOOZE_ROLES unset / `1` / `0`) -> listed suffix and instruction line, covering sidekick, consultant, verifier, helper and a top-level master row; plus one test that `--done` on a sidekick and a master row writes a `snooze` event only for the sidekick. Targeted check: `python3 tests/run.py -k nest`.
2. `status.py` (only through `nest.status_line` or one line beside its call at status.py:170): when any pending row is marked snooze, the status line reads `nest-pending: N threads (M to snooze)`. Also print, for each live role thread (`amp.live_threads`, role not master) whose recorded agent state is `error`, `needs-human: <role> <T> (error); the thread is snoozed, so relay it to the human from the master thread`. Read the state the same way `status` already reads role states (do not add new amp CLI calls); if status has no state for amp role threads at this point, skip the needs-human line and say so in the report instead of building it. Test: one test with the fake amp (`tests/pstack-pair/bin/amp`) giving a sidekick thread state `error`. Targeted check: `python3 tests/run.py -k status`.
3. Docs. `skills/pstack-kitchen/SKILL.md`, the Amp threads bullet that describes nesting (search `nest-pending.tsv`): role threads are also snoozed indefinitely by the master in the same `update_thread` call, master threads never; `PSTACK_SNOOZE_ROLES=0` turns it off. `skills/pstack-kitchen/references/master.md`, the step 5 sentence about `nest-pending:` and the take-master paragraph: the call carries `snooze="indefinite"` for each row `nest` marks `snooze`; and one new sentence: a snoozed role is hidden from the human, so when one is blocked on a question for the human or errors in a way only the human can fix (a `needs-human:` line, a `blocked` report with Questions, an approval dialog), the master relays it to the human from the master thread. Regenerate `tests/SPEC.md` with `python3 tests/spec_gen.py`. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Brian's request (2026-11-21, relayed by Puck T-01a1eafa-f54b-71ff-890f-07428ab9c3e3): snooze every non-master kitchen thread by default; master threads (including rotated generations) stay unsnoozed; the master relays role blockers to him; a setting turns it off; fake-amp tests and docs.
- Pattern to copy: `VIA = 'PSTACK_NEST_VIA'` in nest.py and its test `test_nest_asks_the_named_thread_when_nest_via_is_set`.
- Standing orders 24, 27, 29.

## Acceptance

- With `PSTACK_SNOOZE_ROLES` unset, `kitchen.sh nest <store>` marks every pending non-master row `, snooze`, never a master row, and its instruction names `snooze="indefinite"`; with `PSTACK_SNOOZE_ROLES=0` its output equals today's.
- `nest --done` on a snoozed row logs a `snooze` event and says `snoozed`; on a master row it logs none.
- `kitchen.sh status` counts the rows to snooze, and names a role thread in state `error` with a `needs-human:` line (or the report says why that line was not built).
- SKILL.md and master.md describe the snooze, the off switch and the relay duty; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k nest
python3 tests/run.py -k status
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No new amp CLI calls and no change to mux/amp.py.
- No `time.sleep` in tests, and no elapsed-time asserts.
- No push to main and no PR.

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
