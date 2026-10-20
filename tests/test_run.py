"""run.py fails a test that outlives its timeout and kills what the test started."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402

HANG = '''\
import os
import subprocess
import unittest

PIDFILE = {pidfile!r}
FIFO = {fifo!r}


class Hang(unittest.TestCase):
    def test_hangs(self):
        child = subprocess.Popen(['cat'], stdin=subprocess.PIPE)
        with open(PIDFILE, 'w') as f:
            f.write(str(child.pid))
        open(FIFO).read()

    def test_passes(self):
        pass
'''


def gone(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] == 'Z'
    except FileNotFoundError:
        return True


class RunOneTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.pidfile = self.tmp / 'child.pid'
        fifo = self.tmp / 'never-written'
        os.mkfifo(fifo)
        (self.tmp / 'test_hang.py').write_text(HANG.format(pidfile=str(self.pidfile), fifo=str(fifo)))

    def test_a_test_past_its_timeout_fails_and_its_processes_are_killed(self):
        test_id, code, _secs, out = run.run_one('test_hang.Hang.test_hangs', 2, cwd=self.tmp)
        self.assertEqual(code, 'timeout')
        self.assertTrue(out.startswith('TIMEOUT after 2s'), out)
        self.assertTrue(gone(int(self.pidfile.read_text())))

    def test_a_test_inside_its_timeout_passes(self):
        _id, code, _secs, _out = run.run_one('test_hang.Hang.test_passes', 2, cwd=self.tmp)
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
