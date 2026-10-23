#!/usr/bin/env python3
"""The detached half of `kitchen.sh step` and `kitchen.sh finish`: runs the repo's checks and writes the job's rc file.

    worker.py step   <repo> <kitchen.py> <job> <base> <head> [<config>]
    worker.py finish <repo> <kitchen.py> <brief> <job> [<config>] [<base>]

Each child is kitchen.py, with this process's stdout and stderr (the job's out log). It outlives the command that started it."""

import os
import subprocess
import sys

sys.dont_write_bytecode = True


def kitchen(py, repo, config, *args):
    # The process boundary the detached children keep: they outlive the command that started them. The global options are kitchen.child_args's
    # --repo and --config; this file runs standalone under -I and cannot import kitchen.py.
    rc = subprocess.run(
        [
            sys.executable,
            py,
            '--repo',
            repo,
            *(['--config', config] if config else []),
            *args,
        ],
        stdin=subprocess.DEVNULL,
    ).returncode
    # the same mapping as pstack_core.numbers.exit_code; this file runs standalone under -I and imports nothing from it
    return 128 - rc if rc < 0 else rc


def write_rc(job, rc):
    with open(f'{job}/rc', 'w') as f:
        f.write(f'{rc}\n')


def step(repo, py, job, base, head, config=''):
    path = f'{job}/profiles'
    if not os.path.isfile(path):
        print(f'no profiles file: {path}', flush=True)
        write_rc(job, 2)
        return
    rc = 0
    with open(path) as f:
        profiles = [line for line in f.read().split('\n') if line]
    for profile in profiles:
        if kitchen(
            py,
            repo,
            config,
            'gate',
            profile,
            'fast',
            '--role',
            'sidekick',
            '--base',
            base,
        ):
            rc = 2
    if kitchen(py, repo, config, 'policy', '--base', base, '--head', head):
        rc = 2
    write_rc(job, rc)


def finish(repo, py, brief, job, config='', base=''):
    rc = kitchen(
        py,
        repo,
        config,
        'brief-verify',
        brief,
        '--role',
        'sidekick',
        '--log-dir',
        f'{job}/logs',
        *(['--base', base] if base else []),
    )
    write_rc(job, rc)


def main(argv):
    commands = {'step': step, 'finish': finish}
    if not argv or argv[0] not in commands:
        raise SystemExit('usage: worker.py step|finish <args>')
    try:
        os.chdir(argv[1])
    except OSError:
        return 1
    commands[argv[0]](*argv[1:])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
