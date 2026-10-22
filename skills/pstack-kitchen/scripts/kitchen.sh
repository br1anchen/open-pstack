#!/usr/bin/env bash
# Kitchen store and Herdr channel helper for the pstack-kitchen skill: the
# pair's master and sidekick, a consultant started only when a unit
# escalates, and a short-lived verifier pane per verification. The shared
# commands live in pstack-pair's pair-core.sh and the consultant's in
# pstack_core/consult.py; the repo's rules are read through
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

# Every command of the kitchen runs in Python (skills/pstack-kitchen/scripts/kitchen_core/, through pycore and the kitchen's variant.py): a shim below only
# hands its arguments over. `--exec` replaces the shell, for the commands that wait (a SIGTERM to the shell must end the wait).
cmd_init() { pycore init "$@"; }

cmd_spawn() { pycore spawn "$@"; }

cmd_consultant() { pycore consultant "$@"; }

cmd_classify() { pycore classify "$@"; }

cmd_wait() { pycore --exec wait "$@"; }

cmd_dispatch() { pycore --exec dispatch "$@"; }

cmd_queue() { pycore queue "$@"; }

cmd_discuss() { pycore discuss "$@"; }

cmd_new_consult() { pycore new-consult "$@"; }

cmd_consult() { pycore --exec consult "$@"; }

cmd_advice() { pycore advice "$@"; }

cmd_step() { pycore --exec step "$@"; }

cmd_finish() { pycore --exec finish "$@"; }

cmd_verify() { HOSTNAME="${HOSTNAME:-localhost}" pycore --exec verify "$@"; }

cmd_revise() { pycore revise "$@"; }

cmd_land_check() { pycore land-check "$@"; }

cmd_catch() { pycore catch "$@"; }

cmd_close() { pycore close "$@"; }

cmd_new_handoff() { pycore new-handoff "$@"; }

cmd_rotate_master() { pycore rotate-master "$@"; }

cmd_take_master() { pycore take-master "$@"; }

cmd_retro() { pycore retro "$@"; }

cmd_feedback() { pycore feedback "$@"; }

cmd_fleet() { pycore fleet "$@"; }

cmd_maintainer() { pycore maintainer "$@"; }

# A run stamped at init lists the skills directories its commands ran from; a command from a different one adds a row (kitchen_core.setup.record_skill_build).
# A run from before stamps stays unstamped. It runs here, before pair_main, for every word the script is called with (help, an unknown command, any
# command): no per-command shim reaches them all.
pair_main "$@"
