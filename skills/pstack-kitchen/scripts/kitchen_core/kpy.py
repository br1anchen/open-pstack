"""The one way kitchen_core reaches kitchen.py: its `main(argv)` called in this process, with the output and status it would have had as
`python3 kitchen.py --repo <repo> <args>`. The bash `kpy` (kitchen.sh) is the model for what is passed."""

import contextlib
import importlib.util
import io
import sys
import traceback
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


def run(repo, args, stdin=None):
    """`kitchen.py --repo <repo> <args>` in process: (rc, stdout, stderr). main sets the INVOCATION its child commands use itself; a
    SystemExit (argparse's usage errors, --help) becomes the rc it carries. Only what main writes through sys.stdout and sys.stderr is
    captured: a command that runs a child process (a gate) lets the child write to the real descriptors, as it does for the subprocess. stdin, bytes,
    is what the command reads from standard input (`manifests` reads its paths there)."""
    module = load()
    out, err = io.StringIO(), io.StringIO()
    held = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding='utf-8', errors='surrogateescape')
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
