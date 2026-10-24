import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
fixtures = root / 'tests/fixtures/token-audit'
script = root / 'scripts/pstack-token-audit.py'


def run_audit(*extra, projects=fixtures / 'projects', store=fixtures / 'store'):
    return subprocess.run(
        [sys.executable, str(script), '--projects', str(projects), *extra, str(store)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def audit():
    return json.loads(run_audit('--json'))[0]


class TokenAuditTests(unittest.TestCase):
    def test_a_message_written_as_three_records_is_billed_once(self):
        sidekick = audit()['roles']['sidekick']
        self.assertEqual(sidekick['messages'], 6)
        self.assertEqual(
            sidekick['billed'],
            {
                'input_tokens': 30,
                'cache_creation_input_tokens': 1500,
                'cache_read_input_tokens': 6350,
                'output_tokens': 210,
            },
        )

    def test_roles_come_from_the_verifier_id_or_the_first_prompt(self):
        by_session = {s['session'][:8]: s for s in audit()['sessions']}
        self.assertEqual(
            {k: (v['role'], v['how']) for k, v in by_session.items()},
            {
                'aaaaaaaa': ('sidekick', 'prompt'),
                'bbbbbbbb': ('master', 'prompt'),
                'cccccccc': ('verifier', 'id'),
            },
        )

    def test_a_session_that_starts_before_the_span_and_runs_into_it_is_not_counted(
        self,
    ):
        result = audit()
        self.assertIn('hhhhhhhh', {s['session'][:8] for s in result['ambiguous']})
        self.assertEqual(result['roles']['sidekick']['sessions'], 1)

    def test_the_final_usage_of_a_message_wins_over_a_partial_first_record(self):
        # msg_2 is two records: output_tokens 5, then 30
        self.assertEqual(audit()['roles']['sidekick']['billed']['output_tokens'], 210)

    def test_a_skill_the_skill_tool_injects_and_a_document_cat_through_bash_are_loads(
        self,
    ):
        documents = audit()['roles']['master']['documents']
        self.assertEqual(documents['pstack-kitchen/SKILL.md']['chars'], 1013)
        self.assertEqual(
            documents['pstack-kitchen/references/master.md'],
            {'loads': 1, 'chars': 400, 'tokens': 100, 'truncated': 0},
        )
        # injected 253 tokens x 2 later messages + cat 100 x 1
        self.assertEqual(audit()['roles']['master']['exposure_tokens'], 606)

    def test_a_verifier_whose_prompt_names_no_role_is_assigned_by_its_session_id(self):
        verifier = audit()['roles']['verifier']
        self.assertEqual(verifier['sessions'], 1)
        self.assertEqual(verifier['billed']['output_tokens'], 50)

    def test_sessions_outside_the_span_or_naming_no_role_are_listed_not_counted(self):
        result = audit()
        self.assertEqual(
            {a['session'][:8]: a['reason'] for a in result['ambiguous']},
            {
                'dddddddd': 'first record outside the store span',
                'eeeeeeee': 'prompt names no store',
                'hhhhhhhh': 'first record outside the store span',
            },
        )
        self.assertEqual(sum(r['sessions'] for r in result['roles'].values()), 3)
        self.assertEqual(result['other_stores'], 1)

    def test_documents_are_counted_by_file_and_playbooks_on_their_own(self):
        sidekick = audit()['roles']['sidekick']
        self.assertEqual(
            sidekick['documents']['poteto-mode/SKILL.md'],
            {'loads': 2, 'chars': 800, 'tokens': 200, 'truncated': 0},
        )
        self.assertEqual(
            sidekick['playbooks'],
            {
                'poteto-mode/playbooks/feature.md': {
                    'loads': 1,
                    'chars': 1200,
                    'tokens': 300,
                    'truncated': 0,
                }
            },
        )
        self.assertNotIn('poteto-mode/playbooks/feature.md', sidekick['documents'])

    def test_a_truncated_tool_result_is_counted_at_its_delivered_size_and_flagged(self):
        sidekick = audit()['roles']['sidekick']
        template = sidekick['documents']['pstack-pair/references/report-template.md']
        self.assertEqual(template['truncated'], 1)
        self.assertEqual(template['chars'], 601)
        self.assertEqual(sidekick['truncated_loads'], 1)

    def test_exposure_counts_each_load_over_the_assistant_messages_after_it(self):
        # poteto SKILL.md 100 tokens x 5 messages, kitchen SKILL.md 200 x 5, feature.md 300 x 3,
        # report-template.md 150 x 2, poteto SKILL.md again 100 x 1
        self.assertEqual(audit()['roles']['sidekick']['exposure_tokens'], 2800)

    def test_roles_whose_kind_is_not_claude_are_unmeasured(self):
        result = audit()
        self.assertEqual(
            result['unmeasured'], [{'role': 'consultant', 'kind': 'codex'}]
        )
        self.assertIn('unmeasured: consultant (codex)', run_audit().splitlines())

    def test_the_table_names_the_three_measures_apart(self):
        text = run_audit()
        self.assertIn('billed (provider usage, each message once): input 30', text)
        self.assertIn('documents loaded (est. tokens = chars/4): 550', text)
        self.assertIn('playbooks loaded (est. tokens = chars/4): 300', text)
        self.assertIn(
            'exposure (estimate): ~2800 token-messages = 35.5% of input', text
        )

    def test_a_line_a_live_session_is_still_writing_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory) / 'projects'
            shutil.copytree(fixtures / 'projects', projects)
            live = next(projects.glob('*/aaaaaaaa-*.jsonl'))
            with live.open('a') as f:
                f.write('{"type": "assistant", "message": {"id": "msg_7", "usa')
            result = json.loads(run_audit('--json', projects=projects))[0]
        self.assertEqual(result['roles']['sidekick'], audit()['roles']['sidekick'])

    def test_a_verifier_scratch_outside_the_repo_path_is_found_by_the_store_slug(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory) / 'projects'
            shutil.copytree(fixtures / 'projects', projects)
            [verifier] = projects.glob('*scratch*/cccccccc-*.jsonl')
            elsewhere = (
                projects / '-other-git-pstack-scratch-runs-fixture-1-002-x-verify'
            )
            elsewhere.mkdir()
            record = json.loads(verifier.read_text().splitlines()[0])
            record['message']['content'] = (
                'take the verifier role. pstack-kitchen VERIFY /state/pstack/kitchen/runs/fixture-1/verdicts/002-x-v1'
            )
            (elsewhere / 'gggggggg-0000-4000-8000-000000000007.jsonl').write_text(
                json.dumps(record) + '\n'
            )
            result = json.loads(run_audit('--json', projects=projects))[0]
        self.assertEqual(result['roles']['verifier']['sessions'], 2)
        self.assertIn(
            {
                'session': 'gggggggg-0000-4000-8000-000000000007',
                'role': 'verifier',
                'how': 'prompt',
            },
            result['sessions'],
        )

    def test_the_audit_writes_nothing_in_the_store(self):
        store = fixtures / 'store'
        before = {p.name: p.read_bytes() for p in store.iterdir()}
        run_audit()
        self.assertEqual({p.name: p.read_bytes() for p in store.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
