#!/usr/bin/env python3
"""The Herdr plugin's entry: runs plugins/pstack-action.py from the checkout this plugin came with, or, when Herdr installed the plugin
directory alone, from the checkout the linked pstack-pair skill points at."""

import os
import sys
from pathlib import Path


def action_script():
    here = Path(__file__).resolve().parent
    candidates = [here.parent / 'pstack-action.py']
    for home in ('~/.agents/skills', '~/.claude/skills'):
        skill = Path(os.path.expanduser(home)) / 'pstack-pair'
        if skill.exists():
            candidates.append(skill.resolve().parents[1] / 'plugins/pstack-action.py')
    return next((c for c in candidates if c.is_file()), None)


if __name__ == '__main__':
    script = action_script()
    if script is None:
        sys.stderr.write(
            'pstack: no pstack checkout found; link the skills first (python3 scripts/pstack-sync.py --apply in a checkout)\n'
        )
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
