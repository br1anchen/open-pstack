# Plan {{SEQ}}: {{SLUG}}

round: {{1, or the previous round plus one}}
supersedes: {{none, or the previous plan path}}
contract: 2
store: {{STORE}}
risk: {{routine | escalated, from kitchen.sh classify on a brief with the planned Scope; escalated goes to the consultant too. A reason may follow the class, as in `escalated: contract X changes`}}

## Goal

{{One sentence: the behaviour change or fix, and how we will know it holds.}}

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
