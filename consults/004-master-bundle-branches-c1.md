# Consult 004-c1: master-bundle-branches

unit: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/004-master-bundle-branches.md
kind: design
timebox: 30
advice: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/advice/004-master-bundle-branches-c1.md
sidekick state at send: {{filled by kitchen.sh consult; leave as is}}

## Question

Design for one unit that makes a master hand-over (and a role's rebase) carry git state correctly under the local store (`PSTACK_MUX=amp`, not `PSTACK_STORE=git`). Three items:

1. **Local branches.** `bundle.pack` (skills/pstack-pair/scripts/pstack_core/bundle.py L130-176) bundles HEAD and each scratch worktree's commit as `refs/pstack/out/*` with `--not --remotes`; `apply_git` (L326-360) fetches into `refs/pstack/in/<tag>/*` and `move_checkout` (L298-323) moves only the checked-out branch. In run sidekick-ab2 the unpushed local branch `sidekick-ab` (13 commits) did not reach the next master. Which branches should travel: every local branch with commits not on any remote, only branches a brief/report in the store names, or only on master-to-master sends (rotate-master) versus every send? What should apply do with them: create `refs/heads/<b>` when absent, fast-forward when the local one is an ancestor, and otherwise what (keep and warn, or a side ref)? Weigh bundle size on every role message against simplicity.
2. **The host roster.** `~/.config/pstack/kitchen.toml` (`roster_path()`, skills/pstack-kitchen/scripts/kitchen.py L459-463, `PSTACK_KITCHEN_ROSTER` override) is missing on the next master's orb, so verifiers and new roles start on Amp's default mode. Where should it travel: a tar member of `bundle.pack` (host files are not carried today), an attachment beside `pstack-build.tgz` in `Amp.send` (mux/amp.py about L1241-1258), or a copy in the store at init (`<store>/host-roster.toml`) that `take-master` installs when the orb has none? pstack_core must not import kitchen_core; the roster is a kitchen concept. Should a roster that differs from the orb's existing one ever overwrite it?
3. **Sync after a rebase.** When a role rebased the master's branch, `move_checkout` ends in `own_commits` (L293-295, `rev-list -n1 local --not --remotes head`) and prints `checkout: kept <sha>: it diverged ...`; the master had to `git reset --keep` by hand, and `verify --landing --parent origin/main` refused until then. Proposed: when every commit in `merge-base(local, head)..local` has a `git patch-id --stable` match in `merge-base..head`, run `git reset --keep head` (clean tree only) and print `checkout: moved <branch> to <head>: a rebase of <old> (every patch carried)`, keeping the old head as a ref (`refs/pstack/prerebase/<branch>` or a branch?); otherwise print the exact `git reset --keep <sha>` command. The patch-id helpers live in kitchen_core/units.py L57-75 and cannot be imported from pstack_core. Is this safe, and is the kept old head needed?

Also say whether these three belong in one unit or two (the sidekick will implement; diff limit 600 lines escalates).

## Read first

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/notes/kitchen-gaps.md (gaps 1, 2, 4)
- skills/pstack-pair/scripts/pstack_core/bundle.py L90-375
- skills/pstack-pair/scripts/pstack_core/commands/sync.py L81-140
- skills/pstack-pair/scripts/pstack_core/mux/amp.py L600-620, L1235-1260, L1350-1370
- skills/pstack-kitchen/scripts/kitchen_core/master.py L440-640 (hand_to_next_thread, rotate_master, take_master)
- skills/pstack-kitchen/scripts/kitchen.py L455-540
- tests: tests/test_pstack_kitchen.py `test_the_bundle_carries_the_sidekicks_commit_to_a_checkout_that_lacks_it` (about L17059), `test_take_master_in_the_new_thread_from_the_bundle_it_received` (about L16439); tests/test_pstack_core.py `BundleMergeTests` (about L20643)

## Decision this feeds

The master writes brief 004 (and 007 if you split it) from your answer. Default without advice: all local branches not on a remote travel on every send, apply creates or fast-forwards them and warns otherwise; the roster is copied into the store at init and installed by take-master when absent, never overwriting; the rebase move as proposed, old head kept as `refs/pstack/prerebase/<branch>`; one unit.

## Constraints

- Read the shared tree; never write a non-ignored path in it and never build or test there.
- A prototype or a build runs in `kitchen.sh scratch`, removed before the advice is sent.
- None beyond the standing orders.

## Reply

Write the advice file named above from the advice template. Then
`kitchen.sh notify /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <advice-path>` and end the turn with the single line
`pstack-kitchen ADVICE <path>`.

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
