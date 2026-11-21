"""kitchen.py against a throwaway repo: the kitchen.toml schema, classify,
gate, policy, and doctor, end to end through the command line."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import tomllib
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

# git and the scripts report resolved paths; on macOS the temp dir is under
# /var, a link to /private/var, so tests work in the resolved one.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())

root = Path(__file__).resolve().parents[1]
skill = root / 'skills/pstack-kitchen'
script = skill / 'scripts/kitchen.py'
sys.path.insert(0, str(script.parent))
import kitchen

sys.path.insert(0, str(root / 'tests'))
sys.path.insert(0, str(root / 'skills/pstack-pair/scripts'))
from pstack_core import agents  # noqa: E402
from pstack_core.store import Context  # noqa: E402

BARE_CTX = Context('pstack-kitchen', '/x', '/s', 'Kitchen', (), (), 20, '')
from test_pstack_core import LEAKED_PREFIXES, event_log, watch_jq  # noqa: E402


def inherited_env():
    """The test runner's environment without what a kitchen or pair run exports to its agents
    (PSTACK_KITCHEN_ROLE and the like) and without the XDG data, config and cache homes, which would send HOME-based
    lookups to the host's real directories, so the result does not depend on who runs the suite."""
    return {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            'PSTACK_PLACEMENT',
            'XDG_DATA_HOME',
            'XDG_CONFIG_HOME',
            'XDG_CACHE_HOME',
        )
        and not k.startswith(LEAKED_PREFIXES)
    }


KITCHEN = textwrap.dedent("""\
    version = 1

    [profile.app]
    paths = ["src/**", "tests/**"]
    fast = ["test -f src/app.py"]
    behavioral = ["true"]
    tests = ["tests/**"]
    require_tests = true
    sample = 0.2

    [profile.docs]
    paths = ["docs/**", "**/*.md"]
    fast = ["test -d docs"]
    verify = "gates"
    sample = 0.0

    [profile.contracts]
    paths = ["contracts/**"]
    fast = ["true"]
    verify = "unit"

    [escalate]
    paths = ["contracts/**"]
    manifests = ["requirements.txt"]
    max_profiles = 1
    max_diff_lines = 50

    [[policy.forbid]]
    id = "no-print"
    pattern = '\\bprint\\('
    paths = ["src/**"]
    message = "use the logger"
    source = "AGENTS.md#logging"

    [coverage]
    ignore = [".agents/**", "requirements.txt"]
    """)

FILES = {
    'src/app.py': 'def main():\n    return 1\n',
    'src/util.py': 'X = 1\n',
    'tests/test_app.py': 'def test_main():\n    assert True\n',
    'docs/guide.md': '# Guide\n',
    'contracts/api.json': '{}\n',
    'README.md': '# Demo\n',
    'requirements.txt': 'requests\n',
}


# git starts auto-maintenance in the background after a commit or a fetch; it can still write into .git/objects when a test's temporary directory
# is removed, and the removal then fails with "Directory not empty".
os.environ.update(
    {
        'GIT_CONFIG_COUNT': '2',
        'GIT_CONFIG_KEY_0': 'maintenance.auto',
        'GIT_CONFIG_VALUE_0': 'false',
        'GIT_CONFIG_KEY_1': 'gc.auto',
        'GIT_CONFIG_VALUE_1': '0',
    }
)


class KitchenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.env = {
            **inherited_env(),
            'XDG_STATE_HOME': str(Path(self.tmp.name) / 'state'),
            'PSTACK_KITCHEN_SLOT_POLL_S': '0.2',
            'PSTACK_KITCHEN_ROSTER': str(Path(self.tmp.name) / 'no-roster.toml'),
            'GIT_AUTHOR_NAME': 't',
            'GIT_AUTHOR_EMAIL': 't@t',
            'GIT_COMMITTER_NAME': 't',
            'GIT_COMMITTER_EMAIL': 't@t',
        }
        self.repo.mkdir()
        self.git('init', '-q')
        for path, text in {**FILES, '.agents/kitchen.toml': KITCHEN}.items():
            self.write(path, text)
        self.base = self.commit('init')

    def git(self, *args):
        return subprocess.run(
            ['git', *args],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()

    def write(self, path, text):
        f = self.repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def doctor_check(self, name):
        return {c['check']: c for c in self.data('doctor', code=None)['checks']}[name]

    def run_kitchen(self, *args, code=0):
        result = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=self.repo,
            env=self.env,
            timeout=60,
            capture_output=True,
            text=True,
            check=False,
        )
        if code is not None:
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.stdout = result.stdout
        return result.stdout + result.stderr

    def data(self, *args, code=0):
        self.run_kitchen('--json', *args, code=code)
        return json.loads(self.stdout)

    def change(self, files):
        for path, text in files.items():
            self.write(path, text)
        return self.commit('change')

    def classify(self, files):
        head = self.change(files)
        return self.data('classify', '--base', self.base, '--head', head)


class GlobTests(unittest.TestCase):
    def test_globs_cross_directories_only_with_double_star(self):
        cases = [
            ('*.md', 'README.md', True),
            ('*.md', 'docs/a.md', False),
            ('**/*.md', 'README.md', True),
            ('**/*.md', 'docs/x/a.md', True),
            ('src/**', 'src/a/b.py', True),
            ('src/**', 'srcx/a.py', False),
            ('a/**/b.py', 'a/b.py', True),
            ('a/**/b.py', 'a/x/y/b.py', True),
            ('crates/*/Cargo.toml', 'crates/core/Cargo.toml', True),
            ('crates/*/Cargo.toml', 'crates/a/b/Cargo.toml', False),
            ('?.py', 'a.py', True),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(
                    bool(kitchen.glob_regex(pattern).match(path)), expected
                )


class SchemaTests(KitchenTests):
    def test_reference_example_is_valid(self):
        k = kitchen.parse(
            tomllib.loads((skill / 'references/kitchen-example.toml').read_text())
        )
        self.assertEqual(list(k.profiles), ['ui', 'engine', 'docs'])
        self.assertEqual((k.review['self'], k.review['legacy']), (['no-comments'], []))
        self.assertEqual(k.scratch_setup, ['pnpm install --frozen-lockfile'])

    def test_validate_names_the_bad_key(self):
        cases = [
            ('fast =', 'fsat =', 'profile.app: unknown key fsat'),
            ('tests = ["tests/**"]\n', '', 'require_tests needs tests globs'),
            ("pattern = '\\bprint\\('", "pattern = '('", 'policy.forbid[0].pattern'),
            (
                'verify = "unit"',
                'verify = "sometimes"',
                'profile.contracts.verify: expected one of gates, batch, unit',
            ),
            ('version = 1', 'version = 2', 'version: expected 1'),
        ]
        for old, new, message in cases:
            with self.subTest(new=new):
                self.assertIn(old, KITCHEN)
                self.write('.agents/kitchen.toml', KITCHEN.replace(old, new, 1))
                self.assertIn(message, self.run_kitchen('validate', code=1))
        self.write(
            '.agents/kitchen.toml',
            KITCHEN
            + '\n[[policy.forbid]]\nid = "no-print"\npattern = "x"\nmessage = "m"\n',
        )
        self.assertIn('policy.forbid[1].id', self.run_kitchen('validate', code=1))

    def test_validate_rejects_a_playbooks_target_that_is_not_a_positive_integer(self):
        for value in ('0', '-5', '"big"', '1.5', 'true'):
            with self.subTest(value=value):
                self.write(
                    '.agents/kitchen.toml',
                    KITCHEN + f'\n[playbooks]\ntarget_tokens = {value}\n',
                )
                self.assertIn(
                    'playbooks.target_tokens: expected an integer >= 1',
                    self.run_kitchen('validate', code=1),
                )
        self.write('.agents/kitchen.toml', KITCHEN + '\n[playbooks]\ntarget = 5\n')
        self.assertIn(
            'playbooks: unknown key target', self.run_kitchen('validate', code=1)
        )

    def test_the_playbooks_target_defaults_to_1500_and_reads_the_table(self):
        base = tomllib.loads(KITCHEN)
        self.assertEqual(kitchen.parse(base).playbook_target_tokens, 1500)
        base['playbooks'] = {'target_tokens': 400}
        self.assertEqual(kitchen.parse(base).playbook_target_tokens, 400)

    def test_heavy_takes_true_false_or_a_list_of_stages(self):
        for value, stages in [
            ('true', {'fast', 'behavioral', 'landing'}),
            ('false', set()),
            ('["behavioral"]', {'behavioral'}),
            ('["fast", "landing"]', {'fast', 'landing'}),
        ]:
            with self.subTest(value):
                self.write(
                    '.agents/kitchen.toml',
                    KITCHEN.replace('sample = 0.2', f'sample = 0.2\nheavy = {value}'),
                )
                k, _ = kitchen.load(self.repo, None, None)
                self.assertEqual(k.profiles['app'].heavy, stages)

    def test_an_unknown_heavy_stage_is_refused_naming_the_key(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = ["build"]'),
        )
        self.assertIn(
            'profile.app.heavy: expected true, false, or a list of stages from fast, behavioral, landing',
            self.run_kitchen('validate', code=1),
        )

    GENERATED = '\n[[generated]]\nsources = ["src/**"]\noutputs = ["docs/**"]\ncommand = "make gen"\n'

    def test_a_generated_entry_parses_and_validates(self):
        self.write('.agents/kitchen.toml', KITCHEN + self.GENERATED)
        k, _ = kitchen.load(self.repo, None, None)
        (g,) = k.generated
        self.assertEqual(
            (g.sources, g.outputs, g.command), (('src/**',), ('docs/**',), 'make gen')
        )
        self.run_kitchen('validate')

    def test_a_bad_generated_entry_is_refused_naming_it(self):
        for text, message in [
            (
                '[[generated]]\nsources = ["src/**"]\n',
                'generated[0].outputs: list at least one glob',
            ),
            (
                '[[generated]]\nsources = []\noutputs = ["a"]\n',
                'generated[0].sources: list at least one glob',
            ),
            (
                '[[generated]]\nsources = ["a"]\noutputs = ["b"]\nwhen = "x"\n',
                'generated[0]: unknown key when',
            ),
        ]:
            with self.subTest(message):
                self.write('.agents/kitchen.toml', KITCHEN + '\n' + text)
                self.assertIn(message, self.run_kitchen('validate', code=1))

    def test_missing_kitchen_points_at_setup(self):
        (self.repo / '.agents/kitchen.toml').unlink()
        self.assertIn('pstack-kitchen-setup', self.run_kitchen('validate', code=1))


class ClassifyTests(KitchenTests):
    def with_generated(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN
            + '\n[[generated]]\nsources = ["src/**"]\noutputs = ["docs/**"]\ncommand = "make gen"\n',
        )

    def test_a_scope_covering_a_source_but_none_of_its_outputs_warns(self):
        self.with_generated()
        r = self.data('classify', '--paths', 'src/**')
        self.assertEqual(
            r['warnings'],
            [
                'Scope covers src/app.py but none of its generated outputs (docs/**); '
                'add them to may write, or say why not (regenerate: make gen)'
            ],
        )
        self.assertIn(
            'warn: Scope covers src/app.py',
            self.run_kitchen('classify', '--paths', 'src/**'),
        )

    def test_classify_returns_the_touched_profiles_gate_commands(self):
        self.assertEqual(
            self.data('classify', '--paths', 'src/**')['gates'],
            ['test -f src/app.py', 'true'],
        )
        self.assertEqual(
            self.data('classify', '--paths', 'docs/**')['gates'], ['test -d docs']
        )

    def test_a_scope_with_the_outputs_or_without_the_source_does_not_warn(self):
        self.with_generated()
        self.assertEqual(
            self.data('classify', '--paths', 'src/**', 'docs/**')['warnings'], []
        )
        self.assertEqual(self.data('classify', '--paths', 'tests/**')['warnings'], [])

    def test_a_warning_changes_neither_the_risk_nor_the_exit_code_and_no_entry_means_no_key(
        self,
    ):
        plain = self.data('classify', '--paths', 'src/**')
        self.assertNotIn('warnings', plain)
        self.with_generated()
        warned = self.data('classify', '--paths', 'src/**')
        self.assertEqual({k: v for k, v in warned.items() if k != 'warnings'}, plain)

    def test_routine_change_takes_its_profile_settings(self):
        r = self.classify(
            {
                'src/util.py': 'X = 2\n',
                'tests/test_app.py': 'def test_main():\n    assert 1\n',
            }
        )
        self.assertEqual(
            (r['risk'], r['profiles'], r['escalate']), ('routine', ['app'], [])
        )
        self.assertEqual(r['verify'], {'mode': 'batch', 'kind': 'routine'})
        self.assertEqual(r['sample'], 0.2)
        self.assertNotIn('review', r)

    def test_docs_only_change_needs_no_verifier(self):
        r = self.classify({'docs/guide.md': '# Guide\n\nMore.\n'})
        self.assertEqual(
            (r['risk'], r['verify']['mode'], r['sample']), ('routine', 'gates', 0.0)
        )

    def test_each_escalation_reason(self):
        cases = [
            ({'contracts/api.json': '{"a": 1}\n'}, 'escalate path: contracts/api.json'),
            (
                {'requirements.txt': 'requests\nhttpx\n'},
                'dependency manifest: requirements.txt',
            ),
            (
                {'src/util.py': 'X = 3\n', 'docs/guide.md': '# G\n'},
                '2 profiles touched: app, docs (max 1)',
            ),
            ({'src/big.py': 'x = 1\n' * 60}, '60 diff lines (max 50)'),
            ({'tools/gen.py': 'pass\n'}, '1 files no profile covers: tools/gen.py'),
        ]
        for files, reason in cases:
            with self.subTest(reason=reason):
                r = self.classify(files)
                self.assertEqual(r['risk'], 'escalated')
                self.assertIn(reason, r['escalate'])
                self.assertEqual(r['verify'], {'mode': 'unit', 'kind': 'escalated'})
                self.assertEqual(r['sample'], 1.0)
                self.base = self.git('rev-parse', 'HEAD')

    def test_scope_globs_expand_against_tracked_files(self):
        r = self.data('classify', '--paths', 'src/**', 'src/new.py')
        self.assertEqual(
            [f['path'] for f in r['files']], ['src/app.py', 'src/util.py', 'src/new.py']
        )
        self.assertEqual(r['profiles'], ['app'])

    def test_scope_glob_for_a_new_directory_maps_to_its_profile(self):
        r = self.data('classify', '--paths', 'src/newpkg/**', 'contracts/v2/*.json')
        self.assertEqual(
            [f['path'] for f in r['files']], ['src/newpkg/x', 'contracts/v2/x.json']
        )
        self.assertEqual(
            (r['profiles'], r['risk']), (['app', 'contracts'], 'escalated')
        )
        self.assertEqual((r['behavioral'], r['max_batch_diff']), (['app'], 800))

    def test_scope_outside_the_repo_is_bookkeeping(self):
        r = self.data(
            'classify',
            '--paths',
            '/elsewhere/store/reports/007-x.md',
            f'{self.repo}/src/util.py',
        )
        self.assertEqual(
            ([f['path'] for f in r['files']], r['outside'], r['risk']),
            (['src/util.py'], ['/elsewhere/store/reports/007-x.md'], 'routine'),
        )
        r = self.data('classify', '--paths', '/elsewhere/store/reports/007-x.md')
        self.assertEqual(
            (r['risk'], r['bookkeeping'], r['escalate'], r['verify']['mode']),
            ('routine', True, [], 'gates'),
        )

    def test_statedir_is_shared_by_worktrees_and_keeps_the_old_ledger(self):
        main = self.run_kitchen('statedir').strip()
        wt = Path(self.tmp.name) / 'wt'
        self.git('worktree', 'add', '-q', '--detach', str(wt), 'HEAD')
        self.assertEqual(self.run_kitchen('--repo', str(wt), 'statedir').strip(), main)
        # A ledger under the older per-checkout key moves to the shared one.
        import hashlib

        old = (
            Path(self.tmp.name)
            / 'state/pstack/kitchen/repos'
            / f'wt-{hashlib.sha256(str(wt).encode()).hexdigest()[:8]}'
        )
        old.mkdir(parents=True)
        (old / 'ledger.tsv').write_text('ts\trun\tkind\tclass\tdetail\n')
        Path(main, 'ledger.tsv').unlink(missing_ok=True)
        self.run_kitchen('--repo', str(wt), 'statedir')
        self.assertTrue(Path(main, 'ledger.tsv').exists())

    def test_each_bare_repository_gets_its_own_statedir_and_keeps_the_shared_history(
        self,
    ):
        import hashlib

        t = Path(self.tmp.name)
        parent = t / 'bare'
        state = t / 'state/pstack/kitchen/repos'
        shared = state / f'bare-{hashlib.sha256(str(parent).encode()).hexdigest()[:8]}'
        shared.mkdir(parents=True)
        (shared / 'timings.tsv').write_text('ts\tprofile\n1\tweb\n')
        dirs = []
        for name in ('a.git', 'b.git'):
            git_dir = parent / name
            subprocess.run(
                ['git', 'clone', '-q', '--bare', str(self.repo), str(git_dir)],
                check=True,
                timeout=60,
            )
            wt = parent / f'{name[0]}-main'
            subprocess.run(
                [
                    'git',
                    '-C',
                    str(git_dir),
                    'worktree',
                    'add',
                    '-q',
                    '--detach',
                    str(wt),
                ],
                check=True,
                timeout=60,
            )
            dirs.append(self.run_kitchen('--repo', str(wt), 'statedir').strip())
            self.assertEqual(
                Path(dirs[-1]).name,
                f'{name}-{hashlib.sha256(str(git_dir).encode()).hexdigest()[:8]}',
            )
            self.assertEqual(
                Path(dirs[-1], 'timings.tsv').read_text(), 'ts\tprofile\n1\tweb\n'
            )
        self.assertNotEqual(dirs[0], dirs[1])

    def test_roster_and_statedir(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n'
            '[sidekick.fallback]\nkind = "devin"\n[consultant]\nkind = "codex"\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        r = self.data('roster')
        self.assertEqual(
            r['sidekick'],
            {
                'kind': 'pi',
                'args': ['--model', 'devin/swe-2'],
                'fallback': {'kind': 'devin', 'args': []},
                'fallbacks': [{'kind': 'devin', 'args': []}],
                'alternate': [],
            },
        )
        self.assertEqual(r['consultant']['kind'], 'codex')
        self.assertIn('pstack/kitchen/repos/repo-', self.run_kitchen('statedir'))

    def test_roster_alternate_entries_print_by_kind_and_exclude_a_sidekick_kind(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text(
            '[[sidekick.alternate]]\nkind = "amp"\nargs = ["--model", "m/a"]\n'
            '[[sidekick.alternate]]\nkind = "amp"\nargs = ["--model", "m/b"]\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        self.assertEqual(
            self.data('roster')['sidekick']['alternate'],
            [
                {'kind': 'amp', 'args': ['--model', 'm/a']},
                {'kind': 'amp', 'args': ['--model', 'm/b']},
            ],
        )
        self.assertIn('sidekick alternate amp/amp', self.run_kitchen('roster'))
        roster.write_text(
            '[sidekick]\nkind = "pi"\n[[sidekick.alternate]]\nkind = "amp"\n'
        )
        out = self.run_kitchen('roster', code=1)
        self.assertIn('[sidekick] sets a kind and [[sidekick.alternate]] entries', out)

    def test_roster_verifier_defaults(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text('[sidekick]\nkind = "pi"\n')
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        v = self.data('roster')['verifier']
        self.assertEqual(
            v['routine'],
            {
                'kind': 'sidekick',
                'args': [],
                'fallback': None,
                'fallbacks': [],
                'source': 'sidekick',
            },
        )
        self.assertEqual(
            v['escalated'],
            {
                'kind': 'claude',
                'args': ['--model', 'claude-opus-5-5'],
                'fallback': None,
                'fallbacks': [],
                'source': 'default',
            },
        )

    def test_roster_verifier_entries_and_master_sentinel(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text(
            '[verifier]\nkind = "pi"\nargs = ["--model", "m"]\n'
            '[verifier.fallback]\nkind = "claude"\nargs = ["--model", "s"]\n'
            '[verifier.escalated]\nkind = "master"\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        v = self.data('roster')['verifier']
        self.assertEqual(
            v['routine'],
            {
                'kind': 'pi',
                'args': ['--model', 'm'],
                'fallback': {'kind': 'claude', 'args': ['--model', 's']},
                'fallbacks': [{'kind': 'claude', 'args': ['--model', 's']}],
                'source': 'roster',
            },
        )
        self.assertEqual(
            v['escalated'],
            {
                'kind': 'master',
                'args': [],
                'fallback': None,
                'fallbacks': [],
                'source': 'roster',
            },
        )

    def test_a_role_names_a_fallback_chain_in_order(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "amp"\nargs = ["--model", "a/one"]\n'
            '[[sidekick.fallback]]\nkind = "amp"\nargs = ["--model", "b/two"]\n'
            '[[sidekick.fallback]]\nkind = "amp"\nargs = ["--model", "c/three"]\n'
            '[master]\nkind = "amp"\n[[master.fallback]]\nkind = "claude"\n'
            '[[verifier.escalated.fallback]]\nkind = "codex"\n'
            '[verifier.escalated]\nkind = "claude"\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        r = self.data('roster')
        chain = [
            {'kind': 'amp', 'args': ['--model', 'b/two']},
            {'kind': 'amp', 'args': ['--model', 'c/three']},
        ]
        self.assertEqual(r['sidekick']['fallbacks'], chain)
        self.assertEqual(r['sidekick']['fallback'], chain[0])
        self.assertEqual(r['master']['fallbacks'], [{'kind': 'claude', 'args': []}])
        self.assertEqual(
            r['verifier']['escalated']['fallbacks'], [{'kind': 'codex', 'args': []}]
        )
        roster.write_text(
            '[sidekick]\nkind = "amp"\n[[sidekick.fallback]]\nargs = []\n'
        )
        out = self.run_kitchen('roster', code=1)
        self.assertIn('[[sidekick.fallback]] entries each need a kind', out)

    def test_repo_verify_kinds_warn_only_when_set(self):
        self.assertNotIn('moved to the host roster', self.run_kitchen('validate'))
        self.write(
            '.agents/kitchen.toml', KITCHEN + '\n[verify]\nescalated_kind = "master"\n'
        )
        self.assertIn('moved to the host roster', self.run_kitchen('validate'))
        self.assertTrue(self.data('validate')['ok'])

    def test_working_tree_counts_untracked_files(self):
        self.write('docs/new.md', 'a\nb\n')
        self.write('docs/guide.md', '# Guide\nmore\n')
        r = self.data('classify', '--working-tree')
        self.assertEqual(
            sorted(f['path'] for f in r['files']), ['docs/guide.md', 'docs/new.md']
        )
        self.assertEqual(r['diff_lines'], 3)

    def test_at_reads_the_kitchen_of_that_revision(self):
        self.change(
            {
                '.agents/kitchen.toml': KITCHEN.replace(
                    'max_diff_lines = 50', 'max_diff_lines = 5'
                )
            }
        )
        head = self.change(
            {
                'src/util.py': 'X = 1\nY = 2\nZ = 3\nW = 4\nV = 5\nU = 6\n',
                'tests/test_app.py': 'def test_main():\n    assert 2\n',
            }
        )
        now = self.data('classify', '--base', f'{head}~1', '--head', head)
        then = self.data(
            '--at', self.base, 'classify', '--base', f'{head}~1', '--head', head
        )
        self.assertEqual((now['risk'], then['risk']), ('escalated', 'routine'))


class HistoryTests(KitchenTests):
    def test_history_classifies_each_commit_with_its_findings(self):
        self.change({'src/util.py': 'print(1)\n', 'tests/test_app.py': 'x = 2\n'})
        self.change({'contracts/api.json': '{"b": 2}\n'})
        rows = self.data('history', '--last', '5')['commits']
        self.assertEqual(
            [(r['risk'], r['profiles'], r['findings']) for r in rows],
            [('escalated', ['contracts'], []), ('routine', ['app'], ['no-print'])],
        )
        self.assertIn(
            '1 of 2 escalated; 1 with policy findings', self.run_kitchen('history')
        )


class GateTests(KitchenTests):
    def test_passing_gate_and_logs(self):
        r = self.data('gate', 'app', 'fast')
        self.assertTrue(r['passed'])
        self.assertTrue(Path(r['commands'][0]['log']).exists())
        self.assertEqual(
            self.data('gate', 'app', 'landing'),
            {
                'profile': 'app',
                'stage': 'landing',
                'role': 'sidekick',
                'passed': True,
                'commands': [],
                'waited': 0.0,
            },
        )

    def test_failing_gate_stops_prints_the_log_tail_and_exits_2(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                'fast = ["echo boom; exit 3", "echo second"]',
            ),
        )
        out = self.run_kitchen('gate', 'app', 'fast', code=2)
        self.assertIn('app fast: FAIL', out)
        self.assertIn('exit 3', out)
        self.assertIn('| boom', out)
        self.assertNotIn('echo second', out)
        self.assertEqual(
            [
                c['exit']
                for c in self.data('gate', 'app', 'fast', '--keep-going', code=2)[
                    'commands'
                ]
            ],
            [3, 0],
        )
        only = self.data('gate', 'app', 'fast', '--only', '1')['commands']
        self.assertEqual(
            [(c['command'], c['log'].endswith('app-fast-1.log')) for c in only],
            [('echo second', True)],
        )
        self.assertIn(
            '--only 2: app.fast has 2 commands',
            self.run_kitchen('gate', 'app', 'fast', '--only', '2', code=1),
        )

    def test_gate_and_brief_verify_give_their_commands_the_base_and_a_tmpdir_without_writing_os_environ(
        self,
    ):
        from unittest import mock

        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                'fast = ["echo $PSTACK_KITCHEN_STEP_BASE $TMPDIR"]',
            ),
        )
        brief = self.repo / 'brief.md'
        brief.write_text(
            '## Verify\n\n```bash\necho $PSTACK_KITCHEN_STEP_BASE $TMPDIR\n```\n'
        )
        k, _ = kitchen.load(self.repo, None, None)
        inv = kitchen.Invocation(str(self.repo))
        env = {**self.env, 'PSTACK_KITCHEN_GATE_SLOTS': '1'}
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ.pop(kitchen.WRAPPED, None)
            snapshot = dict(os.environ)
            runs = [
                kitchen.run_gate(
                    inv, self.repo, k, 'app', 'fast', None, base=self.base
                ),
                kitchen.run_brief_verify(
                    inv, self.repo, k, brief, None, base=self.base
                ),
            ]
            self.assertEqual(dict(os.environ), snapshot)
        for r in runs:
            (command,) = r['commands']
            base, tmp = Path(command['log']).read_text().split()
            self.assertEqual(
                (base, Path(tmp).name.startswith('kg-')), (self.base, True), r
            )

    def test_heavy_gate_releases_its_slot(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = true'),
        )
        self.data('gate', 'app', 'fast')
        self.data('gate', 'app', 'fast')
        locks = list(
            (Path(self.tmp.name) / 'state/pstack/kitchen/repos').glob('heavy-*.lock')
        )
        self.assertEqual([p.name for p in locks], ['heavy-0.lock'])

    def slot_order(self, toml):
        """The slot kinds a gate takes, in order, with the machine gate slot held (or not) as each is asked for."""
        from unittest import mock

        self.write('.agents/kitchen.toml', toml)
        real, taken = kitchen.acquire_slot, []

        def spy(lock_dir, prefix, slots, on_wait=None):
            held = [f.name for f in lock_dir.glob('gate-*.lock') if self.is_locked(f)]
            taken.append((prefix, held))
            return real(lock_dir, prefix, slots, on_wait)

        k, _ = kitchen.load(self.repo, None, None)
        env = {**self.env, 'PSTACK_KITCHEN_GATE_SLOTS': '1'}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(kitchen, 'acquire_slot', spy),
            mock.patch.object(kitchen, 'machine_gate_slots', lambda: 1),
        ):
            os.environ.pop(kitchen.WRAPPED, None)
            kitchen.run_gate(
                kitchen.Invocation(str(self.repo)), self.repo, k, 'app', 'fast', None
            )
        return taken

    @staticmethod
    def is_locked(path):
        import fcntl

        with open(path) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def test_a_heavy_gate_takes_the_heavy_slot_before_a_machine_gate_slot(self):
        taken = self.slot_order(
            KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = true')
        )
        self.assertEqual(taken, [('heavy', []), ('gate', [])])

    def test_a_gate_waiting_for_the_heavy_slot_says_where_it_stands(self):
        import contextlib
        import io
        from unittest import mock

        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = true'),
        )
        k, _ = kitchen.load(self.repo, None, None)
        env = {**self.env}
        with mock.patch.dict(os.environ, env, clear=True):
            lock_dir = kitchen.state_dir(self.repo).parent
            holder, _ = kitchen.acquire_slot(lock_dir, 'heavy', 1)
            real = kitchen.acquire_slot

            def release_when_queued(directory, prefix, slots, on_wait=None):
                def wait(pos, n):
                    assert on_wait is not None
                    on_wait(pos, n)
                    holder.close()

                return real(directory, prefix, slots, wait)

            err = io.StringIO()
            with (
                mock.patch.object(kitchen, 'acquire_slot', release_when_queued),
                contextlib.redirect_stderr(err),
            ):
                slot, _ = kitchen.heavy_slot(self.repo, k)
            slot.close()
        self.assertIn('kitchen: queued 1 of 1 for 1 heavy slot\n', err.getvalue())

    def test_a_fast_gate_of_a_behavioral_only_heavy_profile_takes_no_heavy_slot(self):
        toml = KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = ["behavioral"]')
        self.assertEqual(self.slot_order(toml), [('gate', [])])

    def test_a_gate_that_is_not_heavy_takes_only_a_machine_gate_slot(self):
        self.assertEqual(self.slot_order(KITCHEN), [('gate', [])])

    def timing_rows(self):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        return [
            line.split('\t')
            for line in (state_dir / 'timings.tsv').read_text().splitlines()
        ]

    def test_a_gate_row_has_nine_columns_with_the_heavy_wait_and_the_wall_time(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace('fast = ["test -f src/app.py"]', 'fast = ["true", "true"]'),
        )
        self.data('gate', 'app', 'fast')
        (row,) = self.timing_rows()
        self.assertEqual(len(row), 9)
        self.assertEqual(float(row[7]), 0)
        self.assertGreaterEqual(float(row[8]), float(row[5]))

    def test_the_readers_take_seven_and_nine_column_rows(self):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'timings.tsv').write_text(
            '1\tapp\tbehavioral\tverifier\tpass\t100\t0\n'
            '2\tapp\tbehavioral\tverifier\tpass\t300\t5\t1200\t1500\n'
        )
        self.assertEqual(
            self.data('timing', 'app', '--stage', 'behavioral')['seconds'], 300
        )

    def test_commands_see_the_role_they_run_for(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                'fast = ["test \\"$PSTACK_KITCHEN_ROLE\\" = sidekick"]',
            ),
        )
        self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
        self.data('gate', 'app', 'fast', '--role', 'verifier', code=2)
        self.env['PSTACK_KITCHEN_ROLE'] = 'verifier'
        self.assertEqual(self.data('gate', 'app', 'fast', code=2)['role'], 'verifier')

    def test_each_gate_gets_its_own_short_tmpdir_removed_after(self):
        seen = Path(self.tmp.name) / 'seen-tmpdir'
        base = Path(self.tmp.name) / 'gt'
        base.mkdir()
        dead, live = base / 'kg-999999999-dead', base / f'kg-{os.getpid()}-live'
        dead.mkdir(), live.mkdir()
        self.env['PSTACK_KITCHEN_GATE_TMP'] = str(base)
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                f'fast = ["echo \\"$TMPDIR\\" > {seen} && touch \\"$TMPDIR/leftover\\""]',
            ),
        )
        self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
        used = Path(seen.read_text().strip())
        self.assertEqual(used.parent, base)
        self.assertTrue(used.name.startswith('kg-'))
        self.assertFalse(used.exists())
        self.assertFalse(dead.exists())
        self.assertTrue(live.exists())

    def test_a_failure_under_load_is_rerun_once_and_marked_flaky(self):
        mark = Path(self.tmp.name) / 'ran-once'
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                f'fast = ["test -e {mark} || {{ touch {mark}; exit 1; }}"]',
            ),
        )
        self.env['PSTACK_KITCHEN_RETRY_LOAD'] = '-1'
        r = self.data('gate', 'app', 'fast')
        c = r['commands'][0]
        self.assertTrue(r['passed'])
        self.assertTrue(c['flaky'])
        self.assertEqual(c['retried']['first_exit'], 1)
        self.assertTrue(Path(c['retried']['first_log']).exists())
        mark.unlink()
        out = self.run_kitchen('gate', 'app', 'fast')
        self.assertIn('flaky under load: app fast', out)
        self.assertIn('after exit 1', out)
        mark.unlink()
        self.env['PSTACK_KITCHEN_RETRY_LOAD'] = 'off'
        self.assertNotIn(
            'retried', self.data('gate', 'app', 'fast', code=2)['commands'][0]
        )

    def test_a_profile_that_refuses_the_rerun_keeps_the_failure_and_diagnoses_it(self):
        mark = Path(self.tmp.name) / 'ran-once'
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                f'fast = ["test -e {mark} || {{ touch {mark}; exit 1; }}"]\nretry_under_load = false',
            ),
        )
        self.env['PSTACK_KITCHEN_RETRY_LOAD'] = '-1'
        r = self.data('gate', 'app', 'fast', code=2)
        c = r['commands'][0]
        # the rerun passes, yet the failure stands: the rerun only diagnoses it, with its own log beside the failing one
        self.assertFalse(r['passed'])
        self.assertNotIn('retried', c)
        self.assertNotIn('flaky', c)
        self.assertEqual(c['exit'], 1)
        self.assertEqual(
            (c['rerun']['exit'], Path(c['rerun']['log']).name),
            (0, 'app-fast-0.rerun.log'),
        )
        self.assertTrue(Path(c['log']).exists())
        mark.unlink()
        out = self.run_kitchen('gate', 'app', 'fast', code=2)
        self.assertIn('app fast: FAIL', out)
        self.assertRegex(
            out,
            r'\n  reran at load [0-9.]+ \(log: \S+/app-fast-0\.rerun\.log\): passed, so the failure depends on timing: '
            r'look for a race or a tight timeout\n',
        )
        self.assertNotIn('flaky under load', out)
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                'fast = ["exit 3"]\nretry_under_load = false',
            ),
        )
        self.assertIn(
            'rerun.log): failed again (exit 3)\n',
            self.run_kitchen('gate', 'app', 'fast', code=2),
        )
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                'fast = ["test -f src/app.py"]\nretry_under_load = "no"',
            ),
        )
        self.assertIn(
            'profile.app.retry_under_load: expected true or false',
            self.run_kitchen('validate', code=1),
        )

    def test_gate_tmpdirs_default_to_slash_tmp(self):
        os.environ.pop('PSTACK_KITCHEN_GATE_TMP', None)
        self.assertEqual(kitchen.gate_tmp_base(), Path('/tmp'))
        # mbx nests nix-shell.XXXXXX/mbx-session-XXXXXX/cache-agent.sock (52 bytes)
        # under it; a socket path must fit in 104 bytes on macOS.
        self.assertLess(len('/tmp/kg-4194304-abcdefgh/') + 52, 104)

    def test_gates_share_the_machine_slots(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]',
                'fast = ["sleep 2"]',  # kitchen-allow: test-sleep (fake gate must still be running)
            ),
        )
        env = {**self.env, 'PSTACK_KITCHEN_GATE_SLOTS': '1'}
        procs = [
            subprocess.Popen(  # bounded: communicate(timeout=60) below
                [sys.executable, str(script), '--json', 'gate', 'app', 'fast'],
                cwd=self.repo,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [json.loads(p.communicate()[0]) for p in procs]
        self.assertEqual(sorted(r['waited'] >= 1.5 for r in results), [False, True])

    def test_gate_slots_go_to_waiters_in_arrival_order_and_skip_dead_tickets(self):
        import threading

        lock_dir = Path(self.tmp.name) / 'slots'
        lock_dir.mkdir()
        queue = lock_dir / 'gate-queue'
        queue.mkdir()
        dead = queue / f'{1:020d}-999999'
        dead.touch()
        os.utime(dead, (time.time() - 3600, time.time() - 3600))
        holder, _ = kitchen.acquire_slot(lock_dir, 'gate', 1)
        self.assertFalse(dead.exists())
        order, seen = [], {}

        def wait(name):
            f, _ = kitchen.acquire_slot(
                lock_dir, 'gate', 1, lambda pos, n: seen.setdefault(name, (pos, n))
            )
            order.append(name)
            time.sleep(0.5)
            f.close()

        first = threading.Thread(target=wait, args=('first',))
        first.start()
        time.sleep(0.5)
        second = threading.Thread(target=wait, args=('second',))
        second.start()
        time.sleep(0.5)
        holder.close()
        first.join(20)
        second.join(20)
        self.assertEqual(order, ['first', 'second'])
        self.assertEqual(seen, {'first': (1, 1), 'second': (2, 2)})
        self.assertEqual(list(queue.iterdir()), [])

    def test_unknown_profile(self):
        self.assertIn('no profile web', self.run_kitchen('gate', 'web', 'fast', code=1))


class WrapTests(KitchenTests):
    def setUp(self):
        super().setUp()
        # A wrap like `nix develop --command`: noise on stdout, a tool only it
        # puts on PATH, and a count of how often it is entered.
        self.write('wrap/bin/inner-tool', '#!/bin/sh\nexit 0\n')
        (self.repo / 'wrap/bin/inner-tool').chmod(0o755)
        self.write(
            'wrap/enter.sh',
            '#!/usr/bin/env bash\necho "shell hook says hi"\n'
            'echo x >> "$(dirname "$0")/entered"\nPATH="$(dirname "$0")/bin:$PATH" exec "$@"\n',
        )
        (self.repo / 'wrap/enter.sh').chmod(0o755)
        self.entered = self.repo / 'wrap/entered'

    def kitchen(
        self, extra='', fast='["inner-tool", "test \\"$KITCHEN_WRAPPED\\" = 1"]'
    ):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace('fast = ["test -f src/app.py"]', f'fast = {fast}').replace(
                '[coverage]', '[run]\nwrap = "wrap/enter.sh"\n\n[coverage]'
            )
            + extra,
        )

    def test_gate_enters_the_wrap_once_and_keeps_per_command_logs(self):
        self.kitchen()
        r = self.data('gate', 'app', 'fast')
        self.assertTrue(r['passed'], r)
        self.assertEqual([c['exit'] for c in r['commands']], [0, 0])
        self.assertEqual(self.entered.read_text(), 'x\n')

    def test_child_args_name_the_repo_and_only_the_config_and_revision_given(self):
        root = str(self.repo)
        self.assertEqual(kitchen.child_args(kitchen.Invocation(root)), ['--repo', root])
        self.assertEqual(
            kitchen.child_args(kitchen.Invocation(root, 'k.toml', 'HEAD~1')),
            ['--repo', root, '--config', 'k.toml', '--at', 'HEAD~1'],
        )

    def test_a_wrapped_gate_re_runs_kitchen_py_inside_the_wrap_with_the_invocation_it_was_given(
        self,
    ):
        from unittest import mock

        self.kitchen()
        self.write(
            'wrap/enter.sh',
            '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$(dirname "$0")/argv"\n'
            'PATH="$(dirname "$0")/bin:$PATH" exec "$@"\n',
        )
        self.commit('wrap')
        inv = kitchen.Invocation(str(self.repo), None, 'HEAD')
        k, _ = kitchen.load(self.repo, 'HEAD', None)
        with mock.patch.dict(
            os.environ, {**self.env, 'PSTACK_KITCHEN_GATE_SLOTS': '1'}, clear=True
        ):
            os.environ.pop(kitchen.WRAPPED, None)
            self.assertTrue(
                kitchen.run_gate(inv, self.repo, k, 'app', 'fast', None)['passed']
            )
        argv = (self.repo / 'wrap/argv').read_text().splitlines()
        self.assertEqual(
            argv[:9],
            [
                sys.executable,
                str(script),
                '--repo',
                str(self.repo),
                '--at',
                'HEAD',
                'gate',
                'app',
                'fast',
            ],
        )

    def test_a_wrapped_brief_verify_re_runs_kitchen_py_inside_the_wrap_with_the_invocation_it_was_given(
        self,
    ):
        from unittest import mock

        self.kitchen()
        self.write(
            'wrap/enter.sh',
            '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$(dirname "$0")/argv"\n'
            'PATH="$(dirname "$0")/bin:$PATH" exec "$@"\n',
        )
        brief = self.repo / 'brief.md'
        brief.write_text('## Verify\n\n```bash\ntrue\n```\n')
        self.commit('wrap')
        inv = kitchen.Invocation(str(self.repo), None, 'HEAD')
        k, _ = kitchen.load(self.repo, 'HEAD', None)
        with mock.patch.dict(
            os.environ, {**self.env, 'PSTACK_KITCHEN_GATE_SLOTS': '1'}, clear=True
        ):
            os.environ.pop(kitchen.WRAPPED, None)
            self.assertTrue(
                kitchen.run_brief_verify(
                    inv, self.repo, k, brief, None, role='verifier'
                )['passed']
            )
        argv = (self.repo / 'wrap/argv').read_text().splitlines()
        self.assertEqual(
            argv[:7],
            [
                sys.executable,
                str(script),
                '--repo',
                str(self.repo),
                '--at',
                'HEAD',
                'brief-verify',
            ],
        )
        self.assertEqual((argv[7], argv[-2:]), (str(brief), ['--role', 'verifier']))

    def test_a_wrapped_gate_records_one_row_with_the_heavy_wait_and_the_wall_time(self):
        self.kitchen()
        self.data('gate', 'app', 'fast')
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        (row,) = [
            line.split('\t')
            for line in (state_dir / 'timings.tsv').read_text().splitlines()
        ]
        self.assertEqual((len(row), float(row[7])), (9, 0))
        self.assertGreaterEqual(float(row[8]), float(row[5]))

    def test_a_failing_wrap_is_a_failed_gate_with_its_log(self):
        self.kitchen()
        self.write('wrap/enter.sh', '#!/usr/bin/env bash\necho "no devshell"; exit 7\n')
        r = self.data('gate', 'app', 'fast', code=2)
        self.assertEqual(
            (r['commands'][0]['exit'], r['commands'][0]['tail']), (7, ['no devshell'])
        )

    def test_profile_wrap_can_opt_out(self):
        self.kitchen(fast='["test -z \\"$KITCHEN_WRAPPED\\""]')
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace('require_tests = true', 'require_tests = true\nwrap = ""'),
        )
        self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
        self.assertFalse(self.entered.exists())

    def scratch(self, setup, wrap=True):
        self.kitchen()
        text = (self.repo / '.agents/kitchen.toml').read_text()
        if not wrap:
            text = text.replace('[run]\nwrap = "wrap/enter.sh"\n\n', '')
        self.write(
            '.agents/kitchen.toml',
            text + '\n[scratch]\nsetup = ' + json.dumps(setup) + '\n',
        )

    def test_a_setup_run_is_its_own_timing_row_never_a_gate_sample(self):
        self.scratch(['true'])
        self.data('setup')
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        rows = [
            line.split('\t')
            for line in (state_dir / 'timings.tsv').read_text().splitlines()
        ]
        self.assertEqual(
            [(r[1], r[2], r[3], r[4]) for r in rows],
            [('-', 'setup', 'verifier', 'pass')],
        )
        self.assertEqual(
            self.data('timing', 'app', '--stage', 'behavioral')['seconds'], 0
        )

    def test_doctor_warns_when_the_last_verifier_setup_failed(self):
        self.scratch(['echo boom; exit 3'])
        self.data('setup', code=2)
        c = self.doctor_scratch()
        self.assertEqual(c['status'], 'warn')
        self.assertRegex(
            c['detail'],
            r'^the latest setup run failed at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ: echo boom; exit 3$',
        )

    def test_doctor_does_not_warn_once_a_later_setup_run_passed(self):
        self.scratch(['echo boom; exit 3'])
        self.data('setup', code=2)
        self.scratch(['true'])
        self.data('setup')
        self.assertEqual(
            (self.doctor_scratch()['status'], self.doctor_scratch()['detail']),
            ('ok', 'true'),
        )

    def test_two_run_slugs_reach_setup_the_wrap_and_gate_commands(self):
        self.kitchen(fast='["printf \'%s\' \\"$PSTACK_KITCHEN_RUN\\" > gate-run"]')
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text()
            + '\n[scratch]\nsetup = ["printf \'%s\' \\"$PSTACK_KITCHEN_RUN\\" > setup-run"]\n'
        )
        for slug in ('alpha', 'beta'):
            with self.subTest(slug):
                self.env['PSTACK_KITCHEN_RUN'] = slug
                self.entered.unlink(missing_ok=True)
                self.assertTrue(self.data('setup')['passed'])
                self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
                self.assertEqual(
                    (
                        (self.repo / 'setup-run').read_text(),
                        (self.repo / 'gate-run').read_text(),
                    ),
                    (slug, slug),
                )
                self.assertTrue(self.entered.exists())

    def test_setup_with_nothing_declared_passes(self):
        self.kitchen()
        r = self.data('setup')
        self.assertEqual((r['passed'], r['commands'], r['log']), (True, [], None))
        self.assertFalse(self.entered.exists())

    def test_setup_runs_in_order_inside_the_wrap_entered_once_as_the_verifier(self):
        self.scratch(
            [
                'echo one >> out',
                'echo two >> out',
                'printf "%s" "$PSTACK_KITCHEN_ROLE" > role',
                'test "$KITCHEN_WRAPPED" = "" || true',
            ]
        )
        r = self.data('setup')
        self.assertTrue(r['passed'], r)
        self.assertEqual((self.repo / 'out').read_text(), 'one\ntwo\n')
        self.assertEqual((self.repo / 'role').read_text(), 'verifier')
        self.assertEqual(self.entered.read_text(), 'x\n')
        self.assertTrue(Path(r['log']).exists())

    def test_setup_stops_at_the_first_failure_with_the_log_tail(self):
        self.scratch(['true', 'echo boom; exit 3', 'touch never'])
        r = self.data('setup', code=2)
        self.assertEqual((r['passed'], r['exit']), (False, 3))
        self.assertIn('boom', r['tail'])
        self.assertFalse((self.repo / 'never').exists())
        self.assertIn('setup: FAIL exit 3', self.run_kitchen('setup', code=2))

    def test_a_failing_wrap_fails_setup_with_its_output(self):
        self.scratch(['touch ran'])
        self.write('wrap/enter.sh', '#!/usr/bin/env bash\necho "no devshell"; exit 7\n')
        r = self.data('setup', code=2)
        self.assertEqual((r['exit'], r['tail']), (7, ['no devshell']))
        self.assertFalse((self.repo / 'ran').exists())

    def test_setup_without_a_wrap_runs_from_the_repo_root(self):
        self.scratch(['pwd > where'], wrap=False)
        self.assertTrue(self.data('setup')['passed'])
        self.assertEqual(
            (self.repo / 'where').read_text().strip(), str(self.repo.resolve())
        )

    def test_scratch_setup_is_validated(self):
        self.kitchen(extra='\n[scratch]\nsetup = "bun install"\n')
        self.assertIn(
            'scratch.setup: expected a list of non-empty strings',
            self.run_kitchen('validate', code=1),
        )
        self.kitchen(extra='\n[scratch]\ninstall = []\n')
        self.assertIn(
            'scratch: unknown key install', self.run_kitchen('validate', code=1)
        )

    def doctor_scratch(self):
        r = subprocess.run(
            [sys.executable, str(script), '--json', 'doctor'],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return {x['check']: x for x in json.loads(r.stdout)['checks']}['scratch setup']

    def test_doctor_reports_setup_and_warns_only_for_a_lockfile_without_it(self):
        self.kitchen()
        self.assertEqual(self.doctor_scratch()['status'], 'ok')
        self.assertEqual(self.doctor_scratch()['detail'], 'none')
        self.write('package.json', '{}\n')
        self.assertEqual(self.doctor_scratch()['status'], 'ok')
        self.write('bun.lock', '{}\n')
        c = self.doctor_scratch()
        self.assertEqual(c['status'], 'warn')
        self.assertIn('package.json with a lockfile', c['detail'])
        self.scratch(['bun install --frozen-lockfile'])
        c = self.doctor_scratch()
        self.assertEqual(
            (c['status'], c['detail']), ('ok', 'bun install --frozen-lockfile')
        )
        self.kitchen()
        for f in ('package.json', 'bun.lock'):
            (self.repo / f).unlink()
        self.write('pyproject.toml', '[project]\n')
        self.write('uv.lock', '\n')
        self.assertEqual(self.doctor_scratch()['status'], 'warn')

    def test_doctor_resolves_commands_inside_the_wrap(self):
        self.kitchen()
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace('fast = ["true"]', 'fast = ["nosuchtool"]'),
        )
        c = {x['check']: x for x in self.data('doctor', code=3)['checks']}
        self.assertEqual(
            c['commands']['items'], ['contracts.fast: nosuchtool (inside wrap)']
        )
        self.assertEqual(self.entered.read_text(), 'x\n')


class PolicyTests(KitchenTests):
    def test_forbid_rule_on_added_lines_with_allow(self):
        head = self.change(
            {
                'src/app.py': 'def main():\n    print("x")\n    print("y")  # kitchen-allow: no-print, CLI output\n',
                'docs/guide.md': '# Guide\nprint(1)\n',
                'tests/test_app.py': 'def test_main():\n    assert 2\n',
            }
        )
        r = self.data('policy', '--base', self.base, '--head', head, code=2)
        self.assertEqual(
            [(f['rule'], f['path'], f['line']) for f in r['findings']],
            [('no-print', 'src/app.py', 2)],
        )
        self.assertEqual(r['findings'][0]['source'], 'AGENTS.md#logging')

    def test_kitchen_allow_takes_a_comma_list_of_ids_and_the_rest_of_the_line_is_the_reason(
        self,
    ):
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml').read_text()
            + '\n[[policy.forbid]]\nid = "other-rule"\npattern = \'\\bprint\\(\'\npaths = ["src/**"]\nmessage = "also no"\n',
        )
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'second rule')
        base = self.git('rev-parse', 'HEAD')
        lines = {
            'one id then a reason in lowercase words': '    print("a")  # kitchen-allow: no-print bounded reason\n',
            'a comma list then a reason': '    print("b")  # kitchen-allow: no-print, other-rule plain reason\n',
            'a second id after a space is reason text': '    print("c")  # kitchen-allow: other-rule no-print\n',
            'no allow': '    print("d")\n',
        }
        head = self.change(
            {
                'src/app.py': 'def main():\n' + ''.join(lines.values()),
                'tests/test_app.py': 'def test_main():\n    assert 5\n',
            }
        )
        r = self.data('policy', '--base', base, '--head', head, code=2)
        found = sorted((f['line'], f['rule']) for f in r['findings'])
        # line 2: no-print allowed (the words after it are the reason), so only other-rule fires; line 3: both allowed; line 4: other-rule allowed,
        # `no-print` after it is reason text and still fires; line 5: both fire
        self.assertEqual(
            found,
            [(2, 'other-rule'), (4, 'no-print'), (5, 'no-print'), (5, 'other-rule')],
        )

    def test_test_touch(self):
        head = self.change({'src/util.py': 'X = 5\n'})
        r = self.data('policy', '--base', self.base, '--head', head, code=2)
        self.assertEqual(
            [(f['rule'], f['profile']) for f in r['findings']], [('test-touch', 'app')]
        )
        self.assertIn(
            'src/util.py: [test-touch] profile app',
            self.run_kitchen('policy', '--base', self.base, '--head', head, code=2),
        )
        self.assertEqual(
            self.data('history', '--last', '1')['commits'][0]['findings'],
            ['test-touch(app)'],
        )
        head2 = self.change({'tests/test_app.py': 'def test_main():\n    assert 3\n'})
        self.assertEqual(
            self.data('policy', '--base', self.base, '--head', head2), {'findings': []}
        )

    def test_working_tree(self):
        self.write('src/app.py', 'print(1)\n')
        self.write('tests/test_app.py', 'x = 1\n')
        r = self.data('policy', '--working-tree', code=2)
        self.assertEqual([f['rule'] for f in r['findings']], ['no-print'])


class HostKitchenTests(KitchenTests):
    # The repo commits no kitchen; the host keeps it beside the roster.
    def setUp(self):
        super().setUp()
        self.git('rm', '-q', '.agents/kitchen.toml')
        self.base = self.commit('no kitchen')
        self.git('remote', 'add', 'origin', 'git@github.com:Acme/Widget.git')
        self.host = Path(self.tmp.name) / 'kitchens/github.com__Acme__Widget.toml'
        self.host.parent.mkdir()
        self.host.write_text(KITCHEN)

    def doctor(self):
        return {c['check']: c for c in self.data('doctor', code=None)['checks']}[
            'kitchen'
        ]

    def run_kitchen(self, *args, code=0):
        if code is not None:
            return super().run_kitchen(*args, code=code)
        result = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.stdout = result.stdout
        return result.stdout + result.stderr

    def test_snapshot_returns_the_path_of_a_copy_named_by_the_kitchen_content(self):
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            first = kitchen.snapshot(
                kitchen.Invocation(str(self.repo)), Path(self.tmp.name) / 'snaps'
            )
            again = kitchen.snapshot(
                kitchen.Invocation(str(self.repo)), Path(self.tmp.name) / 'snaps'
            )
        self.assertEqual(out.getvalue(), '')
        self.assertEqual((first, again), (again, first))
        self.assertRegex(first, r'/snaps/[0-9a-f]{12}\.toml$')
        self.assertEqual(Path(first).read_text(), KITCHEN)

    def test_origin_key_reads_every_remote_spelling(self):
        for url, key in [
            ('git@github.com:Acme/Widget.git', 'github.com/Acme/Widget'),
            ('https://GitHub.com/acme/widget/', 'github.com/acme/widget'),
            ('ssh://git@host:2222/a/b.git', 'host/a/b'),
            (
                'https://user:' + 'tok@gitlab.example/g/sub/r.git',
                'gitlab.example/g/sub/r',
            ),
            ('/srv/git/r.git', None),
            ('', None),
        ]:
            self.assertEqual(kitchen.origin_key(url), key, url)

    def test_the_host_kitchen_is_read_at_any_revision(self):
        for args in [(), ('--at', 'HEAD'), ('--at', self.base)]:
            r = self.data(*args, 'validate')
            self.assertEqual((r['source'], r['path']), ('user', str(self.host)), args)
        self.assertEqual(
            self.data('where')['host'],
            {'source': 'user', 'path': str(self.host), 'exists': True},
        )

    def test_a_worktree_finds_the_same_host_kitchen(self):
        wt = Path(self.tmp.name) / 'wt'
        self.git('worktree', 'add', '-q', str(wt), 'HEAD')
        self.assertEqual(
            self.data('--repo', str(wt), '--at', 'HEAD', 'validate')['path'],
            str(self.host),
        )

    def test_without_an_origin_the_key_is_the_main_checkout(self):
        self.git('remote', 'remove', 'origin')
        path = Path(self.run_kitchen('where', '--host').strip())
        self.assertEqual(path.parent, self.host.parent)
        self.assertRegex(path.name, r'^repo-[0-9a-f]{8}\.toml$')
        out = self.run_kitchen('--at', 'HEAD', 'validate', code=1)
        self.assertIn(f'no kitchen at HEAD:.agents/kitchen.toml or {path}', out)

    def test_a_roster_glob_picks_the_file(self):
        other = Path(self.tmp.name) / 'kitchens/widget.toml'
        other.write_text(KITCHEN)
        Path(self.env['PSTACK_KITCHEN_ROSTER']).write_text(
            f'[[kitchen]]\nmatch = "{self.tmp.name}/**"\nfile = "widget.toml"\n'
        )
        r = self.data('--at', 'HEAD', 'validate')
        self.assertEqual((r['source'], r['path']), ('roster', str(other)))

    def test_doctor_warns_when_claude_or_codex_does_not_trust_the_scratch_root(self):
        root = Path(self.env['XDG_STATE_HOME']) / 'pstack/scratch'
        claude, codex = Path(self.tmp.name) / 'claude', Path(self.tmp.name) / 'codex'
        claude.mkdir()
        codex.mkdir()
        self.env['CLAUDE_CONFIG_DIR'], self.env['CODEX_HOME'] = str(claude), str(codex)
        Path(self.env['PSTACK_KITCHEN_ROSTER']).write_text(
            '[consultant]\nkind = "codex"\n'
        )
        (claude / '.claude.json').write_text(
            '{"projects": {"/elsewhere": {"hasTrustDialogAccepted": true}}}'
        )
        (codex / 'config.toml').write_text(
            f'[projects."{root.parent}"]\ntrust_level = "trusted"\n'
        )
        check = self.doctor_check('scratch trust')
        self.assertEqual(check['status'], 'warn')
        self.assertIn(f'claude does not trust the scratch root {root}', check['detail'])
        # Trust in a folder above the root covers it.
        (claude / '.claude.json').write_text(
            json.dumps(
                {'projects': {str(root.parent): {'hasTrustDialogAccepted': True}}}
            )
        )
        check = self.doctor_check('scratch trust')
        self.assertEqual(check['status'], 'ok')
        self.assertIn('trusted by codex, claude', check['detail'])

    def test_a_committed_kitchen_shadows_the_host_one(self):
        self.write('.agents/kitchen.toml', KITCHEN)
        self.commit('kitchen')
        self.assertEqual(self.data('--at', 'HEAD', 'validate')['source'], 'repo')
        d = self.doctor()
        self.assertEqual(d['status'], 'warn')
        self.assertIn(f'the host kitchen {self.host} is shadowed', d['detail'])

    def test_doctor_fails_an_uncommitted_repo_kitchen(self):
        self.write('.agents/kitchen.toml', KITCHEN)
        d = self.doctor()
        self.assertEqual(d['status'], 'fail')
        self.assertIn('.agents/kitchen.toml is untracked', d['detail'])
        (self.repo / '.git/info/exclude').write_text('.agents/kitchen.toml\n')
        self.assertIn('is ignored or excluded', self.doctor()['detail'])

    def test_a_snapshot_is_named_by_content_and_read_through_config(self):
        snaps = Path(self.tmp.name) / 'store/kitchens'
        first = self.run_kitchen('snapshot', str(snaps)).strip()
        self.assertEqual(self.run_kitchen('snapshot', str(snaps)).strip(), first)
        self.host.write_text(
            KITCHEN.replace('max_diff_lines = 50', 'max_diff_lines = 60')
        )
        second = self.run_kitchen('snapshot', str(snaps)).strip()
        self.assertNotEqual(first, second)
        self.assertEqual(
            sorted(p.name for p in snaps.iterdir()),
            sorted([Path(first).name, Path(second).name]),
        )
        r = self.data('--config', first, '--at', 'HEAD', 'validate')
        self.assertEqual((r['source'], r['path']), ('file', first))

    def test_host_kitchen_lessons_sit_beside_it(self):
        self.assertEqual(
            self.data('lessons')['path'], str(self.host.with_suffix('.lessons.md'))
        )

    def test_a_repo_kitchen_takes_no_snapshot(self):
        self.write('.agents/kitchen.toml', KITCHEN)
        self.commit('kitchen')
        self.assertEqual(
            self.run_kitchen('snapshot', str(Path(self.tmp.name) / 'snaps')), ''
        )


class LessonTests(KitchenTests):
    def setUp(self):
        super().setUp()
        self.ledger = Path(self.run_kitchen('statedir').strip()) / 'ledger.tsv'
        self.ledger.write_text('ts\trun\tkind\tclass\tdetail\tunit\tarea\tfinding\n')

    def row(self, run, kind, cls, unit, area, finding, detail='-', match='', ts=None):
        ts = ts or f'2026-10-0{len(self.ledger.read_text().splitlines())}T00:00:00Z'
        with self.ledger.open('a') as f:
            f.write(
                f'{ts}\t{run}\t{kind}\t{cls}\t{detail}\t{unit}\t{area}\t{finding}\t{match}\n'
            )

    def candidates(self):
        return [
            (c['kind'], c['class'], c['area'], c['runs'])
            for c in self.data('lessons')['candidates']
        ]

    def key(self, lid):
        x = next(
            x
            for x in kitchen.read_lessons(self.repo / '.agents/LESSONS.md')
            if x.id == lid
        )
        return kitchen.lesson_key(lid, x.fields.get('active', ''))

    def test_trouble_in_one_area_across_runs_is_a_candidate(self):
        self.row(
            'run-a', 'reject', 'verify', '001', 'src/lib', 'the cache is never cleared'
        )
        self.row(
            'run-b',
            'reject',
            'verify',
            '003',
            'docs,src/lib',
            'cache stale after write',
        )
        self.row('run-b', 'reject', 'verify', '004', 'src/api', 'other area, one run')
        self.row('run-a', 'gate-fail', 'app', '002', 'src/lib', 'one run only')
        self.row('run-c', 'catch', 'review', '', '', 'no unit, no area')
        self.row('run-c', 'catch', 'review', '005', 'src/api', 'missed a caller')
        # The most runs first.
        self.assertEqual(
            self.candidates(),
            [
                ('reject', 'verify', 'src/lib', ['run-a', 'run-b']),
                ('catch', 'review', 'src/api', ['run-c']),
            ],
        )
        r = self.data('lessons')['candidates'][0]
        self.assertEqual(
            r['evidence'],
            [
                '2026-10-01 run-a 001: the cache is never cleared',
                '2026-10-02 run-b 003: cache stale after write',
            ],
        )

    def test_evidence_puts_globs_in_code_spans(self):
        finding = (
            'a.ts: [test-touch] no file matching apps/*/test/**, packages/*/test/** (x)'
        )
        self.row('run-a', 'catch', 'review', '001', 'src', finding)
        self.row('run-b', 'catch', 'review', '002', 'src', 'already `src/*.ts` quoted')
        self.assertEqual(
            self.data('lessons')['candidates'][0]['evidence'],
            [
                '2026-10-01 run-a 001: a.ts: [test-touch] no file matching `apps/*/test/**`, `packages/*/test/**` (x)',
                '2026-10-02 run-b 002: already `src/*.ts` quoted',
            ],
        )

    def test_a_gate_failure_is_a_candidate_by_its_check_after_three_runs(self):
        for run in ('run-a', 'run-b'):
            self.row(
                run,
                'gate-fail',
                'web',
                '001',
                'apps/web',
                'x.ts:1:1: error eslint(no-unused-vars)',
                match='fast lint',
            )
            self.row(
                run,
                'gate-fail',
                'web',
                '002',
                'apps/web',
                'x.ts(1,1): error TS2322',
                match='fast typecheck',
            )
        self.row(
            'run-c',
            'gate-fail',
            'web',
            '001',
            'apps/web',
            'y.ts:2:1: error eslint(no-unused-vars)',
            match='fast lint',
        )
        # Without its step's output a failure names no check, and teaches nothing.
        for run in ('run-d', 'run-e', 'run-f'):
            self.row(
                run, 'gate-fail', 'web', '001', 'apps/web', '', detail='001-x abc1234'
            )
        self.assertEqual(
            [
                (c['class'], c['match'], c['area'], c['runs'])
                for c in self.data('lessons')['candidates']
            ],
            [('web', 'fast lint', 'apps/web', ['run-a', 'run-b', 'run-c'])],
        )
        self.assertIn(
            "3 runs (run-a, run-b, run-c): gate-fail web apps/web --match 'fast lint'",
            self.run_kitchen('lessons'),
        )
        self.run_kitchen(
            'lessons',
            'add',
            'gate-fail',
            'web',
            'apps/web',
            'Lint the touched package before each step.',
            '--match',
            'fast lint',
        )
        x = self.data('lessons')['lessons'][0]
        self.assertEqual((x['match'], x['layer']), ('fast lint', 'gate'))
        self.assertIn(
            '2026-10-01 run-a 001: x.ts:1:1: error eslint(no-unused-vars)',
            (self.repo / '.agents/LESSONS.md').read_text(),
        )
        self.assertNotIn('TS2322', (self.repo / '.agents/LESSONS.md').read_text())
        self.assertEqual(self.data('lessons')['candidates'], [])

    def test_a_policy_failure_is_a_policy_lesson(self):
        for run in ('run-a', 'run-b', 'run-c'):
            self.row(
                run,
                'gate-fail',
                'policy',
                '001',
                'tooling',
                'a.ts: [test-touch] no tests',
                match='test-touch',
            )
        self.run_kitchen(
            'lessons',
            'add',
            'gate-fail',
            'policy',
            'tooling',
            'Change a test with the source.',
            '--match',
            'test-touch',
        )
        self.assertIn(
            'target: a [[policy.forbid]] rule',
            self.run_kitchen('lessons', 'promote', 'L001'),
        )

    def test_only_a_steer_that_turned_the_sidekick_is_trouble(self):
        for run in ('run-a', 'run-b'):
            self.row(
                run,
                'steer',
                'brief',
                '001',
                'src',
                'scope: add the fixture',
                match='scope',
            )
            self.row(
                run,
                'steer',
                'brief',
                '002',
                'src',
                'info: news from the owner',
                match='info',
            )
        self.assertEqual(self.candidates(), [])
        self.row(
            'run-a',
            'steer',
            'brief',
            '003',
            'src',
            'redirect: read the cache first',
            match='redirect',
        )
        # A row from before the match column: the steer's kind leads its finding.
        self.row('run-b', 'steer', 'brief', '003', 'src', 'redirect: not the CLI')
        self.assertEqual(
            [(c['match'], c['runs']) for c in self.data('lessons')['candidates']],
            [('redirect', ['run-a', 'run-b'])],
        )

    def test_a_dismissed_candidate_stays_hidden_until_newer_trouble(self):
        self.row('run-a', 'reject', 'verify', '001', 'src', 'one thing')
        self.row('run-b', 'reject', 'verify', '002', 'src', 'another thing')
        self.run_kitchen(
            'lessons', 'dismiss', 'reject', 'verify', 'src', '--note', 'two causes'
        )
        self.assertEqual(self.candidates(), [])
        self.assertIn('none', self.run_kitchen('lessons'))
        self.row(
            'run-c',
            'reject',
            'verify',
            '003',
            'src',
            'a third',
            ts='2099-01-01T00:00:00Z',
        )
        self.assertEqual(
            self.candidates(),
            [('reject', 'verify', 'src', ['run-a', 'run-b', 'run-c'])],
        )
        self.assertIn(
            'no ledger row',
            self.run_kitchen(
                'lessons', 'dismiss', 'reject', 'verify', 'docs', '--note', 'x', code=1
            ),
        )

    def test_ids_come_from_the_ledger_so_parallel_worktrees_do_not_collide(self):
        self.row('run-a', 'catch', 'review', '001', 'src', 'missed a caller')
        self.assertIn(
            'next: commit it on its own',
            self.run_kitchen('lessons', 'add', 'catch', 'review', 'src', 'Rule one.'),
        )
        # Another run's worktree has its own copy of the file, without L001.
        (self.repo / '.agents/LESSONS.md').unlink()
        self.assertEqual(
            self.data('lessons', 'add', 'catch', 'review', 'src', 'Rule two.')['id'],
            'L002',
        )
        out = self.run_kitchen('lessons', 'set', 'L002', 'active')
        self.assertIn('add -- .agents/LESSONS.md && ', out)

    def test_retro_prints_the_strongest_candidates(self):
        for n in range(7):
            for run in ('run-a', 'run-b'):
                self.row(run, 'reject', 'verify', f'00{n}', f'src/m{n}', 'x')
        out = self.run_kitchen('lessons')
        self.assertEqual(out.count(': reject verify src/m'), 5)
        self.assertIn('2 more with fewer runs: kitchen.py lessons --all', out)
        self.assertEqual(
            self.run_kitchen('lessons', '--all').count(': reject verify src/m'), 7
        )

    def test_the_area_is_the_narrowest_that_keeps_every_run(self):
        self.row('run-a', 'steer', 'brief', '001', 'src/lib', 'narrow: x')
        self.row('run-b', 'steer', 'brief', '002', 'src/api', 'narrow: y')
        self.row('run-c', 'steer', 'brief', '003', 'src', 'narrow: z')
        self.assertEqual(
            self.candidates(), [('steer', 'brief', 'src', ['run-a', 'run-b', 'run-c'])]
        )

    def test_a_staged_lesson_lives_in_the_repo_and_hides_its_candidate(self):
        self.row(
            'run-a', 'reject', 'verify', '001', 'src/lib', 'the cache is never cleared'
        )
        self.row(
            'run-b', 'reject', 'verify', '003', 'src/lib', 'cache stale after write'
        )
        self.assertIn(
            'L001 proposed',
            self.run_kitchen(
                'lessons',
                'add',
                'reject',
                'verify',
                'src/lib',
                'Every write to the store clears the cache.',
            ),
        )
        text = (self.repo / '.agents/LESSONS.md').read_text()
        self.assertIn(
            '## L001: Every write to the store clears the cache.\n\nstatus: proposed\nkind: reject\n'
            'class: verify\nlayer: verify\narea: src/lib\n',
            text,
        )
        self.assertIn(
            'evidence:\n\n- 2026-10-01 run-a 001: the cache is never cleared\n', text
        )
        self.assertEqual(self.candidates(), [])
        self.assertIn(
            'needs evidence',
            self.run_kitchen('lessons', 'add', 'reject', 'verify', 'docs', 'x', code=1),
        )
        self.assertEqual(
            self.data('lessons', 'add', 'reject', 'verify', 'src', 'Second rule.')[
                'id'
            ],
            'L002',
        )
        self.assertEqual(
            [x['id'] for x in self.data('lessons')['lessons']], ['L001', 'L002']
        )

    def test_lessons_add_and_set_write_the_formatter_canonical_file(self):
        self.row(
            'run-a', 'reject', 'verify', '001', 'src/lib', 'the cache is never cleared'
        )
        self.row(
            'run-b', 'reject', 'verify', '003', 'src/lib', 'cache stale after write'
        )
        self.run_kitchen(
            'lessons',
            'add',
            'reject',
            'verify',
            'src/lib',
            'Every write to the store clears the cache.',
        )
        self.run_kitchen(
            'lessons', 'set', 'L001', 'retired', '--note', 'covered by the cache lint'
        )
        text = (self.repo / '.agents/LESSONS.md').read_text()
        lesson = re.sub(
            r'^since: .*$', 'since: TODAY', text[text.index('## L001') :], flags=re.M
        )
        self.assertEqual(
            lesson,
            (
                '## L001: Every write to the store clears the cache.\n'
                '\n'
                'status: retired\n'
                'kind: reject\n'
                'class: verify\n'
                'layer: verify\n'
                'area: src/lib\n'
                'since: TODAY\n'
                'note: covered by the cache lint\n'
                '\n'
                'evidence:\n'
                '\n'
                '- 2026-10-01 run-a 001: the cache is never cleared\n'
                '- 2026-10-02 run-b 003: cache stale after write\n'
            ),
        )
        self.run_kitchen(
            'lessons', 'set', 'L001', 'retired', '--note', 'covered by the cache lint'
        )
        self.assertEqual((self.repo / '.agents/LESSONS.md').read_text(), text)

    def test_a_narrow_lesson_leaves_trouble_outside_it_to_propose(self):
        for run, area in [
            ('run-a', 'src/lib'),
            ('run-b', 'src/lib'),
            ('run-c', 'src/api'),
            ('run-d', 'src/api'),
        ]:
            self.row(run, 'reject', 'verify', '001', area, 'x')
        self.run_kitchen('lessons', 'add', 'reject', 'verify', './src/lib/', 'Rule.')
        self.assertEqual(self.data('lessons')['lessons'][0]['area'], 'src/lib')
        self.assertEqual(
            self.candidates(), [('reject', 'verify', 'src/api', ['run-c', 'run-d'])]
        )
        self.assertIn(
            'one repo directory',
            self.run_kitchen(
                'lessons', 'add', 'reject', 'verify', 'src,docs', 'R.', code=1
            ),
        )

    def doctor(self):
        return {c['check']: c for c in self.data('doctor')['checks']}['lessons']

    def test_a_promotion_draft_names_the_layer_and_why_and_leaves_the_proof_to_the_master(
        self,
    ):
        self.row(
            'run-a',
            'catch',
            'review',
            '001',
            'src/lib',
            'missed a caller',
            detail='missed a caller',
        )
        self.run_kitchen(
            'lessons',
            'add',
            'catch',
            'review',
            'src/lib',
            'A renamed function has every caller updated.',
        )
        self.assertEqual(self.doctor()['status'], 'ok')
        self.assertIn('1 proposed', self.doctor()['detail'])
        draft = self.run_kitchen('lessons', 'promote', 'L001')
        self.assertIn(
            'promotable: no (status is proposed, not active; held 0 of 3)', draft
        )
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        for n in range(3):
            self.row(
                f'run-{n}',
                'lesson',
                'held',
                f'00{n}',
                'src/lib',
                '',
                detail=self.key('L001'),
            )
        d = self.doctor()
        self.assertEqual(
            (d['status'], d['items']),
            ('warn', ['L001: promotable (held 3); kitchen.py lessons promote L001']),
        )
        out = Path(self.tmp.name) / 'store/promotions/L001.md'
        self.run_kitchen('lessons', 'promote', 'L001', '--out', str(out))
        draft = out.read_text()
        self.assertTrue(
            draft.startswith(
                '# Promote L001: A renamed function has every caller updated.\n'
            )
        )
        self.assertIn('promotable: yes\n', draft)
        self.assertIn(
            'target: a lint, test or type through the correct skill; else a CODE_STANDARD.md section',
            draft,
        )
        self.assertIn('why this layer: review caught it by reading', draft)
        self.assertIn('- 2026-10-01 run-a 001: missed a caller\n', draft)
        self.assertIn(
            'held or hit in: run-0 000 held, run-1 001 held, run-2 002 held', draft
        )
        for section in ('## Example', '## Proof', '## False positives', '## Decision'):
            self.assertIn(section, draft)
        self.assertIn(
            'no lesson L007', self.run_kitchen('lessons', 'promote', 'L007', code=1)
        )

    def test_doctor_flags_a_lesson_that_keeps_being_hit_or_cannot_be_read(self):
        self.row(
            'run-a', 'gate-fail', 'app', '001', 'src', 'lint', detail='001-x abc1234'
        )
        self.row(
            'run-b', 'gate-fail', 'app', '002', 'src', 'lint', detail='002-y def5678'
        )
        self.run_kitchen(
            'lessons',
            'add',
            'gate-fail',
            'app',
            'src',
            'Run the formatter before each step.',
        )
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertIn(
            'target: a lint rule, or a fast-gate command in profile `app`',
            self.run_kitchen('lessons', 'promote', 'L001'),
        )
        self.assertIn(
            'ledger refs (run, then the unit, verdict or steer to open): run-a 001-x abc1234, run-b 002-y def5678',
            self.run_kitchen('lessons', 'promote', 'L001'),
        )
        for n in range(2):
            self.row(
                f'run-{n}',
                'lesson',
                'hit',
                f'00{n}',
                'src',
                'lint again',
                detail=self.key('L001'),
            )
        # Activating again with the same rule keeps the count.
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertEqual(self.data('lessons')['lessons'][0]['hits'], 2)
        path = self.repo / '.agents/LESSONS.md'
        path.write_text(path.read_text() + '\n## L002: Broken.\n\nstatus: active\n')
        self.assertEqual(
            self.doctor()['items'],
            [
                'L001: a gate lesson hit 2 times; encode an autofix the step runs '
                'before its gate (a formatter or fixer in the profile) in place of more rule text',
                'L002: unreadable (kind, class, layer, area); fix it by hand',
            ],
        )
        # A rewritten rule starts its count again.
        time.sleep(1.1)  # the new version's stamp is to the second
        path.write_text(
            path.read_text().replace('\n## L002: Broken.\n\nstatus: active\n', '')
        )
        self.run_kitchen(
            'lessons',
            'set',
            'L001',
            'active',
            '--rule',
            'Run the formatter in the pre-commit hook.',
        )
        x = self.data('lessons')['lessons'][0]
        self.assertEqual(
            (x['rule'], x['held'], x['hits']),
            ('Run the formatter in the pre-commit hook.', 0, 0),
        )
        self.assertEqual(self.doctor()['status'], 'ok')

    def test_a_lesson_needs_a_rewrite_by_its_hit_rate(self):
        for held, hits, flags in (
            (0, 2, True),
            (1, 1, False),
            (107, 16, False),
            (6, 2, True),
            (7, 2, False),
        ):
            with self.subTest(held=held, hits=hits):
                self.assertEqual(
                    kitchen.needs_rewrite({'held': held, 'hits': hits}), flags
                )

    def test_doctor_points_a_hit_gate_lesson_at_an_autofix(self):
        self.row(
            'run-a', 'gate-fail', 'app', '001', 'src', 'lint', detail='001-x abc1234'
        )
        self.row(
            'run-b', 'gate-fail', 'app', '002', 'src', 'lint', detail='002-y def5678'
        )
        self.run_kitchen(
            'lessons', 'add', 'gate-fail', 'app', 'src', 'Run the formatter.'
        )
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertEqual(self.doctor()['items'], [])
        self.row(
            'run-c', 'lesson', 'hit', '003', 'src', 'lint', detail=self.key('L001')
        )
        self.assertEqual(
            self.doctor()['items'],
            [
                'L001: a gate lesson hit once; encode an autofix the step runs '
                'before its gate (a formatter or fixer in the profile) in place of more rule text'
            ],
        )
        self.row(
            'run-d', 'lesson', 'hit', '004', 'src', 'lint', detail=self.key('L001')
        )
        self.assertEqual(
            self.doctor()['items'],
            [
                'L001: a gate lesson hit 2 times; encode an autofix the step runs '
                'before its gate (a formatter or fixer in the profile) in place of more rule text'
            ],
        )

    def test_doctor_flags_a_non_gate_lesson_by_its_hit_rate(self):
        self.row('run-a', 'reject', 'verify', '001', 'src', 'missed it')
        self.row('run-b', 'reject', 'verify', '002', 'src', 'missed it')
        self.run_kitchen('lessons', 'add', 'reject', 'verify', 'src', 'Check it.')
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        for n, kind in enumerate(['held'] * 7 + ['hit'] * 3):
            self.row(
                f'run-{n}',
                'lesson',
                kind,
                f'1{n:02}',
                'src',
                '',
                detail=self.key('L001'),
            )
        self.assertEqual(
            self.doctor()['items'],
            [
                'L001: hit 3 of 10 units (30%) while active; rewrite the rule or retire it'
            ],
        )

    def test_doctor_flags_the_same_rule_and_add_refuses_it(self):
        self.row('run-a', 'catch', 'review', '001', 'src/lib', 'missed a caller')
        self.run_kitchen('lessons', 'add', 'catch', 'review', 'src/lib', 'Rule one.')
        path = self.repo / '.agents/LESSONS.md'
        before = path.read_text()
        self.assertIn(
            "the rule is already L001's",
            self.run_kitchen(
                'lessons', 'add', 'catch', 'review', 'src/lib', 'RULE   one.', code=1
            ),
        )
        self.assertEqual(path.read_text(), before)
        copy = before[before.index('## L001') :].replace('L001', 'L002')
        path.write_text(before + '\n' + copy.replace('Rule one.', 'rule  ONE.'))
        self.assertIn('L002: same rule as L001; retire one', self.doctor()['items'])

    def test_rewriting_the_file_keeps_what_a_human_wrote(self):
        self.row('run-a', 'catch', 'review', '001', 'src/lib', 'missed a caller')
        self.run_kitchen('lessons', 'add', 'catch', 'review', 'src/lib', 'Rule one.')
        path = self.repo / '.agents/LESSONS.md'
        path.write_text(
            path.read_text().replace(
                '# Kitchen lessons\n', '# Our lessons\n\nTeam note.\n', 1
            )
            + '\nWhy: the API module has three entry points.\nreviewer: ana\n'
        )
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        text = path.read_text()
        self.assertTrue(text.startswith('# Our lessons\n\nTeam note.\n\nRules learned'))
        self.assertIn('is counted on the host, not here.\n\n## L001: Rule one.\n', text)
        self.assertIn(
            'evidence:\n\n- 2026-10-01 run-a 001: missed a caller\n\nWhy: the API module has three entry points.\n'
            'reviewer: ana\n',
            text,
        )
        self.assertIn('status: active\n', text)

    def test_three_clean_units_and_no_hit_make_a_lesson_promotable(self):
        self.row('run-a', 'catch', 'review', '001', 'src/lib', 'missed a caller')
        self.run_kitchen(
            'lessons',
            'add',
            'catch',
            'review',
            'src/lib',
            'A renamed function has every caller updated.',
        )
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertRegex(
            (self.repo / '.agents/LESSONS.md').read_text(),
            r'status: active\n(.*\n)*active: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n',
        )
        for n in range(3):
            self.row(
                f'run-{n}',
                'lesson',
                'held',
                '00{n}',
                'src/lib',
                '',
                detail=self.key('L001'),
            )
        x = self.data('lessons')['lessons'][0]
        self.assertEqual(
            (x['layer'], x['held'], x['hits'], x['promotable']), ('review', 3, 0, True)
        )
        self.assertIn('(promotable)', self.run_kitchen('lessons'))
        self.row(
            'run-9',
            'lesson',
            'hit',
            '009',
            'src/lib',
            'missed again',
            detail=self.key('L001'),
        )
        self.assertFalse(self.data('lessons')['lessons'][0]['promotable'])
        self.assertIn(
            'needs --note',
            self.run_kitchen('lessons', 'set', 'L001', 'promoted', code=1),
        )
        self.run_kitchen(
            'lessons',
            'set',
            'L001',
            'promoted',
            '--note',
            'abc1234 lint rule no-stale-callers',
        )
        x = self.data('lessons')['lessons'][0]
        self.assertEqual(
            (x['status'], x['note']), ('promoted', 'abc1234 lint rule no-stale-callers')
        )
        self.assertIn(
            'no lesson L009',
            self.run_kitchen('lessons', 'set', 'L009', 'active', code=1),
        )


class LessonScoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name)
        for d in ('briefs', 'verdicts'):
            (self.store / d).mkdir()
        self.ledger = self.store / 'ledger.tsv'

    def brief(self, name, lessons):
        (self.store / 'briefs' / name).write_text(
            '# Brief\n\n## Scope\n\nmay write:\n- src/**\n\n## Lessons\n\nx\n\n'
            + ''.join(
                f'- {l} (reject verify, layer verify, in src): rule\n' for l in lessons
            )
        )

    def verdict(self, name, status, units, lessons=''):
        (self.store / 'verdicts' / name).write_text(
            f'# V\n\nstatus: {status}\nunits: {units}\n\n## Findings\n\n'
            f'{"1. broke" if status == "reject" else "none"}\n\n## Lessons\n\n{lessons}'
        )

    def scores(self):
        kitchen.ledger_retro(self.store, self.ledger, 'r', '2026-10-13T00:00:00Z')
        return sorted(
            (r['detail'], r['unit'], r['class'], r['finding'])
            for r in kitchen.ledger_rows(self.ledger)
            if r['kind'] == 'lesson'
        )

    def test_held_hit_or_unscored(self):
        self.brief('001-a.md', ['L001', 'L002'])
        self.brief('001-a-a1.md', ['L001'])
        self.brief('002-b.md', ['L001'])
        self.brief('003-c.md', ['L001'])
        self.verdict(
            '001-a-v1.md',
            'clean',
            '001',
            'L001: held\n- `L002`: hit the CLI path again\n',
        )
        self.verdict('002-b-v1.md', 'reject', '002')
        self.verdict('002-b-v2.md', 'clean', '002')
        self.assertEqual(
            self.scores(),
            [
                ('L001', '001', 'held', ''),
                ('L001', '002', 'hit', 'broke'),
                ('L002', '001', 'hit', 'the CLI path again'),
            ],
        )
        # Retro again replaces the rows.
        self.assertEqual(len(self.scores()), 3)

    def test_a_brief_names_the_lesson_version_it_was_given(self):
        lessons = self.store / 'LESSONS.md'
        kitchen.write_lessons(
            lessons,
            [
                kitchen.Lesson(
                    'L001',
                    'Rule.',
                    {
                        'status': 'active',
                        'kind': 'reject',
                        'class': 'verify',
                        'layer': 'verify',
                        'area': 'src',
                        'active': '2026-10-13T10:00:00Z',
                    },
                    ['e'],
                )
            ],
        )
        b = self.store / 'briefs/001-a.md'
        b.write_text('# Brief\n\n## Scope\n\nmay write:\n- src/**\n')
        kitchen.stamp_lessons(b, lessons)
        self.assertIn(
            '- L001 (reject verify, layer verify, in src, active 2026-10-13T10:00:00Z): Rule.\n',
            b.read_text(),
        )
        self.verdict('001-a-v1.md', 'clean', '001')
        self.assertEqual(
            self.scores(), [('L001@2026-10-13T10:00:00Z', '001', 'held', '')]
        )
        # A row from before versions counts from the lesson's activation.
        with self.ledger.open('a') as f:
            f.write(
                '2026-10-12T00:00:00Z\told\tlesson\thit\tL001\t005\tsrc\tx\n'
                '2026-10-14T00:00:00Z\told\tlesson\theld\tL001\t006\tsrc\t\n'
            )
        x = kitchen.read_lessons(lessons)[0]
        self.assertEqual(
            kitchen.lesson_counts(kitchen.ledger_rows(self.ledger), x),
            {'held': 2, 'hits': 0},
        )

    def test_gates_speak_only_for_gate_lessons_and_a_hit_stands(self):
        (self.store / 'briefs/001-a.md').write_text(
            '## Lessons\n\n- L001 (reject verify, layer verify, in src): r\n'
            '- L002 (gate-fail app, layer gate, in src): r\n'
        )
        (self.store / 'verdicts/001-a-v1.md').write_text(
            '# V\n\nstatus: clean\nunits: 001\nverifier: gates\n'
        )
        self.assertEqual(self.scores(), [('L002', '001', 'held', '')])
        self.brief('002-b.md', ['L001'])
        self.verdict('002-b-v2.md', 'reject', '002', 'L001: hit again\n')
        self.verdict('002-b-v10.md', 'clean', '002', 'L001: held\n')
        self.brief('003-c.md', ['L001'])
        self.verdict('003-c-v1.md', 'reject', '003', 'L001: held\n')
        self.assertEqual(
            self.scores(),
            [
                ('L001', '002', 'hit', 'again'),
                ('L001', '003', 'held', ''),
                ('L002', '001', 'held', ''),
            ],
        )

    def gate_out(self, unit, sha, check, error):
        d = self.store / 'steps/jobs' / f'{unit}-{sha}0000'
        d.mkdir(parents=True)
        (d / 'out').write_text(
            f'web fast: FAIL\n  exit 1 2.0s bun run turbo run {check}  log: /x/web-fast-0.log\n'
            f'    | @x/web:{check}: {error}\n    | @x/web:{check}: error: script "{check}" exited with code 1\n'
            f'    |  ERROR  run failed: command  exited (1)\n    | Failed:    @x/web#{check}\n'
            'policy: clean\n'
        )

    def test_gates_alone_score_a_gate_lesson_by_its_check(self):
        line = '## Lessons\n\n- L001 (gate-fail web "fast lint", layer gate, in src, active 2026-10-13T10:00:00Z): r\n'
        for b in ('001-a', '002-b', '003-c'):
            (self.store / 'briefs' / f'{b}.md').write_text(
                '# Brief\n\n## Scope\n\nmay write:\n- src/**\n\n' + line
            )
        (self.store / 'gates.tsv').write_text(
            '1\t001-a\tabc\tfail\tweb\n1\t001-a\tabd\tpass\tweb\n'
            '1\t002-b\tdef\tfail\tweb\n1\t002-b\tdeg\tpass\tweb\n'
        )
        self.gate_out(
            '001-a',
            'abc',
            'lint',
            'a.ts:1:7: error eslint(no-unused-vars): x is never used',
        )
        self.gate_out('002-b', 'def', 'typecheck', 'a.ts(1,7): error TS2322: no')
        # A verifier cannot see the steps: its word does not score a gate lesson.
        self.verdict('001-a-v1.md', 'clean', '001', 'L001: held\n')
        self.verdict('002-b-v1.md', 'clean', '002')
        (self.store / 'verdicts/003-c-v1.md').write_text(
            '# V\n\nstatus: clean\nunits: 003\nverifier: gates\n'
        )
        self.assertEqual(
            self.scores(),
            [
                (
                    'L001@2026-10-13T10:00:00Z',
                    '001',
                    'hit',
                    '@x/web:lint: a.ts:1:7: error eslint(no-unused-vars): x is never used',
                ),
                ('L001@2026-10-13T10:00:00Z', '002', 'held', ''),
                ('L001@2026-10-13T10:00:00Z', '003', 'held', ''),
            ],
        )
        gates = sorted(
            (r['class'], r['match'], r['finding'])
            for r in kitchen.ledger_rows(self.ledger)
            if r['kind'] == 'gate-fail'
        )
        self.assertEqual(
            gates,
            [
                (
                    'web',
                    'fast lint',
                    '@x/web:lint: a.ts:1:7: error eslint(no-unused-vars): x is never used',
                ),
                (
                    'web',
                    'fast typecheck',
                    '@x/web:typecheck: a.ts(1,7): error TS2322: no',
                ),
            ],
        )
        self.assertEqual(kitchen.lesson_packet([self.store / 'briefs/001-a.md']), '')

    def test_retro_keeps_rows_of_kinds_it_does_not_write(self):
        self.ledger.write_text(
            'ts\trun\tkind\tclass\tdetail\n2026-10-01T00:00:00Z\tr\tfinding\ttests\tx\n'
            '2026-10-01T00:00:00Z\tr\tsteer\tbrief\t001-a-s1\n'
        )
        self.scores()
        self.assertEqual(
            [r['kind'] for r in kitchen.ledger_rows(self.ledger)], ['finding']
        )

    def test_stamp_replaces_the_section_and_drops_it_when_nothing_applies(self):
        lessons = self.store / 'LESSONS.md'
        kitchen.write_lessons(
            lessons,
            [
                kitchen.Lesson(
                    'L001',
                    'Rule one.',
                    {
                        'status': 'active',
                        'kind': 'reject',
                        'class': 'verify',
                        'layer': 'verify',
                        'area': 'src/lib',
                    },
                    ['e'],
                ),
                kitchen.Lesson(
                    'L002',
                    'Rule two.',
                    {
                        'status': 'proposed',
                        'kind': 'reject',
                        'class': 'verify',
                        'layer': 'verify',
                        'area': 'src',
                    },
                    ['e'],
                ),
            ],
        )
        b = self.store / 'briefs/004-d.md'
        b.write_text(
            '# Brief\n\n## Scope\n\nmay write:\n- src/**\n- tests/**\n\n## Acceptance\n\n- works\n'
        )
        self.assertEqual([x.id for x in kitchen.stamp_lessons(b, lessons)], ['L001'])
        kitchen.stamp_lessons(b, lessons)
        text = b.read_text()
        self.assertEqual(text.count('## Lessons'), 1)
        self.assertTrue(
            text.startswith(
                '# Brief\n\n## Scope\n\nmay write:\n- src/**\n- tests/**\n\n## Acceptance\n\n- works\n\n## Lessons\n'
            )
        )
        self.assertEqual(kitchen.brief_lessons(b)[0]['rule'], 'Rule one.')
        kitchen.lessons_set(
            lessons, 'L001', 'retired', None, 'wrong area', '2026-10-13'
        )
        kitchen.stamp_lessons(b, lessons)
        self.assertEqual(
            b.read_text(),
            '# Brief\n\n## Scope\n\nmay write:\n- src/**\n- tests/**\n\n## Acceptance\n\n- works\n',
        )
        self.assertEqual(kitchen.lesson_packet([b]), '')


class BackfillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.runs = self.tmp / 'runs'
        state = self.tmp / 'state' / kitchen.state_key(self.tmp / 'proj.git').name
        state.mkdir(parents=True)
        self.ledger = state / 'ledger.tsv'

    def store(self, run, root):
        s = self.runs / run
        for d in ('briefs', 'steers'):
            (s / d).mkdir(parents=True)
        (s / 'pair.json').write_text(json.dumps({'slug': run, 'git_root': str(root)}))
        (s / 'briefs/001-x.md').write_text(
            '# Brief\n\n## Scope\n\nmay write:\n- src/lib/**\n- docs/adr/**\n'
        )
        (s / 'steers/001-x-s1.md').write_text(
            '# Steer\n\nkind: narrow\n\n## Direction\n\nOnly the library.\n'
        )

    def test_old_rows_gain_where_and_what_and_keep_their_time(self):
        # The worktree is gone; its path still says which repository it was.
        self.store('run-a', self.tmp / 'proj.git/wt-a')
        self.store('run-b', self.tmp / 'other.git/wt-b')
        self.ledger.write_text(
            'ts\trun\tkind\tclass\tdetail\n'
            '2026-01-01T00:00:00Z\trun-a\tfinding\ttests\tx\n'
            '2026-01-02T00:00:00Z\trun-a\tsteer\tbrief\t001-x-s1\n'
            '2026-01-02T00:00:00Z\trun-a\tcatch\treview\tmissed it\n'
            '2026-01-03T00:00:00Z\trun-b\tsteer\tbrief\t001-x-s1\n'
            '2026-01-04T00:00:00Z\trun-z\tsteer\tbrief\t001-y-s1\n'
        )
        self.assertEqual(
            kitchen.ledger_backfill(self.ledger, self.runs),
            {'runs': 1, 'rows': 1, 'missing': 1, 'foreign': 1},
        )
        rows = [
            (r['ts'][:10], r['run'], r['kind'], r['area'], r['match'])
            for r in kitchen.ledger_rows(self.ledger)
        ]
        self.assertEqual(
            rows,
            [
                ('2026-01-01', 'run-a', 'finding', '', ''),
                ('2026-01-02', 'run-a', 'steer', 'src/lib', 'narrow'),
                ('2026-01-02', 'run-a', 'catch', '', ''),
                ('2026-01-03', 'run-b', 'steer', '', ''),
                ('2026-01-04', 'run-z', 'steer', '', ''),
            ],
        )
        self.assertEqual(kitchen.ledger_backfill(self.ledger, self.runs)['runs'], 0)


class AreaTests(unittest.TestCase):
    def test_scope_entries_read_as_pair_core_reads_them(self):
        with tempfile.TemporaryDirectory() as d:
            b = Path(d) / '001-x.md'
            b.write_text(
                '## Scope\n\nmay write:\n- src/a/** — the module\n- src/b/** -- note\n- src/c/** – note\n'
                '- src/d.py (new)\n- `src/e f, g.py` — quoted\n\nmust not write:\n- src/z/**\n'
            )
            self.assertEqual(
                kitchen.brief_scope(b),
                ['src/a/**', 'src/b/**', 'src/c/**', 'src/d.py', 'src/e f, g.py'],
            )

    def test_a_scope_is_the_directories_it_writes(self):
        for globs, area in [
            (['src/**', 'tests/**'], ['src']),
            (['src/api/v2/**', 'src/api/x.py'], ['src/api']),
            (['README.md', 'docs/*.md'], ['.']),
            (['tests/unit/**'], ['tests/unit']),
            (['docs/adr/0001-x.md', 'services/api/**', 'tests/**'], ['services/api']),
            (['docs/adr/**', 'tests/**'], ['docs/adr']),
            (['lib/foo*', 'pkg/'], ['lib', 'pkg']),
            ([], []),
            (['/tmp/store/reports/001-x.md', '~/notes.md', 'src/a/**'], ['src/a']),
            (['/tmp/store/reports/001-x.md'], []),
        ]:
            self.assertEqual(kitchen.scope_area(globs), area, globs)


class DoctorTests(KitchenTests):
    def checks(self, *args, code=0):
        return {c['check']: c for c in self.data('doctor', *args, code=code)['checks']}

    def test_clean_kitchen(self):
        c = self.checks()
        self.assertEqual(
            {k: v['status'] for k, v in c.items()},
            {
                'kitchen': 'ok',
                'lessons': 'ok',
                'coverage': 'ok',
                'profiles': 'ok',
                'commands': 'ok',
                'fast gates': 'ok',
                'features': 'ok',
                'verification skill': 'warn',
                'scratch setup': 'ok',
                'verifier routine': 'ok',
                'verifier fallback': 'ok',
                'verifier escalated': 'ok',
            },
        )

    def test_doctor_names_the_resolved_verifier_for_each_class(self):
        c = self.checks()
        self.assertIn(
            "the run's current sidekick (source sidekick",
            c['verifier routine']['detail'],
        )
        self.assertEqual(c['verifier fallback']['detail'], 'none')
        self.assertEqual(
            c['verifier escalated']['detail'],
            'claude --model claude-opus-5-5 (source default); also used by verify --landing',
        )
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text(
            '[verifier]\nkind = "pi"\nargs = ["--model", "devin/swe-2", "--thinking", "high"]\n'
            '[verifier.fallback]\nkind = "claude"\nargs = ["--model", "claude-haiku-5-5"]\n'
            '[verifier.escalated]\nkind = "claude"\nargs = ["--model", "claude-sonnet-5-5"]\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        c = self.checks()
        self.assertEqual(
            c['verifier routine']['detail'],
            'pi --model devin/swe-2 --thinking high (source roster)',
        )
        self.assertEqual(
            c['verifier fallback']['detail'],
            'claude --model claude-haiku-5-5 (source roster)',
        )
        self.assertEqual(
            c['verifier escalated']['detail'],
            'claude --model claude-sonnet-5-5 (source roster) (differs from the default claude --model claude-opus-5-5); also used by verify --landing',
        )
        self.assertEqual(c['verifier escalated']['status'], 'ok')
        roster.write_text(
            '[verifier.escalated]\nkind = "claude"\nargs = ["--model", "claude-opus-5-5"]\n'
        )
        c = self.checks()
        self.assertEqual(
            c['verifier escalated']['detail'],
            'claude --model claude-opus-5-5 (source roster); also used by verify --landing',
        )
        roster.write_text(
            '[verifier.escalated]\nkind = "master"\n[verifier]\nkind = "devin"\n'
        )
        c = self.checks()
        self.assertEqual(
            c['verifier routine']['detail'], 'devin (model unspecified) (source roster)'
        )
        self.assertIn(
            "the master's own kind (source roster) (differs from the default claude --model claude-opus-5-5)",
            c['verifier escalated']['detail'],
        )

    def test_problems_fail(self):
        self.change({'tools/gen.py': 'pass\n'})
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["true"]', 'fast = ["cd src && nosuchtool --x"]'
            ).replace('verify = "unit"', 'verify = "unit"\nfeatures = ["missing.md"]'),
        )
        c = self.checks(code=3)
        self.assertEqual(c['coverage']['items'], ['tools/gen.py'])
        self.assertEqual(
            c['commands']['status'], 'ok'
        )  # cd is a builtin; only the first word counts
        self.assertEqual(c['features']['items'], ['contracts: missing.md'])
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace('fast = ["true"]', 'fast = ["nosuchtool --x"]'),
        )
        self.assertEqual(
            self.checks(code=3)['commands']['items'], ['contracts.fast: nosuchtool']
        )

    def test_verification_skill_a_required_engine_and_a_legacy_reviewer_table(self):
        self.write(
            '.agents/skills/verify-demo/SKILL.md', '---\nname: verify-demo\n---\n'
        )
        self.write(
            '.agents/kitchen.toml',
            KITCHEN
            + '\n[review]\nengine = "joo"\n[review.escalated]\nstyle = "thorough"\n',
        )
        bin_dir = Path(self.tmp.name) / 'bin'
        bin_dir.mkdir()
        self.env['PATH'] = f'{bin_dir}:/usr/bin:/bin'
        c = self.checks()
        self.assertEqual(
            c['verification skill'],
            {
                'check': 'verification skill',
                'status': 'ok',
                'detail': '.agents/skills/verify-demo',
                'items': [],
            },
        )
        self.assertEqual(c['review']['status'], 'warn')
        self.assertIn('review.escalated tuned a reviewer', c['review']['detail'])
        self.assertEqual(c['review engine']['status'], 'warn')
        self.assertIn(
            'neither joo-dev nor joo is on PATH', c['review engine']['detail']
        )
        (bin_dir / 'joo').write_text('#!/bin/sh\n')
        (bin_dir / 'joo').chmod(0o755)
        c = self.checks()
        self.assertEqual(c['review engine']['status'], 'ok')
        self.assertIn(
            f'joo ({bin_dir}/joo); land-check requires', c['review engine']['detail']
        )
        out = self.run_kitchen('validate')
        self.assertIn(
            'review.escalated are ignored: the kitchen tunes no reviewer', out
        )
        self.assertIn('review engine: joo (required', out)
        self.assertEqual(self.data('validate')['review_engine'], 'joo')
        self.write('.agents/kitchen.toml', KITCHEN + '\n[review]\nengine = "joo; rm"\n')
        self.assertIn(
            'review.engine: expected a tool name', self.run_kitchen('validate', code=1)
        )

    def test_run_records_a_baseline_and_fails_on_red(self):
        c = self.checks('--run')
        self.assertEqual(c['fast gates on HEAD']['status'], 'ok')
        self.assertIn('doctor: app pass in ', self.run_kitchen('doctor', '--run'))
        baseline = json.loads(
            next((Path(self.tmp.name) / 'state').rglob('baseline.json')).read_text()
        )
        self.assertEqual(baseline['head'], self.base)
        self.assertEqual(sorted(baseline['profiles']), ['app', 'contracts', 'docs'])
        (self.repo / 'docs/guide.md').unlink()
        (self.repo / 'docs').rmdir()
        c = self.checks('--run', code=3)
        self.assertEqual(c['fast gates on HEAD']['status'], 'fail')
        self.assertEqual(c['baseline']['status'], 'warn')


if __name__ == '__main__':
    unittest.main()


class EventRowTests(unittest.TestCase):
    """The readers of events.tsv count its first row: event() writes no header."""

    HEADER = 'ts\tepoch\tactor\tevent\tunit\tdetail\n'
    WAKE = '2026-01-01T00:00:00Z\t100\tmaster\twake\t-\tcheckin\n'
    FLAKY = '2026-01-01T00:00:01Z\t101\tsidekick\tgate-flaky\t001-x\ttests/t.py::a\n'

    def store(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / 'run'
        d.mkdir()
        (d / 'events.tsv').write_text(text)
        return d

    def test_run_counters_counts_the_first_event_of_a_headerless_store(self):
        c = kitchen.run_counters(self.store(self.WAKE))
        self.assertEqual((c['wakes'], c['started']), (1, 100))

    def test_run_counters_gives_a_legacy_header_store_the_same_counts(self):
        self.assertEqual(
            kitchen.run_counters(self.store(self.HEADER + self.WAKE)),
            kitchen.run_counters(self.store(self.WAKE)),
        )

    def test_retro_rows_counts_the_first_event_of_a_headerless_store(self):
        rows = kitchen.retro_rows(self.store(self.FLAKY), 'run', 'T')
        self.assertEqual([(r[2], r[4]) for r in rows], [('gate-flaky', '001-x')])

    def test_retro_rows_gives_a_legacy_header_store_the_same_rows(self):
        self.assertEqual(
            kitchen.retro_rows(self.store(self.HEADER + self.FLAKY), 'run', 'T'),
            kitchen.retro_rows(self.store(self.FLAKY), 'run', 'T'),
        )


class PolicyRuleTests(unittest.TestCase):
    def test_repo_test_sleep_rule_refuses_blocking_waits_and_spares_test_helpers(self):
        rules = tomllib.loads((root / '.agents/kitchen.toml').read_text())['policy'][
            'forbid'
        ]
        pattern = re.compile(
            next(r for r in rules if r['id'] == 'test-sleep')['pattern']
        )
        refused = 'time.sleep(1)|sleep 5|threading.Event().wait(0.2)|Event().wait(timeout)|await asyncio.sleep(0.1)|select.select([], [], [], 0.5)|pause.wait(0.2)|while time.monotonic() < deadline:|threading.Timer(1, fire).start()'  # kitchen-allow: test-sleep (samples of what the rule refuses)
        spared = [
            'out = self.wait(4)',
            'proc.wait(timeout=5)',
            "subprocess.Popen(['sleep', '30'])",
            "self.assertIn('x', self.wait(0))",
            'proc.wait(timeout=30)',
            'proc.communicate(timeout=60)',
            'select.select([fd], [], [], 30)',
        ]
        for line in refused.split('|'):
            self.assertTrue(pattern.search(line), line)
        for line in spared:
            self.assertIsNone(pattern.search(line), line)


class LibraryTests(KitchenTests):
    """The queries as functions of an Invocation: each returns data, prints nothing, and raises KitchenError where the command line exits nonzero."""

    def library(self):
        """The library under the test repo's environment, with the output it writes caught: the function returns data and writes none."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, self.env, clear=True))
        self.out, self.err = io.StringIO(), io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(self.out))
        stack.enter_context(contextlib.redirect_stderr(self.err))
        self.addCleanup(stack.close)
        return kitchen.Invocation(str(self.repo))

    def silent(self):
        self.assertEqual((self.out.getvalue(), self.err.getvalue()), ('', ''))

    def test_classify_returns_the_range_the_command_prints(self):
        head = self.change({'src/app.py': 'def main():\n    return 2\n'})
        want = self.data('classify', '--base', self.base, '--head', head)
        inv = self.library()
        got = kitchen.classify_change(inv, base=self.base, head=head)
        self.assertEqual(
            (got, got['profiles'], got['risk']), (want, ['app'], 'routine')
        )
        scoped = kitchen.classify_change(
            inv, paths=['src/**', '/elsewhere/report.md'], escalate=('a plan',)
        )
        self.assertEqual(
            (scoped['profiles'], scoped['outside'], scoped['escalate'], scoped['risk']),
            (['app'], ['/elsewhere/report.md'], ['a plan'], 'escalated'),
        )
        self.silent()

    def test_classify_without_a_range_is_a_kitchen_error(self):
        inv = self.library()
        with self.assertRaises(kitchen.KitchenError) as caught:
            kitchen.classify_change(inv)
        self.assertEqual(
            (str(caught.exception), caught.exception.code),
            ('give --base REV [--head REV], --working-tree, or --paths GLOB...', 1),
        )

    def test_validate_returns_the_facts_and_a_bad_kitchen_raises_what_the_command_prints(
        self,
    ):
        inv = self.library()
        found = kitchen.validate(inv)
        self.assertEqual(
            (
                found.profiles,
                found.source.kind,
                found.review_engine,
                found.landing,
                found.setup,
                found.warnings,
            ),
            (['app', 'docs', 'contracts'], 'repo', '', 'commit', [], []),
        )
        self.assertEqual(found.as_dict(), self.data('validate'))
        self.assertEqual(found.text(), self.run_kitchen('validate').rstrip('\n'))
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast =', 'fsat =', 1))
        with self.assertRaises(kitchen.KitchenError) as caught:
            kitchen.validate(inv)
        printed = self.run_kitchen('validate', code=caught.exception.code)
        self.assertEqual(printed, f'kitchen: {caught.exception}\n')
        self.silent()

    def test_validate_raises_for_a_missing_kitchen_and_for_toml_that_does_not_parse(
        self,
    ):
        inv = self.library()
        self.write('.agents/kitchen.toml', 'version = [\n')
        with self.assertRaises(kitchen.KitchenError) as broken:
            kitchen.validate(inv)
        self.assertIn('.agents/kitchen.toml: ', str(broken.exception))
        self.assertEqual(
            self.run_kitchen('validate', code=broken.exception.code),
            f'kitchen: {broken.exception}\n',
        )
        (self.repo / '.agents/kitchen.toml').unlink()
        with self.assertRaises(kitchen.KitchenError) as missing:
            kitchen.validate(inv)
        self.assertIn('no kitchen at ', str(missing.exception))
        self.assertIn('run the pstack-kitchen-setup skill', str(missing.exception))
        self.assertEqual(
            self.run_kitchen('validate', code=missing.exception.code),
            f'kitchen: {missing.exception}\n',
        )
        self.silent()

    def test_validate_returns_the_warnings_of_a_kitchen_file_that_keeps_ignored_keys(
        self,
    ):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN + '\n[review]\nengine = "joo"\nroutine = "codex"\n',
        )
        found = kitchen.validate(self.library())
        self.assertEqual(found.review_engine, 'joo')
        self.assertEqual([w.split(':')[0] for w in found.warnings], ['kitchen'])
        self.assertIn('review.routine are ignored', found.warnings[0])
        self.silent()

    def test_manifests_takes_paths_as_an_argument(self):
        inv = self.library()
        self.assertEqual(
            kitchen.manifests(
                inv, ['src/app.py', 'requirements.txt', '', 'sub/requirements.txt']
            ),
            ['requirements.txt'],
        )
        self.silent()

    def test_timing_and_the_deadline_return_their_parts(self):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'timings.tsv').write_text(
            '2\tapp\tbehavioral\tverifier\tpass\t300\t5\t1200\t1500\n'
        )
        inv = self.library()
        gates = kitchen.timing(inv, ['app', 'docs'], 'behavioral')
        self.assertEqual((gates['seconds'], gates['unmeasured']), (300, ['docs']))
        self.assertEqual(
            kitchen.timing_text(gates, 'behavioral'),
            'behavioral: 300.0s\n  app 300.0s\n  unmeasured: docs',
        )
        left = kitchen.deadline(inv, ['app'], False)
        self.assertEqual(
            (left['minutes'], left['gate_seconds'], left['unmeasured']), (45, 300, [])
        )
        self.silent()

    def test_the_median_gate_time_is_over_the_last_ten_passing_runs_and_unmeasured_under_three_while_the_deadline_still_reads_the_maximum(
        self,
    ):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        rows = (
            [('app', 1000, 'pass')] * 2
            + [('app', 60, 'pass')] * 5
            + [('app', 90, 'pass')] * 5
            + [('app', 9999, 'fail')]
        )
        rows += [
            ('slow', 500, 'pass'),
            ('slow', 700, 'pass'),
            ('slow', 600, 'pass'),
            ('few', 40, 'pass'),
            ('few', 50, 'pass'),
        ]
        (state_dir / 'timings.tsv').write_text(
            ''.join(
                f'{i}\t{p}\tbehavioral\tverifier\t{r}\t{t}\t0\t0\t{t}\n'
                for i, (p, t, r) in enumerate(rows)
            )
        )
        inv = self.library()
        medians = kitchen.median_timing(
            inv, ['app', 'slow', 'few', 'docs'], 'behavioral'
        )
        self.assertEqual(
            medians['profiles'],
            {'app': {'seconds': 75.0, 'runs': 10}, 'slow': {'seconds': 600, 'runs': 3}},
        )
        self.assertEqual(medians['unmeasured'], ['few', 'docs'])
        gates = kitchen.timing(inv, ['app', 'slow', 'few'], 'behavioral')
        self.assertEqual(
            (gates['profiles'], gates['seconds']),
            ({'app': 90.0, 'slow': 700.0, 'few': 50.0}, 840.0),
        )
        self.assertEqual(
            kitchen.deadline(inv, ['app', 'slow', 'few'], False)['gate_seconds'], 840.0
        )
        self.silent()

    def test_the_passing_runs_of_a_stage_are_oldest_first_and_skip_failures_other_stages_and_other_profiles(
        self,
    ):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            ('app', 'behavioral', 'pass', 30),
            ('app', 'behavioral', 'fail', 99),
            ('app', 'landing', 'pass', 77),
            ('docs', 'behavioral', 'pass', 5),
            ('app', 'behavioral', 'pass', 20),
        ]
        (state_dir / 'timings.tsv').write_text(
            ''.join(
                f'{i}\t{p}\t{st}\tverifier\t{r}\t{t}\n'
                for i, (p, st, r, t) in enumerate(rows)
            )
        )
        self.assertEqual(
            kitchen.passing_runs(
                Path(self.library().root), ['app', 'none'], 'behavioral'
            ),
            {'app': [30.0, 20.0], 'none': []},
        )
        self.silent()

    def test_the_brief_verify_list_is_the_commands_and_a_refused_block_raises(self):
        brief = self.repo / 'brief.md'
        brief.write_text('# Brief\n\n## Verify\n\n```bash\nmake test\ntrue\n```\n')
        inv = self.library()
        self.assertEqual(kitchen.brief_verify_list(inv, brief), ['make test', 'true'])
        brief.write_text('# Brief\n\n## Verify\n\nrun it by hand\n')
        with self.assertRaises(kitchen.KitchenError):
            kitchen.brief_verify_list(inv, brief)
        self.silent()

    def run_store(self):
        """A run store of the smallest kind that counters and the ledger read: its briefs, reports and verdicts directories and a pair.json."""
        store = Path(self.tmp.name) / 'state/pstack/kitchen/runs/demo'
        for sub in ('briefs', 'reports', 'verdicts', 'steers'):
            (store / sub).mkdir(parents=True)
        (store / 'pair.json').write_text(json.dumps({'git_root': str(self.repo)}))
        return store

    def test_the_roster_returns_the_agents_and_renders_as_the_command_prints_it(self):
        roster = Path(self.tmp.name) / 'host-roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "pi"\nargs = ["--model", "m"]\n[sidekick.fallback]\nkind = "devin"\n[verifier.escalated]\nkind = "claude"\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        self.library()
        path, agents = kitchen.roster_data()
        self.assertEqual(
            (
                agents['sidekick']['kind'],
                agents['sidekick']['args'],
                agents['sidekick']['fallback']['kind'],
            ),
            ('pi', ['--model', 'm'], 'devin'),
        )
        self.assertEqual(agents['verifier']['routine']['kind'], 'sidekick')
        self.assertEqual(agents, self.data('roster'))
        self.assertEqual(
            kitchen.roster_text(path, agents), self.run_kitchen('roster').rstrip('\n')
        )
        self.silent()

    def alternate_runs(self, *labels):
        """A runs directory with one earlier store per label, each recording that label as its sidekick choice."""
        runs = Path(tempfile.mkdtemp(dir=self.tmp.name))
        for i, label in enumerate(labels):
            (runs / f'earlier-{i}').mkdir()
            (runs / f'earlier-{i}/pair.json').write_text(
                json.dumps({'sidekick_choice': {'label': label}})
            )
        return runs

    def alternate_entries(self, count):
        return [{'kind': 'amp', 'args': ['--model', f'm/{c}']} for c in 'abc'[:count]]

    def test_the_local_model_label_agrees_with_the_pair_core_s(self):
        cases = [
            (
                ['--mode', 'claude-code', '--features', 'fast,model:claude-sonnet-5-5'],
                'amp claude-code claude-sonnet-5-5',
            ),
            (['--mode', 'claude-code'], 'amp claude-code'),
            (['-m', 'zhipuai/glm-5.3'], 'amp zhipuai/glm-5.3'),
            (['--model', 'a', '--model', 'b', '--effort', 'high'], 'amp b'),
            (['--features', 'fast'], 'amp'),
        ]
        for args, label in cases:
            with self.subTest(args=args):
                self.assertEqual(kitchen.model_label('amp', args), label)
                self.assertEqual(agents.model_label('amp', args), label)

    def test_pick_alternate_takes_the_entry_the_fewest_earlier_runs_used(self):
        self.library()
        used_first = self.alternate_runs('amp m/a')
        for run in ('r1', 'demo'):
            self.assertEqual(
                kitchen.pick_alternate(self.alternate_entries(2), used_first, run), 1
            )
        a_a_b = self.alternate_runs('amp m/a', 'amp m/a', 'amp m/b')
        for run in ('r1', 'demo'):
            self.assertEqual(
                kitchen.pick_alternate(self.alternate_entries(3), a_a_b, run), 2
            )
        self.silent()

    def test_pick_alternate_with_no_earlier_runs_hashes_the_run_name_among_the_ties(
        self,
    ):
        self.library()
        empty = self.alternate_runs()
        # zlib.crc32 of 'demo' is 3594706848 (even) and of 'r1' is 219023793 (odd).
        self.assertEqual(
            kitchen.pick_alternate(self.alternate_entries(2), empty, 'demo'), 0
        )
        self.assertEqual(
            kitchen.pick_alternate(self.alternate_entries(2), empty, 'r1'), 1
        )
        self.silent()

    def test_pick_alternate_ignores_the_run_s_own_store_and_unreadable_ones(self):
        self.library()
        runs = self.alternate_runs('amp m/a')
        (runs / 'earlier-0').rename(runs / 'demo')
        (runs / 'broken').mkdir()
        (runs / 'broken/pair.json').write_text('{')
        (runs / 'bare').mkdir()
        # The run's own earlier record of m/a does not count, so both entries are unused and crc32('demo') picks entry 0.
        self.assertEqual(
            kitchen.pick_alternate(self.alternate_entries(2), runs, 'demo'), 0
        )
        self.silent()

    def test_a_roster_that_does_not_parse_raises_what_the_command_prints(self):
        roster = Path(self.tmp.name) / 'host-roster.toml'
        roster.write_text('x = [\n')
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        self.library()
        with self.assertRaises(kitchen.KitchenError) as caught:
            kitchen.roster_data()
        self.assertEqual(
            self.run_kitchen('roster', code=caught.exception.code),
            f'kitchen: {caught.exception}\n',
        )
        self.assertTrue(str(caught.exception).startswith(f'{roster}: '))
        self.silent()

    def test_the_fleet_returns_rows_that_render_as_the_command_prints_them(self):
        store = self.run_store()
        (store / 'events.tsv').write_text('x\t1767225600\tmaster\twake\t-\tdone\n')
        runs = store.parent
        self.library()
        report = kitchen.fleet(runs, None)
        self.assertEqual([r['run'] for r in report['runs']], ['demo'])
        self.assertEqual(report['runs'][0]['wakes'], 1)
        self.assertEqual(kitchen.fleet(runs, '2027-01-01')['runs'], [])
        self.assertEqual(report, self.data('fleet', '--runs', str(runs)))
        self.assertEqual(
            kitchen.fleet_text(report),
            self.run_kitchen('fleet', '--runs', str(runs)).rstrip('\n'),
        )
        self.silent()

    def test_the_counters_and_the_state_directory_are_returned_not_printed(self):
        store = self.run_store()
        inv = self.library()
        counters = kitchen.run_counters(store)
        self.assertEqual(
            (counters['run'], counters['done_units'], counters['verified_units']),
            ('demo', 0, 0),
        )
        self.assertEqual(
            json.dumps(counters), self.run_kitchen('counters', str(store)).rstrip('\n')
        )
        self.assertEqual(
            kitchen.statedir(inv), self.run_kitchen('statedir').rstrip('\n')
        )
        self.silent()

    def test_the_ledger_takes_a_catch_and_a_retro_and_names_an_unknown_unit(self):
        store = self.run_store()
        ledger = Path(self.tmp.name) / 'ledger.tsv'
        self.library()
        kitchen.ledger_catch(
            store,
            ledger,
            'demo',
            '2026-01-01T00:00:00Z',
            'gate',
            'lint missed it',
            None,
        )
        self.assertEqual(
            kitchen.ledger_retro(store, ledger, 'demo', '2026-01-02T00:00:00Z'), 0
        )
        rows = [line.split('\t') for line in ledger.read_text().splitlines()]
        self.assertEqual(
            [(r[1], r[2], r[3], r[4]) for r in rows[1:]],
            [('demo', 'catch', 'gate', 'lint missed it')],
        )
        with self.assertRaises(kitchen.KitchenError) as caught:
            kitchen.ledger_catch(
                store, ledger, 'demo', '2026-01-03T00:00:00Z', 'gate', 'x', '009'
            )
        self.assertEqual(
            (str(caught.exception), caught.exception.code),
            (f'no brief for unit 009 in {store}/briefs', 1),
        )
        self.silent()

    def test_the_lessons_are_returned_stamped_and_packed_without_printing(self):
        inv = self.library()
        overview = kitchen.lessons_overview(inv)
        self.assertEqual(
            kitchen.lessons_text(overview), self.run_kitchen('lessons').rstrip('\n')
        )
        brief = self.repo / 'brief.md'
        brief.write_text('# Brief\n\n## Scope\n\nmay write:\n- src/app.py\n')
        self.assertEqual(kitchen.lessons_stamp(inv, brief), [])
        self.assertEqual(kitchen.lesson_packet([brief]), '')
        self.silent()

    def test_a_missing_kitchen_stops_the_lessons_with_the_message_the_command_prints(
        self,
    ):
        (self.repo / '.agents/kitchen.toml').unlink()
        inv = self.library()
        with self.assertRaises(kitchen.KitchenError) as caught:
            kitchen.lessons_overview(inv)
        self.assertEqual(
            self.run_kitchen('lessons', code=1), f'kitchen: {caught.exception}\n'
        )
        self.silent()

    def test_a_repo_kitchen_has_no_snapshot(self):
        self.assertEqual(kitchen.snapshot(self.library(), self.repo / 'snaps'), '')
        self.assertFalse((self.repo / 'snaps').exists())
        self.silent()


SIDEKICK_T0 = 1_800_000_000


def write_sidekick_store(d, models, events, steers=(), fixes=(), verdicts=()):
    """A run store holding pair.json .sidekick_models and hand-written events (epoch, actor, event, unit, detail), steer files, review-fix
    briefs by stem and verdicts (name, status, units), added to whatever the store already has."""
    for sub in ('briefs', 'reports', 'verdicts', 'steers'):
        (d / sub).mkdir(parents=True, exist_ok=True)
    pair = (
        json.loads((d / 'pair.json').read_text()) if (d / 'pair.json').exists() else {}
    )
    (d / 'pair.json').write_text(json.dumps({**pair, 'sidekick_models': models}))
    with open(d / 'events.tsv', 'a') as f:
        for e, a, v, u, det in events:
            f.write(f'x\t{e}\t{a}\t{v}\t{u}\t{det}\n')
    for stem in steers:
        (d / 'steers' / f'{stem}.md').write_text('# Steer\n')
    for stem in fixes:
        (d / 'briefs' / f'{stem}.md').write_text('# Brief\n\nplaybook: review-fix\n')
    for verdict, status, units in verdicts:
        (d / 'verdicts' / f'{verdict}.md').write_text(
            f'# Verdict\n\nstatus: {status}\nunits: {units}\n'
        )
    return d


def two_model_run():
    """The arguments of write_sidekick_store for two units on two models: unit 001 on amp m/one (two wakes, one failed gate, one steer, clean first
    time, accepted 10 minutes after dispatch) and unit 002 on amp m/two (one wake, one blocking note, a review-fix brief, rejected then clean,
    accepted 20 minutes after the review that rejected it)."""
    t0, t1 = SIDEKICK_T0, SIDEKICK_T0 + 1800
    return (
        {'001-a': 'amp m/one', '002-b': 'amp m/two', '002-b-a2': 'amp m/two'},
        [
            (t0, 'master', 'send-brief', '001-a', ''),
            (t0 + 60, 'master', 'wake', '-', 'x'),
            (t0 + 90, 'master', 'wake', '-', 'y'),
            (t0 + 120, 'sidekick', 'gate-fail', '001-a', 'pstack'),
            (t0 + 600, 'master', 'log', '-', 'review: accept 001'),
            (t1, 'master', 'send-brief', '002-b', ''),
            (t1 + 60, 'master', 'wake', '-', 'z'),
            (t1 + 120, 'master', 'note', '002-b', '1:blocking'),
            (t1 + 600, 'master', 'log', '-', 'review: reject'),
            (t1 + 1200, 'master', 'log', '-', 'review: accepted'),
        ],
        ['001-a-s1'],
        ['002-b-a2'],
        [
            ('002-b-v1', 'reject', '002-b'),
            ('002-b-v2', 'clean', '002-b'),
            ('001-a-v1', 'clean', '001-a'),
        ],
    )


class FleetTests(unittest.TestCase):
    """kitchen.py fleet and counters over fixture run stores."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.runs = self.t / 'state/pstack/kitchen/runs'
        self.env = inherited_env() | {'XDG_STATE_HOME': str(self.t / 'state')}

    def store(self, name, pair=None, events=(), briefs=(), reports=(), verdicts=()):
        d = self.runs / name
        for sub in ('briefs', 'reports', 'verdicts', 'steers'):
            (d / sub).mkdir(parents=True)
        if pair is not None:
            (d / 'pair.json').write_text(json.dumps(pair))
        if events:
            rows = ['ts\tepoch\tactor\tevent\tunit\tdetail'] + [
                f'x\t{e}\t{a}\t{v}\t-\t{det}' for e, a, v, det in events
            ]
            (d / 'events.tsv').write_text('\n'.join(rows) + '\n')
        for b in briefs:
            (d / 'briefs' / f'{b}.md').write_text('# Brief\n')
        for r, status in reports:
            (d / 'reports' / f'{r}.md').write_text(f'# Report\n\nstatus: {status}\n')
        for name_, status, units in verdicts:
            (d / 'verdicts' / f'{name_}.md').write_text(
                f'# Verdict\n\nstatus: {status}\nunits: {units}\n'
            )
        return d

    def fleet(self, *args):
        r = subprocess.run(
            [
                sys.executable,
                str(script),
                '--json',
                'fleet',
                '--runs',
                str(self.runs),
                *args,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        out = json.loads(r.stdout)
        return {x['run']: x for x in out['runs']}, {
            x['build']: x for x in out['builds']
        }

    T0 = SIDEKICK_T0

    def sidekick_store(self, name, models, events, **files):
        return write_sidekick_store(self.runs / name, models, events, **files)

    def two_model_store(self, name='a'):
        return write_sidekick_store(self.runs / name, *two_model_run())

    ONE = {
        'sidekick': 'amp m/one',
        'units': 1,
        'wakes': 2,
        'failed_gates': 1,
        'steers_and_fixes': 1,
        'rejections': 0,
        'findings': 0,
        'accept_minutes': [10.0],
        'first_pass': '1/1',
        'median_accept_minutes': 10.0,
    }
    TWO = {
        'sidekick': 'amp m/two',
        'units': 1,
        'wakes': 1,
        'failed_gates': 0,
        'steers_and_fixes': 1,
        'rejections': 1,
        'findings': 1,
        'accept_minutes': [20.0],
        'first_pass': '0/1',
        'median_accept_minutes': 20.0,
    }

    def test_a_run_is_split_by_the_sidekick_model_each_unit_was_dispatched_to(self):
        store = self.two_model_store()
        self.assertEqual(
            kitchen.run_counters(store)['by_sidekick'], [self.ONE, self.TWO]
        )

    def test_a_store_without_the_sidekick_model_record_has_no_split(self):
        store = self.store('old', pair={}, events=[(100, 'master', 'wake', 'x')])
        self.assertEqual(kitchen.run_counters(store)['by_sidekick'], [])
        (store / 'pair.json').write_text(json.dumps({'sidekick_models': {}}))
        self.assertEqual(kitchen.run_counters(store)['by_sidekick'], [])

    def fleet_command(self, *args):
        return subprocess.run(
            [sys.executable, str(script), *args, 'fleet', '--runs', str(self.runs)],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def test_the_fleet_sums_each_sidekick_model_over_its_runs(self):
        self.two_model_store('a')
        t0 = self.T0
        self.sidekick_store(
            'b',
            {'001-a': 'amp m/one'},
            [
                (t0, 'master', 'send-brief', '001-a', ''),
                (t0 + 60, 'master', 'wake', '-', 'x'),
                (t0 + 1800, 'master', 'log', '-', 'review: accept 001'),
            ],
            verdicts=[('001-a-v1', 'reject', '001-a'), ('001-a-v2', 'clean', '001-a')],
        )
        totals = json.loads(self.fleet_command('--json'))['sidekicks']
        self.assertEqual(
            totals,
            [
                {
                    'sidekick': 'amp m/one',
                    'runs': 2,
                    'units': 2,
                    'wakes': 3,
                    'failed_gates': 1,
                    'steers_and_fixes': 1,
                    'rejections': 1,
                    'findings': 0,
                    'first_pass': '1/2',
                    'median_accept_minutes': 20.0,
                },
                {
                    'sidekick': 'amp m/two',
                    'runs': 1,
                    'units': 1,
                    'wakes': 1,
                    'failed_gates': 0,
                    'steers_and_fixes': 1,
                    'rejections': 1,
                    'findings': 1,
                    'first_pass': '0/1',
                    'median_accept_minutes': 20.0,
                },
            ],
        )
        lines = self.fleet_command().splitlines()
        [head] = [line for line in lines if line.startswith('sidekick model')]
        self.assertEqual(head.split()[2], 'runs')
        rows = [line.split() for line in lines[lines.index(head) + 1 :]]
        self.assertEqual(
            rows,
            [
                ['amp', 'm/one', '2', '2', '3', '1', '1', '1', '1/2', '0', '20.0'],
                ['amp', 'm/two', '1', '1', '1', '0', '1', '1', '0/1', '1', '20.0'],
            ],
        )

    def test_the_fleet_prints_no_sidekick_table_without_the_model_record(self):
        self.store('old', pair={}, events=[(100, 'master', 'wake', 'x')])
        self.assertEqual(json.loads(self.fleet_command('--json'))['sidekicks'], [])
        self.assertNotIn('sidekick model', self.fleet_command())

    def test_verified_units_are_the_union_over_clean_non_landing_verdicts(self):
        self.store(
            'a',
            pair={
                'git_root': str(self.t),
                'sidekick': {'failovers': [{}, {}]},
                'skill_builds': [{'path': '/s/one'}],
            },
            events=[
                (100, 'master', 'init', ''),
                (160, 'master', 'wake', 'x'),
                (220, 'master', 'wake', 'verify:provider'),
                (300, 'master', 'catch', 'gate'),
                (400, 'sidekick', 'gate-flaky', 'app'),
            ],
            briefs=['001-a', '002-b', '003-c', '004-d'],
            reports=[
                ('001-a', 'done'),
                ('002-b', 'done'),
                ('003-c', 'done'),
                ('004-d', 'partial'),
            ],
            verdicts=[
                ('002-b-v1', 'clean', '001 002'),
                ('003-c-v1', 'reject', '003'),
                ('003-c-v2', 'clean', '003'),
                ('003-c-v3', 'clean', '003'),
                ('005-e-v1', 'clean (gates only: docs)', '005'),
                ('landing-v1', 'clean', 'landing'),
                ('002-b-v1-packet', 'clean', '999'),
            ],
        )
        (self.t / 'state/pstack/builds.tsv').write_text('1\tabc123\t/s/one\n')
        runs, builds = self.fleet()
        a = runs['a']
        self.assertEqual(
            (a['done_units'], a['verified_units'], a['gates_only_units'], a['landing']),
            (3, 4, 1, 'clean'),
        )
        self.assertEqual(
            (a['wakes'], a['wakes_per_verified'], a['failovers']), (2, 0.5, 2)
        )
        self.assertEqual(
            (a['verify_provider'], a['verify_missing'], a['gate_flaky'], a['catches']),
            (1, 0, 1, {'gate': 1}),
        )
        self.assertEqual(
            (a['clean_first_try'], a['build'], a['started'], a['last']),
            (2, 'abc123', 100, 400),
        )
        self.assertEqual(builds['abc123']['runs'], 1)

    def test_a_verdict_slug_containing_packet_still_counts(self):
        self.store(
            'p',
            pair={},
            events=[(10, 'master', 'init', '')],
            verdicts=[
                ('003-packet-hardening-v1', 'clean', '003'),
                ('003-packet-hardening-v1-packet', 'clean', '999'),
            ],
        )
        runs, _ = self.fleet()
        self.assertEqual(
            (runs['p']['verified_units'], runs['p']['clean_first_try']), (1, 1)
        )

    def test_last_ends_at_the_final_working_event_not_the_close_rows(self):
        self.store(
            'c',
            pair={},
            events=[
                (100, 'master', 'init', ''),
                (500, 'master', 'feedback', 'x'),
                (4100, 'master', 'close', 'master'),
                (4101, 'master', 'close', 'sidekick'),
            ],
        )
        self.store('only', pair={}, events=[(7, 'master', 'close', 'master')])
        runs, _ = self.fleet()
        self.assertEqual((runs['c']['last'], runs['only']['last']), (500, 7))

    def test_mixed_and_unstamped_runs_get_their_own_build_rows(self):
        self.store(
            'm',
            pair={'skill_builds': [{'path': '/s/one'}, {'path': '/s/two'}]},
            events=[(10, 'master', 'wake', '')],
        )
        self.store('u', pair={}, events=[(20, 'master', 'wake', '')])
        self.store('empty')
        runs, builds = self.fleet()
        self.assertEqual(
            (runs['m']['build'], runs['u']['build'], runs['empty']['build']),
            ('mixed', 'unknown', 'unknown'),
        )
        self.assertEqual(set(builds), {'mixed', 'unknown'})
        self.assertEqual(builds['unknown']['runs'], 2)
        self.assertEqual(builds['unknown']['eligible_runs'], 0)
        e = runs['empty']
        self.assertEqual(
            (
                e['done_units'],
                e['verified_units'],
                e['wakes'],
                e['wakes_per_verified'],
                e['failovers'],
                e['started'],
            ),
            (0, 0, 0, None, 0, None),
        )

    def packet(self, run, name, agent=None, cls=None, outcome=None):
        lines = ['# Verify x', '', 'verdict: v']
        lines += [f'verifier-agent: {agent}'] if agent else []
        lines += [f'verifier-class: {cls}'] if cls else []
        lines += [f'verifier-outcome: {outcome}'] if outcome else []
        (self.runs / run / 'verdicts' / f'{name}-packet.md').write_text(
            '\n'.join(lines) + '\n'
        )

    def fleet_verifiers(self):
        r = subprocess.run(
            [sys.executable, str(script), '--json', 'fleet', '--runs', str(self.runs)],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return {
            (v['agent'], v['class']): v for v in json.loads(r.stdout)['verifiers']
        }, r.stdout

    def test_fleet_counts_verifier_attempts_by_agent_class_and_outcome(self):
        self.store('a', pair={}, events=[(10, 'master', 'init', '')])
        for n, o in [
            (1, 'provider'),
            (2, 'clean'),
            (3, 'reject'),
            (4, 'invalid'),
            (5, 'missing'),
            (6, 'start-failed'),
            (7, 'running'),
            (8, 'prepared'),
            (9, 'inconclusive'),
        ]:
            self.packet('a', f'00{n}-x-v1', 'pi --model devin/swe-2', 'routine', o)
        self.packet(
            'a', '010-x-v1', 'claude --model claude-sonnet-5-5', 'landing', 'clean'
        )
        self.packet('a', '011-x-v1')
        self.store('b', pair={}, events=[(20, 'master', 'init', '')])
        self.packet('b', '001-x-v1', 'pi --model devin/swe-2', 'routine', 'clean')
        v, _ = self.fleet_verifiers()
        pi = v[('pi --model devin/swe-2', 'routine')]
        self.assertEqual(
            (
                pi['started'],
                pi['clean'],
                pi['reject'],
                pi['inconclusive'],
                pi['invalid'],
            ),
            (10, 2, 1, 1, 1),
        )
        self.assertEqual(
            (
                pi['provider'],
                pi['missing'],
                pi['start-failed'],
                pi['running'],
                pi['unknown'],
            ),
            (1, 1, 1, 2, 0),
        )
        self.assertEqual(v[('claude --model claude-sonnet-5-5', 'landing')]['clean'], 1)
        self.assertEqual(v[('unknown', 'unknown')]['unknown'], 1)

    def test_fleet_text_carries_the_caveat_and_gates_only_verdicts_make_no_attempt(
        self,
    ):
        self.store(
            'a',
            pair={},
            events=[(10, 'master', 'init', '')],
            verdicts=[('001-a-v1', 'clean (gates only; mode gates)', '001')],
        )
        r = subprocess.run(
            [sys.executable, str(script), 'fleet', '--runs', str(self.runs)],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        self.assertIn(
            'reported outcomes on different workloads, not model accuracy', r.stdout
        )
        v, _ = self.fleet_verifiers()
        self.assertEqual(v, {})

    def test_since_filters_by_the_first_event_and_fleet_writes_nothing(self):
        self.store('old', pair={}, events=[(1_000_000_000, 'master', 'init', '')])
        self.store('new', pair={}, events=[(1_790_000_000, 'master', 'init', '')])
        before = sorted(str(p) for p in self.t.rglob('*'))
        runs, _ = self.fleet('--since', '2026-01-01')
        self.assertEqual(list(runs), ['new'])
        self.assertEqual(sorted(str(p) for p in self.t.rglob('*')), before)


# A gate or Verify command that moves the checkout under the run, at a fixed point: its first run commits, its second edits a tracked file.
MOVE_CHECKOUT = (
    'n=$(cat "$FAKE/moves" 2>/dev/null || echo 0); echo $((n + 1)) >"$FAKE/moves"; '
    'case $n in 0) git commit -q --allow-empty -m moved ;; 1) echo "X = 9" >src/util.py ;; esac'
)


class KitchenScriptTests(unittest.TestCase):
    """kitchen.sh against a fake herdr, and a real repo and kitchen.py."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.fake, self.repo = t / 'fake', t / 'repo'
        self.fake.mkdir()
        self.repo.mkdir()
        roster = t / 'roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n'
            '[sidekick.fallback]\nkind = "devin"\n[consultant]\nkind = "codex"\n'
        )
        self.env = {
            **inherited_env(),
            'FAKE': str(self.fake),
            **self.mux_env(),
            'KITCHEN_VERIFY_POLL_S': '0.2',
            'KITCHEN_VERIFY_GRACE_POLL_S': '0.2',
            'KITCHEN_STEP_POLL_S': '0.2',
            'KITCHEN_VERDICT_SETTLE_S': '0',
            'PSTACK_KITCHEN_SLOT_POLL_S': '0.2',
            'PAIR_FILE_POLL_MS': '200',
            'PAIR_SETTLE_POLL_S': '0.2',
            'PAIR_STOP_POLL_S': '0.2',
            'PSTACK_KITCHEN_ROSTER': str(roster),
            'PATH': f'{root / "tests/pstack-pair/bin"}:{os.environ["PATH"]}',
            'HOME': str(t / 'home'),
            'XDG_STATE_HOME': str(t / 'state'),
            'PAIR_SETTLE_HOLD': '0',
            'PAIR_SUBMIT_CHECK_S': '0',
            'KITCHEN_QUIET_MAX_S': '20',
            'KITCHEN_QUIET_POLL_S': '0',
            'PSTACK_KITCHEN_LOAD': '1 8',
            'GIT_AUTHOR_NAME': 't',
            'GIT_AUTHOR_EMAIL': 't@t',
            'GIT_COMMITTER_NAME': 't',
            'GIT_COMMITTER_EMAIL': 't@t',
        }
        self.env.pop('CLAUDE_CODE_SESSION_ID', None)
        watch_jq(self, self.env)
        self.git('init', '-q')
        kitchen_toml = (
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]', 'fast = ["test ! -e FAIL"]'
            )
            + '\n[review]\nself = ["no-comments"]\n'
        )
        for path, text in {**FILES, '.agents/kitchen.toml': kitchen_toml}.items():
            self.write(path, text)
        self.commit('init')
        self.script = skill / 'scripts/kitchen.sh'
        self.store = t / 'state/pstack/kitchen/runs/demo'
        self.run_sh('init', 'demo')
        hook = self.fake / 'hook'
        hook.write_text(f'''#!/usr/bin/env bash
case "$2" in
Load*VERIFY*)
  packet="${{2##* VERIFY }}"
  verdict="$(sed -n 's/^verdict: //p' "$packet")"
  units="$(sed -n 's/^units: //p' "$packet")"
  mode="$(cat "$FAKE/verdict-mode" 2>/dev/null || echo clean)"
  case "$mode" in slow | grace-writes) exit 0 ;; esac
  if [ "$mode" = prime-ratelimit ] && [ "$(cut -d' ' -f2 "$FAKE/agents/$1")" = prime-agent ]; then
    printf '{{"type":"message","message":{{"role":"assistant","stopReason":"error","errorMessage":"Error: capacity issues"}}}}\n' >>"$(cat "$FAKE/sessions/$1")"
    printf 'blocked prime-agent\n' >"$FAKE/agents/$1"
    exit 0
  fi
  # swallow: a startup screen eats the first packet prompt whole.
  [ "$mode" = swallow ] && [ ! -e "$FAKE/swallowed" ] && {{ touch "$FAKE/swallowed"; exit 0; }}
  # devin-working: the verifier is busy on a command when the deadline comes.
  [ "$mode" = devin-working ] && {{ printf 'working devin\n' >"$FAKE/agents/$1"; exit 0; }}
  # ratelimit-live: a pi verifier hit its provider but its pane is still up.
  if [ "$mode" = ratelimit-live ] && [ "$(cut -d' ' -f2 "$FAKE/agents/$1")" = pi ]; then
    printf '{{"type":"message","message":{{"role":"assistant","stopReason":"error","errorMessage":"Reached free model rate limit. Your limit will reset in 50 minutes (at 11:26 UTC)."}}}}\n' >>"$(cat "$FAKE/sessions/$1")"
    exit 0
  fi
  # gone: the verifier exits without a verdict and without a provider error.
  [ "$mode" = gone ] && {{ rm -f "$FAKE/agents/$1"; exit 0; }}
  [ "$mode" = blocked ] && {{ printf 'blocked %s\\n' "$(cut -d' ' -f2 "$FAKE/agents/$1")" >"$FAKE/agents/$1"; exit 0; }}
  # ratelimit: a pi verifier stops on its provider and exits; another kind works.
  # ratelimit-all: every kind does.
  if {{ [ "$mode" = ratelimit ] && [ "$(cut -d' ' -f2 "$FAKE/agents/$1")" = pi ]; }} || [ "$mode" = ratelimit-all ]; then
    printf '{{"type":"message","message":{{"role":"assistant","stopReason":"error","errorMessage":"Reached free model rate limit. Your limit will reset in 50 minutes (at 11:26 UTC)."}}}}\n' >>"$(cat "$FAKE/sessions/$1")"
    rm -f "$FAKE/agents/$1"; exit 0
  fi
  case "$mode" in clean | swallow | ratelimit | ratelimit-live | prime-ratelimit) status=clean ;; inconclusive) status=inconclusive ;; *) status=reject ;; esac
  {{ printf '# Verdict\\n\\nstatus: %s\\nunits: %s\\n\\n## Findings\\n\\n' "$status" "$units"
     [ "$status" = reject ] && printf '1. Acceptance broke: add returns 0\\n'
     printf '\\n## Evidence\\n\\n'
     [ "$mode" = noevidence ] || printf '```\\n$ python3 -c "print(1)"\\n1\\n```\\n'
  }} >"$verdict"
  ;;
Deadline*)
  # The grace prompt: write the inconclusive verdict it names, complete, when the mode says so.
  verdict="$(sed -n 's/^Deadline reached: write \\(.*\\) now with status.*/\\1/p' <<<"$2")"
  [ "$(cat "$FAKE/verdict-mode" 2>/dev/null)" = grace-writes ] && printf '# Verdict\\n\\nstatus: inconclusive\\nunits: 001\\n\\n## Findings\\n\\nran out of time\\n\\n## Evidence\\n\\n```\\n$ ls\\nok\\n```\\n' >"$verdict"
  ;;
*" PLAN "*)
  # A plan round: the sidekick reports and the consultant advises, both agree.
  plan="${{2##* PLAN }}"
  case "$1" in
  *consultant) mkdir -p '{self.store}/advice'; printf 'status: agree\\n' >"{self.store}/advice/$(basename "$plan")" ;;
  *sidekick) printf 'status: agree\\n' >"{self.store}/reports/$(basename "$plan")" ;;
  esac
  ;;
Load*MASTER*) printf '%s\\n' "$2" >"$FAKE/master-bootstrap" ;;
Load*) [ -e "$FAKE/no-ready" ] || printf 'status: done\\n' >'{self.store}/reports/000-ready.md' ;;
esac
''')
        hook.chmod(0o755)
        self.spawn_roles()

    def spawn_roles(self):
        self.run_sh('spawn', str(self.store))

    def mux_env(self):
        """The variables that put kitchen.sh inside the fake multiplexer, with the master in its pane."""
        return {
            'HERDR_SOCKET_PATH': str(self.fake / 'herdr.sock'),
            'HERDR_ENV': '1',
            'HERDR_PANE_ID': 'p0',
        }

    def git(self, *args):
        return subprocess.run(
            ['git', *args],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()

    def write(self, path, text):
        f = self.repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def wake_rows(self):
        log = self.store / 'events.tsv' if hasattr(self, 'store') else None
        if log is None or not log.exists():
            return []
        return [
            r.split('\t', 2)[2]
            for r in log.read_text().splitlines()
            if r.split('\t')[2:4] == ['master', 'wake']
        ]

    def run_sh(self, *args, code=0, timeout=120):
        before = self.wake_rows()
        result = subprocess.run(
            [str(self.script), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if code is not None:
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        added = self.wake_rows()[len(before) :]
        # One kitchen.sh call returns to its caller once (a retry it execs is the same return), so it logs at most one master wake.
        self.assertLessEqual(
            len(added),
            1,
            f'kitchen.sh {args[0]} logged {len(added)} master wake rows for one return: {added}',
        )
        return result.stdout + result.stderr

    def state(self):
        return json.loads((self.store / 'pair.json').read_text())

    def test_nest_writes_nothing_outside_amp_threads(self):
        if self.mux_env().get('PSTACK_MUX') == 'amp':
            self.skipTest('Amp threads nest; every other backend records nothing')
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.assertEqual(
            self.run_sh('nest', str(self.store)), 'nest: nothing pending\n'
        )
        self.assertFalse((self.store / 'nest-pending.tsv').exists())
        self.assertNotIn('anchor', self.state())
        self.assertNotIn('nest-pending', self.run_sh('status', str(self.store)))

    def brief(
        self,
        seq,
        slug,
        scope,
        playbook='feature',
        plan='none',
        acceptance=None,
        verify=None,
    ):
        path = self.store / 'briefs' / f'{seq}-{slug}.md'
        text = (skill / 'references/brief-template.md').read_text()
        text = (
            text.replace('{{SEQ}}', seq)
            .replace('{{SLUG}}', slug)
            .replace('{{STORE}}', str(self.store))
        )
        lines = []
        for line in text.splitlines():
            if line.startswith('playbook:'):
                line = f'playbook: {playbook}'
            elif line.startswith('plan:'):
                line = f'plan: {plan}'
            elif line.startswith('commit:'):
                line = 'commit: yes'
            elif line.startswith('timebox:'):
                line = 'timebox: 30'
            lines.append(line)
        text = '\n'.join(lines) + '\n'
        text = text.replace(
            '- {{one repo-relative path or glob per line, in full (never relative to the line above); `quote` a path that contains a comma; a note may follow after " — ". The kitchen classifies the unit from these lines.}}',
            '\n'.join(f'- {g} — scope' for g in scope),
        )
        if acceptance is not None:
            text = re.sub(
                r'(## Acceptance\n\n).*?(\n## )',
                lambda m: m[1] + '\n'.join(acceptance) + '\n' + m[2],
                text,
                count=1,
                flags=re.DOTALL,
            )
        if verify is not None:
            text = re.sub(
                r'(## Verify\n\n```bash\n).*?(\n```)',
                lambda m: m[1] + '\n'.join(verify) + m[2],
                text,
                count=1,
                flags=re.DOTALL,
            )
        text = re.sub(r'\{\{[^}]*\}\}', 'x', text, flags=re.DOTALL)
        path.write_text(text)
        return path

    def dispatch(self, brief, code=4, timeout=120):
        return self.run_sh(
            'dispatch',
            str(self.store),
            str(brief),
            '--timeout',
            '1',
            code=code,
            timeout=timeout,
        )

    def done(self, brief, head):
        (self.store / 'reports' / brief.name).write_text(
            f'# Report\n\nstatus: done\nhead: {head}\n'
        )

    def test_a_dispatch_records_the_units_sidekick_model(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        state = self.state()
        self.assertEqual(state['dispatch']['sidekick'], 'pi devin/swe-2')
        self.assertEqual(state['sidekick_models'], {'001-util': 'pi devin/swe-2'})

    def test_a_failover_changes_the_label_of_the_next_unit_only(self):
        first = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(first)
        self.run_sh('failover', str(self.store), '--reason', 'provider-error')
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        self.assertEqual(self.state()['sidekick']['kind'], 'devin')
        second = self.brief('002', 'more', ['src/**', 'tests/**'])
        self.dispatch(second)
        state = self.state()
        self.assertEqual(state['dispatch']['sidekick'], 'devin')
        self.assertEqual(
            state['sidekick_models'],
            {'001-util': 'pi devin/swe-2', '002-more': 'devin'},
        )

    def kitchen_variant(self):
        spec = importlib.util.spec_from_file_location(
            'kitchen_variant_under_test', skill / 'scripts/variant.py'
        )
        if spec is None or spec.loader is None:
            raise ImportError(f'cannot load {skill / "scripts/variant.py"}')
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(root / 'skills/pstack-pair/scripts'))
        spec.loader.exec_module(module)
        return module

    def test_replies_kitchen_steps_due_is_false_unless_the_brief_is_escalated(self):
        # risk_of reads the brief's `risk:` header as written (case-sensitive, cut at no word) and steps_due is true only for `escalated`: these are the expected literals.
        module = self.kitchen_variant()
        from pstack_core.store import Context

        ctx = Context('pstack-kitchen', str(skill), '/s', 'Kitchen', (), (), 20, '')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        steps = self.store / 'steps/001-util.tsv'
        steps.parent.mkdir(exist_ok=True)
        steps.write_text('1\tabc\t1\t\tone\n2\tdef\t2\t\ttwo\n')
        text = re.sub(r'^steps:.*\n', '', b.read_text(), flags=re.M)
        for header, risk, due in (
            ('risk: routine', 'routine', False),
            ('risk: escalated', 'escalated', True),
            ('risk: escalated (contract X changes)', 'escalated', True),
            ('risk:', '', False),
            ('risk: Escalated', 'Escalated', False),
            ('risk:\tescalated\tfor a contract', 'escalated', True),
            ('risk: escalated: contract X changes', 'escalated', True),
            ('risk: routine: tests only', 'routine', False),
            ('risk: :', '', False),
        ):
            b.write_text(re.sub(r'^risk:.*$', header, text, flags=re.M))
            self.assertEqual(module.risk_of(str(b)), risk, header)
            self.assertEqual(
                module.VARIANT().steps_due(ctx, str(self.store), str(b)), due, header
            )
        # `steps:` only lowers an escalated unit to its gates; `master` and no line leave it due
        for steps, risk, due in (
            ('steps: gates\n', 'escalated', False),
            ('steps: master\n', 'escalated', True),
            ('', 'escalated', True),
            ('steps: master\n', 'routine', False),
            ('steps: gates\n', 'routine', False),
        ):
            stepped = re.sub(r'^risk:.*\n', f'risk: {risk}\n{steps}', text, flags=re.M)
            b.write_text(stepped)
            self.assertEqual(
                module.VARIANT().steps_due(ctx, str(self.store), str(b)),
                due,
                (steps, risk),
            )
        b.write_text(re.sub(r'^risk:.*\n', '', text, flags=re.M))
        self.assertEqual(
            (
                module.risk_of(str(b)),
                module.VARIANT().steps_due(ctx, str(self.store), str(b)),
            ),
            ('', False),
        )
        self.assertFalse(module.VARIANT().steps_due(ctx, str(self.store), ''))
        # classify (kitchen_core) and steps_due read the header through the one function
        module.load_kitchen_core()
        self.assertIs(
            importlib.import_module('kitchen_core.classify').risk_of, module.risk_of
        )

    def test_verify_job_is_the_brief_name_the_head_prefix_and_the_crc32_of_the_verify_block(
        self,
    ):
        module = self.kitchen_variant()
        # the expected numbers are python3 zlib.crc32 of the Verify block's bytes, each line ended by a newline, with the blank line before the next heading
        blocks = {
            'empty': (b'', 852952723),
            'no trailing newline': (b'true', 2703426882),
            'plain': (b'python3 tests/run.py -k x\ngit diff --exit-code\n', 2501220589),
            'non-ascii': ('echo \u00e9 \u2192 \U0001f600\n'.encode(), 1464077100),
            'over 4 KiB': (
                b'echo ' + b'x' * 5000 + b'\n' + b'true\n' * 900,
                1036027346,
            ),
        }
        for name, (block, cksum) in blocks.items():
            brief = Path(self.tmp.name) / f'{name.replace(" ", "-")}-001-x.md'
            brief.write_bytes(
                b'# Brief\n\n## Goal\n\ng\n\n## Verify\n'
                + block
                + b'\n## Forbidden\n\nnone\n'
            )
            sha = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'
            self.assertEqual(
                module.verify_job(BARE_CTX, '/store', str(brief), sha),
                f'/store/steps/jobs/{brief.stem}-verify-a1b2c3d4e5f6-{cksum}',
                name,
            )
        bare = Path(self.tmp.name) / '002-y.md'
        bare.write_bytes(b'# Brief\n\n## Verify\n\ntrue')
        self.assertEqual(
            module.verify_job(BARE_CTX, '/store', str(bare), 'abc'),
            '/store/steps/jobs/002-y-verify-abc-2002284698',
            'a Verify block that ends the file',
        )

    def test_kitchen_report_ready_adds_the_verify_job_to_the_pairs_readiness(self):
        # the why strings below are the literals an agent reads when report_ready says a done report is not ready.
        module = self.kitchen_variant()
        ready = lambda path: module.VARIANT().report_ready(
            BARE_CTX, str(self.store), str(path)
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('work')
        report = self.store / 'reports' / b.name
        self.done(b, head)
        job = Path(module.verify_job(BARE_CTX, str(self.store), str(b), head))
        short = f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)"
        got = ready(report)
        self.assertEqual(
            (got.ready, got.open, got.why), (False, '', short), 'rc missing'
        )
        job.mkdir(parents=True)
        (job / 'rc').write_text('1\n')
        self.assertEqual(
            (ready(report).ready, ready(report).why), (False, short), 'rc 1'
        )
        (job / 'rc').write_text('0\n')
        got = ready(report)
        self.assertEqual((got.ready, got.open, got.why), (True, '', ''), 'rc 0')
        self.done(b, 'f' * 40)
        got = ready(report)
        self.assertEqual(
            (got.ready, got.why),
            (False, 'its head: is not a commit'),
            'head not a commit',
        )
        other = self.store / 'reports/002-other.md'
        other.write_text(f'# Report\n\nstatus: done\nhead: {head}\n')
        self.assertTrue(ready(other).ready, "not the dispatched unit's report")
        self.done(b, head)
        (self.store / 'notes').mkdir(exist_ok=True)
        (self.store / 'notes/001-util-n1.md').write_text('# N\n\nstatus: blocking\n')
        got = ready(report)
        self.assertEqual(
            (got.ready, got.open, got.why),
            (False, str(self.store / 'notes/001-util-n1.md'), 'open blocking notes n1'),
            'the pair says no first',
        )

    def test_a_dispatch_with_a_number_flag_that_is_no_whole_number_sends_nothing(self):
        """--max, --timeout and --every are read before the command changes anything: `dispatch --max -5` ends with status 1 and the usage error, and
        no brief is recorded and nothing is sent."""
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        before = self.state()
        calls = (
            (self.fake / 'calls.log').read_text()
            if (self.fake / 'calls.log').exists()
            else ''
        )
        for flag, word, unit in (
            ('--max', '-5', 'minutes'),
            ('--max', 'x', 'minutes'),
            ('--timeout', '1.5', 'milliseconds'),
            ('--every', '1+1', 'minutes'),
        ):
            out = self.run_sh('dispatch', str(self.store), str(b), flag, word, code=1)
            self.assertIn(f'{flag} must be a whole number of {unit}', out)
            self.assertEqual(self.state(), before, (flag, word))
            after = (
                (self.fake / 'calls.log').read_text()
                if (self.fake / 'calls.log').exists()
                else ''
            )
            self.assertNotIn('agent prompt', after[len(calls) :], (flag, word))
        for flag in ('--max', '--timeout', '--every'):
            out = self.run_sh('dispatch', str(self.store), str(b), flag, code=1)
            self.assertIn(f'{flag} needs a value', out)
            self.assertEqual(self.state(), before, flag)

    def test_a_verify_job_under_the_old_cksum_key_is_ignored_and_never_collides_with_the_crc32_key(
        self,
    ):
        """Plan 043 U5 switched the Verify job key from POSIX cksum to crc32. A completed job directory under the old key (rc 0) does not make a
        done report ready and is not the directory finish uses now, so the first finish after the switch runs the Verify block again."""
        module = self.kitchen_variant()
        ready = lambda path: module.VARIANT().report_ready(
            BARE_CTX, str(self.store), str(path)
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('work')
        self.done(b, head)
        block = module.verify_block(BARE_CTX, str(b))
        old_key = (
            subprocess.run(
                ['cksum'],
                input=block.encode(),
                capture_output=True,
                check=True,
                timeout=60,
            )
            .stdout.split()[0]
            .decode()
        )
        old_job = self.store / 'steps/jobs' / f'{b.stem}-verify-{head[:12]}-{old_key}'
        new_job = Path(module.verify_job(BARE_CTX, str(self.store), str(b), head))
        self.assertNotEqual(old_key, str(zlib.crc32(block.encode())))
        self.assertNotEqual(old_job, new_job)
        old_job.mkdir(parents=True)
        (old_job / 'rc').write_text('0\n')
        got = ready(self.store / 'reports' / b.name)
        self.assertEqual(
            (got.ready, got.why),
            (
                False,
                f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)",
            ),
        )
        self.assertFalse(new_job.exists())
        self.assertEqual((old_job / 'rc').read_text(), '0\n')

    def test_kitchen_log_records_the_decision_and_a_master_log_event(self):
        self.run_sh(
            'log',
            str(self.store),
            'review',
            'accept 001',
            'diff clean',
            'reviews/001',
            'next',
        )
        rows = (self.store / 'decisions.tsv').read_text().splitlines()
        self.assertEqual(
            rows[-1].split('\t')[1:],
            ['review', 'accept 001', 'diff clean', 'reviews/001', 'next'],
        )
        self.assertEqual(
            (self.store / 'events.tsv').read_text().splitlines()[-1].split('\t')[2:],
            ['master', 'log', '-', 'review: accept 001'],
        )
        out = self.run_sh('log', str(self.store), 'review', code=1)
        self.assertEqual(
            out,
            'kitchen.sh: usage: kitchen.sh log <store> <phase> <decision> <why> <evidence> <result>\n',
        )

    def test_kitchen_new_plan_new_brief_and_new_steer_fill_the_kitchen_templates(self):
        plan = self.run_sh('new-plan', str(self.store), 'shape').strip()
        brief = self.run_sh('new-brief', str(self.store), 'shape').strip()
        self.assertEqual(
            (plan, brief),
            (f'{self.store}/plans/001-shape.md', f'{self.store}/briefs/002-shape.md'),
        )
        self.assertEqual(Path(plan).read_text().splitlines()[0], '# Plan 001: shape')
        self.assertEqual(Path(brief).read_text().splitlines()[0], '# Brief 002: shape')
        steer = self.run_sh('new-steer', str(self.store), '002').strip()
        self.assertEqual(steer, f'{self.store}/steers/002-shape-s1.md')
        self.assertEqual(
            Path(steer).read_text().splitlines()[:3],
            ['# Steer 002-s1: shape', '', f'brief: {self.store}/briefs/002-shape.md'],
        )

    def test_kitchen_pause_reaches_the_sidekick_at_progress_until_resume(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        progress = self.store / 'progress/001-util.md'
        self.assertEqual(
            self.run_sh('progress', str(self.store), 'step 1 done').splitlines(),
            [str(progress)],
        )
        self.assertRegex(progress.read_text(), r'^\d\d:\d\d step 1 done\n$')
        self.assertIn(
            'paused ', self.run_sh('pause', str(self.store), '--reason', 'lunch')
        )
        out = self.run_sh('progress', str(self.store), 'step 2 done').splitlines()
        self.assertEqual(out[0], str(progress))
        self.assertTrue(out[1].startswith('PAUSE: lunch. This is your safe point'), out)
        self.assertEqual(
            self.run_sh('resume', str(self.store)), f'resumed {self.store}\n'
        )
        self.assertEqual(
            self.run_sh('progress', str(self.store), 'step 3 done').splitlines(),
            [str(progress)],
        )

    def test_the_help_lists_log_with_its_six_arguments_and_errors_name_the_script_that_printed_them(
        self,
    ):
        self.assertIn(
            '  log <store> <phase> <decision> <why> <evidence> <result>\n',
            self.run_sh('help'),
        )
        # a ported command run through kitchen.sh, a bash `die` of kitchen.sh's own, and the same command through pair.sh
        self.assertEqual(
            self.run_sh('notes', code=1),
            'kitchen.sh: usage: kitchen.sh notes <store>\n',
        )
        self.assertEqual(
            self.run_sh('verify', code=1).splitlines()[0].split(':')[0], 'kitchen.sh'
        )
        pair = skill.parent / 'pstack-pair/scripts/pair.sh'
        r = subprocess.run(
            [str(pair), 'notes'],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(
            (r.returncode, r.stderr), (1, 'pair.sh: usage: pair.sh notes <store>\n')
        )

    def test_kitchen_notes_lists_a_blocking_note_until_a_step_resolves_it_and_followups_are_tagged(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        base = self.state()['dispatch']['head']
        self.write('src/util.py', 'X = 2\n')
        c1 = self.commit('one')
        self.write('src/util.py', 'X = 3\n')
        c2 = self.commit('two')
        steps = self.store / 'steps/001-util.tsv'
        steps.parent.mkdir(exist_ok=True)
        steps.write_text(f'1\t{c1}\t1\t\tone\n2\t{c2}\t2\t\ttwo\n')
        draft = Path(
            self.run_sh('new-note', str(self.store), '001', '--through', '1').strip()
        )
        self.assertEqual(draft, self.store / 'notes/001-util-n1.md.draft')
        self.assertIn(f'range: {base[:9]}..{c1[:9]}\n', draft.read_text())
        self.assertIn(
            'unfilled placeholders',
            self.run_sh('note', str(self.store), str(draft), code=1),
        )
        draft.write_text(
            '# Note 001-util n1\n\nunit: 001-util\nthrough: step 1\nstatus: blocking\n\n## Blocking\n\n'
            '- src/util.py:1, X is wrong, expected 3\n\n## Follow-ups\n\n- rename X\n- none\n'
        )
        self.assertEqual(
            self.run_sh('note', str(self.store), str(draft)),
            f'published {draft.with_suffix("")} (blocking)\n',
        )
        self.assertFalse(draft.exists())
        self.assertEqual(
            (self.store / 'followups.md').read_text(), '- [001-util n1] rename X\n'
        )
        self.assertIn(
            'master\tnote\t001-util\t1:blocking\n',
            (self.store / 'events.tsv').read_text(),
        )
        out = self.run_sh('notes', str(self.store))
        self.assertIn('notes: 1 open.', out)
        self.assertIn(
            f'\n=== n1 ({self.store}/notes/001-util-n1.md)\n\n- src/util.py:1, X is wrong, expected 3\n',
            out,
        )
        steps.write_text(f'1\t{c1}\t1\t\tone\n2\t{c2}\t2\tn1\ttwo\n')
        self.assertEqual(
            self.run_sh('notes', str(self.store)),
            'notes: none open; go on with the next step\n',
        )
        second = self.run_sh('new-note', str(self.store), '001').strip()
        self.assertEqual(second, f'{self.store}/notes/001-util-n2.md.draft')
        self.assertIn(f'range: {c1[:9]}..{c2[:9]}\n', Path(second).read_text())
        self.assertEqual(
            self.run_sh('new-note', str(self.store), '001', '--through', '1', code=1),
            'kitchen.sh: unit 001 is reviewed through step 1 already; wait for the next steps\n',
        )

    def test_kitchen_note_after_the_report_names_the_fix_brief_and_the_covers_verification(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        draft = self.store / 'notes/001-util-n1.md.draft'
        draft.parent.mkdir(exist_ok=True)
        draft.write_text(
            '# Note 001-util n1\n\nunit: 001-util\nthrough: step 1\nstatus: blocking\n\n## Blocking\n\n- src/util.py:1, X is wrong\n\n## Follow-ups\n\n- none\n'
        )
        (self.store / 'reports').mkdir(exist_ok=True)
        (self.store / 'reports/001-util.md').write_text('# R\n\nstatus: partial\n')
        out = self.run_sh('note', str(self.store), str(draft))
        self.assertEqual(
            out,
            f'published {draft.with_suffix("")} (blocking)\n'
            'kitchen.sh: note: unit 001 already reported partial; the sidekick will not read this note: write a fix brief and verify it with --covers 001\n',
        )

    def test_kitchen_status_lists_the_three_roles_the_advice_column_and_saves_status_md(
        self,
    ):
        out = self.run_sh('status', str(self.store))
        lines = out.splitlines()
        self.assertEqual(lines[0], '# Kitchen status')
        roles = [l for l in lines if re.match(r'(sidekick|verifier|consultant): ', l)]
        self.assertEqual(
            [l.split(':')[0] for l in roles], ['sidekick', 'verifier', 'consultant']
        )
        self.assertRegex(roles[0], r'^sidekick: demo-sidekick \(\S+\) idle$')
        self.assertEqual(roles[1], 'verifier: demo-verifier (unassigned) absent')
        self.assertEqual(roles[2], 'consultant: demo-consultant (unassigned) absent')
        self.assertIn(
            '| seq | kind | unit | report | advice | review |\n|---|---|---|---|---|---|\n',
            out,
        )
        self.assertEqual((self.store / 'status.md').read_text(), out)
        self.brief('001', 'util', ['src/**', 'tests/**'])
        row = [
            l
            for l in self.run_sh('status', str(self.store)).splitlines()
            if l.startswith('| 001 ')
        ]
        self.assertEqual(row, ['| 001 | brief | util | none | none | none |'])

    def test_kitchen_status_names_a_role_that_has_no_recorded_name_by_its_pane_alone(
        self,
    ):
        pair = json.loads((self.store / 'pair.json').read_text())
        for role in ('verifier', 'consultant'):
            pair[role].pop('name')
        (self.store / 'pair.json').write_text(json.dumps(pair))
        roles = [
            l
            for l in self.run_sh('status', str(self.store)).splitlines()
            if re.match(r'(verifier|consultant): ', l)
        ]
        self.assertEqual(
            roles, ['verifier: (unassigned) absent', 'consultant: (unassigned) absent']
        )

    def scratch_stdout(self, *args):
        """stdout alone, as kitchen.sh reads it with | tail -1."""
        r = subprocess.run(
            [str(self.script), 'scratch', str(self.store), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_kitchen_scratch_makes_a_worktree_at_the_sha_and_removes_it(self):
        self.write('src/util.py', 'X = 2\n')
        sha = self.commit('scratch head')
        self.write('src/util.py', 'X = 99\n')
        path = Path(self.scratch_stdout('001-x-review', '--at', sha).splitlines()[-1])
        self.assertEqual(self.git('-C', str(path), 'rev-parse', 'HEAD'), sha)
        self.assertEqual((path / 'src/util.py').read_text(), 'X = 2\n')
        self.assertEqual(self.state()['scratch'], ['001-x-review'])
        self.assertEqual(os.readlink(self.store / 'scratch/001-x-review'), str(path))
        self.assertEqual(
            self.scratch_stdout('001-x-review', '--at', sha).splitlines()[-1], str(path)
        )
        self.assertEqual(
            self.run_sh('scratch', str(self.store), '001-x-review', '--remove'),
            f'removed {path}\n',
        )
        self.assertEqual(
            self.run_sh('scratch', str(self.store), '001-x-review', '--remove'),
            f'removed {path}\n',
        )
        self.assertFalse(path.exists())
        self.assertFalse((self.store / 'scratch/001-x-review').is_symlink())
        self.assertEqual(self.state()['scratch'], [])
        live = Path(self.scratch_stdout('002-x-live').splitlines()[-1])
        self.assertEqual((live / 'src/util.py').read_text(), 'X = 99\n')
        self.run_sh('scratch', str(self.store), '002-x-live', '--remove')

    def colocate(self):
        self.write('src/util.py', 'X = 5\n')
        head = self.commit('base')
        subprocess.run(
            ['jj', 'git', 'init', '--colocate'],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        return head

    def jj_listing(self):
        return subprocess.run(
            ['jj', 'workspace', 'list'],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout

    @unittest.skipUnless(shutil.which('jj'), 'jj not on PATH')
    def test_a_jj_scratch_is_a_git_work_tree_at_the_commit(self):
        head = self.colocate()
        config = self.git('config', '--list')
        index = (self.repo / '.git/index').read_bytes()
        path = Path(self.scratch_stdout('001-x-verify', '--at', head).splitlines()[-1])

        def git_in(*args):
            return subprocess.run(
                ['git', *args],
                cwd=path,
                env=self.env,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout.strip()

        self.assertEqual(
            Path(git_in('rev-parse', '--show-toplevel')).resolve(), path.resolve()
        )
        self.assertEqual(git_in('rev-parse', 'HEAD'), head)
        self.assertEqual(git_in('status', '--porcelain'), '')
        self.assertEqual((path / 'src/util.py').read_text(), 'X = 5\n')
        self.assertIn('scratch-001-x-verify', self.jj_listing())
        self.assertIn(str(path), self.git('worktree', 'list'))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('config', '--list'), config)
        self.assertEqual((self.repo / '.git/index').read_bytes(), index)
        self.assertEqual(
            self.run_sh('scratch', str(self.store), '001-x-verify', '--remove'),
            f'removed {path}\n',
        )
        self.assertFalse(path.exists())
        self.assertNotIn('/pstack/scratch/', self.git('worktree', 'list'))
        self.assertNotIn('scratch-', self.jj_listing())

    @unittest.skipUnless(shutil.which('jj'), 'jj not on PATH')
    def test_init_in_a_jj_workspace_without_git_links_a_git_worktree_and_reinit_refreshes_git_root(
        self,
    ):
        head = self.colocate()
        ws = Path(self.tmp.name) / 'ws'
        subprocess.run(
            ['jj', 'workspace', 'add', str(ws)],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )

        def run_in_ws(*args):
            done = subprocess.run(
                args,
                cwd=ws,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return done.stdout + done.stderr

        out = run_in_ws(str(self.script), 'init', 'jjws')
        self.assertIn(f'git: linked a worktree of {self.repo.resolve()}/.git at ', out)
        store = self.store.parent / 'jjws'
        state = json.loads((store / 'pair.json').read_text())
        self.assertEqual(Path(state['git_root']).resolve(), ws.resolve())
        self.assertEqual(run_in_ws('git', 'rev-parse', 'HEAD').strip(), head)
        self.assertEqual(run_in_ws('git', 'status', '--porcelain'), '')
        run_in_ws('jj', 'status')
        state['git_root'] = ''
        (store / 'pair.json').write_text(json.dumps(state))
        out = run_in_ws(str(self.script), 'init', 'jjws')
        self.assertNotIn('git: linked', out)
        state = json.loads((store / 'pair.json').read_text())
        self.assertEqual(Path(state['git_root']).resolve(), ws.resolve())
        (ws / 'src/util.py').write_text('X = 6\n')
        run_in_ws('jj', 'commit', '-m', 'a jj step')
        stepped = run_in_ws('jj', 'log', '--no-graph', '-r', '@-', '-T', 'commit_id')
        self.assertEqual(run_in_ws('git', 'rev-parse', 'HEAD').strip(), head)
        from kitchen_core.units import follow_jj

        follow_jj(str(ws))
        self.assertEqual(run_in_ws('git', 'rev-parse', 'HEAD').strip(), stepped)
        self.assertEqual(run_in_ws('git', 'status', '--porcelain'), '')
        self.assertEqual((ws / 'src/util.py').read_text(), 'X = 6\n')
        (ws / 'src/util.py').write_text('X = 7\n')
        run_in_ws('git', 'commit', '-qam', 'a git step')
        ahead = run_in_ws('git', 'rev-parse', 'HEAD').strip()
        follow_jj(str(ws))
        self.assertEqual(run_in_ws('git', 'rev-parse', 'HEAD').strip(), ahead)

    @unittest.skipUnless(shutil.which('jj'), 'jj not on PATH')
    def test_a_failed_jj_scratch_leaves_no_git_worktree(self):
        self.colocate()
        stubs = Path(self.tmp.name) / 'stubs'
        stubs.mkdir()
        real = shutil.which('jj')
        jj = stubs / 'jj'
        jj.write_text(
            f'#!/bin/sh\ncase "$*" in *"workspace add"*) exit 1 ;; esac\nexec {real} "$@"\n'
        )
        jj.chmod(0o755)
        self.env['PATH'] = f'{stubs}:{self.env["PATH"]}'
        out = self.run_sh('scratch', str(self.store), '001-x-verify', code=2)
        self.assertIn('jj workspace add failed', out)
        self.assertNotIn('/pstack/scratch/', self.git('worktree', 'list'))
        self.assertNotIn('scratch-', self.jj_listing())

    def test_kitchen_scratch_made_for_a_store_with_a_trailing_slash_or_a_relative_name_is_the_one_removed(
        self,
    ):
        self.write('src/util.py', 'X = 2\n')
        sha = self.commit('scratch head')
        for spelling, cwd in (
            (str(self.store), self.repo),
            (f'{self.store}/', self.repo),
            (self.store.name, self.store.parent),
        ):
            made = subprocess.run(
                [str(self.script), 'scratch', spelling, '001-x-review', '--at', sha],
                cwd=cwd,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
            path = made.stdout.splitlines()[-1]
            self.assertTrue(os.path.isdir(path), spelling)
            expect_dir = self.store.parent.name
            self.assertTrue(
                path.endswith(
                    f'/pstack/scratch/{expect_dir}-{self.store.name}/001-x-review'
                ),
                f'{spelling}: {path}',
            )
            gone = subprocess.run(
                [str(self.script), 'scratch', spelling, '001-x-review', '--remove'],
                cwd=cwd,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(
                (gone.returncode, gone.stdout), (0, f'removed {path}\n'), spelling
            )
            self.assertFalse(os.path.exists(path), spelling)

    def test_kitchen_scratch_names_the_directory_it_could_not_create_in_its_own_words(
        self,
    ):
        shutil.rmtree(self.store / 'scratch', ignore_errors=True)
        (self.store / 'scratch').write_text('in the way')
        out = self.run_sh('scratch', str(self.store), '001-x-review', code=1)
        self.assertEqual(
            out, f'kitchen.sh: cannot create {self.store}/scratch: File exists\n'
        )

    def test_kitchen_metrics_reports_wall_time_and_review_latency_from_events_tsv(self):
        rows = [
            ('1000', 'master', 'send-brief', '001-util', ''),
            ('1600', 'sidekick', 'report', '001-util', ''),
            ('1700', 'master', 'wake', '001-util', 'report:done'),
            ('1760', 'master', 'log', '-', 'review: accepted 001'),
        ]
        (self.store / 'events.tsv').write_text(
            ''.join(
                f'2026-01-01T00:00:00Z\t{epoch}\t{actor}\t{ev}\t{unit}\t{detail}\n'
                for epoch, actor, ev, unit, detail in rows
            )
        )
        self.env['PAIR_METRICS_NOW'] = '1800'
        out = self.run_sh('metrics', str(self.store)).splitlines()
        self.assertEqual(out[0], 'wall: 13m from first event to last')
        self.assertIn(
            'master: 1 wakes, 0 of them check-ins; review latency median 3m over 1 reviews',
            out,
        )
        self.assertIn(
            'sidekick: busy 10m (78% of wall), idle 0m across 0 gaps; 1 briefs (median 10m), 0 plan responses (0m)',
            out,
        )
        self.assertIn('verdicts: accept 1', out)

    def test_kitchen_scan_secrets_exits_1_naming_the_file_and_line_and_0_on_a_clean_store(
        self,
    ):
        clean = self.run_sh('scan-secrets', str(self.store))
        self.assertEqual(clean, f'no secrets in {self.store}\n')
        key = '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----'
        (self.store / 'notes').mkdir(exist_ok=True)
        (self.store / 'notes/001-n1.md').write_text(f'fine\n{key}\n')
        out = self.run_sh('scan-secrets', str(self.store), code=1)
        self.assertEqual(
            out,
            'secret: notes/001-n1.md:2: private-key; redact it (write [REDACTED: private-key]) and run the command again\n',
        )
        self.assertNotIn(key, out)

    def test_init_puts_the_kitchen_in_the_standing_orders(self):
        orders = (self.store / 'standing-orders.md').read_text()
        self.assertIn(
            'runs these skills on its diff and fixes what they find: no-comments',
            orders,
        )
        self.assertIn('Landing goes as far as `commit`', orders)
        self.run_sh('init', 'demo')
        self.assertEqual((self.store / 'standing-orders.md').read_text(), orders)

    def test_init_prepares_a_linked_worktree_with_the_scratch_setup_as_the_sidekick(
        self,
    ):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text()
            + '\n[scratch]\nsetup = ["printf %s \\"$PSTACK_KITCHEN_ROLE\\" > installed"]\n'
        )
        self.commit('declare setup')
        self.assertNotIn('worktree:', self.run_sh('init', 'demo'))
        self.assertFalse((self.repo / 'installed').exists())
        wt = Path(self.tmp.name) / 'wt'
        self.git('worktree', 'add', '-q', '--detach', str(wt), 'HEAD')

        def init(slug):
            return subprocess.run(
                [str(self.script), 'init', slug],
                cwd=wt,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=60,
            )

        r = init('wt')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(
            'worktree: prepared with [scratch].setup (setup: ok (1 commands', r.stdout
        )
        self.assertEqual((wt / 'installed').read_text(), 'sidekick')
        timings = list(
            (Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob(
                '*/timings.tsv'
            )
        )
        self.assertFalse(any('\tsetup\t' in f.read_text() for f in timings))
        (wt / '.agents/kitchen.toml').write_text(
            toml.read_text().replace('> installed', '; exit 3')
        )
        r = init('wt2')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(
            'this run is in a linked worktree and its [scratch].setup failed', r.stderr
        )

    def test_spawn_takes_the_roster_and_classify_stamps_the_brief(self):
        sidekick = self.state()['sidekick']
        self.assertEqual(
            (sidekick['kind'], sidekick['fallback']['kind']), ('pi', 'devin')
        )
        self.assertEqual(sidekick['start_args'][-2:], ['--model', 'devin/swe-2'])
        b = self.brief('001', 'util', ['src/util.py'])
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('risk: routine', out)
        text = b.read_text()
        self.assertIn(
            'risk: routine\nprofiles: app\nverify: batch by routine verifier\n', text
        )
        e = self.brief('002', 'api', ['contracts/**'])
        out = self.run_sh('classify', str(self.store), str(e))
        self.assertIn('escalate: escalate path: contracts/api.json', out)
        self.assertIn('next: kitchen.sh consultant', out)

    def test_classify_keeps_the_masters_reason_on_the_risk_line_when_the_class_is_unchanged(
        self,
    ):
        # B6: an escalated brief that classifies escalated keeps `escalated (contract change)`; one whose class changes is rewritten.
        e = self.brief('002', 'api', ['contracts/**'])
        e.write_text(
            re.sub(r'(?m)^risk:.*$', 'risk: escalated (contract change)', e.read_text())
        )
        self.run_sh('classify', str(self.store), str(e))
        self.assertIn('\nrisk: escalated (contract change)\n', e.read_text())
        r = self.brief('003', 'api2', ['contracts/**'])
        r.write_text(
            re.sub(r'(?m)^risk:.*$', 'risk: routine (it is small)', r.read_text())
        )
        out = self.run_sh('classify', str(self.store), str(r))
        self.assertIn('risk: escalated', out)
        self.assertIn('\nrisk: escalated\n', r.read_text())
        self.assertNotIn('it is small', r.read_text())

    def generate_pair(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text()
            + '\n[[generated]]\nsources = ["src/**"]\noutputs = ["docs/**"]\ncommand = "make gen"\n'
        )
        self.commit('generated pair')

    def test_classify_and_dispatch_print_the_generated_outputs_warning(self):
        plain = self.run_sh(
            'classify', str(self.store), str(self.brief('001', 'util', ['src/util.py']))
        )
        self.assertNotIn('warn:', plain)
        self.generate_pair()
        out = self.run_sh(
            'classify', str(self.store), str(self.brief('002', 'lib', ['src/util.py']))
        )
        self.assertIn(
            'warn: Scope covers src/util.py but none of its generated outputs (docs/**)',
            out,
        )
        self.assertIn('(regenerate: make gen)', out)
        covered = self.run_sh(
            'classify',
            str(self.store),
            str(self.brief('003', 'both', ['src/util.py', 'docs/**'])),
        )
        self.assertNotIn('warn:', covered)
        out = self.dispatch(self.brief('004', 'disp', ['src/util.py']))
        self.assertIn('warn: Scope covers src/util.py', out)

    ACCEPTANCE_071 = [
        '- `git grep -n -E "PAIR_WAIT_|PAIR_QUIET_WAIT|quiet_flagged|has_checkin|SWALLOWED|FLAGGED|unowned:" -- skills`',
        '- `git grep -n "captured(" -- skills/pstack-kitchen/scripts/kitchen_core/wait.py skills/pstack-kitchen/scripts/kitchen_core/dispatch.py`',
        '- `python3 tests/spec_gen.py`',
    ]

    def acceptance_warns(self, acceptance, verify, seq='001'):
        brief = self.brief(
            seq, 'acc', ['src/util.py'], acceptance=acceptance, verify=verify
        )
        out = self.run_sh('classify', str(self.store), str(brief))
        return [l for l in out.splitlines() if l.startswith('warn: Acceptance')]

    def test_classify_warns_on_an_acceptance_command_verify_does_not_run(self):
        warned = self.acceptance_warns(
            self.ACCEPTANCE_071, ['python3 tests/run.py -k core']
        )
        self.assertEqual(len(warned), 3)
        self.assertEqual(
            warned[0],
            'warn: Acceptance runs git grep -n -E "PAIR_WAIT_|PAIR_QUIET_WAIT|quiet_flagged|has_checkin|SWALLOWED|FLAGGED|unowned:" -- skills, '
            'which Verify does not; add it to Verify (wrap a "prints nothing" check as test -z "$(...)")',
        )
        self.assertIn('git grep -n "captured("', warned[1])
        self.assertIn('python3 tests/spec_gen.py,', warned[2])
        fixed = [
            'test -z "$(git grep -n -E "PAIR_WAIT_|PAIR_QUIET_WAIT|quiet_flagged|has_checkin|SWALLOWED|FLAGGED|unowned:" -- skills)"',
            'test -z "$(git grep -n "captured(" -- skills/pstack-kitchen/scripts/kitchen_core/wait.py skills/pstack-kitchen/scripts/kitchen_core/dispatch.py)"',
            'python3 tests/spec_gen.py --check',
        ]
        self.assertEqual(
            self.acceptance_warns(self.ACCEPTANCE_071, fixed, seq='002'), []
        )

    def test_classify_warns_on_acceptance_commands_the_repo_gates_start_with(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'fast = ["test -f src/app.py"]', 'fast = ["bun run check"]'
            ),
        )
        self.commit('bun kitchen')
        cases = [
            ('`bun run lint`', True),
            ('`./tooling/check x`', True),
            ('`rg -n foo src`', True),
            ('`foo.py`', False),
            ('`risk: escalated`', False),
        ]
        for seq, (command, warns) in enumerate(cases, 1):
            with self.subTest(command=command):
                warned = self.acceptance_warns(
                    [f'- {command}'], ['none'], seq=f'{seq:03d}'
                )
                self.assertEqual(len(warned), int(warns), warned)

    def classify_verify(self, verify, seq='001'):
        brief = self.brief(seq, 'probe', ['src/util.py'], verify=verify)
        return self.run_sh('classify', str(self.store), str(brief))

    def test_classify_probes_the_read_only_verify_lines(self):
        self.write('file.txt', 'present\n')
        cases = [
            ('grep -q present file.txt', 'probe: passes now: grep -q present file.txt'),
            (
                'test -z "$(grep -rn present file.txt)"',
                'probe: fails now: test -z "$(grep -rn present file.txt)"',
            ),
            (
                'grep -q x missing.txt',
                'warn: Verify probe grep -q x missing.txt errored (exit 2)',
            ),
            ('test -f missing.txt', 'probe: fails now: test -f missing.txt'),
            (
                'test -n "$(grep -rn \'present\' .)"',
                'probe: passes now: test -n "$(grep -rn \'present\' .)"',
            ),
            (
                '! grep -q absent file.txt',
                'probe: passes now: ! grep -q absent file.txt',
            ),
            (
                "rg -n x -g '!bin/**' src",
                "warn: Verify glob '!bin/**' is matched from the search root; write '!**/bin/**' to exclude it at any depth",
            ),
        ]
        for seq, (command, expected) in enumerate(cases, 1):
            with self.subTest(command=command):
                out = self.classify_verify([command], seq=f'{seq:03d}')
                self.assertIn(expected, out)
        self.assertNotIn(
            'warn: Verify probe test -f',
            self.classify_verify(['test -f missing.txt'], '010'),
        )
        for glob in ("'!**/bin/**'", "'!/bin/**'"):
            with self.subTest(glob=glob):
                out = self.classify_verify([f'rg -n x -g {glob} src'], '011')
                self.assertNotIn('Verify glob', out)

    def test_classify_probe_runs_no_verify_line_that_writes_or_chains(self):
        self.write('a.txt', 'x\n')
        (self.repo / 'x').mkdir()
        never = [
            'python3 tests/run.py -k x',
            'grep x a.txt > out',
            'grep x a.txt; rm a.txt',
            'grep x a.txt && rm a.txt',
            'test -z "$(rm -rf x)"',
            "grep x <<'EOF'\nx\nEOF",
            'test -z "$(grep a b)" || rm -rf x; echo ")"',
            'grep -q x a.txt || rm a.txt',
            '! grep -q x a.txt || rm a.txt',
            'sort -ofile a.txt',
            'sort -uo file a.txt',
            'git grep -Ocat x',
            'git grep --open-files-in-pager=cat x',
            'git diff --ext-diff',
            'rg --pre=cat x',
            'rg --pre cat x',
        ]
        out = self.classify_verify(never)
        self.assertNotIn('probe:', out)
        self.assertNotIn('Verify probe', out)
        self.assertTrue((self.repo / 'a.txt').is_file())
        self.assertTrue((self.repo / 'x').is_dir())
        self.assertFalse((self.repo / 'out').exists())

    def test_acceptance_commands_count_as_run_by_verify_or_a_gate(self):
        diff = ['- `git diff --exit-code -- src`']
        self.assertEqual(self.acceptance_warns(diff, ['git diff -- src']), [])
        self.assertEqual(
            self.acceptance_warns(
                ['- `git diff -- src`'], ['git diff --exit-code -- src'], '002'
            ),
            [],
        )
        self.assertEqual(
            self.acceptance_warns(['- `test ! -e FAIL`'], ['none'], '003'), []
        )
        self.assertEqual(
            self.acceptance_warns(
                ['- `checkin` and `CheckIn` and `gitx a`'], ['none'], '004'
            ),
            [],
        )
        twice = self.acceptance_warns(
            ['- `bash a.sh`', '- again `bash  a.sh`'], ['none'], '005'
        )
        self.assertEqual(len(twice), 1)

    def test_an_acceptance_command_counts_as_run_only_at_a_command_boundary(self):
        seqs = iter(f'{n:03d}' for n in range(1, 100))

        def warns(command, verify):
            return self.acceptance_warns([f'- `{command}`'], verify, next(seqs))

        self.assertEqual(len(warns('test -f a', ['test -f ab'])), 1)
        self.assertEqual(len(warns('test -f a', ['pretest -f a'])), 1)
        self.assertEqual(len(warns('bash a.sh', ['bash a.sh.bak'])), 1)
        for verify in (
            'test -f a',
            'test -f a && echo ok',
            'test -f a; true',
            'test -f a | cat',
            'true && test -f a',
            'true;test -f a&',
            'true|test -f a|cat',
            'test -z "$(test -f a)"',
            "echo 'test -f a'",
            'echo "test -f a"',
            '(test -f a)',
        ):
            with self.subTest(verify=verify):
                self.assertEqual(warns('test -f a', [verify]), [])
        self.assertEqual(warns('test -f a', ['test -f b', 'test -f a']), [])

    def test_dispatch_and_queue_print_the_acceptance_warning(self):
        acc = ['- `bash check.sh`']
        out = self.dispatch(
            self.brief('001', 'first', ['src/**'], acceptance=acc, verify=['none'])
        )
        self.assertIn('warn: Acceptance runs bash check.sh, which Verify does not', out)
        self.generate_pair()
        queued = self.brief(
            '002',
            'next',
            ['src/util.py'],
            acceptance=['- `bash q.sh`'],
            verify=['none'],
        )
        (self.fake / 'agents/demo-sidekick').write_text('working claude\n')
        out = self.run_sh('queue', str(self.store), str(queued))
        self.assertIn('warn: Scope covers src/util.py', out)
        self.assertIn('warn: Acceptance runs bash q.sh, which Verify does not', out)

    def test_an_escalated_plan_or_header_keeps_a_routine_scope_escalated(self):
        plan = self.store / 'plans/001-util.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: util\n\nrisk: escalated\n')
        b = self.brief('001', 'util', ['src/util.py'], plan=str(plan))
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('escalate: escalated plan: 001-util.md', out)
        self.assertIn(
            'risk: escalated\nprofiles: app\nverify: unit by escalated verifier\n',
            b.read_text(),
        )
        plan.write_text('# Plan 001: util\n\nrisk: routine\n')
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('escalate: the brief says risk: escalated', out)
        self.assertIn('risk: escalated\n', b.read_text())

    def test_classify_stamps_who_reads_an_escalated_units_steps(self):
        plan = self.store / 'plans/001-util.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: util\n\nrisk: escalated\n')
        gates_line = (
            'steps: gates (escalated only by its plan; its gates prove each step)\n'
        )

        def classify(b):
            return self.run_sh('classify', str(self.store), str(b))

        def stamped(b):
            return re.findall(r'^steps: (\w+)$', b.read_text(), flags=re.M)

        # (case, scope, plan, extra header, hand edit, classifies, stamp after each, printed line)
        for case, scope, with_plan, edit, runs, want, printed in (
            (
                'escalated plan, routine Scope',
                ['src/util.py'],
                True,
                None,
                1,
                ['gates'],
                gates_line,
            ),
            (
                'escalated plan, escalate path',
                ['src/util.py', 'contracts/api.json'],
                True,
                None,
                1,
                ['master'],
                'steps: master\n',
            ),
            (
                'master wrote risk: escalated',
                ['src/util.py'],
                False,
                (r'^risk:.*$', 'risk: escalated'),
                2,
                ['master', 'master'],
                'steps: master\n',
            ),
            (
                'plan-only brief classified twice',
                ['src/util.py'],
                True,
                None,
                2,
                ['gates', 'gates'],
                gates_line,
            ),
            (
                'steps: master written by hand',
                ['src/util.py'],
                True,
                (r'^steps:.*$', 'steps: master'),
                2,
                ['master', 'master'],
                'steps: master\n',
            ),
            ('routine brief', ['src/util.py'], False, None, 1, ['none'], None),
        ):
            with self.subTest(case):
                b = self.brief(
                    '001', 'util', scope, plan=str(plan) if with_plan else 'none'
                )
                if edit:
                    b.write_text(re.sub(edit[0], edit[1], b.read_text(), flags=re.M))
                for i in range(runs):
                    out = classify(b)
                    self.assertEqual(stamped(b), [want[i]], case)
                    self.assertEqual(
                        [l for l in out.splitlines(True) if l.startswith('steps:')],
                        [printed] if printed else [],
                        case,
                    )
        # a brief classified as routine, then escalated by the master, is read by the master
        b = self.brief('002', 'later', ['src/util.py'])
        classify(b)
        self.assertEqual(stamped(b), ['none'])
        b.write_text(re.sub(r'^risk:.*$', 'risk: escalated', b.read_text(), flags=re.M))
        out = classify(b)
        self.assertEqual(stamped(b), ['master'])
        self.assertIn('steps: master\n', out)

    def use_overlays(self):
        """The playbook fixture's overlays and the docs they point into, committed to the throwaway repo."""
        shutil.copytree(
            root / 'tests/fixtures/playbooks/repo/.agents/playbooks',
            self.repo / '.agents/playbooks',
        )
        for name in ('CODING_STANDARDS.md', 'docs/DOMAIN.md'):
            self.write(
                name, (root / 'tests/fixtures/playbooks/repo' / name).read_text()
            )
        self.write('engine/lib.rs', 'fn main() {}\n')
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text().replace(
                'paths = ["src/**", "tests/**"]',
                'paths = ["src/**", "tests/**", "engine/**"]',
            )
        )
        self.commit('overlays')

    def header(self, brief, *lines):
        """Adds header lines after the brief's `playbook:` line."""
        text = brief.read_text()
        out = []
        for line in text.split('\n'):
            out.append(line)
            if line.startswith('playbook:'):
                out += lines
        brief.write_text('\n'.join(out))

    def composition(self, n='001'):
        return json.loads((self.store / 'compositions' / f'{n}.json').read_text())

    def test_classify_stamps_the_composition_and_prints_the_playbooks(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('playbooks: bug-fix + rust-engine\n', out)
        stamp = self.composition()
        self.assertEqual((stamp['version'], stamp['workflow']), (1, 'bug-fix'))
        self.assertEqual(
            [o['id'] for o in stamp['overlays']['selected']], ['rust-engine']
        )
        self.assertEqual(
            sorted(stamp['roles']), ['consultant', 'master', 'sidekick', 'verifier']
        )
        entry = next(e for e in stamp['roles']['sidekick'] if e['id'] == 'error-model')
        self.assertIn('Return a typed error', entry['text'])

    def test_classify_prints_the_masters_parts_one_per_line(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.header(b, 'with: daily-coaching')
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('playbooks: bug-fix + daily-coaching + rust-engine\n', out)
        self.assertIn('master: session-shape Session shape', out)
        for part in self.composition()['roles']['master']:
            self.assertIn(f'master: {part["id"]} ', out)

    def test_classify_prints_what_a_without_excluded(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.header(b, 'without: rust-engine — a docs-only edit')
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('playbooks: bug-fix\n', out)
        self.assertIn('excluded: rust-engine (without: a docs-only edit)\n', out)

    def test_classify_twice_writes_the_stamp_once(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.run_sh('classify', str(self.store), str(b))
        path = self.store / 'compositions/001.json'
        first = (path.read_bytes(), path.stat().st_ino)
        self.run_sh('classify', str(self.store), str(b))
        self.assertEqual((path.read_bytes(), path.stat().st_ino), first)

    def test_classify_stamps_a_different_composition_when_with_changes(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.run_sh('classify', str(self.store), str(b))
        before = (self.store / 'compositions/001.json').read_bytes()
        self.header(b, 'with: daily-coaching')
        self.run_sh('classify', str(self.store), str(b))
        after = self.composition()
        self.assertNotEqual((self.store / 'compositions/001.json').read_bytes(), before)
        self.assertEqual(
            [o['id'] for o in after['overlays']['selected']],
            ['daily-coaching', 'rust-engine'],
        )
        self.assertEqual(after['with'], ['daily-coaching'])

    def test_an_unmapped_playbook_refuses_classify_and_lists_the_mapped_names(self):
        b = self.brief('001', 'util', ['src/util.py'], playbook='old-label')
        out = self.run_sh('classify', str(self.store), str(b), code=1)
        self.assertIn("playbook 'old-label' is not mapped; mapped: ", out)
        self.assertIn('bug-fix', out)
        self.assertFalse((self.store / 'compositions/001.json').exists())

    def overlay_file(self, stem, text):
        (self.repo / '.agents/playbooks' / f'{stem}.toml').write_text(text)

    def test_a_broken_overlay_the_brief_excludes_leaves_classify_working(self):
        self.use_overlays()
        self.overlay_file('broken', 'applies = [\n')
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.header(b, 'without: broken — not mine')
        out = self.run_sh('classify', str(self.store), str(b), timeout=60)
        self.assertIn('excluded: broken (without: not mine)\n', out)
        self.assertIn('playbooks: bug-fix + rust-engine\n', out)

    def test_an_unreadable_overlay_nobody_names_is_left_out_as_unusable(self):
        self.use_overlays()
        self.overlay_file('broken', 'applies = [\n')
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        out = self.run_sh('classify', str(self.store), str(b), timeout=60)
        self.assertIn('excluded: broken (unusable: ', out)
        self.assertIn('playbooks: bug-fix + rust-engine\n', out)

    def test_an_unreadable_overlay_the_brief_names_refuses_classify(self):
        self.use_overlays()
        self.overlay_file('broken', 'applies = [\n')
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.header(b, 'with: broken')
        out = self.run_sh('classify', str(self.store), str(b), code=1, timeout=60)
        self.assertIn('overlay broken is unusable', out)

    def test_an_overlay_whose_applies_does_not_match_is_never_validated(self):
        self.use_overlays()
        self.overlay_file(
            'elsewhere',
            'schema = 1\nid = "elsewhere"\napplies = ["nowhere/**"]\n\n'
            '[[part]]\nid = "p"\nsource = { root = "repo", path = "gone.md" }\n',
        )
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        out = self.run_sh('classify', str(self.store), str(b), timeout=60)
        self.assertIn('playbooks: bug-fix + rust-engine\n', out)
        self.assertNotIn('elsewhere', out)

    def test_an_unfilled_playbook_header_gets_the_placeholder_message(self):
        b = self.brief('001', 'util', ['src/util.py'], playbook='bug-fix')
        b.write_text(
            b.read_text().replace('playbook: bug-fix', 'playbook: {{PLAYBOOK}}')
        )
        out = self.run_sh('classify', str(self.store), str(b), code=1, timeout=60)
        self.assertIn('still has unfilled placeholders', out)
        self.assertNotIn('is not mapped', out)

    def test_dispatch_writes_the_sidekicks_playbook_section_and_says_so(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        out = self.dispatch(b, timeout=60)
        text = b.read_text()
        self.assertEqual(
            text.count('## Playbook: bug-fix + rust-engine (your parts)'), 1
        )
        sidekick = self.composition()['roles']['sidekick']
        for part in sidekick:
            self.assertIn(f'- {part["id"]}: ', text)
        self.assertRegex(
            out, rf'playbook: {len(sidekick)} parts, ~\d+ tokens, 0 pending\n'
        )

    def test_dispatching_twice_leaves_one_section_and_the_same_bytes(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.dispatch(b, timeout=60)
        first = b.read_bytes()
        self.dispatch(b, timeout=60)
        self.assertEqual(b.read_bytes(), first)
        self.assertEqual(b.read_text().count('## Playbook:'), 1)

    def test_a_changed_composition_replaces_the_section(
        self,
    ):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.dispatch(b, timeout=60)
        self.header(b, 'without: rust-engine — docs only')
        self.dispatch(b, timeout=60)
        text = b.read_text()
        self.assertEqual(text.count('## Playbook:'), 1)
        self.assertIn('## Playbook: bug-fix (your parts)', text)
        self.assertNotIn('rust-engine', text.split('## Playbook:')[1].split('\n')[0])

    def test_a_small_target_turns_later_parts_into_pending_reads(self):
        self.use_overlays()
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + '\n[playbooks]\ntarget_tokens = 1\n')
        self.commit('target')
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        out = self.dispatch(b, timeout=60)
        count = len(self.composition()['roles']['sidekick'])
        self.assertRegex(out, rf'playbook: {count} parts, ~\d+ tokens, {count} pending')
        self.assertIn(
            f'kitchen.sh playbook-part {self.store} 001 sidekick ', b.read_text()
        )

    def test_playbook_part_prints_the_stored_text_after_the_source_changes(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.run_sh('classify', str(self.store), str(b), timeout=60)
        entry = next(
            e
            for e in self.composition()['roles']['sidekick']
            if e['id'] == 'error-model'
        )
        (self.repo / 'CODING_STANDARDS.md').unlink()
        shutil.rmtree(self.repo / '.agents/playbooks')
        out = self.run_sh(
            'playbook-part',
            str(self.store),
            '001',
            'sidekick',
            'error-model',
            timeout=60,
        )
        self.assertIn(entry['text'].rstrip('\n') + '\n\n', out)
        src = entry['source']
        self.assertIn(
            f'from overlay:rust-engine, {src["root"]}:{src["path"]} {src["select"]} {src["digest"]}\n',
            out,
        )

    def test_playbook_part_refuses_an_unknown_unit_role_or_part_with_one_line(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.run_sh('classify', str(self.store), str(b), timeout=60)
        for args, message in (
            (('009', 'sidekick', 'error-model'), 'unit 009 has no stored composition'),
            (('001', 'cook', 'error-model'), 'playbook-part: unknown role cook'),
            (
                ('001', 'sidekick', 'nope'),
                'playbook-part: sidekick has no part nope in unit 001',
            ),
        ):
            with self.subTest(args=args):
                out = self.run_sh(
                    'playbook-part', str(self.store), *args, code=2, timeout=60
                )
                self.assertIn(message, out)
                self.assertEqual(len(out.strip().split('\n')), 1)

    def test_the_section_goes_before_the_report_and_survives_a_redispatch(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.dispatch(b, timeout=60)
        text = b.read_text()
        self.assertLess(text.index('## Playbook:'), text.index('\n## Report'))
        self.dispatch(b, timeout=60)
        self.assertEqual(b.read_text(), text)

    def test_a_fenced_heading_in_the_section_does_not_end_it(self):
        self.kitchen_variant().load_kitchen_core()
        from kitchen_core.playbook_delivery import put_section

        old = (
            '# B\n\n## Playbook: a\n\n```sh\n## kept\n```\nold tail\n\n## Report\n\nR\n'
        )
        new = '## Playbook: b\n\nnew\n'
        self.assertEqual(
            put_section(old, new), '# B\n\n## Playbook: b\n\nnew\n\n## Report\n\nR\n'
        )
        self.assertEqual(put_section(put_section(old, new), new), put_section(old, new))
        self.assertEqual(put_section(old, ''), '# B\n\n## Report\n\nR\n')

    def test_reclassify_without_a_playbook_drops_the_stamp_and_the_section(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.dispatch(b, timeout=60)
        self.assertIn('## Playbook:', b.read_text())
        b.write_text(b.read_text().replace('playbook: bug-fix', 'playbook: none'))
        out = self.dispatch(b, timeout=60)
        self.assertFalse((self.store / 'compositions/001.json').exists())
        self.assertNotIn('## Playbook:', b.read_text())
        self.assertNotIn('playbook: ', out.replace('playbook: none', ''))

    def test_a_brief_without_a_composition_dispatches_byte_identical(self):
        b = self.brief('001', 'util', ['src/util.py'], playbook='none')
        self.run_sh('classify', str(self.store), str(b), timeout=60)
        before = b.read_bytes()
        out = self.dispatch(b, timeout=60)
        self.assertEqual(b.read_bytes(), before)
        self.assertNotIn('playbook:', out)
        self.assertFalse((self.store / 'compositions').exists())

    def test_a_without_with_no_reason_refuses_classify(self):
        self.use_overlays()
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        self.header(b, 'without: rust-engine')
        out = self.run_sh('classify', str(self.store), str(b), code=1)
        self.assertIn('without: rust-engine needs a reason', out)

    def test_a_unit_dispatched_as_escalated_is_verified_by_the_escalated_class(self):
        self.escalated_unit_is_verified_escalated('risk: escalated')

    def test_an_escalated_risk_with_a_reason_gets_the_escalated_class(self):
        self.escalated_unit_is_verified_escalated(
            'risk: escalated (contract change: X)'
        )

    def test_an_escalated_risk_with_a_colon_reason_gets_the_escalated_class(self):
        self.escalated_unit_is_verified_escalated('risk: escalated: contract change')

    def escalated_unit_is_verified_escalated(self, risk_line):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        b.write_text(re.sub(r'^risk:.*$', risk_line, b.read_text(), flags=re.M))
        self.run_sh('verify', str(self.store), '001')
        self.assertEqual(self.packet_fields()['verifier-class'], 'escalated')
        self.assertIn(
            'risk: escalated\n',
            (self.store / 'verdicts/001-util-v1-packet.md').read_text(),
        )

    def test_escalated_units_need_an_agreed_plan_and_routine_ones_do_not(self):
        self.dispatch(self.brief('001', 'util', ['src/util.py']))
        self.dispatch(self.brief('002', 'api', ['contracts/**']), code=6)

    def test_the_plan_gate_names_kitchen_sh_and_a_plan_value_that_is_not_a_path(self):
        b = self.brief('002', 'api', ['contracts/**'], plan='required')
        out = self.dispatch(b, code=6)
        self.assertIn("plan: must be the plan file's path, not 'required'", out)
        self.assertIn('agree it with kitchen.sh discuss', out)
        plan = self.store / 'plans/002-api.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 002: api\n\nrisk: escalated\n')
        b = self.brief('002', 'api', ['contracts/**'], plan=str(plan))
        out = self.dispatch(b, code=6)
        self.assertIn(
            'has no review; run kitchen.sh discuss and write reviews/002-', out
        )
        self.assertNotIn('pair.sh', out)

    def test_step_records_only_after_gates_and_policy_pass(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('step 1')
        self.assertIn(
            'gates: pass (app)', self.run_sh('step', str(self.store), sha, 'util two')
        )
        steps = (self.store / 'steps/001-util.tsv').read_text().splitlines()
        self.assertEqual(len(steps), 1)
        self.write('notes.txt', 'x\n')
        out = self.run_sh('step', str(self.store), self.commit('unmapped'), 'notes')
        self.assertIn('gates: none ran; no profile covers notes.txt', out)
        # A routine unit's steps never wake the master; its gates proved them.
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn('check-in: 001-util.md', out)
        self.assertNotIn('to review on', out)
        self.write('FAIL', 'x\n')
        self.write('src/util.py', 'print(3)\n')
        sha = self.commit('step 2')
        out = self.run_sh('step', str(self.store), sha, 'util three', code=2)
        self.assertIn('[no-print]', out)
        self.assertIn('next: fix it in a new commit', out)
        out = self.run_sh('step', str(self.store), sha, 'util three', code=2)
        self.assertIn('2 failed checks in a row; write the report as blocked', out)
        self.assertEqual(
            len((self.store / 'steps/001-util.tsv').read_text().splitlines()), 2
        )

    def job(self):
        """The one job directory of a step on unit 001-util."""
        (found,) = (self.store / 'steps/jobs').glob('001-util-[0-9a-f]*')
        return found

    def scrub(self, text):
        return re.sub(
            r' [0-9.]+s ',
            ' <S>s ',
            re.sub(r'log: \S+/(\S+\.log)', r'log: <logs>/\1', text),
        )

    def test_a_step_and_a_finish_write_the_out_log_and_rc_the_bash_workers_wrote(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, "grep -q 'X = 2' src/util.py")
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('one')
        self.run_sh('step', str(self.store), sha, 'one')
        step_job = self.job()
        self.assertEqual(
            (
                self.scrub((step_job / 'out.1').read_text()),
                (step_job / 'rc').read_text(),
            ),
            (
                'app fast: pass\n  exit 0 <S>s test ! -e FAIL  log: <logs>/app-fast-0.log\npolicy: clean\n',
                '0\n',
            ),
        )
        self.done(b, sha)
        self.run_sh('finish', str(self.store), str(self.store / 'reports' / b.name))
        (finish_job,) = (self.store / 'steps/jobs').glob('001-util-verify-*')
        self.assertEqual(
            (
                self.scrub((finish_job / 'out').read_text()),
                (finish_job / 'rc').read_text(),
            ),
            (
                "verify: pass (1 commands from the brief)\n  exit 0 <S>s grep -q 'X = 2' src/util.py  log: <logs>/verify-0.log\n"
                '    output: 0 lines\n',
                '0\n',
            ),
        )

    def test_evidence_commits_of_a_step_a_finish_and_a_verification_survive_a_rewrite_of_the_branch_and_gc(
        self,
    ):
        base = self.git('rev-parse', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        stepped = self.commit('one')
        self.run_sh('step', str(self.store), stepped, 'one')
        keep = f'refs/pstack/keep/{self.state()["slug"]}/'
        self.assertEqual(self.git('rev-parse', f'{keep}{stepped}'), stepped)
        self.write('src/util.py', 'X = 3\n')
        last = self.commit('two')
        self.done(b, last)
        self.run_sh('finish', str(self.store), str(self.store / 'reports' / b.name))
        self.assertEqual(self.git('rev-parse', f'{keep}{last}'), last)
        self.assertNotIn(
            f'{keep}{base}', self.git('for-each-ref', '--format=%(refname)')
        )
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertEqual(self.git('rev-parse', f'{keep}{base}'), base)
        # The branch is rewritten and the reflog and loose objects go: only the keep refs hold the old commits.
        self.git('reset', '-q', '--hard', base)
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.git('gc', '-q', '--prune=now')
        for oid in (stepped, last, base):
            self.git('cat-file', '-e', f'{oid}^{{commit}}')

    def test_a_failing_gate_a_policy_failure_and_a_failing_verify_command_leave_rc_2(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'exit 3')
        self.dispatch(b)
        self.write('FAIL', 'x\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('gate fails')
        self.run_sh('step', str(self.store), sha, 'gate', code=2)
        self.assertEqual((self.job() / 'rc').read_text(), '2\n')
        self.assertIn('app fast: FAIL', (self.job() / 'out.1').read_text())
        (self.repo / 'FAIL').unlink()
        self.write('src/util.py', 'print(3)\n')
        self.write('tests/test_app.py', 'x = 3\n')
        sha = self.commit('policy fails')
        self.run_sh('step', str(self.store), sha, 'policy', code=2)
        policy_job = max(
            (self.store / 'steps/jobs').glob('001-util-*'),
            key=lambda d: (d / 'rc').stat().st_mtime_ns,
        )
        self.assertEqual((policy_job / 'rc').read_text(), '2\n')
        self.assertIn('app fast: pass', (policy_job / 'out.1').read_text())
        self.assertIn('[no-print]', (policy_job / 'out.1').read_text())
        self.done(b, sha)
        self.run_sh(
            'finish', str(self.store), str(self.store / 'reports' / b.name), code=2
        )
        (finish_job,) = (self.store / 'steps/jobs').glob('001-util-verify-*')
        # brief-verify's own exit code, 2, as at the dispatch head; the command's 3 is in the verify log
        self.assertEqual((finish_job / 'rc').read_text(), '2\n')
        self.assertIn('exit 3', (finish_job / 'out').read_text())

    def run_worker(self, command, stub_body, profiles='app\n'):
        """worker.py with a stub in place of kitchen.py: the stub's exit status for each subcommand it is called with comes from stub_body (a function of argv)."""
        job = self.store / 'job'
        shutil.rmtree(job, ignore_errors=True)
        (job / 'logs').mkdir(parents=True)
        (job / 'profiles').write_text(profiles)
        stub = self.store / 'stub.py'
        stub.write_text('import os, signal, sys\nargv = sys.argv[1:]\n' + stub_body)
        worker = root / 'skills/pstack-kitchen/scripts/kitchen_core/worker.py'
        argv = (
            ['step', str(self.repo), str(stub), str(job), 'base', 'head']
            if command == 'step'
            else ['finish', str(self.repo), str(stub), 'brief.md', str(job)]
        )
        r = subprocess.run(
            [sys.executable, '-I', str(worker), *argv],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        return (job / 'rc').read_text()

    def test_the_worker_writes_the_exit_status_of_its_child_as_a_shell_would(self):
        self.assertEqual(self.run_worker('finish', 'sys.exit(3)\n'), '3\n')
        self.assertEqual(
            self.run_worker('finish', 'os.kill(os.getpid(), signal.SIGTERM)\n'), '143\n'
        )
        self.assertEqual(self.run_worker('finish', 'sys.exit(0)\n'), '0\n')

    def test_a_step_is_rc_2_for_a_failing_gate_or_a_failing_policy_check_and_0_otherwise(
        self,
    ):
        failing_gate = "sys.exit(7 if 'gate' in argv else 0)\n"
        failing_policy = "sys.exit(7 if 'policy' in argv else 0)\n"
        self.assertEqual(self.run_worker('step', failing_gate), '2\n')
        self.assertEqual(self.run_worker('step', failing_policy), '2\n')
        self.assertEqual(self.run_worker('step', 'sys.exit(0)\n'), '0\n')
        self.assertEqual(self.run_worker('step', failing_gate, profiles='\n'), '0\n')

    def test_a_step_with_no_profiles_file_runs_no_gate_and_is_rc_2_with_the_reason_in_the_out_log(
        self,
    ):
        job = self.store / 'job'
        shutil.rmtree(job, ignore_errors=True)
        (job / 'logs').mkdir(parents=True)
        stub = self.store / 'stub.py'
        stub.write_text('import sys\nopen(sys.argv[0] + ".ran", "w").close()\n')
        worker = root / 'skills/pstack-kitchen/scripts/kitchen_core/worker.py'
        r = subprocess.run(
            [
                sys.executable,
                '-I',
                str(worker),
                'step',
                str(self.repo),
                str(stub),
                str(job),
                'base',
                'head',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(
            (r.returncode, r.stdout, r.stderr),
            (0, f'no profiles file: {job}/profiles\n', ''),
        )
        self.assertEqual((job / 'rc').read_text(), '2\n')
        self.assertFalse((self.store / 'stub.py.ran').exists())

    def test_the_worker_exits_1_and_writes_nothing_when_the_repo_is_gone(self):
        job = self.store / 'job'
        job.mkdir()
        (job / 'profiles').write_text('app\n')
        worker = root / 'skills/pstack-kitchen/scripts/kitchen_core/worker.py'
        gone = str(self.store / 'no-such-repo')
        for argv in (
            ['step', gone, 'kitchen.py', str(job), 'b', 'h'],
            ['finish', gone, 'kitchen.py', 'brief.md', str(job)],
        ):
            r = subprocess.run(
                [sys.executable, '-I', str(worker), *argv],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(
                (
                    r.returncode,
                    r.stdout,
                    r.stderr,
                    sorted(p.name for p in job.iterdir()),
                ),
                (1, '', '', ['profiles']),
                argv[0],
            )

    def test_step_refuses_a_sha_that_is_not_head_and_a_dirty_tracked_tree_before_any_gate(
        self,
    ):
        # B3: the gates run on the checkout, so a step must name HEAD and the tracked tree must be clean.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        first = self.commit('step 1')
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.commit('step 2')
        events = lambda: (self.store / 'events.tsv').read_text()
        before = events()
        out = self.run_sh('step', str(self.store), first, 'one', code=1)
        self.assertEqual(
            out,
            f'kitchen.sh: step {first}: HEAD is {self.git("rev-parse", "--short", "HEAD")}; commit the step and record HEAD\n',
        )
        self.assertFalse((self.store / 'gates.tsv').exists())
        self.write('src/util.py', 'X = 4\n')
        head = self.git('rev-parse', 'HEAD')
        out = self.run_sh('step', str(self.store), head, 'two', code=1)
        self.assertEqual(
            out,
            f'kitchen.sh: step {head}: the tree has uncommitted changes to tracked files; commit them (a WIP commit if unfinished; never git stash) first\n',
        )
        self.assertFalse((self.store / 'gates.tsv').exists())
        self.assertEqual(events(), before)
        self.assertFalse((self.store / 'steps/001-util.tsv').exists())
        # an untracked file is not a tracked change
        self.git('checkout', '--', 'src/util.py')
        self.write('untracked.txt', 'x\n')
        self.assertIn('gates: pass', self.run_sh('step', str(self.store), head, 'two'))

    def test_step_discards_gates_whose_checkout_moved_while_they_ran(self):
        # The gates run on the checkout: a commit or an edit of a tracked file during them means their result proves something else, so the step is
        # refused with no row and no event, and the next run starts them again. The gate's first run commits and its second edits a tracked file.
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text().replace(
                'fast = ["test ! -e FAIL"]', f'fast = [{json.dumps(MOVE_CHECKOUT)}]'
            )
        )
        self.commit('gate')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        first = self.commit('step 1')
        before = (self.store / 'events.tsv').read_text()
        out = self.run_sh('step', str(self.store), first, 'one', code=1)
        moved = self.git('rev-parse', 'HEAD')
        self.assertEqual(
            out,
            f'kitchen.sh: step {first}: HEAD moved to {moved[:9]} while the gates ran, so they did not prove {first}; record the step at HEAD\n',
        )
        out = self.run_sh('step', str(self.store), moved, 'one', code=1)
        self.assertEqual(
            out,
            f'kitchen.sh: step {moved}: tracked files changed while the gates ran, so they did not prove {moved}:\n M src/util.py\n'
            'commit them (a WIP commit if unfinished; never git stash), then run the same step again\n',
        )
        self.assertFalse((self.store / 'gates.tsv').exists())
        self.assertEqual((self.store / 'events.tsv').read_text(), before)
        self.git('checkout', '--', 'src/util.py')
        self.assertIn(
            'gates: pass (app)', self.run_sh('step', str(self.store), moved, 'one')
        )

    def test_recording_the_same_sha_again_runs_nothing_and_merges_resolves(self):
        # B2, B4. The second record of a commit runs no gate, touches no job directory, writes no row and logs no event; --resolves still lands.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('step 1')
        self.assertIn(
            'gates: pass (app)', self.run_sh('step', str(self.store), sha, 'util two')
        )
        note = self.store / 'notes/001-util-n1.md'
        note.parent.mkdir(exist_ok=True)
        note.write_text('# N\n\nstatus: blocking\n')
        job = next((self.store / 'steps/jobs').iterdir())
        (job / 'sentinel').write_text('x')
        steps_events = lambda: [
            l
            for l in (self.store / 'events.tsv').read_text().splitlines()
            if '\tstep\t' in l
        ]
        snapshot = lambda: (
            (self.store / 'gates.tsv').read_text(),
            (self.store / 'progress/001-util.md').read_text(),
            len(steps_events()),
        )
        self.assertIn('001-util-n1', self.run_sh('notes', str(self.store)))
        before = snapshot()
        out = self.run_sh(
            'step', str(self.store), sha, 'util two again', '--resolves', 'n1'
        )
        self.assertIn(f'step {sha[:9]}: already recorded', out)
        self.assertNotIn('gates:', out)
        self.assertEqual(snapshot(), before)
        self.assertTrue((job / 'sentinel').exists())
        rows = (self.store / 'steps/001-util.tsv').read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].split('\t')[3], 'n1')
        self.assertNotIn('001-util-n1', self.run_sh('notes', str(self.store)))
        # again, with the same id: nothing changes
        self.run_sh('step', str(self.store), sha, 'util two again', '--resolves', 'n1')
        self.assertEqual(
            (self.store / 'steps/001-util.tsv').read_text().splitlines(), rows
        )

    def test_each_gate_run_keeps_its_own_log_and_the_fail_event_names_that_run(self):
        # B4, refined: three runs of the same step leave out.1, out.2 and out.3, `out` points at the latest, and every gate-fail event names
        # the log of its own run.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('FAIL', 'x\n')
        self.write('src/util.py', 'print(3)\n')
        sha = self.commit('step 2')
        logs = []
        for n in (1, 2, 3):
            out = self.run_sh('step', str(self.store), sha, 'three', code=2)
            self.assertIn('[no-print]', out)
            job = next((self.store / 'steps/jobs').iterdir())
            logs.append((job / f'out.{n}').read_text())
            self.assertEqual((job / 'out').resolve(), (job / f'out.{n}').resolve())
        failed = [
            l
            for l in (self.store / 'events.tsv').read_text().splitlines()
            if '\tgate-fail\t' in l
        ]
        self.assertEqual(
            [l.rsplit(' ', 1)[1] for l in failed], [f'{job}/out.{n}' for n in (1, 2, 3)]
        )
        for n in (1, 2, 3):
            self.assertEqual((job / f'out.{n}').read_text(), logs[n - 1])

    def test_an_earlier_sha_is_re_recorded_whatever_head_and_the_tree_are_now(self):
        # review 011 (i): record A, record B, then A again with --resolves n1 while A is not HEAD and the tree is dirty.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        shas = []
        for n in (2, 3):
            self.write('src/util.py', f'X = {n}\n')
            self.write('tests/test_app.py', f'x = {n}\n')
            shas.append(self.commit(f'step {n}'))
            self.assertIn(
                'gates: pass',
                self.run_sh('step', str(self.store), shas[-1], f'step {n}'),
            )
        note = self.store / 'notes/001-util-n1.md'
        note.parent.mkdir(exist_ok=True)
        note.write_text('# N\n\nstatus: blocking\n')
        self.write('src/util.py', 'X = 99\n')
        out = self.run_sh('step', str(self.store), shas[0], 'again', '--resolves', 'n1')
        self.assertIn(f'step {shas[0][:9]}: already recorded', out)
        rows = (self.store / 'steps/001-util.tsv').read_text().splitlines()
        self.assertEqual([r.split('\t')[3] for r in rows], ['n1', ''])

    def test_step_gates_see_the_run_and_role(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text().replace(
                'fast = ["test ! -e FAIL"]',
                'fast = ["printf \'%s|%s\' \\"$PSTACK_KITCHEN_RUN\\" \\"$PSTACK_KITCHEN_ROLE\\" > lane.txt"]',
            )
        )
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('step 1')
        self.assertIn(
            'gates: pass (app)', self.run_sh('step', str(self.store), sha, 'util two')
        )
        self.assertEqual((self.repo / 'lane.txt').read_text(), 'demo|sidekick')

    def test_a_step_whose_commit_waives_test_touch_with_a_reason_passes_and_says_so(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 4\n')
        bare = self.commit(
            'no test\n\nkitchen-allow: test-touch\nSigned-off-by: t <t@t>'
        )
        out = self.run_sh('step', str(self.store), bare, 'no reason', code=2)
        self.assertIn('[test-touch] profile app requires tests', out)
        self.write('src/util.py', 'X = 5\n')
        sha = self.commit(
            'rename only\n\nkitchen-allow: test-touch a rename; no behavior moved'
        )
        out = self.run_sh('step', str(self.store), sha, 'rename')
        self.assertIn('gates: pass (app)', out)
        self.assertIn(
            f'policy: test-touch waived by {sha[:9]}: a rename; no behavior moved; the review sees it',
            out,
        )
        row = (self.store / 'gates.tsv').read_text().splitlines()[-1].split('\t')
        self.assertEqual(row[3:], ['pass', 'app', 'waived:test-touch'])
        self.assertIn('\ttest-touch-waived\t', (self.store / 'events.tsv').read_text())

    def test_a_resumed_units_commit_is_checked_against_its_own_diff(self):
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        sha = self.commit('made before the brief')
        self.dispatch(self.brief('001', 'resume', ['src/**', 'tests/**']))
        self.assertIn(
            'gates: pass (app)', self.run_sh('step', str(self.store), sha, 'resumed')
        )
        self.write('notes.txt', 'x\n')
        out = self.run_sh('step', str(self.store), self.commit('unmapped'), 'notes')
        self.assertIn('gates: none ran', out)
        rows = [
            r.split('\t') for r in (self.store / 'gates.tsv').read_text().splitlines()
        ]
        self.assertEqual([(r[3], r[4]) for r in rows], [('pass', 'app'), ('none', '')])

    def test_step_returns_while_checks_run_and_resumes(self):
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace(
                'fast = ["test ! -e FAIL"]',
                'fast = ["sleep 5"]',  # kitchen-allow: test-sleep (fake gate must still be running)
            ),
        )
        self.commit('slow gate')
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        sha = self.commit('step')
        self.env['KITCHEN_STEP_SLICE_S'] = '1'
        out = self.run_sh('step', str(self.store), sha, 'slow', code=4)
        self.assertIn('gates: still running', out)
        self.assertIn('this step is not recorded yet; do not end your turn', out)
        self.assertIn(f'kitchen.sh step {self.store} {sha} "slow"', out)
        self.assertIn(
            'never end your turn on it', (self.store / 'briefs/001-util.md').read_text()
        )
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        self.assertIn(
            'gates: pass (app)', self.run_sh('step', str(self.store), sha, 'slow')
        )
        self.assertEqual(len((self.store / 'gates.tsv').read_text().splitlines()), 1)

    def kill_worker(self, job):
        """Ends the job's detached worker and its checks with no result written, and points its pid file at a live process, as a pid reused after a
        restart would: only the job's lock tells the worker is gone."""
        os.killpg(int((job / 'pid').read_text()), signal.SIGKILL)
        (job / 'pid').write_text(f'{os.getpid()}\n')
        self.assertFalse((job / 'rc').exists())

    def test_a_step_whose_gate_worker_died_says_so_and_reruns_it(self):
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace(
                'fast = ["test ! -e FAIL"]',
                'fast = ["test -e GO || sleep 60"]',  # kitchen-allow: test-sleep (a gate that outlives its killed worker)
            ),
        )
        self.commit('held gate')
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        sha = self.commit('step')
        self.env['KITCHEN_STEP_SLICE_S'] = '0'
        self.assertIn(
            'gates: still running',
            self.run_sh('step', str(self.store), sha, 's', code=4),
        )
        job = next((self.store / 'steps/jobs').iterdir())
        self.kill_worker(job)
        (self.repo / 'GO').touch()
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        out = self.run_sh('step', str(self.store), sha, 's')
        self.assertIn(
            f'gates: the worker died without a result (log: {job}/out); rerunning\n',
            out,
        )
        self.assertIn('gates: pass (app)', out)
        self.assertEqual(sorted(p.name for p in job.glob('out.*')), ['out.1', 'out.2'])
        self.assertEqual(len((self.store / 'gates.tsv').read_text().splitlines()), 1)

    def test_a_step_whose_rerun_worker_dies_too_refuses_with_its_log(self):
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace(
                'fast = ["test ! -e FAIL"]',
                'fast = ["python3 -c \'import os, signal; os.killpg(0, signal.SIGKILL)\'"]',
            ),
        )
        self.commit('a gate that kills its worker')
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        sha = self.commit('step')
        self.env.update({'KITCHEN_STEP_SLICE_S': '30', 'KITCHEN_STEP_POLL_S': '0.05'})
        out = self.run_sh('step', str(self.store), sha, 's', code=1)
        job = next((self.store / 'steps/jobs').iterdir())
        self.assertIn(
            f'gates: the worker died without a result (log: {job}/out); rerunning\n',
            out,
        )
        self.assertIn(
            f'the gates worker died again without a result (log: {job}/out); read the log',
            out,
        )
        self.assertFalse((self.store / 'gates.tsv').exists())

    def test_a_note_published_while_gates_run_refuses_the_rerun_and_the_resolving_rerun_reuses_the_job(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        sha = self.commit('step')
        self.env['KITCHEN_STEP_SLICE_S'] = '0'
        self.assertIn(
            'gates: still running',
            self.run_sh('step', str(self.store), sha, 'slow', code=4),
        )
        note = self.store / 'notes/001-util-n1.md'
        note.parent.mkdir(exist_ok=True)
        note.write_text('# N\n\nstatus: blocking\n')
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        self.assertEqual(
            self.run_sh('step', str(self.store), sha, 'slow', code=5),
            f'kitchen.sh: step: blocking note n1 is open ({note}); fix it and record the fix with --resolves n1\n',
        )
        self.assertFalse((self.store / 'steps/001-util.tsv').exists())
        self.assertIn(
            'gates: pass (app)',
            self.run_sh('step', str(self.store), sha, 'slow', '--resolves', 'n1'),
        )
        job = next((self.store / 'steps/jobs').iterdir())
        self.assertEqual(sorted(p.name for p in job.glob('out.*')), ['out.1'])
        self.assertEqual(len((self.store / 'gates.tsv').read_text().splitlines()), 1)
        self.assertEqual(
            (self.store / 'steps/001-util.tsv').read_text().split('\t')[3], 'n1'
        )

    def await_job_line(self, text, bound=120):
        """Poll the step job's output for a line the detached gate prints; the bound only turns a hang into a failure."""
        import threading

        pause, deadline = threading.Event(), time.monotonic() + bound
        while time.monotonic() < deadline:
            if any(
                text in out.read_text(errors='replace')
                for out in (self.store / 'steps/jobs').glob('*/out')
            ):
                return
            pause.wait(0.2)
        self.fail(f'the step job never printed {text!r}')

    def step_until_recorded(self, sha, summary, tries=600):
        """Run step again, a short slice at a time, until it records; the cap only turns a hang into a failure."""
        for _ in range(tries):
            r = subprocess.run(
                [str(self.script), 'step', str(self.store), sha, summary],
                cwd=self.repo,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if r.returncode == 0:
                return r.stdout + r.stderr
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.fail('the step never recorded')

    def held_slot_step(self, kind, queued_line, digest_line):
        self.env.update({'PSTACK_KITCHEN_GATE_SLOTS': '1', 'KITCHEN_STEP_SLICE_S': '1'})
        repos = self.store.parent.parent / 'repos'
        repos.mkdir(parents=True, exist_ok=True)
        held, _ = kitchen.acquire_slot(repos, kind, 1)
        self.addCleanup(held.close)
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        sha = self.commit('step')
        self.run_sh('step', str(self.store), sha, 'waits', code=4)
        self.await_job_line(queued_line)
        out = self.run_sh('step', str(self.store), sha, 'waits', code=4)
        self.assertIn(digest_line, out)
        held.close()
        self.assertIn('gates: pass (app)', self.step_until_recorded(sha, 'waits'))
        return out

    def test_a_step_waiting_for_a_gate_slot_says_where_it_stands(self):
        self.held_slot_step(
            'gate',
            'kitchen: queued 1 of 1 for 1 machine gate slot',
            'gates: queued 1 of 1 for 1 machine gate slot, behind gates from other kitchens',
        )
        rows = (Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob(
            '*/timings.tsv'
        )
        waited = [
            float(line.split('\t')[6])
            for f in rows
            for line in f.read_text().splitlines()
            if line.split('\t')[3] == 'sidekick'
        ]
        self.assertTrue(any(w > 0 for w in waited), waited)

    def test_a_step_waiting_for_the_heavy_slot_says_where_it_stands(self):
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace(
                'fast = ["test ! -e FAIL"]', 'fast = ["test ! -e FAIL"]\nheavy = true'
            ),
        )
        self.commit('heavy gate')
        out = self.held_slot_step(
            'heavy',
            'kitchen: queued 1 of 1 for 1 heavy slot',
            'gates: queued 1 of 1 for 1 heavy slot, behind heavy gates from other kitchens',
        )
        self.assertNotIn('machine gate slot', out)

    def test_quiet_wait_skips_unflagged_check_ins(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        checkins = lambda: sum(
            l.endswith('\tcheckin')
            for l in (self.store / 'events.tsv').read_text().splitlines()
        )
        # Past --max, an unflagged check-in comes back, marked quiet.
        self.env['KITCHEN_QUIET_MAX_S'] = '0'
        self.assertIn(
            'quiet: 0m with nothing flagged',
            self.run_sh('wait', str(self.store), '--timeout', '1', code=4),
        )
        # A flagged one comes back at once, unmarked.
        self.env['KITCHEN_QUIET_MAX_S'] = '3600'
        self.write('elsewhere.txt', 'x\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn('outside scope: 1', out)
        self.assertNotIn('quiet:', out)
        (self.repo / 'elsewhere.txt').unlink()
        # The sidekick reports on the second wait: the master sleeps through the
        # unflagged check-in before it and wakes on the report.
        (self.fake / 'report-after').write_text(
            f'2 {self.store / "reports" / b.name} partial\n'
        )
        before = checkins()
        out = self.run_sh('wait', str(self.store), '--timeout', '1')
        self.assertIn('report_status: partial', out)
        self.assertNotIn('check-in:', out)
        self.assertEqual(checkins(), before)

    def test_a_quiet_wait_announces_an_open_note_once_across_waits_and_a_replacement_note_again(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        self.write('src/util.py', 'X = 2\n')
        c1 = self.commit('one')
        steps = self.store / 'steps/001-util.tsv'
        steps.parent.mkdir(exist_ok=True)
        steps.write_text(f'1\t{c1}\t1\t\tone\n')
        notes = self.store / 'notes'
        notes.mkdir(exist_ok=True)
        (notes / '001-util-n1.md').write_text('# N\n\nstatus: blocking\n')
        self.env['KITCHEN_QUIET_MAX_S'] = '0'
        first = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn(
            'steps: 1 committed, reviewed through 1, 1 blocking notes open\n', first
        )
        self.assertNotIn('quiet:', first)
        # a second, separate wait finds n1 announced: nothing is flagged
        second = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn('quiet: 0m with nothing flagged', second)
        self.assertEqual(
            json.loads((self.store / 'pair.json').read_text())['announced_notes'],
            ['001-util-n1.md'],
        )
        # n1 is resolved and n2 opens: the open count is still 1, and the new note is announced
        steps.write_text(f'1\t{c1}\t1\tn1\tone\n')
        (notes / '001-util-n2.md').write_text('# N\n\nstatus: blocking\n')
        third = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn(
            'steps: 1 committed, reviewed through 2, 1 blocking notes open\n', third
        )
        self.assertNotIn('quiet:', third)
        self.assertEqual(
            json.loads((self.store / 'pair.json').read_text())['announced_notes'],
            ['001-util-n2.md'],
        )
        # every other flag still wakes: a write outside Scope is announced again on each wait
        self.write('elsewhere.txt', 'x\n')
        for _ in range(2):
            self.assertIn(
                'outside scope: 1',
                self.run_sh('wait', str(self.store), '--timeout', '1', code=4),
            )

    def wakes(self):
        rows = (self.store / 'events.tsv').read_text().splitlines()[1:]
        return [
            r.split('\t')[5] for r in rows if r.split('\t')[2:4] == ['master', 'wake']
        ]

    def test_a_quiet_wait_logs_one_wake_for_the_return_not_one_per_swallowed_check_in(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        (self.fake / 'report-after').write_text(
            f'3 {self.store / "reports" / b.name} partial\n'
        )
        # The clock stands still, so the report on the third poll ends the wait however slow the host is, never --max.
        clock = self.fake / 'now'
        clock.write_text('1000\n')
        self.env['KITCHEN_QUIET_CLOCK'] = str(clock)
        before = self.wakes()
        self.assertIn(
            'report_status: partial',
            self.run_sh('wait', str(self.store), '--timeout', '1'),
        )
        self.assertEqual(self.wakes()[len(before) :], ['report:partial'])

    def once_hook(self, subcommand, script):
        hook = self.fake / 'once' / subcommand
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\n' + script)
        hook.chmod(0o755)

    def test_a_quiet_wait_claims_the_store_once_and_its_later_polls_only_check_it(self):
        # (c) The second inner poll's `agent wait` plays the newer wait: it writes a foreign token. The quiet wait's later polls must find it
        # and end with `superseded:`; a quiet wait that claimed on every poll would overwrite it and go on.
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        other = f'#!/bin/sh\nprintf "other\\n" >"{self.store}/wait.pid"\n'
        self.once_hook(
            'wait',
            f'printf %s {shlex.quote(other)} >"{self.fake}/once/wait"\nchmod +x "{self.fake}/once/wait"\n',
        )
        self.env['KITCHEN_QUIET_POLL_S'] = '0'
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertEqual(
            out.strip().splitlines()[-1],
            'superseded: a newer wait owns this store; this one ends',
        )
        self.assertNotIn('quiet:', out)
        self.assertEqual((self.store / 'wait.pid').read_text(), 'other\n')

    def test_a_failed_first_claim_leaves_the_quiet_wait_unowned_not_superseded(self):
        # The store lock is held for the first inner wait's claim (one try) and freed by the time it waits. The later inner waits must not read
        # the missing claim as a newer owner and end with `superseded:`; the report that lands on the third poll is returned.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        (self.fake / 'report-after').write_text(
            f'3 {self.store / "reports" / b.name} partial\n'
        )
        self.once_hook('wait', f'rmdir "{self.store}/.pair.lock"\n')
        (self.store / '.pair.lock').mkdir()
        self.env.update({'PAIR_LOCK_TRIES': '1', 'PAIR_LOCK_POLL_S': '0'})
        out = self.run_sh('wait', str(self.store), '--timeout', '1')
        self.assertIn('report_status: partial', out)
        self.assertIn('could not claim wait.pid', out)
        self.assertNotIn('superseded', out)
        self.assertNotIn('unowned:', out)

    def test_a_quiet_wait_releases_the_store_when_it_returns(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        (self.fake / 'report-after').write_text(
            f'3 {self.store / "reports" / b.name} partial\n'
        )
        self.once_hook('wait', f'cp "{self.store}/wait.pid" "$FAKE/owner"\n')
        self.assertIn(
            'report_status: partial',
            self.run_sh('wait', str(self.store), '--timeout', '1'),
        )
        self.assertTrue((self.fake / 'owner').read_text().strip().isdigit())
        self.assertFalse((self.store / 'wait.pid').exists())

    def test_a_quiet_wait_that_a_newer_wait_supersedes_ends_without_a_quiet_line(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        self.once_hook('wait', f'printf "other\\n" >"{self.store}/wait.pid"\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '20000', code=4)
        self.assertEqual(
            out.strip(), 'superseded: a newer wait owns this store; this one ends'
        )
        self.assertEqual((self.store / 'wait.pid').read_text(), 'other\n')

    def test_verify_on_a_report_with_no_status_prints_one_error_and_no_usage(self):
        # A10. unit_range dies inside no command substitution now, so the reason is the only line.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.commit('util')
        report = self.store / 'reports' / b.name
        report.write_text('# Report\n\nhead: abc\n')
        self.assertEqual(
            self.run_sh('verify', str(self.store), '001', code=1),
            "kitchen.sh: unit 001's report has no status:\n",
        )
        report.write_text('# Report\n\nstatus: bogus\nhead: abc\n')
        self.assertEqual(
            self.run_sh('verify', str(self.store), '001', code=1),
            "kitchen.sh: unit 001's report is bogus, not done, partial, blocked, or failed\n",
        )

    def test_dispatch_takes_a_brief_path_relative_to_the_store(self):
        # A13. The cwd is the repo, not the store.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b.relative_to(self.store))
        self.assertEqual(self.state()['dispatch']['brief'], str(b))

    def test_a_brief_that_exists_as_given_wins_over_the_same_path_under_the_store(self):
        # A13. briefs/001-util.md exists in the cwd (the repo) and under the store: the cwd one is dispatched.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        local = self.repo / 'briefs' / b.name
        local.parent.mkdir()
        local.write_text(b.read_text() + '\nlocal copy\n')
        self.dispatch(b.relative_to(self.store))
        self.assertEqual(self.state()['dispatch']['brief'], str(local.resolve()))

    def test_queue_classifies_a_store_relative_brief_from_another_cwd(self):
        # A13 for kitchen.sh queue: the brief is stamped with its risk, profiles and verify mode, as when the path is absolute.
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.assertNotIn('risk: routine', b.read_text())
        self.run_sh('queue', str(self.store), str(b.relative_to(self.store)))
        text = b.read_text()
        for line in ('risk: routine', 'profiles: app', 'verify: '):
            self.assertIn(line, text)

    def test_steering_a_settled_sidekick_after_a_done_unit_advises_covers_in_the_kitchen(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'agents/demo-sidekick').write_text('idle pi\n')
        steer = self.store / 'steers/001-util-s1.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text(
            '# Steer 001-s1: util\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n'
        )
        out = self.run_sh('steer', str(self.store), str(steer), code=5)
        self.assertIn('write a fix brief and verify it with --covers 001', out)

    def test_a_quiet_wait_ending_at_max_logs_one_checkin_wake(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        clock = self.fake / 'now'
        clock.write_text('1000\n')
        self.env.update(
            {
                'KITCHEN_QUIET_MAX_S': '60',
                'KITCHEN_QUIET_POLL_S': '0',
                'KITCHEN_QUIET_CLOCK': str(clock),
            }
        )
        # Each round runs one `agent wait`: the first two swallow a check-in, the third finds the clock a minute on.
        once = self.fake / 'once'
        once.mkdir(exist_ok=True)
        third = f'#!/usr/bin/env bash\nprintf "1060\\n" >{clock}\n'
        second = f'#!/usr/bin/env bash\nprintf %s {shlex.quote(third)} >{once}/wait\nchmod +x {once}/wait\n'
        first = f'#!/usr/bin/env bash\nprintf %s {shlex.quote(second)} >{once}/wait\nchmod +x {once}/wait\n'
        (once / 'wait').write_text(first)
        (once / 'wait').chmod(0o755)
        before = self.wakes()
        rounds = lambda: sum(
            l.startswith('agent wait demo-sidekick')
            for l in (self.fake / 'calls.log').read_text().splitlines()
        )
        waits = rounds()
        out = self.run_sh(
            'wait', str(self.store), '--timeout', '1', code=4, timeout=120
        )
        self.assertGreaterEqual(rounds() - waits, 3)
        self.assertIn('quiet: 1m with nothing flagged', out)
        self.assertEqual(self.wakes()[len(before) :], ['checkin'])

    def working_at_dispatch(self):
        """The sidekick is at work when dispatch first waits on it, so that wait ends in a check-in."""
        (self.fake / 'once').mkdir(exist_ok=True)
        hook = self.fake / 'once/wait'
        hook.write_text(
            f'#!/usr/bin/env bash\nprintf "working pi\\n" >"{self.fake}/agents/demo-sidekick"\n'
        )
        hook.chmod(0o755)

    def test_a_dispatch_swallowing_its_first_check_in_logs_only_the_wait_that_returns(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.working_at_dispatch()
        (self.fake / 'report-after').write_text(
            f'3 {self.store / "reports" / b.name} partial\n'
        )
        before = self.wakes()
        self.run_sh('dispatch', str(self.store), str(b), '--timeout', '1')
        self.assertEqual(self.wakes()[len(before) :], ['report:partial'])

    def test_a_dispatch_returning_on_a_flagged_first_check_in_logs_one_checkin_wake(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.working_at_dispatch()
        self.write('elsewhere.txt', 'x\n')
        before = self.wakes()
        out = self.run_sh('dispatch', str(self.store), str(b), '--timeout', '1', code=4)
        self.assertIn('outside scope: 1', out)
        self.assertEqual(self.wakes()[len(before) :], ['checkin'])

    def test_two_catches_in_one_second_are_both_kept_and_two_identical_wakes_are_one(
        self,
    ):
        rows = event_log(
            (100, 'master catch - review'),
            (100, 'master catch - review'),
            (100, 'master wake 001-a checkin'),
            (100, 'master wake 001-a checkin'),
        )
        self.assertEqual([r.split('\t')[2] for r in rows], ['catch', 'catch', 'wake'])

    def test_gates_see_the_step_base(self):
        self.write(
            '.agents/kitchen.toml',
            (self.repo / '.agents/kitchen.toml')
            .read_text()
            .replace(
                'fast = ["test ! -e FAIL"]',
                'fast = ["git cat-file -e \\"$PSTACK_KITCHEN_STEP_BASE^{commit}\\""]',
            ),
        )
        base = self.commit('kitchen sees the base')
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 11\n')
        self.commit('one')
        self.write('tests/test_app.py', 'x = 11\n')
        sha = self.commit('two')
        self.assertIn(
            'gates: pass (app)',
            self.run_sh('step', str(self.store), sha, 'two commits'),
        )
        job = next((self.store / 'steps/jobs').iterdir())
        self.assertEqual((job / 'base').read_text().strip(), base)

    def test_a_squash_is_verified_against_what_it_carries(self):
        self.write('src/util.py', 'X = 21\n')
        self.write('tests/test_app.py', 'x = 21\n')
        self.commit('step one')
        self.write('src/util.py', 'X = 22\n')
        tip = self.commit('step two')
        squash = self.brief(
            '001', 'squash', ['src/**', 'tests/**'], playbook='refactoring'
        )
        self.dispatch(squash)
        self.git('reset', '--soft', 'HEAD~2')
        self.done(squash, self.commit('squashed'))
        self.assertEqual(
            self.git('rev-parse', 'HEAD^{tree}'),
            self.git('rev-parse', f'{tip}^{{tree}}'),
        )
        out = self.run_sh('verify', str(self.store), '001')
        self.assertNotIn('gates only', out)
        self.assertIn('verifier demo-verifier', out)

    def test_a_blocked_unit_can_still_be_verified(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 31\n')
        self.write('tests/test_app.py', 'x = 31\n')
        head = self.commit('util')
        (self.store / 'reports' / b.name).write_text(
            f'# Report\n\nstatus: blocked\nhead: {head}\n'
        )
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn(
            "note: unit 001's report is blocked; checking the head it names", out
        )
        self.assertIn('status: clean', out)

    def test_landing_verification_starts_where_the_stack_leaves_trunk(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        old_base = self.git('rev-parse', 'HEAD')
        self.write('docs/trunk.md', '# moved on\n')
        trunk = self.commit('trunk moved')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        self.write('src/util.py', 'X = 41\n')
        self.write('tests/test_app.py', 'x = 41\n')
        self.done(b, self.commit('stacked on the new trunk'))
        out = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn('landing base: merge-base of remotes/origin/main and HEAD', out)
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn(f'range: {trunk}..', packet)
        self.run_sh('verify', str(self.store), '--landing', '--base', old_base)
        self.assertIn(
            f'range: {old_base}..',
            (self.store / 'verdicts/landing-v2-packet.md').read_text(),
        )

    def verify_block(self, brief, commands):
        text = brief.read_text()
        brief.write_text(
            re.sub(
                r'(## Verify\n\n```bash\n).*?(\n```)',
                lambda m: m[1] + commands + m[2],
                text,
                count=1,
                flags=re.DOTALL,
            )
        )

    def reported(self):
        return [
            l
            for l in (self.store / 'events.tsv').read_text().splitlines()
            if '\tsidekick\treport\t' in l
        ]

    def test_finish_refuses_done_until_the_briefs_verify_commands_pass_at_the_head(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(
            b,
            "# the targeted check\ngrep -q 'X = 2' src/util.py\ntest -f src/app.py \\\n  && true",
        )
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('wrong value'))
        report = str(self.store / 'reports' / b.name)
        out = self.run_sh('finish', str(self.store), report, code=2)
        self.assertIn('verify: FAIL (2 commands from the brief)', out)
        self.assertIn('exit 1', out)
        self.assertIn('the master did not get this report', out)
        self.assertEqual(self.reported(), [])
        # A wait does not show the master the refused report either.
        (self.fake / 'agents/demo-sidekick').write_text('idle pi\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn(
            "written but not ready: the brief's Verify commands have not passed", out
        )
        # Running finish again reruns a failed check rather than reusing it.
        self.assertIn(
            'verify: FAIL', self.run_sh('finish', str(self.store), report, code=2)
        )
        self.write('src/util.py', 'X = 2\n')
        self.done(b, self.commit('right value'))
        out = self.run_sh('finish', str(self.store), report)
        self.assertIn('verify: pass (2 commands from the brief)', out)
        self.assertIn('exit 0', out)
        self.assertEqual(len(self.reported()), 1)

    def core_finish(self, report):
        """finish as the Python core runs it for the kitchen, past kitchen.sh's own Verify run."""
        # the core's own finish: the kitchen's finish (kitchen_core.step) runs the Verify commands first and then this one, so the driver takes `finish` out of
        # the names the variant may replace
        driver = (
            'import sys; sys.path.insert(0, sys.argv[1]); from pstack_core import cli; '
            "cli.OVERRIDABLE = tuple(n for n in cli.OVERRIDABLE if n != 'finish'); sys.exit(cli.main(sys.argv[2:]))"
        )
        argv = [
            sys.executable,
            '-I',
            '-c',
            driver,
            str(root / 'skills/pstack-pair/scripts'),
            '--skill-root',
            str(skill),
            'finish',
            str(self.store),
            str(report),
        ]
        r = subprocess.run(
            argv,
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return r.returncode, r.stdout, r.stderr

    def test_finish_in_the_core_withholds_a_kitchen_done_report_until_its_verify_job_passed(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('work')
        self.done(b, head)
        report = self.store / 'reports' / b.name
        module = self.kitchen_variant()
        job = Path(module.verify_job(BARE_CTX, str(self.store), str(b), head))
        code, out, err = self.core_finish(report)
        why = f"001-util: the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them); rewrite the report at the unit's last commit (head:), then finish again\n"
        self.assertEqual((code, out, err), (7, '', 'kitchen.sh: ' + why))
        self.assertEqual(self.reported(), [])
        job.mkdir(parents=True)
        (job / 'rc').write_text('1\n')
        self.assertEqual(self.core_finish(report)[0], 7)
        (job / 'rc').write_text('0\n')
        # a passing run whose checkout moved under it proves nothing; a job from an older worker has no checkout file
        for ended in (f'{"0" * 40}\n', f'{head}\n M src/util.py\n'):
            (job / 'checkout').write_text(ended)
            self.assertEqual(self.core_finish(report)[0], 7, ended)
        (job / 'checkout').unlink()
        code, out, err = self.core_finish(report)
        self.assertEqual((code, err), (0, ''))
        self.assertRegex(
            out,
            r'^(prompted master demo-master \(was idle\) with|master demo-master is \w+; left) ',
        )
        self.assertIn(
            f'next: end the turn with the single line: pstack-kitchen REPORT {report}\n',
            out,
        )
        self.assertEqual(len(self.reported()), 1)

    def test_finish_runs_the_verify_commands_only_at_the_units_tip(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('step')
        self.done(b, head)
        report = str(self.store / 'reports' / b.name)
        self.write('tests/test_new.py', 'def test_new():\n    pass\n')
        out = self.run_sh('finish', str(self.store), report, code=1)
        self.assertIn("the checkout holds work the unit's head does not", out)
        self.assertIn('tests/test_new.py', out)
        (self.repo / 'tests/test_new.py').unlink()
        self.git('checkout', '-q', 'HEAD~1')
        self.assertIn(
            f"not the report's head: {head[:9]}",
            self.run_sh('finish', str(self.store), report, code=1),
        )
        self.git('checkout', '-q', head)
        self.assertEqual(self.reported(), [])
        self.assertIn(
            'verify: pass (1 commands from the brief)',
            self.run_sh('finish', str(self.store), report),
        )

    def test_finish_discards_a_verify_run_whose_checkout_moved_while_it_ran(self):
        # The Verify command's first run commits and its second edits a tracked file; each discarded run is removed and runs again.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, MOVE_CHECKOUT)
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('step')
        self.done(b, head)
        report = str(self.store / 'reports' / b.name)
        job = Path(
            self.kitchen_variant().verify_job(BARE_CTX, str(self.store), str(b), head)
        )
        out = self.run_sh('finish', str(self.store), report, code=1)
        moved = self.git('rev-parse', 'HEAD')
        self.assertEqual(
            out,
            f"kitchen.sh: the checkout moved to {moved[:9]} while the brief's Verify commands ran at {head[:9]}, so they did not prove it; check "
            "out the report's head (or fix head:) and finish again\n",
        )
        self.assertFalse(job.exists())
        self.git('reset', '-q', '--hard', head)
        out = self.run_sh('finish', str(self.store), report, code=1)
        self.assertEqual(
            out,
            f"kitchen.sh: tracked files changed while the brief's Verify commands ran at {head[:9]}, so they did not prove it:\n M src/util.py\n"
            'Commit this as a step (kitchen.sh step) and name it in head:, or remove it, then finish again\n',
        )
        self.assertFalse(job.exists())
        self.assertEqual(self.reported(), [])
        self.git('checkout', '--', 'src/util.py')
        self.assertIn(
            'verify: pass (1 commands from the brief)',
            self.run_sh('finish', str(self.store), report),
        )
        self.assertEqual(len(self.reported()), 1)

    def test_finish_with_no_verify_commands_and_a_slow_one(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'none')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.done(b, self.commit('step'))
        report = str(self.store / 'reports' / b.name)
        self.assertIn(
            'verify: pass (the brief names none)',
            self.run_sh('finish', str(self.store), report),
        )
        b2 = self.brief('002', 'slow', ['src/**', 'tests/**'])
        self.verify_block(
            b2,
            'sleep 3; printf "%s|%s" "$PSTACK_KITCHEN_ROLE" "$PSTACK_KITCHEN_RUN" >"$OUT"',  # kitchen-allow: test-sleep (a Verify command that must outlast the gates)
        )
        self.env['OUT'] = str(Path(self.tmp.name) / 'lane.txt')
        self.dispatch(b2)
        self.write('src/util.py', 'X = 5\n')
        self.done(b2, self.commit('slow step'))
        report = str(self.store / 'reports' / b2.name)
        self.env['KITCHEN_STEP_SLICE_S'] = '1'
        out = self.run_sh('finish', str(self.store), report, code=4)
        self.assertIn('Verify commands are still running', out)
        self.assertIn(f'kitchen.sh finish {self.store} {report}', out)
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        self.assertIn(
            'verify: pass (1 commands from the brief)',
            self.run_sh('finish', str(self.store), report),
        )
        self.assertEqual(Path(self.env['OUT']).read_text(), 'sidekick|demo')

    def test_a_finish_whose_verify_worker_died_says_so_and_reruns_it(self):
        b = self.brief('001', 'held', ['src/**', 'tests/**'])
        self.verify_block(
            b,
            'test -e "$GO" || sleep 60',  # kitchen-allow: test-sleep (a Verify command that outlives its killed worker)
        )
        self.env['GO'] = str(Path(self.tmp.name) / 'go')
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.done(b, self.commit('held step'))
        report = str(self.store / 'reports' / b.name)
        self.env['KITCHEN_STEP_SLICE_S'] = '0'
        self.assertIn(
            'Verify commands are still running',
            self.run_sh('finish', str(self.store), report, code=4),
        )
        job = next(p.parent for p in self.store.rglob('pid'))
        self.kill_worker(job)
        Path(self.env['GO']).touch()
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        out = self.run_sh('finish', str(self.store), report)
        self.assertIn(
            f'verify: the worker died without a result (log: {job}/out.died.1); rerunning\n',
            out,
        )
        self.assertIn('verify: pass (1 commands from the brief)', out)

    def test_a_finish_whose_verify_worker_keeps_dying_refuses_each_time_and_keeps_each_log(
        self,
    ):
        b = self.brief('001', 'dies', ['src/**', 'tests/**'])
        self.verify_block(
            b, "python3 -c 'import os, signal; os.killpg(0, signal.SIGKILL)'"
        )
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.done(b, self.commit('step'))
        report = str(self.store / 'reports' / b.name)
        self.env.update({'KITCHEN_STEP_SLICE_S': '30', 'KITCHEN_STEP_POLL_S': '0.05'})
        for n in (1, 2):
            out = self.run_sh('finish', str(self.store), report, code=1)
            job = next(p.parent for p in self.store.rglob('pid'))
            self.assertIn(f'(log: {job}/out.died.{n}); rerunning', out)
            self.assertIn(
                f'the verify worker died again without a result (log: {job}/out)', out
            )
        self.assertTrue((job / 'out.died.1').is_file())
        self.assertTrue((job / 'out.died.2').is_file())

    def test_finish_shows_what_each_verify_command_printed_and_warns_on_a_usage_screen(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(
            b,
            "printf 'ran 3 tests\\n\\n3 passed\\n'\nprintf 'Usage: bun run [flags] <script>\\n  e2e\\n'",
        )
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.done(b, self.commit('step'))
        out = self.run_sh(
            'finish', str(self.store), str(self.store / 'reports' / b.name)
        )
        self.assertIn('verify: pass (2 commands from the brief)', out)
        self.assertIn('    output: 2 lines; last: 3 passed\n', out)
        self.assertIn(
            '    output: 2 lines; last: e2e\n    warn: its output opens with a usage line: it may have printed help instead of running\n',
            out,
        )
        self.assertEqual(out.count('warn:'), 1)

    def test_gates_mode_verifies_without_a_verifier(self):
        b = self.brief('001', 'docs', ['docs/**'])
        self.dispatch(b)
        self.write('docs/guide.md', '# Guide\nmore\n')
        head = self.commit('docs')
        self.done(b, head)
        base = self.git('rev-parse', 'HEAD~1')
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn('status: clean (gates only', out)
        self.assertNotIn(
            'agent start demo-verifier', (self.fake / 'calls.log').read_text()
        )
        keep = f'refs/pstack/keep/{self.state()["slug"]}/'
        for oid in (base, head):
            self.assertEqual(self.git('rev-parse', f'{keep}{oid}'), oid)

    def verify_app_unit(self, mode, code, stall=False):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        head = self.commit('util')
        self.done(b, head)
        (self.fake / 'verdict-mode').write_text(mode)
        if stall:
            (self.fake / 'stall').touch()
        return b, self.run_sh('verify', str(self.store), '001', code=code)

    def test_verifier_pane_writes_a_clean_verdict_then_goes(self):
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('verifier demo-verifier (pi --model devin/swe-2) in ', out)
        self.assertIn('status: clean\n', out)
        self.assertRegex(out, r'audit: (yes|no)')
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('agent start demo-verifier --kind pi', calls)
        self.assertIn('agent prompt demo-verifier /quit', calls)
        self.assertRegex(calls, r'pane close p\d')
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertIn(
            f'scratch: {self.env["XDG_STATE_HOME"]}/pstack/scratch/runs-demo/', packet
        )
        self.assertIn('gate app behavioral', packet)
        self.assertIn('## Acceptance', packet)

    def test_a_verifier_trust_dialog_names_the_scratch_root_to_trust(self):
        Path(self.env['PSTACK_KITCHEN_ROSTER']).write_text(
            '[verifier]\nkind = "claude"\n'
        )
        (self.fake / 'trust-dialog').touch()
        _, out = self.verify_app_unit('clean', 5)
        root = f'{self.env["XDG_STATE_HOME"]}/pstack/scratch'
        self.assertIn(f'trust the scratch root {root} once', out)
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])

    def test_the_verifier_close_answers_claudes_background_work_dialog(self):
        self.env.update({'PAIR_EXIT_POLL_S': '0', 'KITCHEN_VERIFIER_EXIT_S': '5'})
        (self.fake / 'exit-dialog').mkdir()
        (self.fake / 'exit-dialog/demo-verifier').write_text(
            'Background work is running\n 1. Exit and stop tasks\n 2. Stay\n'
        )
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('status: clean\n', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('agent send-keys demo-verifier enter', calls)
        self.assertFalse((self.fake / 'agents/demo-verifier').exists())
        self.assertRegex(calls, r'pane close p\d')

    def test_a_verifier_exit_herdr_refuses_is_said_and_the_close_goes_on(self):
        self.env.update({'PAIR_EXIT_POLL_S': '0', 'KITCHEN_VERIFIER_EXIT_S': '0'})
        (self.fake / 'exit-refused').mkdir()
        (self.fake / 'exit-refused/demo-verifier').touch()
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('verifier exit refused: {"error":{"code":"exit_refused"}}', out)
        self.assertRegex((self.fake / 'calls.log').read_text(), r'pane close p\d')
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])

    def test_a_verifier_pane_back_at_its_shell_closes_like_any_other(self):
        self.env.update({'PAIR_EXIT_POLL_S': '0', 'KITCHEN_VERIFIER_EXIT_S': '5'})
        (self.fake / 'exit-not-ready').mkdir()
        (self.fake / 'exit-not-ready/demo-verifier').touch()
        _, out = self.verify_app_unit('clean', 0)
        self.assertNotIn('verifier exit refused', out)
        self.assertFalse((self.fake / 'agents/demo-verifier').exists())

    def test_prime_verifier_stops_worker_before_removing_scratch(self):
        self.set_roster(
            '[verifier]\nkind = "prime-agent"\nargs = ["--model", "devin/swe-2", "--daemon-socket", "/tmp/verifier.sock"]\n'
        )
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('status: clean', out)
        calls = [
            json.loads(line)
            for line in (self.fake / 'prime.log').read_text().splitlines()
        ]
        self.assertIn(
            ['stop', 'live-1', '--json', '--socket', '/tmp/verifier.sock'], calls
        )
        self.assertEqual(
            json.loads((self.fake / 'prime-sessions.json').read_text()), []
        )
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])
        self.assertEqual(len(list((self.fake / 'prime-transcripts').iterdir())), 1)

    def test_prime_blocked_provider_verifier_retries_on_fallback(self):
        self.set_roster(
            '[verifier]\nkind = "prime-agent"\n[verifier.fallback]\nkind = "devin"\n'
        )
        _, out = self.verify_app_unit('prime-ratelimit', 0)
        self.assertIn('capacity issues', out)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertIn('status: clean', out)
        self.assertEqual(len(self.verifier_starts()), 2)
        self.assertEqual(
            json.loads((self.fake / 'prime-sessions.json').read_text()), []
        )

    def test_prime_verifier_failed_stop_preserves_scratch_and_prevents_fallback(self):
        self.set_roster(
            '[verifier]\nkind = "prime-agent"\n[verifier.fallback]\nkind = "devin"\n'
        )
        (self.fake / 'prime-stop-fail').touch()
        self.verify_app_unit('prime-ratelimit', 5)
        self.assertEqual(len(self.verifier_starts()), 1)
        verifying = self.state()['verifying']
        self.assertTrue((self.store / 'scratch' / verifying['scratch']).is_dir())
        self.assertTrue(
            (self.fake / 'panes' / self.state()['verifier']['pane_id']).exists()
        )
        # The failed cleanup died holding the close claim; it released it, so the next wait takes the cleanup up again.
        self.assertNotIn('closing', verifying)
        self.assertNotIn('closing', self.state()['verifying'])
        (self.fake / 'prime-stop-fail').unlink()
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1')
        self.assertNotIn('Prime verifier cleanup failed', out)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertIn('status: clean', out)
        self.assertNotIn('verifying', self.state())

    def test_prime_verifier_failed_start_preserves_its_scratch(self):
        self.set_roster('[verifier]\nkind = "prime-agent"\n')
        (self.fake / 'trust-dialog').touch()
        _, out = self.verify_app_unit('clean', 5)
        self.assertRegex(out, r'Prime verifier start failed in p\d+;')
        self.assertIn(
            'its pane and scratch stay for inspection (verify-wait can resume cleanup once its session is reported)',
            out,
        )
        self.assertTrue(
            (self.store / 'scratch' / self.state()['verifying']['scratch']).is_dir()
        )
        self.assertTrue(
            (self.fake / 'panes' / self.state()['verifier']['pane_id']).exists()
        )

    def test_a_prime_verifier_that_reports_no_session_keeps_its_pane_scratch_and_the_record_of_how_it_was_launched(
        self,
    ):
        self.set_roster('[verifier]\nkind = "prime-agent"\n')
        (self.fake / 'prime-no-session').touch()
        _, out = self.verify_app_unit('clean', 5)
        self.assertIn(
            'Prime verifier did not report its session; its pane and scratch stay for inspection',
            out,
        )
        state = self.state()
        scratch = self.store / 'scratch' / state['verifying']['scratch']
        self.assertTrue(scratch.is_dir())
        self.assertTrue((self.fake / 'panes' / state['verifier']['pane_id']).exists())
        # the verifier record says where the daemon was launched and that its session is not stopped; the open verification, how it was asked for
        self.assertEqual(
            (
                state['verifier']['launch_cwd'],
                state['verifier']['session_stopped'],
                state['verifier']['session'],
            ),
            (str(scratch.resolve()), False, None),
        )
        verifying = state['verifying']
        self.assertEqual(
            (
                verifying['units'],
                verifying['argv'],
                verifying['entry']['kind'],
                verifying['entry']['source'],
            ),
            ('001', ['001'], 'prime-agent', 'roster'),
        )

    def test_a_batch_over_review_max_batch_diff_is_refused_but_a_fix_verified_with_its_unit_is_not(
        self,
    ):
        text = (self.repo / '.agents/kitchen.toml').read_text()
        self.write(
            '.agents/kitchen.toml',
            text.replace('[review]\n', '[review]\nmax_batch_diff = 1\n'),
        )
        self.commit('cap the batch')
        self.three_units()
        out = self.run_sh('verify', str(self.store), '001', '002', code=2)
        self.assertRegex(
            out,
            r'batch of \d+ diff lines is over review.max_batch_diff; verify each unit',
        )
        self.assertNotIn('verifying', self.state())
        self.assertNotIn('verifier demo-verifier', out)
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        self.assertIn('status: clean', out)

    def test_the_pairs_permission_report_and_notify_work_through_kitchen_sh(self):
        self.assertEqual(self.run_sh('permission').strip(), 'auto')
        self.assertEqual(
            self.run_sh('report', str(self.store)).strip(),
            f'{self.store}/reports/000-ready.md',
        )
        newest = self.store / 'reports/001-util.md'
        newest.write_text('# R\n\nstatus: done\nhead: x\n')
        self.assertEqual(self.run_sh('report', str(self.store)).strip(), str(newest))
        self.assertEqual(
            self.run_sh('report', str(self.store), '001').strip(), str(newest)
        )
        self.assertIn(
            'no report found', self.run_sh('report', str(self.store), '007', code=4)
        )
        out = self.run_sh('notify', str(self.store), str(newest))
        self.assertEqual(
            out.strip(), f'prompted master demo-master (was idle) with REPORT {newest}'
        )
        self.assertIn(
            f'agent prompt demo-master pstack-kitchen REPORT {newest}',
            (self.fake / 'calls.log').read_text(),
        )
        self.assertIn(
            f'file not found: {self.store}/reports/none.md',
            self.run_sh(
                'notify', str(self.store), f'{self.store}/reports/none.md', code=1
            ),
        )

    def test_the_consultants_new_consult_consult_and_advice_work_through_kitchen_sh(
        self,
    ):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'codex',
            code=4,
        )
        self.brief('001', 'util', ['src/**', 'tests/**'])
        self.assertIn(
            '--kind must be design, finding, objection, review, or glance',
            self.run_sh('new-consult', str(self.store), '001', code=1),
        )
        consult = Path(
            self.run_sh(
                'new-consult', str(self.store), '001', '--kind', 'finding'
            ).strip()
        )
        self.assertEqual(consult, self.store / 'consults/001-util-c1.md')
        self.assertIn(
            'still has unfilled placeholders',
            self.run_sh('consult', str(self.store), str(consult), code=1),
        )
        consult.write_text(
            re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL)
        )
        self.assertIn('no advice found', self.run_sh('advice', str(self.store), code=4))
        out = self.run_sh(
            'consult', str(self.store), str(consult), '--timeout', '1500', code=4
        )
        self.assertIn('advice: missing', out)
        self.assertIn(
            f'agent prompt demo-consultant pstack-kitchen CONSULT {consult} --wait --timeout 1500',
            (self.fake / 'calls.log').read_text(),
        )
        advice = self.store / 'advice/001-util-c1.md'
        advice.parent.mkdir(exist_ok=True)
        advice.write_text('# A\n\nstatus: answered\n\nadvice here\n')
        out = self.run_sh('consult', str(self.store), str(consult), '--timeout', '1500')
        self.assertIn(f'advice: {advice}\nadvice_status: answered', out)
        self.assertEqual(self.run_sh('advice', str(self.store)).strip(), str(advice))
        self.assertEqual(
            self.run_sh(
                'new-consult', str(self.store), '001', '--kind', 'glance'
            ).strip(),
            str(self.store / 'consults/001-util-c2.md'),
        )
        self.assertIn(
            'unit 001 already had its glance; send a finding consult, or pass --force',
            self.run_sh(
                'new-consult', str(self.store), '001', '--kind', 'glance', code=7
            ),
        )

    def test_consult_flags_are_checked_before_any_state_changes(self):
        self.brief('001', 'util', ['src/**', 'tests/**'])
        self.assertIn(
            '--kind needs a value',
            self.run_sh('new-consult', str(self.store), '001', '--kind', code=1),
        )
        self.assertIn(
            'unknown option --bogus',
            self.run_sh('new-consult', str(self.store), '001', '--bogus', code=1),
        )
        self.assertFalse(
            (self.store / 'consults').exists()
            and list((self.store / 'consults').iterdir())
        )
        consult = Path(
            self.run_sh(
                'new-consult', str(self.store), '001', '--kind', 'finding'
            ).strip()
        )
        consult.write_text(
            re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL)
        )
        before = consult.read_text()
        self.assertIn(
            '--timeout needs a value',
            self.run_sh('consult', str(self.store), str(consult), '--timeout', code=1),
        )
        self.assertIn(
            '--every must be a whole number of minutes',
            self.run_sh(
                'consult', str(self.store), str(consult), '--every', '2x', code=1
            ),
        )
        self.assertEqual(consult.read_text(), before)
        self.assertNotIn('consult', self.state())
        self.assertNotIn('CONSULT', (self.fake / 'calls.log').read_text())

    def test_scratch_lives_outside_the_repo_and_leaves_no_trace(self):
        path = Path(
            self.run_sh('scratch', str(self.store), '001-x-review').splitlines()[0]
        )
        root = Path(self.env['XDG_STATE_HOME']) / 'pstack/scratch'
        self.assertEqual(path.parent.parent, root)
        self.assertFalse((self.repo / '.git/pstack-scratch').exists())
        self.assertEqual(
            (self.store / 'scratch/001-x-review').resolve(), path.resolve()
        )
        self.assertTrue((path / 'calc.py').exists() or (path / 'src/app.py').exists())
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.run_sh('scratch', str(self.store), '001-x-review', '--remove')
        self.assertFalse(path.exists())
        self.assertFalse((self.store / 'scratch/001-x-review').is_symlink())
        self.assertEqual(list(root.iterdir()), [])

    def test_scratch_root_follows_pstack_scratch_root(self):
        root = Path(self.tmp.name) / 'elsewhere'
        self.env['PSTACK_SCRATCH_ROOT'] = str(root)
        path = Path(
            self.run_sh('scratch', str(self.store), '001-x-review').splitlines()[0]
        )
        self.assertEqual(path.parent.parent, root)
        self.run_sh('scratch', str(self.store), '001-x-review', '--remove')
        self.assertFalse(path.exists())

    def test_a_scratch_another_repo_left_under_the_same_run_name_is_refused(self):
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        subprocess.run(['git', 'init', '-q', str(other)], check=True, timeout=60)
        subprocess.run(
            ['git', '-C', str(other), 'commit', '-q', '--allow-empty', '-m', 'x'],
            check=True,
            timeout=60,
            env=self.env,
        )
        leftover = (
            Path(self.env['XDG_STATE_HOME']) / 'pstack/scratch/runs-demo/001-x-review'
        )
        leftover.parent.mkdir(parents=True)
        subprocess.run(
            [
                'git',
                '-C',
                str(other),
                'worktree',
                'add',
                '-q',
                '--detach',
                str(leftover),
            ],
            check=True,
            timeout=60,
        )
        out = self.run_sh('scratch', str(self.store), '001-x-review', code=1)
        self.assertIn(
            f'{leftover} is left over from another repository; remove it (git -C ', out
        )
        self.assertTrue(leftover.is_dir())

    def test_a_scratch_made_under_the_old_git_root_is_still_removed(self):
        old = self.repo / '.git/pstack-scratch/runs-demo/001-x-review'
        old.parent.mkdir(parents=True)
        self.git('worktree', 'add', '--detach', str(old), 'HEAD')
        (self.store / 'scratch').mkdir(exist_ok=True)
        (self.store / 'scratch/001-x-review').symlink_to(old)
        out = self.run_sh('scratch', str(self.store), '001-x-review', '--remove')
        self.assertEqual(out, f'removed {old}\n')
        self.assertFalse(old.exists())
        self.assertNotIn(str(old), self.git('worktree', 'list'))

    def test_a_trust_dialog_is_declined_not_answered(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        (self.fake / 'trust-dialog').touch()
        out = self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'claude',
            code=5,
        )
        self.assertIn('claude asks whether to trust', out)
        self.assertIn('decision is yours', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertRegex(calls, r'pane send-keys p\d+ esc')
        self.assertNotRegex(calls, r'send-keys \S+ enter')
        sent = re.search(r'pane send-keys (p\d+) esc', calls)
        assert sent is not None
        pane = sent.group(1)
        self.assertIn(f'pane close {pane}', calls)
        self.assertNotIn('escalate', (self.store / 'events.tsv').read_text())
        self.assertEqual(self.state().get('escalations', []), [])

    def test_spawned_claude_agents_load_no_mcp_servers(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        # The fake writes no advice/000-ready.md, so the bootstrap ends 4; the start line is what matters.
        self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'claude',
            code=4,
        )
        start = [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-consultant')
        ][-1]
        self.assertIn('--strict-mcp-config --mcp-config', start)
        self.assertIn('references/no-mcp.json', start)
        self.set_roster(
            '[verifier.escalated]\nkind = "claude"\nargs = ["--model", "m", "--mcp-config", "mine.json"]\n'
        )
        out = self.verify_landing()
        (vstart,) = self.verifier_starts()
        self.assertIn('--mcp-config mine.json', vstart)
        self.assertNotIn('--strict-mcp-config', vstart)
        self.assertIn('verifier demo-verifier (claude --model m)', out)

    def test_spawned_codex_agents_turn_off_every_mcp_server_codex_reports(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        (self.fake / 'codex-mcp.json').write_text(
            json.dumps(
                [
                    {'name': 'chrome-devtools', 'enabled': True},
                    {'name': 'railway', 'enabled': True},
                    {'name': 'off-already', 'enabled': False},
                    {'name': 'has.dot', 'enabled': True},
                ]
            )
        )
        (self.fake / 'codex-mcp-plugins.json').write_text(
            json.dumps(
                [
                    {'name': 'cua_repl', 'enabled': True, 'transport': 'stdio'},
                    {'name': 'node_repl', 'enabled': True, 'transport': 'stdio'},
                ]
            )
        )
        self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'codex',
            code=4,
        )
        calls = (self.fake / 'calls.log').read_text()
        start = [
            line
            for line in calls.splitlines()
            if line.startswith('agent start demo-consultant')
        ][-1]
        self.assertIn(
            '--disable plugins -c mcp_servers.chrome-devtools.enabled=false -c mcp_servers.railway.enabled=false',
            start,
        )
        self.assertNotIn('off-already', start)
        self.assertNotIn('has.dot', start)
        self.assertNotIn('cua_repl', start)
        self.assertNotIn('node_repl', start)
        self.assertIn(
            f'codex --disable plugins mcp list --json (cwd {self.repo})', calls
        )
        self.set_roster(
            '[verifier.escalated]\nkind = "codex"\nargs = ["-c", "mcp_servers.railway.enabled=true"]\n'
        )
        self.verify_landing()
        (vstart,) = self.verifier_starts()
        self.assertIn('mcp_servers.railway.enabled=true', vstart)
        self.assertNotIn('chrome-devtools', vstart)

    def filled_handoff(self):
        self.run_sh('new-handoff', str(self.store))
        handoff = self.store / 'session-handoff.md'
        text = re.sub(r'\{\{.*?\}\}', '', handoff.read_text(), flags=re.S)
        handoff.write_text(
            text.replace('## Goal\n', '## Goal\n\nShip util.\n').replace(
                '## Next\n', '## Next\n\n1. kitchen.sh dispatch 002\n'
            )
        )
        (self.fake / 'session-ids').mkdir(exist_ok=True)
        (self.fake / 'session-ids/demo-master').write_text('m-gen1 /work\n')
        return handoff

    def new_master_pane(self):
        return self.state()['master']['pane_id']

    def test_new_handoff_lists_the_units_and_rotate_master_hands_the_run_to_a_new_master_beside_it(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '001')
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.brief('002', 'next', ['docs/**'])
        out = self.run_sh('new-handoff', str(self.store))
        handoff = self.store / 'session-handoff.md'
        self.assertEqual(out.splitlines()[0], str(handoff))
        text = handoff.read_text()
        self.assertIn('# Handoff: demo (master generation 1)', text)
        self.assertIn('| 001 util | done | clean | accept |', text)
        self.assertIn('| 002 next | - | - | - |', text)
        self.assertIn(
            'still has unfilled placeholders',
            self.run_sh('rotate-master', str(self.store), code=1),
        )
        handoff = self.filled_handoff()
        out = self.run_sh('rotate-master', str(self.store))
        self.assertIn(
            'handoff recorded: '
            + str(self.store / 'sessions/master-1-handoff.md')
            + ' (generation 1, session m-gen1)',
            out,
        )
        self.assertIn('claude --resume m-gen1', out)
        state = self.state()['master']
        new_pane = state['pane_id']
        self.assertNotEqual(new_pane, 'p0')
        self.assertEqual(
            (state['name'], state['generation'], state['units_since_handoff']),
            ('demo-master-g2', 2, 0),
        )
        self.assertEqual(
            state['previous'],
            {'name': 'demo-master', 'pane_id': 'p0', 'session': 'm-gen1'},
        )
        self.assertEqual([s['session'] for s in state['sessions']], ['m-gen1'])
        self.assertIn(
            f'next master: demo-master-g2 (generation 2) in {new_pane}, remote control on',
            out,
        )
        bootstrap = (self.fake / 'master-bootstrap').read_text()
        self.assertIn(
            f'pstack-kitchen MASTER {self.store} (generation 2). Run ', bootstrap
        )
        self.assertIn(
            f'kitchen.sh take-master {self.store} first, then read '
            f'~/.agents/skills/pstack-kitchen/references/master.md and {self.store}/session-handoff.md, '
            'each in a read of its own (a file-read tool, not one cat of several files).',
            bootstrap,
        )
        calls = (self.fake / 'calls.log').read_text()
        self.assertNotIn('agent prompt demo-master /exit', calls)
        start = [
            line
            for line in calls.splitlines()
            if line.startswith('agent start demo-master-g2')
        ][-1]
        self.assertIn(f'--kind claude --pane {new_pane}', start)
        self.assertIn('--strict-mcp-config --mcp-config', start)
        self.assertIn('--permission-mode', start)
        self.assertIn('--remote-control demo-master-g2', start)
        self.assertTrue(
            (self.fake / 'agents/demo-master').exists(),
            'the old master stays until take-master',
        )
        self.assertIn(
            'a hand-over is open', self.run_sh('rotate-master', str(self.store), code=5)
        )
        self.assertIn(
            f"take-master runs from the new master's pane {new_pane}",
            self.run_sh('take-master', str(self.store), code=5),
        )
        self.env['HERDR_PANE_ID'] = new_pane
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        self.run_sh('classify', str(self.store), str(b))
        out = self.run_sh('take-master', str(self.store))
        self.assertIn(
            'warning: unreviewed: unit 001 cites no joo run; review its range with joo',
            out,
        )
        self.assertIn('closed the old master demo-master (p0)', out)
        self.assertIn(f'master: demo-master in {new_pane} (generation 2)', out)
        self.assertFalse((self.fake / 'panes/p0').exists())
        self.assertEqual(
            (self.fake / 'agents/demo-master').read_text(), 'idle claude\n'
        )
        self.assertFalse((self.fake / 'agents/demo-master-g2').exists())
        state = self.state()['master']
        self.assertEqual((state['name'], state['pane_id']), ('demo-master', new_pane))
        self.assertNotIn('previous', state)
        self.assertIn(
            f'no hand-over open; demo-master is the master in {new_pane}',
            self.run_sh('take-master', str(self.store)),
        )
        events = (self.store / 'events.tsv').read_text()
        for row in (
            '\tmaster\thandoff\t-\tgeneration:1',
            '\tmaster\tspawn\t-\tclaude:generation:2',
            '\tmaster\ttakeover\t-\tgeneration:2',
        ):
            self.assertIn(row, events)
        self.assertEqual(
            (self.store / 'sessions/master-1-handoff.md').read_text(),
            handoff.read_text(),
        )

    def test_a_next_master_that_does_not_start_leaves_the_old_one_in_charge(self):
        self.filled_handoff()
        (self.fake / 'trust-dialog').touch()
        out = self.run_sh('rotate-master', str(self.store), code=5)
        self.assertIn('asks whether to trust', out)
        state = self.state()['master']
        self.assertEqual(
            (state['name'], state['pane_id'], state.get('generation', 1)),
            ('demo-master', 'p0', 1),
        )
        self.assertNotIn('previous', state)
        self.assertNotIn('sessions', state)
        self.assertFalse((self.store / 'sessions/master-1-handoff.md').exists())

    def test_a_next_master_that_fails_to_start_leaves_its_pane_and_a_log(self):
        self.filled_handoff()
        (self.fake / 'start-fail').touch()
        out = self.run_sh('rotate-master', str(self.store), code=2)
        log = self.store / 'sessions/master-g2-start-failed.log'
        found = re.search(r'did not start in (p\d+);', out)
        assert found, out
        pane = found.group(1)
        self.assertIn(
            f'the next master did not start in {pane}; its pane stays open and the log is {log}; '
            'you stay the master (generation 1), so carry on and rotate at a later boundary',
            out,
        )
        text = log.read_text()
        self.assertIn('--permission-mode', text)
        self.assertIn('start_failed', text)
        self.assertRegex(text, r'(?s)pane screen.*\S')
        self.assertNotIn(f'pane close {pane}', (self.fake / 'calls.log').read_text())
        self.assertIn(
            '\tmaster\trotate-failed\t-\tgeneration:2:start_failed',
            (self.store / 'events.tsv').read_text(),
        )
        self.assertEqual(self.state()['master']['name'], 'demo-master')

    def test_a_next_master_blocked_by_a_trust_dialog_still_closes_its_pane_and_logs(
        self,
    ):
        self.filled_handoff()
        (self.fake / 'trust-dialog').touch()
        out = self.run_sh('rotate-master', str(self.store), code=5)
        log = self.store / 'sessions/master-g2-start-failed.log'
        self.assertIn(
            f'claude asks whether to trust {self.state()["cwd"]}, and that decision is yours: '
            f'open claude there once and trust the folder, then rerun kitchen.sh rotate-master {self.store}; '
            f'the log is {log}; you stay the master (generation 1)',
            out,
        )
        self.assertNotIn('rerun the spawn', out)
        self.assertNotIn('pick another kind', out)
        self.assertIn('agent_not_ready', log.read_text())
        self.assertIn(
            '\tmaster\trotate-failed\t-\tgeneration:2:agent_not_ready',
            (self.store / 'events.tsv').read_text(),
        )
        self.assertIn('pane close', (self.fake / 'calls.log').read_text())

    def test_rotating_again_after_an_abandoned_rotation_keeps_the_generation(self):
        self.filled_handoff()
        # The in-place restart recorded the session, then never started generation 2.
        state = self.state()
        state['master'].update(
            generation=2,
            sessions=[
                {'generation': 1, 'session': 'm-gen1', 'handoff': 'old', 'at': 't'}
            ],
        )
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.env['KITCHEN_MASTER_REMOTE_CONTROL'] = '0'
        out = self.run_sh('rotate-master', str(self.store))
        self.assertIn('(generation 1, session m-gen1)', out)
        self.assertNotIn('remote control', out.split('next master:')[1].splitlines()[0])
        master = self.state()['master']
        self.assertEqual((master['name'], master['generation']), ('demo-master-g2', 2))
        self.assertEqual(
            [(s['generation'], s['session']) for s in master['sessions']],
            [(1, 'm-gen1')],
        )
        start = [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-master-g2')
        ][-1]
        self.assertNotIn('--remote-control', start)

    def test_a_required_review_engine_holds_land_check_until_each_review_note_cites_a_run(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '001')
        review = self.store / 'reviews/001-util.md'
        review.write_text('verdict: accept\n')
        self.assertIn(
            'land-check: pass (review engine: none)',
            self.run_sh('land-check', str(self.store)),
        )
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        self.run_sh('classify', str(self.store), str(b))
        self.assertIn('\nreview-engine: joo\n', b.read_text())
        for note in (
            'verdict: accept\n',
            'verdict: accept\nengine: joo\n',
            'verdict: accept\nengine: codex r1\n',
        ):
            review.write_text(note)
            out = self.run_sh('land-check', str(self.store), code=2)
            self.assertIn(
                f'unreviewed: unit 001 cites no joo run; review its range with joo and add "engine: joo <run id or artifact path>" to {review}',
                out,
            )
        review.write_text('verdict: accept\nhead: x\nengine: joo /tmp/joo-001.json\n')
        self.assertIn(
            'land-check: pass (review engine: joo)',
            self.run_sh('land-check', str(self.store)),
        )

    def test_a_review_engine_named_after_dispatch_does_not_reach_back_to_the_unit(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.assertIn('\nreview-engine: none\n', b.read_text())
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '001')
        review = self.store / 'reviews/001-util.md'
        review.write_text('verdict: accept\n')
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn('land-check: pass (review engine: joo)', out)
        self.assertNotIn('unreviewed', out)
        # A brief classified before classify stamped the engine is held to the kitchen's engine now.
        stamped = b.read_text()
        for unstamped in (
            stamped.replace('review-engine: none\n', ''),
            stamped.replace(
                'review-engine: none\n',
                'review-engine: set by kitchen.sh classify at dispatch\n',
            ),
        ):
            b.write_text(unstamped)
            self.assertIn(
                'unreviewed: unit 001 cites no joo run',
                self.run_sh('land-check', str(self.store), code=2),
            )
        b.write_text(stamped.replace('review-engine: none\n', ''))
        # A stamped engine holds after the kitchen drops it.
        b.write_text(
            b.read_text().replace('plan: none\n', 'plan: none\nreview-engine: joo\n')
        )
        toml.write_text(toml.read_text().replace('engine = "joo"\n', ''))
        self.assertIn(
            'unreviewed: unit 001 cites no joo run',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_deletion_only_unit_may_skip_the_required_engine_with_proof(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        self.commit('require joo')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.git('rm', '-q', 'tests/test_app.py')
        self.done(b, self.commit('delete the old test'))
        self.run_sh('verify', str(self.store), '001')
        review = self.store / 'reviews/001-util.md'
        skip = 'verdict: accept\nengine: skipped (deletion-only, proof: git diff --numstat shows 0 added)\n'
        review.write_text(skip)
        self.assertIn(
            'land-check: pass (review engine: joo)',
            self.run_sh('land-check', str(self.store)),
        )
        review.write_text('verdict: accept\nengine: skipped (deletion-only)\n')
        self.assertIn(
            'unreviewed: unit 001 cites no joo run',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def skipped_unit(self, setup, change):
        """A unit that makes `change` to files `setup` committed before its dispatch; its review note skips the required engine."""
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        setup()
        self.commit('require joo and lay out the files')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        change()
        self.done(b, self.commit('the change'))
        self.run_sh('verify', str(self.store), '--timeout', '60', '001')
        review = self.store / 'reviews/001-util.md'
        review.write_text(
            'verdict: accept\nengine: skipped (deletion-only, proof: git diff --numstat)\n'
        )
        return review

    def test_a_rename_cannot_use_the_deletion_only_skip(self):
        self.skipped_unit(
            lambda: self.write('src/old.py', 'O = 1\n'),
            lambda: self.git('mv', 'src/old.py', 'src/new.py'),
        )
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_mode_change_cannot_use_the_deletion_only_skip(self):
        self.skipped_unit(
            lambda: self.write('src/run.sh', '#!/bin/sh\n'),
            lambda: (self.repo / 'src/run.sh').chmod(0o755),
        )
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_modified_binary_with_a_non_ascii_name_cannot_use_the_deletion_only_skip(
        self,
    ):
        def lay_out():
            (self.repo / 'assets').mkdir(exist_ok=True)
            (self.repo / 'assets/caf\u00e9.png').write_bytes(bytes(range(256)))

        self.skipped_unit(
            lay_out,
            lambda: (self.repo / 'assets/caf\u00e9.png').write_bytes(
                bytes(range(255, -1, -1))
            ),
        )
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_path_with_a_tab_is_read_exactly_by_the_deletion_only_check(self):
        name = 'assets/we\tird.bin'

        def lay_out():
            (self.repo / 'assets').mkdir(exist_ok=True)
            (self.repo / name).write_bytes(bytes(range(256)))

        self.skipped_unit(
            lay_out, lambda: (self.repo / name).write_bytes(bytes(range(255, -1, -1)))
        )
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_deleted_path_with_a_tab_can_use_the_deletion_only_skip(self):
        name = 'assets/we\tird.bin'

        def lay_out():
            (self.repo / 'assets').mkdir(exist_ok=True)
            (self.repo / name).write_bytes(bytes(range(256)))

        self.skipped_unit(lay_out, lambda: self.git('rm', '-q', name))
        self.assertIn(
            'land-check: pass (review engine: joo)',
            self.run_sh('land-check', str(self.store)),
        )

    def test_a_dispatch_head_that_no_longer_resolves_refuses_the_deletion_only_skip(
        self,
    ):
        self.skipped_unit(
            lambda: self.write('src/old.py', 'O = 1\n'),
            lambda: self.git('rm', '-q', 'src/old.py'),
        )
        state = self.state()
        state['heads']['001-util'] = 'deadbeef' * 5
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_mode_change_with_deleted_lines_cannot_use_the_deletion_only_skip(self):
        self.skipped_unit(
            lambda: self.write('src/run.sh', '#!/bin/sh\necho a\necho b\n'),
            lambda: (
                self.write('src/run.sh', '#!/bin/sh\necho a\n'),
                (self.repo / 'src/run.sh').chmod(0o755),
            ),
        )
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_modified_file_whose_name_begins_with_a_tab_and_gains_a_line_cannot_use_the_skip(
        self,
    ):
        name = '\ttab.py'
        self.skipped_unit(
            lambda: self.write(name, 'A = 1\n'),
            lambda: self.write(name, 'A = 1\nB = 2\n'),
        )
        self.assertIn(
            'skipped is only for a deletion-only range',
            self.run_sh('land-check', str(self.store), code=2),
        )

    def test_a_file_whose_name_ends_with_a_tab_and_loses_a_line_can_use_the_skip(self):
        name = 'src/tab\t'
        self.skipped_unit(
            lambda: self.write(name, 'A = 1\nB = 2\n'),
            lambda: self.write(name, 'A = 1\n'),
        )
        self.assertIn(
            'land-check: pass (review engine: joo)',
            self.run_sh('land-check', str(self.store)),
        )

    def test_a_deletion_that_removes_lines_from_a_text_file_can_use_the_skip(self):
        self.skipped_unit(
            lambda: self.write('src/lines.py', 'A = 1\nB = 2\nC = 3\n'),
            lambda: self.write('src/lines.py', 'A = 1\nC = 3\n'),
        )
        self.assertIn(
            'land-check: pass (review engine: joo)',
            self.run_sh('land-check', str(self.store)),
        )

    def test_a_binary_deletion_can_use_the_deletion_only_skip(self):
        def lay_out():
            (self.repo / 'src').mkdir(exist_ok=True)
            (self.repo / 'src/blob.bin').write_bytes(bytes(range(256)))

        self.skipped_unit(lay_out, lambda: self.git('rm', '-q', 'src/blob.bin'))
        self.assertIn(
            'land-check: pass (review engine: joo)',
            self.run_sh('land-check', str(self.store)),
        )

    def test_skipping_the_required_engine_is_refused_for_a_unit_that_adds_lines(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        self.commit('require joo')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/new.py', 'Z = 1\n')
        self.done(b, self.commit('add a module'))
        self.run_sh('verify', str(self.store), '001')
        review = self.store / 'reviews/001-util.md'
        review.write_text(
            'verdict: accept\nengine: skipped (deletion-only, proof: none)\n'
        )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f'unreviewed: unit 001 cites no joo run; review its range with joo and add "engine: joo <run id or artifact path>" to {review}; skipped is only for a deletion-only range',
            out,
        )

    def test_a_clean_verify_warns_of_a_review_note_without_the_required_engine_run(
        self,
    ):
        self.three_units()
        review = self.store / 'reviews/001-u1.md'
        review.write_text('verdict: accept\n')
        out = self.run_sh('verify', str(self.store), '002')
        self.assertNotIn('unreviewed', out)
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        self.run_sh('classify', str(self.store), str(self.store / 'briefs/001-u1.md'))
        out = self.run_sh('verify', str(self.store), '003')
        self.assertIn(
            f'warning: unreviewed: unit 001 cites no joo run; review its range with joo and add "engine: joo <run id or artifact path>" to {review}',
            out,
        )
        review.write_text('verdict: accept\nengine: joo /tmp/joo-001.json\n')
        self.assertNotIn(
            'unreviewed', self.run_sh('verify', str(self.store), '002', '003')
        )

    def test_verify_nudges_a_handoff_after_the_configured_number_of_units(self):
        self.env['KITCHEN_MASTER_ROTATE_UNITS'] = '2'
        self.three_units()
        out = self.run_sh('verify', str(self.store), '001')
        self.assertNotIn('since your last handoff', out)
        out = self.run_sh('verify', str(self.store), '002')
        self.assertIn(
            'master: 2 units verified since your last handoff; rotate at this boundary: kitchen.sh new-handoff',
            out,
        )
        self.assertEqual(self.state()['master']['units_since_handoff'], 2)
        # Every clean verdict from the threshold on, until rotate-master resets the count.
        out = self.run_sh('verify', str(self.store), '003')
        self.assertIn('master: 3 units verified since your last handoff', out)
        b = self.brief('004', 'u4', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 44\n')
        self.write('tests/test_app.py', 'x = 44\n')
        self.done(b, self.commit('u4'))
        out = self.run_sh('verify', str(self.store), '004')
        self.assertIn('master: 4 units verified since your last handoff', out)

    def test_a_clean_landing_verdict_counts_toward_the_handoff_nudge(self):
        self.env['KITCHEN_MASTER_ROTATE_UNITS'] = '2'
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.three_units()
        out = self.run_sh('verify', str(self.store), '001')
        self.assertNotIn('since your last handoff', out)
        out = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn('status: clean', out)
        self.assertIn(
            'rotate only if more work follows (2 units verified since your last handoff)',
            out,
        )
        self.assertEqual(self.state()['master']['units_since_handoff'], 2)
        out = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn(
            'rotate only if more work follows (3 units verified since your last handoff)',
            out,
        )

    def landed(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.three_units()
        unit = self.run_sh('verify', str(self.store), '001')
        landing = self.run_sh('verify', str(self.store), '--landing')
        return unit, landing

    def test_a_landing_with_nothing_left_says_to_rotate_only_if_more_work_follows(self):
        unit, landing = self.landed()
        self.assertNotIn('rotate', unit)
        self.assertIn('status: clean', landing)
        self.assertIn(
            'master: a clean landing verdict ends an issue; nothing is queued or running, so rotate only if more work follows (2 units verified since your last handoff): kitchen.sh new-handoff',
            landing,
        )
        self.assertEqual(self.state()['master']['units_since_handoff'], 2)
        self.assertTrue(self.state()['master']['landed_since_handoff'])

    def test_a_landing_with_a_queued_brief_says_to_rotate_at_this_boundary(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.three_units()
        self.run_sh('verify', str(self.store), '001')
        queued = self.brief('004', 'u4', ['docs/**'])
        (self.store / 'queue').write_text(f'{queued}\n')
        landing = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn(
            'master: a clean landing verdict ends an issue; rotate at this boundary (2 units verified since your last handoff): kitchen.sh new-handoff',
            landing,
        )
        self.assertTrue(self.state()['master']['landed_since_handoff'])

    def test_a_landing_while_a_unit_is_out_says_to_rotate_at_this_boundary(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.three_units()
        self.run_sh('verify', str(self.store), '001')
        self.dispatch(self.brief('004', 'u4', ['docs/**']))
        landing = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn(
            'master: a clean landing verdict ends an issue; rotate at this boundary (2 units verified since your last handoff): kitchen.sh new-handoff',
            landing,
        )
        self.assertTrue(self.state()['master']['landed_since_handoff'])

    def test_dispatch_after_a_landing_repeats_the_rotation_advice_and_still_sends(self):
        reminder = 'master: a landing was verified since your last handoff; rotate before the next issue: kitchen.sh new-handoff'
        b = self.brief('004', 'u4', ['docs/**'])
        self.assertNotIn(reminder, self.dispatch(b))
        self.landed()
        b = self.brief('005', 'u5', ['docs/**'])
        out = self.dispatch(b)
        self.assertEqual(out.count(reminder), 1)
        self.assertIn('report: missing', out)
        self.assertEqual(self.state()['dispatch']['brief'], str(b))

    def test_discuss_after_a_landing_repeats_the_rotation_advice_and_still_sends(self):
        reminder = 'master: a landing was verified since your last handoff; rotate before the next issue: kitchen.sh new-handoff'
        self.landed()
        plan = self.consultant_plan('routine')
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertEqual(out.count(reminder), 1)
        self.assertIn('report_status: agree', out)

    def test_rotate_master_clears_the_landing_rotation_advice(self):
        self.landed()
        self.assertTrue(self.state()['master']['landed_since_handoff'])
        self.run_sh('new-handoff', str(self.store))
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertFalse(self.state()['master']['landed_since_handoff'])
        b = self.brief('005', 'u5', ['docs/**'])
        self.assertNotIn('a landing was verified', self.dispatch(b))

    def test_a_gates_mode_unit_counts_toward_the_handoff_nudge(self):
        self.env['KITCHEN_MASTER_ROTATE_UNITS'] = '2'
        self.three_units(scope=('docs/**',))
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn('gates only', out)
        self.assertNotIn('since your last handoff', out)
        out = self.run_sh('verify', str(self.store), '002')
        self.assertIn('gates only', out)
        self.assertIn('master: 2 units verified since your last handoff', out)

    def muse_consultant_roster(self):
        Path(self.env['PSTACK_KITCHEN_ROSTER']).write_text(
            '[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n[sidekick.fallback]\nkind = "devin"\n'
            '[consultant]\nkind = "muse"\nargs = ["--model", "muse-wonder-full"]\n'
            '[consultant.fallback]\nkind = "claude"\nargs = ["--model", "claude-fable-5-1"]\n'
        )

    def test_a_consultant_whose_muse_preflight_fails_starts_the_roster_fallback(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        (self.fake / 'muse-fail').write_text(
            'Usage limit reached. Wait for usage to reset at 18:00.\n'
        )
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertIn(
            'failover: consultant muse muse-wonder-full -> claude claude-fable-5-1 (preflight: Usage limit reached',
            out,
        )
        start = [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-consultant')
        ][-1]
        self.assertIn('--kind claude', start)
        self.assertIn('--model claude-fable-5-1', start)
        failover = self.state()['consultant']['failovers'][0]
        self.assertEqual(
            (failover['from'], failover['to']),
            ('muse muse-wonder-full', 'claude claude-fable-5-1'),
        )
        self.assertIn(
            '\tmaster\tfailover\t-\tconsultant:muse muse-wonder-full:claude claude-fable-5-1',
            (self.store / 'events.tsv').read_text(),
        )

    def consultant_start(self):
        return [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-consultant')
        ][-1]

    def test_a_consultant_that_cannot_start_starts_the_roster_fallback(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        (self.fake / 'start-fail-kind').write_text('muse')
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertIn('agent start failed in ', out)
        self.assertIn(' shows:\n  Error loading config.toml\n', out)
        self.assertIn(
            'failover: consultant muse muse-wonder-full -> claude claude-fable-5-1 (start: agent start failed in ',
            out,
        )
        start = self.consultant_start()
        self.assertIn('--kind claude', start)
        self.assertIn('--model claude-fable-5-1', start)
        failover = self.state()['consultant']['failovers'][0]
        self.assertEqual(
            (failover['from'], failover['to']),
            ('muse muse-wonder-full', 'claude claude-fable-5-1'),
        )
        closed = [
            l
            for l in (self.fake / 'calls.log').read_text().splitlines()
            if l.startswith('pane close')
        ]
        self.assertEqual(len(closed), 1)

    def test_a_consultant_that_cannot_start_with_no_fallback_says_why(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        (self.fake / 'start-fail-kind').write_text('claude')
        out = self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'claude',
            code=2,
        )
        self.assertIn('agent start failed in ', out)
        self.assertNotIn('failover', out)

    def test_a_consultant_kind_named_in_the_roster_takes_that_entrys_arguments(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'claude',
            code=4,
        )
        self.assertIn('--kind claude', self.consultant_start())
        self.assertIn('--model claude-fable-5-1', self.consultant_start())

    def test_a_consultant_with_no_ready_file_shows_its_pane(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertIn('consultant settled but advice/000-ready.md is missing', out)
        self.assertIn(' shows:\n  pane text\n', out)

    def test_a_consultant_whose_muse_preflight_passes_starts_muse(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertNotIn('failover', out)
        start = [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-consultant')
        ][-1]
        self.assertIn('--kind muse', start)
        self.assertIn('--trust-workspace --model muse-wonder-full', start)
        self.assertIn(
            'exec --model muse-wonder-full Reply with exactly: OK',
            (self.fake / 'muse.log').read_text(),
        )

    def test_a_muse_verifier_starts_trusted_without_approvals(self):
        self.set_roster(
            '[verifier]\nkind = "muse"\nargs = ["--model", "muse-wonder"]\n'
        )
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('status: clean', out)
        (vstart,) = self.verifier_starts()
        self.assertIn('--kind muse', vstart)
        self.assertIn(
            '--approval-mode never --disable-sandbox --trust-workspace --model muse-wonder',
            vstart,
        )
        self.assertNotIn('mcp', vstart)

    def test_close_ends_the_roles_and_closes_their_panes(self):
        state = self.state()
        state['verifying'] = {'verdict': 'x', 'units': '001'}
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.assertIn(
            'a verification is open', self.run_sh('close', str(self.store), code=5)
        )
        del state['verifying']
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.run_sh('close', str(self.store))
        self.assertIn('closed sidekick demo-sidekick', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('agent prompt demo-sidekick /quit', calls)
        self.assertRegex(calls, r'pane close p\d')
        self.assertFalse((self.fake / 'agents/demo-sidekick').exists())
        self.assertTrue(self.state()['sidekick']['closed'])
        self.assertIn(
            'close\tsidekick:pi',
            (self.store / 'events.tsv').read_text().replace('\t-\t', '\t'),
        )
        self.assertEqual(self.run_sh('close', str(self.store)), '')

    def close_with_dialog(self, text, keys):
        self.env['PAIR_EXIT_POLL_S'] = '0'
        (self.fake / 'exit-dialog').mkdir(exist_ok=True)
        (self.fake / 'exit-dialog/demo-sidekick').write_text(text)
        pane = self.state()['sidekick']['pane_id']
        self.assertIn(
            f'closed sidekick demo-sidekick ({pane})\n',
            self.run_sh('close', str(self.store)),
        )
        self.assertEqual(
            [
                l.rsplit(' ', 1)[1]
                for l in self.pane_calls('agent send-keys demo-sidekick')
            ],
            keys,
        )
        self.assertTrue(self.state()['sidekick']['closed'])

    def test_close_gets_through_claudes_exit_dialog_by_stopping_the_tasks(self):
        self.close_with_dialog(
            'Background work is running\n 1. Exit and stop tasks\n 2. Move to background\n 3. Stay\n',
            ['enter'],
        )

    def test_close_gets_through_the_older_move_to_background_and_exit_dialog(self):
        self.close_with_dialog('Move to background and exit?\n', ['1', 'enter'])

    def test_close_sends_no_keys_to_a_pane_that_shows_no_exit_dialog(self):
        self.env['PAIR_EXIT_POLL_S'] = '0'
        # The agent stays after the exit command and leaves on the second pane read: the status check, then the helper's look at the screen.
        (self.fake / 'exit-stuck').mkdir()
        (self.fake / 'exit-stuck/demo-sidekick').write_text('2\n')
        pane = self.state()['sidekick']['pane_id']
        self.assertIn(
            f'closed sidekick demo-sidekick ({pane})\n',
            self.run_sh('close', str(self.store)),
        )
        self.assertEqual(self.pane_calls('agent send-keys demo-sidekick'), [])

    def restart(self, how):
        if how == 'spawn':
            self.run_sh('spawn', str(self.store))
        elif how == 'consultant':
            self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        else:
            state = self.state()
            state['sidekick']['rotation_required'] = True
            (self.store / 'pair.json').write_text(json.dumps(state))
            self.run_sh('rotate', str(self.store))

    def test_a_role_restarted_after_close_is_closed_again_by_the_next_close(self):
        for how, roles in (
            ('spawn', ['sidekick']),
            ('consultant', ['consultant']),
            ('rotate', ['sidekick']),
        ):
            with self.subTest(how):
                self.run_sh('close', str(self.store))
                self.assertTrue(self.state()['sidekick']['closed'])
                self.restart(how)
                for role in roles:
                    self.assertFalse(self.state()[role]['closed'])
                before = len(self.pane_calls('pane close'))
                out = self.run_sh('close', str(self.store))
                for role in roles:
                    pane = self.state()[role]['pane_id']
                    self.assertIn(f'closed {role} demo-{role} ({pane})\n', out)
                    self.assertTrue(self.state()[role]['closed'])
                self.assertEqual(
                    len(self.pane_calls('pane close')), before + len(roles)
                )

    def test_init_refuses_under_the_verifier_role_before_any_herdr_call_or_store_write(
        self,
    ):
        refusal = 'init refuses under PSTACK_KITCHEN_ROLE=verifier: it renames the calling pane, and the kitchen would lose its verifier'
        calls = self.fake / 'calls.log'
        before = calls.read_text()
        self.env['PSTACK_KITCHEN_ROLE'] = 'verifier'
        r = subprocess.run(
            [str(self.script), 'init', 'other'],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(
            (r.returncode, r.stdout, r.stderr), (2, '', f'kitchen.sh: {refusal}\n')
        )
        self.assertEqual(calls.read_text(), before)
        self.assertEqual(
            [p.name for p in self.store.parent.iterdir()], [self.store.name]
        )
        self.env['PSTACK_KITCHEN_ROLE'] = 'sidekick'
        self.assertIn('other', self.run_sh('init', 'other'))

    def test_verify_prompt_left_typed_is_submitted(self):
        _, out = self.verify_app_unit('clean', 0, stall=True)
        self.assertIn('status: clean', out)
        self.assertIn(
            'send-keys demo-verifier enter', (self.fake / 'calls.log').read_text()
        )

    def test_tab_placement_gives_the_verifier_a_tab(self):
        state = json.loads((self.store / 'pair.json').read_text())
        state['placement'] = 'tab'
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.verify_app_unit('clean', 0)
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('--label demo-verifier --no-focus', calls)
        self.assertRegex(calls, r'pane close t\d')

    ROLE_ENV = '--env PSTACK_KITCHEN_ROLE=verifier --env PSTACK_KITCHEN_RUN=demo'

    def pane_calls(self, call):
        return [
            l
            for l in (self.fake / 'calls.log').read_text().splitlines()
            if l.startswith(call)
        ]

    def test_the_verifier_pane_split_carries_the_role_and_run_but_the_sidekicks_does_not(
        self,
    ):
        spawn_splits = self.pane_calls('pane split')
        self.assertTrue(
            spawn_splits and not any('--env' in l for l in spawn_splits), spawn_splits
        )
        self.verify_app_unit('clean', 0)
        added = self.pane_calls('pane split')[len(spawn_splits) :]
        self.assertEqual([l.endswith(self.ROLE_ENV) for l in added], [True], added)

    def test_the_verifier_tab_carries_the_role_and_run_under_tab_placement(self):
        state = self.state()
        state['placement'] = 'tab'
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.verify_app_unit('clean', 0)
        tabs = self.pane_calls('tab create')
        self.assertEqual([l.endswith(self.ROLE_ENV) for l in tabs], [True], tabs)

    def stamp(self, unit, workflow, *parts, overlays=()):
        """Writes a stored composition for a unit whose verifier owns `parts` as (id, text) pairs."""
        verifier = [
            {
                'id': pid,
                'text': text,
                'source': {
                    'root': 'skills',
                    'path': f'poteto-mode/playbooks/{workflow}.md',
                    'select': 'step:1',
                    'digest': 'd' * 12,
                },
                'from': [{'part': pid, 'by': f'workflow:{workflow}'}],
            }
            for pid, text in parts
        ]
        path = self.store / 'compositions' / f'{unit}.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'workflow': workflow,
                    'overlays': {
                        'selected': [
                            {'id': o, 'why': '', 'kind': 'domain'} for o in overlays
                        ],
                        'excluded': [],
                    },
                    'roles': {
                        'master': [],
                        'sidekick': [],
                        'consultant': [],
                        'verifier': verifier,
                    },
                }
            )
        )

    def packet_text(self, name):
        return (self.store / f'verdicts/{name}-packet.md').read_text()

    FENCED_PART = (
        '# Brief\n\n## Playbook: bug-fix (your parts)\n\n````md\n'
        '## Verify\n\nFAKE verify\n\n## Handoff\n\nFAKE text\n````\n\n'
        '## Verify\n\nreal verify\n\n## Handoff\n\n'
    )

    def test_the_verify_block_finish_reruns_skips_a_fenced_heading(self):
        brief = self.store / 'briefs/001-x.md'
        brief.write_text(self.FENCED_PART)
        block = self.kitchen_variant().verify_block(
            types.SimpleNamespace(prog='kitchen.sh'), str(brief)
        )
        self.assertEqual(block, '\nreal verify\n\n')

    def test_the_packet_reads_the_store_paths_outside_the_verify_section_fence_aware(
        self,
    ):
        self.kitchen_variant().load_kitchen_core()
        from kitchen_core.verify_packet import brief_without_verify

        brief = self.store / 'briefs/001-x.md'
        brief.write_text(self.FENCED_PART)
        text = brief_without_verify(str(brief))
        self.assertIn('FAKE verify', text)
        self.assertNotIn('real verify', text)

    def test_the_handoff_check_reads_a_section_fence_aware(self):
        self.kitchen_variant().load_kitchen_core()
        from kitchen_core.master import section_has_text

        brief = self.store / 'briefs/001-x.md'
        brief.write_text(self.FENCED_PART)
        self.assertFalse(section_has_text(str(brief), '## Handoff'))
        brief.write_text(self.FENCED_PART + 'real text\n')
        self.assertTrue(section_has_text(str(brief), '## Handoff'))

    def test_stamping_lessons_leaves_a_playbook_part_holding_a_fenced_lessons_heading_intact(
        self,
    ):
        self.write(
            '.agents/LESSONS.md',
            '# Kitchen lessons\n\n'
            '## L001: Every write to the store clears the cache.\n\nstatus: active\nkind: reject\nclass: verify\n'
            'layer: verify\narea: src\nevidence:\n- 2026-10-01 run-a 001: stale cache\n\n',
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        part = '````md\n## Lessons\n\n- L099 FAKE\n\n## Other\n````\n'
        b.write_text(
            b.read_text().replace('## Scope', f'## Notes\n\n{part}\n## Scope', 1)
        )
        self.dispatch(b)
        self.dispatch(b)
        text = b.read_text()
        self.assertIn(part, text)
        self.assertEqual(text.count('\n## Lessons\n'), 2)
        real = text.rsplit('\n## Lessons\n', 1)[1]
        self.assertIn('- L001 (', real)
        self.assertNotIn('FAKE', real)

    def test_classify_warns_when_a_nested_fence_swallows_acceptance_and_verify(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        nested = (
            '## Notes\n\n```md\n## Acceptance\n\n- FAKE\n\n'
            '## Verify\n\n```bash\nFAKE\n```\n```\n\n'
        )
        text = b.read_text().replace('## Scope', nested + '## Scope', 1)
        b.write_text(text)
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertRegex(
            out,
            r'warn: Acceptance reads empty: its heading at line \d+ sits inside the code fence opened at line \d+',
        )
        self.assertIn('warn: Verify reads empty', out)
        lines = text.split('\n')
        last = max(i for i, x in enumerate(lines) if x == '## Acceptance')
        stray = max(i for i in range(lines.index('## Scope')) if lines[i] == '```')
        self.assertIn(f'its heading at line {last + 1} ', out)
        self.assertIn(f'fence opened at line {stray + 1};', out)
        b.write_text(
            text.replace('```bash', '````bash').replace('```\n```', '```\n````')
        )
        self.assertNotIn(
            'reads empty', self.run_sh('classify', str(self.store), str(b))
        )

    def test_classify_names_the_fence_holding_the_last_heading_not_an_earlier_block(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        notes = (
            '## Notes\n\n```bash\necho balanced\n```\n'
            '```md\n## Acceptance\n\n- FAKE\n\n'
            '## Verify\n\n```bash\nFAKE\n```\n```\n\n'
        )
        text = b.read_text().replace('## Scope', notes + '## Scope', 1)
        b.write_text(text)
        lines = text.split('\n')
        out = self.run_sh('classify', str(self.store), str(b))
        notes_at = lines.index('## Notes')
        stray = max(
            i for i in range(notes_at, lines.index('## Scope')) if lines[i] == '```'
        )
        self.assertIn(f'fence opened at line {stray + 1};', out)
        self.assertNotIn(f'fence opened at line {notes_at + 3};', out)

    def test_dispatch_refuses_a_playbook_part_that_leaves_a_fence_open_end_to_end(self):
        self.use_overlays()
        toml = self.repo / '.agents/playbooks/rust-engine.toml'
        toml.write_text(
            toml.read_text().replace(
                'Check error paths', '```bash\\nnever closed. Check error paths', 1
            )
        )
        self.commit('open fence')
        b = self.brief('001', 'engine', ['engine/**'], playbook='bug-fix')
        out = self.dispatch(b, code=2, timeout=60)
        self.assertIn(
            'playbook part error-model (verifier) leaves a code fence open', out
        )

    def test_put_section_twice_gives_the_bytes_of_once_in_every_placement(self):
        self.kitchen_variant().load_kitchen_core()
        from kitchen_core.playbook_delivery import (
            PLAN_BEFORE,
            PLAN_SECTION,
            put_section,
        )

        section = f'{PLAN_SECTION}\n\nbody\n'
        texts = {
            'before a heading': '# P\n\n## Steps\n\n1. a\n\n## Verification\n\nv\n',
            'at the end': '# P\n\n## Steps\n\n1. a\n',
            'existing at the end': f'# P\n\n## Steps\n\n1. a\n\n{PLAN_SECTION}\n\nold\n',
            'existing before a heading': f'# P\n\n{PLAN_SECTION}\n\nold\n\n## Risks\n\nr\n',
        }
        for name, text in texts.items():
            once = put_section(text, section, PLAN_SECTION, PLAN_BEFORE)
            twice = put_section(once, section, PLAN_SECTION, PLAN_BEFORE)
            self.assertEqual(twice, once, name)

    def test_dispatch_refuses_a_playbook_part_that_leaves_a_fence_open(self):
        self.kitchen_variant().load_kitchen_core()
        from kitchen_core.playbook_delivery import OpenFence, refuse_open_fences

        part = {'id': 'bug-fix.prove', 'text': 'Prove.\n```bash\nnever closed'}
        composition = {'roles': {'sidekick': [part], 'verifier': []}}
        with self.assertRaisesRegex(
            OpenFence, 'bug-fix.prove .*leaves a code fence open'
        ):
            refuse_open_fences(composition)
        part['text'] = 'Prove.\n```bash\nclosed\n```'
        refuse_open_fences(composition)

    def test_a_unit_packet_carries_its_verifier_parts_whole_after_its_acceptance(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'], acceptance=['- it adds'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        long = 'Prove the fix.\n\n' + '\n'.join(f'- line {i}' for i in range(300))
        self.stamp(
            '001',
            'bug-fix',
            ('bug-fix.prove', long),
            ('bug-fix.repro', '## Heading\nR'),
        )
        self.run_sh('verify', str(self.store), '001')
        packet = self.packet_text('001-util-v1')
        section = packet.split('## Playbook (what to prove)')[1].split('\n## ')[0]
        self.assertLess(
            packet.index('- it adds'), packet.index('## Playbook (what to prove)')
        )
        self.assertIn('adds no criteria', section)
        self.assertIn('unit 001: bug-fix', section)
        self.assertIn(long, section)
        self.assertIn(
            '### bug-fix.repro\n\n### Heading\nR'.replace('### H', '#### H'), section
        )
        self.assertIn(
            'from workflow:bug-fix, skills:poteto-mode/playbooks/bug-fix.md step:1 '
            + 'd' * 12,
            section,
        )
        self.assertEqual(packet.count('## Playbook (what to prove)'), 1)

    def test_a_batch_packet_gives_each_unit_its_own_workflow_parts(self):
        self.three_units()
        self.stamp('001', 'bug-fix', ('bug-fix.prove', 'Prove the fix.'))
        self.stamp('002', 'feature', ('feature.prove', 'Prove the feature.'))
        self.run_sh('verify', str(self.store), '001', '002')
        packet = self.packet_text('002-u2-v1')
        one, two = packet.split('## Unit 002')
        self.assertIn('unit 001: bug-fix\n\n### bug-fix.prove', one)
        self.assertNotIn('feature.prove', one)
        self.assertIn('unit 002: feature\n\n### feature.prove', two)

    def test_a_covers_packet_shows_a_shared_part_once(self):
        self.three_units()
        shared = ('feature.prove', 'Prove the feature.')
        self.stamp('001', 'feature', shared)
        self.stamp('002', 'feature', shared, ('feature.extra', 'Extra.'))
        self.stamp('003', 'bug-fix', ('bug-fix.prove', 'Fix.'))
        self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        packet = self.packet_text('003-u3-v1')
        self.assertEqual(packet.count('Prove the feature.'), 1)
        self.assertIn('### feature.prove (also unit 001)', packet)
        self.assertIn('### feature.extra\n\nExtra.', packet)

    def test_a_docs_brief_dispatches_with_no_plan_and_carries_the_docs_parts(self):
        b = self.brief('001', 'readme', ['docs/**'], playbook='docs')
        self.dispatch(b)
        composition = (self.store / 'compositions/001.json').read_text()
        self.assertIn('docs.step-3', composition)
        self.assertIn('Check every path, link, command and flag', b.read_text())

    def test_an_old_brief_with_a_label_and_no_composition_still_verifies(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        b.write_text(b.read_text().replace('playbook: feature', 'playbook: fix', 1))
        (self.store / 'compositions/001.json').unlink(missing_ok=True)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.assertFalse((self.store / 'compositions/001.json').exists())
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn('status: clean', out)
        self.assertIn(
            '## Playbook (what to prove)\n\nplaybook: fix (not mapped)\n\n',
            self.packet_text('001-util-v1'),
        )

    def test_a_fix_is_verified_with_the_unit_it_fixes(self):
        first = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(first)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.commit('first')
        (self.store / 'reports' / first.name).write_text(
            '# Report\n\nstatus: partial\nhead: x\n'
        )
        fix = self.brief('002', 'util-fix', ['src/**', 'tests/**'], playbook='bug-fix')
        self.dispatch(fix)
        self.write('src/util.py', 'X = 6\n')
        self.write('tests/test_app.py', 'x = 6\n')
        self.done(fix, self.commit('fix'))
        out = self.run_sh('verify', str(self.store), '002', '--covers', '001')
        self.assertIn('status: clean', out)
        packet = (self.store / 'verdicts/002-util-fix-v1-packet.md').read_text()
        self.assertIn('units: 001 002', packet)
        self.assertIn('Unit 002 fixes unit 001', packet)
        self.assertIn('--role verifier', packet)
        for name in ('001-util', '002-util-fix'):
            (self.store / f'reviews/{name}.md').write_text('verdict: accept\n')
        self.assertIn('land-check: pass', self.run_sh('land-check', str(self.store)))

    def test_a_unit_carries_its_lessons_to_the_verifier_and_retro_scores_them(self):
        self.write(
            '.agents/LESSONS.md',
            '# Kitchen lessons\n\n'
            '## L001: Every write to the store clears the cache.\n\nstatus: active\nkind: reject\nclass: verify\n'
            'layer: verify\narea: src\nevidence:\n- 2026-10-01 run-a 001: stale cache\n\n'
            '## L002: Callers of a renamed function are updated.\n\nstatus: active\nkind: catch\nclass: review\n'
            'layer: review\narea: src\nevidence:\n- 2026-10-01 run-a 002: missed a caller\n\n'
            '## L003: Docs only.\n\nstatus: active\nkind: steer\nclass: brief\nlayer: brief\narea: docs\n'
            'evidence:\n- 2026-10-01 run-a 003: x\n',
        )
        self.commit('lessons')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        text = b.read_text()
        self.assertIn(
            '- L001 (reject verify, layer verify, in src): Every write to the store clears the cache.\n',
            text,
        )
        self.assertIn('- L002 (catch review, layer review, in src)', text)
        self.assertNotIn('L003', text)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.assertIn('status: clean', self.run_sh('verify', str(self.store), '001'))
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        lessons = packet[packet.index('## Lessons') : packet.index('## Changed')]
        self.assertRegex(lessons, r'Acceptance lines too[^-]*- L001: Every write')
        self.assertRegex(lessons, r'Only after your own pass[^-]*- L002: Callers')
        self.run_sh(
            'catch',
            str(self.store),
            'review',
            'a caller still used the old name',
            '001',
        )
        self.run_sh('retro', str(self.store))
        head, rows = self.ledger()
        self.assertEqual(
            sorted(
                (r['class'], r['detail'], r['unit'])
                for r in rows
                if r['kind'] == 'lesson'
            ),
            [('held', 'L001', '001'), ('hit', 'L002', '001')],
        )
        x = {
            l['id']: l
            for l in json.loads(
                subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        '--repo',
                        str(self.repo),
                        '--json',
                        'lessons',
                    ],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60,
                ).stdout
            )['lessons']
        }
        self.assertEqual((x['L001']['held'], x['L002']['hits']), (1, 1))

    def three_units(self, scope=('src/**', 'tests/**')):
        heads = []
        for n in (1, 2, 3):
            b = self.brief(f'00{n}', f'u{n}', list(scope))
            self.dispatch(b)
            if scope[0].startswith('docs'):
                self.write('docs/guide.md', f'# Guide {n}\n')
            else:
                self.write('src/util.py', f'X = {n + 20}\n')
                self.write('tests/test_app.py', f'x = {n + 20}\n')
            heads.append(self.commit(f'u{n}'))
            self.done(b, heads[-1])
        return heads

    def test_covers_takes_a_comma_list_and_a_correct_order_passes(self):
        self.three_units()
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        self.assertIn('status: clean', out)
        packet = (self.store / 'verdicts/003-u3-v1-packet.md').read_text()
        self.assertIn('units: 001 002 003', packet)

    def test_a_fix_named_before_the_units_it_covers_is_refused_with_both_heads(self):
        heads = self.three_units()
        out = self.run_sh(
            'verify', str(self.store), '003', '--covers', '001', '002', code=2
        )
        self.assertIn(
            f"unit 003's head {heads[2][:9]} is not in the checked-out head {heads[1][:9]} (unit 002)",
            out,
        )
        self.assertIn('name the unit that carries all the others last', out)
        self.assertNotIn('verifier demo-verifier', out)
        self.assertNotIn('verifying', self.state())

    def test_the_head_check_runs_before_a_gates_only_verification(self):
        heads = self.three_units(scope=('docs/**',))
        out = self.run_sh('verify', str(self.store), '002', '--covers', '003', code=2)
        self.assertIn(
            f"unit 003's head {heads[2][:9]} is not in the checked-out head {heads[1][:9]} (unit 002)",
            out,
        )
        self.assertNotIn('gates only', out)

    def test_a_covered_unit_without_a_usable_head_passes_with_a_note(self):
        self.three_units()
        (self.store / 'reports/001-u1.md').write_text(
            '# Report\n\nstatus: partial\nhead: x\n'
        )
        (self.store / 'reports/002-u2.md').unlink()
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        self.assertIn(
            'note: unit 001 has no usable head; its inclusion was not checked', out
        )
        self.assertIn(
            'note: unit 002 has no usable head; its inclusion was not checked', out
        )

    def packet_range(self, name):
        found = re.search(
            r'^range: (\S+)\.\.(\S+)$',
            (self.store / f'verdicts/{name}-packet.md').read_text(),
            re.M,
        )
        assert found is not None
        return found.groups()

    def test_covers_measures_from_the_oldest_dispatch_head_whatever_the_order_named(
        self,
    ):
        heads = self.three_units()
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.run_sh('verify', str(self.store), '003', '--covers', '002,001')
        self.assertEqual(self.packet_range('003-u3-v1'), (init, heads[2]))
        self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        self.assertEqual(self.packet_range('003-u3-v2'), (init, heads[2]))
        self.run_sh('verify', str(self.store), '003', '--covers', '002')
        self.assertEqual(self.packet_range('003-u3-v3'), (heads[0], heads[2]))

    def test_covers_base_flag_beats_the_oldest_dispatch_head(self):
        heads = self.three_units()
        self.run_sh(
            'verify', str(self.store), '003', '--covers', '002,001', '--base', heads[1]
        )
        self.assertEqual(self.packet_range('003-u3-v1'), (heads[1], heads[2]))

    def test_covers_refuses_dispatch_heads_with_no_common_order(self):
        self.three_units()
        tree = self.git('rev-parse', 'HEAD^{tree}')
        stray = self.git('commit-tree', tree, '-m', 'unrelated')
        path = self.store / 'pair.json'
        state = json.loads(path.read_text())
        state['heads']['002-u2'] = stray
        path.write_text(json.dumps(state))
        out = self.run_sh(
            'verify', str(self.store), '003', '--covers', '001,002', code=1
        )
        self.assertEqual(
            out, 'kitchen.sh: units 001 and 002 have no common order: pass --base REV\n'
        )
        self.assertNotIn('verifying', self.state())

    def test_covers_names_a_missing_brief_once(self):
        self.three_units()
        out = self.run_sh(
            'verify', str(self.store), '003', '--covers', '001,099', code=1
        )
        self.assertEqual(out, 'kitchen.sh: no brief for unit 099\n')

    def covers_packet(self):
        packet = (self.store / 'verdicts/003-u3-v1-packet.md').read_text()
        return packet, packet.split('## Context: units between')[1].split('\n## ')[
            0
        ] if '## Context: units between' in packet else ''

    def test_a_covers_packet_lists_the_unit_between_with_its_accepted_exception_as_context(
        self,
    ):
        self.three_units()
        (self.store / 'reviews/002-u2.md').write_text(
            '# Review 002\n\nverdict: accept\n\n## Amendments\n\n'
            'KNOWN_DEAD grew by two names: accepted.\n\n## Checked\n\nread it\n'
        )
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001')
        self.assertNotIn('excluded:', out)
        packet, context = self.covers_packet()
        self.assertEqual(
            context.strip('\n').split('\n'),
            [
                "These are decisions a review accepted; do not judge them again. Judge only the named units' Acceptance.",
                '',
                'unit 002-u2: accepted (reviews/002-u2.md)',
                '',
                'Amendments:',
                '',
                'KNOWN_DEAD grew by two names: accepted.',
            ],
        )
        before_context = packet.split('## Context: units between')[0]
        self.assertNotIn('unit 002-u2', before_context)
        self.assertEqual(
            [l for l in before_context.split('\n') if l.startswith('## Unit ')],
            ['## Unit 001', '## Unit 003'],
        )

    def test_a_covers_packet_gives_a_named_unit_its_accepted_amendments(self):
        rule = 'Accepted by the master (reviews/001-u1.md), whatever the Acceptance above says'
        cases = [
            ('an amendment', '## Amendments\n\nthe CLI keeps --old: accepted.\n', True),
            ('none', '## Amendments\n\nnone\n', False),
            ('no review note', None, False),
        ]
        self.three_units()
        for version, (label, review, shown) in enumerate(cases, 1):
            with self.subTest(label):
                note = self.store / 'reviews/001-u1.md'
                note.unlink(missing_ok=True)
                if review is not None:
                    note.write_text(
                        f'# Review 001\n\nverdict: accept\n\n{review}\n## Checked\n\nread it\n'
                    )
                self.run_sh('verify', str(self.store), '003', '--covers', '001')
                packet = (
                    self.store / f'verdicts/003-u3-v{version}-packet.md'
                ).read_text()
                unit = packet.split('## Unit 001')[1].split('\n## Unit ')[0]
                self.assertEqual(rule in unit, shown)
                self.assertEqual('the CLI keeps --old: accepted.' in unit, shown)
                self.assertNotIn(
                    'Accepted by the master', packet.split('## Unit 003')[1]
                )

    def test_a_covers_packet_says_a_unit_between_has_no_accepted_review(self):
        self.three_units()
        (self.store / 'reviews/002-u2.md').write_text(
            'verdict: revise\n\n## Amendments\n\nnot taken\n'
        )
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001')
        lines = self.covers_packet()[1].split('\n')
        remedy = 'name it with --covers, or verify --landing covers it'
        self.assertIn(f'unit 002-u2: no accepted review; {remedy}', lines)
        self.assertNotIn('not taken', lines)
        self.assertEqual(
            [line for line in out.splitlines() if line.startswith('excluded:')],
            [f'excluded: unit 002 (in the range, no accepted review); {remedy}'],
        )

    def test_a_packet_without_covers_has_no_units_between_context(self):
        self.three_units()
        self.run_sh('verify', str(self.store), '003')
        self.assertNotIn('Context: units between', self.covers_packet()[0])

    def test_covers_rejects_a_value_that_is_not_a_unit_number(self):
        self.three_units()
        self.assertIn(
            '--covers takes unit numbers (NNN)',
            self.run_sh('verify', str(self.store), '003', '--covers', '001,x', code=1),
        )

    def test_verify_returns_at_the_interval_and_resumes(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 8\n')
        self.write('tests/test_app.py', 'x = 8\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertIn('verifying: units 001 by pi', out)
        self.assertIn(
            f'next: other work, then kitchen.sh verify {self.store} --wait', out
        )
        self.assertIn(
            'a verification is open',
            self.run_sh('verify', str(self.store), '001', code=5),
        )
        open_ = json.loads((self.store / 'pair.json').read_text())['verifying']
        Path(open_['verdict']).write_text(
            'status: clean\nunits: 001\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('verify', str(self.store), '--wait')
        self.assertIn('status: clean', out)
        wakes = [
            r.split('\t')[5]
            for r in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\twake\t' in r
        ]
        self.assertEqual(
            [w for w in wakes if w.startswith('verify:')],
            ['verify:progress', 'verify:clean'],
        )
        self.assertNotIn(
            'verifying', json.loads((self.store / 'pair.json').read_text())
        )
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])
        self.assertIn(
            'no verification is open',
            self.run_sh('verify', str(self.store), '--wait', code=5),
        )

    def test_until_verdict_returns_once_on_the_verdict_with_no_interval_return(self):
        # The clock jumps 20 minutes at the first status poll: past the default 9-minute interval, inside the 60-minute deadline.
        shim = Path(self.tmp.name) / 'clock'
        shim.mkdir()
        (shim / 'date').write_text(
            f'#!/bin/bash\nif [ "$1" = +%s ] && [ -e "$FAKE/time-jump" ]; then echo $(( $({shutil.which("date")} +%s) + 1200 )); '
            f'else exec {shutil.which("date")} "$@"; fi\n'
        )
        (shim / 'date').chmod(0o755)
        self.env.update(
            {
                'KITCHEN_VERIFY_POLL_S': '0',
                'KITCHEN_VERDICT_SETTLE_S': '0',
                'KITCHEN_VERIFY_IDLE_S': '100000',
                'PATH': f'{shim}:{self.env["PATH"]}',
            }
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 11\n')
        self.write('tests/test_app.py', 'x = 11\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh(
            'verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4
        )
        verdict = Path(self.state()['verifying']['verdict'])
        # The first status poll starts the jump; the verdict lands at the third.
        hook = self.fake / 'once/get'
        hook.parent.mkdir(exist_ok=True)
        polls = self.fake / 'polls'
        hook.write_text(
            '#!/bin/bash\n'
            f'echo >>{polls}\n'
            f'touch {self.fake}/time-jump\n'
            f'if [ "$(wc -l <{polls})" -ge 3 ]; then printf \'# V\\n\\nstatus: clean\\nunits: 001\\n\\n## Evidence\\n\\n```\\n$ true\\n```\\n\' >{verdict}\n'
            f'else cp "$0" {hook}; fi\n'
        )
        hook.chmod(0o755)
        out = self.run_sh('verify', str(self.store), '--wait', '--until-verdict')
        self.assertIn('status: clean', out)
        self.assertNotIn('verifying: units', out)
        wakes = [
            r.split('\t')[5]
            for r in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\twake\t' in r
        ]
        self.assertEqual(
            [w for w in wakes if w.startswith('verify:')],
            ['verify:progress', 'verify:clean'],
        )

    def test_every_and_until_verdict_are_alternatives(self):
        self.assertIn(
            '--every and --until-verdict are alternatives',
            self.run_sh(
                'verify',
                str(self.store),
                '--wait',
                '--every',
                '1',
                '--until-verdict',
                code=1,
            ),
        )

    def open_slow_verification(self, n='001', slug='util'):
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0'})
        b = self.brief(n, slug, ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', f'X = 1{n}\n')
        self.write('tests/test_app.py', f'x = 1{n}\n')
        self.done(b, self.commit(slug))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh(
            'verify', str(self.store), n, '--every', '0', '--timeout', '60', code=4
        )
        return self.state()['verifying']

    def written_verdict(self, open_, status, units='001', findings=''):
        Path(open_['verdict']).write_text(
            f'status: {status}\nunits: {units}\n\n## Findings\n\n{findings}\n\n## Evidence\n\n```\n$ true\n```\n'
        )

    def test_an_unread_reject_closed_by_the_next_verify_prints_its_findings_and_starts_nothing(
        self,
    ):
        open_ = self.open_slow_verification()
        self.written_verdict(
            open_, 'reject', findings='1. Acceptance broke: add returns 0'
        )
        starts = (
            (self.fake / 'calls.log').read_text().count('agent start demo-verifier')
        )
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=2)
        self.assertIn(f'closed: {open_["verdict"]} (reject)', out)
        self.assertIn('Acceptance broke: add returns 0', out)
        self.assertIn('next: kitchen.sh revise', out)
        self.assertNotIn('verifying', self.state())
        self.assertEqual(
            (self.fake / 'calls.log').read_text().count('agent start demo-verifier'),
            starts,
        )

    def test_an_unread_sampled_clean_closed_by_the_next_verify_prints_the_audit_line(
        self,
    ):
        open_ = self.open_slow_verification(slug='b')
        self.written_verdict(open_, 'clean')
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertIn(f'closed: {open_["verdict"]} (clean)', out)
        self.assertIn('audit: yes', out)
        self.assertIn('verifying: units 001 by pi', out)

    def test_the_handoff_nudge_prints_when_the_second_clean_verdict_is_closed_by_the_next_verify(
        self,
    ):
        self.env['KITCHEN_MASTER_ROTATE_UNITS'] = '2'
        self.assertNotIn('since your last handoff', self.run_sh_unit_clean('001'))
        open_ = self.open_slow_verification('002', 'next')
        self.written_verdict(open_, 'clean', units='002')
        out = self.run_sh('verify', str(self.store), '002', '--every', '0', code=4)
        self.assertIn('master: 2 units verified since your last handoff', out)

    def run_sh_unit_clean(self, n):
        b = self.brief(n, 'first', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/first.py', 'F = 1\n')
        self.write('tests/test_first.py', 'f = 1\n')
        self.done(b, self.commit('first'))
        return self.run_sh('verify', str(self.store), '--timeout', '60', n)

    def on_nth_get(self, n, body):
        """Runs `body` (shell) inside the n-th `herdr agent get` that follows, in whatever command makes it."""
        hook = self.fake / 'once/get'
        hook.parent.mkdir(exist_ok=True)
        counter = self.fake / f'gets-{n}'
        hook.write_text(
            f'#!/bin/bash\necho >>{counter}\nif [ "$(wc -l <{counter})" -ge {n} ]; then\n{body}\nelse cp "$0" {hook}; fi\n'
        )
        hook.chmod(0o755)

    def cancel_and_replace(self):
        return (
            f'{self.script} verify {self.store} --cancel >/dev/null 2>&1\n'
            f'{self.script} verify {self.store} 001 --every 0 --timeout 60 >/dev/null 2>&1'
        )

    def outcome_of(self, packet):
        found = re.search(r'^verifier-outcome: (\S+)$', Path(packet).read_text(), re.M)
        assert found is not None
        return found.group(1)

    def test_verify_while_a_background_waiter_holds_the_close_claim_waits_for_it_and_starts_its_verification(
        self,
    ):
        """The waiter stops inside its close, holding the claim (the test releases it through a pipe, so no timing is involved); the next
        verify finds the verdict being closed, says it waits, and carries on once the waiter is done."""
        self.env.update(
            {
                'KITCHEN_VERIFIER_EXIT_S': '1',
                'KITCHEN_VERIFY_POLL_S': '0',
                'KITCHEN_VERDICT_SETTLE_S': '0',
            }
        )
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        held, go = self.fake / 'held.fifo', self.fake / 'go.fifo'
        os.mkfifo(held)
        os.mkfifo(go)
        self.on_nth_get(1, f'echo held >{held}\ncat {go} >/dev/null')
        waiter = subprocess.Popen(  # bounded: killed by addCleanup
            [str(self.script), 'verify', str(self.store), '--wait', '--every', '1'],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.addCleanup(waiter.kill)
        self.assertEqual(held.read_text().strip(), 'held')
        nxt = subprocess.Popen(  # bounded: killed by addCleanup
            [
                str(self.script),
                'verify',
                str(self.store),
                '001',
                '--every',
                '0',
                '--timeout',
                '60',
            ],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.addCleanup(nxt.kill)
        assert nxt.stdout is not None
        self.assertIn(
            'another call is closing it; waiting for it', nxt.stdout.readline()
        )
        go.write_text('go\n')
        waiter_out, _ = waiter.communicate()
        next_out, _ = nxt.communicate()
        self.assertEqual(waiter.returncode, 0, waiter_out)
        self.assertIn('status: clean', waiter_out)
        self.assertEqual(nxt.returncode, 4, next_out)
        self.assertIn(f'closed: {first["verdict"]} (closed by another call)', next_out)
        self.assertIn('verifying: units 001', next_out)
        self.assertNotEqual(self.state()['verifying']['verdict'], first['verdict'])
        rows = [
            r
            for r in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tverify\t' in r
        ]
        self.assertEqual(len(rows), 1, rows)

    def hold_close_claim(self):
        """A live process (this test) holds the close claim of the open verification, so a call that reaches the close loses it."""
        self.env.update({'HOSTNAME': 'claimhost', 'KITCHEN_VERIFY_POLL_S': '0'})
        self.set_close_claim(f'claimhost:{os.getpid()}:held')

    def finish_close(self, open_, outcome, then=None):
        """The close the claim holder was making ends: the attempt's outcome is in its packet and `.verifying` is gone, or is `then`."""
        packet = Path(open_['packet'])
        packet.write_text(
            re.sub(
                r'(?m)^verifier-outcome:.*',
                f'verifier-outcome: {outcome}',
                packet.read_text(),
            )
        )
        state = self.state()
        if then is None:
            del state['verifying']
        else:
            state['verifying'] = then
        swap = self.store / 'pair.json.swap'
        swap.write_text(json.dumps(state))
        os.replace(swap, self.store / 'pair.json')

    def lose_a_close(self, args, finish):
        """Runs `kitchen.sh <args>` as a call that finds the close claim held, waits for its line saying so, lets `finish` end the holder's close by
        editing the store, and returns (status, output, the herdr calls the loser made after it began to wait). The loser waits on a store
        change, so no timing is involved."""
        self.hold_close_claim()
        call = subprocess.Popen(  # bounded: killed by addCleanup
            [str(self.script), *args],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.addCleanup(call.kill)
        assert call.stdout is not None
        waiting = call.stdout.readline()
        self.assertIn('another call is closing it; waiting for it', waiting)
        before = (self.fake / 'calls.log').read_text()
        finish()
        rest, _ = call.communicate(timeout=60)
        calls = (self.fake / 'calls.log').read_text()
        return call.returncode, waiting + rest, calls[len(before) :]

    def master_verify_rows(self):
        return [
            r
            for r in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tverify\t' in r
        ]

    def lose_a_wait(self, finish):
        return self.lose_a_close(
            ['verify', str(self.store), '--wait', '--every', '1'], finish
        )

    def test_a_verify_wait_that_loses_the_close_reports_the_clean_verdict_and_exits_0(
        self,
    ):
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        rc, out, calls = self.lose_a_wait(lambda: self.finish_close(first, 'clean'))
        self.assertEqual(rc, 0, out)
        self.assertIn(f'verdict: {first["verdict"]}\n', out)
        self.assertIn('status: clean (closed by another verify --wait)', out)
        self.assertEqual(calls, '')
        self.assertEqual(self.master_verify_rows(), [])
        self.assertEqual(self.outcome_of(first['packet']), 'clean')

    def test_a_verify_wait_that_loses_the_close_reports_the_rejection_and_exits_2(
        self,
    ):
        first = self.open_slow_verification()
        self.written_verdict(first, 'reject', findings='1. add returns 0')
        rc, out, calls = self.lose_a_wait(lambda: self.finish_close(first, 'reject'))
        self.assertEqual(rc, 2, out)
        self.assertIn('status: reject (closed by another verify --wait)', out)
        self.assertIn('1. add returns 0', out)
        self.assertEqual(calls, '')
        self.assertEqual(self.master_verify_rows(), [])

    def test_a_verify_wait_that_loses_the_close_reports_the_attempt_it_lost_to_not_a_fallback(
        self,
    ):
        """The holder's close ends and a fallback attempt is already in `.verifying` when the loser resumes: the loser reports the old verdict and
        leaves the new attempt alone. The holder closed the attempt as `provider`, with no verdict, so the verdict file the loser finds was written
        after that close and is void."""
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        fallback = {
            **first,
            'verdict': first['verdict'].replace('-v1.md', '-v2.md'),
            'packet': first['packet'].replace('-v1-', '-v2-'),
        }
        fallback.pop('closing', None)
        rc, out, calls = self.lose_a_wait(
            lambda: self.finish_close(first, 'provider', then=fallback)
        )
        self.assertEqual(rc, 2, out)
        self.assertIn(f'verdict: {first["verdict"]}\n', out)
        self.assertIn(
            'status: invalid (void: its verification closed before this verdict was written)',
            out,
        )
        self.assertNotIn('-v2', out)
        self.assertEqual(self.state()['verifying'], fallback)
        self.assertEqual(calls, '')

    def test_a_verify_wait_that_loses_the_close_to_a_malformed_clean_verdict_is_not_a_success(
        self,
    ):
        first = self.open_slow_verification()
        Path(first['verdict']).write_text('status: clean\nunits: 001\n')
        rc, out, _ = self.lose_a_wait(lambda: self.finish_close(first, 'invalid'))
        self.assertEqual(rc, 2, out)
        self.assertIn(
            'status: invalid (no command output under Evidence) (closed by another verify --wait)',
            out,
        )

    def test_a_verify_wait_that_loses_the_close_of_an_attempt_with_no_verdict_after_a_provider_stop_exits_2_and_starts_no_fallback(
        self,
    ):
        self.rate_limited_verifier()
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0'})
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('ratelimit-live')
        self.run_sh(
            'verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4
        )
        first = self.state()['verifying']
        starts = len(self.verifier_starts())
        rc, out, calls = self.lose_a_wait(lambda: self.finish_close(first, 'provider'))
        self.assertEqual(rc, 2, out)
        self.assertIn(
            'status: inconclusive (the verifier hit its provider, not the work) (closed by another verify --wait)',
            out,
        )
        self.assertNotIn('retry:', out)
        self.assertEqual(len(self.verifier_starts()), starts)
        self.assertEqual(calls, '')

    def test_a_verify_wait_that_loses_a_close_that_never_completes_refuses_after_the_wait(
        self,
    ):
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        self.hold_close_claim()
        self.env['KITCHEN_VERIFY_CLOSE_WAIT_S'] = '0'
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn(
            f'another call has held the close of {first["verdict"]} for 0s', out
        )
        self.assertNotIn('status: clean', out)
        self.assertEqual(self.state()['verifying']['verdict'], first['verdict'])

    def swap_closing(self, closing):
        """Replaces the open verification's close claim (removes it for None) in one rename, so a waiting call never reads a half-written store."""
        state = self.state()
        if closing is None:
            del state['verifying']['closing']
        else:
            state['verifying']['closing'] = closing
        swap = self.store / 'pair.json.swap'
        swap.write_text(json.dumps(state))
        os.replace(swap, self.store / 'pair.json')

    def lose_a_close_to_a_call_that_ends(self, end):
        """A losing `verify --wait` with a very long close wait, whose winner's claim is changed by `end` once the loser is waiting. Returns the
        verdict it lost and its output, after checking that it exited 5 without the long wait's message and left the verification open."""
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        self.env['KITCHEN_VERIFY_CLOSE_WAIT_S'] = '100000'
        rc, out, _ = self.lose_a_wait(end)
        self.assertEqual(rc, 5, out)
        self.assertNotIn('held the close', out)
        self.assertEqual(self.state()['verifying']['verdict'], first['verdict'])
        return first['verdict'], out

    def test_a_verify_wait_that_loses_a_close_to_a_dead_process_stops_waiting(self):
        def die():
            gone = subprocess.Popen(['true'])  # bounded: true exits at once
            gone.wait()
            self.swap_closing({'token': f'claimhost:{gone.pid}:dead'})

        verdict, out = self.lose_a_close_to_a_call_that_ends(die)
        self.assertIn(
            f'the call closing {verdict} ended without closing it; kitchen.sh verify {self.store} --wait',
            out,
        )

    def test_a_verify_wait_that_loses_a_close_whose_claim_is_released_stops_waiting(
        self,
    ):
        verdict, out = self.lose_a_close_to_a_call_that_ends(
            lambda: self.swap_closing(None)
        )
        self.assertIn(
            f'the call closing {verdict} ended without closing it; kitchen.sh verify {self.store} --wait',
            out,
        )

    def test_verify_that_finds_its_unread_verdict_closed_by_another_call_waits_once_and_prints_its_own_line(
        self,
    ):
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        rc, out, calls = self.lose_a_close(
            ['verify', str(self.store), '001', '--every', '0', '--timeout', '60'],
            lambda: self.finish_close(first, 'clean'),
        )
        self.assertEqual(rc, 4, out)
        self.assertEqual(out.count('waiting for it'), 1, out)
        self.assertIn(f'closed: {first["verdict"]} (closed by another call)', out)
        self.assertNotIn('failed', out)
        self.assertNotEqual(self.state()['verifying']['verdict'], first['verdict'])

    def test_verify_that_finds_its_unread_verdict_cancelled_by_another_call_says_so_and_starts_nothing(
        self,
    ):
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        starts = len(self.verifier_starts())
        rc, out, calls = self.lose_a_close(
            ['verify', str(self.store), '001', '--every', '0', '--timeout', '60'],
            lambda: self.finish_close(first, 'cancelled'),
        )
        self.assertEqual(rc, 5, out)
        self.assertIn(f'closed: {first["verdict"]} (cancelled by another call)', out)
        self.assertNotIn('failed', out)
        self.assertEqual(len(self.verifier_starts()), starts)

    def test_a_cancel_racing_an_idle_update_leaves_no_verifying(self):
        """Fails with the idle update written without the ownership filter: it brings `.verifying` back as {idle_since: N}."""
        self.env.update(
            {'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERIFY_IDLE_S': '100000'}
        )
        self.open_slow_verification()
        # The second status poll is the idle branch's provider check, between the ownership check and the idle update.
        self.on_nth_get(
            2, f'{self.script} verify {self.store} --cancel >/dev/null 2>&1'
        )
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn('verification closed elsewhere', out)
        self.assertNotIn('verifying', self.state())

    def set_close_claim(self, token):
        state = self.state()
        state['verifying']['closing'] = {'token': token, 'at': int(time.time())}
        (self.store / 'pair.json').write_text(json.dumps(state))

    def test_a_cancel_while_a_live_process_holds_the_close_claim_prints_no_cancelled_and_changes_nothing(
        self,
    ):
        self.env['HOSTNAME'] = 'claimhost'
        first = self.open_slow_verification()
        pane = self.state()['verifier']['pane_id']
        self.set_close_claim(f'claimhost:{os.getpid()}:held')
        out = self.run_sh('verify', str(self.store), '--cancel', code=5)
        self.assertNotIn('cancelled:', out)
        self.assertIn('another call closed it', out)
        self.assertEqual(self.outcome_of(first['packet']), 'running')
        self.assertEqual(self.state()['verifying']['verdict'], first['verdict'])
        self.assertNotIn(f'pane close {pane}', (self.fake / 'calls.log').read_text())

    def test_a_die_with_status_1_inside_the_close_releases_the_claim(self):
        """A scratch id that cannot be removed ends the close with status 1 after the claim is taken; that is a die, not "closed elsewhere", so
        the claim goes and the next call is not left waiting on it."""
        self.env['KITCHEN_VERIFIER_EXIT_S'] = '1'
        self.open_slow_verification()
        state = self.state()
        state['verifying']['scratch'] = 'not a scratch id'
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.run_sh('verify', str(self.store), '--cancel', code=1)
        self.assertNotIn('another call closed it', out)
        self.assertNotIn('closing', self.state()['verifying'])

    def test_a_close_claim_from_another_host_is_never_taken_over(self):
        self.open_slow_verification()
        self.set_close_claim('elsewhere.example:1:held')
        self.assertIn(
            'another call closed it',
            self.run_sh('verify', str(self.store), '--cancel', code=5),
        )

    def test_a_close_claim_whose_process_is_gone_is_taken_over_at_once(self):
        """The claim is fresh (written now): only the dead process, not its age, lets it be taken over. Fails on unit 017's claim_close,
        which took over only a claim older than two minutes."""
        self.env.update({'KITCHEN_VERIFIER_EXIT_S': '1', 'HOSTNAME': 'claimhost'})
        first = self.open_slow_verification()
        gone = subprocess.Popen(['true'])  # bounded: true exits at once
        gone.wait()
        self.set_close_claim(f'claimhost:{gone.pid}:dead')
        self.assertIn(
            'cancelled: 001', self.run_sh('verify', str(self.store), '--cancel')
        )
        self.assertEqual(self.outcome_of(first['packet']), 'cancelled')

    def test_two_close_outs_of_one_written_verdict_log_it_and_count_the_handoff_once(
        self,
    ):
        """The second closer enters while the first is waiting for the verifier to leave, holding the claim: it waits for that close, prints the
        verdict with `(closed by another verify --wait)`, exits 0, and logs and counts nothing. Without the claim both close it: two `verify`
        rows and a handoff count of 2. The test releases the first through a pipe, so no timing is involved."""
        self.env.update({'KITCHEN_VERIFIER_EXIT_S': '1', 'KITCHEN_VERIFY_POLL_S': '0'})
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        held, go = self.fake / 'held.fifo', self.fake / 'go.fifo'
        os.mkfifo(held)
        os.mkfifo(go)
        self.on_nth_get(1, f'echo held >{held}\ncat {go} >/dev/null')
        wait = [str(self.script), 'verify', str(self.store), '--wait', '--every', '1']

        def start():
            return subprocess.Popen(  # bounded: killed by addCleanup
                wait,
                cwd=self.repo,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

        winner = start()
        self.addCleanup(winner.kill)
        self.assertEqual(held.read_text().strip(), 'held')
        loser = start()
        self.addCleanup(loser.kill)
        assert loser.stdout is not None
        self.assertIn(
            'another call is closing it; waiting for it', loser.stdout.readline()
        )
        go.write_text('go\n')
        winner_out, _ = winner.communicate()
        loser_rest, _ = loser.communicate()
        self.assertEqual((winner.returncode, loser.returncode), (0, 0), loser_rest)
        self.assertIn('status: clean\n', winner_out)
        self.assertIn('status: clean (closed by another verify --wait)', loser_rest)
        self.assertEqual(len(self.master_verify_rows()), 1)
        self.assertEqual(self.state()['master']['units_since_handoff'], 1)

    def test_a_waiter_in_the_grace_whose_verification_was_cancelled_and_replaced_exits_5_and_touches_nothing(
        self,
    ):
        """Fails with `verification_still_open` made a no-op: the waiter then prints the grace progress of the replaced verification and exits 4."""
        self.env['KITCHEN_VERIFY_POLL_S'] = '0'
        self.deadline_verify('slow', code=4, every='0')
        first = self.state()['verifying']['verdict']
        # The third status poll of the resumed wait is the grace loop's own.
        self.on_nth_get(3, self.cancel_and_replace())
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=5)
        self.assertIn('verification closed elsewhere', out)
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], first)
        self.assertEqual(self.outcome_of(second['packet']), 'running')
        self.assertEqual(len(self.verifier_starts()), 2)
        self.assertNotIn('verify:missing', (self.store / 'events.tsv').read_text())

    def test_a_waiter_on_a_provider_error_whose_verification_was_replaced_after_the_loop_runs_no_retry(
        self,
    ):
        """The replacement lands at the third status poll: the waiter's own read of the provider error after the loop, past every check the
        loop makes. Fails on the unit-017 functions (verify_wait, verify_grace, close_verifier of 27bced8): the retry is exec'd for V2."""
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0'})
        self.rate_limited_verifier()
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('ratelimit-live')
        self.run_sh(
            'verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4
        )
        first = self.state()['verifying']['verdict']
        self.on_nth_get(3, self.cancel_and_replace())
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn('verification closed elsewhere', out)
        self.assertNotIn('retry:', out)
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], first)
        self.assertEqual(self.outcome_of(second['packet']), 'running')
        self.assertEqual(len(self.verifier_starts()), 2)

    def test_a_waiter_whose_verification_the_next_verify_closed_after_the_loop_reports_the_verdict_and_leaves_the_new_packet(
        self,
    ):
        """A stalled verifier ends the loop at the fourth status poll; the fifth is the waiter's read of the provider error after it. There the
        verdict is written and the next verify closes it and starts V2. Fails on the unit-017 functions of 27bced8."""
        self.env.update(
            {
                'KITCHEN_VERIFY_POLL_S': '0',
                'KITCHEN_VERDICT_SETTLE_S': '0',
                'KITCHEN_VERIFY_IDLE_S': '0',
            }
        )
        open_ = self.open_slow_verification()
        written = (
            "printf 'status: clean\\nunits: 001\\n\\n## Findings\\n\\nnone\\n\\n## Evidence\\n\\n```\\n$ true\\n```\\n' >"
            f'{open_["verdict"]}\n{self.script} verify {self.store} 001 --every 0 --timeout 60 >/dev/null 2>&1'
        )
        self.on_nth_get(5, written)
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1')
        self.assertIn(f'verdict: {open_["verdict"]}\n', out)
        self.assertIn('status: clean (closed by another verify --wait)', out)
        self.assertEqual(self.outcome_of(open_['packet']), 'clean')
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], open_['verdict'])
        self.assertEqual(self.outcome_of(second['packet']), 'running')

    def test_a_verification_replaced_just_before_the_grace_gets_no_deadline_prompt_on_the_new_verifier(
        self,
    ):
        """The replacement lands at the second status poll of the resumed wait, the provider read that decides the grace. Fails on the
        unit-017 functions of 27bced8: verify_grace prompts V2's verifier with the deadline text."""
        self.env['KITCHEN_VERIFY_POLL_S'] = '0'
        self.deadline_verify('slow', code=4, every='0')
        first = self.state()['verifying']['verdict']
        before = len(self.grace_prompts())
        self.on_nth_get(2, self.cancel_and_replace())
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn('verification closed elsewhere', out)
        self.assertNotEqual(self.state()['verifying']['verdict'], first)
        self.assertEqual(len(self.grace_prompts()), before)

    def test_a_waiter_whose_verification_was_cancelled_and_replaced_touches_nothing(
        self,
    ):
        self.open_slow_verification()
        first = self.state()['verifying']['verdict']
        pane = self.state()['verifier']['pane_id']
        hook = self.fake / 'once/get'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(
            f'#!/bin/bash\n{self.script} verify {self.store} --cancel >/dev/null 2>&1\n'
            f'{self.script} verify {self.store} 001 --every 0 --timeout 60 >/dev/null 2>&1\n'
        )
        hook.chmod(0o755)
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=5)
        self.assertIn('verification closed elsewhere', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertEqual(calls.count(f'pane close {pane}'), 1)
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], first)
        self.assertTrue((self.store / 'scratch' / second['scratch']).exists())
        self.assertTrue(
            (self.fake / 'panes' / self.state()['verifier']['pane_id']).exists()
        )

    def test_a_verdict_written_but_unread_is_closed_by_the_next_verify(self):
        open_ = self.open_slow_verification()
        Path(open_['verdict']).write_text(
            'status: clean\nunits: 001\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertIn(f'closed: {open_["verdict"]} (clean)', out)
        self.assertIn('verifying: units 001 by pi', out)
        self.assertNotEqual(self.state()['verifying']['verdict'], open_['verdict'])
        self.assertIn('\tmaster\tverify\t', (self.store / 'events.tsv').read_text())

    def test_verify_cancel_stops_the_open_verification_and_removes_its_pane_and_scratch(
        self,
    ):
        open_ = self.open_slow_verification()
        pane = self.state()['verifier']['pane_id']
        scratch = self.store / 'scratch' / open_['scratch']
        self.assertTrue(scratch.exists())
        out = self.run_sh('verify', str(self.store), '--cancel')
        self.assertIn('cancelled: 001', out)
        self.assertNotIn('verifying', self.state())
        self.assertFalse(scratch.exists())
        self.assertIn(f'pane close {pane}', (self.fake / 'calls.log').read_text())
        self.assertIn('\tverify-cancel\t', (self.store / 'events.tsv').read_text())
        self.assertEqual(self.packet_fields()['verifier-outcome'], 'cancelled')
        fleet = subprocess.run(
            [
                sys.executable,
                str(script),
                '--json',
                'fleet',
                '--runs',
                str(self.store.parent),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        attempts = json.loads(fleet.stdout)['verifiers']
        self.assertEqual(sum(v['running'] for v in attempts), 0)

    def test_verify_ends_an_earlier_verifier_still_running_before_it_starts_the_next(
        self,
    ):
        self.open_slow_verification()
        pane = self.state()['verifier']['pane_id']
        # The store forgot the verification (a cancel recorded only there); its verifier still runs in its pane.
        doc = self.state()
        del doc['verifying']
        (self.store / 'pair.json').write_text(json.dumps(doc))
        out = self.run_sh(
            'verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4
        )
        self.assertIn(
            'stopped: the earlier verifier demo-verifier was still running; its verdict is void',
            out,
        )
        calls = (self.fake / 'calls.log').read_text()
        self.assertLess(
            calls.index(f'pane close {pane}'), calls.rindex('agent start demo-verifier')
        )

    def test_verify_cancel_with_nothing_open_exits_5(self):
        self.assertIn(
            'no verification open',
            self.run_sh('verify', str(self.store), '--cancel', code=5),
        )

    def rate_limited_verifier(self):
        session = Path(self.tmp.name) / 'pi-session.jsonl'
        session.write_text('{"type":"session"}\n')
        (self.fake / 'sessions').mkdir()
        (self.fake / 'sessions/demo-verifier').write_text(str(session))

    def verify_wakes(self):
        return [w for w in self.wakes() if w.startswith('verify:')]

    def test_a_rate_limited_verifier_says_so_and_retries_on_the_fallback(self):
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit', 0)
        self.assertIn('status: inconclusive (the verifier hit its provider', out)
        self.assertIn(
            'provider_error: Reached free model rate limit. Your limit will reset in 50 minutes',
            out,
        )
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertIn('verifier demo-verifier (devin (model unspecified)) in ', out)
        self.assertIn('status: clean\n', out)
        starts = [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-verifier')
        ]
        self.assertEqual(len(starts), 2)
        self.assertIn('--kind devin', starts[1])
        self.assertNotIn('devin/swe-2', starts[1])
        self.assertEqual(self.verify_wakes(), ['verify:clean'])

    def set_roster(self, extra):
        Path(self.env['PSTACK_KITCHEN_ROSTER']).write_text(
            '[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n'
            '[sidekick.fallback]\nkind = "devin"\n[consultant]\nkind = "codex"\n'
            + extra
        )

    def verifier_starts(self):
        return [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent start demo-verifier')
        ]

    def verify_landing(self):
        self.git(
            'update-ref',
            'refs/remotes/origin/main',
            self.git('rev-list', '--max-parents=0', 'HEAD'),
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        return self.run_sh('verify', str(self.store), '--landing')

    def declare_setup(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(
            toml.read_text()
            + '\n[scratch]\nsetup = ["bun install --frozen-lockfile"]\n'
        )
        self.commit('declare scratch setup')

    def prove_block(self, packet):
        return (
            packet.read_text()
            .split('## Prove', 1)[1]
            .split('```bash\n', 1)[1]
            .split('```', 1)[0]
            .splitlines()
        )

    def test_the_unit_packet_runs_setup_first_only_when_declared(self):
        self.verify_app_unit('clean', 0)
        plain = self.prove_block(self.store / 'verdicts/001-util-v1-packet.md')
        self.assertEqual(
            plain[0], 'export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=demo'
        )
        self.assertIn(' gate app behavioral ', plain[1])
        self.assertFalse(any(' setup' in line for line in plain))

    def test_the_unit_packet_puts_setup_ahead_of_the_gates(self):
        self.declare_setup()
        self.verify_app_unit('clean', 0)
        block = self.prove_block(self.store / 'verdicts/001-util-v1-packet.md')
        self.assertEqual(
            block[0], 'export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=demo'
        )
        self.assertRegex(
            block[1], r'^python3 \S+kitchen\.py --repo \S+/001-util-verify setup$'
        )
        self.assertIn(' gate app behavioral ', block[2])

    def test_every_packet_states_the_write_rule_and_master_only_verify_lines_stay_out(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        text = b.read_text()
        self.assertIn('```bash\nx\n```', text)
        b.write_text(
            text.replace(
                '```bash\nx\n```',
                '```bash\npython3 -c "print(1)"\nkitchen.sh log demo x y z w  # master-only\n```',
            )
        )
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '--timeout', '60', '001')
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        rule = 'Write rule: you write only your verdict file and the scratch worktree you were given'
        self.assertIn(rule, packet.split('## Unit')[0])
        self.assertIn('python3 -c "print(1)"', packet.split('not for the verifier:')[0])
        self.assertEqual(packet.count('kitchen.sh log demo x y z w'), 1)
        self.assertIn(
            'kitchen.sh log demo x y z w', packet.split('not for the verifier:')[1]
        )
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        self.assertIn(rule, (self.store / 'verdicts/landing-v1-packet.md').read_text())

    def packet_with_verify(self, block):
        """A unit packet for a brief whose Verify section is `block` (rewritten after the dispatch, as a brief's Verify may be edited)."""
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        text = b.read_text()
        self.assertIn('```bash\nx\n```', text)
        b.write_text(text.replace('```bash\nx\n```', block))
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        out = self.run_sh(
            'verify', str(self.store), '001', '--timeout', '60', '--every', '0'
        )
        return out, (self.store / 'verdicts/001-util-v1-packet.md').read_text()

    def run_in_store(self, *args, code=0, cwd=None):
        """kitchen.sh run from the store itself (or cwd), the way a store named `.` is."""
        result = subprocess.run(
            [str(self.script), *args],
            cwd=cwd or self.store,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if code is not None:
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_a_store_named_dot_records_absolute_paths_and_lists_relative_commands_for_the_verifier(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        text = b.read_text()
        block = (
            '```bash\n./tooling/x --flag\n'
            "./tooling/nix-develop --command ./tooling/cargo-cached nextest run -E 'test(/^a::b::/)'\n"
            f'cat {self.store}/standing-orders.md\n```'
        )
        b.write_text(text.replace('```bash\nx\n```', block))
        self.run_in_store(
            'dispatch', '.', 'briefs/001-util.md', '--timeout', '1', code=4
        )
        self.assertEqual(self.state()['dispatch']['brief'], str(b))
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.run_in_store('verify', '.', '001', '--timeout', '60', '--every', '0')
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        shown, _, apart = packet.partition('not for the verifier:')
        self.assertIn('\n./tooling/x --flag\n', shown)
        self.assertIn("-E 'test(/^a::b::/)'", shown)
        self.assertIn(
            f'(absolute path)\n```bash\ncat {self.store}/standing-orders.md\n```',
            apart,
        )
        self.assertNotIn('tooling', apart)
        self.assertIn(f'\nverdict: {self.store}/verdicts/001-util-v1.md\n', packet)
        self.assertIn(f'\nbrief: {self.store}/briefs/001-util.md\n', packet)
        self.run_in_store('status', str(self.store), cwd=self.repo)

    def test_rotate_master_with_a_store_named_dot_hands_the_absolute_store_on(self):
        self.filled_handoff()
        self.run_in_store('rotate-master', '.')
        text = (self.store / 'sessions/master-1-handoff.md').read_text()
        self.assertIn(f'store: {self.store}\n', text)
        self.assertNotIn('store: .\n', text)
        self.assertIn(
            f'pstack-kitchen MASTER {self.store}',
            (self.fake / 'master-bootstrap').read_text(),
        )

    def test_finish_with_a_store_named_dot_and_a_relative_report_reruns_verify_and_sends_the_report(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['true'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 6\n')
        self.write('tests/test_app.py', 'x = 6\n')
        head = self.commit('util')
        report = self.store / 'reports' / b.name
        report.write_text(f'# Report\n\nstatus: done\nhead: {head}\n')
        out = self.run_in_store('finish', '.', f'reports/{b.name}')
        self.assertNotIn('FileNotFoundError', out)
        self.assertIn('REPORT', out)

    def test_a_verify_block_the_kitchen_refuses_is_warned_of_and_said_so_in_the_packet(
        self,
    ):
        out, packet = self.packet_with_verify('run the tests please')
        self.assertIn('Brief commands: none (brief-verify: kitchen: ', packet)
        self.assertIn('the Verify section has text outside a ``` block)', packet)
        self.assertIn('warning: unit 001: brief-verify: kitchen: ', out)

    def test_each_brief_command_is_its_own_numbered_block_run_from_the_scratch_root(
        self,
    ):
        _, packet = self.packet_with_verify(
            '```bash\ncd tests\npython3 -c "print(2)"\n```'
        )
        self.assertIn(
            'Run each command in a fresh shell from the scratch root.', packet
        )
        self.assertIn('Command 1:\n\n```bash\ncd tests\n```', packet)
        self.assertIn('Command 2:\n\n```bash\npython3 -c "print(2)"\n```', packet)

    def test_a_command_naming_the_checkout_or_the_store_in_any_spelling_is_not_for_the_verifier(
        self,
    ):
        self.env['HOME'] = self.tmp.name
        home = self.tmp.name
        repo_rel, store_rel = (
            os.path.relpath(self.repo, home),
            os.path.relpath(self.store, home),
        )
        commands = [
            f'cd {self.repo} && python3 tests/run.py',
            f'pytest "{self.repo}"',
            f'python3 {self.repo}/tools/check.py',
            f'cat {self.store}/standing-orders.md',
            f'cat ~/{store_rel}/x',
            f'cat $HOME/{store_rel}/x',
            f'cat ${{HOME}}/{repo_rel}/x',
            f'cat "$HOME"/{store_rel}/x',
            f'cat "${{HOME}}"/{repo_rel}/x',
            f'sh {self.repo}/.git/hooks/x',
            f'x {self.repo};y',
        ]
        own = ['/usr/bin/env python3 x.py', 'ls /tmp/x', f'echo /other{self.repo}/y']
        out, packet = self.packet_with_verify(
            '```bash\n' + '\n'.join(commands + own) + '\n```'
        )
        self.assertNotIn('grep:', out)
        shown, _, apart = packet.partition('not for the verifier:')
        for c in commands:
            self.assertIn(f'(absolute path)\n```bash\n{c}\n```', apart, c)
            self.assertNotIn(f'\n{c}\n', shown, c)
        for n, c in enumerate(own, start=1):
            self.assertIn(f'Command {n}:\n\n```bash\n{c}\n```', shown, c)
        self.assertNotIn(f'Command {len(own) + 1}:', shown)

    def test_a_heredoc_that_holds_a_markdown_fence_stays_whole_in_a_longer_fence(self):
        """The Verify block's own parser ends at a line that starts with three backticks, so the fence inside the heredoc is indented."""
        _, packet = self.packet_with_verify(
            '```bash\ncat <<EOF\n  ```\n  fenced\n  ```\nEOF\n```'
        )
        self.assertIn(
            'Command 1:\n\n````bash\ncat <<EOF\n  ```\n  fenced\n  ```\nEOF\n````',
            packet,
        )

    def test_a_heredoc_marked_master_only_on_its_first_line_stays_out_of_the_verifiers_commands(
        self,
    ):
        _, packet = self.packet_with_verify(
            '```bash\ncat <<EOF  # master-only\nnote\nEOF\ntrue\n```'
        )
        own, _, apart = packet.partition('not for the verifier:')
        self.assertNotIn('cat <<EOF', own)
        self.assertIn(
            '(master-only)\n```bash\ncat <<EOF  # master-only\nnote\nEOF\n```', apart
        )
        self.assertIn('Command 1:\n\n```bash\ntrue\n```', own)

    def test_a_unit_landed_before_the_landing_base_is_left_out_of_the_stack_and_goals(
        self,
    ):
        for n, slug, goal in (
            (1, 'util', 'Users can add numbers.'),
            (2, 'next', 'Users can list numbers.'),
        ):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            b.write_text(
                re.sub(r'(## Goal\n\n)x', rf'\g<1>{goal}', b.read_text(), count=1)
            )
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            head = self.commit(slug)
            self.done(b, head)
            if n == 1:
                self.git('update-ref', 'refs/remotes/origin/main', head)
        self.run_sh(
            'verify', str(self.store), '--timeout', '60', '--landing', '--every', '0'
        )
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        stack = packet.split('## Stack')[1].split('## Goals')[0]
        goals = packet.split('## Goals')[1].split('\n## ')[0]
        self.assertIn('002-next.md', stack)
        self.assertNotIn('001-util.md', stack)
        self.assertIn('Users can list numbers.', goals)
        self.assertNotIn('Users can add numbers.', goals)

    def landing_deadline_world(self):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'behavioral = ["true"]', 'behavioral = ["true"]\nlanding = ["true"]'
            ),
        )
        self.commit('declare a landing command')
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))

    def write_gate_timings(self, rows):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'timings.tsv').write_text(
            ''.join(
                f'{i}\t{profile}\t{stage}\tverifier\tpass\t{seconds}\t0\t0\t{seconds}\n'
                for i, (profile, stage, seconds) in enumerate(rows)
            )
        )

    def test_prove_lines_carry_the_gates_median_and_a_gate_of_eight_minutes_or_more_is_marked_for_the_background(
        self,
    ):
        self.landing_deadline_world()
        self.write_gate_timings(
            [('app', 'behavioral', 9999)] * 2
            + [('app', 'behavioral', 372)] * 10
            + [('app', 'landing', 500)] * 10
        )
        self.run_sh('verify', str(self.store), '--landing', '--every', '0')
        block = self.prove_block(self.store / 'verdicts/landing-v1-packet.md')
        gates = [
            re.sub(
                r'^python3 \S+kitchen\.py --repo \S+ ',
                'python3 K --repo S ',
                re.sub(r'--base [0-9a-f]+', '--base BASE', l),
            )
            for l in block[1:]
        ]
        self.assertEqual(
            gates,
            [
                'python3 K --repo S gate app behavioral --role verifier --base BASE # median 6m 12s over 10 runs',
                '# over 8 minutes: run it in the background, wait for it to finish, then check its exit status',
                'python3 K --repo S gate app landing --role verifier --base BASE # median 8m 20s over 10 runs',
            ],
        )

    def test_a_gate_with_fewer_than_three_passing_runs_is_unmeasured_on_its_prove_line(
        self,
    ):
        self.landing_deadline_world()
        self.write_gate_timings(
            [('app', 'behavioral', 372)] * 2 + [('app', 'landing', 30)] * 3
        )
        self.run_sh('verify', str(self.store), '--landing', '--every', '0')
        gates = [
            re.sub(
                r'^python3 \S+kitchen\.py --repo \S+ ',
                'python3 K --repo S ',
                re.sub(r'--base [0-9a-f]+', '--base BASE', l),
            )
            for l in self.prove_block(self.store / 'verdicts/landing-v1-packet.md')[1:]
        ]
        self.assertEqual(
            gates,
            [
                'python3 K --repo S gate app behavioral --role verifier --base BASE # unmeasured',
                'python3 K --repo S gate app landing --role verifier --base BASE # median 30s over 3 runs',
            ],
        )

    def test_the_landing_deadline_counts_the_landing_gates_it_measured(self):
        self.landing_deadline_world()
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'timings.tsv').write_text(
            f'{int(time.time())}\tapp\tlanding\tverifier\tpass\t600\t0\t0\t600\n'
        )
        out = self.run_sh('verify', str(self.store), '--landing', '--every', '0')
        self.assertIn(
            'deadline +30m for the landing gates of app: 3 x 10m measured; now 75m', out
        )

    def test_the_landing_deadline_adds_a_fixed_allowance_for_a_landing_gate_never_measured(
        self,
    ):
        self.landing_deadline_world()
        out = self.run_sh('verify', str(self.store), '--landing', '--every', '0')
        self.assertIn(
            'deadline +10m for the landing gates of app: 3 x 0m measured, 1 unmeasured at 10m each; now 55m',
            out,
        )

    def test_a_unit_landed_before_the_landing_base_is_neither_in_the_stack_nor_covered_by_the_landing_verdict(
        self,
    ):
        """One test decides both: land-check's `covered:` and the packet's Stack call the same function."""
        for n, slug in ((1, 'util'), (2, 'next')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            head = self.commit(slug)
            self.done(b, head)
            (self.store / f'reviews/00{n}-{slug}.md').write_text('verdict: accept\n')
            if n == 1:
                landed = head
                self.git('update-ref', 'refs/remotes/origin/main', landed)
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {landed[:9]}..{head[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertNotIn('covered: unit 001', out)
        self.assertIn(
            f'land-check: note: unit 001 landed before the base of landing verdict {landing}; verify it on its own',
            out,
        )
        self.assertIn('unverified: unit 001', out)
        self.assertIn(f'covered: unit 002 by the clean landing verdict {landing}', out)
        self.run_sh(
            'verify', str(self.store), '--timeout', '60', '--landing', '--every', '0'
        )
        stack = (
            (self.store / 'verdicts/landing-v2-packet.md')
            .read_text()
            .split('## Stack')[1]
            .split('## Goals')[0]
        )
        self.assertIn('002-next.md', stack)
        self.assertNotIn('001-util.md', stack)

    def landed_units(self, *workflows):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        for n, workflow in enumerate(workflows, 1):
            b = self.brief(f'00{n}', f'u{n}', ['src/**', 'tests/**'])
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            self.done(b, self.commit(f'u{n}'))
            if workflow:
                self.stamp(
                    f'00{n}', workflow, (f'{workflow}.prove', f'Prove {workflow}.')
                )
            else:
                (self.store / f'compositions/00{n}.json').unlink(missing_ok=True)
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        return self.packet_text('landing-v1')

    def landing_playbook(self, packet):
        return packet.split('## Playbook (what to prove)')[1].split('\n## ')[0]

    def test_a_landing_packet_lists_each_distinct_compositions_verifier_parts_once(
        self,
    ):
        section = self.landing_playbook(
            self.landed_units('bug-fix', 'feature', 'bug-fix')
        )
        self.assertEqual(section.count('Prove bug-fix.'), 1)
        self.assertEqual(section.count('Prove feature.'), 1)
        self.assertIn('unit 001: bug-fix', section)
        self.assertIn('unit 002: feature', section)
        self.assertNotIn('unit 003:', section)
        self.assertIn(
            '### shipping.step-1\n\nProve the unit the way a user would', section
        )

    def test_a_landing_under_mode_commit_says_what_the_mode_allows_before_any_part(
        self,
    ):
        section = self.landing_playbook(self.landed_units('bug-fix'))
        cap = '[landing].mode is commit: the kitchen never merges; prove only what this mode allows.'
        self.assertIn(cap, section)
        self.assertLess(section.index(cap), section.index('### bug-fix.prove'))
        self.assertLess(section.index(cap), section.index('### shipping.step-1'))

    def test_a_landing_with_no_compositions_holds_only_the_landing_workflow_parts(
        self,
    ):
        section = self.landing_playbook(self.landed_units(None, None))
        self.assertNotIn('unit 001:', section)
        ids = re.findall(r'(?m)^### (\S+)', section)
        self.assertTrue(
            ids and all(i.startswith(('opening-a-pr.', 'shipping.')) for i in ids), ids
        )
        self.assertIn('shipping.step-1', ids)

    def test_a_landing_packet_holds_each_covered_briefs_goal(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        for n, slug, goal in (
            (1, 'util', 'Users can add numbers.'),
            (2, 'next', 'Users can list numbers.'),
        ):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            b.write_text(
                re.sub(r'(## Goal\n\n)x', rf'\g<1>{goal}', b.read_text(), count=1)
            )
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            self.done(b, self.commit(slug))
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        goals = packet.split('## Goals')[1].split('\n## ')[0]
        self.assertIn('### Unit 001 util\n\nUsers can add numbers.', goals)
        self.assertIn('### Unit 002 next\n\nUsers can list numbers.', goals)
        self.assertIn(
            '## Flows driven', (skill / 'references/verdict-template.md').read_text()
        )

    PLAN_VERIFICATION = (
        '# Plan 900\n\n## Verification\n\n'
        '- **Each open gap:** a test that fails at the old head and passes at the unit head.\n'
        '- **Scenario:** `tests/pstack-pair/scenario.sh` for pair, guided and trio, diffed against the goldens. The suite, '
        '`python3 scripts/check-pstack.py`, and `python3 tests/spec_gen.py` with 0 missing.\n'
        '- `python3 tests/run.py -k plan_cmd`\n'
        "- **Master obligations, not a verifier's:**\n"
        '  - a landing verification after the last unit;\n'
        '  - `kitchen.sh feedback --close` for each of the 21 reports.\n\n'
        '```bash\npython3 -c "print(\'fenced\')"\n```\n\n'
        '```bash\nkitchen.sh log demo x y z w  # master-only\n```\n\n'
        '```bash\nkitchen.sh feedback --close <id> <commit|none> "<note>"\n```\n\n'
        '```text\npython3 not_a_shell_fence.py\n```\n\n'
        '```bash\n# only a comment\n```\n\n'
        '```bash\nfalse\necho after\n```\n\n'
        '## Risks\n\n- `python3 not_a_verification.py`\n'
    )

    def landing_world(
        self,
        units,
        plan_text=None,
        verified=(),
        statuses=(),
        with_plan=True,
        reviews=(),
    ):
        """A landing over units (seq, slug, goal, acceptance) of briefs that name plans/900-demo.md, with reviews as (unit name, text) notes
        written before the packet is made; returns the landing packet's text."""
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        plan = self.store / 'plans/900-demo.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text(self.PLAN_VERIFICATION if plan_text is None else plan_text)
        (self.store / 'reviews/900-demo.md').write_text('verdict: agreed\n')
        for seq, slug, goal, acceptance in units:
            b = self.brief(
                seq,
                slug,
                ['src/**', 'tests/**'],
                plan=str(plan) if with_plan else 'none',
            )
            text = re.sub(
                r'(## Goal\n\n)x',
                lambda m, goal=goal: (
                    m.group(1) + goal.replace('STORE', str(self.store))
                ),
                b.read_text(),
                count=1,
            )
            b.write_text(
                re.sub(
                    r'(## Acceptance\n\n)[^\n]*',
                    lambda m, acceptance=acceptance: m.group(1) + acceptance,
                    text,
                    count=1,
                )
            )
            self.dispatch(b)
            self.write(f'src/mod{seq}.py', f'X = {seq}\n')
            self.write(f'tests/test_mod{seq}.py', f'x = {seq}\n')
            self.done(b, self.commit(slug))
        for seq, status in [(seq, 'clean') for seq in verified] + list(statuses):
            (self.store / 'verdicts').mkdir(exist_ok=True)
            (self.store / f'verdicts/{seq}-v1.md').write_text(
                f'# Verdict\n\nstatus: {status}\nunits: {seq}\n\n## Evidence\n\n```\n$ true\n```\n'
            )
        for name, text in reviews:
            (self.store / f'reviews/{name}.md').write_text(text)
        self.run_sh(
            'verify', str(self.store), '--timeout', '60', '--landing', '--every', '0'
        )
        return (self.store / 'verdicts/landing-v1-packet.md').read_text()

    def test_a_landing_packet_runs_only_a_plans_shell_fences_and_lists_every_other_line_as_a_master_obligation(
        self,
    ):
        packet = self.landing_world([('001', 'util', 'g1', 'a1')])
        plan = self.store / 'plans/900-demo.md'
        section = packet.split(f'## Plan verification: {plan}')[1].split('\n## ')[0]
        prove = self.prove_block(self.store / 'verdicts/landing-v1-packet.md')
        self.assertIn('python3 -c "print(\'fenced\')"', prove)
        self.assertEqual(
            [l for l in prove if l.startswith('# plan ')],
            [
                f'# plan {plan}: Verification command 1',
                f'# plan {plan}: Verification command 2',
            ],
        )
        self.assertFalse(
            [
                l
                for l in prove
                if any(
                    x in l
                    for x in (
                        'spec_gen',
                        'not_a_verification',
                        'check-pstack',
                        'plan_cmd',
                        'not_a_shell_fence',
                        '<id>',
                    )
                )
            ]
        )
        self.assertIn('2 commands from it run in the Prove block below.', section)
        self.assertIn(
            '(master-only)\n```bash\nkitchen.sh log demo x y z w  # master-only\n```',
            section.split('Master obligations')[0],
        )
        obligations = (
            section.split(
                'Master obligations, not yours: report what evidence you see, or write "pending"\n\n'
            )[1]
            .strip('\n')
            .split('\n')
        )
        self.assertEqual(
            obligations,
            [
                '- line 5: **Each open gap:** a test that fails at the old head and passes at the unit head.',
                '- line 6: **Scenario:** `tests/pstack-pair/scenario.sh` for pair, guided and trio, diffed against the goldens. The suite, '
                '`python3 scripts/check-pstack.py`, and `python3 tests/spec_gen.py` with 0 missing.',
                '- line 7: `python3 tests/run.py -k plan_cmd`',
                "- line 8: **Master obligations, not a verifier's:**",
                '- line 9: a landing verification after the last unit;',
                '- line 10: `kitchen.sh feedback --close` for each of the 21 reports.',
                '- line 20: ```bash',
                '- line 21: kitchen.sh feedback --close <id> <commit|none> "<note>"',
                '- line 22: ```',
                '- line 24: ```text',
                '- line 25: python3 not_a_shell_fence.py',
                '- line 26: ```',
                '- line 28: ```bash',
                '- line 29: # only a comment',
                '- line 30: ```',
            ],
        )

    def test_a_plan_command_block_fails_at_its_first_failing_line(self):
        self.landing_world([('001', 'util', 'g1', 'a1')])
        plan = self.store / 'plans/900-demo.md'
        prove = '\n'.join(
            self.prove_block(self.store / 'verdicts/landing-v1-packet.md')
        )
        scratch = Path(self.tmp.name) / 'scratch'
        scratch.mkdir()
        runs = []
        for n in (1, 2):
            block = prove.split(f'# plan {plan}: Verification command {n}\n')[1].split(
                '\n# plan '
            )[0]
            block = re.sub(r'\(set -e\ncd \S+\n', f'(set -e\ncd {scratch}\n', block)
            runs.append(
                subprocess.run(
                    ['bash', '-c', block], capture_output=True, text=True, timeout=60
                )
            )
        self.assertEqual((runs[0].returncode, runs[0].stdout), (0, 'fenced\n'))
        self.assertEqual((runs[1].returncode, runs[1].stdout), (1, ''))

    def test_a_landing_packet_warns_of_a_plan_that_is_missing_or_has_no_verification(
        self,
    ):
        plan = self.store / 'plans/900-demo.md'
        packet = self.landing_world(
            [('001', 'util', 'g1', 'a1')], plan_text='# Plan 900\n\n## Goal\n\nx\n'
        )
        self.assertIn(
            f'## Plan verification: {plan}\n\nwarning: plan {plan}: no ## Verification section\n',
            packet,
        )
        self.assertNotIn('commands from it run', packet)

    def test_a_landing_packet_warns_on_stderr_of_a_plan_that_does_not_exist(self):
        plan = self.store / 'plans/900-demo.md'
        b = self.brief('001', 'util', ['src/**', 'tests/**'], plan=str(plan))
        plan.parent.mkdir(exist_ok=True)
        plan.write_text(self.PLAN_VERIFICATION)
        (self.store / 'reviews/900-demo.md').write_text('verdict: agreed\n')
        self.git(
            'update-ref',
            'refs/remotes/origin/main',
            self.git('rev-list', '--max-parents=0', 'HEAD'),
        )
        self.dispatch(b)
        self.write('src/mod.py', 'X = 1\n')
        self.write('tests/test_mod.py', 'x = 1\n')
        self.done(b, self.commit('util'))
        plan.unlink()
        out = self.run_sh(
            'verify', str(self.store), '--timeout', '60', '--landing', '--every', '0'
        )
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn(
            f'## Plan verification: {plan}\n\nwarning: plan {plan}: no such file\n',
            packet,
        )
        self.assertIn(f'warning: plan {plan}: no such file', out)

    def test_a_landing_packet_adds_the_acceptance_of_a_unit_with_no_clean_verdict_and_names_both_kinds_of_unit(
        self,
    ):
        packet = self.landing_world(
            [
                ('001', 'util', 'g1', 'Acceptance one holds.'),
                ('002', 'next', 'g2', 'Acceptance two holds.'),
            ],
            verified=('001',),
        )
        plan = self.store / 'plans/900-demo.md'
        stack = packet.split('## Stack\n\n')[1].split('\n- ')[0].split('\n')
        self.assertEqual(
            stack[:2],
            [
                'Verified on their own: 001-util.',
                'Folded into this landing, with no verdict of their own: 002-next; their Acceptance is below, and you prove it here.',
            ],
        )
        self.assertNotIn('Each unit below was verified on its own.', packet)
        folded = [l for l in packet.split('\n') if l.startswith('## Acceptance: ')]
        self.assertEqual(folded, [f'## Acceptance: unit 002-next (plan {plan})'])
        self.assertEqual(
            packet.split(folded[0] + '\n\n')[1]
            .split('\n## ')[0]
            .strip('\n')
            .split('\n'),
            ['Acceptance two holds.'],
        )
        self.assertNotIn('Acceptance one holds.', packet)

    def test_a_landing_packet_gives_each_folded_unit_its_accepted_amendments(self):
        note = '# Review {n}\n\nverdict: accept\n\n## Amendments\n\n{text}\n\n## Checked\n\nread it\n'
        packet = self.landing_world(
            [
                ('001', 'util', 'g1', 'Acceptance one holds.'),
                ('002', 'next', 'g2', 'Acceptance two holds.'),
                ('003', 'last', 'g3', 'Acceptance three holds.'),
            ],
            verified=('003',),
            reviews=[
                (
                    '001-util',
                    note.format(n='001', text='the CLI keeps --old: accepted.'),
                ),
                ('002-next', note.format(n='002', text='none')),
                ('003-last', note.format(n='003', text='verified unit amendment')),
            ],
        )
        one = packet.split('## Acceptance: unit 001-util')[1].split('\n## ')[0]
        two = packet.split('## Acceptance: unit 002-next')[1].split('\n## ')[0]
        self.assertIn(
            '\n\nAcceptance one holds.\n\nAccepted by the master (reviews/001-util.md), whatever the Acceptance above says',
            one,
        )
        self.assertTrue(one.rstrip('\n').endswith('the CLI keeps --old: accepted.'))
        self.assertNotIn('Accepted by the master', two)
        # A unit with a verdict of its own is not folded, so its amendments are not part of the landing's Acceptance.
        self.assertNotIn('verified unit amendment', packet)
        self.assertEqual(packet.count('Accepted by the master'), 1)

    def test_a_unit_whose_only_verdict_is_a_reject_or_inconclusive_is_folded_with_its_acceptance(
        self,
    ):
        packet = self.landing_world(
            [
                ('001', 'util', 'g1', 'Acceptance one holds.'),
                ('002', 'next', 'g2', 'Acceptance two holds.'),
            ],
            statuses=(('001', 'reject'), ('002', 'inconclusive')),
        )
        plan = self.store / 'plans/900-demo.md'
        self.assertIn(
            'Verified on their own: none.\nFolded into this landing, with no verdict of their own: 001-util, 002-next; '
            'their Acceptance is below, and you prove it here.\n',
            packet,
        )
        self.assertEqual(
            [l for l in packet.split('\n') if l.startswith('## Acceptance: ')],
            [
                f'## Acceptance: unit 001-util (plan {plan})',
                f'## Acceptance: unit 002-next (plan {plan})',
            ],
        )
        self.assertIn(
            '## Acceptance: unit 001-util (plan %s)\n\nAcceptance one holds.\n' % plan,
            packet,
        )

    def test_a_folded_unit_whose_brief_names_no_plan_shows_plan_none(self):
        packet = self.landing_world(
            [('001', 'util', 'g1', 'Acceptance one holds.')], with_plan=False
        )
        self.assertIn(
            '## Acceptance: unit 001-util (plan none)\n\nAcceptance one holds.\n',
            packet,
        )
        self.assertNotIn('## Plan verification', packet)

    def test_a_landing_packet_over_units_all_verified_says_none_are_folded_in(self):
        packet = self.landing_world(
            [('001', 'util', 'g1', 'Acceptance one holds.')], verified=('001',)
        )
        self.assertIn(
            'Verified on their own: 001-util.\nFolded into this landing, with no verdict of their own: none.\n',
            packet,
        )
        self.assertFalse(
            [l for l in packet.split('\n') if l.startswith('## Acceptance: ')]
        )

    def inputs_of(self, packet):
        """The lines of a packet's `## Inputs (read-only)` section, or None when it has none."""
        if '## Inputs (read-only)\n\n' not in packet:
            return None
        return packet.split('## Inputs (read-only)\n\n')[1].split('\n\n')[0].split('\n')

    def test_a_unit_packet_lists_the_store_paths_its_brief_names_as_read_only_inputs_and_keeps_a_verify_command_apart(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        text = b.read_text().replace(
            '```bash\nx\n```', f'```bash\ncat {self.store}/verify-only.txt\n```'
        )
        for name in (
            'feedback-triage/triage-D.tsv',
            'reports/012-x.md',
            'reviews/003-q.md',
        ):
            (self.store / name).parent.mkdir(exist_ok=True)
            (self.store / name).write_text('x\n')
        b.write_text(
            text
            + f'\n## Notes\n\nRead {self.store}/feedback-triage/triage-D.tsv, then {self.store}/reports/012-x.md.\n'
            f'Again {self.store}/feedback-triage/triage-D.tsv; the store itself, {self.store}, is no input.\n'
            f'A placeholder {self.store}/briefs/<seq>-x.md, a line {self.store}/reports/012-x.md:12, a missing {self.store}/missing/none.md '
            f'and a glob {self.store}/reviews/0*.md.\n'
        )
        self.dispatch(b)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '001', '--timeout', '60', '--every', '0')
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        inputs = self.inputs_of(packet)
        self.assertIn(
            f'- {self.store}/feedback-triage/triage-D.tsv: read it; do not write it',
            inputs,
        )
        self.assertEqual(
            inputs.index(
                f'- {self.store}/feedback-triage/triage-D.tsv: read it; do not write it'
            )
            + 1,
            inputs.index(f'- {self.store}/reports/012-x.md: read it; do not write it'),
        )
        self.assertEqual(len([l for l in inputs if 'triage-D.tsv' in l]), 1)
        self.assertFalse([l for l in inputs if 'verify-only.txt' in l])
        self.assertIn(f'- {self.store}/reviews/0*.md: read it; do not write it', inputs)
        self.assertEqual(
            [l for l in inputs if '/reports/012-x.md' in l],
            [f'- {self.store}/reports/012-x.md: read it; do not write it'],
        )
        self.assertFalse(
            [
                l
                for l in inputs
                if '/briefs/' in l
                and '<seq>' in l
                or l == f'- {self.store}/briefs/: read it; do not write it'
            ]
        )
        self.assertFalse([l for l in inputs if 'none.md' in l])
        self.assertIn(
            f'(absolute path)\n```bash\ncat {self.store}/verify-only.txt\n```',
            packet.split('not for the verifier:')[1],
        )

    def test_a_packet_whose_briefs_name_no_existing_store_path_has_no_inputs_section(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        b.write_text(b.read_text().replace(str(self.store), '/elsewhere'))
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '001', '--timeout', '60', '--every', '0')
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertIsNone(self.inputs_of(packet))
        self.assertNotIn('Inputs', packet)

    def test_a_landing_packet_lists_the_store_paths_its_covered_briefs_name(self):
        (self.store / 'reports').mkdir(exist_ok=True)
        (self.store / 'reports/030-y.md').write_text('x\n')
        packet = self.landing_world(
            [('001', 'util', 'See ' + 'STORE/reports/030-y.md for the flow.', 'a1')]
        )
        inputs = self.inputs_of(packet)
        self.assertIn(
            f'- {self.store}/reports/030-y.md: read it; do not write it', inputs
        )
        self.assertIn(
            f'- {self.store}/plans/900-demo.md: read it; do not write it', inputs
        )

    def test_the_landing_packet_runs_setup_first_when_declared(self):
        self.declare_setup()
        self.verify_landing()
        block = self.prove_block(self.store / 'verdicts/landing-v1-packet.md')
        self.assertRegex(
            block[1], r'^python3 \S+kitchen\.py --repo \S+/000-landing-verify setup$'
        )
        self.assertIn(' gate app behavioral ', block[2])

    def standing_line(self):
        """A line of the test store's standing-orders.md that no other part of a packet repeats."""
        text = (self.store / 'standing-orders.md').read_text()
        line = next(l for l in text.splitlines() if l.startswith('13. '))
        return text, line

    def assert_standing_orders_say_who_they_bind(self, packet):
        section = packet.split('## Standing orders', 1)[1].split('## Changed', 1)[0]
        self.assertIn(
            "An order about the verifier, or about running the repo's commands and tests, binds you; one about the sidekick's scope, steps, commits or reports does not.",
            section,
        )
        self.assertNotIn('as they bind the sidekick', section)
        self.assertNotIn(
            '\n# ', section, 'the inlined orders open no top-level section'
        )

    def test_the_unit_packet_carries_the_runs_standing_orders(self):
        self.verify_app_unit('clean', 0)
        text, line = self.standing_line()
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertIn('## Standing orders', packet)
        self.assertIn(f'{self.store}/standing-orders.md', packet)
        self.assertIn(line, packet)
        self.assertLess(packet.index('## Standing orders'), packet.index('## Changed'))
        self.assert_standing_orders_say_who_they_bind(packet)

    def test_the_landing_packet_carries_the_runs_standing_orders(self):
        self.verify_landing()
        _, line = self.standing_line()
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn('## Standing orders', packet)
        self.assertIn(line, packet)
        self.assertLess(packet.index('## Standing orders'), packet.index('## Changed'))
        self.assert_standing_orders_say_who_they_bind(packet)

    def test_a_store_without_standing_orders_still_gets_its_packets(self):
        (self.store / 'standing-orders.md').unlink()
        self.verify_app_unit('clean', 0)
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertNotIn('## Standing orders', packet)
        self.assertIn('## Changed', packet)
        self.assertIn('## Prove', packet)

    def packet_fields(self, n='001-util-v1'):
        text = (self.store / f'verdicts/{n}-packet.md').read_text()
        return {
            k: v
            for k, v in (
                line.split(': ', 1)
                for line in text.splitlines()
                if line.startswith('verifier-')
            )
        }

    def test_the_packet_records_the_configured_agent_class_and_a_prepared_outcome(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\nargs = ["--approve", "--model", "devin/swe-2", "--thinking", "high"]\n'
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertEqual(
            self.packet_fields(),
            {
                'verifier-agent': 'pi --model devin/swe-2 --thinking high',
                'verifier-class': 'routine',
                'verifier-outcome': 'running',
            },
        )

    def test_every_attempt_ends_with_its_own_outcome(self):
        for mode, code, outcome in [
            ('clean', 0, 'clean'),
            ('reject', 2, 'reject'),
            ('inconclusive', 2, 'inconclusive'),
            ('noevidence', 2, 'invalid'),
            ('gone', 4, 'missing'),
        ]:
            with self.subTest(mode):
                self.setUp()
                self.verify_app_unit(mode, code)
                self.assertEqual(self.packet_fields()['verifier-outcome'], outcome)
                self.assertNotIn('verifying', self.state())

    def test_a_provider_stop_then_a_clean_fallback_are_two_attempts(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n'
        )
        self.rate_limited_verifier()
        self.verify_app_unit('ratelimit', 0)
        self.assertEqual(
            self.packet_fields('001-util-v1')['verifier-outcome'], 'provider'
        )
        self.assertEqual(self.packet_fields('001-util-v2')['verifier-outcome'], 'clean')
        self.assertEqual(
            self.packet_fields('001-util-v2')['verifier-agent'],
            'devin (model unspecified)',
        )

    def test_both_attempts_failing_are_both_recorded(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n'
        )
        self.rate_limited_verifier()
        self.verify_app_unit('ratelimit-all', 2)
        self.assertEqual(
            [self.packet_fields(f'001-util-v{k}')['verifier-outcome'] for k in (1, 2)],
            ['provider', 'provider'],
        )

    def test_a_failed_start_is_recorded_as_start_failed(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'trust-dialog').touch()
        self.run_sh('verify', str(self.store), '001', code=5)
        self.assertEqual(self.packet_fields()['verifier-outcome'], 'start-failed')

    def test_landing_and_escalated_packets_name_their_class(self):
        self.verify_landing()
        self.assertEqual(
            self.packet_fields('landing-v1'),
            {
                'verifier-agent': 'claude --model claude-opus-5-5',
                'verifier-class': 'landing',
                'verifier-outcome': 'clean',
            },
        )

    def deadline_verify(self, mode, *extra, code=0, every=None, prompts_fail=False):
        self.env.setdefault('KITCHEN_VERIFY_GRACE_S', '4')
        self.env['KITCHEN_VERIFY_GRACE_POLL_S'] = '1'
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text(mode)
        if prompts_fail:
            (self.fake / 'prompt-fail').touch()
        return self.run_sh(
            'verify',
            str(self.store),
            '001',
            '--timeout',
            '0',
            *(['--every', every] if every else []),
            *extra,
            code=code,
        )

    def grace_prompts(self):
        return [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent prompt demo-verifier Deadline reached')
        ]

    def test_a_swallowed_packet_is_sent_once_more(self):
        out = self.deadline_verify('swallow', code=0)
        self.assertIn('redelivered: the verifier sat idle without the packet', out)
        self.assertIn('status: clean', out)
        loads = [
            line
            for line in (self.fake / 'calls.log').read_text().splitlines()
            if line.startswith('agent prompt demo-verifier Load')
        ]
        self.assertEqual(len(loads), 2)

    def test_a_verifier_that_got_its_packet_is_not_sent_it_again(self):
        self.deadline_verify('clean')
        self.assertNotIn('verify-redeliver', (self.store / 'events.tsv').read_text())

    def test_wait_sends_the_packet_a_stopped_verify_never_sent(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        state = self.state()
        self.assertTrue(state['verifying']['delivered'])
        # Stopped between the verifier's start and its packet.
        state['verifying']['delivered'] = False
        (self.store / 'pair.json').write_text(json.dumps(state))
        (self.fake / 'verdict-mode').write_text('clean')
        out = self.run_sh('verify', str(self.store), '--wait')
        self.assertIn(
            'redelivered: the verify that started this verifier stopped before sending its packet',
            out,
        )
        self.assertIn('status: clean', out)

    def test_an_idle_verifier_with_nothing_running_stalls_before_its_deadline(self):
        self.env.update({'KITCHEN_VERIFY_IDLE_S': '0', 'KITCHEN_VERIFY_POLL_S': '0'})
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        out = self.run_sh('verify', str(self.store), '001', '--timeout', '60', code=4)
        self.assertIn(
            'the verifier sat idle 0m with nothing running; rerun: kitchen.sh verify',
            out,
        )
        self.assertEqual(self.packet_fields()['verifier-outcome'], 'stalled')
        self.assertIn('verify:stalled', (self.store / 'events.tsv').read_text())
        self.assertNotIn('verifying', self.state())
        self.assertEqual(self.grace_prompts(), [])

    def test_progress_says_what_an_idle_verifier_is_running(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 10\n')
        self.write('tests/test_app.py', 'x = 10\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        verifier = self.state()['verifier']
        pair = self.store / 'pair.json'
        data = json.loads(pair.read_text())
        data['verifying']['started'] -= 100
        pair.write_text(json.dumps(data))
        (self.fake / 'agents' / verifier['name']).write_text('idle pi\n')
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=4)
        self.assertRegex(out, r'verifier idle\n')
        # a link to this interpreter named sleep: macOS hides the environment of its own sleep
        sleeper = self.repo.parent / 'bin' / 'sleep'
        sleeper.parent.mkdir()
        sleeper.symlink_to(sys.executable)
        proc = subprocess.Popen(  # bounded: killed by addCleanup
            [str(sleeper), '-c', 'import signal; signal.pause()'],
            cwd=self.repo,
            env={**self.env, 'HERDR_PANE_ID': verifier['pane_id']},
        )
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=4)
        self.assertRegex(out, r'verifier working \(sleep for 0m\)\n')

    def test_the_deadline_asks_once_for_an_inconclusive_verdict(self):
        out = self.deadline_verify('grace-writes', code=2)
        self.assertIn('status: inconclusive', out)
        self.assertEqual(len(self.grace_prompts()), 1)
        self.assertIn('write the file complete in one write', self.grace_prompts()[0])
        self.assertEqual(self.packet_fields()['verifier-outcome'], 'inconclusive')
        self.assertNotIn('verifying', self.state())

    def test_no_verdict_after_the_grace_is_missing(self):
        out = self.deadline_verify('slow', code=4)
        self.assertIn('no verdict at', out)
        self.assertEqual(len(self.grace_prompts()), 1)
        self.assertEqual(self.packet_fields()['verifier-outcome'], 'missing')

    def claude_transcript(self, text):
        (self.fake / 'session-ids').mkdir(exist_ok=True)
        (self.fake / 'session-ids/demo-verifier').write_text(
            'abc-123 /work/repo.git/v1\n'
        )
        path = (
            Path(self.env['HOME']) / '.claude/projects/-work-repo-git-v1/abc-123.jsonl'
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_a_verdict_less_attempt_names_the_transcript_and_whether_the_packet_arrived(
        self,
    ):
        path = self.claude_transcript(
            '{"type":"user","message":{"content":"Deadline reached: write it"}}\n'
        )
        out = self.deadline_verify('slow', code=4)
        self.assertIn(f'transcript: {path}', out)
        self.assertIn('packet: never reached the verifier', out)

    def test_a_transcript_with_the_packet_says_it_arrived(self):
        packet = self.store / 'verdicts/001-util-v1-packet.md'
        self.claude_transcript(
            f'{{"type":"user","message":{{"content":"pstack-kitchen VERIFY {packet}"}}}}\n'
        )
        out = self.deadline_verify('slow', code=4)
        self.assertIn('packet: the verifier received it', out)

    def test_a_verdict_arriving_during_the_grace_wins(self):
        verdict = self.store / 'verdicts/001-util-v1.md'
        packet = self.store / 'verdicts/001-util-v1-packet.md'
        late = subprocess.Popen(  # bounded: killed by addCleanup
            [
                'bash',
                '-c',
                f"until [ -f {packet} ]; do sleep 0.2; done; sleep 1.5; printf '# V\\n\\nstatus: reject\\nunits: 001\\n\\n## Findings\\n\\n1. x\\n\\n## Evidence\\n\\n```\\na\\n```\\n' >{verdict}",  # kitchen-allow: test-sleep (the verdict is written after the packet appears)
            ]
        )
        self.addCleanup(lambda: (late.kill(), late.wait(timeout=60)))
        out = self.deadline_verify('slow', code=2)
        self.assertIn('status: reject', out)

    def test_a_later_wait_resumes_the_same_grace_and_never_prompts_again(self):
        self.env['KITCHEN_VERIFY_GRACE_S'] = '600'
        out = self.deadline_verify('slow', code=4, every='0')
        self.assertIn('past its deadline; grace', out)
        v = self.state()['verifying']
        self.assertEqual(v['grace_delivery'], 'prompted')
        until = v['grace_until']
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=4)
        self.assertIn('past its deadline; grace', out)
        self.assertEqual(self.state()['verifying']['grace_until'], until)
        self.assertEqual(len(self.grace_prompts()), 1)
        state = self.state()
        state['verifying']['grace_until'] = int(time.time()) - 1
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.run_sh('verify', str(self.store), '--wait', code=4)
        self.assertIn('no verdict at', out)
        self.assertEqual(len(self.grace_prompts()), 1)

    def test_a_failed_delivery_is_recorded_and_the_grace_still_runs_out(self):
        self.deadline_verify('slow', code=4, every='0', prompts_fail=True)
        self.assertEqual(
            self.state()['verifying']['grace_delivery'], 'skipped:prompt-failed'
        )
        self.assertEqual(len(self.grace_prompts()), 1)

    def test_a_blocked_or_absent_verifier_gets_no_grace(self):
        for mode in ('blocked', 'gone'):
            with self.subTest(mode):
                self.setUp()
                out = self.deadline_verify(mode, code=4)
                self.assertIn('no verdict at', out)
                self.assertEqual(self.grace_prompts(), [])
                self.assertNotIn('past its deadline', out)

    def test_a_working_devin_is_not_prompted_but_the_grace_still_waits(self):
        self.set_roster('[verifier]\nkind = "devin"\n')
        self.env['KITCHEN_VERIFY_GRACE_S'] = '600'
        self.deadline_verify('devin-working', code=4, every='0')
        self.assertEqual(
            self.state()['verifying']['grace_delivery'], 'skipped:devin-working'
        )
        self.assertEqual(self.grace_prompts(), [])

    def test_a_provider_error_at_the_deadline_takes_the_fallback_not_the_grace(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n'
        )
        self.rate_limited_verifier()
        out = self.deadline_verify('ratelimit-live', code=0)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertEqual(self.grace_prompts(), [])

    def test_a_routine_verifier_defaults_to_the_sidekick(self):
        self.verify_app_unit('clean', 0)
        self.assertIn('--kind pi', self.verifier_starts()[0])
        self.assertIn('--model devin/swe-2', self.verifier_starts()[0])
        self.assertEqual(self.state()['verifier']['kind'], 'pi')

    def test_an_explicit_verifier_entry_wins_over_the_sidekick(self):
        self.set_roster('[verifier]\nkind = "devin"\nargs = ["--model", "swe-x"]\n')
        self.verify_app_unit('clean', 0)
        (start,) = self.verifier_starts()
        self.assertIn('--kind devin', start)
        self.assertIn('--model swe-x', start)
        self.assertNotIn('devin/swe-2', start)

    def test_landing_and_escalated_verifiers_default_to_claude_opus(self):
        self.verify_landing()
        (start,) = self.verifier_starts()
        self.assertIn('--kind claude', start)
        self.assertIn('--model claude-opus-5-5', start)

    def test_the_master_sentinel_starts_the_masters_own_kind(self):
        self.set_roster('[verifier.escalated]\nkind = "master"\n')
        self.verify_landing()
        (start,) = self.verifier_starts()
        self.assertIn('--kind claude', start)
        self.assertNotIn('claude-opus-5-5', start)

    def test_a_rate_limited_verifier_retries_on_the_roster_fallback_once(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\nargs = ["--model", "m"]\n'
            '[verifier.fallback]\nkind = "devin"\nargs = ["--model", "fb"]\n'
        )
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit', 0)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        first, second = self.verifier_starts()
        self.assertIn('--kind pi', first)
        self.assertIn('--model m', first)
        self.assertIn('--kind devin', second)
        self.assertIn('--model fb', second)
        self.assertTrue(self.state()['verifier']['kind'] == 'devin')

    def test_a_fallback_that_hits_its_provider_too_is_not_retried_again(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n'
        )
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit-all', 2)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertEqual(len(self.verifier_starts()), 2)
        self.assertIn(
            f'next: wait for the limit, or kitchen.sh verify {self.store} 001 --kind <another kind>',
            out,
        )

    def test_a_verifier_stopped_by_its_provider_retries_down_its_whole_chain(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\nargs = ["--model", "first"]\n'
            '[[verifier.fallback]]\nkind = "pi"\nargs = ["--model", "second"]\n'
            '[[verifier.fallback]]\nkind = "devin"\n'
        )
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit', 0)
        starts = self.verifier_starts()
        self.assertEqual(len(starts), 3, starts)
        self.assertIn('--model first', starts[0])
        self.assertIn('--model second', starts[1])
        self.assertIn('--kind devin', starts[2])
        self.assertIn('retry: verifying again on the fallback, pi second', out)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        failovers = [
            line.split('\t')[5]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tfailover\t' in line
        ]
        self.assertEqual(
            failovers, ['verifier:pi first:pi second', 'verifier:pi second:devin']
        )
        self.assertIn('status: clean\n', out)

    def verify_after_a_provider_stop(self, roster, *verify_args):
        """The pi verifier stops on its provider (the fake's `ratelimit` mode; every other kind works): ([the agents started, as kind and
        arguments], [the failover events], the output)."""
        self.set_roster(roster)
        (self.fake / 'amp-preflight-fail').write_text('a/one\n')
        self.rate_limited_verifier()
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('ratelimit')
        out = self.run_sh('verify', str(self.store), '001', *verify_args)
        started = [line.split(' --kind ')[1] for line in self.verifier_starts()]
        failovers = [
            line.split('\t')[5]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tfailover\t' in line
        ]
        return started, failovers, out

    def test_a_stopped_kind_verifier_retries_on_the_next_entry_not_the_agent_that_stopped(
        self,
    ):
        # --kind pi starts the roster's second entry; its provider stop retries on the third, not on the second again.
        started, failovers, out = self.verify_after_a_provider_stop(
            '[verifier]\nkind = "devin"\n[[verifier.fallback]]\nkind = "pi"\nargs = ["--model", "second"]\n'
            '[[verifier.fallback]]\nkind = "devin"\nargs = ["--model", "third"]\n',
            '--kind',
            'pi',
        )
        self.assertIn('status: clean\n', out)
        self.assertIn('retry: verifying again on the fallback, devin third', out)
        self.assertEqual(len(started), 2, started)
        self.assertIn('pi', started[0])
        self.assertIn('--model second', started[0])
        self.assertIn('devin', started[1])
        self.assertIn('--model third', started[1])
        self.assertEqual(failovers, ['verifier:pi second:devin third'])

    def test_a_verifier_retry_steps_past_an_entry_that_fails_its_preflight(self):
        started, failovers, out = self.verify_after_a_provider_stop(
            '[verifier]\nkind = "pi"\nargs = ["--model", "first"]\n'
            '[[verifier.fallback]]\nkind = "amp"\nargs = ["--model", "a/one"]\n'
            '[[verifier.fallback]]\nkind = "devin"\n'
        )
        self.assertIn('status: clean\n', out)
        self.assertIn('failover: verifier amp a/one -> devin (preflight: ', out)
        self.assertEqual(len(started), 2, started)
        self.assertIn('pi', started[0])
        self.assertIn('devin', started[1])
        self.assertEqual(
            failovers, ['verifier:pi first:amp a/one', 'verifier:amp a/one:devin']
        )

    def test_a_fallback_of_the_failed_kind_is_not_retried(self):
        self.set_roster(
            '[verifier]\nkind = "pi"\nargs = ["--model", "same"]\n[verifier.fallback]\nkind = "pi"\nargs = ["--model", "same"]\n'
        )
        self.rate_limited_verifier()
        self.verify_app_unit('ratelimit', 2)
        self.assertEqual(len(self.verifier_starts()), 1)

    GAP = '## Gaps\n\n### 1. dispatch hid the rotation\n\nWhat happened: x\nWorkaround: none\nSuggestion: say so\nEvidence: events.tsv\n\n## What worked\n\n- gates\n'

    def feedback_dir(self):
        return Path(self.env['XDG_STATE_HOME']) / 'pstack/feedback'

    def gap_file(self, text=None, name='gaps.md'):
        f = Path(self.tmp.name) / name
        f.write_text(self.GAP if text is None else text)
        return f

    def file_feedback(self, text=None):
        out = self.run_sh('feedback', str(self.store), str(self.gap_file(text)))
        filed = re.search(r'^filed: (.+)$', out, re.MULTILINE)
        assert filed is not None
        return out, Path(filed.group(1))

    def calls(self):
        return (self.fake / 'calls.log').read_text()

    def test_feedback_files_the_report_behind_a_header(self):
        out, path = self.file_feedback()
        self.assertEqual(path.parent, self.feedback_dir() / 'inbox')
        text = path.read_text()
        self.assertRegex(
            text,
            r'^# Kitchen feedback: demo \(repo\)\nid: \d{8}-repo-demo\nrun: demo   store: ',
        )
        self.assertIn(
            f'master: demo-master (p0)\nbuild: unknown ({skill.parent.resolve()})\nretro: ',
            text,
        )
        self.assertNotIn('filed with:', text)
        self.assertRegex(
            text,
            r'retro: run demo: 0 unit reports, 0 verified units \(0 clean verdicts on the first try\)',
        )
        self.assertRegex(
            text,
            r'filed: \d{4}-\d\d-\d\dT[\d:]+Z\nstatus: open\n\n## Gaps\n\n### 1\. dispatch hid the rotation',
        )
        self.assertIn('feedback\t', (self.store / 'events.tsv').read_text())
        self.assertIn(
            'delivered: notification for the human (no maintainer is registered)', out
        )
        self.assertEqual(list(self.feedback_dir().glob('.filing-*')), [])

    def test_a_file_without_a_gap_files_nothing(self):
        out = self.run_sh(
            'feedback',
            str(self.store),
            str(self.gap_file('## Gaps\n\nnothing\n')),
            code=1,
        )
        self.assertIn('has no gap', out)
        self.assertFalse(
            (self.feedback_dir() / 'inbox').exists()
            and list((self.feedback_dir() / 'inbox').iterdir())
        )

    def test_simultaneous_filings_get_distinct_ids(self):
        f = self.gap_file()
        procs = [
            subprocess.Popen(  # bounded: communicate(timeout=60) below
                [str(self.script), 'feedback', str(self.store), str(f)],
                cwd=self.repo,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for _ in range(4)
        ]
        outs = [p.communicate(timeout=60)[0] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0] * 4, outs)
        ids = sorted(p.stem for p in (self.feedback_dir() / 'inbox').glob('*.md'))
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)
        for p in (self.feedback_dir() / 'inbox').glob('*.md'):
            self.assertIn(f'id: {p.stem}\n', p.read_text())

    def register_maintainer(self):
        out = self.run_sh('maintainer', 'on', '--pane', 'p0')
        self.assertIn('maintainer: demo-master on p0', out)

    def test_a_live_maintainer_gets_the_pointer(self):
        self.register_maintainer()
        out, path = self.file_feedback()
        self.assertIn('delivered: to maintainer demo-master', out)
        self.assertIn(
            f'agent prompt demo-master pstack-kitchen FEEDBACK {path}', self.calls()
        )
        self.assertNotIn('notification show', self.calls())
        self.assertIn('live', self.run_sh('maintainer', 'status'))

    def test_every_broken_maintainer_falls_back_to_the_notification_with_the_report_filed(
        self,
    ):
        self.register_maintainer()
        reg = self.feedback_dir() / 'maintainer.json'
        good = reg.read_text()

        def stale_pane():
            reg.write_text(json.dumps({**json.loads(good), 'pane': 'p9'}))

        def replaced():
            reg.write_text(json.dumps({**json.loads(good), 'session': 'older-session'}))

        def blocked():
            (self.fake / 'agents/demo-master').write_text('blocked claude\n')

        def absent():
            (self.fake / 'agents/demo-master').unlink()

        def failed_prompt():
            (self.fake / 'prompt-fail').touch()

        for name, setup, why in [
            ('stale pane', stale_pane, 'moved off pane p9'),
            ('replaced', replaced, 'different session'),
            ('blocked', blocked, 'is blocked'),
            ('absent', absent, 'is gone'),
            ('failed prompt', failed_prompt, 'prompt to the maintainer failed'),
        ]:
            with self.subTest(name):
                reg.write_text(good)
                (self.fake / 'agents/demo-master').write_text('idle claude\n')
                (self.fake / 'prompt-fail').unlink(missing_ok=True)
                (self.fake / 'calls.log').write_text('')
                setup()
                out, path = self.file_feedback()
                self.assertRegex(
                    out, rf'delivered: notification for the human \([^)]*{why}'
                )
                self.assertIn(
                    'notification show pstack-kitchen: feedback filed --body '
                    + str(path),
                    self.calls(),
                )
                self.assertTrue(path.exists())

    def test_maintainer_off_and_status(self):
        self.assertIn('maintainer: none', self.run_sh('maintainer', 'status'))
        self.register_maintainer()
        self.assertEqual(
            json.loads((self.feedback_dir() / 'maintainer.json').read_text())['pane'],
            'p0',
        )
        self.assertIn('maintainer: none', self.run_sh('maintainer', 'off'))
        self.assertFalse((self.feedback_dir() / 'maintainer.json').exists())

    def test_inbox_lists_oldest_first_and_close_moves_the_report(self):
        _, first = self.file_feedback()
        text = first.read_text().replace(
            'filed: ', 'filed: 2020-01-01T00:00:00Z\nold-filed: '
        )
        first.write_text(text)
        self.file_feedback(self.GAP + '\n### 2. second\n')
        lines = self.run_sh('feedback', '--inbox').strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(first.stem), lines)
        self.assertRegex(lines[0], r'demo\s+repo\s+unknown\s+1\s+\d+d')
        self.assertRegex(lines[1], r'demo\s+repo\s+unknown\s+2\s+\d+m')
        out = self.run_sh(
            'feedback', '--close', first.stem, 'abc1234', 'fixed in the next build'
        )
        self.assertIn(f'closed: {first.stem}', out)
        self.assertFalse(first.exists())
        done = (self.feedback_dir() / 'done' / first.name).read_text()
        self.assertIn('status: closed\n', done)
        self.assertIn('## Closed\n\nclosed: ', done)
        self.assertIn('commit: abc1234\nnote: fixed in the next build\n', done)
        self.assertEqual(
            len(self.run_sh('feedback', '--inbox').strip().splitlines()), 1
        )

    def test_closing_twice_prints_the_block_and_an_unknown_id_fails(self):
        _, path = self.file_feedback()
        self.run_sh('feedback', '--close', path.stem, 'none', 'wontfix')
        again = self.run_sh('feedback', '--close', path.stem, 'ffff', 'other note')
        self.assertIn('already closed', again)
        self.assertIn('note: wontfix', again)
        self.assertNotIn('ffff', (self.feedback_dir() / 'done' / path.name).read_text())
        self.assertIn(
            'no feedback report',
            self.run_sh('feedback', '--close', 'nope', 'none', 'x', code=1),
        )
        _, next_path = self.file_feedback()
        self.assertNotEqual(next_path.stem, path.stem)

    def write_builds(self, *rows):
        f = Path(self.env['XDG_STATE_HOME']) / 'pstack/builds.tsv'
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(''.join('\t'.join(r) + '\n' for r in rows))

    def test_init_stamps_the_skills_build_and_a_later_path_adds_a_row(self):
        here = str(skill.parent.resolve())
        builds = self.state()['skill_builds']
        self.assertEqual([b['path'] for b in builds], [here])
        self.run_sh('status', str(self.store))
        self.assertEqual(len(self.state()['skill_builds']), 1)
        state = self.state()
        state['skill_builds'][0]['path'] = '/old/skills'
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.run_sh('status', str(self.store))
        self.assertEqual(
            [b['path'] for b in self.state()['skill_builds']], ['/old/skills', here]
        )

    def test_every_command_word_stamps_the_build_on_a_store_named_second_and_a_missing_store_stamps_nothing(
        self,
    ):
        here = str(skill.parent.resolve())
        for word, code in (('help', 0), ('frob', 1), ('status', 0)):
            state = self.state()
            state['skill_builds'][0]['path'] = '/old/skills'
            (self.store / 'pair.json').write_text(json.dumps(state))
            self.run_sh(word, str(self.store), code=code)
            self.assertEqual(
                [b['path'] for b in self.state()['skill_builds']],
                ['/old/skills', here],
                word,
            )
        before = (self.store / 'pair.json').read_text()
        self.run_sh('help', str(self.store / 'nowhere'))
        self.run_sh('help')
        self.assertEqual((self.store / 'pair.json').read_text(), before)

    def test_a_run_without_stamps_stays_unstamped(self):
        state = self.state()
        del state['skill_builds']
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.run_sh('status', str(self.store))
        self.assertNotIn('skill_builds', self.state())

    def test_feedback_names_the_build_the_run_started_on_and_the_one_it_filed_with(
        self,
    ):
        here = str(skill.parent.resolve())
        self.write_builds(('1', 'abc123', '/old/skills'), ('2', 'def456', here))
        state = self.state()
        state['skill_builds'][0]['path'] = '/old/skills'
        (self.store / 'pair.json').write_text(json.dumps(state))
        _, path = self.file_feedback()
        text = path.read_text()
        self.assertIn(
            'build: abc123 (/old/skills)\nfiled with: def456 (' + here + ')\n', text
        )

    def test_feedback_build_is_unknown_without_a_builds_file(self):
        _, path = self.file_feedback()
        self.assertIn('build: unknown (', path.read_text())

    def test_a_rate_limited_verifier_without_a_fallback_is_inconclusive(self):
        state = self.state()
        del state['sidekick']['fallback']
        del state['sidekick']['chain']
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit', 2)
        self.assertIn('provider_error: Reached free model rate limit', out)
        self.assertIn(
            f'next: wait for the limit, or kitchen.sh verify {self.store} 001 --kind <another kind>',
            out,
        )
        self.assertNotIn('verify:missing', (self.store / 'events.tsv').read_text())
        self.assertEqual(self.verify_wakes(), ['verify:provider'])

    def open_verification_entry(self):
        """The `.verifying.entry` block of pair.json as written, after a verify that leaves its verifier working."""
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh(
            'verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4
        )
        text = (self.store / 'pair.json').read_text()
        return text[text.index('    "entry": {') : text.index('    "retried"')]

    def test_the_sidekick_source_entry_is_written_byte_for_byte(self):
        self.assertEqual(
            self.open_verification_entry(),
            (
                '    "entry": {\n      "kind": "pi",\n      "args": [\n        "--approve",\n        "--model",\n        "devin/swe-2"\n      ],\n'
                '      "source": "sidekick",\n      "fallback": {\n        "kind": "devin",\n        "args": []\n      }\n    },\n'
            ),
        )

    def sidekick_marker_entry(self, chain_index, kind, start_args):
        """The verifier entry of a sidekick-marker verifier, with the sidekick running chain entry chain_index of a pi, devin chain."""
        state = self.state()
        state['sidekick'].update(
            {
                'kind': kind,
                'start_args': start_args,
                'chain': [
                    {'kind': 'pi', 'args': ['--model', 'top']},
                    {'kind': 'devin', 'args': ['--model', 'second']},
                ],
                'chain_index': chain_index,
            }
        )
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.open_verification_entry()
        return self.state()['verifying']['entry']

    def test_the_sidekick_marker_verifier_gets_the_start_arguments_while_the_top_runs(
        self,
    ):
        # The recorded no-mcp path names the build that spawned the sidekick; the verifier gets this build's file.
        stale = '/old/build/references/no-mcp.json'
        entry = self.sidekick_marker_entry(
            0, 'pi', ['--model', 'top', '--mcp-config', stale]
        )
        self.assertEqual(entry['kind'], 'pi')
        self.assertEqual(entry['args'][:3], ['--model', 'top', '--mcp-config'])
        self.assertNotEqual(entry['args'][3], stale)
        self.assertTrue(entry['args'][3].endswith('/references/no-mcp.json'))

    def test_after_a_sidekick_failover_the_marker_verifier_gets_the_top_chain_entrys_arguments(
        self,
    ):
        entry = self.sidekick_marker_entry(1, 'devin', ['--model', 'second'])
        self.assertEqual((entry['kind'], entry['args']), ('pi', ['--model', 'top']))

    def test_the_master_source_entry_is_written_byte_for_byte(self):
        self.set_roster('[verifier]\nkind = "master"\n')
        self.assertEqual(
            self.open_verification_entry(),
            (
                '    "entry": {\n      "kind": "claude",\n      "args": [],\n      "fallback": null,\n      "fallbacks": [],\n      "source": "master"\n    },\n'
            ),
        )

    def assert_fallback_is_damage(self, value, word):
        state = self.state()
        state['sidekick']['fallback'] = value
        state['sidekick'].pop('chain', None)
        (self.store / 'pair.json').write_text(json.dumps(state))
        _, out = self.verify_app_unit('clean', 1)
        self.assertIn(
            f'pair.json is damaged: sidekick.fallback is {word}, not an object', out
        )
        self.assertEqual(self.verifier_starts(), [])

    def assert_argument_is_damage(self, change, word):
        state = self.state()
        change(state['sidekick'])
        state['sidekick'].pop('chain', None)
        (self.store / 'pair.json').write_text(json.dumps(state))
        _, out = self.verify_app_unit('clean', 1)
        self.assertIn(f'pair.json is damaged: {word}', out)
        self.assertEqual(self.verifier_starts(), [])

    def test_a_sidekick_start_argument_that_is_a_number_is_damage_to_verify(self):
        self.assert_argument_is_damage(
            lambda sidekick: sidekick.__setitem__('start_args', [1]),
            'start_args holds number, not a string',
        )

    def test_a_sidekick_fallback_argument_that_is_a_number_is_damage_to_verify(self):
        self.assert_argument_is_damage(
            lambda sidekick: sidekick.__setitem__(
                'fallback', {'kind': 'devin', 'args': [1]}
            ),
            'args holds number, not a string',
        )

    def test_a_sidekick_fallback_that_is_a_string_is_damage_to_verify(self):
        self.assert_fallback_is_damage('devin', 'string')

    def test_a_sidekick_fallback_that_is_an_array_is_damage_to_verify(self):
        self.assert_fallback_is_damage([], 'array')

    def test_a_sidekick_fallback_that_is_a_number_is_damage_to_verify(self):
        self.assert_fallback_is_damage(7, 'number')

    def test_a_sidekick_fallback_that_is_true_is_damage_to_verify(self):
        self.assert_fallback_is_damage(True, 'boolean')

    def test_a_null_sidekick_fallback_is_no_fallback(self):
        state = self.state()
        state['sidekick']['fallback'] = None
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.verify_app_unit('clean', 0)

    def test_verify_deadline_scales_with_measured_gate_time(self):
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        (state_dir / 'timings.tsv').write_text(
            '1\tapp\tbehavioral\tverifier\tpass\t1190.5\n'
            '2\tapp\tbehavioral\tverifier\tfail\t9000\n'
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 7\n')
        self.write('tests/test_app.py', 'x = 7\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        v = self.state()['verifying']
        self.assertEqual(v['deadline'] - v['started'], 60 * 60)

    def deadline_fixture(self, *timings, setup=False):
        if setup:
            self.declare_setup()
        state_dir = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        (state_dir / 'timings.tsv').write_text(
            ''.join('\t'.join(t) + '\n' for t in timings)
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 7\n')
        self.write('tests/test_app.py', 'x = 7\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')

    def deadline_run(self, *extra):
        out = self.run_sh(
            'verify', str(self.store), '001', '--every', '0', *extra, code=4
        )
        v = self.state()['verifying']
        return out, (v['deadline'] - v['started']) // 60

    def test_the_default_deadline_adds_setup_and_load_and_prints_its_parts(self):
        self.env['PSTACK_KITCHEN_LOAD'] = '22 16'
        self.deadline_fixture(
            ('1', 'app', 'behavioral', 'verifier', 'pass', '1200'),
            ('2', '-', 'setup', 'verifier', 'pass', '600'),
            setup=True,
        )
        out, minutes = self.deadline_run()
        self.assertIn(
            'deadline 97m: 3 x 20m gates + 10m setup, x1.38 for load 22 on 16 cores',
            out,
        )
        self.assertEqual(minutes, 97)
        self.assertIn(
            'deadline: 97m from now\n',
            (self.store / 'verdicts/001-u1-v1-packet.md').read_text()
            if False
            else next((self.store / 'verdicts').glob('001-*-packet.md')).read_text(),
        )

    def test_the_load_factor_is_capped_and_a_setup_row_is_ignored_without_declared_setup(
        self,
    ):
        self.env['PSTACK_KITCHEN_LOAD'] = '100 8'
        self.deadline_fixture(
            ('1', 'app', 'behavioral', 'verifier', 'pass', '1200'),
            ('2', '-', 'setup', 'verifier', 'pass', '600'),
        )
        out, minutes = self.deadline_run()
        self.assertIn('deadline 180m: 3 x 20m gates, x3 for load 100 on 8 cores', out)
        self.assertEqual(minutes, 180)

    def test_an_unmeasured_setup_is_named_and_timeout_wins(self):
        self.deadline_fixture(
            ('1', 'app', 'behavioral', 'verifier', 'pass', '1200'), setup=True
        )
        out, minutes = self.deadline_run()
        self.assertIn(
            'deadline 60m: 3 x 20m gates + setup unmeasured, load 1 on 8 cores', out
        )
        self.setUp()
        self.deadline_fixture(('1', 'app', 'behavioral', 'verifier', 'pass', '1200'))
        out, minutes = self.deadline_run('--timeout', '5')
        self.assertIn('deadline 5m (--timeout)', out)
        self.assertEqual(minutes, 5)

    def test_a_malformed_load_override_is_refused(self):
        self.env['PSTACK_KITCHEN_LOAD'] = 'lots'
        self.deadline_fixture(('1', 'app', 'behavioral', 'verifier', 'pass', '60'))
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=1)
        self.assertIn('PSTACK_KITCHEN_LOAD: expected "<load1> <cores>"', out)

    def test_dispatch_rotates_a_pi_sidekick_after_a_done_unit(self):
        state = self.state()
        state['sidekick']['rotation_required'] = True
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.assertIn(
            'rotated: the pi sidekick starts this brief in a fresh session', out
        )
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('agent prompt demo-sidekick /quit', calls)
        self.assertFalse(self.state()['sidekick'].get('rotation_required'))

    def claude_sidekick(self):
        state = self.state()
        state['sidekick']['kind'] = 'claude'
        (self.store / 'pair.json').write_text(json.dumps(state))
        (self.fake / 'agents/demo-sidekick').write_text('idle claude\n')

    def queue_behind(self, brief, status):
        """The sidekick reports `status` on the dispatched brief while the next one waits in the queue."""
        queued = self.brief('002', 'next', ['src/**', 'tests/**'])
        (self.fake / 'agents/demo-sidekick').write_text('working claude\n')
        self.run_sh('queue', str(self.store), str(queued))
        (self.fake / 'agents/demo-sidekick').write_text('idle claude\n')
        self.write('src/first.py', 'F = 1\n')
        head = self.commit('first')
        (self.store / 'reports' / brief.name).write_text(
            f'# Report\n\nstatus: {status}\nhead: {head}\n'
        )
        return queued

    def test_a_claude_sidekick_starts_the_next_brief_in_a_fresh_session(self):
        self.claude_sidekick()
        first = self.brief('001', 'first', ['src/**', 'tests/**'])
        self.verify_block(first, 'true')
        self.dispatch(first)
        queued = self.queue_behind(first, 'done')
        out = self.run_sh(
            'finish', str(self.store), str(self.store / 'reports' / first.name)
        )
        self.assertIn('rotation: required before the next task', out)
        self.assertNotIn('Start that brief now', out)
        self.assertTrue((self.store / 'queue').exists())
        out = self.dispatch(queued)
        self.assertIn(
            'rotated: the claude sidekick starts this brief in a fresh session', out
        )
        self.assertEqual(self.state()['sidekick']['generation'], 2)
        calls = (self.fake / 'calls.log').read_text()
        ready = calls.index('take the sidekick role (generation 2')
        self.assertLess(calls.index('agent prompt demo-sidekick /exit'), ready)
        self.assertLess(ready, calls.index(f'BRIEF {queued}'))

    def test_a_partial_report_keeps_a_claude_sidekick_in_its_session(self):
        self.claude_sidekick()
        first = self.brief('001', 'first', ['src/**', 'tests/**'])
        self.dispatch(first)
        queued = self.queue_behind(first, 'partial')
        out = self.run_sh(
            'finish', str(self.store), str(self.store / 'reports' / first.name)
        )
        self.assertNotIn('rotation:', out)
        out = self.dispatch(queued)
        self.assertNotIn('rotated:', out)
        self.assertEqual(self.state()['sidekick']['generation'], 1)
        self.assertNotIn(
            'agent prompt demo-sidekick /exit', (self.fake / 'calls.log').read_text()
        )

    def test_discuss_rotates_a_pi_sidekick_after_a_done_unit(self):
        state = self.state()
        state['sidekick']['rotation_required'] = True
        (self.store / 'pair.json').write_text(json.dumps(state))
        plan = self.store / 'plans/001-util.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: util\n\nrisk: routine\n')
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertIn(
            'rotated: the pi sidekick starts this plan in a fresh session', out
        )
        self.assertNotIn('needs a fresh session', out)
        self.assertIn(
            'agent prompt demo-sidekick /quit', (self.fake / 'calls.log').read_text()
        )
        self.assertFalse(self.state()['sidekick'].get('rotation_required'))

    def test_an_escalated_plan_reaches_the_consultant_while_the_sidekick_works(self):
        state = self.state()
        state['consultant'] = {'name': 'demo-consultant', 'kind': 'codex'}
        (self.store / 'pair.json').write_text(json.dumps(state))
        (self.fake / 'agents/demo-consultant').write_text('idle codex\n')
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        plan = self.store / 'plans/001-api.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: api\n\nrisk: escalated\n')
        out = self.run_sh(
            'discuss', str(self.store), str(plan), '--timeout', '1', code=4
        )
        self.assertIn('sent PLAN 001-api.md to consultant', out)
        self.assertNotIn('to sidekick', out)
        self.assertIn('sidekick_response: deferred', out)
        self.assertIn(f'consultant_response: {self.store}/advice/001-api.md', out)
        (self.fake / 'agents/demo-sidekick').write_text('idle pi\n')
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertIn('sent PLAN 001-api.md to sidekick', out)
        self.assertNotIn('to consultant', out)
        self.assertIn('sidekick_status: agree', out)
        self.assertIn('consultant_status: agree', out)
        self.assertEqual(
            (self.fake / 'calls.log')
            .read_text()
            .count('agent prompt demo-consultant pstack-kitchen PLAN'),
            1,
        )
        (self.fake / 'agents/demo-consultant').write_text('working codex\n')
        plan.write_text('# Plan 001: api\n\nrisk: escalated\n\nRevised.\n')
        out = self.run_sh(
            'discuss', str(self.store), str(plan), '--timeout', '1', code=5
        )
        self.assertIn(
            'consultant demo-consultant is working; wait for it before a plan round',
            out,
        )

    def consultant_plan(self, risk):
        state = self.state()
        state['consultant'] = {'name': 'demo-consultant', 'kind': 'codex'}
        (self.store / 'pair.json').write_text(json.dumps(state))
        (self.fake / 'agents/demo-consultant').write_text('idle codex\n')
        plan = self.store / 'plans/001-api.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text(f'# Plan 001: api\n\nrisk: {risk}\n')
        return plan

    def test_a_plan_whose_risk_carries_its_reason_still_reaches_the_consultant(self):
        plan = self.consultant_plan('escalated (contract `Card`, Firestore schema)')
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertIn('sent PLAN 001-api.md to consultant', out)
        self.assertIn('consultant_status: agree', out)
        plan.write_text('# Plan 001: api\n\nrisk: high\n')
        self.assertIn(
            'risk: must start with routine or escalated',
            self.run_sh('discuss', str(self.store), str(plan), code=2),
        )

    def test_a_discuss_answered_by_both_roles_logs_one_wake_naming_both(self):
        plan = self.consultant_plan('escalated')
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        rows = [
            r.split('\t')
            for r in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\twake\t' in r
        ]
        self.assertEqual([r[5] for r in rows], ['sidekick:agree,consultant:agree'])

    def contract_plan(self, *blocks, contract='contract: 2\n'):
        plan = self.consultant_plan('escalated')
        steps = ''.join(f'{b}\n' for b in blocks)
        plan.write_text(
            f'# Plan 001: api\n\n{contract}risk: escalated\n\n## Goal\n\ng\n\n'
            f'## Steps\n\n{steps}\n## Verification\n\nv\n'
        )
        return plan

    def plan_prompts(self):
        return (
            (self.fake / 'calls.log').read_text().count('agent prompt demo-consultant')
        )

    def test_discuss_writes_each_blocks_consultant_and_master_parts_into_the_plan(
        self,
    ):
        plan = self.contract_plan(
            '1. First.\n   playbook: feature', '2. Second.\n   playbook: bug-fix'
        )
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertRegex(out, r'playbook: 2 plan blocks, ~\d+ tokens')
        text = plan.read_text()
        self.assertEqual(text.count('## Playbook (plan blocks)'), 1)
        self.assertLess(text.index('## Steps'), text.index('## Playbook (plan blocks)'))
        self.assertLess(
            text.index('## Playbook (plan blocks)'), text.index('## Verification')
        )
        self.assertIn('### Block 1: feature\n', text)
        self.assertIn('### Block 2: bug-fix\n', text)
        self.assertIn('#### feature.step-7 (consultant, master)\n', text)
        self.assertEqual(text.count('#### feature.step-7 '), 1)
        self.assertIn('#### feature.step-8 (master)\n', text)
        self.assertNotIn('feature.step-1 (', text.split('### Block 2')[1])
        self.assertIn('consultant_status: agree', out)

    def test_an_unchanged_discuss_does_not_ask_an_answered_consultant_again(self):
        plan = self.contract_plan('1. First.\n   playbook: feature')
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        written = plan.read_bytes()
        mtime = plan.stat().st_mtime_ns
        self.assertEqual(self.plan_prompts(), 1)
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertEqual(plan.read_bytes(), written)
        self.assertEqual(plan.stat().st_mtime_ns, mtime)
        self.assertEqual(self.plan_prompts(), 1)
        self.assertNotIn('sent PLAN', out)
        self.assertIn('consultant_state: answered', out)

    def test_an_unchanged_discuss_of_a_plan_with_no_later_heading_keeps_its_bytes(
        self,
    ):
        plan = self.contract_plan('1. First.\n   playbook: feature')
        plan.write_text(
            plan.read_text().split('## Verification')[0].rstrip('\n') + '\n'
        )
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        written = plan.read_bytes()
        mtime = plan.stat().st_mtime_ns
        self.assertTrue(written.endswith(b'\n'))
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertEqual(plan.read_bytes(), written)
        self.assertEqual(plan.stat().st_mtime_ns, mtime)
        self.assertEqual(self.plan_prompts(), 1)

    def test_discuss_asks_again_when_a_block_changed(self):
        plan = self.contract_plan('1. First.\n   playbook: feature')
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        plan.write_text(
            plan.read_text().replace('playbook: feature', 'playbook: bug-fix')
        )
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertIn('sent PLAN 001-api.md to consultant', out)
        self.assertIn('### Block 1: bug-fix\n', plan.read_text())
        self.assertEqual(self.plan_prompts(), 2)

    def test_discuss_refuses_a_block_with_no_playbook_before_anything_is_sent(self):
        plan = self.contract_plan('1. First.\n   playbook: feature', '2. Second.')
        before = plan.read_bytes()
        out = self.run_sh('discuss', str(self.store), str(plan), code=2)
        self.assertEqual(out.count('Steps block'), 1)
        self.assertIn('Steps block 2 names no playbook:', out)
        self.assertEqual(plan.read_bytes(), before)
        self.assertEqual(self.plan_prompts(), 0)

    def test_discuss_refuses_an_unmapped_playbook_with_a_line_per_block(self):
        plan = self.contract_plan(
            '1. First.\n   playbook: old-label', '2. Second.\n   playbook: other'
        )
        out = self.run_sh('discuss', str(self.store), str(plan), code=2)
        self.assertIn("Steps block 1: playbook 'old-label' is not mapped", out)
        self.assertIn("Steps block 2: playbook 'other' is not mapped", out)
        self.assertIn('mapped: ', out)
        self.assertEqual(self.plan_prompts(), 0)

    def test_discuss_warns_about_a_plan_without_a_contract_and_sends_it_as_before(self):
        plan = self.contract_plan('1. First.\n   playbook: feature', contract='')
        before = plan.read_bytes()
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertIn('warn: plan has no contract: 2; no playbook section', out)
        self.assertIn('sent PLAN 001-api.md to consultant', out)
        self.assertEqual(plan.read_bytes(), before)

    def consult_file(self, unit_args):
        """A filled design consult on unit 001, as new-consult makes it."""
        consult = Path(
            self.run_sh(
                'new-consult', str(self.store), '001', '--kind', 'design', *unit_args
            ).strip()
        )
        consult.write_text(
            re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL)
        )
        return consult

    def send_consult(self, consult):
        return self.run_sh(
            'consult', str(self.store), str(consult), '--timeout', '1', code=4
        )

    def stamp_consultant(self, unit, workflow, *parts, overlays=()):
        """Writes a stored composition whose consultant owns `parts` as (id, text) pairs."""
        owned = [
            {
                'id': pid,
                'text': text,
                'source': {'root': 'skills', 'path': 'x.md', 'select': 'step:1'},
                'from': [{'part': pid, 'by': f'workflow:{workflow}'}],
            }
            for pid, text in parts
        ]
        path = self.store / 'compositions' / f'{unit}.json'
        path.parent.mkdir(exist_ok=True)
        selected = [{'id': o, 'why': '', 'kind': 'domain'} for o in overlays]
        path.write_text(
            json.dumps(
                {
                    'workflow': workflow,
                    'overlays': {'selected': selected, 'excluded': []},
                    'roles': {
                        'master': [],
                        'sidekick': [],
                        'consultant': owned,
                        'verifier': [],
                    },
                }
            )
        )

    @staticmethod
    def without_send_state(text):
        """Drop the `sidekick state at send:` line the core consult stamps at every send, so a before/after comparison shows only what the kitchen added."""
        return re.sub(r'^sidekick state at send:.*\n', '', text, flags=re.M)

    def test_a_consult_on_a_brief_with_a_composition_gets_the_consultants_parts(self):
        self.consultant_plan('escalated')
        self.brief('001', 'util', ['src/**', 'tests/**'])
        self.stamp_consultant(
            '001',
            'feature',
            ('feature.step-7', 'Consult on the design.\n## Odd heading'),
            overlays=('daily-coaching',),
        )
        consult = self.consult_file([])
        before = consult.read_text()
        self.send_consult(consult)
        text = consult.read_text()
        self.assertEqual(text.count('## Playbook (your parts)'), 1)
        self.assertLess(
            text.index('## Constraints'), text.index('## Playbook (your parts)')
        )
        self.assertLess(text.index('## Playbook (your parts)'), text.index('## Reply'))
        self.assertIn('feature + daily-coaching, from', text)
        self.assertIn('#### feature.step-7\n', text)
        self.assertIn('Consult on the design.', text)
        self.assertNotIn('\n## Odd heading', text)
        self.assertNotEqual(
            self.without_send_state(text), self.without_send_state(before)
        )
        self.send_consult(consult)
        self.assertEqual(consult.read_text(), text)

    def test_a_consult_on_a_brief_without_a_composition_is_sent_as_before(self):
        self.consultant_plan('escalated')
        self.brief('001', 'util', ['src/**', 'tests/**'])
        consult = self.consult_file([])
        before = consult.read_text()
        self.send_consult(consult)
        self.assertEqual(
            self.without_send_state(consult.read_text()),
            self.without_send_state(before),
        )

    def test_a_consult_whose_composition_was_removed_loses_its_stale_section(self):
        self.consultant_plan('escalated')
        self.brief('001', 'util', ['src/**', 'tests/**'])
        self.stamp_consultant('001', 'feature', ('feature.step-7', 'Design.'))
        consult = self.consult_file([])
        self.send_consult(consult)
        self.assertIn('## Playbook (your parts)', consult.read_text())
        (self.store / 'compositions/001.json').unlink()
        self.send_consult(consult)
        self.assertNotIn('## Playbook', consult.read_text())

    def test_a_consult_on_a_contract_2_plan_gets_the_consultants_parts_per_block(self):
        plan = self.contract_plan(
            '1. First.\n   playbook: feature', '2. Second.\n   playbook: bug-fix'
        )
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        consult = self.consult_file([])
        self.send_consult(consult)
        text = consult.read_text()
        self.assertIn(f'unit: {plan}', text)
        self.assertEqual(text.count('## Playbook (your parts)'), 1)
        self.assertIn('### Block 1: feature\n', text)
        self.assertIn('#### feature.step-7\n', text)
        self.assertNotIn('feature.step-8', text)
        self.assertIn('No consultant parts.', text)
        self.assertLess(text.index('## Playbook (your parts)'), text.index('## Reply'))

    def test_a_consult_on_a_plan_without_a_contract_is_sent_as_before(self):
        plan = self.contract_plan('1. First.\n   playbook: feature', contract='')
        consult = self.consult_file([])
        before = consult.read_text()
        self.send_consult(consult)
        self.assertIn(f'unit: {plan}', before)
        self.assertEqual(
            self.without_send_state(consult.read_text()),
            self.without_send_state(before),
        )

    def test_a_consult_on_a_plan_with_an_unmapped_block_is_refused(self):
        plan = self.contract_plan('1. First.\n   playbook: old-label')
        consult = self.consult_file([])
        out = self.run_sh('consult', str(self.store), str(consult), code=2)
        self.assertIn(f"{plan}: Steps block 1: playbook 'old-label' is not mapped", out)
        self.assertNotIn('CONSULT', (self.fake / 'calls.log').read_text())

    def test_a_plan_dropped_by_an_idle_recipient_is_sent_once_more(self):
        plan = self.consultant_plan('escalated')
        (self.fake / 'prompt-drop').touch()
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertRegex(
            out,
            r'redelivered: (sidekick demo-sidekick|consultant demo-consultant) sat idle without the plan',
        )
        self.assertIn('sidekick_status: agree', out)
        self.assertIn('consultant_status: agree', out)
        self.assertEqual(
            (self.store / 'events.tsv').read_text().count('\tplan-redeliver\t'), 1
        )

    def test_a_rotation_that_never_gets_ready_names_the_provider_error_and_the_failover(
        self,
    ):
        state = self.state()
        state['sidekick']['rotation_required'] = True
        (self.store / 'pair.json').write_text(json.dumps(state))
        (self.fake / 'no-ready').touch()
        (self.fake / 'screen').mkdir(exist_ok=True)
        (self.fake / 'screen/demo-sidekick').write_text(
            'Error: Reached free model rate limit. Your limit will reset in 8 minutes.\n'
        )
        out = self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']), code=4)
        self.assertIn('rotating the sidekick before this brief failed', out)
        self.assertIn('provider_error: ', out)
        self.assertIn('rate limit', out)
        self.assertIn(
            f'next: pair.sh failover {self.store} --reason provider-error', out
        )
        self.assertIn('pi:unready', (self.store / 'events.tsv').read_text())

    @unittest.skipUnless(
        Path('/proc/self').exists(), 'background work is read from /proc, Linux only'
    )
    def test_a_running_command_is_not_a_stale_log(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        prog = self.store / 'progress' / b.name
        prog.parent.mkdir(exist_ok=True)
        prog.write_text('- started\n')
        old = time.time() - 600
        os.utime(prog, (old, old))
        # The sleep below must start after the dispatch by more than the whole seconds /proc and the dispatch time are read in.
        pair = self.state()
        pair['dispatch']['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(old))
        (self.store / 'pair.json').write_text(json.dumps(pair))
        out = self.run_sh(
            'wait', str(self.store), '--timeout', '1', '--max', '0', code=4
        )
        self.assertIn('STALE', out)
        self.assertNotIn('"error"', out)
        pane = self.state()['sidekick']['pane_id']
        test = subprocess.Popen(  # bounded: killed by addCleanup
            ['sleep', '30'], cwd=self.repo, env={**self.env, 'HERDR_PANE_ID': pane}
        )
        self.addCleanup(lambda: (test.kill(), test.wait()))
        out = self.run_sh(
            'wait', str(self.store), '--timeout', '1', '--max', '0', code=4
        )
        self.assertIn('running: sleep for 0m', out)
        self.assertNotIn('STALE', out)

    def test_a_unit_rebased_onto_a_new_trunk_is_measured_from_it(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.write('docs/a.md', '# A\n')
        self.commit('a, before the landing')
        b = self.brief('001', 'docs', ['docs/**'])
        self.dispatch(b)
        # The stack lands as a squash; the unit goes on from the new trunk.
        self.git('checkout', '-q', '-b', 'after-landing', init)
        self.write('docs/a.md', '# A\n')
        landed = self.commit('squash of the stack')
        self.git('update-ref', 'refs/remotes/origin/main', landed)
        self.write('docs/b.md', '# B\n')
        head = self.commit('b, on the new trunk')
        self.done(b, head)
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn("note: unit 001's recorded base", out)
        self.assertIn(f'measuring from {landed[:9]}', out)
        verdict = (self.store / 'verdicts/001-docs-v1.md').read_text()
        self.assertIn(f'range: {landed[:9]}..{head[:9]}', verdict)
        out = self.run_sh('verify', str(self.store), '001', '--base', init)
        self.assertIn(
            f'range: {init[:9]}..{head[:9]}',
            (self.store / 'verdicts/001-docs-v2.md').read_text(),
        )

    def test_a_pi_sidekick_that_exits_on_a_rate_limit_says_so(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        cwd = self.state()['cwd']
        logs = (
            Path(self.env['HOME'])
            / '.pi/agent/sessions'
            / ('--' + cwd.lstrip('/').replace('/', '-') + '--')
        )
        logs.mkdir(parents=True)
        (logs / 's.jsonl').write_text(
            '{"type":"message","message":{"role":"assistant","stopReason":"stop"}}\n'
            '{"type":"message","message":{"role":"assistant","stopReason":"error",'
            '"errorMessage":"Reached free model rate limit. Your limit will reset in 50 minutes (at 11:26 UTC)."}}\n'
        )
        (self.fake / 'agents/demo-sidekick').unlink()
        out = self.run_sh(
            'wait', str(self.store), '--timeout', '1', '--max', '0', code=4
        )
        self.assertIn(
            'provider_error: the pi sidekick exited on: Reached free model rate limit',
            out,
        )
        self.assertIn(f'next: pair.sh failover {self.store} --reason exited', out)

    def test_a_muse_sidekick_that_exits_on_a_billing_error_says_so(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        state = self.state()
        state['sidekick']['kind'] = 'muse'
        (self.store / 'pair.json').write_text(json.dumps(state))
        log = (
            Path(self.env['HOME'])
            / '.local/share/muse/sessions'
            / time.strftime('%Y/%m/%d')
            / '01a1-other/session.jsonl'
        )
        log.parent.mkdir(parents=True)
        log.write_text('{"workspace_root":"/somewhere/else"} rate limited\n')
        log = log.parent.parent / '01a1-this/session.jsonl'
        log.parent.mkdir(parents=True)
        log.write_text(
            f'{{"workspace_root":"{state["cwd"]}"}}\n'
            '{"error":"model failed: API error 402: Billing verification failed. Please check your payment method. (billing_error)"}\n'
        )
        (self.fake / 'agents/demo-sidekick').unlink()
        out = self.run_sh(
            'wait', str(self.store), '--timeout', '1', '--max', '0', code=4
        )
        self.assertIn('provider_error: the muse sidekick exited on: ', out)
        self.assertIn('Billing verification failed', out)
        self.assertNotIn('rate limited', out)

    def test_landing_verification_proves_the_stack_tip(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.assertIn(
            'name no units',
            self.run_sh('verify', str(self.store), '001', '--landing', code=1),
        )
        out = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn('landing base: merge-base of remotes/origin/main and HEAD', out)
        self.assertIn(
            'verifier demo-verifier (claude --model claude-opus-5-5) in ', out
        )
        self.assertIn('status: clean', out)
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn('units: landing', packet)
        self.assertIn(str(b), packet)
        self.assertIn('gate app behavioral', packet)
        self.assertIn(
            'landing verification: clean',
            self.run_sh('land-check', str(self.store), code=0),
        )

    def test_a_landing_packet_runs_each_touched_profiles_landing_commands_and_a_unit_packet_does_not(
        self,
    ):
        self.write(
            '.agents/kitchen.toml',
            KITCHEN.replace(
                'behavioral = ["true"]', 'behavioral = ["true"]\nlanding = ["true"]'
            ),
        )
        self.commit('declare a landing command')
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '--timeout', '60', '001')
        self.assertNotIn(
            ' landing --role verifier',
            (self.store / 'verdicts/001-util-v1-packet.md').read_text(),
        )
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertRegex(
            packet,
            r'gate app behavioral --role verifier --base [0-9a-f]+ # unmeasured\n',
        )
        self.assertRegex(
            packet, r'gate app landing --role verifier --base [0-9a-f]+ # unmeasured\n'
        )

    def test_a_clean_landing_verdict_covers_a_unit_inside_its_range(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        head = self.commit('util')
        self.done(b, head)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.assertIn(
            'unverified: unit 001', self.run_sh('land-check', str(self.store), code=2)
        )
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        for status, tip in (('inconclusive', head), ('clean', init)):
            landing.write_text(
                f'# Verdict\n\nstatus: {status}\nunits: landing\nrange: {init}..{tip}\n\n## Evidence\n\n```\n$ true\n```\n'
            )
            self.assertIn(
                'unverified: unit 001',
                self.run_sh('land-check', str(self.store), code=2),
            )
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{head[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(f'covered: unit 001 by the clean landing verdict {landing}', out)
        self.assertIn('land-check: pass', out)

    def fix_world(self, units=('001',), accepted=None):
        """Accepted units with briefs and no verdicts: the land-check store the fix-chain tests build on."""
        for seq in units:
            self.brief(seq, 'util', ['src/**'])
        for seq in accepted or units:
            (self.store / f'reviews/{seq}-util.md').write_text('verdict: accept\n')
        (self.store / 'verdicts').mkdir(exist_ok=True)

    def verdict_file(self, name, status, units):
        path = self.store / f'verdicts/{name}.md'
        path.write_text(
            f'# Verdict\n\nstatus: {status}\nunits: {units}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        return path

    def revise_fix(self, verdict):
        """The fix brief `revise` drafts from a rejected verdict, as the master runs it: its path."""
        return Path(self.run_sh('revise', str(self.store), str(verdict)).strip())

    def reject_and_fix(self, seq, units=None):
        """A rejecting verdict of unit(s) and the fix brief drafted from it: (the verdict, the fix brief)."""
        verdict = self.verdict_file(f'{seq}-util-v1', 'reject', units or seq)
        return verdict, self.revise_fix(verdict)

    def test_revise_with_a_store_named_dot_and_a_relative_verdict_links_the_absolute_verdict(
        self,
    ):
        self.fix_world()
        verdict = self.verdict_file('001-util-v1', 'reject', '001')
        out = self.run_in_store(
            'revise', '.', os.path.relpath(verdict, self.store), code=None
        )
        fix = next((self.store / 'briefs').glob('002-*.md'))
        self.assertIn(f'- verdict: {verdict}\n', fix.read_text(), out)

    def test_a_verified_fix_covers_the_unit_its_verdict_rejected(self):
        self.fix_world()
        rejected, fix = self.reject_and_fix('001')
        self.assertEqual(fix.name[:3], '002')
        self.assertIn(
            'unverified: unit 001', self.run_sh('land-check', str(self.store), code=2)
        )
        self.verdict_file('002-util-fix-v1', 'clean', '002')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn('covered: unit 001 by its verified fix, unit 002\n', out)
        self.assertIn('land-check: pass', out)

    def malformed_verdict(self, name, status, units):
        path = self.store / f'verdicts/{name}.md'
        path.write_text(f'# Verdict\n\nstatus: {status}\nunits: {units}\n')
        return path

    def test_a_malformed_clean_verdict_covers_no_unit_in_land_check(self):
        self.fix_world()
        self.malformed_verdict('001-util-v1', 'clean', '001')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('unverified: unit 001', out)

    def test_revise_refuses_a_malformed_reject_verdict_and_writes_no_brief(self):
        self.fix_world()
        verdict = self.malformed_verdict('001-util-v1', 'reject', '001')
        before = sorted(p.name for p in (self.store / 'briefs').iterdir())
        out = self.run_sh('revise', str(self.store), str(verdict), code=1)
        self.assertIn(
            'verdict is invalid (no command output under Evidence); read it before a fix brief',
            out,
        )
        self.assertEqual(
            before, sorted(p.name for p in (self.store / 'briefs').iterdir())
        )

    def test_a_malformed_landing_verdict_is_reported_invalid_and_gives_no_range(self):
        self.fix_world()
        self.malformed_verdict('landing-v1', 'clean', 'landing')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertRegex(
            out,
            r'landing verification: invalid \(no command output under Evidence\) \(.*landing-v1\.md\)',
        )
        self.assertIn('unverified: unit 001', out)

    def test_a_malformed_reject_verdict_makes_no_fix_cover_its_unit(self):
        self.fix_world()
        _, fix = self.reject_and_fix('001')
        self.verdict_file('002-util-fix-v1', 'clean', '002')
        self.malformed_verdict('001-util-v1', 'reject', '001')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('unverified: unit 001', out)

    def test_a_fix_with_no_clean_verdict_covers_nothing(self):
        self.fix_world()
        _, fix = self.reject_and_fix('001')
        self.verdict_file('002-util-fix-v1', 'reject', '002')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('by its verified fix', out)

    def test_one_verdict_rejecting_two_units_is_fixed_for_the_last_only(self):
        """`revise` carries the last named unit's acceptance, so its clean fix covers that unit and not the other one the verdict named."""
        self.fix_world(('001', '002'))
        _, fix = self.reject_and_fix('001', '001 002')
        self.assertEqual(fix.name[:3], '003')
        self.assertIn(
            f'- original brief: {self.store}/briefs/002-util.md', fix.read_text()
        )
        self.verdict_file('003-util-fix-v1', 'clean', '003')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('covered: unit 002 by its verified fix, unit 003\n', out)
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('unverified: unit 002', out)

    def test_a_landing_fix_covers_each_unit_it_lists(self):
        self.fix_world(('001', '002'))
        rejected = self.verdict_file('landing-v1', 'reject', 'landing (001 002)')
        fix = self.revise_fix(rejected)
        text = fix.read_text()
        self.assertIn(f'- unit brief: {self.store}/briefs/001-util.md', text)
        self.assertIn(f'- unit brief: {self.store}/briefs/002-util.md', text)
        self.verdict_file('003-landing-fix-v1', 'clean', '003')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn('covered: unit 001 by its verified fix, unit 003\n', out)
        self.assertIn('covered: unit 002 by its verified fix, unit 003\n', out)

    def test_a_fix_covers_only_through_the_lines_revise_writes(self):
        self.fix_world()
        _, fix = self.reject_and_fix('001')
        self.verdict_file('002-util-fix-v1', 'clean', '002')
        original = fix.read_text()
        for name, text in {
            'no verdict line': re.sub(r'(?m)^- verdict: .*\n', '', original),
            'no original brief line': re.sub(
                r'(?m)^- original brief: .*\n', '', original
            ),
            'a verdict that is gone': original.replace(
                '- verdict: ', '- verdict: /nowhere'
            ),
        }.items():
            with self.subTest(name):
                fix.write_text(text)
                out = self.run_sh('land-check', str(self.store), code=2)
                self.assertIn('unverified: unit 001', out)
        fix.write_text(original)
        self.verdict_file('001-util-v1', 'clean', '001')
        self.run_sh('land-check', str(self.store))
        # A reject names the fixed unit; a verdict that rejected another one does not make this fix cover 001.
        self.verdict_file('001-util-v1', 'reject', '009')
        self.assertIn(
            'unverified: unit 001', self.run_sh('land-check', str(self.store), code=2)
        )

    def test_a_fenced_context_heading_does_not_make_a_brief_cover_a_unit(self):
        self.fix_world()
        _, fix = self.reject_and_fix('001')
        self.verdict_file('002-util-fix-v1', 'clean', '002')
        original = fix.read_text()
        links = re.findall(r'(?m)^- (?:verdict|original brief): .*\n', original)
        stripped = re.sub(r'(?m)^- (?:verdict|original brief): .*\n', '', original)
        fenced = '## Notes\n\n```md\n## Context\n\n' + ''.join(links) + '```\n\n'
        fix.write_text(stripped.replace('## Context', fenced + '## Context', 1))
        self.assertIn(
            'unverified: unit 001', self.run_sh('land-check', str(self.store), code=2)
        )
        fix.write_text(original)
        self.assertIn('covered: unit 001', self.run_sh('land-check', str(self.store)))

    def test_a_fenced_scope_heading_stays_out_of_a_landing_fix_brief(self):
        a = self.brief('001', 'util', ['src/**'])
        a.write_text(
            a.read_text().replace(
                '## Scope',
                '## Notes\n\n```md\n## Scope\n\n- FAKE/**\n```\n\n## Scope',
                1,
            )
        )
        verdict = self.rejected('landing-v3', 'landing (001)')
        text = Path(
            self.run_sh('revise', str(self.store), str(verdict)).strip()
        ).read_text()
        self.assertIn('- src/** — scope', text)
        self.assertNotIn('FAKE', text)

    def fix_chain(self, links):
        """Unit 001 rejected, its fix rejected in turn, and so on for `links` fixes; only the last fix has a clean verdict."""
        self.fix_world()
        self.reject_and_fix('001')
        for n in range(2, links + 1):
            self.revise_fix(self.verdict_file(f'00{n}-util-fix-v1', 'reject', f'00{n}'))
        self.verdict_file(f'00{links + 1}-util-fix-v1', 'clean', f'00{links + 1}')

    def test_a_chain_of_five_fix_links_covers_the_unit(self):
        self.fix_chain(5)
        out = self.run_sh('land-check', str(self.store))
        self.assertIn('covered: unit 001 by its verified fix, unit 006\n', out)

    def test_a_fix_of_a_rejected_fix_is_named_by_the_fix_with_the_clean_verdict(self):
        self.fix_chain(2)
        out = self.run_sh('land-check', str(self.store))
        self.assertIn('covered: unit 001 by its verified fix, unit 003\n', out)
        self.assertNotIn('unit 002\n', out)

    def test_a_chain_with_a_middle_fix_brief_gone_covers_nothing(self):
        self.fix_chain(2)
        (self.store / 'briefs/002-util-fix.md').unlink()
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('by its verified fix', out)

    def test_a_chain_longer_than_five_links_covers_nothing(self):
        self.fix_chain(6)
        self.assertIn(
            'unverified: unit 001', self.run_sh('land-check', str(self.store), code=2)
        )

    def test_a_cycle_of_fixes_covers_nothing(self):
        """Unit 001's brief says it is the fix of 002 and 002's says it is the fix of 001; neither has a clean verdict."""
        self.fix_world(('001', '002'), accepted=('001',))
        for seq, other in (('001', '002'), ('002', '001')):
            brief = self.store / f'briefs/{seq}-util.md'
            verdict = self.verdict_file(f'{other}-util-v1', 'reject', other)
            context = f'\n- verdict: {verdict}\n- original brief: {self.store}/briefs/{other}-util.md\n\n'
            brief.write_text(
                re.sub(
                    r'(?ms)^(## Context\n).*?(?=^## )',
                    lambda m, c=context: m.group(1) + c,
                    brief.read_text(),
                )
            )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('by its verified fix', out)

    def landing_range_world(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        head = self.commit('util')
        self.done(b, head)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        return init, head, landing

    def test_a_clean_landing_verdict_whose_range_line_has_words_after_the_head_covers_a_unit(
        self,
    ):
        init, head, landing = self.landing_range_world()
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{head[:9]} from the packet\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(f'covered: unit 001 by the clean landing verdict {landing}', out)
        self.assertIn('land-check: pass', out)
        self.assertNotIn('range head does not resolve', out)

    def two_unit_stack(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        heads = []
        for n, slug in ((1, 'util'), (2, 'next')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            heads.append(self.commit(slug))
            self.done(b, heads[-1])
            (self.store / f'reviews/00{n}-{slug}.md').write_text('verdict: accept\n')
        return init, heads

    def land_on_new_trunk(self, init, heads, squash=False):
        self.git('checkout', '-q', '-b', 'landing', init)
        self.write('docs/trunk.md', '# trunk moved\n')
        trunk = self.commit('trunk moved')
        if squash:
            self.git('cherry-pick', '-n', *heads)
            tip = self.commit('squash of the stack')
        else:
            self.git('cherry-pick', *heads)
            tip = self.git('rev-parse', 'HEAD')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {trunk[:9]}..{tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        return landing, tip

    def test_land_check_covers_a_unit_rebased_into_the_stack_by_patch_id(self):
        init, heads = self.two_unit_stack()
        landing, tip = self.land_on_new_trunk(init, heads)
        out = self.run_sh('land-check', str(self.store))
        self.assertRegex(
            out,
            rf'covered: unit 001 \(rebased as [0-9a-f]{{9}}\) by the clean landing verdict {landing}',
        )
        self.assertRegex(
            out,
            rf'covered: unit 002 \(rebased as {tip[:9]}\) by the clean landing verdict {landing}',
        )
        self.assertIn('land-check: pass', out)

    def stack_on_a_new_trunk(self, init, shas):
        """The commits cherry-picked onto a moved trunk, with a clean landing verdict over exactly that range."""
        self.git('checkout', '-q', '-b', 'landing', init)
        self.write('docs/trunk.md', '# trunk moved\n')
        trunk = self.commit('trunk moved')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        self.git('cherry-pick', *shas)
        return trunk

    def write_landing_verdict(self, trunk, tip):
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {trunk[:9]}..{tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        return landing

    def units_with_reviews(self, count):
        heads = []
        for n in range(1, count + 1):
            b = self.brief(f'00{n}', f'u{n}', ['src/**', 'tests/**'])
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            heads.append(self.commit(f'u{n}'))
            self.done(b, heads[-1])
            (self.store / f'reviews/00{n}-u{n}.md').write_text('verdict: accept\n')
        return heads

    def write_landing(self, k, base, tip, status='clean'):
        landing = self.store / f'verdicts/landing-v{k}.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: {status}\nunits: landing\nrange: {base[:9]}..{tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        return landing

    def test_land_check_credits_a_unit_to_an_older_clean_landing_verdict_that_holds_it(
        self,
    ):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        h1, h2, h3, h4 = self.units_with_reviews(4)
        v1 = self.write_landing(1, init, h1)
        v2 = self.write_landing(2, h1, h3)
        out = self.land_check(2)
        self.assertIn(f'covered: unit 001 by the clean landing verdict {v1}\n', out)
        self.assertIn(f'covered: unit 002 by the clean landing verdict {v2}\n', out)
        self.assertIn(f'covered: unit 003 by the clean landing verdict {v2}\n', out)
        self.assertIn(
            f'land-check: note: unit 004 is newer than landing verdict {v2}; verify --landing again\n',
            out,
        )
        self.assertIn(
            f'unverified: unit 004 has no clean verdict (kitchen.sh verify {self.store} 004)\n',
            out,
        )
        self.assertNotIn('unverified: unit 001', out)
        self.assertIn(f'landing verification: clean ({v2})\n', out)

    def test_land_check_skips_an_older_landing_verdict_that_is_not_clean(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        h1, h2 = self.units_with_reviews(2)
        self.write_landing(1, init, h1, status='reject')
        v2 = self.write_landing(2, h1, h2)
        out = self.land_check(2)
        self.assertIn(f'covered: unit 002 by the clean landing verdict {v2}\n', out)
        self.assertIn('unverified: unit 001', out)

    def test_land_check_keeps_the_revert_check_inside_an_older_landing_verdict(self):
        init, heads = self.two_unit_stack()
        trunk = self.stack_on_a_new_trunk(init, heads)
        self.git('revert', '--no-edit', 'HEAD~1')
        reverting = self.git('rev-parse', 'HEAD')
        v1 = self.write_landing(1, trunk, reverting)
        self.write('docs/later.md', '# later\n')
        later = self.commit('later')
        self.write_landing(2, reverting, later)
        out = self.land_check(2)
        self.assertIn('covered: unit 002 (rebased as', out)
        self.assertIn(f'by the clean landing verdict {v1}\n', out)
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('covered: unit 001', out)

    def test_a_unit_whose_patch_was_later_reverted_in_the_stack_is_neither_covered_nor_accepted_by_verify(
        self,
    ):
        init, heads = self.two_unit_stack()
        self.stack_on_a_new_trunk(init, heads)
        trunk = self.git('rev-parse', 'HEAD~2')
        first = self.git('rev-parse', 'HEAD~1')
        self.git('revert', '--no-edit', first)
        reverting = self.git('rev-parse', 'HEAD')
        tip = reverting
        self.write_landing_verdict(trunk, tip)
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn('unverified: unit 001', out)
        self.assertIn(
            f"land-check: note: unit 001's commits match by patch-id but {reverting[:9]} reverts them",
            out,
        )
        self.assertNotIn('squashed', out)
        self.assertRegex(out, r'covered: unit 002 \(rebased as ')
        report = self.store / 'reports/002-next.md'
        report.write_text(f'# Report\n\nstatus: done\nhead: {tip}\n')
        out = self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '002',
            '--covers',
            '001',
            code=2,
        )
        self.assertIn(
            f"unit 001's commits match by patch-id but {reverting[:9]} reverts them",
            out,
        )

    def test_a_revert_is_found_when_a_later_commit_edited_lines_near_the_unit(self):
        """The unit adds a line after line 10 of a 30-line file; the next unit edits line 13, inside the revert's three lines of context; the
        stack then reverts the first unit. With default-context patch-ids the revert no longer matches the inverse and the unit looks covered."""
        self.write('src/long.py', ''.join(f'line {k}\n' for k in range(1, 31)))
        self.commit('a 30-line file')
        init = self.git('rev-parse', 'HEAD')
        heads = []
        for n, slug in ((1, 'util'), (2, 'next')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            self.dispatch(b)
            lines = [f'line {k}\n' for k in range(1, 31)]
            if n == 1:
                lines.insert(10, 'line 10 and a half\n')
            else:
                lines = (
                    [f'line {k}\n' for k in range(1, 11)]
                    + ['line 10 and a half\n']
                    + [
                        f'line {k}\n' if k != 13 else 'line 13 edited\n'
                        for k in range(11, 31)
                    ]
                )
            self.write('src/long.py', ''.join(lines))
            heads.append(self.commit(slug))
            self.done(b, heads[-1])
            (self.store / f'reviews/00{n}-{slug}.md').write_text('verdict: accept\n')
        trunk = self.stack_on_a_new_trunk(init, heads)
        replayed_first = self.git('rev-parse', 'HEAD~1')
        self.git('revert', '--no-edit', replayed_first)
        reverting = self.git('rev-parse', 'HEAD')
        self.write_landing_verdict(trunk, reverting)
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f"land-check: note: unit 001's commits match by patch-id but {reverting[:9]} reverts them",
            out,
        )
        self.assertNotIn('covered: unit 001', out)
        (self.store / 'reports/002-next.md').write_text(
            f'# Report\n\nstatus: done\nhead: {reverting}\n'
        )
        out = self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '002',
            '--covers',
            '001',
            code=2,
        )
        self.assertIn(
            f"unit 001's commits match by patch-id but {reverting[:9]} reverts them",
            out,
        )

    def test_a_rebased_unit_stays_covered_when_a_later_unit_edits_its_lines(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        heads = []
        for n, slug in ((1, 'util'), (2, 'next')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            self.dispatch(b)
            if n == 1:
                self.write(
                    'src/shared.py', ''.join(f'line {k}\n' for k in range(1, 13))
                )
            else:
                self.write(
                    'src/shared.py',
                    ''.join(
                        (
                            'line 5 edited\n'
                            if k == 5
                            else 'line 13 added\n'
                            if k == 13
                            else f'line {k}\n'
                        )
                        for k in range(1, 14)
                    ),
                )
            heads.append(self.commit(slug))
            self.done(b, heads[-1])
            (self.store / f'reviews/00{n}-{slug}.md').write_text('verdict: accept\n')
        trunk = self.stack_on_a_new_trunk(init, heads)
        tip = self.git('rev-parse', 'HEAD')
        landing = self.write_landing_verdict(trunk, tip)
        out = self.run_sh('land-check', str(self.store))
        self.assertRegex(
            out,
            rf'covered: unit 001 \(rebased as [0-9a-f]{{9}}\) by the clean landing verdict {landing}',
        )
        self.assertRegex(out, rf'covered: unit 002 \(rebased as {tip[:9]}\)')
        (self.store / 'reports/002-next.md').write_text(
            f'# Report\n\nstatus: done\nhead: {tip}\n'
        )
        out = self.run_sh(
            'verify', str(self.store), '--timeout', '60', '002', '--covers', '001'
        )
        self.assertIn("note: unit 001's head is not in", out)
        self.assertIn('its commits match by patch-id', out)

    def test_the_revert_of_one_commit_of_a_two_commit_unit_uncovers_the_unit(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/a.py', 'A = 1\n')
        first = self.commit('a')
        self.write('src/b.py', 'B = 1\n')
        second = self.commit('b')
        self.done(b, second)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        trunk = self.stack_on_a_new_trunk(init, [first, second])
        replayed_first = self.git('rev-parse', 'HEAD~1')
        self.git('revert', '--no-edit', replayed_first)
        reverting = self.git('rev-parse', 'HEAD')
        self.write_landing_verdict(trunk, reverting)
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f"land-check: note: unit 001's commits match by patch-id but {reverting[:9]} reverts them",
            out,
        )

    def test_a_unit_with_a_commit_and_its_revert_has_no_net_change_and_is_covered_by_its_patch_ids(
        self,
    ):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/a.py', 'A = 1\n')
        added = self.commit('a')
        self.git('revert', '--no-edit', added)
        reverted = self.git('rev-parse', 'HEAD')
        self.done(b, reverted)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        trunk = self.stack_on_a_new_trunk(init, [added, reverted])
        self.write_landing_verdict(trunk, self.git('rev-parse', 'HEAD'))
        out = self.run_sh('land-check', str(self.store))
        self.assertRegex(out, r'covered: unit 001 \(rebased as [0-9a-f]{9}\)')

    def unit_on_a_branch(self, n, base_ref):
        """Unit 00n, dispatched and done on a branch of its own off `base_ref`, with an accepted review: its head."""
        b = self.brief(f'00{n}', f'u{n}', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.git('checkout', '-q', '-b', f'ub{n}', base_ref)
        self.write(f'src/mod{n}.py', f'X = {n}\n')
        self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
        head = self.commit(f'u{n}')
        self.done(b, head)
        (self.store / f'reviews/00{n}-u{n}.md').write_text('verdict: accept\n')
        self.git('checkout', '-q', base_ref)
        return head

    def dangling_commit(self, message):
        """A commit no ref holds."""
        return self.git('commit-tree', 'HEAD^{tree}', '-m', message)

    def forget(self, *branches):
        """Drops the branches, the reflog and every unreachable object: what a rewrite and a gc leave of a unit's old commits."""
        for name in branches:
            self.git('branch', '-D', name)
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.git('gc', '-q', '--prune=now')

    def test_land_check_names_only_the_landing_verdicts_and_carriers_that_could_cover_the_unit(
        self,
    ):
        """Unit 002 is carried by 003 (built on it) and not by 004, the higher number; the landing verdict's packet lists 003 only."""
        init = self.git('rev-parse', 'HEAD')
        base = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        gone_head = self.unit_on_a_branch(1, base)
        self.unit_on_a_branch(2, base)
        self.unit_on_a_branch(3, 'ub2')
        self.git('checkout', '-q', base)
        self.unit_on_a_branch(4, base)
        gone_base = self.dangling_commit('old base')
        gone_landing = self.dangling_commit('old landing head')
        state = self.state()
        state['heads']['002-u2'] = gone_base
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.forget('ub1')
        store = self.store
        again = f'prove its Acceptance again with kitchen.sh verify {store}'
        restore = 'restore it from the bundle that carried it'
        nothing = (
            f"{restore}; no other accepted unit's head can be shown to carry its change; "
            f'prove its Acceptance again with a unit that does: kitchen.sh verify {store}'
        )
        no_verdict = lambda n: (
            f'unverified: unit {n} has no clean verdict (kitchen.sh verify {store} {n})\n'
        )
        cases = [
            (
                'no landing verdict: the head of a unit is gone and nothing can be shown to carry it',
                None,
                [
                    f'unverified: unit 001: its evidence is missing (unit head: {gone_head}); {nothing} <current-unit> --covers 001 --base <live-base>\n',
                    f'unverified: unit 002: its evidence is missing (dispatch base: {gone_base}); {restore}, or {again} 003 --covers 002 --base {init}\n',
                    no_verdict('003'),
                    no_verdict('004'),
                ],
                [],
            ),
            (
                'a landing verdict whose head is gone and whose packet lists unit 003 only',
                'Unit 003',
                [
                    f'unverified: unit 003: its evidence is missing (landing verdict head: {gone_landing}); {nothing} <current-unit> --covers 003 --base <live-base>\n',
                    no_verdict('004'),
                ],
                [
                    f'landing verdict head: {gone_landing}); {restore}, or {again} 003 --covers 004'
                ],
            ),
        ]
        for label, listed, expected, unexpected in cases:
            with self.subTest(label):
                if listed:
                    verdicts = store / 'verdicts'
                    verdicts.mkdir(exist_ok=True)
                    (verdicts / 'landing-v1.md').write_text(
                        f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init}..{gone_landing}\n\n## Evidence\n\n```\n$ true\n```\n'
                    )
                    (verdicts / 'landing-v1-packet.md').write_text(
                        f'# Verify landing\n\n## Goals\n\n### {listed} u3\n\nx\n'
                    )
                out = self.land_check(2)
                for line in expected:
                    self.assertIn(line, out)
                for text in unexpected:
                    self.assertNotIn(text, out)
                self.assertNotIn('land-check: pass', out)

    def test_the_recovery_command_land_check_prints_proves_the_lost_unit_again_and_clears_it(
        self,
    ):
        init = self.git('rev-parse', 'HEAD')
        base = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        self.unit_on_a_branch(1, base)
        self.unit_on_a_branch(2, 'ub1')
        self.git('checkout', '-q', base)
        gone_base = self.dangling_commit('old base')
        state = self.state()
        state['heads']['001-u1'] = gone_base
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.forget()
        out = self.land_check(2)
        [line] = [l for l in out.splitlines() if l.startswith('unverified: unit 001')]
        command = line.split('again with ')[1]
        self.assertEqual(
            command, f'kitchen.sh verify {self.store} 002 --covers 001 --base {init}'
        )
        self.assertIn(f'dispatch base: {gone_base}', line)
        proved = self.run_sh(*command.split()[1:])
        self.assertIn('status: clean', proved)
        cleared = self.land_check(0)
        self.assertIn('land-check: pass', cleared)
        self.assertNotIn('unverified', cleared)

    def land_check(self, code):
        """land-check on this store, expecting exit status `code`. Returns the output."""
        return self.run_sh('land-check', str(self.store), code=code)

    def revert_scan_world(self, before, unit_text, stack):
        """Unit 001 turns src/f.txt from `before` into `unit_text`; the stack, on a moved trunk, replays it and then applies each step of `stack`
        (a ('write', text) or ('revert', k) for the k-th stack commit). A clean landing verdict covers exactly that stack. Returns (unit sha, stack shas)."""
        self.write('src/f.txt', before)
        start = self.commit('f before the unit')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/f.txt', unit_text)
        unit = self.commit('the unit')
        self.done(b, unit)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        trunk = self.stack_on_a_new_trunk(start, [unit])
        made = [self.git('rev-parse', 'HEAD')]
        for kind, arg in stack:
            if kind == 'write':
                self.write('src/f.txt', arg)
                made.append(self.commit('stack edit'))
            else:
                self.git('revert', '--no-edit', made[arg])
                made.append(self.git('rev-parse', 'HEAD'))
        self.write_landing_verdict(trunk, made[-1])
        return unit, made

    def test_an_unrelated_identical_one_line_deletion_is_called_reverted_by_the_u0_scan(
        self,
    ):
        """Known limitation (tests/SPEC.md, Decided): the revert scan compares no-context patch-ids, which ignore line numbers. The unit adds a second `dup` line;
        the stack then deletes the first, unrelated `dup`. That commit's patch equals the inverse of the unit's, so land-check says the unit is reverted
        (the conservative, fail-closed answer), though its line is still there."""
        before = 'alpha\ndup\nbeta\ngamma\n'
        unit, made = self.revert_scan_world(
            before, before + 'dup\n', [('write', 'alpha\nbeta\ngamma\ndup\n')]
        )
        out = self.land_check(2)
        self.assertIn(
            f"land-check: note: unit 001's commits match by patch-id but {made[-1][:9]} reverts them",
            out,
        )
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('covered: unit 001', out)

    def test_a_real_git_revert_after_a_nearby_edit_is_called_reverted(self):
        lines = [f'line {k}\n' for k in range(1, 31)]
        unit_text = ''.join(lines[:10] + ['added\n'] + lines[10:])
        nearby = ''.join(
            lines[:10] + ['added\n'] + lines[10:12] + ['line 13 edited\n'] + lines[13:]
        )
        unit, made = self.revert_scan_world(
            ''.join(lines), unit_text, [('write', nearby), ('revert', 0)]
        )
        out = self.land_check(2)
        self.assertIn(
            f"land-check: note: unit 001's commits match by patch-id but {made[-1][:9]} reverts them",
            out,
        )
        self.assertNotIn('covered: unit 001', out)

    def test_a_block_moved_later_in_the_stack_leaves_the_unit_covered(self):
        """The unit adds three lines at the top; a later stack commit moves them to the bottom. That commit's patch is a deletion and an addition,
        which is not the inverse of the unit's patch, so the scan finds no revert and the unit stays covered."""
        before = ''.join(f'line {k}\n' for k in range(1, 11))
        block = 'm1\nm2\nm3\n'
        unit, made = self.revert_scan_world(
            before, block + before, [('write', before + block)]
        )
        out = self.land_check(0)
        self.assertRegex(
            out,
            r'covered: unit 001 \(rebased as [0-9a-f]{9}\) by the clean landing verdict',
        )
        self.assertNotIn('reverts them', out)

    def test_a_deletion_only_unit_reapplied_after_its_lines_were_restored_is_covered_by_the_newest_replay(
        self,
    ):
        """The unit deletes `gone`; the stack replays it, restores the line, and deletes it again with a commit of the same patch. The newest stack
        commit carrying the unit's patch-id is the last deletion, and nothing after it undoes the unit."""
        before = 'alpha\ngone\nbeta\n'
        after = 'alpha\nbeta\n'
        unit, made = self.revert_scan_world(
            before, after, [('write', before), ('write', after)]
        )
        out = self.land_check(0)
        self.assertRegex(
            out,
            rf'covered: unit 001 \(rebased as {made[-1][:9]}\) by the clean landing verdict',
        )

    def test_a_deletion_only_unit_whose_lines_were_restored_is_called_reverted(self):
        before = 'alpha\ngone\nbeta\n'
        unit, made = self.revert_scan_world(
            before, 'alpha\nbeta\n', [('write', before)]
        )
        out = self.land_check(2)
        self.assertIn(
            f"land-check: note: unit 001's commits match by patch-id but {made[-1][:9]} reverts them",
            out,
        )

    def test_a_unit_reverted_and_then_reapplied_is_covered_by_the_reapplication(self):
        """git revert, then the unit's own commit cherry-picked again: the newest stack commit with the unit's patch-id is the reapplication, and
        the revert before it does not count."""
        before = ''.join(f'line {k}\n' for k in range(1, 11))
        unit_text = before + 'added\n'
        unit, made = self.revert_scan_world(
            before, unit_text, [('revert', 0), ('write', unit_text)]
        )
        out = self.land_check(0)
        self.assertRegex(
            out,
            rf'covered: unit 001 \(rebased as {made[-1][:9]}\) by the clean landing verdict',
        )

    def test_verify_covers_a_rebased_unit_by_patch_id_and_says_so(self):
        init, heads = self.two_unit_stack()
        landing, tip = self.land_on_new_trunk(init, heads)
        trunk = self.git('rev-parse', 'HEAD~2')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        report = self.store / 'reports/002-next.md'
        report.write_text(f'# Report\n\nstatus: done\nhead: {tip}\n')
        out = self.run_sh(
            'verify', str(self.store), '--timeout', '60', '002', '--covers', '001'
        )
        self.assertRegex(
            out,
            rf"note: unit 001's head is not in {tip[:9]}; its commits match by patch-id \(rebased as [0-9a-f]{{9}}\)",
        )
        self.assertIn('status: clean', out)

    def verdict_then_a_newer_unit(self, base_text):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b1 = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b1)
        self.write('src/a.py', 'A = 1\n')
        h1 = self.commit('a')
        self.done(b1, h1)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {base_text or init[:9]}..{h1[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        b2 = self.brief('002', 'next', ['src/**', 'tests/**'])
        self.dispatch(b2)
        self.write('src/b.py', 'B = 1\n')
        self.done(b2, self.commit('b'))
        (self.store / 'reviews/002-next.md').write_text('verdict: accept\n')
        return landing

    def test_a_unit_committed_after_the_landing_verdict_is_newer_not_squashed(self):
        landing = self.verdict_then_a_newer_unit('')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f'land-check: note: unit 002 is newer than landing verdict {landing}; verify --landing again',
            out,
        )
        self.assertNotIn('squashed', out)

    def test_a_landing_verdict_whose_base_does_not_resolve_says_so_instead_of_squashed(
        self,
    ):
        landing = self.verdict_then_a_newer_unit('deadbeef0')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f'land-check: note: landing verdict {landing} has no base that resolves; unit 002 cannot be matched by patch-id',
            out,
        )
        self.assertNotIn('squashed', out)

    def test_a_unit_range_with_a_merge_is_not_covered_by_patch_id_and_the_note_says_why(
        self,
    ):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        main = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        start = self.git('rev-parse', 'HEAD')
        self.write('src/a.py', 'A = 1\n')
        first = self.commit('a')
        self.git('checkout', '-q', '-b', 'side', start)
        self.write('src/b.py', 'B = 1\n')
        second = self.commit('b')
        self.git('checkout', '-q', main)
        self.git('merge', '--no-ff', '-q', '-m', 'merge side', 'side')
        self.done(b, self.git('rev-parse', 'HEAD'))
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.git('checkout', '-q', '-b', 'landing', init)
        self.write('docs/trunk.md', '# trunk moved\n')
        trunk = self.commit('trunk moved')
        self.git('cherry-pick', first, second)
        tip = self.git('rev-parse', 'HEAD')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {trunk[:9]}..{tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            "land-check: note: unit 001's range holds a merge commit, so patch-ids do not cover it; verify --landing covers it",
            out,
        )
        self.assertNotIn('covered:', out)

    def test_the_stack_patch_id_map_is_built_once_per_land_check(self):
        init, heads = self.two_unit_stack()
        landing, tip = self.land_on_new_trunk(init, heads)
        trunk = self.git('rev-parse', 'HEAD~2')
        shim = Path(self.tmp.name) / 'gitlog'
        shim.mkdir()
        log = shim / 'calls'
        (shim / 'git').write_text(
            f'#!/bin/bash\necho "$*" >>{log}\nexec {shutil.which("git")} "$@"\n'
        )
        (shim / 'git').chmod(0o755)
        self.env['PATH'] = f'{shim}:{self.env["PATH"]}'
        out = self.run_sh('land-check', str(self.store))
        self.assertEqual(out.count('(rebased as'), 2)
        self.assertEqual(
            log.read_text().count(f'rev-list --reverse --no-merges {trunk}..{tip}'), 1
        )

    def test_land_check_notes_a_unit_squashed_into_the_stack_and_leaves_it_uncovered(
        self,
    ):
        init, heads = self.two_unit_stack()
        self.land_on_new_trunk(init, heads, squash=True)
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            "land-check: note: unit 001's head is not in the stack and its commits do not match by patch-id (squashed?); verify --landing covers it",
            out,
        )
        self.assertIn("land-check: note: unit 002's head is not in the stack", out)
        self.assertNotIn('covered:', out)
        self.assertIn('unverified: unit 001', out)

    def test_a_unit_whose_history_was_squashed_is_covered_by_a_landing_verdict_on_its_tree(
        self,
    ):
        init, heads = self.two_unit_stack()
        self.git('checkout', '-q', '-b', 'squashed', init)
        self.git('cherry-pick', '-n', *heads)
        tip = self.commit('the stack squashed in place')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f"covered: unit 002 (its head's tree is the landing head {tip[:9]}'s, rewritten) by the clean landing verdict {landing}",
            out,
        )
        self.assertIn('unverified: unit 001', out)

    def test_a_unit_whose_tree_only_an_earlier_commit_of_the_landing_range_has_stays_unverified(
        self,
    ):
        init, heads = self.two_unit_stack()
        self.git('checkout', '-q', '-b', 'squashed', init)
        self.git('cherry-pick', '-n', *heads)
        self.commit('the stack squashed in place')
        self.git('rm', '-q', 'src/mod2.py')
        tip = self.commit('unit 002 reverted')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertNotIn('covered: unit 002', out)
        self.assertIn('unverified: unit 002', out)

    def landing_verdict_then_merged(self, squash, merged=True):
        init, heads = self.two_unit_stack()
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        _, tip = self.land_on_new_trunk(init, heads, squash=squash)
        if merged:
            self.git('update-ref', 'refs/remotes/origin/main', tip)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{heads[1][:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store))
        if merged:
            self.assertNotIn('verify --landing again', out)
        return out, landing, heads[1]

    def test_a_landing_verdict_whose_stack_was_rebase_merged_says_landed_rebased(self):
        out, landing, head = self.landing_verdict_then_merged(squash=False)
        self.assertIn(
            f'landing verdict {landing} at {head[:9]}, landed (rebased): refs/remotes/origin/main carries its commits under other shas',
            out,
        )

    def test_a_landing_verdict_whose_stack_was_rebased_but_not_merged_asks_for_a_new_one(
        self,
    ):
        out, landing, head = self.landing_verdict_then_merged(
            squash=False, merged=False
        )
        self.assertNotIn('landed (', out)
        self.assertIn(
            f"landing verdict {landing} at {head[:9]}, not in HEAD's history", out
        )

    def test_a_landing_verdict_whose_stack_was_squash_merged_says_landed_squashed(self):
        out, landing, head = self.landing_verdict_then_merged(squash=True)
        self.assertIn(
            f'landing verdict {landing} at {head[:9]}, landed (squashed): refs/remotes/origin/main carries its commits under other shas',
            out,
        )

    def test_an_older_landing_verdict_says_how_many_commits_behind_head_it_is(self):
        init, head, landing = self.landing_range_world()
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{head[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        self.assertNotIn(
            'commits behind HEAD', self.run_sh('land-check', str(self.store))
        )
        self.write('docs/a.md', 'a\n')
        self.commit('a')
        self.assertIn(
            f'landing verdict {landing} at {head[:9]}, 1 commit behind HEAD',
            self.run_sh('land-check', str(self.store)),
        )
        self.write('docs/b.md', 'b\n')
        self.commit('b')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(
            f'landing verdict {landing} at {head[:9]}, 2 commits behind HEAD', out
        )

    @unittest.skipUnless(shutil.which('jj'), 'jj not on PATH')
    def test_verify_landing_takes_a_jj_revision_that_names_one_commit(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.colocate())
        self.assertIn(
            'kitchen.sh: --parent all() names ',
            self.run_sh(
                'verify', str(self.store), '--landing', '--parent', 'all()', code=1
            ),
        )
        out = self.run_sh('verify', str(self.store), '--landing', '--parent', 'root()+')
        self.assertIn(f'landing base: recorded parent root()+ ({init[:9]})', out)
        self.assertEqual(self.state()['landing']['parent'], init)

    def test_verify_landing_parent_is_recorded_and_used_by_the_next_landing(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        out = self.run_sh('verify', str(self.store), '--landing', '--parent', init[:9])
        self.assertIn(f'landing base: recorded parent {init[:9]} ({init[:9]})', out)
        self.assertEqual(self.state()['landing']['parent'], init)
        out = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn(f'landing base: recorded parent {init[:9]} ({init[:9]})', out)
        self.assertIn(
            f'range: {init}..{self.git("rev-parse", "HEAD")}',
            (self.store / 'verdicts/landing-v2-packet.md').read_text(),
        )
        self.assertIn(
            '--parent goes with --landing',
            self.run_sh('verify', str(self.store), '001', '--parent', 'HEAD', code=1),
        )

    def test_a_landing_verdict_off_heads_history_is_not_called_behind(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        main = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.git('checkout', '-q', '-b', 'side', init)
        self.write('src/other.py', 'Y = 1\n')
        side = self.commit('side work')
        self.git('checkout', '-q', main)
        self.run_sh(
            'verify', str(self.store), '--timeout', '60', '--landing', '--head', 'side'
        )
        landing = self.store / 'verdicts/landing-v1.md'
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{side[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store), code=0)
        self.assertIn(
            f"landing verdict {landing} at {side[:9]}, not in HEAD's history (rebased or another branch); verify --landing again",
            out,
        )
        self.assertNotIn('behind HEAD', out)

    def test_a_recorded_parent_that_is_no_longer_an_ancestor_falls_back_to_the_trunk_merge_base(
        self,
    ):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        unit = self.commit('util')
        self.done(b, unit)
        self.write('src/more.py', 'M = 1\n')
        self.commit('more')
        self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            unit[:9],
        )
        self.assertEqual(self.state()['landing']['parent'], unit)
        self.git('checkout', '-q', '-b', 'elsewhere', init)
        self.write('src/else.py', 'E = 1\n')
        head = self.commit('elsewhere')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        self.assertIn(
            f'landing base: recorded parent {unit[:9]} ({unit[:9]}) is no longer an ancestor of {head[:9]}; using the trunk merge-base',
            out,
        )
        self.assertIn(
            f'landing base: merge-base of remotes/origin/main and HEAD ({init[:9]})',
            out,
        )

    def test_parent_is_recorded_only_after_every_option_passes_and_none_clears_it(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.assertIn(
            '--head nope is not a commit',
            self.run_sh(
                'verify',
                str(self.store),
                '--timeout',
                '60',
                '--landing',
                '--parent',
                init[:9],
                '--head',
                'nope',
                code=1,
            ),
        )
        self.assertNotIn('landing', self.state())
        self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            init[:9],
        )
        self.assertEqual(self.state()['landing']['parent'], init)
        out = self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            'none',
        )
        self.assertIn('landing base: merge-base of remotes/origin/main and HEAD', out)
        self.assertNotIn('parent', self.state().get('landing', {}))

    def landing_world_for_parent_checks(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        head = self.commit('util')
        self.done(b, head)
        return init, head

    def test_base_with_a_parent_that_is_the_head_dies_and_records_nothing(self):
        init, head = self.landing_world_for_parent_checks()
        out = self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--base',
            init[:9],
            '--parent',
            head[:9],
            code=1,
        )
        self.assertIn(
            f'--parent {head[:9]} ({head[:9]}) must be an ancestor of {head[:9]} and not the same commit',
            out,
        )
        self.assertNotIn('landing', self.state())

    def test_a_parent_is_not_recorded_when_the_scratch_worktree_cannot_be_made(self):
        init, head = self.landing_world_for_parent_checks()
        blocked = (
            Path(self.env['XDG_STATE_HOME'])
            / 'pstack/scratch/runs-demo/000-landing-verify'
        )
        blocked.parent.mkdir(parents=True)
        blocked.write_text('a file where the worktree goes\n')
        out = self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            init[:9],
            code=2,
        )
        self.assertIn('git worktree add failed', out)
        self.assertNotIn('landing', self.state())
        blocked.unlink()
        self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            init[:9],
        )
        self.assertEqual(self.state()['landing']['parent'], init)

    def test_land_check_reports_a_stale_recorded_parent_on_stderr(self):
        init, head = self.landing_world_for_parent_checks()
        self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            head[:9],
            '--head',
            head,
            code=1,
        )
        self.write('src/more.py', 'M = 1\n')
        self.commit('more')
        self.run_sh(
            'verify',
            str(self.store),
            '--timeout',
            '60',
            '--landing',
            '--parent',
            head[:9],
        )
        self.assertEqual(self.state()['landing']['parent'], head)
        self.git('checkout', '-q', '-b', 'elsewhere', init)
        self.write('src/else.py', 'E = 1\n')
        elsewhere = self.commit('elsewhere')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(
            f'landing base: recorded parent {head[:9]} ({head[:9]}) is no longer an ancestor of {elsewhere[:9]}; using the trunk merge-base',
            out,
        )

    def test_verify_landing_head_proves_a_branch_beside_head(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        main = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.git('checkout', '-q', '-b', 'side', init)
        self.write('src/other.py', 'Y = 1\n')
        side = self.commit('side work')
        self.git('checkout', '-q', main)
        out = self.run_sh('verify', str(self.store), '--landing', '--head', 'side')
        self.assertIn('status: clean', out)
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn(f'range: {init}..{side}', packet)
        self.assertIn(f'# Verify landing: the stack at {side[:9]}', packet)
        self.assertIn(
            'is not a commit',
            self.run_sh(
                'verify', str(self.store), '--landing', '--head', 'nope', code=1
            ),
        )
        self.assertIn(
            '--head goes with --landing',
            self.run_sh('verify', str(self.store), '001', '--head', 'side', code=1),
        )

    def rebased_after_a_landing_verdict(self, trunk_file):
        init, heads = self.two_unit_stack()
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{heads[-1][:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        self.git('checkout', '-q', '-b', 'rebased', init)
        self.write(trunk_file, 'pytest==9\n')
        trunk = self.commit('trunk moved')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        self.git('cherry-pick', *heads)

    def test_land_check_warns_when_a_dependency_manifest_changed_since_the_verified_base(
        self,
    ):
        self.rebased_after_a_landing_verdict('requirements.txt')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(
            'warning: dependency manifests changed since the verified base: requirements.txt',
            out,
        )

    def verified_on_a_base_with_a_manifest_edit(self, packet=False):
        """The stack was verified on top of a commit that edits requirements.txt, then rebased onto a trunk without that edit."""
        init, heads = self.two_unit_stack()
        self.git('checkout', '-q', '-b', 'verified', init)
        self.write('requirements.txt', 'pytest==9\n')
        verified_base = self.commit('manifest edit')
        self.git('cherry-pick', *heads)
        verified_tip = self.git('rev-parse', 'HEAD')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        if packet:
            (self.store / 'verdicts/landing-v1-packet.md').write_text(
                f'# Verify landing\n\nrange: {verified_base}..{verified_tip}\n'
            )
            landing.write_text(
                '# Verdict\n\nstatus: clean\nunits: landing\nrange: copied wrongly\n\n## Evidence\n\n```\n$ true\n```\n'
            )
        else:
            landing.write_text(
                f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {verified_base[:9]}..{verified_tip[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
            )
        self.git('checkout', '-q', '-b', 'rebased', init)
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.git('cherry-pick', *heads)

    def test_a_manifest_changed_only_on_the_verified_bases_side_still_warns(self):
        self.verified_on_a_base_with_a_manifest_edit()
        out = self.run_sh('land-check', str(self.store), code=0)
        self.assertIn(
            'warning: dependency manifests changed since the verified base: requirements.txt',
            out,
        )

    def test_a_manifest_renamed_on_the_current_bases_side_warns_through_its_old_path(
        self,
    ):
        init, heads = self.two_unit_stack()
        self.git('checkout', '-q', '-b', 'verified', init)
        self.write('requirements.txt', 'pytest==9\n')
        verified_base = self.commit('manifest added')
        self.git('cherry-pick', *heads)
        tip = self.git('rev-parse', 'HEAD')
        self.write_landing_verdict(verified_base, tip)
        self.git('checkout', '-q', '-b', 'renamed', verified_base)
        self.git('mv', 'requirements.txt', 'deps.lock')
        trunk = self.commit('manifest renamed')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        self.git('cherry-pick', *heads)
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(
            'warning: dependency manifests changed since the verified base: requirements.txt',
            out,
        )

    def test_the_manifest_warning_and_coverage_read_one_base_and_head_from_the_verdicts_packet(
        self,
    ):
        """The verdict's own range line is garbled (the verifier copied it wrongly); the packet's is the kitchen's. Coverage and the warning
        both use the packet's, so land-check passes and warns."""
        self.verified_on_a_base_with_a_manifest_edit(packet=True)
        out = self.run_sh('land-check', str(self.store), code=0)
        self.assertIn(
            'warning: dependency manifests changed since the verified base: requirements.txt',
            out,
        )
        self.assertRegex(out, r'covered: unit 001 \(rebased as ')

    def test_a_verdict_verified_on_a_base_inside_the_stack_does_not_warn_of_the_stacks_own_manifest_edits(
        self,
    ):
        init, heads = self.two_unit_stack()
        self.git('checkout', '-q', '-b', 'stack', init)
        self.write('requirements.txt', 'pytest==9\n')
        lower = self.commit('lower unit edits the manifest')
        self.write('src/upper.py', 'U = 1\n')
        upper = self.commit('upper unit')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {lower[:9]}..{upper[:9]}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        # The two units' own commits are not on this branch, so land-check refuses them; only the warning is under test.
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertNotIn('dependency manifests', out)

    def test_verify_landing_warns_of_manifests_changed_since_the_previous_landing_verdicts_base(
        self,
    ):
        self.verified_on_a_base_with_a_manifest_edit()
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        self.assertIn(
            'warning: dependency manifests changed since the verified base: requirements.txt',
            out,
        )

    def test_land_check_says_nothing_of_manifests_when_trunk_moved_another_file(self):
        self.rebased_after_a_landing_verdict('docs/notes.md')
        self.assertNotIn(
            'dependency manifests', self.run_sh('land-check', str(self.store))
        )

    def test_a_clean_landing_verdict_whose_range_head_does_not_resolve_says_so_and_covers_nothing(
        self,
    ):
        init, head, landing = self.landing_range_world()
        landing.write_text(
            f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..deadbeef0\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(
            f'land-check: note: landing verdict {landing} range head does not resolve; no unit is covered by it',
            out,
        )
        self.assertNotIn('covered:', out)
        self.assertIn('unverified: unit 001', out)

    def test_rejected_verdict_drafts_the_fix_brief(self):
        _, out = self.verify_app_unit('reject', 2)
        self.assertIn('Acceptance broke', out)
        verdict = self.store / 'verdicts/001-util-v1.md'
        self.assertIn(
            f'next: kitchen.sh revise {self.store} {verdict}, then verify the fix with --covers 001',
            out,
        )
        fix = Path(self.run_sh('revise', str(self.store), str(verdict)).strip())
        text = fix.read_text()
        self.assertEqual(fix.name, '002-util-fix.md')
        self.assertIn(f'- verdict: {verdict}', text)
        self.assertIn('- src/** — scope', text)
        self.assertIn('playbook: review-fix', text)

    def rejected(self, name, units):
        verdict = self.store / f'verdicts/{name}.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text(
            f'# Verdict\n\nstatus: reject\nunits: {units}\n\n## Findings\n\n1. flaky\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        return verdict

    def test_a_rejected_landing_verdict_drafts_one_fix_over_the_named_units_scopes(
        self,
    ):
        self.brief('001', 'util', ['src/**'])
        self.brief('002', 'guide', ['docs/**'])
        verdict = self.rejected('landing-v3', 'landing (001 002)')
        fix = Path(self.run_sh('revise', str(self.store), str(verdict)).strip())
        self.assertEqual(fix.name, '003-landing-fix.md')
        text = fix.read_text()
        self.assertIn('may write:\n- src/** — scope\n- docs/** — scope\n', text)
        self.assertIn(f'`kitchen.sh verify {self.store} --landing`', text)
        self.assertIn('plan: none\n', text)
        self.assertIn('unit brief: ' + str(self.store / 'briefs/002-guide.md'), text)

    def landing_stack_of_three(self):
        """Units 001 to 003 each commit one file; returns the head of 001 and of 003, so base..head holds 002 and 003 only."""
        heads = []
        for n, slug in ((1, 'one'), (2, 'two'), (3, 'three')):
            b = self.brief(
                f'00{n}',
                slug,
                [f'src/mod{n}.py'],
                acceptance=[f'- unit {n} acceptance'],
            )
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            heads.append(self.commit(slug))
            self.done(b, heads[-1])
        return heads[0], heads[2]

    def rejected_landing(self, name, base, head, findings):
        verdict = self.store / f'verdicts/{name}.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text(
            f'# Verdict\n\nstatus: reject\nunits: landing\nrange: {base[:9]}..{head[:9]}\n\n## Findings\n\n{findings}\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        return verdict

    def landing_fix_text(self, verdict):
        return Path(
            self.run_sh('revise', str(self.store), str(verdict)).strip()
        ).read_text()

    def test_a_landing_fix_covers_the_units_in_the_verdicts_range_and_no_others(self):
        base, head = self.landing_stack_of_three()
        verdict = self.rejected_landing('landing-v1', base, head, '1. flaky')
        text = self.landing_fix_text(verdict)
        self.assertIn('unit brief: ' + str(self.store / 'briefs/002-two.md'), text)
        self.assertIn('unit brief: ' + str(self.store / 'briefs/003-three.md'), text)
        self.assertNotIn('001-one.md', text)
        self.assertIn('- src/mod2.py — scope', text)
        self.assertIn('- src/mod3.py — scope', text)
        self.assertNotIn('src/mod1.py', text)

    def test_a_landing_fix_whose_range_holds_no_unit_still_names_the_verdict(self):
        base, head = self.landing_stack_of_three()
        verdict = self.rejected_landing('landing-v1', head, head, '1. flaky')
        text = self.landing_fix_text(verdict)
        self.assertIn(f'- verdict: {verdict}', text)
        self.assertNotIn('unit brief:', text)

    def test_a_landing_fix_leaves_out_a_forbidden_path_another_unit_may_write(self):
        base, head = self.landing_stack_of_three()
        for seq, slug, _may, forbidden in (
            ('002', 'two', 'src/mod2.py', ['- src/mod3.py', '- src/other/**']),
            ('003', 'three', 'src/mod3.py', ['- src/mod9.py']),
        ):
            brief = self.store / f'briefs/{seq}-{slug}.md'
            text = brief.read_text()
            brief.write_text(
                text.replace(
                    'must not write:\n- x',
                    'must not write:\n' + '\n'.join(forbidden),
                )
            )
        text = self.landing_fix_text(
            self.rejected_landing('landing-v1', base, head, '1. flaky')
        )
        scope = text.split('## Scope', 1)[1].split('\n## ', 1)[0]
        forbidden = scope.split('must not write:', 1)[1]
        self.assertNotIn('src/mod3.py', forbidden)
        self.assertIn('- src/other/**', forbidden)
        self.assertIn('- src/mod9.py', forbidden)

    def test_a_landing_fix_acceptance_is_one_line_per_finding_and_one_still_holds(
        self,
    ):
        base, head = self.landing_stack_of_three()
        verdict = self.rejected_landing(
            'landing-v1',
            base,
            head,
            '1. The first thing broke. It broke twice.\n2. The second thing broke.',
        )
        text = self.landing_fix_text(verdict)
        accept = text.split('## Acceptance\n', 1)[1].split('\n## ', 1)[0].strip()
        self.assertEqual(
            accept.splitlines(),
            [
                f'- Finding 1 of {verdict} no longer holds: The first thing broke.',
                f'- Finding 2 of {verdict} no longer holds: The second thing broke.',
                '- Every Acceptance line of the unit briefs in Context still holds at the new head.',
            ],
        )

    def test_dispatch_refuses_a_landing_fix_until_its_verify_is_filled(self):
        base, head = self.landing_stack_of_three()
        fix = Path(
            self.run_sh(
                'revise',
                str(self.store),
                str(self.rejected_landing('landing-v1', base, head, '1. flaky')),
            ).strip()
        )
        self.assertIn('unfilled placeholders', self.dispatch(fix, code=1))

    FENCED_NOTES = (
        '## Notes\n\n````md\n## Acceptance\n\n- FAKE acceptance\n\n'
        '## Verify\n\n```bash\nFAKE verify\n```\n````\n\n'
    )

    def brief_with_fenced_headings(self, seq, slug):
        b = self.brief(
            seq, slug, ['src/**', 'tests/**'], acceptance=['- REAL acceptance']
        )
        b.write_text(
            b.read_text().replace('## Scope', self.FENCED_NOTES + '## Scope', 1)
        )
        return b

    def test_a_fenced_heading_in_a_brief_does_not_reach_the_packet(self):
        b = self.brief_with_fenced_headings('001', 'util')
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('clean')
        self.run_sh('verify', str(self.store), '001')
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertIn('- REAL acceptance', packet)
        self.assertNotIn('FAKE acceptance', packet)

    def test_a_fenced_heading_in_a_brief_does_not_reach_the_fix_brief(self):
        self.brief_with_fenced_headings('001', 'util')
        verdict = self.rejected('001-util-v1', '001')
        text = Path(
            self.run_sh('revise', str(self.store), str(verdict)).strip()
        ).read_text()
        self.assertIn('- REAL acceptance', text)
        self.assertNotIn('FAKE', text)

    def test_revise_reads_the_last_unit_number_whatever_surrounds_it(self):
        self.brief('001', 'util', ['src/**'])
        verdict = self.rejected('001-util-v1', '(001)')
        self.assertTrue(
            self.run_sh('revise', str(self.store), str(verdict))
            .strip()
            .endswith('002-util-fix.md')
        )

    def test_a_verdict_without_evidence_is_invalid(self):
        _, out = self.verify_app_unit('noevidence', 2)
        self.assertIn('status: invalid (no command output under Evidence)', out)

    def test_land_check_needs_a_verdict_for_every_accepted_unit_and_runs_no_reviewer(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        self.done(b, self.commit('util'))
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.assertIn(
            'unverified: unit 001', self.run_sh('land-check', str(self.store), code=2)
        )
        landing = self.brief('002', 'land', ['src/**'], playbook='opening-a-pr')
        self.assertIn('landing refused', self.dispatch(landing, code=2))
        self.run_sh('verify', str(self.store), '001')
        self.assertIn(
            'land-check: pass (review engine: none)\n',
            self.run_sh('land-check', str(self.store)),
        )
        self.run_sh('review', str(self.store), '001', code=1)
        self.assertFalse((self.store / 'joo').exists())

    def test_retro_and_counters_agree_on_verified_units(self):
        for n, status, units in [
            ('001-a-v1', 'clean', '001 002'),
            ('003-c-v1', 'reject', '003'),
            ('003-c-v2', 'clean', '003'),
        ]:
            (self.store / 'verdicts' / f'{n}.md').write_text(
                f'# V\n\nstatus: {status}\nunits: {units}\n'
            )
        out = self.run_sh('retro', str(self.store))
        self.assertIn(
            'run demo: 0 unit reports, 3 verified units (1 clean verdicts on the first try)',
            out,
        )
        counters = json.loads(
            subprocess.run(
                [sys.executable, str(script), 'counters', str(self.store)],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout
        )
        self.assertEqual(counters['verified_units'], 3)

    def test_retro_counts_the_run_and_repeats_across_runs(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('FAIL', 'x\n')
        self.write('src/util.py', 'X = 7\n')
        sha = self.commit('broken')
        self.run_sh('step', str(self.store), sha, 'one', code=2)
        self.run_sh('step', str(self.store), sha, 'one', code=2)
        self.run_sh('catch', str(self.store), 'review', 'missed a caller in api.py')
        out = self.run_sh('retro', str(self.store))
        self.assertIn('run demo: 0 unit reports', out)
        self.assertRegex(out, r'2 gate-fail\s+app\s+in 1 runs')
        out = self.run_sh('retro', str(self.store))
        self.assertRegex(out, r'2 gate-fail\s+app\s+in 1 runs')

    def ledger(self):
        path = next(
            (Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob(
                '*/ledger.tsv'
            )
        )
        lines = path.read_text().splitlines()
        return lines[0].split('\t'), [
            dict(zip(lines[0].split('\t'), ln.split('\t'), strict=True))
            for ln in lines[1:]
        ]

    def rewrite_ledger(self, edit):
        path = next(
            (Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob(
                '*/ledger.tsv'
            )
        )
        head, rows = self.ledger()
        path.write_text(
            '\n'.join(
                ['\t'.join(head)] + ['\t'.join(r[k] for k in head) for r in edit(rows)]
            )
            + '\n'
        )

    def failing_step(self, text):
        self.write('FAIL', 'x\n')
        self.write('src/util.py', text)
        sha = self.commit('broken')
        self.run_sh('step', str(self.store), sha, 'one', code=2)

    def test_a_retro_rerun_keeps_the_time_of_rows_it_wrote_before(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.failing_step('X = 7\n')
        self.run_sh('retro', str(self.store))
        old = '2026-01-01T00:00:00Z'
        self.rewrite_ledger(lambda rows: [dict(r, ts=old) for r in rows])
        count = len(self.ledger()[1])
        self.assertGreater(count, 0)
        self.run_sh('retro', str(self.store))
        rows = self.ledger()[1]
        self.assertEqual(len(rows), count)
        self.assertEqual({r['ts'] for r in rows}, {old})

    def test_a_dismissed_candidate_stays_hidden_after_a_retro_rerun(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.failing_step('X = 7\n')
        self.run_sh('retro', str(self.store))
        old, between = '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z'

        def three_runs(rows):
            fails = [r for r in rows if r['kind'] == 'gate-fail']
            more = [dict(r, run=f'other-{n}') for n in (1, 2) for r in fails]
            return [dict(r, ts=old) for r in rows + more]

        self.rewrite_ledger(three_runs)
        fail = next(r for r in self.ledger()[1] if r['kind'] == 'gate-fail')
        listed = f'gate-fail {fail["class"]} {fail["area"]}'
        args = [fail['class'], fail['area'], '--match', fail['match']]
        self.assertIn(listed, self.run_sh('retro', str(self.store)))
        subprocess.run(
            [
                sys.executable,
                str(script),
                '--repo',
                str(self.repo),
                'lessons',
                'dismiss',
                'gate-fail',
                *args,
                '--note',
                'mixed',
            ],
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        self.rewrite_ledger(
            lambda rows: [
                dict(r, ts=between) if r['kind'] == 'dismiss' else r for r in rows
            ]
        )
        self.assertNotIn(listed, self.run_sh('retro', str(self.store)))
        self.failing_step('X = 8\n')
        self.assertIn(listed, self.run_sh('retro', str(self.store)))

    def test_the_ledger_says_where_and_what(self):
        b = self.brief('001', 'util', ['src/lib/**', 'tests/**'])
        self.dispatch(b)
        self.write('FAIL', 'x\n')
        self.write('src/lib/util.py', 'X = 7\n')
        sha = self.commit('broken')
        self.run_sh('step', str(self.store), sha, 'one', code=2)
        (self.store / 'verdicts/001-util-v1.md').write_text(
            '# Verdict\n\nstatus: reject\nunits: 001\n\n## Findings\n\n1. Acceptance 2: the\tcache is never cleared\n'
        )
        (self.store / 'steers').mkdir(exist_ok=True)
        (self.store / 'steers/001-util-s1.md').write_text(
            '# Steer\n\nkind: narrow\n\n## Direction\n\nLeave the CLI alone; only the library changes.\n'
        )
        self.run_sh(
            'catch', str(self.store), 'review', 'missed a caller in api.py', '001'
        )
        self.run_sh('catch', str(self.store), 'review', 'unit not given')
        self.run_sh('catch', str(self.store), 'review', 'bad unit', 'util', code=1)
        self.assertIn(
            'no brief for unit 009',
            self.run_sh('catch', str(self.store), 'review', 'x', '009', code=1),
        )
        self.run_sh('catch', str(self.store), 'gate', '--force push slipped through')
        self.run_sh('retro', str(self.store))
        head, rows = self.ledger()
        self.assertEqual(
            head,
            [
                'ts',
                'run',
                'kind',
                'class',
                'detail',
                'unit',
                'area',
                'finding',
                'match',
            ],
        )
        by = {r['kind']: r for r in rows}
        # One row per failing check: the command (no task runner names one), then the policy rule.
        self.assertEqual(
            [
                (r['unit'], r['area'], r['class'], r['match'], r['finding'])
                for r in rows
                if r['kind'] == 'gate-fail'
            ],
            [
                ('001', 'src/lib', 'app', 'fast test ! -e FAIL', 'test ! -e FAIL'),
                (
                    '001',
                    'src/lib',
                    'policy',
                    'test-touch',
                    'src/lib/util.py: [test-touch] profile app requires tests: '
                    '1 source files changed and no file matching tests/** (.agents/kitchen.toml)',
                ),
            ],
        )
        self.assertEqual(
            (by['reject']['area'], by['reject']['finding']),
            ('src/lib', 'Acceptance 2: the cache is never cleared'),
        )
        self.assertEqual(
            (by['steer']['finding'], by['steer']['match']),
            ('narrow: Leave the CLI alone; only the library changes.', 'narrow'),
        )
        catches = [r for r in rows if r['kind'] == 'catch']
        self.assertEqual(
            [(c['area'], c['finding']) for c in catches],
            [
                ('src/lib', 'missed a caller in api.py'),
                ('', 'unit not given'),
                ('', '--force push slipped through'),
            ],
        )
        # Retro again: the run's rows are replaced, the catches kept.
        out = self.run_sh('retro', str(self.store))
        self.assertEqual(len(self.ledger()[1]), len(rows))
        self.assertIn('1 runs (demo): catch review src/lib', out)

    def test_an_old_ledger_gains_the_new_columns(self):
        state = Path(
            subprocess.run(
                [sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
        )
        (state / 'ledger.tsv').write_text(
            'ts\trun\tkind\tclass\tdetail\n2026-01-01T00:00:00Z\told\tsteer\tbrief\t004-x-s1\n'
        )
        self.run_sh('catch', str(self.store), 'gate', 'lint missed it')
        head, rows = self.ledger()
        self.assertEqual(len(head), 9)
        self.assertEqual(
            [(r['run'], r['kind'], r['area']) for r in rows],
            [('old', 'steer', ''), ('demo', 'catch', '')],
        )


class KitchenRexTests(KitchenScriptTests):
    """Verification on the Rex backend against fake rex: the verifier starts in a block of its own, takes its packet, and its verdict
    returns. The rest of KitchenScriptTests reads fake herdr's state and is left to the Herdr run."""

    KEEP = (
        'test_a_blocked_unit_can_still_be_verified',
        'test_a_unit_dispatched_as_escalated_is_verified_by_the_escalated_class',
        'test_landing_verification_starts_where_the_stack_leaves_trunk',
    )

    def mux_env(self):
        master = self.fake / 'rex/blocks/block_m0'
        master.mkdir(parents=True)
        (master / 'fg').write_text('200 claude\n')
        (master / 'state').write_text('working claude-code t0\n')
        return {
            'REX_BLOCK': 'block:m0',
            'REX_SESSION': 'session:s1',
            'PAIR_REX_POLL_S': '0',
            'PAIR_REX_STALL_S': '1',
        }


assert set(KitchenRexTests.KEEP) <= set(dir(KitchenScriptTests)), (
    'a KitchenRexTests.KEEP name is no KitchenScriptTests test'
)
for _name in dir(KitchenScriptTests):
    if _name.startswith('test_') and _name not in KitchenRexTests.KEEP:
        setattr(KitchenRexTests, _name, None)


class SubagentKitchenTests(KitchenScriptTests):
    """The kitchen with no multiplexer: every role is a subagent of the master's session. Each prompt is a `subagent NAME (KIND): TEXT` line
    for the master to hand to its host's subagent tool, nothing waits, and the files the subagents write carry the run."""

    def mux_env(self):
        return {}

    def spawn_roles(self):
        out = self.run_sh('spawn', str(self.store), code=4)
        self.assertIn('subagent demo-sidekick (pi): Load the pstack-kitchen skill', out)
        self.spawn_out = out
        # The sidekick subagent bootstraps.
        (self.store / 'reports/000-ready.md').write_text('status: done\n')

    def test_init_needs_no_multiplexer_and_names_the_master_in_the_session(self):
        self.assertIn(
            'next: run the subagent the line above names, with its prompt; it writes the ready file',
            self.spawn_out,
        )
        doc = self.state()
        self.assertTrue(doc['master']['pane_id'].startswith('subagent:master:'))
        self.assertEqual(doc['sidekick']['pane_id'], 'subagent:1')

    def test_a_dispatch_hands_the_brief_to_the_sidekick_subagent_and_returns(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.dispatch(b)
        self.assertIn(f'subagent demo-sidekick (pi): pstack-kitchen BRIEF {b}', out)
        inbox = self.store.parents[3] / 'pstack/subagents/inbox/demo-sidekick.txt'
        self.assertIn(f'pstack-kitchen BRIEF {b}', inbox.read_text())
        # The sidekick subagent does the unit and reports; the master's wait returns the report.
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        self.done(b, self.commit('util'))
        report = self.store / 'reports' / b.name
        out = self.run_sh('finish', str(self.store), str(report))
        self.assertIn(f'master: pstack-kitchen REPORT {report}', out)
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn(f'the reply to {b.name} was already shown', out)
        # The finished unit asks for a fresh session: the role ends and the next prompt goes to a new subagent.
        out = self.run_sh('rotate', str(self.store), code=4)
        self.assertIn('subagent demo-sidekick (pi): Load the pstack-kitchen skill', out)
        self.assertIn('generation 2', out)

    def test_rotate_master_writes_the_next_sessions_prompt_and_take_master_there_takes_the_run_over(
        self,
    ):
        self.filled_handoff()
        before = self.state()['master']
        out = self.run_sh('rotate-master', str(self.store))
        prompt = self.store / 'sessions/master-g2-prompt.md'
        self.assertIn(
            'handoff recorded: '
            + str(self.store / 'sessions/master-1-handoff.md')
            + ' (generation 1)',
            out,
        )
        self.assertIn(f'next master prompt: {prompt}', out)
        self.assertIn(
            'next: end this session with no further commands; the human opens a new coding-agent session in ',
            out,
        )
        self.assertIn(
            f'outside any multiplexer as this one, and gives it the prompt in {prompt}: generation 2 owns the run',
            out,
        )
        self.assertIn(
            f'pstack-kitchen MASTER {self.store} (generation 2). Run ~/.agents/skills/pstack-kitchen/scripts/kitchen.sh '
            f'take-master {self.store} first',
            prompt.read_text(),
        )
        state = self.state()['master']
        self.assertEqual(
            (state['name'], state['pane_id'], state['generation']),
            (before['name'], before['pane_id'], 2),
        )
        # A second rotation before anyone took over names the prompt to start it with.
        out = self.run_sh('rotate-master', str(self.store), code=5)
        self.assertIn(
            f'a hand-over is open: generation 2 has not taken over yet; start it with {self.store}/sessions/master-g2-prompt.md, '
            f'or run kitchen.sh take-master {self.store} here to keep going',
            out,
        )
        # The next session, in the same checkout, takes over in the same place and keeps the master's name.
        out = self.run_sh('take-master', str(self.store))
        self.assertIn(
            f'took over from {before["name"]} in the same place ({before["pane_id"]})',
            out,
        )
        self.assertIn(
            f'master: {before["name"]} in {before["pane_id"]} (generation 2)', out
        )
        state = self.state()['master']
        self.assertNotIn('previous', state)
        registry = json.loads(
            (self.store.parents[3] / 'pstack/subagents/registry.json').read_text()
        )
        self.assertEqual(registry['agents'][before['name']]['pane'], before['pane_id'])

    def test_a_wrapper_of_one_command_is_not_a_new_session(self):
        self.filled_handoff()
        old = self.state()['master']
        self.run_sh('rotate-master', str(self.store))
        result = subprocess.run(
            ['env', 'timeout', '60', str(self.script), 'take-master', str(self.store)],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'in the same place ({old["pane_id"]})', result.stdout)

    def test_take_master_from_a_session_in_another_checkout_does_not_move_the_run(
        self,
    ):
        self.filled_handoff()
        old = self.state()['master']
        self.run_sh('rotate-master', str(self.store))
        with tempfile.TemporaryDirectory() as elsewhere:
            result = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    'import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))',
                    str(self.script),
                    'take-master',
                    str(self.store),
                ],
                cwd=elsewhere,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn(
            f"take-master runs from the new master's pane {old['pane_id']}, not ",
            result.stderr,
        )
        self.assertEqual(self.state()['master']['pane_id'], old['pane_id'])

    def test_take_master_in_a_new_session_takes_the_run_into_that_sessions_pane(
        self,
    ):
        self.filled_handoff()
        old = self.state()['master']
        self.run_sh('rotate-master', str(self.store))
        # A process between the test and the command stands for the new coding-agent session.
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))',
                str(self.script),
                'take-master',
                str(self.store),
            ],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new = self.state()['master']
        self.assertNotEqual(new['pane_id'], old['pane_id'])
        self.assertTrue(new['pane_id'].startswith(old['pane_id'].rsplit(':', 1)[0]))
        self.assertIn(
            f'took over from {old["name"]}, whose session was {old["pane_id"]}',
            result.stdout,
        )
        registry = json.loads(
            (self.store.parents[3] / 'pstack/subagents/registry.json').read_text()
        )
        self.assertEqual(registry['agents'][old['name']]['pane'], new['pane_id'])

    def test_verify_hands_the_packet_to_a_verifier_subagent_then_reads_its_verdict(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        out = self.run_sh('verify', str(self.store), '001', code=4)
        self.assertIn('subagent demo-verifier', out)
        packet = self.store / 'verdicts/001-util-v1-packet.md'
        self.assertIn(f'VERIFY {packet}', out)
        verdict = self.store / 'verdicts/001-util-v1.md'
        verdict.write_text(
            '# Verdict\n\nstatus: clean\nunits: 001\n\n## Findings\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        out = self.run_sh('verify', str(self.store), '--wait', code=0)
        self.assertIn('status: clean', out)


for _name in dir(KitchenScriptTests):
    if _name.startswith('test_') and _name not in vars(SubagentKitchenTests):
        setattr(SubagentKitchenTests, _name, None)


AMP_MASTER = 'T-aaaaaaaa-0000-4000-8000-000000000000'


def amp_thread(n):
    return f'T-00000000-0000-4000-8000-{n:012d}'


class AmpKitchenTests(KitchenScriptTests):
    """The kitchen with PSTACK_MUX=amp: every role is an Amp thread of its own, started on the model its arguments name, and every prompt
    carries a bundle of the store and its commits that the receiving thread syncs first. Nothing waits; a role's reply is a message to the
    master's thread."""

    def mux_env(self):
        # A dispatch to a working sidekick that never took a brief of its generation waits out its READY turn; no test inherits that wait.
        return {
            'PSTACK_MUX': 'amp',
            'AMP_THREAD_ID': AMP_MASTER,
            'PAIR_ROTATE_READY_S': '0',
        }

    def spawn_roles(self):
        out = self.run_sh(
            'spawn',
            str(self.store),
            '--kind',
            'amp',
            '--',
            '--model',
            'zhipuai/glm-5.3',
            '--orb-size',
            'a1.small',
            code=4,
        )
        self.spawn_out = out
        self.ready_path().write_text('status: done\n')

    def ready_path(self, store=None, role='sidekick'):
        """Where the role of generation pair.json holds writes READY: reports/000-ready.md (advice/ for the consultant), under git with its generation."""
        store = Path(store or self.store)
        directory = 'advice' if role == 'consultant' else 'reports'
        suffix = ''
        if self.env.get('PSTACK_STORE') == 'git':
            generation = json.loads((store / 'pair.json').read_text())[role][
                'generation'
            ]
            suffix = f'-g{generation}'
        return store / directory / f'000-ready{suffix}.md'

    def thread_messages(self, thread):
        d = self.fake / 'amp-threads' / thread
        return [
            p.read_text() for p in sorted(d.glob('*.txt'), key=lambda p: int(p.stem))
        ]

    def as_thread(self, thread, *args, code=0, mux='', env=None, cwd=None):
        """kitchen.sh run from another Amp thread (a role's, or the next master's), here on the same machine; mux is the PSTACK_MUX its orb
        sets, if any, and env what else that orb's environment holds."""
        env = {**self.env, **(env or {}), 'AMP_THREAD_ID': thread}
        env.pop('PSTACK_MUX')
        if mux:
            env['PSTACK_MUX'] = mux
        result = subprocess.run(
            [str(self.script), *args],
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_spawn_starts_the_sidekick_as_a_thread_on_its_model_with_the_store_attached(
        self,
    ):
        sidekick = amp_thread(1)
        self.assertIn(
            f'amp demo-sidekick ({sidekick}): Load the pstack-kitchen skill',
            self.spawn_out,
        )
        self.assertIn(
            "next: end your turn; the role's thread writes the ready file",
            self.spawn_out,
        )
        log = (self.fake / 'amp.log').read_text()
        # The orb size the role's arguments name goes to amp -ox with its model.
        self.assertIn(
            '--model zhipuai/glm-5.3 --orb-size a1.small --title demo-sidekick --attach ',
            log,
        )
        [message] = self.thread_messages(sidekick)
        self.assertIn(f'store: {self.store}', message)
        self.assertIn(
            'then `PSTACK_MUX=amp ~/.agents/skills/pstack-pair/scripts/pair.sh sync <the pstack-demo-sidekick.tgz attachment',
            message,
        )
        self.assertEqual(self.state()['sidekick']['pane_id'], 'amp:1')
        # The thread's orb may hold no pstack, or another build: the prompt carries this one and says how to install it first.
        self.assertIn('run `d=~/.local/share/pstack-builds/', message)
        self.assertIn(
            'python3 $d/scripts/pstack-sync.py --source $d/skills --apply', message
        )
        self.assertIn(
            'reaches it only when you send it: `PSTACK_MUX=amp kitchen.sh finish` or `PSTACK_MUX=amp kitchen.sh notify <store> <file>`',
            message,
        )
        with tarfile.open(
            self.fake / 'amp-threads' / sidekick / '1-pstack-build.tgz'
        ) as tar:
            names = set(tar.getnames())
        self.assertLessEqual(
            {
                'scripts/pstack-sync.py',
                'skills/pstack-pair/scripts/pair.sh',
                'skills/pstack-pair/scripts/pstack_core/mux/amp.py',
            },
            names,
        )
        self.assertFalse([n for n in names if '__pycache__' in n])
        self.assertEqual(self.state()['master']['pane_id'], f'amp:{AMP_MASTER}')

    def alternate_roster(self):
        roster = Path(self.tmp.name) / 'alternate-roster.toml'
        roster.write_text(
            '[[sidekick.alternate]]\nkind = "amp"\nargs = ["--model", "m/a"]\n'
            '[[sidekick.alternate]]\nkind = "amp"\nargs = ["--model", "m/b"]\n'
            '[sidekick.fallback]\nkind = "amp"\nargs = ["--model", "m/fb"]\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)

    def alternate_choice(self, slug):
        path = self.store.parent / slug / 'pair.json'
        return json.loads(path.read_text()).get('sidekick_choice')

    def test_init_picks_a_different_alternate_sidekick_for_each_run_and_keeps_it(self):
        self.alternate_roster()
        first = self.run_sh('init', 'alt-one')
        second = self.run_sh('init', 'alt-two')
        one, two = self.alternate_choice('alt-one'), self.alternate_choice('alt-two')
        self.assertEqual({one['label'], two['label']}, {'amp m/a', 'amp m/b'})
        self.assertEqual(
            two,
            {
                'policy': 'alternate',
                'index': 1 - one['index'],
                'kind': 'amp',
                'args': ['--model', two['label'].split()[1]],
                'label': two['label'],
            },
        )
        for out, choice in ((first, one), (second, two)):
            self.assertIn(
                f'sidekick: alternate picks {choice["label"]} (entry {choice["index"] + 1} of 2)\n',
                out,
            )
        # A second init of the same store keeps the recorded choice, though the other runs now differ.
        again = self.run_sh('init', 'alt-one')
        self.assertEqual(self.alternate_choice('alt-one'), one)
        self.assertIn(
            f'sidekick: alternate picks {one["label"]} (entry {one["index"] + 1} of 2)\n',
            again,
        )

    def test_a_roster_without_alternate_entries_records_no_sidekick_choice(self):
        out = self.run_sh('init', 'plain')
        self.assertNotIn('alternate picks', out)
        self.assertIsNone(self.alternate_choice('plain'))

    def test_spawn_with_no_kind_starts_the_recorded_alternate_sidekick(self):
        self.alternate_roster()
        self.run_sh('init', 'alt-one')
        choice = self.alternate_choice('alt-one')
        self.run_sh('spawn', str(self.store.parent / 'alt-one'), code=4)
        log = (self.fake / 'amp.log').read_text()
        started = [
            line for line in log.splitlines() if '--title alt-one-sidekick' in line
        ]
        self.assertEqual(len(started), 1, log)
        self.assertIn(f'--model {choice["args"][1]} ', started[0])
        self.assertNotIn('--model m/fb', started[0])
        fallback = json.loads((self.store.parent / 'alt-one/pair.json').read_text())[
            'sidekick'
        ]['fallback']
        self.assertEqual(fallback, {'kind': 'amp', 'args': ['--model', 'm/fb']})

    def test_a_dispatch_records_the_sidekick_thread_under_its_model_once(self):
        sidekick = amp_thread(1)
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.assertEqual(
            self.state()['sidekick_threads'], {'amp zhipuai/glm-5.3': [sidekick]}
        )
        self.dispatch(self.brief('002', 'more', ['src/**', 'tests/**']))
        state = self.state()
        self.assertEqual(state['sidekick_threads'], {'amp zhipuai/glm-5.3': [sidekick]})
        self.assertEqual(
            state['sidekick_models'],
            {'001-util': 'amp zhipuai/glm-5.3', '002-more': 'amp zhipuai/glm-5.3'},
        )

    def test_retro_prints_the_sidekick_model_table_and_each_models_thread_cost(self):
        write_sidekick_store(self.store, *two_model_run())
        pair = self.state()
        pair['sidekick_threads'] = {
            'amp m/one': ['T-aaaa1', 'T-aaaa2'],
            'amp m/two': ['T-bbbb1'],
            'amp m/three': ['T-cccc1', 'T-cccc2'],
        }
        (self.store / 'pair.json').write_text(json.dumps(pair))
        costs = self.fake / 'amp-cost'
        costs.mkdir()
        for thread, cost in {
            'T-aaaa1': '1.25',
            'T-aaaa2': '0.50',
            'T-bbbb1': 'none',
            'T-cccc1': '0.50',
            'T-cccc2': 'none',
        }.items():
            (costs / thread).write_text(cost)
        lines = self.run_sh('retro', str(self.store)).splitlines()
        [start] = [n for n, line in enumerate(lines) if line == 'by sidekick model:']
        [head, one, two] = [line.split() for line in lines[start + 1 : start + 4]]
        self.assertEqual(head[:2], ['sidekick', 'model'])
        self.assertEqual(
            one, ['amp', 'm/one', '1', '2', '1', '1', '0', '1/1', '0', '10.0']
        )
        self.assertEqual(
            two, ['amp', 'm/two', '1', '1', '0', '1', '1', '0/1', '1', '20.0']
        )
        self.assertEqual(
            lines[start + 4 : start + 7],
            [
                '  sidekick cost amp m/one: $1.75 over 2 threads: T-aaaa1 T-aaaa2',
                '  sidekick cost amp m/three: $0.50 (1 unknown) over 2 threads: T-cccc1 T-cccc2',
                '  sidekick cost amp m/two: unknown over 1 thread: T-bbbb1',
            ],
        )

    def test_retro_prints_no_sidekick_section_without_the_model_record(self):
        self.assertNotIn('by sidekick model', self.run_sh('retro', str(self.store)))

    def spawn_amp_sidekick(self, slug, primary, fallback):
        """A second run in the same runs directory whose sidekick starts on the amp model `primary` with `fallback` as its fallback model; its
        READY is on file."""
        store = self.store.parent / slug
        self.run_sh('init', slug)
        self.run_sh(
            'spawn',
            str(store),
            '--kind',
            'amp',
            '--fallback',
            'amp',
            '--fallback-arg',
            '--model',
            '--fallback-arg',
            fallback,
            '--',
            '--model',
            primary,
            code=4,
        )
        (store / 'reports/000-ready.md').write_text('status: done\n')
        return store

    def test_init_records_the_masters_own_thread_and_its_title_as_the_runs_anchor(self):
        self.assertEqual(
            self.state()['anchor'],
            {'thread': AMP_MASTER, 'title': f'fake title of {AMP_MASTER}'},
        )

    def drop_anchor(self):
        """The store as an older build left it: no anchor in pair.json."""
        pair = self.state()
        del pair['anchor']
        (self.store / 'pair.json').write_text(json.dumps(pair))

    def record_master_threads(self, **started):
        """threads.json as the kitchen keeps it: the master threads it started for this run, each by its start time."""
        path = Path(self.tmp.name) / 'state/pstack/amp/threads.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    thread: {
                        'name': 'demo-master',
                        'role': 'master',
                        'store': os.path.realpath(self.store),
                        'started': at,
                    }
                    for thread, at in started.items()
                }
            )
        )

    def test_a_store_without_an_anchor_takes_the_oldest_master_thread_recorded_for_it(
        self,
    ):
        self.drop_anchor()
        self.record_master_threads(T_LATER=200, T_OLDEST=100, T_MIDDLE=150)
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        self.assertEqual(self.state()['anchor'], {'thread': 'T_OLDEST'})

    def test_a_store_without_an_anchor_or_a_recorded_master_takes_the_current_master(
        self,
    ):
        self.drop_anchor()
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        self.assertEqual(self.state()['anchor'], {'thread': AMP_MASTER})

    def nest_rows(self):
        """(thread, role, anchor, state) of each row of nest-pending.tsv."""
        path = self.store / 'nest-pending.tsv'
        return [tuple(line.split('\t')[1:]) for line in path.read_text().splitlines()]

    def test_every_role_thread_the_kitchen_starts_is_nest_pending_under_the_anchor_and_a_next_master_under_none(
        self,
    ):
        self.verify_started('001')
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertEqual(
            self.nest_rows(),
            [
                (amp_thread(1), 'sidekick', AMP_MASTER, 'pending'),
                (amp_thread(2), 'verifier', AMP_MASTER, 'pending'),
                (amp_thread(3), 'consultant', AMP_MASTER, 'pending'),
            ],
        )
        events = [
            line.split('\t')[3:]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tnest-pending\t' in line
        ]
        self.assertEqual(
            events,
            [
                ['nest-pending', '-', f'{amp_thread(n)} under {AMP_MASTER}']
                for n in (1, 2, 3)
            ],
        )

    def test_a_message_to_a_thread_that_has_a_row_or_is_the_anchor_adds_none(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.dispatch(self.brief('002', 'more', ['src/**', 'tests/**']))
        self.assertEqual(
            self.nest_rows(), [(amp_thread(1), 'sidekick', AMP_MASTER, 'pending')]
        )

    def three_pending(self):
        """A sidekick (spawned by setUp), a verifier and a consultant, all nest-pending under the master."""
        self.verify_started('001')
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        return [amp_thread(n) for n in (1, 2, 3)]

    def test_nest_lists_exactly_the_pending_rows_and_says_to_call_update_thread(self):
        threads = self.three_pending()
        out = self.run_sh('nest', str(self.store))
        self.assertEqual(
            out.splitlines(),
            [
                f'nest-pending: {threads[0]} (sidekick) under {AMP_MASTER}',
                f'nest-pending: {threads[1]} (verifier) under {AMP_MASTER}',
                f'nest-pending: {threads[2]} (consultant) under {AMP_MASTER}',
                'nest: call update_thread(thread=<T>, parent_thread=<anchor>) for each, '
                f'then kitchen.sh nest {self.store} --done <thread>...',
            ],
        )

    def rotate_on_record(self, *sessions, pane_id):
        """A run whose master rotated: `sessions` are the closed generations' threads, oldest first, and `pane_id` the current master's."""
        doc = self.state()
        doc['master']['pane_id'] = pane_id
        doc['master']['sessions'] = [
            {'generation': n, 'session': thread, 'handoff': '', 'at': ''}
            for n, thread in enumerate(sessions, 1)
        ]
        self.edit_pair(master=doc['master'], anchor=None)

    def test_nest_discover_sets_the_current_master_as_a_top_level_anchor_and_records_the_threads_without_a_row_once(
        self,
    ):
        old_master, sidekick, found = (amp_thread(n) for n in (41, 42, 43))
        (self.store / 'nest-pending.tsv').unlink(missing_ok=True)
        self.rotate_on_record(old_master, pane_id=f'amp:{AMP_MASTER}')
        here = os.path.realpath(self.store)
        self.write_threads(
            {
                sidekick: {'name': 'demo-sidekick', 'role': 'sidekick', 'store': here, 'started': 20},
                old_master: {'name': 'demo-master', 'role': 'master', 'store': here, 'started': 10},
            }
        )  # fmt: skip
        self.search_hits({'id': found, 'title': 'demo-consultant'})
        out = self.run_sh('nest', str(self.store), '--discover')
        self.assertEqual(self.state()['anchor'], {'thread': AMP_MASTER})
        self.assertEqual(
            sorted(self.nest_rows()),
            sorted(
                [
                    (AMP_MASTER, 'master', '-', 'pending'),
                    (amp_thread(1), 'sidekick', AMP_MASTER, 'pending'),  # setUp's
                    (sidekick, 'sidekick', AMP_MASTER, 'pending'),
                    (found, 'consultant', AMP_MASTER, 'pending'),
                ]
            ),
        )
        self.assertIn(f'nest-pending: {AMP_MASTER} (master) top-level', out)
        self.assertIn(f'nest-pending: {sidekick} (sidekick) under {AMP_MASTER}', out)
        self.assertNotIn(old_master, out)
        rows = self.nest_rows()
        again = self.run_sh('nest', str(self.store), '--discover')
        self.assertEqual(self.nest_rows(), rows)
        self.assertEqual(self.state()['anchor'], {'thread': AMP_MASTER})
        self.run_sh(
            'nest',
            str(self.store),
            '--done',
            AMP_MASTER,
            amp_thread(1),
            sidekick,
            found,
        )
        self.assertEqual(
            self.run_sh('nest', str(self.store), '--discover'),
            'nest: nothing pending\n',
        )
        self.assertIn(f'(consultant) under {AMP_MASTER}', again)

    def test_nest_discover_takes_the_newest_master_session_when_the_pane_is_no_thread(
        self,
    ):
        first, second = amp_thread(51), amp_thread(52)
        (self.store / 'nest-pending.tsv').unlink(missing_ok=True)
        self.rotate_on_record(first, second, pane_id='amp:1')
        self.run_sh('nest', str(self.store), '--discover')
        self.assertEqual(self.state()['anchor'], {'thread': second})
        self.assertIn((second, 'master', '-', 'pending'), self.nest_rows())
        self.assertNotIn(first, [row[0] for row in self.nest_rows()])

    SNOOZE_LINE = re.compile(r'(?m)^snooze: (T-\S+) \((\w+)\)$')

    def snooze_listed(self):
        """[(thread, role)] that `kitchen.sh snooze` lists, with every thread but those named in `idle` busy."""
        return self.SNOOZE_LINE.findall(self.run_sh('snooze', str(self.store)))

    def snooze_world(self, states):
        """Every thread the store names is working, but for those states gives: {thread: state}."""
        for thread in self.threads_json():
            self.set_state(thread, 'tool_use')
        for thread, state in states.items():
            self.set_state(thread, state, '2999-01-01T00:00:00.000Z')

    def review_unit(self, reviewed):
        """The sidekick's dispatched unit 001-util is done, and its review note exists when `reviewed`."""
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.done(b, 'abc')
        doc = self.state()
        doc['dispatch'] = {'brief': str(b)}
        doc['sidekick']['rotation_required'] = True
        self.edit_pair(**doc)
        review = self.store / 'reviews' / b.name
        review.parent.mkdir(exist_ok=True)
        review.unlink(missing_ok=True)
        if reviewed:
            review.write_text('verdict: accept\n')

    def test_snooze_lists_exactly_the_role_threads_that_are_idle_with_nothing_open(
        self,
    ):
        sidekick, consultant, old_master = amp_thread(1), amp_thread(2), amp_thread(9)
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        here = os.path.realpath(self.store)
        self.write_threads(
            {
                **self.threads_json(),
                old_master: {'name': 'demo-master', 'role': 'master', 'store': here, 'started': 1},
            }
        )  # fmt: skip
        self.assertEqual(self.threads_json()[consultant]['role'], 'consultant')
        with self.subTest(
            'a verifier is listed once it is done, never while it verifies'
        ):
            self.verify_started('001')
            verifier = amp_thread(3)
            self.snooze_world({sidekick: 'tool_use', verifier: 'idle'})
            self.assertEqual(self.snooze_listed(), [])
            doc = self.state()
            doc['verifying'] = None
            self.edit_pair(**doc)
            self.assertEqual(self.snooze_listed(), [(verifier, 'verifier')])
            self.snooze_world({})
        # (what is open, the states of the sidekick and of the consultant, what the master lists)
        for label, reviewed, consult, states, listed in (
            ('both settled', True, False, ('idle', 'idle'), [(sidekick, 'sidekick'), (consultant, 'consultant')]),
            ('the sidekick is working', True, False, ('tool_use', 'idle'), [(consultant, 'consultant')]),
            ('the sidekick is in error', True, False, ('error', 'idle'), [(consultant, 'consultant')]),
            ('the sidekick was cancelled', True, False, ('cancelled', 'idle'), [(consultant, 'consultant')]),
            ('the sidekick unit has no review', False, False, ('idle', 'idle'), [(consultant, 'consultant')]),
            ('a consult waits for advice', True, True, ('idle', 'idle'), [(sidekick, 'sidekick')]),
            ('the consultant is in error', True, False, ('idle', 'error'), [(sidekick, 'sidekick')]),
        ):  # fmt: skip
            with self.subTest(label):
                (self.store / 'events.tsv').write_text(
                    ''.join(
                        l + '\n'
                        for l in (self.store / 'events.tsv').read_text().splitlines()
                        if '\tsend-consult\t' not in l
                    )
                )
                self.review_unit(reviewed)
                if consult:
                    with (self.store / 'events.tsv').open('a') as f:
                        f.write('1\tmaster\tsend-consult\t001-util-c1\tdesign\n')
                self.snooze_world(
                    {sidekick: states[0], consultant: states[1], old_master: 'idle'}
                )
                self.assertEqual(self.snooze_listed(), listed)
        with self.subTest('PSTACK_SNOOZE_ROLES=0 lists none'):
            self.env['PSTACK_SNOOZE_ROLES'] = '0'
            self.assertEqual(
                self.run_sh('snooze', str(self.store)),
                'snooze: off (PSTACK_SNOOZE_ROLES=0)\n',
            )

    def test_snooze_done_and_wake_record_each_change_log_it_and_refuse_a_thread_the_store_never_named(
        self,
    ):
        sidekick, old_master = amp_thread(1), amp_thread(9)
        self.write_threads(
            {
                **self.threads_json(),
                old_master: {'name': 'demo-master', 'role': 'master', 'store': os.path.realpath(self.store), 'started': 1},
            }
        )  # fmt: skip
        out = self.run_sh('snooze', str(self.store), '--done', 'T-nope', code=1)
        self.assertIn('not recorded for snoozing: T-nope', out)
        self.assertFalse((self.store / 'snooze.tsv').exists())
        # A master, and a thread that is working or has open work, are refused before any row is written.
        self.review_unit(False)
        self.snooze_world({sidekick: 'tool_use', old_master: 'idle'})
        for thread, said in (
            (old_master, f'snooze: {old_master} is a master thread; masters are never snoozed'),
            (sidekick, f'snooze: {sidekick} is not snoozable now (its state is tool_use)'),
        ):  # fmt: skip
            out = self.run_sh('snooze', str(self.store), '--done', thread, code=1)
            self.assertIn(said, out)
        self.snooze_world({sidekick: 'idle'})
        out = self.run_sh('snooze', str(self.store), '--done', sidekick, code=1)
        self.assertIn(
            f'snooze: {sidekick} is not snoozable now (its unit has no review note)',
            out,
        )
        self.assertFalse((self.store / 'snooze.tsv').exists())
        self.review_unit(True)
        self.assertEqual(
            self.run_sh('snooze', str(self.store), '--done', sidekick, sidekick),
            f'snoozed: {sidekick}\n',
        )
        self.assertEqual(
            self.run_sh('snooze', str(self.store), '--done', sidekick),
            f'snoozed: {sidekick} (already)\n',
        )
        self.assertEqual(
            self.run_sh('snooze', str(self.store), '--wake', sidekick),
            f'awake: {sidekick}\n',
        )
        rows = [
            line.split('\t')[1:]
            for line in (self.store / 'snooze.tsv').read_text().splitlines()
        ]
        self.assertEqual(
            rows, [[sidekick, 'sidekick', 'snoozed'], [sidekick, 'sidekick', 'awake']]
        )
        events = [
            line.split('\t')[3:]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tsnooze\t' in line or '\twake\t' in line
        ]
        self.assertEqual(events, [['snooze', '-', sidekick], ['wake', '-', sidekick]])

    def test_a_snoozed_role_thread_refuses_dispatch_consult_and_steer_until_it_is_woken(
        self,
    ):
        sidekick, consultant = amp_thread(1), amp_thread(2)
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        steer = Path(self.run_sh('new-steer', str(self.store), '001').strip())
        steer.write_text(
            '# Steer\n\nkind: info\nscope effect: none\n\n## Direction\n\ngo on\n'
        )
        consult = Path(
            self.run_sh(
                'new-consult', str(self.store), '001', '--kind', 'design'
            ).strip()
        )
        consult.write_text(
            re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL)
        )
        with (self.store / 'snooze.tsv').open('a') as f:
            for thread, role in ((sidekick, 'sidekick'), (consultant, 'consultant')):
                f.write(f'2026-11-23T00:00:00Z\t{thread}\t{role}\tsnoozed\n')
        pair, queue = self.state(), (self.store / 'queue').exists()
        for label, args in (
            ('dispatch', ('dispatch', str(b))),
            ('steer', ('steer', str(steer))),
            ('consult', ('consult', str(consult))),
        ):
            with self.subTest(label):
                thread = consultant if label == 'consult' else sidekick
                out = self.run_sh(args[0], str(self.store), *args[1:], code=5)
                self.assertIn(
                    f'snoozed: {thread} is snoozed; call update_thread(thread={thread}, snooze="off"), '
                    f'then kitchen.sh snooze {self.store} --wake {thread}, then run this again',
                    out,
                )
                self.assertEqual(self.state(), pair)
                self.assertEqual((self.store / 'queue').exists(), queue)
        self.assertEqual(self.briefs_in(sidekick), [])
        # PSTACK_SNOOZE_ROLES=0 stops new snoozing only: a thread snoozed earlier still needs waking.
        self.env['PSTACK_SNOOZE_ROLES'] = '0'
        self.run_sh('dispatch', str(self.store), str(b), code=5)
        self.run_sh('snooze', str(self.store), '--wake', sidekick)
        self.dispatch(b)
        self.assertEqual(len(self.briefs_in(sidekick)), 1)

    def test_status_names_the_role_threads_due_for_snoozing_and_a_role_in_error_for_the_human(
        self,
    ):
        sidekick, consultant = amp_thread(1), amp_thread(2)
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        self.review_unit(True)
        self.snooze_world({sidekick: 'idle', consultant: 'error'})
        out = self.run_sh('status', str(self.store))
        self.assertIn(
            f'snooze-due: 1 role thread; run kitchen.sh snooze {self.store}\n', out
        )
        self.assertIn(
            f'needs-human: consultant {consultant} (error); relay it to the human from the master thread\n',
            out,
        )
        self.env['PSTACK_SNOOZE_ROLES'] = '0'
        out = self.run_sh('status', str(self.store))
        self.assertNotIn('snooze-due:', out)
        self.assertIn(f'needs-human: consultant {consultant} (error)', out)
        self.env.pop('PSTACK_SNOOZE_ROLES')
        self.run_sh('snooze', str(self.store), '--done', sidekick)
        self.assertNotIn('snooze-due:', self.run_sh('status', str(self.store)))

    def test_a_plan_round_to_an_awake_sidekick_and_a_snoozed_consultant_sends_nothing_until_it_is_woken(
        self,
    ):
        sidekick, consultant = amp_thread(1), amp_thread(2)
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        plan = self.store / 'plans/001-api.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: api\n\nrisk: escalated\n')
        with (self.store / 'snooze.tsv').open('a') as f:
            f.write(f'2026-11-23T00:00:00Z\t{consultant}\tconsultant\tsnoozed\n')
        before = len(self.thread_messages(sidekick))
        out = self.run_sh('discuss', str(self.store), str(plan), code=5)
        self.assertIn(f'snoozed: {consultant} is snoozed', out)
        self.assertEqual(len(self.thread_messages(sidekick)), before)
        self.assertNotIn('\tsend-plan\t', (self.store / 'events.tsv').read_text())
        self.run_sh('snooze', str(self.store), '--wake', consultant)
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1', code=None)
        for thread in (sidekick, consultant):
            self.assertTrue(
                any('PLAN' in m for m in self.thread_messages(thread)), thread
            )

    def test_status_exports_each_role_thread_once(self):
        sidekick, consultant = amp_thread(1), amp_thread(2)
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        self.review_unit(True)
        self.snooze_world({sidekick: 'idle', consultant: 'error'})
        log = self.fake / 'amp.log'
        mark = len(log.read_text().splitlines())
        out = self.run_sh('status', str(self.store))
        self.assertIn('snooze-due: 1 role thread', out)
        self.assertIn(f'needs-human: consultant {consultant} (error)', out)
        exports = [
            line.split()[2]
            for line in log.read_text().splitlines()[mark:]
            if line.startswith('threads export ')
        ]
        for thread in (sidekick, consultant):
            self.assertEqual(exports.count(thread), 1, exports)

    def test_nest_asks_the_named_thread_when_nest_via_is_set(self):
        self.three_pending()
        self.env['PSTACK_NEST_VIA'] = 'T-via'
        last = self.run_sh('nest', str(self.store)).splitlines()[-1]
        self.assertEqual(
            last,
            'nest: ask T-via to call update_thread(thread=<T>, parent_thread=<anchor>) for each, '
            f'then kitchen.sh nest {self.store} --done <thread>...',
        )

    def add_nest_row(self, thread, role, anchor, state='pending'):
        with (self.store / 'nest-pending.tsv').open('a') as f:
            f.write(f'2026-11-21T00:00:00Z\t{thread}\t{role}\t{anchor}\t{state}\n')

    def test_nest_lists_a_top_level_row_with_a_null_parent_instruction_and_done_closes_it(
        self,
    ):
        threads = self.three_pending()
        self.add_nest_row('T-top', 'master', '-')
        then = f'then kitchen.sh nest {self.store} --done <thread>...'
        self.assertEqual(
            self.run_sh('nest', str(self.store)).splitlines()[3:],
            [
                'nest-pending: T-top (master) top-level',
                f'nest: call update_thread(thread=<T>, parent_thread=<anchor>) for each, {then}',
                f'nest: call update_thread(thread=<T>, parent_thread=null) for each top-level row, {then}',
            ],
        )
        self.run_sh('nest', str(self.store), '--done', *threads)
        self.assertEqual(
            self.run_sh('nest', str(self.store)).splitlines(),
            [
                'nest-pending: T-top (master) top-level',
                f'nest: call update_thread(thread=<T>, parent_thread=null) for each top-level row, {then}',
            ],
        )
        self.assertEqual(
            self.run_sh('nest', str(self.store), '--done', 'T-top'),
            'nested: T-top top-level\n',
        )
        self.assertEqual(
            self.run_sh('nest', str(self.store)), 'nest: nothing pending\n'
        )
        events = (self.store / 'events.tsv').read_text()
        self.assertIn('\tnest\t-\tT-top top-level\n', events)

    def test_a_thread_nested_under_one_anchor_is_pending_again_under_the_next(self):
        sidekick = self.three_pending()[0]
        self.run_sh('nest', str(self.store), '--done', sidekick)
        self.add_nest_row(sidekick, 'sidekick', 'T-next')
        self.assertIn(
            f'nest-pending: {sidekick} (sidekick) under T-next',
            self.run_sh('nest', str(self.store)).splitlines(),
        )
        self.assertEqual(
            self.run_sh('nest', str(self.store), '--done', sidekick),
            f'nested: {sidekick} under T-next\n',
        )
        self.assertNotIn(sidekick, self.run_sh('nest', str(self.store)))

    def test_nest_done_on_two_of_three_leaves_one_and_status_counts_it(self):
        one, two, three = self.three_pending()
        self.assertIn(
            'nest-pending: 3 threads\n', self.run_sh('status', str(self.store))
        )
        out = self.run_sh('nest', str(self.store), '--done', one, three)
        self.assertEqual(
            out.splitlines(),
            [
                f'nested: {one} under {AMP_MASTER}',
                f'nested: {three} under {AMP_MASTER}',
            ],
        )
        self.assertEqual(
            self.run_sh('nest', str(self.store)).splitlines()[0],
            f'nest-pending: {two} (verifier) under {AMP_MASTER}',
        )
        self.assertIn(
            'nest-pending: 1 thread\n', self.run_sh('status', str(self.store))
        )
        events = (self.store / 'events.tsv').read_text()
        self.assertIn(f'\tnest\t-\t{one} under {AMP_MASTER}\n', events)
        self.run_sh('nest', str(self.store), '--done', two)
        self.assertEqual(
            self.run_sh('nest', str(self.store)), 'nest: nothing pending\n'
        )
        self.assertNotIn('nest-pending', self.run_sh('status', str(self.store)))

    def test_nest_done_refuses_a_thread_the_run_never_recorded(self):
        self.three_pending()
        out = self.run_sh('nest', str(self.store), '--done', 'T-nope', code=1)
        self.assertIn('not recorded for nesting: T-nope', out)

    def test_the_first_rotation_of_a_store_without_an_anchor_anchors_on_the_old_master(
        self,
    ):
        self.drop_anchor()
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertEqual(self.state()['anchor'], {'thread': AMP_MASTER})
        self.assertNotIn(amp_thread(2), [row[0] for row in self.nest_rows()])

    def test_a_rotation_moves_the_anchor_only_when_the_next_master_takes_over(self):
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertEqual(self.state()['anchor']['thread'], AMP_MASTER)
        self.as_thread(amp_thread(2), 'take-master', str(self.store), mux='amp')
        self.assertEqual(self.state()['master']['generation'], 2)
        self.assertEqual(self.state()['anchor']['thread'], amp_thread(2))

    def test_failover_moves_an_amp_sidekick_to_another_model_and_names_both(self):
        store = self.spawn_amp_sidekick('fo', 'a/one', 'b/two')
        out = self.run_sh('failover', str(store), '--reason', 'limit', code=4)
        self.assertIn('failover: amp a/one -> amp b/two (limit)', out)
        started = [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if '--title fo-sidekick' in line
        ]
        self.assertEqual(len(started), 2, started)
        self.assertIn('--model a/one ', started[0])
        self.assertIn('--model b/two ', started[1])
        failover = json.loads((store / 'pair.json').read_text())['sidekick'][
            'failovers'
        ]
        self.assertEqual(
            [(f['from'], f['to'], f['reason']) for f in failover],
            [('amp a/one', 'amp b/two', 'limit')],
        )
        self.assertRegex(
            (store / 'events.tsv').read_text(),
            r'\tmaster\tfailover\t-\tamp a/one:amp b/two\n',
        )
        out = self.run_sh('failover', str(store), code=5)
        self.assertIn('the sidekick runs the last of its chain (amp b/two)', out)

    CHAIN = ('a/one', 'b/two', 'c/three')

    def chain_roster(self):
        roster = Path(self.tmp.name) / 'chain-roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "amp"\nargs = ["--model", "a/one"]\n'
            + ''.join(
                f'[[sidekick.fallback]]\nkind = "amp"\nargs = ["--model", "{m}"]\n'
                for m in self.CHAIN[1:]
            )
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)

    def spawn_chain(self, slug, code=4):
        """A run in the same runs directory whose sidekick starts from the roster's chain, with the models in $FAKE/amp-preflight-fail failing."""
        self.chain_roster()
        self.run_sh('init', slug)
        store = self.store.parent / slug
        out = self.run_sh('spawn', str(store), code=code)
        return store, out

    def sidekick_models_started(self, slug):
        return [
            line.split('--model ')[1].split()[0]
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if f'--title {slug}-sidekick' in line and ' -ox ' in line
        ]

    def failover_events(self, store):
        return [
            line.split('\t')[5]
            for line in (store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tfailover\t' in line
        ]

    def role_chain_roster(self, role, extra=''):
        """A host roster whose `role` runs first on a/one and steps down to b/two and then c/three, all amp."""
        roster = Path(self.tmp.name) / 'role-chain-roster.toml'
        table = role if role != 'escalated' else 'verifier.escalated'
        roster.write_text(
            f'[sidekick]\nkind = "amp"\nargs = ["--model", "s/one"]\n[{table}]\nkind = "amp"\nargs = ["--model", "a/one"]\n'
            + ''.join(
                f'[[{table}.fallback]]\nkind = "amp"\nargs = ["--model", "{m}"]\n'
                for m in ('b/two', 'c/three')
            )
            + extra
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)

    def models_started(self, title):
        return [
            line.split('--model ')[1].split()[0]
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if f'--title {title}' in line and ' -ox ' in line
        ]

    def verify_started(self, *args):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.git(
            'update-ref',
            'refs/remotes/origin/main',
            self.git('rev-list', '--max-parents=0', 'HEAD'),
        )
        return self.run_sh('verify', str(self.store), *args, code=4)

    def verifier_failovers(self):
        return [
            line.split('\t')[5]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tfailover\t' in line and 'verifier:' in line
        ]

    def test_a_routine_verifier_steps_down_its_chain_on_failed_preflights(self):
        self.role_chain_roster('verifier')
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\n')
        out = self.verify_started('001')
        self.assertIn('failover: verifier amp a/one -> amp b/two (preflight: ', out)
        self.assertEqual(self.models_started('demo-verifier'), ['c/three'])
        self.assertEqual(
            self.verifier_failovers(),
            ['verifier:amp a/one:amp b/two', 'verifier:amp b/two:amp c/three'],
        )

    def test_a_verifier_whose_whole_chain_fails_starts_nothing(self):
        self.role_chain_roster('verifier')
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\nc/three\n')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        out = self.run_sh('verify', str(self.store), '001', code=5)
        self.assertIn(
            'every agent of the verifier chain failed its preflight (amp a/one, amp b/two, amp c/three)',
            out,
        )
        self.assertEqual(self.models_started('demo-verifier'), [])

    def test_the_escalated_verifier_starts_from_the_top_of_its_own_chain(self):
        self.role_chain_roster('escalated')
        (self.fake / 'amp-preflight-fail').write_text('a/one\n')
        out = self.verify_started('--landing')
        self.assertIn('failover: verifier amp a/one -> amp b/two (preflight: ', out)
        self.assertEqual(self.models_started('demo-verifier'), ['b/two'])
        self.assertEqual(self.verifier_failovers(), ['verifier:amp a/one:amp b/two'])

    def rotate_master_log(self, *extra):
        self.role_chain_roster('master')
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store), *extra)
        return [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if '--title demo-master-g2' in line
        ]

    def test_rotate_master_starts_the_next_master_on_the_first_passing_roster_entry(
        self,
    ):
        (self.fake / 'amp-preflight-fail').write_text('a/one\n')
        [started] = self.rotate_master_log()
        self.assertIn('-m b/two --title demo-master-g2', started)
        self.assertEqual(
            self.failover_events(self.store), ['master:amp a/one:amp b/two']
        )

    def rotate_master_on(self, master):
        """(the output, the amp start line of the next master) of rotate-master with `master` as the roster's [master] table."""
        roster = Path(self.tmp.name) / 'mixed-master-roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "amp"\nargs = ["--model", "s/one"]\n' + master
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        self.filled_handoff()
        out = self.run_sh('rotate-master', str(self.store))
        [started] = [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if '--title demo-master-g2' in line
        ]
        self.assertEqual(self.failover_events(self.store), [])
        return out, started

    CLAUDE_MASTER = '[master]\nkind = "claude"\nargs = ["--model", "claude-opus-5-5"]\n'

    def test_rotate_master_skips_roster_entries_that_are_not_amp_agents(self):
        out, started = self.rotate_master_on(
            self.CLAUDE_MASTER
            + '[[master.fallback]]\nkind = "amp"\nargs = ["--model", "b/two"]\n'
        )
        self.assertIn('skip: master claude claude-opus-5-5 is not an amp agent', out)
        self.assertIn('-m b/two --title demo-master-g2', started)

    def test_rotate_master_with_no_amp_entry_in_the_roster_starts_on_this_threads_mode(
        self,
    ):
        out, started = self.rotate_master_on(self.CLAUDE_MASTER)
        self.assertIn('skip: master claude claude-opus-5-5 is not an amp agent', out)
        self.assertIn(
            "skip: no amp entry in the master chain; the next master runs on this thread's mode",
            out,
        )
        self.assertNotIn(' -m ', started)

    def test_rotate_master_with_a_model_skips_the_roster_chain(self):
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\nc/three\n')
        [started] = self.rotate_master_log('--model', 'x/y')
        self.assertIn('-m x/y --title demo-master-g2', started)
        self.assertEqual(self.failover_events(self.store), [])

    def test_rotate_master_whose_chain_all_fails_starts_nothing(self):
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\nc/three\n')
        self.role_chain_roster('master')
        self.filled_handoff()
        out = self.run_sh('rotate-master', str(self.store), code=5)
        self.assertIn(
            'every agent of the master chain failed its preflight (amp a/one, amp b/two, amp c/three); pass --model to start the next master anyway',
            out,
        )
        self.assertNotIn('previous', self.state()['master'])
        self.assertEqual(len(self.failover_events(self.store)), 2)

    def test_a_consultant_steps_down_its_chain_on_failed_preflights(self):
        self.role_chain_roster('consultant')
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\n')
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertIn('failover: consultant amp a/one -> amp b/two (preflight: ', out)
        self.assertEqual(self.models_started('demo-consultant'), ['c/three'])
        self.assertEqual(
            self.failover_events(self.store),
            ['consultant:amp a/one:amp b/two', 'consultant:amp b/two:amp c/three'],
        )
        failovers = self.state()['consultant']['failovers']
        self.assertEqual(
            [(f['from'], f['to']) for f in failovers],
            [('amp a/one', 'amp b/two'), ('amp b/two', 'amp c/three')],
        )

    def test_a_consultant_whose_whole_chain_fails_starts_nothing(self):
        self.role_chain_roster('consultant')
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\nc/three\n')
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=2)
        self.assertIn(
            'consultant amp a/one, amp b/two, amp c/three failed its preflight and the roster names no other fallback',
            out,
        )
        self.assertEqual(self.models_started('demo-consultant'), [])
        self.assertEqual(len(self.failover_events(self.store)), 2)

    def test_a_consultant_fallback_the_same_as_its_entry_is_not_preflighted_again(
        self,
    ):
        self.role_chain_roster('consultant')
        roster = Path(self.env['PSTACK_KITCHEN_ROSTER'])
        roster.write_text(
            '[sidekick]\nkind = "amp"\nargs = ["--model", "s/one"]\n'
            '[consultant]\nkind = "amp"\nargs = ["--model", "a/one"]\n'
            '[[consultant.fallback]]\nkind = "amp"\nargs = ["--model", "a/one"]\n'
        )
        (self.fake / 'amp-preflight-fail').write_text('a/one\n')
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=2)
        self.assertIn(
            'consultant amp a/one failed its preflight and the roster names no other fallback',
            out,
        )
        preflights = [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if ' -x ' in line and '--model a/one' in line
        ]
        self.assertEqual(len(preflights), 1)
        self.assertEqual(self.failover_events(self.store), [])

    def test_spawn_records_the_roster_chain_and_starts_its_first_entry(self):
        store, _ = self.spawn_chain('ch')
        sidekick = json.loads((store / 'pair.json').read_text())['sidekick']
        self.assertEqual(
            sidekick['chain'],
            [{'kind': 'amp', 'args': ['--model', m]} for m in self.CHAIN],
        )
        self.assertEqual(sidekick['chain_index'], 0)
        self.assertEqual(sidekick['fallback'], sidekick['chain'][1])
        self.assertEqual(self.sidekick_models_started('ch'), ['a/one'])
        self.assertEqual(self.failover_events(store), [])

    def test_a_spawn_whose_first_entry_fails_its_preflight_starts_the_second(self):
        (self.fake / 'amp-preflight-fail').write_text('a/one\n')
        store, out = self.spawn_chain('ch')
        self.assertIn('failover: amp a/one -> amp b/two (preflight: ', out)
        self.assertEqual(self.sidekick_models_started('ch'), ['b/two'])
        self.assertEqual(self.failover_events(store), ['amp a/one:amp b/two'])
        sidekick = json.loads((store / 'pair.json').read_text())['sidekick']
        self.assertEqual(sidekick['chain_index'], 1)
        self.assertEqual(sidekick['fallback'], sidekick['chain'][2])

    def test_a_spawn_whose_first_two_entries_fail_starts_the_third(self):
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\n')
        store, _ = self.spawn_chain('ch')
        self.assertEqual(self.sidekick_models_started('ch'), ['c/three'])
        self.assertEqual(
            self.failover_events(store),
            ['amp a/one:amp b/two', 'amp b/two:amp c/three'],
        )
        sidekick = json.loads((store / 'pair.json').read_text())['sidekick']
        self.assertEqual(sidekick['chain_index'], 2)

    def test_a_spawn_whose_whole_chain_fails_starts_nothing(self):
        (self.fake / 'amp-preflight-fail').write_text('a/one\nb/two\nc/three\n')
        _, out = self.spawn_chain('ch', code=None)
        self.assertIn(
            'every agent of the sidekick chain failed its preflight (amp a/one, amp b/two, amp c/three)',
            out,
        )
        self.assertEqual(self.sidekick_models_started('ch'), [])

    def test_failover_steps_down_the_chain_and_is_refused_at_its_end(self):
        store, _ = self.spawn_chain('ch')
        (store / 'reports/000-ready.md').write_text('status: done\n')
        self.run_sh('failover', str(store), '--reason', 'limit', code=4)
        self.run_sh('failover', str(store), '--reason', 'limit', code=4)
        self.assertEqual(
            self.sidekick_models_started('ch'), ['a/one', 'b/two', 'c/three']
        )
        self.assertEqual(
            self.failover_events(store),
            ['amp a/one:amp b/two', 'amp b/two:amp c/three'],
        )
        sidekick = json.loads((store / 'pair.json').read_text())['sidekick']
        self.assertEqual(sidekick['chain_index'], 2)
        out = self.run_sh('failover', str(store), code=5)
        self.assertIn('the sidekick runs the last of its chain (amp c/three)', out)

    def test_a_dispatch_continues_the_sidekick_thread_and_its_finish_messages_the_master(
        self,
    ):
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.dispatch(b)
        self.assertIn(f'amp demo-sidekick ({sidekick}): pstack-kitchen BRIEF {b}', out)
        self.assertIn(
            f'threads continue {sidekick} --attach ',
            (self.fake / 'amp.log').read_text(),
        )
        bundle = self.fake / 'amp-threads' / sidekick / '2-pstack-demo-sidekick.tgz'
        # The sidekick's thread syncs first (the registry it learns names the master's thread), does the unit and finishes.
        out = self.as_thread(sidekick, 'sync', str(bundle))
        self.assertIn(f'synced: {self.store} from demo-master', out)
        self.assertNotIn('warning: this orb sets PSTACK_MUX', out)
        # An orb whose project sets another backend is told to prefix the commands that message the master.
        out = self.as_thread(sidekick, 'sync', str(bundle), mux='subagents')
        self.assertIn(
            'warning: this orb sets PSTACK_MUX=subagents; run finish and notify here as PSTACK_MUX=amp kitchen.sh',
            out,
        )
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        self.done(b, self.commit('util'))
        report = self.store / 'reports' / b.name
        out = self.as_thread(sidekick, 'finish', str(self.store), str(report))
        self.assertIn(
            f'amp demo-master ({AMP_MASTER}): pstack-kitchen REPORT {report}', out
        )
        [reply] = self.thread_messages(AMP_MASTER)
        self.assertTrue(reply.startswith(f'pstack-kitchen REPORT {report}\n'), reply)
        self.assertIn(
            'pair.sh sync <the pstack-demo-master.tgz attachment URL below>', reply
        )
        self.assertNotIn('reaches it only when you send it', reply)
        self.assertNotIn('pstack-build.tgz', reply)

    def test_a_role_message_always_reaches_a_working_master_as_one_message_with_its_bundle(
        self,
    ):
        """finish and notify left a file for the master's next wait when its thread was in a turn; under amp that file stayed in the role's
        orb. A READY, a done report and a verdict each queue a message behind the turn, carrying the store bundle and the file's header."""
        sidekick = amp_thread(1)
        self.set_state(AMP_MASTER, 'tool_use')
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        head = self.commit('util')
        self.done(b, head)
        verdict = self.store / 'verdicts/001-util-v1.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text(
            f'# Verdict\n\nstatus: clean\nunits: 001-util\nhead: {head}\n'
        )
        ready = self.store / 'reports/000-ready.md'
        ready.write_text('# Report 000: ready\n\nstatus: done\nhead: r1\n')
        for path, command, header in (
            (ready, 'finish', 'status: done'),
            (self.store / 'reports' / b.name, 'finish', f'head: {head}'),
            (verdict, 'notify', 'units: 001-util'),
        ):
            before = len(self.thread_messages(AMP_MASTER))
            out = self.as_thread(sidekick, command, str(self.store), str(path))
            self.assertIn(f'sent REPORT {path} to master demo-master', out)
            self.assertNotIn('left ', out)
            messages = self.thread_messages(AMP_MASTER)
            self.assertEqual(len(messages), before + 1, out)
            self.assertTrue(
                messages[-1].startswith(f'pstack-kitchen REPORT {path}\n'), messages[-1]
            )
            self.assertIn(header, messages[-1])
            self.assertEqual(
                self.attachments(AMP_MASTER).count('pstack-demo-master.tgz'),
                before + 1,
            )

    def test_the_same_file_is_sent_again_only_when_its_content_changed(self):
        sidekick = amp_thread(1)
        self.set_state(AMP_MASTER, 'tool_use')
        ready = self.store / 'reports/000-ready.md'
        ready.write_text('# Report 000: ready\n\nstatus: done\nhead: r1\n')
        self.as_thread(sidekick, 'finish', str(self.store), str(ready))
        self.as_thread(sidekick, 'finish', str(self.store), str(ready))
        self.assertEqual(len(self.thread_messages(AMP_MASTER)), 1)
        ready.write_text('# Report 000: ready\n\nstatus: done\nhead: r2\n')
        self.as_thread(sidekick, 'finish', str(self.store), str(ready))
        messages = self.thread_messages(AMP_MASTER)
        self.assertEqual(len(messages), 2)
        self.assertIn('head: r2', messages[1])

    def test_a_verdict_already_sent_to_the_master_is_pending_sync_not_a_deadline(self):
        """A verifier that sent its verdict and ended its turn leaves the verdict in its own orb until the master syncs; past the deadline the
        master's verify --wait says so, and neither rewrites the verification nor prompts the verifier for an inconclusive verdict."""
        self.verify_started('001')
        verifier = amp_thread(2)
        verdict = self.store / 'verdicts/001-util-v1.md'
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        self.as_thread(verifier, 'notify', str(self.store), str(verdict))
        verdict.unlink()
        self.set_state(verifier, 'idle', '2026-01-01T00:00:00.000Z')
        state = self.state()
        state['verifying']['deadline'] = 1
        (self.store / 'pair.json').write_text(json.dumps(state))
        before = (self.store / 'pair.json').read_text()
        out = self.run_sh('verify', str(self.store), '--wait', code=4)
        at = 'an unknown time'  # the fake amp dates no message
        self.assertIn(
            f'pending sync: verifier sent {verdict} at {at}; run pair.sh sync from that message',
            out,
        )
        self.assertNotIn('past its deadline', out)
        self.assertEqual((self.store / 'pair.json').read_text(), before)
        self.assertFalse(verdict.exists())
        self.assertEqual(len(self.thread_messages(verifier)), 1)
        # Once a sync brings the verdict in, the same message no longer says pending sync, and the verification closes on it.
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        out = self.run_sh('verify', str(self.store), '--wait', code=None)
        self.assertNotIn('pending sync', out)
        self.assertIn(f'verdict: {verdict}', out)

    def test_a_report_already_sent_to_the_master_is_pending_sync_for_its_wait(self):
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('util'))
        report = self.store / 'reports' / b.name
        self.as_thread(sidekick, 'finish', str(self.store), str(report))
        text = report.read_text()
        report.unlink()
        out = self.run_sh('wait', str(self.store), code=4)
        at = 'an unknown time'  # the fake amp dates no message
        self.assertIn(
            f'pending sync: sidekick sent {report} at {at}; run pair.sh sync from that message',
            out,
        )
        self.assertNotIn('report: missing', out)
        # Once a sync brings the report in, the same message no longer says pending sync.
        report.write_text(text)
        out = self.run_sh('wait', str(self.store), code=None)
        self.assertNotIn('pending sync', out)
        self.assertIn('status: done', out)

    def decisions(self):
        return (self.store / 'decisions.tsv').read_text().splitlines()[1:]

    def test_a_retired_sidekick_thread_is_kept_until_the_new_ready_then_archived_and_logged(
        self,
    ):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=4)
        # The new thread's READY is not in the store: the old thread stays, and says why.
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(
            f'kept {amp_thread(1)}: the new sidekick has not sent READY yet', out
        )
        self.assertFalse((self.fake / 'amp-archived').exists())
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{amp_thread(1)}\n')
        [row] = [
            r for r in self.decisions() if f'\tarchive sidekick {amp_thread(1)}\t' in r
        ]
        self.assertIn('\tarchive\t', row)
        self.assertIn(
            f'\tmaster\tarchive\t-\tsidekick:{amp_thread(1)}\n',
            (self.store / 'events.tsv').read_text(),
        )
        # Archived once: a later sweep neither repeats it nor lists it.
        self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{amp_thread(1)}\n')

    def test_a_working_thread_or_one_with_a_pending_message_is_never_archived(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=4)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        old = amp_thread(1)
        self.set_state(old, 'tool_use')
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'kept {old}: working (tool_use): it is in a turn', out)
        self.assertFalse((self.fake / 'amp-archived').exists())
        # A message sent to it since its turn ended is still pending.
        self.set_state(old, 'idle', '2026-01-01T00:00:00.000Z')
        outbox = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/outbox.json'
        doc = json.loads(outbox.read_text())
        doc[old] = {'hash': 'x', 'id': 'y', 'at': 4102444800}
        outbox.write_text(json.dumps(doc))
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'kept {old}: a message to it is still pending', out)
        self.assertFalse((self.fake / 'amp-archived').exists())

    def sidekick_done_idle(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')
        return amp_thread(1)

    def sidekick_under_verification(self):
        """A sidekick whose last unit's report is done while a verifier still holds its verdict open (pair.json .verifying, set by
        verify_launch and cleared by verify_state.drop_verifying)."""
        thread = self.sidekick_done_idle()
        pair = self.state()
        pair['verifying'] = {
            'verdict': 'verdicts/001-one-v1.md',
            'packet': 'verify/001-one-v1.md',
            'brief': '001-one',
            'unit': '001-one',
            'units': '001-one',
            'started': 0,
            'deadline': 45,
            'delivered': False,
        }
        (self.store / 'pair.json').write_text(json.dumps(pair))
        return thread

    def sidekick_in_a_turn(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.set_state(amp_thread(1), 'tool_use')

    def sidekick_with_an_open_dispatch(self):
        """rotation_required is true again (a stale bundle sets it back) while the dispatched brief has no report."""
        self.sidekick_partial()
        (self.store / 'reports/001-one.md').unlink()
        pair = self.state()
        pair['sidekick']['rotation_required'] = True
        (self.store / 'pair.json').write_text(json.dumps(pair))

    def sidekick_partial(self):
        b = self.brief('001', 'one', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=None)
        (self.store / 'reports' / b.name).write_text(
            '# Report\n\nstatus: partial\nhead: abc1234\n'
        )
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')

    def consultant_with(self, advised):
        """A started consultant, idle, with one consult sent to it; `advised` is whether its advice is on file. The sidekick is in a turn."""
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        (self.store / 'advice').mkdir(exist_ok=True)
        (self.store / 'advice/000-ready.md').write_text('status: done\n')
        with open(self.store / 'events.tsv', 'a') as f:
            f.write('1\tmaster\tsend-consult\t001-one-c1\tdesign\n')
        if advised:
            (self.store / 'advice/001-one-c1.md').write_text('status: agree\n')
        self.set_state(amp_thread(1), 'tool_use')
        self.set_state(amp_thread(2), 'idle', '2999-01-01T00:00:00.000Z')
        return amp_thread(2)

    def test_threads_archive_archives_an_idle_role_with_no_open_work_and_keeps_the_rest_in_use(
        self,
    ):
        """A live sidekick or consultant counted as in use until close, however long it had sat idle. It is archivable once its thread is
        settled and the run holds nothing open for it: the sidekick's last unit is done and nothing is queued, and no verifier is still
        verifying it; every consult has its advice."""
        rows = (
            ('a done sidekick, idle', self.sidekick_done_idle, True, 1),
            ('a sidekick under verification', self.sidekick_under_verification, False, 2),
            ('a sidekick in a turn', self.sidekick_in_a_turn, False, 2),
            ('a sidekick with a partial report', self.sidekick_partial, False, 2),
            ('a sidekick with an open dispatch', self.sidekick_with_an_open_dispatch, False, 2),
            ('a consultant, all advice answered', lambda: self.consultant_with(True), True, 2),
            ('a consultant with an open consult', lambda: self.consultant_with(False), False, 3),
        )  # fmt: skip
        for n, (label, setup, archives, in_use) in enumerate(rows):
            with self.subTest(label):
                if n:
                    self.setUp()
                thread = setup()
                archived = self.fake / 'amp-archived'
                summary = f'archive: {int(archives)} archived, 0 kept, {in_use} in use'
                out = self.merged('threads', str(self.store), '--archive')
                self.assertEqual(self.last_line(out), summary, out)
                if archives:
                    self.assertEqual(archived.read_text(), f'{thread}\n', out)
                else:
                    self.assertFalse(archived.exists(), out)

    def test_the_next_dispatch_starts_a_new_thread_after_an_idle_sidekick_was_archived(
        self,
    ):
        old = self.sidekick_done_idle()
        self.run_sh('threads', str(self.store), '--archive')
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{old}\n')
        registry = json.loads(
            (Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/registry.json').read_text()
        )
        self.assertNotIn('demo-sidekick', registry['agents'])
        before = len(self.thread_messages(old))
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertIn(
            f'amp demo-sidekick ({amp_thread(2)}): Load the pstack-kitchen skill', out
        )
        self.assertEqual(len(self.thread_messages(old)), before)

    def test_the_next_consult_starts_a_new_thread_on_the_same_model_after_an_idle_consultant_was_archived(
        self,
    ):
        """`threads --archive` ended the consultant's registry entry, so its next consult stopped with 'not live; run: pair.sh spawn' and its model
        flags were lost."""
        self.sidekick_done_idle()
        old = self.consultant_with(True)
        self.run_sh('threads', str(self.store), '--archive')
        self.assertIn(old, (self.fake / 'amp-archived').read_text().split())
        before = len(self.thread_messages(old))
        (
            self.store / 'advice/001-one-c1.md'
        ).unlink()  # consultant_with's advice has the name this consult takes
        consult = self.consult_file([])
        out = self.send_consult(consult)
        self.assertNotIn('not live', out)
        self.assertEqual(len(self.thread_messages(old)), before)
        new = next(
            t
            for t in (amp_thread(n) for n in range(3, 9))
            if (self.fake / 'amp-threads' / t).exists()
        )
        message = self.thread_messages(new)[0]
        self.assertIn(f'pstack-kitchen CONSULT {consult}', message)
        self.assertTrue(
            message.startswith(
                'You are a new thread for this role. Read ~/.agents/skills/pstack-kitchen/SKILL.md (your role: consultant)'
            ),
            message,
        )
        starts = [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if '--title demo-consultant' in line
        ]
        self.assertEqual(len(starts), 2, starts)
        self.assertIn('--model c/one', starts[-1])

    def test_stop_names_a_role_whose_entry_holds_no_thread_and_starts_no_new_one(self):
        """`threads --archive` ended the consultant's thread but kept its registry entry (thread ''), so stop would have prompted it: under Amp a
        prompt is what starts a thread, one started only to be told STOP. It names the role and starts nothing."""
        self.sidekick_done_idle()
        old = self.consultant_with(True)
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')
        self.run_sh('threads', str(self.store), '--archive')
        self.assertIn(old, (self.fake / 'amp-archived').read_text().split())
        before = (self.fake / 'amp.log').read_text().splitlines()
        out = self.run_sh('stop', str(self.store))
        self.assertIn('stop: demo-consultant has no thread; nothing to stop', out)
        self.assertEqual((self.fake / 'amp.log').read_text().splitlines(), before)

    def retire_threads(self, entries):
        """threads.json of the amp state dir as the build writes it: {thread: {name, role, at, store}}; each thread is idle."""
        path = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/threads.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        known = json.loads(path.read_text()) if path.exists() else {}
        for thread, entry in entries.items():
            known[thread] = {'at': 1, **entry}
            self.set_state(thread, 'idle')
        path.write_text(json.dumps(known))

    def test_the_threads_command_lists_the_unused_threads_and_archives_exactly_those(
        self,
    ):
        old_sidekick, verifier = amp_thread(7), amp_thread(9)
        self.retire_threads(
            {
                old_sidekick: {
                    'name': 'demo-sidekick',
                    'role': 'sidekick',
                    'store': str(self.store),
                },
                verifier: {
                    'name': 'demo-verifier',
                    'role': 'verifier',
                    'store': str(self.store),
                },
            }
        )
        archived = self.fake / 'amp-archived'
        archived.write_text('')
        out = self.run_sh('threads', str(self.store))
        for thread, role in ((old_sidekick, 'sidekick'), (verifier, 'verifier')):
            self.assertRegex(out, rf'(?m)^{thread}  {role}  idle  archivable$')
        self.assertRegex(out, rf'(?m)^{amp_thread(1)}  sidekick  in use  in use$')
        self.assertEqual(archived.read_text(), '')
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertIn('archived 2 thread(s)', out)
        self.assertEqual(
            sorted(archived.read_text().split()), sorted([old_sidekick, verifier])
        )
        for thread, role in ((old_sidekick, 'sidekick'), (verifier, 'verifier')):
            self.assertTrue(
                any(f'archive {role} {thread}' in r for r in self.decisions())
            )

    def test_a_thread_whose_state_cannot_be_read_is_never_archived(self):
        unknown = amp_thread(8)
        self.retire_threads(
            {unknown: {'name': 'demo-verifier', 'store': str(self.store)}}
        )
        (self.fake / 'amp-state' / unknown).unlink()
        archived = self.fake / 'amp-archived'
        out = self.run_sh('threads', str(self.store))
        self.assertIn(f'{unknown}  verifier  unknown  kept: state unknown', out)
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertIn(f'kept {unknown}: state unknown', out)
        self.assertFalse(archived.exists())

    def test_a_thread_with_no_store_that_the_store_names_is_listed_and_archived_once(
        self,
    ):
        named = amp_thread(11)
        self.retire_threads({named: {'name': 'demo-sidekick', 'role': 'sidekick'}})
        pair = self.state()
        pair['sidekick_threads'] = {'amp m/one': [named]}
        (self.store / 'pair.json').write_text(json.dumps(pair))
        self.set_state(named, 'idle', '2999-01-01T00:00:00.000Z')
        out = self.merged('threads', str(self.store))
        self.assertEqual(len(re.findall(rf'(?m)^{named}  ', out)), 1)
        self.assertRegex(out, rf'(?m)^{named}  sidekick  idle  archivable$')
        out = self.merged('threads', str(self.store), '--archive')
        self.assertEqual(out.count(f'archived {named} '), 1)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{named}\n')
        self.assertRegex(self.last_line(out), r'^archive: 1 archived, 0 kept, ')

    def test_a_dispatch_sweep_archives_a_retired_thread_with_no_store_that_the_store_names(
        self,
    ):
        """A thread retired with no store recorded was listed as archivable by `threads`, yet no automatic sweep archived it."""
        named, stranger = amp_thread(11), amp_thread(12)
        self.retire_threads(
            {
                named: {'name': 'demo-sidekick', 'role': 'sidekick'},
                stranger: {'name': 'demo-sidekick', 'role': 'sidekick'},
            }
        )
        pair = self.state()
        pair['sidekick_threads'] = {'amp m/one': [named]}
        (self.store / 'pair.json').write_text(json.dumps(pair))
        self.set_state(named, 'idle', '2999-01-01T00:00:00.000Z')
        b = self.brief('001', 'one', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'archived {named} (sidekick)', out)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{named}\n')

    def test_a_sweep_archives_only_the_threads_of_its_own_store_and_never_one_with_no_store(
        self,
    ):
        mine, theirs, orphan = amp_thread(7), amp_thread(8), amp_thread(9)
        self.retire_threads(
            {
                mine: {'name': 'demo-verifier', 'store': str(self.store)},
                theirs: {'name': 'other-verifier', 'store': '/somewhere/else/run'},
                orphan: {'name': 'demo-verifier'},
            }
        )
        out = self.run_sh('threads', str(self.store))
        self.assertIn(f'{orphan}  verifier  unknown  kept: no store recorded', out)
        self.assertNotIn(theirs, out)
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{mine}\n')
        # A dispatch sweeps too, and leaves the other store's and the unscoped thread alone.
        self.retire_threads({mine: {'name': 'demo-verifier'}})
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{mine}\n')

    def announced_at_master(self, path):
        """What Amp.sent_to_master answers in the master's thread for the file: its time, 'an unknown time', or None (as text)."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp\n'
            f'print(amp.Amp().sent_to_master("pstack-kitchen", {str(path)!r}))\n'
        )
        return subprocess.run(
            [sys.executable, '-I', '-c', code],
            env={**self.env, 'AMP_THREAD_ID': AMP_MASTER},
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()

    def test_a_superseded_announcement_does_not_keep_a_synced_file_in_pending_sync(
        self,
    ):
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('util'))
        report = self.store / 'reports' / b.name
        first = report.read_text()
        self.as_thread(sidekick, 'finish', str(self.store), str(report))
        second = first + '\nrewritten\n'
        report.write_text(second)
        self.as_thread(sidekick, 'finish', str(self.store), str(report))
        self.assertEqual(len(self.thread_messages(AMP_MASTER)), 2)
        # The master holds the newest content: nothing is pending, although the first announcement quoted another digest.
        self.assertEqual(self.announced_at_master(report), 'None')
        # It holds the older content: the newest announcement is not in its store yet.
        report.write_text(first)
        self.assertNotEqual(self.announced_at_master(report), 'None')
        report.write_text(second)
        self.assertEqual(self.announced_at_master(report), 'None')

    def test_a_verifier_thread_is_archived_once_its_verdict_is_recorded(self):
        self.verify_started('001')
        verifier = amp_thread(2)
        verdict = self.store / 'verdicts/001-util-v1.md'
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        self.as_thread(verifier, 'notify', str(self.store), str(verdict))
        self.set_state(verifier, 'idle', '2999-01-01T00:00:00.000Z')
        self.assertFalse((self.fake / 'amp-archived').exists())
        out = self.run_sh('verify', str(self.store), '--wait', code=None)
        self.assertIn(f'archived {verifier} (verifier): its verdict is recorded', out)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{verifier}\n')

    def test_a_cancelled_verifier_still_in_a_turn_blocks_the_next_until_stopped_and_its_late_verdict_is_void(
        self,
    ):
        self.verify_started('--landing')
        old = amp_thread(2)
        self.set_state(old, 'running')
        stop = f'update_thread({{thread: "{old}", archived: true, stop_running_turn: true}})'
        out = self.run_sh('verify', str(self.store), '--cancel')
        self.assertIn('cancelled: landing', out)
        self.assertIn(f'master: verifier thread {old} is still running', out)
        self.assertIn(stop, out)
        self.assertFalse((self.fake / 'amp-archived').exists())
        # The cancelled verifier writes its verdict anyway: it is void, so land-check and the next landing base never read it.
        late = self.store / 'verdicts/landing-v1.md'
        late.write_text(
            'status: clean\nunits: landing\n\n## Findings\n\nnone\n\n## Evidence\n\n```\n$ true\n```\n'
        )
        self.assertNotIn(
            str(late), self.run_sh('land-check', str(self.store), code=None)
        )
        out = self.run_sh('verify', str(self.store), '--landing', code=5)
        self.assertIn(stop, out)
        self.assertFalse((self.store / 'verdicts/landing-v2-packet.md').exists())
        self.assertFalse((self.fake / 'amp-threads' / amp_thread(3)).exists())
        # The master stops it with update_thread; the next verify archives it and only then starts the next verifier.
        self.set_state(old, 'cancelled')
        out = self.run_sh('verify', str(self.store), '--landing', code=4)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{old}\n')
        self.assertIn(f'amp demo-verifier ({amp_thread(3)})', out)
        self.assertIn(
            f'\tmaster\tarchive\t-\tverifier:{old}\n',
            (self.store / 'events.tsv').read_text(),
        )

    def sync_notified(self, thread, path):
        """The role's thread sends `path` to the master with notify and the master syncs that message's bundle: how a READY, a report or a
        verdict reaches the master's store (and its registry) in production."""
        self.as_thread(thread, 'notify', str(self.store), str(path))
        bundles = (self.fake / 'amp-threads' / AMP_MASTER).glob(
            '*-pstack-demo-master.tgz'
        )
        newest = max(bundles, key=lambda p: int(p.name.split('-', 1)[0]))
        return self.run_sh('sync', str(newest))

    def assert_archived_and_logged(self, thread, role):
        self.assertIn(thread, (self.fake / 'amp-archived').read_text().split())
        self.assertTrue(
            [r for r in self.decisions() if f'\tarchive {role} {thread}\t' in r]
        )
        self.assertIn(
            f'\tmaster\tarchive\t-\t{role}:{thread}\n',
            (self.store / 'events.tsv').read_text(),
        )

    def test_a_rotated_sidekick_whose_replies_reached_the_master_by_sync_is_archived(
        self,
    ):
        old, new = amp_thread(1), amp_thread(2)
        ready = self.store / 'reports/000-ready.md'
        self.sync_notified(old, ready)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.sync_notified(old, self.store / 'reports/001-one.md')
        self.set_state(old, 'idle', '2999-01-01T00:00:00.000Z')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=4)
        ready.write_text('status: done\n')
        self.sync_notified(new, ready)
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'archived {old} (sidekick)', out)
        self.assert_archived_and_logged(old, 'sidekick')

    def test_a_closed_consultant_whose_ready_reached_the_master_by_sync_is_archived(
        self,
    ):
        out = self.run_sh(
            'consultant',
            str(self.store),
            '--reason',
            'contract',
            '--kind',
            'amp',
            '--',
            '--model',
            'c/one',
            code=4,
        )
        consultant = amp_thread(2)
        self.assertIn(f'demo-consultant ({consultant})', out)
        ready = self.store / 'advice/000-ready.md'
        ready.write_text('status: done\n')
        self.sync_notified(consultant, ready)
        self.set_state(consultant, 'idle', '2999-01-01T00:00:00.000Z')
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')
        out = self.run_sh('close', str(self.store), '--force', code=None)
        self.assertIn(f'archived {consultant} (consultant)', out)
        self.assert_archived_and_logged(consultant, 'consultant')

    def test_a_verifier_whose_verdict_reached_the_master_by_sync_is_archived(self):
        self.verify_started('001')
        verifier = amp_thread(2)
        verdict = self.store / 'verdicts/001-util-v1.md'
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        self.sync_notified(verifier, verdict)
        self.set_state(verifier, 'idle', '2999-01-01T00:00:00.000Z')
        out = self.run_sh('verify', str(self.store), '--wait', code=None)
        self.assertIn(f'archived {verifier} (verifier)', out)
        self.assert_archived_and_logged(verifier, 'verifier')
        out = self.run_sh('threads', str(self.store))
        self.assertNotRegex(out, rf'(?m)^{verifier}  verifier  \S+  in use$')

    def test_a_verifier_is_not_in_use_once_its_verdict_is_recorded(self):
        self.verify_started('001')
        verifier = amp_thread(2)
        verdict = self.store / 'verdicts/001-util-v1.md'
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        self.sync_notified(verifier, verdict)
        self.assertRegex(
            self.run_sh('threads', str(self.store)),
            rf'(?m)^{verifier}  verifier  in use  in use$',
        )
        self.run_sh('verify', str(self.store), '--wait', code=None)
        self.assertIsNone(self.state().get('verifying'))
        # The verifier's entry is back in the registry (a reply still in flight is synced), though its verification is over.
        registry = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/registry.json'
        doc = json.loads(registry.read_text())
        doc['agents']['demo-verifier'] = {
            'pane': 'amp:2',
            'kind': 'amp',
            'args': [],
            'thread': verifier,
        }
        registry.write_text(json.dumps(doc))
        out = self.run_sh('threads', str(self.store))
        self.assertRegex(
            out, rf'(?m)^{verifier}  verifier  unknown  kept: state unknown'
        )

    def threads_json(self):
        return json.loads(
            (Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/threads.json').read_text()
        )

    def write_threads(self, doc):
        (Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/threads.json').write_text(
            json.dumps(doc)
        )

    def test_a_thread_the_kitchen_starts_is_recorded_for_its_store_and_stays_in_use_without_its_registry_entry(
        self,
    ):
        sidekick = amp_thread(1)
        entry = self.threads_json()[sidekick]
        self.assertEqual(
            {k: entry[k] for k in ('name', 'role', 'store')},
            {
                'name': 'demo-sidekick',
                'role': 'sidekick',
                'store': os.path.realpath(self.store),
            },
        )
        self.assertGreater(entry['started'], 0)
        (Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/registry.json').unlink()
        out = self.run_sh('threads', str(self.store))
        self.assertRegex(out, rf'(?m)^{sidekick}  sidekick  in use  in use$')

    def search_hits(self, *hits):
        (self.fake / 'amp-search').write_text(json.dumps(list(hits)))

    def test_a_registered_helper_is_archived_when_idle_and_kept_with_its_reason_in_a_turn(
        self,
    ):
        idle, busy = amp_thread(21), amp_thread(22)
        self.set_state(idle, 'idle', '2999-01-01T00:00:00.000Z')
        self.set_state(busy, 'tool_use')
        out = self.run_sh('threads', str(self.store), '--register', idle)
        self.assertIn(f'registered {idle} (helper)', out)
        self.run_sh(
            'threads', str(self.store), '--register', busy, '--role', 'researcher'
        )
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertRegex(out, rf'(?m)^{idle}  helper  idle  archivable$')
        self.assertIn(f'archived {idle} (helper): threads --archive', out)
        self.assertIn(f'kept {busy}: working (tool_use): it is in a turn', out)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{idle}\n')
        self.assert_archived_and_logged(idle, 'helper')
        self.assertEqual(self.threads_json()[busy]['role'], 'researcher')
        # The same store again changes nothing; a thread another run recorded is refused and keeps its store.
        self.run_sh('threads', str(self.store), '--register', busy)
        other = amp_thread(24)
        self.write_threads(
            {**self.threads_json(), other: {'role': 'helper', 'store': '/other/run'}}
        )
        before = self.threads_json()
        out = self.merged('threads', str(self.store), '--register', other, code=1)
        self.assertIn(
            f'kitchen.sh: {other} is recorded for /other/run; not registered', out
        )
        self.assertEqual(self.threads_json()[other], before[other])

    def test_the_next_sweep_archives_a_registered_helper_that_has_gone_idle(self):
        helper = amp_thread(23)
        self.run_sh('threads', str(self.store), '--register', helper)
        self.set_state(helper, 'tool_use')
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'kept {helper}: working (tool_use)', out)
        self.set_state(helper, 'idle', '2999-01-01T00:00:00.000Z')
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'archived {helper} (helper)', out)
        self.assertIn(helper, (self.fake / 'amp-archived').read_text().split())

    def test_threads_discover_records_the_run_s_unrecorded_threads_and_leaves_the_others(
        self,
    ):
        role, helper, other, foreign = (amp_thread(n) for n in (31, 32, 33, 34))
        self.set_state(role, 'idle', '2999-01-01T00:00:00.000Z')
        self.set_state(helper, 'idle', '2999-01-01T00:00:00.000Z')
        self.search_hits(
            {'id': role, 'title': 'demo-consultant'},
            {'id': helper, 'title': 'a side chat', 'text': f'see {self.store}'},
            {'id': AMP_MASTER, 'title': 'demo-master', 'text': str(self.store)},
            {'id': other, 'title': 'other-sidekick', 'text': '/elsewhere/store'},
            {'id': foreign, 'title': 'demo-verifier'},
        )
        known = self.threads_json()
        known[foreign] = {
            'name': 'demo-verifier',
            'role': 'verifier',
            'store': '/other/run',
        }
        (Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/threads.json').write_text(
            json.dumps(known)
        )
        out = self.run_sh('threads', str(self.store), '--discover', '--archive')
        self.assertIn(f'discovered {role} (consultant)', out)
        self.assertIn(f'discovered {helper} (helper)', out)
        archived = (self.fake / 'amp-archived').read_text().split()
        self.assertEqual(sorted(archived), sorted([role, helper]))
        for thread in (AMP_MASTER, other, foreign):
            self.assertNotIn(thread, archived)
        self.assertNotIn(other, self.threads_json())
        self.assertNotEqual(
            self.threads_json()[foreign]['store'], os.path.realpath(self.store)
        )

    def test_threads_discover_records_only_titles_that_are_exactly_this_run_s_and_searches_its_repo(
        self,
    ):
        prefix, other_run, ours = amp_thread(41), amp_thread(42), amp_thread(43)
        self.set_state(ours, 'idle', '2999-01-01T00:00:00.000Z')
        self.search_hits(
            {'id': prefix, 'title': 'demo-x-sidekick'},
            {'id': other_run, 'title': 'demo-sidekick'},
            {'id': ours, 'title': 'demo-verifier'},
        )
        self.write_threads(
            {
                **self.threads_json(),
                other_run: {'name': 'demo-sidekick', 'store': '/other/run'},
            }
        )
        self.git('remote', 'add', 'origin', 'https://example.com/acme/demo.git')
        out = self.run_sh('threads', str(self.store), '--discover', '--archive')
        self.assertIn(f'discovered {ours} (verifier)', out)
        self.assertNotIn(prefix, out)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{ours}\n')
        self.assertEqual(self.threads_json()[other_run]['store'], '/other/run')
        self.assertNotIn(prefix, self.threads_json())
        self.assertIn(
            'archived:false demo- repo:example.com/acme/demo',
            (self.fake / 'amp.log').read_text(),
        )

    def test_the_repo_filter_of_a_search_is_host_owner_name_whatever_the_remote_s_form(
        self,
    ):
        table = {
            'https://ghp_token123@github.com/owner/repo.git': 'github.com/owner/repo',
            'https://user:secret@github.com:8443/owner/repo/': 'github.com/owner/repo',
            'ssh://git@github.com/owner/repo.git': 'github.com/owner/repo',
            'ssh://git@github.com:22/owner/repo': 'github.com/owner/repo',
            'git@github.com:owner/repo.git': 'github.com/owner/repo',
            'github.com:owner/repo': 'github.com/owner/repo',
            'https://github.com/owner/repo': 'github.com/owner/repo',
            '/srv/git/repo.git': '',
            '': '',
        }
        code = (
            'import json, sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux.amp import repo_filter\n'
            'print(json.dumps({u: repo_filter(u) for u in json.loads(sys.argv[1])}))\n'
        )
        out = subprocess.run(
            [sys.executable, '-I', '-c', code, json.dumps(list(table))],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        self.assertEqual(json.loads(out), table)

    def test_a_send_to_an_existing_thread_never_stamps_it_started(self):
        sidekick = amp_thread(1)
        known = self.threads_json()
        del known[sidekick]
        self.write_threads(known)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.assertNotIn(sidekick, self.threads_json())

    def test_a_late_bundle_from_a_retired_thread_does_not_take_its_role_back(self):
        old, new = amp_thread(1), amp_thread(2)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        late = self.store / 'reports/late.md'
        late.write_text('status: done\n')
        self.as_thread(old, 'notify', str(self.store), str(late))
        bundle = max(
            (self.fake / 'amp-threads' / AMP_MASTER).glob('*-pstack-demo-master.tgz'),
            key=lambda p: int(p.name.split('-', 1)[0]),
        )
        self.next_unit_rotates('002', 'guide', ['docs/**'])
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        self.run_sh('sync', str(bundle))
        registry = json.loads(self.registry().read_text())
        self.assertEqual(registry['agents']['demo-sidekick']['thread'], new)
        self.assertNotIn('at', self.threads_json()[new])
        self.assertIn('at', self.threads_json()[old])
        self.set_state(old, 'idle', '2999-01-01T00:00:00.000Z')
        self.set_state(new, 'idle', '2999-01-01T00:00:00.000Z')
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertRegex(out, rf'(?m)^{new}  sidekick  in use  in use$')
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{old}\n')

    def merged(self, *args, code=0):
        """kitchen.sh's stdout and stderr as one stream in the order they were written, as a terminal shows them."""
        result = subprocess.run(
            [str(self.script), *args],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=120,
        )
        if code is not None:
            self.assertEqual(result.returncode, code, result.stdout)
        return result.stdout

    def last_line(self, out):
        return out.rstrip('\n').split('\n')[-1]

    def test_every_sweeping_command_ends_with_one_archive_summary_line(self):
        old, new = amp_thread(1), amp_thread(2)
        ready = self.store / 'reports/000-ready.md'
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.set_state(old, 'idle', '2999-01-01T00:00:00.000Z')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        out = self.merged('dispatch', str(self.store), str(b), code=4)
        self.assertRegex(
            self.last_line(out), r'^archive: 0 archived, 0 kept, \d+ in use$'
        )
        # The command sweeps before and after its rotation check: each thread still counts and prints once (the old thread kept, one thread in use; before the
        # sweep deduped by thread this printed 2 kept and 2 in use).
        out = self.merged('dispatch', str(self.store), str(b), code=None)
        self.assertEqual(self.last_line(out), 'archive: 0 archived, 1 kept, 1 in use')
        self.assertEqual(len(re.findall(rf'(?m)^kept {old}: ', out)), 1)
        ready.write_text('status: done\n')
        out = self.merged('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'archived {old} (sidekick)', out)
        self.assertRegex(
            self.last_line(out), r'^archive: 1 archived, 0 kept, \d+ in use$'
        )
        self.assertEqual(len(re.findall(r'(?m)^archive: ', out)), 1)
        out = self.merged('threads', str(self.store))
        self.assertRegex(
            self.last_line(out), r'^archive: 0 archived, 0 kept, \d+ in use$'
        )
        self.set_state(new, 'tool_use')
        out = self.merged('close', str(self.store), '--force', code=None)
        self.assertRegex(
            self.last_line(out), r'^archive: 0 archived, 1 kept, \d+ in use$'
        )
        self.assertIn(f'kept {new}: ', out)

    def test_a_thread_kept_by_one_sweep_and_archived_by_the_next_counts_once_as_archived(
        self,
    ):
        """A sweep cannot be forced to keep a thread and then archive it within one command, so the summary is driven by its outcomes."""
        sweeps = [
            {'a': 'kept', 'b': 'in use', 'c': 'kept'},
            {'a': 'archived', 'b': 'in use', 'c': 'kept', 'd': 'archived'},
            {'b': 'in use', 'e': 'in use'},
        ]
        code = (
            'import json, sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.commands.rotate import Sweep\n'
            'print(repr(Sweep.summary()))\n'
            'for outcomes in json.loads(sys.argv[1]):\n'
            '    Sweep.count(outcomes)\n'
            'print(repr(Sweep.summary()))\n'
        )
        out = subprocess.run(
            [sys.executable, '-I', '-c', code, json.dumps(sweeps)],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.splitlines()
        self.assertEqual(out, ["''", "'archive: 2 archived, 1 kept, 2 in use\\n'"])

    REGISTRY_WRITES = {
        'a start that claims the pane or name of a live entry': (
            "Amp().start('demo-verifier', 'amp', 'amp:2', ['--model', 'm/x'])"
        ),
        'a pane closed with its agent still in the registry': (
            "Amp().pane_close('amp:2')"
        ),
        'a rename onto a name another thread holds': (
            "Amp().rename('amp:3', 'demo-verifier')"
        ),
    }

    def test_every_registry_write_that_drops_a_thread_retires_it_for_its_store(self):
        reg = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp'
        old = amp_thread(7)
        for why, call in self.REGISTRY_WRITES.items():
            with self.subTest(why):
                (reg / 'threads.json').unlink(missing_ok=True)
                entry = {'kind': 'amp', 'args': [], 'store': str(self.store)}
                (reg / 'registry.json').write_text(
                    json.dumps(
                        {
                            'next': 4,
                            'panes': ['amp:2', 'amp:3'],
                            'agents': {
                                'demo-verifier': {
                                    **entry,
                                    'pane': 'amp:2',
                                    'thread': old,
                                },
                                'demo-helper': {
                                    **entry,
                                    'pane': 'amp:3',
                                    'thread': amp_thread(8),
                                },
                            },
                        }
                    )
                )
                code = (
                    'import sys\n'
                    f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
                    'from pstack_core.mux.amp import Amp\n'
                    f'{call}\n'
                )
                subprocess.run(
                    [sys.executable, '-I', '-c', code],
                    env={**self.env, 'AMP_THREAD_ID': AMP_MASTER},
                    check=True,
                    timeout=60,
                )
                retired = json.loads((reg / 'threads.json').read_text())
                self.assertEqual(
                    {k: retired[old][k] for k in ('name', 'role', 'store')},
                    {
                        'name': 'demo-verifier',
                        'role': 'verifier',
                        'store': os.path.realpath(self.store),
                    },
                )

    def test_a_new_pane_is_never_one_the_registry_already_holds(self):
        """A rotated master's orb has a fresh counter, and a sync teaches it the panes of roles started in other orbs."""
        reg = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp'
        sidekick = {
            'kind': 'amp',
            'args': [],
            'pane': 'amp:2',
            'thread': amp_thread(1),
        }
        (reg / 'registry.json').write_text(
            json.dumps(
                {
                    'next': 1,
                    'panes': ['amp:2', 'amp:2', 'amp:T-x'],
                    'agents': {
                        'demo-sidekick': sidekick,
                        'demo-helper': {**sidekick, 'pane': 'amp:5'},
                    },
                }
            )
        )
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux.amp import Amp\n'
            'print(Amp().new(), Amp().new())\n'
        )
        out = subprocess.run(
            [sys.executable, '-I', '-c', code],
            env={**self.env, 'AMP_THREAD_ID': AMP_MASTER},
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        self.assertEqual(out.split(), ['amp:6', 'amp:7'])

    def test_verifying_after_a_fresh_counter_leaves_the_live_sidekick_in_place(self):
        """The registry of a rotated master's orb starts its counter at 1 and holds the sidekick under amp:1, the pane the verifier started in
        before new() skipped the panes it knew."""
        reg = self.registry()
        doc = json.loads(reg.read_text())
        doc['next'] = 1
        reg.write_text(json.dumps(doc))
        sidekick = json.loads(reg.read_text())['agents']['demo-sidekick']
        self.assertEqual(sidekick['pane'], 'amp:1')
        self.verify_started('--landing')
        agents = json.loads(reg.read_text())['agents']
        self.assertEqual(agents['demo-sidekick'], sidekick)
        self.assertNotEqual(agents['demo-verifier']['pane'], sidekick['pane'])
        self.assertNotIn('at', self.threads_json().get(amp_thread(1), {}))
        self.assertFalse((self.fake / 'amp-archived').exists())

    def test_the_old_master_thread_is_archived_by_take_master_and_not_before(self):
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        archived = self.fake / 'amp-archived'
        self.assertFalse(archived.exists())
        self.set_state(AMP_MASTER, 'idle')
        out = self.as_thread(master2, 'take-master', str(self.store))
        self.assertIn(f'archived {AMP_MASTER} (master): take-master has run', out)
        self.assertEqual(archived.read_text(), f'{AMP_MASTER}\n')
        self.assertNotIn(master2, archived.read_text())

    def take_master_after_rotation(self, **env):
        """The next master's take-master after rotate-master; (its output, the new master's thread)."""
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        self.set_state(AMP_MASTER, 'idle')
        out = self.as_thread(master2, 'take-master', str(self.store), env=env)
        return out, master2

    def test_take_master_moves_the_anchor_to_the_new_master_and_renames_and_lists_it_top_level(
        self,
    ):
        out, master2 = self.take_master_after_rotation()
        title = f'fake title of {AMP_MASTER}'
        anchor = self.state()['anchor']
        self.assertEqual(anchor['thread'], master2)
        self.assertEqual(anchor['previous'], [AMP_MASTER])
        self.assertEqual(anchor['title'], title)
        self.assertEqual(
            (self.fake / 'amp-renamed').read_text(), f'{master2}\t{title}\n'
        )
        self.assertIn(
            f'anchor: {master2} is the run\'s top-level anchor "{title}"; run kitchen.sh nest {self.store}',
            out,
        )
        self.assertIn(f'archived {AMP_MASTER} (master): take-master has run', out)
        listed = self.run_sh('nest', str(self.store)).splitlines()
        self.assertIn(f'nest-pending: {master2} (master) top-level', listed)
        self.assertIn(
            f'nest-pending: {amp_thread(1)} (sidekick) under {master2}', listed
        )
        self.assertNotIn(f'under {AMP_MASTER}', '\n'.join(listed))
        self.assertNotIn((master2, 'master', AMP_MASTER, 'pending'), self.nest_rows())

    def test_take_master_saves_the_anchor_title_init_could_not_read(self):
        pair = self.state()
        pair['anchor']['title'] = ''
        (self.store / 'pair.json').write_text(json.dumps(pair))
        (self.fake / f'amp-title-{AMP_MASTER}').write_text('Run anchor\n')
        out, master2 = self.take_master_after_rotation()
        self.assertEqual(self.state()['anchor']['title'], 'Run anchor')
        self.assertEqual(
            (self.fake / 'amp-renamed').read_text(), f'{master2}\tRun anchor\n'
        )
        self.assertIn('top-level anchor "Run anchor"', out)

    def test_take_master_from_an_anchor_with_no_thread_records_no_empty_previous_anchor(
        self,
    ):
        pair = self.state()
        pair['anchor'] = {'thread': '', 'title': 'Run anchor'}
        (self.store / 'pair.json').write_text(json.dumps(pair))
        _, master2 = self.take_master_after_rotation()
        self.assertEqual(
            self.state()['anchor'], {'thread': master2, 'title': 'Run anchor'}
        )

    def test_a_master_thread_started_for_a_store_with_no_pair_json_is_only_recorded(
        self,
    ):
        from pstack_core.mux import amp

        bare = Path(self.tmp.name) / 'bare-store'
        bare.mkdir()
        state = Path(self.tmp.name) / 'bare-state'
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(state)}):
            amp.record_started('T-bare', 'demo-master', str(bare), True)
            recorded = amp.load_json(amp.threads_path())
        self.assertEqual(recorded['T-bare']['role'], 'master')
        self.assertFalse((bare / 'nest-pending.tsv').exists())

    def test_take_master_nests_a_live_consultant_under_the_new_master(self):
        self.run_sh(
            'consultant', str(self.store), '--reason', 'contract', '--kind', 'amp',
            '--', '--model', 'c/one', code=4,
        )  # fmt: skip
        consultant = amp_thread(2)
        self.assertIn(
            (consultant, 'consultant', AMP_MASTER, 'pending'), self.nest_rows()
        )
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(3)
        self.set_state(AMP_MASTER, 'idle')
        self.as_thread(master2, 'take-master', str(self.store))
        listed = self.run_sh('nest', str(self.store)).splitlines()
        self.assertIn(
            f'nest-pending: {consultant} (consultant) under {master2}', listed
        )
        self.assertIn(
            f'nest-pending: {amp_thread(1)} (sidekick) under {master2}', listed
        )

    def test_a_second_take_master_adds_no_nest_rows_and_leaves_the_anchor(self):
        _, master2 = self.take_master_after_rotation()
        rows, anchor = self.nest_rows(), self.state()['anchor']
        out = self.as_thread(master2, 'take-master', str(self.store))
        self.assertIn('no hand-over open', out)
        self.assertEqual(self.nest_rows(), rows)
        self.assertEqual(self.state()['anchor'], anchor)

    def test_take_master_whose_rename_fails_warns_and_still_takes_over(self):
        (self.fake / 'amp-rename-fail').write_text('')
        out, master2 = self.take_master_after_rotation()
        self.assertIn(f'warn: amp threads rename {master2} failed', out)
        self.assertIn(
            f"anchor: {master2} is the run's top-level anchor; run kitchen.sh nest {self.store}\n",
            out,
        )
        self.assertNotIn('top-level anchor "', out)
        self.assertEqual(self.state()['anchor']['thread'], master2)
        self.assertEqual(self.state()['master']['generation'], 2)

    def test_close_archives_the_roles_that_pass_the_rule_and_says_why_it_kept_the_rest(
        self,
    ):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        sidekick = amp_thread(1)
        self.set_state(sidekick, 'tool_use')
        out = self.run_sh('close', str(self.store), '--force', code=None)
        self.assertIn(f'kept {sidekick}: ', out)
        self.assertFalse((self.fake / 'amp-archived').exists())

    def finish_unit(self, seq, slug, scope, path, thread=1):
        """A routine unit dispatched to the sidekick's thread and finished done there; the dispatch's output."""
        b = self.brief(seq, slug, scope, verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.write(path, f'{slug}\n')
        self.done(b, self.commit(slug))
        self.as_thread(
            amp_thread(thread),
            'finish',
            str(self.store),
            str(self.store / 'reports' / b.name),
        )
        return out

    def next_unit_rotates(self, seq, slug, scope):
        """Dispatching the next unit starts a fresh sidekick thread: the rotation hands off its bootstrap (exit 4). The old thread is retired but
        archived only once the new one's READY is in the store and the next dispatch sweeps."""
        b = self.brief(seq, slug, scope, verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertNotIn('kept:', out)
        self.assertIn(
            f'amp demo-sidekick ({amp_thread(2)}): Load the pstack-kitchen skill', out
        )
        self.assertFalse((self.fake / 'amp-archived').exists())
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn(f'archived {amp_thread(1)} (sidekick)', out)
        self.assertEqual((self.fake / 'amp-archived').read_text(), f'{amp_thread(1)}\n')

    def test_a_routine_unit_in_the_same_area_keeps_the_sidekick_thread(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        out = self.finish_unit('002', 'two', ['src/**', 'tests/**'], 'src/two.py')
        self.assertIn(
            'kept: the amp sidekick keeps its session for this routine unit (app, context 10%)',
            out,
        )
        self.assertNotIn('rotated:', out)
        self.assertIn('pstack-kitchen BRIEF', self.thread_messages(amp_thread(1))[-1])
        self.assertEqual((self.fake / 'amp-started').read_text(), '1')
        self.assertFalse((self.fake / 'amp-archived').exists())

    def test_a_dispatch_that_keeps_the_sidekick_reads_its_context_once(self):
        sidekick = amp_thread(1)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')

        def context_reads():
            log = (self.fake / 'amp.log').read_text().splitlines()
            return log.count(f'threads context {sidekick}')

        before = context_reads()
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn('kept: the amp sidekick keeps its session', out)
        self.assertEqual(context_reads() - before, 1)

    def attachments(self, thread):
        """The names of the files attached to the messages of thread, without the message number."""
        d = self.fake / 'amp-threads' / thread
        return sorted(p.name.split('-', 1)[1] for p in d.glob('*.tgz'))

    def test_a_role_thread_gets_the_build_and_the_send_back_rules_once(self):
        sidekick = amp_thread(1)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.finish_unit('002', 'two', ['src/**', 'tests/**'], 'src/two.py')
        bootstrap, first, second = self.thread_messages(sidekick)
        install, rules = (
            'run `d=~/.local/share/pstack-builds/',
            'reaches it only when you send it',
        )
        sync = f'`PSTACK_MUX=amp ~/.agents/skills/pstack-pair/scripts/pair.sh sync <the pstack-demo-sidekick.tgz attachment URL below>`: it merges the {self.store} store'
        short = 'Send files for the master back with PSTACK_MUX=amp kitchen.sh finish or notify (pair.sh in a pair or trio).'
        self.assertIn(install, bootstrap)
        self.assertIn(rules, bootstrap)
        self.assertNotIn(short, bootstrap)
        for later in (first, second):
            self.assertNotIn(install, later)
            self.assertNotIn(rules, later)
            self.assertIn(sync, later)
            self.assertIn(short, later)
            self.assertLess(len(later), 600)
        # The build travels with the first message only; every message carries the store.
        self.assertEqual(
            self.attachments(sidekick),
            ['pstack-build.tgz', *['pstack-demo-sidekick.tgz'] * 3],
        )

    def test_a_changed_build_is_attached_again_to_a_thread_that_has_the_old_one(self):
        sidekick = amp_thread(1)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        # The same pstack with one file changed is another build.
        copy = Path(self.tmp.name) / 'copy'
        shutil.copytree(
            root / 'skills',
            copy / 'skills',
            ignore=shutil.ignore_patterns('__pycache__'),
            symlinks=True,
        )
        (copy / 'scripts').mkdir()
        shutil.copy(root / 'scripts/pstack-sync.py', copy / 'scripts/pstack-sync.py')
        with (copy / 'skills/pstack-pair/SKILL.md').open('a') as f:
            f.write('\nA changed line.\n')
        self.script = copy / 'skills/pstack-kitchen/scripts/kitchen.sh'
        self.finish_unit('002', 'two', ['src/**', 'tests/**'], 'src/two.py')
        *_, last = self.thread_messages(sidekick)
        self.assertIn('run `d=~/.local/share/pstack-builds/', last)
        self.assertNotIn('reaches it only when you send it', last)
        self.assertEqual(self.attachments(sidekick).count('pstack-build.tgz'), 2)

    def message_from_the_master(self, name, text):
        """Amp.prompt of the master's own thread: a message to the role `name` that no kitchen command would send now."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp\n'
            f'sys.exit(amp.Amp().prompt({name!r}, {text!r}).code)\n'
        )
        subprocess.run(
            [sys.executable, '-I', '-c', code],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )

    def send_from_the_master(self, name, text, thread='', store=''):
        """Amp.send of the master's own thread, as a command outside a dispatch makes it: the thread the message went to."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp\n'
            f'thread, err = amp.Amp().send({name!r}, {text!r}, {thread!r}, store={store or str(self.store)!r})\n'
            'print(thread)\n'
        )
        return subprocess.run(
            [sys.executable, '-I', '-c', code],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()

    # Two orbs on one machine: each role's store is a directory of its own that is swapped in for self.store while that role runs, with a
    # state home (the registry and the last bundle sent to each peer) of its own too.
    def newest_bundle(self, thread, name):
        return max(
            (self.fake / 'amp-threads' / thread).glob(f'*-pstack-{name}.tgz'),
            key=lambda p: int(p.name.split('-', 1)[0]),
        )

    def keep_store(self, key):
        target = Path(self.tmp.name) / f'{key}-store'
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.store, target, symlinks=True)

    def swap_in_store(self, key):
        shutil.rmtree(self.store)
        source = Path(self.tmp.name) / f'{key}-store'
        if source.exists():
            shutil.copytree(source, self.store, symlinks=True)
        else:
            self.store.mkdir(parents=True)

    def on_orb(self, thread, *args, code=0):
        return self.as_thread(
            thread,
            *args,
            code=code,
            mux='amp',
            env={'XDG_STATE_HOME': str(Path(self.tmp.name) / f'state-{thread}')},
        )

    def sync_on_orb(self, thread, name):
        """The master's newest message to `name` is synced on the role's orb, whose store starts empty the first time."""
        bundle = self.newest_bundle(thread, name)
        self.keep_store('master')
        self.swap_in_store(f'orb-{thread}')
        self.on_orb(thread, 'sync', str(bundle))

    def reply_from_orb(self, thread, command, path):
        """The role sends `path` to the master (command finish or notify) and the master syncs the reply."""
        self.on_orb(thread, command, str(self.store), str(path))
        reply = self.newest_bundle(AMP_MASTER, 'demo-master')
        self.keep_store(f'orb-{thread}')
        self.swap_in_store('master')
        return self.run_sh('sync', str(reply))

    def ready_on_orb(self, thread):
        self.sync_on_orb(thread, 'demo-sidekick')
        ready = self.store / 'reports/000-ready.md'
        ready.parent.mkdir(exist_ok=True)
        ready.write_text('status: done\n')
        self.reply_from_orb(thread, 'notify', ready)

    def unit_on_orb(self, seq, scope, path, thread=''):
        """A routine unit dispatched to the sidekick on its own orb and finished there; the dispatch's output."""
        thread = thread or amp_thread(1)
        b = self.brief(seq, f'unit{seq}', scope, verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn('pstack-kitchen BRIEF', out)
        self.sync_on_orb(thread, 'demo-sidekick')
        self.write(path, f'unit {seq}\n')
        self.done(b, self.commit(seq))
        self.reply_from_orb(thread, 'finish', self.store / 'reports' / b.name)
        return out

    def edit_pair(self, **changes):
        doc = self.state()
        doc.update(changes)
        (self.store / 'pair.json').write_text(json.dumps(doc, indent=2) + '\n')

    def test_a_late_reply_after_a_newer_message_leaves_the_masters_state_alone(self):
        thread = amp_thread(1)
        self.ready_on_orb(thread)
        self.unit_on_orb('001', ['src/**', 'tests/**'], 'src/one.py')
        verifier = self.send_from_the_master(
            'demo-verifier', f'pstack-kitchen NOTE {self.store}/standing-orders.md'
        )
        self.sync_on_orb(verifier, 'demo-verifier')
        self.keep_store('orb-verifier')
        self.swap_in_store('master')
        # The master goes on: a docs unit rotates the sidekick to a new thread, which does it.
        b = self.brief('002', 'unit002', ['docs/**'], verify=['python3 -c 1'])
        self.assertIn(
            'rotated:', self.run_sh('dispatch', str(self.store), str(b), code=4)
        )
        shutil.rmtree(Path(self.tmp.name) / f'orb-{thread}-store')
        self.ready_on_orb(amp_thread(3))
        self.unit_on_orb('002', ['docs/**'], 'docs/two.md', amp_thread(3))
        orders = self.store / 'standing-orders.md'
        orders.write_text(
            orders.read_text()
            + '99. appended after the verifier was sent its snapshot\n'
        )
        self.edit_pair(
            consult={'file': 'consults/001-x.md', 'at': '2026-01-02T00:00:00Z'},
            added_after_the_snapshot='kept',
        )
        # A newer message to the verifier replaces the base its reply is merged against.
        self.send_from_the_master(
            'demo-verifier',
            f'pstack-kitchen NOTE {self.store}/briefs/002-unit002.md',
            thread=verifier,
        )
        before = self.state()
        steps = self.store / 'steps/002-unit002.tsv'
        steps.parent.mkdir(exist_ok=True)
        steps.write_text('1\tabc123\ta step the sidekick recorded after the snapshot\n')
        sidekick_files = {
            rel: (self.store / rel).read_bytes()
            for rel in ('reports/002-unit002.md', 'steps/002-unit002.tsv')
        }
        self.keep_store('master')
        self.swap_in_store('orb-verifier')
        verdict = self.store / 'verdicts/001-probe.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text('status: clean\nunits: 001\n')
        self.on_orb(verifier, 'notify', str(self.store), str(verdict))
        reply = self.newest_bundle(AMP_MASTER, 'demo-master')
        self.swap_in_store('master')
        self.run_sh('sync', str(reply))
        after = self.state()
        for key in ('dispatch', 'consult', 'added_after_the_snapshot'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(
            after['sidekick']['generation'], before['sidekick']['generation']
        )
        self.assertEqual(orders.read_text().splitlines()[-1][:3], '99.')
        self.assertTrue((self.store / 'briefs/002-unit002.md').exists())
        for rel, kept in sidekick_files.items():
            self.assertEqual((self.store / rel).read_bytes(), kept, rel)
        self.assertEqual(
            (self.store / 'verdicts/001-probe.md').read_text(),
            'status: clean\nunits: 001\n',
        )
        # The routine docs unit after the docs unit keeps the sidekick, as it did before the late reply.
        b = self.brief('003', 'unit003', ['docs/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn('keeps its session', out)
        self.assertNotIn('rotated:', out)

    def test_a_sidekick_on_its_own_orb_keeps_its_thread_after_a_rotation(self):
        first = amp_thread(1)
        self.ready_on_orb(first)
        self.unit_on_orb('001', ['src/**', 'tests/**'], 'src/one.py')
        b = self.brief('002', 'unit002', ['docs/**'], verify=['python3 -c 1'])
        self.assertIn(
            'rotated:', self.run_sh('dispatch', str(self.store), str(b), code=4)
        )
        shutil.rmtree(Path(self.tmp.name) / f'orb-{first}-store')
        second = amp_thread(2)
        self.ready_on_orb(second)
        self.unit_on_orb('002', ['docs/**'], 'docs/two.md', second)
        out = self.unit_on_orb('003', ['docs/**'], 'docs/three.md', second)
        self.assertIn('keeps its session', out)

    def set_compactions(self, thread, count):
        (self.fake / f'amp-context-{thread}').write_text(
            f'{{"totalTokens": 1000, "maxContextTokens": 100000, "compactionCount": {count}}}\n'
        )

    def test_a_pointer_to_setup_is_sent_only_to_a_role_that_was_sent_the_setup_before_it_compacted(
        self,
    ):
        note = lambda n: f'pstack-kitchen NOTE {self.store}/reviews/{n}.md'
        # (role, the second message, compactions when the first goes, when the second goes, whether the second starts with the pointer)
        cases = [
            ('demo-verifier', note('n2'), 0, 1, True),
            ('demo-verifier', note('n2'), 2, 2, False),
            ('demo-master', note('n2'), 0, 1, False),
            ('demo-verifier', 'a message that names no skill', 0, 1, False),
        ]
        for i, (role, second, before, after, pointer) in enumerate(cases):
            with self.subTest(role=role, second=second, before=before, after=after):
                # a new thread's id is the next one the fake amp hands out
                thread = amp_thread(2 + i)
                self.set_compactions(thread, before)
                self.assertEqual(self.send_from_the_master(role, note('n1')), thread)
                self.set_compactions(thread, after)
                self.send_from_the_master(role, second, thread)
                message = self.thread_messages(thread)[-1]
                self.assertEqual(
                    message.startswith('You compacted since the last message.'),
                    pointer,
                    message,
                )

    def test_a_message_that_carried_no_bundle_does_not_count_as_the_setup_a_thread_was_given(
        self,
    ):
        elsewhere = Path(self.tmp.name) / 'no-store'
        elsewhere.mkdir()
        thread = self.send_from_the_master(
            'demo-verifier', 'pstack-kitchen NOTE x', store=str(elsewhere)
        )
        [first] = self.thread_messages(thread)
        self.assertNotIn('pstack: before you act', first)
        self.send_from_the_master('demo-verifier', 'pstack-kitchen NOTE y', thread)
        second = self.thread_messages(thread)[-1]
        self.assertIn('run `d=~/.local/share/pstack-builds/', second)
        self.assertIn('reaches it only when you send it', second)
        self.assertEqual(self.attachments(thread).count('pstack-build.tgz'), 1)

    def test_a_refused_dispatch_records_no_delivered_sections_and_the_next_brief_carries_them_in_full(
        self,
    ):
        one = self.brief('001', 'one', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.run_sh('pause', str(self.store), '--reason', 'hold')
        self.run_sh('dispatch', str(self.store), str(one), code=None)
        self.assertNotIn('delivered', self.state()['sidekick'])
        self.assertEqual(self.briefs_in(amp_thread(1)), [])
        self.run_sh('resume', str(self.store))
        two = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(two), code=None)
        text = self.brief_text('002', 'two')
        self.assertNotIn('Unchanged from briefs/', text)
        self.assertGreater(len(self.section(text, '## Playbook:')), 1000)
        self.assertEqual(self.state()['sidekick']['delivered']['brief'], '002-two')

    READ_THE_FILE = 'Read the file ~/.agents/skills/pstack-kitchen/SKILL.md (the pstack-kitchen skill; read it as a file if your harness does not load Amp skills) and take the '

    def claude_code_roster(self, table):
        roster = Path(self.tmp.name) / 'claude-code-roster.toml'
        roster.write_text(
            '[sidekick]\nkind = "amp"\nargs = ["--model", "s/one"]\n'
            f'[{table}]\nkind = "amp"\nargs = ["--mode", "claude-code", "--features", "model:claude-sonnet-5-5"]\n'
        )
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)

    def test_a_claude_code_verifier_and_master_are_told_to_read_the_skill_file(self):
        self.claude_code_roster('verifier')
        self.verify_started('001')
        verifier = self.thread_messages(amp_thread(2))[0]
        self.assertTrue(
            verifier.startswith(
                self.READ_THE_FILE + 'verifier role. pstack-kitchen VERIFY '
            ),
            verifier,
        )
        self.claude_code_roster('master')
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master = self.thread_messages(amp_thread(3))[0]
        self.assertTrue(
            master.startswith(
                self.READ_THE_FILE + 'master role: pstack-kitchen MASTER '
            ),
            master,
        )

    def test_a_role_that_compacted_starts_its_next_message_with_a_pointer_and_gets_the_rules_in_full(
        self,
    ):
        sidekick = amp_thread(1)
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        (self.fake / f'amp-context-{sidekick}').write_text(
            '{"totalTokens": 1000, "maxContextTokens": 100000, "compactionCount": 1}\n'
        )
        for n in ('n1', 'n2'):
            self.message_from_the_master(
                'demo-sidekick', f'pstack-kitchen NOTE {self.store}/reviews/{n}.md'
            )
        *_, compacted, same = self.thread_messages(sidekick)
        rules = 'reaches it only when you send it'
        self.assertTrue(
            compacted.startswith(
                'You compacted since the last message. Re-read ~/.agents/skills/pstack-kitchen/SKILL.md (your role: sidekick), '
                f'the store {self.store} (standing-orders.md, pair.json), and the file this message names before you act.\n\n'
                f'pstack-kitchen NOTE {self.store}/reviews/n1.md'
            ),
            compacted,
        )
        self.assertIn(rules, compacted)
        # The next message at the same count is as lean as before.
        self.assertTrue(
            same.startswith(f'pstack-kitchen NOTE {self.store}/reviews/n2.md'), same
        )
        self.assertNotIn(rules, same)
        self.assertIn('Send files for the master back with', same)

    def brief_text(self, seq, slug):
        return (self.store / 'briefs' / f'{seq}-{slug}.md').read_text()

    def section(self, text, heading):
        """The text of the section of a brief whose heading starts with `heading`, without the heading line."""
        found = re.search(rf'(?ms)^{re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)', text)
        assert found is not None, heading
        return found.group(1).strip()

    POINTER = 'Unchanged from briefs/001-one.md, which this session already read; reread it there.'

    def test_a_kept_session_reads_the_playbook_and_report_sections_once(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.finish_unit('002', 'two', ['src/**', 'tests/**'], 'src/two.py')
        first, second = self.brief_text('001', 'one'), self.brief_text('002', 'two')
        self.assertGreater(len(self.section(first, '## Playbook:')), 1000)
        self.assertIn('kitchen.sh finish', self.section(first, '## Report'))
        self.assertEqual(self.section(second, '## Playbook:'), self.POINTER)
        self.assertEqual(self.section(second, '## Report'), self.POINTER)
        # The two sections are what a kept session saves, less the pointers that stand for them.
        saved = len(self.section(first, '## Playbook:')) + len(
            self.section(first, '## Report')
        )
        self.assertGreaterEqual(len(first) - len(second), saved - 2 * len(self.POINTER))
        # What the second brief says of itself is whole: its goal, scope and verify commands.
        self.assertIn('python3 -c 1', second)
        held = self.state()['sidekick']['delivered']
        self.assertEqual((held['generation'], held['brief']), ('1', '001-one'))

    def test_a_rotated_session_gets_the_playbook_and_report_sections_in_full(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        # Another area starts a fresh thread, which has read nothing.
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=4)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        self.run_sh('dispatch', str(self.store), str(b), code=None)
        text = self.brief_text('002', 'guide')
        self.assertNotIn('Unchanged from briefs/', text)
        self.assertGreater(len(self.section(text, '## Playbook:')), 1000)
        self.assertIn('kitchen.sh finish', self.section(text, '## Report'))
        held = self.state()['sidekick']['delivered']
        self.assertEqual((held['generation'], held['brief']), ('2', '002-guide'))

    def test_a_session_that_compacted_since_the_carrier_gets_the_sections_in_full(self):
        sidekick = amp_thread(1)
        b = self.brief('001', 'one', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=4)
        # No unit was finished, so nothing rotates the sidekick: its session goes on, and it compacted.
        (self.fake / f'amp-context-{sidekick}').write_text(
            '{"totalTokens": 1000, "maxContextTokens": 100000, "compactionCount": 1}\n'
        )
        c = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(c), code=None)
        text = self.brief_text('002', 'two')
        self.assertNotIn('Unchanged from briefs/', text)
        self.assertGreater(len(self.section(text, '## Playbook:')), 1000)
        self.assertEqual(self.state()['sidekick']['delivered']['compactions'], 1)
        # The carrier is the brief that holds the sections now; the next brief at the same count points to it.
        d = self.brief('003', 'three', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(d), code=None)
        self.assertEqual(
            self.section(self.brief_text('003', 'three'), '## Report'),
            'Unchanged from briefs/002-two.md, which this session already read; reread it there.',
        )

    def test_a_brief_sent_again_after_a_pointer_has_its_sections_back(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.finish_unit('002', 'two', ['src/**', 'tests/**'], 'src/two.py')
        self.assertEqual(
            self.section(self.brief_text('002', 'two'), '## Report'), self.POINTER
        )
        # Dispatch renders the brief afresh: a pointer is made again, not left half-way or lost.
        self.run_sh(
            'dispatch',
            str(self.store),
            str(self.store / 'briefs/002-two.md'),
            code=None,
        )
        self.assertEqual(
            self.section(self.brief_text('002', 'two'), '## Report'), self.POINTER
        )

    def test_a_claude_code_role_is_told_to_read_the_skill_file_and_any_other_role_to_load_the_skill(
        self,
    ):
        for slug, args, first in [
            (
                'cc',
                ['--mode', 'claude-code', '--features', 'model:claude-sonnet-5-5'],
                'Read the file ~/.agents/skills/pstack-kitchen/SKILL.md (the pstack-kitchen skill; read it as a file if your harness does not load Amp skills) and take the sidekick role (generation ',
            ),
            (
                'plain',
                ['--model', 'zhipuai/glm-5.3'],
                'Load the pstack-kitchen skill from ~/.agents/skills/pstack-kitchen/SKILL.md and take the sidekick role (generation ',
            ),
        ]:
            with self.subTest(slug=slug):
                self.run_sh('init', slug)
                store = self.store.parent / slug
                out = self.run_sh(
                    'spawn', str(store), '--kind', 'amp', '--', *args, code=4
                )
                thread = re.search(rf'amp {slug}-sidekick \((T-[^)]+)\)', out)
                assert thread is not None, out
                [bootstrap] = self.thread_messages(thread[1])
                self.assertTrue(bootstrap.startswith(first), bootstrap)

    def test_half_a_context_window_rotates_the_sidekick_even_in_the_same_area(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        (self.fake / f'amp-context-{amp_thread(1)}').write_text(
            '{"totalTokens": 50000, "maxContextTokens": 100000, "compactionCount": 0}\n'
        )
        self.next_unit_rotates('002', 'two', ['src/**', 'tests/**'])

    def test_a_compacted_sidekick_rotates_even_in_the_same_area(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        (self.fake / f'amp-context-{amp_thread(1)}').write_text(
            '{"totalTokens": 1000, "maxContextTokens": 100000, "compactionCount": 1}\n'
        )
        self.next_unit_rotates('002', 'two', ['src/**', 'tests/**'])

    def rotate_events(self):
        return [
            line.split('\t')[5]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\trotate\t' in line
        ]

    def set_context(self, text):
        (self.fake / f'amp-context-{amp_thread(1)}').write_text(text)

    def previous_brief_header(self, old, new):
        path = self.store / 'briefs/001-one.md'
        path.write_text(path.read_text().replace(old, new))

    def previous_brief_gone(self):
        (self.store / 'briefs/001-one.md').unlink()

    def rotation_says_why(self, reason, scope, setup):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        setup(self)
        b = self.brief('002', 'two', scope, verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertIn(f'rotate: {reason}\n', out)
        self.assertLess(out.index('rotate: '), out.index('rotated: '))
        self.assertEqual(self.rotate_events(), [f'generation:1 reason:{reason}'])

    def test_a_failed_over_sidekick_rotates_with_that_reason(self):
        self.sidekick_on_second_entry_with_a_unit_done()
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertIn('rotate: failed over\n', out)
        self.assertEqual(self.rotate_events(), ['generation:2 reason:failed over'])

    def test_a_manual_rotate_says_it_is_manual_in_a_line_and_in_the_rotate_event(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        out = self.run_sh('rotate', str(self.store), code=None)
        self.assertIn('rotate: manual\n', out)
        self.assertEqual(self.rotate_events(), ['generation:1 reason:manual'])

    def test_a_kept_session_prints_no_rotate_line(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        out = self.finish_unit('002', 'two', ['src/**', 'tests/**'], 'src/two.py')
        self.assertNotIn('rotate:', out)
        self.assertEqual(self.rotate_events(), [])

    def set_master_context(self, percent, compacted=0):
        """What `amp threads context` reports for the master's thread: percent of its window used and its compaction count."""
        (self.fake / f'amp-context-{AMP_MASTER}').write_text(
            f'{{"totalTokens": {round(percent * 1000)}, "maxContextTokens": 100000, "compactionCount": {compacted}}}\n'
        )

    def master_rotate_reason(self):
        """kitchen_core.master's master_context and master_rotate_reason for the run's store, as the master's own thread asks them."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(skill / "scripts")!r}, {str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from kitchen_core import master\n'
            f'print(master.master_context({str(self.store)!r}), '
            f'repr(master.master_rotate_reason({str(self.store)!r})))\n'
        )
        return subprocess.run(
            [sys.executable, '-I', '-c', code],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()

    def test_rotate_due_is_decided_by_the_masters_own_context(self):
        # (percent used, compactions, KITCHEN_MASTER_CONTEXT_PCT, what master_context and master_rotate_reason answer)
        table = [
            (59, 0, None, "0.59 ''"),
            (60, 0, None, "0.6 ''"),
            (60.4, 0, None, "0.604 ''"),
            (61, 0, None, "0.61 'context:61%'"),
            (55, 0, '50', "0.55 'context:55%'"),
            (10, 1, None, "1.0 'compacted'"),
        ]
        for percent, compacted, limit, expected in table:
            with self.subTest(percent=percent, compacted=compacted, limit=limit):
                self.set_master_context(percent, compacted)
                if limit:
                    self.env['KITCHEN_MASTER_CONTEXT_PCT'] = limit
                try:
                    self.assertEqual(self.master_rotate_reason(), expected)
                finally:
                    self.env.pop('KITCHEN_MASTER_CONTEXT_PCT', None)
        with self.subTest('a master with no registered thread'):
            state = self.state()
            state['master']['name'] = 'nobody'
            (self.store / 'pair.json').write_text(json.dumps(state))
            self.assertEqual(self.master_rotate_reason(), "None ''")

    ROTATE_LINE = 'master: your thread has used 61% of its context; rotate before dispatching: kitchen.sh new-handoff '

    def rotate_due_events(self):
        return [
            line
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\trotate-due\t' in line
        ]

    def test_a_master_over_sixty_percent_is_refused_and_logs_one_rotate_due(self):
        self.set_master_context(61)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        for _ in range(2):
            out = self.run_sh('dispatch', str(self.store), str(b), code=9)
            self.assertIn(self.ROTATE_LINE + f'{self.store}, fill it, ', out)
            self.assertIn(
                f'kitchen.sh rotate-master {self.store} [--model <provider/model>]', out
            )
        self.assertEqual(self.briefs_in(amp_thread(1)), [])
        [row] = self.rotate_due_events()
        self.assertTrue(row.endswith('\tmaster\trotate-due\t-\tcontext:61%'), row)
        due = self.state()['master']['rotate_due']
        self.assertEqual((due['reason'], due['generation']), ('context:61%', 1))

    def test_a_master_at_fifty_nine_percent_dispatches(self):
        self.set_master_context(59)
        self.run_sh(
            'dispatch',
            str(self.store),
            str(self.brief('001', 'util', ['src/**', 'tests/**'])),
            code=4,
        )
        self.assertEqual(len(self.briefs_in(amp_thread(1))), 1)
        self.assertEqual(self.rotate_due_events(), [])

    def test_a_compacted_master_is_refused_at_any_percentage(self):
        self.set_master_context(5, compacted=1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=9)
        self.assertIn(
            'master: your thread has compacted; rotate before dispatching: ', out
        )
        self.assertTrue(self.rotate_due_events()[0].endswith('\tcompacted'))

    def test_discuss_and_queue_are_refused_too_but_a_queue_clear_is_not(self):
        sent = len(self.thread_messages(amp_thread(1)))
        self.set_master_context(61)
        plan = self.store / 'plans/001-p.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan\n\nrisk: routine\n')
        out = self.run_sh('discuss', str(self.store), str(plan), code=9)
        self.assertIn(self.ROTATE_LINE, out)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        out = self.run_sh('queue', str(self.store), str(b), code=9)
        self.assertIn(self.ROTATE_LINE, out)
        self.assertNotIn(
            'rotate before', self.run_sh('queue', str(self.store), '--clear')
        )
        self.assertEqual(len(self.thread_messages(amp_thread(1))), sent)

    def test_the_warn_gate_prints_the_line_and_dispatches(self):
        self.set_master_context(61)
        self.env['KITCHEN_MASTER_CONTEXT_GATE'] = 'warn'
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertIn(self.ROTATE_LINE, out)
        self.assertEqual(len(self.briefs_in(amp_thread(1))), 1)

    def registry(self, home=None):
        """The Amp registry of an orb whose XDG_STATE_HOME is home (this one by default)."""
        home = Path(home or self.env['XDG_STATE_HOME'])
        return home / 'pstack/amp/registry.json'

    def test_only_the_masters_own_thread_rotates_the_master(self):
        """Counter panes (amp:N) mean different agents in different orbs, so the master is known by its thread: the registry here gives the
        master the counter pane amp:1 the sidekick has, and pair.json a counter pane no one has."""
        sidekick = amp_thread(1)
        registry = json.loads(self.registry().read_text())
        registry['agents']['demo-master']['pane'] = 'amp:1'
        self.registry().write_text(json.dumps(registry))
        doc = self.state()
        doc['master']['pane_id'] = 'amp:3'
        (self.store / 'pair.json').write_text(json.dumps(doc))
        self.filled_handoff()
        out = self.as_thread(sidekick, 'rotate-master', str(self.store), code=5)
        self.assertIn(
            f"rotate-master: this thread is not the run's master ({sidekick}); only the master rotates",
            out,
        )
        self.assertNotIn('previous', self.state()['master'])
        self.assertFalse((self.fake / 'amp-threads' / amp_thread(2)).exists())
        self.run_sh('rotate-master', str(self.store))
        self.assertEqual(self.state()['master']['previous']['name'], 'demo-master')
        self.assertEqual(len(self.thread_messages(amp_thread(2))), 1)

    def test_take_master_in_the_new_thread_from_the_bundle_it_received(self):
        """The bundle is packed when the next master's thread starts, before the old master records that thread's pane: the new master syncs
        a store whose .master.pane_id is still the counter the pane had before its thread existed."""
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        [bundle] = (self.fake / 'amp-threads' / master2).glob(
            '1-pstack-demo-master-g2.tgz'
        )
        self.as_thread(master2, 'sync', str(bundle))
        self.assertEqual(self.state()['master']['pane_id'], 'amp:2')
        # The new thread's orb checks the run out elsewhere, so the run's own directory does not place it.
        elsewhere = Path(self.tmp.name) / 'orb2-checkout'
        elsewhere.mkdir()
        out = self.as_thread(master2, 'take-master', str(self.store), cwd=elsewhere)
        self.assertIn(f'master: demo-master in amp:{master2} (generation 2)', out)
        self.assertEqual(self.state()['master']['pane_id'], f'amp:{master2}')
        self.assertNotIn('previous', self.state()['master'])

    ROSTER = textwrap.dedent(
        """\
        [[sidekick.alternate]]
        kind = "amp"
        args = ["--model", "zhipuai/glm-5.3"]
        [[sidekick.alternate]]
        kind = "amp"
        args = ["--model", "anthropic/claude-sonnet-5-5"]
        [verifier]
        kind = "amp"
        args = ["--model", "anthropic/claude-sonnet-5-5", "--orb-size", "a1.small"]
        [[verifier.fallback]]
        kind = "amp"
        args = ["--model", "openai/gpt-6.1-sol"]
        [[verifier.fallback]]
        kind = "amp"
        args = ["--model", "google-vertex/gemini-4-hydrogen"]
        """
    )

    def roster_snapshot(self):
        return self.store / 'sessions/master-g2-roster.toml'

    def test_rotate_master_snapshots_the_roster_it_reads_into_the_store(self):
        """The next master's orb may hold no roster: the old master keeps the TOML bytes of the one it reads (PSTACK_KITCHEN_ROSTER or the home
        one) in the store, and the hand-over names the file."""
        roster = Path(self.tmp.name) / 'host-roster.toml'
        roster.write_text(self.ROSTER)
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertEqual(self.roster_snapshot().read_text(), self.ROSTER)
        self.assertEqual(
            self.state()['master']['previous']['roster'],
            'sessions/master-g2-roster.toml',
        )

    def test_rotate_master_without_a_roster_records_that_there_is_none(self):
        self.env['PSTACK_KITCHEN_ROSTER'] = str(Path(self.tmp.name) / 'absent.toml')
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertFalse(self.roster_snapshot().exists())
        self.assertEqual(self.state()['master']['previous']['roster'], '')

    def test_rotate_master_refuses_a_roster_it_cannot_snapshot(self):
        """Nothing is snapshotted and the hand-over is not opened; the refusal names the file and the kind, never the line."""
        secret = 'xoxb-' + '1a2b3c4d5e' * 3
        bad = Path(self.tmp.name) / 'bad-roster.toml'
        for label, write, said in (
            (
                'parse',
                lambda: bad.write_text('[sidekick\n'),
                ['rotate-master: the roster', 'does not parse: '],
            ),
            (
                'secret',
                lambda: bad.write_text(f'[sidekick]\nSLACK_TOKEN = "{secret}"\n'),
                [
                    'rotate-master: the roster',
                    'holds a credential-shaped line (env-secret, slack-token); nothing was snapshotted',
                ],
            ),
            (
                'binary',
                lambda: bad.write_bytes(b'\xff\xfe'),
                ['rotate-master: the roster', 'is not UTF-8 text'],
            ),
            ('unreadable', bad.mkdir, ['rotate-master: cannot read the roster']),
        ):
            with self.subTest(label):
                shutil.rmtree(bad, ignore_errors=True)
                bad.unlink(missing_ok=True)
                write()
                self.env['PSTACK_KITCHEN_ROSTER'] = str(bad)
                self.filled_handoff()
                out = self.run_sh(
                    'rotate-master',
                    str(self.store),
                    '--model',
                    'anthropic/claude-opus-5-5',
                    code=5,
                )
                self.assertIn(str(bad), out)
                for text in said:
                    self.assertIn(text, out)
                self.assertNotIn(secret, out)
                self.assertFalse(self.roster_snapshot().exists())
                self.assertNotIn('previous', self.state()['master'])

    def test_take_master_installs_the_snapshotted_roster_only_where_the_orb_has_none(
        self,
    ):
        """The next master's orb has its own roster path (PSTACK_KITCHEN_ROSTER); the old master's file is never read there, and a file the orb
        holds is never replaced."""
        sender = Path(self.tmp.name) / 'host-roster.toml'
        sender.write_text(self.ROSTER)
        self.env['PSTACK_KITCHEN_ROSTER'] = str(sender)
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        snapshot = self.roster_snapshot()
        handed = (self.store / 'pair.json').read_text()
        master2 = amp_thread(2)
        orb = Path(self.tmp.name) / 'orb2'
        orb.mkdir()
        mine = orb / 'orb2-roster.toml'
        other = '[consultant]\nkind = "codex"\n'

        def take(receiver, snapshot_recorded=True):
            """A fresh hand-over (pair.json as rotate-master left it) taken by the next master on an orb holding `receiver` (None: no roster)."""
            doc = json.loads(handed)
            if not snapshot_recorded:
                del doc['master']['previous']['roster']
            (self.store / 'pair.json').write_text(json.dumps(doc))
            mine.unlink(missing_ok=True)
            if receiver is not None:
                mine.write_text(receiver)
            return self.as_thread(
                master2,
                'take-master',
                str(self.store),
                cwd=orb,
                env={'PSTACK_KITCHEN_ROSTER': str(mine)},
            )

        with self.subTest('absent'):
            out = take(None)
            self.assertIn(f'roster: installed {mine} from {snapshot}', out)
            self.assertEqual(mine.read_bytes(), self.ROSTER.encode())
            self.assertEqual(sorted(p.name for p in orb.iterdir()), [mine.name])
        with self.subTest('identical'):
            self.assertNotIn('roster:', take(self.ROSTER))
            self.assertEqual(mine.read_text(), self.ROSTER)
        with self.subTest('different'):
            out = take(other)
            self.assertIn(f'roster: kept {mine}; it differs from {snapshot}', out)
            self.assertNotIn('codex', out)
            self.assertEqual(mine.read_text(), other)
        with self.subTest('no snapshot recorded'):
            out = take(None, snapshot_recorded=False)
            self.assertIn('roster: none recorded by the previous master', out)
            self.assertFalse(mine.exists())
        with self.subTest('unreadable'):
            mine.unlink(missing_ok=True)
            doc = json.loads(handed)
            (self.store / 'pair.json').write_text(json.dumps(doc))
            mine.mkdir()
            out = self.as_thread(
                master2,
                'take-master',
                str(self.store),
                cwd=orb,
                env={'PSTACK_KITCHEN_ROSTER': str(mine)},
                code=1,
            )
            self.assertIn('take-master: cannot read the roster', out)
            self.assertIn('previous', self.state()['master'])
            mine.rmdir()
        with self.subTest('a rerun keeps what it installed and the user then edited'):
            take(None)
            mine.write_text(other)
            (self.store / 'pair.json').write_text(handed)
            out = self.as_thread(
                master2,
                'take-master',
                str(self.store),
                cwd=orb,
                env={'PSTACK_KITCHEN_ROSTER': str(mine)},
            )
            self.assertIn(f'roster: kept {mine}', out)
            self.assertEqual(mine.read_text(), other)
        self.assertEqual(sender.read_text(), self.ROSTER)

    def test_take_master_without_a_roster_on_the_old_master_installs_nothing(self):
        self.env['PSTACK_KITCHEN_ROSTER'] = str(Path(self.tmp.name) / 'absent.toml')
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        mine = Path(self.tmp.name) / 'orb2-roster.toml'
        out = self.as_thread(
            amp_thread(2),
            'take-master',
            str(self.store),
            env={'PSTACK_KITCHEN_ROSTER': str(mine)},
        )
        self.assertNotIn('roster:', out)
        self.assertFalse(mine.exists())

    def test_the_old_masters_bundle_is_a_hand_over_at_the_first_sync_and_while_it_is_pending(
        self,
    ):
        """The bundle is from `demo-master` to `demo-master-g2`; once the next master holds its pending hand-over, a store of its own, the
        same bundle is still the old master's whole store, not a role's reply that must leave what the master owns alone."""
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        [bundle] = (self.fake / 'amp-threads' / master2).glob(
            '1-pstack-demo-master-g2.tgz'
        )
        orders = self.store / 'standing-orders.md'
        sent = orders.read_text()
        for label in ('first sync', 'pending hand-over'):
            with self.subTest(label):
                orders.write_text('1. the new master edited this\n')
                out = self.as_thread(master2, 'sync', str(bundle))
                self.assertNotIn("kept the master's copy", out)
                self.assertEqual(orders.read_text(), sent)
        self.assertEqual(self.state()['master']['previous']['name'], 'demo-master')

    def test_take_master_from_another_thread_than_the_masters_pane_is_refused(self):
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        self.assertEqual(self.state()['master']['pane_id'], f'amp:{master2}')
        out = self.as_thread(amp_thread(1), 'take-master', str(self.store), code=5)
        self.assertIn(
            f"take-master runs from the new master's pane amp:{master2}, not amp:{amp_thread(1)}",
            out,
        )
        self.assertEqual(self.state()['master']['previous']['name'], 'demo-master')

    def test_take_master_records_the_masters_thread_as_its_pane_in_every_orb(self):
        """The next master's pane is its thread's, whatever counter pane an orb's registry gave it, so the next role started there gets another."""
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        self.assertEqual(self.state()['master']['pane_id'], f'amp:{master2}')
        # What another orb's registry holds after a sync: the master under a counter pane of that orb.
        orb = Path(self.tmp.name) / 'orb2'
        registry = self.registry(orb)
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(
                {
                    'next': 2,
                    'panes': ['amp:1'],
                    'agents': {
                        'demo-master-g2': {
                            'pane': 'amp:1',
                            'kind': 'amp',
                            'args': [],
                            'thread': master2,
                        }
                    },
                }
            )
        )
        doc = self.state()
        doc['master']['pane_id'] = 'amp:1'
        (self.store / 'pair.json').write_text(json.dumps(doc))
        self.as_thread(
            master2, 'take-master', str(self.store), env={'XDG_STATE_HOME': str(orb)}
        )
        self.assertEqual(self.state()['master']['pane_id'], f'amp:{master2}')
        agents = json.loads(registry.read_text())['agents']
        self.assertEqual(
            {n: e['pane'] for n, e in agents.items()},
            {'demo-master': f'amp:{master2}'},
        )
        # The master2 thread now rotates the run; the registry's counter pane for it no longer decides who the master is.
        self.filled_handoff()
        self.as_thread(
            master2, 'rotate-master', str(self.store), env={'XDG_STATE_HOME': str(orb)}
        )
        self.assertEqual(self.state()['master']['previous']['name'], 'demo-master')

    def test_rotate_master_clears_the_gate_for_the_next_master(self):
        self.set_master_context(61)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.run_sh('dispatch', str(self.store), str(b), code=9)
        self.assertIn('rotate_due', self.state()['master'])
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        self.assertNotIn('rotate_due', self.state()['master'])
        master2 = amp_thread(2)
        self.as_thread(master2, 'take-master', str(self.store))
        self.assertNotIn('rotate_due', self.state()['master'])
        out = self.as_thread(master2, 'dispatch', str(self.store), str(b), code=4)
        self.assertNotIn('rotate before dispatching', out)
        self.assertEqual(len(self.briefs_in(amp_thread(1))), 1)
        self.assertEqual(len(self.rotate_due_events()), 1)

    def test_a_sidekicks_finish_and_a_verdicts_handoff_say_the_master_is_due(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.dispatch(b)
        self.set_master_context(61)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('util'))
        out = self.as_thread(
            amp_thread(1),
            'finish',
            str(self.store),
            str(self.store / 'reports' / b.name),
        )
        self.assertIn(
            "note for the master (the sidekick does nothing): the master's thread has used 61% of its context; its next dispatch is refused until it rotates",
            out,
        )
        self.assertNotIn('rotate-master', out)
        self.assertNotIn('new-handoff', out)
        self.assertNotIn('rotate before dispatching', out)
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(skill / "scripts")!r}, {str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from kitchen_core import verify_state\n'
            'said = []\n'
            f'verify_state.handoff_due({str(self.store)!r}, said.append)\n'
            "print(''.join(said))\n"
        )
        said = subprocess.run(
            [sys.executable, '-I', '-c', code],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
        self.assertIn(self.ROTATE_LINE, said)

    def test_a_unit_in_another_area_rotates_the_sidekick(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.next_unit_rotates('002', 'guide', ['docs/**'])

    PRIMARY = ['--model', 'zhipuai/glm-5.3', '--orb-size', 'a1.small']

    def sidekick_on_second_entry_with_a_unit_done(self):
        """The sidekick failed over mid-unit to the second entry of its chain (thread 2) and finished the unit there."""
        pair = self.state()
        second = {'kind': 'amp', 'args': ['--model', 'b/two']}
        pair['sidekick']['chain'] = [{'kind': 'amp', 'args': self.PRIMARY}, second]
        pair['sidekick']['chain_index'] = 0
        pair['sidekick']['fallback'] = second
        (self.store / 'pair.json').write_text(json.dumps(pair))
        first = self.brief(
            '001', 'one', ['src/**', 'tests/**'], verify=['python3 -c 1']
        )
        self.dispatch(first)
        self.run_sh('failover', str(self.store), '--reason', 'limit', code=4)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        # The failed-over thread takes the unit again, so it is the generation that finishes it.
        self.run_sh('dispatch', str(self.store), str(first), code=None)
        self.write('src/one.py', 'one\n')
        self.done(self.store / 'briefs/001-one.md', self.commit('one'))
        self.as_thread(
            amp_thread(2),
            'finish',
            str(self.store),
            str(self.store / 'reports/001-one.md'),
        )
        self.assertEqual(self.state()['sidekick']['chain_index'], 1)

    def failover_and_failback_events(self):
        return [
            line.split('\t')[3:6]
            for line in (self.store / 'events.tsv').read_text().splitlines()
            if '\tmaster\tfailover\t' in line or '\tmaster\tfailback\t' in line
        ]

    def test_the_unit_after_a_failover_starts_the_sidekick_on_the_top_of_its_chain_again(
        self,
    ):
        self.sidekick_on_second_entry_with_a_unit_done()
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertNotIn('kept:', out)
        self.assertIn('failback: the sidekick returns to amp zhipuai/glm-5.3', out)
        started = [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if '--title demo-sidekick' in line and ' -ox ' in line
        ]
        self.assertEqual(len(started), 3, started)
        self.assertIn('--model zhipuai/glm-5.3 ', started[2])
        self.assertEqual(
            self.failover_and_failback_events(),
            [
                ['failover', '-', 'amp zhipuai/glm-5.3:amp b/two'],
                ['failback', '-', 'amp b/two:amp zhipuai/glm-5.3'],
            ],
        )
        self.assertEqual(self.state()['sidekick']['chain_index'], 0)

    def test_a_top_of_the_chain_that_still_fails_keeps_the_sidekick_one_down_without_a_failback(
        self,
    ):
        self.sidekick_on_second_entry_with_a_unit_done()
        (self.fake / 'amp-preflight-fail').write_text('zhipuai/glm-5.3\n')
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertNotIn('failback', out)
        started = [
            line
            for line in (self.fake / 'amp.log').read_text().splitlines()
            if '--title demo-sidekick' in line and ' -ox ' in line
        ]
        self.assertIn('--model b/two ', started[2])
        self.assertEqual(
            self.failover_and_failback_events(),
            [
                ['failover', '-', 'amp zhipuai/glm-5.3:amp b/two'],
                ['failover', '-', 'amp zhipuai/glm-5.3:amp b/two'],
            ],
        )
        self.assertEqual(self.state()['sidekick']['chain_index'], 1)

    def test_a_dispatch_after_rotate_and_the_new_threads_ready_is_sent(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.assertIs(self.state()['sidekick']['rotation_required'], True)
        self.run_sh('rotate', str(self.store), code=4)
        self.assertIs(self.state()['sidekick']['rotation_required'], False)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertNotIn('needs a fresh session', out)
        self.assertEqual(len(self.briefs_in(amp_thread(2))), 1)

    def test_a_dispatch_after_a_failover_that_followed_a_done_unit_is_sent_once_ready(
        self,
    ):
        pair = self.state()
        second = {'kind': 'amp', 'args': ['--model', 'b/two']}
        pair['sidekick']['chain'] = [{'kind': 'amp', 'args': self.PRIMARY}, second]
        pair['sidekick']['chain_index'] = 0
        pair['sidekick']['fallback'] = second
        (self.store / 'pair.json').write_text(json.dumps(pair))
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        self.run_sh('failover', str(self.store), '--reason', 'limit', code=4)
        self.assertIs(self.state()['sidekick']['rotation_required'], False)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        b = self.brief('002', 'two', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertNotIn('needs a fresh session', out)
        self.assertEqual(len(self.briefs_in(amp_thread(2))), 1)

    def test_a_dispatch_after_a_rotation_is_sent_once_the_new_thread_is_ready(self):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=4)
        self.assertIn(
            f'rotated: a new sidekick thread is starting; run dispatch {b} again after its READY',
            out,
        )
        self.assertNotIn('rotating the sidekick before this brief failed', out)
        self.assertEqual(self.briefs_in(amp_thread(2)), [])
        self.assertIs(self.state()['sidekick']['rotation_required'], False)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertNotIn('needs a fresh session', out)
        self.assertEqual(len(self.briefs_in(amp_thread(2))), 1)

    def test_the_first_send_only_dispatch_after_ready_waits_out_the_ready_turn_and_sends(
        self,
    ):
        self.finish_unit('001', 'one', ['src/**', 'tests/**'], 'src/one.py')
        b = self.brief('002', 'guide', ['docs/**'], verify=['python3 -c 1'])
        self.run_sh('dispatch', str(self.store), str(b), code=4)
        (self.store / 'reports/000-ready.md').write_text('status: done\n')
        self.set_state(amp_thread(1), 'idle', '2999-01-01T00:00:00.000Z')
        # The new thread's turn that wrote READY has not ended: the fake reports it idle from its Nth export on.
        self.set_state(amp_thread(2), 'working', '2999-01-01T00:00:00.000Z')
        shim = self.fake / 'shim'
        shim.mkdir()
        (shim / 'amp').write_text(
            '#!/bin/bash\n'
            f'if [ "$1 $2 $3" = "threads export {amp_thread(2)}" ]; then\n'
            f'  echo >>"$FAKE/exports"; [ "$(wc -l <"$FAKE/exports")" -ge $FLIP_AT ] && echo "idle 2999-01-01T00:00:00.000Z" >"$FAKE/amp-state/{amp_thread(2)}"\n'
            'fi\n'
            f'exec {root / "tests/pstack-pair/bin/amp"} "$@"\n'
        )
        (shim / 'amp').chmod(0o755)
        self.env.update(
            PATH=f'{shim}:{self.env["PATH"]}',
            FLIP_AT='1000',
            PAIR_READY_POLL_S='0',
            PAIR_ROTATE_READY_S='0',
        )
        out = self.run_sh('dispatch', str(self.store), str(b), '--send-only', code=5)
        self.assertIn('sidekick demo-sidekick is working', out)
        self.assertNotIn('sent: BRIEF', out)
        self.assertEqual(self.briefs_in(amp_thread(2)), [])
        (self.fake / 'exports').unlink()
        self.env.update(FLIP_AT='6', PAIR_ROTATE_READY_S='30')
        out = self.run_sh('dispatch', str(self.store), str(b), '--send-only', code=0)
        self.assertIn(f'sent: BRIEF {b}', out)
        self.assertEqual(self.state()['dispatch']['brief'], str(b))
        self.assertEqual(len(self.briefs_in(amp_thread(2))), 1)

    def test_a_working_sidekick_that_already_took_a_brief_of_its_generation_is_refused_without_polling(
        self,
    ):
        sidekick = amp_thread(1)
        pair = self.state()
        pair['dispatch'] = {'generation': pair['sidekick']['generation']}
        (self.store / 'pair.json').write_text(json.dumps(pair))
        self.set_state(sidekick, 'working', '2999-01-01T00:00:00.000Z')
        # A thread that turns idle on its third export, which a wait for the turn to end would reach.
        shim = self.fake / 'shim'
        shim.mkdir()
        (shim / 'amp').write_text(
            '#!/bin/bash\n'
            f'if [ "$1 $2 $3" = "threads export {sidekick}" ]; then\n'
            f'  echo >>"$FAKE/exports"; [ "$(wc -l <"$FAKE/exports")" -ge 3 ] && echo "idle 2999-01-01T00:00:00.000Z" >"$FAKE/amp-state/{sidekick}"\n'
            'fi\n'
            f'exec {root / "tests/pstack-pair/bin/amp"} "$@"\n'
        )
        (shim / 'amp').chmod(0o755)
        self.env.update(
            PATH=f'{shim}:{self.env["PATH"]}',
            PAIR_READY_POLL_S='0',
            PAIR_ROTATE_READY_S='30',
        )
        b = self.brief('002', 'util', ['src/**', 'tests/**'])
        out = self.run_sh('dispatch', str(self.store), str(b), '--send-only', code=5)
        self.assertIn('demo-sidekick is working', out)
        self.assertEqual(self.briefs_in(sidekick), [])
        # The dispatch's own status reads are two; a poll would make a third and turn the thread idle.
        self.assertLess(len((self.fake / 'exports').read_text().splitlines()), 3)

    def update_as(self, thread, store, cwd=None, env=None, mux='amp'):
        """Runs store.update from the thread's own process, setting .probe in pair.json: (exit code, output)."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core import store\n'
            f'store.update({str(store)!r}, lambda doc: doc.__setitem__("probe", 1))\n'
        )
        env = {**self.env, **(env or {}), 'AMP_THREAD_ID': thread}
        env.pop('PSTACK_MUX')
        result = subprocess.run(
            [sys.executable, '-I', '-c', code],
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return result.returncode, result.stdout + result.stderr

    def test_a_role_thread_may_write_pair_json_under_the_local_backend(self):
        code, out = self.update_as(amp_thread(1), self.store)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state()['probe'], 1)

    def set_state(self, thread, state, at='2026-01-01T00:00:00.000Z'):
        """The lastKnownAgentState `amp threads export` reports for thread."""
        (self.fake / 'amp-state').mkdir(exist_ok=True)
        (self.fake / 'amp-state' / thread).write_text(f'{state} {at}\n')

    def set_turn_ends(self, thread, *times):
        """The turn ends (ISO times) `amp threads export` reports for thread: assistant messages that stopped on end_turn."""
        (self.fake / 'amp-turn-ends').mkdir(exist_ok=True)
        (self.fake / 'amp-turn-ends' / thread).write_text('\n'.join(times) + '\n')

    def briefs_in(self, thread):
        return [m for m in self.thread_messages(thread) if 'pstack-kitchen BRIEF' in m]

    def stops_in(self, thread):
        return [m for m in self.thread_messages(thread) if 'pstack-kitchen STOP' in m]

    def relays_dir(self):
        return Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/relays'

    def relay_of(self, text, name='demo-sidekick'):
        """(the record, how) of Amp.relay for the run's store, as the master's own thread asks it; (None, '') when there is nothing to relay with."""
        code = (
            'import json, sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp\n'
            f'print(json.dumps(amp.Amp().relay({name!r}, {text!r}, {str(self.store)!r})))\n'
        )
        out = subprocess.run(
            [sys.executable, '-I', '-c', code],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        return json.loads(out.splitlines()[-1])

    def test_a_relay_is_a_record_and_a_bundle_under_git_and_the_same_text_is_relayed_once(
        self,
    ):
        sidekick = amp_thread(1)
        self.set_state(sidekick, 'tool_use')
        record, how = self.relay_of('STEER one')
        self.assertEqual(how, 'made')
        rid = record['id']
        self.assertEqual(
            (record['state'], record['name'], record['thread'], record['store']),
            ('relay-pending', 'demo-sidekick', sidekick, str(self.store)),
        )
        self.assertEqual(record['upload_to'], f'.git/pstack-relay-{rid}.tgz')
        bundle = self.repo / '.git/pstack-relay' / f'{rid}.tgz'
        self.assertEqual(record['bundle'], str(bundle))
        self.assertTrue(bundle.is_file())
        message = Path(record['message']).read_text()
        self.assertEqual(Path(record['message']), self.relays_dir() / f'{rid}.md')
        self.assertTrue(message.startswith('STEER one\n\npstack: before you act'))
        self.assertIn(
            f'`PSTACK_MUX=amp ~/.agents/skills/pstack-pair/scripts/pair.sh sync {self.repo}/.git/pstack-relay-{rid}.tgz`',
            message,
        )
        self.assertTrue(message.endswith(f'pstack-msg: {rid}'))
        self.assertEqual(
            json.loads((self.relays_dir() / f'{rid}.json').read_text()), record
        )
        # Nothing went through the CLI, and the same text is not relayed twice while it waits.
        self.assertEqual(self.thread_messages(sidekick)[1:], [])
        self.assertEqual(self.relay_of('STEER one'), [record, 'pending'])
        self.assertEqual(len(list(self.relays_dir().glob('*.json'))), 1)
        # Once the master has sent it, the same text waits in the turn it was sent into; a new text is its own relay.
        done = {**record, 'state': 'relay-done', 'done_at': 1790000000}
        (self.relays_dir() / f'{rid}.json').write_text(json.dumps(done))
        self.set_state(sidekick, 'tool_use', '2026-09-01T00:00:00.000Z')
        self.assertEqual(self.relay_of('STEER one'), [done, 'delivered'])
        self.assertEqual(self.relay_of('STEER two')[1], 'made')
        # A state change after the send (a tool call, then streaming) is still the same turn: the relay is not made a second time.
        self.set_state(sidekick, 'streaming', '2026-11-18T00:00:00.000Z')
        self.set_turn_ends(sidekick, '2026-08-01T00:00:00.000Z')
        self.assertEqual(self.relay_of('STEER one'), [done, 'delivered'])
        # A turn that ended after the send read the relay at its end (or never will); the same text is its own new relay.
        self.set_turn_ends(
            sidekick, '2026-08-01T00:00:00.000Z', '2026-10-01T00:00:00.000Z'
        )
        fresh, how = self.relay_of('STEER one')
        self.assertEqual(how, 'made')
        self.assertNotEqual(fresh['id'], rid)
        self.set_state(sidekick, 'idle', '2999-01-01T00:00:00.000Z')
        again, how = self.relay_of('STEER one')
        self.assertEqual(how, 'made')
        self.assertNotEqual(again['id'], rid)

    def test_a_thread_in_a_turn_is_working_and_a_brief_is_not_queued_behind_it(self):
        sidekick = amp_thread(1)
        self.set_state(sidekick, 'tool_use')
        self.assertIn(
            'demo-sidekick (amp:1) working', self.run_sh('status', str(self.store))
        )
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn('demo-sidekick is working', out)
        self.assertEqual(self.briefs_in(sidekick), [])

    def working_sidekick_steer(self):
        """The path of a steer for the sidekick, whose thread is in a turn."""
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.set_state(amp_thread(1), 'tool_use', '2026-01-01T00:00:00.000Z')
        steer = self.store / 'steers/001-util-s1.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text(
            '# Steer 001-s1: util\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n'
        )
        return steer

    def test_a_steer_to_a_working_thread_is_relayed_through_the_master_once(self):
        sidekick = amp_thread(1)
        steer = self.working_sidekick_steer()
        before = self.thread_messages(sidekick)
        for flags in ([], ['--interrupt']):
            with self.subTest(flags=flags):
                out = self.run_sh('steer', str(self.store), str(steer), *flags)
                [record_file] = self.relays_dir().glob('*.json')
                record = json.loads(record_file.read_text())
                rid = record['id']
                self.assertEqual(record['state'], 'relay-pending')
                first = f'relay-pending: {rid} for demo-sidekick ({sidekick})'
                lines = out.splitlines()
                self.assertEqual(
                    lines[-4:],
                    [
                        first + (' (already pending; not made again)' if flags else ''),
                        f'relay: upload_thread_file path={self.repo}/.git/pstack-relay/{rid}.tgz destination=.git/pstack-relay-{rid}.tgz thread={sidekick}',
                        f'relay: send_thread_message to {sidekick} with the text of {self.relays_dir()}/{rid}.md',
                        f'relay: then run kitchen.sh relay-done {self.store} {rid}',
                    ],
                )
                self.assertIn(
                    f'pstack-kitchen STEER {steer}', Path(record['message']).read_text()
                )
                # Nothing went through the CLI, and the steer is logged once.
                self.assertEqual(self.thread_messages(sidekick), before)
        events = (self.store / 'events.tsv').read_text()
        self.assertEqual(events.count('send-steer\t001-util-s1\trelay-pending'), 1)

    def test_a_stop_to_a_working_thread_is_relayed_through_the_master_once(self):
        """stop to a sidekick whose Amp thread is in a turn would only queue behind it; like a steer, the master relays the STOP with its own
        tools, so stop prints the relay lines, names the role working and queues nothing through the CLI, and the trailing report line is
        left out: the newest report in the store is older than the stop the sidekick has not read, not an answer to it."""
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('util'))
        self.set_state(amp_thread(1), 'tool_use', '2026-01-01T00:00:00.000Z')
        before = self.thread_messages(sidekick)
        out = self.run_sh('stop', str(self.store))
        [record_file] = self.relays_dir().glob('*.json')
        record = json.loads(record_file.read_text())
        rid = record['id']
        self.assertEqual(record['state'], 'relay-pending')
        self.assertIn(f'relay-pending: {rid} for demo-sidekick ({sidekick})', out)
        self.assertIn(
            f'relay: upload_thread_file path={self.repo}/.git/pstack-relay/{rid}.tgz destination=.git/pstack-relay-{rid}.tgz thread={sidekick}',
            out,
        )
        self.assertIn(
            f'relay: send_thread_message to {sidekick} with the text of {self.relays_dir()}/{rid}.md',
            out,
        )
        self.assertIn(f'relay: then run kitchen.sh relay-done {self.store} {rid}', out)
        self.assertIn('sidekick_state: working', out)
        self.assertNotIn('report:', out)
        self.assertIn(
            f'pstack-kitchen STOP {self.store}', Path(record['message']).read_text()
        )
        # Nothing went through the CLI, and the stop relay is logged once; the role was never sent the STOP by the CLI either.
        self.assertEqual(self.thread_messages(sidekick), before)
        events = (self.store / 'events.tsv').read_text()
        self.assertEqual(events.count('send-stop'), 1)
        self.assertEqual(events.count('send-stop\t-\tsidekick relay-pending'), 1)
        # A second stop while the relay waits makes no second relay and no second row.
        again = self.run_sh('stop', str(self.store))
        self.assertIn(
            f'relay-pending: {rid} for demo-sidekick ({sidekick}) (already pending; not made again)',
            again,
        )
        self.assertEqual(list(self.relays_dir().glob('*.json')), [record_file])
        self.assertEqual((self.store / 'events.tsv').read_text(), events)
        self.assertEqual(self.thread_messages(sidekick), before)

    def test_a_stop_whose_relay_cannot_be_made_falls_back_to_the_queued_send(self):
        """No .git to hold the bundle, or a bundle over upload_thread_file's limit: nothing is relayed, and the STOP goes to the thread through
        the CLI, where it queues behind the turn."""
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('util'))
        self.set_state(sidekick, 'tool_use', '2026-01-01T00:00:00.000Z')
        self.env['PAIR_STOP_DEADLINE_S'] = '0'
        pristine = self.state()

        def no_git():
            (self.store / 'pair.json').write_text(
                json.dumps(
                    {**pristine, 'git_root': self.tmp.name, 'cwd': self.tmp.name}
                )
            )

        def too_large():
            (self.store / 'pair.json').write_text(json.dumps(pristine))
            (self.store / 'advice').mkdir(exist_ok=True)
            for n in range(6):
                (self.store / 'advice' / f'blob{n}.bin').write_bytes(
                    os.urandom(900_000)
                )

        # (label, setup, what stop prints about the relay it could not make)
        rows = (
            ('no .git', no_git, ''),
            ('too large', too_large, "over upload_thread_file's 4 MiB"),
        )
        for label, setup, said in rows:
            with self.subTest(label):
                setup()
                out = self.run_sh(
                    'stop', str(self.store), '--timeout', '1000', code=None
                )
                self.assertNotIn('relay-pending:', out)
                self.assertIn(said, out)
                self.assertEqual(list(self.relays_dir().glob('*.json')), [])
                # The STOP went through the CLI once; the second row's identical text is still pending in the thread, so it is not queued twice.
                self.assertEqual(len(self.stops_in(sidekick)), 1)

    def test_pair_stop_relays_a_working_sidekick_without_the_wait_report_fallback(self):
        """The pair's own stop relays a working Amp sidekick's STOP like the trio's stop and ends at its state line: the wait's report
        fallback would name an older report, and a relay the sidekick has not answered is not one."""
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('util'))
        self.set_state(amp_thread(1), 'tool_use', '2026-01-01T00:00:00.000Z')
        before = self.thread_messages(sidekick)
        pair = skill.parent / 'pstack-pair/scripts/pair.sh'
        r = subprocess.run(
            [str(pair), 'stop', str(self.store)],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout
        [record_file] = self.relays_dir().glob('*.json')
        record = json.loads(record_file.read_text())
        rid = record['id']
        self.assertEqual(record['state'], 'relay-pending')
        self.assertIn(f'relay-pending: {rid} for demo-sidekick ({sidekick})', out)
        self.assertIn(f'relay: then run pair.sh relay-done {self.store} {rid}', out)
        self.assertIn(
            f'pstack-pair STOP {self.store}', Path(record['message']).read_text()
        )
        self.assertIn('state: working', out)
        # An older report is in the store, but the relay is not the sidekick's answer to it: none is printed.
        self.assertNotIn('report:', out)
        self.assertEqual(self.thread_messages(sidekick), before)
        events = (self.store / 'events.tsv').read_text()
        self.assertEqual(events.count('send-stop'), 1)
        self.assertEqual(events.count('send-stop\t-\tsidekick relay-pending'), 1)

    def test_relay_done_marks_the_relay_sent_and_the_same_steer_waits_for_the_threads_next_turn_end(
        self,
    ):
        sidekick = amp_thread(1)
        steer = self.working_sidekick_steer()
        self.run_sh('steer', str(self.store), str(steer))
        [record_file] = self.relays_dir().glob('*.json')
        rid = json.loads(record_file.read_text())['id']
        status = self.run_sh('status', str(self.store))
        self.assertRegex(
            status, rf'(?m)^relay-pending: {rid} for demo-sidekick since \d\d:\d\d$'
        )
        # An id that is no relay of this store is refused, and nothing changes.
        out = self.run_sh('relay-done', str(self.store), 'deadbeef0000', code=5)
        self.assertIn(f'relay-done: no relay deadbeef0000 for {self.store}', out)
        self.assertEqual(json.loads(record_file.read_text())['state'], 'relay-pending')
        out = self.run_sh('relay-done', str(self.store), rid)
        self.assertEqual(out.strip(), f'relayed: {rid} to demo-sidekick ({sidekick})')
        record = json.loads(record_file.read_text())
        self.assertEqual(record['state'], 'relay-done')
        self.assertGreater(record['done_at'], record['at'] - 1)
        self.assertFalse(Path(record['bundle']).exists())
        outbox = json.loads(
            (Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/outbox.json').read_text()
        )
        self.assertEqual(
            (outbox[sidekick]['id'], outbox[sidekick]['hash']), (rid, record['hash'])
        )
        self.assertTrue(
            (
                Path(self.env['XDG_STATE_HOME']) / 'pstack/amp/sent' / f'{sidekick}.tgz'
            ).is_file()
        )
        events = (self.store / 'events.tsv').read_text()
        self.assertEqual(
            events.count(f'\tmaster\trelay\t001-util-s1\t{rid}:{sidekick}'), 1
        )
        self.assertNotIn('relay-pending:', self.run_sh('status', str(self.store)))
        out = self.run_sh('relay-done', str(self.store), rid)
        self.assertEqual(out.strip(), f'relayed: {rid} already done')
        self.assertEqual(events, (self.store / 'events.tsv').read_text())
        # The thread has not ended a turn since: the same steer is not relayed again.
        out = self.run_sh('steer', str(self.store), str(steer))
        self.assertIn(
            f'relayed: {rid} for demo-sidekick ({sidekick}) (already relayed', out
        )
        self.assertEqual(len(list(self.relays_dir().glob('*.json'))), 1)
        # Once the thread has ended its turn it is idle, and a steer reaches it by the CLI between turns: nothing is left for the master to relay.
        self.set_state(sidekick, 'idle', '2999-01-01T00:00:00.000Z')
        before = len(self.thread_messages(sidekick))
        out = self.run_sh('steer', str(self.store), str(steer))
        self.assertIn(f'steered demo-sidekick with {steer}', out)
        self.assertEqual(len(self.thread_messages(sidekick)), before + 1)
        self.assertEqual(len(list(self.relays_dir().glob('*.json'))), 1)

    def test_relay_done_after_a_newer_cli_send_keeps_that_sends_outbox_entry_and_sync_base(
        self,
    ):
        sidekick = amp_thread(1)
        steer = self.working_sidekick_steer()
        self.run_sh('steer', str(self.store), str(steer))
        [record_file] = self.relays_dir().glob('*.json')
        record = json.loads(record_file.read_text())
        # The relay was made a while ago; the turn has ended and the CLI has sent the thread something since.
        record_file.write_text(json.dumps({**record, 'at': record['at'] - 100}))
        self.set_state(sidekick, 'idle', '2999-01-01T00:00:00.000Z')
        self.message_from_the_master(
            'demo-sidekick', f'pstack-kitchen NOTE {self.store}/reviews/n1.md'
        )
        amp_state = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp'
        sent = amp_state / 'sent' / f'{sidekick}.tgz'
        outbox = json.loads((amp_state / 'outbox.json').read_text())[sidekick]
        before = sent.read_bytes()
        out = self.run_sh('relay-done', str(self.store), record['id'])
        self.assertEqual(
            out.strip(),
            f'relayed: {record["id"]} to demo-sidekick ({sidekick}); a newer send already holds the sync base',
        )
        self.assertEqual(json.loads(record_file.read_text())['state'], 'relay-done')
        self.assertEqual(
            json.loads((amp_state / 'outbox.json').read_text())[sidekick], outbox
        )
        self.assertNotEqual(outbox['hash'], record['hash'])
        self.assertEqual(sent.read_bytes(), before)
        self.assertFalse(Path(record['bundle']).exists())

    def test_a_pending_relay_whose_thread_has_ended_a_turn_since_is_stale(self):
        sidekick = amp_thread(1)
        steer = self.working_sidekick_steer()
        self.run_sh('steer', str(self.store), str(steer))
        [record_file] = self.relays_dir().glob('*.json')
        rid = json.loads(record_file.read_text())['id']
        self.assertIn(
            f'relay-pending: {rid} for', self.run_sh('status', str(self.store))
        )
        # The turn the relay was meant for is over: it is no longer owed, and the steer goes by the CLI between turns.
        self.set_state(sidekick, 'idle', '2999-01-01T00:00:00.000Z')
        self.assertNotIn('relay-pending:', self.run_sh('status', str(self.store)))
        out = self.run_sh('steer', str(self.store), str(steer))
        self.assertIn(f'steered demo-sidekick with {steer}', out)
        self.assertNotIn('already pending', out)
        # A relay of the same text made now is a new record, and the old one is marked stale.
        record, how = self.relay_of('STEER again')
        self.assertEqual(how, 'made')
        self.set_state(sidekick, 'idle', '2999-02-01T00:00:00.000Z')
        again, how = self.relay_of('STEER again')
        self.assertEqual(how, 'made')
        self.assertNotEqual(again['id'], record['id'])
        states = {
            r['id']: r['state']
            for r in map(
                lambda f: json.loads(f.read_text()), self.relays_dir().glob('*.json')
            )
        }
        self.assertEqual(states[record['id']], 'stale')
        self.assertEqual(states[again['id']], 'relay-pending')

    def test_a_steer_whose_bundle_is_over_the_upload_limit_says_so_once_and_queues(
        self,
    ):
        sidekick = amp_thread(1)
        steer = self.working_sidekick_steer()
        # A commit of incompressible bytes the sidekick's orb lacks goes into the bundle.
        (self.repo / 'big.bin').write_bytes(os.urandom(5 * 1024 * 1024))
        self.commit('big')
        out = self.run_sh('steer', str(self.store), str(steer))
        self.assertEqual(out.count("over upload_thread_file's 4 MiB"), 1)
        self.assertRegex(out, r'relay: bundle is 5\.\d MiB')
        self.assertIn(f'queued {steer} for demo-sidekick', out)
        self.assertEqual(list(self.relays_dir().glob('*.json')), [])
        self.assertEqual(list((self.repo / '.git/pstack-relay').glob('*.tgz')), [])
        self.assertEqual(
            len([m for m in self.thread_messages(sidekick) if 'STEER' in m]), 1
        )

    def test_the_relay_packed_last_holds_the_sync_base_whichever_is_confirmed_first(
        self,
    ):
        sidekick = amp_thread(1)
        self.working_sidekick_steer()
        amp_state = Path(self.env['XDG_STATE_HOME']) / 'pstack/amp'
        # the dispatch that started the unit was sent long before either relay was packed
        outbox = json.loads((amp_state / 'outbox.json').read_text())
        outbox[sidekick]['at'] -= 100
        (amp_state / 'outbox.json').write_text(json.dumps(outbox))
        first, _ = self.relay_of('STEER one')
        # R1 was packed a while before R2
        first_file = self.relays_dir() / f'{first["id"]}.json'
        first_file.write_text(json.dumps({**first, 'at': first['at'] - 10}))
        second, _ = self.relay_of('STEER two')
        packed = Path(second['bundle']).read_bytes()
        # both were packed before either is confirmed
        (self.relays_dir() / f'{second["id"]}.json').write_text(
            json.dumps({**second, 'at': second['at'] - 5})
        )
        self.run_sh('relay-done', str(self.store), first['id'])
        self.run_sh('relay-done', str(self.store), second['id'])
        outbox = json.loads((amp_state / 'outbox.json').read_text())[sidekick]
        self.assertEqual((outbox['id'], outbox['hash']), (second['id'], second['hash']))
        self.assertEqual((amp_state / 'sent' / f'{sidekick}.tgz').read_bytes(), packed)
        # and the other way round, the earlier relay confirmed last leaves R2's base alone
        third, _ = self.relay_of('STEER three')
        third_file = self.relays_dir() / f'{third["id"]}.json'
        third_file.write_text(json.dumps({**third, 'at': third['at'] - 10}))
        fourth, _ = self.relay_of('STEER four')
        packed = Path(fourth['bundle']).read_bytes()
        (self.relays_dir() / f'{fourth["id"]}.json').write_text(
            json.dumps({**fourth, 'at': fourth['at'] - 5})
        )
        self.run_sh('relay-done', str(self.store), fourth['id'])
        out = self.run_sh('relay-done', str(self.store), third['id'])
        self.assertIn('a newer send already holds the sync base', out)
        self.assertEqual((amp_state / 'sent' / f'{sidekick}.tgz').read_bytes(), packed)

    def test_relay_done_of_a_relay_that_is_no_longer_pending_changes_and_logs_nothing(
        self,
    ):
        self.working_sidekick_steer()
        record, _ = self.relay_of('STEER stale')
        record_file = self.relays_dir() / f'{record["id"]}.json'
        record_file.write_text(json.dumps({**record, 'state': 'stale'}))
        before = (self.store / 'events.tsv').read_text()
        out = self.run_sh('relay-done', str(self.store), record['id'])
        self.assertIn(f'relayed: {record["id"]} already stale', out)
        self.assertEqual(json.loads(record_file.read_text())['state'], 'stale')
        self.assertEqual((self.store / 'events.tsv').read_text(), before)
        self.assertTrue(Path(record['bundle']).exists())

    def test_a_steer_that_cannot_be_relayed_queues_behind_the_turn_once(self):
        sidekick = amp_thread(1)
        steer = self.working_sidekick_steer()
        # A run whose checkout has no .git has nowhere to put the bundle for upload_thread_file.
        state = self.state()
        state['git_root'] = state['cwd'] = str(Path(self.tmp.name))
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.run_sh('steer', str(self.store), str(steer))
        self.assertIn(f'queued {steer} for demo-sidekick: Amp threads deliver', out)
        self.assertNotIn('steered ', out)
        out = self.run_sh('steer', str(self.store), str(steer))
        self.assertIn('not sent again: the same message is still pending there', out)
        steers = [m for m in self.thread_messages(sidekick) if 'STEER' in m]
        self.assertEqual(len(steers), 1)
        # Each message names itself, so a retry can tell whether it already arrived.
        ids = {
            m.rsplit('pstack-msg: ', 1)[-1].strip()
            for m in self.thread_messages(sidekick)
        }
        self.assertEqual(len(ids), len(self.thread_messages(sidekick)))
        self.assertEqual(list(self.relays_dir().glob('*.json')), [])

    def test_a_send_whose_upload_broke_off_is_retried_and_delivered_once(self):
        sidekick = amp_thread(1)
        self.env['PSTACK_AMP_RETRY_S'] = '0'
        (self.fake / 'amp-upload-fail').write_text('1')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.assertEqual(len(self.briefs_in(sidekick)), 1)
        self.assertEqual((self.fake / 'amp-upload-fail').read_text(), '0')

    def test_a_send_that_reached_the_thread_before_its_upload_error_is_not_resent(
        self,
    ):
        sidekick = amp_thread(1)
        self.env['PSTACK_AMP_RETRY_S'] = '0'
        (self.fake / 'amp-upload-fail').write_text('1')
        (self.fake / 'amp-upload-fail-after-delivery').write_text('')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        out = self.dispatch(b)
        self.assertIn(f'amp demo-sidekick ({sidekick}): pstack-kitchen BRIEF {b}', out)
        self.assertEqual(len(self.briefs_in(sidekick)), 1)

    def test_an_upload_that_keeps_failing_fails_the_send_after_its_tries(self):
        self.env['PSTACK_AMP_RETRY_S'] = '0'
        (self.fake / 'amp-upload-fail').write_text('5')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        out = self.run_sh('dispatch', str(self.store), str(b), code=None)
        self.assertIn('Attachment upload did not complete', out)
        self.assertEqual((self.fake / 'amp-upload-fail').read_text(), '2')
        self.assertEqual(self.briefs_in(amp_thread(1)), [])

    def test_the_bundle_carries_the_sidekicks_commit_to_a_checkout_that_lacks_it(self):
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'], verify=['python3 -c 1'])
        self.dispatch(b)
        base = self.git('rev-parse', 'HEAD')
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        head = self.commit('util')
        self.done(b, head)
        self.as_thread(
            sidekick, 'finish', str(self.store), str(self.store / 'reports' / b.name)
        )
        # The master's orb never saw the commit: put its checkout back, drop the evidence ref finish pinned here, and drop the object.
        self.git('reset', '-q', '--hard', base)
        for ref in self.git(
            'for-each-ref', '--format=%(refname)', 'refs/pstack/keep'
        ).split():
            self.git('update-ref', '-d', ref)
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.git('gc', '-q', '--prune=now')
        self.assertNotEqual(
            subprocess.run(
                ['git', 'cat-file', '-e', head], cwd=self.repo, check=False, timeout=60
            ).returncode,
            0,
        )
        out = self.run_sh(
            'sync',
            str(self.fake / 'amp-threads' / AMP_MASTER / '1-pstack-demo-master.tgz'),
        )
        self.assertIn('checkout: ', out)
        self.assertIn(f'at {head[:9]}, as demo-sidekick', out)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def unpushed_branches(self):
        """Two local branches that are not HEAD's, one named with a slash, each with a commit of its own that no remote holds: {name: tip}."""
        here = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        base = self.git('rev-parse', 'HEAD')
        tips = {}
        for name in ('feat/one', 'side'):
            self.git('checkout', '-q', '-b', name, base)
            self.write(f'{name.replace("/", "-")}.txt', f'{name}\n')
            tips[name] = self.commit(f'work on {name}')
        self.git('checkout', '-q', here)
        return tips

    def bundle_of(self, path):
        """(manifest, refs the git bundle lists) of an attached bundle file."""
        with tarfile.open(path, 'r:gz') as tar:
            member = tar.extractfile('manifest.json')
            assert member is not None
            manifest = json.loads(member.read())
            if 'git.bundle' not in tar.getnames():
                return manifest, []
            tar.extract('git.bundle', self.tmp.name, filter='data')
        heads = self.git('bundle', 'list-heads', f'{self.tmp.name}/git.bundle')
        os.unlink(f'{self.tmp.name}/git.bundle')
        return manifest, [line.split(' ', 1)[1] for line in heads.splitlines()]

    def test_a_hand_over_bundle_carries_every_local_branch_and_a_role_message_does_not(
        self,
    ):
        tips = self.unpushed_branches()
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        [handover] = (self.fake / 'amp-threads' / amp_thread(2)).glob(
            '*-pstack-demo-master-g2.tgz'
        )
        manifest, refs = self.bundle_of(handover)
        self.assertEqual(
            {
                **tips,
                self.git('rev-parse', '--abbrev-ref', 'HEAD'): self.git(
                    'rev-parse', 'HEAD'
                ),
            },
            manifest['git']['branches'],
        )
        for name in tips:
            self.assertIn(f'refs/pstack/out/br/{name}', refs)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        to_sidekick = sorted(
            (self.fake / 'amp-threads' / amp_thread(1)).glob(
                '*-pstack-demo-sidekick.tgz'
            )
        )
        self.assertTrue(to_sidekick)
        for path in to_sidekick:
            manifest, refs = self.bundle_of(path)
            self.assertNotIn('branches', manifest['git'])
            self.assertEqual([r for r in refs if '/br/' in r], [])

    def drop_branches(self, tips):
        """The next master's orb never saw these branches: delete them and prune their objects."""
        for name in tips:
            self.git('branch', '-D', name)
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.git('gc', '-q', '--prune=now')

    def test_the_next_master_gets_every_local_branch_at_the_same_tip_after_sync_and_take_master(
        self,
    ):
        tips = self.unpushed_branches()
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        [handover] = (self.fake / 'amp-threads' / master2).glob(
            '*-pstack-demo-master-g2.tgz'
        )
        self.drop_branches(tips)
        out = self.as_thread(master2, 'sync', str(handover))
        for name, tip in tips.items():
            self.assertEqual(self.git('rev-parse', f'refs/heads/{name}'), tip)
            self.assertIn(f'branch {name}: created at {tip[:9]}', out)
        self.as_thread(master2, 'take-master', str(self.store))
        self.assertNotIn('previous', self.state()['master'])

    def test_a_hand_over_bundle_whose_commits_cannot_be_imported_makes_sync_exit_non_zero(
        self,
    ):
        tips = self.unpushed_branches()
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        [handover] = (self.fake / 'amp-threads' / master2).glob(
            '*-pstack-demo-master-g2.tgz'
        )
        # The same hand-over with its git part removed: the commits are on no orb that can be reached.
        stripped = Path(self.tmp.name) / 'stripped.tgz'
        with (
            tarfile.open(handover, 'r:gz') as src,
            tarfile.open(stripped, 'w:gz') as dst,
        ):
            for member in src.getmembers():
                if member.name != 'git.bundle':
                    dst.addfile(
                        member, src.extractfile(member) if member.isfile() else None
                    )
        self.drop_branches(tips)
        out = self.as_thread(master2, 'sync', str(stripped), code=1)
        self.assertIn('was not carried and origin lacks it', out)
        self.assertIn('previous', self.state()['master'])

    def test_a_hand_over_bundle_is_cut_against_origins_default_branch_not_the_remotes_this_side_has(
        self,
    ):
        """A remote only the sender has must not empty the bundle: the receiver fetches origin, so origin's default branch is the one thing
        the bundle may leave out."""
        tips = self.unpushed_branches()
        origin = Path(self.tmp.name) / 'origin.git'
        sender_only = Path(self.tmp.name) / 'sender-only.git'
        for bare in (origin, sender_only):
            subprocess.run(
                ['git', 'init', '-q', '--bare', str(bare)], check=True, timeout=60
            )
        here = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        self.git('remote', 'add', 'origin', str(origin))
        self.git('remote', 'add', 'elsewhere', str(sender_only))
        self.git('push', '-q', 'origin', f'{here}:{here}')
        self.git('remote', 'set-head', 'origin', here)
        self.git('push', '-q', 'elsewhere', *[f'{n}:{n}' for n in tips])
        self.filled_handoff()
        self.run_sh('rotate-master', str(self.store))
        [handover] = (self.fake / 'amp-threads' / amp_thread(2)).glob(
            '*-pstack-demo-master-g2.tgz'
        )
        manifest, refs = self.bundle_of(handover)
        self.assertEqual(manifest['git']['baseline'], self.git('rev-parse', 'HEAD'))
        for name in tips:
            self.assertIn(f'refs/pstack/out/br/{name}', refs)

    def test_rotate_master_starts_the_next_master_as_a_thread_and_a_stale_reply_is_forwarded(
        self,
    ):
        sidekick = amp_thread(1)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.store / 'reports' / '001-util-s1.md').write_text('status: object\n')
        self.as_thread(
            sidekick,
            'finish',
            str(self.store),
            str(self.store / 'reports/001-util-s1.md'),
        )
        stale = self.fake / 'amp-threads' / AMP_MASTER / '1-pstack-demo-master.tgz'
        self.filled_handoff()
        (self.fake / 'amp-mode').write_text('openai/gpt-6-astra\n')
        out = self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        self.assertIn(
            f'next master: demo-master-g2 (generation 2) in thread {master2}; it runs take-master there',
            out,
        )
        self.assertIn(
            '-m openai/gpt-6-astra --title demo-master-g2 --attach ',
            (self.fake / 'amp.log').read_text(),
        )
        [prompt] = self.thread_messages(master2)
        self.assertIn(f'pstack-kitchen MASTER {self.store} (generation 2)', prompt)
        self.assertIn('installs the pstack build the sender runs', prompt)
        state = self.state()['master']
        self.assertEqual(
            (state['name'], state['previous']['name']),
            ('demo-master-g2', 'demo-master'),
        )
        # A second rotation before the new thread took over is refused.
        out = self.run_sh('rotate-master', str(self.store), code=5)
        self.assertIn(
            f'a hand-over is open: generation 2 has not taken over yet; its thread runs kitchen.sh take-master {self.store} first',
            out,
        )
        # A forward amp refuses names the thread it was meant for and fails like the multiplexer.
        (self.fake / 'amp-continue-fail').write_text('thread not found\n')
        out = self.run_sh('sync', str(stale), code=2)
        self.assertIn(
            f'demo-master is now {master2}, and forwarding to it failed: thread not found',
            out,
        )
        (self.fake / 'amp-continue-fail').unlink()
        # A reply the sidekick sent before it learned of the hand-over reaches the old master, whose sync forwards it.
        out = self.run_sh('sync', str(stale))
        self.assertIn(
            f'forwarded to demo-master ({master2}): this thread no longer holds demo-master',
            out,
        )
        self.assertIn('pstack-kitchen REPORT', self.thread_messages(master2)[-1])
        # The new master's thread takes the run over.
        out = self.as_thread(master2, 'take-master', str(self.store))
        self.assertIn(f'closed the old master demo-master (amp:{AMP_MASTER})', out)
        self.assertIn(f'master: demo-master in amp:{master2} (generation 2)', out)
        self.assertNotIn('previous', self.state()['master'])

    def test_the_sidekicks_bootstrap_prompt_names_the_exact_ready_file_of_the_store(
        self,
    ):
        ready = self.ready_path().relative_to(self.store)
        self.assertIn(f'write {ready},', self.thread_messages(amp_thread(1))[0])

    def test_a_master_thread_that_does_not_start_leaves_the_run_where_it_was(self):
        self.filled_handoff()
        before = self.state()['master']
        (self.fake / 'amp-ox-fail').write_text('Unknown model provider\n')
        out = self.run_sh('rotate-master', str(self.store), '--model', 'nope/x', code=2)
        self.assertIn(
            'the next master thread did not start: Unknown model provider', out
        )
        self.assertEqual(self.state()['master'], before)

    def test_each_verification_starts_a_fresh_verifier_thread(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        out = self.run_sh('verify', str(self.store), '001', code=4)
        packet = self.store / 'verdicts/001-util-v1-packet.md'
        self.assertIn(
            f'amp demo-verifier ({amp_thread(2)}): Load the pstack-kitchen skill', out
        )
        self.assertIn(f'VERIFY {packet}', self.thread_messages(amp_thread(2))[0])
        # The packet's scratch worktree travels with the bundle, so the verifier's orb can check it out at the unit's head.
        self.assertIn(
            'scratch/',
            (self.fake / 'amp.log').read_text()
            + str(list((self.store / 'scratch').iterdir())),
        )


def _context(total, compactions):
    return f'{{"totalTokens": {total}, "maxContextTokens": 100000, "compactionCount": {compactions}}}\n'


_ROTATION_SRC = ['src/**', 'tests/**']
# (reason the dispatch says, the next brief's scope, what changes before it is dispatched); each row is its own generated test,
# so tests/run.py runs it in a process of its own.
ROTATION_REASON_CASES = [
    ('profiles app vs docs', ['docs/**'], lambda t: None),
    ('context 50% >= 50%', _ROTATION_SRC, lambda t: t.set_context(_context(50000, 0))),
    ('compacted', _ROTATION_SRC, lambda t: t.set_context(_context(1000, 1))),
    ('context unreadable', _ROTATION_SRC, lambda t: t.set_context('not json\n')),
    ('previous brief missing', _ROTATION_SRC, lambda t: t.previous_brief_gone()),
    (
        'risk escalated/routine',
        _ROTATION_SRC,
        lambda t: t.previous_brief_header('risk: routine', 'risk: escalated'),
    ),
]


def _rotation_reason_test(reason, scope, setup):
    def test(self):
        self.rotation_says_why(reason, scope, setup)

    return test


for _reason, _scope, _setup in ROTATION_REASON_CASES:
    _slug = re.sub(r'\W+', '_', _reason).strip('_')
    setattr(
        AmpKitchenTests,
        f'test_a_rotation_under_amp_says_why_in_a_line_and_in_the_rotate_event_{_slug}',
        _rotation_reason_test(_reason, _scope, _setup),
    )


for _name in dir(KitchenScriptTests):
    if _name.startswith('test_') and _name not in vars(AmpKitchenTests):
        setattr(AmpKitchenTests, _name, None)


ROLE_DIRS = ('reports', 'advice', 'verdicts', 'steps', 'progress', 'drains')


class AmpGitKitchenTests(AmpKitchenTests):
    """The kitchen with PSTACK_MUX=amp and PSTACK_STORE=git: the store is the branch kitchen/demo of the repository's origin, a bare repository
    here. The master works in self.repo and self.store; a role works in a clone and a store of its own, as an orb does, and what it writes
    reaches the master only through the branch (and, for its code, a ref of origin)."""

    def mux_env(self):
        self.remote = Path(self.tmp.name) / 'remote.git'
        subprocess.run(
            ['git', 'init', '-q', '--bare', str(self.remote)], check=True, timeout=60
        )
        return {
            **super().mux_env(),
            'PSTACK_STORE': 'git',
            'PSTACK_STORE_PUSH_BACKOFF_S': '0',
        }

    def git(self, *args):
        out = super().git(*args)
        if args == ('init', '-q'):
            super().git('remote', 'add', 'origin', str(self.remote))
        return out

    def remote_git(self, *args):
        return subprocess.run(
            ['git', '--git-dir', str(self.remote), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout

    def store_git(self, store, *args):
        return subprocess.run(
            ['git', '-C', str(store), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()

    def role_orb(self, name='sidekick'):
        """A role's orb: a clone of the work repository at the master's commit, a state home of its own and the store worktree synced there.
        Returns (clone, store)."""
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        orb = Path(self.tmp.name) / f'{name}-orb'
        clone = orb / 'work'
        subprocess.run(
            ['git', 'clone', '-q', str(self.remote), str(clone)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        store = orb / 'store'
        self.orb_env = {
            'HOME': str(orb / 'home'),
            'XDG_STATE_HOME': str(orb / 'state'),
        }
        from pstack_core import gitstore

        gitstore.attach(str(store), str(clone), 'kitchen/demo')
        registry = orb / 'state/pstack/amp/registry.json'
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(
                {
                    'agents': {
                        'demo-master': {
                            'pane': f'amp:{AMP_MASTER}',
                            'kind': 'amp',
                            'args': [],
                            'thread': AMP_MASTER,
                        }
                    },
                    'panes': [],
                    'next': 1,
                }
            )
        )
        return clone, store

    def as_orb(self, clone, *args, thread=None, code=0):
        thread = thread or amp_thread(1)
        return self.as_thread(
            thread, *args, code=code, env=self.orb_env, cwd=clone, mux='amp'
        )

    def commit_in(self, clone, name):
        (clone / f'{name}.txt').write_text(f'{name}\n')
        for args in (['add', '-A'], ['commit', '-q', '-m', name]):
            subprocess.run(
                ['git', *args],
                cwd=clone,
                env=self.env,
                check=True,
                capture_output=True,
                timeout=60,
            )
        return subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()

    def write_report(self, store, name, status, head):
        path = Path(store) / 'reports' / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'# Report\n\nstatus: {status}\nhead: {head}\n')
        return path

    def branch_tree(self):
        return set(
            self.remote_git('ls-tree', '-r', '--name-only', 'kitchen/demo').split()
        )

    def test_a_role_publishes_its_report_before_the_message_and_a_secret_stops_both(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        token = 'gh' + 'p_' + 'a1' * 18
        report.write_text(report.read_text() + f'\nthe token is {token}\n')
        tip = self.remote_git('rev-parse', 'kitchen/demo')
        messages = len(list((self.fake / 'amp-threads').glob(f'{AMP_MASTER}/*.txt')))
        out = self.as_orb(clone, 'finish', str(orb_store), str(report), code=1)
        self.assertIn(
            'secret: reports/001-util.md:6: github-token; redact it (write [REDACTED: github-token]) and run the command again',
            out,
        )
        self.assertNotIn(token, out)
        self.assertEqual(self.remote_git('rev-parse', 'kitchen/demo'), tip)
        self.assertEqual(
            len(list((self.fake / 'amp-threads').glob(f'{AMP_MASTER}/*.txt'))), messages
        )
        report.write_text(
            f'# Report\n\nstatus: partial\nhead: {head}\n\nthe token is [REDACTED: github-token]\n'
        )
        out = self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('reports/001-util.md', self.branch_tree())
        self.assertIn('events/sidekick-g1.tsv', self.branch_tree())
        self.assertEqual(
            len(list((self.fake / 'amp-threads').glob(f'{AMP_MASTER}/*.txt'))),
            messages + 1,
        )
        self.assertIn(
            f'pstack-kitchen REPORT {report}',
            out + ''.join(self.thread_messages(AMP_MASTER)),
        )

    def test_a_second_finish_of_an_unchanged_report_is_not_sent_again_and_a_changed_one_is(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.set_state(AMP_MASTER, 'tool_use')
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(self.announced(), 1)
        out = self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('not sent again', out)
        self.assertEqual(self.announced(), 1)
        self.write_report(orb_store, '001-util.md', 'partial', head + '\n# changed')
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(self.announced(), 2)

    def test_close_archives_the_branch_as_a_tag_and_deletes_it_and_a_rerun_changes_nothing(
        self,
    ):
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        out = self.run_sh('close', str(self.store))
        run_id = json.loads((self.store / 'pair.json').read_text())['run_id']
        tag = f'kitchen-archive/demo-{run_id}'
        self.assertIn(
            f'close: kitchen/demo is archived as {tag} and deleted on origin', out
        )
        self.assertEqual(self.remote_git('for-each-ref', 'refs/heads/kitchen/demo'), '')
        tip = self.remote_git('rev-parse', f'{tag}^{{}}').strip()
        events = self.remote_git('ls-tree', '-r', '--name-only', tip)
        self.assertIn('events/master-g0.tsv', events)
        self.assertRegex(
            self.remote_git('show', f'{tip}:events/master-g0.tsv'),
            rf'\tclose\t-\tstore: archive {tag}\n',
        )
        again = self.run_sh('close', str(self.store))
        self.assertIn(f'close: kitchen/demo is already archived as {tag}', again)
        self.assertEqual(self.remote_git('rev-parse', f'{tag}^{{}}').strip(), tip)

    def test_store_to_git_moves_a_local_store_to_its_branch_and_a_rerun_changes_nothing(
        self,
    ):
        self.env.pop('PSTACK_STORE')
        self.run_sh('init', 'loc')
        local = self.store.parent / 'loc'
        self.assertFalse((local / '.git').exists())
        out = self.run_sh('store-to-git', str(local))
        self.assertIn('store-to-git: kitchen/loc is on origin at', out)
        self.assertTrue((local / '.git').exists())
        self.assertEqual(
            self.store_git(local, 'branch', '--show-current'), 'kitchen/loc'
        )
        tree = set(
            self.remote_git('ls-tree', '-r', '--name-only', 'kitchen/loc').split()
        )
        self.assertLessEqual({'pair.json', 'standing-orders.md', '.gitignore'}, tree)
        backend = json.loads((local / 'pair.json').read_text())['store_backend']
        migrated_at = backend.pop('migrated_at')
        self.assertEqual(backend, {'backend': 'git', 'branch': 'kitchen/loc'})
        self.assertGreater(migrated_at, 1_700_000_000)
        tip = self.remote_git('rev-parse', 'kitchen/loc')
        again = self.run_sh('store-to-git', str(local))
        self.assertIn('already on kitchen/loc', again)
        self.assertEqual(self.remote_git('rev-parse', 'kitchen/loc'), tip)
        self.assertEqual(
            json.loads((local / 'pair.json').read_text())['store_backend'][
                'migrated_at'
            ],
            migrated_at,
        )

    def test_after_store_to_git_a_thread_from_before_it_with_no_request_archives_by_the_local_rule_and_one_from_after_it_keeps_no_drain_receipt(
        self,
    ):
        self.env.pop('PSTACK_STORE')
        self.run_sh('init', 'loc')
        local = self.store.parent / 'loc'
        old_sidekick, consultant, busy = amp_thread(11), amp_thread(12), amp_thread(13)
        self.retire_threads(
            {
                old_sidekick: {
                    'name': 'loc-sidekick',
                    'role': 'sidekick',
                    'store': str(local),
                    'started': 1,
                },
                consultant: {
                    'name': 'loc-consultant',
                    'role': 'consultant',
                    'store': str(local),
                    'started': 2,
                },
                busy: {
                    'name': 'loc-sidekick',
                    'role': 'sidekick',
                    'store': str(local),
                    'started': 3,
                },
            }
        )
        self.set_state(busy, 'tool_use')
        self.run_sh('store-to-git', str(local))
        migrated_at = json.loads((local / 'pair.json').read_text())['store_backend'][
            'migrated_at'
        ]
        new_sidekick = amp_thread(14)
        self.retire_threads(
            {
                new_sidekick: {
                    'name': 'loc-sidekick',
                    'role': 'sidekick',
                    'store': str(local),
                    'started': migrated_at + 1,
                }
            }
        )
        out = self.run_sh('threads', str(local), '--archive')
        archived = (self.fake / 'amp-archived').read_text().split()
        self.assertCountEqual(archived, [old_sidekick, consultant])
        self.assertIn(f'archived {old_sidekick} (sidekick)', out)
        self.assertIn(f'archived {consultant} (consultant)', out)
        self.assertIn(
            f'kept {new_sidekick}: no drain receipt (the store branch records no request to it)',
            out,
        )
        self.assertRegex(out, rf'kept {busy}: working')

    def test_a_dispatch_publishes_the_masters_brief_and_pair_json_before_the_message(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.assertLessEqual(
            {'briefs/001-util.md', 'pair.json', 'standing-orders.md'},
            self.branch_tree(),
        )
        self.assertIn(
            '"brief": "' + str(b) + '"',
            self.remote_git('show', 'kitchen/demo:pair.json'),
        )

    def test_a_snooze_done_and_a_master_publish_put_snooze_tsv_on_the_branch(self):
        sidekick = amp_thread(1)
        self.review_unit(True)
        self.snooze_world({sidekick: 'idle'})
        self.assertEqual(
            self.run_sh('snooze', str(self.store), '--done', sidekick),
            f'snoozed: {sidekick}\n',
        )
        self.run_sh('snooze', str(self.store), '--wake', sidekick)
        self.dispatch(self.brief('002', 'next', ['src/**', 'tests/**']))
        self.assertIn('snooze.tsv', self.branch_tree())
        self.assertIn(
            f'\t{sidekick}\tsidekick\tsnoozed',
            self.remote_git('show', 'kitchen/demo:snooze.tsv'),
        )

    def reject_code_pushes(self, only='*'):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text(
            '#!/bin/sh\nwhile read old new ref; do\n'
            f'  case "$ref" in refs/heads/kitchen-code/{only}) echo protected namespace >&2; exit 1;; esac\n'
            'done\n'
        )
        hook.chmod(0o755)

    def master_has(self, sha):
        return (
            subprocess.run(
                ['git', 'cat-file', '-e', f'{sha}^{{commit}}'],
                cwd=self.repo,
                capture_output=True,
                timeout=60,
            ).returncode
            == 0
        )

    def master_fetches_the_announced_code(self):
        from pstack_core import gitstore

        self.assertTrue(gitstore.refresh(str(self.store)))
        self.assertTrue(gitstore.fetch_code(str(self.store)))

    def announced(self):
        return len(list((self.fake / 'amp-threads').glob(f'{AMP_MASTER}/*.txt')))

    def test_a_role_from_another_skill_build_runs_kitchen_commands_and_a_master_still_stamps_its_build(
        self,
    ):
        here = str(skill.parent.resolve())
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        # The role's skills directory is not the last one pair.json lists: the startup stamp must not write the master's file.
        state = json.loads((orb_store / 'pair.json').read_text())
        state['skill_builds'][-1]['path'] = '/old/skills'
        (orb_store / 'pair.json').write_text(json.dumps(state))
        before = self.remote_git('show', 'kitchen/demo:pair.json')
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(self.remote_git('show', 'kitchen/demo:pair.json'), before)
        self.assertIn('reports/001-util.md', self.branch_tree())
        self.assertEqual(
            [
                b['path']
                for b in json.loads((orb_store / 'pair.json').read_text())[
                    'skill_builds'
                ]
            ],
            [b['path'] for b in state['skill_builds']],
        )
        # The master's own command from a new build still appends its row.
        mstate = self.state()
        mstate['skill_builds'][-1]['path'] = '/old/skills'
        (self.store / 'pair.json').write_text(json.dumps(mstate))
        self.run_sh('status', str(self.store))
        self.assertEqual(
            [b['path'] for b in self.state()['skill_builds']][-2:],
            ['/old/skills', here],
        )

    def test_a_reports_head_is_pushed_to_the_roles_code_ref_before_the_report_goes_out(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.assertFalse(self.master_has(head))
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(
            self.remote_git('rev-parse', 'kitchen-code/demo/sidekick-g1').strip(), head
        )
        self.assertNotIn('code/sidekick-g1.bundle', self.branch_tree())
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_fetch_code_is_false_and_sync_warns_when_a_trunk_that_resolves_cannot_be_fetched_but_not_when_none_resolves_or_origins_head_replaces_a_stale_one(
        self,
    ):
        from pstack_core import gitstore

        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.master_fetches_the_announced_code()
        self.assertNotIn(
            'could not be read', self.run_sh('sync', '--branch', str(self.store))
        )
        # A repository whose origin has no main or master and which has no origin/HEAD has no trunk to fetch: nothing failed.
        self.remote_git('update-ref', '-d', 'refs/heads/main')
        self.assertTrue(gitstore.fetch_code(str(self.store)))
        self.assertNotIn(
            'could not be read', self.run_sh('sync', '--branch', str(self.store))
        )
        # A trunk that resolves (origin/HEAD names it) but cannot be fetched is a failure the caller must hear of.
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/gone')
        self.assertEqual(
            self.remote_git('rev-parse', 'kitchen-code/demo/sidekick-g1').strip(), head
        )
        self.assertFalse(gitstore.fetch_code(str(self.store)))
        out = self.run_sh('sync', '--branch', str(self.store))
        self.assertIn('warning: origin or a published bundle could not be read', out)
        # origin/HEAD is stale (it names a branch origin no longer has) but origin is readable and its own HEAD names a branch it has: that
        # branch is the trunk, and nothing failed.
        self.remote_git(
            'update-ref',
            'refs/heads/trunk',
            self.remote_git('rev-parse', 'kitchen-code/demo/sidekick-g1').strip(),
        )
        self.remote_git('symbolic-ref', 'HEAD', 'refs/heads/trunk')
        self.assertTrue(gitstore.fetch_code(str(self.store)))
        self.assertNotIn(
            'could not be read', self.run_sh('sync', '--branch', str(self.store))
        )

    def test_a_code_ref_origin_refuses_gives_a_bundle_the_master_fetches_instead(self):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('code/sidekick-g1.bundle', self.branch_tree())
        self.assertIn('reports/001-util.md', self.branch_tree())
        self.assertFalse(self.master_has(head))
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_with_the_code_push_refused_and_no_bundle_the_report_stays_pending_and_nothing_is_sent(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        self.orb_env['PSTACK_CODE_BUNDLE'] = '0'
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        tip, sent = self.remote_git('rev-parse', 'kitchen/demo'), self.announced()
        out = self.as_orb(clone, 'finish', str(orb_store), str(report), code=1)
        self.assertIn('the report stays pending and nothing is sent', out)
        self.assertEqual(self.remote_git('rev-parse', 'kitchen/demo'), tip)
        self.assertEqual(self.announced(), sent)
        self.assertNotIn('reports/001-util.md', self.branch_tree())

    def fresh_orb(self, name):
        """A role's orb that holds nothing yet: a clone of the work repository and a state home of its own, with no store and no registry, as a
        thread's first orb is. Returns (clone, the path its store goes to)."""
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        orb = Path(self.tmp.name) / name
        subprocess.run(
            ['git', 'clone', '-q', str(self.remote), str(orb / 'work')],
            check=True,
            capture_output=True,
            timeout=60,
        )
        self.orb_env = {'HOME': str(orb / 'home'), 'XDG_STATE_HOME': str(orb / 'state')}
        return orb / 'work', orb / 'runs/demo'

    def role_ready(self, name, thread, generation, report=None):
        """A role of this generation, in a fresh orb, syncs the branch, writes its READY file and sends it, then sends report (a done report of
        the dispatched brief) when one is named."""
        clone, orb_store = self.fresh_orb(name)
        self.as_orb(
            clone, 'sync', '--branch', str(orb_store), 'kitchen/demo', thread=thread
        )
        files = [orb_store / 'reports' / f'000-ready-g{generation}.md']
        files[0].parent.mkdir(exist_ok=True)
        files[0].write_text('status: done\n')
        if report:
            head = self.commit_in(clone, 'work')
            files.append(self.write_report(orb_store, report, 'done', head))
        for file in files:
            self.as_orb(clone, 'notify', str(orb_store), str(file), thread=thread)

    def dirty_role_files(self):
        """The paths of the master's store worktree with changes not on the branch that a role owns: a master command leaves none."""
        status = subprocess.run(
            ['git', 'status', '--porcelain', '--untracked-files=all'],
            cwd=self.store,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        paths = [line[3:] for line in status.splitlines()]
        return [p for p in paths if p.split('/')[0] in ROLE_DIRS]

    def test_a_replacement_sidekicks_ready_is_its_own_file_and_the_generation_before_is_never_counted_for_it(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.store / 'reports/000-ready-g1.md').unlink()
        self.role_ready('first-orb', amp_thread(1), 1, report=b.name)
        self.run_sh('sync', '--branch', str(self.store))
        self.run_sh('rotate', str(self.store), code=4)
        self.assertEqual(self.state()['sidekick']['generation'], 2)
        bootstrap = self.thread_messages(amp_thread(2))[0]
        self.assertIn('write reports/000-ready-g2.md', bootstrap)
        out = self.run_sh('spawn', str(self.store), code=4)
        self.assertIn(
            'sidekick bootstrap still pending: reports/000-ready-g2.md is missing', out
        )
        self.assertTrue((self.store / 'reports/000-ready-g1.md').is_file())
        self.assertFalse(list((self.store / 'sessions').glob('sidekick-*-ready.md')))
        self.assertEqual(self.dirty_role_files(), [])
        self.role_ready('second-orb', amp_thread(2), 2)
        self.run_sh('sync', '--branch', str(self.store))
        out = self.run_sh('spawn', str(self.store))
        self.assertIn('000-ready-g2.md', out)
        self.assertIs(self.state()['sidekick']['bootstrap_pending'], False)
        self.assertEqual(self.dirty_role_files(), [])
        self.assertEqual(
            self.remote_git(
                'ls-tree', '--name-only', 'kitchen/demo', 'reports/'
            ).split(),
            [
                'reports/000-ready-g1.md',
                'reports/000-ready-g2.md',
                'reports/001-util.md',
            ],
        )

    def rotated_run(self):
        """A run whose sidekick was rotated: thread 1 retired, thread 2 current. Every record is on the branch."""
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.store / 'reports/000-ready-g1.md').unlink()
        self.role_ready('first-orb', amp_thread(1), 1, report=b.name)
        self.run_sh('sync', '--branch', str(self.store))
        self.run_sh('rotate', str(self.store), code=4)

    def fresh_master(self, name):
        """The master in a clone that holds nothing: no registry, threads or outbox, and a store path no other orb has. (clone, store)"""
        clone, taken = self.fresh_orb(name)
        self.as_orb(
            clone, 'sync', '--branch', str(taken), 'kitchen/demo', thread=AMP_MASTER
        )
        return clone, taken

    def test_a_master_in_a_fresh_clone_rebuilds_the_role_threads_from_the_branch_records_of_its_run_only(
        self,
    ):
        self.rotated_run()
        old, new = amp_thread(1), amp_thread(2)
        self.assertLessEqual(
            {f'threads/{old}.json', f'threads/{new}.json'}, self.branch_tree()
        )
        record = json.loads(self.remote_git('show', f'kitchen/demo:threads/{old}.json'))
        self.assertEqual(
            (record['role'], record['name'], record['state']),
            ('sidekick', 'demo-sidekick', 'retired'),
        )
        self.assertEqual(record['run'], self.state()['run_id'])
        self.assertNotIn(str(self.store), json.dumps(record))
        clone, taken = self.fresh_master('master-orb')
        (taken / 'threads' / f'{amp_thread(9)}.json').write_text(
            json.dumps(
                {
                    'run': 'another-run-of-the-same-slug',
                    'role': 'sidekick',
                    'thread': amp_thread(9),
                    'name': 'demo-sidekick',
                    'state': 'started',
                }
            )
        )
        out = self.as_orb(clone, 'threads', str(taken), thread=AMP_MASTER)
        self.assertRegex(out, rf'(?m)^{old}  sidekick  ')
        self.assertRegex(out, rf'(?m)^{new}  sidekick  ')
        self.assertNotIn(amp_thread(9), out)
        state = Path(self.orb_env['XDG_STATE_HOME']) / 'pstack/amp'
        known = json.loads((state / 'threads.json').read_text())
        self.assertEqual(known[old]['role'], 'sidekick')
        self.assertTrue(known[old]['at'])
        self.assertEqual(known[new]['store'], str(taken))
        registry = json.loads((state / 'registry.json').read_text())
        self.assertEqual(registry['agents']['demo-sidekick']['thread'], new)
        self.assertEqual(self.dirty_role_files(), [])

    def requests_of(self, thread):
        record = json.loads(
            self.remote_git('show', f'kitchen/demo:threads/{thread}.json')
        )
        return record['requests']

    def test_a_send_that_was_delivered_and_never_recorded_stays_pending_on_the_branch_and_is_not_sent_again_under_a_new_id(
        self,
    ):
        sidekick = amp_thread(1)
        self.ready_path().unlink()
        self.role_ready('first-orb', sidekick, 1)
        self.run_sh('sync', '--branch', str(self.store))
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        (self.fake / 'amp-kill-after-delivery').write_text('')
        out = self.run_sh(
            'dispatch', str(self.store), str(b), '--timeout', '1', code=None
        )
        self.assertFalse((self.fake / 'amp-kill-after-delivery').exists(), out)
        delivered = len(self.briefs_in(sidekick))
        self.assertEqual(delivered, 1)
        [pending] = [r for r in self.requests_of(sidekick) if r['state'] == 'pending']
        self.assertIn(f'pstack-msg: {pending["id"]}', self.briefs_in(sidekick)[0])
        # A master in a fresh clone reads the request as pending from the branch alone.
        clone, taken = self.fresh_master('master-orb')
        record = json.loads((taken / 'threads' / f'{sidekick}.json').read_text())
        self.assertEqual(
            [(r['id'], r['state']) for r in record['requests']][-1:],
            [(pending['id'], 'pending')],
        )
        self.dispatch(b)
        self.assertEqual(len(self.briefs_in(sidekick)), delivered)
        [settled] = [r for r in self.requests_of(sidekick) if r['id'] == pending['id']]
        self.assertEqual({r['state'] for r in self.requests_of(sidekick)}, {'sent'})
        self.assertEqual((settled['id'], settled['state']), (pending['id'], 'sent'))
        self.assertEqual(settled['seq'], pending['seq'])
        self.assertEqual(self.dirty_role_files(), [])

    def test_threads_settles_a_pending_request_from_the_threads_own_export(self):
        sidekick = amp_thread(1)
        self.ready_path().unlink()
        self.role_ready('first-orb', sidekick, 1)
        self.run_sh('sync', '--branch', str(self.store))
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        (self.fake / 'amp-kill-after-delivery').write_text('')
        self.run_sh('dispatch', str(self.store), str(b), '--timeout', '1', code=None)
        [pending] = [r for r in self.requests_of(sidekick) if r['state'] == 'pending']
        self.assertEqual(len(self.briefs_in(sidekick)), 1)
        # The next listing finds the request's id in the thread and settles it, with nothing sent again.
        self.run_sh('threads', str(self.store))
        [settled] = [r for r in self.requests_of(sidekick) if r['id'] == pending['id']]
        self.assertEqual(settled['state'], 'sent')
        self.assertEqual(len(self.briefs_in(sidekick)), 1)

    def test_threads_fails_a_pending_request_the_thread_never_received_once_it_ended_a_turn(
        self,
    ):
        sidekick = amp_thread(1)
        self.ready_path().unlink()
        self.role_ready('first-orb', sidekick, 1)
        self.run_sh('sync', '--branch', str(self.store))
        died = 'import os\namp.deliver = lambda *a: os._exit(9)'
        code, out = self.send_to_thread(sidekick, 'pstack-kitchen BRIEF z', before=died)
        self.assertEqual(code, 9, out)
        [pending] = [r for r in self.requests_of(sidekick) if r['state'] == 'pending']
        self.assertEqual(self.briefs_in(sidekick), [])
        # The last turn ended before the request was made: the message may yet arrive.
        self.set_state(sidekick, 'idle', '2020-01-01T00:00:00.000Z')
        self.run_sh('threads', str(self.store))
        self.assertEqual(
            [
                r['state']
                for r in self.requests_of(sidekick)
                if r['id'] == pending['id']
            ],
            ['pending'],
        )
        self.set_state(sidekick, 'idle', '2999-01-01T00:00:00.000Z')
        self.run_sh('threads', str(self.store))
        self.assertEqual(
            [
                r['state']
                for r in self.requests_of(sidekick)
                if r['id'] == pending['id']
            ],
            ['failed'],
        )

    def test_threads_marks_a_pending_relay_on_the_branch_stale_once_the_thread_ended_a_turn(
        self,
    ):
        sidekick = amp_thread(1)
        self.set_state(sidekick, 'tool_use')
        record, how = self.relay_of('STEER one')
        self.assertEqual(how, 'made')

        def relays():
            return json.loads(
                self.remote_git('show', f'kitchen/demo:threads/{sidekick}.json')
            )['relays']

        self.run_sh('threads', str(self.store))
        self.assertEqual([r['state'] for r in relays()], ['pending'])
        self.set_state(sidekick, 'idle', '2999-01-01T00:00:00.000Z')
        self.run_sh('threads', str(self.store))
        self.assertEqual(
            [(r['id'], r['state']) for r in relays()], [(record['id'], 'stale')]
        )

    def as_master_py(self, body):
        """Runs the Python `body` in the master's own process with amp and amp_routing imported: (exit code, output)."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp, amp_routing\n'
            f'store = {str(self.store)!r}\n{body}\n'
        )
        env = {**self.env, 'AMP_THREAD_ID': AMP_MASTER}
        env.pop('PSTACK_MUX')
        result = subprocess.run(
            [sys.executable, '-I', '-c', code],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return result.returncode, result.stdout + result.stderr

    def record_on_branch(self, thread):
        return json.loads(
            self.remote_git('show', f'kitchen/demo:threads/{thread}.json')
        )

    def test_a_thread_record_keeps_its_first_attributes_never_moves_back_and_skips_a_non_numeric_field(
        self,
    ):
        self.rotated_run()
        old, new, late, bad = amp_thread(1), amp_thread(2), amp_thread(7), amp_thread(8)
        code, out = self.as_master_py(
            f'amp.record_thread({late!r}, {{"name": "demo-sidekick", "role": "sidekick", '
            f'"store": store, "at": 123}})'
        )
        self.assertEqual(code, 0, out)
        # A record first written at retire time is not the current thread's: it takes none of the registry's attributes for the name.
        first = self.record_on_branch(late)
        self.assertEqual(
            (
                first['state'],
                first['pane'],
                first['kind'],
                first['args'],
                first['generation'],
            ),
            ('retired', '', '', [], 0),
        )
        self.assertNotEqual(self.record_on_branch(new)['pane'], '')
        # A stale threads.json entry (no archive in it) does not take an archived record back to retired.
        code, out = self.as_master_py(
            f'amp.record_thread({old!r}, {{"archived": 5}})\n'
            f'amp_routing.lifecycle(store, {old!r}, {{"name": "demo-sidekick", "role": "sidekick", "at": 9}})'
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record_on_branch(old)['state'], 'archived')
        # A value on the branch that is no number is skipped with a warning, and the rest of the records are still read.
        (self.store / 'threads' / f'{bad}.json').write_text(
            json.dumps(
                {
                    'run': self.state()['run_id'],
                    'role': 'sidekick',
                    'thread': bad,
                    'name': 'demo-sidekick',
                    'state': 'started',
                    'started': 'soon',
                }
            )
        )
        out = self.run_sh('threads', str(self.store))
        self.assertIn('skipping non-numeric started', out)
        self.assertRegex(out, rf'(?m)^{new}  sidekick  ')
        self.assertNotIn('Traceback', out)

    def test_requests_are_ordered_by_a_sequence_that_counts_across_the_run_not_by_the_clock(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.dispatch(self.brief('002', 'more', ['src/**', 'tests/**']))
        seqs = [r['seq'] for r in self.requests_of(amp_thread(1))]
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertGreaterEqual(len(seqs), 2)
        self.assertEqual(
            {r['state'] for r in self.requests_of(amp_thread(1))}, {'sent'}
        )

    def test_a_roles_notify_leaves_the_masters_thread_records_untouched(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        before = self.remote_git('rev-parse', 'kitchen/demo:threads').strip()
        self.as_orb(clone, 'notify', str(orb_store), str(report))
        self.assertIn('reports/001-util.md', self.branch_tree())
        self.assertEqual(
            self.remote_git('rev-parse', 'kitchen/demo:threads').strip(), before
        )
        status = subprocess.run(
            ['git', 'status', '--porcelain', '--untracked-files=all', '--', 'threads'],
            cwd=orb_store,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        self.assertEqual(status, '')

    def drain_paths(self):
        return sorted(p for p in self.branch_tree() if p.startswith('drains/'))

    def sent_requests(self, thread):
        return [r['id'] for r in self.requests_of(thread) if r['state'] == 'sent']

    def test_a_masters_message_names_its_request_beside_its_message_id(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        sidekick = amp_thread(1)
        for request in self.sent_requests(sidekick):
            self.assertTrue(
                any(
                    f'pstack-msg: {request}\npstack-request: {request}' in m
                    for m in self.thread_messages(sidekick)
                ),
                request,
            )

    def test_a_sidekicks_finish_publishes_its_report_event_and_receipts_in_one_commit(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        sidekick = amp_thread(1)
        requests = self.sent_requests(sidekick)
        self.assertTrue(requests)
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        before = self.remote_git('rev-parse', 'kitchen/demo').strip()
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        [commit] = self.remote_git('rev-list', f'{before}..kitchen/demo').split()
        changed = set(
            self.remote_git('show', '--name-only', '--format=', commit).split()
        )
        self.assertEqual(
            changed,
            {
                'reports/001-util.md',
                'events/sidekick-g1.tsv',
                *(f'drains/sidekick-g1/{r}.json' for r in requests),
            },
        )
        receipt = json.loads(
            self.remote_git(
                'show', f'kitchen/demo:drains/sidekick-g1/{requests[-1]}.json'
            )
        )
        self.assertEqual(
            receipt,
            {
                'run': self.state()['run_id'],
                'role': 'sidekick',
                'generation': 1,
                'thread': sidekick,
                'request': requests[-1],
                'outputs': [
                    {
                        'path': 'reports/001-util.md',
                        'sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
                    }
                ],
                'code': [head],
            },
        )

    def test_a_finish_whose_push_is_refused_leaves_no_receipt_on_origin_and_a_retry_publishes_it(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        requests = self.sent_requests(amp_thread(1))
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text(
            '#!/bin/sh\nwhile read old new ref; do\n'
            '  [ "$ref" = refs/heads/kitchen/demo ] && exit 1\n'
            'done\nexit 0\n'
        )
        hook.chmod(0o755)
        tip = self.remote_git('rev-parse', 'kitchen/demo').strip()
        messages = len(list((self.fake / 'amp-threads').glob(f'{AMP_MASTER}/*.txt')))
        self.as_orb(clone, 'finish', str(orb_store), str(report), code=1)
        self.assertEqual(self.remote_git('rev-parse', 'kitchen/demo').strip(), tip)
        self.assertEqual(self.drain_paths(), [])
        self.assertEqual(
            len(list((self.fake / 'amp-threads').glob(f'{AMP_MASTER}/*.txt'))), messages
        )
        hook.unlink()
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(
            self.drain_paths(),
            [f'drains/sidekick-g1/{r}.json' for r in sorted(requests)],
        )

    def test_a_finish_whose_push_is_refused_once_leaves_one_report_row_and_one_receipt_per_request_on_origin(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        requests = self.sent_requests(amp_thread(1))
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text(
            '#!/bin/sh\nwhile read old new ref; do\n'
            '  [ "$ref" = refs/heads/kitchen/demo ] && exit 1\n'
            'done\nexit 0\n'
        )
        hook.chmod(0o755)
        self.as_orb(clone, 'finish', str(orb_store), str(report), code=1)
        hook.unlink()
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        rows = [
            line.split('\t')
            for line in self.remote_git(
                'show', 'kitchen/demo:events/sidekick-g1.tsv'
            ).splitlines()
            if line.split('\t')[3:5] == ['report', '001-util']
        ]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(
            self.drain_paths(),
            [f'drains/sidekick-g1/{r}.json' for r in sorted(requests)],
        )

    def test_a_publication_between_a_refused_finish_and_its_retry_leaves_one_report_row_and_a_clean_store(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        requests = self.sent_requests(amp_thread(1))
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text(
            '#!/bin/sh\nwhile read old new ref; do\n'
            '  [ "$ref" = refs/heads/kitchen/demo ] && exit 1\n'
            'done\nexit 0\n'
        )
        hook.chmod(0o755)
        self.as_orb(clone, 'finish', str(orb_store), str(report), code=1)
        self.assertEqual(
            self.store_git(orb_store, 'status', '--porcelain', '--', 'events'), ''
        )
        hook.unlink()
        other = self.write_report(orb_store, '002-more.md', 'done', head)
        self.as_orb(clone, 'notify', str(orb_store), str(other))
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        rows = [
            line.split('\t')[3:5]
            for line in self.remote_git(
                'show', 'kitchen/demo:events/sidekick-g1.tsv'
            ).splitlines()
            if '\treport\t' in line
        ]
        self.assertEqual(sorted(rows), [['report', '001-util'], ['report', '002-more']])
        self.assertEqual(
            self.drain_paths(),
            [f'drains/sidekick-g1/{r}.json' for r in sorted(requests)],
        )
        self.assertEqual(self.store_git(orb_store, 'status', '--porcelain'), '')

    def test_a_notify_after_a_receipt_for_an_older_request_writes_one_for_the_new_request_and_keeps_the_old(
        self,
    ):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        sidekick = amp_thread(1)
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'notify', str(orb_store), str(report))
        first = self.drain_paths()
        self.assertTrue(first)
        code, out = self.send_to_thread(sidekick, 'pstack-kitchen NOTE x')
        self.assertEqual(code, 0, out)
        [new] = set(self.sent_requests(sidekick)) - {Path(p).stem for p in first}
        later = self.write_report(orb_store, '002-more.md', 'done', head)
        self.as_orb(clone, 'notify', str(orb_store), str(later))
        self.assertEqual(
            self.drain_paths(),
            sorted([*first, f'drains/sidekick-g1/{new}.json']),
        )
        receipt = json.loads(
            self.remote_git('show', f'kitchen/demo:drains/sidekick-g1/{new}.json')
        )
        self.assertEqual(receipt['outputs'][0]['path'], 'reports/002-more.md')
        old = json.loads(self.remote_git('show', f'kitchen/demo:{first[0]}'))
        self.assertEqual(old['outputs'][0]['path'], 'reports/001-util.md')

    def send_as_master(self, text, name='demo-sidekick', before=''):
        """Amp.send of a text to a new thread of `name`, from the master's own process, after running the Python `before`: (exit code, output)."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp\n'
            f'{before}\n'
            f'print(amp.Amp().send({name!r}, {text!r}, "", ["-m", "x/y"], {str(self.store)!r}))\n'
        )
        env = {**self.env, 'AMP_THREAD_ID': AMP_MASTER}
        env.pop('PSTACK_MUX')
        result = subprocess.run(
            [sys.executable, '-I', '-c', code],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return result.returncode, result.stdout + result.stderr

    def test_a_launch_that_was_delivered_and_never_recorded_is_a_pending_launch_record_and_is_not_launched_again(
        self,
    ):
        (self.fake / 'amp-kill-after-delivery').write_text('')
        self.send_as_master('pstack-kitchen BRIEF x')
        self.assertFalse((self.fake / 'amp-kill-after-delivery').exists())
        launches = [p for p in self.branch_tree() if p.startswith('threads/launch-')]
        self.assertEqual(len(launches), 1)
        launch = json.loads(self.remote_git('show', f'kitchen/demo:{launches[0]}'))
        [request] = launch['requests']
        self.assertEqual(
            (launch['name'], launch['role'], request['state']),
            ('demo-sidekick', 'sidekick', 'pending'),
        )
        started = len(list((self.fake / 'amp-threads').iterdir()))
        code, out = self.send_as_master('pstack-kitchen BRIEF x')
        self.assertEqual(code, 0, out)
        self.assertIn(f'earlier launch of demo-sidekick (request {request["id"]})', out)
        self.assertEqual(len(list((self.fake / 'amp-threads').iterdir())), started)

    def test_a_launch_that_raised_before_the_amp_call_is_failed_and_the_role_launches_again(
        self,
    ):
        before = (
            'def broken(path):\n'
            '    raise OSError("no space left")\n'
            'amp.pack_build = broken'
        )
        started = list((self.fake / 'amp-threads').iterdir())
        code, out = self.send_as_master('pstack-kitchen BRIEF x', before=before)
        self.assertNotEqual(code, 0, out)
        self.assertIn('no space left', out)
        self.assertEqual(list((self.fake / 'amp-threads').iterdir()), started)
        [launch] = [p for p in self.branch_tree() if p.startswith('threads/launch-')]
        [request] = json.loads(self.remote_git('show', f'kitchen/demo:{launch}'))[
            'requests'
        ]
        self.assertEqual(request['state'], 'failed')
        code, out = self.send_as_master('pstack-kitchen BRIEF x')
        self.assertEqual(code, 0, out)
        self.assertNotIn('earlier launch', out)
        found = re.search(r'T-[0-9a-f-]{36}', out)
        assert found is not None, out
        self.assertEqual({r['state'] for r in self.requests_of(found[0])}, {'sent'})

    def test_a_launch_that_succeeds_replaces_its_launch_record_with_the_threads_own(
        self,
    ):
        code, out = self.send_as_master('pstack-kitchen BRIEF y')
        self.assertEqual(code, 0, out)
        found = re.search(r'T-[0-9a-f-]{36}', out)
        assert found is not None, out
        thread = found[0]
        tree = self.branch_tree()
        self.assertIn(f'threads/{thread}.json', tree)
        self.assertEqual([p for p in tree if p.startswith('threads/launch-')], [])
        [request] = self.requests_of(thread)
        self.assertEqual(request['state'], 'sent')

    def send_to_thread(self, thread, text, name='demo-sidekick', before=''):
        """Amp.send of a text to an existing thread, from the master's own process, after running the Python `before`: (exit code, output)."""
        code = (
            'import sys\n'
            f'sys.path[:0] = [{str(root / "skills/pstack-pair/scripts")!r}]\n'
            'from pstack_core.mux import amp\n'
            f'{before}\n'
            f'print(amp.Amp().send({name!r}, {text!r}, {thread!r}, [], {str(self.store)!r}))\n'
        )
        env = {**self.env, 'AMP_THREAD_ID': AMP_MASTER}
        env.pop('PSTACK_MUX')
        result = subprocess.run(
            [sys.executable, '-I', '-c', code],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return result.returncode, result.stdout + result.stderr

    def test_a_relay_and_a_request_are_pending_in_the_branch_records_and_take_master_in_a_fresh_clone_rebuilds_the_threads(
        self,
    ):
        self.rotated_run()
        old, new = amp_thread(1), amp_thread(2)
        self.set_state(new, 'tool_use')
        record, how = self.relay_of('STEER one')
        self.assertEqual(how, 'made')
        [relayed] = json.loads(
            self.remote_git('show', f'kitchen/demo:threads/{new}.json')
        )['relays']
        self.assertEqual((relayed['id'], relayed['state']), (record['id'], 'pending'))
        self.set_state(new, 'idle')
        (self.fake / 'amp-kill-after-delivery').write_text('')
        self.send_to_thread(new, 'pstack-kitchen NOTE x')
        self.assertFalse((self.fake / 'amp-kill-after-delivery').exists())
        self.filled_handoff()
        out = self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(3)
        self.assertIn(f'in thread {master2}', out)
        clone, taken = self.fresh_orb('master2-orb')
        state = Path(self.orb_env['XDG_STATE_HOME']) / 'pstack/amp'
        self.assertFalse(state.exists())
        self.as_orb(
            clone, 'sync', '--branch', str(taken), 'kitchen/demo', thread=master2
        )
        for cache in ('threads.json', 'outbox.json'):
            (state / cache).unlink(missing_ok=True)
        out = self.as_orb(clone, 'take-master', str(taken), thread=master2)
        self.assertIn(f'master: demo-master in amp:{master2}', out)
        known = json.loads((state / 'threads.json').read_text())
        self.assertEqual(
            (known[old]['role'], known[new]['role']), ('sidekick', 'sidekick')
        )
        self.assertTrue(known[old]['at'])
        registry = json.loads((state / 'registry.json').read_text())
        self.assertEqual(registry['agents']['demo-sidekick']['thread'], new)
        record_in_clone = json.loads((taken / 'threads' / f'{new}.json').read_text())
        self.assertEqual([r['state'] for r in record_in_clone['relays']], ['pending'])
        self.assertEqual(
            [r['state'] for r in record_in_clone['requests']][-1], 'pending'
        )
        out = self.as_orb(clone, 'threads', str(taken), thread=master2)
        self.assertRegex(out, rf'(?m)^{old}  sidekick  ')
        self.assertRegex(out, rf'(?m)^{new}  sidekick  ')
        # The relay's bundle is on the other orb: here it is never delivered, so relay-done finds nothing to settle.
        out = self.as_orb(
            clone, 'relay-done', str(taken), record['id'], thread=master2, code=5
        )
        self.assertIn(f'no relay {record["id"]}', out)
        self.assertEqual(
            json.loads(self.remote_git('show', f'kitchen/demo:threads/{new}.json'))[
                'relays'
            ][0]['state'],
            'pending',
        )

    @contextlib.contextmanager
    def tampered(self, edit):
        """origin's store branch with `edit(clone dir)` committed on top of it, then reverted by a commit of its own when the block ends: the tip
        only moves forward, so the master's checkout, which a command fast-forwards, never holds a commit origin dropped."""
        clone = Path(self.tmp.name) / 'tamper'
        shutil.rmtree(clone, ignore_errors=True)
        self.git_in(
            Path(self.tmp.name),
            'clone',
            '-q',
            '-b',
            'kitchen/demo',
            str(self.remote),
            str(clone),
        )
        edit(clone)
        self.git_in(clone, 'add', '-A')
        self.git_in(clone, 'commit', '-q', '-m', 'tamper')
        self.git_in(clone, 'push', '-q', 'origin', 'HEAD:refs/heads/kitchen/demo')
        try:
            yield
        finally:
            self.git_in(clone, 'revert', '--no-edit', 'HEAD')
            self.git_in(clone, 'push', '-q', 'origin', 'HEAD:refs/heads/kitchen/demo')

    def git_in(self, where, *args):
        return subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@localhost', *args],
            cwd=where,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout

    def drained_old_thread(self):
        """A rotated run whose old sidekick thread (g1) drained every request it was sent and is idle, and whose successor (g2) has sent READY.
        Returns (the old thread, its receipts' paths on the branch)."""
        self.rotated_run()
        self.role_ready('second-orb', amp_thread(2), 2)
        self.run_sh('sync', '--branch', str(self.store))
        self.run_sh('spawn', str(self.store))
        old = amp_thread(1)
        self.set_state(old, 'idle', '2999-01-01T00:00:00.000Z')
        return old, [p for p in self.drain_paths() if 'sidekick-g1/' in p]

    def listed(self, thread):
        """How `threads` lists the thread: what follows its role and state."""
        out = self.run_sh('threads', str(self.store))
        [line] = [ln for ln in out.splitlines() if ln.startswith(thread)]
        return line.split('  ', 2)[2]

    def test_under_git_a_sidekick_thread_is_archivable_only_on_drain_receipts_that_origin_holds(
        self,
    ):
        old, receipts = self.drained_old_thread()
        self.assertTrue(receipts)
        self.assertEqual(self.listed(old), 'idle  archivable')

        def remove_last(clone):
            (clone / receipts[-1]).unlink()

        def edit_receipts(change):
            def edit(clone):
                for rel in receipts:
                    doc = json.loads((clone / rel).read_text())
                    change(doc)
                    (clone / rel).write_text(json.dumps(doc))

            return edit

        def move_to_g2(clone):
            for rel in receipts:
                target = clone / rel.replace('sidekick-g1', 'sidekick-g2')
                target.parent.mkdir(parents=True, exist_ok=True)
                (clone / rel).rename(target)

        def only_acks(clone):
            for rel in receipts:
                (clone / rel).unlink()
            (clone / 'acks').mkdir(exist_ok=True)
            (clone / 'acks/sidekick-g1.tsv').write_text('reports/001-util.md\n')

        def other_sha(clone):
            doc = json.loads((clone / receipts[-1]).read_text())
            doc['outputs'][0]['sha256'] = '0' * 64
            (clone / receipts[-1]).write_text(json.dumps(doc))

        cases = [
            ('the last request has no receipt (its report push failed)', remove_last),
            (
                'a receipt of another thread',
                edit_receipts(lambda d: d.update(thread=amp_thread(9))),
            ),
            (
                'a receipt of another generation',
                edit_receipts(lambda d: d.update(generation=2)),
            ),
            ("receipts in the other generation's directory", move_to_g2),
            ("a receipt whose sha256 is not its output's at its commit", other_sha),
            ('a parked-note ack and no receipt', only_acks),
        ]
        for label, edit in cases:
            with self.subTest(label), self.tampered(edit):
                self.assertRegex(self.listed(old), r'^idle  kept: no drain receipt')
        self.assertEqual(self.listed(old), 'idle  archivable')
        real = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        self.git('remote', 'set-url', 'origin', str(self.remote) + '-gone')
        try:
            self.assertRegex(self.listed(old), r'^idle  kept: freshness unknown')
        finally:
            self.git('remote', 'set-url', 'origin', real)
        code, out = self.send_to_thread(
            old, 'pstack-kitchen NOTE after the last receipt'
        )
        self.assertEqual(code, 0, out)
        [newest] = [
            r
            for r in self.requests_of(old)
            if r['id'] not in {Path(p).stem for p in receipts}
        ]
        self.assertEqual(
            self.listed(old), f'idle  kept: no drain receipt for request {newest["id"]}'
        )
        self.assertEqual(self.dirty_role_files(), [])

    def test_a_successor_rewriting_a_drained_output_does_not_keep_the_old_thread(
        self,
    ):
        old, receipts = self.drained_old_thread()
        self.assertEqual(self.listed(old), 'idle  archivable')
        [out] = json.loads(self.remote_git('show', f'kitchen/demo:{receipts[-1]}'))[
            'outputs'
        ]
        name = Path(out['path']).name
        clone, orb_store = self.fresh_orb('g2-orb')
        self.as_orb(
            clone,
            'sync',
            '--branch',
            str(orb_store),
            'kitchen/demo',
            thread=amp_thread(2),
        )
        head = self.commit_in(clone, 'rewrite')
        rewrite = self.write_report(orb_store, name, 'done', head)
        self.as_orb(clone, 'notify', str(orb_store), str(rewrite), thread=amp_thread(2))
        self.assertNotEqual(
            hashlib.sha256(rewrite.read_bytes()).hexdigest(), out['sha256']
        )
        self.assertEqual(
            hashlib.sha256(
                self.remote_git('show', f'kitchen/demo:{out["path"]}').encode()
            ).hexdigest(),
            hashlib.sha256(rewrite.read_bytes()).hexdigest(),
        )
        self.assertEqual(self.listed(old), 'idle  archivable')

    def test_a_rotated_sidekick_thread_is_archived_on_its_own_generations_receipts_and_kept_without_them(
        self,
    ):
        old, receipts = self.drained_old_thread()
        clone, orb_store = self.fresh_orb('old-orb')
        self.as_orb(
            clone, 'sync', '--branch', str(orb_store), 'kitchen/demo', thread=old
        )
        head = self.commit_in(clone, 'late')
        late = self.write_report(orb_store, '003-late.md', 'partial', head)
        out = self.as_orb(clone, 'notify', str(orb_store), str(late), thread=old)
        self.assertIn('drain: generation 1 wrote nothing as generation 2', out)
        written = [p for p in self.drain_paths() if 'sidekick-g2/' in p]
        self.assertTrue(written)
        for rel in written:
            receipt = json.loads(self.remote_git('show', f'kitchen/demo:{rel}'))
            self.assertEqual(receipt['thread'], amp_thread(2))
        archived = self.fake / 'amp-archived'
        with self.tampered(lambda c: (c / receipts[-1]).unlink()):
            out = self.run_sh('threads', str(self.store), '--archive')
            self.assertIn(f'kept {old}: no drain receipt for request', out)
        self.assertNotIn(old, archived.read_text() if archived.exists() else '')
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertIn(f'archived {old} (sidekick)', out)
        self.assertEqual(archived.read_text().split(), [old])
        self.assertEqual(self.dirty_role_files(), [])

    def test_under_git_a_retired_verifier_thread_is_archived_with_no_drain_receipt(
        self,
    ):
        self.rotated_run()
        archived = self.fake / 'amp-archived'
        verifier = amp_thread(7)
        self.retire_threads(
            {verifier: {'name': 'demo-verifier', 'store': str(self.store)}}
        )
        out = self.run_sh('threads', str(self.store), '--archive')
        self.assertIn(f'archived {verifier} (verifier)', out)
        self.assertEqual(archived.read_text().split(), [verifier])
        self.assertEqual(self.dirty_role_files(), [])

    def refuse_master_pushes_where(self, condition):
        """origin refuses a push to kitchen/* while `condition` (a shell test over $old and $new, the tips) holds."""
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text(
            '#!/bin/sh\nwhile read old new ref; do\n'
            '  case "$ref" in refs/heads/kitchen/*)\n'
            f'    if {condition}; then echo refused >&2; exit 1; fi;;\n'
            '  esac\ndone\n'
        )
        hook.chmod(0o755)

    def test_a_master_rollback_that_cannot_be_published_leaves_the_launch_error_standing(
        self,
    ):
        self.filled_handoff()
        (self.fake / 'amp-ox-fail').write_text('boom launch\n')
        # Only the rollback push is refused: the tip it replaces holds the hand-over and the new tree does not.
        self.refuse_master_pushes_where(
            'git show "$old:pair.json" 2>/dev/null | grep -q \'"previous"\''
            ' && ! git show "$new:pair.json" | grep -q \'"previous"\''
        )
        out = self.run_sh('rotate-master', str(self.store), code=None)
        self.assertIn('the next master thread did not start: boom launch', out)
        self.assertIn('warning: the rollback could not be published:', out)
        self.assertNotIn('Traceback', out)

    def test_a_sidekick_rotation_rollback_that_cannot_be_published_leaves_the_spawn_error_standing(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.store / 'reports/000-ready-g1.md').unlink()
        self.role_ready('first-orb', amp_thread(1), 1, report=b.name)
        self.run_sh('sync', '--branch', str(self.store))
        doc = self.state()
        doc['sidekick']['chain'] = [
            {'kind': 'amp', 'args': ['--model', 'x/one']},
            {'kind': 'amp', 'args': ['--model', 'y/two']},
        ]
        doc['sidekick']['chain_index'] = 1
        (self.store / 'pair.json').write_text(json.dumps(doc))
        (self.fake / 'amp-preflight-fail').write_text('x/one\ny/two\n')
        self.refuse_master_pushes_where(
            'git show "$new:events/master-g0.tsv" 2>/dev/null | grep -q "$(printf \'\\trotate\\t\')"'
        )
        out = self.run_sh('rotate', str(self.store), code=None)
        self.assertIn('every agent of the sidekick chain failed its preflight', out)
        self.assertIn('warning: the rollback could not be published:', out)

    def rotate_with_the_final_publish_refused(self, code, ready=False):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.store / 'reports/000-ready-g1.md').unlink()
        self.role_ready('first-orb', amp_thread(1), 1, report=b.name)
        self.run_sh('sync', '--branch', str(self.store))
        # The spawn's own publishes go through; the last push of a rotation is the one that carries nest-pending.tsv.
        self.refuse_master_pushes_where(
            'git diff --name-only "$old" "$new" | grep -q nest-pending.tsv'
        )
        if ready:
            (self.store / 'reports/000-ready-g2.md').write_text('status: done\n')
        return self.run_sh('rotate', str(self.store), code=code)

    def test_a_rotation_whose_spawn_returned_4_keeps_that_exit_code_when_its_final_publish_fails(
        self,
    ):
        out = self.rotate_with_the_final_publish_refused(4)
        self.assertIn('warning: the rotation could not be published:', out)
        self.assertIn('was not pushed', out)

    def test_a_rotation_that_spawned_still_raises_the_error_of_its_final_publish(self):
        out = self.rotate_with_the_final_publish_refused(1, ready=True)
        self.assertIn('was not pushed', out)
        self.assertNotIn('could not be published', out)

    def test_a_rotation_whose_spawn_dies_still_publishes_its_rotate_event(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.store / 'reports/000-ready-g1.md').unlink()
        self.role_ready('first-orb', amp_thread(1), 1, report=b.name)
        self.run_sh('sync', '--branch', str(self.store))
        doc = self.state()
        doc['sidekick']['chain'] = [
            {'kind': 'amp', 'args': ['--model', 'x/one']},
            {'kind': 'amp', 'args': ['--model', 'y/two']},
        ]
        doc['sidekick']['chain_index'] = 1
        (self.store / 'pair.json').write_text(json.dumps(doc))
        (self.fake / 'amp-preflight-fail').write_text('x/one\ny/two\n')
        out = self.run_sh('rotate', str(self.store), code=None)
        self.assertIn('every agent of the sidekick chain failed its preflight', out)
        self.assertEqual(self.dirty_role_files(), [])
        self.assertIn(
            '\trotate\t',
            self.remote_git('show', 'kitchen/demo:events/master-g0.tsv'),
        )

    def test_a_master_rotated_on_the_branch_is_taken_over_in_a_clone_that_holds_nothing_and_a_role_there_learns_the_new_master(
        self,
    ):
        self.ready_path().unlink()
        self.filled_handoff()
        out = self.run_sh('rotate-master', str(self.store))
        master2 = amp_thread(2)
        self.assertIn(f'(generation 2) in thread {master2}', out)
        self.assertEqual(self.dirty_role_files(), [])
        self.assertEqual(
            subprocess.run(
                ['git', 'status', '--porcelain', '--untracked-files=all'],
                cwd=self.store,
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout,
            '',
        )
        on_branch = json.loads(self.remote_git('show', 'kitchen/demo:pair.json'))
        self.assertEqual(
            (
                on_branch['master']['generation'],
                on_branch['master']['pane_id'],
                on_branch['master']['previous']['name'],
            ),
            (2, f'amp:{master2}', 'demo-master'),
        )
        self.assertIn('sessions/master-1-handoff.md', self.branch_tree())
        clone, taken = self.fresh_orb('master2-orb')
        self.as_orb(
            clone,
            'sync',
            '--branch',
            str(taken),
            'kitchen/demo',
            thread=master2,
        )
        out = self.as_orb(clone, 'take-master', str(taken), thread=master2)
        self.assertIn(f'master: demo-master in amp:{master2} (generation 2)', out)
        on_branch = json.loads(self.remote_git('show', 'kitchen/demo:pair.json'))
        self.assertNotIn('previous', on_branch['master'])
        self.assertEqual(on_branch['master']['name'], 'demo-master')
        clone, role_store = self.fresh_orb('role-orb')
        self.as_orb(clone, 'sync', '--branch', str(role_store), 'kitchen/demo')
        registry = json.loads(
            (
                Path(self.orb_env['XDG_STATE_HOME']) / 'pstack/amp/registry.json'
            ).read_text()
        )
        self.assertEqual(registry['agents']['demo-master']['thread'], master2)

    def test_a_master_thread_that_does_not_start_leaves_no_hand_over_on_the_branch(
        self,
    ):
        self.ready_path().unlink()
        self.filled_handoff()
        (self.fake / 'amp-ox-fail').write_text('Unknown model provider\n')
        out = self.run_sh('rotate-master', str(self.store), '--model', 'nope/x', code=2)
        self.assertIn(
            'the next master thread did not start: Unknown model provider', out
        )
        self.assertEqual(self.dirty_role_files(), [])
        on_branch = json.loads(self.remote_git('show', 'kitchen/demo:pair.json'))
        self.assertNotIn('previous', on_branch['master'])
        self.assertEqual(on_branch['master'].get('generation', 1), 1)
        clone, taken = self.fresh_orb('master2-orb')
        self.as_orb(
            clone, 'sync', '--branch', str(taken), 'kitchen/demo', thread=amp_thread(2)
        )
        fresh = json.loads((taken / 'pair.json').read_text())
        self.assertNotIn('previous', fresh['master'])
        self.assertEqual(fresh['master'].get('generation', 1), 1)

    def test_a_role_thread_cannot_write_pair_json_under_git_and_the_master_and_the_handing_over_master_can(
        self,
    ):
        self.ready_path().unlink()
        clone, orb_store = self.role_orb()
        before = (orb_store / 'pair.json').read_bytes()
        code, out = self.update_as(
            amp_thread(1), orb_store, cwd=clone, env=self.orb_env
        )
        self.assertNotEqual(code, 0)
        self.assertIn(
            f'pair.json: belongs to the master (amp:{AMP_MASTER}), not to this thread (amp:{amp_thread(1)})',
            out,
        )
        self.assertIn('OwnershipError', out)
        self.assertEqual((orb_store / 'pair.json').read_bytes(), before)
        self.assertFalse((orb_store / '.pair.lock').exists())
        code, out = self.update_as(AMP_MASTER, self.store)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state()['probe'], 1)
        self.assertEqual(self.dirty_role_files(), [])
        # The master handing over still writes until the next one takes over; the next master is the master itself.
        doc = self.state()
        doc['master']['previous'] = {'name': 'old', 'pane_id': f'amp:{amp_thread(4)}'}
        (self.store / 'pair.json').write_text(json.dumps(doc))
        code, out = self.update_as(amp_thread(4), self.store)
        self.assertEqual(code, 0, out)

    def test_a_verifier_threads_scratch_under_git_makes_and_removes_its_worktree_and_leaves_pair_json_alone(
        self,
    ):
        clone, orb_store = self.role_orb('verifier')
        before = (orb_store / 'pair.json').read_bytes()
        verifier = amp_thread(3)
        out = self.as_orb(
            clone, 'scratch', str(orb_store), '001-util-verify', thread=verifier
        )
        path = out.strip().splitlines()[0]
        self.assertTrue(Path(path).is_dir(), out)
        self.assertEqual((orb_store / 'pair.json').read_bytes(), before)
        self.as_orb(
            clone,
            'scratch',
            str(orb_store),
            '001-util-verify',
            '--remove',
            thread=verifier,
        )
        self.assertFalse(Path(path).exists())
        self.assertEqual((orb_store / 'pair.json').read_bytes(), before)
        self.assertNotIn('OwnershipError', out)

    def test_a_message_to_a_role_announces_the_store_commit_and_carries_no_store_bundle(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        sidekick = amp_thread(1)
        sent = sorted(p.name for p in (self.fake / 'amp-threads' / sidekick).iterdir())
        self.assertEqual(
            [n for n in sent if n.endswith('.tgz')], ['1-pstack-build.tgz']
        )
        message = self.thread_messages(sidekick)[-1]
        found = re.search(r'store: kitchen/demo at ([0-9a-f]{40})', message)
        assert found is not None, message
        announced = found[1]
        # The master records the send's result after the message went out: the branch moved on from the announced commit by routing records only.
        self.remote_git('merge-base', '--is-ancestor', announced, 'kitchen/demo')
        moved = self.remote_git('diff', '--name-only', announced, 'kitchen/demo')
        self.assertEqual([p for p in moved.split() if not p.startswith('threads/')], [])
        self.assertIn(
            'briefs/001-util.md',
            self.remote_git('ls-tree', '-r', '--name-only', announced),
        )
        self.assertIn(f'pair.sh sync --branch {self.store} kitchen/demo`', message)
        self.assertNotIn('attachment URL below>', message)
        self.assertIn('briefs/001-util.md', self.branch_tree())

    def test_sync_branch_checks_the_store_out_in_a_fresh_orb_and_the_master_fetches_announced_code(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        out = self.as_thread(
            AMP_MASTER, 'sync', '--branch', str(self.store), cwd=self.repo
        )
        self.assertIn('store: kitchen/demo at ', out)
        self.assertTrue((self.store / 'reports/001-util.md').is_file())
        self.assertTrue(self.master_has(head))
        orb = Path(self.tmp.name) / 'late-orb'
        self.orb_env = {'HOME': str(orb / 'home'), 'XDG_STATE_HOME': str(orb / 'state')}
        late = orb / 'runs/demo'
        subprocess.run(
            ['git', 'clone', '-q', str(self.remote), str(orb / 'work')],
            check=True,
            capture_output=True,
            timeout=60,
        )
        out = self.as_orb(orb / 'work', 'sync', '--branch', str(late), 'kitchen/demo')
        self.assertIn('store: kitchen/demo at ', out)
        self.assertTrue(
            (late / 'briefs').is_dir() or (late / 'reports/001-util.md').is_file()
        )
        registry = json.loads((orb / 'state/pstack/amp/registry.json').read_text())
        self.assertEqual(registry['agents']['demo-master']['thread'], AMP_MASTER)

    def test_a_message_announcing_a_commit_the_store_lacks_reads_as_not_yet_fetched(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        report = self.store / 'reports' / b.name
        unknown = 'a' * 40
        thread = self.fake / 'amp-threads' / AMP_MASTER
        thread.mkdir(parents=True)
        (thread / '1.txt').write_text(
            f'pstack-kitchen REPORT {report}\nstatus: done\nsha256: 0123456789ab\nstore: kitchen/demo at {unknown}\n'
        )
        out = self.run_sh('wait', str(self.store), code=4)
        self.assertIn(
            f'announced {unknown[:9]} not yet fetched (sidekick sent {report}); run pair.sh sync --branch <store>',
            out,
        )
        self.assertNotIn('report: missing', out)

    def test_sync_branch_takes_the_branch_from_the_announcement_not_from_the_directory_name(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        orb = Path(self.tmp.name) / 'other-orb'
        self.orb_env = {'HOME': str(orb / 'home'), 'XDG_STATE_HOME': str(orb / 'state')}
        subprocess.run(
            ['git', 'clone', '-q', str(self.remote), str(orb / 'work')],
            check=True,
            capture_output=True,
            timeout=60,
        )
        out = self.as_orb(
            orb / 'work',
            'sync',
            '--branch',
            str(orb / 'runs/elsewhere'),
            'kitchen/demo',
        )
        self.assertIn('store: kitchen/demo at ', out)
        self.assertTrue((orb / 'runs/elsewhere/reports/001-util.md').is_file())
        out = self.as_orb(
            orb / 'work', 'sync', '--branch', str(orb / 'runs/none'), code=1
        )
        self.assertIn('name the branch the message announced', out)
        self.assertFalse((orb / 'runs/none').exists())

    def test_a_message_announcing_a_commit_off_the_stores_head_reads_as_not_yet_fetched(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        report = self.store / 'reports' / b.name
        (self.repo / 'elsewhere.txt').write_text('x\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'elsewhere')
        off_branch = self.git('rev-parse', 'HEAD').strip()
        thread = self.fake / 'amp-threads' / AMP_MASTER
        thread.mkdir(parents=True)
        (thread / '1.txt').write_text(
            f'pstack-kitchen REPORT {report}\nstatus: done\nsha256: 0123456789ab\nstore: kitchen/demo at {off_branch}\n'
        )
        out = self.run_sh('wait', str(self.store), code=4)
        self.assertIn(f'announced {off_branch[:9]} not yet fetched', out)
        self.assertNotIn('report: missing', out)

    def test_a_dropped_message_still_leaves_the_report_for_the_masters_wait_after_a_refresh(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, b.name, 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        for message in (self.fake / 'amp-threads' / AMP_MASTER).glob('*.txt'):
            message.unlink()
        self.assertFalse((self.store / 'reports' / b.name).exists())
        out = self.run_sh('wait', str(self.store), code=None)
        self.assertIn(f'report: {self.store}/reports/{b.name}', out)
        self.assertIn('report_status: partial', out)

    def test_a_verdict_on_the_branch_past_the_deadline_gives_the_verdict_not_a_deadline(
        self,
    ):
        self.verify_started('001')
        clone, orb_store = self.role_orb('verifier')
        verdict = Path(orb_store) / 'verdicts/001-util-v1.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        self.as_orb(clone, 'notify', str(orb_store), str(verdict), thread=amp_thread(2))
        self.set_state(amp_thread(2), 'idle', '2026-01-01T00:00:00.000Z')
        state = self.state()
        state['verifying']['deadline'] = 1
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.assertFalse((self.store / 'verdicts/001-util-v1.md').exists())
        out = self.run_sh('verify', str(self.store), '--wait', code=None)
        self.assertIn(f'verdict: {self.store}/verdicts/001-util-v1.md', out)
        self.assertNotIn('past its deadline', out)
        self.assertNotIn('no verdict at', out)

    def test_rotate_master_take_master_and_a_dispatch_say_when_freshness_is_unknown(
        self,
    ):
        from pstack_core import gitstore

        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.filled_handoff()
        self.remote.rename(self.remote.with_name('gone.git'))
        commands = {
            'a dispatch (rotate_if_due)': ('dispatch', str(self.store), str(b)),
            'take-master': ('take-master', str(self.store)),
            'rotate-master': ('rotate-master', str(self.store)),
        }
        for label, args in commands.items():
            with self.subTest(label):
                out = self.run_sh(*args, code=None)
                self.assertIn(gitstore.UNKNOWN_FRESHNESS, out)

    def test_with_origin_unreachable_wait_and_verify_say_freshness_is_unknown_and_declare_nothing(
        self,
    ):
        self.verify_started('001')
        self.remote.rename(self.remote.with_name('gone.git'))
        out = self.run_sh('wait', str(self.store), code=4)
        self.assertIn('freshness: unknown', out)
        self.assertNotIn('report: missing', out)
        self.assertNotIn('stall', out)
        state = self.state()
        state['verifying']['deadline'] = 1
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=4)
        self.assertIn('freshness: unknown', out)
        self.assertNotIn('past its deadline', out)
        self.assertNotIn('no verdict at', out)
        status = self.run_sh('status', str(self.store))
        self.assertIn('freshness: unknown', status)

    def test_verify_wait_retries_a_failed_fetch_and_returns_the_verdict_the_next_fetch_finds(
        self,
    ):
        self.verify_started('001')
        clone, orb_store = self.role_orb('verifier')
        verdict = Path(orb_store) / 'verdicts/001-util-v1.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text('# Verdict\n\nstatus: clean\nunits: 001-util\n')
        self.as_orb(clone, 'notify', str(orb_store), str(verdict), thread=amp_thread(2))
        self.set_state(amp_thread(2), 'idle', '2026-01-01T00:00:00.000Z')
        shim = Path(self.tmp.name) / 'shim'
        shim.mkdir()
        failed = Path(self.tmp.name) / 'fetch-failed'
        (shim / 'git').write_text(
            '#!/bin/sh\n'
            f'case " $* " in *" fetch "*) if [ ! -e {failed} ]; then : > {failed}; echo refused >&2; exit 1; fi;; esac\n'
            f'exec {shutil.which("git")} "$@"\n'
        )
        (shim / 'git').chmod(0o755)
        self.env['PATH'] = f'{shim}:{self.env["PATH"]}'
        self.env['KITCHEN_VERIFY_POLL_S'] = '0'
        out = self.run_sh('verify', str(self.store), '--wait', code=None)
        self.assertTrue(failed.exists())
        self.assertIn(f'verdict: {self.store}/verdicts/001-util-v1.md', out)
        self.assertNotIn('freshness: unknown', out)

    def test_verify_wait_stops_retrying_the_fetch_once_another_call_closed_the_verification(
        self,
    ):
        self.verify_started('001')
        pair = self.store / 'pair.json'
        count = Path(self.tmp.name) / 'fetches'
        shim = Path(self.tmp.name) / 'shim'
        shim.mkdir()
        close = Path(self.tmp.name) / 'close.py'
        close.write_text(
            'import json, sys\n'
            'doc = json.load(open(sys.argv[1]))\n'
            "doc['verifying']['verdict'] += '.other'\n"
            'json.dump(doc, open(sys.argv[1], "w"))\n'
        )
        (shim / 'git').write_text(
            '#!/bin/sh\n'
            'case " $* " in *" fetch "*)\n'
            f'  n=$(cat {count} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {count}\n'
            f'  if [ $n -eq 1 ]; then python3 {close} {pair}; fi\n'
            '  if [ $n -le 40 ]; then echo refused >&2; exit 1; fi;;\n'
            'esac\n'
            f'exec {shutil.which("git")} "$@"\n'
        )
        (shim / 'git').chmod(0o755)
        self.env['PATH'] = f'{shim}:{self.env["PATH"]}'
        self.env['KITCHEN_VERIFY_POLL_S'] = '0'
        self.run_sh('verify', str(self.store), '--wait', '--every', '5', code=None)
        self.assertLess(int(count.read_text()), 10)

    def test_a_diverged_store_branch_ends_the_reading_command_with_its_own_message(
        self,
    ):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(
            clone,
            'finish',
            str(orb_store),
            str(Path(orb_store) / 'reports/001-util.md'),
        )
        mine = self.write_report(self.store, '001-util.md', 'done', 'abc')
        mine.write_text(mine.read_text() + 'a different report\n')
        subprocess.run(
            ['git', 'add', 'reports/001-util.md'],
            cwd=self.store,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            [
                'git',
                'commit',
                '-q',
                '-m',
                'a writer wrote a report that is not its own',
            ],
            cwd=self.store,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        for command in (('wait', str(self.store)), ('status', str(self.store))):
            out = self.run_sh(*command, code=1)
            self.assertIn('do not combine', out)
            self.assertNotIn('freshness: unknown', out)

    def push_head(self, clone, ref):
        subprocess.run(
            ['git', 'push', '-q', 'origin', f'HEAD:refs/heads/{ref}'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )

    def test_a_head_only_on_an_unrelated_origin_branch_is_bundled_for_the_master_when_the_code_ref_push_is_refused(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        self.push_head(clone, 'feature')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('code/sidekick-g1.bundle', self.branch_tree())
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_a_head_whose_parent_sits_only_on_an_unrelated_origin_branch_is_bundled_with_that_parent_when_the_code_ref_push_is_refused(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        parent = self.commit_in(clone, 'parent')
        self.push_head(clone, 'feature')
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('code/sidekick-g1.bundle', self.branch_tree())
        self.assertFalse(self.master_has(parent))
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def bundle_prerequisites(self, store):
        header = (
            (Path(store) / 'code/sidekick-g1.bundle').read_bytes().split(b'\n\n')[0]
        )
        return [
            line[1:41].decode() for line in header.splitlines() if line.startswith(b'-')
        ]

    def on_trunk(self, clone):
        subprocess.run(
            ['git', 'checkout', '-q', '-B', 'main', 'origin/main'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )

    def push_trunk(self, clone):
        self.push_head(clone, 'main')
        return subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()

    def test_a_head_one_commit_past_origins_default_branch_is_bundled_with_only_that_commit_when_the_code_ref_push_is_refused(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        self.on_trunk(clone)
        trunk = self.remote_git('rev-parse', 'main').strip()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(self.bundle_prerequisites(orb_store), [trunk])
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_a_head_that_is_origins_default_branch_head_sends_the_report_with_no_bundle_when_the_code_ref_push_is_refused(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        self.on_trunk(clone)
        head = self.commit_in(clone, 'work')
        self.assertEqual(self.push_trunk(clone), head)
        self.assertFalse(self.master_has(head))
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('reports/001-util.md', self.branch_tree())
        self.assertNotIn('code/sidekick-g1.bundle', self.branch_tree())
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_a_bundle_cut_less_trunk_applies_after_trunk_moved_on_origin_past_the_masters_clone(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        self.on_trunk(clone)
        moved = self.commit_in(clone, 'trunk-moved')
        self.assertEqual(self.push_trunk(clone), moved)
        self.assertFalse(self.master_has(moved))
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(self.bundle_prerequisites(orb_store), [moved])
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_a_stale_tracking_ref_origin_no_longer_holds_does_not_stand_for_the_head(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        self.push_head(clone, 'kitchen-code/demo/old-g1')
        subprocess.run(
            ['git', 'fetch', '-q', 'origin'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        self.remote_git('update-ref', '-d', 'refs/heads/kitchen-code/demo/old-g1')
        self.reject_code_pushes()
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('code/sidekick-g1.bundle', self.branch_tree())
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(head))

    def test_a_head_on_another_code_ref_of_the_run_sends_the_report_and_drops_the_older_bundle(
        self,
    ):
        self.reject_code_pushes('demo/sidekick-*')
        clone, orb_store = self.role_orb()
        first = self.commit_in(clone, 'first')
        report = self.write_report(orb_store, '001-util.md', 'partial', first)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('code/sidekick-g1.bundle', self.branch_tree())
        second = self.commit_in(clone, 'second')
        self.push_head(clone, 'kitchen-code/demo/other-g1')
        self.write_report(orb_store, '001-util.md', 'partial', second)
        sent = self.announced()
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertIn('reports/001-util.md', self.branch_tree())
        self.assertNotIn('code/sidekick-g1.bundle', self.branch_tree())
        self.assertEqual(self.announced(), sent + 1)
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(second))

    def test_a_rewritten_head_is_fetched_from_the_bundle_that_replaces_the_old_one(
        self,
    ):
        self.reject_code_pushes()
        clone, orb_store = self.role_orb()
        first = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', first)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(first))
        (clone / 'work.txt').write_text('rewritten\n')
        subprocess.run(
            ['git', 'commit', '-q', '--amend', '-a', '-m', 'work again'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        second = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=clone,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        self.assertNotEqual(first, second)
        self.write_report(orb_store, '001-util.md', 'partial', second)
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.master_fetches_the_announced_code()
        self.assertTrue(self.master_has(second))
        self.assertEqual(
            self.git('rev-parse', 'refs/pstack/code/sidekick-g1.bundle'), second
        )

    def sidekick_events(self):
        return [
            row
            for row in self.remote_git(
                'show', 'kitchen/demo:events/sidekick-g1.tsv'
            ).splitlines()
            if '\treport\t' in row
        ]

    def test_a_refused_publish_leaves_no_report_row_and_the_retry_writes_exactly_one(
        self,
    ):
        clone, orb_store = self.role_orb()
        head = self.commit_in(clone, 'work')
        report = self.write_report(orb_store, '001-util.md', 'partial', head)
        token = 'gh' + 'p_' + 'a1' * 18
        report.write_text(report.read_text() + f'\nthe token is {token}\n')
        self.as_orb(clone, 'finish', str(orb_store), str(report), code=1)
        local = Path(orb_store) / 'events/sidekick-g1.tsv'
        rows = local.read_text() if local.exists() else ''
        self.assertNotIn('\treport\t', rows)
        report.write_text(
            f'# Report\n\nstatus: partial\nhead: {head}\n\nthe token is [REDACTED: github-token]\n'
        )
        self.as_orb(clone, 'finish', str(orb_store), str(report))
        self.assertEqual(len(self.sidekick_events()), 1)


for _name in dir(AmpKitchenTests):
    if _name.startswith('test_') and _name not in vars(AmpGitKitchenTests):
        setattr(AmpGitKitchenTests, _name, None)
