# Consult 006-c1: land-check-gone-head

unit: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/briefs/006-land-check-gone-head.md
kind: design
timebox: 30
advice: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/advice/006-land-check-gone-head-c1.md
sidekick state at send: working at a90bb41

## Question

Design: land-check (skills/pstack-kitchen/scripts/kitchen_core/landing.py `land_check` L516-575) prints `unverified: unit NNN has no clean verdict` for a unit covered only by a landing verdict whose head no longer exists in the repo. In sidekick-ab2, unit 017 was covered by landing-v1 at d21c554; the branch was later rebased (f6ca845..d92bcb5 on origin/main carry its commits under other shas), d21c554 was gone from every repo the master had, and every later land-check exited 2. `landing_range_covering` (L397-444) returns '' when `unit_head` (units.py L154-165) cannot resolve the head, and `rebased_in`/`landed_as` need the old commits' objects to compute patch-ids. Once the objects are gone no patch-id can be computed.

Which fix is right?
(a) Record patch-ids when they can be computed: at `step` (per step commit, in steps/<unit>.tsv) or at the landing verdict / review accept (per unit, in the store), so a later land-check matches them against trunk without the old objects.
(b) A unit with an accepted review and a clean landing verdict that named it, whose head is gone, counts as covered with a warning (`covered by landing-vK at <head>, which this repo no longer has`), on the master's say-so.
(c) Keep the old heads reachable (a `refs/pstack/keep/<unit>` ref at verify time), so objects survive rebase and gc and travel in the bundle.
(d) Something else.
Weigh correctness (a land-check that passes must mean the verified code is what lands), what travels between orbs (the local store bundle, 1 MiB file limit), and diff size. Also: should a unit whose commits trunk already carries by patch-id be reported as `landed` and dropped from the unverified list, as the gap suggests?

## Read first

- /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/notes/kitchen-gaps.md (gap 5)
- skills/pstack-kitchen/scripts/kitchen_core/landing.py L220-575
- skills/pstack-kitchen/scripts/kitchen_core/units.py L57-381
- tests/test_pstack_kitchen.py `test_land_check_covers_a_unit_rebased_into_the_stack_by_patch_id` (about L12457) and the landed-rebased/squashed tests (about L12953-13040)

## Decision this feeds

Brief 006. Default without advice: (a) at the landing verdict and at each step, plus `landed` for units trunk carries by recorded patch-id.

## Constraints

- Read the shared tree; never write a non-ignored path in it and never build or test there.
- A prototype runs in `kitchen.sh scratch`, removed before the advice is sent.
- None beyond the standing orders.

## Reply

Write the advice file named above from the advice template. Then
`kitchen.sh notify /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <advice-path>` and end the turn with the single line
`pstack-kitchen ADVICE <path>`.

Never paste credentials, tokens, private keys, `.env` content, env dumps, Authorization headers or raw command output that holds them; write `[REDACTED: <kind>]`.
