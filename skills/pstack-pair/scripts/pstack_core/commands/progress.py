import os
import sys
import time

from ..errors import Die
from ..store import event, pair_file, pause_notice, progress_path, steer_notice


def run(ctx, args):
    if len(args) != 2:
        raise Die('usage: pair.sh progress <store> <text>')
    store, text = args
    pair_file(store)
    path = progress_path(store)
    if path is None:
        raise Die('no brief dispatched yet; progress belongs to a running brief')
    os.makedirs(f'{store}/progress', exist_ok=True)
    with open(path, 'ab') as f:
        f.write(f'{time.strftime("%H:%M")} {text}\n'.encode('utf-8', 'surrogateescape'))
    event(store, 'sidekick', 'progress', path)
    sys.stdout.write(f'{path}\n{pause_notice(store)}{steer_notice(store)}')
    return 0
