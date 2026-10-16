"""What a sidekick that stopped without a report may have hit: its model provider's errors, read from the pane,
the agent's own session log, or herdr. Text is handled in characters under a UTF-8 locale and in bytes otherwise,
as grep, cut and awk do."""
import glob
import json
import os
import re

from .collate import collate_key
from .herdr import agent_status, herdr, pane_text
from .review import jq_raw
from .store import alt, cut_chars, pair_json, update, utf8_locale

PROVIDER_ERROR_RE = ('capacity issues|no api providers|no api key found|unauthorized|forbidden|rate.?limit|too many requests|overloaded|fetch failed|'
                     'econnreset|econnrefused|etimedout|socket hang up|usage limit reached|wait for usage to reset|missing meta credentials|'
                     'billing verification failed|billing_error')
ALTERNATIVES = [re.compile(alt_, re.IGNORECASE) for alt_ in PROVIDER_ERROR_RE.split('|')]


def grep_o_context(text, before, after):
    """grep -oiE ".{0,before}(provider_error_re).{0,after}": every match, line by line, each the longest match from
    the leftmost start, the next search starting where the last match ended."""
    if not utf8_locale():
        text = text.encode('utf-8', 'surrogateescape').decode('latin-1')
    found = []
    for line in text.split('\n'):
        n = len(line)
        longest = {}
        for a in ALTERNATIVES:
            for m in re.finditer(f'(?=({a.pattern}))', line, re.IGNORECASE):
                longest[m.start()] = max(longest.get(m.start(), 0), len(m.group(1)))
        positions = sorted(longest)
        pos = 0
        while True:
            q0 = next((q for q in positions if q >= pos), None)
            if q0 is None:
                break
            start = max(pos, q0 - before)
            end = max(min(q + longest[q] + after, n) for q in positions if start <= q <= start + before)
            found.append(line[start:end])
            pos = end
    if not utf8_locale():
        found = [f.encode('latin-1').decode('utf-8', 'surrogateescape') for f in found]
    return found


def tsv_cell(value):
    """One cell of jq's @tsv; None when jq cannot format the value (it stops the stream there)."""
    if value is None or value is False:
        return ''
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return 'true'
    text = value if isinstance(value, str) else jq_raw(value)
    return text.replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')


def json_stream(text):
    """The JSON values jq reads from text, up to the first one it cannot parse."""
    decoder, pos, n = json.JSONDecoder(), 0, len(text)
    while True:
        while pos < n and text[pos] in ' \t\n\r':
            pos += 1
        if pos >= n:
            return
        try:
            value, pos = decoder.raw_decode(text, pos)
        except ValueError:
            return
        yield value


def tail_lines(text, count):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines[-count:]) + ('\n' if lines else '')


def newest(paths):
    key = collate_key()
    return min(paths, key=lambda p: (-os.lstat(p).st_mtime_ns, key(os.path.basename(p))), default=None)


def read_log(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'surrogateescape')


def agent_session_error(session, cwd=''):
    """The error a pi agent stopped on, from its session log: session is the log herdr reported, else the newest log
    in pi's session directory for cwd. Only when the session's last assistant turn ended in an error."""
    path = session
    if not path or not os.path.isfile(path):
        if not cwd:
            return ''
        directory = f'{os.environ.get("HOME", "")}/.pi/agent/sessions/--{cwd.removeprefix("/").replace("/", "-")}--'
        path = newest(glob.glob(os.path.join(glob.escape(directory), '*.jsonl'))) or ''
    if not path or not os.path.isfile(path):
        return ''
    last = None
    for value in json_stream(tail_lines(read_log(path), 200)):
        message = value.get('message') if isinstance(value, dict) else None
        if not (isinstance(message, dict) and message.get('role') == 'assistant'):
            continue
        cells = [tsv_cell(message.get('stopReason')), tsv_cell(message.get('errorMessage'))]
        if None in cells:
            continue
        last = cells
    if last and last[0] == 'error' and last[1] != '':
        return awk_substr(last[1], 240)
    return ''


def awk_substr(text, count):
    """substr($2, 1, N) in gawk: characters under a UTF-8 locale, bytes otherwise."""
    return cut_chars(text, count)


def muse_session_error(cwd):
    """The provider error a Muse Code session stopped on, from the newest of its 20 newest logs that names cwd.
    '' when none does. Called directly, the bash function ends the shell when no log is found (its search assignment
    fails under set -e, with status 2 when there is no log at all and 1 when none names cwd); provider_hint calls it in
    a command substitution, which does not inherit set -e, so there it returns nothing."""
    if not cwd:
        return ''
    base = os.environ.get('XDG_DATA_HOME') or f'{os.environ.get("HOME", "")}/.local/share'
    logs = glob.glob(os.path.join(glob.escape(base), 'muse/sessions/*/*/*/*/session.jsonl'))
    key = collate_key()
    logs = sorted(logs, key=lambda p: (-os.lstat(p).st_mtime_ns, key(os.path.basename(p))))[:20]
    chosen = ''
    for log in logs:
        try:
            if cwd in read_log(log):
                chosen = log
                break
        except OSError:
            continue
    if not chosen:
        return ''
    matches = grep_o_context(tail_lines(read_log(chosen), 400), 60, 100)
    return cut_chars(matches[-1], 240) if matches else ''


def record_prime_session(store, role):
    """Record the daemon session herdr reports for the role's prime-agent pane. False when herdr reports none."""
    pair = pair_json(store)
    section = alt(pair.get(role), {})
    name = jq_raw(section.get('name') if isinstance(section, dict) else None)
    code, out = herdr('agent', 'get', name)
    if code != 0:
        return False
    try:
        agent = json.loads(out)['result']['agent']
        session = agent['agent_session']
        ok = (agent.get('agent') == 'prime-agent' and agent.get('pane_id') == jq_raw(section.get('pane_id'))
              and session.get('kind') in ('path', 'id') and isinstance(session.get('value'), str) and len(session['value']) > 0)
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    if not ok:
        return False
    chosen = {'kind': session['kind'], 'value': session['value']}

    def mutate(doc):
        if not isinstance(doc.get(role), dict):
            doc[role] = {}
        doc[role]['session'] = chosen

    update(store, mutate)
    return True


def prime_role_error(store, role):
    record_prime_session(store, role)
    section = alt(pair_json(store).get(role), {})
    session = alt(section.get('session') if isinstance(section, dict) else None, {})
    if isinstance(session, dict) and session.get('kind') == 'path':
        value = jq_raw(alt(session.get('value'), ''))
        if value:
            return agent_session_error(value)
    return ''


def provider_hint(store, phase=''):
    """provider_hint(): the evidence that a sidekick settled on its provider and the way out, or ''."""
    pair = pair_json(store)
    sidekick = alt(pair.get('sidekick'), {})
    sidekick = sidekick if isinstance(sidekick, dict) else {}
    name, kind = jq_raw(sidekick.get('name')), jq_raw(sidekick.get('kind'))
    fallback_section = alt(sidekick.get('fallback'), {})
    fallback = jq_raw(alt(fallback_section.get('kind') if isinstance(fallback_section, dict) else None, ''))
    if not fallback or fallback == kind:
        fallback = ''
    logged = ''
    if kind == 'pi':
        logged = agent_session_error('', jq_raw(pair.get('cwd')))
    elif kind == 'prime-agent':
        logged = prime_role_error(store, 'sidekick')
    elif kind == 'muse':
        logged = muse_session_error(jq_raw(pair.get('cwd')))
    out = ''
    if agent_status(name) == 'absent':
        out += f'provider_error: the {kind} sidekick exited on: {logged}\n' if logged else f'provider_error: the {kind} sidekick exited\n'
        if fallback:
            out += f'next: pair.sh failover {store} --reason exited\n'
        return out
    line = ''
    if kind != 'prime-agent':
        matches = grep_o_context(pane_text(name).rstrip('\n'), 60, 60)
        line = matches[-1] if matches else ''
    line = line or logged
    if not line:
        return ''
    out += f'provider_error: {line}\n'
    if not fallback:
        return out
    if phase == 'bootstrap' or kind == 'prime-agent':
        out += f'next: pair.sh failover {store} --reason provider-error\n'
    else:
        out += f'next: prompt the sidekick once to continue; on a second provider error, pair.sh failover {store} --reason provider-error\n'
    return out
