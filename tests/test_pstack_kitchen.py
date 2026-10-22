"""kitchen.py against a throwaway repo: the kitchen.toml schema, classify,
gate, policy, and doctor, end to end through the command line."""
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import zlib
import unittest
from pathlib import Path

import tomllib

# git and the scripts report resolved paths; on macOS the temp dir is under
# /var, a link to /private/var, so tests work in the resolved one.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())

root = Path(__file__).resolve().parents[1]
skill = root / 'skills/pstack-kitchen'
script = skill / 'scripts/kitchen.py'
sys.path.insert(0, str(script.parent))
import kitchen

sys.path.insert(0, str(root / 'tests'))
from test_pstack_core import event_log, watch_jq  # noqa: E402

LEAKED_PREFIXES = ('PSTACK_KITCHEN_', 'KITCHEN_', 'PAIR_')


def inherited_env():
    """The test runner's environment without what a kitchen or pair run exports to its agents
    (PSTACK_KITCHEN_ROLE and the like) and without the XDG data, config and cache homes, which would send HOME-based
    lookups to the host's real directories, so the result does not depend on who runs the suite."""
    return {k: v for k, v in os.environ.items()
            if k not in ('PSTACK_PLACEMENT', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME') and not k.startswith(LEAKED_PREFIXES)}

KITCHEN = textwrap.dedent('''\
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
    ''')

FILES = {
    'src/app.py': 'def main():\n    return 1\n',
    'src/util.py': 'X = 1\n',
    'tests/test_app.py': 'def test_main():\n    assert True\n',
    'docs/guide.md': '# Guide\n',
    'contracts/api.json': '{}\n',
    'README.md': '# Demo\n',
    'requirements.txt': 'requests\n',
}


class KitchenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.env = {**inherited_env(), 'XDG_STATE_HOME': str(Path(self.tmp.name) / 'state'), 'PSTACK_KITCHEN_SLOT_POLL_S': '0.2',
                    'PSTACK_KITCHEN_ROSTER': str(Path(self.tmp.name) / 'no-roster.toml'),
                    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        self.repo.mkdir()
        self.git('init', '-q')
        for path, text in {**FILES, '.agents/kitchen.toml': KITCHEN}.items():
            self.write(path, text)
        self.base = self.commit('init')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, path, text):
        f = self.repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def run_kitchen(self, *args, code=0):
        result = subprocess.run([sys.executable, str(script), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, check=False)
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
            ('*.md', 'README.md', True), ('*.md', 'docs/a.md', False),
            ('**/*.md', 'README.md', True), ('**/*.md', 'docs/x/a.md', True),
            ('src/**', 'src/a/b.py', True), ('src/**', 'srcx/a.py', False),
            ('a/**/b.py', 'a/b.py', True), ('a/**/b.py', 'a/x/y/b.py', True),
            ('crates/*/Cargo.toml', 'crates/core/Cargo.toml', True),
            ('crates/*/Cargo.toml', 'crates/a/b/Cargo.toml', False), ('?.py', 'a.py', True),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(kitchen.glob_regex(pattern).match(path)), expected)


class SchemaTests(KitchenTests):
    def test_reference_example_is_valid(self):
        k = kitchen.parse(tomllib.loads((skill / 'references/kitchen-example.toml').read_text()))
        self.assertEqual(list(k.profiles), ['ui', 'engine', 'docs'])
        self.assertEqual((k.review['self'], k.review['legacy']), (['no-comments'], []))
        self.assertEqual(k.scratch_setup, ['pnpm install --frozen-lockfile'])

    def test_validate_names_the_bad_key(self):
        cases = [
            ('fast =', 'fsat =', 'profile.app: unknown key fsat'),
            ('tests = ["tests/**"]\n', '', 'require_tests needs tests globs'),
            ("pattern = '\\bprint\\('", "pattern = '('", 'policy.forbid[0].pattern'),
            ('verify = "unit"', 'verify = "sometimes"', 'profile.contracts.verify: expected one of gates, batch, unit'),
            ('version = 1', 'version = 2', 'version: expected 1'),
        ]
        for old, new, message in cases:
            with self.subTest(new=new):
                self.assertIn(old, KITCHEN)
                self.write('.agents/kitchen.toml', KITCHEN.replace(old, new, 1))
                self.assertIn(message, self.run_kitchen('validate', code=1))
        self.write('.agents/kitchen.toml', KITCHEN + '\n[[policy.forbid]]\nid = "no-print"\npattern = "x"\nmessage = "m"\n')
        self.assertIn('policy.forbid[1].id', self.run_kitchen('validate', code=1))

    def test_heavy_takes_true_false_or_a_list_of_stages(self):
        for value, stages in [('true', {'fast', 'behavioral', 'landing'}), ('false', set()), ('["behavioral"]', {'behavioral'}),
                              ('["fast", "landing"]', {'fast', 'landing'})]:
            with self.subTest(value):
                self.write('.agents/kitchen.toml', KITCHEN.replace('sample = 0.2', f'sample = 0.2\nheavy = {value}'))
                k, _ = kitchen.load(self.repo, None, None)
                self.assertEqual(k.profiles['app'].heavy, stages)

    def test_an_unknown_heavy_stage_is_refused_naming_the_key(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = ["build"]'))
        self.assertIn('profile.app.heavy: expected true, false, or a list of stages from fast, behavioral, landing',
                      self.run_kitchen('validate', code=1))

    GENERATED = ('\n[[generated]]\nsources = ["src/**"]\noutputs = ["docs/**"]\ncommand = "make gen"\n')

    def test_a_generated_entry_parses_and_validates(self):
        self.write('.agents/kitchen.toml', KITCHEN + self.GENERATED)
        k, _ = kitchen.load(self.repo, None, None)
        (g,) = k.generated
        self.assertEqual((g.sources, g.outputs, g.command), (('src/**',), ('docs/**',), 'make gen'))
        self.run_kitchen('validate')

    def test_a_bad_generated_entry_is_refused_naming_it(self):
        for text, message in [('[[generated]]\nsources = ["src/**"]\n', 'generated[0].outputs: list at least one glob'),
                              ('[[generated]]\nsources = []\noutputs = ["a"]\n', 'generated[0].sources: list at least one glob'),
                              ('[[generated]]\nsources = ["a"]\noutputs = ["b"]\nwhen = "x"\n', 'generated[0]: unknown key when')]:
            with self.subTest(message):
                self.write('.agents/kitchen.toml', KITCHEN + '\n' + text)
                self.assertIn(message, self.run_kitchen('validate', code=1))

    def test_missing_kitchen_points_at_setup(self):
        (self.repo / '.agents/kitchen.toml').unlink()
        self.assertIn('pstack-kitchen-setup', self.run_kitchen('validate', code=1))


class ClassifyTests(KitchenTests):
    def with_generated(self):
        self.write('.agents/kitchen.toml', KITCHEN + '\n[[generated]]\nsources = ["src/**"]\noutputs = ["docs/**"]\ncommand = "make gen"\n')

    def test_a_scope_covering_a_source_but_none_of_its_outputs_warns(self):
        self.with_generated()
        r = self.data('classify', '--paths', 'src/**')
        self.assertEqual(r['warnings'], ['Scope covers src/app.py but none of its generated outputs (docs/**); '
                                         'add them to may write, or say why not (regenerate: make gen)'])
        self.assertIn('warn: Scope covers src/app.py', self.run_kitchen('classify', '--paths', 'src/**'))

    def test_a_scope_with_the_outputs_or_without_the_source_does_not_warn(self):
        self.with_generated()
        self.assertEqual(self.data('classify', '--paths', 'src/**', 'docs/**')['warnings'], [])
        self.assertEqual(self.data('classify', '--paths', 'tests/**')['warnings'], [])

    def test_a_warning_changes_neither_the_risk_nor_the_exit_code_and_no_entry_means_no_key(self):
        plain = self.data('classify', '--paths', 'src/**')
        self.assertNotIn('warnings', plain)
        self.with_generated()
        warned = self.data('classify', '--paths', 'src/**')
        self.assertEqual({k: v for k, v in warned.items() if k != 'warnings'}, plain)

    def test_routine_change_takes_its_profile_settings(self):
        r = self.classify({'src/util.py': 'X = 2\n', 'tests/test_app.py': 'def test_main():\n    assert 1\n'})
        self.assertEqual((r['risk'], r['profiles'], r['escalate']), ('routine', ['app'], []))
        self.assertEqual(r['verify'], {'mode': 'batch', 'kind': 'routine'})
        self.assertEqual(r['sample'], 0.2)
        self.assertNotIn('review', r)

    def test_docs_only_change_needs_no_verifier(self):
        r = self.classify({'docs/guide.md': '# Guide\n\nMore.\n'})
        self.assertEqual((r['risk'], r['verify']['mode'], r['sample']), ('routine', 'gates', 0.0))

    def test_each_escalation_reason(self):
        cases = [
            ({'contracts/api.json': '{"a": 1}\n'}, 'escalate path: contracts/api.json'),
            ({'requirements.txt': 'requests\nhttpx\n'}, 'dependency manifest: requirements.txt'),
            ({'src/util.py': 'X = 3\n', 'docs/guide.md': '# G\n'}, '2 profiles touched: app, docs (max 1)'),
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
        self.assertEqual([f['path'] for f in r['files']], ['src/app.py', 'src/util.py', 'src/new.py'])
        self.assertEqual(r['profiles'], ['app'])

    def test_scope_glob_for_a_new_directory_maps_to_its_profile(self):
        r = self.data('classify', '--paths', 'src/newpkg/**', 'contracts/v2/*.json')
        self.assertEqual([f['path'] for f in r['files']], ['src/newpkg/x', 'contracts/v2/x.json'])
        self.assertEqual((r['profiles'], r['risk']), (['app', 'contracts'], 'escalated'))
        self.assertEqual((r['behavioral'], r['max_batch_diff']), (['app'], 800))

    def test_scope_outside_the_repo_is_bookkeeping(self):
        r = self.data('classify', '--paths', '/elsewhere/store/reports/007-x.md', f'{self.repo}/src/util.py')
        self.assertEqual(([f['path'] for f in r['files']], r['outside'], r['risk']),
                         (['src/util.py'], ['/elsewhere/store/reports/007-x.md'], 'routine'))
        r = self.data('classify', '--paths', '/elsewhere/store/reports/007-x.md')
        self.assertEqual((r['risk'], r['bookkeeping'], r['escalate'], r['verify']['mode']), ('routine', True, [], 'gates'))

    def test_statedir_is_shared_by_worktrees_and_keeps_the_old_ledger(self):
        main = self.run_kitchen('statedir').strip()
        wt = Path(self.tmp.name) / 'wt'
        self.git('worktree', 'add', '-q', '--detach', str(wt), 'HEAD')
        self.assertEqual(self.run_kitchen('--repo', str(wt), 'statedir').strip(), main)
        # A ledger under the older per-checkout key moves to the shared one.
        import hashlib
        old = Path(self.tmp.name) / 'state/pstack/kitchen/repos' / f"wt-{hashlib.sha256(str(wt).encode()).hexdigest()[:8]}"
        old.mkdir(parents=True)
        (old / 'ledger.tsv').write_text('ts\trun\tkind\tclass\tdetail\n')
        Path(main, 'ledger.tsv').unlink(missing_ok=True)
        self.run_kitchen('--repo', str(wt), 'statedir')
        self.assertTrue(Path(main, 'ledger.tsv').exists())

    def test_each_bare_repository_gets_its_own_statedir_and_keeps_the_shared_history(self):
        import hashlib
        t = Path(self.tmp.name)
        parent = t / 'bare'
        state = t / 'state/pstack/kitchen/repos'
        shared = state / f"bare-{hashlib.sha256(str(parent).encode()).hexdigest()[:8]}"
        shared.mkdir(parents=True)
        (shared / 'timings.tsv').write_text('ts\tprofile\n1\tweb\n')
        dirs = []
        for name in ('a.git', 'b.git'):
            git_dir = parent / name
            subprocess.run(['git', 'clone', '-q', '--bare', str(self.repo), str(git_dir)], check=True)
            wt = parent / f'{name[0]}-main'
            subprocess.run(['git', '-C', str(git_dir), 'worktree', 'add', '-q', '--detach', str(wt)], check=True)
            dirs.append(self.run_kitchen('--repo', str(wt), 'statedir').strip())
            self.assertEqual(Path(dirs[-1]).name, f"{name}-{hashlib.sha256(str(git_dir).encode()).hexdigest()[:8]}")
            self.assertEqual(Path(dirs[-1], 'timings.tsv').read_text(), 'ts\tprofile\n1\tweb\n')
        self.assertNotEqual(dirs[0], dirs[1])

    def test_roster_and_statedir(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text('[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n'
                          '[sidekick.fallback]\nkind = "devin"\n[consultant]\nkind = "codex"\n')
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        r = self.data('roster')
        self.assertEqual(r['sidekick'], {'kind': 'pi', 'args': ['--model', 'devin/swe-2'],
                                         'fallback': {'kind': 'devin', 'args': []}})
        self.assertEqual(r['consultant']['kind'], 'codex')
        self.assertIn('pstack/kitchen/repos/repo-', self.run_kitchen('statedir'))

    def test_roster_verifier_defaults(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text('[sidekick]\nkind = "pi"\n')
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        v = self.data('roster')['verifier']
        self.assertEqual(v['routine'], {'kind': 'sidekick', 'args': [], 'fallback': None, 'source': 'sidekick'})
        self.assertEqual(v['escalated'], {'kind': 'claude', 'args': ['--model', 'claude-opus-5-5'],
                                          'fallback': None, 'source': 'default'})

    def test_roster_verifier_entries_and_master_sentinel(self):
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text('[verifier]\nkind = "pi"\nargs = ["--model", "m"]\n'
                          '[verifier.fallback]\nkind = "claude"\nargs = ["--model", "s"]\n'
                          '[verifier.escalated]\nkind = "master"\n')
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        v = self.data('roster')['verifier']
        self.assertEqual(v['routine'], {'kind': 'pi', 'args': ['--model', 'm'],
                                        'fallback': {'kind': 'claude', 'args': ['--model', 's']}, 'source': 'roster'})
        self.assertEqual(v['escalated'], {'kind': 'master', 'args': [], 'fallback': None, 'source': 'roster'})

    def test_repo_verify_kinds_warn_only_when_set(self):
        self.assertNotIn('moved to the host roster', self.run_kitchen('validate'))
        self.write('.agents/kitchen.toml', KITCHEN + '\n[verify]\nescalated_kind = "master"\n')
        self.assertIn('moved to the host roster', self.run_kitchen('validate'))
        self.assertTrue(self.data('validate')['ok'])

    def test_working_tree_counts_untracked_files(self):
        self.write('docs/new.md', 'a\nb\n')
        self.write('docs/guide.md', '# Guide\nmore\n')
        r = self.data('classify', '--working-tree')
        self.assertEqual(sorted(f['path'] for f in r['files']), ['docs/guide.md', 'docs/new.md'])
        self.assertEqual(r['diff_lines'], 3)

    def test_at_reads_the_kitchen_of_that_revision(self):
        self.change({'.agents/kitchen.toml': KITCHEN.replace('max_diff_lines = 50', 'max_diff_lines = 5')})
        head = self.change({'src/util.py': 'X = 1\nY = 2\nZ = 3\nW = 4\nV = 5\nU = 6\n',
                            'tests/test_app.py': 'def test_main():\n    assert 2\n'})
        now = self.data('classify', '--base', f'{head}~1', '--head', head)
        then = self.data('--at', self.base, 'classify', '--base', f'{head}~1', '--head', head)
        self.assertEqual((now['risk'], then['risk']), ('escalated', 'routine'))


class HistoryTests(KitchenTests):
    def test_history_classifies_each_commit_with_its_findings(self):
        self.change({'src/util.py': 'print(1)\n', 'tests/test_app.py': 'x = 2\n'})
        self.change({'contracts/api.json': '{"b": 2}\n'})
        rows = self.data('history', '--last', '5')['commits']
        self.assertEqual([(r['risk'], r['profiles'], r['findings']) for r in rows], [
            ('escalated', ['contracts'], []), ('routine', ['app'], ['no-print'])])
        self.assertIn('1 of 2 escalated; 1 with policy findings', self.run_kitchen('history'))


class GateTests(KitchenTests):
    def test_passing_gate_and_logs(self):
        r = self.data('gate', 'app', 'fast')
        self.assertTrue(r['passed'])
        self.assertTrue(Path(r['commands'][0]['log']).exists())
        self.assertEqual(self.data('gate', 'app', 'landing'), {'profile': 'app', 'stage': 'landing', 'role': 'sidekick',
                                                                'passed': True, 'commands': [], 'waited': 0.0})

    def test_failing_gate_stops_prints_the_log_tail_and_exits_2(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace(
            'fast = ["test -f src/app.py"]', 'fast = ["echo boom; exit 3", "echo second"]'))
        out = self.run_kitchen('gate', 'app', 'fast', code=2)
        self.assertIn('app fast: FAIL', out)
        self.assertIn('exit 3', out)
        self.assertIn('| boom', out)
        self.assertNotIn('echo second', out)
        self.assertEqual([c['exit'] for c in self.data('gate', 'app', 'fast', '--keep-going', code=2)['commands']], [3, 0])
        only = self.data('gate', 'app', 'fast', '--only', '1')['commands']
        self.assertEqual([(c['command'], c['log'].endswith('app-fast-1.log')) for c in only], [('echo second', True)])
        self.assertIn('--only 2: app.fast has 2 commands', self.run_kitchen('gate', 'app', 'fast', '--only', '2', code=1))

    def test_heavy_gate_releases_its_slot(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = true'))
        self.data('gate', 'app', 'fast')
        self.data('gate', 'app', 'fast')
        locks = list((Path(self.tmp.name) / 'state/pstack/kitchen/repos').glob('heavy-*.lock'))
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
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(kitchen, 'acquire_slot', spy), \
                mock.patch.object(kitchen, 'machine_gate_slots', lambda: 1):
            os.environ.pop(kitchen.WRAPPED, None)
            kitchen.run_gate(self.repo, k, 'app', 'fast', None)
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
        taken = self.slot_order(KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = true'))
        self.assertEqual(taken, [('heavy', []), ('gate', [])])

    def test_a_gate_waiting_for_the_heavy_slot_says_where_it_stands(self):
        import contextlib
        import io
        from unittest import mock
        self.write('.agents/kitchen.toml', KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = true'))
        k, _ = kitchen.load(self.repo, None, None)
        env = {**self.env}
        with mock.patch.dict(os.environ, env, clear=True):
            lock_dir = kitchen.state_dir(self.repo).parent
            holder, _ = kitchen.acquire_slot(lock_dir, 'heavy', 1)
            real = kitchen.acquire_slot
            def release_when_queued(directory, prefix, slots, on_wait=None):
                def wait(pos, n):
                    on_wait(pos, n)
                    holder.close()
                return real(directory, prefix, slots, wait)
            err = io.StringIO()
            with mock.patch.object(kitchen, 'acquire_slot', release_when_queued), contextlib.redirect_stderr(err):
                slot, _ = kitchen.heavy_slot(self.repo, k)
            slot.close()
        self.assertIn('kitchen: queued 1 of 1 for 1 heavy slot\n', err.getvalue())

    def test_a_fast_gate_of_a_behavioral_only_heavy_profile_takes_no_heavy_slot(self):
        toml = KITCHEN.replace('sample = 0.2', 'sample = 0.2\nheavy = ["behavioral"]')
        self.assertEqual(self.slot_order(toml), [('gate', [])])

    def test_a_gate_that_is_not_heavy_takes_only_a_machine_gate_slot(self):
        self.assertEqual(self.slot_order(KITCHEN), [('gate', [])])

    def timing_rows(self):
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                                        env=self.env, capture_output=True, text=True, check=True).stdout.strip())
        return [line.split('\t') for line in (state_dir / 'timings.tsv').read_text().splitlines()]

    def test_a_gate_row_has_nine_columns_with_the_heavy_wait_and_the_wall_time(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast = ["test -f src/app.py"]', 'fast = ["true", "true"]'))
        self.data('gate', 'app', 'fast')
        (row,) = self.timing_rows()
        self.assertEqual(len(row), 9)
        self.assertEqual(float(row[7]), 0)
        self.assertGreaterEqual(float(row[8]), float(row[5]))

    def test_the_readers_take_seven_and_nine_column_rows(self):
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                                        env=self.env, capture_output=True, text=True, check=True).stdout.strip())
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'timings.tsv').write_text('1\tapp\tbehavioral\tverifier\tpass\t100\t0\n'
                                               '2\tapp\tbehavioral\tverifier\tpass\t300\t5\t1200\t1500\n')
        self.assertEqual(self.data('timing', 'app', '--stage', 'behavioral')['seconds'], 300)

    def test_commands_see_the_role_they_run_for(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast = ["test -f src/app.py"]',
                                                           'fast = ["test \\"$PSTACK_KITCHEN_ROLE\\" = sidekick"]'))
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
        self.write('.agents/kitchen.toml', KITCHEN.replace(
            'fast = ["test -f src/app.py"]', f'fast = ["echo \\"$TMPDIR\\" > {seen} && touch \\"$TMPDIR/leftover\\""]'))
        self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
        used = Path(seen.read_text().strip())
        self.assertEqual(used.parent, base)
        self.assertTrue(used.name.startswith('kg-'))
        self.assertFalse(used.exists())
        self.assertFalse(dead.exists())
        self.assertTrue(live.exists())

    def test_a_failure_under_load_is_rerun_once_and_marked_flaky(self):
        mark = Path(self.tmp.name) / 'ran-once'
        self.write('.agents/kitchen.toml', KITCHEN.replace(
            'fast = ["test -f src/app.py"]', f'fast = ["test -e {mark} || {{ touch {mark}; exit 1; }}"]'))
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
        self.assertNotIn('retried', self.data('gate', 'app', 'fast', code=2)['commands'][0])

    def test_gate_tmpdirs_default_to_slash_tmp(self):
        os.environ.pop('PSTACK_KITCHEN_GATE_TMP', None)
        self.assertEqual(kitchen.gate_tmp_base(), Path('/tmp'))
        # mbx nests nix-shell.XXXXXX/mbx-session-XXXXXX/cache-agent.sock (52 bytes)
        # under it; a socket path must fit in 104 bytes on macOS.
        self.assertLess(len('/tmp/kg-4194304-abcdefgh/') + 52, 104)

    def test_gates_share_the_machine_slots(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast = ["test -f src/app.py"]', 'fast = ["sleep 2"]'))
        env = {**self.env, 'PSTACK_KITCHEN_GATE_SLOTS': '1'}
        procs = [subprocess.Popen([sys.executable, str(script), '--json', 'gate', 'app', 'fast'], cwd=self.repo,
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
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
            f, _ = kitchen.acquire_slot(lock_dir, 'gate', 1, lambda pos, n: seen.setdefault(name, (pos, n)))
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
        self.write('wrap/enter.sh', '#!/usr/bin/env bash\necho "shell hook says hi"\n'
                   'echo x >> "$(dirname "$0")/entered"\nPATH="$(dirname "$0")/bin:$PATH" exec "$@"\n')
        (self.repo / 'wrap/enter.sh').chmod(0o755)
        self.entered = self.repo / 'wrap/entered'

    def kitchen(self, extra='', fast='["inner-tool", "test \\"$KITCHEN_WRAPPED\\" = 1"]'):
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast = ["test -f src/app.py"]', f'fast = {fast}')
                   .replace('[coverage]', '[run]\nwrap = "wrap/enter.sh"\n\n[coverage]') + extra)

    def test_gate_enters_the_wrap_once_and_keeps_per_command_logs(self):
        self.kitchen()
        r = self.data('gate', 'app', 'fast')
        self.assertTrue(r['passed'], r)
        self.assertEqual([c['exit'] for c in r['commands']], [0, 0])
        self.assertEqual(self.entered.read_text(), 'x\n')

    def test_a_wrapped_gate_records_one_row_with_the_heavy_wait_and_the_wall_time(self):
        self.kitchen()
        self.data('gate', 'app', 'fast')
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                                        env=self.env, capture_output=True, text=True, check=True).stdout.strip())
        (row,) = [line.split('\t') for line in (state_dir / 'timings.tsv').read_text().splitlines()]
        self.assertEqual((len(row), float(row[7])), (9, 0))
        self.assertGreaterEqual(float(row[8]), float(row[5]))

    def test_a_failing_wrap_is_a_failed_gate_with_its_log(self):
        self.kitchen()
        self.write('wrap/enter.sh', '#!/usr/bin/env bash\necho "no devshell"; exit 7\n')
        r = self.data('gate', 'app', 'fast', code=2)
        self.assertEqual((r['commands'][0]['exit'], r['commands'][0]['tail']), (7, ['no devshell']))

    def test_profile_wrap_can_opt_out(self):
        self.kitchen(fast='["test -z \\"$KITCHEN_WRAPPED\\""]')
        self.write('.agents/kitchen.toml', (self.repo / '.agents/kitchen.toml').read_text()
                   .replace('require_tests = true', 'require_tests = true\nwrap = ""'))
        self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
        self.assertFalse(self.entered.exists())

    def scratch(self, setup, wrap=True):
        self.kitchen()
        text = (self.repo / '.agents/kitchen.toml').read_text()
        if not wrap:
            text = text.replace('[run]\nwrap = "wrap/enter.sh"\n\n', '')
        self.write('.agents/kitchen.toml', text + '\n[scratch]\nsetup = ' + json.dumps(setup) + '\n')

    def test_a_setup_run_is_its_own_timing_row_never_a_gate_sample(self):
        self.scratch(['true'])
        self.data('setup')
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                                        env=self.env, capture_output=True, text=True, check=True).stdout.strip())
        rows = [line.split('\t') for line in (state_dir / 'timings.tsv').read_text().splitlines()]
        self.assertEqual([(r[1], r[2], r[3], r[4]) for r in rows], [('-', 'setup', 'verifier', 'pass')])
        self.assertEqual(self.data('timing', 'app', '--stage', 'behavioral')['seconds'], 0)

    def test_two_run_slugs_reach_setup_the_wrap_and_gate_commands(self):
        self.kitchen(fast='["printf \'%s\' \\"$PSTACK_KITCHEN_RUN\\" > gate-run"]')
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + '\n[scratch]\nsetup = ["printf \'%s\' \\"$PSTACK_KITCHEN_RUN\\" > setup-run"]\n')
        for slug in ('alpha', 'beta'):
            with self.subTest(slug):
                self.env['PSTACK_KITCHEN_RUN'] = slug
                self.entered.unlink(missing_ok=True)
                self.assertTrue(self.data('setup')['passed'])
                self.assertTrue(self.data('gate', 'app', 'fast')['passed'])
                self.assertEqual(((self.repo / 'setup-run').read_text(), (self.repo / 'gate-run').read_text()), (slug, slug))
                self.assertTrue(self.entered.exists())

    def test_setup_with_nothing_declared_passes(self):
        self.kitchen()
        r = self.data('setup')
        self.assertEqual((r['passed'], r['commands'], r['log']), (True, [], None))
        self.assertFalse(self.entered.exists())

    def test_setup_runs_in_order_inside_the_wrap_entered_once_as_the_verifier(self):
        self.scratch(['echo one >> out', 'echo two >> out', 'printf "%s" "$PSTACK_KITCHEN_ROLE" > role',
                      'test "$KITCHEN_WRAPPED" = "" || true'])
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
        self.assertEqual((self.repo / 'where').read_text().strip(), str(self.repo.resolve()))

    def test_scratch_setup_is_validated(self):
        self.kitchen(extra='\n[scratch]\nsetup = "bun install"\n')
        self.assertIn('scratch.setup: expected a list of non-empty strings', self.run_kitchen('validate', code=1))
        self.kitchen(extra='\n[scratch]\ninstall = []\n')
        self.assertIn('scratch: unknown key install', self.run_kitchen('validate', code=1))

    def doctor_scratch(self):
        r = subprocess.run([sys.executable, str(script), '--json', 'doctor'], cwd=self.repo, env=self.env,
                           capture_output=True, text=True, check=False)
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
        self.assertEqual((c['status'], c['detail']), ('ok', 'bun install --frozen-lockfile'))
        self.kitchen()
        for f in ('package.json', 'bun.lock'):
            (self.repo / f).unlink()
        self.write('pyproject.toml', '[project]\n')
        self.write('uv.lock', '\n')
        self.assertEqual(self.doctor_scratch()['status'], 'warn')

    def test_doctor_resolves_commands_inside_the_wrap(self):
        self.kitchen()
        self.write('.agents/kitchen.toml', (self.repo / '.agents/kitchen.toml').read_text()
                   .replace('fast = ["true"]', 'fast = ["nosuchtool"]'))
        c = {x['check']: x for x in self.data('doctor', code=3)['checks']}
        self.assertEqual(c['commands']['items'], ['contracts.fast: nosuchtool (inside wrap)'])
        self.assertEqual(self.entered.read_text(), 'x\n')


class PolicyTests(KitchenTests):
    def test_forbid_rule_on_added_lines_with_allow(self):
        head = self.change({
            'src/app.py': 'def main():\n    print("x")\n    print("y")  # kitchen-allow: no-print, CLI output\n',
            'docs/guide.md': '# Guide\nprint(1)\n',
            'tests/test_app.py': 'def test_main():\n    assert 2\n',
        })
        r = self.data('policy', '--base', self.base, '--head', head, code=2)
        self.assertEqual([(f['rule'], f['path'], f['line']) for f in r['findings']], [('no-print', 'src/app.py', 2)])
        self.assertEqual(r['findings'][0]['source'], 'AGENTS.md#logging')

    def test_kitchen_allow_takes_a_comma_list_of_ids_and_the_rest_of_the_line_is_the_reason(self):
        self.write('.agents/kitchen.toml', (self.repo / '.agents/kitchen.toml').read_text() +
                   '\n[[policy.forbid]]\nid = "other-rule"\npattern = \'\\bprint\\(\'\npaths = ["src/**"]\nmessage = "also no"\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'second rule')
        base = self.git('rev-parse', 'HEAD')
        lines = {
            'one id then a reason in lowercase words': '    print("a")  # kitchen-allow: no-print bounded reason\n',
            'a comma list then a reason': '    print("b")  # kitchen-allow: no-print, other-rule plain reason\n',
            'a second id after a space is reason text': '    print("c")  # kitchen-allow: other-rule no-print\n',
            'no allow': '    print("d")\n',
        }
        head = self.change({'src/app.py': 'def main():\n' + ''.join(lines.values()), 'tests/test_app.py': 'def test_main():\n    assert 5\n'})
        r = self.data('policy', '--base', base, '--head', head, code=2)
        found = sorted((f['line'], f['rule']) for f in r['findings'])
        # line 2: no-print allowed (the words after it are the reason), so only other-rule fires; line 3: both allowed; line 4: other-rule allowed,
        # `no-print` after it is reason text and still fires; line 5: both fire
        self.assertEqual(found, [(2, 'other-rule'), (4, 'no-print'), (5, 'no-print'), (5, 'other-rule')])

    def test_test_touch(self):
        head = self.change({'src/util.py': 'X = 5\n'})
        r = self.data('policy', '--base', self.base, '--head', head, code=2)
        self.assertEqual([(f['rule'], f['profile']) for f in r['findings']], [('test-touch', 'app')])
        self.assertIn('src/util.py: [test-touch] profile app', self.run_kitchen('policy', '--base', self.base, '--head', head, code=2))
        self.assertEqual(self.data('history', '--last', '1')['commits'][0]['findings'], ['test-touch(app)'])
        head2 = self.change({'tests/test_app.py': 'def test_main():\n    assert 3\n'})
        self.assertEqual(self.data('policy', '--base', self.base, '--head', head2), {'findings': []})

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
        return {c['check']: c for c in self.data('doctor', code=None)['checks']}['kitchen']

    def run_kitchen(self, *args, code=0):
        if code is not None:
            return super().run_kitchen(*args, code=code)
        result = subprocess.run([sys.executable, str(script), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, check=False)
        self.stdout = result.stdout
        return result.stdout + result.stderr

    def test_origin_key_reads_every_remote_spelling(self):
        for url, key in [('git@github.com:Acme/Widget.git', 'github.com/Acme/Widget'),
                         ('https://GitHub.com/acme/widget/', 'github.com/acme/widget'),
                         ('ssh://git@host:2222/a/b.git', 'host/a/b'),
                         ('https://user:tok@gitlab.example/g/sub/r.git', 'gitlab.example/g/sub/r'),
                         ('/srv/git/r.git', None), ('', None)]:
            self.assertEqual(kitchen.origin_key(url), key, url)

    def test_the_host_kitchen_is_read_at_any_revision(self):
        for args in [(), ('--at', 'HEAD'), ('--at', self.base)]:
            r = self.data(*args, 'validate')
            self.assertEqual((r['source'], r['path']), ('user', str(self.host)), args)
        self.assertEqual(self.data('where')['host'], {'source': 'user', 'path': str(self.host), 'exists': True})

    def test_a_worktree_finds_the_same_host_kitchen(self):
        wt = Path(self.tmp.name) / 'wt'
        self.git('worktree', 'add', '-q', str(wt), 'HEAD')
        self.assertEqual(self.data('--repo', str(wt), '--at', 'HEAD', 'validate')['path'], str(self.host))

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
            f'[[kitchen]]\nmatch = "{self.tmp.name}/**"\nfile = "widget.toml"\n')
        r = self.data('--at', 'HEAD', 'validate')
        self.assertEqual((r['source'], r['path']), ('roster', str(other)))

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
        self.host.write_text(KITCHEN.replace('max_diff_lines = 50', 'max_diff_lines = 60'))
        second = self.run_kitchen('snapshot', str(snaps)).strip()
        self.assertNotEqual(first, second)
        self.assertEqual(sorted(p.name for p in snaps.iterdir()), sorted([Path(first).name, Path(second).name]))
        r = self.data('--config', first, '--at', 'HEAD', 'validate')
        self.assertEqual((r['source'], r['path']), ('file', first))

    def test_host_kitchen_lessons_sit_beside_it(self):
        self.assertEqual(self.data('lessons')['path'], str(self.host.with_suffix('.lessons.md')))

    def test_a_repo_kitchen_takes_no_snapshot(self):
        self.write('.agents/kitchen.toml', KITCHEN)
        self.commit('kitchen')
        self.assertEqual(self.run_kitchen('snapshot', str(Path(self.tmp.name) / 'snaps')), '')


class LessonTests(KitchenTests):
    def setUp(self):
        super().setUp()
        self.ledger = Path(self.run_kitchen('statedir').strip()) / 'ledger.tsv'
        self.ledger.write_text('ts\trun\tkind\tclass\tdetail\tunit\tarea\tfinding\n')

    def row(self, run, kind, cls, unit, area, finding, detail='-', match='', ts=None):
        ts = ts or f'2026-10-0{len(self.ledger.read_text().splitlines())}T00:00:00Z'
        with self.ledger.open('a') as f:
            f.write(f'{ts}\t{run}\t{kind}\t{cls}\t{detail}\t{unit}\t{area}\t{finding}\t{match}\n')

    def candidates(self):
        return [(c['kind'], c['class'], c['area'], c['runs']) for c in self.data('lessons')['candidates']]

    def key(self, lid):
        x = next(x for x in kitchen.read_lessons(self.repo / '.agents/LESSONS.md') if x.id == lid)
        return kitchen.lesson_key(lid, x.fields.get('active', ''))

    def test_trouble_in_one_area_across_runs_is_a_candidate(self):
        self.row('run-a', 'reject', 'verify', '001', 'src/lib', 'the cache is never cleared')
        self.row('run-b', 'reject', 'verify', '003', 'docs,src/lib', 'cache stale after write')
        self.row('run-b', 'reject', 'verify', '004', 'src/api', 'other area, one run')
        self.row('run-a', 'gate-fail', 'app', '002', 'src/lib', 'one run only')
        self.row('run-c', 'catch', 'review', '', '', 'no unit, no area')
        self.row('run-c', 'catch', 'review', '005', 'src/api', 'missed a caller')
        # The most runs first.
        self.assertEqual(self.candidates(), [('reject', 'verify', 'src/lib', ['run-a', 'run-b']),
                                             ('catch', 'review', 'src/api', ['run-c'])])
        r = self.data('lessons')['candidates'][0]
        self.assertEqual(r['evidence'], ['2026-10-01 run-a 001: the cache is never cleared',
                                         '2026-10-02 run-b 003: cache stale after write'])

    def test_a_gate_failure_is_a_candidate_by_its_check_after_three_runs(self):
        for run in ('run-a', 'run-b'):
            self.row(run, 'gate-fail', 'web', '001', 'apps/web', 'x.ts:1:1: error eslint(no-unused-vars)', match='fast lint')
            self.row(run, 'gate-fail', 'web', '002', 'apps/web', 'x.ts(1,1): error TS2322', match='fast typecheck')
        self.row('run-c', 'gate-fail', 'web', '001', 'apps/web', 'y.ts:2:1: error eslint(no-unused-vars)', match='fast lint')
        # Without its step's output a failure names no check, and teaches nothing.
        for run in ('run-d', 'run-e', 'run-f'):
            self.row(run, 'gate-fail', 'web', '001', 'apps/web', '', detail='001-x abc1234')
        self.assertEqual([(c['class'], c['match'], c['area'], c['runs']) for c in self.data('lessons')['candidates']],
                         [('web', 'fast lint', 'apps/web', ['run-a', 'run-b', 'run-c'])])
        self.assertIn("3 runs (run-a, run-b, run-c): gate-fail web apps/web --match 'fast lint'", self.run_kitchen('lessons'))
        self.run_kitchen('lessons', 'add', 'gate-fail', 'web', 'apps/web', 'Lint the touched package before each step.',
                         '--match', 'fast lint')
        x = self.data('lessons')['lessons'][0]
        self.assertEqual((x['match'], x['layer']), ('fast lint', 'gate'))
        self.assertIn('2026-10-01 run-a 001: x.ts:1:1: error eslint(no-unused-vars)',
                      (self.repo / '.agents/LESSONS.md').read_text())
        self.assertNotIn('TS2322', (self.repo / '.agents/LESSONS.md').read_text())
        self.assertEqual(self.data('lessons')['candidates'], [])

    def test_a_policy_failure_is_a_policy_lesson(self):
        for run in ('run-a', 'run-b', 'run-c'):
            self.row(run, 'gate-fail', 'policy', '001', 'tooling', 'a.ts: [test-touch] no tests', match='test-touch')
        self.run_kitchen('lessons', 'add', 'gate-fail', 'policy', 'tooling', 'Change a test with the source.',
                         '--match', 'test-touch')
        self.assertIn("target: a [[policy.forbid]] rule", self.run_kitchen('lessons', 'promote', 'L001'))

    def test_only_a_steer_that_turned_the_sidekick_is_trouble(self):
        for run in ('run-a', 'run-b'):
            self.row(run, 'steer', 'brief', '001', 'src', 'scope: add the fixture', match='scope')
            self.row(run, 'steer', 'brief', '002', 'src', 'info: news from the owner', match='info')
        self.assertEqual(self.candidates(), [])
        self.row('run-a', 'steer', 'brief', '003', 'src', 'redirect: read the cache first', match='redirect')
        # A row from before the match column: the steer's kind leads its finding.
        self.row('run-b', 'steer', 'brief', '003', 'src', 'redirect: not the CLI')
        self.assertEqual([(c['match'], c['runs']) for c in self.data('lessons')['candidates']],
                         [('redirect', ['run-a', 'run-b'])])

    def test_a_dismissed_candidate_stays_hidden_until_newer_trouble(self):
        self.row('run-a', 'reject', 'verify', '001', 'src', 'one thing')
        self.row('run-b', 'reject', 'verify', '002', 'src', 'another thing')
        self.run_kitchen('lessons', 'dismiss', 'reject', 'verify', 'src', '--note', 'two causes')
        self.assertEqual(self.candidates(), [])
        self.assertIn('none', self.run_kitchen('lessons'))
        self.row('run-c', 'reject', 'verify', '003', 'src', 'a third', ts='2099-01-01T00:00:00Z')
        self.assertEqual(self.candidates(), [('reject', 'verify', 'src', ['run-a', 'run-b', 'run-c'])])
        self.assertIn('no ledger row', self.run_kitchen('lessons', 'dismiss', 'reject', 'verify', 'docs', '--note', 'x', code=1))

    def test_ids_come_from_the_ledger_so_parallel_worktrees_do_not_collide(self):
        self.row('run-a', 'catch', 'review', '001', 'src', 'missed a caller')
        self.assertIn('next: commit it on its own', self.run_kitchen('lessons', 'add', 'catch', 'review', 'src', 'Rule one.'))
        # Another run's worktree has its own copy of the file, without L001.
        (self.repo / '.agents/LESSONS.md').unlink()
        self.assertEqual(self.data('lessons', 'add', 'catch', 'review', 'src', 'Rule two.')['id'], 'L002')
        out = self.run_kitchen('lessons', 'set', 'L002', 'active')
        self.assertIn('add -- .agents/LESSONS.md && ', out)

    def test_retro_prints_the_strongest_candidates(self):
        for n in range(7):
            for run in ('run-a', 'run-b'):
                self.row(run, 'reject', 'verify', f'00{n}', f'src/m{n}', 'x')
        out = self.run_kitchen('lessons')
        self.assertEqual(out.count(': reject verify src/m'), 5)
        self.assertIn('2 more with fewer runs: kitchen.py lessons --all', out)
        self.assertEqual(self.run_kitchen('lessons', '--all').count(': reject verify src/m'), 7)

    def test_the_area_is_the_narrowest_that_keeps_every_run(self):
        self.row('run-a', 'steer', 'brief', '001', 'src/lib', 'narrow: x')
        self.row('run-b', 'steer', 'brief', '002', 'src/api', 'narrow: y')
        self.row('run-c', 'steer', 'brief', '003', 'src', 'narrow: z')
        self.assertEqual(self.candidates(), [('steer', 'brief', 'src', ['run-a', 'run-b', 'run-c'])])

    def test_a_staged_lesson_lives_in_the_repo_and_hides_its_candidate(self):
        self.row('run-a', 'reject', 'verify', '001', 'src/lib', 'the cache is never cleared')
        self.row('run-b', 'reject', 'verify', '003', 'src/lib', 'cache stale after write')
        self.assertIn('L001 proposed', self.run_kitchen('lessons', 'add', 'reject', 'verify', 'src/lib',
                                                        'Every write to the store clears the cache.'))
        text = (self.repo / '.agents/LESSONS.md').read_text()
        self.assertIn('## L001: Every write to the store clears the cache.\n\nstatus: proposed\nkind: reject\n'
                      'class: verify\nlayer: verify\narea: src/lib\n', text)
        self.assertIn('evidence:\n- 2026-10-01 run-a 001: the cache is never cleared\n', text)
        self.assertEqual(self.candidates(), [])
        self.assertIn('needs evidence', self.run_kitchen('lessons', 'add', 'reject', 'verify', 'docs', 'x', code=1))
        self.assertEqual(self.data('lessons', 'add', 'reject', 'verify', 'src', 'Second rule.')['id'], 'L002')
        self.assertEqual([x['id'] for x in self.data('lessons')['lessons']], ['L001', 'L002'])

    def test_a_narrow_lesson_leaves_trouble_outside_it_to_propose(self):
        for run, area in [('run-a', 'src/lib'), ('run-b', 'src/lib'), ('run-c', 'src/api'), ('run-d', 'src/api')]:
            self.row(run, 'reject', 'verify', '001', area, 'x')
        self.run_kitchen('lessons', 'add', 'reject', 'verify', './src/lib/', 'Rule.')
        self.assertEqual(self.data('lessons')['lessons'][0]['area'], 'src/lib')
        self.assertEqual(self.candidates(), [('reject', 'verify', 'src/api', ['run-c', 'run-d'])])
        self.assertIn('one repo directory', self.run_kitchen('lessons', 'add', 'reject', 'verify', 'src,docs', 'R.', code=1))

    def doctor(self):
        return {c['check']: c for c in self.data('doctor')['checks']}['lessons']

    def test_a_promotion_draft_names_the_layer_and_why_and_leaves_the_proof_to_the_master(self):
        self.row('run-a', 'catch', 'review', '001', 'src/lib', 'missed a caller', detail='missed a caller')
        self.run_kitchen('lessons', 'add', 'catch', 'review', 'src/lib', 'A renamed function has every caller updated.')
        self.assertEqual(self.doctor()['status'], 'ok')
        self.assertIn('1 proposed', self.doctor()['detail'])
        draft = self.run_kitchen('lessons', 'promote', 'L001')
        self.assertIn('promotable: no (status is proposed, not active; held 0 of 3)', draft)
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        for n in range(3):
            self.row(f'run-{n}', 'lesson', 'held', f'00{n}', 'src/lib', '', detail=self.key('L001'))
        d = self.doctor()
        self.assertEqual((d['status'], d['items']), ('warn', ['L001: promotable (held 3); kitchen.py lessons promote L001']))
        out = Path(self.tmp.name) / 'store/promotions/L001.md'
        self.run_kitchen('lessons', 'promote', 'L001', '--out', str(out))
        draft = out.read_text()
        self.assertTrue(draft.startswith('# Promote L001: A renamed function has every caller updated.\n'))
        self.assertIn('promotable: yes\n', draft)
        self.assertIn('target: a lint, test or type through the correct skill; else a CODE_STANDARD.md section', draft)
        self.assertIn('why this layer: review caught it by reading', draft)
        self.assertIn('- 2026-10-01 run-a 001: missed a caller\n', draft)
        self.assertIn('held or hit in: run-0 000 held, run-1 001 held, run-2 002 held', draft)
        for section in ('## Example', '## Proof', '## False positives', '## Decision'):
            self.assertIn(section, draft)
        self.assertIn('no lesson L007', self.run_kitchen('lessons', 'promote', 'L007', code=1))

    def test_doctor_flags_a_lesson_that_keeps_being_hit_or_cannot_be_read(self):
        self.row('run-a', 'gate-fail', 'app', '001', 'src', 'lint', detail='001-x abc1234')
        self.row('run-b', 'gate-fail', 'app', '002', 'src', 'lint', detail='002-y def5678')
        self.run_kitchen('lessons', 'add', 'gate-fail', 'app', 'src', 'Run the formatter before each step.')
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertIn('target: a lint rule, or a fast-gate command in profile `app`',
                      self.run_kitchen('lessons', 'promote', 'L001'))
        self.assertIn('ledger refs (run, then the unit, verdict or steer to open): run-a 001-x abc1234, run-b 002-y def5678',
                      self.run_kitchen('lessons', 'promote', 'L001'))
        for n in range(2):
            self.row(f'run-{n}', 'lesson', 'hit', f'00{n}', 'src', 'lint again', detail=self.key('L001'))
        # Activating again with the same rule keeps the count.
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertEqual(self.data('lessons')['lessons'][0]['hits'], 2)
        path = self.repo / '.agents/LESSONS.md'
        path.write_text(path.read_text() + '\n## L002: Broken.\n\nstatus: active\n')
        self.assertEqual(self.doctor()['items'], ['L001: hit 2 times while active; rewrite the rule or retire it',
                                                  'L002: unreadable (kind, class, layer, area); fix it by hand'])
        # A rewritten rule starts its count again.
        time.sleep(1.1)  # the new version's stamp is to the second
        path.write_text(path.read_text().replace('\n## L002: Broken.\n\nstatus: active\n', ''))
        self.run_kitchen('lessons', 'set', 'L001', 'active', '--rule', 'Run the formatter in the pre-commit hook.')
        x = self.data('lessons')['lessons'][0]
        self.assertEqual((x['rule'], x['held'], x['hits']), ('Run the formatter in the pre-commit hook.', 0, 0))
        self.assertEqual(self.doctor()['status'], 'ok')

    def test_rewriting_the_file_keeps_what_a_human_wrote(self):
        self.row('run-a', 'catch', 'review', '001', 'src/lib', 'missed a caller')
        self.run_kitchen('lessons', 'add', 'catch', 'review', 'src/lib', 'Rule one.')
        path = self.repo / '.agents/LESSONS.md'
        path.write_text(path.read_text().replace('# Kitchen lessons\n', '# Our lessons\n\nTeam note.\n', 1)
                        + '\nWhy: the API module has three entry points.\nreviewer: ana\n')
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        text = path.read_text()
        self.assertTrue(text.startswith('# Our lessons\n\nTeam note.\n\nRules learned'))
        self.assertIn('is counted on the host, not here.\n\n## L001: Rule one.\n', text)
        self.assertIn('evidence:\n- 2026-10-01 run-a 001: missed a caller\n\nWhy: the API module has three entry points.\n'
                      'reviewer: ana\n', text)
        self.assertIn('status: active\n', text)

    def test_three_clean_units_and_no_hit_make_a_lesson_promotable(self):
        self.row('run-a', 'catch', 'review', '001', 'src/lib', 'missed a caller')
        self.run_kitchen('lessons', 'add', 'catch', 'review', 'src/lib', 'A renamed function has every caller updated.')
        self.run_kitchen('lessons', 'set', 'L001', 'active')
        self.assertRegex((self.repo / '.agents/LESSONS.md').read_text(), r'status: active\n(.*\n)*active: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n')
        for n in range(3):
            self.row(f'run-{n}', 'lesson', 'held', '00{n}', 'src/lib', '', detail=self.key('L001'))
        x = self.data('lessons')['lessons'][0]
        self.assertEqual((x['layer'], x['held'], x['hits'], x['promotable']), ('review', 3, 0, True))
        self.assertIn('(promotable)', self.run_kitchen('lessons'))
        self.row('run-9', 'lesson', 'hit', '009', 'src/lib', 'missed again', detail=self.key('L001'))
        self.assertFalse(self.data('lessons')['lessons'][0]['promotable'])
        self.assertIn('needs --note', self.run_kitchen('lessons', 'set', 'L001', 'promoted', code=1))
        self.run_kitchen('lessons', 'set', 'L001', 'promoted', '--note', 'abc1234 lint rule no-stale-callers')
        x = self.data('lessons')['lessons'][0]
        self.assertEqual((x['status'], x['note']), ('promoted', 'abc1234 lint rule no-stale-callers'))
        self.assertIn('no lesson L009', self.run_kitchen('lessons', 'set', 'L009', 'active', code=1))


class LessonScoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name)
        for d in ('briefs', 'verdicts'):
            (self.store / d).mkdir()
        self.ledger = self.store / 'ledger.tsv'

    def brief(self, name, lessons):
        (self.store / 'briefs' / name).write_text('# Brief\n\n## Scope\n\nmay write:\n- src/**\n\n## Lessons\n\nx\n\n'
                                                  + ''.join(f'- {l} (reject verify, layer verify, in src): rule\n' for l in lessons))

    def verdict(self, name, status, units, lessons=''):
        (self.store / 'verdicts' / name).write_text(f'# V\n\nstatus: {status}\nunits: {units}\n\n## Findings\n\n'
                                                    f'{"1. broke" if status == "reject" else "none"}\n\n## Lessons\n\n{lessons}')

    def scores(self):
        kitchen.ledger_retro(self.store, self.ledger, 'r', '2026-10-13T00:00:00Z')
        return sorted((r['detail'], r['unit'], r['class'], r['finding'])
                      for r in kitchen.ledger_rows(self.ledger) if r['kind'] == 'lesson')

    def test_held_hit_or_unscored(self):
        self.brief('001-a.md', ['L001', 'L002'])
        self.brief('001-a-a1.md', ['L001'])
        self.brief('002-b.md', ['L001'])
        self.brief('003-c.md', ['L001'])
        self.verdict('001-a-v1.md', 'clean', '001', 'L001: held\n- `L002`: hit the CLI path again\n')
        self.verdict('002-b-v1.md', 'reject', '002')
        self.verdict('002-b-v2.md', 'clean', '002')
        self.assertEqual(self.scores(), [('L001', '001', 'held', ''), ('L001', '002', 'hit', 'broke'),
                                         ('L002', '001', 'hit', 'the CLI path again')])
        # Retro again replaces the rows.
        self.assertEqual(len(self.scores()), 3)

    def test_a_brief_names_the_lesson_version_it_was_given(self):
        lessons = self.store / 'LESSONS.md'
        kitchen.write_lessons(lessons, [kitchen.Lesson('L001', 'Rule.', {
            'status': 'active', 'kind': 'reject', 'class': 'verify', 'layer': 'verify', 'area': 'src',
            'active': '2026-10-13T10:00:00Z'}, ['e'])])
        b = self.store / 'briefs/001-a.md'
        b.write_text('# Brief\n\n## Scope\n\nmay write:\n- src/**\n')
        kitchen.stamp_lessons(b, lessons)
        self.assertIn('- L001 (reject verify, layer verify, in src, active 2026-10-13T10:00:00Z): Rule.\n', b.read_text())
        self.verdict('001-a-v1.md', 'clean', '001')
        self.assertEqual(self.scores(), [('L001@2026-10-13T10:00:00Z', '001', 'held', '')])
        # A row from before versions counts from the lesson's activation.
        with self.ledger.open('a') as f:
            f.write('2026-10-12T00:00:00Z\told\tlesson\thit\tL001\t005\tsrc\tx\n'
                    '2026-10-14T00:00:00Z\told\tlesson\theld\tL001\t006\tsrc\t\n')
        x = kitchen.read_lessons(lessons)[0]
        self.assertEqual(kitchen.lesson_counts(kitchen.ledger_rows(self.ledger), x), {'held': 2, 'hits': 0})

    def test_gates_speak_only_for_gate_lessons_and_a_hit_stands(self):
        (self.store / 'briefs/001-a.md').write_text('## Lessons\n\n- L001 (reject verify, layer verify, in src): r\n'
                                                    '- L002 (gate-fail app, layer gate, in src): r\n')
        (self.store / 'verdicts/001-a-v1.md').write_text('# V\n\nstatus: clean\nunits: 001\nverifier: gates\n')
        self.assertEqual(self.scores(), [('L002', '001', 'held', '')])
        self.brief('002-b.md', ['L001'])
        self.verdict('002-b-v2.md', 'reject', '002', 'L001: hit again\n')
        self.verdict('002-b-v10.md', 'clean', '002', 'L001: held\n')
        self.brief('003-c.md', ['L001'])
        self.verdict('003-c-v1.md', 'reject', '003', 'L001: held\n')
        self.assertEqual(self.scores(), [('L001', '002', 'hit', 'again'), ('L001', '003', 'held', ''),
                                         ('L002', '001', 'held', '')])

    def gate_out(self, unit, sha, check, error):
        d = self.store / 'steps/jobs' / f'{unit}-{sha}0000'
        d.mkdir(parents=True)
        (d / 'out').write_text(f'web fast: FAIL\n  exit 1 2.0s bun run turbo run {check}  log: /x/web-fast-0.log\n'
                               f'    | @x/web:{check}: {error}\n    | @x/web:{check}: error: script "{check}" exited with code 1\n'
                               f'    |  ERROR  run failed: command  exited (1)\n    | Failed:    @x/web#{check}\n'
                               'policy: clean\n')

    def test_gates_alone_score_a_gate_lesson_by_its_check(self):
        line = '## Lessons\n\n- L001 (gate-fail web "fast lint", layer gate, in src, active 2026-10-13T10:00:00Z): r\n'
        for b in ('001-a', '002-b', '003-c'):
            (self.store / 'briefs' / f'{b}.md').write_text('# Brief\n\n## Scope\n\nmay write:\n- src/**\n\n' + line)
        (self.store / 'gates.tsv').write_text('1\t001-a\tabc\tfail\tweb\n1\t001-a\tabd\tpass\tweb\n'
                                              '1\t002-b\tdef\tfail\tweb\n1\t002-b\tdeg\tpass\tweb\n')
        self.gate_out('001-a', 'abc', 'lint', 'a.ts:1:7: error eslint(no-unused-vars): x is never used')
        self.gate_out('002-b', 'def', 'typecheck', 'a.ts(1,7): error TS2322: no')
        # A verifier cannot see the steps: its word does not score a gate lesson.
        self.verdict('001-a-v1.md', 'clean', '001', 'L001: held\n')
        self.verdict('002-b-v1.md', 'clean', '002')
        (self.store / 'verdicts/003-c-v1.md').write_text('# V\n\nstatus: clean\nunits: 003\nverifier: gates\n')
        self.assertEqual(self.scores(), [
            ('L001@2026-10-13T10:00:00Z', '001', 'hit', '@x/web:lint: a.ts:1:7: error eslint(no-unused-vars): x is never used'),
            ('L001@2026-10-13T10:00:00Z', '002', 'held', ''), ('L001@2026-10-13T10:00:00Z', '003', 'held', '')])
        gates = sorted((r['class'], r['match'], r['finding']) for r in kitchen.ledger_rows(self.ledger) if r['kind'] == 'gate-fail')
        self.assertEqual(gates, [('web', 'fast lint', '@x/web:lint: a.ts:1:7: error eslint(no-unused-vars): x is never used'),
                                 ('web', 'fast typecheck', '@x/web:typecheck: a.ts(1,7): error TS2322: no')])
        self.assertEqual(kitchen.lesson_packet([self.store / 'briefs/001-a.md']), '')

    def test_retro_keeps_rows_of_kinds_it_does_not_write(self):
        self.ledger.write_text('ts\trun\tkind\tclass\tdetail\n2026-10-01T00:00:00Z\tr\tfinding\ttests\tx\n'
                               '2026-10-01T00:00:00Z\tr\tsteer\tbrief\t001-a-s1\n')
        self.scores()
        self.assertEqual([r['kind'] for r in kitchen.ledger_rows(self.ledger)], ['finding'])

    def test_stamp_replaces_the_section_and_drops_it_when_nothing_applies(self):
        lessons = self.store / 'LESSONS.md'
        kitchen.write_lessons(lessons, [
            kitchen.Lesson('L001', 'Rule one.', {'status': 'active', 'kind': 'reject', 'class': 'verify',
                                                 'layer': 'verify', 'area': 'src/lib'}, ['e']),
            kitchen.Lesson('L002', 'Rule two.', {'status': 'proposed', 'kind': 'reject', 'class': 'verify',
                                                 'layer': 'verify', 'area': 'src'}, ['e'])])
        b = self.store / 'briefs/004-d.md'
        b.write_text('# Brief\n\n## Scope\n\nmay write:\n- src/**\n- tests/**\n\n## Acceptance\n\n- works\n')
        self.assertEqual([x.id for x in kitchen.stamp_lessons(b, lessons)], ['L001'])
        kitchen.stamp_lessons(b, lessons)
        text = b.read_text()
        self.assertEqual(text.count('## Lessons'), 1)
        self.assertTrue(text.startswith('# Brief\n\n## Scope\n\nmay write:\n- src/**\n- tests/**\n\n## Acceptance\n\n- works\n\n## Lessons\n'))
        self.assertEqual(kitchen.brief_lessons(b)[0]['rule'], 'Rule one.')
        kitchen.lessons_set(lessons, 'L001', 'retired', None, 'wrong area', '2026-10-13')
        kitchen.stamp_lessons(b, lessons)
        self.assertEqual(b.read_text(), '# Brief\n\n## Scope\n\nmay write:\n- src/**\n- tests/**\n\n## Acceptance\n\n- works\n')
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
        (s / 'briefs/001-x.md').write_text('# Brief\n\n## Scope\n\nmay write:\n- src/lib/**\n- docs/adr/**\n')
        (s / 'steers/001-x-s1.md').write_text('# Steer\n\nkind: narrow\n\n## Direction\n\nOnly the library.\n')

    def test_old_rows_gain_where_and_what_and_keep_their_time(self):
        # The worktree is gone; its path still says which repository it was.
        self.store('run-a', self.tmp / 'proj.git/wt-a')
        self.store('run-b', self.tmp / 'other.git/wt-b')
        self.ledger.write_text('ts\trun\tkind\tclass\tdetail\n'
                               '2026-01-01T00:00:00Z\trun-a\tfinding\ttests\tx\n'
                               '2026-01-02T00:00:00Z\trun-a\tsteer\tbrief\t001-x-s1\n'
                               '2026-01-02T00:00:00Z\trun-a\tcatch\treview\tmissed it\n'
                               '2026-01-03T00:00:00Z\trun-b\tsteer\tbrief\t001-x-s1\n'
                               '2026-01-04T00:00:00Z\trun-z\tsteer\tbrief\t001-y-s1\n')
        self.assertEqual(kitchen.ledger_backfill(self.ledger, self.runs), {'runs': 1, 'rows': 1, 'missing': 1, 'foreign': 1})
        rows = [(r['ts'][:10], r['run'], r['kind'], r['area'], r['match']) for r in kitchen.ledger_rows(self.ledger)]
        self.assertEqual(rows, [('2026-01-01', 'run-a', 'finding', '', ''), ('2026-01-02', 'run-a', 'steer', 'src/lib', 'narrow'),
                                ('2026-01-02', 'run-a', 'catch', '', ''), ('2026-01-03', 'run-b', 'steer', '', ''),
                                ('2026-01-04', 'run-z', 'steer', '', '')])
        self.assertEqual(kitchen.ledger_backfill(self.ledger, self.runs)['runs'], 0)


class AreaTests(unittest.TestCase):
    def test_scope_entries_read_as_pair_core_reads_them(self):
        with tempfile.TemporaryDirectory() as d:
            b = Path(d) / '001-x.md'
            b.write_text('## Scope\n\nmay write:\n- src/a/** — the module\n- src/b/** -- note\n- src/c/** – note\n'
                         '- src/d.py (new)\n- `src/e f, g.py` — quoted\n\nmust not write:\n- src/z/**\n')
            self.assertEqual(kitchen.brief_scope(b), ['src/a/**', 'src/b/**', 'src/c/**', 'src/d.py', 'src/e f, g.py'])

    def test_a_scope_is_the_directories_it_writes(self):
        for globs, area in [(['src/**', 'tests/**'], ['src']), (['src/api/v2/**', 'src/api/x.py'], ['src/api']),
                            (['README.md', 'docs/*.md'], ['.']), (['tests/unit/**'], ['tests/unit']),
                            (['docs/adr/0001-x.md', 'services/api/**', 'tests/**'], ['services/api']),
                            (['docs/adr/**', 'tests/**'], ['docs/adr']),
                            (['lib/foo*', 'pkg/'], ['lib', 'pkg']), ([], []),
                            (['/tmp/store/reports/001-x.md', '~/notes.md', 'src/a/**'], ['src/a']),
                            (['/tmp/store/reports/001-x.md'], [])]:
            self.assertEqual(kitchen.scope_area(globs), area, globs)


class DoctorTests(KitchenTests):
    def checks(self, *args, code=0):
        return {c['check']: c for c in self.data('doctor', *args, code=code)['checks']}

    def test_clean_kitchen(self):
        c = self.checks()
        self.assertEqual({k: v['status'] for k, v in c.items()}, {
            'kitchen': 'ok', 'lessons': 'ok',
            'coverage': 'ok', 'profiles': 'ok', 'commands': 'ok', 'fast gates': 'ok',
            'features': 'ok', 'verification skill': 'warn', 'scratch setup': 'ok',
            'verifier routine': 'ok', 'verifier fallback': 'ok', 'verifier escalated': 'ok'})

    def test_doctor_names_the_resolved_verifier_for_each_class(self):
        c = self.checks()
        self.assertIn("the run's current sidekick (source sidekick", c['verifier routine']['detail'])
        self.assertEqual(c['verifier fallback']['detail'], 'none')
        self.assertEqual(c['verifier escalated']['detail'],
                         'claude --model claude-opus-5-5 (source default); also used by verify --landing')
        roster = Path(self.tmp.name) / 'roster.toml'
        roster.write_text('[verifier]\nkind = "pi"\nargs = ["--model", "devin/swe-2", "--thinking", "high"]\n'
                          '[verifier.fallback]\nkind = "claude"\nargs = ["--model", "claude-haiku-5-5"]\n'
                          '[verifier.escalated]\nkind = "claude"\nargs = ["--model", "claude-sonnet-5-5"]\n')
        self.env['PSTACK_KITCHEN_ROSTER'] = str(roster)
        c = self.checks()
        self.assertEqual(c['verifier routine']['detail'], 'pi --model devin/swe-2 --thinking high (source roster)')
        self.assertEqual(c['verifier fallback']['detail'], 'claude --model claude-haiku-5-5 (source roster)')
        self.assertEqual(c['verifier escalated']['detail'], 'claude --model claude-sonnet-5-5 (source roster) (differs from the default claude --model claude-opus-5-5); also used by verify --landing')
        self.assertEqual(c['verifier escalated']['status'], 'ok')
        roster.write_text('[verifier.escalated]\nkind = "claude"\nargs = ["--model", "claude-opus-5-5"]\n')
        c = self.checks()
        self.assertEqual(c['verifier escalated']['detail'], 'claude --model claude-opus-5-5 (source roster); also used by verify --landing')
        roster.write_text('[verifier.escalated]\nkind = "master"\n[verifier]\nkind = "devin"\n')
        c = self.checks()
        self.assertEqual(c['verifier routine']['detail'], 'devin (model unspecified) (source roster)')
        self.assertIn("the master's own kind (source roster) (differs from the default claude --model claude-opus-5-5)", c['verifier escalated']['detail'])

    def test_problems_fail(self):
        self.change({'tools/gen.py': 'pass\n'})
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast = ["true"]', 'fast = ["cd src && nosuchtool --x"]')
                   .replace('verify = "unit"', 'verify = "unit"\nfeatures = ["missing.md"]'))
        c = self.checks(code=3)
        self.assertEqual(c['coverage']['items'], ['tools/gen.py'])
        self.assertEqual(c['commands']['status'], 'ok')  # cd is a builtin; only the first word counts
        self.assertEqual(c['features']['items'], ['contracts: missing.md'])
        self.write('.agents/kitchen.toml', KITCHEN.replace('fast = ["true"]', 'fast = ["nosuchtool --x"]'))
        self.assertEqual(self.checks(code=3)['commands']['items'], ['contracts.fast: nosuchtool'])

    def test_verification_skill_a_required_engine_and_a_legacy_reviewer_table(self):
        self.write('.agents/skills/verify-demo/SKILL.md', '---\nname: verify-demo\n---\n')
        self.write('.agents/kitchen.toml', KITCHEN + '\n[review]\nengine = "joo"\n[review.escalated]\nstyle = "thorough"\n')
        bin_dir = Path(self.tmp.name) / 'bin'
        bin_dir.mkdir()
        self.env['PATH'] = f'{bin_dir}:/usr/bin:/bin'
        c = self.checks()
        self.assertEqual(c['verification skill'], {'check': 'verification skill', 'status': 'ok',
                                                   'detail': '.agents/skills/verify-demo', 'items': []})
        self.assertEqual(c['review']['status'], 'warn')
        self.assertIn('review.escalated tuned a reviewer', c['review']['detail'])
        self.assertEqual(c['review engine']['status'], 'warn')
        self.assertIn('neither joo-dev nor joo is on PATH', c['review engine']['detail'])
        (bin_dir / 'joo').write_text('#!/bin/sh\n')
        (bin_dir / 'joo').chmod(0o755)
        c = self.checks()
        self.assertEqual(c['review engine']['status'], 'ok')
        self.assertIn(f'joo ({bin_dir}/joo); land-check requires', c['review engine']['detail'])
        out = self.run_kitchen('validate')
        self.assertIn('review.escalated are ignored: the kitchen tunes no reviewer', out)
        self.assertIn('review engine: joo (required', out)
        self.assertEqual(self.data('validate')['review_engine'], 'joo')
        self.write('.agents/kitchen.toml', KITCHEN + '\n[review]\nengine = "joo; rm"\n')
        self.assertIn('review.engine: expected a tool name', self.run_kitchen('validate', code=1))

    def test_run_records_a_baseline_and_fails_on_red(self):
        c = self.checks('--run')
        self.assertEqual(c['fast gates on HEAD']['status'], 'ok')
        self.assertIn('doctor: app pass in ', self.run_kitchen('doctor', '--run'))
        baseline = json.loads(next((Path(self.tmp.name) / 'state').rglob('baseline.json')).read_text())
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
        self.assertEqual(kitchen.run_counters(self.store(self.HEADER + self.WAKE)), kitchen.run_counters(self.store(self.WAKE)))

    def test_retro_rows_counts_the_first_event_of_a_headerless_store(self):
        rows = kitchen.retro_rows(self.store(self.FLAKY), 'run', 'T')
        self.assertEqual([(r[2], r[4]) for r in rows], [('gate-flaky', '001-x')])

    def test_retro_rows_gives_a_legacy_header_store_the_same_rows(self):
        self.assertEqual(kitchen.retro_rows(self.store(self.HEADER + self.FLAKY), 'run', 'T'),
                         kitchen.retro_rows(self.store(self.FLAKY), 'run', 'T'))


class PolicyRuleTests(unittest.TestCase):
    def test_repo_test_sleep_rule_refuses_blocking_waits_and_spares_test_helpers(self):
        rules = tomllib.loads((root / '.agents/kitchen.toml').read_text())['policy']['forbid']
        pattern = re.compile(next(r for r in rules if r['id'] == 'test-sleep')['pattern'])
        refused = 'time.sleep(1)|sleep 5|threading.Event().wait(0.2)|Event().wait(timeout)|await asyncio.sleep(0.1)|select.select([], [], [], 0.5)'  # kitchen-allow: test-sleep (samples of what the rule refuses)
        spared = ["out = self.wait(4)", "proc.wait(timeout=5)", "subprocess.Popen(['sleep', '30'])", "self.assertIn('x', self.wait(0))"]
        for line in refused.split('|'):
            self.assertTrue(pattern.search(line), line)
        for line in spared:
            self.assertIsNone(pattern.search(line), line)


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
            rows = ['ts\tepoch\tactor\tevent\tunit\tdetail'] + [f'x\t{e}\t{a}\t{v}\t-\t{det}' for e, a, v, det in events]
            (d / 'events.tsv').write_text('\n'.join(rows) + '\n')
        for b in briefs:
            (d / 'briefs' / f'{b}.md').write_text('# Brief\n')
        for r, status in reports:
            (d / 'reports' / f'{r}.md').write_text(f'# Report\n\nstatus: {status}\n')
        for name_, status, units in verdicts:
            (d / 'verdicts' / f'{name_}.md').write_text(f'# Verdict\n\nstatus: {status}\nunits: {units}\n')
        return d

    def fleet(self, *args):
        r = subprocess.run([sys.executable, str(script), '--json', 'fleet', '--runs', str(self.runs), *args],
                           env=self.env, capture_output=True, text=True, check=True)
        out = json.loads(r.stdout)
        return {x['run']: x for x in out['runs']}, {x['build']: x for x in out['builds']}

    def test_verified_units_are_the_union_over_clean_non_landing_verdicts(self):
        self.store('a', pair={'git_root': str(self.t), 'sidekick': {'failovers': [{}, {}]},
                              'skill_builds': [{'path': '/s/one'}]},
                   events=[(100, 'master', 'init', ''), (160, 'master', 'wake', 'x'), (220, 'master', 'wake', 'verify:provider'),
                           (300, 'master', 'catch', 'gate'), (400, 'sidekick', 'gate-flaky', 'app')],
                   briefs=['001-a', '002-b', '003-c', '004-d'],
                   reports=[('001-a', 'done'), ('002-b', 'done'), ('003-c', 'done'), ('004-d', 'partial')],
                   verdicts=[('002-b-v1', 'clean', '001 002'), ('003-c-v1', 'reject', '003'), ('003-c-v2', 'clean', '003'),
                             ('003-c-v3', 'clean', '003'), ('005-e-v1', 'clean (gates only: docs)', '005'),
                             ('landing-v1', 'clean', 'landing'), ('002-b-v1-packet', 'clean', '999')])
        (self.t / 'state/pstack/builds.tsv').write_text('1\tabc123\t/s/one\n')
        runs, builds = self.fleet()
        a = runs['a']
        self.assertEqual((a['done_units'], a['verified_units'], a['gates_only_units'], a['landing']), (3, 4, 1, 'clean'))
        self.assertEqual((a['wakes'], a['wakes_per_verified'], a['failovers']), (2, 0.5, 2))
        self.assertEqual((a['verify_provider'], a['verify_missing'], a['gate_flaky'], a['catches']), (1, 0, 1, {'gate': 1}))
        self.assertEqual((a['clean_first_try'], a['build'], a['started'], a['last']), (2, 'abc123', 100, 400))
        self.assertEqual(builds['abc123']['runs'], 1)

    def test_a_verdict_slug_containing_packet_still_counts(self):
        self.store('p', pair={}, events=[(10, 'master', 'init', '')],
                   verdicts=[('003-packet-hardening-v1', 'clean', '003'), ('003-packet-hardening-v1-packet', 'clean', '999')])
        runs, _ = self.fleet()
        self.assertEqual((runs['p']['verified_units'], runs['p']['clean_first_try']), (1, 1))

    def test_last_ends_at_the_final_working_event_not_the_close_rows(self):
        self.store('c', pair={}, events=[(100, 'master', 'init', ''), (500, 'master', 'feedback', 'x'),
                                         (4100, 'master', 'close', 'master'), (4101, 'master', 'close', 'sidekick')])
        self.store('only', pair={}, events=[(7, 'master', 'close', 'master')])
        runs, _ = self.fleet()
        self.assertEqual((runs['c']['last'], runs['only']['last']), (500, 7))

    def test_mixed_and_unstamped_runs_get_their_own_build_rows(self):
        self.store('m', pair={'skill_builds': [{'path': '/s/one'}, {'path': '/s/two'}]}, events=[(10, 'master', 'wake', '')])
        self.store('u', pair={}, events=[(20, 'master', 'wake', '')])
        self.store('empty')
        runs, builds = self.fleet()
        self.assertEqual((runs['m']['build'], runs['u']['build'], runs['empty']['build']), ('mixed', 'unknown', 'unknown'))
        self.assertEqual(set(builds), {'mixed', 'unknown'})
        self.assertEqual(builds['unknown']['runs'], 2)
        self.assertEqual(builds['unknown']['eligible_runs'], 0)
        e = runs['empty']
        self.assertEqual((e['done_units'], e['verified_units'], e['wakes'], e['wakes_per_verified'], e['failovers'], e['started']),
                         (0, 0, 0, None, 0, None))

    def packet(self, run, name, agent=None, cls=None, outcome=None):
        lines = ['# Verify x', '', 'verdict: v']
        lines += [f'verifier-agent: {agent}'] if agent else []
        lines += [f'verifier-class: {cls}'] if cls else []
        lines += [f'verifier-outcome: {outcome}'] if outcome else []
        (self.runs / run / 'verdicts' / f'{name}-packet.md').write_text('\n'.join(lines) + '\n')

    def fleet_verifiers(self):
        r = subprocess.run([sys.executable, str(script), '--json', 'fleet', '--runs', str(self.runs)],
                           env=self.env, capture_output=True, text=True, check=True)
        return {(v['agent'], v['class']): v for v in json.loads(r.stdout)['verifiers']}, r.stdout

    def test_fleet_counts_verifier_attempts_by_agent_class_and_outcome(self):
        self.store('a', pair={}, events=[(10, 'master', 'init', '')])
        for n, o in [(1, 'provider'), (2, 'clean'), (3, 'reject'), (4, 'invalid'), (5, 'missing'), (6, 'start-failed'),
                     (7, 'running'), (8, 'prepared'), (9, 'inconclusive')]:
            self.packet('a', f'00{n}-x-v1', 'pi --model devin/swe-2', 'routine', o)
        self.packet('a', '010-x-v1', 'claude --model claude-sonnet-5-5', 'landing', 'clean')
        self.packet('a', '011-x-v1')
        self.store('b', pair={}, events=[(20, 'master', 'init', '')])
        self.packet('b', '001-x-v1', 'pi --model devin/swe-2', 'routine', 'clean')
        v, _ = self.fleet_verifiers()
        pi = v[('pi --model devin/swe-2', 'routine')]
        self.assertEqual((pi['started'], pi['clean'], pi['reject'], pi['inconclusive'], pi['invalid']), (10, 2, 1, 1, 1))
        self.assertEqual((pi['provider'], pi['missing'], pi['start-failed'], pi['running'], pi['unknown']), (1, 1, 1, 2, 0))
        self.assertEqual(v[('claude --model claude-sonnet-5-5', 'landing')]['clean'], 1)
        self.assertEqual(v[('unknown', 'unknown')]['unknown'], 1)

    def test_fleet_text_carries_the_caveat_and_gates_only_verdicts_make_no_attempt(self):
        self.store('a', pair={}, events=[(10, 'master', 'init', '')], verdicts=[('001-a-v1', 'clean (gates only; mode gates)', '001')])
        r = subprocess.run([sys.executable, str(script), 'fleet', '--runs', str(self.runs)], env=self.env,
                           capture_output=True, text=True, check=True)
        self.assertIn('reported outcomes on different workloads, not model accuracy', r.stdout)
        v, _ = self.fleet_verifiers()
        self.assertEqual(v, {})

    def test_since_filters_by_the_first_event_and_fleet_writes_nothing(self):
        self.store('old', pair={}, events=[(1_000_000_000, 'master', 'init', '')])
        self.store('new', pair={}, events=[(1_790_000_000, 'master', 'init', '')])
        before = sorted(str(p) for p in self.t.rglob('*'))
        runs, _ = self.fleet('--since', '2026-01-01')
        self.assertEqual(list(runs), ['new'])
        self.assertEqual(sorted(str(p) for p in self.t.rglob('*')), before)


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
        roster.write_text('[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n'
                          '[sidekick.fallback]\nkind = "devin"\n[consultant]\nkind = "codex"\n')
        self.env = {**inherited_env(), 'FAKE': str(self.fake), 'HERDR_SOCKET_PATH': str(self.fake / 'herdr.sock'), 'KITCHEN_VERIFY_POLL_S': '0.2', 'KITCHEN_VERIFY_GRACE_POLL_S': '0.2', 'KITCHEN_STEP_POLL_S': '0.2', 'KITCHEN_VERDICT_SETTLE_S': '0', 'PSTACK_KITCHEN_SLOT_POLL_S': '0.2', 'PAIR_FILE_POLL_MS': '200', 'PAIR_SETTLE_POLL_S': '0.2', 'PAIR_STOP_POLL_S': '0.2',  'PSTACK_KITCHEN_ROSTER': str(roster),
                    'PATH': f'{root / "tests/pstack-pair/bin"}:{os.environ["PATH"]}',
                    'HOME': str(t / 'home'), 'XDG_STATE_HOME': str(t / 'state'),
                    'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0', 'PAIR_SETTLE_HOLD': '0',
                    'PAIR_SUBMIT_CHECK_S': '0', 'KITCHEN_QUIET_MAX_S': '20', 'KITCHEN_QUIET_POLL_S': '0', 'PSTACK_KITCHEN_LOAD': '1 8',
                    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        self.env.pop('CLAUDE_CODE_SESSION_ID', None)
        watch_jq(self, self.env)
        self.git('init', '-q')
        kitchen_toml = KITCHEN.replace('fast = ["test -f src/app.py"]', 'fast = ["test ! -e FAIL"]') \
            + '\n[review]\nself = ["no-comments"]\n'
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
        self.run_sh('spawn', str(self.store))

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

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
        return [r.split('\t', 2)[2] for r in log.read_text().splitlines() if r.split('\t')[2:4] == ['master', 'wake']]

    def run_sh(self, *args, code=0, timeout=None):
        before = self.wake_rows()
        result = subprocess.run([str(self.script), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, check=False, timeout=timeout)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        added = self.wake_rows()[len(before):]
        # One kitchen.sh call returns to its caller once (a retry it execs is the same return), so it logs at most one master wake.
        self.assertLessEqual(len(added), 1, f'kitchen.sh {args[0]} logged {len(added)} master wake rows for one return: {added}')
        return result.stdout + result.stderr

    def state(self):
        return json.loads((self.store / 'pair.json').read_text())

    def brief(self, seq, slug, scope, playbook='feature', plan='none'):
        path = self.store / 'briefs' / f'{seq}-{slug}.md'
        text = (skill / 'references/brief-template.md').read_text()
        text = text.replace('{{SEQ}}', seq).replace('{{SLUG}}', slug).replace('{{STORE}}', str(self.store))
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
        text = text.replace('- {{one repo-relative path or glob per line, in full (never relative to the line above); `quote` a path that contains a comma; a note may follow after " — ". The kitchen classifies the unit from these lines.}}',
                            '\n'.join(f'- {g} — scope' for g in scope))
        text = re.sub(r'\{\{[^}]*\}\}', 'x', text, flags=re.DOTALL)
        path.write_text(text)
        return path

    def dispatch(self, brief, code=4):
        return self.run_sh('dispatch', str(self.store), str(brief), '--timeout', '1', code=code)

    def done(self, brief, head):
        (self.store / 'reports' / brief.name).write_text(f'# Report\n\nstatus: done\nhead: {head}\n')

    def kitchen_variant(self):
        spec = importlib.util.spec_from_file_location('kitchen_variant_under_test', skill / 'scripts/variant.py')
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(root / 'skills/pstack-pair/scripts'))
        spec.loader.exec_module(module)
        return module

    def test_replies_kitchen_steps_due_is_false_unless_the_brief_is_escalated(self):
        # risk_of reads the brief's `risk:` header as written (case-sensitive, cut at no word) and steps_due is true only for `escalated`: these are the expected literals.
        module = self.kitchen_variant()
        from pstack_core.context import Context
        ctx = Context('pstack-kitchen', str(skill), '/s', 'Kitchen', (), (), 20, '')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        steps = self.store / 'steps/001-util.tsv'
        steps.parent.mkdir(exist_ok=True)
        steps.write_text('1\tabc\t1\t\tone\n2\tdef\t2\t\ttwo\n')
        text = b.read_text()
        for header, risk, due in (('risk: routine', 'routine', False), ('risk: escalated', 'escalated', True), ('risk: escalated (contract X changes)', 'escalated', True),
                                  ('risk:', '', False), ('risk: Escalated', 'Escalated', False)):
            b.write_text(re.sub(r'^risk:.*$', header, text, flags=re.M))
            self.assertEqual(module.risk_of(str(b)), risk, header)
            self.assertEqual(module.VARIANT().steps_due(ctx, str(self.store), str(b)), due, header)
        b.write_text(re.sub(r'^risk:.*\n', '', text, flags=re.M))
        self.assertEqual((module.risk_of(str(b)), module.VARIANT().steps_due(ctx, str(self.store), str(b))), ('', False))
        self.assertFalse(module.VARIANT().steps_due(ctx, str(self.store), ''))

    def test_verify_job_is_the_brief_name_the_head_prefix_and_the_crc32_of_the_verify_block(self):
        module = self.kitchen_variant()
        # the expected numbers are python3 zlib.crc32 of the Verify block's bytes, each line ended by a newline, with the blank line before the next heading
        blocks = {'empty': (b'', 852952723), 'no trailing newline': (b'true', 2703426882), 'plain': (b'python3 tests/run.py -k x\ngit diff --exit-code\n', 2501220589),
                  'non-ascii': ('echo \u00e9 \u2192 \U0001F600\n'.encode(), 1464077100), 'over 4 KiB': (b'echo ' + b'x' * 5000 + b'\n' + b'true\n' * 900, 1036027346)}
        for name, (block, cksum) in blocks.items():
            brief = Path(self.tmp.name) / f'{name.replace(" ", "-")}-001-x.md'
            brief.write_bytes(b'# Brief\n\n## Goal\n\ng\n\n## Verify\n' + block + b'\n## Forbidden\n\nnone\n')
            sha = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'
            self.assertEqual(module.verify_job('/store', str(brief), sha), f'/store/steps/jobs/{brief.stem}-verify-a1b2c3d4e5f6-{cksum}', name)
        bare = Path(self.tmp.name) / '002-y.md'
        bare.write_bytes(b'# Brief\n\n## Verify\n\ntrue')
        self.assertEqual(module.verify_job('/store', str(bare), 'abc'), '/store/steps/jobs/002-y-verify-abc-2002284698', 'a Verify block that ends the file')

    def test_kitchen_report_ready_adds_the_verify_job_to_the_pairs_readiness(self):
        # the why strings below are the literals an agent reads when report_ready says a done report is not ready.
        module = self.kitchen_variant()
        ready = lambda path: module.VARIANT().report_ready(None, str(self.store), str(path))
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('work')
        report = self.store / 'reports' / b.name
        self.done(b, head)
        job = Path(module.verify_job(str(self.store), str(b), head))
        short = f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)"
        got = ready(report)
        self.assertEqual((got.ready, got.open, got.why), (False, '', short), 'rc missing')
        job.mkdir(parents=True)
        (job / 'rc').write_text('1\n')
        self.assertEqual((ready(report).ready, ready(report).why), (False, short), 'rc 1')
        (job / 'rc').write_text('0\n')
        got = ready(report)
        self.assertEqual((got.ready, got.open, got.why), (True, '', ''), 'rc 0')
        self.done(b, 'f' * 40)
        got = ready(report)
        self.assertEqual((got.ready, got.why), (False, 'its head: is not a commit'), 'head not a commit')
        other = self.store / 'reports/002-other.md'
        other.write_text(f'# Report\n\nstatus: done\nhead: {head}\n')
        self.assertTrue(ready(other).ready, 'not the dispatched unit\'s report')
        self.done(b, head)
        (self.store / 'notes').mkdir(exist_ok=True)
        (self.store / 'notes/001-util-n1.md').write_text('# N\n\nstatus: blocking\n')
        got = ready(report)
        self.assertEqual((got.ready, got.open, got.why), (False, str(self.store / 'notes/001-util-n1.md'), 'open blocking notes n1'), 'the pair says no first')

    def test_a_dispatch_with_a_number_flag_that_is_no_whole_number_sends_nothing(self):
        """--max, --timeout and --every are read before the command changes anything: `dispatch --max -5` ends with status 1 and the usage error, and
        no brief is recorded and nothing is sent."""
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        before = self.state()
        calls = (self.fake / 'calls.log').read_text() if (self.fake / 'calls.log').exists() else ''
        for flag, word, unit in (('--max', '-5', 'minutes'), ('--max', 'x', 'minutes'), ('--timeout', '1.5', 'milliseconds'), ('--every', '1+1', 'minutes')):
            out = self.run_sh('dispatch', str(self.store), str(b), flag, word, code=1)
            self.assertIn(f'{flag} must be a whole number of {unit}', out)
            self.assertEqual(self.state(), before, (flag, word))
            after = (self.fake / 'calls.log').read_text() if (self.fake / 'calls.log').exists() else ''
            self.assertNotIn('agent prompt', after[len(calls):], (flag, word))

    def test_a_verify_job_under_the_old_cksum_key_is_ignored_and_never_collides_with_the_crc32_key(self):
        """Plan 043 U5 switched the Verify job key from POSIX cksum to crc32. A completed job directory under the old key (rc 0) does not make a
        done report ready and is not the directory finish uses now, so the first finish after the switch runs the Verify block again."""
        module = self.kitchen_variant()
        ready = lambda path: module.VARIANT().report_ready(None, str(self.store), str(path))
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('work')
        self.done(b, head)
        block = module.verify_block(str(b))
        old_key = subprocess.run(['cksum'], input=block, capture_output=True, check=True).stdout.split()[0].decode()
        old_job = self.store / 'steps/jobs' / f'{b.stem}-verify-{head[:12]}-{old_key}'
        new_job = Path(module.verify_job(str(self.store), str(b), head))
        self.assertNotEqual(old_key, str(zlib.crc32(block)))
        self.assertNotEqual(old_job, new_job)
        old_job.mkdir(parents=True)
        (old_job / 'rc').write_text('0\n')
        got = ready(self.store / 'reports' / b.name)
        self.assertEqual((got.ready, got.why), (False, f"the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them)"))
        self.assertFalse(new_job.exists())
        self.assertEqual((old_job / 'rc').read_text(), '0\n')

    def test_kitchen_log_records_the_decision_and_a_master_log_event(self):
        self.run_sh('log', str(self.store), 'review', 'accept 001', 'diff clean', 'reviews/001', 'next')
        rows = (self.store / 'decisions.tsv').read_text().splitlines()
        self.assertEqual(rows[-1].split('\t')[1:], ['review', 'accept 001', 'diff clean', 'reviews/001', 'next'])
        self.assertEqual((self.store / 'events.tsv').read_text().splitlines()[-1].split('\t')[2:], ['master', 'log', '-', 'review: accept 001'])
        out = self.run_sh('log', str(self.store), 'review', code=1)
        self.assertEqual(out, 'kitchen.sh: usage: kitchen.sh log <store> <phase> <decision> <why> <evidence> <result>\n')

    def test_kitchen_new_plan_new_brief_and_new_steer_fill_the_kitchen_templates(self):
        plan = self.run_sh('new-plan', str(self.store), 'shape').strip()
        brief = self.run_sh('new-brief', str(self.store), 'shape').strip()
        self.assertEqual((plan, brief), (f'{self.store}/plans/001-shape.md', f'{self.store}/briefs/002-shape.md'))
        self.assertEqual(Path(plan).read_text().splitlines()[0], '# Plan 001: shape')
        self.assertEqual(Path(brief).read_text().splitlines()[0], '# Brief 002: shape')
        steer = self.run_sh('new-steer', str(self.store), '002').strip()
        self.assertEqual(steer, f'{self.store}/steers/002-shape-s1.md')
        self.assertEqual(Path(steer).read_text().splitlines()[:3],
                         ['# Steer 002-s1: shape', '', f'brief: {self.store}/briefs/002-shape.md'])

    def test_kitchen_pause_reaches_the_sidekick_at_progress_until_resume(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        progress = self.store / 'progress/001-util.md'
        self.assertEqual(self.run_sh('progress', str(self.store), 'step 1 done').splitlines(), [str(progress)])
        self.assertRegex(progress.read_text(), r'^\d\d:\d\d step 1 done\n$')
        self.assertIn('paused ', self.run_sh('pause', str(self.store), '--reason', 'lunch'))
        out = self.run_sh('progress', str(self.store), 'step 2 done').splitlines()
        self.assertEqual(out[0], str(progress))
        self.assertTrue(out[1].startswith('PAUSE: lunch. This is your safe point'), out)
        self.assertEqual(self.run_sh('resume', str(self.store)), f'resumed {self.store}\n')
        self.assertEqual(self.run_sh('progress', str(self.store), 'step 3 done').splitlines(), [str(progress)])

    def test_the_help_lists_log_with_its_six_arguments_and_errors_name_the_script_that_printed_them(self):
        self.assertIn('  log <store> <phase> <decision> <why> <evidence> <result>\n', self.run_sh('help'))
        # a ported command run through kitchen.sh, a bash `die` of kitchen.sh's own, and the same command through pair.sh
        self.assertEqual(self.run_sh('notes', code=1), 'kitchen.sh: usage: kitchen.sh notes <store>\n')
        self.assertEqual(self.run_sh('verify', code=1).splitlines()[0].split(':')[0], 'kitchen.sh')
        pair = skill.parent / 'pstack-pair/scripts/pair.sh'
        r = subprocess.run([str(pair), 'notes'], env=self.env, capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: usage: pair.sh notes <store>\n'))

    def test_kitchen_notes_lists_a_blocking_note_until_a_step_resolves_it_and_followups_are_tagged(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        base = self.state()['dispatch']['head']
        self.write('src/util.py', 'X = 2\n')
        c1 = self.commit('one')
        self.write('src/util.py', 'X = 3\n')
        c2 = self.commit('two')
        steps = self.store / 'steps/001-util.tsv'
        steps.parent.mkdir(exist_ok=True)
        steps.write_text(f'1\t{c1}\t1\t\tone\n2\t{c2}\t2\t\ttwo\n')
        draft = Path(self.run_sh('new-note', str(self.store), '001', '--through', '1').strip())
        self.assertEqual(draft, self.store / 'notes/001-util-n1.md.draft')
        self.assertIn(f'range: {base[:9]}..{c1[:9]}\n', draft.read_text())
        self.assertIn('unfilled placeholders', self.run_sh('note', str(self.store), str(draft), code=1))
        draft.write_text('# Note 001-util n1\n\nunit: 001-util\nthrough: step 1\nstatus: blocking\n\n## Blocking\n\n'
                         '- src/util.py:1, X is wrong, expected 3\n\n## Follow-ups\n\n- rename X\n- none\n')
        self.assertEqual(self.run_sh('note', str(self.store), str(draft)), f'published {draft.with_suffix("")} (blocking)\n')
        self.assertFalse(draft.exists())
        self.assertEqual((self.store / 'followups.md').read_text(), '- [001-util n1] rename X\n')
        self.assertIn('master\tnote\t001-util\t1:blocking\n', (self.store / 'events.tsv').read_text())
        out = self.run_sh('notes', str(self.store))
        self.assertIn('notes: 1 open.', out)
        self.assertIn(f'\n=== n1 ({self.store}/notes/001-util-n1.md)\n\n- src/util.py:1, X is wrong, expected 3\n', out)
        steps.write_text(f'1\t{c1}\t1\t\tone\n2\t{c2}\t2\tn1\ttwo\n')
        self.assertEqual(self.run_sh('notes', str(self.store)), 'notes: none open; go on with the next step\n')
        second = self.run_sh('new-note', str(self.store), '001').strip()
        self.assertEqual(second, f'{self.store}/notes/001-util-n2.md.draft')
        self.assertIn(f'range: {c1[:9]}..{c2[:9]}\n', Path(second).read_text())
        self.assertEqual(self.run_sh('new-note', str(self.store), '001', '--through', '1', code=1),
                         'kitchen.sh: unit 001 is reviewed through step 1 already; wait for the next steps\n')

    def test_kitchen_status_lists_the_three_roles_the_advice_column_and_saves_status_md(self):
        out = self.run_sh('status', str(self.store))
        lines = out.splitlines()
        self.assertEqual(lines[0], '# Kitchen status')
        roles = [l for l in lines if re.match(r'(sidekick|verifier|consultant): ', l)]
        self.assertEqual([l.split(':')[0] for l in roles], ['sidekick', 'verifier', 'consultant'])
        self.assertRegex(roles[0], r'^sidekick: demo-sidekick \(\S+\) idle$')
        self.assertEqual(roles[1], 'verifier: demo-verifier (unassigned) absent')
        self.assertEqual(roles[2], 'consultant: demo-consultant (unassigned) absent')
        self.assertIn('| seq | kind | unit | report | advice | review |\n|---|---|---|---|---|---|\n', out)
        self.assertEqual((self.store / 'status.md').read_text(), out)
        self.brief('001', 'util', ['src/**', 'tests/**'])
        row = [l for l in self.run_sh('status', str(self.store)).splitlines() if l.startswith('| 001 ')]
        self.assertEqual(row, ['| 001 | brief | util | none | none | none |'])

    def scratch_stdout(self, *args):
        """stdout alone, as kitchen.sh reads it with | tail -1."""
        r = subprocess.run([str(self.script), 'scratch', str(self.store), *args], cwd=self.repo, env=self.env, capture_output=True, text=True, check=False)
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
        self.assertEqual(self.scratch_stdout('001-x-review', '--at', sha).splitlines()[-1], str(path))
        self.assertEqual(self.run_sh('scratch', str(self.store), '001-x-review', '--remove'), f'removed {path}\n')
        self.assertEqual(self.run_sh('scratch', str(self.store), '001-x-review', '--remove'), f'removed {path}\n')
        self.assertFalse(path.exists())
        self.assertFalse((self.store / 'scratch/001-x-review').is_symlink())
        self.assertEqual(self.state()['scratch'], [])
        live = Path(self.scratch_stdout('002-x-live').splitlines()[-1])
        self.assertEqual((live / 'src/util.py').read_text(), 'X = 99\n')
        self.run_sh('scratch', str(self.store), '002-x-live', '--remove')

    def test_kitchen_metrics_reports_wall_time_and_review_latency_from_events_tsv(self):
        rows = [('1000', 'master', 'send-brief', '001-util', ''),
                ('1600', 'sidekick', 'report', '001-util', ''),
                ('1700', 'master', 'wake', '001-util', 'report:done'),
                ('1760', 'master', 'log', '-', 'review: accepted 001')]
        (self.store / 'events.tsv').write_text(''.join(f'2026-01-01T00:00:00Z\t{epoch}\t{actor}\t{ev}\t{unit}\t{detail}\n'
                                                      for epoch, actor, ev, unit, detail in rows))
        self.env['PAIR_METRICS_NOW'] = '1800'
        out = self.run_sh('metrics', str(self.store)).splitlines()
        self.assertEqual(out[0], 'wall: 13m from first event to last')
        self.assertIn('master: 1 wakes, 0 of them check-ins; review latency median 3m over 1 reviews', out)
        self.assertIn('sidekick: busy 10m (78% of wall), idle 0m across 0 gaps; 1 briefs (median 10m), 0 plan responses (0m)', out)
        self.assertIn('verdicts: accept 1', out)

    def test_init_puts_the_kitchen_in_the_standing_orders(self):
        orders = (self.store / 'standing-orders.md').read_text()
        self.assertIn('runs these skills on its diff and fixes what they find: no-comments', orders)
        self.assertIn('Landing goes as far as `commit`', orders)
        self.run_sh('init', 'demo')
        self.assertEqual((self.store / 'standing-orders.md').read_text(), orders)

    def test_init_prepares_a_linked_worktree_with_the_scratch_setup_as_the_sidekick(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + '\n[scratch]\nsetup = ["printf %s \\"$PSTACK_KITCHEN_ROLE\\" > installed"]\n')
        self.commit('declare setup')
        self.assertNotIn('worktree:', self.run_sh('init', 'demo'))
        self.assertFalse((self.repo / 'installed').exists())
        wt = Path(self.tmp.name) / 'wt'
        self.git('worktree', 'add', '-q', '--detach', str(wt), 'HEAD')
        def init(slug):
            return subprocess.run([str(self.script), 'init', slug], cwd=wt, env=self.env, capture_output=True, text=True)
        r = init('wt')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('worktree: prepared with [scratch].setup (setup: ok (1 commands', r.stdout)
        self.assertEqual((wt / 'installed').read_text(), 'sidekick')
        timings = list((Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob('*/timings.tsv'))
        self.assertFalse(any('\tsetup\t' in f.read_text() for f in timings))
        (wt / '.agents/kitchen.toml').write_text(toml.read_text().replace('> installed', '; exit 3'))
        r = init('wt2')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('this run is in a linked worktree and its [scratch].setup failed', r.stderr)

    def test_spawn_takes_the_roster_and_classify_stamps_the_brief(self):
        sidekick = self.state()['sidekick']
        self.assertEqual((sidekick['kind'], sidekick['fallback']['kind']), ('pi', 'devin'))
        self.assertEqual(sidekick['start_args'][-2:], ['--model', 'devin/swe-2'])
        b = self.brief('001', 'util', ['src/util.py'])
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('risk: routine', out)
        text = b.read_text()
        self.assertIn('risk: routine\nprofiles: app\nverify: batch by routine verifier\n', text)
        e = self.brief('002', 'api', ['contracts/**'])
        out = self.run_sh('classify', str(self.store), str(e))
        self.assertIn('escalate: escalate path: contracts/api.json', out)
        self.assertIn('next: kitchen.sh consultant', out)

    def test_classify_keeps_the_masters_reason_on_the_risk_line_when_the_class_is_unchanged(self):
        # B6: an escalated brief that classifies escalated keeps `escalated (contract change)`; one whose class changes is rewritten.
        e = self.brief('002', 'api', ['contracts/**'])
        e.write_text(re.sub(r'(?m)^risk:.*$', 'risk: escalated (contract change)', e.read_text()))
        self.run_sh('classify', str(self.store), str(e))
        self.assertIn('\nrisk: escalated (contract change)\n', e.read_text())
        r = self.brief('003', 'api2', ['contracts/**'])
        r.write_text(re.sub(r'(?m)^risk:.*$', 'risk: routine (it is small)', r.read_text()))
        out = self.run_sh('classify', str(self.store), str(r))
        self.assertIn('risk: escalated', out)
        self.assertIn('\nrisk: escalated\n', r.read_text())
        self.assertNotIn('it is small', r.read_text())

    def generate_pair(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + '\n[[generated]]\nsources = ["src/**"]\noutputs = ["docs/**"]\ncommand = "make gen"\n')
        self.commit('generated pair')

    def test_classify_and_dispatch_print_the_generated_outputs_warning(self):
        plain = self.run_sh('classify', str(self.store), str(self.brief('001', 'util', ['src/util.py'])))
        self.assertNotIn('warn:', plain)
        self.generate_pair()
        out = self.run_sh('classify', str(self.store), str(self.brief('002', 'lib', ['src/util.py'])))
        self.assertIn('warn: Scope covers src/util.py but none of its generated outputs (docs/**)', out)
        self.assertIn('(regenerate: make gen)', out)
        covered = self.run_sh('classify', str(self.store), str(self.brief('003', 'both', ['src/util.py', 'docs/**'])))
        self.assertNotIn('warn:', covered)
        out = self.dispatch(self.brief('004', 'disp', ['src/util.py']))
        self.assertIn('warn: Scope covers src/util.py', out)

    def test_an_escalated_plan_or_header_keeps_a_routine_scope_escalated(self):
        plan = self.store / 'plans/001-util.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: util\n\nrisk: escalated\n')
        b = self.brief('001', 'util', ['src/util.py'], plan=str(plan))
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('escalate: escalated plan: 001-util.md', out)
        self.assertIn('risk: escalated\nprofiles: app\nverify: unit by escalated verifier\n', b.read_text())
        plan.write_text('# Plan 001: util\n\nrisk: routine\n')
        out = self.run_sh('classify', str(self.store), str(b))
        self.assertIn('escalate: the brief says risk: escalated', out)
        self.assertIn('risk: escalated\n', b.read_text())

    def test_a_unit_dispatched_as_escalated_is_verified_by_the_escalated_class(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        b.write_text(b.read_text().replace('risk: routine', 'risk: escalated'))
        self.run_sh('verify', str(self.store), '001')
        self.assertEqual(self.packet_fields()['verifier-class'], 'escalated')
        self.assertIn('risk: escalated\n', (self.store / 'verdicts/001-util-v1-packet.md').read_text())

    def test_escalated_units_need_an_agreed_plan_and_routine_ones_do_not(self):
        self.dispatch(self.brief('001', 'util', ['src/util.py']))
        self.dispatch(self.brief('002', 'api', ['contracts/**']), code=6)

    def test_step_records_only_after_gates_and_policy_pass(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('step 1')
        self.assertIn('gates: pass (app)', self.run_sh('step', str(self.store), sha, 'util two'))
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
        self.assertEqual(len((self.store / 'steps/001-util.tsv').read_text().splitlines()), 2)

    def job(self):
        """The one job directory of a step on unit 001-util."""
        (found,) = (self.store / 'steps/jobs').glob('001-util-[0-9a-f]*')
        return found

    def scrub(self, text):
        return re.sub(r' [0-9.]+s ', ' <S>s ', re.sub(r'log: \S+/(\S+\.log)', r'log: <logs>/\1', text))

    def test_a_step_and_a_finish_write_the_out_log_and_rc_the_bash_workers_wrote(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, "grep -q 'X = 2' src/util.py")
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('one')
        self.run_sh('step', str(self.store), sha, 'one')
        step_job = self.job()
        self.assertEqual((self.scrub((step_job / 'out.1').read_text()), (step_job / 'rc').read_text()),
                         ('app fast: pass\n  exit 0 <S>s test ! -e FAIL  log: <logs>/app-fast-0.log\npolicy: clean\n', '0\n'))
        self.done(b, sha)
        self.run_sh('finish', str(self.store), str(self.store / 'reports' / b.name))
        (finish_job,) = (self.store / 'steps/jobs').glob('001-util-verify-*')
        self.assertEqual((self.scrub((finish_job / 'out').read_text()), (finish_job / 'rc').read_text()),
                         ("verify: pass (1 commands from the brief)\n  exit 0 <S>s grep -q 'X = 2' src/util.py  log: <logs>/verify-0.log\n", '0\n'))

    def test_a_failing_gate_a_policy_failure_and_a_failing_verify_command_leave_rc_2(self):
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
        policy_job = max((self.store / 'steps/jobs').glob('001-util-*'), key=lambda d: (d / 'rc').stat().st_mtime_ns)
        self.assertEqual((policy_job / 'rc').read_text(), '2\n')
        self.assertIn('app fast: pass', (policy_job / 'out.1').read_text())
        self.assertIn('[no-print]', (policy_job / 'out.1').read_text())
        self.done(b, sha)
        self.run_sh('finish', str(self.store), str(self.store / 'reports' / b.name), code=2)
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
        argv = (['step', str(self.repo), str(stub), str(job), 'base', 'head'] if command == 'step'
                else ['finish', str(self.repo), str(stub), 'brief.md', str(job)])
        r = subprocess.run([sys.executable, '-I', str(worker), *argv], capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        return (job / 'rc').read_text()

    def test_the_worker_writes_the_exit_status_of_its_child_as_a_shell_would(self):
        self.assertEqual(self.run_worker('finish', 'sys.exit(3)\n'), '3\n')
        self.assertEqual(self.run_worker('finish', 'os.kill(os.getpid(), signal.SIGTERM)\n'), '143\n')
        self.assertEqual(self.run_worker('finish', 'sys.exit(0)\n'), '0\n')

    def test_a_step_is_rc_2_for_a_failing_gate_or_a_failing_policy_check_and_0_otherwise(self):
        failing_gate = "sys.exit(7 if 'gate' in argv else 0)\n"
        failing_policy = "sys.exit(7 if 'policy' in argv else 0)\n"
        self.assertEqual(self.run_worker('step', failing_gate), '2\n')
        self.assertEqual(self.run_worker('step', failing_policy), '2\n')
        self.assertEqual(self.run_worker('step', 'sys.exit(0)\n'), '0\n')
        self.assertEqual(self.run_worker('step', failing_gate, profiles='\n'), '0\n')

    def test_the_worker_exits_1_and_writes_nothing_when_the_repo_is_gone(self):
        job = self.store / 'job'
        job.mkdir()
        (job / 'profiles').write_text('app\n')
        worker = root / 'skills/pstack-kitchen/scripts/kitchen_core/worker.py'
        gone = str(self.store / 'no-such-repo')
        for argv in (['step', gone, 'kitchen.py', str(job), 'b', 'h'], ['finish', gone, 'kitchen.py', 'brief.md', str(job)]):
            r = subprocess.run([sys.executable, '-I', str(worker), *argv], capture_output=True, text=True, check=False)
            self.assertEqual((r.returncode, r.stdout, r.stderr, sorted(p.name for p in job.iterdir())), (1, '', '', ['profiles']), argv[0])

    def test_step_refuses_a_sha_that_is_not_head_and_a_dirty_tracked_tree_before_any_gate(self):
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
        self.assertEqual(out, f'kitchen.sh: step {first}: HEAD is {self.git("rev-parse", "--short", "HEAD")}; commit the step and record HEAD\n')
        self.assertFalse((self.store / 'gates.tsv').exists())
        self.write('src/util.py', 'X = 4\n')
        head = self.git('rev-parse', 'HEAD')
        out = self.run_sh('step', str(self.store), head, 'two', code=1)
        self.assertEqual(out, f'kitchen.sh: step {head}: the tree has uncommitted changes to tracked files; commit or stash them first\n')
        self.assertFalse((self.store / 'gates.tsv').exists())
        self.assertEqual(events(), before)
        self.assertFalse((self.store / 'steps/001-util.tsv').exists())
        # an untracked file is not a tracked change
        self.git('checkout', '--', 'src/util.py')
        self.write('untracked.txt', 'x\n')
        self.assertIn('gates: pass', self.run_sh('step', str(self.store), head, 'two'))

    def test_recording_the_same_sha_again_runs_nothing_and_merges_resolves(self):
        # B2, B4. The second record of a commit runs no gate, touches no job directory, writes no row and logs no event; --resolves still lands.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('step 1')
        note = self.store / 'notes/001-util-n1.md'
        note.parent.mkdir(exist_ok=True)
        note.write_text('# N\n\nstatus: blocking\n')
        self.assertIn('gates: pass (app)', self.run_sh('step', str(self.store), sha, 'util two'))
        job = next((self.store / 'steps/jobs').iterdir())
        (job / 'sentinel').write_text('x')
        steps_events = lambda: [l for l in (self.store / 'events.tsv').read_text().splitlines() if '\tstep\t' in l]
        snapshot = lambda: ((self.store / 'gates.tsv').read_text(), (self.store / 'progress/001-util.md').read_text(), len(steps_events()))
        self.assertIn('001-util-n1', self.run_sh('notes', str(self.store)))
        before = snapshot()
        out = self.run_sh('step', str(self.store), sha, 'util two again', '--resolves', 'n1')
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
        self.assertEqual((self.store / 'steps/001-util.tsv').read_text().splitlines(), rows)

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
        failed = [l for l in (self.store / 'events.tsv').read_text().splitlines() if '\tgate-fail\t' in l]
        self.assertEqual([l.rsplit(' ', 1)[1] for l in failed], [f'{job}/out.{n}' for n in (1, 2, 3)])
        for n in (1, 2, 3):
            self.assertEqual((job / f'out.{n}').read_text(), logs[n - 1])

    def test_an_earlier_sha_is_re_recorded_whatever_head_and_the_tree_are_now(self):
        # review 011 (i): record A, record B, then A again with --resolves n1 while A is not HEAD and the tree is dirty.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        note = self.store / 'notes/001-util-n1.md'
        note.parent.mkdir(exist_ok=True)
        note.write_text('# N\n\nstatus: blocking\n')
        shas = []
        for n in (2, 3):
            self.write('src/util.py', f'X = {n}\n')
            self.write('tests/test_app.py', f'x = {n}\n')
            shas.append(self.commit(f'step {n}'))
            self.assertIn('gates: pass', self.run_sh('step', str(self.store), shas[-1], f'step {n}'))
        self.write('src/util.py', 'X = 99\n')
        out = self.run_sh('step', str(self.store), shas[0], 'again', '--resolves', 'n1')
        self.assertIn(f'step {shas[0][:9]}: already recorded', out)
        rows = (self.store / 'steps/001-util.tsv').read_text().splitlines()
        self.assertEqual([r.split('\t')[3] for r in rows], ['n1', ''])

    def test_step_gates_see_the_run_and_role(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text().replace('fast = ["test ! -e FAIL"]',
                                                 'fast = ["printf \'%s|%s\' \\"$PSTACK_KITCHEN_RUN\\" \\"$PSTACK_KITCHEN_ROLE\\" > lane.txt"]'))
        self.write('src/util.py', 'X = 2\n')
        self.write('tests/test_app.py', 'x = 2\n')
        sha = self.commit('step 1')
        self.assertIn('gates: pass (app)', self.run_sh('step', str(self.store), sha, 'util two'))
        self.assertEqual((self.repo / 'lane.txt').read_text(), 'demo|sidekick')

    def test_a_resumed_units_commit_is_checked_against_its_own_diff(self):
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        sha = self.commit('made before the brief')
        self.dispatch(self.brief('001', 'resume', ['src/**', 'tests/**']))
        self.assertIn('gates: pass (app)', self.run_sh('step', str(self.store), sha, 'resumed'))
        self.write('notes.txt', 'x\n')
        out = self.run_sh('step', str(self.store), self.commit('unmapped'), 'notes')
        self.assertIn('gates: none ran', out)
        rows = [r.split('\t') for r in (self.store / 'gates.tsv').read_text().splitlines()]
        self.assertEqual([(r[3], r[4]) for r in rows], [('pass', 'app'), ('none', '')])

    def test_step_returns_while_checks_run_and_resumes(self):
        self.write('.agents/kitchen.toml', (self.repo / '.agents/kitchen.toml').read_text()
                   .replace('fast = ["test ! -e FAIL"]', 'fast = ["sleep 5"]'))
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
        self.assertIn('never end your turn on it', (self.store / 'briefs/001-util.md').read_text())
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        self.assertIn('gates: pass (app)', self.run_sh('step', str(self.store), sha, 'slow'))
        self.assertEqual(len((self.store / 'gates.tsv').read_text().splitlines()), 1)

    def await_job_line(self, text, bound=120):
        """Poll the step job's output for a line the detached gate prints; the bound only turns a hang into a failure."""
        import threading
        pause, deadline = threading.Event(), time.monotonic() + bound
        while time.monotonic() < deadline:
            if any(text in out.read_text(errors='replace') for out in (self.store / 'steps/jobs').glob('*/out')):
                return
            pause.wait(0.2)
        self.fail(f'the step job never printed {text!r}')

    def step_until_recorded(self, sha, summary, tries=600):
        """Run step again, a short slice at a time, until it records; the cap only turns a hang into a failure."""
        for _ in range(tries):
            r = subprocess.run([str(self.script), 'step', str(self.store), sha, summary], cwd=self.repo, env=self.env,
                               capture_output=True, text=True, check=False)
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
        self.held_slot_step('gate', 'kitchen: queued 1 of 1 for 1 machine gate slot',
                            'gates: queued 1 of 1 for 1 machine gate slot, behind gates from other kitchens')
        rows = (Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob('*/timings.tsv')
        waited = [float(line.split('\t')[6]) for f in rows for line in f.read_text().splitlines() if line.split('\t')[3] == 'sidekick']
        self.assertTrue(any(w > 0 for w in waited), waited)

    def test_a_step_waiting_for_the_heavy_slot_says_where_it_stands(self):
        self.write('.agents/kitchen.toml', (self.repo / '.agents/kitchen.toml').read_text()
                   .replace('fast = ["test ! -e FAIL"]', 'fast = ["test ! -e FAIL"]\nheavy = true'))
        self.commit('heavy gate')
        out = self.held_slot_step('heavy', 'kitchen: queued 1 of 1 for 1 heavy slot',
                                  'gates: queued 1 of 1 for 1 heavy slot, behind heavy gates from other kitchens')
        self.assertNotIn('machine gate slot', out)

    def test_quiet_wait_skips_unflagged_check_ins(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        checkins = lambda: sum(l.endswith('\tcheckin') for l in (self.store / 'events.tsv').read_text().splitlines())
        # Past --max, an unflagged check-in comes back, marked quiet.
        self.env['KITCHEN_QUIET_MAX_S'] = '0'
        self.assertIn('quiet: 0m with nothing flagged', self.run_sh('wait', str(self.store), '--timeout', '1', code=4))
        # A flagged one comes back at once, unmarked.
        self.env['KITCHEN_QUIET_MAX_S'] = '3600'
        self.write('elsewhere.txt', 'x\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn('outside scope: 1', out)
        self.assertNotIn('quiet:', out)
        (self.repo / 'elsewhere.txt').unlink()
        # The sidekick reports on the second wait: the master sleeps through the
        # unflagged check-in before it and wakes on the report.
        (self.fake / 'report-after').write_text(f'2 {self.store / "reports" / b.name} partial\n')
        before = checkins()
        out = self.run_sh('wait', str(self.store), '--timeout', '1')
        self.assertIn('report_status: partial', out)
        self.assertNotIn('check-in:', out)
        self.assertEqual(checkins(), before)

    def wakes(self):
        rows = (self.store / 'events.tsv').read_text().splitlines()[1:]
        return [r.split('\t')[5] for r in rows if r.split('\t')[2:4] == ['master', 'wake']]

    def test_a_quiet_wait_logs_one_wake_for_the_return_not_one_per_swallowed_check_in(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        (self.fake / 'report-after').write_text(f'3 {self.store / "reports" / b.name} partial\n')
        before = self.wakes()
        self.assertIn('report_status: partial', self.run_sh('wait', str(self.store), '--timeout', '1'))
        self.assertEqual(self.wakes()[len(before):], ['report:partial'])

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
        self.once_hook('wait', f'printf %s {shlex.quote(other)} >"{self.fake}/once/wait"\nchmod +x "{self.fake}/once/wait"\n')
        self.env['KITCHEN_QUIET_POLL_S'] = '0'
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertEqual(out.strip().splitlines()[-1], 'superseded: a newer wait owns this store; this one ends')
        self.assertNotIn('quiet:', out)
        self.assertEqual((self.store / 'wait.pid').read_text(), 'other\n')

    def test_a_failed_first_claim_leaves_the_quiet_wait_unowned_not_superseded(self):
        # The store lock is held for the first inner wait's claim (one try) and freed by the time it waits. The later inner waits must not read
        # the missing claim as a newer owner and end with `superseded:`; the report that lands on the third poll is returned.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        (self.fake / 'report-after').write_text(f'3 {self.store / "reports" / b.name} partial\n')
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
        (self.fake / 'report-after').write_text(f'3 {self.store / "reports" / b.name} partial\n')
        self.once_hook('wait', f'cp "{self.store}/wait.pid" "$FAKE/owner"\n')
        self.assertIn('report_status: partial', self.run_sh('wait', str(self.store), '--timeout', '1'))
        self.assertTrue((self.fake / 'owner').read_text().strip().isdigit())
        self.assertFalse((self.store / 'wait.pid').exists())

    def test_a_quiet_wait_that_a_newer_wait_supersedes_ends_without_a_quiet_line(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        self.once_hook('wait', f'printf "other\\n" >"{self.store}/wait.pid"\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '20000', code=4)
        self.assertEqual(out.strip(), 'superseded: a newer wait owns this store; this one ends')
        self.assertEqual((self.store / 'wait.pid').read_text(), 'other\n')

    def test_verify_on_a_report_with_no_status_prints_one_error_and_no_usage(self):
        # A10. unit_range dies inside no command substitution now, so the reason is the only line.
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.commit('util')
        report = self.store / 'reports' / b.name
        report.write_text('# Report\n\nhead: abc\n')
        self.assertEqual(self.run_sh('verify', str(self.store), '001', code=1), "kitchen.sh: unit 001's report has no status:\n")
        report.write_text('# Report\n\nstatus: bogus\nhead: abc\n')
        self.assertEqual(self.run_sh('verify', str(self.store), '001', code=1),
                         "kitchen.sh: unit 001's report is bogus, not done, partial, blocked, or failed\n")

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

    def test_steering_a_settled_sidekick_after_a_done_unit_advises_covers_in_the_kitchen(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'agents/demo-sidekick').write_text('idle pi\n')
        steer = self.store / 'steers/001-util-s1.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text('# Steer 001-s1: util\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n')
        out = self.run_sh('steer', str(self.store), str(steer), code=5)
        self.assertIn('write a fix brief and verify it with --covers 001', out)

    def test_a_quiet_wait_ending_at_max_logs_one_checkin_wake(self):
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        clock = self.fake / 'now'
        clock.write_text('1000\n')
        self.env.update({'KITCHEN_QUIET_MAX_S': '60', 'KITCHEN_QUIET_POLL_S': '0', 'KITCHEN_QUIET_CLOCK': str(clock)})
        # Each round runs one `agent wait`: the first two swallow a check-in, the third finds the clock a minute on.
        once = self.fake / 'once'
        once.mkdir(exist_ok=True)
        third = f'#!/usr/bin/env bash\nprintf "1060\\n" >{clock}\n'
        second = f'#!/usr/bin/env bash\nprintf %s {shlex.quote(third)} >{once}/wait\nchmod +x {once}/wait\n'
        first = f'#!/usr/bin/env bash\nprintf %s {shlex.quote(second)} >{once}/wait\nchmod +x {once}/wait\n'
        (once / 'wait').write_text(first)
        (once / 'wait').chmod(0o755)
        before = self.wakes()
        rounds = lambda: sum(l.startswith('agent wait demo-sidekick') for l in (self.fake / 'calls.log').read_text().splitlines())
        waits = rounds()
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4, timeout=120)
        self.assertGreaterEqual(rounds() - waits, 3)
        self.assertIn('quiet: 1m with nothing flagged', out)
        self.assertEqual(self.wakes()[len(before):], ['checkin'])

    def working_at_dispatch(self):
        """The sidekick is at work when dispatch first waits on it, so that wait ends in a check-in."""
        (self.fake / 'once').mkdir(exist_ok=True)
        hook = self.fake / 'once/wait'
        hook.write_text(f'#!/usr/bin/env bash\nprintf "working pi\\n" >"{self.fake}/agents/demo-sidekick"\n')
        hook.chmod(0o755)

    def test_a_dispatch_swallowing_its_first_check_in_logs_only_the_wait_that_returns(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.working_at_dispatch()
        (self.fake / 'report-after').write_text(f'3 {self.store / "reports" / b.name} partial\n')
        before = self.wakes()
        self.run_sh('dispatch', str(self.store), str(b), '--timeout', '1')
        self.assertEqual(self.wakes()[len(before):], ['report:partial'])

    def test_a_dispatch_returning_on_a_flagged_first_check_in_logs_one_checkin_wake(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.working_at_dispatch()
        self.write('elsewhere.txt', 'x\n')
        before = self.wakes()
        out = self.run_sh('dispatch', str(self.store), str(b), '--timeout', '1', code=4)
        self.assertIn('outside scope: 1', out)
        self.assertEqual(self.wakes()[len(before):], ['checkin'])

    def test_two_catches_in_one_second_are_both_kept_and_two_identical_wakes_are_one(self):
        rows = event_log((100, 'master catch - review'), (100, 'master catch - review'), (100, 'master wake 001-a checkin'), (100, 'master wake 001-a checkin'))
        self.assertEqual([r.split('\t')[2] for r in rows], ['catch', 'catch', 'wake'])

    def test_gates_see_the_step_base(self):
        self.write('.agents/kitchen.toml', (self.repo / '.agents/kitchen.toml').read_text()
                   .replace('fast = ["test ! -e FAIL"]', 'fast = ["git cat-file -e \\"$PSTACK_KITCHEN_STEP_BASE^{commit}\\""]'))
        base = self.commit('kitchen sees the base')
        self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']))
        self.write('src/util.py', 'X = 11\n')
        self.commit('one')
        self.write('tests/test_app.py', 'x = 11\n')
        sha = self.commit('two')
        self.assertIn('gates: pass (app)', self.run_sh('step', str(self.store), sha, 'two commits'))
        job = next((self.store / 'steps/jobs').iterdir())
        self.assertEqual((job / 'base').read_text().strip(), base)

    def test_a_squash_is_verified_against_what_it_carries(self):
        self.write('src/util.py', 'X = 21\n')
        self.write('tests/test_app.py', 'x = 21\n')
        self.commit('step one')
        self.write('src/util.py', 'X = 22\n')
        tip = self.commit('step two')
        squash = self.brief('001', 'squash', ['src/**', 'tests/**'], playbook='refactoring')
        self.dispatch(squash)
        self.git('reset', '--soft', 'HEAD~2')
        self.done(squash, self.commit('squashed'))
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}'), self.git('rev-parse', f'{tip}^{{tree}}'))
        out = self.run_sh('verify', str(self.store), '001')
        self.assertNotIn('gates only', out)
        self.assertIn('verifier demo-verifier', out)

    def test_a_blocked_unit_can_still_be_verified(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 31\n')
        self.write('tests/test_app.py', 'x = 31\n')
        head = self.commit('util')
        (self.store / 'reports' / b.name).write_text(f'# Report\n\nstatus: blocked\nhead: {head}\n')
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn("note: unit 001's report is blocked; checking the head it names", out)
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
        self.assertIn(f'range: {old_base}..', (self.store / 'verdicts/landing-v2-packet.md').read_text())

    def verify_block(self, brief, commands):
        text = brief.read_text()
        brief.write_text(re.sub(r'(## Verify\n\n```bash\n).*?(\n```)', lambda m: m[1] + commands + m[2], text,
                                count=1, flags=re.DOTALL))

    def reported(self):
        return [l for l in (self.store / 'events.tsv').read_text().splitlines() if '\tsidekick\treport\t' in l]

    def test_finish_refuses_done_until_the_briefs_verify_commands_pass_at_the_head(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, "# the targeted check\ngrep -q 'X = 2' src/util.py\ntest -f src/app.py \\\n  && true")
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.done(b, self.commit('wrong value'))
        report = str(self.store / 'reports' / b.name)
        out = self.run_sh('finish', str(self.store), report, code=2)
        self.assertIn('verify: FAIL (2 commands from the brief)', out)
        self.assertIn("exit 1", out)
        self.assertIn('the master did not get this report', out)
        self.assertEqual(self.reported(), [])
        # A wait does not show the master the refused report either.
        (self.fake / 'agents/demo-sidekick').write_text('idle pi\n')
        out = self.run_sh('wait', str(self.store), '--timeout', '1', code=4)
        self.assertIn("written but not ready: the brief's Verify commands have not passed", out)
        # Running finish again reruns a failed check rather than reusing it.
        self.assertIn('verify: FAIL', self.run_sh('finish', str(self.store), report, code=2))
        self.write('src/util.py', 'X = 2\n')
        self.done(b, self.commit('right value'))
        out = self.run_sh('finish', str(self.store), report)
        self.assertIn('verify: pass (2 commands from the brief)', out)
        self.assertIn("exit 0", out)
        self.assertEqual(len(self.reported()), 1)

    def core_finish(self, report):
        """finish as the Python core runs it for the kitchen, past kitchen.sh's own Verify run."""
        # the core's own finish: the kitchen's finish (kitchen_core.step) runs the Verify commands first and then this one, so the driver takes `finish` out of
        # the names the variant may replace
        driver = ('import sys; sys.path.insert(0, sys.argv[1]); from pstack_core import cli; '
                  "cli.OVERRIDABLE = tuple(n for n in cli.OVERRIDABLE if n != 'finish'); sys.exit(cli.main(sys.argv[2:]))")
        argv = [sys.executable, '-I', '-c', driver, str(root / 'skills/pstack-pair/scripts'), '--skill-root', str(skill), 'finish', str(self.store), str(report)]
        r = subprocess.run(argv, cwd=self.repo, env=self.env, capture_output=True, text=True, check=False)
        return r.returncode, r.stdout, r.stderr

    def test_finish_in_the_core_withholds_a_kitchen_done_report_until_its_verify_job_passed(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'true')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        head = self.commit('work')
        self.done(b, head)
        report = self.store / 'reports' / b.name
        module = self.kitchen_variant()
        job = Path(module.verify_job(str(self.store), str(b), head))
        code, out, err = self.core_finish(report)
        why = f"001-util: the brief's Verify commands have not passed at {head[:9]} (kitchen.sh finish runs them); rewrite the report at the unit's last commit (head:), then finish again\n"
        self.assertEqual((code, out, err), (7, '', 'kitchen.sh: ' + why))
        self.assertEqual(self.reported(), [])
        job.mkdir(parents=True)
        (job / 'rc').write_text('1\n')
        self.assertEqual(self.core_finish(report)[0], 7)
        (job / 'rc').write_text('0\n')
        code, out, err = self.core_finish(report)
        self.assertEqual((code, err), (0, ''))
        self.assertRegex(out, r'^(prompted master demo-master \(was idle\) with|master demo-master is \w+; left) ')
        self.assertIn(f'next: end the turn with the single line: pstack-kitchen REPORT {report}\n', out)
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
        self.assertIn(f"not the report's head: {head[:9]}", self.run_sh('finish', str(self.store), report, code=1))
        self.git('checkout', '-q', head)
        self.assertEqual(self.reported(), [])
        self.assertIn('verify: pass (1 commands from the brief)', self.run_sh('finish', str(self.store), report))

    def test_finish_with_no_verify_commands_and_a_slow_one(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.verify_block(b, 'none')
        self.dispatch(b)
        self.write('src/util.py', 'X = 2\n')
        self.done(b, self.commit('step'))
        report = str(self.store / 'reports' / b.name)
        self.assertIn('verify: pass (the brief names none)', self.run_sh('finish', str(self.store), report))
        b2 = self.brief('002', 'slow', ['src/**', 'tests/**'])
        self.verify_block(b2, 'sleep 3; printf "%s|%s" "$PSTACK_KITCHEN_ROLE" "$PSTACK_KITCHEN_RUN" >"$OUT"')
        self.env['OUT'] = str(Path(self.tmp.name) / 'lane.txt')
        self.dispatch(b2)
        self.write('src/util.py', 'X = 5\n')
        self.done(b2, self.commit('slow step'))
        report = str(self.store / 'reports' / b2.name)
        self.env['KITCHEN_STEP_SLICE_S'] = '1'
        out = self.run_sh('finish', str(self.store), report, code=4)
        self.assertIn("Verify commands are still running", out)
        self.assertIn(f'kitchen.sh finish {self.store} {report}', out)
        self.env['KITCHEN_STEP_SLICE_S'] = '30'
        self.assertIn('verify: pass (1 commands from the brief)', self.run_sh('finish', str(self.store), report))
        self.assertEqual(Path(self.env['OUT']).read_text(), 'sidekick|demo')

    def test_gates_mode_verifies_without_a_verifier(self):
        b = self.brief('001', 'docs', ['docs/**'])
        self.dispatch(b)
        self.write('docs/guide.md', '# Guide\nmore\n')
        head = self.commit('docs')
        self.done(b, head)
        out = self.run_sh('verify', str(self.store), '001')
        self.assertIn('status: clean (gates only', out)
        self.assertNotIn('agent start demo-verifier', (self.fake / 'calls.log').read_text())

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
        self.assertIn(f'scratch: {self.repo.resolve()}/.git/pstack-scratch/', packet)
        self.assertIn('gate app behavioral', packet)
        self.assertIn('## Acceptance', packet)

    def test_prime_verifier_stops_worker_before_removing_scratch(self):
        self.set_roster('[verifier]\nkind = "prime-agent"\nargs = ["--model", "devin/swe-2", "--daemon-socket", "/tmp/verifier.sock"]\n')
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('status: clean', out)
        calls = [json.loads(line) for line in (self.fake / 'prime.log').read_text().splitlines()]
        self.assertIn(['stop', 'live-1', '--json', '--socket', '/tmp/verifier.sock'], calls)
        self.assertEqual(json.loads((self.fake / 'prime-sessions.json').read_text()), [])
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])
        self.assertEqual(len(list((self.fake / 'prime-transcripts').iterdir())), 1)

    def test_prime_blocked_provider_verifier_retries_on_fallback(self):
        self.set_roster('[verifier]\nkind = "prime-agent"\n[verifier.fallback]\nkind = "devin"\n')
        _, out = self.verify_app_unit('prime-ratelimit', 0)
        self.assertIn('capacity issues', out)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertIn('status: clean', out)
        self.assertEqual(len(self.verifier_starts()), 2)
        self.assertEqual(json.loads((self.fake / 'prime-sessions.json').read_text()), [])

    def test_prime_verifier_failed_stop_preserves_scratch_and_prevents_fallback(self):
        self.set_roster('[verifier]\nkind = "prime-agent"\n[verifier.fallback]\nkind = "devin"\n')
        (self.fake / 'prime-stop-fail').touch()
        self.verify_app_unit('prime-ratelimit', 5)
        self.assertEqual(len(self.verifier_starts()), 1)
        verifying = self.state()['verifying']
        self.assertTrue((self.store / 'scratch' / verifying['scratch']).is_dir())
        self.assertTrue((self.fake / 'panes' / self.state()['verifier']['pane_id']).exists())
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
        self.assertIn('its pane and scratch stay for inspection (verify-wait can resume cleanup once its session is reported)', out)
        self.assertTrue((self.store / 'scratch' / self.state()['verifying']['scratch']).is_dir())
        self.assertTrue((self.fake / 'panes' / self.state()['verifier']['pane_id']).exists())

    def test_a_prime_verifier_that_reports_no_session_keeps_its_pane_scratch_and_the_record_of_how_it_was_launched(self):
        self.set_roster('[verifier]\nkind = "prime-agent"\n')
        (self.fake / 'prime-no-session').touch()
        _, out = self.verify_app_unit('clean', 5)
        self.assertIn('Prime verifier did not report its session; its pane and scratch stay for inspection', out)
        state = self.state()
        scratch = self.store / 'scratch' / state['verifying']['scratch']
        self.assertTrue(scratch.is_dir())
        self.assertTrue((self.fake / 'panes' / state['verifier']['pane_id']).exists())
        # the verifier record says where the daemon was launched and that its session is not stopped; the open verification, how it was asked for
        self.assertEqual((state['verifier']['launch_cwd'], state['verifier']['session_stopped'], state['verifier']['session']), (str(scratch.resolve()), False, None))
        verifying = state['verifying']
        self.assertEqual((verifying['units'], verifying['argv'], verifying['entry']['kind'], verifying['entry']['source']), ('001', ['001'], 'prime-agent', 'roster'))

    def test_a_batch_over_review_max_batch_diff_is_refused_but_a_fix_verified_with_its_unit_is_not(self):
        text = (self.repo / '.agents/kitchen.toml').read_text()
        self.write('.agents/kitchen.toml', text.replace('[review]\n', '[review]\nmax_batch_diff = 1\n'))
        self.commit('cap the batch')
        self.three_units()
        out = self.run_sh('verify', str(self.store), '001', '002', code=2)
        self.assertRegex(out, r'batch of \d+ diff lines is over review.max_batch_diff; verify each unit')
        self.assertNotIn('verifying', self.state())
        self.assertNotIn('verifier demo-verifier', out)
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        self.assertIn('status: clean', out)

    def test_the_pairs_permission_report_and_notify_work_through_kitchen_sh(self):
        self.assertEqual(self.run_sh('permission').strip(), 'auto')
        self.assertEqual(self.run_sh('report', str(self.store)).strip(), f'{self.store}/reports/000-ready.md')
        newest = self.store / 'reports/001-util.md'
        newest.write_text('# R\n\nstatus: done\nhead: x\n')
        self.assertEqual(self.run_sh('report', str(self.store)).strip(), str(newest))
        self.assertEqual(self.run_sh('report', str(self.store), '001').strip(), str(newest))
        self.assertIn('no report found', self.run_sh('report', str(self.store), '007', code=4))
        out = self.run_sh('notify', str(self.store), str(newest))
        self.assertEqual(out.strip(), f'prompted master demo-master (was idle) with REPORT {newest}')
        self.assertIn(f'agent prompt demo-master pstack-kitchen REPORT {newest}', (self.fake / 'calls.log').read_text())
        self.assertIn(f'file not found: {self.store}/reports/none.md', self.run_sh('notify', str(self.store), f'{self.store}/reports/none.md', code=1))

    def test_the_consultants_new_consult_consult_and_advice_work_through_kitchen_sh(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.run_sh('consultant', str(self.store), '--reason', 'contract', '--kind', 'codex', code=4)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.assertIn('--kind must be design, finding, objection, review, or glance', self.run_sh('new-consult', str(self.store), '001', code=1))
        consult = Path(self.run_sh('new-consult', str(self.store), '001', '--kind', 'finding').strip())
        self.assertEqual(consult, self.store / 'consults/001-util-c1.md')
        self.assertIn('still has unfilled placeholders', self.run_sh('consult', str(self.store), str(consult), code=1))
        consult.write_text(re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL))
        self.assertIn('no advice found', self.run_sh('advice', str(self.store), code=4))
        out = self.run_sh('consult', str(self.store), str(consult), '--timeout', '1500', code=4)
        self.assertIn('advice: missing', out)
        self.assertIn(f'agent prompt demo-consultant pstack-kitchen CONSULT {consult} --wait --timeout 1500', (self.fake / 'calls.log').read_text())
        advice = self.store / 'advice/001-util-c1.md'
        advice.parent.mkdir(exist_ok=True)
        advice.write_text('# A\n\nstatus: answered\n\nadvice here\n')
        out = self.run_sh('consult', str(self.store), str(consult), '--timeout', '1500')
        self.assertIn(f'advice: {advice}\nadvice_status: answered', out)
        self.assertEqual(self.run_sh('advice', str(self.store)).strip(), str(advice))
        self.assertEqual(self.run_sh('new-consult', str(self.store), '001', '--kind', 'glance').strip(), str(self.store / 'consults/001-util-c2.md'))
        self.assertIn('unit 001 already had its glance; send a finding consult, or pass --force', self.run_sh('new-consult', str(self.store), '001', '--kind', 'glance', code=7))

    def test_consult_flags_are_checked_before_any_state_changes(self):
        self.brief('001', 'util', ['src/**', 'tests/**'])
        self.assertIn('--kind needs a value', self.run_sh('new-consult', str(self.store), '001', '--kind', code=1))
        self.assertIn('unknown option --bogus', self.run_sh('new-consult', str(self.store), '001', '--bogus', code=1))
        self.assertFalse((self.store / 'consults').exists() and list((self.store / 'consults').iterdir()))
        consult = Path(self.run_sh('new-consult', str(self.store), '001', '--kind', 'finding').strip())
        consult.write_text(re.sub(r'\{\{[^}]*\}\}', 'x', consult.read_text(), flags=re.DOTALL))
        before = consult.read_text()
        self.assertIn('--timeout needs a value', self.run_sh('consult', str(self.store), str(consult), '--timeout', code=1))
        self.assertIn('--every must be a whole number of minutes', self.run_sh('consult', str(self.store), str(consult), '--every', '2x', code=1))
        self.assertEqual(consult.read_text(), before)
        self.assertNotIn('consult', self.state())
        self.assertNotIn('CONSULT', (self.fake / 'calls.log').read_text())

    def test_scratch_lives_inside_the_repo_git_dir_and_leaves_no_trace(self):
        path = Path(self.run_sh('scratch', str(self.store), '001-x-review').splitlines()[0])
        git_dir = self.repo / '.git' / 'pstack-scratch'
        self.assertEqual(path.parent.parent, git_dir)
        self.assertEqual((self.store / 'scratch/001-x-review').resolve(), path.resolve())
        self.assertTrue((path / 'calc.py').exists() or (path / 'src/app.py').exists())
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.run_sh('scratch', str(self.store), '001-x-review', '--remove')
        self.assertFalse(path.exists())
        self.assertFalse((self.store / 'scratch/001-x-review').is_symlink())
        self.assertEqual(list(git_dir.iterdir()), [])

    def test_a_trust_dialog_is_declined_not_answered(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        (self.fake / 'trust-dialog').touch()
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', '--kind', 'claude', code=5)
        self.assertIn('claude asks whether to trust', out)
        self.assertIn('decision is yours', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertRegex(calls, r'pane send-keys p\d+ esc')
        self.assertNotRegex(calls, r'send-keys \S+ enter')
        pane = re.search(r'pane send-keys (p\d+) esc', calls).group(1)
        self.assertIn(f'pane close {pane}', calls)
        self.assertNotIn('escalate', (self.store / 'events.tsv').read_text())
        self.assertEqual(self.state().get('escalations', []), [])

    def test_spawned_claude_agents_load_no_mcp_servers(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        # The fake writes no advice/000-ready.md, so the bootstrap ends 4; the start line is what matters.
        self.run_sh('consultant', str(self.store), '--reason', 'contract', '--kind', 'claude', code=4)
        start = [line for line in (self.fake / 'calls.log').read_text().splitlines()
                 if line.startswith('agent start demo-consultant')][-1]
        self.assertIn('--strict-mcp-config --mcp-config', start)
        self.assertIn('references/no-mcp.json', start)
        self.set_roster('[verifier.escalated]\nkind = "claude"\nargs = ["--model", "m", "--mcp-config", "mine.json"]\n')
        out = self.verify_landing()
        (vstart,) = self.verifier_starts()
        self.assertIn('--mcp-config mine.json', vstart)
        self.assertNotIn('--strict-mcp-config', vstart)
        self.assertIn('verifier demo-verifier (claude --model m)', out)

    def test_spawned_codex_agents_turn_off_every_mcp_server_codex_reports(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        (self.fake / 'codex-mcp.json').write_text(json.dumps([
            {'name': 'chrome-devtools', 'enabled': True}, {'name': 'railway', 'enabled': True},
            {'name': 'off-already', 'enabled': False}, {'name': 'has.dot', 'enabled': True}]))
        self.run_sh('consultant', str(self.store), '--reason', 'contract', '--kind', 'codex', code=4)
        calls = (self.fake / 'calls.log').read_text()
        start = [line for line in calls.splitlines() if line.startswith('agent start demo-consultant')][-1]
        self.assertIn('--disable plugins -c mcp_servers.chrome-devtools.enabled=false -c mcp_servers.railway.enabled=false', start)
        self.assertNotIn('off-already', start)
        self.assertNotIn('has.dot', start)
        self.assertIn(f'codex mcp list --json (cwd {self.repo})', calls)
        self.set_roster('[verifier.escalated]\nkind = "codex"\nargs = ["-c", "mcp_servers.railway.enabled=true"]\n')
        self.verify_landing()
        (vstart,) = self.verifier_starts()
        self.assertIn('mcp_servers.railway.enabled=true', vstart)
        self.assertNotIn('chrome-devtools', vstart)

    def filled_handoff(self):
        self.run_sh('new-handoff', str(self.store))
        handoff = self.store / 'session-handoff.md'
        text = re.sub(r'\{\{.*?\}\}', '', handoff.read_text(), flags=re.S)
        handoff.write_text(text.replace('## Goal\n', '## Goal\n\nShip util.\n').replace('## Next\n', '## Next\n\n1. kitchen.sh dispatch 002\n'))
        (self.fake / 'session-ids').mkdir(exist_ok=True)
        (self.fake / 'session-ids/demo-master').write_text('m-gen1 /work\n')
        return handoff

    def new_master_pane(self):
        return self.state()['master']['pane_id']

    def test_new_handoff_lists_the_units_and_rotate_master_hands_the_run_to_a_new_master_beside_it(self):
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
        self.assertIn('still has unfilled placeholders', self.run_sh('rotate-master', str(self.store), code=1))
        handoff = self.filled_handoff()
        out = self.run_sh('rotate-master', str(self.store))
        self.assertIn('handoff recorded: ' + str(self.store / 'sessions/master-1-handoff.md') + ' (generation 1, session m-gen1)', out)
        self.assertIn('claude --resume m-gen1', out)
        state = self.state()['master']
        new_pane = state['pane_id']
        self.assertNotEqual(new_pane, 'p0')
        self.assertEqual((state['name'], state['generation'], state['units_since_handoff']), ('demo-master-g2', 2, 0))
        self.assertEqual(state['previous'], {'name': 'demo-master', 'pane_id': 'p0', 'session': 'm-gen1'})
        self.assertEqual([s['session'] for s in state['sessions']], ['m-gen1'])
        self.assertIn(f'next master: demo-master-g2 (generation 2) in {new_pane}, remote control on', out)
        bootstrap = (self.fake / 'master-bootstrap').read_text()
        self.assertIn(f'pstack-kitchen MASTER {self.store} (generation 2). Run ', bootstrap)
        self.assertIn(f'kitchen.sh take-master {self.store} first, then read {self.store}/session-handoff.md.', bootstrap)
        calls = (self.fake / 'calls.log').read_text()
        self.assertNotIn('agent prompt demo-master /exit', calls)
        start = [line for line in calls.splitlines() if line.startswith('agent start demo-master-g2')][-1]
        self.assertIn(f'--kind claude --pane {new_pane}', start)
        self.assertIn('--strict-mcp-config --mcp-config', start)
        self.assertIn('--permission-mode', start)
        self.assertIn('--remote-control demo-master-g2', start)
        self.assertTrue((self.fake / 'agents/demo-master').exists(), 'the old master stays until take-master')
        self.assertIn('a hand-over is open', self.run_sh('rotate-master', str(self.store), code=5))
        self.assertIn(f'take-master runs from the new master\'s pane {new_pane}', self.run_sh('take-master', str(self.store), code=5))
        self.env['HERDR_PANE_ID'] = new_pane
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        out = self.run_sh('take-master', str(self.store))
        self.assertIn('warning: unreviewed: unit 001 cites no joo run; review its range with joo', out)
        self.assertIn('closed the old master demo-master (p0)', out)
        self.assertIn(f'master: demo-master in {new_pane} (generation 2)', out)
        self.assertFalse((self.fake / 'panes/p0').exists())
        self.assertEqual((self.fake / 'agents/demo-master').read_text(), 'idle claude\n')
        self.assertFalse((self.fake / 'agents/demo-master-g2').exists())
        state = self.state()['master']
        self.assertEqual((state['name'], state['pane_id']), ('demo-master', new_pane))
        self.assertNotIn('previous', state)
        self.assertIn(f'no hand-over open; demo-master is the master in {new_pane}', self.run_sh('take-master', str(self.store)))
        events = (self.store / 'events.tsv').read_text()
        for row in ('\tmaster\thandoff\t-\tgeneration:1', '\tmaster\tspawn\t-\tclaude:generation:2', '\tmaster\ttakeover\t-\tgeneration:2'):
            self.assertIn(row, events)
        self.assertEqual((self.store / 'sessions/master-1-handoff.md').read_text(), handoff.read_text())

    def test_a_next_master_that_does_not_start_leaves_the_old_one_in_charge(self):
        self.filled_handoff()
        (self.fake / 'trust-dialog').touch()
        out = self.run_sh('rotate-master', str(self.store), code=5)
        self.assertIn('asks whether to trust', out)
        state = self.state()['master']
        self.assertEqual((state['name'], state['pane_id'], state.get('generation', 1)), ('demo-master', 'p0', 1))
        self.assertNotIn('previous', state)
        self.assertNotIn('sessions', state)
        self.assertFalse((self.store / 'sessions/master-1-handoff.md').exists())

    def test_rotating_again_after_an_abandoned_rotation_keeps_the_generation(self):
        self.filled_handoff()
        # The in-place restart recorded the session, then never started generation 2.
        state = self.state()
        state['master'].update(generation=2, sessions=[{'generation': 1, 'session': 'm-gen1', 'handoff': 'old', 'at': 't'}])
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.env['KITCHEN_MASTER_REMOTE_CONTROL'] = '0'
        out = self.run_sh('rotate-master', str(self.store))
        self.assertIn('(generation 1, session m-gen1)', out)
        self.assertNotIn('remote control', out.split('next master:')[1].splitlines()[0])
        master = self.state()['master']
        self.assertEqual((master['name'], master['generation']), ('demo-master-g2', 2))
        self.assertEqual([(s['generation'], s['session']) for s in master['sessions']], [(1, 'm-gen1')])
        start = [line for line in (self.fake / 'calls.log').read_text().splitlines() if line.startswith('agent start demo-master-g2')][-1]
        self.assertNotIn('--remote-control', start)

    def test_a_required_review_engine_holds_land_check_until_each_review_note_cites_a_run(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '001')
        review = self.store / 'reviews/001-util.md'
        review.write_text('verdict: accept\n')
        self.assertIn('land-check: pass (review engine: none)', self.run_sh('land-check', str(self.store)))
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        for note in ('verdict: accept\n', 'verdict: accept\nengine: joo\n', 'verdict: accept\nengine: codex r1\n'):
            review.write_text(note)
            out = self.run_sh('land-check', str(self.store), code=2)
            self.assertIn(f'unreviewed: unit 001 cites no joo run; review its range with joo and add "engine: joo <run id or artifact path>" to {review}', out)
        review.write_text('verdict: accept\nhead: x\nengine: joo /tmp/joo-001.json\n')
        self.assertIn('land-check: pass (review engine: joo)', self.run_sh('land-check', str(self.store)))

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
        self.assertIn('land-check: pass (review engine: joo)', self.run_sh('land-check', str(self.store)))
        review.write_text('verdict: accept\nengine: skipped (deletion-only)\n')
        self.assertIn('unreviewed: unit 001 cites no joo run', self.run_sh('land-check', str(self.store), code=2))

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
        review.write_text('verdict: accept\nengine: skipped (deletion-only, proof: git diff --numstat)\n')
        return review

    def test_a_rename_cannot_use_the_deletion_only_skip(self):
        review = self.skipped_unit(lambda: self.write('src/old.py', 'O = 1\n'), lambda: self.git('mv', 'src/old.py', 'src/new.py'))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_mode_change_cannot_use_the_deletion_only_skip(self):
        review = self.skipped_unit(lambda: self.write('src/run.sh', '#!/bin/sh\n'), lambda: (self.repo / 'src/run.sh').chmod(0o755))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_modified_binary_with_a_non_ascii_name_cannot_use_the_deletion_only_skip(self):
        def lay_out():
            (self.repo / 'assets').mkdir(exist_ok=True)
            (self.repo / 'assets/caf\u00e9.png').write_bytes(bytes(range(256)))

        self.skipped_unit(lay_out, lambda: (self.repo / 'assets/caf\u00e9.png').write_bytes(bytes(range(255, -1, -1))))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_path_with_a_tab_is_read_exactly_by_the_deletion_only_check(self):
        name = 'assets/we\tird.bin'

        def lay_out():
            (self.repo / 'assets').mkdir(exist_ok=True)
            (self.repo / name).write_bytes(bytes(range(256)))

        self.skipped_unit(lay_out, lambda: (self.repo / name).write_bytes(bytes(range(255, -1, -1))))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_deleted_path_with_a_tab_can_use_the_deletion_only_skip(self):
        name = 'assets/we\tird.bin'

        def lay_out():
            (self.repo / 'assets').mkdir(exist_ok=True)
            (self.repo / name).write_bytes(bytes(range(256)))

        self.skipped_unit(lay_out, lambda: self.git('rm', '-q', name))
        self.assertIn('land-check: pass (review engine: joo)', self.run_sh('land-check', str(self.store)))

    def test_a_dispatch_head_that_no_longer_resolves_refuses_the_deletion_only_skip(self):
        self.skipped_unit(lambda: self.write('src/old.py', 'O = 1\n'), lambda: self.git('rm', '-q', 'src/old.py'))
        state = self.state()
        state['heads']['001-util'] = 'deadbeef' * 5
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_mode_change_with_deleted_lines_cannot_use_the_deletion_only_skip(self):
        self.skipped_unit(lambda: self.write('src/run.sh', '#!/bin/sh\necho a\necho b\n'),
                          lambda: (self.write('src/run.sh', '#!/bin/sh\necho a\n'), (self.repo / 'src/run.sh').chmod(0o755)))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_modified_file_whose_name_begins_with_a_tab_and_gains_a_line_cannot_use_the_skip(self):
        name = '\ttab.py'
        self.skipped_unit(lambda: self.write(name, 'A = 1\n'), lambda: self.write(name, 'A = 1\nB = 2\n'))
        self.assertIn('skipped is only for a deletion-only range', self.run_sh('land-check', str(self.store), code=2))

    def test_a_file_whose_name_ends_with_a_tab_and_loses_a_line_can_use_the_skip(self):
        name = 'src/tab\t'
        self.skipped_unit(lambda: self.write(name, 'A = 1\nB = 2\n'), lambda: self.write(name, 'A = 1\n'))
        self.assertIn('land-check: pass (review engine: joo)', self.run_sh('land-check', str(self.store)))

    def test_a_deletion_that_removes_lines_from_a_text_file_can_use_the_skip(self):
        self.skipped_unit(lambda: self.write('src/lines.py', 'A = 1\nB = 2\nC = 3\n'), lambda: self.write('src/lines.py', 'A = 1\nC = 3\n'))
        self.assertIn('land-check: pass (review engine: joo)', self.run_sh('land-check', str(self.store)))

    def test_a_binary_deletion_can_use_the_deletion_only_skip(self):
        def lay_out():
            (self.repo / 'src').mkdir(exist_ok=True)
            (self.repo / 'src/blob.bin').write_bytes(bytes(range(256)))
        review = self.skipped_unit(lay_out, lambda: self.git('rm', '-q', 'src/blob.bin'))
        self.assertIn('land-check: pass (review engine: joo)', self.run_sh('land-check', str(self.store)))

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
        review.write_text('verdict: accept\nengine: skipped (deletion-only, proof: none)\n')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(f'unreviewed: unit 001 cites no joo run; review its range with joo and add "engine: joo <run id or artifact path>" to {review}; skipped is only for a deletion-only range', out)

    def test_a_clean_verify_warns_of_a_review_note_without_the_required_engine_run(self):
        self.three_units()
        review = self.store / 'reviews/001-u1.md'
        review.write_text('verdict: accept\n')
        out = self.run_sh('verify', str(self.store), '002')
        self.assertNotIn('unreviewed', out)
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + 'engine = "joo"\n')
        out = self.run_sh('verify', str(self.store), '003')
        self.assertIn(f'warning: unreviewed: unit 001 cites no joo run; review its range with joo and add "engine: joo <run id or artifact path>" to {review}', out)
        review.write_text('verdict: accept\nengine: joo /tmp/joo-001.json\n')
        self.assertNotIn('unreviewed', self.run_sh('verify', str(self.store), '002', '003'))

    def test_verify_nudges_a_handoff_after_the_configured_number_of_units(self):
        self.env['KITCHEN_MASTER_ROTATE_UNITS'] = '2'
        heads = self.three_units()
        out = self.run_sh('verify', str(self.store), '001')
        self.assertNotIn('since your last handoff', out)
        out = self.run_sh('verify', str(self.store), '002')
        self.assertIn('master: 2 units verified since your last handoff; rotate at this boundary: kitchen.sh new-handoff', out)
        self.assertEqual(self.state()['master']['units_since_handoff'], 2)
        # Once at the threshold and at each multiple of it, not in between.
        out = self.run_sh('verify', str(self.store), '003')
        self.assertNotIn('since your last handoff', out)
        b = self.brief('004', 'u4', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 44\n')
        self.write('tests/test_app.py', 'x = 44\n')
        self.done(b, self.commit('u4'))
        out = self.run_sh('verify', str(self.store), '004')
        self.assertIn('master: 4 units verified since your last handoff', out)

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
            '[consultant.fallback]\nkind = "claude"\nargs = ["--model", "claude-fable-5-1"]\n')

    def test_a_consultant_whose_muse_preflight_fails_starts_the_roster_fallback(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        (self.fake / 'muse-fail').write_text('Usage limit reached. Wait for usage to reset at 18:00.\n')
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertIn('failover: consultant muse -> claude (preflight: Usage limit reached', out)
        start = [line for line in (self.fake / 'calls.log').read_text().splitlines()
                 if line.startswith('agent start demo-consultant')][-1]
        self.assertIn('--kind claude', start)
        self.assertIn('--model claude-fable-5-1', start)
        failover = self.state()['consultant']['failovers'][0]
        self.assertEqual((failover['from'], failover['to']), ('muse', 'claude'))
        self.assertIn('\tmaster\tfailover\t-\tconsultant:muse:claude', (self.store / 'events.tsv').read_text())

    def test_a_consultant_whose_muse_preflight_passes_starts_muse(self):
        (self.fake / 'agents/demo-consultant').unlink(missing_ok=True)
        self.muse_consultant_roster()
        out = self.run_sh('consultant', str(self.store), '--reason', 'contract', code=4)
        self.assertNotIn('failover', out)
        start = [line for line in (self.fake / 'calls.log').read_text().splitlines()
                 if line.startswith('agent start demo-consultant')][-1]
        self.assertIn('--kind muse', start)
        self.assertIn('--trust-workspace --model muse-wonder-full', start)
        self.assertIn('exec --model muse-wonder-full Reply with exactly: OK', (self.fake / 'muse.log').read_text())

    def test_a_muse_verifier_starts_trusted_without_approvals(self):
        self.set_roster('[verifier]\nkind = "muse"\nargs = ["--model", "muse-wonder"]\n')
        _, out = self.verify_app_unit('clean', 0)
        self.assertIn('status: clean', out)
        (vstart,) = self.verifier_starts()
        self.assertIn('--kind muse', vstart)
        self.assertIn('--approval-mode never --disable-sandbox --trust-workspace --model muse-wonder', vstart)
        self.assertNotIn('mcp', vstart)

    def test_close_ends_the_roles_and_closes_their_panes(self):
        state = self.state()
        state['verifying'] = {'verdict': 'x', 'units': '001'}
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.assertIn('a verification is open', self.run_sh('close', str(self.store), code=5))
        del state['verifying']
        (self.store / 'pair.json').write_text(json.dumps(state))
        out = self.run_sh('close', str(self.store))
        self.assertIn('closed sidekick demo-sidekick', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('agent prompt demo-sidekick /quit', calls)
        self.assertRegex(calls, r'pane close p\d')
        self.assertFalse((self.fake / 'agents/demo-sidekick').exists())
        self.assertTrue(self.state()['sidekick']['closed'])
        self.assertIn('close\tsidekick:pi', (self.store / 'events.tsv').read_text().replace('\t-\t', '\t'))
        self.assertEqual(self.run_sh('close', str(self.store)), '')

    def test_init_refuses_under_the_verifier_role_before_any_herdr_call_or_store_write(self):
        refusal = 'init refuses under PSTACK_KITCHEN_ROLE=verifier: it renames the calling pane, and the kitchen would lose its verifier'
        calls = self.fake / 'calls.log'
        before = calls.read_text()
        self.env['PSTACK_KITCHEN_ROLE'] = 'verifier'
        r = subprocess.run([str(self.script), 'init', 'other'], cwd=self.repo, env=self.env, capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (2, '', f'kitchen.sh: {refusal}\n'))
        self.assertEqual(calls.read_text(), before)
        self.assertEqual([p.name for p in self.store.parent.iterdir()], [self.store.name])
        self.env['PSTACK_KITCHEN_ROLE'] = 'sidekick'
        self.assertIn('other', self.run_sh('init', 'other'))

    def test_verify_prompt_left_typed_is_submitted(self):
        _, out = self.verify_app_unit('clean', 0, stall=True)
        self.assertIn('status: clean', out)
        self.assertIn('send-keys demo-verifier enter', (self.fake / 'calls.log').read_text())

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
        return [l for l in (self.fake / 'calls.log').read_text().splitlines() if l.startswith(call)]

    def test_the_verifier_pane_split_carries_the_role_and_run_but_the_sidekicks_does_not(self):
        spawn_splits = self.pane_calls('pane split')
        self.assertTrue(spawn_splits and not any('--env' in l for l in spawn_splits), spawn_splits)
        self.verify_app_unit('clean', 0)
        added = self.pane_calls('pane split')[len(spawn_splits):]
        self.assertEqual([l.endswith(self.ROLE_ENV) for l in added], [True], added)

    def test_the_verifier_tab_carries_the_role_and_run_under_tab_placement(self):
        state = self.state()
        state['placement'] = 'tab'
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.verify_app_unit('clean', 0)
        tabs = self.pane_calls('tab create')
        self.assertEqual([l.endswith(self.ROLE_ENV) for l in tabs], [True], tabs)

    def test_a_fix_is_verified_with_the_unit_it_fixes(self):
        first = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(first)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.commit('first')
        (self.store / 'reports' / first.name).write_text('# Report\n\nstatus: partial\nhead: x\n')
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
        self.write('.agents/LESSONS.md', '# Kitchen lessons\n\n'
                   '## L001: Every write to the store clears the cache.\n\nstatus: active\nkind: reject\nclass: verify\n'
                   'layer: verify\narea: src\nevidence:\n- 2026-10-01 run-a 001: stale cache\n\n'
                   '## L002: Callers of a renamed function are updated.\n\nstatus: active\nkind: catch\nclass: review\n'
                   'layer: review\narea: src\nevidence:\n- 2026-10-01 run-a 002: missed a caller\n\n'
                   '## L003: Docs only.\n\nstatus: active\nkind: steer\nclass: brief\nlayer: brief\narea: docs\n'
                   'evidence:\n- 2026-10-01 run-a 003: x\n')
        self.commit('lessons')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        text = b.read_text()
        self.assertIn('- L001 (reject verify, layer verify, in src): Every write to the store clears the cache.\n', text)
        self.assertIn('- L002 (catch review, layer review, in src)', text)
        self.assertNotIn('L003', text)
        self.write('src/util.py', 'X = 5\n')
        self.write('tests/test_app.py', 'x = 5\n')
        self.done(b, self.commit('util'))
        self.assertIn('status: clean', self.run_sh('verify', str(self.store), '001'))
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        lessons = packet[packet.index('## Lessons'):packet.index('## Changed')]
        self.assertRegex(lessons, r'Acceptance lines too[^-]*- L001: Every write')
        self.assertRegex(lessons, r'Only after your own pass[^-]*- L002: Callers')
        self.run_sh('catch', str(self.store), 'review', 'a caller still used the old name', '001')
        self.run_sh('retro', str(self.store))
        head, rows = self.ledger()
        self.assertEqual(sorted((r['class'], r['detail'], r['unit']) for r in rows if r['kind'] == 'lesson'),
                         [('held', 'L001', '001'), ('hit', 'L002', '001')])
        x = {l['id']: l for l in json.loads(subprocess.run(
            [sys.executable, str(script), '--repo', str(self.repo), '--json', 'lessons'], env=self.env,
            capture_output=True, text=True, check=True).stdout)['lessons']}
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
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001', '002', code=2)
        self.assertIn(f"unit 003's head {heads[2][:9]} is not in the checked-out head {heads[1][:9]} (unit 002)", out)
        self.assertIn('name the unit that carries all the others last', out)
        self.assertNotIn('verifier demo-verifier', out)
        self.assertNotIn('verifying', self.state())

    def test_the_head_check_runs_before_a_gates_only_verification(self):
        heads = self.three_units(scope=('docs/**',))
        out = self.run_sh('verify', str(self.store), '002', '--covers', '003', code=2)
        self.assertIn(f"unit 003's head {heads[2][:9]} is not in the checked-out head {heads[1][:9]} (unit 002)", out)
        self.assertNotIn('gates only', out)

    def test_a_covered_unit_without_a_usable_head_passes_with_a_note(self):
        self.three_units()
        (self.store / 'reports/001-u1.md').write_text('# Report\n\nstatus: partial\nhead: x\n')
        (self.store / 'reports/002-u2.md').unlink()
        out = self.run_sh('verify', str(self.store), '003', '--covers', '001,002')
        self.assertIn('note: unit 001 has no usable head; its inclusion was not checked', out)
        self.assertIn('note: unit 002 has no usable head; its inclusion was not checked', out)

    def test_covers_rejects_a_value_that_is_not_a_unit_number(self):
        self.three_units()
        self.assertIn("--covers takes unit numbers (NNN)", self.run_sh('verify', str(self.store), '003', '--covers', '001,x', code=1))

    def test_verify_returns_at_the_interval_and_resumes(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 8\n')
        self.write('tests/test_app.py', 'x = 8\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertIn('verifying: units 001 by pi', out)
        self.assertIn(f'next: other work, then kitchen.sh verify {self.store} --wait', out)
        self.assertIn('a verification is open', self.run_sh('verify', str(self.store), '001', code=5))
        open_ = json.loads((self.store / 'pair.json').read_text())['verifying']
        Path(open_['verdict']).write_text('status: clean\nunits: 001\n\n## Evidence\n\n```\n$ true\n```\n')
        out = self.run_sh('verify', str(self.store), '--wait')
        self.assertIn('status: clean', out)
        wakes = [r.split('\t')[5] for r in (self.store / 'events.tsv').read_text().splitlines() if '\tmaster\twake\t' in r]
        self.assertEqual([w for w in wakes if w.startswith('verify:')], ['verify:progress', 'verify:clean'])
        self.assertNotIn('verifying', json.loads((self.store / 'pair.json').read_text()))
        self.assertEqual(list((self.store / 'scratch').iterdir()), [])
        self.assertIn('no verification is open', self.run_sh('verify', str(self.store), '--wait', code=5))

    def test_until_verdict_returns_once_on_the_verdict_with_no_interval_return(self):
        # The clock jumps 20 minutes at the first status poll: past the default 9-minute interval, inside the 60-minute deadline.
        shim = Path(self.tmp.name) / 'clock'
        shim.mkdir()
        (shim / 'date').write_text(f'#!/bin/bash\nif [ "$1" = +%s ] && [ -e "$FAKE/time-jump" ]; then echo $(( $({shutil.which("date")} +%s) + 1200 )); '
                                   f'else exec {shutil.which("date")} "$@"; fi\n')
        (shim / 'date').chmod(0o755)
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0', 'KITCHEN_VERIFY_IDLE_S': '100000',
                         'PATH': f'{shim}:{self.env["PATH"]}'})
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 11\n')
        self.write('tests/test_app.py', 'x = 11\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4)
        verdict = Path(self.state()['verifying']['verdict'])
        # The first status poll starts the jump; the verdict lands at the third.
        hook = self.fake / 'once/get'
        hook.parent.mkdir(exist_ok=True)
        polls = self.fake / 'polls'
        hook.write_text(
            "#!/bin/bash\n"
            f"echo >>{polls}\n"
            f"touch {self.fake}/time-jump\n"
            f"if [ \"$(wc -l <{polls})\" -ge 3 ]; then printf '# V\\n\\nstatus: clean\\nunits: 001\\n\\n## Evidence\\n\\n```\\n$ true\\n```\\n' >{verdict}\n"
            f"else cp \"$0\" {hook}; fi\n")
        hook.chmod(0o755)
        out = self.run_sh('verify', str(self.store), '--wait', '--until-verdict')
        self.assertIn('status: clean', out)
        self.assertNotIn('verifying: units', out)
        wakes = [r.split('\t')[5] for r in (self.store / 'events.tsv').read_text().splitlines() if '\tmaster\twake\t' in r]
        self.assertEqual([w for w in wakes if w.startswith('verify:')], ['verify:progress', 'verify:clean'])

    def test_every_and_until_verdict_are_alternatives(self):
        self.assertIn('--every and --until-verdict are alternatives',
                      self.run_sh('verify', str(self.store), '--wait', '--every', '1', '--until-verdict', code=1))

    def open_slow_verification(self, n='001', slug='util'):
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0'})
        b = self.brief(n, slug, ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', f'X = 1{n}\n')
        self.write('tests/test_app.py', f'x = 1{n}\n')
        self.done(b, self.commit(slug))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), n, '--every', '0', '--timeout', '60', code=4)
        return self.state()['verifying']

    def written_verdict(self, open_, status, units='001', findings=''):
        Path(open_['verdict']).write_text(f'status: {status}\nunits: {units}\n\n## Findings\n\n{findings}\n\n## Evidence\n\n```\n$ true\n```\n')

    def test_an_unread_reject_closed_by_the_next_verify_prints_its_findings_and_starts_nothing(self):
        open_ = self.open_slow_verification()
        self.written_verdict(open_, 'reject', findings='1. Acceptance broke: add returns 0')
        starts = (self.fake / 'calls.log').read_text().count('agent start demo-verifier')
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=2)
        self.assertIn(f"closed: {open_['verdict']} (reject)", out)
        self.assertIn('Acceptance broke: add returns 0', out)
        self.assertIn('next: kitchen.sh revise', out)
        self.assertNotIn('verifying', self.state())
        self.assertEqual((self.fake / 'calls.log').read_text().count('agent start demo-verifier'), starts)

    def test_an_unread_sampled_clean_closed_by_the_next_verify_prints_the_audit_line(self):
        open_ = self.open_slow_verification(slug='b')
        self.written_verdict(open_, 'clean')
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertIn(f"closed: {open_['verdict']} (clean)", out)
        self.assertIn('audit: yes', out)
        self.assertIn('verifying: units 001 by pi', out)

    def test_the_handoff_nudge_prints_when_the_second_clean_verdict_is_closed_by_the_next_verify(self):
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
        hook.write_text(f'#!/bin/bash\necho >>{counter}\nif [ "$(wc -l <{counter})" -ge {n} ]; then\n{body}\nelse cp "$0" {hook}; fi\n')
        hook.chmod(0o755)

    def cancel_and_replace(self):
        return (f'{self.script} verify {self.store} --cancel >/dev/null 2>&1\n'
                f'{self.script} verify {self.store} 001 --every 0 --timeout 60 >/dev/null 2>&1')

    def outcome_of(self, packet):
        return re.search(r'^verifier-outcome: (\S+)$', Path(packet).read_text(), re.M).group(1)

    def test_verify_while_a_background_waiter_holds_the_close_claim_waits_for_it_and_starts_its_verification(self):
        """The waiter stops inside its close, holding the claim (the test releases it through a pipe, so no timing is involved); the next
        verify finds the verdict being closed, says it waits, and carries on once the waiter is done."""
        self.env.update({'KITCHEN_VERIFIER_EXIT_S': '1', 'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0'})
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        held, go = self.fake / 'held.fifo', self.fake / 'go.fifo'
        os.mkfifo(held)
        os.mkfifo(go)
        self.on_nth_get(1, f'echo held >{held}\ncat {go} >/dev/null')
        waiter = subprocess.Popen([str(self.script), 'verify', str(self.store), '--wait', '--every', '1'], cwd=self.repo, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(waiter.kill)
        self.assertEqual(held.read_text().strip(), 'held')
        nxt = subprocess.Popen([str(self.script), 'verify', str(self.store), '001', '--every', '0', '--timeout', '60'], cwd=self.repo, env=self.env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(nxt.kill)
        self.assertIn('another call is closing it; waiting for it', nxt.stdout.readline())
        go.write_text('go\n')
        waiter_out, _ = waiter.communicate()
        next_out, _ = nxt.communicate()
        self.assertEqual(waiter.returncode, 0, waiter_out)
        self.assertIn('status: clean', waiter_out)
        self.assertEqual(nxt.returncode, 4, next_out)
        self.assertIn(f"closed: {first['verdict']} (closed by another call)", next_out)
        self.assertIn('verifying: units 001', next_out)
        self.assertNotEqual(self.state()['verifying']['verdict'], first['verdict'])
        rows = [r for r in (self.store / 'events.tsv').read_text().splitlines() if '\tmaster\tverify\t' in r]
        self.assertEqual(len(rows), 1, rows)

    def test_a_cancel_racing_an_idle_update_leaves_no_verifying(self):
        """Fails with the idle update written without the ownership filter: it brings `.verifying` back as {idle_since: N}."""
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERIFY_IDLE_S': '100000'})
        self.open_slow_verification()
        # The second status poll is the idle branch's provider check, between the ownership check and the idle update.
        self.on_nth_get(2, f'{self.script} verify {self.store} --cancel >/dev/null 2>&1')
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn('verification closed elsewhere', out)
        self.assertNotIn('verifying', self.state())

    def set_close_claim(self, token):
        state = self.state()
        state['verifying']['closing'] = {'token': token, 'at': int(time.time())}
        (self.store / 'pair.json').write_text(json.dumps(state))

    def test_a_cancel_while_a_live_process_holds_the_close_claim_prints_no_cancelled_and_changes_nothing(self):
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
        self.assertIn('another call closed it', self.run_sh('verify', str(self.store), '--cancel', code=5))

    def test_a_close_claim_whose_process_is_gone_is_taken_over_at_once(self):
        """The claim is fresh (written now): only the dead process, not its age, lets it be taken over. Fails on unit 017's claim_close,
        which took over only a claim older than two minutes."""
        self.env.update({'KITCHEN_VERIFIER_EXIT_S': '1', 'HOSTNAME': 'claimhost'})
        first = self.open_slow_verification()
        gone = subprocess.Popen(['true'])
        gone.wait()
        self.set_close_claim(f'claimhost:{gone.pid}:dead')
        self.assertIn('cancelled: 001', self.run_sh('verify', str(self.store), '--cancel'))
        self.assertEqual(self.outcome_of(first['packet']), 'cancelled')

    def test_two_close_outs_of_one_written_verdict_log_it_and_count_the_handoff_once(self):
        """The second closer enters while the first is waiting for the verifier to leave, holding the claim: it ends with `closed elsewhere`
        and logs, prints and counts nothing. Without the claim both close it: two `verify` rows and a handoff count of 2."""
        self.env['KITCHEN_VERIFIER_EXIT_S'] = '1'
        first = self.open_slow_verification()
        self.written_verdict(first, 'clean')
        self.on_nth_get(1, f'{self.script} verify {self.store} --wait --every 1 >{self.fake}/second.out 2>&1; echo $? >{self.fake}/second.rc')
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1')
        self.assertIn('status: clean', out)
        self.assertEqual((self.fake / 'second.rc').read_text().strip(), '5')
        self.assertIn('verification closed elsewhere', (self.fake / 'second.out').read_text())
        self.assertNotIn('status: clean', (self.fake / 'second.out').read_text())
        rows = [r for r in (self.store / 'events.tsv').read_text().splitlines() if '\tmaster\tverify\t' in r]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(self.state()['master']['units_since_handoff'], 1)

    def test_a_waiter_in_the_grace_whose_verification_was_cancelled_and_replaced_exits_5_and_touches_nothing(self):
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

    def test_a_waiter_on_a_provider_error_whose_verification_was_replaced_after_the_loop_runs_no_retry(self):
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
        self.run_sh('verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4)
        first = self.state()['verifying']['verdict']
        self.on_nth_get(3, self.cancel_and_replace())
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn('verification closed elsewhere', out)
        self.assertNotIn('retry:', out)
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], first)
        self.assertEqual(self.outcome_of(second['packet']), 'running')
        self.assertEqual(len(self.verifier_starts()), 2)

    def test_a_waiter_whose_verification_the_next_verify_closed_after_the_loop_exits_5_and_leaves_the_new_packet(self):
        """A stalled verifier ends the loop at the fourth status poll; the fifth is the waiter's read of the provider error after it. There the
        verdict is written and the next verify closes it and starts V2. Fails on the unit-017 functions of 27bced8."""
        self.env.update({'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0', 'KITCHEN_VERIFY_IDLE_S': '0'})
        open_ = self.open_slow_verification()
        written = ("printf 'status: clean\\nunits: 001\\n\\n## Findings\\n\\nnone\\n\\n## Evidence\\n\\n```\\n$ true\\n```\\n' >"
                   f"{open_['verdict']}\n{self.script} verify {self.store} 001 --every 0 --timeout 60 >/dev/null 2>&1")
        self.on_nth_get(5, written)
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '1', code=5)
        self.assertIn('verification closed elsewhere', out)
        self.assertEqual(self.outcome_of(open_['packet']), 'clean')
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], open_['verdict'])
        self.assertEqual(self.outcome_of(second['packet']), 'running')

    def test_a_verification_replaced_just_before_the_grace_gets_no_deadline_prompt_on_the_new_verifier(self):
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

    def test_a_waiter_whose_verification_was_cancelled_and_replaced_touches_nothing(self):
        self.open_slow_verification()
        first = self.state()['verifying']['verdict']
        pane = self.state()['verifier']['pane_id']
        hook = self.fake / 'once/get'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f'#!/bin/bash\n{self.script} verify {self.store} --cancel >/dev/null 2>&1\n'
                        f'{self.script} verify {self.store} 001 --every 0 --timeout 60 >/dev/null 2>&1\n')
        hook.chmod(0o755)
        out = self.run_sh('verify', str(self.store), '--wait', '--every', '0', code=5)
        self.assertIn('verification closed elsewhere', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertEqual(calls.count(f'pane close {pane}'), 1)
        second = self.state()['verifying']
        self.assertNotEqual(second['verdict'], first)
        self.assertTrue((self.store / 'scratch' / second['scratch']).exists())
        self.assertTrue((self.fake / 'panes' / self.state()['verifier']['pane_id']).exists())

    def test_a_verdict_written_but_unread_is_closed_by_the_next_verify(self):
        open_ = self.open_slow_verification()
        Path(open_['verdict']).write_text('status: clean\nunits: 001\n\n## Evidence\n\n```\n$ true\n```\n')
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertIn(f"closed: {open_['verdict']} (clean)", out)
        self.assertIn('verifying: units 001 by pi', out)
        self.assertNotEqual(self.state()['verifying']['verdict'], open_['verdict'])
        self.assertIn('\tmaster\tverify\t', (self.store / 'events.tsv').read_text())

    def test_verify_cancel_stops_the_open_verification_and_removes_its_pane_and_scratch(self):
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
        fleet = subprocess.run([sys.executable, str(script), '--json', 'fleet', '--runs', str(self.store.parent)],
                               env=self.env, capture_output=True, text=True, check=True)
        attempts = json.loads(fleet.stdout)['verifiers']
        self.assertEqual(sum(v['running'] for v in attempts), 0)

    def test_verify_cancel_with_nothing_open_exits_5(self):
        self.assertIn('no verification open', self.run_sh('verify', str(self.store), '--cancel', code=5))

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
        self.assertIn('provider_error: Reached free model rate limit. Your limit will reset in 50 minutes', out)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertIn('verifier demo-verifier (devin (model unspecified)) in ', out)
        self.assertIn('status: clean\n', out)
        starts = [line for line in (self.fake / 'calls.log').read_text().splitlines()
                  if line.startswith('agent start demo-verifier')]
        self.assertEqual(len(starts), 2)
        self.assertIn('--kind devin', starts[1])
        self.assertNotIn('devin/swe-2', starts[1])
        self.assertEqual(self.verify_wakes(), ['verify:clean'])

    def set_roster(self, extra):
        Path(self.env['PSTACK_KITCHEN_ROSTER']).write_text(
            '[sidekick]\nkind = "pi"\nargs = ["--model", "devin/swe-2"]\n'
            '[sidekick.fallback]\nkind = "devin"\n[consultant]\nkind = "codex"\n' + extra)

    def verifier_starts(self):
        return [line for line in (self.fake / 'calls.log').read_text().splitlines()
                if line.startswith('agent start demo-verifier')]

    def verify_landing(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.git('rev-list', '--max-parents=0', 'HEAD'))
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        return self.run_sh('verify', str(self.store), '--landing')

    def declare_setup(self):
        toml = self.repo / '.agents/kitchen.toml'
        toml.write_text(toml.read_text() + '\n[scratch]\nsetup = ["bun install --frozen-lockfile"]\n')
        self.commit('declare scratch setup')

    def prove_block(self, packet):
        return packet.read_text().split('## Prove', 1)[1].split('```bash\n', 1)[1].split('```', 1)[0].splitlines()

    def test_the_unit_packet_runs_setup_first_only_when_declared(self):
        self.verify_app_unit('clean', 0)
        plain = self.prove_block(self.store / 'verdicts/001-util-v1-packet.md')
        self.assertEqual(plain[0], 'export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=demo')
        self.assertIn(' gate app behavioral ', plain[1])
        self.assertFalse(any(' setup' in line for line in plain))

    def test_the_unit_packet_puts_setup_ahead_of_the_gates(self):
        self.declare_setup()
        self.verify_app_unit('clean', 0)
        block = self.prove_block(self.store / 'verdicts/001-util-v1-packet.md')
        self.assertEqual(block[0], 'export PSTACK_KITCHEN_ROLE=verifier PSTACK_KITCHEN_RUN=demo')
        self.assertRegex(block[1], r'^python3 \S+kitchen\.py --repo \S+/001-util-verify setup$')
        self.assertIn(' gate app behavioral ', block[2])

    def test_every_packet_states_the_write_rule_and_master_only_verify_lines_stay_out(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        text = b.read_text()
        self.assertIn('```bash\nx\n```', text)
        b.write_text(text.replace('```bash\nx\n```', '```bash\npython3 -c "print(1)"\nkitchen.sh log demo x y z w  # master-only\n```'))
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
        self.assertIn('kitchen.sh log demo x y z w', packet.split('not for the verifier:')[1])
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
        out = self.run_sh('verify', str(self.store), '001', '--timeout', '60', '--every', '0')
        return out, (self.store / 'verdicts/001-util-v1-packet.md').read_text()

    def test_a_verify_block_the_kitchen_refuses_is_warned_of_and_said_so_in_the_packet(self):
        out, packet = self.packet_with_verify('run the tests please')
        self.assertIn('Brief commands: none (brief-verify: kitchen: ', packet)
        self.assertIn('the Verify section has text outside a ``` block)', packet)
        self.assertIn('warning: unit 001: brief-verify: kitchen: ', out)

    def test_each_brief_command_is_its_own_numbered_block_run_from_the_scratch_root(self):
        _, packet = self.packet_with_verify('```bash\ncd tests\npython3 -c "print(2)"\n```')
        self.assertIn('Run each command in a fresh shell from the scratch root.', packet)
        self.assertIn('Command 1:\n\n```bash\ncd tests\n```', packet)
        self.assertIn('Command 2:\n\n```bash\npython3 -c "print(2)"\n```', packet)

    def test_a_command_naming_the_checkout_or_the_store_in_any_spelling_is_not_for_the_verifier(self):
        self.env['HOME'] = self.tmp.name
        home = self.tmp.name
        repo_rel, store_rel = os.path.relpath(self.repo, home), os.path.relpath(self.store, home)
        commands = [f'cd {self.repo} && python3 tests/run.py', f'pytest "{self.repo}"', f'python3 {self.repo}/tools/check.py',
                    f'cat {self.store}/standing-orders.md', f'cat ~/{store_rel}/x', f'cat $HOME/{store_rel}/x', f'cat ${{HOME}}/{repo_rel}/x', f'cat "$HOME"/{store_rel}/x', f'cat "${{HOME}}"/{repo_rel}/x',
                    f'sh {self.repo}/.git/hooks/x', f'x {self.repo};y']
        own = ['/usr/bin/env python3 x.py', 'ls /tmp/x', f'echo /other{self.repo}/y']
        out, packet = self.packet_with_verify('```bash\n' + '\n'.join(commands + own) + '\n```')
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
        _, packet = self.packet_with_verify('```bash\ncat <<EOF\n  ```\n  fenced\n  ```\nEOF\n```')
        self.assertIn('Command 1:\n\n````bash\ncat <<EOF\n  ```\n  fenced\n  ```\nEOF\n````', packet)

    def test_a_heredoc_marked_master_only_on_its_first_line_stays_out_of_the_verifiers_commands(self):
        _, packet = self.packet_with_verify('```bash\ncat <<EOF  # master-only\nnote\nEOF\ntrue\n```')
        own, _, apart = packet.partition('not for the verifier:')
        self.assertNotIn('cat <<EOF', own)
        self.assertIn('(master-only)\n```bash\ncat <<EOF  # master-only\nnote\nEOF\n```', apart)
        self.assertIn('Command 1:\n\n```bash\ntrue\n```', own)

    def test_a_unit_landed_before_the_landing_base_is_left_out_of_the_stack_and_goals(self):
        for n, slug, goal in ((1, 'util', 'Users can add numbers.'), (2, 'next', 'Users can list numbers.')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            b.write_text(re.sub(r'(## Goal\n\n)x', rf'\g<1>{goal}', b.read_text(), count=1))
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            head = self.commit(slug)
            self.done(b, head)
            if n == 1:
                self.git('update-ref', 'refs/remotes/origin/main', head)
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--every', '0')
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        stack = packet.split('## Stack')[1].split('## Goals')[0]
        goals = packet.split('## Goals')[1].split('\n## ')[0]
        self.assertIn('002-next.md', stack)
        self.assertNotIn('001-util.md', stack)
        self.assertIn('Users can list numbers.', goals)
        self.assertNotIn('Users can add numbers.', goals)

    def landing_deadline_world(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('behavioral = ["true"]', 'behavioral = ["true"]\nlanding = ["true"]'))
        self.commit('declare a landing command')
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))

    def test_the_landing_deadline_counts_the_landing_gates_it_measured(self):
        self.landing_deadline_world()
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'], env=self.env,
                                        capture_output=True, text=True, check=True).stdout.strip())
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'timings.tsv').write_text(f'{int(time.time())}\tapp\tlanding\tverifier\tpass\t600\t0\t0\t600\n')
        out = self.run_sh('verify', str(self.store), '--landing', '--every', '0')
        self.assertIn('deadline +30m for the landing gates of app: 3 x 10m measured; now 75m', out)

    def test_the_landing_deadline_adds_a_fixed_allowance_for_a_landing_gate_never_measured(self):
        self.landing_deadline_world()
        out = self.run_sh('verify', str(self.store), '--landing', '--every', '0')
        self.assertIn('deadline +10m for the landing gates of app: 3 x 0m measured, 1 unmeasured at 10m each; now 55m', out)

    def test_a_unit_landed_before_the_landing_base_is_neither_in_the_stack_nor_covered_by_the_landing_verdict(self):
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
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {landed[:9]}..{head[:9]}\n')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertNotIn('covered: unit 001', out)
        self.assertIn(f'land-check: note: unit 001 landed before the base of landing verdict {landing}; verify it on its own', out)
        self.assertIn('unverified: unit 001', out)
        self.assertIn(f'covered: unit 002 by the clean landing verdict {landing}', out)
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--every', '0')
        stack = (self.store / 'verdicts/landing-v2-packet.md').read_text().split('## Stack')[1].split('## Goals')[0]
        self.assertIn('002-next.md', stack)
        self.assertNotIn('001-util.md', stack)

    def test_a_landing_packet_holds_each_covered_briefs_goal(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        for n, slug, goal in ((1, 'util', 'Users can add numbers.'), (2, 'next', 'Users can list numbers.')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            b.write_text(re.sub(r'(## Goal\n\n)x', rf'\g<1>{goal}', b.read_text(), count=1))
            self.dispatch(b)
            self.write(f'src/mod{n}.py', f'X = {n}\n')
            self.write(f'tests/test_mod{n}.py', f'x = {n}\n')
            self.done(b, self.commit(slug))
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        goals = packet.split('## Goals')[1].split('\n## ')[0]
        self.assertIn('### Unit 001 util\n\nUsers can add numbers.', goals)
        self.assertIn('### Unit 002 next\n\nUsers can list numbers.', goals)
        self.assertIn('## Flows driven', (skill / 'references/verdict-template.md').read_text())

    def test_the_landing_packet_runs_setup_first_when_declared(self):
        self.declare_setup()
        self.verify_landing()
        block = self.prove_block(self.store / 'verdicts/landing-v1-packet.md')
        self.assertRegex(block[1], r'^python3 \S+kitchen\.py --repo \S+/000-landing-verify setup$')
        self.assertIn(' gate app behavioral ', block[2])

    def standing_line(self):
        """A line of the test store's standing-orders.md that no other part of a packet repeats."""
        text = (self.store / 'standing-orders.md').read_text()
        line = next(l for l in text.splitlines() if l.startswith('13. '))
        return text, line

    def test_the_unit_packet_carries_the_runs_standing_orders(self):
        self.verify_app_unit('clean', 0)
        text, line = self.standing_line()
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertIn('## Standing orders', packet)
        self.assertIn(f'{self.store}/standing-orders.md', packet)
        self.assertIn(line, packet)
        self.assertLess(packet.index('## Standing orders'), packet.index('## Changed'))

    def test_the_landing_packet_carries_the_runs_standing_orders(self):
        self.verify_landing()
        _, line = self.standing_line()
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn('## Standing orders', packet)
        self.assertIn(line, packet)
        self.assertLess(packet.index('## Standing orders'), packet.index('## Changed'))

    def test_a_store_without_standing_orders_still_gets_its_packets(self):
        (self.store / 'standing-orders.md').unlink()
        self.verify_app_unit('clean', 0)
        packet = (self.store / 'verdicts/001-util-v1-packet.md').read_text()
        self.assertNotIn('## Standing orders', packet)
        self.assertIn('## Changed', packet)
        self.assertIn('## Prove', packet)

    def packet_fields(self, n='001-util-v1'):
        text = (self.store / f'verdicts/{n}-packet.md').read_text()
        return {k: v for k, v in (line.split(': ', 1) for line in text.splitlines() if line.startswith('verifier-'))}

    def test_the_packet_records_the_configured_agent_class_and_a_prepared_outcome(self):
        self.set_roster('[verifier]\nkind = "pi"\nargs = ["--approve", "--model", "devin/swe-2", "--thinking", "high"]\n')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 9\n')
        self.write('tests/test_app.py', 'x = 9\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')
        self.run_sh('verify', str(self.store), '001', '--every', '0', code=4)
        self.assertEqual(self.packet_fields(), {'verifier-agent': 'pi --model devin/swe-2 --thinking high',
                                                'verifier-class': 'routine', 'verifier-outcome': 'running'})

    def test_every_attempt_ends_with_its_own_outcome(self):
        for mode, code, outcome in [('clean', 0, 'clean'), ('reject', 2, 'reject'), ('inconclusive', 2, 'inconclusive'),
                                    ('noevidence', 2, 'invalid'), ('gone', 4, 'missing')]:
            with self.subTest(mode):
                self.setUp()
                self.verify_app_unit(mode, code)
                self.assertEqual(self.packet_fields()['verifier-outcome'], outcome)
                self.assertNotIn('verifying', self.state())

    def test_a_provider_stop_then_a_clean_fallback_are_two_attempts(self):
        self.set_roster('[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n')
        self.rate_limited_verifier()
        self.verify_app_unit('ratelimit', 0)
        self.assertEqual(self.packet_fields('001-util-v1')['verifier-outcome'], 'provider')
        self.assertEqual(self.packet_fields('001-util-v2')['verifier-outcome'], 'clean')
        self.assertEqual(self.packet_fields('001-util-v2')['verifier-agent'], 'devin (model unspecified)')

    def test_both_attempts_failing_are_both_recorded(self):
        self.set_roster('[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n')
        self.rate_limited_verifier()
        self.verify_app_unit('ratelimit-all', 2)
        self.assertEqual([self.packet_fields(f'001-util-v{k}')['verifier-outcome'] for k in (1, 2)], ['provider', 'provider'])

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
        self.assertEqual(self.packet_fields('landing-v1'), {'verifier-agent': 'claude --model claude-opus-5-5',
                                                            'verifier-class': 'landing', 'verifier-outcome': 'clean'})

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
        return self.run_sh('verify', str(self.store), '001', '--timeout', '0', *(['--every', every] if every else []), *extra, code=code)

    def grace_prompts(self):
        return [line for line in (self.fake / 'calls.log').read_text().splitlines() if line.startswith('agent prompt demo-verifier Deadline reached')]

    def test_a_swallowed_packet_is_sent_once_more(self):
        out = self.deadline_verify('swallow', code=0)
        self.assertIn('redelivered: the verifier sat idle without the packet', out)
        self.assertIn('status: clean', out)
        loads = [line for line in (self.fake / 'calls.log').read_text().splitlines()
                 if line.startswith('agent prompt demo-verifier Load')]
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
        self.assertIn('redelivered: the verify that started this verifier stopped before sending its packet', out)
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
        self.assertIn('the verifier sat idle 0m with nothing running; rerun: kitchen.sh verify', out)
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
        proc = subprocess.Popen(['sleep', '30'], cwd=self.repo, env={**self.env, 'HERDR_PANE_ID': verifier['pane_id']})
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
        (self.fake / 'session-ids/demo-verifier').write_text('abc-123 /work/repo.git/v1\n')
        path = Path(self.env['HOME']) / '.claude/projects/-work-repo-git-v1/abc-123.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_a_verdict_less_attempt_names_the_transcript_and_whether_the_packet_arrived(self):
        path = self.claude_transcript('{"type":"user","message":{"content":"Deadline reached: write it"}}\n')
        out = self.deadline_verify('slow', code=4)
        self.assertIn(f'transcript: {path}', out)
        self.assertIn('packet: never reached the verifier', out)

    def test_a_transcript_with_the_packet_says_it_arrived(self):
        packet = self.store / 'verdicts/001-util-v1-packet.md'
        self.claude_transcript(f'{{"type":"user","message":{{"content":"pstack-kitchen VERIFY {packet}"}}}}\n')
        out = self.deadline_verify('slow', code=4)
        self.assertIn('packet: the verifier received it', out)

    def test_a_verdict_arriving_during_the_grace_wins(self):
        verdict = self.store / 'verdicts/001-util-v1.md'
        packet = self.store / 'verdicts/001-util-v1-packet.md'
        late = subprocess.Popen(['bash', '-c', f"until [ -f {packet} ]; do sleep 0.2; done; sleep 1.5; printf '# V\\n\\nstatus: reject\\nunits: 001\\n\\n## Findings\\n\\n1. x\\n\\n## Evidence\\n\\n```\\na\\n```\\n' >{verdict}"])
        self.addCleanup(late.wait)
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
        self.assertEqual(self.state()['verifying']['grace_delivery'], 'skipped:prompt-failed')
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
        self.assertEqual(self.state()['verifying']['grace_delivery'], 'skipped:devin-working')
        self.assertEqual(self.grace_prompts(), [])

    def test_a_provider_error_at_the_deadline_takes_the_fallback_not_the_grace(self):
        self.set_roster('[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n')
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
        self.set_roster('[verifier]\nkind = "pi"\nargs = ["--model", "m"]\n'
                        '[verifier.fallback]\nkind = "devin"\nargs = ["--model", "fb"]\n')
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
        self.set_roster('[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "devin"\n')
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit-all', 2)
        self.assertIn('retry: verifying again on the fallback, devin', out)
        self.assertEqual(len(self.verifier_starts()), 2)
        self.assertIn(f'next: wait for the limit, or kitchen.sh verify {self.store} 001 --kind <another kind>', out)

    def test_a_fallback_of_the_failed_kind_is_not_retried(self):
        self.set_roster('[verifier]\nkind = "pi"\n[verifier.fallback]\nkind = "pi"\nargs = ["--model", "other"]\n')
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
        return out, Path(re.search(r'^filed: (.+)$', out, re.MULTILINE).group(1))

    def calls(self):
        return (self.fake / 'calls.log').read_text()

    def test_feedback_files_the_report_behind_a_header(self):
        out, path = self.file_feedback()
        self.assertEqual(path.parent, self.feedback_dir() / 'inbox')
        text = path.read_text()
        self.assertRegex(text, r'^# Kitchen feedback: demo \(repo\)\nid: \d{8}-repo-demo\nrun: demo   store: ')
        self.assertIn(f'master: demo-master (p0)\nbuild: unknown ({skill.parent.resolve()})\nretro: ', text)
        self.assertNotIn('filed with:', text)
        self.assertRegex(text, r'retro: run demo: 0 unit reports, 0 verified units \(0 clean verdicts on the first try\)')
        self.assertRegex(text, r'filed: \d{4}-\d\d-\d\dT[\d:]+Z\nstatus: open\n\n## Gaps\n\n### 1\. dispatch hid the rotation')
        self.assertIn('feedback\t', (self.store / 'events.tsv').read_text())
        self.assertIn('delivered: notification for the human (no maintainer is registered)', out)
        self.assertEqual(list(self.feedback_dir().glob('.filing-*')), [])

    def test_a_file_without_a_gap_files_nothing(self):
        out = self.run_sh('feedback', str(self.store), str(self.gap_file('## Gaps\n\nnothing\n')), code=1)
        self.assertIn('has no gap', out)
        self.assertFalse((self.feedback_dir() / 'inbox').exists() and list((self.feedback_dir() / 'inbox').iterdir()))

    def test_simultaneous_filings_get_distinct_ids(self):
        f = self.gap_file()
        procs = [subprocess.Popen([str(self.script), 'feedback', str(self.store), str(f)], cwd=self.repo, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for _ in range(4)]
        outs = [p.communicate()[0] for p in procs]
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
        self.assertIn(f'agent prompt demo-master pstack-kitchen FEEDBACK {path}', self.calls())
        self.assertNotIn('notification show', self.calls())
        self.assertIn('live', self.run_sh('maintainer', 'status'))

    def test_every_broken_maintainer_falls_back_to_the_notification_with_the_report_filed(self):
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

        for name, setup, why in [('stale pane', stale_pane, 'moved off pane p9'), ('replaced', replaced, 'different session'),
                                 ('blocked', blocked, 'is blocked'), ('absent', absent, 'is gone'),
                                 ('failed prompt', failed_prompt, 'prompt to the maintainer failed')]:
            with self.subTest(name):
                reg.write_text(good)
                (self.fake / 'agents/demo-master').write_text('idle claude\n')
                (self.fake / 'prompt-fail').unlink(missing_ok=True)
                (self.fake / 'calls.log').write_text('')
                setup()
                out, path = self.file_feedback()
                self.assertRegex(out, rf'delivered: notification for the human \([^)]*{why}')
                self.assertIn('notification show pstack-kitchen: feedback filed --body ' + str(path), self.calls())
                self.assertTrue(path.exists())

    def test_maintainer_off_and_status(self):
        self.assertIn('maintainer: none', self.run_sh('maintainer', 'status'))
        self.register_maintainer()
        self.assertEqual(json.loads((self.feedback_dir() / 'maintainer.json').read_text())['pane'], 'p0')
        self.assertIn('maintainer: none', self.run_sh('maintainer', 'off'))
        self.assertFalse((self.feedback_dir() / 'maintainer.json').exists())

    def test_inbox_lists_oldest_first_and_close_moves_the_report(self):
        _, first = self.file_feedback()
        text = first.read_text().replace('filed: ', 'filed: 2020-01-01T00:00:00Z\nold-filed: ')
        first.write_text(text)
        self.file_feedback(self.GAP + '\n### 2. second\n')
        lines = self.run_sh('feedback', '--inbox').strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(first.stem), lines)
        self.assertRegex(lines[0], r'demo\s+repo\s+unknown\s+1\s+\d+d')
        self.assertRegex(lines[1], r'demo\s+repo\s+unknown\s+2\s+\d+m')
        out = self.run_sh('feedback', '--close', first.stem, 'abc1234', 'fixed in the next build')
        self.assertIn(f'closed: {first.stem}', out)
        self.assertFalse(first.exists())
        done = (self.feedback_dir() / 'done' / first.name).read_text()
        self.assertIn('status: closed\n', done)
        self.assertIn('## Closed\n\nclosed: ', done)
        self.assertIn('commit: abc1234\nnote: fixed in the next build\n', done)
        self.assertEqual(len(self.run_sh('feedback', '--inbox').strip().splitlines()), 1)

    def test_closing_twice_prints_the_block_and_an_unknown_id_fails(self):
        _, path = self.file_feedback()
        self.run_sh('feedback', '--close', path.stem, 'none', 'wontfix')
        again = self.run_sh('feedback', '--close', path.stem, 'ffff', 'other note')
        self.assertIn('already closed', again)
        self.assertIn('note: wontfix', again)
        self.assertNotIn('ffff', (self.feedback_dir() / 'done' / path.name).read_text())
        self.assertIn('no feedback report', self.run_sh('feedback', '--close', 'nope', 'none', 'x', code=1))
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
        self.assertEqual([b['path'] for b in self.state()['skill_builds']], ['/old/skills', here])

    def test_a_run_without_stamps_stays_unstamped(self):
        state = self.state()
        del state['skill_builds']
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.run_sh('status', str(self.store))
        self.assertNotIn('skill_builds', self.state())

    def test_feedback_names_the_build_the_run_started_on_and_the_one_it_filed_with(self):
        here = str(skill.parent.resolve())
        self.write_builds(('1', 'abc123', '/old/skills'), ('2', 'def456', here))
        state = self.state()
        state['skill_builds'][0]['path'] = '/old/skills'
        (self.store / 'pair.json').write_text(json.dumps(state))
        _, path = self.file_feedback()
        text = path.read_text()
        self.assertIn('build: abc123 (/old/skills)\nfiled with: def456 (' + here + ')\n', text)

    def test_feedback_build_is_unknown_without_a_builds_file(self):
        _, path = self.file_feedback()
        self.assertIn('build: unknown (', path.read_text())

    def test_a_rate_limited_verifier_without_a_fallback_is_inconclusive(self):
        state = self.state()
        del state['sidekick']['fallback']
        (self.store / 'pair.json').write_text(json.dumps(state))
        self.rate_limited_verifier()
        _, out = self.verify_app_unit('ratelimit', 2)
        self.assertIn('provider_error: Reached free model rate limit', out)
        self.assertIn(f'next: wait for the limit, or kitchen.sh verify {self.store} 001 --kind <another kind>', out)
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
        self.run_sh('verify', str(self.store), '001', '--every', '0', '--timeout', '60', code=4)
        text = (self.store / 'pair.json').read_text()
        return text[text.index('    "entry": {'):text.index('    "retried"')]

    def test_the_sidekick_source_entry_is_written_byte_for_byte(self):
        self.assertEqual(self.open_verification_entry(), (
            '    "entry": {\n      "kind": "pi",\n      "args": [\n        "--approve",\n        "--model",\n        "devin/swe-2"\n      ],\n'
            '      "source": "sidekick",\n      "fallback": {\n        "kind": "devin",\n        "args": []\n      }\n    },\n'))

    def test_the_master_source_entry_is_written_byte_for_byte(self):
        self.set_roster('[verifier]\nkind = "master"\n')
        self.assertEqual(self.open_verification_entry(), (
            '    "entry": {\n      "kind": "claude",\n      "args": [],\n      "fallback": null,\n      "source": "master"\n    },\n'))

    def assert_fallback_is_damage(self, value, word):
        state = self.state()
        state['sidekick']['fallback'] = value
        (self.store / 'pair.json').write_text(json.dumps(state))
        _, out = self.verify_app_unit('clean', 1)
        self.assertIn(f'pair.json is damaged: sidekick.fallback is {word}, not an object', out)
        self.assertEqual(self.verifier_starts(), [])

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
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                                        env=self.env, capture_output=True, text=True, check=True).stdout.strip())
        (state_dir / 'timings.tsv').write_text('1\tapp\tbehavioral\tverifier\tpass\t1190.5\n'
                                               '2\tapp\tbehavioral\tverifier\tfail\t9000\n')
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
        state_dir = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'],
                                        env=self.env, capture_output=True, text=True, check=True).stdout.strip())
        (state_dir / 'timings.tsv').write_text(''.join('\t'.join(t) + '\n' for t in timings))
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 7\n')
        self.write('tests/test_app.py', 'x = 7\n')
        self.done(b, self.commit('util'))
        (self.fake / 'verdict-mode').write_text('slow')

    def deadline_run(self, *extra):
        out = self.run_sh('verify', str(self.store), '001', '--every', '0', *extra, code=4)
        v = self.state()['verifying']
        return out, (v['deadline'] - v['started']) // 60

    def test_the_default_deadline_adds_setup_and_load_and_prints_its_parts(self):
        self.env['PSTACK_KITCHEN_LOAD'] = '22 16'
        self.deadline_fixture(('1', 'app', 'behavioral', 'verifier', 'pass', '1200'), ('2', '-', 'setup', 'verifier', 'pass', '600'),
                              setup=True)
        out, minutes = self.deadline_run()
        self.assertIn('deadline 97m: 3 x 20m gates + 10m setup, x1.38 for load 22 on 16 cores', out)
        self.assertEqual(minutes, 97)
        self.assertIn('deadline: 97m from now\n', (self.store / 'verdicts/001-u1-v1-packet.md').read_text() if False else
                      next((self.store / 'verdicts').glob('001-*-packet.md')).read_text())

    def test_the_load_factor_is_capped_and_a_setup_row_is_ignored_without_declared_setup(self):
        self.env['PSTACK_KITCHEN_LOAD'] = '100 8'
        self.deadline_fixture(('1', 'app', 'behavioral', 'verifier', 'pass', '1200'), ('2', '-', 'setup', 'verifier', 'pass', '600'))
        out, minutes = self.deadline_run()
        self.assertIn('deadline 180m: 3 x 20m gates, x3 for load 100 on 8 cores', out)
        self.assertEqual(minutes, 180)

    def test_an_unmeasured_setup_is_named_and_timeout_wins(self):
        self.deadline_fixture(('1', 'app', 'behavioral', 'verifier', 'pass', '1200'), setup=True)
        out, minutes = self.deadline_run()
        self.assertIn('deadline 60m: 3 x 20m gates + setup unmeasured, load 1 on 8 cores', out)
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
        self.assertIn('rotated: the pi sidekick starts this brief in a fresh session', out)
        calls = (self.fake / 'calls.log').read_text()
        self.assertIn('agent prompt demo-sidekick /quit', calls)
        self.assertFalse(self.state()['sidekick'].get('rotation_required'))

    def test_discuss_rotates_a_pi_sidekick_after_a_done_unit(self):
        state = self.state()
        state['sidekick']['rotation_required'] = True
        (self.store / 'pair.json').write_text(json.dumps(state))
        plan = self.store / 'plans/001-util.md'
        plan.parent.mkdir(exist_ok=True)
        plan.write_text('# Plan 001: util\n\nrisk: routine\n')
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertIn('rotated: the pi sidekick starts this plan in a fresh session', out)
        self.assertNotIn('needs a fresh session', out)
        self.assertIn('agent prompt demo-sidekick /quit', (self.fake / 'calls.log').read_text())
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
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1', code=4)
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
        self.assertEqual((self.fake / 'calls.log').read_text().count('agent prompt demo-consultant pstack-kitchen PLAN'), 1)
        (self.fake / 'agents/demo-consultant').write_text('working codex\n')
        plan.write_text('# Plan 001: api\n\nrisk: escalated\n\nRevised.\n')
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1', code=5)
        self.assertIn('consultant demo-consultant is working; wait for it before a plan round', out)

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
        self.assertIn('risk: must start with routine or escalated', self.run_sh('discuss', str(self.store), str(plan), code=2))

    def test_a_discuss_answered_by_both_roles_logs_one_wake_naming_both(self):
        plan = self.consultant_plan('escalated')
        self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        rows = [r.split('\t') for r in (self.store / 'events.tsv').read_text().splitlines() if '\tmaster\twake\t' in r]
        self.assertEqual([r[5] for r in rows], ['sidekick:agree,consultant:agree'])

    def test_a_plan_dropped_by_an_idle_recipient_is_sent_once_more(self):
        plan = self.consultant_plan('escalated')
        (self.fake / 'prompt-drop').touch()
        out = self.run_sh('discuss', str(self.store), str(plan), '--timeout', '1')
        self.assertRegex(out, r'redelivered: (sidekick demo-sidekick|consultant demo-consultant) sat idle without the plan')
        self.assertIn('sidekick_status: agree', out)
        self.assertIn('consultant_status: agree', out)
        self.assertEqual((self.store / 'events.tsv').read_text().count('\tplan-redeliver\t'), 1)

    def test_a_rotation_that_never_gets_ready_names_the_provider_error_and_the_failover(self):
        state = self.state()
        state['sidekick']['rotation_required'] = True
        (self.store / 'pair.json').write_text(json.dumps(state))
        (self.fake / 'no-ready').touch()
        (self.fake / 'screen').mkdir(exist_ok=True)
        (self.fake / 'screen/demo-sidekick').write_text('Error: Reached free model rate limit. Your limit will reset in 8 minutes.\n')
        out = self.dispatch(self.brief('001', 'util', ['src/**', 'tests/**']), code=4)
        self.assertIn('rotating the sidekick before this brief failed', out)
        self.assertIn('provider_error: ', out)
        self.assertIn('rate limit', out)
        self.assertIn(f'next: pair.sh failover {self.store} --reason provider-error', out)
        self.assertIn('pi:unready', (self.store / 'events.tsv').read_text())

    @unittest.skipUnless(Path('/proc/self').exists(), 'background work is read from /proc, Linux only')
    def test_a_running_command_is_not_a_stale_log(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        (self.fake / 'agents/demo-sidekick').write_text('working pi\n')
        prog = self.store / 'progress' / b.name
        prog.parent.mkdir(exist_ok=True)
        prog.write_text('- started\n')
        old = time.time() - 600
        os.utime(prog, (old, old))
        out = self.run_sh('wait', str(self.store), '--timeout', '1', '--max', '0', code=4)
        self.assertIn('STALE', out)
        self.assertNotIn('"error"', out)
        pane = self.state()['sidekick']['pane_id']
        test = subprocess.Popen(['sleep', '30'], cwd=self.repo, env={**self.env, 'HERDR_PANE_ID': pane})
        self.addCleanup(lambda: (test.kill(), test.wait()))
        time.sleep(1.1)
        out = self.run_sh('wait', str(self.store), '--timeout', '1', '--max', '0', code=4)
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
        self.assertIn(f'range: {init[:9]}..{head[:9]}', (self.store / 'verdicts/001-docs-v2.md').read_text())

    def test_a_pi_sidekick_that_exits_on_a_rate_limit_says_so(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        cwd = self.state()['cwd']
        logs = Path(self.env['HOME']) / '.pi/agent/sessions' / ('--' + cwd.lstrip('/').replace('/', '-') + '--')
        logs.mkdir(parents=True)
        (logs / 's.jsonl').write_text(
            '{"type":"message","message":{"role":"assistant","stopReason":"stop"}}\n'
            '{"type":"message","message":{"role":"assistant","stopReason":"error",'
            '"errorMessage":"Reached free model rate limit. Your limit will reset in 50 minutes (at 11:26 UTC)."}}\n')
        (self.fake / 'agents/demo-sidekick').unlink()
        out = self.run_sh('wait', str(self.store), '--timeout', '1', '--max', '0', code=4)
        self.assertIn('provider_error: the pi sidekick exited on: Reached free model rate limit', out)
        self.assertIn(f'next: pair.sh failover {self.store} --reason exited', out)

    def test_a_muse_sidekick_that_exits_on_a_billing_error_says_so(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        state = self.state()
        state['sidekick']['kind'] = 'muse'
        (self.store / 'pair.json').write_text(json.dumps(state))
        log = Path(self.env['HOME']) / '.local/share/muse/sessions' / time.strftime('%Y/%m/%d') / '01a1-other/session.jsonl'
        log.parent.mkdir(parents=True)
        log.write_text('{"workspace_root":"/somewhere/else"} rate limited\n')
        log = log.parent.parent / '01a1-this/session.jsonl'
        log.parent.mkdir(parents=True)
        log.write_text(f'{{"workspace_root":"{state["cwd"]}"}}\n'
                       '{"error":"model failed: API error 402: Billing verification failed. Please check your payment method. (billing_error)"}\n')
        (self.fake / 'agents/demo-sidekick').unlink()
        out = self.run_sh('wait', str(self.store), '--timeout', '1', '--max', '0', code=4)
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
        self.assertIn('name no units', self.run_sh('verify', str(self.store), '001', '--landing', code=1))
        out = self.run_sh('verify', str(self.store), '--landing')
        self.assertIn('landing base: merge-base of remotes/origin/main and HEAD', out)
        self.assertIn('verifier demo-verifier (claude --model claude-opus-5-5) in ', out)
        self.assertIn('status: clean', out)
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertIn('units: landing', packet)
        self.assertIn(str(b), packet)
        self.assertIn('gate app behavioral', packet)
        self.assertIn('landing verification: clean', self.run_sh('land-check', str(self.store), code=0))

    def test_a_landing_packet_runs_each_touched_profiles_landing_commands_and_a_unit_packet_does_not(self):
        self.write('.agents/kitchen.toml', KITCHEN.replace('behavioral = ["true"]', 'behavioral = ["true"]\nlanding = ["true"]'))
        self.commit('declare a landing command')
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.run_sh('verify', str(self.store), '--timeout', '60', '001')
        self.assertNotIn(' landing --role verifier', (self.store / 'verdicts/001-util-v1-packet.md').read_text())
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        packet = (self.store / 'verdicts/landing-v1-packet.md').read_text()
        self.assertRegex(packet, r'gate app behavioral --role verifier --base [0-9a-f]+\n')
        self.assertRegex(packet, r'gate app landing --role verifier --base [0-9a-f]+\n')

    def test_a_clean_landing_verdict_covers_a_unit_inside_its_range(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        head = self.commit('util')
        self.done(b, head)
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.assertIn('unverified: unit 001', self.run_sh('land-check', str(self.store), code=2))
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        for status, tip in (('inconclusive', head), ('clean', init)):
            landing.write_text(f'# Verdict\n\nstatus: {status}\nunits: landing\nrange: {init}..{tip}\n')
            self.assertIn('unverified: unit 001', self.run_sh('land-check', str(self.store), code=2))
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{head[:9]}\n')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(f'covered: unit 001 by the clean landing verdict {landing}', out)
        self.assertIn('land-check: pass', out)

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

    def test_a_clean_landing_verdict_whose_range_line_has_words_after_the_head_covers_a_unit(self):
        init, head, landing = self.landing_range_world()
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{head[:9]} from the packet\n')
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
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {trunk[:9]}..{tip[:9]}\n')
        return landing, tip

    def test_land_check_covers_a_unit_rebased_into_the_stack_by_patch_id(self):
        init, heads = self.two_unit_stack()
        landing, tip = self.land_on_new_trunk(init, heads)
        out = self.run_sh('land-check', str(self.store))
        self.assertRegex(out, rf'covered: unit 001 \(rebased as [0-9a-f]{{9}}\) by the clean landing verdict {landing}')
        self.assertRegex(out, rf'covered: unit 002 \(rebased as {tip[:9]}\) by the clean landing verdict {landing}')
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
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {trunk[:9]}..{tip[:9]}\n')
        return landing

    def test_a_unit_whose_patch_was_later_reverted_in_the_stack_is_neither_covered_nor_accepted_by_verify(self):
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
        self.assertIn(f"land-check: note: unit 001's commits match by patch-id but {reverting[:9]} reverts them", out)
        self.assertNotIn('squashed', out)
        self.assertRegex(out, r'covered: unit 002 \(rebased as ')
        report = self.store / 'reports/002-next.md'
        report.write_text(f'# Report\n\nstatus: done\nhead: {tip}\n')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '002', '--covers', '001', code=2)
        self.assertIn(f"unit 001's commits match by patch-id but {reverting[:9]} reverts them", out)

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
                lines = ([f'line {k}\n' for k in range(1, 11)] + ['line 10 and a half\n']
                         + [f'line {k}\n' if k != 13 else 'line 13 edited\n' for k in range(11, 31)])
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
        self.assertIn(f"land-check: note: unit 001's commits match by patch-id but {reverting[:9]} reverts them", out)
        self.assertNotIn('covered: unit 001', out)
        (self.store / 'reports/002-next.md').write_text(f'# Report\n\nstatus: done\nhead: {reverting}\n')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '002', '--covers', '001', code=2)
        self.assertIn(f"unit 001's commits match by patch-id but {reverting[:9]} reverts them", out)

    def test_a_rebased_unit_stays_covered_when_a_later_unit_edits_its_lines(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        heads = []
        for n, slug in ((1, 'util'), (2, 'next')):
            b = self.brief(f'00{n}', slug, ['src/**', 'tests/**'])
            self.dispatch(b)
            if n == 1:
                self.write('src/shared.py', ''.join(f'line {k}\n' for k in range(1, 13)))
            else:
                self.write('src/shared.py', ''.join(('line 5 edited\n' if k == 5 else 'line 13 added\n' if k == 13 else f'line {k}\n') for k in range(1, 14)))
            heads.append(self.commit(slug))
            self.done(b, heads[-1])
            (self.store / f'reviews/00{n}-{slug}.md').write_text('verdict: accept\n')
        trunk = self.stack_on_a_new_trunk(init, heads)
        tip = self.git('rev-parse', 'HEAD')
        landing = self.write_landing_verdict(trunk, tip)
        out = self.run_sh('land-check', str(self.store))
        self.assertRegex(out, rf'covered: unit 001 \(rebased as [0-9a-f]{{9}}\) by the clean landing verdict {landing}')
        self.assertRegex(out, rf'covered: unit 002 \(rebased as {tip[:9]}\)')
        (self.store / 'reports/002-next.md').write_text(f'# Report\n\nstatus: done\nhead: {tip}\n')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '002', '--covers', '001')
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
        self.assertIn(f"land-check: note: unit 001's commits match by patch-id but {reverting[:9]} reverts them", out)

    def test_a_unit_with_a_commit_and_its_revert_has_no_net_change_and_is_covered_by_its_patch_ids(self):
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

    def test_an_unrelated_identical_one_line_deletion_is_called_reverted_by_the_u0_scan(self):
        """Known limitation (tests/SPEC.md, Decided): the revert scan compares no-context patch-ids, which ignore line numbers. The unit adds a second `dup` line;
        the stack then deletes the first, unrelated `dup`. That commit's patch equals the inverse of the unit's, so land-check says the unit is reverted
        (the conservative, fail-closed answer), though its line is still there."""
        before = 'alpha\ndup\nbeta\ngamma\n'
        unit, made = self.revert_scan_world(before, before + 'dup\n', [('write', 'alpha\nbeta\ngamma\ndup\n')])
        out = self.land_check(2)
        self.assertIn(f"land-check: note: unit 001's commits match by patch-id but {made[-1][:9]} reverts them", out)
        self.assertIn('unverified: unit 001', out)
        self.assertNotIn('covered: unit 001', out)

    def test_a_real_git_revert_after_a_nearby_edit_is_called_reverted(self):
        lines = [f'line {k}\n' for k in range(1, 31)]
        unit_text = ''.join(lines[:10] + ['added\n'] + lines[10:])
        nearby = ''.join(lines[:10] + ['added\n'] + lines[10:12] + ['line 13 edited\n'] + lines[13:])
        unit, made = self.revert_scan_world(''.join(lines), unit_text, [('write', nearby), ('revert', 0)])
        out = self.land_check(2)
        self.assertIn(f"land-check: note: unit 001's commits match by patch-id but {made[-1][:9]} reverts them", out)
        self.assertNotIn('covered: unit 001', out)

    def test_a_block_moved_later_in_the_stack_leaves_the_unit_covered(self):
        """The unit adds three lines at the top; a later stack commit moves them to the bottom. That commit's patch is a deletion and an addition,
        which is not the inverse of the unit's patch, so the scan finds no revert and the unit stays covered."""
        before = ''.join(f'line {k}\n' for k in range(1, 11))
        block = 'm1\nm2\nm3\n'
        unit, made = self.revert_scan_world(before, block + before, [('write', before + block)])
        out = self.land_check(0)
        self.assertRegex(out, r'covered: unit 001 \(rebased as [0-9a-f]{9}\) by the clean landing verdict')
        self.assertNotIn('reverts them', out)

    def test_a_deletion_only_unit_reapplied_after_its_lines_were_restored_is_covered_by_the_newest_replay(self):
        """The unit deletes `gone`; the stack replays it, restores the line, and deletes it again with a commit of the same patch. The newest stack
        commit carrying the unit's patch-id is the last deletion, and nothing after it undoes the unit."""
        before = 'alpha\ngone\nbeta\n'
        after = 'alpha\nbeta\n'
        unit, made = self.revert_scan_world(before, after, [('write', before), ('write', after)])
        out = self.land_check(0)
        self.assertRegex(out, rf'covered: unit 001 \(rebased as {made[-1][:9]}\) by the clean landing verdict')

    def test_a_deletion_only_unit_whose_lines_were_restored_is_called_reverted(self):
        before = 'alpha\ngone\nbeta\n'
        unit, made = self.revert_scan_world(before, 'alpha\nbeta\n', [('write', before)])
        out = self.land_check(2)
        self.assertIn(f"land-check: note: unit 001's commits match by patch-id but {made[-1][:9]} reverts them", out)

    def test_a_unit_reverted_and_then_reapplied_is_covered_by_the_reapplication(self):
        """git revert, then the unit's own commit cherry-picked again: the newest stack commit with the unit's patch-id is the reapplication, and
        the revert before it does not count."""
        before = ''.join(f'line {k}\n' for k in range(1, 11))
        unit_text = before + 'added\n'
        unit, made = self.revert_scan_world(before, unit_text, [('revert', 0), ('write', unit_text)])
        out = self.land_check(0)
        self.assertRegex(out, rf'covered: unit 001 \(rebased as {made[-1][:9]}\) by the clean landing verdict')

    def test_verify_covers_a_rebased_unit_by_patch_id_and_says_so(self):
        init, heads = self.two_unit_stack()
        landing, tip = self.land_on_new_trunk(init, heads)
        trunk = self.git('rev-parse', 'HEAD~2')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        report = self.store / 'reports/002-next.md'
        report.write_text(f'# Report\n\nstatus: done\nhead: {tip}\n')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '002', '--covers', '001')
        self.assertRegex(out, rf"note: unit 001's head is not in {tip[:9]}; its commits match by patch-id \(rebased as [0-9a-f]{{9}}\)")
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
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {base_text or init[:9]}..{h1[:9]}\n')
        b2 = self.brief('002', 'next', ['src/**', 'tests/**'])
        self.dispatch(b2)
        self.write('src/b.py', 'B = 1\n')
        self.done(b2, self.commit('b'))
        (self.store / 'reviews/002-next.md').write_text('verdict: accept\n')
        return landing

    def test_a_unit_committed_after_the_landing_verdict_is_newer_not_squashed(self):
        landing = self.verdict_then_a_newer_unit('')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(f'land-check: note: unit 002 is newer than landing verdict {landing}; verify --landing again', out)
        self.assertNotIn('squashed', out)

    def test_a_landing_verdict_whose_base_does_not_resolve_says_so_instead_of_squashed(self):
        landing = self.verdict_then_a_newer_unit('deadbeef0')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(f'land-check: note: landing verdict {landing} has no base that resolves; unit 002 cannot be matched by patch-id', out)
        self.assertNotIn('squashed', out)

    def test_a_unit_range_with_a_merge_is_not_covered_by_patch_id_and_the_note_says_why(self):
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
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {trunk[:9]}..{tip[:9]}\n')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn("land-check: note: unit 001's range holds a merge commit, so patch-ids do not cover it; verify --landing covers it", out)
        self.assertNotIn('covered:', out)

    def test_the_stack_patch_id_map_is_built_once_per_land_check(self):
        init, heads = self.two_unit_stack()
        landing, tip = self.land_on_new_trunk(init, heads)
        trunk = self.git('rev-parse', 'HEAD~2')
        shim = Path(self.tmp.name) / 'gitlog'
        shim.mkdir()
        log = shim / 'calls'
        (shim / 'git').write_text(f'#!/bin/bash\necho "$*" >>{log}\nexec {shutil.which("git")} "$@"\n')
        (shim / 'git').chmod(0o755)
        self.env['PATH'] = f'{shim}:{self.env["PATH"]}'
        out = self.run_sh('land-check', str(self.store))
        self.assertEqual(out.count('(rebased as'), 2)
        self.assertEqual(log.read_text().count(f'rev-list --reverse --no-merges {trunk}..{tip}'), 1)

    def test_land_check_notes_a_unit_squashed_into_the_stack_and_leaves_it_uncovered(self):
        init, heads = self.two_unit_stack()
        self.land_on_new_trunk(init, heads, squash=True)
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn("land-check: note: unit 001's head is not in the stack and its commits do not match by patch-id (squashed?); verify --landing covers it", out)
        self.assertIn("land-check: note: unit 002's head is not in the stack", out)
        self.assertNotIn('covered:', out)
        self.assertIn('unverified: unit 001', out)

    def test_an_older_landing_verdict_says_how_many_commits_behind_head_it_is(self):
        init, head, landing = self.landing_range_world()
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{head[:9]}\n')
        self.assertNotIn('commits behind HEAD', self.run_sh('land-check', str(self.store)))
        self.write('docs/a.md', 'a\n')
        self.commit('a')
        self.assertIn(f'landing verdict {landing} at {head[:9]}, 1 commit behind HEAD', self.run_sh('land-check', str(self.store)))
        self.write('docs/b.md', 'b\n')
        self.commit('b')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(f'landing verdict {landing} at {head[:9]}, 2 commits behind HEAD', out)

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
        self.assertIn(f'range: {init}..{self.git("rev-parse", "HEAD")}', (self.store / 'verdicts/landing-v2-packet.md').read_text())
        self.assertIn('--parent goes with --landing', self.run_sh('verify', str(self.store), '001', '--parent', 'HEAD', code=1))

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
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--head', 'side')
        landing = self.store / 'verdicts/landing-v1.md'
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{side[:9]}\n')
        out = self.run_sh('land-check', str(self.store), code=0)
        self.assertIn(f"landing verdict {landing} at {side[:9]}, not in HEAD's history (rebased or another branch); verify --landing again", out)
        self.assertNotIn('behind HEAD', out)

    def test_a_recorded_parent_that_is_no_longer_an_ancestor_falls_back_to_the_trunk_merge_base(self):
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
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', unit[:9])
        self.assertEqual(self.state()['landing']['parent'], unit)
        self.git('checkout', '-q', '-b', 'elsewhere', init)
        self.write('src/else.py', 'E = 1\n')
        head = self.commit('elsewhere')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        self.assertIn(f'landing base: recorded parent {unit[:9]} ({unit[:9]}) is no longer an ancestor of {head[:9]}; using the trunk merge-base', out)
        self.assertIn(f'landing base: merge-base of remotes/origin/main and HEAD ({init[:9]})', out)

    def test_parent_is_recorded_only_after_every_option_passes_and_none_clears_it(self):
        init = self.git('rev-list', '--max-parents=0', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 3\n')
        self.write('tests/test_app.py', 'x = 3\n')
        self.done(b, self.commit('util'))
        self.assertIn('--head nope is not a commit', self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', init[:9], '--head', 'nope', code=1))
        self.assertNotIn('landing', self.state())
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', init[:9])
        self.assertEqual(self.state()['landing']['parent'], init)
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', 'none')
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
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--base', init[:9], '--parent', head[:9], code=1)
        self.assertIn(f'--parent {head[:9]} ({head[:9]}) must be an ancestor of {head[:9]} and not the same commit', out)
        self.assertNotIn('landing', self.state())

    def test_a_parent_is_not_recorded_when_the_scratch_worktree_cannot_be_made(self):
        init, head = self.landing_world_for_parent_checks()
        blocked = self.repo / '.git/pstack-scratch/runs-demo/000-landing-verify'
        blocked.parent.mkdir(parents=True)
        blocked.write_text('a file where the worktree goes\n')
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', init[:9], code=2)
        self.assertIn('git worktree add failed', out)
        self.assertNotIn('landing', self.state())
        blocked.unlink()
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', init[:9])
        self.assertEqual(self.state()['landing']['parent'], init)

    def test_land_check_reports_a_stale_recorded_parent_on_stderr(self):
        init, head = self.landing_world_for_parent_checks()
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', head[:9], '--head', head, code=1)
        self.write('src/more.py', 'M = 1\n')
        more = self.commit('more')
        self.run_sh('verify', str(self.store), '--timeout', '60', '--landing', '--parent', head[:9])
        self.assertEqual(self.state()['landing']['parent'], head)
        self.git('checkout', '-q', '-b', 'elsewhere', init)
        self.write('src/else.py', 'E = 1\n')
        elsewhere = self.commit('elsewhere')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn(f'landing base: recorded parent {head[:9]} ({head[:9]}) is no longer an ancestor of {elsewhere[:9]}; using the trunk merge-base', out)

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
        self.assertIn('is not a commit', self.run_sh('verify', str(self.store), '--landing', '--head', 'nope', code=1))
        self.assertIn('--head goes with --landing', self.run_sh('verify', str(self.store), '001', '--head', 'side', code=1))

    def rebased_after_a_landing_verdict(self, trunk_file):
        init, heads = self.two_unit_stack()
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..{heads[-1][:9]}\n')
        self.git('checkout', '-q', '-b', 'rebased', init)
        self.write(trunk_file, 'pytest==9\n')
        trunk = self.commit('trunk moved')
        self.git('update-ref', 'refs/remotes/origin/main', trunk)
        self.git('cherry-pick', *heads)

    def test_land_check_warns_when_a_dependency_manifest_changed_since_the_verified_base(self):
        self.rebased_after_a_landing_verdict('requirements.txt')
        out = self.run_sh('land-check', str(self.store))
        self.assertIn('warning: dependency manifests changed since the verified base: requirements.txt', out)

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
            (self.store / 'verdicts/landing-v1-packet.md').write_text(f'# Verify landing\n\nrange: {verified_base}..{verified_tip}\n')
            landing.write_text('# Verdict\n\nstatus: clean\nunits: landing\nrange: copied wrongly\n')
        else:
            landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {verified_base[:9]}..{verified_tip[:9]}\n')
        self.git('checkout', '-q', '-b', 'rebased', init)
        self.git('update-ref', 'refs/remotes/origin/main', init)
        self.git('cherry-pick', *heads)

    def test_a_manifest_changed_only_on_the_verified_bases_side_still_warns(self):
        self.verified_on_a_base_with_a_manifest_edit()
        out = self.run_sh('land-check', str(self.store), code=0)
        self.assertIn('warning: dependency manifests changed since the verified base: requirements.txt', out)

    def test_a_manifest_renamed_on_the_current_bases_side_warns_through_its_old_path(self):
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
        self.assertIn('warning: dependency manifests changed since the verified base: requirements.txt', out)

    def test_the_manifest_warning_and_coverage_read_one_base_and_head_from_the_verdicts_packet(self):
        """The verdict's own range line is garbled (the verifier copied it wrongly); the packet's is the kitchen's. Coverage and the warning
        both use the packet's, so land-check passes and warns."""
        self.verified_on_a_base_with_a_manifest_edit(packet=True)
        out = self.run_sh('land-check', str(self.store), code=0)
        self.assertIn('warning: dependency manifests changed since the verified base: requirements.txt', out)
        self.assertRegex(out, r'covered: unit 001 \(rebased as ')

    def test_a_verdict_verified_on_a_base_inside_the_stack_does_not_warn_of_the_stacks_own_manifest_edits(self):
        init, heads = self.two_unit_stack()
        self.git('checkout', '-q', '-b', 'stack', init)
        self.write('requirements.txt', 'pytest==9\n')
        lower = self.commit('lower unit edits the manifest')
        self.write('src/upper.py', 'U = 1\n')
        upper = self.commit('upper unit')
        self.git('update-ref', 'refs/remotes/origin/main', init)
        landing = self.store / 'verdicts/landing-v1.md'
        landing.parent.mkdir(exist_ok=True)
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {lower[:9]}..{upper[:9]}\n')
        # The two units' own commits are not on this branch, so land-check refuses them; only the warning is under test.
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertNotIn('dependency manifests', out)

    def test_verify_landing_warns_of_manifests_changed_since_the_previous_landing_verdicts_base(self):
        self.verified_on_a_base_with_a_manifest_edit()
        out = self.run_sh('verify', str(self.store), '--timeout', '60', '--landing')
        self.assertIn('warning: dependency manifests changed since the verified base: requirements.txt', out)

    def test_land_check_says_nothing_of_manifests_when_trunk_moved_another_file(self):
        self.rebased_after_a_landing_verdict('docs/notes.md')
        self.assertNotIn('dependency manifests', self.run_sh('land-check', str(self.store)))

    def test_a_clean_landing_verdict_whose_range_head_does_not_resolve_says_so_and_covers_nothing(self):
        init, head, landing = self.landing_range_world()
        landing.write_text(f'# Verdict\n\nstatus: clean\nunits: landing\nrange: {init[:9]}..deadbeef0\n')
        out = self.run_sh('land-check', str(self.store), code=2)
        self.assertIn(f'land-check: note: landing verdict {landing} range head does not resolve; no unit is covered by it', out)
        self.assertNotIn('covered:', out)
        self.assertIn('unverified: unit 001', out)

    def test_rejected_verdict_drafts_the_fix_brief(self):
        _, out = self.verify_app_unit('reject', 2)
        self.assertIn('Acceptance broke', out)
        verdict = self.store / 'verdicts/001-util-v1.md'
        self.assertIn(f'next: kitchen.sh revise {self.store} {verdict}, then verify the fix with --covers 001', out)
        fix = Path(self.run_sh('revise', str(self.store), str(verdict)).strip())
        text = fix.read_text()
        self.assertEqual(fix.name, '002-util-fix.md')
        self.assertIn(f'- verdict: {verdict}', text)
        self.assertIn('- src/** — scope', text)
        self.assertIn('playbook: bug-fix', text)

    def rejected(self, name, units):
        verdict = self.store / f'verdicts/{name}.md'
        verdict.parent.mkdir(exist_ok=True)
        verdict.write_text(f'# Verdict\n\nstatus: reject\nunits: {units}\n\n## Findings\n\n1. flaky\n')
        return verdict

    def test_a_rejected_landing_verdict_drafts_one_fix_over_the_units_scopes(self):
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
        verdict = self.rejected('landing-v4', 'landing')
        text = Path(self.run_sh('revise', str(self.store), str(verdict)).strip()).read_text()
        self.assertIn('- src/** — scope\n- docs/** — scope\n', text)

    def test_revise_reads_the_last_unit_number_whatever_surrounds_it(self):
        self.brief('001', 'util', ['src/**'])
        verdict = self.rejected('001-util-v1', '(001)')
        self.assertTrue(self.run_sh('revise', str(self.store), str(verdict)).strip().endswith('002-util-fix.md'))

    def test_a_verdict_without_evidence_is_invalid(self):
        _, out = self.verify_app_unit('noevidence', 2)
        self.assertIn('status: invalid (no command output under Evidence)', out)

    def test_land_check_needs_a_verdict_for_every_accepted_unit_and_runs_no_reviewer(self):
        b = self.brief('001', 'util', ['src/**', 'tests/**'])
        self.dispatch(b)
        self.write('src/util.py', 'X = 4\n')
        self.write('tests/test_app.py', 'x = 4\n')
        self.done(b, self.commit('util'))
        (self.store / 'reviews/001-util.md').write_text('verdict: accept\n')
        self.assertIn('unverified: unit 001', self.run_sh('land-check', str(self.store), code=2))
        landing = self.brief('002', 'land', ['src/**'], playbook='opening-a-pr')
        self.assertIn('landing refused', self.dispatch(landing, code=2))
        self.run_sh('verify', str(self.store), '001')
        self.assertIn('land-check: pass (review engine: none)\n', self.run_sh('land-check', str(self.store)))
        self.run_sh('review', str(self.store), '001', code=1)
        self.assertFalse((self.store / 'joo').exists())

    def test_retro_and_counters_agree_on_verified_units(self):
        for n, status, units in [('001-a-v1', 'clean', '001 002'), ('003-c-v1', 'reject', '003'), ('003-c-v2', 'clean', '003')]:
            (self.store / 'verdicts' / f'{n}.md').write_text(f'# V\n\nstatus: {status}\nunits: {units}\n')
        out = self.run_sh('retro', str(self.store))
        self.assertIn('run demo: 0 unit reports, 3 verified units (1 clean verdicts on the first try)', out)
        counters = json.loads(subprocess.run([sys.executable, str(script), 'counters', str(self.store)], env=self.env,
                                             capture_output=True, text=True, check=True).stdout)
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
        path = next((Path(self.env['XDG_STATE_HOME']) / 'pstack/kitchen/repos').glob('*/ledger.tsv'))
        lines = path.read_text().splitlines()
        return lines[0].split('\t'), [dict(zip(lines[0].split('\t'), ln.split('\t'))) for ln in lines[1:]]

    def test_the_ledger_says_where_and_what(self):
        b = self.brief('001', 'util', ['src/lib/**', 'tests/**'])
        self.dispatch(b)
        self.write('FAIL', 'x\n')
        self.write('src/lib/util.py', 'X = 7\n')
        sha = self.commit('broken')
        self.run_sh('step', str(self.store), sha, 'one', code=2)
        (self.store / 'verdicts/001-util-v1.md').write_text(
            '# Verdict\n\nstatus: reject\nunits: 001\n\n## Findings\n\n1. Acceptance 2: the\tcache is never cleared\n')
        (self.store / 'steers').mkdir(exist_ok=True)
        (self.store / 'steers/001-util-s1.md').write_text(
            '# Steer\n\nkind: narrow\n\n## Direction\n\nLeave the CLI alone; only the library changes.\n')
        self.run_sh('catch', str(self.store), 'review', 'missed a caller in api.py', '001')
        self.run_sh('catch', str(self.store), 'review', 'unit not given')
        self.run_sh('catch', str(self.store), 'review', 'bad unit', 'util', code=1)
        self.assertIn('no brief for unit 009', self.run_sh('catch', str(self.store), 'review', 'x', '009', code=1))
        self.run_sh('catch', str(self.store), 'gate', '--force push slipped through')
        self.run_sh('retro', str(self.store))
        head, rows = self.ledger()
        self.assertEqual(head, ['ts', 'run', 'kind', 'class', 'detail', 'unit', 'area', 'finding', 'match'])
        by = {r['kind']: r for r in rows}
        # One row per failing check: the command (no task runner names one), then the policy rule.
        self.assertEqual([(r['unit'], r['area'], r['class'], r['match'], r['finding']) for r in rows if r['kind'] == 'gate-fail'],
                         [('001', 'src/lib', 'app', 'fast test ! -e FAIL', 'test ! -e FAIL'),
                          ('001', 'src/lib', 'policy', 'test-touch', 'src/lib/util.py: [test-touch] profile app requires tests: '
                                                                   '1 source files changed and no file matching tests/** (.agents/kitchen.toml)')])
        self.assertEqual((by['reject']['area'], by['reject']['finding']), ('src/lib', 'Acceptance 2: the cache is never cleared'))
        self.assertEqual((by['steer']['finding'], by['steer']['match']),
                         ('narrow: Leave the CLI alone; only the library changes.', 'narrow'))
        catches = [r for r in rows if r['kind'] == 'catch']
        self.assertEqual([(c['area'], c['finding']) for c in catches],
                         [('src/lib', 'missed a caller in api.py'), ('', 'unit not given'), ('', '--force push slipped through')])
        # Retro again: the run's rows are replaced, the catches kept.
        out = self.run_sh('retro', str(self.store))
        self.assertEqual(len(self.ledger()[1]), len(rows))
        self.assertIn('1 runs (demo): catch review src/lib', out)

    def test_an_old_ledger_gains_the_new_columns(self):
        state = Path(subprocess.run([sys.executable, str(script), '--repo', str(self.repo), 'statedir'], env=self.env,
                                    capture_output=True, text=True, check=True).stdout.strip())
        (state / 'ledger.tsv').write_text('ts\trun\tkind\tclass\tdetail\n2026-01-01T00:00:00Z\told\tsteer\tbrief\t004-x-s1\n')
        self.run_sh('catch', str(self.store), 'gate', 'lint missed it')
        head, rows = self.ledger()
        self.assertEqual(len(head), 9)
        self.assertEqual([(r['run'], r['kind'], r['area']) for r in rows], [('old', 'steer', ''), ('demo', 'catch', '')])
