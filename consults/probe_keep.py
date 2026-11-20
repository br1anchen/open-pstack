import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path('/home/user/workspace/repo/tests')))
import test_pstack_kitchen as t  # noqa: E402


class Probe(t.AmpKitchenTests):
    def test_probe_keep_after_rotation(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        print('D002a', out)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        print('D002b', out)
        self.write('docs/g.md', 'g\n')
        self.done(b, self.commit('guide'))
        print(
            'FIN',
            self.as_thread(
                t.amp_thread(2),
                'finish',
                str(self.store),
                str(self.store / 'reports' / b.name),
            ),
        )
        print('STATE', self.state()['sidekick'], self.state()['dispatch'])
        c = self.brief('003', 'guide2', ['docs/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(c), code=None)
        print('D003', out)


if __name__ == '__main__':
    unittest.main(argv=['x', 'Probe.test_probe_keep_after_rotation'])
