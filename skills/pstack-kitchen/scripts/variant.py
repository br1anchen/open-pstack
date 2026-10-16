import os
import sys
from pathlib import Path

from pstack_core.review import jq_raw
from pstack_core.store import dispatched_brief, expected_report, header_field, pair_json
from pstack_core.variant import ReportReadiness, commit_of, load_skill_variant

TrioVariant = load_skill_variant(Path(__file__).resolve().parents[2], 'pstack-trio')


def cksum(data):
    """POSIX cksum: the CRC-32 with polynomial 0x04C11DB7, fed the bytes and then the length."""
    crc = 0
    for byte in [*data, *length_bytes(len(data))]:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7 if crc & 0x80000000 else crc << 1) & 0xFFFFFFFF
    return ~crc & 0xFFFFFFFF


def length_bytes(n):
    out = []
    while n:
        out.append(n & 0xFF)
        n >>= 8
    return out


def verify_block(brief):
    """awk '/^## Verify/{p=1; next} /^## /{p=0} p' on the brief, as bytes (awk ends every record with a newline)."""
    try:
        with open(brief, 'rb') as f:
            records = f.read().split(b'\n')
    except OSError as e:
        sys.stderr.write(f"awk: fatal: cannot open file `{brief}' for reading: {os.strerror(e.errno)}\n")
        return b''
    if records[-1] == b'':
        records.pop()
    out, show = [], False
    for line in records:
        if line.startswith(b'## Verify'):
            show = True
        elif line.startswith(b'## '):
            show = False
        elif show:
            out.append(line + b'\n')
    return b''.join(out)


def verify_job(store, brief, head):
    """Where finish keeps the run of the brief's Verify block at commit head: one per head and block, so an amended block runs again."""
    unit = os.path.basename(brief)
    unit = unit[:-3] if unit.endswith('.md') and unit != '.md' else unit
    return f'{store}/steps/jobs/{unit}-verify-{head[:12]}-{cksum(verify_block(brief))}'


class KitchenVariant(TrioVariant):
    def report_ready(self, ctx, store, report):
        """A done report for the dispatched unit also needs the brief's Verify commands to have passed at its head,
        rerun by the kitchen: a wait never shows the master a done report that finish refused."""
        base = super().report_ready(ctx, store, report)
        if not base.ready:
            return base
        if header_field(report, 'status') != 'done':
            return base
        brief = dispatched_brief(store)
        if not brief or os.path.realpath(report) != expected_report(store, brief):
            return base
        head = commit_of(jq_raw(pair_json(store).get('git_root')), header_field(report, 'head'))
        if head is None:
            return ReportReadiness(False, '', 'its head: is not a commit')
        try:
            with open(f'{verify_job(store, brief, head)}/rc', 'rb') as f:
                rc = f.read().decode('utf-8', 'surrogateescape').rstrip('\n')
        except OSError:
            rc = ''
        if rc == '0':
            return base
        return ReportReadiness(False, '', f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)")


VARIANT = KitchenVariant
