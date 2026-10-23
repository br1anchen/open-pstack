"""catch and retro: the run's ledger rows, and the counts a retro prints.."""

import json
import re
import sys
from pathlib import Path

from pstack_core.errors import Die, Exit, ToolFailed
from pstack_core.store import event, now, pair_file, read, read_text, who

from . import kpy

LAYERS = ('gate', 'policy', 'self', 'verify', 'review', 'triage', 'escalation')
HINT = """encode a repeated class at the highest level that works (the correct skill):
  gate-fail <profile>  a sharper brief, or a lint whose error names the fix
  gate-flaky <profile> a suite that fails under load: related tests per step, the full suite in behavioral
  reject verify        acceptance the gates do not cover: add a behavioral command or test
  steer brief          briefs miss a decision the master keeps making: a standing order or plan template line
  catch <layer>        the layer that should have caught it gets the check
"""


def slug_of(store):
    return read(store, 'slug')


def state_ledger(store, abort=False):
    """The ledger path kitchen.py's `statedir` names, its stderr passing through. A failing `statedir` is not looked at by catch and ends the
    command with its status for abort (retro)."""
    try:
        state = kpy.kitchen_py.statedir(
            kpy.kitchen_py.Invocation(read(store, 'git_root'))
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        if abort:
            raise ToolFailed(e.code) from None
        state = ''
    return state + '/ledger.tsv'


def catch(ctx, args):
    if len(args) not in (3, 4):
        raise Die('usage: kitchen.sh catch <store> <layer> <text> [<unit>]')
    store, layer, text = args[0], args[1], args[2]
    unit = args[3] if len(args) == 4 else ''
    if layer not in LAYERS:
        raise Die(
            'layer is gate, policy, self, verify, review, triage, or escalation: the one that should have caught it'
        )
    pair_file(store)
    if unit and not re.fullmatch(r'[0-9]{3}', unit):
        raise Die("unit is a brief's number, like 003")
    # Options first, then --, so a text that starts with - stays text.
    try:
        kpy.kitchen_py.ledger_catch(
            Path(store),
            Path(state_ledger(store)),
            slug_of(store),
            now(),
            layer,
            text,
            unit or None,
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
        raise Exit(e.code) from None
    event(store, 'master', 'catch', '-', layer)
    sys.stdout.write(f'caught at {layer}: {text}\n')
    return 0


def counters(store):
    return kpy.kitchen_py.run_counters(Path(store))


def retro_line(store, c):
    """The one-line count of a run: units reported and verified, first-try verdicts, wakes, escalations, failovers."""
    return (
        f'{who("run", slug_of(store))}: {json.dumps(c["done_units"])} unit reports, {json.dumps(c["verified_units"])} verified units '
        f'({json.dumps(c["clean_first_try"])} clean verdicts on the first try), {json.dumps(c["wakes"])} master wakes, '
        f'{json.dumps(c["escalations"])} escalations, {json.dumps(c["failovers"])} failovers'
    )


def repeated_rows(ledger):
    """The classes the ledger holds twice or more, as "count layer-ish area in N runs" rows, most frequent first."""
    count, seen, runs = {}, set(), {}
    try:
        lines = read_text(ledger).split('\n')
    except OSError:
        return []
    if lines and lines[-1] == '':
        lines.pop()
    for number, line in enumerate(lines, start=1):
        if number == 1:
            continue
        fields = line.split('\t')
        field = lambda n: fields[n - 1] if n <= len(fields) else ''
        if re.fullmatch(r'lesson|lesson-added|dismiss', field(3)):
            continue
        key = (field(3), field(4))
        count[key] = count.get(key, 0) + 1
        if (key, field(2)) not in seen:
            seen.add((key, field(2)))
            runs[key] = runs.get(key, 0) + 1
    rows = [
        (n, f'  {n:3d} {k[0]:<10s} {k[1]:<24s} in {runs[k]} runs')
        for k, n in count.items()
        if n >= 2
    ]
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [r[1] for r in rows]


def retro(ctx, args):
    if len(args) != 1:
        raise Die('usage: kitchen.sh retro <store>')
    store = args[0]
    pair_file(store)
    ledger = state_ledger(store, abort=True)
    run = slug_of(store)
    kpy.call(kpy.kitchen_py.ledger_retro, Path(store), Path(ledger), run, now())
    c = counters(store)
    out = sys.stdout
    out.write(retro_line(store, c) + '\n')
    if c['verified_units'] != 0:
        out.write(
            f'master wakes per verified unit: {c["wakes"] / c["verified_units"]:.1f}\n'
        )
    out.write(
        'repeated across runs (the kitchen should catch these, not the master):\n'
    )
    for row in repeated_rows(ledger):
        out.write(row + '\n')
    out.write(HINT)
    out.flush()
    try:
        sys.stdout.write(
            kpy.kitchen_py.lessons_text(
                kpy.kitchen_py.lessons_overview(
                    kpy.kitchen_py.Invocation(read(store, 'git_root'))
                )
            )
            + '\n'
        )
    except kpy.KitchenError as e:
        kpy.explain(e)
    return 0
