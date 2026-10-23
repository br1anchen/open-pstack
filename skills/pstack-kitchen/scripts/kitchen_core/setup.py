"""init, spawn, consultant and fleet: the commands that set a kitchen run up.

The pair's init and spawn are called as the core's functions.
kitchen.py is reached through kpy.run, in process: `roster`, `validate`, `setup` and `fleet` read the repo's kitchen and print, or (setup) run the
repo's commands as a child process with a copy of the environment. None writes os.environ or reads INVOCATION (kitchen.py :745, :771, :1023, :1038 belong
to `gate` and to `brief-verify` without --list). test_setup_kpy_calls_leave_the_environment_alone pins that."""

import json
import os
import sys
from dataclasses import replace

from pstack_core import agents, spawn as pair_spawn
from pstack_core.spawn import spawn_options, start_sidekick
from pstack_core.argv import Spec, read
from pstack_core.commands import init as core_init
from pstack_core.store import field, read_text, event, json_type, listing, load, member, now, pair_file, text_at, update
from pstack_core.commands.report import require_pair
from pstack_core.errors import DamagedStore, Die, Exit, SetupFailed, ToolFailed
from pstack_core import git
from pstack_core.panes import agent_status, require_herdr
from pstack_core.wait import end_with_parent

from . import kpy
from .units import has_pair, repo_root


def last_build_path(builds):
    last = builds[-1]
    if not isinstance(last, dict):
        raise DamagedStore(f'skill_builds holds {json_type(last)}, not an object')
    return text_at(last, 'path')


def record_skill_build(ctx, store):
    """A run stamped at init lists the skills directories its commands ran from; a command from a different one adds a row. A run from before
    stamps stays unstamped."""
    skills_dir = os.path.dirname(ctx.skill_root)
    if not has_pair(store):
        return
    raw = load(store).get('skill_builds')
    if raw is None or raw is False:
        return
    builds = listing(load(store), 'skill_builds')
    if builds and last_build_path(builds) == skills_dir:
        return

    def mutate(doc):
        current = doc.get('skill_builds')
        if not isinstance(current, list):
            raise DamagedStore(f'skill_builds is {json_type(current)}, not an array')
        doc['skill_builds'] = current + [{'path': skills_dir, 'at': stamp}]
    stamp = now()
    update(store, mutate)


def stamp_skill_builds(ctx, store):
    skills_dir = os.path.dirname(ctx.skill_root)
    stamp = now()

    def mutate(doc):
        doc['skill_builds'] = [{'path': skills_dir, 'at': stamp}]
    update(store, mutate)


def quiet_kitchen(store, args):
    """`kpy "$store" ARGS 2>/dev/null`: (status, stdout without its trailing newlines)."""
    status, out, _ = kpy.run(repo_root(store), args)
    return status, out.rstrip('\n')


def init(ctx, args):
    lines = []
    made = core_init.init_store(ctx, core_init.init_request(ctx, list(args)), lines.append)
    sys.stdout.write(''.join(lines))
    store, orders = made.store, made.orders
    stamp_skill_builds(ctx, store)
    status, found = quiet_kitchen(store, ['--json', 'validate'])
    if status != 0:
        sys.stdout.flush()
        sys.stderr.write('kitchen: none valid in this repo; run pstack-kitchen-setup before dispatching\n')
        return 0
    text = read_text(orders)
    data = json.loads(found)
    if 'kitchen.toml review.self' not in text and (data.get('self') or []):
        with open(orders, 'a') as f:
            f.write(f'16. Before reporting done, the sidekick runs these skills on its diff and fixes what they find: {", ".join(data["self"])} (kitchen.toml review.self).\n')
    if 'kitchen.toml landing.mode' not in text:
        with open(orders, 'a') as f:
            f.write(f'17. Landing goes as far as `{data.get("landing") or ""}` (kitchen.toml landing.mode) and never merges.\n')
    worktree_setup(ctx, store, found)
    return 0


def worktree_setup(ctx, store, found):
    """A run in a linked worktree (git worktree add, a jj workspace) starts without the main checkout's untracked dependencies, so its gates would run
    whatever tool the PATH has instead of the repo's pinned one. init prepares it once the way verifier scratches are prepared: the repo's
    [scratch].setup, as the sidekick. A failed setup stops init with its log. found is validate's JSON."""
    require_pair(ctx, store)
    root = repo_root(store)
    if not (json.loads(found).get('setup') or []):
        return
    if git.run(root, 'rev-parse', '--path-format=absolute', '--git-dir').stdout.rstrip('\n') == git.run(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.rstrip('\n'):
        return
    status, out, err = kpy.run(root, ['setup', '--role', 'sidekick'])
    out = (out + err).rstrip('\n')
    event(store, 'master', 'setup', '-', 'pass' if status == 0 else 'fail')
    if status != 0:
        raise SetupFailed(f'this run is in a linked worktree and its [scratch].setup failed:\n{out}')
    sys.stdout.write(f'worktree: prepared with [scratch].setup ({out})\n')


def roster_json():
    """`python3 kitchen.py --json roster`, a plain assignment: a failure ends the command with its status."""
    status, out, err = kpy.run(None, ['--json', 'roster'])
    if err:
        sys.stdout.flush()
        sys.stderr.write(err)
    if status != 0:
        raise ToolFailed(status)
    return out.rstrip('\n')


def roster_text(roster, *keys):
    """The string at keys in kitchen.py's roster, which prints each role as an object and every args as a list of strings; '' when it is null."""
    value = roster
    for key in keys:
        value = value[key]
    return value or ''


def roster_lines(roster, *keys):
    """The list at keys in kitchen.py's roster."""
    value = roster
    for key in keys:
        value = value[key]
    return list(value)


def spawn(ctx, args):
    require_herdr()
    if len(args) < 1:
        raise Die('usage: kitchen.sh spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [-- agent-args...]')
    store, fb_args = args[0], []
    opts = read(args[1:], Spec(valued=('--kind', '--fallback', '--fallback-arg'), dashdash=True, unknown='collect'))
    kind, fallback = opts.last('--kind', ''), opts.last('--fallback', '')
    native = list(opts.after_dashdash)
    roster = json.loads(roster_json())
    if not kind:
        kind = roster_text(roster, 'sidekick', 'kind')
        if not kind:
            raise Die('no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)')
        if not native:
            native = roster_lines(roster, 'sidekick', 'args')
    if not fallback and kind == roster_text(roster, 'sidekick', 'kind'):
        fallback = roster_text(roster, 'sidekick', 'fallback', 'kind')
        fb_args = roster_lines(roster, 'sidekick', 'fallback', 'args')
    given = spawn_options(list(opts.leftovers))
    end_with_parent()
    return start_sidekick(ctx, store, replace(given, kind=kind, fallback=fallback, native=tuple(native),
                                              fallback_args=(*opts.all('--fallback-arg'), *fb_args)))


def consultant(ctx, args):
    """The consultant joins only when a unit escalates: the scarcest quota is spent on design questions that warrant it."""
    require_herdr()
    if len(args) < 1:
        raise Die('usage: kitchen.sh consultant <store> --reason TEXT [--kind KIND] [--permission MODE|none] [-- agent-args...]')
    store = args[0]
    opts = read(args[1:], Spec(valued=('--reason', '--kind', '--permission'), dashdash=True))
    reason, kind, permission = opts.last('--reason', ''), opts.last('--kind', ''), opts.last('--permission', '')
    native = list(opts.after_dashdash)
    pair_file(store)
    if not reason:
        raise Die('--reason is required: the escalation that warrants the consultant')
    fallback, fb_args = '', []
    if not kind:
        roster = json.loads(roster_json())
        kind = roster_text(roster, 'consultant', 'kind')
        if not native:
            native = roster_lines(roster, 'consultant', 'args')
        fallback = roster_text(roster, 'consultant', 'fallback', 'kind')
        fb_args = roster_lines(roster, 'consultant', 'fallback', 'args')
    if not kind:
        raise Die('no --kind and no [consultant] kind in the host roster')
    # A consultant whose kind has a preflight (Muse, pi, Prime) is checked before its pane starts, as the sidekick is: a logged-out account or a
    # spent plan starts the roster's [consultant.fallback] instead.
    if agents.has_preflight(kind) and agent_status(field(store, 'consultant', 'name')) == 'absent':
        code, why = pair_spawn.preflight_reason(kind, native)
        if code != 0:
            if not fallback or fallback == kind:
                raise Die(f'consultant {kind} failed its preflight and the roster names no other fallback: {why or "no answer"}', 2)
            shown = ((why or 'no answer').replace('\n', ' ') + ' ')[:200]
            sys.stdout.write(f'failover: consultant {kind} -> {fallback} (preflight: {shown})\n')
            record_consultant_failover(store, kind, fallback, f'preflight: {why or "no answer"}')
            event(store, 'master', 'failover', '-', f'consultant:{kind}:{fallback}')
            kind = fallback
            native = fb_args
    # The escalation is recorded once the consultant is up, so a start that fails (a trust prompt) and is retried counts it once.
    end_with_parent()
    code = pair_spawn.spawn_consultant(ctx, store, kind, permission, '60000', list(native))
    if code:
        raise Exit(code)
    stamp = now()

    def mutate(doc):
        doc['escalations'] = listing(doc, 'escalations') + [{'reason': reason, 'at': stamp}]
    update(store, mutate)
    event(store, 'master', 'escalate', '-', reason)
    return 0


def record_consultant_failover(store, source, target, reason):
    """Appends {from, to, reason, at} to .consultant.failovers."""
    stamp = now()

    def mutate(doc):
        entry = member(doc, 'consultant')
        entry['failovers'] = listing(entry, 'failovers') + [{'from': source, 'to': target, 'reason': reason, 'at': stamp}]
    update(store, mutate)


def fleet(ctx, args):
    """Every run on the host and each skills build; read-only and repo-free."""
    status, out, err = kpy.run(None, ['fleet', *args])
    sys.stdout.write(out)
    sys.stdout.flush()
    sys.stderr.write(err)
    return status
