"""Starting a role's agent in a pane and bootstrapping it: spawn_role and the recovery of a bootstrap prompt that a first-run screen swallowed.
Returns 0, 3 (the bootstrap did not settle) or 4 (it settled without the ready file); a die is a Die."""

import io
import os
import sys
import time
from dataclasses import dataclass, replace

from . import agents
from .argv import Spec, read
from .errors import (
    AgentStartFailed,
    BadPlacement,
    Die,
    FreshStartOnly,
    KindRequired,
    NoFallback,
    PrimeFailed,
)
from .numbers import env_float, env_int, exit_code, whole_number
from .panes import (
    DEVNULL,
    MERGED,
    PIPE,
    agent_field,
    agent_prompt,
    agent_start,
    agent_status,
    await_file,
    herdr,
    new_pane,
    refuse_untrusted,
    require_herdr,
)
from .permission import mode_of_this_shell
from .providers import provider_hint, record_prime_session
from .send import fresh_kind, submit_typed, visible_without_blanks
from .store import (
    count,
    event,
    field,
    listing,
    load,
    mark_ready,
    member,
    names,
    pair_file,
    peek,
    settle_ready,
    text_at,
    update,
    update_role,
    who,
)
from .wait import end_with_parent

FRESH_SESSION_FLAGS = (
    '-c',
    '--continue',
    '-r',
    '--resume',
    '--session',
    '--session-id',
    '--fork',
)


def stderr_of(text):
    sys.stderr.write(text)


def bootstrap_recover(ctx, store, name, role, pane, ready, bootstrap, generation):
    """After a bootstrap prompt stalled and no ready file exists: an idle or done agent in this pane gets the Enter its input still waits for, or
    the bootstrap sent once more when nothing was typed (a blocked agent needs the human); otherwise state detection lagged on a narrow pane while
    the agent works. False only for a blocked agent."""
    time.sleep(env_float('PAIR_SUBMIT_CHECK_S', 5))
    if os.path.isfile(f'{store}/{ready}'):
        return True
    code, info = herdr('agent', 'get', name)
    if code != 0:
        return True
    live_pane = agent_field(info, 'pane_id')
    if (live_pane if live_pane not in (None, False) else '') != pane:
        return True
    if field(store, role, 'generation') != generation:
        return True
    state = agent_status(name)
    if state in ('idle', 'done'):
        if os.path.basename(ready) in visible_without_blanks(name):
            herdr('agent', 'send-keys', name, 'enter')
        else:
            agent_prompt(
                name,
                bootstrap,
                wait=True,
                timeout_ms=240000,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
    elif state == 'blocked':
        stderr_of(
            f'{name} is blocked on a dialog that needs the human; no keys were sent. Read it with: herdr agent read {name} --source visible --lines 60\n'
        )
        return False
    return True


@dataclass(frozen=True)
class SpawnRequest:
    """How to start a role's agent: its kind and permission ('' for the kind's default), the pane to split from, the start timeout in milliseconds, the
    split direction, the pane to reuse, and the agent's own arguments."""

    kind: str
    permission: str = ''
    anchor: str = ''
    timeout: str = '60000'
    direction: str = ''
    pane: str = ''
    args: tuple = ()


def start_permission(kind, permission, master_mode, args):
    """(permission, permission flags): the permission named, else bypassPermissions for devin and the master's mode for the rest; native-args when the
    caller's own arguments carry one or it is 'none', and untranslated (with a warning) when the kind has no flags for it."""
    if not permission:
        permission = 'bypassPermissions' if kind == 'devin' else master_mode
    if permission == 'none' or agents.has_permission_arg(args):
        return 'native-args', []
    flags = agents.permission_args(kind, permission)
    if not flags and not agents.asks_no_approvals(kind, permission):
        stderr_of(
            f'no permission translation for kind {kind}; pass native flags after -- to match the master ({master_mode})\n'
        )
        permission = 'untranslated'
    return permission, flags


def start_arguments(kind, flags, args, cwd):
    """The agent's start arguments: the permission flags, then the trust and MCP flags when the caller's arguments carry none, then the caller's own.
    A kind whose tasks start fresh refuses an argument that resumes a session."""
    if not agents.has_trust_arg(args):
        flags += agents.trust_args(kind)
    if not agents.has_mcp_arg(args):
        flags += agents.mcp_args(kind, cwd)
    args = [*flags, *args]
    if fresh_kind(kind):
        for arg in args:
            if arg in FRESH_SESSION_FLAGS or arg.startswith(
                ('--resume=', '--session=')
            ):
                raise FreshStartOnly(
                    f'{kind} task sessions start fresh; recover partial work from the store instead of passing {arg}'
                )
    return args


def report_live(store, role, name, status, ready):
    """A role whose agent is already live: a pending bootstrap is settled when its ready file is there (0) or reported missing (4)."""
    if peek(load(store), role).get('bootstrap_pending') is True:
        if not os.path.isfile(f'{store}/{ready}'):
            stderr_of(f'{role} bootstrap still pending: {ready} is missing\n')
            return 4
        print(f'ready: {store}/{ready}')
        settle_ready(store, role)
    pane = field(store, role, 'pane_id')
    print(
        f'{who(role, name)} already live ({status})' + (f' in {pane}' if pane else '')
    )
    return 0


def start_agent(store, role, name, request, args, cwd):
    """(pane, whether a prime-agent failed to start): the agent starts in the pane asked for, else the role's recorded pane when herdr still has it,
    else in a new pane. A failed start in a given pane falls back to a new one, except for a prime-agent, whose pane and launch stay for inspection."""
    kind, pane, prime_start_failed = request.kind, request.pane, False
    if not pane:
        previous = field(store, role, 'pane_id')
        if previous and herdr('pane', 'get', previous)[0] == 0:
            pane = previous
    if pane:
        r = agent_start(
            name, kind, pane, args, request.timeout, stdout=PIPE, stderr=MERGED
        )
        if r.returncode == 0:
            print(f'reused pane {pane}')
        else:
            refuse_untrusted(pane, kind, cwd)
            stderr_of(
                f'pane {pane} unavailable for agent start: {r.stdout.rstrip(chr(10))}\n'
            )
            if kind == 'prime-agent':
                prime_start_failed = True
            else:
                pane = ''
    if not pane:
        pane = new_pane(store, role, request.anchor, request.direction, cwd)
        r = agent_start(name, kind, pane, args, request.timeout, stderr=None)
        if r.returncode != 0:
            if kind == 'prime-agent':
                prime_start_failed = True
            else:
                refuse_untrusted(pane, kind, cwd, True)
                raise AgentStartFailed(
                    f'agent start failed in {pane}; inspect: herdr pane read {pane} --source visible'
                )
    return pane, prime_start_failed


def record_started(store, role, kind, pane, args, permission, master_mode):
    """The role's entry in pair.json once its agent started: pane, kind, start arguments, the next generation, and the permission both agents run with."""
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def started(doc):
        entry = member(doc, role)
        entry['pane_id'], entry['kind'], entry['started_at'], entry['start_args'] = (
            pane,
            kind,
            now,
            list(args),
        )
        entry['generation'] = count(entry, 'generation') + 1
        entry['bootstrap_pending'] = True
        entry['permission_mode'] = permission
        entry['closed'] = False
        member(doc, 'master')['permission_mode'] = master_mode

    update(store, started)


def record_prime_start(store, role, name, pane, cwd, start_failed):
    """A prime-agent's launch: its session fields reset and its session recorded; a start that failed, or that reported no session, is a PrimeFailed."""
    update_role(
        store,
        role,
        {'session': None, 'session_stopped': False, 'closed': False, 'launch_cwd': cwd},
    )
    if start_failed:
        record_prime_session(store, role)
        raise PrimeFailed(
            f'Prime start failed in {pane}; its pane and recorded launch stay for inspection'
        )
    if not record_prime_session(store, role):
        raise PrimeFailed(
            f'Prime did not report its session; inspect {name} before prompting'
        )


def bootstrap_role(ctx, store, role, kind, name, pane, ready):
    """Sends the bootstrap prompt and waits for the ready file: 0 once it is there, 3 when the prompt never settled and 4 when it settled without it. A
    ready file left by the generation before is kept aside first."""
    generation = field(store, role, 'generation')
    if os.path.isfile(f'{store}/{ready}'):
        os.makedirs(f'{store}/sessions', exist_ok=True)
        os.replace(
            f'{store}/{ready}',
            f'{store}/sessions/{role}-{generation}-previous-ready.md',
        )
    bootstrap = (
        f'Load the {ctx.skill} skill from ~/.agents/skills/{ctx.skill}/SKILL.md and take the {role} role (generation '
        f'{generation}, pane {pane}). {ctx.store_label} store: {store}. Follow its bootstrap steps, write {ready}, and reply READY.'
    )
    r = agent_prompt(name, bootstrap, wait=True, timeout_ms=240000)
    code, err = exit_code(r.returncode), r.stderr.rstrip('\n')
    if not os.path.isfile(f'{store}/{ready}'):
        submitted = submit_typed(name, os.path.basename(ready))
        if submitted != 0 and code == 0:
            await_file(f'{store}/{ready}', 240000)
    if code != 0 and 'agent_prompt_stalled' in err:
        # A first-run screen (pi's changelog after an update, a trust prompt, Claude's update banner) can take the Enter that submits the bootstrap,
        # or swallow the prompt whole. bootstrap_recover acts only on a live idle agent in this pane; otherwise state detection lagged on a narrow
        # pane while the agent works. Either way the ready file is the signal.
        if bootstrap_recover(
            ctx, store, name, role, pane, ready, bootstrap, generation
        ):
            print(f'{role} prompt looked stalled; waiting for {ready} instead')
            if await_file(
                f'{store}/{ready}', env_int('PAIR_BOOTSTRAP_WAIT_MS', 240000)
            ):
                code = 0
    if code != 0:
        stderr_of(f'{role} bootstrap did not settle: {err}\n')
        stderr_of(f'inspect: herdr agent read {name} --source visible --lines 60\n')
        event(store, role, 'spawn', '-', f'{kind}:failed')
        if role == 'sidekick' and kind == 'prime-agent':
            stderr_of(provider_hint(store, 'bootstrap'))
        return 3
    if os.path.isfile(f'{store}/{ready}'):
        mark_ready(store, role)
        print(f'ready: {store}/{ready}')
        event(store, role, 'spawn', '-', f'{kind}:ready')
        return 0
    stderr_of(
        f'{role} settled but {ready} is missing; state={agent_status(name)}. Read the pane before prompting again.\n'
    )
    # A provider stop (a rate limit) is the usual cause, and it shows only in the pane or the agent's session log.
    if role == 'sidekick':
        stderr_of(provider_hint(store, 'bootstrap'))
    event(store, role, 'spawn', '-', f'{kind}:unready')
    return 4


def spawn_role(ctx, store, role, request):
    """Starts the role's agent and bootstraps it: 0 once it is ready, 3 when the bootstrap did not settle and 4 when it settled without the ready file."""
    kind, master_mode = request.kind, mode_of_this_shell()
    permission, flags = start_permission(
        kind, request.permission, master_mode, request.args
    )
    cwd = text_at(load(store), 'cwd')
    args = start_arguments(kind, flags, list(request.args), cwd)
    ready = 'advice/000-ready.md' if role == 'consultant' else 'reports/000-ready.md'
    name = field(store, role, 'name')
    status = agent_status(name)
    if status != 'absent':
        return report_live(store, role, name, status, ready)
    pane, prime_start_failed = start_agent(store, role, name, request, args, cwd)
    record_started(store, role, kind, pane, args, permission, master_mode)
    if kind == 'prime-agent':
        record_prime_start(store, role, name, pane, cwd, prime_start_failed)
    print(
        f'{role} {name} ({kind}) started in {pane} with permission {permission} (master: {master_mode})'
    )
    return bootstrap_role(ctx, store, role, kind, name, pane, ready)


def set_placement(store, want=''):
    """How roles get their panes: --tab or --split from spawn, else what the store has, else PSTACK_PLACEMENT, else split."""
    if not want and not text_at(load(store), 'placement'):
        want = os.environ.get('PSTACK_PLACEMENT') or 'split'
    if not want:
        return
    if want not in ('split', 'tab'):
        raise BadPlacement(f'placement must be split or tab, not {want}')

    def mutate(doc):
        doc['placement'] = want

    update(store, mutate)


def record_failover(store, source, target, reason):
    """The sidekick's failover from one kind to another: .sidekick.failovers, the master's failover event, and the line it prints."""
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def mutate(doc):
        entry = member(doc, 'sidekick')
        entry['failovers'] = listing(entry, 'failovers') + [
            {'from': source, 'to': target, 'reason': reason, 'at': now}
        ]

    update(store, mutate)
    event(store, 'master', 'failover', '-', f'{source}:{target}')
    print(f'failover: {source} -> {target} ({reason})')


def record_fallback(store, kind, args):
    """The sidekick's fallback from the spawn options; an empty kind keeps any recorded one."""
    if not kind:
        return

    def mutate(doc):
        member(doc, 'sidekick')['fallback'] = {'kind': kind, 'args': list(args)}

    update(store, mutate)


def preflight_reason(kind, args):
    """(status, text): preflight_kind with its stderr captured."""
    seen, sys.stderr = sys.stderr, io.StringIO()
    try:
        code = agents.preflight_kind(kind, args)
        return code, sys.stderr.getvalue().rstrip('\n')
    finally:
        sys.stderr = seen


def spawn_sidekick(ctx, store, request):
    """Starts the sidekick, or its recorded fallback when the kind's preflight fails. The fallback starts with its own recorded arguments and
    its kind's default permission. A live sidekick skips the preflight. Returns spawn_role's 0, 3 or 4."""
    kind = request.kind
    if (
        agents.has_preflight(kind)
        and agent_status(field(store, 'sidekick', 'name')) == 'absent'
    ):
        code, reason = preflight_reason(kind, list(request.args))
        if code != 0:
            reason = f'preflight: {reason or "no answer"}'
            sys.stderr.write(f'{kind} failed {reason}\n')
            fallback = peek(peek(load(store), 'sidekick'), 'fallback')
            name = text_at(fallback, 'kind')
            if not name or name == kind:
                raise NoFallback(
                    f'no fallback recorded for a failed {kind}; pass --fallback KIND or fix the agent'
                )
            record_failover(store, kind, name, reason)
            return spawn_role(
                ctx,
                store,
                'sidekick',
                replace(
                    request,
                    kind=name,
                    permission='',
                    args=tuple(names(fallback, 'args')),
                ),
            )
    return spawn_role(ctx, store, 'sidekick', request)


def spawn_consultant(ctx, store, request):
    """The consultant splits from the sidekick's pane, so the master keeps its height; the master's pane when the sidekick's is gone."""
    anchor = field(store, 'sidekick', 'pane_id')
    if not anchor or herdr('pane', 'get', anchor)[0] != 0:
        anchor = field(store, 'master', 'pane_id')
    return spawn_role(ctx, store, 'consultant', replace(request, anchor=anchor))


def usage(ctx):
    return (
        f'usage: {ctx.prog} spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--direction right|down] [--pane ID] [--timeout MS] '
        '[-- agent-args...]'
    )


VALUED = {
    '--kind': 'kind',
    '--fallback': 'fallback',
    '--permission': 'permission',
    '--direction': 'direction',
    '--pane': 'pane',
    '--timeout': 'timeout',
}
SPEC = Spec(
    valued=(*VALUED, '--fallback-arg'), flags=('--tab', '--split'), dashdash=True
)


@dataclass(frozen=True)
class SpawnOptions:
    """What `spawn` reads from its words after the store; the empty string is an option not given."""

    kind: str = ''
    fallback: str = ''
    fallback_args: tuple = ()
    permission: str = ''
    direction: str = ''
    pane: str = ''
    timeout: str = '60000'
    placement: str = ''
    native: tuple = ()


def spawn_options(words):
    """The SpawnOptions of the words after the store; the last of a repeated option wins, and an option that takes a value takes the next word."""
    parsed = read(words, SPEC)
    given = {}
    placement = ''
    for flag, value in parsed.occurrences:
        if flag in VALUED:
            given[VALUED[flag]] = value
        elif flag in ('--tab', '--split'):
            placement = flag[2:]
    return SpawnOptions(
        **given,
        fallback_args=tuple(parsed.all('--fallback-arg')),
        placement=placement,
        native=tuple(parsed.after_dashdash),
    )


def start_sidekick(ctx, store, options):
    """Starts the sidekick (or its recorded fallback) in a pane: 0, 3 (the bootstrap did not settle) or 4 (it settled without the ready file)."""
    whole_number(options.timeout, '--timeout', 'milliseconds')
    if not options.kind:
        raise KindRequired()
    pair_file(store)
    set_placement(store, options.placement)
    record_fallback(store, options.fallback, options.fallback_args)
    return spawn_sidekick(
        ctx,
        store,
        SpawnRequest(
            options.kind,
            options.permission,
            field(store, 'master', 'pane_id'),
            options.timeout,
            options.direction,
            options.pane,
            options.native,
        ),
    )


def spawn(ctx, args):
    end_with_parent()
    require_herdr()
    if not args:
        raise Die(usage(ctx))
    return start_sidekick(ctx, args[0], spawn_options(args[1:]))
