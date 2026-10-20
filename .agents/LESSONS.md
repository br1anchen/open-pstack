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

status: active
kind: reject
class: verify
layer: verify
area: skills
since: 2026-10-16
active: 2026-10-16T11:33:46Z
note: owner 2026-10-18: keep active, not promoted (no mechanical check)
evidence:
- 2026-10-16 python-port 003: Acceptance "stdout, stderr, exit codes and store files stay unchanged" fails on one error path (Evidence E5, E6).

## L004: Commit a source change under skills/pstack-pair in the same step as the test that pins it; the test-touch gate refuses a step that changes source without a test.

status: active
kind: gate-fail
class: policy
match: test-touch
layer: policy
area: skills/pstack-pair
since: 2026-10-17
active: 2026-10-17T22:31:31Z
note: owner deferred to master 2026-10-18: 5 test-touch gate failures in 3 runs
evidence:
- 2026-10-16 python-port 002: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-16 python-port 007: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-16 python-port 008: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 2 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-16 python-port-2 002: skills/pstack-pair/scripts/pair-core.sh: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)
- 2026-10-17 python-port-3 014: skills/pstack-pair/scripts/pstack_core/exits.py: [test-touch] profile pstack requires tests: 1 source files changed and no file matching tests/** (.agents/kitchen.toml)

## L005: An A/B helper's test proves it runs HEAD's code: mutate HEAD's side once and see the comparison fail before trusting a pass.

status: proposed
kind: catch
class: review
layer: review
area: skills/pstack-pair
since: 2026-10-17
evidence:
- 2026-10-17 python-port-3 010: leaf_both ran B3 against B3 when no Python snippet was given, so 010's shim-form test never ran HEAD's shims; the master's n3 review read the diff and missed it

## L006: A test that drives a waiting command passes it an explicit small timeout or poll bound, never its default; a hung step gate means a test lacks one.

status: active
kind: steer
class: brief
match: redirect
layer: brief
area: skills/pstack-pair
since: 2026-10-17
active: 2026-10-17T22:31:31Z
note: owner deferred to master 2026-10-18: 2 redirect steers in 2 runs over hung step gates
evidence:
- 2026-10-16 python-port-2 007: redirect: The step gate for f0a7c9a has hung for over 15 minutes. `test_wait_ab_signals_end_the_command_as_they_end_the_bash`, case sig-4, signalled the base bash `pair.sh wait`. But non-interactive …
- 2026-10-17 python-port-3 012: redirect: Dropping `--timeout 2000` from 'a working devin whose text stays typed' makes it run on stop's 300000 ms default. The uncommitted run of `test_stop_matches_..._never_sent_the_text` (pid 425…

## L007: When a change narrows the values a command accepts (a status allow-list, a choice set), grep every caller that sends it a value and add a test per value they send before the step.

status: proposed
kind: catch
class: gate
layer: gate
area: skills/pstack-pair
since: 2026-10-20
evidence:
- 2026-10-18 python-port-4 006: finish's new status allow-list broke plan responses (agree) and guided asks (asking); no test runs finish on either

## L008: A test that waits for a state change triggers it at a fixed point (fake herdr $FAKE/once hooks or a fifo), never waits on time; the test-sleep policy refuses it.

status: proposed
kind: gate-fail
class: policy
match: test-sleep
layer: policy
area: skills/pstack-pair
since: 2026-10-20
evidence:
- 2026-10-16 python-port-2 006: tests/test_pstack_core.py:850: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BUS…
- 2026-10-17 python-port-3 009: tests/test_pstack_core.py:703: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BUS…
- 2026-10-17 python-port-3 010: tests/test_pstack_core.py:1092: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BU…
- 2026-10-20 python-port-4 013: tests/test_pstack_kitchen.py:3040: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR…
- 2026-10-20 python-port-4 029: tests/test_pstack_core.py:9312: [test-sleep] trigger the state change at a fixed point instead (fake herdr hooks: $FAKE/once/<subcommand>, $FAKE/report-after), and tune poll intervals by env (PAIR_BU…
