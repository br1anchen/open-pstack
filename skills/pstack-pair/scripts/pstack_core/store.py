import glob
import json
import os
import re
import time

from .errors import Die


def last_line(path):
    """The last line of a file as tail -n 1 prints it, read from the end."""
    with open(path, 'rb') as f:
        size = f.seek(0, os.SEEK_END)
        data, pos = b'', size
        while pos > 0 and data.count(b'\n') < 2:
            pos = max(0, pos - 4096)
            f.seek(pos)
            data = f.read(size - pos)
    lines = data.decode('utf-8', 'surrogateescape').split('\n')
    return lines[-1] if lines[-1] else (lines[-2] if len(lines) > 1 else '')


def event(store, actor, name, unit, detail=''):
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
                if last_line(log).split('\t', 1)[-1] == row:
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


def read_text(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'surrogateescape')


def write_text(path, text):
    with open(path, 'wb') as f:
        f.write(text.encode('utf-8', 'surrogateescape'))


def pair_file(store):
    path = f'{store}/pair.json'
    if not os.path.isfile(path):
        raise Die(f'no pair.json in {store}; run: pair.sh init <slug>')
    return path


def pair_json(store):
    """pair.json, the read side of field(). A writer stays in bash for now."""
    with open(pair_file(store), 'rb') as f:
        return json.load(f)


def alt(value, default=''):
    """jq's "value // default": null and false both count as missing."""
    return default if value is None or value is False else value


def dispatched_brief(store):
    """field "$store" '.dispatch.brief // empty'"""
    dispatch = alt(pair_json(store).get('dispatch'), {})
    brief = alt(dispatch.get('brief') if isinstance(dispatch, dict) else None, '')
    return brief if isinstance(brief, str) else json.dumps(brief)


def next_seq(store):
    top = 0
    for d in ('briefs', 'plans', 'reports'):
        for f in glob.glob(os.path.join(glob.escape(store), d, '[0-9][0-9][0-9]-*.md')):
            top = max(top, int(os.path.basename(f)[:3]))
    return f'{top + 1:03d}'


def paused_reason(store):
    """The first line of the paused file, or None when the store is not paused."""
    path = f'{store}/paused'
    if not os.path.isfile(path):
        return None
    return read_text(path).split('\n', 1)[0]


def pause_notice(store):
    reason = paused_reason(store)
    if reason is None:
        return ''
    return (f'PAUSE: {reason}. This is your safe point: commit what is verified, write the report as partial with where '
            'you stopped and the next step under Deviations, run pair.sh finish, and end the turn.\n')


def progress_path(store):
    brief = dispatched_brief(store)
    if not brief:
        return None
    return f'{store}/progress/{unit_name(brief)}.md'


def unit_name(path):
    name = os.path.basename(path)
    return name[:-3] if name.endswith('.md') and name != '.md' else name


def steer_number(path):
    m = re.fullmatch(r'.*-s([0-9]+)', unit_name(path), re.S)
    return m.group(1) if m else unit_name(path)


def open_steers(store):
    brief = dispatched_brief(store)
    if not brief:
        return []
    unit = unit_name(brief)
    prog = f'{store}/progress/{unit}.md'
    found = []
    for f in alt(pair_json(store).get('notice_steers'), []):
        name = os.path.basename(f)
        if not os.path.isfile(f) or not re.fullmatch(re.escape(unit) + r'-s[0-9].*\.md', name, re.S):
            continue
        k = steer_number(f)
        if os.path.isfile(prog) and re.search(f'steer s{k} (applied|withdrawn|late)', read_text(prog)):
            continue
        if os.path.isfile(f'{store}/reports/{unit}-s{k}.md'):
            continue
        found.append(f)
    return found


def steer_notice(store):
    return ''.join(
        f'STEER: read {f} now, before your next tool call. Agree: apply it and run pair.sh progress {store} '
        f'"steer s{steer_number(f)} applied: <what changed>". Object: write the steer response and run pair.sh finish on it.\n'
        for f in open_steers(store))


TEMPLATE_TOKENS = ('SEQ', 'SLUG', 'K', 'STORE', 'SUPERSEDES')


def fill_template(path, values):
    """Literal token replacement in order, where the bash used sed -e "s|{{X}}|$x|g"."""
    text = read_text(path)
    for token in TEMPLATE_TOKENS:
        if token in values:
            text = text.replace('{{' + token + '}}', values[token])
    return text


def new_from_template(ctx, store, slug, directory, template):
    pair_file(store)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', slug):
        raise Die(f'{directory.removesuffix("s")} slug must be lowercase [a-z0-9_-]')
    seq = next_seq(store)
    path = f'{store}/{directory}/{seq}-{slug}.md'
    write_text(path, fill_template(f'{ctx.skill_root}/references/{template}', {'SEQ': seq, 'SLUG': slug, 'STORE': store}))
    return path
