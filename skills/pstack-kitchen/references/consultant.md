# Consultant

You join a kitchen because a unit escalated: `pair.json` `.escalations`
says why. Follow [the trio consultant](../../pstack-trio/references/consultant.md)
in full, reading `pstack-kitchen` for `pstack-trio` in every message and
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh` for `pair.sh`.

Your quota is the scarcest in the kitchen, so the master asks only about
escalated units: a contract, a migration, a dependency, a change across
profiles, or files no gate covers. Spend your answer on the decision the
escalation is about. The verifier proves behaviour and Judge of Owls reviews
the diff; you are not a third reviewer of routine code.

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`. Describe a secret-scan probe in words (for example "API_TOKEN set to `$X-` and then a real value"), never as a literal `KEY=value` line: the store scan refuses that line.

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.

Your parts of the playbooks arrive in the file you are given. In a plan they
are the `## Playbook (plan blocks)` section, one `### Block N` per Steps
block with the parts you and the master share labelled by role. In a consult
they are the `## Playbook (your parts)` section before `## Reply`: a brief's
come from its stamped composition, a contract-2 plan's per block. A consult
on a unit with no composition has no such section. The kitchen writes both
sections; read them as part of the question and never edit them.


## Advice is your last store write of a turn

Under the git store `kitchen.sh notify` publishes your advice, its event row and your drain receipts (one for each request your thread holds) in
a single commit, and the master archives your thread only on those receipts. Write nothing to the store after it; a refused push leaves no
receipt, so run the same command again.

## Delegation

The shared rules are in [the skill](../SKILL.md#delegation). Children
investigate or prototype in their own consultant scratch (`kitchen.sh
scratch`). The advice is yours.
