import os


class Die(Exception):
    """pair-core.sh's die(): the first part is the message and the second the exit code (1 when absent)."""

    def __init__(self, *parts):
        self.message = str(parts[0])
        self.code = int(parts[1]) if len(parts) >= 2 else 1
        super().__init__(self.message)


class Exit(Exception):
    """End the command with a code after the given text on stderr, which the bash printed itself (a failing
    command substitution under set -e, for one) and without pair.sh's prefix."""

    def __init__(self, code, stderr=''):
        self.code = code
        self.stderr = stderr
        super().__init__(stderr)


def unbound_arg():
    """The Die bash's set -u raises for a flag whose value `$2` is missing."""
    return Die('$2: unbound variable')


def core_line(text, after='cmd_wait() {', path=None):
    """(path, line number) of the first line holding text in the function of pair-core.sh (or of the script at `path`) that starts with
    `after`, for the messages bash prefixes to its errors. When the function no longer holds the text (its body moved into Python and it is a
    shim) the line is the function's own definition; it is never 0 unless the script has no such function."""
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pair-core.sh')
    definition = 0
    try:
        with open(path, encoding='utf-8') as f:
            for number, line in enumerate(f, 1):
                if not definition and line.startswith(after):
                    definition = number
                if definition and text in line:
                    return path, number
    except OSError:
        pass
    return path, definition


def fail(text, message, after='cmd_wait() {', path=None):
    """An error bash prints for the line holding text, in the function starting with `after` (of the script at path), then ends with status 1."""
    path, number = core_line(text, after, path)
    raise Exit(1, f'{path}: line {number}: {message}\n')
