import importlib.util
import tempfile
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
fixtures = root / 'tests/fixtures/playbooks'
spec = importlib.util.spec_from_file_location(
    'playbooks', root / 'skills/pstack-kitchen/scripts/playbooks.py'
)
assert spec and spec.loader
playbooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(playbooks)


def load(name):
    return playbooks.parse((fixtures / name).read_text())


def pick(doc, selector):
    return playbooks.text_of(doc, playbooks.select(doc, selector))


class SelectorTests(unittest.TestCase):
    def test_intro_selects_the_paragraphs_before_the_first_step(self):
        doc = load('bug-fix.md')
        self.assertTrue(pick(doc, 'intro:1').startswith('**You own this task.'))
        self.assertTrue(pick(doc, 'intro:2').startswith('Be scientific.'))

    def test_step_selects_a_top_level_item_with_its_continuation_lines(self):
        doc = load('bug-fix.md')
        self.assertTrue(pick(doc, 'step:3').startswith('3. Plan the fix.'))
        step5 = pick(doc, 'step:5')
        self.assertTrue(step5.startswith('5. Stage the commits'))
        self.assertIn('canonical **sequence-verifiable-units**', step5)

    def test_reply_selects_the_reply_line(self):
        self.assertTrue(
            pick(load('bug-fix.md'), 'reply').startswith('**Reply:** what was broken')
        )

    def test_tail_selects_the_paragraph_after_the_last_step(self):
        doc = load('investigation.md')
        self.assertTrue(pick(doc, 'tail:1').startswith('No PR, no babysit'))
        self.assertTrue(pick(doc, 'step:4').startswith('4. Apply the **unslop**'))

    def test_lead_selects_the_paragraph_with_that_bold_lead_in(self):
        doc = load('opening-a-pr.md')
        self.assertTrue(pick(doc, 'lead:Worktree').startswith('**Worktree.**'))
        self.assertTrue(
            pick(doc, 'lead:Built-in PR tool').startswith('**Built-in PR tool.**')
        )
        self.assertTrue(pick(doc, 'intro:1').startswith('Invoked at the end'))

    def test_a_lead_keeps_its_list_but_not_the_paragraphs_after_it(self):
        doc = load('opening-a-pr.md')
        descriptions = pick(doc, 'lead:Descriptions')
        self.assertIn('- `## Why`', descriptions)
        self.assertTrue(descriptions.rstrip().endswith('metric tables.'))
        self.assertTrue(
            pick(doc, 'after:Descriptions/1').startswith('After the sections')
        )
        babysit = pick(doc, 'lead:Babysit')
        self.assertNotIn('A subagent that opens a PR', babysit)
        self.assertTrue(
            pick(doc, 'after:Babysit/1').startswith('A subagent that opens a PR')
        )

    def test_a_fence_after_a_lead_is_its_own_part(self):
        doc = playbooks.parse('### T\n\n**Lead.** Text.\n\n```\ncode\n```\n\nMore.\n')
        self.assertEqual(pick(doc, 'lead:Lead'), '**Lead.** Text.')
        self.assertEqual(pick(doc, 'after:Lead/1'), '```\ncode\n```')
        self.assertEqual(pick(doc, 'after:Lead/2'), 'More.')

    def test_a_reply_under_a_heading_is_its_own_part(self):
        doc = load('reply-in-section.md')
        self.assertEqual(pick(doc, 'reply'), '**Reply:** the closing reply.')
        section = pick(doc, 'section:Last')
        self.assertIn('Closing text.', section)
        self.assertNotIn('Reply', section)

    def test_a_block_after_a_reply_inside_a_section_is_unsupported(self):
        doc = playbooks.parse('### T\n\n#### S\n\n**Reply:** r.\n\nStray.\n')
        self.assertEqual(
            [(a.selector, a.kind) for a in doc.atoms if a.kind],
            [('block after the reply', 'after reply')],
        )

    def test_blocks_before_the_title_are_preamble_parts(self):
        doc = load('pstack-tdd.md')
        self.assertTrue(
            pick(doc, 'preamble:1').startswith('Read [the runtime adapter]')
        )
        self.assertTrue(pick(doc, 'intro:1').startswith('When fixing a bug'))

    def test_a_single_sentence_bold_opening_is_an_intro_not_a_lead(self):
        names = {a.selector for a in load('bug-fix.md').atoms}
        self.assertFalse([n for n in names if n.startswith('lead:')])
        self.assertIn('intro:1', names)

    def test_steps_of_a_section_are_addressed_by_the_section_name(self):
        doc = load('pstack-tdd.md')
        self.assertTrue(
            pick(doc, 'step:Workflow/3').startswith('3. **Write the failing test')
        )
        self.assertTrue(pick(doc, 'step:Workflow/6').startswith('6. **Rerun'))
        self.assertTrue(pick(doc, 'intro:1').startswith('When fixing a bug'))

    def test_a_section_is_selected_whole_by_its_heading(self):
        doc = load('pstack-tdd.md')
        guardrails = pick(doc, 'section:Guardrails')
        self.assertTrue(guardrails.startswith('## Guardrails'))
        self.assertIn('Do not change tests merely', guardrails)
        self.assertNotIn('Final Response', guardrails)
        self.assertTrue(pick(doc, 'section:Final Response').endswith('used instead.'))

    def test_a_section_of_only_steps_is_selectable_whole_too(self):
        workflow = pick(load('pstack-tdd.md'), 'section:Workflow')
        self.assertTrue(workflow.startswith('## Workflow'))
        self.assertIn('6. **Rerun', workflow)

    def test_numbers_in_nested_lists_and_fences_are_not_steps(self):
        doc = load('nested.md')
        steps = [a.selector for a in doc.atoms if a.selector.startswith('step:')]
        self.assertEqual(steps, ['step:1', 'step:2', 'step:3'])
        first = pick(doc, 'step:1')
        self.assertIn('a nested number, not a step', first)
        self.assertIn('a number in a fence, not a step', first)
        self.assertNotIn('Second step', first)

    def test_a_blank_line_inside_an_item_does_not_end_it(self):
        second = pick(load('nested.md'), 'step:2')
        self.assertIn('Its indented continuation.', second)
        self.assertNotIn('Third step', second)

    def test_heading_paths_are_hierarchical(self):
        doc = load('headings.md')
        self.assertTrue(pick(doc, 'heading:Rust/Errors').startswith('### Errors'))
        self.assertIn('Rust error rules.', pick(doc, 'heading:Rust/Errors'))
        self.assertNotIn('Rust testing', pick(doc, 'heading:Rust/Errors'))
        web = pick(doc, 'heading:Web/Errors')
        self.assertIn('Web boundary rules.', web)
        self.assertEqual(
            pick(doc, 'heading:Web/Errors/Boundaries'),
            '#### Boundaries\n\nWeb boundary rules.',
        )

    def test_a_heading_shared_by_two_branches_is_ambiguous(self):
        with self.assertRaisesRegex(playbooks.PlaybookError, 'ambiguous'):
            playbooks.select(load('headings.md'), 'heading:Errors')

    def test_a_selector_that_matches_nothing_or_is_malformed_is_refused(self):
        doc = load('bug-fix.md')
        for selector in ('step:9', 'lead:Nope', 'tail:1', 'heading:Nope', 'bogus:1'):
            with self.assertRaises(playbooks.PlaybookError, msg=selector):
                playbooks.select(doc, selector)
        for selector in ('step:x', 'reply:1', 'step:'):
            with self.assertRaises(playbooks.PlaybookError, msg=selector):
                playbooks.select(doc, selector)

    def test_a_second_numbered_list_is_an_unsupported_shape(self):
        doc = playbooks.parse('### T\n\n1. a\n2. b\n\nText.\n\n1. c\n')
        self.assertEqual(
            [(a.selector, a.kind) for a in doc.atoms if a.kind],
            [('numbered list', 'second list')],
        )


class DigestTests(unittest.TestCase):
    def test_whitespace_edits_keep_the_digest(self):
        base = 'one two\n\nthree\n'
        edited = '\none two   \n\n\n\nthree  \n\n'
        self.assertEqual(playbooks.digest(base), playbooks.digest(edited))
        self.assertEqual(len(playbooks.digest(base)), 12)

    def test_a_word_edit_changes_the_digest(self):
        self.assertNotEqual(
            playbooks.digest('one two\n\nthree'), playbooks.digest('one too\n\nthree')
        )

    def test_indentation_is_part_of_the_text(self):
        self.assertNotEqual(playbooks.digest('a\n  b'), playbooks.digest('a\nb'))


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        (self.base / 'repo').mkdir()
        (self.base / 'repo/doc.md').write_text('# Doc\n')
        (self.base / 'outside.md').write_text('# Out\n')
        (self.base / 'repo/link.md').symlink_to(self.base / 'outside.md')
        (self.base / 'repo/inner.md').symlink_to(self.base / 'repo/doc.md')
        self.roots = {'repo': self.base / 'repo'}

    def resolve(self, path, root='repo'):
        return playbooks.resolve_source(self.roots, root, path)

    def test_a_relative_path_inside_the_root_resolves(self):
        self.assertEqual(self.resolve('doc.md'), self.base / 'repo/doc.md')

    def test_a_symlink_that_stays_inside_the_root_resolves(self):
        self.assertEqual(self.resolve('inner.md'), self.base / 'repo/doc.md')

    def test_traversal_an_absolute_path_and_an_escaping_link_are_rejected(self):
        for path in ('../outside.md', 'a/../../outside.md', '/etc/passwd', 'link.md'):
            with self.assertRaises(playbooks.PlaybookError, msg=path):
                self.resolve(path)

    def test_an_unknown_root_and_a_missing_file_are_rejected(self):
        with self.assertRaisesRegex(playbooks.PlaybookError, 'unknown source root'):
            self.resolve('doc.md', root='host')
        with self.assertRaisesRegex(playbooks.PlaybookError, 'not a file'):
            self.resolve('nope.md')

    def test_the_skills_root_admits_a_skill_directory_that_is_a_link(self):
        installed = self.base / 'installed'
        (installed).mkdir()
        store = self.base / 'store/skill'
        store.mkdir(parents=True)
        (store / 'doc.md').write_text('# Doc\n')
        (installed / 'skill').symlink_to(store)
        roots = {'skills': installed, 'repo': installed}
        self.assertEqual(
            playbooks.resolve_source(roots, 'skills', 'skill/doc.md'), store / 'doc.md'
        )
        with self.assertRaises(playbooks.PlaybookError):
            playbooks.resolve_source(roots, 'repo', 'skill/doc.md')

    def test_the_skills_root_is_the_directory_holding_this_skill(self):
        skills = playbooks.default_roots()['skills']
        self.assertTrue((skills / 'pstack-kitchen/scripts/playbooks.py').is_file())
        self.assertTrue(
            playbooks.resolve_source(
                playbooks.default_roots(), 'skills', 'poteto-mode/playbooks/bug-fix.md'
            ).is_file()
        )


SOURCE = """### Small

**You own it. Do it well.** Go.

1. First.
2. Second.

A tail paragraph.

**Reply:** what happened.
"""


class MapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.roots = {'skills': Path(tmp.name)}
        (self.roots['skills'] / 'small.md').write_text(SOURCE)
        self.doc = playbooks.parse(SOURCE)

    def part(self, pid, selector, **extra):
        reviewed = playbooks.digest(pick(self.doc, selector))
        return {
            'id': pid,
            'select': selector,
            'owners': ['sidekick'],
            'reviewed': reviewed,
            **extra,
        }

    def sound_parts(self, without=()):
        selectors = {
            'small.intro': 'intro:1',
            'small.step-1': 'step:1',
            'small.step-2': 'step:2',
            'small.tail': 'tail:1',
            'small.reply': 'reply',
        }
        return [
            self.part(pid, selector)
            for pid, selector in selectors.items()
            if pid not in without
        ]

    def problems(self, parts=None, **entry):
        entry = {
            'source': {'root': 'skills', 'path': 'small.md'},
            'part': self.sound_parts() if parts is None else parts,
            **entry,
        }
        return playbooks.validate_map({'playbook': {'small': entry}}, self.roots)

    def test_a_map_that_covers_its_source_has_no_problems(self):
        self.assertEqual(self.problems(), [])

    def test_an_unknown_owner_is_a_problem(self):
        parts = self.sound_parts()
        parts[0]['owners'] = ['sidekick', 'chef']
        self.assertEqual(
            self.problems(parts), ["small: part small.intro: unknown owner 'chef'"]
        )

    def test_kitchen_text_needs_an_agent_role_that_owns_the_part(self):
        parts = self.sound_parts()
        parts[0]['kitchen'] = {'verifier': 'x', 'kitchen': 'y'}
        self.assertEqual(
            self.problems(parts),
            [
                'small: part small.intro: kitchen text for verifier, which is not an owner',
                "small: part small.intro: kitchen text for 'kitchen', which is not a role",
            ],
        )

    def test_an_unknown_source_root_or_a_missing_file_is_a_problem(self):
        for source, text in (
            ({'root': 'host', 'path': 'small.md'}, "unknown source root 'host'"),
            ({'root': 'skills', 'path': 'gone.md'}, 'is not a file'),
            ({'root': 'skills', 'path': '../small.md'}, 'must be relative'),
        ):
            problems = self.problems(source=source)
            self.assertEqual(len(problems), 1, source)
            self.assertIn(text, problems[0])

    def test_a_repeated_part_id_is_a_problem(self):
        parts = self.sound_parts()
        parts[1]['id'] = 'small.intro'
        self.assertEqual(self.problems(parts), ['small: duplicate part id small.intro'])

    def test_a_selector_used_twice_is_a_problem(self):
        parts = self.sound_parts() + [self.part('small.again', 'step:01')]
        problems = self.problems(parts)
        self.assertIn('small: duplicate selector step:01', problems)

    def test_overlapping_selections_are_a_problem(self):
        parts = self.sound_parts() + [self.part('small.all', 'heading:Small')]
        problems = self.problems(parts)
        self.assertTrue(
            any(
                p.startswith('small: overlapping selections: part small.intro')
                for p in problems
            )
        )

    def test_a_block_neither_selected_nor_excluded_is_a_problem(self):
        parts = self.sound_parts(without=['small.tail'])
        self.assertEqual(
            self.problems(parts),
            ['small: tail:1 (line 8) is neither selected nor excluded'],
        )

    def test_an_exclusion_with_a_reason_covers_a_block(self):
        parts = self.sound_parts(without=['small.tail'])
        self.assertEqual(
            self.problems(parts, exclude=[{'select': 'tail:1', 'reason': 'prose'}]), []
        )

    def test_an_exclusion_needs_a_reason(self):
        parts = self.sound_parts(without=['small.tail'])
        self.assertEqual(
            self.problems(parts, exclude=[{'select': 'tail:1'}]),
            ['small: exclude tail:1 has no reason'],
        )

    def test_a_shape_no_selector_addresses_is_a_problem(self):
        source = SOURCE.replace(
            'A tail paragraph.', 'A tail paragraph.\n\n1. Again.\n2. Another.'
        )
        (self.roots['skills'] / 'small.md').write_text(source)
        self.doc = playbooks.parse(source)
        self.assertEqual(
            self.problems(self.sound_parts()),
            ['small: unsupported shape at line 10: second list'],
        )

    def test_a_changed_word_in_the_source_is_a_digest_mismatch(self):
        (self.roots['skills'] / 'small.md').write_text(
            SOURCE.replace('Second', 'Third')
        )
        problems = self.problems()
        self.assertEqual(len(problems), 1)
        self.assertRegex(
            problems[0],
            r'small: part small.step-2: digest mismatch \(step:2\): reviewed \w{12}, current \w{12}',
        )

    def test_a_selector_that_matches_nothing_is_a_problem(self):
        parts = self.sound_parts()
        parts[0]['select'] = 'step:7'
        self.assertIn(
            'small: part small.intro: step:7: no such part', self.problems(parts)
        )


if __name__ == '__main__':
    unittest.main()
