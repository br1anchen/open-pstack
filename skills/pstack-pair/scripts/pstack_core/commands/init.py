import os
import re
import subprocess
import time

from ..errors import Die, Exit, prog
from ..herdr import herdr
from ..permission import logical_cwd
from ..store import TEXT, _dumps, event, member, update

STANDING_ORDERS_TEMPLATE = 'standing-orders-template.md'
GATES = '# Gates\n\nOne entry per open question for the human: question, options, default on no answer.\n'
DECISIONS = 'ts\tphase\tdecision\twhy\tevidence\tresult\n'


def make_dir(path):
    """mkdir -p, where a failure ends the command with mkdir's own message and status."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        code = subprocess.run(['mkdir', '-p', path], check=False).returncode
        if code:
            raise Exit(code)


def git_root():
    try:
        r = subprocess.run(['git', 'rev-parse', '--show-toplevel'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, **TEXT)
    except OSError:
        return ''
    return r.stdout.rstrip('\n') if r.returncode == 0 else ''


def write_new(path, text):
    """Writes text to path; a failure ends the command with the system's reason."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
    except OSError as e:
        raise Die(f'{path}: {e.strerror}')


def re_register(store, pane, workspace, tab, now):
    """json_update of .master.pane_id, .master.workspace_id, .master.tab_id and .master.registered_at."""
    def mutate(doc):
        member(doc, 'master').update({'pane_id': pane, 'workspace_id': workspace, 'tab_id': tab, 'registered_at': now})
    update(store, mutate)


def run(ctx, args):
    if os.environ.get('PSTACK_KITCHEN_ROLE') == 'verifier':
        raise Exit(2, f'{prog()}: init refuses under PSTACK_KITCHEN_ROLE=verifier: it renames the calling pane, and the kitchen would lose its verifier\n')
    if os.environ.get('HERDR_ENV') != '1':
        raise Die('not running inside Herdr (HERDR_ENV != 1)')
    if not args:
        raise Die(f'usage: {prog()} init <slug> [--store DIR] [--pane ID]')
    slug, rest = args[0], args[1:]
    store, longest, pane = '', ctx.roles[-1], os.environ.get('HERDR_PANE_ID', '')
    i = 0
    while i < len(rest):
        option = rest[i]
        if option in ('--store', '--pane'):
            if i + 1 >= len(rest):
                raise Die(f'{option} needs a value')
            if option == '--store':
                store = rest[i + 1]
            else:
                pane = rest[i + 1]
            i += 2
        else:
            raise Die(f'unknown option {option}')
    if not re.fullmatch(rf'[a-z][a-z0-9_-]{{0,{ctx.slug_max}}}', slug):
        raise Die(f"slug must match [a-z][a-z0-9_-]{{0,{ctx.slug_max}}} (so <slug>-{longest} fits Herdr's 32-char name limit)")
    store = store or f'{ctx.state_root}/{slug}'
    if not pane:
        raise Die('HERDR_PANE_ID is unset; run from a Herdr-managed pane, or pass --pane ID')
    for d in ctx.store_dirs:
        make_dir(f'{store}/{d}')
    master = f'{slug}-master'
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    cwd, root = logical_cwd(), git_root()
    workspace, tab = os.environ.get('HERDR_WORKSPACE_ID', ''), os.environ.get('HERDR_TAB_ID', '')
    # HERDR_PANE_ID can be stale: Codex runs every session's commands through one shared app-server daemon, which keeps the pane it was
    # started in.
    if herdr('agent', 'rename', pane, master)[0] != 0:
        raise Die(f"pane {pane} is not a live Herdr pane; HERDR_PANE_ID is stale when a harness runs commands through a shared daemon "
                  "(Codex's app-server). Pass your pane with --pane ID (herdr agent list shows each agent's pane)", 2)
    if os.path.isfile(f'{store}/pair.json'):
        re_register(store, pane, workspace, tab, now)
        print(f'store: {store} (re-registered master {master} in {pane})')
    else:
        doc = {'slug': slug, 'store': store, 'created_at': now, 'cwd': cwd, 'git_root': root,
               'master': {'name': master, 'pane_id': pane, 'workspace_id': workspace, 'tab_id': tab, 'registered_at': now}}
        for role in ctx.roles:
            doc[role] = {'name': f'{slug}-{role}', 'pane_id': None, 'kind': None, 'started_at': None}
        if 'consultant' in doc:
            doc['scratch'] = []
        write_new(f'{store}/pair.json', _dumps(doc))
        print(f'store: {store} (master {master} in {pane})')
    event(store, 'master', 'init', '-', ctx.skill)
    # The template comes from a read-only install, so give the copy its own mode.
    orders = f'{store}/standing-orders.md'
    if not os.path.isfile(orders):
        if subprocess.run(['cp', f'{ctx.skill_root}/references/{STANDING_ORDERS_TEMPLATE}', orders], check=False).returncode == 0:
            subprocess.run(['chmod', 'u+w', orders], check=False)
    if not os.path.isfile(f'{store}/gates.md'):
        write_new(f'{store}/gates.md', GATES)
    if not os.path.isfile(f'{store}/decisions.tsv'):
        write_new(f'{store}/decisions.tsv', DECISIONS)
    print(f'standing orders: {orders}')
    return 0
