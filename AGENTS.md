# Repository Guidelines

## Layout

- `skills/<name>/` holds one skill each: `SKILL.md`, `references/`, and `scripts/`. `sources.json` lists every skill and where it came from (upstream `cursor/plugins` at a pinned revision, or `local customization`).
- `pstack-pair/scripts/pair-core.sh` is the script core of the multi-agent workflows; `pstack-pair`, `pstack-pair-guided`, `pstack-trio` and `pstack-kitchen` each source it from their own entry script (`pair.sh`, or `kitchen.sh` for the kitchen). The consultant's commands live in `pstack-trio/scripts/consultant-core.sh`. `pstack-kitchen/scripts/kitchen.py` reads a repo's `.agents/kitchen.toml` as data.
- `pstack-pair/scripts/pair.py` and `pstack_core/` are the stdlib-only Python core; a ported command runs in it through the `pycore` shim in `pair-core.sh`, and the bash dispatch and the variant overrides stay as they were. `tests/test_pstack_core.py` is the parity suite: it runs the bash helpers and `pair.sh` as it was at d06a932 against the Python core on shared fixtures. Two commits are the parity bases: d06a932 for phase 1, and e6e83e3 for phase 2, whose `base_skills()` extracts `skills/` as it was there to run as the oracle for the wait loop, finish, notify, report, next, queue, step, dispatch and discuss.
- `scripts/pstack-sync.py` installs the skills as per-skill links for each agent runtime; `scripts/check-pstack.py` checks the inventory, frontmatter and links.
- `tests/` drives the scripts against a fake `herdr` (`tests/pstack-pair/bin`) and compares golden transcripts (`tests/pstack-pair/golden`).

## Style

- Bash scripts stay `bash -n` clean and keep JSON work in `jq`; Python is stdlib-only, Python 3.11 or later.
- Skill text: say what holds, in plain sentences; link with relative paths that `check-pstack.py` can resolve.
- Name commits with Conventional Commits and a scope naming the skill or area (`fix(skills/pstack-kitchen): ...`, `test(tests): ...`). Keep commits atomic.

## Testing Guidelines

- `python3 scripts/check-pstack.py` and `python3 tests/run.py` must pass before a commit. `tests/run.py` runs one process per test in parallel; `-k <text>` selects tests by id; `PSTACK_PAIR_UPDATE=1` regenerates the pair goldens, and a golden changes only when the transcript is meant to change.
- Tests are deterministic: no sleeps and no elapsed-time asserts. Trigger a state change at a fixed point with the fake herdr hooks (`$FAKE/once/<subcommand>`, `$FAKE/report-after`) and assert on output.
- Tests are hermetic: they scrub inherited `PSTACK_KITCHEN_*`, `KITCHEN_*` and `PAIR_*` variables, so a run inside a live kitchen gives the same result as one outside it.
- Drive pair.sh and kitchen.sh by hand only against the fake herdr: put `tests/pstack-pair/bin` first on PATH and point XDG_STATE_HOME at a temp directory, as `tests/pstack-pair/scenario.sh` does. `init`, `spawn`, `stop` and `close` against the real herdr register, rename or close the calling pane, so an agent that runs them there loses its own pane.

## Security

- No secrets in the repo; agents read tokens from the host environment.
- Host-specific settings (the agent roster in `~/.config/pstack/kitchen.toml`, model preferences in `~/.config/pstack/models/`) stay on the host.
