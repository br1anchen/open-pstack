import os
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


def proc_root():
    """Where processes are read from: /proc, or the tree PAIR_PROC_ROOT names (tests build one). The one reader of the variable: a caller asks
    once and passes the answer on."""
    return os.environ.get('PAIR_PROC_ROOT') or '/proc'


def _text(path):
    with open(path, encoding='utf-8', errors='replace', newline='') as f:
        return f.read()


def read(pid, root):
    """The process with this pid, or None when it is not there or vanishes while it is read."""
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


def of_user(uid, root):
    """Every process the user runs, as read now; a process that is gone before it is read is left out."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = (read(int(n), root) for n in names if n.isdigit())
    return [p for p in found if p is not None and p.uid == uid]


def environ(pid, root):
    """The process's environment as KEY=value lines, or None when it cannot be read."""
    try:
        return _text(f'{root}/{pid}/environ').replace('\0', '\n').split('\n')
    except OSError:
        return None
