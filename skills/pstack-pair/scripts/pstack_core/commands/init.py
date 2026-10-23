import os
import re
import shutil
import stat
import sys
import time

from .. import git
from ..argv import Spec, read
from ..store import Die, Exit, prog, _dumps, event, member, update
from ..panes import herdr, require_herdr
from ..permission import logical_cwd

STANDING_ORDERS_TEMPLATE = 'standing-orders-template.md'
GATES = '# Gates\n\nOne entry per open question for the human: question, options, default on no answer.\n'
DECISIONS = 'ts\tphase\tdecision\twhy\tevidence\tresult\n'


def make_dir(path):
    """Create path and any parents it lacks; a failure ends the command with status 1 and says which directory and why."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise Exit(1, f'{prog()}: cannot create {e.filename or path}: {os.strerror(e.errno)}\n') from None


def install_standing_orders(skill_root, orders):
    """Copy the template to orders and make the copy writable; a failure is said on stderr and init goes on."""
    source = f'{skill_root}/references/{STANDING_ORDERS_TEMPLATE}'
    try:
        shutil.copyfile(source, orders)
    except OSError as e:
        sys.stderr.write(f'{prog()}: cannot copy {source} to {orders}: {os.strerror(e.errno)}\n')
        return
    try:
        os.chmod(orders, os.stat(orders).st_mode | stat.S_IWUSR)
    except OSError as e:
        sys.stderr.write(f'{prog()}: cannot make {orders} writable: {os.strerror(e.errno)}\n')


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
    require_herdr()
    if not args:
        raise Die(f'usage: {prog()} init <slug> [--store DIR] [--pane ID]')
    slug = args[0]
    opts = read(args[1:], Spec(valued=('--store', '--pane')))
    store, longest = opts.last('--store', ''), ctx.roles[-1]
    pane = opts.last('--pane', os.environ.get('HERDR_PANE_ID', ''))
    if not re.fullmatch(rf'[a-z][a-z0-9_-]{{0,{ctx.slug_max}}}', slug):
        raise Die(f"slug must match [a-z][a-z0-9_-]{{0,{ctx.slug_max}}} (so <slug>-{longest} fits Herdr's 32-char name limit)")
    store = store or f'{ctx.state_root}/{slug}'
    if not pane:
        raise Die('HERDR_PANE_ID is unset; run from a Herdr-managed pane, or pass --pane ID')
    for d in ctx.store_dirs:
        make_dir(f'{store}/{d}')
    master = f'{slug}-master'
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    cwd, root = logical_cwd(), git.toplevel()
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
        install_standing_orders(ctx.skill_root, orders)
    if not os.path.isfile(f'{store}/gates.md'):
        write_new(f'{store}/gates.md', GATES)
    if not os.path.isfile(f'{store}/decisions.tsv'):
        write_new(f'{store}/decisions.tsv', DECISIONS)
    print(f'standing orders: {orders}')
    return 0
