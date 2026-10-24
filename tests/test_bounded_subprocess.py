"""Every subprocess a test starts is bounded, so a hung child cannot stall the suite."""

import ast
import io
import tokenize
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / 'fixtures' / 'bounded'
RUNNERS = {'run', 'check_output', 'check_call', 'call'}
WAITS = {'communicate', 'wait'}
MARK = '# bounded:'


def aliased_import(node):
    """An import that would hide subprocess calls from this check."""
    if isinstance(node, ast.ImportFrom):
        return node.module == 'subprocess'
    if isinstance(node, ast.Import):
        return any(a.name == 'subprocess' and a.asname for a in node.names)
    return False


def subprocess_function(call):
    """The name of the `subprocess` function a call invokes, or None."""
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == 'subprocess'
    ):
        return func.attr
    return None


def none_defaults(function):
    """The parameters of a function that default to None."""
    args = function.args
    positional = [*args.posonlyargs, *args.args]
    pairs = list(
        zip(
            positional[len(positional) - len(args.defaults) :],
            args.defaults,
            strict=True,
        )
    )
    pairs += [
        (arg, d) for arg, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d
    ]
    return {
        arg.arg
        for arg, default in pairs
        if isinstance(default, ast.Constant) and default.value is None
    }


def has_timeout(call, scope):
    """Whether the call names a timeout that is not None, nor a parameter of its function that defaults to None."""
    unset = none_defaults(scope) if hasattr(scope, 'args') else set()
    for keyword in call.keywords:
        if keyword.arg != 'timeout':
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and value.value is None:
            return False
        return not (isinstance(value, ast.Name) and value.id in unset)
    return False


def marked_lines(source):
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return {
        token.start[0]
        for token in tokens
        if token.type == tokenize.COMMENT and token.string.startswith(MARK)
    }


def enclosing_scopes(tree):
    """Each node mapped to the function (or module) that contains it."""
    scope_of = {}

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            scope_of[child] = scope
            is_function = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            visit(child, child if is_function else scope)

    scope_of[tree] = tree
    visit(tree, tree)
    return scope_of


def popen_target(tree, call):
    """The name a Popen is assigned to, or None when it is not bound to one."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and node.value is call
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            return node.targets[0].id
    return None


def waits_with_timeout(scope, name, after):
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WAITS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and has_timeout(node, scope)
            and node.lineno >= after
        ):
            return True
    return False


def unbounded_calls(source):
    """The line and a reason for each subprocess call in `source` that has no bound."""
    tree = ast.parse(source)
    marks = marked_lines(source)
    scopes = enclosing_scopes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and aliased_import(node):
            found.append((node.lineno, 'subprocess imported under another name'))
        if not isinstance(node, ast.Call):
            continue
        function = subprocess_function(node)
        if function in RUNNERS:
            if not has_timeout(node, scopes[node]):
                found.append(
                    (
                        node.lineno,
                        f'subprocess.{function} without a timeout that is set',
                    )
                )
        elif function == 'Popen':
            span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
            if any(line in marks for line in span):
                continue
            name = popen_target(tree, node)
            if name and waits_with_timeout(scopes[node], name, node.lineno):
                continue
            found.append(
                (
                    node.lineno,
                    f'subprocess.Popen with no communicate/wait(timeout=) and no {MARK} comment',
                )
            )
    return sorted(found)


class BoundedSubprocessTests(unittest.TestCase):
    def fixture(self, name):
        return unbounded_calls((FIXTURES / name).read_text())

    def test_a_run_without_a_timeout_is_reported_with_its_line(self):
        self.assertEqual(
            self.fixture('bare_run.py'),
            [(5, 'subprocess.run without a timeout that is set')],
        )

    def test_a_run_with_a_literal_none_timeout_is_reported(self):
        self.assertEqual(
            [line for line, _ in self.fixture('run_with_none_timeout.py')], [5]
        )

    def test_a_run_whose_timeout_is_a_parameter_defaulting_to_none_is_reported(self):
        self.assertEqual(
            [line for line, _ in self.fixture('run_with_none_default.py')], [5]
        )

    def test_a_run_whose_timeout_parameter_defaults_to_a_number_is_bounded(self):
        self.assertEqual(self.fixture('run_with_number_default.py'), [])

    def test_an_import_that_renames_subprocess_is_reported(self):
        self.assertEqual(
            [line for line, _ in self.fixture('aliased_import.py')], [1, 2]
        )

    def test_a_run_with_a_timeout_is_bounded(self):
        self.assertEqual(self.fixture('run_with_timeout.py'), [])

    def test_a_run_whose_keywords_only_come_from_kwargs_is_reported(self):
        self.assertEqual(
            [line for line, _ in self.fixture('run_with_kwargs_only.py')], [5]
        )

    def test_a_popen_whose_process_is_communicated_with_a_timeout_is_bounded(self):
        self.assertEqual(self.fixture('popen_with_communicate.py'), [])

    def test_a_popen_with_a_bounded_comment_is_bounded(self):
        self.assertEqual(self.fixture('popen_with_comment.py'), [])

    def test_a_popen_without_a_bound_is_reported(self):
        self.assertEqual([line for line, _ in self.fixture('popen_bare.py')], [5])

    def test_every_subprocess_call_in_the_test_modules_is_bounded(self):
        report = [
            f'{path.relative_to(TESTS.parent)}:{line}: {reason}'
            for path in sorted(TESTS.glob('test_*.py'))
            for line, reason in unbounded_calls(path.read_text())
        ]
        self.assertEqual(report, [])


if __name__ == '__main__':
    unittest.main()
