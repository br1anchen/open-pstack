# Consult 011-c1: git-store-default-on

unit: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/011-git-store-default-on.md
kind: design
timebox: 45
advice: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/advice/011-git-store-default-on-c1.md
sidekick state at send: working at a90bb41

## Question

Brian (owner) decided, 2026-11-20: the git branch becomes the durable source for kitchens. Three new units, each with tests and docs; I need a design for each and the order and unit boundaries between them, so I can write plans or briefs:

1. **Unit 011: `PSTACK_STORE=git` is the default store.** The local bundle store stays as an opt-out (`PSTACK_STORE=bundle`, or another spelling you argue for). New runs push their run files to `kitchen/<slug>` on origin (Brian approved publishing these branches to br1anchen/open-pstack). Today `commands/init.py` L203 opts in only on `PSTACK_STORE == 'git'`; `gitstore.py` L476 refuses without an `origin` remote. Questions: what does init do in a repository with no `origin`, or one the user cannot push to (fail, or fall back to the bundle store with a printed line)? Under Herdr, Rex and subagents (no Amp threads), does the default change too, or only under `PSTACK_MUX=amp`? How does a store started on an older build (local, no `.store_backend`) keep working? Which tests that assume the local store need `PSTACK_STORE=bundle` pinned, and is a suite-wide default in the test harness better than per-test pins? The `kitchen/*` branch publishes the run's briefs, reports and thread ids: what should init print the first time, and does the secret scan before the first push (store-to-git does it) also gate init?
2. **Unit 012: role orbs install pstack from a pinned open-pstack commit, not `pstack-build.tgz`.** Today `mux/amp.py` `pack_build` (L604-619), the install line (L623-627, under `~/.local/share/pstack-builds/<id>`) and the attachment (L1092) ship the master's skills build on a role's first message and again when it changes (`.sidekick.delivered` and the build id). New: the master pins the commit it runs (its skills directory is a checkout of this repo, `.agents/setup` links `skills/*`); the role installs from that commit on origin (`git fetch origin <sha>` and a worktree or archive under the same builds directory, then `scripts/pstack-sync.py`); the master re-pins and tells roles to reinstall when its build changes; the tarball stays only as a fallback when the commit is not on the remote (unpushed local commits, a dirty skills tree). How should the master decide "the commit is on the remote" (ls-remote / `git branch -r --contains`, cost per send), and what does a dirty skills tree map to? Note that under the git store the master's unit commits reach origin only on `kitchen-code/*` refs or bundles: does the pin live on such a ref, or must it be on a branch of origin?
3. **Unit 013: with the git store, rotate-master's hand-over carries only notes and scripts.** The new master fetches the run branch (`pair.sh sync --branch <store> kitchen/<slug>`) instead of receiving store files; the hand-over message carries the handoff notes and any scripts. Today `kitchen_core/master.py` `hand_to_next_thread` (about L482-519) → `Amp.launch_master` (mux/amp.py about L1351-1369) → `send`, which already skips `bundle.pack` under git (L1241-1257). What is still carried today under git, what must the new master fetch and in what order (run branch, code refs, the pinned build of unit 012), and how does this interact with brief 004 (all local branches on hand-over, written for the local store) and brief 010 (roster snapshot in `sessions/`, which under git would travel on the branch)?

Also: plan 008 and briefs 004, 006 and 010 were written for the local (bundle) store. With git the default, which of them still matter as written, which should be reduced to the bundle opt-out, and is any now pointless? Give the order you would run all of them in.

## Read first

- This store: `briefs/004-master-bundle-branches.md`, `briefs/006-land-check-gone-head.md`, `briefs/010-roster-handoff.md`, `briefs/009-git-store-default.md` (AGENTS.md docs only; likely folded into 011), `plans/008-kept-sidekick-session.md`, your advice 004-c1, 005-c1, 006-c1, `standing-orders.md` (orders 21-26)
- skills/pstack-pair/scripts/pstack_core/commands/init.py L180-240, gitstore.py (create, publish, RULES, fetch_code), commands/sync.py, commands/store_to_git.py, mux/amp.py L590-640, L1080-1100, L1235-1260, L1350-1370
- skills/pstack-kitchen/scripts/kitchen_core/master.py L440-660, skills/pstack-kitchen/SKILL.md "Amp threads", AGENTS.md "Kitchen on Amp orbs", .agents/setup
- tests that set PSTACK_STORE: `rg -n PSTACK_STORE tests/*.py`

## Decision this feeds

Plans for units 011-013 (likely escalated: they change the store's default contract and the build delivery), and a re-ordering of the run. Default without advice: 011 first (git default with a printed fallback to bundle when origin is missing or unpushable, Amp threads only, tests pinned by a harness default), then 013, then 012 with an ls-remote check; 004, 006 and 010 kept for the bundle opt-out only, 008 kept.

## Constraints

- Read the shared tree; never write a non-ignored path in it and never build or test there.
- Prototypes run in `kitchen.sh scratch`, removed before the advice is sent.
- Send the advice as one file (no separate probe file this time; put probe code in a scratch path and quote what it printed): each separate notify currently risks a stale-merge rollback of the master's store (decisions.tsv `incident`).

## Reply

Write the advice file named above from the advice template. Then
`kitchen.sh notify /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <advice-path>` and end the turn with the single line
`pstack-kitchen ADVICE <path>`.

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
