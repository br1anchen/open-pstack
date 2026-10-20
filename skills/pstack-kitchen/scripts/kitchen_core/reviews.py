"""The review notes the kitchen requires an engine run in: which accepted units still lack one. warn_unreviewed is ported from kitchen.sh at 9f0f6e5;
its bash copy stays until the verify commands that still call it move. The engine tests it shares with land-check live in landing.py."""

import sys

from .landing import accepted_units, engine_met, review_engine


def warn_unreviewed(store):
    """A `warning: unreviewed:` line for each accepted unit whose review note lacks the run the kitchen's [review].engine requires; nothing when no
    engine is set."""
    engine = review_engine(store)
    if not engine:
        return
    for review, seq, brief in accepted_units(store):
        met, gap = engine_met(store, brief, review, engine)
        if not met:
            sys.stdout.write(f'warning: unreviewed: unit {seq} cites no {engine} run; review its range with {engine} and add "engine: {engine} <run id or artifact path>" to {review}{gap}\n')
