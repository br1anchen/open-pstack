import os
import time


def event(store, actor, name, unit, detail):
    """One events.tsv row, as event() in pair-core.sh. Never fails the command it records.

    The bash writes no header: its redirect creates the file before the "[ -f ]" test, so
    the header branch never runs. A store's first row is data.

    With PAIR_EVENT_DEDUPE=1 a master wake row identical to the last row in epoch, actor,
    event, unit and detail is not written twice."""
    log = f'{store}/events.tsv'
    unit = os.path.basename(unit or '-')
    if unit.endswith('.md') and unit != '.md':
        unit = unit[:-3]
    now = time.time()
    epoch = int(now)
    row = f'{epoch}\t{actor}\t{name}\t{unit}\t{detail}'
    try:
        if os.environ.get('PAIR_EVENT_DEDUPE') == '1' and actor == 'master' and name == 'wake':
            try:
                with open(log, 'rb') as f:
                    lines = f.read().decode('utf-8', 'surrogateescape').split('\n')
                last = lines[-1] if lines[-1] else (lines[-2] if len(lines) > 1 else '')
                if last.split('\t', 1)[-1] == row:
                    return
            except OSError:
                pass
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))
        text = f'{stamp}\t{row}\n'
        with open(log, 'ab') as f:
            f.write(text.encode('utf-8', 'surrogateescape'))
    except OSError:
        pass


def header_field(path, key):
    """The value of the first "key: value" line, or empty, as header_field() in pair-core.sh."""
    prefix = f'{key}:'
    try:
        with open(path, 'rb') as f:
            for line in f.read().decode('utf-8', 'surrogateescape').split('\n'):
                if line.startswith(prefix):
                    return line[len(prefix):].lstrip(' \t\n\r\f\v')
    except OSError:
        pass
    return ''
