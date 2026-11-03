# Master: verifying and reviewing a unit

The detail behind step 6 of [master.md](master.md).

   - `kitchen.sh verify <store> <NNN>`. It returns every nine minutes (`--every N` sets the minutes; it and `--until-verdict` are alternatives) with
     the verifier's progress (exit 4) while the verifier works on: do other
     work, such as `kitchen.sh wait` on the sidekick, then
     `kitchen.sh verify <store> --wait` (exit 4 while it runs, 5 when none is
     open). A caller that can run a command in the background (Claude Code)
     starts `kitchen.sh verify <store> <NNN> --until-verdict` in the background
     instead: it returns once, on the verdict, the deadline, a stall or a
     provider error, never at the interval. When `[review].engine` is set, a
     clean verify and `take-master` print `warning: unreviewed: unit NNN ...`
     for each accepted unit whose review note cites no run of it, the same lines
     `land-check` holds the landing on; with no engine they print nothing. A
     verdict that was written but never read is closed by the next `kitchen.sh
     verify`: it prints `closed: <verdict> (<status>)` and everything `verify
     --wait` would print for it (findings, the `revise` line, `audit:`,
     warnings, the rotation nudge), then starts the new verification only when
     the closed verdict is clean; otherwise it exits as `--wait` would (2) and
     starts nothing. A `verify --wait` still running when another call closed
     its verification waits for that close (`KITCHEN_VERIFY_CLOSE_WAIT_S`, 120
     s; past it, exit 5 with `another call has held the close of <verdict>`;
     when the closing call is gone, its claim released or held by a dead process
     on this host, the wait ends at once with `the call closing <verdict> ended
     without closing it` and exit 5, and the rerun takes the close), then prints
     the verdict of the attempt it started on with `(closed by another verify
     --wait)` and exits 0 when clean, 2 otherwise, without touching the pane or
     scratch, logging, notifying or following a fallback; one whose attempt
     another call cancelled prints `verification closed elsewhere` and exits 5.
     Of two calls closing one verdict only the first to claim it (an atomic mark
     in `pair.json`, `.verifying.closing`, naming the host and process; a mark
     whose process is gone on this host is taken over, one from another host
     never, and every exit of the close releases it) logs, prints and counts it.
     A `verify` that finds its unread verdict being closed by another call waits
     for it (`KITCHEN_VERIFY_CLOSE_WAIT_S`, 120 s) and goes on, printing
     `closed: <verdict> (closed by another call)`; `kitchen.sh verify <store>
     --cancel` stops an open verification that has no verdict yet, closing its
     pane and scratch (it waits `KITCHEN_VERIFIER_EXIT_S`, 15 s, for the
     verifier to leave, and says nothing of `cancelled:` when another call
     closed the verification meanwhile) (`cancelled: <units>`; exit 5 with `no
     verification open` when none is). Every return of `verify` (progress,
     grace, a verdict) is logged
     as one `verify:<what>` wake. One verification is open at a time. When a
     unit reports done while another unit's verification is open, do not hold
     the sidekick: dispatch the next brief, and verify the finished unit
     together with the next one (`kitchen.sh verify <store> 008 009`) once the
     open verification returns. The last units before landing need no
     verification of their own: when nothing else is queued, go straight to
     `kitchen.sh verify <store> --landing`, whose clean verdict covers every
     unit whose head its range contains, then review those units against it. In
     strict-daily-window, 007 (51 min), 009 (38 min) and the landing (27 min)
     were verified one after another while the sidekick sat idle for 67 min. A
     partial or blocked unit can
     be verified or reviewed at the head its report names; the command notes
     it, and land-check still needs a clean verdict and an accepted review.
     A clean landing verdict counts as that verdict for every unit whose head
     its range contains (`covered:` in land-check); an inconclusive one is
     still yours to settle. The landing packet carries the Acceptance of each
     such folded unit and the verdict must answer every line of it.
     A `batch` unit may wait for the next done unit and verify together
     (`kitchen.sh verify <store> 004 005`); verify a batch before landing at
     the latest. Exit 2 with `reject`: run the `revise` command it prints,
     read the fix brief (it runs the `review-fix` workflow), dispatch it, and verify the fix together with the
     unit it fixes (`kitchen.sh verify <store> 007 --covers 003`): one
     verdict at the fix's head covers both, and land-check accepts it. A
     rejected landing verdict gets one fix brief over the union of the
     units' Scopes from the same `revise`; prove it with `verify --landing`
     again. Never
     re-verify the rejected unit at its own head. A second rejection, an
     `inconclusive`, or an `invalid` verdict is yours: read the verdict and
     the unit before anything else.
   - The verifier's deadline defaults to `max(45, ceil((3 x the slowest recent
     passing behavioral gates + the last recorded `kitchen.py setup` run, when
     the repo declares setup) x min(3, max(1, load1 / cores)) / 60))` minutes;
     `verify` prints the parts (`deadline 97m: 3 x 20m gates + 10m setup, x1.38
     for load 22 on 16 cores`) and `--timeout MIN` overrides it. When it passes
     with no verdict, a live verifier is asked once to write an `inconclusive`
     one with what it proved, and given `KITCHEN_VERIFY_GRACE_S` (600 s) to do
     it; `verify` then returns exit 4 with the grace left, and
     `verify --wait` resumes the same expiry. A working Devin is not prompted
     (Enter would cancel its command) and a blocked verifier gets no grace.
   - A verifier never seen working, with nothing typed and no verdict, gets
     its packet once more (`redelivered:`). One idle for
     `KITCHEN_VERIFY_IDLE_S` (600 s) with no verdict and no process of its
     own running in its pane does not sit out the deadline: `verify` closes
     it as `stalled` and exits 4 with the command to rerun. Any attempt that
     ends without a clean or rejecting verdict prints the verifier's
     `transcript:` and whether the packet ever reached it; one that never got
     its packet says nothing about the work, so rerun it. A verifier's
     exit is answered like a sidekick's ("Exit and stop tasks" for Claude's
     background-work dialog) and waits `KITCHEN_VERIFIER_EXIT_S` (15 s) for
     herdr to drop the agent. When herdr refuses the exit command, the close
     says `verifier exit refused: <herdr error>` on stderr and still closes
     the pane and removes the scratch.
   - `--covers` takes one unit per flag or a comma list, and the fix is named
     last: `verify 015 --covers 010,011,013` checks out 015's head. `verify`
     refuses a head that does not contain a named unit's head, and says which.
     The range starts at the oldest named unit's dispatch head by git ancestry,
     whatever order the units are named in; two heads with no common order are
     refused (`units NNN and MMM have no common order: pass --base REV`), and
     `--base` wins. The packet lists each unit inside the range that is not
     named under `## Context: units between`, with its review path and the
     review's Amendments, for the verifier to read and not judge.
   - A verifier that hits its provider (a rate limit, an outage) writes no
     verdict. `verify` reports `inconclusive` with the provider's error,
     including when the limit resets, and a routine unit verifies again on
     the verifier's fallback in the same call (the roster's
     `[verifier.fallback]`, else the sidekick's), once, and only when the
     fallback is a different kind from the one that failed. Escalated units
     have no fallback by default. The verifier comes from the host roster's
     `[verifier]` (routine, default the current sidekick) or
     `[verifier.escalated]` (default Claude Opus); the repo no longer
     chooses. `--kind KIND` picks the verifier's kind yourself, with the
     matching entry's arguments when its class names that kind.
     `verify:missing` now means the verifier ended without a verdict and
     without a provider error: read the packet and the pane.
   - The start line names the configured agent, `verifier <name> (<kind>
     <model arguments>)`, or `(<kind> (model unspecified))` when the entry
     sets no model, so you see which model proves the unit before the verdict;
     it is what the CLI was asked for. The packet records it as
     `verifier-agent:`, with `verifier-class:` and a `verifier-outcome:` the
     host updates per attempt. `kitchen.sh fleet` ends with a table by agent
     and class built from those; read it as reported outcomes on different
     workloads, not as model accuracy. `kitchen.py doctor` prints the resolved
     routine, fallback and escalated verifier and where each came from; the
     escalated line adds `(differs from the default claude --model
     claude-opus-5-5)` when the roster names another one.
     It also warns on `scratch setup` when the latest `[scratch].setup` run in
     the host timings failed (`the latest setup run failed at <time>: <commands>`);
     a later passing run clears the warning.
   - A unit is measured from its dispatch head, but never from before where
     its branch leaves trunk: a unit dispatched before a landing and then
     rebased onto the new trunk is measured from there (`verify` prints a
     `note:` when it moves the base). `--base REV` sets it by hand.
   - Review the unit's range (from the dispatch head, or the base `verify`
     used) with what the repo's owner uses: your own read, your agent's
     built-in review, or an external tool you run yourself. The kitchen runs
     none of them and reads no tool's output. When `[review].engine` is set
     (`kitchen.py validate` prints `review engine:`), the owner requires that
     engine on every unit: run it on the unit's range and add
     `engine: <name> <run id or artifact path>` under the review note's
     `head:` line, or land-check refuses the unit. The engine a unit needs is
     the one classify stamped on its brief at dispatch (`review-engine:`): an
     engine named after a unit was dispatched does not reach back to it, and a
     brief with no stamp (classified by an older build) needs the engine set
     now. A unit whose range only deletes (read from `git diff --name-status -z`
     and `--numstat -z`,
     NUL-separated so no path is quoted: every path deleted, or modified by
     removing lines only; a created, renamed or mode-changed file, a modified
     binary or a failing git command refuses) may instead say
     `engine: skipped (deletion-only, proof: <command>)`; for a unit that adds
     lines, land-check refuses that line (`skipped is only for a deletion-only
     range`). For `joo`:
     `joo-dev engine review --repo <root> --range <base>..<head> --json --out <store>/scratch/joo-NNN > <store>/scratch/joo-NNN.json`
     (or `joo`). `--out` names a directory where joo keeps the run's state
     (its index, snapshots and `runs/<run id>/`); the review itself is the
     JSON on standard output, an envelope `{ok, result, version}`. Cite that
     file and the run id it holds (`.result.runId`) and read the findings from
     `.result.findings`. For a connected review, write `.result` to a file of
     its own and pass that file to
     `joo-dev connected review context --artifact <file> --json`; it refuses
     the envelope and the directory.
     Decide each blocking finding:
     fixed (a note, or a fix brief to the sidekick with `playbook: review-fix`,
     queued behind its running unit when it is busy), a follow-up in the
     review note (it goes to `followups.md`), or dismissed with the reason. A finding that a file is
     "not in the range" may be a binary add a reviewer cannot read: check
     `git diff --name-status <base>..<head>` before dismissing it. A later
     review round, a landing review above all, can raise again what an
     earlier one settled; check the run's review notes before re-reading.
