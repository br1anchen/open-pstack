# Handoff: kitchen-followups (master generation 1)

store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
written: 2026-11-21T00:11:22Z

A fresh master reads this before anything else. Write it for a stranger with
no chat access: what the run is for, where every unit stands, what you
decided and why, and the next commands to run. Keep it under 150 lines; the
store holds the detail, so point at files instead of quoting them.

## Units

| unit | report | verdict | review |
|---|---|---|---|
| 001 swallowed-failures | done | clean | accept |
| 002 amp-idle-and-stop | done | clean | accept |
| 003 packet-accepted-deviations | done | - | accept |
| 004 master-bundle-branches | - | - | - |
| 005 kept-sidekick-session | done | clean | revise |
| 006 land-check-gone-head | - | - | - |
| 009 git-store-default | - | - | - |
| 010 roster-handoff | - | - | - |
| 011 git-store-default-on | - | - | - |
| 012 store-pinned-build | - | - | - |
| 013 git-handoff | - | - | - |
| 017 swallowed-failures-fix | done | clean | accept |
| 018 amp-idle-and-stop-fix | done | clean | accept |
| 019 role-stamp-under-git | done | - | accept |
| 020 landing-packet-amendments | done | - | accept |
| 022 migrated-threads-archive-nest | - | - | - |
| 023 dispatch-after-ready-sends | done | reject | revise |
| 024 verify-names-excluded-fix | - | - | - |
| 025 reply-deletes-no-master-state | - | - | - |
| 026 dispatch-after-ready-sends-fix | - | - | - |

dispatched: 023-dispatch-after-ready-sends.md
queued: 025-reply-deletes-no-master-state.md

## Goal

Fix the 11 top follow-ups that run sidekick-ab2 left (`notes/top-followups.md`, details in `notes/kitchen-gaps.md`), plus Brian's units 011-013 (git store as default, pinned-commit build install, git-store hand-over) and the chenchess s4-ledger-1252 issues (`notes/s4-ledger-1252-issues.md`). Done means every unit has an accept review and a clean verdict, a clean `verify --landing`, land-check passes, and Brian has approved the push to main in the anchor thread.

Constraints from Brian, beyond standing-orders.md:
- Pushing `kitchen/*` and `kitchen-code/*` branches to origin is allowed. **Every push to main needs his explicit approval in the anchor thread T-01a1f946-a105-76cd-ad9b-cfd01bbf4cce.** No push to main has happened.
- GLM-5.3 vs Sonnet A/B: run GLM (Amp-billed) only on a few routine units, and keep escalated units on Sonnet 5.5 (Claude Code). GLM budget left: one unit, planned for 010. At each retro, append rows to the Run log of https://ampcode.com/artifacts/sidekick-model-comparison-glm-5-3-vs-sonnet-5-5-41b45815-a276-4b6b-9703-cc97e5fa9248. Read it first, publish with base_revision, and leave the Weekly summary alone. Partial rows were added at this rotation (revision 2).
- Brian set the order: 023 and 025 right after 005, before 004.
- **Owed to Puck thread T-01a1eafa-f54b-71ff-890f-07428ab9c3e3:** the commit hashes for units 011-013 when they are ready, and the units run on each model when the run finishes or something blocks it.
- The chenchess sender (T-01a1f94d) asked for no reply.

## Units annotated

- Accepted with clean verdicts: 001 (+ fix 017), 002 (+ fix 018), 003, 019 and 020. The verdict for 003, 019 and 020 is `verdicts/020-...-v1` (it covers all three). The code is on origin `kitchen-code/kitchen-followups/sidekick-g3` at c5cf250.
- 005: done at b1ca49f (sidekick-g4), with a clean Opus verdict. The review is **revise**: joo found that a stale reply still rolls back other roles' files. That fix is folded into **025**, which is escalated and verified with `--covers 005`.
- 023: done at 4bca786 (sidekick-g5). The verdict is **reject**: tests inherit the 60 s READY wait and sleep for real. The review is revise, and the fix brief **026** is written and classified. Verify it with `--covers 023`.
- 008 and 021 are plan rounds for 005 (agreed with amendments A1-A5, in `reviews/021`).
- Brief 009 is folded into plan/brief 016/011. Plans 014, 015 and 016 are drafted for briefs 012, 013 and 011 but not yet discussed.
- 004, 006, 010, 022 and 024 have routine briefs written and not yet dispatched.
- Nothing has landed. Local `main` is at c5cf250, unpushed. origin/main moved to 7218f31 (someone else's joo fix), so the landing rebases the stack onto it.

## Decisions

- No patch-id auto-reset or patch-id coverage (consultant counterexamples, advice 004/006).
- pair.json merge uses an allowlist: only a positive current-sidekick `rotation_required` (plans 008/021, `reviews/021`).
- The git store becomes the default last, after the pinned build (014) and the git hand-over (015) (advice 011).
- The final-tip landing invariant and git-store evidence durability are follow-ups, not units.
- The Design 4 narrowing in 005 is accepted: clear the flag only on non-waiting backends.
- The master rotated after one compaction, at about 8% context, on Brian's explicit approval in the anchor thread (dispatch exit 9).

## Open concerns

- The master's build (local main, c5cf250) lacks 005's fix. After a sidekick's READY, pair.json often still has `.sidekick.rotation_required=true` and dispatch refuses. Workaround: `jq '.sidekick.rotation_required=false' $S/pair.json > /tmp/pj && cp /tmp/pj $S/pair.json`.
- 023's fix is not in the master's build either, so the first dispatch after READY may archive and not send. Rerun the same dispatch if no `sent:` line appears.
- `queue` holds one brief, and `--replace` swaps it.
- New sidekicks start at origin/main without the stack. Every brief's Context must say: `git fetch origin kitchen-code/kitchen-followups/<latest> && git checkout -B kitchen-followups FETCH_HEAD`; do not rebase onto origin/main. The latest code ref is sidekick-g5 (4bca786), until 026 or 025 push a newer one.
- Kitchen gaps to file at the retro with `kitchen.sh feedback`, all recorded in decisions.tsv:
  - stale-merge rollbacks
  - rotate leaves rotation_required set
  - store-to-git publishes no code
  - the role startup stamp under git
  - the roster alternation is per run, not per unit
  - a rotation is recorded after a failed push
  - new sidekicks start at origin/main
  - duplicate READY messages
  - dispatch exit 9 conflicts with "don't create another master thread"
- joo is `joo-dev --range A..B --repo . --out /tmp/joo/NNN </dev/null >log 2>&1 &`. Findings are in `runs/*/executions/*.json`. Copy the run to `.amp/in/orb-backup/joo/NNN-run`. joo-dev keeps running after the executions are written; kill it once the manifest says completed.

## Next

1. `take-master`, then sync: `PSTACK_MUX=amp pair.sh sync --branch $S kitchen/kitchen-followups`. Sidekick g6, T-01a1fb29-d3a6-77ce-99fe-6741609db301 (Sonnet on Claude Code), is idle with no brief.
2. Clear rotation_required if it is set, then `kitchen.sh dispatch $S briefs/026-dispatch-after-ready-sends-fix.md --send-only`. 026 is routine and starts from sidekick-g5 4bca786. Queue 025 next.
3. On 026's report, run joo on 4bca786..head, then `verify $S 026 --covers 023`, and write the reviews.
4. Dispatch 025. It is escalated, needs Sonnet, and should start from 026's code ref. Update its Context line, which currently says sidekick-g5 4bca786. Verify it with `--covers 005`.
5. Then 004, 022, 024 and 006. Then 010 on GLM: switch pair.json `.sidekick.start_args` to `["--model","zhipuai/glm-5.3"]`, set `.chain_index=0`, then `kitchen.sh rotate`. Then switch back to Sonnet: `["--mode","claude-code","--features","model:claude-sonnet-5-5"]`.
6. Run plan rounds with `kitchen.sh discuss` for 014, then 015, then 016, and dispatch 012, 013 and 011 in that order. Brian wants tests and docs for each.
7. End of run:
   - `verify --landing`, land-check, retro, the artifact rows, feedback.
   - Reply to Puck with the 011-013 hashes and the units on each model.
   - **Ask Brian before any push to main**, and ask whether to close the roles.

Models so far: 001 Sonnet on Amp, 002 GLM, and 003 onward Sonnet on Claude Code.

## Pointers

- Standing orders: `standing-orders.md` (Brian's rules are 18-26).
- Work list: `notes/top-followups.md`, `notes/kitchen-gaps.md`, `notes/s4-ledger-1252-issues.md`, `followups.md` (the low follow-ups, appended).
- Reviews: `reviews/021` (005's amendments), `reviews/023`. Verdicts: `verdicts/023-...-v1`. Advice: `advice/011-git-store-default-on-c1.md` (the 014→015→016 order).
- Master scratch in the old orb: `.amp/in/orb-backup/master/fill.py` (fills a brief from a body file: header lines, `---`, then the sections). Body files are `b*.txt`. The joo runs are in `.amp/in/orb-backup/joo/`. Copies of fill.py and fillc.py are in the store at `notes/master-scripts/`.
- Threads:
  - consultant T-01a1f958 (idle)
  - old sidekick g4 T-01a1fa73 and g5 T-01a1fb0e
  - verifier for 023, T-01a1fb2a
  - The anchor for nesting is T-01a1f946-a105-76cd-ad9b-cfd01bbf4cce.
