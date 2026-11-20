# Brief 004: master-bundle-branches

playbook: bug-fix
timebox: 120
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/004-master-bundle-branches.md

## Goal

A master hand-over under the local store (`rotate-master` under `PSTACK_MUX=amp`) carries every local branch name and tip to the next master, which creates or fast-forwards those branches safely; and a sync that finds the checkout diverged from the sender (a role rebased the branch) keeps it and prints a guarded recovery recipe instead of a bare "kept".

## Scope

may write:
- skills/pstack-pair/scripts/pstack_core/bundle.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py
- skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- skills/**/references/**
- skills/**/SKILL.md
- skills/pstack-pair/scripts/pstack_core/gitstore.py

## Steps

The design is the consultant's advice, `advice/004-master-bundle-branches-c1.md` in the store, sections "Branch transport and application" (points 1-6) and "Divergent and rebased checkouts". Read both first; this brief summarises them and the advice wins on any detail it settles.

1. **Pack every local head on a master hand-over only.** Add an explicit bundle mode (a keyword such as `branches=True`) from `Amp.launch_master` (mux/amp.py about L1351-1366) through `send`/`with_bundle` to `bundle.pack` (bundle.py L130-176). Ordinary role messages keep today's HEAD-plus-scratch payload. In that mode, record a manifest map `git.branches: {name: full_oid}` of every `refs/heads/*` (including branches origin already holds) and pack their tips into the same one git bundle via the existing ref map in `git_bundle` (L110-127). Cut only against a baseline the receiver fetches: origin's default branch as freshly fetched, else a self-contained bundle; never `--not --remotes` in this mode (a sender-only remote would empty it). Old manifests without `branches` keep their meaning. Test: a fresh receiver lacking the objects gets two unpushed non-HEAD branches (one with a `/` in its name) after rotate-master, sync and take-master; an ordinary role message's bundle does not carry them. Commit, `kitchen.sh step`.
2. **Apply branches conservatively.** In `apply_git` (L326-360): fetch the incoming tips into a per-bundle side namespace (`refs/pstack/in/<tag>/<bundle-id>/...`, not replaced by the next send). Validate each name as a head ref (`git check-ref-format --branch`). For each recorded branch: absent locally → create; present, not checked out in any worktree (`git worktree list --porcelain`), and its tip an ancestor of incoming → update with an expected-old-OID `update-ref`; ahead or divergent → keep and print both tips and the side ref; checked out in another worktree → keep with a line saying so; the current worktree's branch goes through `move_checkout` as today. Never delete or force-update a receiver branch. An object import failure exits non-zero before take-master treats the hand-over as complete; a ref conflict does not. One table-driven test over: absent, fast-forward, local ahead, divergent, checked out in a second worktree, repeat apply (idempotent). Commit, step.
3. **A diverged checkout gets a recovery recipe.** In `move_checkout` (L298-323), keep the refusal. When it reports `kept ...: it diverged`, also print the current branch, the old OID, the incoming OID and its durable side ref, and a recipe: check you are on <branch> at <old-oid> with no local changes, `git branch kitchen-recovery/<old-oid> <old-oid>` (no force), then `git reset --keep <full-incoming-oid>`. Shell-quote names. No automatic reset, no patch-id test (the advice's two counterexamples show patch-id containment can lose wanted content). Test: a role rebases the branch, sync prints the recipe and leaves HEAD and the branch unchanged; following the recipe moves it and keeps the recovery branch. Commit, step.

## Context

- Gap 1 and gap 4 of run sidekick-ab2: `notes/kitchen-gaps.md` in the store.
- Consultant probes: `advice/004-master-bundle-branches-c1-probe.py` (counterexamples; run as its header says, not part of the suite).
- Closest tests: `test_the_bundle_carries_the_sidekicks_commit_to_a_checkout_that_lacks_it` (tests/test_pstack_kitchen.py about L17059), `test_take_master_in_the_new_thread_from_the_bundle_it_received` (about L16439), `BundleMergeTests` (tests/test_pstack_core.py about L20643).
- The host roster's hand-over is a separate unit; do not touch it here.

## Acceptance

- After rotate-master, sync and take-master into a checkout lacking them, every local branch of the old master (including two unpushed non-HEAD branches, one named with a `/`) exists at the same tip.
- An ordinary role message (a report or steer bundle) carries no extra branches.
- A local branch ahead of or diverged from the incoming tip, or checked out in another worktree, is left unchanged and named in the output; an absent one is created and an ancestor one fast-forwarded; a second apply changes nothing.
- A sync onto a checkout a role rebased leaves HEAD and the branch unchanged and prints the recovery recipe with full OIDs and the `kitchen-recovery/<old-oid>` branch; no automatic reset happens.
- A failed object import makes the hand-over's sync exit non-zero.

## Verify

```bash
python3 tests/run.py -k bundle
python3 tests/run.py -k take_master
python3 tests/run.py -k rotate_master
```

## Forbidden

- No automatic reset or branch move based on patch-ids.
- pstack_core imports nothing from kitchen_core.

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
