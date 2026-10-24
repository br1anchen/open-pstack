# Kitchen lessons

Rules learned from trouble that repeated in one area across kitchen runs.
`kitchen.py lessons` proposes them from the run ledger, the master writes each
rule, and the human approves every promotion into a check or a standard.
How often each held or recurred is counted on the host, not here.

## L001: A test that waits for a condition polls in a bounded loop marked kitchen-allow: test-sleep with a reason, never another blocking call that slips past the rule.

status: retired
kind: catch
class: policy
layer: policy
area: skills
since: 2026-10-16
note: encoded as the test-sleep policy pattern (Event().wait, asyncio.sleep, select.select) in .agents/kitchen.toml at b724976, pinned by PolicyRuleTests

evidence:

- 2026-10-16 kitchen-perf 011: test-sleep rule misses threading.Event().wait polls; a sidekick used it to sidestep the rule instead of kitchen-allow

## L002: When porting or changing a command that writes events.tsv, compare the exact rows each entry point adds (dispatch, wait, verify, retry) against the base, snapshotting before the call.

status: promoted
kind: catch
class: verify
layer: verify
area: skills
since: 2026-10-16
active: 2026-10-16T11:33:47Z
note: bda04b0 run_sh at-most-one-master-wake guard

evidence:

- 2026-10-16 kitchen-perf 002: kitchen.sh dispatch logs a checkin wake for a swallowed first check-in (core_dispatch without PAIR_QUIET_WAIT), then quiet_wait logs its own: two rows per return
- 2026-10-16 kitchen-perf 002: verify provider path logs verify:provider then execs the fallback retry, which logs a second wake for the same return

## L003: Porting set -e bash: decide each failing assignment, command or pipeline stage by running the base bash, never by reading it, and pin the result with a parity test.

status: retired
kind: reject
class: verify
layer: verify
area: skills
since: 2026-10-16
active: 2026-10-16T11:33:46Z
note: owner 2026-10-20: phase 4 ended bash porting; de-bash units are held to goldens and a behaviour spec (standing order 27)

evidence:

- 2026-10-16 python-port 003: Acceptance "stdout, stderr, exit codes and store files stay unchanged" fails on one error path (Evidence E5, E6).

## L004: Commit a source change under skills/pstack-pair in the same step as the test that pins it; the test-touch gate refuses a step that changes source without a test.

status: promoted
kind: gate-fail
class: policy
match: test-touch
layer: policy
area: skills/pstack-pair
since: 2026-10-17
active: 2026-10-17T22:31:31Z
note: 50dbdd0 AGENTS.md Testing Guidelines; the test-touch policy is the check

evidence:

- 2026-10-16 python-port 002: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-16 python-port 007: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-16 python-port 008: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 2 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-16 python-port-2 002: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-17 python-port-3 014: skills/pstack-pair/scripts/pstack_core/exits.py: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)

## L005: An A/B helper's test proves it runs HEAD's code: mutate HEAD's side once and see the comparison fail before trusting a pass.

status: retired
kind: catch
class: review
layer: review
area: skills/pstack-pair
since: 2026-10-17
note: owner 2026-10-20: the de-bash plan deletes the A/B parity helpers

evidence:

- 2026-10-17 python-port-3 010: leaf_both ran B3 against B3 when no Python snippet was given, so 010's shim-form test never ran HEAD's shims; the master's n3 review read the diff and missed it

## L006: A test that drives a waiting command passes it an explicit small timeout or poll bound, never its default; a hung step gate means a test lacks one.

status: promoted
kind: steer
class: brief
match: redirect
layer: brief
area: skills/pstack-pair
since: 2026-10-17
active: 2026-10-17T22:31:31Z
note: 0786412..55a2e2b tests/run.py per-test timeout with process-group kill, pinned by tests/test_run.py

evidence:

- 2026-10-16 python-port-2 007: redirect: The step gate for f0a7c9a has hung for over 15 minutes. `test_wait_ab_signals_end_the_command_as_they_end_the_bash`, case sig-4, signalled the base bash `pair.sh wait`. But non-interactive …
- 2026-10-17 python-port-3 012: redirect: Dropping `--timeout 2000` from 'a working devin whose text stays typed' makes it run on stop's 300000 ms default. The uncommitted run of `test_stop_matches_..._never_sent_the_text` (pid 425…

## L007: When a change narrows the values a command accepts (a status allow-list, a choice set), grep every caller that sends it a value and add a test per value they send before the step.

status: promoted
kind: catch
class: gate
layer: gate
area: skills/pstack-pair
since: 2026-10-20
active: 2026-10-20T18:16:33Z
note: 957d9b0 test_finish_and_notify_take_every_status_a_template_tells_a_role_to_write, run by pstack-docs fast

evidence:

- 2026-10-18 python-port-4 006: finish's new status allow-list broke plan responses (agree) and guided asks (asking); no test runs finish on either

## L008: A test that waits for a state change triggers it at a fixed point (fake herdr $FAKE/once hooks or a fifo), never waits on time; the test-sleep policy refuses it.

status: promoted
kind: gate-fail
class: policy
match: test-sleep
layer: policy
area: skills/pstack-pair
since: 2026-10-20
active: 2026-10-20T18:16:33Z
note: be6050f test-sleep pattern: float .wait(), time.monotonic/perf_counter, threading.Timer

evidence:

- 2026-10-16 python-port-2 006: tests/test_pstack_core.py:850: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BUS…
- 2026-10-17 python-port-3 009: tests/test_pstack_core.py:703: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BUS…
- 2026-10-17 python-port-3 010: tests/test_pstack_core.py:1092: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BU…
- 2026-10-20 python-port-4 013: tests/test_pstack_kitchen.py:3040: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR…
- 2026-10-20 python-port-4 029: tests/test_pstack_core.py:9312: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BU…

## L009: Before the report, run each Acceptance line as written and paste its output; a line that holds only in spirit, or a check that over-matches, goes to the master as a question.

status: active
kind: reject
class: verify
layer: verify
area: skills/pstack-pair
since: 2026-10-23
active: 2026-10-24T05:56:14Z

evidence:

- 2026-10-16 python-port 003: Acceptance "stdout, stderr, exit codes and store files stay unchanged" fails on one error path (Evidence E5, E6).
- 2026-10-16 python-port-2 008 009: Unit 009 Acceptance "Every line or path mask in the step and wait A/B tests has a comment giving its reason" does not hold. Behaviour is fine; this is the one failing line. Unexplained masks (Evidenc…
- 2026-10-17 python-port-3 008: The "working Devin never shown the text" stop parity case is nondeterministic and fails the unit's own behavioral gate. `test_stop_matches_the_bash_at_the_phase_3_base_when_a_working_devin_is_never_s…
- 2026-10-17 python-port-3 008 012: `KNOWN_DEAD` grew, which the shared Acceptance line forbids ("the guard passes with `KNOWN_DEAD` not grown"). Against the range base 68c0bd0 it gained six entries: `pstack-kitchen/scripts/kitchen.sh:…
- 2026-10-23 python-port-4 010: Commit 681e535 deleted `test_finish_without_a_live_wait_differs_from_the_bash_only_in_what_notify_marks_seen`. The brief's step 4 removes only `test_damaged_store_shapes_differ_...`, and `test_spawn_…

## L010: Before the report, run each Acceptance line as written and paste its output; a line that holds only in spirit, or a check that over-matches, goes to the master as a question.

status: active
kind: reject
class: verify
layer: verify
area: skills/pstack-kitchen
since: 2026-10-23
active: 2026-10-24T05:56:14Z

evidence:

- 2026-10-16 python-port-2 008 009: Unit 009 Acceptance "Every line or path mask in the step and wait A/B tests has a comment giving its reason" does not hold. Behaviour is fine; this is the one failing line. Unexplained masks (Evidenc…
- 2026-10-17 python-port-3 008: The "working Devin never shown the text" stop parity case is nondeterministic and fails the unit's own behavioral gate. `test_stop_matches_the_bash_at_the_phase_3_base_when_a_working_devin_is_never_s…
- 2026-10-17 python-port-3 008 012: `KNOWN_DEAD` grew, which the shared Acceptance line forbids ("the guard passes with `KNOWN_DEAD` not grown"). Against the range base 68c0bd0 it gained six entries: `pstack-kitchen/scripts/kitchen.sh:…
- 2026-10-23 python-port-4 050: Acceptance line 2 ("The harness tripwire is on for every pair.sh and kitchen.sh run in the suite") does not hold. `watch_jq` is called only from `CoreTests.setUp` (tests/test_pstack_core.py:95) and `…
- 2026-10-23 python-port-4 071: Acceptance line 1 is not met literally. The grep prints one hit: `skills/pstack-pair/scripts/pstack_core/wait.py:47: unowned: bool = False`, a `WaitOutcome` dataclass field. The brief allows only the…

## L011: A gate that fails under load and passes on rerun hides a race: write polled files via temp+rename, wait on a pidfd or fifo before checking a process; never accept the rerun.

status: proposed
kind: gate-flaky
class: pstack
layer: gate
area: skills
since: 2026-10-24

evidence:

- 2026-10-16 python-port 003: 003-templates-pause-progress
- 2026-10-16 python-port-2 009: 009-plan-gates-fixes
- 2026-10-24 python-port-4 025: 025-units-and-landing
- 2026-10-24 python-port-4 039: 039-take-upstream-0-15-29
- 2026-10-24 python-port-4 067: 067-clean-u2-io
