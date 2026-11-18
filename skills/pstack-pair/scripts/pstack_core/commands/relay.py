"""relay-done: the master has sent a steer relay (mux/amp.py Amp.relay) with its own tools, so the steer counts as sent."""

import os
import re
import shutil
import sys
import time

from ..errors import Die
from ..mux import amp
from ..store import event, pair_file


def settle(record):
    """Marks the pending relay done and records what it sent, under the relay lock. The outbox entry carries the time the relay was packed, so
    of two relays the one packed last holds the sync base whichever is confirmed first; a CLI send made after the relay was packed keeps its
    own entry and bundle. Whether a newer send holds the sync base."""
    thread = record['thread']
    amp.write_relay({**record, 'state': amp.DONE, 'done_at': int(time.time())})
    at = int(record.get('at') or 0)
    newer = int(amp.load_json(amp.outbox_path()).get(thread, {}).get('at') or 0) > at
    if not newer:
        # What send records, so the CLI does not send the same text again, and the base the role's reply is merged against.
        amp.record_sent(thread, {'hash': record['hash'], 'id': record['id'], 'at': at})
        if os.path.isfile(record['bundle']):
            os.makedirs(os.path.dirname(amp.sent_path(thread)), exist_ok=True)
            shutil.copyfile(record['bundle'], amp.sent_path(thread))
    if os.path.isfile(record['bundle']):
        os.remove(record['bundle'])
    return newer


def relay_done(ctx, args):
    if len(args) != 2:
        raise Die(f'usage: {ctx.prog} relay-done <store> <id>')
    store, rid = args
    pair_file(store)
    path = os.path.join(amp.relays_dir(), f'{rid}.json')
    with amp.relay_lock():
        # read again under the lock: another relay-done, or a new relay of the same steer, may have settled it
        record = amp.load_json(path) if re.fullmatch(r'[0-9a-f]+', rid) else {}
        if record.get('store') != store:
            raise Die(f'relay-done: no relay {rid} for {store}', 5)
        if record.get('state') != amp.PENDING:
            word = (
                'done'
                if record.get('state') == amp.DONE
                else 'stale (its thread ended a turn and the steer was relayed again); nothing recorded'
            )
            print(f'relayed: {rid} already {word}')
            return 0
        newer = settle(record)
    thread = record['thread']
    event(store, 'master', 'relay', record.get('steer') or '-', f'{rid}:{thread}')
    held = '; a newer send already holds the sync base' if newer else ''
    sys.stdout.write(f'relayed: {rid} to {record["name"]} ({thread}){held}\n')
    return 0
