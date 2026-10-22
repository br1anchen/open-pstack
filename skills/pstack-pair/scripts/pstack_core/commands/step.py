import glob
import os
import re
import subprocess
import sys
import time

from ..errors import Die, Exit
from ..replies import dispatched
from ..review import jq_raw, steps_file
from ..store import event, get, pair_file, pause_notice, paused_reason, progress_path, steer_notice, unit_name


def record_again(file, full, resolves):
    """True when the steps file has a row for this commit; its resolves column then gains the given note ids (sorted by number, once each)."""
    try:
        with open(file, 'rb') as f:
            rows = f.read().decode('utf-8', 'surrogateescape').split('\n')
    except OSError:
        return False  # the count below says why it cannot be read, as it always did
    found = False
    for n, row in enumerate(rows):
        cols = row.split('\t')
        if len(cols) > 3 and cols[1] == full:
            found = True
            ids = {i for i in cols[3].split(',') if i} | {i for i in resolves.split(',') if i}
            cols[3] = ','.join(sorted(ids, key=lambda i: (i.rpartition('-')[0], int(i.rpartition('n')[2]))))
            rows[n] = '\t'.join(cols)
            break
    if found and resolves:
        with open(f'{file}.tmp', 'wb') as f:
            f.write('\n'.join(rows).encode('utf-8', 'surrogateescape'))
        os.replace(f'{file}.tmp', file)
    return found


def step(ctx, args, now=None):
    """The sidekick records a committed step and goes straight on; the master's wait picks it up for review. --resolves names the
    notes the step fixes."""
    if len(args) < 3:
        raise Die('usage: pair.sh step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]')
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
    r = subprocess.run(['git', '-C', jq_raw(get(store, 'cwd')), 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}'], capture_output=True, check=False)
    sys.stderr.write(r.stderr.decode('utf-8', 'surrogateescape'))
    if r.returncode != 0:
        raise Die(f'not a commit: {sha}; commit the step first')
    full = r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')
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
    file = steps_file(store, brief)
    if os.path.isfile(file) and record_again(file, full, resolves):
        # this unit already recorded this commit: it only gains the notes it now resolves, and nothing else is written
        sys.stdout.write(f'step {full[:9]}: already recorded\n')
        sys.stdout.write(steer_notice(store))
        return 0
    k = 1
    if os.path.isfile(file):
        # `[ -f file ] && k=$(( $(grep -c . file) + 1 ))`: the assignment takes grep's status, and as the last command of an AND list a
        # failure ends the command under set -e: 1 when the file has no non-empty line, 2 when it cannot be read.
        try:
            with open(file, 'rb') as f:
                count = sum(1 for line in f.read().split(b'\n') if line)
        except OSError as e:
            sys.stderr.write(f'grep: {file}: {os.strerror(e.errno)}\n')
            raise Exit(2)
        if count == 0:
            raise Exit(1)
        k = count + 1
    stamp = time.time() if now is None else now
    summary_cell = summary.replace('\t', ' ').replace('\n', ' ')
    with open(file, 'ab') as f:
        f.write(f'{k}\t{full}\t{int(stamp)}\t{resolves}\t{summary_cell}\n'.encode('utf-8', 'surrogateescape'))
    with open(progress_path(store), 'ab') as f:
        f.write(f'{time.strftime("%H:%M", time.localtime(stamp))} step {k} {full[:9]}: {summary}\n'.encode('utf-8', 'surrogateescape'))
    event(store, 'sidekick', 'step', brief, f'{k}:{full[:9]}')
    if paused_reason(store) is not None:
        sys.stdout.write(f'step {k} recorded at {full[:9]}.\n')
        sys.stdout.write(pause_notice(store))
    else:
        sys.stdout.write(f'step {k} recorded at {full[:9]}; go on with the next step. At the next step boundary: pair.sh notes {store}\n')
    sys.stdout.write(steer_notice(store))
    return 0
