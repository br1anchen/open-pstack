# Brief 032: master-bundle-branches-fix2

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/032-master-bundle-branches-fix2.md

## Goal

Fix the joo findings on unit 031 that can lose a ref or mislead the reader. The verifier passed 004+031 (verdict 031-v1); joo run 2026-11-21T07-02-41-670Z-3f3b8542 found one medium in two lanes and several lows.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/git.py
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/pstack-pair/golden/**

## Steps

1. **Pruning never drops the newest sets or a recipe's ref** (joo medium, two lanes; verdict observation 3). Bundle ids from before 031 are bare uuid4 hex. They sort as text above the new `<time_ns>-<hex>` ids, so after an upgrade pruning keeps old sets and deletes the newest. Order sets by their clock prefix, and treat an id without one as older than every prefixed id. Also never prune a set that a recovery recipe printed in this apply names, as brief 031 step 5 required (joo low says it can). Test: legacy ids beginning `f…` plus N+1 new sets leave the N newest new sets. A second test: a diverged apply whose recipe names an incoming ref keeps that ref while it prunes.
2. **No recipe moves branch X onto a detached sender's head** (joo low). With a detached sender, move_checkout still prints a recipe that resets the receiver's branch X onto the sender's head. Print no reset recipe for a detached sender: name the incoming ref instead. When the checkout is on another branch, the diverged line must name X and not claim the checkout kept Y's tip (joo low). Test both cases.
3. **Each branch is reported once, and settings are checked first** (joo lows). The sender's current branch can be reported by both move_checkout and apply_branches; report it once. Read and validate `PSTACK_BUNDLE_KEEP` before apply changes anything. `PSTACK_BUNDLE_FETCH_TIMEOUT_S=0` must mean no timeout, not an instant timeout. In `git.run`, accept only `timeout` and `env` as extra keywords instead of `**popen`, and say in the docstring that `env` replaces the environment (the one caller merges `os.environ`). One table-driven test over the two env settings, plus one test that a branch line appears once.

## Context

- Before you start: `git fetch origin kitchen-code/kitchen-followups/unit-022 && git checkout -B kitchen-followups FETCH_HEAD` (unit 022's head, which the master pushes when 022 reports; on top of 031's 6dcc8e3). Do not rebase onto origin/main. If unit-022 is not on origin, report blocked.
- Verdict 031-v1 (observations 1 to 4) and joo run 3f3b8542 (copied to the master's `.amp/in/orb-backup/joo/031-run`).
- Left as follow-ups, not in this brief: a rejected hand-over leaves its import refs (documented by the verifier; a later apply prunes them); SIGKILL on fetch timeout and orphaned transports; the stale-ref cut on fetch failure; receiving-side fetch timeouts.

## Acceptance

- Each step's tests fail without its fix (show by scratch revert).
- 004's and 031's Acceptance still hold, and every bundle, sync and take_master test passes.

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
