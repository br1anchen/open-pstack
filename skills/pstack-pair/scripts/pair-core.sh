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
	printf 'pair.sh: %s\n' "$*" >&2
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
# the pid of this shell as PAIR_SHELL_PID ($$ is the main shell inside $( ) too).
# With --exec first, Python replaces the shell, so a signal sent to the command's
# process reaches the Python code (wait uses it: it must stop when it is stopped).
pycore() {
	local run=()
	[ "${1:-}" != --exec ] || { run=(exec); shift; }
	PAIR_SHELL_PID=$$ ${run[@]+"${run[@]}"} python3 -I "$pair_core_dir/pair.py" --skill "$PAIR_SKILL" --skill-root "$skill_root" \
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

next_seq() {
	local store="$1" max=0 n
	for f in "$store"/briefs/[0-9][0-9][0-9]-*.md "$store"/plans/[0-9][0-9][0-9]-*.md "$store"/reports/[0-9][0-9][0-9]-*.md; do
		[ -e "$f" ] || continue
		n=$((10#$(basename "$f" | cut -c1-3)))
		[ "$n" -gt "$max" ] && max=$n
	done
	printf '%03d\n' $((max + 1))
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
iso_epoch() {
	jq -rn --arg t "$1" '$t | fromdateiso8601'
}

# timeout(1) where coreutils has it. macOS ships none, so perl stands in with
# the same contract: $1 seconds, then the command; exit 124 when it runs out.
with_timeout() {
	if command -v timeout >/dev/null 2>&1; then
		timeout "$@"
	else
		perl -e 'my $s = shift; my $p = fork // die "fork: $!\n";
			unless ($p) { exec { $ARGV[0] } @ARGV or exit 127 }
			local $SIG{ALRM} = sub { kill "TERM", $p; waitpid $p, 0; exit 124 };
			alarm $s; waitpid $p, 0; exit($? & 127 ? 128 + ($? & 127) : $? >> 8)' "$@"
	fi
}

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

# A quiet progress log is not stale while a command the sidekick started
# after its last line still runs (a ten-minute test suite). The sidekick's
# processes are the ones carrying its pane's HERDR_PANE_ID, which a detached
# gate inherits too; the master's own commands carry the master's. A pane id
# is unique only within one Herdr server, so when this command runs under one
# (HERDR_SOCKET_PATH) a process counts only if it carries the same socket.
# Prints the longest-running one, preferring a command over the shell that
# runs it, and its age; or nothing. Linux only.
busy_command() {
	# The trackers start below any etimes, so a process under a second old (etimes 0) is still picked.
	local pane="$1" since="$2" now pid et comm best="" best_et=-1 shell="" shell_et=-1 env sock="${HERDR_SOCKET_PATH:-}"
	[ -n "$pane" ] && [ -d /proc ] || return 0
	now="$(date +%s)"
	while read -r pid et comm; do
		[ $((now - et)) -gt "$since" ] || continue
		env="$(tr '\0' '\n' 2>/dev/null <"/proc/$pid/environ")" || continue
		grep -qxF "HERDR_PANE_ID=$pane" <<<"$env" || continue
		[ -z "$sock" ] || grep -qxF "HERDR_SOCKET_PATH=$sock" <<<"$env" || continue
		case "$comm" in
		bash | sh | zsh | dash | fish) [ "$et" -le "$shell_et" ] || { shell_et="$et"; shell="$comm"; } ;;
		*) [ "$et" -le "$best_et" ] || { best_et="$et"; best="$comm"; } ;;
		esac
	done < <(ps -u "$(id -u)" -o pid=,etimes=,comm= 2>/dev/null)
	[ -n "$best" ] || { best="$shell"; best_et="$shell_et"; }
	[ -z "$best" ] || printf '%s for %dm' "$best" $((best_et / 60))
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

# Variant hook: further gates on an agreed plan. No bash calls it any more: the
# Python gate (pstack_core/gates.py) runs each variant's plan_gate_extra from its
# variant.py. Kept until phase 3 deletes the dead bash.
plan_gate_extra() { :; }

# Implementation playbooks may only run against a plan the sidekick agreed to
# and the master marked agreed. Exit 6 names what is missing.
require_agreed_plan() {
	local store="$1" brief="$2" playbook plan seq review verdict
	playbook="$(header_field "$brief" playbook)"
	case "$playbook" in
	feature | bug-fix | refactoring | perf-issue | pstack-tdd) ;;
	*) return 0 ;;
	esac
	plan="$(header_field "$brief" plan)"
	[ -n "$plan" ] && [ "$plan" != none ] || die "playbook $playbook needs an agreed plan; brief has plan: ${plan:-<missing>}" 6
	[ -f "$plan" ] || die "plan not found: $plan" 6
	seq="$(basename "$plan" | cut -c1-3)"
	review="$(ls "$store"/reviews/"$seq"-*.md 2>/dev/null | head -1 || true)"
	[ -n "$review" ] || die "plan $plan has no review; run pair.sh discuss and write reviews/$seq-<slug>.md with verdict: agreed" 6
	verdict="$(header_field "$review" verdict)"
	[ "$verdict" = agreed ] || die "plan $plan review verdict is '${verdict:-missing}', not agreed" 6
	plan_gate_extra "$store" "$plan" "$review" "$seq"
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

queued_brief() {
	# $1 store: the brief waiting in the queue, or empty.
	[ -f "$1/queue" ] && cat "$1/queue" || true
}

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

# True when a step landed that neither a note nor an earlier steps wake has
# covered: once shown, a range does not wake the master again while it reviews.
steps_due() {
	local brief file shown noted last
	unreviewed_steps "$1" >/dev/null || return 1
	brief="$(field "$1" '.dispatch.brief // empty')"
	file="$(steps_file "$1" "$brief")"
	shown="$(jq -r --arg u "$(basename "$brief" .md)" '.shown[$u] // 0' "$1/pair.json")"
	noted="$(noted_through "$1" "$brief")"
	last="$(grep -c . "$file")"
	[ "$last" -gt "$shown" ] && [ "$last" -gt "$noted" ]
}

# Blocking notes on a unit that no step has resolved yet, one path per line.
open_notes() {
	local unit f id file
	unit="$(basename "$2" .md)"
	file="$(steps_file "$1" "$unit")"
	for f in "$1"/notes/"$unit"-n[0-9]*.md; do
		[ -e "$f" ] || continue
		[ "$(header_field "$f" status)" = blocking ] || continue
		id="$(basename "$f" .md | sed -E 's/.*-(n[0-9]+)$/\1/')"
		if [ -f "$file" ] && awk -F '\t' -v id="$id" '{n = split($4, r, ","); for (i = 1; i <= n; i++) if (r[i] == id) found = 1} END {exit !found}' "$file"; then
			continue
		fi
		printf '%s\n' "$f"
	done
}

# Whether a report may be shown to the master as a reply. Sets report_open
# (the open note paths) and report_why (the reason it is withheld). Only a
# done unit report can be withheld: partial, blocked and failed reports, plan
# responses, asks (-q<k>) and objections (-s<k>) are ready as soon as written.
# A done report waits while its own unit has open blocking notes, and, when
# the unit has recorded steps and the report's head resolves to a commit, until
# that head contains the last recorded step, so a step that resolves the last
# note does not expose the report written before it. A unit with no steps, or a
# head that does not resolve (older stores, fixtures), is judged on notes alone.
# It judges the report's own unit, whichever brief is running now.
report_ready() {
	local store="$1" report="$2" base file head root last k
	report_open=""
	report_why=""
	[ "$(header_field "$report" status)" = "done" ] || return 0
	base="$(basename "$report" .md)"
	case "$base" in *-[sq][0-9]*) return 0 ;; esac
	report_open="$(open_notes "$store" "$report")"
	if [ -n "$report_open" ]; then
		report_why="open blocking notes $(printf '%s\n' "$report_open" | sed -E 's/.*-(n[0-9]+)\.md$/\1/' | paste -sd, -)"
		return 1
	fi
	file="$(steps_file "$store" "$base")"
	[ -s "$file" ] || return 0
	head="$(header_field "$report" head | grep -oE '^[0-9a-f]{7,40}' || true)"
	[ -n "$head" ] || return 0
	root="$(field "$store" '.git_root // .cwd')"
	git -C "$root" rev-parse --verify --quiet "$head^{commit}" >/dev/null 2>&1 || return 0
	k="$(tail -1 "$file" | cut -f1)"
	last="$(tail -1 "$file" | cut -f2)"
	git -C "$root" rev-parse --verify --quiet "$last^{commit}" >/dev/null 2>&1 || return 0
	git -C "$root" merge-base --is-ancestor "$last" "$head" 2>/dev/null && return 0
	report_why="the report's head ${head:0:9} predates step $k ${last:0:9}"
	return 1
}

provider_error_re='capacity issues|no api providers|no api key found|unauthorized|forbidden|rate.?limit|too many requests|overloaded|fetch failed|econnreset|econnrefused|etimedout|socket hang up|usage limit reached|wait for usage to reset|missing meta credentials|billing verification failed|billing_error'

# The error a pi agent stopped on, from its own session log: $1 the log herdr
# reported, else the newest log in pi's session directory for cwd $2. Printed
# only when the session's last assistant turn ended in an error, so a limit
# pi recovered from is not reported. A pane that is gone still says why.
agent_session_error() {
	local f="$1" dir
	if [ -z "$f" ] || [ ! -f "$f" ]; then
		[ -n "${2:-}" ] || return 0
		dir="$HOME/.pi/agent/sessions/--$(printf '%s' "${2#/}" | tr '/' '-')--"
		f="$(ls -t "$dir"/*.jsonl 2>/dev/null | head -1 || true)"
	fi
	[ -n "$f" ] && [ -f "$f" ] || return 0
	tail -n 200 "$f" | jq -r 'select(.message.role? == "assistant") | [.message.stopReason // "", .message.errorMessage // ""] | @tsv' 2>/dev/null |
		tail -1 | awk -F '\t' '$1 == "error" && $2 != "" {print substr($2, 1, 240)}' || true
}

# The provider error a Muse Code session stopped on, from its own log: the
# newest session.jsonl under Muse's session store that names working
# directory $1 (Herdr reports no Muse session id). Muse writes its usage
# limit as "Usage limit reached" and "wait for usage to reset at <time>", and
# an account whose payment is not verified as HTTP 402 "Billing verification
# failed" (billing_error).
muse_session_error() {
	local cwd="$1" f
	[ -n "$cwd" ] || return 0
	f="$(ls -t "${XDG_DATA_HOME:-$HOME/.local/share}"/muse/sessions/*/*/*/*/session.jsonl 2>/dev/null | head -20 |
		while IFS= read -r x; do grep -qF -- "$cwd" "$x" && { printf '%s\n' "$x"; break; }; done)"
	[ -n "$f" ] || return 0
	tail -n 400 "$f" | grep -oiE ".{0,60}($provider_error_re).{0,100}" | tail -1 | cut -c1-240 || true
}

cmd_init() { pycore init "$@"; }

# The master's permission mode, in Claude Code vocabulary:
# auto | acceptEdits | bypassPermissions | manual | dontAsk | plan.
# Sources in order: the launching command line of the agent process above this
# shell, the current Claude session transcript (a mode toggled at runtime lands
# there), settings defaultMode, then auto.
detect_permission_mode() { pycore __detect_permission_mode; }

# Native arguments that give an agent of KIND the permission MODE. Empty for
# kinds without a known translation; pass native flags after -- for those.
permission_args() { pycore __permission_args "$@"; }

# Native arguments that skip the kind's prompt to trust the working folder.
# The prompt appears in any folder the agent has not seen (pi: any repo with
# .agents/skills), would hang an unattended agent, and eats the bootstrap's
# Enter. The master already works in that folder.
trust_args() { pycore __trust_args "$@"; }

# A spawned agent needs none of the human's MCP servers, and each one is a
# process per session: the Railway server alone held 150-250 MB in every
# sidekick and verifier on this host, and a Codex consultant's set (Chrome
# DevTools, Firebase, Railway, a repo's own) over 600 MB. Claude starts with an
# empty MCP configuration unless the native arguments name one. Codex has no
# such switch: it starts with plugins off (the account's curated plugins, such
# as Firebase and OpenAI Developers, each bring MCP servers `codex mcp list`
# does not show; pstack roles load their skills from ~/.agents/skills, not
# plugins), and each server `codex mcp list` reports in $2 (the agent's
# working directory: a repo can add its own) is turned off by name. A name
# that is not a bare TOML key is left on, since Codex rejects a quoted one.
mcp_args() { pycore __mcp_args "$@"; }

has_mcp_arg() { pycore __has_mcp_arg "$@" || { local pycore_rc=$?; case $pycore_rc in 1) return 1 ;; *) exit $pycore_rc ;; esac; }; }

# True when the native arguments already decide folder trust.
has_trust_arg() { pycore __has_trust_arg "$@" || { local pycore_rc=$?; case $pycore_rc in 1) return 1 ;; *) exit $pycore_rc ;; esac; }; }

# True when the caller's native arguments already set a permission policy.
has_permission_arg() { pycore __has_permission_arg "$@" || { local pycore_rc=$?; case $pycore_rc in 1) return 1 ;; *) exit $pycore_rc ;; esac; }; }

cmd_permission() { pycore permission "$@"; }

pick_direction() {
	# Cells are about twice as tall as wide: a pane is visually wider than tall
	# when width >= 2 * height. Split wide panes right and tall panes down.
	local pane="$1" w h
	read -r w h < <(herdr pane layout --pane "$pane" | jq -r --arg p "$pane" '.result.layout.panes[] | select(.pane_id == $p) | "\(.rect.width) \(.rect.height)"')
	if [ "${w:-0}" -ge $((2 * ${h:-0})) ]; then printf 'right\n'; else printf 'down\n'; fi
}

# A dialog asking to trust the working folder (Claude, Codex) blocks an
# agent at startup. Trusting a repo is the human's decision, never the
# script's: decline it, which returns the pane to its shell, and stop with
# what to do. Claude's dialog defaults to "No, exit", so an Enter would quit.
trust_re='trust this folder|project you created or one you trust|do you trust the (files|contents)'
refuse_untrusted() { pycore __refuse_untrusted "$@" || exit $?; }

# A new pane for a role: a split beside the anchor, or, with placement tab
# (spawn --tab, or PSTACK_PLACEMENT=tab), a tab of its own in the master's
# workspace, labelled with the role. Sets new_pane_id. $1 store, $2 role,
# $3 anchor, $4 direction (empty picks one), $5 cwd.
new_pane() {
	local store="$1" role="$2" anchor="$3" direction="$4" cwd="$5" master
	if [ "$(field "$store" '.placement // empty')" = tab ]; then
		master="$(field "$store" .master.pane_id)"
		new_pane_id="$(herdr tab create --workspace "${master%%:*}" --cwd "$cwd" --label "$(field "$store" .slug)-$role" --no-focus |
			jq -r '.result.root_pane.pane_id')"
		[ -n "$new_pane_id" ] && [ "$new_pane_id" != null ] || die "tab create returned no pane id" 2
		printf 'tab %s-%s -> %s\n' "$(field "$store" .slug)" "$role" "$new_pane_id"
	else
		[ -n "$direction" ] || direction="$(pick_direction "$anchor")"
		new_pane_id="$(herdr pane split --pane "$anchor" --direction "$direction" --cwd "$cwd" --no-focus | jq -r '.result.pane.pane_id')"
		[ -n "$new_pane_id" ] && [ "$new_pane_id" != null ] || die "pane split returned no pane id" 2
		printf 'split %s %s -> %s\n' "$anchor" "$direction" "$new_pane_id"
	fi
}

# Records how roles get their panes: --tab or --split from spawn, else what
# the store already has, else PSTACK_PLACEMENT, else split.
set_placement() {
	local store="$1" want="${2:-}"
	[ -n "$want" ] || [ -n "$(field "$store" '.placement // empty')" ] || want="${PSTACK_PLACEMENT:-split}"
	[ -n "$want" ] || return 0
	case "$want" in split | tab) ;; *) die "placement must be split or tab, not $want" ;; esac
	json_update "$store" --arg p "$want" '.placement = $p'
}

# Start one role, record it in pair.json, and bootstrap it. It reuses $pane,
# or the role's previous pane when that is back at a shell prompt, and
# otherwise splits $anchor in $direction (picked from the anchor's shape when
# empty). $1 store, $2 role, $3 kind, $4 permission mode, $5 anchor pane,
# $6 timeout, $7 direction, $8 pane, rest: native agent args. Returns 3 when
# the bootstrap did not settle and 4 when it settled without the ready file.
spawn_role() { pycore __spawn_role "$@" || { local pycore_rc=$?; case $pycore_rc in 3 | 4) return $pycore_rc ;; *) exit $pycore_rc ;; esac; }; }

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

record_failover() {
	# $1 store, $2 from kind, $3 to kind, $4 reason
	json_update "$1" --arg from "$2" --arg to "$3" --arg why "$4" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		'.sidekick.failovers = ((.sidekick.failovers // []) + [{from: $from, to: $to, reason: $why, at: $now}])'
	event "$1" master failover - "$2:$3"
	printf 'failover: %s -> %s (%s)\n' "$2" "$3" "$4"
}

# Starts the sidekick, or its recorded fallback when the kind's preflight
# fails. The fallback starts with its own recorded arguments and its kind's
# default permission. A live sidekick skips the preflight. Arguments as
# spawn_role, without the role.
spawn_sidekick() {
	local store="$1" kind="$2" permission="$3" anchor="$4" timeout="$5" direction="$6" pane="$7" reason fallback
	shift 7
	if has_preflight "$kind" && [ "$(agent_status "$(field "$store" .sidekick.name)")" = absent ]; then
		if ! reason="$(preflight_kind "$kind" "$@" 2>&1)"; then
			reason="preflight: ${reason:-no answer}"
			printf '%s failed %s\n' "$kind" "$reason" >&2
			fallback="$(field "$store" '.sidekick.fallback.kind // empty')"
			[ -n "$fallback" ] && [ "$fallback" != "$kind" ] || die "no fallback recorded for a failed $kind; pass --fallback KIND or fix the agent" 2
			record_failover "$store" "$kind" "$fallback" "$reason"
			local -a args=()
			mapfile -t args < <(jq -r '.sidekick.fallback.args[]?' "$store/pair.json")
			spawn_role "$store" sidekick "$fallback" "" "$anchor" "$timeout" "$direction" "$pane" "${args[@]}"
			return
		fi
	fi
	spawn_role "$store" sidekick "$kind" "$permission" "$anchor" "$timeout" "$direction" "$pane" "$@"
}

# Records the sidekick's fallback from the spawn options: $1 store, $2 kind
# (empty keeps any recorded one), rest: its native arguments.
record_fallback() {
	local store="$1" kind="$2"
	shift 2
	[ -n "$kind" ] || return 0
	json_update "$store" --arg kind "$kind" --argjson args "$(jq -cn --args '$ARGS.positional' -- "$@")" \
		'.sidekick.fallback = {kind: $kind, args: $args}'
}

exit_command() {
	case "$1" in
	pi | prime-agent | codex | muse) printf '/quit\n' ;;
	*) printf '/exit\n' ;;
	esac
}

# Ends a role's agent and closes its pane, for a run that is over: the exit
# command, then up to fifteen seconds for the agent to go (a Claude asking
# what to do with its background tasks is told to stop them and exit). The
# pane closes only once the agent is gone; an agent that stays keeps its pane
# and the function returns 1. A role never spawned, or already closed, is
# skipped.
close_role() {
	local store="$1" role="$2" name pane kind deadline
	name="$(field "$store" ".$role.name // empty")"
	pane="$(field "$store" ".$role.pane_id // empty")"
	[ -n "$name" ] && [ -n "$pane" ] || return 0
	[ "$(field "$store" ".$role.closed // false")" != true ] || return 0
	kind="$(field "$store" ".$role.kind // empty")"
	if [ "$kind" = prime-agent ]; then
		stop_prime_role "$store" "$role" || return 1
	fi
	if [ "$(agent_status "$name")" != absent ]; then
		herdr agent prompt "$name" "$(exit_command "$kind")" >/dev/null 2>&1 || true
		deadline=$(( $(date +%s) + 15 ))
		while [ "$(agent_status "$name")" != absent ] && [ "$(date +%s)" -lt "$deadline" ]; do
			if grep -qi 'move to background and exit' <<<"$(pane_text "$name")"; then
				herdr agent send-keys "$name" 1 >/dev/null 2>&1 || true
				herdr agent send-keys "$name" enter >/dev/null 2>&1 || true
			fi
			sleep 1
		done
	fi
	if [ "$(agent_status "$name")" != absent ]; then
		printf '%s %s did not exit; its pane %s stays open\n' "$role" "$name" "$pane" >&2
		return 1
	fi
	herdr pane close "$pane" >/dev/null 2>&1 || true
	json_update "$store" --arg r "$role" '.[$r].closed = true'
	event "$store" master close - "$role:$kind"
	printf 'closed %s %s (%s)\n' "$role" "$name" "$pane"
}

# Replaces a failing sidekick with its recorded fallback in the same pane.
# The new agent picks up from the store: the step log, progress, and the
# brief it was on; every committed step survives. A run stays on the
# fallback, since failover never switches back.
cmd_failover() {
	in_herdr
	[ $# -ge 1 ] || die "usage: pair.sh failover <store> [--reason TEXT] [--force]"
	local store="$1" reason=requested force=0
	shift
	while [ $# -gt 0 ]; do
		case "$1" in
		--reason) reason="$2"; shift 2 ;;
		--force) force=1; shift ;;
		*) die "unknown option $1" ;;
		esac
	done
	pair_file "$store" >/dev/null
	local kind fallback name status deadline rc=0
	kind="$(field "$store" .sidekick.kind)"
	fallback="$(field "$store" '.sidekick.fallback.kind // empty')"
	[ -n "$fallback" ] || die "no fallback recorded; spawn the sidekick with --fallback KIND" 5
	[ "$kind" != "$fallback" ] || die "the sidekick already runs the fallback ($fallback)" 5
	name="$(field "$store" .sidekick.name)"
	status="$(agent_status "$name")"
	if [ "$status" = working ]; then
		[ "$force" -eq 1 ] || die "sidekick $name is working; pair.sh stop $store first, or pass --force to interrupt it" 5
		herdr agent send-keys "$name" esc >/dev/null 2>&1 || true
		sleep 2
	fi
	if [ "$kind" = prime-agent ]; then
		stop_prime_role "$store" sidekick || die "Prime session cleanup failed; no fallback was started" 5
	elif [ "$status" != absent ]; then
		herdr agent prompt "$name" "$(exit_command "$kind")" >/dev/null 2>&1 || true
		deadline=$(($(date +%s) + 15))
		while [ "$(agent_status "$name")" != absent ]; do
			[ "$(date +%s)" -lt "$deadline" ] || die "$kind sidekick has not exited; close it by hand, then rerun failover" 5
			sleep 1
		done
	fi
	record_failover "$store" "$kind" "$fallback" "$reason"
	local -a args=()
	mapfile -t args < <(jq -r '.sidekick.fallback.args[]?' "$store/pair.json")
	spawn_role "$store" sidekick "$fallback" "" "$(field "$store" .master.pane_id)" 60000 "" "$(field "$store" .sidekick.pane_id)" "${args[@]}" || rc=$?
	local running
	running="$(field "$store" '.dispatch.brief // empty')"
	if [ "$rc" -eq 0 ] && [ -n "$running" ] && [ ! -f "$(expected_report "$store" "$running")" ]; then
		printf 'next: re-dispatch %s with "resumed after failover from %s" in its Context\n' "$running" "$kind"
	fi
	exit "$rc"
}

# Rotation exits the idle sidekick and reuses its pane with the same native
# arguments. It preserves the store and never deletes a Devin session.
cmd_rotate() {
	in_herdr
	[ $# -eq 1 ] || die "usage: pair.sh rotate <store>"
	local store="$1" name status err code=0 deadline
	pair_file "$store" >/dev/null
	require_not_paused "$store"
	local kind
	kind="$(field "$store" '.sidekick.kind')"
	fresh_per_brief "$kind" || die "rotate is for a sidekick that starts fresh per brief (devin, pi, prime-agent), not $kind" 5
	[ "$(field "$store" '.sidekick.rotation_required // false')" = true ] || die "rotation requires a completed task; partial or blocked work keeps its session" 5
	jq -e '.sidekick.start_args | type == "array"' "$store/pair.json" >/dev/null || die "no recorded startup arguments; exit the idle sidekick and spawn it with its original model and permission flags" 5
	name="$(field "$store" .sidekick.name)"
	status="$(agent_status "$name")"
	case "$status" in
	idle | "done")
		if [ "$kind" = prime-agent ]; then
			stop_prime_role "$store" sidekick || die "Prime session cleanup failed; no new session was started" 5
		else
			err="$(herdr agent prompt "$name" "$(exit_command "$kind")" 2>&1)" || code=$?
			if [ "$code" -ne 0 ] && ! grep -qE 'agent_prompt_stalled|agent_not_found|not_found' <<<"$err"; then
				die "$kind exit failed: $err" 2
			fi
			deadline=$(($(date +%s) + 15))
			while [ "$(agent_status "$name")" != absent ]; do
				[ "$(date +%s)" -lt "$deadline" ] || die "$kind has not exited; inspect $name before retrying rotate" 5
				sleep 1
			done
		fi
		;;
	absent)
		[ "$kind" != prime-agent ] || stop_prime_role "$store" sidekick || die "Prime session cleanup failed; no new session was started" 5 ;;
	*) die "sidekick $name is $status; wait for its report and turn to end before rotating" 5 ;;
	esac
	local -a args
	mapfile -t args < <(jq -r '.sidekick.start_args[]' "$store/pair.json")
	event "$store" master rotate - "generation:$(field "$store" .sidekick.generation)"
	spawn_role "$store" sidekick "$kind" none "$(field "$store" .master.pane_id)" 60000 "" "$(field "$store" .sidekick.pane_id)" "${args[@]}"
}

cmd_spawn() { pycore --exec spawn "$@"; }

new_from_template() {
	# $1 store, $2 slug, $3 directory (plans|briefs), $4 template name
	local store="$1" slug="$2" dir="$3" seq path
	pair_file "$store" >/dev/null
	[[ "$slug" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "${dir%s} slug must be lowercase [a-z0-9_-]"
	seq="$(next_seq "$store")"
	path="$store/$dir/$seq-$slug.md"
	sed -e "s|{{SEQ}}|$seq|g" -e "s|{{SLUG}}|$slug|g" -e "s|{{STORE}}|$store|g" \
		"$skill_root/references/$4" >"$path"
	printf '%s\n' "$path"
}

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
