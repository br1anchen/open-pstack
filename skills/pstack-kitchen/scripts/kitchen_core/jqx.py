"""jq reads of pair.json for the places where a field is not a plain `.role.key` (spawn.field covers that): the answer jq gives, its error on
stderr, and, for a plain assignment in the bash, the end of the command with its status."""

import subprocess
import sys

from pstack_core.errors import Exit


def pair_jq(store, program, check=False):
    """`jq -r PROGRAM pair.json`, stdout without its trailing newlines. With check, a jq failure ends the command with jq's status, as a failing
    assignment does under set -e."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['jq', '-r', program, f'{store}/pair.json'], stdout=subprocess.PIPE, check=False)
    if check and r.returncode != 0:
        raise Exit(r.returncode)
    return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')
