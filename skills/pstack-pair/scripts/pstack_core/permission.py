import json
import os
import re
import subprocess

from .store import TEXT, read_text

DEPTH_LIMIT = 12


def ps(*args):
    """ps with stderr dropped: (exit code, stdout without its trailing newlines)."""
    r = subprocess.run(['ps', *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, **TEXT)
    return r.returncode, r.stdout.rstrip('\n')


def agent_mode(comm, args):
    """The mode an agent process's own command line states, or ''."""
    if comm == 'claude':
        if '--dangerously-skip-permissions' in args:
            return 'bypassPermissions'
        if '--permission-mode' in args:
            found = re.search(r'--permission-mode[= ]([A-Za-z]+)', args)
            return found.group(1) if found else ''
    else:
        if '--dangerously-bypass-approvals-and-sandbox' in args:
            return 'bypassPermissions'
        if any(f in args for f in ('-a never', '--ask-for-approval never', '--ask-for-approval=never')):
            return 'dontAsk'
        if any(f in args for f in ('-a on-request', '--ask-for-approval on-request', '--ask-for-approval=on-request')):
            return 'auto'
    return ''


def transcript_mode(path):
    """The last "permissionMode" the transcript names, or ''."""
    found = re.findall(r'"permissionMode":"([A-Za-z]*)"', read_text(path))
    return found[-1] if found else ''


def settings_mode(path):
    """The settings file's permissions.defaultMode; a missing file, a file that does not parse and a missing key all give ''."""
    try:
        mode = json.loads(read_text(path))['permissions']['defaultMode']
    except (OSError, ValueError, KeyError, TypeError):
        return ''
    return mode if isinstance(mode, str) else ''


def detect(pid, cwd, home, session, ps_run=ps):
    """The master's permission mode, in Claude Code vocabulary: auto | acceptEdits | bypassPermissions | manual | dontAsk | plan.
    Sources in order: the launching command line of the agent process above the shell with pid `pid`, the current Claude session
    transcript, settings defaultMode, then auto.
"""
    mode, depth = '', 0

    while pid > 1 and depth < DEPTH_LIMIT:
        code, out = ps_run('-o', 'ppid=', '-p', str(pid))
        ppid = out.replace(' ', '')
        if code != 0 or ppid == '':
            break
        comm = os.path.basename(ps_run('-o', 'comm=', '-p', str(pid))[1])
        args = ps_run('-o', 'args=', '-p', str(pid))[1]
        if comm in ('node', 'bun'):
            if 'claude' in args:
                comm = 'claude'
            elif 'codex' in args:
                comm = 'codex'
        if comm in ('claude', 'codex'):
            mode = agent_mode(comm, args)
            break
        try:
            pid = int(ppid)
        except ValueError:
            break
        depth += 1
    if mode == '' and session:
        transcript = f'{home}/.claude/projects/{re.sub(r"[/.]", "-", cwd)}/{session}.jsonl'
        if os.path.isfile(transcript):
            mode = transcript_mode(transcript)
    if mode == '':
        for f in (f'{cwd}/.claude/settings.local.json', f'{cwd}/.claude/settings.json', f'{home}/.claude/settings.local.json', f'{home}/.claude/settings.json'):
            if not os.path.isfile(f):
                continue
            mode = settings_mode(f)
            if mode != '':
                break
    return mode or 'auto'


def caller_pid():
    """The pid of the shell that ran this process."""
    return os.getppid()


def logical_cwd():
    """The shell's $PWD: the inherited PWD while it names the current directory, else the physical one."""
    pwd = os.environ.get('PWD', '')
    try:
        if pwd.startswith('/') and os.path.samefile(pwd, '.'):
            return pwd
    except OSError:
        pass
    return os.getcwd()


def mode_of_this_shell():
    return detect(caller_pid(), logical_cwd(), os.environ['HOME'], os.environ.get('CLAUDE_CODE_SESSION_ID', ''))


def permission(ctx, args):
    print(mode_of_this_shell())
    return 0
