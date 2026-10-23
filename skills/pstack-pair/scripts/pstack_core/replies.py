"""The readers of a store's replies, as the wait loop uses them. Each takes the pieces of state it reads from the store and returns what the
loop prints."""

import glob
import os
import re

from .review import noted_through, review_base
from .steplog import StepLog
from .store import (
    count,
    expected_report,
    field,
    header_field,
    load,
    names,
    peek,
    read_text,
    suffix_number,
    unit_name,
    update,
)
from .commands.status import latest


def file_mtime(path):
    """stat -c %Y: whole seconds, of a symlink itself (stat does not follow it)."""
    return int(os.lstat(path).st_mtime)


def latest_report(store, want=''):
    """The newest reports/<want>-*.md, or the newest NNN-*.md, or None."""
    return latest(store, 'reports', want or None)


def sent_field(store, key):
    """The text of the last send's key, or ''."""
    return field(store, 'sent', key)


def sent_epoch(store):
    """sent.epoch as an integer; absent is 0, any other type is damage."""
    return count(peek(load(store), 'sent'), 'epoch')


def dispatched(store):
    return field(store, 'dispatch', 'brief')


def first3(path):
    """basename | cut -c1-3."""
    return os.path.basename(path.rstrip('/') or path)[:3]


def queued_brief(store):
    """The brief waiting in the queue, as a command substitution gives it, or ''."""
    path = f'{store}/queue'
    return read_text(path).rstrip('\n') if os.path.isfile(path) else ''


def queue_note(store, report):
    """What queue_note prints after a report: whether the sidekick moved on to another unit's brief, and the queued brief."""
    out = ''
    running = dispatched(store)
    if running and first3(running) != first3(report):
        out += f'running: {running}\n'
    queued = queued_brief(store)
    if queued:
        # The queue never moves on its own: after any status it waits for the master's dispatch, which rotates a due sidekick first.
        status = header_field(report, 'status') if os.path.isfile(report) else 'done'
        held = (
            f' (held: report is {status or "unknown"}; dispatch it yourself)'
            if status != 'done'
            else ''
        )
        out += f'queued: {queued}{held}\n'
    return out


def pending(store):
    """The non-empty lines of the pending briefs; a pending that is not an array is damage."""
    return [
        line
        for item in names(load(store), 'pending')
        for line in item.split('\n')
        if line
    ]


def pending_report(ctx, store):
    """The oldest pending brief whose report exists and is ready, unless a review already covers its unit."""
    for brief in pending(store):
        if glob.glob(
            os.path.join(
                glob.escape(store), 'reviews', glob.escape(first3(brief)) + '-*.md'
            )
        ):
            continue
        report = expected_report(store, brief)
        if (
            os.path.isfile(report)
            and ctx.variant.report_ready(ctx, store, report).ready
        ):
            return report
    return None


def fresh_reply(ctx, store):
    """The newest report written since the last send that answers it, or None."""
    sent = sent_field(store, 'file')
    if not sent:
        return None
    unit = re.sub(r'-a[0-9]+$', '', unit_name(sent))
    floor = sent_epoch(store)
    reports = os.path.join(glob.escape(store), 'reports')
    candidates = [
        f'{store}/reports/{unit}.md',
        *sorted(glob.glob(os.path.join(reports, glob.escape(unit) + '-[sq][0-9]*.md'))),
    ]
    best, reply = -1, ''
    for f in candidates:
        if not os.path.isfile(f):
            continue
        t = file_mtime(f)
        if t < floor:
            continue
        if not ctx.variant.report_ready(ctx, store, f).ready:
            continue
        k = suffix_number(unit_name(f), 'sq')
        key = t * 1000 + (int(k) if k else 999)
        if key > best:
            best, reply = key, f
    if not reply:
        return None
    if f'{reply}:{file_mtime(reply)}' == sent_field(store, 'seen'):
        return None
    return reply


def seen_reply(store):
    """The reply the master was already shown for the last message, if it still exists."""
    seen = sent_field(store, 'seen')
    path = seen.rsplit(':', 1)[0] if ':' in seen else seen
    return path if seen and os.path.isfile(path) else None


def mark_seen(store, report):
    """Drop the brief whose report the master has now seen from pending. Rewrites pair.json even when nothing drops."""
    keep = [b for b in pending(store) if expected_report(store, b) != report]
    update(store, lambda doc: doc.__setitem__('pending', keep))


def unreviewed_steps(ctx, store):
    """Steps of the running brief past the last note, as "k<TAB>sha<TAB>summary" lines, or None."""
    brief = dispatched(store)
    if not brief:
        return None
    log = StepLog(store, brief)
    steps = log.rows()
    if not steps:
        return None
    report = expected_report(store, brief)
    if os.path.isfile(report):
        stamp = log.last().epoch
        written_after = stamp is not None and file_mtime(report) >= stamp
        if written_after and ctx.variant.report_ready(ctx, store, report).ready:
            return None
    through = noted_through(store, brief)
    rows = [
        f'{s.number}\t{s.sha}\t{s.summary}'
        for s in steps
        if s.index is not None and s.index > through
    ]
    return rows or None


def withheld_report(ctx, store):
    """(path, why) of the sent unit's report when it is written but withheld, or None."""
    sent = sent_field(store, 'file')
    if not sent:
        return None
    report = f'{store}/reports/{re.sub(r"-a[0-9]+$", "", unit_name(sent))}.md'
    floor = sent_epoch(store)
    if not (os.path.isfile(report) and file_mtime(report) >= floor):
        return None
    ready = ctx.variant.report_ready(ctx, store, report)
    return None if ready.ready else (report, ready.why)


def review_delta(store, report):
    """The one diff the final review reads, as the line review_delta prints, or ''."""
    unit = unit_name(report)
    last = StepLog(store, unit).last()
    if not last:
        return ''
    m = re.match(r'[0-9a-f]{7,40}', header_field(report, 'head'))
    if m:
        head = m.group(0)
    else:
        head = last.sha
    base = review_base(store, unit)
    return f'review-delta: {base[:9]}..{head[:9]} (steps reviewed through {noted_through(store, unit)})\n'
