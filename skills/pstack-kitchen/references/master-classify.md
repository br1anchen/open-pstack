# Master: what classify prints

A failed step's `gate-fail` event names the log of that run
(`steps/jobs/<unit>-<sha>/out.<n>`); each run keeps its own.

A `warn: Scope covers ...` line means the Scope reaches a generated file's
source but not its outputs: add them to may write before dispatch, or say
in the brief why not.

A `warn: Acceptance runs <command>, which Verify does not` line names a
backquoted command in Acceptance (one starting with git, python3, test,
grep, awk, uv, bash or rg, with `./`, or with the first word of a touched
profile's gate) that neither Verify nor a touched profile's fast or
behavioral gate runs (Verify runs it when it appears there whole, between
command boundaries: `test -f a` is not run by `test -f ab`), so `finish` would not rerun it: add it to Verify, a
"prints nothing" check as `test -z "$(...)"`, or say in the brief why not;
`queue` prints it too.

Classify also runs the brief's read-only Verify lines once at the current
tree: a pipeline of `rg`, `grep`, `wc`, `head`, `tail` or `cut`
stages (or `git grep`, `git ls-files`), `test -e|-f|-d|-s <path>`,
and `test -z|-n "$(...)"` around such a pipeline, each with an optional
leading `!`. A `|` is the only operator a probe may hold: anything with `;`,
`&&`, `||`, a redirection, a backtick, another first word, or an option that
writes or starts a program (`--pre`, `--output`, `-O`) is never run. Each
prints `probe: passes now: <command>` or `probe: fails now: <command>`; a
probe that wrote to stderr or exited 2 or more prints `warn: Verify probe
<command> errored (exit N): <stderr line>`, and an rg glob such as `!bin/**`
that is matched from the search root prints `warn: Verify glob ...` (write
`!**/bin/**` to exclude at any depth). A "prints nothing" check should fail
now when the unit is meant to remove the text, and pass now when it guards
against a regression. Probes never stop classify, dispatch or queue.
