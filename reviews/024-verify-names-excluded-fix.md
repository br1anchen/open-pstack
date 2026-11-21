# Review 024: verify-names-excluded-fix

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/024-verify-names-excluded-fix.md
head: 1e105784ab1a2ffb8dd8f14b0c9974332fd9dbcb
engine: joo 2026-11-21T08-52-43-715Z-0f86254d

## Amendments

none

## Checked

- verdicts/024-verify-names-excluded-fix-v1.md: clean, no findings. `verify --covers` names the unit in the range it leaves out and how to include it; docs updated.
- Deviation accepted: the notice is printed from write_packet, where the packet is written.
- joo: two lows on the same point, a follow-up. Gate the notice on a covers packet, not a landing one, and reuse the units_between result. A landing packet covers the whole range, so the notice there is redundant, not wrong.
