import glob
import os
import sys

from pstack_core.argv import Spec, read
from pstack_core.consult import advice, advice_is_stop, consult, discuss_with_consultant, new_consult
from pstack_core.commands.status import latest
from pstack_core.commands.stop import clear_queue, stop_role
from pstack_core.store import field, header_field, pair_file, whole_number, who
from pstack_core.errors import Die, MasterKindUnknown, NoDiverseKinds, PlanNotAgreed
from pstack_core.panes import agent_kind, agent_status, require_herdr
from pstack_core.spawn import record_fallback, set_placement, spawn_consultant, spawn_sidekick
from pstack_core.variant import PAIR_COMMANDS, Variant, command_table, usage_text
from pstack_core.wait import agent_state, end_with_parent, timeout_ms

def stop(ctx, args):
    """STOP to each of the run's roles in turn (--only names one): the trio's stop, which the kitchen runs too. It loops over
    ctx.roles, so the kitchen's verifier gets its STOP too. A role that is not live is named and skipped, a blocked one sets the exit status
    to 3, and the newest report and advice are shown at the end."""
    require_herdr()
    end_with_parent()
    if not args:
        raise Die('usage: pair.sh stop <store> [--only sidekick|consultant] [--timeout MS]')
    store = args[0]
    opts = read(args[1:], Spec(valued=('--only', '--timeout', '--every')))
    timeout, only = timeout_ms(opts, 300000), opts.last('--only', '')
    clear_queue(store)
    rc = 0
    for role in ctx.roles:
        if only and only != role:
            continue
        name = field(store, role, 'name')
        status = agent_status(name)
        if status == 'absent':
            print(f'{who(role, name)} is not live')
            continue
        if status == 'blocked':
            sys.stderr.write(f'{who(role, name)} is blocked; inspect it before stopping\n')
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
        raise MasterKindUnknown("cannot read the master's agent kind from herdr; run pair.sh init first")
    if master == sidekick == consultant:
        raise NoDiverseKinds(f'all three agents would be {master}; pstack-trio needs at least two distinct kinds (master {master}, sidekick {sidekick}, '
                             f'consultant {consultant}). Restart with a different --sidekick or --consultant kind, or use pstack-pair')


VALUED = {'--sidekick': 'sidekick', '--consultant': 'consultant', '--fallback': 'fallback', '--only': 'only', '--permission': 'permission',
          '--consultant-permission': 'cpermission', '--timeout': 'timeout'}
SPEC = Spec(valued=(*VALUED, '--kind', '--fallback-arg'), flags=('--tab', '--split'), dashdash=True)


def spawn(ctx, args):
    """Starts the sidekick and the consultant (--only names one) in panes of their own, the sidekick first: the first role that does not come up
    ends the command with its status (3: the bootstrap did not settle; 4: it settled without the ready file)."""
    end_with_parent()
    require_herdr()
    if not args:
        raise Die(USAGE)
    store = args[0]
    opts = {'sidekick': '', 'consultant': '', 'only': '', 'permission': '', 'cpermission': '', 'timeout': '60000', 'fallback': '', 'placement': ''}
    parsed = read(args[1:], SPEC)
    for flag, value in parsed.occurrences:
        if flag == '--kind':
            opts['sidekick'], opts['consultant'] = value, opts['consultant'] or value
        elif flag in VALUED:
            opts[VALUED[flag]] = value
        elif flag in ('--tab', '--split'):
            opts['placement'] = flag[2:]
    fallback_args, rest = parsed.all('--fallback-arg'), list(parsed.after_dashdash)
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
    sidekick_entry = field(store, 'sidekick', 'kind', default='absent')
    consultant_entry = field(store, 'consultant', 'kind', default='absent')
    print(f'kinds: master {master_kind}, sidekick {sidekick_entry}, consultant {consultant_entry}')
    return 0


USAGE = usage_text(
    command_table(
        PAIR_COMMANDS,
        replace={
            'init': tuple(line.replace('the pair store', 'the trio store') for line in PAIR_COMMANDS['init']),
            'spawn': (
                '  spawn <store> --sidekick KIND --consultant KIND [--only sidekick|consultant] [--permission MODE|none]',
                '        [--consultant-permission MODE|none] [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--timeout MS] [-- agent-args...]',
                '                                            split panes beside the master, start and bootstrap both agents;',
                "                                            Devin defaults to bypass, other kinds inherit the master's mode. Refuses a trio whose",
                '                                            three kinds are all the same. --only respawns one role. A pi or Prime sidekick first proves',
                '                                            its model answers; on failure the --fallback kind starts instead. --tab gives each role',
                '                                            its own tab instead of a split (PSTACK_PLACEMENT=tab sets the default).'),
            'discuss': PAIR_COMMANDS['discuss'][:1] + (
                '                                            send PLAN to the sidekick and the consultant at once and wait for',
                "                                            both responses: the sidekick's report and the consultant's advice"),
            'notify': ('  notify <store> <path>                     sidekick or consultant -> master: prompt the master if it is idle',
                       '                                            (REPORT for a report path, ADVICE for an advice path)'),
            'stop': ('  stop <store> [--only sidekick|consultant] [--timeout MS]',
                     '                                            send STOP; each agent pauses safely and reports'),
            'status': ('  status <store>                            table of units, reports, advice, reviews, agent states, open scratch',)},
        after={'discuss': {
            'new-consult': ('  new-consult <store> <NNN> --kind design|finding|objection|review|glance [--force]',
                            '                                            create consults/NNN-<slug>-c<k>.md for unit NNN; three per unit, plus',
                            '                                            one glance (a five-minute diff read at a check-in)'),
            'consult': ('  consult <store> <consult-path> [--timeout MS]',
                        '                                            send CONSULT to the consultant and wait for its advice; it may run while',
                        '                                            the sidekick works, reading committed state or a scratch snapshot'),
            'advice': ('  advice <store> [NNN]                      print the latest (or NNN) advice path',)}}),
    'exit codes: 0 ok, 1 usage or precondition, 2 herdr error, 3 agent blocked, 4 no report or advice yet or queue empty,\n'
    '            5 agent in the wrong state or queue taken, 6 plan not agreed or consultant advice missing, 7 steer or consult cap reached,\n'
    '            8 agent kinds not diverse')


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
            raise PlanNotAgreed(f'plan {plan} has no consultant advice under advice/{seq}-*; run pair.sh discuss so both agents respond')


VARIANT = TrioVariant
