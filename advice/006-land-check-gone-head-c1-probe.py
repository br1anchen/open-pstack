"""Read-only design evidence, run only with PROBE_ROOT pointing at a kitchen scratch."""

import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(os.environ['PROBE_ROOT']).resolve()
tempfile.tempdir = str(ROOT)
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'skills/pstack-pair/scripts'),
               str(ROOT / 'skills/pstack-kitchen/scripts')]
from test_pstack_kitchen import KitchenScriptTests
from pstack_core import bundle
from kitchen_core.landing import landed_as
from kitchen_core.verify_packet import landing_covered_briefs


def present(repo, oid):
    return subprocess.run(
        ['git', '-C', str(repo), 'cat-file', '-e', f'{oid}^{{commit}}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def collect(t):
    t.git('reflog', 'expire', '--expire=now', '--all')
    t.git('gc', '--prune=now')


t = KitchenScriptTests()
try:
    t.setUp()
    base, old, verdict = t.landing_range_world()
    t.write_landing(1, base, old)
    assert 'land-check: pass' in t.land_check(0)
    old_branch = t.git('symbolic-ref', '--short', 'HEAD')
    t.git('checkout', '-qb', 'rewritten', base)
    t.write('docs/new-trunk.md', 'new upstream\n')
    upstream = t.commit('new upstream')
    t.git('cherry-pick', old)
    new = t.git('rev-parse', 'HEAD')
    assert old != new
    t.git('branch', '-D', old_branch)
    pin = f'refs/pstack/keep/probe/001/{old}'
    t.git('update-ref', pin, old)
    collect(t)
    assert present(t.repo, old)
    assert 'land-check: pass' in t.land_check(0)
    print('RETAINED REF: old unit and landing head survive GC; original CLI still passes')

    archive = Path(t.tmp.name) / 'ordinary.tgz'
    bundle.pack(str(t.store), str(archive), {'name': 'sender', 'thread': 'sender'}, {}, {})
    manifest, files, code = bundle.read_bundle(str(archive))
    assert code
    receiver = Path(t.tmp.name) / 'receiver'
    receiver.mkdir()
    t.git('-C', str(receiver), 'init', '-q')
    code_path = Path(t.tmp.name) / 'code.bundle'
    code_path.write_bytes(code)
    assert bundle.fetch(str(receiver), str(code_path), 'probe')
    assert present(receiver, new)
    assert not present(receiver, old)
    print('TRANSPORT GAP: ordinary bundle carries HEAD but not the retained evidence ref')
    explicit = Path(t.tmp.name) / 'evidence.bundle'
    assert bundle.git_bundle(str(t.repo), {'head': new, f'evidence/{old}': old}, str(explicit))
    assert bundle.fetch(str(receiver), str(explicit), 'probe-evidence')
    assert present(receiver, old)
    t.git('-C', str(receiver), 'reflog', 'expire', '--expire=now', '--all')
    t.git('-C', str(receiver), 'gc', '--prune=now')
    assert present(receiver, old)
    print('EXPLICIT TRANSPORT: evidence tip fetched under a ref survives receiver GC')

    t.git('update-ref', '-d', pin)
    collect(t)
    assert not present(t.repo, old)
    t.git('update-ref', 'refs/remotes/origin/main', new)
    out = t.land_check(2)
    assert 'range head does not resolve' in out
    assert 'unverified: unit 001 has no clean verdict' in out
    print('MISSING EVIDENCE: same landed code, old objects pruned, land-check exits 2')
    t.write_landing(2, upstream, new)
    out = t.land_check(2)
    assert 'unverified: unit 001 has no clean verdict' in out
    assert landing_covered_briefs(str(t.store), str(t.repo), upstream, new) == []
    print('FRESH LANDING GAP: even a new verdict at the rewritten tip cannot associate the missing unit')

    individual = t.store / 'verdicts/001-util-v1.md'
    individual.write_text(
        f'status: clean\nunits: 001\nrange: {base}..{old}\n\n'
        '## Evidence\n\n```\nfixture\n```\n'
    )
    assert 'land-check: pass' in t.land_check(0)
    print('CONTRACT ASYMMETRY: an explicitly named clean unit verdict passes with its objects missing')
    individual.unlink()
    recovery = t.brief('002', 'recovery', ['src/**', 'tests/**'])
    t.dispatch(recovery)
    t.done(recovery, new)
    result = t.run_sh('verify', str(t.store), '002', '--covers', '001', '--base', base)
    assert 'unit 001 has no usable head; its inclusion was not checked' in result
    packet = next((t.store / 'verdicts').glob('002-recovery-v*-packet.md')).read_text()
    assert f'range: {base}..{new}' in packet
    assert 'units: 001 002' in packet and '## Unit 001' in packet
    assert 'land-check: pass' in t.land_check(0)
    print('RECOVERY PATH: explicit --covers with a live report and --base sends the missing unit Acceptance at the current head; fake clean verdict clears it')
finally:
    t.doCleanups()

t = KitchenScriptTests()
try:
    t.setUp()
    base, old, verdict = t.landing_range_world()
    t.write_landing(1, base, old)
    t.write('src/util.py', 'X = 999\n')
    t.commit('unverified behavior change after verdict')
    out = t.land_check(0)
    assert '1 commit behind HEAD' in out
    assert 'land-check: pass' in out
    assert t.git('show', f'{old}:src/util.py') == 'X = 3'
    assert t.git('show', 'HEAD:src/util.py') == 'X = 999'
    print('STRICT CONTRACT GAP: changed unverified tip still passes with a behind-HEAD note')

    t.git('checkout', '-qb', 'landed-and-reverted', base)
    t.write('docs/new-trunk.md', 'upstream\n')
    t.commit('upstream')
    t.git('cherry-pick', old)
    replayed = t.git('rev-parse', 'HEAD')
    t.git('revert', '--no-edit', replayed)
    t.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
    how, trunk = landed_as(str(t.repo), base, old)
    assert how == 'rebased'
    assert t.git('show', 'HEAD:src/util.py') == 'X = 1'
    print('LANDED DIAGNOSTIC: landed_as returns rebased even after trunk reverts the change')
finally:
    t.doCleanups()

t = KitchenScriptTests()
try:
    t.setUp()
    # The per-store-file limit does not apply to the separate git.bundle member.
    t.write('bulk.dat', random.Random(0).randbytes((1 << 20) + 65536).hex())
    t.commit('fixture larger than store-file limit')
    (t.store / 'too-big.log').write_bytes(b'x' * ((1 << 20) + 1))
    archive = Path(t.tmp.name) / 'large.tgz'
    bundle.pack(str(t.store), str(archive), {'name': 'sender', 'thread': 'sender'}, {}, {})
    _, files, code = bundle.read_bundle(str(archive))
    assert 'too-big.log' not in files
    assert code and len(code) > (1 << 20)
    print('SIZE LIMIT: >1 MiB store file omitted; >1 MiB git.bundle transported')
finally:
    t.doCleanups()
