"""The Herdr plugin manifest, the Rex actions and plugins/pstack-action.py, against the fake herdr and fake rex."""

import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tests'))
from test_pstack_core import LEAKED_PREFIXES  # noqa: E402

script = root / 'plugins/pstack-action.py'


class PluginTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.fake = self.path / 'fake'
        (self.fake / 'rex').mkdir(parents=True)
        (self.path / 'home').mkdir()
        self.env = {
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith(LEAKED_PREFIXES) and k != 'REX_CONFIG'
            },
            'PATH': f'{root / "tests/pstack-pair/bin"}:{os.environ["PATH"]}',
            'FAKE': str(self.fake),
            'HOME': str(self.path / 'home'),
            'XDG_CONFIG_HOME': str(self.path / 'config'),
            'XDG_STATE_HOME': str(self.path / 'state'),
            'PAIR_REX_POLL_S': '0',
        }

    def action(self, *args, **env):
        return subprocess.run(
            [sys.executable, str(script), *args],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def calls(self):
        log = self.fake / 'calls.log'
        return log.read_text().splitlines() if log.exists() else []

    def test_the_herdr_manifest_has_the_required_fields_and_runs_only_files_it_ships(
        self,
    ):
        manifest = root / 'plugins/herdr/herdr-plugin.toml'
        doc = tomllib.loads(manifest.read_text())
        for key in ('id', 'name', 'version', 'min_herdr_version', 'platforms'):
            self.assertIn(key, doc)
        entries = doc['actions'] + doc['panes']
        self.assertEqual(
            sorted(e['id'] for e in entries),
            ['fleet', 'kitchen', 'pair', 'sync', 'trio'],
        )
        for entry in entries:
            self.assertEqual(entry['command'][:2], ['python3', 'run.py'])
        self.assertTrue((manifest.parent / 'run.py').is_file())

    def test_the_herdr_launcher_finds_the_checkout_through_the_linked_skill(self):
        alone = self.path / 'installed'
        alone.mkdir()
        (alone / 'run.py').write_text((root / 'plugins/herdr/run.py').read_text())
        launch = lambda: subprocess.run(
            [sys.executable, str(alone / 'run.py'), 'install-rex', '--print'],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        r = launch()
        self.assertEqual(r.returncode, 1)
        self.assertIn('no pstack checkout found', r.stderr)
        skills = self.path / 'home/.agents/skills'
        skills.mkdir(parents=True)
        (skills / 'pstack-pair').symlink_to(root / 'skills/pstack-pair')
        r = launch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-- pstack begin', r.stdout)

    def test_a_herdr_action_prompts_the_agent_in_the_focused_pane(self):
        r = self.action('prompt', '/pstack-kitchen', HERDR_ENV='1', HERDR_PANE_ID='p3')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('agent prompt p3 /pstack-kitchen', self.calls())
        self.assertEqual(r.stdout, 'sent /pstack-kitchen to p3\n')

    def test_a_rex_action_prompts_the_agent_in_its_block_and_refuses_a_bare_shell(self):
        block = self.fake / 'rex/blocks/block_b7'
        block.mkdir(parents=True)
        (block / 'fg').write_text('100 zsh\n')
        r = self.action(
            'prompt', '--target', 'block:b7', '/pstack-pair', PSTACK_MUX='rex'
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn('agent_not_ready', r.stderr)
        (block / 'fg').write_text('200 claude\n')
        (block / 'state').write_text('idle claude-code t1\n')
        r = self.action(
            'prompt', '--target', 'block:b7', '/pstack-pair', PSTACK_MUX='rex'
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('rex send -b block:b7 -- /pstack-pair', self.calls())

    def test_install_rex_writes_one_marked_block_backs_up_and_uninstall_removes_it(
        self,
    ):
        init = self.path / 'config/rex/init.lua'
        init.parent.mkdir(parents=True)
        init.write_text('rex.bind("cmd+d", "pane.split")\n')
        (self.path / 'home/.pi/agent/extensions').mkdir(parents=True)
        for _ in range(2):
            r = self.action('install-rex')
            self.assertEqual(r.returncode, 0, r.stderr)
        text = init.read_text()
        self.assertEqual(text.count('-- pstack begin'), 1)
        self.assertTrue(
            text.startswith('rex.bind("cmd+d", "pane.split")\n\n-- pstack begin')
        )
        self.assertIn(f'dofile("{root}/plugins/rex/pstack.lua")("{root}")', text)
        self.assertEqual(len(list(init.parent.glob('init.lua.pstack-*'))), 1)
        link = self.path / 'home/.pi/agent/extensions/pstack-program-status.ts'
        self.assertEqual(
            link.resolve(), root / 'plugins/rex/agents/pi-program-status.ts'
        )
        self.assertEqual(self.calls().count('rex config reload'), 2)
        r = self.action('install-rex', '--uninstall')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(init.read_text(), 'rex.bind("cmd+d", "pane.split")\n')
        self.assertFalse(link.is_symlink())

    def test_install_rex_refuses_a_block_whose_end_marker_is_gone(self):
        init = self.path / 'config/rex/init.lua'
        init.parent.mkdir(parents=True)
        init.write_text(
            '-- pstack begin (written by plugins/pstack-action.py install-rex; remove with --uninstall)\nmine()\n'
        )
        r = self.action('install-rex')
        self.assertEqual(r.returncode, 1)
        self.assertIn('no "-- pstack end" line', r.stderr)
        self.assertIn('mine()', init.read_text())

    def test_install_rex_replaces_a_dangling_pi_extension_link(self):
        ext = self.path / 'home/.pi/agent/extensions'
        ext.mkdir(parents=True)
        (ext / 'pstack-program-status.ts').symlink_to(self.path / 'gone.ts')
        r = self.action('install-rex')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            (ext / 'pstack-program-status.ts').resolve(),
            root / 'plugins/rex/agents/pi-program-status.ts',
        )

    def test_the_lua_block_quotes_a_path_with_quotes_and_backslashes(self):
        sys.path.insert(0, str(root / 'plugins'))
        try:
            import importlib

            action = importlib.import_module('pstack-action')
        finally:
            sys.path.pop(0)
        self.assertEqual(action.lua_string('a"b\\c'), '"a\\"b\\\\c"')

    def test_install_rex_print_shows_the_block_and_changes_nothing(self):
        r = self.action('install-rex', '--print')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-- pstack begin', r.stdout)
        self.assertFalse((self.path / 'config').exists())
        self.assertEqual(self.calls(), [])

    def test_the_rex_actions_name_each_workflow_and_run_the_shared_script(self):
        lua = (root / 'plugins/rex/pstack.lua').read_text()
        for name in ('"pstack_" .. w', 'pstack_fleet', 'pstack_sync'):
            self.assertIn(name, lua)
        self.assertIn('"/plugins/pstack-action.py"', lua)
        self.assertIn('PSTACK_MUX = "rex"', lua)


if __name__ == '__main__':
    unittest.main()
