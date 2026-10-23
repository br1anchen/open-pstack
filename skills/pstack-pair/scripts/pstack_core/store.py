import glob
import json
import os
import re
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from .errors import BadSlug, DamagedStore, LockHeld, MissingStore
from .numbers import env_float, env_int

# What subprocess.run takes to give str output: UTF-8, a byte that is not UTF-8 shown as U+FFFD.
TEXT = {'text': True, 'encoding': 'utf-8', 'errors': 'replace'}


def newest_first(paths):
    """The paths by modification time, newest first; two that share a time go by name without its extension (003-x.md before 003-x-s1.md)."""
    return sorted(
        paths,
        key=lambda p: (
            -os.lstat(p).st_mtime_ns,
            os.path.splitext(os.path.basename(p))[0],
        ),
    )


def newest(paths):
    """The most recently modified path, or None when there are none."""
    return next(iter(newest_first(paths)), None)


def last_line(path, block=4096):
    """The last line of a file as tail -n 1 prints it: the text after the last newline, or the line before it when that is empty.
    Read back from the end a block at a time, so a long events.tsv costs one block."""
    with open(path, 'rb') as f:
        size = f.seek(0, os.SEEK_END)
        pos, data = size, b''
        while pos > 0 and data.count(b'\n') < 2:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.decode('utf-8', errors='replace').split('\n')
    return lines[-1] if lines[-1] else (lines[-2] if len(lines) > 1 else '')


def event(store, actor, name, unit, detail='', dedupe=False):
    """One events.tsv row. Never fails the command it records.

    The file has no header: a store's first row is data.

    With dedupe a master wake row identical to the last row in epoch, actor,
    event, unit and detail is not written twice."""
    log = f'{store}/events.tsv'
    unit = os.path.basename(unit or '-')
    if unit.endswith('.md') and unit != '.md':
        unit = unit[:-3]
    now = time.time()
    epoch = int(now)
    row = f'{epoch}\t{actor}\t{name}\t{unit}\t{detail}'
    try:
        if dedupe and actor == 'master' and name == 'wake':
            try:
                if last_line(log).split('\t', 1)[-1] == row:
                    return
            except OSError:
                pass
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))
        text = f'{stamp}\t{row}\n'
        with open(log, 'a', encoding='utf-8') as f:
            f.write(text)
    except OSError:
        pass


def wake(ctx, store, unit, detail):
    """The row for a master wake, written once when the variant dedupes them (the kitchen's event_dedupe)."""
    event(store, 'master', 'wake', unit, detail, dedupe=ctx.event_dedupe)


def header_field(path, key):
    """The value of the first "key: value" line, or empty."""
    prefix = f'{key}:'
    try:
        for line in read_text(path).split('\n'):
            if line.startswith(prefix):
                return line[len(prefix) :].lstrip(' \t\n\r\f\v')
    except OSError:
        pass
    return ''


def read_text(path):
    with open(path, encoding='utf-8', errors='replace', newline='') as f:
        return f.read()


def write_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def now_s():
    return int(time.time())


def basename(path):
    return os.path.basename(path.rstrip('/')) or '/'


def risk_of(brief):
    """A brief's or plan's risk class: the first word of its risk: header, less a colon after it, which a master may follow with the reason
    (`escalated (contract change)` or `escalated: contract change`)."""
    words = [w for w in re.split(r'[ \t\n]+', header_field(brief, 'risk')) if w]
    return words[0].removesuffix(':') if words else ''


def pair_file(store):
    path = f'{store}/pair.json'
    if not os.path.isfile(path):
        raise MissingStore(store)
    return path


def load(store):
    """pair.json, the read side of every field. Writes go through update() in this module, under the .pair.lock.
    A file that does not parse, or is not an object, is one message: pair.json is DamagedStore (plan 043 decision b)."""
    try:
        doc = json.loads(read_text(pair_file(store)))
    except ValueError as e:
        raise DamagedStore(e) from None
    if not isinstance(doc, dict):
        raise DamagedStore(f'expected an object, found {json_type(doc)}')
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


def who(role, name):
    """The role and its agent's name for a message, the role alone when the store records no name."""
    return f'{role} {name}' if name else role


def peek(doc, key):
    """The object at doc[key], {} when it is null or absent and left out of doc; any other type is damage."""
    value = doc.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DamagedStore(f'{key} is {json_type(value)}, not an object')
    return value


def names(doc, key):
    """The array of strings at doc[key], [] when it is null, absent or false; any other type, or an item that is no string, is damage."""
    items = listing(doc, key)
    for item in items:
        if not isinstance(item, str):
            raise DamagedStore(f'{key} holds {json_type(item)}, not a string')
    return items


def count(doc, key):
    """The integer at doc[key], 0 when it is null, absent or false; any other type is damage."""
    value = doc.get(key)
    if value is None or value is False:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise DamagedStore(f'{key} is {json_type(value)}, not an integer')
    return value


def member(doc, key):
    """The object at doc[key], created when it is null or absent; any other type is damage."""
    value = doc.get(key)
    if value is None:
        value = doc[key] = {}
    if not isinstance(value, dict):
        raise DamagedStore(f'{key} is {json_type(value)}, not an object')
    return value


def listing(doc, key):
    """The array at doc[key], [] when it is null, absent or false; any other type is damage."""
    value = doc.get(key)
    if value is None or value is False:
        return []
    if not isinstance(value, list):
        raise DamagedStore(f'{key} is {json_type(value)}, not an array')
    return value


def text_at(doc, key, default=''):
    """The string at doc[key], default when it is null, absent or false; a number is its digits; any other type is damage."""
    value = doc.get(key)
    if value is None or value is False:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DamagedStore(f'{key} is {json_type(value)}, not a string')
    return str(value)


def get(store, path, default=None):
    """The value at a dotted path ('sidekick.kind') in pair.json; default when a step is absent or not an object."""
    value = load(store)
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def walk(store, path):
    """(the object holding the last key of a dotted path in pair.json, that key): the holder is {} when a step is null or absent, and a step
    over any other value that is no object is damage."""
    *steps, last = path.split('.')
    value, held = load(store), None
    for key in steps:
        if value is None:
            return {}, last
        if not isinstance(value, dict):
            raise DamagedStore(f'{held} is {json_type(value)}, not an object')
        value, held = value.get(key), key
    if value is None:
        return {}, last
    if not isinstance(value, dict):
        raise DamagedStore(f'{held} is {json_type(value)}, not an object')
    return value, last


def read(store, path, default=''):
    """The text at a dotted path in pair.json, default when it is null or absent (see text)."""
    holder, key = walk(store, path)
    return text_at(holder, key, default)


def read_int(store, path):
    """The whole number at a dotted path in pair.json: None when absent or null, damage when it is any other type."""
    holder, key = walk(store, path)
    value = holder.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise DamagedStore(f'{path} is {json_type(value)}, not a whole number')
    return value


def read_flag(store, path):
    """The value at a dotted path in pair.json for an `is True` or `is False` test: None when it is absent or null."""
    holder, key = walk(store, path)
    return holder.get(key)


def field(store, role, key, default=''):
    """The text of a role's key (see text); a role that is no object is damage."""
    return text_at(peek(load(store), role), key, default)


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def dispatched_brief(store):
    """The brief path recorded at dispatch, or ''."""
    return field(store, 'dispatch', 'brief')


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
    return (
        f'PAUSE: {reason}. This is your safe point: commit what is verified, write the report as partial with where '
        'you stopped and the next step under Deviations, run pair.sh finish, and end the turn.\n'
    )


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
    """The digits of a trailing -<letter><digits> in name, the letter one of letters, or None."""
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
    for f in names(load(store), 'notice_steers'):
        name = os.path.basename(f)
        if not os.path.isfile(f) or not re.fullmatch(
            re.escape(unit) + r'-s[0-9].*\.md', name, re.S
        ):
            continue
        k = steer_number(f)
        if os.path.isfile(prog) and re.search(
            f'steer s{re.escape(k)} (applied|withdrawn|late)', read_text(prog)
        ):
            continue
        if os.path.isfile(f'{store}/reports/{unit}-s{k}.md'):
            continue
        found.append(f)
    return found


def note_notice(ctx, store):
    """The blocking notes parked for a Devin sidekick (.notice_notes), once: each is printed and taken off the list."""
    parked = [f for f in names(load(store), 'notice_notes') if os.path.isfile(f)]
    if not parked:
        return ''
    update(store, lambda doc: doc.pop('notice_notes', None))
    return ''.join(
        f'NOTE: read {f} now, before your next tool call. Fix each blocking item first, as a fixup commit recorded with '
        f'{ctx.prog} step {store} <sha> <summary> --resolves <n-ids>.\n'
        for f in parked
    )


def steer_notice(ctx, store):
    return note_notice(ctx, store) + ''.join(
        f'STEER: read {f} now, before your next tool call. Agree: apply it and run pair.sh progress {store} '
        f'"steer s{steer_number(f)} applied: <what changed>". Object: write the steer response and run pair.sh finish on it.\n'
        for f in open_steers(store)
    )


def fill_template(path, values, braced=True):
    """The template's text with each token replaced by its value, literally and in the order given. A token is {{NAME}}; with braced False the
    key is the text to replace as it stands."""
    text = read_text(path)
    for token, value in values.items():
        text = text.replace('{{' + token + '}}' if braced else token, value)
    return text


def new_from_template(ctx, store, slug, directory, template):
    pair_file(store)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', slug):
        raise BadSlug(
            f'{directory.removesuffix("s")} slug must be lowercase [a-z0-9_-]'
        )
    seq = next_seq(store)
    path = f'{store}/{directory}/{seq}-{slug}.md'
    write_text(
        path,
        fill_template(
            f'{ctx.skill_root}/references/{template}',
            {'SEQ': seq, 'SLUG': slug, 'STORE': store},
        ),
    )
    return path


def _dumps(doc):
    """The text jq writes for doc. Python's own output matches for every string and number a store holds
    except DEL, which jq escapes, and the spellings of numbers no writer produces (see the tests)."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + '\n').replace(
        '\x7f', '\\u007f'
    )


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
                raise LockHeld(store)
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


@dataclass(slots=True, eq=False)
class Context:
    """What a variant's class attributes configure: skill, state root, labels, roles, store directories, slug limit, unfilled pattern, the name
    its messages carry (pair.sh, kitchen.sh) and whether it writes a repeated master wake once."""

    skill: str
    skill_root: str
    state_root: str
    store_label: str
    roles: tuple
    store_dirs: tuple
    slug_max: int
    unfilled_re: str
    prog: str = 'pair.sh'
    event_dedupe: bool = False
    _variant: object = dataclass_field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def variant(self):
        """The skill's Variant, loaded on first use so a command that needs no hook pays for none."""
        if self._variant is None:
            from .variant import load_variant

            self._variant = load_variant(self)
        return self._variant
