class Die(Exception):
    """pair-core.sh's die(): every part is printed, and the second part is the exit code."""

    def __init__(self, *parts):
        self.message = ' '.join(str(p) for p in parts)
        self.code = int(parts[1]) if len(parts) >= 2 else 1
        super().__init__(self.message)
