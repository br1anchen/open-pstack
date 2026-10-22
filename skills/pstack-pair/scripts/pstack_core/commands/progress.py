import os
import sys
import time

from ..errors import Die, prog
from ..store import event, pair_file, pause_notice, progress_path, steer_notice


def run(ctx, args):
    if len(args) != 2:
        raise Die(f'usage: {prog()} progress <store> <text>')
    store, text = args
    pair_file(store)
    path = progress_path(store)
    if path is None:
        raise Die('no brief dispatched yet; progress belongs to a running brief')
    os.makedirs(f'{store}/progress', exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%H:%M")} {text}\n')
    event(store, 'sidekick', 'progress', path)
    sys.stdout.write(f'{path}\n{pause_notice(store)}{steer_notice(store)}')
    return 0
