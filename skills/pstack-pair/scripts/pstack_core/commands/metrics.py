import os
import re
import sys
import time

from ..errors import Die

NUMBER = re.compile(r'[ \t\n]*[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


def num(value):
    """awk's number of a value: None is 0, a string is its leading decimal number or 0."""
    if value is None:
        return 0.0
    if isinstance(value, float):
        return value
    m = NUMBER.match(value)
    return float(m.group(0)) if m else 0.0


def looks_numeric(value):
    """Whether a field, awk -v value or uninitialised variable compares as a number."""
    if value is None or isinstance(value, float):
        return True
    m = NUMBER.match(value)
    return m is not None and value[m.end():].strip(' \t\n') == ''


def text(value):
    if value is None:
        return ''
    if isinstance(value, float):
        return str(int(value)) if value == int(value) else '%.6g' % value
    return value


def compare(a, b):
    """awk's comparison of two values: numeric when both look numeric, else as strings."""
    if looks_numeric(a) and looks_numeric(b):
        x, y = num(a), num(b)
    else:
        x, y = text(a), text(b)
    return (x > y) - (x < y)


def m(seconds):
    return '%.0fm' % (seconds / 60)


def median(values, n):
    """Insertion sort, then the lower-middle element: a[int((n + 1) / 2)]."""
    a = sorted(values[:n])
    return a[int((n + 1) / 2) - 1] if n else 0.0


def ascii_lower(s):
    return s.translate({c: c + 32 for c in range(ord('A'), ord('Z') + 1)})


class Metrics:
    """The awk program of cmd_metrics, line for line. Fields are strings, computed values floats, unset ones None."""

    def __init__(self, now):
        self.now = now
        self.first = self.last = None
        self.sk_on = self.co_on = False
        self.sk_since = self.sk_unit = self.sk_kind = self.sk_free = None
        self.co_since = self.co_kind = None
        self.sk_busy = self.sk_idle = self.co_busy = self.plan_sk = self.plan_co = self.consult_t = 0.0
        self.sk_gaps = self.pickups = self.taken = self.nb = self.np = self.nc = 0
        self.nfail = self.nsteps = self.nnotes = self.nblock = self.nsl = self.nr = self.wakes = self.checkins = 0
        self.fails = ''
        self.bl = ''
        self.review_from = None
        self.bd, self.sl, self.rl = [], [], []
        self.queued, self.st, self.noted, self.verdicts = set(), {}, {}, {}

    def sk_start(self, t, u, kind):
        if self.sk_on:
            self.sk_close(t, ' (stopped)')
        elif text(self.sk_free) != '':
            self.sk_idle += num(t) - num(self.sk_free)
            self.sk_gaps += 1
            if compare(t, self.sk_free) == 0:
                self.pickups += 1
        self.sk_on, self.sk_since, self.sk_unit, self.sk_kind = True, t, u, kind

    def sk_close(self, t, note):
        self.sk_on = False
        self.sk_busy += num(t) - num(self.sk_since)
        self.sk_free = t
        if self.sk_kind == 'brief':
            self.nb += 1
            self.bd.append(num(t) - num(self.sk_since))
            self.bl += '  %s %s%s\n' % (self.sk_unit, m(num(t) - num(self.sk_since)), note)
            if note == '':
                self.review_from = t
        else:
            self.np += 1
            self.plan_sk += num(t) - num(self.sk_since)

    def sk_end(self, t, u):
        """Only a report for the unit the sidekick holds ends it."""
        if self.sk_on and u[:3] == self.sk_unit[:3]:
            self.sk_close(t, ' (paused)' if re.search(r'-[sq][0-9]+$', u) else '')

    def co_start(self, t, kind):
        if not self.co_on:
            self.co_on, self.co_since, self.co_kind = True, t, kind

    def co_end(self, t):
        if not self.co_on:
            return
        self.co_on = False
        self.co_busy += num(t) - num(self.co_since)
        if self.co_kind == 'plan':
            self.plan_co += num(t) - num(self.co_since)
        else:
            self.nc += 1
            self.consult_t += num(t) - num(self.co_since)

    def record(self, fields):
        t, actor, ev, u, d = (fields + [''] * 6)[1:6]
        if not self.first:
            self.first = t
        self.last = t
        if ev in ('send-brief', 'send-answer'):
            self.sk_start(t, u, 'brief')
            if d == 'queued':
                self.taken += 1
        elif ev == 'send-plan' and d != 'consultant':
            self.sk_start(t, u, 'plan')
        elif ev == 'send-plan' and d == 'consultant':
            self.co_start(t, 'plan')
        elif ev == 'send-consult':
            self.co_start(t, 'consult')
        elif ev == 'send-steer' and d == 'objection':
            self.sk_start(t, u, 'brief')
        elif ev == 'queue' and u != '-':
            self.queued.add(u)
        elif ev == 'failover':
            self.nfail += 1
            self.fails += ', ' + d
        elif ev == 'step':
            self.nsteps += 1
            parts = d.split(':') if d else []
            self.st[u + '\x1c' + (parts[0] if parts else '')] = t
        elif ev == 'note':
            self.nnotes += 1
            parts = d.split(':') if d else []
            k1 = parts[0] if parts else None
            if len(parts) > 1 and parts[1] == 'blocking':
                self.nblock += 1
            i = num(self.noted.get(u)) + 1
            while compare(i, k1) <= 0:
                if u + '\x1c' + text(i) in self.st:
                    self.nsl += 1
                    self.sl.append(num(t) - num(self.st[u + '\x1c' + text(i)]))
                i += 1
            self.noted[u] = k1
        elif actor == 'sidekick' and ev == 'report':
            self.sk_end(t, u)
        elif actor == 'consultant' and ev == 'advice':
            self.co_end(t)
        elif ev == 'wake':
            self.wakes += 1
            if d == 'checkin':
                self.checkins += 1
            if d.startswith('report:') or d.startswith('sidekick:'):
                self.sk_end(t, u)
            if d.startswith('advice:') or d.startswith('consultant:'):
                self.co_end(t)
        elif ev == 'log' and (d.startswith('review:') or d.startswith('plan:')):
            for w in re.split(r'[^a-z]+', ascii_lower(d)):
                v = w[:-2] if w.endswith('ed') else w
                if v in ('accept', 'reject'):
                    self.verdicts[v] = self.verdicts.get(v, 0) + 1
                    break
                if w in ('agreed', 'revise'):
                    self.verdicts[w] = self.verdicts.get(w, 0) + 1
                    break
            if d.startswith('review:') and text(self.review_from) != '':
                self.nr += 1
                self.rl.append(num(t) - num(self.review_from))
                self.review_from = ''

    def report(self):
        live = (self.sk_on or self.co_on) and compare(self.now, self.last) >= 0
        end = self.now if live else self.last
        running = ''
        if self.sk_on:
            running = 'running: %s for %s\n' % (self.sk_unit, m(num(end) - num(self.sk_since)))
            self.sk_busy += num(end) - num(self.sk_since)
        wall = num(end) - num(self.first)
        out = ['wall: %s from first event to %s\n' % (m(wall), 'now' if (self.sk_on or self.co_on) else 'last')]
        pct = lambda busy: int(100 * busy / wall) if wall else 0
        out.append('sidekick: busy %s (%d%% of wall), idle %s across %d gaps; %d briefs (median %s), %d plan responses (%s)\n'
                   % (m(self.sk_busy), pct(self.sk_busy), m(self.sk_idle), self.sk_gaps, self.nb, m(median(self.bd, self.nb)),
                      self.np, m(self.plan_sk)))
        q = len(self.queued)
        if q or self.taken:
            out.append('queue: %d briefs queued, %d taken by the sidekick; %d started the moment a report landed\n'
                       % (q, self.taken, self.pickups))
        if self.co_busy or self.nc or self.co_on:
            out.append('consultant: busy %s (%d%% of wall); %d consults (%s), plan advice %s\n'
                       % (m(self.co_busy), pct(self.co_busy), self.nc, m(self.consult_t), m(self.plan_co)))
        out.append('master: %d wakes, %d of them check-ins; review latency median %s over %d reviews\n'
                   % (self.wakes, self.checkins, m(median(self.rl, self.nr)), self.nr))
        if self.nfail:
            out.append('failovers: %d (%s)\n' % (self.nfail, self.fails[2:]))
        if self.nsteps:
            out.append('steps: %d committed, %d notes (%d blocking); step to note median %s\n'
                       % (self.nsteps, self.nnotes, self.nblock, m(median(self.sl, self.nsl))))
        v = ''.join(' %s %d' % (name, self.verdicts[name]) for name in ('agreed', 'accept', 'revise', 'reject') if name in self.verdicts)
        out.append('verdicts:%s\n' % (v or ' none'))
        if self.nb or running:
            out.append('briefs:\n%s%s' % (self.bl, '  ' + running if running else ''))
        return ''.join(out)


def run(ctx, args):
    if len(args) != 1:
        raise Die('usage: pair.sh metrics <store>')
    store = args[0]
    log = f'{store}/events.tsv'
    if not (os.path.exists(log) and os.stat(log).st_size > 0):
        raise Die(f'no events.tsv in {store}; it is written from the first command on')
    metrics = Metrics(os.environ.get('PAIR_METRICS_NOW') or str(int(time.time())))
    # awk skips a directory with a warning and reads nothing, and stops with exit 2 on a file it cannot open.
    if os.path.isdir(log):
        sys.stderr.write(f"awk: warning: command line argument `{log}' is a directory: skipped\n")
        records = []
    else:
        try:
            with open(log, 'rb') as f:
                records = f.read().decode('utf-8', 'surrogateescape').split('\n')
        except OSError as e:
            sys.stderr.write(f"awk: fatal: cannot open file `{log}' for reading: {os.strerror(e.errno)}\n")
            return 2
    if records and records[-1] == '':
        records.pop()
    # event() writes no header; a store from an older build may start with one, and that line is skipped.
    if records and records[0].startswith('ts\t'):
        records = records[1:]
    for row in records:
        metrics.record(row.split('\t'))
    sys.stdout.write(metrics.report())
    return 0
