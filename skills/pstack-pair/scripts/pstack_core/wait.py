"""wait, as the wait closure in pair-core.sh runs it: the option parser, the loop that waits for the sidekick's reply,
and finish_wait, which prints the outcome. WaitResult carries what the bash passed in the globals code, out, err,
ready and steps_due. send_and_wait, stop and steer keep the bash closure."""
import json
import os
import re
import signal
import subprocess
import sys
import time

from .checkin import checkin, sidekick_activity
from .errors import Die, Exit
from .herdr import agent_status, pane_busy
from .providers import provider_hint
from .replies import (dispatched, fresh_reply, latest_report, mark_seen, pending_report, queue_note, review_delta, seen_reply, sent_field,
                      unreviewed_steps, withheld_report, file_mtime)
from .review import jq_raw, review_base
from .store import alt, bash_int, cut_chars, event, header_field, pair_file, pair_json, paused_reason, unit_name, update


class WaitResult:
    __slots__ = ('code', 'out', 'err', 'ready', 'steps_due')

    def __init__(self, code=0, out='', err='', ready='', steps_due=False):
        self.code = code
        self.out = out
        self.err = err
        self.ready = ready
        self.steps_due = steps_due


class ArithError(Exception):
    def __init__(self, text):
        super().__init__(text)
        self.text = text


def bash_arith(expr):
    """$(( expr )) for the forms a --timeout or --every value takes: integers (decimal, 0octal, 0xhex, base#n), blanks, unary
    + - ! ~, ** * / % + - and parentheses. Anything else ends in the error bash prints, as far as that is reproduced."""
    expr = expr.lstrip(' \t\n')
    pos, n = 0, len(expr)

    def error(msg, token_from, prefix_to=None):
        shown = expr if prefix_to is None else expr[:prefix_to]
        raise ArithError(f'{shown}: {msg} (error token is "{expr[token_from:]}")')

    def skip():
        nonlocal pos
        while pos < n and expr[pos] in ' \t\n':
            pos += 1

    last_number = 0

    def number():
        nonlocal pos, last_number
        start = last_number = pos
        while pos < n and (expr[pos].isalnum() or expr[pos] in '_#@'):
            pos += 1
        word = expr[start:pos]
        try:
            if re.fullmatch(r'0[xX][0-9a-fA-F]+', word):
                return int(word, 16)
            if re.fullmatch(r'0[0-7]*', word):
                return int(word, 8) if len(word) > 1 else 0
            if re.fullmatch(r'[1-9][0-9]*', word):
                return int(word)
            m = re.fullmatch(r'([0-9]+)#([0-9a-zA-Z@_]+)', word)
            if m and 2 <= int(m.group(1)) <= 64:
                digits = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ@_'
                base = int(m.group(1))
                value = 0
                for ch in m.group(2):
                    d = digits.index(ch)
                    if (d >= 36 and base <= 36) and ch.isupper():
                        d -= 26 if base <= 36 else 0
                    if d >= base:
                        raise ValueError
                    value = value * base + d
                return value
        except ValueError:
            pass
        raise ArithError(f'{expr[:pos]}: value too great for base (error token is "{word}")')

    def primary():
        nonlocal pos
        skip()
        if pos >= n:
            error('arithmetic syntax error: operand expected', pos)
        c = expr[pos]
        if c == '(':
            pos += 1
            value = additive()
            skip()
            if pos >= n or expr[pos] != ')':
                raise ArithError(f"{expr}: missing `)' (error token is \"{expr[last_number:]}\")")
            pos += 1
            return value
        if c in '+-!~':
            pos += 1
            value = primary()
            return {'+': value, '-': -value, '!': int(not value), '~': ~value}[c]
        if c.isdigit():
            return number()
        if c.isalpha() or c == '_':
            end = pos
            while end < n and (expr[end].isalnum() or expr[end] == '_'):
                end += 1
            raise ArithError(f'{expr[pos:end]}: unbound variable')
        if c in '*/%':
            error('arithmetic syntax error: operand expected', pos)
        error('arithmetic syntax error: invalid arithmetic operator', pos)

    def power():
        nonlocal pos
        base = primary()
        skip()
        if expr[pos:pos + 2] == '**':
            pos += 2
            exponent = power()
            if exponent < 0:
                raise ArithError(f'{expr}: exponent less than 0 (error token is "{expr[pos:]}")')
            return base ** exponent
        return base

    def multiplicative():
        nonlocal pos
        value = power()
        while True:
            skip()
            if pos < n and expr[pos] in '*/%' and expr[pos:pos + 2] != '**':
                op = expr[pos]
                pos += 1
                start = pos
                skip()
                rhs_at = pos
                rhs = power()
                if op != '*' and rhs == 0:
                    raise ArithError(f'{expr}: division by 0 (error token is "{expr[rhs_at:]}")')
                if op == '*':
                    value *= rhs
                else:
                    q = abs(value) // abs(rhs)
                    value = q if (value < 0) == (rhs < 0) else -q if op == '/' else value - (q if (value < 0) == (rhs < 0) else -q) * rhs
            else:
                return value

    def additive():
        nonlocal pos
        value = multiplicative()
        while True:
            skip()
            if pos < n and expr[pos] in '+-':
                op = expr[pos]
                pos += 1
                rhs = multiplicative()
                value = value + rhs if op == '+' else value - rhs
            else:
                return value

    if expr.strip(' \t\n') == '':
        return 0
    value = additive()
    skip()
    if pos < n:
        error('arithmetic syntax error in expression' if expr[pos] == ')' or expr[pos].isalnum() else 'arithmetic syntax error: invalid arithmetic operator', pos)
    return value


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def core_line(text, after='wait_opts() {'):
    """(path, line number) of the first line holding text in the function of pair-core.sh that starts with `after`, for the messages
    bash prefixes to its errors. When the function no longer holds the text (its body moved into Python and it is a shim) the line is
    the function's own definition; it is never 0 unless pair-core.sh has no such function."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pair-core.sh')
    definition = 0
    try:
        with open(path, encoding='utf-8') as f:
            for number, line in enumerate(f, 1):
                if not definition and line.startswith(after):
                    definition = number
                if definition and text in line:
                    return path, number
    except OSError:
        pass
    return path, definition


def shell_error(line_text, message):
    path, number = core_line(line_text)
    raise Exit(1, f'{path}: line {number}: {message}\n')


def wait_opts(args, timeout='540000'):
    """wait_opts(): (timeout text, check-in interval in minutes, send_only, leftover args). The timeout is kept as the text
    bash keeps, since bash evaluates it as arithmetic wherever it is used; --every is evaluated where it is read."""
    rest, send_only, i = [], False, 0
    while i < len(args):
        arg = args[i]
        if arg == '--timeout':
            if i + 1 >= len(args):
                shell_error('--timeout) timeout="$2"', '$2: unbound variable')
            timeout, i = args[i + 1], i + 2
        elif arg == '--every':
            if i + 1 >= len(args):
                shell_error('--every) timeout=$(($2 * 60000))', '$2: unbound variable')
            try:
                timeout = str(bash_arith(f'{args[i + 1]} * 60000'))
            except ArithError as e:
                shell_error('--every) timeout=$(($2 * 60000))', e.text)
            i += 2
        elif arg == '--send-only':
            send_only, i = True, i + 1
        else:
            rest.append(arg)
            i += 1
    try:
        interval = tdiv(bash_arith(timeout), 60000)
    except ArithError as e:
        shell_error('checkin_interval_m=$((timeout / 60000))', e.text)
    return timeout, interval, send_only, rest


def timeout_ms(timeout):
    return bash_arith(timeout)


def reply_mode(result):
    return 'steps' if not result.ready and result.steps_due else 'reply'


def now_s():
    return int(time.time())


def bash_sleep(text):
    """sleep with the text the bash gives it: seconds, fractions and s/m/h/d suffixes; anything else is sleep's own error."""
    m = re.fullmatch(r'(?:[0-9]+\.?[0-9]*|\.[0-9]+)[smhd]?', text)
    if not m:
        # GNU sleep takes more than this (0x1, 0e0, inf, a leading blank); run it, and end the command as set -e does if it refuses.
        r = subprocess.run(['sleep', text], check=False)
        if r.returncode:
            raise Exit(r.returncode)
        return
    unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}.get(text[-1], 1)
    time.sleep(float(text.rstrip('smhd')) * unit)


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


class JqError(Exception):
    pass


def jq_text(value):
    """jq -r of one output."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False).replace('\x7f', '\\u007f')
    return jq_raw(value)


def jq_member(value, key):
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    raise JqError


def truthy(value):
    return value is not None and value is not False


def jq_run(text, program, default=None):
    """What `jq -r PROGRAM` prints for text, with `|| printf DEFAULT` after it, as the command substitution gives it."""
    values, ok = json_values(text)
    out, failed = [], not ok
    for value in values:
        try:
            result = program(value)
        except JqError:
            failed = True
            continue
        out.append(result)
    printed = ''.join(f'{jq_text(o)}\n' for o in out if o is not None)
    if failed and default is not None:
        printed += default
    return printed.rstrip('\n')


def error_state(err):
    """.error.code // .error // "herdr_error" """
    def program(value):
        error = jq_member(value, 'error')
        code = jq_member(error, 'code')
        if truthy(code):
            return code
        return error if truthy(error) else 'herdr_error'
    return jq_run(err, program, 'herdr_error')


def agent_state(out, default):
    """.result.agent.agent_status // DEFAULT (empty: no output)"""
    def program(value):
        status = jq_member(jq_member(jq_member(value, 'result'), 'agent'), 'agent_status')
        return status if truthy(status) else default
    return jq_run(out, program, 'settled' if default == 'settled' else None)


def json_update_failed(code, text=''):
    raise Exit(code, text)


def finish_wait(ctx, store, interval, result, mode, report='', now=None):
    """finish_wait(): print the outcome of a wait and return the exit code the bash exits with."""
    write = sys.stdout.write
    clock = now_s if now is None else (lambda: now)
    name = lambda: jq_raw(alt(alt(pair_json(store).get('sidekick'), {}).get('name'), None))
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
        return 4
    why = ''
    if withheld:
        write(f'report: {withheld[0]} written but not ready: {withheld[1]}\n')
        why = 'report:unready'
    else:
        write('report: missing\n')
    if state == 'blocked':
        event(store, 'master', 'wake', running, why or 'blocked')
        write(provider_hint(store))
        return 3
    if agent_status(name()) == 'working' or activity:
        if os.environ.get('PAIR_QUIET_WAIT') != '1':
            event(store, 'master', 'wake', running, why or 'checkin')
        write(checkin(ctx, store, str(interval), clock()))
    else:
        event(store, 'master', 'wake', running, why or 'missing')
        write(provider_hint(store))
    return 4


def sent_file(store):
    """`.sent.file` as jq's `// default` leaves it: None when missing, null or false; an empty string stays an empty string."""
    sent = alt(pair_json(store).get('sent'), {})
    value = alt(sent.get('file') if isinstance(sent, dict) else None, None)
    return None if value is None else jq_raw(value)


def status_of(report):
    """header_field "$report" status, whose grep complains on stderr when the file is gone."""
    if not os.path.isfile(report):
        sys.stderr.write(f'grep: {report}: No such file or directory\n')
    return header_field(report, 'status')


def set_sent_seen(doc, seen):
    if not isinstance(doc.get('sent'), dict):
        doc['sent'] = {}
    doc['sent']['seen'] = seen


def set_shown(doc, unit, k):
    if not isinstance(doc.get('shown'), dict):
        doc['shown'] = {}
    doc['shown'][unit] = k


def herdr_get(name):
    """herdr agent get NAME 2>/dev/null || true: its stdout."""
    r = subprocess.run(['herdr', 'agent', 'get', name], capture_output=True, check=False)
    return r.stdout.decode('utf-8', 'replace').rstrip('\n')


def wait_for_reply(ctx, store, name, timeout, now=None):
    """wait_for_reply(): block until the sidekick's reply lands or the interval ends, in slices so a report is seen the moment
    it is written. A settle from herdr is a hint, not proof: only one that holds for PAIR_SETTLE_HOLD seconds ends the wait."""
    result = WaitResult()
    settle_hold = os.environ.get('PAIR_SETTLE_HOLD') or '60'
    busy_poll = os.environ.get('PAIR_BUSY_POLL') or '10'
    settle_poll = os.environ.get('PAIR_SETTLE_POLL_S') or '5'
    clock = now_s if now is None else (lambda: now)
    deadline = clock() + tdiv(timeout_ms(timeout), 1000)
    settled_at = None
    while True:
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
                bash_sleep(busy_poll)
                if not clock() < deadline:
                    break
                continue
            if settled_at is None:
                settled_at = clock()
            hold = bash_int(settle_hold)
            if hold is None:
                # bash 5.3's words; bash 5.2 and earlier say "integer expression expected". Only stderr differs, on an odd PAIR_SETTLE_HOLD.
                path, number = core_line('-lt "$settle_hold_s"', 'wait_for_reply() {')
                sys.stderr.write(f'{path}: line {number}: [: {settle_hold}: integer expected\n')
                break
            if not clock() - settled_at < hold:
                break
            if not clock() < deadline:
                break
            bash_sleep(settle_poll)
            continue
        settled_at = None
        code = jq_run(result.err, lambda v: (lambda c: c if truthy(c) else None)(jq_member(jq_member(v, 'error'), 'code')), None)
        if code != 'timeout':
            break
        if not clock() < deadline:
            break
    return result


def die_with_the_shell():
    """End the way the bash does. A signal kills the process outright, with no Python traceback and the signal as the exit
    status; and when the shell that ran it dies first (a SIGTERM to the shell alone), the wait goes too instead of running on
    as an orphan."""
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        # Python swaps in its own SIGINT handler; a signal the parent ignored (a background job's SIGINT) stays ignored, as in bash.
        if signal.getsignal(number) is not signal.SIG_IGN:
            signal.signal(number, signal.SIG_DFL)
    try:
        import ctypes
        ctypes.CDLL(None).prctl(1, int(signal.SIGTERM))
    except (OSError, AttributeError):
        pass


def wait(ctx, args):
    from .commands.report import in_herdr
    in_herdr()
    die_with_the_shell()
    if len(args) < 1:
        raise Die('usage: pair.sh wait <store> [--timeout MS | --every MIN]')
    store = args[0]
    timeout, interval, _, rest = wait_opts(args[1:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(f'{store}/pair.json'):
        # The bash goes on with an empty agent name and prints a pair of error lines for every field() it then reads; the
        # first pair and the status are kept.
        sys.stdout.write('state: \n')
        raise Exit(2, f'pair.sh: no pair.json in {store}; run: pair.sh init <slug>\njq: error: Could not open file : No such file or directory\n')
    pair = pair_json(store)
    name = jq_raw(alt(alt(pair.get('sidekick'), {}).get('name'), None))
    result = wait_for_reply(ctx, store, name, timeout)
    return finish_wait(ctx, store, interval, result, reply_mode(result), result.ready)
