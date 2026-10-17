# Parity with the bash

The Python core (`skills/pstack-pair/scripts/pstack_core/`, `P` below) replaced the bash of `pair-core.sh` command by command. The parity suite
(`tests/test_pstack_core.py`) runs the bash at a base commit against the Python on shared fixtures and fake agents. This file says which base is the
oracle for what, and lists every difference from the bash that was accepted, with the test that pins it.

## The parity bases

| Base | Oracle for | In the tests |
| --- | --- | --- |
| d06a932 | phase 1: the helpers and the first ported commands | the original `bash()` harness against the tree it was cut from |
| e6e83e3 | phase 2: wait, finish, notify, report, next, queue, step, dispatch, discuss, the plan gates, `report_ready`, `steps_due`, `open_notes` | `base_skills()` extracts `skills/` at that commit; `DELETED_BASH` names the helpers whose bash is gone from HEAD, and `bash()` runs any snippet that names one against this tree, asserting the tree defines it |
| 04d2c13 | phase 3: init, permission, stop, steer, answer, spawn, rotate, failover, the pane helpers, `spawn_role`, the preflights, `stop_prime_role` | `phase3_skills()`; `phase3_ab()` and `leaf_both()` run a world through 04d2c13's script and the working tree's and compare stdout, stderr, status, the store, events.tsv rows and calls.log |

A test that drives deleted bash always reads it from one of these trees, never from HEAD, and a comparison is never replaced by a literal where a
base can still run.

## Accepted differences

Each row was run against its base at the unit that wrote this file (017) unless it says "not re-run". "Pinned" names the test that fails when the
behaviour changes. Where a row is unpinned, the input is a hand-edited or damaged store or a host the tests do not model.

| Id | Input | Bash base | Port | Where | Pinned by |
| --- | --- | --- | --- | --- | --- |
| D6 | an MCP name with non-ASCII letters (`café`) under `LC_ALL=en_US.UTF-8` | `[[ $name =~ ^[A-Za-z0-9_-]+$ ]]` accepts it (locale ranges); the name is disabled with `-c` | rejects it (ASCII only); status 1 from the loop | `P/agents.py:mcp_args` | unpinned |
| D7 | `preflight_kind muse --model` (a trailing flag with no value) | the loop never ends | skips the flag and returns | `P/agents.py:preflight_muse` | unpinned |
| D8, D13 | herdr fails on `pane split` or `tab create`, or answers with non-JSON | `new_pane_id="$(herdr ... \| jq ...)"` ends a caller whose errexit is on with herdr's status and no message | dies 2 with `pane split returned no pane id` | `P/panes.py:new_pane` | `SPAWN_ERREXIT_DIFFERENCE` (`a split that fails`, `a tab that fails`); the non-JSON reply is unpinned |
| D10 | recorded `start_args` or `fallback.args` that are not strings (objects, `1e100`) | `jq -r` prints an object over several lines (`mapfile` makes several arguments) and writes `1E+100` | one Python repr argument; `1e+100` | `P/spawn.py:mapfile_args` | unpinned (store text differs) |
| D11 | `.sidekick.fallback` is a string | jq's `Cannot index string with string ("kind")` first, then the same die or exit 5 | the same die without jq's line (`spawn`), `no fallback recorded` instead of jq's exit 5 (`failover`) | `P/spawn.py:spawn_sidekick`, `P/commands/failover.py` | unpinned |
| D12 | trio spawn with no `--sidekick`/`--consultant` and `.sidekick` or `.consultant` not an object | jq error, exit 5 | `--sidekick KIND is required`, exit 1 | `skills/pstack-trio/scripts/variant.py:spawn` | unpinned |
| D14 | `failover` or `rotate` with `.sidekick` a string or array; `failover` with `.dispatch` a string | jq error and exit 5 (`.dispatch`: exit 5, no failover) | the jq error, then the port's own die (exit 5); `.dispatch` string: failover runs and exits 0 | `P/commands/failover.py`, `P/commands/rotate.py` | unpinned |
| D15 | the shell that ran the command dies alone | not re-run: the bash command runs on | `die_with_the_shell` sets `PR_SET_PDEATHSIG`, so the Python ends with its parent | `P/wait.py:die_with_the_shell` | the shim's signal status: `test_the_stop_prime_role_shim_returns_a_signal_status...`; the rest unpinned |
| D16 | a non-object element in the daemon's session list after the stop; process info that is not JSON | jq errors for a non-null, non-object element (null counts as no match); `jq -e` fails on bad JSON | returns False without jq's message; a null row counts as no match | `P/exits.py:stop_prime_role`, `shell_owns_foreground` | the null row: `a daemon whose list holds a null row after the stop`; the rest unpinned |
| D17 | `finish` or `notify` on a store without pair.json | not re-run (python-port-2 landing review g3): the bash printed the message once or twice by report status and exited 7 | the message count and exit code are copied by status | `P/commands/report.py:finish`, `notify` | unpinned beyond the common cases |
| D18 | a queue that is a directory; `heads: false`; mixed `pending` | not re-run (landing review g3): jq's own errors | Python's reading of the value | `P/dispatch.py:take_queued`, `record_dispatch` | unpinned |
| D19 | a bash error that names the script and line (`$2: unbound variable`) | the file and the line of the original `$2` | the file and the line of the one-line shim | `P/wait.py:core_line` | masked in every A/B; the shim's line is asserted where it is produced |
| D20 | a value `bash_int` cannot read (a `.seen` mark that bash would read as octal) | not re-run (landing review g3): bash arithmetic | `bash_int` returns None and the caller reads 0 | `P/replies.py`, `P/checkin.py`, `P/store.py:bash_int` | unpinned |
| D21 | `.sidekick` false or not an object; `.pending` of other shapes | not re-run (landing review g3): jq errors | the section/alt reading of the value | `P/wait.py`, `P/send.py`, `P/replies.py` | unpinned |
| D22 | `record_dispatch` inside `send_and_wait` | not re-run (landing review g3) | takes no `now` argument: the clock is read inside | `P/send.py` | tests fix the clock on both sides |
| D23 | `init` through kitchen.sh after a mkdir or redirect failure (read-only parent, a pair.json that is a directory, dead symlinks) | the error text names kitchen.sh as the source of the failing line | names pair-core.sh | `P/commands/init.py` | `init_failure_cases` in the init A/B (the kitchen entry is excepted there) |
| D24 | a non-numeric parent pid while walking up to find the master's agent | not re-run | the walk ends (`int()` fails) | `P/permission.py` | unpinned |
| D26 | `.session` of a Prime role that is not an object | not re-run | `prime_active_session` returns None | `P/prime.py:prime_active_session` | unpinned |
| record_failover, spawn | `.sidekick.failovers` is not an array | the jq write fails, its error is printed, and the failover goes on (errexit is off under `\|\| rc=$?`) | the same (`errexit_off=True`) | `P/spawn.py:record_failover` | `a failover whose record fails` in the spawn A/B |
| record_failover, failover | the same store | `cmd_failover` runs it as a plain command under errexit: exit 5 with jq's message and no event | the same (`errexit_off=False`) | `P/commands/failover.py` | `a failover record that jq refuses` in the failover A/B |
| rotate, spawn_role's writes | a `jq` write inside `spawn_role` that fails when the caller has errexit on (rotate, kitchen.sh `consultant`) | the shell ends | the write's error is printed and the start goes on (the spawn path's behaviour) | `P/spawn.py:spawn_role` | unpinned |
| rotate, split failure | a failing `pane split` under a direct `rotate` | silent exit with herdr's status | dies 2 with a message (see D8) | `P/panes.py:new_pane` | unpinned |

Run against the base for this file: D6, D7 (on this host: bash 5.3, jq 1.8), D10, D11, D12, D14 (through the 04d2c13 A/B helpers on the fake herdr; the
differences are what they printed), D8/D13 and the two `record_failover` rows (their pins pass), D16's null row. The rows marked "not re-run" are as
the reviews and audits of units 006 to 015 recorded them.
