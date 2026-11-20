## Gaps

### 1. Local branches do not travel with rotate-master

What happened: master g3 rotated to g4 on a new orb. The bundle carried HEAD and the scratch worktrees, not the local branch `sidekick-ab` (units 022-034, 13 commits, never pushed). Brief 036 started from fa11bda, which g4 did not hold (`git cat-file: could not get object info`).
Workaround: restored the archived g3 thread with the owner's approval; it uploaded a git bundle of the branch.
Suggestion: `rotate-master` bundles every local branch a brief or report in the store names (or every branch not on origin), and `take-master` fetches them.
Evidence: session-handoff.md (g3 Next names fa11bda); decisions.tsv rows after "land 035+037".

### 2. The host roster does not travel with rotate-master

What happened: ~/.config/pstack/kitchen.toml is missing on the next master's orb, so verifiers and new roles run on Amp's default mode until the master copies the roster back.
Workaround: the g3 handoff told g4 to copy <store>/host-roster.toml by hand.
Suggestion: carry the roster in the master bundle and install it at take-master when the orb has none.
Evidence: session-handoff.md Decisions.

### 3. The amp sidekick rotates on every dispatch, routine or not

What happened: every dispatch this generation (037, 036, 038, 039, 041, 044, 045, 046, 047, 049, 050) printed `rotated: a new sidekick thread is starting`, including back-to-back routine units in the same profiles (pstack, pstack-docs), where SKILL.md says the session is kept (`kept: ...`). Each rotation cost about 3 minutes and one extra master wake (READY, then dispatch again).
Workaround: none.
Suggestion: check why the kept-session rule never fires under Amp (rotation_required, last unit done, context check).
Evidence: /tmp dispatch logs are gone; events.tsv `rotate` rows of generations 20-29.

### 4. pair.sh sync does not move the master's branch after a role rebased it

What happened: 050 rebased sidekick-ab onto main. sync fetched the commits but left the master's branch at the pre-rebase head (not a fast-forward). `verify --landing --parent origin/main` refused: "must be an ancestor of 48e3e1960".
Workaround: `git reset --keep` to the report's head, old head kept as a branch.
Suggestion: when the report's head is on the branch the checkout is on and the old head's patches are all in the new range (patch-id), move the branch and say so; otherwise print the reset command.
Evidence: reports/050-git-store-docs-rebase.md, decisions.tsv "landing-v7".

### 5. land-check fails forever on a unit whose verified head was rebased away

What happened: unit 017's verified head d21c554 no longer exists anywhere; every later land-check exits 2 with "unit 017 has no clean verdict", though 017 is on main.
Workaround: landed 035+037 over exit 2 with a logged decision.
Suggestion: a unit whose commits trunk carries by patch-id (or whose review note names a landed commit) counts as landed.
Evidence: decisions.tsv "land 035+037 despite land-check exit 2".

### 6. Archive safety keeps threads forever after the master edits a file they sent

What happened: `unsynced_files` compares digests; after the master redacted a verdict, or the next sidekick overwrote reports/000-ready.md, the sending thread stayed `kept: sent ..., which the master store does not hold yet`.
Workaround: archived by hand.
Suggestion: a file the master holds a newer revision of counts as received.
Evidence: notes-kitchen-gaps.md; T-01a1f0aa, T-01a1f188.

### 7. A verifier rejects on deviations the master already accepted

What happened: verdicts 041-v1 and 045-v1 rejected solely on Acceptance lines whose premise the master's review had already corrected (accepted deviation). The only paths were revise or an overrule.
Workaround: overruled in the review note, logged.
Suggestion: put the review note's accepted deviations in the verify packet, so the verifier judges against them.
Evidence: verdicts/041-handover-fixes-v1.md, verdicts/045-routing-fixes-v1.md, reviews/041, reviews/045.

### 8. Smaller ones

What happened: `stop` to a working Amp sidekick queues behind its turn instead of relaying like `steer`; `dispatch` of a brief refuses while another is queued (needs clear, dispatch, re-queue); classify lowered a master-written `steps: master` to `none` (024); no command restarts a live consultant with new arguments; joo-dev opens its TUI unless stdin is /dev/null.
Workaround: by hand each time.
Suggestion: stop relays; `dispatch --ahead`; classify never lowers steps; `consultant --restart`; the master reference says to run joo with </dev/null and read runs/*/executions.
Evidence: notes-kitchen-gaps.md.

## What worked

- The consultant's drain-receipt counterexample (advice 040-c1) caught an archive rule that would have lost reports; adopting it in full was right.
- joo on every unit found real bugs the sidekick's tests missed (038's empty bundle, 040's unpublished rollback, 046's row deletion).
- Folding fixes into small review-fix briefs verified with --covers kept each unit's diff readable.
