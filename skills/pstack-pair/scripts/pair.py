#!/usr/bin/env python3
"""Entry for the commands pair-core.sh runs in Python (see pycore there)."""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pstack_core.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
