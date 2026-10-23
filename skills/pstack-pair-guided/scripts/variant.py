import glob
import os
import re

from pstack_core.argv import Spec, read
from pstack_core.errors import Die, MissingStore, PlanNotAgreed, refuse_as
from pstack_core.gates import require_not_paused
from pstack_core.panes import require_herdr
from pstack_core.send import send_and_wait
from pstack_core.store import (
    fill_template,
    header_field,
    newest,
    pair_file,
    read_text,
    write_text,
)
from pstack_core.variant import PAIR_COMMANDS, Variant, command_table, usage_text
from pstack_core.wait import TIMEOUT_ONLY, end_with_parent, timeout_ms


def answer(ctx, args):
    """Deliver the human's or master's answer to a sidekick's ask (a message of kind ANSWER) and wait for its reply."""
    require_herdr()
    end_with_parent()
    if len(args) < 2:
        raise Die(
            'usage: pair.sh answer <store> <answer-path> [--timeout MS | --every MIN]'
        )
    store, path = args[0], args[1]
    opts = read(args[2:], TIMEOUT_ONLY)
    timeout = timeout_ms(opts)
    opts.refuse_leftovers()
    if not os.path.isfile(path):
        raise Die(f'answer not found: {path}')
    require_not_paused(store)
    if not re.search(r'(?m)^## Answer', read_text(path)):
        raise Die(f'not an answer file: {path}')
    try:
        return send_and_wait(
            ctx, store, os.path.realpath(path), 'ANSWER', timeout, False
        )
    except MissingStore as e:
        raise refuse_as(e, ctx.prog, 2) from None


def new_direction(ctx, args):
    """plans/NNN-<slug>.direction.md, the human's summary of a plan, from the direction template; prints its path."""
    if len(args) != 2:
        raise Die('usage: pair.sh new-direction <store> <plan-path>')
    store = args[0]
    pair_file(store)
    plan = os.path.realpath(args[1])
    if not os.path.isfile(plan):
        raise Die(f'plan not found: {plan}')
    name = os.path.basename(plan)
    seq, slug = name[:3], name.removesuffix('.md')[4:]
    path = f'{store}/plans/{seq}-{slug}.direction.md'
    if os.path.isfile(path):
        print(path)
        return 0
    scale = header_field(plan, 'scale') or '{{small | medium | large}}'
    text = fill_template(
        f'{ctx.skill_root}/references/direction-template.md',
        {'SLUG': slug, 'PLAN': plan, 'STORE': store},
    )
    write_text(path, re.sub(r'(?m)^scale: .*', lambda _: f'scale: {scale}', text))
    print(path)
    return 0


def new_answer(ctx, args):
    """answers/NNN-<slug>-a<k>.md, the answer to the newest ask on unit NNN, from the answer template; prints its path."""
    if len(args) < 2:
        raise Die('usage: pair.sh new-answer <store> <NNN> [--force]')
    store, seq = args[0], args[1]
    force = read(
        args[2:], Spec(flags=('--force',), slots={'--force': 0}, unknown='collect')
    ).present('--force')
    pair_file(store)
    asks = {
        p: m
        for p in glob.glob(
            os.path.join(
                glob.escape(store), 'reports', f'{glob.escape(seq)}-*-q[0-9]*.md'
            )
        )
        if (m := re.fullmatch(r'(\d{3})-(.+)-q(\d+)\.md', os.path.basename(p)))
    }
    ask = newest(asks)
    if ask is None:
        raise Die(
            f'no ask report for unit {seq} (expected reports/{seq}-<slug>-q<k>.md)'
        )
    if header_field(ask, 'status') != 'asking':
        raise Die(f'{ask} is not status: asking')
    _, slug, k = asks[ask].groups()
    path = f'{store}/answers/{seq}-{slug}-a{k}.md'
    if os.path.isfile(path):
        print(path)
        return 0
    if int(k) > 2 and not force:
        raise Die(
            f'ask {k} exceeds the two-ask cap for unit {seq}; re-plan the unit, or pass --force to answer anyway',
            7,
        )
    write_text(
        path,
        fill_template(
            f'{ctx.skill_root}/references/answer-template.md',
            {
                'NNN-<k>': f'{seq}-{k}',
                'NNN': seq,
                '<slug>': slug,
                '<k>': k,
                '<store>': store,
            },
            braced=False,
        ),
    )
    print(path)
    return 0


USAGE = usage_text(
    command_table(
        PAIR_COMMANDS,
        drop=('rotate',),
        replace={
            'dispatch': PAIR_COMMANDS['dispatch'][:3]
            + (
                '                                            implementation playbooks require an agreed plan whose',
                "                                            review approval matches the plan's scale",
            )
        },
        after={
            'discuss': {
                'new-direction': (
                    "  new-direction <store> <plan-path>         create plans/NNN-<slug>.direction.md, the human's summary; prints its path",
                ),
                'new-answer': (
                    '  new-answer <store> <NNN> [--force]        create answers/NNN-<slug>-a<k>.md for the newest ask on unit NNN',
                ),
                'answer': (
                    '  answer <store> <answer-path> [--timeout MS | --every MIN]',
                    '                                            send ANSWER to the sidekick and wait for it to settle',
                ),
            }
        },
    ),
    'exit codes: 0 ok, 1 usage or precondition, 2 herdr error, 3 sidekick blocked, 4 no report yet or queue empty, 5 sidekick in the wrong state or queue taken, '
    '6 plan not agreed or not approved, 7 ask or steer cap reached',
)


class GuidedVariant(Variant):
    PAIR_SKILL = 'pstack-pair-guided'
    STATE_DIR = 'pair-guided'
    store_dirs = (
        'plans',
        'briefs',
        'reports',
        'reviews',
        'answers',
        'progress',
        'steers',
    )
    unfilled_re = '^(decided by|status): .*\\|'
    usage = USAGE
    commands = {
        'answer': answer,
        'new-direction': new_direction,
        'new-answer': new_answer,
    }

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """An agreed plan also needs the human approval its scale calls for."""
        scale = header_field(plan, 'scale')
        approval = header_field(review, 'approval')
        if scale == 'small':
            return
        if scale == 'medium':
            if approval not in ('posted', 'human'):
                raise PlanNotAgreed(
                    f"medium plan needs approval: posted or human in {review} (found '{approval or 'missing'}')"
                )
        elif scale == 'large':
            if approval != 'human':
                raise PlanNotAgreed(
                    f"large plan needs approval: human in {review} (found '{approval or 'missing'}'); post the direction summary and wait for the human"
                )
        else:
            raise PlanNotAgreed(
                f"plan {plan} has scale '{scale or 'missing'}'; set small, medium, or large"
            )


VARIANT = GuidedVariant
