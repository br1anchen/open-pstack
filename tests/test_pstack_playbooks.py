import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
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

    def test_the_skills_root_rejects_a_top_level_file_link_that_escapes_it(self):
        skills = self.base / 'skills'
        skills.mkdir()
        (skills / 'leak.md').symlink_to(self.base / 'outside.md')
        with self.assertRaisesRegex(playbooks.PlaybookError, 'leaves the skills root'):
            playbooks.resolve_source({'skills': skills}, 'skills', 'leak.md')

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

    def test_a_kitchen_value_that_is_not_a_table_is_a_problem_not_a_crash(self):
        parts = self.sound_parts()
        parts[0]['kitchen'] = 'text'
        self.assertEqual(
            self.problems(parts),
            ['small: part small.intro: kitchen must be a table of role texts'],
        )

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


MAP = root / 'skills/pstack-kitchen/references/playbook-roles.toml'
DELEGATION = "delegate to a subagent on your roster model, within your role's limits"
EXPECTED = {
    'bug-fix',
    'feature',
    'refactoring',
    'pstack-tdd',
    'investigation',
    'perf-issue',
    'prototype',
    'runtime-forensics',
    'trace-forensics',
    'session-pickup',
    'pause-safely',
    'opening-a-pr',
    'shipping',
}


def shipped():
    with MAP.open('rb') as f:
        return tomllib.load(f)


def parts(data):
    return {
        part['id']: part
        for entry in data['playbook'].values()
        for part in entry['part']
    }


class ShippedMapTests(unittest.TestCase):
    def test_the_shipped_map_validates_against_the_real_sources(self):
        self.assertEqual(playbooks.validate_map(shipped()), [])

    def test_the_map_covers_the_thirteen_playbooks_kitchen_briefs_use(self):
        self.assertEqual(set(shipped()['playbook']), EXPECTED)

    def test_every_reply_is_owned_by_the_sidekick_and_the_verifier_with_kitchen_text(
        self,
    ):
        replies = [
            p
            for p in parts(shipped()).values()
            if p['select'] in ('reply', 'section:Final Response')
        ]
        self.assertEqual(len(replies), len(EXPECTED - {'opening-a-pr'}))
        for part in replies:
            self.assertTrue({'sidekick', 'verifier'} <= set(part['owners']), part['id'])
            self.assertTrue(
                {'sidekick', 'verifier'} <= set(part['kitchen']), part['id']
            )

    def test_bug_fix_overrides_where_upstream_conflicts_with_a_role(self):
        by_id = parts(shipped())
        for pid in ('intro-1', 'step-1', 'step-3'):
            kitchen = by_id[f'bug-fix.{pid}']['kitchen']
            self.assertTrue({'sidekick', 'verifier'} <= set(kitchen), pid)
        self.assertIn(
            'implement the fix', by_id['bug-fix.step-3']['kitchen']['sidekick']
        )

    def test_no_kitchen_text_names_a_model_slug_or_a_cursor_command(self):
        for pid, part in parts(shipped()).items():
            for role, text in part.get('kitchen', {}).items():
                for banned in (
                    'grok-',
                    '/loop',
                    '/deslop',
                    '/no-comments',
                    'Cursor',
                    '/unslop',
                ):
                    self.assertNotIn(banned, text, f'{pid} {role}')

    def test_delegation_always_carries_the_roster_model_and_role_limits(self):
        for pid, part in parts(shipped()).items():
            for role, text in part.get('kitchen', {}).items():
                if 'subagent' in text:
                    self.assertIn(DELEGATION, text, f'{pid} {role}')

    def test_a_no_pr_landing_mode_and_the_ask_the_human_replacement_survive(self):
        by_id = parts(shipped())
        for pid in ('bug-fix.step-6', 'shipping.step-4', 'opening-a-pr.intro-1'):
            self.assertIn('[landing].mode', by_id[pid]['kitchen']['master'], pid)
        self.assertIn('never merges', by_id['shipping.step-4']['kitchen']['master'])
        ask = by_id['bug-fix.step-1']['kitchen']
        self.assertIn('Questions', ask['sidekick'])
        self.assertIn('the master asks the human', ask['sidekick'])
        self.assertIn('you never ask the human', ask['verifier'])

    def test_a_part_the_kitchen_rewrites_is_pinned_to_the_upstream_text_it_replaces(
        self,
    ):
        part = parts(shipped())['bug-fix.step-1']
        source = (root / 'skills/poteto-mode/playbooks/bug-fix.md').read_text()
        self.assertEqual(
            part['reviewed'], playbooks.digest(pick(playbooks.parse(source), 'step:1'))
        )
        self.assertIn('ask the user', source)


class ShippedMapRoleTests(unittest.TestCase):
    def test_a_verifier_that_owns_an_instruction_to_edit_gets_a_check_only_text(self):
        by_id = parts(shipped())
        for pid in (
            'pstack-tdd.workflow-3',
            'pstack-tdd.workflow-4',
            'pstack-tdd.workflow-5',
            'pstack-tdd.impractical',
            'pstack-tdd.guardrails',
            'bug-fix.step-5',
        ):
            text = by_id[pid]['kitchen']['verifier']
            self.assertRegex(text, r'Do not (write|edit|restage)', pid)

    def test_commit_advice_never_rewrites_a_recorded_step(self):
        by_id = parts(shipped())
        for pid, role in (
            ('feature.step-6', 'sidekick'),
            ('refactoring.step-8', 'sidekick'),
            ('opening-a-pr.commits', 'sidekick'),
            ('opening-a-pr.commits', 'master'),
        ):
            self.assertIn(
                'Never rebase or amend commits already recorded as steps',
                by_id[pid]['kitchen'][role],
                f'{pid} {role}',
            )

    def test_opening_a_pr_says_the_landing_cap_once_and_keeps_the_forge_rules(self):
        by_id = parts(shipped())
        capped = [
            pid
            for pid, part in by_id.items()
            if pid.startswith('opening-a-pr.')
            and '[landing].mode' in part.get('kitchen', {}).get('master', '')
        ]
        self.assertEqual(capped, ['opening-a-pr.intro-1'])
        for pid in ('forge', 'built-in', 'stacks', 'readiness'):
            self.assertNotIn('kitchen', by_id[f'opening-a-pr.{pid}'], pid)

    def test_no_kitchen_text_names_a_skill_that_is_not_installed(self):
        installed = {p.parent.name for p in (root / 'skills').glob('*/SKILL.md')}
        for pid, part in parts(shipped()).items():
            for role, text in part.get('kitchen', {}).items():
                self.assertNotIn('deslop', text, f'{pid} {role}')
        self.assertTrue({'no-comments', 'unslop', 'technical-writing'} <= installed)

    def test_every_owner_of_the_investigation_reply_has_text_for_it(self):
        reply = parts(shipped())['investigation.reply']
        self.assertEqual(set(reply['owners']), set(reply['kitchen']))


KITCHEN = root / 'skills/pstack-kitchen/scripts/kitchen.py'


def kitchen(*args):
    return subprocess.run(
        [sys.executable, str(KITCHEN), 'playbooks', *args],
        capture_output=True,
        text=True,
    )


class PlaybooksCommandTests(unittest.TestCase):
    def edited_skills(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        skills = Path(tmp.name)
        for source in ('poteto-mode/playbooks', 'pstack-tdd'):
            shutil.copytree(root / 'skills' / source, skills / source)
        bug_fix = skills / 'poteto-mode/playbooks/bug-fix.md'
        bug_fix.write_text(
            bug_fix.read_text().replace(
                'Verify on the same surface.', 'Verify on the same device.'
            )
        )
        return skills

    def test_validate_passes_on_the_shipped_map(self):
        done = kitchen('validate')
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        self.assertIn('13 playbooks', done.stdout)

    def test_validate_fails_naming_the_part_when_a_source_word_changes(self):
        done = kitchen('--skills', str(self.edited_skills()), 'validate')
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, '')
        lines = done.stderr.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(
            'kitchen: bug-fix: part bug-fix.step-4: digest mismatch (step:4)', lines[0]
        )

    def test_review_names_the_changed_part_its_digests_and_its_current_text(self):
        done = kitchen('--skills', str(self.edited_skills()), 'review', 'bug-fix')
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        self.assertIn('1 changed part(s)', done.stdout)
        self.assertIn('bug-fix.step-4 (step:4)', done.stdout)
        self.assertIn('Verify on the same device.', done.stdout)
        expected = playbooks.digest(pick(load('bug-fix.md'), 'step:4'))
        self.assertIn(f'expected {expected}', done.stdout)
        self.assertRegex(done.stdout, r'current  \w{12}')

    def test_review_names_the_commit_that_last_changed_the_map(self):
        done = kitchen('--skills', str(self.edited_skills()), 'review', 'bug-fix')
        commit = playbooks.last_map_commit(playbooks.MAP_PATH)
        self.assertIn(
            f'the map was last changed in {commit or "no commit"}', done.stdout
        )

    def test_the_commits_text_has_no_repeated_sentence_and_the_master_does_not_step(
        self,
    ):
        parts = {
            p['id']: p
            for p in playbooks.load_map(playbooks.MAP_PATH)['playbook']['opening-a-pr'][
                'part'
            ]
        }
        texts = parts['opening-a-pr.commits']['kitchen']
        for role, text in texts.items():
            sentences = [x.strip() for x in text.split('. ') if x.strip()]
            self.assertEqual(len(sentences), len(set(sentences)), role)
        self.assertNotIn('kitchen.sh step', texts['master'])
        self.assertIn('kitchen.sh step', texts['sidekick'])

    def test_review_of_an_unchanged_playbook_says_so(self):
        done = kitchen('review', 'bug-fix')
        self.assertEqual(done.returncode, 0)
        self.assertIn('every part still matches', done.stdout)

    def test_review_of_a_playbook_the_map_lacks_is_an_error(self):
        done = kitchen('review', 'nope')
        self.assertEqual(done.returncode, 2)
        self.assertIn(
            "kitchen: playbooks map or name not usable: no playbook 'nope' in the map",
            done.stderr,
        )

    def test_review_json_lists_the_changed_parts(self):
        done = subprocess.run(
            [
                sys.executable,
                str(KITCHEN),
                '--json',
                'playbooks',
                '--skills',
                str(self.edited_skills()),
                'review',
                'bug-fix',
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(done.stdout)
        self.assertEqual([c['id'] for c in data['changed']], ['bug-fix.step-4'])
        self.assertEqual(data['playbook'], 'bug-fix')


if __name__ == '__main__':
    unittest.main()
