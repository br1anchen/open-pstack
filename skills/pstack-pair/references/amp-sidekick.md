# Amp as a pstack role

The Amp CLI (`amp`) runs as a Herdr `amp` kind: Herdr starts it with
`herdr agent start --kind amp` and reads its idle, working and blocked state
from the screen. Under Rex, pstack goes by the state Amp reports to its block,
or by its screen when it reports none. Any role can use it: set `kind = "amp"` in a roster entry, or pass
`--kind amp` to `pair.sh spawn`, with Amp-native arguments such as
`--model anthropic/claude-opus-5-5 --effort high` (or `--mode <mode>`).
`amp login` signs in; each role session is an Amp thread in that account.

What pstack does for it:

- **Permissions.** None are passed. Amp runs every tool without asking, so
  every mode but `plan` needs no flag. Amp has no read-only flag, so a `plan`
  role is recorded as `untranslated` with a warning; give it read-only
  instructions in its brief.
- **Workspace trust.** None is needed: the first launch in a repo opens no
  trust dialog.
- **No MCP opt-out.** Amp loads the human's configured MCP servers; its
  `--mcp-config` adds servers and cannot remove them.
- **Preflight.** A sidekick, and a kitchen consultant, first runs one
  `amp <model flags> -x "Reply with exactly: OK"` in `/tmp`, passing on
  `--model`, `-m`, `--mode`, `--effort`, `--features`, `--settings-file` and
  `--fast`. Amp archives the thread it makes. When Amp cannot answer (an error
  such as `Unknown model provider`, an account that cannot run a turn, or no
  answer within `PAIR_PREFLIGHT_TIMEOUT`, 90 s), the roster fallback
  (`[sidekick.fallback]`, `[consultant.fallback]`, or `--fallback`) starts
  instead.
- **Exit.** Two Ctrl+C presses, which quit Amp even mid-turn. Amp takes a typed
  `/exit` as a message to its model, so pstack never sends one.
- **Steering.** A steer sent while Amp works lands after its current tool
  call. Esc does not stop a running tool call, so `steer --interrupt` cannot
  cut one short.

Like every kind, Amp starts a fresh session per completed brief: rotation exits it with two Ctrl+C presses and
respawns it with its recorded arguments, which start a new thread.
