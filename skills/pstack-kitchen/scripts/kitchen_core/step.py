"""step and finish: the two kitchen commands that run the repo's checks. Ported from kitchen.sh at b632713 (cmd_step, verify_job and cmd_finish).

A step is recorded only once the touched profiles' fast gates and the policy check pass on it; finish lets a done report through only once the brief's
Verify commands, rerun by the kitchen, exit 0. Both start their checks detached and return within KITCHEN_STEP_SLICE_S seconds (default 100); running the
same command again waits on the same job. A detached run is a `bash -c` script started in a session of its own (the bash used setsid) with the script's argv,
PSTACK_KITCHEN_RUN, its working directory, standard input from /dev/null and its output in the job's log file; the Python parent never waits for it, and it
writes the job's `rc` file when done. A step's job directory holds out.N (one log per run) and `out` (a link to the latest), rc, consumed, classify.json,
profiles and base; finish's holds out, rc, consumed and logs/.

kitchen.py is reached through kpy (in process) for `--json classify`; the detached scripts run it as `python3 kitchen.py` exactly as the bash did, since they
outlive this process. None of the in-process calls writes os.environ (kitchen.py :745, :771, :1023, :1038 belong to `gate` and `brief-verify`, which only the
detached scripts run)."""

import os
import re
import signal
import subprocess
import sys
import time

from pstack_core.commands import report as core_report
from pstack_core.commands import step as core_step
from pstack_core.errors import Die, Exit
from pstack_core.replies import file_mtime
from pstack_core.review import awk_equal, range_floor, steps_file
from pstack_core.store import bash_int, event, expected_report, header_field, last_line, pair_file, read_text, readlink_f, unit_name

from . import kpy
from .classify import array_of, brief_kitchen, parse_json
from .jqx import exit_status, head_of, pair_field, pair_text
from .units import git
from .verify_state import shell_arithmetic
from .wait import plain_signals, poll_sleep

STEP_SCRIPT = '''
\t\t\tcd "$1" || exit 1
\t\t\trc=0
\t\t\twhile IFS= read -r p; do
\t\t\t\t[ -n "$p" ] || continue
\t\t\t\tpython3 "$2" --repo "$1" ${6:+--config "$6"} gate "$p" fast --role sidekick --base "$4" || rc=2
\t\t\tdone <"$3/profiles"
\t\t\tpython3 "$2" --repo "$1" ${6:+--config "$6"} policy --base "$4" --head "$5" || rc=2
\t\t\tprintf "%s\\n" "$rc" >"$3/rc"
\t\t'''

FINISH_SCRIPT = '''
\t\t\tcd "$1" || exit 1
\t\t\trc=0
\t\t\tpython3 "$2" --repo "$1" ${5:+--config "$5"} brief-verify "$3" --role sidekick --log-dir "$4/logs" ${6:+--base "$6"} || rc=$?
\t\t\tprintf "%s\\n" "$rc" >"$4/rc"
\t\t'''


def verify_job(ctx, store, brief, head):
    """Where finish keeps the run of the brief's Verify block at commit head: the kitchen variant's own function, which report_ready reads too."""
    return ctx.variant.verify_job(store, brief, head)


def ignore_interrupts():
    """A command the shell starts with `&` while job control is off (a script) has SIGINT and SIGQUIT ignored, and the gate run was one: a ^C at the
    terminal that ended the step does not reach it."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)


def detached(script, argv, log, store):
    """`PSTACK_KITCHEN_RUN="$(field "$store" .slug)" detached bash -c SCRIPT ARGV... >LOG 2>&1 </dev/null &` and `disown`."""
    env = {**os.environ, 'PSTACK_KITCHEN_RUN': pair_text(store, 'slug')}
    sys.stdout.flush()
    sys.stderr.flush()
    with open(log, 'wb') as out:
        subprocess.Popen(['bash', '-c', script, *argv], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True,
                         preexec_fn=ignore_interrupts)


def slice_deadline():
    # `$(( $(date +%s) + slice ))`: slice is a variable, whose value is itself evaluated as an expression (`5 == 5` adds 1, `2*3` adds 6)
    return shell_arithmetic(f"{int(time.time())} + ({os.environ.get('KITCHEN_STEP_SLICE_S') or '100'})")


def past(deadline):
    return int(time.time()) >= deadline


def not_zero(text):
    """`[ "$rc" -ne 0 ]`: a word that is no integer is an error, which reads as false."""
    n = bash_int(text)
    if n is None:
        sys.stdout.flush()
        sys.stderr.write(f'pair.sh: [: {text}: integer expected\n')
        return False
    return n != 0


def cat(path):
    with open(path, 'rb') as f:
        sys.stdout.write(f.read().decode('utf-8', 'surrogateescape'))


def queue_line(log):
    """What the step says of a gate still waiting for a machine slot or the heavy slot: the log's last `kitchen: queued|waited` line, as sed prints it."""
    try:
        lines = [l for l in read_text(log).split('\n') if re.match(r'kitchen: (queued|waited) ', l)]
    except OSError:
        return ''
    if not lines:
        return ''
    last = lines[-1]
    if re.fullmatch(r'kitchen: queued .* heavy slots?', last):
        return f'gates: {last[len("kitchen: "):]}, behind heavy gates from other kitchens\n'
    if last.startswith('kitchen: queued '):
        return f'gates: {last[len("kitchen: "):]}, behind gates from other kitchens\n'
    return ''


def cat_n(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'surrogateescape').rstrip('\n')


def step(ctx, args):
    plain_signals()
    if len(args) < 3:
        raise Die('usage: kitchen.sh step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]')
    store, sha = args[0], args[1]
    pair_file(store)
    brief = pair_field(store, 'dispatch', 'brief', check=True, empty=True)
    if not brief:
        raise Die('no brief dispatched; a step belongs to a running brief')
    unit = unit_name(brief)
    root = pair_text(store, 'git_root', check=True)
    status, full = git(root, 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}')
    full = full.rstrip('\n')
    if status != 0:
        raise Die(f'not a commit: {sha}; commit the step first')
    file = steps_file(store, brief)
    # A SHA this unit already recorded proves nothing new: no gates, no job directory touched, no row, no event (the core step only merges --resolves
    # into the row it has). It is looked up first, so an earlier commit can be re-recorded whatever HEAD and the tree are now.
    if os.path.isfile(file) and os.path.getsize(file) > 0 and any((row.split('\t')[1] if '\t' in row else row) == full for row in read_text(file).split('\n')):
        return core_step.step(ctx, args)
    # The gates run on the checkout, so they prove HEAD: a step names the commit they ran on, and a tracked file edited since would be proven too.
    if git(root, 'rev-parse', 'HEAD')[1].rstrip('\n') != full:
        raise Die(f'step {sha}: HEAD is {git(root, "rev-parse", "--short", "HEAD")[1].rstrip(chr(10))}; commit the step and record HEAD')
    if git(root, 'status', '--porcelain', '--untracked-files=no')[1].rstrip('\n'):
        raise Die(f'step {sha}: the tree has uncommitted changes to tracked files; commit or stash them first')
    if os.path.isfile(file) and os.path.getsize(file) > 0:
        tail = last_line(file)
        base = tail.split('\t')[1] if '\t' in tail else tail
    else:
        base = head_of(store, unit)
    if not base:
        raise Die(f'no base for {unit}; was it dispatched?')
    # A commit made before this brief was dispatched (a resumed unit) is the brief's own base, which leaves an empty range; check its own diff.
    counted = git(root, 'rev-list', '--count', f'{base}..{full}', quiet=True)
    if (int(counted[1]) if counted[0] == 0 and counted[1].strip().isdigit() else 0) == 0:
        parent = git(root, 'rev-parse', '--verify', '--quiet', f'{full}~1')
        if parent[0] != 0:
            raise Die(f'{sha} has no parent to check it against')
        base = parent[1].rstrip('\n')
    # A rewritten base (a squash, an amend) is measured from where the histories fork, so the step covers every change it carries.
    forked = git(root, 'merge-base', base, full, quiet=True)
    base = forked[1].rstrip('\n') if forked[0] == 0 else base
    # And never from before where the branch leaves trunk (a unit rebased onto a moved trunk, with no step recorded yet).
    base = range_floor(root, base, full)[0]
    job = f'{store}/steps/jobs/{unit}-{full[:12]}'
    cfg = brief_kitchen(brief)
    if not os.path.isdir(job) or os.path.isfile(f'{job}/consumed'):
        # every run of the gates has its own log (out.1, out.2, ...), and `out` points at the latest: a path an event named keeps its run's text
        for leftover in ('rc', 'consumed', 'classify.json', 'profiles', 'base'):
            subprocess.run(['rm', '-rf', f'{job}/{leftover}'], check=False)
        os.makedirs(job, exist_ok=True)
        run = len([n for n in os.listdir(job) if re.match(r'out\.[0-9]', n)]) + 1
        subprocess.run(['ln', '-sfn', f'out.{run}', f'{job}/out'], check=False)
        code, found, err = kpy.run(root, [*cfg, '--json', 'classify', '--base', base, '--head', full])
        if err:
            sys.stdout.flush()
            sys.stderr.write(err)
        if code != 0:
            raise Exit(code)
        text = found.rstrip('\n')
        with open(f'{job}/classify.json', 'w') as f:
            f.write(text + '\n')
        profiles = array_of(parse_json(text, 'kitchen.py classify output'), 'profiles', 'kitchen.py classify output')
        with open(f'{job}/profiles', 'w') as f:
            f.write(''.join(f'{name}\n' for name in profiles))
        with open(f'{job}/base', 'w') as f:
            f.write(base + '\n')
        detached(STEP_SCRIPT, ['kitchen-step', root, str(kpy.KITCHEN_PY), job, base, full, cfg[1] if cfg else ''], f'{job}/out.{run}', store)
    deadline = slice_deadline()
    while not os.path.isfile(f'{job}/rc'):
        if past(deadline):
            sys.stdout.write(f'gates: still running for {full[:9]} ({int(time.time()) - file_mtime(f"{job}/base")}s so far; log: {job}/out)\n')
            sys.stdout.write(queue_line(f'{job}/out'))
            sys.stdout.write(f'next: this step is not recorded yet; do not end your turn. Run the same command again now, in the foreground, until it records: '
                             f'kitchen.sh step {store} {sha} "{args[2]}"\n')
            raise Exit(4)
        poll_sleep(os.environ.get('KITCHEN_STEP_POLL_S') or '2')
    open(f'{job}/consumed', 'w').close()
    rc = cat_n(f'{job}/rc')
    classified = cat_n(f'{job}/classify.json')
    profiles = [l for l in read_text(f'{job}/profiles').split('\n')][:-1] if read_text(f'{job}/profiles') else []
    # A step with no profile ran no gate: it is recorded as none, never pass.
    if not_zero(rc):
        state = 'fail'
    elif not profiles:
        state = 'none'
    else:
        state = 'pass'
    os.makedirs(f'{store}/steps', exist_ok=True)
    with open(f'{store}/gates.tsv', 'a') as f:
        f.write(f'{int(time.time())}\t{unit}\t{full[:9]}\t{state}\t{",".join(profiles)}\n')
    if not_zero(rc):
        cat(f'{job}/out')
        event(store, 'sidekick', 'gate-fail', brief, f'{full[:9]} {job}/{os.readlink(f"{job}/out")}')
        fails = 0
        for row in read_text(f'{store}/gates.tsv').split('\n'):
            cols = row.split('\t')
            if len(cols) > 1 and awk_equal(cols[1], unit):
                fails = fails + 1 if len(cols) > 3 and cols[3] == 'fail' else 0
        if fails >= 2:
            sys.stdout.write(f'next: {fails} failed checks in a row; write the report as blocked with the logs under Questions, then kitchen.sh finish\n')
        else:
            sys.stdout.write(f'next: fix it in a new commit and run kitchen.sh step {store} <new-sha> "{args[2]}" again\n')
        raise Exit(2)
    if state == 'none':
        unmapped = ', '.join(array_of(parse_json(classified, 'kitchen.py classify output'), 'unmapped', 'kitchen.py classify output')) or 'these changes'
        sys.stdout.write(f'gates: none ran; no profile covers {unmapped}\n')
    else:
        sys.stdout.write(f'gates: pass ({",".join(profiles)})\n')
        # A pass that needed a rerun under load is recorded, so retro shows which suites flake on a loaded machine.
        names = '\n'.join(m.group(1) for m in (re.match(r'flaky under load: ([^ ]*) ', l) for l in read_text(f'{job}/out').split('\n')) if m)
        flaky = subprocess.run(['sort', '-u'], input=(names + '\n').encode('utf-8', 'surrogateescape'), stdout=subprocess.PIPE, check=False).stdout.decode('utf-8', 'surrogateescape') if names else ''
        flaky = ','.join(l for l in flaky.split('\n') if l)
        if flaky:
            sys.stdout.write(f'flaky under load: {flaky} failed, then passed on a rerun (see {job}/out)\n')
            event(store, 'sidekick', 'gate-flaky', brief, flaky)
    return core_step.step(ctx, args)


def finish(ctx, args):
    core_report.in_herdr()
    plain_signals()
    if len(args) != 2:
        return core_report.finish(ctx, args)
    store = args[0]
    report = readlink_f(args[1])
    pair_file(store)
    brief = pair_field(store, 'dispatch', 'brief', check=True, empty=True)
    # Everything but the dispatched unit's done report, and a report the pair's checks refuse anyway (no head:, open notes), goes straight on.
    if (not os.path.isfile(report) or header_field(report, 'status') != 'done' or not brief or report != expected_report(store, brief)
            or core_report.pair_report_ready(ctx, [store, report]) != 0):
        return core_report.finish(ctx, args)
    wanted = header_field(report, 'head')
    if wanted == '' or wanted.startswith('<'):
        return core_report.finish(ctx, args)
    root = pair_text(store, 'git_root', check=True)
    found = git(root, 'rev-parse', '--verify', '--quiet', f'{header_field(report, "head")}^{{commit}}')
    if found[0] != 0:
        raise Die(f'{os.path.basename(report)}: head: {header_field(report, "head")} is not a commit; name the unit\'s last commit and finish again')
    head = found[1].rstrip('\n')
    current = git(root, 'rev-parse', 'HEAD')
    if current[0] != 0:
        raise Exit(current[0])
    cur = current[1].rstrip('\n')
    if cur != head:
        raise Die(f"the checkout is at {cur[:9]}, not the report's head: {head[:9]}; the kitchen reruns the brief's Verify commands at the unit's tip, so check out that "
                  'head (or fix head:) and finish again')
    if not header_field(brief, 'commit').startswith('no'):
        # a plain assignment: git failing (a damaged index) ends the command with git's status, before any job directory exists
        status, listed = git(root, 'status', '--porcelain', '--untracked-files=normal')
        if status != 0:
            raise Exit(status)
        dirty = listed.rstrip('\n')
        if dirty:
            raise Die(f"the checkout holds work the unit's head does not:\n{dirty}\nThe kitchen reruns the brief's Verify commands at the unit's tip. Commit this as a "
                      'step (kitchen.sh step) and name it in head:, or remove it, then finish again')
    unit = unit_name(brief)
    job = verify_job(ctx, store, brief, head)
    if os.path.isfile(f'{job}/rc') and cat_n(f'{job}/rc') != '0' and os.path.isfile(f'{job}/consumed'):
        subprocess.run(['rm', '-rf', job], check=False)
    if not os.path.isdir(job):
        os.makedirs(job, exist_ok=True)
        cfg = brief_kitchen(brief)
        base = head_of(store, unit)
        detached(FINISH_SCRIPT, ['kitchen-finish', root, str(kpy.KITCHEN_PY), brief, job, cfg[1] if cfg else '', base], f'{job}/out', store)
    deadline = slice_deadline()
    while not os.path.isfile(f'{job}/rc'):
        if past(deadline):
            sys.stdout.write(f"verify: the brief's Verify commands are still running at {head[:9]} (log: {job}/out)\n")
            sys.stdout.write('next: the report is not sent yet; do not end your turn. Run the same command again now, in the foreground, until it sends: '
                             f'kitchen.sh finish {store} {args[1]}\n')
            raise Exit(4)
        poll_sleep(os.environ.get('KITCHEN_STEP_POLL_S') or '2')
    open(f'{job}/consumed', 'w').close()
    rc = cat_n(f'{job}/rc')
    cat(f'{job}/out')
    if not_zero(rc):
        event(store, 'sidekick', 'finish-verify-fail', brief, head[:9])
        sys.stdout.write('next: the master did not get this report. Fix what failed in a new commit, record it with kitchen.sh step, name it in head:, and finish again; if the '
                         'commands cannot pass, write the report as blocked with this output under Questions, then kitchen.sh finish\n')
        raise Exit(2)
    event(store, 'sidekick', 'finish-verify-pass', brief, head[:9])
    return core_report.finish(ctx, args)
