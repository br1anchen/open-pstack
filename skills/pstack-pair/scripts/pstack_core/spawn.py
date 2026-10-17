"""Starting a role's agent in a pane and bootstrapping it: spawn_role and the recovery of a bootstrap prompt that a first-run screen swallowed.
Returns 0, 3 (the bootstrap did not settle) or 4 (it settled without the ready file); a die is a Die."""
import io
import json
import os
import re
import subprocess
import sys
import time

from . import agents
from .dispatch import fresh_per_brief
from .errors import Die
from .herdr import agent_field, agent_status, herdr
from .panes import await_file, new_pane, refuse_untrusted
from .permission import mode_of_this_shell
from .providers import provider_hint, record_prime_session
from .review import jq_raw
from .send import submit_typed, visible_without_blanks
from .store import alt, event, pair_json, update, update_or_jq
from .wait import bash_sleep, die_with_the_shell

FRESH_SESSION_FLAGS = ('-c', '--continue', '-r', '--resume', '--session', '--session-id', '--fork')


def section(pair, role):
    value = pair.get(role)
    return value if isinstance(value, dict) else {}


def field(store, role, key):
    return jq_raw(section(pair_json(store), role).get(key))


def update_role(store, role, changes):
    """json_update of .[role] keys, in order, creating the role's entry when it is null."""
    def mutate(doc):
        entry = doc.get(role)
        if entry is None:
            entry = doc[role] = {}
        entry.update(changes)
    update(store, mutate)


def mark_ready(store, role):
    update_role(store, role, {'rotation_required': False, 'bootstrap_pending': False})


def stderr_of(text):
    sys.stderr.write(text)


def bootstrap_recover(ctx, store, name, role, pane, ready, bootstrap, generation):
    """After a bootstrap prompt stalled and no ready file exists: an idle or done agent in this pane gets the Enter its input still waits for, or
    the bootstrap sent once more when nothing was typed (a blocked agent needs the human); otherwise state detection lagged on a narrow pane while
    the agent works. False only for a blocked agent."""
    bash_sleep(os.environ.get('PAIR_SUBMIT_CHECK_S') or '5')
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
        if os.path.basename(ready).encode() in visible_without_blanks(name):
            herdr('agent', 'send-keys', name, 'enter')
        else:
            subprocess.run(['herdr', 'agent', 'prompt', name, bootstrap, '--wait', '--timeout', '240000'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    elif state == 'blocked':
        stderr_of(f'{name} is blocked on a dialog that needs the human; no keys were sent. Read it with: herdr agent read {name} --source visible --lines 60\n')
        return False
    return True


def spawn_role(ctx, store, role, kind, permission, anchor, timeout, direction, pane, args):
    master_mode = mode_of_this_shell(False)
    if not permission:
        permission = 'bypassPermissions' if kind == 'devin' else master_mode
    agent_args = []
    if permission == 'none' or agents.has_permission_arg(args):
        permission = 'native-args'
    else:
        agent_args = agents.permission_args(kind, permission)
        if not agent_args and not agents.asks_no_approvals(kind, permission):
            stderr_of(f'no permission translation for kind {kind}; pass native flags after -- to match the master ({master_mode})\n')
            permission = 'untranslated'
    cwd = jq_raw(pair_json(store).get('cwd'))
    if not agents.has_trust_arg(args):
        agent_args += agents.trust_args(kind)
    if not agents.has_mcp_arg(args):
        agent_args += agents.mcp_args(kind, cwd)[0]
    args = [*agent_args, *args]
    if fresh_per_brief(kind):
        for arg in args:
            if arg in FRESH_SESSION_FLAGS or arg.startswith(('--resume=', '--session=')):
                raise Die(f'{kind} task sessions start fresh; recover partial work from the store instead of passing {arg}', 5)
    ready = 'advice/000-ready.md' if role == 'consultant' else 'reports/000-ready.md'
    name = field(store, role, 'name')
    status = agent_status(name)
    if status != 'absent':
        if jq_raw(alt(section(pair_json(store), role).get('bootstrap_pending'), False)) == 'true':
            if not os.path.isfile(f'{store}/{ready}'):
                stderr_of(f'{role} bootstrap still pending: {ready} is missing\n')
                return 4
            mark_ready(store, role)
            print(f'ready: {store}/{ready}')
            event(store, role, 'spawn', '-', f'{field(store, role, "kind")}:ready')
        print(f'{role} {name} already live ({status}) in {field(store, role, "pane_id")}')
        return 0
    prime_start_failed = False
    if not pane:
        previous = alt(section(pair_json(store), role).get('pane_id'), '')
        if previous and herdr('pane', 'get', jq_raw(previous))[0] == 0:
            pane = jq_raw(previous)
    if pane:
        r = subprocess.run(['herdr', 'agent', 'start', name, '--kind', kind, '--pane', pane, '--timeout', timeout, '--', *args],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if r.returncode == 0:
            print(f'reused pane {pane}')
        else:
            refuse_untrusted(pane, kind, cwd)
            stderr_of(f'pane {pane} unavailable for agent start: {r.stdout.decode("utf-8", "surrogateescape").rstrip(chr(10))}\n')
            if kind == 'prime-agent':
                prime_start_failed = True
            else:
                pane = ''
    if not pane:
        pane = new_pane(store, role, anchor, direction, cwd)
        r = subprocess.run(['herdr', 'agent', 'start', name, '--kind', kind, '--pane', pane, '--timeout', timeout, '--', *args], stdout=subprocess.DEVNULL, check=False)
        if r.returncode != 0:
            if kind == 'prime-agent':
                prime_start_failed = True
            else:
                refuse_untrusted(pane, kind, cwd, True)
                raise Die(f'agent start failed in {pane}; inspect: herdr pane read {pane} --source visible', 2)
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def started(doc):
        entry = doc.get(role)
        if entry is None:
            entry = doc[role] = {}
        entry['pane_id'], entry['kind'], entry['started_at'], entry['start_args'] = pane, kind, now, list(args)
        entry['generation'] = alt(entry.get('generation'), 0) + 1
        entry['bootstrap_pending'] = True
        entry['permission_mode'] = permission
        master = doc.get('master')
        if master is None:
            master = doc['master'] = {}
        master['permission_mode'] = master_mode
    update(store, started)
    if kind == 'prime-agent':
        update_role(store, role, {'session': None, 'session_stopped': False, 'closed': False, 'launch_cwd': cwd})
        if prime_start_failed:
            record_prime_session(store, role)
            raise Die(f'Prime start failed in {pane}; its pane and recorded launch stay for inspection', 5)
        if not record_prime_session(store, role):
            raise Die(f'Prime did not report its session; inspect {name} before prompting', 5)
    print(f'{role} {name} ({kind}) started in {pane} with permission {permission} (master: {master_mode})')
    generation = field(store, role, 'generation')
    if os.path.isfile(f'{store}/{ready}'):
        os.makedirs(f'{store}/sessions', exist_ok=True)
        os.replace(f'{store}/{ready}', f'{store}/sessions/{role}-{field(store, role, "generation")}-previous-ready.md')
    bootstrap = (f'Load the {ctx.skill} skill from ~/.agents/skills/{ctx.skill}/SKILL.md and take the {role} role (generation '
                 f'{field(store, role, "generation")}, pane {pane}). {ctx.store_label} store: {store}. Follow its bootstrap steps, write {ready}, and reply READY.')
    r = subprocess.run(['herdr', 'agent', 'prompt', name, bootstrap, '--wait', '--timeout', '240000'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    code, err = r.returncode, r.stderr.decode('utf-8', 'surrogateescape').rstrip('\n')
    if not os.path.isfile(f'{store}/{ready}'):
        submitted = submit_typed(name, os.path.basename(ready))
        if submitted != 0 and code == 0:
            await_file(f'{store}/{ready}', 240000)
    if code != 0 and 'agent_prompt_stalled' in err:
        # A first-run screen (pi's changelog after an update, a trust prompt, Claude's update banner) can take the Enter that submits the bootstrap,
        # or swallow the prompt whole. bootstrap_recover acts only on a live idle agent in this pane; otherwise state detection lagged on a narrow
        # pane while the agent works. Either way the ready file is the signal.
        if bootstrap_recover(ctx, store, name, role, pane, ready, bootstrap, generation):
            print(f'{role} prompt looked stalled; waiting for {ready} instead')
            if await_file(f'{store}/{ready}', int(os.environ.get('PAIR_BOOTSTRAP_WAIT_MS') or 240000)):
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
    stderr_of(f'{role} settled but {ready} is missing; state={agent_status(name)}. Read the pane before prompting again.\n')
    # A provider stop (a rate limit) is the usual cause, and it shows only in the pane or the agent's session log.
    if role == 'sidekick':
        stderr_of(provider_hint(store, 'bootstrap'))
    event(store, role, 'spawn', '-', f'{kind}:unready')
    return 4


def entry_spawn_role(ctx, args):
    """__spawn_role store role kind permission anchor timeout direction pane [native args...]: the positional arguments the bash function takes."""
    die_with_the_shell()
    if len(args) < 8:
        raise Die('usage: __spawn_role <store> <role> <kind> <permission> <anchor> <timeout> <direction> <pane> [args...]')
    return spawn_role(ctx, *args[:8], list(args[8:]))


def set_placement(store, want=''):
    """How roles get their panes: --tab or --split from spawn, else what the store has, else PSTACK_PLACEMENT, else split."""
    if not want and jq_raw(alt(pair_json(store).get('placement'), '')) == '':
        want = os.environ.get('PSTACK_PLACEMENT') or 'split'
    if not want:
        return
    if want not in ('split', 'tab'):
        raise Die(f'placement must be split or tab, not {want}')

    def mutate(doc):
        doc['placement'] = want
    update_or_jq(store, mutate, '.placement = $p', '--arg', 'p', want)


def record_failover(store, source, target, reason):
    """The sidekick's failover from one kind to another: .sidekick.failovers, the master's failover event, and the line it prints."""
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def mutate(doc):
        entry = doc.get('sidekick')
        if entry is None:
            entry = doc['sidekick'] = {}
        current = alt(entry.get('failovers'), [])
        if not isinstance(current, list):
            raise TypeError('failovers is not an array')
        entry['failovers'] = current + [{'from': source, 'to': target, 'reason': reason, 'at': now}]
    update_or_jq(store, mutate, '.sidekick.failovers = ((.sidekick.failovers // []) + [{from: $from, to: $to, reason: $why, at: $now}])',
                 '--arg', 'from', source, '--arg', 'to', target, '--arg', 'why', reason, '--arg', 'now', now)
    event(store, 'master', 'failover', '-', f'{source}:{target}')
    print(f'failover: {source} -> {target} ({reason})')


def record_fallback(store, kind, args):
    """The sidekick's fallback from the spawn options; an empty kind keeps any recorded one."""
    if not kind:
        return

    def mutate(doc):
        entry = doc.get('sidekick')
        if entry is None:
            entry = doc['sidekick'] = {}
        entry['fallback'] = {'kind': kind, 'args': list(args)}
    update_or_jq(store, mutate, '.sidekick.fallback = {kind: $kind, args: $args}', '--arg', 'kind', kind, '--argjson', 'args', json.dumps(list(args), separators=(',', ':')))


def fallback_args(fallback):
    """mapfile -t of jq -r '.sidekick.fallback.args[]?': one argument per output line."""
    values = fallback.get('args') if isinstance(fallback, dict) else None
    values = values if isinstance(values, list) else list(values.values()) if isinstance(values, dict) else []
    return ''.join(f'{jq_raw(v)}\n' for v in values).split('\n')[:-1]


def preflight_reason(kind, args):
    """(status, text): preflight_kind with its stderr captured, as `$(preflight_kind ... 2>&1)` leaves it."""
    seen, sys.stderr = sys.stderr, io.StringIO()
    try:
        code = agents.preflight_kind(kind, args)
        return code, sys.stderr.getvalue().rstrip('\n')
    finally:
        sys.stderr = seen


def spawn_sidekick(ctx, store, kind, permission, anchor, timeout, direction, pane, args):
    """Starts the sidekick, or its recorded fallback when the kind's preflight fails. The fallback starts with its own recorded arguments and
    its kind's default permission. A live sidekick skips the preflight. Returns spawn_role's 0, 3 or 4."""
    if agents.has_preflight(kind) and agent_status(field(store, 'sidekick', 'name')) == 'absent':
        code, reason = preflight_reason(kind, args)
        if code != 0:
            reason = f'preflight: {reason or "no answer"}'
            sys.stderr.write(f'{kind} failed {reason}\n')
            fallback = section(pair_json(store), 'sidekick').get('fallback')
            name = jq_raw(alt(fallback.get('kind'), '')) if isinstance(fallback, dict) else ''
            if not name or name == kind:
                raise Die(f'no fallback recorded for a failed {kind}; pass --fallback KIND or fix the agent', 2)
            record_failover(store, kind, name, reason)
            return spawn_role(ctx, store, 'sidekick', name, '', anchor, timeout, direction, pane, fallback_args(fallback))
    return spawn_role(ctx, store, 'sidekick', kind, permission, anchor, timeout, direction, pane, args)
