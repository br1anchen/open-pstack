# Sidekick

You own the working tree. Follow [the pair sidekick](../../pstack-pair/references/sidekick.md)
for bootstrap, plan responses, briefs, steers, notes, pausing, and stop,
reading `pstack-kitchen` for `pstack-pair` in every message and
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh` for `pair.sh`. These
differ:

- **`--resolves` takes another unit's note too.** `kitchen.sh step ... --resolves
  n2` closes this unit's note 2; `001-n2` closes unit 001's note 2 from a step
  on a later unit, so a fix carried by the next unit still closes the note.
- **Steps go through the gates.** After each step's commit, run
  `kitchen.sh step <store> <sha> "<summary>"` with the commit you just made:
  the sha must be HEAD and the tracked files clean (an untracked file does not
  count), or it refuses before any gate. A sha the unit already recorded prints
  `step <short>: already recorded` and only merges `--resolves`; it runs and
  writes nothing else. It runs the fast gates of the
  profiles your commits touched and the repo's policy check, and records the
  step only when they pass. On a failure it prints each failing command's log
  tail: fix it in a new commit and run `step` again with that commit. When it
  says two failures in a row, stop: write the report with status `blocked`,
  the logs under Questions, and what you tried, then `kitchen.sh finish`.
  The checks run detached, and `step` returns within about two minutes; when
  it prints `gates: still running`, run the same command again, at once, to
  keep waiting. Never move `step` to your harness's background and wait for a
  notification: an idle turn there is a stalled kitchen.
- **Policy rules are the repo's.** A forbid finding names what to use
  instead; use it. Opting a line out (`kitchen-allow: <id>` with the reason)
  is for a case the rule did not foresee, and the master reads every one.
  A test fixture that must contain text a rule forbids (a checker's own
  tests) carries `kitchen-allow: <id>` and the reason on that line; the brief
  for such work says so up front.
- **A routine brief is the plan.** Its Steps name the files and functions to
  change. If one cannot be done as written, do not redesign it: report
  `blocked` with the conflict and the option you would pick.
- **A brief's Lessons are known trouble here.** The kitchen appends them at
  dispatch from earlier runs in the same area. Follow each as you would a
  standing order; the verifier checks them, and one you cannot follow is a
  Question, not a silent skip.
- **Before done,** run the review skills the standing orders name (from the
  kitchen's `review.self`) on your diff, and fix what they find in a commit
  of its own.
- **`finish` reruns the brief's Verify commands.** For a done report it runs
  every command in the brief's Verify block exactly as written, each on its
  own from the repo root, at the report's `head:`. The checkout must be at
  that commit with nothing uncommitted or untracked. The report goes to the
  master only when every command exits 0. On a failure (exit 2) it prints
  each command's log tail: fix it in a new commit, record it with `step`,
  name it in `head:`, and run `finish` again. If they cannot pass, report
  `blocked` with the output. Exit 4 means they are still running, as with
  `step`: run the same `finish` again at once.
- **After a verifier's rejection,** the fix brief's Context names the
  verdict. Fix each finding with the check that shows it gone; the next
  verification repeats the same acceptance.
- **A `pstack-kitchen NOTE <path>` message is a blocking review note.** Read it
  between tool calls: fix each blocking item first, as a fixup commit recorded
  with `kitchen.sh step ... --resolves <n-ids>`, then go on. A Devin gets it as a `NOTE:` line from its next `kitchen.sh step`, `progress` or `notes`; a clear note is never sent.
- **`step` and `finish` are `scripts/kitchen_core/step.py`.** The gates and the Verify commands they start run in a detached worker of their own (`scripts/kitchen_core/worker.py`), so a
  command that returns at `KITCHEN_STEP_SLICE_S` ("still running", exit 4) leaves them going, and the same command run again picks them up. The logs are
  under `steps/jobs/` in the store: `out.N` for each run of a step's gates, `out` for the latest.
