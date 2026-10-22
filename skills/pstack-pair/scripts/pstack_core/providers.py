"""What a sidekick that stopped without a report may have hit: its model provider's errors, read from the pane,
the agent's own session log, or herdr."""
import glob
import os
import re

from .panes import agent_status, dig, herdr, pane_text, parse
from .store import load, read_text, member, newest, newest_first, peek, text_at, update

PROVIDER_ERROR_RE = ('capacity issues|no api providers|no api key found|unauthorized|forbidden|rate.?limit|too many requests|overloaded|fetch failed|'
                     'econnreset|econnrefused|etimedout|socket hang up|usage limit reached|wait for usage to reset|missing meta credentials|'
                     'billing verification failed|billing_error')


# The window is greedy, so where several matches overlap the one taken is the last, where grep -o took the longest.
def error_context(text, before, after):
    """Every provider error in text with up to `before` characters ahead of it and `after` behind it on its line, matches that overlap taken as one."""
    pattern = re.compile(f'.{{0,{before}}}(?:{PROVIDER_ERROR_RE}).{{0,{after}}}', re.IGNORECASE)
    return [found.group(0) for found in pattern.finditer(text)]


def peek_message(value):
    message = value.get('message') if isinstance(value, dict) else None
    return message if isinstance(message, dict) else {}


def tail_lines_with_newline(text, count):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines[-count:]) + ('\n' if lines else '')


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
    for line in tail_lines_with_newline(read_text(path), 200).split('\n'):
        message = peek_message(parse(line))
        if message.get('role') == 'assistant':
            last = message
    error = last.get('errorMessage') if last else None
    if last and last.get('stopReason') == 'error' and isinstance(error, str) and error:
        return ' '.join(error.split())[:240]
    return ''


def muse_session_error(cwd):
    """The provider error a Muse Code session stopped on, from the newest of its 20 newest logs that names cwd.
    '' when none does."""
    if not cwd:
        return ''
    base = os.environ.get('XDG_DATA_HOME') or f'{os.environ.get("HOME", "")}/.local/share'
    logs = glob.glob(os.path.join(glob.escape(base), 'muse/sessions/*/*/*/*/session.jsonl'))
    logs = newest_first(logs)[:20]
    chosen = ''
    for log in logs:
        try:
            if cwd in read_text(log):
                chosen = log
                break
        except OSError:
            continue
    if not chosen:
        return ''
    matches = error_context(tail_lines_with_newline(read_text(chosen), 400), 60, 100)
    return matches[-1][:240] if matches else ''


def record_prime_session(store, role):
    """Record the daemon session herdr reports for the role's prime-agent pane. False when herdr reports none."""
    section = peek(load(store), role)
    code, out = herdr('agent', 'get', text_at(section, 'name'))
    if code != 0:
        return False
    agent = dig(parse(out), 'result', 'agent')
    session = dig(agent, 'agent_session')
    if not (isinstance(agent, dict) and isinstance(session, dict) and agent.get('agent') == 'prime-agent'
            and agent.get('pane_id') == text_at(section, 'pane_id') and session.get('kind') in ('path', 'id')
            and isinstance(session.get('value'), str) and session['value']):
        return False
    chosen = {'kind': session['kind'], 'value': session['value']}

    def mutate(doc):
        member(doc, role)['session'] = chosen

    update(store, mutate)
    return True


def prime_role_error(store, role):
    record_prime_session(store, role)
    session = peek(load(store), role).get('session')
    if isinstance(session, dict) and session.get('kind') == 'path' and isinstance(session.get('value'), str) and session['value']:
        return agent_session_error(session['value'])
    return ''


def provider_hint(store, phase=''):
    """provider_hint(): the evidence that a sidekick settled on its provider and the way out, or ''."""
    pair = load(store)
    sidekick = peek(pair, 'sidekick')
    name, kind = text_at(sidekick, 'name'), text_at(sidekick, 'kind')
    fallback = sidekick.get('fallback')
    fallback = text_at(fallback, 'kind') if isinstance(fallback, dict) else ''
    if not fallback or fallback == kind:
        fallback = ''
    logged = ''
    if kind == 'pi':
        logged = agent_session_error('', text_at(pair, 'cwd'))
    elif kind == 'prime-agent':
        logged = prime_role_error(store, 'sidekick')
    elif kind == 'muse':
        logged = muse_session_error(text_at(pair, 'cwd'))
    out = ''
    if agent_status(name) == 'absent':
        out += f'provider_error: the {kind} sidekick exited on: {logged}\n' if logged else f'provider_error: the {kind} sidekick exited\n'
        if fallback:
            out += f'next: pair.sh failover {store} --reason exited\n'
        return out
    line = ''
    if kind != 'prime-agent':
        matches = error_context(pane_text(name).rstrip('\n'), 60, 60)
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
