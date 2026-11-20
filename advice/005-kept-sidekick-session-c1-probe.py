import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRATCH = Path(os.environ.get('PROBE_ROOT', '/home/user/workspace/repo/.amp/in/orb-backup/consult-scratch/runs-kitchen-followups/005-kept-sidekick-session-c1'))
tempfile.tempdir = '/home/user/workspace/repo/.amp/in/orb-backup'
sys.path.insert(0, str(SCRATCH / 'tests'))
import test_pstack_kitchen as t

if os.environ.get('PROBE_SKILLS'):
    t.skill = Path(os.environ['PROBE_SKILLS']) / 'pstack-kitchen'
    t.script = t.skill / 'scripts/kitchen.py'


class Probe(t.AmpKitchenTests):
    def bundle_for(self, thread, name):
        return max(
            (self.fake / 'amp-threads' / thread).glob(f'*-pstack-{name}.tgz'),
            key=lambda p: int(p.name.split('-', 1)[0]),
        )

    def save_store(self, key):
        target = Path(self.tmp.name) / f'{key}-store'
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.store, target, symlinks=True)

    def load_store(self, key):
        shutil.rmtree(self.store)
        source = Path(self.tmp.name) / f'{key}-store'
        if source.exists():
            shutil.copytree(source, self.store, symlinks=True)
        else:
            self.store.mkdir(parents=True)

    def role(self, thread, *args):
        return self.as_thread(
            thread, *args, mux='amp',
            env={'XDG_STATE_HOME': str(Path(self.tmp.name) / f'state-{thread}')},
        )

    def print_state(self, label):
        state = self.state()
        fields = ('name', 'generation', 'pane_id', 'rotation_required', 'bootstrap_pending')
        print(label, {k: state['sidekick'].get(k) for k in fields}, state.get('dispatch', {}).get('brief'))

    def ready_remote(self, thread):
        bootstrap = self.bundle_for(thread, 'demo-sidekick')
        self.save_store('master')
        self.load_store('sidekick')
        self.role(thread, 'sync', str(bootstrap))
        ready = self.store / 'reports/000-ready.md'
        ready.parent.mkdir(exist_ok=True)
        ready.write_text('status: done\n')
        self.role(thread, 'notify', str(self.store), str(ready))
        reply = self.bundle_for(t.AMP_MASTER, 'demo-master')
        self.save_store('sidekick')
        self.load_store('master')
        self.run_sh('sync', str(reply))

    def remote_unit(self, seq, scope, path, thread=1):
        b = self.brief(seq, f'unit{seq}', scope, verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        print('DISPATCH', seq, '\n'.join(line for line in out.splitlines() if 'kept:' in line or 'rotated:' in line))
        self.assertIn('pstack-kitchen BRIEF', out)
        sent = self.bundle_for(t.amp_thread(thread), 'demo-sidekick')
        self.save_store('master')
        self.load_store('sidekick')
        self.role(t.amp_thread(thread), 'sync', str(sent))
        self.write(path, f'unit {seq}\n')
        self.done(b, self.commit(seq))
        report = self.store / 'reports' / b.name
        self.role(t.amp_thread(thread), 'finish', str(self.store), str(report))
        reply = self.bundle_for(t.AMP_MASTER, 'demo-master')
        self.save_store('sidekick')
        self.load_store('master')
        self.run_sh('sync', str(reply))
        self.print_state(f'AFTER {seq}')
        return out

    def test_two_orbs_keep(self):
        self.ready_remote(t.amp_thread(1))
        self.remote_unit('001', ['src/**', 'tests/**'], 'src/one.py')
        out = self.remote_unit('002', ['src/**', 'tests/**'], 'src/two.py')
        self.assertIn('keeps its session', out)
        out = self.remote_unit('003', ['src/**', 'tests/**'], 'src/three.py')
        self.assertIn('keeps its session', out)
        self.assertEqual((self.fake / 'amp-started').read_text(), '1')

    def test_two_orbs_keep_after_rotation(self):
        self.ready_remote(t.amp_thread(1))
        self.remote_unit('001', ['src/**', 'tests/**'], 'src/one.py')
        b = self.brief('002', 'unit002', ['docs/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertIn('rotated:', out)
        self.print_state('ROTATED')
        # A new thread starts with an empty orb, not the previous role's store.
        shutil.rmtree(Path(self.tmp.name) / 'sidekick-store')
        self.ready_remote(t.amp_thread(2))
        self.remote_unit('002', ['docs/**'], 'docs/two.md', thread=2)
        out = self.remote_unit('003', ['docs/**'], 'docs/three.md', thread=2)
        self.assertIn('keeps its session', out)

    def test_delayed_verifier_preserves_dispatch(self, newer_outbound=False):
        self.ready_remote(t.amp_thread(1))
        self.remote_unit('001', ['src/**', 'tests/**'], 'src/one.py')
        verifier = self.send_from_the_master('demo-verifier', f'pstack-kitchen NOTE {self.store}/standing-orders.md')
        sent = self.bundle_for(verifier, 'demo-verifier')
        self.save_store('master')
        self.load_store('verifier')
        self.role(verifier, 'sync', str(sent))
        self.save_store('verifier')
        self.load_store('master')
        if newer_outbound:
            b = self.brief('002', 'unit002', ['docs/**'], verify=['python3 -c 1'])
            out = self.run_sh('dispatch', str(self.store), str(b), code=4)
            self.assertIn('rotated:', out)
            shutil.rmtree(Path(self.tmp.name) / 'sidekick-store')
            self.ready_remote(t.amp_thread(3))
            self.remote_unit('002', ['docs/**'], 'docs/two.md', thread=3)
        else:
            self.remote_unit('002', ['src/**', 'tests/**'], 'src/two.py')
        before = self.state()
        if newer_outbound:
            self.send_from_the_master('demo-verifier', f'pstack-kitchen NOTE {self.store}/briefs/002-unit002.md', thread=verifier)
        self.save_store('master')
        self.load_store('verifier')
        verdict = self.store / 'verdicts/001-probe.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text('status: clean\nunits: 001\n')
        self.role(verifier, 'notify', str(self.store), str(verdict))
        reply = self.bundle_for(t.AMP_MASTER, 'demo-master')
        self.load_store('master')
        self.run_sh('sync', str(reply))
        after = self.state()
        if newer_outbound:
            self.assertTrue(before['dispatch']['brief'].endswith('002-unit002.md'))
            self.assertTrue(after['dispatch']['brief'].endswith('001-unit001.md'))
            b = self.brief('003', 'unit003', ['docs/**'], verify=['python3 -c 1'])
            out = self.run_sh('dispatch', str(self.store), str(b), code=4)
            self.assertIn('rotated:', out)
            self.assertNotIn('keeps its session', out)
            print('NEWER OUTBOUND overwrote merge base; delayed verifier rolled dispatch 002 back to 001; routine docs unit 003 rotated after routine docs unit 002 at 10% context')
            return
        self.assertEqual(after['dispatch'], before['dispatch'])
        self.assertEqual(after['sidekick'], before['sidekick'])
        print('DELAYED VERIFIER preserved current dispatch and sidekick')
        out = self.remote_unit('003', ['src/**', 'tests/**'], 'src/three.py')
        self.assertIn('keeps its session', out)

    def test_newer_outbound_exposes_stale_base(self):
        self.test_delayed_verifier_preserves_dispatch(newer_outbound=True)


if __name__ == '__main__':
    unittest.main(argv=['probe', 'Probe.test_two_orbs_keep', 'Probe.test_two_orbs_keep_after_rotation', 'Probe.test_delayed_verifier_preserves_dispatch', 'Probe.test_newer_outbound_exposes_stale_base'])
