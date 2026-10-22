import glob
import os
import sys

from pstack_core.commands.report import in_herdr
from pstack_core.consult import advice, advice_is_stop, consult, discuss_with_consultant, new_consult
from pstack_core.commands.status import latest
from pstack_core.commands.stop import clear_queue, stop_role
from pstack_core.store import Die, field, header_field, pair_file, whole_number
from pstack_core.panes import agent_kind, agent_status
from pstack_core.spawn import record_fallback, set_placement, spawn_consultant, spawn_sidekick
from pstack_core.variant import Variant
from pstack_core.wait import agent_state, end_with_parent

def stop(ctx, args):
    """STOP to each of the run's roles in turn (--only names one): the trio's stop, which the kitchen runs too. It loops over
    ctx.roles, so the kitchen's verifier gets its STOP too. A role that is not live is named and skipped, a blocked one sets the exit status
    to 3, and the newest report and advice are shown at the end."""
    in_herdr()
    end_with_parent()
    if not args:
        raise Die('usage: pair.sh stop <store> [--only sidekick|consultant] [--timeout MS]')
    store, rest = args[0], args[1:]
    timeout, only, i = 300000, '', 0
    while i < len(rest):
        option = rest[i]
        if option not in ('--only', '--timeout', '--every'):
            raise Die(f'unknown option {option}')
        if i + 1 >= len(rest):
            raise Die(f'{option} needs a value')
        value = rest[i + 1]
        if option == '--only':
            only = value
        elif option == '--timeout':
            timeout = whole_number(value, '--timeout', 'milliseconds')
        else:
            timeout = whole_number(value, '--every', 'minutes') * 60000
        i += 2
    clear_queue(store)
    rc = 0
    for role in ctx.roles:
        if only and only != role:
            continue
        name = field(store, role, 'name')
        status = agent_status(name)
        if status == 'absent':
            print(f'{role} {name} is not live')
            continue
        if status == 'blocked':
            sys.stderr.write(f'{role} {name} is blocked; inspect it before stopping\n')
            rc = 3
            continue
        code, out, _ = stop_role(ctx, store, role, timeout)
        status = agent_status(name) if code != 0 else agent_state(out, 'settled')
        print(f'{role}_state: {status}')
        if status == 'blocked':
            rc = 3
    report = latest(store, 'reports')
    if report:
        print(f'report: {report} ({header_field(report, "status")})')
    advice = latest(store, 'advice')
    if advice:
        print(f'advice: {advice} ({header_field(advice, "status")})')
    return rc


def discuss(ctx, args):
    """A trio plan round goes to the sidekick and the consultant at once."""
    return discuss_with_consultant(ctx, args)


def require_diverse_kinds(master, sidekick, consultant):
    """The trio needs at least two distinct agent kinds, so the consultant is a second perspective and not the master's echo."""
    if not master:
        raise Die("cannot read the master's agent kind from herdr; run pair.sh init first", 2)
    if master == sidekick == consultant:
        raise Die(f'all three agents would be {master}; pstack-trio needs at least two distinct kinds (master {master}, sidekick {sidekick}, '
                  f'consultant {consultant}). Restart with a different --sidekick or --consultant kind, or use pstack-pair', 8)


USAGE = ('usage: pair.sh spawn <store> --sidekick KIND --consultant KIND [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] '
         '[--only sidekick|consultant] [--permission MODE|none] [--consultant-permission MODE|none] [--timeout MS] [-- agent-args...]')
VALUED = {'--sidekick': 'sidekick', '--consultant': 'consultant', '--fallback': 'fallback', '--only': 'only', '--permission': 'permission',
          '--consultant-permission': 'cpermission', '--timeout': 'timeout'}


def spawn(ctx, args):
    """Starts the sidekick and the consultant (--only names one) in panes of their own, the sidekick first: the first role that does not come up
    ends the command with its status (3: the bootstrap did not settle; 4: it settled without the ready file)."""
    end_with_parent()
    in_herdr()
    if not args:
        raise Die(USAGE)
    store, rest = args[0], list(args[1:])
    opts = {'sidekick': '', 'consultant': '', 'only': '', 'permission': '', 'cpermission': '', 'timeout': '60000', 'fallback': '', 'placement': ''}
    fallback_args = []
    while rest:
        flag = rest[0]
        if flag in VALUED or flag in ('--kind', '--fallback-arg'):
            if len(rest) < 2:
                raise Die(f'{flag} needs a value')
            if flag == '--kind':
                opts['sidekick'], opts['consultant'] = rest[1], opts['consultant'] or rest[1]
            elif flag == '--fallback-arg':
                fallback_args.append(rest[1])
            else:
                opts[VALUED[flag]] = rest[1]
            rest = rest[2:]
        elif flag in ('--tab', '--split'):
            opts['placement'], rest = flag[2:], rest[1:]
        elif flag == '--':
            rest = rest[1:]
            break
        else:
            raise Die(f'unknown option {flag}')
    whole_number(opts['timeout'], '--timeout', 'milliseconds')
    pair_file(store)
    set_placement(store, opts['placement'])
    only = opts['only']
    if only not in ('', 'sidekick', 'consultant'):
        raise Die('--only takes sidekick or consultant')
    # A role not being spawned keeps the kind recorded for it.
    sidekick_kind = opts['sidekick'] or field(store, 'sidekick', 'kind')
    consultant_kind = opts['consultant'] or field(store, 'consultant', 'kind')
    if only != 'consultant' and not sidekick_kind:
        raise Die('--sidekick KIND is required (run: herdr agent, for the kind list)')
    if only != 'sidekick' and not consultant_kind:
        raise Die('--consultant KIND is required (run: herdr agent, for the kind list)')
    master_kind = agent_kind(field(store, 'master', 'pane_id'))
    require_diverse_kinds(master_kind, sidekick_kind or master_kind, consultant_kind or master_kind)
    master_pane = field(store, 'master', 'pane_id')
    cpermission = opts['cpermission'] or opts['permission']
    if only != 'consultant':
        record_fallback(store, opts['fallback'], fallback_args)
        rc = spawn_sidekick(ctx, store, sidekick_kind, opts['permission'], master_pane, opts['timeout'], '', '', rest)
        if rc != 0:
            return rc
    if only != 'sidekick':
        rc = spawn_consultant(ctx, store, consultant_kind, cpermission, opts['timeout'], rest)
        if rc != 0:
            return rc
    # field "$store" '.sidekick.kind // "absent"': over a role jq cannot index, jq's error is printed and the entry reads empty
    sidekick_entry = field(store, 'sidekick', 'kind', default='absent')
    consultant_entry = field(store, 'consultant', 'kind', default='absent')
    print(f'kinds: master {master_kind}, sidekick {sidekick_entry}, consultant {consultant_entry}')
    return 0


USAGE = """usage: pair.sh <command> [args]

  init <slug> [--store DIR]                 create or re-register the trio store; prints its path
  spawn <store> --sidekick KIND --consultant KIND [--only sidekick|consultant] [--permission MODE|none]
        [--consultant-permission MODE|none] [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--timeout MS] [-- agent-args...]
                                            split panes beside the master, start and bootstrap both agents;
                                            Devin defaults to bypass, other kinds inherit the master's mode. Refuses a trio whose
                                            three kinds are all the same. --only respawns one role. A pi or Prime sidekick first proves
                                            its model answers; on failure the --fallback kind starts instead. --tab gives each role
                                            its own tab instead of a split (PSTACK_PLACEMENT=tab sets the default).
  rotate <store>                            exit an idle sidekick after a done brief, respawn with recorded
                                            arguments, and bootstrap a fresh session; preserve the queue and history
  failover <store> [--reason TEXT] [--force]
                                            replace a failing sidekick with its recorded fallback in the same pane;
                                            the fallback picks up from the store, and the run stays on it
  permission                                print the master's detected permission mode
  new-plan <store> <slug>                   create plans/NNN-<slug>.md from the template; prints its path
  discuss <store> <plan-path> [--timeout MS]
                                            send PLAN to the sidekick and the consultant at once and wait for
                                            both responses: the sidekick's report and the consultant's advice
  new-consult <store> <NNN> --kind design|finding|objection|review|glance [--force]
                                            create consults/NNN-<slug>-c<k>.md for unit NNN; three per unit, plus
                                            one glance (a five-minute diff read at a check-in)
  consult <store> <consult-path> [--timeout MS]
                                            send CONSULT to the consultant and wait for its advice; it may run while
                                            the sidekick works, reading committed state or a scratch snapshot
  advice <store> [NNN]                      print the latest (or NNN) advice path
  new-brief <store> <slug>                  create briefs/NNN-<slug>.md from the template; prints its path
  dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]
                                            send BRIEF to the sidekick (a due rotation runs first, up to a minute plus
                                            the READY wait, even with --send-only) and wait for it to settle;
                                            implementation playbooks require an agreed plan
  wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path
                                            (and any queued or now-running brief), or a check-in digest when the
                                            interval passes first
  queue <store> <brief-path> [--replace]    hold the next brief for a working sidekick; it waits for the master's
                                            dispatch, which rotates a due sidekick first. One slot. queue <store> --clear empties it
  step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]
                                            sidekick: record a committed step of the running brief and go on;
                                            the master's wait picks it up for review
  notes <store>                             sidekick, at each step boundary: print blocking review notes still open
  new-note <store> <NNN>                    create the draft note reviewing unit NNN's steps since the last note
  note <store> <note-draft-path>            publish a filled note to the sidekick; its follow-ups go to followups.md
  finish <store> <report-path>              sidekick, after writing any report: notify the master, then print
                                            the REPORT line; a done brief requires rotation
  next <store>                              sidekick: refused for every kind (exit 5); the master dispatches the queued brief to a fresh session
  progress <store> <text>                   sidekick: append one timestamped line to the running brief's progress log
  new-steer <store> <NNN> [--supersedes STEER | --force]
                                            create steers/NNN-<slug>-s<k>.md; prints its path. --supersedes answers
                                            an objection; two fresh steers per brief
  steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]
                                            send STEER: to a working sidekick, return at once (its harness hands it
                                            over between tool calls; --interrupt cancels the running one first);
                                            to one paused on an objection, wait for it to settle
  report <store> [NNN]                      print the latest (or NNN) report path
  notify <store> <path>                     sidekick or consultant -> master: prompt the master if it is idle
                                            (REPORT for a report path, ADVICE for an advice path)
  stop <store> [--only sidekick|consultant] [--timeout MS]
                                            send STOP; each agent pauses safely and reports
  scratch <store> <id> [--at SHA] [--remove]
                                            a throwaway worktree under <store>/scratch/<id>: at HEAD with the live
                                            diff applied, or exactly at SHA to recheck a unit while the sidekick works
  pause <store> [--reason TEXT]             pause at the next safe point: the sidekick stops at its next step or
                                            progress boundary; dispatch, queue, discuss, consult, answer refuse
  resume <store>                            lift the pause; re-dispatch the paused brief to continue it
  status <store>                            table of units, reports, advice, reviews, agent states, open scratch
  log <store> <phase> <decision> <why> <evidence> <result>
                                            append a decisions.tsv row (show-me-your-work format)
  metrics <store>                           where the time went, from events.tsv: busy and idle per agent,
                                            master wakes, review latency, verdicts

exit codes: 0 ok, 1 usage or precondition, 2 herdr error, 3 agent blocked, 4 no report or advice yet or queue empty,
            5 agent in the wrong state or queue taken, 6 plan not agreed or consultant advice missing, 7 steer or consult cap reached,
            8 agent kinds not diverse
"""


class TrioVariant(Variant):
    PAIR_SKILL = 'pstack-trio'
    STATE_DIR = 'trio'
    store_label = 'Trio'
    roles = ('sidekick', 'consultant')
    store_dirs = ('plans', 'briefs', 'reports', 'reviews', 'progress', 'steers', 'consults', 'advice', 'scratch')
    slug_max = 20
    usage = USAGE
    commands = {'spawn': spawn, 'stop': stop, 'discuss': discuss, 'new-consult': new_consult, 'consult': consult, 'advice': advice}

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """A plan the consultant took part in also needs its advice on file."""
        if not any(not advice_is_stop(f) for f in glob.glob(os.path.join(glob.escape(store), 'advice', f'{glob.escape(seq)}-*.md'))):
            raise Die(f'plan {plan} has no consultant advice under advice/{seq}-*; run pair.sh discuss so both agents respond', 6)


VARIANT = TrioVariant
