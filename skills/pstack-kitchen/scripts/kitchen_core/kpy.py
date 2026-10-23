"""The one way kitchen_core reaches kitchen.py: the module imported by path, whose query functions take an `Invocation` and return data. `run`, its
`main(argv)` called in this process, is left for `setup --role`, which unit 074 moves."""

import contextlib
import importlib.util
import io
import sys
import traceback
from pathlib import Path

from pstack_core.errors import ToolFailed

KITCHEN_PY = Path(__file__).resolve().parents[1] / 'kitchen.py'
MODULE = 'kitchen_py'


def load():
    """kitchen.py as a module, imported by path from the scripts directory beside this package, once."""
    module = sys.modules.get(MODULE)
    if module is None:
        spec = importlib.util.spec_from_file_location(MODULE, KITCHEN_PY)
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[MODULE]
            raise
    return module


kitchen_py = load()
KitchenError = kitchen_py.KitchenError


def invocation(store, root=None, config=None, at=None):
    """The Invocation of kitchen.py's queries for a run: the store's repo (or `root`), a `--config` snapshot where the unit has one, and a revision."""
    from .units import repo_root
    return kitchen_py.Invocation(str(root) if root is not None else repo_root(store), config, at)


def explain(error):
    """A KitchenError as kitchen.py's command line prints it, on stderr after anything already written to stdout."""
    sys.stdout.flush()
    sys.stderr.write(f'kitchen: {error}\n')


def call(query, *args, **kwargs):
    """A kitchen.py query as a plain command: a KitchenError is shown as kitchen.py prints it and ends the command with its status."""
    try:
        return query(*args, **kwargs)
    except KitchenError as e:
        explain(e)
        raise ToolFailed(e.code) from None


def run(repo, args, stdin=None):
    """`kitchen.py --repo <repo> <args>` in process: (rc, stdout, stderr). main sets the INVOCATION its child commands use itself; a
    SystemExit (argparse's usage errors, --help) becomes the rc it carries. Only what main writes through sys.stdout and sys.stderr is
    captured: a command that runs a child process (a gate) lets the child write to the real descriptors, as it does for the subprocess. stdin, text,
    is what the command reads from standard input (`manifests` reads its paths there)."""
    module = load()
    out, err = io.StringIO(), io.StringIO()
    held = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode('utf-8')), encoding='utf-8')
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = module.main([*(['--repo', str(repo)] if repo is not None else []), *args])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if isinstance(e.code, str):
                err.write(e.code + '\n')
        except Exception as e:  # what the interpreter prints for an uncaught one, status 1
            rc = 1
            err.write(uncaught(e))
        finally:
            sys.stdin = held
    return rc, out.getvalue(), err.getvalue()


def uncaught(error):
    """The traceback `python3 kitchen.py` prints when main raises: the frames from the module's `sys.exit(main())` inward."""
    summary = traceback.TracebackException.from_exception(error)
    frames = [f for f in summary.stack]
    start = next((i for i, f in enumerate(frames) if f.filename == str(KITCHEN_PY) and f.name == 'main'), 0)
    source = KITCHEN_PY.read_text().splitlines()
    line = next((n for n, text in enumerate(source, start=1) if text == '    sys.exit(main())' or text == 'sys.exit(main())'), 0)
    text = source[line - 1] if line else 'sys.exit(main())'
    column = text.index('main()') - 1
    entry = traceback.FrameSummary(str(KITCHEN_PY), line, '<module>', line=text, end_lineno=line, colno=column, end_colno=column + 6)
    summary.stack = traceback.StackSummary.from_list([entry, *frames[start:]])
    return ''.join(summary.format())


class Kitchen:
    """kitchen.py against the store's repo, in process. The repo is read from pair.json at each call."""

    def __init__(self, store, root=None):
        self.store, self.fixed = store, root

    @property
    def root(self):
        from .units import repo_root
        return self.fixed if self.fixed is not None else repo_root(self.store)

    @staticmethod
    def show(stderr):
        if stderr:
            sys.stdout.flush()
            sys.stderr.write(stderr)

    def call(self, args, show=False):
        """`kpy "$store" ARGS` as a plain command or assignment: stdout (written as it was when show is set, the command's own output). kitchen.py's
        stderr is shown; a failure ends the command with its status."""
        status, stdout, stderr = run(self.root, args)
        if show:
            sys.stdout.write(stdout)
        self.show(stderr)
        if status != 0:
            raise ToolFailed(status)
        return stdout

    def soft(self, args, show=False):
        """`kpy "$store" ARGS` where the caller tests the status: (status, stdout); stderr is shown."""
        status, stdout, stderr = run(self.root, args)
        if show:
            sys.stdout.write(stdout)
        self.show(stderr)
        return status, stdout

    def merged(self, args):
        """`kpy "$store" ARGS 2>&1`: (status, stdout and stderr together)."""
        status, stdout, stderr = run(self.root, args)
        return status, stdout + stderr

    def elsewhere(self, args):
        """`python3 kitchen.py ARGS` with no --repo (the roster and the lessons are not the repo's): (status, stdout); stderr is shown."""
        status, stdout, stderr = run(None, args)
        self.show(stderr)
        return status, stdout
