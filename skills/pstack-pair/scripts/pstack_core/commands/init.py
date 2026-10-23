import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass

from .. import git
from ..argv import Spec, read
from ..errors import Die, Exit, FileFailed, PathFailed, reason
from ..panes import herdr, require_herdr
from ..permission import logical_cwd
from ..store import _dumps, event, member, update

STANDING_ORDERS_TEMPLATE = 'standing-orders-template.md'
GATES = '# Gates\n\nOne entry per open question for the human: question, options, default on no answer.\n'
DECISIONS = 'ts\tphase\tdecision\twhy\tevidence\tresult\n'


def make_dir(path):
    """Create path and any parents it lacks; a failure ends the command with status 1 and says which directory and why."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise FileFailed('create', e.filename or path, e) from None


def install_standing_orders(ctx, orders):
    """Copy the template to orders and make the copy writable; a failure is said on stderr and init goes on."""
    source = f'{ctx.skill_root}/references/{STANDING_ORDERS_TEMPLATE}'
    try:
        shutil.copyfile(source, orders)
    except OSError as e:
        sys.stderr.write(f'{ctx.prog}: cannot copy {source} to {orders}: {reason(e)}\n')
        return
    try:
        os.chmod(orders, os.stat(orders).st_mode | stat.S_IWUSR)
    except OSError as e:
        sys.stderr.write(f'{ctx.prog}: cannot make {orders} writable: {reason(e)}\n')


def write_new(path, text):
    """Writes text to path; a failure ends the command with the system's reason."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
    except OSError as e:
        raise PathFailed(path, e) from None


def re_register(store, pane, workspace, tab, now):
    """json_update of .master.pane_id, .master.workspace_id, .master.tab_id and .master.registered_at."""

    def mutate(doc):
        member(doc, 'master').update(
            {
                'pane_id': pane,
                'workspace_id': workspace,
                'tab_id': tab,
                'registered_at': now,
            }
        )

    update(store, mutate)


@dataclass(frozen=True)
class InitRequest:
    slug: str
    store: str
    pane: str


@dataclass(frozen=True)
class InitResult:
    store: str
    orders: str


def init_request(ctx, args):
    """The InitRequest of `init <slug> [--store DIR] [--pane ID]`, once the command may run here at all."""
    if os.environ.get('PSTACK_KITCHEN_ROLE') == 'verifier':
        raise Exit(
            2,
            f'{ctx.prog}: init refuses under PSTACK_KITCHEN_ROLE=verifier: it renames the calling pane, and the kitchen would lose its verifier\n',
        )
    require_herdr()
    if not args:
        raise Die(f'usage: {ctx.prog} init <slug> [--store DIR] [--pane ID]')
    slug = args[0]
    opts = read(args[1:], Spec(valued=('--store', '--pane')))
    store, longest = opts.last('--store', ''), ctx.roles[-1]
    pane = opts.last('--pane', os.environ.get('HERDR_PANE_ID', ''))
    if not re.fullmatch(rf'[a-z][a-z0-9_-]{{0,{ctx.slug_max}}}', slug):
        raise Die(
            f"slug must match [a-z][a-z0-9_-]{{0,{ctx.slug_max}}} (so <slug>-{longest} fits Herdr's 32-char name limit)"
        )
    store = store or f'{ctx.state_root}/{slug}'
    if not pane:
        raise Die(
            'HERDR_PANE_ID is unset; run from a Herdr-managed pane, or pass --pane ID'
        )
    return InitRequest(slug, store, pane)


def init_store(ctx, request, out=None):
    """Creates or re-registers the store and returns where it is. The lines the command prints go to `out` (default: standard output) as they are
    produced; what goes wrong is said on stderr or raised."""
    write = sys.stdout.write if out is None else out
    slug, store, pane = request.slug, request.store, request.pane
    for d in ctx.store_dirs:
        make_dir(f'{store}/{d}')
    master = f'{slug}-master'
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    cwd, root = logical_cwd(), git.toplevel()
    workspace, tab = (
        os.environ.get('HERDR_WORKSPACE_ID', ''),
        os.environ.get('HERDR_TAB_ID', ''),
    )
    # HERDR_PANE_ID can be stale: Codex runs every session's commands through one shared app-server daemon, which keeps the pane it was
    # started in.
    if herdr('agent', 'rename', pane, master)[0] != 0:
        raise Die(
            f'pane {pane} is not a live Herdr pane; HERDR_PANE_ID is stale when a harness runs commands through a shared daemon '
            "(Codex's app-server). Pass your pane with --pane ID (herdr agent list shows each agent's pane)",
            2,
        )
    if os.path.isfile(f'{store}/pair.json'):
        re_register(store, pane, workspace, tab, now)
        write(f'store: {store} (re-registered master {master} in {pane})\n')
    else:
        doc = {
            'slug': slug,
            'store': store,
            'created_at': now,
            'cwd': cwd,
            'git_root': root,
            'master': {
                'name': master,
                'pane_id': pane,
                'workspace_id': workspace,
                'tab_id': tab,
                'registered_at': now,
            },
        }
        for role in ctx.roles:
            doc[role] = {
                'name': f'{slug}-{role}',
                'pane_id': None,
                'kind': None,
                'started_at': None,
            }
        if 'consultant' in doc:
            doc['scratch'] = []
        write_new(f'{store}/pair.json', _dumps(doc))
        write(f'store: {store} (master {master} in {pane})\n')
    event(store, 'master', 'init', '-', ctx.skill)
    # The template comes from a read-only install, so give the copy its own mode.
    orders = f'{store}/standing-orders.md'
    if not os.path.isfile(orders):
        install_standing_orders(ctx, orders)
    if not os.path.isfile(f'{store}/gates.md'):
        write_new(f'{store}/gates.md', GATES)
    if not os.path.isfile(f'{store}/decisions.tsv'):
        write_new(f'{store}/decisions.tsv', DECISIONS)
    write(f'standing orders: {orders}\n')
    return InitResult(store, orders)


def run(ctx, args):
    init_store(ctx, init_request(ctx, args))
    return 0
