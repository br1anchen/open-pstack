# Amp orb environment

Amp orbs are the Debian 12 sandboxes Amp agents work in
([docs](https://ampcode.com/docs/orbs)). Every new thread of the
`br1anchen/open-pstack` project starts in an orb that is ready to run the
`pstack-kitchen` workflow on this repo.

## What setup provides

`.agents/setup` runs once per fresh orb, and Amp snapshots the result. It:

- links this checkout's own `skills/*` into `~/.agents/skills` (and
  `~/.claude/skills`, `~/.grok/skills`) with `scripts/pstack-sync.py --apply`,
  so `~/.agents/skills/pstack-kitchen/scripts/kitchen.sh` resolves to the
  working tree. Nothing is cloned: an edit to the checkout is what the next
  `kitchen.sh` command runs. Setup writes no `builds.tsv` row, because the
  kitchen commits to that tree as it runs and no one revision is the build a
  run used; `kitchen.sh fleet` and feedback show the build as `unknown`;
- runs `uv sync --frozen`, so the pinned ruff and ty that the `pstack`
  profile's fast gates run are in `.venv`;
- installs the Claude CLI, writes `~/.joo/config.json` with `claude:opus`
  unless one exists, and puts a `joo-dev` stub in `~/.local/bin`. joo, the
  review engine `.agents/kitchen.toml` names, builds from the private
  `br1anchen/judge_of_owls` and reviews on the owner's Claude subscription
  token, the personal Amp secret `CLAUDE_CODE_OAUTH_TOKEN`. Amp runs project
  setup with no user identity, so setup can read neither; the build happens in
  the thread, after the snapshot, and no token or login reaches the snapshot.

`.agents/resume` runs on every activation and wake. It reruns the skill sync,
a no-op unless a snapshot came without the links, and runs
`.agents/joo-build start`: when the token is set, that starts a background
check, under a lock in `~/.cache`, which builds joo from the head of
`br1anchen/judge_of_owls` main when joo is not built or its build is behind
main. Wakes that overlap never build twice, and the build logs to
`~/.cache/joo-build.log`. Resume returns in well under a second.

`~/.local/bin/joo-dev` stays the stub: the build moves the real binary to
`~/.local/bin/joo-dev-built`, next to the skills and `joo-codebase-index` it
loads, and records its revision in `~/.local/bin/.joo-revision`. The stub
runs `.agents/joo-build wait` before every `joo-dev` call, so every review
runs the latest build of joo's main. `wait` waits for a running build, then
compares the recorded revision with main (`git ls-remote`, about a second):
when they match it returns, and otherwise it builds now. A cold build takes
about 2.5 minutes. When main cannot be read, the token is unset or the
rebuild fails, it warns and reviews with the previous build; with no build
at all it exits 1 and says why, and a failed build prints the log's last
lines. `kitchen.py doctor` finds the stub on PATH, so it does not prove joo
is built; `cat ~/.local/bin/.joo-revision` shows the revision it built.

Three Amp project environment variables (Settings, Secrets & Env Vars)
configure the kitchen for orbs, because agent tools run `bash -c` and never
read `~/.bash_profile`:

- `PSTACK_MUX=amp`, so the kitchen runs each role as an Amp thread on a
  model of its own; single-session subagent mode needs
  `PSTACK_MUX=subagents` on each command
- `XDG_STATE_HOME=/home/user/workspace/repo/.amp/in/orb-backup/pstack`, so
  the run store, ledger and subagent inbox survive orb deletion; Amp's
  `.git/info/exclude` ignores `.amp/in/`, so the store never dirties the tree
  that `kitchen.sh finish` checks
- `PSTACK_SCRATCH_ROOT=/home/user/.local/state/pstack/scratch`, which keeps
  verifier worktrees outside the checkout, as pstack requires

The test suite scrubs `PSTACK_MUX` and `PSTACK_SCRATCH_ROOT` and gives each
test its own `XDG_STATE_HOME`, so these settings do not change its results.

`AGENTS.md` (Kitchen on Amp orbs) covers both modes. A role's orb needs
none of this setup's links: each prompt carries the master's skills build and
installs it under `~/.local/share/pstack-builds/`, and the master's own
rotation starts the next master as a thread.

## Checks

In a fresh orb, these confirm the kitchen is ready:

```bash
~/.agents/skills/pstack-kitchen/scripts/kitchen.sh help
python3 skills/pstack-kitchen/scripts/kitchen.py validate
python3 skills/pstack-kitchen/scripts/kitchen.py doctor
```
