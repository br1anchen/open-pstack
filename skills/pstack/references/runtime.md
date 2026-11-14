# Runtime adapter

Read this once when entering any pstack workflow, and again after compaction if
its rules are no longer in context. These mappings replace the Cursor-specific
operations in the imported workflows, references, agent prompts, and scripts.
The host's actual tool schemas, permissions, and explicit user instructions take
precedence. A skill never grants permission to publish, message others, delete
work, bypass a sandbox, or use a model forbidden by the host.

## Resolve the runtime first

Identify the active agent from its session context, not from which executables
happen to be installed. Use `claude`, `codex`, `pi`, `grok`, `amp`, or `generic`
as the runtime key. Read `~/.config/pstack/models/<runtime>.md` if present. Substitute
that key for the literal `<runtime>` in workflow paths. Missing roles inherit
the parent model. Prime Agent uses `generic` and its exposed native tools;
Pi extensions and their APIs do not apply. Read the budget comment and
per-role reasoning annotations when present. Pass a reasoning override only through a supported host field,
within its allowed values and permissions; a budget never changes inherited
parent settings. Treat every upstream model slug as a role suggestion, never
as evidence that the model is available.

Skills live together under `~/.agents/skills` in this installation. In a portable
copy, use the directory containing the sibling skill directories. Resolve the
Markdown links relative to the file containing them. Load a named skill through
native discovery when available, otherwise read its SKILL.md directly. This
also applies when a workflow names a slash command. `tdd` and `teach` references
within pstack mean `pstack-tdd` and `pstack-teach`.

## Tools and delegation

| Operation in upstream prose | Claude Code | Codex | Pi | Amp | Grok Build / generic |
| --- | --- | --- | --- | --- | --- |
| Read, edit, search, shell | Read, Edit/Write, Grep/Glob, Bash | Exposed shell/exec and apply_patch tools | read, edit/write, bash | The file and shell tools Amp exposes for the selected model, such as edit_file, create_file and shell_command | Use the exposed file and shell tools |
| Task / spawn a worker | Agent, or Task on older sessions | spawn_agent when exposed | subagent extension only when installed and exposed | Task; create_thread for a separately owned task, another machine, or another model | Native subagent tool when exposed |
| Resume / drain workers | Agent resume and task-output tools available in session | followup_task or resume equivalent; wait_agent, messages, status tools as exposed | Extension's documented lifecycle | Task with the worker's `id` continues it; `mode: async` results arrive as a message; subagent_stop; for threads, send_thread_message, wait_for_threads, read_thread | Runtime's documented lifecycle |
| AskQuestion | AskUserQuestion when exposed | request_user_input_async or request_user_input when allowed in the active mode | Extension UI when exposed | ask_user_choice, within the limits its description sets | Available structured question tool |
| TodoWrite / todolist | TodoWrite or task tools | update_plan when exposed | Extension plan tool | No plan tool: a Markdown checklist, or an artifact document for a plan the user reviews | Native plan tool |

When a question or plan tool is absent, use a concise question in chat or a
Markdown checklist. Never invent tool calls or pass Cursor fields to a foreign
schema. Tools such as `readonly`, `run_in_background`, `environment`,
`cloud_base_branch`, and `subagent_type` are concepts to map, not universal
parameters. Read-only reviewers use the host's least-privilege tools; do not
request write access merely to obtain MCP reads.

Dispatch `poteto-agent` by passing [its prompt](agents/poteto-agent.md) and the
skill location to a native general worker. Dispatch Comment Sicko with
[its prompt](agents/comment-sicko.md). These are prompt templates, not registered
agent type IDs. Pass this adapter to every worker. New work normally gets a
fresh worker with the original brief, later directives, and the prior report
and branch. Reuse a worker only when the work needs costly-to-transfer live
state, such as its uncommitted checkout or a running process. Stop and hold
messages still reach the running worker. Respect the host concurrency and
nesting limits.

Use distinct confirmed models for panels when the host permits selection.
`inherit-parent` and `auto` omit the model override. If only one model is
available, use separate role prompts and report the reduced diversity. If no
subagent tool exists, run the roles sequentially, keep separate findings, and
label the result a single-agent review. Independent verification remains
unfulfilled where the playbook requires it. Do not describe sequential work as
parallel or independently reviewed. Do not launch other agent CLIs to bypass
the host's delegation restrictions.

## Amp

- Skills: Amp discovers the linked skills in `~/.agents/skills` and loads one
  with its `skill` tool. It also reads `~/.claude/skills`, but the first skill
  with a name wins, so the second link set adds no duplicates. A user invokes a
  skill by naming it in the prompt, as in "use the pstack skill to ...". Amp has
  no slash command for skills. `reload_skills` or a new thread picks up a sync;
  `amp skills list` shows where each skill came from.
- Models: Task subagents run the thread's model and reasoning effort and take no
  model override. When the `amp` models file names a model for a role, run
  that role as a thread through create_thread with its `model` (and `effort`
  where the model supports it); list_agent_modes lists the choices. Otherwise
  use separate Task role prompts and report the reduced diversity. Plugin
  agents such as `@amp/oracle` run the model set in their plugin file.
- Threads: create_thread starts a separate thread, by default in a new orb with
  its own checkout of the project's default branch, or on a named runner. It
  does not see this thread's uncommitted work: send files with
  upload_thread_file, pull them back with download_thread_changes, and ask the
  thread to report with send_thread_message to this thread's ID. This is the
  documented remote execution service the cloud agent mapping below refers to.
- Control: `control-cli` runs commands with shell_command and drives a TUI in a
  tmux session. `control-ui` uses agent-browser (load `using-agent-browser`) for
  web UI and the Mac satellite (mac_sidecar, mac_shell) for Xcode and the iOS
  Simulator. Inspect captures with view_media.
- Persistent state: an idle orb can be deleted, and the next message restores
  only the thread's git changes and `.amp/in/orb-backup/`. In an orb, keep a
  task store that must survive under `.amp/in/orb-backup/pstack/` instead of
  `XDG_STATE_HOME`; a local CLI session keeps the usual path. Artifacts hold
  reports and plans people read and comment on.
- Automation: a recurring audit or `/loop 1h` becomes the thread's schedule
  (load `building-schedules`; one schedule per thread). Event wake-ups use Amp's
  GitHub, GitLab and Linear subscriptions, found with tool_search. A webhook is a
  plugin's `amp.createWebhook`, which works only in orb threads (load
  `creating-webhooks`). Secrets go in Amp's project or personal secrets, never
  in the thread.
- Amp can also be a role the pair, trio and kitchen start in a pane
  (`kind = "amp"`); read [the Amp adapter](../../pstack-pair/references/amp-sidekick.md).
- MCP servers and deferred Amp tools are reached through tool_search and
  code_exec. Painter generates images; diagrams and charts go in a visual
  artifact (load `creating-artifacts`).

## Editor and service features

- Cloud agents become local workers in separate git worktrees unless the host
  exposes a documented remote execution service. One writer owns each worktree.
  Cloud durability, depth-three nesting, and hundreds of workers are not assumed.
  Inspect native task status; transcript mtime is not a liveness signal. After
  restart, verify worker status before reattaching or restarting from a brief.
- Cursor's agent store becomes a task-specific directory under
  `${XDG_STATE_HOME:-$HOME/.local/state}/pstack/<runtime>/`. Store its path in the
  task checklist and pass it explicitly to `orch`. Inspect `orch --help` for
  arguments. A durable store preserves evidence, not a live agent process.
- `/goal` means use a native goal tool only if the user explicitly requested a
  standing goal and the tool exists. Otherwise record the objective and stop
  condition in the run's standing orders. `/loop` means continue the authorized
  work within the session. Upstream `/loop 1h` means an hourly audit using an
  available scheduler or wake mechanism, subject to the same authorization.
  For recurrence, use a configured scheduler only when
  the user requested scheduling; document cadence, stop condition, and ownership.
  Do not promise work will continue after the host ends the session.
- Sticky `mode`, `icon`, `color`, and `reminder` frontmatter is removed. Keep the
  selected workflow in the task checklist; re-read it after compaction. There is
  no automatic SessionStart hook in this installation.
- `control-cli` means run the real CLI or drive the TUI with an exposed terminal
  controller. `control-ui` means the available browser/app automation. Read its
  schema first. When no controller exists, provide a concrete manual check and
  mark UI verification pending. Screenshots or source inspection alone do not
  prove an interaction works.
- `create-skill` means the installed skill-authoring guidance, or standard
  SKILL.md with name, description, steps, and linked references. Project skills
  go in `.agents/skills` for Codex/Pi/Amp, `.claude/skills` for Claude, or
  `.grok/skills` for Grok unless the repository already defines a shared location.
  On Amp, the built-in `building-skills` skill is the authoring guidance.
- `deslop` means review the actual diff for redundant code, unnecessary guards,
  and irrelevant changes, then apply the no-comments and unslop workflows.
  `cursor-team-kit` is not a dependency. Use repository checks in place of IDE
  diagnostics. Use actual available image tools for visual explanations; when
  absent, write Mermaid or an SVG instead of claiming image generation.
- Discover MCP tools from the host's tool list or discovery API. A Cursor `mcps/`
  directory is not required. Missing integrations are reported as unavailable.

## Transcript scope

Resolve the active workspace and session before reading history. Claude normally
stores JSONL under `~/.claude/projects`; Codex under `$CODEX_HOME/sessions`, or
`~/.codex/sessions`; Pi under `~/.pi/agent/sessions`. Discover the actual session
file from host metadata or workspace fields before opening message bodies.
For Grok, prefer its session tooling or an explicit export; do not assume a
Cursor-compatible schema or filesystem path. Amp keeps threads on its server,
not in local files: the current thread's URL is in the session environment;
read another thread with read_thread, find one with find_thread (through
tool_search), or export one with `amp threads markdown T-...` or
`amp threads export T-...`. On generic agents, use the current
conversation or an explicitly supplied transcript.

All mentions of `~/.cursor/projects`, `agent-transcripts`, Cursor project slugs,
and a transcript directory supposedly present in the system prompt refer to
this scoped discovery step. Never scan unrelated message bodies to find the
project. If history cannot be identified safely, use the supplied conversation,
git state, and task artifacts, and state the missing history.

## Bundled executable helpers

The source may be read-only in the Nix store. Run Bun helpers through
`pstack/scripts/run-helper.sh <relative-script> [arguments...]`, relative to the
shared skill root. For example:

```bash
~/.agents/skills/pstack/scripts/run-helper.sh orch/orch.ts --help
~/.agents/skills/pstack/scripts/run-helper.sh watch-pr/watch-pr --help
```

The wrapper copies the helper tree to a writable, content-addressed cache before
Bun installs its locked dependencies. It preserves the caller's working
directory. This replaces every direct `bun ...scripts/...` instruction. Bun,
Git, gh, and any repository-specific CLI must already be available; inspect a
helper's help before using it. Network and install permission still apply.

`worktree-audit.sh` takes an optional `PSTACK_TRANSCRIPTS_DIR` pointing at an
already scoped transcript directory. With no transcript evidence its cleanup
verdict stays conservative. The audit never authorizes deleting a worktree.

## Automation and secrets

Benny's pack is at `pstack/references/benny`. Its standalone setup skill links
there. Cursor's `/automate`, Automations editor, webhook `update_state`,
`SendToUser` secret cards, and `[routine]` event format have no portable API.
For Benny, use an explicitly configured event runner or scheduler and the
bundled triage/repro prompts. For make-bot-ui, use an existing documented webhook
service. Complete local configuration and validation first; external activation
requires the user's authorization and actual credentials/integrations.
Store secret values in host environment or a secret manager. Without a service,
prepare the local UI/handler and report webhook delivery as pending.
