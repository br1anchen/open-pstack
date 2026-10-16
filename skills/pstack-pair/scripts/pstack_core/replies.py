"""The readers of a store's replies, as the wait loop in pair-core.sh uses them. Each twin takes the pieces of
state it reads from the store and returns what the bash function printed."""
import glob
import os
import re

from .collate import collate_key, glob_sorted
from .review import jq_raw, noted_through, review_base, steps_file
from .store import alt, bash_int, cut_chars, expected_report, header_field, last_line, pair_json, read_text, unit_name, update
from .commands.status import latest


def file_mtime(path):
    """stat -c %Y: whole seconds, of a symlink itself (stat does not follow it)."""
    return int(os.lstat(path).st_mtime)


def latest_report(store, want=''):
    """The newest reports/<want>-*.md, or the newest NNN-*.md, or None."""
    return latest(store, 'reports', want or None)


def sent_field(store, key):
    """field "$store" '.sent.<key> // empty', as jq -r prints it."""
    sent = alt(pair_json(store).get('sent'), {})
    return jq_raw(alt(sent.get(key) if isinstance(sent, dict) else None, ''))


def dispatched(store):
    dispatch = alt(pair_json(store).get('dispatch'), {})
    return jq_raw(alt(dispatch.get('brief') if isinstance(dispatch, dict) else None, ''))


def first3(path):
    """basename | cut -c1-3."""
    return cut_chars(os.path.basename(path.rstrip('/') or path), 3)


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
        out += f'queued: {queued}\n'
    return out


def pending(store):
    """jq -r '.pending // [] | .[]', the non-empty lines."""
    items = alt(pair_json(store).get('pending'), [])
    return [line for item in items for line in jq_raw(item).split('\n') if line]


def pending_report(ctx, store):
    """The oldest pending brief whose report exists and is ready, unless a review already covers its unit."""
    for brief in pending(store):
        if glob.glob(os.path.join(glob.escape(store), 'reviews', glob.escape(first3(brief)) + '-*.md')):
            continue
        report = expected_report(store, brief)
        if os.path.isfile(report) and ctx.variant.report_ready(ctx, store, report).ready:
            return report
    return None


def fresh_reply(ctx, store):
    """The newest report written since the last send that answers it, or None."""
    sent = sent_field(store, 'file')
    if not sent:
        return None
    unit = re.sub(r'-a[0-9]+$', '', unit_name(sent))
    epoch = sent_field(store, 'epoch') if sent_field(store, 'epoch') != '' else '0'
    reports = os.path.join(glob.escape(store), 'reports')
    candidates = [f'{store}/reports/{unit}.md', *glob_sorted(glob.glob(os.path.join(reports, glob.escape(unit) + '-[sq][0-9]*.md')))]
    best, reply = -1, ''
    for f in candidates:
        if not os.path.isfile(f):
            continue
        t = file_mtime(f)
        floor = bash_int(epoch)
        if floor is None or t < floor:
            continue
        if not ctx.variant.report_ready(ctx, store, f).ready:
            continue
        m = re.fullmatch(r'.*-[sq]([0-9]+)', unit_name(f), re.S)
        key = t * 1000 + (int(m.group(1)) if m else 999)
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


def awk_greater(field, k):
    """awk's $1 > k with a numeric string on the right: numeric when both look numeric, else text."""
    try:
        return float(field) > float(k)
    except ValueError:
        key = collate_key()
        return key(field) > key(str(k))


def unreviewed_steps(ctx, store):
    """Steps of the running brief past the last note, as "k<TAB>sha<TAB>summary" lines, or None."""
    brief = dispatched(store)
    if not brief:
        return None
    file = steps_file(store, brief)
    if not (os.path.isfile(file) and os.path.getsize(file) > 0):
        return None
    report = expected_report(store, brief)
    if os.path.isfile(report):
        cols = last_line(file).split('\t')
        stamp = bash_int(cols[2] if len(cols) > 2 else '')
        written_after = stamp is not None and file_mtime(report) >= stamp
        if written_after and ctx.variant.report_ready(ctx, store, report).ready:
            return None
    through = noted_through(store, brief)
    rows = []
    for row in read_text(file).removesuffix('\n').split('\n'):
        cols = row.split('\t')
        if awk_greater(cols[0], through):
            line = '\t'.join([cols[0], cols[1] if len(cols) > 1 else '', cols[4] if len(cols) > 4 else ''])
            if line:
                rows.append(line)
    return rows or None


def withheld_report(ctx, store):
    """(path, why) of the sent unit's report when it is written but withheld, or None."""
    sent = sent_field(store, 'file')
    if not sent:
        return None
    report = f'{store}/reports/{re.sub(r"-a[0-9]+$", "", unit_name(sent))}.md'
    epoch = sent_field(store, 'epoch') if sent_field(store, 'epoch') != '' else '0'
    floor = bash_int(epoch)
    if floor is None or not (os.path.isfile(report) and file_mtime(report) >= floor):
        return None
    ready = ctx.variant.report_ready(ctx, store, report)
    return None if ready.ready else (report, ready.why)


def review_delta(store, report):
    """The one diff the final review reads, as the line review_delta prints, or ''."""
    unit = unit_name(report)
    file = steps_file(store, unit)
    if not (os.path.isfile(file) and os.path.getsize(file) > 0):
        return ''
    m = re.match(r'[0-9a-f]{7,40}', header_field(report, 'head'))
    if m:
        head = m.group(0)
    else:
        cols = last_line(file).split('\t')
        head = cols[1] if len(cols) > 1 else cols[0]
    base = review_base(store, unit)
    return f'review-delta: {base.encode()[:9].decode(errors="replace")}..{head[:9]} (steps reviewed through {noted_through(store, unit)})\n'
