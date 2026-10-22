import os
import subprocess
import time

from .store import alt, expected_report, load, member, names, peek, update


def fresh_kind(kind):
    """Sidekick kinds whose sessions cannot be resumed, so spawn refuses a resume flag for them."""
    return kind in ('devin', 'pi', 'prime-agent')


def record_dispatch(store, brief, now=None):
    """The dispatch record: .dispatch (with .sidekick.generation), .sent (with .sent.seen when a report already exists), the unit's base head, and the pending list."""
    old = expected_report(store, brief)
    seen = f'{old}:{int(os.stat(old).st_mtime)}' if os.path.isfile(old) else ''
    pair = load(store)
    cwd = pair.get('cwd')
    r = subprocess.run(['git', '-C', 'null' if cwd is None else str(cwd), 'rev-parse', 'HEAD'], capture_output=True, check=False)
    head = r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')
    stamp = time.time() if now is None else now
    at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stamp))
    epoch = int(stamp)

    def mutate(doc):
        generation = alt(peek(doc, 'sidekick').get('generation'), 0)
        doc['dispatch'] = {'brief': brief, 'at': at, 'head': head, 'generation': generation}
        doc['sent'] = {'file': brief, 'kind': 'BRIEF', 'at': at, 'epoch': epoch}
        if seen:
            doc['sent']['seen'] = seen
        heads = member(doc, 'heads')
        key = brief.split('/')[-1]
        key = key[:-3] if key.endswith('.md') else key
        if alt(heads.get(key), None) is None:
            heads[key] = head
        doc['pending'] = sorted({*names(doc, 'pending'), *([] if seen else [brief])})

    update(store, mutate)

