- 002 audit low: commands/stop.py:173 the pair's own stop no-thread path (an Amp.start entry before its first prompt) has no test.
- 017 joo lows: gitstore.py:1197 stale origin/HEAD never repaired after the fallback (extra fetch + ls-remote per sync); gitstore.py:1196 fallback also runs after a transient fetch failure of a trunk that still exists.
- 020 joo low: verify_packet.py:392 accepted_amendments shows Amendments from a review whose verdict is revise or reject; context_section checks verdict == accept.
- (023, joo low) send.py:181 `took_a_brief` keys on a brief dispatched to the generation, so a new generation busy on a discuss PLAN makes a later send poll up to PAIR_ROTATE_READY_S before refusing.
- (023, joo low) no test that a working sidekick whose generation already took a brief is refused at once without polling; a regression of `took_a_brief` to always-False would only add 60s waits.

- 027/029 (joo, low): nest.take_anchor's `old != thread` check is redundant; guard against a non-dict `.anchor` in pair.json.

- 004 (joo, low/note): move_checkout could pass whether the target is checked out instead of asking git again in diverged and recovery_recipe; make `exclude` a required argument of git_bundle.

- 030 (joo, low): the per-command context memo also caches a failed read; consider not caching a None so a transient amp failure does not blind later readers in the same command.

- 031 (verifier/joo): a rejected hand-over leaves its import refs until the next apply prunes them; a timed-out fetch is SIGKILLed, which can leave ref locks and orphaned transports; on fetch failure the bundle is cut at a possibly stale tracking ref; the receiving side's fetches have no timeout; PSTACK_BUNDLE_FETCH_TIMEOUT_S and PSTACK_BUNDLE_KEEP are undocumented.

- 022 (joo, low): before_migration treats a retirement time `at` as a start time; filter archived threads after merging pair.json and registry names; share one run-thread-set helper with Amp.thread_rows; route nest --discover through mux.get(); nest --discover does not mark threads nested.

- 032 (verifier/joo): prune_sets' "a set a line names stays" guard protects nothing reachable today (lines name only the set being applied); git.run's **extra with a hand-written TypeError could be named keyword parameters if the size guard allows; prune_sets' printed/keep parameters could be required; a 0 fetch timeout took two waits against a slow origin (untraced).

- 024 (joo, low): print the excluded notice only for a covers packet, not a landing packet, and reuse the units_between result instead of recomputing it.

- 006 (joo, note): keep refs under refs/pstack/keep are GC roots that nothing deletes; removal after a run closes needs a retention policy.

- 033 (verifier obs. 2): with the checkout on X and the sender's Y only ahead here, [brief 035, Brian asked to fix before landing] the one Y line says 'diverged' with a recovery recipe. joo lows: make apply_branches' `reported` required; drop a redundant conditional in the branch-target helper.

- 034 (joo, low): a missing landing packet silently drops the gone-head blame; carries_change builds a full StackIndex per accepted unit though only the newest carrier is used; dead `.md` fallback, rpartition filter and `lost and` guard in landing.py.

- 035: the ahead line under a detached checkout has no test of its own.
- 036 (joo, low): one helper for the bounded, no-prompt origin fetch, used also for the head fetch above it, which can still hang.

## joo on the landing stack be882968..daf51dc0 (run 2026-11-21T15-08-59-362Z-0cf5d75c; 4 medium, 27 low) — fix brief 040 after the landing

- bundle.py:379 (medium): a late reply still deletes a master-written file outside the MASTER class (gates-mode verdicts under verdicts/); a reply should never delete a file the sender did not write.
- spawn.py:226 (medium, regression): record_started clears rotation_required before READY, so a replacement that never boots cannot be rotated again; dispatch and rotate refuse each other.
- bundle.py:173 (medium, regression): with origin unreachable the bundle is cut at a stale origin ref instead of carrying every commit; the receiver's fallback `git fetch origin` (bundle.py:546) is unbounded.
- bundle.py:258 (medium, regression): an ordinary send with evidence refs is cut against the sender's --remotes, which a receiver may lack, so local-only evidence is lost.
- nest.py:175 / nest.py:194 (low): run_threads re-adds archived threads; discover's record_pending falls back to anchor()'s oldest master when current_master finds none.

## s4-hero-1258 master feedback (T-01a1feab-6ada-7110-87cc-63c074d88bde, 2026-11-21)

- Items 1-4 (nesting: starts print no nest lines, archived threads cannot take a parent, take-master nests under the old master, `nest --done` unchecked): brief 040 nest-every-start.
- Item 5 (spawn after `close` keeps rotation_required, dispatch refuses after READY): with joo's spawn.py:226 medium (record_started clears rotation_required before READY) in brief 046, the joo-mediums fix; one rule for when rotation_required is set and cleared.
- Item 6 (a fresh orb has no roster; spawn fails "no --kind and no [sidekick] kind"): units 010/039 carry the roster to the next master; brief 046 adds the fallback to pair.json `.sidekick.chain` (and the recorded entry for other roles) when the orb has no roster.
- `feedback` with no registered maintainer delivers nowhere useful (the report reached this run only by a thread message): retro feedback.

## ChenChess orb-mbx-warm feedback (T-01a1fed1-e978-74a5-a348-340b497bb301, 2026-11-23)

- The landing verifier and the lander compile the same tree in two orbs (~17 min cold median vs 1m42s warm rerun): plan 041 verifier-lands (escalated, plan round), after the queued work. Joo-mediums fix is brief 046.

## 039 (joo 2026-11-23T20-14-04-846Z-ae62c835)

- master.py:539 (medium): the roster relay names the old orb's absolute store path as the upload destination; steer's relay uses a repo-relative `.git/...` path. If the next master's store path differs, the upload misses and the next master quietly keeps its own roster (039's fallback). Brian: fix before landing; brief 042 roster-relay-path.
- gitstore.py:97 (low): the ignore glob `sessions/*-roster.toml` is wider than the LOCAL regex `sessions/master-g\d+-roster\.toml`; make them match: brief 042 roster-relay-path.

## 042 (joo 2026-11-23T21-21-49-638Z-cd5a1769)

- master.py:559 (medium): take-master finds the upload through `repo_of(store)`, which reads the published pair.json's old paths: fix brief 044 roster-relay-repo before landing 010/039/042.
- 042 test keeps the store at the old master's path (report Deviations); 044's test covers a different checkout path.

## 043 (joo 2026-11-23T22-03-17-839Z-801d9887)

- snooze.py:181 (medium): `--done` checks eligibility after update_thread already snoozed the thread; on refusal the thread is snoozed in Amp but recorded awake. Fix: the refusal tells the master to undo it (`update_thread(thread=<T>, snooze="off")`), or `--done` records what was done and prints the warning. Brief 046.
- 043 verdict observation: a dispatch to a snoozed sidekick runs classify and the archive sweep before `require_awake` refuses; move the check ahead of the sweep. Brief 046.

## 040 (joo 2026-11-24T00-02-06-571Z-21c69123)

- nest.py:310 (low): `process_prog()` re-parses sys.argv and reloads the variant for the program name; pass `ctx.prog` down instead. Brief 046.
- 040 behaviour change: take-master keeps the old master (`kept: nest pending`) until `nest --done`; a later sweep archives it.

## Landing stack 3 (joo 2026-11-24T00-40-25-500Z-8f7e5175 on eb8228f9..699837d8) — first in brief 046

- amp.py:1214 / amp.py:1191 (medium x2): `stop` and `close` to a snoozed role refuse in `Amp.send` only after the queue is cleared and `send-stop` is logged. Recoverable (the refusal names update_thread snooze="off" + --wake), but run end hits it. Fix: STOP is exempt from require_awake, or stop/close wake first; check before clearing the queue.
- snooze.py:180 (medium, = 043's): --done refuses after the external snooze; give the undo instruction.
- master.md:93 (low): every CLI send or relay to a snoozed thread refuses, not only dispatch/discuss/consult/steer.
- snooze.py:86 (note), snooze.py:177 (low): docstring vs own_thread check; a dedicated PairError subclass for snooze refusals.
- landing-v3 note: `kitchen.sh help` does not list `snooze`. Brief 046.
