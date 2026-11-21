# Report 022: migrated-threads-archive-nest

status: done
brief: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/022-migrated-threads-archive-nest.md
playbook: bug-fix
branch: kitchen-followups
head: ece578550c5019b3e36b6d0b37a25039d12859b7
tree: clean

## Changed

Started from unit 031's head 6dcc8e3 (fetched from origin kitchen-code/kitchen-followups/unit-031). Four commits:

| step | commit | what |
| --- | --- | --- |
| 1 | 28b1cb015 | store-to-git records `.store_backend.migrated_at`; `amp_routing.undrained` returns '' (the local-store checks decide) for a thread that began before it and has no request on the branch |
| 2 | fe439ae + 9ececb639 | `nest <store> --discover`; 9ececb639 only regenerates tests/SPEC.md, which fe439ae left stale (first gate failed spec_gen --check) |
| 3 | ece578550 | SKILL.md: one sentence each for the migrated-thread rule and `nest --discover`, and `migrated_at` in the store-additions table |

Files: pstack_core/mux/amp_routing.py, commands/store_to_git.py, commands/nest.py, skills/pstack-kitchen/SKILL.md, tests/test_pstack_kitchen.py, tests/test_pstack_core.py (one existing assertion on the backend record now allows migrated_at), tests/SPEC.md. mux/amp.py needed no change: `Amp.discover` is reused as is.

- Step 1: `before_migration(store, thread)` compares threads.json's `started` (else `at`) with `migrated_at`; a thread with neither, or a store with no `migrated_at`, keeps the old answer, so a thread started after the migration still reads "no drain receipt (the store branch records no request to it)". The drain-receipt rule for threads with a request is untouched.
- Step 2: `nest --discover` calls `Amp.discover(store)` (the `threads --discover` search), sets `.anchor.thread` to the oldest master thread when pair.json has none (lowest generation of `.master.sessions` naming a thread, else the earliest started master in threads.json, else the current master), then records every thread the store names with no row (threads.json entries of this store, `.sidekick_threads`, master sessions and current master, registry agents titled for the run) as nest-pending via the existing `record_pending`, and prints the usual lines. Rows already present are not repeated.

## Ran

Each new test fails without its fix (fix reverted in a scratch copy, restored after):

```
step 1: before_migration call disabled   -> -k after_store_to_git: 1 tests, 1 failed
step 2: nest.py restored to HEAD~       -> -k nest_discover:      1 tests, 1 failed
```

At the head (ece578550c5019b3e36b6d0b37a25039d12859b7):

```
$ python3 tests/run.py -k archive   -> 24 tests, 0 failed
$ python3 tests/run.py -k nest      -> 12 tests, 0 failed
$ python3 tests/spec_gen.py --check -> ok: tests/SPEC.md is current, 0 missing
```

The fast gates of pstack and pstack-docs (full tests/run.py, spec_gen --check, ruff format and check, uv check --frozen, check-pstack) passed at each recorded step: 1 at 28b1cb015, 2 at 9ececb639, 3 at ece578550. `kitchen.sh finish` reruns the Verify block at this head.

## Self-check

figures: counts above are the tails of the commands shown; gate results are the "step N recorded" lines.
acceptance:
- pre-migration thread with no request archives by the local rule, post-migration one keeps `no drain receipt`: test_after_store_to_git_a_thread_from_before_it_with_no_request_archives_by_the_local_rule_and_one_from_after_it_keeps_no_drain_receipt (an idle sidekick and an idle consultant from before are archived; a sidekick started after keeps the reason; a pre-migration sidekick still in a turn is kept as working).
- `nest --discover` sets the anchor to the oldest master and lists the unnested threads; a rerun adds nothing: test_nest_discover_sets_the_oldest_master_as_anchor_and_records_the_threads_without_a_row_once (three older threads plus the current master and setUp's sidekick; rerun leaves the rows unchanged; after `--done` on all, `nest: nothing pending`).
callers: `store_backend` consumers: grep found init.py, close.py, store_to_git.py and gitstore.backend_record; none compares the record to an exact dict except the two tests I updated. `undrained`: one caller (kept_by_branch). `nest` CLI usage text changed; no caller parses it.
fresh: gates ran in this checkout with no cache; targeted -k runs are fresh processes.
gate: ruff format --check, ruff check, uv check --frozen, spec_gen --check pass at the head; L012 held (uv check --frozen before each step).

## Deviations

- Steps 2 and 3 were written in a scratch worktree while step 1's gate ran (about 17 minutes each), then fast-forwarded and recorded in order; the worktree and branch are removed.
- Step 2 needed a second commit (9ececb639, SPEC.md regeneration) after its first gate failed on a stale SPEC.
- Step 1 touches tests/test_pstack_core.py, which the brief's Scope does not list: an existing assertion compared the whole backend record and now fails with migrated_at present. One-assertion change.
- The timebox of 75 minutes passed during the gate runs.

## Questions

none

## Follow-ups

- `before_migration` compares threads.json's `started`, which only threads the kitchen started or `discover` recorded carry; an unrecorded old thread keeps the receipt rule until `threads --discover` records it (and `discover` stamps `started` only for threads the run launched).
- `nest --discover` does not mark threads nested; the master still runs the printed update_thread calls and `--done`.
