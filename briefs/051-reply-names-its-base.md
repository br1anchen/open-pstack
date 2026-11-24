# Brief 051: reply-names-its-base

playbook: review-fix
timebox: 75
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/051-reply-names-its-base.md

## Goal

A role's reply is merged against the bundle the role actually synced, not the newest bundle the master sent it, so a reply deletes a file only when the role had it and deleted it: a file the master wrote after the role's snapshot survives a late reply, and a role's own deletion still reaches the master.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py — unit 040 is not on main yet
- skills/pstack-pair/scripts/pstack_core/commands/snooze.py — unit 046 is not on main yet
- anything else

## Steps

Start from unit 049's head: `git checkout -B kitchen-followups-047 648f31bfbc25a86d5a5b5c5106ce4d56e60778b0` (on origin as kitchen-code/kitchen-followups/unit-049).

1. Every bundle gets an id in its manifest (the sha256 of its packed files, or a uuid; pick one and say which). `apply` records, on the receiving side, the id of the last bundle it applied from each sender (beside the existing sent-bundle record, mux/amp.py `sent_path` / `keep_sent_bundle`). `pack` writes that id into the manifest as `synced` (the bundle this side last applied from the receiver), '' when none.
2. The master keeps each bundle it sends a role by id (not only the last; keep the newest `PSTACK_BUNDLE_KEEP` (8) per thread, which `apply` already reads as KEEP_ENV), and `sync` (commands/sync.py ~143) passes `bundle.apply` the kept bundle whose id is the reply's `synced` as the base. When the reply names no id or one no longer kept, the base is none and `merge_store` keeps every local file the reply lacks (047's rule: no deletion by omission), and prints `merge: the reply's base <id> is not kept; files it lacks are kept`. With the right base, 049's rule stands (keep only when local differs from base or base lacks it).
3. Tests in tests/test_pstack_core.py, one table over: (a) the master writes a gates-mode verdict under verdicts/ after the role's snapshot, sends a newer bundle, the role's older reply (synced = the earlier bundle) arrives -> verdict kept; (b) the role deletes its own unchanged report and replies (synced = the bundle it had) -> removed; (c) reply with an unknown synced id -> file kept and the line printed; (d) the verifier variant of (a). Restore 047's verifier assertion that 049 dropped. Each row fails at 648f31bf where it should.
4. mux/amp.py `role_is_idle` (~549): use `send.took_a_brief` for the generation check instead of the str() comparison; 049's archive test still passes.
5. SKILL.md: the reply-deletion sentence 049 wrote says a reply is merged against the bundle it synced, and a reply whose base is gone deletes nothing. Regenerate tests/SPEC.md.

## Context

- verdicts/049-bundle-and-rotation-fixes-2-v1.md: reject on the same bug, from the verifier side: 047's verifier subtest, run unchanged, fails at 648f31bf (verdicts/ is a ROLE path the master co-owns, so `is_master_owned` misses it). Restore that test file's verifier subtest exactly as 047 had it.
- reviews/049-bundle-and-rotation-fixes-2.md (verdict revise), joo 2026-11-24T15-21-10-554Z-4a5793fb, bundle.py:527 medium and amp.py:549 low.
- 047's step 1 (reply never deletes what its sender did not write) and 049's step 3 (a role's own deletion goes through) are both required; this unit makes them hold together.

## Acceptance

- Rows (a)-(d) of step 3 hold, and (a), (b), (d) fail at 648f31bf or 97366025 as the report shows.
- A reply with no or an unknown `synced` id deletes nothing and says so.
- 047's `test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot` verifier subtest is back unchanged and passes.
- 047's and 049's other tests pass; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k archive
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No change to nest.py or snooze.py.
- No `time.sleep` in tests, and no elapsed-time asserts.
- Never end your turn while `kitchen.sh step` or `finish` prints `still running`; rerun it in the foreground.
- No push to main and no PR.

## Playbook: review-fix (your parts)

You own the working tree.

Your parts:
- review-fix.intro-1: **Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdi
- review-fix.step-1: 1. Read each finding, the code it names, and the parent unit's brief and report. A finding you canno
- review-fix.step-2: Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the 
- review-fix.step-3: 3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks 
- review-fix.reply: In the report, under Ran and the sections the template names: each finding with its commit, the test

### review-fix.intro-1

**Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdict or a landing review already found each one; the unit that raised them already settled the design.

### review-fix.step-1

1. Read each finding, the code it names, and the parent unit's brief and report. A finding you cannot place, or one whose fix would change the parent's design, goes in the report under Questions; do not redesign around it.

### review-fix.step-2

Fix one finding at a time, the smallest change that answers it. Each finding is one commit with the test that pins it, in the same commit, recorded with kitchen.sh step. A finding with no testable behaviour (a comment, a name, a log line) says so in its commit message.

### review-fix.step-3

3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks for a failing test committed before its fix.

### review-fix.reply

In the report, under Ran and the sections the template names: each finding with its commit, the test that pins it, and the command output that shows it fixed. A finding left open says why. Every claim carries its command output or a file pointer.

## Report

Work the Steps in order. End each in a commit and record it with
`kitchen.sh step /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <sha> "<what it does>"`: it runs the touched
profiles' fast gates and the repo's policy check, and records the step only
when they pass. When it exits 4 with `gates: still running`, the step is not
recorded: run the same command again at once, in the foreground, and keep
going until it records; never end your turn on it. On a failure, fix it in a
new commit and run `kitchen.sh step` again with that commit; it tells you when
two failures in a row make this a blocked report. At each step boundary run `kitchen.sh notes /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups` and fix
any open blocking note first, as a fixup commit recorded with `--resolves`.
Append one progress line per change of approach with
`kitchen.sh progress /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups "<line>"`. When any `kitchen.sh` command prints
`PAUSE:`, stop at that safe point: commit what is verified and report
`partial` with the next step. Before reporting done, run the review skills the
kitchen's `review.self` names on your diff. Write the report file named above
using the pstack-pair report template, then run
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh finish /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <report path>`
and do what it prints. For a done report, finish first reruns the Verify
commands above at the report's head:, with the checkout at that commit and
nothing uncommitted, and sends the report only when every one exits 0; exit 4
means they are still running, so run the same finish again at once.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
