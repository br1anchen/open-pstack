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

unit_brief() {
	# $1 store, $2 NNN: the unit's brief path.
	local f
	f="$(ls "$1"/briefs/"$2"-*.md 2>/dev/null | head -1 || true)"
	[ -n "$f" ] || die "no brief for unit $2"
	printf '%s\n' "$f"
}

# Where a stack at $2 leaves trunk (or at the parent recorded in store $3), in $landing_base_rev; call it directly,
# not in a command substitution, so its refusals end the command.
landing_base() {
	local root="$1" head="$2" store="$3" trunk parent label pc
	# A parent given to this call, else the one recorded by an earlier verify --landing --parent, is where the
	# stack starts, while it is an ancestor of the head being verified.
	parent="${landing_parent_override:-}"
	label="${landing_parent_label:-}"
	if [ -z "$parent" ] && [ "$label" != none ]; then
		parent="$(jq -r '.landing.parent // empty' "$store/pair.json" 2>/dev/null || true)"
		label="$(jq -r '.landing.parent_ref // empty' "$store/pair.json" 2>/dev/null || true)"
	fi
	if [ -n "$parent" ] && [ -z "${range_base:-}" ]; then
		pc="$(git -C "$root" rev-parse --verify --quiet "$parent^{commit}" 2>/dev/null || true)"
		if [ -n "$pc" ] && [ "$pc" != "$head" ] && git -C "$root" merge-base --is-ancestor "$pc" "$head" 2>/dev/null; then
			landing_base_rev="$pc"
			printf 'landing base: recorded parent %s (%s)\n' "$label" "${pc:0:9}"
			return 0
		fi
		[ -z "${landing_parent_override:-}" ] || die "--parent $label (${parent:0:9}) must be an ancestor of ${head:0:9} and not the same commit; name where the stack starts"
		printf 'landing base: recorded parent %s (%s) is no longer an ancestor of %s; using the trunk merge-base\n' "$label" "${parent:0:9}" "${head:0:9}"
	fi
	trunk="$(trunk_ref "$root")"
	[ -n "$trunk" ] || die "cannot tell this repo's trunk; pass --base <trunk ref or commit>"
	landing_base_rev="$(git -C "$root" merge-base "$trunk" "$head")" || die "no merge-base between $trunk and HEAD; pass --base"
	[ "$landing_base_rev" != "$head" ] || die "HEAD is already on ${trunk#refs/}, so the landing range is empty; pass --base <where the stack starts>"
	printf 'landing base: merge-base of %s and HEAD (%s)\n' "${trunk#refs/}" "${landing_base_rev:0:9}"
}

unit_range() {
	# $1 store, rest: NNN...; prints "base head" for the units, first to
	# last, from their dispatch heads and done reports. A unit named in the
	# global covered list needs no done report: a later fix unit carries it.
	local store="$1" first="$2" last="${*: -1}" brief report base head unit
	brief="$(unit_brief "$store" "$first")"
	base="$(jq -r --arg u "$(basename "$brief" .md)" '.heads[$u] // empty' "$store/pair.json")"
	[ -n "$base" ] || die "unit $first was never dispatched"
	# A partial or blocked unit can still be verified or reviewed at the head
	# its report names; the master is told it is not a done unit.
	for unit in "${@:2}"; do
		[[ " ${covered[*]:-} " != *" $unit "* ]] || continue
		report="$(expected_report "$store" "$(unit_brief "$store" "$unit")")"
		[ -f "$report" ] || die "unit $unit has no report"
		case "$(header_field "$report" status)" in
		"done") ;;
		partial | blocked | failed) printf 'note: unit %s'"'"'s report is %s; checking the head it names\n' "$unit" "$(header_field "$report" status)" >&2 ;;
		"") die "unit $unit's report has no status:" ;;
		*) die "unit $unit's report is $(header_field "$report" status), not done, partial, blocked, or failed" ;;
		esac
	done
	head="$(header_field "$(expected_report "$store" "$(unit_brief "$store" "$last")")" head)"
	[ -n "$head" ] || die "unit $last's report has no head:"
	local root
	root="$(field "$store" .git_root)"
	head="$(git -C "$root" rev-parse --verify --quiet "$head^{commit}")" || die "unit $last's head is not a commit"
	if [ -n "${range_base:-}" ]; then
		base="$(git -C "$root" rev-parse --verify --quiet "$range_base^{commit}")" || die "--base $range_base is not a commit"
	else
		range_floor "$root" "$base" "$head"
		[ -z "$range_floor_note" ] || printf 'note: unit %s'"'"'s %s\n' "$first" "$range_floor_note" >&2
		base="$range_floor_rev"
	fi
	printf '%s %s\n' "$base" "$head"
}

# A unit's head: its report's head:, else its last recorded step; empty when
# neither names a commit. $1 store, $2 brief.
unit_head() {
	local root report file uhead=""
	root="$(field "$1" .git_root)"
	report="$(expected_report "$1" "$2")"
	[ ! -f "$report" ] || uhead="$(git -C "$root" rev-parse --verify --quiet "$(header_field "$report" head)^{commit}" 2>/dev/null || true)"
	if [ -z "$uhead" ]; then
		file="$(steps_file "$1" "$2")"
		[ ! -s "$file" ] || uhead="$(git -C "$root" rev-parse --verify --quiet "$(tail -1 "$file" | cut -f2)^{commit}" 2>/dev/null || true)"
	fi
	printf '%s' "$uhead"
}

# The patch-id of a commit's own change (or, with -R, of its inverse), from plumbing that ignores
# diff.noprefix, diff.external and textconv. $1 root, $2 commit; $3 -R is the inverse: the diff from
# the commit to its parent (not `diff-tree -R`, which swaps the a/ and b/ prefixes patch-id hashes).
# $4 -U0 is the same patch with no context lines, which a later edit near the change cannot alter.
commit_patch_id() {
	local -a context=()
	[ "${4:-}" != -U0 ] || context=(-U0)
	if [ "${3:-}" = -R ]; then
		git -C "$1" diff-tree -p --binary --full-index "${context[@]}" "$2" "$2^"
	else
		git -C "$1" diff-tree -p --binary --full-index "${context[@]}" "$2"
	fi | git patch-id --stable | cut -d' ' -f1
}

# The stack's patch-ids for <base>..<head> (merges left out), built once per call: stack_ids maps a
# patch-id to its commit, stack_index a commit to its place from the oldest, stack_commits lists them
# oldest first, and stack_id_of and stack_id0_of map a commit to its patch-id with the default context
# and with none (the revert scan reads the second). $1 root, $2 base, $3 head.
declare -A stack_ids=() stack_index=() stack_id_of=() stack_id0_of=()
declare -a stack_commits=()
stack_ids_range=""
stack_patch_ids() {
	local c id n=0
	[ "$stack_ids_range" != "$2..$3" ] || return 0
	stack_ids=() stack_index=() stack_id_of=() stack_id0_of=() stack_commits=()
	while read -r c; do
		id="$(commit_patch_id "$1" "$c")"
		stack_commits+=("$c")
		stack_index[$c]=$n
		stack_id_of[$c]="$id"
		stack_id0_of[$c]="$(commit_patch_id "$1" "$c" '' -U0)"
		[ -z "$id" ] || stack_ids[$id]="$c"
		n=$((n + 1))
	done < <(git -C "$1" rev-list --reverse --no-merges "$2..$3")
	stack_ids_range="$2..$3"
}

# Whether a unit rebased into a stack: every commit of the unit's range (its dispatch head to its
# head) has a patch-id among the commits of <stack base>..<stack head>, and no stack commit after the
# one matching the unit's head reverts the unit (has, with no context lines, the patch-id of the inverse
# of the unit's net change or of one of its commits: a commit that edits lines near the unit's change
# changes a revert's context but not its changes). Later edits of the unit's lines do not matter, a revert does. A
# range that holds a merge is not matched (its patch-ids are not stable across a rebase); a unit
# with commits but no net change is covered by its patch-ids alone. Sets rebased_sha to the stack
# commit matching the unit's head, rebased_why to merge or reverted, and rebased_by to the reverting
# commit. $1 store, $2 brief, $3 unit head, $4 stack base, $5 stack head.
unit_rebased_in() {
	local root unit_base c id tip_id="" ids=0 later inverted net i
	local -A undo=()
	root="$(field "$1" .git_root)"
	unit_base="$(jq -r --arg u "$(basename "$2" .md)" '.heads[$u] // empty' "$1/pair.json")"
	rebased_sha="" rebased_why="" rebased_by=""
	[ -n "$unit_base" ] && [ -n "$4" ] || return 1
	git -C "$root" rev-parse --verify --quiet "$unit_base^{commit}" >/dev/null || return 1
	if [ -n "$(git -C "$root" rev-list --merges "$unit_base..$3" | head -1)" ]; then
		rebased_why=merge
		return 1
	fi
	stack_patch_ids "$root" "$4" "$5"
	while read -r c; do
		id="$(commit_patch_id "$root" "$c")"
		[ -n "$id" ] || continue
		[ -n "${stack_ids[$id]:-}" ] || return 1
		ids=$((ids + 1))
		[ -n "$tip_id" ] || tip_id="$id"
		inverted="$(commit_patch_id "$root" "$c" -R -U0)"
		[ -z "$inverted" ] || undo[$inverted]=1
	done < <(git -C "$root" rev-list --no-merges "$unit_base..$3")
	[ "$ids" -gt 0 ] || return 1
	net="$(git -C "$root" diff-tree -p --binary --full-index -U0 "$3" "$unit_base" | git patch-id --stable | cut -d' ' -f1)"
	[ -z "$net" ] || undo[$net]=1
	rebased_sha="${stack_ids[$tip_id]}"
	for ((i = ${stack_index[$rebased_sha]} + 1; i < ${#stack_commits[@]}; i++)); do
		later="${stack_commits[i]}"
		if [ -n "${stack_id0_of[$later]}" ] && [ -n "${undo[${stack_id0_of[$later]}]:-}" ]; then
			rebased_why=reverted rebased_by="$later" rebased_sha=""
			return 1
		fi
	done
}

# Every unit this verification names must be in the head it checks out, the
# last unit's: a fix is named last (verify 015 --covers 010,011,013), or
# the earlier units' work would be missing from what the verifier proves. A unit
# is judged at its report's head, or for a covered unit with none, at its last
# recorded step; one with no usable head passes with a note.
unit_heads_in() {
	local store="$1" head="$2" last unit uhead brief root
	shift 2
	last="${*: -1}"
	root="$(field "$store" .git_root)"
	for unit in "$@"; do
		[ "$unit" != "$last" ] || continue
		brief="$(unit_brief "$store" "$unit")"
		uhead="$(unit_head "$store" "$brief")"
		if [ -z "$uhead" ]; then
			printf 'note: unit %s has no usable head; its inclusion was not checked\n' "$unit" >&2
			continue
		fi
		git -C "$root" merge-base --is-ancestor "$uhead" "$head" 2>/dev/null && continue
		if unit_rebased_in "$store" "$brief" "$uhead" "$(git -C "$root" merge-base "$(trunk_ref "$root")" "$head" 2>/dev/null || true)" "$head"; then
			printf 'note: unit %s'"'"'s head is not in %s; its commits match by patch-id (rebased as %s)\n' "$unit" "${head:0:9}" "${rebased_sha:0:9}" >&2
		elif [ "$rebased_why" = reverted ]; then
			die "unit $unit's commits match by patch-id but ${rebased_by:0:9} reverts them in the checked-out head ${head:0:9} (unit $last)" 2
		else
			die "unit $unit's head ${uhead:0:9} is not in the checked-out head ${head:0:9} (unit $last); name the unit that carries all the others last" 2
		fi
	done
}

next_index() {
	# $1 dir, $2 prefix, $3 letter: the next k for files <prefix>-<letter><k>.*
	local k=0 f n
	for f in "$1/$2-$3"[0-9]*; do
		[ -e "$f" ] || continue
		n="$(basename "$f" | sed -E "s/^.*-$3([0-9]+).*$/\1/")"
		[ "$n" -gt "$k" ] && k="$n"
	done
	printf '%s\n' "$((k + 1))"
}

# The repo's kitchen speaks through the standing orders too: the review
# skills the sidekick runs before done, and how far landing goes.
# The flake rev that built a skills directory, from the switch's builds.tsv
# ("unknown" for a path no switch recorded, such as a working checkout).
build_rev() {
	local rev
	rev="$(awk -F '\t' -v p="$1" '$3 == p {r = $2} END {print r}' "${XDG_STATE_HOME:-$HOME/.local/state}/pstack/builds.tsv" 2>/dev/null || true)"
	printf '%s\n' "${rev:-unknown}"
}

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
		land_check "$1" >/dev/null || die "landing refused: kitchen.sh land-check $1 fails" 2
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

verdict_problem() {
	# $1 verdict: empty when it is well formed, else what is wrong.
	case "$(header_field "$1" status)" in
	clean | reject | inconclusive) ;;
	*) printf 'status is not clean, reject, or inconclusive'; return ;;
	esac
	awk '/^## Evidence/{e=1; next} /^## /{e=0} e && /^```/{n++} END{exit !(n >= 2)}' "$1" || printf 'no command output under Evidence'
}

audit_due() {
	# $1 unit id, $2 sample rate: a stable draw per unit.
	local h
	h="$(printf '%s' "$1" | cksum | cut -d' ' -f1)"
	awk -v h="$h" -v s="$2" 'BEGIN{exit !((h % 1000) < s * 1000)}'
}

# A code fence for $1: three backticks, or one more than the longest run of them in the text.
fence_for() {
	local longest
	longest="$(printf '%s' "$1" | grep -o '`\+' | awk '{ if (length($0) > m) m = length($0) } END { print m + 0 }')"
	[ "$longest" -ge 3 ] || longest=2
	printf '%*s' "$((longest + 1))" '' | tr ' ' '`'
}

# Whether command text $1 names path $2 as a whole path: after a leading ~/, $HOME/ or ${HOME}/ is
# spelled out, $2 appears not inside a longer path and is followed by /, space, a quote, ), ;, &, | or the end.
names_path() {
	local text="$1" path="$2" quoted
	text="$(printf '%s' "$text" | sed -E "s#(^|[[:space:]=:\"'(])(~|\\\$HOME|\\\$\\{HOME\\}|\"\\\$HOME\"|\"\\\$\\{HOME\\}\")/#\\1$HOME/#g")"
	quoted="$(printf '%s' "$path" | sed 's/[][\.*^$+?(){}|]/\\&/g')"
	printf '%s\n' "$text" | grep -qE "(^|[^A-Za-z0-9_./-])${quoted}(/|[[:space:]\"'\`);&|]|\$)"
}

# The brief's Verify commands for a verifier's packet, each in a block of its own, run from the
# scratch root. A command that ends in `# master-only` (on its first or last line: a heredoc is
# marked on its first) writes the store, and one that names the main checkout or the store (in any
# spelling: absolute, ~/, $HOME/) cannot run in the scratch; both are listed apart, in full, each with
# its reason. A Verify block the kitchen refuses is said so in the packet and warned of, not skipped.
# $1 store, $2 brief, $3 unit.
brief_commands() {
	local list rc=0 root store="$1" n=0 c first last why fence apart=""
	root="$(field "$store" .git_root)"
	list="$(kpy "$store" --json brief-verify "$2" --list 2>&1)" || rc=$?
	if [ "$rc" -ne 0 ]; then
		list="$(head -1 <<<"$list")"
		printf 'Brief commands: none (brief-verify: %s)\n\n' "$list"
		printf 'warning: unit %s: brief-verify: %s\n' "${3:-?}" "$list" >&2
		return 0
	fi
	while IFS= read -r c; do
		[ -n "$c" ] || continue
		c="$(jq -r . <<<"$c")"
		first="$(head -1 <<<"$c")"
		last="$(tail -1 <<<"$c")"
		fence="$(fence_for "$c")"
		if [[ "$first" =~ \#\ master-only[[:space:]]*$ ]] || [[ "$last" =~ \#\ master-only[[:space:]]*$ ]]; then
			why="master-only"
		elif names_path "$c" "$root" || names_path "$c" "$store"; then
			why="absolute path"
		else
			n=$((n + 1))
			[ "$n" -ne 1 ] || printf 'Brief commands:\n\nRun each command in a fresh shell from the scratch root.\n\n'
			printf 'Command %s:\n\n%sbash\n%s\n%s\n\n' "$n" "$fence" "$c" "$fence"
			continue
		fi
		apart+="$(printf '(%s)\n%sbash\n%s\n%s\n\n' "$why" "$fence" "$c" "$fence")"$'\n\n'
	done < <(jq -c '.commands[]?' <<<"$list" 2>/dev/null)
	[ -z "$apart" ] || printf 'not for the verifier:\n\n%s' "$apart"
}

# Whether a unit is covered by a landing range: its head is in base..head (an ancestor of the head and not
# of the base), or its commits were rebased into it (unit_rebased_in). Sets rebased_sha to the stack commit
# for a rebased unit and to nothing otherwise. $1 store, $2 brief, $3 unit head, $4 base, $5 head.
unit_in_landing_range() {
	local root
	root="$(field "$1" .git_root)"
	rebased_sha="" rebased_why="" rebased_by=""
	[ -n "$3" ] || return 1
	if git -C "$root" merge-base --is-ancestor "$3" "$5" 2>/dev/null; then
		! git -C "$root" merge-base --is-ancestor "$3" "$4" 2>/dev/null
		return
	fi
	unit_rebased_in "$1" "$2" "$3" "$4" "$5"
}

# The done unit briefs a landing range holds: those whose head is in base..head, or whose commits
# were rebased into it (the test land-check's `covered:` uses). A unit landed before the base is out.
# $1 store, $2 base, $3 head.
landing_covered_briefs() {
	local b
	for b in "$1"/briefs/[0-9][0-9][0-9]-*.md; do
		[ -e "$b" ] && [ "$(header_field "$(expected_report "$1" "$b")" status 2>/dev/null)" = "done" ] || continue
		is_landing "$b" && continue
		unit_in_landing_range "$1" "$b" "$(unit_head "$1" "$b")" "$2" "$3" || continue
		printf '%s\n' "$b"
	done
}

# verify starts a verification and waits for it in slices of --every
# minutes, so one call never outlasts a master's shell limit; the verifier
# keeps working between calls, and `verify <store> --wait` picks it up. The
# open verification lives in pair.json .verifying, one at a time.
cmd_verify() {
	in_herdr
	[ $# -ge 2 ] || die "usage: kitchen.sh verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND] | kitchen.sh verify <store> --landing [--base REV] [--parent REF|none] [--head REV] [...] | kitchen.sh verify <store> --wait [--every MIN | --until-verdict] | kitchen.sh verify <store> --cancel"
	local store="$1" every_set=0 until_set=0 parent_ref="" land_head="" timeout_m="" every_m=9 wait=0 cancel=0 units=() kind_override="" landing=0 retry_entry="" u
	local -a cov=()
	range_base=""
	local -a argv=("${@:2}")
	covered=()
	shift
	while [ $# -gt 0 ]; do
		case "$1" in
		--timeout) timeout_m="$2"; shift 2 ;;
		--kind) kind_override="$2"; shift 2 ;;
		--retry-entry) retry_entry="$2"; shift 2 ;;
		--every) every_m="$2"; every_set=1; shift 2 ;;
		--until-verdict) every_m=525600; until_set=1; shift ;;
		--covers)
			# One unit per flag, or a comma list: --covers 010,011,013.
			[ $# -ge 2 ] || die "--covers takes a unit NNN or a comma list"
			IFS=, read -ra cov <<<"$2"
			for u in "${cov[@]}"; do
				[[ "$u" =~ ^[0-9]{3}$ ]] || die "--covers takes unit numbers (NNN), one per flag or a comma list, not '$u'"
				covered+=("$u")
			done
			shift 2 ;;
		--base) range_base="$2"; shift 2 ;;
		--landing) landing=1; shift ;;
		--parent) parent_ref="$2"; shift 2 ;;
		--head) land_head="$2"; shift 2 ;;
		--wait) wait=1; shift ;;
		--cancel) cancel=1; shift ;;
		[0-9][0-9][0-9]) units+=("$1"); shift ;;
		*) die "unknown option $1" ;;
		esac
	done
	[ "$every_set" -eq 0 ] || [ "$until_set" -eq 0 ] || die "--every and --until-verdict are alternatives"
	pair_file "$store" >/dev/null
	if [ "$cancel" -eq 1 ]; then
		[ "$wait" -eq 0 ] && [ "${#units[@]}" -eq 0 ] && [ "$landing" -eq 0 ] || die "--cancel takes no units, --landing or --wait"
		verify_cancel "$store"
	fi
	if [ "$wait" -eq 1 ]; then
		[ -n "$(field "$store" '.verifying.verdict // empty')" ] || die "no verification is open; start one with kitchen.sh verify $store <NNN>" 5
		verify_wait "$store" "$every_m"
	fi
	[ "$landing" -eq 1 ] || [ "${#units[@]}" -gt 0 ] || die "name at least one unit NNN, or --landing"
	[ "$landing" -eq 0 ] || [ "${#units[@]}" -eq 0 ] || die "--landing verifies the whole stack; name no units"
	# A verdict written but never read closes the verification it belonged to; the new one starts after it.
	local open_verdict
	open_verdict="$(field "$store" '.verifying.verdict // empty')"
	if [ -n "$open_verdict" ] && [ -f "$open_verdict" ] && [ -n "$(header_field "$open_verdict" status)" ]; then
		local closed closed_rc=0 closed_status closed_brief
		closed_brief="$(field "$store" .verifying.brief)"
		local waiting_since="" by_another=0
		while :; do
			if [ "$(field "$store" '.verifying.verdict // empty')" != "$open_verdict" ]; then
				by_another=1
				break
			fi
			closed_rc=0
			closed="$(closing=1 verify_wait "$store" 0 2>&1)" || closed_rc=$?
			closed_status="$(sed -n 's/^status: //p' <<<"$closed" | head -1)"
			[ -z "$closed_status" ] || break
			grep -q 'verification closed elsewhere' <<<"$closed" || break
			# Another call is closing this verdict: wait for it to finish, then go on as after a close of our own.
			if [ -z "$waiting_since" ]; then
				waiting_since="$(date +%s)"
				printf 'closing %s: another call is closing it; waiting for it\n' "$open_verdict"
			fi
			[ $(( $(date +%s) - waiting_since )) -lt "${KITCHEN_VERIFY_CLOSE_WAIT_S:-120}" ] || die "another call has held the close of $open_verdict for ${KITCHEN_VERIFY_CLOSE_WAIT_S:-120}s; kitchen.sh verify $store --wait" 5
			sleep "${KITCHEN_VERIFY_POLL_S:-10}"
		done
		if [ "$by_another" -eq 1 ]; then
			closed_status="closed by another call"
			closed=""
			case "$(header_field "$open_verdict" status)" in clean) closed_rc=0 ;; *) closed_rc=2 ;; esac
		elif [ -z "$closed_status" ]; then
			printf 'closing the unread verdict %s failed:\n%s\n' "$open_verdict" "$closed" >&2
			exit "$(( closed_rc > 0 ? closed_rc : 5 ))"
		fi
		printf 'closed: %s (%s)\n%s\n' "$open_verdict" "$closed_status" "$closed"
		# A verdict that is not clean is the master's to act on before another verification starts.
		if [ "$closed_rc" -ne 0 ]; then
			event "$store" master wake "$closed_brief" "verify:${closed_status%% *}"
			exit "$closed_rc"
		fi
	fi
	[ -z "$(field "$store" '.verifying.verdict // empty')" ] || die "a verification is open ($(field "$store" '.verifying.units')); kitchen.sh verify $store --wait" 5
	local -a all=("${covered[@]}" "${units[@]}")
	local root base head json last brief slug k verdict packet mode class risk snap prior prior_base_ref
	root="$(field "$store" .git_root)"
	[ -z "$land_head" ] || [ "$landing" -eq 1 ] || die "--head goes with --landing"
	[ -z "$parent_ref" ] || [ "$landing" -eq 1 ] || die "--parent goes with --landing"
	if [ "$landing" -eq 1 ]; then
		# The stack tip, from where it leaves trunk, by a fresh verifier of
		# the escalated class: a squash or rebase step touches no profile, so
		# the units' own verdicts never saw the stack as it lands.
		head="$(git -C "$root" rev-parse --verify --quiet "${land_head:-HEAD}^{commit}")" || die "--head $land_head is not a commit"
		landing_parent_override="" landing_parent_label=""
		[ "$parent_ref" != none ] || landing_parent_label=none
		if [ -n "$parent_ref" ] && [ "$parent_ref" != none ]; then
			landing_parent_override="$(git -C "$root" rev-parse --verify --quiet "$parent_ref^{commit}")" || die "--parent $parent_ref is not a commit"
			landing_parent_label="$parent_ref"
			[ "$landing_parent_override" != "$head" ] && git -C "$root" merge-base --is-ancestor "$landing_parent_override" "$head" 2>/dev/null ||
				die "--parent $parent_ref (${landing_parent_override:0:9}) must be an ancestor of ${head:0:9} and not the same commit; name where the stack starts"
		fi
		if [ -n "$range_base" ]; then
			base="$(git -C "$root" rev-parse --verify --quiet "$range_base^{commit}")" || die "--base $range_base is not a commit"
		else
			landing_base "$root" "$head" "$store"
			base="$landing_base_rev"
		fi
		# A rebase since the last landing verdict may have moved a dependency manifest.
		prior="$(latest_landing_verdict "$store")"
		if [ -n "$prior" ]; then
			read -r prior_base_ref _ <<<"$(verdict_range "$prior")"
			manifest_warning "$store" "$(git -C "$root" rev-parse --verify --quiet "$prior_base_ref^{commit}" 2>/dev/null || true)" "$base" "$head"
		fi
		# The stack lands under the kitchen as it is now.
		kitchen_cfg=()
		snap="$(kitchen_snapshot "$store")"
		[ -z "$snap" ] || kitchen_cfg=(--config "$snap")
		json="$(kpy "$store" "${kitchen_cfg[@]}" --at "$head" --json classify --base "$base" --head "$head")"
		risk=landing
		mode=unit
		class=escalated
	else
		# not inside the substitution that feeds read: a die in unit_range must end verify, not leave base and head empty
		local span
		span="$(unit_range "$store" "${all[@]}")"
		read -r base head <<<"$span"
		unit_heads_in "$store" "$head" "${all[@]}"
		# A unit dispatched as escalated is verified as escalated, whatever
		# its diff touches.
		local -a esc=()
		for u in "${all[@]}"; do
			[ "$(header_field "$(unit_brief "$store" "$u")" risk)" != escalated ] || esc+=(--escalate "unit $u was dispatched as escalated")
		done
		brief_kitchen "$(unit_brief "$store" "${all[-1]}")"
		json="$(kpy "$store" "${kitchen_cfg[@]}" --at "$head" --json classify --base "$base" --head "$head" "${esc[@]}")"
		risk="$(jq -r .risk <<<"$json")"
		mode="$(jq -r .verify.mode <<<"$json")"
		class="$(jq -r .verify.kind <<<"$json")"
	fi
	# A batch of independent units has a size cap; a fix verified with the
	# unit it fixes does not, since splitting it would re-reject the first.
	if [ "${#covered[@]}" -eq 0 ] && [ "${#units[@]}" -gt 1 ] && [ "$(jq '.diff_lines > .max_batch_diff' <<<"$json")" = true ]; then
		die "batch of $(jq .diff_lines <<<"$json") diff lines is over review.max_batch_diff; verify each unit" 2
	fi
	if [ "$landing" -eq 1 ]; then
		last=landing
		brief=-
		k="$(next_index "$store/verdicts" landing v)"
		verdict="$store/verdicts/landing-v$k.md"
		packet="$store/verdicts/landing-v$k-packet.md"
	else
		last="${all[-1]}"
		brief="$(unit_brief "$store" "$last")"
		slug="$(basename "$brief" .md | cut -c5-)"
		k="$(next_index "$store/verdicts" "$last-$slug" v)"
		verdict="$store/verdicts/$last-$slug-v$k.md"
		packet="$store/verdicts/$last-$slug-v$k-packet.md"
	fi
	if [ "$mode" = gates ]; then
		{
			printf '# Verdict %s: %s\n\nstatus: clean\nunits: %s\nrange: %s..%s\nverifier: gates\n\n' "$last" "$slug" "${all[*]}" "${base:0:9}" "${head:0:9}"
			printf '## Findings\n\nnone\n\n## Evidence\n\nEvery step passed its profiles'"'"' fast gates and the policy check:\n\n```\n'
			local u
			for u in "${all[@]}"; do
				awk -F '\t' -v u="$(basename "$(unit_brief "$store" "$u")" .md)" '$2 == u' "$store/gates.tsv" 2>/dev/null || true
			done
			printf '```\n'
		} >"$verdict"
		event "$store" master verify "$brief" "gates:clean"
		event "$store" master wake "$brief" "verify:clean"
		printf 'verdict: %s\nstatus: clean (gates only; mode gates)\n' "$verdict"
		warn_unreviewed "$store"
		handoff_due "$store" "$last"
		exit 0
	fi
	# The default deadline: 3 x the slowest recent passing behavioral gates, plus
	# the last recorded setup run when the repo declares one, scaled by the
	# machine's load at the start (capped at 3x), and never under 45 minutes. The
	# parts are printed; --timeout wins.
	if [ -z "$timeout_m" ]; then
		local -a dlargs=(timing --deadline --stage behavioral)
		[ "$(jq '.scratch_setup | length' <<<"$json")" -eq 0 ] || dlargs+=(--with-setup)
		mapfile -t -O "${#dlargs[@]}" dlargs < <(jq -r '.behavioral[]' <<<"$json")
		timeout_m="$(kpy "$store" --json "${dlargs[@]}" | jq .minutes)"
		kpy "$store" "${dlargs[@]}"
		# A landing verification also runs the touched profiles' landing commands: 3 x their measured time,
		# and a fixed ten minutes for each that has no measured run.
		if [ "$landing" -eq 1 ]; then
			local -a lprofs=()
			mapfile -t lprofs < <(jq -r '.landing[]?' <<<"$json")
			if [ "${#lprofs[@]}" -gt 0 ]; then
				local ljson lsec lunmeasured ladd
				ljson="$(kpy "$store" --json timing --stage landing "${lprofs[@]}")"
				lsec="$(jq '.seconds' <<<"$ljson")"
				lunmeasured="$(jq '.unmeasured | length' <<<"$ljson")"
				ladd="$(awk -v s="$lsec" -v u="$lunmeasured" 'BEGIN { m = int((3 * s + 59) / 60); print m + 10 * u }')"
				timeout_m=$(( timeout_m + ladd ))
				printf 'deadline +%sm for the landing gates of %s: 3 x %sm measured%s; now %sm\n' "$ladd" "${lprofs[*]}" \
					"$(awk -v s="$lsec" 'BEGIN { printf "%.0f", s / 60 }')" "$([ "$lunmeasured" -eq 0 ] || printf ', %s unmeasured at 10m each' "$lunmeasured")" "$timeout_m"
			fi
		fi
	else
		printf 'deadline %sm (--timeout)\n' "$timeout_m"
	fi
	local kind name pane anchor scratch_id scratch new_pane_id entry
	local -a args=()
	entry="$(verifier_entry "$store" "$class")"
	if [ -n "$retry_entry" ]; then
		kind="$(jq -r .kind <<<"$retry_entry")"
		mapfile -t args < <(verifier_args "$kind" "$(jq -c '{kind, args}' <<<"$retry_entry")" "$(field "$store" .git_root)")
	elif [ -n "$kind_override" ]; then
		kind="$kind_override"
		mapfile -t args < <(verifier_args "$kind" "$(jq -c --arg k "$kind" '[., .fallback][] | select(. != null and .kind == $k) | {kind, args}' <<<"$entry" | head -1)" "$(field "$store" .git_root)")
	else
		kind="$(jq -r .kind <<<"$entry")"
		mapfile -t args < <(verifier_args "$kind" "$entry" "$(field "$store" .git_root)")
	fi
	[ -n "$kind" ] && [ "$kind" != null ] || die "cannot tell which agent kind verifies ($class)"
	local identity vclass
	identity="$(verifier_identity "$kind" "${args[@]}")"
	vclass="$class"
	[ "$landing" -eq 0 ] || vclass=landing
	if [ "$landing" -eq 1 ]; then scratch_id="000-landing-verify"; else scratch_id="$last-$slug-verify"; fi
	scratch="$(cmd_scratch "$store" "$scratch_id" --at "$head" | tail -1)"
	# The last point verify can die before it starts the verifier: only now is the stack's parent recorded or cleared.
	if [ "$landing" -eq 1 ]; then
		if [ "$parent_ref" = none ]; then
			json_update "$store" 'del(.landing.parent, .landing.parent_ref)'
		elif [ -n "$landing_parent_override" ]; then
			json_update "$store" --arg p "$landing_parent_override" --arg r "$landing_parent_label" '.landing.parent = $p | .landing.parent_ref = $r'
		fi
	fi
	local packet_write_rule="Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict."
	{
		local u b
		if [ "$landing" -eq 1 ]; then
			printf '# Verify landing: the stack at %s\n\nverdict: %s\ntemplate: %s\nscratch: %s\nrange: %s..%s\nrisk: landing\nunits: landing\nverifier-agent: %s\nverifier-class: %s\nverifier-outcome: prepared\ndeadline: %sm from now\n\n' \
				"${head:0:9}" "$verdict" "$skill_root/references/verdict-template.md" "$scratch" "$base" "$head" "$identity" "$vclass" "$timeout_m"
			printf '%s\n\n' "$packet_write_rule"
			printf '## Stack\n\nEach unit below was verified on its own. Prove that together, at this tip,\nthey still hold: run every Prove command, then drive the main flows the\nbriefs describe, and try the seams between units. Read a brief only for\nthe flow you are driving.\n\n'
			local -a stacked=()
			mapfile -t stacked < <(landing_covered_briefs "$store" "$base" "$head")
			for b in "${stacked[@]}"; do
				printf -- '- %s\n' "$b"
			done
			printf '\n## Goals\n\n'
			for b in "${stacked[@]}"; do
				printf '### Unit %s %s\n\n' "$(basename "$b" .md | cut -c1-3)" "$(basename "$b" .md | cut -c5-)"
				printf '%s\n\n' "$(awk '/^## Goal/{p=1; next} /^## /{p=0} p' "$b" | sed '/./,$!d')"
			done
		else
			printf '# Verify %s: %s\n\nverdict: %s\ntemplate: %s\nscratch: %s\nrange: %s..%s\nrisk: %s\nunits: %s\nverifier-agent: %s\nverifier-class: %s\nverifier-outcome: prepared\ndeadline: %sm from now\n\n' \
				"$last" "$slug" "$verdict" "$skill_root/references/verdict-template.md" "$scratch" "$base" "$head" "$risk" "${all[*]}" "$identity" "$vclass" "$timeout_m"
			printf '%s\n\n' "$packet_write_rule"
			[ "${#covered[@]}" -eq 0 ] || printf 'Unit %s fixes unit %s: prove every unit'"'"'s Acceptance at this one head.\n\n' "${units[*]}" "${covered[*]}"
			for u in "${all[@]}"; do
				b="$(unit_brief "$store" "$u")"
				printf '## Unit %s\n\nbrief: %s\n\n' "$u" "$b"
				awk '/^## (Goal|Acceptance)/{p=1; print; next} /^## /{p=0} p' "$b"
				printf '\n'
				brief_commands "$store" "$b" "$u"
			done
			local -a ubriefs=()
			local lessons
			# Every brief of each unit: an amendment may not carry the Lessons.
			for u in "${all[@]}"; do ubriefs+=("$store"/briefs/"$u"-*.md); done
			lessons="$(python3 "$here/kitchen.py" lesson-packet "${ubriefs[@]}")"
			[ -z "$lessons" ] || printf '%s\n\n' "$lessons"
		fi
		if [[ -f "$store/standing-orders.md" ]]; then
			printf '## Standing orders\n\nThey bind you as they bind the sidekick (%s):\n\n%s\n\n' "$store/standing-orders.md" "$(cat "$store/standing-orders.md")"
		fi
		printf '## Changed\n\n```\n%s\n```\n\n' "$(git -C "$root" diff --stat "$base...$head" | tail -40)"
		printf '## Prove\n\nYou share this machine with the sidekick. Run every repo command with\n`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo'"'"'s scripts give you\nyour own ports, emulators, and data, and stop everything you start before\nyou end. Run these in the scratch worktree, then drive what they cannot reach:\n\n```bash\nexport PSTACK_KITCHEN_ROLE=verifier'
		printf ' PSTACK_KITCHEN_RUN=%s\n' "$(field "$store" .slug)"
		jq -r --arg k "$here/kitchen.py${kitchen_cfg[1]:+ --config ${kitchen_cfg[1]}}" --arg s "$scratch" 'if (.scratch_setup | length) > 0 then "python3 \($k) --repo \($s) setup" else empty end' <<<"$json"
		jq -r --arg k "$here/kitchen.py${kitchen_cfg[1]:+ --config ${kitchen_cfg[1]}}" --arg s "$scratch" --arg b "$(git -C "$root" merge-base "$base" "$head" 2>/dev/null || printf '%s' "$base")" \
			'.behavioral[] | "python3 \($k) --repo \($s) gate \(.) behavioral --role verifier --base \($b)"' <<<"$json"
		# A landing verification also runs the touched profiles' landing commands.
		[ "$risk" != landing ] || jq -r --arg k "$here/kitchen.py${kitchen_cfg[1]:+ --config ${kitchen_cfg[1]}}" --arg s "$scratch" --arg b "$(git -C "$root" merge-base "$base" "$head" 2>/dev/null || printf '%s' "$base")" \
			'.landing[] | "python3 \($k) --repo \($s) gate \(.) landing --role verifier --base \($b)"' <<<"$json"
		printf '```\n\nfeature map: %s\n' "$(jq -r '.features | if length == 0 then "none listed" else join(", ") end' <<<"$json")"
		printf 'verification skill: %s\n' "$(ls -d "$root"/.agents/skills/verify-*/ 2>/dev/null | tr '\n' ' ' || true)"
	} >"$packet"
	name="$(field "$store" .verifier.name)"
	anchor="$(field "$store" '.sidekick.pane_id // empty')"
	[ -n "$anchor" ] && herdr pane get "$anchor" >/dev/null 2>&1 || anchor="$(field "$store" .master.pane_id)"
	new_pane "$store" verifier "$anchor" "" "$scratch" PSTACK_KITCHEN_ROLE=verifier "PSTACK_KITCHEN_RUN=$(field "$store" .slug)" >/dev/null
	pane="$new_pane_id"
	local start_failed=0
	herdr agent start "$name" --kind "$kind" --pane "$pane" --timeout 60000 -- "${args[@]}" >/dev/null || start_failed=1
	if [ "$start_failed" -eq 1 ] && [ "$kind" != prime-agent ]; then
		local why="verifier start failed in $pane"
		if grep -qiE "$trust_re" <<<"$(herdr pane read "$pane" --source visible --lines 40 2>/dev/null | tr -s '\n\t ' ' ')"; then
			why="$kind asks whether to trust $scratch, and that decision is yours: open $kind in the repo once and trust it, or verify with another kind"
			herdr pane send-keys "$pane" esc >/dev/null 2>&1 || true
		fi
		herdr pane close "$pane" >/dev/null 2>&1 || true
		cmd_scratch "$store" "$scratch_id" --remove >/dev/null
		set_outcome "$packet" start-failed
		die "$why" 5
	fi
	local started_at
	started_at="$(date +%s)"
	json_update "$store" --arg pane "$pane" --arg kind "$kind" --arg verdict "$verdict" --arg packet "$packet" \
		--arg brief "$brief" --arg scratch "$scratch_id" --arg units "${all[*]:-landing}" --arg risk "$risk" \
		--argjson sample "$(if [ "$landing" -eq 1 ]; then echo 0; else jq .sample <<<"$json"; fi)" \
		--arg unit "$(if [ "$landing" -eq 1 ]; then echo landing; else echo "$last-$slug"; fi)" \
		--argjson started "$started_at" --argjson deadline "$(( started_at + timeout_m * 60 ))" \
		--arg class "$class" --argjson entry "$entry" --argjson retried "$(if [ -n "$retry_entry" ]; then echo true; else echo false; fi)" \
		--argjson argv "$(jq -cn '$ARGS.positional' --args -- "${argv[@]}")" \
		--argjson native "$(jq -cn '$ARGS.positional' --args -- "${args[@]}")" \
		--arg launch_cwd "$scratch" \
		'.verifier.pane_id = $pane | .verifier.kind = $kind
		 | if $kind == "prime-agent" then .verifier.start_args = $native | .verifier.session = null | .verifier.session_stopped = false | .verifier.closed = false | .verifier.launch_cwd = $launch_cwd else . end
		 | .verifying = {verdict: $verdict, packet: $packet, brief: $brief, scratch: $scratch, units: $units,
		                 risk: $risk, sample: $sample, unit: $unit, started: $started, deadline: $deadline,
		                 class: $class, entry: $entry, retried: $retried, argv: $argv, delivered: false}'
	set_outcome "$packet" running
	if [ "$kind" = prime-agent ]; then
		if [ "$start_failed" -eq 1 ]; then
			set_outcome "$packet" start-failed
			record_prime_session "$store" verifier || true
			die "Prime verifier start failed in $pane; its pane and scratch stay for inspection (verify-wait can resume cleanup once its session is reported)" 5
		fi
		record_prime_session "$store" verifier || die "Prime verifier did not report its session; its pane and scratch stay for inspection" 5
	fi
	event "$store" master send-verify "$brief" "$kind:${all[*]:-landing}"
	record_verifier_session "$store" "$name"
	printf 'verifier %s (%s) in %s on %s..%s\n' "$name" "$identity" "$pane" "${base:0:9}" "${head:0:9}"
	# A just-started agent can take the text before it takes the Enter (pi
	# drawing its startup screen), so the prompt must be seen working; an
	# idle agent gets one more Enter, which submits the typed text. A startup
	# screen can also swallow the prompt whole: an agent never seen working,
	# with nothing typed and no verdict, still idle, gets the packet once more.
	local took=0
	herdr agent prompt "$name" "$(verify_prompt "$packet")" --wait --until working --timeout 30000 >/dev/null 2>&1 && took=1
	submit_typed "$name" "$(basename "$packet")"
	if [ "$took" -eq 0 ] && [ "$submitted" -eq 0 ] && [ ! -f "$verdict" ]; then
		case "$(agent_status "$name")" in
		idle | "done") redeliver_packet "$store" "the verifier sat idle without the packet" ;;
		esac
	fi
	json_update "$store" '.verifying.delivered = true'
	record_verifier_session "$store" "$name"
	verify_wait "$store" "$every_m"
}

# The verifier for a class: the host roster's entry, with the sidekick marker
# resolved from the run (so after a failover it is the agent now implementing)
# and the master sentinel resolved to the master's own kind. One JSON object:
# kind, args, fallback (kind and args, or null), and where it came from.
verifier_entry() {
	local store="$1" class="$2" entry kind
	entry="$(python3 "$here/kitchen.py" --json roster | jq -c --arg c "$class" '.verifier[$c]')"
	kind="$(jq -r .kind <<<"$entry")"
	case "$kind" in
	sidekick)
		jq -c '{kind: .sidekick.kind, args: (.sidekick.start_args // []), source: "sidekick",
			fallback: (if .sidekick.fallback.kind then {kind: .sidekick.fallback.kind, args: (.sidekick.fallback.args // [])} else null end)}' "$store/pair.json" ;;
	master)
		jq -c --arg k "$(agent_kind "$(field "$store" .master.pane_id)")" '.kind = $k | .args = [] | .source = "master"' <<<"$entry" ;;
	*) printf '%s\n' "$entry" ;;
	esac
}

# The verifier as configured, for the start line and the packet: the kind and
# its model arguments, shell-quoted, with the permission and trust arguments
# left out; "<kind> (model unspecified)" when no argument is left. It is what
# was asked of the CLI, not proof of the model that answered.
verifier_identity() {
	local kind="$1" a skip=0 out="" any=0
	shift
	for a in "$@"; do
		if [ "$skip" -eq 1 ]; then skip=0; continue; fi
		case "$a" in
		--permission-mode | --respect-workspace-trust | -a | -s | --ask-for-approval | --sandbox | --mcp-config) skip=1; continue ;;
		--permission-mode=* | --respect-workspace-trust=* | --ask-for-approval=* | --sandbox=* | --approve | --no-approve | -na | \
			--mcp-config=* | --strict-mcp-config | \
			--dangerously-skip-permissions | --allow-dangerously-skip-permissions | --dangerously-bypass-approvals-and-sandbox) continue ;;
		esac
		out+=" $(printf '%q' "$a")"
		any=1
	done
	if [ "$any" -eq 1 ]; then printf '%s%s\n' "$kind" "$out"; else printf '%s (model unspecified)\n' "$kind"; fi
}

# The packet's host-written outcome for this attempt: prepared, running, then
# one of clean, reject, inconclusive, invalid, provider, missing, start-failed.
set_outcome() {
	[ -f "$1" ] || return 0
	VAL="$2" perl -i -pe 's/^verifier-outcome:.*/verifier-outcome: $ENV{VAL}/' "$1"
}

# A verifier starts with its entry's arguments, then its kind's permission
# default, trust flag and MCP opt-out when they are missing; $2 is the entry
# (or an empty string for a kind the class does not name), $3 the repo the
# scratch is checked out from, whose MCP servers the opt-out names.
verifier_args() {
	local kind="$1" entry="$2" dir="${3:-}" permission
	local -a args=()
	[ -z "$entry" ] || mapfile -t args < <(jq -r '.args[]?' <<<"$entry")
	if ! has_permission_arg "${args[@]}"; then
		case "$kind" in devin) permission=bypassPermissions ;; *) permission="$(detect_permission_mode)" ;; esac
		permission_args "$kind" "$permission"
	fi
	has_trust_arg "${args[@]}" || trust_args "$kind"
	has_mcp_arg "${args[@]}" || mcp_args "$kind" "$dir"
	[ "${#args[@]}" -eq 0 ] || printf '%s\n' "${args[@]}"
}

# The agent's own session log, so a verifier that died on its provider or
# never got its packet can still say why after its pane is gone. Herdr gives
# pi's as a path and Claude's as an id, whose transcript Claude keeps under
# ~/.claude/projects/ in a directory named for the working directory.
record_verifier_session() {
	local info session id cwd
	info="$(herdr agent get "$2" 2>/dev/null)" || return 0
	session="$(jq -r '.result.agent.agent_session | select(.kind == "path") | .value' <<<"$info" 2>/dev/null || true)"
	if [ -z "$session" ]; then
		id="$(jq -r '.result.agent | select(.agent == "claude") | .agent_session | select(.kind == "id") | .value' <<<"$info" 2>/dev/null || true)"
		cwd="$(jq -r '.result.agent.cwd // empty' <<<"$info" 2>/dev/null || true)"
		[ -n "$cwd" ] || cwd="$(readlink -f "$1/scratch/$(field "$1" '.verifying.scratch // empty')" 2>/dev/null || true)"
		[ -z "$id" ] || [ -z "$cwd" ] || session="$HOME/.claude/projects/$(printf '%s' "$cwd" | sed 's/[^A-Za-z0-9-]/-/g')/$id.jsonl"
	fi
	[ -z "$session" ] || json_update "$1" --arg s "$session" '.verifying.session = $s'
}

# What the verifier's transcript says about an attempt that ended without a
# clean or rejecting verdict: where it is, and whether the packet reached it.
verifier_trail() {
	local session="$1" packet="$2"
	[ -n "$session" ] || return 0
	printf 'transcript: %s\n' "$session"
	[ -f "$session" ] || return 0
	if grep -qF "VERIFY $packet" "$session"; then
		printf 'packet: the verifier received it\n'
	else
		printf 'packet: never reached the verifier; this attempt says nothing about the work, so rerun it\n'
	fi
}

# The verifier's latest provider error: from its screen while it is up, else
# from the error stop its session log recorded. Empty when there is none.
verifier_provider_error() {
	local store="$1" name="$2" line="" session
	# $3 is the session of the verification the caller owns, when it captured it.
	if [ "$(field "$store" .verifier.kind)" = prime-agent ]; then
		prime_role_error "$store" verifier
		return 0
	fi
	[ "$(agent_status "$name")" = absent ] ||
		line="$(grep -oiE ".{0,60}($provider_error_re).{0,100}" <<<"$(pane_text "$name")" | tail -1 || true)"
	session="${3-$(field "$store" '.verifying.session // empty')}"
	if [ -z "$line" ]; then
		local cwd=""
		[ "$(field "$store" .verifier.kind)" != pi ] && [ "$(field "$store" .verifier.kind)" != muse ] ||
			cwd="$(readlink -f "$store/scratch/$(field "$store" .verifying.scratch)" 2>/dev/null || true)"
		if [ "$(field "$store" .verifier.kind)" = muse ]; then
			line="$(muse_session_error "$cwd")"
		else
			line="$(agent_session_error "$session" "$cwd")"
		fi
	fi
	printf '%s' "${line:0:240}"
}

# The verifier's state for a progress line: working when its pane runs a command of its own.
verifier_shown() {
	local store="$1" state="$2" cmd
	case "$state" in
	idle | "done")
		cmd="$(busy_command "$(field "$store" .verifier.pane_id)" "$(field "$store" .verifying.started)")"
		if [ -n "$cmd" ]; then printf 'working (%s)' "$cmd"; return; fi ;;
	esac
	printf '%s' "$state"
}

# At the deadline a live verifier is asked, once, to write an inconclusive
# verdict with what it proved and what it never reached, and given
# KITCHEN_VERIFY_GRACE_S (default 600) to write it. The grace is part of the
# attempt: its state is stored before the prompt is delivered, a later --wait
# resumes the stored expiry and never prompts again, and while it runs the scratch
# and the pane stay. A verdict already written wins and a provider error takes
# the provider path before this runs. pi, claude and codex take the prompt
# whether idle, done or working (their harnesses queue it); a working Devin does
# not (Enter would cancel its command), so it is only waited on; a blocked or
# unknown agent gets no prompt and no grace. $5 is this call's interval end.
verify_grace() {
	local store="$1" name="$2" kind="$3" verdict="$4" until="$5" brief="$6" session="$7" state now delivery grace_until text
	# Before anything is read or the verifier is prompted: the verification must still be this wait's.
	verification_still_open "$store" "$verdict"
	grace_until="$(field "$store" '.verifying.grace_until // empty')"
	if [ -z "$grace_until" ]; then
		state="$(agent_status "$name")"
		case "$state" in blocked | unknown | absent) return 0 ;; esac
		now="$(date +%s)"
		grace_until=$(( now + ${KITCHEN_VERIFY_GRACE_S:-600} ))
		delivery=prompted
		if [ "$kind" = devin ] && [ "$state" = working ]; then delivery=skipped:devin-working; fi
		update_verifying "$store" "$verdict" '.verifying.grace_at = $at | .verifying.grace_until = $until | .verifying.grace_delivery = $d' \
			--argjson at "$now" --argjson until "$grace_until" --arg d "$delivery"
		if [ "$delivery" = prompted ]; then
			text="Deadline reached: write $verdict now with status inconclusive, listing what you proved (with its evidence) and what you never reached, then end your turn. Stop any process you started first, and write the file complete in one write: create it under a temporary name in the same directory, then rename it into place."
			if herdr agent prompt "$name" "$text" >/dev/null 2>&1; then
				submit_typed "$name" "$(basename "$verdict")"
			else
				delivery=skipped:prompt-failed
				update_verifying "$store" "$verdict" '.verifying.grace_delivery = $d' --arg d "$delivery"
			fi
		fi
		event "$store" master verify-grace "$brief" "$delivery"
	fi
	while :; do
		{ [ -f "$verdict" ] && [ -n "$(header_field "$verdict" status)" ]; } && return 0
		state="$(agent_status "$name")"
		verification_still_open "$store" "$verdict"
		[ "$state" != absent ] || return 0
		now="$(date +%s)"
		[ "$now" -lt "$grace_until" ] || return 0
		case "$state" in
		idle | "done" | blocked) [ -z "$(verifier_provider_error "$store" "$name" "$session")" ] || return 0 ;;
		esac
		if [ "$now" -ge "$until" ]; then
			printf 'verifying: units %s by %s, past its deadline; grace %ss left (%s), verifier %s\n' "$(field "$store" .verifying.units)" "$kind" \
				"$(( grace_until - now ))" "$(field "$store" .verifying.grace_delivery)" "$(verifier_shown "$store" "$state")"
			printf 'next: other work, then kitchen.sh verify %s --wait\n' "$store"
			event "$store" master wake "$brief" "verify:grace"
			exit 4
		fi
		sleep "${KITCHEN_VERIFY_GRACE_POLL_S:-5}"
	done
}

# Cancels the open verification that has no verdict yet: its verifier and pane
# stop, its scratch goes, and .verifying is cleared.
verify_cancel() {
	local store="$1" verdict units name kind brief packet
	verdict="$(field "$store" '.verifying.verdict // empty')"
	[ -n "$verdict" ] || die "no verification open" 5
	if [ -f "$verdict" ] && [ -n "$(header_field "$verdict" status)" ]; then
		die "the verdict is written ($verdict); kitchen.sh verify $store --wait reads it" 5
	fi
	units="$(field "$store" .verifying.units)"
	brief="$(field "$store" .verifying.brief)"
	packet="$(field "$store" .verifying.packet)"
	name="$(field "$store" .verifier.name)"
	kind="$(field "$store" .verifier.kind)"
	run_close "$store" "$name" "$kind" "$(field "$store" .verifier.pane_id)" "$verdict" || die "no verification open (another call closed it)" 5
	still_my_claim "$store"
	set_outcome "$packet" cancelled
	drop_verifying "$store" "$verdict"
	event "$store" master verify-cancel "$brief" "$units"
	printf 'cancelled: %s\n' "$units"
	exit 0
}

# Waits up to $2 minutes for the open verification's verdict. A verifier
# still working at the interval leaves the verification open (exit 4, with
# its progress); a verdict, a gone verifier, or the deadline closes it:
# the pane is closed, the scratch removed, and the verdict read. So does a
# stall: a verifier idle for KITCHEN_VERIFY_IDLE_S (default 600) with no
# verdict and no process of its own still running in its pane, which would
# otherwise sit out the whole deadline.
verify_prompt() {
	printf 'Load the %s skill from ~/.agents/skills/%s/SKILL.md and take the verifier role. %s VERIFY %s' "$PAIR_SKILL" "$PAIR_SKILL" "$PAIR_SKILL" "$1"
}

# Sends the open verification's packet once more; $2 says why. $3 to $6 are the verifier's name, the
# packet, the brief and the verifier kind when the caller captured them (a wait does); else the store's.
redeliver_packet() {
	local store="$1" name packet brief kind
	name="${3:-$(field "$store" .verifier.name)}"
	packet="${4:-$(field "$store" .verifying.packet)}"
	brief="${5:-$(field "$store" .verifying.brief)}"
	kind="${6:-$(field "$store" .verifier.kind)}"
	event "$store" master verify-redeliver "$brief" "$kind"
	printf 'redelivered: %s; sent it again\n' "$2"
	herdr agent prompt "$name" "$(verify_prompt "$packet")" --wait --until working --timeout 30000 >/dev/null 2>&1 || true
	submit_typed "$name" "$(basename "$packet")"
}

# Clears .verifying only while it still names the verification with verdict $2:
# another call may have closed it and started the next.
drop_verifying() {
	json_update "$1" --arg v "$2" 'if .verifying.verdict == $v then del(.verifying) else . end'
}

# Applies jq update $3 to .verifying only while it still names the verification with verdict $2, so a
# write that lands after another call closed it cannot bring .verifying back. $4... are jq arguments.
update_verifying() {
	local store="$1" v="$2" body="$3"
	shift 3
	json_update "$store" --arg v "$v" "$@" "if .verifying.verdict == \$v then ($body) else . end"
}

# Claims the close of the verification with verdict $2: one atomic update of pair.json that
# succeeds for the first caller only. The claim names this host and process; a claim whose process
# is gone on this host is taken over, one from another host is never touched, a live one is
# respected. Returns 1 when the verification is gone or another call holds the claim. The claim ends
# with the verification (drop_verifying) or, when the close dies, with release_my_claim.
claim_close() {
	local token current="" pid="" host="" here_host
	here_host="${HOSTNAME:-localhost}"
	token="$here_host:$$:$RANDOM"
	current="$(field "$1" '.verifying.closing.token // empty')"
	if [ -n "$current" ]; then
		host="${current%%:*}"
		pid="${current#*:}"
		pid="${pid%%:*}"
		[ "$host" = "$here_host" ] && ! kill -0 "$pid" 2>/dev/null || return 1
	fi
	update_verifying "$1" "$2" 'if (.verifying.closing.token // "") == $old then .verifying.closing = {token: $t} else . end' \
		--arg t "$token" --arg old "$current"
	[ "$(field "$1" '.verifying.closing.token // empty')" = "$token" ]
}

# Gives up the claim $3 on the verification with verdict $2, when it is still the holder's.
release_close() {
	update_verifying "$1" "$2" 'if .verifying.closing.token == $t then del(.verifying.closing) else . end' --arg t "$3" 2>/dev/null || true
}

# Whether the claim in the store is this process's (the token names this host and pid).
my_claim() {
	case "$(field "$1" '.verifying.closing.token // empty')" in "${HOSTNAME:-localhost}:$$:"*) return 0 ;; esac
	return 1
}

# After close_verifier: the claim must still be this closer's before anything is written.
still_my_claim() {
	my_claim "$1" && return 0
	printf 'verification closed elsewhere\n'
	exit 5
}

# Runs close_verifier (arguments as it takes them) in a subshell so that a die inside it, the Prime cleanup
# failing, cannot leave this process's claim behind: the claim is released and the same status returned.
# close_verifier's own status 70 is "not ours to close" (closed elsewhere); any other nonzero status is a die. $1 is the store, $5 the verdict.
run_close() {
	local rc=0
	( close_verifier "$@" ) || rc=$?
	[ "$rc" -eq 0 ] && return 0
	[ "$rc" -eq 70 ] && return 1
	if my_claim "$1"; then
		release_close "$1" "$5" "$(field "$1" '.verifying.closing.token // empty')"
	fi
	exit "$rc"
}

# Stops the open verification's verifier, closes its pane and removes its scratch,
# unless .verifying no longer names the verification with verdict $5 or another call claimed it (status 70).
close_verifier() {
	local store="$1" name="$2" kind="$3" pane="$4" gone scratch
	# Of two closers only the first to claim the verification closes it; while it holds the claim nothing can
	# replace the verification, so the pane and scratch below are its own.
	claim_close "$store" "${5:-}" || return 70
	scratch="$(field "$store" .verifying.scratch)"
	if [ "$kind" = prime-agent ]; then
		stop_prime_role "$store" verifier || die "Prime verifier cleanup failed; its pane and scratch stay for inspection" 5
	else
		herdr agent prompt "$name" "$(exit_command "$kind")" >/dev/null 2>&1 || true
		gone=$(( $(date +%s) + ${KITCHEN_VERIFIER_EXIT_S:-15} ))
		while [ "$(agent_status "$name")" != absent ] && [ "$(date +%s)" -lt "$gone" ]; do sleep 1; done
	fi
	herdr pane close "$pane" >/dev/null 2>&1 || true
	[ "$kind" != prime-agent ] || json_update "$store" '.verifier.closed = true'
	cmd_scratch "$store" "$scratch" --remove >/dev/null
}

# A wait ends, touching nothing, when another call closed the verification it
# started on (cancelled it, or read its verdict and began the next).
verification_still_open() {
	[ "$(field "$1" '.verifying.verdict // empty')" = "$2" ] && return 0
	printf 'verification closed elsewhere\n'
	exit 5
}

verify_wait() {
	local store="$1" every_m="$2" verdict name kind until state="" now idle_since stalled=0
	local brief risk sample unit pane packet session provider="" fallback="" retried
	local -a argv=()
	# The verification this wait took, read once: after another call closes it and starts the next, these
	# still describe the one this wait owns, never the store's current one.
	verdict="$(field "$store" .verifying.verdict)"
	name="$(field "$store" .verifier.name)"
	kind="$(field "$store" .verifier.kind)"
	packet="$(field "$store" .verifying.packet)"
	session="$(field "$store" '.verifying.session // empty')"
	brief="$(field "$store" .verifying.brief)"
	risk="$(field "$store" .verifying.risk)"
	sample="$(field "$store" .verifying.sample)"
	unit="$(field "$store" .verifying.unit)"
	pane="$(field "$store" .verifier.pane_id)"
	fallback="$(field "$store" '.verifying.entry.fallback // empty' | jq -c . 2>/dev/null || true)"
	retried="$(field "$store" '.verifying.retried // false')"
	mapfile -t argv < <(jq -r '.verifying.argv[]?' "$store/pair.json")
	until=$(( $(date +%s) + every_m * 60 ))
	# A verify stopped between the verifier's start and its packet leaves the
	# verifier idle with nothing to do, until the stall check closes it.
	if [ "$(field "$store" .verifying.delivered)" = false ] && [ ! -f "$verdict" ]; then
		verification_still_open "$store" "$verdict"
		case "$(agent_status "$name")" in
		idle | "done") redeliver_packet "$store" "the verify that started this verifier stopped before sending its packet" "$name" "$packet" "$brief" "$kind" ;;
		esac
		update_verifying "$store" "$verdict" '.verifying.delivered = true'
	fi
	while :; do
		[ -f "$verdict" ] && [ -n "$(header_field "$verdict" status)" ] && break
		state="$(agent_status "$name")"
		verification_still_open "$store" "$verdict"
		now="$(date +%s)"
		[ "$state" != absent ] && [ "$now" -lt "$(field "$store" .verifying.deadline)" ] || break
		if [ "$now" -ge "$until" ]; then
			printf 'verifying: units %s by %s, %sm of %sm, verifier %s\n' "$(field "$store" .verifying.units)" "$kind" \
				"$(( (now - $(field "$store" .verifying.started)) / 60 ))" "$(( ($(field "$store" .verifying.deadline) - $(field "$store" .verifying.started)) / 60 ))" "$(verifier_shown "$store" "$state")"
			printf 'next: other work, then kitchen.sh verify %s --wait\n' "$store"
			event "$store" master wake "$(field "$store" .verifying.brief)" "verify:progress"
			exit 4
		fi
		# A verifier that ended its turn on a provider error will not write.
		idle_since="$(field "$store" '.verifying.idle_since // empty')"
		case "$state" in
		blocked)
			if [ "$(field "$store" .verifier.kind)" = prime-agent ] && [ -n "$(verifier_provider_error "$store" "$name" "$session")" ]; then
				break
			fi
			[ -z "$idle_since" ] || update_verifying "$store" "$verdict" 'del(.verifying.idle_since)' ;;
		idle | "done")
			[ -z "$(verifier_provider_error "$store" "$name" "$session")" ] || break
			if [ -n "$(busy_command "$(field "$store" .verifier.pane_id)" "$(field "$store" .verifying.started)")" ]; then
				[ -z "$idle_since" ] || update_verifying "$store" "$verdict" 'del(.verifying.idle_since)'
			elif [ -z "$idle_since" ]; then
				update_verifying "$store" "$verdict" '.verifying.idle_since = $at' --argjson at "$now"
			elif [ $(( now - idle_since )) -ge "${KITCHEN_VERIFY_IDLE_S:-600}" ]; then
				stalled=1
				break
			fi ;;
		*) [ -z "$idle_since" ] || update_verifying "$store" "$verdict" 'del(.verifying.idle_since)' ;;
		esac
		sleep "${KITCHEN_VERIFY_POLL_S:-10}"
	done
	verification_still_open "$store" "$verdict"
	# A verifier still alive at the deadline is asked once for what it proved.
	if { [ ! -f "$verdict" ] || [ -z "$(header_field "$verdict" status)" ]; } && [ "$state" != absent ] &&
		[ "$(date +%s)" -ge "$(field "$store" .verifying.deadline)" ] && [ -z "$(verifier_provider_error "$store" "$name" "$session")" ]; then
		verify_grace "$store" "$name" "$kind" "$verdict" "$until" "$brief" "$session"
		verification_still_open "$store" "$verdict"
	fi
	# A verdict just written may still be closing; give the writer a moment.
	[ ! -f "$verdict" ] || sleep "${KITCHEN_VERDICT_SETTLE_S:-3}"
	verification_still_open "$store" "$verdict"
	[ -f "$verdict" ] || provider="$(verifier_provider_error "$store" "$name" "$session")"
	run_close "$store" "$name" "$kind" "$pane" "$verdict" || { printf 'verification closed elsewhere\n'; exit 5; }
	# The wait may have been long (the verifier's exit); the claim must still be ours before anything is written.
	still_my_claim "$store"
	# The attempt's outcome is written to its packet before .verifying goes and
	# before any fallback starts, so fleet can tell attempts apart.
	local problem="" status=""
	if [ -f "$verdict" ]; then
		problem="$(verdict_problem "$verdict")"
		status="$(header_field "$verdict" status)"
		[ -z "$problem" ] || status=invalid
		set_outcome "$packet" "$status"
	elif [ -n "$provider" ]; then
		set_outcome "$packet" provider
	elif [ "$stalled" -eq 1 ]; then
		set_outcome "$packet" stalled
	else
		set_outcome "$packet" missing
	fi
	drop_verifying "$store" "$verdict"
	[ "$status" = clean ] || [ "$status" = reject ] || verifier_trail "$session" "$packet"
	if [ ! -f "$verdict" ] && [ -n "$provider" ]; then
		printf 'verdict: none\nstatus: inconclusive (the verifier hit its provider, not the work)\nprovider_error: %s\n' "$provider"
		if [ "$retried" = false ] && [ -n "$fallback" ] && [ "$(jq -r .kind <<<"$fallback")" != "$kind" ] && [ "${#argv[@]}" -gt 0 ]; then
			printf 'retry: verifying again on the fallback, %s\n' "$(jq -r .kind <<<"$fallback")"
			exec "$here/kitchen.sh" verify "$store" "${argv[@]}" --retry-entry "$fallback"
		fi
		event "$store" master wake "$brief" "verify:provider"
		# A retried call carries its entry; the advice is for the master's own call.
		local -a shown=()
		local i
		for ((i = 0; i < ${#argv[@]}; i++)); do
			if [ "${argv[i]}" = --retry-entry ]; then i=$((i + 1)); else shown+=("${argv[i]}"); fi
		done
		printf 'next: wait for the limit, or kitchen.sh verify %s %s --kind <another kind>\n' "$store" "${shown[*]}"
		exit 2
	fi
	if [ ! -f "$verdict" ] && [ "$stalled" -eq 1 ]; then
		event "$store" master wake "$brief" "verify:stalled"
		local -a again=()
		local j
		for ((j = 0; j < ${#argv[@]}; j++)); do
			if [ "${argv[j]}" = --retry-entry ]; then j=$((j + 1)); else again+=("${argv[j]}"); fi
		done
		die "no verdict at $verdict: the verifier sat idle $(( ${KITCHEN_VERIFY_IDLE_S:-600} / 60 ))m with nothing running; rerun: kitchen.sh verify $store ${again[*]}" 4
	fi
	if [ ! -f "$verdict" ]; then
		event "$store" master wake "$brief" "verify:missing"
		die "no verdict at $verdict (verifier ${state:-gone}); read the packet and verify by hand, or rerun" 4
	fi
	event "$store" master verify "$brief" "$status"
	[ -n "${closing:-}" ] || event "$store" master wake "$brief" "verify:${status%% *}"
	printf 'verdict: %s\nstatus: %s%s\n' "$verdict" "$status" "${problem:+ ($problem)}"
	case "$status" in
	clean)
		if [ "$risk" = routine ] && audit_due "$unit" "$sample"; then
			printf 'audit: yes; read the review-delta yourself before accepting, and kitchen.sh catch what the kitchen missed\n'
		else
			printf 'audit: no\n'
		fi
		warn_unreviewed "$store"
		handoff_due "$store" "$unit"
		exit 0 ;;
	reject)
		awk '/^## Findings/{p=1; next} /^## /{p=0} p' "$verdict" | sed '/^$/d' | head -20
		local rejects
		rejects="$(grep -l '^status: reject' "$store/verdicts/$unit"-v[0-9]*.md 2>/dev/null | grep -vc packet || true)"
		if [ "$unit" = landing ]; then
			printf 'next: kitchen.sh revise %s %s, then kitchen.sh verify %s --landing again\n' "$store" "$verdict" "$store"
		elif [ "$rejects" -ge 2 ]; then
			printf 'next: second rejection of %s; read the verdict and the unit yourself before another fix\n' "$unit"
		else
			printf 'next: kitchen.sh revise %s %s, then verify the fix with --covers %s\n' "$store" "$verdict" "${unit:0:3}"
		fi
		exit 2 ;;
	*)
		printf 'next: read the verdict; an inconclusive or invalid one is the master'"'"'s to settle\n'
		exit 2 ;;
	esac
}

# A fix brief from a rejected verdict, with the Scope, Acceptance, Verify and
# Forbidden of the briefs it fixes. A unit verdict fixes its last unit; a
# landing verdict fixes the stack, so its brief takes the union of the units'
# Scopes and is proved again with verify --landing.
# Every clean unit verdict counts toward the master's next handoff; after
# KITCHEN_MASTER_ROTATE_UNITS (default 5) the verdict says to rotate, at the
# threshold and at each multiple of it. A gates-mode unit counts too.
handoff_due() {
	local store="$1" n thr
	[ "$2" != landing ] || return 0
	json_update "$store" '.master.units_since_handoff = ((.master.units_since_handoff // 0) + 1)'
	n="$(field "$store" .master.units_since_handoff)"
	# Once at the threshold and at each further multiple of it.
	thr="${KITCHEN_MASTER_ROTATE_UNITS:-5}"
	[ "$thr" -ge 1 ] || thr=1
	[ $(( n % thr )) -eq 0 ] || return 0
	printf 'master: %s units verified since your last handoff; rotate at this boundary: kitchen.sh new-handoff %s, fill it, kitchen.sh rotate-master %s\n' "$n" "$store" "$store"
}

cmd_revise() {
	[ $# -eq 2 ] || die "usage: kitchen.sh revise <store> <verdict-path>"
	local store="$1" verdict="$2" units slug path b u landing=0
	local -a briefs=()
	[ -f "$verdict" ] || die "verdict not found: $verdict"
	[ "$(header_field "$verdict" status)" = reject ] || die "only a rejected verdict needs a fix brief"
	units="$(header_field "$verdict" units)"
	if [[ "$units" == landing* ]]; then
		landing=1
		for u in $(grep -oE '[0-9]{3}' <<<"$units" || true); do
			b="$(unit_brief "$store" "$u")"
			briefs+=("$b")
		done
		if [ "${#briefs[@]}" -eq 0 ]; then
			for b in "$store"/briefs/[0-9][0-9][0-9]-*.md; do
				[ -f "$b" ] && ! is_landing "$b" && briefs+=("$b")
			done
		fi
		[ "${#briefs[@]}" -gt 0 ] || die "no unit briefs to fix in $store/briefs"
		slug=landing-fix
	else
		u="$(grep -oE '[0-9]{3}' <<<"$units" | tail -1 || true)"
		[ -n "$u" ] || die "$verdict names no unit (units: $units)"
		b="$(unit_brief "$store" "$u")"
		briefs=("$b")
		slug="$(basename "${briefs[0]}" .md | cut -c5- | sed -E 's/-fix[0-9]*$//')-fix"
	fi
	path="$(new_from_template "$store" "$slug" briefs brief-template.md)"
	VERDICT="$verdict" STORE="$store" LANDING="$landing" python3 - "$path" "${briefs[@]}" <<'PY'
import os, re, sys
path, briefs = sys.argv[1], sys.argv[2:]
verdict, store, landing = os.environ['VERDICT'], os.environ['STORE'], os.environ['LANDING'] == '1'
olds, new = [open(b).read() for b in briefs], open(path).read()
def section(text, name):
    m = re.search(rf'^## {name}\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    return m.group(1) if m else ''
def header(text, key):
    m = re.search(rf'^{key}: (.*)$', text, re.M)
    return m.group(1) if m else ''
def union(lines):
    return list(dict.fromkeys(lines))
def scope(texts):
    may, mustnot = [], []
    for t in texts:
        cur = None
        for line in section(t, 'Scope').splitlines():
            if line.startswith('may write:'):
                cur = may
            elif line.startswith('must not write:'):
                cur = mustnot
            elif cur is not None and line.startswith('- '):
                cur.append(line)
    return '\nmay write:\n' + '\n'.join(union(may)) + '\n\nmust not write:\n' + '\n'.join(union(mustnot) or ['- none']) + '\n\n'
def lines(texts, name):
    body = union(l for t in texts for l in section(t, name).strip('\n').splitlines() if l.strip())
    return '\n' + '\n'.join(body) + '\n\n'
last = olds[-1]
new = re.sub(r'^playbook: .*$', 'playbook: bug-fix', new, count=1, flags=re.M)
for key in ('timebox', 'commit'):
    new = re.sub(rf'^{key}: .*$', f'{key}: {header(last, key)}', new, count=1, flags=re.M)
new = re.sub(r'^plan: .*$', f'plan: {"none" if landing else header(last, "plan")}', new, count=1, flags=re.M)
if landing:
    fills = {
        'Goal': f'\nFix every finding in the landing verdict {verdict}, so a fresh `kitchen.sh verify {store} --landing` of the stack passes.\n\n',
        'Steps': '\n1. One commit per finding in the verdict: the change, and the check that shows the finding is gone.\n\n',
        'Context': f'\n- verdict: {verdict}\n' + ''.join(f'- unit brief: {b}\n' for b in briefs) + '\n',
        'Scope': scope(olds), 'Acceptance': lines(olds, 'Acceptance'), 'Forbidden': lines(olds, 'Forbidden'),
        'Verify': '\n' + ''.join(section(t, 'Verify').strip('\n') + '\n\n' for t in olds),
    }
else:
    fills = {
        'Goal': f'\nFix every finding in the verifier\'s verdict {verdict}, so a fresh verification of the same acceptance passes.\n\n',
        'Steps': '\n1. One commit per finding in the verdict: the change, and the check that shows the finding is gone.\n\n',
        'Context': f'\n- verdict: {verdict}\n- original brief: {briefs[0]}\n\n',
    }
    for name in ('Scope', 'Acceptance', 'Verify', 'Forbidden'):
        fills[name] = section(last, name)
for name, body in fills.items():
    new = re.sub(rf'(^## {name}\n)(.*?)(?=^## |\Z)', lambda m, b=body: m.group(1) + b, new, count=1, flags=re.M | re.S)
open(path, 'w').write(new)
PY
	printf '%s\n' "$path"
}

# Whether a review note cites a run of the required engine: "engine: <name> <run id or artifact path>".
cites_engine() {
	local cited
	cited="$(header_field "$1" engine)"
	[ "${cited%% *}" = "$2" ] && [ "${cited#* }" != "$cited" ]
}

# The newest landing verdict file in the store, or nothing.
latest_landing_verdict() {
	local f lk best=0 found=""
	for f in "$1"/verdicts/landing-v[0-9]*.md; do
		[ -e "$f" ] && [[ "$f" != *-packet.md ]] || continue
		lk="${f##*-v}"
		lk="${lk%.md}"
		[ "$lk" -le "$best" ] || { best="$lk"; found="$f"; }
	done
	printf '%s' "$found"
}

# Where the stack at HEAD leaves trunk now (the recorded landing parent, else
# the merge-base with trunk), or nothing. $1 store.
current_landing_base() {
	local root parent trunk pc head
	root="$(field "$1" .git_root)"
	head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
	parent="$(field "$1" '.landing.parent // empty')"
	if [ -n "$parent" ]; then
		pc="$(git -C "$root" rev-parse --verify --quiet "$parent^{commit}" 2>/dev/null || true)"
		if [ -n "$pc" ] && [ "$pc" != "$head" ] && git -C "$root" merge-base --is-ancestor "$pc" "$head" 2>/dev/null; then
			printf '%s\n' "$pc"
			return 0
		fi
		printf 'landing base: recorded parent %s (%s) is no longer an ancestor of %s; using the trunk merge-base\n' \
			"$(field "$1" '.landing.parent_ref // empty')" "${parent:0:9}" "${head:0:9}" >&2
	fi
	trunk="$(trunk_ref "$root")"
	[ -z "$trunk" ] || git -C "$root" merge-base "$trunk" HEAD 2>/dev/null || true
}

# The base and head a landing verdict used, as "base head" (the refs as written): its packet's range
# (written by the kitchen, full shas), else the range line the verifier copied into the verdict, with
# no words after the head. Coverage and the manifest warning both read it. $1 verdict.
verdict_range() {
	local packet="${1%.md}-packet.md" range
	range="$(header_field "$packet" range 2>/dev/null || true)"
	[ -n "$range" ] || range="$(header_field "$1" range)"
	range="${range%%[[:space:]]*}"
	printf '%s %s\n' "${range%%..*}" "${range##*..}"
}

# When the stack was rebased off the base a landing was verified on, lists the dependency manifests
# (kitchen.toml escalate.manifests, matched by kitchen.py) that differ between that base and the
# current one, whichever side changed them. A verified base above the current one and in the head's
# history (a --base inside the stack) is the stack's own lower commits, not a moved trunk: no warning.
# $1 store, $2 verified base, $3 current base, $4 the head being landed (default HEAD).
manifest_warning() {
	local root files
	[ -n "$2" ] && [ -n "$3" ] && [ "$2" != "$3" ] || return 0
	root="$(field "$1" .git_root)"
	if git -C "$root" merge-base --is-ancestor "$3" "$2" 2>/dev/null && git -C "$root" merge-base --is-ancestor "$2" "${4:-HEAD}" 2>/dev/null; then
		return 0
	fi
	files="$(git -C "$root" diff --name-only -z --no-renames "$2" "$3" 2>/dev/null | kpy "$1" --json manifests -z 2>/dev/null | jq -r '.manifests | join(", ")' 2>/dev/null || true)"
	[ -z "$files" ] || printf 'warning: dependency manifests changed since the verified base: %s\n' "$files"
}

# Whether the unit's range only deletes: every changed path is deleted, or modified by removing
# lines only (no line added) with its mode unchanged. It reads `git diff --raw -z --no-renames` (the
# status and both modes of each path) and `--numstat -z` from the merge-base of the dispatch head and
# the unit head, the range classify reads: NUL-separated, so a path is never quoted, and a rename is
# a delete plus an add, which refuses. A mode change refuses; so does a modified path the numstat
# does not account for; a binary file modified is a change, deleted it is not. Any failing git
# command refuses (the output goes to files, so no pipe or substitution swallows a status).
# $1 store, $2 brief.
unit_adds_no_lines() {
	local root base head record path names nums ok=1 added deleted rest m1 m2 status
	local -A modified=() counted=()
	root="$(field "$1" .git_root)"
	base="$(jq -r --arg u "$(basename "$2" .md)" '.heads[$u] // empty' "$1/pair.json")"
	head="$(unit_head "$1" "$2")"
	[ -n "$base" ] && [ -n "$head" ] || return 1
	git -C "$root" rev-parse --verify --quiet "$base^{commit}" >/dev/null || return 1
	names="$(mktemp)"
	nums="$(mktemp)"
	if git -C "$root" diff --raw -z --no-renames "$base...$head" >"$names" 2>/dev/null &&
		git -C "$root" diff --numstat -z --no-renames "$base...$head" >"$nums" 2>/dev/null; then
		while IFS= read -r -d '' record && IFS= read -r -d '' path; do
			# ":<old mode> <new mode> <old sha> <new sha> <status>"
			read -r m1 m2 _ _ status <<<"${record#:}"
			case "$status" in
			D) ;;
			M)
				[ "$m1" = "$m2" ] || ok=0
				modified[$path]=1 ;;
			*) ok=0 ;;
			esac
		done <"$names"
		while IFS= read -r -d '' record; do
			added="${record%%$'\t'*}"
			rest="${record#*$'\t'}"
			deleted="${rest%%$'\t'*}"
			path="${rest#*$'\t'}"
			[ -n "${modified[$path]:-}" ] || continue
			counted[$path]=1
			[[ "$added" =~ ^[0-9]+$ ]] && [[ "$deleted" =~ ^[0-9]+$ ]] && [ "$added" -eq 0 ] && [ "$deleted" -gt 0 ] || ok=0
		done <"$nums"
		for path in "${!modified[@]}"; do
			[ -n "${counted[$path]:-}" ] || ok=0
		done
	else
		ok=0
	fi
	rm -f "$names" "$nums"
	[ "$ok" -eq 1 ]
}

# Whether the review note meets the required engine: it cites a run, or it says
# `engine: skipped (deletion-only, proof: <command>)` and the unit's range adds
# no lines. On failure, $engine_gap says why when the note tried to skip.
# $1 store, $2 brief, $3 review note, $4 engine.
engine_met() {
	engine_gap=""
	cites_engine "$3" "$4" && return 0
	if [[ "$(header_field "$3" engine)" =~ ^skipped\ \(deletion-only,\ proof:\ .+\)$ ]]; then
		unit_adds_no_lines "$1" "$2" && return 0
		engine_gap="; skipped is only for a deletion-only range"
	fi
	return 1
}

# Prints a "warning: unreviewed:" line for each accepted unit whose review note
# lacks the run the kitchen's [review].engine requires; nothing when no engine
# is set. land-check holds the landing on the same lines.
warn_unreviewed() {
	local store="$1" engine r seq brief
	engine="$(kpy "$store" --json validate 2>/dev/null | jq -r '.review_engine // empty' 2>/dev/null || true)"
	[ -n "$engine" ] || return 0
	for r in "$store"/reviews/[0-9][0-9][0-9]-*.md; do
		[ -e "$r" ] || continue
		[ "$(header_field "$r" verdict)" = accept ] || continue
		seq="$(basename "$r" | cut -c1-3)"
		brief="$(ls "$store"/briefs/"$seq"-*.md 2>/dev/null | head -1 || true)"
		[ -n "$brief" ] && [ "$(header_field "$brief" commit | cut -c1-3)" = yes ] || continue
		is_landing "$brief" && continue
		engine_met "$store" "$brief" "$r" "$engine" ||
			printf 'warning: unreviewed: unit %s cites no %s run; review its range with %s and add "engine: %s <run id or artifact path>" to %s%s\n' \
				"$seq" "$engine" "$engine" "$engine" "$r" "$engine_gap"
	done
}

# Why a unit outside a clean landing verdict's range could not be matched by
# patch-id, as one land-check note. $1 store, $2 unit NNN, $3 brief, $4 unit head,
# $5 landing verdict, $6 its base, $7 its head.
landing_unmatched_note() {
	local root ubase
	root="$(field "$1" .git_root)"
	ubase="$(jq -r --arg u "$(basename "$3" .md)" '.heads[$u] // empty' "$1/pair.json")"
	if [ -n "$6" ] && git -C "$root" merge-base --is-ancestor "$4" "$6" 2>/dev/null; then
		printf 'land-check: note: unit %s landed before the base of landing verdict %s; verify it on its own\n' "$2" "$5"
	elif [ "$rebased_why" = merge ]; then
		printf 'land-check: note: unit %s'"'"'s range holds a merge commit, so patch-ids do not cover it; verify --landing covers it\n' "$2"
	elif [ "$rebased_why" = reverted ]; then
		printf 'land-check: note: unit %s'"'"'s commits match by patch-id but %s reverts them\n' "$2" "${rebased_by:0:9}"
	elif [ -z "$6" ]; then
		printf 'land-check: note: landing verdict %s has no base that resolves; unit %s cannot be matched by patch-id; verify --landing covers it\n' "$5" "$2"
	elif [ -n "$ubase" ] && git -C "$root" merge-base --is-ancestor "$7" "$ubase" 2>/dev/null; then
		printf 'land-check: note: unit %s is newer than landing verdict %s; verify --landing again\n' "$2" "$5"
	else
		printf 'land-check: note: unit %s'"'"'s head is not in the stack and its commits do not match by patch-id (squashed?); verify --landing covers it\n' "$2"
	fi
}

land_check() {
	# Prints one line per problem; returns 1 when there is any. A unit with no
	# clean verdict of its own is covered by the latest landing verdict when
	# that one is clean and its range's head contains the unit's head. A
	# kitchen that names a review engine requires each accepted unit's review
	# note to cite a run of it: "engine: <name> <run id or artifact path>".
	local store="$1" r seq brief verdict f problems=0 uhead engine
	local landing_verdict="" landing_head="" landing_base="" lk best=0 behind vhead verdict_base_ref verdict_head_ref verdict_base_rev verdict_head_rev
	engine="$(kpy "$store" --json validate 2>/dev/null | jq -r '.review_engine // empty')" ||
		{ printf 'kitchen: validate fails (kitchen.py validate shows why)\n'; return 1; }
	for f in "$store"/verdicts/landing-v[0-9]*.md; do
		[ -e "$f" ] && [[ "$f" != *-packet.md ]] || continue
		lk="${f##*-v}"
		lk="${lk%.md}"
		[ "$lk" -le "$best" ] || { best="$lk"; landing_verdict="$f"; }
	done
	if [ -n "$landing_verdict" ]; then
		read -r verdict_base_ref verdict_head_ref <<<"$(verdict_range "$landing_verdict")"
		verdict_base_rev="$(git -C "$(field "$store" .git_root)" rev-parse --verify --quiet "$verdict_base_ref^{commit}" 2>/dev/null || true)"
		verdict_head_rev="$(git -C "$(field "$store" .git_root)" rev-parse --verify --quiet "$verdict_head_ref^{commit}" 2>/dev/null || true)"
	fi
	if [ -n "$landing_verdict" ] && [ "$(header_field "$landing_verdict" status)" = clean ]; then
		landing_head="$verdict_head_rev"
		landing_base="$verdict_base_rev"
		[ -n "$landing_head" ] || printf 'land-check: note: landing verdict %s range head does not resolve; no unit is covered by it\n' "$landing_verdict"
	fi
	for r in "$store"/reviews/[0-9][0-9][0-9]-*.md; do
		[ -e "$r" ] || continue
		[ "$(header_field "$r" verdict)" = accept ] || continue
		seq="$(basename "$r" | cut -c1-3)"
		brief="$(ls "$store"/briefs/"$seq"-*.md 2>/dev/null | head -1 || true)"
		[ -n "$brief" ] && [ "$(header_field "$brief" commit | cut -c1-3)" = yes ] || continue
		is_landing "$brief" && continue
		if [ -n "$engine" ]; then
			if ! engine_met "$store" "$brief" "$r" "$engine"; then
				printf 'unreviewed: unit %s cites no %s run; review its range with %s and add "engine: %s <run id or artifact path>" to %s%s\n' \
					"$seq" "$engine" "$engine" "$engine" "$r" "$engine_gap"
				problems=$((problems + 1))
			fi
		fi
		verdict=""
		for f in "$store"/verdicts/*-v[0-9]*.md; do
			[ -e "$f" ] && [[ "$f" != *-packet.md ]] || continue
			grep -qE "^units:(.* )?$seq( |$)" "$f" && [ "$(header_field "$f" status)" = clean ] && verdict="$f"
		done
		if [ -z "$verdict" ] && [ -n "$landing_head" ]; then
			uhead="$(unit_head "$store" "$brief")"
			if [ -n "$uhead" ]; then
				if unit_in_landing_range "$store" "$brief" "$uhead" "$landing_base" "$landing_head"; then
					verdict="$landing_verdict"
					if [ -n "$rebased_sha" ]; then
						printf 'covered: unit %s (rebased as %s) by the clean landing verdict %s\n' "$seq" "${rebased_sha:0:9}" "$landing_verdict"
					else
						printf 'covered: unit %s by the clean landing verdict %s\n' "$seq" "$landing_verdict"
					fi
				else
					landing_unmatched_note "$store" "$seq" "$brief" "$uhead" "$landing_verdict" "$landing_base" "$landing_head"
				fi
			fi
		fi
		if [ -z "$verdict" ]; then
			printf 'unverified: unit %s has no clean verdict (kitchen.sh verify %s %s)\n' "$seq" "$store" "$seq"
			problems=$((problems + 1))
		fi
	done
	# A landing verification is reported, not required.
	if [ -n "$landing_verdict" ]; then
		manifest_warning "$store" "$verdict_base_rev" "$(current_landing_base "$store")"
		printf 'landing verification: %s (%s)\n' "$(header_field "$landing_verdict" status)" "$landing_verdict"
		vhead="$verdict_head_rev"
		if [ -n "$vhead" ]; then
			if git -C "$(field "$store" .git_root)" merge-base --is-ancestor "$vhead" HEAD 2>/dev/null; then
				behind="$(git -C "$(field "$store" .git_root)" rev-list --count "$vhead..HEAD" 2>/dev/null || true)"
				[ "${behind:-0}" -eq 0 ] || printf 'landing verdict %s at %s, %s commit%s behind HEAD\n' "$landing_verdict" "${vhead:0:9}" "$behind" "$([ "$behind" -eq 1 ] || printf s)"
			else
				printf 'landing verdict %s at %s, not in HEAD'"'"'s history (rebased or another branch); verify --landing again\n' "$landing_verdict" "${vhead:0:9}"
			fi
		fi
	else
		printf 'landing verification: none; kitchen.sh verify %s --landing proves the stack tip with a fresh verifier\n' "$store"
	fi
	[ "$problems" -eq 0 ] || return 1
	printf 'land-check: pass (review engine: %s)\n' "${engine:-none}"
}

cmd_land_check() {
	[ $# -eq 1 ] || die "usage: kitchen.sh land-check <store>"
	pair_file "$1" >/dev/null
	land_check "$1" || exit 2
}

cmd_catch() {
	[ $# -eq 3 ] || [ $# -eq 4 ] || die "usage: kitchen.sh catch <store> <layer> <text> [<unit>]"
	local store="$1" layer="$2" text="$3" unit="${4:-}"
	case "$layer" in gate | policy | self | verify | review | triage | escalation) ;; *) die "layer is gate, policy, self, verify, review, triage, or escalation: the one that should have caught it" ;; esac
	pair_file "$store" >/dev/null
	[ -z "$unit" ] || [[ "$unit" =~ ^[0-9]{3}$ ]] || die "unit is a brief's number, like 003"
	# Options first, then --, so a text that starts with - stays text.
	python3 "$here/kitchen.py" ledger-catch --run "$(field "$store" .slug)" --ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		${unit:+--unit "$unit"} -- "$store" "$(kpy "$store" statedir)/ledger.tsv" "$layer" "$text" || exit 1
	event "$store" master catch - "$layer"
	printf 'caught at %s: %s\n' "$layer" "$text"
}

# The run's counts, read-only and from the same function fleet uses: sets
# the rc_* globals. retro prints them and feedback quotes the same line.
retro_counts() {
	local c
	c="$(python3 "$here/kitchen.py" counters "$1")"
	rc_units="$(jq .done_units <<<"$c")"
	rc_verified="$(jq .verified_units <<<"$c")"
	rc_clean_first="$(jq .clean_first_try <<<"$c")"
	rc_wakes="$(jq .wakes <<<"$c")"
	rc_escalations="$(jq .escalations <<<"$c")"
	rc_failovers="$(jq .failovers <<<"$c")"
	rc_repo="$(jq -r .repo <<<"$c")"
}

retro_line() {
	printf 'run %s: %s unit reports, %s verified units (%s clean verdicts on the first try), %s master wakes, %s escalations, %s failovers\n' \
		"$(field "$1" .slug)" "$rc_units" "$rc_verified" "$rc_clean_first" "$rc_wakes" "$rc_escalations" "$rc_failovers"
}

# Where the master was needed, and what repeated. Each repeated class is a
# candidate for the kitchen: a rule, a lint, a profile command, a sharper
# brief. Appends this run's classes to the repo's ledger, then counts across
# runs.
# A finished run's agents hold their memory until someone closes them (about
# a gigabyte per run on this host, idle for days). The master asks the human
# once after the retro; this is the yes.
cmd_close() {
	in_herdr
	[ $# -ge 1 ] || die "usage: kitchen.sh close <store> [--force]"
	local store="$1" force=0 role rc=0
	[ "${2:-}" != --force ] || force=1
	pair_file "$store" >/dev/null
	if [ "$force" -eq 0 ]; then
		[ -z "$(field "$store" '.verifying.verdict // empty')" ] || die "a verification is open ($(field "$store" '.verifying.units')); finish it (kitchen.sh verify $store --wait) or pass --force" 5
		[ "$(agent_status "$(field "$store" .sidekick.name)")" != working ] || die "the sidekick is working; kitchen.sh stop $store first, or pass --force" 5
	fi
	for role in verifier consultant sidekick; do
		close_role "$store" "$role" || rc=1
	done
	[ "$rc" -eq 0 ] || exit 5
}

# The master's own session grows with every diff and file it reads, and
# every model call re-reads all of it: on this host a master that served
# several runs re-read 500-900k tokens per call, at 27-31 calls per unit. A
# handoff lets the master end its session at a unit boundary and start a
# fresh one in the same pane, which reads the handoff and the store instead
# of the history. new-handoff writes the template with the units' state
# filled in; rotate-master records the filled handoff and the session it
# closes, then hands the run to a new master beside it.
cmd_new_handoff() {
	[ $# -eq 1 ] || die "usage: kitchen.sh new-handoff <store>"
	local store="$1" path gen units
	pair_file "$store" >/dev/null
	path="$store/session-handoff.md"
	gen="$(field "$store" '.master.generation // 1')"
	units="$(handoff_units "$store")"
	UNITS="$units" python3 - "$skill_root/references/handoff-template.md" "$path" "$(field "$store" .slug)" "$store" "$gen" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<'PY'
import os, sys
src, dst, slug, store, gen, now = sys.argv[1:7]
text = open(src).read()
for k, v in {'SLUG': slug, 'STORE': store, 'GENERATION': gen, 'WRITTEN': now, 'UNITS': os.environ['UNITS']}.items():
    text = text.replace('{{' + k + '}}', v)
open(dst, 'w').write(text)
PY
	printf '%s\n' "$path"
	printf 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master %s\n' "$store"
}

# One row per unit: brief, report status, latest verdict, review verdict.
handoff_units() {
	local store="$1" b seq slug report rstatus verdict vstatus review rv
	printf '| unit | report | verdict | review |\n|---|---|---|---|\n'
	for b in "$store"/briefs/[0-9][0-9][0-9]-*.md; do
		[ -e "$b" ] || continue
		seq="$(basename "$b" | cut -c1-3)"
		slug="$(basename "$b" .md | cut -c5-)"
		rstatus=-
		report="$(ls -t "$store"/reports/"$seq"-*.md 2>/dev/null | head -1 || true)"
		[ -z "$report" ] || rstatus="$(header_field "$report" status)"
		vstatus=-
		verdict="$(ls -t "$store"/verdicts/"$seq"-*-v[0-9]*.md 2>/dev/null | grep -v packet | head -1 || true)"
		[ -z "$verdict" ] || vstatus="$(header_field "$verdict" status)"
		rv=-
		review="$store/reviews/$seq-$slug.md"
		[ ! -f "$review" ] || rv="$(header_field "$review" verdict)"
		printf '| %s %s | %s | %s | %s |\n' "$seq" "$slug" "${rstatus:--}" "${vstatus:--}" "${rv:--}"
	done
	[ -z "$(field "$store" '.dispatch.brief // empty')" ] || printf '\ndispatched: %s\n' "$(basename "$(field "$store" .dispatch.brief)")"
	[ -z "$(field "$store" '.verifying.unit // empty')" ] || printf 'verifying: %s\n' "$(field "$store" .verifying.unit)"
	[ -z "$(queued_brief "$store")" ] || printf 'queued: %s\n' "$(basename "$(queued_brief "$store")")"
}

# The --model the master's own Claude was started with, so the next
# generation runs the same model; empty when none is on its command line.
detect_model_arg() {
	local pid=$$ ppid comm args depth=0
	while [ "$pid" -gt 1 ] && [ "$depth" -lt 12 ]; do
		ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')" || break
		[ -n "$ppid" ] || break
		comm="$(basename "$(ps -o comm= -p "$pid" 2>/dev/null)")"
		args="$(ps -o args= -p "$pid" 2>/dev/null)"
		case "$comm" in node | bun) case "$args" in *claude*) comm=claude ;; esac ;; esac
		if [ "$comm" = claude ]; then
			sed -nE 's/.*--model[= ]([^ ]+).*/\1/p' <<<"$args"
			return 0
		fi
		pid="$ppid"
		depth=$((depth + 1))
	done
}

# A hand-over, not a restart in place: the closing master starts the next
# generation in a pane beside its own and points pair.json at it, and the new
# master's first command, take-master, closes the old pane. The old session
# never has to exit itself (a Claude with background tasks answers /exit with
# a dialog), and a start that fails leaves the old master in charge.
cmd_rotate_master() {
	in_herdr
	[ $# -ge 1 ] || die "usage: kitchen.sh rotate-master <store> [--model MODEL]"
	local store="$1" model="" name pane kind gen next session handoff permission cwd temp
	shift
	while [ $# -gt 0 ]; do
		case "$1" in
		--model) model="$2"; shift 2 ;;
		*) die "unknown option $1" ;;
		esac
	done
	pair_file "$store" >/dev/null
	handoff="$store/session-handoff.md"
	[ -f "$handoff" ] || die "no handoff; write one first: kitchen.sh new-handoff $store" 5
	require_filled "$handoff"
	for s in Goal Next; do
		[ -n "$(awk -v h="## $s" '$0 == h {p=1; next} /^## /{p=0} p && NF' "$handoff")" ] || die "$handoff: the $s section is empty" 5
	done
	name="$(field "$store" .master.name)"
	pane="$(field "$store" .master.pane_id)"
	[ -z "$(field "$store" '.master.previous.name // empty')" ] ||
		die "a hand-over is open: $name has not taken over yet; it runs kitchen.sh take-master $store first" 5
	[ "$(agent_status "$name")" != absent ] || die "master $name is not live in $pane; run this from the master's own session" 5
	kind="$(agent_kind "$name")"
	[ "$kind" = claude ] || die "rotate-master restarts a Claude master; $name is $kind" 5
	[ -n "$model" ] || model="$(detect_model_arg)"
	session="$(herdr agent get "$name" 2>/dev/null | jq -r '.result.agent.agent_session.value // empty' 2>/dev/null || true)"
	gen="$(field "$store" '.master.generation // 1')"
	# A rotation that never finished already recorded this session; it keeps
	# that generation instead of skipping one.
	if [ -n "$session" ] && [ "$session" = "$(field "$store" '.master.sessions[-1].session // empty')" ]; then
		gen="$(field "$store" '.master.sessions[-1].generation')"
	fi
	next=$((gen + 1))
	# Herdr names are unique, so the new master holds a generation name until
	# take-master frees the run's own.
	temp="$(field "$store" .slug)-master-g$next"
	permission="$(field "$store" '.master.permission_mode // empty')"
	[ -n "$permission" ] || permission="$(detect_permission_mode)"
	local -a args=()
	mapfile -t args < <(permission_args claude "$permission")
	[ -z "$model" ] || args+=(--model "$model")
	mapfile -t -O "${#args[@]}" args < <(mcp_args claude)
	[ "${KITCHEN_MASTER_REMOTE_CONTROL:-1}" = 0 ] || args+=(--remote-control "$temp")
	cwd="$(field "$store" .cwd)"
	new_pane "$store" master "$pane" "" "$cwd"
	if ! herdr agent start "$temp" --kind claude --pane "$new_pane_id" --timeout 60000 -- "${args[@]}" >/dev/null 2>&1; then
		refuse_untrusted "$new_pane_id" claude "$cwd" close
		herdr pane close "$new_pane_id" >/dev/null 2>&1 || true
		event "$store" master rotate-failed - "generation:$next"
		die "the next master did not start in $new_pane_id; you stay the master (generation $gen), so carry on and rotate at a later boundary" 2
	fi
	mkdir -p "$store/sessions"
	cp "$handoff" "$store/sessions/master-$gen-handoff.md"
	json_update "$store" --arg s "$session" --arg h "$store/sessions/master-$gen-handoff.md" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		--argjson g "$gen" --arg old "$name" --arg oldpane "$pane" --arg new "$temp" --arg newpane "$new_pane_id" \
		'.master.previous = {name: $old, pane_id: $oldpane, session: $s}
		 | .master.name = $new | .master.pane_id = $newpane | .master.registered_at = $now
		 | .master.generation = ($g + 1) | .master.units_since_handoff = 0
		 | .master.sessions = ([(.master.sessions // [])[] | select($s == "" or .session != $s)]
		     + [{generation: $g, session: $s, handoff: $h, at: $now}])'
	event "$store" master handoff - "generation:$gen"
	event "$store" master spawn - "claude:generation:$next"
	herdr agent prompt "$temp" "Load the $PAIR_SKILL skill from ~/.agents/skills/$PAIR_SKILL/SKILL.md and take the master role: $PAIR_SKILL MASTER $store (generation $next). Run ~/.agents/skills/$PAIR_SKILL/scripts/kitchen.sh take-master $store first, then read $store/session-handoff.md." \
		--wait --until working --timeout 30000 >/dev/null 2>&1 || true
	submit_typed "$temp" "take-master"
	printf 'handoff recorded: %s (generation %s, session %s)\n' "$store/sessions/master-$gen-handoff.md" "$gen" "${session:-unknown}"
	printf 'next master: %s (generation %s) in %s%s; it closes this pane with take-master\n' \
		"$temp" "$next" "$new_pane_id" "$([ "${KITCHEN_MASTER_REMOTE_CONTROL:-1}" = 0 ] || printf ', remote control on')"
	printf 'next: end your turn now, with no further commands, and act on no later prompt or task notification: generation %s owns the run. Resume this session later with: claude --resume %s\n' \
		"$next" "${session:-<id>}"
}

# The new master's first command after a hand-over: give the old master a
# moment to end its turn, close its pane (and with it any background waits it
# left running), and take back the run's master name. Safe to repeat.
cmd_take_master() {
	in_herdr
	[ $# -eq 1 ] || die "usage: kitchen.sh take-master <store>"
	local store="$1" name pane prev prev_pane host base deadline closed=kept
	pair_file "$store" >/dev/null
	name="$(field "$store" .master.name)"
	pane="$(field "$store" .master.pane_id)"
	prev="$(field "$store" '.master.previous.name // empty')"
	[ -n "$prev" ] || { printf 'no hand-over open; %s is the master in %s\n' "$name" "$pane"; return 0; }
	[ "${HERDR_PANE_ID:-}" = "$pane" ] || die "take-master runs from the new master's pane $pane, not ${HERDR_PANE_ID:-an unknown pane}" 5
	prev_pane="$(field "$store" .master.previous.pane_id)"
	deadline=$(( $(date +%s) + ${KITCHEN_TAKEOVER_WAIT_S:-30} ))
	while [ "$(agent_status "$prev")" = working ] && [ "$(date +%s)" -lt "$deadline" ]; do
		sleep "${KITCHEN_ROTATE_POLL_S:-2}"
	done
	# Close the pane only while it still holds the old master, or nothing.
	host="$(herdr agent get "$prev_pane" 2>/dev/null | jq -r '.result.agent.name // empty' 2>/dev/null || true)"
	if ! herdr pane get "$prev_pane" >/dev/null 2>&1; then
		closed=gone
	elif [ -z "$host" ] || [ "$host" = "$prev" ]; then
		herdr pane close "$prev_pane" >/dev/null 2>&1 && closed=closed
	fi
	base="$(field "$store" .slug)-master"
	if [ "$name" != "$base" ] && herdr agent rename "$pane" "$base" >/dev/null 2>&1; then
		name="$base"
	fi
	json_update "$store" --arg n "$name" '.master.name = $n | del(.master.previous)'
	event "$store" master takeover - "generation:$(field "$store" .master.generation)"
	case "$closed" in
	closed) printf 'closed the old master %s (%s)\n' "$prev" "$prev_pane" ;;
	gone) printf 'the old master pane %s is already gone\n' "$prev_pane" ;;
	*) printf 'left pane %s open: it now holds %s, not the old master %s\n' "$prev_pane" "$host" "$prev" ;;
	esac
	printf 'master: %s in %s (generation %s)\n' "$name" "$pane" "$(field "$store" .master.generation)"
	warn_unreviewed "$store"
}

cmd_retro() {
	[ $# -eq 1 ] || die "usage: kitchen.sh retro <store>"
	local store="$1" ledger run now
	pair_file "$store" >/dev/null
	ledger="$(kpy "$store" statedir)/ledger.tsv"
	run="$(field "$store" .slug)"
	now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	python3 "$here/kitchen.py" ledger-retro "$store" "$ledger" --run "$run" --ts "$now"
	retro_counts "$store"
	retro_line "$store"
	[ "$rc_verified" -eq 0 ] || printf 'master wakes per verified unit: %s\n' "$(awk -v w="$rc_wakes" -v v="$rc_verified" 'BEGIN{printf "%.1f", w / v}')"
	printf 'repeated across runs (the kitchen should catch these, not the master):\n'
	# Lesson scores, staged lessons and dismissals are the lessons report's, below.
	awk -F '\t' 'NR > 1 && $3 !~ /^(lesson|lesson-added|dismiss)$/ {k = $3 "\t" $4; n[k]++; if (!((k, $2) in seen)) {seen[k, $2] = 1; runs[k]++}}
		END {for (k in n) if (n[k] >= 2) {split(k, p, "\t"); printf "  %3d %-10s %-24s in %d runs\n", n[k], p[1], p[2], runs[k]}}' "$ledger" | sort -rn || true
	cat <<'HINT'
encode a repeated class at the highest level that works (the correct skill):
  gate-fail <profile>  a sharper brief, or a lint whose error names the fix
  gate-flaky <profile> a suite that fails under load: related tests per step, the full suite in behavioral
  reject verify        acceptance the gates do not cover: add a behavioral command or test
  steer brief          briefs miss a decision the master keeps making: a standing order or plan template line
  catch <layer>        the layer that should have caught it gets the check
HINT
	kpy "$store" lessons || true
}

# The host's feedback inbox: gaps in the kitchen itself, filed by masters
# and read by whoever maintains pstack on this host.
fb_root() {
	printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/pstack/feedback"
}

# One short lock around allocating an id and moving a report between
# directories, so concurrent filings and closures never share a name.
fb_lock() {
	local lock="$1/.lock" tries=0
	while ! mkdir "$lock" 2>/dev/null; do
		# A lock left by a killed command is older than any filing takes.
		[ -z "$(find "$lock" -maxdepth 0 -mmin +1 2>/dev/null)" ] || { rmdir "$lock" 2>/dev/null || true; continue; }
		tries=$((tries + 1))
		[ "$tries" -lt 100 ] || die "the feedback inbox is locked ($lock)"
		sleep 0.1
	done
}

fb_unlock() {
	rmdir "$1/.lock" 2>/dev/null || true
}

# Why the registered maintainer cannot take a pointer, or nothing when it can.
maintainer_problem() {
	local file="$1/maintainer.json" name pane session info
	[ -f "$file" ] || { printf 'no maintainer is registered'; return 0; }
	name="$(jq -r .name "$file")"
	pane="$(jq -r .pane "$file")"
	session="$(jq -r '.session // empty' "$file")"
	info="$(herdr agent get "$name" 2>/dev/null)" || { printf 'agent %s is gone' "$name"; return 0; }
	[ "$(jq -r '.result.agent.pane_id // empty' <<<"$info")" = "$pane" ] || { printf 'agent %s moved off pane %s' "$name" "$pane"; return 0; }
	if [ -n "$session" ] && [ "$(jq -r '.result.agent.agent_session.value // empty' <<<"$info")" != "$session" ]; then
		printf 'agent %s is a different session than the one registered' "$name"
		return 0
	fi
	[ "$(jq -r '.result.agent.agent_status // empty' <<<"$info")" != blocked ] || printf 'agent %s is blocked on a dialog' "$name"
}

# Open reports, oldest first: id, run, repo, build, gaps, age.
fb_inbox() {
	local fb f now filed secs age rows=""
	fb="$(fb_root)"
	now="$(date +%s)"
	for f in "$fb"/inbox/*.md; do
		[ -e "$f" ] || continue
		[ ! -e "$fb/done/$(basename "$f")" ] || continue
		filed="$(header_field "$f" filed)"
		secs="$(iso_epoch "$filed" 2>/dev/null || file_mtime "$f")"
		age=$(( (now - secs) / 60 ))
		if [ "$age" -ge 2880 ]; then age="$((age / 1440))d"; elif [ "$age" -ge 120 ]; then age="$((age / 60))h"; else age="${age}m"; fi
		rows+="$secs	$(header_field "$f" id)	$(sed -n 's/^run: \([^ ]*\) .*/\1/p' "$f" | head -1)	$(head -1 "$f" | sed -n 's/^.*(\(.*\))$/\1/p')	$(header_field "$f" build | cut -d' ' -f1)	$(grep -c '^### ' "$f")	$age
"
	done
	if [ -z "$rows" ]; then
		printf 'feedback inbox: empty\n'
		return 0
	fi
	printf '%s' "$rows" | sort -n | cut -f2- | column -t -s "$(printf '\t')"
}

# Move a report to done/ with a Closed block. Closing a closed id prints its
# block and succeeds.
fb_close() {
	[ $# -eq 3 ] || die "usage: kitchen.sh feedback --close <id> <commit|none> <note>"
	local fb id="${1%.md}" commit="$2" note="$3" tmp
	fb="$(fb_root)"
	[ -f "$fb/inbox/$id.md" ] || [ -f "$fb/done/$id.md" ] || die "no feedback report $id in $fb"
	fb_lock "$fb"
	if [ -f "$fb/inbox/$id.md" ] && [ ! -f "$fb/done/$id.md" ]; then
		tmp="$(mktemp "$fb/.closing-XXXXXX")"
		{
			sed 's/^status: open$/status: closed/' "$fb/inbox/$id.md"
			printf '\n## Closed\n\nclosed: %s\ncommit: %s\nnote: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$(printf '%s' "$note" | tr '\n' ' ')"
		} >"$tmp"
		mv "$tmp" "$fb/done/$id.md"
		rm -f "$fb/inbox/$id.md"
		fb_unlock "$fb"
		printf 'closed: %s (commit %s)\n' "$id" "$commit"
		return 0
	fi
	fb_unlock "$fb"
	rm -f "$fb/inbox/$id.md"
	printf 'already closed: %s\n' "$id"
	sed -n '/^## Closed/,$p' "$fb/done/$id.md"
}

# File a report: the master's text behind a header this command writes.
cmd_feedback() {
	[ $# -ge 1 ] || die "usage: kitchen.sh feedback <store> <file> | --inbox | --close <id> <commit|none> <note>"
	case "$1" in
	--inbox) shift; fb_inbox "$@"; return ;;
	--close) shift; fb_close "$@"; return ;;
	esac
	[ $# -eq 2 ] || die "usage: kitchen.sh feedback <store> <file>"
	local store="$1" src="$2" fb run repo stem k name tmp retro path problem first
	pair_file "$store" >/dev/null
	[ -f "$src" ] || die "no such file: $src"
	awk '/^## Gaps/ {g = 1; next} /^## / {g = 0} g && /^### / {found = 1} END {exit !found}' "$src" ||
		die "$src has no gap: a \`### <n>. <title>\` heading under \`## Gaps\` (see $skill_root/references/feedback-template.md)"
	fb="$(fb_root)"
	mkdir -p "$fb/inbox" "$fb/done"
	run="$(field "$store" .slug)"
	retro_counts "$store"
	repo="$rc_repo"
	[ -n "$repo" ] || repo="$(basename "$(field "$store" .git_root)")"
	retro="$(retro_line "$store")"
	stem="$(date -u +%Y%m%d)-$repo-$run"
	tmp="$(mktemp "$fb/.filing-XXXXXX")"
	fb_lock "$fb"
	k=1
	name="$stem"
	while [ -e "$fb/inbox/$name.md" ] || [ -e "$fb/done/$name.md" ]; do
		k=$((k + 1))
		name="$stem-$k"
	done
	{
		printf '# Kitchen feedback: %s (%s)\n' "$run" "$repo"
		printf 'id: %s\n' "$name"
		printf 'run: %s   store: %s   repo: %s   master: %s (%s)\n' "$run" "$store" "$(field "$store" .git_root)" \
			"$(field "$store" '.master.name // "unknown"')" "$(field "$store" '.master.pane_id // "unknown"')"
		first="$(field "$store" '.skill_builds[0].path // empty')"
		printf 'build: %s (%s)\n' "$([ -n "$first" ] && build_rev "$first" || echo unknown)" "${first:-unknown}"
		[ -z "$first" ] || [ "$first" = "$skills_dir" ] || printf 'filed with: %s (%s)\n' "$(build_rev "$skills_dir")" "$skills_dir"
		printf 'retro: %s\n' "$retro"
		printf 'filed: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf 'status: open\n\n'
		cat "$src"
	} >"$tmp"
	mv "$tmp" "$fb/inbox/$name.md"
	fb_unlock "$fb"
	path="$fb/inbox/$name.md"
	event "$store" master feedback - "$name"
	printf 'filed: %s\n' "$path"
	problem="$(maintainer_problem "$fb")"
	if [ -z "$problem" ] && herdr agent prompt "$(jq -r .name "$fb/maintainer.json")" "pstack-kitchen FEEDBACK $path" >/dev/null 2>&1; then
		printf 'delivered: to maintainer %s\n' "$(jq -r .name "$fb/maintainer.json")"
		return 0
	fi
	[ -n "$problem" ] || problem="the prompt to the maintainer failed"
	herdr notification show "pstack-kitchen: feedback filed" --body "$path" --sound request >/dev/null 2>&1 || true
	printf 'delivered: notification for the human (%s); read it with: cat %s\n' "$problem" "$path"
}

# Every run on the host and each skills build; read-only and repo-free.
cmd_fleet() {
	python3 "$here/kitchen.py" fleet "$@"
}

cmd_maintainer() {
	[ $# -ge 1 ] || die "usage: kitchen.sh maintainer on [--pane ID] | off | status"
	local fb file sub="$1" pane="" info tmp problem
	shift
	fb="$(fb_root)"
	file="$fb/maintainer.json"
	case "$sub" in
	on)
		while [ $# -gt 0 ]; do
			case "$1" in
			--pane) pane="$2"; shift 2 ;;
			*) die "unknown option $1" ;;
			esac
		done
		[ "${HERDR_ENV:-}" = 1 ] || die "maintainer on needs Herdr (HERDR_ENV=1)"
		[ -n "$pane" ] || pane="${HERDR_PANE_ID:-}"
		[ -n "$pane" ] || die "no pane: pass --pane ID"
		info="$(herdr agent get "$pane" 2>/dev/null)" || die "herdr has no agent on pane $pane"
		mkdir -p "$fb"
		tmp="$(mktemp "$fb/.maintainer-XXXXXX")"
		jq -n --argjson i "$info" --arg since "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
			'{name: $i.result.agent.name, pane: $i.result.agent.pane_id, session: ($i.result.agent.agent_session.value // null), since: $since}' >"$tmp"
		[ "$(jq -r '.name // empty' "$tmp")" != "" ] || { rm -f "$tmp"; die "herdr reports no name for the agent on pane $pane; name it first"; }
		mv "$tmp" "$file"
		printf 'maintainer: %s on %s\n' "$(jq -r .name "$file")" "$(jq -r .pane "$file")"
		;;
	off)
		rm -f "$file"
		printf 'maintainer: none\n'
		;;
	status)
		[ -f "$file" ] || { printf 'maintainer: none\n'; return 0; }
		problem="$(maintainer_problem "$fb")"
		printf 'maintainer: %s on %s since %s: %s\n' "$(jq -r .name "$file")" "$(jq -r .pane "$file")" "$(jq -r .since "$file")" "${problem:-live}"
		;;
	*) die "usage: kitchen.sh maintainer on [--pane ID] | off | status" ;;
	esac
}

if [ $# -ge 2 ] && [ -f "$2/pair.json" ]; then record_skill_build "$2"; fi
pair_main "$@"
