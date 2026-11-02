# Running in Rex

The pair, trio, guided pair and kitchen run in [Rex](https://www.superlogical.com/rex) as they do in Herdr. `pair.sh` and `kitchen.sh`
pick the multiplexer they run in: `PSTACK_MUX=herdr` or `PSTACK_MUX=rex` when set, otherwise Herdr when `HERDR_ENV=1`, otherwise Rex
when `REX_BLOCK` is set. Every command, message and store file is the same in both.

## What differs

- A pane is a Rex block, and a pane id is a block id (`block:…`). A tab is a Rex window.
- Rex has no agent list. pstack names the blocks its agents run in and keeps the names in
  `${XDG_STATE_HOME:-~/.local/state}/pstack/rex-agents.json`. An agent pstack did not name goes by its block id.
- An agent's status is what it reports to its block (the Program Status Protocol, OSC 7501). Claude Code, Codex and Muse report it
  themselves. pi reports it through [the pstack pi extension](../../../plugins/rex/agents/pi-program-status.ts). For an agent that
  reports nothing (Devin, opencode), pstack reads its screen: it is working while the screen changes and idle once
  the screen stays still.
- Rex shows no notification that a program outside the block can raise, so `kitchen.sh feedback` with no maintainer prints
  "not delivered" and the path to read.
- Prime Agent does not run under Rex: Rex reports no Prime daemon session, which its role's stop, rotation and failover go through,
  so `spawn` and the kitchen verifier refuse `prime-agent` there.
- Every block and the store live on one machine. With a remote Rex server, run pstack on that machine.

## Reading a pane

What `pair.sh` prints for a human to run uses the Rex commands:

| Herdr | Rex |
| --- | --- |
| `herdr agent read NAME --source visible` | `rex capture -b BLOCK --trim` |
| `herdr pane read PANE --source visible` | `rex capture -b BLOCK --trim` |
| `herdr agent list` | `rex block ls` |

Load the Rex skill (`rex skill show`) before you touch blocks by hand. Never type into your own block.

The Rex actions (`pstack_kitchen` and the rest) start a workflow from Rex's command palette; install them with
`python3 plugins/pstack-action.py install-rex` (see the README).
