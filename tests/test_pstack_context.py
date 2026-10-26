import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import os
import re
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


FILES = {
    'STANDARDS.md',
    'docs/guide.md',
    'docs/adr/0007-standards.md',
    '.agents/kitchen.toml',
}


KITCHEN_TOML = (fixtures / 'repo/.agents/kitchen.toml').read_bytes()
BASE = {'STANDARDS.md': STANDARDS, '.agents/kitchen.toml': KITCHEN_TOML}


def render_sample(migration=None, present=()):
    return context.render(migration or sample_migration(), BASE, present, FILES)


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
                '.agents/kitchen.toml',
            },
        )
        for path, data in out.items():
            with self.subTest(path=path):
                self.assertEqual(data.decode(), expected(path).decode())

    def test_the_anchor_map_follows_each_moved_heading_to_its_new_home(self):
        m = sample_migration()
        got = context.anchor_map(m, BASE, render_sample(m))
        typed, rust = 'docs/standards/typed-evidence.md', 'docs/standards/rust.md'
        self.assertEqual(
            got,
            {
                ('STANDARDS.md', 'typed-evidence'): (typed, 'typed-evidence'),
                ('STANDARDS.md', 'rust-engine'): (rust, 'rust-engine'),
                ('STANDARDS.md', 'rust-notes'): (rust, 'rust-notes'),
                ('STANDARDS.md', 'notes'): (typed, 'notes'),
                # the suffix is document-scoped: the second Notes of the source is the only one in its destination
                ('STANDARDS.md', 'notes-1'): (
                    'docs/standards/testing-notes.md',
                    'notes',
                ),
            },
        )

    def test_the_anchor_map_leaves_unmoved_headings_out(self):
        m = sample_migration()
        got = context.anchor_map(m, BASE, render_sample(m))
        for anchor in (
            'coding-standards',
            'testing',
            'examples',
            'examples-1',
            'style',
        ):
            self.assertNotIn(('STANDARDS.md', anchor), got)

    def test_frontmatter_and_unmoved_text_stay_byte_for_byte(self):
        index = render_sample()['STANDARDS.md']
        self.assertTrue(
            index.startswith(STANDARDS[: STANDARDS.index(b'## Typed evidence')])
        )
        for kept in (
            b'Tests are deterministic.\n',
            b'First example.\n',
            b'Keep it short. The [shared note][shared] applies.\n',
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


class RebaseTests(unittest.TestCase):
    def test_a_link_to_a_file_outside_the_move_is_relative_to_the_destination(self):
        typed = render_sample()['docs/standards/typed-evidence.md']
        self.assertIn(b'[guide](../guide.md#moved)', typed)

    def test_a_fragment_to_a_staying_section_names_the_source_from_the_destination(
        self,
    ):
        typed = render_sample()['docs/standards/typed-evidence.md']
        self.assertIn(b'[style rules](../../STANDARDS.md#style)', typed)

    def test_a_fragment_between_two_moved_sections_goes_to_the_new_anchor(self):
        rust = render_sample()['docs/standards/rust.md']
        self.assertIn(b'[Notes](typed-evidence.md#notes)', rust)
        self.assertIn(b'[typed evidence](typed-evidence.md#typed-evidence)', rust)

    def test_a_fragment_within_one_destination_stays_a_bare_fragment(self):
        typed = render_sample()['docs/standards/typed-evidence.md']
        self.assertIn(b'Back to [the rule](#typed-evidence).', typed)

    def test_a_suffixed_anchor_goes_through_the_map_not_the_text(self):
        data = b'# Top\n\n## Notes\n\nfirst\n\n## Notes\n\nsee [the first](#notes) and [this](#notes-1)\n'
        m = migration_of(move(('Top', 'Notes'), 'sub/b.md', occurrence=2))
        out = context.render(m, {'STANDARDS.md': data})
        self.assertIn(
            b'see [the first](../STANDARDS.md#notes) and [this](#notes)',
            out['sub/b.md'],
        )

    def test_a_definition_used_only_by_moved_text_moves_with_it(self):
        out = render_sample()
        self.assertIn(b'[moved-only]: ../guide.md\n', out['docs/standards/rust.md'])
        self.assertNotIn(b'[moved-only]', out['STANDARDS.md'])

    def test_a_definition_used_by_moved_and_staying_text_stays_and_is_copied(self):
        out = render_sample()
        self.assertIn(b'[shared]: docs/guide.md#moved "Shared"\n', out['STANDARDS.md'])
        for dest in ('rust', 'typed-evidence'):
            self.assertIn(
                b'[shared]: ../guide.md#moved "Shared"\n',
                out[f'docs/standards/{dest}.md'],
            )

    def test_a_definition_used_by_nothing_stays(self):
        out = render_sample()
        self.assertIn(b'[unused]: docs/guide.md\n', out['STANDARDS.md'])
        for dest in ('rust', 'typed-evidence', 'testing-notes'):
            self.assertNotIn(b'[unused]', out[f'docs/standards/{dest}.md'])

    def test_a_definition_inside_moved_text_used_by_staying_text_is_refused(self):
        data = b'# Top\n\n## Moved\n\n[x]: a.md\n\n## Stays\n\nsee [x]\n'
        m = migration_of(move(('Top', 'Moved'), 'b.md'))
        with self.assertRaises(context.ContextError) as caught:
            context.render(m, {'STANDARDS.md': data}, (), {'a.md'})
        self.assertIn('STANDARDS.md:5', str(caught.exception))
        self.assertIn('[x]', str(caught.exception))

    def test_a_reference_that_does_not_resolve_is_left_byte_for_byte(self):
        data = (
            b'# Top\n\n## Moved\n\n[gone](nope.md) [nowhere](#missing) '
            b'[away](https://example.com/a) [up](../../out.md) [dir](docs/)\n'
        )
        m = migration_of(move(('Top', 'Moved'), 'sub/b.md'))
        out = context.render(m, {'STANDARDS.md': data}, (), {'docs/x.md'})
        self.assertEqual(
            out['sub/b.md'].split(b'\n\n', 1)[1], data.split(b'\n\n', 2)[2]
        )

    def test_a_link_whose_text_wraps_is_rebased_on_the_target_line(self):
        data = b'# Top\n\n## Moved\n\nsee [the\nguide](docs/guide.md)\n'
        m = migration_of(move(('Top', 'Moved'), 'sub/b.md'))
        out = context.render(m, {'STANDARDS.md': data}, (), {'docs/guide.md'})
        self.assertIn(b'see [the\nguide](../docs/guide.md)\n', out['sub/b.md'])

    def test_a_reference_in_a_moved_heading_is_rebased_for_its_destination(self):
        data = b'# Top\n\n## See [g](docs/guide.md)\n\ntext\n'
        m = migration_of(move(('Top', 'See [g](docs/guide.md)'), 'sub/b.md'))
        out = context.render(m, {'STANDARDS.md': data}, (), {'docs/guide.md'})
        self.assertTrue(out['sub/b.md'].startswith(b'# See [g](../docs/guide.md)\n'))
        self.assertIn(b'## See [g](docs/guide.md)\n', out['STANDARDS.md'])

    def test_only_the_target_bytes_change(self):
        out = render_sample()['docs/standards/rust.md']
        self.assertIn(b'[only here][moved-only]', out)

    def test_plan_resolves_against_the_files_at_the_base(self):
        repo, text = fixture_repo(self)
        writes = context.plan(repo, context.parse_migration(text))
        typed = next(w for w in writes if w.path == 'docs/standards/typed-evidence.md')
        self.assertIn(b'[guide](../guide.md#moved)', typed.after)


class RewriteTests(unittest.TestCase):
    def rewrite_sample(self, **changes):
        m = sample_migration()
        r = m.rewrites[0]
        fields = {
            'path': r.path,
            'from_': r.from_,
            'to': r.to,
            'count': r.count,
        } | changes
        return dataclasses.replace(m, rewrites=(context.Rewrite(**fields),))

    def refused(self, m, base, fragment):
        with self.assertRaises(context.ContextError) as caught:
            context.render(m, base, (), FILES)
        self.assertIn(fragment, str(caught.exception))

    def test_a_rewrite_replaces_exactly_its_from_text_in_the_base_bytes(self):
        out = render_sample()
        self.assertEqual(
            out['.agents/kitchen.toml'].decode(),
            expected('.agents/kitchen.toml').decode(),
        )
        self.assertEqual(
            out['.agents/kitchen.toml'],
            KITCHEN_TOML.replace(
                b'"STANDARDS.md#typed-evidence"',
                b'"docs/standards/typed-evidence.md#typed-evidence"',
            ),
        )

    def test_a_from_that_occurs_twice_is_refused_with_the_file_and_the_count(self):
        twice = KITCHEN_TOML + b'again = "STANDARDS.md#typed-evidence"\n'
        self.refused(
            sample_migration(),
            BASE | {'.agents/kitchen.toml': twice},
            ".agents/kitchen.toml: `from` 'STANDARDS.md#typed-evidence' occurs 2 times",
        )

    def test_a_count_of_two_rewrites_both_occurrences(self):
        twice = KITCHEN_TOML + b'again = "STANDARDS.md#typed-evidence"\n'
        out = context.render(
            self.rewrite_sample(count=2),
            BASE | {'.agents/kitchen.toml': twice},
            (),
            FILES,
        )
        self.assertEqual(
            out['.agents/kitchen.toml'].count(
                b'"docs/standards/typed-evidence.md#typed-evidence"'
            ),
            2,
        )
        self.assertNotIn(b'"STANDARDS.md#typed-evidence"', out['.agents/kitchen.toml'])

    def test_a_count_of_two_with_one_occurrence_is_refused_with_the_count(self):
        self.refused(
            self.rewrite_sample(count=2),
            BASE,
            'occurs 1 times; it must occur exactly 2',
        )

    def test_a_count_below_one_is_refused_at_load(self):
        for bad in ('0', '-1', 'true', '"2"'):
            with self.subTest(count=bad):
                refused(
                    self,
                    edit(
                        SAMPLE,
                        'to = "docs/standards/typed-evidence.md#typed-evidence"',
                        f'to = "docs/standards/typed-evidence.md#typed-evidence"\ncount = {bad}',
                    ),
                    '[[rewrite]] 1: `count` must be a whole number from 1',
                )

    def test_a_count_is_read_from_a_rewrite(self):
        text = edit(
            SAMPLE,
            'to = "docs/standards/typed-evidence.md#typed-evidence"',
            'to = "docs/standards/typed-evidence.md#typed-evidence"\ncount = 2',
        )
        self.assertEqual(context.parse_migration(text).rewrites[0].count, 2)

    def test_a_from_that_never_occurs_is_refused_with_the_file_and_the_count(self):
        self.refused(
            self.rewrite_sample(from_='STANDARDS.md#nowhere'),
            BASE,
            ".agents/kitchen.toml: `from` 'STANDARDS.md#nowhere' occurs 0 times",
        )

    def test_a_newline_in_a_from_or_a_to_is_refused_at_load(self):
        for key, old in (
            ('from', 'from = "STANDARDS.md#typed-evidence"'),
            ('to', 'to = "docs/standards/typed-evidence.md#typed-evidence"'),
        ):
            with self.subTest(key):
                refused(
                    self,
                    edit(SAMPLE, old, old[:-1] + '\\nx"'),
                    f'[[rewrite]] 1: `{key}` must not contain a newline',
                )

    def test_the_spans_of_a_from_that_overlaps_itself_do_not_overlap(self):
        m = migration_of(
            move(RUST, 'docs/rust.md'),
            rewrites=(context.Rewrite('STANDARDS.md', 'aa', 'b', 2),),
        )
        self.assertEqual(
            context.rewrite_spans(m, 'STANDARDS.md', 'aaaa'), [(0, 2), (2, 4)]
        )

    def test_a_to_that_resolves_to_nothing_is_refused_naming_it(self):
        self.refused(
            self.rewrite_sample(to='docs/standards/missing.md#x'),
            BASE,
            "rewrite `to` 'docs/standards/missing.md#x' resolves to nothing",
        )

    def test_a_to_naming_a_heading_the_destination_lacks_is_refused(self):
        self.refused(
            self.rewrite_sample(to='docs/standards/rust.md#nowhere'),
            BASE,
            'names no heading of docs/standards/rust.md',
        )

    def test_a_to_may_name_a_file_of_the_repo(self):
        out = context.render(
            self.rewrite_sample(to='docs/guide.md#moved'), BASE, (), FILES
        )
        self.assertIn(b'"docs/guide.md#moved"', out['.agents/kitchen.toml'])

    def test_a_rewrite_target_that_is_also_moved_is_refused(self):
        m = self.rewrite_sample(path='STANDARDS.md')
        self.refused(m, BASE, 'also be a move source or destination')

    def test_a_requires_naming_a_migration_source_is_accepted(self):
        m = migration_of(move(RUST, 'docs/rust.md', requires=['STANDARDS.md']))
        out = context.render(m, {'STANDARDS.md': STANDARDS})
        self.assertIn(
            b'Read first: [STANDARDS.md](../STANDARDS.md).', out['docs/rust.md']
        )


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
            '--- a/.agents/kitchen.toml\n+++ b/.agents/kitchen.toml\n',
        ):
            self.assertIn(header, done.stdout)
        self.assertNotIn('unchanged:', done.stdout)
        self.assertIn(
            '+standards = ["docs/standards/typed-evidence.md#typed-evidence"]',
            done.stdout,
        )
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
        rewritten = writes['.agents/kitchen.toml']
        self.assertEqual(rewritten['before_sha256'], sha(KITCHEN_TOML))
        self.assertEqual(
            rewritten['after_sha256'], sha(expected('.agents/kitchen.toml'))
        )

    def test_it_reads_the_base_commit_not_the_working_tree(self):
        repo, text = fixture_repo(self)
        first = preview(repo, text).stdout
        (repo / 'STANDARDS.md').write_text('# edited after the base\n')
        (repo / 'docs').mkdir(exist_ok=True)
        self.assertEqual(preview(repo, text).stdout, first)
        self.assertEqual(
            sorted(p.name for p in repo.iterdir() if p.name != '.git'),
            ['.agents', 'STANDARDS.md', 'docs'],
        )
        self.assertEqual(
            sorted(p.name for p in (repo / 'docs').iterdir()), ['adr', 'guide.md']
        )

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


def without(text, *tables):
    """The migration text without its `[[rewrite]]` and/or `[[keep]]` tables (and the kitchen.toml they target)."""
    head, *blocks = text.split('\n[[')
    kept = []
    for block in blocks:
        name = block.split(']]', 1)[0]
        if name in tables:
            continue
        if name == 'file' and 'rewrite' in tables and '.agents/kitchen.toml' in block:
            continue
        kept.append(block)
    if 'rewrite' in tables:
        head = head.replace('  ".agents/kitchen.toml",\n', '')
    return '\n[['.join([head, *kept])


KITCHEN_LINE = 'unresolved: .agents/kitchen.toml:2: STANDARDS.md#typed-evidence\n'
ADR_LINE = (
    'unresolved: docs/adr/0007-standards.md:3: '
    '[typed evidence](../../STANDARDS.md#typed-evidence)\n'
)


def unresolved_lines(stdout):
    return ''.join(x + '\n' for x in stdout.splitlines() if x.startswith('unresolved:'))


class UnresolvedTests(unittest.TestCase):
    def test_a_migration_without_its_rewrite_and_keep_lists_both_and_exits_one(self):
        repo, text = fixture_repo(self)
        done = preview(repo, without(text, 'rewrite', 'keep'))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(unresolved_lines(done.stdout), KITCHEN_LINE + ADR_LINE)
        self.assertTrue(done.stdout.endswith(ADR_LINE))
        self.assertIn('+++ b/docs/standards/rust.md', done.stdout)
        self.assertEqual(git(repo, 'status', '--porcelain'), '')

    def test_the_rewrite_covers_its_reference_and_the_keep_covers_the_other(self):
        repo, text = fixture_repo(self)
        keep_only = preview(repo, without(text, 'rewrite'))
        self.assertEqual(keep_only.returncode, 1, keep_only.stderr)
        self.assertEqual(unresolved_lines(keep_only.stdout), KITCHEN_LINE)
        rewrite_only = preview(repo, without(text, 'keep'))
        self.assertEqual(rewrite_only.returncode, 1, rewrite_only.stderr)
        self.assertEqual(unresolved_lines(rewrite_only.stdout), ADR_LINE)

    def test_a_migration_with_both_exits_zero_and_lists_nothing(self):
        repo, text = fixture_repo(self)
        done = preview(repo, text)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(unresolved_lines(done.stdout), '')

    def test_json_adds_the_unresolved_references_with_their_resolved_target(self):
        repo, text = fixture_repo(self)
        done = preview(repo, without(text, 'rewrite', 'keep'), '--json')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(
            json.loads(done.stdout)['unresolved'],
            [
                {
                    'path': '.agents/kitchen.toml',
                    'line': 2,
                    'raw': 'STANDARDS.md#typed-evidence',
                    'target': 'STANDARDS.md#typed-evidence',
                },
                {
                    'path': 'docs/adr/0007-standards.md',
                    'line': 3,
                    'raw': '[typed evidence](../../STANDARDS.md#typed-evidence)',
                    'target': 'STANDARDS.md#typed-evidence',
                },
            ],
        )
        covered = json.loads(preview(repo, text, '--json').stdout)
        self.assertEqual(covered['unresolved'], [])

    def test_a_reference_inside_the_moved_text_is_not_unresolved(self):
        repo, text = fixture_repo(self)
        done = preview(repo, without(text, 'rewrite', 'keep'))
        self.assertNotIn('unresolved: STANDARDS.md', done.stdout)

    def test_a_staying_reference_to_a_moved_section_is_unresolved_in_its_own_file(self):
        staying = STANDARDS.replace(
            b'Keep it short.', b'Keep it short, as [typed](#typed-evidence) says.'
        )
        m = migration_of(move(TYPED, 'docs/t.md'))
        got = context.unresolved(m, {'STANDARDS.md': staying}, {'STANDARDS.md'})
        self.assertEqual(
            [(r.path, r.line, r.raw) for r in got],
            [('STANDARDS.md', 48, '[typed](#typed-evidence)')],
        )

    def test_files_are_read_through_git_at_the_base_not_the_working_tree(self):
        repo, text = fixture_repo(self)
        (repo / 'docs/adr/0007-standards.md').write_text('# ADR\n')
        (repo / 'docs/new.md').write_text('[t](../STANDARDS.md#typed-evidence)\n')
        m = context.parse_migration(without(text, 'rewrite', 'keep'))
        got = context.plan_unresolved(repo, m)
        self.assertEqual(
            [(r.path, r.line) for r in got],
            [('.agents/kitchen.toml', 2), ('docs/adr/0007-standards.md', 3)],
        )


def inventory(repo, *extra):
    return subprocess.run(
        [sys.executable, str(KITCHEN), 'context', 'inventory', *extra],
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def pinned(name, commit):
    return (
        (fixtures / name)
        .read_text()
        .replace('COMMIT12', commit[:12])
        .replace('COMMIT', commit)
    )


PAD = 'word ' * 2100


def doc(*headings, pad=PAD):
    return ''.join(f'## {h}\n\n{pad}\n\n' for h in headings).encode()


def inventory_of(files):
    return context.inventory_of('c' * 40, {p: d for p, d in files.items()})


def documents(files):
    return {d.path: d for d in inventory_of(files).documents}


def candidate_files(**over):
    files = {
        'big.md': doc('One', 'Two', 'Three'),
        'a.md': b'[one](big.md#one)\n',
        'b.md': b'[two](big.md#two)\n',
    }
    return files | over


class InventoryTests(unittest.TestCase):
    def test_the_fixture_repo_text_is_pinned(self):
        repo, _ = fixture_repo(self)
        done = inventory(repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, pinned('inventory.txt', git(repo, 'rev-parse', 'HEAD'))
        )

    def test_the_fixture_repo_json_is_pinned(self):
        repo, _ = fixture_repo(self)
        done = inventory(repo, '--json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, pinned('inventory.json', git(repo, 'rev-parse', 'HEAD'))
        )

    def test_inventory_and_draft_write_nothing(self):
        repo, _ = fixture_repo(self)
        for extra in ((), ('--json',), ('--draft', 'STANDARDS.md')):
            with self.subTest(extra=extra):
                self.assertEqual(inventory(repo, *extra).returncode, 0)
                self.assertEqual(git(repo, 'status', '--porcelain'), '')
        self.assertEqual(
            sorted(p.name for p in repo.iterdir() if p.name != '.git'),
            ['.agents', 'STANDARDS.md', 'docs'],
        )

    def test_it_reads_head_not_the_working_tree(self):
        repo, _ = fixture_repo(self)
        first = inventory(repo).stdout
        (repo / 'STANDARDS.md').write_text('# edited\n')
        (repo / 'docs/untracked.md').write_text('[x](../STANDARDS.md#a)\n')
        self.assertEqual(inventory(repo).stdout, first)

    def test_repo_can_follow_the_subcommand(self):
        repo, _ = fixture_repo(self)
        done = inventory(repo.parent, '--repo', str(repo))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('STANDARDS.md: 873 bytes', done.stdout)

    def test_a_directory_without_commits_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, 'init', '-q')
            done = inventory(tmp)
        self.assertEqual(done.returncode, 2)
        self.assertIn('has no commit at HEAD', done.stderr)

    def test_the_top_level_is_the_shallowest_heading_level_present(self):
        got = documents({'x.md': b'### Deep\n\ntext\n\n## Mid\n\n## Mid2\n\nmore\n'})
        x = got['x.md']
        self.assertEqual(x.level, 2)
        self.assertEqual([h.raw for h in x.sections], ['Mid', 'Mid2'])
        self.assertEqual(x.sections[0].tokens, len('## Mid') // 4)
        self.assertEqual(
            x.tokens, len('### Deep\n\ntext\n\n## Mid\n\n## Mid2\n\nmore\n') // 4
        )

    def test_a_lone_title_hands_the_top_level_to_the_headings_below_it(self):
        got = documents({'x.md': b'# Title\n\n## A\n\ntext\n\n## B\n\n### C\n\nmore\n'})
        x = got['x.md']
        self.assertEqual(x.level, 2)
        self.assertEqual([h.raw for h in x.sections], ['A', 'B'])
        self.assertEqual(x.sections[1].tokens, len('## B\n\n### C\n\nmore') // 4)

    def test_a_chain_of_lone_headings_descends_until_a_level_has_several(self):
        got = documents({'x.md': b'# T\n\n## S\n\n### A\n\n### B\n'})
        self.assertEqual(got['x.md'].level, 3)
        self.assertEqual([h.raw for h in got['x.md'].sections], ['A', 'B'])

    def test_a_lone_heading_with_nothing_below_it_stays_the_top_level(self):
        got = documents({'x.md': b'# Only\n\ntext\n'})
        self.assertEqual(got['x.md'].level, 1)
        self.assertEqual([h.raw for h in got['x.md'].sections], ['Only'])

    def test_a_titled_document_is_a_candidate_by_its_sections_below_the_title(self):
        titled = b'# Title\n\n' + doc('One', 'Two', 'Three')
        got = documents(candidate_files(**{'big.md': titled}))
        self.assertEqual(got['big.md'].level, 2)
        self.assertEqual(
            [h.raw for h in got['big.md'].sections], ['One', 'Two', 'Three']
        )
        self.assertTrue(got['big.md'].candidate)

    def test_a_document_that_cannot_be_read_is_listed_with_its_reason(self):
        got = documents({'x.md': b'Title\n=====\n', 'y.md': b'# Fine\n'})
        self.assertIn('setext heading', got['x.md'].error)
        self.assertEqual(got['x.md'].sections, ())
        self.assertFalse(got['x.md'].candidate)
        self.assertIsNone(got['y.md'].error)

    def test_inbound_references_group_by_referring_file_and_skip_the_file_itself(self):
        got = documents(
            {
                'a.md': b'# A\n\n[self](#a) [b](b.md#top)\n[again](b.md)\n',
                'b.md': b'# Top\n',
                'c.toml': b'see = "b.md#top"\n',
            }
        )
        self.assertEqual(got['a.md'].inbound, ())
        self.assertEqual(
            [
                (i.path, [(r.line, r.fragment) for r in i.refs])
                for i in got['b.md'].inbound
            ],
            [('a.md', [(3, 'top'), (4, '')]), ('c.toml', [(1, 'top')])],
        )

    def test_a_candidate_has_three_sections_over_2000_tokens_and_two_referring_files(
        self,
    ):
        self.assertTrue(documents(candidate_files())['big.md'].candidate)

    def test_each_condition_of_the_candidate_flag_can_fail_alone(self):
        cases = {
            'two sections': candidate_files(**{'big.md': doc('One', 'Two')}),
            'small': candidate_files(**{'big.md': doc('One', 'Two', 'Three', pad='w')}),
            'one referrer': candidate_files(**{'b.md': b'nothing\n'}),
            'same file twice': candidate_files(
                **{'a.md': b'[1](big.md#one)\n[2](big.md#two)\n', 'b.md': b'x\n'}
            ),
            'no anchors': candidate_files(
                **{'a.md': b'[f](big.md)\n', 'b.md': b'[f](big.md)\n'}
            ),
            'self references only': candidate_files(
                **{
                    'big.md': doc('One', 'Two', 'Three') + b'[a](#one) [b](#two)\n',
                    'a.md': b'x\n',
                    'b.md': b'x\n',
                }
            ),
        }
        for name, files in cases.items():
            with self.subTest(name):
                self.assertFalse(documents(files)['big.md'].candidate)

    def test_a_reference_to_nothing_is_broken_and_is_not_inbound(self):
        files = {
            'a.md': b'# A\n\n[gone](gone.md) [bad](b.md#nope) [ok](b.md#top) [dir](docs/) [dir2](docs)\n',
            'b.md': b'# Top\n',
            'docs/x.txt': b'text\n',
            'cfg.toml': b'p = "b.md#also-missing"\n',
        }
        inv = inventory_of(files)
        self.assertEqual(
            [(r.path, r.line, r.raw) for r in inv.broken],
            [
                ('a.md', 3, '[gone](gone.md)'),
                ('a.md', 3, '[bad](b.md#nope)'),
                ('cfg.toml', 1, 'b.md#also-missing'),
            ],
        )
        b = {d.path: d for d in inv.documents}['b.md']
        self.assertEqual([(i.path, len(i.refs)) for i in b.inbound], [('a.md', 1)])

    def test_the_coverage_block_names_what_is_recognized_and_what_is_not_seen(self):
        data = context.inventory_data(inventory_of({'a.md': b'# A\n'}))
        cov = data['coverage']
        self.assertEqual(cov['file_types'], ['.md', '.markdown', '.toml', '.txt'])
        self.assertEqual(cov['broken'], {'count': 0, 'refs': []})
        for needle in (
            'globs',
            'generated paths',
            'external links',
            'HTML comments',
            'wrap across lines',
            'next line',
            'empty `#` fragment',
        ):
            self.assertTrue(any(needle in x for x in cov['limits']), needle)
        for needle in (
            'inline links and images',
            'reference definitions',
            'wrapped over lines',
            'TOML and plain text',
        ):
            self.assertTrue(any(needle in x for x in cov['syntaxes']), needle)

    def test_migration_files_are_not_scanned(self):
        files = {
            'a.md': b'# A\n',
            '.agents/migrations/m.toml': b'from = "a.md#a"\n',
        }
        inv = inventory_of(files)
        self.assertEqual(inv.documents[0].inbound, ())


class DraftTests(unittest.TestCase):
    def test_the_draft_of_the_fixture_standards_is_pinned(self):
        repo, _ = fixture_repo(self)
        done = inventory(repo, '--draft', 'STANDARDS.md')
        self.assertEqual(done.returncode, 0, done.stderr)
        want = pinned('draft.toml', git(repo, 'rev-parse', 'HEAD')).replace(
            'SHA256', sha(STANDARDS)
        )
        self.assertEqual(done.stdout, want)

    def test_a_block_for_each_top_level_section_with_occurrence_for_a_repeat(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        (repo / 'docs').mkdir()
        (repo / 'docs/My Guide.md').write_text(
            '# Intro\n\ntext\n\n# Rules\n\n## Sub\n\n# Intro\n\nagain\n'
        )
        git(repo, 'init', '-q')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'x')
        text = context.draft(repo, 'docs/My Guide.md')
        self.assertIn('id = "My-Guide"', text)
        self.assertEqual(text.count('# [[move]]'), 3)
        self.assertIn('# ancestry = ["Intro"]\n# occurrence = 1\n# to = "TODO"\n', text)
        self.assertIn('# ancestry = ["Rules"]\n# to = "TODO"\n', text)
        self.assertIn('# ancestry = ["Intro"]\n# occurrence = 2\n# to = "TODO"\n', text)
        self.assertNotIn('Sub', text)

    def test_the_draft_loads_once_a_move_is_uncommented_and_filled_in(self):
        repo, _ = fixture_repo(self)
        text = context.draft(repo, 'STANDARDS.md')
        lines = text.splitlines()
        lines = [x.removeprefix('# ') if x.startswith('# ') else x for x in lines]
        filled = '\n'.join(lines)
        for name in ('a', 'b', 'c'):
            filled = filled.replace(
                'to = "TODO"', f'to = "docs/standards/{name}.md"', 1
            )
        filled = filled.replace(
            '  "STANDARDS.md",\n',
            '  "STANDARDS.md",\n  "docs/standards/a.md",\n  "docs/standards/b.md",\n  "docs/standards/c.md",\n',
        )
        m = context.parse_migration(filled)
        self.assertEqual(m.base, git(repo, 'rev-parse', 'HEAD'))
        self.assertEqual(m.files[0].sha256, sha(STANDARDS))
        self.assertEqual(m.moves[0].ancestry, ('Coding standards', 'Typed evidence'))
        self.assertEqual(m.moves[0].to, 'docs/standards/a.md')
        self.assertEqual(len(m.moves), 3)
        context.plan(repo, m)

    def test_a_titled_document_drafts_one_move_per_section_below_the_title(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        (repo / 'big.md').write_bytes(b'# Title\n\n' + doc('One', 'Two', 'Three'))
        git(repo, 'init', '-q')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'x')
        text = context.draft(repo, 'big.md')
        self.assertEqual(text.count('# [[move]]'), 3)
        for name in ('One', 'Two', 'Three'):
            self.assertIn(f'# ancestry = ["Title", "{name}"]\n', text)
        lines = [
            x.removeprefix('# ') if x.startswith('# ') else x for x in text.splitlines()
        ]
        filled = '\n'.join(lines)
        for name in ('One', 'Two', 'Three'):
            filled = filled.replace('to = "TODO"', f'to = "docs/{name}.md"', 1)
        filled = filled.replace(
            '  "big.md",\n',
            '  "big.md",\n  "docs/One.md",\n  "docs/Two.md",\n  "docs/Three.md",\n',
        )
        m = context.parse_migration(filled)
        self.assertEqual(len(m.moves), 3)
        context.plan(repo, m)

    def test_the_draft_alone_is_a_migration_with_nothing_to_write_but_its_source(self):
        repo, _ = fixture_repo(self)
        with self.assertRaises(context.ContextError) as caught:
            context.parse_migration(context.draft(repo, 'STANDARDS.md'))
        self.assertIn(
            'is not a source, destination or rewrite target', str(caught.exception)
        )

    def test_a_missing_file_a_heading_less_file_and_an_escape_exit_two(self):
        repo, _ = fixture_repo(self)
        (repo / 'plain.md').write_text('no headings\n')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'plain')
        for path, why in (
            ('nope.md', 'not a file at HEAD'),
            ('plain.md', 'has no headings'),
            ('../x.md', 'must not contain a `..` part'),
        ):
            with self.subTest(path):
                done = inventory(repo, '--draft', path)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stdout, '')
                self.assertIn(why, done.stderr)


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


TYPED_MD = 'docs/standards/typed-evidence.md'
RUST_MD = 'docs/standards/rust.md'


def boom(*args, **kwargs):
    raise AssertionError('prove called a writer')


def with_files(base):
    """`base` plus the bytes of each scanned file of FILES it lacks, from the fixture repo: `prove` reads them all."""
    return {
        p: (fixtures / 'repo' / p).read_bytes()
        for p in FILES
        if p.endswith(context.SCANNED) and (fixtures / 'repo' / p).is_file()
    } | base


def proved(m, base, outputs):
    """`prove` with every writer unavailable: it may use the readers only."""
    base = with_files(base)
    with contextlib.ExitStack() as stack:
        for name in ('assemble', 'rebase', 'rewritten', 'render', 'anchor_map'):
            stack.enter_context(mock.patch.object(context, name, boom))
        return context.prove(base, m, outputs, FILES)


def small(data, *moves, files=FILES):
    """Render a one-file migration with the real writers, then return (migration, base, outputs)."""
    m = migration_of(*moves)
    base = {'STANDARDS.md': data}
    return m, base, context.render(m, base, (), files)


def kinds(proof, path):
    return [(f.kind, f.line) for f in proof.findings if f.path == path]


class ProveTests(unittest.TestCase):
    def sample(self):
        m = sample_migration()
        return m, BASE, render_sample(m)

    def test_the_fixture_outputs_are_proved_without_the_writers(self):
        m, base, out = self.sample()
        proof = proved(m, base, out)
        self.assertEqual(proof.findings, [])
        self.assertTrue(proof.ok)
        self.assertEqual(
            [(b.source, b.start, b.end, b.dest, b.lines) for b in proof.blocks],
            [
                ('STANDARDS.md', 9, 29, TYPED_MD, 18),
                ('STANDARDS.md', 18, 25, RUST_MD, 12),
                ('STANDARDS.md', 42, 45, 'docs/standards/testing-notes.md', 4),
            ],
        )

    def test_every_output_line_has_one_origin(self):
        m, base, out = self.sample()
        proof = proved(m, base, out)
        for path, data in out.items():
            with self.subTest(path=path):
                self.assertEqual(len(proof.origins[path]), len(context.lines_of(data)))

    def test_the_origins_name_each_edit(self):
        m, base, out = self.sample()
        got = proved(m, base, out).origins
        typed = got[TYPED_MD]
        O, G = context.Original, context.Generated
        self.assertEqual(typed[0], O('STANDARDS.md', 9, 'shift'))
        self.assertEqual(typed[1], O('STANDARDS.md', 10, 'none'))
        self.assertEqual(typed[2], O('STANDARDS.md', 11, 'target'))
        self.assertEqual(typed[9], G('nested-heading', 'STANDARDS.md', 18))
        self.assertEqual(typed[11], G('pointer', 'STANDARDS.md', 18))
        self.assertEqual(typed[-1], context.Copied('STANDARDS.md', 51))
        self.assertEqual(typed[-2], O('STANDARDS.md', 29, 'none'))
        rust = got[RUST_MD]
        self.assertEqual(
            [x.kind for x in rust[1:3]], ['read-first-blank', 'read-first']
        )
        index = got['STANDARDS.md']
        self.assertIn(G('kept-heading', 'STANDARDS.md', 9), index)
        self.assertIn(G('pointer', 'STANDARDS.md', 9), index)
        self.assertEqual(index[0], O('STANDARDS.md', 1, 'none'))

    def test_a_dropped_block_fails_naming_the_path_and_line(self):
        m, base, out = self.sample()
        gone = b'Notes on evidence. Back to [the rule](#typed-evidence).\n\n'
        out[TYPED_MD] = out[TYPED_MD].replace(gone, b'')
        proof = proved(m, base, out)
        self.assertFalse(proof.ok)
        self.assertEqual(
            [k for k, _ in kinds(proof, TYPED_MD)], ['unexplained', 'dropped']
        )
        self.assertEqual(kinds(proof, TYPED_MD)[0][1], 16)

    def test_a_reordered_block_fails(self):
        m, base, out = self.sample()
        lines = out[RUST_MD].split(b'\n')
        lines[4], lines[8] = lines[8], lines[4]
        out[RUST_MD] = b'\n'.join(lines)
        proof = proved(m, base, out)
        self.assertTrue(all(f.path == RUST_MD for f in proof.findings))
        self.assertEqual(kinds(proof, RUST_MD)[0], ('dropped', 5))
        self.assertIn(('reordered', 7), kinds(proof, RUST_MD))
        self.assertIn(('reordered', 9), kinds(proof, RUST_MD))

    def test_a_duplicated_line_fails(self):
        m, base, out = self.sample()
        out[RUST_MD] = out[RUST_MD].replace(
            b'Notes on Rust.\n', b'Notes on Rust.\nNotes on Rust.\n'
        )
        proof = proved(m, base, out)
        self.assertEqual(kinds(proof, RUST_MD)[0], ('duplicated', 10))

    def test_an_unexplained_line_fails(self):
        m, base, out = self.sample()
        out['STANDARDS.md'] = out['STANDARDS.md'].replace(
            b'Keep it short.', b'Keep it short. And long.'
        )
        proof = proved(m, base, out)
        self.assertEqual(kinds(proof, 'STANDARDS.md'), [('unexplained', 43)])

    def test_a_pointer_to_the_wrong_destination_fails(self):
        m, base, out = self.sample()
        out['STANDARDS.md'] = out['STANDARDS.md'].replace(
            b'(docs/standards/rust.md#rust-engine)',
            b'(docs/standards/typed-evidence.md#rust-engine)',
            1,
        )
        proof = proved(m, base, out)
        self.assertFalse(proof.ok)

    def test_a_wrong_heading_level_fails(self):
        m, base, out = self.sample()
        out[TYPED_MD] = out[TYPED_MD].replace(
            b'# Typed evidence', b'## Typed evidence', 1
        )
        self.assertFalse(proved(m, base, out).ok)

    def test_a_definition_that_left_the_index_and_is_copied_nowhere_fails(self):
        m, base, out = self.sample()
        out[RUST_MD] = out[RUST_MD].replace(b'[moved-only]: ../guide.md\n', b'')
        proof = proved(m, base, out)
        self.assertEqual(
            [(f.path, f.kind, f.message) for f in proof.findings],
            [
                (
                    'STANDARDS.md',
                    'dropped',
                    'STANDARDS.md:50 left the index and no destination copies it',
                )
            ],
        )

    def test_a_definition_copied_twice_into_one_destination_fails(self):
        m, base, out = self.sample()
        out[RUST_MD] += b'[shared]: ../guide.md#moved "Shared"\n'
        proof = proved(m, base, out)
        self.assertEqual(kinds(proof, RUST_MD), [('duplicated', 13)])

    def test_a_definition_the_slice_already_holds_is_not_copied_again(self):
        data = b'# A\n\n## B\n\nsee [l].\n\n[l]: docs/guide.md\n'
        m, base, out = small(data, move(('A', 'B'), 'docs/b.md'))
        self.assertTrue(proved(m, base, out).ok)
        out['docs/b.md'] += b'\n[l]: docs/guide.md\n'
        self.assertEqual(
            [(f.path, f.kind) for f in proved(m, base, out).findings],
            [('docs/b.md', 'duplicated')],
        )
        self.assertEqual(kinds(proved(m, base, out), 'docs/b.md'), [('duplicated', 7)])

    def test_a_scanned_file_of_the_repo_without_bytes_is_refused(self):
        m, base, out = self.sample()
        with self.assertRaises(context.ContextError) as caught:
            context.prove(BASE, m, out, FILES)
        self.assertIn(
            'docs/adr/0007-standards.md: a scanned file of the repo has no bytes',
            str(caught.exception),
        )

    def test_a_rewrite_target_without_bytes_is_refused(self):
        m, base, out = self.sample()
        base = {
            k: v for k, v in with_files(BASE).items() if k != '.agents/kitchen.toml'
        }
        with self.assertRaises(context.ContextError) as caught:
            context.prove(base, m, out, FILES)
        self.assertIn(
            '.agents/kitchen.toml: rewrite target is not among the base files',
            str(caught.exception),
        )

    def test_a_broken_reference_in_a_file_the_migration_does_not_write_is_listed_with_its_line(
        self,
    ):
        m, base, out = self.sample()
        base = base | {
            'docs/guide.md': b'# Guide\n\n## Moved\n\nSee [x](../STANDARDS.md#nowhere).\n'
        }
        proof = proved(m, base, out)
        self.assertTrue(proof.ok, proof.findings)
        self.assertEqual(
            [(f.path, f.line) for f in proof.pre_existing if f.path == 'docs/guide.md'],
            [('docs/guide.md', 5)],
        )

    def test_a_bare_path_rewrite_is_accepted(self):
        data = b'# A\n\n## B\n\ntext\n'
        conf = b'path = "STANDARDS.md"\n'
        m = migration_of(
            move(('A', 'B'), 'docs/b.md'),
            files=('STANDARDS.md', 'conf.toml'),
            rewrites=(context.Rewrite('conf.toml', 'STANDARDS.md', 'docs/b.md'),),
        )
        base = {'STANDARDS.md': data, 'conf.toml': conf}
        files = {*FILES, 'conf.toml'}
        out = context.render(m, base, (), files)
        self.assertEqual(out['conf.toml'], b'path = "docs/b.md"\n')
        with contextlib.ExitStack() as stack:
            for name in ('assemble', 'rebase', 'rewritten', 'render', 'anchor_map'):
                stack.enter_context(mock.patch.object(context, name, boom))
            proof = context.prove(with_files(base), m, out, files)
        self.assertEqual(proof.findings, [])
        self.assertEqual([f for f in proof.pre_existing if f.path == 'conf.toml'], [])

    def test_chained_bare_path_rewrites_on_one_file_pass(self):
        data = b'# A\n\n## B\n\ntext\n'
        m = migration_of(
            move(('A', 'B'), 'docs/b.md'),
            files=('STANDARDS.md', 'conf.toml'),
            rewrites=(
                context.Rewrite('conf.toml', 'STANDARDS.md', 'docs/b.md'),
                context.Rewrite('conf.toml', 'docs/b.md', 'docs/c.md'),
            ),
        )
        base = {
            'STANDARDS.md': data,
            'conf.toml': b'path = "STANDARDS.md"\n',
            'docs/c.md': b'# C\n',
        }
        files = {*FILES, 'conf.toml', 'docs/c.md'}
        out = context.render(m, base, (), files)
        self.assertEqual(out['conf.toml'], b'path = "docs/c.md"\n')
        proof = context.prove(with_files(base), m, out, files)
        self.assertEqual(proof.findings, [])

    def test_a_copied_definition_the_destination_never_uses_fails(self):
        m, base, out = self.sample()
        out['docs/standards/testing-notes.md'] += (
            b'\n[shared]: ../guide.md#moved "Shared"\n'
        )
        proof = proved(m, base, out)
        self.assertTrue(any('never uses' in f.message for f in proof.findings))

    def test_an_absent_output_fails(self):
        m, base, out = self.sample()
        out[RUST_MD] = None
        proof = proved(m, base, out)
        self.assertEqual(kinds(proof, RUST_MD), [('absent', 0)])

    def test_a_link_rebased_to_the_wrong_one_of_two_same_named_headings_fails(self):
        m, base, out = self.sample()
        out[RUST_MD] = out[RUST_MD].replace(
            b'typed-evidence.md#notes', b'testing-notes.md#notes'
        )
        proof = proved(m, base, out)
        self.assertEqual(
            [(f.path, f.kind) for f in proof.findings], [(RUST_MD, 'link')]
        )
        self.assertIn(
            'is expected to land on docs/standards/typed-evidence.md#notes',
            proof.findings[0].message,
        )

    def test_a_link_to_a_heading_that_is_not_there_fails(self):
        m, base, out = self.sample()
        out[RUST_MD] = out[RUST_MD].replace(
            b'typed-evidence.md#notes', b'typed-evidence.md#nowhere'
        )
        proof = proved(m, base, out)
        self.assertEqual(
            [(f.path, f.kind) for f in proof.findings], [(RUST_MD, 'link')]
        )

    def test_a_fragment_between_two_moved_sections_passes(self):
        m, base, out = self.sample()
        self.assertIn(b'[Notes](typed-evidence.md#notes)', out[RUST_MD])
        self.assertTrue(proved(m, base, out).ok)

    def test_a_pointer_to_the_wrong_heading_of_the_right_file_fails(self):
        m, base, out = self.sample()
        out['STANDARDS.md'] = out['STANDARDS.md'].replace(
            b'rust.md#rust-engine', b'rust.md#rust-notes', 1
        )
        proof = proved(m, base, out)
        self.assertEqual([f.kind for f in proof.findings], ['link'])
        self.assertIn('points at #rust-notes', proof.findings[0].message)

    def test_a_kept_reference_in_a_file_the_migration_does_not_write_still_resolves(
        self,
    ):
        m, base, out = self.sample()
        base = base | {
            'docs/adr/0007-standards.md': (
                fixtures / 'repo/docs/adr/0007-standards.md'
            ).read_bytes(),
            'docs/guide.md': (fixtures / 'repo/docs/guide.md').read_bytes(),
        }
        proof = proved(m, base, out)
        self.assertTrue(proof.ok, proof.findings)

    def test_headings_that_collide_only_through_a_code_span_keep_their_own_links(self):
        data = (
            b'# A\n\n## `x` y\n\nbody1\n\n## x y\n\nbody2 see [1](#x-y) and [2](#x-y-1)\n\n'
            b'## Z\n\nstay see [first](#x-y)\n'
        )
        m, base, out = small(
            data, move(('A', '`x` y'), 'p.md'), move(('A', 'x y'), 'q.md')
        )
        self.assertEqual(
            out['q.md'], b'# x y\n\nbody2 see [1](p.md#x-y) and [2](#x-y)\n\n'
        )
        self.assertTrue(proved(m, base, out).ok)
        out['q.md'] = out['q.md'].replace(b'(p.md#x-y)', b'(#x-y)')
        proof = proved(m, base, out)
        self.assertEqual([(f.path, f.kind) for f in proof.findings], [('q.md', 'link')])

    def test_a_reference_broken_at_base_is_listed_and_not_counted(self):
        data = (
            b'# A\n\n## B\n\nsee [gone](missing.md) and [there](docs/guide.md#moved)\n'
        )
        m, base, out = small(data, move(('A', 'B'), 'docs/b.md'))
        proof = proved(m, base, out)
        self.assertTrue(proof.ok, proof.findings)
        self.assertEqual(
            [
                (f.path, f.line, f.message)
                for f in proof.pre_existing
                if f.path == 'STANDARDS.md'
            ],
            [('STANDARDS.md', 5, '[gone](missing.md) (broken at base)')],
        )

    def test_a_rewrite_target_must_equal_its_base_with_the_rewrite_applied(self):
        m, base, out = self.sample()
        out['.agents/kitchen.toml'] = KITCHEN_TOML
        proof = proved(m, base, out)
        self.assertEqual(kinds(proof, '.agents/kitchen.toml')[0][0], 'unexplained')

    def test_a_rewrite_target_lines_are_originals(self):
        m, base, out = self.sample()
        origins = proved(m, base, out).origins['.agents/kitchen.toml']
        self.assertEqual(
            {o.edit for o in origins if isinstance(o, context.Original)},
            {'none', 'target'},
        )

    def test_an_index_that_ends_at_the_moved_section_may_omit_its_last_blank(self):
        data = b'# A\n\n## B\n\ntext\n'
        m, base, out = small(data, move(('A', 'B'), 'b.md'))
        self.assertTrue(out['STANDARDS.md'].endswith(b'#b).\n'))
        self.assertTrue(proved(m, base, out).ok)
        out['STANDARDS.md'] += b'\n'
        self.assertTrue(proved(m, base, out).ok)

    def test_the_last_line_of_a_slice_may_gain_a_newline_before_a_copied_definition(
        self,
    ):
        data = b'# A\n\n[l]: docs/guide.md\n\nsee [l].\n\n## B\n\nuses [l]'
        m, base, out = small(data, move(('A', 'B'), 'docs/standards/b.md'))
        self.assertTrue(
            out['docs/standards/b.md'].endswith(b'uses [l]\n\n[l]: ../guide.md\n')
        )
        proof = proved(m, base, out)
        self.assertTrue(proof.ok, proof.findings)
        edits = [
            o.edit
            for o in proof.origins['docs/standards/b.md']
            if isinstance(o, context.Original)
        ]
        self.assertEqual(edits[-1], 'newline')
        self.assertIn(
            context.Copied('STANDARDS.md', 3), proof.origins['docs/standards/b.md']
        )

    def test_a_newline_added_where_nothing_follows_fails(self):
        data = b'# A\n\n## B\n\ntext'
        m, base, out = small(data, move(('A', 'B'), 'b.md'))
        out['b.md'] += b'\n'
        self.assertFalse(proved(m, base, out).ok)

    def test_a_blank_after_the_read_first_line_is_generated_when_text_follows(self):
        data = b'# A\n\n## B\ntext\n\n## C\n\nother\n'
        m = migration_of(
            move(('A', 'C'), 'c.md'), move(('A', 'B'), 'b.md', requires=['c.md'])
        )
        base = {'STANDARDS.md': data}
        out = context.render(m, base, (), FILES)
        self.assertEqual(out['b.md'], b'# B\n\nRead first: [c.md](c.md).\n\ntext\n\n')
        proof = proved(m, base, out)
        self.assertTrue(proof.ok, proof.findings)
        self.assertEqual(
            [o.kind for o in proof.origins['b.md'] if isinstance(o, context.Generated)],
            ['read-first-blank', 'read-first', 'read-first-blank'],
        )
        out['b.md'] = out['b.md'].replace(b'.\n\ntext', b'.\ntext')
        self.assertFalse(proved(m, base, out).ok)


OVERLAY = '.agents/playbooks/standards.toml'


def overlay_text(*parts, source='STANDARDS.md'):
    head = 'schema = 1\nid = "standards"\ntitle = "Standards"\nkind = "language"\n'
    body = ''.join(
        f'\n[[part]]\nid = "{pid}"\nsource = {{ root = "repo", path = "{source}" }}\n'
        f'select = "{selector}"\nowners = ["sidekick"]\nreviewed = "{"0" * 12}"\n'
        for pid, selector in parts
    )
    return (head + body).encode()


def without_rewrites(m):
    return dataclasses.replace(
        m,
        rewrites=(),
        writes=tuple(w for w in m.writes if w != '.agents/kitchen.toml'),
    )


class ConsumerTests(unittest.TestCase):
    def prove_with(self, m, base, consumers):
        out = context.render(m, base, (), {*FILES, *base})
        return proved_with(m, base, out, consumers)

    def test_a_kitchen_source_landing_on_an_index_pointer_fails(self):
        m = without_rewrites(sample_migration())
        proof = self.prove_with(m, BASE, {'.agents/kitchen.toml': KITCHEN_TOML})
        self.assertEqual(
            [(f.path, f.line, f.kind) for f in proof.findings],
            [('.agents/kitchen.toml', 2, 'consumer')],
        )
        self.assertIn(
            'lands on the pointer STANDARDS.md keeps', proof.findings[0].message
        )

    def test_the_same_source_passes_when_the_migration_rewrites_it(self):
        m = sample_migration()
        proof = self.prove_with(m, BASE, {'.agents/kitchen.toml': KITCHEN_TOML})
        self.assertTrue(proof.ok, proof.findings)
        self.assertEqual(
            [(c.path, c.what, c.ok) for c in proof.consumers],
            [('.agents/kitchen.toml', 'line 2', True)],
        )

    def test_an_overlay_with_two_parts_from_one_source_fails_until_it_is_rewritten(
        self,
    ):
        parts = (
            ('a', 'heading:Typed evidence'),
            ('b', 'heading:Typed evidence/Notes'),
        )
        text = overlay_text(*parts)
        m = without_rewrites(sample_migration())
        proof = self.prove_with(m, BASE | {OVERLAY: text}, {OVERLAY: text})
        self.assertEqual([f.kind for f in proof.findings], ['consumer', 'consumer'])
        self.assertIn('part a', proof.findings[0].message)
        self.assertIn('part b', proof.findings[1].message)
        self.assertIn(
            'only points to where its section moved', proof.findings[1].message
        )

        rewrite = context.Rewrite(
            OVERLAY, 'STANDARDS.md', 'docs/standards/typed-evidence.md', 2
        )
        base = BASE | {OVERLAY: text}
        done = dataclasses.replace(
            m,
            rewrites=(rewrite,),
            writes=(*m.writes, OVERLAY),
            files=(*m.files, context.FileEntry(OVERLAY, '0' * 64)),
        )
        proof = self.prove_with(done, base, {OVERLAY: text})
        self.assertTrue(proof.ok, proof.findings)
        self.assertEqual([c.ok for c in proof.consumers], [True, True])
        self.assertRegex(
            proof.consumers[0].detail, r'^reviewed 0{12}, now [0-9a-f]{12}$'
        )

    def test_an_overlay_part_on_a_staying_section_holds_and_a_nested_stub_fails(self):
        text = overlay_text(
            ('style', 'heading:Style'),
            ('rust', 'heading:Rust (Engine)'),
            ('none', 'heading:Nowhere'),
            ('twice', 'heading:Examples'),
        )
        m = without_rewrites(sample_migration())
        proof = self.prove_with(m, BASE, {OVERLAY: text})
        verdicts = {c.what: c.ok for c in proof.consumers}
        self.assertEqual(
            verdicts,
            {
                'part style': True,
                'part rust': False,
                'part none': False,
                'part twice': False,
            },
        )
        why = {c.what: c.detail for c in proof.consumers}
        self.assertIn('no such part', why['part none'])
        self.assertIn('ambiguous, 2 parts match', why['part twice'])

    def test_a_part_whose_source_is_not_migrated_or_not_in_the_repo_root_is_skipped(
        self,
    ):
        text = overlay_text(('a', 'heading:Typed evidence'), source='docs/guide.md')
        m = without_rewrites(sample_migration())
        proof = self.prove_with(m, BASE, {OVERLAY: text})
        self.assertEqual(proof.consumers, [])
        skills = text.replace(b'root = "repo"', b'root = "skills"')
        self.assertEqual(self.prove_with(m, BASE, {OVERLAY: skills}).consumers, [])


class ConsumerReferenceTests(unittest.TestCase):
    """A kitchen.toml reference is judged against what it resolved to at base."""

    def trees(self, base_guide, out_guide):
        toml = b'source = "docs/guide.md#moved"\n'
        files = {'docs/guide.md', '.agents/kitchen.toml'}
        base = context.Tree(
            {'docs/guide.md': base_guide, '.agents/kitchen.toml': toml}, files
        )
        out = context.Tree(
            {'docs/guide.md': out_guide, '.agents/kitchen.toml': toml}, files
        )
        return base, out

    def judge(self, base_guide, out_guide):
        base, out = self.trees(base_guide, out_guide)
        return context.consumer_proof(
            base, out, ['.agents/kitchen.toml'], {'docs/guide.md': []}, {}
        )

    def test_a_reference_that_resolved_at_base_and_no_longer_does_fails(self):
        found, seen, broken = self.judge(b'# Moved\n', b'# Elsewhere\n')
        self.assertEqual(
            [(f.path, f.line, f.kind) for f in found],
            [('.agents/kitchen.toml', 1, 'consumer')],
        )
        self.assertIn('docs/guide.md#moved no longer resolves', found[0].message)
        self.assertEqual(broken, [])
        self.assertEqual([c.ok for c in seen], [False])

    def test_a_reference_broken_at_base_is_listed_not_failed(self):
        found, seen, broken = self.judge(b'# Other\n', b'# Other\n')
        self.assertEqual(found, [])
        self.assertEqual(seen, [])
        self.assertEqual(
            [(f.path, f.line, f.message) for f in broken],
            [('.agents/kitchen.toml', 1, 'docs/guide.md#moved (broken at base)')],
        )

    def test_a_reference_that_still_resolves_holds(self):
        found, seen, broken = self.judge(b'# Moved\n', b'# Moved\n')
        self.assertEqual((found, broken), ([], []))
        self.assertEqual([c.ok for c in seen], [True])


def proved_with(m, base, outputs, consumers):
    with contextlib.ExitStack() as stack:
        for name in ('assemble', 'rebase', 'rewritten', 'render', 'anchor_map'):
            stack.enter_context(mock.patch.object(context, name, boom))
        return context.prove(with_files(base), m, outputs, FILES, consumers)


def applied_repo(test):
    repo, text = fixture_repo(test)
    mig = repo / 'migration.toml'
    mig.write_text(text)
    m = context.parse_migration(text)
    for path, data in context.render(
        m,
        context.read_base(repo, m),
        context.base_files_at(repo, m, {x.to for x in m.moves}),
        context.repo_files(repo, m),
    ).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(data)
    return repo, mig


class CheckTests(unittest.TestCase):
    def test_a_tree_holding_the_rendered_outputs_checks_clean(self):
        repo, mig = applied_repo(self)
        proof = context.check(repo, mig)
        self.assertTrue(proof.ok, proof.findings)

    def test_an_overlay_edited_in_the_working_tree_after_the_apply_is_read_from_there(
        self,
    ):
        repo, mig = applied_repo(self)
        (repo / '.agents/playbooks').mkdir()
        overlay = repo / OVERLAY
        overlay.write_bytes(overlay_text(('a', 'heading:Typed evidence')))
        proof = context.check(repo, mig)
        self.assertEqual([f.kind for f in proof.findings], ['consumer'])
        overlay.write_bytes(
            overlay_text(
                ('a', 'heading:Typed evidence'),
                source='docs/standards/typed-evidence.md',
            )
        )
        proof = context.check(repo, mig)
        self.assertTrue(proof.ok, proof.findings)
        self.assertEqual([c.ok for c in proof.consumers if c.path == OVERLAY], [True])

    def test_a_deleted_line_of_a_destination_fails_naming_it(self):
        repo, mig = applied_repo(self)
        dest = repo / 'docs/standards/testing-notes.md'
        dest.write_bytes(dest.read_bytes().replace(b'Notes on testing.\n', b''))
        proof = context.check(repo, mig)
        self.assertFalse(proof.ok)
        self.assertEqual(proof.findings[0].path, 'docs/standards/testing-notes.md')


def check_run(repo, mig, *extra):
    return subprocess.run(
        [sys.executable, str(KITCHEN), 'context', 'check', str(mig), *extra],
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def stable(text):
    """Mask the temp repo's base sha, which differs on every run."""
    return re.sub(r'base [0-9a-f]{12}', 'base BASE', text)


class CheckCommandTests(unittest.TestCase):
    def test_the_text_has_a_line_per_block_the_consumers_and_the_trees(self):
        repo, mig = applied_repo(self)
        done = check_run(repo, mig)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            stable(done.stdout),
            'STANDARDS.md:9-29 -> docs/standards/typed-evidence.md (18 lines): ok\n'
            'STANDARDS.md:18-25 -> docs/standards/rust.md (12 lines): ok\n'
            'STANDARDS.md:42-45 -> docs/standards/testing-notes.md (4 lines): ok\n'
            'consumer .agents/kitchen.toml line 2: docs/standards/typed-evidence.md#typed-evidence: ok\n'
            'proven against: the planned files, the kitchen.toml and the overlays as they are on disk;'
            ' every other file as at base BASE\n'
            'ok\n',
        )

    def test_json_is_the_proof(self):
        repo, mig = applied_repo(self)
        done = check_run(repo, mig, '--json')
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(done.stdout)
        self.assertEqual(data['ok'], True)
        self.assertEqual(data['findings'], [])
        self.assertEqual(data['pre_existing'], [])
        self.assertEqual(
            data['blocks'][0],
            {
                'source': 'STANDARDS.md',
                'start': 9,
                'end': 29,
                'dest': TYPED_MD,
                'lines': 18,
            },
        )
        self.assertEqual(
            data['consumers'],
            [
                {
                    'path': '.agents/kitchen.toml',
                    'what': 'line 2',
                    'target': 'docs/standards/typed-evidence.md#typed-evidence',
                    'ok': True,
                    'detail': '',
                }
            ],
        )
        self.assertEqual(
            data['origins'][TYPED_MD][:2],
            [
                {
                    'origin': 'original',
                    'source': 'STANDARDS.md',
                    'line': 9,
                    'edit': 'shift',
                },
                {
                    'origin': 'original',
                    'source': 'STANDARDS.md',
                    'line': 10,
                    'edit': 'none',
                },
            ],
        )
        self.assertEqual(
            data['origins'][TYPED_MD][9],
            {
                'origin': 'generated',
                'kind': 'nested-heading',
                'source': 'STANDARDS.md',
                'line': 18,
            },
        )
        self.assertEqual({s['state'] for s in data['states']}, {'post'})
        self.assertEqual(
            {p: len(o) for p, o in data['origins'].items()}['STANDARDS.md'],
            len(context.lines_of((repo / 'STANDARDS.md').read_bytes())),
        )

    def test_a_tree_with_a_file_at_its_pre_image_prints_the_summary_and_stops(self):
        repo, mig = applied_repo(self)
        (repo / 'docs/standards/testing-notes.md').unlink()
        done = check_run(repo, mig)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout,
            '1 of 5 planned files at pre-image, 4 at post-image, 0 foreign\n'
            'pre: docs/standards/testing-notes.md\n',
        )
        data = json.loads(check_run(repo, mig, '--json').stdout)
        self.assertEqual(data['ok'], False)
        self.assertEqual(
            [s['state'] for s in data['states']],
            ['post', 'post', 'post', 'pre', 'post'],
        )

    def test_a_tree_nothing_was_applied_to_is_all_pre_image(self):
        repo, text = fixture_repo(self)
        mig = repo / 'migration.toml'
        mig.write_text(text)
        done = check_run(repo, mig)
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stdout.startswith(
                '5 of 5 planned files at pre-image, 0 at post-image, 0 foreign\n'
            )
        )

    def test_a_deleted_line_of_a_destination_exits_one_naming_path_and_line(self):
        repo, mig = applied_repo(self)
        dest = repo / 'docs/standards/testing-notes.md'
        dest.write_bytes(dest.read_bytes().replace(b'Notes on testing.\n', b''))
        done = check_run(repo, mig)
        self.assertEqual(done.returncode, 1)
        lines = done.stdout.splitlines()
        self.assertEqual(
            lines[0], '0 of 5 planned files at pre-image, 4 at post-image, 1 foreign'
        )
        self.assertEqual(lines[1], 'foreign: docs/standards/testing-notes.md')
        self.assertIn(
            'docs/standards/testing-notes.md:3: dropped: missing before this line: STANDARDS.md:44',
            lines,
        )
        self.assertIn(
            'STANDARDS.md:42-45 -> docs/standards/testing-notes.md (3 lines): dropped: missing before this line: STANDARDS.md:44',
            lines,
        )


def small_repo(test, files, migration):
    """A git repository of `files` and a migration for it: `migration` with `{base}` and each `{sha:path}` filled."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    repo = Path(tmp.name) / 'repo'
    repo.mkdir()
    for path, data in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(data)
    git(repo, 'init', '-q')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'base')
    text = migration.replace('{base}', git(repo, 'rev-parse', 'HEAD'))
    for path, data in files.items():
        text = text.replace(f'{{sha:{path}}}', sha(data))
    mig = repo.parent / 'migration.toml'
    mig.write_text(text)
    return repo, mig


TOC = b'# A\n\nSee [b](#b) and [c](#c).\n\n## B\n\ntext\n\n## C\n\nmore\n'
TOC_MIGRATION = """schema = 1
id = "toc"
base = "{base}"
writes = ["STANDARDS.md", "b.md"]

[[file]]
path = "STANDARDS.md"
sha256 = "{sha:STANDARDS.md}"

[[move]]
source = "STANDARDS.md"
ancestry = ["A", "B"]
to = "b.md"
"""


class IndexLocalTests(unittest.TestCase):
    def preview_toc(self, *extra):
        repo, mig = small_repo(self, {'STANDARDS.md': TOC}, TOC_MIGRATION)
        return subprocess.run(
            [sys.executable, str(KITCHEN), 'context', 'preview', str(mig), *extra],
            cwd=repo,
            env=GIT_ENV,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_a_staying_reference_to_a_section_the_same_file_moved_does_not_fail_preview(
        self,
    ):
        done = self.preview_toc()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(
            'index-local: STANDARDS.md:3: [b](#b) (the tool cannot rewrite a move source)',
            done.stdout.splitlines(),
        )
        self.assertNotIn('unresolved:', done.stdout)

    def test_json_marks_it_and_keeps_the_other_keys(self):
        done = self.preview_toc('--json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            json.loads(done.stdout)['unresolved'],
            [
                {
                    'path': 'STANDARDS.md',
                    'line': 3,
                    'raw': '[b](#b)',
                    'target': 'STANDARDS.md#b',
                    'index-local': True,
                }
            ],
        )

    def test_a_reference_from_another_file_still_counts(self):
        files = {'STANDARDS.md': TOC, 'other.md': b'See [b](STANDARDS.md#b).\n'}
        repo, mig = small_repo(self, files, TOC_MIGRATION)
        done = subprocess.run(
            [sys.executable, str(KITCHEN), 'context', 'preview', str(mig)],
            cwd=repo,
            env=GIT_ENV,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn('unresolved: other.md:1: [b](STANDARDS.md#b)', done.stdout)
        self.assertIn('index-local: STANDARDS.md:3', done.stdout)


def apply_run(repo, mig, *extra, env=None, umask=None):
    return subprocess.run(
        [sys.executable, str(KITCHEN), 'context', 'apply', str(mig), *extra],
        cwd=repo,
        env=GIT_ENV | (env or {}),
        preexec_fn=None if umask is None else (lambda: os.umask(umask)),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def tree_of(repo):
    """Every file of the working tree outside .git, by path, with its bytes."""
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in sorted(repo.rglob('*'))
        if p.is_file() and '.git' not in p.relative_to(repo).parts
    }


def fixture_migration(test, text=None):
    repo, sample = fixture_repo(test)
    mig = repo.parent / 'migration.toml'
    mig.write_text(sample if text is None else text(sample))
    return repo, mig


class ApplyTests(unittest.TestCase):
    def test_it_writes_every_planned_file_and_check_then_passes(self):
        repo, mig = fixture_migration(self)
        done = apply_run(repo, mig)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [f'wrote: {p}' for p in sample_migration().writes],
        )
        for path in sample_migration().writes:
            self.assertEqual((repo / path).read_bytes(), expected(path))
        checked = check_run(repo, mig)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_a_second_run_says_no_change_and_touches_no_file(self):
        repo, mig = fixture_migration(self)
        self.assertEqual(apply_run(repo, mig).returncode, 0)
        stamps = {p: (repo / p).stat().st_mtime_ns for p in sample_migration().writes}
        done = apply_run(repo, mig)
        self.assertEqual((done.returncode, done.stdout), (0, 'no change\n'))
        self.assertEqual(
            stamps,
            {p: (repo / p).stat().st_mtime_ns for p in sample_migration().writes},
        )

    def test_a_hand_edited_source_refuses_naming_it_and_changes_nothing(self):
        repo, mig = fixture_migration(self)
        with (repo / 'STANDARDS.md').open('a') as f:
            f.write('by hand\n')
        before = tree_of(repo)
        done = apply_run(repo, mig)
        self.assertEqual(done.returncode, 1)
        self.assertIn('foreign: STANDARDS.md', done.stderr)
        self.assertIn('inventory --draft', done.stderr)
        self.assertEqual(tree_of(repo), before)

    def test_a_destination_created_by_hand_refuses_naming_it_and_changes_nothing(self):
        repo, mig = fixture_migration(self)
        (repo / 'docs/standards').mkdir(parents=True)
        (repo / 'docs/standards/rust.md').write_text('mine\n')
        before = tree_of(repo)
        done = apply_run(repo, mig)
        self.assertEqual(done.returncode, 1)
        self.assertIn('foreign: docs/standards/rust.md', done.stderr)
        self.assertEqual(tree_of(repo), before)

    def test_an_unresolved_reference_refuses_and_changes_nothing(self):
        repo, mig = fixture_migration(self, lambda t: without(t, 'rewrite', 'keep'))
        before = tree_of(repo)
        done = apply_run(repo, mig)
        self.assertEqual(done.returncode, 1)
        self.assertIn(KITCHEN_LINE.strip(), done.stderr)
        self.assertIn(ADR_LINE.strip(), done.stderr)
        self.assertEqual(tree_of(repo), before)

    def test_a_path_outside_writes_is_untouched(self):
        repo, mig = fixture_migration(self)
        before = tree_of(repo)
        self.assertEqual(apply_run(repo, mig).returncode, 0)
        after = tree_of(repo)
        writes = set(sample_migration().writes)
        for path, data in before.items():
            if path not in writes:
                self.assertEqual(after[path], data, path)
        self.assertEqual(set(after) - set(before) - writes, set())

    def test_a_plan_that_does_not_prove_writes_nothing(self):
        repo, mig = fixture_migration(self)
        before = tree_of(repo)
        with mock.patch.object(context, 'prove', return_value=failing_proof()):
            with self.assertRaises(context.ApplyRefused) as caught:
                context.apply(repo, mig)
        self.assertIn('nothing written', str(caught.exception))
        self.assertEqual(tree_of(repo), before)

    def test_a_stop_after_that_is_not_a_positive_whole_number_exits_two(self):
        repo, mig = fixture_migration(self)
        for bad in ('0', '-1', 'x', ''):
            with self.subTest(bad):
                done = apply_run(repo, mig, env={'KITCHEN_CONTEXT_STOP_AFTER': bad})
                self.assertEqual(done.returncode, 2)
                self.assertIn('KITCHEN_CONTEXT_STOP_AFTER', done.stderr)


def failing_proof():
    return context.Proof({}, [], [context.Finding('x.md', 1, 'dropped', 'gone')])


STOP = {'KITCHEN_CONTEXT_STOP_AFTER': '1'}


def counts(repo, mig):
    found = [s for _, s in context.states(repo, context.load(mig))]
    return {s: found.count(s) for s in context.STATES}


class ApplyRecoveryTests(unittest.TestCase):
    def test_a_stop_leaves_every_file_at_an_image_and_each_rerun_goes_on(self):
        repo, mig = fixture_migration(self)
        total = len(sample_migration().writes)
        first = apply_run(repo, mig, env=STOP)
        self.assertEqual(first.returncode, 1)
        self.assertIn('stopped after 1 writes', first.stderr)
        self.assertEqual(first.stdout, 'wrote: STANDARDS.md\n')
        self.assertEqual(
            [
                p
                for p in tree_of(repo)
                if '.tmp' in p or p.rsplit('/', 1)[-1].startswith('.')
            ],
            [],
        )
        self.assertEqual(counts(repo, mig), {'pre': total - 1, 'post': 1, 'foreign': 0})
        half = check_run(repo, mig)
        self.assertEqual(half.returncode, 1)
        self.assertIn(
            f'{total - 1} of {total} planned files at pre-image, 1 at post-image, 0 foreign',
            half.stdout,
        )
        second = apply_run(repo, mig, env=STOP)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(counts(repo, mig), {'pre': total - 2, 'post': 2, 'foreign': 0})
        done = apply_run(repo, mig)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count('wrote: '), total - 2)
        checked = check_run(repo, mig)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_an_existing_file_keeps_its_mode_and_a_new_one_gets_the_umask_default(self):
        repo, mig = fixture_migration(self)
        (repo / 'STANDARDS.md').chmod(0o755)
        done = apply_run(repo, mig, umask=0o027)
        self.assertEqual(done.returncode, 0, done.stderr)
        mode = lambda p: (repo / p).stat().st_mode & 0o777  # noqa: E731
        self.assertEqual(mode('STANDARDS.md'), 0o755)
        self.assertEqual(mode('docs/standards/rust.md'), 0o640)
        self.assertEqual(mode('.agents/kitchen.toml'), 0o644)

    def test_no_temp_file_is_left_beside_the_written_ones(self):
        repo, mig = fixture_migration(self)
        before = set(tree_of(repo))
        self.assertEqual(apply_run(repo, mig).returncode, 0)
        self.assertEqual(
            set(tree_of(repo)) - before, set(sample_migration().writes) - before
        )


def kitchen_run(repo, *args):
    return subprocess.run(
        [sys.executable, str(KITCHEN), 'context', *args],
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class EndToEndTests(unittest.TestCase):
    def test_preview_apply_check_apply_and_validate_on_a_git_repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / 'repo'
        shutil.copytree(fixtures / 'repo', repo)
        toml = repo / '.agents/kitchen.toml'
        toml.write_text(
            'version = 1\n[profile.docs]\npaths = ["**/*.md"]\n' + toml.read_text()
        )
        git(repo, 'init', '-q')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'fixture')
        text = SAMPLE.replace(PLACEHOLDER_BASE, git(repo, 'rev-parse', 'HEAD'))
        for path, placeholder in PLACEHOLDER_SHA.items():
            text = text.replace(placeholder, sha((repo / path).read_bytes()))
        mig = Path(tmp.name) / 'migration.toml'
        mig.write_text(text)
        before = tree_of(repo)
        seen = kitchen_run(repo, 'preview', str(mig))
        self.assertEqual(seen.returncode, 0, seen.stderr)
        self.assertEqual(tree_of(repo), before)
        done = kitchen_run(repo, 'apply', str(mig))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count('wrote: '), 5)
        checked = kitchen_run(repo, 'check', str(mig))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        again = kitchen_run(repo, 'apply', str(mig))
        self.assertEqual((again.returncode, again.stdout), (0, 'no change\n'))
        valid = subprocess.run(
            [sys.executable, str(KITCHEN), '--repo', str(repo), 'validate'],
            env=GIT_ENV,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)


class ApplyExactStopTests(unittest.TestCase):
    def test_a_stop_after_as_many_as_remain_writes_them_all_and_exits_zero(self):
        repo, mig = fixture_migration(self)
        total = len(sample_migration().writes)
        done = apply_run(repo, mig, env={'KITCHEN_CONTEXT_STOP_AFTER': str(total)})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count('wrote: '), total)


if __name__ == '__main__':
    unittest.main()
