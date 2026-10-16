"""The Python core behind pair-core.sh: die parity and the store helpers it duplicates from bash."""
import contextlib
import io
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
scripts = root / 'skills/pstack-pair/scripts'
sys.path.insert(0, str(scripts))
sys.dont_write_bytecode = True
from pstack_core import cli, herdr, review, store  # noqa: E402
from pstack_core.commands import metrics  # noqa: E402
from pstack_core.context import Context  # noqa: E402
from pstack_core.errors import Die  # noqa: E402

LEGACY = 'd06a932'
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

    def bash(self, snippet, env=None, check=True):
        harness = f'''set -euo pipefail
skill_root={root / "skills/pstack-pair"}; PAIR_SKILL=pstack-pair; state_root=/s; store_label=Pair
roles=(sidekick); store_dirs=(plans); slug_max=21
. {scripts / "pair-core.sh"}
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

    PORTED = ('status', 'metrics', 'log', 'notes', 'note', 'new_note', 'progress', 'new_plan', 'new_brief', 'new_steer', 'pause', 'resume', 'scratch')
    DUPLICATED = ('event', 'header_field', 'next_seq', 'paused_reason', 'pause_notice', 'steer_notice', 'open_steers', 'progress_path',
                  'new_from_template', 'steps_file', 'noted_through', 'open_notes', 'trunk_ref', 'range_floor', 'review_base', 'require_filled',
                  'agent_status', 'pane_busy', 'pane_text', 'agent_kind', 'latest_report', 'latest_advice', 'queued_brief', 'json_update',
                  'report_ready', 'expected_report')

    def test_ported_commands_are_pycore_shims_and_each_duplicated_bash_helper_still_has_a_bash_caller(self):
        text = {p: p.read_text() for p in (root / 'skills').glob('*/scripts/*.sh')}
        core = text[scripts / 'pair-core.sh']
        for name in self.PORTED:
            self.assertRegex(core, rf'(?m)^cmd_{name}\(\) \{{ pycore \S+ "\$@"; \}}$', name)
        for helper in self.DUPLICATED:
            self.assertRegex(core, rf'(?m)^{helper}\(\) \{{$', helper)
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
