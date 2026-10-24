import fnmatch
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
fixtures = root / 'tests/fixtures/playbooks'
spec = importlib.util.spec_from_file_location(
    'playbooks', root / 'skills/pstack-kitchen/scripts/playbooks.py'
)
assert spec and spec.loader
playbooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(playbooks)


def playbooks_loader():
    fresh = importlib.util.spec_from_file_location(
        'playbooks', root / 'skills/pstack-kitchen/scripts/playbooks.py'
    )
    assert fresh and fresh.loader
    module = importlib.util.module_from_spec(fresh)
    fresh.loader.exec_module(module)
    return module


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


FIXTURE_REPO = fixtures / 'repo'
BAD = fixtures / 'bad'
WORKFLOWS = sorted(playbooks.load_map(playbooks.MAP_PATH)['playbook'])


def overlay_roots(**extra):
    return {
        'repo': FIXTURE_REPO,
        'skills': playbooks.default_roots()['skills'],
        **extra,
    }


def bad_problems(name):
    (overlay,) = [o for o in playbooks.load_overlays(BAD) if o.stem == name]
    return playbooks.overlay_problems(overlay, overlay_roots(), WORKFLOWS)


class OverlayLoadingTests(unittest.TestCase):
    def test_the_fixture_overlays_load_and_are_sound(self):
        overlays = playbooks.load_overlays(FIXTURE_REPO / '.agents/playbooks')
        self.assertEqual([o.stem for o in overlays], ['daily-coaching', 'rust-engine'])
        for o in overlays:
            self.assertEqual(
                playbooks.overlay_problems(o, overlay_roots(), WORKFLOWS), [], o.stem
            )

    def test_a_narrative_file_beside_an_overlay_is_ignored(self):
        directory = FIXTURE_REPO / '.agents/playbooks'
        self.assertTrue((directory / 'rust-engine.md').is_file())
        names = [o.stem for o in playbooks.load_overlays(directory)]
        self.assertEqual(names.count('rust-engine'), 1)

    def test_a_missing_directory_has_no_overlays(self):
        self.assertEqual(playbooks.load_overlays(fixtures / 'nowhere'), [])

    def test_a_file_that_is_not_toml_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'broken.toml').write_text('id = = 1\n')
            (overlay,) = playbooks.load_overlays(Path(tmp))
            problems = playbooks.overlay_problems(overlay, overlay_roots(), WORKFLOWS)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith('overlay broken: '))

    def test_the_index_is_sorted_and_lists_parts_with_owners_source_and_digest(self):
        index = playbooks.overlay_index(
            playbooks.load_overlays(FIXTURE_REPO / '.agents/playbooks')
        )
        self.assertEqual([o['id'] for o in index], ['daily-coaching', 'rust-engine'])
        rust = index[1]
        self.assertEqual(rust['applies'], ['engine/**'])
        self.assertEqual(rust['combines'], ['bug-fix', 'feature', 'refactoring'])
        self.assertEqual(
            rust['parts'][0],
            {
                'id': 'error-model',
                'owners': ['sidekick', 'verifier'],
                'source': {'root': 'repo', 'path': 'CODING_STANDARDS.md'},
                'select': 'heading:Rust (Engine)/Errors',
                'digest': '6b7c36b4f3ca',
            },
        )
        self.assertEqual(index[0]['applies'], [])


class OverlayProblemTests(unittest.TestCase):
    def assertOneProblem(self, name, fragment):
        problems = bad_problems(name)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(fragment, problems[0])

    def test_a_wrong_schema(self):
        self.assertOneProblem('bad-schema', 'schema must be 1')

    def test_an_id_that_is_not_the_file_stem(self):
        self.assertOneProblem('id-mismatch', "id 'other-id' must equal the file name")

    def test_a_duplicate_part_id(self):
        self.assertOneProblem('duplicate-part', 'duplicate part id error-model')

    def test_an_unknown_owner(self):
        self.assertOneProblem('unknown-owner', "unknown owner 'janitor'")

    def test_a_combines_name_the_map_does_not_define(self):
        self.assertOneProblem('unknown-combines', "names 'cooking'")

    def test_an_escaping_source_path(self):
        self.assertOneProblem('escaping-source', 'must be relative inside repo')

    def test_a_missing_source_file(self):
        self.assertOneProblem('broken-source', 'repo:NOPE.md is not a file')

    def test_a_heading_two_sections_share(self):
        self.assertOneProblem('ambiguous-heading', 'ambiguous, 2 parts match')

    def test_a_digest_that_no_longer_matches(self):
        self.assertOneProblem('digest-mismatch', 'digest mismatch')

    def test_a_kind_outside_the_four(self):
        self.assertOneProblem('bad-kind', 'kind must be one of')

    def test_a_host_source_without_a_host_directory_is_a_broken_source(self):
        (overlay,) = playbooks.load_overlays(FIXTURE_REPO / '.agents/playbooks')[:1]
        data = json.loads(json.dumps(overlay.data))
        data['part'][0]['source']['root'] = 'host'
        problems = playbooks.overlay_problems(
            playbooks.Overlay(overlay.stem, data), overlay_roots(), WORKFLOWS
        )
        self.assertTrue(any('unknown source root' in p for p in problems), problems)

    def test_an_unknown_key_is_a_problem(self):
        (overlay,) = playbooks.load_overlays(FIXTURE_REPO / '.agents/playbooks')[:1]
        data = {**overlay.data, 'apply': ['x']}
        problems = playbooks.overlay_problems(
            playbooks.Overlay(overlay.stem, data), overlay_roots(), WORKFLOWS
        )
        self.assertEqual(problems, ["overlay daily-coaching: unknown key 'apply'"])


class OverlayRepo(unittest.TestCase):
    """A throwaway git repo that carries the fixture overlays."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / 'repo'
        shutil.copytree(FIXTURE_REPO, self.repo)
        (self.repo / 'engine').mkdir()
        (self.repo / 'engine/lib.rs').write_text('fn main() {}\n')
        self.env = {
            **os.environ,
            'XDG_STATE_HOME': str(Path(tmp.name) / 'state'),
            'PSTACK_KITCHEN_ROSTER': str(Path(tmp.name) / 'no-roster.toml'),
            'GIT_AUTHOR_NAME': 't',
            'GIT_AUTHOR_EMAIL': 't@t',
            'GIT_COMMITTER_NAME': 't',
            'GIT_COMMITTER_EMAIL': 't@t',
        }
        for args in (['init', '-q'], ['add', '-A'], ['commit', '-q', '-m', 'init']):
            subprocess.run(['git', *args], cwd=self.repo, env=self.env, check=True)

    def kitchen(self, *args):
        return subprocess.run(
            [sys.executable, str(KITCHEN.parent / 'kitchen.py'), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )


class OverlayKitchenTests(OverlayRepo):
    """validate and doctor against that repo."""

    def test_validate_passes_with_sound_overlays(self):
        done = self.kitchen('validate')
        self.assertEqual((done.returncode, done.stderr), (0, ''))

    def test_validate_reports_an_overlay_problem_with_the_kitchen(self):
        shutil.copy(BAD / 'bad-schema.toml', self.repo / '.agents/playbooks')
        done = self.kitchen('validate')
        self.assertEqual(done.returncode, 1)
        self.assertIn('kitchen: overlay bad-schema: schema must be 1', done.stderr)

    def doctor_check(self):
        done = self.kitchen('--json', 'doctor')
        (check,) = [
            c for c in json.loads(done.stdout)['checks'] if c['check'] == 'playbooks'
        ]
        return check

    def test_doctor_warns_on_an_applies_glob_that_matches_no_tracked_file(self):
        self.assertEqual(self.doctor_check()['status'], 'ok')
        (self.repo / 'engine/lib.rs').unlink()
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, env=self.env, check=True)
        check = self.doctor_check()
        self.assertEqual(check['status'], 'warn')
        self.assertEqual(
            check['items'], ['rust-engine: applies engine/** matches no tracked file']
        )

    def test_doctor_fails_on_an_overlay_problem(self):
        shutil.copy(BAD / 'bad-schema.toml', self.repo / '.agents/playbooks')
        check = self.doctor_check()
        self.assertEqual(check['status'], 'fail')
        self.assertIn('overlay bad-schema: schema must be 1', check['items'])


class OverlayCommandTests(OverlayRepo):
    def test_index_lists_both_fixture_overlays_with_their_parts(self):
        done = subprocess.run(
            [
                sys.executable,
                str(KITCHEN),
                '--repo',
                str(self.repo),
                'playbooks',
                'index',
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        self.assertIn('rust-engine (language): Rust engine', done.stdout)
        self.assertIn('daily-coaching (domain): Daily coaching', done.stdout)
        self.assertIn('part error-model [sidekick, verifier]', done.stdout)

    def test_index_json_is_the_derived_list(self):
        done = subprocess.run(
            [
                sys.executable,
                str(KITCHEN),
                '--json',
                '--repo',
                str(self.repo),
                'playbooks',
                'index',
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        ids = [o['id'] for o in json.loads(done.stdout)['overlays']]
        self.assertEqual(ids, ['daily-coaching', 'rust-engine'])

    def test_validate_counts_the_overlays_of_a_repo(self):
        done = subprocess.run(
            [
                sys.executable,
                str(KITCHEN),
                '--repo',
                str(self.repo),
                'playbooks',
                'validate',
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        self.assertIn('2 overlays, ok', done.stdout)

    def test_validate_fails_naming_each_bad_overlay(self):
        shutil.copy(BAD / 'digest-mismatch.toml', self.repo / '.agents/playbooks')
        done = subprocess.run(
            [
                sys.executable,
                str(KITCHEN),
                '--repo',
                str(self.repo),
                'playbooks',
                'validate',
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            'kitchen: overlay digest-mismatch: part p: digest mismatch', done.stderr
        )


def fixture_overlays(*extra):
    return [*playbooks.load_overlays(FIXTURE_REPO / '.agents/playbooks'), *extra]


def clone(stem, **changes):
    (rust,) = [o for o in fixture_overlays() if o.stem == 'rust-engine']
    data = json.loads(json.dumps(rust.data))
    data['id'] = stem
    data.update(changes)
    return playbooks.Overlay(stem, data)


def glob_match(glob, path):
    return fnmatch.fnmatch(path, glob)


def compose(workflow='bug-fix', files=('engine/src/lib.rs',), overlays=None, **kw):
    return playbooks.compose(
        playbooks.load_map(playbooks.MAP_PATH),
        fixture_overlays() if overlays is None else overlays,
        overlay_roots(),
        workflow,
        list(files),
        glob_match,
        **kw,
    )


def ids(result, role):
    return [e['id'] for e in result['roles'][role]]


class RenderRoleTests(unittest.TestCase):
    """What a role is handed: authority, the whole inventory, then texts within the target."""

    def composition(self, **texts):
        parts = [
            {'id': pid, 'text': text, 'source': {}, 'from': []}
            for pid, text in texts.items()
        ]
        return {
            'workflow': 'feature',
            'overlays': {'selected': [{'id': 'coach', 'why': '', 'kind': 'domain'}]},
            'roles': {'sidekick': parts, 'master': [], 'verifier': parts},
        }

    def test_under_the_target_every_text_is_inline_in_composition_order(self):
        comp = self.composition(zeta='Zeta line.\nmore', alpha='Alpha line.')
        text, pending = playbooks.render_role(comp, 'sidekick', 1500)
        self.assertEqual(pending, [])
        self.assertNotIn('Pending reads', text)
        self.assertTrue(
            text.startswith('## Playbook: feature + coach (your parts)\n\nYou own')
        )
        self.assertLess(text.index('### zeta'), text.index('### alpha'))
        self.assertIn('- zeta: Zeta line.\n- alpha: Alpha line.\n', text)
        self.assertIn('Zeta line.\nmore', text)

    def test_a_small_target_lists_later_parts_as_pending_reads_with_a_whole_inventory(
        self,
    ):
        comp = self.composition(
            first='intro\n' + 'a' * 100,
            second='intro\n' + 'b' * 100,
            third='intro\n' + 'c' * 100,
        )
        none_fit = playbooks.render_role(comp, 'sidekick', 0)[0]
        head = len(none_fit.split('\nPending reads')[0])
        text, pending = playbooks.render_role(
            comp, 'sidekick', (head + 150) // 4, store='/s', unit='010'
        )
        self.assertEqual(pending, ['second', 'third'])
        self.assertIn('### first', text)
        self.assertNotIn('### second', text)
        self.assertNotIn('b' * 100, text)
        for pid in ('first', 'second', 'third'):
            self.assertIn(f'- {pid}: ', text)
        self.assertIn(
            '- second: kitchen.sh playbook-part /s 010 sidekick second\n', text
        )
        self.assertIn('- third: kitchen.sh playbook-part /s 010 sidekick third\n', text)

    def test_a_part_after_an_overflow_stays_pending_even_when_it_is_small(self):
        comp = self.composition(big='x' * 4000, tiny='ok')
        _, pending = playbooks.render_role(comp, 'sidekick', 100)
        self.assertEqual(pending, ['big', 'tiny'])

    def test_a_verifiers_parts_are_always_whole(self):
        comp = self.composition(proof='p' * 4000)
        text, pending = playbooks.render_role(comp, 'verifier', 10)
        self.assertEqual(pending, [])
        self.assertIn('p' * 4000, text)

    def test_a_role_with_no_parts_gets_no_section(self):
        self.assertEqual(
            playbooks.render_role(self.composition(a='A'), 'master', 1500), ('', [])
        )

    def test_headings_in_a_part_never_start_a_brief_section(self):
        comp = self.composition(a='## Verify\nbody\n```sh\n## kept\n```\n# Top')
        text, _ = playbooks.render_role(comp, 'sidekick', 1500)
        self.assertEqual(
            [x for x in text.split('\n') if x.startswith('## ')],
            ['## Playbook: feature + coach (your parts)'],
        )
        self.assertIn('#### Verify', text)
        self.assertIn('#### kept', text)
        self.assertIn('#### Top', text)


class ComposeTests(unittest.TestCase):
    def test_an_overlay_whose_applies_matches_the_scope_is_selected_by_path(self):
        r = compose()
        self.assertEqual(
            r['overlays']['selected'],
            [
                {
                    'id': 'rust-engine',
                    'why': 'applies engine/** matches engine/src/lib.rs',
                    'kind': 'language',
                }
            ],
        )
        self.assertEqual(r['overlays']['excluded'], [])

    def test_the_workflow_parts_come_first_then_overlay_parts_in_id_order(self):
        r = compose(with_ids=('daily-coaching',))
        sidekick = ids(r, 'sidekick')
        mapped = [i for i in sidekick if i.startswith('bug-fix.')]
        self.assertEqual(sidekick[: len(mapped)], mapped)
        self.assertEqual(
            sidekick[len(mapped) :],
            ['session-shape', 'tone', 'error-model', 'testing'],
        )
        self.assertEqual(
            [o['id'] for o in r['overlays']['selected']],
            ['daily-coaching', 'rust-engine'],
        )

    def test_with_adds_an_overlay_no_path_matches(self):
        r = compose(files=('docs/readme.md',), with_ids=('daily-coaching',))
        self.assertEqual(
            r['overlays']['selected'],
            [{'id': 'daily-coaching', 'why': 'with:', 'kind': 'domain'}],
        )

    def test_an_overlay_with_empty_applies_never_matches_by_path(self):
        r = compose(files=('anything/at/all.rs',))
        self.assertEqual(r['overlays']['selected'], [])

    def test_each_role_gets_only_the_parts_it_owns_with_its_own_text(self):
        r = compose()
        self.assertIn('error-model', ids(r, 'verifier'))
        self.assertNotIn('error-model', ids(r, 'master'))
        verifier = next(e for e in r['roles']['verifier'] if e['id'] == 'error-model')
        sidekick = next(e for e in r['roles']['sidekick'] if e['id'] == 'error-model')
        self.assertTrue(verifier['text'].startswith('Check error paths against this'))
        self.assertIn('Return a typed error', sidekick['text'])
        self.assertEqual(
            sidekick['source'],
            {
                'root': 'repo',
                'path': 'CODING_STANDARDS.md',
                'select': 'heading:Rust (Engine)/Errors',
                'digest': '6b7c36b4f3ca',
            },
        )
        self.assertEqual(
            sidekick['from'], [{'part': 'error-model', 'by': 'overlay:rust-engine'}]
        )

    def test_without_with_a_reason_drops_only_that_overlay(self):
        r = compose(
            with_ids=('daily-coaching',),
            without=(('rust-engine', 'this change is a doc edit'),),
        )
        self.assertEqual(
            [o['id'] for o in r['overlays']['selected']], ['daily-coaching']
        )
        self.assertEqual(
            r['overlays']['excluded'],
            [{'id': 'rust-engine', 'reason': 'without: this change is a doc edit'}],
        )
        self.assertNotIn('error-model', ids(r, 'sidekick'))

    def test_without_needs_a_reason(self):
        for reason in ('', '   '):
            with self.assertRaisesRegex(
                playbooks.PlaybookError, 'without: rust-engine needs a reason'
            ):
                compose(without=(('rust-engine', reason),))

    def test_naming_an_overlay_the_repo_lacks_is_an_error(self):
        with self.assertRaisesRegex(playbooks.PlaybookError, "no overlay 'ghost'"):
            compose(with_ids=('ghost',))
        with self.assertRaisesRegex(playbooks.PlaybookError, "no overlay 'ghost'"):
            compose(without=(('ghost', 'why'),))

    def test_with_and_without_of_one_overlay_is_an_error(self):
        with self.assertRaisesRegex(playbooks.PlaybookError, 'both name rust-engine'):
            compose(with_ids=('rust-engine',), without=(('rust-engine', 'no'),))

    def test_an_explicit_overlay_whose_combines_excludes_the_workflow_is_an_error(self):
        with self.assertRaisesRegex(
            playbooks.PlaybookError,
            'with: rust-engine combines only with bug-fix, feature, refactoring, not investigation',
        ):
            compose('investigation', with_ids=('rust-engine',))

    def test_an_automatic_overlay_that_does_not_combine_is_reported_and_skipped(self):
        r = compose('investigation')
        self.assertEqual(r['overlays']['selected'], [])
        self.assertEqual(
            r['overlays']['excluded'],
            [
                {
                    'id': 'rust-engine',
                    'reason': 'inapplicable: combines only with bug-fix, feature, refactoring, not investigation',
                }
            ],
        )
        self.assertNotIn('error-model', ids(r, 'sidekick'))

    def test_an_empty_combines_fits_any_workflow(self):
        r = compose('investigation', with_ids=('daily-coaching',))
        self.assertEqual(
            [o['id'] for o in r['overlays']['selected']], ['daily-coaching']
        )

    def test_one_selection_from_two_overlays_is_one_part_with_both_origins(self):
        twin = clone('rust-twin')
        r = compose(overlays=fixture_overlays(twin))
        self.assertEqual(ids(r, 'sidekick').count('error-model'), 1)
        entry = next(e for e in r['roles']['sidekick'] if e['id'] == 'error-model')
        self.assertEqual(
            [x['by'] for x in entry['from']],
            ['overlay:rust-engine', 'overlay:rust-twin'],
        )

    def test_the_same_text_under_two_part_ids_is_one_part_with_both_provenances(self):
        twin = clone('rust-twin')
        twin.data['part'][0]['id'] = 'errors-again'
        r = compose(overlays=fixture_overlays(twin))
        entry = next(e for e in r['roles']['sidekick'] if e['id'] == 'error-model')
        self.assertEqual(ids(r, 'sidekick').count('errors-again'), 0)
        self.assertEqual(
            entry['from'],
            [
                {'part': 'error-model', 'by': 'overlay:rust-engine'},
                {'part': 'errors-again', 'by': 'overlay:rust-twin'},
            ],
        )

    def test_one_part_id_with_two_definitions_is_an_error(self):
        other = clone('rust-twin')
        other.data['part'][0]['select'] = 'heading:Rust (Engine)/Testing'
        other.data['part'][0]['reviewed'] = '37cba2f76bee'
        with self.assertRaisesRegex(
            playbooks.PlaybookError,
            'part id error-model is defined differently by overlay:rust-engine and overlay:rust-twin',
        ):
            compose(overlays=fixture_overlays(other))

    def test_a_workflow_the_map_lacks_is_an_error_listing_the_mapped_ones(self):
        with self.assertRaises(playbooks.PlaybookError) as raised:
            compose('some-old-label')
        message = str(raised.exception)
        self.assertIn("playbook 'some-old-label' is not mapped; mapped: ", message)
        self.assertIn('bug-fix', message)
        self.assertIn('shipping', message)

    def test_a_selected_overlay_with_a_stale_digest_fails_composition(self):
        stale = clone('rust-engine')
        stale.data['part'][0]['reviewed'] = '000000000000'
        with self.assertRaisesRegex(playbooks.PlaybookError, 'digest mismatch'):
            compose(overlays=[stale])

    def test_an_unselected_overlay_is_not_read(self):
        stale = clone('rust-engine', applies=['elsewhere/**'])
        stale.data['part'][0]['reviewed'] = '000000000000'
        self.assertEqual(compose(overlays=[stale])['overlays']['selected'], [])

    def test_the_result_is_plain_data_that_survives_json(self):
        r = compose(with_ids=('daily-coaching',))
        self.assertEqual(json.loads(json.dumps(r)), r)
        self.assertEqual(
            sorted(r['roles']), ['consultant', 'master', 'sidekick', 'verifier']
        )


class ComposeQueryTests(OverlayRepo):
    """kitchen.compose reads the Scope against the repo's tracked files, new ones included."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(KITCHEN.parent))
        import kitchen

        cls.kitchen_py = kitchen

    def compose(self, paths, **kw):
        inv = self.kitchen_py.Invocation(str(self.repo))
        return self.kitchen_py.compose(inv, 'bug-fix', paths, **kw)

    def test_a_scoped_file_that_does_not_exist_yet_still_selects_an_overlay(self):
        r = self.compose(['engine/new/**'])
        self.assertEqual([o['id'] for o in r['overlays']['selected']], ['rust-engine'])

    def test_a_scope_outside_every_applies_selects_nothing(self):
        self.assertEqual(self.compose(['docs/**'])['overlays']['selected'], [])

    def test_errors_are_kitchen_errors(self):
        with self.assertRaises(self.kitchen_py.KitchenError):
            self.compose(['engine/**'], with_ids=('ghost',))


class HostOverlayTests(unittest.TestCase):
    """A host kitchen's overlays sit in `<kitchen-file-stem>.playbooks/` beside its file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.config = self.tmp / 'config'
        (self.config / 'kitchens').mkdir(parents=True)
        self.env = {
            **os.environ,
            'XDG_STATE_HOME': str(self.tmp / 'state'),
            'PSTACK_KITCHEN_ROSTER': str(self.config / 'kitchen.toml'),
            'GIT_AUTHOR_NAME': 't',
            'GIT_AUTHOR_EMAIL': 't@t',
            'GIT_COMMITTER_NAME': 't',
            'GIT_COMMITTER_EMAIL': 't@t',
        }
        self.entries = ''

    def repo(self, name, kitchen_file=None, own_kitchen=False):
        path = self.tmp / name
        shutil.copytree(FIXTURE_REPO, path)
        shutil.rmtree(path / '.agents')
        (path / '.agents').mkdir()
        if own_kitchen:
            (path / '.agents/kitchen.toml').write_text(
                'version = 1\n\n[profile.all]\npaths = ["**"]\nfast = ["true"]\n'
            )
        for args in (['init', '-q'], ['add', '-A'], ['commit', '-q', '-m', 'init']):
            subprocess.run(['git', *args], cwd=path, env=self.env, check=True)
        if kitchen_file:
            self.entries += f'[[kitchen]]\nmatch = "{path}"\nfile = "{kitchen_file}"\n'
            (self.config / 'kitchen.toml').write_text(self.entries)
            (self.config / 'kitchens' / kitchen_file).write_text(
                'version = 1\n\n[profile.all]\npaths = ["**"]\nfast = ["true"]\n'
            )
        return path

    def host_overlay(self, kitchen_file, stem):
        directory = self.config / 'kitchens' / f'{Path(kitchen_file).stem}.playbooks'
        directory.mkdir()
        shutil.copy(
            FIXTURE_REPO / '.agents/playbooks/daily-coaching.toml',
            directory / f'{stem}.toml',
        )
        data = (
            (directory / f'{stem}.toml')
            .read_text()
            .replace('id = "daily-coaching"', f'id = "{stem}"')
        )
        (directory / f'{stem}.toml').write_text(data)

    def index(self, repo, *flags):
        done = subprocess.run(
            [sys.executable, str(KITCHEN), '--json', *flags, 'playbooks', 'index'],
            cwd=repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return [o['id'] for o in json.loads(done.stdout)['overlays']]

    def test_two_host_kitchens_each_see_only_their_own_overlays(self):
        a = self.repo('a', 'a.toml')
        b = self.repo('b', 'b.toml')
        self.host_overlay('a.toml', 'alpha')
        self.host_overlay('b.toml', 'beta')
        self.assertEqual(self.index(a), ['alpha'])
        self.assertEqual(self.index(b), ['beta'])

    def test_a_host_kitchen_ignores_overlays_lying_in_the_repo(self):
        repo = self.repo('r', 'h.toml')
        shutil.copytree(FIXTURE_REPO / '.agents/playbooks', repo / '.agents/playbooks')
        self.host_overlay('h.toml', 'alpha')
        self.assertEqual(self.index(repo), ['alpha'])
        self.assertEqual(self.index(self.tmp, '--repo', str(repo)), ['alpha'])

    def test_a_repo_kitchen_never_reads_host_overlays(self):
        repo = self.repo('r', 'h.toml', own_kitchen=True)
        self.host_overlay('h.toml', 'alpha')
        self.assertEqual(self.index(repo), [])
        done = subprocess.run(
            [
                sys.executable,
                str(KITCHEN),
                '--repo',
                str(repo),
                '--json',
                'playbooks',
                'index',
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(done.stdout), {'overlays': []})


class UnusableMapTests(OverlayRepo):
    """A missing map is a validation problem, and only when overlays exist."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(KITCHEN.parent))
        import kitchen

        cls.kitchen_py = kitchen

    def broken_map(self):
        module = playbooks_loader()
        module.MAP_PATH = self.repo / 'no-such-map.toml'
        return mock.patch.object(self.kitchen_py, 'playbooks_module', lambda: module)

    def test_validate_reports_the_map_when_overlays_exist(self):
        inv = self.kitchen_py.Invocation(str(self.repo))
        with self.broken_map(), self.assertRaises(self.kitchen_py.KitchenError) as e:
            self.kitchen_py.validate(inv)
        self.assertIn('playbook map unusable', str(e.exception))

    def test_validate_does_not_read_the_map_without_overlays(self):
        shutil.rmtree(self.repo / '.agents/playbooks')
        inv = self.kitchen_py.Invocation(str(self.repo))
        with self.broken_map():
            self.assertTrue(self.kitchen_py.validate(inv).profiles)


if __name__ == '__main__':
    unittest.main()
