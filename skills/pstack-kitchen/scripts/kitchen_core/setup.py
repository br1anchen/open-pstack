"""init, spawn, consultant and fleet: the commands that set a kitchen run up. Ported from kitchen.sh at b632713 (cmd_init, worktree_setup, cmd_spawn,
cmd_consultant, cmd_fleet and record_skill_build).

The bash wrapped the pair's init and spawn (core_init, core_spawn, snapshots of pair-core's commands); here they call the core's functions directly.
kitchen.py is reached through kpy.run, in process: `roster`, `validate`, `setup` and `fleet` read the repo's kitchen and print, or (setup) run the
repo's commands as a child process with a copy of the environment. None writes os.environ or reads INVOCATION (kitchen.py :745, :771, :1023, :1038 belong
to `gate` and to `brief-verify` without --list). test_setup_kpy_calls_leave_the_environment_alone pins that."""

import os
import sys

from pstack_core import agents
from pstack_core import spawn as pair_spawn
from pstack_core.commands import init as core_init
from pstack_core.commands import spawn as core_spawn
from pstack_core.commands.report import in_herdr
from pstack_core.errors import Die, Exit, unbound_arg
from pstack_core.gates import gnu_dirname
from pstack_core.herdr import agent_status
from pstack_core.store import alt, cut_chars, event, now, pair_file, update_or_jq
from pstack_core.wait import die_with_the_shell

from . import kpy
from .classify import jq, jq_lines
from .jqx import tolerant_field, pair_jq
from .units import git, git_root
from .verify import in_subshell


def record_skill_build(ctx, store):
    """A run stamped at init lists the skills directories its commands ran from; a command from a different one adds a row. A run from before
    stamps stays unstamped."""
    skills_dir = os.path.dirname(ctx.skill_root)
    if pair_jq(store, '.skill_builds // empty | length') == '':
        return
    if pair_jq(store, '.skill_builds[-1].path') == skills_dir:
        return

    def mutate(doc):
        current = doc.get('skill_builds')
        if not isinstance(current, list):
            raise TypeError('skill_builds is not an array')
        doc['skill_builds'] = current + [{'path': skills_dir, 'at': stamp}]
    stamp = now()
    update_or_jq(store, mutate, '.skill_builds += [{path: $p, at: $a}]', '--arg', 'p', skills_dir, '--arg', 'a', stamp)


def record_skill_build_entry(ctx, args):
    """__record_skill_build store: kitchen.sh runs it before any command whose second word is a store with a pair.json."""
    record_skill_build(ctx, args[0])
    return 0


def stamp_skill_builds(ctx, store):
    skills_dir = os.path.dirname(ctx.skill_root)
    stamp = now()

    def mutate(doc):
        doc['skill_builds'] = [{'path': skills_dir, 'at': stamp}]
    update_or_jq(store, mutate, '.skill_builds = [{path: $p, at: $a}]', '--arg', 'p', skills_dir, '--arg', 'a', stamp)


def quiet_kitchen(store, args):
    """`kpy "$store" ARGS 2>/dev/null`: (status, stdout without its trailing newlines)."""
    status, out, _ = kpy.run(git_root(store), args)
    return status, out.rstrip('\n')


def init(ctx, args):
    def run_core_init():
        code = core_init.run(ctx, list(args))
        if code:
            raise Exit(code)

    code, out = in_subshell(run_core_init, merge=False)
    if code:
        raise Exit(code)
    sys.stdout.write(out + '\n')
    orders = '\n'.join(line[len('standing orders: '):] for line in out.split('\n') if line.startswith('standing orders: '))
    store = gnu_dirname(orders)
    stamp_skill_builds(ctx, store)
    status, found = quiet_kitchen(store, ['--json', 'validate'])
    if status != 0:
        sys.stdout.flush()
        sys.stderr.write('kitchen: none valid in this repo; run pstack-kitchen-setup before dispatching\n')
        return 0
    with open(orders, 'rb') as f:
        text = f.read()
    if b'kitchen.toml review.self' not in text and jq(found, '.self | length') != '0':
        with open(orders, 'a') as f:
            f.write(f'16. Before reporting done, the sidekick runs these skills on its diff and fixes what they find: {jq(found, ".self | join(\", \")")} (kitchen.toml review.self).\n')
    if b'kitchen.toml landing.mode' not in text:
        with open(orders, 'a') as f:
            f.write(f'17. Landing goes as far as `{jq(found, ".landing")}` (kitchen.toml landing.mode) and never merges.\n')
    worktree_setup(store, found)
    return 0


def worktree_setup(store, found):
    """A run in a linked worktree (git worktree add, a jj workspace) starts without the main checkout's untracked dependencies, so its gates would run
    whatever tool the PATH has instead of the repo's pinned one. init prepares it once the way verifier scratches are prepared: the repo's
    [scratch].setup, as the sidekick. A failed setup stops init with its log. found is validate's JSON."""
    root = git_root(store, check=True)
    try:
        if int(jq(found, '.setup | length')) <= 0:
            return
    except ValueError:
        return
    if git(root, 'rev-parse', '--path-format=absolute', '--git-dir')[1].rstrip('\n') == git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')[1].rstrip('\n'):
        return
    status, out, err = kpy.run(root, ['setup', '--role', 'sidekick'])
    out = (out + err).rstrip('\n')
    event(store, 'master', 'setup', '-', 'pass' if status == 0 else 'fail')
    if status != 0:
        raise Die(f'this run is in a linked worktree and its [scratch].setup failed:\n{out}', 2)
    sys.stdout.write(f'worktree: prepared with [scratch].setup ({out})\n')


def roster_json():
    """`python3 kitchen.py --json roster`, a plain assignment: a failure ends the command with its status."""
    status, out, err = kpy.run(None, ['--json', 'roster'])
    if err:
        sys.stdout.flush()
        sys.stderr.write(err)
    if status != 0:
        raise Exit(status)
    return out.rstrip('\n')


def lines_of(text):
    """`mapfile -t array < <(jq -r ...)`: one element per output line."""
    return text.split('\n')[:-1] if text else []


def spawn(ctx, args):
    in_herdr()
    if len(args) < 1:
        raise Die('usage: kitchen.sh spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [-- agent-args...]')
    store, kind, fallback, rest, native, fb_args = args[0], '', '', [], [], []
    rest_in = list(args[1:])
    while rest_in:
        flag = rest_in[0]
        if flag in ('--kind', '--fallback'):
            if len(rest_in) < 2:
                raise unbound_arg()
            if flag == '--kind':
                kind = rest_in[1]
            else:
                fallback = rest_in[1]
            rest_in = rest_in[2:]
        elif flag == '--':
            native = rest_in[1:]
            break
        else:
            rest.append(flag)
            rest_in = rest_in[1:]
    roster = roster_json()
    if not kind:
        kind = jq(roster, '.sidekick.kind')
        if not kind:
            raise Die('no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)')
        if not native:
            native = lines_of(jq_lines(roster, '.sidekick.args[]'))
    if not fallback and kind == jq(roster, '.sidekick.kind'):
        fallback = jq(roster, '.sidekick.fallback.kind')
        fb_args = lines_of(jq_lines(roster, '.sidekick.fallback.args[]'))
    if fallback:
        rest += ['--fallback', fallback]
    for a in fb_args:
        rest += ['--fallback-arg', a]
    return core_spawn.spawn(ctx, [store, '--kind', kind, *rest, '--', *native])


def consultant(ctx, args):
    """The consultant joins only when a unit escalates: the scarcest quota is spent on design questions that warrant it."""
    in_herdr()
    if len(args) < 1:
        raise Die('usage: kitchen.sh consultant <store> --reason TEXT [--kind KIND] [--permission MODE|none] [-- agent-args...]')
    store, reason, kind, permission, native = args[0], '', '', '', []
    rest = list(args[1:])
    while rest:
        flag = rest[0]
        if flag in ('--reason', '--kind', '--permission'):
            if len(rest) < 2:
                raise unbound_arg()
            if flag == '--reason':
                reason = rest[1]
            elif flag == '--kind':
                kind = rest[1]
            else:
                permission = rest[1]
            rest = rest[2:]
        elif flag == '--':
            native = rest[1:]
            break
        else:
            raise Die(f'unknown option {flag}')
    pair_file(store)
    if not reason:
        raise Die('--reason is required: the escalation that warrants the consultant')
    fallback, fb_args = '', []
    if not kind:
        roster = roster_json()
        kind = jq(roster, '.consultant.kind')
        if not native:
            native = lines_of(jq_lines(roster, '.consultant.args[]'))
        fallback = jq(roster, '.consultant.fallback.kind // empty')
        fb_args = lines_of(jq_lines(roster, '.consultant.fallback.args[]?'))
    if not kind:
        raise Die('no --kind and no [consultant] kind in the host roster')
    # A consultant whose kind has a preflight (Muse, pi, Prime) is checked before its pane starts, as the sidekick is: a logged-out account or a
    # spent plan starts the roster's [consultant.fallback] instead.
    if agents.has_preflight(kind) and agent_status(tolerant_field(store, 'consultant', 'name')) == 'absent':
        code, why = pair_spawn.preflight_reason(kind, native)
        if code != 0:
            if not fallback or fallback == kind:
                raise Die(f'consultant {kind} failed its preflight and the roster names no other fallback: {why or "no answer"}', 2)
            shown = cut_chars((why or 'no answer').replace('\n', ' ') + ' ', 200)
            sys.stdout.write(f'failover: consultant {kind} -> {fallback} (preflight: {shown})\n')
            record_consultant_failover(store, kind, fallback, f'preflight: {why or "no answer"}')
            event(store, 'master', 'failover', '-', f'consultant:{kind}:{fallback}')
            kind = fallback
            native = fb_args
    # The escalation is recorded once the consultant is up, so a start that fails (a trust prompt) and is retried counts it once.
    die_with_the_shell()
    code = pair_spawn.spawn_consultant(ctx, store, kind, permission, '60000', list(native), errexit_off=False)
    if code:
        raise Exit(code)
    stamp = now()

    def mutate(doc):
        current = alt(doc.get('escalations'), [])
        if not isinstance(current, list):
            raise TypeError('escalations is not an array')
        doc['escalations'] = current + [{'reason': reason, 'at': stamp}]
    update_or_jq(store, mutate, '.escalations = ((.escalations // []) + [{reason: $why, at: $now}])', '--arg', 'why', reason, '--arg', 'now', stamp)
    event(store, 'master', 'escalate', '-', reason)
    return 0


def record_consultant_failover(store, source, target, reason):
    stamp = now()

    def mutate(doc):
        entry = doc.get('consultant')
        if entry is None:
            entry = doc['consultant'] = {}
        current = alt(entry.get('failovers'), [])
        if not isinstance(current, list):
            raise TypeError('failovers is not an array')
        entry['failovers'] = current + [{'from': source, 'to': target, 'reason': reason, 'at': stamp}]
    update_or_jq(store, mutate, '.consultant.failovers = ((.consultant.failovers // []) + [{from: $from, to: $to, reason: $why, at: $now}])',
                 '--arg', 'from', source, '--arg', 'to', target, '--arg', 'why', reason, '--arg', 'now', stamp)


def fleet(ctx, args):
    """Every run on the host and each skills build; read-only and repo-free."""
    status, out, err = kpy.run(None, ['fleet', *args])
    sys.stdout.write(out)
    sys.stdout.flush()
    sys.stderr.write(err)
    return status
