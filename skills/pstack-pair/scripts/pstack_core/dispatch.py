import os
import subprocess
import sys
import time

from .errors import Die
from .store import alt, event, expected_report, pair_json, update


def fresh_per_brief(kind):
    """Sidekick kinds that start a fresh session for every brief."""
    return kind in ('devin', 'pi', 'prime-agent')


def record_dispatch(store, brief):
    """record_dispatch() in pair-core.sh: the dispatch, the sent message, the unit's base head, and the pending list."""
    old = expected_report(store, brief)
    seen = f'{old}:{int(os.stat(old).st_mtime)}' if os.path.isfile(old) else ''
    pair = pair_json(store)
    cwd = pair.get('cwd')
    r = subprocess.run(['git', '-C', 'null' if cwd is None else str(cwd), 'rev-parse', 'HEAD'], capture_output=True, check=False)
    head = r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    epoch = int(time.time())

    def mutate(doc):
        sidekick = doc.get('sidekick')
        generation = alt(sidekick.get('generation') if isinstance(sidekick, dict) else None, 0)
        doc['dispatch'] = {'brief': brief, 'at': now, 'head': head, 'generation': generation}
        doc['sent'] = {'file': brief, 'kind': 'BRIEF', 'at': now, 'epoch': epoch}
        if seen:
            doc['sent']['seen'] = seen
        heads = doc.get('heads')
        if heads is None or heads is False:
            heads = doc['heads'] = {}
        key = brief.split('/')[-1]
        key = key[:-3] if key.endswith('.md') else key
        if alt(heads.get(key), None) is None:
            heads[key] = head
        doc['pending'] = sorted({*alt(doc.get('pending'), []), *([] if seen else [brief])})

    update(store, mutate)


def take_queued(store):
    """take_queued(): the queued brief, now dispatched, or None. The bash runs it in a command substitution, so a
    failure inside it (a brief that is gone, a held lock) prints its message and reads as an empty queue."""
    taken = f'{store}/queue.taken.{os.getpid()}'
    try:
        os.rename(f'{store}/queue', taken)
    except OSError:
        return None
    with open(taken, 'rb') as f:
        brief = f.read().decode('utf-8', 'surrogateescape').rstrip('\n')
    try:
        os.unlink(taken)
    except OSError:
        pass
    try:
        if not os.path.isfile(brief):
            raise Die(f'queued brief is gone: {brief}')
        record_dispatch(store, brief)
    except Die as e:
        sys.stderr.write(f'pair.sh: {e.message}\n')
        return None
    event(store, 'sidekick', 'send-brief', brief, 'queued')
    return brief
