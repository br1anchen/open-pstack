# Brief 031: master-bundle-branches-fix

playbook: review-fix
timebox: 90
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/031-master-bundle-branches-fix.md

## Goal

Fix joo's findings on unit 004 (master hand-over bundles carry every branch). The verifier passed 004, but joo found four medium-severity defects and two lows that let a hand-over lose a branch, half-apply, move the wrong branch, or bloat a bundle. A hand-over must apply all or nothing, never drop the sender's working branch, never move a branch onto another branch's head, and stay small and bounded when origin is unreachable.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/git.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/pstack-pair/golden/**

## Steps

1. **All or nothing** (joo medium). Today `apply_git` runs `move_checkout` (possibly `checkout -B`) and `apply_branches` creates or fast-forwards branches in sorted order, raising only when it reaches a missing tip, after the store, the checkout and earlier branches have changed. For a hand-over (a manifest with `git.branches`), check that every incoming tip resolves before changing any ref or the checkout, and before `merge_store` writes store files; on a missing tip, raise with nothing changed. Test with a hand-over of two or more branches where one tip is missing: refs, HEAD and the store files are as they were.
2. **The sender's working branch is never dropped** (joo medium). `apply_branches` skips `name == current` and leaves it to `move_checkout`, which changes no ref when it returns `kept` (tracked changes, ahead, diverged). Remove that skip so the normal rules (skip when the checkout moved to it, keep when it is checked out here or diverged) apply. Test: receiver has tracked changes, so its checkout is kept; the sender's branch, absent here, is created at the sender's tip and a line says so.
3. **The recipe never moves one branch onto another's head** (joo medium, two lanes). `move_checkout`'s first divergence check passes this checkout's current branch to `diverged`, so with the receiver on X and the sender on Y the recipe `reset --keep`s X onto Y's head, and the later check for a diverged local Y never runs. Compare against the sender's `branch`: when the receiver is on another branch, leave it alone and treat Y by the branch rules (create, fast-forward, or report diverged with its own recipe). Resolve a branch's old head as `refs/heads/<branch>` to match the recipe's guard (joo low). Test: receiver on X with an own commit, sender on Y; X unchanged, Y created or reported, no recipe that names X.
4. **A bounded, small hand-over bundle when origin is slow or down** (joo medium + low). `origin_baseline` returns '' as soon as `git fetch origin` fails, so the bundle carries all history. Run the fetch with `GIT_TERMINAL_PROMPT=0` and a timeout (env knob, default 30 s, through the existing env-int helper), and when it fails or times out, fall back to the existing tracking ref (`refs/remotes/origin/HEAD`, then `origin/main`, `origin/master`) as the cut; only with none of those is the bundle self-contained. Test with an origin that cannot be fetched but a tracking ref present: the bundle excludes the tracking ref's history. Bound the test's subprocess calls as the 004 tests do; no sleeps.
5. **Prune per-bundle refs** (joo low, two lanes). Every apply leaves a new `refs/pstack/in/<sender>/<bundle id>/` set and nothing removes them. Keep the newest N bundle ref sets per sender (N=8 by default; env knob) and delete older ones at apply time, never the set being applied and never one a printed recipe of this apply names. Test: N+2 syncs from one sender leave N sets.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-030 && git checkout -B kitchen-followups FETCH_HEAD` (unit 030's head, which the master pushes when 030 reports; on top of 004's 550a7d7). Do not rebase onto origin/main. If unit-030 is not on origin yet, report blocked; do not start from unit-004.
- joo run on 004: 2026-11-21T05-01-52-966Z-2c2b1e65 (copied to the master's `.amp/in/orb-backup/joo/004-run`); findings quoted in the steps.
- Verdict for 004: `verdicts/004-master-bundle-branches-v1.md` (clean; its observations name the same partial-apply and ref-growth points). 004's brief and report: `briefs/004-…`, `reports/004-…`.

## Acceptance

- Each of steps 1 to 5 has a test that fails without its fix (show each by a scratch revert).
- 004's Acceptance still holds at the head, and every existing bundle, sync, hand-over and take_master test passes.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k sync
python3 tests/run.py -k take_master
python3 tests/run.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- No `time.sleep` in tests, and no elapsed-time asserts.
- No push and no PR.

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
