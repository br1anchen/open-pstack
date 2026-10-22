"""verify: starts a verification (a fresh verifier on a scratch worktree, or the gates alone) and waits for it. Ported from kitchen.sh at b632713
(cmd_verify, verifier_entry, verifier_identity, verifier_args, record_verifier_session, brief_commands, fence_for, names_path, landing_covered_briefs,
next_index, kitchen_snapshot and brief_kitchen).

It is built on units.py (ranges, inclusion, verdicts), verify_state.py (the claim, the compare-and-set, the close, the cancel) and verify_wait.py (the
wait); nothing calls back into it. The bash ran in the main shell with errexit on: a plain command or assignment that fails ends the command with its
status, which is what an Exit or a Die does here, and what ran under `||` or in an `if` keeps going.

kitchen.py is reached through kpy.run, in process. The commands verify makes (roster, snapshot, classify, timing, brief-verify --list and
lesson-packet) read the repo's kitchen and git and print; none of them writes os.environ or reads INVOCATION: those belong to `gate` and to
`brief-verify` without --list (kitchen.py :745, :771, :1023, :1038), which verify never runs. test_verify_kpy_calls_leave_the_environment_alone pins
that."""

import glob
import io
import json
import os
import re
import subprocess
import sys
import time
import contextlib

from pstack_core.agents import has_mcp_arg, has_permission_arg, has_trust_arg, mcp_args, permission_args, trust_args
from pstack_core.collate import glob_sorted
from pstack_core.commands import scratch as scratch_command
from pstack_core.commands.report import in_herdr
from pstack_core.errors import Die, Exit, unbound_arg
from pstack_core.herdr import agent_kind, agent_status, herdr
from pstack_core.panes import TRUST_RE, new_pane
from pstack_core.permission import mode_of_this_shell
from pstack_core.providers import record_prime_session
from pstack_core.send import submit_typed
from pstack_core.store import field
from pstack_core.store import bash_int, event, expected_report, header_field, pair_file, peek, readlink_f, text_of, update
from pstack_core.variant import commit_of

from . import kpy
from .kpy import Kitchen
from . import verify_state as vs
from .classify import brief_kitchen, kitchen_snapshot
from .jqx import exit_status, pair_jq
from .landing import is_landing, latest_landing_verdict, manifest_warning
from .reviews import warn_unreviewed
from .units import StackIndex, Verdict, brief_or_nothing, git, git_root, heads_in, inclusion, is_ancestor, landing_base, unit_brief, unit_head, unit_range
from .verify_wait import Waiter

USAGE = ('usage: kitchen.sh verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND] | '
         'kitchen.sh verify <store> --landing [--base REV] [--parent REF|none] [--head REV] [...] | kitchen.sh verify <store> --wait '
         '[--every MIN | --until-verdict] | kitchen.sh verify <store> --cancel')


def out(text):
    sys.stdout.write(text)


def warn(text):
    sys.stdout.flush()
    sys.stderr.write(text)


def data_of(text):
    return text.encode('utf-8', 'surrogateescape')


def run(argv, stdin=None, stderr=None):
    """A command with its stdout captured: (status, text)."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(argv, input=None if stdin is None else data_of(stdin), stdout=subprocess.PIPE, stderr=stderr, check=False)
    return exit_status(r.returncode), text_of(r.stdout)


def jq(text, program, *args, compact=False, quiet=False):
    """`jq -r PROGRAM <<<TEXT` (-c for compact JSON): (status, stdout without its trailing newlines); jq's own errors on stderr unless quiet."""
    status, found = run(['jq', '-c' if compact else '-r', *args, program], stdin=text + '\n', stderr=subprocess.DEVNULL if quiet else None)
    return status, found.rstrip('\n')


def mapfile(text):
    """`mapfile -t`: the lines of the text, none for no text."""
    if text == '':
        return []
    lines = text.split('\n')
    return lines[:-1] if lines[-1] == '' else lines


def bash_q(word):
    """printf %q: backslashes before the characters the shell treats as special, $'...' for a word with control characters."""
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
    for path in glob_sorted(glob.glob(f'{glob.escape(directory)}/{glob.escape(prefix)}-{letter}[0-9]*')):
        if not os.path.exists(path):
            continue
        n = bash_int(re.sub(rf'^.*-{letter}([0-9]+).*$', r'\1', os.path.basename(path)))
        if n is not None and n > k:
            k = n
    return k + 1


def fence_for(text):
    """A code fence for text: three backticks, or one more than the longest run of them in the text."""
    longest = max((len(m) for m in re.findall(r'`+', text)), default=0)
    return '`' * ((longest if longest >= 3 else 2) + 1)


def names_path(text, path):
    """Whether command text names path as a whole path: after a leading ~/, $HOME/ or ${HOME}/ is spelled out, path appears not inside a longer path and
    is followed by /, space, a quote, ), ;, &, | or the end. The bash's sed and grep, run as they were run."""
    home = os.environ.get('HOME', '')
    expand = 's#(^|[[:space:]=:"\'(])(~|\\$HOME|\\$\\{HOME\\}|"\\$HOME"|"\\$\\{HOME\\}")/#\\1' + home + '/#g'
    _, text = run(['sed', '-E', expand], stdin=text)
    text = text.rstrip('\n')
    _, quoted = run(['sed', 's/[][\\.*^$+?(){}|]/\\\\&/g'], stdin=path)
    quoted = quoted.rstrip('\n')
    pattern = '(^|[^A-Za-z0-9_./-])' + quoted + '(/|[[:space:]"\'`);&|]|$)'
    status, _ = run(['grep', '-qE', pattern], stdin=text + '\n', stderr=subprocess.DEVNULL)
    return status == 0


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
    _, lines = jq(listed, '.commands[]?', compact=True, quiet=True)
    for item in mapfile(lines):
        if item == '':
            continue
        _, command = jq(item, '.')
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
    for brief in glob_sorted(glob.glob(f'{glob.escape(store)}/briefs/[0-9][0-9][0-9]-*.md')):
        if not os.path.exists(brief) or header_field(expected_report(store, brief), 'status') != 'done' or is_landing(brief):
            continue
        from .units import RecordedUnit
        if inclusion(RecordedUnit(store, brief), index).covered:
            covered.append(brief)
    return covered


def verifier_entry(kit, cls):
    """The verifier for a class: the host roster's entry, with the sidekick marker resolved from the run (so after a failover it is the agent now
    implementing) and the master sentinel resolved to the master's own kind. One compact JSON object: kind, args, fallback and where it came from.
    Returns (entry, status): the bash function ran in a command substitution, where only its last command's status survives, and a failing one ends the
    assignment that captured it."""
    store = kit.store
    _, roster = kit.elsewhere(['--json', 'roster'])
    _, entry = jq(roster, '.verifier[$c]', '--arg', 'c', cls, compact=True)
    _, kind = jq(entry, '.kind')
    if kind == 'sidekick':
        status, found = run(['jq', '-c', '{kind: .sidekick.kind, args: (.sidekick.start_args // []), source: "sidekick", fallback: (if .sidekick.fallback.kind then '
                            '{kind: .sidekick.fallback.kind, args: (.sidekick.fallback.args // [])} else null end)}', f'{store}/pair.json'])
        return found.rstrip('\n'), status
    if kind == 'master':
        master_kind = agent_kind(field(store, 'master', 'pane_id'))
        status, found = jq(entry, '.kind = $k | .args = [] | .source = "master"', '--arg', 'k', master_kind, compact=True)
        return found, status
    return entry, 0


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
        shown += ' ' + bash_q(a)
    return f'{kind}{shown}' if shown else f'{kind} (model unspecified)'


def verifier_args(kind, entry, directory):
    """A verifier starts with its entry's arguments, then its kind's permission default, trust flag and MCP opt-out when they are missing; entry is the
    entry as JSON (or an empty string for a kind the class does not name), directory the repo the scratch is checked out from, whose MCP servers the
    opt-out names. The arguments as the bash's mapfile read them from the lines it printed."""
    args = mapfile(run(['jq', '-r', '.args[]?'], stdin=entry + '\n')[1]) if entry else []
    lines = []
    if not has_permission_arg(args):
        permission = 'bypassPermissions' if kind == 'devin' else mode_of_this_shell(False)
        lines += permission_args(kind, permission)
    if not has_trust_arg(args):
        lines += trust_args(kind)
    if not has_mcp_arg(args):
        lines += mcp_args(kind, directory)[0]
    printed = [*lines, *args]
    return mapfile('\n'.join(printed) + '\n') if printed else []


def record_verifier_session(store, name):
    """The agent's own session log, so a verifier that died on its provider or never got its packet can still say why after its pane is gone. Herdr
    gives pi's as a path and Claude's as an id, whose transcript Claude keeps under ~/.claude/projects/ in a directory named for the working directory."""
    code, info = herdr('agent', 'get', name)
    if code != 0:
        return
    session = jq(info, '.result.agent.agent_session | select(.kind == "path") | .value', quiet=True)[1]
    if not session:
        ident = jq(info, '.result.agent | select(.agent == "claude") | .agent_session | select(.kind == "id") | .value', quiet=True)[1]
        cwd = jq(info, '.result.agent.cwd // empty', quiet=True)[1]
        if not cwd:
            try:
                cwd = readlink_f(f'{store}/scratch/{pair_jq(store, ".verifying.scratch // empty")}')
            except Exit:
                cwd = ''
        if ident and cwd:
            session = f'{os.environ.get("HOME", "")}/.claude/projects/{re.sub(r"[^A-Za-z0-9-]", "-", cwd)}/{ident}.jsonl'
    if session:
        update(store, lambda doc: vs.set_member(doc, 'verifying', 'session', session))


def in_subshell(call, merge=True):
    """`$(call 2>&1)`, or with merge off `$(call)`: call runs in a child process, its stdout (and stderr, merged) on one pipe: (status, text without its
    trailing newlines). A process that execs away inside it (the retry) takes the child's place, as it takes a subshell's."""
    sys.stdout.flush()
    sys.stderr.flush()
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            os.close(reader)
            os.dup2(writer, 1)
            if merge:
                os.dup2(writer, 2)
            try:
                call()
                code = 0
            except Exit as e:
                sys.stderr.write(e.stderr)
                code = e.code
            except Die as e:
                sys.stderr.write(f'pair.sh: {e.message}\n')
                code = e.code
            except (OSError, ValueError) as e:
                sys.stderr.write(f'pair.sh: {e}\n')
                code = 1
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(code)
    os.close(writer)
    chunks = []
    while True:
        chunk = os.read(reader, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(reader)
    _, status = os.waitpid(pid, 0)
    return exit_status(os.waitstatus_to_exitcode(status)), text_of(b''.join(chunks)).rstrip('\n')


def tail_lines(text, count):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines[-count:])


def verify(ctx, args):
    """The bash's cmd_verify. It always ended in an exit, so this raises Exit for each end, and returns nothing."""
    in_herdr()
    if len(args) < 2:
        raise Die(USAGE)
    store, argv = args[0], list(args[1:])
    every_set = until_set = False
    parent_ref = land_head = timeout_m = kind_override = retry_entry = range_base = ''
    every_m = '9'
    wait = cancel = landing = False
    units, covered = [], []
    rest = list(argv)
    while rest:
        flag = rest[0]
        takes = ('--timeout', '--kind', '--retry-entry', '--every', '--base', '--parent', '--head')
        if flag in takes:
            if len(rest) < 2:
                raise unbound_arg()
            value = rest[1]
            del rest[:2]
            if flag == '--timeout':
                timeout_m = value
            elif flag == '--kind':
                kind_override = value
            elif flag == '--retry-entry':
                retry_entry = value
            elif flag == '--every':
                every_m, every_set = value, True
            elif flag == '--base':
                range_base = value
            elif flag == '--parent':
                parent_ref = value
            else:
                land_head = value
        elif flag == '--until-verdict':
            every_m, until_set = '525600', True
            del rest[:1]
        elif flag == '--covers':
            if len(rest) < 2:
                raise Die('--covers takes a unit NNN or a comma list')
            fields = rest[1].split(',')
            if fields and fields[-1] == '':
                fields.pop()
            for u in fields:
                if not re.fullmatch(r'[0-9]{3}', u):
                    raise Die(f"--covers takes unit numbers (NNN), one per flag or a comma list, not '{u}'")
                covered.append(u)
            del rest[:2]
        elif flag == '--landing':
            landing = True
            del rest[:1]
        elif flag == '--wait':
            wait = True
            del rest[:1]
        elif flag == '--cancel':
            cancel = True
            del rest[:1]
        elif re.fullmatch(r'[0-9][0-9][0-9]', flag):
            units.append(flag)
            del rest[:1]
        else:
            raise Die(f'unknown option {flag}')
    if every_set and until_set:
        raise Die('--every and --until-verdict are alternatives')
    pair_file(store)
    if cancel:
        if wait or units or landing:
            raise Die('--cancel takes no units, --landing or --wait')
        vs.verify_cancel(ctx, store)
        raise Exit(0)
    if wait:
        if not pair_jq(store, '.verifying.verdict // empty'):
            raise Die(f'no verification is open; start one with kitchen.sh verify {store} <NNN>', 5)
        Waiter(ctx, store, every_m, retry=lambda again: verify(ctx, again)).run()
    if not landing and not units:
        raise Die('name at least one unit NNN, or --landing')
    if landing and units:
        raise Die('--landing verifies the whole stack; name no units')
    close_unread(ctx, store)
    if pair_jq(store, '.verifying.verdict // empty'):
        raise Die(f'a verification is open ({pair_jq(store, ".verifying.units")}); kitchen.sh verify {store} --wait', 5)
    allunits = [*covered, *units]
    root = git_root(store, check=True)
    if land_head and not landing:
        raise Die('--head goes with --landing')
    if parent_ref and not landing:
        raise Die('--parent goes with --landing')
    kit = Kitchen(store, root)
    here = f'{ctx.skill_root}/scripts'
    override = label = ''
    cfg = []
    if landing:
        head = commit_of(root, land_head or 'HEAD')
        if head is None:
            raise Die(f'--head {land_head} is not a commit')
        if parent_ref == 'none':
            label = 'none'
        if parent_ref and parent_ref != 'none':
            override = commit_of(root, parent_ref)
            if override is None:
                raise Die(f'--parent {parent_ref} is not a commit')
            label = parent_ref
            if not (override != head and is_ancestor(root, override, head)):
                raise Die(f'--parent {parent_ref} ({override[:9]}) must be an ancestor of {head[:9]} and not the same commit; name where the stack starts')
        if range_base:
            base = commit_of(root, range_base)
            if base is None:
                raise Die(f'--base {range_base} is not a commit')
        else:
            base = landing_base(store, root, head, override, label, range_base)
        # A rebase since the last landing verdict may have moved a dependency manifest.
        prior = latest_landing_verdict(store)
        if prior:
            prior_ref = Verdict.parse(prior).refs()[0]
            manifest_warning(store, commit_of(root, prior_ref) or '', base, head)
        # The stack lands under the kitchen as it is now.
        snap = kitchen_snapshot(store, root)
        cfg = ['--config', snap] if snap else []
        classified = kit.call([*cfg, '--at', head, '--json', 'classify', '--base', base, '--head', head])
        risk, mode, cls = 'landing', 'unit', 'escalated'
    else:
        base, head = unit_range(store, allunits, covered, range_base)
        heads_in(store, head, allunits)
        # A unit dispatched as escalated is verified as escalated, whatever its diff touches.
        esc = []
        for u in allunits:
            if header_field(brief_or_nothing(store, u), 'risk') == 'escalated':
                esc += ['--escalate', f'unit {u} was dispatched as escalated']
        cfg = brief_kitchen(brief_or_nothing(store, allunits[-1]))
        classified = kit.call([*cfg, '--at', head, '--json', 'classify', '--base', base, '--head', head, *esc])
        data = json.loads(classified)
        risk, mode, cls = (jq_text(data.get('risk')), jq_text((data.get('verify') or {}).get('mode')), jq_text((data.get('verify') or {}).get('kind')))
    # A batch of independent units has a size cap; a fix verified with the unit it fixes does not, since splitting it would re-reject the first.
    if not covered and len(units) > 1 and jq(classified, '.diff_lines > .max_batch_diff')[1] == 'true':
        raise Die(f'batch of {jq(classified, ".diff_lines", compact=True)[1]} diff lines is over review.max_batch_diff; verify each unit', 2)
    if landing:
        last, brief, slug = 'landing', '-', ''
        k = next_index(f'{store}/verdicts', 'landing', 'v')
        verdict, packet = f'{store}/verdicts/landing-v{k}.md', f'{store}/verdicts/landing-v{k}-packet.md'
    else:
        last = allunits[-1]
        brief = unit_brief(store, last)
        slug = os.path.basename(brief)[:-3][4:] if brief.endswith('.md') else os.path.basename(brief)[4:]
        k = next_index(f'{store}/verdicts', f'{last}-{slug}', 'v')
        verdict, packet = f'{store}/verdicts/{last}-{slug}-v{k}.md', f'{store}/verdicts/{last}-{slug}-v{k}-packet.md'
    if mode == 'gates':
        gates_verdict(store, verdict, last, slug, allunits, base, head)
        event(store, 'master', 'verify', brief, 'gates:clean')
        event(store, 'master', 'wake', brief, 'verify:clean')
        out(f'verdict: {verdict}\nstatus: clean (gates only; mode gates)\n')
        warn_unreviewed(store)
        vs.handoff_due(store, last)
        raise Exit(0)
    # The default deadline: 3 x the slowest recent passing behavioral gates, plus the last recorded setup run when the repo declares one, scaled by the
    # machine's load at the start (capped at 3x), and never under 45 minutes. The parts are printed; --timeout wins.
    if not timeout_m:
        dlargs = ['timing', '--deadline', '--stage', 'behavioral']
        if jq(classified, '.scratch_setup | length')[1] != '0':
            dlargs.append('--with-setup')
        dlargs += mapfile(jq(classified, '.behavioral[]')[1] + '\n') if jq(classified, '.behavioral[]')[1] else []
        minutes = jq(kit.call(['--json', *dlargs]), '.minutes')[1]
        timeout_m = minutes
        kit.call(dlargs, show=True)
        # A landing verification also runs the touched profiles' landing commands: 3 x their measured time, and a fixed ten minutes for each that has
        # no measured run.
        if landing:
            profiles = mapfile(jq(classified, '.landing[]?')[1] + '\n') if jq(classified, '.landing[]?')[1] else []
            if profiles:
                timing = kit.call(['--json', 'timing', '--stage', 'landing', *profiles])
                seconds = jq(timing, '.seconds')[1]
                unmeasured = jq(timing, '.unmeasured | length')[1]
                added = awk_landing_minutes(seconds, unmeasured)
                timeout_m = str(vs.shell_arithmetic(timeout_m) + added)
                measured = awk_round_minutes(seconds)
                out(f'deadline +{added}m for the landing gates of {" ".join(profiles)}: 3 x {measured}m measured'
                    f'{"" if bash_int(unmeasured) == 0 else f", {unmeasured} unmeasured at 10m each"}; now {timeout_m}m\n')
    else:
        out(f'deadline {timeout_m}m (--timeout)\n')
    entry, entry_status = verifier_entry(kit, cls)
    if entry_status != 0:
        raise Exit(entry_status)
    if retry_entry:
        kind = jq(retry_entry, '.kind')[1]
        picked = jq(retry_entry, '{kind, args}', compact=True)[1]
        args_list = verifier_args(kind, picked, root)
    elif kind_override:
        kind = kind_override
        picked = pick_entry(entry, kind)
        args_list = verifier_args(kind, picked, root)
    else:
        kind = jq(entry, '.kind')[1]
        args_list = verifier_args(kind, entry, root)
    if not kind or kind == 'null':
        raise Die(f'cannot tell which agent kind verifies ({cls})')
    identity = verifier_identity(kind, args_list)
    vclass = 'landing' if landing else cls
    scratch_id = '000-landing-verify' if landing else f'{last}-{slug}-verify'
    scratch = scratch_path(ctx, store, scratch_id, head)
    # The last point verify can die before it starts the verifier: only now is the stack's parent recorded or cleared.
    if landing:
        if parent_ref == 'none':
            def clear(doc):
                section = peek(doc, 'landing')
                section.pop('parent', None)
                section.pop('parent_ref', None)

            update(store, clear)
        elif override:
            def record(doc):
                vs.set_member(doc, 'landing', 'parent', override)
                vs.set_member(doc, 'landing', 'parent_ref', label)

            update(store, record)
    write_packet(ctx, kit, here, store, packet, verdict, scratch, base, head, identity, vclass, timeout_m, risk, allunits, units, covered, landing, last, slug,
                 cls, classified, cfg)
    name = field(store, 'verifier', 'name')
    anchor = field(store, 'sidekick', 'pane_id', empty=True)
    if not (anchor and herdr('pane', 'get', anchor)[0] == 0):
        anchor = field(store, 'master', 'pane_id')
    if pair_jq(store, '.placement // empty') == 'tab':
        field(store, 'master', 'pane_id')  # new_pane's `master="$(field ...)"`: a master that is no object ends verify before any herdr call
    with contextlib.redirect_stdout(io.StringIO()):
        pane = new_pane(store, 'verifier', anchor, '', scratch, env=('PSTACK_KITCHEN_ROLE=verifier', f'PSTACK_KITCHEN_RUN={pair_jq(store, ".slug")}'))
    sys.stdout.flush()
    started = subprocess.run(['herdr', 'agent', 'start', name, '--kind', kind, '--pane', pane, '--timeout', '60000', '--', *args_list],
                             stdout=subprocess.DEVNULL, check=False).returncode == 0
    if not started and kind != 'prime-agent':
        why = f'verifier start failed in {pane}'
        screen = re.sub(r'[\n\t ]+', ' ', herdr('pane', 'read', pane, '--source', 'visible', '--lines', '40')[1])
        if TRUST_RE.search(screen):
            why = f'{kind} asks whether to trust {scratch}, and that decision is yours: open {kind} in the repo once and trust it, or verify with another kind'
            herdr('pane', 'send-keys', pane, 'esc')
        herdr('pane', 'close', pane)
        removed = vs.remove_scratch(ctx, store, scratch_id)
        if removed:
            raise Exit(removed)
        vs.set_outcome(packet, 'start-failed')
        raise Die(why, 5)
    started_at = int(time.time())
    sample = 0 if landing else json.loads(jq(classified, '.sample', compact=True)[1])
    unit_label = 'landing' if landing else f'{last}-{slug}'
    units_label = ' '.join(allunits) if allunits else 'landing'
    deadline = started_at + vs.shell_arithmetic(timeout_m) * 60
    native = list(args_list)
    try:
        entry_value = json.loads(entry)
    except ValueError as e:
        raise Die(f'verifier entry is not JSON: {e}', 2)

    def record_start(doc):
        # .verifier.pane_id and .verifier.kind; for a prime-agent also .verifier.start_args, .verifier.session, .verifier.session_stopped, .verifier.closed and .verifier.launch_cwd
        vs.set_member(doc, 'verifier', 'pane_id', pane)
        vs.set_member(doc, 'verifier', 'kind', kind)
        if kind == 'prime-agent':
            verifier = doc['verifier']
            verifier['start_args'], verifier['session'], verifier['session_stopped'], verifier['closed'], verifier['launch_cwd'] = native, None, False, False, scratch
        doc['verifying'] = {'verdict': verdict, 'packet': packet, 'brief': brief, 'scratch': scratch_id, 'units': units_label, 'risk': risk, 'sample': sample,
                            'unit': unit_label, 'started': started_at, 'deadline': deadline, 'class': cls, 'entry': entry_value, 'retried': bool(retry_entry),
                            'argv': argv, 'delivered': False}

    update(store, record_start)
    vs.set_outcome(packet, 'running')
    if kind == 'prime-agent':
        if not started:
            vs.set_outcome(packet, 'start-failed')
            record_prime_session(store, 'verifier')
            raise Die(f'Prime verifier start failed in {pane}; its pane and scratch stay for inspection (verify-wait can resume cleanup once its session is reported)', 5)
        if not record_prime_session(store, 'verifier'):
            raise Die('Prime verifier did not report its session; its pane and scratch stay for inspection', 5)
    event(store, 'master', 'send-verify', brief, f'{kind}:{units_label}')
    record_verifier_session(store, name)
    out(f'verifier {name} ({identity}) in {pane} on {base[:9]}..{head[:9]}\n')
    # A just-started agent can take the text before it takes the Enter (pi drawing its startup screen), so the prompt must be seen working; an idle agent
    # gets one more Enter, which submits the typed text. A startup screen can also swallow the prompt whole: an agent never seen working, with nothing
    # typed and no verdict, still idle, gets the packet once more.
    sys.stdout.flush()
    took = herdr('agent', 'prompt', name, vs.verify_prompt(ctx, packet), '--wait', '--until', 'working', '--timeout', '30000')[0] == 0
    submitted = submit_typed(name, os.path.basename(packet))
    if not took and submitted == 0 and not os.path.isfile(verdict):
        if agent_status(name) in ('idle', 'done'):
            vs.redeliver_packet(ctx, store, 'the verifier sat idle without the packet')
    update(store, lambda doc: vs.set_member(doc, 'verifying', 'delivered', True))
    record_verifier_session(store, name)
    Waiter(ctx, store, every_m, retry=lambda again: verify(ctx, again)).run()


def jq_text(value):
    """jq -r of a JSON value read as Python."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return value if isinstance(value, str) else json.dumps(value)


def pick_entry(entry, kind):
    """`jq -c --arg k KIND '[., .fallback][] | select(. != null and .kind == $k) | {kind, args}' <<<"$entry" | head -1`."""
    sys.stdout.flush()
    sys.stderr.flush()
    status, found = jq(entry, '[., .fallback][] | select(. != null and .kind == $k) | {kind, args}', '--arg', 'k', kind, compact=True)
    return found.split('\n')[0]


def awk_round_minutes(seconds):
    """awk's `printf "%.0f", s / 60`."""
    value = float(re.match(r'[-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?', seconds).group(0)) if re.match(r'[-+]?[0-9.]', seconds) else 0.0
    return '%.0f' % (value / 60)


def awk_landing_minutes(seconds, unmeasured):
    """awk's `m = int((3 * s + 59) / 60); print m + 10 * u`."""
    def number(text):
        m = re.match(r'[ \t]*([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)', text)
        return float(m.group(1)) if m else 0.0

    m = int((3 * number(seconds) + 59) / 60)
    return int(m + 10 * number(unmeasured))


def scratch_path(ctx, store, scratch_id, head):
    """`$(cmd_scratch "$store" "$scratch_id" --at "$head" | tail -1)`: the scratch's path; the command's refusal ends verify with its status."""
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            rc = scratch_command.run(ctx, [store, scratch_id, '--at', head])
    except Die as e:
        warn(f'pair.sh: {e.message}\n')
        raise Exit(e.code)
    if rc:
        raise Exit(rc)
    lines = sink.getvalue().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines[-1] if lines else ''


def gates_verdict(store, verdict, last, slug, allunits, base, head):
    """The verdict a gates-mode verification writes itself: every step passed its profiles' fast gates and the policy check."""
    text = (f'# Verdict {last}: {slug}\n\nstatus: clean\nunits: {" ".join(allunits)}\nrange: {base[:9]}..{head[:9]}\nverifier: gates\n\n'
            "## Findings\n\nnone\n\n## Evidence\n\nEvery step passed its profiles' fast gates and the policy check:\n\n```\n")
    for u in allunits:
        wanted = os.path.basename(brief_or_nothing(store, u))
        wanted = wanted[:-3] if wanted.endswith('.md') else wanted
        try:
            with open(f'{store}/gates.tsv', 'rb') as f:
                for line in text_of(f.read()).split('\n'):
                    fields = line.split('\t')
                    if len(fields) >= 2 and fields[1] == wanted:
                        text += line + '\n'
        except OSError:
            pass
    text += '```\n'
    with open(verdict, 'wb') as f:
        f.write(data_of(text))


def close_unread(ctx, store):
    """A verdict written but never read closes the verification it belonged to; the new one starts after it."""
    open_verdict = pair_jq(store, '.verifying.verdict // empty', check=True)
    if not (open_verdict and os.path.isfile(open_verdict) and header_field(open_verdict, 'status')):
        return
    closed_brief = pair_jq(store, '.verifying.brief', check=True)
    waiting_since, by_another, closed, closed_rc, closed_status = None, False, '', 0, ''
    while True:
        if pair_jq(store, '.verifying.verdict // empty') != open_verdict:
            by_another = True
            break
        closed_rc, closed = in_subshell(lambda: Waiter(ctx, store, '0', closing=True, retry=lambda again: verify(ctx, again)).run())
        closed_status = next((line[len('status: '):] for line in closed.split('\n') if line.startswith('status: ')), '')
        if closed_status:
            break
        if 'verification closed elsewhere' not in closed:
            break
        # Another call is closing this verdict: wait for it to finish, then go on as after a close of our own.
        if waiting_since is None:
            waiting_since = int(time.time())
            out(f'closing {open_verdict}: another call is closing it; waiting for it\n')
        limit = bash_int(os.environ.get('KITCHEN_VERIFY_CLOSE_WAIT_S') or '120')
        if limit is None or not int(time.time()) - waiting_since < limit:
            raise Die(f'another call has held the close of {open_verdict} for {os.environ.get("KITCHEN_VERIFY_CLOSE_WAIT_S") or "120"}s; '
                      f'kitchen.sh verify {store} --wait', 5)
        from pstack_core.wait import bash_sleep
        bash_sleep(os.environ.get('KITCHEN_VERIFY_POLL_S') or '10')
    if by_another:
        closed_status = 'closed by another call'
        closed = ''
        closed_rc = 0 if header_field(open_verdict, 'status') == 'clean' else 2
    elif not closed_status:
        warn(f'closing the unread verdict {open_verdict} failed:\n{closed}\n')
        raise Exit(closed_rc if closed_rc > 0 else 5)
    out(f'closed: {open_verdict} ({closed_status})\n{closed}\n')
    # A verdict that is not clean is the master's to act on before another verification starts.
    if closed_rc != 0:
        event(store, 'master', 'wake', closed_brief, f'verify:{closed_status.split(" ")[0]}')
        raise Exit(closed_rc)


def section_lines(path, pattern, keep_heading):
    """`awk '/^## (Goal|Acceptance)/{p=1; print; next} /^## /{p=0} p' file` (keep_heading) or the Goal variant that skips the heading line."""
    found, show = [], False
    try:
        with open(path, 'rb') as f:
            lines = text_of(f.read()).split('\n')
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
    """The verifier's packet, written as the bash's brace group wrote it: opened first, so a command that fails midway leaves what was printed."""
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
                while goal and goal[0] == '':  # sed '/./,$!d'
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
                found = glob_sorted(glob.glob(f'{glob.escape(store)}/briefs/{glob.escape(u)}-*.md'))
                ubriefs += found if found else [f'{store}/briefs/{u}-*.md']
            status, lessons = kit.elsewhere(['lesson-packet', *ubriefs])
            if status != 0:
                raise Exit(status)
            lessons = lessons.rstrip('\n')
            if lessons:
                parts.append(f'{lessons}\n\n')
        if os.path.isfile(f'{store}/standing-orders.md'):
            with open(f'{store}/standing-orders.md', 'rb') as f:
                orders = text_of(f.read()).rstrip('\n')
            parts.append(f'## Standing orders\n\nThey bind you as they bind the sidekick ({store}/standing-orders.md):\n\n{orders}\n\n')
        diff = text_of(subprocess.run(['git', '-C', root, 'diff', '--stat', f'{base}...{head}'], stdout=subprocess.PIPE, check=False).stdout)
        parts.append(f'## Changed\n\n```\n{tail_lines(diff, 40)}\n```\n\n')
        parts.append('## Prove\n\nYou share this machine with the sidekick. Run every repo command with\n`PSTACK_KITCHEN_ROLE=verifier` exported, so the repo\'s scripts give you\n'
                     'your own ports, emulators, and data, and stop everything you start before\nyou end. Run these in the scratch worktree, then drive what they cannot reach:\n\n'
                     '```bash\nexport PSTACK_KITCHEN_ROLE=verifier')
        parts.append(f' PSTACK_KITCHEN_RUN={pair_jq(store, ".slug")}\n')
        config = cfg[1] if len(cfg) > 1 else ''
        tool = f'{here}/kitchen.py' + (f' --config {config}' if config else '')
        data = json.loads(classified)
        if (data.get('scratch_setup') or []):
            parts.append(f'python3 {tool} --repo {scratch} setup\n')
        merge_base = git(root, 'merge-base', base, head, quiet=True)
        merge_base = merge_base[1].rstrip('\n') if merge_base[0] == 0 else base
        for profile in data.get('behavioral') or []:
            parts.append(f'python3 {tool} --repo {scratch} gate {jq_text(profile)} behavioral --role verifier --base {merge_base}\n')
        # A landing verification also runs the touched profiles' landing commands.
        if risk == 'landing':
            for profile in data.get('landing') or []:
                parts.append(f'python3 {tool} --repo {scratch} gate {jq_text(profile)} landing --role verifier --base {merge_base}\n')
        features = data.get('features') or []
        parts.append('```\n\nfeature map: ' + ('none listed' if len(features) == 0 else ', '.join(jq_text(f) for f in features)) + '\n')
        skills = glob_sorted([p + '/' for p in glob.glob(f'{glob.escape(root)}/.agents/skills/verify-*') if os.path.isdir(p)])
        parts.append('verification skill: ' + ''.join(s + ' ' for s in skills) + '\n')
    finally:
        with open(packet, 'wb') as f:
            f.write(data_of(''.join(parts)))


def verify_command(ctx, args):
    """pycore verify <store> ...: the bash's cmd_verify, which ended in an exit on every path."""
    verify(ctx, args)
    return 0
