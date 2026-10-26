import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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
        refused_read(
            self, b'Title\n=====\n\n## B\n', 'line 2 underlines a setext heading'
        )
        refused_read(
            self, b'# A\n\nSub title\n---\n', 'line 4 underlines a setext heading'
        )

    def test_an_indented_setext_heading_is_refused(self):
        refused_read(
            self, b'# A\n\n  Title\n---\n', 'line 4 underlines a setext heading'
        )
        refused_read(
            self, b'# A\n\nTitle\n  ===\n', 'line 4 underlines a setext heading'
        )
        refused_read(
            self, b'# A\n\n   Title\n   ---\n', 'line 4 underlines a setext heading'
        )

    def test_an_atx_heading_indented_one_to_three_spaces_is_refused(self):
        for indent in (' ', '  ', '   '):
            with self.subTest(indent=len(indent)):
                data = f'# A\n\ntext\n{indent}## B\n'.encode()
                refused_read(self, data, 'line 4 is a heading indented 1 to 3 spaces')

    def test_indented_lines_that_are_not_headings_are_not_refused(self):
        fine = [
            b'# A\n\n    ## code block\n',
            b'# A\n\n\t## code block\n',
            b'# A\n\n    Title\n    ---\n',
            b'# A\n\n ##hashtag\n',
            b'# A\n\n```\n  ## fenced\n  Title\n  ---\n```\n',
            b'---\n  ## frontmatter\n---\n# A\n',
        ]
        for data in fine:
            with self.subTest(data=data):
                context.read(data, 'doc.md')

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

    def test_a_missing_heading_is_refused(self):
        self.refused('no heading with ancestry', move(('Nope',), 'a.md'))


class SlugTests(unittest.TestCase):
    def test_plain_renders_inline_markup_to_its_text(self):
        cases = {
            '`kitchen.toml` rules': 'kitchen.toml rules',
            'The [spec](docs/spec.md) and ![img](a.png)': 'The spec and img',
            'A **strong** and *soft* and _under_ word': 'A strong and soft and under word',
            'Use <b>bold</b> tags': 'Use bold tags',
            'Ref [link][1]': 'Ref link',
            'snake_case word': 'snake_case word',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(context.plain(raw), expected)

    def test_slug_follows_githubs_rule(self):
        cases = {
            'Typed evidence': 'typed-evidence',
            'Rust (Engine)': 'rust-engine',
            'Rust Coach: the engine!': 'rust-coach-the-engine',
            '`kitchen.toml` rules': 'kitchentoml-rules',
            'snake_case-name': 'snake_case-name',
            'Ünïcode Ünder': 'ünïcode-ünder',
            'A  B': 'a--b',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(context.slug(raw, {}), expected)

    def test_a_repeat_takes_a_document_scoped_suffix(self):
        seen = {}
        got = [
            context.slug(t, seen)
            for t in ['Notes', 'Other', 'Notes', 'Notes', 'Notes-1']
        ]
        self.assertEqual(got, ['notes', 'other', 'notes-1', 'notes-2', 'notes-1-1'])

    def test_anchors_are_by_heading_line_with_suffixes(self):
        found = context.sections(STANDARDS)
        got = context.anchors(found)
        by_path = {s.path: got[s.start] for s in found}
        self.assertEqual(by_path[NOTES], 'notes-1')
        self.assertEqual(by_path[(*TYPED, 'Notes')], 'notes')


def expected(path):
    return (fixtures / 'expected' / path).read_bytes()


def render_sample(migration=None, present=()):
    return context.render(
        migration or sample_migration(), {'STANDARDS.md': STANDARDS}, present
    )


class RenderTests(unittest.TestCase):
    def test_the_fixture_renders_to_the_expected_bytes(self):
        out = render_sample()
        self.assertEqual(
            set(out),
            {
                'STANDARDS.md',
                'docs/standards/typed-evidence.md',
                'docs/standards/rust.md',
                'docs/standards/testing-notes.md',
            },
        )
        for path, data in out.items():
            with self.subTest(path=path):
                self.assertEqual(data.decode(), expected(path).decode())

    def test_frontmatter_and_unmoved_text_stay_byte_for_byte(self):
        index = render_sample()['STANDARDS.md']
        self.assertTrue(
            index.startswith(STANDARDS[: STANDARDS.index(b'## Typed evidence')])
        )
        for kept in (
            b'Tests are deterministic.\n',
            b'First example.\n',
            b'Keep it short.\n',
        ):
            self.assertIn(kept, index)

    def test_a_destination_opens_at_level_one_and_fenced_lines_are_untouched(self):
        typed = render_sample()['docs/standards/typed-evidence.md']
        self.assertTrue(typed.startswith(b'# Typed evidence\n'))
        self.assertIn(b'\n## Notes\n', typed)
        self.assertIn(
            b'```text\n# fenced lookalike\n## also not a heading\n```\n', typed
        )
        rust = render_sample()['docs/standards/rust.md']
        self.assertTrue(rust.startswith(b'# Rust (Engine)\n'))
        self.assertIn(b'\n## Rust notes\n', rust)

    def test_a_nested_section_leaves_a_stub_in_its_parent(self):
        typed = render_sample()['docs/standards/typed-evidence.md']
        self.assertIn(
            b'## Rust (Engine)\n\nMoved to [docs/standards/rust.md](rust.md#rust-engine).\n\n',
            typed,
        )
        self.assertNotIn(b'Parse at the boundary', typed)
        self.assertNotIn(b'Rust notes', typed)

    def test_every_heading_of_moved_text_stays_in_the_index_with_its_anchor(self):
        index = render_sample()['STANDARDS.md']
        pointers = [
            b'### Rust (Engine)\n\nMoved to [docs/standards/rust.md](docs/standards/rust.md#rust-engine).\n\n',
            b'#### Rust notes\n\nMoved to [docs/standards/rust.md](docs/standards/rust.md#rust-notes).\n\n',
            b'### Notes\n\nMoved to [docs/standards/typed-evidence.md](docs/standards/typed-evidence.md#notes).\n\n',
        ]
        for pointer in pointers:
            self.assertIn(pointer, index)
        old = context.anchors(context.sections(STANDARDS))
        new = context.anchors(context.sections(index))
        self.assertEqual(sorted(old.values()), sorted(new.values()))

    def test_a_duplicate_heading_takes_its_anchor_from_the_destination(self):
        index = render_sample()['STANDARDS.md']
        self.assertIn(
            b'### Notes\n\nMoved to [docs/standards/testing-notes.md](docs/standards/testing-notes.md#notes).\n',
            index,
        )
        self.assertEqual(
            context.anchors(context.sections(STANDARDS))[
                next(s.start for s in context.sections(STANDARDS) if s.path == NOTES)
            ],
            'notes-1',
        )

    def test_an_occurrence_moves_the_second_of_two_equal_paths(self):
        m = migration_of(move((*NOTES[:2], 'Examples'), 'docs/second.md', occurrence=2))
        out = context.render(m, {'STANDARDS.md': STANDARDS})
        self.assertEqual(
            out['docs/second.md'],
            b'# Examples\n\nSecond example.\n\n',
        )
        self.assertIn(b'First example.\n', out['STANDARDS.md'])
        self.assertNotIn(b'Second example.', out['STANDARDS.md'])

    def test_requires_render_relative_to_the_destination(self):
        m = migration_of(
            move(TYPED, 'a/b/typed.md'),
            move(NOTES, 'docs/notes.md', requires=['a/b/typed.md', 'README.md']),
        )
        out = context.render(m, {'STANDARDS.md': STANDARDS}, present={'README.md'})
        self.assertTrue(
            out['docs/notes.md'].startswith(
                b'# Notes\n\nRead first: [a/b/typed.md](../a/b/typed.md), [README.md](../README.md).\n\nNotes on testing.\n'
            )
        )

    def test_a_requires_cycle_is_refused(self):
        m = migration_of(
            move(TYPED, 'a.md', requires=['b.md']),
            move(NOTES, 'b.md', requires=['a.md']),
        )
        with self.assertRaises(context.ContextError) as caught:
            context.render(m, {'STANDARDS.md': STANDARDS})
        self.assertIn(
            'requires form a cycle: a.md -> b.md -> a.md', str(caught.exception)
        )

    def test_a_requires_of_itself_is_refused(self):
        m = migration_of(move(TYPED, 'a.md', requires=['a.md']))
        with self.assertRaises(context.ContextError) as caught:
            context.render(m, {'STANDARDS.md': STANDARDS})
        self.assertIn('cycle', str(caught.exception))

    def test_a_requires_that_is_not_a_destination_or_a_base_file_is_refused(self):
        m = migration_of(move(TYPED, 'a.md', requires=['nowhere.md']))
        with self.assertRaises(context.ContextError) as caught:
            context.render(m, {'STANDARDS.md': STANDARDS})
        self.assertIn("requires 'nowhere.md'", str(caught.exception))

    def test_a_destination_that_exists_at_the_base_is_refused_by_render(self):
        m = migration_of(move(RUST, 'docs/there.md'))
        with self.assertRaises(context.ContextError) as caught:
            context.render(m, {'STANDARDS.md': STANDARDS}, present={'docs/there.md'})
        self.assertIn('already exists at base', str(caught.exception))

    def test_a_final_line_without_a_newline_keeps_that_state_in_the_destination(self):
        data = b'# Top\n\n## Last\ntext'
        m = migration_of(move(('Top', 'Last'), 'last.md'))
        out = context.render(m, {'STANDARDS.md': data})
        self.assertEqual(out['last.md'], b'# Last\ntext')
        self.assertEqual(
            out['STANDARDS.md'],
            b'# Top\n\n## Last\n\nMoved to [last.md](last.md#last).\n',
        )

    def test_rendering_reads_the_given_bytes_only(self):
        again = render_sample()
        self.assertEqual(render_sample(), again)


KITCHEN = scripts / 'kitchen.py'


def preview(repo, migration_text, *extra, cwd=None):
    migration = Path(repo).parent / 'migration.toml'
    migration.write_text(migration_text)
    return subprocess.run(
        [sys.executable, str(KITCHEN), 'context', 'preview', str(migration), *extra],
        cwd=cwd or repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class PreviewTests(unittest.TestCase):
    def test_it_prints_a_diff_for_every_planned_write_and_writes_nothing(self):
        repo, text = fixture_repo(self)
        done = preview(repo, text)
        self.assertEqual(done.returncode, 0, done.stderr)
        for header in (
            '--- a/STANDARDS.md\n+++ b/STANDARDS.md\n',
            '--- /dev/null\n+++ b/docs/standards/typed-evidence.md\n',
            '--- /dev/null\n+++ b/docs/standards/rust.md\n',
            '--- /dev/null\n+++ b/docs/standards/testing-notes.md\n',
        ):
            self.assertIn(header, done.stdout)
        self.assertIn('unchanged: .agents/kitchen.toml', done.stdout)
        self.assertIn(
            '+Read first: [docs/standards/typed-evidence.md](typed-evidence.md).',
            done.stdout,
        )
        self.assertIn('-Parse at the boundary.', done.stdout)
        self.assertEqual(git(repo, 'status', '--porcelain'), '')

    def test_the_diff_lists_the_writes_in_the_order_of_writes(self):
        repo, text = fixture_repo(self)
        done = preview(repo, text, '--json')
        self.assertEqual(done.returncode, 0, done.stderr)
        writes = json.loads(done.stdout)['writes']
        m = context.parse_migration(text)
        self.assertEqual([w['path'] for w in writes], list(m.writes))

    def test_json_gives_the_hashes_and_the_diff(self):
        repo, text = fixture_repo(self)
        writes = {
            w['path']: w
            for w in json.loads(preview(repo, text, '--json').stdout)['writes']
        }
        index = writes['STANDARDS.md']
        self.assertEqual(index['before_sha256'], sha(STANDARDS))
        self.assertEqual(index['after_sha256'], sha(expected('STANDARDS.md')))
        self.assertEqual(index['diff'].splitlines()[0], '--- a/STANDARDS.md')
        new = writes['docs/standards/rust.md']
        self.assertIsNone(new['before_sha256'])
        self.assertEqual(new['after_sha256'], sha(expected('docs/standards/rust.md')))
        same = writes['.agents/kitchen.toml']
        self.assertEqual(
            (same['before_sha256'], same['diff']), (same['after_sha256'], '')
        )

    def test_it_reads_the_base_commit_not_the_working_tree(self):
        repo, text = fixture_repo(self)
        first = preview(repo, text).stdout
        (repo / 'STANDARDS.md').write_text('# edited after the base\n')
        (repo / 'docs').mkdir()
        self.assertEqual(preview(repo, text).stdout, first)
        self.assertEqual(
            sorted(p.name for p in repo.iterdir() if p.name != '.git'),
            ['.agents', 'STANDARDS.md', 'docs'],
        )
        self.assertEqual(list((repo / 'docs').iterdir()), [])

    def test_repo_can_follow_the_subcommand(self):
        repo, text = fixture_repo(self)
        done = preview(repo, text, '--repo', str(repo), cwd=repo.parent)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('+++ b/docs/standards/rust.md', done.stdout)

    def test_an_invalid_migration_exits_two_with_its_message(self):
        repo, text = fixture_repo(self)
        done = preview(repo, edit(text, 'schema = 1', 'schema = 2'))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, '')
        self.assertIn('kitchen: `schema` must be 1', done.stderr)

    def test_a_stale_sha256_exits_two_and_names_the_path(self):
        repo, text = fixture_repo(self)
        done = preview(repo, text.replace(sha(STANDARDS), '1' * 64))
        self.assertEqual(done.returncode, 2)
        self.assertIn('STANDARDS.md: sha256', done.stderr)

    def test_a_destination_that_exists_at_the_base_exits_two(self):
        repo, text = fixture_repo(self)
        (repo / 'docs/standards').mkdir(parents=True)
        (repo / 'docs/standards/rust.md').write_text('# mine\n')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'add rust.md')
        base = git(repo, 'rev-parse', 'HEAD')
        old = git(repo, 'rev-parse', 'HEAD~1')
        done = preview(repo, text.replace(old, base))
        self.assertEqual(done.returncode, 2)
        self.assertIn(
            "destination 'docs/standards/rust.md' already exists at base", done.stderr
        )


GUIDE = 'docs/guide.md'
REF_FILES = {
    GUIDE,
    'docs/other.md',
    'docs/my file.md',
    'docs/a(1).md',
    'img/a.png',
    'ignored.md',
}


def guide_refs():
    data = (fixtures / 'refs' / GUIDE).read_bytes()
    return context.references(GUIDE, data)


class ReferenceTests(unittest.TestCase):
    def test_the_guide_fixture_gives_every_recognized_form_and_nothing_else(self):
        got = [(r.line, r.kind, r.target, r.fragment) for r in guide_refs()]
        self.assertEqual(
            got,
            [
                (7, 'inline', 'other.md', ''),
                (7, 'inline', 'other.md', 'part'),
                (7, 'inline', '', 'top'),
                (8, 'image', '../img/a.png', ''),
                (8, 'inline', 'my file.md', ''),
                (8, 'inline', 'a(1).md', 'x'),
                (11, 'definition', 'other.md', 'part'),
                (12, 'definition', 'my file.md', ''),
                (18, 'inline', 'other.md', 'live'),
                (25, 'image', '../img/a.png', ''),
                (25, 'inline', 'other.md', 'nest'),
                (28, 'inline', 'other.md', 'wrapped'),
                (30, 'image', '../img/a.png', ''),
                (31, 'inline', 'other.md', 'outer'),
            ],
        )

    def test_link_text_wrapped_over_lines_reports_the_target_line_and_the_whole_raw(
        self,
    ):
        got = {
            r.fragment: r for r in guide_refs() if r.fragment in ('wrapped', 'outer')
        }
        self.assertEqual(
            got['wrapped'].raw, '[text wraps over\ntwo lines](other.md#wrapped)'
        )
        self.assertEqual(
            got['outer'].raw,
            '[with an ![inner\nimage](../img/a.png) inside\nthe text](other.md#outer)',
        )
        [inner] = [r for r in guide_refs() if r.kind == 'image' and r.line == 30]
        self.assertEqual(inner.raw, '![inner\nimage](../img/a.png)')
        lines = (fixtures / 'refs' / GUIDE).read_text().split('\n')
        for r in (got['wrapped'], got['outer'], inner):
            written = lines[r.line - 1][r.start : r.end]
            self.assertEqual(
                written, r.target + ('#' + r.fragment if r.fragment else '')
            )

    def test_a_close_with_no_opener_in_its_paragraph_is_not_a_link(self):
        raws = ' '.join(r.raw for r in guide_refs())
        self.assertNotIn('orphan', raws)
        self.assertNotIn('#gap', raws)

    def test_a_reference_carries_its_raw_text_and_the_columns_of_its_target(self):
        lines = (fixtures / 'refs' / GUIDE).read_text().split('\n')
        for r in guide_refs():
            with self.subTest(line=r.line, raw=r.raw):
                text = lines[r.line - 1]
                held = r.raw.count('\n')
                self.assertIn(r.raw, '\n'.join(lines[r.line - 1 - held : r.line]))
                written = text[r.start : r.end]
                self.assertEqual(
                    written, r.target + ('#' + r.fragment if r.fragment else '')
                )

    def test_a_title_and_angle_brackets_are_not_part_of_the_target(self):
        got = {r.raw: r.target for r in guide_refs()}
        self.assertEqual(got['![alt](../img/a.png "title")'], '../img/a.png')
        self.assertEqual(got['[ref2]: <my file.md> "Title"'], 'my file.md')

    def test_urls_footnotes_code_spans_fences_frontmatter_and_escapes_are_skipped(self):
        raws = ' '.join(r.raw for r in guide_refs())
        for skipped in (
            'example.com',
            'mailto',
            'cdn/x.md',
            '[^1]',
            '[code](other.md)',
            '[code2]',
            'fenced',
            'ref3',
            'ignored.md',
            '[not](other.md)',
            '[empty]()',
        ):
            self.assertNotIn(skipped, raws)

    def test_a_fence_that_never_closes_is_ordinary_text(self):
        got = context.references('a.md', b'```\n[x](y.md)\n')
        self.assertEqual([r.target for r in got], ['y.md'])

    def test_toml_and_text_give_exact_tracked_path_strings(self):
        files = {'docs/guide.md', 'STANDARDS.md'}
        toml = (
            b'[review]\nstandards = ["STANDARDS.md#typed-evidence", "xSTANDARDS.md", '
            b'"docs/guide.md"]\n# see STANDARDS.md.\nmissing = "docs/none.md#a"\n'
        )
        got = context.references('.agents/kitchen.toml', toml, files)
        self.assertEqual(
            [(r.line, r.kind, r.target, r.fragment, r.raw) for r in got],
            [
                (
                    2,
                    'text',
                    'STANDARDS.md',
                    'typed-evidence',
                    'STANDARDS.md#typed-evidence',
                ),
                (2, 'text', 'docs/guide.md', '', 'docs/guide.md'),
                (3, 'text', 'STANDARDS.md', '', 'STANDARDS.md'),
            ],
        )
        notes = context.references(
            'notes.txt', b'read docs/guide.md#top first\n', files
        )
        self.assertEqual([r.raw for r in notes], ['docs/guide.md#top'])
        self.assertEqual(context.references('notes.txt', b'docs/guide.md\n'), [])

    def test_bytes_that_are_not_utf8_make_no_references(self):
        self.assertEqual(context.references('a.md', b'[x](y.md) \xff'), [])

    def test_markdown_targets_resolve_from_the_referring_files_directory(self):
        got = {
            (r.line, r.raw[:12]): context.resolve(r, REF_FILES) for r in guide_refs()
        }
        self.assertEqual(got[(7, '[text](other')], ('docs/other.md', ''))
        self.assertEqual(got[(7, '[with fragme')], ('docs/other.md', 'part'))
        self.assertEqual(got[(7, '[bare](#top)')], (GUIDE, 'top'))
        self.assertEqual(got[(8, '![alt](../im')], ('img/a.png', ''))
        self.assertEqual(got[(8, '[a b](<my fi')], ('docs/my file.md', ''))
        self.assertEqual(got[(8, '[parens](a(1')], ('docs/a(1).md', 'x'))

    def test_toml_and_text_targets_resolve_from_the_repo_root(self):
        files = {'docs/guide.md'}
        [ref] = context.references(
            '.agents/kitchen.toml', b'a = "docs/guide.md#Top"\n', files
        )
        self.assertEqual(context.resolve(ref, files), ('docs/guide.md', 'top'))

    def test_a_root_relative_link_percent_escapes_and_queries_resolve(self):
        def ref(target, fragment=''):
            return context.Ref('docs/guide.md', 1, 0, 0, 'inline', target, fragment, '')

        files = {'docs/other.md', 'docs/my file.md', 'top.md'}
        self.assertEqual(context.resolve(ref('/top.md'), files), ('top.md', ''))
        self.assertEqual(
            context.resolve(ref('my%20file.md'), files), ('docs/my file.md', '')
        )
        self.assertEqual(
            context.resolve(ref('other.md?plain=1', 'A%20b'), files),
            ('docs/other.md', 'a b'),
        )

    def test_a_target_that_names_no_tracked_file_or_leaves_the_repo_resolves_to_none(
        self,
    ):
        def ref(target):
            return context.Ref('docs/guide.md', 1, 0, 0, 'inline', target, '', '')

        files = {'docs/other.md', 'x.md'}
        for target in ('missing.md', '../../x.md', '../..', 'other.md/', '.'):
            with self.subTest(target=target):
                self.assertIsNone(context.resolve(ref(target), files))
        self.assertEqual(context.resolve(ref('../x.md'), files), ('x.md', ''))


if __name__ == '__main__':
    unittest.main()
