# Handoff: kitchen-followups (master generation 3)

store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
written: 2026-11-24T17:12:02Z

A fresh master reads this before anything else. Write it for a stranger with
no chat access: what the run is for, where every unit stands, what you
decided and why, and the next commands to run. Keep it under 150 lines; the
store holds the detail, so point at files instead of quoting them.

## Goal

Land the kitchen follow-up units on br1anchen/open-pstack main as accepted units: each with a clean verdict, a joo review, a landing verdict and a passing land-check. Every push to main needs Brian's explicit approval in this thread (T-01a1fe7d); after each push, reply to Puck T-01a1eafa-f54b-71ff-890f-07428ab9c3e3 with the hash. The master never edits code; joo runs on every unit (`joo-dev ... </dev/null`); nest every new role thread under the anchor T-01a1fe7d-0da7-76c6-b123-db5d0999b3d6. Standing orders 1-29 hold, including 28 (GLM trial: no Amp credits, sidekicks on Sonnet/Claude Code; end at ~20 units per model or a clear decision rule, then tell Puck units per model; every handoff carries this) and 29 (snooze a role thread only when idle with no open work; never snooze masters).

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
| 010 roster-handoff | done | clean | accept |
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
| 037 landing-rebase-2 | done | - | accept |
| 038 snooze-role-threads | done | reject | accept |
| 039 roster-handoff-fix | done | clean | accept |
| 040 nest-every-start | done | clean | accept |
| 042 roster-relay-path | done | reject | accept |
| 043 snooze-role-threads-fix | done | clean | accept |
| 044 roster-relay-repo | done | clean | accept |
| 045 landing-rebase-3 | done | - | accept |
| 046 snooze-stop-and-done | done | clean | accept |
| 047 bundle-and-rotation-fixes | done | clean | revise |
| 048 landing-rebase-4 | done | - | accept |
| 049 bundle-and-rotation-fixes-2 | done | reject | revise |
| 050 landing-rebase-5 | done | - | accept |
| 051 reply-names-its-base | done | clean | revise |
| 052 relay-keeps-bundle-id | - | - | - |

dispatched: 051-reply-names-its-base.md (done); 052 written, not dispatched

Landed on main (no GitHub CI exists for this repo): be882968, daf51dc0, 699837d8 (038/043, 010/039/042/044), 29cc53c2 (040, 046, rebased by 050 onto Brian's commits to 3b951444). All accepted units through 050 are on main.
Open stack, not on main: 047 (97366025), 049 (648f31bf), 051 (b131308e), on origin as kitchen-code/kitchen-followups/unit-NNN. Verdict 051-v1 clean for 047 049 051, but joo found a real medium (relay bundles not kept by id; master confirmed at commands/relay.py:56-57), so 051 is revise and brief 052 fixes it from b131308e. 052 is written and classified (routine, pstack + pstack-docs, batch verify) but NOT dispatched: dispatch and queue refuse until the master rotates (compaction).
009/011/012/013: plans not yet run (see Next).

## Decisions

- Fold verifier and joo findings into one fix brief per stack rather than separate revise briefs (051 folded 049's reject; 052 folds 051's medium and six lows). Record: reviews/051-reply-names-its-base.md.
- Land stacks via a sidekick cherry-pick landing brief onto current origin/main, then `verify --landing`, `land-check`, `git merge-base --is-ancestor origin/main <tip>` before pushing; main moved outside this run twice.
- Ignore the "N units verified; rotate" advice; rotate only above 60% context or after a compaction (this rotation is for a compaction).
- `verify` takes `--covers` once per unit: `verify $S 052 --covers 047 --covers 049 --covers 051`.

## Open concerns

- Old idle sidekick threads T-01a1f99d…, T-01a1fe0d…, T-01a1f94a… are kept; update_thread on them errored. Consultant T-01a1f958-10b7-7675-b9d4-ad9f43f019fc is idle. Sidekick g18 T-01a20db5-f98d-70f3-87e0-b2323c3b3bac is idle after 051 and can take 052 (same area).
- Kitchen gaps to file at retro with `kitchen.sh feedback`: a sidekick published a stale master pair.json (repaired at c5261df3); a sidekick turn ended while gates ran (039); `feedback` with no registered maintainer reaches no one; the 6aaa6770 build role workaround.
- Unanswered: Puck is owed the GLM-trial end message (order 28). The ChenChess merge controller T-01a209ff-7d3c-7000-b557-034d754b8caf asked whether this run has a chenchess PR to land; this run lands on open-pstack, not chenchess, so the answer is "nothing to land" if not yet sent (check this thread).

## Next

1. `kitchen.sh dispatch $S $S/briefs/052-relay-keeps-bundle-id.md --send-only` (to sidekick g18), then wait for its report.
2. On 052 done: sync, `git push origin <head>:refs/heads/kitchen-code/kitchen-followups/unit-052`, `kitchen.sh verify $S 052 --covers 047 --covers 049 --covers 051`, nest the verifier, joo on b131308e..<head> into .amp/in/orb-backup/joo/052, write reviews/052-*.md.
3. On clean verdict + accept: accept 047/049/051/052, write a landing brief that cherry-picks 29cc53c2..<052 head> onto current origin/main, `verify --landing`, `land-check`, then ASK Brian to approve the push (no approval yet for this stack).
4. Then a brief for followups.md lows; plan rounds 014→unit 012, 015→unit 013, 016→unit 011 (includes Brian's rule: on an orb or PSTACK_MUX=amp default PSTACK_STORE=git, on local herdr/rex the directory store, explicit PSTACK_STORE wins), each with `kitchen.sh consultant`, `new-plan`, `discuss`; 041 verifier-lands (ChenChess feedback) last.
5. Run end: retro, Run log rows in the sidekick comparison artifact, feedback, Puck GLM-trial message.

## Pointers

- standing orders: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
- reviews/051-reply-names-its-base.md, verdicts/051-reply-names-its-base-v1.md, briefs/052-relay-keeps-bundle-id.md
- followups.md (non-blocking findings still to brief), events.tsv / decision log
