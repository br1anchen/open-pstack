import glob
import os
import re
import sys
import time

from .. import git
from ..argv import Spec, read
from ..errors import Die, open_notes_refusal
from ..replies import dispatched
from ..review import open_notes
from ..steplog import StepLog
from ..store import (
    acquire_lock,
    event,
    load,
    note_id,
    pair_file,
    pause_notice,
    paused_reason,
    progress_path,
    steer_notice,
    text_at,
    unit_name,
)

RESOLVES = re.compile(r'((?:[0-9]{3}-)?n[0-9]+(,(?:[0-9]{3}-)?n[0-9]+)*)?')


def unresolved_notes(store, brief, resolves):
    """(id, file) of the blocking notes on the unit that are open and that `resolves` (a comma list of note ids) does not name."""
    named = set(resolves.split(','))
    seq = unit_name(brief)[:3]
    return [
        (note_id(f), f)
        for f in open_notes(store, brief)
        if note_id(f) not in named and f'{seq}-{note_id(f)}' not in named
    ]


def resolves_arg(args):
    """The --resolves value among the arguments after the summary, as written."""
    return read(args, Spec(valued=('--resolves',))).last('--resolves', '')


def malformed_resolves(resolves):
    """The message for a --resolves value that is no list of note ids, else ''."""
    return (
        ''
        if RESOLVES.fullmatch(resolves)
        else "--resolves takes note ids like n2,n3, or 001-n2 for another unit's note"
    )


def unpublished_resolves(store, unit, resolves):
    """The message for the first --resolves id that names no published note, else ''."""
    for note in resolves.replace(',', ' ').split():
        if re.match(r'[0-9]{3}-', note):
            # another unit's note: notes/<NNN>-<slug>-n<k>.md
            seq, bare = note.split('-', 1)
            if not glob.glob(
                os.path.join(glob.escape(store), 'notes', f'{seq}-*-{bare}.md')
            ):
                return f'--resolves {note}: no published note {bare} on unit {seq}; resolve only notes pair.sh notes printed'
        elif not os.path.isfile(f'{store}/notes/{unit}-{note}.md'):
            return f'--resolves {note}: no published note {note} on {unit}; resolve only notes pair.sh notes printed'
    return ''


def step(ctx, args, now=None):
    """The sidekick records a committed step and goes straight on; the master's wait picks it up for review. --resolves names the
    notes the step fixes. A commit this unit recorded already only gains the notes it now resolves; the check for it and the append
    are one critical section under the store lock, so two calls for one commit leave one row."""
    if len(args) < 3:
        raise Die(
            f'usage: {ctx.prog} step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]'
        )
    store, sha, summary = args[:3]
    resolves = resolves_arg(args[3:])
    if error := malformed_resolves(resolves):
        raise Die(error)
    pair_file(store)
    brief = dispatched(store)
    if not brief:
        raise Die('no brief dispatched; a step belongs to a running brief')
    r = git.run(
        text_at(load(store), 'cwd'),
        'rev-parse',
        '--verify',
        '--quiet',
        f'{sha}^{{commit}}',
        stderr=git.PIPE,
    )
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        raise Die(f'not a commit: {sha}; commit the step first')
    full = r.stdout.rstrip('\n')
    unit = unit_name(brief)
    if error := unpublished_resolves(store, unit, resolves):
        raise Die(error)
    os.makedirs(f'{store}/steps', exist_ok=True)
    os.makedirs(f'{store}/progress', exist_ok=True)
    log = StepLog(store, brief)
    stamp = time.time() if now is None else now
    lock = acquire_lock(store)
    try:
        if log.add_resolves(full, resolves):
            k = None
        else:
            k = log.next_number()
            left = unresolved_notes(store, brief, resolves)
            if left:
                raise open_notes_refusal(ctx.prog, left)
            log.append(k, full, int(stamp), resolves, summary)
    finally:
        os.rmdir(lock)
    if k is None:
        # this unit already recorded this commit: it only gained the notes it now resolves, and nothing else is written
        sys.stdout.write(f'step {full[:9]}: already recorded\n')
        sys.stdout.write(steer_notice(ctx, store))
        return 0
    with open(progress_path(store), 'a', encoding='utf-8') as f:
        f.write(
            f'{time.strftime("%H:%M", time.localtime(stamp))} step {k} {full[:9]}: {summary}\n'
        )
    event(store, 'sidekick', 'step', brief, f'{k}:{full[:9]}')
    if paused_reason(store) is not None:
        sys.stdout.write(f'step {k} recorded at {full[:9]}.\n')
        sys.stdout.write(pause_notice(store))
    else:
        sys.stdout.write(
            f'step {k} recorded at {full[:9]}; go on with the next step. At the next step boundary: pair.sh notes {store}\n'
        )
    sys.stdout.write(steer_notice(ctx, store))
    return 0
