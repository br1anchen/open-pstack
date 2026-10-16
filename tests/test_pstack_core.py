"""The Python core behind pair-core.sh: die parity and the store helpers it duplicates from bash."""
import json
import os
import shlex
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
from pstack_core.context import Context  # noqa: E402
from pstack_core.errors import Die  # noqa: E402

LEGACY = 'd06a932'
LEAKED_PREFIXES = ('PSTACK_KITCHEN_', 'KITCHEN_', 'PAIR_')


def inherited_env():
    return {k: v for k, v in os.environ.items() if k != 'PSTACK_PLACEMENT' and not k.startswith(LEAKED_PREFIXES)}


def core_args(skill_root='/nowhere/pstack-pair'):
    return ['--skill', 'pstack-pair', '--skill-root', skill_root, '--state-root', '/s', '--store-label', 'Pair',
            '--roles', 'sidekick', '--store-dirs', 'plans briefs', '--slug-max', '21', '--unfilled-re', '', '--']


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
