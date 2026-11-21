# Handoff: kitchen-followups (master generation 2)

store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
written: 2026-11-21T14:22:14Z

A fresh master reads this before anything else. Write it for a stranger with
no chat access: what the run is for, where every unit stands, what you
decided and why, and the next commands to run. Keep it under 150 lines; the
store holds the detail, so point at files instead of quoting them.

## Goal

Land the kitchen follow-ups (top-followups.md items, Brian's in-run requests, the untangle and ChenChess bug reports) as accepted units with clean verdicts, a clean landing verdict and a passing land-check; every push to main needs Brian's explicit approval (standing orders 18-27). Brian approved (2026-11-21, this thread) pushing the whole accepted stack to main "now", then continuing with 010: that approval is NOT consumed yet.

## Units

| unit | report | verdict | review |
|---|---|---|---|
| 001 swallowed-failures | done | clean | accept |
| 002 amp-idle-and-stop | done | clean | accept |
| 003 packet-accepted-deviations | done | - | accept |
| 004 master-bundle-branches | done | clean | accept |
| 005 kept-sidekick-session | done | clean | accept |
| 006 land-check-gone-head | done | clean | accept |
| 009 git-store-default | - | - | - |
| 010 roster-handoff | - | - | - |
| 011 git-store-default-on | - | - | - |
| 012 store-pinned-build | - | - | - |
| 013 git-handoff | - | - | - |
| 017 swallowed-failures-fix | done | clean | accept |
| 018 amp-idle-and-stop-fix | done | clean | accept |
| 019 role-stamp-under-git | done | - | accept |
| 020 landing-packet-amendments | done | - | accept |
| 022 migrated-threads-archive-nest | done | clean | accept |
| 023 dispatch-after-ready-sends | done | reject | accept |
| 024 verify-names-excluded-fix | done | clean | accept |
| 025 reply-deletes-no-master-state | done | reject | accept |
| 026 dispatch-after-ready-sends-fix | done | reject | accept |
| 027 master-is-anchor | done | clean | accept |
| 028 dispatch-after-ready-sends-fix | done | clean | accept |
| 029 master-is-anchor-fix | done | clean | accept |
| 030 reply-deletes-no-master-state-fix | done | clean | accept |
| 031 master-bundle-branches-fix | done | clean | accept |
| 032 master-bundle-branches-fix2 | done | reject | accept |
| 033 master-bundle-branches-fix | done | clean | accept |
| 034 land-check-gone-head-fix | done | reject | accept |
| 035 branch-ahead-not-diverged | done | - | accept |
| 036 land-check-gone-head-fix | done | clean | accept |
| 037 landing-rebase-2 | - | - | - |

dispatched: 010-roster-handoff.md

Landed on main be882968 (pushed 13:25Z, first approval, consumed): 001, 002, 003, 017, 018, 019, 020. PR #13 (027+029) merged at b681abc.
Accepted, not landed (the approved second push): 005, 022-026, 028, 030-036, 004, 006 = commits c5cf2504..84ea957f (46 commits, kitchen-code/kitchen-followups/unit-036).
Open: 010 dispatched to sidekick g16 T-01a1fe59-398e-71cb-8cac-577e8fe343df (Sonnet), from unit-036. 037 landing-rebase-2 is written and classified but NOT queued (queue refused: master compacted); queue it as the next brief. 009 is superseded by plans 014-016 (units 012, 013, 011). 012/013/011 wait on escalated plan rounds 014 -> 015 -> 016 (plans in plans/), each with a fresh consultant via kitchen.sh consultant.

## Decisions

- Second push blocked: master's trial rebase of c5cf2504..84ea957f onto be88296 stops at fe439aee (022 nest --discover) against PR #13's take_anchor in nest.py. Earlier conflicts are tests/SPEC.md only. Master never edits code, so brief 037 hands the rebase to the sidekick (decisions.tsv land-2).
- 037 step 2: discover's missing-anchor default moves from the oldest master to the current master, recorded top-level, because order 27 / PR #13 make the current master the anchor. Changes an accepted unit's behaviour; say so to Brian at landing.
- Accepted 006, 034, 035, 036 on clean verdict 036-v1; joo lows in followups.md. Sidekick runs on Sonnet via claude-code (Amp credits out; Brian's choice); GLM A/B ended at 002.

## Open concerns

- Amp credits exhausted: Amp-billed models (GLM, Amp Opus) fail; claude-code Sonnet and GPT-6.1 Sol (ChatGPT) work. Verifiers so far ran fine.
- Puck T-01a1eafa-f54b-71ff-890f-07428ab9c3e3 is owed: the second main hash with its units, then 011-013 hashes and units by model at run end.

## Next

1. `kitchen.sh queue <store> briefs/037-landing-rebase-2.md` so it runs after 010. On 010's report: push unit-010, joo, verify 010.
2. On 037's report: push to kitchen-code/kitchen-followups/unit-037, joo on be88296..head, `verify <store> --landing --base be88296... --head <037 head>`, land-check, then push to main (Brian already approved this push) with a CI subscription, and reply to Puck with the hash and units. 010 lands in a later push with its own approval. Then plan rounds 014, 015, 016 and units 012, 013, 011; retro, artifact rows, feedback.

## Pointers

- standing orders: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
- briefs/037-landing-rebase-2.md; decisions.tsv (land, land-2); verdicts/landing-v1.md (first landing); followups.md; reviews/036. Landing worktree /home/user/workspace/landing (branch landing-followups at be88296, clean). Rebase helper .amp/in/orb-backup/master/rebase-spec.sh (SPEC.md-only conflicts). joo copies under .amp/in/orb-backup/joo/.
