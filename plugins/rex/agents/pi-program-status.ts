// pstack: pi reports its state to Rex with OSC 7501 (Program Status Protocol), which pi does not do itself.
// Installed into ~/.pi/agent/extensions/ by the pstack installer; it does nothing outside a Rex block or outside pi's TUI.
// @ts-nocheck

type State = "idle" | "working" | "blocked" | "done" | "error";

function report(state: State, kind?: string): void {
  const body = [`state=${state}`, "app=pi", ...(kind ? [`kind=${kind}`] : [])].join(":");
  process.stdout.write(`\x1b]7501;${body}\x07`);
}

export default function (pi) {
  if (!process.env.REX_BLOCK) {
    return;
  }
  let tui = false;
  let active = false;
  let prompts = 0;
  let failed = false;
  let last: string | undefined;

  function publish(force = false): void {
    const state: State = prompts > 0 ? "blocked" : active ? "working" : failed ? "error" : "idle";
    if (!tui || (!force && state === last)) {
      return;
    }
    last = state;
    report(state, state === "blocked" ? "question" : undefined);
  }

  pi.on("session_start", (_event, ctx) => {
    // A new session starts with no prompt open and nothing reported yet.
    prompts = 0;
    failed = false;
    last = undefined;
    tui = ctx?.mode === "tui";
    active = ctx?.isIdle?.() === false;
    publish(true);
  });
  pi.on("agent_start", () => {
    active = true;
    failed = false;
    publish();
  });
  // A run whose last assistant message stopped on an error (a provider failure) ends in error, not idle.
  pi.on("agent_end", (event) => {
    const replies = (event?.messages ?? []).filter((m) => m?.role === "assistant");
    failed = replies.at(-1)?.stopReason === "error";
  });
  pi.on("agent_settled", (_event, ctx) => {
    // Another extension may already have started the next run; without isIdle, settled means stopped.
    if (typeof ctx?.isIdle === "function" && ctx.isIdle() !== true) {
      return;
    }
    active = false;
    publish();
  });
  pi.on("ui_prompt_start", () => {
    prompts += 1;
    publish();
  });
  pi.on("ui_prompt_end", () => {
    prompts = Math.max(0, prompts - 1);
    publish();
  });
  pi.on("session_shutdown", () => {
    prompts = 0;
    last = undefined;
    if (tui) {
      process.stdout.write("\x1b]7501;state=clear\x07");
    }
  });
}
