import glob
import json
import os
import re
import time

from .errors import Die


def newest_first(paths):
    """The paths by modification time, newest first; two that share a time go by name without its extension (003-x.md before 003-x-s1.md)."""
    return sorted(paths, key=lambda p: (-os.lstat(p).st_mtime_ns, os.path.splitext(os.path.basename(p))[0]))


def newest(paths):
    """The most recently modified path, or None when there are none."""
    return next(iter(newest_first(paths)), None)


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


# Whether a master wake row identical to the last row is written once: the variant's event_dedupe, set by cli.main (the kitchen's).
dedupe_wakes = False


def event(store, actor, name, unit, detail=''):
    """One events.tsv row, as the bash event() did. Never fails the command it records.

    The bash writes no header: its redirect creates the file before the "[ -f ]" test, so
    the header branch never runs. A store's first row is data.

    With dedupe_wakes a master wake row identical to the last row in epoch, actor,
    event, unit and detail is not written twice."""
    log = f'{store}/events.tsv'
    unit = os.path.basename(unit or '-')
    if unit.endswith('.md') and unit != '.md':
        unit = unit[:-3]
    now = time.time()
    epoch = int(now)
    row = f'{epoch}\t{actor}\t{name}\t{unit}\t{detail}'
    try:
        if dedupe_wakes and actor == 'master' and name == 'wake':
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
    """The value of the first "key: value" line, or empty, as the bash header_field() did."""
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


def load(store):
    """pair.json, the read side of every field. Writes go through update() in this module, under the same .pair.lock as bash json_update.
    A file that does not parse, or is not an object, is one message: pair.json is damaged (plan 043 decision b)."""
    with open(pair_file(store), 'rb') as f:
        try:
            doc = json.load(f)
        except ValueError as e:
            raise Die(f'pair.json is damaged: {e}', 1)
    if not isinstance(doc, dict):
        raise damaged(f'expected an object, found {json_type(doc)}')
    return doc


def json_type(value):
    """The JSON name of a value's type: null, boolean, number, string, array or object."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    return 'array' if isinstance(value, list) else 'object'


def damaged(what):
    """The one error for a pair.json value of a type the code cannot read (plan 043 decision b)."""
    return Die(f'pair.json is damaged: {what}', 1)


def peek(doc, key):
    """The object at doc[key], {} when it is null or absent and left out of doc; any other type is damage."""
    value = doc.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise damaged(f'{key} is {json_type(value)}, not an object')
    return value


def names(doc, key):
    """The array of strings at doc[key], [] when it is null, absent or false; any other type, or an item that is no string, is damage."""
    items = listing(doc, key)
    for item in items:
        if not isinstance(item, str):
            raise damaged(f'{key} holds {json_type(item)}, not a string')
    return items


def count(doc, key):
    """The integer at doc[key], 0 when it is null, absent or false; any other type is damage."""
    value = alt(doc.get(key), 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise damaged(f'{key} is {json_type(value)}, not an integer')
    return value


def member(doc, key):
    """The object at doc[key], created when it is null or absent; any other type is damage."""
    value = doc.get(key)
    if value is None:
        value = doc[key] = {}
    if not isinstance(value, dict):
        raise damaged(f'{key} is {json_type(value)}, not an object')
    return value


def listing(doc, key):
    """The array at doc[key], [] when it is null, absent or false; any other type is damage."""
    value = alt(doc.get(key), [])
    if not isinstance(value, list):
        raise damaged(f'{key} is {json_type(value)}, not an array')
    return value


def get(store, path, default=None):
    """The value at a dotted path ('sidekick.kind') in pair.json; default when a step is absent or not an object."""
    value = load(store)
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def read(store, path, empty=False, default=None):
    """jq -r '.a.b' on pair.json, as a command substitution gives it: a scalar raw, an object or array as indented JSON. A step over a null gives
    null; a step over any other value that is no object is damage. With empty the filter is '.a.b // empty'; with default, '.a.b // default'."""
    from .wait import as_text
    value, held = load(store), None
    for key in path.split('.'):
        if value is None:
            break
        if not isinstance(value, dict):
            raise damaged(f'{held} is {json_type(value)}, not an object')
        value, held = value.get(key), key
    if default is not None:
        value = alt(value, default)
    elif empty:
        value = alt(value, '')
    return as_text(value)


def read_int(store, path):
    """The whole number at a dotted path in pair.json: None when absent or null, damage when it is any other type."""
    value, held = load(store), None
    for key in path.split('.'):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise damaged(f'{held} is {json_type(value)}, not an object')
        value, held = value.get(key), key
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise damaged(f'{path} is {json_type(value)}, not a whole number')
    return value


def field(store, role, key, empty=False, default=None):
    """jq -r '.role.key': a role that is not an object is damage. With empty, the filter is '.role.key // empty'; with default,
    '.role.key // "<default>"'."""
    value = load(store).get(role)
    if value is not None and not isinstance(value, dict):
        raise damaged(f'{role} is {json_type(value)}, not an object')
    found = (value or {}).get(key)
    if default is not None:
        return jq_raw(alt(found, default))
    return jq_raw(alt(found, '')) if empty else jq_raw(found)


def text_of(raw, strip=False):
    """Bytes as the str a command substitution gives; strip drops the trailing newlines."""
    text = raw.decode('utf-8', 'surrogateescape')
    return text.rstrip('\n') if strip else text


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def whole_number(text, flag, unit):
    """A CLI number: ASCII digits only, so a sign, a fraction or an expression is a usage error."""
    if not (text.isascii() and text.isdigit()):
        raise Die(f'{flag} must be a whole number of {unit}', 1)
    return int(text)


def int_or_none(text):
    """The integer a line of text holds (ASCII digits), or None."""
    text = text.strip()
    return int(text) if text.isascii() and text.isdigit() else None


def env_int(name, default):
    """${NAME:-default} as a whole number; anything else is a usage error."""
    text = os.environ.get(name) or str(default)
    if not (text.isascii() and text.isdigit()):
        raise Die(f'{name} must be a whole number', 1)
    return int(text)


def env_float(name, default):
    """${NAME:-default} as a number of seconds, fractions allowed; anything else is a usage error."""
    text = os.environ.get(name) or str(default)
    if not re.fullmatch(r'[0-9]+(\.[0-9]*)?|\.[0-9]+', text):
        raise Die(f'{name} must be a number', 1)
    return float(text)


def alt(value, default=''):
    """jq's "value // default": null and false both count as missing."""
    return default if value is None or value is False else value


def jq_raw(value):
    """jq -r of a value: null, true and false print as words."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return value if isinstance(value, str) else str(value)


def dispatched_brief(store):
    """field "$store" '.dispatch.brief // empty'"""
    dispatch = alt(get(store, 'dispatch'), {})
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


def suffix_number(name, letters):
    """The digits of a trailing -<letter><digits> in name, the letter one of letters (bash ${name##*-n}), or None."""
    m = re.fullmatch(rf'.*-[{letters}]([0-9]+)', name, re.S)
    return m.group(1) if m else None


def note_id(path):
    """n<k> for a note file, else its unit name unchanged."""
    k = suffix_number(unit_name(path), 'n')
    return f'n{k}' if k else unit_name(path)


def steer_number(path):
    return suffix_number(unit_name(path), 's') or unit_name(path)


def open_steers(store):
    brief = dispatched_brief(store)
    if not brief:
        return []
    unit = unit_name(brief)
    prog = f'{store}/progress/{unit}.md'
    found = []
    for f in alt(get(store, 'notice_steers'), []):
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


def note_notice(store):
    """The blocking notes parked for a Devin sidekick (.notice_notes), once: each is printed and taken off the list."""
    from . import cli
    parked = [f for f in alt(get(store, 'notice_notes'), []) if os.path.isfile(f)]
    if not parked:
        return ''
    update(store, lambda doc: doc.pop('notice_notes', None))
    return ''.join(
        f'NOTE: read {f} now, before your next tool call. Fix each blocking item first, as a fixup commit recorded with '
        f'{cli.PROG} step {store} <sha> <summary> --resolves <n-ids>.\n' for f in parked)


def steer_notice(store):
    return note_notice(store) + ''.join(
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


def acquire_lock(store):
    """The mkdir lock on pair.json, waited for up to 10 s (PAIR_LOCK_TRIES polls of PAIR_LOCK_POLL_S): its path. The caller removes it."""
    lock = f'{store}/.pair.lock'
    tries = env_int('PAIR_LOCK_TRIES', 100)
    poll = env_float('PAIR_LOCK_POLL_S', 0.1)
    i = 0
    while True:
        try:
            os.mkdir(lock)
            return lock
        except OSError:
            i += 1
            if i >= tries:
                raise Die(f'pair.json lock held for 10s; remove {store}/.pair.lock if no pair.sh is running')
            time.sleep(poll)


def update(store, mutate):
    """json_update: pair.json has two writers, so each write holds the mkdir lock, parses the file as it
    is once the lock is held, lets mutate(doc) change that document in place, and replaces the file
    atomically with the bytes jq would write. A mutate reads values through member, peek, listing, names and count, which raise the damage
    for a value of the wrong type; any other exception is the mutate's own bug and propagates. A mutate that raises, or a pair.json that does not parse,
    leaves pair.json and pair.json.tmp as they were and frees the lock. PAIR_LOCK_TRIES and
    PAIR_LOCK_POLL_S exist so tests can shorten the 10 s wait."""
    lock = acquire_lock(store)
    try:
        doc = load(store)
        mutate(doc)
        text = _dumps(doc)
        with open(f'{store}/pair.json.tmp', 'w', encoding='utf-8') as out:
            out.write(text)
        os.replace(f'{store}/pair.json.tmp', f'{store}/pair.json')
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass
