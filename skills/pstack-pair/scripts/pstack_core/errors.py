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
