import os
import sys

from pstack_core.commands.report import in_herdr
from pstack_core.commands.status import latest
from pstack_core.commands.stop import clear_queue, role_value, stop_role
from pstack_core.errors import Die, Exit
from pstack_core.herdr import agent_status
from pstack_core.store import header_field
from pstack_core.variant import Variant
from pstack_core.wait import ArithError, agent_state, bash_arith, core_line, die_with_the_shell, tdiv

CONSULTANT_CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'consultant-core.sh')


def shell_error(text, message):
    """An error bash prints for a line of cmd_stop in consultant-core.sh, then ends with status 1."""
    path, number = core_line(text, 'cmd_stop() {', CONSULTANT_CORE)
    raise Exit(1, f'{path}: line {number}: {message}\n')


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
            shell_error(f'{option}) ', '$2: unbound variable')
        value = rest[i + 1]
        if option == '--only':
            only = value
        elif option == '--timeout':
            timeout = value
        else:
            try:
                timeout = str(bash_arith(f'{value} * 60000'))
            except ArithError as e:
                shell_error('--every) timeout=$(($2 * 60000))', e.text)
        i += 2
    # checkin_interval_m is read by nothing after this loop; only its arithmetic can still end the command.
    try:
        tdiv(bash_arith(timeout), 60000)
    except ArithError as e:
        shell_error('checkin_interval_m=$((timeout / 60000))', e.text)
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


class TrioVariant(Variant):
    commands = {'stop': stop}

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """A plan the consultant took part in also needs its advice on file."""
        if not latest(store, 'advice', seq):
            raise Die(f'plan {plan} has no consultant advice under advice/{seq}-*; run pair.sh discuss so both agents respond', 6)


VARIANT = TrioVariant
