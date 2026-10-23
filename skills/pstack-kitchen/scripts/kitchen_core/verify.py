"""verify: starts a verification (a fresh verifier on a scratch worktree, or the gates alone) and waits for it.

It is built on units.py (ranges, inclusion, verdicts), verify_options.py (its words), verify_launch.py (which agent verifies and its start), verify_packet.py
(what the verifier is told), verify_state.py (the claim, the compare-and-set, the close, the cancel) and verify_wait.py (the wait); nothing calls back into
it. A command that fails ends it with an Exit or a Die. This file holds the command, the verdict a gates-mode verification writes itself, and the close of a
verdict nobody read.

kitchen.py is reached through kpy: the queries verify makes (roster_data, snapshot, classify_change, timing, deadline, brief_verify_list and lesson_packet)
are its functions, read the repo's kitchen and git and return data; none of them writes os.environ or builds a child's command line (`child_args`,
`child_env`): those belong to `gate` and to `brief-verify` without --list, which verify never runs. test_verify_kpy_calls_leave_the_environment_alone pins
that."""

import glob
import json
import os
import re
import sys
import time
import contextlib
from types import SimpleNamespace

from pstack_core.commands.scratch import make_scratch
from pstack_core.store import wake, event, header_field, pair_file, peek, read, read_text, write_text, update
from pstack_core.numbers import env_float, env_int
from pstack_core.commands.report import require_pair
from pstack_core.errors import BadRevision, CloseHeld, Die, PairError, ToolFailed, translate
from pstack_core.panes import require_herdr
from pstack_core.variant import commit_of

from . import kpy
from . import verify_state as vs
from .classify import brief_kitchen, kitchen_snapshot
from .landing import latest_landing_verdict, manifest_warning
from .verify_launch import choose_verifier, deliver_packet, launch_verifier, record_start, verifier_identity
from .verify_options import parse_options
from .verify_packet import Packet, write_packet
from .verify_wait import warn_unreviewed, Waiter, WaiterOutcome
from .units import Verdict, brief_or_nothing, heads_in, is_ancestor, landing_base, repo_root, unit_brief, unit_range

USAGE = ('usage: kitchen.sh verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND] | '
         'kitchen.sh verify <store> --landing [--base REV] [--parent REF|none] [--head REV] [...] | kitchen.sh verify <store> --wait '
         '[--every MIN | --until-verdict] | kitchen.sh verify <store> --cancel')


def out(text):
    sys.stdout.write(text)


def warn(text):
    sys.stdout.flush()
    sys.stderr.write(text)


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


def classify_landing(store, root, o):
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
    data = kpy.call(kpy.kitchen_py.classify_change, kpy.invocation(store, root, snap or None, head), base=base, head=head)
    return SimpleNamespace(base=base, head=head, override=override, label=label, cfg=cfg, data=data,
                           risk='landing', mode='unit', cls='escalated')


def classify_units(ctx, store, root, o, allunits):
    """The named units' range and class. A unit dispatched as escalated is verified as escalated, whatever its diff touches."""
    base, head = unit_range(ctx, store, allunits, o.covered, o.range_base)
    heads_in(store, head, allunits)
    escalate = tuple(f'unit {u} was dispatched as escalated' for u in allunits if header_field(brief_or_nothing(ctx, store, u), 'risk') == 'escalated')
    cfg = brief_kitchen(brief_or_nothing(ctx, store, allunits[-1]))
    data = kpy.call(kpy.kitchen_py.classify_change, kpy.invocation(store, root, cfg[1] if cfg else None, head), base=base, head=head, escalate=escalate)
    verify_class = data.get('verify') or {}
    return SimpleNamespace(base=base, head=head, override='', label='', cfg=cfg, data=data, risk=data.get('risk') or '',
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


def deadline_minutes(store, root, data, o):
    """The deadline in minutes: --timeout, else 3 x the slowest recent passing behavioral gates, plus the last recorded setup run when the repo
    declares one, scaled by the machine's load at the start (capped at 3x), and never under 45 minutes. The parts are printed. A landing also gets
    3 x the touched profiles' measured landing commands, and a fixed ten minutes for each that has no measured run."""
    if o.timeout_m is not None:
        out(f'deadline {o.timeout_m}m (--timeout)\n')
        return o.timeout_m
    inv = kpy.invocation(store, root)
    deadline = kpy.call(kpy.kitchen_py.deadline, inv, data['behavioral'], bool(data['scratch_setup']))
    minutes = deadline['minutes']
    out(kpy.kitchen_py.deadline_text(deadline) + '\n')
    profiles = data.get('landing') or [] if o.landing else []
    if profiles:
        timed = kpy.call(kpy.kitchen_py.timing, inv, profiles, 'landing')
        seconds = timed.get('seconds') or 0
        unmeasured = len(timed['unmeasured'])
        added = int((3 * seconds + 59) / 60) + 10 * unmeasured
        minutes += added
        out(f'deadline +{added}m for the landing gates of {" ".join(profiles)}: 3 x {seconds / 60:.0f}m measured'
            f'{"" if unmeasured == 0 else f", {unmeasured} unmeasured at 10m each"}; now {minutes}m\n')
    return minutes


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


def start_verification(ctx, store, argv, o, root, allunits, c):
    """Everything after the units are classified: a gates-mode verdict written at once, or the verifier started with its packet, then the wait for its
    verdict. The status of the wait is the exit code."""
    data = c.data
    last, brief, slug, verdict, packet = verdict_paths(store, o.landing, allunits)
    if c.mode == 'gates':
        gates_verdict(ctx, store, verdict, last, slug, allunits, c.base, c.head)
        event(store, 'master', 'verify', brief, 'gates:clean')
        wake(ctx, store, brief, 'verify:clean')
        out(f'verdict: {verdict}\nstatus: clean (gates only; mode gates)\n')
        warn_unreviewed(store)
        vs.handoff_due(store, last)
        return 0
    timeout_m = deadline_minutes(store, root, data, o)
    entry, kind, args_list = choose_verifier(store, root, c.cls, o)
    scratch_id = '000-landing-verify' if o.landing else f'{last}-{slug}-verify'
    v = SimpleNamespace(ctx=ctx, store=store, argv=argv, landing=o.landing, units=allunits, last=last, slug=slug, brief=brief, base=c.base, head=c.head,
                        cls=c.cls, risk=c.risk, data=data, verdict=verdict, packet=packet, timeout_m=timeout_m, entry=entry, kind=kind, args=args_list,
                        identity=verifier_identity(kind, args_list), scratch_id=scratch_id, retried=bool(o.retry_entry),
                        scratch=scratch_path(ctx, store, scratch_id, c.head))
    if o.landing:
        record_landing_parent(store, o, c)
    write_packet(packet, Packet(
        template=f'{ctx.skill_root}/references/verdict-template.md', tool_dir=f'{ctx.skill_root}/scripts', root=root, store=store, verdict=verdict,
        scratch=v.scratch, base=c.base, head=c.head, identity=v.identity, vclass='landing' if o.landing else c.cls, timeout_m=timeout_m, risk=c.risk,
        units=tuple(allunits), own=tuple(o.units), covered=tuple(o.covered), landing=o.landing, last=last, slug=slug, data=c.data,
        config=c.cfg[1] if len(c.cfg) > 1 else ''))
    pane, started = launch_verifier(v)
    record_start(v, pane, started)
    deliver_packet(v)
    return Waiter(ctx, store, o.every_m, retry=lambda again: verify(ctx, again)).run().rc


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
        return Waiter(ctx, store, o.every_m, retry=retry).run().rc
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
    c = classify_landing(store, root, o) if o.landing else classify_units(ctx, store, root, o, allunits)
    data = c.data
    # A batch of independent units has a size cap; a fix verified with the unit it fixes does not, since splitting it would re-reject the first.
    if not o.covered and len(o.units) > 1 and is_number(data.get('diff_lines')) and is_number(data.get('max_batch_diff')) and data['diff_lines'] > data['max_batch_diff']:
        raise Die(f'batch of {json.dumps(data["diff_lines"])} diff lines is over review.max_batch_diff; verify each unit', 2)
    return start_verification(ctx, store, argv, o, root, allunits, c)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def scratch_path(ctx, store, scratch_id, head):
    """The scratch worktree's path at head; the scratch command's refusal ends verify with its status."""
    try:
        return make_scratch(ctx, store, scratch_id, at=head)
    except PairError as e:
        text, code = translate(e, ctx.prog)
        warn(text)
        raise ToolFailed(code) from None


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


class Sink:
    """A text stream whose writes go to a callable, for the one sink a close says its stdout and stderr to."""

    def __init__(self, say):
        self.say = say

    def write(self, text):
        self.say(text)
        return len(text)

    def flush(self):
        pass


def close_wait(ctx, store, say):
    """The WaiterOutcome of closing the verdict nobody read. Everything the wait says on either stream goes to `say` in the order it was said: what
    the wait writes to its sinks, and what the code under it writes to sys.stderr (the Prime cleanup's reasons, herdr's reply problems), which
    stands in for the sink here; a child process on the close path has its stderr piped to sys.stderr by the code that runs it. A failure ends
    the outcome with its message after what was said, and the status of the verdict the wait had already shown."""
    waiter = Waiter(ctx, store, 0, closing=True, retry=lambda again: verify(ctx, again), out=say, err=say)
    try:
        with contextlib.redirect_stderr(Sink(say)):
            return waiter.run()
    except Exception as e:
        text, rc = translate(e, ctx.prog)
        say(text)
        return WaiterOutcome(rc, waiter.status)


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
        lines = []
        ended = close_wait(ctx, store, lines.append)
        closed, closed_rc, closed_status = ''.join(lines).rstrip('\n'), ended.rc, ended.status
        if closed_status or not ended.closed_elsewhere:
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


