import os

from ..errors import Die, prog
from ..gates import require_not_paused
from ..replies import queued_brief
from ..send import send_and_wait
from ..wait import end_with_parent, wait_opts
from .report import in_herdr
from .rotate import rotate_if_due


def discuss(ctx, args):
    in_herdr()
    if len(args) < 2:
        raise Die(f'usage: {prog()} discuss <store> <plan-path> [--timeout MS]')
    store, plan = args[0], args[1]
    end_with_parent()
    timeout, interval, send_only, rest = wait_opts(args[2:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    require_not_paused(store)
    rotate_if_due(ctx, store, 'plan')
    return send_and_wait(ctx, store, os.path.realpath(plan), 'PLAN', timeout, interval, send_only)


def store_relative(store, path):
    """A brief path as given when it exists, else the same path under the store (briefs/NNN-x.md), else as given."""
    return f'{store}/{path}' if not os.path.isfile(path) and os.path.isfile(f'{store}/{path}') else path


def dispatch(ctx, args):
    in_herdr()
    if len(args) < 2:
        raise Die(f'usage: {prog()} dispatch <store> <brief-path> [--timeout MS | --every MIN]')
    store, brief = args[0], store_relative(args[0], args[1])
    end_with_parent()
    timeout, interval, send_only, rest = wait_opts(args[2:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    brief = os.path.realpath(brief)
    require_not_paused(store)
    ctx.variant.require_agreed_plan(ctx, store, brief)
    queued = queued_brief(store)
    if queued and queued != brief:
        raise Die(f'the queue holds {queued}; dispatch that first, or: pair.sh queue {store} --clear', 5)
    rotate_if_due(ctx, store, 'brief')
    return send_and_wait(ctx, store, brief, 'BRIEF', timeout, interval, send_only)
