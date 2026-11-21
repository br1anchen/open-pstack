# Verdict 033: master-bundle-branches-fix

status: clean
units: 032 031 004 033
range: 6a83f500bc5d5ed07276c49035859a9b8322e879..179a1958332a8e60958e9439ff69d899266f4694
verifier: amp claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Observations, not findings (no Acceptance line rests on them):

1. Step 1's brief text says "keep the `kept as .../br/Y` ref in that line". At the head the single Y line is move_checkout's recipe line, which names `.../<id>/head`, not `.../<id>/br/Y` (Evidence D1, `br/Y mentions: 0`). Both refs exist after the apply and hold the same commit (the sender's HEAD is Y), so nothing is lost and the recipe is correct, but the line names the head ref, not br/Y.
2. With the checkout on X and the sender's Y **ahead** here (Y has commits past the sent tip, no divergence), the only Y line now says `branch Y here diverged from master` with a recovery recipe (Evidence D1, ahead-elsewhere). At 032's head the same checkout line was printed, followed by an accurate `branch Y: kept ..., ahead of master`; 033 suppresses that second line. The "diverged" wording comes from move_checkout's `own_commits` check and predates 033; 033 removed the line that corrected it. Y is left unchanged and named, so 004's "ahead ... left unchanged and named" line still holds. Severity low.
3. Verdict 032's observations 2 to 4 are follow-ups by the brief; not re-judged.

## Evidence

### Scratch

The packet's scratch did not exist on this orb; I created it with `git worktree add --detach <scratch> 179a1958332a8e60958e9439ff69d899266f4694` from the main checkout (HEAD `179a1958 fix(skills/pstack-pair): a checkout kept for tracked changes ...`). A second worktree at 032's head 08666a79 held the base comparison and the reverts, and is removed.

### Brief commands at 179a1958, PSTACK_KITCHEN_ROLE=verifier, from the scratch root

```
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261121T102719-4859.log)
$ python3 tests/run.py -k bundle          -> exit 0: 30 tests, 0 failed, 62s wall with 4 jobs
$ python3 tests/run.py -k sync            -> exit 0: 21 tests, 0 failed, 34s wall with 4 jobs
$ python3 tests/run.py -k take_master     -> exit 0: 11 tests, 0 failed, 36s wall with 4 jobs
$ python3 tests/run.py -k rotate_master   -> exit 0: 13 tests, 0 failed, 22s wall with 4 jobs   (unit 004's Command 3)
$ python3 tests/spec_gen.py --check       -> exit 0: ok: tests/SPEC.md is current, 0 missing
$ uv run --frozen ruff format --check --exclude .verify -> 111 files already formatted
$ uv run --frozen ruff check --exclude .verify          -> All checks passed!
$ uv check --frozen                       -> exit 0: All checks passed!
$ python3 scripts/check-pstack.py         -> exit 0: 65 skills checked; all 48 previous skills covered
$ uv lock --check                         -> exit 0: Resolved 3 packages in 1ms
```

Ruff without `--exclude` flagged only `.verify/pack.py`, my own untracked probe script in the scratch (`unformatted: File would be reformatted --> .verify/pack.py:2:1`); excluding it, the repo's 111 files pass. No tracked file is affected.

Command 4 (full suite) through the Prove gate, which runs `python3 tests/run.py`:

```
$ python3 skills/pstack-kitchen/scripts/kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 6a83f500bc5d5ed07276c49035859a9b8322e879
pstack behavioral: pass
  exit 0 734.3s python3 tests/run.py  log: .../logs/20261121T102726/pstack-behavioral-0.log
(log tail) 2007 tests, 0 failed, 733s wall with 4 jobs
```

### R. Each 033 step's test fails without its fix (script .verify/reverts.sh: at each step commit, only bundle.py taken back to its parent, tests kept)

```
== at 8552a58b, bundle.py from 6921d9f0 (fix reverted): -k diverged_here: failed: test_pstack_core.BundleBranchApplyTests.test_a_checkout_on_another_branch_gets_the_recipe_of_the_senders_branch_when_that_one_diverged_here
== at 8552a58b (fix in place): -k diverged_here: 1 tests, 0 failed, 1s wall with 4 jobs
== at 179a1958, bundle.py from 8552a58b (fix reverted): -k tracked_changes_on_the_senders: failed: test_pstack_core.BundleBranchApplyTests.test_a_checkout_kept_for_tracked_changes_on_the_senders_branch_still_names_the_branch_ref_once
== at 179a1958 (fix in place): -k tracked_changes_on_the_senders: 1 tests, 0 failed, 1s wall with 4 jobs
== at head, 179a1958's bundle.py diff reverse-applied:  1 file changed, 1 insertion(+), 1 deletion(-)
   -k diverged_here: 1 tests, 0 failed; -k tracked_changes_on_the_senders: failed: ...still_names_the_branch_ref_once
```
(8552a58b's diff does not reverse-apply at the head, since 179a1958 edits a line in its hunk; it is shown at its own commit above.)

Commit layout (review-fix step 2): each step is one commit with its fix, its test and SPEC.md.

```
8552a58b fix(skills/pstack-pair): a sender's branch that diverged here under another checkout is reported once, by the checkout line
 skills/pstack-pair/scripts/pstack_core/bundle.py | 39, tests/SPEC.md | 2, tests/test_pstack_core.py | 2
179a1958 fix(skills/pstack-pair): a checkout kept for tracked changes still names the sender's branch ref
 skills/pstack-pair/scripts/pstack_core/bundle.py | 2, tests/SPEC.md | 2, tests/test_pstack_core.py | 17
```

### D1. Steps 1 and 2 through `pair.sh sync` (PSTACK_MUX=amp) at the head and at 032's head (script .verify/drive.sh; bundles packed by that head's bundle.pack with branches=True; temp paths shown as <T>; recipe `cd ...` lines omitted in the second block)

At 179a1958, receiver on X with its own commit, local Y diverged from the sent Y (verdict 032 finding 1's scenario):
```
checkout: kept 551b2607e: X stays, and branch Y here diverged from master (709adf3da)
  Y is at 11e8bf94fe00ea9222e4e3a53baf2d56ad85743e; master is at 709adf3da0681e7f9452663c24fe5c174fd48c38 (kept as refs/pstack/in/T-sender/01795257039142554217-710e7c3a66c5/head)
  to take master's commits and keep this side's under kitchen-recovery/11e8bf94fe00ea9222e4e3a53baf2d56ad85743e, run (it changes nothing unless this checkout is still as described):
    cd <T>/repo && true && [ "$(git rev-parse refs/heads/Y)" = 11e8bf94... ] && [ -z "$(git status --porcelain --untracked-files=no)" ] && { git branch kitchen-recovery/11e8bf94... 11e8bf94... || ...; } && git checkout -q -B Y 709adf3da0681e7f9452663c24fe5c174fd48c38
synced: <T>/store from master
sync exit=0
lines naming Y as 'branch Y': 0
'diverged' count: 1
br/Y mentions: 0
HEAD X 551b2607e5b0012423bab9a695a83fd915adda90; Y 11e8bf94fe00ea9222e4e3a53baf2d56ad85743e
```
Same scenario at 08666a79 (032's head): `'branch Y': 1`, `'diverged' count: 2` (the checkout line and `branch Y: kept a9953b227: it diverged from master; ... (kept as .../br/Y)`). At the head the ref set holds both `.../<id>/br/Y` and `.../<id>/head` after the apply (second run's `for-each-ref`).

At 179a1958, receiver with a tracked edit on Y at base, sender's Y one commit ahead (step 2):
```
checkout: kept d5cf27ca0: tracked changes here; master is at 709adf3da
branch Y: kept d5cf27ca0: checked out in <T>/repo; master is at 709adf3da0681e7f9452663c24fe5c174fd48c38 (kept as refs/pstack/in/T-sender/01795257039762419622-d53b323873ba/br/Y)
sync exit=0
br/Y mentions: 1
HEAD d5cf27ca005dc36c7e33e2f54e76ac27efb4e6ec dirty:  M base.txt
```
Tracked edit on X, Y absent here: `checkout: kept b59d27675: tracked changes here; ...` then `branch Y: created at 61ec42eab, as master`; Y equals the sent tip.

Receiver on X, Y ahead of the sent tip (observation 2), at 179a1958:
```
checkout: kept 4f2f07502: X stays, and branch Y here diverged from master (61ec42eab)
  Y is at ee8d05684c37...; master is at 61ec42eab330... (kept as .../head)
  to take master's commits and keep this side's under kitchen-recovery/ee8d05684c37..., run (...):
sync exit=0
Y ee8d05684c370cce7dbbe098810a83eddbddb536
```
Same at 08666a79: the same checkout line, then `branch Y: kept fe9bf1dc0, ahead of master (319b2369c)`.

### D4. 004's Acceptance through `pair.sh sync` at the head (script .verify/d4.sh): sender with unpushed non-HEAD branches feat/x and plain2; receiver lacks them, has `up` behind, `ahead` past, `busy` behind and checked out in a second worktree

```
sender: feat/x=1df24c638c81... plain2=a640d6474... up=d63e7af98... busy=afa40e786... main=1000bece7...
=== ordinary bundle
checkout: at 1000bece7, as master
sync exit=0
feat/x or plain2 present: 0
=== hand-over
checkout: at 1000bece7, as master
branch ahead: kept b3c1aa56c, ahead of master (057dc7530)
branch busy: kept 057dc7530: checked out in <T>/busy; master is at afa40e786911a17bc9efd74dcaf9e887f64733a9 (kept as refs/pstack/in/T-sender/01795257211843411231-cb6ea9913eaa/br/busy)
branch feat/x: created at 1df24c638, as master
branch plain2: created at a640d6474, as master
branch up: fast-forwarded 057dc7530 to d63e7af98, as master
sync exit=0
ahead b3c1aa56c0f3...  busy 057dc75307e1...  feat/x 1df24c638c81ee343231dd16ee96bb0cc196ad24  main 1000bece7b05...  plain2 a640d647421b7aacf07cbbbb7a59510fce81718c  up d63e7af9817c70168f5a928eaa4d8979d0da8943
=== second apply
sync exit=0
refs/heads and HEAD unchanged
=== failed import (a tip the bundle does not carry and origin lacks)
pair.sh: <T>/bad.tgz: branch zzz/missing: fffffffff was not carried and origin lacks it
sync exit=1
refs/heads and HEAD unchanged
=== rebased checkout: receiver's main rebased (own commit not in sender's main)
checkout: kept 4ce9d9e9c: it diverged from master (1000bece7)
  main is at 4ce9d9e9cdd353802abbf2ef27f49b28107145b4; master is at 1000bece7b05266256f133c092aa79c75afa9dd6 (kept as refs/pstack/in/T-sender/.../head)
  to take master's commits and keep this side's under kitchen-recovery/4ce9d9e9cdd353802abbf2ef27f49b28107145b4, run (...):
sync exit=0
HEAD 4ce9d9e9cdd3... main 4ce9d9e9cdd3... (mine 4ce9d9e9cdd3...)
kitchen-recovery/4ce9d9e9cdd353802abbf2ef27f49b28107145b4
1    (one guarded `reset --keep` recipe, printed, not run)
```
Every branch exists at the sender's tip, including the unpushed non-HEAD `feat/x` (with a `/`) and `plain2`; the ordinary bundle carries none; ahead, diverged and checked-out branches are kept and named; the second apply changes nothing; the failed import exits 1 with refs unchanged; the rebased checkout keeps HEAD and prints the recipe with full OIDs and `kitchen-recovery/<old-oid>`.

### Unit 031 and 032 Acceptance

031's step 1 to 5 tests and 032's step 1 to 3 tests all run inside `-k bundle` (30 tests, 0 failed) and the full suite (2007, 0 failed) at this head. Their fail-without-fix reverts were shown at their own commits in verdicts 031-v1 and 032-v1; 033 changes only `move_checkout`'s return and `move_git`'s hand-off to `apply_branches`, and those commits are unchanged ancestors of this head. 004's line holds through D4 above.

### Standing check on the tests in the range

The new test (`test_a_checkout_kept_for_tracked_changes_on_the_senders_branch_still_names_the_branch_ref_once`) and the extended assert in `..._diverged_here` run git through `BundleRepoCase.git` (`timeout=60`) and `bundle.apply` on local repos. No sleeps, no elapsed-time asserts, no output masks, no env reads outside the hermetic set.

## Flows driven

- pstack: `pair.sh sync <bundle>` under PSTACK_MUX=amp with hand-over and ordinary bundles packed by the head's bundle.pack: the diverged-elsewhere, tracked-changes-on-Y, tracked-changes-on-X and ahead-elsewhere cases at the head and at 032's head (D1); 004's create, fast-forward, keep, second-apply, failed-import and rebased-checkout cases (D4). rotate-master and take-master end to end through the suite (`-k take_master` 11/0, `-k rotate_master` 13/0).
- pstack-docs: no user-facing flow; 033 changes no docs (SPEC.md header only), and spec_gen --check and check-pstack pass.

## Folded Acceptance

none

## Not covered

- rotate-master followed by take-master across two live Amp orbs; covered by the suite and by D4's sync of a hand-over bundle.

## Lessons

- L012: held (uv check --frozen passes at the head; 033 adds no stdlib call returning an Optional; `git.out(...) or ''` guards the symbolic-ref read).
