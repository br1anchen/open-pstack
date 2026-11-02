"""Herdr (herdr.dev) as the multiplexer: every call is one `herdr` command, and its JSON replies are read here and nowhere else."""

import json
import os
import subprocess
import sys
import time

from ..errors import NotInMux
from ..numbers import env_float
from ..store import TEXT
from .base import DEVNULL, MERGED, PANE_BUSY, PIPE, Agent, Mux, Process, Reply


def run(args, stdout=PIPE, stderr=PIPE):
    """The one place herdr is run: its CompletedProcess, each stream as the caller asked."""
    return subprocess.run(
        ['herdr', *args], stdout=stdout, stderr=stderr, check=False, **TEXT
    )


def parse(reply):
    try:
        return json.loads(reply)
    except ValueError:
        return None


def dig(value, *keys):
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def error_code(text):
    """The error code of the reply herdr printed, or ''."""
    code = dig(parse(text or ''), 'error', 'code')
    return code if isinstance(code, str) else ''


def reply_status(out):
    status = dig(parse(out or ''), 'result', 'agent', 'agent_status')
    return status if isinstance(status, str) else ''


class Herdr(Mux):
    program = 'herdr'
    label = 'Herdr'
    failure = 'herdr_error'
    pane_var = 'HERDR_PANE_ID'
    inside = 'HERDR_ENV=1'

    def reply(self, r, stderr=PIPE):
        """A Reply from herdr's CompletedProcess; with stderr merged, the error is read from stdout."""
        text = r.stdout if stderr == MERGED else r.stderr
        error = (error_code(text) or self.failure) if r.returncode != 0 else ''
        return Reply(r.returncode, r.stdout, r.stderr, error, reply_status(r.stdout))

    def json(self, *args):
        """herdr with its stderr left on the terminal: the parsed JSON of its stdout, or None."""
        return parse(run(args, stderr=None).stdout)

    def code_out(self, *args):
        r = run(args)
        return r.returncode, r.stdout

    def require(self, what=None):
        if os.environ.get('HERDR_ENV') != '1':
            raise NotInMux(what or 'not running inside Herdr (HERDR_ENV != 1)')

    def own_pane(self):
        return os.environ.get(self.pane_var, '')

    def location(self):
        return os.environ.get('HERDR_WORKSPACE_ID', ''), os.environ.get(
            'HERDR_TAB_ID', ''
        )

    def pane_env(self, pane, env):
        sock = env.get('HERDR_SOCKET_PATH', '')
        return [
            f'HERDR_PANE_ID={pane}',
            *([f'HERDR_SOCKET_PATH={sock}'] if sock else []),
        ]

    def outside_env(self):
        return {'HERDR_ENV': '0'}

    def pane_exists(self, pane):
        return run(['pane', 'get', pane]).returncode == 0

    def pane_close(self, pane):
        return run(['pane', 'close', pane]).returncode == 0

    def pane_screen(self, pane, lines):
        return self.code_out(
            'pane', 'read', pane, '--source', 'visible', '--lines', str(lines)
        )

    def pane_key(self, pane, key):
        return run(['pane', 'send-keys', pane, key]).returncode == 0

    def pane_size(self, pane):
        panes = dig(
            self.json('pane', 'layout', '--pane', pane), 'result', 'layout', 'panes'
        )
        for each in panes if isinstance(panes, list) else []:
            if isinstance(each, dict) and each.get('pane_id') == pane:
                rect = each.get('rect') if isinstance(each.get('rect'), dict) else {}
                return rect.get('width'), rect.get('height')
        return None

    def split(self, anchor, direction, cwd, env=()):
        made = self.json(
            'pane',
            'split',
            '--pane',
            anchor,
            '--direction',
            direction,
            '--cwd',
            cwd,
            '--no-focus',
            *[part for kv in env for part in ('--env', kv)],
        )
        pane = dig(made, 'result', 'pane', 'pane_id')
        return pane if isinstance(pane, str) else ''

    def tab(self, near, label, cwd, env=()):
        made = self.json(
            'tab',
            'create',
            '--workspace',
            near.split(':', 1)[0],
            '--cwd',
            cwd,
            '--label',
            label,
            '--no-focus',
            *[part for kv in env for part in ('--env', kv)],
        )
        pane = dig(made, 'result', 'root_pane', 'pane_id')
        return pane if isinstance(pane, str) else ''

    def processes(self, pane):
        code, info = self.code_out('pane', 'process-info', '--pane', pane)
        if code != 0:
            return None
        process = dig(parse(info), 'result', 'process_info')
        process = process if isinstance(process, dict) else {}
        listed = process.get('foreground_processes')
        return Process(
            process.get('shell_pid'),
            process.get('foreground_process_group_id'),
            tuple(p.get('pid') for p in listed if isinstance(p, dict))
            if isinstance(listed, list)
            else None,
        )

    def agent(self, name):
        code, out = self.code_out('agent', 'get', name)
        if code != 0:
            return None
        agent = dig(parse(out), 'result', 'agent')
        agent = agent if isinstance(agent, dict) else {}
        text = lambda key: agent[key] if isinstance(agent.get(key), str) else ''
        session = agent.get('agent_session')
        return Agent(
            text('name'),
            text('agent'),
            text('agent_status'),
            text('pane_id'),
            text('cwd'),
            session if isinstance(session, dict) else None,
        )

    def screen(self, name, lines):
        return self.code_out(
            'agent', 'read', name, '--source', 'visible', '--lines', str(lines)
        )

    def key(self, name, key, stderr=DEVNULL):
        return run(['agent', 'send-keys', name, key], DEVNULL, stderr).returncode == 0

    def rename(self, target, name):
        return run(['agent', 'rename', target, name]).returncode == 0

    def start(
        self, name, kind, pane, args, timeout_ms=60000, stdout=DEVNULL, stderr=PIPE
    ):
        """herdr agent start, tried again while herdr calls the pane busy (as a pane split a moment ago can be): up to five tries
        PAIR_START_RETRY_S apart. Each try's stderr is captured to read the code; the last one's goes where the caller asked."""
        argv = [
            'agent',
            'start',
            name,
            '--kind',
            kind,
            '--pane',
            pane,
            '--timeout',
            str(timeout_ms),
            '--',
            *args,
        ]
        capture = stderr if stderr == MERGED else PIPE
        for tries in range(1, START_TRIES + 1):
            r = run(argv, stdout, capture)
            text = r.stdout if capture == MERGED else r.stderr
            if (
                r.returncode == 0
                or tries == START_TRIES
                or error_code(text) != PANE_BUSY
            ):
                break
            time.sleep(env_float('PAIR_START_RETRY_S', 1))
        reply = self.reply(r, capture)
        if stderr is None and r.stderr:
            sys.stderr.write(r.stderr)
        if stderr == DEVNULL:
            reply.err = None
        return reply

    def prompt(
        self,
        name,
        text,
        wait=False,
        until=None,
        timeout_ms=None,
        stdout=PIPE,
        stderr=PIPE,
    ):
        if until and not wait:
            raise ValueError('prompt: until needs wait')
        argv = ['agent', 'prompt', name, text]
        if wait:
            argv.append('--wait')
        if until:
            argv += ['--until', until]
        if timeout_ms is not None:
            argv += ['--timeout', str(timeout_ms)]
        return self.reply(run(argv, stdout, stderr), stderr)

    def wait(self, name, until=(), timeout_ms=None, stdout=PIPE, stderr=PIPE):
        argv = [
            'agent',
            'wait',
            name,
            *[part for u in until for part in ('--until', u)],
        ]
        if timeout_ms is not None:
            argv += ['--timeout', str(timeout_ms)]
        return self.reply(run(argv, stdout, stderr), stderr)

    def notify(self, title, body):
        return (
            run(
                ['notification', 'show', title, '--body', body, '--sound', 'request']
            ).returncode
            == 0
        )

    def inspect_agent(self, name, lines=None):
        return f'herdr agent read {name} --source visible' + (
            f' --lines {lines}' if lines else ''
        )

    def inspect_pane(self, pane):
        return f'herdr pane read {pane} --source visible'

    def list_agents(self):
        return 'herdr agent list'

    def list_kinds(self):
        return 'herdr agent'


START_TRIES = 5
