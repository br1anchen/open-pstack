import os
import subprocess

from ..errors import Die
from ..store import event


def run(ctx, args):
    if len(args) != 6:
        raise Die('usage: pair.sh log <store> <phase> <decision> <why> <evidence> <result>')
    store, phase, decision, *rest = args
    helper = f'{ctx.skill_root}/../show-me-your-work/scripts/log.sh'
    if not (os.path.isfile(helper) and os.access(helper, os.X_OK)):
        raise Die(f'show-me-your-work log helper not found at {helper}')
    code = subprocess.run([helper, f'{store}/decisions.tsv', phase, decision, *rest], check=False).returncode
    if code:
        raise SystemExit(code)
    event(store, 'master', 'log', '-', f'{phase}: {decision}')
    return 0
