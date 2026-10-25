import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        cases = {
            '../evil.md': 'must not contain a `..` part',
            'a/../b.md': 'must not contain a `..` part',
            '/abs.md': 'must not start with `/`',
            'a\\\\b.md': 'must not contain a backslash',
            'a//b.md': 'must not have an empty path part',
            'a/': 'must not have an empty path part',
            './a.md': 'must not contain a `.` part',
            '': 'a path must be a non-empty string',
        }
        for bad, fragment in cases.items():
            with self.subTest(path=bad):
                text = edit(SAMPLE, 'to = "docs/standards/rust.md"', f'to = "{bad}"')
                refused(self, text, fragment)

    def test_a_schema_that_is_not_the_integer_one_is_refused(self):
        for bad in ('1.0', 'true', '"1"'):
            with self.subTest(schema=bad):
                refused(
                    self,
                    edit(SAMPLE, 'schema = 1', f'schema = {bad}'),
                    '`schema` must be 1',
                )

    def test_a_rewrite_target_without_a_file_entry_is_refused_at_load(self):
        text = edit(
            SAMPLE,
            'path = ".agents/kitchen.toml"\nsha256',
            'path = "other.toml"\nsha256',
        )
        refused(self, text, "target '.agents/kitchen.toml' has no [[file]] entry")

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
            edit(
                text,
                '[[move]]',
                f'[[file]]\npath = "gone.md"\nsha256 = "{"0" * 64}"\n\n[[move]]',
            )
        )
        with self.assertRaises(context.ContextError) as caught:
            context.read_base(repo, m)
        self.assertIn('gone.md: not a file at base', str(caught.exception))

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

    def test_a_directory_at_the_base_is_not_a_file(self):
        repo, text = fixture_repo(self)
        m = context.parse_migration(
            edit(
                text,
                '[[move]]',
                f'[[file]]\npath = ".agents"\nsha256 = "{"0" * 64}"\n\n[[move]]',
            )
        )
        with self.assertRaises(context.ContextError) as caught:
            context.read_base(repo, m)
        self.assertIn('.agents: not a file at base', str(caught.exception))

    def test_an_inherited_git_environment_does_not_redirect_the_reads(self):
        repo, text = fixture_repo(self)
        other = repo.parent / 'other'
        other.mkdir()
        git(other, 'init', '-q')
        env = {
            'GIT_DIR': str(other / '.git'),
            'GIT_WORK_TREE': str(other),
            'GIT_INDEX_FILE': str(other / 'index'),
        }
        with mock.patch.dict(os.environ, env):
            found = context.read_base(repo, context.parse_migration(text))
        self.assertEqual(found['STANDARDS.md'], STANDARDS)

    def test_destinations_at_the_base_are_reported(self):
        repo, text = fixture_repo(self)
        m = context.parse_migration(text)
        names = {'STANDARDS.md', 'docs/standards/rust.md'}
        self.assertEqual(context.base_files_at(repo, m, names), {'STANDARDS.md'})


def sample_migration():
    return context.load(fixtures / 'sample.toml')


def migration_of(*moves, files=('STANDARDS.md',), rewrites=()):
    writes = {m.source for m in moves} | {m.to for m in moves}
    writes |= {r.path for r in rewrites}
    return context.Migration(
        1,
        't',
        'base',
        tuple(sorted(writes)),
        tuple(context.FileEntry(f, '0' * 64) for f in files),
        tuple(moves),
        tuple(rewrites),
        (),
    )


def move(ancestry, to, occurrence=None, requires=(), source='STANDARDS.md'):
    return context.Move(source, tuple(ancestry), occurrence, to, tuple(requires))


TYPED = ('Coding standards', 'Typed evidence')
RUST = (*TYPED, 'Rust (Engine)')
NOTES = ('Coding standards', 'Testing', 'Notes')


def refused_read(test, data, fragment):
    with test.assertRaises(context.ContextError) as caught:
        context.read(data, 'doc.md')
    test.assertIn(fragment, str(caught.exception))


class ReaderTests(unittest.TestCase):
    def test_lines_keep_their_endings(self):
        self.assertEqual(context.lines_of(b'a\nb'), [b'a\n', b'b'])
        self.assertEqual(context.lines_of(b'a\n\nb\n'), [b'a\n', b'\n', b'b\n'])
        self.assertEqual(context.lines_of(b''), [])
        self.assertEqual(b''.join(context.lines_of(STANDARDS)), STANDARDS)

    def test_a_carriage_return_is_refused(self):
        refused_read(self, b'# A\r\n\r\ntext\r\n', 'carriage return')

    def test_an_unclosed_fence_is_refused_and_named(self):
        refused_read(
            self, b'# A\n\n```\ntext\n\n## B\n', 'fence opened at line 3 never closes'
        )

    def test_a_fence_that_closes_is_not_unclosed(self):
        found = context.sections(b'# A\n\n```\n## B\n```\n\n## C\n')
        self.assertEqual([s.raw for s in found], ['A', 'C'])

    def test_a_setext_heading_is_refused(self):
        refused_read(self, b'Title\n=====\n\n## B\n', 'line 1 is a setext heading')
        refused_read(self, b'# A\n\nSub title\n---\n', 'line 3 is a setext heading')

    def test_lines_that_only_look_like_setext_underlines_are_not_refused(self):
        fine = [
            b'# A\n\ntext\n\n---\n\nmore\n',
            b'# A\n\n| a | b |\n|---|---|\n| 1 | 2 |\n',
            b'# A\n\n- item\n---\n',
            b'# A\n\n```\ntext\n---\n```\n',
            b'---\nname: x\n---\n# A\n',
        ]
        for data in fine:
            with self.subTest(data=data):
                context.read(data, 'doc.md')

    def test_a_fenced_lookalike_and_frontmatter_comment_are_not_headings(self):
        found = context.sections(STANDARDS)
        raws = [s.raw for s in found]
        self.assertNotIn('fenced lookalike', raws)
        self.assertNotIn('also not a heading', raws)
        self.assertNotIn('a comment, not a heading', raws)
        self.assertEqual(
            raws[:4],
            ['Coding standards', 'Typed evidence', 'Rust (Engine)', 'Rust notes'],
        )

    def test_a_section_runs_to_the_next_heading_of_the_same_or_higher_level_untrimmed(
        self,
    ):
        text = context.decode(context.lines_of(STANDARDS), 'STANDARDS.md')
        found = {s.path: s for s in context.sections(STANDARDS)}
        typed = found[TYPED]
        self.assertEqual(text[typed.start], '## Typed evidence')
        self.assertEqual(text[typed.end], '## Testing')
        self.assertEqual(text[typed.end - 1], '')
        rust = found[RUST]
        self.assertEqual(text[rust.end], '### Notes')
        self.assertEqual((rust.level, rust.plain), (3, 'Rust (Engine)'))
        top = found[('Coding standards',)]
        self.assertEqual(top.end, len(text))

    def test_a_path_names_every_ancestor(self):
        found = context.sections(b'# A\n\n### C\n\n## B\n\n#### D\n')
        self.assertEqual(
            [s.path for s in found], [('A',), ('A', 'C'), ('A', 'B'), ('A', 'B', 'D')]
        )

    def test_frontmatter_stays_outside_every_section(self):
        found = context.sections(STANDARDS)
        self.assertEqual(found[0].start, 4)

    def test_find_picks_a_unique_ancestry(self):
        found = context.sections(STANDARDS)
        self.assertEqual(context.find(found, RUST, None).raw, 'Rust (Engine)')

    def test_find_refuses_an_ambiguous_ancestry_without_an_occurrence(self):
        found = context.sections(STANDARDS)
        with self.assertRaises(context.ContextError) as caught:
            context.find(found, (*NOTES[:2], 'Examples'), None)
        self.assertIn('2 headings share the ancestry', str(caught.exception))
        self.assertIn('add `occurrence`', str(caught.exception))

    def test_find_takes_an_occurrence_in_document_order(self):
        found = context.sections(STANDARDS)
        ancestry = (*NOTES[:2], 'Examples')
        first = context.find(found, ancestry, 1)
        second = context.find(found, ancestry, 2)
        self.assertLess(first.start, second.start)

    def test_find_refuses_an_occurrence_out_of_range_and_a_missing_heading(self):
        found = context.sections(STANDARDS)
        for ancestry, occurrence, fragment in [
            ((*NOTES[:2], 'Examples'), 3, 'occurrence 3 is out of range'),
            (RUST, 2, 'occurrence 2 is out of range'),
            (('Coding standards', 'Nowhere'), None, 'no heading with ancestry'),
        ]:
            with self.subTest(ancestry=ancestry, occurrence=occurrence):
                with self.assertRaises(context.ContextError) as caught:
                    context.find(found, ancestry, occurrence)
                self.assertIn(fragment, str(caught.exception))


class CutTests(unittest.TestCase):
    def cut(self, *moves, existing=(), files=('STANDARDS.md',), rewrites=()):
        m = migration_of(*moves, files=files, rewrites=rewrites)
        found = {'STANDARDS.md': context.sections(STANDARDS)}
        return context.cuts(m, found, existing)

    def refused(self, fragment, *moves, **kw):
        with self.assertRaises(context.ContextError) as caught:
            self.cut(*moves, **kw)
        self.assertIn(fragment, str(caught.exception))

    def test_a_nested_move_names_its_parent(self):
        cuts = self.cut(move(RUST, 'r.md'), move(TYPED, 't.md'), move(NOTES, 'n.md'))
        self.assertEqual([c.parent for c in cuts], [1, None, None])
        self.assertEqual(
            [c.section.raw for c in cuts], ['Rust (Engine)', 'Typed evidence', 'Notes']
        )

    def test_the_tightest_container_is_the_parent(self):
        deep = (*RUST, 'Rust notes')
        cuts = self.cut(move(deep, 'd.md'), move(RUST, 'r.md'), move(TYPED, 't.md'))
        self.assertEqual([c.parent for c in cuts], [1, 2, None])

    def test_moves_of_one_section_twice_are_refused(self):
        self.refused('is moved twice', move(RUST, 'a.md'), move(RUST, 'b.md'))

    def test_a_destination_named_twice_is_refused(self):
        self.refused('named by two moves', move(RUST, 'a.md'), move(NOTES, 'a.md'))

    def test_a_destination_that_is_a_source_is_refused(self):
        self.refused('is also a move source', move(RUST, 'STANDARDS.md'))

    def test_a_destination_that_exists_at_the_base_is_refused(self):
        self.refused('already exists at base', move(RUST, 'a.md'), existing={'a.md'})

    def test_a_move_whose_source_has_no_file_entry_is_refused(self):
        self.refused('has no [[file]] entry', move(RUST, 'a.md'), files=())

    def test_a_rewrite_target_without_a_file_entry_is_refused(self):
        rewrite = context.Rewrite('other.toml', 'a', 'b')
        self.refused('rewrite target', move(RUST, 'a.md'), rewrites=(rewrite,))

    def test_a_missing_heading_is_refused(self):
        self.refused('no heading with ancestry', move(('Nope',), 'a.md'))

    def test_sections_that_overlap_without_nesting_are_refused(self):
        a = context.Section(2, 'A', 'A', ('A',), 0, 5)
        b = context.Section(2, 'B', 'B', ('B',), 3, 8)
        m = migration_of(move(('A',), 'a.md'), move(('B',), 'b.md'))
        with self.assertRaises(context.ContextError) as caught:
            context.cuts(m, {'STANDARDS.md': [a, b]})
        self.assertIn('overlap without nesting', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
