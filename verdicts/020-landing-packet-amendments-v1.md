# Verdict 020: landing-packet-amendments

status: clean
units: 003 019 020
range: 6ac18e8e557c12a97f8ff88524d57b4e19630bcd..c5cf25045919b5390bebab589b40d39e79a036c0
verifier: amp anthropic/claude-sonnet-5-5
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small

## Findings

none

## Evidence

Acceptance 003.1 (--covers packet shows `Accepted by the master` rule + amendment text) and 003.2 (Amendments `none` or no review note: no block):

```
$ python3 tests/run.py -k covers      # scratch at c5cf250
40 tests, 0 failed, 57s wall with 2 jobs
$ python3 tests/run.py -k packet
41 tests, 0 failed, 49s wall with 2 jobs; exit 0
```

test_a_covers_packet_gives_a_named_unit_its_accepted_amendments (tests/test_pstack_kitchen.py:8835) subtests: an amendment -> rule line `Accepted by the master (reviews/001-u1.md), whatever the Acceptance above says` and text inside `## Unit 001`; `none` -> no block; no review note -> no block; the fix unit's own section never carries a block.

Same tests against the unit's parent code (second scratch at 6ac18e8, head's tests/test_pstack_kitchen.py copied in), proving they fail without the fix:

```
$ python3 tests/run.py -k gives_a_named_unit_its_accepted_amendments
FAIL: test_a_covers_packet_gives_a_named_unit_its_accepted_amendments [an amendment]
AssertionError: False != True
1 tests, 1 failed
```

Acceptance 003.2, byte-identical packets without review notes: I built the same --covers packet from the same fixture (three units, no review notes) at 6ac18e8 and at c5cf250 and diffed them after normalising /tmp dirs:

```
$ diff <(sed -E 's#/tmp/tmp[a-z0-9_]+#<TMP>#g' base.md) <(sed ... head.md)
4c4  template: .../020-base-verify/... vs .../020-landing-packet-amendments-verify/...
6c6  range: <fixture commit hashes differ between two fixture builds>
148c148  gate line: scratch tree path and the same fixture hashes
```
Only the scratch tree path and the fixture's random commit hashes differ (the two fixture repos are separate); no content line differs. (The normalisation is mine, in my probe, not in a repo test.)

Real amendment text, multi-paragraph and `None.` edge, printed from a real `kitchen.sh verify ... --covers` packet:

```
Accepted by the master (reviews/001-u1.md), whatever the Acceptance above says: prove the Acceptance as these amend it, and do not reject on a departure listed here.

first para.

second para: accepted.
```
and for an Amendments text `None.` the block is still printed (the code treats only '' or the exact word `none`, case-insensitive, with no period, as empty). That is the pre-existing convention of context_section ('none'), and the review note template uses `none`; not an Acceptance failure, noted under Not covered.

Acceptance 003.3 (docs, check-pstack):

```
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered      (exit 0)
$ git diff 6ac18e8..HEAD -- skills/pstack-kitchen/references/master-verify.md verifier.md
master-verify.md: +3 lines "A named unit's own section carries its review's Amendments too ... Accepted by the master (reviews/<unit>.md) ..." (+3 lines on recording the amendment in `## Amendments` before the fix is sent)
verifier.md: +3 lines "A unit's section may carry an `Accepted by the master` block: ... Prove the Acceptance as amended and do not reject on a departure the block lists."
```
Each is 1-2 sentences describing the block.

Brief command 3:

```
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing      (exit 0)
```

Acceptance 019.1 (git store: a sidekick's `kitchen.sh finish` succeeds with a differing skills directory, branch pair.json unchanged) and 019.2 (master from a new skills directory still appends a row):

```
$ python3 tests/run.py -k skill_build
2 tests, 0 failed, 6s wall with 2 jobs
```
test_a_role_from_another_skill_build_runs_kitchen_commands_and_a_master_still_stamps_its_build (AmpGitKitchenTests): sets the role's local pair.json last skill_builds path to `/old/skills`, runs `finish` as the role orb, asserts `git show kitchen/demo:pair.json` is identical before and after, the report is on the branch, the role's own skill_builds is unchanged, then sets the master's last path to `/old/skills`, runs `status` as the master and asserts the list gains `here` as its last row. test_record_skill_build_stamps_a_later_skills_directory_on_a_stamped_run_only (core) passes too.

Original failure, reproduced on the unit's parent code with head's tests (second scratch at 6ac18e8):

```
$ python3 tests/run.py -k a_role_from_another_skill_build
AssertionError: 1 != 0 : kitchen.sh: pair.json: belongs to the master (amp:T-aaaaaaaa-0000-4000-8000-000000000000), not to this thread (amp:T-00000000-0000-4000-8000-000000000001)
1 tests, 1 failed
```
The same error text hit me as a role thread running `kitchen.sh scratch` at this packet's parent-era skills build, on the real surface:
```
kitchen.sh: pair.json: belongs to the master (amp:T-01a1f946-...), not to this thread (amp:T-01a1fa77-...)
```
(That run used the installed build, which does not contain 507870a; the installed skills directory differs from the last skill_builds row.)

Acceptance 020.1 (landing packet shows each folded unit's `Accepted by the master` block, unchanged for units without amendments):

```
$ python3 tests/run.py -k packet     # includes test_a_landing_packet_gives_each_folded_unit_its_accepted_amendments
41 tests, 0 failed
```
The test builds a landing packet with three units: 001 folded with an amendment (block shown after its Acceptance, ends with the amendment text), 002 folded with `none` (no block), 003 verified on its own with an amendment (not folded, no block); `Accepted by the master` appears once in the packet. Same test against the parent code (head's tests, base code):
```
$ python3 tests/run.py -k gives_each_folded_unit_its_accepted_amendments
AssertionError: '\n\nAcceptance one holds.\n\nAccepted by the master (reviews/001-util.md), whatever the Acceptance above says' not found in ' (plan .../plans/900-demo.md)\n\nAcceptance one holds.\n'
1 tests, 1 failed
```

Acceptance 020.2 (unit 003's Acceptance still holds at this head): every 003 line proven above at c5cf250.

Prove-block gate, in the scratch at c5cf250:

```
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> setup
setup: ok (1 commands, ...)
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 6ac18e8e557c12a97f8ff88524d57b4e19630bcd
pstack behavioral: pass
  exit 0 1234.3s python3 tests/run.py
```
(My first gate run was started with `nohup`, which makes SIGHUP ignored and fails two signal tests, `test_a_child_killed_by_a_signal_...` with `0 != 129`; that was my launch method, not the code. I killed it and reran from a launcher that resets SIGHUP to default; the rerun passed.)

History check (fix and test in one commit, per review-fix.step-2 and bug-fix.step-5; no repro-first rule for a fix brief; unit 019's is a bug-fix): each commit carries its source and test together: e57fe39 (verify_packet.py + test), 507870a (setup.py + test), c5cf250 (verify_packet.py + test), ff65476 docs only. Unit 019's test and fix share commit 507870a, so the failing test did not land before the fix in history. I proved failure-then-pass by running the test on parent code (above). Not a finding under the repo's AGENTS.md rule (source and its pinning test in the same commit).

Standing check on the range's tests (masks, normalisations, subprocess bounds): the new tests assert on packet substrings and on `git show` output; they use no regex rewrite, filtered line or tolerance, and start subprocesses only through the existing run_sh/as_orb/remote_git helpers, which the suite's per-test timeout bounds. Nothing hermetic is touched: new tests set their own state. No finding.

## Flows driven

- pstack (the only touched profile): drove the `kitchen.sh verify <store> 003 --covers 001` flow (packet generation) through the test fixture against a fake herdr, at base and head, with real amendment text, a multi-paragraph amendment, `None.`, and no review note (Evidence blocks 003.1 to 003.2 above); drove `kitchen.sh finish` as a role orb and `status` as the master under the git store (019 block); the landing packet (020 block). The real store's review for unit 003 has Amendments `none`, so a real-store packet would show no block; the fixture is the surface that has an amendment.

## Folded Acceptance

none

## Not covered

- A real master-to-verifier run against the live run's store with a non-empty amendment: no real review note in this store has one, so the fixture stood in.
- Amendments `None.` (with a period) prints the block (only an empty section or the word `none` is treated as empty); same convention as the existing context_section, so not judged.

## Lessons

none
