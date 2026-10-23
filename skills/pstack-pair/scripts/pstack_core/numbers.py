"""The whole numbers and the exit statuses the commands read: a flag's digits, an environment knob, a clock difference and a child's status."""

import os
import re

from .errors import BadNumber


def tdiv(a, b):
    """Integer division that truncates toward zero, so a clock that ran backwards gives a small negative figure, not one rounded away."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def exit_code(returncode):
    """A child's status as a shell reports it: 128 plus the signal number for a child a signal killed (subprocess says -N, which `sys.exit` would
    turn into 256-N)."""
    return 128 - returncode if returncode < 0 else returncode


def whole_number(text, flag, unit):
    """A CLI number: ASCII digits only, so a sign, a fraction or an expression is a usage error."""
    if not (text.isascii() and text.isdigit()):
        raise BadNumber(f'{flag} must be a whole number of {unit}')
    return int(text)


def int_or_none(text):
    """The integer a line of text holds (ASCII digits), or None."""
    text = text.strip()
    return int(text) if text.isascii() and text.isdigit() else None


def env_int(name, default):
    """${NAME:-default} as a whole number; anything else is a usage error."""
    text = os.environ.get(name) or str(default)
    if not (text.isascii() and text.isdigit()):
        raise BadNumber(f'{name} must be a whole number')
    return int(text)


def env_float(name, default):
    """${NAME:-default} as a number of seconds, fractions allowed; anything else is a usage error."""
    text = os.environ.get(name) or str(default)
    if not re.fullmatch(r'[0-9]+(\.[0-9]*)?|\.[0-9]+', text):
        raise BadNumber(f'{name} must be a number')
    return float(text)
