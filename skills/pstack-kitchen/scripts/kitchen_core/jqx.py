"""jq reads of pair.json for the places where a field is not a plain `.role.key` (store.field covers that): the answer jq gives, its error on
stderr, and, for a plain assignment in the bash, the end of the command with its status."""

import os
import subprocess
import sys

from pstack_core.commands.report import MISSING_PAIR
from pstack_core.errors import Exit
from pstack_core.store import field, load


def exit_status(returncode):
    """The status a shell reports for a child: 128 plus the signal number when a signal killed it (subprocess and waitpid give -N)."""
    return 128 - returncode if returncode < 0 else returncode


def pair_jq(store, program, check=False, quiet=False):
    """`jq -r PROGRAM pair.json`, stdout without its trailing newlines. With check, a jq failure ends the command with jq's status, as a failing
    assignment does under set -e."""
    if os.path.isfile(f'{store}/pair.json'):
        load(store)
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['jq', '-r', program, f'{store}/pair.json'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if quiet else None, check=False)
    if check and r.returncode != 0:
        raise Exit(exit_status(r.returncode))
    return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')


def tolerant_field(store, role, key, check=False, default=None, empty=False):
    """`field "$store" .role.key` in a substitution: a store with no pair.json gets the messages jq and pair_file give and an empty value; with
    check, the failure of the assignment ends the command with jq's status. A pair.json that is damaged is store.load's one message."""
    if not os.path.isfile(f'{store}/pair.json'):
        sys.stdout.flush()
        sys.stderr.write(MISSING_PAIR.format(store=store))
        sys.stderr.flush()
        if check:
            raise Exit(2)
        return ''
    return field(store, role, key, check=check, default=default, empty=empty)
