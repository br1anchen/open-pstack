# Master

You own judgment and the human, never the working tree. In the kitchen your
attention is the scarce resource: spend it on plans, precise briefs, review
triage, and exceptions, and let the gates and the verifier carry routine
work. Follow [the pair master](../../pstack-pair/references/master.md)
for everything below that does not differ: check-ins and steers, queueing,
pausing, approval dialogs, Devin sessions, waits, and recovery.

`kitchen.sh` below means `~/.agents/skills/pstack-kitchen/scripts/kitchen.sh`;
`kitchen.py` is beside it.

## Steps

1. **Frame.** As in the pair: route through poteto-mode, stop before any
   code write, state the done predicate, split the work into units that each
   end in a verifiable state. `in-your-own-words` is an optional first step
   when the human's goal is vague.
2. **Open the store.** `kitchen.sh init <slug>` from a fresh, MCP-free
   session (see Rotate yourself). Put the human's constraints
   and the landing policy in `standing-orders.md`. Check the kitchen:
   `kitchen.py validate`; a missing or failing kitchen goes to
   pstack-kitchen-setup first.
3. **Spawn the sidekick.** `kitchen.sh spawn <store>` takes the kind, args,
   and fallback from the host roster; `--kind` overrides. `--tab` (or
   `PSTACK_PLACEMENT=tab`) gives the sidekick, the consultant, and each
   verifier a tab of their own instead of a split. Read
   `reports/000-ready.md`.
4. **Classify before you plan.** Draft the brief's Scope first (one
   repo-relative path or glob per line, in full; dispatch refuses `a, b` on
   one line) and run `kitchen.sh classify <store> <brief>`. Classify keeps a
   reason you wrote after the class (`risk: escalated (contract change)`) while
   the class is unchanged and rewrites the line only when the class changes.
   `risk: escalated: contract change` reads as `escalated` too.
   Classify also resolves the brief's playbook and overlays once and stamps them
   in `<store>/compositions/<NNN>.json`.
   A failed step's `gate-fail` event names the log of that run
   (`steps/jobs/<unit>-<sha>/out.<n>`); each run keeps its own.
   A `warn: Scope covers ...` line means the Scope reaches a generated file's
   source but not its outputs: add them to may write before dispatch, or say
   in the brief why not.
   A `warn: Acceptance runs <command>, which Verify does not` line names a
   backquoted command in Acceptance (one starting with git, python3, test,
   grep, awk, uv or bash) that neither Verify nor a touched profile's fast or
   behavioral gate runs (Verify runs it when it appears there whole, between
   command boundaries: `test -f a` is not run by `test -f ab`), so `finish` would not rerun it: add it to Verify, a
   "prints nothing" check as `test -z "$(...)"`, or say in the brief why not;
   `queue` prints it too.
   - **Routine:** no plan round. The brief is the plan, so make it precise:
     Steps of fifteen to thirty minutes that name the files and functions,
     Acceptance a script or command can check. The sidekick may run a cheaper
     model; a decision you leave open is one it may get wrong.
   - **Escalated:** start the consultant with
     `kitchen.sh consultant <store> --reason "<the first escalate line>"`,
     then a plan round as in the trio: `kitchen.sh new-plan`, set
     `risk: escalated`, `kitchen.sh discuss`. A plan carries `contract: 2`
     in its header and a `playbook: <name>` line (and optionally `with: <id>`)
     under each Steps block; `discuss` refuses a block without a mapped
     playbook and writes each block's consultant and master parts into the
     plan. Consults work as in
     [the trio master](../../pstack-trio/references/master.md), with
     `kitchen.sh` for `pair.sh`. Classify stamps `steps:` on the brief: `master`
     when the unit's own paths or a `risk: escalated` you wrote escalate it,
     `gates` when only its plan is escalated, so its gates prove each step. Write
     `steps: master` on a plan-only brief to read every step; classify never
     lowers it.
5. **Dispatch and wait.** `kitchen.sh dispatch <store> <brief>`, then
   `kitchen.sh wait <store>`, each in your harness's background mode where it
   has one (Claude Code: run in the background and you are re-invoked when it
   returns), so a wait never holds your turn. The kitchen's wait is quiet: a
   check-in with nothing flagged does not return. It returns for a report, the
   steps of an escalated unit whose brief says `steps: master`, a blocked sidekick, a digest that carries a flag
   (a stale log, a write outside Scope, an objection, an open blocking note (a
   blocking note you publish is also delivered to a working sidekick at once),
   a timebox overrun, a pause), or after `--max` minutes (an hour) with the
   latest digest. A check-in the wait swallows is not logged as a wake; the
   one it returns is. Read a digest for direction only, as the pair master does;
   steer only when your review would otherwise say revise. A steer to a sidekick
   that is idle with a unit reported `blocked` or `partial` resumes the unit:
   its scope effect goes into the brief and the brief is sent again, with the
   old report set aside; the busy rule and the other cases are in the
   pstack-pair master reference. When you work outside the kitchen in your own
   session (another branch, a prototype, a question for the human), leave a
   `kitchen.sh wait <store>` running in the background so a report or a step is
   not left waiting for you; in lichess-token-1083, steps sat 13 min unread
   while the master did unrelated work. Run one wait at a time: a second wait on
   the same store (`wait` or `dispatch`) ends the first with `superseded: a
   newer wait owns this store; this one ends` (exit 4) and takes over, so two
   overlapping waits no longer steal each other's events; a report announced
   while no wait owns the store is not returned again. The escalated `discuss`
   (it waits on the sidekick and the consultant with a bare `herdr agent wait`)
   and `stop` do not claim the store, so a report announced during them is
   marked seen and your next wait shows it on the idle path (`idle:`, then the
   queue note and review delta). Never wrap
   `dispatch` in a short `timeout`: when you need the call back at once, pass
   `--send-only`, which returns as soon as the brief is delivered, then wait.
   A due rotation runs before the send, so `--send-only` can take up to a
   minute plus the READY wait and the fresh session's first turn (`PAIR_ROTATE_READY_S`); a fresh
   session that stays working, turns blocked or vanishes fails the rotation and sends nothing.
   Delivery itself ignores TERM, so a killed dispatch never leaves a brief
   recorded but unsent. A sidekick of any kind whose last unit is done is
   rotated by `dispatch` or `discuss` itself (`rotated:` in its output), so
   there is no separate `rotate` call; after partial or blocked work it keeps its
   session. A log that has gone quiet is not flagged stale while a command
   the sidekick started since its last line still runs in its tree; the
   digest shows `running: <command> for Nm` instead, or `editing: last change
   Nm ago` while files or HEAD changed within the interval (activity, not
   advancement; the timebox still applies). A done report that `finish` would
   refuse (open blocking notes, a head older than the last recorded step, or
   Verify commands that have not passed at its head) does not wake you; a wait that ends on one says `written but not ready`.
6. **On a done report:** verify, then review.
   Run `kitchen.sh verify <store> <NNN>` (in Claude Code, start it with
   `--until-verdict` in the background). One verification is open at a time;
   a done unit that arrives while one is open is verified together with the
   next. `verify` returns exit 4 with progress while the verifier works; then
   run `kitchen.sh verify <store> --wait` (exit 5 means none is open). A reject
   goes to `revise` and the fix is verified with `--covers`. A rejected unit is
   never re-verified at its own head; a second rejection, an `inconclusive`
   verdict or an `invalid` one is yours to read.
   Review the unit's range with the owner's engine and cite it in the review
   note. With `[review].engine` set, the review note cites the run under
   `head:` as `engine: <name> <run>`, or land-check refuses the unit.
   Details: [master-verify.md](master-verify.md).
7. **Review and accept.** With a clean verdict and every blocking finding decided,
   write `reviews/NNN-<slug>.md` from the pair's review template, reading the
   report's `review-delta` and the verdict, not the whole diff. When
   `verify` printed `audit: yes`, read the unit's whole diff as the pair
   master would; anything the kitchen missed is
   `kitchen.sh catch <store> <layer> "<what>" <NNN>`, naming the layer that
   should have caught it and the unit it was in.
8. **Land.** When the last units were folded into the landing verification
   (its packet lists their Acceptance, which the verdict answers line by line),
   its clean verdict comes first, then the review notes for those units, then
   `kitchen.sh land-check <store>`. Review the stack as a
   whole for anything between units, then the landing brief. Review it from
   where it leaves trunk (origin's default branch, else main or master), the
   base `verify --landing` prints, so a rebase never pulls trunk's commits
   into it. The landing goes only as far as `[landing].mode` allows and the
   kitchen never merges; the human lands.
   Once the landing is handed to the human and more work follows, rotate
   (`new-handoff`, then `rotate-master`): the next issue starts on a fresh
   master.
   Details: [master-landing.md](master-landing.md).
9. **Retro and close.** `kitchen.sh retro <store>`. It ends with the repo's
   lessons and the strongest candidates the ledger holds: the same trouble
   in one area across runs (a gate check failing in 3, a reject or a
   redirecting steer in 2), or a catch. Each candidate prints its arguments.
   Read its evidence; if one cause runs through it, write the rule a
   sidekick or verifier could have followed to avoid it, one sentence, and
   stage it: `kitchen.py --repo <root> lessons add <args> "<rule>"`. If the
   evidence shares no cause, dismiss it, `kitchen.py --repo <root> lessons
   dismiss <args> --note "<why>"`; it stays hidden until newer trouble of
   its kind comes back; rerunning retro keeps the time of rows it already
   wrote, so a rerun alone does not bring it back. A staged lesson is `proposed`; the human makes it
   `active` (`lessons set <id> active`). The lessons file is this run's
   change in the shared worktree: commit it on its own, as `lessons add`
   prints, so it lands with the run and no sidekick commit sweeps it up.
   Other runs see it once it lands.

   A lesson marked `(promotable)` held in 3 units with no hit. Draft its
   proposal, `kitchen.py --repo <root> lessons promote <id> --out
   <store>/promotions/<id>.md`, and fill what it leaves to you: the exact
   check, an example hunk from the evidence (before and after), the check
   failing on the evidence commit and passing on HEAD, and its hits over
   recent history. Ask the human with the file, one lesson at a time; never
   promote without a yes. On yes the check is a brief like any other (a
   kitchen.toml change through pstack-kitchen-setup's maintain path), and
   once it lands, `lessons set <id> promoted --note "<commit> <check>"`. A
   lesson hit twice while active is wrong or too weak: rewrite it with
   `lessons set <id> active --rule "<rule>"`, or retire it with a note.
   For a class already
   repeated and plain enough to enforce now, propose the encoding it names
   to the human, or run the `correct` skill on it; a kitchen change is a
   brief like any other, through pstack-kitchen-setup's maintain path. Reply as the pair master does (the shape of `ketchup`), with
   the retro's summary line and the land-check result, and ask the human once
   whether to close the run's panes, in the turn land-check passes, with the retro and the feedback: a finished run's sidekick, consultant
   and verifier hold about a gigabyte of memory for as long as they stay
   open. On yes, `kitchen.sh close <store>` ends them and closes their
   panes; your own pane stays. A role started again after an earlier close
   (`spawn`, `consultant`, a rotation) is live again, so the next `close` ends it;
   a Claude that asks what to do with its background work is answered with
   "Exit and stop tasks".

   Gaps in the kitchen itself (a command, message, default or document of
   pstack-kitchen that cost the run time or a wake) are not repo classes.
   Write them from [the feedback template](feedback-template.md) and file
   them: `kitchen.sh feedback <store> <file>`. It prints where the report
   landed and whether a maintainer session or the human was told. Repo
   classes stay with pstack-kitchen-setup's maintain path.

## Rotate yourself

Your session is the run's largest cost. Every model call re-reads all of
it, and on this host a master that served several runs re-read 500-900k
tokens per call, 27-31 calls per unit, which was 55-65% of the run's
dollars. The fix is a fresh session at a unit boundary, with a handoff in
place of the history:

- One master session per run. Never carry a session into the next run;
  start a new one, without the human's MCP servers (an MCP-free master's
  first call is about 52k tokens; one with them, about 117k), and with
  Remote Control on so the human can follow it from claude.ai or a phone:
  `claude --strict-mcp-config --mcp-config ~/.agents/skills/pstack-pair/references/no-mcp.json --remote-control <slug>-master`.
- From the `KITCHEN_MASTER_ROTATE_UNITS`-th (5) clean verdict on (a gates-mode unit and a landing verdict count too),
  every clean verdict's `verify` prints `master: N units verified since your last handoff`. At
  that boundary, with no dispatch you must answer this turn. A clean landing
  verdict is a boundary on its own, whatever the unit count: `verify --landing`
  says to rotate when a brief is queued or a unit is still out, and to
  rotate only if more work follows when nothing is; every `dispatch` and
  `discuss` repeats the reminder until `rotate-master` runs. All of it is
  advice; nothing refuses:
  `kitchen.sh new-handoff <store>` writes `session-handoff.md` from
  [the template](handoff-template.md) with every unit's state filled in.
  Fill Goal, Decisions and Next, correct Units, then
  `kitchen.sh rotate-master <store>`. It starts the next generation in a
  pane beside yours (MCP-free, Remote Control on, your permission mode and
  model), points `pair.json` at it, and prompts it with the handoff. End
  your turn with no further commands, and act on no later prompt or task
  notification: the new master owns the run and closes your pane. If the
  next master does not start, `rotate-master` leaves its pane open (a
  folder-trust dialog closes it, and the refusal says to trust the folder in
  claude, rerun `rotate-master` and read the start-failed log), writes `sessions/master-g<n>-start-failed.log`
  with the launch command, herdr's error and the pane's screen, and changes
  nothing else; you stay the master and rotate at a later boundary.
- `rotate-master` records the session it closes under `pair.json`
  `.master.sessions`, so a human can `claude --resume <id>` it.
  `KITCHEN_MASTER_REMOTE_CONTROL=0` starts the next master without Remote
  Control.

On `pstack-kitchen MASTER <store>`: you are that next generation. Run
`kitchen.sh take-master <store>` first: it waits briefly for the old master
to end its turn, closes its pane (and the background waits it left
running), and gives you back the run's master name. Then read
`session-handoff.md`, `standing-orders.md` and `pair.json` (the dispatch
and verifying fields), then `kitchen.sh status` (its `queued:` line shows
`<store>/queue`), then the files the handoff points at, and
continue from its Next section; run `wait` or `verify --wait` on open
dispatches and verifications. Do not re-run `init` or `spawn`; the roles
are live.

## Delegation

The shared rules are in [the skill](../SKILL.md#delegation). Your children read
and advise; scope, review and the human stay with you.

What you read stays in your context for the rest of the session, and the
reads that fill it are diffs and files for review: `git show`, `git diff`,
`sed -n`, `cat`, `Read`, a few thousand tokens each, hundreds per run. Read
directly only what you act on in this turn (a verdict's Findings, a
report's review-delta, a digest). For everything else, delegate the read to
a subagent of your harness (Claude Code: the Agent tool, Explore type) with
a bounded question and a bounded answer: "read range A..B for unit NNN
against its Acceptance; report findings as file:line and one sentence each,
at most ten; say `none` otherwise". Only the answer enters your context; the
subagent's reading is discarded with it. The same holds for an audit read
(`audit: yes`), a landing review and a consultant's prototype.

## Briefs for a cheaper sidekick

The sidekick is the largest pool and often not the strongest model. A
precise brief costs you a few hundred tokens and saves it a wrong turn:
name the function to change and the one to leave alone, the test file to
add to, the existing pattern to copy (by path), and what done looks like as
a command's output. Put prior verdicts and findings in Context by path.

When a brief names several cases of one rule (a decision, a cap, a retry), it
asks for one parameterised table over those cases, not one test per case, and
names only the few behaviour-level tests that cannot share it.

## Reports and bookkeeping

`finish` refuses a done report without its `head:`, so a malformed report is
fixed in the sidekick's own turn, not by a brief. When a report must still
change after the fact, a brief whose Scope lists only store files is
bookkeeping: `classify` keeps it routine and gates-only, and it escalates
nothing. Prefer `--covers` over a brief that rewrites a report to say a later
unit met its acceptance.

Work superseded elsewhere (rebuilt on another branch, dropped in a rebase)
needs no command of its own. `kitchen.sh stop <store>` empties the queue even
when the sidekick is gone, a review note with `verdict: reject` saying what
superseded the unit keeps it out of land-check, and `pair.json` `.dispatch`
is replaced by the next dispatch; until then it only labels wake rows and the
handoff's `dispatched:` line. Log the decision with `kitchen.sh log`.

## Escalation mid-unit

A unit classified routine can turn out to need a design decision. When a
digest or a steer objection shows that, stop the unit (`kitchen.sh stop`),
start the consultant, and take it through a plan round before the next
brief. Record why with `kitchen.sh log`.
