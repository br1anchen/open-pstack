"""The Python core behind pair-core.sh: die parity and the store helpers it duplicates from bash."""
import contextlib
import io
import json
import os
import random
import re
import shlex
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
scripts = root / 'skills/pstack-pair/scripts'
sys.path.insert(0, str(scripts))
sys.dont_write_bytecode = True
from pstack_core import checkin, cli, dispatch, herdr, providers, replies, review, store, wait  # noqa: E402
from pstack_core.commands import metrics  # noqa: E402
from pstack_core.context import Context  # noqa: E402
from pstack_core.errors import Die, Exit  # noqa: E402

LEGACY = 'd06a932'
BASE = 'e6e83e3'
LEAKED_PREFIXES = ('PSTACK_KITCHEN_', 'KITCHEN_', 'PAIR_')


def inherited_env():
    return {k: v for k, v in os.environ.items() if k != 'PSTACK_PLACEMENT' and not k.startswith(LEAKED_PREFIXES)}


def core_args(skill_root='/nowhere/pstack-pair'):
    return ['--skill', 'pstack-pair', '--skill-root', skill_root, '--state-root', '/s', '--store-label', 'Pair',
            '--roles', 'sidekick', '--store-dirs', 'plans briefs', '--slug-max', '21', '--unfilled-re', '', '--']


LEGACY_HEADER = 'ts\tepoch\tactor\tevent\tunit\tdetail'


def mask(text):
    """events.tsv without its ts and epoch columns."""
    return [row.split('\t', 2)[2] for row in text.splitlines()]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.env = {**inherited_env(), 'PATH': f'{root / "tests/pstack-pair/bin"}:{os.environ["PATH"]}'}

    def pair_py(self, *args):
        return subprocess.run([sys.executable, '-I', str(scripts / 'pair.py'), *core_args(), *args],
                              env=self.env, capture_output=True, text=True, check=False)

    def bash(self, snippet, env=None, check=True, base=False):
        """snippet run with pair-core.sh sourced: the working tree's, or with base=True skills/ as it was at e6e83e3, the oracle for
        the commands that moved to Python since."""
        skills = self.base_skills() if base else root / 'skills'
        harness = f'''set -euo pipefail
skill_root={skills / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair
roles=(sidekick); store_dirs=(plans); slug_max=21
. {skills / "pstack-pair/scripts/pair-core.sh"}
{snippet}'''
        r = subprocess.run(['bash', '-c', harness], env={**self.env, **(env or {})}, capture_output=True, text=True, check=check)
        return r.stdout if check else r

    def test_die_without_a_code_exits_1(self):
        r = self.pair_py('log', 'only-one-arg')
        self.assertEqual((r.returncode, r.stdout), (1, ''))
        self.assertEqual(r.stderr, 'pair.sh: usage: pair.sh log <store> <phase> <decision> <why> <evidence> <result>\n')

    def test_die_with_a_code_prints_it_and_exits_with_it(self):
        err = tempfile.TemporaryFile('w+')
        self.addCleanup(err.close)

        def boom(ctx, args):
            raise Die('x', 7)

        with mock.patch.dict(cli.COMMANDS, {'boom': boom}), mock.patch.object(sys, 'stderr', err):
            code = cli.main([*core_args(), 'boom'])
        err.seek(0)
        self.assertEqual((code, err.read()), (7, 'pair.sh: x 7\n'))

    def test_an_unknown_command_dies(self):
        r = self.pair_py('nope')
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: unknown command: nope\n'))

    def test_event_matches_bash(self):
        a, b = self.path / 'a', self.path / 'b'
        a.mkdir()
        b.mkdir()
        for unit in ('-', 'reports/003-x.md', ''):
            self.bash(f'event {a} master log {unit!r} "p: d"')
            store.event(str(b), 'master', 'log', unit, 'p: d')
        self.bash(f'event {a} sidekick step - ""')
        store.event(str(b), 'sidekick', 'step', '-', '')
        text = (b / 'events.tsv').read_text()
        self.assertEqual(mask(text), mask((a / 'events.tsv').read_text()))
        self.assertEqual(len(text.splitlines()), 4)

    def test_the_first_event_in_a_new_store_is_the_first_line_of_events_tsv(self):
        a, b = self.path / 'a', self.path / 'b'
        a.mkdir()
        b.mkdir()
        self.bash(f'event {a} master init - pstack-pair')
        store.event(str(b), 'master', 'init', '-', 'pstack-pair')
        for d in (a, b):
            rows = (d / 'events.tsv').read_text().splitlines()
            self.assertEqual([r.split('\t', 2)[2] for r in rows], ['master\tinit\t-\tpstack-pair'])

    def test_event_dedupes_a_repeated_master_wake_in_one_second(self):
        a, b = self.path / 'a', self.path / 'b'
        a.mkdir()
        b.mkdir()
        env = {'PAIR_EVENT_DEDUPE': '1'}
        self.bash(f'date() {{ if [ "$1" = +%s ]; then echo 1000; else command date "$@"; fi; }}\n'
                  f'for i in 1 2; do event {a} master wake 001 "r"; done; event {a} master log - "r"', env)
        with mock.patch.dict(os.environ, env), mock.patch('time.time', return_value=1000.0):
            for _ in range(2):
                store.event(str(b), 'master', 'wake', '001', 'r')
            store.event(str(b), 'master', 'log', '-', 'r')
        self.assertEqual(mask((b / 'events.tsv').read_text()), mask((a / 'events.tsv').read_text()))
        self.assertEqual(len((b / 'events.tsv').read_text().splitlines()), 2)

    def test_header_field_matches_bash(self):
        f = self.path / 'h.md'
        f.write_text('# T\n\nstatus:\t done | partial\nstatus: later\nempty:\nkind:   a  b \n')
        for key in ('status', 'empty', 'kind', 'missing'):
            self.assertEqual(store.header_field(str(f), key), self.bash(f'header_field {f} {key}').removesuffix('\n'), key)


    def fixture_store(self, name='s', brief='001-one.md'):
        st = self.path / name
        for d in ('briefs', 'plans', 'reports', 'progress', 'steers'):
            (st / d).mkdir(parents=True)
        (st / 'pair.json').write_text(json.dumps({'dispatch': {'brief': f'{st}/briefs/{brief}'} if brief else None, 'notice_steers': []}))
        return st

    def ctx(self):
        return Context('pstack-pair', str(root / 'skills/pstack-pair'), '/s', 'Pair', ('sidekick',), ('plans',), 21, '')

    def test_next_seq_matches_bash(self):
        st = self.fixture_store()
        self.assertEqual(store.next_seq(str(st)), self.bash(f'next_seq {st}').strip())
        for rel in ('briefs/001-a.md', 'plans/003-b.md', 'reports/007-c.md', 'reports/x.md', 'briefs/12-no.md', 'plans/009-b.txt', 'reports/010x.md'):
            (st / rel).write_text('')
            self.assertEqual(store.next_seq(str(st)), self.bash(f'next_seq {st}').strip(), rel)
        self.assertEqual(store.next_seq(str(st)), '008')

    def test_paused_reason_and_pause_notice_match_bash(self):
        st = self.fixture_store()
        for content in (None, '', 'for lunch\n', 'two\nlines\n', 'no newline'):
            if content is not None:
                (st / 'paused').write_text(content)
            bash = self.bash(f'printf "[%s]\\n" "$(paused_reason {st} || echo NONE)"; pause_notice {st}')
            reason = store.paused_reason(str(st))
            ours = f'[{"NONE" if reason is None else reason}]\n' + store.pause_notice(str(st))
            self.assertEqual(ours, bash, repr(content))

    def test_steer_notice_matches_bash(self):
        st = self.fixture_store()
        names = [f'{st}/steers/001-one-s{k}.md' for k in (1, 2, 3, 12)] + [f'{st}/steers/001-one-s9.md', f'{st}/steers/002-other-s1.md']
        for n in names[:4] + names[5:]:
            Path(n).write_text('')
        pair = json.loads((st / 'pair.json').read_text())
        pair['notice_steers'] = names
        (st / 'pair.json').write_text(json.dumps(pair))
        self.assertEqual(self.bash(f'steer_notice {st}'), store.steer_notice(str(st)))
        (st / 'progress/001-one.md').write_text('10:00 steer s1 applied: x\n10:01 steer s12 late: reported\n')
        (st / 'reports/001-one-s2.md').write_text('status: object\n')
        bash = self.bash(f'steer_notice {st}')
        self.assertEqual(store.steer_notice(str(st)), bash)
        self.assertEqual(bash.count('STEER:'), 1)
        self.assertIn('001-one-s3.md', bash)

    def test_progress_path_matches_bash(self):
        for brief in ('001-one.md', None):
            st = self.fixture_store(f's-{brief}', brief)
            bash = self.bash(f'progress_path {st} || echo NONE').strip()
            self.assertEqual(store.progress_path(str(st)) or 'NONE', bash)

    def test_new_from_template_matches_bash(self):
        st = self.fixture_store()
        (st / 'plans/004-p.md').write_text('')
        for directory, template in (('plans', 'plan-template.md'), ('briefs', 'brief-template.md')):
            out = self.bash(f'new_from_template {st} good-slug {directory} {template}').strip()
            bash_text = Path(out).read_text()
            Path(out).unlink()
            self.assertEqual(store.new_from_template(self.ctx(), str(st), 'good-slug', directory, template), out)
            self.assertEqual(Path(out).read_text(), bash_text)
            self.assertTrue(not any(t in bash_text for t in ('{{SEQ}}', '{{SLUG}}', '{{STORE}}')) and 'good-slug' in bash_text, directory)
        r = self.bash(f'new_from_template {st} Bad_Slug briefs brief-template.md', check=False)
        with self.assertRaises(Die) as cm:
            store.new_from_template(self.ctx(), str(st), 'Bad_Slug', 'briefs', 'brief-template.md')
        self.assertEqual((r.returncode, r.stderr), (cm.exception.code, f'pair.sh: {cm.exception.message}\n'))

    def test_header_field_is_silent_on_a_missing_or_empty_path(self):
        self.assertEqual((store.header_field('', 'status'), store.header_field(str(self.path / 'none.md'), 'status')), ('', ''))

    def test_event_detail_defaults_to_empty(self):
        store.event(str(self.path), 'sidekick', 'step', '-')
        self.assertTrue((self.path / 'events.tsv').read_text().endswith('\tsidekick\tstep\t-\t\n'))


    def test_jq_alternative_treats_false_and_a_missing_key_as_absent(self):
        for name, pair in (('false', {'dispatch': {'brief': False}, 'notice_steers': False}), ('missing', {})):
            st = self.path / name
            st.mkdir()
            (st / 'pair.json').write_text(json.dumps(pair))
            self.assertEqual(store.progress_path(str(st)) or 'NONE', self.bash(f'progress_path {st} || echo NONE').strip(), name)
            self.assertEqual(store.steer_notice(str(st)), self.bash(f'steer_notice {st}'), name)

    def notes_fixture(self):
        st = self.fixture_store()
        (st / 'notes').mkdir()
        (st / 'steps').mkdir()
        for name, status in (('n1', 'blocking'), ('n2', 'clear'), ('n3', 'blocking'), ('n10', 'blocking')):
            (st / f'notes/001-one-{name}.md').write_text(f'# Note\n\nstatus: {status}\n')
        (st / 'notes/001-one-n4.md.draft').write_text('status: blocking\n')
        (st / 'notes/002-two-n7.md').write_text('status: blocking\n')
        (st / 'steps/001-one.tsv').write_text('1\taaa111\tfirst\t\n2\tbbb222\tsecond\tn3,n9\n')
        return st

    def test_noted_through_and_open_notes_match_bash(self):
        st = self.notes_fixture()
        for unit in ('001-one', '001-one.md', '002-two', '003-none'):
            self.assertEqual(str(review.noted_through(str(st), unit)), self.bash(f'noted_through {st} {unit}').strip(), unit)
            self.assertEqual('\n'.join(review.open_notes(str(st), unit)), self.bash(f'open_notes {st} {unit}').rstrip('\n'), unit)
        self.assertEqual(review.steps_file(str(st), '001-one.md'), self.bash(f'steps_file {st} 001-one.md').strip())

    def git_repo(self, branch='main'):
        repo = self.path / 'repo'
        repo.mkdir()
        env = {**self.env, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        self.git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.git('init', '-q', '-b', branch)
        return repo

    def commit(self, name):
        (self.path / 'repo' / name).write_text(name)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', name)
        return self.git('rev-parse', 'HEAD')

    def readiness_fixture(self):
        """A repo with commits c1 < c2 < c3 and a store whose unit 001-x has steps up to c2."""
        repo = self.git_repo()
        c1, c2, c3 = self.commit('c1'), self.commit('c2'), self.commit('c3')
        st = self.fixture_store('ready')
        (st / 'pair.json').write_text(json.dumps({'git_root': str(repo), 'cwd': str(repo), 'dispatch': {'brief': f'{st}/briefs/001-x.md'}}))
        (st / 'steps').mkdir()
        (st / 'notes').mkdir()
        return st, c1, c2, c3

    def test_report_ready_matches_bash_on_every_branch(self):
        st, c1, c2, c3 = self.readiness_fixture()
        missing = 'f' * 40
        both, tail_gone, no_tab = [(1, c1, ''), (2, c2, '')], [(1, c1, ''), (2, missing, '')], None
        # name, steps rows (None: no file, []: empty file), blocking notes, report name, status, head
        cases = [
            ('not done', both, (), '001-x.md', 'partial', c3),
            ('head contains the last step', both, (), '001-x.md', 'done', c3),
            ('head is the last step', both, (), '001-x.md', 'done', c2),
            ('head predates the last step', both, (), '001-x.md', 'done', c1),
            ('head with a suffix', both, (), '001-x.md', 'done', c1[:7] + ' (after the fix)'),
            ('steer stem', both, (), '001-x-s1.md', 'done', c1),
            ('consult stem', both, (), '001-x-q2.md', 'done', c1),
            ('head not a commit', both, (), '001-x.md', 'done', missing),
            ('no head', both, (), '001-x.md', 'done', ''),
            ('placeholder head', both, (), '001-x.md', 'done', '<sha>'),
            ('last step not a commit', tail_gone, (), '001-x.md', 'done', c1),
            ('empty steps file', [], (), '001-x.md', 'done', c1),
            ('no steps file', None, (), '001-x.md', 'done', c1),
            ('one open note', both, (1,), '001-x.md', 'done', c3),
            ('two open notes', both, (1, 12), '001-x.md', 'done', c3),
            ('notes resolved by a step', [(1, c1, 'n1,n12'), (2, c2, '')], (1, 12), '001-x.md', 'done', c1),
        ]
        ctx = Context('pstack-pair', str(root / 'skills/pstack-pair'), '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        for name, rows, notes, report, status, head in cases:
            steps = st / 'steps/001-x.tsv'
            steps.unlink(missing_ok=True)
            if rows is not None:
                steps.write_text(''.join(f'{k}\t{sha}\ts\t{res}\n' for k, sha, res in rows))
            for f in (st / 'notes').iterdir():
                f.unlink()
            for n in notes:
                (st / f'notes/001-x-n{n}.md').write_text('# N\n\nstatus: blocking\n')
            path = st / 'reports' / report
            path.write_text(f'# R\n\nstatus: {status}\nhead: {head}\n')
            got = ctx.variant.report_ready(ctx, str(st), str(path))
            r = self.bash(f'rc=0; report_ready {st} {path} || rc=$?; printf "%s\\037%s\\037%s" "$rc" "$report_open" "$report_why"')
            code, opened, why = r.split('\x1f')
            self.assertEqual((int(not got.ready), got.open, got.why), (int(code), opened, why), name)
        steps = st / 'steps/001-x.tsv'
        steps.write_text(f'1\t{c1}\ts\t\n{c2}')
        for f in (st / 'notes').iterdir():
            f.unlink()
        path = st / 'reports/001-x.md'
        path.write_text(f'# R\n\nstatus: done\nhead: {c1}\n')
        got = ctx.variant.report_ready(ctx, str(st), str(path))
        r = self.bash(f'rc=0; report_ready {st} {path} || rc=$?; printf "%s\\037%s" "$rc" "$report_why"')
        self.assertEqual((int(not got.ready), got.why), (int(r.split('\x1f')[0]), r.split('\x1f')[1]), 'a last step row without a tab')

    def base_skills(self):
        """skills/ as it was at e6e83e3, the bash that finish, notify, report and next ran in before they moved to Python."""
        cls = type(self)
        if cls.__dict__.get('_base') is None:
            tmp = tempfile.TemporaryDirectory()
            self.addClassCleanup(tmp.cleanup)
            git = subprocess.run(['git', '-C', str(root), 'archive', BASE, 'skills'], capture_output=True, check=False)
            if git.returncode != 0:
                raise unittest.SkipTest(f'{BASE} is not in this checkout')
            subprocess.run(['tar', '-x', '-C', tmp.name], input=git.stdout, check=True)
            cls._base = Path(tmp.name) / 'skills'
        return cls._base

    GIT = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
           'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z'}

    def ab_world(self, label, spec):
        """A repo with commits c1 < c2 < c3, a store and a fake herdr, built the same for every label."""
        top = self.path / label
        repo, st, fake = top / 'repo', top / 'st', top / 'fake'
        for d in (repo, fake / 'agents', fake / 'panes', top / 'home', *(st / n for n in ('briefs', 'reports', 'plans', 'steps', 'notes', 'progress', 'steers', 'advice'))):
            d.mkdir(parents=True)
        env = {**self.env, **self.GIT, 'FAKE': str(fake), 'HOME': str(top / 'home'), 'XDG_STATE_HOME': str(top / 'state'),
               'HERDR_PANE_ID': 'p0', 'HERDR_SOCKET_PATH': str(top / 'herdr.sock'), 'PAIR_SETTLE_HOLD': '0', 'PAIR_SUBMIT_CHECK_S': '0'}
        if not spec.get('noenv'):
            env['HERDR_ENV'] = '1'
        env.pop('CLAUDE_CODE_SESSION_ID', None)
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True).stdout.strip()
        git('init', '-q', '-b', 'main')
        shas = {}
        for name in () if spec.get('emptyrepo') else ('c1', 'c2', 'c3'):
            (repo / name).write_text(name)
            git('add', '-A')
            git('commit', '-q', '-m', name)
            shas[name] = git('rev-parse', 'HEAD')
        sub = {**shas, 'st': str(st), 'T': str(top), 'repo': str(repo)}
        fill = lambda text: text.format(**sub)
        pair = {'master': {'name': 'm1'}, 'sidekick': {'kind': spec.get('kind', 'claude'), 'generation': 1, 'name': 's1'},
                'git_root': str(repo), 'cwd': str(repo), 'dispatch': {'brief': f'{st}/briefs/001-x.md', 'generation': spec.get('dispatch_generation', 1)},
                **spec.get('pair', {})}
        if not spec.get('nopair'):
            def walk(v):
                return fill(v) if isinstance(v, str) else [walk(x) for x in v] if isinstance(v, list) else {k: walk(x) for k, x in v.items()} if isinstance(v, dict) else v

            (st / 'pair.json').write_text(json.dumps(walk(pair), indent=2) + '\n')
        for name in ('001-x', '002-y'):
            (st / f'briefs/{name}.md').write_text(f'# Brief {name}\n')
        for name, target in spec.get('links', {}).items():
            (top / name).symlink_to(fill(target))
        for rel, text in spec.get('files', {}).items():
            (st / rel).parent.mkdir(parents=True, exist_ok=True)
            (st / rel).write_text(fill(text))
        if spec.get('master', 'idle'):
            (fake / 'agents/m1').write_text(f'{spec.get("master", "idle")} claude\n')
        for agent, state in spec.get('agents', {}).items():
            (fake / 'agents' / agent).write_text(state + '\n')
        if spec.get('panes'):
            for pane, agent in spec['panes'].items():
                (fake / 'panes' / pane).write_text(agent)
        for agent, text in spec.get('screens', {}).items():
            (fake / 'screen').mkdir(exist_ok=True)
            (fake / 'screen' / agent).write_text(fill(text))
        for sub, script in spec.get('once', {}).items():
            (fake / 'once').mkdir(exist_ok=True)
            (fake / 'once' / sub).write_text(fill(script))
            (fake / 'once' / sub).chmod(0o755)
        if spec.get('report_after'):
            (fake / 'report-after').write_text(fill(spec['report_after']) + '\n')
        env.update(spec.get('env', {}))
        if spec.get('busy'):
            (fake / 'busy').mkdir(exist_ok=True)
            (fake / 'busy' / 's1').write_text('')
        if spec.get('promptfail'):
            (fake / 'prompt-fail').write_text('')
        (st / 'events.tsv').write_text('2026-01-01T00:00:00Z\t1\tmaster\tinit\t-\tbefore\n')
        return top, st, env, fill

    def ab_run(self, label, skills, skill, spec):
        top, st, env, fill = self.ab_world(label, spec)
        script = 'kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh'
        r = subprocess.run([str(skills / skill / 'scripts' / script), *(fill(a) for a in spec['args'])], cwd=top / 'repo', env=env, timeout=120,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT if spec.get('merged') else subprocess.PIPE, text=True, check=False)
        norm = lambda text: re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text.replace(str(top), '<T>')))
        files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
        events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()]
        calls = norm((top / 'fake/calls.log').read_text()) if (top / 'fake/calls.log').exists() else ''
        return {'rc': r.returncode, 'stdout': norm(r.stdout), 'stderr': norm(r.stderr or ''), 'files': files, 'events': events, 'calls': calls}

    AB_CASES = {
        'done report to an idle master': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'done report to a working master': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], master='working', files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'done report, no master agent': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], master=None, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'done report to a done master': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], master='done', files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'prompt delivery fails': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], promptfail=True, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'partial report': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
        'done report without a head': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\n'}),
        'done report with the template head': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\nhead: <sha>\n'}),
        'done report of another unit without a head': dict(args=['finish', '{st}', '{st}/reports/002-y.md'], files={'reports/002-y.md': '# R\n\nstatus: done\n'}),
        'done report with open notes': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'steps/001-x.tsv': '1\t{c2}\ts\t\n', 'notes/001-x-n1.md': '# N\n\nstatus: blocking\n',
            'notes/001-x-n3.md': '# N\n\nstatus: blocking\n'}),
        'done report with a stale head': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c1}\n', 'steps/001-x.tsv': '1\t{c2}\ts\t\n2\t{c3}\ts\t\n'}),
        'queued brief after a done report': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'queue': '{st}/briefs/002-y.md\n'}),
        'queued brief that is gone': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'queue': '{st}/briefs/009-gone.md\n'}),
        'queued brief that is gone, the streams merged': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], merged=True, files={
            'reports/001-x.md': '# R\\n\\nstatus: done\\nhead: {c3}\\n', 'queue': '{st}/briefs/009-gone.md\\n'}),
        'queued brief of an answered unit': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'reports/002-y.md': '# old\n', 'queue': '{st}/briefs/002-y.md\n'},
            pair={'heads': {'001-x': 'kept'}, 'pending': ['{st}/briefs/z.md', '{st}/briefs/001-x.md']}),
        'queued brief in a repo without commits': dict(args=['next', '{st}'], emptyrepo=True, files={'queue': '{st}/briefs/002-y.md\\n'}),
        'queued brief behind a pause': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'queue': '{st}/briefs/002-y.md\n', 'paused': 'lunch\n'}),
        'queue with a partial report': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={
            'reports/001-x.md': '# R\n\nstatus: partial\n', 'queue': '{st}/briefs/002-y.md\n'}),
        'devin done report asks for a rotation': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], kind='devin', files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'queue': '{st}/briefs/002-y.md\n'}),
        'devin that already asked for a rotation': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], kind='pi', pair={'sidekick': {'kind': 'pi', 'generation': 1, 'rotation_required': True}},
            files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'devin report of an older generation': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], kind='devin', dispatch_generation=0,
            files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'devin partial report': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], kind='devin', files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
        'late steer': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], pair={'notice_steers': ['{st}/steers/001-x-s1.md', '{st}/steers/001-x-s2.md']}, files={
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'steers/001-x-s1.md': '# S\n', 'steers/001-x-s2.md': '# S\n',
            'progress/001-x.md': '12:00 steer s2 applied: ok\n'}),
        'steer objection report': dict(args=['finish', '{st}', '{st}/reports/001-x-s1.md'], files={'reports/001-x-s1.md': '# R\n\nstatus: object\n'}),
        'advice to the master': dict(args=['notify', '{st}', '{st}/advice/001-x.md'], files={'advice/001-x.md': '# A\n\nstatus: answered\n'}),
        'notify a report': dict(args=['notify', '{st}', '{st}/reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\nhead: nothing\n'}),
        'notify through a relative path': dict(args=['notify', '{st}', '../st/reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\n'}),
        'notify a missing file': dict(args=['notify', '{st}', '{st}/reports/none.md']),
        'notify under a missing directory': dict(args=['notify', '{st}', '{T}/nope/none.md']),
        'notify an empty path': dict(args=['notify', '{st}', '']),
        'notify without arguments': dict(args=['notify']),
        'notify outside herdr': dict(args=['notify', '{st}', '{st}/reports/001-x.md'], noenv=True, files={'reports/001-x.md': '# R\n'}),
        'notify without pair.json': dict(args=['notify', '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': '# R\n\nstatus: done\n'}),
        'notify with a null master': dict(args=['notify', '{st}', '{st}/reports/001-x.md'], pair={'master': None}, files={'reports/001-x.md': '# R\n'}),
        'finish a missing report': dict(args=['finish', '{st}', '{st}/reports/none.md']),
        'finish under a missing directory': dict(args=['finish', '{st}', '{T}/nope/none.md']),
        'finish an empty path': dict(args=['finish', '{st}', '']),
        'finish without arguments': dict(args=['finish', '{st}']),
        'finish outside herdr': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], noenv=True, files={'reports/001-x.md': '# R\n'}),
        'finish a done report without pair.json': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'finish a partial report without pair.json': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
        'finish a report path with a trailing slash': dict(args=['finish', '{st}', '{st}/reports/001-x.md/'], files={'reports/001-x.md': '# R\\n\\nstatus: done\\nhead: {c3}\\n'}),
        'finish through a missing intermediate directory': dict(args=['finish', '{st}', '{st}/nope/../reports/001-x.md'], files={'reports/001-x.md': '# R\\n\\nstatus: done\\nhead: {c3}\\n'}),
        'notify through a missing intermediate directory': dict(args=['notify', '{st}', '{st}/nope/../reports/001-x.md'], files={'reports/001-x.md': '# R\\n\\nstatus: done\\n'}),
        'finish a symlink loop': dict(args=['finish', '{st}', '{T}/loop1'], links={'loop1': 'loop2', 'loop2': 'loop1'}, files={'reports/001-x.md': '# R\\n\\nstatus: done\\nhead: {c3}\\n'}),
        'finish a symlinked report': dict(args=['finish', '{st}', '{T}/link.md'], links={'link.md': '{st}/reports/001-x.md'}, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'report, newest': dict(args=['report', '{st}'], files={'reports/001-x.md': '# R\n', 'reports/002-y.md': '# R\n', 'reports/notes.md': '# R\n'}),
        'report by number': dict(args=['report', '{st}', '001'], files={'reports/001-x.md': '# R\n', 'reports/001-x-s1.md': '# R\n', 'reports/002-y.md': '# R\n'}),
        'report with none': dict(args=['report', '{st}']),
        'report of a number with none': dict(args=['report', '{st}', '007'], files={'reports/001-x.md': '# R\n'}),
        'report without arguments': dict(args=['report']),
        'next with a queued brief': dict(args=['next', '{st}'], files={'queue': '{st}/briefs/002-y.md\n'}),
        'next with nothing queued': dict(args=['next', '{st}']),
        'next while paused': dict(args=['next', '{st}'], files={'queue': '{st}/briefs/002-y.md\n', 'paused': 'lunch\n'}),
        'next with a brief that is gone': dict(args=['next', '{st}'], files={'queue': '{st}/briefs/009-gone.md\n'}),
        'next for a devin sidekick': dict(args=['next', '{st}'], kind='devin', files={'queue': '{st}/briefs/002-y.md\n'}),
        'next for a pi sidekick with a null kind': dict(args=['next', '{st}'], pair={'sidekick': None}, files={'queue': '{st}/briefs/002-y.md\n'}),
        'next with a stale answer on file': dict(args=['next', '{st}'], files={'queue': '{st}/briefs/002-y.md\n', 'reports/002-y.md': '# old\n'}),
        'next without pair.json': dict(args=['next', '{st}'], nopair=True),
        'next with arguments missing': dict(args=['next']),
    }

    def ab_compare(self, name, spec, skill='pstack-pair'):
        spec = dict(spec)
        want = self.ab_run(f'base-{len(list(self.path.iterdir()))}', self.base_skills(), skill, spec)
        got = self.ab_run(f'port-{len(list(self.path.iterdir()))}', root / 'skills', skill, spec)
        self.mismatches.update({name: {k: (got[k], want[k]) for k in got if got[k] != want[k]}} if got != want else {})
        return got

    def test_finish_notify_report_and_next_match_the_bash_at_the_base_commit(self):
        self.mismatches = {}
        outcomes = {}
        for name, spec in self.AB_CASES.items():
            outcomes[name] = self.ab_compare(name, spec)
        self.maxDiff = None
        self.assertEqual(self.mismatches, {})
        self.assertEqual(outcomes['done report to an idle master']['stdout'].split('\n')[0].split(' ')[:3], ['prompted', 'master', 'm1'])
        self.assertEqual(outcomes['done report with open notes']['rc'], 7)
        self.assertEqual(outcomes['done report with a stale head']['rc'], 7)
        self.assertIn('next: the master queued', outcomes['queued brief after a done report']['stdout'])
        self.assertIn('rotation-required', ''.join(outcomes['devin done report asks for a rotation']['events']))
        self.assertIn('late steer', outcomes['late steer']['stdout'])
        self.assertEqual(outcomes['finish a done report without pair.json']['rc'], 2)

    @staticmethod
    def mask_minutes(text):
        """The minutes a digest prints, which move when the base run and the port run fall on either side of a minute."""
        return re.sub(r'(elapsed |last change |last |for )[0-9]+m', r'\1Nm', text)

    @staticmethod
    def collapse_polls(calls):
        """The herdr call log with a poll that repeated (a wait, or a wait and the pane reads that follow it) kept once: how many
        times it repeats depends on where the whole-second deadline falls, not on what the scripts decide."""
        lines = re.sub(r'(agent wait \S+ --timeout )[0-9]+', r'\1N', calls).splitlines()
        changed = True
        while changed:
            changed = False
            for size in (1, 2, 3, 4):
                i = 0
                while i + 2 * size <= len(lines):
                    if lines[i:i + size] == lines[i + size:i + 2 * size]:
                        del lines[i + size:i + 2 * size]
                        changed = True
                    else:
                        i += 1
        return '\n'.join(lines)

    def wait_ab_cases(self):
        T = int(time.time())
        at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(T - 20 * 60 - 30))
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1}
        working = {'s1': 'working claude'}
        done = '# R\n\nstatus: done\nhead: {c3}\n'
        run = ['wait', '{st}', '--timeout', '20000']
        short = ['wait', '{st}', '--timeout', '1000']
        digest = {'sent': {**sent, 'epoch': T + 3600}, 'pending': ['{st}/briefs/001-x.md'], 'dispatch': {'brief': '{st}/briefs/001-x.md', 'at': at, 'head': '{c3}'}}
        steps = {'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n'}
        brief = lambda risk: {'briefs/001-x.md': f'# Brief\n\nrisk: {risk}\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/**\n'}
        log = {'progress/001-x.md': '10:00 one\n10:05 two\n'}
        return {
            'a report landing mid-wait': dict(args=run, agents=working, report_after='2 {st}/reports/001-x.md', pair={'sent': sent}),
            'a pending report at once': dict(args=run, pair={'sent': sent, 'pending': ['{st}/briefs/001-x.md']}, files={'reports/001-x.md': done}),
            'a reply to the last message at once': dict(args=run, pair={'sent': sent}, files={'reports/001-x.md': done}),
            'a reply that is withheld': dict(args=short, pair={'sent': sent}, files={'reports/001-x.md': done, 'steps/001-x.tsv': '1\t{c2}\t5\t\tone\n2\t{c3}\t5\t\ttwo\n', 'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'steps due': dict(args=run, pair={'sent': sent}, files=steps),
            'steps due, noted through 1': dict(args=run, pair={'sent': sent}, files={**steps, 'notes/001-x-n1.md': '# N\n\nstatus: clear\n'}),
            'steps already shown': dict(args=short, agents=working, pair={**digest, 'shown': {'001-x': 2}}, files={**steps, **log}),
            'a digest at the interval': dict(args=short, agents=working, pair=digest, files={**log, **brief('routine')}),
            'a digest with steers': dict(args=short, agents=working, pair=digest, files={**log, **brief('routine'), 'steers/001-x-s1.md': '# S\n\nkind: narrow\nscope effect: may write "extra/**"\n',
                                                                                        'reports/001-x-s1.md': '# R\n\nstatus: object\n', 'progress/001-x.md': '10:00 steer s1 applied: ok\n'}),
            'a digest with an objection': dict(args=short, agents=working, pair=digest, files={**log, **brief('routine'), 'steers/001-x-s1.md': '# S\n', 'reports/001-x-s1.md': '# R\n\nstatus: object\n'}),
            'a blocked sidekick': dict(args=run, agents={'s1': 'blocked claude'}, screens={'s1': 'Error: rate limit reached\n'}, pair={**digest, 'sidekick': {'kind': 'claude', 'generation': 1, 'name': 's1', 'fallback': {'kind': 'devin'}}}, files=brief('routine')),
            'a blocked sidekick with nothing to say': dict(args=run, agents={'s1': 'blocked claude'}, screens={'s1': 'a dialog\n'}, pair=digest),
            'an idle sidekick with nothing to report': dict(args=run, agents={'s1': 'idle claude'}, screens={'s1': 'quiet\n'}, pair=digest, env={'PAIR_SETTLE_HOLD': '0'}),
            'an idle sidekick that settles after a hold': dict(args=run, agents={'s1': 'idle claude'}, pair=digest, env={'PAIR_SETTLE_HOLD': '1', 'PAIR_SETTLE_POLL_S': '0.2'}),
            'a settle while the pane is busy': dict(args=['wait', '{st}', '--timeout', '2000'], agents={'s1': 'idle claude'}, busy=True, pair=digest, env={'PAIR_BUSY_POLL': '1'}, files={**log, **brief('routine')}),
            'a settled sidekick with a reply already shown': dict(args=run, agents={'s1': 'idle claude'}, pair={**digest, 'sent': {**digest['sent'], 'seen': '{st}/reports/001-x.md:1'}}, files={'reports/001-x.md': done}),
            'a settle hold that is not an integer': dict(args=run, agents={'s1': 'idle claude'}, pair=digest, env={'PAIR_SETTLE_HOLD': '1.5'}),
            'a busy poll that sleep refuses': dict(args=['wait', '{st}', '--timeout', '2000'], agents={'s1': 'idle claude'}, busy=True, pair=digest, env={'PAIR_BUSY_POLL': 'abc'}),
            'a busy poll that sleep accepts and the pattern does not': dict(args=['wait', '{st}', '--timeout', '2000'], agents={'s1': 'idle claude'}, busy=True, pair=digest, env={'PAIR_BUSY_POLL': '0x1'}),
            'a herdr timeout': dict(args=short, agents=working, pair=digest, files={**log, **brief('routine')}),
            'a herdr error that is not a timeout': dict(args=short, agents={}, pair=digest, files=brief('routine')),
            'a paused store': dict(args=run, pair={'sent': sent}, files={'reports/001-x.md': done, 'paused': 'lunch\n'}),
            'a queued brief': dict(args=run, pair={'sent': sent}, files={'reports/001-x.md': done, 'queue': '{st}/briefs/002-y.md\n'}),
            'send-only is accepted': dict(args=['wait', '{st}', '--send-only', '--timeout', '1000'], pair={'sent': sent}, files={'reports/001-x.md': done}),
            'every instead of a timeout': dict(args=['wait', '{st}', '--every', '1'], pair={'sent': sent}, files={'reports/001-x.md': done}),
            'an unknown option': dict(args=['wait', '{st}', '--bogus']),
            'a missing timeout value': dict(args=['wait', '{st}', '--timeout']),
            'a non-numeric timeout': dict(args=['wait', '{st}', '--timeout', 'abc']),
            'a fractional every': dict(args=['wait', '{st}', '--every', '1.5']),
            'an empty every': dict(args=['wait', '{st}', '--every', '']),
            'a timeout of zero': dict(args=['wait', '{st}', '--timeout', '0'], agents=working, pair=digest, files={**log, **brief('routine')}),
            'a negative timeout': dict(args=['wait', '{st}', '--timeout', '-5'], agents=working, pair=digest, files={**log, **brief('routine')}),
            'no arguments': dict(args=['wait']),
            'outside herdr': dict(args=run, noenv=True),
            'without pair.json': dict(args=run, nopair=True),
            'a null sidekick': dict(args=short, pair={'sidekick': None, 'sent': sent}, files={'reports/001-x.md': done}),
        }

    def wait_ab_compare(self, name, spec, skill='pstack-pair'):
        spec = dict(spec)
        want = self.ab_run(f'wbase-{len(list(self.path.iterdir()))}', self.base_skills(), skill, spec)
        got = self.ab_run(f'wport-{len(list(self.path.iterdir()))}', root / 'skills', skill, spec)
        return got, want

    def test_wait_ab_matches_the_bash_at_the_base_commit(self):
        mismatches = {}
        results = {}
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
        for name, spec in self.wait_ab_cases().items():
            got, want = self.wait_ab_compare(name, spec)
            for side in (got, want):
                side['stderr'] = norm_core(side['stderr'])
                side['stdout'] = self.mask_minutes(re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', side['stdout']))
                side['calls'] = self.collapse_polls(side['calls'])
            results[name] = got
            if name == 'without pair.json':
                # Permitted difference: with no pair.json the bash goes on with an empty agent name and prints a pair of error lines
                # for every field() it reads (some 40 lines, and a herdr call for the empty name); Python stops after the first pair.
                self.assertEqual((got['rc'], got['stdout'], got['stderr'], got['files'], got['events']),
                                 (want['rc'], want['stdout'], want['stderr'][:len(got['stderr'])], want['files'], want['events']))
                self.assertTrue(want['stderr'].startswith('pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>\njq: error: Could not open file : No such file or directory\n'))
                continue
            if got != want:
                mismatches[name] = {k: (got[k], want[k]) for k in got if got[k] != want[k]}
        self.maxDiff = None
        self.assertEqual(mismatches, {})

    def wait_ab_variant(self, skill, cases):
        mismatches = {}
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
        outcomes = {}
        for name, spec in cases.items():
            got, want = self.wait_ab_compare(name, spec, skill)
            for side in (got, want):
                side['stderr'] = norm_core(side['stderr'])
                side['stdout'] = self.mask_minutes(side['stdout'])
                side['calls'] = self.collapse_polls(side['calls'])
            outcomes[name] = got
            if got != want:
                mismatches[name] = {k: (got[k], want[k]) for k in got if got[k] != want[k]}
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        return outcomes

    def test_wait_ab_kitchen_wakes_on_steps_only_for_an_escalated_brief(self):
        T = int(time.time())
        at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(T - 20 * 60 - 30))
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': T + 3600}
        pair = {'sent': sent, 'pending': ['{st}/briefs/001-x.md'], 'dispatch': {'brief': '{st}/briefs/001-x.md', 'at': at, 'head': '{c3}'}}
        steps = {'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n', 'progress/001-x.md': '10:00 one\n'}
        brief = lambda risk: {'briefs/001-x.md': f'# Brief\n\nrisk: {risk}\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/**\n'}
        env = {'KITCHEN_QUIET_POLL_S': '0', 'KITCHEN_QUIET_MAX_S': '1'}
        cases = {
            'steps wake an escalated brief': dict(args=['wait', '{st}', '--timeout', '3000'], pair=pair, agents={'s1': 'working claude'}, files={**steps, **brief('escalated (a contract)')}, env=env),
            'steps do not wake a routine brief': dict(args=['wait', '{st}', '--timeout', '1000'], pair=pair, agents={'s1': 'working claude'}, files={**steps, **brief('routine')}, env=env),
            'a flagged digest is shown': dict(args=['wait', '{st}', '--timeout', '1000'], pair=pair, agents={'s1': 'working claude'}, files={'progress/001-x.md': '10:00 one\n', **brief('routine')},
                                              env={**env, 'KITCHEN_QUIET_MAX_S': '3'}),
            'a report at once': dict(args=['wait', '{st}', '--timeout', '3000'], pair={'sent': {**sent, 'epoch': 1}}, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', **brief('routine')}, env=env),
            'a done report whose verify has not passed': dict(args=['wait', '{st}', '--timeout', '1000'], pair={'sent': {**sent, 'epoch': 1}, 'dispatch': {'brief': '{st}/briefs/001-x.md'}},
                                                             files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', **brief('routine')}, env=env),
        }
        outcomes = self.wait_ab_variant('pstack-kitchen', cases)
        self.assertIn('steps: 2 to review on 001-x', outcomes['steps wake an escalated brief']['stdout'])
        self.assertNotIn('to review', outcomes['steps do not wake a routine brief']['stdout'])
        self.assertIn("written but not ready: the brief's Verify commands have not passed", outcomes['a done report whose verify has not passed']['stdout'])

    def test_wait_ab_matches_the_bash_in_the_other_variants(self):
        cases = {name: spec for name, spec in self.wait_ab_cases().items() if name in (
            'a report landing mid-wait', 'a digest at the interval', 'a blocked sidekick', 'steps due', 'a settled sidekick with a reply already shown', 'a herdr error that is not a timeout')}
        for skill in ('pstack-pair-guided', 'pstack-trio'):
            self.wait_ab_variant(skill, cases)

    def wait_signalled(self, skills, number, group, skill='pstack-pair', ignore_int=False, timeout_ms=20000):
        """Run `pair.sh wait`, signal it while it sits in herdr's wait (the fake herdr's once hook holds it there on a pipe), and give
        (exit status, stdout, stderr). Nothing here can hang: each wait is bounded, the hook is released in a finally, and the
        whole process group is killed if the command outlives the bound."""
        top, st, env, fill = self.ab_world(f'sig-{len(list(self.path.iterdir()))}', {'agents': {'s1': 'working claude'}, 'pair': {'sent': {'file': '{st}/briefs/001-x.md', 'epoch': 1}}})
        ready, release = top / 'ready', top / 'release'
        os.mkfifo(ready)
        os.mkfifo(release)
        # Held read-write from the start: the hook's open of the pipe then never waits for a writer, and what the test writes after the
        # signal reaches the hook however late it gets there.
        release_fd = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(lambda: os.close(release_fd) if release_fd is not None else None)
        hook = top / 'fake/once/wait'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f'#!/bin/sh\necho in >"{ready}"\nread x <"{release}"\n')
        hook.chmod(0o755)

        def default_signals():
            # a gate that runs as a background job hands its children SIGINT ignored; the signal under test must be deliverable
            for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(each, signal.SIG_IGN if ignore_int and each == signal.SIGINT else signal.SIG_DFL)

        script = 'kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh'
        before = self.store_snapshot(st)
        proc = subprocess.Popen([str(skills / skill / 'scripts' / script), 'wait', str(st), '--timeout', str(timeout_ms)], cwd=top / 'repo', env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=default_signals)

        def kill_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        def open_release():
            os.write(release_fd, b'x\n')

        self.addCleanup(kill_group)
        try:
            fd = os.open(ready, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not select.select([fd], [], [], 30)[0]:
                    self.fail('the wait never reached herdr')
            finally:
                os.close(fd)
            (os.killpg if group else os.kill)(proc.pid, number)
            # A SIGINT to the shell alone is held by the bash until herdr's call returns, so the hook is let go at once.
            open_release()
            try:
                status = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill_group()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                self.fail(f'{number.name} did not end the wait')
        finally:
            open_release()
        # The pipe closes when everything that held it has ended: a Python wait left behind by a signal to the shell alone must follow
        # its shell (PR_SET_PDEATHSIG) instead of running on to its timeout.
        if not select.select([proc.stdout], [], [], 10)[0]:
            kill_group()
            self.fail('the wait outlived its shell')
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            kill_group()
            proc.communicate(timeout=10)
            self.fail('a process kept the output open')
        return status, out, err, self.store_snapshot(st) == before

    @staticmethod
    def store_snapshot(st):
        return {str(f.relative_to(st)): f.read_bytes() for f in sorted(st.rglob('*')) if f.is_file()}

    def test_wait_ab_signals_end_the_command_as_they_end_the_bash(self):
        # Group-wide, as a terminal's Ctrl-C or a harness kill delivers it: INT, TERM and HUP end the command with the signal and
        # print nothing, and the store is as it was.
        for number, group in ((signal.SIGTERM, True), (signal.SIGINT, True), (signal.SIGHUP, True)):
            want = self.wait_signalled(self.base_skills(), number, group)
            got = self.wait_signalled(root / 'skills', number, group)
            self.assertEqual(got, want, (number.name, group))
            self.assertEqual(got, (-number, '', '', True), (number.name, group))

    def test_wait_ab_signals_to_the_shell_alone_end_the_wait_with_it(self):
        # The signal goes to the pair.sh process only. The base bash dies of INT, TERM or HUP (INT after herdr's call returns) and
        # writes nothing; the port is the same process, so it must die of the signal at once and leave nothing running or written.
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            want = self.wait_signalled(self.base_skills(), number, False)
            got = self.wait_signalled(root / 'skills', number, False)
            self.assertEqual(got, want, number.name)
            self.assertEqual(got, (-number, '', '', True), number.name)

    def test_wait_ab_an_ignored_sigint_stays_ignored(self):
        # A background job hands its children SIGINT ignored, and a process cannot un-ignore what it inherited ignored: bash does not,
        # and the Python wait must not turn it back into a fatal signal. The wait runs to its end and answers as it would unsignalled.
        want = self.wait_signalled(self.base_skills(), signal.SIGINT, True, ignore_int=True, timeout_ms=1000)
        got = self.wait_signalled(root / 'skills', signal.SIGINT, True, ignore_int=True, timeout_ms=1000)
        self.assertEqual(got[0], want[0])
        self.assertEqual(got[0], 4)
        self.assertEqual(got[1:3], want[1:3])

    def test_wait_ab_kitchen_signal_to_the_shell_alone(self):
        # kitchen.sh runs the wait in a command substitution, so the signalled shell and the wait are two processes. TERM kills the shell
        # at once, as in the base; the Python wait must not outlive it (the base's subshell does run on). INT is held by the shell until
        # the wait ends, in the base too, so it is not signalled here.
        got = self.wait_signalled(root / 'skills', signal.SIGTERM, False, 'pstack-kitchen')
        want = self.wait_signalled(self.base_skills(), signal.SIGTERM, False, 'pstack-kitchen')
        self.assertEqual(got[:3], want[:3])
        self.assertEqual(got, (-signal.SIGTERM, '', '', True))

    def test_finish_and_notify_match_the_bash_in_the_other_variants(self):
        self.mismatches = {}
        self.maxDiff = None
        for skill in ('pstack-pair-guided', 'pstack-trio'):
            for name in ('done report to an idle master', 'done report with open notes', 'queued brief after a done report', 'advice to the master',
                         'devin done report asks for a rotation', 'next with a queued brief', 'report, newest'):
                self.ab_compare(f'{skill}: {name}', self.AB_CASES[name], skill)
        self.assertEqual(self.mismatches, {})

    def test_readlink_f_matches_readlink(self):
        top = self.path / 'rl'
        (top / 'd').mkdir(parents=True)
        (top / 'f').write_text('')
        (top / 'd/g').write_text('')
        for name, target in (('lf', 'f'), ('dang', 'nowhere'), ('loop1', 'loop2'), ('loop2', 'loop1'), ('ld', 'd'), ('dd', 'dang/x'), ('abs', str(top / 'f')),
                             ('up', 'd/../d')):
            (top / name).symlink_to(target)
        paths = ['f', 'd', 'lf', 'dang', 'loop1', 'ld', 'missing', 'd/missing', 'missing/x', 'f/', 'd/', 'lf/', 'ld/', 'dang/', 'missing/', 'missing/..',
                 'f/x', './f', 'd/../f', 'loop1/x', 'd/.', 'd/..', '.', '..', '/', '//', 'f//', 'd//', 'ld/g', 'dd', 'f/..', 'missing/../f',
                 'd/missing/..', 'abs', 'up', 'up/g', 'd/./g', 'd//g', 'missing/.', str(top / 'f'), str(top / 'nope/f'), str(top) + '/d/../lf', '']
        cwd = os.getcwd()
        os.chdir(top)
        self.addCleanup(os.chdir, cwd)
        for path in paths:
            r = subprocess.run(['readlink', '-f', '--', path], capture_output=True, text=True, check=False)
            try:
                got = (0, store.readlink_f(path))
            except Exit as e:
                got = (e.code, '')
            self.assertEqual(got, (r.returncode, r.stdout.rstrip('\n')), repr(path))

    def replies_world(self, name, sent=None, pending=(), dispatch='001-x.md', shown=None, extra=None):
        """A store with a repo (commits c1 < c2 < c3), reports, steps and notes laid out by the caller."""
        if not hasattr(self, 'replies_repo'):
            self.replies_repo = self.git_repo()
            self.replies_shas = [self.commit('c1'), self.commit('c2'), self.commit('c3')]
        st = self.path / name
        for d in ('briefs', 'reports', 'steps', 'notes', 'reviews', 'progress'):
            (st / d).mkdir(parents=True)
        pair = {'git_root': str(self.replies_repo), 'cwd': str(self.replies_repo), 'pending': list(pending), 'sidekick': {'kind': 'claude'}}
        if dispatch:
            pair['dispatch'] = {'brief': f'{st}/briefs/{dispatch}', 'head': self.replies_shas[0]}
        if sent is not None:
            pair['sent'] = sent
        if shown is not None:
            pair['shown'] = shown
        pair.update(extra or {})
        (st / 'pair.json').write_text(json.dumps(pair, indent=2) + '\n')
        return st

    def bash_value(self, snippet):
        """(return code, stdout as a command substitution gives it) of a bash snippet in the harness."""
        r = self.bash(f'rc=0; out="$({snippet})" || rc=$?; printf "%s\\037%s" "$rc" "$out"')
        code, out = r.split('\x1f', 1)
        return int(code), out

    def bash_value_env(self, snippet, env):
        r = self.bash(f'rc=0; out="$({snippet})" || rc=$?; printf "%s\\037%s" "$rc" "$out"', env=env)
        code, out = r.split('\x1f', 1)
        return int(code), out

    def replies_ctx(self):
        return Context('pstack-pair', str(root / 'skills/pstack-pair'), '/s', 'Pair', ('sidekick',), ('plans',), 21, '')

    def test_replies_file_mtime_and_latest_report_match_bash(self):
        st = self.replies_world('r1')
        a, b, c = (st / 'reports' / n for n in ('001-x.md', '002-y.md', '002-z.md'))
        for f, t in ((a, 1_700_000_000), (b, 1_700_000_100), (c, 1_700_000_100)):
            f.write_text('# R\n')
            os.utime(f, (t, t))
        (st / 'reports/notes.md').write_text('x')
        for f in (a, b):
            self.assertEqual(replies.file_mtime(str(f)), int(self.bash_value(f'file_mtime {f}')[1]))
        self.assertEqual(self.bash_value(f'file_mtime {st}/reports/none.md')[0] != 0, True)
        (st / 'reports/link.md').symlink_to(a)
        (st / 'reports/dangling.md').symlink_to('nowhere')
        os.utime(st / 'reports/link.md', (1_700_000_900, 1_700_000_900), follow_symlinks=False)
        for name in ('link.md', 'dangling.md'):
            self.assertEqual(replies.file_mtime(f'{st}/reports/{name}'), int(self.bash_value(f'file_mtime {st}/reports/{name}')[1]), name)
        (st / 'reports/link.md').unlink()
        (st / 'reports/dangling.md').unlink()
        with self.assertRaises(OSError):
            replies.file_mtime(f'{st}/reports/none.md')
        for want in ('', '001', '002', '003', '00'):
            got = replies.latest_report(str(st), want)
            self.assertEqual((0, got) if got else (1, ''), self.bash_value(f'latest_report {st} {want}'), want)
        (st / 'reports/002-z.md').unlink()
        os.utime(b, (1_700_000_500, 1_700_000_500))
        self.assertEqual(replies.latest_report(str(st)), self.bash_value(f'latest_report {st}')[1])

    def test_replies_queue_helpers_match_bash(self):
        st = self.replies_world('r2')
        for locale_name in ('en_US.UTF-8', 'C'):
            q = st / 'queue'
            q.unlink(missing_ok=True)
            pair = json.loads((st / 'pair.json').read_text())
            pair['dispatch'] = {'brief': f'{st}/briefs/\u00e9\u00e91-x.md'}
            (st / 'pair.json').write_text(json.dumps(pair))
            report_path = f'{st}/reports/\u00e9\u00e92-y.md'
            with mock.patch.dict(os.environ, {'LC_ALL': locale_name}):
                ours = replies.queue_note(str(st), report_path)
            self.assertEqual((0, ours.rstrip('\n')), self.bash_value_env(f'queue_note {st} {shlex.quote(report_path)}', {'LC_ALL': locale_name}), locale_name)
        for queue, running, report in ((None, '001-x.md', '001-x.md'), ('', '001-x.md', '002-y.md'), (f'{st}/briefs/002-y.md\n\n', '001-x.md', '001-x.md'),
                                       (f'{st}/briefs/002-y.md\n', '001-x.md', '002-y.md'), (f'{st}/briefs/001-x.md', '001-x.md', '001-x-a2.md'),
                                       ('/a/b\n', '001-x.md', '\u00e9\u00e9-r.md'), (None, '', '001-x.md')):
            q = st / 'queue'
            q.unlink(missing_ok=True)
            if queue is not None:
                q.write_text(queue)
            pair = json.loads((st / 'pair.json').read_text())
            pair['dispatch'] = {'brief': f'{st}/briefs/{running}'} if running else None
            (st / 'pair.json').write_text(json.dumps(pair))
            report_path = f'{st}/reports/{report}'
            self.assertEqual(replies.queued_brief(str(st)), self.bash_value(f'queued_brief {st}')[1], queue)
            self.assertEqual((0, replies.queue_note(str(st), report_path).rstrip('\n')), self.bash_value(f'queue_note {st} {shlex.quote(report_path)}'), (queue, running, report))

    def test_replies_pending_report_and_mark_seen_match_bash(self):
        ctx = self.replies_ctx()
        st = self.replies_world('r3')
        briefs = [f'{st}/briefs/00{n}-x.md' for n in (1, 2, 3, 4)]

        def lay(pending, reports, reviews=(), status='done'):
            pair = json.loads((st / 'pair.json').read_text())
            pair['pending'] = pending
            (st / 'pair.json').write_text(json.dumps(pair, indent=2) + '\n')
            for d in ('reports', 'reviews'):
                for f in (st / d).iterdir():
                    f.unlink()
            for n in reports:
                (st / f'reports/00{n}-x.md').write_text(f'# R\n\nstatus: {status}\nhead: {self.replies_shas[2]}\n')
            for n in reviews:
                (st / f'reviews/00{n}-x.md').write_text('# review\n')

        for case, pending, reports, reviews in (('oldest first', briefs[:3], (2, 3), ()), ('reviewed unit skipped', briefs[:3], (1, 2), (1,)), ('none ready', briefs[:2], (), ()),
                                                ('no pending', [], (1,), ()), ('empty entry', ['', briefs[0]], (1,), ()), ('missing key', None, (1,), ())):
            lay(pending if pending is not None else [], reports, reviews)
            if pending is None:
                pair = json.loads((st / 'pair.json').read_text())
                del pair['pending']
                (st / 'pair.json').write_text(json.dumps(pair))
            got = replies.pending_report(ctx, str(st))
            self.assertEqual((0, got) if got else (1, ''), self.bash_value(f'pending_report {st}'), case)
        lay(briefs[:2], (1, 2), status='partial')
        self.assertEqual(replies.pending_report(ctx, str(st)), self.bash_value(f'pending_report {st}')[1])
        for case, report in (('drops one', f'{st}/reports/002-x.md'), ('drops none', f'{st}/reports/009-x.md'), ('answer suffix', f'{st}/reports/001-x.md')):
            for label in ('bash', 'py'):
                lay(briefs[:3] + [f'{st}/briefs/001-x-a2.md'], (1, 2))
                before = (st / 'pair.json').read_bytes()
                if label == 'bash':
                    self.bash(f'mark_seen {st} {shlex.quote(report)}')
                else:
                    replies.mark_seen(str(st), report)
                after = (st / 'pair.json').read_text()
                if label == 'bash':
                    want = after
                else:
                    self.assertEqual(after, want, case)
        lay([], ())
        (st / 'pair.json').write_text('{"a":   1}')
        self.bash(f'mark_seen {st} x')
        bash_bytes = (st / 'pair.json').read_text()
        (st / 'pair.json').write_text('{"a":   1}')
        replies.mark_seen(str(st), 'x')
        self.assertEqual((st / 'pair.json').read_text(), bash_bytes, 'rewritten even when nothing changed')

    def test_replies_fresh_reply_and_seen_reply_match_bash(self):
        ctx = self.replies_ctx()
        c1, c2, c3 = None, None, None
        st = self.replies_world('r4')
        head = self.replies_shas[2]
        cases = {}
        base = {'file': f'{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1_700_000_000}
        def lay(files, sent, notes=(), steps=None):
            for d in ('reports', 'notes', 'steps'):
                for f in (st / d).iterdir():
                    f.unlink()
            for name, (status, t) in files.items():
                f = st / 'reports' / name
                f.write_text(f'# R\n\nstatus: {status}\nhead: {head}\n')
                os.utime(f, (t, t))
            for n in notes:
                (st / f'notes/001-x-n{n}.md').write_text('# N\n\nstatus: blocking\n')
            if steps:
                (st / 'steps/001-x.tsv').write_text(steps)
            pair = json.loads((st / 'pair.json').read_text())
            pair['sent'] = sent
            (st / 'pair.json').write_text(json.dumps(pair, indent=2) + '\n')
        T0 = 1_700_000_000
        scenarios = {
            'own report': ({'001-x.md': ('done', T0 + 5)}, base),
            'older than the send': ({'001-x.md': ('done', T0 - 5)}, base),
            'objection and own report tie': ({'001-x.md': ('done', T0 + 5), '001-x-s1.md': ('object', T0 + 5)}, base),
            'later objection wins': ({'001-x.md': ('done', T0 + 5), '001-x-s2.md': ('object', T0 + 9), '001-x-s10.md': ('object', T0 + 9)}, base),
            'ask and objection tie on rank': ({'001-x-q1.md': ('ask', T0 + 5), '001-x-s1.md': ('object', T0 + 5)}, base),
            'suffix without digits': ({'001-x-s1abc.md': ('object', T0 + 5), '001-x.md': ('done', T0 + 5)}, base),
            'answer brief': ({'001-x.md': ('done', T0 + 5)}, {**base, 'file': f'{st}/briefs/001-x-a2.md'}),
            'already seen': ({'001-x.md': ('done', T0 + 5)}, {**base, 'seen': f'{st}/reports/001-x.md:{T0 + 5}'}),
            'seen but rewritten': ({'001-x.md': ('done', T0 + 6)}, {**base, 'seen': f'{st}/reports/001-x.md:{T0 + 5}'}),
            'no sent file': ({'001-x.md': ('done', T0 + 5)}, {'kind': 'BRIEF'}),
            'sent null': ({'001-x.md': ('done', T0 + 5)}, None),
            'no epoch': ({'001-x.md': ('done', T0 + 5)}, {'file': f'{st}/briefs/001-x.md'}),
            'float epoch': ({'001-x.md': ('done', T0 + 5)}, {**base, 'epoch': 1700000000.5}),
            'withheld by a note': ({'001-x.md': ('done', T0 + 5), '001-x-s1.md': ('object', T0 + 4)}, base),
            'plan response': ({'001-x.md': ('agree', T0 + 5)}, base),
        }
        for name, (files, sent) in scenarios.items():
            lay(files, sent, notes=(1,) if name == 'withheld by a note' else ())
            got = replies.fresh_reply(ctx, str(st))
            self.assertEqual((0, got) if got else (1, ''), self.bash_value(f'fresh_reply {st}'), name)
            seen = replies.seen_reply(str(st))
            self.assertEqual((0, seen) if seen else (1, ''), self.bash_value(f'seen_reply {st}'), name)
        lay({'001-x.md': ('done', T0 + 5)}, {**base, 'seen': f'{st}/reports/001-x.md'})
        self.assertEqual(replies.seen_reply(str(st)), self.bash_value(f'seen_reply {st}')[1], 'seen without a stamp')

    def test_replies_steps_helpers_match_bash(self):
        ctx = self.replies_ctx()
        c1, c2, c3 = self.replies_world('r5') and self.replies_shas
        st = self.path / 'r5'
        T0 = 1_700_000_000

        def lay(rows, report=None, notes=(), shown=None, brief='001-x.md', sent=None, head=None, report_t=T0 + 100, extra_pair=None):
            for d in ('reports', 'notes', 'steps'):
                for f in (st / d).iterdir():
                    f.unlink()
            if rows is not None:
                (st / 'steps/001-x.tsv').write_text(''.join(rows))
            if report:
                f = st / 'reports/001-x.md'
                f.write_text(f'# R\n\nstatus: {report}\nhead: {head or c3}\n')
                os.utime(f, (report_t, report_t))
            for n in notes:
                (st / f'notes/001-x-n{n}.md').write_text(f'# N\n\nstatus: {"blocking" if n != 9 else "clear"}\n')
            pair = json.loads((st / 'pair.json').read_text())
            pair['dispatch'] = {'brief': f'{st}/briefs/{brief}', 'head': c1} if brief else None
            pair['shown'] = shown
            pair['sent'] = sent
            pair.update(extra_pair or {})
            if shown is None:
                del pair['shown']
            (st / 'pair.json').write_text(json.dumps(pair, indent=2) + '\n')

        row = lambda k, sha, t, res='', summary='s': f'{k}\t{sha}\t{t}\t{res}\t{summary}\n'
        steps3 = [row(1, c1, T0 + 10, '', 'one'), row(2, c2, T0 + 20, '', 'two'), row(3, c3, T0 + 30, '', 'three')]
        cases = {
            'no steps file': dict(rows=None),
            'empty steps file': dict(rows=[]),
            'all due': dict(rows=steps3),
            'report after the last step': dict(rows=steps3, report='done', report_t=T0 + 100),
            'report before the last step': dict(rows=steps3, report='done', report_t=T0 + 25),
            'partial report after the last step': dict(rows=steps3, report='partial'),
            'done report withheld by a note': dict(rows=steps3, report='done', notes=(1,)),
            'noted through 2': dict(rows=steps3, notes=(2,)),
            'noted through 3': dict(rows=steps3, notes=(3,)),
            'shown 2': dict(rows=steps3, shown={'001-x': 2}),
            'shown 3': dict(rows=steps3, shown={'001-x': 3}),
            'shown for another unit': dict(rows=steps3, shown={'002-y': 3}),
            'shown null': dict(rows=steps3, shown={'001-x': None}),
            'shown a word': dict(rows=steps3, shown={'001-x': 'many'}),
            'no dispatch': dict(rows=steps3, brief=None),
            'blank line in the steps': dict(rows=[steps3[0], '\n', steps3[1]]),
            'row without a summary': dict(rows=[f'1\t{c1}\t{T0 + 1}\n', f'2\t{c2}\n']),
            'step numbers as text': dict(rows=[f'a\t{c1}\t{T0}\t\tx\n', f'10\t{c2}\t{T0}\t\ty\n', f'9\t{c3}\t{T0}\t\tz\n']),
            'last row without a trailing newline': dict(rows=[steps3[0], steps3[1].rstrip('\n')]),
        }
        for name, kw in cases.items():
            lay(**kw)
            ours = replies.unreviewed_steps(ctx, str(st))
            self.assertEqual((0, '\n'.join(ours)) if ours else (1, ''), self.bash_value(f'unreviewed_steps {st}'), name)
            brief = f'{st}/briefs/001-x.md'
            due = ctx.variant.steps_due(ctx, str(st), brief if kw.get('brief', '001-x.md') else '')
            code = self.bash_value(f'steps_due {st}')[0]
            self.assertEqual(due, code == 0, name)
            self.assertEqual(code, 2 if name == 'shown a word' else int(not due), name)

        sent = {'file': f'{st}/briefs/001-x.md', 'epoch': T0 + 50}
        for name, kw in {
            'written since the send but open notes': dict(rows=steps3, report='done', notes=(1,), sent=sent),
            'ready': dict(rows=steps3, report='done', sent=sent),
            'written before the send': dict(rows=steps3, report='done', sent=sent, report_t=T0 + 40),
            'partial is ready': dict(rows=steps3, report='partial', sent=sent),
            'stale head': dict(rows=steps3, report='done', sent=sent, head=c1),
            'answer brief': dict(rows=steps3, report='done', sent={**sent, 'file': f'{st}/briefs/001-x-a1.md'}),
            'no sent': dict(rows=steps3, report='done', sent=None),
            'epoch missing': dict(rows=steps3, report='done', sent={'file': sent['file']}),
        }.items():
            lay(**kw)
            got = replies.withheld_report(ctx, str(st))
            self.assertEqual((0, f'{got[0]}\t{got[1]}') if got else (1, ''), self.bash_value(f'withheld_report {st}'), name)

        for name, kw, head_line in (('head in the report', dict(rows=steps3, report='done', head=c2), True), ('placeholder head', dict(rows=steps3, report='done', head='<sha>'), True),
                                    ('no steps', dict(rows=None, report='done'), True), ('noted through 2', dict(rows=steps3, report='done', notes=(2,)), True),
                                    ('empty steps', dict(rows=[], report='done'), True), ('no tab in the last row', dict(rows=[steps3[0], f'{c2}\n'], report='done', head='x'), True)):
            lay(**kw)
            self.assertEqual(replies.review_delta(str(st), str(st / 'reports/001-x.md')), self.bash_value(f'review_delta {st} {st}/reports/001-x.md')[1] + ('\n' if self.bash_value(f'review_delta {st} {st}/reports/001-x.md')[1] else ''), name)

    def bash_at(self, snippet, now, extra=None):
        """The harness with `date +%s` fixed at now, so the clock the bash reads is the clock the Python is given."""
        fake = self.path / f'fakedate-{now}'
        fake.mkdir(exist_ok=True)
        (fake / 'date').write_text(f'#!/bin/sh\nif [ "$1" = "+%s" ]; then echo {now}; else exec {shutil.which("date")} "$@"; fi\n')
        (fake / 'date').chmod(0o755)
        return self.bash(snippet, env={'PATH': f'{fake}:{self.env["PATH"]}', **(extra or {})})

    def test_checkin_case_match_agrees_with_bash_case(self):
        pairs = [('dir/*', ['dir/a', 'dir/a/b', 'dir/', 'dir', 'xdir/a']), ('dir/**', ['dir/a/b', 'dir/', 'dir']), ('*.md', ['a.md', 'd/a.md', '.md', 'a.mdx']),
                 ('a/*/b', ['a/x/b', 'a/x/y/b', 'a//b', 'a/b']), ('src/?.py', ['src/a.py', 'src/ab.py', 'src//.py']), ('tests/test_[ab]*.py', ['tests/test_a.py', 'tests/test_c.py', 'tests/test_b1.py']),
                 ('f[!a]g', ['fag', 'fbg', 'f]g']), ('f[^a]g', ['fag', 'fbg']), ('f[]]g', ['f]g', 'fag']), ('f[a-c]g', ['fbg', 'fdg', 'f-g']), ('f[[:digit:]]g', ['f1g', 'fag']),
                 ('a,b/c', ['a,b/c', 'a', 'b/c']), ('a b/*', ['a b/c', 'a/c']), ('{a,b}/x', ['{a,b}/x', 'a/x']), ('weird\\*name', ['weird*name', 'weirdXname']), ('[abc', ['[abc', 'a']),
                 ('a[', ['a[', 'a']), ('', ['', 'a']), ('*', ['', 'a/b/c', '.hidden']), ('.*', ['.a', 'a']), ('dir/*/', ['dir/a/', 'dir/a']), ('é*', ['éa', 'ea']), ('a?c', ['a\u00e9c', 'ac']),
                 ('f[a\\]]g', ['f]g', 'fag']), ('f[a-]g', ['f-g', 'fag']), ('[[:alpha:]]*', ['abc', '1bc'])]
        script = ''.join(f'p={shlex.quote(pat)}; case {shlex.quote(text)} in $p) printf 1;; *) printf 0;; esac\n' for pat, texts in pairs for text in texts)
        bash = self.bash(script)
        ours = ''.join('1' if checkin.case_match(pat, text) else '0' for pat, texts in pairs for text in texts)
        self.assertEqual(ours, bash)

    def test_checkin_scope_entries_and_steer_scope_entries_match_bash(self):
        st = self.replies_world('c2')
        brief = st / 'briefs/001-x.md'
        brief.write_text('\n'.join([
            '# Brief', '', '## Scope', '', 'may write:', '- skills/a/** \u2014 the whole skill', '- `tests/x, y.py` (quoted comma path)', '- docs/ (the docs)', '- plain.md',
            '-  spaced', '- a -- note', '- b \u2013 note', '- `unterminated', '- `a`b` tail', '- ', '- weird (note) (second)', 'not a bullet', '',
            'must not write:', '- skills/b/**', '- `x/y`', '', '## Other', '', 'may write:', '- after/**', '', 'may write:', '- again/**', '- trailing.md']))
        for want in ('may', 'mustnot', 'neither'):
            self.assertEqual('\n'.join(checkin.scope_entries(str(brief), want)), self.bash_value(f'scope_entries {brief} {want}')[1], want)
        self.assertEqual(checkin.scope_entries(str(st / 'briefs/none.md'), 'may'), [])
        steers = st / 'steers'
        steers.mkdir(exist_ok=True)
        def steer(name, kind='narrow', effect='', sup='none'):
            (steers / name).write_text(f'# S\n\nkind: {kind}\nsupersedes: {sup}\nscope effect: {effect}\n')
        steer('001-x-s1.md', effect='may write "a/**" and `b/**` and "c d/**"')
        steer('001-x-s2.md', effect='may write: `lib/**` (extra note)')
        steer('001-x-s3.md', effect='may write')
        steer('001-x-s4.md', effect='none')
        steer('001-x-s5.md', effect='must not write: z/**')
        steer('001-x-s6.md', kind='withdraw', sup=f'{steers}/001-x-s1.md note words')
        steer('001-x-s7.md', effect='may write "e/**"', sup='001-x-s2.md')
        steer('001-x-s8.md', kind='withdraw', sup='none')
        steer('002-y-s1.md', effect='may write "other/**"')
        (steers / '001-x-s9.md.draft').write_text('x')
        steer('001-x-s10.md', effect='may write "oops')
        steer('001-x-s0.md', effect='may write "first/**"')
        self.assertEqual('\n'.join(checkin.steer_scope_entries(str(st), '001', errexit=False)), self.bash_value(f'steer_scope_entries {st} 001')[1])
        self.assertEqual('\n'.join(checkin.steer_scope_entries(str(st), '009')), self.bash_value(f'steer_scope_entries {st} 009')[1])
        # Under set -e a steer with a quote and no quoted string ends the function (the grep pipeline fails): s10 sorts before s2.
        dies = self.bash(f'steer_scope_entries {st} 001', check=False)
        self.assertEqual(''.join(f'{e}\n' for e in checkin.steer_scope_entries(str(st), '001')), dies.stdout)
        self.assertNotEqual(dies.returncode, 0)

    def test_checkin_status_paths_and_tree_activity_match_bash(self):
        repo = self.git_repo()
        base = self.commit('base')
        for name in ('keep.txt', 'gone.txt', 'old name.txt', 'comma, file.txt'):
            (repo / name).write_text(name)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'files')
        head = self.git('rev-parse', 'HEAD')
        now = int(time.time())
        self.assertEqual(checkin.status_paths(str(repo)), [])
        self.assertEqual(checkin.tree_activity(str(repo), '', now), None)
        self.assertEqual(self.bash_value(f'tree_activity {repo} ""')[1], '')
        (repo / 'keep.txt').write_text('changed')
        (repo / 'gone.txt').unlink()
        self.git('mv', 'old name.txt', 'new name.txt')
        (repo / 'comma, file.txt').write_text('edited')
        (repo / 'sub dir').mkdir()
        (repo / 'sub dir/new\u00e9.txt').write_text('x')
        (repo / 'future.txt').write_text('x')
        (repo / 'far.txt').write_text('x')
        for name, delta in (('keep.txt', -500), ('comma, file.txt', -90), ('sub dir/new\u00e9.txt', -300), ('future.txt', 30), ('far.txt', 120), ('new name.txt', -1000)):
            os.utime(repo / name, (now + delta, now + delta))
        self.assertEqual('\n'.join(checkin.status_paths(str(repo))), self.bash_value(f'status_paths {repo}')[1])
        for h in ('', head, base):
            ours = checkin.tree_activity(str(repo), h, now)
            self.assertEqual(str(ours or ''), self.bash_at(f'tree_activity {repo} {h!r}', now).strip(), h)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'later')
        for h in (head, base, 'nonsense'):
            ours = checkin.tree_activity(str(repo), h, now)
            self.assertEqual(str(ours or ''), self.bash_at(f'tree_activity {repo} {h!r}', now).strip(), h)
        self.assertEqual(checkin.status_paths(str(self.path / 'not-a-repo')), [])

    def test_checkin_busy_command_and_sidekick_activity_match_bash(self):
        now = int(time.time())
        pane = 'p-checkin'
        envs = {'other-pane': {**self.env, 'HERDR_PANE_ID': 'p-other'}, 'shell': {**self.env, 'HERDR_PANE_ID': pane}, 'plain': {**self.env, 'HERDR_PANE_ID': pane}}
        sock = str(self.path / 'herdr.sock')
        procs = []
        for kind, env in (('sleep-other', envs['other-pane']), ('sleep-pane', {**envs['plain'], 'HERDR_SOCKET_PATH': sock}), ('sleep-nosock', envs['plain']),
                          ('sleep-othersock', {**envs['plain'], 'HERDR_SOCKET_PATH': '/elsewhere'})):
            procs.append(subprocess.Popen(['sleep', '60'], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        sh = subprocess.Popen(['sh', '-c', 'cat >/dev/null; true'], env={**envs['shell'], 'HERDR_SOCKET_PATH': sock}, stdin=subprocess.PIPE, start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: [(os.killpg(p.pid, 9), p.wait()) for p in [*procs, sh]])
        for since, sock_env in ((now - 3600, sock), (now - 3600, ''), (now + 5, sock), (now - 3600, '/elsewhere')):
            extra = {'HERDR_SOCKET_PATH': sock_env}
            ours = checkin.busy_command(pane, since, now + 130, env=extra)
            bash = self.bash_at(f'busy_command {pane} {since}', now + 130, extra)
            self.assertEqual(ours, bash, (since, sock_env))
        self.assertTrue(ours == '' or ours.endswith('m'))
        self.assertEqual(checkin.busy_command('', 0, now), self.bash_at('busy_command "" 0', now))
        only_shell = subprocess.Popen(['sh', '-c', 'read x'], env={**self.env, 'HERDR_PANE_ID': 'p-only-shell'}, stdin=subprocess.PIPE, start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (os.killpg(only_shell.pid, 9), only_shell.wait()))
        ours = checkin.busy_command('p-only-shell', now - 10, now + 200, env={})
        self.assertEqual(ours, self.bash_at('busy_command p-only-shell ' + str(now - 10), now + 200, {'HERDR_SOCKET_PATH': ''}))
        self.assertEqual(ours, 'sh for 0m')
        st = self.replies_world('c4')
        iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 3600))
        for pair_patch in ({'sidekick': {'pane_id': pane}, 'dispatch': {'brief': 'b', 'at': iso}}, {'sidekick': {'pane_id': pane}, 'dispatch': {'brief': 'b', 'at': 'garbage'}},
                           {'sidekick': {}, 'dispatch': {'brief': 'b', 'at': iso}}, {'sidekick': {'pane_id': pane}, 'dispatch': {'brief': 'b'}}):
            (st / 'pair.json').write_text(json.dumps(pair_patch))
            self.assertEqual(checkin.sidekick_activity(str(st), now + 130), self.bash_at(f'sidekick_activity {st}', now + 130, {'HERDR_SOCKET_PATH': ''}), pair_patch)

    def test_checkin_digest_matches_bash(self):
        # The clock is fixed: bash runs with a `date` that answers +%s with NOW, and Python is given NOW, so the minutes
        # in the digest are compared as they are, not masked.
        ctx = self.replies_ctx()
        st = self.replies_world('c5')
        repo = self.replies_repo
        c1, c2, c3 = self.replies_shas
        now = int(time.time())
        pane = 'p-digest'
        iso = lambda t: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))
        brief = st / 'briefs/001-x.md'
        progress = st / 'progress/001-x.md'
        seen = st / 'progress/001-x.md.seen'
        scope = '\n'.join(['# Brief', '', 'playbook: feature', 'timebox: 45 minutes', '', '## Scope', '', 'may write:', '- src/** \u2014 the code', '- docs/ (the docs)',
                            '- `my, file.txt`', '- *.md', '', 'must not write:', '- lib/**', '', '## Steps', '', '1. do'])
        procs = []
        self.addCleanup(lambda: [(os.killpg(p.pid, 9), p.wait()) for p in procs])

        def reset():
            self.git('reset', '-q', '--hard', c3)
            self.git('clean', '-fdq')
            for d in ('steers', 'reports', 'steps', 'notes'):
                shutil.rmtree(st / d, ignore_errors=True)
                (st / d).mkdir()
            for f in (progress, seen):
                f.unlink(missing_ok=True)
            brief.write_text(scope + '\n')

        def write(path, text='x', age=0):
            f = repo / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            os.utime(f, (now - age, now - age))

        def pair(head=c3, at=None, pane_id=pane):
            (st / 'pair.json').write_text(json.dumps({'git_root': str(repo), 'cwd': str(repo), 'sidekick': {'pane_id': pane_id},
                                                      'dispatch': {'brief': str(brief), 'at': at or iso(now - 20 * 60), 'head': head}}, indent=2) + '\n')

        def log(lines, age_m, seen_value=None):
            progress.write_text(''.join(f'{i:02d}:00 line {i}\n' for i in range(lines)))
            os.utime(progress, (now - age_m * 60, now - age_m * 60))
            if seen_value is not None:
                seen.write_text(seen_value)

        def sleeper():
            procs.append(subprocess.Popen(['sleep', '60'], env={**self.env, 'HERDR_PANE_ID': pane}, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

        def steer(name, effect='', kind='narrow'):
            (st / 'steers' / name).write_text(f'# S\n\nkind: {kind}\nsupersedes: none\nscope effect: {effect}\n')

        def scenario_a():
            write('src/a.py'), write('docs/n/b.md'), write('my, file.txt'), write('README.md'), write('lib/x.py'), write('top.txt'), log(3, 1)

        def scenario_commits():
            pair(head=c1)
            write('src/new.py')
            log(2, 2, '1\n')

        def scenario_stale():
            log(4, 30, '2\n')

        def scenario_running():
            sleeper()
            log(4, 30, '0\n')

        def scenario_fresh_run():
            sleeper()
            log(4, 1)

        def scenario_editing():
            write('src/hot.py', age=120)
            log(4, 30)

        def scenario_old_edit():
            write('src/cold.py', age=3600)
            log(4, 30)

        def scenario_steers():
            steer('001-x-s1.md', 'may write "extra/**" and "more/*.txt"')
            steer('001-x-s2.md', 'may write: `other/**` (note)')
            steer('001-x-s3.md', kind='withdraw')
            (st / 'reports/001-x-s2.md').write_text('# R\n\nstatus: object\n')
            write('extra/a.py'), write('more/b.txt'), write('other/c.py'), write('src/ok.py'), write('nope/z.py')
            progress.write_text('00:01 steer s1 applied: ok\n00:02 steer s2 applied: ok\n00:03 plain\n')

        def scenario_caps():
            for i in range(35):
                write(f'outside/f{i:02d}.txt')
            log(30, 1, '0\n')

        def scenario_no_log():
            write('src/a.py')

        def scenario_odd_seen():
            log(5, 1, 'banana\n')

        def scenario_big_seen():
            log(5, 1, '99\n')

        def scenario_steps():
            (st / 'steps/001-x.tsv').write_text(f'1\t{c1}\t{now}\t\tone\n2\t{c2}\t{now}\tn1\ttwo\n\n')
            (st / 'notes/001-x-n1.md').write_text('# N\n\nstatus: blocking\n')
            (st / 'notes/001-x-n2.md').write_text('# N\n\nstatus: blocking\n')
            log(2, 1)

        def scenario_unbalanced():
            steer('001-x-s1.md', 'may write "oops')
            steer('001-x-s2.md', 'may write "later/**"')
            write('later/a.py'), write('src/b.py')
            log(1, 1)

        def scenario_untimed():
            brief.write_text('# Brief\n\n## Scope\n\nmay write:\n- src/**\n')
            write('src/a.py')
            log(1, 1)

        def scenario_bad_at():
            pair(at='yesterday')
            write('src/a.py')
            log(1, 1)

        def scenario_no_head():
            pair(head='')
            write('src/a.py'), write('top.txt')
            log(1, 1)

        def scenario_rename():
            self.git('mv', 'c1', 'src_moved')
            write('src_moved', 'moved')
            log(1, 1)

        scenarios = [scenario_a, scenario_commits, scenario_stale, scenario_running, scenario_fresh_run, scenario_editing, scenario_old_edit, scenario_steers, scenario_caps,
                     scenario_no_log, scenario_odd_seen, scenario_big_seen, scenario_steps, scenario_unbalanced, scenario_untimed, scenario_bad_at, scenario_no_head, scenario_rename]
        outputs = {}
        for build in scenarios:
            for p in procs:
                os.killpg(p.pid, 9), p.wait()
            procs.clear()
            reset()
            pair()
            build()
            # Back-date the dispatch so the sleeper started just now counts as started since it.
            progress_before = progress.read_bytes() if progress.exists() else None
            seen_before = seen.read_bytes() if seen.exists() else None
            self.git('status', '--porcelain')
            r = self.bash_at(f'checkin {st} 9', now, {'HERDR_SOCKET_PATH': ''})
            seen_bash = seen.read_text() if seen.exists() else None
            if seen_before is None:
                seen.unlink(missing_ok=True)
            else:
                seen.write_bytes(seen_before)
            with mock.patch.dict(os.environ, {'HERDR_SOCKET_PATH': ''}):
                ours = checkin.checkin(ctx, str(st), '9', now)
            seen_py = seen.read_text() if seen.exists() else None
            self.assertEqual(ours, r, build.__name__)
            self.assertEqual(seen_py, seen_bash, build.__name__)
            outputs[build.__name__] = ours
        self.assertIn('STALE', outputs['scenario_stale'])
        self.assertIn('running: sleep for', outputs['scenario_running'])
        self.assertIn('editing: last change 2m ago', outputs['scenario_editing'])
        self.assertIn('outside scope: 35', outputs['scenario_caps'])
        self.assertIn('steers: 3 sent, 2 applied, 1 objected', outputs['scenario_steers'])
        self.assertIn('2 commits since dispatch', outputs['scenario_commits'])
        self.assertIn('elapsed ?m of 45m', outputs['scenario_bad_at'])

    def test_provider_error_context_and_cuts_match_bash(self):
        texts = ['Error: Reached free model rate limit. Your limit will reset in 50 minutes.', 'nothing to see', 'x' * 100 + ' Rate-Limit ' + 'y' * 300,
                 'a rate limit then another RATE LIMIT and overloaded close by', 'Usage limit reached. wait for usage to reset at 5pm; billing_error',
                 '\u00e9' * 70 + ' capacity issues ' + '\u00e9' * 120, 'caf\u00e9 \u2192 unauthorized \u2192 forbidden \u2192 Too Many Requests',
                 'first line rate limit\nsecond line fetch failed\nthird', 'ECONNRESET', 'x rate_limit x', 'ratelimit', 'rate\tlimit rate  limit', '']
        for locale_name in ('en_US.UTF-8', 'C'):
            with mock.patch.dict(os.environ, {'LC_ALL': locale_name}):
                for text in texts:
                    for before, after in ((60, 100), (60, 60)):
                        ours = providers.grep_o_context(text, before, after)
                        r = subprocess.run(['bash', '-c', f'{self.provider_re_line()}\nprintf "%s\\n" "$1" | grep -oiE ".{{0,{before}}}($provider_error_re).{{0,{after}}}" || true', 'x', text],
                                           env={**self.env, 'LC_ALL': locale_name}, capture_output=True, check=True)
                        self.assertEqual('\n'.join(ours).encode('utf-8', 'surrogateescape') + (b'\n' if ours else b''), r.stdout, (locale_name, text, before, after))
                    cut = subprocess.run(['bash', '-c', 'printf "%s\\n" "$1" | cut -c1-240', 'x', text], env={**self.env, 'LC_ALL': locale_name}, capture_output=True, check=True).stdout
                    self.assertEqual(providers.cut_chars(text, 240).encode('utf-8', 'surrogateescape') + b'\n', cut, (locale_name, 'cut', text[:20]))
        self.assertEqual(providers.PROVIDER_ERROR_RE, re.search(r"^provider_error_re='(.*)'$", (scripts / 'pair-core.sh').read_text(), re.M).group(1))

    def provider_re_line(self):
        return re.search(r"^provider_error_re='.*'$", (scripts / 'pair-core.sh').read_text(), re.M).group(0)

    def provider_home(self, name):
        home = self.path / name
        home.mkdir()
        return home

    def pi_line(self, role='assistant', stop='error', error='boom', **extra):
        message = {'role': role, **({'stopReason': stop} if stop is not None else {}), **({'errorMessage': error} if error is not None else {}), **extra}
        return json.dumps({'type': 'message', 'message': message}, ensure_ascii=False)

    def test_provider_agent_session_error_matches_bash(self):
        home = self.provider_home('h1')
        log = home / 'session.jsonl'
        long_text = '\u00e9\u2192' * 200 + ' tail'
        cases = {
            'last turn errored': [self.pi_line('user', None, None), self.pi_line(error='Reached free model rate limit')],
            'recovered': [self.pi_line(error='old'), self.pi_line(stop='stop', error=None)],
            'tab newline backslash': [self.pi_line(error='a\tb\nc\\d\re')],
            'long non-ascii': [self.pi_line(error=long_text)],
            'long ascii': [self.pi_line(error='x' * 500)],
            'error without text': [self.pi_line(error=None)],
            'empty text': [self.pi_line(error='')],
            'user turn last': [self.pi_line(error='boom'), self.pi_line('user', 'error', 'ignored')],
            'garbage line stops the stream': [self.pi_line(error='before'), 'not json', self.pi_line(error='after')],
            'object text stops the stream': [self.pi_line(error='before'), self.pi_line(error={'a': 1}), self.pi_line(error='after')],
            'numeric text': [self.pi_line(error=42)],
            'false text': [self.pi_line(error=False)],
            'true stop reason': [self.pi_line(stop=True, error='x')],
            'not objects': ['[1,2]', '3', '"s"', 'null', self.pi_line(error='kept')],
            'no message': [json.dumps({'type': 'x'}), self.pi_line(error='kept')],
            'more than 200 lines': [self.pi_line(error='dropped')] + [self.pi_line('user', None, None)] * 250,
            'empty file': [],
        }
        for name, lines in cases.items():
            log.write_text('\n'.join(lines) + ('\n' if lines else ''))
            for locale_name in ('en_US.UTF-8', 'C'):
                with mock.patch.dict(os.environ, {'LC_ALL': locale_name, 'HOME': str(home)}):
                    ours = providers.agent_session_error(str(log))
                r = self.bash(f'agent_session_error {log}', env={'LC_ALL': locale_name, 'HOME': str(home)})
                self.assertEqual(ours + '\n' if ours else '', r, (name, locale_name))
        self.assertEqual(providers.agent_session_error('', ''), '')
        self.assertEqual(providers.agent_session_error(str(home / 'none.jsonl'), ''), self.bash(f'agent_session_error {home}/none.jsonl'))
        cwd = '/work/my project'
        directory = home / '.pi/agent/sessions/--work-my project--'
        directory.mkdir(parents=True)
        for i, error in enumerate(('older', 'newest')):
            f = directory / f'{i}.jsonl'
            f.write_text(self.pi_line(error=error) + '\n')
            os.utime(f, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
        with mock.patch.dict(os.environ, {'HOME': str(home)}):
            ours = providers.agent_session_error('', cwd)
            ours_missing = providers.agent_session_error(str(home / 'gone'), cwd)
        self.assertEqual((ours, ours_missing), ('newest', 'newest'))
        self.assertEqual(self.bash(f'agent_session_error "" {shlex.quote(cwd)}', env={'HOME': str(home)}), 'newest\n')
        self.assertEqual(self.bash(f'agent_session_error {home}/gone {shlex.quote(cwd)}', env={'HOME': str(home)}), 'newest\n')

    def test_provider_muse_session_error_matches_bash(self):
        data = self.provider_home('d1')
        cwd = '/work/muse project'
        sessions = data / 'muse/sessions'

        def make(rel, text, t):
            f = sessions / rel / 'session.jsonl'
            f.parent.mkdir(parents=True)
            f.write_text(text)
            os.utime(f, (t, t))

        make('a/b/c/d1', f'{{"cwd": "{cwd}"}}\nUsage limit reached, wait for usage to reset at 5pm\nlater line\n', 1_700_000_100)
        make('a/b/c/d2', 'another project\nrate limit hit\n', 1_700_000_300)
        make('a/b/c/d3', f'{{"cwd": "{cwd}"}}\n' + 'ok line\n' * 10 + 'HTTP 402 Billing verification failed (billing_error) \u00e9\u00e9' * 3 + '\n' + 'x' * 400 + '\n', 1_700_000_200)
        for locale_name in ('en_US.UTF-8', 'C'):
            for xdg in (str(data), ''):
                env = {'LC_ALL': locale_name, 'XDG_DATA_HOME': xdg, 'HOME': str(data.parent / 'nohome')}
                if not xdg:
                    (data.parent / 'nohome/.local').mkdir(parents=True, exist_ok=True)
                    if not (data.parent / 'nohome/.local/share').exists():
                        (data.parent / 'nohome/.local/share').symlink_to(data)
                with mock.patch.dict(os.environ, env):
                    ours = providers.muse_session_error(cwd)
                r = self.bash(f'muse_session_error {shlex.quote(cwd)}', env=env)
                self.assertEqual(ours + '\n' if ours else '', r, (locale_name, xdg))
        # With no log naming the directory the function returns nothing; called on its own (not in a substitution, as
        # provider_hint does) the bash ends the shell there: its search assignment fails under set -e.
        with mock.patch.dict(os.environ, {'XDG_DATA_HOME': str(data), 'LC_ALL': 'en_US.UTF-8'}):
            self.assertEqual(providers.muse_session_error(''), '')
            self.assertEqual(providers.muse_session_error('/nowhere'), '')
        self.assertEqual(self.bash('muse_session_error ""', env={'XDG_DATA_HOME': str(data)}), '')
        for xdg, code in ((str(data), 1), (str(self.path / 'no-such-data'), 2)):
            r = self.bash('muse_session_error /nowhere', env={'XDG_DATA_HOME': xdg}, check=False)
            self.assertEqual((r.returncode, r.stdout), (code, ''), xdg)
            self.assertEqual(self.bash('x="$(muse_session_error /nowhere)"; printf "[%s]" "$x"', env={'XDG_DATA_HOME': xdg}), '[]', xdg)
            with mock.patch.dict(os.environ, {'XDG_DATA_HOME': xdg}):
                self.assertEqual(providers.muse_session_error('/nowhere'), '')

    def provider_world(self, label, kind, fallback=None, agent=True, screen=None, session=None, pair_extra=None, home_logs=None, muse_logs=None):
        """A store, a fake herdr with the sidekick's pane, and logs under a temp HOME, built the same for every label."""
        top = self.path / label
        st, fake, home = top / 'st', top / 'fake', top / 'home'
        for d in (st, fake / 'agents', fake / 'panes', fake / 'screen', fake / 'sessions', home):
            d.mkdir(parents=True)
        sidekick = {'name': 's1', 'kind': kind, 'pane_id': 'p5', **({'fallback': {'kind': fallback}} if fallback else {})}
        (st / 'pair.json').write_text(json.dumps({'cwd': '/work/proj', 'sidekick': sidekick, **(pair_extra or {})}, indent=2) + '\n')
        if agent:
            (fake / 'agents/s1').write_text(f'idle {kind}\n')
            (fake / 'panes/p5').write_text('s1')
        if screen is not None:
            (fake / 'screen/s1').write_text(screen)
        if session is not None:
            log = top / 'prime-session.jsonl'
            log.write_text(session)
            (fake / 'sessions/s1').write_text(str(log))
        for rel, text in (home_logs or {}).items():
            f = home / '.pi/agent/sessions/--work-proj--' / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        for rel, text in (muse_logs or {}).items():
            f = home / '.local/share/muse/sessions' / rel / 'session.jsonl'
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        env = {'FAKE': str(fake), 'HOME': str(home), 'HERDR_SOCKET_PATH': str(top / 'sock'), 'XDG_DATA_HOME': '', 'LC_ALL': 'en_US.UTF-8', 'PATH': self.env['PATH']}
        return st, env

    def provider_both(self, name, call_bash, call_py, **kw):
        outcomes = []
        for label, run in (('bash', call_bash), ('py', call_py)):
            st, env = self.provider_world(f'{name}-{label}', **kw)
            code, out = run(st, env)
            outcomes.append((code, out.replace(str(self.path / f'{name}-{label}'), 'ROOT'), (st / 'pair.json').read_text().replace(str(self.path / f'{name}-{label}'), 'ROOT')))
        self.assertEqual(outcomes[0], outcomes[1], name)
        return outcomes[1]

    def provider_hint_both(self, name, phase='', **kw):
        def call_bash(st, env):
            r = self.bash(f'provider_hint {st} {phase}', env=env, check=False)
            return r.returncode, r.stdout

        def call_py(st, env):
            with mock.patch.dict(os.environ, env):
                try:
                    return 0, providers.provider_hint(str(st), phase)
                except Exit as e:
                    return e.code, ''

        return self.provider_both(name, call_bash, call_py, **kw)

    def test_provider_hint_matches_bash(self):
        error_log = self.pi_line(error='Reached free model rate limit. Your limit will reset in 50 minutes.') + '\n'
        ok_log = self.pi_line(stop='stop', error=None) + '\n'
        prime_log = self.pi_line(error='capacity issues, try later \u00e9') + '\n'
        cases = {
            'claude pane error with fallback': dict(kind='claude', fallback='devin', screen='working... Error: 429 Too Many Requests, rate limit hit\nnext line\n'),
            'claude pane error without fallback': dict(kind='claude', screen='Unauthorized: no api key found\n'),
            'claude pane clean': dict(kind='claude', fallback='devin', screen='all good\n'),
            'claude fallback is itself': dict(kind='claude', fallback='claude', screen='rate limit\n'),
            'claude bootstrap': dict(kind='claude', fallback='devin', screen='rate limit\n', phase='bootstrap'),
            'claude pane error far from the edges': dict(kind='claude', fallback='pi', screen='a ' * 100 + 'overloaded' + ' b' * 100 + '\n'),
            'agent gone': dict(kind='claude', fallback='pi', agent=False),
            'agent gone, no fallback': dict(kind='devin', agent=False),
            'pi gone with a logged error': dict(kind='pi', fallback='devin', agent=False, home_logs={'1.jsonl': error_log}),
            'pi pane clean, logged error': dict(kind='pi', fallback='devin', screen='quiet\n', home_logs={'1.jsonl': error_log}),
            'pi pane clean, recovered': dict(kind='pi', fallback='devin', screen='quiet\n', home_logs={'1.jsonl': ok_log}),
            'pi pane error beats the log': dict(kind='pi', fallback='devin', screen='fetch failed\n', home_logs={'1.jsonl': error_log}),
            'prime-agent with a session log': dict(kind='prime-agent', fallback='devin', screen='rate limit on screen is ignored\n', session=prime_log),
            'prime-agent gone': dict(kind='prime-agent', fallback='devin', agent=False, session=prime_log),
            'prime-agent without a session': dict(kind='prime-agent', fallback='devin', screen='x\n'),
            'muse with a logged error': dict(kind='muse', fallback='devin', screen='quiet\n', muse_logs={'a/b/c/d': '/work/proj\nBilling verification failed\n'}),
            'muse gone with a logged error': dict(kind='muse', fallback='devin', agent=False, muse_logs={'a/b/c/d': '/work/proj\nusage limit reached\n'}),
            'muse with no log': dict(kind='muse', fallback='devin', screen='quiet\n'),
            'muse with logs that name another directory': dict(kind='muse', fallback='devin', screen='quiet\n', muse_logs={'a/b/c/d': '/elsewhere\nrate limit\n'}),
            'other kind, no fallback key': dict(kind='codex', screen='forbidden\n'),
        }
        outcomes = {}
        for name, kw in cases.items():
            kw = dict(kw)
            outcomes[name] = self.provider_hint_both(name.replace(' ', '-').replace(',', ''), kw.pop('phase', ''), **kw)
        self.assertIn('provider_error: the claude sidekick exited', outcomes['agent gone'][1])
        self.assertIn('next: pair.sh failover', outcomes['agent gone'][1])
        self.assertEqual(outcomes['muse with no log'][:2], (0, ''))
        self.assertEqual(outcomes['muse with logs that name another directory'][:2], (0, ''))
        self.assertEqual(outcomes['claude pane clean'][1], '')
        self.assertIn('capacity issues', outcomes['prime-agent with a session log'][1])
        self.assertIn('"session"', outcomes['prime-agent with a session log'][2])

    def test_provider_record_prime_session_and_role_error_match_bash(self):
        prime_log = self.pi_line(error='capacity issues') + '\n'
        for name, kw in {'recorded': dict(session=prime_log), 'no session on file': dict(), 'agent gone': dict(agent=False, session=prime_log),
                         'role without a section': dict(session=prime_log, pair_extra={'sidekick': None}), 'consultant role': dict(session=prime_log, pair_extra={'consultant': {'name': 's1', 'pane_id': 'p5', 'kind': 'prime-agent'}}),
                         'other kind': dict(kind='claude', session=prime_log)}.items():
            kw = {'kind': 'prime-agent', **kw}
            role = 'consultant' if name == 'consultant role' else 'sidekick'
            def call_bash(st, env, role=role):
                r = self.bash(f'record_prime_session {st} {role} && echo yes || echo no', env=env, check=False)
                return r.returncode, r.stdout
            def call_py(st, env, role=role):
                with mock.patch.dict(os.environ, env):
                    return 0, 'yes\n' if providers.record_prime_session(str(st), role) else 'no\n'
            self.provider_both('rec-' + name.replace(' ', '-'), call_bash, call_py, **kw)

            def call_bash2(st, env, role=role):
                r = self.bash(f'prime_role_error {st} {role}', env=env, check=False)
                return r.returncode, r.stdout
            def call_py2(st, env, role=role):
                with mock.patch.dict(os.environ, env):
                    out = providers.prime_role_error(str(st), role)
                return 0, out + '\n' if out else ''
            self.provider_both('err-' + name.replace(' ', '-'), call_bash2, call_py2, **kw)

    def test_bash_int_matches_the_bash_integer_test(self):
        inputs = [' 5', '5 ', ' 5 ', '+5', '-0', '-5', '5_0', '\u0665', '', '0x10', '010', '1e3', '1.0', '+', '-', '5 5', '5\n', '\t5', '99999999999999999999',
                  '9223372036854775807', '9223372036854775808', '-9223372036854775808', '--5', '+-5', '00', '0 ', '1 \n', '\n1', '5\x0b', '\u0663', ' ', '8', '08', '٨']
        for text in inputs:
            rc = {}
            for want in (0, 8):
                r = subprocess.run(['bash', '-c', '[ "$1" -eq ' + str(want) + ' ]', '_', text], capture_output=True, check=False)
                rc[want] = r.returncode
            ours = store.bash_int(text)
            self.assertEqual(ours is None, rc[0] == 2, repr(text))
            if ours is not None:
                self.assertEqual((ours == 0, ours == 8), (rc[0] == 0, rc[8] == 0), repr(text))
        self.assertEqual((store.bash_int('010'), store.bash_int(' -7 '), store.bash_int('+5')), (10, -7, 5))

    def test_wait_finish_iso_epoch_matches_bash(self):
        for stamp in ('2026-10-16T11:39:33Z', '1970-01-01T00:00:00Z', '1969-12-31T23:59:59Z', '2026-02-29T00:00:00Z', '2024-02-29T12:00:00Z', '2026-10-16T11:39:33',
                      '2026-10-16 11:39:33Z', '2026-1-1T0:0:0Z', 'garbage', '', '2026-10-16T11:39:33.5Z', ' 2026-10-16T11:39:33Z', '2026-13-01T00:00:00Z', '2026-10-16T25:00:00Z', '2026-10-16T11:39:33Z ', '2026-10-16T11:39:33Z junk', '2026-10-16T11:39:33Zjunk', '2026-04-31T00:00:00Z',
                      '0001-01-01T00:00:00Z', '12345-01-01T00:00:00Z', '2026-0-1T00:00:00Z', '2026-10-16T11:39:62Z'):
            r = self.bash(f'iso_epoch {shlex.quote(stamp)}', check=False, base=True)
            ours = checkin.iso_epoch(stamp)
            self.assertEqual((0, str(ours)) if ours is not None else (5, ''), (r.returncode, r.stdout.strip()), repr(stamp))

    def test_wait_finish_opts_match_bash(self):
        core = str(self.base_skills() / 'pstack-pair/scripts/pair-core.sh')
        mismatched = []
        cases = [[], ['--timeout', '1000'], ['--every', '2'], ['--send-only'], ['--timeout', '5', '--every', '3'], ['--every', '3', '--timeout', '7'], ['--bogus'], ['x', '--timeout', '9', 'y'],
                 ['--timeout'], ['--every'], ['--timeout', 'abc'], ['--every', 'abc'], ['--timeout', '1.5'], ['--every', '1.5'], ['--every', ''], ['--timeout', ''], ['--every', '0'],
                 ['--timeout', '0'], ['--timeout', '-5'], ['--timeout', '0x10'], ['--every', '1x'], ['--timeout', ' 5 '], ['--every', '011'], ['--timeout', '2*3'], ['--timeout', '(5+1)*1000'],
                 ['--timeout', '1000', '--bogus', '--every', '2'], ['--every', '07+08'], ['--timeout', '9999999999999'], ['--every', '2#101'], ['--timeout', '10 / 0'], ['--timeout', '5 5'],
                 ['--timeout', '(5'], ['--timeout', '5)'], ['--timeout', '-1500000']]
        for args in cases:
            snippet = ('rest=(); timeout=540000; send_only=0; wait_opts "$@"; printf "%s\\037%s\\037%s" "$timeout" "$checkin_interval_m" "$send_only"; '
                       'for r in ${rest[@]+"${rest[@]}"}; do printf "\\037%s" "$r"; done')
            r = subprocess.run(['bash', '-c', f'set -euo pipefail\nskill_root=/x; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\nroles=(sidekick); store_dirs=(plans); slug_max=21\n. {core}\n{snippet}', '_', *args],
                               env=self.env, capture_output=True, text=True, check=False)
            try:
                timeout, interval, send_only, rest = wait.wait_opts(args)
                ours = (0, '\x1f'.join([timeout, str(interval), '1' if send_only else '0']) + ''.join('\x1f' + x for x in rest), '')
            except Exit as e:
                ours = (e.code, '', e.stderr)
            # the file and line the bash names are the working tree's for Python and e6e83e3's for the oracle: compare the message
            mask = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
            if (ours[0], ours[1], mask(ours[2])) != (r.returncode, r.stdout, mask(r.stderr)):
                mismatched.append((args, mask(ours[2]), mask(r.stderr)))
        self.assertEqual(mismatched, [])

    def test_wait_finish_reply_mode_matches_bash(self):
        for ready, steps_due in (('', False), ('', True), ('/r.md', False), ('/r.md', True)):
            r = self.bash(f'ready={shlex.quote(ready)}; steps_due={int(steps_due)}; reply_mode', base=True)
            self.assertEqual(wait.reply_mode(wait.WaitResult(0, '', '', ready, steps_due)), r.strip(), (ready, steps_due))

    FW_NOW = 1_800_000_000

    def finish_wait_both(self, name, mode='reply', code=0, out='', err='', report='', interval=9, **spec):
        """finish_wait in bash and in Python on identical worlds: (return code, stdout, stderr, pair.json, files, events added)."""
        results = []
        for label in ('bash', 'py'):
            top, st, env, fill = self.ab_world(f'{name}-{label}', {'agents': {'s1': 'idle claude'}, **spec})
            ctx = Context('pstack-pair', str(root / 'skills/pstack-pair'), str(top / 'state'), 'Pair', ('sidekick',), ('plans',), 21, '')
            rep = fill(report)
            for log in (st / 'progress').glob('*.md'):
                os.utime(log, (self.FW_NOW - 600, self.FW_NOW - 600))
            if label == 'bash':
                fake = self.path / f'fakedate-{self.FW_NOW}'
                fake.mkdir(exist_ok=True)
                (fake / 'date').write_text(f'#!/bin/sh\nif [ "$1" = "+%s" ]; then echo {self.FW_NOW}; else exec {shutil.which("date")} "$@"; fi\n')
                (fake / 'date').chmod(0o755)
                # events.tsv is pre-seeded with one row, so the header branch unit 002 removed from event() cannot show
                script = (f'set -euo pipefail\nskill_root={self.base_skills() / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\nroles=(sidekick); store_dirs=(plans); slug_max=21\n'
                          f'. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\ncheckin_interval_m={interval}\nfinish_wait {st} {code} "$1" "$2" {mode} {shlex.quote(rep)}')
                r = subprocess.run(['bash', '-c', script, '_', out, err], env={**env, 'PATH': f'{fake}:{env["PATH"]}'}, capture_output=True, text=True, check=False)
                rc, so, se = r.returncode, r.stdout, r.stderr
            else:
                so_io, se_io = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(so_io), contextlib.redirect_stderr(se_io):
                    try:
                        rc = wait.finish_wait(ctx, str(st), interval, wait.WaitResult(code, out, err.rstrip('\n'), rep, mode == 'steps'), mode, rep, now=self.FW_NOW)
                    except Exit as e:
                        rc = e.code
                        se_io.write(e.stderr)
                so, se = so_io.getvalue(), se_io.getvalue()
            norm = lambda text: re.sub(r':1[0-9]{9}\b', ':<MTIME>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text.replace(str(top), '<T>')))
            files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
            events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()[1:]]
            results.append((rc, norm(so), norm(se), files, events))
        if results[0] != results[1]:
            diff = {}
            for i, k in enumerate(('rc', 'stdout', 'stderr', 'files', 'events')):
                if results[0][i] != results[1][i]:
                    diff[k] = ({f: (results[1][i].get(f), results[0][i].get(f)) for f in {*results[0][i], *results[1][i]} if results[0][i].get(f) != results[1][i].get(f)}
                               if k == 'files' else (results[1][i], results[0][i]))
            self.fw_mismatches[name] = diff
        return results[1]

    def test_wait_finish_matches_bash(self):
        done = '# R\n\nstatus: done\nhead: {c3}\n'
        sent = {'sent': {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1}, 'pending': ['{st}/briefs/001-x.md']}
        rep = '{st}/reports/001-x.md'
        working = {'s1': 'working claude'}
        ok_out = '{"result":{"agent":{"agent_status":"idle"}}}'
        T0 = self.FW_NOW
        digest_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(T0 - 1230))
        digest_pair = {**sent, 'dispatch': {'brief': '{st}/briefs/001-x.md', 'at': digest_at, 'head': '{c1}'}}
        results = {}
        self.fw_mismatches = {}
        cases = {
            'done report': dict(report=rep, out=ok_out, pair=sent, files={'reports/001-x.md': done}),
            'done report, blocked state': dict(report=rep, out='{"result":{"agent":{"agent_status":"blocked"}}}', pair=sent, files={'reports/001-x.md': done}),
            'partial report': dict(report=rep, out=ok_out, pair=sent, files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
            'earlier report of a queued brief': dict(report='{st}/reports/001-x.md', out=ok_out, pair={'sent': {'file': '{st}/briefs/002-y.md', 'epoch': 1, 'seen': 'keep'}, 'pending': ['{st}/briefs/001-x.md', '{st}/briefs/002-y.md']},
                                                      files={'reports/001-x.md': done, 'queue': '{st}/briefs/002-y.md\n'}),
            'reply to a sent unit marks it shown': dict(report=rep, out='', pair={'sent': {'file': '{st}/briefs/001-x-a2.md', 'epoch': 1}}, files={'reports/001-x.md': done}),
            'the report is gone when its time is read': dict(report='{st}/reports/009-x.md', out=ok_out, pair={'sent': {'file': '{st}/briefs/009-x.md', 'epoch': 1}}),
            'an empty sent file, a report': dict(report=rep, out=ok_out, pair={'sent': {'file': '', 'epoch': 1}}, files={'reports/001-x.md': done}),
            'an empty sent file, idle with a reply shown': dict(out=ok_out, pair={'sent': {'file': '', 'epoch': 1, 'seen': '{st}/reports/001-x.md:1'}}, files={'reports/001-x.md': done}),
            'no sent field': dict(report=rep, out=ok_out, pair={'sent': None}, files={'reports/001-x.md': done}),
            'steps mode': dict(mode='steps', out=ok_out, pair=sent, files={'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n3\t{c3}\t7\tn1\tthree\n'}),
            'steps mode, nothing to review': dict(mode='steps', out=ok_out, pair=sent),
            'steps mode, a step number that is not JSON': dict(mode='steps', out=ok_out, pair=sent, files={'steps/001-x.tsv': 'a\t{c1}\t5\t\tone\n'}),
            'idle, reply already shown': dict(out=ok_out, pair={'sent': {'file': '{st}/briefs/001-x.md', 'epoch': 1, 'seen': '{st}/reports/001-x.md:1'}}, files={'reports/001-x.md': done}),
            'report written but not ready': dict(out=ok_out, pair=sent, files={'reports/001-x.md': done, 'steps/001-x.tsv': '1\t{c2}\t5\t\tone\n2\t{c3}\t5\t\ttwo\n', 'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'report missing, sidekick working': dict(out=ok_out, pair=digest_pair, agents=working, files={'progress/001-x.md': '10:00 one\n10:05 two\n', 'briefs/001-x.md': '# Brief\n\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/**\n'}),
            'report missing, sidekick working, quiet': dict(out=ok_out, pair=digest_pair, agents=working, wait_env={'PAIR_QUIET_WAIT': '1'}, files={'progress/001-x.md': '10:00 one\n'}),
            'report missing, sidekick idle': dict(out=ok_out, pair=digest_pair, agents={'s1': 'idle claude'}, screens={'s1': 'Error: rate limit hit\n'}, files={'briefs/001-x.md': '# Brief\n'}),
            'report missing, sidekick idle, nothing to say': dict(out=ok_out, pair=digest_pair, agents={'s1': 'idle claude'}, screens={'s1': 'all quiet\n'}),
            'report missing, blocked state': dict(out='{"result":{"agent":{"agent_status":"blocked"}}}', pair=digest_pair, agents={'s1': 'blocked claude'}, screens={'s1': 'Unauthorized\n'}),
            'report missing, blocked and unready': dict(out='{"result":{"agent":{"agent_status":"blocked"}}}', pair=sent, files={'reports/001-x.md': done, 'steps/001-x.tsv': '1\t{c2}\t5\t\tone\n2\t{c3}\t5\t\ttwo\n', 'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'herdr timeout payload': dict(code=1, err='{"error":{"code":"timeout"}}', pair=digest_pair, agents=working, files={'progress/001-x.md': '10:00 one\n'}),
            'herdr timeout, idle sidekick': dict(code=1, err='{"error":{"code":"timeout"}}', pair=digest_pair, agents={'s1': 'idle claude'}),
            'herdr error with a code': dict(code=1, err='{"error":{"code":"not_found"}}', pair=digest_pair),
            'herdr error as a string': dict(code=1, err='{"error":"boom"}', pair=digest_pair),
            'herdr error that is not JSON': dict(code=1, err='herdr: connection refused', pair=digest_pair),
            'herdr error that is empty': dict(code=1, err='', pair=digest_pair),
            'herdr error as an object without a code': dict(code=1, err='{"error":{"message":"m"}}', pair=digest_pair),
            'herdr error, two payloads': dict(code=1, err='{"a":1}\n{"error":{"code":"x"}}', pair=digest_pair),
            'agent get output that is not JSON': dict(out='not json', pair=digest_pair),
            'agent get output that is empty': dict(out='', pair=digest_pair),
            'agent get output with a null status': dict(out='{"result":{"agent":{"agent_status":null}}}', pair=digest_pair),
            'agent get output of the wrong type': dict(out='{"result":"x"}', pair=digest_pair),
            'paused store': dict(report=rep, out=ok_out, pair=sent, files={'reports/001-x.md': done, 'paused': 'lunch\nmore\n'}),
            'mode any with reports': dict(mode='any', out=ok_out, pair=sent, files={'reports/001-x.md': done, 'reports/002-y.md': '# R\n\nstatus: partial\n'}),
            'mode any without reports': dict(mode='any', out=ok_out, pair=sent),
            'a queue note after the report': dict(report=rep, out=ok_out, pair={**sent, 'dispatch': {'brief': '{st}/briefs/002-y.md'}}, files={'reports/001-x.md': done, 'queue': '{st}/briefs/003-z.md\n'}),
            'review delta after the report': dict(report=rep, out=ok_out, pair=sent, files={'reports/001-x.md': done, 'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t5\t\ttwo\n'}),
        }
        for name, kw in cases.items():
            kw = dict(kw)
            pair = kw.pop('pair', None)
            env_extra = kw.pop('wait_env', None)
            spec = {k: kw.pop(k) for k in ('files', 'agents', 'screens') if k in kw}
            if pair is not None:
                spec['pair'] = pair
            if env_extra:
                spec['env'] = env_extra
            results[name] = self.finish_wait_both(name.replace(' ', '-').replace(',', ''), **kw, **spec)
        self.maxDiff = None
        self.assertEqual(self.fw_mismatches, {})
        self.assertEqual(results['done report'][0], 0)
        self.assertEqual(results['done report, blocked state'][0], 3)
        self.assertEqual(results['idle, reply already shown'][0], 4)
        self.assertIn('idle: the reply to 001-x.md was already shown', results['idle, reply already shown'][1])
        self.assertIn('check-in: 001-x.md', results['report missing, sidekick working'][1])
        self.assertIn('state: ', results['herdr timeout payload'][1])
        self.assertIn('boom', results['herdr error as a string'][2])

    def test_fresh_per_brief_matches_bash(self):
        for kind in ('devin', 'pi', 'prime-agent', 'claude', 'codex', 'null', '', 'Devin', 'pi '):
            code = self.bash(f'if fresh_per_brief {shlex.quote(kind)}; then echo 0; else echo 1; fi').strip()
            self.assertEqual(int(not dispatch.fresh_per_brief(kind)), int(code), kind)

    def test_record_dispatch_matches_bash(self):
        repo = self.git_repo()
        head = self.commit('c1')
        results = []
        for label in ('bash', 'py'):
            for case, extra in (('empty store', {}), ('an answered unit', {'old': True}), ('known heads and pending', {'heads': {'002-y': 'kept', '001-x': None}, 'pending': ['z', 'a']}),
                                ('no sidekick', {'nosidekick': True})):
                st = self.path / f'{label}-{case.replace(" ", "-")}'
                for d in ('briefs', 'reports'):
                    (st / d).mkdir(parents=True)
                (st / 'briefs/002-y.md').write_text('# B\n')
                if extra.get('old'):
                    (st / 'reports/002-y.md').write_text('# old\n')
                    os.utime(st / 'reports/002-y.md', (1_700_000_000, 1_700_000_000))
                pair = {'cwd': str(repo), 'sidekick': {'generation': 3}, **{k: v for k, v in extra.items() if k in ('heads', 'pending')}}
                if extra.get('nosidekick'):
                    del pair['sidekick']
                (st / 'pair.json').write_text(json.dumps(pair, indent=2) + '\n')
                brief = f'{st}/briefs/002-y.md'
                if label == 'bash':
                    self.bash(f'record_dispatch {st} {brief}')
                else:
                    dispatch.record_dispatch(str(st), brief)
                doc = (st / 'pair.json').read_text().replace(str(st), 'ST')
                results.append((case, re.sub(r'"(at|epoch)": ("[0-9T:Z-]+"|[0-9]+)', r'"\1": T', doc)))
        half = len(results) // 2
        self.assertEqual(results[:half], results[half:])
        self.assertIn(head, results[0][1])
        self.assertIn('1700000000', results[1][1])

    def test_expected_report_matches_bash(self):
        for brief in ('/s/briefs/001-x.md', '001-x-a2.md', '/s/briefs/001-x-a12', '/s/briefs/001-x-a.md', '/s/briefs/-a1.md', '.md', '/s/briefs/001-a1-x.md'):
            self.assertEqual(store.expected_report('/s', brief), self.bash(f'expected_report /s {shlex.quote(brief)}').rstrip('\n'), brief)

    def test_load_variant_is_lazy_and_picks_the_skills_class_by_path(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import pstack_core.cli; '
                'assert "pstack_core.variant" not in sys.modules; print("lazy")')
        r = subprocess.run([sys.executable, '-I', '-c', code, str(scripts)], capture_output=True, text=True, check=False)
        self.assertEqual((r.stdout, r.returncode), ('lazy\n', 0), r.stderr)
        names = {}
        for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen'):
            ctx = Context(skill, str(root / 'skills' / skill), '/s', 'Pair', (), (), 21, '')
            self.assertIsNone(ctx._variant)
            names[skill] = [c.__name__ for c in type(ctx.variant).__mro__[:-1]]
            self.assertIs(ctx.variant, ctx.variant)
        self.assertEqual(names, {'pstack-pair': ['Variant'], 'pstack-pair-guided': ['Variant'],
                                 'pstack-trio': ['TrioVariant', 'Variant'], 'pstack-kitchen': ['KitchenVariant', 'TrioVariant', 'Variant']})

    def test_trunk_ref_range_floor_and_review_base_match_bash(self):
        repo = self.git_repo('work')
        self.commit('c1')
        self.assertEqual(review.trunk_ref(str(repo)) or '', self.bash(f'trunk_ref {repo}').strip(), 'no trunk')
        self.assertEqual(review.trunk_ref(str(repo)), None)
        self.git('checkout', '-q', '-b', 'main')
        c2 = self.commit('c2')
        self.git('checkout', '-q', '-b', 'feature')
        f1, f2 = self.commit('f1'), self.commit('f2')
        st = self.fixture_store()
        pair = {'git_root': str(repo), 'dispatch': {'head': c2}}

        def compare(label, base, tip, steps=None, heads=None):
            (st / 'pair.json').write_text(json.dumps({**pair, 'heads': heads or {}, 'dispatch': {'head': base}}))
            (st / 'steps').mkdir(exist_ok=True)
            steps_path = st / 'steps/001-one.tsv'
            steps_path.unlink(missing_ok=True)
            if steps:
                steps_path.write_text(steps)
            rev, note = review.range_floor(str(repo), base, tip)
            out = self.bash(f'range_floor {repo} {base} {tip}; printf "%s\\n%s\\n" "$range_floor_rev" "$range_floor_note"')
            self.assertEqual(f'{rev}\n{note}\n', out, label)
            self.assertEqual(review.review_base(str(st), '001-one'), self.bash(f'review_base {st} 001-one').rstrip('\n'), label)
            return rev, note

        self.assertEqual(review.trunk_ref(str(repo)), 'refs/heads/main')
        self.assertEqual(compare('base is the merge-base', c2, f2), (c2, ''))
        self.assertEqual(compare('with a steps file', c2, f2, steps=f'1\t{f1}\ta\t\n2\t{f2}\tb\t\n'), (c2, ''))
        self.git('checkout', '-q', 'main')
        c3 = self.commit('c3')
        self.git('checkout', '-q', 'feature')
        self.git('rebase', '-q', 'main')
        g2 = self.git('rev-parse', 'HEAD')
        rev, note = compare('base behind after a rebase', c2, g2)
        self.assertEqual((rev, note), (c3, f'recorded base {c2[:9]} is behind where its branch leaves heads/main; measuring from {c3[:9]}'))
        compare('behind, from a steps file tip', c2, g2, steps=f'1\t{g2}\ta\t\n')
        compare('base from heads', c2, g2, heads={'001-one': c2})
        (st / 'pair.json').write_text(json.dumps({'git_root': False, 'cwd': str(repo)}))
        self.assertEqual(review.review_base(str(st), '001-one'), self.bash(f'review_base {st} 001-one').rstrip('\n'))
        (st / 'pair.json').write_text(json.dumps({'dispatch': {'head': c2}}))
        self.assertEqual(review.review_base(str(st), '001-one'), self.bash(f'review_base {st} 001-one').rstrip('\n'))
        for allow in (False, True):
            rev, note = review.range_floor(str(repo), c2, c3, allow_head=allow)
            flag = ' --allow-head' if allow else ''
            out = self.bash(f'range_floor {repo} {c2} {c3}{flag}; printf "%s\\n%s\\n" "$range_floor_rev" "$range_floor_note"')
            self.assertEqual(f'{rev}\n{note}\n', out, allow)
            self.assertEqual((rev, note), (c3, f'recorded base {c2[:9]} is behind where its branch leaves heads/main; measuring from {c3[:9]}') if allow else (c2, ''))

    def test_require_filled_matches_bash(self):
        f = self.path / 'f.md'
        guided = '^(decided by|status): .*\\|'
        quoted = shlex.quote(guided)
        ctxs = {'plain': Context('x', '', '', '', (), (), 1, ''), 'guided': Context('x', '', '', '', (), (), 1, guided)}
        for text in ('status: done\n', 'status: done | partial\n', 'decided by: human\n', 'a {{b}} c\n', 'x\n'):
            f.write_text(text)
            for name, ctx in ctxs.items():
                r = self.bash(f"unfilled_re={quoted}; [ {name} = plain ] && unfilled_re=; require_filled {f}", check=False)
                try:
                    review.require_filled(ctx, str(f))
                    ours = (0, '')
                except Die as e:
                    ours = (1, f'pair.sh: {e.message}\n')
                self.assertEqual(ours, (r.returncode, r.stderr), (text, name))
        self.assertIsNone(review.require_filled(ctxs['guided'], str(self.path / 'missing.md')))


    legacy_dir = None

    @classmethod
    def legacy_pair(cls):
        """pair.sh as it was before the port, extracted from git, the oracle for commands that no longer have a bash body."""
        if cls.legacy_dir is None:
            cls.legacy_tmp = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.legacy_tmp.cleanup)
            git = subprocess.run(['git', '-C', str(root), 'archive', LEGACY, 'skills'], capture_output=True, check=False)
            if git.returncode != 0:
                raise unittest.SkipTest(f'{LEGACY} is not in this checkout')
            subprocess.run(['tar', '-x', '-C', cls.legacy_tmp.name], input=git.stdout, check=True)
            cls.legacy_dir = Path(cls.legacy_tmp.name)
        return cls.legacy_dir / 'skills/pstack-pair/scripts/pair.sh'

    def run_pair(self, *args, script=None, extra_env=None):
        env = {**self.env, 'FAKE': str(self.path), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0', 'HERDR_SOCKET_PATH': str(self.path / 'herdr.sock'),
               'XDG_STATE_HOME': str(self.path / 'state'), 'HOME': str(self.path), **(extra_env or {})}
        return subprocess.run([str(script or scripts / 'pair.sh'), *args], env=env, capture_output=True, text=True, check=False)

    def test_new_steer_counts_files_caps_fresh_steers_and_answers_objections(self):
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = self.path / 'state/pstack/pair/demo'
        out = lambda r: (r.returncode, r.stdout, r.stderr)
        self.assertEqual(out(self.run_pair('new-steer', str(st), '001')), (1, '', 'pair.sh: no brief for unit 001\n'))
        plan = self.run_pair('new-plan', str(st), 'p').stdout.strip()
        self.assertEqual(plan, f'{st}/plans/001-p.md')
        brief = self.run_pair('new-brief', str(st), 'one').stdout.strip()
        self.assertEqual(brief, f'{st}/briefs/002-one.md')
        self.assertEqual(out(self.run_pair('new-brief', str(st), 'Bad')), (1, '', 'pair.sh: brief slug must be lowercase [a-z0-9_-]\n'))
        self.assertEqual(out(self.run_pair('new-plan', str(st))), (1, '', 'pair.sh: usage: pair.sh new-plan <store> <slug>\n'))
        self.assertEqual(out(self.run_pair('new-steer', str(st), '002', '--bogus')), (1, '', 'pair.sh: unknown option --bogus\n'))
        first = self.run_pair('new-steer', str(st), '002').stdout.strip()
        second = self.run_pair('new-steer', str(st), '002').stdout.strip()
        self.assertEqual((first, second), (f'{st}/steers/002-one-s1.md', f'{st}/steers/002-one-s2.md'))
        text = Path(second).read_text()
        self.assertIn('supersedes: none', text)
        self.assertNotIn('{{SEQ}}', text)
        self.assertEqual(out(self.run_pair('new-steer', str(st), '002')),
                         (7, '', 'pair.sh: unit 002 already has two fresh steers; stop the unit and re-brief, or pass --force 7\n'))
        self.assertEqual(self.run_pair('new-steer', str(st), '002', '--force').stdout.strip(), f'{st}/steers/002-one-s3.md')
        self.assertEqual(out(self.run_pair('new-steer', str(st), '002', '--supersedes', first)),
                         (1, '', 'pair.sh: no open objection on unit 002 to answer; send a fresh steer instead\n'))
        (st / 'reports/002-one-s2.md').write_text('status: object\n')
        answer = self.run_pair('new-steer', str(st), '002', '--supersedes', first).stdout.strip()
        self.assertEqual(answer, f'{st}/steers/002-one-s4.md')
        self.assertIn(f'supersedes: {first}', Path(answer).read_text())
        self.assertEqual(out(self.run_pair('new-steer', str(st), '002', '--supersedes', str(st / 'nope.md'))),
                         (1, '', f'pair.sh: superseded steer not found: {st}/nope.md\n'))

    def test_new_note_checks_in_bash_order_and_defaults_through_to_the_shown_step(self):
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = self.path / 'state/pstack/pair/demo'
        err = lambda *a: self.run_pair('new-note', str(st), *a).stderr
        self.assertEqual(err('001'), 'pair.sh: no brief for unit 001\n')
        (st / 'briefs/001-one.md').write_text('')
        self.assertEqual(err('001', '--bogus'), 'pair.sh: unknown option --bogus\n')
        self.assertEqual(err('001'), 'pair.sh: unit 001 has no steps recorded\n')
        (st / 'steps').mkdir(exist_ok=True)
        (st / 'steps/001-one.tsv').write_text('1\taaaaaaaaaaaa\t1\t\tone\n2\tbbbbbbbbbbbb\t2\t\ttwo\n')
        self.assertEqual(err('001', '--through', '3'), 'pair.sh: unit 001 has no step 3\n')
        self.assertEqual(err('001', '--through', 'x'), 'pair.sh: unit 001 has no step x\n')
        pair = json.loads((st / 'pair.json').read_text())
        pair['shown'] = {'001-one': 1}
        (st / 'pair.json').write_text(json.dumps(pair))
        first = self.run_pair('new-note', str(st), '001')
        self.assertEqual(first.stdout, f'{st}/notes/001-one-n1.md.draft\n')
        self.assertIn('through: step 1\n', Path(first.stdout.strip()).read_text())
        (st / 'notes/001-one-n1.md').write_text('status: clear\n')
        second = self.run_pair('new-note', str(st), '001')
        self.assertIn('through: step 2\n', Path(second.stdout.strip()).read_text())
        (st / 'notes/001-one-n2.md').write_text('status: clear\n')
        self.assertEqual(err('001'), 'pair.sh: unit 001 is reviewed through step 2 already; wait for the next steps\n')


    def test_new_steer_supersedes_exits_silently_when_the_path_cannot_resolve_and_names_a_missing_file(self):
        legacy = self.legacy_pair()
        for script in (legacy, None):
            self.assertEqual(self.run_pair('init', 'demo', script=script).returncode, 0)
        out = {}
        for label, script in (('legacy', legacy), ('port', None)):
            st = self.path / f'state/pstack/{"pair"}/demo'
            (st / 'briefs').mkdir(exist_ok=True)
            (st / 'briefs/001-one.md').write_text('')
            (st / 'steers').mkdir(exist_ok=True)
            for f in (st / 'steers').iterdir():
                f.unlink()
            cases = {}
            for name, target in (('no parent', '/nonexistent-dir/steer.md'), ('not a file', str(st / 'steers/none.md'))):
                r = self.run_pair('new-steer', str(st), '001', '--supersedes', target, script=script)
                cases[name] = (r.returncode, r.stdout, r.stderr)
            out[label] = cases
        self.assertEqual(out['legacy'], out['port'])
        self.assertEqual(out['port']['no parent'], (1, '', ''))
        self.assertTrue(out['port']['not a file'][2].startswith('pair.sh: superseded steer not found: '))


    def fake_herdr_dir(self, label, state, busy):
        fake = self.path / f'fake-{label}'
        for d in ('agents', 'busy', 'screen', 'panes'):
            (fake / d).mkdir(parents=True)
        if state != 'absent':
            (fake / 'agents/w').write_text(f'{state} devin\n')
        if busy:
            (fake / 'busy/w').write_text('')
        return fake

    def test_agent_status_matches_bash_in_every_state_and_calls_the_same_herdr(self):
        for i, (state, busy) in enumerate((('idle', False), ('working', False), ('done', True), ('done', False), ('idle', True),
                                           ('blocked', False), ('absent', False))):
            theirs_dir, ours_dir = self.fake_herdr_dir(f'bash{i}', state, busy), self.fake_herdr_dir(f'py{i}', state, busy)
            theirs = self.bash('agent_status w; agent_kind w', {'FAKE': str(theirs_dir)})
            with mock.patch.dict(os.environ, {**self.env, 'FAKE': str(ours_dir)}):
                status = herdr.agent_status('w')
                kind = herdr.agent_kind('w')
                ours = f'{status}\n' + (f'{kind}\n' if kind else '')
            self.assertEqual(ours, theirs, (state, busy))
            self.assertEqual((ours_dir / 'calls.log').read_text(), (theirs_dir / 'calls.log').read_text(), (state, busy))
            self.assertEqual(ours.splitlines()[0], 'working' if busy and state in ('idle', 'done') else state, (state, busy))


    @unittest.skipUnless(any(l.lower().replace('-', '') == 'en_us.utf8' for l in subprocess.run(['locale', '-a'], capture_output=True, text=True).stdout.split()),
                         'needs the en_US.UTF-8 locale')
    def test_first_brief_follows_the_callers_collation_like_bash(self):
        legacy = self.legacy_pair()
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = self.path / 'state/pstack/pair/demo'
        for name in ('001-a-c.md', '001-a_b.md'):
            (st / 'briefs' / name).write_text('')
        picked = {}
        for locale_name in ('C', 'en_US.UTF-8'):
            env = {'LC_ALL': locale_name}
            for label, script in (('legacy', legacy), ('port', None)):
                for f in (st / 'steers').glob('*'):
                    f.unlink()
                out = self.run_pair('new-steer', str(st), '001', script=script, extra_env=env).stdout.strip()
                picked[locale_name, label] = out
        self.assertEqual(picked['C', 'legacy'], picked['C', 'port'])
        self.assertEqual(picked['en_US.UTF-8', 'legacy'], picked['en_US.UTF-8', 'port'])
        self.assertNotEqual(picked['C', 'port'], picked['en_US.UTF-8', 'port'])


    def status_pair(self, variant, script_root, *args, keep_order=False):
        env = {**self.env, 'FAKE': str(self.path / 'fake'), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0', 'HERDR_SOCKET_PATH': str(self.path / 'herdr.sock'),
               'XDG_STATE_HOME': str(self.path / 'state'), 'HOME': str(self.path)}
        r = subprocess.run([str(script_root / 'skills' / variant / 'scripts/pair.sh'), *args], env=env, capture_output=True, text=True, check=False)
        stamp = re.compile(r'generated: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')
        return r.returncode, stamp.sub('generated: T', r.stdout), (r.stderr.splitlines() if keep_order else sorted(r.stderr.splitlines()))

    def populate_store(self, st, consultant):
        def put(rel, text='', age=0):
            f = st / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            t = 1_700_000_000 - age
            os.utime(f, (t, t))
        put('plans/001-plan-a.md')
        put('briefs/002-b_one.md')
        put('briefs/002-b-two.md')
        put('briefs/003-c.direction.md')
        put('briefs/003-c.md')
        put('reports/000-ready.md', 'status: done\n', 90)
        put('reports/002-b_one.md', 'status: partial\n', 50)
        put('reports/002-b_one-s1.md', 'status: object\n', 60)
        put('reports/003-c.md', '# nothing\n', 10)
        put('reviews/002-b_one.md', 'verdict: agreed\n')
        put('reviews/003-c.md', '# no verdict\n')
        put('queue', f'{st}/briefs/003-c.md\n\n')
        put('paused', 'for lunch\nsecond line\n')
        put('gates.md', '# Gates\n\n- one\n- two\nnot a gate\n')
        put('answers/002-b_one-a1.md')
        put('reports/002-b_one-q1.md', 'status: asking\n', 100)
        put('reports/003-c-q2.md', 'status: asking\n', 100)
        if consultant:
            put('advice/002-b_one.md', 'status: agree\n', 5)
            put('advice/002-b_one-c1.md', 'status: object\n', 20)
            put('advice/003-c.md', '# no status\n', 1)
        pair = json.loads((st / 'pair.json').read_text())
        pair['scratch'] = ['/tmp/one', 'two words', '*.nothing-glob']
        (st / 'pair.json').write_text(json.dumps(pair))

    def test_status_matches_pair_sh_at_the_legacy_commit_in_every_variant(self):
        legacy = self.legacy_pair().parents[3]
        new_root = root
        for variant, consultant in (('pstack-pair', False), ('pstack-pair-guided', False), ('pstack-trio', True)):
            fake = self.path / 'fake'
            for d in ('agents', 'busy', 'screen', 'panes', 'sessions'):
                (fake / d).mkdir(parents=True, exist_ok=True)
            label = variant.removeprefix('pstack-')
            self.assertEqual(self.status_pair(variant, new_root, 'init', 'demo')[0], 0, variant)
            st = self.path / f'state/pstack/{label}/demo'
            self.populate_store(st, consultant)
            (fake / 'agents/demo-sidekick').write_text('done pi\n')
            (fake / 'busy/demo-sidekick').write_text('')
            results = {}
            for name, tree in (('legacy', legacy), ('port', new_root)):
                (fake / 'calls.log').unlink(missing_ok=True)
                results[name] = self.status_pair(variant, tree, 'status', str(st)) + (re.sub(r'generated: \S+', 'generated: T', (st / 'status.md').read_text()), (fake / 'calls.log').read_text())
            self.assertEqual(results['legacy'], results['port'], variant)
            code, out, _err, saved, calls = results['port']
            self.assertEqual(code, 0)
            self.assertEqual(saved, out, variant)
            self.assertIn('| 002 | brief | b_one | partial |', out)
            self.assertIn('agent read demo-sidekick --source visible --lines 40', calls)
            self.assertIn('unanswered asks: 1\n', out)
            self.assertIn('open gates: 2\n', out)
            self.assertEqual('(c1)' in out, consultant, variant)

    def test_status_names_an_unassigned_role_and_a_master_with_no_name_like_bash(self):
        legacy = self.legacy_pair().parents[3]
        fake = self.path / 'fake'
        for d in ('agents', 'busy', 'screen', 'panes'):
            (fake / d).mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.status_pair('pstack-trio', root, 'init', 'demo')[0], 0)
        st = self.path / 'state/pstack/trio/demo'
        pair = json.loads((st / 'pair.json').read_text())
        pair['master'] = {}
        pair['sidekick'] = {}
        pair['consultant'] = {'name': 'demo-consultant', 'pane_id': False}
        pair['scratch'] = [' ', '']
        (st / 'pair.json').write_text(json.dumps(pair))
        results = {}
        for name, tree in (('legacy', legacy), ('port', root)):
            (fake / 'calls.log').unlink(missing_ok=True)
            code, out, err = self.status_pair('pstack-trio', tree, 'status', str(st))
            results[name] = (code, out, err, re.sub(r'generated: \S+', 'generated: T', (st / 'status.md').read_text()), (fake / 'calls.log').read_text())
        self.assertEqual(results['legacy'], results['port'])
        self.assertIn('master: null (null) absent\n', results['port'][1])
        self.assertIn('sidekick: null (unassigned) absent\n', results['port'][1])
        self.assertIn('agent get null\n', results['port'][4])
        self.assertIn('\nopen scratch worktrees (remove with pair.sh scratch <store> <id> --remove):\n  \n', results['port'][1])


    def test_status_on_a_store_without_pair_json_prints_the_header_then_dies_like_bash(self):
        legacy = self.legacy_pair().parents[3]
        for label, make_dir in (('dir exists', True), ('dir missing', False)):
            st = self.path / f'state/pstack/pair/{label.replace(" ", "-")}'
            if make_dir:
                st.mkdir(parents=True)
            results = {}
            for name, tree in (('legacy', legacy), ('port', root)):
                (st / 'status.md').unlink(missing_ok=True)
                code, out, err = self.status_pair('pstack-pair', tree, 'status', str(st), keep_order=make_dir)
                saved = re.sub(r'generated: \S+', 'generated: T', (st / 'status.md').read_text()) if (st / 'status.md').exists() else None
                results[name] = (code, out, err, saved)
            self.assertEqual(results['legacy'], results['port'], label)
            self.assertEqual(results['port'][3], results['port'][1] if make_dir else None, label)
            self.assertEqual(results['port'][0], 1 if not make_dir else 2)
            self.assertTrue(results['port'][1].startswith('# Pair status\n\ngenerated: T\n'), label)
            self.assertIn(f'pair.sh: no pair.json in {st}; run: pair.sh init <slug>', results['port'][2])
            self.assertEqual(any('tee:' in l for l in results['port'][2]), not make_dir, label)


    def test_update_waits_on_a_held_lock_and_applies_its_mutation_to_the_file_as_it_is_after_acquiring_it(self):
        st = self.fixture_store()
        pair = st / 'pair.json'
        original = pair.read_text()
        (st / '.pair.lock').mkdir()
        waits = []

        def holder_finishes(_seconds):
            waits.append(pair.read_text())
            pair.write_text(json.dumps({**json.loads(pair.read_text()), 'holder': 1}))
            (st / '.pair.lock').rmdir()
            self.bash(f"json_update {st} '.bash = true'")

        with mock.patch.object(store.time, 'sleep', holder_finishes):
            store.update(str(st), lambda doc: doc.update(py='1'))
        self.assertEqual(waits, [original])
        final = json.loads(pair.read_text())
        self.assertEqual((final['holder'], final['bash'], final['py']), (1, True, '1'))
        self.assertEqual(sorted(p.name for p in st.iterdir() if p.name.startswith('pair.json') or p.name == '.pair.lock'), ['pair.json'])

    def test_update_whose_mutation_raises_leaves_pair_json_alone_and_frees_the_lock(self):
        st = self.fixture_store()
        before = (st / 'pair.json').read_bytes()

        def boom(doc):
            doc['changed'] = True
            raise RuntimeError('boom')

        with self.assertRaises(RuntimeError):
            store.update(str(st), boom)
        self.assertEqual((st / 'pair.json').read_bytes(), before)
        self.assertEqual(sorted(p.name for p in st.iterdir() if 'pair' in p.name), ['pair.json'])

    def test_update_of_a_pair_json_that_does_not_parse_leaves_it_alone_and_frees_the_lock(self):
        st = self.fixture_store()
        (st / 'pair.json').write_text('{"a": ')
        with self.assertRaises(json.JSONDecodeError):
            store.update(str(st), lambda doc: None)
        self.assertEqual((st / 'pair.json').read_text(), '{"a": ')
        self.assertEqual(sorted(p.name for p in st.iterdir() if 'pair' in p.name), ['pair.json'])

    def test_update_gives_up_on_a_lock_that_stays_held_with_the_bash_message_and_leaves_that_lock(self):
        st = self.fixture_store()
        (st / '.pair.lock').mkdir()
        sleeps = []
        with mock.patch.dict(os.environ, {'PAIR_LOCK_TRIES': '3'}), mock.patch.object(store.time, 'sleep', sleeps.append):
            with self.assertRaises(Die) as cm:
                store.update(str(st), lambda doc: doc.update(x=1))
        self.assertEqual((cm.exception.message, cm.exception.code, len(sleeps)),
                         (f'pair.json lock held for 10s; remove {st}/.pair.lock if no pair.sh is running', 1, 2))
        self.assertTrue((st / '.pair.lock').is_dir())
        self.assertNotIn('"x"', (st / 'pair.json').read_text())

    INIT_PAIR = (root / 'tests/pstack-pair/golden/pair.txt').read_text().split('--- ./pair.json\n', 1)[1].split('\n--- ', 1)[0].replace('<EPOCH>', '1767225600') + '\n'
    JQ_FIXTURES = {
        'init': INIT_PAIR,
        'kitchen': '{"slug":"k","master":{"name":"m","sessions":[{"generation":1,"id":"a\\u00e9"}],"previous":null},'
                   '"sidekick":{"generation":2,"failovers":0,"fallback":{"kind":"pi","args":["--model","x"]}},"consultant":{"pane_id":null},'
                   '"verifying":{"unit":"001","at":"2026-01-01T00:00:00Z","sample":0.0},"pending":["a","b"],"scratch":[],"heads":{},"on":true}',
        'sample 0.0': '{"verifying":{"sample":0.0}}',
        'sample 0.25': '{"verifying":{"sample":0.25}}',
        'sample 1': '{"verifying":{"sample":1}}',
        'strings': '{"a":"\\u00e9 \\u2192 \\ud83d\\ude00","c":"\\u001f","d":"q\\"b\\\\s/","e":"\\u0000\\b\\f\\n\\r\\t","f":"\\u2028","g":"\\u00ad"}',
        'del': '{"b":"x\\u007fy"}',
        'empties': '{"a":[],"b":{},"c":{"d":[],"e":{}},"f":[[],{}]}',
        'integers': '{"a":0,"b":-5,"c":9007199254740993,"d":100000000000000000000,"e":1.0}',
        'floats': '{"a":0.1,"b":3.14159,"c":100.0}',
    }

    def jq_bytes(self, text):
        return subprocess.run(['jq', '.'], input=text, capture_output=True, text=True, check=True).stdout

    def test_update_writes_the_bytes_jq_writes(self):
        for name, text in self.JQ_FIXTURES.items():
            st = self.fixture_store(name.replace(' ', '-'))
            (st / 'pair.json').write_text(text)
            store.update(str(st), lambda doc: None)
            self.assertEqual((st / 'pair.json').read_text(), self.jq_bytes(text), name)

    def test_update_documents_where_its_number_spelling_differs_from_jq(self):
        # jq 1.8 keeps the spelling of a number it does not compute on, in the canonical form 1E+3; Python parses
        # it as a float and prints its own. No writer puts these in pair.json (counts and epochs are integers, and
        # sample comes from toml as 0.0, 0.25 or 1), and no reader treats the text as anything but JSON.
        for text, jq, py in (('1e3', '1E+3', '1000.0'), ('-0', '-0', '0'), ('1.5e-7', '1.5E-7', '1.5e-07'),
                             ('1.10', '1.10', '1.1'), ('1E2', '1E+2', '100.0'), ('0.000001', '0.000001', '1e-06')):
            doc = f'{{"n":{text}}}'
            st = self.fixture_store('num-' + text)
            (st / 'pair.json').write_text(doc)
            store.update(str(st), lambda d: None)
            self.assertEqual(
                (self.jq_bytes(doc), (st / 'pair.json').read_text()),
                (f'{{\n  "n": {jq}\n}}\n', f'{{\n  "n": {py}\n}}\n'), text)

    def test_scratch_mutations_match_their_jq_programs(self):
        docs = ['{"a":1}', '{"a":1,"scratch":null}', '{"a":1,"scratch":false}', '{"a":1,"scratch":[]}',
                '{"scratch":["x","001-a","x"],"a":1}', '{"scratch":["z","b","001-a","a"]}']
        programs = {
            'remove': ('.scratch = ((.scratch // []) - [$id])', lambda doc: doc.update(scratch=[x for x in doc.get('scratch') or [] if x != 'x'])),
            'add': ('.scratch = ((.scratch // []) + [$id] | unique)', lambda doc: doc.update(scratch=sorted({*(doc.get('scratch') or []), 'x'}))),
        }
        for name, (program, mutate) in programs.items():
            for n, text in enumerate(docs):
                st = self.fixture_store(f'{name}-{n}')
                (st / 'pair.json').write_text(text)
                store.update(str(st), mutate)
                want = subprocess.run(['jq', '--arg', 'id', 'x', program], input=text, capture_output=True, text=True, check=True).stdout
                self.assertEqual((st / 'pair.json').read_text(), want, (name, text))


    GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
               'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z'}

    def scratch_env(self, label, tree, jj=False):
        """A repo with a tracked change and an untracked file, and a store whose pair.json names it."""
        top = self.path / label
        repo, xdg = top / 'repo', top / 'state'
        (repo / 'sub').mkdir(parents=True)
        env = {**self.env, **self.GIT_ENV, 'XDG_STATE_HOME': str(xdg), 'HOME': str(top), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0',
               'FAKE': str(top / 'fake'), 'HERDR_SOCKET_PATH': str(top / 'herdr.sock')}
        for d in ('agents', 'panes', 'busy', 'screen'):
            (top / 'fake' / d).mkdir(parents=True)
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True).stdout.strip()
        git('init', '-q', '-b', 'main')
        (repo / 'a.txt').write_text('one\n')
        (repo / 'sub/b.txt').write_text('b\n')
        git('add', '-A')
        git('commit', '-q', '-m', 'init')
        (repo / 'a.txt').write_text('one\ntwo\n')
        (repo / 'sub/new.txt').write_text('untracked\n')
        os.chmod(repo / 'sub/new.txt', 0o755)
        if jj:
            subprocess.run(['jj', 'git', 'init', '--colocate', str(repo)], env=env, capture_output=True, check=True)
        script = tree / 'skills/pstack-pair/scripts/pair.sh'
        subprocess.run([str(script), 'init', 'demo'], cwd=repo, env=env, capture_output=True, check=True)
        store = xdg / 'pstack/pair/demo'
        (store / 'pair.json').write_text(json.dumps({'git_root': str(repo), 'scratch': []}, indent=2) + '\n')
        return top, repo, store, script, env, git

    def run_scratch(self, label, top, script, env, store, *args):
        r = subprocess.run([str(script), 'scratch', str(store), *args], env=env, capture_output=True, text=True, check=False)
        norm = lambda t: re.sub(r'\b[0-9a-f]{8,}\b|\b[k-z]{8,}\b', 'ID', t.replace(str(top), 'ROOT'))
        return r.returncode, norm(r.stdout), norm(r.stderr)

    def scratch_cases(self, jj):
        legacy = self.legacy_pair().parents[3]
        results = {}
        for label, tree in (('legacy', legacy), ('port', root)):
            top, repo, store, script, env, git = self.scratch_env(label, tree, jj)
            head = git('rev-parse', 'HEAD')
            out = []
            state = lambda: (store / 'pair.json').read_text().replace(str(top), 'ROOT')
            for name, args in (('create', ('001-x-review',)), ('again', ('001-x-review',)), ('at', ('002-y-c1', '--at', head)),
                               ('bad sha', ('003-z', '--at', 'nosuchrev')), ('bad id', ('bad',)), ('unknown option', ('004-w', '--bogus')),
                               ('usage', ()), ('remove', ('001-x-review', '--remove')), ('remove again', ('001-x-review', '--remove')),
                               ('remove at', ('002-y-c1', '--remove'))):
                r = self.run_scratch(label, top, script, env, store, *args)
                scratch_root = repo / '.git/pstack-scratch'
                tree_files = sorted(str(f.relative_to(top)) for f in scratch_root.rglob('*') if '.git' not in f.relative_to(scratch_root).parts[-1:]
                                    and f.name != '.git') if scratch_root.exists() else []
                links = sorted((l.name, os.readlink(l).replace(str(top), 'ROOT')) for l in (store / 'scratch').glob('*') if l.is_symlink()) if (store / 'scratch').exists() else []
                out.append((name, r, state(), tree_files, links))
                if name == 'create' and not jj:
                    wt = Path(r[1].strip().replace('ROOT', str(top)))
                    out.append(('content', (wt / 'a.txt').read_text(), (wt / 'sub/new.txt').read_text(), oct((wt / 'sub/new.txt').stat().st_mode & 0o777),
                                (wt / 'sub/new.txt').stat().st_mtime_ns == (repo / 'sub/new.txt').stat().st_mtime_ns))
            results[label] = out
        self.assertEqual(results['legacy'], results['port'])
        return results['port']

    def test_scratch_matches_pair_sh_at_the_legacy_commit_in_a_git_repo(self):
        out = dict((o[0], o) for o in self.scratch_cases(jj=False))
        self.assertEqual(out['create'][1][0], 0)
        self.assertEqual(out['content'][1:], ('one\ntwo\n', 'untracked\n', '0o755', True))
        self.assertEqual(out['again'][1][1], out['create'][1][1])
        self.assertEqual(out['bad sha'][1][:3], (1, '', 'pair.sh: not a commit: nosuchrev\n'))
        self.assertEqual(out['bad id'][1][0], 1)
        self.assertIn('"001-x-review"', out['create'][2])
        self.assertNotIn('001-x-review', out['remove'][2])
        self.assertEqual(out['remove'][1][1].split(' ')[0], 'removed')

    def test_scratch_with_a_git_root_that_is_not_a_repo_or_a_path_that_is_a_file_matches_bash(self):
        legacy = self.legacy_pair().parents[3]
        results = {}
        for label, tree in (('legacy', legacy), ('port', root)):
            top, repo, store, script, env, git = self.scratch_env(label, tree)
            out = []
            norm = lambda t: t.replace(str(top), 'ROOT')
            state = lambda: norm((store / 'pair.json').read_text())
            common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir'))
            blocker = common / 'pstack-scratch/pair-demo/001-x-file'
            blocker.parent.mkdir(parents=True)
            blocker.write_text('in the way')
            out.append(('worktree add fails', self.run_scratch(label, top, script, env, store, '001-x-file', '--at', git('rev-parse', 'HEAD')), state()))
            out.append(('again', self.run_scratch(label, top, script, env, store, '001-x-file'), state()))
            (store / 'pair.json').write_text(json.dumps({'git_root': str(top / 'norepo'), 'scratch': ['001-gone']}, indent=2) + '\n')
            (top / 'norepo').mkdir()
            for args in (('001-gone', '--remove'), ('002-b',)):
                out.append(('not a repo', self.run_scratch(label, top, script, env, store, *args), state()))
            (store / 'pair.json').write_text(json.dumps({'scratch': []}) + '\n')
            out.append(('no git_root', self.run_scratch(label, top, script, env, store, '003-c'), state()))
            results[label] = out
        self.assertEqual(results['legacy'], results['port'])
        by_name = {o[0]: o for o in results['port']}
        self.assertEqual(by_name['worktree add fails'][1][0], 2)
        self.assertIn('pair.sh: git worktree add failed 2', by_name['worktree add fails'][1][2])
        self.assertEqual(by_name['no git_root'][1][:3], (1, '', "pair.sh: the store's cwd is not inside a git repository\n"))


    @unittest.skipUnless(shutil.which('jj'), 'needs jj on PATH')
    def test_scratch_matches_pair_sh_at_the_legacy_commit_in_a_jj_repo(self):
        out = dict((o[0], o) for o in self.scratch_cases(jj=True))
        self.assertEqual(out['create'][1][0], 0)


    def generated_events(self, seed):
        """One events.tsv behind a legacy header line, and a PAIR_METRICS_NOW, from a fixed seed.

        The d06a932 awk program drops the first line as a header and event() never wrote one, so the oracle reads
        a header it was written for. The first-event correction is exempt from the oracle: see the headerless tests."""
        rng = random.Random(seed)
        units = ['001-alpha', '002-beta', '002-beta-s1', '003-gamma-q2', '003-gamma', '004-delta-s2', '-']
        words = ['accepted', 'Accept', 'agreed', 'revise', 'rejected', 'REJECT', 'looks fine', '', 'Agreed with notes', 'revised']
        t = 1_700_000_000
        rows = []
        for _ in range(1 if seed % 25 == 0 else rng.randint(2, 45)):
            t += rng.choice([0, 0, 1, 7, 30, 61, 90, 600, 3600])
            u = rng.choice(units)
            kind = rng.randrange(18)
            actor, ev, d = 'master', 'log', ''
            if kind == 0:
                ev, d = rng.choice(['send-brief', 'send-answer']), rng.choice(['', 'queued', 'fresh'])
            elif kind == 1:
                ev, d = 'send-plan', rng.choice(['', 'consultant', 'sidekick'])
            elif kind == 2:
                ev = 'send-consult'
            elif kind == 3:
                ev, d = 'send-steer', rng.choice(['objection', 'redirect', ''])
            elif kind == 4:
                ev = 'queue'
            elif kind == 5:
                ev, d = 'failover', rng.choice(['pi:unready', 'devin:rate limit'])
            elif kind == 6:
                actor, ev, d = 'sidekick', 'step', rng.choice([f'{rng.randint(1, 5)}:abc123', 'x:y', '', '01:zzz'])
            elif kind == 7:
                actor, ev, d = 'master', 'note', rng.choice([f'{rng.randint(1, 6)}:blocking', f'{rng.randint(1, 6)}:clear', '', ':clear'])
            elif kind == 8:
                actor, ev = 'sidekick', 'report'
            elif kind == 9:
                actor, ev = 'consultant', 'advice'
            elif kind in (10, 11):
                ev, d = 'wake', rng.choice(['checkin', 'report:done', 'sidekick:idle', 'advice:agree', 'consultant:idle', 'other', ''])
            elif kind in (12, 13, 14):
                ev, d = 'log', rng.choice(['review', 'plan', 'other']) + ': ' + rng.choice(words) + rng.choice(['', ' 002', ' because'])
            else:
                actor, ev = rng.choice([('sidekick', 'report'), ('master', 'wake'), ('consultant', 'advice')])
            epoch = str(t)
            if rng.randrange(40) == 0:
                epoch = rng.choice(['abc', '12.5', '', f'{t}.5'])
            rows.append(f'2026-01-01T00:00:00Z\t{epoch}\t{actor}\t{ev}\t{u}\t{d}')
            if rng.randrange(60) == 0:
                rows.append(rng.choice(['', 'short\trow', f'x\t{t}']))
        now = rng.choice([str(t - 100), str(t), str(t + 50), str(t + 5000), 'later'])
        return '\n'.join([LEGACY_HEADER, *rows]) + rng.choice(['\n', '']), now

    def metrics_oracle(self):
        """The generated stores and the awk program's output for each, from pair.sh as it was at d06a932, in one bash process."""
        legacy = self.legacy_pair().parents[3] / 'skills/pstack-pair/scripts/pair-core.sh'
        stores = {}
        for seed in range(200):
            text, now = self.generated_events(seed)
            st = self.path / f'm{seed}'
            st.mkdir(exist_ok=True)
            (st / 'events.tsv').write_text(text)
            stores[seed] = (st, now)
        loop = '\n'.join(f'printf "\\037%d\\n" {seed}; PAIR_METRICS_NOW={shlex.quote(now)} cmd_metrics {st}' for seed, (st, now) in stores.items())
        harness = (f'set -uo pipefail\nskill_root={root / "skills/pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\n'
                   f'roles=(sidekick); store_dirs=(plans); slug_max=21\n. {legacy}\nset +e\n{loop}\n')
        oracle = subprocess.run(['bash', '-c', harness], env=self.env, capture_output=True, text=True, check=False)
        parts = oracle.stdout.split('\x1f')[1:]
        self.assertEqual(len(parts), 200, oracle.stderr[-500:])
        outputs = {}
        for seed in stores:
            label, want = parts[seed].split('\n', 1)
            self.assertEqual(label, str(seed))
            outputs[seed] = want
        return stores, outputs

    METRICS_LINES = {'wall', 'sidekick', 'queue', 'consultant', 'master', 'failovers', 'steps', 'verdicts', 'briefs'}

    def covered(self, outputs):
        seen = set()
        for out in outputs:
            seen.update(line.split(':')[0] for line in out.splitlines() if not line.startswith(' '))
            seen.update(w for w in (' (stopped)', ' (paused)', 'running:') if w in out)
        return seen

    def test_generated_logs_reach_every_metrics_line_and_the_oracle_is_repeatable(self):
        stores, outputs = self.metrics_oracle()
        self.assertLessEqual(self.METRICS_LINES | {' (stopped)', ' (paused)', 'running:'}, self.covered(outputs.values()))
        self.assertEqual(self.metrics_oracle()[1], outputs)
        self.assertGreater(len(set(outputs.values())), 100)

    def test_metrics_matches_the_awk_program_on_generated_logs(self):
        stores, outputs = self.metrics_oracle()
        ctx = Context('pstack-pair', str(root / 'skills/pstack-pair'), '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        for seed, (st, now) in stores.items():
            out = io.StringIO()
            with mock.patch.dict(os.environ, {'PAIR_METRICS_NOW': now}), contextlib.redirect_stdout(out):
                metrics.run(ctx, [str(st)])
            self.assertEqual(out.getvalue(), outputs[seed], (seed, now))


    @unittest.skipIf(os.geteuid() == 0, 'file modes do not stop root')
    def test_metrics_matches_pair_sh_at_the_legacy_commit_where_it_aborts_or_reads_nothing(self):
        legacy = self.legacy_pair().parents[3]
        cases = {}
        for label, tree in (('legacy', legacy), ('port', root)):
            runs = {}
            for name in ('missing', 'empty', 'header only', 'no trailing newline', 'directory', 'unreadable'):
                st = self.path / f'abort-{label}-{name.replace(" ", "-")}'
                st.mkdir()
                log = st / 'events.tsv'
                if name == 'empty':
                    log.write_text('')
                elif name == 'header only':
                    log.write_text('ts\tepoch\n')
                elif name == 'no trailing newline':
                    log.write_text(f'{LEGACY_HEADER}\nx\t1\tm\tsend-brief\t001-a\t\nx\t5\tm\tsend-brief\t001-a\t')
                elif name == 'directory':
                    log.mkdir()
                    (log / 'f').write_text('x')
                elif name == 'unreadable':
                    log.write_text('x\t1\n')
                    log.chmod(0)
                r = self.run_pair('metrics', str(st), script=tree / 'skills/pstack-pair/scripts/pair.sh', extra_env={'PAIR_METRICS_NOW': '100'})
                runs[name] = (r.returncode, r.stdout, r.stderr.replace(str(st), 'ST'))
                if name == 'unreadable':
                    log.chmod(0o644)
            runs['usage'] = (lambda r: (r.returncode, r.stdout, r.stderr))(
                self.run_pair('metrics', script=tree / 'skills/pstack-pair/scripts/pair.sh'))
            cases[label] = runs
        self.assertEqual(cases['legacy'], cases['port'])
        self.assertEqual(cases['port']['unreadable'][0], 2)
        self.assertEqual(cases['port']['directory'][0], 0)
        self.assertIn('is a directory: skipped', cases['port']['directory'][2])
        self.assertEqual(cases['port']['missing'][0], 1)


    FIRST_EVENTS = ('2026-01-01T00:00:00Z\t100\tmaster\tsend-brief\t001-a\t\n'
                    '2026-01-01T00:01:00Z\t160\tsidekick\treport\t001-a\t\n')

    def metrics_of(self, text):
        st = self.path / f'first-{len(list(self.path.iterdir()))}'
        st.mkdir()
        (st / 'events.tsv').write_text(text)
        self.env['PAIR_METRICS_NOW'] = '200'
        r = self.pair_py('metrics', str(st))
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        return r.stdout

    def test_metrics_counts_the_first_event_of_a_headerless_store(self):
        out = self.metrics_of(self.FIRST_EVENTS)
        self.assertIn('sidekick: busy 1m (100% of wall), idle 0m across 0 gaps; 1 briefs (median 1m), 0 plan responses (0m)\n', out)
        self.assertIn('  001-a 1m\n', out)

    def test_metrics_gives_a_legacy_header_store_the_same_output_as_the_headerless_one(self):
        self.assertEqual(self.metrics_of(LEGACY_HEADER + '\n' + self.FIRST_EVENTS), self.metrics_of(self.FIRST_EVENTS))

    PORTED = ('status', 'metrics', 'log', 'notes', 'note', 'new_note', 'progress', 'new_plan', 'new_brief', 'new_steer', 'pause', 'resume', 'scratch',
              'finish', 'notify', 'report', 'next', 'wait')
    DUPLICATED = ('event', 'header_field', 'next_seq', 'paused_reason', 'pause_notice', 'steer_notice', 'open_steers', 'progress_path',
                  'new_from_template', 'steps_file', 'noted_through', 'open_notes', 'trunk_ref', 'range_floor', 'review_base', 'require_filled',
                  'agent_status', 'pane_busy', 'pane_text', 'agent_kind', 'latest_report', 'latest_advice', 'queued_brief', 'json_update',
                  'report_ready', 'expected_report', 'fresh_per_brief', 'record_dispatch', 'file_mtime', 'queue_note', 'pending_report', 'fresh_reply', 'seen_reply',
                  'mark_seen', 'unreviewed_steps', 'withheld_report', 'review_delta', 'steps_due', 'status_paths', 'tree_activity', 'scope_entries',
                  'steer_scope_entries', 'sidekick_activity', 'busy_command', 'checkin', 'agent_session_error', 'muse_session_error', 'provider_hint', 'prime_role_error',
                  'record_prime_session', 'wait_opts', 'reply_mode', 'finish_wait', 'wait_for_reply')

    def test_ported_commands_are_pycore_shims_and_each_duplicated_bash_helper_still_has_a_bash_caller(self):
        text = {p: p.read_text() for p in (root / 'skills').glob('*/scripts/*.sh')}
        core = text[scripts / 'pair-core.sh']
        for name in self.PORTED:
            self.assertRegex(core, rf'(?m)^cmd_{name}\(\) \{{ pycore (--exec )?\S+ "\$@"; \}}$', name)
        defined = core + text[scripts / 'prime-agent.sh']
        for helper in self.DUPLICATED:
            self.assertRegex(defined, rf'(?m)^{helper}\(\) \{{$', helper)
            callers = sum(len(re.findall(rf'(?<![\w-]){helper}(?![\w-])(?!\(\) \{{)', body)) for body in text.values())
            self.assertGreater(callers, 0, f'{helper} has no bash caller left: delete the bash twin')


    def test_pair_sh_stops_with_a_message_when_python3_is_not_on_path(self):
        bin_dir = self.path / 'bin-without-python3'
        bin_dir.mkdir()
        for tool in ('bash', 'jq', 'env', 'dirname', 'basename', 'readlink', 'cat', 'mkdir', 'date', 'rm', 'ls', 'head', 'tail', 'cut', 'sed', 'grep', 'awk', 'tr', 'sort', 'mv', 'cp', 'stat'):
            found = shutil.which(tool)
            if found:
                (bin_dir / tool).symlink_to(found)
        (bin_dir / 'herdr').symlink_to(root / 'tests/pstack-pair/bin/herdr')
        env = {**inherited_env(), 'PATH': str(bin_dir), 'HOME': str(self.path), 'HERDR_ENV': '1', 'FAKE': str(self.path / 'fake'),
               'XDG_STATE_HOME': str(self.path / 'state')}
        self.assertIsNone(shutil.which('python3', path=str(bin_dir)))
        r = subprocess.run([str(bin_dir / 'bash'), str(scripts / 'pair.sh'), 'help'], env=env, capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, '', 'pair.sh: python3 is required on PATH\n'))


    def test_pause_resume_and_progress_through_pair_sh(self):
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = str(self.path / 'state/pstack/pair/demo')
        r = self.run_pair('progress', st, 'x')
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: no brief dispatched yet; progress belongs to a running brief\n'))
        pair = json.loads(Path(st, 'pair.json').read_text())
        pair['dispatch'] = {'brief': f'{st}/briefs/001-one.md'}
        Path(st, 'pair.json').write_text(json.dumps(pair))
        r = self.run_pair('progress', st, 'step one done')
        self.assertEqual((r.returncode, r.stdout), (0, f'{st}/progress/001-one.md\n'))
        self.assertRegex(Path(st, 'progress/001-one.md').read_text(), r'^\d\d:\d\d step one done\n$')
        r = self.run_pair('pause', st, '--reason', 'lunch')
        self.assertEqual((r.returncode, Path(st, 'paused').read_text()), (0, 'lunch\n'))
        self.assertTrue(r.stdout.startswith(f'paused {st}: '))
        self.assertTrue(self.run_pair('progress', st, 'again').stdout.splitlines()[1].startswith('PAUSE: lunch. This is your safe point'))
        r = self.run_pair('resume', st)
        self.assertEqual((r.returncode, r.stdout, Path(st, 'paused').exists()), (0, f'resumed {st}\n', False))
        self.assertNotIn('PAUSE:', self.run_pair('progress', st, 'more').stdout)
        r = self.run_pair('pause', st, '--bogus')
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: unknown option --bogus\n'))
        self.assertEqual(self.run_pair('pause', st).returncode, 0)
        self.assertEqual(Path(st, 'paused').read_text(), 'paused by the human\n')
        events = [row.split('\t')[2:] for row in Path(st, 'events.tsv').read_text().splitlines()]
        self.assertIn(['master', 'pause', '-', 'lunch'], events)
        self.assertIn(['master', 'resume', '-', ''], events)
        self.assertIn(['sidekick', 'progress', '001-one', ''], events)


    def test_log_records_a_row_and_an_event_through_each_pair_entry_script(self):
        env = {**self.env, 'FAKE': str(self.path), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0',
               'HERDR_SOCKET_PATH': str(self.path / 'herdr.sock'), 'XDG_STATE_HOME': str(self.path / 'state'), 'HOME': str(self.path)}
        for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio'):
            script = str(root / 'skills' / skill / 'scripts/pair.sh')
            run = lambda *a: subprocess.run([script, *a], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(run('init', 'demo').returncode, 0, skill)
            store = self.path / 'state/pstack' / skill.removeprefix('pstack-') / 'demo'
            r = run('log', str(store), 'p', 'd', 'w', 'e', 'r')
            self.assertEqual((r.returncode, r.stderr), (0, ''), skill)
            self.assertEqual((store / 'decisions.tsv').read_text().splitlines()[-1].split('\t')[1:], ['p', 'd', 'w', 'e', 'r'], skill)
            self.assertEqual((store / 'events.tsv').read_text().splitlines()[-1].split('\t')[2:], ['master', 'log', '-', 'p: d'], skill)
            r = run('log', str(store), 'p')
            self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: usage: pair.sh log <store> <phase> <decision> <why> <evidence> <result>\n'), skill)


if __name__ == '__main__':
    unittest.main()
