"""Panes for the roles: where a new one goes, the folder-trust dialog that stops an agent at startup, and the wait for a file an agent writes."""
import json
import os
import re
import subprocess
import sys

from .errors import Die, Exit
from .herdr import herdr
from .review import jq_raw
from .store import pair_json
from .wait import bash_sleep, core_line

TRUST_RE = re.compile('trust this folder|project you created or one you trust|do you trust the (files|contents)', re.IGNORECASE)


def herdr_json(*args):
    """herdr with its stderr left on the terminal: the parsed JSON of its stdout, or None when it is not JSON."""
    r = subprocess.run(['herdr', *args], stdout=subprocess.PIPE, check=False)
    try:
        return json.loads(r.stdout.decode('utf-8', 'replace'))
    except ValueError:
        return None


def dig(value, *keys):
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def pick_direction(pane):
    """Cells are about twice as tall as wide: a pane is visually wider than tall when width >= 2 * height. Split wide panes right and tall
    panes down."""
    layout = herdr_json('pane', 'layout', '--pane', pane)
    panes = dig(layout, 'result', 'layout', 'panes')
    for each in panes if isinstance(panes, list) else []:
        if isinstance(each, dict) and each.get('pane_id') == pane:
            rect = each.get('rect') if isinstance(each.get('rect'), dict) else {}
            width, height = jq_raw(rect.get('width')), jq_raw(rect.get('height'))
            if not re.fullmatch(r'-?[0-9]+', height):
                # $((2 * h)) reads a word as a variable, which `set -u` refuses; the shell ends there
                path, number = core_line('$((2 *', 'pick_direction() {')
                raise Exit(127, f'{path}: line {number}: {height}: unbound variable\n')
            if not re.fullmatch(r'-?[0-9]+', width):
                path, number = core_line('-ge $((2 *', 'pick_direction() {')
                sys.stderr.write(f'{path}: line {number}: [: {width}: integer expected\n')
                return 'down'
            return 'right' if int(width) >= 2 * int(height) else 'down'
    return 'right'


def new_pane(store, role, anchor, direction, cwd):
    """A new pane for a role: a split beside the anchor, or, with placement tab (spawn --tab, or PSTACK_PLACEMENT=tab), a tab of its own in the
    master's workspace, labelled with the role. Prints where it went and returns its id."""
    pair = pair_json(store)
    slug = jq_raw(pair.get('slug'))
    if pair.get('placement') == 'tab':
        master = jq_raw((pair.get('master') or {}).get('pane_id') if isinstance(pair.get('master'), dict) else None)
        made = herdr_json('tab', 'create', '--workspace', master.split(':', 1)[0], '--cwd', cwd, '--label', f'{slug}-{role}', '--no-focus')
        pane = jq_raw(dig(made, 'result', 'root_pane', 'pane_id'))
        if not pane or pane == 'null':
            raise Die('tab create returned no pane id', 2)
        print(f'tab {slug}-{role} -> {pane}')
        return pane
    direction = direction or pick_direction(anchor)
    made = herdr_json('pane', 'split', '--pane', anchor, '--direction', direction, '--cwd', cwd, '--no-focus')
    pane = jq_raw(dig(made, 'result', 'pane', 'pane_id'))
    if not pane or pane == 'null':
        raise Die('pane split returned no pane id', 2)
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
    raise Die(f'{kind} asks whether to trust {directory}, and that decision is yours: open {kind} there once and trust the folder (or pick another '
              'kind), then rerun the spawn', 5)


def await_file(path, timeout_ms):
    """Poll for a file every PAIR_FILE_POLL_MS (5000 by default) until it exists or timeout_ms of polling has passed."""
    poll = int(os.environ.get('PAIR_FILE_POLL_MS') or 5000)
    waited = 0
    while not os.path.isfile(path) and waited < timeout_ms:
        bash_sleep(f'{poll // 1000}.{poll % 1000:03d}')
        waited += poll
    return os.path.isfile(path)


def entry_refuse_untrusted(ctx, args):
    refuse_untrusted(args[0], args[1], args[2], len(args) > 3 and args[3] != '')
    return 0
