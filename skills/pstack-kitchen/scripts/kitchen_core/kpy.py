"""The one way kitchen_core reaches kitchen.py: the module imported by path, whose query functions take an `Invocation` and return data. `run`, its
`main(argv)` called in this process, is left for `setup --role`, which unit 074 moves."""

import contextlib
import importlib.util
import io
import sys
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


def __getattr__(name):
    """`kpy.kitchen_py` is kitchen.py, and `kpy.KitchenError` its error, imported when first named."""
    if name == 'kitchen_py':
        return load()
    if name == 'KitchenError':
        return load().KitchenError
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def invocation(store, root=None, config=None, at=None):
    """The Invocation of kitchen.py's queries for a run: the store's repo (or `root`), a `--config` snapshot where the unit has one, and a revision."""
    from .units import repo_root
    return load().Invocation(str(root) if root is not None else repo_root(store), config, at)


def explain(error):
    """A KitchenError as kitchen.py's command line prints it, on stderr after anything already written to stdout."""
    sys.stdout.flush()
    sys.stderr.write(f'kitchen: {error}\n')


def call(query, *args, **kwargs):
    """A kitchen.py query as a plain command: a KitchenError is shown as kitchen.py prints it and ends the command with its status."""
    try:
        return query(*args, **kwargs)
    except load().KitchenError as e:
        explain(e)
        raise ToolFailed(e.code) from None


def run(repo, args, stdin=None):
    """`kitchen.py --repo <repo> <args>` in process: (rc, stdout, stderr). main sets the INVOCATION its child commands use itself; a
    SystemExit (argparse's usage errors, --help) becomes the rc it carries; a bug propagates. Only what main writes through sys.stdout and sys.stderr is
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
        finally:
            sys.stdin = held
    return rc, out.getvalue(), err.getvalue()
