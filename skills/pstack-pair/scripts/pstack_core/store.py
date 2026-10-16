import glob
import json
import os
import re
import stat
import time

from .errors import Die, Exit


def utf8_locale():
    """Whether the locale (LC_ALL, then LC_CTYPE, then LANG) is UTF-8, so grep, cut and awk count characters."""
    for name in ('LC_ALL', 'LC_CTYPE', 'LANG'):
        value = os.environ.get(name)
        if value:
            return bool(re.search(r'utf-?8', value, re.IGNORECASE))
    return False


def cut_chars(text, last):
    """cut -c1-N: characters under a UTF-8 locale, bytes otherwise."""
    if utf8_locale():
        return text[:last]
    return text.encode('utf-8', 'surrogateescape')[:last].decode('utf-8', 'surrogateescape')


def readlink_f(path):
    """readlink -f, where a failure ends the command with 1 and no message (set -e on an assignment). Every
    component but the last must exist and be a directory; a trailing slash needs a directory, except after a
    missing last component; more than 40 symlinks, or a loop, fail."""
    if path == '':
        raise Exit(1)
    rest, links = path, 0
    parts = [] if path.startswith('/') else [p for p in os.getcwd().split('/') if p]
    while True:
        rest = rest.lstrip('/')
        if rest == '':
            break
        name, sep, tail = rest.partition('/')
        rest = sep + tail
        if name in ('', '.'):
            continue
        if name == '..':
            if parts:
                parts.pop()
            continue
        here = '/' + '/'.join([*parts, name])
        try:
            mode = os.lstat(here).st_mode
        except FileNotFoundError:
            if rest.strip('/') == '':
                parts.append(name)
                break
            raise Exit(1)
        except OSError:
            raise Exit(1)
        if stat.S_ISLNK(mode):
            links += 1
            if links > 40:
                raise Exit(1)
            target = os.readlink(here)
            if target.startswith('/'):
                parts = []
            rest = target + rest
            continue
        if rest.startswith('/') and not stat.S_ISDIR(mode):
            raise Exit(1)
        parts.append(name)
    return '/' + '/'.join(parts)


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
    """pair.json, the read side of field(). Writes go through update() in this module, under the same .pair.lock as bash json_update."""
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
            if os.path.exists(f):
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


def expected_report(store, brief):
    """The report a message asks for: reports/<NNN-slug>.md, where an answer's -a<k> suffix names the brief it answers."""
    return f'{store}/reports/{re.sub(r"-a[0-9]+$", "", unit_name(brief))}.md'


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
        if os.path.isfile(prog) and re.search(f'steer s{re.escape(k)} (applied|withdrawn|late)', read_text(prog)):
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


def fill_template(path, values):
    """Literal token replacement in the order given, where the bash used sed -e "s|{{X}}|$x|g"."""
    text = read_text(path)
    for token, value in values.items():
        text = text.replace('{{' + token + '}}', value)
    return text


def new_from_template(ctx, store, slug, directory, template):
    pair_file(store)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', slug):
        raise Die(f'{directory.removesuffix("s")} slug must be lowercase [a-z0-9_-]')
    seq = next_seq(store)
    path = f'{store}/{directory}/{seq}-{slug}.md'
    write_text(path, fill_template(f'{ctx.skill_root}/references/{template}', {'SEQ': seq, 'SLUG': slug, 'STORE': store}))
    return path


def _dumps(doc):
    """The text jq writes for doc. Python's own output matches for every string and number a store holds
    except DEL, which jq escapes, and the spellings of numbers no writer produces (see the tests)."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + '\n').replace('\x7f', '\\u007f')


def update(store, mutate):
    """json_update: pair.json has two writers, so each write holds the mkdir lock, parses the file as it
    is once the lock is held, lets mutate(doc) change that document in place, and replaces the file
    atomically with the bytes jq would write. A mutate that raises, or a pair.json that does not parse,
    leaves pair.json and pair.json.tmp as they were and frees the lock. PAIR_LOCK_TRIES and
    PAIR_LOCK_POLL_S exist so tests can shorten the 10 s wait."""
    lock = f'{store}/.pair.lock'
    tries = int(os.environ.get('PAIR_LOCK_TRIES', '100'))
    poll = float(os.environ.get('PAIR_LOCK_POLL_S', '0.1'))
    i = 0
    while True:
        try:
            os.mkdir(lock)
            break
        except OSError:
            i += 1
            if i >= tries:
                raise Die(f'pair.json lock held for 10s; remove {store}/.pair.lock if no pair.sh is running')
            time.sleep(poll)
    try:
        with open(f'{store}/pair.json', encoding='utf-8') as f:
            doc = json.load(f)
        mutate(doc)
        with open(f'{store}/pair.json.tmp', 'w', encoding='utf-8') as out:
            out.write(_dumps(doc))
        os.replace(f'{store}/pair.json.tmp', f'{store}/pair.json')
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass
