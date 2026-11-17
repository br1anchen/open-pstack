# Amp orb environment

Amp orbs are the Debian 12 sandboxes Amp agents work in
([docs](https://ampcode.com/docs/orbs)). Every new thread of the
`br1anchen/open-pstack` project starts in an orb that is ready to run the
`pstack-kitchen` workflow on this repo.

## What setup provides

`.agents/setup` runs once per fresh orb, and Amp snapshots the result. It:

- runs `uv sync --frozen`, so the pinned ruff and ty that the `pstack`
  profile's fast gates run are in `.venv`;
- builds joo (`joo-dev`, the review engine `.agents/kitchen.toml` names) from
  the private `br1anchen/judge_of_owls` into `~/.local/bin`, installs the
  Claude CLI, and writes `~/.joo/config.json` with `claude:opus` unless one
  exists. Reviews run on the owner's Claude subscription token, the personal
  Amp secret `CLAUDE_CODE_OAUTH_TOKEN`; without it, or without read access to
  that repository, the step skips, and a failed fetch, build or CLI install
  only warns, so check `command -v joo-dev` before relying on it;
- links this checkout's own `skills/*` into `~/.agents/skills` (and
  `~/.claude/skills`, `~/.grok/skills`) with `scripts/pstack-sync.py --apply`,
  so `~/.agents/skills/pstack-kitchen/scripts/kitchen.sh` resolves to the
  working tree. Nothing is cloned: an edit to the checkout is what the next
  `kitchen.sh` command runs. Setup writes no `builds.tsv` row, because the
  kitchen commits to that tree as it runs and no one revision is the build a
  run used; `kitchen.sh fleet` and feedback show the build as `unknown`.

`.agents/resume` runs the same sync on every activation and wake, which is a
no-op unless a snapshot came without the links.

Three Amp project environment variables (Settings, Secrets & Env Vars)
configure the kitchen for orbs, because agent tools run `bash -c` and never
read `~/.bash_profile`:

- `PSTACK_MUX=subagents`
- `XDG_STATE_HOME=/home/user/workspace/repo/.amp/in/orb-backup/pstack`, so
  the run store, ledger and subagent inbox survive orb deletion; Amp's
  `.git/info/exclude` ignores `.amp/in/`, so the store never dirties the tree
  that `kitchen.sh finish` checks
- `PSTACK_SCRATCH_ROOT=/home/user/.local/state/pstack/scratch`, which keeps
  verifier worktrees outside the checkout, as pstack requires

The test suite scrubs `PSTACK_MUX` and `PSTACK_SCRATCH_ROOT` and gives each
test its own `XDG_STATE_HOME`, so these settings do not change its results.

`AGENTS.md` (Kitchen on Amp orbs) covers subagent mode and master rotation
across threads.

## Checks

In a fresh orb, these confirm the kitchen is ready:

```bash
~/.agents/skills/pstack-kitchen/scripts/kitchen.sh help
python3 skills/pstack-kitchen/scripts/kitchen.py validate
python3 skills/pstack-kitchen/scripts/kitchen.py doctor
```
