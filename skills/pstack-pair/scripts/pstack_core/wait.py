"""wait, as the wait closure in pair-core.sh ran it before phase 3: the option parser, the loop that waits for the sidekick's reply,
and finish_wait, which prints the outcome. WaitResult carries what the bash passed in the globals code, out, err,
ready and steps_due. send_and_wait, stop and steer are ported (pstack_core/send.py, commands/stop.py, commands/steer.py)."""
import json
import os
import signal
import subprocess
import sys
import time

from .checkin import checkin
from .checkin import sidekick_activity as _sidekick_activity
from .errors import Die, Exit
from .herdr import agent_status, pane_busy
from .providers import provider_hint
from .replies import (dispatched, fresh_reply, latest_report, mark_seen, pending_report, queue_note, review_delta, seen_reply,
                      unreviewed_steps, withheld_report, file_mtime)
from .review import jq_raw, review_base
from .store import acquire_lock, alt, cut_chars, env_float, env_int, event, get, header_field, member, paused_reason, unit_name, update, whole_number


class WaitResult:
    __slots__ = ('code', 'out', 'err', 'ready', 'steps_due', 'superseded')

    def __init__(self, code=0, out='', err='', ready='', steps_due=False, superseded=False):
        self.superseded = superseded
        self.code = code
        self.out = out
        self.err = err
        self.ready = ready
        self.steps_due = steps_due


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def wait_opts(args, timeout=540000):
    """wait_opts(): (timeout in milliseconds, check-in interval in minutes, send_only, leftover args). --timeout takes
    milliseconds and --every minutes, both whole numbers."""
    rest, send_only, i = [], False, 0
    while i < len(args):
        arg = args[i]
        if arg == '--timeout':
            if i + 1 >= len(args):
                raise Die('--timeout needs a value')
            timeout, i = whole_number(args[i + 1], '--timeout', 'milliseconds'), i + 2
        elif arg == '--every':
            if i + 1 >= len(args):
                raise Die('--every needs a value')
            timeout, i = whole_number(args[i + 1], '--every', 'minutes') * 60000, i + 2
        elif arg == '--send-only':
            send_only, i = True, i + 1
        else:
            rest.append(arg)
            i += 1
    return timeout, tdiv(timeout, 60000), send_only, rest


def reply_mode(result):
    return 'steps' if not result.ready and result.steps_due else 'reply'


def now_s():
    return int(time.time())


def json_values(text):
    """(values, ok): the JSON values jq reads from text, and whether it reached the end without a parse error."""
    decoder, pos, n, values = json.JSONDecoder(), 0, len(text), []
    while True:
        while pos < n and text[pos] in ' \t\n\r':
            pos += 1
        if pos >= n:
            return values, True
        try:
            value, pos = decoder.raw_decode(text, pos)
        except ValueError:
            return values, False
        values.append(value)


def as_text(value):
    """A value as a command substitution of `jq -r` gives it: a string raw, null, true and false as words, an object or array as indented JSON."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False).replace('\x7f', '\\u007f')
    return jq_raw(value)


def truthy(value):
    return value is not None and value is not False


def read_path(value, *keys):
    """The value at keys in parsed herdr JSON: (found, readable). A null on the way gives null; a step over anything but an object or null is unreadable."""
    for key in keys:
        if value is None:
            return None, True
        if not isinstance(value, dict):
            return None, False
        value = value.get(key)
    return value, True


def reply_at(reply, *keys, quiet=False):
    """The value at keys in the first JSON value of a herdr or daemon reply, None when it has none. A reply that is not JSON, or whose path steps
    over a value that is no object, is one stderr line saying so (quiet leaves it out), and None."""
    values, ok = json_values(reply)
    problem = '' if ok and values else 'is not JSON'
    if not problem:
        found, readable = read_path(values[0], *keys)
        if readable:
            return found
        problem = f'has no object on the way to {".".join(keys)}'
    if not quiet:
        sys.stdout.flush()
        sys.stderr.write(f'herdr reply {problem}\n')
        sys.stderr.flush()
    return None


def printed(lines, failed, default):
    """What a command substitution holds after the lines were printed one each; a failure adds the default."""
    text = ''.join(f'{as_text(line)}\n' for line in lines)
    if failed and default is not None:
        text += default
    return text.rstrip('\n')


def error_code(err):
    """The `.error.code` of each reply herdr printed that holds one (null, false and absent give nothing), one per line."""
    values, _ = json_values(err)
    codes = []
    for value in values:
        code, readable = read_path(value, 'error', 'code')
        if readable and truthy(code):
            codes.append(code)
    return printed(codes, False, None)


def error_state(err):
    """.error.code // .error // "herdr_error" """
    values, ok = json_values(err)
    states = []
    for value in values:
        error, readable = read_path(value, 'error')
        code, code_readable = read_path(value, 'error', 'code')
        if not (readable and code_readable):
            ok = False
        elif truthy(code):
            states.append(code)
        else:
            states.append(error if truthy(error) else 'herdr_error')
    return printed(states, not ok, 'herdr_error')


def agent_state(out, default):
    """.result.agent.agent_status // DEFAULT (empty: no output)"""
    values, ok = json_values(out)
    states = []
    for value in values:
        status, readable = read_path(value, 'result', 'agent', 'agent_status')
        if not readable:
            ok = False
        elif truthy(status):
            states.append(status)
        elif default is not None:
            states.append(default)
    return printed(states, not ok, 'settled' if default == 'settled' else None)


def finish_wait(ctx, store, interval, result, mode, report='', now=None):
    """finish_wait(): print the outcome of a wait and return the exit code the bash exits with."""
    write = sys.stdout.write
    clock = now_s if now is None else (lambda: now)
    from .store import field
    name = lambda: field(store, 'sidekick', 'name')
    if result.code != 0:
        state = error_state(result.err)
        if state == 'timeout':
            state = agent_status(name())
        else:
            sys.stderr.write(f'{result.err}\n')
        write(f'state: {state}\n')
    else:
        state = agent_state(result.out, 'settled')
        write(f'state: {state}\n')
    reason = paused_reason(store)
    if reason is not None:
        write(f'paused: {reason} (start nothing new; record the next step in gates.md and end your turn)\n')
    if not report and mode == 'any':
        report = latest_report(store) or ''
    if report:
        mark_seen(store, report)
        sent = sent_file(store)
        if sent is None:
            # basename '---' reads the word as an option, prints its error and gives nothing to compare.
            sys.stderr.write("basename: unrecognized option '---'\nTry 'basename --help' for more information.\n")
        if sent is not None and cut_chars(os.path.basename(report), 3) == cut_chars(os.path.basename(sent.rstrip('/') or sent), 3):
            try:
                stamp = str(file_mtime(report))
            except OSError:
                sys.stderr.write("stat: cannot read file system information for '%m': No such file or directory\n")
                sys.stderr.write(f"stat: cannot read file system information for '{report}': No such file or directory\n")
                stamp = ''
            seen = f'{report}:{stamp}'
            update(store, lambda doc: set_sent_seen(doc, seen))
        write(f'report: {report}\n')
        write(f'report_status: {status_of(report)}\n')
        event(store, 'master', 'wake', report, f'report:{status_of(report)}')
        write(queue_note(store, report))
        write(review_delta(store, report))
        return 3 if state == 'blocked' else 0
    running = dispatched(store)
    if mode == 'steps':
        rows = unreviewed_steps(ctx, store)
        if rows is None:
            return 1
        first = rows[0].split('\t')[0]
        write(f'steps: {len(rows)} to review on {unit_name(running)}\n')
        for row in rows:
            cols = row.split('\t')
            write(f'  {cols[0]} {cut_chars(cols[1] if len(cols) > 1 else "", 9)} {cols[2] if len(cols) > 2 else ""}\n')
        last = rows[-1].split('\t')
        write(f'range: {cut_chars(review_base(store, running), 9)}..{cut_chars(last[1] if len(last) > 1 else "", 9)}\n')
        write(f'next: review the range, then pair.sh new-note {store} {cut_chars(os.path.basename(running.rstrip("/") or running), 3)}\n')
        try:
            k = json.loads(last[0])
        except ValueError:
            # json_update redirects jq's output to pair.json.tmp before jq refuses the argument, and leaves the empty file.
            open(f'{store}/pair.json.tmp', 'w').close()
            raise Exit(2, 'jq: invalid JSON text passed to --argjson\nUse jq --help for help with command-line options,\n'
                          'or see the jq manpage, or online docs at https://jqlang.org\n')
        update(store, lambda doc: set_shown(doc, unit_name(running), k))
        event(store, 'master', 'wake', running, f'steps:{first}')
        return 0
    withheld = withheld_report(ctx, store)
    activity = sidekick_activity(store, clock())
    shown = seen_reply(store)
    if shown and not withheld and not activity and result.code == 0 and agent_status(name()) != 'working':
        sent = sent_file(store)
        sent = '-' if sent is None else sent
        write(f'idle: the reply to {os.path.basename(sent.rstrip("/") or sent)} was already shown ({shown}); send the next message\n')
        event(store, 'master', 'wake', sent, 'idle')
        # Since notify marks a report it announced, this is the normal path to a report the master already has by message: show what the
        # report path would add. A plan response or advice carries neither.
        record = alt(get(store, 'sent'), {})
        if isinstance(record, dict) and record.get('kind') == 'BRIEF':
            write(queue_note(store, shown))
            write(review_delta(store, shown))
        return 4
    why = ''
    if withheld:
        write(f'report: {withheld[0]} written but not ready: {withheld[1]}\n')
        why = 'report:unready'
    else:
        write('report: missing\n')
    if state == 'blocked':
        event(store, 'master', 'wake', running, why or 'blocked')
        write(hint_for(store))
        return 3
    if agent_status(name()) == 'working' or activity:
        if os.environ.get('PAIR_QUIET_WAIT') != '1':
            event(store, 'master', 'wake', running, why or 'checkin')
        write(checkin(ctx, store, interval, clock()))
    else:
        event(store, 'master', 'wake', running, why or 'missing')
        write(hint_for(store))
    return 4


def sidekick_activity(store, now):
    """checkin.sidekick_activity, whose first read, field .sidekick.pane_id, prints jq's error when `.sidekick` cannot be indexed."""
    from .store import field
    if not isinstance(get(store, 'sidekick'), (dict, type(None))):
        field(store, 'sidekick', 'pane_id', empty=True)
    return _sidekick_activity(store, now)


def hint_for(store):
    """provider_hint, whose first read, field .sidekick.name, ends the command with jq's status when `.sidekick`
    cannot be indexed (a string, an array, a number or a boolean)."""
    from .store import field
    if not isinstance(get(store, 'sidekick'), (dict, type(None))):
        field(store, 'sidekick', 'name')
    return provider_hint(store)


def sent_file(store):
    """`.sent.file` as jq's `// default` leaves it: None when missing, null or false; an empty string stays an empty string."""
    sent = alt(get(store, 'sent'), {})
    value = alt(sent.get('file') if isinstance(sent, dict) else None, None)
    return None if value is None else jq_raw(value)


def status_of(report):
    """header_field "$report" status, whose grep complains on stderr when the file is gone."""
    if not os.path.isfile(report):
        sys.stderr.write(f'grep: {report}: No such file or directory\n')
    return header_field(report, 'status')


def mark_report_seen(store, report):
    """mark_seen plus the sent entry's seen mark, as finish_wait leaves a report it returned: no later wait returns it again."""
    mark_seen(store, report)
    sent = sent_file(store)
    if sent and cut_chars(os.path.basename(report), 3) == cut_chars(os.path.basename(sent.rstrip('/') or sent), 3) and os.path.isfile(report):
        seen = f'{report}:{file_mtime(report)}'
        update(store, lambda doc: set_sent_seen(doc, seen))


def set_sent_seen(doc, seen):
    member(doc, 'sent')['seen'] = seen


def set_shown(doc, unit, k):
    member(doc, 'shown')[unit] = k


def herdr_get(name):
    """herdr agent get NAME 2>/dev/null || true: its stdout."""
    r = subprocess.run(['herdr', 'agent', 'get', name], capture_output=True, check=False)
    return r.stdout.decode('utf-8', 'replace').rstrip('\n')


SUPERSEDED = 'superseded: a newer wait owns this store; this one ends'


def wait_owner_path(store):
    return f'{store}/wait.pid'


def wait_token():
    """The token a wait owns the store under: $PAIR_WAIT_OWNER (a caller that runs several waits as one, kitchen.sh's quiet wait), else this pid."""
    return os.environ.get('PAIR_WAIT_OWNER') or str(os.getpid())


def read_wait_owner(store):
    """The token in <store>/wait.pid, or None when the file is absent."""
    try:
        with open(wait_owner_path(store), 'rb') as f:
            return f.read().decode('utf-8', 'surrogateescape').strip()
    except OSError:
        return None


def claim_wait(store, token):
    """Take the store: write the token to wait.pid under the store lock, replacing whatever is there. The older wait ends at its next poll."""
    lock = acquire_lock(store)
    try:
        path = wait_owner_path(store)
        with open(f'{path}.tmp', 'w') as f:
            f.write(f'{token}\n')
        os.replace(f'{path}.tmp', path)
    finally:
        os.rmdir(lock)


def release_wait(store, token):
    """Remove wait.pid when it still holds this token, reading and unlinking inside one hold of the store lock: a newer owner's file stays."""
    lock = acquire_lock(store)
    try:
        if read_wait_owner(store) == token:
            os.unlink(wait_owner_path(store))
    finally:
        os.rmdir(lock)


def superseded(store, token):
    """True when wait.pid does not hold this token. A missing file counts: only a newer owner's release (or claim) removes or replaces it."""
    return read_wait_owner(store) != token


def wait_for_reply(ctx, store, name, timeout, now=None, token=None):
    """wait_for_reply(): block until the sidekick's reply lands or the interval ends, in slices so a report is seen the moment
    it is written. A settle from herdr is a hint, not proof: only one that holds for PAIR_SETTLE_HOLD seconds ends the wait."""
    result = WaitResult()
    settle_hold = env_int('PAIR_SETTLE_HOLD', 60)
    busy_poll = env_float('PAIR_BUSY_POLL', 10)
    settle_poll = env_float('PAIR_SETTLE_POLL_S', 5)
    clock = now_s if now is None else (lambda: now)
    deadline = clock() + tdiv(timeout, 1000)
    settled_at = None
    # what the top of the loop looks for: a report written in the last slice must not read as missing because the deadline ended the wait
    arrived = lambda: bool(pending_report(ctx, store) or fresh_reply(ctx, store) or ctx.variant.steps_due(ctx, store, dispatched(store)))
    while True:
        if token is not None and superseded(store, token):
            result.superseded = True
            return result
        ready = pending_report(ctx, store) or fresh_reply(ctx, store)
        if ready:
            result.ready, result.code, result.err = ready, 0, ''
            result.out = herdr_get(name)
            break
        result.ready = ''
        if ctx.variant.steps_due(ctx, store, dispatched(store)):
            result.steps_due, result.code, result.err = True, 0, ''
            result.out = herdr_get(name)
            break
        slice_ms = (deadline - clock()) * 1000
        slice_ms = min(slice_ms, 30000)
        slice_ms = max(slice_ms, 1000)
        r = subprocess.run(['herdr', 'agent', 'wait', name, '--timeout', str(slice_ms)], capture_output=True, check=False)
        result.code = r.returncode
        result.out = r.stdout.decode('utf-8', 'replace').rstrip('\n')
        result.err = r.stderr.decode('utf-8', 'replace').rstrip('\n')
        if result.code == 0:
            state = agent_state(result.out, None)
            if state == 'blocked':
                break
            if pending_report(ctx, store) or fresh_reply(ctx, store) or ctx.variant.steps_due(ctx, store, dispatched(store)):
                continue
            if not pane_busy(name) and not sidekick_activity(store, clock()) and seen_reply(store):
                break
            if pane_busy(name) or sidekick_activity(store, clock()):
                settled_at = None
                time.sleep(busy_poll)
                if not clock() < deadline:
                    break
                continue
            if settled_at is None:
                settled_at = clock()
            if not clock() - settled_at < settle_hold:
                break
            if not clock() < deadline:
                break
            time.sleep(settle_poll)
            continue
        settled_at = None
        code = error_code(result.err)
        if code != 'timeout':
            break
        if not clock() < deadline:
            if arrived():
                continue
            break
    return result


def default_signals():
    """Restore default handling of INT, TERM and HUP, so a command killed by one of them ends without a Python traceback and the signal is its
    exit status. A signal the parent ignored (a background job's SIGINT) stays ignored."""
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(number) is not signal.SIG_IGN:
            signal.signal(number, signal.SIG_DFL)


def end_with_parent():
    """Restore default handling of INT, TERM and HUP (an ignored signal stays ignored) so a wait killed by a signal ends without a traceback, and
    set PR_SET_PDEATHSIG so a background wait ends when its parent does."""
    default_signals()
    try:
        import ctypes
        ctypes.CDLL(None).prctl(1, int(signal.SIGTERM))
    except (OSError, AttributeError):
        pass


def owned_wait(ctx, store, name, timeout, interval, now=None):
    """A wait that owns the store: it claims wait.pid (unless a caller that runs several waits as one claims only on its first), ends with
    SUPERSEDED and 4 when a newer wait took the store, and releases the claim when it returns (unless that caller keeps it)."""
    from . import cli
    held = os.environ.get('PAIR_WAIT_OWNER')
    token = wait_token()
    if held and os.environ.get('PAIR_WAIT_UNOWNED') == '1':
        # a quiet wait whose first claim failed: it runs on without ownership, checking nothing
        token = None
    elif not held or os.environ.get('PAIR_WAIT_CLAIM') == '1':
        try:
            claim_wait(store, token)
        except Die as e:
            # A lock that stays held must not fail a wait whose work is done (a dispatch has delivered its brief): wait without ownership.
            sys.stderr.write(f'{cli.PROG}: wait: could not claim wait.pid ({e.message}); waiting without ownership\n')
            token = None
            if held:
                sys.stdout.write('unowned: wait.pid could not be claimed\n')
    try:
        result = wait_for_reply(ctx, store, name, timeout, now, token=token)
        if result.superseded:
            sys.stdout.write(f'{SUPERSEDED}\n')
            return 4
        return finish_wait(ctx, store, interval, result, reply_mode(result), result.ready, now)
    finally:
        if not held and token is not None:
            try:
                release_wait(store, token)
            except Die as e:
                sys.stderr.write(f'{cli.PROG}: wait: could not release wait.pid ({e.message})\n')


def missing_pair(store):
    # The bash goes on with an empty agent name and prints a pair of error lines for every field() it then reads; the
    # first pair and the status are kept.
    sys.stdout.write('state: \n')
    raise Exit(2, f'pair.sh: no pair.json in {store}; run: pair.sh init <slug>\njq: error: Could not open file : No such file or directory\n')


def wait(ctx, args):
    from .commands.report import in_herdr
    if len(args) == 2 and args[1] == '--release':
        # kitchen.sh's quiet wait gives its claim back when it exits (internal; it runs from an exiting shell, so no herdr checks)
        if not os.path.isfile(f'{args[0]}/pair.json'):
            missing_pair(args[0])
        release_wait(args[0], wait_token())
        return 0
    in_herdr()
    end_with_parent()
    if len(args) < 1:
        raise Die('usage: pair.sh wait <store> [--timeout MS | --every MIN]')
    store = args[0]
    timeout, interval, _, rest = wait_opts(args[1:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(f'{store}/pair.json'):
        missing_pair(store)
    from .store import field
    name = field(store, 'sidekick', 'name')
    return owned_wait(ctx, store, name, timeout, interval)
