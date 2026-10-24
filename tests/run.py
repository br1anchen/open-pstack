#!/usr/bin/env python3
"""Run the repo's Python test suites in parallel, one process per test.

The suites drive real scripts through fakes and wait on real timers, so one
test at a time leaves the machine idle: the serial run took about ten
minutes. Every test already works in its own temporary directory, with its
own fake agents, state and Herdr socket, so the tests run side by side.

    python3 tests/run.py                      # every suite, one worker per core
    python3 tests/run.py -j 4 test_pstack_pair
    python3 tests/run.py -k rotate            # tests whose id contains "rotate"
    python3 tests/run.py --timeout 300   # fail a test that runs longer (default 1200 s, or $PSTACK_TEST_TIMEOUT_S)

Every test process starts with a `jq` first on PATH that prints `jq was started`
to stderr and exits 127: the scripts under test read JSON in Python, so a jq
call is a failure.

Prints each failure's output, the slowest tests, and a summary; exits 1 when
any test fails.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

here = Path(__file__).resolve().parent
SUITES = [
    'test_pstack_core',
    'test_pstack_kitchen',
    'test_pstack_pair',
    'test_pstack_sync',
    'test_pstack_token_audit',
    'test_pstack_upstream',
    'test_spec_gen',
    'test_run',
]


# The jq every test process finds first on PATH. TRIPWIRE_LOG, when a test sets it, collects the arguments of each call.
TRIPWIRE_JQ = '#!/bin/sh\nprintf "jq was started\\n" >&2\n[ -z "${TRIPWIRE_LOG:-}" ] || printf "jq %s\\n" "$*" >>"$TRIPWIRE_LOG"\nexit 127\n'


def write_tripwire_jq(directory):
    path = Path(directory) / 'jq'
    path.write_text(TRIPWIRE_JQ)
    path.chmod(0o755)


def tripwire_env(directory):
    """The environment of a test process: os.environ with a directory holding the failing jq first on PATH."""
    write_tripwire_jq(directory)
    return {**os.environ, 'PATH': f'{directory}:{os.environ.get("PATH", "")}'}


def test_ids(suites):
    sys.path.insert(0, str(here))
    ids = []

    def walk(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                ids.append(t.id())

    for name in suites:
        walk(unittest.defaultTestLoader.loadTestsFromName(name))
    return ids


def as_text(data):
    if data is None:
        return ''
    return data.decode(errors='replace') if isinstance(data, bytes) else data


def run_one(test_id, timeout=0, *, cwd=here, env=None):
    start = time.monotonic()
    p = subprocess.Popen(
        [sys.executable, '-m', 'unittest', '-q', test_id],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = p.communicate(timeout=timeout or None)
        return test_id, p.returncode, time.monotonic() - start, out
    except subprocess.TimeoutExpired as e:
        partial = as_text(e.output)
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired as e2:
            out = as_text(e2.output) or partial
        return (
            test_id,
            'timeout',
            time.monotonic() - start,
            f'TIMEOUT after {timeout}s\n{as_text(out)}',
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('suites', nargs='*', default=SUITES)
    ap.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 4)
    ap.add_argument('-k', '--keyword', default='')
    ap.add_argument(
        '--timeout',
        type=int,
        default=int(os.environ.get('PSTACK_TEST_TIMEOUT_S', '1200')),
        help='seconds a test may run; 0 for no limit',
    )
    ap.add_argument('--slowest', type=int, default=8)
    args = ap.parse_args()
    ids = [i for i in test_ids(args.suites) if args.keyword in i]
    start = time.monotonic()
    failed, times = [], []
    with (
        tempfile.TemporaryDirectory(prefix='pstack-tests-jq-') as jq_dir,
        ThreadPoolExecutor(max_workers=args.jobs) as pool,
    ):
        env = tripwire_env(jq_dir)
        for fut in as_completed(
            [pool.submit(run_one, i, args.timeout, env=env) for i in ids]
        ):
            test_id, code, secs, out = fut.result()
            times.append((secs, test_id))
            if code != 0:  # an int, or 'timeout'
                failed.append(test_id)
                print(f'FAIL {test_id} ({secs:.1f}s)\n{out}', flush=True)
    wall = time.monotonic() - start
    print('slowest:')
    for secs, test_id in sorted(times, reverse=True)[: args.slowest]:
        print(f'  {secs:6.1f}s  {test_id}')
    print(
        f'{len(ids)} tests, {len(failed)} failed, {wall:.0f}s wall with {args.jobs} jobs'
    )
    for test_id in failed:
        print(f'failed: {test_id}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
