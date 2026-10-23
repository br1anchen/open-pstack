"""Starting the verifier: which agent verifies, its arguments and name as the start line shows them, its pane and session, and the packet's delivery."""

import contextlib
import io
import json
import os
import re
import sys
import time

from pstack_core.agents import (
    has_mcp_arg,
    has_permission_arg,
    has_trust_arg,
    mcp_args,
    permission_args,
    trust_args,
)
from pstack_core.errors import (
    BadVerifierEntry,
    DamagedStore,
    PrimeFailed,
    ToolFailed,
    UnknownVerifierKind,
    VerifierStartFailed,
)
from pstack_core.panes import (
    TRUST_RE,
    agent_kind,
    agent_prompt,
    agent_start,
    agent_status,
    herdr,
    new_pane,
    reply_at,
)
from pstack_core.permission import mode_of_this_shell
from pstack_core.providers import record_prime_session
from pstack_core.send import submit_typed
from pstack_core.store import (
    event,
    field,
    json_type,
    load,
    names,
    peek,
    read,
    text_at,
    update,
)

from . import kpy
from . import verify_state as vs


def shell_word(word):
    """A word as the verifier line shows it: backslashes before the characters a shell treats as special, $'...' for a word with control characters."""
    if word == '':
        return "''"
    if any(ord(c) < 32 or ord(c) == 127 for c in word):
        names = {
            '\n': '\\n',
            '\t': '\\t',
            '\r': '\\r',
            '\x1b': '\\E',
            '\x07': '\\a',
            '\x08': '\\b',
            '\x0c': '\\f',
            '\x0b': '\\v',
            "'": "\\'",
            '\\': '\\\\',
        }
        return (
            "$'"
            + ''.join(
                names.get(c, f'\\{ord(c):03o}' if ord(c) < 32 or ord(c) == 127 else c)
                for c in word
            )
            + "'"
        )
    escaped = re.sub(r'([ !"$&\'()*,;<>?\[\\\]^`{|}])', r'\\\1', word)
    if escaped[0] in '#~':
        escaped = '\\' + escaped
    return escaped


def verifier_entry(store, cls):
    """The verifier for a class: the host roster's entry, with the sidekick marker resolved from the run (so after a failover it is the agent now
    implementing) and the master sentinel resolved to the master's own kind. One object: kind, args, fallback and where it came from."""
    try:
        roster = kpy.kitchen_py.roster_data()[1]
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise ToolFailed(2) from None
    verifiers = roster.get('verifier') if isinstance(roster, dict) else None
    entry = verifiers.get(cls) if isinstance(verifiers, dict) else None
    kind = entry.get('kind') if isinstance(entry, dict) else ''
    if kind == 'sidekick':
        sidekick = peek(load(store), 'sidekick')
        fallback = sidekick.get('fallback')
        if fallback is None:
            fallback = {}
        elif not isinstance(fallback, dict):
            raise DamagedStore(
                f'sidekick.fallback is {json_type(fallback)}, not an object'
            )
        return {
            'kind': sidekick.get('kind'),
            'args': names(sidekick, 'start_args'),
            'source': 'sidekick',
            'fallback': {'kind': fallback['kind'], 'args': names(fallback, 'args')}
            if fallback.get('kind')
            else None,
        }
    if kind == 'master' and isinstance(entry, dict):
        return {
            **entry,
            'kind': agent_kind(field(store, 'master', 'pane_id')),
            'args': [],
            'source': 'master',
        }
    return entry


def parse_entry(text):
    """The roster or a retried call's entry as an object; text that is not JSON is the one error."""
    try:
        return json.loads(text)
    except ValueError as e:
        raise BadVerifierEntry(f'verifier entry is not JSON: {e}') from e


def verifier_identity(kind, args):
    """The verifier as configured, for the start line and the packet: the kind and its model arguments, shell-quoted, with the permission and trust
    arguments left out; "<kind> (model unspecified)" when no argument is left. It is what was asked of the CLI, not proof of the model that answered."""
    skip, shown = False, ''
    for a in args:
        if skip:
            skip = False
            continue
        if a in (
            '--permission-mode',
            '--respect-workspace-trust',
            '-a',
            '-s',
            '--ask-for-approval',
            '--sandbox',
            '--mcp-config',
        ):
            skip = True
            continue
        if a.startswith(
            (
                '--permission-mode=',
                '--respect-workspace-trust=',
                '--ask-for-approval=',
                '--sandbox=',
                '--mcp-config=',
            )
        ) or a in (
            '--approve',
            '--no-approve',
            '-na',
            '--strict-mcp-config',
            '--dangerously-skip-permissions',
            '--allow-dangerously-skip-permissions',
            '--dangerously-bypass-approvals-and-sandbox',
        ):
            continue
        shown += ' ' + shell_word(a)
    return f'{kind}{shown}' if shown else f'{kind} (model unspecified)'


def verifier_args(kind, entry, directory):
    """A verifier starts with its entry's arguments, then its kind's permission default, trust flag and MCP opt-out when they are missing; entry is the
    entry as an object (or None for a kind the class does not name), directory the repo the scratch is checked out from, whose MCP servers the
    opt-out names."""
    args = (
        list(entry['args'])
        if isinstance(entry, dict) and isinstance(entry.get('args'), list)
        else []
    )
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
        ident = (
            text_at(held, 'value')
            if agent.get('agent') == 'claude' and held.get('kind') == 'id'
            else ''
        )
        cwd = text_at(agent, 'cwd')
        if not cwd:
            scratch = f'{store}/scratch/{read(store, "verifying.scratch")}'
            cwd = os.path.realpath(scratch) if os.path.isdir(scratch) else ''
        if ident and cwd:
            session = f'{os.environ.get("HOME", "")}/.claude/projects/{re.sub(r"[^A-Za-z0-9-]", "-", cwd)}/{ident}.jsonl'
    if session:
        update(store, lambda doc: vs.set_member(doc, 'verifying', 'session', session))


def choose_verifier(store, root, cls, o):
    """(entry, kind, arguments) of the verifier: the retried entry, else --kind's pick from the class's entry and its fallback, else the entry."""
    entry = verifier_entry(store, cls)
    if o.retry_entry:
        retried = parse_entry(o.retry_entry)
        kind = retried.get('kind') if isinstance(retried, dict) else ''
        picked = (
            {'kind': retried.get('kind'), 'args': retried.get('args')}
            if isinstance(retried, dict)
            else None
        )
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


def pick_entry(entry, kind):
    """The first of the entry and its fallback that names the kind, as {kind, args}; None when neither does."""
    for candidate in (
        entry,
        entry.get('fallback') if isinstance(entry, dict) else None,
    ):
        if isinstance(candidate, dict) and candidate.get('kind') == kind:
            return {'kind': candidate.get('kind'), 'args': candidate.get('args')}
    return None


def launch_verifier(v):
    """The verifier's pane beside the sidekick (the master when it is gone) and its agent, started on the scratch. A start that fails closes the pane
    and the scratch and ends the command; a Prime verifier keeps both. (pane, whether the agent started)."""
    store = v.store
    v.name = field(store, 'verifier', 'name')
    anchor = field(store, 'sidekick', 'pane_id')
    if not (anchor and herdr('pane', 'get', anchor)[0] == 0):
        anchor = field(store, 'master', 'pane_id')
    if read(store, 'placement') == 'tab':
        field(
            store, 'master', 'pane_id'
        )  # a master that is no object ends verify before any herdr call
    with contextlib.redirect_stdout(io.StringIO()):
        pane = new_pane(
            store,
            'verifier',
            anchor,
            '',
            v.scratch,
            env=(
                'PSTACK_KITCHEN_ROLE=verifier',
                f'PSTACK_KITCHEN_RUN={read(store, "slug")}',
            ),
        )
    sys.stdout.flush()
    started = agent_start(v.name, v.kind, pane, v.args, stderr=None).returncode == 0
    if not started and v.kind != 'prime-agent':
        why = f'verifier start failed in {pane}'
        screen = re.sub(
            r'[\n\t ]+',
            ' ',
            herdr('pane', 'read', pane, '--source', 'visible', '--lines', '40')[1],
        )
        if TRUST_RE.search(screen):
            why = f'{v.kind} asks whether to trust {v.scratch}, and that decision is yours: open {v.kind} in the repo once and trust it, or verify with another kind'
            herdr('pane', 'send-keys', pane, 'esc')
        herdr('pane', 'close', pane)
        vs.remove_scratch(v.ctx, store, v.scratch_id)
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
            (
                verifier['start_args'],
                verifier['session'],
                verifier['session_stopped'],
                verifier['closed'],
                verifier['launch_cwd'],
            ) = list(v.args), None, False, False, v.scratch
        doc['verifying'] = {
            'verdict': v.verdict,
            'packet': v.packet,
            'brief': v.brief,
            'scratch': v.scratch_id,
            'units': v.units_label,
            'risk': v.risk,
            'sample': sample,
            'unit': unit_label,
            'started': started_at,
            'deadline': started_at + v.timeout_m * 60,
            'class': v.cls,
            'entry': v.entry,
            'retried': v.retried,
            'argv': v.argv,
            'delivered': False,
        }

    update(store, mutate)
    vs.set_outcome(v.packet, 'running')
    if v.kind == 'prime-agent':
        if not started:
            vs.set_outcome(v.packet, 'start-failed')
            record_prime_session(store, 'verifier')
            raise PrimeFailed(
                f'Prime verifier start failed in {pane}; its pane and scratch stay for inspection (verify-wait can resume cleanup once its session is reported)'
            )
        if not record_prime_session(store, 'verifier'):
            raise PrimeFailed(
                'Prime verifier did not report its session; its pane and scratch stay for inspection'
            )
    event(store, 'master', 'send-verify', v.brief, f'{v.kind}:{v.units_label}')
    record_verifier_session(store, v.name)
    sys.stdout.write(
        f'verifier {v.name} ({v.identity}) in {pane} on {v.base[:9]}..{v.head[:9]}\n'
    )


def deliver_packet(v):
    """A just-started agent can take the text before it takes the Enter (pi drawing its startup screen), so the prompt must be seen working; an idle
    agent gets one more Enter, which submits the typed text. A startup screen can also swallow the prompt whole: an agent never seen working, with
    nothing typed and no verdict, still idle, gets the packet once more."""
    sys.stdout.flush()
    took = (
        agent_prompt(
            v.name,
            vs.verify_prompt(v.ctx, v.packet),
            wait=True,
            until='working',
            timeout_ms=30000,
        ).returncode
        == 0
    )
    submitted = submit_typed(v.name, os.path.basename(v.packet))
    if not took and submitted == 0 and not os.path.isfile(v.verdict):
        if agent_status(v.name) in ('idle', 'done'):
            vs.redeliver_packet(
                v.ctx, v.store, 'the verifier sat idle without the packet'
            )
    update(v.store, lambda doc: vs.set_member(doc, 'verifying', 'delivered', True))
    record_verifier_session(v.store, v.name)
