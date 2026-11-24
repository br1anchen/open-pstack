# Brief 049: bundle-and-rotation-fixes-2

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/049-bundle-and-rotation-fixes-2.md

## Goal

Unit 047's fixes keep their gains without its regressions: a fresh sidekick generation is never read as idle before its first brief; an ordinary send makes no network call; a reply can still delete a file its own role deleted; the unshallow fallback gets a timeout of its own; and SKILL.md says what the bundle code does.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/errors.py
- skills/pstack-kitchen/scripts/kitchen_core/setup.py
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py — unit 040 is not on main yet
- skills/pstack-pair/scripts/pstack_core/commands/snooze.py — unit 046 is not on main yet
- anything else

## Steps

Start from unit 047's head: `git checkout -B kitchen-followups-047 973660250d7aa41552d66f9f0e837c38f6006699` (on origin as kitchen-code/kitchen-followups/unit-047).

1. A fresh generation is not idle (mux/amp.py `role_is_idle`, ~545). The sidekick is idle only when `pair.json .dispatch.generation` equals `.sidekick.generation` (the brief whose done report it reads went to this generation); a generation that has not been dispatched to is in use. Leave 047's rotation-flag rule as it is. Test: after a rotation whose new thread wrote READY and before the next dispatch, `threads --archive` keeps the new sidekick (`in use`) and archives nothing of it. Targeted check: `python3 tests/run.py -k archive` and `python3 tests/run.py -k rotat`.
2. No fetch on an ordinary send (bundle.py `pack`, ~250-266). Only a master hand-over (`branches`) runs `origin_baseline` (fetch, and '' on failure). A send that carries keep refs is cut against origin's default branch as this side's tracking ref already has it (`refs/remotes/origin/HEAD`, else origin/main or origin/master), with no fetch; a send with neither keeps `--remotes`. The receiver's missing-prerequisite path (its bounded origin fetch) covers a tracking ref newer than the receiver's. Tests: a fetch-counting test (wrap git in the fixture, or count `fetch` calls) asserts an ordinary send and an evidence send make zero fetches and a hand-over makes one; 047's evidence test still keeps the local-only evidence commit. Targeted check: `python3 tests/run.py -k bundle`.
3. A role's own deletion still reaches the master (bundle.py `merge_store`, ~515). In a reply, keep this side's copy of a path the reply lacks only when this side's copy differs from the base, the path is not in the base, or the master owns the path (gitstore ownership as `merge_store` already reads it); otherwise the role's deletion stands. Replace 047's sidekick subtest, which passes without the fix (verdicts/ is not sidekick-owned), with one where the replying role owns the path: (a) the master wrote the file after the role's snapshot -> kept; (b) the role deleted its own unchanged file -> removed. Targeted check: `python3 tests/run.py -k bundle`.
4. Unshallow timeout (bundle.py ~558): the `--unshallow` fallback fetch gets `PSTACK_BUNDLE_UNSHALLOW_TIMEOUT_S`, default 600, through the same `fetch_origin` (add a timeout parameter). Test: the fallback runs with that timeout. Targeted check: `python3 tests/run.py -k bundle`.
5. Lows: errors.py `RotateRefused` docstring covers any refused rotation (master hand-over or a role rotation); setup.py ~248 forces permission `none` only when the recorded `start_args` are reused, not when the caller passed its own `--` args (one test row). Targeted check: `python3 tests/run.py -k spawn`.
6. SKILL.md (~277): say exactly what `pack` does after step 2 — a hand-over fetches origin and carries every commit when the fetch fails; an evidence send is cut against origin's default branch as last fetched, with no fetch; any other send is cut against this side's remote-tracking refs, with no fetch; the receiver fetches origin (bounded) when a prerequisite is missing. Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Review: reviews/047-bundle-and-rotation-fixes.md (verdict revise). joo run 2026-11-24T13-17-52-337Z-54331eaa on 699837d8..97366025; findings by file:line in the review note.
- This run uses the git store, so the bundle costs do not hit it; they do hit every local-store run.

## Acceptance

- After a rotation with READY and no dispatch yet, the idle-roles sweep keeps the new sidekick.
- An ordinary send and an evidence send make no fetch; a hand-over makes one; local-only evidence still survives.
- A reply removes a file its own role deleted unchanged, and keeps a file the master wrote after the role's snapshot.
- The unshallow fallback uses its own timeout; RotateRefused and setup.py lows as step 5 says.
- SKILL.md matches pack; 047's tests pass except the one step 3 replaces; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k rotat
python3 tests/run.py -k archive
python3 tests/run.py -k spawn
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
