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
from pstack_core import checkin, cli, dispatch, herdr, permission, providers, replies, review, send, store, wait  # noqa: E402
from pstack_core.commands import metrics, step  # noqa: E402
from pstack_core.context import Context  # noqa: E402
from pstack_core.errors import Die, Exit  # noqa: E402
from pstack_core.variant import Variant, load_skill_variant  # noqa: E402

sys.path.insert(0, str(root / 'tests'))
import bash_reach  # noqa: E402

LEGACY = 'd06a932'
BASE = 'e6e83e3'
PHASE3 = '04d2c13'
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
                              env=self.env, capture_output=True, text=True, check=False, timeout=120)

    # Bash helpers whose bodies left the working tree once their last caller moved to Python. A snippet that names one runs from the tree at
    # e6e83e3, where they are still defined: the oracle the Python twins are compared with.
    DELETED_BASH = ('check_scope', 'checkin', 'finish_wait', 'fresh_reply', 'mark_seen', 'noted_through', 'pending_report', 'progress_path',
                    'prompt_sidekick', 'prune_pending', 'queue_note', 'record_dispatch', 'record_sent', 'reply_mode', 'review_base', 'review_delta',
                    'scope_entry_exists', 'seen_reply', 'send_and_wait', 'sidekick_activity', 'status_paths', 'steer_scope_entries', 'tree_activity',
                    'unreviewed_steps', 'wait_for_reply', 'withheld_report', 'wait_opts', 'prime_steer', 'steps_due', 'report_ready',
                    'provider_hint', 'bootstrap_recover', 'asks_no_approvals', 'plan_gate_extra', 'require_agreed_plan', 'open_notes')

    def bash(self, snippet, env=None, check=True, base=None):
        """snippet run with pair-core.sh sourced: the working tree's, or with base=True skills/ as it was at e6e83e3, the oracle for
        the commands that moved to Python since (and the default for a snippet that names a helper whose bash body is gone)."""
        if base is None:
            base = re.search(r'\b(' + '|'.join(self.DELETED_BASH) + r')\b', snippet) is not None
        skills = self.base_skills() if base else root / 'skills'
        if base:
            # the oracle tree must hold the bodies the snippet calls that the working tree no longer has
            core = (skills / 'pstack-pair/scripts/pair-core.sh').read_text() + (skills / 'pstack-pair/scripts/prime-agent.sh').read_text()
            for name in set(re.findall(r'\b(' + '|'.join(self.DELETED_BASH) + r')\b', snippet)):
                self.assertRegex(core, rf'(?m)^{name}\(\) \{{', f'{name} is not defined in the oracle tree')
        harness = f'''set -euo pipefail
skill_root={skills / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair
roles=(sidekick); store_dirs=(plans); slug_max=21
. {skills / "pstack-pair/scripts/pair-core.sh"}
{snippet}'''
        r = subprocess.run(['bash', '-c', harness], env={**self.env, **(env or {})}, capture_output=True, text=True, check=check, timeout=120)
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
            bash = self.bash(f'printf "[%s]\\n" "$(paused_reason {st} || echo NONE)"; pause_notice {st}', base=True)
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
        self.assertEqual(self.bash(f'steer_notice {st}', base=True), store.steer_notice(str(st)))
        (st / 'progress/001-one.md').write_text('10:00 steer s1 applied: x\n10:01 steer s12 late: reported\n')
        (st / 'reports/001-one-s2.md').write_text('status: object\n')
        bash = self.bash(f'steer_notice {st}', base=True)
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
            self.assertEqual(store.steer_notice(str(st)), self.bash(f'steer_notice {st}', base=True), name)

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
        self.git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True, timeout=120).stdout.strip()
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
        both, tail_gone = [(1, c1, ''), (2, c2, '')], [(1, c1, ''), (2, missing, '')]
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
        """skills/ as it was at e6e83e3, the oracle for wait, finish, notify, report, next, queue, step, dispatch, discuss and the plan gates."""
        cls = type(self)
        if cls.__dict__.get('_base') is None:
            tmp = tempfile.TemporaryDirectory()
            self.addClassCleanup(tmp.cleanup)
            git = subprocess.run(['git', '-C', str(root), 'archive', BASE, 'skills'], capture_output=True, check=False, timeout=120)
            if git.returncode != 0:
                raise unittest.SkipTest(f'{BASE} is not in this checkout')
            subprocess.run(['tar', '-x', '-C', tmp.name], input=git.stdout, check=True, timeout=120)
            cls._base = Path(tmp.name) / 'skills'
        return cls._base

    def phase3_skills(self):
        """skills/ as it was at PHASE3, the oracle for init, permission, stop, steer, answer, spawn, rotate and failover."""
        cls = type(self)
        if cls.__dict__.get('_phase3') is None:
            tmp = tempfile.TemporaryDirectory()
            self.addClassCleanup(tmp.cleanup)
            git = subprocess.run(['git', '-C', str(root), 'archive', PHASE3, 'skills'], capture_output=True, check=False, timeout=120)
            if git.returncode != 0:
                raise unittest.SkipTest(f'{PHASE3} is not in this checkout')
            subprocess.run(['tar', '-x', '-C', tmp.name], input=git.stdout, check=True, timeout=120)
            cls._phase3 = Path(tmp.name) / 'skills'
        return cls._phase3

    def init_run(self, label, skills, skill, spec):
        """`init` in a world with no store, then the spec's `then` step in the same world: the outputs of each run, and everything the runs left
        under the world's directory (the repo aside), with the world's directory and the skills directory masked."""
        script = skills / skill / 'scripts' / ('kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh')
        slug_max = int(re.search(r'(?m)^slug_max=(\d+)', script.read_text()).group(1))
        top, st, env, fill = self.ab_world(label, {'nopair': True, 'master': None, 'noenv': spec.get('noenv'), 'repo_files': spec.get('repo_files', {})})
        shutil.rmtree(st)
        cwd = top / ('plain' if spec.get('plain') else 'repo')
        cwd.mkdir(exist_ok=True)
        env['PWD'] = str(cwd)
        norm = lambda text: re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>',
                                   re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text.replace(str(skills), '<SKILLS>').replace(str(top), '<T>')))))
        outs = []
        for step in (spec, *([spec['then']] if 'then' in spec else [])):
            if step is not spec and step.get('prep'):
                step['prep'](top)
            elif step is spec and step.get('prep'):
                step['prep'](top)
            args = step['args'](slug_max) if callable(step['args']) else step['args']
            run_env = {**env, **step.get('env', {})}
            for key in step.get('unset', ()):
                run_env.pop(key, None)
            r = subprocess.run([str(script), *(a.replace('{T}', str(top)) for a in args)], cwd=cwd, env=run_env, capture_output=True, text=True, check=False, timeout=120)
            outs.append((r.returncode, norm(r.stdout), norm(r.stderr)))
        files = {}
        for f in sorted(top.rglob('*')):
            rel = f.relative_to(top)
            if rel.parts[0] in ('repo', 'plain') or f.name in ('.pair.lock',):
                continue
            if f.is_symlink():
                files[str(rel)] = ('link', norm(os.readlink(f)))
            elif f.is_dir():
                files[str(rel)] = ('dir', oct(f.stat().st_mode & 0o777))
            else:
                files[str(rel)] = (oct(f.stat().st_mode & 0o777), norm(f.read_text(errors='replace')))
        return outs, files

    def init_cases(self):
        cases = {
            'a fresh store at the default path': dict(args=['init', 'demo']),
            'a fresh store with --store': dict(args=['init', 'demo', '--store', '{T}/new']),
            'a slug at slug_max': dict(args=lambda m: ['init', 'a' + 'b' * m]),
            'a slug past slug_max': dict(args=lambda m: ['init', 'a' + 'b' * (m + 1)]),
            'a slug with an uppercase letter': dict(args=['init', 'Demo']),
            'a slug that starts with a digit': dict(args=['init', '1demo']),
            'a slug with a dot': dict(args=['init', 'de.mo']),
            'an empty slug': dict(args=['init', '']),
            'a slug with a newline': dict(args=['init', 'demo\nx']),
            'no slug': dict(args=['init']),
            'outside herdr': dict(args=['init', 'demo'], noenv=True),
            'HERDR_PANE_ID unset': dict(args=['init', 'demo'], unset=['HERDR_PANE_ID']),
            'HERDR_PANE_ID empty': dict(args=['init', 'demo'], env={'HERDR_PANE_ID': ''}),
            '--pane': dict(args=['init', 'demo', '--pane', 'p7'], unset=['HERDR_PANE_ID']),
            '--pane with no value': dict(args=['init', 'demo', '--pane']),
            '--store with no value': dict(args=['init', 'demo', '--store']),
            'an unknown option': dict(args=['init', 'demo', '--force']),
            'an unknown option after a good one': dict(args=['init', 'demo', '--pane', 'p7', '--force']),
            'the option before the slug': dict(args=['init', '--pane', 'p7', 'demo']),
            'a stale HERDR_PANE_ID': dict(args=['init', 'demo'], env={'HERDR_PANE_ID': 'gone1'}),
            'a stale --pane': dict(args=['init', 'demo', '--pane', 'gone2']),
            'workspace and tab ids': dict(args=['init', 'demo'], env={'HERDR_WORKSPACE_ID': 'w1', 'HERDR_TAB_ID': 'w1:t2'}),
            'a working directory outside a git repo': dict(args=['init', 'demo'], plain=True),
            'a re-init in another pane': dict(args=['init', 'demo'], then=dict(args=['init', 'demo'], env={'HERDR_PANE_ID': 'p3', 'HERDR_WORKSPACE_ID': 'w9', 'HERDR_TAB_ID': 'w9:t1'})),
            'a re-init that keeps edited standing orders, gates and decisions': dict(
                args=['init', 'demo'], then=dict(args=['init', 'demo'], prep=lambda top: [
                    (p := next(top.glob('state/pstack/**/demo'))).joinpath('standing-orders.md').write_text('mine\n'),
                    p.joinpath('gates.md').write_text('g\n'), p.joinpath('decisions.tsv').write_text('d\n')])),
            'a re-init after the store lost its standing orders': dict(
                args=['init', 'demo'], then=dict(args=['init', 'demo'], prep=lambda top: next(top.glob('state/pstack/**/demo')).joinpath('standing-orders.md').unlink())),
            'a re-init of a pair.json whose master is null': dict(
                args=['init', 'demo'], then=dict(args=['init', 'demo'], prep=lambda top: (lambda f: f.write_text(re.sub(r'"master": \{.*?\n  \}', '"master": null', f.read_text(), flags=re.S)))(
                    next(top.glob('state/pstack/**/demo/pair.json'))))),
            'a re-init of a pair.json that does not parse': dict(
                args=['init', 'demo'], then=dict(args=['init', 'demo'], prep=lambda top: next(top.glob('state/pstack/**/demo/pair.json')).write_text('{not json'))),
            'a re-init of a pair.json whose master is a string': dict(
                args=['init', 'demo'], then=dict(args=['init', 'demo'], prep=lambda top: next(top.glob('state/pstack/**/demo/pair.json')).write_text('{"master": "x"}'))),
        }
        return cases

    def init_failure_cases(self):
        def read_only(path):
            path.chmod(0o555)
            self.addCleanup(path.chmod, 0o755)
        link_dead = lambda name: (lambda top: (top / 'new').mkdir() or (top / 'new' / name).symlink_to(top / 'nope/x'))
        return {
            'a store that cannot be created': dict(args=['init', 'demo', '--store', '{T}/ro/s'], prep=lambda top: (top / 'ro').mkdir() or read_only(top / 'ro')),
            'a pair.json that is a directory': dict(args=['init', 'demo', '--store', '{T}/new'], prep=lambda top: (top / 'new/pair.json').mkdir(parents=True)),
            'a store that cannot be written': dict(args=['init', 'demo', '--store', '{T}/new'], prep=lambda top: [
                (top / 'new' / d).mkdir(parents=True) for d in ('plans', 'briefs', 'reports', 'reviews', 'progress', 'steers', 'advice', 'consults')] and read_only(top / 'new')),
            'standing orders that cannot be copied': dict(args=['init', 'demo', '--store', '{T}/new'], prep=link_dead('standing-orders.md')),
            'a gates.md that cannot be written': dict(args=['init', 'demo', '--store', '{T}/new'], prep=link_dead('gates.md')),
            'a decisions.tsv that cannot be written': dict(args=['init', 'demo', '--store', '{T}/new'], prep=link_dead('decisions.tsv')),
        }

    def test_init_matches_the_bash_at_the_phase_3_base_through_every_entry_script(self):
        skills3 = self.phase3_skills()
        kitchen_cases = ('a fresh store at the default path', 'a fresh store with --store', 'a slug past slug_max', 'a slug at slug_max', 'no slug', 'outside herdr',
                         'HERDR_PANE_ID unset', '--pane', '--pane with no value', '--store with no value', 'an unknown option', 'a stale HERDR_PANE_ID', 'a stale --pane',
                         'a re-init in another pane', 'a re-init that keeps edited standing orders, gates and decisions', 'workspace and tab ids')
        cases = {**self.init_cases(), **self.init_failure_cases()}
        mismatches, seen = {}, {}
        n = 0
        for name, spec in cases.items():
            for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen'):
                if skill == 'pstack-kitchen' and name not in kitchen_cases:
                    continue
                if skill != 'pstack-pair' and name in self.init_failure_cases() and name != 'a pair.json that is a directory':
                    continue
                spec = dict(spec, repo_files={'.agents/kitchen.toml': self.KITCHEN_TOML} if skill == 'pstack-kitchen' else {})
                n += 1
                want = self.init_run(f'w{n}', skills3, skill, spec)
                got = self.init_run(f'g{n}', root / 'skills', skill, spec)
                seen[(name, skill)] = want
                if skill == 'pstack-kitchen' and name in ('--pane with no value', '--store with no value'):
                    # Permitted difference: kitchen.sh builds core_init by eval from cmd_init, so the bash's own error for the missing value
                    # names kitchen.sh as the source; the port's names pair-core.sh, whose shim it is, as it does for the other entry scripts.
                    self.assertEqual(want[0][0][2], '<SKILLS>/pstack-kitchen/scripts/kitchen.sh: line N: $2: unbound variable\n')
                    want = ([(want[0][0][0], want[0][0][1], '<core>: line N: $2: unbound variable\n')], want[1])
                if got != want:
                    mismatches[f'{name} through {skill}'] = {'got': got, 'want': want}
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        self.assertEqual(seen[('a fresh store at the default path', 'pstack-pair')][0][0][0], 0)
        self.assertEqual(seen[('a stale --pane', 'pstack-pair')][0][0][0], 2)
        self.assertEqual(seen[('a slug past slug_max', 'pstack-pair')][0][0][0], 1)

    SKILLS_WITH_A_VARIANT = ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen')
    # The whole commands each skill's variant runs in place of the core's, by name. Each table is the variant's own: a skill that extends another
    # lists what it chooses to keep.
    EFFECTIVE_COMMANDS = {'pstack-pair': (), 'pstack-pair-guided': ('answer',), 'pstack-trio': ('spawn', 'stop'), 'pstack-kitchen': ('stop',)}

    def test_each_skills_effective_command_table_is_pinned(self):
        tables = {skill: tuple(sorted(load_skill_variant(root / 'skills', skill).commands)) for skill in self.SKILLS_WITH_A_VARIANT}
        self.assertEqual(tables, self.EFFECTIVE_COMMANDS)
        self.assertTrue(set(name for table in tables.values() for name in table) <= set(cli.OVERRIDABLE))

    def test_kitchen_runs_the_trios_stop_from_a_table_of_its_own_and_has_no_spawn(self):
        trio = load_skill_variant(root / 'skills', 'pstack-trio')
        kitchen = load_skill_variant(root / 'skills', 'pstack-kitchen')
        self.assertIsNot(kitchen.commands, trio.commands)
        for table in (trio.commands, kitchen.commands):
            self.assertEqual((table['stop'].__name__, table['stop'].__code__.co_filename), ('stop', str(root / 'skills/pstack-trio/scripts/variant.py')))
        self.assertNotIn('spawn', kitchen.commands)
        # the trio's spawn is its own command, in its variant; the kitchen's (the captured pair command under its bash wrapper) is the core's
        self.assertEqual((trio.commands['spawn'].__name__, trio.commands['spawn'].__code__.co_filename), ('spawn', str(root / 'skills/pstack-trio/scripts/variant.py')))

    def test_every_variant_class_defines_its_own_commands(self):
        for cls in (Variant, *(load_skill_variant(root / 'skills', skill) for skill in self.SKILLS_WITH_A_VARIANT)):
            self.assertIn('commands', cls.__dict__, cls.__name__)
            self.assertIsInstance(cls.__dict__['commands'], dict, cls.__name__)

    def test_only_an_overridable_name_asks_the_variant_and_an_internal_entry_never_does(self):
        class Exploding:
            @property
            def commands(self):
                raise AssertionError('the variant was asked')

        ctx = Context('pstack-pair', '/nowhere/pstack-pair', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        ctx._variant = Exploding()
        for name in cli.COMMANDS:
            if name not in cli.OVERRIDABLE:
                self.assertIs(cli.resolve(ctx, name), cli.COMMANDS[name], name)
        self.assertIsNone(cli.resolve(ctx, '__nothing'))
        ctx._variant = type('V', (), {'commands': {'answer': 'mine', 'status': 'not asked', '__detect_permission_mode': 'not asked'}})()
        self.assertEqual((cli.resolve(ctx, 'answer'), cli.resolve(ctx, 'status'), cli.resolve(ctx, '__detect_permission_mode')),
                         ('mine', cli.COMMANDS['status'], cli.COMMANDS['__detect_permission_mode']))

    def test_a_command_the_variant_does_not_override_runs_without_loading_the_variant_module(self):
        store = self.path / 'st'
        store.mkdir()
        (store / 'pair.json').write_text('{}')
        r = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from pstack_core import cli, variant; '
                            'variant.load_variant = lambda ctx: (_ for _ in ()).throw(AssertionError("loaded")); sys.exit(cli.main(sys.argv[2:]))',
                            str(scripts), *core_args(), 'resume', str(store)], capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, f'resumed {store}\n', ''))

    STOP_HOOK = ('#!/bin/sh\nst="$FAKE/../st"\ncase "$2" in *" STOP "*) %s ;; esac\nexit 0\n')

    def stop_hook(self, name='001-stop.md', heading='Stop', status='partial', directory='reports'):
        return self.STOP_HOOK % f'printf "# {heading}\\n\\nstatus: {status}\\n" >"$st/{directory}/{name}"'

    def stop_cases(self):
        """Cases for `stop` of the sidekick alone: the pair's and the guided pair's."""
        fast = {'PAIR_STOP_POLL_S': '0.1'}
        working = lambda kind='claude': {'s1': f'working {kind}'}
        devin = dict(kind='devin', agents=working('devin'), env=fast)
        run = ['stop', '{st}']
        return {
            'no arguments': dict(args=['stop']),
            'an unknown option': dict(args=run + ['--force'], agents={'s1': 'idle claude'}),
            'a timeout that is not a number': dict(args=run + ['--timeout', 'abc'], agents={'s1': 'idle claude'}),
            '--timeout with no value': dict(args=run + ['--timeout'], agents={'s1': 'idle claude'}),
            '--every with no value': dict(args=run + ['--every'], agents={'s1': 'idle claude'}),
            '--every 2': dict(args=run + ['--every', '2'], agents={'s1': 'idle claude'}),
            '--send-only is taken and ignored': dict(args=run + ['--send-only'], agents={'s1': 'idle claude'}),
            'outside herdr': dict(args=run, env={'HERDR_ENV': '0'}, agents={'s1': 'idle claude'}),
            'no pair.json': dict(args=run, nopair=True),
            'the sidekick is absent': dict(args=run),
            'the sidekick is blocked': dict(args=run, agents={'s1': 'blocked claude'}),
            'an idle sidekick': dict(args=run, agents={'s1': 'idle claude'}),
            'an idle sidekick that drops the prompt': dict(args=run, agents={'s1': 'idle claude'}, promptfail=True),
            'an idle sidekick with a queue and pending briefs': dict(args=run, agents={'s1': 'idle claude'}, files={'queue': '{st}/briefs/002-y.md\n'},
                                                                      pair={'pending': ['{st}/briefs/001-x.md']}),
            'a done sidekick': dict(args=run, agents={'s1': 'done claude'}),
            'a working sidekick that writes NNN-stop.md': dict(args=run, agents=working(), hook=self.stop_hook(), env=fast),
            'a working sidekick that writes a stop report under another name': dict(args=run, agents=working(), env=fast,
                                                                                    hook=self.stop_hook('001-land-partial1.md', 'partial (STOP)')),
            'a working sidekick whose status says stopped': dict(args=run, agents=working(), env=fast,
                                                                 hook=self.stop_hook('001-x-part.md', 'Partial', 'stopped-partial')),
            'a working sidekick whose report has bus-stop in its slug': dict(args=run + ['--timeout', '1000'], agents=working(), env=fast,
                                                                              hook=self.stop_hook('001-bus-stop.md', 'Report', 'partial')),
            'a working sidekick that never writes it': dict(args=run + ['--timeout', '1000'], agents=working(), env=fast),
            'a working sidekick and a prompt that fails': dict(args=run, agents=working(), promptfail=True, env=fast),
            'a working sidekick with an older stop report on file': dict(args=run + ['--timeout', '1000'], agents=working(), env=fast,
                                                                         files={'reports/001-stop.md': '# Stop\n\nstatus: partial\n'}),
            'a working sidekick and a stop report that gets rewritten': dict(args=run, agents=working(), env=fast, hook=self.stop_hook(),
                                                                              files={'reports/001-stop.md': '# Stop\n\nstatus: partial\n'}, mtimes={'reports/001-stop.md': 1_000_000_000}),
            'a working devin whose input box holds the text': dict(devin, args=run, fake={'devin-input': ''}, hook=self.stop_hook()),
            'a working devin with an older queued message': dict(devin, args=run, fake={'devin-input': '', 'queued/s1': 'an older steer'}, hook=self.stop_hook()),
            'a working devin whose report lands at once': dict(devin, args=run, hook=self.stop_hook()),
            'a working devin that never shows the text': dict(devin, args=run + ['--timeout', '2000']),
            'a working devin and a stop under another name': dict(devin, args=run, fake={'devin-input': ''}, hook=self.stop_hook('001-land-partial1.md', 'partial (STOP)')),
            'a working devin whose text stays typed': dict(devin, args=run, hook=self.stop_hook(), fake={'devin-input': '', 'typing/s1': 'pstack-pair STOP x'}),
        }

    def stop_ab_run(self, skill, group, cases=None):
        """The stop cases of a group through one entry script, against the bash at the phase 3 base: the outcomes of the base."""
        skills3 = self.phase3_skills()
        mismatches, results = {}, {}
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*(?:pair|consultant)-core\.sh|\S*/pair\.sh', '<core>', text))
        for name, spec in (cases or self.stop_cases()).items():
            if not group(name):
                continue
            n = len(list(self.path.iterdir()))
            want = self.ab_run(f'wb{n}', skills3, skill, spec)
            got = self.ab_run(f'wp{n}', root / 'skills', skill, spec)
            for side in (got, want):
                side['stderr'] = norm_core(side['stderr'])
                side['stdout'] = self.mask_minutes(side['stdout'])
                side['calls'] = self.collapse_polls(side['calls'])
            results[name] = want
            if got != want:
                mismatches[name] = {k: (got[k], want[k]) for k in got if got[k] != want[k]}
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        return results

    def test_stop_matches_the_bash_at_the_phase_3_base_when_the_sidekick_is_not_working(self):
        results = self.stop_ab_run('pstack-pair', lambda n: 'working' not in n)
        rc = lambda name: results[name]['rc']
        self.assertEqual((rc('the sidekick is absent'), rc('the sidekick is blocked'), rc('no arguments'), rc('no pair.json')), (2, 3, 1, 2))
        self.assertEqual(rc('an idle sidekick'), 4)
        self.assertIn('state: prompt_failed', results['an idle sidekick that drops the prompt']['stdout'])

    def test_stop_matches_the_bash_at_the_phase_3_base_when_the_sidekick_is_working(self):
        results = self.stop_ab_run('pstack-pair', lambda n: 'working sidekick' in n)
        self.assertEqual(results['a working sidekick that writes NNN-stop.md']['rc'], 0)
        self.assertIn('report: <T>/st/reports/001-land-partial1.md', results['a working sidekick that writes a stop report under another name']['stdout'])
        self.assertEqual(results['a working sidekick that never writes it']['rc'], 4)
        self.assertIn('report: missing', results['a working sidekick that never writes it']['stdout'])

    def test_stop_matches_the_bash_at_the_phase_3_base_when_the_sidekick_is_a_working_devin(self):
        results = self.stop_ab_run('pstack-pair', lambda n: 'working devin' in n and 'never' not in n and 'stays typed' not in n)
        self.assertEqual(results['a working devin whose input box holds the text']['rc'], 0)

    def test_stop_matches_the_bash_at_the_phase_3_base_when_a_working_devin_is_never_sent_the_text(self):
        # 'stays typed' has no --timeout on purpose: the watch then ends on the queued text's send-now hint (the second Enter) and never on its
        # deadline. With a short deadline it ended either on the hint (read, Enter, read, Enter) or on the clock (read, Enter, read), by where
        # the whole second fell, in the bash and in the port alike. 'never shows' ends on its deadline by design; its repeated reads collapse.
        # The window is two seconds, not one: the outcome depends on whether the pane watch gets to run before the deadline, and a slow fake
        # herdr call between the send and the watch must not tip a one-second window.
        results = self.stop_ab_run('pstack-pair', lambda n: 'never' in n and 'devin' in n or 'stays typed' in n)
        self.assertIn('STOP never showed in the pane; sent one Enter as a fallback', results['a working devin that never shows the text']['stderr'])

    def test_stop_ab_signals_end_the_command_as_they_end_the_bash(self):
        # While the stop sits in herdr's prompt, a signal ends it with the signal and nothing printed, group-wide or to the shell alone, as it
        # ends the bash (INT to the shell alone after herdr's call returns). The core stop and the consultant-core stop are one process.
        skills3 = self.phase3_skills()
        roles = {'consultant': {'name': 'c1', 'kind': 'codex'}, 'verifier': {'name': 'v1', 'kind': 'claude'}}
        worlds = {'pstack-pair': {'agents': {'s1': 'idle claude'}},
                  'pstack-trio': {'agents': {'s1': 'idle claude', 'c1': 'idle codex'}, 'pair': roles},
                  'pstack-kitchen': {'agents': {'s1': 'idle claude', 'c1': 'idle codex', 'v1': 'idle claude'}, 'pair': roles}}
        for skill, world in worlds.items():
            for number, group in ((signal.SIGTERM, True), (signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGINT, True), (signal.SIGINT, False)):
                run = dict(hold='prompt', argv=lambda st: ['stop', str(st), '--timeout', '20000'], world=world)
                want = self.wait_signalled(skills3, number, group, skill, **run)
                got = self.wait_signalled(root / 'skills', number, group, skill, **run)
                self.assertEqual(got, want, (skill, number.name, group))
                self.assertEqual(got[:3], (-number, '', ''), (skill, number.name, group))

    def test_stop_matches_the_bash_at_the_phase_3_base_in_guided(self):
        self.stop_ab_run('pstack-pair-guided', lambda n: n in ('an idle sidekick', 'the sidekick is blocked', 'the sidekick is absent', 'a working sidekick that writes NNN-stop.md',
                                                               'a working devin whose input box holds the text'))

    def multi_stop_hook(self, writes):
        """A fake-herdr hook: on STOP, each agent named in writes (sidekick s1, consultant c1, verifier v1) writes its stop file."""
        files = {'s1': 'reports/001-stop.md', 'c1': 'advice/001-stop.md', 'v1': 'reports/002-stop.md'}
        arms = ''.join(f'{n}) printf "# Stop\\n\\nstatus: partial\\n" >"$st/{files[n]}" ;; ' for n in writes)
        return f'#!/bin/sh\nst="$FAKE/../st"\ncase "$2" in *" STOP "*) case "$1" in {arms}esac ;; esac\nexit 0\n'

    def stop_cc_cases(self, kitchen=False):
        """Cases for the consultant-core stop: the trio's, and with kitchen=True the kitchen's, whose roles also include the verifier."""
        fast = {'PAIR_STOP_POLL_S': '0.1'}
        roles = {'consultant': {'name': 'c1', 'kind': 'codex'}, **({'verifier': {'name': 'v1', 'kind': 'claude'}} if kitchen else {})}
        idle = {'s1': 'idle claude', 'c1': 'idle codex', **({'v1': 'idle claude'} if kitchen else {})}
        run = ['stop', '{st}']
        base = dict(args=run, pair=roles, agents=idle)
        cases = {
            'no arguments': dict(base, args=['stop']),
            'an unknown option': dict(base, args=run + ['--force']),
            '--only with no value': dict(base, args=run + ['--only']),
            '--timeout with no value': dict(base, args=run + ['--timeout']),
            '--every with no value': dict(base, args=run + ['--every']),
            '--every that is not a number': dict(base, args=run + ['--every', 'a b']),
            '--timeout that is not a number': dict(base, args=run + ['--timeout', 'abc']),
            '--every 2': dict(base, args=run + ['--every', '2']),
            'outside herdr': dict(base, env={'HERDR_ENV': '0'}),
            'no pair.json': dict(base, nopair=True),
            'every role idle': base,
            'every role absent': dict(base, agents={}),
            'the consultant is absent': dict(base, agents={k: v for k, v in idle.items() if k != 'c1'}),
            'the sidekick is blocked': dict(base, agents={**idle, 's1': 'blocked claude'}),
            'the consultant is blocked': dict(base, agents={**idle, 'c1': 'blocked codex'}),
            '--only consultant': dict(base, args=run + ['--only', 'consultant']),
            '--only sidekick': dict(base, args=run + ['--only', 'sidekick']),
            '--only a role nobody has': dict(base, args=run + ['--only', 'nobody']),
            'a queue and pending briefs': dict(base, files={'queue': '{st}/briefs/002-y.md\n'}, pair={**roles, 'pending': ['{st}/briefs/001-x.md']}),
            'a report and advice on file': dict(base, files={'reports/001-x.md': '# R\n\nstatus: done\n', 'advice/001-x.md': '# A\n\nstatus: answered\n'}),
            'prompts that fail': dict(base, promptfail=True),
            'a working consultant that writes its stop advice': dict(base, agents={**idle, 'c1': 'working codex'}, hook=self.multi_stop_hook(['c1']), env=fast),
            'a working consultant that never does': dict(base, args=run + ['--timeout', '1000'], agents={**idle, 'c1': 'working codex'}, env=fast),
            'a working sidekick and a working consultant': dict(base, agents={**idle, 's1': 'working claude', 'c1': 'working codex'},
                                                                hook=self.multi_stop_hook(['s1', 'c1']), env=fast),
            'a working consultant and a prompt that fails': dict(base, agents={**idle, 'c1': 'working codex'}, promptfail=True, env=fast),
            'a working devin sidekick': dict(base, agents={**idle, 's1': 'working devin'}, pair={**roles, 'sidekick': {'kind': 'devin', 'generation': 1, 'name': 's1'}},
                                             fake={'devin-input': ''}, hook=self.multi_stop_hook(['s1']), env=fast),
        }
        if kitchen:
            cases.update({
                '--only verifier': dict(base, args=run + ['--only', 'verifier']),
                'the verifier is absent': dict(base, agents={k: v for k, v in idle.items() if k != 'v1'}),
                'the verifier is unassigned': dict(base, pair={'consultant': roles['consultant']}),
                'a working verifier that writes its stop report': dict(base, agents={**idle, 'v1': 'working claude'}, hook=self.multi_stop_hook(['v1']), env=fast),
                'a blocked verifier': dict(base, agents={**idle, 'v1': 'blocked claude'}),
            })
        return cases

    def test_stop_matches_the_bash_at_the_phase_3_base_in_trio(self):
        results = self.stop_ab_run('pstack-trio', lambda n: True, self.stop_cc_cases())
        self.assertEqual(results['every role idle']['rc'], 0)
        self.assertIn('consultant c1 is not live', results['the consultant is absent']['stdout'])
        self.assertEqual((results['the sidekick is blocked']['rc'], results['the consultant is blocked']['rc']), (3, 3))
        self.assertIn('consultant_state:', results['a working consultant that writes its stop advice']['stdout'])
        self.assertIn('advice: <T>/st/advice/001-stop.md (partial)', results['a working consultant that writes its stop advice']['stdout'])

    def test_stop_matches_the_bash_at_the_phase_3_base_in_the_kitchen(self):
        results = self.stop_ab_run('pstack-kitchen', lambda n: True, {n: dict(spec, repo_files={'.agents/kitchen.toml': self.KITCHEN_TOML})
                                                                       for n, spec in self.stop_cc_cases(kitchen=True).items()})
        self.assertIn('verifier_state: ', results['every role idle']['stdout'])
        self.assertIn('verifier v1 is not live', results['the verifier is absent']['stdout'])
        self.assertIn('verifier null is not live', results['the verifier is unassigned']['stdout'])
        self.assertEqual(results['a blocked verifier']['rc'], 3)

    PRIME_WRAPPER = (
        '#!/usr/bin/env python3\nimport os, signal, sys\nfake = os.environ["FAKE"]\nargs = sys.argv[1:]\n'
        'def read(name):\n    try:\n        return open(os.path.join(fake, name)).read()\n    except OSError:\n        return None\n'
        'hang = read("wrap-hang")\nif hang is not None and args and args[0] in hang.split():\n    signal.pause()\n'
        'receipt = read("wrap-receipt")\nif receipt is not None and args and args[0] == "send":\n    with open(os.path.join(fake, "prime.log"), "a") as log:\n'
        '        import json; log.write(json.dumps(args) + "\\n")\n    sys.stdout.write(receipt); sys.exit(int(read("wrap-receipt-exit") or 0))\n'
        'reply = read("wrap-list")\nif reply is not None and args and args[0] == "list":\n    with open(os.path.join(fake, "prime.log"), "a") as log:\n'
        '        import json; log.write(json.dumps(args) + "\\n")\n    sys.stdout.write(reply); sys.exit(0)\n'
        'os.execv(sys.argv[0] + ".real", [sys.argv[0] + ".real", *args])\n')

    def prime_world(self, label, spec):
        """A Prime sidekick in a fake-herdr world: its pair.json entry, the daemon's session list, and a prime-agent in front of the fixture that a
        case can make hang, answer with a given receipt or session list."""
        sessions = [{'activeSessionId': 'live-1', 'sessionId': 'sess-1', 'sessionFile': '/t/one.jsonl', 'name': 's1'}]
        sidekick = {'kind': 'prime-agent', 'generation': 1, 'name': 's1', 'pane_id': 'p1', 'launch_cwd': '{T}/launch', **spec.get('sidekick', {})}
        world = {'agents': spec.get('agents', {'s1': 'idle prime-agent'}), 'panes': spec.get('panes', {'p1': 's1'}), 'once': spec.get('once', {}),
                 'files': spec.get('files', {}), 'clock': spec.get('clock'), 'pair': {'sidekick': sidekick, **spec.get('pair', {})},
                 'fake': {'prime-sessions.json': json.dumps(spec.get('sessions', sessions)),
                          **({} if spec.get('herdr_session') is False else {'sessions/s1': spec.get('herdr_session', '/t/one.jsonl')}),
                          **spec.get('fake', {})}, 'env': {'PAIR_PRIME_COMMAND_TIMEOUT': '1', **spec.get('env', {})}}
        top, st, env, fill = self.ab_world(label, world)
        sidekick_json = json.loads((st / 'pair.json').read_text())
        sidekick_json['sidekick'] = json.loads(json.dumps(sidekick_json['sidekick']).replace('{T}', str(top)))
        (st / 'pair.json').write_text(json.dumps(sidekick_json, indent=2) + '\n')
        wrap = top / 'wrap'
        wrap.mkdir()
        real = shutil.which('prime-agent', path=env['PATH'])
        (wrap / 'prime-agent.real').symlink_to(real)
        (wrap / 'prime-agent').write_text(self.PRIME_WRAPPER)
        (wrap / 'prime-agent').chmod(0o755)
        env['PATH'] = f'{wrap}:{env["PATH"]}'
        return top, st, env

    def prime_both(self, label, bash_snippet, python_snippet, shim=False, **spec):
        """A Prime helper of the 04d2c13 bash (sourced under set -euo pipefail) against its Python twin run in a fresh interpreter, on identical
        worlds: (exit code, stdout, stderr, pair.json, the prime-agent and herdr calls)."""
        results = []
        for side in ('bash', 'py'):
            top, st, env = self.prime_world(f'{label}-{side}', spec)
            skills3 = self.phase3_skills() if side == 'bash' or not shim else root / 'skills'
            if side == 'bash' or shim:
                script = (f'set -euo pipefail\nskill_root={skills3 / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\n'
                          f'roles=(sidekick); store_dirs=(plans); slug_max=21\n. {skills3 / "pstack-pair/scripts/pair-core.sh"}\nstore={shlex.quote(str(st))}\nset +e\n{bash_snippet}')
                argv = ['bash', '-c', script]
            else:
                code = f'import sys\nsys.path.insert(0, {str(scripts)!r})\nfrom pstack_core import prime\nstore = {str(st)!r}\n{python_snippet}'
                argv = [sys.executable, '-I', '-c', code]
            r = subprocess.run(argv, cwd=top / 'repo', env=env, capture_output=True, text=True, check=False, timeout=120)
            norm = lambda text: re.sub(r'line [0-9]+', 'line N', text.replace(str(top), '<T>').replace(str(self.phase3_skills()), '<SK>').replace(str(root / 'skills'), '<SK>'))
            read = lambda f: norm((top / f).read_text()) if (top / f).exists() else ''
            results.append((r.returncode, norm(r.stdout), norm(r.stderr), read('st/pair.json'), read('fake/prime.log'), read('fake/calls.log')))
        self.assertEqual(results[0], results[1], label)
        return results[1]

    def test_prime_active_session_and_the_socket_match_the_bash_at_the_phase_3_base(self):
        sess = lambda *rows: {'sessions': list(rows)}
        row = lambda active, sid, path, name='s1': {'activeSessionId': active, 'sessionId': sid, 'sessionFile': path, 'name': name}
        path_session = {'kind': 'path', 'value': '/t/one.jsonl'}
        id_session = {'kind': 'id', 'value': 'sess-1'}
        bash = 'prime_active_session "$store" sidekick; rc=$?; printf "rc=%s id=[%s]\\n" "$rc" "${prime_active_id-unset}"'
        py = 'id = prime.prime_active_session(store, "sidekick")\nprint(f"rc={int(id is None)} id=[{\'unset\' if id is None else id}]")'
        cases = {
            'no recorded session': dict(),
            'a path session that is live': dict(sidekick={'session': path_session}),
            'an id session matched by sessionId': dict(sidekick={'session': id_session}),
            'an id session matched by the active id': dict(sidekick={'session': {'kind': 'id', 'value': 'live-1'}}),
            'no live session matches': dict(sidekick={'session': {'kind': 'path', 'value': '/t/other.jsonl'}}),
            'a name alone does not match': dict(sidekick={'session': {'kind': 'id', 'value': 's1'}}),
            'two live ids match': dict(sidekick={'session': id_session}, sessions=[row('live-1', 'sess-1', '/a'), row('live-2', 'sess-1', '/b')]),
            'one active id listed twice': dict(sidekick={'session': id_session}, sessions=[row('live-1', 'sess-1', '/a'), row('live-1', 'sess-1', '/b')]),
            'sessions with no active id are skipped': dict(sidekick={'session': id_session}, sessions=[row('', 'sess-1', '/a'), {'sessionId': 'sess-1'}, 7, row('live-3', 'sess-1', '/c')]),
            'a daemon that fails': dict(sidekick={'session': path_session}, fake={'prime-list-fail': ''}),
            'a reply that is not JSON': dict(sidekick={'session': path_session}, fake={'wrap-list': 'not json\n'}),
            'a reply with no sessions array': dict(sidekick={'session': path_session}, fake={'wrap-list': '{"sessions": 3}\n'}),
            'an empty reply': dict(sidekick={'session': path_session}, fake={'wrap-list': ''}),
            'a daemon that hangs': dict(sidekick={'session': path_session}, fake={'wrap-hang': 'list'}),
            'a relative socket against the launch directory': dict(sidekick={'session': path_session, 'start_args': ['--model', 'x', '--daemon-socket', 'run/d.sock']}),
            'a relative socket against the run directory': dict(sidekick={'session': path_session, 'launch_cwd': None, 'start_args': ['--daemon-socket=run/d.sock']}),
            'an absolute socket': dict(sidekick={'session': path_session, 'start_args': ['--daemon-socket', '/run/d.sock', '--x']}),
            'a socket flag with no value': dict(sidekick={'session': path_session, 'start_args': ['--daemon-socket']}),
            'start args that are not strings': dict(sidekick={'session': path_session, 'start_args': [1, None, {'a': 1}, '--daemon-socket=/s']}),
        }
        results = {name: self.prime_both('active-' + re.sub(r'\W+', '-', name), bash, py, **spec) for name, spec in cases.items()}
        self.assertEqual(results['a path session that is live'][1], 'rc=0 id=[live-1]\n')
        self.assertEqual(results['no live session matches'][1], 'rc=0 id=[]\n')
        self.assertIn('multiple live Prime sessions match', results['two live ids match'][2])
        self.assertIn('no recorded Prime session for sidekick', results['no recorded session'][2])
        self.assertIn('"--socket", "<T>/launch/run/d.sock"', results['a relative socket against the launch directory'][4])
        self.assertIn('"--socket", "<T>/repo/run/d.sock"', results['a relative socket against the run directory'][4])

    def test_prime_steer_matches_the_bash_at_the_phase_3_base(self):
        bash = 'if prime_steer "$store" "pstack-pair STEER /s/001-x-s1.md"; then echo ok; else echo fail; fi'
        py = 'print("ok" if prime.prime_steer(store, "pstack-pair STEER /s/001-x-s1.md") else "fail")'
        path_session = {'kind': 'path', 'value': '/t/one.jsonl'}
        receipt = lambda **kw: json.dumps({'deliveryMode': 'steer', 'deliveryStatus': 'queued', 'target': {'activeSessionId': 'live-1'}, **kw}) + '\n'
        cases = {
            'a queued steer': dict(),
            'a delivered steer': dict(fake={'wrap-receipt': receipt(deliveryStatus='delivered')}),
            'a receipt that says failed': dict(fake={'wrap-receipt': receipt(deliveryStatus='failed')}),
            'a receipt for follow-up': dict(fake={'wrap-receipt': receipt(deliveryMode='follow-up')}),
            'a receipt for another session': dict(fake={'wrap-receipt': receipt(target={'activeSessionId': 'live-9'})}),
            'a receipt with no target': dict(fake={'wrap-receipt': '{"deliveryMode": "steer", "deliveryStatus": "queued"}\n'}),
            'a receipt that is not JSON': dict(fake={'wrap-receipt': 'sent\n'}),
            'a send that exits non-zero': dict(fake={'wrap-receipt': receipt(), 'wrap-receipt-exit': '1'}),
            'a send that hangs': dict(fake={'wrap-hang': 'send'}),
            'a daemon that cannot list': dict(fake={'prime-list-fail': ''}),
            'herdr reports no session': dict(herdr_session=False),
            'no live session matches': dict(sessions=[]),
            'two live sessions match': dict(sessions=[{'activeSessionId': 'live-1', 'sessionFile': '/t/one.jsonl'}, {'activeSessionId': 'live-2', 'sessionFile': '/t/one.jsonl'}]),
            'a socket of its own': dict(sidekick={'start_args': ['--daemon-socket=run/d.sock']}),
        }
        results = {name: self.prime_both('steer-' + re.sub(r'\W+', '-', name), bash, py, **dict(spec, sidekick={'session': path_session, **spec.get('sidekick', {})}))
                   for name, spec in cases.items()}
        self.assertEqual(results['a queued steer'][1], 'ok\n')
        self.assertEqual([results[n][1] for n in ('a receipt that says failed', 'a receipt for follow-up', 'a receipt for another session', 'a send that hangs')], ['fail\n'] * 4)

    STEER_TEXT = '# Steer 001-x-s1\n\nkind: narrow\nscope effect: none\n\n## Direction\n\nuse the other helper\n'

    def steer_cases(self):
        """Cases for `steer`, through every entry script. A case's files are all old (see send_ab_run): the objection report must not count as the
        reply to a steer sent in the same second."""
        steer, objection = {'steers/001-x-s1.md': self.STEER_TEXT}, {'reports/001-x-s1.md': '# R\n\nstatus: object\n'}
        run = ['steer', '{st}', '{st}/steers/001-x-s1.md']
        working = {'s1': 'working claude'}
        settle = {'wait': '#!/bin/sh\nprintf "idle claude\\n" >"$FAKE/agents/s1"\n'}
        prime_session = {'kind': 'path', 'value': '/t/one.jsonl'}
        prime = dict(agents={'s1': 'working prime-agent'}, panes={'p1': 's1'}, files=steer, fake={'prime-sessions.json': json.dumps(
            [{'activeSessionId': 'live-1', 'sessionId': 'sess-1', 'sessionFile': '/t/one.jsonl', 'name': 's1'}]), 'sessions/s1': '/t/one.jsonl'},
            pair={'sidekick': {'kind': 'prime-agent', 'generation': 1, 'name': 's1', 'pane_id': 'p1', 'session': prime_session}})
        idle = dict(args=run, agents={'s1': 'idle claude'}, files={**steer, **objection}, pair={'sent': {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 5}})
        reply = '1 {st}/reports/001-x.md'
        return {
            'no arguments': dict(args=['steer']),
            'one argument': dict(args=['steer', '{st}']),
            'a steer path that cannot be resolved': dict(args=['steer', '{st}', '']),
            'a missing steer file': dict(args=['steer', '{st}', '{st}/steers/009-none.md'], agents=working),
            'a file that is not a steer': dict(args=run, agents=working, files={'steers/001-x-s1.md': '# Notes\n'}),
            'a steer with a placeholder': dict(args=run, agents=working, files={'steers/001-x-s1.md': self.STEER_TEXT.replace('other helper', '{{{{what}}}}')}),
            'a steer whose kind line lists alternatives': dict(args=run, agents=working, files={'steers/001-x-s1.md': self.STEER_TEXT.replace('narrow', 'narrow | widen')}),
            'a steer whose scope effect lists alternatives': dict(args=run, agents=working, files={'steers/001-x-s1.md': self.STEER_TEXT.replace('none', 'none | extra')}),
            'an unknown option': dict(args=run + ['--bogus'], agents=working, files=steer),
            '--timeout with no value': dict(args=run + ['--timeout'], agents=working, files=steer),
            'outside herdr': dict(args=run, agents=working, files=steer, env={'HERDR_ENV': '0'}),
            'no pair.json': dict(args=run, nopair=True, files=steer),
            'the sidekick is absent': dict(args=run, files=steer),
            'the sidekick is blocked': dict(args=run, agents={'s1': 'blocked claude'}, files=steer),
            'a working sidekick': dict(args=run, agents=working, files=steer),
            'a working sidekick and a prompt that fails': dict(args=run, agents=working, files=steer, promptfail=True),
            'a working sidekick with --interrupt that settles': dict(args=run + ['--interrupt'], agents=working, files=steer, once=settle),
            'a working sidekick with --interrupt that does not settle': dict(args=run + ['--interrupt'], agents=working, files=steer),
            'a working devin': dict(args=run, agents={'s1': 'working devin'}, kind='devin', files=steer),
            'a working devin with a steer already left and another': dict(args=run, agents={'s1': 'working devin'}, kind='devin', files=steer,
                                                                           pair={'notice_steers': ['{st}/steers/001-x-s1.md', '/a/other-s2.md']}),
            'a working devin whose notice list holds a number': dict(args=run, agents={'s1': 'working devin'}, kind='devin', files=steer, pair={'notice_steers': [1]}),
            'a working devin whose notice list holds an object': dict(args=run, agents={'s1': 'working devin'}, kind='devin', files=steer, pair={'notice_steers': [{'a': 1}]}),
            'a working devin whose notice list is a string': dict(args=run, agents={'s1': 'working devin'}, kind='devin', files=steer, pair={'notice_steers': 'x'}),
            'a working devin and a steer path that is not UTF-8': dict(args=['steer', '{st}', '{st}/steers/001-x-\udcff-s1.md'], agents={'s1': 'working devin'}, kind='devin',
                                                                        files={'steers/001-x-\udcff-s1.md': self.STEER_TEXT}),
            'a steer file that cannot be read': dict(args=['steer', '{st}', '{T}/steer.md'], agents=working, topfiles={'steer.md': (self.STEER_TEXT, 0)}),
            'a steer reached through a symlink': dict(args=['steer', '{st}', '{T}/link.md'], agents=working, files=steer, links={'link.md': '{st}/steers/001-x-s1.md'}),
            'a working devin with a null notice list': dict(args=run, agents={'s1': 'working devin'}, kind='devin', files=steer, pair={'notice_steers': None}),
            'a working devin with --interrupt': dict(args=run + ['--interrupt'], agents={'s1': 'working devin'}, kind='devin', files=steer, once=settle),
            'a working prime sidekick': dict(prime, args=run),
            'a working prime sidekick whose daemon fails': dict(prime, args=run, fake={**prime['fake'], 'prime-list-fail': ''}),
            'a working prime sidekick with --interrupt': dict(prime, args=run + ['--interrupt'], once=settle),
            'an idle sidekick with an open objection': dict(idle, report_after=reply),
            'an idle sidekick whose reply is a report with another status': dict(idle, report_after=reply + ' partial'),
            'a done sidekick with an open objection': dict(idle, agents={'s1': 'done claude'}, report_after=reply),
            'an idle sidekick that never replies': dict(idle, args=run + ['--timeout', '1000']),
            '--every on the wait': dict(idle, args=run + ['--every', '1'], report_after=reply),
            'an idle sidekick and a prompt that fails': dict(idle, promptfail=True),
            'an idle sidekick with no objection': dict(args=run, agents={'s1': 'idle claude'}, files=steer),
            'an idle sidekick whose objection was answered': dict(idle, files={**steer, 'reports/001-x-s1.md': '# R\n\nstatus: agree\n'}),
            'an idle sidekick and a steer that supersedes none': dict(idle, files={**steer, 'steers/001-x-s1.md': self.STEER_TEXT + '\nsupersedes: none\n'}),
            'an idle sidekick and a steer that supersedes an objection': dict(idle, report_after=reply, files={**steer, 'steers/001-x-s1.md': self.STEER_TEXT + '\nsupersedes: s1\n'}),
            'a second objection newer than the first': dict(idle, report_after=reply, files={**steer, **objection, 'reports/001-x-s2.md': '# R\n\nstatus: agree\n'},
                                                             mtimes={'reports/001-x-s2.md': 1_000_000_100}),
            'a sent entry already marked seen': dict(idle, report_after=reply, pair={'sent': {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 5, 'seen': 'x:1'}}),
            'no sent entry': dict(idle, report_after=reply, pair={'sent': None}),
            'an unknown sidekick': dict(args=run, agents={'s1': 'unknown claude'}, files=steer),
            'an unknown sidekick with --force': dict(args=run + ['--force'], agents={'s1': 'unknown claude'}, files=steer),
            'an unknown sidekick with --force and a prompt that fails': dict(args=run + ['--force'], agents={'s1': 'unknown claude'}, files=steer, promptfail=True),
        }

    def steer_specs(self):
        return {n: {**spec, 'mtimes': {**{rel: self.OLD_MTIME for rel in spec.get('files', {})}, **spec.get('mtimes', {})}} for n, spec in self.steer_cases().items()}

    IDLE_STEER_CASES = ('idle sidekick', 'done sidekick', '--every on the wait', 'second objection', 'sent entry', 'no sent entry')

    def test_steer_matches_the_bash_at_the_phase_3_base_in_pair_when_the_sidekick_is_not_idle(self):
        results = self.stop_ab_run('pstack-pair', lambda n: not any(w in n for w in self.IDLE_STEER_CASES), self.steer_specs())
        self.assertEqual(results['a working sidekick']['rc'], 0)
        self.assertIn('steered s1 with <T>/st/steers/001-x-s1.md; its harness hands it over', results['a working sidekick']['stdout'])
        self.assertEqual((results['the sidekick is absent']['rc'], results['the sidekick is blocked']['rc'], results['an unknown sidekick']['rc']), (2, 3, 5))
        self.assertEqual(results['a working sidekick with --interrupt that does not settle']['rc'], 3)
        self.assertIn('left for s1', results['a working devin']['stdout'])
        self.assertIn('Prime queues it', results['a working prime sidekick']['stdout'])

    def test_steer_matches_the_bash_at_the_phase_3_base_in_pair_when_the_sidekick_is_idle(self):
        results = self.stop_ab_run('pstack-pair', lambda n: any(w in n for w in self.IDLE_STEER_CASES), self.steer_specs())
        self.assertIn('report_status: partial', results['an idle sidekick whose reply is a report with another status']['stdout'])
        self.assertEqual(results['an idle sidekick with no objection']['rc'], 5)
        self.assertEqual(results['an idle sidekick that never replies']['rc'], 4)
        self.assertIn('send-steer\t', ' '.join(f'{e}\t' for e in results['an idle sidekick with an open objection']['events']))
        self.assertIn('"epoch"', results['no sent entry']['files']['pair.json'])

    ANSWER_TEXT = '# Answer\n\n## Answer\n\nuse the other helper\n'

    def answer_cases(self):
        answer = {'answers/001-x-a1.md': self.ANSWER_TEXT}
        run = ['answer', '{st}', '{st}/answers/001-x-a1.md']
        idle = {'s1': 'idle claude'}
        reply = '1 {st}/reports/001-x.md'
        cases = {
            'no arguments': dict(args=['answer']),
            'one argument': dict(args=['answer', '{st}']),
            'an unknown option': dict(args=run + ['--force'], agents=idle, files=answer),
            '--timeout with no value': dict(args=run + ['--timeout'], agents=idle, files=answer),
            '--every with no value': dict(args=run + ['--every'], agents=idle, files=answer),
            '--every that is not a number': dict(args=run + ['--every', 'a b'], agents=idle, files=answer),
            'a missing answer file': dict(args=['answer', '{st}', '{st}/answers/009-none.md'], agents=idle),
            'a file without an answer heading': dict(args=run, agents=idle, files={'answers/001-x-a1.md': '# Notes\n'}),
            'an answer with a placeholder': dict(args=run, agents=idle, files={'answers/001-x-a1.md': self.ANSWER_TEXT.replace('other helper', '{{{{x}}}}')}),
            'a paused store': dict(args=run, agents=idle, files={**answer, 'paused': 'lunch\n'}),
            'outside herdr': dict(args=run, agents=idle, files=answer, env={'HERDR_ENV': '0'}),
            'no pair.json': dict(args=run, nopair=True, files=answer),
            'the sidekick is absent': dict(args=run, files=answer),
            'the sidekick is blocked': dict(args=run, agents={'s1': 'blocked claude'}, files=answer),
            'the sidekick is working': dict(args=run, agents={'s1': 'working claude'}, files=answer),
            'a devin that needs a rotation': dict(args=run, agents={'s1': 'idle devin'}, files=answer,
                                                  pair={'sidekick': {'kind': 'devin', 'generation': 1, 'name': 's1', 'rotation_required': True}}),
            'an answer that gets its reply': dict(args=run, agents=idle, files=answer, report_after=reply),
            'an answer through a relative path': dict(args=['answer', '{st}', '../st/answers/001-x-a1.md'], agents=idle, files=answer, report_after=reply),
            '--timeout 1000 and no reply': dict(args=run + ['--timeout', '1000'], agents=idle, files=answer),
            '--every 1 and a reply': dict(args=run + ['--every', '1'], agents=idle, files=answer, report_after=reply),
            'a prompt that fails': dict(args=run, agents=idle, files=answer, promptfail=True),
        }
        return {n: {**spec, 'mtimes': {rel: self.OLD_MTIME for rel in spec.get('files', {})}} for n, spec in cases.items()}

    def test_answer_matches_the_bash_at_the_phase_3_base_in_guided(self):
        results = self.stop_ab_run('pstack-pair-guided', lambda n: True, self.answer_cases())
        self.assertEqual((results['the sidekick is absent']['rc'], results['the sidekick is blocked']['rc'], results['the sidekick is working']['rc']), (2, 3, 5))
        self.assertIn('report: <T>/st/reports/001-x.md', results['an answer that gets its reply']['stdout'])
        self.assertEqual(results['--timeout 1000 and no reply']['rc'], 4)
        self.assertIn('send-answer', ' '.join(results['an answer that gets its reply']['events']))

    def test_answer_ab_signals_end_the_command_as_they_end_the_bash(self):
        skills3 = self.phase3_skills()
        world = {k: v for k, v in self.answer_cases()['an answer that gets its reply'].items() if k not in ('args', 'report_after')}
        for number, group in ((signal.SIGTERM, True), (signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGINT, True)):
            run = dict(hold='wait', argv=lambda st: ['answer', str(st), str(st / 'answers/001-x-a1.md')], world=world)
            want = self.wait_signalled(skills3, number, group, 'pstack-pair-guided', **run)
            got = self.wait_signalled(root / 'skills', number, group, 'pstack-pair-guided', **run)
            self.assertEqual(got, want, (number.name, group))
            self.assertEqual(got[0], -number, (number.name, group))

    def test_steer_ab_signals_end_the_command_as_they_end_the_bash(self):
        # The idle path sits in herdr's prompt (held by a fake-herdr hook); a signal ends it with the signal and nothing printed, as it ends the
        # bash, whether it goes to the process group or to the shell alone: the port is one process (pycore --exec).
        skills3 = self.phase3_skills()
        world = {k: v for k, v in self.steer_specs()['an idle sidekick with an open objection'].items() if k not in ('args', 'report_after')}
        for skill in ('pstack-pair', 'pstack-kitchen'):
            for number, group in ((signal.SIGTERM, True), (signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGINT, True)):
                run = dict(hold='prompt', argv=lambda st: ['steer', str(st), str(st / 'steers/001-x-s1.md')], world=world)
                want = self.wait_signalled(skills3, number, group, skill, **run)
                got = self.wait_signalled(root / 'skills', number, group, skill, **run)
                self.assertEqual(got, want, (skill, number.name, group))
                self.assertEqual(got[:3], (-number, '', ''), (skill, number.name, group))

    def test_steer_matches_the_bash_at_the_phase_3_base_in_guided_and_trio(self):
        keep = ('no arguments', 'a missing steer file', 'a working sidekick', 'a working devin', 'the sidekick is blocked', 'an unknown sidekick with --force',
                'an idle sidekick with an open objection', 'an idle sidekick with no objection', 'an idle sidekick that never replies')
        specs = {n: spec for n, spec in self.steer_specs().items() if n in keep}
        for skill in ('pstack-pair-guided', 'pstack-trio'):
            self.stop_ab_run(skill, lambda n: True, specs)

    def test_steer_matches_the_bash_at_the_phase_3_base_in_the_kitchen(self):
        # The idle path waits through the kitchen's own steps_due and report_ready: an escalated unit with steps to review ends the wait on them.
        escalated = {'briefs/001-x.md': '# Brief\n\nrisk: escalated (x)\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/**\n', 'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n'}
        done = {'briefs/001-x.md': '# Brief\n\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/**\n'}
        keep = ('a working sidekick', 'the sidekick is blocked', 'an idle sidekick with an open objection', 'an idle sidekick that never replies', 'an unknown sidekick with --force')
        specs = {n: dict(spec, repo_files={'.agents/kitchen.toml': self.KITCHEN_TOML}) for n, spec in self.steer_specs().items() if n in keep}
        base = self.steer_specs()['an idle sidekick with an open objection']
        specs['an escalated unit with steps to review'] = dict(base, files={**base['files'], **escalated}, mtimes={**base['mtimes'], **{r: self.OLD_MTIME for r in escalated}},
                                                               repo_files={'.agents/kitchen.toml': self.KITCHEN_TOML})
        specs['a routine unit whose reply arrives'] = dict(base, files={**base['files'], **done}, mtimes={**base['mtimes'], **{r: self.OLD_MTIME for r in done}}, report_after='1 {st}/reports/001-x.md',
                                                           repo_files={'.agents/kitchen.toml': self.KITCHEN_TOML})
        results = self.stop_ab_run('pstack-kitchen', lambda n: True, specs)
        self.assertIn('steps: 1 to review on 001-x', results['an escalated unit with steps to review']['stdout'])

    def leaf_both(self, label, bash_snippet, python_snippet=None, logs=True, same=True, sources=(), define='', **spec):
        """A bash helper of the 04d2c13 core (sourced under set -euo pipefail) against its Python twin in a fresh interpreter, on identical
        worlds with the fake agents on PATH: (exit code, stdout, stderr, the fakes' logs). The skills directories and the world's directory
        are masked; so is the scratch directory a Prime preflight makes. Without a Python snippet the second side is the working tree's bash: the
        shims, in the forms their callers use."""
        results = []
        trees = []
        for side in ('bash', 'py'):
            top, st, env, fill = self.ab_world(f'{label}-{side}', {'master': None, **spec})
            skills = self.phase3_skills() if side == 'bash' else root / 'skills'
            trees.append(skills)
            if side == 'bash' or python_snippet is None:
                script = (f'set -euo pipefail\nskill_root={skills / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\n'
                          f'roles=(sidekick); store_dirs=(plans); slug_max=21\n. {skills / "pstack-pair/scripts/pair-core.sh"}\n'
                          + ''.join(f'. {skills / source}\n' for source in sources) + f'store={shlex.quote(str(st))}\nset +e\n{define if side == "py" else ""}{bash_snippet}')
                argv = ['bash', '-c', script]
            else:
                code = (f'import sys\nsys.path.insert(0, {str(scripts)!r})\nfrom pstack_core import agents, panes, spawn\nfrom pstack_core.errors import Die, Exit\n'
                        f'store = {str(st)!r}\n{python_snippet}')
                argv = [sys.executable, '-I', '-c', code]
            r = subprocess.run(argv, cwd=top / 'repo', env=env, capture_output=True, text=True, check=False, timeout=120)
            norm = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'"--cwd", "[^"]+"', '"--cwd", "<SCRATCH>"', re.sub(r'--cwd \S+', '--cwd <SCRATCH>',
                                                                                         text.replace(str(top), '<T>').replace(str(self.phase3_skills()), '<SK>').replace(str(root / 'skills'), '<SK>'))))
            written = {f.name: norm(f.read_text()) for f in sorted((top / 'fake').glob('*.log'))}
            stamps = lambda text: re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', norm(text)))
            kept = {str(f.relative_to(st)): stamps(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file()}
            results.append((r.returncode, norm(r.stdout), norm(r.stderr), written if logs else {}, kept))
        # a comparison of a tree with itself proves nothing: the second side is the working tree (its bash shims, or the Python twin)
        self.assertNotEqual(trees[0], root / 'skills')
        self.assertEqual(trees[1], root / 'skills')
        if not same:
            return results
        self.assertEqual(results[0], results[1], label)
        return results[1]

    LEAF_KINDS = ('claude', 'codex', 'pi', 'devin', 'muse', 'prime-agent', 'grok', 'nobody', '')
    LEAF_MODES = ('auto', 'acceptEdits', 'bypassPermissions', 'manual', 'dontAsk', 'plan', 'none', 'whatever', '')

    def test_permission_trust_and_approval_helpers_match_the_bash_at_the_phase_3_base(self):
        pairs = [(k, m) for k in self.LEAF_KINDS for m in self.LEAF_MODES]
        bash = ''.join(f'echo "== {k} {m}"; permission_args {shlex.quote(k)} {shlex.quote(m)}; echo "rc=$?"; asks_no_approvals {shlex.quote(k)} {shlex.quote(m)}; echo "asks=$?"\n' for k, m in pairs)
        bash += ''.join(f'echo "== trust {k}"; trust_args {shlex.quote(k)}; echo "rc=$?"\n' for k in self.LEAF_KINDS)
        py = (f'for k, m in {pairs!r}:\n    print(f"== {{k}} {{m}}")\n    sys.stdout.write(agents.lines(agents.permission_args(k, m)))\n    print("rc=0")\n'
              f'    print(f"asks={{agents.status(agents.asks_no_approvals(k, m))}}")\n'
              f'for k in {self.LEAF_KINDS!r}:\n    print(f"== trust {{k}}")\n    sys.stdout.write(agents.lines(agents.trust_args(k)))\n    print("rc=0")\n')
        out = self.leaf_both('leaf-perm', bash, py)
        self.assertIn('== devin plan\n--permission-mode\nauto\nrc=0', out[1])
        self.assertIn('== codex dontAsk\n-a\nnever\n-s\nworkspace-write\nrc=0', out[1])
        self.assertIn('== pi plan\nrc=0\nasks=1', out[1])

    def test_the_has_helpers_match_the_bash_at_the_phase_3_base(self):
        argvs = [[], ['--'], ['x'], ['--mcp-config'], ['--mcp-config=x.json'], ['--mcp-config', 'x'], ['--strict-mcp-config'], ['mcp_servers.a.enabled=false'],
                 ['-c', 'mcp_servers.a.enabled=false'], ['--config=mcp_servers.a'], ['--config', 'mcp_servers.a'], ['--', '--mcp-config'], ['--mcp-configx'],
                 ['--approve'], ['--no-approve'], ['-na'], ['--respect-workspace-trust'], ['--respect-workspace-trust=false'], ['--respect-workspace-trust', 'false'],
                 ['--trust-workspace'], ['--yolo'], ['--approvex'], ['--permission-mode'], ['--permission-mode=plan'], ['--permission-mode', 'plan'],
                 ['--dangerously-skip-permissions'], ['--allow-dangerously-skip-permissions'], ['-a'], ['-a', 'never'], ['--ask-for-approval'], ['--ask-for-approval=never'],
                 ['-s'], ['--sandbox'], ['--sandbox=read-only'], ['--dangerously-bypass-approvals-and-sandbox'], ['--approval-mode'], ['--approval-mode=never'],
                 ['--disable-approval'], ['--disable-sandbox'], ['--permission-profile'], ['--permission-profile=x'], ['-p', '--model', 'm'], [''], ['a b', '--yolo'], ['--', 'x', '--approve'], ['--', '--yolo'], ['--', '--permission-mode=plan'], ['--', '--mcp-config=x']]
        names = ('has_mcp_arg', 'has_trust_arg', 'has_permission_arg')
        bash = ''.join(f'{n} {shlex.join(a)}; echo "{n} {a!r} $?"\n' for n in names for a in argvs)
        py = (f'for n, a in {[(n, a) for n in names for a in argvs]!r}:\n    print(f"{{n}} {{a!r}} {{agents.status(getattr(agents, n)(a))}}")\n')
        out = self.leaf_both('leaf-has', bash, py)
        self.assertIn("has_mcp_arg ['--mcp-config=x.json'] 0", out[1])
        self.assertIn("has_permission_arg ['--', '--mcp-config'] 1", out[1])

    def test_the_preflights_match_the_bash_at_the_phase_3_base(self):
        fast = {'PAIR_PREFLIGHT_RETRY_S': '0'}
        hang = {**fast, 'PAIR_PREFLIGHT_TIMEOUT': '1'}
        noisy = 'line one\n\n   \nline two\nline three\nline four\nline five\nline six\n\n'
        cases = {
            'no preflight for claude': ('claude', [], {}, fast), 'no preflight for an unknown kind': ('nobody', ['--x'], {}, fast),
            'pi answers': ('pi', ['--model', 'm'], {}, fast),
            'pi drops the approve and no-approve flags': ('pi', ['--approve', '-a', '--no-approve', '-na', '--model', 'm', 'a b'], {}, fast),
            'pi fails once then answers': ('pi', [], {'pi-fail-once': ''}, fast),
            'pi fails twice': ('pi', [], {'pi-fail': 'quota exhausted\n'}, fast),
            'pi fails twice with a long noisy message': ('pi', [], {'pi-fail': noisy}, fast),
            'pi answers without OK': ('pi', [], {'pi-reply': 'hello\n'}, fast),
            'pi answers with nothing': ('pi', [], {'pi-reply': ''}, fast),
            'pi answers with blanks only': ('pi', [], {'pi-reply': '  \n\n'}, fast),
            'pi OK inside a sentence': ('pi', [], {'pi-reply': 'it is OKAY\n'}, fast),
            'pi fails twice with a retry wait that is not a number': ('pi', [], {'pi-fail': 'quota exhausted\n'}, {**fast, 'PAIR_PREFLIGHT_RETRY_S': 'x'}),
            'prime fails twice with a retry wait that is not a number': ('prime-agent', [], {'prime-fail': 'capacity issues\n'}, {**fast, 'PAIR_PREFLIGHT_RETRY_S': 'x'}),
            'pi fails once with a retry wait that is not a number': ('pi', [], {'pi-fail-once': ''}, {**fast, 'PAIR_PREFLIGHT_RETRY_S': 'x'}),
            'pi hangs': ('pi', [], {'pi-hang': ''}, hang),
            'muse answers': ('muse', ['--model', 'm', '--provider', 'p', '--contributor-mode', 'c', '--permission-profile', 'x', '--other', 'y', 'z'], {}, fast),
            'muse with a flag and no value': ('muse', ['--model', 'm', 'stray'], {}, fast),
            'muse fails': ('muse', ['--model', 'm'], {'muse-fail': 'logged out\n'}, fast),
            'muse fails with a long message': ('muse', [], {'muse-fail': noisy}, fast),
            'muse answers without OK': ('muse', [], {'muse-reply': 'no\n'}, fast),
            'muse answers with nothing': ('muse', [], {'muse-reply': ''}, fast),
            'muse hangs': ('muse', [], {'muse-hang': ''}, hang),
            'prime answers': ('prime-agent', ['--model', 'm'], {}, fast),
            'prime fails once then answers': ('prime-agent', [], {'prime-fail-once': ''}, fast),
            'prime fails twice': ('prime-agent', ['--model', 'm'], {'prime-fail': 'capacity issues\n'}, fast),
            'prime fails twice with a long message': ('prime-agent', [], {'prime-fail': noisy}, fast),
            'prime answers OK padded with blanks': ('prime-agent', [], {'prime-reply': '  OK  \n'}, fast),
            'prime answers OK among other lines': ('prime-agent', [], {'prime-reply': 'thinking\nOK\n'}, fast),
            'prime answers OK inside a sentence': ('prime-agent', [], {'prime-reply': 'OK, sure\n'}, fast),
            'prime answers with nothing': ('prime-agent', [], {'prime-reply': ''}, fast),
            'prime hangs': ('prime-agent', [], {'prime-hang': ''}, {**hang}),
        }
        results = {}
        for label, (kind, args, fake, env) in cases.items():
            bash = f'preflight_kind {shlex.join([kind, *args])}; echo "rc=$?"'
            py = f'code = agents.preflight_kind({kind!r}, {args!r})\nprint(f"rc={{code}}")'
            # A fake that never answers is cut off after one real second, and whether it got to write its argv log first depends on the load (the
            # Prime fake starts an interpreter first): the timeout cases compare status and summary, the answering cases compare the argv logs.
            results[label] = self.leaf_both('leaf-pre-' + re.sub(r'\W+', '-', label), bash, py, logs='hangs' not in label, fake=fake, env=env)
        # the kind's binary absent from PATH: the summary names no version and no path, and the attempt's status is timeout(1)'s 127
        tools = self.path / 'only-these'
        tools.mkdir()
        for name in ('bash', 'timeout', 'jq', 'python3', 'cat', 'date', 'mkdir', 'mktemp', 'rm', 'grep', 'sed', 'tr', 'tail', 'head', 'basename', 'dirname', 'readlink', 'sleep',
                     'ls', 'cut', 'env', 'perl', 'chmod', 'cp', 'mv', 'printf', 'tee', 'sort', 'wc', 'awk', 'git', 'ps'):
            found = shutil.which(name)
            if found:
                (tools / name).symlink_to(found)
        (tools / 'herdr').symlink_to(root / 'tests/pstack-pair/bin/herdr')
        for kind in ('pi', 'prime-agent'):
            absent = self.leaf_both('leaf-pre-absent-' + kind, f'preflight_kind {kind}; echo "rc=$?"', f'code = agents.preflight_kind({kind!r}, [])\nprint(f"rc={{code}}")',
                                    env={'PAIR_PREFLIGHT_RETRY_S': '0', 'PATH': str(tools)})
            self.assertIn(f'{kind} ? at , twice:', absent[2])
            self.assertIn('exit 127', absent[2])
        self.assertEqual(results['pi answers'][1], 'rc=0\n')
        self.assertIn('pi 1.0.2 at ', results['pi fails twice'][2])
        self.assertIn('timed out after 1s', results['pi hangs'][2])
        self.assertIn('answered without OK', results['pi answers without OK'][2])
        self.assertIn('(no output)', results['pi answers with nothing'][2])
        self.assertEqual(results['no preflight for claude'][1], 'rc=0\n')
        self.assertEqual(results['pi fails once then answers'][3]['pi.log'].count('\n'), 2)

    def test_has_preflight_matches_the_bash_at_the_phase_3_base(self):
        bash = ''.join(f'has_preflight {shlex.quote(k)}; echo "{k!r} $?"\n' for k in self.LEAF_KINDS)
        py = f'for k in {self.LEAF_KINDS!r}:\n    print(f"{{k!r}} {{agents.status(agents.has_preflight(k))}}")\n'
        self.assertEqual(self.leaf_both('leaf-has-pre', bash, py)[1].count(' 0\n'), 3)

    def test_the_shims_behave_as_the_bash_bodies_in_the_forms_their_callers_use(self):
        # The working tree's bash (shims into Python) against 04d2c13's bodies, through the callers' forms: mapfile from a process substitution,
        # if / ! / || on the has_* statuses, and mcp_args' status under set -e.
        argvs = [[], ['--yolo'], ['--mcp-config=x'], ['--approve'], ['-a', 'never'], ['--', '--yolo'], ['x', 'y']]
        snippet = ''
        for kind in self.LEAF_KINDS:
            for mode in self.LEAF_MODES:
                snippet += f'mapfile -t got < <(permission_args {shlex.quote(kind)} {shlex.quote(mode)}); echo "{kind}/{mode}: ${{#got[@]}} ${{got[*]-}}"\n'
            snippet += f'mapfile -t got < <(trust_args {shlex.quote(kind)}); echo "trust {kind}: ${{got[*]-}}"\n'
            snippet += f'if has_preflight {shlex.quote(kind)}; then echo pre; else echo nopre; fi\n'
        for argv in argvs:
            a = shlex.join(argv)
            snippet += (f'if has_mcp_arg {a}; then echo m1; else echo m0; fi\nif ! has_trust_arg {a}; then echo t0; fi\nhas_permission_arg {a} || echo p0\n'
                        f'has_mcp_arg {a} && echo m-and\n')
        out = self.leaf_both('shims-forms', snippet)
        self.assertIn('claude/plan: 2 --permission-mode plan', out[1])
        for label, fake in {'refused last': {'codex-mcp.json': '[{"name": "ok"}, {"name": "not ok"}]\n'}, 'a string element': {'codex-mcp.json': '[{"name": "a"}, "s"]\n'},
                            'not JSON': {'codex-mcp.json': 'oops\n'}, 'fine': {'codex-mcp.json': '[{"name": "a"}]\n'}}.items():
            out = self.leaf_both('shims-mcp-' + label.replace(' ', '-'), 'set -e\nmapfile -t got < <(mcp_args codex "$PWD"); echo "got: ${got[*]}"\nmcp_args codex "$PWD"\necho "after"\n', fake=fake)
            self.assertEqual(out[0], 0 if label == 'fine' else {'refused last': 1, 'a string element': 5, 'not JSON': 5}[label], label)

    DIE = ('\ndef run(fn):\n    try:\n        return fn()\n    except Die as e:\n        sys.stderr.write(f"pair.sh: {e.message}\\n")\n        sys.exit(e.code)\n    except Exit as e:\n        sys.stderr.write(e.stderr)\n        sys.exit(e.code)\n')

    def test_pick_direction_and_new_pane_match_the_bash_at_the_phase_3_base(self):
        master = {'master': {'name': 'm1', 'pane_id': 'w1:p0'}}
        cases = {'a wide pane': {'layout-size': '200 50'}, 'exactly twice as wide': {'layout-size': '100 50'}, 'a hair narrower': {'layout-size': '99 50'},
                 'a tall pane': {'layout-size': '40 60'}, 'a pane the layout does not list': {'layout-gone': ''}, 'a height that is null': {'layout-size': '40 null'}, 'a width that is null': {'layout-size': 'null 40'}}
        for label, fake in cases.items():
            out = self.leaf_both('pane-dir-' + label.replace(' ', '-'), 'pick_direction p1; echo "rc=$?"', self.DIE.lstrip('\n') + 'print(run(lambda: panes.pick_direction("p1")))\nprint("rc=0")', fake=fake)
            if label == 'a height that is null':
                self.assertEqual(out[0], 127)
        for label, (pair, direction, fake) in {
                'a split picked from the anchor': ({}, '', {}), 'a split to the down': ({}, 'down', {}), 'a split to the left': ({}, 'left', {}),
                'a tall anchor': ({}, '', {'layout-size': '40 60'}), 'a tab': ({'placement': 'tab', **master}, '', {}),
                'a tab in another workspace': ({'placement': 'tab', 'master': {'name': 'm1', 'pane_id': 'w7:p3'}}, '', {}),
                'a split that returns no id': ({}, '', {'split-null': ''}), 'a tab that returns no id': ({'placement': 'tab', **master}, '', {'tab-null': ''})}.items():
            py = self.DIE.lstrip('\n') + f'print("id=" + run(lambda: panes.new_pane(store, "sidekick", "p1", {direction!r}, __import__("os").getcwd())))'
            self.leaf_both('pane-new-' + label.replace(' ', '-'), f'new_pane "$store" sidekick p1 {shlex.quote(direction)} "$PWD"; echo "id=$new_pane_id"', py, pair=pair, fake=fake)
        self.assertIn('split p1 right -> p', self.leaf_both('pane-new-check', 'new_pane "$store" sidekick p1 "" "$PWD"; echo "id=$new_pane_id"',
                      self.DIE.lstrip('\n') + 'print("id=" + run(lambda: panes.new_pane(store, "sidekick", "p1", "", __import__("os").getcwd())))')[1])

    def test_refuse_untrusted_and_await_file_match_the_bash_at_the_phase_3_base(self):
        dialog = {'trust-dialog': ''}
        for label, (fake, close) in {'no dialog': ({}, ''), 'a dialog': (dialog, ''), 'a dialog and a close': (dialog, 'close')}.items():
            call = f'refuse_untrusted p1 claude /work {close}'
            py = self.DIE.lstrip('\n') + f'run(lambda: panes.refuse_untrusted("p1", "claude", "/work", {bool(close)}))\nprint("returned")'
            self.leaf_both('pane-trust-' + label.replace(' ', '-'), f'{call}; echo "returned"', py, fake=fake)
            # the shim in the working tree's bash ends the caller's shell with the die's status
            out = self.leaf_both('pane-trust-shim-' + label.replace(' ', '-'), f'( {call} ); echo "rc=$?"; {call}; echo "after"', None, fake=fake)
            if fake:
                self.assertIn('rc=5', out[1])
                self.assertNotIn('after', out[1])
        for label, (present, wait_ms) in {'a file that is there': (True, 300), 'a file that never comes': (False, 300), 'no time to wait': (False, 0)}.items():
            mk = 'touch "$store/f"; ' if present else ''
            self.leaf_both('pane-await-' + label.replace(' ', '-'), f'{mk}if await_file "$store/f" {wait_ms}; then echo yes; else echo no; fi',
                           f'import os\nif {present}: open(store + "/f", "w").close()\nprint("yes" if panes.await_file(store + "/f", {wait_ms}) else "no")',
                           env={'PAIR_FILE_POLL_MS': '1'})
        # a file that appears while it polls: the bash's external sleep and the port's sleep each create it on their second call
        fake_sleep = self.path / 'fake-sleep'
        fake_sleep.mkdir()
        (fake_sleep / 'sleep').write_text('#!/bin/sh\nn=$(cat "$FAKE/sleeps" 2>/dev/null || echo 0); n=$((n + 1)); echo $n >"$FAKE/sleeps"; [ "$n" -lt 2 ] || : >"$FAKE/../st/f"\n')
        (fake_sleep / 'sleep').chmod(0o755)
        py = ('import os\ncalls = []\ndef fake(text):\n    calls.append(text)\n    if len(calls) >= 2:\n        open(store + "/f", "w").close()\n'
              'panes.bash_sleep = fake\nprint("yes" if panes.await_file(store + "/f", 100000) else "no", len(calls))')
        bash = 'PATH="' + str(fake_sleep) + ':$PATH"; if await_file "$store/f" 100000; then echo "yes $(cat "$FAKE/sleeps")"; else echo no; fi'
        self.assertEqual(self.leaf_both('pane-await-appears', bash, py, env={'PAIR_FILE_POLL_MS': '1000'})[1], 'yes 2\n')

    def test_new_pane_when_herdr_fails_differs_from_the_bash_only_where_set_e_decides(self):
        # At 04d2c13 `new_pane_id="$(herdr ... | jq ...)"` under set -euo pipefail ends a caller's shell with status 1 and no message where errexit
        # is in force (a plain command), and reaches the die (status 2) where it is ignored (`|| rc=$?`). The port always dies with status 2: it
        # cannot tell which context it was called from. For a herdr that fails outright the two differ in the plain-command form: a documented
        # difference, pinned here on both sides.
        py = self.DIE.lstrip('\n') + 'run(lambda: panes.new_pane(store, "sidekick", "p1", "", __import__("os").getcwd()))'
        call = 'new_pane "$store" sidekick p1 "" "$PWD"'
        for label, (pair, fake) in {'a split': ({}, {'split-fail': ''}),
                                    'a tab': ({'placement': 'tab', 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}, {'tab-fail': ''})}.items():
            bash_or, port_or = self.leaf_both(f'pane-fail-or-{label[2:]}', f'rc=0; {call} || rc=$?; echo "rc=$rc"', py, same=False, pair=pair, fake=fake)
            bash_plain, port_plain = self.leaf_both(f'pane-fail-plain-{label[2:]}', f'set -e\n{call}\necho "after"', py, same=False, pair=pair, fake=fake)
            # errexit ignored: the bash reaches its die (status 2, the message), as the port does
            self.assertEqual((bash_or[0], 'returned no pane id' in bash_or[2]), (2, True), label)
            self.assertEqual((port_or[0], 'returned no pane id' in port_or[2]), (2, True), label)
            # errexit in force: the bash ends at once with status 1 and no message; the port dies with 2 and the message
            self.assertEqual((bash_plain[0], 'no pane id' in bash_plain[2]), (1, False), label)
            self.assertEqual((port_plain[0], 'no pane id' in port_plain[2]), (2, True), label)

    READY_HOOK = ('#!/bin/sh\nst="$FAKE/../st"\ncase "$2" in Load*) case "$1" in c1) printf "# R\\n\\nstatus: answered\\n" >"$st/advice/000-ready.md" ;; '
                  '*) printf "# R\\n\\nstatus: done\\n" >"$st/reports/000-ready.md" ;; esac ;; esac\nexit 0\n')

    def spawn_cases(self):
        """Worlds for spawn_role: (spec, argv of the call). The call is `spawn_role store role kind permission anchor timeout direction pane [args]`."""
        hook = {'hook': self.READY_HOOK, 'env': {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300'}}
        quiet = {'env': {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300'}}
        pane1 = {'panes': {'p1': ''}}
        old = lambda **kw: {'sidekick': {'name': 's1', 'pane_id': 'p1', 'kind': 'claude', 'generation': 1, **kw}}
        call = lambda role='sidekick', kind='claude', perm='plan', anchor='p0', direction='', pane='', args=(): [role, kind, perm, anchor, '5000', direction, pane, *args]
        prime = {'agents': {}}
        pf = lambda **kw: {'prime-sessions.json': '[]', **kw}
        return {
            'an already live role': (dict(agents={'s1': 'idle claude'}, pair=old(), **hook), call()),
            'a pending bootstrap without its ready file': (dict(agents={'s1': 'idle claude'}, pair=old(bootstrap_pending=True), **quiet), call()),
            'a pending bootstrap with its ready file': (dict(agents={'s1': 'idle claude'}, pair=old(bootstrap_pending=True), files={'reports/000-ready.md': '# R\n'}, **quiet), call()),
            'a previous pane reused': (dict(pair=old(), **pane1, **hook), call()),
            'a reused pane that shows a trust dialog': (dict(pair=old(), **pane1, fake={'trust-dialog': ''}, **hook), call()),
            'a reused pane where the start fails': (dict(pair=old(), **pane1, fake={'start-fail': ''}, **hook), call()),
            'a pane given by the caller': (dict(pair=old(pane_id=None), **pane1, **hook), call(pane='p1')),
            'a new split': (dict(pair=old(pane_id=None), **hook), call()),
            'a new split in a direction': (dict(pair=old(pane_id=None), **hook), call(direction='down')),
            'a new tab': (dict(pair={**old(pane_id=None), 'placement': 'tab', 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}, **hook), call()),
            'a start that fails in a new pane': (dict(pair=old(pane_id=None), fake={'start-fail': ''}, **hook), call()),
            'a start that fails in a new pane behind a trust dialog': (dict(pair=old(pane_id=None), fake={'start-fail': '', 'trust-dialog': ''}, **hook), call()),
            'a bootstrap that settles with the ready file': (dict(pair=old(pane_id=None), **hook), call(args=('--model', 'm'))),
            'a bootstrap that settles without it': (dict(pair=old(pane_id=None), **quiet), call()),
            'a consultant bootstrap that settles without it': (dict(pair={**old(pane_id=None), 'consultant': {'name': 'c1', 'kind': 'codex', 'pane_id': None}}, **quiet), call('consultant', 'codex')),
            'a consultant bootstrap that settles with it': (dict(pair={**old(pane_id=None), 'consultant': {'name': 'c1', 'kind': 'codex', 'pane_id': None}}, **hook), call('consultant', 'codex')),
            'a prompt that stalls and gets its Enter': (dict(pair=old(pane_id=None), fake={'stall': ''}, **hook), call()),
            'a prompt that is dropped and resent': (dict(pair=old(pane_id=None), fake={'prompt-drop': ''}, **hook), call()),
            'a prompt that is dropped by a dialog': (dict(pair=old(pane_id=None), fake={'prompt-drop': 'blocked'}, **hook), call()),
            'a prompt that fails': (dict(pair=old(pane_id=None), promptfail=True, **hook), call()),
            'a fresh-per-brief kind with --resume': (dict(pair=old(pane_id=None, kind='devin'), **hook), call(kind='devin', args=('--resume',))),
            'a fresh-per-brief kind with --session=x': (dict(pair=old(pane_id=None, kind='pi'), **hook), call(kind='pi', args=('--session=x',))),
            'a fresh-per-brief kind with -c': (dict(pair=old(pane_id=None, kind='devin'), **hook), call(kind='devin', args=('-c',))),
            'a kind that carries -c and may': (dict(pair=old(pane_id=None, kind='codex'), **hook), call(kind='codex', args=('-c', 'x=1'))),
            'a permission from the master': (dict(pair=old(pane_id=None), **hook), call(perm='')),
            'devin without a permission': (dict(pair=old(pane_id=None, kind='devin'), **hook), call(kind='devin', perm='')),
            'permission none': (dict(pair=old(pane_id=None), **hook), call(perm='none')),
            'native permission args': (dict(pair=old(pane_id=None), **hook), call(args=('--permission-mode', 'plan'))),
            'a kind with no translation': (dict(pair=old(pane_id=None, kind='grok'), **hook), call(kind='grok', perm='plan')),
            'a prime start': (dict(pair=old(pane_id=None, kind='prime-agent'), **prime, fake=pf(), **hook), call(kind='prime-agent', perm='plan')),
            'a prime start that fails in a new pane': (dict(pair=old(pane_id=None, kind='prime-agent'), **prime, fake=pf(**{'start-fail': ''}), **hook), call(kind='prime-agent', perm='plan')),
            'a prime start in a reused pane that fails': (dict(pair=old(kind='prime-agent'), **prime, **pane1, fake=pf(**{'start-fail': ''}), **hook), call(kind='prime-agent', perm='plan')),
            'a prime that reports no session': (dict(pair=old(pane_id=None, kind='prime-agent'), **prime, fake=pf(**{'prime-no-session': ''}), **hook), call(kind='prime-agent', perm='plan')),
            'a prime sidekick whose bootstrap settles without it': (dict(pair=old(pane_id=None, kind='prime-agent'), **prime, fake=pf(), **quiet), call(kind='prime-agent', perm='plan')),
            'a split that fails': (dict(pair=old(pane_id=None), fake={'split-fail': ''}, **hook), call()),
            'a tab that fails': (dict(pair={**old(pane_id=None), 'placement': 'tab', 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}, fake={'tab-fail': ''}, **hook), call()),
            'a bootstrap that settles without it and a rate limit on screen': (dict(pair={**old(pane_id=None), 'fallback': {'kind': 'devin'}}, screens={'s1': 'Error: rate limit reached\\n'}, **quiet), call()),
            'a stalled prompt whose ready name is on screen': (dict(pair=old(pane_id=None), fake={'prompt-drop': ''}, screens={'s1': 'type 000-ready.md\\n'}, **quiet), call()),
            'a stalled prompt for a working agent': (dict(pair=old(pane_id=None), fake={'prompt-drop': 'working'}, **quiet), call()),
            'a previous ready file moved aside': (dict(pair=old(pane_id=None), files={'reports/000-ready.md': '# old\n'}, **hook), call()),
        }

    # spawn_role had no bash caller left once rotate and failover moved, so its bash shim is gone; the call forms it served are still run, against a
    # definition of the same shim in the test, so the Python entry is pinned the way the bash callers used it.
    SPAWN_ROLE_SHIM = 'spawn_role() { pycore __spawn_role "$@" || { local pycore_rc=$?; case $pycore_rc in 3 | 4) return $pycore_rc ;; *) exit $pycore_rc ;; esac; }; }\n'
    SPAWN_FORMS = {
        'with an or': 'rc=0; spawn_role "$store" {a} || rc=$?; echo "returned rc=$rc"',
        'as a plain command': 'spawn_role "$store" {a}; echo "returned"',
        'as a plain command under errexit': 'set -e\nspawn_role "$store" {a}\necho "returned"',
    }
    # Where herdr itself fails to open a pane, 04d2c13's `new_pane_id="$(herdr ... | jq ...)"` ends a caller whose errexit is in force (the under-errexit form) with status 1
    # and no message (the port dies with 2 and the message, as the bash does where errexit is ignored): a documented difference, pinned below.
    SPAWN_ERREXIT_DIFFERENCE = ('a split that fails', 'a tab that fails')

    def spawn_ab(self, part, size=6):
        """The part-th slice of `size` spawn cases through the three call forms (with an or, as a plain command, under errexit): 04d2c13's function
        against the working tree's shim, {(case, form): outcome}."""
        results = {}
        for n, (label, (spec, argv)) in enumerate(self.spawn_cases().items()):
            if not part * size <= n < (part + 1) * size:
                continue
            for form, template in self.SPAWN_FORMS.items():
                call = template.format(a=shlex.join(argv))
                if label in self.SPAWN_ERREXIT_DIFFERENCE and form == 'as a plain command under errexit':
                    bash_side, port_side = self.leaf_both(f'spawn-{part}-{len(results)}', call, None, same=False, define=self.SPAWN_ROLE_SHIM, **spec)
                    self.assertEqual((bash_side[0], 'no pane id' in bash_side[2]), (1, False), (label, form))
                    self.assertEqual((port_side[0], 'no pane id' in port_side[2]), (2, True), (label, form))
                    results[(label, form)] = port_side
                else:
                    results[(label, form)] = self.leaf_both(f'spawn-{part}-{len(results)}', call, None, define=self.SPAWN_ROLE_SHIM, **spec)
        return results

    SPAWN_EXPECT = {
        ('an already live role', 'with an or'): (0, 'already live (idle)'),
        ('a new split', 'with an or'): (0, 'started in p'),
        ('a pending bootstrap without its ready file', 'with an or'): (0, 'returned rc=4'),
        ('a bootstrap that settles without it', 'with an or'): (0, 'returned rc=4'),
        ('a prompt that fails', 'with an or'): (0, 'returned rc=3'),
        ('a prompt that fails', 'as a plain command under errexit'): (3, ''),
        ('a bootstrap that settles without it', 'as a plain command under errexit'): (4, ''),
        ('a fresh-per-brief kind with --resume', 'with an or'): (5, ''),
        ('a prime start', 'with an or'): (0, 'ready: '),
        ('a stalled prompt whose ready name is on screen', 'with an or'): (0, 'returned rc=3'),
        ('a bootstrap that settles without it and a rate limit on screen', 'with an or'): (0, 'returned rc=4'),
    }

    def test_the_spawn_role_slices_cover_every_case(self):
        size, parts = 6, [int(m) for m in re.findall(r'test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_([0-9]+)', Path(__file__).read_text())]
        self.assertEqual(sorted(set(parts)), list(range(len(set(parts)))))
        self.assertGreaterEqual(len(set(parts)) * size, len(self.spawn_cases()), 'spawn_ab slices leave cases out: add a part')
        self.assertLess((len(set(parts)) - 1) * size, len(self.spawn_cases()), 'a part has no case')

    def check_spawn_part(self, part):
        results = self.spawn_ab(part)
        for key, (code, needle) in self.SPAWN_EXPECT.items():
            if key in results:
                self.assertEqual(results[key][0], code, key)
                self.assertIn(needle, results[key][1], key)
        hint = ('a bootstrap that settles without it and a rate limit on screen', 'with an or')
        if hint in results:
            self.assertRegex(results[hint][2], r'(?i)rate.?limit', 'the provider hint is printed')
        return results

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_0(self):
        self.check_spawn_part(0)

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_1(self):
        self.check_spawn_part(1)

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_2(self):
        self.check_spawn_part(2)

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_3(self):
        self.check_spawn_part(3)

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_4(self):
        self.check_spawn_part(4)

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_5(self):
        self.check_spawn_part(5)

    def test_spawn_role_matches_the_bash_at_the_phase_3_base_in_every_call_form_part_6(self):
        self.check_spawn_part(6)

    def test_mcp_args_match_the_bash_at_the_phase_3_base(self):
        servers = lambda *items: {'codex-mcp.json': json.dumps(list(items)) + '\n'}
        cases = {
            'claude': ('claude', {}),
            'codex with no servers': ('codex', {}),
            'codex with servers': ('codex', servers({'name': 'github', 'enabled': True}, {'name': 'docs'}, {'name': 'old', 'enabled': False})),
            'codex with names the filter refuses': ('codex', servers({'name': 'ok_1-b'}, {'name': 'not ok'}, {'name': 'x;y'}, {'name': ''}, {'name': 5}, {})),
            'codex with a null and a string in the list': ('codex', servers({'name': 'a'}, None, {'name': 'b'}, 'str', {'name': 'c'})),
            'codex with an object instead of a list': ('codex', {'codex-mcp.json': '{"x": {"name": "viaobject"}, "y": {"name": "other", "enabled": false}}\n'}),
            'codex with output that is not JSON': ('codex', {'codex-mcp.json': 'oops\n'}),
            'codex whose last name is refused': ('codex', servers({'name': 'ok'}, {'name': 'not ok'})),
            'codex with a name that spans lines': ('codex', servers({'name': 'a\nb'})),
            'pi': ('pi', {}), 'devin': ('devin', {}), 'an unknown kind': ('x', {})}
        for label, (kind, fake) in cases.items():
            bash = f'mcp_args {kind} "$PWD"; echo "rc=$?"'
            py = f'import os\nargs, code = agents.mcp_args({kind!r}, os.getcwd())\nsys.stdout.write(agents.lines(args))\nprint(f"rc={{code}}")'
            out = self.leaf_both('leaf-mcp-' + re.sub(r'\W+', '-', label), bash, py, fake=fake)
        # the directory codex runs in: another than the process's, none given, empty, and one that does not exist
        for label, call in {'a directory of its own': '"$PWD/sub"', 'none given': None, 'an empty one': '""', 'one that does not exist': '"$PWD/none"'}.items():
            arg = '' if call is None else f' {call}'
            bash = f'mkdir -p "$PWD/sub"; mcp_args codex{arg}; echo "rc=$?"'
            py = (f'import os\nos.makedirs(os.getcwd() + "/sub", exist_ok=True)\ncwd = {None if call is None else "os.getcwd() + " + repr("/sub" if "sub" in call else "/none") if call != chr(34) * 2 else repr("")}\n'
                  'args, code = agents.mcp_args("codex", cwd)\nsys.stdout.write(agents.lines(args))\nprint(f"rc={code}")')
            out = self.leaf_both('leaf-mcp-cwd-' + re.sub(r'\W+', '-', label), bash, py, fake=servers({'name': 'github'}))
        self.assertIn('(cwd <T>/repo/sub)', self.leaf_both('leaf-mcp-cwd-sub', 'mkdir -p "$PWD/sub"; mcp_args codex "$PWD/sub"',
                      'import os\nos.makedirs(os.getcwd() + "/sub", exist_ok=True)\nsys.stdout.write(agents.lines(agents.mcp_args("codex", os.getcwd() + "/sub")[0]))',
                      fake=servers({'name': 'github'}))[3]['calls.log'])
        self.assertEqual(self.leaf_both('leaf-mcp-claude-path', 'mcp_args claude', 'sys.stdout.write(agents.lines(agents.mcp_args("claude")[0]))')[1],
                         '--strict-mcp-config\n--mcp-config\n<SK>/pstack-pair/scripts/../references/no-mcp.json\n')
        self.assertIn('-c\nmcp_servers.github.enabled=false\n', self.leaf_both('leaf-mcp-names', 'mcp_args codex "$PWD"', 'import os\nsys.stdout.write(agents.lines(agents.mcp_args("codex", os.getcwd())[0]))',
                                                                                 fake=servers({'name': 'github'}))[1])

    def test_phase3_skills_extracts_the_bash_at_the_phase_3_base(self):
        core = self.phase3_skills() / 'pstack-pair/scripts/pair-core.sh'
        self.assertTrue(core.is_file())
        subprocess.run(['bash', '-n', str(core)], check=True, capture_output=True, timeout=60)

    def detect_in(self, procs, strict=False, files=None, session=''):
        """permission.detect over a table {pid: (ppid, comm, args)} in place of ps, with HOME and cwd under the test's directory."""
        home, cwd = self.path / 'home', self.path / 'cwd'
        for rel, text in (files or {}).items():
            (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.path / rel).write_text(text)

        def ps(*args):
            row = procs.get(int(args[3]))
            if row is None:
                return 1, ''
            return 0, {'ppid=': f'  {row[0]}', 'comm=': row[1], 'args=': row[2]}[args[1]]
        return permission.detect(100, str(cwd), str(home), session, strict, ps)

    def test_detect_reads_the_agent_above_the_shell_and_stops_after_twelve_hops(self):
        shell = (101, 'bash', 'bash')
        agent = lambda pid, args: (1, 'claude', f'claude {args}')
        for shells, want in ((11, 'plan'), (12, 'auto'), (13, 'auto')):
            procs = {100: (101, 'bash', 'bash')}
            procs.update({100 + i: (101 + i, 'bash', 'bash') for i in range(1, shells)})
            procs[100 + shells] = agent(100 + shells, '--permission-mode plan')
            self.assertEqual(self.detect_in(procs), want, f'{shells} shells before the agent')
        self.assertEqual(self.detect_in({100: shell, 101: agent(101, '--dangerously-skip-permissions')}), 'bypassPermissions')
        self.assertEqual(self.detect_in({100: shell, 101: (1, 'node', 'node /x/claude --permission-mode=acceptEdits')}), 'acceptEdits')
        self.assertEqual(self.detect_in({100: shell, 101: (1, 'codex', 'codex --ask-for-approval=never')}), 'dontAsk')
        self.assertEqual(self.detect_in({100: shell, 101: (1, 'sh', 'sh -c claude --permission-mode plan')}), 'auto')

    def test_detect_falls_back_to_the_transcript_then_the_settings_files_in_order_then_auto(self):
        alone = {100: (1, 'bash', 'bash')}
        settings = lambda mode: '{"permissions": {"defaultMode": "%s"}}' % mode
        cwd_rel = 'cwd'
        self.assertEqual(self.detect_in(alone), 'auto')
        files = {'home/.claude/settings.json': settings('acceptEdits')}
        self.assertEqual(self.detect_in(alone, files=files), 'acceptEdits')
        files['cwd/.claude/settings.json'] = settings('dontAsk')
        self.assertEqual(self.detect_in(alone, files=files), 'dontAsk')
        files['home/.claude/settings.local.json'] = settings('manual')
        self.assertEqual(self.detect_in(alone, files=files), 'dontAsk')
        files['cwd/.claude/settings.local.json'] = settings('plan')
        self.assertEqual(self.detect_in(alone, files=files), 'plan')
        slug = re.sub(r'[/.]', '-', str(self.path / cwd_rel))
        files[f'home/.claude/projects/{slug}/s1.jsonl'] = '{"permissionMode":"auto"}\n{"permissionMode":"bypassPermissions"}\n'
        self.assertEqual(self.detect_in(alone, files=files, session='s1'), 'bypassPermissions')

    def test_detect_ends_with_the_status_of_a_failing_command_only_when_the_caller_runs_with_errexit(self):
        alone = {100: (1, 'bash', 'bash')}
        files = {'cwd/.claude/settings.local.json': '{not json', 'home/.claude/settings.json': '{"permissions": {"defaultMode": "plan"}}'}
        self.assertEqual(self.detect_in(alone, files=files), 'plan')
        with self.assertRaises(Exit) as raised:
            self.detect_in(alone, strict=True, files=files)
        self.assertEqual(raised.exception.code, 5)

    def perm_world(self, label, spec):
        """A shell with a chain of processes above it (a fake ps table), a HOME, a cwd with settings files and maybe a transcript."""
        top = self.path / label
        home, cwd = top / 'home', top / 'cwd'
        for d in (home / '.claude', cwd / '.claude'):
            d.mkdir(parents=True)
        for rel, text in spec.get('files', {}).items():
            (top / rel).parent.mkdir(parents=True, exist_ok=True)
            (top / rel).write_text(text)
        if 'transcript' in spec:
            where = home / '.claude/projects' / re.sub(r'[/.]', '-', str(cwd))
            where.mkdir(parents=True)
            (where / 'sess.jsonl').write_text(spec['transcript'])
        chain = spec['chain']
        rows = []
        for i, (comm, args) in enumerate(chain):
            pid = 'SELF' if i == 0 else str(5000 + i)
            ppid = str(5000 + i + 1) if i + 1 < len(chain) else spec.get('top_ppid', '1')
            rows.append(f'{pid}\t{ppid}\t{comm}\t{args}')
        (top / 'ps.tsv').write_text('\n'.join(rows) + '\n')
        env = {**self.env, 'HOME': str(home), 'PWD': str(cwd), 'HERDR_ENV': '1', 'FAKE_PS_TABLE': str(top / 'ps.tsv'), 'FAKE_PS_FAIL': spec.get('fail', ''),
               'FAKE': str(top / 'fake')}
        env.pop('CLAUDE_CODE_SESSION_ID', None)
        if 'transcript' in spec or spec.get('session'):
            env['CLAUDE_CODE_SESSION_ID'] = 'sess'
        return top, cwd, env

    def perm_run(self, label, spec, argv, nested=None):
        """argv in the world, or, with nested=(skills dir, snippet), the snippet run under set -euo pipefail with that dir's pair-core.sh sourced.
        (exit code, stdout, stderr), with the world's directory and bash's line numbers masked."""
        top, cwd, env = self.perm_world(label, spec)
        if nested:
            skills, snippet = nested
            harness = (f'set -euo pipefail\nskill_root={skills / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\n'
                       f'roles=(sidekick); store_dirs=(plans); slug_max=21\n. {skills / "pstack-pair/scripts/pair-core.sh"}\n{snippet}')
            argv = ['bash', '-c', harness]
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120)
        mask = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text.replace(str(top), '<T>')))
        return r.returncode, mask(r.stdout), mask(r.stderr)

    SHELL = ('bash', 'bash /x/pair.sh permission')

    def perm_cases(self):
        shell, claude = self.SHELL, lambda args: ('claude', f'claude {args}')
        settings = lambda mode: '{"permissions": {"defaultMode": "%s"}}' % mode
        local, plain = 'cwd/.claude/settings.local.json', 'cwd/.claude/settings.json'
        hlocal, hplain = 'home/.claude/settings.local.json', 'home/.claude/settings.json'
        cases = {
            'claude with skip-permissions': dict(chain=[shell, claude('--model x --dangerously-skip-permissions')]),
            'claude with --permission-mode acceptEdits': dict(chain=[shell, claude('--permission-mode acceptEdits')]),
            'claude with --permission-mode=plan': dict(chain=[shell, claude('--permission-mode=plan --model x')]),
            'claude with two --permission-mode': dict(chain=[shell, claude('--permission-mode a --permission-mode=b+c')]),
            'claude with --permission-mode and nothing after it': dict(chain=[shell, claude('--permission-mode ')]),
            'claude by a path': dict(chain=[shell, ('/usr/local/bin/claude', 'claude --permission-mode plan')]),
            'node running claude': dict(chain=[shell, ('node', 'node /lib/claude/cli.js --permission-mode plan')]),
            'bun running codex': dict(chain=[shell, ('bun', 'bun run codex -a never')]),
            'claude without a flag falls to the settings': dict(chain=[shell, claude('--model x')], files={hplain: settings('acceptEdits')}),
            'a shell that mentions a flag': dict(chain=[('bash', 'bash -c claude --dangerously-skip-permissions'), ('bash', 'bash')]),
            'codex bypass': dict(chain=[shell, ('codex', 'codex --dangerously-bypass-approvals-and-sandbox')]),
            'codex -a never': dict(chain=[shell, ('codex', 'codex -a never')]),
            'codex --ask-for-approval never': dict(chain=[shell, ('codex', 'codex --ask-for-approval never')]),
            'codex --ask-for-approval=never': dict(chain=[shell, ('codex', 'codex --ask-for-approval=never')]),
            'codex -a on-request': dict(chain=[shell, ('codex', 'codex -a on-request')]),
            'codex --ask-for-approval on-request': dict(chain=[shell, ('codex', 'codex --ask-for-approval on-request')]),
            'codex --ask-for-approval=on-request': dict(chain=[shell, ('codex', 'codex --ask-for-approval=on-request')]),
            'codex without a flag': dict(chain=[shell, ('codex', 'codex exec')]),
            'agent 11 shells up (found)': dict(chain=[shell, *[('bash', 'bash')] * 10, claude('--permission-mode plan')]),
            'agent 12 shells up (the depth limit)': dict(chain=[shell, *[('bash', 'bash')] * 11, claude('--permission-mode plan')]),
            'agent 13 shells up': dict(chain=[shell, *[('bash', 'bash')] * 12, claude('--permission-mode plan')]),
            'a chain that ends at pid 1': dict(chain=[shell, ('sh', 'sh')]),
            'a parent that ps does not know': dict(chain=[shell], top_ppid='777'),
            'ps cannot answer for args': dict(chain=[shell, claude('--permission-mode plan')], fail='args:SELF'),
            'ps cannot answer for comm': dict(chain=[shell, claude('--permission-mode plan')], fail='comm:SELF'),
            'ps cannot answer for the agent args': dict(chain=[shell, claude('--permission-mode plan')], fail='args:5001'),
            'a login shell comm': dict(chain=[('-bash', '-bash'), claude('--permission-mode plan')]),
            'no agent, a transcript with two modes': dict(chain=[shell], transcript='{"permissionMode":"plan"}\n{"x":1,"permissionMode":"acceptEdits"}\n'),
            'no agent, a transcript with no mode': dict(chain=[shell], transcript='{"x":1}\n', files={hplain: settings('dontAsk')}),
            'no agent, a transcript with an empty mode': dict(chain=[shell], transcript='{"permissionMode":""}\n'),
            'a session id and no transcript': dict(chain=[shell], session=True, files={hplain: settings('plan')}),
            'an agent mode beats the transcript': dict(chain=[shell, claude('--permission-mode plan')], transcript='{"permissionMode":"auto"}\n'),
            'cwd settings.local.json beats home settings.json': dict(chain=[shell], files={local: settings('plan'), hplain: settings('acceptEdits')}),
            'cwd settings.json beats home settings.local.json': dict(chain=[shell], files={plain: settings('dontAsk'), hlocal: settings('plan')}),
            'home settings.local.json beats home settings.json': dict(chain=[shell], files={hlocal: settings('plan'), hplain: settings('acceptEdits')}),
            'cwd settings.local.json beats cwd settings.json': dict(chain=[shell], files={local: settings('manual'), plain: settings('plan')}),
            'invalid JSON before a good file': dict(chain=[shell], files={local: '{not json', hplain: settings('plan')}),
            'invalid JSON alone': dict(chain=[shell], files={hplain: '{not json'}),
            'permissions that is not an object': dict(chain=[shell], files={local: '{"permissions": "x"}', hplain: settings('plan')}),
            'defaultMode null before a good file': dict(chain=[shell], files={local: '{"permissions": {"defaultMode": null}}', hplain: settings('plan')}),
            'defaultMode a number': dict(chain=[shell], files={local: '{"permissions": {"defaultMode": 5}}'}),
            'an empty settings file': dict(chain=[shell], files={local: '', hplain: settings('plan')}),
            'nothing anywhere': dict(chain=[shell]),
        }
        return cases

    def test_permission_matches_the_bash_at_the_phase_3_base(self):
        skills3 = self.phase3_skills()
        entries = {'pstack-pair': 'pair.sh', 'pstack-pair-guided': 'pair.sh', 'pstack-trio': 'pair.sh', 'pstack-kitchen': 'kitchen.sh'}
        mismatches, seen = {}, {}
        for i, (name, spec) in enumerate(self.perm_cases().items()):
            want = self.perm_run(f'w{i}', spec, [str(skills3 / 'pstack-pair/scripts/pair.sh'), 'permission'])
            seen[name] = want
            for j, (skill, script) in enumerate(entries.items() if i % 7 == 0 else [('pstack-pair', 'pair.sh')]):
                got = self.perm_run(f'g{i}-{j}', spec, [str(root / 'skills' / skill / 'scripts' / script), 'permission'])
                if got != want:
                    mismatches[f'{name} through {skill}'] = (got, want)
            want = self.perm_run(f'n{i}', spec, None, nested=(skills3, 'mode="$(detect_permission_mode)"\nprintf "%s\\n" "$mode"'))
            got = self.perm_run(f'm{i}', spec, None, nested=(root / 'skills', 'mode="$(detect_permission_mode)"\nprintf "%s\\n" "$mode"'))
            seen[f'{name} (nested)'] = want
            if got != want:
                mismatches[f'{name} inside $( )'] = (got, want)
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        self.assertEqual(seen['agent 11 shells up (found)'][1], 'plan\n')
        self.assertEqual(seen['agent 12 shells up (the depth limit)'][1], 'auto\n')
        self.assertEqual(seen['agent 13 shells up'][1], 'auto\n')
        self.assertEqual(seen['no agent, a transcript with two modes'][1], 'acceptEdits\n')
        self.assertEqual(seen['cwd settings.local.json beats home settings.json'][1], 'plan\n')
        # the commands that fail under errexit end the bash with the status of the failing command; inside $( ) it goes on
        self.assertEqual(seen['invalid JSON before a good file'][0], 5)
        self.assertEqual(seen['invalid JSON before a good file (nested)'][1], 'plan\n')
        self.assertEqual(seen['no agent, a transcript with no mode'][0], 1)
        self.assertEqual(seen['ps cannot answer for args'][0], 1)

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
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True, timeout=120).stdout.strip()
        git('init', '-q', '-b', 'main')
        shas = {}
        for rel, text in spec.get('repo_files', {}).items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        for name in () if spec.get('emptyrepo') else ('c1', 'c2', 'c3'):
            (repo / name).write_text(name)
            git('add', '-A')
            git('commit', '-q', '-m', name)
            shas[name] = git('rev-parse', 'HEAD')
        sub = {**shas, **{f'{k}s': v[:9] for k, v in shas.items()}, 'st': str(st), 'T': str(top), 'repo': str(repo)}
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
        for rel, stamp in spec.get('mtimes', {}).items():
            os.utime(st / rel, (stamp, stamp))
        for subcommand, script in spec.get('once', {}).items():
            (fake / 'once').mkdir(exist_ok=True)
            (fake / 'once' / subcommand).write_text(script)
            (fake / 'once' / subcommand).chmod(0o755)
        if spec.get('prompt_error') is not None:
            # a herdr whose `agent prompt` fails with exactly this stderr; every other call goes to the fake
            (top / 'prompt_error.txt').write_text(spec['prompt_error'])
            wrap = top / 'wrapprompt'
            wrap.mkdir()
            # prompt_error_exits: the agent leaves although the prompt reports the error
            if spec.get('prompt_error_exits'):
                (top / 'prompt_exits').write_text('')
            (wrap / 'herdr').write_text(f'#!/bin/sh\nif [ "$1 $2" = "agent prompt" ]; then cat "{top}/prompt_error.txt" >&2; [ ! -e "{top}/prompt_exits" ] || rm -f "$FAKE/agents/$3"; exit 1; fi\nexec {root / "tests/pstack-pair/bin/herdr"} "$@"\n')
            (wrap / 'herdr').chmod(0o755)
            env['PATH'] = f'{wrap}:{env["PATH"]}'
        if spec.get('hook'):
            (fake / 'hook').write_text(spec['hook'])
            (fake / 'hook').chmod(0o755)
        if spec.get('report_after'):
            (fake / 'report-after').write_text(fill(spec['report_after']) + '\n')
        env.update(spec.get('env', {}))
        if spec.get('clock'):
            # A clock that runs 100 s on every `date +%s`, and a sleep that returns at once: the bash's 15 s deadline is past at its second look, with
            # no waiting. The port's deadline and poll are set to 0 for the same effect.
            clock = top / 'clockbin'
            clock.mkdir()
            (clock / 'date').write_text('#!/bin/sh\nif [ "$*" = "+%s" ]; then n=$(cat "$FAKE/date-calls" 2>/dev/null || echo 0); echo $((n + 1)) >"$FAKE/date-calls"; echo $((1000 + 100 * n)); else exec /usr/bin/env -i PATH=/usr/bin:/bin date "$@"; fi\n')
            (clock / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            for each in ('date', 'sleep'):
                (clock / each).chmod(0o755)
            env['PATH'] = f'{clock}:{env["PATH"]}'
            env.update({'PAIR_EXIT_DEADLINE_S': '0', 'PAIR_EXIT_POLL_S': '0', 'PAIR_ESC_WAIT_S': '0'})
        if spec.get('busy'):
            (fake / 'busy').mkdir(exist_ok=True)
            (fake / 'busy' / 's1').write_text('')
        if spec.get('promptfail'):
            (fake / 'prompt-fail').write_text('')
        for rel, (text, mode) in spec.get('topfiles', {}).items():
            (top / rel).parent.mkdir(parents=True, exist_ok=True)
            (top / rel).write_text(text)
            (top / rel).chmod(mode)
            self.addCleanup((top / rel).chmod, 0o644)
        for rel, text in spec.get('fake', {}).items():
            (fake / rel).parent.mkdir(parents=True, exist_ok=True)
            (fake / rel).write_text(text)
        (st / 'events.tsv').write_text('2026-01-01T00:00:00Z\t1\tmaster\tinit\t-\tbefore\n')
        return top, st, env, fill

    def ab_run(self, label, skills, skill, spec):
        top, st, env, fill = self.ab_world(label, spec)
        script = 'kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh'
        run = lambda: subprocess.run([str(skills / skill / 'scripts' / script), *(fill(a) for a in spec['args'])], cwd=top / 'repo', env=env, timeout=120,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT if spec.get('merged') else subprocess.PIPE, text=True, errors='surrogateescape', check=False)
        r = run()
        second = None
        if spec.get('twice'):
            # The same command again in the same store, once the detached job the first one started has written its rc (a bounded poll: the job is
            # a real process, and the second run reads what it left).
            for _ in range(400):
                if list(st.glob('steps/jobs/*/rc')):
                    break
                time.sleep(0.05)  # kitchen-allow: test-sleep (a bounded wait for a detached job's rc file)
            second = run()
        # The base run and the port run are two worlds, built in two temp directories at two moments, so only what that explains is rewritten:
        # <T> is the world's directory (it appears in paths); <TS> the ISO stamps the store writes (.dispatch.at, .sent.at); <EPOCH> the epoch
        # seconds it writes (.sent.epoch, steps and gates rows); <HM> the HH:MM that starts a progress line `step`. Nothing else.
        norm = lambda text: re.sub(r'(exit [0-9]+ )[0-9.]+s', r'\1<S>s', re.sub(r'(?m)^[0-9]{2}:[0-9]{2} step ', '<HM> step ', re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text.replace(str(top), '<T>')))))
        files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
        events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text(errors='surrogateescape').splitlines()]
        calls = norm((top / 'fake/calls.log').read_text()) if (top / 'fake/calls.log').exists() else ''
        out = {'rc': r.returncode, 'stdout': norm(r.stdout), 'stderr': norm(r.stderr or ''), 'files': files, 'events': events, 'calls': calls}
        if second is not None:
            out['second'] = (second.returncode, norm(second.stdout), norm(second.stderr or ''))
        return out

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
        # Masked: bash's own error text (an unknown value for `--every`, a `[` that finds no integer) names the file and line of the statement
        # that failed. For the oracle that is e6e83e3's pair-core.sh and its line, which moved by the time of HEAD; Python names cmd_wait's
        # line (checked never to be 0 below and exactly in test_wait_finish_opts_match_bash).
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
        for name, spec in self.wait_ab_cases().items():
            got, want = self.wait_ab_compare(name, spec)
            self.assertNotIn('line 0:', got['stderr'], name)
            for side in (got, want):
                side['stderr'] = norm_core(side['stderr'])
                side['stdout'] = self.mask_minutes(re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', side['stdout']))
                side['calls'] = self.collapse_polls(side['calls'])
            results[name] = got
            if name == 'a settle hold that is not an integer':
                # The port hard-codes bash 5.3's words; bash 5.2 and earlier say "integer expression expected". Pin the port's text exactly, accept
                # either on the bash side, and compare everything else as it is.
                self.assertEqual(got['stderr'], '<core>: line N: [: 1.5: integer expected\n')
                self.assertRegex(want['stderr'], r'\A<core>: line N: \[: 1\.5: (integer expected|integer expression expected)\n\Z')
                want['stderr'] = want['stderr'].replace('integer expression expected', 'integer expected')
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
        # Masked: bash's own error text (an unknown value for `--every`, a `[` that finds no integer) names the file and line of the statement
        # that failed. For the oracle that is e6e83e3's pair-core.sh and its line, which moved by the time of HEAD; Python names cmd_wait's
        # line (checked never to be 0 below and exactly in test_wait_finish_opts_match_bash).
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
        outcomes = {}
        for name, spec in cases.items():
            got, want = self.wait_ab_compare(name, spec, skill)
            self.assertNotIn('line 0:', got['stderr'], name)
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

    def wait_signalled(self, skills, number, group, skill='pstack-pair', ignore_int=False, timeout_ms=20000, hold='wait', argv=None, world=None):
        """Run `pair.sh wait`, signal it while it sits in herdr's wait (the fake herdr's once hook holds it there on a pipe), and give
        (exit status, stdout, stderr). Nothing here can hang: each wait is bounded, the hook is released in a finally, and the
        whole process group is killed if the command outlives the bound."""
        top, st, env, fill = self.ab_world(f'sig-{len(list(self.path.iterdir()))}', world or {'agents': {'s1': 'working claude'}, 'pair': {'sent': {'file': '{st}/briefs/001-x.md', 'epoch': 1}}})
        ready, release = top / 'ready', top / 'release'
        os.mkfifo(ready)
        os.mkfifo(release)
        # Held read-write from the start: the hook's open of the pipe then never waits for a writer, and what the test writes after the
        # signal reaches the hook however late it gets there.
        release_fd = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(lambda: os.close(release_fd) if release_fd is not None else None)
        hook = top / 'fake/once' / hold
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f'#!/bin/sh\necho in >"{ready}"\nread x <"{release}"\n')
        hook.chmod(0o755)

        def default_signals():
            # a gate that runs as a background job hands its children SIGINT ignored; the signal under test must be deliverable
            for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(each, signal.SIG_IGN if ignore_int and each == signal.SIGINT else signal.SIG_DFL)

        script = 'kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh'
        before = self.store_snapshot(st)
        proc = subprocess.Popen([str(skills / skill / 'scripts' / script), *(argv(st) if argv else ['wait', str(st), '--timeout', str(timeout_ms)])], cwd=top / 'repo', env=env, text=True,
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
        # A short timeout: the base's subshell outlives the signalled shell and runs to the end of its wait, which the test then waits for.
        got = self.wait_signalled(root / 'skills', signal.SIGTERM, False, 'pstack-kitchen', timeout_ms=3000)
        want = self.wait_signalled(self.base_skills(), signal.SIGTERM, False, 'pstack-kitchen', timeout_ms=3000)
        self.assertEqual(got[:3], want[:3])
        self.assertEqual(got, (-signal.SIGTERM, '', '', True))

    KITCHEN_TOML = 'version = 1\n\n[profile.app]\npaths = ["src/**", "docs/**", "extra/**"]\nfast = ["true"]\n\n[escalate]\npaths = ["contracts/**"]\n'

    def queue_ab_cases(self):
        working = {'s1': 'working claude'}
        brief_text = lambda *lines, headers='playbook: investigation\n': '# Brief 003-z\n\n' + headers + '\n## Scope\n\nmay write:\n' + ''.join(f'- {l}\n' for l in (lines or ('src/a.py',))) + '\nmust not write:\n- lib/**\n'
        plan_files = {'plans/001-x.md': '# Plan\n\nscale: small\n', 'reviews/001-x.md': '# Review\n\nverdict: agreed\n'}
        feature = 'playbook: feature\nplan: {st}/plans/001-x.md\n'
        base = dict(args=['queue', '{st}', '{st}/briefs/003-z.md'], agents=working, files={'briefs/003-z.md': brief_text()}, repo_files={'src/a.py': 'x', 'docs/guide.md': 'x'})
        with_files = lambda **extra: {**base, 'files': {'briefs/003-z.md': brief_text(), **extra.pop('files', {})}, **extra}
        return {
            'clear a queue': dict(args=['queue', '{st}', '--clear'], files={'queue': '{st}/briefs/002-y.md\n'}),
            'clear nothing': dict(args=['queue', '{st}', '--clear']),
            'queue a brief': base,
            'queue the same brief again': with_files(files={'queue': '{st}/briefs/003-z.md\n'}),
            'the queue is held by another brief': with_files(files={'queue': '{st}/briefs/002-y.md\n'}),
            'replace the queue': with_files(args=['queue', '{st}', '{st}/briefs/003-z.md', '--replace'], files={'queue': '{st}/briefs/002-y.md\n'}),
            'an unknown option': with_files(args=['queue', '{st}', '{st}/briefs/003-z.md', '--force']),
            'an empty third argument': with_files(args=['queue', '{st}', '{st}/briefs/003-z.md', '']),
            'a missing brief': with_files(args=['queue', '{st}', '{st}/briefs/009-none.md']),
            'a relative brief path': with_files(args=['queue', '{st}', '../st/briefs/003-z.md']),
            'a symlinked brief': with_files(args=['queue', '{st}', '{T}/link.md'], links={'link.md': '{st}/briefs/003-z.md'}),
            'a brief path with a trailing slash': with_files(args=['queue', '{st}', '{st}/briefs/003-z.md/']),
            'too few arguments': dict(args=['queue', '{st}']),
            'no pair.json': with_files(nopair=True),
            'a paused store': with_files(files={'paused': 'lunch\n'}),
            'an unfilled brief': with_files(files={'briefs/003-z.md': brief_text('{{{{path}}}}')}),  # ab_world formats file text: doubled braces stay literal
            'a scope that lists paths on one line': with_files(files={'briefs/003-z.md': brief_text('src/a.py, src/b.py')}),
            'a scope that joins paths with and': with_files(files={'briefs/003-z.md': brief_text('src/a.py and docs/guide.md')}),
            'a scope entry that matches nothing': with_files(files={'briefs/003-z.md': brief_text('src/a.py', 'nodir/new.py', 'nodir/**')}),
            'a scope with globs and a directory': with_files(files={'briefs/003-z.md': brief_text('src/', 'docs/**', '**/guide.md', '*.nope')}),
            'a feature brief with no plan': with_files(files={'briefs/003-z.md': brief_text(headers='playbook: feature\n')}),
            'a feature brief with plan none': with_files(files={'briefs/003-z.md': brief_text(headers='playbook: feature\nplan: none\n')}),
            'a feature brief whose plan is missing': with_files(files={'briefs/003-z.md': brief_text(headers='playbook: feature\nplan: {st}/plans/009-none.md\n')}),
            'a feature brief whose plan has no review': with_files(files={'briefs/003-z.md': brief_text(headers=feature), 'plans/001-x.md': '# Plan\n\nscale: small\n'}),
            'a feature brief whose review is not agreed': with_files(files={'briefs/003-z.md': brief_text(headers=feature), 'plans/001-x.md': '# Plan\n', 'reviews/001-x.md': '# R\n\nverdict: revise\n'}),
            'a feature brief with an agreed plan': with_files(files={'briefs/003-z.md': brief_text(headers=feature), **plan_files}),
            'the sidekick is idle': with_files(agents={'s1': 'idle claude'}),
            'the sidekick is done': with_files(agents={'s1': 'done claude'}),
            'the sidekick is blocked': with_files(agents={'s1': 'blocked claude'}),
            'the sidekick is absent': with_files(agents={}),
            'a null sidekick': with_files(pair={'sidekick': None}),
        }

    def queue_ab_run(self, cases, skill):
        mismatches, outcomes = {}, {}
        # Masked: bash's own error text (an unknown value for `--every`, a `[` that finds no integer) names the file and line of the statement
        # that failed. For the oracle that is e6e83e3's pair-core.sh and its line, which moved by the time of HEAD; Python names cmd_wait's
        # line (checked never to be 0 below and exactly in test_wait_finish_opts_match_bash).
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
        for name, spec in cases.items():
            got, want = self.wait_ab_compare(name, spec, skill)
            self.assertNotIn('line 0:', got['stderr'], name)
            for side in (got, want):
                side['stderr'] = norm_core(side['stderr'])
            outcomes[name] = got
            if got != want:
                mismatches[name] = {k: (got[k], want[k]) for k in got if got[k] != want[k]}
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        return outcomes

    def test_queue_ab_matches_the_bash_at_the_base_commit(self):
        outcomes = self.queue_ab_run(self.queue_ab_cases(), 'pstack-pair')
        self.assertEqual(outcomes['queue a brief']['rc'], 0)
        self.assertIn('queued ', outcomes['queue a brief']['stdout'])
        self.assertEqual(outcomes['an unfilled brief']['rc'], 1)
        self.assertIn('still has unfilled placeholders', outcomes['an unfilled brief']['stderr'])
        self.assertEqual(outcomes['the queue is held by another brief']['rc'], 5)
        self.assertEqual(outcomes['the sidekick is blocked']['rc'], 3)
        self.assertEqual(outcomes['a feature brief with no plan']['rc'], 6)
        self.assertIn('warning: Scope entry "nodir/new.py"', outcomes['a scope entry that matches nothing']['stderr'])

    def test_queue_ab_matches_the_bash_in_the_other_variants(self):
        cases = self.queue_ab_cases()
        keep = ('queue a brief', 'the queue is held by another brief', 'a feature brief with an agreed plan', 'a feature brief whose plan has no review', 'a paused store', 'the sidekick is idle')
        for skill in ('pstack-pair-guided', 'pstack-trio'):
            subset = {name: cases[name] for name in keep}
            if skill == 'pstack-pair-guided':
                # the guided gate: scale and approval
                for scale, approval in (('small', ''), ('medium', ''), ('medium', 'posted'), ('large', 'posted'), ('large', 'human'), ('', 'human')):
                    spec = dict(cases['a feature brief with an agreed plan'])
                    spec['files'] = {**spec['files'], 'plans/001-x.md': f'# Plan\n\n{"scale: " + scale + chr(10) if scale else ""}',
                                     'reviews/001-x.md': f'# R\n\nverdict: agreed\n{"approval: " + approval + chr(10) if approval else ""}'}
                    subset[f'guided scale {scale or "none"} approval {approval or "none"}'] = spec
            else:
                spec = dict(cases['a feature brief with an agreed plan'])
                subset['trio with advice'] = {**spec, 'files': {**spec['files'], 'advice/001-x.md': '# Advice\n'}}
            self.queue_ab_run(subset, skill)

    def test_queue_ab_kitchen_runs_classify_and_its_gates_first(self):
        cases = self.queue_ab_cases()
        toml = {'.agents/kitchen.toml': self.KITCHEN_TOML}
        plan_files = {'plans/001-x.md': '# Plan\n', 'reviews/001-x.md': '# R\n\nverdict: agreed\n'}
        picked = {name: {**cases[name], 'repo_files': {**cases[name].get('repo_files', {}), **toml}} for name in (
            'queue a brief', 'queue the same brief again', 'the queue is held by another brief', 'replace the queue', 'a paused store', 'an unfilled brief',
            'a scope that lists paths on one line', 'a scope entry that matches nothing', 'the sidekick is idle', 'a feature brief with an agreed plan', 'a feature brief with no plan')}
        brief = lambda headers: {**cases['queue a brief']['files'], 'briefs/003-z.md': cases['queue a brief']['files']['briefs/003-z.md'].replace('playbook: investigation\n', headers)}
        base = {**cases['queue a brief'], 'repo_files': toml}
        picked['an escalated brief with no advice'] = {**base, 'files': {**brief('playbook: feature\nplan: {st}/plans/001-x.md\nrisk: escalated (a contract)\n'), **plan_files}}
        picked['an escalated brief with advice'] = {**base, 'files': {**brief('playbook: feature\nplan: {st}/plans/001-x.md\nrisk: escalated (a contract)\n'), **plan_files, 'advice/001-x.md': '# A\n'}}
        picked['a routine brief with plan none'] = {**base, 'files': brief('playbook: feature\nplan: none\nrisk: routine\n')}
        picked['a routine brief with a plan that is not agreed'] = {**base, 'files': {**brief('playbook: feature\nplan: {st}/plans/001-x.md\nrisk: routine\n'), 'plans/001-x.md': '# P\n', 'reviews/001-x.md': '# R\n\nverdict: revise\n'}}
        picked['a landing brief skips classify'] = {**base, 'files': brief('playbook: opening-a-pr\n')}
        picked['a landing brief by header'] = {**base, 'files': brief('playbook: investigation\nlanding: yes\n')}
        picked['a brief with nothing under Scope'] = {**base, 'files': {'briefs/003-z.md': '# Brief\n\nplaybook: investigation\n\n## Scope\n\nmay write:\n'}}
        outcomes = self.queue_ab_run(picked, 'pstack-kitchen')
        self.assertEqual(outcomes['an escalated brief with advice']['rc'], 0)
        self.assertEqual(outcomes['an escalated brief with no advice']['rc'], 6)
        self.assertEqual(outcomes['a routine brief with plan none']['rc'], 0)
        self.assertEqual(outcomes['a routine brief with a plan that is not agreed']['rc'], 6)

    STEP_NOW = 1_800_000_123

    def step_both(self, name, args, **spec):
        """cmd_step in bash (the e6e83e3 core, with `date` fixed at STEP_NOW) and step() in Python (given the same instant) on identical
        worlds: (return code, stdout, stderr, store files, events added)."""
        results = []
        for label in ('bash', 'py'):
            top, st, env, fill = self.ab_world(f'{name}-{label}', {'agents': {'s1': 'working claude'}, **spec})
            argv = [fill(a) for a in args]
            ctx = Context('pstack-pair', str(root / 'skills/pstack-pair'), str(top / 'state'), 'Pair', ('sidekick',), ('plans',), 21, '')
            if label == 'bash':
                fake = self.path / f'fakedate-{self.STEP_NOW}'
                fake.mkdir(exist_ok=True)
                (fake / 'date').write_text(f'#!/bin/sh\ncase "$1" in +%s) echo {self.STEP_NOW} ;; +%H:%M) exec {shutil.which("date")} -d @{self.STEP_NOW} +%H:%M ;; *) exec {shutil.which("date")} "$@" ;; esac\n')
                (fake / 'date').chmod(0o755)
                script = (f'set -euo pipefail\nskill_root={self.base_skills() / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\nroles=(sidekick); store_dirs=(plans); slug_max=21\n'
                          f'. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\ncmd_step "$@"')
                r = subprocess.run(['bash', '-c', script, '_', *argv], env={**env, 'PATH': f'{fake}:{env["PATH"]}'}, cwd=top / 'repo', capture_output=True, text=True, check=False, timeout=120)
                rc, so, se = r.returncode, r.stdout, r.stderr
            else:
                so_io, se_io = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(so_io), contextlib.redirect_stderr(se_io):
                    try:
                        rc = step.step(ctx, argv, now=self.STEP_NOW)
                    except Die as e:
                        rc = e.code
                        se_io.write(f'pair.sh: {e.message}\n')
                    except Exit as e:
                        rc = e.code
                        se_io.write(e.stderr)
                so, se = so_io.getvalue(), se_io.getvalue()
            # The clock is fixed for both runs (a fake `date` for the bash, `now=` for Python), so times are compared as they are; the two runs
            # of one world use different temp directories, and <T> stands for that directory in paths. Nothing else is rewritten here.
            norm = lambda text: text.replace(str(top), '<T>')
            # Only the message for `--resolves` with no value is masked, and only its file and line: the bash names the line of its `$2` in the
            # cmd_step body, which is gone from HEAD (the body moved into Python and cmd_step is a one-line shim), and the oracle's path is
            # not the working tree's. The line Python names is checked below against the shim's own line.
            mask = lambda text: re.sub(r'\S*pair-core\.sh: line [0-9]+: \$2: unbound variable', '<core>: line N: $2: unbound variable', text)
            if label == 'py':
                self.step_python_stderr[name] = se
            files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
            events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()[1:]]
            results.append((rc, norm(so), mask(norm(se)), files, events))
        self.assertEqual(results[0], results[1], name)
        return results[1]

    def test_step_ab_matches_the_bash_at_the_base_commit(self):
        self.step_python_stderr = {}
        sha = '{c2}'
        rows = '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n'
        note = {'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}
        steer = {'pair': {'notice_steers': ['{st}/steers/001-x-s1.md']}, 'files': {'steers/001-x-s1.md': '# S\n'}}
        out = {}
        cases = {
            'no arguments': dict(args=['step']), 'two arguments': dict(args=['step', '{st}', sha][1:]),
            'the first step': dict(args=['{st}', sha, 'did a thing']),
            'a later step': dict(args=['{st}', sha, 'did more'], files={'steps/001-x.tsv': rows}),
            'an empty steps file': dict(args=['{st}', sha, 'did more'], files={'steps/001-x.tsv': ''}),
            'a steps file of blank lines only': dict(args=['{st}', sha, 'did more'], files={'steps/001-x.tsv': '\n\n'}),
            'a steps file with blank lines': dict(args=['{st}', sha, 'did more'], files={'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n\n\n2\t{c2}\t6\t\ttwo\n\n'}),
            'a summary with a tab and a newline': dict(args=['{st}', sha, 'a\tb\nc\n']),
            'a summary with a percent and a backslash': dict(args=['{st}', sha, '100% \\n done \u00e9']),
            'a short sha': dict(args=['{st}', '{c2s}', 'short']),
            'HEAD': dict(args=['{st}', 'HEAD', 'head']),
            'a ref name': dict(args=['{st}', 'main', 'branch']),
            'a sha that is not a commit': dict(args=['{st}', 'f' * 40, 'nope']),
            'an empty sha': dict(args=['{st}', '', 'nope']),
            'no brief dispatched': dict(args=['{st}', sha, 'x'], pair={'dispatch': None}),
            'no cwd': dict(args=['{st}', sha, 'x'], pair={'cwd': None}),
            'an unknown option': dict(args=['{st}', sha, 'x', '--force']),
            'resolves a published note': dict(args=['{st}', sha, 'x', '--resolves', 'n1'], files=note),
            'resolves two published notes': dict(args=['{st}', sha, 'x', '--resolves', 'n1,n2'], files={**note, 'notes/001-x-n2.md': '# N\n'}),
            'resolves an unpublished note': dict(args=['{st}', sha, 'x', '--resolves', 'n3'], files=note),
            'resolves with a trailing comma': dict(args=['{st}', sha, 'x', '--resolves', 'n1,']),
            'resolves a bad id': dict(args=['{st}', sha, 'x', '--resolves', 'x1']),
            'resolves two ids with a space': dict(args=['{st}', sha, 'x', '--resolves', 'n1 n2'], files=note),
            'resolves nothing': dict(args=['{st}', sha, 'x', '--resolves', '']),
            'resolves with no value': dict(args=['{st}', sha, 'x', '--resolves']),
            'an empty fourth argument': dict(args=['{st}', sha, 'x', '']),
            'no pair.json': dict(args=['{st}', sha, 'x'], nopair=True),
            'a paused store': dict(args=['{st}', sha, 'x'], files={'paused': 'lunch\n'}),
            'a steer waiting for its ack': dict(args=['{st}', sha, 'x'], **steer),
            'a paused store with a steer': dict(args=['{st}', sha, 'x'], pair=steer['pair'], files={**steer['files'], 'paused': 'lunch\n'}),
            'a steer already acknowledged': dict(args=['{st}', sha, 'x'], pair=steer['pair'], files={**steer['files'], 'progress/001-x.md': '10:00 steer s1 applied: ok\n'}),
            'an existing progress log': dict(args=['{st}', sha, 'x'], files={'progress/001-x.md': '10:00 first\n'}),
            'a progress log without a final newline': dict(args=['{st}', sha, 'x'], files={'progress/001-x.md': '10:00 first'}),
        }
        for name, case in cases.items():
            case = dict(case)
            args = case.pop('args')
            if name == 'two arguments':
                args = ['{st}', '{c2}']
            if name == 'no arguments':
                args = []
            out[name] = self.step_both(name.replace(' ', '-'), args, **case)
        self.assertEqual(out['the first step'][0], 0)
        self.assertEqual(out['a later step'][3]['steps/001-x.tsv'].count('\n'), 3)
        self.assertEqual(out['resolves an unpublished note'][0], 1)
        self.assertEqual(out['no brief dispatched'][0], 1)
        # The missing `--resolves` value: bash's format, HEAD's pair-core.sh, and the line of the cmd_step shim, never 0.
        core = scripts / 'pair-core.sh'
        shim = next(n for n, line in enumerate(core.read_text().splitlines(), 1) if line.startswith('cmd_step() {'))
        stderr = self.step_python_stderr['resolves-with-no-value']
        self.assertEqual(stderr.replace(str(self.path), '<P>').replace(str(core), '<core>'), f'<core>: line {shim}: $2: unbound variable\n')
        for text in self.step_python_stderr.values():
            self.assertNotIn('line 0:', text)

    def test_step_ab_kitchen_runs_its_gates_then_the_core_step(self):
        # kitchen.sh step classifies the commit, runs the touched profiles' fast gates and the policy check, and only then calls the core
        # step, which is the Python shim. The profile below covers the files the world's commits touch, so the gates run and pass.
        toml = 'version = 1\n\n[profile.app]\npaths = ["c*", "src/**"]\nfast = ["true"]\n'
        env = {'KITCHEN_STEP_POLL_S': '0.2', 'PSTACK_KITCHEN_SLOT_POLL_S': '0.2', 'PSTACK_KITCHEN_LOAD': '1 8'}
        base = dict(repo_files={'.agents/kitchen.toml': toml}, env=env, pair={'heads': {'001-x': '{c1}'}})
        cases = {
            'the first step': dict(base, args=['step', '{st}', '{c2}', 'did a thing']),
            'a step that resolves a published note': dict(base, args=['step', '{st}', '{c2}', 'fix', '--resolves', 'n1'], files={'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'a paused store': dict(base, args=['step', '{st}', '{c2}', 'x'], files={'paused': 'lunch\n'}),
            'a step that resolves an unpublished note': dict(base, args=['step', '{st}', '{c2}', 'x', '--resolves', 'n9']),
            'a sha that is not a commit': dict(base, args=['step', '{st}', 'f' * 40, 'x']),
        }
        mismatches, outcomes = {}, {}
        for name, spec in cases.items():
            got, want = self.wait_ab_compare(name, spec, 'pstack-kitchen')
            # Left out of the comparison: the gate job's directory under steps/jobs holds the log paths and run times of a detached process.
            # Stderr is compared as it is: none of these cases prints a line number or the path of pair-core.sh (no kitchen case has a
            # `--resolves` with no value, the one message that does).
            for side in (got, want):
                side['files'] = {k: v for k, v in side['files'].items() if not k.startswith('steps/jobs/')}
            outcomes[name] = got
            if got != want:
                mismatches[name] = {k: (got[k], want[k]) for k in got if got[k] != want[k]}
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        self.assertIn('gates: pass (app)', outcomes['the first step']['stdout'])
        self.assertIn('step 1 recorded', outcomes['the first step']['stdout'])
        self.assertIn('gates.tsv', ' '.join(outcomes['the first step']['files']))
        self.assertEqual(outcomes['a step that resolves an unpublished note']['rc'], 1)

    def test_step_ab_a_missing_resolves_value_names_the_shim_line_through_pair_sh(self):
        core = scripts / 'pair-core.sh'
        shim = next(n for n, line in enumerate(core.read_text().splitlines(), 1) if line.startswith('cmd_step() {'))
        got = self.ab_run('shim-line', root / 'skills', 'pstack-pair', dict(args=['step', '{st}', '{c2}', 'x', '--resolves'], agents={'s1': 'working claude'}))
        self.assertEqual((got['rc'], got['stdout'], got['stderr']), (1, '', f'{core}: line {shim}: $2: unbound variable\n'))

    def test_step_ab_through_every_variants_script(self):
        # The shim, end to end: `step` through pair.sh, guided's and trio's pair.sh at e6e83e3 and at HEAD. The clock is masked here (the
        # fixed-clock test above compares it); kitchen.sh's step runs its gates first and has its own tests.
        sha = '{c2}'
        cases = {
            'a step': dict(args=['step', '{st}', sha, 'did a thing']),
            'a step that resolves a note': dict(args=['step', '{st}', sha, 'fix', '--resolves', 'n1'], files={'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'a paused store with a steer': dict(args=['step', '{st}', sha, 'x'], pair={'notice_steers': ['{st}/steers/001-x-s1.md']}, files={'steers/001-x-s1.md': '# S\n', 'paused': 'lunch\n'}),
            'a sha that is not a commit': dict(args=['step', '{st}', 'f' * 40, 'x']),
            'an empty steps file': dict(args=['step', '{st}', sha, 'x'], files={'steps/001-x.tsv': ''}),
            'no brief dispatched': dict(args=['step', '{st}', sha, 'x'], pair={'dispatch': None}),
        }
        for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio'):
            self.queue_ab_run(cases, skill)

    def test_step_ab_unreadable_steps_file(self):
        if os.geteuid() == 0:
            self.skipTest('file modes do not stop root')
        results = []
        for label in ('bash', 'py'):
            # A world of its own for each side, so that what one leaves behind cannot show in the other.
            top, st, env, fill = self.ab_world(f'unreadable-{label}', {'agents': {'s1': 'working claude'}, 'files': {'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n'}})
            (st / 'steps/001-x.tsv').chmod(0o000)
            self.addCleanup(lambda f=st / 'steps/001-x.tsv': f.chmod(0o644))
            ctx = Context('pstack-pair', str(root / 'skills/pstack-pair'), str(top / 'state'), 'Pair', ('sidekick',), ('plans',), 21, '')
            if label == 'bash':
                script = (f'set -euo pipefail\nskill_root={self.base_skills() / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\nroles=(sidekick); store_dirs=(plans); slug_max=21\n'
                          f'. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\ncmd_step "$@"')
                r = subprocess.run(['bash', '-c', script, '_', str(st), fill('{c2}'), 'x'], env=env, cwd=top / 'repo', capture_output=True, text=True, check=False, timeout=120)
                rc, out, err = r.returncode, r.stdout, r.stderr
            else:
                so, se = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
                    try:
                        rc = step.step(ctx, [str(st), fill('{c2}'), 'x'])
                    except Exit as e:
                        rc = e.code
                        se.write(e.stderr)
                out, err = so.getvalue(), se.getvalue()
            (st / 'steps/001-x.tsv').chmod(0o644)
            files = {str(f.relative_to(st)): f.read_text(errors='replace').replace(str(top), '<T>') for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
            events = [l.split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()[1:]]
            results.append((rc, out, err.replace(str(top), '<T>'), files, events))
            (st / 'steps/001-x.tsv').chmod(0o000)
        self.assertEqual(results[0][:3], (2, '', 'grep: <T>/st/steps/001-x.tsv: Permission denied\n'), results[0])
        self.assertEqual(results[1], results[0])

    SEND_NOW = 1_800_000_321

    def send_both(self, name, snippet, call, mask_ts=True, **spec):
        """A helper of the send path in bash (the e6e83e3 core, `date` fixed at SEND_NOW) and in Python (given the same instant) on identical
        worlds: (return code, stdout, stderr, store files, events added, herdr calls)."""
        results = []
        for label in ('bash', 'py'):
            top, st, env, fill = self.ab_world(f'{name}-{label}', {'agents': {'s1': 'idle claude'}, **spec})
            env = {**env, 'PAIR_SUBMIT_CHECK_S': '0'}
            ctx = Context('pstack-pair', str(root / 'skills/pstack-pair'), str(top / 'state'), 'Pair', ('sidekick',), ('plans',), 21, '')
            if label == 'bash':
                fake = self.path / f'fakedate-{self.SEND_NOW}'
                fake.mkdir(exist_ok=True)
                (fake / 'date').write_text(f'#!/bin/sh\ncase "$1" in +%s) echo {self.SEND_NOW} ;; -u) shift; exec {shutil.which("date")} -u -d @{self.SEND_NOW} "$@" ;; *) exec {shutil.which("date")} "$@" ;; esac\n')
                (fake / 'date').chmod(0o755)
                script = (f'set -euo pipefail\nskill_root={self.base_skills() / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\nroles=(sidekick); store_dirs=(plans); slug_max=21\n'
                          f'. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\nstore={shlex.quote(str(st))}\n{fill(snippet)}')
                r = subprocess.run(['bash', '-c', script], env={**env, 'PATH': f'{fake}:{env["PATH"]}'}, cwd=top / 'repo', capture_output=True, text=True, check=False, timeout=120)
                rc, so, se = r.returncode, r.stdout, r.stderr
            else:
                so_io, se_io = io.StringIO(), io.StringIO()
                # The Python half runs in this process: every subprocess it starts gets the timeout the bash half has, so a hung fake herdr
                # fails the test instead of the gate.
                real_run = subprocess.run
                bounded = lambda *a, **k: real_run(*a, **{'timeout': 120, **k})
                with mock.patch.dict(os.environ, env), mock.patch.object(subprocess, 'run', bounded), contextlib.redirect_stdout(so_io), contextlib.redirect_stderr(se_io):
                    try:
                        out = call(ctx, str(st), fill)
                        rc = 0
                        so_io.write(out or '')
                    except Die as e:
                        rc = e.code
                        se_io.write(f'pair.sh: {e.message}\n')
                so, se = so_io.getvalue(), se_io.getvalue()
            norm = lambda text: (re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text) if mask_ts else text).replace(str(top), '<T>')
            files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
            events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()[1:]]
            calls = norm((top / 'fake/calls.log').read_text()) if (top / 'fake/calls.log').exists() else ''
            results.append((rc, norm(so), norm(se), files, events, calls))
        self.assertEqual(results[0], results[1], name)
        return results[1]

    def test_send_helpers_prune_pending_and_record_sent_match_bash(self):
        done = '# R\n\nstatus: done\nhead: {c3}\n'
        pending = lambda *names: {'pending': [f'{{st}}/briefs/{n}' for n in names]}
        for label, spec in {
            'no pending key': dict(pair={}),
            'an empty list': dict(pair=pending()),
            'a null list': dict(pair={'pending': None}),
            'one owed brief': dict(pair=pending('001-x.md')),
            'a brief whose report exists': dict(pair=pending('001-x.md', '002-y.md'), files={'reports/001-x.md': done}),
            'a brief with a review': dict(pair=pending('001-x.md', '002-y.md'), files={'reviews/002-notes.md': '# R\n'}),
            'every brief answered': dict(pair=pending('001-x.md'), files={'reports/001-x.md': done}),
            'an answer brief': dict(pair=pending('001-x-a2.md'), files={'reports/001-x.md': done}),
            'a duplicate and an empty entry': dict(pair={'pending': ['{st}/briefs/001-x.md', '{st}/briefs/001-x.md', '']}),
        }.items():
            self.send_both('prune ' + label.replace(' ', '-'), 'prune_pending "$store"', lambda ctx, st, fill: send.prune_pending(st), **spec)
        for kind in ('PLAN', 'ANSWER', 'BRIEF'):
            self.send_both(f'sent-{kind}', f'record_sent "$store" {{st}}/plans/001-x.md {kind}', lambda ctx, st, fill, kind=kind: send.record_sent(st, fill('{st}/plans/001-x.md'), kind, self.SEND_NOW), mask_ts=False, pair={'sent': {'file': 'old', 'epoch': 1}})
        self.send_both('sent-none', 'record_sent "$store" /f PLAN', lambda ctx, st, fill: send.record_sent(st, '/f', 'PLAN', self.SEND_NOW), mask_ts=False, pair={'sent': None})
        got = self.send_both('sent-at-is-the-fixed-instant', 'record_sent "$store" /f PLAN', lambda ctx, st, fill: send.record_sent(st, '/f', 'PLAN', self.SEND_NOW), mask_ts=False)
        self.assertIn('"at": "2027-01-15T08:05:21Z"', got[3]['pair.json'])

    def test_take_queued_matches_bash(self):
        # take_queued runs in a command substitution in the bash, so a failure inside it (a brief that is gone, a held lock) prints its message
        # and reads as an empty queue: both halves call it that way.
        snippet = 'b="$(take_queued "$store")" && echo "got=$b" || echo "got-nothing"'
        call = lambda ctx, st, fill: (lambda b: f'got={b}\n' if b is not None else 'got-nothing\n')(dispatch.take_queued(st, self.SEND_NOW))
        cases = {
            'a queued brief': dict(files={'queue': '{st}/briefs/002-y.md\n'}),
            'a queued brief with trailing blank lines': dict(files={'queue': '{st}/briefs/002-y.md\n\n\n'}),
            'a queued brief that is gone': dict(files={'queue': '{st}/briefs/009-gone.md\n'}),
            'an empty queue file': dict(files={'queue': ''}),
            'no queue': dict(),
            'a queued answered brief': dict(files={'queue': '{st}/briefs/002-y.md\n', 'reports/002-y.md': '# old\n'}, mtimes={'reports/002-y.md': 1_700_000_000}),
            'a queued brief with known heads and pending': dict(files={'queue': '{st}/briefs/002-y.md\n'}, pair={'heads': {'002-y': 'kept'}, 'pending': ['z', '{st}/briefs/001-x.md']}),
            'a devin sidekick generation': dict(files={'queue': '{st}/briefs/002-y.md\n'}, pair={'sidekick': {'kind': 'devin', 'generation': 4, 'name': 's1'}}),
        }
        results = {}
        for name, spec in cases.items():
            results[name] = self.send_both('take-' + name.replace(' ', '-'), snippet, call, mask_ts=False, **spec)
        self.assertEqual(results['a queued brief'][1].split('\n')[0][:4], 'got=')
        self.assertIn('send-brief', ' '.join(results['a queued brief'][4]))
        self.assertIn('queued brief is gone', results['a queued brief that is gone'][2])
        self.assertEqual(results['no queue'][1], 'got-nothing\n')
        self.assertNotIn('queue', results['a queued brief'][3])

    def test_send_helpers_require_fresh_session_matches_bash(self):
        for kind in ('devin', 'pi', 'prime-agent', 'claude', 'codex'):
            for extra in ({}, {'rotation_required': True}, {'rotation_required': False}, {'bootstrap_pending': True}, {'bootstrap_pending': False}, {'rotation_required': 'x'},
                          {'rotation_required': None, 'bootstrap_pending': None}, {'rotation_required': 0}):
                self.send_both(f'fresh-{kind}-{sorted(extra.items())}'.replace(' ', '').replace("'", '').replace('[', '').replace(']', '').replace('(', '').replace(')', '').replace(',', '-'),
                               'require_fresh_session "$store"', lambda ctx, st, fill: send.require_fresh_session(st),
                               pair={'sidekick': {'kind': kind, 'name': 's1', 'generation': 1, **extra}})
        self.send_both('fresh-null-sidekick', 'require_fresh_session "$store"', lambda ctx, st, fill: send.require_fresh_session(st), pair={'sidekick': None})

    def test_send_helpers_submit_typed_and_prompt_sidekick_match_bash(self):
        screen = lambda text: {'screens': {'s1': text}}
        call = lambda ctx, st, fill: f'submitted={send.submit_typed("s1", "001-x.md")}\n'
        snippet = 'submit_typed s1 001-x.md; echo "submitted=$submitted"'
        got = self.send_both('submit-not-visible', snippet, call, **screen('a quiet pane\n'))
        self.assertEqual(got[1], 'submitted=0\n', 'submit-not-visible')
        got = self.send_both('submit-visible-always', snippet, call, **screen('❯ PAIR BRIEF /s/briefs/001-x.md\n'))
        self.assertEqual(got[1], 'submitted=3\n', 'submit-visible-always')
        self.send_both('submit-visible-with-bars-and-blanks', snippet, call, **screen('│ ❯ PAIR  BRIEF\n│ /s/briefs/\n│ 001-x.md │\n'))
        self.send_both('submit-box-drawing-bytes', snippet, call, **screen('─ 001-x.md ─\n'))
        got = self.send_both('submit-typed-once', snippet, call, **screen('quiet\n'), once={'read': '#!/bin/sh\nmkdir -p "$FAKE/stalled" && printf "PAIR BRIEF /s/briefs/001-x.md" >"$FAKE/stalled/s1"\n'})
        self.assertEqual(got[1], 'submitted=1\n', 'submit-typed-once')
        got = self.send_both('submit-devin-working', snippet, call, agents={'s1': 'working devin'}, **screen('❯ 001-x.md\n'))
        self.assertEqual(got[1], 'submitted=0\n', 'submit-devin-working')
        self.send_both('submit-devin-idle', snippet, call, agents={'s1': 'idle devin'}, **screen('❯ 001-x.md\n'))
        got = self.send_both('submit-codex-working', snippet, call, agents={'s1': 'working codex'}, **screen('❯ 001-x.md\n'))
        self.assertEqual(got[1], 'submitted=3\n', 'submit-codex-working')
        self.send_both('submit-no-agent', snippet, call, agents={'s1': ''}, **screen('❯ 001-x.md\n'))
        # The marker is compared after tr -d '\n │' has deleted bytes from the screen, E2 and 82 among them, so a marker with a euro sign (E2 82 AC)
        # can never be found again: the locale is set explicitly and the bash and Python must agree.
        snippet_eur = 'submit_typed s1 "a\u20acb"; echo "submitted=$submitted"'
        call_eur = lambda ctx, st, fill: f'submitted={send.submit_typed("s1", "a\u20acb")}\n'
        for locale_name in ('en_US.UTF-8', 'C'):
            with mock.patch.dict(os.environ, {'LC_ALL': locale_name}):
                got = self.send_both('submit-euro-' + locale_name, snippet_eur, call_eur, env={'LC_ALL': locale_name}, **screen('x a\u20acb y\n'))
            self.assertEqual(got[1], 'submitted=0\n', locale_name)
        text = 'PAIR BRIEF {st}/briefs/001-x.md'
        pcall = lambda ctx, st, fill: f'submitted={send.prompt_sidekick("s1", fill(text))}\n'
        psnip = 'prompt_sidekick s1 "PAIR BRIEF $store/briefs/001-x.md"; echo "submitted=$submitted"'
        self.send_both('prompt-idle-times-out', psnip, pcall, agents={'s1': 'idle claude'})
        self.send_both('prompt-working-takes-it', psnip, pcall, agents={'s1': 'working claude'})
        got = self.send_both('prompt-fails', psnip, pcall, files={'../fake/prompt-fail': ''})
        self.assertEqual(got[0], 2)
        self.assertIn('prompt_failed', got[2])
        self.send_both('prompt-stalls', psnip, pcall, agents={'s1': 'working claude'}, files={'../fake/prompt-drop': ''})
        self.send_both('prompt-stalls-typed', psnip, pcall, agents={'s1': 'working claude'}, files={'../fake/stall': ''})
        self.send_both('prompt-to-an-absent-agent', psnip, pcall, agents={'s1': ''})
        for label, error, code in (('empty-stderr', '', 2), ('non-json-stderr', 'herdr: socket gone\n', 2), ('json-timeout', '{"error":{"code":"timeout"}}\n', 0),
                                   ('json-stalled', '{"error":{"code":"agent_prompt_stalled"}}', 0), ('json-string-error', '{"error":"boom"}\n', 2), ('two-payloads', '{"a":1}\n{"error":{"code":"timeout"}}\n', 0)):
            got = self.send_both('prompt-' + label, psnip, pcall, agents={'s1': 'working claude'}, prompt_error=error)
            self.assertEqual(got[0], code, label)
        self.send_both('prompt-text-ending-in-a-slash', 'prompt_sidekick s1 "PAIR BRIEF /"; echo "submitted=$submitted"',
                       lambda ctx, st, fill: f'submitted={send.prompt_sidekick("s1", "PAIR BRIEF /")}\n', agents={'s1': 'working claude'}, **screen('PAIR BRIEF /\n'))
        self.send_both('prompt-path-with-a-space', 'prompt_sidekick s1 "PAIR BRIEF $store/briefs/a b.md"; echo "submitted=$submitted"',
                       lambda ctx, st, fill: f'submitted={send.prompt_sidekick("s1", "PAIR BRIEF " + st + "/briefs/a b.md")}\n', agents={'s1': 'working claude'})

    def send_ab_cases(self):
        idle = {'s1': 'idle claude'}
        brief_text = lambda *lines, headers='playbook: investigation\n': '# Brief 003-z\n\n' + headers + 'timebox: 45\n\n## Scope\n\nmay write:\n' + ''.join(f'- {l}\n' for l in (lines or ('src/a.py',))) + '\nmust not write:\n- lib/**\n'
        plan_files = {'plans/001-x.md': '# Plan\n\nscale: small\n', 'reviews/001-x.md': '# Review\n\nverdict: agreed\n'}
        feature = 'playbook: feature\nplan: {st}/plans/001-x.md\n'
        run = ['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', '20000']
        base = dict(args=run, agents=idle, files={'briefs/003-z.md': brief_text()}, repo_files={'src/a.py': 'x', 'docs/guide.md': 'x'}, report_after='1 {st}/reports/003-z.md')
        mod = lambda **kw: {**base, **kw, 'files': {'briefs/003-z.md': brief_text(), **kw.pop('files', {})} if False else {**base['files'], **kw.get('files', {})}}
        steps = {'steps/003-z.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n'}
        return {
            'a dispatch to a report': base,
            'a dispatch with send-only': mod(args=run + ['--send-only']),
            'a dispatch with every': mod(args=['dispatch', '{st}', '{st}/briefs/003-z.md', '--every', '1']),
            'a dispatch that waits to steps': mod(files=steps, report_after=None),
            'a dispatch that takes the queued brief': mod(files={'queue': '{st}/briefs/003-z.md\n'}),
            'a dispatch while another brief is queued': mod(files={'queue': '{st}/briefs/002-y.md\n'}),
            'a dispatch of a brief with pending briefs': mod(pair={'pending': ['{st}/briefs/001-x.md', '{st}/briefs/002-y.md', '']}, files={'reports/001-x.md': '# R\n'}),
            'a dispatch of an answered brief': mod(files={'reports/003-z.md': '# old\n'}, mtimes={'reports/003-z.md': 1_700_000_000}),
            'a dispatch in a paused store': mod(files={'paused': 'lunch\n'}),
            'a dispatch of an unfilled brief': mod(files={'briefs/003-z.md': brief_text('{{{{path}}}}')}),
            'a dispatch with a comma scope': mod(files={'briefs/003-z.md': brief_text('src/a.py, src/b.py')}),
            'a dispatch with a scope entry that matches nothing': mod(files={'briefs/003-z.md': brief_text('src/a.py', 'nodir/**')}),
            'a feature brief with no plan': mod(files={'briefs/003-z.md': brief_text(headers='playbook: feature\n')}),
            'a feature brief with an agreed plan': mod(files={'briefs/003-z.md': brief_text(headers=feature), **plan_files}),
            'a feature brief whose review is not agreed': mod(files={'briefs/003-z.md': brief_text(headers=feature), 'plans/001-x.md': '# P\n', 'reviews/001-x.md': '# R\n\nverdict: revise\n'}),
            'a brief that is missing': mod(args=['dispatch', '{st}', '{st}/briefs/009-none.md']),
            'a relative brief path': mod(args=['dispatch', '{st}', '../st/briefs/003-z.md', '--timeout', '20000']),
            'a symlinked brief': mod(args=['dispatch', '{st}', '{T}/link.md', '--timeout', '20000'], links={'link.md': '{st}/briefs/003-z.md'}),
            'an unknown option': mod(args=run + ['--force']),
            'too few arguments': dict(args=['dispatch', '{st}']),
            'outside herdr': mod(noenv=True),
            'a bad timeout': mod(args=['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', 'abc']),
            'the sidekick is absent': mod(agents={}),
            'the sidekick is blocked': mod(agents={'s1': 'blocked claude'}),
            'the sidekick is working': mod(agents={'s1': 'working claude'}),
            'the sidekick is done': mod(agents={'s1': 'done claude'}),
            'a devin sidekick that needs a rotation': mod(agents={'s1': 'idle devin'}, pair={'sidekick': {'kind': 'devin', 'generation': 1, 'name': 's1', 'rotation_required': True}}),
            'a pi sidekick awaiting its bootstrap': mod(agents={'s1': 'idle pi'}, pair={'sidekick': {'kind': 'pi', 'generation': 1, 'name': 's1', 'bootstrap_pending': True}}),
            'a prompt that stalls and gets an Enter': mod(files={'../fake/stall': ''}),
            'a prompt that fails': mod(files={'../fake/prompt-fail': ''}),
            'a null sidekick': mod(pair={'sidekick': None}),
            'no pair.json': mod(nopair=True),
            'a null git root and cwd': mod(pair={'git_root': None, 'cwd': None}),
        }

    def send_ab_run(self, cases, skill):
        mismatches, outcomes = {}, {}
        # Masked: bash's own error text (a bad --timeout, a missing $2) names pair-core.sh and a line; for the oracle that is e6e83e3's file and
        # its line, which moved by HEAD. Python names cmd_wait's line, never 0 (asserted below).
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', text))
        for name, spec in cases.items():
            # A file the world holds before the send counts as an answer to it only when its integer-second mtime is not before the send's epoch,
            # so one written when the world is built would count or not by whether the build and the send fall in the same second, differently
            # in the oracle's world and the port's. Every fixture file is old; a case that needs a fresh one says so in its own `mtimes`.
            spec = {**spec, 'mtimes': {**{rel: self.OLD_MTIME for rel in spec.get('files', {})}, **spec.get('mtimes', {})}}
            got, want = self.wait_ab_compare(name, spec, skill)
            self.assertNotIn('line 0:', got['stderr'], name)
            for side in (got, want):
                side['stderr'] = norm_core(side['stderr'])
                side['stdout'] = self.mask_minutes(re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', side['stdout']))
                side['calls'] = self.collapse_polls(side['calls'])
            outcomes[name] = got
            if got != want:
                mismatches[name] = {k: (got[k], want[k]) for k in got if got[k] != want[k]}
        self.maxDiff = None
        self.assertEqual(mismatches, {})
        return outcomes

    OLD_MTIME = 1_000_000_000

    def test_send_ab_dispatch_matches_the_bash_at_the_base_commit(self):
        cases = self.send_ab_cases()
        cases['a brief through a missing directory'] = {**cases['a dispatch to a report'], 'args': ['dispatch', '{st}', '{st}/nope/../briefs/003-z.md']}
        outcomes = self.send_ab_run(cases, 'pstack-pair')
        # With no pair.json the bash prints a pair of error lines for each field() it reads: the repo root for the Scope check (whose
        # warnings then name an empty root), then the kind in require_fresh_session, which ends the command with 2. Python reproduces all of it.
        self.assertEqual(outcomes['no pair.json']['rc'], 2)
        self.assertEqual(outcomes['no pair.json']['stderr'].count('jq: error: Could not open file : No such file or directory'), 2)
        self.assertIn('matches no file or directory in ;', outcomes['no pair.json']['stderr'])
        self.assertEqual(outcomes['a dispatch to a report']['rc'], 0)
        self.assertEqual(outcomes['a dispatch with send-only']['rc'], 0)
        self.assertIn('sent: BRIEF', outcomes['a dispatch with send-only']['stdout'])
        self.assertEqual([outcomes[n]['rc'] for n in ('the sidekick is absent', 'the sidekick is blocked', 'the sidekick is working')], [2, 3, 5])
        self.assertEqual(outcomes['a dispatch while another brief is queued']['rc'], 5)
        self.assertEqual(outcomes['a feature brief with no plan']['rc'], 6)
        self.assertEqual(outcomes['a prompt that fails']['rc'], 2)
        self.assertEqual(outcomes['a dispatch of an unfilled brief']['rc'], 1)

    def test_send_ab_matches_the_bash_in_guided_and_trio(self):
        cases = self.send_ab_cases()
        keep = ('a dispatch to a report', 'a dispatch with send-only', 'a dispatch that waits to steps', 'a dispatch while another brief is queued', 'a feature brief with an agreed plan',
                'a feature brief with no plan', 'a dispatch in a paused store', 'the sidekick is absent', 'a prompt that stalls and gets an Enter')
        for skill in ('pstack-pair-guided', 'pstack-trio'):
            subset = {name: cases[name] for name in keep}
            agreed = cases['a feature brief with an agreed plan']
            if skill == 'pstack-pair-guided':
                for scale, approval in (('small', ''), ('medium', ''), ('medium', 'posted'), ('large', 'posted'), ('large', 'human'), ('', 'human')):
                    spec = dict(agreed)
                    spec['files'] = {**agreed['files'], 'plans/001-x.md': f'# Plan\n\n{"scale: " + scale + chr(10) if scale else ""}',
                                     'reviews/001-x.md': f'# R\n\nverdict: agreed\n{"approval: " + approval + chr(10) if approval else ""}'}
                    subset[f'guided scale {scale or "none"} approval {approval or "none"}'] = spec
            else:
                subset['trio with advice'] = {**agreed, 'files': {**agreed['files'], 'advice/001-x.md': '# Advice\n'}}
            self.send_ab_run(subset, skill)

    def test_send_ab_discuss_matches_the_bash_at_the_base_commit(self):
        idle = {'s1': 'idle claude'}
        plan = lambda *extra: '# Plan 001-x\n\n' + ''.join(extra) + 'scale: small\n'
        run = ['discuss', '{st}', '{st}/plans/001-x.md', '--timeout', '20000']
        base = dict(args=run, agents=idle, files={'plans/001-x.md': plan()}, report_after='1 {st}/reports/001-x.md')
        cases = {
            'a plan to its reply': base,
            'a plan with send-only': {**base, 'args': run + ['--send-only']},
            'a plan that is missing': {**base, 'args': ['discuss', '{st}', '{st}/plans/009-none.md']},
            'a plan in a paused store': {**base, 'files': {**base['files'], 'paused': 'lunch\n'}},
            'an unfilled plan': {**base, 'files': {'plans/001-x.md': plan('{{{{x}}}}\n')}},
            'a plan with an unknown option': {**base, 'args': run + ['--force']},
            'too few arguments': dict(args=['discuss', '{st}']),
            'a relative plan path': {**base, 'args': ['discuss', '{st}', '../st/plans/001-x.md', '--timeout', '20000']},
            'a symlinked plan': {**base, 'args': ['discuss', '{st}', '{T}/link.md', '--timeout', '20000'], 'links': {'link.md': '{st}/plans/001-x.md'}},
            'the sidekick is absent': {**base, 'agents': {}},
            'no pair.json': {**base, 'nopair': True},
            'the sidekick is working': {**base, 'agents': {'s1': 'working claude'}},
            'a rotation is required': {**base, 'agents': {'s1': 'idle pi'}, 'pair': {'sidekick': {'kind': 'pi', 'generation': 1, 'name': 's1', 'rotation_required': True}}},
            'a prompt that stalls': {**base, 'files': {**base['files'], '../fake/stall': ''}},
            'a reply already on file': {**base, 'report_after': None, 'files': {**base['files'], 'reports/001-x.md': '# R\n\nstatus: agree\n'}, 'mtimes': {'reports/001-x.md': int(time.time()) + 3600}},
            'a reply on file from before the send': {**base, 'report_after': None, 'files': {**base['files'], 'reports/001-x.md': '# R\n\nstatus: agree\n'}, 'mtimes': {'reports/001-x.md': 1_700_000_000}},
        }
        outcomes = self.send_ab_run(cases, 'pstack-pair')
        self.assertEqual(outcomes['a plan to its reply']['rc'], 0)
        self.assertIn('send-plan', ' '.join(outcomes['a plan to its reply']['events']))
        for skill in ('pstack-pair-guided',):
            self.send_ab_run({n: cases[n] for n in ('a plan to its reply', 'a plan with send-only', 'the sidekick is absent')}, skill)

    ROSTER_TOML = ('[sidekick]\nkind = "claude"\nargs = ["--model", "m1"]\n\n[sidekick.fallback]\nkind = "devin"\nargs = ["--fb", "x"]\n')

    def phase3_ab(self, label, skill, spec):
        """One world run through 04d2c13's script and the working tree's, outcomes with the two trees' paths masked: (working tree, base)."""
        bases = self.phase3_skills()
        # the oracle is 04d2c13 (PHASE3), where `cmd_spawn` is still the bash body, not the phase-2 tree or a shim
        self.assertIn('set_placement "$store" "$placement"', (bases / 'pstack-pair/scripts/pair-core.sh').read_text())
        if skill == 'pstack-kitchen':
            spec = {**spec, 'repo_files': {**spec.get('repo_files', {}), '.agents/kitchen.toml': self.KITCHEN_TOML}}
        want = self.ab_run(f'{label}-base', bases, skill, spec)
        got = self.ab_run(f'{label}-port', root / 'skills', skill, spec)
        # the two trees sit at two paths (calls.log and pair.json name references/no-mcp.json under them)
        mask = lambda o, tree: json.loads(json.dumps(o).replace(json.dumps(str(tree))[1:-1], '<SK>'))
        return mask(got, root / 'skills'), mask(want, bases)

    def test_kitchen_spawn_matches_the_bash_at_the_phase_3_base(self):
        """kitchen.sh `spawn` (its bash wrapper over the captured pair command) at 04d2c13 and at the working tree, on the fake herdr: the roster's
        default kind, `--kind` over it, the roster's fallback, and `--tab`. Stdout, stderr, status, the store, events.tsv rows and calls.log."""
        hook = {'hook': self.READY_HOOK, 'env': {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300'}}
        roster = {'home/.config/pstack/kitchen.toml': (self.ROSTER_TOML, 0o644)}
        base = dict(args=['spawn', '{st}'], topfiles=roster, **hook, pair={'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None}})
        cases = {
            'the roster default kind': base,
            'the roster default kind and a trailing native arg': {**base, 'args': ['spawn', '{st}', '--', '--x']},
            '--kind over the roster': {**base, 'args': ['spawn', '{st}', '--kind', 'codex']},
            '--kind equal to the roster kind takes its fallback': {**base, 'args': ['spawn', '{st}', '--kind', 'claude']},
            '--kind with an explicit fallback': {**base, 'args': ['spawn', '{st}', '--kind', 'codex', '--fallback', 'pi']},
            '--tab': {**base, 'args': ['spawn', '{st}', '--tab'], 'pair': {'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None},
                                                                          'master': {'name': 'm1', 'pane_id': 'w1:p0'}}},
            'an empty roster and no --kind': {**base, 'topfiles': {}},
        }
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            got, want = self.phase3_ab(f'k{n}', 'pstack-kitchen', spec)
            self.maxDiff = None
            self.assertEqual(got, want, name)
            outcomes[name] = got
        self.assertEqual(outcomes['the roster default kind']['rc'], 0)
        self.assertIn('started in', outcomes['the roster default kind']['stdout'])
        self.assertIn('"kind": "claude"', outcomes['the roster default kind']['files']['pair.json'])
        self.assertIn('"kind": "codex"', outcomes['--kind over the roster']['files']['pair.json'])
        self.assertIn('"kind": "devin"', outcomes['--kind equal to the roster kind takes its fallback']['files']['pair.json'])
        self.assertNotEqual(outcomes['an empty roster and no --kind']['rc'], 0)

    def spawn_command_cases(self):
        """Worlds for the spawn command (args after `spawn`): {name: spec}."""
        hook = {'hook': self.READY_HOOK, 'env': {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PREFLIGHT_RETRY_S': '0'}}
        quiet = {'env': hook['env']}
        side = lambda **kw: {'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None, **kw}}
        base = dict(pair=side(), **hook)
        run = lambda *a, **kw: {**base, 'args': ['spawn', '{st}', *a], **kw}
        return {
            'no arguments': {**base, 'args': ['spawn']},
            'a missing --kind': run(),
            'an unknown option': run('--bogus'),
            'an unknown option after --kind': run('--kind', 'claude', '--bogus'),
            'an unknown store': {**base, 'args': ['spawn', '{T}/nope', '--kind', 'claude']},
            'a store without pair.json': {**base, 'nopair': True, 'args': ['spawn', '{st}', '--kind', 'claude']},
            'a plain spawn': run('--kind', 'claude'),
            'a master that is a string': run('--kind', 'claude', pair={**side(), 'master': 'x'}),
            'a master that is an array': run('--kind', 'claude', pair={**side(), 'master': [1]}),
            '--tab': run('--kind', 'claude', '--tab', pair={**side(), 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}),
            '--split after --tab': run('--kind', 'claude', '--tab', '--split'),
            'a placement already recorded': run('--kind', 'claude', pair={**side(), 'placement': 'tab', 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}),
            'a placement from the environment': run('--kind', 'claude', env={**hook['env'], 'PSTACK_PLACEMENT': 'tab'}),
            'a bad placement from the environment': run('--kind', 'claude', env={**hook['env'], 'PSTACK_PLACEMENT': 'floating'}),
            'a fallback with its arguments': run('--kind', 'claude', '--fallback', 'devin', '--fallback-arg', '--model', '--fallback-arg', 'a b'),
            'a fallback with an empty and a newline argument': run('--kind', 'claude', '--fallback', 'devin', '--fallback-arg', '', '--fallback-arg', 'x\ny'),
            'a fallback over a recorded one': run('--kind', 'claude', '--fallback', 'codex', pair=side(fallback={'kind': 'devin', 'args': ['x']})),
            'native arguments': run('--kind', 'claude', '--', '--model', 'm', 'a b'),
            'permission, direction, pane and timeout': run('--kind', 'claude', '--permission', 'plan', '--direction', 'down', '--timeout', '5000'),
            'a pane given': run('--kind', 'claude', '--pane', 'p1', panes={'p1': ''}),
            'a missing value': run('--kind'),
            'a pi preflight that fails with no fallback': run('--kind', 'pi', fake={'pi-fail': 'quota exhausted\n'}),
            'a pi preflight that fails with a fallback': run('--kind', 'pi', '--fallback', 'devin', '--fallback-arg', '--m', fake={'pi-fail': 'quota exhausted\n'}),
            'a pi preflight that fails with the same fallback': run('--kind', 'pi', '--fallback', 'pi', fake={'pi-fail': 'quota exhausted\n'}),
            'a pi preflight that fails with a fallback recorded earlier': run('--kind', 'pi', fake={'pi-fail': 'quota exhausted\n'}, pair=side(fallback={'kind': 'codex', 'args': ['-c', 'x=1']})),
            'a pi preflight that fails with nothing to say': run('--kind', 'pi', '--fallback', 'devin', fake={'pi-reply': ''}),
            'a pi preflight that fails with a retry wait that is not a number': run('--kind', 'pi', '--fallback', 'devin', fake={'pi-fail': 'quota exhausted\n'},
                                                                                  env={**hook['env'], 'PAIR_PREFLIGHT_RETRY_S': 'x'}),
            'a pi preflight that passes': run('--kind', 'pi', '--fallback', 'devin'),
            'a failover whose record fails (failovers is not an array)': run('--kind', 'pi', '--fallback', 'devin', fake={'pi-fail': 'quota exhausted\n'}, pair=side(failovers='x')),
            'a failover over an object of failovers': run('--kind', 'pi', '--fallback', 'devin', fake={'pi-fail': 'quota exhausted\n'}, pair=side(failovers={'a': 1})),
            'a fallback recorded over a sidekick that is not an object': run('--kind', 'claude', '--fallback', 'devin', pair={**side(), 'sidekick': 'x'}),
            'a placement recorded over a pair.json that is an array': {**base, 'args': ['spawn', '{st}', '--kind', 'claude', '--tab'], 'nopair': True, 'files': {'pair.json': '[]\n'}},
            'a failover over a null sidekick failovers': run('--kind', 'pi', '--fallback', 'devin', fake={'pi-fail': 'quota exhausted\n'}, pair=side(failovers=None)),
            'a live sidekick skips the preflight': run('--kind', 'pi', '--fallback', 'devin', agents={'s1': 'idle pi'}, fake={'pi-fail': 'quota exhausted\n'}, pair=side(kind='pi', pane_id='p1')),
            'a bootstrap that returns 4': {**run('--kind', 'claude'), 'hook': None, **quiet},
            'a bootstrap that returns 3': run('--kind', 'claude', promptfail=True),
            'a fallback that returns 4': {**run('--kind', 'pi', '--fallback', 'devin', fake={'pi-fail': 'quota exhausted\n'}), 'hook': None, **quiet},
        }

    def spawn_command_part(self, skill, part, parts=3):
        cases = list(self.spawn_command_cases().items())
        outcomes = {}
        for n, (name, spec) in enumerate(cases):
            if n % parts == part:
                got, want = self.phase3_ab(f'sc{n}', skill, spec)
                self.maxDiff = None
                if name == 'a missing value':
                    # the bash's set -u names its file and the line of `--kind "$2"`; the port names the file and the line of the cmd_spawn shim
                    # (kitchen.sh's own wrapper fails first, on both sides)
                    self.assertRegex(want['stderr'], r'line [0-9]+: \$2: unbound variable')
                    if 'pair-core.sh' in got['stderr']:
                        shim = self.shim_line('pstack-pair/scripts/pair-core.sh', 'cmd_spawn')
                        self.assertRegex(got['stderr'], rf'pair-core\.sh: line {shim}: \$2: unbound variable\n')
                    mask = lambda text: re.sub(r'\S*(pair-core|kitchen)\.sh: line [0-9]+', '<core>: line N', text)
                    got, want = dict(got, stderr=mask(got['stderr'])), dict(want, stderr=mask(want['stderr']))
                self.assertEqual(got, want, name)
                outcomes[name] = got
                if name in self.SPAWN_COMMAND_EXPECT and not (skill == 'pstack-kitchen' and name in ('a missing --kind', 'an unknown option', 'a bad placement from the environment')):
                    code, needle = self.SPAWN_COMMAND_EXPECT[name]
                    self.assertEqual(got['rc'], code, name)
                    self.assertIn(needle, got['stdout'] + got['stderr'] + ' '.join(got['events']) + got['files']['pair.json'], name)
        return outcomes

    def shim_line(self, rel, name):
        """The line a one-line shim of a script sits on."""
        return next(n for n, line in enumerate((root / 'skills' / rel).read_text().splitlines(), 1) if line.startswith(f'{name}() {{'))

    SPAWN_COMMAND_EXPECT = {
        'a plain spawn': (0, 'claude:ready'), '--tab': (0, 'started in t0'), 'a bad placement from the environment': (1, 'placement must be split or tab, not floating'),
        'a pi preflight that fails with no fallback': (2, 'no fallback recorded for a failed pi'), 'a pi preflight that fails with a fallback': (0, 'devin:ready'),
        'a pi preflight that fails with a fallback recorded earlier': (0, 'codex:ready'), 'a live sidekick skips the preflight': (0, 'already live (idle) in p1'),
        'a bootstrap that returns 4': (4, 'claude:unready'), 'a bootstrap that returns 3': (3, 'claude:failed'), 'a fallback that returns 4': (4, 'devin:unready'),
        'an unknown option': (1, 'unknown option --bogus'), 'a missing --kind': (1, '--kind is required'),
        'a master that is a string': (0, 'Cannot index string with string ("permission_mode")'),
        'a pi preflight that fails with a retry wait that is not a number': (0, 'sleep: invalid time interval'), 'a master that is an array': (0, 'Cannot index array with string ("permission_mode")'),
        # spawn_sidekick runs under `|| rc=$?`, where errexit is off: a failed failover record prints jq's error and the fallback still starts; a failed
        # fallback or placement record (in cmd_spawn proper, errexit on) ends the command
        'a failover whose record fails (failovers is not an array)': (0, 'cannot be added'), 'a failover over an object of failovers': (0, 'devin:ready'),
        'a fallback recorded over a sidekick that is not an object': (5, 'Cannot index string'), 'a placement recorded over a pair.json that is an array': (5, 'jq: error')}


    def test_spawn_command_through_pair_matches_the_bash_at_the_phase_3_base_part_0(self):
        out = self.spawn_command_part('pstack-pair', 0)
        if 0 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_pair_matches_the_bash_at_the_phase_3_base_part_1(self):
        out = self.spawn_command_part('pstack-pair', 1)
        if 1 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_pair_matches_the_bash_at_the_phase_3_base_part_2(self):
        out = self.spawn_command_part('pstack-pair', 2)
        if 2 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_guided_matches_the_bash_at_the_phase_3_base_part_0(self):
        out = self.spawn_command_part('pstack-pair-guided', 0)
        if 0 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_guided_matches_the_bash_at_the_phase_3_base_part_1(self):
        out = self.spawn_command_part('pstack-pair-guided', 1)
        if 1 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_guided_matches_the_bash_at_the_phase_3_base_part_2(self):
        out = self.spawn_command_part('pstack-pair-guided', 2)
        if 2 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_kitchen_matches_the_bash_at_the_phase_3_base_part_0(self):
        out = self.spawn_command_part('pstack-kitchen', 0)
        if 0 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_kitchen_matches_the_bash_at_the_phase_3_base_part_1(self):
        out = self.spawn_command_part('pstack-kitchen', 1)
        if 1 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    def test_spawn_command_through_kitchen_matches_the_bash_at_the_phase_3_base_part_2(self):
        out = self.spawn_command_part('pstack-kitchen', 2)
        if 2 == 0:
            self.assertEqual(out['a plain spawn']['rc'], 0)

    SIDEKICK_ONLY_HOOK = ('#!/bin/sh\nst="$FAKE/../st"\ncase "$2" in Load*) case "$1" in c1) ;; *) printf "# R\\n\\nstatus: done\\n" >"$st/reports/000-ready.md" ;; esac ;; esac\nexit 0\n')

    def trio_spawn_cases(self):
        """Worlds for the trio's spawn (args after `spawn {st}`): {name: spec}. The master is a claude in p0; the roles are s1 and c1."""
        env = {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PREFLIGHT_RETRY_S': '0'}
        hook = {'hook': self.READY_HOOK, 'env': env}
        quiet = {'env': env}
        pair = lambda **kw: {'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None},
                             'consultant': {'name': 'c1', 'kind': 'codex', 'generation': 1, 'pane_id': None}, **kw}
        base = dict(pair=pair(), panes={'p0': 'm1'}, **hook)
        run = lambda *a, **kw: {**base, 'args': ['spawn', '{st}', *a], **kw}
        return {
            'no arguments': {**base, 'args': ['spawn']},
            'an unknown option': run('--bogus'),
            'an unknown store': {**base, 'args': ['spawn', '{T}/nope', '--sidekick', 'codex']},
            'a missing value': run('--consultant'),
            '--sidekick and --consultant': run('--sidekick', 'codex', '--consultant', 'devin'),
            '--kind alone': run('--kind', 'codex'),
            '--kind then --consultant': run('--kind', 'codex', '--consultant', 'devin'),
            '--consultant then --kind': run('--consultant', 'devin', '--kind', 'codex'),
            'the recorded kinds': run(),
            'the recorded sidekick kind is missing': run(pair=pair(sidekick={'name': 's1', 'kind': None, 'pane_id': None})),
            'the recorded consultant kind is missing': run('--sidekick', 'codex', pair=pair(consultant={'name': 'c1', 'kind': None, 'pane_id': None})),
            '--only sidekick': run('--only', 'sidekick', '--sidekick', 'codex'),
            '--only sidekick with no recorded consultant kind': run('--only', 'sidekick', '--sidekick', 'codex', pair=pair(consultant=None)),
            '--only consultant': run('--only', 'consultant', '--consultant', 'devin'),
            '--only consultant with no recorded sidekick kind': run('--only', 'consultant', '--consultant', 'devin', pair=pair(sidekick=None)),
            '--only with a bad value': run('--only', 'both', '--sidekick', 'codex'),
            'all three kinds equal': run('--sidekick', 'claude', '--consultant', 'claude'),
            'all three kinds equal for --only sidekick': run('--only', 'sidekick', '--sidekick', 'claude'),
            'an unreadable master kind': run('--sidekick', 'codex', '--consultant', 'devin', panes={}, pair=pair(master={'name': 'm1', 'pane_id': 'gone'})),
            'a sidekick that returns 4': run('--sidekick', 'codex', '--consultant', 'devin', hook=None, **quiet),
            'a consultant that returns 4': run('--sidekick', 'codex', '--consultant', 'devin', hook=self.SIDEKICK_ONLY_HOOK),
            'a consultant that dies with 5': run('--sidekick', 'codex', '--consultant', 'devin', '--', '--resume'),
            'a consultant whose anchor pane is gone': run('--sidekick', 'codex', '--consultant', 'devin', agents={'s1': 'idle codex'},
                                                          pair=pair(sidekick={'name': 's1', 'kind': 'codex', 'generation': 1, 'pane_id': 'p9'})),
            'a consultant anchored on the sidekick pane': run('--sidekick', 'codex', '--consultant', 'devin', agents={'s1': 'idle codex'}, panes={'p0': 'm1', 'p1': 's1'},
                                                              pair=pair(sidekick={'name': 's1', 'kind': 'codex', 'generation': 1, 'pane_id': 'p1'})),
            'an unrecorded sidekick pane': run('--only', 'consultant', '--consultant', 'devin', pair=pair(sidekick={'name': 's1', 'kind': 'codex'})),
            '--consultant-permission': run('--sidekick', 'codex', '--consultant', 'devin', '--permission', 'plan', '--consultant-permission', 'none'),
            '--permission for both': run('--sidekick', 'codex', '--consultant', 'devin', '--permission', 'plan'),
            '--tab': run('--sidekick', 'codex', '--consultant', 'devin', '--tab'),
            '--split after a recorded tab': run('--sidekick', 'codex', '--consultant', 'devin', '--split', pair=pair(placement='tab')),
            'a bad placement from the environment': run('--sidekick', 'codex', '--consultant', 'devin', env={**env, 'PSTACK_PLACEMENT': 'floating'}),
            'native arguments for both': run('--sidekick', 'codex', '--consultant', 'devin', '--', '--model', 'm'),
            'a fallback recorded for the sidekick': run('--sidekick', 'codex', '--consultant', 'devin', '--fallback', 'devin', '--fallback-arg', '--x'),
            'a fallback with --only consultant is not recorded': run('--only', 'consultant', '--consultant', 'devin', '--fallback', 'devin'),
            'a sidekick preflight that fails over': run('--sidekick', 'pi', '--consultant', 'devin', '--fallback', 'codex', fake={'pi-fail': 'quota\n'}),
            'a sidekick preflight that fails with no fallback': run('--sidekick', 'pi', '--consultant', 'devin', fake={'pi-fail': 'quota\n'}),
            'a consultant that is already live': run('--sidekick', 'codex', '--consultant', 'devin', agents={'c1': 'idle devin'}),
        }

    def trio_spawn_part(self, part, parts=3):
        outcomes = {}
        for n, (name, spec) in enumerate(self.trio_spawn_cases().items()):
            if n % parts != part:
                continue
            got, want = self.phase3_ab(f'ts{n}', 'pstack-trio', spec)
            self.maxDiff = None
            if name == 'a missing value':
                trio = 'pstack-trio/scripts/pair.sh'
                self.assertRegex(want['stderr'], r'line [0-9]+: \$2: unbound variable')
                self.assertRegex(got['stderr'], rf'pstack-trio/scripts/pair\.sh: line {self.shim_line(trio, "cmd_spawn")}: \$2: unbound variable\n')
                mask = lambda text: re.sub(r'\S*pair\.sh: line [0-9]+', '<trio>: line N', text)
                got, want = dict(got, stderr=mask(got['stderr'])), dict(want, stderr=mask(want['stderr']))
            self.assertEqual(got, want, name)
            outcomes[name] = got
            if name in self.TRIO_SPAWN_EXPECT:
                code, needle = self.TRIO_SPAWN_EXPECT[name]
                self.assertEqual(got['rc'], code, name)
                self.assertIn(needle, got['stdout'] + got['stderr'] + ' '.join(got['events']) + got['calls'], name)
        return outcomes

    TRIO_SPAWN_EXPECT = {
        '--sidekick and --consultant': (0, 'kinds: master claude, sidekick codex, consultant devin'), '--kind alone': (0, 'kinds: master claude, sidekick codex, consultant codex'),
        '--consultant then --kind': (0, 'kinds: master claude, sidekick codex, consultant devin'), 'the recorded kinds': (0, 'kinds: master claude, sidekick claude, consultant codex'),
        '--only sidekick': (0, 'sidekick s1 (codex) started'), '--only consultant': (0, 'consultant c1 (devin) started'),
        '--only with a bad value': (1, '--only takes sidekick or consultant'), 'all three kinds equal': (8, 'all three agents would be claude'),
        'an unreadable master kind': (2, "cannot read the master's agent kind"), 'a sidekick that returns 4': (4, 'codex:unready'),
        'a consultant that returns 4': (4, 'devin:unready'), 'a consultant that dies with 5': (5, 'devin task sessions start fresh'),
        'the recorded sidekick kind is missing': (1, '--sidekick KIND is required'), 'the recorded consultant kind is missing': (1, '--consultant KIND is required'),
        'a sidekick preflight that fails over': (0, 'codex:ready'), 'a sidekick preflight that fails with no fallback': (2, 'no fallback recorded for a failed pi'),
        'a consultant that is already live': (0, 'consultant c1 already live (idle)'), '--tab': (0, 'tab create')}

    def test_trio_spawn_matches_the_bash_at_the_phase_3_base_part_0(self):
        self.trio_spawn_part(0)

    def test_trio_spawn_matches_the_bash_at_the_phase_3_base_part_1(self):
        self.trio_spawn_part(1)

    def test_trio_spawn_matches_the_bash_at_the_phase_3_base_part_2(self):
        self.trio_spawn_part(2)

    def test_kitchen_consultant_matches_the_bash_at_the_phase_3_base(self):
        """kitchen.sh `consultant`, which calls spawn_consultant as a plain command under set -e: a start that succeeds records the escalation; one
        that returns 4 ends the command with 4 and records nothing; a die inside ends it with the die's status. At 04d2c13 and at the working tree."""
        env = {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PREFLIGHT_RETRY_S': '0'}
        roster = {'home/.config/pstack/kitchen.toml': ('[consultant]\nkind = "pi"\nargs = ["--model", "c"]\n\n[consultant.fallback]\nkind = "codex"\nargs = ["--fb"]\n', 0o644)}
        pair = lambda **kw: {'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None},
                             'consultant': {'name': 'c1', 'kind': 'codex', 'generation': 1, 'pane_id': None}, **kw}
        base = dict(pair=pair(), panes={'p0': 'm1'}, hook=self.READY_HOOK, env=env, args=['consultant', '{st}', '--reason', 'a contract'])
        run = lambda *a, **kw: {**base, 'args': [*base['args'], *a], **kw}
        cases = {
            'a start that succeeds': run('--kind', 'codex'),
            'a start that succeeds anchored on the sidekick pane': run('--kind', 'codex', agents={'s1': 'idle claude'}, panes={'p0': 'm1', 'p1': 's1'},
                                                                      pair=pair(sidekick={'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p1'})),
            'a start whose anchor pane is gone': run('--kind', 'codex', agents={'s1': 'idle claude'}, pair=pair(sidekick={'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p9'})),
            'a master that is a string': run('--kind', 'codex', pair=pair(master='x')),
            'a master that is an array': run('--kind', 'codex', pair=pair(master=[1])),
            'a start that returns 4': run('--kind', 'codex', hook=None),
            'a start that returns 3': run('--kind', 'codex', promptfail=True),
            'a start that dies with 5': run('--kind', 'devin', '--', '--resume'),
            'the roster kind and its native args': run(topfiles=roster),
            'the roster kind failing its preflight': run(topfiles=roster, fake={'pi-fail': 'quota\n'}),
            'a roster kind that returns 4 after its preflight': run(topfiles=roster, hook=None),
            'a live consultant': run('--kind', 'codex', agents={'c1': 'idle codex'}),
            'no reason': {**base, 'args': ['consultant', '{st}', '--kind', 'codex']},
        }
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            got, want = self.phase3_ab(f'kc{n}', 'pstack-kitchen', spec)
            self.maxDiff = None
            self.assertEqual(got, want, name)
            outcomes[name] = got
        self.assertEqual(outcomes['a start that succeeds']['rc'], 0)
        self.assertIn('a contract', outcomes['a start that succeeds']['files']['pair.json'])
        self.assertIn('master\tescalate', '\n'.join(outcomes['a start that succeeds']['events']))
        self.assertEqual(outcomes['a start that returns 4']['rc'], 4)
        self.assertNotIn('a contract', outcomes['a start that returns 4']['files']['pair.json'])
        self.assertNotIn('escalate', '\n'.join(outcomes['a start that returns 4']['events']))
        self.assertEqual(outcomes['a start that returns 3']['rc'], 3)
        self.assertEqual(outcomes['a start that dies with 5']['rc'], 5)
        # kitchen.sh consultant calls spawn_consultant as a plain command under set -e: a master that is not an object ends it at the anchor lookup,
        # before any herdr call
        for name in ('a master that is a string', 'a master that is an array'):
            self.assertEqual((outcomes[name]['rc'], outcomes[name]['calls']), (5, ''), name)
            self.assertIn('jq: error', outcomes[name]['stderr'], name)
        self.assertIn('failover: consultant pi -> codex', outcomes['the roster kind failing its preflight']['stdout'])

    SPAWN_CONSULTANT_FORMS = {
        'with an or': 'rc=0; spawn_consultant "$store" {a} || rc=$?; echo "returned rc=$rc"',
        'as a plain command': 'spawn_consultant "$store" {a}; echo "returned"',
        'as a plain command under errexit': 'set -e\nspawn_consultant "$store" {a}\necho "returned"',
    }

    def test_spawn_consultant_matches_the_bash_at_the_phase_3_base_in_every_call_form(self):
        """consultant-core.sh's spawn_consultant (04d2c13's function against the working tree's shim) from the forms its callers use: kitchen.sh
        `consultant` calls it as a plain command under set -e, and `|| rc=$?` and a plain command with errexit off are the other two. The shim returns
        3 and 4 and ends the caller on anything else: a die (here 5) must end the caller in the form where a return would not."""
        env = {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300'}
        pair = lambda **kw: {'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None},
                             'consultant': {'name': 'c1', 'kind': 'codex', 'generation': 1, 'pane_id': None}, **kw}
        world = dict(pair=pair(), panes={'p0': 'm1'}, hook=self.READY_HOOK, env=env)
        call = lambda kind='codex', perm='', *args: shlex.join([kind, perm, '5000', *args])
        cases = {
            'a start that succeeds': (world, call()),
            'a start that returns 4': ({**world, 'hook': None}, call()),
            'a start that returns 3': ({**world, 'promptfail': True}, call()),
            'a start that dies with 5': (world, call('devin', '', '--resume')),
            'a start that dies with 2': ({**world, 'fake': {'start-fail': ''}}, call()),
            'an anchor on the sidekick pane': ({**world, 'agents': {'s1': 'idle claude'}, 'panes': {'p0': 'm1', 'p1': 's1'},
                                                'pair': pair(sidekick={'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p1'})}, call()),
            'an anchor pane that is gone': ({**world, 'agents': {'s1': 'idle claude'}, 'pair': pair(sidekick={'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p9'})}, call()),
            'a permission and native arguments': (world, call('codex', 'plan', '--model', 'm')),
            'a live consultant': ({**world, 'agents': {'c1': 'idle codex'}}, call()),
        }
        seen = {}
        for n, (name, (spec, argv)) in enumerate(cases.items()):
            for form, template in self.SPAWN_CONSULTANT_FORMS.items():
                seen[(name, form)] = self.leaf_both(f'sc-{n}-{len(seen)}', template.format(a=argv), None, sources=('pstack-trio/scripts/consultant-core.sh',), **spec)
        self.assertEqual((seen[('a start that returns 4', 'with an or')][0], 'returned rc=4' in seen[('a start that returns 4', 'with an or')][1]), (0, True))
        self.assertEqual(seen[('a start that returns 4', 'as a plain command under errexit')][0], 4)
        self.assertEqual(seen[('a start that returns 3', 'with an or')][1].count('returned rc=3'), 1)
        for form in self.SPAWN_CONSULTANT_FORMS:
            self.assertEqual(seen[('a start that dies with 5', form)][0], 5, form)
            self.assertNotIn('returned', seen[('a start that dies with 5', form)][1], form)
            self.assertEqual(seen[('a start that dies with 2', form)][0], 2, form)
            self.assertEqual(seen[('a start that succeeds', form)][0], 0, form)

    def test_exit_command_matches_the_bash_at_the_phase_3_base(self):
        kinds = ('claude', 'codex', 'pi', 'devin', 'muse', 'prime-agent', 'grok', '', 'PI', 'pi ')
        bash = ''.join(f'printf "[%s]" "$(exit_command {shlex.quote(k)})"; exit_command {shlex.quote(k)} | od -c | head -1\n' for k in kinds)
        out = self.prime_both('exit-command', bash, '', shim=True)
        self.assertIn('[/quit]', out[1])
        self.assertIn('[/exit]', out[1])

    def stop_prime_cases(self):
        path_session = {'kind': 'path', 'value': '/t/one.jsonl'}
        recorded = {'sidekick': {'session': path_session}}
        gone = {'agents': {}, 'sidekick': {'session': path_session}}
        return {
            'an idle client': dict(),
            'a done client': dict(agents={'s1': 'done prime-agent'}),
            'a blocked client': dict(agents={'s1': 'blocked prime-agent'}),
            'a working client': dict(agents={'s1': 'working prime-agent'}),
            'an absent client with its session recorded': dict(**gone),
            'an absent client with no session recorded': dict(agents={}),
            'a client herdr reports no session for': dict(herdr_session=False),
            'no live session matches': dict(sessions=[]),
            'a session that stays active': dict(fake={'prime-stays-live': ''}),
            'a stop that fails': dict(fake={'prime-stop-fail': ''}),
            'a daemon whose list fails': dict(fake={'prime-list-fail': ''}),
            'a daemon whose list fails after the stop': dict(fake={'prime-list-fail-after-stop': ''}),
            'a daemon whose list holds a null row after the stop': dict(fake={'prime-list-null-after-stop': ''}),
            'a daemon whose list is not JSON after the stop': dict(fake={'prime-list-fail-after-stop': '', 'wrap-list': 'not json\n'}),
            'a pane whose shell never takes the foreground': dict(fake={'prime-client-stays': ''}, clock=True),
            'a pane whose shell never takes the foreground and an absent client': dict(**gone, fake={'prime-client-stays': ''}, clock=True),
            'a pane with no process info': dict(**gone, panes={}),
            'a pane id that is not recorded': dict(agents={}, sidekick={'session': path_session, 'pane_id': None}, panes={}),
            'a lock that stays held at the final write': dict(**gone, files={'.pair.lock/held': ''}, env={'PAIR_LOCK_TRIES': '1', 'PAIR_LOCK_POLL_S': '0'}, clock=True),
            'a role entry that is null': dict(pair={'consultant': None}),
        }

    def test_stop_prime_role_matches_the_bash_at_the_phase_3_base(self):
        """stop_prime_role, which its callers test with `||`: 04d2c13's function against the working tree's shim, over every client status and
        every way the cleanup stops short. The deadline cases run on a clock that jumps 100 s per `date +%s` and a sleep that returns at once (the
        port's deadline and poll are 0)."""
        bash = 'stop_prime_role "$store" sidekick || echo "returned rc=$?"; echo "after"'
        out = {name: self.prime_both('spr-' + re.sub(r'\W+', '-', name), bash, '', shim=True, **spec) for name, spec in self.stop_prime_cases().items()}
        self.assertEqual(out['an idle client'][1], 'after\n')
        self.assertIn('"session_stopped": true', out['an idle client'][3])
        self.assertIn('["stop", "live-1"', out['an idle client'][4])
        self.assertIn('agent prompt s1 /quit', out['an idle client'][5])
        self.assertIn('agent send-keys s1 ctrl+d', out['a blocked client'][5])
        self.assertNotIn('/quit', out['a working client'][5])
        self.assertIn('no recorded Prime session for sidekick', out['an absent client with no session recorded'][2])
        self.assertIn('no live daemon session matches s1', out['no live session matches'][2])
        self.assertIn('is still active', out['a session that stays active'][2])
        self.assertIn('returned rc=1', out['a daemon whose list fails after the stop'][1])
        self.assertEqual(out['a daemon whose list holds a null row after the stop'][1], 'after\n')
        self.assertIn('"stop"', out['a daemon whose list fails after the stop'][4])
        self.assertNotIn('"session_stopped"', out['a daemon whose list fails after the stop'][3])
        self.assertIn('Prime client has not exited in p1', out['a pane whose shell never takes the foreground'][2])
        self.assertIn('returned rc=1', out['a stop that fails'][1])
        self.assertEqual(out['a lock that stays held at the final write'][0], 1)
        self.assertNotIn('after', out['a lock that stays held at the final write'][1])

    def test_the_kitchens_verifier_cleanup_matches_the_bash_at_the_phase_3_base_with_the_shims(self):
        """kitchen.sh's verifier cleanup (the lines of its verdict collection that stop a Prime verifier with `stop_prime_role ... || die ... 5` or
        exit any other kind and wait for it to go), cut out of kitchen.sh and run on its own against 04d2c13's helpers and the working tree's."""
        lines = (root / 'skills/pstack-kitchen/scripts/kitchen.sh').read_text().splitlines()
        first = next((n for n, line in enumerate(lines) if line == '\tif [ "$kind" = prime-agent ]; then' and 'stop_prime_role "$store" verifier' in lines[n + 1]), None)
        self.assertIsNotNone(first, 'kitchen.sh no longer holds the lines `if [ "$kind" = prime-agent ]; then` / `stop_prime_role "$store" verifier`')
        last = next((n for n in range(first, len(lines)) if lines[n].startswith('\t[ "$kind" != prime-agent ] || json_update "$store" \'.verifier.closed = true\'')), None)
        self.assertIsNotNone(last, 'kitchen.sh no longer holds the line `[ "$kind" != prime-agent ] || json_update "$store" \'.verifier.closed = true\'`')
        cleanup = '\n'.join(lines[first:last + 1])
        self.assertIn('exit_command "$kind"', cleanup)
        verifier = {'kind': 'prime-agent', 'name': 'v1', 'pane_id': 'p2', 'session': {'kind': 'path', 'value': '/t/v.jsonl'}}
        row = {'activeSessionId': 'live-v', 'sessionId': 'sess-v', 'sessionFile': '/t/v.jsonl', 'name': 'v1'}
        prime = dict(agents={'v1': 'idle prime-agent'}, panes={'p1': 's1', 'p2': 'v1'}, pair={'verifier': verifier},
                     sessions=[row], fake={'sessions/v1': '/t/v.jsonl'})
        bash = f'kind=$(jq -r .verifier.kind "$store/pair.json"); name=v1; pane=p2\n( {cleanup}\n echo "cleaned" ) ; echo "rc=$?"'
        cases = {
            'a Prime verifier whose cleanup succeeds': prime,
            'a Prime verifier whose cleanup fails': {**prime, 'fake': {**prime['fake'], 'prime-stop-fail': ''}},
            'a Prime verifier whose shell never takes the foreground': {**prime, 'clock': True, 'fake': {**prime['fake'], 'prime-client-stays': ''}},
            'a claude verifier': dict(agents={'v1': 'idle claude'}, panes={'p1': 's1', 'p2': 'v1'}, pair={'verifier': {'kind': 'claude', 'name': 'v1', 'pane_id': 'p2'}}, clock=True),
        }
        out = {name: self.prime_both('vc-' + re.sub(r'\W+', '-', name), bash, '', shim=True, **spec) for name, spec in cases.items()}
        self.assertIn('cleaned', out['a Prime verifier whose cleanup succeeds'][1])
        self.assertIn('"closed": true', out['a Prime verifier whose cleanup succeeds'][3])
        self.assertEqual(out['a Prime verifier whose cleanup fails'][1], 'rc=5\n')
        self.assertIn('Prime verifier cleanup failed; its pane and scratch stay for inspection', out['a Prime verifier whose cleanup fails'][2])
        self.assertIn('agent prompt v1 /exit', out['a claude verifier'][5])

    def test_the_prime_smoke_script_sources_pair_core_and_its_names_resolve(self):
        """tests/prime_herdr_smoke.py needs a real Prime, so only what can be proved here is: it sources pair-core.sh (which sources prime-agent.sh)
        with no stubs of the helpers that file defines, and in that shell `stop_prime_role` and `pycore` are functions."""
        text = (root / 'tests/prime_herdr_smoke.py').read_text()
        self.assertIn('skills/pstack-pair/scripts/pair-core.sh', text)
        self.assertNotRegex(text, r'(?m)^(field|json_update|agent_status)\(\) \{')
        script = re.search(r'script = """(.*?)"""', text, re.S).group(1)
        core = root / 'skills/pstack-pair/scripts/pair-core.sh'
        r = subprocess.run(['bash', '-c', script.split('stop_prime_role "$1"')[0] + 'type -t stop_prime_role pycore', 'smoke', 'unused', str(core)],
                           capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, 'function\nfunction\n'), r.stderr)

    def test_update_or_jq_waits_out_a_held_lock_in_its_jq_fallback(self):
        """The fallback (taken when the native mutate cannot apply) retries the held lock as update() does: the lock is held when the call starts and
        released at the first poll, where the first attempt used to die. No real wait: the poll is the test's own function."""
        st = self.path / 'lockwait'
        st.mkdir()
        (st / 'pair.json').write_text('{"a": 1}\n')
        lock = st / '.pair.lock'
        lock.mkdir()
        polls = []

        def release(seconds):
            polls.append(seconds)
            lock.rmdir()

        def refuses(doc):
            raise TypeError('only jq can apply this')

        # update() is made to refuse before it takes the lock, so the only wait is the fallback's own
        with mock.patch.object(store, 'update', side_effect=TypeError), mock.patch.object(store.time, 'sleep', release), \
                mock.patch.dict(os.environ, {'PAIR_LOCK_POLL_S': '0.25'}):
            store.update_or_jq(str(st), refuses, '.b = 2')
        self.assertEqual(polls, [0.25])
        self.assertEqual(json.loads((st / 'pair.json').read_text()), {'a': 1, 'b': 2})
        self.assertFalse(lock.exists())
        # a lock that is never released still ends in the die, after PAIR_LOCK_TRIES polls
        lock.mkdir()
        with mock.patch.object(store, 'update', side_effect=TypeError), mock.patch.object(store.time, 'sleep', lambda s: None), \
                mock.patch.dict(os.environ, {'PAIR_LOCK_TRIES': '3'}):
            with self.assertRaises(Die) as caught:
                store.update_or_jq(str(st), refuses, '.c = 3')
        self.assertIn('pair.json lock held for 10s', caught.exception.message)

    def test_the_stop_prime_role_shim_returns_a_signal_status_and_exits_for_a_die(self):
        """A pycore killed by a signal (143 for TERM) comes back from the shim as 143, not 43; a die the core reports as 100 plus its code (103) still
        ends the shell with that code; 0 and 1 come back as returns."""
        snippet = '''
pycore() { sh -c 'kill -TERM $$'; }
stop_prime_role "$store" sidekick || echo "signal rc=$?"
pycore() { sh -c 'exit 103'; }
( stop_prime_role "$store" sidekick || echo "die returned rc=$?" ); echo "die exit rc=$?"
pycore() { return 1; }
stop_prime_role "$store" sidekick || echo "answer rc=$?"
pycore() { return 0; }
stop_prime_role "$store" sidekick && echo "stopped"
'''
        top, st, env, fill = self.ab_world('shim-signal', {'master': None})
        script = (f'set -euo pipefail\nskill_root={root / "skills/pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\n'
                  f'roles=(sidekick); store_dirs=(plans); slug_max=21\n. {root / "skills/pstack-pair/scripts/pair-core.sh"}\nstore={shlex.quote(str(st))}\nset +e\n{snippet}')
        r = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(r.stdout, 'signal rc=143\ndie exit rc=3\nanswer rc=1\nstopped\n')

    def test_close_role_matches_the_bash_at_the_phase_3_base_with_the_shims(self):
        """close_role, the shims' remaining pair-core caller (kitchen.sh `close` runs it): 04d2c13's function against the working tree's."""
        path_session = {'kind': 'path', 'value': '/t/one.jsonl'}
        bash = 'close_role "$store" sidekick || echo "returned rc=$?"; echo "after"'
        cases = {
            'a prime-agent role': dict(),
            'a prime-agent role whose cleanup fails': dict(fake={'prime-stop-fail': ''}),
            'a prime-agent role already absent': dict(agents={}, sidekick={'session': path_session}),
            'a prime-agent role whose shell never takes the foreground': dict(fake={'prime-client-stays': ''}, clock=True),
            'a claude role': dict(agents={'s1': 'idle claude'}, sidekick={'kind': 'claude'}),
            'a codex role': dict(agents={'s1': 'idle codex'}, sidekick={'kind': 'codex'}),
            'a claude role already absent': dict(agents={}, sidekick={'kind': 'claude'}, clock=True),
            'a role already closed': dict(sidekick={'closed': True}),
            'a role never spawned': dict(sidekick={'pane_id': None}),
        }
        out = {name: self.prime_both('cr-' + re.sub(r'\W+', '-', name), bash, '', shim=True, **spec) for name, spec in cases.items()}
        self.assertIn('closed sidekick s1 (p1)', out['a prime-agent role'][1])
        self.assertIn('"closed": true', out['a prime-agent role'][3])
        self.assertIn('returned rc=1', out['a prime-agent role whose cleanup fails'][1])
        self.assertIn('agent prompt s1 /exit', out['a claude role'][5])
        self.assertIn('agent prompt s1 /quit', out['a codex role'][5])

    def rotation_world(self, kind='devin', **sidekick):
        """A rotation-due sidekick of this kind in p1, the master in p0; the bootstrap writes its ready file."""
        env = {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PRIME_COMMAND_TIMEOUT': '2'}
        entry = {'name': 's1', 'kind': kind, 'generation': 1, 'pane_id': 'p1', 'rotation_required': True, 'start_args': ['--model', 'm', 'a b'], **sidekick}
        world = dict(pair={'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': entry}, panes={'p0': 'm1', 'p1': ''}, agents={'s1': f'idle {kind}'},
                     hook=self.READY_HOOK, env=env)
        if kind == 'prime-agent':
            world['panes'] = {'p0': 'm1', 'p1': 's1'}
            world['fake'] = {'prime-sessions.json': json.dumps([{'activeSessionId': 'live-1', 'sessionId': 'sess-1', 'sessionFile': '/t/one.jsonl', 'name': 's1'}]),
                             'sessions/s1': '/t/one.jsonl'}
            world['pair']['sidekick']['session'] = {'kind': 'path', 'value': '/t/one.jsonl'}
        return world

    def rotate_cases(self):
        world = self.rotation_world
        run = lambda w, *a: {**w, 'args': ['rotate', '{st}', *a]}
        plain = world()
        quiet = {**plain, 'hook': None}
        return {
            'no arguments': {**plain, 'args': ['rotate']},
            'too many arguments': run(plain, 'x'),
            'an unknown store': {**plain, 'args': ['rotate', '{T}/nope']},
            'a kind that is not fresh per brief': run(world('claude')),
            'no rotation required': run(world(rotation_required=False)),
            'a rotation flag that is not true': run(world(rotation_required='yes')),
            'a rotation flag that is the string true': run(world(rotation_required='true')),
            'no recorded start args': run(world(start_args=None)),
            'start args that are not an array': run(world(start_args='x')),
            'a paused run': {**run(plain), 'files': {'paused': 'lunch\n'}},
            'an idle devin': run(plain),
            'a done devin': run({**plain, 'agents': {'s1': 'done devin'}}),
            'an absent devin': run({**plain, 'agents': {}}),
            'a working devin': run({**plain, 'agents': {'s1': 'working devin'}}),
            'a blocked devin': run({**plain, 'agents': {'s1': 'blocked devin'}}),
            'an idle pi': run(world('pi')),
            'an idle devin whose start args hold an empty and a newline argument': run(world(start_args=['', 'x\ny', '--m'])),
            'an exit prompt that fails with a tolerated error, and an agent that stays': run({**plain, 'prompt_error': '{"error":{"code":"agent_not_found"}}\n', 'clock': True}),
            'an exit prompt that fails with a stalled prompt': run({**plain, 'prompt_error': '{"error":{"code":"agent_prompt_stalled"}}\n', 'clock': True}),
            'a tolerated agent_not_found and an agent that leaves': run({**plain, 'prompt_error': '{"error":{"code":"agent_not_found"}}\n', 'prompt_error_exits': True}),
            'a tolerated agent_prompt_stalled and an agent that leaves': run({**plain, 'prompt_error': '{"error":{"code":"agent_prompt_stalled"}}\n', 'prompt_error_exits': True}),
            'a tolerated not_found and an agent that leaves': run({**plain, 'prompt_error': 'xx not_found yy\n', 'prompt_error_exits': True}),
            'an untolerated error and an agent that leaves': run({**plain, 'prompt_error': '{"error":{"code":"boom"}}\n', 'prompt_error_exits': True}),
            'an exit prompt that fails with an untolerated error': run({**plain, 'prompt_error': '{"error":{"code":"boom"}}\n'}),
            'an exit prompt error that only contains a tolerated word': run({**plain, 'prompt_error': 'weird not_found text\n', 'clock': True}),
            'a sidekick that does not exit in time': run({**plain, 'prompt_error': '{"error":{"code":"not_found"}}\n', 'clock': True}),
            'a spawn that returns 4': run(quiet),
            'a spawn that dies': run({**plain, 'fake': {'start-fail': ''}}),
            'a spawn that returns 3': run({**plain, 'promptfail': True}),
            'a master that is a string': run({**plain, 'pair': {**plain['pair'], 'master': 'x'}}),
            'a master that is an array': run({**plain, 'pair': {**plain['pair'], 'master': [1]}}),
            'a prime-agent sidekick': run(world('prime-agent')),
            'a prime-agent sidekick that is done': run({**world('prime-agent'), 'agents': {'s1': 'done prime-agent'}}),
            'a prime-agent sidekick already absent': run({**world('prime-agent'), 'agents': {}}),
            'a prime-agent sidekick whose cleanup fails': run({**world('prime-agent'), 'fake': {**world('prime-agent')['fake'], 'prime-stop-fail': ''}}),
            'a prime-agent sidekick absent whose cleanup fails': run({**world('prime-agent'), 'agents': {}, 'fake': {**world('prime-agent')['fake'], 'prime-stop-fail': ''}}),
            'a prime-agent sidekick with no recorded session': run(world('prime-agent', session=None)),
            'a prime-agent sidekick whose shell never takes the foreground': run({**world('prime-agent'), 'clock': True,
                                                                                  'fake': {**world('prime-agent')['fake'], 'prime-client-stays': ''}}),
            'a working prime-agent sidekick': run({**world('prime-agent'), 'agents': {'s1': 'working prime-agent'}}),
        }

    def failover_cases(self):
        env = {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PRIME_COMMAND_TIMEOUT': '2'}

        def world(kind='claude', fallback=None, **sidekick):
            fb = {'kind': 'devin', 'args': ['--model', 'm', 'a b']} if fallback is None else fallback
            entry = {'name': 's1', 'kind': kind, 'generation': 1, 'pane_id': 'p1', 'fallback': fb, **sidekick}
            w = dict(pair={'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': entry}, panes={'p0': 'm1', 'p1': ''}, agents={'s1': f'idle {kind}'},
                     hook=self.READY_HOOK, env=env)
            if kind == 'prime-agent':
                w['panes'] = {'p0': 'm1', 'p1': 's1'}
                w['fake'] = {'prime-sessions.json': json.dumps([{'activeSessionId': 'live-1', 'sessionId': 'sess-1', 'sessionFile': '/t/one.jsonl', 'name': 's1'}]),
                             'sessions/s1': '/t/one.jsonl'}
                w['pair']['sidekick']['session'] = {'kind': 'path', 'value': '/t/one.jsonl'}
            return w
        run = lambda w, *a: {**w, 'args': ['failover', '{st}', *a]}
        plain = world()
        return {
            'no arguments': {**plain, 'args': ['failover']},
            'an unknown option': run(plain, '--bogus'),
            'a reason with no value': run(plain, '--reason'),
            'an unknown store': {**plain, 'args': ['failover', '{T}/nope']},
            'no fallback recorded': run(world(fallback={})),
            'no fallback recorded (null)': run(world(fallback=None, **{}) | {'pair': {'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': {'name': 's1', 'kind': 'claude', 'pane_id': 'p1'}}}),
            'already on the fallback': run(world('devin', fallback={'kind': 'devin'})),
            'an idle sidekick': run(plain),
            'an idle sidekick with a reason': run(plain, '--reason', 'out of quota'),
            'a done sidekick': run({**plain, 'agents': {'s1': 'done claude'}}),
            'an absent sidekick': run({**plain, 'agents': {}}),
            'a blocked sidekick': run({**plain, 'agents': {'s1': 'blocked claude'}}),
            'a working sidekick without --force': run({**plain, 'agents': {'s1': 'working claude'}}),
            'a working sidekick with --force': run({**plain, 'agents': {'s1': 'working claude'}, 'clock': True}, '--force'),
            'a fallback without args': run(world(fallback={'kind': 'codex'})),
            'fallback args with an empty and a newline argument': run(world(fallback={'kind': 'codex', 'args': ['', 'x\ny']})),
            'a sidekick that does not exit in time': run({**plain, 'prompt_error': '{"error":{"code":"x"}}\n', 'clock': True}),
            'a running dispatch with no report': run(plain),
            'a running dispatch with its report': {**run(plain), 'files': {'reports/001-x.md': '# R\n'}},
            'a running dispatch whose brief is an attempt': {**run(plain), 'pair': {**plain['pair'], 'dispatch': {'brief': '{st}/briefs/001-x-a2.md', 'generation': 1}}},
            'no running dispatch': run({**plain, 'pair': {**plain['pair'], 'dispatch': None}}),
            'a spawn that returns 4': run({**plain, 'hook': None}),
            'a spawn that returns 3': run({**plain, 'promptfail': True}),
            'a spawn that dies': run({**plain, 'fake': {'start-fail': ''}}),
            'a failover record that jq refuses': run(world(failovers='x')),
            'failovers already recorded': run(world(failovers=[{'from': 'a', 'to': 'b', 'reason': 'r', 'at': 't'}])),
            'a prime-agent sidekick': run(world('prime-agent')),
            'a prime-agent sidekick whose cleanup fails': run({**world('prime-agent'), 'fake': {**world('prime-agent')['fake'], 'prime-stop-fail': ''}}),
            'a prime-agent sidekick already absent': run({**world('prime-agent'), 'agents': {}}),
            'a working prime-agent sidekick with --force': run({**world('prime-agent'), 'agents': {'s1': 'working prime-agent'}, 'clock': True}, '--force'),
            'a prime-agent sidekick whose shell never takes the foreground': run({**world('prime-agent'), 'clock': True,
                                                                                  'fake': {**world('prime-agent')['fake'], 'prime-client-stays': ''}}),
            'a codex sidekick': run(world('codex')),
        }

    ROTATE_EXPECT = {
        # a jq that refuses the start's write ends a direct rotate (errexit on); inside kitchen.sh's $( ) it does not (the kitchen A/B below)
        'a master that is a string': (5, 'Cannot index string with string ("permission_mode")'), 'a master that is an array': (5, 'Cannot index array with string ("permission_mode")'),
        'a rotation flag that is the string true': (0, 'devin:ready'), 'a tolerated agent_not_found and an agent that leaves': (3, 'master\trotate'),
        # the wrapper fails the bootstrap prompt of the spawn too, so a rotation that got past the exit ends in spawn_role's 3
        'a tolerated agent_prompt_stalled and an agent that leaves': (3, 'devin:failed'),
        'a tolerated not_found and an agent that leaves': (3, 'master\trotate'), 'an untolerated error and an agent that leaves': (2, 'devin exit failed:'),
        'a kind that is not fresh per brief': (5, 'rotate is for a sidekick that starts fresh per brief'), 'no rotation required': (5, 'rotation requires a completed task'),
        'no recorded start args': (5, 'no recorded startup arguments'), 'a paused run': (5, 'the store is paused'), 'an idle devin': (0, 'devin:ready'),
        'a working devin': (5, 'sidekick s1 is working'), 'an exit prompt that fails with an untolerated error': (2, 'devin exit failed:'),
        'a sidekick that does not exit in time': (5, 'devin has not exited; inspect s1'), 'a spawn that returns 4': (4, 'devin:unready'),
        'a prime-agent sidekick': (0, 'devin:ready' if False else 'prime-agent:ready'), 'a prime-agent sidekick whose cleanup fails': (5, 'Prime session cleanup failed; no new session was started'),
        'an absent devin': (0, 'master\trotate'), 'a prime-agent sidekick whose shell never takes the foreground': (5, 'Prime client has not exited')}
    FAILOVER_EXPECT = {
        'no fallback recorded': (5, 'no fallback recorded; spawn the sidekick with --fallback KIND'), 'already on the fallback': (5, 'already runs the fallback (devin)'),
        'an idle sidekick': (0, 'failover: claude -> devin (requested)'), 'a working sidekick without --force': (5, 'is working; pair.sh stop'),
        'a working sidekick with --force': (0, 'send-keys s1 esc'), 'a sidekick that does not exit in time': (5, 'claude sidekick has not exited'),
        'a running dispatch with no report': (0, 'next: re-dispatch'), 'a spawn that returns 4': (4, 'devin:unready'),
        'a prime-agent sidekick whose cleanup fails': (5, 'Prime session cleanup failed; no fallback was started'),
        'an idle sidekick with a reason': (0, 'failover: claude -> devin (out of quota)'), 'a failover record that jq refuses': (5, 'cannot be added'), 'a prime-agent sidekick': (0, 'failover: prime-agent -> devin')}

    def lifecycle_part(self, kind, skill, part, parts, keep=None):
        cases = self.rotate_cases() if kind == 'rotate' else self.failover_cases()
        expect = self.ROTATE_EXPECT if kind == 'rotate' else self.FAILOVER_EXPECT
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            if n % parts != part or (keep is not None and name not in keep):
                continue
            got, want = self.phase3_ab(f'{kind[:2]}{n}', skill, spec)
            self.maxDiff = None
            if name in ('a reason with no value', 'no arguments') and 'line' in want['stderr']:
                self.assertRegex(want['stderr'], r'line [0-9]+: \$2: unbound variable')
                mask = lambda text: re.sub(r'\S*pair-core\.sh: line [0-9]+', '<core>: line N', text)
                got, want = dict(got, stderr=mask(got['stderr'])), dict(want, stderr=mask(want['stderr']))
            self.assertEqual(got, want, name)
            outcomes[name] = got
            if name in expect:
                code, needle = expect[name]
                self.assertEqual(got['rc'], code, name)
                self.assertIn(needle, got['stdout'] + got['stderr'] + '\n'.join(got['events']) + got['calls'] + got['files']['pair.json'], name)
        return outcomes

    def test_rotate_matches_the_bash_at_the_phase_3_base_part_0(self):
        self.lifecycle_part('rotate', 'pstack-pair', 0, 3)

    def test_rotate_matches_the_bash_at_the_phase_3_base_part_1(self):
        self.lifecycle_part('rotate', 'pstack-pair', 1, 3)

    def test_rotate_matches_the_bash_at_the_phase_3_base_part_2(self):
        self.lifecycle_part('rotate', 'pstack-pair', 2, 3)

    def test_rotate_through_the_kitchen_matches_the_bash_at_the_phase_3_base(self):
        names = {'rotate': ('an idle devin', 'a paused run', 'a prime-agent sidekick', 'a spawn that returns 4', 'a kind that is not fresh per brief'),
                 'failover': ('an idle sidekick', 'no fallback recorded', 'a working sidekick with --force', 'a spawn that returns 4', 'a running dispatch with its report')}['rotate']
        self.lifecycle_part('rotate', 'pstack-kitchen', 0, 1, keep=names)

    def test_failover_matches_the_bash_at_the_phase_3_base_part_0(self):
        self.lifecycle_part('failover', 'pstack-pair', 0, 3)

    def test_failover_matches_the_bash_at_the_phase_3_base_part_1(self):
        self.lifecycle_part('failover', 'pstack-pair', 1, 3)

    def test_failover_matches_the_bash_at_the_phase_3_base_part_2(self):
        self.lifecycle_part('failover', 'pstack-pair', 2, 3)

    def test_failover_through_the_kitchen_matches_the_bash_at_the_phase_3_base(self):
        names = {'rotate': ('an idle devin', 'a paused run', 'a prime-agent sidekick', 'a spawn that returns 4', 'a kind that is not fresh per brief'),
                 'failover': ('an idle sidekick', 'no fallback recorded', 'a working sidekick with --force', 'a spawn that returns 4', 'a running dispatch with its report')}['failover']
        self.lifecycle_part('failover', 'pstack-kitchen', 0, 1, keep=names)

    def test_kitchen_rotates_a_sidekick_before_a_brief_as_at_the_phase_3_base(self):
        """kitchen.sh `dispatch` calls cmd_rotate inside $( ) (rotate_if_due) and reads its output and status: a rotation that succeeds, one that
        fails with the die's status and message, one that is not due, and a spawn that returns 4."""
        toml = {'.agents/kitchen.toml': self.KITCHEN_TOML}
        brief = '# Brief 003-z\n\nplaybook: investigation\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/a.py\n\nmust not write:\n- lib/**\n'
        due = self.rotation_world()
        base = dict(due, args=['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', '20000'], files={'briefs/003-z.md': brief},
                    repo_files={'src/a.py': 'x', **toml}, report_after='1 {st}/reports/003-z.md partial')
        base['env'] = {**base['env'], 'KITCHEN_QUIET_POLL_S': '0', 'KITCHEN_QUIET_MAX_S': '2'}
        cases = {
            'a rotation that succeeds': base,
            'a rotation flag that is the string true': {**base, 'pair': {**base['pair'], 'sidekick': {**base['pair']['sidekick'], 'rotation_required': 'true'}}},
            'a master that is a string': {**base, 'pair': {**base['pair'], 'master': 'x'}},
            'a rotation that is not due': {**base, 'pair': {**base['pair'], 'sidekick': {**base['pair']['sidekick'], 'rotation_required': False}}},
            'a kind that is not fresh per brief': {**base, 'agents': {'s1': 'idle claude'}, 'pair': {**base['pair'], 'sidekick': {**base['pair']['sidekick'], 'kind': 'claude'}}},
            'a rotation whose exit prompt fails': {**base, 'prompt_error': '{"error":{"code":"boom"}}\n'},
            'a rotation that does not exit in time': {**base, 'prompt_error': '{"error":{"code":"not_found"}}\n', 'clock': True},
            'a rotation whose spawn returns 4': {**base, 'hook': None},
            'a paused run': {**base, 'files': {**base['files'], 'paused': 'lunch\n'}},
            'a prime-agent rotation': {**self.rotation_world('prime-agent'), **{k: base[k] for k in ('args', 'files', 'repo_files', 'report_after')}, 'env': base['env']},
        }
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            got, want = self.phase3_ab(f'kr{n}', 'pstack-kitchen', spec)
            self.maxDiff = None
            self.assertEqual(got, want, name)
            outcomes[name] = got
        self.assertIn('rotated: the devin sidekick starts this brief in a fresh session', outcomes['a rotation that succeeds']['stdout'])
        self.assertIn('rotating the sidekick before this brief failed:', outcomes['a rotation whose exit prompt fails']['stderr'])
        self.assertEqual(outcomes['a rotation whose exit prompt fails']['rc'], 2)
        self.assertEqual(outcomes['a rotation that does not exit in time']['rc'], 5)
        self.assertEqual(outcomes['a rotation whose spawn returns 4']['rc'], 4)

    def test_kitchen_finish_matches_the_bash_at_the_phase_3_base(self):
        """kitchen.sh `finish` asks the pair's report_ready (once the bash function core_report_ready, now the core's base hook) before it runs the
        brief's Verify commands: each way the pair's checks send the report straight to the core's finish, and a ready one whose Verify rerun starts."""
        brief = '# Brief 001-x\n\nplaybook: investigation\ncommit: no\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/a.py\n\n## Verify\n\n```bash\ntrue\n```\n'
        report = lambda status='done', head='{c3}': f'# R\n\nstatus: {status}\nhead: {head}\n'
        base = dict(args=['finish', '{st}', '{st}/reports/001-x.md'], repo_files={'src/a.py': 'x'}, files={'briefs/001-x.md': brief, 'reports/001-x.md': report()},
                    env={'KITCHEN_STEP_SLICE_S': '0', 'KITCHEN_STEP_POLL_S': '0'})
        steps = {'steps/001-x.tsv': '1\t{c2}\ts\t\n2\t{c3}\ts\t\n'}
        cases = {
            'a report that is not done': {**base, 'files': {**base['files'], 'reports/001-x.md': report('partial')}},
            'no dispatched brief': {**base, 'pair': {'dispatch': None}},
            'a report that is not the expected one': {**base, 'args': ['finish', '{st}', '{st}/reports/002-y.md'], 'files': {**base['files'], 'reports/002-y.md': report()}},
            'a report that does not exist': {**base, 'args': ['finish', '{st}', '{st}/reports/009-none.md']},
            'an open blocking note': {**base, 'files': {**base['files'], **steps, 'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}},
            'a head older than the last step': {**base, 'files': {**base['files'], **steps, 'reports/001-x.md': report(head='{c1}')}},
            'a head that is not a commit': {**base, 'files': {**base['files'], 'reports/001-x.md': report(head='{c3}x')}},
            'a ready report whose Verify rerun starts, then passes on the next finish': {**base, 'twice': True},
        }
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            got, want = self.phase3_ab(f'kf{n}', 'pstack-kitchen', spec)
            self.maxDiff = None
            # the Verify job's directory is named by a hash of the store's path, which differs between the two worlds
            mask = lambda o: json.loads(re.sub(r'(?<=/)[0-9a-f]{12}(?=[/\s"\\])', '<JOB>', json.dumps(o)))
            self.assertEqual(mask(got), mask(want), name)
            outcomes[name] = got
        ready = outcomes['a ready report whose Verify rerun starts, then passes on the next finish']
        self.assertEqual(ready['rc'], 4)
        self.assertIn('are still running', ready['stdout'])
        self.assertEqual(ready['second'][0], 0)
        self.assertIn('verify: pass', ready['second'][1])
        self.assertTrue(any(name.endswith('/rc') for name in ready['files']), 'the Verify job wrote no rc file inside the bounded poll: steps/jobs/*/rc is missing')
        self.assertTrue(any(name.endswith('/consumed') for name in ready['files']), 'the second finish took the first one\'s job')
        self.assertEqual(outcomes['an open blocking note']['rc'], 7)
        self.assertEqual(outcomes['a head older than the last step']['rc'], 7)
        self.assertEqual(outcomes['a report that is not done']['rc'], 0)

    def test_send_ab_kitchen_dispatch_and_routine_discuss(self):
        toml = {'.agents/kitchen.toml': self.KITCHEN_TOML}
        env = {'KITCHEN_QUIET_POLL_S': '0', 'KITCHEN_QUIET_MAX_S': '2'}
        brief = lambda headers='playbook: investigation\n': '# Brief 003-z\n\n' + headers + 'timebox: 45\n\n## Scope\n\nmay write:\n- src/a.py\n\nmust not write:\n- lib/**\n'
        base = dict(args=['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', '20000'], agents={'s1': 'idle claude'}, files={'briefs/003-z.md': brief()},
                    repo_files={'src/a.py': 'x', **toml}, env=env, report_after='1 {st}/reports/003-z.md partial')
        plan_files = {'plans/001-x.md': '# Plan\n', 'reviews/001-x.md': '# R\n\nverdict: agreed\n'}
        hook = '#!/bin/sh\ncase "$2" in *" BRIEF "*) printf "working claude\\n" >"$FAKE/agents/$1" ;; esac\n'
        cases = {
            'a routine brief to a report': base,
            'a routine brief with send-only': {**base, 'args': base['args'] + ['--send-only']},
            'an escalated brief with advice': {**base, 'files': {'briefs/003-z.md': brief('playbook: feature\nplan: {st}/plans/001-x.md\nrisk: escalated (a contract)\n'), **plan_files, 'advice/001-x.md': '# A\n'}},
            'an escalated brief with no advice': {**base, 'files': {'briefs/003-z.md': brief('playbook: feature\nplan: {st}/plans/001-x.md\nrisk: escalated (a contract)\n'), **plan_files}},
            'a landing brief': {**base, 'files': {'briefs/003-z.md': brief('playbook: opening-a-pr\n')}},
            'a brief that waits to steps': {**base, 'report_after': None, 'files': {'briefs/003-z.md': brief('playbook: investigation\nrisk: escalated (x)\n'), 'steps/003-z.tsv': '1\t{c1}\t5\t\tone\n'}},
            'a check-in the quiet wait swallows': {**base, 'report_after': None, 'hook': hook, 'args': ['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', '1000'],
                                                  'files': {'briefs/003-z.md': brief(), 'progress/003-z.md': '10:00 one\n'}},
            'a check-in with a flag': {**base, 'report_after': None, 'hook': hook, 'args': ['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', '1000'],
                                      'files': {'briefs/003-z.md': brief(), 'steers/003-z-s1.md': '# S\n', 'reports/003-z-s1.md': '# R\n\nstatus: object\n'}},
            'a brief that is missing': {**base, 'args': ['dispatch', '{st}', '{st}/briefs/009-none.md']},
            'the sidekick is absent': {**base, 'agents': {}},
            'a queued brief taken': {**base, 'files': {'briefs/003-z.md': brief(), 'queue': '{st}/briefs/003-z.md\n'}},
            'a paused store': {**base, 'files': {'briefs/003-z.md': brief(), 'paused': 'lunch\n'}},
            'the sidekick is blocked': {**base, 'agents': {'s1': 'blocked claude'}},
            'the sidekick is working': {**base, 'agents': {'s1': 'working claude'}},
            'an unfilled brief': {**base, 'files': {'briefs/003-z.md': brief().replace('src/a.py', '{{{{path}}}}')}},
            'another brief queued': {**base, 'files': {'briefs/003-z.md': brief(), 'queue': '{st}/briefs/002-y.md\n'}},
            'a discuss of a routine plan': dict(base, args=['discuss', '{st}', '{st}/plans/001-x.md', '--timeout', '20000'], files={'plans/001-x.md': '# Plan 001-x\n\nrisk: routine\n'},
                                                report_after='1 {st}/reports/001-x.md agree'),
            'a done report that is not ready': {**base, 'report_after': '1 {st}/reports/003-z.md done'},
            'a discuss of a plan with no risk': dict(base, args=['discuss', '{st}', '{st}/plans/001-x.md'], files={'plans/001-x.md': '# Plan 001-x\n'}),
        }
        outcomes = self.send_ab_run(cases, 'pstack-kitchen')
        self.assertEqual({n: o['rc'] for n, o in outcomes.items()}, {
            'a routine brief to a report': 0, 'a routine brief with send-only': 0, 'an escalated brief with advice': 0, 'an escalated brief with no advice': 6, 'a landing brief': 0,
            'a brief that waits to steps': 0, 'a check-in the quiet wait swallows': 4, 'a check-in with a flag': 4, 'a brief that is missing': 1, 'the sidekick is absent': 2,
            'a queued brief taken': 0, 'a paused store': 5, 'the sidekick is blocked': 3, 'the sidekick is working': 5, 'an unfilled brief': 1, 'another brief queued': 5, 'a discuss of a routine plan': 0, 'a done report that is not ready': 4, 'a discuss of a plan with no risk': 2})
        self.assertIn('written but not ready: its head: is not a commit', outcomes['a done report that is not ready']['stdout'])

    def dispatch_signalled(self, skills, number, group, hook_sub, skill='pstack-pair', ignored=(), capture=None):
        """`dispatch`, signalled while a fake-herdr hook holds it in `hook_sub` (the prompt of the delivery, or the wait that follows):
        (exit status, stdout, stderr, the store as it ends). Bounded as wait_signalled is."""
        spec = {'agents': {'s1': 'idle claude'}, 'repo_files': {'src/a.py': 'x', **({'.agents/kitchen.toml': self.KITCHEN_TOML} if skill == 'pstack-kitchen' else {})},
                'env': {'KITCHEN_QUIET_MAX_S': '3', 'KITCHEN_QUIET_POLL_S': '0'}, 'report_after': '1 {st}/reports/003-z.md',
                'files': {'briefs/003-z.md': '# Brief 003-z\n\nplaybook: investigation\ntimebox: 45\n\n## Scope\n\nmay write:\n- src/a.py\n'}}
        top, st, env, fill = self.ab_world(f'sigd-{len(list(self.path.iterdir()))}', spec)
        ready, release = top / 'ready', top / 'release'
        os.mkfifo(ready)
        os.mkfifo(release)
        release_fd = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(lambda: os.close(release_fd))
        hook = top / 'fake/once' / hook_sub
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f'#!/bin/sh\necho in >"{ready}"\nread x <"{release}"\n')
        hook.chmod(0o755)

        def default_signals():
            for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(each, signal.SIG_IGN if each in ignored else signal.SIG_DFL)

        # A wrapper around the fake herdr tells the test when a herdr call that outlived its shell is over (it may still be writing the report
        # the fake's report-after promises), so the store is read after it and not in a race with it.
        finished = top / 'finished'
        os.mkfifo(finished)
        finished_fd = os.open(finished, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(lambda: os.close(finished_fd))
        wrapbin = top / 'wrapbin'
        wrapbin.mkdir()
        (wrapbin / 'herdr').write_text(f'#!/bin/sh\n{root / "tests/pstack-pair/bin/herdr"} "$@"\nrc=$?\n[ "$1 $2" != "agent wait" ] || echo done >"{finished}"\nexit $rc\n')
        (wrapbin / 'herdr').chmod(0o755)
        env = {**env, 'PATH': f'{wrapbin}:{env["PATH"]}'}
        script = 'kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh'
        proc = subprocess.Popen([str(skills / skill / 'scripts' / script), 'dispatch', str(st), str(st / 'briefs/003-z.md'), '--timeout', '3000'], cwd=top / 'repo', env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=default_signals)

        def kill_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.addCleanup(kill_group)
        try:
            fd = os.open(ready, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not select.select([fd], [], [], 30)[0]:
                    self.fail(f'dispatch never reached herdr\'s {hook_sub}')
            finally:
                os.close(fd)
            norm_now = lambda text: re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text.replace(str(top), '<T>')))
            if capture is not None:
                capture['before'] = ({str(f.relative_to(st)): norm_now(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'},
                                     [norm_now(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()[1:]])
            (os.killpg if group else os.kill)(proc.pid, number)
            os.write(release_fd, b'x\n')
            try:
                status = proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                kill_group()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                self.fail(f'{number.name} did not end the dispatch')
        finally:
            try:
                os.write(release_fd, b'x\n')
            except OSError:
                pass
        if not select.select([proc.stdout], [], [], 10)[0]:
            kill_group()
            self.fail('the dispatch outlived its shell')
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            kill_group()
            proc.communicate(timeout=10)
            self.fail('a process kept the output open')
        if hook_sub == 'wait' and not group:
            # the herdr call the shell left behind finishes after its hook is released; wait for its last word
            self.assertTrue(select.select([finished_fd], [], [], 20)[0], 'the herdr call left behind never ended')
        norm = lambda text: re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text.replace(str(top), '<T>')))
        files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
        events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text().splitlines()[1:]]
        return status, self.mask_minutes(norm(out)), norm(err), files, events

    def test_send_ab_signals_during_the_wait_end_the_dispatch_as_they_end_the_bash(self):
        # After delivery a signal ends the dispatch with the signal, and the store is as the bash leaves it (the dispatch is recorded).
        for number, group in ((signal.SIGTERM, True), (signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGINT, True)):
            want = self.dispatch_signalled(self.base_skills(), number, group, 'wait')
            got = self.dispatch_signalled(root / 'skills', number, group, 'wait')
            self.assertEqual(got, want, (number.name, group))
            self.assertEqual(got[0], -number, (number.name, group))
            self.assertIn('send-brief', ' '.join(got[4]))

    def test_send_ab_signals_inherited_as_ignored_after_delivery(self):
        # Decided by running the bash: after `trap '' TERM INT HUP` and `trap - TERM INT HUP`, an inherited-ignored SIGINT is at its default
        # (a SIGINT in the wait kills the base) while an inherited-ignored SIGHUP stays ignored (the base finishes its wait). The port must do
        # the same. This differs from `wait`, which has no trap and so keeps an inherited ignore of SIGINT.
        # reports/ is left out of the comparison: whether the fake herdr's hook, which inherited the ignored signal, goes on to write its report
        # after it is released is the fake's own doing, not the dispatch's.
        strip = lambda r: (r[0], r[1], r[2], {k: v for k, v in r[3].items() if not k.startswith('reports/')}, r[4])
        for number, status in ((signal.SIGINT, -signal.SIGINT), (signal.SIGHUP, 0)):
            want = self.dispatch_signalled(self.base_skills(), number, True, 'wait', ignored=(number,))
            got = self.dispatch_signalled(root / 'skills', number, True, 'wait', ignored=(number,))
            self.assertEqual(strip(got), strip(want), number.name)
            self.assertEqual(got[0], status, number.name)

    def test_send_ab_kitchen_signal_to_the_shell_alone_during_the_wait(self):
        # kitchen.sh runs the dispatch in a command substitution, so the signalled shell and the dispatch are two processes. TERM kills the
        # shell at once, as in the base. The base's subshell then outlives it and finishes its wait, writing the store after the signal
        # (a report mark, a wake row); the exec'd Python follows its parent (PR_SET_PDEATHSIG), so the port leaves nothing running and the
        # store as it was when the signal came.
        capture = {}
        got = self.dispatch_signalled(root / 'skills', signal.SIGTERM, False, 'wait', skill='pstack-kitchen', capture=capture)
        self.assertEqual(got[0], -signal.SIGTERM)
        # reports/ is left out: the report is the fake herdr's own write, made when the test lets its hook go, not the dispatch's
        self.assertEqual(({k: v for k, v in got[3].items() if not k.startswith('reports/')}, got[4]), capture['before'])
        want = self.dispatch_signalled(self.base_skills(), signal.SIGTERM, False, 'wait', skill='pstack-kitchen')
        self.assertEqual(want[0], -signal.SIGTERM)
        self.assertEqual(want[1:3], got[1:3])

    def test_send_ab_signals_during_delivery_are_ignored_as_in_the_bash(self):
        # TERM, INT and HUP wait out the prompt: bash's `trap '' TERM INT HUP` discards them, so the dispatch goes on to its report.
        for number, group in ((signal.SIGTERM, False), (signal.SIGTERM, True), (signal.SIGHUP, True), (signal.SIGINT, True), (signal.SIGINT, False)):
            want = self.dispatch_signalled(self.base_skills(), number, group, 'prompt')
            got = self.dispatch_signalled(root / 'skills', number, group, 'prompt')
            self.assertEqual(got, want, (number.name, group))
            self.assertEqual(got[0], 0, (number.name, group))
            self.assertIn('report:', got[1])

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
            r = subprocess.run(['readlink', '-f', '--', path], capture_output=True, text=True, check=False, timeout=120)
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
        st = self.replies_world('r4')
        head = self.replies_shas[2]
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
        # `cut -c1-9` and `${head:0:9}` count characters in a UTF-8 locale and bytes in C
        wide = '\u00e9' * 12
        for name, kw in (('wide head in the last row', dict(rows=[steps3[0], row(2, wide, T0 + 20)], report='done', head='x')),
                         ('wide base from the dispatch', dict(rows=steps3, report='done', extra_pair={'dispatch': {'brief': f'{st}/briefs/001-x.md', 'head': wide}}))):
            lay(**kw)
            for locale_name in ('en_US.UTF-8', 'C'):
                with mock.patch.dict(os.environ, {'LC_ALL': locale_name}):
                    ours = replies.review_delta(str(st), str(st / 'reports/001-x.md'))
                # bytes, because `cut -c` in C can split a character
                r = subprocess.run(['bash', '-c', f'set -euo pipefail\nskill_root={self.base_skills() / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair; roles=(sidekick); '
                                    f'store_dirs=(plans); slug_max=21\n. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\nreview_delta {st} {st}/reports/001-x.md'],
                                   env={**self.env, 'LC_ALL': locale_name}, capture_output=True, check=True, timeout=120)
                self.assertEqual(ours.encode('utf-8', 'surrogateescape'), r.stdout, (name, locale_name))

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
            procs.append(subprocess.Popen(['sleep', '3600'], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        sh = subprocess.Popen(['sh', '-c', 'cat >/dev/null; true'], env={**envs['shell'], 'HERDR_SOCKET_PATH': sock}, stdin=subprocess.PIPE, start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: [(os.killpg(p.pid, 9), p.wait(timeout=10)) for p in [*procs, sh]])
        for since, sock_env in ((now - 3600, sock), (now - 3600, ''), (now + 5, sock), (now - 3600, '/elsewhere')):
            extra = {'HERDR_SOCKET_PATH': sock_env}
            ours = checkin.busy_command(pane, since, now + 130, env=extra)
            bash = self.bash_at(f'busy_command {pane} {since}', now + 130, extra)
            self.assertEqual(ours, bash, (since, sock_env))
        self.assertTrue(ours == '' or ours.endswith('m'))
        self.assertEqual(checkin.busy_command('', 0, now), self.bash_at('busy_command "" 0', now))
        only_shell = subprocess.Popen(['sh', '-c', 'read x'], env={**self.env, 'HERDR_PANE_ID': 'p-only-shell'}, stdin=subprocess.PIPE, start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (os.killpg(only_shell.pid, 9), only_shell.wait(timeout=10)))
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
        self.addCleanup(lambda: [(os.killpg(p.pid, 9), p.wait(timeout=10)) for p in procs])

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
            procs.append(subprocess.Popen(['sleep', '3600'], env={**self.env, 'HERDR_PANE_ID': pane}, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

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
                os.killpg(p.pid, 9), p.wait(timeout=10)
            procs.clear()
            reset()
            pair()
            build()
            # Back-date the dispatch so the sleeper started just now counts as started since it.
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
                                           env={**self.env, 'LC_ALL': locale_name}, capture_output=True, check=True, timeout=120)
                        self.assertEqual('\n'.join(ours).encode('utf-8', 'surrogateescape') + (b'\n' if ours else b''), r.stdout, (locale_name, text, before, after))
                    cut = subprocess.run(['bash', '-c', 'printf "%s\\n" "$1" | cut -c1-240', 'x', text], env={**self.env, 'LC_ALL': locale_name}, capture_output=True, check=True, timeout=120).stdout
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

    def test_provider_hint_keeps_the_bytes_of_a_screen_that_is_not_utf8_as_the_bash_does(self):
        # grep -o in a UTF-8 locale cannot match across a byte that is not UTF-8; in C every byte is a character
        screens = {'bad bytes around the match': b'quiet \xff\xfe rate limit hit caf\xc3\xa9 \xe9 end\n', 'bad byte after the match': b'rate limit \xff only\n',
                   'bad byte inside a word': b'rate\xfflimit then overloaded\n', 'only bad bytes': b'\xff\xfe\n'}
        for locale_name in ('en_US.UTF-8', 'C'):
            for name, screen in screens.items():
                outs = []
                for who in ('bash', 'py'):
                    st, env = self.provider_world(f'{who}-{locale_name}-{len(outs)}-{name}'.replace(' ', '-').replace('.', '-'), kind='claude', screen='')
                    (st.parent / 'fake/screen/s1').write_bytes(screen)
                    env['LC_ALL'] = locale_name
                    if who == 'bash':
                        r = subprocess.run(['bash', '-c', f'set -euo pipefail\nskill_root={self.base_skills() / "pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair; roles=(sidekick); '
                                            f'store_dirs=(plans); slug_max=21\n. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\nprovider_hint {st}'],
                                           env={**self.env, **env}, capture_output=True, check=True, timeout=120)
                        outs.append(r.stdout)
                    else:
                        with mock.patch.dict(os.environ, env):
                            outs.append(providers.provider_hint(str(st)).encode('utf-8', 'surrogateescape'))
                self.assertEqual(outs[1], outs[0], (locale_name, name))
        self.assertEqual(outs[0], b'')

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
                r = subprocess.run(['bash', '-c', '[ "$1" -eq ' + str(want) + ' ]', '_', text], capture_output=True, check=False, timeout=120)
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
                               env=self.env, capture_output=True, text=True, check=False, timeout=120)
            try:
                timeout, interval, send_only, rest = wait.wait_opts(args)
                ours = (0, '\x1f'.join([timeout, str(interval), '1' if send_only else '0']) + ''.join('\x1f' + x for x in rest), '')
            except Exit as e:
                ours = (e.code, '', e.stderr)
            # Masked: the file and line bash names are the working tree's for Python and e6e83e3's for the oracle. wait_opts is gone from the bash at HEAD
            # (Python names cmd_wait's line), which must never be 0.
            self.assertNotIn('line 0:', ours[2], args)
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
                r = subprocess.run(['bash', '-c', script, '_', out, err], env={**env, 'PATH': f'{fake}:{env["PATH"]}'}, capture_output=True, text=True, check=False, timeout=120)
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

    GATE_PLAYBOOKS = ('investigation', 'feature', 'bug-fix', 'refactoring', 'perf-issue', 'pstack-tdd', 'prototype')

    def gate_bash(self, skill, snippet, store_dir):
        """snippet run with the e6e83e3 pair-core.sh, plus the variant's bash gate as its pair.sh or consultant-core.sh defines it."""
        skills = self.base_skills()
        # the oracle tree holds the gate bodies the working tree has deleted
        self.assertRegex((skills / 'pstack-pair/scripts/pair-core.sh').read_text(), r'(?m)^require_agreed_plan\(\) \{')
        extra = ''
        if skill in ('pstack-trio', 'pstack-kitchen'):
            self.assertRegex((skills / 'pstack-trio/scripts/consultant-core.sh').read_text(), r'(?m)^plan_gate_extra\(\) \{')
            extra = f'. {skills / "pstack-trio/scripts/consultant-core.sh"}\n'
        elif skill == 'pstack-pair-guided':
            text = (skills / 'pstack-pair-guided/scripts/pair.sh').read_text()
            extra = re.search(r'^plan_gate_extra\(\) \{.*?^\}$', text, re.M | re.S).group(0) + '\n'
        script = (f'set -euo pipefail\nskill_root={skills / skill}; PAIR_SKILL={skill}; state_root=/s; store_label=Pair\nroles=(sidekick consultant); store_dirs=(plans advice); slug_max=21\n'
                  f'. {skills / "pstack-pair/scripts/pair-core.sh"}\n{extra}{snippet}')
        r = subprocess.run(['bash', '-c', script], env=self.env, capture_output=True, text=True, check=False, cwd=store_dir, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def gate_python(self, skill, call, store_dir):
        ctx = Context(skill, str(root / 'skills' / skill), '/s', 'Pair', ('sidekick', 'consultant'), ('plans', 'advice'), 21, '')
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.chdir(store_dir), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                call(ctx)
            code = 0
        except Die as e:
            code, err = e.code, io.StringIO(err.getvalue() + f'pair.sh: {e.message}\n')
        return code, out.getvalue(), err.getvalue()

    def gate_world(self, name, playbook='feature', plan='{plan}', plan_text='# Plan\n\nscale: small\n', review=None, advice=None, extra_review=None, relative=False):
        """A store with a brief naming the plan, the plan file, its review and its advice, as the case lays them out."""
        top = self.path / name
        for d in ('briefs', 'plans', 'reviews', 'advice'):
            (top / d).mkdir(parents=True)
        plan_path = top / 'plans/001-x.md'
        if plan_text is not None:
            plan_path.write_text(plan_text)
        shown = os.path.relpath(plan_path, top / 'briefs') if relative else plan_path
        header = f'playbook: {playbook}\n' + (f'plan: {plan.format(plan=shown)}\n' if plan is not None else '')
        (top / 'briefs/002-y.md').write_text(f'# Brief\n\n{header}')
        if review is not None:
            (top / 'reviews/001-x.md').write_text(f'# Review\n\n{review}')
        if extra_review is not None:
            (top / 'reviews/001-a.md').write_text(f'# Review\n\n{extra_review}')
        if advice:
            (top / 'advice/001-x.md').write_text('# Advice\n')
        return top

    def test_plan_gate_matches_bash_in_every_variant(self):
        agreed = 'verdict: agreed\n'
        plain = [('investigation, no gate', dict(playbook='investigation', plan=None)), ('prototype, no gate', dict(playbook='prototype')),
                 ('plan missing from the brief', dict(plan=None)), ('plan: none', dict(plan='none')), ('plan file missing', dict(plan_text=None, review=agreed)),
                 ('no review', dict()), ('review not agreed', dict(review='verdict: revise\n')), ('review with no verdict', dict(review='status: x\n')),
                 ('agreed', dict(review=agreed)), ('agreed, with advice', dict(review=agreed, advice=True)),
                 ('bug-fix agreed', dict(playbook='bug-fix', review=agreed)), ('pstack-tdd without a plan', dict(playbook='pstack-tdd', plan='')),
                 ('perf-issue not agreed', dict(playbook='perf-issue', review='verdict: rejected\n')),
                 ('two reviews, the first by name is agreed', dict(review='verdict: revise\n', extra_review=agreed)),
                 ('two reviews, the first by name is not', dict(review=agreed, extra_review='verdict: revise\n'))]
        guided = [(f'scale {scale}, approval {approval}', dict(plan_text=f'# Plan\n\n{"scale: " + scale + chr(10) if scale else ""}', review=f'verdict: agreed\n{"approval: " + approval + chr(10) if approval else ""}'))
                  for scale in ('small', 'medium', 'large', '') for approval in ('', 'posted', 'human', 'later')]
        n = 0
        for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio'):
            for label, kw in plain + (guided if skill == 'pstack-pair-guided' else []):
                n += 1
                top = self.gate_world(f'gate{n}', **kw)
                brief = top / 'briefs/002-y.md'
                bash = self.gate_bash(skill, f'require_agreed_plan {top} {brief}', top)
                ours = self.gate_python(skill, lambda ctx: ctx.variant.require_agreed_plan(ctx, str(top), str(brief)), top)
                norm = lambda r: (r[0], r[1].replace(str(top), '<T>'), r[2].replace(str(top), '<T>'))
                self.assertEqual(norm(ours), norm(bash), (skill, label))
        # The plan is a path relative to the brief's directory in this world, and the commands run in the store: both sides must resolve
        # it from the same working directory.
        top = self.gate_world('gate-relative', review=agreed, relative=True)
        brief = top / 'briefs/002-y.md'
        for cwd in (top, top / 'briefs'):
            bash = self.gate_bash('pstack-pair', f'require_agreed_plan {top} {brief}', cwd)
            ours = self.gate_python('pstack-pair', lambda ctx: ctx.variant.require_agreed_plan(ctx, str(top), str(brief)), cwd)
            self.assertEqual(ours, bash, str(cwd))
        # The trio's advice gate, named: without advice it refuses with 6, with advice it passes.
        for name, advice, code in (('gate-trio-without', False, 6), ('gate-trio-with', True, 0)):
            top = self.gate_world(name, review=agreed, advice=advice)
            brief = top / 'briefs/002-y.md'
            self.assertEqual(self.gate_python('pstack-trio', lambda ctx: ctx.variant.require_agreed_plan(ctx, str(top), str(brief)), top)[0], code, name)

    def test_plan_gate_kitchen_passes_its_gate_as_an_argument(self):
        # kitchen.sh cannot be sourced in a harness, so these are the literal results of kitchen.sh require_agreed_plan (444-453), a body the
        # working tree has deleted; the oracle for the literals is that function at e6e83e3.
        self.assertRegex((self.base_skills() / 'pstack-kitchen/scripts/kitchen.sh').read_text(), r'(?m)^require_agreed_plan\(\) \{')
        agreed = 'verdict: agreed\n'
        ctx = Context('pstack-kitchen', str(root / 'skills/pstack-kitchen'), '/s', 'Kitchen', ('sidekick', 'consultant'), ('plans',), 20, '')
        variant = ctx.variant
        before = dict(vars(variant))

        def gate(name, risk, expect, **kw):
            top = self.gate_world(name, **kw)
            brief = top / 'briefs/002-y.md'
            if risk:
                brief.write_text(brief.read_text() + f'risk: {risk}\n')
            out = self.gate_python('pstack-kitchen', lambda c: variant.require_agreed_plan(c, str(top), str(brief)), top)
            self.assertEqual((out[0], out[2].replace(str(top), '<T>')), expect, name)

        advice = 'pair.sh: plan <T>/plans/001-x.md has no consultant advice under advice/001-*; run pair.sh discuss so both agents respond 6\n'
        gate('kg1', 'escalated (a contract)', (6, advice), review=agreed)
        gate('kg2', 'escalated', (0, ''), review=agreed, advice=True)
        gate('kg3', 'escalated', (6, "pair.sh: playbook feature needs an agreed plan; brief has plan: none 6\n"), plan='none')
        gate('kg4', 'routine', (0, ''), plan='none')
        gate('kg5', 'routine', (0, ''), plan='')
        gate('kg6', None, (0, ''), plan='none')
        gate('kg7', 'routine', (6, "pair.sh: plan <T>/plans/001-x.md review verdict is 'revise', not agreed 6\n"), review='verdict: revise\n')
        gate('kg8', 'routine', (0, ''), review=agreed)
        gate('kg9', 'routine', (0, ''), playbook='investigation', plan=None)
        gate('kg10', 'routine', (6, "pair.sh: plan <T>/plans/001-x.md has no review; run pair.sh discuss and write reviews/001-<slug>.md with verdict: agreed 6\n"))
        self.assertEqual(vars(variant), before, 'the gate is an argument: nothing is stored on the instance')

    def test_plan_gate_scope_and_pause_match_bash(self):
        repo = self.git_repo()
        for name in ('README.md', 'src/a.py', 'src/sub/b.py', 'docs/guide.md', 'my, file.txt', 'top.md'):
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(name)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'files')
        (repo / 'src/untracked.py').write_text('x')
        (repo / 'ignored.log').write_text('x')
        (repo / '.gitignore').write_text('*.log\n')
        entries = [
            ('src/a.py', 'exists'), ('src/', 'dir'), ('src/**', 'glob'), ('**/x.md', 'no match, no dir'), ('**/guide.md', 'nested'), ('**/top.md', 'top-level'),
            ('docs/*.md', 'docs glob'), ('src/new_file.py', 'new file in an existing dir'), ('nodir/new.py', 'new file in a missing dir'), ('nodir/**', 'glob under a missing dir'),
            ('nodir/', 'missing dir'), ('src/sub/', 'nested dir'), ('src/sub/new/', 'new dir under a dir'), ('*.md', 'top glob'), ('*.nope', 'glob with no file'),
            ('[ab]rc/*', 'bracket glob'), ('src/?.py', 'question mark'), ('src/untracked.py', 'untracked file'), ('ignored.log', 'ignored file'), ('/abs/path', 'absolute'),
            ('README.md', 'top file'), ('newtop.md', 'new top file'), ('.', 'dot'), ('./src/a.py', 'dot slash'), ('src//a.py', 'double slash'),
        ]
        brief = self.path / 'scope-brief.md'
        variants = {
            'one entry each': [f'- {e}' for e, _ in entries],
            'comma list': ['- src/a.py, src/b.py'], 'and list': ['- src/a.py and docs/guide.md'], 'prose and': ['- README and docs'], 'and with one bare word': ['- src/a.py and docs'],
            'and with extensions': ['- a.ts and b.ts'], 'backquoted comma': ['- `my, file.txt`', '- `src/a.py`'], 'dash annotation': ['- src/a.py \u2014 the code', '- docs/ \u2013 docs', '- top.md -- top'],
            'paren note': ['- src/a.py (the code)', '- docs/guide.md (a guide) (second)'], 'trailing space': ['- src/a.py '], 'bare dash': ['-', '- '], 'entry after must not write': ['- src/a.py'],
            'comma in must-not': [], 'three and': ['- a.py and b.py and c.py'], 'and then a comma': ['- a.py and b.py, c.py'],
        }
        for label, lines in variants.items():
            text = '# Brief\n\n## Scope\n\nmay write:\n' + '\n'.join(lines) + '\n\nmust not write:\n' + ('- x, y\n' if label == 'comma in must-not' else '- lib/**\n') + '\n## Steps\n\n- one, two\n'
            brief.write_text(text)
            bash = self.gate_bash('pstack-pair', f'check_scope {brief} {repo}', repo)
            ours = self.gate_python('pstack-pair', lambda ctx: __import__('pstack_core.gates', fromlist=['x']).check_scope(str(brief), str(repo)), repo)
            self.assertEqual(ours, bash, label)
        for e, label in entries:
            tracked = subprocess.run(['git', '-C', str(repo), 'ls-files', '-co', '--exclude-standard'], capture_output=True, text=True, check=True, timeout=120).stdout.rstrip('\n')
            from pstack_core import gates
            for tr in (tracked, ''):
                bash = subprocess.run(['bash', '-c', f'set -euo pipefail\nskill_root=/x; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair\nroles=(sidekick); store_dirs=(plans); slug_max=21\n. {self.base_skills() / "pstack-pair/scripts/pair-core.sh"}\nif scope_entry_exists "$1" "$2" "$3"; then echo yes; else echo no; fi', '_', str(repo), e, tr],
                                      env=self.env, capture_output=True, text=True, check=True, timeout=120).stdout.strip()
                self.assertEqual('yes' if gates.scope_entry_exists(str(repo), e, tr) else 'no', bash, (e, label, bool(tr)))
        for reason in (None, 'lunch', '', 'two words'):
            st = self.path / f'paused-{reason}'
            st.mkdir()
            if reason is not None:
                (st / 'paused').write_text(reason + '\nmore\n')
            bash = self.gate_bash('pstack-pair', f'require_not_paused {shlex.quote(str(st))}', repo)
            ours = self.gate_python('pstack-pair', lambda ctx: __import__('pstack_core.gates', fromlist=['x']).require_not_paused(str(st)), repo)
            self.assertEqual((ours[0], ours[1], ours[2].replace(str(st), '<S>')), (bash[0], bash[1], bash[2].replace(str(st), '<S>')), reason)

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
        r = subprocess.run([sys.executable, '-I', '-c', code, str(scripts)], capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual((r.stdout, r.returncode), ('lazy\n', 0), r.stderr)
        names = {}
        for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen'):
            ctx = Context(skill, str(root / 'skills' / skill), '/s', 'Pair', (), (), 21, '')
            self.assertIsNone(ctx._variant)
            names[skill] = [c.__name__ for c in type(ctx.variant).__mro__[:-1]]
            self.assertIs(ctx.variant, ctx.variant)
        self.assertEqual(names, {'pstack-pair': ['Variant'], 'pstack-pair-guided': ['GuidedVariant', 'Variant'],
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
            git = subprocess.run(['git', '-C', str(root), 'archive', LEGACY, 'skills'], capture_output=True, check=False, timeout=120)
            if git.returncode != 0:
                raise unittest.SkipTest(f'{LEGACY} is not in this checkout')
            subprocess.run(['tar', '-x', '-C', cls.legacy_tmp.name], input=git.stdout, check=True, timeout=120)
            cls.legacy_dir = Path(cls.legacy_tmp.name)
        return cls.legacy_dir / 'skills/pstack-pair/scripts/pair.sh'

    def run_pair(self, *args, script=None, extra_env=None):
        env = {**self.env, 'FAKE': str(self.path), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0', 'HERDR_SOCKET_PATH': str(self.path / 'herdr.sock'),
               'XDG_STATE_HOME': str(self.path / 'state'), 'HOME': str(self.path), **(extra_env or {})}
        return subprocess.run([str(script or scripts / 'pair.sh'), *args], env=env, capture_output=True, text=True, check=False, timeout=120)

    def test_init_refuses_under_the_verifier_role_before_any_herdr_call_or_store_write(self):
        refusal = 'init refuses under PSTACK_KITCHEN_ROLE=verifier: it renames the calling pane, and the kitchen would lose its verifier'
        r = self.run_pair('init', 'demo', extra_env={'PSTACK_KITCHEN_ROLE': 'verifier'})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (2, '', f'pair.sh: {refusal}\n'))
        calls = self.path / 'calls.log'
        self.assertNotIn('agent rename', calls.read_text() if calls.exists() else '')
        self.assertFalse((self.path / 'state/pstack/pair/demo').exists())
        self.assertEqual(self.run_pair('init', 'demo', extra_env={'PSTACK_KITCHEN_ROLE': 'sidekick'}).returncode, 0)

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

    def test_new_steer_counts_the_steers_bash_tests_with_e_so_a_dangling_symlink_does_not_count(self):
        # d06a932's `[ -e "$f" ] || continue` is false for a link whose target is gone; the glob alone still lists it
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = self.path / 'state/pstack/pair/demo'
        self.run_pair('new-brief', str(st), 'one')
        outcomes = []
        for label, setup in (('only a dangling link', ()), ('one real steer and a dangling link', ('s1',)), ('two real steers and a dangling link', ('s1', 's2'))):
            for f in (st / 'steers').iterdir():
                f.unlink()
            for k in setup:
                (st / f'steers/001-one-{k}.md').write_text('supersedes: none\n')
            (st / 'steers/001-one-s9.md').symlink_to(self.path / 'nowhere')
            got = {}
            for who, script in (('port', None), ('bash', self.legacy_pair())):
                r = self.run_pair('new-steer', str(st), "001", script=script)
                got[who] = (r.returncode, r.stdout, r.stderr)
                if r.stdout.strip():
                    Path(r.stdout.strip()).unlink()
            self.assertEqual(got['port'], got['bash'], label)
            outcomes.append(got['bash'][:1] + (got['bash'][1].rsplit('/', 1)[-1].strip(),))
        self.assertEqual(outcomes, [(0, '001-one-s1.md'), (0, '001-one-s2.md'), (7, '')])

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


    @unittest.skipUnless(any(l.lower().replace('-', '') == 'en_us.utf8' for l in subprocess.run(['locale', '-a'], capture_output=True, text=True, timeout=120).stdout.split()),
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
        r = subprocess.run([str(script_root / 'skills' / variant / 'scripts/pair.sh'), *args], env=env, capture_output=True, text=True, check=False, timeout=120)
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
                code, out, err = self.status_pair('pstack-pair', tree, 'status', str(st), keep_order=True)
                saved = re.sub(r'generated: \S+', 'generated: T', (st / 'status.md').read_text()) if (st / 'status.md').exists() else None
                results[name] = (code, out, err, saved)
            if make_dir:
                self.assertEqual(results['legacy'], results['port'], label)
            else:
                # The legacy `{ ... } | tee` interleaves tee's error with the die mid-line under load, so the legacy side is compared with the port as
                # the characters of its stderr; the port's own lines are compared exactly, against the text they should be.
                chars = lambda r: (r[0], r[1], sorted(''.join(r[2])), r[3])
                self.assertEqual(chars(results['legacy']), chars(results['port']), label)
                self.assertEqual(results['port'][2], [f'tee: {st}/status.md: No such file or directory',
                                                      f'pair.sh: no pair.json in {st}; run: pair.sh init <slug>',
                                                      'jq: error: Could not open file : No such file or directory'], label)
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

        real_sleep = time.sleep

        def holder_finishes(seconds):
            # store.time is the time module: subprocess.run(timeout=...) polls with time.sleep too, and must get the real one.
            if waits:
                return real_sleep(seconds)
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

    def test_update_whose_document_cannot_be_serialised_leaves_no_pair_json_tmp_and_frees_the_lock(self):
        st = self.fixture_store()
        before = (st / 'pair.json').read_bytes()
        with self.assertRaises(TypeError):
            store.update(str(st), lambda doc: doc.update(bad={1, 2}))
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
        return subprocess.run(['jq', '.'], input=text, capture_output=True, text=True, check=True, timeout=120).stdout

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
                want = subprocess.run(['jq', '--arg', 'id', 'x', program], input=text, capture_output=True, text=True, check=True, timeout=120).stdout
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
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True, timeout=120).stdout.strip()
        git('init', '-q', '-b', 'main')
        (repo / 'a.txt').write_text('one\n')
        (repo / 'sub/b.txt').write_text('b\n')
        git('add', '-A')
        git('commit', '-q', '-m', 'init')
        (repo / 'a.txt').write_text('one\ntwo\n')
        (repo / 'sub/new.txt').write_text('untracked\n')
        os.chmod(repo / 'sub/new.txt', 0o755)
        if jj:
            subprocess.run(['jj', 'git', 'init', '--colocate', str(repo)], env=env, capture_output=True, check=True, timeout=120)
        script = tree / 'skills/pstack-pair/scripts/pair.sh'
        subprocess.run([str(script), 'init', 'demo'], cwd=repo, env=env, capture_output=True, check=True, timeout=120)
        store = xdg / 'pstack/pair/demo'
        (store / 'pair.json').write_text(json.dumps({'git_root': str(repo), 'scratch': []}, indent=2) + '\n')
        return top, repo, store, script, env, git

    def run_scratch(self, label, top, script, env, store, *args):
        r = subprocess.run([str(script), 'scratch', str(store), *args], env=env, capture_output=True, text=True, check=False, timeout=120)
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
        proc = subprocess.Popen(['bash', '-c', harness], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        parts = stdout.split('\x1f')[1:]
        self.assertEqual(len(parts), 200, stderr[-500:])
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

    # Permitted difference: the bash at d06a932 never returns on a note whose id is not a number (`master note 001-alpha a:blocking`):
    # `for (i = noted[u] + 1; i <= nk[1]; i++)` compares `i <= "a"` as strings, which is always true. The port skips the stall pairing
    # for a note id that is not a number.
    def test_metrics_returns_on_a_note_whose_id_is_not_a_number(self):
        st = self.path / 'hang'
        st.mkdir()
        (st / 'events.tsv').write_bytes((root / 'tests/fixtures/metrics-hang-events.tsv').read_bytes())
        self.env['PAIR_METRICS_NOW'] = '1700022613'
        r = subprocess.run([sys.executable, '-I', str(scripts / 'pair.py'), *core_args(), 'metrics', str(st)],
                           env=self.env, capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertEqual(r.stdout, (
            'wall: 376m from first event to now\n'
            'sidekick: busy 284m (75% of wall), idle 0m across 0 gaps; 1 briefs (median 2m), 3 plan responses (157m)\n'
            'queue: 4 briefs queued, 0 taken by the sidekick; 0 started the moment a report landed\n'
            'consultant: busy 229m (60% of wall); 1 consults (229m), plan advice 0m\n'
            'master: 9 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'failovers: 2 (devin:rate limit, devin:rate limit)\n'
            'steps: 3 committed, 4 notes (2 blocking); step to note median 0m\n'
            'verdicts: agreed 2 revise 1 reject 1\n'
            'briefs:\n'
            '  - 2m (stopped)\n'
            '  running: 003-gamma for 126m\n'))

    def test_metrics_gives_a_legacy_header_store_the_same_output_as_the_headerless_one(self):
        self.assertEqual(self.metrics_of(LEGACY_HEADER + '\n' + self.FIRST_EVENTS), self.metrics_of(self.FIRST_EVENTS))

    ENTRIES = ('pstack-pair/scripts/pair.sh', 'pstack-pair-guided/scripts/pair.sh', 'pstack-trio/scripts/pair.sh', 'pstack-kitchen/scripts/kitchen.sh')
    CORE = 'pstack-pair/scripts/pair-core.sh'
    CONSULTANT_CORE = 'pstack-trio/scripts/consultant-core.sh'
    CONSULTANT_ENTRIES = ('pstack-trio/scripts/pair.sh', 'pstack-kitchen/scripts/kitchen.sh')
    HELPER_FILES = (CORE, 'pstack-pair/scripts/prime-agent.sh')
    PORTED = ('status', 'metrics', 'permission', 'init', 'stop', 'steer', 'log', 'notes', 'note', 'new_note', 'progress', 'new_plan', 'new_brief', 'new_steer', 'pause', 'resume', 'scratch',
              'finish', 'notify', 'report', 'next', 'wait', 'queue', 'step', 'dispatch', 'discuss', 'spawn')
    # bash helpers that have a Python twin (pstack_core), under the twin's name
    TWINS = ('event', 'header_field', 'next_seq', 'paused_reason',
             'new_from_template', 'steps_file', 'trunk_ref', 'range_floor', 'require_filled',
             'agent_status', 'pane_busy', 'pane_text', 'agent_kind', 'latest_report', 'queued_brief', 'json_update', 'pair_file',
             'expected_report', 'fresh_per_brief', 'file_mtime',
             'scope_entries',
             'busy_command', 'agent_session_error', 'muse_session_error', 'prime_role_error',
             'record_prime_session', 'require_not_paused',
             'require_fresh_session', 'submit_typed',
             'iso_epoch', 'in_herdr', 'latest_advice', 'detect_permission_mode',
             'permission_args', 'trust_args', 'mcp_args', 'has_mcp_arg', 'has_trust_arg', 'has_permission_arg', 'has_preflight', 'preflight_kind',
             'pick_direction', 'new_pane', 'refuse_untrusted', 'await_file')
    TWIN_NAMES = {'json_update': 'update', 'latest_advice': 'latest', 'detect_permission_mode': 'detect'}
    # a variant's bash override of a hook, against the Python hook in that skill's variant.py
    # The bash bodies no entry script reaches. Phase 3 ended with none: a body that loses its last caller is deleted in the commit that orphans it,
    # and the guard has no baseline to add it to.
    KNOWN_DEAD = ()
    # kitchen.sh keeps the pair's commands under core_* names (copy_fn) and wraps them
    KITCHEN_ALIASES = {'core_init': 'pstack-pair/scripts/pair-core.sh:cmd_init', 'core_spawn': 'pstack-pair/scripts/pair-core.sh:cmd_spawn',
                       'core_dispatch': 'pstack-pair/scripts/pair-core.sh:cmd_dispatch', 'core_wait': 'pstack-pair/scripts/pair-core.sh:cmd_wait',
                       'core_queue': 'pstack-pair/scripts/pair-core.sh:cmd_queue', 'core_discuss': 'pstack-pair/scripts/pair-core.sh:cmd_discuss',
                       'core_step': 'pstack-pair/scripts/pair-core.sh:cmd_step', 'core_finish': 'pstack-pair/scripts/pair-core.sh:cmd_finish'}
    KITCHEN_WRAPS = ('init', 'dispatch', 'wait', 'queue', 'step', 'finish', 'discuss', 'spawn')

    def reach(self):
        cls = type(self)
        if cls.__dict__.get('_reach') is None:
            skills = root / 'skills'
            cls._reach = bash_reach.Reach([skills / e for e in self.ENTRIES], skills)
        return cls._reach

    def test_every_bash_function_runs_from_an_entry_script_or_is_known_dead(self):
        self.assertEqual(self.KNOWN_DEAD, ())
        reach = self.reach()
        dead, known = reach.dead(), set(self.KNOWN_DEAD)
        problems = [f'{k} has no caller from any entry script: delete its bash body' for k in sorted(dead - known)]
        problems += [f'{k} no longer exists: remove it from KNOWN_DEAD' if k not in reach.nodes else f'{k} is reachable again: remove it from KNOWN_DEAD'
                     for k in sorted(known - dead)]
        self.assertEqual(problems, [])

    def test_every_name_the_bash_calls_resolves_to_a_definition_or_a_command(self):
        """The reachability guard follows only calls to names it knows; a call to a deleted function would be dropped. Every command-position name in
        every definition and top-level line of each entry script is a definition of that entry, a shell builtin, or an external command."""
        reach = self.reach()
        missing = set()
        for entry in self.ENTRIES:
            path = (root / 'skills' / entry).resolve()
            table = reach.tables[path]
            for definition in table.values():
                missing |= {(entry, name, definition.key) for name in definition.calls if name not in table}
            missing |= {(entry, name, '<top>') for name, _ in reach.roots[path] if name not in table}
        names = sorted({name for _, name, _ in missing})
        # The fakes (herdr, ps, pi) come first on PATH, as in the other tests; a name that resolves to a file must be one of the external commands the
        # scripts are known to call, so a typo that happens to match a binary on this host fails by name.
        env = {**os.environ, 'PATH': f"{root / 'tests/pstack-pair/bin'}:{os.environ['PATH']}"}
        kind = {n: subprocess.run(['bash', '-c', 'type -t -- "$1"', 'x', n], capture_output=True, text=True, check=False, env=env).stdout.strip() for n in names}
        unknown = sorted(n for n in names if kind[n] == 'file' and n not in self.EXTERNAL_COMMANDS)
        self.assertEqual(unknown, [], 'external commands not listed in EXTERNAL_COMMANDS (add a command the scripts really call, fix a typo otherwise)')
        self.assertEqual(sorted(m for m in missing if not kind[m[1]]), [], 'calls to a name no definition, builtin or command provides')
        absent = sorted(n for n in self.EXTERNAL_COMMANDS if not shutil.which(n, path=env['PATH']))
        self.assertEqual(absent, [], 'EXTERNAL_COMMANDS names that do not resolve')

    # the external commands the entry scripts call, written out; the fakes under tests/pstack-pair/bin stand in for herdr and ps
    EXTERNAL_COMMANDS = frozenset((
        'awk', 'basename', 'cat', 'cksum', 'column', 'cp', 'cut', 'date', 'dirname', 'find', 'git', 'grep', 'head', 'herdr', 'id', 'jq', 'ls', 'mkdir',
        'mktemp', 'mv', 'paste', 'perl', 'ps', 'python3', 'readlink', 'rm', 'rmdir', 'sed', 'setsid', 'sleep', 'sort', 'stat', 'tail', 'tr'))

    def test_each_bash_twin_has_its_bash_definition_and_its_python_def(self):
        reach = self.reach()
        python = '\n'.join(p.read_text() for p in (scripts / 'pstack_core').rglob('*.py'))
        for helper in self.TWINS:
            self.assertTrue(any(f'{f}:{helper}' in reach.nodes for f in self.HELPER_FILES), f'{helper} has no bash definition: remove it from TWINS')
            self.assertRegex(python, rf'(?m)^\s*def {self.TWIN_NAMES.get(helper, helper)}\(', f'{helper} has no Python twin')

    def test_ported_commands_are_pycore_shims_in_the_script_that_defines_their_final_body(self):
        reach = self.reach()
        shim = re.compile(r'\s*pycore (--exec )?\S+ "\$@";?\s*\Z')
        wrapped = {'pstack-kitchen/scripts/kitchen.sh': self.KITCHEN_WRAPS}
        for entry in self.ENTRIES:
            for name in self.PORTED:
                final = reach.final(root / 'skills' / entry, f'cmd_{name}')
                self.assertIsNotNone(final, f'{entry}: cmd_{name} is not defined')
                if name in wrapped.get(entry, ()):
                    self.assertFalse(shim.fullmatch(final.body), f'{final.key} is a wrapper around core_{name}, not a shim')
                    core = reach.final(root / 'skills' / entry, f'core_{name}')
                    self.assertEqual(core.alias_of.key, f'{self.CORE}:cmd_{name}', f'{entry}: core_{name} is not a snapshot of the pair command')
                elif (entry, name) == ('pstack-trio/scripts/pair.sh', 'discuss'):
                    self.assertEqual(final.body.strip(), 'discuss_with_consultant "$@";')
                else:
                    self.assertRegex(final.body, shim, f'{final.key} is not a pycore shim')
                    # the trio's and the kitchen's stop is consultant-core's; every other ported command's final body is pair-core's
                    home = self.CONSULTANT_CORE if name == 'stop' and entry in self.CONSULTANT_ENTRIES else self.CORE
                    if (entry, name) == ('pstack-trio/scripts/pair.sh', 'spawn'):
                        home = entry  # the trio's own command, run from its variant
                    self.assertEqual(final.key, f'{home}:cmd_{name}', f'{entry}: the final cmd_{name} is not {home}')

    def test_answer_is_guided_only_and_a_pycore_shim_there(self):
        reach = self.reach()
        final = reach.final(root / 'skills/pstack-pair-guided/scripts/pair.sh', 'cmd_answer')
        self.assertRegex(final.body, r'\s*pycore (--exec )?answer "\$@";?\s*\Z')
        self.assertEqual(final.key, 'pstack-pair-guided/scripts/pair.sh:cmd_answer')
        for entry in self.ENTRIES:
            if entry != 'pstack-pair-guided/scripts/pair.sh':
                self.assertIsNone(reach.final(root / 'skills' / entry, 'cmd_answer'), entry)

    def test_kitchen_keeps_exactly_these_copy_fn_snapshots_of_the_pair_core_bodies(self):
        table = self.reach().tables[(root / 'skills/pstack-kitchen/scripts/kitchen.sh').resolve()]
        self.assertEqual({name: d.alias_of.key for name, d in table.items() if d.alias_of}, self.KITCHEN_ALIASES)
        for name in ('init', 'spawn'):
            self.assertEqual(table[f'core_{name}'].alias_of.key, f'{self.CORE}:cmd_{name}')
            self.assertEqual(table[f'cmd_{name}'].key, f'pstack-kitchen/scripts/kitchen.sh:cmd_{name}')

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
        r = subprocess.run([str(bin_dir / 'bash'), str(scripts / 'pair.sh'), 'help'], env=env, capture_output=True, text=True, check=False, timeout=120)
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
            run = lambda *a: subprocess.run([script, *a], env=env, capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(run('init', 'demo').returncode, 0, skill)
            store = self.path / 'state/pstack' / skill.removeprefix('pstack-') / 'demo'
            r = run('log', str(store), 'p', 'd', 'w', 'e', 'r')
            self.assertEqual((r.returncode, r.stderr), (0, ''), skill)
            self.assertEqual((store / 'decisions.tsv').read_text().splitlines()[-1].split('\t')[1:], ['p', 'd', 'w', 'e', 'r'], skill)
            self.assertEqual((store / 'events.tsv').read_text().splitlines()[-1].split('\t')[2:], ['master', 'log', '-', 'p: d'], skill)
            r = run('log', str(store), 'p')
            self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: usage: pair.sh log <store> <phase> <decision> <why> <evidence> <result>\n'), skill)


class BashReachTests(unittest.TestCase):
    """tests/bash_reach.py on small synthetic scripts."""

    COPY_FN = 'copy_fn() {\n\teval "$2() $(declare -f "$1" | tail -n +2)"\n}\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def reach(self, **files):
        for name, text in files.items():
            (self.dir / f'{name}.sh').write_text(text)
        return bash_reach.Reach([self.dir / 'entry.sh'], self.dir)

    SOURCE_LIB = '. "$here/lib.sh"\n'

    def test_a_cycle_with_no_root_is_dead_and_what_a_cmd_function_calls_is_live(self):
        reach = self.reach(lib='a() { b; }\nb() { a; }\nlive() { :; }\ncmd_x() { live; }\n', entry=self.SOURCE_LIB)
        self.assertEqual(reach.dead(), {'lib.sh:a', 'lib.sh:b'})
        self.assertEqual(reach.reachable(self.dir / 'entry.sh'), {'lib.sh:cmd_x', 'lib.sh:live'})

    def test_the_last_definition_in_an_entry_wins_and_the_overridden_body_takes_its_callees_with_it(self):
        reach = self.reach(lib='helper() { leaf; }\nleaf() { :; }\ncmd_x() { helper; }\n', entry=self.SOURCE_LIB + 'helper() { :; }\n')
        self.assertEqual(reach.dead(), {'lib.sh:helper', 'lib.sh:leaf'})
        self.assertIn('entry.sh:helper', reach.reachable(self.dir / 'entry.sh'))

    def test_the_override_is_per_entry_so_the_other_entry_keeps_the_first_body_live(self):
        (self.dir / 'lib.sh').write_text('helper() { leaf; }\nleaf() { :; }\ncmd_x() { helper; }\n')
        (self.dir / 'entry.sh').write_text(self.SOURCE_LIB + 'helper() { :; }\n')
        (self.dir / 'other.sh').write_text(self.SOURCE_LIB)
        reach = bash_reach.Reach([self.dir / 'entry.sh', self.dir / 'other.sh'], self.dir)
        self.assertEqual(reach.dead(), set())
        self.assertEqual(reach.reachable(self.dir / 'other.sh'), {'lib.sh:cmd_x', 'lib.sh:helper', 'lib.sh:leaf'})

    def test_a_copy_fn_alias_is_live_when_called_and_dead_when_not_and_its_body_resolves_late(self):
        reach = self.reach(lib='orig() { leaf; }\nleaf() { :; }\n',
                           entry=self.SOURCE_LIB + self.COPY_FN + 'copy_fn orig kept\ncopy_fn orig unused\norig() { :; }\ncmd_x() { kept; orig; }\n')
        self.assertEqual(reach.dead(), {'lib.sh:orig', 'entry.sh:unused'})
        self.assertEqual(reach.reachable(self.dir / 'entry.sh'), {'entry.sh:cmd_x', 'entry.sh:kept', 'entry.sh:orig', 'lib.sh:leaf', 'entry.sh:copy_fn'})
        self.assertEqual(reach.final(self.dir / 'entry.sh', 'kept').alias_of.key, 'lib.sh:orig')

    def test_a_name_in_echo_quotes_a_comment_or_heredoc_data_is_not_a_call(self):
        script = ('helper() { :; }\nlive() { :; }\nhidden() { :; }\ncmd_x() {\n\techo helper\n\tprintf "%s\\n" "helper"  # helper\n'
                  '\tcat <<EOF\nhelper\n$(live)\nEOF\n\tcat <<\'EOF\'\n$(hidden)\nEOF\n}\n')
        self.assertEqual(self.reach(lib=script, entry=self.SOURCE_LIB).dead(), {'lib.sh:helper', 'lib.sh:hidden'})

    def test_a_name_in_command_position_inside_a_substitution_or_a_construct_is_a_call(self):
        script = ('one() { :; }\ntwo() { :; }\nthree() { :; }\nfour() { :; }\nfive() { :; }\nnever() { :; }\n'
                  'cmd_x() {\n\tv="$(one)"\n\tmapfile -t a < <(two)\n\techo "${v:-$(three)}"\n'
                  '\tcase "$1" in\n\tnever) four ;;\n\t*) : ;;\n\tesac\n\tif ! [ -n x ] && five; then :; fi\n}\n')
        self.assertEqual(self.reach(lib=script, entry=self.SOURCE_LIB).dead(), {'lib.sh:never'})

    def test_what_the_model_does_not_cover_raises(self):
        cases = {'a dynamic command word': ('lib', 'cmd_x() { "$fn" arg; }\n'),
                 'an eval outside copy_fn': ('lib', 'cmd_x() { eval "$x"; }\n'),
                 'declare -F outside pair_main and copy_fn': ('lib', 'cmd_x() { declare -F cmd_y; }\n'),
                 'a trap handler': ('lib', 'cmd_x() { trap cleanup EXIT; }\n'),
                 'a backtick substitution': ('lib', 'cmd_x() { v=`one`; }\n'),
                 'a source path it cannot resolve': ('entry', '. "$elsewhere/lib.sh"\n'),
                 'a copy_fn of a name with no earlier definition': ('entry', self.SOURCE_LIB + self.COPY_FN + 'copy_fn missing alias\n')}
        for label, (where, text) in cases.items():
            with self.subTest(label):
                files = {'lib': 'cmd_y() { :; }\n', 'entry': self.SOURCE_LIB}
                files[where] = text
                with self.assertRaises(bash_reach.ReachError):
                    self.reach(**files)


    def test_a_command_substitution_inside_arithmetic_is_a_call(self):
        script = 'helper() { :; }\nloop() { :; }\ncmd_x() {\n\tif (( $(helper) > 0 )); then :; fi\n\tfor ((i = 0; i < $(loop); i++)); do :; done\n}\n'
        self.assertEqual(self.reach(lib=script, entry=self.SOURCE_LIB).dead(), set())

    def test_a_heredoc_body_is_read_like_a_double_quoted_string(self):
        reach = self.reach(lib='x() { :; }\ncmd_x() {\n\tcat <<EOF\n$((x + 1))\nEOF\n}\n', entry=self.SOURCE_LIB)
        self.assertEqual(reach.dead(), {'lib.sh:x'})
        with self.assertRaises(bash_reach.ReachError):
            self.reach(lib='helper() { :; }\ncmd_x() {\n\tcat <<EOF\n`helper`\nEOF\n}\n', entry=self.SOURCE_LIB)

    def test_a_nested_definition_or_a_brace_with_nothing_to_close_raises(self):
        for label, script in {'a nested definition': 'cmd_x() {\n\tinner() { :; }\n\tinner\n}\n', 'an extra }': 'cmd_x() { :; }\n}\n'}.items():
            with self.subTest(label), self.assertRaises(bash_reach.ReachError):
                self.reach(lib=script, entry=self.SOURCE_LIB)

    def test_copy_fn_inside_a_function_raises(self):
        with self.assertRaises(bash_reach.ReachError):
            self.reach(lib='cmd_y() { :; }\n', entry=self.SOURCE_LIB + self.COPY_FN + 'later() {\n\tcopy_fn cmd_y alias\n}\n')

    def test_a_prefix_does_not_hide_a_form_the_model_rejects_and_time_does_not_hide_a_call(self):
        for label, body in {'builtin eval': 'builtin eval "$x"', 'command eval': 'command eval "$x"', 'builtin source of a missing file': 'builtin source "$here/none.sh"',
                            'compgen -A function': 'compgen -A function', 'export -f': 'export -f cmd_y', 'coproc': 'coproc cmd_y'}.items():
            with self.subTest(label), self.assertRaises(bash_reach.ReachError):
                self.reach(lib=f'cmd_y() {{ :; }}\ncmd_x() {{ {body}; }}\n', entry=self.SOURCE_LIB)
        script = 'f() { :; }\ng() { :; }\ncmd_x() {\n\ttime -p f\n\ttime g\n\tcommand -v eval\n}\n'
        self.assertEqual(self.reach(lib=script, entry=self.SOURCE_LIB).dead(), set())

    def test_each_dynamic_form_is_pinned_to_the_command_it_models(self):
        ok = {'copy_fn': self.COPY_FN, 'pycore': 'pycore() {\n\tlocal run=()\n\t${run[@]+"${run[@]}"} python3 -I x -- "$@"\n}\n',
              'pair_main': 'pair_main() {\n\tlocal fn=cmd_y\n\tif declare -F "$fn" >/dev/null; then\n\t\t"$fn" "$@"\n\tfi\n}\n'}
        for name, text in ok.items():
            with self.subTest(name):
                self.reach(lib='cmd_y() { :; }\n', entry=self.SOURCE_LIB + text)
        changed = {'copy_fn': self.COPY_FN.replace('+2', '+3'), 'pycore': ok['pycore'].replace('python3 -I x', 'helper'),
                   'pair_main': ok['pair_main'].replace('"$fn" "$@"', '"$fn" extra')}
        for name, text in changed.items():
            with self.subTest(f'changed {name}'), self.assertRaises(bash_reach.ReachError):
                self.reach(lib='cmd_y() { :; }\nhelper() { :; }\n', entry=self.SOURCE_LIB + text)


if __name__ == '__main__':
    unittest.main()
