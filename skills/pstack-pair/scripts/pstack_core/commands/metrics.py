import os
import re
import statistics
import sys
import time
from dataclasses import dataclass

from ..store import env_int, read_text
from ..errors import Die

WHOLE_NUMBER = re.compile(r'[0-9]+', re.ASCII)
WORDS = re.compile(r'[a-z]+', re.ASCII | re.IGNORECASE)
PAUSED_UNIT = re.compile(r'-[sq][0-9]+$')


@dataclass(frozen=True)
class Event:
    """One events.tsv row. `time` is None when the time column is not a whole number; `note_id` is a note's id when it is one."""
    time: int | None
    actor: str
    kind: str
    unit: str
    detail: str
    note_id: int | None = None


def parse_event(row):
    """None for a row with too few columns."""
    columns = row.split('\t')
    if len(columns) < 6:
        return None
    _, time_, actor, kind, unit, detail = columns[:6]
    note_id = None
    if kind == 'note':
        id_ = detail.split(':')[0]
        note_id = int(id_) if WHOLE_NUMBER.fullmatch(id_) else None
    return Event(int(time_) if WHOLE_NUMBER.fullmatch(time_) else None, actor, kind, unit, detail, note_id)


def parse_events(log):
    """The events in a log, in file order. The log has no header, but a store from an older build may start with one."""
    rows = read_text(log).split('\n')
    if rows and rows[-1] == '':
        rows.pop()
    if rows and rows[0].startswith('ts\t'):
        rows = rows[1:]
    return [e for e in map(parse_event, rows) if e]


def minutes(seconds):
    return f'{round(seconds / 60)}m'


def median(values):
    return statistics.median_low(values) if values else 0


class Metrics:
    """Busy, idle and latency figures folded from events, in whole seconds."""

    def __init__(self, now):
        self.now = now
        self.first = self.last = None
        self.sk_on = self.co_on = False
        self.sk_since = self.sk_unit = self.sk_kind = self.sk_free = None
        self.co_since = self.co_kind = None
        self.sk_busy = self.sk_idle = self.co_busy = self.plan_sk = self.plan_co = self.consult_t = 0
        self.sk_gaps = self.pickups = self.taken = self.nsteps = self.nnotes = self.nblock = self.wakes = self.checkins = 0
        self.failovers = []
        self.briefs = []
        self.brief_secs, self.stall_secs, self.review_secs = [], [], []
        self.plans = self.consults = 0
        self.review_from = None
        self.queued, self.steps, self.noted, self.verdicts = set(), {}, {}, {}
        self.counters = {
            'send-brief': self.count_send_brief, 'send-answer': self.count_send_brief, 'queue': self.count_queue,
            'failover': self.count_failover, 'step': self.count_step, 'note': self.count_note, 'wake': self.count_wake,
            'log': self.count_log,
        }
        self.timed = {
            'send-brief': self.on_send_brief, 'send-answer': self.on_send_brief, 'send-plan': self.on_send_plan,
            'send-consult': self.on_send_consult, 'send-steer': self.on_send_steer, 'step': self.on_step,
            'note': self.on_note, 'report': self.on_report, 'advice': self.on_advice, 'wake': self.on_wake,
            'log': self.on_log,
        }

    def record(self, e):
        """Every event is counted; only an event with a time moves the clock and the busy figures."""
        if e.kind in self.counters:
            self.counters[e.kind](e)
        if e.time is None:
            return
        if self.first is None:
            self.first = e.time
        self.last = e.time
        if e.kind in self.timed:
            self.timed[e.kind](e)

    def sk_start(self, t, unit, kind):
        if self.sk_on:
            self.sk_close(t, ' (stopped)')
        elif self.sk_free is not None:
            self.sk_idle += t - self.sk_free
            self.sk_gaps += 1
            if t == self.sk_free:
                self.pickups += 1
        self.sk_on, self.sk_since, self.sk_unit, self.sk_kind = True, t, unit, kind

    def sk_close(self, t, note):
        self.sk_on = False
        spent = t - self.sk_since
        self.sk_busy += spent
        self.sk_free = t
        if self.sk_kind == 'brief':
            self.brief_secs.append(spent)
            self.briefs.append(f'  {self.sk_unit} {minutes(spent)}{note}\n')
            if note == '':
                self.review_from = t
        else:
            self.plans += 1
            self.plan_sk += spent

    def sk_end(self, t, unit):
        """Only a report for the unit the sidekick holds ends it."""
        if self.sk_on and unit[:3] == self.sk_unit[:3]:
            self.sk_close(t, ' (paused)' if PAUSED_UNIT.search(unit) else '')

    def co_start(self, t, kind):
        if not self.co_on:
            self.co_on, self.co_since, self.co_kind = True, t, kind

    def co_end(self, t):
        if not self.co_on:
            return
        self.co_on = False
        spent = t - self.co_since
        self.co_busy += spent
        if self.co_kind == 'plan':
            self.plan_co += spent
        else:
            self.consults += 1
            self.consult_t += spent

    def count_send_brief(self, e):
        if e.detail == 'queued':
            self.taken += 1

    def count_queue(self, e):
        if e.unit != '-':
            self.queued.add(e.unit)

    def count_failover(self, e):
        self.failovers.append(e.detail)

    def count_step(self, e):
        self.nsteps += 1

    def count_note(self, e):
        self.nnotes += 1
        if e.detail.split(':')[1:2] == ['blocking']:
            self.nblock += 1

    def count_wake(self, e):
        self.wakes += 1
        if e.detail == 'checkin':
            self.checkins += 1

    def count_log(self, e):
        if not e.detail.startswith(('review:', 'plan:')):
            return
        for found in WORDS.findall(e.detail):
            word = found.lower()
            stem = word[:-2] if word.endswith('ed') else word
            if stem in ('accept', 'reject'):
                self.verdicts[stem] = self.verdicts.get(stem, 0) + 1
                break
            if word in ('agreed', 'revise'):
                self.verdicts[word] = self.verdicts.get(word, 0) + 1
                break

    def on_send_brief(self, e):
        self.sk_start(e.time, e.unit, 'brief')

    def on_send_plan(self, e):
        if e.detail == 'consultant':
            self.co_start(e.time, 'plan')
        else:
            self.sk_start(e.time, e.unit, 'plan')

    def on_send_consult(self, e):
        self.co_start(e.time, 'consult')

    def on_send_steer(self, e):
        if e.detail == 'objection':
            self.sk_start(e.time, e.unit, 'brief')

    def on_step(self, e):
        self.steps[e.unit, e.detail.split(':')[0]] = e.time

    def on_note(self, e):
        if e.note_id is None:
            return
        for n in range(self.noted.get(e.unit, 0) + 1, e.note_id + 1):
            if (e.unit, str(n)) in self.steps:
                self.stall_secs.append(e.time - self.steps[e.unit, str(n)])
        self.noted[e.unit] = e.note_id

    def on_report(self, e):
        if e.actor == 'sidekick':
            self.sk_end(e.time, e.unit)

    def on_advice(self, e):
        if e.actor == 'consultant':
            self.co_end(e.time)

    def on_wake(self, e):
        if e.detail.startswith(('report:', 'sidekick:')):
            self.sk_end(e.time, e.unit)
        if e.detail.startswith(('advice:', 'consultant:')):
            self.co_end(e.time)

    def on_log(self, e):
        if e.detail.startswith('review:') and self.review_from is not None:
            self.review_secs.append(e.time - self.review_from)
            self.review_from = None

    def report(self):
        first, last = self.first or 0, self.last or 0
        live = (self.sk_on or self.co_on) and self.now >= last
        end = self.now if live else last
        sk_busy, running = self.sk_busy, ''
        if self.sk_on:
            running = f'running: {self.sk_unit} for {minutes(end - self.sk_since)}\n'
            sk_busy += end - self.sk_since
        wall = end - first
        pct = lambda busy: int(100 * busy / wall) if wall else 0
        out = [f'wall: {minutes(wall)} from first event to {"now" if self.sk_on or self.co_on else "last"}\n',
               f'sidekick: busy {minutes(sk_busy)} ({pct(sk_busy)}% of wall), idle {minutes(self.sk_idle)} across {self.sk_gaps} gaps; '
               f'{len(self.brief_secs)} briefs (median {minutes(median(self.brief_secs))}), '
               f'{self.plans} plan responses ({minutes(self.plan_sk)})\n']
        if self.queued or self.taken:
            out.append(f'queue: {len(self.queued)} briefs queued, {self.taken} taken by the sidekick; '
                       f'{self.pickups} started the moment a report landed\n')
        if self.co_busy or self.consults or self.co_on:
            out.append(f'consultant: busy {minutes(self.co_busy)} ({pct(self.co_busy)}% of wall); '
                       f'{self.consults} consults ({minutes(self.consult_t)}), plan advice {minutes(self.plan_co)}\n')
        out.append(f'master: {self.wakes} wakes, {self.checkins} of them check-ins; '
                   f'review latency median {minutes(median(self.review_secs))} over {len(self.review_secs)} reviews\n')
        if self.failovers:
            out.append(f'failovers: {len(self.failovers)} ({", ".join(self.failovers)})\n')
        if self.nsteps:
            out.append(f'steps: {self.nsteps} committed, {self.nnotes} notes ({self.nblock} blocking); '
                       f'step to note median {minutes(median(self.stall_secs))}\n')
        verdicts = ''.join(f' {name} {self.verdicts[name]}' for name in ('agreed', 'accept', 'revise', 'reject') if name in self.verdicts)
        out.append(f'verdicts:{verdicts or " none"}\n')
        if self.brief_secs or running:
            out.append(f'briefs:\n{"".join(self.briefs)}{"  " + running if running else ""}')
        return ''.join(out)


def run(ctx, args):
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} metrics <store>')
    store = args[0]
    log = f'{store}/events.tsv'
    if not (os.path.exists(log) and os.stat(log).st_size > 0):
        raise Die(f'no events.tsv in {store}; it is written from the first command on')
    metrics = Metrics(env_int('PAIR_METRICS_NOW', int(time.time())))
    # A directory in place of the log is skipped with a warning and counts nothing; a log that cannot be read ends with exit 2.
    events = []
    if os.path.isdir(log):
        sys.stderr.write(f'{ctx.prog}: warning: {log} is a directory: skipped\n')
    else:
        try:
            events = parse_events(log)
        except OSError as e:
            sys.stderr.write(f'{ctx.prog}: cannot read {log}: {os.strerror(e.errno)}\n')
            return 2
    for event in events:
        metrics.record(event)
    sys.stdout.write(metrics.report())
    return 0
