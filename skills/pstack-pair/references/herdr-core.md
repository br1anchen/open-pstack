# Running in herdr-core

The pair, trio, guided pair and kitchen run on [herdr-core](https://herdr.dev) (`hc`) as they do in Herdr. `pair.sh` and
`kitchen.sh` take herdr-core when `PSTACK_MUX=herdr-core`, or when `HC_TERMINAL_ID` is set and neither `HERDR_ENV=1` nor
`REX_BLOCK` is. Every command, message and store file is the same.

## What differs

- hc's terminals are headless. A pane is an hc terminal and a pane id is its address (`t3`). A split or a tab is a new
  terminal running your login shell in the folder; there is no layout, so a role's pane has no size and no place.
- An agent starts as `exec` and its command line typed into that shell, so its terminal ends when the agent exits.
- hc names cannot change, so pstack names an agent with the terminal label `pstack.agent` and its kind with `pstack.kind`.
- An agent's status is what it reports to hc (OSC 7501); an error counts as blocked. For an agent that reports nothing, pstack
  reads its screen: working while it changes, idle once it stays still.
- A prompt is a plain `hc send`, after pstack checks the agent is idle or done. `hc send --if idle` refuses `done`, which
  Claude reports after every turn (herdr-core issue #154), so pstack does not use it.
- hc shows no notification and reports no Prime daemon session: `kitchen.sh feedback` with no maintainer prints "not
  delivered", and `spawn` refuses `prime-agent`.

## Reading a pane

| Herdr | herdr-core |
| --- | --- |
| `herdr agent read NAME --source visible` | `hc read TERMINAL` |
| `herdr pane read PANE --source visible` | `hc read TERMINAL` |
| `herdr agent list` | `hc ls` |

Never type into your own terminal.
