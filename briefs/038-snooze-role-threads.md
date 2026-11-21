# Brief 038: snooze-role-threads

playbook: feature
timebox: 120
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

Under Amp threads the master snoozes a role thread (sidekick, consultant, verifier, helper) only once it has no open work and its turn is settled, and unsnoozes it before the kitchen sends it new work, so Brian sees active role threads and master threads and nothing else; master threads and threads blocked on a question or an error are never snoozed, and `PSTACK_SNOOZE_ROLES=0` turns the snoozing off (on by default).

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/commands/snooze.py — new
- skills/pstack-pair/scripts/pstack_core/cli.py — register `snooze` beside `nest`
- skills/pstack-pair/scripts/pstack_core/commands/status.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py — the wake check and the one helper step 1 extracts
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- tests/test_pstack_kitchen.py
- tests/SPEC.md
- tests/pstack-pair/bin/amp

must not write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py (threads are not snoozed when they start; nesting is unchanged)
- anything else

## Steps

Start from the rebased stack 037 leaves (main be882968 plus the run's units). The amp CLI cannot snooze: the master does it with its own `update_thread` tool, as it does the nesting, and the kitchen records what it did, as `nest-pending.tsv` does.

1. `commands/snooze.py`, modelled on `commands/nest.py` (its `rows()`, `stamp()`, `event()` use and its `nest`/`done` shape). A record `snooze.tsv` in the store, one row per change: `time, thread, role, state` with state `snoozed` or `awake`; the newest row of a thread is its state; append only, exactly 4 cells, so the `.tsv` union merge of bundles works. `SNOOZE = 'PSTACK_SNOOZE_ROLES'`; `enabled()` is True unless `os.environ.get(SNOOZE) == '0'`. `snoozable(store, thread, role)`, True only when all hold: role is not `master`; `amp.thread_state(amp.export(thread))[0] == 'idle'` (not working, not `error`, not `cancelled`, not unknown); no message or relay to it is pending — extract the outbox check and the relay check from `amp.assess` into one helper in mux/amp.py, `pending_to(store, thread, since)` returning the reason or '', and use it from both `assess` (unchanged behaviour) and `snoozable`; and, when the thread is a live role (`amp.live_threads`): the sidekick when `amp.role_is_idle(store, doc, 'sidekick')` and `reviews/<NNN-slug>.md` of the dispatched brief exists (accepted or rejected); the consultant when `amp.role_is_idle(store, doc, 'consultant')`; a verifier when `pair.json .verifying` is null. The candidates are the rows `kitchen.sh threads <store>` lists (the store's thread registry, the same function), not archived. Command `snooze <store>`: with `enabled()` false print `snooze: off (PSTACK_SNOOZE_ROLES=0)` and exit 0; else for each candidate that is snoozable and not recorded snoozed print `snooze: <T> (<role>)`, then one line `snooze: call update_thread(thread=<T>, snooze="indefinite") for each, then <prog> snooze <store> --done <thread>...`, or `snooze: nothing to snooze`. `--done <T>...` appends `snoozed` rows and a `snooze` event; `--wake <T>...` appends `awake` rows and a `wake` event; both refuse a thread the store's registry never named, as `nest --done` does. Tests: one parameterised table over the fake amp (state idle/working/error/cancelled x role sidekick-with-review/sidekick-without-review/consultant-with-open-consult/consultant-answered/verifier-verifying/verifier-done/master) -> listed or not; plus `PSTACK_SNOOZE_ROLES=0` lists nothing; plus `--done` then `--wake` leaves the thread awake and logs both events. Targeted check: `python3 tests/run.py -k snooze`.
2. Wake before work, in mux/amp.py: `require_awake(store, thread)` raises a PairError subclass when `snooze.tsv` records the thread `snoozed`, with the message `snoozed: <T> is snoozed; call update_thread(thread=<T>, snooze="off"), then kitchen.sh snooze <store> --wake <T>, then run this again`. Call it where the kitchen continues an existing role thread (`Amp.with_bundle`, before anything is packed), and also wherever needed so that `dispatch`, `discuss`, `consult` and `steer` refuse before they record anything: a test dispatches to a snoozed sidekick and asserts the exit is non-zero, the message names `snooze="off"`, and `pair.json .dispatch` and the queue are unchanged; after `--wake` the same dispatch sends. The check applies whatever `PSTACK_SNOOZE_ROLES` says (a thread snoozed earlier still needs waking). A fresh thread (rotation, a new verifier) has no row and is never refused. Targeted check: `python3 tests/run.py -k snooze` and `python3 tests/run.py -k dispatch`.
3. `status.py`, beside the `nest.status_line(store)` call: with `enabled()`, `snooze-due: N role threads; run kitchen.sh snooze <store>` when N > 0 threads are snoozable and not recorded snoozed; and for each live role thread whose state is `error`, `needs-human: <role> <T> (error); relay it to the human from the master thread` (whatever the switch says). Test: fake amp with one idle reviewed sidekick and one consultant in `error`. Targeted check: `python3 tests/run.py -k status`.
4. Docs. `skills/pstack-kitchen/SKILL.md`, in the Amp threads section beside the nesting bullet: a new bullet for the snooze — when it happens (no open work, settled turn), never a master or a thread blocked on a question or error, `kitchen.sh snooze` and `--done`/`--wake`, the refusal that makes the master unsnooze first, `PSTACK_SNOOZE_ROLES=0`; add `snooze.tsv` to the Store additions table. `skills/pstack-kitchen/references/master.md` step 5: run `kitchen.sh snooze` at a `snooze-due:` line (after a review note, an answered consult, a verdict), unsnooze on the refusal, and a snoozed or not, a role blocked on a question for the human or in an error only the human can fix (`needs-human:`, a `blocked` report with Questions, an approval dialog) is relayed to the human from the master thread. Regenerate `tests/SPEC.md` (`python3 tests/spec_gen.py`). Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Brian's request (2026-11-21, relayed by Puck T-01a1eafa-f54b-71ff-890f-07428ab9c3e3), revised: do not snooze at start; active role threads stay visible so he can tell a run is stuck; snooze once there is no open work and the state is settled; unsnooze (`snooze="off"`) before new work; never snooze masters or threads blocked on a question or error; off switch, fake-amp tests, docs.
- Patterns: `commands/nest.py` (record file, `--done`, refusal of unknown threads, `PSTACK_NEST_VIA` env knob) and its tests around `test_nest_lists_exactly_the_pending_rows_and_says_to_call_update_thread` in tests/test_pstack_kitchen.py; `amp.assess`, `amp.role_is_idle`, `amp.open_consults` in mux/amp.py.
- Standing orders 24, 27, 29.

## Acceptance

- `kitchen.sh snooze <store>` lists exactly the role threads that are idle with no open work (sidekick reviewed, consultant answered, verifier not verifying, nothing pending to it), and never a master, a working thread or a thread in `error`; `PSTACK_SNOOZE_ROLES=0` lists none.
- `--done` and `--wake` record the state and log `snooze`/`wake` events; an unknown thread is refused.
- A dispatch (and a discuss, consult or steer) to a thread recorded snoozed refuses before recording anything and names `update_thread(... snooze="off")`; after `--wake` it sends.
- `kitchen.sh status` prints `snooze-due:` and `needs-human:` lines as step 3 says.
- `amp.assess` behaves as before (the existing threads/archive tests pass unchanged).
- SKILL.md and master.md describe the snooze, the wake, the off switch and the relay duty; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k snooze
python3 tests/run.py -k dispatch
python3 tests/run.py -k status
python3 tests/run.py -k archive
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No change to nest.py and no snooze at thread start.
- No new amp CLI subcommand calls beyond the `export` the kitchen already makes.
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
