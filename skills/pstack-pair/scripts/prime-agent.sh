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
