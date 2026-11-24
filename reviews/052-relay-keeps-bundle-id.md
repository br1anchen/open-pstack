# Review 052: relay-keeps-bundle-id

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/052-relay-keeps-bundle-id.md
head: f117480b83a23ccbceccdf5d394e552470e89391
engine: joo 2026-11-24T17-40-50-906Z-50b58d4c

## Amendments

none

## Checked

- joo 2026-11-24T17-40-50-906Z-50b58d4c (b131308e..f117480b): reviewed 8/8 files in 2 lanes; 1 finding, rated medium by one lane and low by the other.
- verdicts/052-relay-keeps-bundle-id-v1.md: clean for 047, 049, 051 and 052, with no findings. The verifier ran the real steer, relay-done, reply and sync path through the CLI; it fails at b131308e and passes at the head.
- The master read bundle.py merge_file:467-489 at f117480b. With base None and two different copies, it returns (incoming, False), so an unknown base already overwrote silently before 052.

## Blocking

none

## Non-blocking

- bundle.py:952 (joo medium): when a reply's base is unknown, apply merges against the newest bundle sent to that role. If the master's edit to a role-owned file was already in that bundle, the role's older copy replaces it without a conflict line. This is not a regression, despite joo's baseline claim: the old empty base was silent in that case too, and 052 only adds a conflict line where the master changed the file after its last send. Recorded in followups.md.
