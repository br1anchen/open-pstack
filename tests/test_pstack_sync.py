import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

# git and the scripts report resolved paths; on macOS the temp dir is under
# /var, a link to /private/var, so tests work in the resolved one.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())

spec = importlib.util.spec_from_file_location(
    'pstack_sync', Path(__file__).resolve().parents[1] / 'scripts/pstack-sync.py'
)
if spec is None or spec.loader is None:
    raise ImportError('cannot load scripts/pstack-sync.py')
sync_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_module)


class SyncTests(unittest.TestCase):
    def test_preserves_conflicts_and_unrelated_skills_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, home = root / 'source', root / 'home'
            (source / 'pstack').mkdir(parents=True)
            (source / 'pstack/SKILL.md').write_text('new')
            shared = home / '.agents/skills'
            (shared / 'pstack').mkdir(parents=True)
            (shared / 'pstack/SKILL.md').write_text('local edits')
            (shared / 'unrelated').mkdir()
            (home / '.claude').mkdir()
            (home / '.claude/skills').symlink_to(shared, target_is_directory=True)
            dry = sync_module.sync(source, home)
            self.assertEqual(len(dry['changed']), 2)
            self.assertEqual((shared / 'pstack/SKILL.md').read_text(), 'local edits')
            result = sync_module.sync(source, home, True)
            self.assertEqual(len(result['changed']), 2)
            self.assertEqual(
                (Path(result['backup']) / '.agents/skills/pstack/SKILL.md').read_text(),
                'local edits',
            )
            self.assertTrue((shared / 'unrelated').is_dir())
            for path in sync_module.ROOTS:
                self.assertEqual((home / path / 'pstack').resolve(), source / 'pstack')
            self.assertEqual(sync_module.sync(source, home, True)['changed'], [])
            # pi reads ~/.agents/skills, so nothing is linked under ~/.pi.
            self.assertFalse((home / '.pi').exists())

    def test_removes_its_old_pi_links_and_keeps_others(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, home = root / 'source', root / 'home'
            (source / 'pstack').mkdir(parents=True)
            (source / 'pstack/SKILL.md').write_text('new')
            pi = home / '.pi/agent/skills'
            pi.mkdir(parents=True)
            (pi / 'pstack').symlink_to(source / 'pstack')
            (source / 'pstack-pair').mkdir()
            (source / 'pstack-pair/SKILL.md').write_text('new')
            # An older pinned copy of the tree, and a skill of the user's own that shares no pstack name.
            pinned = root / 'pinned/skills'
            for name in ('pstack', 'pstack-pair'):
                (pinned / name).mkdir(parents=True)
                (pinned / name / 'SKILL.md').write_text('old')
            (pi / 'pstack-pair').symlink_to(pinned / 'pstack-pair')
            (root / 'mine').mkdir()
            (pi / 'mine').symlink_to(root / 'mine')
            dry = sync_module.sync(source, home)
            self.assertIn(f'{pi / "pstack"} (removed)', dry['changed'])
            self.assertIn(f'{pi / "pstack-pair"} (removed)', dry['changed'])
            self.assertTrue((pi / 'pstack').is_symlink())
            result = sync_module.sync(source, home, True)
            self.assertFalse((pi / 'pstack').is_symlink())
            self.assertFalse((pi / 'pstack-pair').is_symlink())
            self.assertTrue(
                (Path(result['backup']) / '.pi/agent/skills/pstack-pair').is_symlink()
            )
            self.assertTrue((pi / 'mine').is_symlink())

    def test_a_pi_skills_dir_that_aliases_the_shared_one_keeps_its_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, home = root / 'source', root / 'home'
            (source / 'pstack').mkdir(parents=True)
            (source / 'pstack/SKILL.md').write_text('new')
            (home / '.agents/skills').mkdir(parents=True)
            (home / '.pi/agent').mkdir(parents=True)
            (home / '.pi/agent/skills').symlink_to(
                home / '.agents/skills', target_is_directory=True
            )
            sync_module.sync(source, home, True)
            self.assertEqual(
                (home / '.agents/skills/pstack').resolve(), source / 'pstack'
            )
            self.assertEqual(sync_module.sync(source, home, True)['changed'], [])

    def test_replaces_broken_links_and_updates_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, home = root / 'source', root / 'home'
            (source / 'pstack').mkdir(parents=True)
            (source / 'pstack/SKILL.md').write_text('new')
            (home / '.grok/skills').mkdir(parents=True)
            (home / '.grok/skills/pstack').symlink_to(root / 'missing')
            result = sync_module.sync(source, home, True)
            self.assertTrue(
                (Path(result['backup']) / '.grok/skills/pstack').is_symlink()
            )
            newer = root / 'newer'
            source.rename(newer)
            sync_module.sync(newer, home, True)
            self.assertEqual((home / '.grok/skills/pstack').resolve(), newer / 'pstack')


class WorktreeAuditTests(unittest.TestCase):
    def test_spaces_and_absent_transcripts_do_not_allow_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='pstack audit ') as directory:
            root = Path(directory)
            repo, worktree = root / 'repo', root / 'work tree'
            repo.mkdir()

            def git(*args):
                subprocess.run(
                    ['git', '-C', str(repo), *args],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )

            git('init', '-b', 'main')
            git(
                '-c',
                'user.name=Test',
                '-c',
                'user.email=test@example.com',
                'commit',
                '--allow-empty',
                '-m',
                'fixture',
            )
            git('worktree', 'add', '-b', 'topic', str(worktree))
            binaries = root / 'bin'
            binaries.mkdir()
            gh = binaries / 'gh'
            gh.write_text('#!/bin/sh\nprintf "[]\\n"\n')
            gh.chmod(0o755)
            script = (
                Path(__file__).resolve().parents[1]
                / 'skills/poteto-mode/scripts/worktree-audit.sh'
            )
            env = dict(
                os.environ,
                PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                PSTACK_TRANSCRIPTS_DIR='',
            )
            result = subprocess.run(
                ['bash', str(script), '-b', 'main', str(repo)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            self.assertIn('review-no-transcripts', result.stdout)
            self.assertIn(str(worktree), result.stdout)
            self.assertTrue(worktree.exists())
            transcripts = root / 'transcripts'
            transcripts.mkdir()
            (transcripts / 'session.jsonl').write_text(
                '{"cwd": ' + repr(str(worktree)).replace("'", '"') + '}\n'
            )
            env['PSTACK_TRANSCRIPTS_DIR'] = str(transcripts)
            recent = subprocess.run(
                ['bash', str(script), '-b', 'main', str(repo)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            self.assertIn('verify-recent-chat', recent.stdout)


if __name__ == '__main__':
    unittest.main()
