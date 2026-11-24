"""The relay the master sends with its own tools (mux/amp.py Amp.relay): what both a relayed steer (commands/steer.py relay_steer) and a relayed
STOP (commands/stop.py relay_stop) print, and relay-done, which the master runs once it has sent one."""

import os
import re
import shutil
import sys
import time

from ..errors import Die
from ..mux import amp
from ..store import event, pair_file


def print_relay(prog, store, name, record, how, kind, arg, detail='relay-pending'):
    """What a relay the master must send with its own tools prints: the too-large fallback, and for a relay that was made, is still pending or was
    already delivered, its id with the three calls the master makes, logging `kind` (arg) with `detail` when the relay was made. Whether the
    master carries the send (a caller falls back to sending it through the CLI)."""
    if how == 'too-large':
        # relay returns 'too-large' only with the bundle's size; the None case is the next line's.
        print(
            f"relay: bundle is {record['size'] / 1048576:.1f} MiB, over upload_thread_file's 4 MiB; send it with the CLI after the turn instead"
        )
    if record is None or how == 'too-large':
        return False
    rid, thread = record['id'], record['thread']
    if how == 'delivered':
        print(
            f'relayed: {rid} for {name} ({thread}) (already relayed into the turn the thread is still in; not relayed again)'
        )
        return True
    if how == 'made':
        event(store, 'master', kind, arg, detail)
    again = '' if how == 'made' else ' (already pending; not made again)'
    print(
        f'relay-pending: {rid} for {name} ({thread}){again}\n'
        f'relay: upload_thread_file path={record["bundle"]} destination={record["upload_to"]} thread={thread}\n'
        f'relay: send_thread_message to {thread} with the text of {record["message"]}\n'
        f'relay: then run {prog} relay-done {store} {rid}'
    )
    return True


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
        # Kept by id whether or not a newer send holds the legacy base: the role synced this bundle, and its reply names it.
        amp.keep_bundle_by_id(thread, record['bundle'])
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
                else 'stale (its thread ended a turn and it was relayed again); nothing recorded'
            )
            print(f'relayed: {rid} already {word}')
            return 0
        newer = settle(record)
    thread = record['thread']
    event(store, 'master', 'relay', record.get('steer') or '-', f'{rid}:{thread}')
    held = '; a newer send already holds the sync base' if newer else ''
    sys.stdout.write(f'relayed: {rid} to {record["name"]} ({thread}){held}\n')
    return 0
