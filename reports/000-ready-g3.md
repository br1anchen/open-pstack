# Report 000: ready (generation 3)

status: done
brief: none (bootstrap)
playbook: none
branch: main
head: a90bb4130393b864176dbeded9bb216fcb5f255c
tree: clean

## Changed

none (bootstrap)

## Ran

```
$ pstack-sync.py --source <build e6400ac119341ef7>/skills --apply    -> installed
$ PSTACK_MUX=amp pair.sh sync --branch <store> kitchen/kitchen-followups
store: kitchen/kitchen-followups at 4e4a2e485
$ jq '{sidekick, dispatch, sent}' <store>/pair.json
sidekick.generation: 3, pane_id: amp:1 (both match this bootstrap), kind: amp,
  start_args: ["--mode","claude-code","--features","model:claude-sonnet-5-5"],
  permission_mode: native-args, rotation_required: true
dispatch.brief: briefs/002-amp-idle-and-stop.md (gen 2, sent 2026-11-20T15:43:34Z); reports/002-amp-idle-and-stop.md is status done, head 6ac18e8
$ git status --short --branch
## main...origin/main   (tree clean)
$ git rev-parse HEAD
a90bb4130393b864176dbeded9bb216fcb5f255c   (= origin/main)
$ git cat-file -t 6ac18e8e557c12a97f8ff88524d57b4e19630bcd
fatal: not our ref   (the head of report 002 is not in this checkout)
```

## Self-check

figures: every value above comes from pair.json, git, or the commands shown
generation: 3, pane amp:1, matches `.sidekick.generation` and `.sidekick.pane_id`
runtime: Amp thread (PSTACK_MUX=amp), thread T-01a1fa04-0358-74ba-bf70-0d0d9db5a602, model claude-sonnet-5-5 via `--mode claude-code` (per `start_args`); effort not set by the roster
permission mode: native-args (`.sidekick.permission_mode`)
cwd: /home/user/workspace/repo
state: brief 002 is done at the master's side (report 002, head 6ac18e8); no partial or queued work for me to pick up. standing-orders.md read (rules 1-26); no session-handoff.md present.

## Deviations

none

## Questions

none

## Follow-ups

this checkout is at origin/main a90bb41 and does not hold the gen-2 commits through 6ac18e8 (unit 001 and 002 work, unpushed in the master's or gen 2's orb). A brief that builds on them needs the master to push them to a branch or send them with the dispatch.
- git-store bug: under `PSTACK_STORE=git` a role thread cannot run any `kitchen.sh <cmd> <store>` (finish, notify): the variant's `startup` hook calls `record_skill_build` -> `update(pair.json)`, which `require_master` refuses (`pair.json: belongs to the master`) whenever the role's skills dir differs from the last `skill_builds` row. I sent this report with a scratch wrapper (`.amp/in/orb-backup/kitchen-nostamp.py`) that runs `pair.py` with that startup stamp skipped; the send itself worked. Fix candidate: `record_skill_build` should skip, not raise, for a non-master caller under git (`caller_is_master`).
