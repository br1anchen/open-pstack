"""verify: starts a verification (a fresh verifier on a scratch worktree, or the gates alone) and waits for it.

It is built on units.py (ranges, inclusion, verdicts), verify_state.py (the claim, the compare-and-set, the close, the cancel) and verify_wait.py (the
wait); nothing calls back into it. A command that fails ends it with an Exit or a Die.

kitchen.py is reached through kpy.run, in process. The commands verify makes (roster, snapshot, classify, timing, brief-verify --list and
lesson-packet) read the repo's kitchen and git and print; none of them writes os.environ or reads INVOCATION: those belong to `gate` and to
`brief-verify` without --list (kitchen.py :745, :771, :1023, :1038), which verify never runs. test_verify_kpy_calls_leave_the_environment_alone pins
that."""

import glob
import io
import json
import os
import re
import sys
import time
import contextlib
from types import SimpleNamespace

from pstack_core.argv import Spec, read as read_options
from pstack_core.agents import has_mcp_arg, has_permission_arg, has_trust_arg, mcp_args, permission_args, trust_args
from pstack_core.commands import scratch as scratch_command
from pstack_core.store import (
    wake,     env_float, env_int, event, expected_report, field, header_field, json_type, load, names, pair_file, peek, read, text_at, read_text, write_text,
    update, whole_number,
)
from pstack_core.commands.report import require_pair
from pstack_core.errors import (
    BadRevision, BadVerifierEntry, CloseHeld, DamagedStore, Die, PairError, PrimeFailed, ToolFailed, UnknownVerifierKind, VerifierStartFailed,
    translate,
)
from pstack_core import git
from pstack_core.panes import TRUST_RE, agent_kind, agent_prompt, agent_start, agent_status, herdr, new_pane, parse, reply_at, require_herdr
from pstack_core.permission import mode_of_this_shell
from pstack_core.providers import record_prime_session
from pstack_core.send import submit_typed
from pstack_core.variant import commit_of

from .kpy import Kitchen
from . import verify_state as vs
from .classify import brief_kitchen, kitchen_snapshot
from .landing import is_landing, latest_landing_verdict, manifest_warning
from .verify_wait import warn_unreviewed, Waiter
from .units import captured, StackIndex, Verdict, brief_or_nothing, heads_in, inclusion, is_ancestor, landing_base, repo_root, unit_brief, unit_range

USAGE = ('usage: kitchen.sh verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND] | '
         'kitchen.sh verify <store> --landing [--base REV] [--parent REF|none] [--head REV] [...] | kitchen.sh verify <store> --wait '
         '[--every MIN | --until-verdict] | kitchen.sh verify <store> --cancel')


def out(text):
    sys.stdout.write(text)


def warn(text):
    sys.stdout.flush()
    sys.stderr.write(text)


def shell_word(word):
    """A word as the verifier line shows it: backslashes before the characters a shell treats as special, $'...' for a word with control characters."""
    if word == '':
        return "''"
    if any(ord(c) < 32 or ord(c) == 127 for c in word):
        names = {'\n': '\\n', '\t': '\\t', '\r': '\\r', '\x1b': '\\E', '\x07': '\\a', '\x08': '\\b', '\x0c': '\\f', '\x0b': '\\v', "'": "\\'", '\\': '\\\\'}
        return "$'" + ''.join(names.get(c, f'\\{ord(c):03o}' if ord(c) < 32 or ord(c) == 127 else c) for c in word) + "'"
    escaped = re.sub(r'([ !"$&\'()*,;<>?\[\\\]^`{|}])', r'\\\1', word)
    if escaped[0] in '#~':
        escaped = '\\' + escaped
    return escaped


def next_index(directory, prefix, letter):
    """The next k for files <prefix>-<letter><k>.* in directory."""
    k = 0
    for path in sorted(glob.glob(f'{glob.escape(directory)}/{glob.escape(prefix)}-{letter}[0-9]*')):
        if not os.path.exists(path):
            continue
        m = re.match(rf'.*-{letter}([0-9]+)', os.path.basename(path))
        if m and int(m.group(1)) > k:
            k = int(m.group(1))
    return k + 1


def fence_for(text):
    """A code fence for text: three backticks, or one more than the longest run of them in the text."""
    longest = max((len(m) for m in re.findall(r'`+', text)), default=0)
    return '`' * ((longest if longest >= 3 else 2) + 1)


def names_path(text, path):
    """Whether command text names path as a whole path: after a leading ~/, $HOME/ or ${HOME}/ is spelled out, path appears not inside a longer path and
    is followed by /, space, a quote, ), ;, &, | or the end."""
    home = os.environ.get('HOME', '')
    text = re.sub(r"""(^|[\s=:"'(])(~|\$HOME|\$\{HOME\}|"\$HOME"|"\$\{HOME\}")/""", lambda m: m.group(1) + home + '/', text, flags=re.M)
    return re.search(r"""(^|[^A-Za-z0-9_./-])""" + re.escape(path) + r"""(/|[\s"'`);&|]|$)""", text, flags=re.M) is not None


def brief_commands(kit, brief, unit):
    """The brief's Verify commands for a verifier's packet, each in a block of its own, run from the scratch root. A command that ends in
    `# master-only` (on its first or last line) writes the store, and one that names the main checkout or the store cannot run in the scratch; both are
    listed apart, in full, each with its reason. A Verify block the kitchen refuses is said so in the packet and warned of, not skipped."""
    store, root = kit.store, kit.root
    status, listed = kit.merged(['--json', 'brief-verify', brief, '--list'])
    if status != 0:
        first = listed.rstrip('\n').split('\n')[0]
        warn(f'warning: unit {unit or "?"}: brief-verify: {first}\n')
        return f'Brief commands: none (brief-verify: {first})\n\n'
    text, n, apart = '', 0, ''
    reply = parse(listed)
    commands = reply.get('commands') if isinstance(reply, dict) else None
    for command in commands if isinstance(commands, list) else []:
        parts = command.split('\n')
        first, last = parts[0], parts[-1]
        fence = fence_for(command)
        if re.search(r'# master-only[ \t\n\r\f\v]*$', first) or re.search(r'# master-only[ \t\n\r\f\v]*$', last):
            why = 'master-only'
        elif names_path(command, root) or names_path(command, store):
            why = 'absolute path'
        else:
            n += 1
            if n == 1:
                text += 'Brief commands:\n\nRun each command in a fresh shell from the scratch root.\n\n'
            text += f'Command {n}:\n\n{fence}bash\n{command}\n{fence}\n\n'
            continue
        apart += f'({why})\n{fence}bash\n{command}\n{fence}\n\n'
    if apart:
        text += f'not for the verifier:\n\n{apart}'
    return text


def landing_covered_briefs(store, root, base, head):
    """The done unit briefs a landing range holds: those whose head is in base..head, or whose commits were rebased into it."""
    covered, index = [], StackIndex.build(root, base, head)
    for brief in sorted(glob.glob(f'{glob.escape(store)}/briefs/[0-9][0-9][0-9]-*.md')):
        if not os.path.exists(brief) or header_field(expected_report(store, brief), 'status') != 'done' or is_landing(brief):
            continue
        from .units import RecordedUnit
        if inclusion(RecordedUnit(store, brief), index).covered:
            covered.append(brief)
    return covered


def verifier_entry(kit, cls):
    """The verifier for a class: the host roster's entry, with the sidekick marker resolved from the run (so after a failover it is the agent now
    implementing) and the master sentinel resolved to the master's own kind. One object: kind, args, fallback and where it came from."""
    store = kit.store
    roster = parse_entry(kit.elsewhere(['--json', 'roster'])[1])
    verifiers = roster.get('verifier') if isinstance(roster, dict) else None
    entry = verifiers.get(cls) if isinstance(verifiers, dict) else None
    kind = entry.get('kind') if isinstance(entry, dict) else ''
    if kind == 'sidekick':
        sidekick = peek(load(store), 'sidekick')
        fallback = sidekick.get('fallback')
        if fallback is None:
            fallback = {}
        elif not isinstance(fallback, dict):
            raise DamagedStore(f'sidekick.fallback is {json_type(fallback)}, not an object')
        return {'kind': sidekick.get('kind'), 'args': names(sidekick, 'start_args'), 'source': 'sidekick',
                'fallback': {'kind': fallback['kind'], 'args': names(fallback, 'args')} if fallback.get('kind') else None}
    if kind == 'master':
        return {**entry, 'kind': agent_kind(field(store, 'master', 'pane_id')), 'args': [], 'source': 'master'}
    return entry


def parse_entry(text):
    """The roster or a retried call's entry as an object; text that is not JSON is the one error."""
    try:
        return json.loads(text)
    except ValueError as e:
        raise BadVerifierEntry(f'verifier entry is not JSON: {e}')


def verifier_identity(kind, args):
    """The verifier as configured, for the start line and the packet: the kind and its model arguments, shell-quoted, with the permission and trust
    arguments left out; "<kind> (model unspecified)" when no argument is left. It is what was asked of the CLI, not proof of the model that answered."""
    skip, shown = False, ''
    for a in args:
        if skip:
            skip = False
            continue
        if a in ('--permission-mode', '--respect-workspace-trust', '-a', '-s', '--ask-for-approval', '--sandbox', '--mcp-config'):
            skip = True
            continue
        if (a.startswith(('--permission-mode=', '--respect-workspace-trust=', '--ask-for-approval=', '--sandbox=', '--mcp-config='))
                or a in ('--approve', '--no-approve', '-na', '--strict-mcp-config', '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions',
                         '--dangerously-bypass-approvals-and-sandbox')):
            continue
        shown += ' ' + shell_word(a)
    return f'{kind}{shown}' if shown else f'{kind} (model unspecified)'


def verifier_args(kind, entry, directory):
    """A verifier starts with its entry's arguments, then its kind's permission default, trust flag and MCP opt-out when they are missing; entry is the
    entry as an object (or None for a kind the class does not name), directory the repo the scratch is checked out from, whose MCP servers the
    opt-out names."""
    args = list(entry['args']) if isinstance(entry, dict) and isinstance(entry.get('args'), list) else []
    lines = []
    if not has_permission_arg(args):
        permission = 'bypassPermissions' if kind == 'devin' else mode_of_this_shell()
        lines += permission_args(kind, permission)
    if not has_trust_arg(args):
        lines += trust_args(kind)
    if not has_mcp_arg(args):
        lines += mcp_args(kind, directory)
    return [*lines, *args]


def record_verifier_session(store, name):
    """The agent's own session log, so a verifier that died on its provider or never got its packet can still say why after its pane is gone. Herdr
    gives pi's as a path and Claude's as an id, whose transcript Claude keeps under ~/.claude/projects/ in a directory named for the working directory."""
    code, info = herdr('agent', 'get', name)
    if code != 0:
        return
    agent = reply_at(info, 'result', 'agent', quiet=True)
    agent = agent if isinstance(agent, dict) else {}
    held = agent.get('agent_session')
    held = held if isinstance(held, dict) else {}
    session = text_at(held, 'value') if held.get('kind') == 'path' else ''
    if not session:
        ident = text_at(held, 'value') if agent.get('agent') == 'claude' and held.get('kind') == 'id' else ''
        cwd = text_at(agent, 'cwd')
        if not cwd:
            scratch = f'{store}/scratch/{read(store, "verifying.scratch")}'
            cwd = os.path.realpath(scratch) if os.path.isdir(scratch) else ''
        if ident and cwd:
            session = f'{os.environ.get("HOME", "")}/.claude/projects/{re.sub(r"[^A-Za-z0-9-]", "-", cwd)}/{ident}.jsonl'
    if session:
        update(store, lambda doc: vs.set_member(doc, 'verifying', 'session', session))


def tail_lines(text, count):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines[-count:])


VERIFY_SPEC = Spec(valued=('--timeout', '--kind', '--retry-entry', '--every', '--base', '--parent', '--head', '--covers'),
                   flags=('--until-verdict', '--landing', '--wait', '--cancel'), positional=r'[0-9]{3}')


def parse_options(argv):
    """verify's flags and unit numbers as one namespace; a flag with no value, a malformed number and an unknown flag end the command."""
    o = SimpleNamespace(every_m=9, every_set=False, until_set=False, timeout_m=None, kind_override='', retry_entry='', range_base='', parent_ref='',
                        land_head='', wait=False, cancel=False, landing=False, units=[], covered=[])
    parsed = read_options(argv, VERIFY_SPEC)
    o.units = list(parsed.positionals)
    for flag, value in parsed.occurrences:
        if flag == '--timeout':
            o.timeout_m = whole_number(value, '--timeout', 'minutes')
        elif flag == '--kind':
            o.kind_override = value
        elif flag == '--retry-entry':
            o.retry_entry = value
        elif flag == '--every':
            o.every_m, o.every_set = whole_number(value, '--every', 'minutes'), True
        elif flag == '--base':
            o.range_base = value
        elif flag == '--parent':
            o.parent_ref = value
        elif flag == '--head':
            o.land_head = value
        elif flag == '--covers':
            fields = value.split(',')
            if fields and fields[-1] == '':
                fields.pop()
            for u in fields:
                if not re.fullmatch(r'[0-9]{3}', u):
                    raise Die(f"--covers takes unit numbers (NNN), one per flag or a comma list, not '{u}'")
                o.covered.append(u)
        elif flag == '--until-verdict':
            o.every_m, o.until_set = 525600, True
        else:
            setattr(o, flag[2:], True)
    if o.every_set and o.until_set:
        raise Die('--every and --until-verdict are alternatives')
    return o


def classify_landing(store, root, kit, o):
    """The landing's range and class: the head, the parent the stack starts from (--parent, or the last landing's), the kitchen as it is now, and
    the diff's classification."""
    head = commit_of(root, o.land_head or 'HEAD')
    if head is None:
        raise BadRevision(f'--head {o.land_head} is not a commit')
    override = label = ''
    if o.parent_ref == 'none':
        label = 'none'
    if o.parent_ref and o.parent_ref != 'none':
        override = commit_of(root, o.parent_ref)
        if override is None:
            raise BadRevision(f'--parent {o.parent_ref} is not a commit')
        label = o.parent_ref
        if not (override != head and is_ancestor(root, override, head)):
            raise BadRevision(f'--parent {o.parent_ref} ({override[:9]}) must be an ancestor of {head[:9]} and not the same commit; name where the stack starts')
    if o.range_base:
        base = commit_of(root, o.range_base)
        if base is None:
            raise BadRevision(f'--base {o.range_base} is not a commit')
    else:
        base = landing_base(store, root, head, override, label, o.range_base)
    # A rebase since the last landing verdict may have moved a dependency manifest.
    prior = latest_landing_verdict(store)
    if prior:
        sys.stdout.write(manifest_warning(store, commit_of(root, Verdict.parse(prior).refs()[0]) or '', base, head))
    # The stack lands under the kitchen as it is now.
    snap = kitchen_snapshot(store, root)
    cfg = ['--config', snap] if snap else []
    classified = kit.call([*cfg, '--at', head, '--json', 'classify', '--base', base, '--head', head])
    return SimpleNamespace(base=base, head=head, override=override, label=label, cfg=cfg, classified=classified, data=json.loads(classified),
                           risk='landing', mode='unit', cls='escalated')


def classify_units(ctx, store, kit, o, allunits):
    """The named units' range and class. A unit dispatched as escalated is verified as escalated, whatever its diff touches."""
    base, head = unit_range(ctx, store, allunits, o.covered, o.range_base)
    heads_in(store, head, allunits)
    esc = []
    for u in allunits:
        if header_field(brief_or_nothing(ctx, store, u), 'risk') == 'escalated':
            esc += ['--escalate', f'unit {u} was dispatched as escalated']
    cfg = brief_kitchen(brief_or_nothing(ctx, store, allunits[-1]))
    classified = kit.call([*cfg, '--at', head, '--json', 'classify', '--base', base, '--head', head, *esc])
    data = json.loads(classified)
    verify_class = data.get('verify') or {}
    return SimpleNamespace(base=base, head=head, override='', label='', cfg=cfg, classified=classified, data=data, risk=data.get('risk') or '',
                           mode=verify_class.get('mode') or '', cls=verify_class.get('kind') or '')


def verdict_paths(store, landing, allunits):
    """(last unit, its brief, its slug, verdict path, packet path) for the verification this call starts."""
    if landing:
        k = next_index(f'{store}/verdicts', 'landing', 'v')
        return 'landing', '-', '', f'{store}/verdicts/landing-v{k}.md', f'{store}/verdicts/landing-v{k}-packet.md'
    last = allunits[-1]
    brief = unit_brief(store, last)
    slug = os.path.basename(brief)[:-3][4:] if brief.endswith('.md') else os.path.basename(brief)[4:]
    k = next_index(f'{store}/verdicts', f'{last}-{slug}', 'v')
    return last, brief, slug, f'{store}/verdicts/{last}-{slug}-v{k}.md', f'{store}/verdicts/{last}-{slug}-v{k}-packet.md'


def deadline_minutes(kit, data, o):
    """The deadline in minutes: --timeout, else 3 x the slowest recent passing behavioral gates, plus the last recorded setup run when the repo
    declares one, scaled by the machine's load at the start (capped at 3x), and never under 45 minutes. The parts are printed. A landing also gets
    3 x the touched profiles' measured landing commands, and a fixed ten minutes for each that has no measured run."""
    if o.timeout_m is not None:
        out(f'deadline {o.timeout_m}m (--timeout)\n')
        return o.timeout_m
    dlargs = ['timing', '--deadline', '--stage', 'behavioral']
    if data['scratch_setup']:
        dlargs.append('--with-setup')
    dlargs += data['behavioral']
    minutes = int(json.loads(kit.call(['--json', *dlargs])).get('minutes'))
    kit.call(dlargs, show=True)
    profiles = data.get('landing') or [] if o.landing else []
    if profiles:
        timed = json.loads(kit.call(['--json', 'timing', '--stage', 'landing', *profiles]))
        seconds = timed.get('seconds') or 0
        unmeasured = len(timed['unmeasured'])
        added = int((3 * seconds + 59) / 60) + 10 * unmeasured
        minutes += added
        out(f'deadline +{added}m for the landing gates of {" ".join(profiles)}: 3 x {seconds / 60:.0f}m measured'
            f'{"" if unmeasured == 0 else f", {unmeasured} unmeasured at 10m each"}; now {minutes}m\n')
    return minutes


def choose_verifier(kit, root, cls, o):
    """(entry, kind, arguments) of the verifier: the retried entry, else --kind's pick from the class's entry and its fallback, else the entry."""
    entry = verifier_entry(kit, cls)
    if o.retry_entry:
        retried = parse_entry(o.retry_entry)
        kind = retried.get('kind') if isinstance(retried, dict) else ''
        picked = {'kind': retried.get('kind'), 'args': retried.get('args')} if isinstance(retried, dict) else None
    elif o.kind_override:
        kind = o.kind_override
        picked = pick_entry(entry, kind)
    else:
        kind = entry.get('kind') if isinstance(entry, dict) else ''
        picked = entry
    args_list = verifier_args(kind, picked, root)
    if not kind or not isinstance(kind, str):
        raise UnknownVerifierKind(f'cannot tell which agent kind verifies ({cls})')
    return entry, kind, args_list


def record_landing_parent(store, o, c):
    """The last point verify can die before it starts the verifier: only now is the stack's parent recorded or cleared."""
    if o.parent_ref == 'none':
        def clear(doc):
            section = peek(doc, 'landing')
            section.pop('parent', None)
            section.pop('parent_ref', None)

        update(store, clear)
    elif c.override:
        def record(doc):
            vs.set_member(doc, 'landing', 'parent', c.override)
            vs.set_member(doc, 'landing', 'parent_ref', c.label)

        update(store, record)


def launch_verifier(v):
    """The verifier's pane beside the sidekick (the master when it is gone) and its agent, started on the scratch. A start that fails closes the pane
    and the scratch and ends the command; a Prime verifier keeps both. (pane, whether the agent started)."""
    store = v.store
    v.name = field(store, 'verifier', 'name')
    anchor = field(store, 'sidekick', 'pane_id')
    if not (anchor and herdr('pane', 'get', anchor)[0] == 0):
        anchor = field(store, 'master', 'pane_id')
    if read(store, 'placement') == 'tab':
        field(store, 'master', 'pane_id')  # a master that is no object ends verify before any herdr call
    with contextlib.redirect_stdout(io.StringIO()):
        pane = new_pane(store, 'verifier', anchor, '', v.scratch, env=('PSTACK_KITCHEN_ROLE=verifier', f'PSTACK_KITCHEN_RUN={read(store, "slug")}'))
    sys.stdout.flush()
    started = agent_start(v.name, v.kind, pane, v.args, stderr=None).returncode == 0
    if not started and v.kind != 'prime-agent':
        why = f'verifier start failed in {pane}'
        screen = re.sub(r'[\n\t ]+', ' ', herdr('pane', 'read', pane, '--source', 'visible', '--lines', '40')[1])
        if TRUST_RE.search(screen):
            why = f'{v.kind} asks whether to trust {v.scratch}, and that decision is yours: open {v.kind} in the repo once and trust it, or verify with another kind'
            herdr('pane', 'send-keys', pane, 'esc')
        herdr('pane', 'close', pane)
        removed = vs.remove_scratch(v.ctx, store, v.scratch_id)
        if removed:
            raise ToolFailed(removed)
        vs.set_outcome(v.packet, 'start-failed')
        raise VerifierStartFailed(why)
    return pane, started


def record_start(v, pane, started):
    """The verifier and the open verification, in pair.json, once the agent has started; a Prime verifier must also have reported its session."""
    store = v.store
    started_at = int(time.time())
    sample = 0 if v.landing else v.data.get('sample')
    unit_label = 'landing' if v.landing else f'{v.last}-{v.slug}'
    v.units_label = ' '.join(v.units) if v.units else 'landing'

    def mutate(doc):
        # .verifier.pane_id and .verifier.kind; for a prime-agent also .verifier.start_args, .verifier.session, .verifier.session_stopped, .verifier.closed and .verifier.launch_cwd
        vs.set_member(doc, 'verifier', 'pane_id', pane)
        vs.set_member(doc, 'verifier', 'kind', v.kind)
        if v.kind == 'prime-agent':
            verifier = doc['verifier']
            verifier['start_args'], verifier['session'], verifier['session_stopped'], verifier['closed'], verifier['launch_cwd'] = list(v.args), None, False, False, v.scratch
        doc['verifying'] = {'verdict': v.verdict, 'packet': v.packet, 'brief': v.brief, 'scratch': v.scratch_id, 'units': v.units_label, 'risk': v.risk,
                            'sample': sample, 'unit': unit_label, 'started': started_at, 'deadline': started_at + v.timeout_m * 60, 'class': v.cls,
                            'entry': v.entry, 'retried': v.retried, 'argv': v.argv, 'delivered': False}

    update(store, mutate)
    vs.set_outcome(v.packet, 'running')
    if v.kind == 'prime-agent':
        if not started:
            vs.set_outcome(v.packet, 'start-failed')
            record_prime_session(store, 'verifier')
            raise PrimeFailed(f'Prime verifier start failed in {pane}; its pane and scratch stay for inspection (verify-wait can resume cleanup once its session is reported)')
        if not record_prime_session(store, 'verifier'):
            raise PrimeFailed('Prime verifier did not report its session; its pane and scratch stay for inspection')
    event(store, 'master', 'send-verify', v.brief, f'{v.kind}:{v.units_label}')
    record_verifier_session(store, v.name)
    out(f'verifier {v.name} ({v.identity}) in {pane} on {v.base[:9]}..{v.head[:9]}\n')


def deliver_packet(v):
    """A just-started agent can take the text before it takes the Enter (pi drawing its startup screen), so the prompt must be seen working; an idle
    agent gets one more Enter, which submits the typed text. A startup screen can also swallow the prompt whole: an agent never seen working, with
    nothing typed and no verdict, still idle, gets the packet once more."""
    sys.stdout.flush()
    took = agent_prompt(v.name, vs.verify_prompt(v.ctx, v.packet), wait=True, until='working', timeout_ms=30000).returncode == 0
    submitted = submit_typed(v.name, os.path.basename(v.packet))
    if not took and submitted == 0 and not os.path.isfile(v.verdict):
        if agent_status(v.name) in ('idle', 'done'):
            vs.redeliver_packet(v.ctx, v.store, 'the verifier sat idle without the packet')
    update(v.store, lambda doc: vs.set_member(doc, 'verifying', 'delivered', True))
    record_verifier_session(v.store, v.name)


def verify(ctx, args):
    """Start a verification of the named units or the landing and wait for its verdict; the status is the exit code."""
    require_herdr()
    if len(args) < 2:
        raise Die(USAGE)
    store, argv = args[0], list(args[1:])
    o = parse_options(argv)

    def retry(again):
        return verify(ctx, again)

    pair_file(store)
    if o.cancel:
        if o.wait or o.units or o.landing:
            raise Die('--cancel takes no units, --landing or --wait')
        return vs.verify_cancel(ctx, store)
    if o.wait:
        if not read(store, 'verifying.verdict'):
            raise Die(f'no verification is open; start one with kitchen.sh verify {store} <NNN>', 5)
        return Waiter(ctx, store, o.every_m, retry=retry).run()
    if not o.landing and not o.units:
        raise Die('name at least one unit NNN, or --landing')
    if o.landing and o.units:
        raise Die('--landing verifies the whole stack; name no units')
    close_unread(ctx, store)
    if read(store, 'verifying.verdict'):
        raise Die(f'a verification is open ({read(store, "verifying.units")}); kitchen.sh verify {store} --wait', 5)
    allunits = [*o.covered, *o.units]
    require_pair(ctx, store)
    root = repo_root(store)
    if o.land_head and not o.landing:
        raise Die('--head goes with --landing')
    if o.parent_ref and not o.landing:
        raise Die('--parent goes with --landing')
    kit = Kitchen(store, root)
    c = classify_landing(store, root, kit, o) if o.landing else classify_units(ctx, store, kit, o, allunits)
    data = c.data
    # A batch of independent units has a size cap; a fix verified with the unit it fixes does not, since splitting it would re-reject the first.
    if not o.covered and len(o.units) > 1 and is_number(data.get('diff_lines')) and is_number(data.get('max_batch_diff')) and data['diff_lines'] > data['max_batch_diff']:
        raise Die(f'batch of {json.dumps(data["diff_lines"])} diff lines is over review.max_batch_diff; verify each unit', 2)
    last, brief, slug, verdict, packet = verdict_paths(store, o.landing, allunits)
    if c.mode == 'gates':
        gates_verdict(ctx, store, verdict, last, slug, allunits, c.base, c.head)
        event(store, 'master', 'verify', brief, 'gates:clean')
        wake(ctx, store, brief, 'verify:clean')
        out(f'verdict: {verdict}\nstatus: clean (gates only; mode gates)\n')
        warn_unreviewed(store)
        vs.handoff_due(store, last)
        return 0
    timeout_m = deadline_minutes(kit, data, o)
    entry, kind, args_list = choose_verifier(kit, root, c.cls, o)
    scratch_id = '000-landing-verify' if o.landing else f'{last}-{slug}-verify'
    v = SimpleNamespace(ctx=ctx, store=store, argv=argv, landing=o.landing, units=allunits, last=last, slug=slug, brief=brief, base=c.base, head=c.head,
                        cls=c.cls, risk=c.risk, data=data, verdict=verdict, packet=packet, timeout_m=timeout_m, entry=entry, kind=kind, args=args_list,
                        identity=verifier_identity(kind, args_list), scratch_id=scratch_id, retried=bool(o.retry_entry),
                        scratch=scratch_path(ctx, store, scratch_id, c.head))
    if o.landing:
        record_landing_parent(store, o, c)
    write_packet(ctx, kit, f'{ctx.skill_root}/scripts', store, packet, verdict, v.scratch, c.base, c.head, v.identity, 'landing' if o.landing else c.cls, timeout_m,
                 c.risk, allunits, o.units, o.covered, o.landing, last, slug, c.cls, c.classified, c.cfg)
    pane, started = launch_verifier(v)
    record_start(v, pane, started)
    deliver_packet(v)
    return Waiter(ctx, store, o.every_m, retry=retry).run()


def pick_entry(entry, kind):
    """The first of the entry and its fallback that names the kind, as {kind, args}; None when neither does."""
    for candidate in (entry, entry.get('fallback') if isinstance(entry, dict) else None):
        if isinstance(candidate, dict) and candidate.get('kind') == kind:
            return {'kind': candidate.get('kind'), 'args': candidate.get('args')}
    return None


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def scratch_path(ctx, store, scratch_id, head):
    """The scratch worktree's path at head; the scratch command's refusal ends verify with its status."""
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            rc = scratch_command.run(ctx, [store, scratch_id, '--at', head])
    except (Die, PairError) as e:
        text, code = translate(e, ctx.prog)
        warn(text)
        raise ToolFailed(code) from None
    if rc:
        raise ToolFailed(rc)
    lines = sink.getvalue().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines[-1] if lines else ''


def gates_verdict(ctx, store, verdict, last, slug, allunits, base, head):
    """The verdict a gates-mode verification writes itself: every step passed its profiles' fast gates and the policy check."""
    text = (f'# Verdict {last}: {slug}\n\nstatus: clean\nunits: {" ".join(allunits)}\nrange: {base[:9]}..{head[:9]}\nverifier: gates\n\n'
            "## Findings\n\nnone\n\n## Evidence\n\nEvery step passed its profiles' fast gates and the policy check:\n\n```\n")
    for u in allunits:
        wanted = os.path.basename(brief_or_nothing(ctx, store, u))
        wanted = wanted[:-3] if wanted.endswith('.md') else wanted
        try:
            for line in read_text(f'{store}/gates.tsv').split('\n'):
                fields = line.split('\t')
                if len(fields) >= 2 and fields[1] == wanted:
                    text += line + '\n'
        except OSError:
            pass
    text += '```\n'
    write_text(verdict, text)


def close_unread(ctx, store):
    """A verdict written but never read closes the verification it belonged to; the new one starts after it."""
    open_verdict = read(store, 'verifying.verdict')
    if not (open_verdict and os.path.isfile(open_verdict) and header_field(open_verdict, 'status')):
        return
    closed_brief = read(store, 'verifying.brief')
    waiting_since, by_another, closed, closed_rc, closed_status = None, False, '', 0, ''
    while True:
        if read(store, 'verifying.verdict') != open_verdict:
            by_another = True
            break
        closed_rc, closed = captured(ctx, lambda: Waiter(ctx, store, 0, closing=True, retry=lambda again: verify(ctx, again)).run(), merge_stderr=True)
        closed_status = next((line[len('status: '):] for line in closed.split('\n') if line.startswith('status: ')), '')
        if closed_status:
            break
        if 'verification closed elsewhere' not in closed:
            break
        # Another call is closing this verdict: wait for it to finish, then go on as after a close of our own.
        if waiting_since is None:
            waiting_since = int(time.time())
            out(f'closing {open_verdict}: another call is closing it; waiting for it\n')
        limit = env_int('KITCHEN_VERIFY_CLOSE_WAIT_S', 120)
        if not int(time.time()) - waiting_since < limit:
            raise CloseHeld(f'another call has held the close of {open_verdict} for {limit}s; kitchen.sh verify {store} --wait')
        time.sleep(env_float('KITCHEN_VERIFY_POLL_S', 10))
    if by_another:
        closed_status = 'closed by another call'
        closed = ''
        closed_rc = 0 if header_field(open_verdict, 'status') == 'clean' else 2
    elif not closed_status:
        warn(f'closing the unread verdict {open_verdict} failed:\n{closed}\n')
        raise ToolFailed(closed_rc if closed_rc > 0 else 5)
    out(f'closed: {open_verdict} ({closed_status})\n{closed}\n')
    # A verdict that is not clean is the master's to act on before another verification starts.
    if closed_rc != 0:
        wake(ctx, store, closed_brief, f'verify:{closed_status.split(" ")[0]}')
        raise ToolFailed(closed_rc)


def section_lines(path, pattern, keep_heading):
    """The lines under the brief's `## Goal` and `## Acceptance` headings, headings included with keep_heading, else the Goal section without its heading."""
    found, show = [], False
    try:
        lines = read_text(path).split('\n')
    except OSError:
        return found
    if lines and lines[-1] == '':
        lines.pop()
    for line in lines:
        if re.match(pattern, line):
            show = True
            if keep_heading:
                found.append(line)
            continue
        if line.startswith('## '):
            show = False
        if show:
            found.append(line)
    return found


def write_packet(ctx, kit, here, store, packet, verdict, scratch, base, head, identity, vclass, timeout_m, risk, allunits, units, covered, landing, last,
                 slug, cls, classified, cfg):
    """The verifier's packet, written even when a step fails midway, so what was gathered stays."""
    root = kit.root
    parts = []
    rule = 'Write rule: you write only your verdict file and the scratch worktree you were given; work only in that worktree and write nothing else but the verdict.'
    try:
        if landing:
            parts.append(f'# Verify landing: the stack at {head[:9]}\n\nverdict: {verdict}\ntemplate: {ctx.skill_root}/references/verdict-template.md\nscratch: {scratch}\n'
                         f'range: {base}..{head}\nrisk: landing\nunits: landing\nverifier-agent: {identity}\nverifier-class: {vclass}\nverifier-outcome: prepared\n'
                         f'deadline: {timeout_m}m from now\n\n')
            parts.append(f'{rule}\n\n')
            parts.append('## Stack\n\nEach unit below was verified on its own. Prove that together, at this tip,\nthey still hold: run every Prove command, then drive the main flows the\n'
                         'briefs describe, and try the seams between units. Read a brief only for\nthe flow you are driving.\n\n')
            stacked = landing_covered_briefs(store, root, base, head)
            for b in stacked:
                parts.append(f'- {b}\n')
            parts.append('\n## Goals\n\n')
            for b in stacked:
                name = os.path.basename(b)[:-3]
                parts.append(f'### Unit {name[:3]} {name[4:]}\n\n')
                goal = section_lines(b, r'^## Goal', False)
                while goal and goal[0] == '':
                    goal.pop(0)
                parts.append('\n'.join(goal).rstrip('\n') + '\n\n')
        else:
            parts.append(f'# Verify {last}: {slug}\n\nverdict: {verdict}\ntemplate: {ctx.skill_root}/references/verdict-template.md\nscratch: {scratch}\nrange: {base}..{head}\n'
                         f'risk: {risk}\nunits: {" ".join(allunits)}\nverifier-agent: {identity}\nverifier-class: {vclass}\nverifier-outcome: prepared\n'
                         f'deadline: {timeout_m}m from now\n\n')
            parts.append(f'{rule}\n\n')
            if covered:
                parts.append(f"Unit {' '.join(units)} fixes unit {' '.join(covered)}: prove every unit's Acceptance at this one head.\n\n")
            for u in allunits:
                b = unit_brief(store, u)
                parts.append(f'## Unit {u}\n\nbrief: {b}\n\n')
                for line in section_lines(b, r'^## (Goal|Acceptance)', True):
                    parts.append(line + '\n')
                parts.append('\n')
                parts.append(brief_commands(kit, b, u))
            ubriefs = []
            for u in allunits:
                found = sorted(glob.glob(f'{glob.escape(store)}/briefs/{glob.escape(u)}-*.md'))
                ubriefs += found if found else [f'{store}/briefs/{u}-*.md']
            status, lessons = kit.elsewhere(['lesson-packet', *ubriefs])
            if status != 0:
                raise ToolFailed(status)
            lessons = lessons.rstrip('\n')
            if lessons:
                parts.append(f'{lessons}\n\n')
        if os.path.isfile(f'{store}/standing-orders.md'):
            orders = read_text(f'{store}/standing-orders.md').rstrip('\n')
            parts.append(f'## Standing orders\n\nThey bind you as they bind the sidekick ({store}/standing-orders.md):\n\n{orders}\n\n')
        diff = git.run(root, 'diff', '--stat', f'{base}...{head}').stdout
        parts.append(f'## Changed\n\n```\n{tail_lines(diff, 40)}\n```\n\n')
        parts.append('## Prove\n\nYou share this machine with the sidekick. Run every repo command with\n`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo\'s scripts give you\n'
                     'your own ports, emulators, and data, and stop everything you start before\nyou end. Run these in the scratch worktree, then drive what they cannot reach:\n\n'
                     '```bash\nexport PSTACK_KITCHEN_ROLE=verifier')
        parts.append(f' PSTACK_KITCHEN_RUN={read(store, "slug")}\n')
        config = cfg[1] if len(cfg) > 1 else ''
        tool = f'{here}/kitchen.py' + (f' --config {config}' if config else '')
        data = json.loads(classified)
        if (data.get('scratch_setup') or []):
            parts.append(f'python3 {tool} --repo {scratch} setup\n')
        forked = git.run(root, 'merge-base', base, head, quiet=True)
        merge_base = forked.stdout.rstrip('\n') if forked.returncode == 0 else base
        for profile in data.get('behavioral') or []:
            parts.append(f'python3 {tool} --repo {scratch} gate {profile} behavioral --role verifier --base {merge_base}\n')
        # A landing verification also runs the touched profiles' landing commands.
        if risk == 'landing':
            for profile in data.get('landing') or []:
                parts.append(f'python3 {tool} --repo {scratch} gate {profile} landing --role verifier --base {merge_base}\n')
        features = data.get('features') or []
        parts.append('```\n\nfeature map: ' + ('none listed' if len(features) == 0 else ', '.join(features)) + '\n')
        skills = sorted([p + '/' for p in glob.glob(f'{glob.escape(root)}/.agents/skills/verify-*') if os.path.isdir(p)])
        parts.append('verification skill: ' + ''.join(s + ' ' for s in skills) + '\n')
    finally:
        write_text(packet, ''.join(parts))
