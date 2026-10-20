"""jq reads of pair.json for the places where a field is not a plain `.role.key` (spawn.field covers that): the answer jq gives, its error on
stderr, and, for a plain assignment in the bash, the end of the command with its status."""

import json
import os
import subprocess
import sys

from pstack_core import spawn
from pstack_core.commands.report import MISSING_PAIR
from pstack_core.errors import Exit


def pair_jq(store, program, check=False, quiet=False):
    """`jq -r PROGRAM pair.json`, stdout without its trailing newlines. With check, a jq failure ends the command with jq's status, as a failing
    assignment does under set -e."""
    sys.stdout.flush()
    sys.stderr.flush()
    r = subprocess.run(['jq', '-r', program, f'{store}/pair.json'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if quiet else None, check=False)
    if check and r.returncode != 0:
        raise Exit(r.returncode)
    return r.stdout.decode('utf-8', 'surrogateescape').rstrip('\n')


def field(store, role, key, check=False, default=None, empty=False):
    """`field "$store" .role.key` in a substitution: a store with no pair.json, or one that is no object, gets the messages jq and pair_file give and
    an empty value; with check, the failure of the assignment ends the command with jq's status."""
    path = f'{store}/pair.json'
    if not os.path.isfile(path):
        sys.stdout.flush()
        sys.stderr.write(MISSING_PAIR.format(store=store))
        sys.stderr.flush()
        if check:
            raise Exit(2)
        return ''
    try:
        with open(path, 'rb') as f:
            whole = json.load(f)
    except ValueError:
        whole = None
    if not isinstance(whole, dict):
        suffix = f' // "{default}"' if default is not None else (' // empty' if empty else '')
        return pair_jq(store, f'.{role}.{key}' + suffix, check=check)
    return spawn.field(store, role, key, check=check, default=default, empty=empty)
