import os
import signal
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Proc:
    """One process as /proc shows it. argv is the whole command line; comm is the kernel's name for the program, cut at 15 characters."""

    pid: int
    ppid: int
    uid: int
    comm: str
    argv: tuple[str, ...]
    elapsed: int

    @property
    def args(self):
        return ' '.join(self.argv)


PS_TIMEOUT_S = 10
DEFAULT_ROOT = '/proc'


def proc_root():
    """Where processes are read from: /proc, or the tree PAIR_PROC_ROOT names (tests build one). The one reader of the variable: a caller asks
    once and passes the answer on."""
    return os.environ.get('PAIR_PROC_ROOT') or DEFAULT_ROOT


def ps_host(root):
    """Whether processes at `root` are read through `ps`: macOS has no /proc, so the default root means ps there. A tree PAIR_PROC_ROOT
    names is always read as files."""
    return sys.platform == 'darwin' and root == DEFAULT_ROOT


def readable(root):
    """Whether there are processes to read at `root`: a /proc tree, or ps."""
    return os.path.isdir(root) or ps_host(root)


def _ps(*args):
    """What `ps` prints for `args` as text, or None when it cannot run, times out or fails."""
    try:
        done = subprocess.run(
            ['ps', *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=PS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _comm(path):
    """The kernel's name for a program from the path ps shows: its base name, cut at 15 characters."""
    return path.rsplit('/', 1)[-1][:15]


def _text(path):
    with open(path, encoding='utf-8', errors='replace', newline='') as f:
        return f.read()


def read(pid, root):
    """The process with this pid, or None when it is not there or vanishes while it is read."""
    if ps_host(root):
        return read_ps(pid)
    base = f'{root}/{pid}'
    try:
        stat = _text(f'{base}/stat')
        comm = _text(f'{base}/comm').rstrip('\n')
        cmdline = _text(f'{base}/cmdline')
        status = _text(f'{base}/status')
        uptime = float(_text(f'{root}/uptime').split()[0])
        # the program name in stat may hold spaces and brackets, so the fields count from the last bracket: state is the first after it
        fields = stat[stat.rindex(')') + 2 :].split()
        ppid, started = int(fields[1]), int(fields[19])
        uid = next(
            int(line.split()[2])
            for line in status.split('\n')
            if line.startswith('Uid:')
        )
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    argv = (
        tuple(
            cmdline.split('\0')[:-1] if cmdline.endswith('\0') else cmdline.split('\0')
        )
        if cmdline
        else ()
    )
    return Proc(
        pid,
        ppid,
        uid,
        comm,
        argv,
        max(0, int(uptime - started / os.sysconf('SC_CLK_TCK'))),
    )


def alive(pid):
    """True when `pid` (a number, or the text of one) is a running process. Pids 1 and below are never alive: kill(0) and kill(-1) signal whole groups."""
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 1:
        return False
    try:
        os.kill(number, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def read_ps(pid):
    """`read` through ps: the same fields, argv as ps shows the command line (split at single spaces, which `args` joins back)."""
    row = _ps('-ww', '-o', 'ppid=,uid=,etime=,comm=', '-p', str(pid))
    args = _ps('-ww', '-o', 'args=', '-p', str(pid))
    if not row or args is None:
        return None
    fields = row.strip().split(None, 3)
    if len(fields) < 4 or not (fields[0] + fields[1]).isdigit():
        return None
    elapsed = etime_seconds(fields[2])
    if elapsed is None:
        return None
    text = args.rstrip('\n')
    return Proc(
        pid,
        int(fields[0]),
        int(fields[1]),
        _comm(fields[3]),
        tuple(text.split(' ')) if text else (),
        elapsed,
    )


def of_user_ps(uid):
    """`of_user` through ps: two listings merged by pid, so a process that starts or ends between them is left out."""
    rows = _ps('-A', '-ww', '-o', 'pid=,ppid=,uid=,etime=,comm=')
    args = _ps('-A', '-ww', '-o', 'pid=,args=')
    if rows is None or args is None:
        return []
    argv = {}
    for line in args.splitlines():
        pid, _, text = line.strip().partition(' ')
        if pid.isdigit():
            argv[int(pid)] = tuple(text.split(' ')) if text else ()
    found = []
    for line in rows.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 5 or not (fields[0] + fields[1] + fields[2]).isdigit():
            continue
        pid, elapsed = int(fields[0]), etime_seconds(fields[3])
        if int(fields[2]) == uid and elapsed is not None and pid in argv:
            found.append(
                Proc(pid, int(fields[1]), uid, _comm(fields[4]), argv[pid], elapsed)
            )
    return found


def environ_ps(pid):
    """`environ` through ps: `ps eww` prints the command line and then the environment, space separated, so a value holding a space
    splits into more than one entry. None when ps shows no environment (a restricted system program, another user's process)."""
    shown = _ps('eww', '-o', 'command=', '-p', str(pid))
    args = _ps('-ww', '-o', 'args=', '-p', str(pid))
    if shown is None or args is None:
        return None
    command = args.rstrip('\n')
    text = shown.rstrip('\n')
    if not text.startswith(command) or text == command:
        return None
    return text[len(command) :].split(' ')[1:]


def of_user(uid, root):
    """Every process the user runs, as read now; a process that is gone before it is read is left out."""
    if ps_host(root):
        return of_user_ps(uid)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = (read(int(n), root) for n in names if n.isdigit())
    return [p for p in found if p is not None and p.uid == uid]


def environ(pid, root):
    """The process's environment as KEY=value lines, or None when it cannot be read."""
    if ps_host(root):
        return environ_ps(pid)
    try:
        return _text(f'{root}/{pid}/environ').replace('\0', '\n').split('\n')
    except OSError:
        return None


def etime_seconds(text):
    """Seconds in a ps elapsed time, [[dd-]hh:]mm:ss, or None when it is not one."""
    days, _, clock = text.rpartition('-')
    try:
        seconds = 0
        for part in clock.split(':'):
            seconds = seconds * 60 + int(part)
        return int(days or 0) * 86400 + seconds
    except ValueError:
        return None


def from_ps():
    """Every process `ps` lists, for a host without /proc: ps shows no environments, so the uid and argv stay empty, and comm is the
    program's base name. A line ps spells in no known way is left out."""
    try:
        listed = subprocess.run(
            ['ps', '-A', '-o', 'pid=,ppid=,etime=,comm='],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return []
    found = []
    for line in listed.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4 or not (fields[0] + fields[1]).isdigit():
            continue
        elapsed = etime_seconds(fields[2])
        if elapsed is not None:
            comm = fields[3].rsplit('/', 1)[-1]
            found.append(Proc(int(fields[0]), int(fields[1]), -1, comm, (), elapsed))
    return found


def descendants(procs, roots, skip):
    """The pids that descend from one of `roots` without passing through `skip` or its own descendants: the roots themselves and the pids
    in `skip` are left out."""
    children = {}
    for proc in procs:
        children.setdefault(proc.ppid, []).append(proc.pid)

    def below(pids):
        found = set()
        todo = list(pids)
        while todo:
            for kid in children.get(todo.pop(), []):
                if kid not in found:
                    found.add(kid)
                    todo.append(kid)
        return found

    return below(roots) - below(skip) - set(skip) - set(roots)


def end_when_parent_exits():
    """macOS has no PR_SET_PDEATHSIG: a thread blocks on a kqueue event for the parent's exit and then sends this process SIGTERM. A parent that
    is already gone ends it at once."""
    import select
    import threading

    parent = os.getppid()
    queue = select.kqueue()
    watch = select.kevent(
        parent,
        select.KQ_FILTER_PROC,
        select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
        select.KQ_NOTE_EXIT,
    )
    try:
        queue.control([watch], 0, 0)
    except ProcessLookupError:
        os.kill(os.getpid(), signal.SIGTERM)
        return

    def wait_for_parent():
        queue.control(None, 1, None)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=wait_for_parent, daemon=True).start()
