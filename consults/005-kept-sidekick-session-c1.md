# Consult 005-c1: kept-sidekick-session

unit: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/005-kept-sidekick-session.md
kind: design
timebox: 40
advice: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/advice/005-kept-sidekick-session-c1.md
sidekick state at send: working at a90bb41

## Question

Finding the root cause: under `PSTACK_MUX=amp`, run sidekick-ab2 printed `rotated: a new sidekick thread is starting` on every dispatch of one master generation (037, 036, 038, 039, 041, 044, 045, 046, 047, 049, 050), including back-to-back routine units with the same profiles (`pstack, pstack-docs`), where `keeps_session` should print `kept: the amp sidekick keeps its session ...`. The run had 0 failovers and one sidekick model (claude-sonnet-5-5), so the `failed_over` branch (`commands/rotate.py` L315, `chain_index > 0`) is ruled out by the retro. A scratch probe subclassing `AmpKitchenTests` (`/home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/consults/probe_keep.py`) shows the keep path works in the fake: done 001, rotate for 002, READY, re-dispatch 002, finish, 003 kept. Real `amp threads context` output matches the fake's shape (totalTokens, maxContextTokens, compactionCount).

What differs in a real run? Leads not yet checked:
- The g3 handoff noted: `pair.sh sync` of a stale bundle can set `sidekick.rotation_required` back to true, since `pair.json` merges key by key (bundle.py three-way merge). A role's `finish` message bundle carries its own pair.json; does a sidekick's (or verifier's) pair.json carry `dispatch.brief`, `sidekick.generation`, `sidekick.pane_id`/thread or `rotation_required` values that overwrite the master's after the master dispatched? Note `keeps_session` reads `field(store, 'dispatch', 'brief')` as a realpath (send.py L267): a realpath written on another orb is the same path here only if the store path is identical.
- `keeps_session` (rotate.py L153-170) reads the previous brief's `risk:`; does anything (a verify, a `--covers`, `revise`) rewrite `dispatch.brief` to a non-routine brief or a fix brief?
- The verifier: a routine verifier thread syncs the store and sends a bundle back; does applying it change `.sidekick` or `.dispatch`?
- `context_used` (amp.py L695-707) reading the sidekick's thread id: after a rotation, which thread id does it read, and could it be the archived old one, or empty, giving None?

Trace it in the code with a scratch reproduction if you can (fake amp, two orbs simulated by two store copies and a real `pair.sh sync` of the sidekick's finish bundle). Name the cause with the lines, or the ranked candidates with the evidence that would separate them, and the smallest fix.

## Read first

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/notes/kitchen-gaps.md (gap 3)
- skills/pstack-pair/scripts/pstack_core/commands/rotate.py L140-350
- skills/pstack-kitchen/scripts/kitchen_core/dispatch.py L80-120; skills/pstack-pair/scripts/pstack_core/send.py L250-400
- skills/pstack-pair/scripts/pstack_core/bundle.py (merge of pair.json), skills/pstack-pair/scripts/pstack_core/commands/sync.py
- skills/pstack-pair/scripts/pstack_core/mux/amp.py L680-730
- tests/test_pstack_kitchen.py `test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread` (about L15949) and its helpers L15915-15947
- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/consults/probe_keep.py

## Decision this feeds

Brief 005, a bug fix with a regression test that fails on today's code. Default without advice: a diagnosis brief to the sidekick that reproduces the two-orb sync sequence before any fix.

## Constraints

- Read the shared tree; never write a non-ignored path in it and never build or test there.
- A prototype or a reproduction runs in `kitchen.sh scratch`, removed before the advice is sent.
- None beyond the standing orders.

## Reply

Write the advice file named above from the advice template. Then
`kitchen.sh notify /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <advice-path>` and end the turn with the single line
`pstack-kitchen ADVICE <path>`.

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
