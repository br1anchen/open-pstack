---
name: pstack-kitchen
description: "Run coding work as a kitchen in one Herdr or Rex session: a master that plans, briefs, triages code review, and decides; a sidekick on the cheapest capable model that implements under the repo's own deterministic gates; a consultant started only when a unit escalates; and a fresh verifier pane that proves each unit on the real surface. The master is woken by exceptions, not by every step. Needs the repo's .agents/kitchen.toml (pstack-kitchen-setup writes it). Use for /pstack-kitchen, 'kitchen mode', 'run the kitchen', a `pstack-kitchen PLAN|BRIEF|STEER|CONSULT|VERIFY|REPORT|ADVICE|FEEDBACK|MASTER|STOP` message, 'be the sidekick/consultant/verifier' in a kitchen, or sustained delegated work that should not need the master on every step. Requires Herdr (HERDR_ENV=1) or Rex (REX_BLOCK), or runs its roles as Amp threads on models of their own (PSTACK_MUX=amp), or as subagents of one session."
---

Read [the runtime adapter](../pstack/references/runtime.md) before following this workflow. Its runtime mappings apply to all referenced playbooks and scripts.

# Pstack kitchen

pstack-pair with the master's attention moved from dishes to the kitchen.
The repo states, in `.agents/kitchen.toml`, which of its own commands prove
which part of it and what makes a change risky; `kitchen.py` reads that as
data. Every committed step passes those gates before it counts, a fresh
verifier proves each finished unit, and Judge of Owls sweeps its diff. The
master plans, writes precise briefs, triages the review, and decides; it is
woken by exceptions, not by routine steps. What keeps going wrong becomes a
rule in the kitchen, not another correction.

Everything [pstack-pair](../pstack-pair/SKILL.md) says about the store, the
messages, plans, check-ins, steers, the queue, notes, pausing, Devin, Pi, and Prime
sidekicks, and shared rules holds here, with `kitchen.sh` in place of
`pair.sh` and `pstack-kitchen` in every message. This file covers what
differs.

## Where the commands live

`scripts/kitchen.sh` is one exec line into `pstack-pair/scripts/pair.py`; the kitchen's `scripts/variant.py` holds its startup hook, which records
which skills build a run's commands came from. Every command runs in Python, in `scripts/kitchen_core/` through that variant on top of the pair's
`pstack_core`: `step.py` holds `step` and `finish` (the checks they start run detached and outlive the command), `wait.py` the quiet wait, `dispatch.py`
`dispatch`, `queue` and `discuss`, `setup.py` `init`, `spawn`, `consultant` and `fleet`, and the other modules the rest (`classify`, `verify`, `land-check`,
`revise`, `catch`, `retro`, the master hand-over and the feedback inbox). `scripts/kitchen.py` reads the repo's kitchen and runs its gates;
`kitchen_core/kpy.py` is the only way the commands reach it.

[`references/playbook-roles.toml`](references/playbook-roles.toml) maps each part of the pstack playbooks a brief names to the roles that own it, with a reviewed digest per part. `kitchen.py playbooks validate` checks it against the playbook sources (`scripts/check-pstack.py` runs the same check), and `kitchen.py playbooks review <name>` lists the parts whose source changed since they were reviewed. `kitchen.py context` moves sections of a repo's Markdown into modules from a reviewed migration file; [the reference page](references/context-migration.md) has the sequence.

## Preconditions

1. Inside Herdr (`test "${HERDR_ENV:-}" = 1`, the Herdr skill loaded, `herdr`
   on PATH) or Rex (`REX_BLOCK` set, `rex skill show` read, `rex` on PATH; see
   [Running in Rex](../pstack-pair/references/rex.md)), and Python 3.11 or later on PATH. `scripts/kitchen.sh help` lists the commands.
   Inside neither, the kitchen runs in this one session with its roles as
   subagents: read [Without a multiplexer](#without-a-multiplexer). In an
   Amp thread, `PSTACK_MUX=amp` runs every role as an Amp thread on its own
   model instead: read [Amp threads](#amp-threads).
2. The repo has a kitchen (`.agents/kitchen.toml`, or a host kitchen; see
   [kitchen-toml.md](references/kitchen-toml.md)) and `kitchen.py validate`
   passes (`scripts/kitchen.py`). Without it, run pstack-kitchen-setup first; a
   kitchen without rules is a pair with extra steps.
3. The host roster, `~/.config/pstack/kitchen.toml`, names the agents (all
   optional; `--kind` overrides):

   ```toml
   [sidekick]
   kind = "pi"
   args = ["--model", "devin/swe-2", "--thinking", "high"]
   [sidekick.fallback]
   kind = "devin"
   [consultant]
   kind = "codex"
   [consultant.fallback] # optional: started when the consultant's preflight or start fails
   kind = "claude"
   args = ["--model", "claude-fable-5-1"]
   [verifier]            # routine units; default: the run's current sidekick
   kind = "pi"
   args = ["--model", "devin/swe-2", "--thinking", "high"]
   [verifier.fallback]   # a verifier stopped by its provider retries once here
   kind = "claude"
   args = ["--model", "claude-sonnet-5-5"]
   [verifier.escalated]  # escalated units and --landing; default: claude opus
   kind = "claude"
   args = ["--model", "claude-opus-5-5"]
   [machine]
   gate_slots = 1   # gates running at once across every kitchen on this host
   ```

   Gates waiting for a slot take it in arrival order, whichever kitchen
   they come from; a step still waiting prints its place (`gates: queued 2
   of 3 for 1 machine gate slot`, or `gates: queued 1 of 1 for 1 heavy slot,
   behind heavy gates from other kitchens`). A heavy gate (`heavy` in its profile) takes
   its heavy slot first and the machine gate slot second, so it never holds a
   gate slot while it waits for a build. `timings.tsv` records each gate's
   gate-slot wait in column 7, its heavy-slot wait in column 8 and its wall
   seconds, from holding its slots to its end, in column 9.

   Without `[verifier]`, a routine unit is verified by the sidekick now
   implementing the run (its kind and arguments from `pair.json`, so after a
   failover it is the fallback), and an escalated unit or `verify --landing`
   by `claude --model claude-opus-5-5` with no fallback. `kind = "master"`
   in either entry means the master's own kind.

   Verifiers and consults work in scratch worktrees under
   `${XDG_STATE_HOME:-~/.local/state}/pstack/scratch/` (`PSTACK_SCRATCH_ROOT`
   moves it), outside the repo. Claude and Codex ask to trust a folder they
   have not seen, so trust that root once per host: open each agent there and
   accept. `kitchen.py doctor` warns while a roster kind does not trust it.

   In a jj workspace with no `.git` of its own (`jj workspace add`, not
   colocated), `init` links a git worktree of the backing repository in place
   at the working copy's parent, so git and the gates work there; running
   `init` again fills a `git_root` that is empty or gone.

   Every gate takes one of the machine's slots, so parallel kitchens queue
   their test suites instead of overloading the machine. The default is one
   slot per eight cores.

To use Muse Code, set `kind = "muse"` in any roster entry with Muse-native
`args` (for example `["--model", "muse-wonder-full"]`); read
[the Muse adapter](../pstack-pair/references/muse-sidekick.md).

To use Amp, set `kind = "amp"` in any roster entry with Amp-native `args`
(for example `["--model", "anthropic/claude-opus-5-5"]`); read
[the Amp adapter](../pstack-pair/references/amp-sidekick.md).

To use Prime Agent, set `kind = "prime-agent"` in `[sidekick]`,
`[consultant]`, or a verifier entry, with Prime-native `args`. Read
[the Prime adapter](../pstack-pair/references/prime-sidekick.md). Each verifier
stops its exact daemon session before its scratch worktree is removed; failed
cleanup preserves both the pane and scratch for inspection.

## Without a multiplexer

When neither Herdr nor Rex is found (or `PSTACK_MUX=subagents`), the kitchen
runs in the master's own session, as plain pstack does, and every other role
is a subagent the master starts with its host's subagent tool. The commands
and the store are the same; what changes is who carries each message:

- A command that would prompt a role prints one line,
  `subagent NAME (KIND): TEXT`, and keeps it in
  `${XDG_STATE_HOME:-~/.local/state}/pstack/subagents/inbox/NAME.txt`. Start a
  subagent with TEXT as its whole prompt. It loads this skill, takes the role
  TEXT names, runs the role's `kitchen.sh` commands itself, and writes its
  files. KIND is the roster's wish; the subagent runs on the host's own model.
- Nothing waits. `spawn`, `dispatch`, `discuss`, `consult` and `verify` hand
  off their prompt and return (exit 4 while the answer is not on file); run
  the subagent, then the next command (`wait`, `verify --wait`), which reads
  what it wrote. A `master: TEXT` line is a notice for you, already handled.
- A rotation or failover ends the role; its next prompt goes to a fresh
  subagent. The master is this session itself, so `rotate-master` starts
  no new master: after `new-handoff` it records the hand-over and writes the
  next generation's prompt to `sessions/master-g<n>-prompt.md`. End this
  session; the human opens a new one in the same checkout, outside any
  multiplexer, and gives it that prompt; its `take-master` moves the run
  to that session. `prime-agent` cannot run here (it needs a
  multiplexer that tracks its session).
- Gates, Verify reruns, `land-check`, `classify`, the ledger and `retro` run
  as they do anywhere.

## Amp threads

With `PSTACK_MUX=amp`, run from an Amp thread (`AMP_THREAD_ID` set), every
role is an Amp thread of its own, in an orb of its own, on the model its
roster entry names. This is how a kitchen on Amp gets model diversity, since
Amp's Task subagents run the master's model and take no override:

```toml
# Subscription-first: each chain lists the Claude subscription model, then the
# ChatGPT subscription model, then a model billed in Amp credits.
[master]
kind = "amp"
args = ["--model", "anthropic/claude-opus-5-5"]
[[master.fallback]]
kind = "amp"
args = ["--model", "openai/gpt-6-astra"]
[[master.fallback]]
kind = "amp"
args = ["--model", "google-vertex/gemini-4-carbon"]

[sidekick]
kind = "amp"
args = ["--model", "anthropic/claude-sonnet-5-5"]
[[sidekick.fallback]]
kind = "amp"
args = ["--model", "openai/gpt-6.1-sol"]
[[sidekick.fallback]]
kind = "amp"
args = ["--model", "zhipuai/glm-5.3"]

[consultant]          # OpenAI first, for a view the other roles lack
kind = "amp"
args = ["--model", "openai/gpt-6-astra", "--effort", "high"]
[[consultant.fallback]]
kind = "amp"
args = ["--model", "anthropic/claude-fable-5-2"]
[[consultant.fallback]]
kind = "amp"
args = ["--model", "google-vertex/gemini-4-carbon"]

[verifier]
kind = "amp"
args = ["--model", "anthropic/claude-sonnet-5-5", "--orb-size", "a1.small"]
[[verifier.fallback]]
kind = "amp"
args = ["--model", "openai/gpt-6.1-sol", "--orb-size", "a1.small"]
[[verifier.fallback]]
kind = "amp"
args = ["--model", "google-vertex/gemini-4-hydrogen", "--orb-size", "a1.small"]

[verifier.escalated]
kind = "amp"
args = ["--model", "anthropic/claude-opus-5-5", "--orb-size", "a1.large"]
[[verifier.escalated.fallback]]
kind = "amp"
args = ["--model", "openai/gpt-6-astra", "--orb-size", "a1.large"]
[[verifier.escalated.fallback]]
kind = "amp"
args = ["--model", "google-vertex/gemini-4-carbon", "--orb-size", "a1.large"]
```

- A connected Claude subscription serves `anthropic/*` models and a ChatGPT
  subscription serves `openai/*`; every other provider bills Amp credits.
  Order each chain Claude subscription, ChatGPT subscription, then an
  Amp-billed model, and put a role on an OpenAI model first only where it is
  assigned there (the consultant, for diversity). `amp config model-providers
  check-access --provider-model <model>` says which a model is:
  `modelRouting.connectionType` is `model_provider_claude_sub` or
  `model_provider_openai_chatgpt` for a subscription, and `modelRouting.kind`
  is `upstream-model-provider` for an Amp-billed model (it answers `ok: false`
  with no detail for `zhipuai/*` models; the thread's usage page shows the
  Amp billing).
- A role steps down its chain when its model hits a usage limit or a routing
  error. The step is automatic when a start's preflight fails, and also for a
  verifier its provider stops. Mid-unit the master makes it with
  `kitchen.sh failover <store> --reason <why>`. Each step logs a `failover`
  event. The sidekick returns to the top of its chain at the next rotation
  (`failback: the sidekick returns to <label>`, a `failback` event); a
  verifier and a consultant start from the top each time, and so does the next
  master. A single `[sidekick.fallback]` table still works.
- A roster may replace the sidekick's `[sidekick]` `kind` and `args` with two
  or more `[[sidekick.alternate]]` tables, each with its own `kind` and
  `args` (`[sidekick.fallback]` still applies), to compare models across runs:

  ```toml
  [[sidekick.alternate]]
  kind = "amp"
  args = ["--model", "zhipuai/glm-5.3"]
  [[sidekick.alternate]]
  kind = "amp"
  args = ["--model", "anthropic/claude-sonnet-5-5"]
  ```

  `init` picks the entry the fewest runs on this host recorded (ties break by
  a hash of the run name, so fresh orbs pick at random), prints `sidekick:
  alternate picks <label> (entry i of n)` and records it in `pair.json`
  `.sidekick_choice`. `spawn` with no `--kind` starts that entry, so a
  rotation or a respawn keeps the model. Setting `[sidekick]` `kind` and
  alternates together is an error.
- A role's thread starts with the first prompt it is sent (`amp -ox` with the
  entry's `--model`, `-m`, `--mode`, `--effort`, `--features`, `--fast` and
  `--orb-size`; without `--orb-size` the orb takes the project's default
  size, so name `a1.small` for a docs-only role and a larger size for one
  whose gates build and test);
  later prompts continue it (`amp threads continue <T> -ox`). A role of any
  other kind runs on Amp's default mode, with a warning. Each prompt prints
  `amp NAME (THREAD): TEXT`.
- Every orb has its own checkout and its own copy of the store. Each prompt
  attaches a bundle: the store's files, a git bundle of HEAD and of each
  scratch worktree the store links (less what the remote holds), and the
  agents this side knows. A prompt to a role also attaches the master's
  skills build and the line that installs it under
  `~/.local/share/pstack-builds/`, so every orb runs the run's build. The
  receiving thread's first move is
  `PSTACK_MUX=amp ~/.agents/skills/pstack-pair/scripts/pair.sh sync <URL>`,
  which the prompt names. It merges the store three-way against the bundle
  it last sent that thread (a file only one side changed keeps that side's
  copy; one both changed takes the sender's and is named; `pair.json` merges
  key by key; `.tsv` files take the union of their lines), fetches the
  commits, moves a clean checkout to the sender's HEAD when that loses no
  commit, and adds the scratch worktrees. Files over 1 MiB, locks and pid
  files stay home.
- A role gets its setup once. Its first message (the bootstrap at spawn or
  rotation) carries the build, the line that installs it and the full rules for
  sending files back to the master; a later message to the same thread carries
  the store bundle, the sync line and a one-line send-back rule, and attaches
  the build again only when the master's build changed. A role whose thread
  compacted since the last message gets that message headed by `You compacted
  since the last message. Re-read ~/.agents/skills/<skill>/SKILL.md (your
  role: <role>), the store <store> (standing-orders.md, pair.json), and the
  file this message names before you act.` and the full send-back rules again.
  A role on `--mode claude-code` may not load Amp skills, so its bootstrap says
  `Read the file ~/.agents/skills/<skill>/SKILL.md` where others say `Load the
  <skill> skill`. A kept sidekick session also reads a brief's `## Playbook:`
  and `## Report` sections once: when the session has received them already
  and they are unchanged, a later brief holds `Unchanged from briefs/<NNN-slug>.md,
  which this session already read; reread it there.` in their place, and a new
  generation (a spawn, a rotation or a failover), a changed composition or a
  compaction since gives them in full again (`.sidekick.delivered` in
  `pair.json` records what was delivered, to which generation).
- Nothing waits. `spawn`, `dispatch`, `discuss`, `consult` and `verify` send
  and return (exit 4 while the answer is not on file); end your turn. A
  role's `finish` or `notify` messages the master's thread with its own
  bundle, and the master runs that sync, then the next command.
- A role's `finish` and `notify` always send the file as a message to the
  master's thread, whatever the master is doing: the message queues behind its
  turn, carries the store bundle with the file, and quotes the file's header
  (status, brief, head, units) and a digest of its content, so a file rewritten
  under the same path is sent again and an unchanged one is not sent twice. The
  master reads it when its turn ends, after the sync the message names. Before
  `wait` or `verify --wait` declares a missing report, a deadline or a stall, it
  looks in the master's thread for that role's message; one found prints
  `pending sync: <role> sent <file> at <time>; run pair.sh sync from that
  message` and nothing is rewritten.
- A role thread the store no longer uses is archived by the master, as soon as
  that is safe, and each archive is logged (`decisions.tsv` phase `archive`, an
  `archive` event) with its thread id: a replaced sidekick or consultant once
  the new one's READY is in the store, a verifier once `verify --wait` has
  recorded its verdict, the old master once `take-master` has run, and every
  remaining role at `close`. A thread is kept (`kept <thread>: <reason>`) while
  it is in a turn, a message or steer relay to it is pending, it sent the master
  a file the master's store does not hold yet, its report's `head:` is not in the
  master's checkout, or its replacement has not sent READY. `kitchen.sh threads
  <store>` lists every thread the store named with its role, state and `in use`,
  `archivable` or `kept: <reason>`; `threads <store> --archive` archives the
  archivable ones. Run it once on a store from an older build.
  Whichever command retires a thread is covered: every write to the thread
  registry that drops or replaces a role's thread (a pane reused by a rotation,
  a verifier slot reused, a synced bundle, a closed pane) records the old thread
  as retired for its store, and every thread the kitchen starts is recorded for
  its store when it starts. A thread the master started outside the kitchen (a
  `create_thread` helper) is recorded with `threads <store> --register <T-id>
  [--role helper]`; it is archived by the same rule as a role, never while in a
  turn. `threads <store> --discover` records the unarchived threads that name
  the run (by its path or its `<slug>-` title) on a store started on an older
  build; add `--archive` to archive what is safe. Every command that sweeps ends
  its output with one line, `archive: <a> archived, <k> kept, <u> in use`.
- A message sent by the CLI reaches a role's thread only after its current
  turn ends: `amp threads continue -ox` queues it, and the CLI has no way to
  steer a running turn. So a role whose thread is in a turn reads `working`
  (its `lastKnownAgentState` from `amp threads export`): `dispatch` and a plan
  round refuse it instead of queueing behind it. A `steer` for it (with
  `--interrupt` too, since there is no Esc) is a relay, because the master's
  own `send_thread_message` does reach a busy thread between tool calls.
  `steer` packs the bundle under the master's `.git`, writes the message and
  records the relay as `relay-pending`, then prints four lines: `relay-pending:
  <id> for <name> (<thread>)`, `relay: upload_thread_file path=<bundle>
  destination=<upload_to> thread=<thread>`, `relay: send_thread_message to
  <thread> with the text of <message file>` and `relay: then run kitchen.sh
  relay-done <store> <id>`. The master finishes the relay with
  three calls: `upload_thread_file({thread, path, destination, overwrite:
  true})` from the amp module through code_exec, with the three values the
  line names; `send_thread_message` to the thread with the text of the
  message file; `kitchen.sh relay-done <store> <id>`, which marks it sent
  and logs a `relay` event (and leaves the sync base alone when a CLI send to
  the thread was made after the relay). The same steer again while it is pending prints
  the same lines with `(already pending; not made again)` and makes no second
  record, and `status` lists each relay the master still owes as
  `relay-pending: <id> for <name> since <hh:mm>`. A pending relay whose thread
  has ended a turn since is stale: `status` drops it and the same steer is
  relayed anew or sent by the CLI. Once done, the same steer is not relayed
  again until the thread has ended a turn, and prints `relayed: <id> for
  <name> (<thread>) (already relayed; ...)` instead. A bundle over
  `upload_thread_file`'s 4 MiB, or a checkout with no `.git`, falls back to
  `queued ...`: the CLI delivers it after the turn. A message to an idle role
  still goes by the CLI.
  Every message ends with a `pstack-msg: <id>` line. The same text sent again
  to a thread that has not ended a turn since is not delivered twice (`not
  sent again: the same message is still pending there`), and a send that
  fails with `Attachment upload did not complete` is retried
  (`PSTACK_AMP_SEND_TRIES`, 3, `PSTACK_AMP_RETRY_S` seconds apart, 10) unless
  the thread already holds its id.
- A new role thread takes two to three minutes before it works: the orb
  starts, runs the project's setup, installs the build, syncs and builds
  joo. The rotation policy spends that on fresh context only where it buys
  something:
  - The sidekick keeps its thread across consecutive routine units in the
    same profiles: `dispatch` prints `kept: the amp sidekick keeps its
    session for this routine unit (<profiles>, context N%)`. It rotates when
    the next unit is escalated, its profiles differ, or the thread has used
    `PAIR_REUSE_CONTEXT_PCT` (50) percent of its context window or compacted
    (`amp threads context`). A rotation or failover archives the role's
    thread; its next prompt starts a fresh one.
  - The verifier stays fresh: each verification starts a new verifier
    thread, which sends its verdict back with `kitchen.sh notify`; run
    `verify --wait` after its sync. Keep it for escalated units and
    landings, and give routine profiles `verify = "batch"` or `"gates"` in
    `.agents/kitchen.toml`, so a routine unit starts no verifier thread of
    its own.
  - The consultant starts at the first escalation and lives the run, as
    anywhere.
  - The master rotates when its thread has used more than
    `KITCHEN_MASTER_CONTEXT_PCT` (60) percent of its context window or has
    compacted, and the kitchen enforces it: `dispatch`, `discuss` and `queue`
    refuse with exit 9 and the rotation commands, log one `rotate-due` event
    per master generation, and `verify` and `finish` print the same line;
    `KITCHEN_MASTER_CONTEXT_GATE=warn` prints without refusing, and
    `rotate-master` and `take-master` clear it. `rotate-master`
    starts the next master as a new thread, on `--model` or this thread's
    own mode, with the bundle and the master prompt; its `take-master` takes
    the run over there. No human step is needed. A role's reply that still
    reaches the old master's thread names a sync that forwards it to the new
    one.

  The cost to optimize is latency, not money: an idle orb pauses after five
  minutes and costs nothing while paused, and a start costs about a cent at
  a1.medium. Never skip joo on a role orb to start faster; the review step
  needs it.
- `prime-agent` cannot run here.

## Roles and quota

Each role draws on a different quota; the kitchen spends each where it is
worth most.

| | Master | Sidekick | Consultant | Verifier |
| --- | --- | --- | --- | --- |
| Agent | the strongest model | the largest, cheapest pool | a different strong model, the scarcest | the host roster's `[verifier]`: the sidekick's kind (routine) or Claude Opus (escalated) |
| Lives | the whole run | the whole run, rotated or failed over | from the first escalation | one verification, then its pane closes |
| Owns | plans, briefs, review triage, decisions, the human | the working tree, steps, fixes | design critique | the verdict |
| Writes | the store | the brief's Scope | advice, scratch | its verdict, in a scratch worktree |

Enter your role. Master: [references/master.md](references/master.md). (read it in a read of its own; with other files in one shell call it overflows the tool's output and comes back as a preview)
Sidekick: [references/sidekick.md](references/sidekick.md).
Consultant: [references/consultant.md](references/consultant.md).
Verifier: [references/verifier.md](references/verifier.md).

## Delegation

Every role may hand bounded tasks to subagents when its host has a subagent
tool. Delegation is available, never required. A role's reference says what
its children may do beyond these shared rules.

- Each task names the role, the unit head or scratch path, the scope, the
  exact question, an output bound, a deadline, and the write and command
  limits.
- No child writes the store, runs a state-changing kitchen command (any
  `kitchen.sh` command other than `status`, `notes`, `playbook-part` and
  reads of the store), talks to the human or another role, publishes, or
  delegates further.
- The parent collects or stops its children before it reports, times out,
  rotates or removes scratch.
- A child runs on the role's own roster model and effort when the host can
  select them, otherwise on the host's parent setting; say which in the
  report or advice.
- Never start another agent CLI to get around the host.

## A unit's path

1. **Classify.** The brief's may-write Scope maps to profiles and a risk
   class (`kitchen.sh classify`; dispatch does it too). `routine` runs on the
   brief alone. `escalated` (a contract, a migration, a dependency, a
   cross-profile change, files no gate covers) starts the consultant and needs
   a plan round with it.
2. **Steps.** The sidekick commits each step and records it with
   `kitchen.sh step`, which runs the touched profiles' fast gates and the
   policy check and records only a passing step. The master does not read a
   routine unit's steps. At done, `kitchen.sh finish` reruns the brief's
   Verify commands exactly as written at the report's head, and the report
   reaches the master only when every one exits 0. Each passing command's
   line shows how many lines it printed and the last one, so a test run's
   summary, or its absence, is in view; output that opens with a usage line
   (help, which exits 0 too) gets a warning.
3. **Verify.** `kitchen.sh verify` per the unit's verify mode: `gates` (the
   step gates were the proof), `batch` (one verifier for several routine
   units), or `unit`. The verifier proves the brief's Acceptance at the
   unit's head and writes a verdict with evidence; a fix is verified with
   the unit it fixes (`--covers`). `verify` returns at an interval (`--every MIN`) while the
   verifier works, so the master is never held in one long call;
   `--until-verdict` instead returns once, on the verdict, for a caller that
   runs it in the background.
4. **Review.** The master reviews the unit's range with whatever the repo's
   owner uses: its own read, the agent's built-in review, or an external
   tool it runs itself. The kitchen runs no reviewer and reads no tool's
   output, but when `[review].engine` names one, the owner requires it: the
   master runs it on every unit and its review note cites the run; blocking findings become notes or a fix brief, follow-ups go to
   `followups.md`. A unit answers to the engine classify stamped on its brief
   (`review-engine:`), so an engine named mid-run starts with the next dispatch.
5. **Accept.** The master's review note is short when the verdict is clean;
   a sampled unit (`audit: yes`) gets a full read.
6. **Land.** `kitchen.sh land-check` passes only when every unit with an
   accepted review note is verified; `kitchen.sh verify --landing`
   proves the stack tip with a fresh verifier, and its clean verdict covers
   the last units that were not verified on their own (review them against it,
   then run land-check). land-check covers a unit rebased into the stack by
   patch-id, or rewritten (a squash) when a clean landing verdict's head has
   the unit head's tree; it says how many commits behind HEAD an older landing
   verdict is, says `landed (rebased)` or `landed (squashed)` when trunk
   carries a landing verdict's commits under other shas, and warns when dependency manifests changed after its verified base;
   `verify --landing --parent REF` records where the stack starts and
   `--head REV` proves a branch other than HEAD (in a jj workspace either may
   be a jj revision, such as `main@origin`, that names one commit). The revert check that
   leaves a rebased unit uncovered compares patches without context, so a later
   commit that happens to delete an identical line elsewhere also reads as a
   revert: a known limitation that refuses (never covers) a unit, and
   `verify --landing` covers it. A unit that only deletes (no added line, new file, rename or mode change) may
   cite `engine: skipped (deletion-only, proof: <command>)` when
   `[review].engine` is set. The landing brief goes as far
   as `[landing].mode` allows and never merges.
7. **Learn.** `kitchen.sh retro` shows where the master was needed and what
   repeated; each repeated class becomes a rule, a lint, a profile command, or
   a standing order. A gap in the kitchen itself goes to `kitchen.sh feedback`.
   Trouble of one kind in one area across runs becomes a lesson:
   - **Propose:** retro lists the strongest candidates from the ledger,
     grouped by what failed (gate check, policy rule, steer kind); the master
     writes the rule and stages it (`kitchen.py lessons add`) or dismisses a
     candidate with no one cause; the human activates it.
   - **Apply:** dispatch adds the active lessons for a brief's area to its
     Lessons section; the verifier proves the verification ones as Acceptance
     and checks the rest after its own pass, marking each held or hit. Gates
     score gate lessons; the verifier never sees them.
   - **Score:** retro counts held and hit per unit in the host ledger.
   - **Promote:** after 3 held and no hit, `kitchen.py lessons promote` drafts
     the proposal (the check, which layer and why, an example, proof, false
     positives); the human decides each one, and the check lands through a
     brief. A lesson hit in a quarter of its units (two hits before 8 units) is
     rewritten or retired, a gate lesson's hits call for an autofix in the step,
     and `doctor` flags both and any duplicate rule.

## Feedback

`kitchen.sh feedback <store> <file>` files the master's gaps (from [the
template](references/feedback-template.md)) into the host inbox,
`${XDG_STATE_HOME:-$HOME/.local/state}/pstack/feedback/`, behind a header it
writes. A maintainer session registered with `kitchen.sh maintainer on` gets
`pstack-kitchen FEEDBACK <path>` and works as [references/maintainer.md](references/maintainer.md)
says; with none live, the human gets a notification. `feedback --inbox` lists
open reports and `feedback --close` retires one.

## When the master wakes

A report (done, partial, blocked), a check-in digest at the interval, a
steer objection, the steps of an escalated unit whose brief says `steps: master`, a rejected or inconclusive
verdict, review findings, a sampled audit, an approval dialog, and a
provider error. Two failed gates in a row reach the master as a blocked
report. Everything else the kitchen handles. Each `wait`, `dispatch` and
`verify` return to the master logs one `master wake` row in `events.tsv`: a
check-in that `wait` or `dispatch` swallows is not a wake, and a `verify`
return is (`verify:progress`, `verify:grace`, `verify:<status>`). A `discuss`
answered by both roles logs one; `discuss` with no answer yet (a deferred
sidekick) and `verify` with nothing open log none.

## Store additions

The pair's store under `${XDG_STATE_HOME:-$HOME/.local/state}/pstack/kitchen/runs/<slug>/`, plus:

| Path | Writer | Content |
| --- | --- | --- |
| `gates.tsv` | `kitchen.sh step` | one row per step check: time, unit, commit, pass or fail, profiles |
| `verdicts/NNN-<slug>-v<k>.md` | verifier, or `verify` for gates mode | the verdict, from [the verdict template](references/verdict-template.md) |
| `verdicts/NNN-<slug>-v<k>-packet.md` | `kitchen.sh verify` | what the verifier was given |
| `pair.json` `.escalations` | `kitchen.sh consultant` | why the consultant was started, one entry per start; an escalated unit is its brief's `risk:` line |
| `session-handoff.md` | master via `kitchen.sh new-handoff` | the running master's handoff to its next generation, from [the handoff template](references/handoff-template.md) |
| `sessions/master-<g>-handoff.md`, `pair.json` `.master.sessions` | `kitchen.sh rotate-master` | each closed master generation: its handoff and resumable session id |
| `pair.json` `.master.previous` | `kitchen.sh rotate-master`, cleared by `take-master` | the old master during a hand-over, whose pane the new master closes |
| `pair.json` `.sidekick_choice` | `kitchen.sh init` | the `[[sidekick.alternate]]` entry this run starts, with its label |
| `pair.json` `.dispatch.sidekick`, `.sidekick_models` | `kitchen.sh dispatch` | each brief's sidekick as `<kind> <model>`; `.sidekick_models` keeps it per unit |
| `pair.json` `.sidekick_threads` | `kitchen.sh dispatch`, under Amp threads | the threads each sidekick model ran in |

Each store's `pair.json` `.skill_builds` lists the skills directories its commands ran from, stamped
at `init`; a run from before stamps has none and counts as `unknown`. `pstack/builds.tsv`, which an install
step may append to at each switch, maps a skills directory to the revision that built it. `kitchen.sh fleet` reads both,
and every store, without writing: a row per run and a row per build (`mixed` runs, which ran on more
than one build, and `unknown` ones stay in their own rows). `retro` takes its verified-unit count from
the same function: the union of unit ids over clean, non-landing verdicts.

`fleet` and `retro` also print a by-sidekick-model table from `.sidekick_models`: per model, the units, the
master wakes while that model's unit was the newest dispatch, failed step gates, steers plus review-fix
briefs, verifier rejections with the units verified on their first verdict, blocking review notes, and the
median minutes from a unit's first dispatch to the master's first review accept. `fleet` adds a runs column.
Under Amp threads `retro` adds one `sidekick cost <label>: $X over N threads` line per model, read from
`amp threads usage`; a run from before `.sidekick_models` gives no rows.

Beside the runs, on the host: `pstack/feedback/inbox/<date>-<repo>-<run>[-k].md` (open reports),
`feedback/done/` (closed ones, with a Closed block) and `feedback/maintainer.json` (the registered
maintainer), all written by `kitchen.sh feedback` and `maintainer`.

The repo's ledger, across runs, is `ledger.tsv` in `kitchen.py statedir`; `timings.tsv` beside it
records each gate's run time (column 6), its slot waits and its wall seconds (columns 7 to 9; rows from older builds stop at 7), which `kitchen.py timing` reads.
Each ledger row names the unit, its area (the directories its brief may write, tests and docs aside), the finding and its match.
`kitchen.py ledger-backfill` gives rows from older builds the same, from the run stores still on disk.
`kitchen.py lessons` reads it into candidate lessons and lists the staged ones in `.agents/LESSONS.md`
(beside the host file for a host kitchen); a lesson's held and hit counts stay in the ledger.
