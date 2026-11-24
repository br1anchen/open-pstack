# Brief 046: snooze-stop-and-done

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/046-snooze-stop-and-done.md

## Goal

The role-thread snooze on main (units 038 and 043) refuses before it changes anything on every path, says how to undo a snooze it will not record, and is listed in `kitchen.sh help`: `stop`, `close` and `dispatch` check that their role threads are awake before they clear a queue, log an event or run the archive sweep; `snooze --done` refusing a thread prints the `update_thread(thread=<T>, snooze="off")` call that undoes the Amp snooze; snooze refusals have their own error class.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/snooze.py
- skills/pstack-pair/scripts/pstack_core/commands/stop.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/send.py
- skills/pstack-pair/scripts/pstack_core/errors.py
- skills/pstack-pair/scripts/pstack_core/variant.py
- skills/pstack-kitchen/scripts/variant.py
- skills/pstack-kitchen/scripts/kitchen_core/dispatch.py
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py (unit 040 changes it and is not on main yet)
- anything else

## Steps

Start from main 699837d8 (`git fetch origin main && git checkout -B kitchen-followups-046 origin/main`).

1. Stop and close refuse first. Today `stop` clears the queue and logs `send-stop` and only then `Amp.send` raises `Snoozed` (amp.py ~1191, ~1214; joo 8f7e5175). In `commands/stop.py` `stop`, and in `close` (find it from `kitchen.sh close`; it ends every live role), call `amp.require_awake(store, thread)` for every role thread the command will message before the first change (queue, events, pair.json, panes). The refusal lists every snoozed thread at once, each with its `update_thread(thread=<T>, snooze="off")` and the `kitchen.sh snooze <store> --wake <T>...` line. Keep the check in `Amp.send` as the last guard. Tests (fake amp): stop to a snoozed sidekick exits non-zero with the queue, events.tsv and pair.json unchanged; close with a snoozed consultant and an awake sidekick refuses with neither stopped; after `--wake` both run as today. Targeted check: `python3 tests/run.py -k snooze` and `python3 tests/run.py -k stop`.
2. Dispatch refuses before classify and the sweep. The 043 verdict saw `dispatch` to a snoozed sidekick print the classify and playbook lines and run the archive sweep before refusing. Move the `require_awake` check for the sidekick's thread to the start of the kitchen's dispatch (kitchen_core/dispatch.py), before classify, rotation and the sweep; a dispatch that rotates (a fresh thread) is not refused. Test: dispatch to a snoozed sidekick prints only the refusal, and the fake amp records no archive call. Targeted check: `python3 tests/run.py -k snooze` and `python3 tests/run.py -k dispatch`.
3. `snooze --done` refusal names the undo, and refusals get a class. In `snooze.record`: both refusals (master thread; not snoozable now) end with `; if you already called update_thread(... snooze="indefinite"), undo it: update_thread(thread=<T>, snooze="off")`. Add `SnoozeRefused(PairError)` in errors.py (the pattern of the other PairError subclasses there, same exit code as ToolFailed(1) today) and raise it in place of `ToolFailed` in `record`; make `amp.Snoozed` keep its exit 5. Fix `candidates()`'s docstring (snooze.py ~86) to say what it checks, or add the master check it claims; pick the check if `amp.own_thread()` and the master's recorded thread are already at hand, else the docstring. Test: extend 043's refusal rows to assert the undo text. Targeted check: `python3 tests/run.py -k snooze`.
4. Help and docs. `kitchen.sh help` lists `snooze <store> [--done <thread>... | --wake <thread>...]` with a one-line description, next to `nest` in skills/pstack-kitchen/scripts/variant.py (and pstack-pair's usage if pair.sh exposes it). master.md (~line 93) and SKILL.md: every message or relay the kitchen sends to a thread recorded snoozed is refused (dispatch, discuss, consult, steer, verify, queue, stop, close), and `--done` refusing after the Amp snooze means undo it with the printed call. Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- joo 2026-11-24T00-40-25-500Z-8f7e5175 on the third landing (eb8228f9..699837d8): amp.py:1214 and amp.py:1191 mediums (stop/close refuse after clearing the queue), snooze.py:180 medium (= joo 801d9887 on 043), master.md:93 low, snooze.py:86 note, snooze.py:177 low.
- verdicts/043-snooze-role-threads-fix-v1.md observation (dispatch runs classify and the sweep before refusing) and verdicts/landing-v3.md note (`help` does not list `snooze`).
- followups.md, sections 043 and "Landing stack 3".

## Acceptance

- stop and close to a snoozed role refuse with no queue, event, pair.json or pane change and name the wake calls for every snoozed thread; after --wake they run.
- dispatch to a snoozed sidekick prints only the refusal and archives nothing.
- `snooze --done` refusals print the update_thread snooze="off" undo and raise SnoozeRefused.
- `kitchen.sh help` lists snooze; SKILL.md and master.md say every send or relay refuses.
- 038's and 043's tests pass; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k snooze
python3 tests/run.py -k stop
python3 tests/run.py -k dispatch
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

You own the working tree.

Your parts:
- review-fix.intro-1: **Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdi
- review-fix.step-1: 1. Read each finding, the code it names, and the parent unit's brief and report. A finding you canno
- review-fix.step-2: Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the 
- review-fix.step-3: 3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks 
- review-fix.reply: In the report, under Ran and the sections the template names: each finding with its commit, the test

### review-fix.intro-1

**Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdict or a landing review already found each one; the unit that raised them already settled the design.

### review-fix.step-1

1. Read each finding, the code it names, and the parent unit's brief and report. A finding you cannot place, or one whose fix would change the parent's design, goes in the report under Questions; do not redesign around it.

### review-fix.step-2

Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the test that pins it, in the same commit, recorded with kitchen.sh step. A finding with no testable behaviour (a comment, a name, a log line) says so in its commit message.

### review-fix.step-3

3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks for a failing test committed before its fix.

### review-fix.reply

In the report, under Ran and the sections the template names: each finding with its commit, the test that pins it, and the command output that shows it fixed. A finding left open says why. Every claim carries its command output or a file pointer.

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
