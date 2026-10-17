# shellcheck shell=bash
# Prime's TUI attaches to a daemon session. Its Herdr session reference, rather
# than a name or the newest log in a directory, identifies the session we own.
record_prime_session() {
	local store="$1" role="$2" info session
	info="$(herdr agent get "$(field "$store" ".$role.name")" 2>/dev/null)" || return 1
	session="$(jq -ce --arg pane "$(field "$store" ".$role.pane_id")" '
		.result.agent | select(.agent == "prime-agent" and .pane_id == $pane)
		| .agent_session | select((.kind == "path" or .kind == "id") and (.value | type == "string" and length > 0))
		| {kind, value}' <<<"$info")" || return 1
	json_update "$store" --arg role "$role" --argjson session "$session" '.[$role].session = $session'
}

# Runs in the Python core. It returns 0 when the role is stopped and 1 when it cannot be, and the callers test it with `||`, so every status comes
# back as a return; a die in the core (100 plus its code) ends the shell, as the die did.
stop_prime_role() { pycore __stop_prime_role "$@" || { local pycore_rc=$?; case $pycore_rc in 1[0-9][0-9]) exit $((pycore_rc - 100)) ;; *) return $pycore_rc ;; esac; }; }

prime_role_error() {
	local store="$1" role="$2" session
	record_prime_session "$store" "$role" || true
	session="$(field "$store" ".$role.session | select(.kind == \"path\") | .value // empty")"
	[ -z "$session" ] || agent_session_error "$session"
}
