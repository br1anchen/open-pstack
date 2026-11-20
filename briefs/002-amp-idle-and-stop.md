# Brief 002: amp-idle-and-stop

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/002-amp-idle-and-stop.md

## Goal

Under Amp threads, `threads --archive` keeps a sidekick whose unit is still being verified, and `kitchen.sh stop` neither starts a fresh thread for a role whose thread was archived nor queues STOP behind a working role's turn: it relays STOP the way `steer` relays a steer.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/commands/stop.py
- skills/pstack-pair/scripts/pstack_core/commands/steer.py
- skills/pstack-trio/scripts/variant.py
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md
- tests/pstack-pair/golden/**

## Steps

1. **A sidekick under verification is not idle** (item 7). In `skills/pstack-pair/scripts/pstack_core/mux/amp.py`, `role_is_idle` (about L476-490), the sidekick branch returns True when `rotation_required`, nothing queued and the brief's report is `done`; it never reads `pair.json` `verifying`, which `kitchen_core/verify_launch.py` (about L392-408) writes while a verifier works and `verify_state.drop_verifying` clears. Add `and not doc.get('verifying')` to the sidekick branch. In the same commit drop the redundant `role in ('sidekick', 'consultant')` test before `role_is_idle` in `assess` (about L514); `role_is_idle` already returns False for other roles. Extend the table in `AmpKitchenTests.test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use` (tests/test_pstack_kitchen.py about L15152; rows like `sidekick_done_idle` at about L15111) with a row: sidekick's unit done but `pair.json` has `verifying` naming it, expected kept/in use. Commit, `kitchen.sh step`.
2. **stop skips a role with no thread** (item 10a). `Amp.archive` (amp.py about L984-1000) keeps an archived consultant's registry entry with `thread: ''`; `Amp.agent` (about L808-823) then reports it `idle`, and the trio `stop` (`skills/pstack-trio/scripts/variant.py` about L32-70, loop at L47, absent check at L52) calls `stop_role`, whose `prompt` starts a fresh thread only to send it STOP. In the trio stop loop, after the absent check, skip a role whose agent has no session (`panes.agent_session(agent)` is `''`, which is how Amp reports an empty thread) when the backend is one that starts a thread on prompt: use a capability attribute on the mux the way `steers_running` and `relays` are (amp.py about L687-689), for example `starts_on_prompt = True` on Amp and False on the base class, rather than a check of the backend's name. Print `stop: <name> has no thread; nothing to stop`. Apply the same skip in the pair's own stop loop in `pstack_core/commands/stop.py` (about L141-158) if it can reach the same state; say in the report whether it can. Test in `AmpKitchenTests`, modelled on `test_the_next_consult_starts_a_new_thread_on_the_same_model_after_an_idle_consultant_was_archived` (about L15197): archive an idle consultant, run `kitchen.sh stop`, assert the skip line and that no new thread started (the fake amp records `amp -ox` starts). Commit, step.
3. **stop relays to a working Amp role** (item 10b). In `pstack_core/commands/stop.py`, `stop_role` (about L101-127), the working branch calls `mux.get().prompt(...)` and polls `stop_written`; under Amp that queues behind the turn. Before it, when `not mux.get().steers_running and mux.get().relays`, relay the STOP text with `mux.get().relay(name, text, store)` (amp.py about L1217) and print the same three `relay:` lines `steer` prints, then return without the `stop_written` poll (the master must upload and send first). Do not duplicate the printing: factor the part of `relay_steer` (steer.py about L86-109) that prints the `relay-pending`/`relay:` lines into a function both use; keep `relay_steer`'s own event (`send-steer`) and log `send-stop` with `relay-pending` for the stop. A relay that cannot be made (relay returns nothing: bundle too big, no .git) falls back to the queued prompt as now. Test in `AmpKitchenTests`, modelled on `test_a_steer_to_a_working_thread_is_relayed_through_the_master_once` (about L16789): a working sidekick, `kitchen.sh stop`, assert `relay-pending:` and the `upload_thread_file`/`send_thread_message`/`relay-done` lines and no `amp threads continue` queued to that thread. Commit, step.

## Context

- Follow-ups from run sidekick-ab2: "amp.py role_is_idle: a sidekick whose unit is still under verification (pair.json verifying) counts as idle, so threads --archive can archive it before a fix brief" (joo 035); "kitchen.sh stop after threads --archive starts a fresh consultant thread only to send it STOP" (joo 037); "stop to a working Amp sidekick queues behind its turn instead of relaying like steer" (gap 8).
- steer's relay decision: steer.py about L114-123 (`if not mux.get().steers_running and mux.get().relays and relay_steer(...)`).

## Acceptance

- `threads <store> --archive` keeps a sidekick whose unit report is done while `pair.json` `.verifying` exists, and archives it once `.verifying` is cleared (the existing done-and-idle row still archives).
- After an idle consultant was archived, `kitchen.sh stop <store>` prints `stop: kitchen-...-consultant has no thread; nothing to stop` (or the role's name) and starts no thread.
- `kitchen.sh stop` to a sidekick whose Amp thread is in a turn prints `relay-pending: <id> for <name>` and the three `relay:` lines and queues nothing; to an idle sidekick it sends STOP by the CLI as before.
- Herdr and Rex stop behaviour and the trio goldens are unchanged.

## Verify

```bash
python3 tests/run.py -k threads_archive
python3 tests/run.py -k stop
python3 tests/run.py -k relay
```

## Forbidden

- Do not change `Amp.archive`'s registry shape (the entry with `thread: ''` stays; other code reads it).
- Do not regenerate goldens.

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
