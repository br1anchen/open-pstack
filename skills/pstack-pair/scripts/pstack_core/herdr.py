import json
import re
import subprocess
import sys

BUSY = re.compile(r'esc (twice )?to interrupt|guide devin while it works|send queued messages now|↵ send now|ctrl\+enter to send now',
                  re.IGNORECASE)


def herdr(*args, errors='replace'):
    """herdr with stderr dropped: (exit code, stdout text). Text that jq reads decodes with 'replace', as jq does for bad
    bytes; text that is shown again, like a screen, passes errors='surrogateescape' to keep its bytes."""
    r = subprocess.run(['herdr', *args], capture_output=True, check=False)
    return r.returncode, r.stdout.decode('utf-8', errors)


def pane_text(name):
    """The pane's visible screen as one line."""
    return re.sub(r'[\n\t ]+', ' ', herdr('agent', 'read', name, '--source', 'visible', '--lines', '40', errors='surrogateescape')[1])


def pane_busy(name):
    return BUSY.search(pane_text(name)) is not None


def parse(reply):
    """The one JSON document a herdr or daemon reply holds, or None when it is not JSON."""
    try:
        return json.loads(reply)
    except ValueError:
        return None


def dig(value, *keys):
    """The value at keys in parsed JSON, None when a step is absent or over a value that is no object."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def reply_at(reply, *keys, quiet=False):
    """The value at keys in a herdr or daemon reply, None when it has none. A reply that is not JSON, or whose path steps
    over a value that is no object, is one stderr line saying so (quiet leaves it out), and None."""
    doc = parse(reply)
    problem = 'is not JSON' if doc is None and reply.strip() != 'null' else ''
    if not problem:
        for key in keys:
            if doc is not None and not isinstance(doc, dict):
                problem = f'has no object on the way to {".".join(keys)}'
                break
            doc = doc.get(key) if doc else None
        else:
            return doc
    if not quiet:
        sys.stdout.flush()
        sys.stderr.write(f'herdr reply {problem}\n')
        sys.stderr.flush()
    return None


def error_code(err):
    """The error code of the reply herdr printed, or ''."""
    code = dig(parse(err), 'error', 'code')
    return code if isinstance(code, str) else ''


def error_state(err):
    """The error code of the reply herdr printed, `herdr_error` when it has none."""
    return error_code(err) or 'herdr_error'


def agent_state(out, default):
    """The agent status in the reply, else default ('' when there is none)."""
    status = dig(parse(out), 'result', 'agent', 'agent_status')
    return status if isinstance(status, str) and status else (default or '')


def agent_field(out, key):
    """The key of the agent in herdr's JSON reply; None when it is missing or null."""
    agent = dig(parse(out), 'result', 'agent')
    return agent.get(key) if isinstance(agent, dict) else None


def agent_status(name):
    """idle|working|blocked|done|unknown, or absent when herdr has no such agent."""
    code, out = herdr('agent', 'get', name)
    if code != 0:
        return 'absent'
    state = agent_field(out, 'agent_status') or 'unknown'
    if state in ('idle', 'done') and pane_busy(name):
        return 'working'
    return state


def agent_kind(name):
    """The herdr agent kind hosting name (a name or pane id), or empty."""
    return agent_field(herdr('agent', 'get', name)[1], 'agent') or ''
