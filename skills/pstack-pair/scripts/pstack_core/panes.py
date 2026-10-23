"""Panes for the roles: where a new one goes, the folder-trust dialog that stops an agent at startup, and the wait for a file an agent writes."""

import json
import os
import re
import subprocess
import sys
import time

from .store import TEXT, load, peek, text_at
from .numbers import env_int
from .errors import NoPaneId, NotInHerdr, PaneSizeUnreadable, TrustPrompt


TRUST_RE = re.compile('trust this folder|project you created or one you trust|do you trust the (files|contents)', re.IGNORECASE)


DEVNULL, PIPE, MERGED = subprocess.DEVNULL, subprocess.PIPE, subprocess.STDOUT


def run_herdr(args, stdout=PIPE, stderr=PIPE):
    """The one place herdr is run: its CompletedProcess, with each stream captured (PIPE), dropped (DEVNULL), merged into stdout (MERGED) or
    left on the terminal (None)."""
    return subprocess.run(['herdr', *args], stdout=stdout, stderr=stderr, check=False, **TEXT)


def require_herdr(what=None):
    """Ends with the caller's message (the default says the command must run inside Herdr) when HERDR_ENV is not 1."""
    if os.environ.get('HERDR_ENV') != '1':
        raise NotInHerdr(what)


def herdr_json(*args):
    """herdr with its stderr left on the terminal: the parsed JSON of its stdout, or None when it is not JSON."""
    try:
        return json.loads(run_herdr(args, stderr=None).stdout)
    except ValueError:
        return None


def herdr_ok(*args):
    """herdr with stdout dropped and stderr left on the terminal: whether it succeeded."""
    return run_herdr(args, stdout=DEVNULL, stderr=None).returncode == 0


def agent_start(name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE):
    """herdr agent start NAME in the pane, running `args` as the agent's command line: the CompletedProcess."""
    return run_herdr(['agent', 'start', name, '--kind', kind, '--pane', pane, '--timeout', str(timeout_ms), '--', *args], stdout, stderr)


def agent_prompt(name, text, wait=False, until=None, timeout_ms=None, stdout=PIPE, stderr=PIPE):
    """herdr agent prompt NAME TEXT, optionally waiting (until a state) up to timeout_ms: the CompletedProcess."""
    if until and not wait:
        raise ValueError('agent_prompt: until needs wait')
    argv = ['agent', 'prompt', name, text]
    if wait:
        argv.append('--wait')
    if until:
        argv += ['--until', until]
    if timeout_ms is not None:
        argv += ['--timeout', str(timeout_ms)]
    return run_herdr(argv, stdout, stderr)


def herdr_get(name):
    """The stdout of herdr agent get NAME, whatever its status."""
    return herdr('agent', 'get', name)[1].rstrip('\n')


def pick_direction(pane):
    """Cells are about twice as tall as wide: a pane is visually wider than tall when width >= 2 * height. Split wide panes right and tall
    panes down."""
    layout = herdr_json('pane', 'layout', '--pane', pane)
    panes = dig(layout, 'result', 'layout', 'panes')
    for each in panes if isinstance(panes, list) else []:
        if isinstance(each, dict) and each.get('pane_id') == pane:
            rect = each.get('rect') if isinstance(each.get('rect'), dict) else {}
            width, height = rect.get('width'), rect.get('height')
            if not all(isinstance(n, int) and not isinstance(n, bool) for n in (width, height)):
                raise PaneSizeUnreadable(f'pane {pane} has a size that is not a number: {width} x {height}')
            return 'right' if width >= 2 * height else 'down'
    return 'right'


def new_pane(store, role, anchor, direction, cwd, env=()):
    """A new pane for a role: a split beside the anchor, or, with placement tab (spawn --tab, or PSTACK_PLACEMENT=tab), a tab of its own in the
    master's workspace, labelled with the role; each KEY=VALUE of env is an --env for the launched process. Prints where it went and returns its id."""
    pair = load(store)
    slug = text_at(pair, 'slug')
    env_args = [part for kv in env for part in ('--env', kv)]
    if pair.get('placement') == 'tab':
        master = text_at(peek(pair, 'master'), 'pane_id')
        made = herdr_json('tab', 'create', '--workspace', master.split(':', 1)[0], '--cwd', cwd, '--label', f'{slug}-{role}', '--no-focus', *env_args)
        pane = dig(made, 'result', 'root_pane', 'pane_id')
        if not pane or not isinstance(pane, str):
            raise NoPaneId('tab create returned no pane id')
        print(f'tab {slug}-{role} -> {pane}')
        return pane
    direction = direction or pick_direction(anchor)
    made = herdr_json('pane', 'split', '--pane', anchor, '--direction', direction, '--cwd', cwd, '--no-focus', *env_args)
    pane = dig(made, 'result', 'pane', 'pane_id')
    if not pane or not isinstance(pane, str):
        raise NoPaneId('pane split returned no pane id')
    print(f'split {anchor} {direction} -> {pane}')
    return pane


def refuse_untrusted(pane, kind, directory, close=False):
    """A dialog asking to trust the working folder (Claude, Codex) blocks an agent at startup. Trusting a repo is the human's decision, never the
    script's: decline it, which returns the pane to its shell, and stop with what to do. Claude's dialog defaults to "No, exit", so an Enter
    would quit."""
    code, out = herdr('pane', 'read', pane, '--source', 'visible', '--lines', '40')
    if not TRUST_RE.search(re.sub(r'[\n\t ]+', ' ', out)):
        return
    herdr('pane', 'send-keys', pane, 'esc')
    # A pane split for this start holds nothing else; a retry opens its own.
    if close:
        herdr('pane', 'close', pane)
    raise TrustPrompt(f'{kind} asks whether to trust {directory}, and that decision is yours: open {kind} there once and trust the folder (or pick another '
                      'kind), then rerun the spawn')


def await_file(path, timeout_ms):
    """Poll for a file every PAIR_FILE_POLL_MS (5000 by default) until it exists or timeout_ms of polling has passed."""
    poll = env_int('PAIR_FILE_POLL_MS', 5000)
    waited = 0
    while not os.path.isfile(path) and waited < timeout_ms:
        time.sleep(poll / 1000)
        waited += poll
    return os.path.isfile(path)


BUSY = re.compile(r'esc (twice )?to interrupt|guide devin while it works|send queued messages now|↵ send now|ctrl\+enter to send now',
                  re.IGNORECASE)


def herdr(*args):
    """herdr with stderr dropped: (exit code, stdout text)."""
    r = run_herdr(args)
    return r.returncode, r.stdout


def pane_text(name):
    """The pane's visible screen as one line."""
    return re.sub(r'[\n\t ]+', ' ', herdr('agent', 'read', name, '--source', 'visible', '--lines', '40')[1])


def pane_busy(name):
    return BUSY.search(pane_text(name)) is not None


def parse(reply):
    """The one JSON document a herdr or daemon reply holds, or None when it is not JSON."""
    try:
        return json.loads(reply)
    except ValueError:
        return None


def dig(value, *keys):
    """The value at keys in parsed JSON, None when a step is absent or over a value that is no object."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def reply_at(reply, *keys, quiet=False):
    """The value at keys in a herdr or daemon reply, None when it has none. A reply that is not JSON, or whose path steps
    over a value that is no object, is one stderr line saying so (quiet leaves it out), and None."""
    doc = parse(reply)
    problem = 'is not JSON' if doc is None and reply.strip() != 'null' else ''
    if not problem:
        for key in keys:
            if doc is not None and not isinstance(doc, dict):
                problem = f'has no object on the way to {".".join(keys)}'
                break
            doc = doc.get(key) if doc else None
        else:
            return doc
    if not quiet:
        sys.stdout.flush()
        sys.stderr.write(f'herdr reply {problem}\n')
        sys.stderr.flush()
    return None


def error_code(err):
    """The error code of the reply herdr printed, or ''."""
    code = dig(parse(err), 'error', 'code')
    return code if isinstance(code, str) else ''


def error_state(err):
    """The error code of the reply herdr printed, `herdr_error` when it has none."""
    return error_code(err) or 'herdr_error'


def agent_state(out, default):
    """The agent status in the reply, else default ('' when there is none)."""
    status = dig(parse(out), 'result', 'agent', 'agent_status')
    return status if isinstance(status, str) and status else (default or '')


def agent_field(out, key):
    """The key of the agent in herdr's JSON reply; None when it is missing or null."""
    agent = dig(parse(out), 'result', 'agent')
    return agent.get(key) if isinstance(agent, dict) else None


def agent_status(name):
    """idle|working|blocked|done|unknown, or absent when herdr has no such agent."""
    code, out = herdr('agent', 'get', name)
    if code != 0:
        return 'absent'
    state = agent_field(out, 'agent_status') or 'unknown'
    if state in ('idle', 'done') and pane_busy(name):
        return 'working'
    return state


def agent_kind(name):
    """The herdr agent kind hosting name (a name or pane id), or empty."""
    return agent_field(herdr('agent', 'get', name)[1], 'agent') or ''
