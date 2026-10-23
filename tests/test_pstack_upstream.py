import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# git and the scripts report resolved paths; on macOS the temp dir is under
# /var, a link to /private/var, so tests work in the resolved one.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/pstack-upstream.py'
spec = importlib.util.spec_from_file_location('pstack_upstream', SCRIPT)
pu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pu)

PINNED = 'e43c7ee26e0038c6c1fa8380dd34ce86ff94cb2a'
# The commit whose skills/ tree is 0.15.9 (e43c7ee) adapted, before any update was applied.
TREE_AT = '14f6bdc16b9a34c91cbe5bf29850be68b34406bd'
# audit's account of this repo against the pinned revision, reviewed file by
# file. A change to adapt() that hides one of these edits drops it from the
# list and fails test_real_checkout_audit_names_every_customization.
CUSTOMIZED_AT_PINNED = [
    'skills/architect/SKILL.md',
    'skills/arena/SKILL.md',
    'skills/automate-me/SKILL.md',
    'skills/benchmark-checklist/SKILL.md',
    'skills/create-verification-skill/SKILL.md',
    'skills/how/SKILL.md',
    'skills/interrogate/SKILL.md',
    'skills/maintain-verification-skill/SKILL.md',
    'skills/make-bot-ui/SKILL.md',
    'skills/poteto-mode/playbooks/bug-fix.md',
    'skills/poteto-mode/scripts/worktree-audit.sh',
    'skills/reflect/SKILL.md',
    'skills/reflect/references/divergent-reviewer.md',
    'skills/reflect/references/judgment-reviewer.md',
    'skills/reflect/references/tooling-reviewer.md',
    'skills/reproduce-and-fix-issues/SKILL.md',
    'skills/reproduce-and-fix-issues/references/control-adapter.md',
    'skills/reproduce-and-fix-issues/references/feature-map.example.md',
    'skills/setup-benny/SKILL.md',
    'skills/setup-pstack/SKILL.md',
    'skills/swarm/SKILL.md',
    'skills/triage-issue-reports/references/routing.example.md',
    'skills/why/SKILL.md',
]

PINNED_0_15_29 = '699e1e7'
# The commit whose skills/ tree is 0.15.29 (699e1e7) taken, adapted and hand-edited (unit 039).
TREE_AT_0_15_29 = '73c3f9bb5594d943af7ddbdfdd6ad723fa90843c'
CUSTOMIZED_AT_0_15_29 = [
    'skills/architect/SKILL.md',
    'skills/arena/SKILL.md',
    'skills/automate-me/SKILL.md',
    'skills/benchmark-checklist/SKILL.md',
    'skills/create-formal-verification-skill/SKILL.md',
    'skills/create-verification-skill/SKILL.md',
    'skills/how/SKILL.md',
    'skills/interrogate/SKILL.md',
    'skills/ketchup/SKILL.md',
    'skills/maintain-formal-verification-skill/SKILL.md',
    'skills/maintain-verification-skill/SKILL.md',
    'skills/make-bot-ui/SKILL.md',
    'skills/poteto-help/SKILL.md',
    'skills/poteto-mode/playbooks/bug-fix.md',
    'skills/poteto-mode/scripts/worktree-audit.sh',
    'skills/reflect/SKILL.md',
    'skills/reflect/references/divergent-reviewer.md',
    'skills/reflect/references/judgment-reviewer.md',
    'skills/reflect/references/tooling-reviewer.md',
    'skills/reproduce-and-fix-issues/SKILL.md',
    'skills/reproduce-and-fix-issues/references/control-adapter.md',
    'skills/reproduce-and-fix-issues/references/feature-map.example.md',
    'skills/setup-benny/SKILL.md',
    'skills/setup-pstack/SKILL.md',
    'skills/swarm/SKILL.md',
    'skills/triage-issue-reports/references/routing.example.md',
    'skills/why/SKILL.md',
]


def git(cwd, *args):
    return (
        subprocess.run(
            [
                'git',
                '-c',
                'user.name=t',
                '-c',
                'user.email=t@t',
                '-c',
                'commit.gpgsign=false',
                *args,
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )


def upstream_skill(name, body='Body.', keys='disable-model-invocation: true\n'):
    return (
        f'---\nname: {name}\ndescription: A skill.\n{keys}---\n\n# {name}\n\n{body}\n'
    )


def commit_upstream(repo, files, version='1.0.0'):
    """Replace pstack/ in the fixture upstream with `files` and commit it."""
    repo = Path(repo)
    if not (repo / '.git').exists():
        git(repo, 'init', '-q')
    git(repo, 'rm', '-rq', '--ignore-unmatch', 'pstack')
    files = {
        'pstack/plugin.json': json.dumps({'name': 'pstack', 'version': version}),
        **files,
    }
    for path, content in files.items():
        mode = 0o644
        if isinstance(content, tuple):
            content, mode = content
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content if isinstance(content, bytes) else content.encode())
        os.chmod(file, mode)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '--allow-empty', '-m', f'upstream {version}')
    return git(repo, 'rev-parse', 'HEAD')


def make_local(root, base, files, skills, **manifest):
    root = Path(root)
    git(root, 'init', '-q') if not (root / '.git').exists() else None
    for path, content in files.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content if isinstance(content, bytes) else content.encode())
    data = {
        'upstream': {'repository': 'x', 'revision': base, 'version': '1.0.0'},
        'skills': skills,
        'excluded': {},
        'watch': {},
        **manifest,
    }
    (root / 'sources.json').write_text(json.dumps(data, indent=2) + '\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'local')


class AdaptTests(unittest.TestCase):
    def test_skill_md_gets_local_name_loses_cursor_keys_and_gains_the_adapter(self):
        upstream = (
            '---\nname: Poteto Mode\ndescription: "Multi\n  line."\ndisable-model-invocation: true\n'
            'mode: true\nicon: crown\ncolor: yellow\nreminder: New task?\n  and more\n---\n\n# Body\n'
        )
        expected = (
            '---\nname: poteto-mode\ndescription: "Multi\n  line."\n---\n\n'
            + pu.ADAPTER
            + '\n\n\n# Body\n'
        )
        self.assertEqual(
            pu.adapt('poteto-mode', 'SKILL.md', upstream.encode()), expected.encode()
        )

    def test_the_pstack_entry_point_gets_no_adapter_and_other_files_pass_through(self):
        skill = b'---\nname: pstack\ndescription: d\n---\n\n# P\n'
        self.assertEqual(pu.adapt('pstack', 'SKILL.md', skill), skill)
        raw = b'---\nname: Not Frontmatter\n---\n\x00\xff'
        self.assertEqual(pu.adapt('x', 'references/a.md', raw), raw)
        self.assertEqual(
            pu.adapt('x', 'SKILL.md', b'no frontmatter\n'), b'no frontmatter\n'
        )

    def test_body_whitespace_is_never_normalized(self):
        text = '---\nname: x\ndescription: d\n---\n\n\n\n# T  \n\n\n'
        out = pu.adapt('y', 'SKILL.md', text.encode()).decode()
        self.assertTrue(out.endswith('\n\n\n\n\n# T  \n\n\n'))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.up_repo = Path(self.tmp.name) / 'up'
        self.local = Path(self.tmp.name) / 'local'
        self.up_repo.mkdir()
        self.local.mkdir()

    def test_audit_names_customized_absent_local_only_watched_and_unmapped(self):
        base = commit_upstream(
            self.up_repo,
            {
                'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
                'pstack/skills/alpha/references/r.md': 'upstream r\n',
                'pstack/skills/alpha/references/gone.md': 'present upstream\n',
                'pstack/skills/beta/SKILL.md': upstream_skill('beta'),
                'pstack/skills/tdd/SKILL.md': upstream_skill('tdd'),
                'pstack/skills/stray/SKILL.md': upstream_skill('stray'),
            },
        )
        make_local(
            self.local,
            base,
            {
                'skills/alpha/SKILL.md': pu.adapt(
                    'alpha', 'SKILL.md', upstream_skill('alpha').encode()
                ),
                'skills/alpha/references/r.md': 'upstream r\nlocal line\n',
                'skills/alpha/references/mine.md': 'ours\n',
                'skills/beta/SKILL.md': pu.adapt(
                    'beta', 'SKILL.md', upstream_skill('beta').encode()
                ),
                'skills/pstack-tdd/SKILL.md': 'ours\n',
                'skills/own/SKILL.md': 'ours\n',
            },
            {
                'alpha': 'pstack/skills/alpha',
                'beta': 'pstack/skills/beta',
                'pstack-tdd': 'local customization',
                'own': 'local skill',
            },
            watch={'pstack-tdd': 'pstack/skills/tdd'},
            excluded={'nothing': 'x'},
        )
        tree = pu.Tree(self.local)
        report = pu.audit(tree, pu.Upstream(self.up_repo), base)
        self.assertEqual(report['customized'], [('skills/alpha/references/r.md', 1, 0)])
        self.assertEqual(report['absent'], ['skills/alpha/references/gone.md'])
        self.assertEqual(
            report['local_only_files'], ['skills/alpha/references/mine.md']
        )
        self.assertEqual(report['local_only_skills'], ['own', 'pstack-tdd'])
        self.assertEqual(report['watched'], {'pstack-tdd': 'pstack/skills/tdd'})
        self.assertEqual(report['unmapped'], ['stray'])

    def test_audit_command_prints_the_report(self):
        base = commit_upstream(
            self.up_repo, {'pstack/skills/alpha/SKILL.md': upstream_skill('alpha')}
        )
        make_local(
            self.local,
            base,
            {'skills/alpha/SKILL.md': 'changed\n'},
            {'alpha': 'pstack/skills/alpha'},
        )
        result = subprocess.run(
            [
                'python3',
                str(SCRIPT),
                '--root',
                str(self.local),
                'audit',
                '--upstream',
                str(self.up_repo),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            'customized upstream files (1):\n  skills/alpha/SKILL.md  +', result.stdout
        )


def local_from(files, renames=None):
    """Our pristine copy of upstream `files`: what adapt() makes of each mapped file."""
    out = {}
    for path, content in files.items():
        found = pu.skill_of(path)
        if found:
            data = content[0] if isinstance(content, tuple) else content
            data = data if isinstance(data, bytes) else data.encode()
            out[f'skills/{found[1]}/{found[2]}'] = pu.adapt(found[1], found[2], data)
    return out


class UpdateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.up_repo = Path(self.tmp.name) / 'up'
        self.local = Path(self.tmp.name) / 'local'
        self.up_repo.mkdir()
        self.local.mkdir()

    def skills(self, *names):
        return {name: f'pstack/skills/{name}' for name in names}

    def start(
        self, base_files, target_files, local_files=None, skills=('alpha',), **manifest
    ):
        """Fixture upstream with two commits and a local repo pinned at the first."""
        self.base = commit_upstream(self.up_repo, base_files, '1.0.0')
        self.to = commit_upstream(self.up_repo, target_files, '2.0.0')
        local = (
            local_from(base_files)
            if local_files is None
            else {**local_from(base_files), **local_files}
        )
        make_local(self.local, self.base, local, self.skills(*skills), **manifest)

    def cli(self, command, *args, to=None):
        argv = ['--root', str(self.local), command, '--upstream', str(self.up_repo)]
        if command != 'audit':
            argv += ['--to', to or self.to]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pu.main(argv + list(args))
        return code, out.getvalue(), err.getvalue()

    def manifest(self):
        return json.loads((self.local / 'sources.json').read_text())

    def status(self):
        return git(self.local, 'status', '--porcelain')

    def read(self, path):
        return (self.local / path).read_text()

    def snapshot(self):
        return {
            str(p.relative_to(self.local)): p.read_bytes()
            for p in sorted(self.local.rglob('*'))
            if p.is_file() and '.git' not in p.parts
        }


MERGE_BASE = ''.join(f'line {i}\n' for i in range(1, 11))
MERGE_THEIRS = MERGE_BASE.replace('line 9\n', 'line 9 upstream\n')
MERGE_OURS = MERGE_BASE.replace('line 2\n', 'line 2 local\n')
CONFLICT_BASE = 'one\ntwo\nthree\n'


class PlanApplyTests(UpdateCase):
    def mixed(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/ff.md': 'one\n',
            'pstack/skills/alpha/merge.md': MERGE_BASE,
            'pstack/skills/alpha/conflict.md': CONFLICT_BASE,
        }
        target = {
            **base,
            'pstack/skills/alpha/ff.md': 'one\ntwo\n',
            'pstack/skills/alpha/merge.md': MERGE_THEIRS,
            'pstack/skills/alpha/conflict.md': 'one\ntwo upstream\nthree\n',
            'pstack/skills/newbie/SKILL.md': upstream_skill('newbie'),
            'pstack/skills/newbie/run.sh': ('#!/bin/sh\n', 0o755),
        }
        self.start(
            base,
            target,
            {
                'skills/alpha/merge.md': MERGE_OURS,
                'skills/alpha/conflict.md': 'one\ntwo local\nthree\n',
            },
        )

    def test_plan_prints_one_row_per_change_and_writes_nothing(self):
        self.mixed()
        before = self.snapshot()
        code, out, _ = self.cli('plan')
        self.assertEqual(code, 0)
        rows = [
            line.split()[:2]
            for line in out.splitlines()
            if line.startswith(('add', 'fast-forward', 'merge', 'conflict'))
        ]
        self.assertEqual(
            sorted(rows),
            [
                ['add', 'skills/newbie/SKILL.md'],
                ['add', 'skills/newbie/run.sh'],
                ['conflict', 'skills/alpha/conflict.md'],
                ['fast-forward', 'skills/alpha/ff.md'],
                ['merge', 'skills/alpha/merge.md'],
            ],
        )
        self.assertIn('summary: 2 add, 1 conflict, 1 fast-forward, 1 merge', out)
        self.assertEqual((self.snapshot(), self.status()), (before, ''))

    def test_conflict_then_resolution_then_finalize_then_nothing_to_do(self):
        self.mixed()
        code, out, _ = self.cli('apply')
        self.assertEqual(code, 3, out)
        self.assertEqual(self.read('skills/alpha/ff.md'), 'one\ntwo\n')
        merged = self.read('skills/alpha/merge.md')
        self.assertIn('line 2 local', merged)
        self.assertIn('line 9 upstream', merged)
        marked = self.read('skills/alpha/conflict.md')
        for label in (
            '<<<<<<< local',
            f'||||||| upstream {self.base[:7]}',
            f'>>>>>>> upstream {self.to[:7]}',
        ):
            self.assertIn(label, marked)
        upstream = self.manifest()['upstream']
        self.assertEqual(
            (upstream['revision'], upstream['version']), (self.base, '1.0.0')
        )
        self.assertEqual(upstream['pending']['revision'], self.to)
        self.assertEqual(upstream['pending']['conflicts'], ['skills/alpha/conflict.md'])
        self.assertEqual(self.manifest()['skills']['newbie'], 'pstack/skills/newbie')
        # Same target while markers remain, and any other target, are refused.
        self.assertEqual(self.cli('apply')[0], 3)
        other = commit_upstream(
            self.up_repo,
            {'pstack/skills/alpha/SKILL.md': upstream_skill('alpha')},
            '3.0.0',
        )
        code, _, err = self.cli('apply', to=other)
        self.assertEqual(code, 2)
        self.assertIn('unresolved', err)
        # A missing conflicted file is an error, not a resolution.
        (self.local / 'skills/alpha/conflict.md').rename(self.local / 'moved.md')
        self.assertEqual(self.cli('apply')[0], 2)
        (self.local / 'moved.md').rename(self.local / 'skills/alpha/conflict.md')
        # Resolve without committing; finalize keeps the new skill and the version.
        (self.local / 'skills/alpha/conflict.md').write_text('one\ntwo both\nthree\n')
        code, out, _ = self.cli('apply')
        self.assertEqual(code, 0, out)
        upstream = self.manifest()['upstream']
        self.assertEqual(
            (upstream['revision'], upstream['version'], 'pending' in upstream),
            (self.to, '2.0.0', False),
        )
        self.assertEqual(
            self.read('skills/alpha/conflict.md'), 'one\ntwo both\nthree\n'
        )
        self.assertEqual(
            oct((self.local / 'skills/newbie/run.sh').stat().st_mode & 0o777), '0o755'
        )
        git(self.local, 'add', '-A')
        git(self.local, 'commit', '-q', '-m', 'update')
        after = self.snapshot()
        code, out, _ = self.cli('apply')
        self.assertEqual(
            (code, 'nothing to do' in out, self.snapshot()), (0, True, after)
        )
        self.assertIn('nothing to do', self.cli('plan')[1])

    def test_pending_may_name_an_accepted_deletion(self):
        self.mixed()
        self.cli('apply')
        manifest = self.manifest()
        manifest['upstream']['pending']['deleted'] = ['skills/alpha/conflict.md']
        (self.local / 'sources.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (self.local / 'skills/alpha/conflict.md').unlink()
        self.assertEqual(self.cli('apply')[0], 0)
        self.assertNotIn('pending', self.manifest()['upstream'])

    def test_merge_file_failure_writes_nothing_and_keeps_the_pin(self):
        self.mixed()
        before = self.snapshot()
        with mock.patch.object(
            pu,
            'merge_files',
            side_effect=pu.Stop(2, 'git merge-file failed with exit 255'),
        ):
            code, _, err = self.cli('apply')
        self.assertEqual((code, self.snapshot(), self.status()), (2, before, ''))
        self.assertIn('exit 255', err)

    def test_merge_file_exit_codes(self):
        missing = [str(Path(self.tmp.name) / n) for n in ('a', 'b', 'c')]
        real = subprocess.run(
            ['git', 'merge-file', '-p', *missing], capture_output=True
        ).returncode
        self.assertNotIn(real, range(0, 128))
        with self.assertRaises(pu.Stop) as caught:
            pu.merge_files(*missing, self.tmp.name, self.tmp.name)
        self.assertEqual(caught.exception.code, 2)
        for code in (-9, 128, 255):
            with mock.patch.object(
                subprocess,
                'run',
                return_value=subprocess.CompletedProcess([], code, b'', b'boom'),
            ):
                with self.assertRaises(pu.Stop):
                    pu.merge_files('a', 'b', 'c', 'r1', 'r2')
        for code in (0, 1, 127):
            with mock.patch.object(
                subprocess,
                'run',
                return_value=subprocess.CompletedProcess([], code, b'out', b''),
            ):
                self.assertEqual(
                    pu.merge_files('a', 'b', 'c', 'r1', 'r2'), (b'out', code)
                )

    def test_dirty_affected_path_is_refused_but_unrelated_dirt_is_not(self):
        self.mixed()
        (self.local / 'skills/alpha/ff.md').write_text('one\nlocal edit\n')
        (self.local / 'README.md').write_text('unrelated\n')
        git(self.local, 'add', 'README.md')
        git(self.local, 'commit', '-q', '-m', 'readme')
        (self.local / 'README.md').write_text('unrelated, edited\n')
        code, _, err = self.cli('apply')
        self.assertEqual(code, 2)
        self.assertIn('skills/alpha/ff.md', err)
        self.assertNotIn('README.md', err)
        self.assertEqual(self.manifest()['upstream']['revision'], self.base)
        git(self.local, 'checkout', '--', 'skills/alpha/ff.md')
        self.assertEqual(self.cli('apply')[0], 3)

    def test_excluded_skill_is_skipped_and_never_recorded(self):
        self.mixed()
        manifest = self.manifest()
        manifest['excluded'] = {'newbie': 'not wanted'}
        (self.local / 'sources.json').write_text(json.dumps(manifest, indent=2) + '\n')
        git(self.local, 'commit', '-qam', 'exclude')
        code, out, _ = self.cli('plan')
        self.assertNotIn('newbie', out)
        self.cli('apply')
        self.assertFalse((self.local / 'skills/newbie').exists())
        self.assertNotIn('newbie', self.manifest()['skills'])

    def test_watched_skill_is_reported_not_written_and_not_work(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/tdd/SKILL.md': upstream_skill('tdd'),
        }
        target = {
            **base,
            'pstack/skills/tdd/SKILL.md': upstream_skill('tdd', 'Changed upstream.'),
        }
        self.start(
            base,
            target,
            {'skills/pstack-tdd/SKILL.md': 'ours\n'},
            watch={'pstack-tdd': 'pstack/skills/tdd'},
        )
        code, out, _ = self.cli('plan')
        self.assertEqual(code, 0)
        self.assertIn('watched (never written):\n  pstack-tdd: ', out)
        self.assertIn('pstack/skills/tdd/SKILL.md', out)
        self.assertIn('nothing to do', out)
        before = self.snapshot()
        code, out, _ = self.cli('apply')
        self.assertEqual(
            (code, self.snapshot()['skills/pstack-tdd/SKILL.md']), (0, b'ours\n')
        )
        self.assertEqual(self.manifest()['upstream']['revision'], self.to)
        self.assertEqual(set(before), set(self.snapshot()))

    def test_upstream_delete_is_kept_reported_and_listed_by_a_later_audit(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/old.md': 'old\n',
        }
        target = {'pstack/skills/alpha/SKILL.md': upstream_skill('alpha')}
        self.start(base, target)
        code, out, _ = self.cli('plan')
        self.assertIn('keep          skills/alpha/old.md  (deleted upstream)', out)
        self.assertEqual(self.cli('apply')[0], 0)
        self.assertEqual(self.read('skills/alpha/old.md'), 'old\n')
        report = pu.audit(pu.Tree(self.local), pu.Upstream(self.up_repo), self.to)
        self.assertEqual(report['local_only_files'], ['skills/alpha/old.md'])

    def test_pristine_rename_moves_the_file_and_keeps_the_mode(self):
        body = ''.join(f'line {i}\n' for i in range(30))
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/old.sh': (body, 0o755),
        }
        target = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/new.sh': (body, 0o755),
        }
        self.start(base, target)
        self.assertEqual(self.cli('apply')[0], 0)
        self.assertFalse((self.local / 'skills/alpha/old.sh').exists())
        self.assertEqual(self.read('skills/alpha/new.sh'), body)
        self.assertEqual(
            oct((self.local / 'skills/alpha/new.sh').stat().st_mode & 0o777), '0o755'
        )

    def test_a_mode_change_alone_is_applied(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/run.sh': ('echo\n', 0o644),
        }
        target = {**base, 'pstack/skills/alpha/run.sh': ('echo\n', 0o755)}
        self.start(base, target)
        self.assertEqual(self.cli('apply')[0], 0)
        self.assertEqual(
            oct((self.local / 'skills/alpha/run.sh').stat().st_mode & 0o777), '0o755'
        )


class JudgeTests(UpdateCase):
    def assertBlocked(self, needle):
        before = self.snapshot()
        code, out, _ = self.cli('apply')
        self.assertEqual(code, 4, out)
        self.assertIn(needle, out)
        self.assertEqual(
            (self.snapshot(), self.status(), self.manifest()['upstream']['revision']),
            (before, '', self.base),
        )
        self.assertIn('judge', self.cli('plan')[1])

    def test_new_skill_colliding_with_a_local_skill(self):
        base = {'pstack/skills/alpha/SKILL.md': upstream_skill('alpha')}
        target = {**base, 'pstack/skills/ours/SKILL.md': upstream_skill('ours')}
        self.start(base, target, {'skills/ours/SKILL.md': 'ours\n'})
        self.assertBlocked('new upstream skill collides')

    def test_added_file_that_differs_from_a_local_file(self):
        base = {'pstack/skills/alpha/SKILL.md': upstream_skill('alpha')}
        target = {**base, 'pstack/skills/alpha/extra.md': 'upstream\n'}
        self.start(base, target, {'skills/alpha/extra.md': 'ours\n'})
        self.assertBlocked('different file exists locally')

    def test_customized_binary_file(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/b.bin': b'\x00one',
        }
        target = {**base, 'pstack/skills/alpha/b.bin': b'\x00two'}
        self.start(base, target, {'skills/alpha/b.bin': b'\x00ours'})
        self.assertBlocked('binary file customized')

    def test_customized_file_renamed_upstream(self):
        body = ''.join(f'line {i}\n' for i in range(30))
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/old.md': body,
        }
        target = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/new.md': body,
        }
        self.start(base, target, {'skills/alpha/old.md': body + 'ours\n'})
        self.assertBlocked('customized file renamed upstream')

    def test_file_removed_locally_but_changed_upstream(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/f.md': 'one\n',
        }
        target = {**base, 'pstack/skills/alpha/f.md': 'two\n'}
        self.start(base, target)
        (self.local / 'skills/alpha/f.md').unlink()
        git(self.local, 'commit', '-qam', 'drop')
        self.assertBlocked('removed locally')

    def test_file_that_holds_conflict_markers(self):
        base = {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/f.md': 'one\n',
        }
        target = {**base, 'pstack/skills/alpha/f.md': 'two\n'}
        self.start(base, target, {'skills/alpha/f.md': '<<<<<<< local\nx\n'})
        self.assertBlocked('holds conflict markers')


CHECK = SCRIPT.parent / 'check-pstack.py'
ADAPTED = '---\nname: {0}\ndescription: d\n---\n\nRead [the runtime adapter](../pstack/references/runtime.md) first.\n\n{1}\n'


class CheckPstackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'skills/pstack/references').mkdir(parents=True)
        (self.root / 'skills/pstack/LICENSE').write_text('MIT\n')
        (self.root / 'skills/pstack/references/runtime.md').write_text('# Runtime\n')
        self.write('skills/pstack/SKILL.md', ADAPTED.format('pstack', 'Entry.'))
        self.write('skills/a/SKILL.md', ADAPTED.format('a', 'Body.'))
        self.manifest()

    def manifest(self, **upstream):
        data = {
            'upstream': {'revision': 'r', 'version': '1', **upstream},
            'skills': {'pstack': 'x', 'a': 'y'},
            'previous_install': {},
        }
        (self.root / 'sources.json').write_text(json.dumps(data))

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def check(self):
        return subprocess.run(
            ['python3', str(CHECK), str(self.root)], capture_output=True, text=True
        )

    def assertFails(self, needle):
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_a_clean_tree_passes(self):
        result = self.check()
        self.assertEqual(
            (result.returncode, result.stdout),
            (0, '2 skills checked; all 0 previous skills covered\n'),
        )

    def test_a_conflict_marker_anywhere_in_skills_fails(self):
        for path in (
            'skills/a/SKILL.md',
            'skills/a/scripts/run.sh',
            'skills/a/references/x.txt',
        ):
            with self.subTest(path):
                self.write(
                    path,
                    'before\n<<<<<<< local\nours\n=======\ntheirs\n>>>>>>> upstream 1234567\n',
                )
                self.assertFails('unresolved merge conflict marker')
                self.write(
                    path,
                    ADAPTED.format('a', 'Body.')
                    if path.endswith('SKILL.md')
                    else 'clean\n',
                )
        self.assertEqual(self.check().returncode, 0)

    def test_a_marker_in_a_copy_of_a_real_skill_file_fails(self):
        real_skill = (SCRIPT.parents[1] / 'skills/how/SKILL.md').read_text()
        self.write('skills/a/SKILL.md', real_skill + '\n<<<<<<< local\n')
        self.assertFails('unresolved merge conflict marker')

    def test_an_unresolved_upstream_update_fails(self):
        self.manifest(pending={'revision': 'abc', 'conflicts': []})
        self.assertFails('unresolved upstream update')

    def test_anchors_follow_github_slugs_duplicates_fences_and_explicit_ids(self):
        self.write(
            'skills/a/references/t.md',
            '# Top Title\n\n## Same\n\n## Same\n\n### `Code` & **bold** [link](u.md)\n\n'
            '```\n# Fenced Heading\n```\n\n<a id="Explicit"></a>\n\n## Under_score: and-dash?\n',
        )
        links = [
            't.md#top-title',
            't.md#same',
            't.md#same-1',
            't.md#code--bold-link',
            't.md#explicit',
            't.md#under_score-and-dash',
        ]
        self.write(
            'skills/a/references/u.md',
            ''.join(f'[x]({link})\n' for link in links) + '[self](#own)\n\n# Own\n',
        )
        self.assertEqual(self.check().returncode, 0, self.check().stderr)
        for broken in ('t.md#fenced-heading', 't.md#same-2', 't.md#nope', '#missing'):
            with self.subTest(broken):
                self.write('skills/a/references/v.md', f'[x]({broken})\n')
                self.assertFails('no heading for ' + broken)
        (self.root / 'skills/a/references/v.md').unlink()
        self.write(
            'skills/a/references/w.md',
            '[x](https://example.com/page#frag) [y](../SKILL.md)\n',
        )
        self.assertEqual(self.check().returncode, 0)


@unittest.skipUnless(
    os.environ.get('PSTACK_UPSTREAM_CHECKOUT'),
    'PSTACK_UPSTREAM_CHECKOUT names a cursor/plugins checkout',
)
class RealCheckoutTests(unittest.TestCase):
    def audit_at(self, tree_at, pinned):
        """Audit our skills as they were at tree_at against upstream at pinned, whatever sources.json pins now."""
        repo = Path(__file__).resolve().parents[1]
        checkout = os.environ['PSTACK_UPSTREAM_CHECKOUT']
        for root, rev in ((repo, tree_at), (checkout, pinned)):
            if subprocess.run(
                ['git', 'cat-file', '-e', f'{rev}^{{commit}}'],
                cwd=root,
                capture_output=True,
            ).returncode:
                self.skipTest(f'{root} has no commit {rev}')
        with tempfile.TemporaryDirectory() as tmp:
            archive = subprocess.run(
                ['git', 'archive', tree_at, 'skills', 'sources.json'],
                cwd=repo,
                capture_output=True,
                check=True,
            )
            subprocess.run(['tar', '-x', '-C', tmp], input=archive.stdout, check=True)
            return pu.audit(pu.Tree(tmp), pu.Upstream(checkout), pinned)

    def test_real_checkout_audit_names_every_customization(self):
        report = self.audit_at(TREE_AT, PINNED)
        self.assertEqual(
            sorted(path for path, _, _ in report['customized']), CUSTOMIZED_AT_PINNED
        )
        # tdd and teach are unmapped here only because sources.json had no `watch` key at TREE_AT.
        self.assertEqual(
            (
                report['binary'],
                report['absent'],
                report['local_only_files'],
                report['unmapped'],
            ),
            ([], [], [], ['tdd', 'teach']),
        )

    def test_real_checkout_audit_names_every_customization_at_0_15_29(self):
        report = self.audit_at(TREE_AT_0_15_29, PINNED_0_15_29)
        self.assertEqual(
            sorted(path for path, _, _ in report['customized']), CUSTOMIZED_AT_0_15_29
        )
        self.assertEqual(
            (
                report['binary'],
                report['absent'],
                report['local_only_files'],
                report['unmapped'],
            ),
            ([], [], ['skills/poteto-help/references/scenarios.md'], []),
        )


if __name__ == '__main__':
    unittest.main()
