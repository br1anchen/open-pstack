import glob
import os
import re
import sys
import time

from .. import git
from ..store import Die, prog, event, load, pair_file, text_at, pause_notice, paused_reason, progress_path, steer_notice, unit_name
from ..replies import dispatched
from ..steplog import StepLog


def step(ctx, args, now=None):
    """The sidekick records a committed step and goes straight on; the master's wait picks it up for review. --resolves names the
    notes the step fixes."""
    if len(args) < 3:
        raise Die(f'usage: {prog()} step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]')
    store, sha, summary = args[:3]
    rest = args[3:]
    resolves = ''
    option = rest[0] if rest else ''
    if option == '--resolves':
        if len(rest) < 2:
            raise Die('--resolves needs a value')
        resolves = rest[1]
    elif option != '':
        raise Die(f'unknown option {option}')
    if not re.fullmatch(r'((?:[0-9]{3}-)?n[0-9]+(,(?:[0-9]{3}-)?n[0-9]+)*)?', resolves):
        raise Die('--resolves takes note ids like n2,n3, or 001-n2 for another unit\'s note')
    pair_file(store)
    brief = dispatched(store)
    if not brief:
        raise Die('no brief dispatched; a step belongs to a running brief')
    r = git.run(text_at(load(store), 'cwd'), 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}', stderr=git.PIPE)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        raise Die(f'not a commit: {sha}; commit the step first')
    full = r.stdout.rstrip('\n')
    unit = unit_name(brief)
    for note in resolves.replace(',', ' ').split():
        if re.match(r'[0-9]{3}-', note):
            # another unit's note: notes/<NNN>-<slug>-n<k>.md
            seq, bare = note.split('-', 1)
            if not glob.glob(os.path.join(glob.escape(store), 'notes', f'{seq}-*-{bare}.md')):
                raise Die(f'--resolves {note}: no published note {bare} on unit {seq}; resolve only notes pair.sh notes printed')
        elif not os.path.isfile(f'{store}/notes/{unit}-{note}.md'):
            raise Die(f'--resolves {note}: no published note {note} on {unit}; resolve only notes pair.sh notes printed')
    os.makedirs(f'{store}/steps', exist_ok=True)
    os.makedirs(f'{store}/progress', exist_ok=True)
    log = StepLog(store, brief)
    if log.add_resolves(full, resolves):
        # this unit already recorded this commit: it only gains the notes it now resolves, and nothing else is written
        sys.stdout.write(f'step {full[:9]}: already recorded\n')
        sys.stdout.write(steer_notice(store))
        return 0
    k = log.next_number()
    stamp = time.time() if now is None else now
    log.append(k, full, int(stamp), resolves, summary)
    with open(progress_path(store), 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%H:%M", time.localtime(stamp))} step {k} {full[:9]}: {summary}\n')
    event(store, 'sidekick', 'step', brief, f'{k}:{full[:9]}')
    if paused_reason(store) is not None:
        sys.stdout.write(f'step {k} recorded at {full[:9]}.\n')
        sys.stdout.write(pause_notice(store))
    else:
        sys.stdout.write(f'step {k} recorded at {full[:9]}; go on with the next step. At the next step boundary: pair.sh notes {store}\n')
    sys.stdout.write(steer_notice(store))
    return 0
