"""What the pair hands an agent when it starts one, and the preflights that prove an agent answers first: the native arguments for a
permission mode, trust and MCP, the has_* tests for arguments the caller passed itself, and the print-mode preflights of pi, Muse and Prime."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .prime import with_timeout
from .review import jq_raw
from .wait import bash_sleep, jq_text

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
    """(name lines, jq's status): the MCP servers codex lists where it runs that are not disabled, as the bash's jq prints them. jq stops at
    an element it cannot index (status 5) and fails on text it cannot read (also status 5; jq 1.7 and 1.8 both use 5 for runtime and parse
    errors, and the callers only test for non-zero); under pipefail that status is the pipeline's."""
    try:
        r = with_timeout('20', ['codex', 'mcp', 'list', '--json'], cwd=cwd or '.', stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return [], 0
    text = r.stdout.decode('utf-8', 'surrogateescape')
    try:
        listed = json.loads(text) if text.strip() else None
    except ValueError:
        return [], 5
    items = listed if isinstance(listed, list) else list(listed.values()) if isinstance(listed, dict) else []
    names, status = [], 0
    for item in items:
        # jq -r '.[]? | select(.enabled != false) | .name': an element it cannot index ends the stream
        if item is not None and not isinstance(item, dict):
            status = 5
            break
        if item is not None and item.get('enabled') is False:
            continue
        names.extend(jq_text(None if item is None else item.get('name')).split('\n'))
    return names, status


def mcp_args(kind, cwd=None):
    """(arguments, the bash's status): the loop that filters the names returns the status of its last name, and a jq failure upstream of it
    is the pipeline's status when the loop's is 0."""
    if kind == 'claude':
        return ['--strict-mcp-config', '--mcp-config', f'{PAIR_DIR}/../references/no-mcp.json'], 0
    if kind == 'codex':
        names, jq_status = codex_mcp_names(cwd)
        accepted = [re.fullmatch(r'[A-Za-z0-9_-]+', n) is not None for n in names]
        loop_status = 0 if not names or accepted[-1] else 1
        args = ['--disable', 'plugins', *[arg for n, ok in zip(names, accepted) if ok for arg in ('-c', f'mcp_servers.{n}.enabled=false')]]
        return args, loop_status or jq_status
    return [], 0


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


def text_of(raw):
    """A command substitution's text: trailing newlines gone."""
    return raw.decode('utf-8', 'surrogateescape').rstrip('\n')


def last_lines(out, n):
    """printf '%s\\n' "$out" | grep -v '^[[:space:]]*$' | tail -N, as separate lines."""
    return [line for line in out.split('\n') if line.strip(' \t\n\r\f\v')][-n:]


def tool_line(tool):
    """(version, path) as the failure summary shows them: `$(tool --version 2>/dev/null || printf '?')` and `$(command -v tool)`."""
    try:
        version = subprocess.run([tool, '--version'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        shown = text_of(version.stdout) if version.returncode == 0 else '?'
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
    out = text_of(r.stdout)
    if r.returncode == 0 and 'OK' in out:
        return 0
    sys.stderr.write('\n'.join(out.split('\n')[-5:]) + '\n')
    return 1


def preflight_pi(args):
    kept = [a for a in args if a not in ('--approve', '-a', '--no-approve', '-na')]
    out, code = '', 0
    for attempt in (1, 2):
        r = with_timeout(timeout_s(), ['pi', '--no-session', '--no-approve', '--no-context-files', '--no-skills', '-p', *kept, 'Reply with exactly: OK'],
                         stderr=subprocess.STDOUT)
        out, code = text_of(r.stdout), r.returncode
        if code == 0 and 'OK' in out:
            return 0
        # A provider can fail one request and answer the next, so a failure is retried once before it counts.
        if attempt == 1:
            bash_sleep(os.environ.get('PAIR_PREFLIGHT_RETRY_S') or '3')
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
            out, code = text_of(r.stdout), r.returncode
            if code == 0 and any(re.fullmatch(r'[ \t\n\r\f\v]*OK[ \t\n\r\f\v]*', line) for line in out.split('\n')):
                return 0
            if attempt == 1:
                bash_sleep(os.environ.get('PAIR_PREFLIGHT_RETRY_S') or '3')
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


def lines(values):
    """The lines a bash helper prints for a list of arguments."""
    return ''.join(f'{v}\n' for v in values)


def status(answer):
    return 0 if answer else 1


def emit(values, code=0):
    sys.stdout.write(lines(values))
    return code


COMMANDS = {
    '__permission_args': lambda ctx, a: emit(permission_args(a[0], a[1])),
    '__asks_no_approvals': lambda ctx, a: status(asks_no_approvals(a[0], a[1])),
    '__trust_args': lambda ctx, a: emit(trust_args(a[0])),
    '__mcp_args': lambda ctx, a: emit(*mcp_args(a[0], a[1] if len(a) > 1 else None)),
    '__has_mcp_arg': lambda ctx, a: status(has_mcp_arg(a)),
    '__has_trust_arg': lambda ctx, a: status(has_trust_arg(a)),
    '__has_permission_arg': lambda ctx, a: status(has_permission_arg(a)),
    '__has_preflight': lambda ctx, a: status(has_preflight(a[0])),
    '__preflight_kind': lambda ctx, a: preflight_kind(a[0], a[1:]),
}
