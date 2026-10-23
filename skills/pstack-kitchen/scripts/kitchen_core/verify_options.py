"""verify's words: the flags and unit numbers of one `kitchen.sh verify` call, reduced to the options the command reads."""

import re
from types import SimpleNamespace

from pstack_core.argv import Spec, read as read_options
from pstack_core.errors import Die
from pstack_core.numbers import whole_number


VERIFY_SPEC = Spec(valued=('--timeout', '--kind', '--retry-entry', '--every', '--base', '--parent', '--head', '--covers'),
                   flags=('--until-verdict', '--landing', '--wait', '--cancel'), positional=r'[0-9]{3}')


def parse_options(argv):
    """verify's flags and unit numbers as one namespace; a flag with no value, a malformed number and an unknown flag end the command."""
    o = SimpleNamespace(every_m=9, every_set=False, until_set=False, timeout_m=None, kind_override='', retry_entry='', range_base='', parent_ref='',
                        land_head='', wait=False, cancel=False, landing=False, units=[], covered=[])
    parsed = read_options(argv, VERIFY_SPEC)
    o.units = list(parsed.positionals)
    for flag, value in parsed.occurrences:
        if flag == '--timeout':
            o.timeout_m = whole_number(value, '--timeout', 'minutes')
        elif flag == '--kind':
            o.kind_override = value
        elif flag == '--retry-entry':
            o.retry_entry = value
        elif flag == '--every':
            o.every_m, o.every_set = whole_number(value, '--every', 'minutes'), True
        elif flag == '--base':
            o.range_base = value
        elif flag == '--parent':
            o.parent_ref = value
        elif flag == '--head':
            o.land_head = value
        elif flag == '--covers':
            fields = value.split(',')
            if fields and fields[-1] == '':
                fields.pop()
            for u in fields:
                if not re.fullmatch(r'[0-9]{3}', u):
                    raise Die(f"--covers takes unit numbers (NNN), one per flag or a comma list, not '{u}'")
                o.covered.append(u)
        elif flag == '--until-verdict':
            o.every_m, o.until_set = 525600, True
        else:
            setattr(o, flag[2:], True)
    if o.every_set and o.until_set:
        raise Die('--every and --until-verdict are alternatives')
    return o
