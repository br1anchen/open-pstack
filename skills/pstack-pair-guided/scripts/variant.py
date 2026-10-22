import glob
import os
import re

from pstack_core.commands.report import in_herdr
from pstack_core.errors import Die
from pstack_core.gates import require_not_paused
from pstack_core.send import send_and_wait
from pstack_core.store import header_field, newest, pair_file, read_text, whole_number, write_text
from pstack_core.variant import Variant
from pstack_core.wait import end_with_parent, tdiv

def answer(ctx, args):
    """Deliver the human's or master's answer to a sidekick's ask (a message of kind ANSWER) and wait for its reply."""
    in_herdr()
    end_with_parent()
    if len(args) < 2:
        raise Die('usage: pair.sh answer <store> <answer-path> [--timeout MS | --every MIN]')
    store, path, rest = args[0], args[1], args[2:]
    timeout, i = 540000, 0
    while i < len(rest):
        option = rest[i]
        if option not in ('--timeout', '--every'):
            raise Die(f'unknown option {option}')
        if i + 1 >= len(rest):
            raise Die(f'{option} needs a value')
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
    return send_and_wait(ctx, store, os.path.realpath(path), 'ANSWER', timeout, interval, False)


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
    text = read_text(f'{ctx.skill_root}/references/direction-template.md')
    for token, value in (('{{SLUG}}', slug), ('{{PLAN}}', plan), ('{{STORE}}', store)):
        text = text.replace(token, value)
    write_text(path, re.sub(r'(?m)^scale: .*', lambda _: f'scale: {scale}', text))
    print(path)
    return 0


def new_answer(ctx, args):
    """answers/NNN-<slug>-a<k>.md, the answer to the newest ask on unit NNN, from the answer template; prints its path."""
    if len(args) < 2:
        raise Die('usage: pair.sh new-answer <store> <NNN> [--force]')
    store, seq, force = args[0], args[1], args[2:3] == ['--force']
    pair_file(store)
    asks = [p for p in glob.glob(os.path.join(glob.escape(store), 'reports', f'{glob.escape(seq)}-*-q[0-9]*.md'))
            if re.fullmatch(r'\d{3}-.+-q\d+\.md', os.path.basename(p))]
    ask = newest(asks)
    if ask is None:
        raise Die(f'no ask report for unit {seq} (expected reports/{seq}-<slug>-q<k>.md)')
    if header_field(ask, 'status') != 'asking':
        raise Die(f'{ask} is not status: asking')
    _, slug, k = re.fullmatch(r'(\d{3})-(.+)-q(\d+)\.md', os.path.basename(ask)).groups()
    path = f'{store}/answers/{seq}-{slug}-a{k}.md'
    if os.path.isfile(path):
        print(path)
        return 0
    if int(k) > 2 and not force:
        raise Die(f'ask {k} exceeds the two-ask cap for unit {seq}; re-plan the unit, or pass --force to answer anyway', 7)
    text = read_text(f'{ctx.skill_root}/references/answer-template.md')
    for token, value in (('NNN-<k>', f'{seq}-{k}'), ('NNN', seq), ('<slug>', slug), ('<k>', k), ('<store>', store)):
        text = text.replace(token, value)
    write_text(path, text)
    print(path)
    return 0


USAGE = """usage: pair.sh <command> [args]

  init <slug> [--store DIR]                 create or re-register the pair store; prints its path
  spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [--direction right|down] [--pane ID] [--timeout MS] [-- agent-args...]
                                            split from the master pane, start and bootstrap the sidekick;
                                            Devin defaults to bypass, other kinds inherit the master's mode;
                                            a pi or Prime sidekick first proves its model answers; on failure the
                                            --fallback kind starts instead; --tab gives the sidekick its own tab
                                            instead of a split (PSTACK_PLACEMENT=tab sets the default)
  failover <store> [--reason TEXT] [--force]
                                            replace a failing sidekick with its recorded fallback in the same pane;
                                            the fallback picks up from the store, and the run stays on it
  permission                                print the master's detected permission mode
  new-plan <store> <slug>                   create plans/NNN-<slug>.md from the template; prints its path
  discuss <store> <plan-path> [--timeout MS]
                                            send PLAN to the sidekick and wait for its agree/object response
  new-direction <store> <plan-path>         create plans/NNN-<slug>.direction.md, the human's summary; prints its path
  new-answer <store> <NNN> [--force]        create answers/NNN-<slug>-a<k>.md for the newest ask on unit NNN
  answer <store> <answer-path> [--timeout MS | --every MIN]
                                            send ANSWER to the sidekick and wait for it to settle
  new-brief <store> <slug>                  create briefs/NNN-<slug>.md from the template; prints its path
  dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]
                                            send BRIEF to the sidekick and wait for it to settle;
                                            implementation playbooks require an agreed plan whose
                                            review approval matches the plan's scale
  wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path
                                            (and any queued or now-running brief), or a check-in digest when the
                                            interval passes first
  queue <store> <brief-path> [--replace]    hold the next brief for a working sidekick; it takes it the moment its
                                            current report is written. One slot. queue <store> --clear empties it
  step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]
                                            sidekick: record a committed step of the running brief and go on;
                                            the master's wait picks it up for review
  notes <store>                             sidekick, at each step boundary: print blocking review notes still open
  new-note <store> <NNN>                    create the draft note reviewing unit NNN's steps since the last note
  note <store> <note-draft-path>            publish a filled note to the sidekick; its follow-ups go to followups.md
  finish <store> <report-path>              sidekick, after writing any report: notify the master, then print
                                            the queued brief to start (after a done report) or the line to end on
  next <store>                              sidekick: refused for every kind (exit 5); the master dispatches the queued brief to a fresh session
  progress <store> <text>                   sidekick: append one timestamped line to the running brief's progress log
  new-steer <store> <NNN> [--supersedes STEER | --force]
                                            create steers/NNN-<slug>-s<k>.md; prints its path. --supersedes answers
                                            an objection; two fresh steers per brief
  steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]
                                            send STEER: to a working sidekick, return at once (its harness hands it
                                            over between tool calls; --interrupt cancels the running one first);
                                            to one paused on an objection, wait for it to settle
  report <store> [NNN]                      print the latest (or NNN) report path
  notify <store> <report-path>              sidekick -> master: prompt the master if it is idle
  stop <store> [--timeout MS]               send STOP; the sidekick pauses safely and reports
  scratch <store> <id> [--at SHA] [--remove]
                                            a throwaway worktree under <store>/scratch/<id>: at HEAD with the live
                                            diff applied, or exactly at SHA to recheck a unit while the sidekick works
  pause <store> [--reason TEXT]             pause at the next safe point: the sidekick stops at its next step or
                                            progress boundary; dispatch, queue, discuss, consult, answer refuse
  resume <store>                            lift the pause; re-dispatch the paused brief to continue it
  status <store>                            table of briefs, reports, reviews, and live agent states
  log <store> <phase> <decision> <why> <evidence> <result>
                                            append a decisions.tsv row (show-me-your-work format)
  metrics <store>                           where the time went, from events.tsv: busy and idle per agent,
                                            master wakes, review latency, verdicts

exit codes: 0 ok, 1 usage or precondition, 2 herdr error, 3 sidekick blocked, 4 no report yet or queue empty, 5 sidekick in the wrong state or queue taken, 6 plan not agreed or not approved, 7 ask or steer cap reached
"""


class GuidedVariant(Variant):
    PAIR_SKILL = 'pstack-pair-guided'
    STATE_DIR = 'pair-guided'
    store_dirs = ('plans', 'briefs', 'reports', 'reviews', 'answers', 'progress', 'steers')
    unfilled_re = '^(decided by|status): .*\\|'
    usage = USAGE
    commands = {'answer': answer, 'new-direction': new_direction, 'new-answer': new_answer}

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
