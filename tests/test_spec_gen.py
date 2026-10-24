"""spec_gen.py --check builds SPEC.md's text without writing it and reports what is missing or stale."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLASSIFY = 'skills/pstack-kitchen/scripts/kitchen_core/classify.py'


def git(cwd, *args):
    subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    )


def copy_tracked_files(dest):
    listing = subprocess.run(
        ['git', 'ls-files', '-z'],
        cwd=REPO,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    for name in listing.decode().split('\0'):
        source = REPO / name
        if name and source.is_file():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / name)
    git(dest, 'init', '-q')
    git(dest, 'add', '-A')
    git(dest, 'commit', '-qm', 'fixture')


class SpecGenCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.copy = Path(tmp.name)
        copy_tracked_files(self.copy)
        self.spec = self.copy / 'tests' / 'SPEC.md'

    def spec_gen(self, *args):
        return subprocess.run(
            [sys.executable, 'tests/spec_gen.py', *args],
            cwd=self.copy,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_check_passes_on_a_fresh_spec_and_ignores_the_generated_at_sha(self):
        self.assertEqual(self.spec_gen().returncode, 0)
        fresh = self.spec.read_bytes()
        result = self.spec_gen('--check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ok: tests/SPEC.md is current, 0 missing', result.stdout)
        self.assertEqual(self.spec.read_bytes(), fresh)

        text = fresh.decode()
        marker = 'Generated at '
        start = text.index(marker) + len(marker)
        end = start + len(text[start:].split()[0])
        self.spec.write_text(text[:start] + '0000000' + text[end:])
        self.assertEqual(self.spec_gen('--check').returncode, 0)

    def test_check_fails_on_a_stale_spec_and_leaves_it_as_it_was(self):
        self.assertEqual(self.spec_gen().returncode, 0)
        lines = self.spec.read_text().splitlines(keepends=True)
        first = next(i for i, l in enumerate(lines) if l == '## Commands\n') + 2
        lines[first] = lines[first].replace('- ', '- edited ', 1)
        self.spec.write_text(''.join(lines))
        edited = self.spec.read_bytes()
        result = self.spec_gen('--check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('stale: tests/SPEC.md differs', result.stdout)
        self.assertEqual(self.spec.read_bytes(), edited)

        self.spec.unlink()
        result = self.spec_gen('--check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('stale: tests/SPEC.md differs', result.stdout)
        self.assertFalse(self.spec.exists())

    def test_check_fails_naming_a_line_no_test_covers(self):
        classify = self.copy / CLASSIFY
        classify.write_text(
            classify.read_text()
            + "\n\ndef spec_gen_check_fixture():\n    raise Die('spec gen check fixture', 3)\n"
        )
        result = self.spec_gen('--check')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        missing = [l for l in result.stdout.splitlines() if l.startswith('missing: ')]
        self.assertTrue(
            any('spec gen check fixture' in l for l in missing), result.stdout
        )

    def test_an_unknown_argument_exits_2(self):
        self.assertEqual(self.spec_gen('--bogus').returncode, 2)


if __name__ == '__main__':
    unittest.main()
