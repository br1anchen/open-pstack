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

Your parts of the playbooks arrive in the file you are given. In a plan they
are the `## Playbook (plan blocks)` section, one `### Block N` per Steps
block with the parts you and the master share labelled by role. In a consult
they are the `## Playbook (your parts)` section before `## Reply`: a brief's
come from its stamped composition, a contract-2 plan's per block. A consult
on a unit with no composition has no such section. The kitchen writes both
sections; read them as part of the question and never edit them.
