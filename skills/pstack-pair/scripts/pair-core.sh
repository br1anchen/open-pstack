# Shared store and Herdr channel core for pstack-pair, pstack-pair-guided, and
# pstack-trio. Each skill's scripts/pair.sh sets the variables below, sources
# this file, defines usage and any command it adds or overrides, then calls
# pair_main "$@". A command is any function named cmd_<name>, with dashes in
# the name as underscores. Every command is idempotent and prints what it did.
# A ported command is a pycore shim into pair.py (the stdlib-only Python core in
# pstack_core/); a variant overrides it by redefining cmd_<name> after sourcing
# this file, as it does any other command.
#
# Set by the caller before sourcing:
#   skill_root    the calling skill's directory (templates live under it)
#   PAIR_SKILL    skill name; prefixes every prompt ("$PAIR_SKILL BRIEF <path>")
#   state_root    default parent directory of stores
#   store_label   "Pair" or "Trio", for the bootstrap prompt and status title
#   roles         agents the master spawns, in pane order: (sidekick [consultant])
#   store_dirs    directories init creates under the store
#   slug_max      longest slug tail, so <slug>-<longest role> fits Herdr's 32 chars
#   unfilled_re   optional ERE for header lines that still list alternatives

: "${unfilled_re:=}"

die() {
	printf '%s: %s\n' "${PAIR_PROG:-pair.sh}" "$1" >&2
	exit "${2:-1}"
}

need() {
	command -v "$1" >/dev/null 2>&1 || die "$1 is required on PATH"
}

need herdr
need python3

pair_core_dir="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=prime-agent.sh
. "$pair_core_dir/prime-agent.sh"

# Run a command in the Python core. The variant's globals travel as argv, and
# the pid of this shell as PAIR_SHELL_PID ($$ is the main shell inside $( ) too)
# and its option flags as PAIR_SHELL_FLAGS ($- holds e where errexit is on: in a
# direct call and in ( ... ) &, not inside $( ), which clears it; it also holds e
# where errexit is only suspended, as in `f || x` and `if f`, so a caller that
# runs in such a context reads as errexit on).
# With --exec first, Python replaces the shell, so a signal sent to the command's
# process reaches the Python code (wait uses it: it must stop when it is stopped).
pycore() {
	local run=()
	[ "${1:-}" != --exec ] || { run=(exec); shift; }
	PAIR_SHELL_PID=$$ PAIR_SHELL_FLAGS=$- ${run[@]+"${run[@]}"} python3 -I "$pair_core_dir/pair.py" --skill-root "$skill_root" "$@"
}

pair_file() {
	local store="$1"
	[ -f "$store/pair.json" ] || die "no pair.json in $store; run: pair.sh init <slug>"
	printf '%s\n' "$store/pair.json"
}

header_field() {
	# $1 file, $2 key: the value of a "key: value" header line, or empty.
	grep -m1 -E "^$2:" "$1" | sed -E "s/^$2:[[:space:]]*//" || true
}

cmd_pause() { pycore pause "$@"; }

cmd_resume() { pycore resume "$@"; }

# Draft-PR review. The sidekick commits each step of a brief and records it
# with pair.sh step, then goes on without waiting. A wait returns each step
# the master has not reviewed; the master reviews that diff and publishes a
# note: blocking items the sidekick resolves at its next step boundary, and
# follow-ups that go to followups.md instead of widening the unit. The final
# review then reads only the diff since the last note.

# The oracle for pstack_core.providers.PROVIDER_ERROR_RE (tests/test_pstack_core.py compares the two); no script reads it.
provider_error_re='capacity issues|no api providers|no api key found|unauthorized|forbidden|rate.?limit|too many requests|overloaded|fetch failed|econnreset|econnrefused|etimedout|socket hang up|usage limit reached|wait for usage to reset|missing meta credentials|billing verification failed|billing_error'

cmd_init() { pycore init "$@"; }

cmd_permission() { pycore permission "$@"; }

cmd_failover() { pycore --exec failover "$@"; }

cmd_rotate() { pycore --exec rotate "$@"; }

cmd_spawn() { pycore --exec spawn "$@"; }

cmd_new_plan() { pycore new-plan "$@"; }

cmd_new_brief() { pycore new-brief "$@"; }

cmd_discuss() { pycore --exec discuss "$@"; }

cmd_dispatch() { pycore --exec dispatch "$@"; }

cmd_wait() { pycore --exec wait "$@"; }

# The master's next brief, held for the sidekick to take the moment its
# current report is written. One slot: more than one queued brief is a plan.
cmd_queue() { pycore queue "$@"; }

# The sidekick records a committed step and goes straight on; the master's
# wait picks it up for review. --resolves names the notes the step fixes.
cmd_step() { pycore step "$@"; }

# The master's review of a unit's steps: a draft note covering the steps since
# the last note, through the last step a wait showed (or --through k), so a
# step that lands while the master reviews is left for the next note.
# Published with pair.sh note once filled.
cmd_new_note() { pycore new-note "$@"; }

# Publishes a filled note: the sidekick sees it at its next step boundary,
# and its follow-ups go to followups.md rather than into the unit.
cmd_note() { pycore note "$@"; }

# The sidekick's check at each step boundary: blocking notes still open on
# the running brief, printed in full so they can be fixed without another read.
cmd_notes() { pycore notes "$@"; }

# The sidekick's pickup after writing a report: prints the queued brief's
# message to act on. Exit 4 when the queue is empty, so the turn ends.
cmd_next() { pycore next "$@"; }

# The one command a sidekick runs after writing any report: tells the master,
# then says what to do next. Devin ends the turn for master-owned rotation;
# other kinds may take a queued brief after a done report.
cmd_finish() { pycore finish "$@"; }

cmd_report() { pycore report "$@"; }

cmd_notify() { pycore notify "$@"; }

cmd_stop() { pycore --exec stop "$@"; }

cmd_progress() { pycore progress "$@"; }

# Fresh steers on a unit are capped at two; a steer that supersedes an objected
# one is a discussion round and does not count.
cmd_new_steer() { pycore new-steer "$@"; }

# A steer is the one message that may reach a working sidekick. It lands in
# the agent's input queue, handed over between tool calls; the ack is a
# progress line, so there is nothing to wait for. A sidekick paused on an
# objection is idle, so a steer that answers it is sent with a wait instead.
cmd_steer() { pycore --exec steer "$@"; }

cmd_status() { pycore status "$@"; }

cmd_log() { pycore log "$@"; }

# A throwaway worktree beside the shared tree. Default: detached at HEAD with
# the sidekick's uncommitted diff applied, so a prototype or build starts from
# the live state. With --at SHA: exactly that commit, so the master can rerun
# a unit's checks, or the consultant read it, while the sidekick keeps working.
# Removed when done; status lists the open ones.
cmd_scratch() { pycore scratch "$@"; }

# Where a run's time went, from events.tsv. A sidekick is busy from a brief,
# plan, answer, or objection steer until its report for that unit, a new
# send, or now if it is still running; idle from a report to the next send.
# The same holds for the consultant between a plan or consult and its advice.
# Review latency runs from a brief's report to the master's next review log
# row. PAIR_METRICS_NOW fixes "now", for a view as of an earlier time.
cmd_metrics() { pycore metrics "$@"; }

pair_main() {
	[ $# -ge 1 ] || { usage; exit 1; }
	local cmd="$1" fn
	shift
	case "$cmd" in
	help | -h | --help) usage; return 0 ;;
	esac
	fn="cmd_${cmd//-/_}"
	if [[ "$cmd" =~ ^[a-z][a-z-]*$ ]] && declare -F "$fn" >/dev/null; then
		"$fn" "$@"
	else
		usage
		die "unknown command: $cmd"
	fi
}
