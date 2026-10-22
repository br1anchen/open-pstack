"""What the pair hands an agent when it starts one, and the preflights that prove an agent answers first: the native arguments for a
permission mode, trust and MCP, the has_* tests for arguments the caller passed itself, and the print-mode preflights of pi, Muse and Prime."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .prime import with_timeout
from .store import TEXT, env_float

PAIR_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def permission_args(kind, mode):
    """Native arguments that give an agent of KIND the permission MODE. Empty for kinds without a known translation."""
    if kind == 'claude':
        return ['--permission-mode', mode]
    if kind == 'devin':
        # devin: auto approves read-only tools, smart lets a fast model judge the rest.
        if mode in ('bypassPermissions', 'dontAsk'):
            return ['--permission-mode', 'dangerous']
        if mode == 'acceptEdits':
            return ['--permission-mode', 'accept-edits']
        return ['--permission-mode', 'auto' if mode == 'plan' else 'smart']
    if kind == 'muse':
        # Muse sandboxes its shell to the workspace by default, which would refuse the role's writes to the run store and the gates' own
        # caches; approvals off keeps an unattended role from blocking.
        if mode == 'bypassPermissions':
            return ['--yolo']
        if mode == 'plan':
            return ['--approval-mode', 'never', '--disable-write']
        return ['--approval-mode', 'never', '--disable-sandbox']
    if kind == 'codex':
        if mode == 'bypassPermissions':
            return ['--dangerously-bypass-approvals-and-sandbox']
        if mode == 'dontAsk':
            return ['-a', 'never', '-s', 'workspace-write']
        if mode == 'plan':
            return ['-a', 'on-request', '-s', 'read-only']
        return ['-a', 'on-request', '-s', 'workspace-write']
    return []


def asks_no_approvals(kind, mode):
    return kind in ('pi', 'prime-agent') and mode != 'plan'


def trust_args(kind):
    return {'pi': ['--approve'], 'devin': ['--respect-workspace-trust', 'false'], 'muse': ['--trust-workspace']}.get(kind, [])


def codex_mcp_names(cwd):
    """The names of the MCP servers codex lists where it runs that are not disabled; none when codex is absent or its list is no JSON."""
    try:
        r = with_timeout('20', ['codex', 'mcp', 'list', '--json'], cwd=cwd or '.', stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return []
    try:
        listed = json.loads(r.stdout)
    except ValueError:
        return []
    items = listed.values() if isinstance(listed, dict) else listed if isinstance(listed, list) else []
    return [item['name'] for item in items if isinstance(item, dict) and item.get('enabled') is not False and isinstance(item.get('name'), str)]


def mcp_args(kind, cwd=None):
    """The arguments that keep an agent's MCP servers off."""
    if kind == 'claude':
        return ['--strict-mcp-config', '--mcp-config', f'{PAIR_DIR}/../references/no-mcp.json']
    if kind == 'codex':
        return ['--disable', 'plugins', *[arg for n in codex_mcp_names(cwd) if re.fullmatch(r'[A-Za-z0-9_-]+', n) for arg in ('-c', f'mcp_servers.{n}.enabled=false')]]
    return []


def has_mcp_arg(args):
    return any(a in ('--mcp-config', '--strict-mcp-config') or a.startswith(('--mcp-config=', 'mcp_servers.', '--config=mcp_servers.')) for a in args)


def has_trust_arg(args):
    return any(a in ('--approve', '--no-approve', '-na', '--respect-workspace-trust', '--trust-workspace', '--yolo') or a.startswith('--respect-workspace-trust=')
               for a in args)


def has_permission_arg(args):
    exact = ('--permission-mode', '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions', '-a', '--ask-for-approval', '-s', '--sandbox',
             '--dangerously-bypass-approvals-and-sandbox', '--approval-mode', '--yolo', '--disable-approval', '--disable-sandbox', '--permission-profile')
    prefixes = ('--permission-mode=', '--ask-for-approval=', '--sandbox=', '--approval-mode=', '--permission-profile=')
    return any(a in exact or a.startswith(prefixes) for a in args)


def has_preflight(kind):
    return kind in ('pi', 'prime-agent', 'muse')


def timeout_s():
    return os.environ.get('PAIR_PREFLIGHT_TIMEOUT') or '90'


def last_lines(out, n):
    """The last n non-blank lines of out."""
    return [line for line in out.split('\n') if line.strip(' \t\n\r\f\v')][-n:]


def tool_line(tool):
    """(version, path) as the failure summary shows them: the tool's `--version` line, or ? when it has none, and where it is on PATH."""
    try:
        version = subprocess.run([tool, '--version'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, **TEXT)
        shown = version.stdout.rstrip('\n') if version.returncode == 0 else '?'
    except OSError:
        shown = '?'
    return shown, shutil.which(tool) or ''


def preflight_muse(args):
    kept, i = [], 0
    while i < len(args):
        if args[i] in ('--model', '--provider', '--contributor-mode', '--permission-profile'):
            if i + 1 < len(args):
                kept += [args[i], args[i + 1]]
            i += 2
        else:
            i += 1
    r = with_timeout(timeout_s(), ['muse', 'exec', *kept, 'Reply with exactly: OK'], cwd='/tmp', stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    out = r.stdout.rstrip('\n')
    if r.returncode == 0 and 'OK' in out:
        return 0
    sys.stderr.write('\n'.join(out.split('\n')[-5:]) + '\n')
    return 1


def retry_wait():
    """The pause before a preflight's second try."""
    time.sleep(env_float('PAIR_PREFLIGHT_RETRY_S', 3))


def preflight_pi(args):
    kept = [a for a in args if a not in ('--approve', '-a', '--no-approve', '-na')]
    out, code = '', 0
    for attempt in (1, 2):
        r = with_timeout(timeout_s(), ['pi', '--no-session', '--no-approve', '--no-context-files', '--no-skills', '-p', *kept, 'Reply with exactly: OK'],
                         stderr=subprocess.STDOUT)
        out, code = r.stdout.rstrip('\n'), r.returncode
        if code == 0 and 'OK' in out:
            return 0
        # A provider can fail one request and answer the next, so a failure is retried once before it counts.
        if attempt == 1:
            retry_wait()
    # The master's PATH can hold an older pi than the pane's, so name it.
    version, where = tool_line('pi')
    sys.stderr.write(f'pi {version} at {where}, twice: ')
    sys.stderr.write({124: f'timed out after {timeout_s()}s ', 0: 'answered without OK '}.get(code, f'exit {code} '))
    sys.stderr.write(''.join(f'{line} ' for line in last_lines(out, 3)))
    if not out.strip(' \t\n\r\f\v'):
        sys.stderr.write('(no output)')
    return 1


def preflight_prime(args):
    scratch = tempfile.mkdtemp()
    out, code = '', 0
    try:
        for attempt in (1, 2):
            r = with_timeout(os.environ.get('PAIR_PREFLIGHT_TIMEOUT') or '90',
                             ['prime-agent', *args, '--cwd', scratch, '--no-session', '--no-context-files', '--no-skills', '--no-prompt-templates',
                              '--print', '--mode', 'text', 'Reply with exactly: OK. Do not use tools.'],
                             stderr=subprocess.STDOUT, env={**os.environ, 'HERDR_ENV': '0'})
            out, code = r.stdout.rstrip('\n'), r.returncode
            if code == 0 and any(re.fullmatch(r'[ \t\n\r\f\v]*OK[ \t\n\r\f\v]*', line) for line in out.split('\n')):
                return 0
            if attempt == 1:
                retry_wait()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    version, where = tool_line('prime-agent')
    sys.stderr.write(f'prime-agent {version} at {where}, twice: exit {code} ')
    sys.stderr.write(''.join(f'{line} ' for line in last_lines(out, 3)))
    sys.stderr.write('\n')
    return 1


def preflight_kind(kind, args):
    """Prove an agent of this kind answers before it is started: 0 when it does, or has no preflight; 1 with the reason on stderr."""
    if not has_preflight(kind):
        return 0
    if kind == 'prime-agent':
        return preflight_prime(args)
    if kind == 'muse':
        return preflight_muse(args)
    return preflight_pi(args)
