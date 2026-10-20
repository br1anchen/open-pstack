#!/usr/bin/env bash
# Kitchen store and Herdr channel helper for the pstack-kitchen skill: the
# pair's master and sidekick, a consultant started only when a unit
# escalates, and a short-lived verifier pane per verification. The shared
# commands live in pstack-pair's pair-core.sh and the consultant's in
# pstack-trio's consultant-core.sh; the repo's rules are read through
# kitchen.py from .agents/kitchen.toml or the host kitchen.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
skill_root="$(dirname "$here")"
skills_dir="$(dirname "$skill_root")"
PAIR_SKILL=pstack-kitchen
PAIR_PROG=kitchen.sh
export PAIR_EVENT_DEDUPE=1
state_root="${XDG_STATE_HOME:-$HOME/.local/state}/pstack/kitchen/runs"
store_label=Kitchen
roles=(sidekick verifier consultant)
store_dirs=(plans briefs reports reviews progress steers consults advice scratch verdicts)
slug_max=20
# shellcheck source-path=SCRIPTDIR source=../../pstack-pair/scripts/pair-core.sh
. "$skill_root/../pstack-pair/scripts/pair-core.sh"
# shellcheck source-path=SCRIPTDIR source=../../pstack-trio/scripts/consultant-core.sh
. "$skill_root/../pstack-trio/scripts/consultant-core.sh"

usage() {
	cat <<'USAGE'
usage: kitchen.sh <command> [args]

The pair's commands (pstack-pair's pair.sh help) work unchanged: init, rotate, failover, permission,
new-plan, new-brief, wait, queue, notes, new-note, note, finish, next, progress, new-steer, steer,
report, notify, stop, scratch, pause, resume, status, log, metrics, and the consultant's new-consult,
consult, advice. These differ or are new:

  log <store> <phase> <decision> <why> <evidence> <result>
                                            append one row to decisions.tsv and a log event for the master

  spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [-- args...]
                                            start the sidekick; kind, args, and fallback default to the
                                            host roster (~/.config/pstack/kitchen.toml); --tab gives every role
                                            (sidekick, consultant, verifier) its own tab instead of a split
  consultant <store> --reason TEXT [--kind KIND] [-- args...]
                                            start the consultant for an escalated unit (roster kind by default)
  classify <store> <brief-path>             map the brief's may-write Scope to profiles and a risk class and
                                            stamp risk:, profiles:, verify: into its header
  discuss <store> <plan-path>               a routine plan goes to the sidekick; risk: escalated to both
  dispatch <store> <brief-path> [...] [--max MIN] [--send-only]
                                            classify, then the pair's dispatch; escalated units need an agreed
                                            plan with advice, landing units need land-check to pass; waits quietly,
                                            or returns once the brief is delivered with --send-only
  wait <store> [...] [--max MIN]            the pair's wait, quiet through check-ins with nothing flagged; returns
                                            for a report, steps, a block, a flagged check-in, or after --max (60)
  step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]
                                            sidekick: run the touched profiles' fast gates and policy on the
                                            step's commits, then record it; exit 2 when they fail. The checks run
                                            detached: exit 4 "still running" after ~2 minutes, run it again to wait
  finish <store> <report>                   the pair's finish; a done report first reruns the brief's Verify
                                            commands as written at its head: (the checkout, clean), and goes to
                                            the master only when every one exits 0 (exit 2 otherwise, 4 while
                                            they run: run it again to wait)
  verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND]
                                            verify one unit, or several as a batch, or a fix with the unit it
                                            fixes (--covers): gates only, or a fresh verifier in a scratch
                                            worktree at the head; returns at the interval with its progress
                                            (exit 4; --until-verdict never returns at the interval, only on
                                            the verdict, the deadline, a stall or a provider error), and with
                                            the verdict and whether to audit when it lands.
                                            A verifier stopped by its provider is retried on the sidekick's
                                            fallback; the deadline defaults to 3x the measured gate time
  verify <store> --landing [--base REV] [--parent REF|none] [--head REV]
                                            a fresh verifier at the stack tip: every behavioral gate the stack
                                            touches, from where it leaves trunk, or from the parent --parent
                                            records for later landings (--parent none clears it; --base wins
                                            for one call and may be combined with --parent);
                                            --head REV proves that commit or branch instead of HEAD
  verify <store> --wait [--every MIN | --until-verdict]
                                            wait again for the open verification: exit 4 while it runs, 5 when none
                                            is open
  verify <store> --cancel                   stop the open verification that has no verdict yet: its verifier, pane
                                            and scratch go (exit 5 when none is open); a verdict written but
                                            unread is closed by the next verify (`closed:`)
  revise <store> <verdict-path>             draft the fix brief for a rejected verdict
  land-check <store>                        every accepted unit verified
  catch <store> <layer> <text> [<unit>]     record a miss found later (audit, human review): layer is
                                            gate, policy, self, verify, review, triage, or escalation;
                                            the unit (e.g. 003) gives the ledger row its area
  new-handoff <store>                       write session-handoff.md from the template, with every unit's state
  rotate-master <store> [--model M]         record the filled handoff and this master's session, and start the
                                            next generation in a pane beside this one (MCP-free, remote control
                                            on, same permission mode and model); nudged by verify after
                                            KITCHEN_MASTER_ROTATE_UNITS (5) verified units
  take-master <store>                       the new master's first command: close the old master's pane and
                                            take back the run's master name
  close <store> [--force]                   end a finished run's sidekick, consultant and verifier and close
                                            their panes; refuses while a verification is open or the sidekick
                                            works (--force overrides); the master's own pane stays
  retro <store>                             where the master was needed, and what repeated, from this run
                                            and the repo's ledger
  feedback <store> <file>                   file the master's kitchen gaps (see references/feedback-template.md)
                                            into the host inbox; tells the registered maintainer, else the human
  feedback --inbox                          open reports, oldest first
  feedback --close <id> <commit|none> <note>
                                            retire a report (closing twice is harmless)
  maintainer on [--pane ID] | off | status  register this session to receive FEEDBACK pointers
  fleet [--since YYYY-MM-DD] [--runs DIR] [--json]
                                            one row per run on the host (verified units, wakes, failovers,
                                            provider stops, catches, open feedback), then one per skills build;
                                            read-only, no store needed

exit codes: the pair's, plus 2 for a failed gate, a rejected or invalid verdict, or a failed land-check
USAGE
}

copy_fn() {
	# Keeps the shared definition of $1 callable as $2 before this file
	# replaces $1.
	eval "$2() $(declare -f "$1" | tail -n +2)"
}
copy_fn cmd_init core_init
copy_fn cmd_spawn core_spawn
copy_fn cmd_dispatch core_dispatch
copy_fn cmd_wait core_wait
copy_fn cmd_queue core_queue
copy_fn cmd_discuss core_discuss
copy_fn cmd_step core_step
copy_fn cmd_finish core_finish

# A plan's or brief's risk class: the first word of its risk: header, which
# a master may follow with the reason ("escalated (contract X)").
risk_of() {
	header_field "$1" risk | awk '{print $1}'
}

kpy() {
	# kitchen.py against the store's repo: $1 store, rest: its arguments.
	local store="$1"
	shift
	python3 "$here/kitchen.py" --repo "$(field "$store" .git_root)" "$@"
}

set_header() {
	# $1 file, $2 key, $3 value: replace the key's header line, or add it
	# after the plan: line.
	if grep -q "^$2:" "$1"; then
		KEY="$2" VAL="$3" perl -i -pe 's/^\Q$ENV{KEY}\E:.*$/$ENV{KEY}: $ENV{VAL}/ if !$done && /^\Q$ENV{KEY}\E:/ && ($done = 1)' "$1"
	else
		KEY="$2" VAL="$3" perl -i -pe '$_ .= "$ENV{KEY}: $ENV{VAL}\n" if !$done && /^plan:/ && ($done = 1)' "$1"
	fi
}

scope_globs() {
	# The brief's may-write entries, without their notes.
	scope_entries "$1" may
}

# The stack's patch-ids for <base>..<head> (merges left out), built once per call: stack_ids maps a
# patch-id to its commit, stack_index a commit to its place from the oldest, stack_commits lists them
# oldest first, and stack_id_of and stack_id0_of map a commit to its patch-id with the default context
# and with none (the revert scan reads the second). $1 root, $2 base, $3 head.
declare -A stack_ids=() stack_index=() stack_id_of=() stack_id0_of=()
declare -a stack_commits=()
stack_ids_range=""
# The repo's kitchen speaks through the standing orders too: the review
# skills the sidekick runs before done, and how far landing goes.
# The flake rev that built a skills directory, from the switch's builds.tsv
# ("unknown" for a path no switch recorded, such as a working checkout).

# A run stamped at init lists the skills directories its commands ran from; a
# command from a different one adds a row. A run from before stamps stays unstamped.
record_skill_build() {
	[ "$(field "$1" '.skill_builds // empty | length')" != "" ] || return 0
	[ "$(field "$1" '.skill_builds[-1].path')" != "$skills_dir" ] || return 0
	json_update "$1" --arg p "$skills_dir" --arg a "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '.skill_builds += [{path: $p, at: $a}]'
}

cmd_init() {
	local out store json orders
	out="$(core_init "$@")"
	printf '%s\n' "$out"
	orders="$(sed -n 's/^standing orders: //p' <<<"$out")"
	store="$(dirname "$orders")"
	json_update "$store" --arg p "$skills_dir" --arg a "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '.skill_builds = [{path: $p, at: $a}]'
	json="$(kpy "$store" --json validate 2>/dev/null)" || { printf 'kitchen: none valid in this repo; run pstack-kitchen-setup before dispatching\n' >&2; return 0; }
	grep -q 'kitchen.toml review.self' "$orders" || [ "$(jq '.self | length' <<<"$json")" -eq 0 ] ||
		printf '16. Before reporting done, the sidekick runs these skills on its diff and fixes what they find: %s (kitchen.toml review.self).\n' \
			"$(jq -r '.self | join(", ")' <<<"$json")" >>"$orders"
	grep -q 'kitchen.toml landing.mode' "$orders" ||
		printf '17. Landing goes as far as `%s` (kitchen.toml landing.mode) and never merges.\n' "$(jq -r .landing <<<"$json")" >>"$orders"
	worktree_setup "$store" "$json"
}

# A run in a linked worktree (git worktree add, a jj workspace) starts without
# the main checkout's untracked dependencies, so its gates would run whatever
# tool the PATH has instead of the repo's pinned one. init prepares it once
# the way verifier scratches are prepared: the repo's [scratch].setup, as the
# sidekick. A failed setup stops init with its log. $2 is validate's JSON.
worktree_setup() {
	local store="$1" root out rc=0
	root="$(field "$store" .git_root)"
	[ "$(jq '.setup | length' <<<"$2")" -gt 0 ] || return 0
	[ "$(git -C "$root" rev-parse --path-format=absolute --git-dir)" != "$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)" ] || return 0
	out="$(kpy "$store" setup --role sidekick 2>&1)" || rc=$?
	event "$store" master setup - "$([ "$rc" -eq 0 ] && echo pass || echo fail)"
	[ "$rc" -eq 0 ] || die "this run is in a linked worktree and its [scratch].setup failed:
$out" 2
	printf 'worktree: prepared with [scratch].setup (%s)\n' "$out"
}

cmd_spawn() {
	in_herdr
	[ $# -ge 1 ] || die "usage: kitchen.sh spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [-- agent-args...]"
	local store="$1" kind="" fallback="" roster rest=() native=() fb_args=() a
	shift
	while [ $# -gt 0 ]; do
		case "$1" in
		--kind) kind="$2"; shift 2 ;;
		--fallback) fallback="$2"; shift 2 ;;
		--) shift; native=("$@"); break ;;
		*) rest+=("$1"); shift ;;
		esac
	done
	roster="$(python3 "$here/kitchen.py" --json roster)"
	if [ -z "$kind" ]; then
		kind="$(jq -r '.sidekick.kind' <<<"$roster")"
		[ -n "$kind" ] || die "no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)"
		[ "${#native[@]}" -gt 0 ] || mapfile -t native < <(jq -r '.sidekick.args[]' <<<"$roster")
	fi
	if [ -z "$fallback" ] && [ "$kind" = "$(jq -r '.sidekick.kind' <<<"$roster")" ]; then
		fallback="$(jq -r '.sidekick.fallback.kind' <<<"$roster")"
		mapfile -t fb_args < <(jq -r '.sidekick.fallback.args[]' <<<"$roster")
	fi
	[ -z "$fallback" ] || rest+=(--fallback "$fallback")
	for a in "${fb_args[@]}"; do rest+=(--fallback-arg "$a"); done
	core_spawn "$store" --kind "$kind" "${rest[@]}" -- "${native[@]}"
}

# The consultant joins only when a unit escalates: the scarcest quota is
# spent on design questions that warrant it.
cmd_consultant() {
	in_herdr
	[ $# -ge 1 ] || die "usage: kitchen.sh consultant <store> --reason TEXT [--kind KIND] [--permission MODE|none] [-- agent-args...]"
	local store="$1" reason="" kind="" permission="" native=()
	shift
	while [ $# -gt 0 ]; do
		case "$1" in
		--reason) reason="$2"; shift 2 ;;
		--kind) kind="$2"; shift 2 ;;
		--permission) permission="$2"; shift 2 ;;
		--) shift; native=("$@"); break ;;
		*) die "unknown option $1" ;;
		esac
	done
	pair_file "$store" >/dev/null
	[ -n "$reason" ] || die "--reason is required: the escalation that warrants the consultant"
	local fallback="" why
	local -a fb_args=()
	if [ -z "$kind" ]; then
		local roster
		roster="$(python3 "$here/kitchen.py" --json roster)"
		kind="$(jq -r '.consultant.kind' <<<"$roster")"
		[ "${#native[@]}" -gt 0 ] || mapfile -t native < <(jq -r '.consultant.args[]' <<<"$roster")
		fallback="$(jq -r '.consultant.fallback.kind // empty' <<<"$roster")"
		mapfile -t fb_args < <(jq -r '.consultant.fallback.args[]?' <<<"$roster")
	fi
	[ -n "$kind" ] || die "no --kind and no [consultant] kind in the host roster"
	# A consultant whose kind has a preflight (Muse, pi, Prime) is checked
	# before its pane starts, as the sidekick is: a logged-out account or a
	# spent plan starts the roster's [consultant.fallback] instead.
	if has_preflight "$kind" && [ "$(agent_status "$(field "$store" .consultant.name)")" = absent ]; then
		if ! why="$(preflight_kind "$kind" "${native[@]}" 2>&1)"; then
			[ -n "$fallback" ] && [ "$fallback" != "$kind" ] ||
				die "consultant $kind failed its preflight and the roster names no other fallback: ${why:-no answer}" 2
			printf 'failover: consultant %s -> %s (preflight: %s)\n' "$kind" "$fallback" "$(tr '\n' ' ' <<<"${why:-no answer}" | cut -c1-200)"
			json_update "$store" --arg from "$kind" --arg to "$fallback" --arg why "preflight: ${why:-no answer}" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
				'.consultant.failovers = ((.consultant.failovers // []) + [{from: $from, to: $to, reason: $why, at: $now}])'
			event "$store" master failover - "consultant:$kind:$fallback"
			kind="$fallback"
			native=("${fb_args[@]}")
		fi
	fi
	# The escalation is recorded once the consultant is up, so a start that
	# fails (a trust prompt) and is retried counts it once.
	spawn_consultant "$store" "$kind" "$permission" 60000 "${native[@]}"
	json_update "$store" --arg why "$reason" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		'.escalations = ((.escalations // []) + [{reason: $why, at: $now}])'
	event "$store" master escalate - "$reason"
}

# Why a brief is escalated beyond its paths, one line each: the master wrote
# risk: escalated on it, or its plan is escalated. Classify only raises a
# brief's risk from these, never lowers it.
brief_escalations() {
	local plan
	[ "$(risk_of "$1")" != escalated ] || printf 'the brief says risk: escalated\n'
	plan="$(header_field "$1" plan)"
	case "$plan" in "" | none) return 0 ;; esac
	[ ! -f "$plan" ] || [ "$(risk_of "$plan")" != escalated ] || printf 'escalated plan: %s\n' "$(basename "$plan")"
}

# A host kitchen has no history, so a brief is classified, stepped and
# verified against the copy taken when it was classified (kitchen: in its
# header), the way a repo kitchen is read at the unit's commit. $1 store;
# prints the copy, or nothing for a repo kitchen.
kitchen_snapshot() {
	kpy "$1" snapshot "$1/kitchens" || die "kitchen.py snapshot failed; run kitchen.py validate in the repo"
}

# --config <snapshot> for $1 brief, in kitchen_cfg; empty for a repo kitchen.
brief_kitchen() {
	local snap
	kitchen_cfg=()
	snap="$(header_field "$1" kitchen)"
	case "$snap" in "" | repo) ;; *) kitchen_cfg=(--config "$snap") ;; esac
}

classify_brief() {
	# $1 store, $2 brief: stamp risk, profiles, verify, kitchen; print the summary.
	local store="$1" brief="$2" json risk reason snap
	local -a globs esc=()
	mapfile -t globs < <(scope_globs "$brief")
	[ "${#globs[@]}" -gt 0 ] || die "$brief lists nothing under Scope's may write:"
	while IFS= read -r reason; do esc+=(--escalate "$reason"); done < <(brief_escalations "$brief")
	snap="$(kitchen_snapshot "$store")"
	if [ -n "$snap" ]; then
		set_header "$brief" kitchen "$snap"
	elif [ -n "$(header_field "$brief" kitchen)" ]; then
		set_header "$brief" kitchen repo
	fi
	brief_kitchen "$brief"
	json="$(kpy "$store" "${kitchen_cfg[@]}" --json classify --paths "${globs[@]}" "${esc[@]}")" || die "kitchen.py classify failed; run kitchen.py validate in the repo"
	risk="$(jq -r .risk <<<"$json")"
	# The master may have written a reason after the class (`escalated (contract change)`): rewrite the line only when the class changes.
	[ "$(risk_of "$brief")" = "$risk" ] || set_header "$brief" risk "$risk"
	set_header "$brief" profiles "$(jq -r '.profiles | join(", ") | if . == "" then "none" else . end' <<<"$json")"
	set_header "$brief" verify "$(jq -r '"\(.verify.mode) by \(.verify.kind) verifier"' <<<"$json")"
	printf 'risk: %s\n' "$risk"
	kpy "$store" "${kitchen_cfg[@]}" lessons stamp "$brief" || die "kitchen.py lessons stamp failed; run kitchen.py lessons in the repo"
	jq -r '"profiles: \(.profiles | join(", "))", "verify: \(.verify.mode) by \(.verify.kind) verifier", (.escalate[] | "escalate: \(.)"), (.warnings[]? | "warn: \(.)"),
		(if .bookkeeping then "bookkeeping: the Scope is outside the repo; gates only, nothing to escalate" else empty end)' <<<"$json"
	if [ "$risk" = escalated ] && [ "$(agent_status "$(field "$store" .consultant.name)")" = absent ]; then
		printf 'next: kitchen.sh consultant %s --reason "%s", then a plan round with it\n' "$store" "$(jq -r '.escalate[0]' <<<"$json")"
	fi
}

cmd_classify() {
	[ $# -eq 2 ] || die "usage: kitchen.sh classify <store> <brief-path>"
	pair_file "$1" >/dev/null
	[ -f "$2" ] || die "brief not found: $2"
	classify_brief "$1" "$2"
}

is_landing() {
	case "$(header_field "$1" playbook)" in
	opening-a-pr | shipping) return 0 ;;
	esac
	[ "$(header_field "$1" landing)" = yes ]
}

# A routine check-in is a wake with nothing to act on, so a kitchen wait
# stays quiet through it: it returns for a report, a steps wake, a blocked
# sidekick, or a check-in that carries a flag (a stale log, a write outside
# Scope, an objection, an open blocking note, a timebox overrun, a pause),
# or after --max minutes (KITCHEN_QUIET_MAX_S; default an hour) with the
# latest digest. Run it in the harness's background mode where there is one.
# KITCHEN_QUIET_CLOCK, a file holding epoch seconds, replaces the wall clock
# for that --max check; tests move it at a fixed point instead of waiting.
quiet_now() {
	if [ -n "${KITCHEN_QUIET_CLOCK:-}" ] && [ -r "$KITCHEN_QUIET_CLOCK" ]; then cat "$KITCHEN_QUIET_CLOCK"; else date +%s; fi
}

quiet_flagged() {
	grep -qE 'STALE|^outside scope: [1-9]|[1-9][0-9]* objected|[1-9][0-9]* blocking notes open|^paused:' <<<"$1" && return 0
	local spent box
	read -r spent box < <(sed -nE 's/^check-in: .*elapsed ([0-9]+)m of ([0-9]+)m.*/\1 \2/p' <<<"$1") || true
	[ -n "${spent:-}" ] && [ -n "${box:-}" ] && [ "$spent" -gt "$box" ]
}

# The one wake row for a wait or dispatch that returns on a check-in: the pair
# core logs none of its own under PAIR_QUIET_WAIT. $1 store, $2 the output.
log_checkin_wake() {
	event "$1" master wake "$(field "$1" '.dispatch.brief // empty')" \
		"$(grep -q 'written but not ready' <<<"$2" && echo report:unready || echo checkin)"
}

quiet_wait() {
	local store="$1" max="${KITCHEN_QUIET_MAX_S:-3600}" start out rc
	shift
	local -a args=()
	while [ $# -gt 0 ]; do
		case "$1" in
		--max) max=$(( $2 * 60 )); shift 2 ;;
		*) args+=("$1"); shift ;;
		esac
	done
	start="$(quiet_now)"
	# One wait owns the store under this shell's pid: the first inner wait claims it, the later ones only check it. A claim that could not be
	# made (the inner wait says `unowned:`) leaves the rest of this run unowned: its later waits must not read the missing claim as a newer owner.
	local claim=1 unowned=0
	while :; do
		rc=0
		out="$(PAIR_WAIT_OWNER=$$ PAIR_WAIT_CLAIM=$claim PAIR_WAIT_UNOWNED=$unowned PAIR_QUIET_WAIT=1 core_wait "$store" "${args[@]}")" || rc=$?
		claim=0
		if grep -q '^unowned:' <<<"$out"; then
			unowned=1
			out="$(grep -v '^unowned:' <<<"$out" || true)"
		fi
		if [ "$rc" -eq 4 ] && grep -q '^check-in:' <<<"$out" && ! quiet_flagged "$out" && [ $(( $(quiet_now) - start )) -lt "$max" ]; then
			sleep "${KITCHEN_QUIET_POLL_S:-1}"
			continue
		fi
		[ "$rc" -ne 4 ] || ! grep -q '^check-in:' <<<"$out" || quiet_flagged "$out" ||
			printf 'quiet: %sm with nothing flagged; the sidekick works on\n' "$(( ($(quiet_now) - start) / 60 ))"
		if [ "$rc" -eq 4 ] && grep -q '^check-in:' <<<"$out"; then log_checkin_wake "$store" "$out"; fi
		printf '%s\n' "$out"
		# in a substitution: core_wait is `pycore --exec`, which replaces the shell it runs in, so outside $( ) it would replace this one and
		# skip the exit "$rc" below
		: "$(PAIR_WAIT_OWNER=$$ core_wait "$store" --release 2>&1)" || true
		exit "$rc"
	done
}

cmd_wait() {
	[ $# -ge 1 ] || die "usage: kitchen.sh wait <store> [--timeout MS | --every MIN] [--max MIN]"
	quiet_wait "$@"
}

# A sidekick that starts fresh per brief (pi, prime-agent, devin) is rotated before the
# next brief or plan once its last unit is done, so the rotation costs the
# master no extra wake. Partial or blocked work never sets rotation_required
# and keeps its session. $1 store, $2 what comes next (brief or plan).
rotate_if_due() {
	local out rc=0
	fresh_per_brief "$(field "$1" .sidekick.kind)" && [ "$(field "$1" '.sidekick.rotation_required // false')" = true ] || return 0
	# The report lands a moment before the sidekick's turn ends.
	herdr agent wait "$(field "$1" .sidekick.name)" --until idle --until "done" --timeout 60000 >/dev/null 2>&1 || true
	out="$(cmd_rotate "$1" 2>&1)" || rc=$?
	[ "$rc" -eq 0 ] || die "rotating the sidekick before this $2 failed:
$out" "$rc"
	printf 'rotated: the %s sidekick starts this %s in a fresh session\n' "$(field "$1" .sidekick.kind)" "$2"
}

cmd_dispatch() {
	[ $# -ge 2 ] || die "usage: kitchen.sh dispatch <store> <brief-path> [--timeout MS | --every MIN] [--max MIN]"
	# A brief path may be given relative to the store (briefs/NNN-x.md).
	if [ ! -f "$2" ] && [ -f "$1/$2" ]; then set -- "$1" "$1/$2" "${@:3}"; fi
	[ -f "$2" ] || die "brief not found: $2"
	if is_landing "$2"; then
		pycore land-check "$1" >/dev/null || die "landing refused: kitchen.sh land-check $1 fails" 2
	else
		classify_brief "$1" "$2"
	fi
	local -a waitargs=() sendargs=()
	local a rc=0 out
	rotate_if_due "$1" brief
	for a in "${@:3}"; do waitargs+=("$a"); done
	# --max belongs to the quiet wait, not to the pair's dispatch.
	set -- "$1" "$2"
	local i=0
	while [ "$i" -lt "${#waitargs[@]}" ]; do
		if [ "${waitargs[$i]}" = --max ]; then i=$((i + 2)); else sendargs+=("${waitargs[$i]}"); i=$((i + 1)); fi
	done
	out="$(PAIR_QUIET_WAIT=1 core_dispatch "$@" "${sendargs[@]}")" || rc=$?
	if [ "$rc" -eq 4 ] && grep -q '^check-in:' <<<"$out"; then
		if ! quiet_flagged "$out"; then
			printf '%s\n' "$(grep -v -e '^check-in:' -e '^progress:' -e '^touched:' -e '^  ' -e '^steers:' -e '^steps:' -e '^report: missing' <<<"$out")"
			quiet_wait "$1" "${waitargs[@]}"
		fi
		log_checkin_wake "$1" "$out"
	fi
	printf '%s\n' "$out"
	exit "$rc"
}

cmd_queue() {
	# A brief path may be given relative to the store (briefs/NNN-x.md), as for dispatch.
	if [ $# -ge 2 ] && [ "$2" != --clear ] && [ ! -f "$2" ] && [ -f "$1/$2" ]; then set -- "$1" "$1/$2" "${@:3}"; fi
	if [ $# -ge 2 ] && [ "$2" != --clear ] && [ -f "$2" ] && ! is_landing "$2"; then
		classify_brief "$1" "$2" >/dev/null
	fi
	core_queue "$@"
}

cmd_discuss() {
	[ $# -ge 2 ] || die "usage: kitchen.sh discuss <store> <plan-path> [--timeout MS]"
	[ -f "$2" ] || die "plan not found: $2"
	case "$(risk_of "$2")" in
	routine | escalated) ;;
	*) die "$2: risk: must start with routine or escalated, as kitchen.sh classify prints it" 2 ;;
	esac
	if [ "$(risk_of "$2")" = escalated ]; then
		[ "$(agent_status "$(field "$1" .consultant.name)")" != absent ] || die "an escalated plan goes to the consultant too; start it: kitchen.sh consultant $1 --reason TEXT" 5
	fi
	rotate_if_due "$1" plan
	if [ "$(risk_of "$2")" = escalated ]; then
		PAIR_ONE_WAKE=1 discuss_with_consultant "$@"
	else
		core_discuss "$@"
	fi
}

# A step is recorded only once the touched profiles' fast gates and the
# policy check pass on it. Two failures in a row are an exception for the
# master, reported as blocked. The checks run detached and step returns
# within KITCHEN_STEP_SLICE_S seconds (default 100): an agent harness moves a
# longer command to the background and can sit idle on it, so a step still
# checking says so, and running the same step command again waits on.
cmd_step() {
	[ $# -ge 3 ] || die "usage: kitchen.sh step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]"
	local store="$1" sha="$2" brief root base full json p fails file unit job slice run
	pair_file "$store" >/dev/null
	brief="$(field "$store" '.dispatch.brief // empty')"
	[ -n "$brief" ] || die "no brief dispatched; a step belongs to a running brief"
	unit="$(basename "$brief" .md)"
	root="$(field "$store" .git_root)"
	full="$(git -C "$root" rev-parse --verify --quiet "$sha^{commit}")" || die "not a commit: $sha; commit the step first"
	file="$(steps_file "$store" "$brief")"
	# A SHA this unit already recorded proves nothing new: no gates, no job directory touched, no row, no event (the core step only merges
	# --resolves into the row it has). It is looked up first, so an earlier commit can be re-recorded whatever HEAD and the tree are now.
	if [ -s "$file" ] && cut -f2 "$file" | grep -qxF "$full"; then
		core_step "$@"
		return
	fi
	# The gates run on the checkout, so they prove HEAD: a step names the commit they ran on, and a tracked file edited since would be proven too.
	[ "$(git -C "$root" rev-parse HEAD)" = "$full" ] || die "step $sha: HEAD is $(git -C "$root" rev-parse --short HEAD); commit the step and record HEAD"
	[ -z "$(git -C "$root" status --porcelain --untracked-files=no)" ] || die "step $sha: the tree has uncommitted changes to tracked files; commit or stash them first"
	base="$( { [ -s "$file" ] && tail -1 "$file" | cut -f2; } || jq -r --arg u "$unit" '.heads[$u] // empty' "$store/pair.json")"
	[ -n "$base" ] || die "no base for $unit; was it dispatched?"
	# A commit made before this brief was dispatched (a resumed unit) is the
	# brief's own base, which leaves an empty range; check its own diff.
	if [ "$(git -C "$root" rev-list --count "$base..$full" 2>/dev/null || printf 0)" -eq 0 ]; then
		base="$(git -C "$root" rev-parse --verify --quiet "$full~1")" || die "$sha has no parent to check it against"
	fi
	# A rewritten base (a squash, an amend) is measured from where the
	# histories fork, so the step covers every change it carries.
	base="$(git -C "$root" merge-base "$base" "$full" 2>/dev/null || printf '%s' "$base")"
	# And never from before where the branch leaves trunk (a unit rebased onto
	# a moved trunk, with no step recorded yet).
	range_floor "$root" "$base" "$full"
	base="$range_floor_rev"
	job="$store/steps/jobs/$unit-${full:0:12}"
	if [ ! -d "$job" ] || [ -f "$job/consumed" ]; then
		# every run of the gates has its own log (out.1, out.2, ...), and `out` points at the latest: a path an event named keeps its run's text
		rm -rf "$job/rc" "$job/consumed" "$job/classify.json" "$job/profiles" "$job/base"
		mkdir -p "$job"
		run=$(( $(find "$job" -maxdepth 1 -name 'out.[0-9]*' | wc -l) + 1 ))
		ln -sfn "out.$run" "$job/out"
		brief_kitchen "$brief"
		json="$(kpy "$store" "${kitchen_cfg[@]}" --json classify --base "$base" --head "$full")"
		printf '%s\n' "$json" >"$job/classify.json"
		jq -r '.profiles[]' <<<"$json" >"$job/profiles"
		printf '%s\n' "$base" >"$job/base"
		# shellcheck disable=SC2016
		PSTACK_KITCHEN_RUN="$(field "$store" .slug)" detached bash -c '
			cd "$1" || exit 1
			rc=0
			while IFS= read -r p; do
				[ -n "$p" ] || continue
				python3 "$2" --repo "$1" ${6:+--config "$6"} gate "$p" fast --role sidekick --base "$4" || rc=2
			done <"$3/profiles"
			python3 "$2" --repo "$1" ${6:+--config "$6"} policy --base "$4" --head "$5" || rc=2
			printf "%s\n" "$rc" >"$3/rc"
		' kitchen-step "$root" "$here/kitchen.py" "$job" "$base" "$full" "${kitchen_cfg[1]:-}" >"$job/out.$run" 2>&1 </dev/null &
		disown 2>/dev/null || true
	fi
	slice="${KITCHEN_STEP_SLICE_S:-100}"
	local deadline=$(( $(date +%s) + slice ))
	until [ -f "$job/rc" ]; do
		if [ "$(date +%s)" -ge "$deadline" ]; then
			printf 'gates: still running for %s (%ss so far; log: %s)\n' "${full:0:9}" "$(( $(date +%s) - $(file_mtime "$job/base") ))" "$job/out"
			# A gate still waiting for a machine slot, or the heavy slot, says where it stands.
			grep -E '^kitchen: (queued|waited) ' "$job/out" 2>/dev/null | tail -1 |
				sed -n -e '/^kitchen: queued .* heavy slots\{0,1\}$/{s/^kitchen: \(queued .*\)/gates: \1, behind heavy gates from other kitchens/p;b' -e '}' \
					-e 's/^kitchen: \(queued .*\)/gates: \1, behind gates from other kitchens/p' || true
			printf 'next: this step is not recorded yet; do not end your turn. Run the same command again now, in the foreground, until it records: kitchen.sh step %s %s "%s"\n' "$store" "$sha" "$3"
			exit 4
		fi
		sleep "${KITCHEN_STEP_POLL_S:-2}"
	done
	: >"$job/consumed"
	local rc state
	rc="$(cat "$job/rc")"
	json="$(cat "$job/classify.json")"
	local -a profiles
	mapfile -t profiles <"$job/profiles"
	# A step with no profile ran no gate: it is recorded as none, never pass.
	if [ "$rc" -ne 0 ]; then state=fail
	elif [ "${#profiles[@]}" -eq 0 ]; then state=none
	else state=pass; fi
	mkdir -p "$store/steps"
	printf '%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$unit" "${full:0:9}" "$state" "$(IFS=,; printf '%s' "${profiles[*]}")" >>"$store/gates.tsv"
	if [ "$rc" -ne 0 ]; then
		cat "$job/out"
		event "$store" sidekick gate-fail "$brief" "${full:0:9} $job/$(readlink "$job/out")"
		fails="$(awk -F '\t' -v u="$unit" '$2 == u {n = ($4 == "fail") ? n + 1 : 0} END {print n + 0}' "$store/gates.tsv")"
		if [ "$fails" -ge 2 ]; then
			printf 'next: %s failed checks in a row; write the report as blocked with the logs under Questions, then kitchen.sh finish\n' "$fails"
		else
			printf 'next: fix it in a new commit and run kitchen.sh step %s <new-sha> "%s" again\n' "$store" "$3"
		fi
		exit 2
	fi
	if [ "$state" = none ]; then
		printf 'gates: none ran; no profile covers %s\n' "$(jq -r '.unmapped | join(", ") | if . == "" then "these changes" else . end' <<<"$json")"
	else
		printf 'gates: pass (%s)\n' "$(IFS=,; printf '%s' "${profiles[*]}")"
		# A pass that needed a rerun under load is recorded, so retro shows
		# which suites flake on a loaded machine.
		local flaky
		flaky="$(sed -n 's/^flaky under load: \([^ ]*\) .*/\1/p' "$job/out" | sort -u | paste -sd, -)"
		if [ -n "$flaky" ]; then
			printf 'flaky under load: %s failed, then passed on a rerun (see %s)\n' "$flaky" "$job/out"
			event "$store" sidekick gate-flaky "$brief" "$flaky"
		fi
	fi
	core_step "$@"
}

# Where finish keeps the run of $2's Verify block at commit $3: one per head
# and block, so an amended block runs again.
verify_job() {
	printf '%s/steps/jobs/%s-verify-%s-%s\n' "$1" "$(basename "$2" .md)" "${3:0:12}" \
		"$(awk '/^## Verify/{p=1; next} /^## /{p=0} p' "$2" | cksum | cut -d' ' -f1)"
}

# A done report reaches the master only once the brief's Verify commands,
# rerun by the kitchen exactly as written at the unit's tip, all exit 0: a
# sidekick's word that they pass is not evidence. The tip is the checkout,
# which must sit at the report's head: and, for a brief that commits, hold
# nothing uncommitted. The commands run detached like a step's gates (exit 4
# "still running", and the same finish waits on); a pass is kept for that
# head and Verify block, and a failure reruns on the next finish.
cmd_finish() {
	in_herdr
	[ $# -eq 2 ] || core_finish "$@"
	local store="$1" report brief root head cur dirty unit base job slice rc
	report="$(readlink -f "$2")"
	pair_file "$store" >/dev/null
	brief="$(field "$store" '.dispatch.brief // empty')"
	# Everything but the dispatched unit's done report, and a report the
	# pair's checks refuse anyway (no head:, open notes), goes straight on.
	if [ ! -f "$report" ] || [ "$(header_field "$report" status)" != "done" ] || [ -z "$brief" ] ||
		[ "$report" != "$(expected_report "$store" "$brief")" ] || ! pycore __pair_report_ready "$store" "$report"; then
		core_finish "$@"
		return
	fi
	case "$(header_field "$report" head)" in "" | "<"*) core_finish "$@"; return ;; esac
	root="$(field "$store" .git_root)"
	head="$(git -C "$root" rev-parse --verify --quiet "$(header_field "$report" head)^{commit}")" ||
		die "$(basename "$report"): head: $(header_field "$report" head) is not a commit; name the unit's last commit and finish again"
	cur="$(git -C "$root" rev-parse HEAD)"
	[ "$cur" = "$head" ] || die "the checkout is at ${cur:0:9}, not the report's head: ${head:0:9}; the kitchen reruns the brief's Verify commands at the unit's tip, so check out that head (or fix head:) and finish again"
	case "$(header_field "$brief" commit)" in
	no*) ;;
	*)
		dirty="$(git -C "$root" status --porcelain --untracked-files=normal)"
		[ -z "$dirty" ] || die "the checkout holds work the unit's head does not:
$dirty
The kitchen reruns the brief's Verify commands at the unit's tip. Commit this as a step (kitchen.sh step) and name it in head:, or remove it, then finish again"
		;;
	esac
	unit="$(basename "$brief" .md)"
	job="$(verify_job "$store" "$brief" "$head")"
	if [ -f "$job/rc" ] && [ "$(cat "$job/rc")" != 0 ] && [ -f "$job/consumed" ]; then rm -rf "$job"; fi
	if [ ! -d "$job" ]; then
		mkdir -p "$job"
		brief_kitchen "$brief"
		base="$(jq -r --arg u "$unit" '.heads[$u] // empty' "$store/pair.json")"
		# shellcheck disable=SC2016
		PSTACK_KITCHEN_RUN="$(field "$store" .slug)" detached bash -c '
			cd "$1" || exit 1
			rc=0
			python3 "$2" --repo "$1" ${5:+--config "$5"} brief-verify "$3" --role sidekick --log-dir "$4/logs" ${6:+--base "$6"} || rc=$?
			printf "%s\n" "$rc" >"$4/rc"
		' kitchen-finish "$root" "$here/kitchen.py" "$brief" "$job" "${kitchen_cfg[1]:-}" "$base" >"$job/out" 2>&1 </dev/null &
		disown 2>/dev/null || true
	fi
	slice="${KITCHEN_STEP_SLICE_S:-100}"
	local deadline=$(( $(date +%s) + slice ))
	until [ -f "$job/rc" ]; do
		if [ "$(date +%s)" -ge "$deadline" ]; then
			printf 'verify: the brief'"'"'s Verify commands are still running at %s (log: %s)\n' "${head:0:9}" "$job/out"
			printf 'next: the report is not sent yet; do not end your turn. Run the same command again now, in the foreground, until it sends: kitchen.sh finish %s %s\n' "$store" "$2"
			exit 4
		fi
		sleep "${KITCHEN_STEP_POLL_S:-2}"
	done
	: >"$job/consumed"
	rc="$(cat "$job/rc")"
	cat "$job/out"
	if [ "$rc" -ne 0 ]; then
		event "$store" sidekick finish-verify-fail "$brief" "${head:0:9}"
		printf 'next: the master did not get this report. Fix what failed in a new commit, record it with kitchen.sh step, name it in head:, and finish again; if the commands cannot pass, write the report as blocked with this output under Questions, then kitchen.sh finish\n'
		exit 2
	fi
	event "$store" sidekick finish-verify-pass "$brief" "${head:0:9}"
	core_finish "$@"
}

# verify starts a verification and waits for it in slices of --every minutes, so one call never outlasts a master's shell limit; the verifier
# keeps working between calls, and `verify <store> --wait` picks it up. The open verification lives in pair.json .verifying, one at a time.
cmd_verify() { HOSTNAME="${HOSTNAME:-localhost}" pycore --exec verify "$@"; }

cmd_revise() { pycore revise "$@"; }

cmd_land_check() { pycore land-check "$@"; }

cmd_catch() { pycore catch "$@"; }

# The run's counts, read-only and from the same function fleet uses: sets
# the rc_* globals. retro prints them and feedback quotes the same line.


# Where the master was needed, and what repeated. Each repeated class is a
# candidate for the kitchen: a rule, a lint, a profile command, a sharper
# brief. Appends this run's classes to the repo's ledger, then counts across
# runs.
# A finished run's agents hold their memory until someone closes them (about
# a gigabyte per run on this host, idle for days). The master asks the human
# once after the retro; this is the yes.
cmd_close() { pycore close "$@"; }

# The master's own session grows with every diff and file it reads, and
# every model call re-reads all of it: on this host a master that served
# several runs re-read 500-900k tokens per call, at 27-31 calls per unit. A
# handoff lets the master end its session at a unit boundary and start a
# fresh one in the same pane, which reads the handoff and the store instead
# of the history. new-handoff writes the template with the units' state
# filled in; rotate-master records the filled handoff and the session it
# closes, then hands the run to a new master beside it.
cmd_new_handoff() { pycore new-handoff "$@"; }

# One row per unit: brief, report status, latest verdict, review verdict.

# The --model the master's own Claude was started with, so the next
# generation runs the same model; empty when none is on its command line.

# A hand-over, not a restart in place: the closing master starts the next
# generation in a pane beside its own and points pair.json at it, and the new
# master's first command, take-master, closes the old pane. The old session
# never has to exit itself (a Claude with background tasks answers /exit with
# a dialog), and a start that fails leaves the old master in charge.
cmd_rotate_master() { pycore rotate-master "$@"; }

# The new master's first command after a hand-over: give the old master a
# moment to end its turn, close its pane (and with it any background waits it
# left running), and take back the run's master name. Safe to repeat.
cmd_take_master() { pycore take-master "$@"; }

cmd_retro() { pycore retro "$@"; }

# The host's feedback inbox: gaps in the kitchen itself, filed by masters
# and read by whoever maintains pstack on this host.

# One short lock around allocating an id and moving a report between
# directories, so concurrent filings and closures never share a name.


# Why the registered maintainer cannot take a pointer, or nothing when it can.

# Open reports, oldest first: id, run, repo, build, gaps, age.

# Move a report to done/ with a Closed block. Closing a closed id prints its
# block and succeeds.

# File a report: the master's text behind a header this command writes.
cmd_feedback() { pycore feedback "$@"; }

# Every run on the host and each skills build; read-only and repo-free.
cmd_fleet() {
	python3 "$here/kitchen.py" fleet "$@"
}

cmd_maintainer() { pycore maintainer "$@"; }

if [ $# -ge 2 ] && [ -f "$2/pair.json" ]; then record_skill_build "$2"; fi
pair_main "$@"
