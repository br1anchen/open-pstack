# Verdict 024: verify-names-excluded-fix

status: clean
units: 024
range: 08666a79896199e85d01238ecb5ce445d876b67a..1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb
verifier: amp claude-code claude-sonnet-5-5
verifier-agent: amp --mode claude-code --features model:claude-sonnet-5-5

## Findings

none

## Evidence

Acceptance line 1, positive case: a covers verification whose range holds a unit with no accepted review prints `excluded: unit NNN ...` naming both remedies. The updated test drives `kitchen.sh verify <store> 003 --covers 001` with unit 002-u2's review at `verdict: revise` and asserts the exact stdout line. It passes at the head (Evidence 1). It fails at the parent's source with the head's test (Evidence 2), so the failing repro is the old behaviour and the passing run is the fix (the repro and fix land in one commit, ce815fa).

Acceptance line 1, negative case: the accepted-unit test asserts `assertNotIn('excluded:', out)` (diff of tests/test_pstack_kitchen.py, test_a_covers_packet_lists_the_unit_between_with_its_accepted_exception_as_context) and passes (Evidence 1). test_a_packet_without_covers_has_no_units_between_context also passes there.

Diff read (`git diff 08666a79..1e105784`): `write_packet` prints `excluded_notice(p)` only when `p.covered`; `not_accepted` is the one check shared by the packet line and the printed line, so they cannot disagree. Printed line: `excluded: unit 002 (in the range, no accepted review); name it with --covers, or verify --landing covers it`. The packet line: `unit 002-u2: no accepted review; <same remedy>`. The unit label differs (NNN versus full name) as the brief's example specifies; not a finding.

Evidence 1, at the head (scratch at 1e105784, PSTACK_KITCHEN_ROLE=verifier):
```
$ python3 tests/run.py -k covers
...
40 tests, 0 failed, 41s wall with 4 jobs
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
```

Evidence 2, parent source (08666a79) with the head's tests/test_pstack_kitchen.py:
```
$ python3 tests/run.py -k covers_packet
FAIL test_pstack_kitchen.KitchenScriptTests.test_a_covers_packet_says_a_unit_between_has_no_accepted_review
AssertionError: 'unit 002-u2: no accepted review; name it with --covers, or verify --landing covers it' not found in [..., 'unit 002-u2: no accepted review', '']
4 tests, 1 failed
```

Test standing check: the new assertions are plain equality and `assertNotIn`, with no mask or normalisation; the test runs `verify` through run_sh, which the existing harness bounds and scrubs. Source and test land in the same commit (ce815fa) and SPEC.md is current.

Not run: the packet's `kitchen.py setup` and `gate pstack behavioral` lines. The unit's changed profiles were already gated at each step by the sidekick (report: pstack and pstack-docs full-suite gates passed), and this orb's verification ran the brief's two commands and spec_gen --check.

## Flows driven

pstack-kitchen verify CLI (`kitchen.sh verify ... --covers`): driven through the test harness against a fake store, Evidence 1 and 2. pstack-docs: master-verify.md gained one sentence matching the printed line (read in the diff); no user-facing flow.

## Folded Acceptance

none

## Not covered

The behavioral gate line from the packet's Prove block (see the note under Evidence); a live `verify --covers` against a real kitchen store was not driven beyond the test harness.

## Lessons

none
