import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scripts = root / 'skills/pstack-kitchen/scripts'
fixtures = root / 'tests/fixtures/context'
spec = importlib.util.spec_from_file_location('context', scripts / 'context.py')
assert spec and spec.loader
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)

GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')} | {
    'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_AUTHOR_NAME': 'Fixture',
    'GIT_AUTHOR_EMAIL': 'fixture@example.com',
    'GIT_COMMITTER_NAME': 'Fixture',
    'GIT_COMMITTER_EMAIL': 'fixture@example.com',
}
SAMPLE = (fixtures / 'sample.toml').read_text()
STANDARDS = (fixtures / 'repo/STANDARDS.md').read_bytes()
PLACEHOLDER_BASE = '0123456789abcdef0123456789abcdef01234567'
PLACEHOLDER_SHA = {
    'STANDARDS.md': '0' * 63 + '1',
    '.agents/kitchen.toml': '0' * 63 + '2',
}


def git(cwd, *args):
    done = subprocess.run(
        [
            'git',
            '-c',
            'commit.gpgsign=false',
            '-c',
            'core.hooksPath=/dev/null',
            *args,
        ],
        cwd=cwd,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def refused(test, text, fragment):
    with test.assertRaises(context.ContextError) as caught:
        context.parse_migration(text)
    test.assertIn(fragment, str(caught.exception))


def edit(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


def fixture_repo(test):
    """A git repository holding the fixture repo, and the sample migration pinned to its commit."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    repo = Path(tmp.name) / 'repo'
    shutil.copytree(fixtures / 'repo', repo)
    git(repo, 'init', '-q')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'fixture')
    base = git(repo, 'rev-parse', 'HEAD')
    text = SAMPLE.replace(PLACEHOLDER_BASE, base)
    for path, placeholder in PLACEHOLDER_SHA.items():
        text = text.replace(placeholder, sha((repo / path).read_bytes()))
    return repo, text


class LoaderTests(unittest.TestCase):
    def test_the_documented_sample_loads(self):
        m = context.load(fixtures / 'sample.toml')
        self.assertEqual(
            (m.schema, m.id, m.base), (1, 'standards-split', PLACEHOLDER_BASE)
        )
        self.assertEqual(
            [(f.path, f.sha256[-1]) for f in m.files],
            [('STANDARDS.md', '1'), ('.agents/kitchen.toml', '2')],
        )
        self.assertEqual(
            m.moves[1],
            context.Move(
                'STANDARDS.md',
                ('Coding standards', 'Typed evidence', 'Rust (Engine)'),
                None,
                'docs/standards/rust.md',
                ('docs/standards/typed-evidence.md',),
            ),
        )
        self.assertEqual(
            m.rewrites,
            (
                context.Rewrite(
                    '.agents/kitchen.toml',
                    'STANDARDS.md#typed-evidence',
                    'docs/standards/typed-evidence.md#typed-evidence',
                ),
            ),
        )
        self.assertEqual(m.keeps[0].reason, 'ADRs are historical records')
        self.assertEqual(len(m.writes), 5)

    def test_an_occurrence_is_read_from_a_move(self):
        text = edit(
            SAMPLE,
            'ancestry = ["Coding standards", "Testing", "Notes"]\n',
            'ancestry = ["Coding standards", "Testing", "Notes"]\noccurrence = 2\n',
        )
        self.assertEqual(context.parse_migration(text).moves[2].occurrence, 2)

    def test_a_schema_other_than_one_is_refused(self):
        refused(self, edit(SAMPLE, 'schema = 1', 'schema = 2'), '`schema` must be 1')

    def test_text_that_is_not_toml_is_refused(self):
        refused(self, 'schema = ', 'not valid TOML')

    def test_a_missing_migration_file_is_refused(self):
        with self.assertRaises(context.ContextError) as caught:
            context.load(fixtures / 'nowhere.toml')
        self.assertIn('cannot read migration', str(caught.exception))

    def test_root_keys_are_checked(self):
        refused(
            self,
            edit(SAMPLE, 'schema = 1\n', 'schema = 1\ncolour = 1\n'),
            'unknown root key `colour`',
        )
        refused(
            self,
            edit(SAMPLE, 'id = "standards-split"\n', ''),
            '`id` must be a non-empty string',
        )
        refused(self, edit(SAMPLE, 'id = "standards-split"', 'id = "a b"'), '`id`')
        refused(
            self,
            edit(SAMPLE, f'base = "{PLACEHOLDER_BASE}"\n', ''),
            '`base` must be a non-empty string',
        )
        refused(
            self,
            edit(SAMPLE, PLACEHOLDER_BASE, '-x'),
            'must be a commit, not an option',
        )

    def test_a_table_key_is_checked(self):
        refused(
            self,
            edit(
                SAMPLE,
                'to = "docs/standards/typed-evidence.md"\n',
                'to = "docs/standards/typed-evidence.md"\nselect = "heading:Typed evidence"\n',
            ),
            '[[move]] 1: unknown key `select`',
        )
        refused(
            self,
            edit(SAMPLE, 'ancestry = ["Coding standards", "Typed evidence"]\n', ''),
            '`ancestry` must be a list of non-empty strings',
        )
        refused(
            self,
            edit(SAMPLE, 'reason = "ADRs are historical records"\n', ''),
            '[[keep]] 1: `reason` must be a non-empty string',
        )

    def test_a_table_of_the_wrong_kind_is_refused(self):
        refused(
            self,
            edit(SAMPLE, '[[keep]]', '[keep]'),
            '`keep` must be an array of tables',
        )

    def test_a_value_of_the_wrong_type_is_refused(self):
        for bad in ('"2"', '0', 'true', '1.5'):
            with self.subTest(occurrence=bad):
                text = edit(
                    SAMPLE,
                    'ancestry = ["Coding standards", "Testing", "Notes"]\n',
                    f'ancestry = ["Coding standards", "Testing", "Notes"]\noccurrence = {bad}\n',
                )
                refused(self, text, '`occurrence` must be a whole number from 1')
        refused(
            self,
            edit(
                SAMPLE,
                'ancestry = ["Coding standards", "Typed evidence"]',
                'ancestry = []',
            ),
            'at least one heading',
        )
        refused(
            self,
            edit(
                SAMPLE,
                'requires = ["docs/standards/typed-evidence.md"]',
                'requires = "x"',
            ),
            '`requires` must be a list',
        )

    def test_writes_placed_after_an_array_table_is_not_a_root_key(self):
        start = SAMPLE.index('writes = [')
        stop = SAMPLE.index(']\n', start) + 2
        block = SAMPLE[start:stop]
        moved = SAMPLE[:start] + SAMPLE[stop:] + '\n' + block
        refused(self, moved, '`writes` is not a root key here: it follows [[keep]] 1')

    def test_writes_must_be_exact_unique_and_complete(self):
        refused(
            self,
            edit(SAMPLE, '"docs/standards/rust.md",\n  ', '"docs/standards/*.md",\n  '),
            'is a glob',
        )
        refused(
            self,
            edit(
                SAMPLE,
                '".agents/kitchen.toml",\n]',
                '".agents/kitchen.toml",\n  ".agents/kitchen.toml",\n]',
            ),
            "writes: '.agents/kitchen.toml' is listed twice",
        )
        refused(
            self,
            edit(SAMPLE, '"docs/standards/rust.md",\n  ', ''),
            "'docs/standards/rust.md' is a source, destination or rewrite target but is not in writes",
        )
        refused(
            self,
            edit(
                SAMPLE,
                '".agents/kitchen.toml",\n]',
                '".agents/kitchen.toml",\n  "README.md",\n]',
            ),
            "'README.md' is not a source, destination or rewrite target",
        )

    def test_a_path_must_stay_inside_the_repo(self):
        for bad in ('../evil.md', '/abs.md', 'a\\\\b.md', 'a//b.md', './a.md', ''):
            with self.subTest(path=bad):
                text = edit(SAMPLE, 'to = "docs/standards/rust.md"', f'to = "{bad}"')
                refused(self, text, 'path')

    def test_a_sha256_must_be_64_lowercase_hex_digits(self):
        refused(self, edit(SAMPLE, '0' * 63 + '1', 'ABC'), '64 lowercase hex digits')

    def test_a_file_listed_twice_is_refused(self):
        refused(
            self,
            edit(
                SAMPLE,
                '[[move]]',
                f'[[file]]\npath = "STANDARDS.md"\nsha256 = "{"0" * 64}"\n\n[[move]]',
            ),
            "'STANDARDS.md' is listed twice",
        )


class BaseTests(unittest.TestCase):
    def test_every_file_is_read_from_the_base_commit(self):
        repo, text = fixture_repo(self)
        found = context.read_base(repo, context.parse_migration(text))
        self.assertEqual(found['STANDARDS.md'], STANDARDS)
        self.assertEqual(set(found), {'STANDARDS.md', '.agents/kitchen.toml'})

    def test_a_base_that_does_not_resolve_is_refused(self):
        repo, text = fixture_repo(self)
        m = context.parse_migration(
            text.replace(git(repo, 'rev-parse', 'HEAD'), 'f' * 40)
        )
        with self.assertRaises(context.ContextError) as caught:
            context.read_base(repo, m)
        self.assertIn('does not resolve to a commit', str(caught.exception))

    def test_a_path_missing_at_the_base_is_refused(self):
        repo, text = fixture_repo(self)
        m = context.parse_migration(
            text.replace('path = ".agents/kitchen.toml"\nsha', 'path = "gone.md"\nsha')
        )
        with self.assertRaises(context.ContextError) as caught:
            context.read_base(repo, m)
        self.assertIn('gone.md: not present at base', str(caught.exception))

    def test_a_sha256_mismatch_names_the_path(self):
        repo, text = fixture_repo(self)
        m = context.parse_migration(text.replace(sha(STANDARDS), '1' * 64))
        with self.assertRaises(context.ContextError) as caught:
            context.read_base(repo, m)
        self.assertIn('STANDARDS.md: sha256', str(caught.exception))
        self.assertIn('differs from the migration', str(caught.exception))

    def test_the_working_tree_is_not_consulted(self):
        repo, text = fixture_repo(self)
        (repo / 'STANDARDS.md').write_bytes(b'# changed\n')
        found = context.read_base(repo, context.parse_migration(text))
        self.assertEqual(found['STANDARDS.md'], STANDARDS)

    def test_destinations_at_the_base_are_reported(self):
        repo, text = fixture_repo(self)
        m = context.parse_migration(text)
        names = {'STANDARDS.md', 'docs/standards/rust.md'}
        self.assertEqual(context.base_files_at(repo, m, names), {'STANDARDS.md'})


if __name__ == '__main__':
    unittest.main()
