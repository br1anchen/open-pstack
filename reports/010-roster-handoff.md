# Report 010: roster-handoff

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/010-roster-handoff.md
playbook: bug-fix
branch: kitchen-followups
head: c40b7b0cd3989064a31295d45eb8cc880164f64e
tree: clean

## Changed

Base is unit 036's head 84ea957f. Three commits, all recorded with `kitchen.sh step` (steps 1 and 2; step 3's tests were folded into those commits as each change needed its own tests):

- fd05036e feat: `rotate-master` snapshots the host roster. `snapshot_roster` in `kitchen_core/master.py` reads the roster bytes from `kitchen.py`'s `roster_path()` (so `PSTACK_KITCHEN_ROSTER` is honoured), refuses an unreadable, non-UTF-8, secret-bearing (`pstack_core.secrets.scan`) or non-TOML roster with its path and kind, never the contents, and writes nothing; otherwise it writes `sessions/master-g<next>-roster.toml` atomically before `launch_master` packs the store. `record_handover` stores the store-relative path in `.master.previous.roster`, `''` when this orb has no roster. A failed launch removes the snapshot with the rest of the rollback.
- 63ed7b84 feat: `take-master` restores it. `restore_roster` runs after the caller is confirmed as the pending next master and before the old master is closed or `.master.previous` is cleared. Missing target: install with temp file + `os.link` (never replaces a file created meanwhile), print `roster: installed <path> from <snapshot>`. Identical: nothing. Different: keep it, print `roster: kept <path>; it differs from <snapshot>`. Unreadable target or snapshot: `ToolFailed(1)`, hand-over left open so take-master reruns. No `roster` key recorded: `roster: none recorded by the previous master`. Only under a master-as-thread backend (Amp).
- c40b7b0c fix: the first step 2 attempt failed two repo guards (a helper must raise a PairError subclass, not Die; take_master went to 82 body lines). restore_roster raises `ToolFailed`, and the wait for the old master's turn moved to `wait_for_old_master`.

No pstack_core change. `tests/SPEC.md` regenerated.

## Ran

```
$ python3 tests/run.py -k take_master
13 tests, 0 failed
$ python3 tests/run.py -k roster
24 tests, 0 failed
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check / ruff check / uv check --frozen
111 files already formatted / All checks passed! / All checks passed!
kitchen.sh step: gates pass (pstack, pstack-docs) at fd05036e and at c40b7b0c; policy clean
```

Mutation check (not committed): replacing `os.link` with `os.replace` fails the `different` and the rerun subtests; removing the `restore_roster` call fails the absent, different, no-snapshot, unreadable and rerun subtests.

Tests added in `tests/test_pstack_kitchen.py` `AmpKitchenTests`:
- `test_rotate_master_snapshots_the_roster_it_reads_into_the_store` (bytes equal, including `[[sidekick.alternate]]` and `[[verifier.fallback]]` chains; `PSTACK_KITCHEN_ROSTER` honoured on the sending side)
- `test_rotate_master_without_a_roster_records_that_there_is_none`
- `test_rotate_master_refuses_a_roster_it_cannot_snapshot` (table: parse, secret via a token built at runtime so the source holds no literal, binary, unreadable; each: path named, no secret echoed, no snapshot, hand-over not opened)
- `test_take_master_installs_the_snapshotted_roster_only_where_the_orb_has_none` (table over the receiver's roster: absent, identical, different, no snapshot recorded, unreadable, and a rerun that keeps a file the user edited after install; the receiver uses its own `PSTACK_KITCHEN_ROSTER`, and the sender's file is untouched)
- `test_take_master_without_a_roster_on_the_old_master_installs_nothing`

## Self-check

figures: counts above are from the commands shown
acceptance: orb with no roster gets the old master's roster byte for byte (absent subtest, `read_bytes` equality); an existing different roster is kept and named by path (different and rerun subtests); parse failure or secret refuses the rotation and writes no snapshot (refusal table, `assertFalse(snapshot.exists())`, no `previous` in pair.json); no recorded snapshot takes over with the notice (no-snapshot subtest)
callers: `record_handover` gained an optional `roster` argument (default None, nothing recorded); its other callers (`hand_to_next_session`, the herdr/rex path) are unchanged. `take_master` gained one `restore_roster(store)` call and one extracted helper.
fresh: the suites ran on this checkout; the gate runs ran the full suite
gate: format, lint, typecheck pass on every commit

## Deviations

- Step 3's tests were committed with steps 1 and 2 rather than as a separate third commit, because each change carried its own tests (the playbook's test-in-the-same-commit rule).
- A third commit (c40b7b0c) was needed for two guards that the brief did not mention (ends-itself and function size). `wait_for_old_master` is a small extraction in `take_master` to stay under the 80-line limit.
- The installed pstack build in my orb fails a role's `kitchen.sh` commands under the git store ("belongs to the master"), so I ran `skills/pstack-kitchen/scripts/kitchen.sh` from this checkout (it has fix 69304c30).

## Questions

none

## Follow-ups

- A snapshot copies the raw roster, so `[machine]` and host `[[kitchen]]` mappings travel too; an existing roster on the receiving orb wins, as the advice says. Not migrated: files those host entries point to.
- The pstack build the master sends to role orbs (6aaa6770ea89df2b) predates 69304c30; rebuild it from HEAD.
