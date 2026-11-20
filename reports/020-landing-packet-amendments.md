# Report 020: landing-packet-amendments

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/020-landing-packet-amendments.md
playbook: review-fix
branch: main
head: c5cf25045919b5390bebab589b40d39e79a036c0
tree: clean

## Changed

Started from 507870a (unit 019's head, this checkout).

```
$ git diff --stat 507870a..HEAD
 .../scripts/kitchen_core/verify_packet.py          |  1 +
 tests/SPEC.md                                      |  6 +--
 tests/test_pstack_kitchen.py                       | 43 +++++++++++++++++++++-
```

- Finding (joo low, verify_packet.py:468), c5cf25045: `landing_sections` now appends `accepted_amendments(p.store, b)` after each folded unit's `## Acceptance`, the same block unit and `--covers` packets carry. A unit with its own clean verdict is not folded, so it gets none.
- Test: new `test_a_landing_packet_gives_each_folded_unit_its_accepted_amendments` beside the other landing-packet tests, and `landing_world` takes a `reviews` argument (unit name, text) written before the packet is made. Rows: folded unit with an amendment shows the `Accepted by the master (reviews/001-util.md), whatever the Acceptance above says` line and ends with the text; folded unit with Amendments `none` shows nothing; a verified (not folded) unit with an amendment is not in the packet; the packet holds exactly one block.
  Deviation from the brief's wording: it said to extend unit 003's table test; that test drives a `--covers` packet through `three_units`, and a landing needs `landing_world`, so the landing row is its own test.

## Ran

```
$ python3 tests/run.py -k packet   -> 41 tests, 0 failed
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format / ruff check / uv check --frozen -> all passed
```

Without the source change (skills/ stashed) the new test FAILS; with it, it passes. Step 1 recorded with `gates: pass (pstack,pstack-docs)`.

## Self-check

figures: every value above comes from the commands shown
acceptance: (1) a landing packet folding a unit whose review has a non-empty Amendments shows that unit's `Accepted by the master` block (asserted); packets for units without amendments are unchanged (Amendments `none` shows nothing; every other landing packet test passes unchanged). (2) unit 003's `--covers` test passes unchanged in the `packet` selection.
callers: `accepted_amendments` now has two callers, `unit_sections` and `landing_sections`; `landing_world` has one new optional parameter, existing callers unchanged.
fresh: tests ran in this checkout, no cache
gate: format, ruff check and uv check --frozen pass; lesson L012: no Optional stdlib result added

## Deviations

- See Changed: the landing row is its own test, not a row of unit 003's table test.
- The installed build I run under does not hold unit 019's fix, so I still ran `kitchen.sh` through .amp/in/orb-backup/kitchen-nostamp.py.

## Questions

none

## Follow-ups

- Each step's gates take about 7 to 10 minutes of repeated `step` reruns; not investigated.
