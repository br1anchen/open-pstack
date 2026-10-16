import json
import re
import subprocess

BUSY = re.compile(r'esc (twice )?to interrupt|guide devin while it works|send queued messages now|↵ send now|ctrl\+enter to send now',
                  re.IGNORECASE)


def herdr(*args):
    """herdr with stderr dropped: (exit code, stdout text)."""
    r = subprocess.run(['herdr', *args], capture_output=True, check=False)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


def pane_text(name):
    """The pane's visible screen as one line."""
    return re.sub(r'[\n\t ]+', ' ', herdr('agent', 'read', name, '--source', 'visible', '--lines', '40')[1])


def pane_busy(name):
    return BUSY.search(pane_text(name)) is not None


def agent_field(out, key):
    """jq -r '.result.agent.<key>' of herdr's JSON; None when it is missing or null."""
    try:
        agent = json.loads(out)['result']['agent']
        return agent.get(key)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def agent_status(name):
    """idle|working|blocked|done|unknown, or absent when herdr has no such agent."""
    code, out = herdr('agent', 'get', name)
    if code != 0:
        return 'absent'
    state = agent_field(out, 'agent_status')
    state = 'unknown' if state is None or state is False else str(state)
    if state in ('idle', 'done') and pane_busy(name):
        return 'working'
    return state


def agent_kind(name):
    """The herdr agent kind hosting name (a name or pane id), or empty."""
    kind = agent_field(herdr('agent', 'get', name)[1], 'agent')
    return '' if kind is None or kind is False else str(kind)
