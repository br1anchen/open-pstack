# Report 003: packet-accepted-deviations

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/003-packet-accepted-deviations.md
playbook: review-fix
branch: main
head: ff654763b99eebda2561a20f0806e4f169094f7f
tree: clean

## Changed

Started from 6ac18e8e557c12a97f8ff88524d57b4e19630bcd (fetched `origin kitchen-code/kitchen-followups/master-g1`, `merge --ff-only` from a90bb41).

```
$ git diff --stat 6ac18e8..HEAD
 skills/pstack-kitchen/references/master-verify.md  |  6 +++++
 skills/pstack-kitchen/references/verifier.md       |  3 +++
 .../scripts/kitchen_core/verify_packet.py          | 23 ++++++++++++++----
 tests/SPEC.md                                      | 12 +++++-----
 tests/test_pstack_kitchen.py                       | 27 ++++++++++++++++++++++
```

- Step 1, e57fe3955: `verify_packet.py` gets `ACCEPTED_RULE`, `review_amendments(review)` (the extraction `context_section` used, now shared) and `accepted_amendments(store, brief)`; `unit_sections` appends its block after each named unit's Goal/Acceptance. Test `test_a_covers_packet_gives_a_named_unit_its_accepted_amendments` (subTests: an amendment, `none`, no review note). SPEC.md regenerated.
- Step 2, ff654763b: master-verify.md (two sentences at the `--covers` fix paragraph, two at the units-between paragraph) and verifier.md (one sentence); SPEC.md citations regenerated.

## Ran

```
$ python3 tests/run.py -k packet   -> 40 tests, 0 failed
$ python3 tests/run.py -k covers   -> 40 tests, 0 failed
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
$ python3 scripts/check-pstack.py  -> 65 skills checked; all 48 previous skills covered
$ uv run --frozen ruff format / ruff check / uv check --frozen -> all passed
```

New test fails without the source change (stashed verify_packet.py: `FAIL ... [an amendment]`). Gates: both steps `gates: pass (pstack,pstack-docs)` and recorded.

## Self-check

figures: every value above comes from the commands shown
acceptance: (1) a `--covers` packet for a fix of 003 over 001 with a non-empty Amendments shows the `Accepted by the master (reviews/001-u1.md), whatever the Acceptance above says` line and the amendment text in `## Unit 001` (subTest "an amendment"). (2) Amendments `none` and no review note give no block (subTests 2 and 3); `## Unit 003` never carries one; no-review packets are unchanged because the helper returns '' (existing packet tests pass unchanged). (3) master-verify.md and verifier.md describe the block; check-pstack passes.
callers: `context_section` now calls `review_amendments` (same output, `or 'none'` kept); its existing test passes unchanged. `unit_sections` is the only caller of `accepted_amendments`.
fresh: tests ran on this checkout, no cache involved
gate: format, ruff check and uv check --frozen pass; lesson L012: no Optional stdlib results added (os.path.basename and section_lines only)

## Deviations

- Step order: I committed the docs before recording step 1, and `kitchen.sh step` refused a non-HEAD sha, so I reset to e57fe39, recorded step 1, and reapplied the docs commit as step 2 (patch kept at .amp/in/orb-backup/step2.patch). Same content, two commits.
- The brief's amendment test of "empty or none" treats the section case-insensitively (`None`, `none`); an own unit's review (not only covered) also gets its block, since the brief says "each unit it names".
- review.self: .agents/kitchen.toml names no `review.self`, so I ran none; joo is the master's per order 19.
- The git-store startup-stamp bug from report 000-ready-g3 still applies: I ran `kitchen.sh` through .amp/in/orb-backup/kitchen-nostamp.py (skips the variant's startup stamp only).

## Questions

none

## Follow-ups

- The "gates" for each step took about 8 to 10 minutes of repeated "still running" reruns (the step command returns at ~100 s and needs reruns); not investigated.
