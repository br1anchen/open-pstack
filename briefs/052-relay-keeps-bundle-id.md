# Brief 052: relay-keeps-bundle-id

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/052-relay-keeps-bundle-id.md

## Goal

A role's reply after a relayed steer is merged against the relayed bundle it synced, exactly like a reply after a direct send, and an unknown base only stops deletions, without reporting false conflicts.

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/commands/relay.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-kitchen/SKILL.md
- tests/test_pstack_core.py
- tests/test_pstack_kitchen.py
- tests/SPEC.md

must not write:
- skills/pstack-pair/scripts/pstack_core/commands/nest.py — unit 040 is on main; not this unit's area
- skills/pstack-pair/scripts/pstack_core/commands/snooze.py — not this unit's area
- anything else

## Steps

Start from unit 051's head: `git checkout -B kitchen-followups-047 b131308e833d2eb556d3383db39fb484daac25a7` (on origin as kitchen-code/kitchen-followups/unit-051).

1. Relayed bundles are kept by id. commands/relay.py `settle` (~line 56) keeps the settled bundle under `sent/<thread>/<id>.tgz` through the same helper `keep_sent_bundle` uses in mux/amp.py (~391), with the same newest-8 pruning, as well as the legacy `sent_path(thread)`. Extract the by-id copy and prune into one function in mux/amp.py that both call; do not duplicate it. Test in tests/test_pstack_core.py, a row in `BundleReplySyncTests`: the master relays a steer, the role syncs the relay bundle, deletes its own unchanged report and replies; through `sync` the report is deleted on the master and no `is not kept` line prints. The row fails at b131308e.
2. An unknown base stops deletions only. In bundle.py `apply`/`merge_store` (~542, ~950), when the reply's base is unknown, an own-role file the reply changed is taken from the reply without a `conflicts, took theirs` line unless the master also changed it since the last bundle it sent that role (use the legacy `sent_path` copy as that comparison; if none exists, take theirs silently). Deletions still do nothing. Drop merge_store's `based` parameter if it no longer changes anything. Test: row (c) of `BundleReplySyncTests` also asserts no conflict line for an own-role file only the role changed.
3. Pruning tolerates races: the kept-bundle prune ignores a file already removed (FileNotFoundError). Leave the count on PSTACK_BUNDLE_KEEP and add one sentence to its docstring saying it bounds both evidence refs and kept bundles. No new test (no concurrency test without sleeps).
4. mux/amp.py `role_is_idle` (~595): catch DamagedStore from `send.took_a_brief` and return the same result as "in use", so one damaged field does not stop `threads --archive`. tests/test_pstack_kitchen.py archive table (~15844): add a row where the sidekick has no generation and dispatch has generation 0 (it is archivable; fails with the old str() comparison), and a row with a string generation (reported in use, the sweep continues).
5. SKILL.md (~303): reword the reply-merge sentence so the reason holds now: a reply changes only its own role's files because of the ownership rules, it is merged against the bundle it synced (sent or relayed), and one whose base is gone deletes nothing. Regenerate tests/SPEC.md.

## Context

- reviews/051-reply-names-its-base.md (verdict revise) and joo 2026-11-24T16-54-00-542Z-d6ba01b6 under .amp/in/orb-backup/joo/051: the medium at amp.py:391/1415 and the lows this brief folds in.
- verdicts/051-reply-names-its-base-v1.md: clean for 047 049 051; its mutation probe note explains why `apply`'s id check backs up sync.py's base choice. Keep both.
- 047's `test_a_late_reply_never_deletes_a_file_the_master_wrote_after_its_snapshot` stays unchanged.

## Acceptance

- After a relayed steer, a role's reply's own deletion reaches the master and no `is not kept` line prints; the new row fails at b131308e as the report shows.
- With an unknown base, an own-role file only the role changed comes through without a conflict line, and nothing is deleted.
- The archive rows of step 4 hold; the generation-0 row fails with the old str() comparison.
- 047's, 049's and 051's tests pass unchanged; check-pstack and spec_gen --check pass.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k relay
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

Unchanged from briefs/051-reply-names-its-base.md, which this session already read; reread it there.

## Report

Unchanged from briefs/051-reply-names-its-base.md, which this session already read; reread it there.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
