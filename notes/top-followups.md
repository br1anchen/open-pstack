# Top follow-ups from run sidekick-ab2 (master g4)

Chosen for cost to later runs or a real correctness risk. Details: kitchen-gaps.md (gap numbers) and followups.md (all lows).

## Kitchen gaps that cost the last run time
1. rotate-master does not carry local branches the store names (gap 1); take-master should fetch them.
2. The host roster does not travel with rotate-master (gap 2).
3. The amp sidekick rotates on every dispatch, even back-to-back routine units in the same profiles (gap 3).
4. pair.sh sync leaves the master's branch at the pre-rebase head after a role rebased it (gap 4).
5. land-check fails forever on a unit whose verified head was rebased away (gap 5).
6. The verify packet lacks the master's accepted deviations, so verifiers reject on them (gap 7).

## Correctness lows worth fixing
7. amp.py role_is_idle: a sidekick whose unit is still being verified counts as idle, so threads --archive can archive it before a fix brief.
8. gitstore.fetch_code ignores a failed trunk fetch and returns True, though a head on trunk now ships with no bundle.
9. kitchen_core/master.py hand_to_next_thread and take_master ignore a False from gitstore.refreshed; master.py:513 and rotate.py:124-126 let a failing publish hide the original launch/spawn error.
10. kitchen.sh stop after threads --archive starts a fresh consultant thread just to send it STOP; stop to a working amp sidekick queues instead of relaying like steer.
11. tests/run.py -k <text> exits 0 with "0 tests" when the test module fails to import.
