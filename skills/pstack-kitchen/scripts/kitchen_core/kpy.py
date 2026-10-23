"""The one way kitchen_core reaches kitchen.py: the module imported by path, whose query functions take an `Invocation` and return data."""

import importlib.util
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
        if spec is None or spec.loader is None:
            raise ImportError(f'cannot load {KITCHEN_PY}')
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

    return load().Invocation(
        str(root) if root is not None else repo_root(store), config, at
    )


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
