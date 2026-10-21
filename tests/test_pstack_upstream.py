import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

# git and the scripts report resolved paths; on macOS the temp dir is under
# /var, a link to /private/var, so tests work in the resolved one.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/pstack-upstream.py'
spec = importlib.util.spec_from_file_location('pstack_upstream', SCRIPT)
pu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pu)

PINNED = 'e43c7ee26e0038c6c1fa8380dd34ce86ff94cb2a'
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


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *args],
                          cwd=cwd, check=True, capture_output=True).stdout.decode().strip()


def upstream_skill(name, body='Body.', keys='disable-model-invocation: true\n'):
    return f'---\nname: {name}\ndescription: A skill.\n{keys}---\n\n# {name}\n\n{body}\n'


def commit_upstream(repo, files, version='1.0.0'):
    """Replace pstack/ in the fixture upstream with `files` and commit it."""
    repo = Path(repo)
    if not (repo / '.git').exists():
        git(repo, 'init', '-q')
    git(repo, 'rm', '-rq', '--ignore-unmatch', 'pstack')
    files = {'pstack/plugin.json': json.dumps({'name': 'pstack', 'version': version}), **files}
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
    data = {'upstream': {'repository': 'x', 'revision': base, 'version': '1.0.0'}, 'skills': skills,
            'excluded': {}, 'watch': {}, **manifest}
    (root / 'sources.json').write_text(json.dumps(data, indent=2) + '\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'local')


class AdaptTests(unittest.TestCase):
    def test_skill_md_gets_local_name_loses_cursor_keys_and_gains_the_adapter(self):
        upstream = ('---\nname: Poteto Mode\ndescription: "Multi\n  line."\ndisable-model-invocation: true\n'
                    'mode: true\nicon: crown\ncolor: yellow\nreminder: New task?\n  and more\n---\n\n# Body\n')
        expected = ('---\nname: poteto-mode\ndescription: "Multi\n  line."\n---\n\n' + pu.ADAPTER + '\n\n\n# Body\n')
        self.assertEqual(pu.adapt('poteto-mode', 'SKILL.md', upstream.encode()), expected.encode())

    def test_the_pstack_entry_point_gets_no_adapter_and_other_files_pass_through(self):
        skill = b'---\nname: pstack\ndescription: d\n---\n\n# P\n'
        self.assertEqual(pu.adapt('pstack', 'SKILL.md', skill), skill)
        raw = b'---\nname: Not Frontmatter\n---\n\x00\xff'
        self.assertEqual(pu.adapt('x', 'references/a.md', raw), raw)
        self.assertEqual(pu.adapt('x', 'SKILL.md', b'no frontmatter\n'), b'no frontmatter\n')

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
        base = commit_upstream(self.up_repo, {
            'pstack/skills/alpha/SKILL.md': upstream_skill('alpha'),
            'pstack/skills/alpha/references/r.md': 'upstream r\n',
            'pstack/skills/alpha/references/gone.md': 'present upstream\n',
            'pstack/skills/beta/SKILL.md': upstream_skill('beta'),
            'pstack/skills/tdd/SKILL.md': upstream_skill('tdd'),
            'pstack/skills/stray/SKILL.md': upstream_skill('stray'),
        })
        make_local(self.local, base, {
            'skills/alpha/SKILL.md': pu.adapt('alpha', 'SKILL.md', upstream_skill('alpha').encode()),
            'skills/alpha/references/r.md': 'upstream r\nlocal line\n',
            'skills/alpha/references/mine.md': 'ours\n',
            'skills/beta/SKILL.md': pu.adapt('beta', 'SKILL.md', upstream_skill('beta').encode()),
            'skills/pstack-tdd/SKILL.md': 'ours\n',
            'skills/own/SKILL.md': 'ours\n',
        }, {'alpha': 'pstack/skills/alpha', 'beta': 'pstack/skills/beta', 'pstack-tdd': 'local customization',
            'own': 'local skill'}, watch={'pstack-tdd': 'pstack/skills/tdd'}, excluded={'nothing': 'x'})
        tree = pu.Tree(self.local)
        report = pu.audit(tree, pu.Upstream(self.up_repo), base)
        self.assertEqual(report['customized'], [('skills/alpha/references/r.md', 1, 0)])
        self.assertEqual(report['absent'], ['skills/alpha/references/gone.md'])
        self.assertEqual(report['local_only_files'], ['skills/alpha/references/mine.md'])
        self.assertEqual(report['local_only_skills'], ['own', 'pstack-tdd'])
        self.assertEqual(report['watched'], {'pstack-tdd': 'pstack/skills/tdd'})
        self.assertEqual(report['unmapped'], ['stray'])

    def test_audit_command_prints_the_report(self):
        base = commit_upstream(self.up_repo, {'pstack/skills/alpha/SKILL.md': upstream_skill('alpha')})
        make_local(self.local, base, {'skills/alpha/SKILL.md': 'changed\n'}, {'alpha': 'pstack/skills/alpha'})
        result = subprocess.run(['python3', str(SCRIPT), '--root', str(self.local), 'audit', '--upstream', str(self.up_repo)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('customized upstream files (1):\n  skills/alpha/SKILL.md  +', result.stdout)


@unittest.skipUnless(os.environ.get('PSTACK_UPSTREAM_CHECKOUT'), 'PSTACK_UPSTREAM_CHECKOUT names a cursor/plugins checkout')
class RealCheckoutTests(unittest.TestCase):
    def test_real_checkout_audit_names_every_customization(self):
        root = Path(__file__).resolve().parents[1]
        tree = pu.Tree(root)
        if tree.manifest['upstream']['revision'] != PINNED:
            self.skipTest('the repo has moved off the pinned revision this list describes')
        report = pu.audit(tree, pu.Upstream(os.environ['PSTACK_UPSTREAM_CHECKOUT']), PINNED)
        self.assertEqual(sorted(path for path, _, _ in report['customized']), CUSTOMIZED_AT_PINNED)
        self.assertEqual((report['binary'], report['absent'], report['local_only_files'], report['unmapped']), ([], [], [], []))


if __name__ == '__main__':
    unittest.main()
