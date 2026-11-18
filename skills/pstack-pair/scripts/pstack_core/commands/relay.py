"""relay-done: the master has sent a steer relay (mux/amp.py Amp.relay) with its own tools, so the steer counts as sent."""

import os
import re
import shutil
import sys
import time

from ..errors import Die
from ..mux import amp
from ..store import event, pair_file


def relay_done(ctx, args):
    if len(args) != 2:
        raise Die(f'usage: {ctx.prog} relay-done <store> <id>')
    store, rid = args
    pair_file(store)
    record = (
        amp.load_json(os.path.join(amp.relays_dir(), f'{rid}.json'))
        if re.fullmatch(r'[0-9a-f]+', rid)
        else {}
    )
    if record.get('store') != store:
        raise Die(f'relay-done: no relay {rid} for {store}', 5)
    if record.get('state') == amp.DONE:
        print(f'relayed: {rid} already done')
        return 0
    thread, now = record['thread'], int(time.time())
    amp.write_relay({**record, 'state': amp.DONE, 'done_at': now})
    # A send the CLI made to the thread after this relay was made is newer: its outbox entry and the bundle it sent stay the thread's.
    newer = int(amp.load_json(amp.outbox_path()).get(thread, {}).get('at') or 0) > int(
        record.get('at') or 0
    )
    if not newer:
        # What send records, so the CLI does not send the same text again, and the base the role's reply is merged against.
        amp.record_sent(thread, {'hash': record['hash'], 'id': rid, 'at': now})
        if os.path.isfile(record['bundle']):
            os.makedirs(os.path.dirname(amp.sent_path(thread)), exist_ok=True)
            shutil.copyfile(record['bundle'], amp.sent_path(thread))
    if os.path.isfile(record['bundle']):
        os.remove(record['bundle'])
    event(store, 'master', 'relay', record.get('steer') or '-', f'{rid}:{thread}')
    held = '; a newer send already holds the sync base' if newer else ''
    sys.stdout.write(f'relayed: {rid} to {record["name"]} ({thread}){held}\n')
    return 0
