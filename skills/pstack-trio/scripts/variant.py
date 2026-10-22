import glob
import os
import sys

from pstack_core.commands.report import in_herdr
from pstack_core.commands.status import latest
from pstack_core.commands.stop import clear_queue, role_value, stop_role
from pstack_core.errors import Die, Exit, fail
from pstack_core.herdr import agent_kind, agent_status
from pstack_core.spawn import record_fallback, set_placement, spawn_consultant, spawn_sidekick
from pstack_core.store import field, header_field, pair_file
from pstack_core.variant import Variant
from pstack_core.wait import ArithError, agent_state, bash_arith, core_line, die_with_the_shell, tdiv

CONSULTANT_CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'consultant-core.sh')
TRIO_PAIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pair.sh')


def stop(ctx, args):
    """STOP to each of the run's roles in turn (--only names one): the consultant-core stop, which the trio and the kitchen run. It loops over
    ctx.roles, so the kitchen's verifier gets its STOP too. A role that is not live is named and skipped, a blocked one sets the exit status
    to 3, and the newest report and advice are shown at the end."""
    in_herdr()
    die_with_the_shell()
    if not args:
        raise Die('usage: pair.sh stop <store> [--only sidekick|consultant] [--timeout MS]')
    store, rest = args[0], args[1:]
    timeout, only, i = '300000', '', 0
    while i < len(rest):
        option = rest[i]
        if option not in ('--only', '--timeout', '--every'):
            raise Die(f'unknown option {option}')
        if i + 1 >= len(rest):
            fail(f'{option}) ', '$2: unbound variable', 'cmd_stop() {', CONSULTANT_CORE)
        value = rest[i + 1]
        if option == '--only':
            only = value
        elif option == '--timeout':
            timeout = value
        else:
            try:
                timeout = str(bash_arith(f'{value} * 60000'))
            except ArithError as e:
                fail('--every) timeout=$(($2 * 60000))', e.text, 'cmd_stop() {', CONSULTANT_CORE)
        i += 2
    # checkin_interval_m is read by nothing after this loop; only its arithmetic can still end the command.
    try:
        tdiv(bash_arith(timeout), 60000)
    except ArithError as e:
        fail('checkin_interval_m=$((timeout / 60000))', e.text, 'cmd_stop() {', CONSULTANT_CORE)
    clear_queue(store)
    rc = 0
    for role in ctx.roles:
        if only and only != role:
            continue
        name = role_value(store, role, 'name')
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
    die_with_the_shell()
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
                path, number = core_line(f'{flag}) ', 'cmd_spawn() {', TRIO_PAIR)
                raise Exit(1, f'{path}: line {number}: $2: unbound variable\n')
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
    pair_file(store)
    set_placement(store, opts['placement'])
    only = opts['only']
    if only not in ('', 'sidekick', 'consultant'):
        raise Die('--only takes sidekick or consultant')
    # A role not being spawned keeps the kind recorded for it.
    sidekick_kind = opts['sidekick'] or field(store, 'sidekick', 'kind', check=True, empty=True)
    consultant_kind = opts['consultant'] or field(store, 'consultant', 'kind', check=True, empty=True)
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


def advice_is_stop(path):
    """The consultant leaving, not an answer: kind: stop, or, with no kind: line, a file named *-stop.md."""
    kind = header_field(path, 'kind')
    return kind == 'stop' if kind else path.endswith('-stop.md')


class TrioVariant(Variant):
    commands = {'spawn': spawn, 'stop': stop}

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """A plan the consultant took part in also needs its advice on file."""
        if not any(not advice_is_stop(f) for f in glob.glob(os.path.join(glob.escape(store), 'advice', f'{glob.escape(seq)}-*.md'))):
            raise Die(f'plan {plan} has no consultant advice under advice/{seq}-*; run pair.sh discuss so both agents respond', 6)


VARIANT = TrioVariant
