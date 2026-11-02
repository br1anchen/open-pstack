#!/usr/bin/env bash
# Drive pair.sh (pair or trio) through a run on the Rex backend against fake rex and print a normalized transcript: command, exit code,
# output, and the rex calls that change something (the status looks a wait repeats depend on timing and are left out).
# Usage: rex-scenario.sh <skills-dir> <pair|trio>
set -u
skills="$(readlink -f "$1")" variant="$2"
H="$(cd "$(dirname "$0")" && pwd)"
case "$variant" in
pair) skill=pstack-pair prefix=pstack-pair dir=pair ;;
trio) skill=pstack-trio prefix=pstack-trio dir=trio ;;
esac
T="$(cd "$(mktemp -d)" && pwd -P)"
unset HERDR_ENV HERDR_PANE_ID HERDR_SOCKET_PATH PSTACK_MUX PSTACK_KITCHEN_ROLE PSTACK_KITCHEN_RUN CLAUDE_CODE_SESSION_ID
export PAIR_PROC_TREE=0 PAIR_SETTLE_HOLD=1 PAIR_SUBMIT_CHECK_S=0 PAIR_BUSY_POLL=0.2 PAIR_SETTLE_POLL_S=0.05 PAIR_FILE_POLL_MS=50 PAIR_STOP_POLL_S=0.05 \
	PAIR_REX_POLL_S=0 PAIR_REX_STALL_S=1 HOME="$T/home" FAKE="$T/fake" PATH="$H/bin:$PATH" XDG_STATE_HOME="$T/state" REX_BLOCK=block:m0 REX_SESSION=session:s1
# The master: Claude Code at work in block m0.
mkdir -p "$FAKE/rex/blocks/block_m0" "$T/repo" "$HOME"
printf '200 claude\n' >"$FAKE/rex/blocks/block_m0/fg"
printf 'working claude-code t0\n' >"$FAKE/rex/blocks/block_m0/state"
git -C "$T/repo" init -q -b main && git -C "$T/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
cd "$T/repo"
P="$skills/$skill/scripts/pair.sh"
store="$T/state/pstack/$dir/demo"

cat >"$FAKE/hook" <<HOOK
#!/usr/bin/env bash
name="\$1" text="\$2" store="$store"
role=sidekick; case "\$name" in *-consultant) role=consultant ;; esac
body() { printf '# %s\n\nstatus: %s\nhead: abc\n' "\$1" "\$2"; }
case "\$text" in
Load*) [ \$role = sidekick ] && body ready done >"\$store/reports/000-ready.md"; [ \$role = consultant ] && body ready answered >"\$store/advice/000-ready.md" ;;
"$prefix PLAN "*) b="\$(basename "\${text#* PLAN }")"
	[ \$role = sidekick ] && body plan agree >"\$store/reports/\$b"
	[ \$role = consultant ] && body plan agree >"\$store/advice/\$b" ;;
"$prefix BRIEF "*) body report done >"\$store/reports/\$(basename "\${text##* }")" ;;
"$prefix STOP "*) [ \$role = sidekick ] && body stop partial >"\$store/reports/\$(ls \$store/briefs | tail -1 | cut -c1-3)-stop.md" ;;
esac
exit 0
HOOK
chmod +x "$FAKE/hook"

norm() { T="$T" S="$skills" perl -pe 's#\Q$ENV{S}\E#<SKILLS>#g; s#\Q$ENV{T}\E#<T>#g; s/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z/<TS>/g; s/\b1[0-9]{9}\b/<EPOCH>/g; s/[0-9a-f]{7,40}/<SHA>/g'; }
run() {
	printf '\n$ pair.sh %s\n' "$*" | norm
	local out code
	out="$("$P" "$@" 2>&1)"; code=$?
	printf '%s\n[exit %s]\n' "$out" "$code" | norm
	if [ -s "$FAKE/calls.log" ]; then grep -vE '^rex (block call|capture) ' "$FAKE/calls.log" | uniq | sed 's/^/  /' | norm; : >"$FAKE/calls.log"; fi
}
fill() { perl -0pi -e 's/\{\{.*?\}\}/x/gs' "$1"; }
set_hdr() { KEY="$2" VAL="$3" perl -i -pe 's/^\Q$ENV{KEY}\E:.*/$ENV{KEY}: $ENV{VAL}/' "$1"; }

run init demo
if [ $variant = trio ]; then run spawn "$store" --sidekick codex --consultant claude --permission none
else run spawn "$store" --kind claude --permission none; fi
plan="$("$P" new-plan "$store" design)"; fill "$plan"
run discuss "$store" "$plan"
printf '# Review\n\nverdict: agreed\napproval: not-required\n' >"$store/reviews/$(basename "$plan")"
brief="$("$P" new-brief "$store" build)"; fill "$brief"; set_hdr "$brief" playbook feature; set_hdr "$brief" plan "$plan"
printf -- '\n## Scope\n\nmay write:\n- a.txt\n\nmust not write:\n- x\n' >>"$brief"
run dispatch "$store" "$brief" --timeout 3000
run status "$store"
run stop "$store" --timeout 3000
printf '\nregistry:\n'; perl -0pe 's/\s+/ /g' "$T/state/pstack/rex-agents.json" | norm; printf '\n'
rm -rf "$T"
