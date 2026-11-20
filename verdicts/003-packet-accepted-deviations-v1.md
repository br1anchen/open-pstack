# Verdict 003: packet-accepted-deviations

status: clean
units: 003
range: 6ac18e8e557c12a97f8ff88524d57b4e19630bcd..ff654763b99eebda2561a20f0806e4f169094f7f
verifier: amp claude-sonnet-5-5
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small

## Findings

none

Non-blocking observation: an Amendments body of `none.` (with a period) is not treated as empty and would produce the block; only `none` (any case) and an empty section are. The brief's Acceptance says `none`, so this is within it.

## Evidence

Scratch: git worktree at ff65476 (the packet's scratch path did not exist on this orb, so I created it there). Ran the packet's `kitchen.py ... setup` first.

```
$ kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261120T180206-4037.log)
```

Acceptance 1 and 2 (block shown with rule line then text; none / no note gives no block) and brief Verify commands:

```
$ python3 tests/run.py -k packet
... test_pstack_kitchen.KitchenScriptTests.test_a_covers_packet_gives_a_named_unit_its_accepted_amendments (5.0s)
40 tests, 0 failed, 68s wall with 2 jobs

$ python3 tests/run.py -k covers
40 tests, 0 failed, 84s wall with 2 jobs

$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
```

The new test asserts, per case, on `## Unit 001` of the generated packet: with an amendment the `Accepted by the master (reviews/001-u1.md), whatever the Acceptance above says` rule and the amendment text are both present; with `none` and with no review note both are absent; and the block never appears under `## Unit 003`.

Own probe of `accepted_amendments` (verify_packet.py) with review notes written to a temp store:

```
== multi 'not reject on a departure listed here.\n\nline one\n\nline two\n\n'
== None ''
== empty ''
== last 'it, and do not reject on a departure listed here.\n\n- a\n- b\n\n'
== dotnone '... listed here.\n\nnone.\n\n'
missing ''
```

Multi-paragraph amendments are kept whole and stop at the next `##` section; `None`, an empty section and a missing note return '' (the caller appends '' so packets without a note are unchanged by construction: `parts.append(accepted_amendments(...))` adds an empty string).

Acceptance 3 (docs and check):

```
$ git diff 6ac18e8..HEAD -- skills/pstack-kitchen/references/{master-verify,verifier}.md
master-verify.md: +3 lines under step 6 (accept deviation in review note under ## Amendments; packet gives it to the verifier as accepted) and +3 lines naming the `Accepted by the master (reviews/<unit>.md)` block, absent for no note / `none`.
verifier.md: +3 lines: the block amends that unit's Acceptance; prove as amended, do not reject on a listed departure.

$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
check=0
```

Test hygiene standing check: the new test starts no subprocess of its own beyond the existing `run_sh` helper and applies no mask or normalisation.

## Flows driven

- pstack (tests/skills profile): drove the real `kitchen.sh verify <store> 003 --covers 001` path via the new test, producing real packet files, plus the direct probe above. See Acceptance 1 and 2 evidence.

## Folded Acceptance

none

## Not covered

- The packet's `gate pstack behavioral` command was not run separately; its content is the same test suite, of which I ran the brief's three Verify commands (`-k packet`, `-k covers`, `spec_gen --check`) and `check-pstack.py`.
- Wording quality of the two doc paragraphs was read, not tested; both are 2 to 3 sentences.

## Lessons

none
