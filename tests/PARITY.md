# Parity with the bash

The Python core replaced the bash of the pair and kitchen entry scripts command by command, and each port was held to the bash at a base commit
(d06a932, e6e83e3, 04d2c13, b632713). Those A/B tests are gone (units 045 and 046 of plan 043). What holds the behaviour now is
[`SPEC.md`](SPEC.md) (one line per behaviour an agent, skill doc or script depends on, each with the test that asserts it; regenerate with
`python3 tests/spec_gen.py`) and the golden transcripts in `tests/pstack-pair/golden`.

## Differences a caller can still meet

Three behaviours differ from what an agent might expect. Each is a line in SPEC.md, section "Decided in plan 043", and a test pins it.

| Behaviour | SPEC.md line (bullet starts with) | Pinned by |
| --- | --- | --- |
| land-check and verify can call a live unit reverted: the revert scan compares no-context patch-ids, so an unrelated identical one-line deletion later in the stack counts. The answer fails closed. | "a known limit of the revert scan" | `test_an_unrelated_identical_one_line_deletion_is_called_reverted_by_the_u0_scan` |
| A close claim held by a process the closer may not signal reads as a dead owner's and is taken over; a pid reused by another process holds the claim while it lives. | "a close claim held by a live process of another user" | `test_claim_close_takes_a_claim_only_from_a_holder_that_is_gone` |
| A landing verdict whose version is not a plain decimal (`landing-v1x.md`) is taken as the newest. | "a landing verdict `landing-vN.md`" | `test_a_landing_verdict_with_a_version_that_is_not_a_number_is_the_newest` |

A host with no `python3` on PATH now ends a script with bash's own `exec: python3: not found` (exit 127) where it printed `pair.sh: python3 is required on PATH` and exited 1; the missing `herdr` line is unchanged.

## History

The table of accepted differences from the bash bases (D1 to D27 and the per-unit rows) is in git: `git show b60b497:tests/PARITY.md`.
It recorded how each port differed from its base (error text naming a line of the old script, jq's messages, a shell exit where Python dies with a
message); none of those is a contract an agent reads today.
