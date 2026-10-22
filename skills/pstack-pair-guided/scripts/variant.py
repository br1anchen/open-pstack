import os
import re

from pstack_core.commands.report import in_herdr
from pstack_core.errors import Die, fail
from pstack_core.gates import require_not_paused
from pstack_core.send import send_and_wait
from pstack_core.store import header_field, readlink_f, whole_number
from pstack_core.variant import Variant
from pstack_core.wait import die_with_the_shell, tdiv

GUIDED_PAIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pair.sh')


def answer(ctx, args):
    """Deliver the human's or master's answer to a sidekick's ask (a message of kind ANSWER) and wait for its reply."""
    in_herdr()
    die_with_the_shell()
    if len(args) < 2:
        raise Die('usage: pair.sh answer <store> <answer-path> [--timeout MS | --every MIN]')
    store, path, rest = args[0], args[1], args[2:]
    timeout, i = 540000, 0
    while i < len(rest):
        option = rest[i]
        if option not in ('--timeout', '--every'):
            raise Die(f'unknown option {option}')
        if i + 1 >= len(rest):
            fail(f'{option}) ', '$2: unbound variable', 'cmd_answer() {', GUIDED_PAIR)
        if option == '--timeout':
            timeout = whole_number(rest[i + 1], '--timeout', 'milliseconds')
        else:
            timeout = whole_number(rest[i + 1], '--every', 'minutes') * 60000
        i += 2
    interval = tdiv(timeout, 60000)
    if not os.path.isfile(path):
        raise Die(f'answer not found: {path}')
    require_not_paused(store)
    with open(path, 'rb') as f:
        if not re.search(r'(?m)^## Answer', f.read().decode('utf-8', 'surrogateescape')):
            raise Die(f'not an answer file: {path}')
    return send_and_wait(ctx, store, readlink_f(path), 'ANSWER', timeout, interval, False)


class GuidedVariant(Variant):
    commands = {'answer': answer}

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """An agreed plan also needs the human approval its scale calls for."""
        scale = header_field(plan, 'scale')
        approval = header_field(review, 'approval')
        if scale == 'small':
            return
        if scale == 'medium':
            if approval not in ('posted', 'human'):
                raise Die(f"medium plan needs approval: posted or human in {review} (found '{approval or 'missing'}')", 6)
        elif scale == 'large':
            if approval != 'human':
                raise Die(f"large plan needs approval: human in {review} (found '{approval or 'missing'}'); post the direction summary and wait for the human", 6)
        else:
            raise Die(f"plan {plan} has scale '{scale or 'missing'}'; set small, medium, or large", 6)


VARIANT = GuidedVariant
