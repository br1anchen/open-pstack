import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass

from .. import git, mux
from ..argv import Spec, read
from ..errors import Die, Exit, FileFailed, PathFailed, reason
from ..panes import require_mux
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


def jj_git_worktree(cwd, write):
    """The git root of a jj workspace that has no .git of its own (`jj workspace add` beside a repo, not colocated), where git finds no repository and
    every git and gate command fails: a git worktree of the workspace's backing repository is linked in place at the working copy's parent, as a jj
    scratch is (added elsewhere with no checkout, its .git moved in, repaired, its index read from HEAD). '' when cwd is no such workspace or jj or
    git cannot do it."""
    if (
        not os.path.isdir(f'{cwd}/.jj')
        or os.path.exists(f'{cwd}/.git')
        or not shutil.which('jj')
    ):
        return ''

    def jj(*args):
        done = subprocess.run(
            ['jj', '-R', cwd, '--ignore-working-copy', *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return done.stdout.strip() if done.returncode == 0 else ''

    gitdir = jj('git', 'root')
    commit = jj('log', '--no-graph', '-r', '@-', '-T', 'commit_id')
    if not gitdir or not commit:
        return ''
    staging = f'{cwd}.pstack-git'
    if not git.ok(
        None,
        '--git-dir',
        gitdir,
        'worktree',
        'add',
        '--no-checkout',
        '--detach',
        staging,
        commit,
    ):
        return ''
    os.replace(f'{staging}/.git', f'{cwd}/.git')
    os.rmdir(staging)
    # git would list .jj as untracked, and a gate that wants a clean tree would fail
    if not os.path.exists(f'{cwd}/.jj/.gitignore'):
        with open(f'{cwd}/.jj/.gitignore', 'w') as f:
            f.write('/*\n')
    if not (git.ok(cwd, 'worktree', 'repair') and git.ok(cwd, 'reset', '-q')):
        return ''
    write(
        f'git: linked a worktree of {gitdir} at {cwd} (jj workspace without .git) at {commit[:9]}\n'
    )
    return git.toplevel(cwd)


def re_register(store, pane, workspace, tab, now, root=''):
    """json_update of .master.pane_id, .master.workspace_id, .master.tab_id and .master.registered_at, and of .git_root when it is empty or gone and
    the checkout now has one."""

    def mutate(doc):
        if root and not os.path.isdir(doc.get('git_root') or ''):
            doc['git_root'] = root
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
    require_mux()
    if not args:
        raise Die(f'usage: {ctx.prog} init <slug> [--store DIR] [--pane ID]')
    slug = args[0]
    opts = read(args[1:], Spec(valued=('--store', '--pane')))
    store, longest = opts.last('--store', ''), ctx.roles[-1]
    pane = opts.last('--pane', mux.get().own_pane())
    if not re.fullmatch(rf'[a-z][a-z0-9_-]{{0,{ctx.slug_max}}}', slug):
        raise Die(
            f'slug must match [a-z][a-z0-9_-]{{0,{ctx.slug_max}}} (so <slug>-{longest} fits the 32-char agent name limit)'
        )
    store = store or f'{ctx.state_root}/{slug}'
    if not pane:
        m = mux.get()
        raise Die(
            f'{m.pane_var} is unset; run from a {m.label}-managed pane, or pass --pane ID'
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
    root = root or jj_git_worktree(cwd, write)
    workspace, tab = mux.get().location()
    # The pane variable can be stale: Codex runs every session's commands through one shared app-server daemon, which keeps the pane it was
    # started in.
    if not mux.get().rename(pane, master):
        m = mux.get()
        raise Die(
            f'pane {pane} is not a live {m.label} pane; {m.pane_var} is stale when a harness runs commands through a shared daemon '
            f"(Codex's app-server). Pass your pane with --pane ID ({m.list_agents()} shows each agent's pane)",
            2,
        )
    if os.path.isfile(f'{store}/pair.json'):
        re_register(store, pane, workspace, tab, now, root)
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
