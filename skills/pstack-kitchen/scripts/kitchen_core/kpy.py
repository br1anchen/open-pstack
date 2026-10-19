"""The one way kitchen_core reaches kitchen.py: its `main(argv)` called in this process, with the output and status it would have had as
`python3 kitchen.py --repo <repo> <args>`. The bash `kpy` (kitchen.sh) is the model for what is passed."""

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

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


def run(repo, args):
    """`kitchen.py --repo <repo> <args>` in process: (rc, stdout, stderr). main sets the INVOCATION its child commands use itself; a
    SystemExit (argparse's usage errors, --help) becomes the rc it carries. Only what main writes through sys.stdout and sys.stderr is
    captured: a command that runs a child process (a gate) lets the child write to the real descriptors, as it does for the subprocess."""
    module = load()
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = module.main(['--repo', str(repo), *args])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if isinstance(e.code, str):
                err.write(e.code + '\n')
    return rc, out.getvalue(), err.getvalue()
