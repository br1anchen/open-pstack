"""step and finish: the two kitchen commands that run the repo's checks.

A step is recorded only once the touched profiles' fast gates and the policy check pass on it; finish lets a done report through only once the brief's
Verify commands, rerun by the kitchen, exit 0. Both start their checks detached and return within KITCHEN_STEP_SLICE_S seconds (default 100); running the
same command again waits on the same job. A detached run is worker.py started in a session of its own with its argv,
PSTACK_KITCHEN_RUN, its working directory, standard input from /dev/null and its output in the job's log file; the Python parent never waits for it, and it
writes the job's `rc` file when done. A step's job directory holds out.N (one log per run) and `out` (a link to the latest), rc, consumed, classify.json,
profiles and base; finish's holds out, rc, consumed and logs/.

kitchen.py is reached through kpy (its classify_change function, in process); worker.py runs it as a child process, since it
outlives this process. None of the in-process calls writes os.environ (kitchen.py :745, :771, :1023, :1038 belong to `gate` and `brief-verify`, which only
worker.py runs)."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from pstack_core import git
from pstack_core.commands import report as core_report
from pstack_core.commands import step as core_step
from pstack_core.errors import Die, Exit, StepRefused, ToolFailed, open_notes_refusal
from pstack_core.numbers import env_float, env_int, exit_code, int_or_none
from pstack_core.panes import require_herdr
from pstack_core.replies import file_mtime
from pstack_core.review import range_floor
from pstack_core.steplog import StepLog
from pstack_core.store import (
    event,
    expected_report,
    field,
    header_field,
    pair_file,
    read,
    read_text,
    unit_name,
)
from pstack_core.wait import default_signals

from . import kpy
from .classify import brief_kitchen
from .units import recorded_head
from .wait import poll_sleep

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'worker.py')


def verify_job(ctx, store, brief, head):
    """Where finish keeps the run of the brief's Verify block at commit head: the kitchen variant's own function, which report_ready reads too."""
    return ctx.variant.verify_job(ctx, store, brief, head)


def ignore_interrupts():
    """A command the shell starts with `&` while job control is off (a script) has SIGINT and SIGQUIT ignored, and the gate run was one: a ^C at the
    terminal that ended the step does not reach it."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)


def detached(command, argv, log, store):
    """worker.py COMMAND ARGV... with its output in log and PSTACK_KITCHEN_RUN set to the run's slug, in a session of its own, never waited for."""
    env = {**os.environ, 'PSTACK_KITCHEN_RUN': read(store, 'slug')}
    sys.stdout.flush()
    sys.stderr.flush()
    with open(log, 'w', encoding='utf-8') as out:
        subprocess.Popen(
            [sys.executable, '-I', WORKER, command, *argv],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            preexec_fn=ignore_interrupts,
        )


def slice_deadline():
    return int(time.time()) + env_int('KITCHEN_STEP_SLICE_S', 100)


def past(deadline):
    return int(time.time()) >= deadline


def remove_path(path):
    """Remove a file, link or directory tree, and nothing when it is not there."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def point_at(ctx, link, target):
    """Make link a symbolic link to target, replacing what was there."""
    fresh = f'{link}.{os.getpid()}'
    try:
        remove_path(fresh)
        os.symlink(target, fresh)
        os.replace(fresh, link)
    except OSError as e:
        remove_path(fresh)
        sys.stderr.write(
            f'{ctx.prog}: cannot point {link} at {target}: {os.strerror(e.errno)}\n'
        )


def queue_line(log):
    """What the step says of a gate still waiting for a machine slot or the heavy slot: the log's last `kitchen: queued|waited` line."""
    try:
        lines = [
            l
            for l in read_text(log).split('\n')
            if re.match(r'kitchen: (queued|waited) ', l)
        ]
    except OSError:
        return ''
    if not lines:
        return ''
    last = lines[-1]
    if re.fullmatch(r'kitchen: queued .* heavy slots?', last):
        return f'gates: {last[len("kitchen: ") :]}, behind heavy gates from other kitchens\n'
    if last.startswith('kitchen: queued '):
        return f'gates: {last[len("kitchen: ") :]}, behind gates from other kitchens\n'
    return ''


def wait_for_rc(job):
    """Waits for the job's rc file for up to a slice: whether it is there."""
    deadline = slice_deadline()
    while not os.path.isfile(f'{job}/rc'):
        if past(deadline):
            return False
        poll_sleep(env_float('KITCHEN_STEP_POLL_S', 2))
    return True


def check_checkout(root, sha, full):
    """The gates run on the checkout, so they prove HEAD: a step names the commit they ran on, and a tracked file edited since would be proven too."""
    if git.run(root, 'rev-parse', 'HEAD').stdout.rstrip('\n') != full:
        raise StepRefused(
            f'step {sha}: HEAD is {git.run(root, "rev-parse", "--short", "HEAD").stdout.rstrip(chr(10))}; commit the step and record HEAD'
        )
    if git.run(root, 'status', '--porcelain', '--untracked-files=no').stdout.rstrip(
        '\n'
    ):
        raise StepRefused(
            f'step {sha}: the tree has uncommitted changes to tracked files; commit or stash them first'
        )


def step_base(store, root, unit, log, sha, full):
    """Where the step's range starts: the last recorded step, else the head recorded at dispatch; for a commit made before the dispatch (a resumed
    unit) its own parent; measured from where the histories fork after a rewritten base, and never from before the branch leaves trunk."""
    last = log.last()
    base = last.sha if last else recorded_head(store, unit)
    if not base:
        raise StepRefused(f'no base for {unit}; was it dispatched?')
    counted = git.run(root, 'rev-list', '--count', f'{base}..{full}', quiet=True)
    if (
        int(counted.stdout)
        if counted.returncode == 0 and counted.stdout.strip().isdigit()
        else 0
    ) == 0:
        parent = git.run(root, 'rev-parse', '--verify', '--quiet', f'{full}~1')
        if parent.returncode != 0:
            raise StepRefused(f'{sha} has no parent to check it against')
        base = parent.stdout.rstrip('\n')
    forked = git.run(root, 'merge-base', base, full, quiet=True)
    base = forked.stdout.rstrip('\n') if forked.returncode == 0 else base
    return range_floor(root, base, full)[0]


def start_gates(ctx, store, root, brief, job, base, full):
    """Starts the detached gate run for the step unless a job that is still unconsumed holds it: classifies the range, writes the job's files and
    hands worker.py the run."""
    if os.path.isdir(job) and not os.path.isfile(f'{job}/consumed'):
        return
    cfg = brief_kitchen(brief)
    # every run of the gates has its own log (out.1, out.2, ...), and `out` points at the latest: a path an event named keeps its run's text
    for leftover in ('rc', 'consumed', 'classify.json', 'profiles', 'base'):
        remove_path(f'{job}/{leftover}')
    os.makedirs(job, exist_ok=True)
    run = len([n for n in os.listdir(job) if re.match(r'out\.[0-9]', n)]) + 1
    point_at(ctx, f'{job}/out', f'out.{run}')
    try:
        found = kpy.kitchen_py.classify_change(
            kpy.invocation(store, root, cfg[1] if cfg else None), base=base, head=full
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise ToolFailed(e.code) from None
    with open(f'{job}/classify.json', 'w') as f:
        f.write(json.dumps(found, indent=2) + '\n')
    with open(f'{job}/profiles', 'w') as f:
        f.write(''.join(f'{name}\n' for name in found['profiles']))
    with open(f'{job}/base', 'w') as f:
        f.write(base + '\n')
    detached(
        'step',
        [root, str(kpy.KITCHEN_PY), job, base, full, cfg[1] if cfg else ''],
        f'{job}/out.{run}',
        store,
    )


def gates_running(store, sha, summary, full, job):
    sys.stdout.write(
        f'gates: still running for {full[:9]} ({int(time.time()) - file_mtime(f"{job}/base")}s so far; log: {job}/out)\n'
    )
    sys.stdout.write(queue_line(f'{job}/out'))
    sys.stdout.write(
        f'next: this step is not recorded yet; do not end your turn. Run the same command again now, in the foreground, until it records: '
        f'kitchen.sh step {store} {sha} "{summary}"\n'
    )


@dataclass(frozen=True)
class GateRun:
    """What a finished gate job says: whether it passed, the state recorded for the step (a step with no profile ran no gate: none, never pass), the
    profiles it ran and the classification of the range."""

    passed: bool
    state: str
    profiles: list
    classified: str


def settle_gates(store, unit, full, job):
    """Consumes the finished job and appends the step's row to gates.tsv."""
    open(f'{job}/consumed', 'w').close()
    rc = int_or_none(read_text(f'{job}/rc').rstrip('\n'))
    classified = read_text(f'{job}/classify.json').rstrip('\n')
    profiles = (
        [l for l in read_text(f'{job}/profiles').split('\n')][:-1]
        if read_text(f'{job}/profiles')
        else []
    )
    state = 'fail' if rc != 0 else 'none' if not profiles else 'pass'
    os.makedirs(f'{store}/steps', exist_ok=True)
    with open(f'{store}/gates.tsv', 'a') as f:
        f.write(
            f'{int(time.time())}\t{unit}\t{full[:9]}\t{state}\t{",".join(profiles)}\n'
        )
    return GateRun(rc == 0, state, profiles, classified)


def report_failed_gates(store, unit, brief, full, job, summary):
    """The failing log and what to do next: the step ends with status 2, and with two failures in a row the master hears of it as a blocked report."""
    sys.stdout.write(read_text(f'{job}/out'))
    event(
        store,
        'sidekick',
        'gate-fail',
        brief,
        f'{full[:9]} {job}/{os.readlink(f"{job}/out")}',
    )
    fails = 0
    for row in read_text(f'{store}/gates.tsv').split('\n'):
        cols = row.split('\t')
        if len(cols) > 1 and cols[1] == unit:
            fails = fails + 1 if len(cols) > 3 and cols[3] == 'fail' else 0
    if fails >= 2:
        sys.stdout.write(
            f'next: {fails} failed checks in a row; write the report as blocked with the logs under Questions, then kitchen.sh finish\n'
        )
    else:
        sys.stdout.write(
            f'next: fix it in a new commit and run kitchen.sh step {store} <new-sha> "{summary}" again\n'
        )
    raise ToolFailed(2)


def report_passed_gates(store, brief, job, run):
    """What a passing step says: the profiles that ran, or that none covers the change, and a pass that needed a rerun under load, which retro uses
    to show which suites flake on a loaded machine."""
    if run.state == 'none':
        unmapped = ', '.join(json.loads(run.classified)['unmapped']) or 'these changes'
        sys.stdout.write(f'gates: none ran; no profile covers {unmapped}\n')
        return
    sys.stdout.write(f'gates: pass ({",".join(run.profiles)})\n')
    names = '\n'.join(
        m.group(1)
        for m in (
            re.match(r'flaky under load: ([^ ]*) ', l)
            for l in read_text(f'{job}/out').split('\n')
        )
        if m
    )
    flaky = ','.join(sorted(set(n for n in names.split('\n') if n)))
    if flaky:
        sys.stdout.write(
            f'flaky under load: {flaky} failed, then passed on a rerun (see {job}/out)\n'
        )
        event(store, 'sidekick', 'gate-flaky', brief, flaky)


def step(ctx, args):
    default_signals()
    if len(args) < 3:
        raise Die(
            'usage: kitchen.sh step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]'
        )
    store, sha, summary = args[0], args[1], args[2]
    pair_file(store)
    brief = field(store, 'dispatch', 'brief')
    if not brief:
        raise Die('no brief dispatched; a step belongs to a running brief')
    unit = unit_name(brief)
    root = read(store, 'git_root')
    found = git.run(root, 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}')
    full = found.stdout.rstrip('\n')
    if found.returncode != 0:
        raise Die(f'not a commit: {sha}; commit the step first')
    log = StepLog(store, brief)
    # A SHA this unit already recorded proves nothing new: no gates, no job directory touched, no row, no event (the core step only merges --resolves
    # into the row it has). It is looked up first, so an earlier commit can be re-recorded whatever HEAD and the tree are now.
    if log.has(full):
        return core_step.step(ctx, args)
    resolves = core_step.resolves_arg(args[3:])
    if error := core_step.malformed_resolves(resolves):
        raise Die(error)
    if error := core_step.unpublished_resolves(store, unit, resolves):
        raise Die(error)
    left = core_step.unresolved_notes(store, brief, resolves)
    if left:
        raise open_notes_refusal(ctx.prog, left)
    check_checkout(root, sha, full)
    base = step_base(store, root, unit, log, sha, full)
    job = f'{store}/steps/jobs/{unit}-{full[:12]}'
    start_gates(ctx, store, root, brief, job, base, full)
    if not wait_for_rc(job):
        gates_running(store, sha, summary, full, job)
        raise Exit(4)
    run = settle_gates(store, unit, full, job)
    if not run.passed:
        report_failed_gates(store, unit, brief, full, job, summary)
    report_passed_gates(store, brief, job, run)
    return core_step.step(ctx, args)


def verify_running(store, report, head, job):
    sys.stdout.write(
        f"verify: the brief's Verify commands are still running at {head[:9]} (log: {job}/out)\n"
    )
    sys.stdout.write(
        'next: the report is not sent yet; do not end your turn. Run the same command again now, in the foreground, until it sends: '
        f'kitchen.sh finish {store} {report}\n'
    )


def require_head_checked_out(root, report, brief):
    """The full sha of the report's `head:`, once the checkout is at it with nothing uncommitted: the kitchen reruns the brief's Verify commands at the
    unit's tip, so a head that is no commit, another HEAD or a dirty tree is refused with what to do."""
    found = git.run(
        root,
        'rev-parse',
        '--verify',
        '--quiet',
        f'{header_field(report, "head")}^{{commit}}',
    )
    if found.returncode != 0:
        raise StepRefused(
            f"{os.path.basename(report)}: head: {header_field(report, 'head')} is not a commit; name the unit's last commit and finish again"
        )
    head = found.stdout.rstrip('\n')
    current = git.run(root, 'rev-parse', 'HEAD')
    if current.returncode != 0:
        raise ToolFailed(exit_code(current.returncode))
    cur = current.stdout.rstrip('\n')
    if cur != head:
        raise StepRefused(
            f"the checkout is at {cur[:9]}, not the report's head: {head[:9]}; the kitchen reruns the brief's Verify commands at the unit's tip, so check out that "
            'head (or fix head:) and finish again'
        )
    if not header_field(brief, 'commit').startswith('no'):
        # a plain assignment: git failing (a DamagedStore index) ends the command with git's status, before any job directory exists
        listed = git.run(root, 'status', '--porcelain', '--untracked-files=normal')
        if listed.returncode != 0:
            raise ToolFailed(exit_code(listed.returncode))
        dirty = listed.stdout.rstrip('\n')
        if dirty:
            raise StepRefused(
                f"the checkout holds work the unit's head does not:\n{dirty}\nThe kitchen reruns the brief's Verify commands at the unit's tip. Commit this as a "
                'step (kitchen.sh step) and name it in head:, or remove it, then finish again'
            )
    return head


def finish(ctx, args):
    require_herdr()
    default_signals()
    if len(args) != 2:
        return core_report.finish(ctx, args)
    store = args[0]
    report = os.path.realpath(args[1])
    pair_file(store)
    brief = field(store, 'dispatch', 'brief')
    # Everything but the dispatched unit's done report, and a report the pair's checks refuse anyway (no head:, open notes), goes straight on.
    if (
        not os.path.isfile(report)
        or header_field(report, 'status') != 'done'
        or not brief
        or report != expected_report(store, brief)
        or not core_report.pair_report_ready(ctx, store, report)
    ):
        return core_report.finish(ctx, args)
    wanted = header_field(report, 'head')
    if wanted == '' or wanted.startswith('<'):
        return core_report.finish(ctx, args)
    root = read(store, 'git_root')
    head = require_head_checked_out(root, report, brief)
    unit = unit_name(brief)
    job = verify_job(ctx, store, brief, head)
    if (
        os.path.isfile(f'{job}/rc')
        and read_text(f'{job}/rc').rstrip('\n') != '0'
        and os.path.isfile(f'{job}/consumed')
    ):
        remove_path(job)
    if not os.path.isdir(job):
        os.makedirs(job, exist_ok=True)
        cfg = brief_kitchen(brief)
        base = recorded_head(store, unit)
        detached(
            'finish',
            [root, str(kpy.KITCHEN_PY), brief, job, cfg[1] if cfg else '', base],
            f'{job}/out',
            store,
        )
    if not wait_for_rc(job):
        verify_running(store, args[1], head, job)
        raise Exit(4)
    open(f'{job}/consumed', 'w').close()
    rc = read_text(f'{job}/rc').rstrip('\n')
    sys.stdout.write(read_text(f'{job}/out'))
    if int_or_none(rc) != 0:
        event(store, 'sidekick', 'finish-verify-fail', brief, head[:9])
        sys.stdout.write(
            'next: the master did not get this report. Fix what failed in a new commit, record it with kitchen.sh step, name it in head:, and finish again; if the '
            'commands cannot pass, write the report as blocked with this output under Questions, then kitchen.sh finish\n'
        )
        raise Exit(2)
    event(store, 'sidekick', 'finish-verify-pass', brief, head[:9])
    return core_report.finish(ctx, args)
