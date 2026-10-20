# Shared store and Herdr channel core for pstack-pair, pstack-pair-guided, and
# pstack-trio. Each skill's scripts/pair.sh sets the variables below, sources
# this file, defines usage and any command it adds or overrides, then calls
# pair_main "$@". A command is any function named cmd_<name>, with dashes in
# the name as underscores. Every command is idempotent and prints what it did.
# A ported command is a pycore shim into pair.py (the stdlib-only Python core in
# pstack_core/); a variant overrides it by redefining cmd_<name> after sourcing
# this file, as it does any other command.
# JSON from herdr is parsed with jq; identifiers are never guessed.
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
need jq
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
	PAIR_SHELL_PID=$$ PAIR_SHELL_FLAGS=$- ${run[@]+"${run[@]}"} python3 -I "$pair_core_dir/pair.py" --skill "$PAIR_SKILL" --prog "${PAIR_PROG:-pair.sh}" --skill-root "$skill_root" \
		--state-root "$state_root" --store-label "$store_label" --roles "${roles[*]}" \
		--store-dirs "${store_dirs[*]}" --slug-max "$slug_max" --unfilled-re "$unfilled_re" -- "$@"
}

in_herdr() {
	[ "${HERDR_ENV:-}" = 1 ] || die "not running inside Herdr (HERDR_ENV != 1)"
}

pair_file() {
	local store="$1"
	[ -f "$store/pair.json" ] || die "no pair.json in $store; run: pair.sh init <slug>"
	printf '%s\n' "$store/pair.json"
}

field() {
	jq -r "$2" "$(pair_file "$1")"
}

# pair.json has two writers once a brief is queued (the master, and the
# sidekick through next), so every write takes a short mkdir lock and replaces
# the file atomically. $1 store, rest: jq arguments ending in the filter.
json_update() {
	local store="$1" i=0 rc=0
	shift
	until mkdir "$store/.pair.lock" 2>/dev/null; do
		i=$((i + 1))
		[ "$i" -lt 100 ] || die "pair.json lock held for 10s; remove $store/.pair.lock if no pair.sh is running"
		sleep 0.1
	done
	jq "$@" "$store/pair.json" >"$store/pair.json.tmp" && mv "$store/pair.json.tmp" "$store/pair.json" || rc=$?
	rmdir "$store/.pair.lock"
	return "$rc"
}

# herdr's state detection can miss a Devin agent in a narrow pane for a whole
# run, reporting done while it works. Every agent kind shows "esc to
# interrupt" (Devin: "esc twice to interrupt") in its status line while it
# works, so a settled state is checked against the pane's visible screen,
# never scrollback, where an old frame could linger. Devin shows two more
# signs of a running turn: its input placeholder "Guide Devin while it works",
# and, while a message is queued, "send queued messages now" or "↵ send now".
# In an 11-row pane the queued block pushes the interrupt hint off screen, so
# each sign counts on its own. A narrow pane wraps them, so lines are joined.
pane_busy() {
	grep -qiE 'esc (twice )?to interrupt|guide devin while it works|send queued messages now|↵ send now|ctrl\+enter to send now' <<<"$(pane_text "$1")"
}

# The pane's visible screen as one line. Callers match it with a here-string:
# under pipefail, `cmd | grep -q` fails when grep exits early and cmd takes
# SIGPIPE, which turned matches into misses at random.
pane_text() {
	herdr agent read "$1" --source visible --lines 40 2>/dev/null | tr -s '\n\t ' ' ' || true
}

agent_status() {
	# Prints idle|working|blocked|done|unknown, or "absent" when herdr has no such agent.
	local out state
	if out="$(herdr agent get "$1" 2>/dev/null)"; then
		state="$(printf '%s\n' "$out" | jq -r '.result.agent.agent_status // "unknown"')"
		case "$state" in
		idle | done) pane_busy "$1" && state=working ;;
		esac
		printf '%s\n' "$state"
	else
		printf 'absent\n'
	fi
}

agent_kind() {
	# Prints the Herdr agent kind hosting $1 (a name or pane id), or empty.
	herdr agent get "$1" 2>/dev/null | jq -r '.result.agent.agent // empty' || true
}

latest_report() {
	local store="$1" want="${2:-}" f
	if [ -n "$want" ]; then
		f="$(ls -t "$store"/reports/"$want"-*.md 2>/dev/null | head -1 || true)"
	else
		f="$(ls -t "$store"/reports/[0-9][0-9][0-9]-*.md 2>/dev/null | head -1 || true)"
	fi
	[ -n "$f" ] && printf '%s\n' "$f"
}

latest_advice() {
	# $1 store, $2 optional NNN: the newest advice file, by mtime.
	local store="$1" want="${2:-}" f
	if [ -n "$want" ]; then
		f="$(ls -t "$store"/advice/"$want"-*.md 2>/dev/null | head -1 || true)"
	else
		f="$(ls -t "$store"/advice/[0-9][0-9][0-9]-*.md 2>/dev/null | head -1 || true)"
	fi
	[ -n "$f" ] && printf '%s\n' "$f"
}

# Herdr's state detection can lag or miss a turn on a narrow pane, so a
# settled agent with no file yet is polled for the file, not trusted. $1
# path, $2 timeout in ms. Returns 0 when the file appears. Polls every
# PAIR_FILE_POLL_MS (default 5000); tests set it short.
await_file() {
	local path="$1" timeout_ms="$2" waited=0 poll_ms="${PAIR_FILE_POLL_MS:-5000}"
	while [ ! -f "$path" ] && [ "$waited" -lt "$timeout_ms" ]; do
		sleep "$((poll_ms / 1000)).$(printf '%03d' $((poll_ms % 1000)))"
		waited=$((waited + poll_ms))
	done
	[ -f "$path" ]
}

# One row per channel event, so a run's timing is measured rather than guessed
# from file times. $1 store, $2 actor, $3 event, $4 unit (a file's basename
# without .md, or -), $5 detail. Never fails the command it records. With
# PAIR_EVENT_DEDUPE=1 (the kitchen sets it), a master wake row identical to the log's last
# one in epoch, actor, event, unit and detail is not written twice.
event() {
	local log="$1/events.tsv" unit="${4:-}" epoch last
	unit="$(basename "${unit:--}" .md)"
	epoch="$(date +%s)"
	if [ "${PAIR_EVENT_DEDUPE:-}" = 1 ] && [ "$2" = master ] && [ "$3" = wake ]; then
		last="$(tail -n 1 "$log" 2>/dev/null | cut -f2- || true)"
		[ "$last" != "$(printf '%s\t%s\t%s\t%s\t%s' "$epoch" "$2" "$3" "$unit" "${5:-}")" ] || return 0
	fi
	printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$epoch" "$2" "$3" "$unit" "${5:-}" >>"$log" 2>/dev/null || true
}

header_field() {
	# $1 file, $2 key: the value of a "key: value" header line, or empty.
	grep -m1 -E "^$2:" "$1" | sed -E "s/^$2:[[:space:]]*//" || true
}

# Refuse a message file that still holds template text.
require_filled() {
	if grep -q '{{' "$1" || { [ -n "$unfilled_re" ] && grep -qE "$unfilled_re" "$1"; }; then
		die "$1 still has unfilled placeholders"
	fi
}

# Minutes a dispatch or wait blocks before printing a check-in. Set from
# --every or --timeout by the command; nine minutes stays under the shell cap.
checkin_interval_m=9

file_mtime() {
	stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"
}

# Epoch seconds of a UTC stamp as the store writes them
# (date -u +%Y-%m-%dT%H:%M:%SZ). jq parses it, since BSD date has no -d.

# setsid(1) where util-linux has it. macOS ships none, so perl starts the
# command in a session of its own.
detached() {
	if command -v setsid >/dev/null 2>&1; then
		setsid "$@"
	else
		perl -MPOSIX -e 'POSIX::setsid(); exec { $ARGV[0] } @ARGV or exit 127' "$@"
	fi
}

# The report a message asks for: reports/<NNN-slug>.md, where an answer's
# -a<k> suffix names the brief it answers.
expected_report() {
	printf '%s/reports/%s.md\n' "$1" "$(basename "$2" .md | sed -E 's/-a[0-9]+$//')"
}

# A brief's Scope entries, one path or glob per line, for `may` (the
# may write: list) or `mustnot` (must not write:). An entry is the text after
# "- ", without a " — " annotation or a trailing "(...)" note; one pair of
# surrounding backquotes is removed and what is inside is kept whole, commas and
# spaces included. Braces are literal, as in kitchen.py's glob language. Every
# consumer (the digest, the kitchen's classifier) reads Scope through this.
scope_entries() {
	awk -v want="$2" '
		/^may write:/ { f = (want == "may"); next }
		/^must not write:/ { f = (want == "mustnot"); next }
		/^## / { f = 0 }
		f && /^- / {
			sub(/^- /, "")
			if (substr($0, 1, 1) == "`") {
				e = substr($0, 2); i = index(e, "`")
				if (i > 0) e = substr(e, 1, i - 1)
			} else {
				e = $0; sub(/ +(—|–|--) .*$/, "", e); sub(/ +\(.*\)$/, "", e)
			}
			if (e != "") print e
		}' "$1"
}

# The repo's trunk: origin's default branch, else main or master; never the
# checked-out branch itself. Prints the ref, or nothing.
trunk_ref() {
	local root="$1" ref current candidates
	current="$(git -C "$root" symbolic-ref --quiet HEAD 2>/dev/null || true)"
	candidates="$(git -C "$root" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null || true)"
	for ref in $candidates refs/remotes/origin/main refs/remotes/origin/master refs/heads/main refs/heads/master; do
		[ "$ref" != "$current" ] || continue
		git -C "$root" rev-parse --verify --quiet "$ref" >/dev/null && { printf '%s\n' "$ref"; return 0; }
	done
	return 0
}

# Where a unit's range should start: never from before the point its branch
# leaves trunk. A unit dispatched before a landing, or rebased onto a moved
# trunk, keeps a recorded base that trunk has since replaced, and measuring
# from it takes trunk's commits in. $2 is the recorded base, $3 the range's
# tip. Sets range_floor_rev (the base to use) and range_floor_note (why it
# moved, or empty); call it directly, not in a command substitution. The floor
# moves only when merge-base(base, tip) is an ancestor of merge-base(trunk,
# tip), and, for a verification range that must not be empty, never to the tip
# itself. A digest passes --allow-head: zero own commits after a rebase is a
# real state there. With no trunk ref the base stays as it was.
range_floor() {
	local root="$1" base="$2" tip="$3" allow="${4:-}" mb trunk tmb
	range_floor_rev="$base"
	range_floor_note=""
	trunk="$(trunk_ref "$root")"
	[ -n "$trunk" ] || return 0
	mb="$(git -C "$root" merge-base "$base" "$tip" 2>/dev/null || printf '%s' "$base")"
	tmb="$(git -C "$root" merge-base "$trunk" "$tip" 2>/dev/null)" || return 0
	[ "$tmb" != "$mb" ] || return 0
	[ "$tmb" != "$tip" ] || [ "$allow" = --allow-head ] || return 0
	git -C "$root" merge-base --is-ancestor "$mb" "$tmb" 2>/dev/null || return 0
	range_floor_note="recorded base ${base:0:9} is behind where its branch leaves ${trunk#refs/}; measuring from ${tmb:0:9}"
	range_floor_rev="$tmb"
}

# A pause asked for at the next safe point: the sidekick stops at its next
# step or progress boundary with its work committed, and the master starts
# nothing new. $store/paused holds the reason; pair.sh resume removes it.
paused_reason() {
	[ -f "$1/paused" ] && head -1 "$1/paused"
}

require_not_paused() {
	local reason
	reason="$(paused_reason "$1")" || return 0
	die "the store is paused ($reason): start nothing new; record where the work stands and end your turn, or run pair.sh resume $1" 5
}

cmd_pause() { pycore pause "$@"; }

cmd_resume() { pycore resume "$@"; }

# Draft-PR review. The sidekick commits each step of a brief and records it
# with pair.sh step, then goes on without waiting. A wait returns each step
# the master has not reviewed; the master reviews that diff and publishes a
# note: blocking items the sidekick resolves at its next step boundary, and
# follow-ups that go to followups.md instead of widening the unit. The final
# review then reads only the diff since the last note.

# steps/<unit>.tsv, one row per step: k, sha, epoch, notes it resolves, summary.
steps_file() {
	printf '%s/steps/%s.tsv\n' "$1" "$(basename "$2" .md)"
}

provider_error_re='capacity issues|no api providers|no api key found|unauthorized|forbidden|rate.?limit|too many requests|overloaded|fetch failed|econnreset|econnrefused|etimedout|socket hang up|usage limit reached|wait for usage to reset|missing meta credentials|billing verification failed|billing_error'

cmd_init() { pycore init "$@"; }

cmd_permission() { pycore permission "$@"; }

# A dialog asking to trust the working folder (Claude, Codex) blocks an
# agent at startup. Trusting a repo is the human's decision, never the
# script's: decline it, which returns the pane to its shell, and stop with
# what to do. Claude's dialog defaults to "No, exit", so an Enter would quit.
trust_re='trust this folder|project you created or one you trust|do you trust the (files|contents)'

# Kinds whose sidekick starts a fresh conversation for each completed brief:
# Devin, Pi, and Prime, whose compacted conversation would otherwise carry every
# earlier brief into the next. The master rotates them; the queue waits.
fresh_per_brief() {
	case "$1" in devin | pi | prime-agent) return 0 ;; esac
	return 1
}

require_fresh_session() {
	local kind
	kind="$(field "$1" '.sidekick.kind')"
	if fresh_per_brief "$kind" && [ "$(field "$1" '(.sidekick.rotation_required // false) or (.sidekick.bootstrap_pending // false)')" = true ]; then
		die "the $kind sidekick needs a fresh session or a successful bootstrap before another task; inspect READY and run pair.sh rotate $1 after a completed task. The queue is preserved." 5
	fi
}

has_preflight() { pycore __has_preflight "$@" || { local pycore_rc=$?; case $pycore_rc in 1) return 1 ;; *) exit $pycore_rc ;; esac; }; }

# Muse Code: one headless prompt on the role's model proves the account is
# logged in and its plan has usage left. Only the arguments `muse exec`
# shares with the TUI pass through.
# Proves a kind's model answers before a pane starts. pi reaches some models
# through community provider packages that can break or run out of capacity,
# so it gets one print-mode prompt, without the repo's resources. Other kinds
# have no check here. $1 kind, rest: the native args the agent starts with.
# Prints the last lines of a failure on stderr.
preflight_kind() { pycore __preflight_kind "$@"; }

cmd_failover() { pycore --exec failover "$@"; }

cmd_rotate() { pycore --exec rotate "$@"; }

cmd_spawn() { pycore --exec spawn "$@"; }

cmd_new_plan() { pycore new-plan "$@"; }

cmd_new_brief() { pycore new-brief "$@"; }

cmd_discuss() { pycore --exec discuss "$@"; }

cmd_dispatch() { pycore --exec dispatch "$@"; }

# Seconds a settle must hold, with no reply, before a wait believes it, and
# between checks while herdr says settled but the pane shows the agent busy.
settle_hold_s="${PAIR_SETTLE_HOLD:-60}"
busy_poll_s="${PAIR_BUSY_POLL:-10}"

submit_typed() {
	local name="$1" marker="$2" kind tries=0
	submitted=0
	kind="$(agent_kind "$name")"
	while [ "$tries" -lt 3 ]; do
		sleep "${PAIR_SUBMIT_CHECK_S:-5}"
		grep -qF "$marker" <<<"$(herdr agent read "$name" --source visible --lines 12 2>/dev/null | tr -d '\n │')" || return 0
		[ "$kind" != devin ] || [ "$(agent_status "$name")" != working ] || return 0
		herdr agent send-keys "$name" enter >/dev/null 2>&1 || true
		tries=$((tries + 1))
		submitted="$tries"
	done
}

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
