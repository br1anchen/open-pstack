import os

from ..errors import Die
from ..gates import require_not_paused
from ..replies import queued_brief
from ..send import send_and_wait
from ..store import readlink_f
from ..wait import die_with_the_shell, wait_opts
from .report import in_herdr


def discuss(ctx, args):
    in_herdr()
    if len(args) < 2:
        raise Die('usage: pair.sh discuss <store> <plan-path> [--timeout MS]')
    store, plan = args[0], args[1]
    die_with_the_shell()
    timeout, interval, send_only, rest = wait_opts(args[2:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    require_not_paused(store)
    return send_and_wait(ctx, store, readlink_f(plan), 'PLAN', timeout, interval, send_only)


def dispatch(ctx, args):
    in_herdr()
    if len(args) < 2:
        raise Die('usage: pair.sh dispatch <store> <brief-path> [--timeout MS | --every MIN]')
    store, brief = args[0], args[1]
    die_with_the_shell()
    timeout, interval, send_only, rest = wait_opts(args[2:])
    if rest:
        raise Die(f'unknown option {rest[0]}')
    if not os.path.isfile(brief):
        raise Die(f'brief not found: {brief}')
    brief = readlink_f(brief)
    require_not_paused(store)
    ctx.variant.require_agreed_plan(ctx, store, brief)
    queued = queued_brief(store)
    if queued and queued != brief:
        raise Die(f'the queue holds {queued}; dispatch that first, or: pair.sh queue {store} --clear', 5)
    return send_and_wait(ctx, store, brief, 'BRIEF', timeout, interval, send_only)
