# Report 028: dispatch-after-ready-sends-fix

status: blocked
brief: briefs/028-dispatch-after-ready-sends-fix.md
playbook: review-fix
branch: main
head: 7218f3112db91d4448b20d708b5c92117b6543db
tree: clean

## Changed

nothing. No step was started.

## Ran

```
$ git fetch origin kitchen-code/kitchen-followups/sidekick-g6 && git checkout -B kitchen-followups FETCH_HEAD
HEAD a0eb1b7 (unit 027's head), not b6b68a4
$ git cat-file -t b6b68a4 ; git cat-file -t cd4e197
fatal: Not a valid object name (both)
$ git ls-remote origin | grep -c -E '^(b6b68a4|cd4e197)'
0
$ git for-each-ref --contains 4bca786
refs/remotes/origin/kitchen-code/kitchen-followups/sidekick-g5   (023's head; only ref that has it)
$ git ls-tree -r --name-only origin/kitchen/kitchen-followups | grep '^code/'
(no output: no code bundle on the store branch)
$ drains/sidekick-g6/eafd29eab1e6.json
code: ["b6b68a410bae7153de115c72b752b1226b6331aa"]   (the receipt names 026's head, but no ref holds it)
```

## Questions

1. The brief says to base on 026's head b6b68a4 via `kitchen-code/kitchen-followups/sidekick-g6`. Generation 6's unit 027 push overwrote that ref with a0eb1b7, whose parent is main 7218f31. So b6b68a4 and cd4e197 (026's two commits) exist on no ref I can reach. 4bca786 (023, on sidekick-g5) is not an ancestor of main or of a0eb1b7 either.
2. The Acceptance depends on the lost commits: cd4e197 (the `AmpKitchenTests.mux_env` default `PAIR_ROTATE_READY_S='0'`, which makes `test_a_thread_in_a_turn_is_working...` run in under 10 s) and b6b68a4 (the step-2 case `test_a_working_sidekick_that_already_took_a_brief_of_its_generation_is_refused_without_polling`). Step 1's own target, `test_every_rotation_under_amp_says_why...`, exists at 4bca786 (and b1ca49f), but not at a0eb1b7 or main.

Options (I would pick A):
- A. The master pushes b6b68a4 from its own checkout to a ref (for example `git push origin b6b68a4:refs/heads/kitchen-code/kitchen-followups/unit-026`), or sends a bundle, then re-dispatches 028 with that base. Nothing else changes.
- B. I base on origin's sidekick-g5 (4bca786) and re-create cd4e197 and b6b68a4 from report 026's description, then do step 1. The shas would differ from the ones already verified and reviewed, and the 026 test is written from a description, not the original. Dispatch it only if the master cannot reach b6b68a4.

## Deviations

none. I did not start work on a guessed base. The checkout is back on `main` at 7218f31, clean. The local branch `kitchen-followups` still points at a0eb1b7 (unit 027's head, untouched).

## Self-check

figures: every value above comes from the commands shown
