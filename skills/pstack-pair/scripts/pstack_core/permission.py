import json
import os
import re
import subprocess

from .errors import Exit

DEPTH_LIMIT = 12


def ps(*args):
    """ps with stderr dropped: (exit code, stdout as the bash reads it, trailing newlines gone)."""
    r = subprocess.run(['ps', *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return r.returncode, r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')


def sed_n(text, pattern, repl):
    """sed -nE 's/pattern/repl/p' of text, as a command substitution leaves it: the substituted lines only."""
    out = []
    for line in text.split('\n'):
        done, n = re.subn(pattern, repl, line, count=1)
        if n:
            out.append(done)
    return '\n'.join(out).rstrip('\n')


def basename(path):
    """basename "$path", run as the bash runs it: (exit code, text). Its stderr, when it fails, stays on this process's stderr."""
    r = subprocess.run(['basename', path], stdout=subprocess.PIPE, check=False)
    return r.returncode, r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')


def agent_mode(comm, args):
    """The mode an agent process's own command line states, or ''."""
    if comm == 'claude':
        if '--dangerously-skip-permissions' in args:
            return 'bypassPermissions'
        if '--permission-mode' in args:
            return sed_n(args, r'.*--permission-mode[= ]([A-Za-z]+).*', r'\1')
    else:
        if '--dangerously-bypass-approvals-and-sandbox' in args:
            return 'bypassPermissions'
        if any(f in args for f in ('-a never', '--ask-for-approval never', '--ask-for-approval=never')):
            return 'dontAsk'
        if any(f in args for f in ('-a on-request', '--ask-for-approval on-request', '--ask-for-approval=on-request')):
            return 'auto'
    return ''


def transcript_mode(path):
    """grep -o '"permissionMode":"[A-Za-z]*"' | tail -1 | sed -E 's/.*:"([A-Za-z]+)"/\\1/' of the transcript: (pipeline status, mode).
    Under pipefail a transcript with no such field fails with grep's status 1."""
    with open(path, 'rb') as f:
        found = re.findall(rb'"permissionMode":"[A-Za-z]*"', f.read())
    if not found:
        return 1, ''
    return 0, re.sub(r'.*:"([A-Za-z]+)"', r'\1', found[-1].decode('utf-8', 'surrogateescape'), count=1)


def settings_mode(path):
    """The settings file's permissions.defaultMode; a missing file, a file that does not parse and a missing key all give ''."""
    try:
        with open(path, 'rb') as f:
            mode = json.load(f)['permissions']['defaultMode']
    except (OSError, ValueError, KeyError, TypeError):
        return ''
    return mode if isinstance(mode, str) else ''


def detect(pid, cwd, home, session, strict, ps_run=ps):
    """The master's permission mode, in Claude Code vocabulary: auto | acceptEdits | bypassPermissions | manual | dontAsk | plan.
    Sources in order: the launching command line of the agent process above the shell with pid `pid`, the current Claude session
    transcript, settings defaultMode, then auto.

    strict is the caller's errexit: run as a command of its own the bash stops with the failing command's status where an assignment
    from a failing command substitution sits; inside $( ) bash clears errexit there and goes on with an empty value."""
    mode, depth = '', 0

    def value(code, text):
        if code != 0 and strict:
            raise Exit(code)
        return text

    while pid > 1 and depth < DEPTH_LIMIT:
        code, out = ps_run('-o', 'ppid=', '-p', str(pid))
        ppid = out.replace(' ', '')
        if code != 0 or ppid == '':
            break
        code, out = ps_run('-o', 'comm=', '-p', str(pid))
        code, comm = basename(out)
        comm = value(code, comm)
        code, args = ps_run('-o', 'args=', '-p', str(pid))
        args = value(code, args)
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
            code, found = transcript_mode(transcript)
            mode = value(code, found)
    if mode == '':
        for f in (f'{cwd}/.claude/settings.local.json', f'{cwd}/.claude/settings.json', f'{home}/.claude/settings.local.json', f'{home}/.claude/settings.json'):
            if not os.path.isfile(f):
                continue
            mode = settings_mode(f)
            if mode != '':
                break
    return mode or 'auto'


def caller_pid():
    """The pid of the shell that ran pycore, which pycore puts in PAIR_SHELL_PID (the bash $$)."""
    return int(os.environ['PAIR_SHELL_PID'])


def logical_cwd():
    """The bash's $PWD: the inherited PWD while it names the current directory, else the physical one."""
    pwd = os.environ.get('PWD', '')
    try:
        if pwd.startswith('/') and os.path.samefile(pwd, '.'):
            return pwd
    except OSError:
        pass
    return os.getcwd()


def mode_of_this_shell(strict):
    return detect(caller_pid(), logical_cwd(), os.environ['HOME'], os.environ.get('CLAUDE_CODE_SESSION_ID', ''), strict)


def permission(ctx, args):
    print(mode_of_this_shell(True))
    return 0


def helper(ctx, args):
    print(mode_of_this_shell(False))
    return 0
