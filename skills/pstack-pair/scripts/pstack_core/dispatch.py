import os
import subprocess
import sys
import time

from .errors import Die, Exit
from .store import alt, event, expected_report, load, update_or_jq

# record_dispatch's jq program at the base: where the native update cannot take a document's shapes, jq decides.
RECORD_DISPATCH = (
    '.dispatch = {brief: $brief, at: $now, head: $head, generation: (.sidekick.generation // 0)} | .sent = {file: $brief, kind: "BRIEF", at: $now, epoch: $epoch}\n'
    '\t\t | (if $seen == "" then . else .sent.seen = $seen end)\n'
    '\t\t | .heads[$brief | split("/") | last | rtrimstr(".md")] //= $head\n'
    '\t\t | .pending = ((.pending // []) + (if $seen == "" then [$brief] else [] end) | unique)')


def fresh_per_brief(kind):
    """Sidekick kinds that start a fresh session for every brief."""
    return kind in ('devin', 'pi', 'prime-agent')


def record_dispatch(store, brief, now=None):
    """The dispatch record (record_dispatch in the bash before phase 3): the dispatch, the sent message, the unit's base head, and the pending list."""
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
        sidekick = doc.get('sidekick')
        generation = alt(sidekick.get('generation') if isinstance(sidekick, dict) else None, 0)
        doc['dispatch'] = {'brief': brief, 'at': at, 'head': head, 'generation': generation}
        doc['sent'] = {'file': brief, 'kind': 'BRIEF', 'at': at, 'epoch': epoch}
        if seen:
            doc['sent']['seen'] = seen
        heads = doc.get('heads')
        if heads is None:
            heads = doc['heads'] = {}
        if not isinstance(heads, dict):
            raise ValueError('heads is not an object')
        key = brief.split('/')[-1]
        key = key[:-3] if key.endswith('.md') else key
        if alt(heads.get(key), None) is None:
            heads[key] = head
        pending = alt(doc.get('pending'), [])
        if not isinstance(pending, list):
            raise ValueError('pending is not an array')
        doc['pending'] = sorted({*pending, *([] if seen else [brief])})

    update_or_jq(store, mutate, RECORD_DISPATCH, '--arg', 'brief', brief, '--arg', 'now', at, '--arg', 'head', head, '--arg', 'seen', seen,
                 '--argjson', 'epoch', str(epoch))


def take_queued(store, now=None):
    """take_queued(): the queued brief, now dispatched, or None. The bash runs it in a command substitution, so a
    failure inside it (a brief that is gone, a held lock) prints its message and reads as an empty queue."""
    taken = f'{store}/queue.taken.{os.getpid()}'
    try:
        os.rename(f'{store}/queue', taken)
    except OSError:
        return None
    try:
        with open(taken, 'rb') as f:
            brief = f.read().decode('utf-8', 'surrogateescape').rstrip('\n')
        os.unlink(taken)
    except OSError:
        # `brief="$(cat "$taken")"; rm -f "$taken"`: where the queue cannot be read (a directory, say) both commands say so themselves and the
        # brief reads empty
        r = subprocess.run(['cat', taken], stdout=subprocess.PIPE, check=False)
        brief = r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')
        subprocess.run(['rm', '-f', taken], check=False)
    try:
        if not os.path.isfile(brief):
            raise Die(f'queued brief is gone: {brief}')
        try:
            record_dispatch(store, brief, now)
        except Exit:
            # jq refused the write and has said why; the substitution this runs in has errexit off, so the brief is dispatched all the same
            pass
    except Die as e:
        sys.stderr.write(f'pair.sh: {e.message}\n')
        return None
    event(store, 'sidekick', 'send-brief', brief, 'queued')
    return brief
