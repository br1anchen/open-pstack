// Drives plugins/rex/agents/pi-program-status.ts the way pi would: argv[2] is a JSON list of [event, payload, ctx] steps, where ctx is
// {mode, idle} and idle (true, false or absent) becomes ctx.isIdle(). Prints {"handlers": [names], "out": [each stdout write]} as JSON.
// @ts-nocheck
import extension from "../../plugins/rex/agents/pi-program-status.ts";

const handlers = {};
const out = [];
const write = process.stdout.write.bind(process.stdout);
process.stdout.write = (chunk) => {
  out.push(String(chunk));
  return true;
};
extension({ on: (name, fn) => (handlers[name] = fn) });
for (const [name, payload, ctx] of JSON.parse(process.argv[2])) {
  const c = ctx && { mode: ctx.mode, ...("idle" in ctx ? { isIdle: () => ctx.idle } : {}) };
  handlers[name]?.(payload ?? {}, c);
}
process.stdout.write = write;
write(JSON.stringify({ handlers: Object.keys(handlers).sort(), out }) + "\n");
