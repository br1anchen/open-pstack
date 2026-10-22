"""Reads of pair.json for kitchen_core, and the status a shell reports for a child.

The pair.json fields the kitchen reads by name (path strings given to pair_text, pair_field and the store readers):
.consultant.args .consultant.fallback .consultant.kind .master.permission_mode .sidekick.args .sidekick.start_args .verifier.name
.verifying.argv .verifying.brief .verifying.deadline .verifying.delivered .verifying.entry .verifying.idle_since .verifying.packet
.verifying.retried .verifying.risk .verifying.sample .verifying.scratch .verifying.session .verifying.started .verifying.unit .verifying.units
"""

import os
import sys

from pstack_core.commands.report import MISSING_PAIR
from pstack_core.errors import Exit
from pstack_core.store import alt, field, load, peek, read
from pstack_core.wait import as_text, json_values, read_path


def exit_status(returncode):
    """The status a shell reports for a child: 128 plus the signal number when a signal killed it (subprocess and waitpid give -N)."""
    return 128 - returncode if returncode < 0 else returncode


def no_pair(store, check=False, quiet=False):
    """Whether the store has no pair.json. Then the reader's value is empty after the two lines a missing pair.json gets, or, with check,
    the command ends with 2, as a failing assignment does under set -e. quiet leaves out the lines."""
    if os.path.isfile(f'{store}/pair.json'):
        return False
    if not quiet:
        sys.stdout.flush()
        sys.stderr.write(MISSING_PAIR.format(store=store))
        sys.stderr.flush()
    if check:
        raise Exit(2)
    return True


def pair_text(store, path, check=False, quiet=False, empty=False, default=None):
    """store.read for the places that must go on without a pair.json."""
    if no_pair(store, check, quiet):
        return ''
    return read(store, path, empty=empty, default=default)


def pair_field(store, role, key, check=False, default=None, empty=False):
    """store.field for the places that must go on without a pair.json."""
    if no_pair(store, check):
        return ''
    return field(store, role, key, default=default, empty=empty)


def head_of(store, unit):
    """A unit's recorded base: heads[unit], empty when there is none."""
    if no_pair(store, quiet=True):
        return ''
    return as_text(alt(peek(load(store), 'heads').get(unit), ''))


def herdr_raw(reply, *keys, quiet=False):
    """The value at keys in a herdr or daemon reply, null when it has none. A reply that is not JSON, or whose path steps over a value that is no
    object, is one stderr line naming it, and null."""
    values, ok = json_values(reply)
    problem = '' if ok and values else 'is not JSON'
    if not problem:
        found, readable = read_path(values[0], *keys)
        if readable:
            return found
        problem = f'has no object on the way to {".".join(keys)}'
    if not quiet:
        sys.stdout.flush()
        sys.stderr.write(f'herdr reply {problem}\n')
        sys.stderr.flush()
    return None


def herdr_value(reply, *keys, empty=True, quiet=False):
    """The text jq -r prints for the value at keys in a reply: empty for null (or, without empty, `null`)."""
    found = herdr_raw(reply, *keys, quiet=quiet)
    return as_text(alt(found, '') if empty else found)
