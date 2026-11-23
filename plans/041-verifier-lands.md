# Plan 041: verifier-lands

round: 1
supersedes: none
contract: 2
store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
risk: escalated (landing contract: a verifier thread that lands; archive exemption)

## Goal

Draft, not yet discussed: needs a plan round with the consultant before any brief. Under `PSTACK_MUX=amp`, an opt-in landing mode (for example `[landing] lander = "verifier"` in kitchen.toml, or `verify --landing --then-land`) lets the landing verifier's own thread land the tip from the worktree it verified in, so the repo's pre-push proof replays warm instead of compiling again in the master's orb. Today's mode stays the default.

Requested shape (feedback from the ChenChess orb-mbx-warm run, thread T-01a1fed1-e978-74a5-a348-340b497bb301, open-pstack daf51dc):
- The verifier proves the tip and writes its verdict read-only, as today.
- Only after a clean verdict, a passing land-check and the human's explicit authorization, the master sends that thread a separate LAND message naming the landing command; the step is an events.tsv row.
- The thread lands from its scratch worktree and reports the result back.
- While a land is pending the thread and its scratch are kept (`kept: awaiting land`), with a timeout after which they are archived as today.
- Trunk moved since the verdict: the thread rebases, reruns the proof warm and verifies again, or reports back to the master.
- Landing still goes no further than `[landing].mode`; the kitchen never merges on its own authority.

Evidence: ChenChess pre-push proof `turbo run lint test release-build --filter=chenchess-rust` took a median ~17 min cold over 121 orb threads (7-28), 4m35s with a warm mbx snapshot, 1m42s rerun in the orb that already built it (threads T-01a1fec8-aae0-766a-99f5-f2ab98cddeae, T-01a1fed1-e978-74a5-a348-340b497bb301, T-01a1f5da-2b15-75e8-ada6-f1dd2c20b39a; report 20261123-repo-orb-mbx-warm.md in that orb's feedback inbox).

Manual workaround until it lands: after the landing verdict, the master sends the landing instructions to the verifier's thread and does not archive it.

## Design

{{The master's implementation, architecture, and system design. Data shape and
organizing structure first (state machine, table, typed model, boundary, per
principle-model-the-domain), then module boundaries, interfaces, and how the
change sits in the system.}}

## Alternatives and trade-offs

{{Every option the master weighed, from architect or its own exploration. One
row per option; the chosen one marked.}}

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| | | | |

{{Why the chosen option wins on these trade-offs, in two or three sentences.}}

## Steps

{{One numbered block per future brief, each a slice that lands on its own.
Under each, its commit-sized steps (twenty to forty-five minutes): the files,
the commit, the check. Each block names the files it touches, the
playbook it runs, and the check that proves it, so the sequence proves itself
per principle-sequence-verifiable-units. With `contract: 2` in the header,
each block carries a `playbook: <name>` line (a name `kitchen.sh classify`
maps) and may carry `with: <id>, <id>` for repo overlays; `discuss` refuses
the plan otherwise and writes each block's parts for the consultant and the
master into a `## Playbook (plan blocks)` section.}}

1. {{Slice.}}
   playbook: {{feature | bug-fix | refactoring | ...}}
   with: {{optional overlay ids}}

## Verification

{{How the finished work is proven on the real artifact, not a proxy.}}

## Risks

{{What could break elsewhere, what is uncertain, what the timebox does not cover.}}

## Open questions

{{Questions the sidekick should answer from the code, or "none".}}

## Disagreements resolved

{{Round 2 onward: each objection from the previous response and how this round
answers it, adopted or declined with the reason. "none" in round 1.}}
