# Report 000: ready

status: done
brief: none (bootstrap)
playbook: none
branch: main
head: 7fca27105380693abb8741e0af8347d63c413f10
tree: clean

## Changed

none (bootstrap; gen 1's unit 001 is complete at HEAD, its six commits ahead of origin/main)

## Ran

```
$ jq '{sidekick, dispatch, sent}' <store>/pair.json
sidekick.generation: 2, sidekick.pane_id: amp:1 (matches this bootstrap), kind: amp,
  start_args: ["--model", "zhipuai/glm-5.3"], permission_mode: native-args
$ git status --short --branch
## main...origin/main [ahead 6]   (no tracked changes; tree clean)
$ git rev-parse HEAD
7fca27105380693abb8741e0af8347d63c413f10
```

## Self-check

bootstrap: pstack build 895a3b3b1dbc02c6 installed and synced (`pair.sh sync`, 124 files); store copy merged at 7fca27105380693abb8741e0af8347d63c413f10, matching report 001's head
generation: 2 (matches `.sidekick.generation`; delivered shows gen 1 finished brief 001, status done)
runtime: Amp thread (PSTACK_MUX=amp), thread T-01a1f99d-ba1f-7063-b6e6-adc0858249f6
model: zhipuai/glm-5.3, per the roster's chain entry the bootstrap started (`start_args`); effort not set by the roster
permission mode: native-args (`.sidekick.permission_mode`)
cwd: /home/user/workspace/repo
branch: main, head 7fca27105380693abb8741e0af8347d63c413f10, tree clean
state: brief 001 swallowed-failures is done (report written, head matches this checkout); no queued or partial work to pick up

## Deviations

none

## Questions

none

## Follow-ups

none
