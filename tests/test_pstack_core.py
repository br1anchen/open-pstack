"""The Python core behind pair-core.sh: die parity and the store helpers it duplicates from bash."""
import calendar
import contextlib
import inspect
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
PHASE4 = 'b632713'


def die_prints_only_its_message(skills_dir):
    """Unit 006 changed `die "msg" 2` from printing `pair.sh: msg 2` to printing `pair.sh: msg`. The bases above print the old form, so the one
    line is patched in each extracted oracle (the line count stays, so `line N` text still matches); every other difference stays visible."""
    core = skills_dir / 'pstack-pair/scripts/pair-core.sh'
    old = "printf 'pair.sh: %s\\n' \"$*\" >&2"
    text = core.read_text()
    assert text.count(old) == 1, f'{core} no longer has the die line to patch'
    core.write_text(text.replace(old, old.replace('"$*"', '"$1"')))
    errors = skills_dir / 'pstack-pair/scripts/pstack_core/errors.py'
    if errors.exists():
        # the Python ported before this unit joined every part of a Die, the exit code included
        joined = "self.message = ' '.join(str(p) for p in parts)"
        text = errors.read_text()
        assert text.count(joined) == 1, f'{errors} no longer has the Die line to patch'
        errors.write_text(text.replace(joined, 'self.message = str(parts[0])'))


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
    DELETED_BASH = ('expected_report', 'file_mtime', 'trunk_ref', 'range_floor', 'steps_file', 'detached', 'check_scope', 'checkin', 'finish_wait', 'fresh_reply', 'mark_seen', 'noted_through', 'pending_report', 'progress_path',
                    'prompt_sidekick', 'prune_pending', 'queue_note', 'record_dispatch', 'record_sent', 'reply_mode', 'review_base', 'review_delta',
                    'scope_entry_exists', 'seen_reply', 'send_and_wait', 'sidekick_activity', 'status_paths', 'steer_scope_entries', 'tree_activity',
                    'unreviewed_steps', 'wait_for_reply', 'withheld_report', 'wait_opts', 'prime_steer', 'steps_due', 'report_ready',
                    'provider_hint', 'bootstrap_recover', 'asks_no_approvals', 'plan_gate_extra', 'require_agreed_plan', 'open_notes', 'next_seq', 'new_from_template', 'busy_command', 'agent_session_error', 'muse_session_error', 'prime_role_error', 'scope_entries', 'record_prime_session', 'detect_permission_mode', 'permission_args', 'trust_args', 'mcp_args', 'has_mcp_arg', 'has_trust_arg', 'has_permission_arg', 'pick_direction', 'new_pane')

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

    def test_die_with_a_code_exits_with_it_and_prints_only_the_message(self):
        err = tempfile.TemporaryFile('w+')
        self.addCleanup(err.close)

        def boom(ctx, args):
            raise Die('x', 7)

        with mock.patch.dict(cli.COMMANDS, {'boom': boom}), mock.patch.object(sys, 'stderr', err):
            code = cli.main([*core_args(), 'boom'])
        err.seek(0)
        self.assertEqual((code, err.read()), (7, 'pair.sh: x\n'))

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
            die_prints_only_its_message(Path(tmp.name) / 'skills')
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
            die_prints_only_its_message(Path(tmp.name) / 'skills')
            cls._phase3 = Path(tmp.name) / 'skills'
        return cls._phase3

    def phase4_skills(self):
        """skills/ as it was at PHASE4, the oracle for kitchen.sh's own commands (catch, retro, new-handoff, then the rest as they are ported)."""
        cls = type(self)
        if cls.__dict__.get('_phase4') is None:
            tmp = tempfile.TemporaryDirectory()
            self.addClassCleanup(tmp.cleanup)
            git = subprocess.run(['git', '-C', str(root), 'archive', PHASE4, 'skills'], capture_output=True, check=False, timeout=120)
            if git.returncode != 0:
                raise unittest.SkipTest(f'{PHASE4} is not in this checkout')
            subprocess.run(['tar', '-x', '-C', tmp.name], input=git.stdout, check=True, timeout=120)
            # no die patch: PHASE4 comes after unit 006, so its `die` already prints only its message
            cls._phase4 = Path(tmp.name) / 'skills'
        return cls._phase4

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
        # unit 006: an error under kitchen.sh starts `kitchen.sh:`; the base printed `pair.sh:`
        norm = lambda text: re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>',
                                   re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*pair-core\.sh', '<core>', re.sub(r'(?m)^kitchen\.sh: ', 'pair.sh: ', re.sub(r'(?m)^kitchen\.sh: (usage: )kitchen\.sh init ', r'pair.sh: \1pair.sh init ', text.replace(str(skills), '<SKILLS>').replace(str(top), '<T>')))))))
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
    EFFECTIVE_COMMANDS = {'pstack-pair': (), 'pstack-pair-guided': ('answer',), 'pstack-trio': ('spawn', 'stop'), 'pstack-kitchen': ('__classify_brief', '__record_skill_build', 'catch', 'classify', 'close', 'consultant', 'discuss', 'dispatch', 'feedback', 'finish', 'fleet', 'init', 'land-check', 'maintainer', 'new-handoff', 'queue', 'retro', 'revise', 'rotate-master', 'spawn', 'step', 'stop', 'take-master', 'verify', 'wait')}

    def test_each_skills_effective_command_table_is_pinned(self):
        tables = {skill: tuple(sorted(load_skill_variant(root / 'skills', skill).commands)) for skill in self.SKILLS_WITH_A_VARIANT}
        self.assertEqual(tables, self.EFFECTIVE_COMMANDS)
        # a variant table holds a name the core lets it replace, or one the core lacks (the kitchen's own commands)
        self.assertTrue(all(name in cli.OVERRIDABLE or name not in cli.COMMANDS for table in tables.values() for name in table))

    def test_kitchen_runs_the_trios_stop_from_a_table_of_its_own_and_has_no_spawn(self):
        trio = load_skill_variant(root / 'skills', 'pstack-trio')
        kitchen = load_skill_variant(root / 'skills', 'pstack-kitchen')
        self.assertIsNot(kitchen.commands, trio.commands)
        for table in (trio.commands, kitchen.commands):
            self.assertEqual((table['stop'].__name__, table['stop'].__code__.co_filename), ('stop', str(root / 'skills/pstack-trio/scripts/variant.py')))
        # the trio's spawn is its own command, in its variant; the kitchen's is kitchen_core.setup's (roster kind and args, then the core's spawn)
        self.assertEqual(kitchen.commands['spawn'].__name__, 'spawn')
        self.assertNotEqual(kitchen.commands['spawn'].__code__.co_filename, trio.commands['spawn'].__code__.co_filename)
        self.assertEqual((trio.commands['spawn'].__name__, trio.commands['spawn'].__code__.co_filename), ('spawn', str(root / 'skills/pstack-trio/scripts/variant.py')))

    def test_every_variant_class_defines_its_own_commands(self):
        for cls in (Variant, *(load_skill_variant(root / 'skills', skill) for skill in self.SKILLS_WITH_A_VARIANT)):
            self.assertIn('commands', cls.__dict__, cls.__name__)
            self.assertIsInstance(cls.__dict__['commands'], dict, cls.__name__)

    def test_only_an_overridable_name_or_one_the_core_lacks_asks_the_variant(self):
        class Exploding:
            @property
            def commands(self):
                raise AssertionError('the variant was asked')

        ctx = Context('pstack-pair', '/nowhere/pstack-pair', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        ctx._variant = Exploding()
        for name in cli.COMMANDS:
            if name not in cli.OVERRIDABLE:
                self.assertIs(cli.resolve(ctx, name), cli.COMMANDS[name], name)
        with self.assertRaises(AssertionError):
            cli.resolve(ctx, '__nothing')  # an internal entry the core lacks is the variant's to supply, so the variant is asked
        ctx._variant = type('V', (), {'commands': {'answer': 'mine', 'status': 'not asked', '__detect_permission_mode': 'not asked'}})()
        self.assertEqual((cli.resolve(ctx, 'answer'), cli.resolve(ctx, 'status'), cli.resolve(ctx, '__detect_permission_mode')),
                         ('mine', cli.COMMANDS['status'], cli.COMMANDS['__detect_permission_mode']))

    def test_a_name_the_core_lacks_asks_the_variant_internal_entries_included(self):
        class Mine:
            commands = {'catch': 'kitchen catch', '__bridge': 'kitchen bridge'}

        ctx = Context('pstack-pair', '/nowhere/pstack-pair', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        ctx._variant = Mine()
        self.assertEqual(cli.resolve(ctx, 'catch'), 'kitchen catch')
        self.assertEqual(cli.resolve(ctx, '__bridge'), 'kitchen bridge')
        self.assertIsNone(cli.resolve(ctx, '__nothing'))
        self.assertIsNone(cli.resolve(ctx, 'nothing-like-it'))
        self.assertIs(cli.resolve(ctx, 'status'), cli.COMMANDS['status'])

    def test_commands_load_the_variant_they_need_and_no_kitchen_code_they_do_not(self):
        """Each command is run through cli.main in a fresh interpreter with the real skill root and a complete argv; the probe reports what ran and
        what was imported or opened. A pair.sh run never reaches a pstack-kitchen file; kitchen.sh's stop and status load the kitchen variant
        (stop asks for it; status needs no variant at all) but not kitchen_core; a kitchen command that is kitchen_core's loads it."""
        store = self.path / 'probestore'
        store.mkdir()
        (store / 'pair.json').write_text('{"sidekick": {"name": "s1", "kind": "claude"}, "master": {"name": "m1"}, "git_root": "/nonexistent"}')
        for d in ('briefs', 'reports', 'plans'):
            (store / d).mkdir()
        probe = ('import json, sys; sys.path.insert(0, sys.argv[1]); opened = []\n'
                 'sys.addaudithook(lambda event, args: opened.append(str(args[0])) if event == "open" and "pstack-kitchen" in str(args[0]) else None)\n'
                 'from pstack_core import cli\n'
                 'try:\n    rc = cli.main(json.loads(sys.argv[2]))\nexcept SystemExit as e:\n    rc = e.code\n'
                 'print(json.dumps({"rc": rc, "kitchen_core": "kitchen_core" in sys.modules, "variant": [m for m in sys.modules if m.startswith("pstack_variant_")], '
                 '"opened": opened}))')
        pair_args = lambda skill, label: ['--skill', skill, '--skill-root', str(root / 'skills' / skill), '--state-root', '/s', '--store-label', label,
                                          '--roles', 'sidekick', '--store-dirs', 'plans briefs', '--slug-max', '21', '--unfilled-re', '', '--']
        kitchen_args = ['--skill', 'pstack-kitchen', '--skill-root', str(root / 'skills/pstack-kitchen'), '--state-root', '/s', '--store-label', 'Kitchen',
                        '--roles', 'sidekick consultant verifier', '--store-dirs', 'plans briefs', '--slug-max', '21', '--unfilled-re', '', '--']
        runs = []
        for command in (['status', str(store)], ['stop', str(store)], ['spawn', str(store), '--kind']):
            runs.append(('pair', command[0], pair_args('pstack-pair', 'Pair') + command))
        for command in (['status', str(store)], ['stop', str(store)], ['catch', str(store)]):
            runs.append(('kitchen', command[0], kitchen_args + command))
        seen = {}
        for kind, name, argv in runs:
            r = subprocess.run([sys.executable, '-I', '-c', probe, str(scripts), json.dumps(argv)], capture_output=True, text=True, check=False, timeout=60, cwd='/')
            self.assertNotIn('bad core option', r.stderr, (kind, name, r.stderr))
            self.assertNotIn('unknown command', r.stderr, (kind, name, r.stderr))
            seen[kind, name] = (json.loads(r.stdout.splitlines()[-1]), r.stdout, r.stderr)
        for key in (('pair', 'status'), ('pair', 'stop'), ('pair', 'spawn')):
            info = seen[key][0]
            self.assertEqual((info['kitchen_core'], info['opened']), (False, []), (key, seen[key]))
        # each command ran: status prints its page, stop and spawn end in their own diagnostics
        expected = {('pair', 'status'): (0, '# Pair status', 'stdout'), ('pair', 'stop'): (2, 'pair.sh: sidekick s1 is not live', 'stderr'),
                    ('pair', 'spawn'): (1, '$2: unbound variable', 'stderr'), ('kitchen', 'status'): (0, '# Kitchen status', 'stdout'),
                    ('kitchen', 'catch'): (1, 'usage: kitchen.sh catch <store> <layer> <text> [<unit>]', 'stderr')}
        for key, (rc, line, stream) in expected.items():
            self.assertEqual(seen[key][0]['rc'], rc, (key, seen[key]))
            self.assertIn(line, seen[key][1] if stream == 'stdout' else seen[key][2], (key, seen[key]))
        self.assertEqual(seen['kitchen', 'stop'][0]['rc'], 0, seen['kitchen', 'stop'])
        self.assertIn('sidekick s1 is not live\nconsultant null is not live\nverifier null is not live\n', seen['kitchen', 'stop'][1])
        for name in ('status', 'stop'):
            info = seen['kitchen', name][0]
            self.assertFalse(info['kitchen_core'], (name, seen['kitchen', name]))
        loaded = lambda key: any('/pstack-kitchen/scripts/' in o and 'variant' in o for o in seen[key][0]['opened'])
        self.assertTrue(loaded(('kitchen', 'stop')))
        self.assertFalse(loaded(('kitchen', 'status')), 'status asks nothing of the variant')
        self.assertTrue(loaded(('kitchen', 'catch')))
        self.assertEqual(seen['kitchen', 'catch'][0]['kitchen_core'], True)
        self.assertIn('usage: kitchen.sh catch <store> <layer> <text> [<unit>]', seen['kitchen', 'catch'][2])

    def test_kpy_run_equals_the_subprocess_in_output_stderr_and_status(self):
        """kitchen_core.kpy.run against `python3 kitchen.py`: a passing call, a failing one that prints to stderr, and an argparse error."""
        repo = self.path / 'kpyrepo'
        (repo / '.agents').mkdir(parents=True)
        (repo / '.agents/kitchen.toml').write_text('version = 1\n\n[profile.app]\npaths = ["src/**"]\nfast = ["true"]\n')
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
        bad = self.path / 'badrepo'
        (bad / '.agents').mkdir(parents=True)
        (bad / '.agents/kitchen.toml').write_text('version = 1\n\n[profile.app]\nfsat = ["true"]\n')
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(bad)], check=True)
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        from kitchen_core import kpy
        cases = [(repo, ['--json', 'validate']), (bad, ['validate']), (repo, ['--no-such-flag'])]
        for repository, args in cases:
            sub = subprocess.run([sys.executable, str(root / 'skills/pstack-kitchen/scripts/kitchen.py'), '--repo', str(repository), *args],
                                 capture_output=True, text=True, check=False, timeout=60)
            ours = kpy.run(repository, args)
            self.assertEqual(ours, (sub.returncode, sub.stdout, sub.stderr), (str(repository), args))
        self.assertEqual([kpy.run(repo, ['validate'])[0], kpy.run(bad, ['validate'])[0], kpy.run(repo, ['--no-such-flag'])[0]], [0, 1, 2])

    def test_a_kitchen_command_run_from_the_filesystem_root_loads_kitchen_core(self):
        kitchen_args = ['--skill', 'pstack-kitchen', '--skill-root', str(root / 'skills/pstack-kitchen'), '--state-root', '/s', '--store-label', 'Kitchen',
                        '--roles', 'sidekick consultant verifier', '--store-dirs', 'plans briefs', '--slug-max', '21', '--unfilled-re', '', '--']
        probe = ('import sys; sys.path.insert(0, sys.argv[1]); from pstack_core import cli\n'
                 'rc = cli.main(sys.argv[2:])\nprint(rc, "kitchen_core" in sys.modules)')
        r = subprocess.run([sys.executable, '-I', '-c', probe, str(scripts), *kitchen_args, 'catch'], capture_output=True, text=True, check=False, timeout=60, cwd='/')
        self.assertEqual(r.stdout, '1 True\n', (r.stdout, r.stderr))
        self.assertIn('usage: kitchen.sh catch <store> <layer> <text> [<unit>]', r.stderr)

    def test_the_kitchen_core_package_loads_by_path_from_any_cwd_and_once(self):
        probe = ('import sys; sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])\n'
                 'import variant\n'
                 'assert "kitchen_core" not in sys.modules\n'
                 'a = variant.load_kitchen_core(); b = variant.load_kitchen_core()\n'
                 'print(a is b, a.__name__, list(a.__path__) == [str(variant.KITCHEN_CORE_DIR)])')
        r = subprocess.run([sys.executable, '-I', '-c', probe, str(scripts), str(root / 'skills/pstack-kitchen/scripts')], capture_output=True, text=True,
                           check=False, timeout=60, cwd='/')
        self.assertEqual((r.returncode, r.stdout), (0, 'True kitchen_core True\n'), r.stderr)

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
            'a working devin that never shows the text': dict(devin, args=run, clock=True),
            'a working devin and a stop under another name': dict(devin, args=run, fake={'devin-input': ''}, hook=self.stop_hook('001-land-partial1.md', 'partial (STOP)')),
            'a working devin whose text stays typed': dict(devin, args=run, hook=self.stop_hook(), fake={'devin-input': '', 'typing/s1': 'pstack-pair STOP x'}),
        }

    def stop_ab_run(self, skill, group, cases=None):
        """The stop cases of a group through one entry script, against the bash at the phase 3 base: the outcomes of the base."""
        skills3 = self.phase3_skills()
        mismatches, results = {}, {}
        # the trio's stop usage line is Python, so under kitchen.sh it names kitchen.sh now (unit 006); the base printed pair.sh
        norm_core = lambda text: re.sub(r'line [0-9]+', 'line N', re.sub(r'\S*(?:pair|consultant)-core\.sh|\S*/pair\.sh', '<core>', text.replace('usage: kitchen.sh stop ', 'usage: pair.sh stop ')))
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
        # the whole second fell, in the bash and in the port alike. 'never shows' runs on the instant clock (a `date` that jumps 100 s a call, a
        # `sleep` that returns at once, the port's PAIR_DEVIN_WATCH_S and PAIR_STOP_DEADLINE_S at 0): the watch is over at its first look, so the
        # fallback Enter goes and the stop deadline is past, in the bash and in the port alike. Its repeated reads collapse.
        results = self.stop_ab_run('pstack-pair', lambda n: 'never' in n and 'devin' in n or 'stays typed' in n)
        never = results['a working devin that never shows the text']
        self.assertIn('STOP never showed in the pane; sent one Enter as a fallback', never['stderr'])
        self.assertEqual(never['rc'], 4)
        self.assertIn('report: missing', never['stdout'])

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

    def prime_both(self, label, bash_snippet, python_snippet, shim=False, expect_same=True, **spec):
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
        if not expect_same:
            return results
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
            bash_out = self.leaf_both('pane-trust-' + label.replace(' ', '-'), f'{call}; echo "returned"', py, fake=fake)
            # a trust dialog dies with status 5 and says so; no dialog returns
            self.assertEqual(bash_out[0], 5 if fake else 0, (label, bash_out))
            self.assertEqual('asks whether to trust' in bash_out[2], bool(fake), (label, bash_out))
            self.assertEqual('returned' in bash_out[1], not fake, (label, bash_out))
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
            'a parent pid that is empty': dict(chain=[shell, ('sh', 'sh')], top_ppid=''),
            'a parent pid that is zero': dict(chain=[shell, ('sh', 'sh')], top_ppid='0'),
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

    def test_a_parent_pid_that_is_not_a_number_ends_the_walk_silently_where_the_bash_complains_on_stderr(self):
        """D24. The walk up to the master's agent reads each parent pid with `[ "$pid" -gt 1 ]`: on `abc` or `1.5` the base prints
        `[: abc: integer expected` and treats it as the end of the chain; the port ends the walk with nothing printed. Both fall to the transcript
        and then the settings, so stdout and the exit code agree."""
        shell = self.SHELL
        skills3 = self.phase3_skills()
        cases = {'not a number': (dict(chain=[shell, ('sh', 'sh')], top_ppid='abc'), 'auto\n', 'abc'),
                 'a float': (dict(chain=[shell, ('sh', 'sh')], top_ppid='1.5'), 'auto\n', '1.5'),
                 'not a number, then a transcript': (dict(chain=[shell, ('sh', 'sh')], top_ppid='abc', transcript='{"permissionMode":"plan"}\n'), 'plan\n', 'abc')}
        for i, (name, (spec, mode, shown)) in enumerate(cases.items()):
            want = self.perm_run(f'pd{i}b', spec, [str(skills3 / 'pstack-pair/scripts/pair.sh'), 'permission'])
            got = self.perm_run(f'pd{i}p', spec, [str(root / 'skills/pstack-pair/scripts/pair.sh'), 'permission'])
            self.assertEqual(want, (0, mode, f'<core>: line N: [: {shown}: integer expected\n'), name)
            self.assertEqual(got, (0, mode, ''), name)

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
            got = self.perm_run(f'm{i}', spec, None, nested=(root / 'skills', 'mode="$(pycore __detect_permission_mode)"\nprintf "%s\\n" "$mode"'))
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
        for op in spec.get('gitops', ()):
            # history beyond c1 < c2 < c3: ('write', path, text), ('writeb', path, bytes), ('chmod', path, mode), ('commit', name) naming the new commit,
            # ('git', *args) and ('git>', name, *args) naming the sha it prints; arguments are formatted with the names so far
            if op[0] == 'write':
                (repo / op[1]).parent.mkdir(parents=True, exist_ok=True)
                (repo / op[1]).write_text(op[2])
            elif op[0] == 'writeb':
                (repo / op[1]).write_bytes(op[2])
            elif op[0] == 'chmod':
                os.chmod(repo / op[1], op[2])
            elif op[0] == 'rename':
                os.rename(repo / op[1], repo / op[2])
            elif op[0] == 'commit':
                git('add', '-A')
                git('commit', '-q', '-m', op[1])
                shas[op[1]] = git('rev-parse', 'HEAD')
            elif op[0] == 'git':
                git(*(a.format(**shas, T=str(top)) for a in op[1:]))
            elif op[0] == 'git>':
                shas[op[1]] = git(*(a.format(**shas, T=str(top)) for a in op[2:]))
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
        for rel, text in spec.get('statefiles', {}).items():
            (top / 'state' / rel).parent.mkdir(parents=True, exist_ok=True)
            (top / 'state' / rel).write_text(text)
        for rel, age in spec.get('statelocks', {}).items():
            (top / 'state' / rel).mkdir(parents=True)
            stamp = time.time() - age
            os.utime(top / 'state' / rel, (stamp, stamp))
        for rel, text in spec.get('fakefiles', {}).items():
            (fake / rel).parent.mkdir(parents=True, exist_ok=True)
            (fake / rel).write_text(text)
        if spec.get('pstable'):
            (top / 'ps.tsv').write_text(spec['pstable'])
            env['FAKE_PS_TABLE'] = str(top / 'ps.tsv')
        for rel, target in spec.get('statelinks', {}).items():
            (top / 'state' / rel).parent.mkdir(parents=True, exist_ok=True)
            (top / 'state' / rel).symlink_to(target)
        for rel, mode in spec.get('statemodes', {}).items():
            os.chmod(top / 'state' / rel, mode)
            self.addCleanup(os.chmod, top / 'state' / rel, 0o755)
        for rel, age in spec.get('stateages', {}).items():
            stamp = time.time() - age
            os.utime(top / 'state' / rel, (stamp, stamp))
        if spec.get('tick'):
            # a `sleep` that, given KITCHEN_QUIET_POLL_S (any other sleep is the real one), moves the KITCHEN_QUIET_CLOCK file on by this many seconds (when it holds a number), so a quiet wait's polls end at a fixed count
            ticks = top / 'ticks'
            ticks.mkdir()
            (ticks / 'sleep').write_text(f'#!/bin/sh\n[ "$1" = "$KITCHEN_QUIET_POLL_S" ] || exec {shutil.which("sleep")} "$@"\nn=$(cat "$KITCHEN_QUIET_CLOCK" 2>/dev/null)\ncase "$n" in\n  ""|*[!0-9]*) ;;\n  *) echo $((n + {int(spec["tick"])})) >"$KITCHEN_QUIET_CLOCK" ;;\nesac\n')
            (ticks / 'sleep').chmod(0o755)
            env['PATH'] = f'{ticks}:{env["PATH"]}'
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
        if spec.get('noexit') or spec.get('agent_get') is not None:
            # a herdr that ignores the exit command (the agent stays), or answers `agent get` with exactly this text; the rest goes to the fake
            wrap = top / 'wrapherdr'
            wrap.mkdir()
            body = '#!/bin/sh\n'
            if spec.get('noexit'):
                body += 'if [ "$1 $2" = "agent prompt" ] && { [ "$4" = /exit ] || [ "$4" = /quit ]; }; then printf "{}\\n"; exit 0; fi\n'
            if spec.get('agent_get') is not None:
                (top / 'agent_get.txt').write_text(spec['agent_get'])
                body += f'if [ "$1 $2" = "agent get" ]; then cat "{top}/agent_get.txt"; exit 0; fi\n'
            (wrap / 'herdr').write_text(body + f'exec {root / "tests/pstack-pair/bin/herdr"} "$@"\n')
            (wrap / 'herdr').chmod(0o755)
            env['PATH'] = f'{wrap}:{env["PATH"]}'
        if spec.get('hook'):
            (fake / 'hook').write_text(spec['hook'])
            (fake / 'hook').chmod(0o755)
        if spec.get('report_after'):
            (fake / 'report-after').write_text(fill(spec['report_after']) + '\n')
        env.update(spec.get('env', {}))
        env.update({k: fill(v) for k, v in spec.get('envfill', {}).items()})
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
            env.update({'PAIR_EXIT_DEADLINE_S': '0', 'PAIR_EXIT_POLL_S': '0', 'PAIR_ESC_WAIT_S': '0', 'PAIR_DEVIN_WATCH_S': '0', 'PAIR_STOP_DEADLINE_S': '0'})
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
        if spec.get('live_wait', bool(spec.get('args')) and spec['args'][0] in ('finish', 'notify')):
            # a wait that owns the store and is running (this test process): notify leaves the report for it, as the base always did
            (st / 'wait.pid').write_text(f'{os.getpid()}\n')
        return top, st, env, fill

    def ab_run(self, label, skills, skill, spec):
        top, st, env, fill = self.ab_world(label, spec)
        script = 'kitchen.sh' if skill == 'pstack-kitchen' else 'pair.sh'
        entry = skills / skill / 'scripts' / script
        if spec.get('background'):
            # The entry script with its command run as `( pair_main ... ) &` and waited for: a subshell where errexit stays on, unlike `$( )`.
            scripts_dir = entry.parent
            text = entry.read_text()
            here = 'here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"'
            self.assertTrue(text.count(here) == 1 and text.endswith('\npair_main "$@"\n'), 'pair.sh changed shape')
            entry = top / 'pair-background.sh'
            entry.write_text(text.replace(here, f'here="{scripts_dir}"').removesuffix('pair_main "$@"\n') + '( pair_main "$@" ) &\nwait $!\n')
            entry.chmod(0o755)
        run = lambda: subprocess.run([str(entry), *(fill(a) for a in spec['args'])], cwd=top / spec.get('cwd', 'repo'), env=env, timeout=120,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT if spec.get('merged') else subprocess.PIPE, text=True, errors='surrogateescape', check=False)
        r = run()
        second = None
        if spec.get('twice'):
            # The same command again in the same store, once the detached job the first one started has written its rc (a bounded poll: the job is
            # a real process, and the second run reads what it left).
            for _ in range(2400):
                if list(st.glob('steps/jobs/*/rc')):
                    break
                time.sleep(0.05)  # kitchen-allow: test-sleep (a wait for a detached job's rc file, bounded by the 120 s of the run's subprocess timeout; a passing run leaves at once)
            second = run()
        # The base run and the port run are two worlds, built in two temp directories at two moments, so only what that explains is rewritten:
        # <T> is the world's directory (it appears in paths); <TS> the ISO stamps the store writes (.dispatch.at, .sent.at); <EPOCH> the epoch
        # seconds it writes (.sent.epoch, steps and gates rows); <HM> the HH:MM that starts a progress line `step`. Nothing else.
        norm = lambda text: re.sub(r'(exit [0-9]+ )[0-9.]+s', r'\1<S>s', re.sub(r'(?m)^[0-9]{2}:[0-9]{2} step ', '<HM> step ', re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text.replace(str(top), '<T>')))))
        files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'events.tsv'}
        # a wait's claim file holds its process id (the bash's $$, the port's own pid): a number that differs between two worlds by nature
        files = {rel: re.sub(r'\A[0-9]+\n\Z', '<PID>\n', text) if rel.endswith('wait.pid') else text for rel, text in files.items()}
        # directories too (standing order 26): a lock left held, a scratch directory that was not emptied
        files.update({str(d.relative_to(st)) + '/': '' for d in sorted(st.rglob('*')) if d.is_dir() and not d.is_symlink()})
        # skip: store paths a case leaves out of the comparison because a detached job is still writing under them when the command returns
        files = {rel: text for rel, text in files.items() if not rel.startswith(tuple(spec.get('skip', ('\0',))))}
        events = [norm(l).split('\t', 2)[2] for l in (st / 'events.tsv').read_text(errors='surrogateescape').splitlines()]
        calls = norm((top / 'fake/calls.log').read_text()) if (top / 'fake/calls.log').exists() else ''
        # Unit 006: an error a command prints under kitchen.sh now starts `kitchen.sh:`; the bases print `pair.sh:` there.
        script_name = lambda text: re.sub(r'(?m)^kitchen\.sh: ', 'pair.sh: ', text)
        out = {'rc': r.returncode, 'stdout': norm(r.stdout), 'stderr': script_name(norm(r.stderr or '')), 'files': files, 'events': events, 'calls': calls}
        if second is not None:
            out['second'] = (second.returncode, norm(second.stdout), script_name(norm(second.stderr or '')))
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
            'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n', 'queue': '{st}/briefs/009-gone.md\\n'}),
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
        'finish outside herdr': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], noenv=True, files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
        'finish a done report without pair.json': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'finish a partial report without pair.json': dict(args=['finish', '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
        'finish a report path with a trailing slash': dict(args=['finish', '{st}', '{st}/reports/001-x.md/'], files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'finish through a missing intermediate directory': dict(args=['finish', '{st}', '{st}/nope/../reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
        'notify through a missing intermediate directory': dict(args=['notify', '{st}', '{st}/nope/../reports/001-x.md'], files={'reports/001-x.md': '# R\n\nstatus: done\n'}),
        'finish a symlink loop': dict(args=['finish', '{st}', '{T}/loop1'], links={'loop1': 'loop2', 'loop2': 'loop1'}, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
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
        'notify a partial report without pair.json': dict(args=['notify', '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
        **{f'{cmd} a {status} report without pair.json': dict(args=[cmd, '{st}', '{st}/reports/001-x.md'], nopair=True, files={'reports/001-x.md': f'# R\n\nstatus: {status}\nhead: {{c3}}\n'})
           for cmd in ('finish', 'notify') for status in ('blocked', 'failed', 'bogus', '') if not (cmd == 'finish' and status in ('bogus', ''))},
        'next, heads null': dict(args=['next', '{st}'], pair={'heads': None}, files={'queue': '{st}/briefs/002-y.md\n'}),
        'next, heads with the unit as false': dict(args=['next', '{st}'], pair={'heads': {'002-y': False}}, files={'queue': '{st}/briefs/002-y.md\n'}),
        'next, pending false': dict(args=['next', '{st}'], pair={'pending': False}, files={'queue': '{st}/briefs/002-y.md\n'}),
        'next, pending holding the brief, with its report on file': dict(args=['next', '{st}'], pair={'pending': ['{st}/briefs/002-y.md', 'z']},
                                                                          files={'queue': '{st}/briefs/002-y.md\n', 'reports/002-y.md': '# old\n'}),
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

    def damaged_store_cases(self):
        """Stores a hand edit or a crash leaves in a shape the commands never write: {name: (spec, base rc, text the base's stderr holds)}. The
        phase 2 commands (wait, next, finish, dispatch) run at e6e83e3 and in the working tree, and the whole outcome must be equal."""
        queued = {'queue': '{st}/briefs/002-y.md\n'}
        nxt = lambda **kw: dict(args=['next', '{st}'], files=queued, **kw)
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1}
        done = {'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}
        wait = lambda **kw: dict(args=['wait', '{st}', '--timeout', '1000'], files=done, **kw)
        index = lambda kind, key: f'Cannot index {kind} with string ("{key}")'
        cases = {
            'next with a queue that is a directory': (dict(args=['next', '{st}'], files={'queue/x': ''}), 4, 'queued brief is gone'),
            'finish a done report with a queue that is a directory': (dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files={**done, 'queue/x': ''}), 0,
                                                                      'queued brief is gone'),
            'next with heads false': (nxt(pair={'heads': False}), 0, index('boolean', '002-y')),
            'next with heads a string': (nxt(pair={'heads': 'x'}), 0, index('string', '002-y')),
            'next with heads an array': (nxt(pair={'heads': [1]}), 0, index('array', '002-y')),
            'next with heads a number': (nxt(pair={'heads': 3}), 0, index('number', '002-y')),
            'next with pending that mixes strings, a number and a null': (nxt(pair={'pending': ['b', 1, None, 'a']}), 0, ''),
            'next with pending that holds an object': (nxt(pair={'pending': ['b', {'k': 1}, 'a']}), 0, ''),
            'next with pending numbers': (nxt(pair={'pending': [3, 1, 2]}), 0, ''),
            'next with pending a string': (nxt(pair={'pending': 'x'}), 0, 'cannot be added'),
            'next with pending an object': (nxt(pair={'pending': {'a': 1}}), 0, 'cannot be added'),
            'wait with an epoch that is not a number': (dict(args=['wait', '{st}', '--timeout', '1000'], files=done, pair={'sent': {**sent, 'epoch': 'abc'}}), 4,
                                                        'integer expected'),
            'wait with pending a number': (wait(pair={'pending': 3, 'sent': sent}), 0, 'Cannot iterate over number (3)'),
            'wait with pending a string': (wait(pair={'pending': 'x', 'sent': sent}), 0, 'Cannot iterate over string ("x")'),
            'wait with pending an object': (wait(pair={'pending': {'a': 1}, 'sent': sent}), 0, ''),
        }
        send = lambda **kw: dict(args=['dispatch', '{st}', '{st}/briefs/002-y.md', '--send-only'], agents={'s1': 'idle claude'}, **kw)
        cases['dispatch --send-only over heads a string'] = (send(pair={'heads': 'x'}), 5, index('string', '002-y'))
        cases['dispatch --send-only over pending that mixes a string, a number and a null'] = (send(pair={'pending': ['b', 1, None, 'a']}), 0, '')
        cases['dispatch --send-only over pending a string'] = (send(pair={'pending': 'x'}), 0, 'Cannot iterate over string ("x")')
        kinds = (('false', False, 'boolean'), ('a string', 'x', 'string'), ('an array', [1], 'array'), ('a number', 3, 'number'), ('true', True, 'boolean'))
        idle = lambda value: dict(args=['wait', '{st}', '--timeout', '1000'], agents={'s1': 'idle claude'}, env={'PAIR_SETTLE_HOLD': '0'}, pair={'sidekick': value, 'sent': sent})
        for label, value, shown in kinds:
            cases[f'wait with a sidekick that is {label}'] = (wait(pair={'sidekick': value, 'sent': sent}), 0, index(shown, 'name'))
            cases[f'wait for an idle sidekick that is {label}'] = (idle(value), 5, index(shown, 'name'))
            cases[f'dispatch to a sidekick that is {label}'] = (dict(args=['dispatch', '{st}', '{st}/briefs/002-y.md', '--send-only'], pair={'sidekick': value}), 5,
                                                              index(shown, 'kind'))
            cases[f'finish a done report with a sidekick that is {label}'] = (dict(args=['finish', '{st}', '{st}/reports/001-x.md'], files=done, pair={'sidekick': value}), 0,
                                                                              index(shown, 'kind'))
        return cases

    @staticmethod
    def mask_damaged(outcome):
        """The two things a damaged-store outcome carries that differ between two runs by construction: the pid in `queue.taken.<pid>` (the
        queue is a directory) and bash's `pair-core.sh: line N:` prefix, whose path and line belong to the tree it ran from (PARITY.md D19)."""
        def mask(text):
            return re.sub(r'\S*pair-core\.sh: line [0-9]+', '<core>', re.sub(r'queue\.taken\.[0-9]+', 'queue.taken.PID', text))
        return {**outcome, 'stdout': mask(outcome['stdout']), 'stderr': mask(outcome['stderr']), 'files': {mask(k): mask(v) for k, v in outcome['files'].items()},
                'calls': mask(outcome['calls'])}

    def test_a_damaged_store_gets_what_the_bash_gave_it(self):
        """D17, D18, D20, D21: stdout, stderr, exit code, the store's files, events.tsv and the fake herdr's calls equal to e6e83e3's, and no
        Python traceback. The base's rc and a piece of its stderr are asserted too, so a case cannot pass by doing nothing."""
        self.maxDiff = None
        for name, (spec, base_rc, base_text) in self.damaged_store_cases().items():
            n = len(list(self.path.iterdir()))
            want = self.ab_run(f'dsb{n}', self.base_skills(), 'pstack-pair', dict(spec))
            got = self.ab_run(f'dsp{n}', root / 'skills', 'pstack-pair', dict(spec))
            self.assertEqual(want['rc'], base_rc, name)
            self.assertIn(base_text, want['stderr'], name)
            self.assertNotIn('Traceback', got['stderr'], name)
            self.assertEqual(self.mask_damaged(got), self.mask_damaged(want), name)

    def test_finish_without_a_live_wait_differs_from_the_bash_only_in_what_notify_marks_seen(self):
        """A1 on the marking path: the finish/notify A/Bs run with a live wait.pid, so notify marks nothing there. Here there is none, and the
        port marks the report seen; everything else (stdout, stderr, events, calls, the queue take, late-steer and rotation lines) must still
        match the base once `pending` and `.sent.seen` are masked in pair.json."""
        names = ('done report to an idle master', 'done report, no master agent', 'done report to a done master', 'queued brief after a done report',
                 'queue with a partial report', 'devin done report asks for a rotation', 'late steer', 'queued brief behind a pause', 'done report with open notes',
                 'partial report', 'steer objection report')

        def masked(out):
            files = dict(out['files'])
            text = re.sub(r'(?s)(,\s*)?"pending": \[.*?\]', '', files['pair.json'])
            files['pair.json'] = re.sub(r'(,\s*)?"seen": "[^"]*"', '', text)
            return {**out, 'files': files}

        marked = 0
        for name in names:
            spec = dict(self.AB_CASES[name], live_wait=False)
            n = len(list(self.path.iterdir()))
            want = self.ab_run(f'nlb{n}', self.base_skills(), 'pstack-pair', dict(spec))
            got = self.ab_run(f'nlp{n}', root / 'skills', 'pstack-pair', dict(spec))
            self.maxDiff = None
            self.assertEqual(masked(got), masked(want), name)
            marked += got['files']['pair.json'] != want['files']['pair.json']
        self.assertGreaterEqual(marked, 1, 'no case reached the marking path')

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
            'a seen mark that bash would read as octal (08)': dict(args=short, pair={'sent': {**sent, 'seen': '{st}/reports/001-x.md:08'}}, files={'reports/001-x.md': done}),
            'a seen mark that bash would read as octal (010)': dict(args=short, pair={'sent': {**sent, 'seen': '{st}/reports/001-x.md:010'}}, files={'reports/001-x.md': done}),
            'a seen mark that is hexadecimal': dict(args=short, pair={'sent': {**sent, 'seen': '{st}/reports/001-x.md:0x10'}}, files={'reports/001-x.md': done}),
            'a seen mark that is not a number': dict(args=short, pair={'sent': {**sent, 'seen': '{st}/reports/001-x.md:abc'}}, files={'reports/001-x.md': done}),
            'a sent epoch that bash would read as octal': dict(args=short, pair={'sent': {**sent, 'epoch': '010'}}, files={'reports/001-x.md': done}),
            'pending holding a number and a null': dict(args=run, pair={'pending': ['{st}/briefs/001-x.md', 1, None], 'sent': sent}, files={'reports/001-x.md': done}),
            'pending that is false': dict(args=run, pair={'pending': False, 'sent': sent}, files={'reports/001-x.md': done}),
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
        # wait.pid is the wait's claim on the store (a killed wait leaves it); the base wrote no such file
        return {str(f.relative_to(st)): f.read_bytes() for f in sorted(st.rglob('*')) if f.is_file() and f.name != 'wait.pid'}

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

    def own_world(self, label, once=None, skill='pstack-pair', **spec):
        """A world for the port alone (a behaviour the base never had) and a way to run its commands one after the other."""
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1}
        spec = {'agents': {'s1': 'working claude'}, 'pair': {'sent': sent, 'pending': ['{st}/briefs/001-x.md']},
                'files': {'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}, **spec}
        top, st, env, fill = self.ab_world(label, spec)
        for name, script in (once or {}).items():
            (top / 'fake/once').mkdir(exist_ok=True)
            (top / 'fake/once' / name).write_text(script)
            (top / 'fake/once' / name).chmod(0o755)
        run = lambda *args, extra_env=None: subprocess.run([str(root / 'skills' / skill / 'scripts/pair.sh'), *(fill(a) for a in args)], cwd=top / 'repo', env={**env, **(extra_env or {})},
                                           capture_output=True, text=True, check=False, timeout=120)
        return top, st, run

    def test_a_newer_wait_supersedes_the_older_and_the_wait_that_returns_releases_the_store(self):
        # A2. The fake herdr's once hook plays the newer wait at a fixed poll: it writes its own owner into wait.pid while this wait sits in
        # `agent wait`; this wait sees another owner at its next poll, says so, ends with 4 and marks nothing seen.
        hook = '#!/bin/sh\nprintf \'other\\n\' >"$FAKE/../st/wait.pid"\n'
        top, st, run = self.own_world('sup', files={}, once={'wait': hook})
        before = (st / 'pair.json').read_text()
        r = run('wait', '{st}', '--timeout', '5000')
        self.assertEqual((r.returncode, r.stdout, r.stderr), (4, 'superseded: a newer wait owns this store; this one ends\n', ''))
        self.assertEqual((st / 'wait.pid').read_text(), 'other\n')
        self.assertEqual((st / 'pair.json').read_text(), before)
        # the wait that owns the store returns the report and takes its claim away
        top, st, run = self.own_world('own')
        r = run('wait', '{st}', '--timeout', '5000')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'report: {st}/reports/001-x.md', r.stdout)
        self.assertFalse((st / 'wait.pid').exists())

    def test_notify_marks_a_report_seen_when_no_wait_will_return_it(self):
        # A1. With no live wait the master's next wait must not return the report the notify just handed it.
        top, st, run = self.own_world('nm')
        r = run('wait', '{st}', '--timeout', '1000')
        self.assertIn('report:', r.stdout)
        top, st, run = self.own_world('nm2')
        r = run('notify', '{st}', '{st}/reports/001-x.md')
        self.assertIn('prompted master m1 (was idle) with REPORT', r.stdout)
        self.assertEqual(json.loads((st / 'pair.json').read_text())['pending'], [])
        r = run('wait', '{st}', '--timeout', '1000')
        self.assertNotIn(f'report: {st}/reports/001-x.md', r.stdout)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        # a wait.pid left by a killed wait is still a wait.pid: notify marks nothing (one duplicate wake, never a missed report)
        top, st, run = self.own_world('nm3')
        (st / 'wait.pid').write_text('99999999\n')
        run('notify', '{st}', '{st}/reports/001-x.md')
        self.assertEqual(json.loads((st / 'pair.json').read_text())['pending'], [f'{st}/briefs/001-x.md'])

    def test_a_queued_brief_behind_a_report_that_is_not_done_says_it_is_held(self):
        # A3. The queue is taken only after a done report, so the digest says a queued brief behind any other report waits for the master.
        held = lambda status: (self.own_world(f'qn-{status}', files={'reports/001-x.md': f'# R\n\nstatus: {status}\nhead: {{c3}}\n', 'queue': '{st}/briefs/002-y.md\n'}))
        for status in ('blocked', 'partial', 'failed'):
            top, st, run = held(status)
            out = run('wait', '{st}', '--timeout', '1000').stdout
            self.assertIn(f'queued: {st}/briefs/002-y.md (held: report is {status}; dispatch it yourself)\n', out, status)
        top, st, run = held('done')
        out = run('wait', '{st}', '--timeout', '1000').stdout
        self.assertIn(f'queued: {st}/briefs/002-y.md\n', out)
        self.assertNotIn('held:', out)

    def test_finish_refuses_a_report_with_no_valid_status_line(self):
        # A10. Before any other check, so the message is the reason and the store is not touched.
        for label, text in (('no status', '# R\n\nhead: abc\n'), ('status: bogus', '# R\n\nstatus: bogus\n'), ('an empty status', '# R\n\nstatus:\n')):
            top, st, run = self.own_world('fs-' + label.replace(' ', '-').replace(':', ''), files={'reports/001-x.md': text})
            before = (st / 'pair.json').read_text()
            r = run('finish', '{st}', '{st}/reports/001-x.md')
            self.assertEqual((r.returncode, r.stdout, r.stderr), (2, '', f'pair.sh: {st}/reports/001-x.md has no valid status: line (one of done, partial, blocked, failed, object, agree, asking)\n'), label)
            self.assertEqual((st / 'pair.json').read_text(), before, label)
        # every status a role runs finish on: reports, steer and plan objections, plan agreements, guided asks
        for status in ('done', 'partial', 'blocked', 'failed', 'object', 'agree', 'asking'):
            top, st, run = self.own_world('fv-' + status, files={'reports/001-x.md': f'# R\n\nstatus: {status}\nhead: {{c3}}\n'})
            self.assertNotIn('no valid status', run('finish', '{st}', '{st}/reports/001-x.md').stderr, status)

    def test_dispatch_and_queue_take_a_brief_path_relative_to_the_store(self):
        # A13. From a cwd that is the repo, not the store.
        top, st, run = self.own_world('rel', files={})
        r = run('queue', '{st}', 'briefs/002-y.md')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((st / 'queue').read_text().strip(), f'{st}/briefs/002-y.md')
        top, st, run = self.own_world('rel2', files={}, agents={'s1': 'idle claude'})
        r = run('dispatch', '{st}', 'briefs/002-y.md', '--send-only')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((st / 'pair.json').read_text())['dispatch']['brief'], f'{st}/briefs/002-y.md')
        r = run('dispatch', '{st}', 'briefs/009-none.md', '--send-only')
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: brief not found: briefs/009-none.md\n'))

    def test_a_fresh_wait_claims_the_store_from_an_older_token(self):
        # (a) A wait that starts takes wait.pid whatever it held; the older wait finds a foreign token at its next poll.
        hook = '#!/bin/sh\ncp "$FAKE/../st/wait.pid" "$FAKE/claimed"\n'
        top, st, run = self.own_world('fresh', files={}, once={'wait': hook})
        (st / 'wait.pid').write_text('older\n')
        r = run('wait', '{st}', '--timeout', '1000')
        claimed = (top / 'fake/claimed').read_text().strip()
        self.assertTrue(claimed.isdigit(), claimed)
        self.assertNotEqual(claimed, 'older')
        self.assertFalse((st / 'wait.pid').exists(), r.stdout)

    def test_a_wait_whose_claim_was_removed_is_superseded_too(self):
        # (b) A newer wait that returned and released took the file away: the older one must not read a missing file as its own.
        hook = '#!/bin/sh\nrm -f "$FAKE/../st/wait.pid"\n'
        top, st, run = self.own_world('gone', files={}, once={'wait': hook})
        before = (st / 'pair.json').read_text()
        r = run('wait', '{st}', '--timeout', '5000')
        self.assertEqual((r.returncode, r.stdout, r.stderr), (4, 'superseded: a newer wait owns this store; this one ends\n', ''))
        self.assertFalse((st / 'wait.pid').exists())
        self.assertEqual((st / 'pair.json').read_text(), before)

    def test_a_dispatch_owns_the_store_so_a_finish_during_its_wait_is_returned_to_it(self):
        # (d) The sidekick finishes while the dispatch waits: notify must not mark the report seen under the dispatch's wait.
        pair = root / 'skills/pstack-pair/scripts/pair.sh'
        hook = ('#!/bin/sh\nST="$FAKE/../st"\nprintf "# R\\n\\nstatus: partial\\n" >"$ST/reports/002-y.md"\n'
                f'{pair} finish "$ST" "$ST/reports/002-y.md" >"$FAKE/finish.out" 2>&1\ncp "$ST/pair.json" "$FAKE/after-notify.json"\n')
        top, st, run = self.own_world('dwait', files={}, agents={'s1': 'idle claude'}, once={'wait': hook}, pair={})
        r = run('dispatch', '{st}', '{st}/briefs/002-y.md', '--timeout', '5000')
        self.assertIn('prompted master', (top / 'fake/finish.out').read_text())
        after = json.loads((top / 'fake/after-notify.json').read_text())
        self.assertEqual(after['pending'], [f'{st}/briefs/002-y.md'])
        self.assertNotIn('seen', after['sent'])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'report: {st}/reports/002-y.md', r.stdout)
        self.assertIn('report_status: partial', r.stdout)
        self.assertFalse((st / 'wait.pid').exists())

    def test_release_never_deletes_a_newer_owners_file(self):
        # (f)
        st = self.path / 'rel'
        st.mkdir()
        (st / 'wait.pid').write_text('newer\n')
        wait.release_wait(str(st), 'mine')
        self.assertEqual((st / 'wait.pid').read_text(), 'newer\n')
        wait.release_wait(str(st), 'newer')
        self.assertFalse((st / 'wait.pid').exists())
        wait.release_wait(str(st), 'newer')
        self.assertTrue(wait.superseded(str(st), 'newer'))

    def test_require_filled_reads_a_complete_placeholder_pair_in_prose_only(self):
        # A12. A double opening brace inside a fenced block or backticks is code; a complete pair in prose is a placeholder.
        ctx = Context('pstack-pair', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        f = self.path / 'filled.md'
        fence = '```'
        ok = {'a fenced block': f'text\n{fence}sh\necho ${{{{x}}}}\n{fence}\nmore\n', 'backticks': 'use `${{name}}` here\n', 'a lone opening pair in prose': 'a {{ is just a brace pair\n',
              'a fenced block with a placeholder-looking pair': f'{fence}\n{{{{x}}}}\n{fence}\n'}
        for label, text in ok.items():
            f.write_text(text)
            self.assertIsNone(review.require_filled(ctx, str(f)), label)
        bad = {'a pair around x in prose': 'fill {{x}} in\n', 'a pair across lines': 'fill {{x\nmore}} in\n', 'a pair after a closed fence': f'{fence}\ncode\n{fence}\n{{{{y}}}}\n',
               'a pair after a backtick span': 'see `a` then {{z}}\n'}
        for label, text in bad.items():
            f.write_text(text)
            with self.assertRaises(Die, msg=label):
                review.require_filled(ctx, str(f))

    def test_a_steer_whose_scope_effect_is_not_one_of_the_three_is_refused_before_any_herdr_call(self):
        # A5.
        for label, effect, ok in (('prose around the form', 'add "may write: x"', False), ('empty', '', False), ('a bare path', 'src/x.py', False),
                                  ('may write with no path', 'may write:', False), ('none', 'none', True), ('may write', 'may write: src/**', True),
                                  ('must not write', 'must not write: docs/x.md', True)):
            top, st, run = self.own_world('se-' + label.replace(' ', '-'), files={})
            steer = st / 'steers/001-x-s1.md'
            steer.parent.mkdir(exist_ok=True)
            steer.write_text(f'# Steer 001-s1: x\n\nbrief: {st}/briefs/001-x.md\nsupersedes: none\nkind: scope\nscope effect: {effect}\n\n## Direction\n\nDo it.\n')
            r = run('steer', '{st}', str(steer))
            calls = (top / 'fake/calls.log').read_text() if (top / 'fake/calls.log').exists() else ''
            if ok:
                self.assertEqual(r.returncode, 0, (label, r.stdout, r.stderr))
            else:
                self.assertEqual((r.returncode, r.stdout), (1, ''), label)
                self.assertEqual(r.stderr, f'pair.sh: {steer}: scope effect must be none, "may write: <path or glob>" or "must not write: <path or glob>"\n', label)
                self.assertNotIn('agent', calls, label)

    def test_steering_a_settled_sidekick_says_what_to_do_by_the_units_report(self):
        # A9. Exit 5, the first clause as before, the advice by the status of the unit's own report (not an -s<k> objection); no herdr prompt.
        for label, report, advice in (('blocked', 'blocked', 'apply its scope effect to the brief and re-dispatch it (dispatch {st} <brief> --send-only)'),
                                      ('partial', 'partial', 'apply its scope effect to the brief and re-dispatch it (dispatch {st} <brief> --send-only)'),
                                      ('done', 'done', 'write a fix brief'),
                                      ('no report', None, 'fold the steer into the next brief instead'),
                                      ('failed', 'failed', 'fold the steer into the next brief instead')):
            files = {} if report is None else {'reports/001-x.md': f'# R\n\nstatus: {report}\nhead: {{c3}}\n'}
            top, st, run = self.own_world('sa-' + label.replace(' ', '-'), files=files, agents={'s1': 'idle claude'})
            steer = st / 'steers/001-x-s1.md'
            steer.parent.mkdir(exist_ok=True)
            steer.write_text('# Steer 001-s1: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n')
            r = run('steer', '{st}', str(steer))
            self.assertEqual((r.returncode, r.stdout), (5, ''), label)
            self.assertEqual(r.stderr, f'pair.sh: sidekick s1 is idle and no objection is open on unit 001; {advice.format(st=st)}\n', label)
            calls = (top / 'fake/calls.log').read_text() if (top / 'fake/calls.log').exists() else ''
            self.assertNotIn('agent prompt', calls, label)

    def note_world(self, label, status, **spec):
        top, st, run = self.own_world(label, files={'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n'}, **spec)
        draft = st / 'notes/001-x-n1.md.draft'
        draft.parent.mkdir(exist_ok=True)
        draft.write_text(f'# Note 001-n1: x\n\nunit: 001-x\nstatus: {status}\n\n## Blocking\n\n- fix the thing\n\n## Follow-ups\n\n- none\n')
        return top, st, run, draft

    def test_a_blocking_note_reaches_a_working_sidekick_at_once(self):
        # A11. Claude through a prompt; Devin keeps it for its next notes; a clear note or a sidekick that is not working gets nothing.
        top, st, run, draft = self.note_world('nb', 'blocking', agents={'s1': 'working claude'})
        r = run('note', '{st}', str(draft))
        published = f'{st}/notes/001-x-n1.md'
        self.assertEqual((r.returncode, r.stdout), (0, f'published {published} (blocking); sent to s1\n'))
        prompts = [l for l in (top / 'fake/calls.log').read_text().splitlines() if l.startswith('agent prompt')]
        self.assertEqual(prompts, [f'agent prompt s1 pstack-pair NOTE {published}'])
        top, st, run, draft = self.note_world('nd', 'blocking', agents={'s1': 'working devin'}, kind='devin')
        r = run('note', '{st}', str(draft))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"published {st}/notes/001-x-n1.md (blocking); left for s1: it lands at the sidekick's next pair.sh step, notes, or progress", r.stdout)
        calls = (top / 'fake/calls.log').read_text() if (top / 'fake/calls.log').exists() else ''
        self.assertNotIn('agent prompt', calls)
        # parked: the next progress prints it once, the second does not
        first = run('progress', '{st}', 'one').stdout
        self.assertIn(f'NOTE: read {st}/notes/001-x-n1.md now, before your next tool call.', first)
        self.assertIn(f'pair.sh step {st} <sha> <summary> --resolves <n-ids>', first)
        self.assertNotIn('NOTE:', run('progress', '{st}', 'two').stdout)
        self.assertIn('fix the thing', run('notes', '{st}').stdout)
        for label, status, agents in (('clear note', 'clear', {'s1': 'working claude'}), ('idle sidekick', 'blocking', {'s1': 'idle claude'})):
            top, st, run, draft = self.note_world('nc-' + label.replace(' ', '-'), status, agents=agents)
            r = run('note', '{st}', str(draft))
            self.assertEqual((r.returncode, r.stdout), (0, f'published {st}/notes/001-x-n1.md ({status})\n'), label)
            calls = (top / 'fake/calls.log').read_text() if (top / 'fake/calls.log').exists() else ''
            self.assertNotIn('agent prompt', calls, label)

    def test_a_steer_scope_effect_in_every_form_the_template_describes_passes(self):
        # 008 review: several quoted paths and a note after an em dash are in steer-template.md and read by the digest.
        for effect in ('none', 'none \u2014 no scope change', 'none ', 'may write: src/**', 'may write: x \u2014 regenerated only',
                       'may write "src/a.py" and "src/b.py"', 'may write "`docs/two.md`" and "gen/**" \u2014 both', 'must not write: docs/x.md',
                       'must not write: docs/x.md \u2014 frozen'):
            top, st, run = self.own_world('sf-' + str(abs(hash(effect))), files={})
            steer = st / 'steers/001-x-s1.md'
            steer.parent.mkdir(exist_ok=True)
            steer.write_text(f'# Steer 001-s1: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: {effect}\n\n## Direction\n\nDo it.\n')
            r = run('steer', '{st}', str(steer))
            self.assertEqual(r.returncode, 0, (effect, r.stderr))

    def test_a_steer_with_a_brace_pair_in_code_passes_and_in_prose_fails(self):
        # steer.py reads placeholders through has_placeholder, as require_filled does.
        for label, body, ok in (('backticks', 'Use `${{name}}` here.', True), ('a fence', 'See:\n```sh\necho ${{x}}\n```', True),
                                ('prose', 'Fill {{this}} in.', False)):
            top, st, run = self.own_world('sb-' + label.replace(' ', '-'), files={})
            steer = st / 'steers/001-x-s1.md'
            steer.parent.mkdir(exist_ok=True)
            steer.write_text(f'# Steer 001-s1: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\n{body}\n')
            r = run('steer', '{st}', str(steer))
            if ok:
                self.assertEqual(r.returncode, 0, (label, r.stderr))
            else:
                self.assertIn('still has unfilled placeholders', r.stderr, label)

    def test_require_filled_follows_the_fence_span_and_template_rules(self):
        ctx = Context('pstack-pair', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        f = self.path / 'rules.md'
        bt, tl = '`', '~'
        fence3 = bt * 3
        template_verify = next(p for p in review.template_pairs() if p.startswith('{{exact commands'))
        store_pair = '{{STORE}}'
        ok = {'a CI matrix expression in a fence': f'{fence3}yaml\nruns-on: ${{{{ matrix.os }}}}\n{fence3}\n',
              'a double-brace x in backticks': 'say `{{x}}` here\n',
              'a line that opens and closes a fence on itself': f'{fence3}{{{{x}}}}{fence3} is code\n',
              'a longer fence holding a shorter one': f'{bt * 4}\n{fence3}\n{{{{x}}}}\n{bt * 4}\n',
              'a tilde fence': f'{tl * 3}\n{{{{x}}}}\n{tl * 3}\n',
              'a span by run length': f'``a`{{{{b}}}}`` c\n',
              'a double-backtick span holding one backtick and a placeholder': 'use ``a`b{{c}}`` here\n'}
        bad = {'an unclosed four-backtick fence, then a three-backtick pair around a placeholder': f'{bt * 4}\ncode\n{fence3}\n{{{{x}}}}\n{fence3}\n',
               'a one-line open and close, then a placeholder, then a fence line': f'{fence3}x{fence3}\n{{{{y}}}}\n{fence3}\n',
               'the template Verify placeholder in a fence': f'## Verify\n\n{fence3}bash\n{template_verify}\n{fence3}\n',
               'the STORE placeholder in backticks': f'see `{store_pair}/briefs/x.md`\n',
               'an unclosed fence is prose': f'{fence3}\n{{{{x}}}}\n',
               'a tilde does not close a backtick fence, so the pair stays code but a prose pair after the real close fails': f'{fence3}\n~~~\n{fence3}\n{{{{y}}}}\n',
               'a pair in prose beside a span': 'a `b` {{c}}\n'}
        for label, text in ok.items():
            f.write_text(text)
            self.assertIsNone(review.require_filled(ctx, str(f)), label)
        for label, text in bad.items():
            f.write_text(text)
            with self.assertRaises(Die, msg=label):
                review.require_filled(ctx, str(f))

    def test_the_guided_unfilled_re_reads_prose_only(self):
        ctx = Context('pstack-pair-guided', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '^(decided by|status): .*\\|')
        f = self.path / 'resp.md'
        fence3 = '`' * 3
        f.write_text(f'# R\n\nstatus: agree\n\n{fence3}\nstatus: agree | object\n{fence3}\n')
        self.assertIsNone(review.require_filled(ctx, str(f)))
        f.write_text('# R\n\nstatus: agree | object\n')
        with self.assertRaises(Die):
            review.require_filled(ctx, str(f))

    def test_steering_a_settled_sidekick_after_a_done_unit_advises_covers_only_in_the_kitchen_and_reads_the_units_own_report(self):
        # 008 review: --covers is the kitchen's. In the other variants say `write a fix brief`. And an -s<k> report that is not `object` beside a
        # blocked unit report must not be read as the unit's report.
        for skill, advice in (('pstack-pair', 'write a fix brief'), ('pstack-pair-guided', 'write a fix brief'), ('pstack-trio', 'write a fix brief')):
            top, st, run = self.own_world('sc-' + skill, skill=skill, files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}, agents={'s1': 'idle claude'})
            steer = st / 'steers/001-x-s1.md'
            steer.parent.mkdir(exist_ok=True)
            steer.write_text('# Steer 001-s1: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n')
            r = run('steer', '{st}', str(steer))
            self.assertEqual(r.stderr, f'pair.sh: sidekick s1 is idle and no objection is open on unit 001; {advice}\n', skill)
        top, st, run = self.own_world('sc-s1', files={'reports/001-x.md': '# R\n\nstatus: blocked\n', 'reports/001-x-s1.md': '# R\n\nstatus: partial\n'},
                                      agents={'s1': 'idle claude'})
        steer = st / 'steers/001-x-s2.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text('# Steer 001-s2: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n')
        r = run('steer', '{st}', str(steer))
        self.assertIn('apply its scope effect to the brief and re-dispatch it', r.stderr)

    def test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line(self):
        top, st, run, draft = self.note_world('np', 'blocking', agents={'s1': 'working claude'})
        published = f'{st}/notes/001-x-n1.md'
        # a failing prompt: the published line first, then the error, exit 2
        (top / 'fake/prompt-fail').write_text('')
        r = run('note', '{st}', str(draft))
        self.assertEqual((r.returncode, r.stdout), (2, f'published {published} (blocking)\n'))
        self.assertIn('herdr prompt failed for s1', r.stderr)
        # Prime takes the message through prime_steer
        from pstack_core.commands import notes as notes_mod
        sent = []
        ctx = Context('pstack-pair', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        pair = self.path / 'primestore'
        pair.mkdir()
        (pair / 'pair.json').write_text(json.dumps({'sidekick': {'name': 's1', 'kind': 'prime-agent'}}))
        with mock.patch.object(notes_mod, 'agent_status', return_value='working'), \
                mock.patch.object(notes_mod, 'prime_steer', side_effect=lambda store, message: sent.append((store, message)) or True):
            self.assertEqual(notes_mod.deliver_blocking_note(ctx, str(pair), '/p/n.md'), '; sent to s1')
        self.assertEqual(sent, [(str(pair), 'pstack-pair NOTE /p/n.md')])

    def test_the_idle_path_after_a_marked_report_shows_the_queue_note_and_review_delta(self):
        # A report notify marked is not returned; the master's next wait says so and adds what the report path would have: the queue note
        # (held behind a report that is not done) and the review delta. A marked plan response gets the idle line alone.
        steps = {'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n', 'notes/001-x-n1.md': '# N\n\nstatus: clear\n', 'queue': '{st}/briefs/002-y.md\n'}
        top, st, run = self.own_world('idle1', agents={'s1': 'idle claude'}, files={'reports/001-x.md': '# R\n\nstatus: partial\nhead: {c3}\n', **steps})
        self.assertIn('prompted master', run('notify', '{st}', '{st}/reports/001-x.md').stdout)
        r = run('wait', '{st}', '--timeout', '1000')
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        idle = next(i for i, l in enumerate(lines) if l.startswith('idle: the reply to 001-x.md was already shown'))
        self.assertEqual(lines[idle + 1], f'queued: {st}/briefs/002-y.md (held: report is partial; dispatch it yourself)')
        self.assertTrue(lines[idle + 2].startswith('review-delta: '), lines)
        self.assertNotIn('report:', r.stdout.replace(f'({st}/reports/001-x.md)', ''))
        sent = {'file': '{st}/plans/001-x.md', 'kind': 'PLAN', 'epoch': 1}
        top, st, run = self.own_world('idle2', agents={'s1': 'idle claude'}, files={'reports/001-x.md': '# R\n\nstatus: agree\nhead: {c3}\n', 'plans/001-x.md': '# P\n', **steps},
                                      pair={'sent': sent, 'pending': []})
        run('notify', '{st}', '{st}/reports/001-x.md')
        r = run('wait', '{st}', '--timeout', '1000')
        self.assertNotIn('queued:', r.stdout)
        self.assertNotIn('review-delta:', r.stdout)

    def test_notify_and_finish_canonicalise_the_store_they_are_given(self):
        # A store given as <dir>/../<store> must clear `pending` and take the ADVICE branch as the canonical path does.
        top, st, run = self.own_world('canon')
        odd = f'{st}/../st'
        r = run('notify', odd, '{st}/reports/001-x.md')
        self.assertIn('prompted master', r.stdout, r.stderr)
        self.assertEqual(json.loads((st / 'pair.json').read_text())['pending'], [])
        top, st, run = self.own_world('canon2', files={'advice/001-x.md': '# A\n\nstatus: answered\n'})
        odd = f'{st}/../st'
        r = run('notify', odd, '{st}/advice/001-x.md')
        self.assertIn('ADVICE', r.stdout)
        top, st, run = self.own_world('canon3')
        r = run('finish', f'{st}/../st', '{st}/reports/001-x.md')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((st / 'pair.json').read_text())['pending'], [])

    def test_a_lock_that_stays_held_never_turns_a_delivered_dispatch_or_a_printed_report_into_a_failure(self):
        # The claim takes the store lock. Held for the claim's one try and freed by the time the wait finds its report, the wait still returns
        # the report with exit 0, says so on stderr, and does not check for a newer owner it could not have been replaced by.
        hook = '#!/bin/sh\nrmdir "$FAKE/../st/.pair.lock"\nprintf "# R\\n\\nstatus: partial\\n" >"$FAKE/../st/reports/001-x.md"\n'
        top, st, run = self.own_world('lk', files={}, once={'wait': hook})
        (st / '.pair.lock').mkdir()
        r = run('wait', '{st}', '--timeout', '5000', extra_env={'PAIR_LOCK_TRIES': '1', 'PAIR_LOCK_POLL_S': '0'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'report: {st}/reports/001-x.md', r.stdout)
        self.assertEqual(r.stderr, 'pair.sh: wait: could not claim wait.pid (pair.json lock held for 10s; remove '
                                   f'{st}/.pair.lock if no pair.sh is running); waiting without ownership\n')

    def test_owned_wait_keeps_its_outcome_when_the_claim_or_the_release_cannot_take_the_lock(self):
        ctx = Context('pstack-pair', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        for fail_claim, fail_release in ((True, False), (False, True), (True, True)):
            err = tempfile.TemporaryFile('w+')
            self.addCleanup(err.close)
            tokens = []

            def claim(store, token):
                if fail_claim:
                    raise Die('pair.json lock held for 10s; remove x if no pair.sh is running')

            def release(store, token):
                raise Die('pair.json lock held for 10s; remove x if no pair.sh is running')

            def reply(ctx_, store, name, timeout, now=None, token=None):
                tokens.append(token)
                return wait.WaitResult(0, 'idle', '', '/s/reports/001-x.md')

            def finish(ctx_, store, interval, result, mode, report='', now=None):
                sys.stdout.write('report: /s/reports/001-x.md\n')
                return 0

            with mock.patch.object(wait, 'claim_wait', claim), mock.patch.object(wait, 'release_wait', release if fail_release else (lambda *a: None)), \
                    mock.patch.object(wait, 'wait_for_reply', reply), mock.patch.object(wait, 'finish_wait', finish), mock.patch.object(sys, 'stderr', err), \
                    mock.patch('sys.stdout', new_callable=io.StringIO) as out, mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop('PAIR_WAIT_OWNER', None)
                code = wait.owned_wait(ctx, '/s', 's1', '1000', '9')
            err.seek(0)
            text = err.read()
            self.assertEqual((code, out.getvalue()), (0, 'report: /s/reports/001-x.md\n'), (fail_claim, fail_release))
            self.assertEqual(text.count('could not claim wait.pid'), 1 if fail_claim else 0, text)
            self.assertEqual(text.count('could not release wait.pid'), 1 if fail_release and not fail_claim else 0, text)
            self.assertEqual(tokens, [None] if fail_claim else [str(os.getpid())])

    def test_wait_release_checks_its_store(self):
        top, st, run = self.own_world('rl')
        r = run('wait', '{st}/nope', '--release')
        self.assertEqual((r.returncode, r.stdout), (2, 'state: \n'))
        self.assertIn(f'pair.sh: no pair.json in {st}/nope', r.stderr)
        (st / 'wait.pid').write_text('x\n')
        self.assertEqual(run('wait', '{st}', '--release').returncode, 0)
        self.assertTrue((st / 'wait.pid').exists())

    def test_a_report_written_in_the_last_slice_is_returned_though_the_deadline_has_passed(self):
        # The fake herdr's once hook writes the report during the wait's first `agent wait`; with --timeout 0 the deadline is already past when
        # that call returns. The wait looks once more before it gives up.
        hook = '#!/bin/sh\nprintf "# R\\n\\nstatus: partial\\n" >"$FAKE/../st/reports/001-x.md"\n'
        top, st, run = self.own_world('late', files={}, once={'wait': hook})
        r = run('wait', '{st}', '--timeout', '0')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'report: {st}/reports/001-x.md', r.stdout)
        self.assertIn('report_status: partial', r.stdout)

    def test_a_steer_with_an_empty_quoted_scope_path_is_refused(self):
        top, st, run = self.own_world('se-empty', files={})
        steer = st / 'steers/001-x-s1.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text('# Steer 001-s1: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: may write ""\n\n## Direction\n\nDo it.\n')
        r = run('steer', '{st}', str(steer))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('scope effect must be none', r.stderr)
        self.assertNotIn('agent', (top / 'fake/calls.log').read_text() if (top / 'fake/calls.log').exists() else '')

    def test_a_step_on_another_unit_resolves_a_qualified_note_and_a_bare_id_stays_this_units(self):
        # B1. Unit 002 is running; unit 001's blocking note n1 is closed by a step on 002 that names it as 001-n1. A bare n1 on 002 is 002's.
        files = {'notes/001-x-n1.md': '# N\n\nstatus: blocking\n', 'notes/002-y-n1.md': '# N\n\nstatus: blocking\n'}
        top, st, run = self.own_world('qn', files=files, pair={'dispatch': {'brief': '{st}/briefs/002-y.md', 'generation': 1}, 'heads': {'002-y': '{c1}'}})
        open_for = lambda unit: [os.path.basename(f) for f in review.open_notes(str(st), f'{unit}.md')]
        self.assertEqual((open_for('001-x'), open_for('002-y')), (['001-x-n1.md'], ['002-y-n1.md']))
        r = run('step', '{st}', '{c3}', 'fixes', '--resolves', '001-n1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((open_for('001-x'), open_for('002-y')), ([], ['002-y-n1.md']))
        r = run('step', '{st}', '{c2}', 'own', '--resolves', 'n1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((open_for('001-x'), open_for('002-y')), ([], []))
        for bad, text in (('001-n9', 'no published note n9 on unit 001'), ('009-n1', 'no published note n1 on unit 009'), ('1-n1', '--resolves takes note ids')):
            r = run('step', '{st}', '{c1}', 'x', '--resolves', bad)
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn(text, r.stderr, bad)

    def test_notes_with_a_trailing_slash_store_still_counts_a_bare_id_resolved(self):
        # review 011 (iii): the steps file's path and the glob's differ by a slash; they are the same file.
        files = {'notes/002-y-n1.md': '# N\n\nstatus: blocking\n'}
        top, st, run = self.own_world('slash', files=files, pair={'dispatch': {'brief': '{st}/briefs/002-y.md', 'generation': 1}, 'heads': {'002-y': '{c1}'}})
        self.assertEqual(run('step', '{st}', '{c3}', 'own', '--resolves', 'n1').returncode, 0)
        self.assertEqual(review.open_notes(str(st) + '/', '002-y.md'), [])
        self.assertEqual(run('notes', str(st) + '/').stdout, 'notes: none open; go on with the next step\n')

    def test_notify_marks_nothing_while_a_live_wait_owns_the_store(self):
        # A1 and A2 together: the running wait returns the report itself, so notify must leave it unseen.
        top, st, run = self.own_world('nl')
        (st / 'wait.pid').write_text(f'{os.getpid()}\n')
        before = (st / 'pair.json').read_text()
        r = run('notify', '{st}', '{st}/reports/001-x.md')
        self.assertIn('prompted master m1', r.stdout)
        self.assertEqual((st / 'pair.json').read_text(), before)
        self.assertEqual((st / 'wait.pid').read_text(), f'{os.getpid()}\n')
        r = run('wait', '{st}', '--timeout', '1000')
        self.assertIn(f'report: {st}/reports/001-x.md', r.stdout)

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
                # Unit 011 (B6): classify keeps `risk: escalated (a contract)` when the class is unchanged; the base rewrote it to `risk: escalated`.
                side['files'] = {k: re.sub(r'(?m)^risk: (routine|escalated|landing) \(.*\)$', r'risk: \1', v) for k, v in side['files'].items()}
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
            # Unit 011 (B1): `--resolves` also takes another unit's note (001-n2), and its error and usage say so; the base's words are restored here.
            mask = lambda text: re.sub(r'\S*pair-core\.sh: line [0-9]+: \$2: unbound variable', '<core>: line N: $2: unbound variable',
                                       text.replace(", or 001-n2 for another unit's note", '').replace(' | 001-n1]', ']'))
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
            'a later step': dict(args=['{st}', '{c3}', 'did more'], files={'steps/001-x.tsv': rows}),
            'an empty steps file': dict(args=['{st}', sha, 'did more'], files={'steps/001-x.tsv': ''}),
            'a steps file of blank lines only': dict(args=['{st}', sha, 'did more'], files={'steps/001-x.tsv': '\n\n'}),
            'a steps file with blank lines': dict(args=['{st}', '{c3}', 'did more'], files={'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n\n\n2\t{c2}\t6\t\ttwo\n\n'}),
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
            'the first step': dict(base, args=['step', '{st}', '{c3}', 'did a thing']),
            'a step that resolves a published note': dict(base, args=['step', '{st}', '{c3}', 'fix', '--resolves', 'n1'], files={'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'a paused store': dict(base, args=['step', '{st}', '{c3}', 'x'], files={'paused': 'lunch\n'}),
            'a step that resolves an unpublished note': dict(base, args=['step', '{st}', '{c3}', 'x', '--resolves', 'n9']),
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
            files = {str(f.relative_to(st)): f.read_text(errors='replace').replace(str(top), '<T>') for f in sorted(st.rglob('*')) if f.is_file() and f.name not in ('events.tsv', 'wait.pid')}
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
                # unit 011 (B6): a reason after the class on the brief's `risk:` line is kept when the class is unchanged; the base dropped it
                side['files'] = {k: re.sub(r'(?m)^risk: (routine|escalated|landing) \(.*\)$', r'risk: \1', v) for k, v in side['files'].items()}
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
        masked = [mask(got, root / 'skills'), mask(want, bases)]
        for pattern, replacement in spec.get('mask', ()):
            masked = [json.loads(re.sub(pattern, replacement, json.dumps(o))) for o in masked]
        return masked[0], masked[1]

    def phase4_ab(self, label, spec):
        """One kitchen world run through b632713's kitchen.sh and the working tree's: (working tree, base), the two trees' paths masked."""
        bases = self.phase4_skills()
        toml = (spec.get('toml') or self.KITCHEN_TOML) + ('\n[review]\nengine = "joo"\n' if spec.get('review_engine') else '')
        spec = {**spec, 'repo_files': {**spec.get('repo_files', {}), **({} if spec.get('toml', 1) is None else {'.agents/kitchen.toml': toml})}}
        want = self.ab_run(f'{label}-base', bases, 'pstack-kitchen', spec)
        got = self.ab_run(f'{label}-port', root / 'skills', 'pstack-kitchen', spec)
        for outcome, tag in ((want, 'base'), (got, 'port')):
            outcome['state'] = {} if spec.get('skip') else self.state_tree(self.path / f'{label}-{tag}' / 'state', self.path / f'{label}-{tag}')
        mask = lambda o, tree: json.loads(re.sub(r'(\.(?:closing|filing|maintainer)-)[A-Za-z0-9]{6}', r'\1<R>', json.dumps(o).replace(json.dumps(str(tree))[1:-1], '<SK>')))
        masked = [mask(got, root / 'skills'), mask(want, bases)]
        # the scratch worktree is named for the store's directory, and the two worlds' directories carry the label
        masked = [json.loads(re.sub(rf'{re.escape(label)}-(base|port)', f'{label}-X', json.dumps(o))) for o in masked]
        for pattern, replacement in spec.get('mask', ()):
            masked = [json.loads(re.sub(pattern, replacement, json.dumps(o))) for o in masked]
        return masked[0], masked[1]

    def state_tree(self, directory, top):
        """What a run left under the host state directory (the kitchen's ledger and timings), times masked."""
        found = {}
        for path in sorted(directory.rglob('*')) if directory.is_dir() else ():
            if path.is_symlink():
                found[re.sub(r'repo-[0-9a-f]{8}', 'repo-<H>', str(path.relative_to(directory)).replace(top.name, '<T>')) + '@'] = os.readlink(path)
            elif path.is_dir():
                found[re.sub(r'repo-[0-9a-f]{8}', 'repo-<H>', str(path.relative_to(directory)).replace(top.name, '<T>')) + '/'] = ''
            elif path.is_file():
                text = path.read_text(errors='replace').replace(str(top), '<T>')
                found[re.sub(r'repo-[0-9a-f]{8}', 'repo-<H>', str(path.relative_to(directory)).replace(top.name, '<T>'))] = re.sub(r'\b1[0-9]{9}\b', '<EPOCH>', re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', '<TS>', text))
        return found

    def phase4_cases(self, label, cases, expect):
        """Every case through both trees; they must be equal, and the working tree's run must have done what the case says: `expect` maps every case
        to (rc, text), its exit status and a piece of its stdout or stderr. A case with no entry, or an entry with no case, fails the test, so a
        case whose setup stops it short (a dead world, a usage error) cannot pass as an equal pair of failures. Returns the outcomes by name."""
        self.assertEqual(sorted(expect), sorted(cases), f'{label}: expect names exactly the cases')
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            got, want = self.phase4_ab(f'{label}{n}', spec)
            self.maxDiff = None
            self.assertEqual(got, want, name)
            rc, text = expect[name]
            self.assertEqual(got['rc'], rc, f'{name}: {got["stdout"]}{got["stderr"]}')
            self.assertIn(text, f'{got["stdout"]}\n{got["stderr"]}', name)
            if not text:
                self.assertEqual((got['stdout'], got['stderr']), ('', ''), f'{name}: an expectation of no text is for a run that prints nothing')
            outcomes[name] = got
        return outcomes

    def test_catch_matches_the_bash_at_the_phase_4_base(self):
        run = ['catch', '{st}', 'gate', 'a missed lint']
        cases = {
            'a catch': dict(args=run),
            'a catch with a unit': dict(args=run + ['001']),
            'a text that starts with a dash': dict(args=['catch', '{st}', 'review', '--not-an-option']),
            'a bad layer': dict(args=['catch', '{st}', 'luck', 'x']),
            'a git_root that is not a repository': dict(args=run, pair={'git_root': '/nonexistent/repo'}),
            'a bad unit': dict(args=run + ['3']),
            'too few arguments': dict(args=['catch', '{st}', 'gate']),
            'a unit with no brief': dict(args=run + ['009']),
            'too many arguments': dict(args=run + ['001', 'extra']),
            'no pair.json': dict(args=run, nopair=True),
        }
        out = self.phase4_cases('catch', cases, {
            'a catch': (0, 'caught at gate: a missed lint'),
            'a catch with a unit': (0, 'caught at gate: a missed lint'),
            'a text that starts with a dash': (0, 'caught at review: --not-an-option'),
            'a bad layer': (1, 'pair.sh: layer is gate, policy, self, verify, review, triage, or escalation: the one that '),
            'a git_root that is not a repository': (1, 'kitchen: not a git repository: /nonexistent/repo'),
            'a bad unit': (1, "pair.sh: unit is a brief's number, like 003"),
            'too few arguments': (1, 'pair.sh: usage: kitchen.sh catch'),
            'a unit with no brief': (1, 'kitchen: no brief for unit 009 in'),
            'too many arguments': (1, 'pair.sh: usage: kitchen.sh catch'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
        })
        self.assertEqual(out['a catch']['rc'], 0)
        self.assertIn('caught at gate: a missed lint', out['a catch']['stdout'])
        self.assertTrue(any('ledger.tsv' in k and 'a missed lint' in v for k, v in out['a catch']['state'].items()), out['a catch']['state'])
        self.assertNotEqual(out['a unit with no brief']['rc'], 0)
        self.assertEqual(out['a bad layer']['rc'], 1)
        self.assertEqual(out['no pair.json']['rc'], 1)

    def kitchen_run_files(self):
        """A store with two units reported, one clean verdict, one reject, two steers, wakes, an escalation and a failover."""
        ev = lambda n, actor, event, unit='-', detail='': f'2026-10-0{n}T10:00:00Z\t{1790000000 + n}\t{actor}\t{event}\t{unit}\t{detail}\n'
        return {
            'reports/001-x.md': '# Report\n\nstatus: done\nhead: {c2}\n', 'reports/002-y.md': '# Report\n\nstatus: partial\nhead: {c3}\n',
            'verdicts/001-x-v1.md': '# V\n\nstatus: clean\nunits: 001\n', 'verdicts/002-y-v1.md': '# V\n\nstatus: reject\nunits: 002\n\n## Findings\n\n1. it broke\n',
            'verdicts/002-y-v1-packet.md': '# P\n\nverifier-outcome: reject\n', 'verdicts/002-y-v2.md': '# V\n\nstatus: clean\nunits: 002\n',
            'steers/001-x-s1.md': '# Steer\n\nkind: scope\n\n## Direction\n\nstay in src\n', 'steers/002-y-s1.md': '# Steer\n\nkind: redirect\n\n## Direction\n\ndo it again\n',
            'reviews/001-x.md': '# R\n\nverdict: accept\n',
            'events.tsv': 'ts\tepoch\tactor\tevent\tunit\tdetail\n' + ev(1, 'master', 'init') + ev(2, 'master', 'wake', '001-x', 'sidekick:done')
            + ev(3, 'master', 'wake', '002-y', 'verify:clean') + ev(4, 'master', 'escalate', '002-y', 'contract') + ev(5, 'master', 'failover', '-', 'pi'),
            'gates.tsv': '1\t001-x\tabc\tfail\tpstack\n'}

    def test_retro_matches_the_bash_at_the_phase_4_base(self):
        run = ['retro', '{st}']
        rich = self.kitchen_run_files()
        no_verdicts = {k: v for k, v in rich.items() if not k.startswith('verdicts/')}
        cases = {
            'a store with briefs only': dict(args=run),
            'units, verdicts, steers and events': dict(args=run, files=rich, pair={'slug': 'demo'}),
            'no verified unit': dict(args=run, files=no_verdicts),
            'a git_root that is not a repository': dict(args=run, files=rich, pair={'slug': 'demo', 'git_root': '/nonexistent/repo'}),
            'no pair.json': dict(args=run, nopair=True),
            'too many arguments': dict(args=run + ['x']),
            'no arguments': dict(args=['retro']),
        }
        out = self.phase4_cases('retro', cases, {
            'a store with briefs only': (0, 'run null: 0 unit reports, 0 verified units (0 clean verdicts on the first try), 0 master w'),
            'units, verdicts, steers and events': (0, 'run demo: 1 unit reports, 2 verified units (1 clean verdicts on the first try), 0 master w'),
            'no verified unit': (0, 'run null: 1 unit reports, 0 verified units (0 clean verdicts on the first try), 0 master w'),
            'a git_root that is not a repository': (1, 'kitchen: not a git repository: /nonexistent/repo'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'too many arguments': (1, 'pair.sh: usage: kitchen.sh retro'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh retro'),
        })
        rich_out = out['units, verdicts, steers and events']
        self.assertEqual(rich_out['rc'], 0, rich_out['stderr'])
        self.assertIn('master wakes per verified unit:', rich_out['stdout'])
        self.assertIn('run demo: 1 unit reports, 2 verified units', rich_out['stdout'])
        self.assertRegex(rich_out['stdout'], r'(?m)^ +2 steer +brief +in 1 runs$')
        self.assertIn('repeated across runs', rich_out['stdout'])
        self.assertNotIn('master wakes per verified unit', out['no verified unit']['stdout'])

    def test_catch_retro_and_new_handoff_on_an_unparseable_pair_json_die_with_pythons_message(self):
        """Accepted (PARITY.md, unparseable pair.json): the bash read pair.json with jq. catch went on and wrote a ledger row with an empty run
        (rc 0); retro and new-handoff stopped with jq's rc 5. The port reads it once and dies with `kitchen.sh: Expecting value...`, rc 1,
        writing nothing."""
        for args, base_rc in ((['catch', '{st}', 'gate', 'x'], 0), (['retro', '{st}'], 5), (['new-handoff', '{st}'], 5)):
            got, want = self.phase4_ab('unp' + args[0], dict(args=args, nopair=True, files={'pair.json': 'not json\n'}))
            self.assertEqual((want['rc'], got['rc']), (base_rc, 1), args[0])
            self.assertIn('parse error', want['stderr'])
            self.assertTrue(got['stderr'].endswith('pair.sh: Expecting value: line 1 column 1 (char 0)\n'), got['stderr'])
            files = lambda tree: {k: v for k, v in tree.items() if not k.endswith('/')}
            self.assertEqual(files(got['state']), {}, args[0])
            self.assertEqual(bool(files(want['state'])), args[0] == 'catch')

    def test_feedback_on_an_unparseable_pair_json_dies_with_pythons_message(self):
        """Accepted (PARITY.md): the bash read the slug with jq in a plain assignment and stopped with jq's rc 5 after creating inbox/ and done/.
        The port reads pair.json once: `kitchen.sh: Expecting value...`, rc 1, after the same two directories."""
        got, want = self.phase4_ab('fbunp', dict(args=['feedback', '{st}', '{st}/report.md'], nopair=True, files={'pair.json': 'not json\n', 'report.md': self.FEEDBACK_REPORT}))
        self.assertEqual((want['rc'], got['rc']), (5, 1))
        self.assertIn('parse error', want['stderr'])
        self.assertTrue(got['stderr'].endswith('pair.sh: Expecting value: line 1 column 1 (char 0)\n'), got['stderr'])
        self.assertEqual(got['state'], want['state'])
        self.assertIn('pstack/feedback/inbox/', got['state'])

    def test_new_handoff_matches_the_bash_at_the_phase_4_base(self):
        rich = self.kitchen_run_files()
        run = ['new-handoff', '{st}']
        pair = {'slug': 'demo', 'master': {'name': 'm1', 'generation': 3}, 'dispatch': {'brief': '{st}/briefs/002-y.md'}, 'verifying': {'unit': '001-x'}}
        cases = {
            'a store with units in every state': dict(args=run, files={**rich, 'queue': '{st}/briefs/003-z.md\n', 'briefs/003-z.md': '# B\n'}, pair=pair),
            'a store with briefs only': dict(args=run, pair={'slug': 'demo', 'dispatch': {}}),
            'an existing handoff is overwritten': dict(args=run, files={'session-handoff.md': 'old text\n', **rich}, pair=pair),
            'a master with no generation': dict(args=run, files=rich, pair={'slug': 'demo', 'master': {'name': 'm1'}}),
            'a master that is a string ends with the jq error': dict(args=run, files=rich, pair={'slug': 'demo', 'master': 'm1'}),
            'a dispatch that is a string is jq\'s error and the command goes on': dict(args=run, files=rich, pair={'slug': 'demo', 'dispatch': 'x'}),
            'a verifying that is an array': dict(args=run, files=rich, pair={'slug': 'demo', 'verifying': [1]}),
            'a dangling brief symlink is skipped': dict(args=run, files=rich, pair={'slug': 'demo'}, links={'st/briefs/009-z.md': '/nonexistent/brief.md'}),
            'no pair.json': dict(args=run, nopair=True),
            'too many arguments': dict(args=run + ['x']),
            'no arguments': dict(args=['new-handoff']),
        }
        out = self.phase4_cases('handoff', cases, {
            'a store with units in every state': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'a store with briefs only': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'an existing handoff is overwritten': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'a master with no generation': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'a master that is a string ends with the jq error': (5, '/st/pair.json:15): Cannot index string with string ("generation")'),
            "a dispatch that is a string is jq's error and the command goes on": (0, 'Cannot index string with string ("brief")'),
            'a verifying that is an array': (0, 'Cannot index array with string ("unit")'),
            'a dangling brief symlink is skipped': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'too many arguments': (1, 'pair.sh: usage: kitchen.sh new-handoff'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh new-handoff'),
        })
        first = out['a store with units in every state']
        self.assertEqual(first['rc'], 0, first['stderr'])
        text = first['files']['session-handoff.md']
        self.assertIn('| 001 x | done | clean | accept |', text)
        self.assertIn('| 002 y | partial | clean | - |', text)
        self.assertIn('dispatched: 002-y.md', text)
        self.assertIn('verifying: 001-x', text)
        self.assertIn('queued: 003-z.md', text)
        self.assertIn('(master generation 3)', text)
        self.assertNotIn('old text', out['an existing handoff is overwritten']['files']['session-handoff.md'])
        string_master = out['a master that is a string ends with the jq error']
        self.assertEqual((string_master['rc'], 'session-handoff.md' in string_master['files']), (5, False))
        self.assertIn('Cannot index string with string ("generation")', string_master['stderr'])
        self.assertIn('Cannot index string with string ("brief")', out["a dispatch that is a string is jq's error and the command goes on"]['stderr'])
        self.assertNotIn('009-z', out['a dangling brief symlink is skipped']['files']['session-handoff.md'])

    FEEDBACK_REPORT = '# Feedback\n\n## Gaps\n\n### 1. a gap\n\nit hurt\n\n### 2. another\n\nagain\n'
    MAINTAINER_JSON = '{"name": "mt", "pane": "p7", "session": null, "since": "2026-10-01T10:00:00Z"}\n'

    @staticmethod
    def feedback_filed(now=None, days=19):
        """The `filed:` stamp of a report filed `days` days (and an hour, so the age is whole days for a day) before `now`: the age --inbox prints is then
        the same on every date the test runs."""
        return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime((time.time() if now is None else now) - days * 86400 - 3600))

    def test_feedback_filed_is_the_same_distance_from_any_date(self):
        stamp = lambda y, m, d: calendar.timegm((y, m, d, 12, 0, 0))
        for day in (stamp(2026, 10, 21), stamp(2026, 12, 31), stamp(2027, 3, 1)):
            filed = calendar.timegm(time.strptime(self.feedback_filed(day), '%Y-%m-%dT%H:%M:%SZ'))
            self.assertEqual(day - filed, 19 * 86400 + 3600)

    def feedback_inbox_file(self, id_, filed=None, build='abc123 (x)', gaps=2, status='open'):
        filed = self.feedback_filed() if filed is None else filed
        gap_text = ''.join(f'### {n}. gap\n\n' for n in range(1, gaps + 1))
        head = f'# Kitchen feedback: run{id_[-1]} (repo{id_[-1]})\nid: {id_}\nrun: run{id_[-1]}   store: /s   repo: /r   master: m (p)\n'
        return head + (f'build: {build}\n' if build else '') + (f'filed: {filed}\n' if filed else '') + f'status: {status}\n\n## Gaps\n\n{gap_text}'

    def test_feedback_filing_matches_the_bash_at_the_phase_4_base(self):
        stem = f'{time.strftime("%Y%m%d", time.gmtime())}-repo-demo'
        file_args = ['feedback', '{st}', '{st}/report.md']
        base = dict(args=file_args, files={'report.md': self.FEEDBACK_REPORT}, pair={'slug': 'demo'})
        live = dict(agents={'mt': 'idle claude'}, panes={'p7': 'mt'}, statefiles={'pstack/feedback/maintainer.json': self.MAINTAINER_JSON})
        cases = {
            'no maintainer: the human is notified': base,
            'a live maintainer is told': {**base, **live},
            'a maintainer whose prompt fails': {**base, **live, 'promptfail': True},
            'a dead maintainer': {**base, 'statefiles': live['statefiles']},
            'a maintainer moved off its pane': {**base, **{**live, 'panes': {'p9': 'mt'}}},
            'a maintainer with another session': {**base, **{**live, 'statefiles': {'pstack/feedback/maintainer.json': self.MAINTAINER_JSON.replace('null', '"other"')}}},
            'a maintainer blocked on a dialog': {**base, **{**live, 'agents': {'mt': 'blocked claude'}}},
            'builds: the same skills and another': {**base, 'pair': {'slug': 'demo', 'skill_builds': [{'path': '/x/skills'}]},
                                                   'statefiles': {'pstack/builds.tsv': 'a\told\t/x/skills\nb\tabc123\t/x/skills\n'}},
            'the name is taken once': {**base, 'statefiles': {f'pstack/feedback/inbox/{stem}.md': 'x\n'}},
            'the name is taken in inbox and done': {**base, 'statefiles': {f'pstack/feedback/inbox/{stem}.md': 'x\n', f'pstack/feedback/done/{stem}-2.md': 'x\n'}},
            'a stale lock is taken over': {**base, 'statelocks': {'pstack/feedback/.lock': 300}},
            'a maintainer.json that is not JSON': {**base, 'statefiles': {'pstack/feedback/maintainer.json': 'not json\n'}},
            'a maintainer.json that is an array': {**base, **live, 'statefiles': {'pstack/feedback/maintainer.json': '[1]\n'}},
            'herdr answers an agent of null': {**base, 'statefiles': live['statefiles'], 'agent_get': '{"result":{"agent":null}}\n'},
            'a master that is a string': {**base, 'pair': {'slug': 'demo', 'master': 'm1'}},
            'a master that is an array': {**base, 'pair': {'slug': 'demo', 'master': [1]}},
            'a skill_builds entry that is a number stops with jq, leaving the lock': {**base, 'pair': {'slug': 'demo', 'skill_builds': [1]}},
            'a skill_builds entry that is an array': {**base, 'pair': {'slug': 'demo', 'skill_builds': [[1]]}},
            'a skill_builds path that is a number': {**base, 'pair': {'slug': 'demo', 'skill_builds': [{'path': 3}]}},
            'a dangling link at the name being allocated': {**base, 'statelinks': {f'pstack/feedback/inbox/{stem}.md': '/nonexistent/x'}},
            'a report with no gap': dict(base, files={'report.md': '# Feedback\n\n## Gaps\n\nnone\n\n## Other\n\n### 1. not a gap\n'}),
            'a missing report file': dict(base, args=['feedback', '{st}', '{st}/none.md']),
            'no pair.json': dict(base, nopair=True),
            'no arguments': dict(args=['feedback']),
            'one argument': dict(args=['feedback', '{st}']),
            'three arguments': dict(args=file_args + ['x']),
        }
        out = self.phase4_cases('fbfile', cases, {
            'no maintainer: the human is notified': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'a live maintainer is told': (0, 'delivered: to maintainer mt'),
            'a maintainer whose prompt fails': (0, 'delivered: notification for the human (the prompt to the maintainer failed); read it with:'),
            'a dead maintainer': (0, 'delivered: notification for the human (agent mt is gone); read it with: cat'),
            'a maintainer moved off its pane': (0, 'delivered: notification for the human (agent mt moved off pane p7); read it with: cat'),
            'a maintainer with another session': (0, 'delivered: notification for the human (agent mt is a different session than the one regist'),
            'a maintainer blocked on a dialog': (0, 'delivered: notification for the human (agent mt is blocked on a dialog); read it with: cat'),
            'builds: the same skills and another': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'the name is taken once': (0, '-repo-demo-2.md'),
            'the name is taken in inbox and done': (0, '-repo-demo-3.md'),
            'a stale lock is taken over': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'a maintainer.json that is not JSON': (0, 'delivered: notification for the human (agent  is gone); read it with: cat'),
            'a maintainer.json that is an array': (0, 'delivered: notification for the human (agent  is gone); read it with: cat'),
            'herdr answers an agent of null': (0, 'delivered: notification for the human (agent mt moved off pane p7); read it with: cat'),
            'a master that is a string': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'a master that is an array': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'a skill_builds entry that is a number stops with jq, leaving the lock': (5, '/st/pair.json:20): Cannot index number with string ("path")'),
            'a skill_builds entry that is an array': (5, '/st/pair.json:22): Cannot index array with string ("path")'),
            'a skill_builds path that is a number': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'a dangling link at the name being allocated': (0, 'delivered: notification for the human (no maintainer is registered); read it with: cat'),
            'a report with no gap': (1, '/pstack-kitchen/references/feedback-template.md)'),
            'a missing report file': (1, 'pair.sh: no such file:'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh feedback'),
            'one argument': (1, 'pair.sh: usage: kitchen.sh feedback'),
            'three arguments': (1, 'pair.sh: usage: kitchen.sh feedback'),
        })
        self.assertIn('delivered: notification for the human (no maintainer is registered)', out['no maintainer: the human is notified']['stdout'])
        self.assertIn('delivered: to maintainer mt', out['a live maintainer is told']['stdout'])
        self.assertIn('prompt to the maintainer failed', out['a maintainer whose prompt fails']['stdout'])
        self.assertIn('agent mt is gone', out['a dead maintainer']['stdout'])
        self.assertIn('moved off pane p7', out['a maintainer moved off its pane']['stdout'])
        self.assertIn('different session', out['a maintainer with another session']['stdout'])
        self.assertIn('blocked on a dialog', out['a maintainer blocked on a dialog']['stdout'])
        self.assertTrue(any(k.endswith(f'{stem}-3.md') for k in out['the name is taken in inbox and done']['state']), out['the name is taken in inbox and done']['state'].keys())
        self.assertTrue(any(k.endswith(f'{stem}-2.md') for k in out['the name is taken once']['state']))
        self.assertIn('filed with: ', out['builds: the same skills and another']['state'][f'pstack/feedback/inbox/{stem}.md'].replace('repo-<H>', ''))
        self.assertEqual(out['a report with no gap']['rc'], 1)
        for name in ('a master that is a string', 'a master that is an array'):
            self.assertEqual(out[name]['rc'], 0, name)
            self.assertIn('Cannot index', out[name]['stderr'], name)
            self.assertIn('master:  ()', out[name]['state'][f'pstack/feedback/inbox/{stem}.md'], name)
        stopped = out['a skill_builds entry that is a number stops with jq, leaving the lock']
        self.assertEqual(stopped['rc'], 5)
        self.assertIn('pstack/feedback/.lock/', stopped['state'])
        self.assertEqual(out['a dangling link at the name being allocated']['rc'], 0)
        self.assertIn('filed: ', out['a master that is a string']['stdout'])

    def test_feedback_inbox_and_close_match_the_bash_at_the_phase_4_base(self):
        inbox = lambda *names: {f'pstack/feedback/inbox/{n}.md': self.feedback_inbox_file(n) for n in names}
        with_done = lambda files: {**files, 'pstack/feedback/done/.keep': ''}
        old = self.feedback_inbox_file('20261001-repo-b', filed='2026-09-01T10:00:00Z', gaps=1)
        no_filed = self.feedback_inbox_file('20261002-repo-c', filed='', build='')
        two = {**inbox('20261003-repo-a'), 'pstack/feedback/inbox/20261001-repo-b.md': old, 'pstack/feedback/inbox/20261002-repo-c.md': no_filed}
        done_copy = {**inbox('20261003-repo-a'), 'pstack/feedback/done/20261003-repo-a.md': self.feedback_inbox_file('20261003-repo-a', status='closed') + '\n## Closed\n\nclosed: x\ncommit: none\nnote: n\n'}
        close = ['feedback', '--close', '20261003-repo-a', 'abc123', 'fixed in\ntwo lines']
        cases = {
            'an empty inbox': dict(args=['feedback', '--inbox']),
            'three reports of different ages': dict(args=['feedback', '--inbox'], statefiles=two, stateages={'pstack/feedback/inbox/20261002-repo-c.md': 7200 * 30}),
            'a report that is also in done is not listed': dict(args=['feedback', '--inbox'], statefiles=done_copy),
            'an inbox file with no gap heading ends --inbox with status 1': dict(args=['feedback', '--inbox'], statefiles={**two, 'pstack/feedback/inbox/20261004-repo-d.md': 'no gaps here\nid: d\n'}),
            'a dangling link in the inbox is skipped': dict(args=['feedback', '--inbox'], statefiles=inbox('20261003-repo-a'), statelinks={'pstack/feedback/inbox/20261009-repo-z.md': '/nonexistent/z'}),
            'a dangling link in done does not hide a report': dict(args=['feedback', '--inbox'], statefiles=inbox('20261003-repo-a'), statelinks={'pstack/feedback/done/20261003-repo-a.md': '/nonexistent/z'}),
            'a close whose rm fails keeps the lock': dict(args=close, statefiles=with_done(inbox('20261003-repo-a')), statemodes={'pstack/feedback/inbox': 0o555}),
            'a second close whose rm fails': dict(args=close, statefiles=done_copy, statemodes={'pstack/feedback/inbox': 0o555}),
            'closing a report': dict(args=close, statefiles=with_done(inbox('20261003-repo-a'))),
            'closing with no done directory': dict(args=close, statefiles=inbox('20261003-repo-a')),
            'closing with the .md suffix and none': dict(args=['feedback', '--close', '20261003-repo-a.md', 'none', 'n'], statefiles=with_done(inbox('20261003-repo-a'))),
            'closing twice': dict(args=close, statefiles=done_copy),
            'an unknown id': dict(args=close),
            'close with too few arguments': dict(args=['feedback', '--close', 'x', 'none']),
            'close with too many arguments': dict(args=close + ['x']),
            'a stale lock is taken over on close': dict(args=close, statefiles=with_done(inbox('20261003-repo-a')), statelocks={'pstack/feedback/.lock': 300}),
        }
        out = self.phase4_cases('fbinbox', cases, {
            'an empty inbox': (0, 'feedback inbox: empty'),
            'three reports of different ages': (0, '20261002-repo-c  runc  repoc          2  2d'),
            'a report that is also in done is not listed': (0, 'feedback inbox: empty'),
            'an inbox file with no gap heading ends --inbox with status 1': (1, ''),
            'a dangling link in the inbox is skipped': (0, '20261003-repo-a  runa  repoa  abc123  2  19d'),
            'a dangling link in done does not hide a report': (0, '20261003-repo-a  runa  repoa  abc123  2  19d'),
            'a close whose rm fails keeps the lock': (1, "/state/pstack/feedback/inbox/20261003-repo-a.md': Permission denied"),
            'a second close whose rm fails': (1, "/state/pstack/feedback/inbox/20261003-repo-a.md': Permission denied"),
            'closing a report': (0, 'closed: 20261003-repo-a (commit abc123)'),
            'closing with no done directory': (1, "/state/pstack/feedback/done/20261003-repo-a.md': No such file or directory"),
            'closing with the .md suffix and none': (0, 'closed: 20261003-repo-a (commit none)'),
            'closing twice': (0, 'note: n'),
            'an unknown id': (1, 'pair.sh: no feedback report 20261003-repo-a in'),
            'close with too few arguments': (1, 'pair.sh: usage: kitchen.sh feedback --close'),
            'close with too many arguments': (1, 'pair.sh: usage: kitchen.sh feedback --close'),
            'a stale lock is taken over on close': (0, 'closed: 20261003-repo-a (commit abc123)'),
        })
        self.assertIn('feedback inbox: empty', out['an empty inbox']['stdout'])
        rows = out['three reports of different ages']['stdout'].splitlines()
        self.assertEqual(len(rows), 3, rows)
        self.assertIn('closed: 20261003-repo-a (commit abc123)', out['closing a report']['stdout'])
        self.assertIn('already closed: 20261003-repo-a', out['closing twice']['stdout'])
        self.assertEqual(out['an unknown id']['rc'], 1)
        self.assertEqual((out['an inbox file with no gap heading ends --inbox with status 1']['rc'], out['an inbox file with no gap heading ends --inbox with status 1']['stdout']), (1, ''))
        self.assertIn('20261003-repo-a', out['a dangling link in the inbox is skipped']['stdout'])
        self.assertNotIn('20261009-repo-z', out['a dangling link in the inbox is skipped']['stdout'])
        self.assertIn('20261003-repo-a', out['a dangling link in done does not hide a report']['stdout'])
        self.assertEqual(out['a close whose rm fails keeps the lock']['rc'], 1)
        self.assertIn("rm: cannot remove", out['a close whose rm fails keeps the lock']['stderr'])
        self.assertIn('pstack/feedback/.lock/', out['a close whose rm fails keeps the lock']['state'])
        self.assertNotIn('pstack/feedback/.lock/', out['closing a report']['state'])
        self.assertIn('pstack/feedback/.lock/', out['closing with no done directory']['state'])
        self.assertEqual(out['a second close whose rm fails']['rc'], 1)
        self.assertNotIn('already closed', out['a second close whose rm fails']['stdout'])

    def test_a_held_feedback_lock_ends_the_command_as_the_bash_does_after_a_hundred_tries(self):
        """The one test that waits: the lock is fresh, so neither side takes it, and each gives up after 100 tries at 0.1 s (about ten seconds)."""
        out = self.phase4_cases('fblock', {'a held lock': dict(args=['feedback', '--close', 'x', 'none', 'n'], statefiles={'pstack/feedback/inbox/x.md': 'x\n'},
                                                                 statelocks={'pstack/feedback/.lock': 1})},
                                 {'a held lock': (1, 'the feedback inbox is locked')})
        self.assertRegex(out['a held lock']['stderr'], r'the feedback inbox is locked \(<T>/state/pstack/feedback/\.lock\)')

    def test_the_python_feedback_lock_is_the_bash_lock(self):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        from kitchen_core import feedback
        fb = self.path / 'lockfb'
        fb.mkdir()
        # a lock the bash holds (mkdir): Python waits on it, then takes it when it goes, with the tries and delay the bash has
        (fb / '.lock').mkdir()
        slept = []

        def release(delay):
            slept.append(delay)
            if len(slept) == 3:
                (fb / '.lock').rmdir()

        feedback.fb_lock(str(fb), sleep=release)
        self.assertEqual(slept, [0.1, 0.1, 0.1])
        self.assertTrue((fb / '.lock').is_dir())
        # the lock Python holds blocks the bash: its function, cut out of the base kitchen.sh, makes no progress
        base_text = (self.phase4_skills() / 'pstack-kitchen/scripts/kitchen.sh').read_text()
        lock_fn = re.search(r'(?m)^fb_lock\(\) \{.*?^\}$', base_text, re.S).group(0)
        blocked = subprocess.run(['timeout', '1', 'bash', '-c', f'die() {{ echo "$1" >&2; exit 1; }}\n{lock_fn}\nfb_lock {fb}; echo got'],
                                 capture_output=True, text=True, check=False)
        self.assertEqual((blocked.returncode, blocked.stdout), (124, ''))
        # an old lock (over a minute) is taken over by both rules: the same age test
        os.utime(fb / '.lock', (time.time() - 300, time.time() - 300))
        feedback.fb_lock(str(fb), tries=1, sleep=lambda delay: self.fail('slept on a stale lock'))
        # and gives up on the hundredth failure after ninety-nine waits
        fresh = []
        (fb / '.lock').rmdir() if (fb / '.lock').exists() else None
        (fb / '.lock').mkdir()
        with self.assertRaises(Exception) as caught:
            feedback.fb_lock(str(fb), sleep=fresh.append)
        self.assertEqual((len(fresh), caught.exception.message), (99, f'the feedback inbox is locked ({fb}/.lock)'))

    def test_maintainer_matches_the_bash_at_the_phase_4_base(self):
        live = dict(agents={'mt': 'idle claude'}, panes={'p7': 'mt'})
        registered = {'pstack/feedback/maintainer.json': self.MAINTAINER_JSON}
        cases = {
            'on with --pane': dict(args=['maintainer', 'on', '--pane', 'p7'], **live),
            'on with the pane of the shell': dict(args=['maintainer', 'on'], **live, env={'HERDR_PANE_ID': 'p7'}),
            'on again replaces the registration': dict(args=['maintainer', 'on', '--pane', 'p7'], **live, statefiles={'pstack/feedback/maintainer.json': self.MAINTAINER_JSON.replace('mt', 'old')}),
            'on without herdr': dict(args=['maintainer', 'on', '--pane', 'p7'], **live, noenv=True),
            'on with no pane': dict(args=['maintainer', 'on'], **live, env={'HERDR_PANE_ID': ''}),
            'on with an unknown option': dict(args=['maintainer', 'on', '--bogus']),
            'on for a pane with no agent': dict(args=['maintainer', 'on', '--pane', 'p9'], **live),
            'off': dict(args=['maintainer', 'off'], statefiles=registered),
            'off with none': dict(args=['maintainer', 'off']),
            'off whose rm fails': dict(args=['maintainer', 'off'], statefiles={'pstack/feedback/maintainer.json/held': 'x\n'}),
            'status with none': dict(args=['maintainer', 'status']),
            'status live': dict(args=['maintainer', 'status'], **live, statefiles=registered),
            'status gone': dict(args=['maintainer', 'status'], statefiles=registered),
            'status moved': dict(args=['maintainer', 'status'], agents={'mt': 'idle claude'}, panes={'p9': 'mt'}, statefiles=registered),
            'status blocked': dict(args=['maintainer', 'status'], agents={'mt': 'blocked claude'}, panes={'p7': 'mt'}, statefiles=registered),
            'status with another session': dict(args=['maintainer', 'status'], **live, statefiles={'pstack/feedback/maintainer.json': self.MAINTAINER_JSON.replace('null', '"other"')}),
            'status with a maintainer.json that is not JSON': dict(args=['maintainer', 'status'], **live, statefiles={'pstack/feedback/maintainer.json': 'not json\n'}),
            'status with a maintainer.json that is an array': dict(args=['maintainer', 'status'], **live, statefiles={'pstack/feedback/maintainer.json': '[1]\n'}),
            'status with a maintainer.json that is a string': dict(args=['maintainer', 'status'], **live, statefiles={'pstack/feedback/maintainer.json': '"x"\n'}),
            'status when herdr answers an agent of null': dict(args=['maintainer', 'status'], statefiles=registered, agent_get='{"result":{"agent":null}}\n'),
            'status when herdr answers text that is not JSON': dict(args=['maintainer', 'status'], statefiles=registered, agent_get='nope\n'),
            'on when herdr answers an agent of null': dict(args=['maintainer', 'on', '--pane', 'p7'], agent_get='{"result":{"agent":null}}\n'),
            'no arguments': dict(args=['maintainer']),
            'an unknown subcommand': dict(args=['maintainer', 'stop']),
        }
        out = self.phase4_cases('maint', cases, {
            'on with --pane': (0, 'maintainer: mt on p7'),
            'on with the pane of the shell': (0, 'maintainer: mt on p7'),
            'on again replaces the registration': (0, 'maintainer: mt on p7'),
            'on without herdr': (0, 'maintainer: mt on p7'),
            'on with no pane': (1, 'pair.sh: no pane: pass --pane ID'),
            'on with an unknown option': (1, 'pair.sh: unknown option --bogus'),
            'on for a pane with no agent': (1, 'pair.sh: herdr has no agent on pane p9'),
            'off': (0, 'maintainer: none'),
            'off with none': (0, 'maintainer: none'),
            'off whose rm fails': (1, "/state/pstack/feedback/maintainer.json': Is a directory"),
            'status with none': (0, 'maintainer: none'),
            'status live': (0, 'maintainer: mt on p7 since'),
            'status gone': (0, 'maintainer: mt on p7 since'),
            'status moved': (0, ': agent mt moved off pane p7'),
            'status blocked': (0, ': agent mt is blocked on a dialog'),
            'status with another session': (0, ': agent mt is a different session than the one registered'),
            'status with a maintainer.json that is not JSON': (0, 'maintainer:  on  since : agent  is gone'),
            'status with a maintainer.json that is an array': (0, 'maintainer:  on  since : agent  is gone'),
            'status with a maintainer.json that is a string': (0, 'maintainer:  on  since : agent  is gone'),
            'status when herdr answers an agent of null': (0, ': agent mt moved off pane p7'),
            'status when herdr answers text that is not JSON': (0, ': agent mt moved off pane p7'),
            'on when herdr answers an agent of null': (1, 'pair.sh: herdr reports no name for the agent on pane p7; name it first'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh maintainer on [--pane ID] | off | status'),
            'an unknown subcommand': (1, 'pair.sh: usage: kitchen.sh maintainer on [--pane ID] | off | status'),
        })
        self.assertIn('maintainer: mt on p7', out['on with --pane']['stdout'])
        self.assertIn('"session": null', out['on with --pane']['state']['pstack/feedback/maintainer.json'])
        self.assertIn('maintainer: mt on p7 since <TS>: live', out['status live']['stdout'].replace('2026-10-01T10:00:00Z', '<TS>'))
        self.assertEqual(out['on for a pane with no agent']['rc'], 1)
        for name in ('status with a maintainer.json that is not JSON', 'status with a maintainer.json that is an array', 'status with a maintainer.json that is a string'):
            self.assertEqual(out[name]['rc'], 0, name)
            self.assertIn('maintainer: ', out[name]['stdout'], name)
            self.assertIn('jq: ', out[name]['stderr'], name)
        self.assertIn('moved off pane p7', out['status when herdr answers an agent of null']['stdout'])
        self.assertIn('herdr reports no name for the agent on pane p7', out['on when herdr answers an agent of null']['stderr'])

    CLOSE_ROLES = {'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}, 'consultant': {'name': 'c1', 'pane_id': 'p2', 'kind': 'codex'},
                   'sidekick': {'name': 's1', 'pane_id': 'p1', 'kind': 'claude'}}

    def test_close_matches_the_bash_at_the_phase_4_base(self):
        live = dict(agents={'v1': 'idle claude', 'c1': 'idle codex', 's1': 'idle claude'}, panes={'p1': 's1', 'p2': 'c1', 'p3': 'v1'}, pair={**self.CLOSE_ROLES, 'slug': 'demo'})
        with_roles = lambda **roles: {**live, 'pair': {**live['pair'], **roles}}
        run = ['close', '{st}']
        cases = {
            'an idle run closes verifier, consultant and sidekick': dict(live, args=run),
            'an open verification refuses': dict(with_roles(verifying={'verdict': '{st}/verdicts/x.md', 'units': '001'}), args=run),
            'an open verification and --force': dict(with_roles(verifying={'verdict': '{st}/verdicts/x.md', 'units': '001'}), args=run + ['--force']),
            'a working sidekick refuses': dict(live, agents={**live['agents'], 's1': 'working claude'}, args=run),
            'a working sidekick and --force': dict(live, agents={**live['agents'], 's1': 'working claude'}, args=run + ['--force']),
            'a role with no pane is skipped': dict(with_roles(consultant={'name': 'c1', 'kind': 'codex'}), args=run),
            'a pane already gone': dict(live, agents={'s1': 'idle claude'}, args=run),
            'a role already closed is skipped': dict(with_roles(verifier={**self.CLOSE_ROLES['verifier'], 'closed': True}), args=run),
            'a consultant that is a string': dict(with_roles(consultant='c1'), args=run),
            'a verifier that is an array': dict(with_roles(verifier=[1]), args=run),
            'a sidekick that is false': dict(with_roles(sidekick=False), args=run),
            'a verifying that is a string': dict(with_roles(verifying='x'), args=run),
            'a Prime role with a session': dict(with_roles(verifier={**self.CLOSE_ROLES['verifier'], 'kind': 'prime-agent', 'session': {'kind': 'path', 'value': '/t/v.jsonl'}}),
                                                  args=run + ['--force']),
            'a role that does not exit keeps its pane': dict(live, args=run, noexit=True, clock=True),
            '--force is only read as the second argument': dict(live, args=['close', '--force', '{st}']),
            'a third argument is ignored': dict(live, args=run + ['--force', 'x']),
            'no arguments': dict(args=['close']),
            'no pair.json': dict(args=run, nopair=True),
            'outside herdr': dict(live, args=run, noenv=True),
        }
        out = self.phase4_cases('close', cases, {
            'an idle run closes verifier, consultant and sidekick': (0, 'closed verifier v1 (p3)'),
            'an open verification refuses': (5, 'pair.sh: a verification is open (001); finish it (kitchen.sh verify'),
            'an open verification and --force': (0, 'closed verifier v1 (p3)'),
            'a working sidekick refuses': (5, 'pair.sh: the sidekick is working; kitchen.sh stop'),
            'a working sidekick and --force': (0, 'closed verifier v1 (p3)'),
            'a role with no pane is skipped': (0, 'closed sidekick s1 (p1)'),
            'a pane already gone': (0, 'closed verifier v1 (p3)'),
            'a role already closed is skipped': (0, 'closed consultant c1 (p2)'),
            'a consultant that is a string': (0, 'closed verifier v1 (p3)'),
            'a verifier that is an array': (0, 'closed consultant c1 (p2)'),
            'a sidekick that is false': (0, 'closed verifier v1 (p3)'),
            'a verifying that is a string': (0, 'closed verifier v1 (p3)'),
            'a Prime role with a session': (5, 'closed consultant c1 (p2)'),
            'a role that does not exit keeps its pane': (5, 'verifier v1 did not exit; its pane p3 stays open'),
            '--force is only read as the second argument': (1, 'pair.sh: no pair.json in --force; run: pair.sh init'),
            'a third argument is ignored': (0, 'closed verifier v1 (p3)'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh close'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'outside herdr': (0, 'closed verifier v1 (p3)'),
        })
        idle = out['an idle run closes verifier, consultant and sidekick']
        self.assertEqual(idle['rc'], 0, idle['stderr'])
        self.assertEqual(idle['stdout'].splitlines(), ['closed verifier v1 (p3)', 'closed consultant c1 (p2)', 'closed sidekick s1 (p1)'])
        self.assertEqual(out['an open verification refuses']['rc'], 5)
        self.assertEqual(out['a role that does not exit keeps its pane']['rc'], 5)
        self.assertIn('verifier v1 did not exit; its pane p3 stays open', out['a role that does not exit keeps its pane']['stderr'])
        self.assertIn('a verification is open (001)', out['an open verification refuses']['stderr'])
        self.assertEqual(out['a working sidekick refuses']['rc'], 5)
        self.assertEqual(out['an open verification and --force']['rc'], 0)
        self.assertIn('Cannot index string with string', out['a consultant that is a string']['stderr'])
        self.assertNotIn('closed consultant', out['a consultant that is a string']['stdout'])

    HANDOFF_FILLED = '# Handoff\n\n## Goal\n\nfinish the port\n\n## Next\n\nunit 025\n'

    def test_a_flag_with_no_value_dies_with_the_unbound_variable_message_without_a_line_number(self):
        """Accepted (PARITY.md): `maintainer on --pane` and `rotate-master --model` with no value. The bash's set -u prints the shell's own
        `kitchen.sh: line N: $2: unbound variable` (N is a line of the old kitchen.sh); the port prints `kitchen.sh: $2: unbound variable`. Both exit 1."""
        for args in (['rotate-master', '{st}', '--model'], ['maintainer', 'on', '--pane']):
            got, want = self.phase4_ab('unbound' + args[0], dict(args=args, pair={'slug': 'demo', 'master': {'name': 'm1', 'pane_id': 'p0'}},
                                                               files={'session-handoff.md': self.HANDOFF_FILLED}, panes={'p0': 'm1'}))
            self.assertEqual((want['rc'], got['rc']), (1, 1), args)
            self.assertRegex(want['stderr'], r'kitchen\.sh: line [0-9]+: \$2: unbound variable\n$')
            self.assertEqual(got['stderr'], 'pair.sh: $2: unbound variable\n')

    def test_rotate_master_matches_the_bash_at_the_phase_4_base(self):
        master = {'name': 'm1', 'pane_id': 'p0', 'generation': 1, 'permission_mode': 'auto'}
        world = lambda **extra: {**dict(args=['rotate-master', '{st}'], pair={'slug': 'demo', 'master': master}, panes={'p0': 'm1'},
                                        files={'session-handoff.md': self.HANDOFF_FILLED}), **extra}
        with_master = lambda **changes: world(pair={'slug': 'demo', 'master': {**master, **changes}})
        ps_found = 'SELF\t200\tbash\tbash kitchen.sh\n200\t300\tclaude\tclaude --model opus-x --resume abc\n300\t1\tinit\tinit\n'
        ps_node = 'SELF\t200\tbash\tbash kitchen.sh\n200\t300\tnode\tnode /x/claude/cli.js --model=sonnet-y\n300\t1\tinit\tinit\n'
        ps_none = 'SELF\t200\tbash\tbash kitchen.sh\n200\t1\tzsh\tzsh\n'
        cases = {
            'a filled handoff': world(),
            'a handoff with a placeholder': world(files={'session-handoff.md': self.HANDOFF_FILLED.replace('unit 025', '{{{{what next}}}}')}),
            'an empty Goal section': world(files={'session-handoff.md': '# H\n\n## Goal\n\n\n## Next\n\nx\n'}),
            'an empty Next section': world(files={'session-handoff.md': '# H\n\n## Goal\n\nx\n\n## Next\n\n'}),
            'no handoff': world(files={}),
            '--model': dict(world(), args=['rotate-master', '{st}', '--model', 'opus-z']),
            'an unknown option': dict(world(), args=['rotate-master', '{st}', '--bogus']),
            'no arguments': dict(args=['rotate-master']),
            'remote control off': dict(world(), env={'KITCHEN_MASTER_REMOTE_CONTROL': '0'}),
            'the model found in the process tree': dict(world(), pstable=ps_found),
            'the model found behind node': dict(world(), pstable=ps_node),
            'no claude in the process tree': dict(world(), pstable=ps_none),
            'the next master does not start': dict(world(), fakefiles={'start-fail': ''}),
            'the next master asks for folder trust': dict(world(), fakefiles={'start-fail': '', 'trust-dialog': ''}),
            'a hand-over already open': with_master(previous={'name': 'old', 'pane_id': 'p9'}),
            'a master that is not live': dict(world(), master=None),
            'a master that is not Claude': dict(world(), agents={'m1': 'idle codex'}),
            'an unfinished rotation keeps its generation': dict(with_master(sessions=[{'generation': 4, 'session': 'sess-1', 'handoff': '/h', 'at': 't'}], generation=5),
                                                                 fakefiles={'session-ids/m1': 'sess-1 /x'}),
            'sessions are replaced by the same session': dict(with_master(sessions=[{'generation': 1, 'session': 'sess-1', 'handoff': '/h', 'at': 't'},
                                                                                   {'generation': 2, 'session': 'sess-2', 'handoff': '/h', 'at': 't'}]),
                                                               fakefiles={'session-ids/m1': 'sess-2 /x'}),
            'a master that is a string ends with the jq error': dict(world(), pair={'slug': 'demo', 'master': 'm1'}),
            'a master with sessions that is not a list': dict(with_master(sessions='x'), fakefiles={'session-ids/m1': 'sess-1 /x'}),
            'a master with sessions that is 0': dict(with_master(sessions=0), fakefiles={'session-ids/m1': 'sess-1 /x'}),
            'a master with sessions that is an empty string': dict(with_master(sessions=''), fakefiles={'session-ids/m1': 'sess-1 /x'}),
            'a master with sessions that is false': dict(with_master(sessions=False), fakefiles={'session-ids/m1': 'sess-1 /x'}),
            'a master with sessions that is null': dict(with_master(sessions=None), fakefiles={'session-ids/m1': 'sess-1 /x'}),
            'no pair.json': dict(world(), nopair=True),
            'outside herdr': dict(world(), noenv=True),
        }
        out = self.phase4_cases('rotm', cases, {
            'a filled handoff': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
            'a handoff with a placeholder': (1, '/st/session-handoff.md still has unfilled placeholders'),
            'an empty Goal section': (5, '/st/session-handoff.md: the Goal section is empty'),
            'an empty Next section': (5, '/st/session-handoff.md: the Next section is empty'),
            'no handoff': (5, 'pair.sh: no handoff; write one first: kitchen.sh new-handoff'),
            '--model': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
            'an unknown option': (1, 'pair.sh: unknown option --bogus'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh rotate-master'),
            'remote control off': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
            'the model found in the process tree': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
            'the model found behind node': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
            'no claude in the process tree': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
            'the next master does not start': (2, 'pair.sh: the next master did not start in p1; you stay the master (generation 1), so carry'),
            'the next master asks for folder trust': (5, 'asks whether to trust'),
            'a hand-over already open': (5, 'pair.sh: a hand-over is open: m1 has not taken over yet; it runs kitchen.sh take-master'),
            'a master that is not live': (5, "pair.sh: master m1 is not live in p0; run this from the master's own session"),
            'a master that is not Claude': (5, 'pair.sh: rotate-master restarts a Claude master; m1 is codex'),
            'an unfinished rotation keeps its generation': (0, '/st/sessions/master-4-handoff.md (generation 4, session sess-1)'),
            'sessions are replaced by the same session': (0, '/st/sessions/master-2-handoff.md (generation 2, session sess-2)'),
            'a master that is a string ends with the jq error': (5, '/st/pair.json:15): Cannot index string with string ("name")'),
            'a master with sessions that is not a list': (5, 'Cannot iterate over string ("x")'),
            'a master with sessions that is 0': (5, 'Cannot iterate over number (0)'),
            'a master with sessions that is an empty string': (5, 'Cannot iterate over string ("")'),
            'a master with sessions that is false': (0, '/st/sessions/master-1-handoff.md (generation 1, session sess-1)'),
            'a master with sessions that is null': (0, '/st/sessions/master-1-handoff.md (generation 1, session sess-1)'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'outside herdr': (0, '/st/sessions/master-1-handoff.md (generation 1, session unknown)'),
        })
        ok = out['a filled handoff']
        self.assertEqual(ok['rc'], 0, ok['stderr'])
        self.assertIn('next master: demo-master-g2 (generation 2) in ', ok['stdout'])
        self.assertIn('--permission-mode auto', ok['calls'])
        self.assertIn('--remote-control demo-master-g2', ok['calls'])
        self.assertIn('--model opus-x', out['the model found in the process tree']['calls'])
        self.assertIn('--model sonnet-y', out['the model found behind node']['calls'])
        self.assertNotIn('--model', out['no claude in the process tree']['calls'])
        self.assertNotIn('--remote-control', out['remote control off']['calls'])
        self.assertEqual(out['the next master does not start']['rc'], 2)
        self.assertEqual(out['a handoff with a placeholder']['rc'], 1)
        self.assertEqual(out['a master that is a string ends with the jq error']['rc'], 5)
        self.assertIn('(generation 4, session sess-1)', out['an unfinished rotation keeps its generation']['stdout'])
        self.assertIn('master-4-handoff.md', out['an unfinished rotation keeps its generation']['stdout'])

    def test_take_master_matches_the_bash_at_the_phase_4_base(self):
        prev = {'name': 'm1', 'pane_id': 'p0', 'session': 's'}
        master = {'name': 'demo-master-g2', 'pane_id': 'p5', 'generation': 2, 'previous': prev}
        world = lambda **extra: {**dict(args=['take-master', '{st}'], pair={'slug': 'demo', 'master': master}, panes={'p0': 'm1', 'p5': 'demo-master-g2'},
                                        agents={'m1': 'idle claude', 'demo-master-g2': 'idle claude'}, env={'HERDR_PANE_ID': 'p5', 'KITCHEN_ROTATE_POLL_S': '0'}), **extra}
        engine = dict(review_engine=True, files={'briefs/001-x.md': '# Brief\n\ncommit: yes\nplaybook: bug-fix\n', 'reviews/001-x.md': '# R\n\nverdict: accept\n'})
        cases = {
            'a previous master that is live': world(),
            'a previous master that is gone but whose pane is there': world(agents={'demo-master-g2': 'idle claude'}, master=None),
            'a previous pane that is already gone': world(panes={'p5': 'demo-master-g2'}, agents={'demo-master-g2': 'idle claude'}),
            'a previous pane that holds another agent': world(panes={'p0': 'other', 'p5': 'demo-master-g2'}, agents={'other': 'idle claude', 'demo-master-g2': 'idle claude'}),
            'a master already named for the run': world(pair={'slug': 'demo', 'master': {**master, 'name': 'demo-master'}}, agents={'m1': 'idle claude', 'demo-master': 'idle claude'},
                                                        panes={'p0': 'm1', 'p5': 'demo-master'}),
            'no hand-over open': world(pair={'slug': 'demo', 'master': {'name': 'demo-master', 'pane_id': 'p5', 'generation': 2}}),
            'run from another pane': world(env={'HERDR_PANE_ID': 'p9', 'KITCHEN_ROTATE_POLL_S': '0'}),
            'run from no pane': world(env={'HERDR_PANE_ID': '', 'KITCHEN_ROTATE_POLL_S': '0'}),
            'a previous master still working when the wait is over': world(agents={'m1': 'working claude', 'demo-master-g2': 'idle claude'}, env={
                'HERDR_PANE_ID': 'p5', 'KITCHEN_ROTATE_POLL_S': '0', 'KITCHEN_TAKEOVER_WAIT_S': '0'}),
            'a previous master that settles during the wait': world(agents={'m1': 'working claude', 'demo-master-g2': 'idle claude'},
                                                                    # the first `agent get` still says working and arms the hook for the next one, which flips m1
                                                                    once={'get': 'printf \'#!/bin/sh\\nprintf "idle claude\\\\n" >"$FAKE/agents/m1"\\n\' >"$FAKE/once/get"\nchmod +x "$FAKE/once/get"\n'}),
            'a master that is a string': world(pair={'slug': 'demo', 'master': 'x'}),
            'unreviewed units are listed': world(**engine),
            'a review that cites the engine is not listed': world(review_engine=True, files={**engine['files'], 'reviews/001-x.md': '# R\n\nverdict: accept\nengine: joo /tmp/run.json\n'}),
            'a skipped engine over an empty range': world(review_engine=True, pair={'slug': 'demo', 'master': master, 'heads': {'001-x': '{c3}'}},
                                                          files={**engine['files'], 'reviews/001-x.md': '# R\n\nverdict: accept\nengine: skipped (deletion-only, proof: none)\n',
                                                                 'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
            'a skipped engine over a range that adds lines': world(review_engine=True, pair={'slug': 'demo', 'master': master, 'heads': {'001-x': '{c1}'}},
                                                                   files={**engine['files'], 'reviews/001-x.md': '# R\n\nverdict: accept\nengine: skipped (deletion-only, proof: none)\n',
                                                                          'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}),
            'a landing brief is not listed': world(review_engine=True, files={**engine['files'], 'briefs/001-x.md': '# B\n\ncommit: yes\nplaybook: shipping\n'}),
            'a review not accepted is not listed': world(review_engine=True, files={**engine['files'], 'reviews/001-x.md': '# R\n\nverdict: reject\n'}),
            'two arguments': world(args=['take-master', '{st}', 'x']),
            'no arguments': dict(args=['take-master']),
            'no pair.json': world(nopair=True),
            'outside herdr': world(noenv=True),
        }
        out = self.phase4_cases('takem', cases, {
            'a previous master that is live': (0, 'closed the old master m1 (p0)'),
            'a previous master that is gone but whose pane is there': (0, 'closed the old master m1 (p0)'),
            'a previous pane that is already gone': (0, 'the old master pane p0 is already gone'),
            'a previous pane that holds another agent': (0, 'left pane p0 open: it now holds other, not the old master m1'),
            'a master already named for the run': (0, 'closed the old master m1 (p0)'),
            'no hand-over open': (0, 'no hand-over open; demo-master is the master in p5'),
            'run from another pane': (5, "pair.sh: take-master runs from the new master's pane p5, not p9"),
            'run from no pane': (5, "pair.sh: take-master runs from the new master's pane p5, not an unknown pane"),
            'a previous master still working when the wait is over': (0, 'closed the old master m1 (p0)'),
            'a previous master that settles during the wait': (0, 'closed the old master m1 (p0)'),
            'a master that is a string': (5, '/st/pair.json:15): Cannot index string with string ("name")'),
            'unreviewed units are listed': (0, 'warning: unreviewed: unit 001 cites no joo run'),
            'a review that cites the engine is not listed': (0, 'master: demo-master in p5 (generation 2)'),
            'a skipped engine over an empty range': (0, 'master: demo-master in p5 (generation 2)'),
            'a skipped engine over a range that adds lines': (0, 'skipped is only for a deletion-only range'),
            'a landing brief is not listed': (0, 'master: demo-master in p5 (generation 2)'),
            'a review not accepted is not listed': (0, 'master: demo-master in p5 (generation 2)'),
            'two arguments': (1, 'pair.sh: usage: kitchen.sh take-master'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh take-master'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'outside herdr': (0, 'closed the old master m1 (p0)'),
        })
        live = out['a previous master that is live']
        self.assertEqual(live['rc'], 0, live['stderr'])
        self.assertIn('closed the old master m1 (p0)', live['stdout'])
        self.assertIn('master: demo-master in p5 (generation 2)', live['stdout'])
        self.assertIn('agent rename p5 demo-master', live['calls'])
        calls = lambda name: out[name]['calls'].splitlines()
        self.assertEqual(calls('a previous master that settles during the wait').count('agent get m1'), 2)
        self.assertEqual(calls('a previous master that is live').count('agent get m1'), 1)
        self.assertIn('agent read m1 --source visible --lines 40', calls('a previous master that is live'))
        self.assertNotIn('agent read m1 --source visible --lines 40', calls('a previous master that is gone but whose pane is there'))
        self.assertIn('already gone', out['a previous pane that is already gone']['stdout'])
        self.assertIn('left pane p0 open: it now holds other, not the old master m1', out['a previous pane that holds another agent']['stdout'])
        self.assertIn('no hand-over open', out['no hand-over open']['stdout'])
        self.assertEqual(out['run from another pane']['rc'], 5)
        self.assertIn('warning: unreviewed: unit 001 cites no joo run', out['unreviewed units are listed']['stdout'])
        self.assertNotIn('warning: unreviewed', out['a review that cites the engine is not listed']['stdout'])
        self.assertNotIn('warning: unreviewed', out['a skipped engine over an empty range']['stdout'])
        self.assertIn('skipped is only for a deletion-only range', out['a skipped engine over a range that adds lines']['stdout'])
        self.assertNotIn('warning: unreviewed', out['a landing brief is not listed']['stdout'])
        self.assertEqual(out['a master that is a string']['rc'], 5)

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
            'a sidekick that is a string (claude)': run('--kind', 'claude', pair={**side(), 'sidekick': 'x'}),
            'a sidekick that is a string (pi)': run('--kind', 'pi', pair={**side(), 'sidekick': 'x'}),
            'a sidekick that is an array (claude)': run('--kind', 'claude', pair={**side(), 'sidekick': [1]}),
            'a sidekick that is an array (pi)': run('--kind', 'pi', pair={**side(), 'sidekick': [1]}),
            'a sidekick that is false (claude)': run('--kind', 'claude', pair={**side(), 'sidekick': False}),
            'a sidekick that is false (pi)': run('--kind', 'pi', pair={**side(), 'sidekick': False}),
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

    def test_phase4_skills_extracts_the_kitchen_oracle(self):
        script = self.phase4_skills() / 'pstack-kitchen/scripts/kitchen.sh'
        self.assertTrue(script.is_file())
        self.assertIn('claim_close()', script.read_text())

    def test_a_prime_spawn_over_a_sidekick_that_is_not_an_object_matches_the_bash_at_the_phase_3_base(self):
        """D27, Prime. `spawn --kind prime-agent` over a `.sidekick` that is a string, an array or false reaches spawn.py's Prime jq program and
        ends as the base does: rc 5, `Prime did not report its session`, the same files, events, herdr calls and stderr, including the jq line
        `record_prime_session` prints when it reads the role's name through `spawn.field` (the call is `agent get` with no name). The fake
        prime-agent refuses an empty name without a Traceback, so neither side has one."""
        plain = self.spawn_command_cases()['a plain spawn']
        for n, (label, value) in enumerate((('a string', 'x'), ('an array', [1]), ('false', False))):
            spec = {**plain, 'pair': {**plain['pair'], 'sidekick': value}, 'args': ['spawn', '{st}', '--kind', 'prime-agent']}
            got, want = self.phase3_ab(f'd27p{n}', 'pstack-pair', spec)
            self.assertEqual((got['rc'], want['rc']), (5, 5), label)
            for key in ('stdout', 'stderr', 'files', 'events', 'calls'):
                self.assertEqual(got[key], want[key], (label, key))
            self.assertNotIn('Traceback', got['stderr'], label)

    def test_an_unparseable_pair_json_ends_next_spawn_and_dispatch_with_python_s_message_where_the_bash_had_jq_s(self):
        """Unit 012 (vi), pinned and accepted. With a pair.json that is not JSON the bash's first `field` read fails in jq: its parse error (twice,
        for the two reads before the command ends), exit status 5, and from `json_update` an empty pair.json.tmp. The port reads the document
        once and ends with Python's message and status 1, writing nothing. stdout, events and the document itself are equal."""
        plain = self.spawn_command_cases()['a plain spawn']
        cases = (('spawn', self.phase3_skills, {**plain, 'nopair': True, 'files': {'pair.json': 'not json\n'}, 'args': ['spawn', '{st}', '--kind', 'claude']}, True),
                 ('next', self.base_skills, dict(args=['next', '{st}'], nopair=True, files={'pair.json': 'not json\n', 'queue': '{st}/briefs/002-y.md\n'}), False),
                 ('dispatch', self.base_skills, dict(args=['dispatch', '{st}', '{st}/briefs/002-y.md', '--send-only'], nopair=True, agents={'s1': 'idle claude'},
                                                      files={'pair.json': 'not json\n'}), False))
        jq_error = 'jq: parse error: Invalid numeric literal at line 1, column 4\n'
        for n, (name, tree, spec, tmp) in enumerate(cases):
            want = self.ab_run(f'unp{n}b', tree(), 'pstack-pair', dict(spec))
            got = self.ab_run(f'unp{n}p', root / 'skills', 'pstack-pair', dict(spec))
            self.assertEqual((want['rc'], want['stderr']), (5, jq_error * 2), name)
            self.assertEqual((got['rc'], got['stderr']), (1, 'pair.sh: Expecting value: line 1 column 1 (char 0)\n'), name)
            self.assertEqual((got['stdout'], got['events']), (want['stdout'], want['events']), name)
            expected_files = dict(want['files'])
            if tmp:
                self.assertEqual(expected_files.pop('pair.json.tmp'), '', name)
            self.assertEqual(got['files'], expected_files, name)
            self.assertNotIn('Traceback', got['stderr'], name)

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
                    mask = lambda text: re.sub(r'(?m)^pair\.sh: \$2: unbound', '<core>: line N: $2: unbound', re.sub(r'\S*(pair-core|kitchen)\.sh: line [0-9]+', '<core>: line N', text))
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
            'a sidekick that is a string and no --sidekick': run(pair=pair(sidekick='x')),
            'a sidekick that is an array and no --sidekick': run(pair=pair(sidekick=[1])),
            'a consultant that is a string and no --consultant': run('--sidekick', 'codex', pair=pair(consultant='x')),
            'a consultant that is an array and no --consultant': run('--sidekick', 'codex', pair=pair(consultant=[1])),
            'a sidekick that is a string with both kinds given': run('--sidekick', 'codex', '--consultant', 'devin', pair=pair(sidekick='x')),
            'a sidekick that is an array with both kinds given': run('--sidekick', 'codex', '--consultant', 'devin', pair=pair(sidekick=[1])),
            'a sidekick that is false with both kinds given': run('--sidekick', 'codex', '--consultant', 'devin', pair=pair(sidekick=False)),
            'a consultant that is an array with both kinds given': run('--sidekick', 'codex', '--consultant', 'devin', pair=pair(consultant=[1])),
            'a consultant that is a string with both kinds given': run('--sidekick', 'codex', '--consultant', 'devin', pair=pair(consultant='x')),
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

    def test_exit_command_matches_the_bash_at_the_phase_3_base(self):
        """04d2c13's exit_command against exits.exit_command (the bash shim went when the kitchen's close left bash: unit 026)."""
        kinds = ('claude', 'codex', 'pi', 'devin', 'muse', 'prime-agent', 'grok', '', 'PI', 'pi ')
        bash = ''.join(f'printf "[%s]\\n" "$(exit_command {shlex.quote(k)})"\n' for k in kinds)
        py = 'from pstack_core import exits\n' + ''.join(f'print("[%s]" % exits.exit_command({k!r}))\n' for k in kinds)
        out = self.prime_both('exit-command', bash, py)
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
            'a role entry that is a string': dict(pair={'sidekick': 'x'}),
            'a role entry that is an array': dict(pair={'sidekick': [1]}),
        }

    def test_stop_prime_role_matches_the_bash_at_the_phase_3_base(self):
        """stop_prime_role, which its callers test with `||`: 04d2c13's function against exits.entry_stop_prime_role (the bash shim went with its last caller), over every client status and
        every way the cleanup stops short. The deadline cases run on a clock that jumps 100 s per `date +%s` and a sleep that returns at once (the
        port's deadline and poll are 0)."""
        bash = 'stop_prime_role "$store" sidekick || echo "returned rc=$?"; echo "after"'
        py = 'import sys\nfrom pstack_core import exits\nrc = exits.entry_stop_prime_role(None, [store, "sidekick"])\nif 100 <= rc <= 127:\n    raise SystemExit(rc - 100)\nif rc:\n    print("returned rc=%d" % rc)\nprint("after")'
        out = {name: self.prime_both('spr-' + re.sub(r'\W+', '-', name), bash, py, **spec) for name, spec in self.stop_prime_cases().items()}
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

    def test_a_prime_session_that_is_not_an_object_gets_what_the_bash_gave_it(self):
        """D26. `prime_active_session` and `stop_prime_role` over a role whose recorded `.session` is a string, an array, a number, true, false,
        an empty string or an object without a kind, against 04d2c13's functions: exit code, stdout, stderr, pair.json, prime.log (the daemon
        calls) and the herdr calls all equal (`prime_both` compares the whole tuple). A session jq cannot take as a `--argjson` or index prints
        jq's own error on both sides; the list of sessions is empty in the last cases, where jq has nothing to index and says nothing."""
        bash = 'prime_active_session "$store" sidekick; rc=$?; printf "rc=%s id=[%s]\\n" "$rc" "${prime_active_id-unset}"'
        py = 'id = prime.prime_active_session(store, "sidekick")\nprint(f"rc={int(id is None)} id=[{\'unset\' if id is None else id}]")'
        stop = 'stop_prime_role "$store" sidekick || echo "returned rc=$?"; echo "after"'
        stop_py = 'import sys\nfrom pstack_core import exits\nrc = exits.entry_stop_prime_role(None, [store, "sidekick"])\nif 100 <= rc <= 127:\n    raise SystemExit(rc - 100)\nif rc:\n    print("returned rc=%d" % rc)\nprint("after")'
        errors = {'x': 'invalid JSON text passed to --argjson', '["x"]': 'Cannot index array with string ("kind")', '3': 'Cannot index number with string ("kind")',
                  'true': 'Cannot index boolean with string ("kind")', '': 'no recorded Prime session for sidekick'}
        cases = (('a string', 'x', errors['x']), ('an array', ['x'], errors['["x"]']), ('a number', 3, errors['3']), ('true', True, errors['true']),
                 ('an empty string', '', errors['']), ('false', False, 'no recorded Prime session for sidekick'), ('an object without kind', {}, ''),
                 ('zero', 0, errors['3']), ('0.0', 0.0, errors['3']))
        for label, value, needle in cases:
            for what, snippet, python, shim in (('prime_active_session', bash, py, False), ('stop_prime_role', stop, stop_py, False)):
                name = f'd26-{what}-{label}'.replace(' ', '-')
                result = self.prime_both(name, snippet, python, shim=shim, agents={}, sidekick={'session': value})
                self.assertIn(needle, result[2], (what, label))
                self.assertNotIn('Traceback', result[2], (what, label))
        # the daemon's list fails: the bash still runs jq, so an invalid --argjson is said and a session jq could index is not (no input to index)
        for label, value, needle in (('a string', 'x', errors['x']), ('an array', ['x'], ''), ('zero', 0, '')):
            for what, snippet, python, shim in (('prime_active_session', bash, py, False), ('stop_prime_role', stop, stop_py, False)):
                result = self.prime_both(f'd26-listfail-{what}-{label}'.replace(' ', '-'), snippet, python, shim=shim, agents={}, sidekick={'session': value},
                                         fake={'prime-list-fail': ''})
                self.assertIn(needle, result[2], (what, label))
        # nothing live to index: jq takes the odd session without complaint and finds no match
        for what, snippet, python, shim in (('prime_active_session', bash, py, False), ('stop_prime_role', stop, stop_py, False)):
            result = self.prime_both(f'd26-empty-{what}', snippet, python, shim=shim, agents={}, sessions=[], sidekick={'session': ['x']})
            self.assertNotIn('Cannot index', result[2], what)

    def test_the_prime_smoke_script_sources_pair_core_and_its_names_resolve(self):
        """tests/prime_herdr_smoke.py needs a real Prime, so only what can be proved here is: it sources pair-core.sh (which sources prime-agent.sh)
        with no stubs of the helpers that file defines, and in that shell `pycore` is a function and the core has the `__stop_prime_role` entry the script calls."""
        text = (root / 'tests/prime_herdr_smoke.py').read_text()
        self.assertIn('skills/pstack-pair/scripts/pair-core.sh', text)
        self.assertNotRegex(text, r'(?m)^(field|json_update|agent_status)\(\) \{')
        script = re.search(r'script = """(.*?)"""', text, re.S).group(1)
        core = root / 'skills/pstack-pair/scripts/pair-core.sh'
        r = subprocess.run(['bash', '-c', script.split('pycore __stop_prime_role "$1"')[0] + 'type -t pycore', 'smoke', 'unused', str(core)],
                           capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, 'function\n'), r.stderr)
        self.assertIn('pycore __stop_prime_role "$1" sidekick', text)

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

    def test_close_role_matches_the_bash_at_the_phase_3_base_with_the_shims(self):
        """close_role: 04d2c13's pair-core function against kitchen_core.master's, which replaced it (unit 024; kitchen.sh `close` was its only caller)."""
        path_session = {'kind': 'path', 'value': '/t/one.jsonl'}
        bash = 'close_role "$store" sidekick || echo "returned rc=$?"; echo "after"'
        py = (f'sys.path.insert(0, {str(root / "skills/pstack-kitchen/scripts")!r})\nimport variant\nvariant.load_kitchen_core()\n'
              'from kitchen_core import master\nif not master.close_role(store, "sidekick"):\n    print("returned rc=1")\nprint("after")')
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
        out = {name: self.prime_both('cr-' + re.sub(r'\W+', '-', name), bash, py, **spec) for name, spec in cases.items()}
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
            'a master that is a string from a background subshell': {**run({**plain, 'pair': {**plain['pair'], 'master': 'x'}}), 'background': True},
            'a sidekick that is a string': run({**plain, 'pair': {**plain['pair'], 'sidekick': 'x'}}),
            'a sidekick that is an array': run({**plain, 'pair': {**plain['pair'], 'sidekick': [1]}}),
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
            'a sidekick that is a string': run({**plain, 'pair': {**plain['pair'], 'sidekick': 'x'}}),
            'a sidekick that is an array': run({**plain, 'pair': {**plain['pair'], 'sidekick': [1]}}),
            'a dispatch that is a string': run({**plain, 'pair': {**plain['pair'], 'dispatch': 'x'}}),
        }

    ROTATE_EXPECT = {
        # a jq that refuses the start's write ends a direct rotate (errexit on); inside kitchen.sh's $( ) it does not (the kitchen A/B below)
        'a master that is a string from a background subshell': (5, 'Cannot index string with string ("permission_mode")'),
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
        base = dict(due, args=['dispatch', '{st}', '{st}/briefs/003-z.md', '--timeout', '40000'], files={'briefs/003-z.md': brief},
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
                capture['before'] = ({str(f.relative_to(st)): norm_now(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name not in ('events.tsv', 'wait.pid')},
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
        files = {str(f.relative_to(st)): norm(f.read_text(errors='replace')) for f in sorted(st.rglob('*')) if f.is_file() and f.name not in ('events.tsv', 'wait.pid')}
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
            self.assertEqual(replies.queued_brief(str(st)), self.bash_value(f'[ -f {st}/queue ] && cat {st}/queue || true')[1], queue)  # the body of the deleted bash queued_brief
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

    def gate_world(self, name, playbook='feature', plan='{plan}', plan_text='# Plan\n\nscale: small\n', review=None, advice=None, extra_review=None, relative=False,
                   advice_files=None):
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
        for fname, text in (advice_files or {}).items():
            (top / 'advice' / fname).write_text(text)
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

    STOP_ADVICE_CASES = (
        ('a stop advice with kind: stop', {'001-stop.md': 'kind: stop\nstatus: partial\n'}, False),
        ('a stop advice with no kind: line', {'001-stop.md': 'status: partial\n'}, False),
        ('a stop advice named for a slug', {'001-x-stop.md': 'status: partial\n'}, False),
        ('an advice of another kind named stop', {'001-stop.md': 'kind: design\nstatus: answered\n'}, True),
        ('a stop beside a real advice', {'001-stop.md': 'kind: stop\nstatus: partial\n', '001-x.md': 'kind: design\nstatus: answered\n'}, True),
        ('a real advice with no kind: line', {'001-x.md': 'status: answered\n'}, True))

    def test_the_trio_plan_gate_does_not_count_a_consultants_stop_advice(self):
        """D1 (unit 015), a difference from the trio base, decided by running it: the base's gate passes on any `advice/NNN-*` file, so the
        consultant's `NNN-stop.md` (kind: stop, written when the consultant is told to stop) satisfied it. The port refuses (exit 6) unless a file
        that is not a stop is on file; a stop is `kind: stop`, or, with no `kind:` line, a name ending in `-stop.md`."""
        for n, (label, files, real) in enumerate(self.STOP_ADVICE_CASES):
            top = self.gate_world(f'stopgate{n}', review='verdict: agreed\n', advice_files=files)
            brief = top / 'briefs/002-y.md'
            ours = self.gate_python('pstack-trio', lambda ctx: ctx.variant.require_agreed_plan(ctx, str(top), str(brief)), top)
            bash = self.gate_bash('pstack-trio', f'require_agreed_plan {top} {brief}', top)
            self.assertEqual(bash[0], 0, label)
            self.assertEqual(ours[0], 0 if real else 6, label)
            if not real:
                self.assertIn('has no consultant advice under advice/001-*', ours[2], label)

    def test_discuss_sends_a_new_plan_to_a_consultant_whose_last_advice_was_its_stop(self):
        """D1 end to end on the fake herdr: with `advice/001-stop.md` newer than the plan, the base's `discuss` took it for the consultant's answer and
        sent the plan to the sidekick only; the port sends it to both."""
        plan = '# Plan 001-x\n\nscale: small\n'
        spec = dict(args=['discuss', '{st}', '{st}/plans/001-x.md', '--timeout', '200'], agents={'s1': 'idle claude', 'c1': 'idle codex'},
                    pair={'consultant': {'kind': 'codex', 'name': 'c1', 'generation': 1, 'pane_id': 'w1:p9'}},
                    files={'plans/001-x.md': plan, 'advice/001-stop.md': 'kind: stop\nstatus: partial\n'},
                    mtimes={'advice/001-stop.md': int(time.time()) + 3600}, env={'PAIR_FILE_POLL_MS': '100', 'PAIR_SUBMIT_CHECK_S': '0'})
        want = self.ab_run('stopd-base', self.base_skills(), 'pstack-trio', dict(spec))
        got = self.ab_run('stopd-port', root / 'skills', 'pstack-trio', dict(spec))
        self.assertEqual((want['rc'], got['rc']), (4, 4), (want['stdout'], want['stderr'], got['stdout'], got['stderr']))
        sent = lambda o: [e for e in o['events'] if 'send-plan' in e]
        self.assertEqual(len(sent(want)), 1, want['events'])
        self.assertTrue(sent(want)[0].endswith('sidekick'), sent(want))
        self.assertEqual(len(sent(got)), 2, got['events'])
        self.assertTrue(any(e.endswith('consultant') for e in sent(got)), sent(got))

    def test_a_consultants_stop_advice_is_not_a_plans_answer(self):
        """D1 (unit 015), a difference from the trio base: `plan_answer` for the consultant takes the newest advice for the plan's number written
        after the plan, and the base took `NNN-stop.md` for it, so a new plan round saw the consultant as answered and never sent it the plan.
        The port skips stop advice (the same rule as the gate) and answers with the newest advice that is not one."""
        def answer(tree, store, plan):
            script = (f'set -eu; source {tree}/pstack-pair/scripts/pair-core.sh; source {tree}/pstack-trio/scripts/consultant-core.sh; '
                      f'plan_answer {store} consultant 001 {plan}')
            r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.replace(str(store), '<S>')
        base = self.base_skills()
        now = int(time.time())
        for n, (label, files, real) in enumerate(self.STOP_ADVICE_CASES):
            top = self.path / f'stopans{n}'
            (top / 'advice').mkdir(parents=True)
            plan = top / '001-x.md'
            plan.write_text('# Plan\n')
            os.utime(plan, (now - 100, now - 100))
            for i, (name, text) in enumerate(sorted(files.items())):
                (top / 'advice' / name).write_text(text)
                os.utime(top / 'advice' / name, (now - 50 + (10 if name.endswith('stop.md') else 0), now - 50 + (10 if name.endswith('stop.md') else 0)))
            want, got = answer(base, top, plan), answer(root / 'skills', top, plan)
            self.assertNotEqual(want, '', label)
            self.assertEqual(got != '', real, label)
            if real and len(files) > 1:
                self.assertEqual(got, '<S>/advice/001-x.md\n'.replace('\n', ''), label)
                self.assertIn('stop.md', want, label)

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

        advice = 'pair.sh: plan <T>/plans/001-x.md has no consultant advice under advice/001-*; run pair.sh discuss so both agents respond\n'
        gate('kg1', 'escalated (a contract)', (6, advice), review=agreed)
        gate('kg2', 'escalated', (0, ''), review=agreed, advice=True)
        gate('kg3', 'escalated', (6, "pair.sh: playbook feature needs an agreed plan; brief has plan: none\n"), plan='none')
        gate('kg4', 'routine', (0, ''), plan='none')
        gate('kg5', 'routine', (0, ''), plan='')
        gate('kg6', None, (0, ''), plan='none')
        gate('kg7', 'routine', (6, "pair.sh: plan <T>/plans/001-x.md review verdict is 'revise', not agreed\n"), review='verdict: revise\n')
        gate('kg8', 'routine', (0, ''), review=agreed)
        gate('kg9', 'routine', (0, ''), playbook='investigation', plan=None)
        gate('kg10', 'routine', (6, "pair.sh: plan <T>/plans/001-x.md has no review; run pair.sh discuss and write reviews/001-<slug>.md with verdict: agreed\n"))
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
            die_prints_only_its_message(Path(cls.legacy_tmp.name) / 'skills')
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
                         (7, '', 'pair.sh: unit 002 already has two fresh steers; stop the unit and re-brief, or pass --force\n'))
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
        self.assertIn('pair.sh: git worktree add failed\n', by_name['worktree add fails'][1][2])
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
    TWINS = ('event', 'header_field', 'paused_reason',
             'require_filled',
             'agent_status', 'pane_busy', 'pane_text', 'agent_kind', 'latest_report', 'json_update', 'pair_file',
             'fresh_per_brief',
             'require_not_paused',
             'require_fresh_session', 'submit_typed',
             'in_herdr', 'latest_advice', 
             'await_file')
    TWIN_NAMES = {'json_update': 'update', 'latest_advice': 'latest'}
    # a variant's bash override of a hook, against the Python hook in that skill's variant.py
    # The bash bodies no entry script reaches. Phase 3 ended with none: a body that loses its last caller is deleted in the commit that orphans it,
    # and the guard has no baseline to add it to.
    KNOWN_DEAD = ()

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
        self.assertEqual(sorted(self.OPTIONAL_HOST_COMMANDS - set(names)), [], 'OPTIONAL_HOST_COMMANDS names that no script calls any more: remove them')
        # The fakes (herdr, ps, pi) come first on PATH, as in the other tests; a name that resolves to a file must be one of the external commands the
        # scripts are known to call, so a typo that happens to match a binary on this host fails by name.
        env = {**os.environ, 'PATH': f"{root / 'tests/pstack-pair/bin'}:{os.environ['PATH']}"}
        kind = {n: subprocess.run(['bash', '-c', 'type -t -- "$1"', 'x', n], capture_output=True, text=True, check=False, env=env).stdout.strip() for n in names}
        unknown = sorted(n for n in names if kind[n] == 'file' and n not in self.EXTERNAL_COMMANDS | self.OPTIONAL_HOST_COMMANDS)
        self.assertEqual(unknown, [], 'external commands not listed in EXTERNAL_COMMANDS (add a command the scripts really call, fix a typo otherwise)')
        self.assertEqual(sorted(m for m in missing if not kind[m[1]] and m[1] not in self.OPTIONAL_HOST_COMMANDS), [], 'calls to a name no definition, builtin or command provides')
        absent = sorted(n for n in self.EXTERNAL_COMMANDS if not shutil.which(n, path=env['PATH']))
        self.assertEqual(absent, [], 'EXTERNAL_COMMANDS names that do not resolve')

    # the external commands the entry scripts call, written out; the fakes under tests/pstack-pair/bin stand in for herdr and ps
    EXTERNAL_COMMANDS = frozenset((
        'awk', 'basename', 'cat', 'cksum', 'cp', 'cut', 'date', 'dirname', 'find', 'git', 'grep', 'head', 'herdr', 'id', 'jq', 'ln', 'ls', 'mkdir',
        'mktemp', 'mv', 'paste', 'ps', 'python3', 'readlink', 'rm', 'rmdir', 'sed', 'sleep', 'sort', 'stat', 'tail', 'tr', 'wc'))
    # commands the scripts call that a host may lack: column (kitchen.sh :1867, retro's table), setsid (pair-core.sh :212, the detach, which falls
    # back to perl) and perl (kitchen.sh :131 and :133, the brief header edits, and :1068, a verifier-outcome edit; consultant-core.sh :199 and :230;
    # the setsid fallback in pair-core.sh :215; and prime.py's timeout where coreutils has no timeout(1), which is Python, not bash, so not scanned)
    OPTIONAL_HOST_COMMANDS = frozenset(('perl',))

    def test_each_bash_twin_has_its_bash_definition_and_its_python_def(self):
        reach = self.reach()
        python = '\n'.join(p.read_text() for p in (scripts / 'pstack_core').rglob('*.py'))
        for helper in self.TWINS:
            self.assertTrue(any(f'{f}:{helper}' in reach.nodes for f in self.HELPER_FILES), f'{helper} has no bash definition: remove it from TWINS')
            self.assertRegex(python, rf'(?m)^\s*def {self.TWIN_NAMES.get(helper, helper)}\(', f'{helper} has no Python twin')

    # kitchen.sh functions verify (units 026 and 027) still calls, with their kitchen_core twin and the test that runs both on the same fixtures. Unit 027
    # deletes the bash and this entry with it.
    KITCHEN_TWINS = {  # kitchen.sh functions with a kitchen_core twin that bash still calls
    }

    def test_each_kitchen_bash_twin_has_its_bash_definition_its_python_def_and_a_test_that_runs_both(self):
        bash = (root / 'skills/pstack-kitchen/scripts/kitchen.sh').read_text()
        for name, (module, python, pin) in self.KITCHEN_TWINS.items():
            self.assertRegex(bash, rf'(?m)^{name}\(\) \{{', f'{name} has no bash definition: remove it from KITCHEN_TWINS')
            source = (root / f'skills/pstack-kitchen/scripts/kitchen_core/{module}.py').read_text()
            self.assertRegex(source, rf'(?m)^(def|class) {python}\b', f'{name} has no Python twin {module}.{python}')
            self.assertTrue(hasattr(type(self), pin), f'{name}: pinned by {pin}, which does not exist')
            # the pin must run the base bash: it names the bash function in a snippet sourced from b632713's kitchen.sh (phase4_bash), or it is an A/B
            # of the whole command through b632713's kitchen.sh, which calls the function itself
            body = inspect.getsource(getattr(type(self), pin))
            self.assertTrue(re.search(rf'\b{name}\b', body) and 'phase4_bash(' in body or re.search(r'phase4_(cases|ab)\(', body),
                            f'{name}: {pin} does not run the bash function or an A/B through b632713')

    def test_ported_commands_are_pycore_shims_in_the_script_that_defines_their_final_body(self):
        reach = self.reach()
        shim = re.compile(r'\s*pycore (--exec )?\S+ "\$@";?\s*\Z')
        for entry in self.ENTRIES:
            for name in self.PORTED:
                final = reach.final(root / 'skills' / entry, f'cmd_{name}')
                self.assertIsNotNone(final, f'{entry}: cmd_{name} is not defined')
                if (entry, name) == ('pstack-trio/scripts/pair.sh', 'discuss'):
                    self.assertEqual(final.body.strip(), 'discuss_with_consultant "$@";')
                else:
                    self.assertRegex(final.body, shim, f'{final.key} is not a pycore shim')
                    # the trio's and the kitchen's stop is consultant-core's; every other ported command's final body is pair-core's
                    home = self.CONSULTANT_CORE if name == 'stop' and entry in self.CONSULTANT_ENTRIES else self.CORE
                    if entry == 'pstack-kitchen/scripts/kitchen.sh' and name in ('queue', 'discuss', 'dispatch', 'finish', 'init', 'spawn', 'step', 'wait'):
                        home = entry  # the kitchen's own command, run from its variant
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

    def test_kitchen_sh_keeps_no_copy_fn_snapshot_of_a_pair_core_body(self):
        """Unit 029 ended the last snapshots: every command kitchen.sh has is a pycore shim, and none of the pair's commands lives on under a core_* name."""
        text = (root / 'skills/pstack-kitchen/scripts/kitchen.sh').read_text()
        self.assertNotIn('copy_fn', text)
        table = self.reach().tables[(root / 'skills/pstack-kitchen/scripts/kitchen.sh').resolve()]
        for name in ('init', 'spawn', 'queue', 'discuss', 'consultant', 'fleet', 'wait', 'dispatch', 'step', 'finish'):
            self.assertEqual(table[f'cmd_{name}'].key, f'pstack-kitchen/scripts/kitchen.sh:cmd_{name}')
            self.assertNotIn(f'core_{name}', table)

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

    # ---- unit 025: kitchen_core.units against kitchen.sh's own functions at b632713 ----

    def kitchen_core_module(self, name):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        return __import__(f'kitchen_core.{name}', fromlist=[name])

    def phase4_bash(self, store, snippet, cwd=None):
        """snippet run with b632713's kitchen.sh sourced (its last two lines, which run the command, left out): (status, stdout, stderr)."""
        lib = self.phase4_skills() / 'pstack-kitchen/scripts/kitchen-lib.sh'
        if not lib.exists():
            lib.write_text(''.join(lib.with_name('kitchen.sh').read_text().splitlines(keepends=True)[:-2]))
        env = {**self.env, **self.GIT, 'HOME': str(self.path / 'home'), 'STORE': str(store)}
        r = subprocess.run(['bash', '-c', f'. {shlex.quote(str(lib))}\nset +e\n{snippet}'], env=env, cwd=cwd or self.path, capture_output=True, text=True,
                           check=False, timeout=300)
        return r.returncode, r.stdout, r.stderr

    def units_world(self, name):
        """A repo and store for the unit model: a unit branch (u1 adds a line after line 10, u2 a file), a deletion-only unit, a merge unit and a
        trunk; stacks built on the trunk by cherry-picking, reverting, editing and squashing them."""
        top = self.path / name
        repo, st = top / 'repo', top / 'st'
        env = {**self.env, **self.GIT}
        repo.mkdir(parents=True)
        for d in ('briefs', 'reports', 'steps', 'verdicts'):
            (st / d).mkdir(parents=True)

        def g(*a):
            return subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True, timeout=120).stdout.strip()

        def w(rel, text):
            (repo / rel).write_text(text)

        def commit(msg):
            g('add', '-A')
            g('commit', '-q', '-m', msg)
            return g('rev-parse', 'HEAD')

        lines = [f'line {k}\n' for k in range(1, 31)]
        g('init', '-q', '-b', 'main')
        w('a.txt', ''.join(lines))
        w('d.txt', ''.join(f'd{k}\n' for k in range(1, 6)))
        base = commit('base')
        g('checkout', '-q', '-b', 'unit')
        w('a.txt', ''.join(lines[:10] + ['half\n'] + lines[10:]))
        u1 = commit('u1')
        w('c.txt', 'C\n')
        u2 = commit('u2')
        g('checkout', '-q', '-b', 'del', base)
        w('d.txt', 'd1\nd2\nd3\n')
        del1 = commit('del')
        g('checkout', '-q', '-b', 'mrg', base)
        w('m1.txt', 'm1\n')
        m1 = commit('m1')
        g('checkout', '-q', '-b', 'side', base)
        w('m2.txt', 'm2\n')
        s1 = commit('s1')
        g('checkout', '-q', 'mrg')
        g('merge', '--no-ff', '-q', '-m', 'merge', 'side')
        merged = g('rev-parse', 'HEAD')
        g('checkout', '-q', '-b', 'trunk', base)
        w('t.txt', 'T\n')
        trunk = commit('trunk')
        g('update-ref', 'refs/remotes/origin/main', trunk)
        stacks = {'unit': (base, u2)}

        def stack(label, steps):
            g('checkout', '-q', '-b', label, trunk)
            made = []
            for step in steps:
                if step[0] == 'pick':
                    g('cherry-pick', step[1])
                elif step[0] == 'revert':
                    g('revert', '--no-edit', made[step[1]])
                elif step[0] == 'edit':
                    w(step[1], step[2])
                    commit(step[3])
                elif step[0] == 'squash':
                    g('cherry-pick', '-n', *step[1])
                    commit('squash')
                made.append(g('rev-parse', 'HEAD'))
            stacks[label] = (trunk, g('rev-parse', 'HEAD'))
            return made

        near = ''.join(lines[:10] + ['half\n'] + lines[10:12] + ['line 13 edited\n'] + lines[13:])
        stack('plain', [('pick', u1), ('pick', u2)])
        stack('reverted', [('pick', u1), ('pick', u2), ('revert', 0)])
        stack('nearby', [('pick', u1), ('edit', 'a.txt', near, 'nearby'), ('pick', u2), ('revert', 0)])
        stack('squash', [('squash', [u1, u2])])
        stack('partial', [('pick', u1)])
        stack('reapplied', [('pick', u1), ('pick', u2), ('revert', 0), ('pick', u1)])
        stack('moved', [('pick', u1), ('edit', 'a.txt', ''.join(lines[:20] + ['half\n'] + lines[20:]), 'moved'), ('pick', u2)])
        stack('deletion', [('pick', del1)])
        stack('merges', [('pick', m1)])
        g('checkout', '-q', 'main')
        shas = {'base': base, 'u1': u1, 'u2': u2, 'del': del1, 'merged': merged, 'trunk': trunk, 'm1': m1, 's1': s1}
        units = {'001-a': u2, '002-b': u1, '003-del': del1, '004-mrg': merged, '005-nobase': u2, '006-nohead': None, '007-steps': None,
                 '008-badbase': u2, '009-badreport': None}
        heads = {n: base for n in units if n not in ('005-nobase',)}
        heads['008-badbase'] = 'nothere'
        pair = {'git_root': str(repo), 'cwd': str(repo), 'heads': heads, 'master': {'name': 'm1'}}
        (st / 'pair.json').write_text(json.dumps(pair, indent=2) + '\n')
        for n, head in units.items():
            (st / f'briefs/{n}.md').write_text(f'# Brief {n}\n\ncommit: yes\n')
            if head:
                (st / f'reports/{n}.md').write_text(f'# Report\n\nstatus: done\nhead: {head}\n')
        (st / 'reports/009-badreport.md').write_text('# Report\n\nstatus: done\nhead: not-a-commit\n')
        (st / 'steps/007-steps.tsv').write_text(f'1\t{u1}\tfirst\n')
        return st, repo, shas, stacks, units

    @staticmethod
    def inclusion_row(unit, inc):
        """The bash's `rc sha why by` for an Inclusion: rc 0 when the unit is covered."""
        return [0 if inc.covered else 1, inc.sha, inc.reason, inc.by]

    def test_the_unit_model_equals_the_bash_functions_at_the_phase_4_base(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uworld')
        names = list(recorded)
        bases = {'trunk': None, 'empty': '', 'base': shas['base']}
        seen = {}
        for label, (stack_base, head) in stacks.items():
            for which, override in bases.items():
                sbase = stack_base if override is None else override
                script = (f'for n in {" ".join(names)}; do b="$STORE/briefs/$n.md"; uh="$(unit_head "$STORE" "$b")"\n'
                          f'  if unit_in_landing_range "$STORE" "$b" "$uh" "{sbase}" "{head}"; then rc=0; else rc=$?; fi\n'
                          '  printf "range %s %s %s|%s|%s|%s\\n" "$n" "${uh:0:9}" "$rc" "$rebased_sha" "$rebased_why" "$rebased_by"\n'
                          f'  if unit_rebased_in "$STORE" "$b" "$uh" "{sbase}" "{head}"; then rc=0; else rc=$?; fi\n'
                          '  printf "rebased %s %s|%s|%s|%s\\n" "$n" "$rc" "$rebased_sha" "$rebased_why" "$rebased_by"\n'
                          'done\n')
                rc, out, err = self.phase4_bash(st, script)
                self.assertEqual(rc, 0, (label, which, err))
                index = units.StackIndex.build(str(repo), sbase, head)
                got = []
                for n in names:
                    unit = units.RecordedUnit(str(st), f'{st}/briefs/{n}.md')
                    row = self.inclusion_row(unit, units.inclusion(unit, index))
                    got.append(f'range {n} {unit.head[:9]} {row[0]}|{row[1]}|{row[2]}|{row[3]}')
                    row = self.inclusion_row(unit, units.rebased_in(unit, index))
                    got.append(f'rebased {n} {row[0]}|{row[1]}|{row[2]}|{row[3]}')
                self.assertEqual(got, out.splitlines(), (label, which))
                seen[label, which] = got
        rows = [re.fullmatch(r'range (\S+) (\S*) (\d)\|(\w*)\|(\w*)\|(\w*)', line) for lines in seen.values() for line in lines if line.startswith('range')]
        self.assertNotIn(None, rows)
        shapes = {('covered' if m[3] == '0' else 'not covered', 'sha' if m[4] else 'no sha', m[5]) for m in rows}
        # a rebased unit, a direct one, a refused merge, a revert, and units the stack does not carry
        self.assertEqual(shapes, {('covered', 'sha', ''), ('covered', 'no sha', ''), ('not covered', 'no sha', ''), ('not covered', 'no sha', 'merge'),
                                  ('not covered', 'no sha', 'reverted')})
        reverted = seen['reverted', 'trunk']
        self.assertTrue(any(line.startswith('range 002-b') and '|reverted|' in line for line in reverted), reverted)

    def test_the_patch_ids_the_unit_model_reads_equal_the_bash_commit_patch_id(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('upatch')
        commits = [c for c in subprocess.run(['git', '-C', str(repo), 'rev-list', '--all'], capture_output=True, text=True, check=True).stdout.split()]
        script = ('for c in ' + ' '.join(commits) + '; do\n'
                  f'  printf "%s|%s|%s|%s|%s\\n" "$c" "$(commit_patch_id {repo} "$c")" "$(commit_patch_id {repo} "$c" "" -U0)" '
                  f'"$(commit_patch_id {repo} "$c" -R)" "$(commit_patch_id {repo} "$c" -R -U0)"\n'
                  'done\n')
        rc, out, err = self.phase4_bash(st, script)
        self.assertEqual(rc, 0, err)
        got = [f'{c}|{units.patch_id(str(repo), c)}|{units.patch_id(str(repo), c, u0=True)}|{units.patch_id(str(repo), c, inverse=True)}|'
               f'{units.patch_id(str(repo), c, inverse=True, u0=True)}' for c in commits]
        self.assertEqual(got, out.splitlines())
        self.assertTrue(any(line.split('|')[1] for line in got))

    def test_the_stack_index_matches_the_bash_stack_arrays_and_gives_a_shared_patch_id_to_the_newer_commit(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uindex')
        base, head = stacks['reapplied']
        script = (f'stack_patch_ids {repo} {base} {head}\n'
                  'for c in "${stack_commits[@]}"; do printf "%s %s %s %s\\n" "$c" "${stack_index[$c]}" "${stack_id_of[$c]}" "${stack_id0_of[$c]}"; done\n'
                  'for i in "${!stack_ids[@]}"; do printf "id %s %s\\n" "$i" "${stack_ids[$i]}"; done | sort\n')
        rc, out, err = self.phase4_bash(st, script)
        self.assertEqual(rc, 0, err)
        index = units.StackIndex.build(str(repo), base, head).load()
        got = [f'{c} {index.position[c]} {index.id_of[c]} {index.id0_of[c]}' for c in index.commits]
        got += sorted(f'id {i} {c}' for i, c in index.by_id.items())
        self.assertEqual(got, out.splitlines())
        # u1 was picked twice: its patch-id belongs to the newer pick
        self.assertEqual(index.by_id[index.id_of[index.commits[0]]], index.commits[-1])

    def test_heads_in_equals_unit_heads_in_at_the_phase_4_base(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uheads')
        lists = [['002', '001'], ['001', '002'], ['003', '001'], ['007', '001'], ['006', '001'], ['004', '001'], ['009', '001'], ['002', '003', '001'], ['001'], ['010', '001']]
        outcomes, said = set(), ''
        for label, (stack_base, head) in stacks.items():
            subprocess.run(['git', '-C', str(repo), 'checkout', '-q', label], check=True, env={**self.env, **self.GIT})
            for names in lists:
                rc, out, err = self.phase4_bash(st, f'( set -e; unit_heads_in "$STORE" {head} {" ".join(names)} ); echo "rc=$?"', cwd=repo)
                buf, status = io.StringIO(), 0
                with contextlib.redirect_stderr(buf):
                    try:
                        units.heads_in(str(st), head, names)
                    except Die as e:
                        buf.write(f'kitchen.sh: {e.message}\n')
                        status = e.code
                lines = out.splitlines()
                self.assertEqual((status, buf.getvalue()), (int(lines[-1].removeprefix('rc=')), err), (label, names))
                outcomes.add(err.split(':')[0] if err else '')
                said += err
        self.assertTrue({'note', '', 'kitchen.sh'} <= outcomes, outcomes)
        for text in ('has no usable head', '(rebased as ', 'reverts them in the checked-out head', 'is not in the checked-out head', 'no brief for unit'):
            self.assertIn(text, said)

    def test_unit_head_and_unit_brief_equal_the_bash(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uhead')
        # a steps file whose last row is blank reads as no head; a last row with no tab is the whole row
        (st / 'steps/011-blank.tsv').write_text(f'1\t{shas["u1"]}\tfirst\n\n')
        (st / 'steps/012-notab.tsv').write_text(f'1\tabc\tfirst\n{shas["u2"]}\n')
        (st / 'steps/013-tabs.tsv').write_text(f'1\t{shas["u1"]}\n2\t\tx\n')
        recorded = {**recorded, '011-blank': None, '012-notab': None, '013-tabs': None}
        script = ('for n in ' + ' '.join(recorded) + ' 010-nobrief; do b="$STORE/briefs/$n.md"; printf "head %s %s\\n" "$n" "$(unit_head "$STORE" "$b")"; done\n'
                  'for n in 001 002 009 010 011; do printf "brief %s %s\\n" "$n" "$(unit_brief "$STORE" "$n" 2>&1 | sed "s|$STORE|<S>|")"; done\n')
        rc, out, err = self.phase4_bash(st, script)
        self.assertEqual(rc, 0, err)
        got = [f'head {n} {units.unit_head(str(st), f"{st}/briefs/{n}.md")}' for n in [*recorded, '010-nobrief']]
        for n in ('001', '002', '009', '010', '011'):
            try:
                found = units.unit_brief(str(st), n).replace(str(st), '<S>')
            except Die as e:
                found = f'kitchen.sh: {e.message}'
            got.append(f'brief {n} {found}')
        self.assertEqual(got, out.splitlines())
        self.assertIn(f'head 007-steps {shas["u1"]}', got)
        self.assertIn(f'head 012-notab {shas["u2"]}', got)
        self.assertIn('head 011-blank ', got)
        self.assertIn('brief 010 kitchen.sh: no brief for unit 010', got)

    def test_verdict_parse_equals_verdict_range_and_verdict_problem(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uverdict')
        fence = '```'
        body = f'## Evidence\n\n{fence}\nout\n{fence}\n{fence}\nmore\n{fence}\n'
        cases = {
            'clean': f'# V\n\nstatus: clean\nunits: landing\nrange: a..b\n\n{body}',
            'words after the head': f'# V\n\nstatus: clean\nunits: landing 001\nrange: aaa..bbb from the packet\n\n{body}',
            'no dots': '# V\n\nstatus: reject\nunits: 001\nrange: abc\n\n' + body,
            'no range': '# V\n\nstatus: reject\nunits: 001\n\n' + body,
            'empty base': f'# V\n\nstatus: clean\nunits: landing\nrange: ..bbb\n\n{body}',
            'empty head': f'# V\n\nstatus: clean\nunits: landing\nrange: aaa..\n\n{body}',
            'two dots twice': f'# V\n\nstatus: clean\nunits: landing\nrange: a..b..c\n\n{body}',
            'a tab after the head': f'# V\n\nstatus: clean\nunits: landing\nrange: a..b\tx\n\n{body}',
            'a bad status': f'# V\n\nstatus: maybe\nunits: 001\nrange: a..b\n\n{body}',
            'no status': f'# V\n\nunits: 001\nrange: a..b\n\n{body}',
            'one fence': f'# V\n\nstatus: clean\nunits: 001\nrange: a..b\n\n## Evidence\n\n{fence}\nout\n',
            'fences after another section': f'# V\n\nstatus: clean\nunits: 001\nrange: a..b\n\n## Evidence\n\n{fence}\n## Notes\n{fence}\n',
            'evidence heading with words': f'# V\n\nstatus: clean\nunits: 001\n\n## Evidence of it\n{fence}\n{fence}\n',
        }
        paths = {}
        for n, (name, text) in enumerate(cases.items()):
            paths[name] = st / f'verdicts/{n:03d}-v1.md'
            paths[name].write_text(text)
        packet = st / 'verdicts/001-v1-packet.md'
        packet.write_text('# P\n\nrange: pppp..qqqq\n')
        script = ('for v in ' + ' '.join(shlex.quote(str(p)) for p in [*paths.values(), st / 'verdicts/missing.md']) + '; do\n'
                  '  printf "%s|%s|%s|%s\\n" "$(verdict_range "$v")" "$(header_field "$v" status)" "$(header_field "$v" units)" "$(verdict_problem "$v")"\n'
                  'done\n')
        rc, out, err = self.phase4_bash(st, script)
        self.assertEqual(rc, 0, err)
        got = []
        for p in [*paths.values(), st / 'verdicts/missing.md']:
            v = units.Verdict.parse(str(p))
            got.append(f'{" ".join(v.refs())}|{v.status}|{v.units}|{v.problem}')
        # verdict_range prints "<base> <head>" before `read -r` splits it, so the raw halves are compared as printed
        raw = []
        for p in [*paths.values(), st / 'verdicts/missing.md']:
            v = units.Verdict.parse(str(p))
            raw.append(f'{v.range_base} {v.range_head}|{v.status}|{v.units}|{v.problem}')
        self.assertEqual(raw, out.splitlines())
        self.assertTrue(units.Verdict.parse(str(paths['words after the head'])).landing)
        self.assertFalse(units.Verdict.parse(str(paths['no dots'])).landing)
        self.assertEqual(units.Verdict.parse(str(paths['empty base'])).refs(), ('bbb', ''))
        # the packet beside a verdict wins
        self.assertEqual(got[1].split('|')[0], 'pppp qqqq')
        self.assertEqual(got[0].split('|')[0], 'a b')

    LC_BRIEF = '# Brief 001: x\n\nplaybook: bug-fix\ntimebox: 30\ncommit: yes\n'
    LC_DONE = '# R\n\nstatus: done\nhead: {c2}\n'

    def land_world(self, **extra):
        """land-check over one accepted unit, 001-x: dispatched at c1, done at c2, with no verdict yet."""
        files = {'briefs/001-x.md': self.LC_BRIEF, 'reports/001-x.md': self.LC_DONE, 'reviews/001-x.md': '# R\n\nverdict: accept\n', **extra.pop('files', {})}
        pair = {'slug': 'demo', 'heads': {'001-x': '{c1}'}, **extra.pop('pair', {})}
        return {'args': ['land-check', '{st}'], 'files': files, 'pair': pair, **extra}

    @staticmethod
    def lc_verdict(rng, status='clean', units='landing'):
        return f'# V\n\nstatus: {status}\nunits: {units}\nrange: {rng}\n'

    def test_land_check_matches_the_bash_at_the_phase_4_base(self):
        world, verdict = self.land_world, self.lc_verdict
        clean = {'verdicts/001-x-v1.md': '# V\n\nstatus: clean\nunits: 001\n'}
        landing = lambda rng, **kw: {'verdicts/landing-v1.md': verdict(rng, **kw)}
        stack = [('git', 'checkout', '-q', '-b', 'stack', '{c1}'), ('write', 't.txt', 'T\n'), ('commit', 'trunk'), ('git', 'cherry-pick', '{c2}'),
                 ('git>', 'tip', 'rev-parse', 'HEAD')]
        reverted = [*stack, ('git', 'revert', '--no-edit', 'HEAD'), ('git>', 'tip2', 'rev-parse', 'HEAD')]
        squashed = [('git', 'checkout', '-q', '-b', 'stack', '{c1}'), ('write', 't.txt', 'T\n'), ('commit', 'trunk'), ('git', 'cherry-pick', '-n', '{c2}', '{c3}'),
                    ('commit', 'squash')]
        merged = [('git', 'checkout', '-q', '-b', 'side', '{c1}'), ('write', 'm.txt', 'M\n'), ('commit', 'side'), ('git', 'checkout', '-q', 'main'),
                  ('git', 'merge', '--no-ff', '-q', '-m', 'merge', 'side'), ('git>', 'merged', 'rev-parse', 'HEAD'),
                  ('git', 'checkout', '-q', '-b', 'stack', '{c1}'), ('write', 't.txt', 'T\n'), ('commit', 'trunk'), ('git', 'cherry-pick', '{c2}'),
                  ('git>', 'tip', 'rev-parse', 'HEAD')]
        deletion = [('write', 'd.txt', 'a\nb\nc\n'), ('commit', 'b0'), ('write', 'd.txt', 'a\n'), ('commit', 'h')]
        skipped = {'reviews/001-x.md': '# R\n\nverdict: accept\nengine: skipped (deletion-only, proof: true)\n', 'reports/001-x.md': '# R\n\nstatus: done\nhead: {h}\n'}
        skip = lambda ops, files=None, pair=None: world(review_engine=True, gitops=ops, pair={'heads': {'001-x': '{b0}'}, **(pair or {})}, files={**skipped, **clean, **(files or {})})
        toml_manifests = self.KITCHEN_TOML.replace('paths = ["contracts/**"]\n', 'paths = ["contracts/**"]\nmanifests = ["requirements.txt"]\n')
        gap = 'skipped is only for a deletion-only range'
        cases = {
            'a verified unit passes': world(files=clean),
            'an unverified unit': world(),
            'a unit covered by the landing verdict': world(files=landing('{c1}..{c3}')),
            'a landing verdict that is not clean': world(files=landing('{c1}..{c3}', status='reject')),
            'a landing verdict whose head does not resolve': world(files=landing('{c1}..deadbeef0')),
            'a unit landed before the landing base': world(files={**landing('{c2}..{c3}'), 'reports/001-x.md': '# R\n\nstatus: done\nhead: {c1}\n'}),
            'a unit newer than the landing verdict': world(files={**landing('{c1}..{c2}'), 'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'},
                                                      pair={'heads': {'001-x': '{c2}'}}),
            'a unit rebased into the stack': world(gitops=stack, files=landing('{trunk}..{tip}')),
            'a rebased unit whose patch was reverted later': world(gitops=reverted, files=landing('{trunk}..{tip2}')),
            'a unit squashed into the stack': world(gitops=squashed, files=landing('{trunk}..{squash}')),
            'a landing verdict with no base that resolves, over a rebased unit': world(gitops=stack, files=landing('deadbeef0..{tip}')),
            'a unit whose range holds a merge': world(gitops=merged, files={**landing('{trunk}..{tip}'), 'reports/001-x.md': '# R\n\nstatus: done\nhead: {merged}\n'}),
            'a unit with no head': world(files={**landing('{c1}..{c3}'), 'reports/001-x.md': '# R\n\nstatus: partial\n'}),
            'a unit whose head is its last recorded step': world(files={**landing('{c1}..{c3}'), 'reports/001-x.md': '# R\n\nstatus: partial\n',
                                                                       'steps/001-x.tsv': '1\t{c2}\tfirst\n'}),
            'the newest landing verdict counts': world(files={'verdicts/landing-v2.md': verdict('{c1}..{c3}'), 'verdicts/landing-v10.md': verdict('{c1}..{c3}', status='reject')}),
            'a packet range beats the verdict range': world(files={**landing('{c1}..{c1}'), 'verdicts/landing-v1-packet.md': '# P\n\nrange: {c1}..{c3}\n'}),
            'words after the range': world(files=landing('{c1}..{c3} from the packet')),
            'a landing verdict behind HEAD': world(files=landing('{c1}..{c2}')),
            "a landing verdict not in HEAD's history": world(gitops=stack, files=landing('{c1}..{c3}')),
            'a unit named by a clean landing verdict units line': world(files={'verdicts/landing-v1.md': verdict('{c1}..{c3}', units='landing 001 002')}),
            'a landing brief needs nothing': world(files={'briefs/001-x.md': '# B\n\nplaybook: shipping\ncommit: yes\n'}),
            'a review that is not accepted': world(files={'reviews/001-x.md': '# R\n\nverdict: reject\n'}),
            'a brief that does not commit': world(files={'briefs/001-x.md': '# B\n\ncommit: no\n'}),
            'a recorded landing parent that is no longer an ancestor': world(gitops=stack, files=landing('{trunk}..{tip}'),
                                                                            pair={'landing': {'parent': '{c3}', 'parent_ref': 'rel'}}),
            'a manifest changed since the verified base': world(gitops=[('write', 'requirements.txt', 'a\n'), ('commit', 'reqs'),
                                                                        ('git', 'checkout', '-q', '-b', 'stack', '{c1}')],
                                                                toml=toml_manifests, files={**landing('{reqs}..{reqs}'), **clean}),
            'a heads that is a string': world(gitops=stack, files=landing('{trunk}..{tip}'), pair={'heads': 'x'}),
            'a dispatch that is a string': world(files=clean, pair={'dispatch': 'x'}),
            'a landing that is a string': world(files=landing('{c1}..{c3}'), pair={'landing': 'x'}),
            'a master that is a string': world(files=clean, pair={'master': 'm1'}),
            'a missing engine run': world(review_engine=True, files=clean),
            'a cited engine run': world(review_engine=True, files={**clean, 'reviews/001-x.md': '# R\n\nverdict: accept\nengine: joo /tmp/run.json\n'}),
            'an engine named with no run': world(review_engine=True, files={**clean, 'reviews/001-x.md': '# R\n\nverdict: accept\nengine: joo\n'}),
            'a deletion-only skip': skip(deletion),
            'a skip over a mode change': skip([('write', 'd.txt', 'a\nb\nc\n'), ('commit', 'b0'), ('write', 'd.txt', 'a\n'), ('chmod', 'd.txt', 0o755), ('commit', 'h')]),
            'a skip over a file with a tab in its name': skip([('write', 'a\tb.txt', 'a\nb\nc\n'), ('commit', 'b0'), ('write', 'a\tb.txt', 'a\n'), ('commit', 'h')]),
            'a skip over a deleted tab-named file': skip([('write', 'a\tb.txt', 'a\nb\nc\n'), ('commit', 'b0'), ('git', 'rm', '-q', 'a\tb.txt'), ('commit', 'h')]),
            'a skip over a modified binary file': skip([('writeb', 'b.bin', bytes(range(256))), ('commit', 'b0'), ('writeb', 'b.bin', bytes(range(128))), ('commit', 'h')]),
            'a skip over a deleted binary file': skip([('writeb', 'b.bin', bytes(range(256))), ('commit', 'b0'), ('git', 'rm', '-q', 'b.bin'), ('commit', 'h')]),
            'a skip over a rename': skip([('write', 'd.txt', 'a\nb\nc\n'), ('commit', 'b0'), ('rename', 'd.txt', 'e.txt'), ('commit', 'h')]),
            'a skip over added lines': skip([('write', 'd.txt', 'a\n'), ('commit', 'b0'), ('write', 'd.txt', 'a\nb\n'), ('commit', 'h')]),
            'a skip over a dangling dispatch head': skip(deletion, pair={'heads': {'001-x': 'deadbeef0000'}}),
            'a skip with no dispatch head': skip(deletion, pair={'heads': {}}),
            'a skip with a heads that is a string': skip(deletion, pair={'heads': 'x'}),
            'a skip with no head': skip(deletion, files={'reports/001-x.md': '# R\n\nstatus: partial\n'}),
            'a skip with no proof': skip(deletion, files={'reviews/001-x.md': '# R\n\nverdict: accept\nengine: skipped (deletion-only, proof: )\n'}),
            'a kitchen that does not validate': world(files=clean, toml='version = 1\n[profile.app]\npaths = 3\n'),
            'no pair.json': world(nopair=True),
            'no arguments': dict(args=['land-check']),
            'two arguments': dict(args=['land-check', '{st}', 'x']),
        }
        expect = {
            'a verified unit passes': (0, 'land-check: pass (review engine: none)'),
            'an unverified unit': (2, 'unverified: unit 001 has no clean verdict'),
            'a unit covered by the landing verdict': (0, 'covered: unit 001 by the clean landing verdict'),
            'a landing verdict that is not clean': (2, 'landing verification: reject'),
            'a landing verdict whose head does not resolve': (2, 'range head does not resolve; no unit is covered by it'),
            'a unit landed before the landing base': (2, 'landed before the base of landing verdict'),
            'a unit newer than the landing verdict': (2, 'is newer than landing verdict'),
            'a unit rebased into the stack': (0, 'covered: unit 001 (rebased as'),
            'a rebased unit whose patch was reverted later': (2, "commits match by patch-id but"),
            'a unit squashed into the stack': (2, '(squashed?); verify --landing covers it'),
            'a landing verdict with no base that resolves, over a rebased unit': (2, 'has no base that resolves; unit 001 cannot be matched by patch-id'),
            'a unit whose range holds a merge': (2, "range holds a merge commit, so patch-ids do not cover it"),
            'a unit with no head': (2, 'unverified: unit 001'),
            'a unit whose head is its last recorded step': (0, 'covered: unit 001 by the clean landing verdict'),
            'the newest landing verdict counts': (2, 'landing verification: reject'),
            'a packet range beats the verdict range': (0, 'covered: unit 001 by the clean landing verdict'),
            'words after the range': (0, 'covered: unit 001 by the clean landing verdict'),
            'a landing verdict behind HEAD': (0, '1 commit behind HEAD'),
            "a landing verdict not in HEAD's history": (0, "not in HEAD's history (rebased or another branch)"),
            'a unit named by a clean landing verdict units line': (0, 'land-check: pass'),
            'a landing brief needs nothing': (0, 'land-check: pass (review engine: none)'),
            'a review that is not accepted': (0, 'land-check: pass (review engine: none)'),
            'a brief that does not commit': (0, 'land-check: pass (review engine: none)'),
            'a recorded landing parent that is no longer an ancestor': (0, 'is no longer an ancestor of'),
            'a manifest changed since the verified base': (0, 'warning: dependency manifests changed since the verified base: requirements.txt'),
            'a heads that is a string': (2, 'Cannot index string with string ("001-x")'),
            'a dispatch that is a string': (0, 'land-check: pass'),
            'a landing that is a string': (0, 'Cannot index string with string ("parent")'),
            'a master that is a string': (0, 'land-check: pass'),
            'a missing engine run': (2, 'unreviewed: unit 001 cites no joo run'),
            'a cited engine run': (0, 'land-check: pass (review engine: joo)'),
            'an engine named with no run': (2, 'unreviewed: unit 001 cites no joo run'),
            'a deletion-only skip': (0, 'land-check: pass (review engine: joo)'),
            'a skip over a mode change': (2, gap),
            'a skip over a file with a tab in its name': (0, 'land-check: pass (review engine: joo)'),
            'a skip over a deleted tab-named file': (0, 'land-check: pass (review engine: joo)'),
            'a skip over a modified binary file': (2, gap),
            'a skip over a deleted binary file': (0, 'land-check: pass (review engine: joo)'),
            'a skip over a rename': (2, gap),
            'a skip over added lines': (2, gap),
            'a skip over a dangling dispatch head': (2, gap),
            'a skip with no dispatch head': (2, gap),
            'a skip with a heads that is a string': (2, 'Cannot index string with string ("001-x")'),
            'a skip with no head': (2, gap),
            'a skip with no proof': (2, 'unreviewed: unit 001 cites no joo run'),
            'a kitchen that does not validate': (2, 'kitchen: validate fails (kitchen.py validate shows why)'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh land-check <store>'),
            'two arguments': (1, 'pair.sh: usage: kitchen.sh land-check <store>'),
        }
        out = self.phase4_cases('landck', cases, expect)
        self.assertRegex(out['a unit rebased into the stack']['stdout'], r'covered: unit 001 \(rebased as [0-9a-f]{9}\) by the clean landing verdict <T>/st/verdicts/landing-v1\.md')
        self.assertIn('reverts them', out['a rebased unit whose patch was reverted later']['stdout'])
        self.assertNotIn('covered', out['a unit squashed into the stack']['stdout'])
        self.assertIn('landing verification: clean (<T>/st/verdicts/landing-v10.md)'.replace('clean', 'reject'), out['the newest landing verdict counts']['stdout'])
        self.assertIn('is no longer an ancestor of', out['a recorded landing parent that is no longer an ancestor']['stderr'])
        self.assertIn('Cannot index string with string ("parent")', out['a landing that is a string']['stderr'])
        self.assertIn(gap, out['a skip over a dangling dispatch head']['stdout'])
        self.assertIn('unit 001 cites no joo run', out['a skip with no proof']['stdout'])

    def test_a_landing_verdict_with_a_version_that_is_not_a_number_is_the_newest_and_the_bash_complains_on_stderr(self):
        """Accepted (PARITY.md): `landing-v1x.md` matches the glob; the bash's `[ 1x -le 0 ]` prints `line N: [: 1x: integer expected`, takes the file as
        the newest, and so does every later one. `1_0` (which Python's int() would accept) is such a version too: with a clean `landing-v1_0.md` beside a
        rejected `landing-v5.md` the bash takes v5, and so does the port. The port prints no shell message. stdout and the status are equal."""
        for n, version in enumerate(('1x', '1_0', '1\u0661')):
            spec = self.land_world(files={f'verdicts/landing-v{version}.md': self.lc_verdict('{c1}..{c3}'),
                                          'verdicts/landing-v5.md': self.lc_verdict('{c1}..{c3}', status='reject')})
            got, want = self.phase4_ab(f'landv{n}', spec)
            self.assertEqual((got['rc'], got['stdout'], got['files']), (want['rc'], want['stdout'], want['files']), version)
            self.assertIn('landing verification: reject (<T>/st/verdicts/landing-v5.md)', got['stdout'])
            self.assertRegex(want['stderr'], rf'line [0-9]+: \[: .*integer expected')
            self.assertEqual(got['stderr'], '')

    def test_land_check_over_a_pair_json_that_is_not_an_object_gives_the_bash_s_answer_with_jq_s_errors_counted_differently(self):
        """Accepted (PARITY.md): `.git_root` of a pair.json that is an array, or that does not parse, is an empty answer plus jq's error in the bash, at
        every read (a different number of reads per path). The port reads it through jq as well, with its own count of reads. stdout, status and
        files are equal; both sides print jq's errors."""
        for n, (label, text) in enumerate((('an array', '[]\n'), ('not JSON', 'not json\n'))):
            for kind, extra in (('no verdict', {}), ('a landing verdict', {'verdicts/landing-v1.md': self.lc_verdict('{c1}..{c3}')})):
                spec = self.land_world(nopair=True, files={'pair.json': text, **extra})
                got, want = self.phase4_ab(f'landnp{n}{len(extra)}', spec)
                self.assertEqual((got['rc'], got['stdout'], got['files']), (want['rc'], want['stdout'], want['files']), (label, kind))
                self.assertIn('unverified: unit 001' if not extra else 'covered: unit 001', got['stdout'])
                self.assertIn('jq: ', want['stderr'])
                self.assertIn('jq: ', got['stderr'])

    def test_latest_landing_verdict_equals_the_bash_and_is_landing_equals_the_bash(self):
        landing = self.kitchen_core_module('landing')
        st, repo, shas, stacks, recorded = self.units_world('ulatest')
        names = ['landing-v1.md', 'landing-v2.md', 'landing-v10.md', 'landing-v3-packet.md', 'landing-v1_0.md', 'landing-v09.md']
        sets = [[], names[:1], names[:3], names[:4], names[:3] + names[4:5], names[:3] + names[5:], names[5:] + names[:1]]
        briefs = {'a': 'playbook: opening-a-pr\n', 'b': 'playbook: shipping\n', 'c': 'landing: yes\n', 'd': 'landing: no\nplaybook: feature\n', 'e': '', 'f': 'landing: yes \n'}
        for key, text in briefs.items():
            (st / f'briefs/9{key}.md').write_text('# B\n\n' + text)
        for n, files in enumerate(sets):
            for f in (st / 'verdicts').glob('landing-*'):
                f.unlink()
            for f in files:
                (st / 'verdicts' / f).write_text('# V\n')
            rc, out, err = self.phase4_bash(st, 'latest_landing_verdict "$STORE"')
            self.assertEqual(landing.latest_landing_verdict(str(st)), out, files)
        script = ''.join(f'if is_landing "$STORE/briefs/9{k}.md"; then echo {k}=yes; else echo {k}=no; fi\n' for k in briefs)
        rc, out, err = self.phase4_bash(st, script)
        got = ''.join(f'{k}={"yes" if landing.is_landing(str(st / f"briefs/9{k}.md")) else "no"}\n' for k in briefs)
        self.assertEqual(got, out)
        self.assertIn('a=yes', got)
        self.assertIn('d=no', got)

    def test_manifest_warning_equals_the_bash_including_the_no_warning_branch_and_the_head_argument(self):
        landing = self.kitchen_core_module('landing')
        top = self.path / 'mw'
        repo, st = top / 'repo', top / 'st'
        repo.mkdir(parents=True)
        st.mkdir()
        env = {**self.env, **self.GIT}
        g = lambda *a: subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True, check=True).stdout.strip()

        def commit(msg, **files):
            for rel, text in files.items():
                (repo / rel.replace('__', '/')).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel.replace('__', '/')).write_text(text)
            g('add', '-A')
            g('commit', '-q', '-m', msg)
            return g('rev-parse', 'HEAD')

        g('init', '-q', '-b', 'main')
        (repo / '.agents').mkdir()
        (repo / '.agents/kitchen.toml').write_text(self.KITCHEN_TOML.replace('paths = ["contracts/**"]\n', 'paths = ["contracts/**"]\nmanifests = ["requirements.txt"]\n'))
        base = commit('base', **{'requirements.txt': 'a\n', 'a.txt': 'a\n'})
        r2 = commit('manifest', **{'requirements.txt': 'b\n'})
        r3 = commit('code', **{'src__x.py': 'x\n'})
        g('checkout', '-q', '-b', 'side', base)
        side = commit('side', **{'other.txt': 'o\n'})
        g('checkout', '-q', 'main')
        (st / 'pair.json').write_text(json.dumps({'git_root': str(repo)}) + '\n')
        calls = [(r2, base, None), (r2, base, base), (base, r2, None), (r3, base, None), (r3, side, None), ('', base, None), (r2, '', None), (r2, r2, None),
                 (r2, base, 'main'), (base, r3, r3)]
        warned = 0
        for verified, current, head in calls:
            extra = f' {head}' if head else ''
            rc, want, err = self.phase4_bash(st, f'manifest_warning "$STORE" "{verified}" "{current}"{extra}', cwd=repo)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                landing.manifest_warning(str(st), verified, current, head or '')
            self.assertEqual(buf.getvalue(), want, (verified[:9], current[:9], head))
            warned += bool(want)
        self.assertEqual(warned, 4)

    def test_dispatch_of_a_landing_brief_goes_through_the_python_land_check(self):
        """cmd_dispatch's bash called land_check; it calls `pycore land-check` now. A landing brief with a unit that has no clean verdict is refused
        with the same message by both trees."""
        landing_brief = '# Brief 003-ship\n\nplaybook: shipping\ntimebox: 30\ncommit: no\n\n## Scope\n\nmay write:\n- src/a.py\n\nmust not write:\n- lib/**\n'
        spec = self.land_world(args=['dispatch', '{st}', '{st}/briefs/003-ship.md', '--timeout', '1000'], files={'briefs/003-ship.md': landing_brief},
                               repo_files={'src/a.py': 'x'})
        out = self.phase4_cases('landdisp', {'a landing brief with an unverified unit': spec},
                                {'a landing brief with an unverified unit': (2, 'landing refused: kitchen.sh land-check')})
        self.assertIn('landing refused', out['a landing brief with an unverified unit']['stderr'])

    def test_revise_matches_the_bash_at_the_phase_4_base(self):
        def brief(n, scope, extra=''):
            return (f'# Brief 00{n}: x\n\nplaybook: feature\ntimebox: {20 + n}\ncommit: yes\nplan: none\nrisk: routine\n{extra}\n## Goal\n\nDo it.\n\n## Scope\n\n'
                    f'may write:\n' + ''.join(f'- {g}\n' for g in scope) + f'\nmust not write:\n- lib/**\n\n## Steps\n\n1. s\n\n## Acceptance\n\n- works {n}\n- shared\n\n'
                    f'## Verify\n\n```bash\ntrue {n}\n```\n\n## Forbidden\n\n- nothing {n}\n- shared\n')
        rejected = lambda units, status='reject': f'# V\n\nstatus: {status}\nunits: {units}\n\n## Findings\n\n1. broke\n\n## Evidence\n\n```\nout\n```\n'
        files = {'briefs/001-x.md': brief(1, ['src/a.py', 'docs/**']), 'briefs/002-y.md': brief(2, ['src/a.py', 'tests/**']), 'verdicts/001-x-v1.md': rejected('001')}
        run = ['revise', '{st}', '{st}/verdicts/001-x-v1.md']
        with_verdict = lambda units, name='verdicts/001-x-v1.md', **kw: dict(args=['revise', '{st}', '{st}/' + name], files={**files, name: rejected(units, **kw)})
        cases = {
            'a rejected unit verdict': dict(args=run, files=files),
            'a rejected unit verdict of a fix brief': dict(args=['revise', '{st}', '{st}/verdicts/003-x-fix2-v1.md'],
                                                           files={**files, 'briefs/003-x-fix2.md': brief(3, ['src/b.py']), 'verdicts/003-x-fix2-v1.md': rejected('003')}),
            'the last unit named wins': with_verdict('001 002'),
            'a rejected landing verdict names the union of the Scopes': with_verdict('landing 001 002', 'verdicts/landing-v1.md'),
            'a rejected landing verdict that names no unit takes every unit brief': with_verdict('landing', 'verdicts/landing-v1.md'),
            'a landing verdict skips landing briefs': dict(with_verdict('landing', 'verdicts/landing-v1.md'),
                                                           files={**files, 'verdicts/landing-v1.md': rejected('landing'), 'briefs/003-ship.md': brief(3, ['x'], 'landing: yes\n')}),
            'a landing verdict whose unit has no brief': with_verdict('landing 009', 'verdicts/landing-v1.md'),
            'a clean verdict is refused': dict(args=run, files={**files, 'verdicts/001-x-v1.md': rejected('001', status='clean')}),
            'an inconclusive verdict is refused': dict(args=run, files={**files, 'verdicts/001-x-v1.md': rejected('001', status='inconclusive')}),
            'a verdict with no status': dict(args=run, files={**files, 'verdicts/001-x-v1.md': '# V\n\nunits: 001\n'}),
            'a missing verdict': dict(args=['revise', '{st}', '{st}/verdicts/none.md'], files=files),
            'a verdict that names no unit': with_verdict('none'),
            'a unit with no brief': with_verdict('009'),
            'a landing verdict over a store whose briefs are the two default ones': dict(args=['revise', '{st}', '{st}/verdicts/landing-v1.md'],
                                                                                        files={'verdicts/landing-v1.md': rejected('landing')}, pair={'slug': 'demo'}),
            'a landing verdict over a store with no unit briefs to fix': dict(args=['revise', '{st}', '{st}/verdicts/landing-v1.md'],
                                                                              files={'verdicts/landing-v1.md': rejected('landing'),
                                                                                     'briefs/001-x.md': '# B\n\nplaybook: shipping\n', 'briefs/002-y.md': '# B\n\nlanding: yes\n'}),
            'a heads that is a string': dict(args=run, files=files, pair={'slug': 'demo', 'heads': 'x'}),
            'a dispatch that is a string': dict(args=run, files=files, pair={'slug': 'demo', 'dispatch': 'x'}),
            'no pair.json': dict(args=run, files=files, nopair=True),
            'no arguments': dict(args=['revise']),
            'one argument': dict(args=['revise', '{st}']),
            'three arguments': dict(args=run + ['x']),
        }
        expect = {
            'a rejected unit verdict': (0, '/st/briefs/003-x-fix.md'),
            'a rejected unit verdict of a fix brief': (0, '/st/briefs/004-x-fix.md'),
            'the last unit named wins': (0, '/st/briefs/003-y-fix.md'),
            'a rejected landing verdict names the union of the Scopes': (0, '/st/briefs/003-landing-fix.md'),
            'a rejected landing verdict that names no unit takes every unit brief': (0, '/st/briefs/003-landing-fix.md'),
            'a landing verdict skips landing briefs': (0, '/st/briefs/004-landing-fix.md'),
            'a landing verdict whose unit has no brief': (1, 'pair.sh: no brief for unit 009'),
            'a clean verdict is refused': (1, 'pair.sh: only a rejected verdict needs a fix brief'),
            'an inconclusive verdict is refused': (1, 'pair.sh: only a rejected verdict needs a fix brief'),
            'a verdict with no status': (1, 'pair.sh: only a rejected verdict needs a fix brief'),
            'a missing verdict': (1, 'pair.sh: verdict not found:'),
            'a verdict that names no unit': (1, 'names no unit (units: none)'),
            'a unit with no brief': (1, 'pair.sh: no brief for unit 009'),
            'a landing verdict over a store whose briefs are the two default ones': (0, '/st/briefs/003-landing-fix.md'),
            'a landing verdict over a store with no unit briefs to fix': (1, 'pair.sh: no unit briefs to fix in'),
            'a heads that is a string': (0, '/st/briefs/003-x-fix.md'),
            'a dispatch that is a string': (0, '/st/briefs/003-x-fix.md'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh revise <store> <verdict-path>'),
            'one argument': (1, 'pair.sh: usage: kitchen.sh revise <store> <verdict-path>'),
            'three arguments': (1, 'pair.sh: usage: kitchen.sh revise <store> <verdict-path>'),
        }
        out = self.phase4_cases('revise', cases, expect)
        unit = out['a rejected unit verdict']['files']['briefs/003-x-fix.md']
        self.assertIn('playbook: bug-fix', unit)
        self.assertIn('- verdict: <T>/st/verdicts/001-x-v1.md', unit)
        self.assertIn('true 1', unit)
        landing = out['a rejected landing verdict names the union of the Scopes']['files']['briefs/003-landing-fix.md']
        self.assertEqual(landing.count('- src/a.py'), 1)
        self.assertIn('- docs/**', landing)
        self.assertIn('- tests/**', landing)
        self.assertIn('true 1', landing)
        self.assertIn('true 2', landing)

    # ---- unit 026: kitchen_core.verify_state against kitchen.sh's own functions at b632713 ----

    def kitchen_py(self, store, code, env=None, cwd=None):
        """Python run in a fresh interpreter with kitchen_core.verify_state loaded as `vs` and a context `ctx`: (status, stdout, stderr)."""
        pre = (f'import sys\nsys.path.insert(0, {str(scripts)!r})\nsys.path.insert(0, {str(root / "skills/pstack-kitchen/scripts")!r})\n'
               'import variant\nvariant.load_kitchen_core()\nfrom kitchen_core import units, verify, verify_state as vs\nfrom pstack_core.context import Context\n'
               'from pstack_core.errors import Die, Exit\n'
               f'ctx = Context("pstack-kitchen", {str(root / "skills/pstack-kitchen")!r}, "/s", "Kitchen", ("sidekick",), ("plans",), 20, "")\n'
               f'store = {str(store)!r}\n')
        r = subprocess.run([sys.executable, '-I', '-c', pre + code], env={**self.env, **self.GIT, 'HOSTNAME': 'testhost', **(env or {})}, capture_output=True,
                           text=True, check=False, timeout=120, cwd=cwd)
        return r.returncode, r.stdout, r.stderr

    def claim_store(self, name, verifying, extra=None):
        st = self.path / name
        (st / 'verdicts').mkdir(parents=True)
        (st / 'pair.json').write_text(json.dumps({'master': {'name': 'm1'}, 'verifying': verifying, **(extra or {})}, indent=2) + '\n')
        return st

    @staticmethod
    def mask_token(text):
        return re.sub(r'testhost:[0-9]+:[0-9]+', 'testhost:<PID>:<N>', text)

    def spawn_live_and_dead_pids(self):
        """A pid that is alive until the test ends, and one that is gone."""
        live = subprocess.Popen(['sleep', '600'])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        dead = subprocess.Popen(['true'])
        dead.wait()
        return live.pid, dead.pid

    def test_claim_close_equals_the_bash_for_a_fresh_claim_a_held_one_a_dead_owner_and_odd_documents(self):
        live, dead = self.spawn_live_and_dead_pids()
        base = {'verdict': '/v/001-x-v1.md', 'units': '001'}
        cases = {
            'a fresh claim': base,
            'a claim held by a live process': {**base, 'closing': {'token': f'testhost:{live}:7'}},
            'a claim whose owner is gone': {**base, 'closing': {'token': f'testhost:{dead}:7'}},
            'a claim from another host': {**base, 'closing': {'token': f'elsewhere:{dead}:7'}},
            'a claim held by a process of another user (EPERM; pid 1)': {**base, 'closing': {'token': 'testhost:1:7'}},
            'a token with no colon': {**base, 'closing': {'token': 'garbage'}},
            'a token with a pid that is no number': {**base, 'closing': {'token': 'testhost:abc:7'}},
            'a token with a pid int() would take (1_2345)': {**base, 'closing': {'token': 'testhost:1_2345:7'}},
            'a token with a pid in other digits': {**base, 'closing': {'token': 'testhost:\u0661\u0662:7'}},
            'an empty token': {**base, 'closing': {'token': ''}},
            'a token that is a number': {**base, 'closing': {'token': 5}},
            'a token that is false': {**base, 'closing': {'token': False}},
            'closing that is a string': {**base, 'closing': 'x'},
            'closing that is an array': {**base, 'closing': [1]},
            'a verification that is another attempt': {'verdict': '/v/001-x-v2.md', 'units': '001'},
            'a verification that is a string': 'x',
            'a verification that is an array': [1],
            'no verification': None,
        }
        outcomes = {}
        for n, (name, verifying) in enumerate(cases.items()):
            extra = {} if verifying is not None else {'verifying': None}
            results = []
            for side in ('bash', 'py'):
                st = self.claim_store(f'claim{n}{side}', verifying)
                if side == 'bash':
                    rc, out, err = self.phase4_bash(st, 'HOSTNAME=testhost; claim_close "$STORE" /v/001-x-v1.md; echo "rc=$?"')
                else:
                    rc, out, err = self.kitchen_py(st, 'print("rc=%d" % (0 if vs.claim_close(store, "/v/001-x-v1.md") else 1))', env={'PAIR_SHELL_PID': str(os.getpid())})
                self.assertEqual(rc, 0, (name, side, err))
                results.append((out, self.mask_token(err).replace(str(st), '<S>'), self.mask_token((st / 'pair.json').read_text()), self.mask_token(
                    ''.join(sorted(p.name for p in st.iterdir())))))
            self.assertEqual(results[0], results[1], name)
            outcomes[name] = results[0]
        self.assertTrue(outcomes['a fresh claim'][0].endswith('rc=0\n'))
        self.assertIn('testhost:<PID>:<N>', outcomes['a fresh claim'][2])
        self.assertEqual(outcomes['a claim held by a live process'][0], 'rc=1\n')
        self.assertTrue(outcomes['a claim whose owner is gone'][0].endswith('rc=0\n'))
        self.assertEqual(outcomes['a claim from another host'][0], 'rc=1\n')
        self.assertTrue(outcomes['a token with a pid that is no number'][0].endswith('rc=0\n'))  # kill -0 abc fails: taken over
        self.assertEqual(outcomes['a verification that is another attempt'][0], 'rc=1\n')
        for odd in ('a token with a pid int() would take (1_2345)', 'a token with a pid in other digits'):
            self.assertTrue(outcomes[odd][0].endswith('rc=0\n'), odd)  # kill -0 refuses it: taken over
        self.assertIn('Cannot index', outcomes['closing that is a string'][1])

    def test_update_verifying_drop_verifying_release_close_and_the_claim_tests_equal_the_bash(self):
        live, dead = self.spawn_live_and_dead_pids()
        other_attempt = {'verdict': '/v/001-x-v2.md', 'units': '001', 'idle_since': 5}
        mine = {'verdict': '/v/001-x-v1.md', 'units': '001', 'idle_since': 5, 'closing': {'token': 'testhost:4242:9'}}
        bash_calls = {
            'a stale-attempt write after the next attempt started': 'update_verifying "$STORE" /v/001-x-v1.md \'.verifying.idle_since = $at\' --argjson at 99',
            'a write on the verification it holds': 'update_verifying "$STORE" /v/001-x-v1.md \'.verifying.idle_since = $at\' --argjson at 99',
            'a drop on the verification it holds': 'drop_verifying "$STORE" /v/001-x-v1.md',
            'a stale drop': 'drop_verifying "$STORE" /v/001-x-v1.md',
            'a release with its own token': 'release_close "$STORE" /v/001-x-v1.md testhost:4242:9',
            'a release with another token': 'release_close "$STORE" /v/001-x-v1.md testhost:1:1',
            'a release on a stale verification': 'release_close "$STORE" /v/001-x-v1.md testhost:4242:9',
            'a release over closing that is a string': 'release_close "$STORE" /v/001-x-v1.md testhost:4242:9',
        }
        py_calls = {
            'a stale-attempt write after the next attempt started': 'vs.update_verifying(store, "/v/001-x-v1.md", ".verifying.idle_since = $at", lambda v: v.__setitem__("idle_since", 99), "--argjson", "at", "99")',
            'a write on the verification it holds': 'vs.update_verifying(store, "/v/001-x-v1.md", ".verifying.idle_since = $at", lambda v: v.__setitem__("idle_since", 99), "--argjson", "at", "99")',
            'a drop on the verification it holds': 'vs.drop_verifying(store, "/v/001-x-v1.md")',
            'a stale drop': 'vs.drop_verifying(store, "/v/001-x-v1.md")',
            'a release with its own token': 'vs.release_close(store, "/v/001-x-v1.md", "testhost:4242:9")',
            'a release with another token': 'vs.release_close(store, "/v/001-x-v1.md", "testhost:1:1")',
            'a release on a stale verification': 'vs.release_close(store, "/v/001-x-v1.md", "testhost:4242:9")',
            'a release over closing that is a string': 'vs.release_close(store, "/v/001-x-v1.md", "testhost:4242:9")',
        }
        docs = {'a stale-attempt write after the next attempt started': other_attempt, 'a stale drop': other_attempt, 'a release on a stale verification': other_attempt,
                'a release over closing that is a string': {**mine, 'closing': 'x'}}
        for n, name in enumerate(bash_calls):
            results = []
            for side in ('bash', 'py'):
                st = self.claim_store(f'upd{n}{side}', docs.get(name, mine))
                if side == 'bash':
                    rc, out, err = self.phase4_bash(st, f'{bash_calls[name]}; echo "rc=$?"')
                else:
                    rc, out, err = self.kitchen_py(st, py_calls[name] + '\nprint("rc=0")')
                if side == 'py' and rc != 0:
                    out = 'rc=%d\n' % rc
                results.append((out, err.replace(str(st), '<S>'), (st / 'pair.json').read_text()))
            self.assertEqual(results[0], results[1], name)
        written = json.loads(self.claim_store('updx', mine).joinpath('pair.json').read_text())
        self.assertIn('closing', written['verifying'])

    def test_my_claim_and_still_my_claim_equal_the_bash(self):
        """b632713's functions as they are: the snippet writes the token from inside the shell, with its own `$$`, and the port then runs over the same
        document with that pid as the shell's. Every case is compared."""
        cases = {'a claim of this process': '$HOSTNAME:$$:9', 'a claim of another process': '$HOSTNAME:$(( $$ + 1 )):9', 'a claim of another host': 'other:$$:9',
                 'a claim of this host with a longer pid': '$HOSTNAME:${$}0:9', 'no claim': ''}
        seen = {}
        for n, (name, token) in enumerate(cases.items()):
            st = self.claim_store(f'my{n}', {'verdict': 'v'})
            write = f'json_update "$STORE" --arg t "{token}" \'.verifying.closing = {{token: $t}}\'; ' if token else ''
            rc, out, err = self.phase4_bash(st, 'HOSTNAME=testhost; echo "pid=$$"; ' + write + 'if my_claim "$STORE"; then echo mine; else echo notmine; fi; '
                                                   '( still_my_claim "$STORE" ); echo "rc=$?"')
            self.assertEqual(rc, 0, (name, err))
            pid = out.splitlines()[0].removeprefix('pid=')
            want = out.splitlines()[1:]
            code = ('print("mine" if vs.my_claim(store) else "notmine")\ntry:\n    vs.still_my_claim(store)\n    print("rc=0")\n'
                    'except Exit as e:\n    print("rc=%d" % e.code)')
            prc, got, perr = self.kitchen_py(st, code, env={'PAIR_SHELL_PID': pid})
            self.assertEqual((got.splitlines(), perr), (want, err), name)
            seen[name] = want
        self.assertEqual(seen['a claim of this process'][0], 'mine')
        self.assertEqual(seen['a claim of another process'][0], 'notmine')
        self.assertEqual(seen['a claim of another host'][0], 'notmine')
        self.assertEqual(seen['no claim'][-1], 'rc=5')
        self.assertEqual(seen['a claim of this process'][-1], 'rc=0')

    def test_cksum_and_audit_due_equal_the_commands(self):
        for text in ('001-x', '', 'landing', '002-y-fix', 'é', 'a' * 5000):
            want = subprocess.run(['cksum'], input=text.encode(), capture_output=True, check=True).stdout.split()[0].decode()
            self.assertEqual(str(self.kitchen_core_module('verify_state').cksum(text.encode())), want, text[:20])
        vs = self.kitchen_core_module('verify_state')
        st = self.claim_store('audit', {'verdict': 'v'})
        for unit in ('001-x', '002-y', '003-z', 'landing', '010-fix'):
            for sample in ('0', '0.1', '0.5', '1', '', 'abc', '2'):
                rc, out, err = self.phase4_bash(st, f'if audit_due "{unit}" "{sample}"; then echo yes; else echo no; fi')
                self.assertEqual('yes\n' if vs.audit_due(unit, sample) else 'no\n', out, (unit, sample))

    def test_a_failed_cleanup_releases_the_claim_and_run_close_ends_with_the_status_equal_to_the_bash(self):
        """close_verifier's last command is the scratch removal: an id the scratch command refuses ends the close with its status, and the claim,
        which this process took, is given back before the command ends."""
        verifying = {'verdict': '/v/001-x-v1.md', 'units': '001', 'scratch': 'bad id', 'brief': '001-x', 'packet': '/nonexistent/p.md'}
        extra = {'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}, 'git_root': str(self.path)}
        results = []
        for side in ('bash', 'py'):
            st = self.claim_store(f'rc{side}', verifying, extra)
            if side == 'bash':
                rc, out, err = self.phase4_bash(st, 'HOSTNAME=testhost; (run_close "$STORE" v1 claude p3 /v/001-x-v1.md); echo "rc=$?"')
            else:
                rc, out, err = self.kitchen_py(st, 'try:\n    vs.run_close(ctx, store, "v1", "claude", "p3", "/v/001-x-v1.md")\nexcept Exit as e:\n    print("rc=%d" % e.code)\n',
                                               env={'PAIR_SHELL_PID': str(os.getpid())})
            results.append((out, self.mask_token(err).replace(str(st), '<S>').replace('kitchen.sh: ', 'pair.sh: '), self.mask_token((st / 'pair.json').read_text())))
        self.assertEqual(results[0], results[1])
        self.assertIn('rc=1', results[0][0])
        self.assertNotIn('closing', results[0][2])

    def test_a_claim_written_by_one_side_binds_the_other_in_both_directions(self):
        """The compatibility A6 names, for the claim: the Python closer's token is read by the bash closer as a live holder (while its process lives)
        and as a dead one (after), the bash closer's token is taken over by the Python closer once its process is gone, and either side releases the
        other's token."""
        live, dead = self.spawn_live_and_dead_pids()
        verdict = '/v/001-x-v1.md'
        st = self.claim_store('compat', {'verdict': verdict, 'units': '001'})
        # a Python closer, whose process is alive: the bash closer must refuse
        self.assertEqual(self.kitchen_py(st, f'print(vs.claim_close(store, {verdict!r}))', env={'PAIR_SHELL_PID': str(live)})[1], 'True\n')
        token = json.loads((st / 'pair.json').read_text())['verifying']['closing']['token']
        self.assertRegex(token, rf'^testhost:{live}:[0-9]+$')
        rc, out, err = self.phase4_bash(st, f'HOSTNAME=testhost; if claim_close "$STORE" {verdict}; then echo took; else echo refused; fi')
        self.assertEqual(out, 'refused\n')
        self.assertEqual(json.loads((st / 'pair.json').read_text())['verifying']['closing']['token'], token)
        # the bash closer releases the Python closer's token
        rc, out, err = self.phase4_bash(st, f'HOSTNAME=testhost; release_close "$STORE" {verdict} {token}')
        self.assertNotIn('closing', json.loads((st / 'pair.json').read_text())['verifying'])
        # a bash closer that has gone (its token names a dead pid): the Python closer takes over, and releases with its own token
        rc, out, err = self.phase4_bash(st, 'HOSTNAME=testhost; claim_close "$STORE" ' + verdict)
        bash_token = json.loads((st / 'pair.json').read_text())['verifying']['closing']['token']
        self.assertRegex(bash_token, r'^testhost:[0-9]+:[0-9]+$')
        self.assertEqual(self.kitchen_py(st, f'print(vs.claim_close(store, {verdict!r}))', env={'PAIR_SHELL_PID': str(live)})[1], 'True\n')
        mine = json.loads((st / 'pair.json').read_text())['verifying']['closing']['token']
        self.assertRegex(mine, rf'^testhost:{live}:')
        self.assertNotEqual(mine, bash_token)
        self.kitchen_py(st, f'vs.release_close(store, {verdict!r}, {mine!r})')
        self.assertNotIn('closing', json.loads((st / 'pair.json').read_text())['verifying'])
        # the Python closer's token names a process that has gone: the bash closer takes over
        self.assertEqual(self.kitchen_py(st, f'print(vs.claim_close(store, {verdict!r}))', env={'PAIR_SHELL_PID': str(dead)})[1], 'True\n')
        rc, out, err = self.phase4_bash(st, f'HOSTNAME=testhost; if claim_close "$STORE" {verdict}; then echo took; else echo refused; fi')
        self.assertEqual(out, 'took\n')

    VERIFY_ENV = {'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERIFY_GRACE_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0', 'KITCHEN_VERIFIER_EXIT_S': '0'}
    VERDICT_FENCES = '\n## Evidence\n\n```\n$ true\n```\n\n```\nok\n```\n'

    def verify_world(self, **extra):
        """An open verification of unit 001-x by claude verifier v1 in pane p3, started 150 s ago with 30 minutes to run, on the fake herdr."""
        now = int(time.time())
        verifying = {'verdict': '{st}/verdicts/001-x-v1.md', 'units': '001', 'brief': '001-x', 'packet': '{st}/verdicts/001-x-v1-packet.md', 'scratch': '001-x-v1',
                     'started': now - 150, 'deadline': now + 1800, 'risk': 'routine', 'sample': 0, 'unit': '001-x', 'delivered': True, 'argv': ['001'],
                     **extra.pop('verifying', {})}
        pair = {'slug': 'demo', 'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}, 'verifying': verifying, 'master': {'name': 'm1', 'units_since_handoff': 0},
                **extra.pop('pair', {})}
        files = {'verdicts/001-x-v1-packet.md': '# Packet\n\nverifier-outcome: running\n', **extra.pop('files', {})}
        env = {**self.VERIFY_ENV, **extra.pop('env', {})}
        return {'args': ['verify', '{st}', '--cancel'], 'pair': pair, 'agents': extra.pop('agents', {'v1': 'idle claude'}), 'panes': {'p3': 'v1'}, 'files': files,
                'env': env, **extra}

    @staticmethod
    def live_and_dead_token_hosts():
        """The host the shell reads from $HOSTNAME, a pid that is alive for the whole test and one that is gone."""
        host = subprocess.run(['bash', '-c', 'echo "$HOSTNAME"'], capture_output=True, text=True, check=True).stdout.strip()
        dead = subprocess.Popen(['true'])
        dead.wait()
        return host, os.getpid(), dead.pid

    def test_verify_cancel_matches_the_bash_at_the_phase_4_base(self):
        host, live, dead = self.live_and_dead_token_hosts()
        world = self.verify_world
        row = {'activeSessionId': 'live-v', 'sessionId': 'sess-v', 'sessionFile': '/t/v.jsonl', 'name': 'v1'}
        prime = dict(agents={'v1': 'idle prime-agent'}, pair={'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'prime-agent', 'session': {'kind': 'path', 'value': '/t/v.jsonl'}}},
                     fake={'prime-sessions.json': json.dumps([row]), 'sessions/v1': '/t/v.jsonl'})
        cases = {
            'a verification with no verdict is cancelled': world(),
            'a verdict already written': world(files={'verdicts/001-x-v1.md': '# V\n\nstatus: clean\nunits: 001\n'}),
            'nothing open': world(pair={'verifying': None}),
            'a claim held by a live call': world(verifying={'closing': {'token': f'{host}:{live}:1'}}),
            'a claim whose owner is gone': world(verifying={'closing': {'token': f'{host}:{dead}:1'}}),
            'a claim from another host': world(verifying={'closing': {'token': f'elsewhere:{dead}:1'}}),
            'a verifier that does not exit': world(noexit=True),
            'a Prime verifier whose cleanup succeeds': world(**prime),
            'a Prime verifier whose cleanup fails': world(**{**prime, 'fake': {**prime['fake'], 'prime-stop-fail': ''}}),
            'a Prime verifier whose shell never takes the foreground': world(**{**prime, 'clock': True, 'fake': {**prime['fake'], 'prime-client-stays': ''}}),
            'a verifier already gone': world(agents={'v1': 'idle claude'}, panes={}, master=None),
            'a scratch id the scratch command refuses': world(verifying={'scratch': 'bad id'}),
            'a packet that is not there': world(verifying={'packet': '{st}/verdicts/none-packet.md'}),
            'a verifying that is a string': world(pair={'verifying': 'x'}),
            'a verifier that is a string': world(pair={'verifier': 'v1'}),
            'a verifier that is false': world(pair={'verifier': False}),
            'a master that is a string': world(pair={'master': 'm1'}),
            '--cancel with --wait': world(args=['verify', '{st}', '--cancel', '--wait']),
            '--cancel with a unit': world(args=['verify', '{st}', '--cancel', '001']),
            'outside herdr': world(env={'HERDR_ENV': '0'}),
        }
        expect = {
            'a verification with no verdict is cancelled': (0, 'cancelled: 001'),
            'a verdict already written': (5, 'the verdict is written ('),
            'nothing open': (5, 'no verification open'),
            'a claim held by a live call': (5, 'no verification open (another call closed it)'),
            'a claim whose owner is gone': (0, 'cancelled: 001'),
            'a claim from another host': (5, 'no verification open (another call closed it)'),
            'a verifier that does not exit': (0, 'cancelled: 001'),
            'a Prime verifier whose cleanup succeeds': (0, 'cancelled: 001'),
            'a Prime verifier whose cleanup fails': (5, 'Prime verifier cleanup failed; its pane and scratch stay for inspection'),
            'a Prime verifier whose shell never takes the foreground': (5, 'Prime client has not exited in p3'),
            'a verifier already gone': (0, 'cancelled: 001'),
            'a scratch id the scratch command refuses': (1, 'scratch id must look like'),
            'a packet that is not there': (0, 'cancelled: 001'),
            'a verifying that is a string': (5, 'Cannot index string with string ("verdict")'),
            'a verifier that is a string': (5, 'Cannot index string with string ("name")'),
            'a verifier that is false': (5, 'Cannot index boolean with string ("name")'),
            'a master that is a string': (0, 'cancelled: 001'),
            '--cancel with --wait': (1, '--cancel takes no units, --landing or --wait'),
            '--cancel with a unit': (1, '--cancel takes no units, --landing or --wait'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.phase4_cases('vcancel', cases, expect)
        done = out['a verification with no verdict is cancelled']
        self.assertNotIn('"verifying"', done['files']['pair.json'])
        self.assertIn('verifier-outcome: cancelled', done['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verify-cancel\t001-x\t001', '\n'.join(done['events']))
        self.assertIn('pane close p3', done['calls'])
        self.assertIn('agent prompt v1 /exit', done['calls'])
        held = out['a claim held by a live call']
        self.assertIn('"verifying"', held['files']['pair.json'])
        self.assertNotIn('pane close', held['calls'])
        refused = out['a scratch id the scratch command refuses']
        self.assertNotIn('"closing"', refused['files']['pair.json'])
        self.assertIn('"verifying"', refused['files']['pair.json'])

    @staticmethod
    def vverdict(status='clean', units='001', evidence=True, findings='1. it broke\n'):
        return (f'# Verdict\n\nstatus: {status}\nunits: {units}\n\n## Findings\n\n{findings}' + ('\n## Evidence\n\n```\n$ true\n```\n\n```\nok\n```\n' if evidence else ''))

    def wait_world(self, every='5', **extra):
        spec = self.verify_world(**extra)
        spec['args'] = ['verify', '{st}', '--wait', '--every', every]
        return spec

    def wait_mask(self):
        return [(r'grace [0-9]+s left', 'grace Ns left')]

    def test_verify_wait_matches_the_bash_at_the_phase_4_base_for_verdicts(self):
        world, V = self.wait_world, self.vverdict
        verdict = lambda text, name='verdicts/001-x-v1.md': {name: text}
        engine = dict(review_engine=True, files={'briefs/001-x.md': '# B\n\ncommit: yes\nplaybook: bug-fix\n', 'reviews/001-x.md': '# R\n\nverdict: accept\n',
                                                 'verdicts/001-x-v1.md': V()})
        cases = {
            'a clean verdict': world(files=verdict(V())),
            'a clean verdict that is sampled for audit': world(files=verdict(V()), verifying={'sample': 1}),
            'a clean verdict that is the fifth since the handoff': world(files=verdict(V()), pair={'master': {'name': 'm1', 'units_since_handoff': 4}}),
            'a clean verdict of a landing verification': world(files=verdict(V(units='landing'), 'verdicts/landing-v1.md'),
                                                                verifying={'verdict': '{st}/verdicts/landing-v1.md', 'unit': 'landing', 'packet': '{st}/verdicts/landing-v1-packet.md'},
                                                                pair={'master': {'name': 'm1', 'units_since_handoff': 4}}),
            'a clean verdict and an unreviewed unit': world(**engine),
            'a rejected verdict': world(files=verdict(V('reject'))),
            'a second rejection of the unit': world(files={'verdicts/001-x-v1.md': V('reject'), 'verdicts/001-x-v2.md': V('reject'), 'verdicts/001-x-v3-packet.md': 'status: reject\n'}),
            'a rejected landing verdict': world(files=verdict(V('reject', units='landing'), 'verdicts/landing-v1.md'),
                                                 verifying={'verdict': '{st}/verdicts/landing-v1.md', 'unit': 'landing', 'packet': '{st}/verdicts/landing-v1-packet.md'}),
            'a rejected verdict with many findings': world(files=verdict(V('reject', findings=''.join(f'{n}. finding {n}\n\n' for n in range(1, 30))))),
            'an inconclusive verdict': world(files=verdict(V('inconclusive'))),
            'a verdict with no evidence': world(files=verdict(V(evidence=False))),
            'a verdict with a status that is none of the three': world(files=verdict(V('maybe'))),
            'a verdict with no status line': world('0', files=verdict('# V\n\nunits: 001\n'), agents={'v1': 'working claude'}),
            'a verdict closed with a claim whose owner is gone': world(files=verdict(V()), verifying={'closing': {'token': '@HOST@:@DEAD@:1'}}),
            'a verdict while another live call holds the claim': world(files=verdict(V()), verifying={'closing': {'token': '@HOST@:@LIVE@:1'}}),
            'a verdict whose scratch the scratch command refuses': world(files=verdict(V()), verifying={'scratch': 'bad id'}),
            'a verdict while the verifier does not exit': world(files=verdict(V()), noexit=True),
            'a verdict with a packet that is not there': world(files=verdict(V()), verifying={'packet': '{st}/verdicts/none-packet.md'}),
            'a verdict of a verifier that is gone': world(files=verdict(V()), agents={}, panes={}, master=None),
        }
        host, live, dead = self.live_and_dead_token_hosts()
        for spec in cases.values():
            token = spec['pair']['verifying'].get('closing', {}).get('token')
            if token:
                spec['pair']['verifying']['closing']['token'] = token.replace('@HOST@', host).replace('@LIVE@', str(live)).replace('@DEAD@', str(dead))
        expect = {
            'a clean verdict': (0, 'audit: no'),
            'a clean verdict that is sampled for audit': (0, 'audit: yes; read the review-delta yourself before accepting'),
            'a clean verdict that is the fifth since the handoff': (0, 'master: 5 units verified since your last handoff'),
            'a clean verdict of a landing verification': (0, 'audit: no'),
            'a clean verdict and an unreviewed unit': (0, 'warning: unreviewed: unit 001 cites no joo run'),
            'a rejected verdict': (2, 'next: kitchen.sh revise'),
            'a second rejection of the unit': (2, 'next: second rejection of 001-x'),
            'a rejected landing verdict': (2, '--landing again'),
            'a rejected verdict with many findings': (2, '20. finding 20'),
            'an inconclusive verdict': (2, "next: read the verdict; an inconclusive or invalid one is the master's to settle"),
            'a verdict with no evidence': (2, 'status: invalid (no command output under Evidence)'),
            'a verdict with a status that is none of the three': (2, 'status: invalid (status is not clean, reject, or inconclusive)'),
            'a verdict with no status line': (4, 'verifying: units 001 by claude'),
            'a verdict closed with a claim whose owner is gone': (0, 'status: clean'),
            'a verdict while another live call holds the claim': (5, 'verification closed elsewhere'),
            'a verdict whose scratch the scratch command refuses': (1, 'scratch id must look like'),
            'a verdict while the verifier does not exit': (0, 'status: clean'),
            'a verdict with a packet that is not there': (0, 'status: clean'),
            'a verdict of a verifier that is gone': (0, 'status: clean'),
        }
        out = self.phase4_cases('vwait', {k: {**v, 'mask': self.wait_mask()} for k, v in cases.items()}, expect)
        clean = out['a clean verdict']
        self.assertNotIn('"verifying"', clean['files']['pair.json'])
        self.assertIn('verifier-outcome: clean', clean['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verify\t001-x\tclean', '\n'.join(clean['events']))
        self.assertIn('wake\t001-x\tverify:clean', '\n'.join(clean['events']))
        self.assertIn('"units_since_handoff": 1', clean['files']['pair.json'])
        self.assertIn('verifier-outcome: invalid', out['a verdict with no evidence']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('"verifying"', out['a verdict while another live call holds the claim']['files']['pair.json'])
        self.assertNotIn('"closing"', out['a verdict whose scratch the scratch command refuses']['files']['pair.json'])

    def test_the_interval_of_a_wait_is_read_as_the_bash_arithmetic_reads_it(self):
        """Accepted (PARITY.md): `--every WORD` goes to `$(( now + every_m * 60 ))`. A whole number (decimal, octal with a leading 0, hexadecimal, a sign,
        blanks) or an empty word gives the same minutes; a name (unbound under set -u), a decimal point, a digit string that is no octal number and an
        expression end the command with status 1 in both, the bash with the shell's message and line, the port with the word. Expressions the shell
        would evaluate (`1+1`) are refused by the port."""
        wait = self.kitchen_core_module('verify_wait')
        st = self.claim_store('arith', {'verdict': 'v'})
        for word in ('5', '0', '010', '0x1F', '-1', '+3', '', '  7 ', '007', '1+1', '2*15', '(2+3)*4', '7/2', '-7%3', '2**3', '1<<2', '3>2', '1?2:3', '1,2', '!0', '~0', '- -1'):
            rc, out, err = self.phase4_bash(st, 'every_m="$1"; set -eu; echo $(( 100 + every_m * 60 ))'.replace('"$1"', repr(word)))
            self.assertEqual(rc, 0, (word, err))
            self.assertEqual(100 + 60 * wait.Waiter.arithmetic(word), int(out), word)
        for word in ('abc', '1.5', '08', 'a b', '1+', '(1', '5%0', '2**-1', '1 2'):
            rc, out, err = self.phase4_bash(st, 'bash -c \'every_m="$1"; set -eu; echo $(( 100 + every_m * 60 ))\' x ' + repr(word))
            self.assertEqual(out, '', word)
            self.assertNotEqual(err, '', word)
            with self.assertRaises(Die) as caught:
                wait.Waiter.arithmetic(word)
            self.assertEqual(caught.exception.code, 1)

    def test_verify_wait_matches_the_bash_at_the_phase_4_base_for_the_wait_itself(self):
        world, V = self.wait_world, self.vverdict
        flip = 'jq \'.verifying.verdict = "/elsewhere/other-v1.md"\' "$FAKE/../st/pair.json" >"$FAKE/../st/pair.tmp" && mv "$FAKE/../st/pair.tmp" "$FAKE/../st/pair.json"\n'
        grace_hook = ('#!/bin/sh\ncase "$2" in Deadline*) printf \'# V\\n\\nstatus: clean\\nunits: 001\\n\\n## Evidence\\n\\n```\\n$ true\\n```\\n\\n```\\nok\\n```\\n\\n\' '
                      '>"$FAKE/../st/verdicts/001-x-v1.md" ;; esac\n')
        past = lambda **kw: dict(verifying={'deadline': int(time.time()) - 10, **kw.pop('verifying', {})}, **kw)
        fallback = {'entry': {'fallback': {'kind': 'codex', 'args': []}}}
        rate = {'v1': 'Error: rate limit exceeded, try later'}
        pi_log = '{{"type":"message","message":{{"role":"assistant","stopReason":"error","errorMessage":"Reached free model rate limit."}}}}\n'
        cases = {
            'a verifier still working': world('0', agents={'v1': 'working claude'}),
            'a verifier still working, a claim in the store': world('0', agents={'v1': 'working claude'}, verifying={'closing': {'token': 'x:1:1'}}),
            'a verifier that is blocked': world('0', agents={'v1': 'blocked claude'}),
            'a verifier that is idle, within its time': world('0', agents={'v1': 'idle claude'}),
            'a verifier gone and no verdict': world(agents={}, panes={}, master=None),
            'a verifier that sits idle past the limit': world(env={'KITCHEN_VERIFY_IDLE_S': '0'}),
            'a verifier idle since long ago': world(verifying={'idle_since': int(time.time()) - 5000}),
            'a stalled verifier with a retried call': world(env={'KITCHEN_VERIFY_IDLE_S': '0'}, verifying={'argv': ['001', '--retry-entry', '{{"kind":"codex"}}', '--covers', '002']}),
            'a verifier past its deadline is prompted for what it proved': world('0', **past()),
            'a devin verifier at its deadline is only waited on': world('0', agents={'v1': 'working devin'}, pair={'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'devin'}}, **past()),
            'a deadline prompt that fails': world('0', promptfail=True, **past()),
            'a grace already started is resumed without a prompt': world('0', **past(verifying={'grace_until': int(time.time()) + 500, 'grace_delivery': 'prompted'})),
            'a grace that has run out': world('5', **past(verifying={'grace_until': int(time.time()) - 5, 'grace_delivery': 'prompted'})),
            'a verifier that writes its verdict in the grace': world('5', hook=grace_hook, **past()),
            'a blocked verifier at its deadline gets no prompt': world('5', agents={'v1': 'blocked claude'}, **past()),
            'a provider error on the screen': world(screens=rate),
            'a provider error with a fallback': world(screens=rate, verifying=fallback),
            'a provider error with the same kind as its fallback': world(screens=rate, verifying={'entry': {'fallback': {'kind': 'claude', 'args': []}}}),
            'a provider error on a retried call': world(screens=rate, verifying={**fallback, 'retried': True}),
            'a provider error with a retry entry among the arguments': world(screens=rate, verifying={'argv': ['001', '--retry-entry', '{{"kind":"codex"}}']}),
            'a provider error in the session log': world(files={'log.jsonl': pi_log}, verifying={'session': '{st}/log.jsonl'}),
            'a verification closed elsewhere while waiting': world('5', agents={'v1': 'working claude'}, once={'get': flip}),
            'a packet never delivered to an idle verifier': world('0', verifying={'delivered': False}),
            'a packet never delivered to a working verifier': world('0', agents={'v1': 'working claude'}, verifying={'delivered': False}),
            'nothing open': world(pair={'verifying': None}),
            'a verifying that is a string': world(pair={'verifying': 'x'}),
            'a verifier that is a string': world(pair={'verifier': 'v1'}),
            'an interval that is empty': world('', agents={'v1': 'working claude'}),
            'outside herdr': world(env={'HERDR_ENV': '0'}),
        }
        expect = {
            'a verifier still working': (4, 'verifying: units 001 by claude, 2m of 32m, verifier working'),
            'a verifier still working, a claim in the store': (4, 'verifying: units 001 by claude'),
            'a verifier that is blocked': (4, 'verifier blocked'),
            'a verifier that is idle, within its time': (4, 'verifier idle'),
            'a verifier gone and no verdict': (4, '(verifier absent); read the packet and verify by hand, or rerun'),
            'a verifier that sits idle past the limit': (4, 'the verifier sat idle 0m with nothing running; rerun: kitchen.sh verify'),
            'a verifier idle since long ago': (4, 'the verifier sat idle 10m with nothing running'),
            'a stalled verifier with a retried call': (4, 'rerun: kitchen.sh verify'),
            'a verifier past its deadline is prompted for what it proved': (4, 'past its deadline; grace Ns left (prompted)'),
            'a devin verifier at its deadline is only waited on': (4, '(skipped:devin-working)'),
            'a deadline prompt that fails': (4, '(skipped:prompt-failed)'),
            'a grace already started is resumed without a prompt': (4, 'past its deadline; grace Ns left (prompted)'),
            'a grace that has run out': (4, 'no verdict at'),
            'a verifier that writes its verdict in the grace': (0, 'status: clean'),
            'a blocked verifier at its deadline gets no prompt': (4, 'no verdict at'),
            'a provider error on the screen': (2, 'provider_error: '),
            'a provider error with a fallback': (1, 'retry: verifying again on the fallback, codex'),
            'a provider error with the same kind as its fallback': (2, 'next: wait for the limit, or kitchen.sh verify'),
            'a provider error on a retried call': (2, 'next: wait for the limit'),
            'a provider error with a retry entry among the arguments': (2, 'next: wait for the limit, or kitchen.sh verify {st} 001 --kind <another kind>'),
            'a provider error in the session log': (2, 'provider_error: Reached free model rate limit.'),
            'a verification closed elsewhere while waiting': (5, 'verification closed elsewhere'),
            'a packet never delivered to an idle verifier': (4, 'redelivered: the verify that started this verifier stopped before sending its packet; sent it again'),
            'a packet never delivered to a working verifier': (4, 'verifying: units 001'),
            'nothing open': (5, 'no verification is open; start one with kitchen.sh verify'),
            'a verifying that is a string': (5, 'no verification is open'),
            'a verifier that is a string': (5, 'Cannot index string with string ("name")'),
            'an interval that is empty': (4, 'verifying: units 001 by claude'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        host = self.live_and_dead_token_hosts()[0]
        out = self.phase4_cases('vwait2', {k: {**v, 'mask': self.wait_mask()} for k, v in cases.items()}, {k: (rc, text.replace('{st}', '<T>/st')) for k, (rc, text) in expect.items()})
        self.assertIn('prompt v1 Deadline reached: write', out['a verifier past its deadline is prompted for what it proved']['calls'])
        self.assertEqual(out['a grace already started is resumed without a prompt']['calls'].count('Deadline reached'), 0)
        self.assertIn('"grace_delivery": "skipped:devin-working"', out['a devin verifier at its deadline is only waited on']['files']['pair.json'])
        self.assertIn('verifier-outcome: provider', out['a provider error on the screen']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verifier-outcome: stalled', out['a verifier that sits idle past the limit']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verifier-outcome: missing', out['a verifier gone and no verdict']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('transcript: ', out['a provider error in the session log']['stdout'])
        self.assertIn('packet: never reached the verifier', out['a provider error in the session log']['stdout'])
        self.assertEqual(out['a verification closed elsewhere while waiting']['calls'].count('pane close'), 0)
        self.assertIn('agent prompt v1 ', out['a packet never delivered to an idle verifier']['calls'])

    def test_set_outcome_verify_prompt_and_handoff_due_equal_the_bash(self):
        st = self.claim_store('twin2', {'verdict': 'v'})
        packets = {'one outcome line': '# P\n\nverifier-outcome: prepared\n\nverdict: x\n', 'two outcome lines': 'verifier-outcome: a\nbody\nverifier-outcome: b\n',
                   'none': '# P\n\nverdict: x\n', 'indented': '  verifier-outcome: no\nverifier-outcome:\n', 'no trailing newline': 'x\nverifier-outcome: y', 'empty': ''}
        for n, (name, text) in enumerate(packets.items()):
            for side in ('bash', 'py'):
                packet = st / f'{side}-{n}.md'
                packet.write_text(text)
                if side == 'bash':
                    self.phase4_bash(st, f'set_outcome {packet} "running (a|b)"')
                else:
                    self.kitchen_py(st, f'vs.set_outcome({str(packet)!r}, "running (a|b)")')
            self.assertEqual((st / f'bash-{n}.md').read_text(), (st / f'py-{n}.md').read_text(), name)
        self.assertEqual(self.phase4_bash(st, 'set_outcome "$STORE/none.md" x; echo "rc=$?"')[1], 'rc=0\n')
        self.kitchen_py(st, 'vs.set_outcome(store + "/none.md", "x")')
        self.assertFalse((st / 'none.md').exists())
        want = self.phase4_bash(st, 'PAIR_SKILL=pstack-kitchen; verify_prompt /s/verdicts/001-x-v1-packet.md')[1]
        got = self.kitchen_py(st, 'print(vs.verify_prompt(ctx, "/s/verdicts/001-x-v1-packet.md"), end="")')[1]
        self.assertEqual(got, want)
        self.assertIn('take the verifier role. pstack-kitchen VERIFY /s/verdicts/001-x-v1-packet.md', got)
        masters = {'no master': None, 'zero': {'units_since_handoff': 0}, 'four': {'units_since_handoff': 4}, 'nine': {'units_since_handoff': 9},
                   'ten': {'units_since_handoff': 10}, 'null': {'units_since_handoff': None}, 'false': {'units_since_handoff': False}, 'a string': 'x',
                   'a string count': {'units_since_handoff': 'a'}, 'a float': {'units_since_handoff': 1.5}, 'an empty master': {}}
        seen = set()
        for n, (name, master) in enumerate(masters.items()):
            for unit, env in (('001-x', {}), ('landing', {}), ('001-x', {'KITCHEN_MASTER_ROTATE_UNITS': '2'}), ('001-x', {'KITCHEN_MASTER_ROTATE_UNITS': '0'}),
                              ('001-x', {'KITCHEN_MASTER_ROTATE_UNITS': 'x'}), ('001-x', {'KITCHEN_MASTER_ROTATE_UNITS': ''})):
                results = []
                for side in ('bash', 'py'):
                    store = self.claim_store(f'ho{n}{side}{unit}{len(env)}{sorted(env.values())}'.replace("'", '').replace('[', '').replace(']', '').replace(' ', ''), {'verdict': 'v'}, {'master': master})
                    if side == 'bash':
                        exports = ''.join(f'export {k}={v!r}; ' for k, v in env.items())
                        rc, out, err = self.phase4_bash(store, f'{exports}( set -e; handoff_due "$STORE" {unit} ); echo "rc=$?"')
                    else:
                        rc, out, err = self.kitchen_py(store, f'vs.handoff_due(store, {unit!r})\nprint("rc=0")', env=env)
                        if rc != 0:
                            out = f'rc={rc}\n'
                    results.append((out.replace(str(store), '<S>').replace('rc=5', 'rc=ERR').replace('rc=1', 'rc=ERR'), err.replace(str(store), '<S>'), (store / 'pair.json').read_text()))
                self.assertEqual(results[0][0], results[1][0], (name, unit, env))
                self.assertEqual(results[0][2], results[1][2], (name, unit, env))
                seen.add('master:' in results[0][0])
        self.assertEqual(seen, {True, False})

    def test_a_close_or_a_wait_that_fails_inside_releases_its_claim_and_leaves_the_store_as_the_bash_does(self):
        """Steer 027-s1. A damaged `KITCHEN_VERIFIER_EXIT_S`, a top-level `.scratch` that is a number (a traceback from the scratch command), a
        `.verifying.idle_since` or `.verifying.started` that bash's arithmetic refuses: the command ends with status 1, the claim of the close is
        released, and the store, the herdr calls and the files are the bash's. Only the shell's own message differs from ours (it names a line of the old
        kitchen.sh; PARITY.md), so stderr is checked for the word and not compared."""
        world, V = self.wait_world, self.vverdict
        cases = {
            'a verifier exit wait of abc': (world(files={'verdicts/001-x-v1.md': V()}, env={'KITCHEN_VERIFIER_EXIT_S': 'abc'}), 'abc'),
            'a verifier exit wait of 1.5': (world(files={'verdicts/001-x-v1.md': V()}, env={'KITCHEN_VERIFIER_EXIT_S': '1.5'}), '1.5'),
            'a top-level scratch that is a number, at a close': (world(files={'verdicts/001-x-v1.md': V()}, pair={'scratch': 5}), 'Traceback'),
            'an idle_since of abc': (world(verifying={'idle_since': 'abc'}), 'abc'),
            'an idle_since of 1.5': (world(verifying={'idle_since': 1.5}), '1.5'),
            'an idle_since of true': (world(verifying={'idle_since': True}), 'true'),
            'a started of abc': (world('0', agents={'v1': 'working claude'}, verifying={'started': 'abc'}), 'abc'),
            'a started of 1.5': (world('0', agents={'v1': 'working claude'}, verifying={'started': 1.5}), '1.5'),
            'a started of true': (world('0', agents={'v1': 'working claude'}, verifying={'started': True}), 'true'),
        }
        cancel = {
            'a cancel with a verifier exit wait of abc': (self.verify_world(env={'KITCHEN_VERIFIER_EXIT_S': 'abc'}), 'abc'),
            'a cancel with a top-level scratch that is a number': (self.verify_world(pair={'scratch': 5}), 'Traceback'),
        }
        for n, (name, (spec, word)) in enumerate({**cases, **cancel}.items()):
            got, want = self.phase4_ab(f'vfail{n}', spec)
            self.assertEqual({k: v for k, v in got.items() if k != 'stderr'}, {k: v for k, v in want.items() if k != 'stderr'}, name)
            self.assertEqual(got['rc'], 1, (name, got['stderr']))
            self.assertIn(word, want['stderr'], name)
            self.assertIn(word, got['stderr'], name)
            if 'exit wait' in name or 'scratch' in name:
                self.assertNotIn('"closing"', got['files']['pair.json'], name)
                self.assertIn('"verifying"', got['files']['pair.json'], name)
            if 'idle_since' in name:
                self.assertNotIn('pane close', got['calls'], name)

    def test_the_verify_helpers_equal_the_bash_at_the_phase_4_base(self):
        """verifier_identity (printf %q included), fence_for, names_path, next_index and verifier_args against b632713's functions."""
        st = self.claim_store('vhelp', {'verdict': 'v'})
        words = ['--model', 'opus', 'a b', 'it\'s', '', 'tab\there', 'new\nline', 'é', '~x', '#x', 'x#', 'a=b', 'a,b', '{x}', '*', '!x', '--mcp-config', 'f.json',
                 '--permission-mode', 'auto', '-a', 'never', '--approve', '--sandbox=read-only', '$HOME', '"q"', '\\', 'a;b', 'a&b', '<x>', '%s', '\x7f', '\x1b[0m']
        lists = [[], words[:2], words[2:12], words[12:22], words[22:], words, ['--model', 'x', '--permission-mode', 'y', '--approve', '--yolo']]
        script = ''
        code = ''
        for n, args in enumerate(lists):
            quoted = ' '.join(shlex.quote(a) for a in args)
            script += f'verifier_identity claude {quoted}; verifier_identity pi {quoted}\n'
            code += f'print(verify.verifier_identity("claude", {args!r}))\nprint(verify.verifier_identity("pi", {args!r}))\n'
        rc, want, err = self.phase4_bash(st, script)
        prc, got, perr = self.kitchen_py(st, code)
        self.assertEqual((got, perr), (want, err))
        self.assertIn("a\\ b", got)
        texts = ['', 'plain', 'with ``` fence', 'a ````` longer run', '`one`', '``two``', 'a\nb```c\n````']
        script = ''.join(f'printf "%s\\n" "$(fence_for {shlex.quote(t)})"\n' for t in texts)
        want = self.phase4_bash(st, script)[1]
        got = self.kitchen_py(st, ''.join(f'print(verify.fence_for({t!r}))\n' for t in texts))[1]
        self.assertEqual(got, want)
        paths = ['/work/repo', '/tmp/x y', '/a.b', '/tmp/store']
        commands = ['cd /work/repo && ls', 'cat /work/repo/x', 'cat /work/repo2/x', 'cat "/work/repo"', "cat '/work/repo'", 'x/work/repo', 'cat ~/work/repo', 'cat $HOME/w',
                    'cat ${HOME}/w', 'cat "$HOME/w"', 'cat /tmp/x y/z', 'echo /a.b;', 'echo /aXb', 'echo /work/repo|wc', 'echo /work/repo&', 'echo=/work/repo', 'a:/work/repo',
                    '(/work/repo)', 'multi\nline /work/repo\nend', '/tmp/store', 'a /tmp/store/briefs/x.md']
        script = ''.join(f'if names_path {shlex.quote(c)} {shlex.quote(p)}; then echo yes; else echo no; fi\n' for c in commands for p in paths)
        code = ''.join(f'print("yes" if verify.names_path({c!r}, {p!r}) else "no")\n' for c in commands for p in paths)
        env = {'HOME': '/work'}
        want = subprocess.run(['bash', '-c', f'. {shlex.quote(str(self.phase4_skills() / "pstack-kitchen/scripts/kitchen-lib.sh"))}\nset +e\n{script}'], env={**self.env, 'HOME': '/work'},
                              capture_output=True, text=True, check=False).stdout
        got = self.kitchen_py(st, code, env=env)[1]
        self.assertEqual(got, want)
        self.assertGreater(want.count('yes'), 5)
        self.assertGreater(want.count('no'), 5)
        directory = self.path / 'vidx'
        directory.mkdir()
        for name in ('x-v1.md', 'x-v1-packet.md', 'x-v7.md', 'x-v10.md', 'x-vx.md', 'x-v02.md', 'y-v3.md', 'landing-v4.md', 'landing-v12-packet.md'):
            (directory / name).write_text('')
        script = ''.join(f'next_index {directory} {prefix} v\n' for prefix in ('x', 'y', 'z', 'landing', 'x-v1'))
        got = self.kitchen_py(st, ''.join(f'print(verify.next_index({str(directory)!r}, {prefix!r}, "v"))\n' for prefix in ('x', 'y', 'z', 'landing', 'x-v1')))[1]
        self.assertEqual(got, self.phase4_bash(st, script)[1])
        entries = ['', '{"kind":"claude","args":["--model","m"]}', '{"kind":"pi","args":["--approve"]}', '{"kind":"codex","args":["-a","never"]}',
                   '{"kind":"claude","args":["--permission-mode","plan","--strict-mcp-config"]}', '{"kind":"devin","args":[]}', '{"kind":"muse","args":["a\nb",""]}']
        script = ''.join(f'echo "== {kind}"; verifier_args {kind} {shlex.quote(e)} /tmp\n' for kind in ('claude', 'pi', 'codex', 'devin', 'muse', 'prime-agent') for e in entries)
        code = ''.join(f'print("== {kind}")\nfor line in verify.verifier_args({kind!r}, {e!r}, "/tmp"): print(line)\n' for kind in ('claude', 'pi', 'codex', 'devin', 'muse', 'prime-agent') for e in entries)
        want = self.phase4_bash(st, script, cwd='/tmp')[1]
        got = self.kitchen_py(st, code, env={'PAIR_SHELL_PID': str(os.getpid())}, cwd='/tmp')[1]
        trees = lambda text: text.replace(str(self.phase4_skills()), '<SK>').replace(str(root / 'skills'), '<SK>')
        self.assertEqual(trees(got), trees(want))
        self.assertIn('--permission-mode', want)

    def test_verify_kpy_calls_leave_the_environment_alone(self):
        """verify reaches kitchen.py in process (kpy.run). The commands it makes (roster, snapshot, classify, timing, brief-verify --list, lesson-packet)
        write nothing to os.environ, and INVOCATION, which kitchen.py keeps in process, is read only by `gate` and by `brief-verify` without --list
        (kitchen.py :686 and :1043), neither of which verify runs."""
        kpy = self.kitchen_core_module('kpy')
        source = (root / 'skills/pstack-kitchen/scripts/kitchen.py').read_text().splitlines()
        reads = [n for n, line in enumerate(source, start=1) if 'INVOCATION' in line and 'INVOCATION[:]' not in line and 'INVOCATION: list' not in line]
        self.assertEqual(len(reads), 2, [source[n - 1] for n in reads])
        for n in reads:
            enclosing = next(source[m - 1] for m in range(n, 0, -1) if source[m - 1].startswith('def '))
            self.assertRegex(enclosing, r'def (run_wrapped|run_brief_verify)\(')  # the gate's wrapper and the brief's Verify commands, run by `gate` and `brief-verify`
        top = self.path / 'kpyenv'
        repo = top / 'repo'
        (repo / '.agents').mkdir(parents=True)
        (repo / '.agents/kitchen.toml').write_text(self.LAUNCH_TOML)
        (repo / 'src').mkdir()
        (repo / 'src/a.py').write_text('a = 1\n')
        env = {**self.env, **self.GIT}
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-q', '-m', 'c1']):
            subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True)
        brief = top / 'brief.md'
        brief.write_text(self.launch_brief('001', 'x'))
        (top / 'brief2.md').write_text(self.launch_brief('002', 'y'))
        before = dict(os.environ)
        with mock.patch.dict(os.environ, {'HOME': str(top), 'XDG_STATE_HOME': str(top / 'state')}):
            snapshot = dict(os.environ)
            calls = [(None, ['--json', 'roster']), (str(repo), ['snapshot', str(top / 'kitchens')]), (str(repo), ['--at', 'HEAD', '--json', 'classify', '--base', 'HEAD', '--head', 'HEAD']),
                     (str(repo), ['--json', 'timing', '--deadline', '--stage', 'behavioral', 'app']), (str(repo), ['timing', '--deadline', '--stage', 'behavioral', 'app']),
                     (str(repo), ['--json', 'timing', '--stage', 'landing', 'app']), (str(repo), ['--json', 'brief-verify', str(brief), '--list']),
                     (None, ['lesson-packet', str(brief), str(top / 'brief2.md')])]
            statuses = []
            for repo_arg, args in calls:
                statuses.append(kpy.run(repo_arg, args)[0])
                self.assertEqual(dict(os.environ), snapshot, args)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(statuses.count(0), len(calls), statuses)

    def test_record_skill_build_equals_the_bash(self):
        """A later skills directory is stamped on a stamped run before any command: the bash function at b632713 and kitchen_core.setup.record_skill_build
        run on the same pair.json files: the document, the stderr and the status."""
        documents = {'no builds': {'master': {'name': 'm'}}, 'an empty list': {'skill_builds': []}, 'the same path last': {'skill_builds': [{'path': '/skills', 'at': 't'}]},
                     'another path last': {'skill_builds': [{'path': '/old', 'at': 't'}]}, 'two rows': {'skill_builds': [{'path': '/skills', 'at': 't'}, {'path': '/old', 'at': 't'}]},
                     'a string': {'skill_builds': 'x'}, 'an object': {'skill_builds': {}}, 'a number': {'skill_builds': 3}, 'null': {'skill_builds': None}, 'false': {'skill_builds': False},
                     'rows that are strings': {'skill_builds': ['a']}}
        for n, (name, doc) in enumerate(documents.items()):
            ways = []
            for how in ('bash', 'python'):
                st = self.path / f'rsb{n}{how}'
                st.mkdir()
                (st / 'pair.json').write_text(json.dumps(doc, indent=2) + '\n')
                if how == 'bash':
                    rc, out, err = self.phase4_bash(st, 'skills_dir=/skills\nrecord_skill_build "$STORE"; echo "rc=$?"')
                else:
                    class Ctx:
                        skill_root = '/skills/pstack-kitchen'
                    code = ('import sys\nfrom kitchen_core import setup\nfrom pstack_core.errors import Exit\ntry:\n    setup.record_skill_build(Ctx, store)\n    code = 0\n'
                            'except Exit as e:\n    sys.stderr.write(e.stderr)\n    code = e.code\nprint("rc=%d" % code)')
                    rc, out, err = self.kitchen_py(st, 'class Ctx:\n    skill_root = "/skills/pstack-kitchen"\n' + code)
                text = (st / 'pair.json').read_text()
                ways.append((out, re.sub(r'\S*pair\.json', 'pair.json', err), re.sub(r'"at": "[0-9TZ:-]+"', '"at": "<TS>"', text)))
            self.assertEqual(ways[1], ways[0], name)
        self.assertIn('"/skills"', ways[0][2])

    def record_skill_build_cases(self):
        stamped = {'skill_builds': [{'path': '/elsewhere', 'at': '2020-01-01T00:00:00Z'}]}
        return {
            'a stamped run from another build': dict(args=['status', '{st}'], pair=stamped),
            'a run that was never stamped': dict(args=['status', '{st}']),
            'a stamped run, the command being help': dict(args=['help', '{st}'], pair=stamped),
            'a stamped run, the command being unknown': dict(args=['nonsense', '{st}'], pair=stamped),
            'a pair.json that does not parse': dict(args=['status', '{st}'], nopair=True, files={'pair.json': '{{\n'}),
        }

    def test_every_kitchen_command_records_the_skills_build_like_the_bash(self):
        cases = self.record_skill_build_cases()
        expect = {
            'a stamped run from another build': (0, '# Kitchen status'),
            'a run that was never stamped': (0, '# Kitchen status'),
            'a stamped run, the command being help': (0, 'usage: kitchen.sh'),
            'a stamped run, the command being unknown': (1, 'nonsense'),
            'a pair.json that does not parse': (1, 'parse error'),
        }
        out = self.phase4_cases('rsb', cases, expect)
        self.assertRegex(out['a stamped run from another build']['files']['pair.json'], r'"path": "<SK>"')
        self.assertNotIn('<SK>', out['a run that was never stamped']['files']['pair.json'])

    def test_brief_kitchen_equals_the_bash(self):
        st = self.claim_store('bk', {'verdict': 'v'})
        for n, text in enumerate(('', 'kitchen: repo', 'kitchen: /tmp/snap.toml', 'kitchen:   /tmp/spaced.toml  ', 'risk: routine')):
            brief = self.path / f'kb{n}.md'
            brief.write_text('# B\n\n' + text + '\n')
            rc, want, err = self.phase4_bash(st, f'brief_kitchen {brief}; echo "[${{kitchen_cfg[*]-}}] ${{#kitchen_cfg[@]}}"')
            got = self.kitchen_py(st, f'from kitchen_core import classify\ncfg = classify.brief_kitchen({str(brief)!r})\nprint("[%s] %d" % (" ".join(cfg), len(cfg)))')[1]
            self.assertEqual(got, want, text)

    def test_kitchen_snapshot_equals_the_bash(self):
        st = self.claim_store('ksnap', {'verdict': 'v'})
        repo = self.path / 'ksnap-repo'
        (repo / '.agents').mkdir(parents=True)
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True, capture_output=True, env={**self.env, **self.GIT})
        (st / 'pair.json').write_text(json.dumps({'git_root': str(repo)}) + '\n')
        for good in (True, False):
            (repo / '.agents/kitchen.toml').write_text(self.LAUNCH_TOML if good else 'version = 1\n[profile.app]\npaths = 3\n')
            rc, want, err = self.phase4_bash(st, '( set -e; x="$(kitchen_snapshot "$STORE")"; echo "[$x]" ); echo "rc=$?"', cwd=repo)
            code = ('import sys\nfrom pstack_core.errors import Die\nfrom kitchen_core import classify\ntry:\n    print("[%s]" % classify.kitchen_snapshot(store))\n    code = 0\n'
                    'except Die as e:\n    sys.stderr.write("pair.sh: %s\\n" % e.message)\n    code = e.code\nprint("rc=%d" % code)')
            prc, got, perr = self.kitchen_py(st, code, cwd=repo)
            self.assertEqual((got, perr.replace(str(repo), '<R>')), (want, err.replace('kitchen.sh: ', 'pair.sh: ').replace(str(repo), '<R>')), good)
            self.assertIn('rc=0' if good else 'rc=1', want)

    READY_PLAN_HOOK = ('#!/bin/sh\ncase "$2" in *" PLAN "*) ;; *) exit 0 ;; esac\ncase "$1" in\n'
                       's1) printf "# R\\n\\nstatus: agree\\n" >"$FAKE/../st/reports/001-x.md" ;;\n'
                       'c1) printf "# A\\n\\nstatus: agree\\n" >"$FAKE/../st/advice/001-x.md" ;;\nesac\n')

    ROTATE_PLAN_HOOK = READY_HOOK.replace('exit 0\n', '') + READY_PLAN_HOOK.split('\n', 1)[1]

    def discuss_cases(self):
        plan = lambda risk='escalated': f'# Plan 001-x\n\nrisk: {risk}\n'
        consultant = {'consultant': {'name': 'c1', 'kind': 'codex', 'pane_id': 'p2'}}
        env = {'PAIR_FILE_POLL_MS': '1'}
        old = {'plans/001-x.md': 1000}
        base = dict(args=['discuss', '{st}', '{st}/plans/001-x.md', '--timeout', '3'], files={'plans/001-x.md': plan()}, mtimes=old,
                    agents={'m1': 'idle claude', 's1': 'idle claude', 'c1': 'idle codex'}, pair=consultant, env=env, hook=self.READY_PLAN_HOOK)
        routine = dict(base, files={'plans/001-x.md': plan('routine')}, agents={'s1': 'idle claude'}, pair={})
        with_args = lambda spec, *extra: dict(spec, args=['discuss', '{st}', '{st}/plans/001-x.md', *extra])
        answered = {'plans/001-x.md': plan(), 'reports/001-x.md': '# R\n\nstatus: agree\n', 'advice/001-x.md': '# A\n\nstatus: agree\n'}
        due = self.rotation_world('pi')
        rotating = lambda **kw: {**due, 'args': base['args'], 'files': {'plans/001-x.md': plan('routine')}, 'mtimes': old, **kw}
        return {
            'a routine plan answered by a hook': dict(routine, hook=self.READY_PLAN_HOOK, report_after='1 {st}/reports/001-x.md agree'),
            'a routine plan whose sidekick is working': dict(routine, agents={'s1': 'working claude'}),
            'a routine plan with the sidekick absent': dict(routine, agents={}),
            'a routine plan with the sidekick blocked': dict(routine, agents={'s1': 'blocked claude'}),
            'a routine plan in a paused store': dict(routine, files={'plans/001-x.md': plan('routine'), 'paused': 'lunch\n'}),
            'a routine plan with a placeholder': dict(routine, files={'plans/001-x.md': plan('routine') + '\n{{{{path}}}}\n'}),
            'a plan that is not there': dict(base, args=['discuss', '{st}', '{st}/plans/009-none.md']),
            'a plan with no risk': dict(base, files={'plans/001-x.md': '# Plan 001-x\n'}),
            'a plan whose risk is neither class': dict(base, files={'plans/001-x.md': plan('maybe')}),
            'a plan whose risk keeps its reason': dict(base, files={'plans/001-x.md': plan('escalated (a contract)')}),
            'too few arguments': dict(base, args=['discuss', '{st}']),
            'an escalated plan with no consultant': dict(base, pair={}, agents={'m1': 'idle claude', 's1': 'idle claude'}),
            'an escalated plan with a consultant that is a string': dict(base, pair={'consultant': 'c1'}),
            'an escalated plan both roles answer': base,
            'an escalated plan answered before the call': dict(base, files=answered, mtimes=old, hook=None),
            'a store given with a trailing slash, answered': dict(base, args=['discuss', '{st}/', '{st}/plans/001-x.md', '--timeout', '3'], files=answered, mtimes=old, hook=None),
            'a store given with a trailing slash, answered by a hook': dict(base, args=['discuss', '{st}/', '{st}/plans/001-x.md', '--timeout', '3']),
            'a risk word followed by a no-break space': dict(base, files={'plans/001-x.md': '# Plan 001-x\n\nrisk: escalated\u00a0(x)\n'}),
            'a risk word followed by a vertical tab': dict(base, files={'plans/001-x.md': '# Plan 001-x\n\nrisk: escalated\x0b(x)\n'}),
            'a risk word followed by a form feed': dict(base, files={'plans/001-x.md': '# Plan 001-x\n\nrisk: routine\x0c(x)\n'}),
            'a risk word followed by tabs': dict(base, files={'plans/001-x.md': '# Plan 001-x\n\nrisk:\t\t escalated \t(x)\n'}),
            'an escalated plan whose consultant answered before': dict(base, files={'plans/001-x.md': plan(), 'advice/001-x.md': '# A\n\nstatus: agree\n'}),
            'an escalated plan whose sidekick answered before': dict(base, files={'plans/001-x.md': plan(), 'reports/001-x.md': '# R\n\nstatus: agree\n'}),
            'an escalated plan whose consultant only left a stop': dict(base, files={'plans/001-x.md': plan(), 'advice/001-x.md': '# A\n\nkind: stop\n'}),
            'a stop named for it with no kind line': dict(base, files={'plans/001-x.md': plan(), 'advice/001-x-stop.md': '# A\n'}),
            'a stop and an answer': dict(base, files={'plans/001-x.md': plan(), 'advice/001-x.md': '# A\n\nstatus: agree\n', 'advice/001-x-late.md': '# A\n\nkind: stop\n'}, hook=None),
            'a sidekick at work, the consultant idle': dict(base, agents={'m1': 'idle claude', 's1': 'working claude', 'c1': 'idle codex'}),
            'a sidekick at work, the consultant working': dict(base, agents={'m1': 'idle claude', 's1': 'working claude', 'c1': 'working codex'}),
            'a sidekick at work, the consultant answered': dict(base, agents={'m1': 'idle claude', 's1': 'working claude', 'c1': 'idle codex'}, files={'plans/001-x.md': plan(), 'advice/001-x.md': '# A\n\nstatus: agree\n'}),
            'the sidekick working, the consultant gone': dict(base, agents={'m1': 'idle claude', 's1': 'working claude'}),
            'a sidekick that is gone': dict(base, agents={'m1': 'idle claude', 'c1': 'idle codex'}),
            'a sidekick that is blocked': dict(base, agents={'m1': 'idle claude', 's1': 'blocked claude', 'c1': 'idle codex'}),
            'a consultant that is blocked': dict(base, agents={'m1': 'idle claude', 's1': 'idle claude', 'c1': 'blocked codex'}),
            'a consultant that is working': dict(base, agents={'m1': 'idle claude', 's1': 'idle claude', 'c1': 'working codex'}),
            'nobody answers': dict(base, hook=None),
            'only the consultant answers': dict(base, hook=self.READY_PLAN_HOOK.replace('s1)', 'zz)')),
            'a role that blocks after the prompt': dict(base, once={'wait': 'printf "blocked claude\\n" >"$FAKE/agents/s1"\n'}, hook=None),
            'a prompt the first time stalls': dict(base, fakefiles={'prompt-drop': ''}),
            'a prompt that fails': dict(base, promptfail=True),
            'a prompt that fails with another code': dict(base, prompt_error='{"error":{"code":"boom"}}\n'),
            'a prompt that times out': dict(base, prompt_error='{"error":{"code":"timeout"}}\n'),
            'a prompt whose error is no json': dict(base, prompt_error='broken\n'),
            '--every': with_args(base, '--every', '1'),
            '--every with an expression': with_args(base, '--every', '1+1'),
            '--timeout with no value': dict(with_args(base, '--timeout'), mask=self.SETUP_MASK),
            '--every with no value': dict(with_args(base, '--every'), mask=self.SETUP_MASK),
            '--every with a name': dict(with_args(base, '--every', 'abc'), mask=self.SETUP_MASK),
            '--timeout that is no number': dict(with_args(base, '--timeout', 'abc'), mask=self.SETUP_MASK),
            '--timeout that is no number with nobody answering': dict(with_args(base, '--timeout', 'abc'), mask=self.SETUP_MASK, hook=None),
            '--timeout with an unknown option after': with_args(base, '--timeout', '3', '--bogus'),
            'an unknown option': with_args(base, '--bogus'),
            'outside herdr': dict(base, env={**env, 'HERDR_ENV': '0'}),
            'a pair.json that is an array': dict(base, nopair=True, files={'plans/001-x.md': plan(), 'pair.json': '[]\n'}),
            'no pair.json': dict(base, nopair=True),
            'a sidekick that is a string': dict(base, pair={**consultant, 'sidekick': 's1'}),
            'a rotation that succeeds before a routine plan': rotating(hook=self.ROTATE_PLAN_HOOK),
            'a rotation flag that is the string true': rotating(pair={**due['pair'], 'sidekick': {**due['pair']['sidekick'], 'rotation_required': 'true'}}),
            'a rotation that is not due': rotating(pair={**due['pair'], 'sidekick': {**due['pair']['sidekick'], 'rotation_required': False}}),
            'a rotation whose exit prompt fails': rotating(prompt_error='{"error":{"code":"boom"}}\n'),
            'a rotation that does not exit in time': rotating(prompt_error='{"error":{"code":"not_found"}}\n', clock=True),
            'a rotation whose spawn returns 4': rotating(hook=None),
            'a rotation in a paused store': rotating(files={'plans/001-x.md': plan('routine'), 'paused': 'lunch\n'}),
            'a rotation before an escalated plan': dict(due, args=base['args'], files={'plans/001-x.md': plan()}, mtimes=old, agents={'s1': 'idle pi', 'c1': 'idle codex'},
                                                       pair={**due['pair'], **consultant}, hook=self.ROTATE_PLAN_HOOK, env={**due['env'], **env}),
            'a rotation that leaves the plan unanswered': rotating(),
            'a sidekick that is a string with a consultant due': dict(base, pair={**consultant, 'sidekick': 'pi'}, files={'plans/001-x.md': plan('routine')}),
            'a prime-agent rotation': dict(self.rotation_world('prime-agent'), args=base['args'], files={'plans/001-x.md': plan('routine')}, mtimes=old),
        }

    def test_discuss_matches_the_bash_at_the_phase_4_base(self):
        cases = self.discuss_cases()
        answers = 'sidekick_response: <T>/st/reports/001-x.md\nsidekick_status: agree\nconsultant_state: idle\nconsultant_response: <T>/st/advice/001-x.md'
        expect = {
            'a routine plan answered by a hook': (0, 'state: idle\nreport: <T>/st/reports/001-x.md\nreport_status: agree\n'),
            'a routine plan whose sidekick is working': (5, 'sidekick s1 is working; run: pair.sh wait'),
            'a routine plan with the sidekick absent': (2, 'sidekick s1 is not live; run: pair.sh spawn'),
            'a routine plan with the sidekick blocked': (3, 'sidekick s1 is blocked; inspect: herdr agent read s1'),
            'a routine plan in a paused store': (5, 'the store is paused (lunch)'),
            'a routine plan with a placeholder': (1, 'still has unfilled placeholders'),
            'a plan that is not there': (1, 'plan not found: <T>/st/plans/009-none.md'),
            'a plan with no risk': (2, 'risk: must start with routine or escalated, as kitchen.sh classify prints it'),
            'a plan whose risk is neither class': (2, 'risk: must start with routine or escalated, as kitchen.sh classify prints it'),
            'a plan whose risk keeps its reason': (0, 'sent PLAN 001-x.md to consultant c1\n'),
            'too few arguments': (1, 'usage: kitchen.sh discuss <store> <plan-path> [--timeout MS]'),
            'an escalated plan with no consultant': (5, 'an escalated plan goes to the consultant too; start it: kitchen.sh consultant <T>/st --reason TEXT'),
            'an escalated plan with a consultant that is a string': (5, 'Cannot index string with string ("name")'),
            'an escalated plan both roles answer': (0, 'sent PLAN 001-x.md to sidekick s1\nsent PLAN 001-x.md to consultant c1\nsidekick_state: idle\n' + answers),
            'a store given with a trailing slash, answered': (0, 'consultant_response: <T>/st//advice/001-x.md'),
            'a store given with a trailing slash, answered by a hook': (0, 'consultant_response: <T>/st//advice/001-x.md'),
            'a risk word followed by a no-break space': (2, 'risk: must start with routine or escalated'),
            'a risk word followed by a vertical tab': (2, 'risk: must start with routine or escalated'),
            'a risk word followed by a form feed': (2, 'risk: must start with routine or escalated'),
            'a risk word followed by tabs': (0, 'consultant_status: agree'),
            'an escalated plan answered before the call': (0, 'sidekick_state: answered\nsidekick_response: <T>/st/reports/001-x.md\nsidekick_status: agree\nconsultant_state: answered\n'),
            'an escalated plan whose consultant answered before': (0, 'sent PLAN 001-x.md to sidekick s1\nsidekick_state: idle\n'),
            'an escalated plan whose sidekick answered before': (0, 'sent PLAN 001-x.md to consultant c1\nsidekick_state: answered\n'),
            'an escalated plan whose consultant only left a stop': (0, 'consultant_response: <T>/st/advice/001-x.md\nconsultant_status: agree\n'),
            'a stop named for it with no kind line': (0, 'consultant_response: <T>/st/advice/001-x.md\nconsultant_status: agree\n'),
            'a stop and an answer': (4, 'consultant_state: answered\nconsultant_response: <T>/st/advice/001-x.md\nconsultant_status: agree'),
            'a sidekick at work, the consultant idle': (4, 'sidekick_response: deferred (it is working on a unit); run discuss again once it reports, to send it this plan\n'),
            'a sidekick at work, the consultant working': (5, 'consultant c1 is working; wait for it before a plan round'),
            'a sidekick at work, the consultant answered': (4, 'consultant_state: answered\n'),
            'the sidekick working, the consultant gone': (5, 'an escalated plan goes to the consultant too'),
            'a sidekick that is gone': (2, 'sidekick s1 is not live; run: pair.sh spawn <T>/st --only sidekick --sidekick <kind>'),
            'a sidekick that is blocked': (3, 'sidekick s1 is blocked'),
            'a consultant that is blocked': (3, 'consultant c1 is blocked'),
            'a consultant that is working': (5, 'consultant c1 is working; wait for it before a plan round'),
            'nobody answers': (4, 'redelivered: consultant c1 sat idle without the plan; sent it again\n'),
            'only the consultant answers': (4, 'sidekick settled without a response; polling for the file\nsidekick_state: idle\nsidekick_response: missing\n'),
            'a role that blocks after the prompt': (4, 'sidekick_state: blocked\nsidekick_response: missing\n'),
            'a prompt the first time stalls': (0, 'redelivered: sidekick s1 sat idle without the plan; sent it again\n'),
            'a prompt that fails': (2, 'herdr prompt failed for s1'),
            'a prompt that fails with another code': (2, 'herdr prompt failed for s1'),
            'a prompt that times out': (4, 'sidekick settled without a response; polling for the file'),
            'a prompt whose error is no json': (2, 'broken\n'),
            '--every': (0, 'consultant_status: agree\n'),
            '--every with an expression': (0, 'consultant_status: agree\n'),
            '--timeout with no value': (1, '$2: unbound variable'),
            '--every with no value': (1, '$2: unbound variable'),
            '--every with a name': (1, 'abc: unbound variable'),
            '--timeout that is no number': (0, 'consultant_status: agree\n'),
            '--timeout that is no number with nobody answering': (4, 'settled without a response; polling for the file'),
            '--timeout with an unknown option after': (1, 'unknown option --bogus'),
            'an unknown option': (1, 'unknown option --bogus'),
            'outside herdr': (1, 'not running inside Herdr (HERDR_ENV != 1)'),
            'a pair.json that is an array': (5, 'Cannot index array with string ("consultant")'),
            'no pair.json': (5, 'no pair.json in <T>/st; run: pair.sh init <slug>'),
            'a sidekick that is a string': (5, 'Cannot index string with string ("name")'),
            'a rotation that succeeds before a routine plan': (0, 'rotated: the pi sidekick starts this plan in a fresh session\nstate: idle\nreport: <T>/st/reports/001-x.md\n'),
            'a rotation flag that is the string true': (4, 'rotated: the pi sidekick starts this plan in a fresh session\n'),
            'a rotation that is not due': (4, 'state: idle\nreport: missing\n'),
            'a rotation whose exit prompt fails': (2, 'rotating the sidekick before this plan failed:\npair.sh: pi exit failed'),
            'a rotation that does not exit in time': (5, 'rotating the sidekick before this plan failed:\npair.sh: pi has not exited; inspect s1 before retrying rotate'),
            'a rotation whose spawn returns 4': (4, 'rotating the sidekick before this plan failed:\nreused pane p1\n'),
            'a rotation in a paused store': (5, 'rotating the sidekick before this plan failed:\npair.sh: the store is paused (lunch)'),
            'a rotation before an escalated plan': (0, 'rotated: the pi sidekick starts this plan in a fresh session\nsent PLAN 001-x.md to sidekick s1\n'),
            'a rotation that leaves the plan unanswered': (4, 'rotated: the pi sidekick starts this plan in a fresh session\n'),
            'a sidekick that is a string with a consultant due': (5, 'Cannot index string with string ("kind")'),
            'a prime-agent rotation': (4, 'rotated: the prime-agent sidekick starts this plan in a fresh session\n'),
        }
        self.phase4_cases('disc', cases, expect)

    def queue_cases(self):
        world = self.launch_world
        queue = lambda *a, **kw: world(args=['queue', '{st}', *a], agents={'m1': 'idle claude', 's1': 'working claude'}, **kw)
        path = '{st}/briefs/002-y.md'
        return {
            'a brief queued behind a working sidekick': queue(path),
            'a brief given relative to the store': queue('briefs/002-y.md'),
            'a replace': queue(path, '--replace', files={'queue': '{st}/briefs/001-x.md\n'}),
            'another brief already queued': queue(path, files={'queue': '{st}/briefs/001-x.md\n'}),
            'the same brief already queued': queue(path, files={'queue': '{st}/briefs/002-y.md\n'}),
            'the queue cleared': queue('--clear', files={'queue': '{st}/briefs/001-x.md\n'}),
            'a clear with nothing queued': queue('--clear'),
            'a clear with an extra word': queue('--clear', 'x'),
            'an idle sidekick': world(args=['queue', '{st}', path], agents={'m1': 'idle claude', 's1': 'idle claude'}),
            'a blocked sidekick': world(args=['queue', '{st}', path], agents={'m1': 'idle claude', 's1': 'blocked claude'}),
            'an absent sidekick': world(args=['queue', '{st}', path], agents={'m1': 'idle claude'}),
            'a landing brief': queue(path, files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('playbook: feature', 'playbook: shipping')}),
            'a landing brief marked by landing: yes': queue(path, files={'briefs/002-y.md': self.launch_brief('002', 'y', 'landing: yes\n')}),
            'a brief with an empty Scope': queue(path, files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('- src/**\n', '')}),
            'a brief that escalates': queue(path, files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('risk: routine', 'risk: escalated')}),
            'a brief that changes class': queue(path, files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('- src/**\n', '- contracts/**\n')}),
            'a brief outside a kitchen': queue(path, toml=None, mask=[(r'repo-[0-9a-f]{8}', 'repo-H')]),
            'a kitchen that does not parse': queue(path, toml='version = 1\n[profile.app]\npaths = 3\n'),
            'a brief that is not there': queue('{st}/briefs/009-none.md'),
            'an unknown option': queue(path, '--bogus'),
            'a paused store': queue(path, files={'paused': 'lunch\n'}),
            'a brief with a placeholder': queue(path, files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('Do y.', '{{{{path}}}}')}),
            'too few arguments': world(args=['queue', '{st}']),
            'no pair.json': queue(path, nopair=True),
            'a sidekick that is a string': queue(path, pair={'sidekick': 's1'}),
            'an escalated brief and no pair.json': queue(path, nopair=True, files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('risk: routine', 'risk: escalated')}),
        }

    def test_queue_matches_the_bash_at_the_phase_4_base(self):
        cases = self.queue_cases()
        queued = 'queued <T>/st/briefs/002-y.md; the sidekick takes it when its current report is written\n'
        expect = {
            'a brief queued behind a working sidekick': (0, queued),
            'a brief given relative to the store': (0, queued),
            'a replace': (0, queued),
            'another brief already queued': (5, 'the queue holds <T>/st/briefs/001-x.md; pass --replace to swap it'),
            'the same brief already queued': (0, queued),
            'the queue cleared': (0, 'queue cleared\n'),
            'a clear with nothing queued': (0, 'queue cleared\n'),
            'a clear with an extra word': (0, 'queue cleared\n'),
            'an idle sidekick': (5, 'sidekick s1 is idle; dispatch instead: pair.sh dispatch'),
            'a blocked sidekick': (3, 'sidekick s1 is blocked; resolve that before queueing'),
            'an absent sidekick': (3, 'sidekick s1 is absent; resolve that before queueing'),
            'a landing brief': (0, queued),
            'a landing brief marked by landing: yes': (0, queued),
            'a brief with an empty Scope': (1, "lists nothing under Scope's may write:"),
            'a brief that escalates': (6, 'playbook feature needs an agreed plan; brief has plan: none'),
            'a brief that changes class': (6, 'playbook feature needs an agreed plan; brief has plan: none'),
            'a brief outside a kitchen': (1, 'kitchen.py snapshot failed; run kitchen.py validate in the repo'),
            'a kitchen that does not parse': (1, 'kitchen.py snapshot failed; run kitchen.py validate in the repo'),
            'a brief that is not there': (1, 'brief not found: <T>/st/briefs/009-none.md'),
            'an unknown option': (1, 'unknown option --bogus'),
            'a paused store': (5, 'the store is paused (lunch)'),
            'a brief with a placeholder': (1, 'still has unfilled placeholders'),
            'too few arguments': (1, 'usage: kitchen.sh queue <store> <brief-path> [--replace] | pair.sh queue <store> --clear'),
            'no pair.json': (1, 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>\njq: error: Could not open file : No such file or directory\n'),
            'a sidekick that is a string': (3, 'sidekick null is absent; resolve that before queueing'),
            'an escalated brief and no pair.json': (1, 'no pair.json in <T>/st; run: pair.sh init <slug>'),
        }
        out = self.phase4_cases('que', cases, expect)
        # the classify the queue runs is quiet on stdout and stamps the brief: the bytes are compared by the A/B, and the stamp is there
        self.assertIn('profiles: app', out['a brief queued behind a working sidekick']['files']['briefs/002-y.md'])
        self.assertNotIn('profiles:', out['a landing brief']['files']['briefs/002-y.md'])

    SETUP_HOOK = {'hook': READY_HOOK, 'env': {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PREFLIGHT_RETRY_S': '0'}}
    SETUP_MASK = [(r'<SK>[^\s"\\]*(kitchen|pair-core|consultant-core)\.sh: line [0-9]+: (\$2|abc): unbound variable', r'pair.sh: \2: unbound variable'), (r'<SK>[^\s"\\]*(consultant-core|pair-core|kitchen)\.sh: line [0-9]+: \[: ', 'pair.sh: [: '), (r'setup-[0-9]{8}T[0-9]{6}-[0-9]+\.log', 'setup-<LOG>.log'), (r'repo-[0-9a-f]{8}', 'repo-H')]

    def init_command_cases(self):
        full = self.KITCHEN_TOML + '\n[review]\nself = ["pstack-review", "ce-review"]\n\n[landing]\nmode = "pr"\n'
        setup = self.KITCHEN_TOML + '\n[scratch]\nsetup = ["echo prepared", "touch ready.txt"]\n'
        failing = self.KITCHEN_TOML + '\n[scratch]\nsetup = ["echo before", "exit 3"]\n'
        init = lambda *a, **kw: dict(args=['init', 'demo', '--store', '{st}', *a], mask=self.SETUP_MASK, **kw)
        both = {'standing-orders.md': '# Standing orders\n\n16. kept (kitchen.toml review.self)\n17. kept (kitchen.toml landing.mode)\n'}
        worktree = [('git', 'worktree', 'add', '-q', '{T}/wt')]
        return {
            'a re-init under a repo kitchen': init(),
            'a kitchen with review.self and a landing mode': init(toml=full),
            'orders that hold both lines already': init(toml=full, files=both),
            'orders that hold one line': init(toml=full, files={'standing-orders.md': '# O\n\n17. kept (kitchen.toml landing.mode)\n'}),
            'a store that is new': dict(args=['init', 'demo', '--store', '{T}/new'], mask=self.SETUP_MASK),
            'no kitchen at all': init(toml=None),
            'a kitchen that does not parse': init(toml='version = 1\n[profile.app]\npaths = 3\n'),
            'a pair.json that already lists builds': init(pair={'skill_builds': [{'path': '/old', 'at': '2026-01-01T00:00:00Z'}]}),
            'a pair.json that is an array': init(files={'pair.json': '[]\n'}),
            'a main checkout with a setup': init(toml=setup),
            'a linked worktree with no setup': dict(args=['init', 'demo', '--store', '{T}/new'], mask=self.SETUP_MASK, toml=full, gitops=worktree, cwd='wt'),
            'a linked worktree with a setup': dict(args=['init', 'demo', '--store', '{T}/new'], mask=self.SETUP_MASK, toml=setup, gitops=worktree, cwd='wt'),
            'a linked worktree whose setup fails': dict(args=['init', 'demo', '--store', '{T}/new'], mask=self.SETUP_MASK, toml=failing, gitops=worktree, cwd='wt'),
            'no slug': dict(args=['init'], mask=self.SETUP_MASK),
            'outside herdr': init(env={'HERDR_ENV': '0'}),
            'an unknown option': init('--force'),
            '--pane with no value': init('--pane'),
            '--store with no value': dict(args=['init', 'demo', '--store'], mask=self.SETUP_MASK),
            'a stale --pane': init('--pane', 'gone2'),
            'a slug that is not one': dict(args=['init', 'Demo', '--store', '{st}'], mask=self.SETUP_MASK),
        }

    def spawn_command4_cases(self):
        roster = {'home/.config/pstack/kitchen.toml': (self.ROSTER_TOML, 0o644)}
        bare = {'home/.config/pstack/kitchen.toml': ('[sidekick]\nkind = "claude"\nargs = ["--model", "m1"]\n', 0o644)}
        broken = {'home/.config/pstack/kitchen.toml': ('[sidekick\n', 0o644)}
        side = lambda **kw: {'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None, **kw}}
        base = dict(args=['spawn', '{st}'], topfiles=roster, mask=self.SETUP_MASK, **self.SETUP_HOOK, pair=side())
        run = lambda *a, **kw: {**base, 'args': ['spawn', '{st}', *a], **kw}
        return {
            'the roster default kind': base,
            'the roster default kind and a trailing native arg': run('--', '--x'),
            '--kind over the roster': run('--kind', 'codex'),
            '--kind equal to the roster kind takes its fallback': run('--kind', 'claude'),
            '--kind with an explicit fallback': run('--kind', 'codex', '--fallback', 'pi'),
            '--kind with native arguments': run('--kind', 'codex', '--', '--a', '--b'),
            'the roster kind with a fallback of its own named': run('--fallback', 'pi'),
            'a roster with no fallback': run(topfiles=bare),
            '--tab': run('--tab', pair={**side(), 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}),
            'an option for the core': run('--kind', 'claude', '--bogus'),
            '--kind with no value': run('--kind'),
            '--fallback with no value': run('--kind', 'claude', '--fallback'),
            'an empty roster and no --kind': run(topfiles={}),
            'an empty roster and a --kind': run('--kind', 'claude', topfiles={}),
            'a roster that does not parse': run(topfiles=broken),
            'a roster that does not parse and a --kind': run('--kind', 'claude', topfiles=broken),
            'no store argument': dict(base, args=['spawn']),
            'a store with no pair.json': run('--kind', 'claude', nopair=True),
            'outside herdr': run(env={**self.SETUP_HOOK['env'], 'HERDR_ENV': '0'}),
            'a pi preflight that fails with the roster fallback': run('--kind', 'pi', topfiles={'home/.config/pstack/kitchen.toml': ('[sidekick]\nkind = "pi"\n[sidekick.fallback]\nkind = "devin"\nargs = ["--fb"]\n', 0o644)},
                                                                       fake={'pi-fail': 'quota\n'}),
            'a bootstrap that returns 4': run(hook=None),
        }

    def consultant_command_cases(self):
        roster = {'home/.config/pstack/kitchen.toml': ('[consultant]\nkind = "pi"\nargs = ["--model", "c"]\n\n[consultant.fallback]\nkind = "codex"\nargs = ["--fb"]\n', 0o644)}
        nofb = {'home/.config/pstack/kitchen.toml': ('[consultant]\nkind = "pi"\nargs = ["--model", "c"]\n', 0o644)}
        same = {'home/.config/pstack/kitchen.toml': ('[consultant]\nkind = "pi"\n[consultant.fallback]\nkind = "pi"\n', 0o644)}
        broken = {'home/.config/pstack/kitchen.toml': ('[consultant\n', 0o644)}
        pair = lambda **kw: {'master': {'name': 'm1', 'pane_id': 'p0'}, 'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': None},
                             'consultant': {'name': 'c1', 'kind': 'codex', 'generation': 1, 'pane_id': None}, **kw}
        base = dict(pair=pair(), panes={'p0': 'm1'}, hook=self.READY_HOOK, env=self.SETUP_HOOK['env'], mask=self.SETUP_MASK, args=['consultant', '{st}', '--reason', 'a contract'])
        run = lambda *a, **kw: {**base, 'args': [*base['args'], *a], **kw}
        bare = lambda *a, **kw: {**base, 'args': ['consultant', '{st}', *a], **kw}
        return {
            'a start that succeeds': run('--kind', 'codex'),
            'a start anchored on the sidekick pane': run('--kind', 'codex', agents={'s1': 'idle claude'}, panes={'p0': 'm1', 'p1': 's1'},
                                                         pair=pair(sidekick={'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p1'})),
            'a start whose anchor pane is gone': run('--kind', 'codex', agents={'s1': 'idle claude'}, pair=pair(sidekick={'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p9'})),
            'a master that is a string': run('--kind', 'codex', pair=pair(master='x')),
            'a start that returns 4': run('--kind', 'codex', hook=None),
            'a start that returns 3': run('--kind', 'codex', promptfail=True),
            'a start that dies with 5': run('--kind', 'devin', '--', '--resume'),
            'a permission': run('--kind', 'codex', '--permission', 'none'),
            'native arguments after --': run('--kind', 'codex', '--', '--x', '--y'),
            'the roster kind and its native args': run(topfiles=roster),
            'the roster kind failing its preflight takes the fallback': run(topfiles=roster, fake={'pi-fail': 'quota\n'}),
            'a failover over earlier failovers': run(topfiles=roster, fake={'pi-fail': 'quota\n'}, pair=pair(consultant={'name': 'c1', 'kind': 'codex', 'generation': 1, 'pane_id': None, 'failovers': [{'from': 'a', 'to': 'b'}]})),
            'a failover over failovers that are no array': run(topfiles=roster, fake={'pi-fail': 'quota\n'}, pair=pair(consultant={'name': 'c1', 'kind': 'codex', 'generation': 1, 'pane_id': None, 'failovers': 'x'})),
            'the roster kind failing its preflight with no fallback': run(topfiles=nofb, fake={'pi-fail': 'quota\n'}),
            'a fallback that is the kind itself': run(topfiles=same, fake={'pi-fail': 'quota\n'}),
            'a failing preflight that says nothing': run(topfiles=roster, fake={'pi-fail': '\n'}),
            'a failing preflight with a long message': run(topfiles=roster, fake={'pi-fail': 'x' * 300 + '\nsecond line\n'}),
            'a roster kind that returns 4 after its preflight': run(topfiles=roster, hook=None),
            'a live consultant skips the preflight': run(topfiles=roster, agents={'c1': 'idle codex'}, fake={'pi-fail': 'quota\n'}),
            'a consultant that is a string': run(topfiles=roster, fake={'pi-fail': 'quota\n'}, pair=pair(consultant='c1')),
            'escalations already recorded': run('--kind', 'codex', pair=pair(escalations=[{'reason': 'old', 'at': 'then'}])),
            'escalations that are no array': run('--kind', 'codex', pair=pair(escalations='x')),
            'an empty roster and no --kind': run(),
            'a roster that does not parse': run(topfiles=broken),
            'no reason': bare('--kind', 'codex'),
            '--reason with no value': bare('--reason'),
            '--kind with no value': run('--kind'),
            'an unknown option': run('--kind', 'codex', '--bogus'),
            'no store argument': dict(base, args=['consultant']),
            'a store with no pair.json': run('--kind', 'codex', nopair=True),
            'outside herdr': run('--kind', 'codex', env={**self.SETUP_HOOK['env'], 'HERDR_ENV': '0'}),
        }

    def fleet_cases(self):
        run_state = lambda started: {'pstack/kitchen/runs/demo/pair.json': json.dumps({'slug': 'demo', 'master': {'name': 'm1'}, 'sidekick': {'name': 's1', 'kind': 'claude'}, 'started': started}) + '\n',
                                     'pstack/kitchen/runs/demo/events.tsv': '2026-01-01T00:00:00Z\t1767225600\tmaster\tinit\t-\tx\n'}
        return {
            'no runs': dict(args=['fleet']),
            'no runs, json': dict(args=['fleet', '--json']),
            'one run': dict(args=['fleet'], statefiles=run_state(1767225600)),
            'one run, json': dict(args=['fleet', '--json'], statefiles=run_state(1767225600)),
            'one run since a later day': dict(args=['fleet', '--since', '2027-01-01'], statefiles=run_state(1767225600)),
            'a runs directory that is not there': dict(args=['fleet', '--runs', '{T}/nope']),
            'a runs directory given': dict(args=['fleet', '--runs', '{T}/state/pstack/kitchen/runs'], statefiles=run_state(1767225600)),
            'an unknown option': dict(args=['fleet', '--bogus']),
            '--since with no value': dict(args=['fleet', '--since']),
            'a --since that is no date': dict(args=['fleet', '--since', 'soon']),
        }

    def test_init_matches_the_bash_at_the_phase_4_base(self):
        cases = self.init_command_cases()
        expect = {
            'a re-init under a repo kitchen': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'a kitchen with review.self and a landing mode': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'orders that hold both lines already': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'orders that hold one line': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'a store that is new': (0, 'store: <T>/new (master demo-master in p0)'),
            'no kitchen at all': (0, 'kitchen: none valid in this repo; run pstack-kitchen-setup before dispatching'),
            'a kitchen that does not parse': (0, 'kitchen: none valid in this repo; run pstack-kitchen-setup before dispatching'),
            'a pair.json that already lists builds': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'a pair.json that is an array': (5, 'jq: error (at <T>/st/pair.json:1): Cannot index array with string ("master")'),
            'a main checkout with a setup': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'a linked worktree with no setup': (0, 'store: <T>/new (master demo-master in p0)'),
            'a linked worktree with a setup': (0, 'store: <T>/new (master demo-master in p0)'),
            'a linked worktree whose setup fails': (2, 'pair.sh: this run is in a linked worktree and its [scratch].setup failed:'),
            'no slug': (1, 'pair.sh: usage: kitchen.sh init <slug> [--store DIR] [--pane ID]'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
            'an unknown option': (1, 'pair.sh: unknown option --force'),
            '--pane with no value': (1, 'pair.sh: $2: unbound variable'),
            '--store with no value': (1, 'pair.sh: $2: unbound variable'),
            'a stale --pane': (2, 'pair.sh: pane gone2 is not a live Herdr pane; HERDR_PANE_ID is stale when a harness runs commands through a sh'),
            'a slug that is not one': (1, "pair.sh: slug must match [a-z][a-z0-9_-]{0,20} (so <slug>-consultant fits Herdr's 32-char name limit)"),
        }
        out = self.phase4_cases('init', cases, expect)
        orders = lambda name: out[name]['files'].get('standing-orders.md', '')
        self.assertIn('16. Before reporting done, the sidekick runs these skills on its diff and fixes what they find: pstack-review, ce-review (kitchen.toml review.self).', orders('a kitchen with review.self and a landing mode'))
        self.assertIn('17. Landing goes as far as `pr` (kitchen.toml landing.mode) and never merges.', orders('a kitchen with review.self and a landing mode'))
        self.assertIn('17. Landing goes as far as `commit` (kitchen.toml landing.mode)', orders('a re-init under a repo kitchen'))
        self.assertNotIn('16. Before', orders('a re-init under a repo kitchen'))
        self.assertEqual(orders('orders that hold both lines already').count('kept'), 2)
        self.assertNotIn('Landing goes as far as', orders('orders that hold both lines already'))
        self.assertEqual(orders('orders that hold one line').count('landing.mode'), 1)
        self.assertIn('16. Before', orders('orders that hold one line'))
        self.assertIn('worktree: prepared with [scratch].setup (setup: ok (2 commands', out['a linked worktree with a setup']['stdout'])
        self.assertNotIn('worktree:', out['a main checkout with a setup']['stdout'])
        self.assertIn('"path"', out['a re-init under a repo kitchen']['files']['pair.json'])

    def test_spawn_matches_the_bash_at_the_phase_4_base(self):
        cases = self.spawn_command4_cases()
        expect = {
            'the roster default kind': (0, 'sidekick s1 (claude) started in p0'),
            'the roster default kind and a trailing native arg': (0, 'started in'),
            '--kind over the roster': (0, 'started in'),
            '--kind equal to the roster kind takes its fallback': (0, 'started in'),
            '--kind with an explicit fallback': (0, 'sidekick s1 (codex) started in p0'),
            '--kind with native arguments': (0, 'started in'),
            'the roster kind with a fallback of its own named': (0, 'started in'),
            'a roster with no fallback': (0, 'started in'),
            '--tab': (0, 'started in'),
            'an option for the core': (1, 'pair.sh: unknown option --bogus'),
            '--kind with no value': (1, 'pair.sh: $2: unbound variable'),
            '--fallback with no value': (1, 'pair.sh: $2: unbound variable'),
            'an empty roster and no --kind': (1, 'pair.sh: no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)'),
            'an empty roster and a --kind': (0, 'started in'),
            'a roster that does not parse': (1, 'Traceback (most recent call last):'),
            'a roster that does not parse and a --kind': (1, 'Traceback (most recent call last):'),
            'no store argument': (1, 'pair.sh: usage: kitchen.sh spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [-- agent-arg'),
            'a store with no pair.json': (1, 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
            'a pi preflight that fails with the roster fallback': (0, 'pi failed preflight: pi 1.0.2 at'),
            'a bootstrap that returns 4': (4, 'sidekick settled but reports/000-ready.md is missing; state=idle. Read the pane before prompting again.'),
        }
        out = self.phase4_cases('spawn', cases, expect)
        self.assertIn('"kind": "devin"', out['--kind equal to the roster kind takes its fallback']['files']['pair.json'])
        self.assertIn('"kind": "codex"', out['--kind over the roster']['files']['pair.json'])
        self.assertIn('"kind": "devin"', out['a pi preflight that fails with the roster fallback']['files']['pair.json'])

    def test_consultant_matches_the_bash_at_the_phase_4_base(self):
        cases = self.consultant_command_cases()
        expect = {
            'a start that succeeds': (0, 'consultant c1 (codex) started in p1'),
            'a start anchored on the sidekick pane': (0, 'started in'),
            'a start whose anchor pane is gone': (0, 'started in'),
            'a master that is a string': (5, 'jq: error (at <T>/st/pair.json:21): Cannot index string with string ("pane_id")'),
            'a start that returns 4': (4, 'consultant settled but advice/000-ready.md is missing; state=idle. Read the pane before prompting again.'),
            'a start that returns 3': (3, 'consultant bootstrap did not settle: {"error":{"code":"prompt_failed"}}'),
            'a start that dies with 5': (5, 'pair.sh: devin task sessions start fresh; recover partial work from the store instead of passing --resume'),
            'a permission': (0, 'started in'),
            'native arguments after --': (0, 'started in'),
            'the roster kind and its native args': (0, 'consultant c1 (pi) started in p1'),
            'the roster kind failing its preflight takes the fallback': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a failover over earlier failovers': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a failover over failovers that are no array': (5, 'jq: error (at <T>/st/pair.json:25): string ("x") and array ([{"from":"pi","to":"codex...]) cannot be added'),
            'the roster kind failing its preflight with no fallback': (2, 'pair.sh: consultant pi failed its preflight and the roster names no other fallback: pi 1.0.2 at'),
            'a fallback that is the kind itself': (2, 'pair.sh: consultant pi failed its preflight and the roster names no other fallback: pi 1.0.2 at'),
            'a failing preflight that says nothing': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a failing preflight with a long message': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a roster kind that returns 4 after its preflight': (4, 'consultant settled but advice/000-ready.md is missing; state=idle. Read the pane before prompting again.'),
            'a live consultant skips the preflight': (0, 'consultant c1 already live (idle) in null'),
            'a consultant that is a string': (5, 'jq: error (at <T>/st/pair.json:19): Cannot index string with string ("name")'),
            'escalations already recorded': (0, 'started in'),
            'escalations that are no array': (5, 'jq: error (at <T>/st/pair.json:38): string ("x") and array ([{"reason":"a contract","...]) cannot be added'),
            'an empty roster and no --kind': (1, 'pair.sh: no --kind and no [consultant] kind in the host roster'),
            'a roster that does not parse': (1, 'Traceback (most recent call last):'),
            'no reason': (1, 'pair.sh: --reason is required: the escalation that warrants the consultant'),
            '--reason with no value': (1, 'pair.sh: $2: unbound variable'),
            '--kind with no value': (1, 'pair.sh: $2: unbound variable'),
            'an unknown option': (1, 'pair.sh: unknown option --bogus'),
            'no store argument': (1, 'pair.sh: usage: kitchen.sh consultant <store> --reason TEXT [--kind KIND] [--permission MODE|none] [-- agent-a'),
            'a store with no pair.json': (1, 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
        }
        out = self.phase4_cases('cons', cases, expect)
        self.assertIn('a contract', out['a start that succeeds']['files']['pair.json'])
        self.assertIn('master\tescalate', '\n'.join(out['a start that succeeds']['events']))
        self.assertNotIn('a contract', out['a start that returns 4']['files']['pair.json'])
        self.assertIn('"failovers"', out['the roster kind failing its preflight takes the fallback']['files']['pair.json'])
        self.assertIn('consultant:pi:codex', '\n'.join(out['the roster kind failing its preflight takes the fallback']['events']))
        self.assertNotIn('failover', ''.join(out['a live consultant skips the preflight']['stdout']))

    def test_fleet_matches_the_bash_at_the_phase_4_base(self):
        cases = self.fleet_cases()
        expect = {
            'no runs': (0, 'run                      repo           build'),
            'no runs, json': (2, 'usage: kitchen.py [-h] [--repo REPO] [--config CONFIG] [--at AT] [--json]'),
            'one run': (0, 'run                      repo           build'),
            'one run, json': (2, 'usage: kitchen.py [-h] [--repo REPO] [--config CONFIG] [--at AT] [--json]'),
            'one run since a later day': (0, 'run                      repo           build'),
            'a runs directory that is not there': (0, 'run                      repo           build'),
            'a runs directory given': (0, 'run                      repo           build'),
            'an unknown option': (2, 'usage: kitchen.py [-h] [--repo REPO] [--config CONFIG] [--at AT] [--json]'),
            '--since with no value': (2, 'usage: kitchen.py fleet [-h] [--since YYYY-MM-DD] [--runs RUNS]'),
            'a --since that is no date': (0, 'run                      repo           build'),
        }
        out = self.phase4_cases('fleet', cases, expect)
        self.assertIn('demo ', out['one run']['stdout'])
        self.assertNotIn('demo ', out['one run since a later day']['stdout'])

    def dispatch_cases(self):
        world = self.launch_world
        # the quiet wait's clock: each poll's sleep moves the clock file on 1500 s, so a dispatch that waits quietly makes a fixed number of polls
        clock = dict(topfiles={'clock': ('1000\n', 0o644)}, envfill={'KITCHEN_QUIET_CLOCK': '{T}/clock'}, tick=1500)
        env = {'KITCHEN_QUIET_POLL_S': '0.0123', 'KITCHEN_QUIET_MAX_S': '3600'}
        mask = self.SETUP_MASK + [(r'elapsed [0-9]+m', 'elapsed Nm'), (r'last -?[0-9]+m ago', 'last Nm ago'), (r'quiet: [0-9]+m', 'quiet: Nm')]
        sidekick = {'sidekick': {'kind': 'claude', 'generation': 1, 'name': 's1', 'pane_id': 'p1'}}
        agents = {'m1': 'idle claude', 's1': 'idle claude'}
        panes = {'p0': 'm1', 'p1': 's1'}
        path = '{st}/briefs/002-y.md'
        fresh = {'progress/002-y.md': '10:00 one\n'}
        # no report for unit 002: a report already there makes the core's loop end on a path that depends on how many herdr looks it takes
        dispatch = lambda *a, **kw: world(**{**dict(args=['dispatch', '{st}', *(a or (path, '--timeout', '0'))], agents=agents, panes=panes, mask=mask, pair=sidekick, env=env),
                                              **kw, 'files': {'reports/002-y.md': None, **kw.get('files', {})}})
        quiet = lambda *a, **kw: dispatch(*a, **{**clock, 'once': {'prompt': 'printf "working claude\\n" >"$FAKE/agents/s1"\n'}, 'mtimes': {'progress/002-y.md': 4_000_000_000}, **kw, 'files': {**fresh, **kw.get('files', {})}})
        landing = lambda extra: {'briefs/002-y.md': self.launch_brief('002', 'y', extra)}
        return {
            'a brief and no pair.json': dispatch(path, '--timeout', '100', nopair=True, agents={'m1': 'idle claude', 's1': 'idle claude'}),
            'a quiet check-in, then the quiet wait': quiet(),
            'a quiet check-in, then --max ends it': quiet(path, '--timeout', '0', '--max', '1'),
            'a quiet check-in and --max with no value': quiet(path, '--timeout', '0', '--max'),
            'a stale log is not swallowed': quiet(mtimes={'progress/002-y.md': 1000}),
            'a brief given relative to the store': quiet('briefs/002-y.md', '--timeout', '0'),
            'send only': dispatch(path, '--send-only'),
            'an option the dispatch does not know': dispatch(path, '--bogus'),
            'a brief that is not there': dispatch('{st}/briefs/009-none.md', '--timeout', '0'),
            'one argument': dispatch('{st}'),
            'a paused store': dispatch(path, '--timeout', '0', files={'paused': 'lunch\n'}),
            'the queue holds another brief': dispatch(path, '--timeout', '0', files={'queue': '{st}/briefs/001-x.md\n'}),
            'no brief argument': dispatch(args=['dispatch', '{st}']),
            'a landing brief that land-check passes': quiet(path, '--timeout', '0', files=landing('landing: yes\n')),
            'a landing brief under the shipping playbook': quiet(path, '--timeout', '0', files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('playbook: feature', 'playbook: shipping')}),
            'a landing brief under a kitchen that does not validate': dispatch(path, '--timeout', '0', files=landing('landing: yes\n'), toml='version = 1\n[profile.app]\npaths = 3\n'),
            'a landing brief and no pair.json': dispatch(path, '--timeout', '0', nopair=True, files=landing('landing: yes\n')),
            'a brief that classify refuses': dispatch(path, '--timeout', '0', files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('- src/**\n', '')}),
            'an escalated brief': dispatch(path, '--timeout', '0', files={'briefs/002-y.md': self.launch_brief('002', 'y').replace('risk: routine', 'risk: escalated')}),
            'a sidekick that is blocked': dispatch(path, '--timeout', '0', agents={**agents, 's1': 'blocked claude'}),
            'outside herdr': dispatch(path, '--timeout', '0', env={**env, 'HERDR_ENV': '0'}),
        }

    def test_dispatch_matches_the_bash_at_the_phase_4_base(self):
        cases = self.dispatch_cases()
        quiet = (4, 'quiet: Nm with nothing flagged; the sidekick works on')
        expect = {
            'a brief and no pair.json': (2, 'no pair.json in'),
            'a quiet check-in, then the quiet wait': quiet,
            'a quiet check-in, then --max ends it': quiet,
            'a quiet check-in and --max with no value': (1, '$2: unbound variable'),
            'a stale log is not swallowed': (4, 'STALE'),
            'a brief given relative to the store': quiet,
            'send only': (0, 'sent: BRIEF'),
            'an option the dispatch does not know': (1, 'unknown option --bogus'),
            'a brief that is not there': (1, 'brief not found: <T>/st/briefs/009-none.md'),
            'one argument': (1, 'brief not found: <T>/st'),
            'no brief argument': (1, 'usage: kitchen.sh dispatch <store> <brief-path>'),
            'a paused store': (5, 'the store is paused (lunch)'),
            'the queue holds another brief': (5, 'the queue holds'),
            'a landing brief that land-check passes': quiet,
            'a landing brief under the shipping playbook': quiet,
            'a landing brief under a kitchen that does not validate': (2, 'landing refused: kitchen.sh land-check <T>/st fails'),
            'a landing brief and no pair.json': (2, 'warning: Scope entry'),
            'a brief that classify refuses': (1, "lists nothing under Scope's may write:"),
            'an escalated brief': (6, 'playbook feature needs an agreed plan'),
            'a sidekick that is blocked': (3, 'sidekick s1 is blocked'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.phase4_cases('disp', cases, expect)
        # the quiet path swallows the digest and leaves the wake row to the quiet wait; a flagged digest is printed whole and logged once
        self.assertEqual(out['a quiet check-in, then the quiet wait']['events'].count('master\twake\t002-y\tcheckin'), 1)
        self.assertEqual(out['a stale log is not swallowed']['events'].count('master\twake\t002-y\tcheckin'), 1)
        self.assertIn('risk: routine', out['a quiet check-in, then the quiet wait']['stdout'])
        for name in ('a landing brief that land-check passes', 'a landing brief under the shipping playbook'):
            self.assertNotIn('risk:', out[name]['stdout'], f'{name}: a landing brief is not classified')

    def step_world(self, *args, **extra):
        """Unit 002 dispatched at u1 on the launch world's repo (HEAD is k1, the last commit); `step <store> ARGS`. Each gate command is `true`, so the
        profiles the commits touch pass, unless a case brings its own kitchen."""
        sidekick = {'sidekick': {'kind': 'claude', 'generation': 1, 'name': 's1', 'pane_id': 'p1'}, 'dispatch': {'brief': '{st}/briefs/002-y.md', 'head': '{u1}'}}
        env = {'KITCHEN_STEP_POLL_S': '0.05', 'PSTACK_KITCHEN_SLOT_POLL_S': '0.05', 'KITCHEN_STEP_SLICE_S': '60', 'PSTACK_KITCHEN_RETRY_LOAD': 'off', **extra.pop('env', {})}
        pair = {**sidekick, **extra.pop('pair', {})}
        return self.launch_world(**{**dict(args=['step', '{st}', *args], agents={'m1': 'idle claude', 's1': 'idle claude'}, panes={'p0': 'm1', 'p1': 's1'}, pair=pair, env=env,
                                           mask=self.SETUP_MASK + [(r'\([0-9]+s so far', '(Ns so far'), (r'logs/[0-9]{8}T[0-9]{6}', 'logs/<STAMP>')]), **extra})

    def step_cases(self):
        step = self.step_world
        failing = self.LAUNCH_TOML.replace('fast = ["true"]', 'fast = ["false"]')
        flaky = self.LAUNCH_TOML.replace('[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["true"]', '[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["test -e .flaky || { touch .flaky; exit 1; }"]')
        extra_commit = [*self.LAUNCH_OPS, ('write', 'notes.txt', 'x\n'), ('commit', 'n1')]
        return {
            'a first step': step('{k1}', 'did a thing'),
            'a step whose gate fails': step('{k1}', 'did a thing', toml=failing),
            'a step whose gate fails twice in a row': step('{k1}', 'did a thing', toml=failing, twice=True),
            'a step no profile covers': step('{n1}', 'notes', gitops=extra_commit, pair={'heads': {'002-y': '{k1}'}}),
            'a gate that passed after a rerun under load': step('{k1}', 'did a thing', toml=flaky, env={'PSTACK_KITCHEN_RETRY_LOAD': '0'}, mask=self.SETUP_MASK + [(r'logs/[0-9]{8}T[0-9]{6}', 'logs/<STAMP>'), (r'at load [0-9.]+', 'at load N')]),
            'a step already recorded': step('{k1}', 'again', files={'steps/002-y.tsv': '1\t{k1}\t5\t\tone\n'}),
            'a step whose gates are still running': step('{k1}', 'did a thing', env={'KITCHEN_STEP_SLICE_S': '0'}, skip=['steps/jobs/']),
            'a sha that is not HEAD': step('{u2}', 'x'),
            'a tracked file edited since': step('{k1}', 'x', gitops=[*self.LAUNCH_OPS, ('write', 'src/app.py', 'y = 2\n')]),
            'a sha that is not a commit': step('f' * 40, 'x'),
            'no brief dispatched': step('{k1}', 'x', pair={'dispatch': {}}),
            'no base for the unit': step('{k1}', 'x', pair={'heads': {}}),
            'a step on the unit base, so an empty range': step('{k1}', 'x', pair={'heads': {'002-y': '{k1}'}}),
            'a step that resolves a note': step('{k1}', 'fix', '--resolves', 'n1', files={'notes/002-y-n1.md': '# N\n\nstatus: blocking\n'}),
            'a brief stamped with a kitchen snapshot': step('{k1}', 'x', topfiles={'snap.toml': (self.LAUNCH_TOML, 0o644)},
                                                          files={'briefs/002-y.md': self.launch_brief('002', 'y', 'kitchen: {T}/snap.toml\n')}),
            'a base behind where the branch leaves trunk': step('{k1}', 'x', pair={'heads': {'002-y': '{u0}'}}),
            'two arguments': step('{k1}'),
            'no pair.json': step('{k1}', 'x', nopair=True),
            'a pair.json that is an array': step('{k1}', 'x', nopair=True, files={'pair.json': '[]\n'}),
        }

    def test_step_matches_the_bash_at_the_phase_4_base(self):
        cases = self.step_cases()
        recorded = (0, 'step 1 recorded at')
        expect = {
            'a first step': (0, 'gates: pass (app,contracts,docs)'),
            'a step whose gate fails': (2, 'next: fix it in a new commit and run kitchen.sh step <T>/st <new-sha> "did a thing" again'),
            'a step whose gate fails twice in a row': (2, 'next: fix it in a new commit'),
            'a step no profile covers': (0, 'gates: none ran; no profile covers notes.txt'),
            'a gate that passed after a rerun under load': (0, 'flaky under load: app failed, then passed on a rerun'),
            'a step already recorded': (0, 'already recorded'),
            'a step whose gates are still running': (4, 'gates: still running for'),
            'a sha that is not HEAD': (1, 'HEAD is'),
            'a tracked file edited since': (1, 'the tree has uncommitted changes to tracked files'),
            'a sha that is not a commit': (1, 'not a commit: ffffffffffffffffffffffffffffffffffffffff'),
            'no brief dispatched': (1, 'no brief dispatched; a step belongs to a running brief'),
            'no base for the unit': (1, 'no base for 002-y; was it dispatched?'),
            'a step on the unit base, so an empty range': (0, 'gates: pass (contracts)'),
            'a step that resolves a note': recorded,
            'a brief stamped with a kitchen snapshot': recorded,
            'a base behind where the branch leaves trunk': recorded,
            'two arguments': (1, 'usage: kitchen.sh step <store> <sha> <summary>'),
            'no pair.json': (1, 'no pair.json in'),
            'a pair.json that is an array': (5, 'Cannot index array with string ("dispatch")'),
        }
        out = self.phase4_cases('step', cases, expect)
        # the second of two failing steps counts the failures in a row; a step whose gates run on leaves a job directory with its log and a link to it
        self.assertIn('2 failed checks in a row; write the report as blocked', out['a step whose gate fails twice in a row']['second'][1])
        self.assertTrue(any(name.endswith('/out.1') for name in out['a first step']['files']))
        self.assertIn('gates: pass', out['a first step']['stdout'])
        self.assertEqual(out['a step whose gate fails']['events'][-1].split('\t')[:2], ['sidekick', 'gate-fail'])
        self.assertEqual(out['a gate that passed after a rerun under load']['events'][-2].split('\t')[:2], ['sidekick', 'gate-flaky'])
        self.assertTrue(any('002-y' in name and name.endswith('.tsv') for name in out['a first step']['files']))

    def test_the_detached_gate_and_verify_runs_ignore_the_signals_the_bashs_did(self):
        """`setsid ... &` in a script starts the job with SIGINT and SIGQUIT ignored (SigIgn 0x6 at b632713): the port's detached runs match, read from
        /proc by the gate's own command and the brief's Verify command."""
        gate = self.LAUNCH_TOML.replace('[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["true"]', '[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["grep SigIgn /proc/self/status >../sigign"]')
        verify = self.launch_brief('002', 'y').replace('```bash\ntrue\n```', '```bash\ngrep SigIgn /proc/self/status >../sigign\n```')
        specs = {'step': self.step_world('{k1}', 'x', toml=gate, env={'PSTACK_KITCHEN_RETRY_LOAD': 'off'}),
                 'finish': self.step_world(files={'reports/002-y.md': '# R\n\nstatus: done\nhead: {k1}\n', 'briefs/002-y.md': verify}, args=['finish', '{st}', '{st}/reports/002-y.md'])}
        specs['finish']['args'] = ['finish', '{st}', '{st}/reports/002-y.md']
        found = {}
        for name, spec in specs.items():
            spec = {**spec, 'repo_files': {'.agents/kitchen.toml': spec.get('toml') or self.KITCHEN_TOML}}
            for tag, skills in (('base', self.phase4_skills()), ('port', root / 'skills')):
                got = self.ab_run(f'sig{name}-{tag}', skills, 'pstack-kitchen', spec)
                self.assertEqual(got['rc'], 0, f'{name} {tag}: {got["stdout"]}{got["stderr"]}')
                found[name, tag] = (self.path / f'sig{name}-{tag}' / 'sigign').read_text().strip()
            self.assertEqual(found[name, 'port'], found[name, 'base'], name)
            self.assertEqual(int(found[name, 'base'].split()[-1], 16) & 0x6, 0x6, f'{name}: SIGINT and SIGQUIT ignored in the base')

    @staticmethod
    def kill_group(group):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)

    def test_step_and_finish_outlive_the_shell_that_ran_them_as_the_bashs_did(self):
        """cmd_step and cmd_finish were bash with no parent-death signal (and the core step and finish never set one): `sh -c 'kitchen.sh step ...; :'`
        with the `sh` killed mid-gate still recorded the step. The gate (or the Verify command) blocks on a fifo the test opens after it killed the
        shell, so nothing here waits on a clock."""
        blocked = 'mkfifo ../gate.fifo; cat ../gate.fifo >/dev/null'
        gate = self.LAUNCH_TOML.replace('[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["true"]', f'[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["{blocked}"]')
        verify = self.launch_brief('002', 'y').replace('```bash\ntrue\n```', f'```bash\n{blocked}\n```')
        done = {'reports/002-y.md': '# R\n\nstatus: done\nhead: {k1}\n', 'briefs/002-y.md': verify}
        specs = {'step': (self.step_world('{k1}', 'x', toml=gate), lambda st: (st / 'gates.tsv').read_text().count('\t002-y\t')),
                 'finish': (self.step_world(files=done), lambda st: (st / 'events.tsv').read_text().count('finish-verify-pass'))}
        specs['finish'][0]['args'] = ['finish', '{st}', '{st}/reports/002-y.md']
        for name, (spec, recorded) in specs.items():
            spec = {**spec, 'repo_files': {'.agents/kitchen.toml': spec.get('toml') or self.KITCHEN_TOML}}
            for tag, skills in (('base', self.phase4_skills()), ('port', root / 'skills')):
                top, st, env, fill = self.ab_world(f'orphan{name}-{tag}', spec)
                shell = subprocess.Popen(['sh', '-c', '"$0" "$@"; :', str(skills / 'pstack-kitchen/scripts/kitchen.sh'), *(fill(a) for a in spec['args'])], cwd=top / 'repo',
                                         env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                self.addCleanup(self.kill_group, shell.pid)
                for _ in range(1200):
                    if (top / 'gate.fifo').exists():
                        break
                    time.sleep(0.05)  # kitchen-allow: test-sleep (a bounded wait, at most 60 s, for the gate to start and make its fifo; a passing run leaves at once)
                self.assertTrue((top / 'gate.fifo').exists(), f'{name} {tag}: the gate never started')
                shell.kill()
                shell.wait()
                with open(top / 'gate.fifo', 'w'):
                    pass
                for _ in range(1200):
                    if (st / ('gates.tsv' if name == 'step' else 'events.tsv')).exists() and recorded(st):
                        break
                    time.sleep(0.05)  # kitchen-allow: test-sleep (a bounded wait, at most 60 s, for the command that outlived its shell to record; a passing run leaves at once)
                self.assertEqual(recorded(st), 1, f'{name} {tag}: the command did not outlive the shell that ran it')

    def finish_cases(self):
        world = self.step_world
        path = '{st}/reports/002-y.md'
        done = lambda head='{k1}': {'reports/002-y.md': f'# R\n\nstatus: done\nhead: {head}\n'}
        finish = lambda *a, **kw: world(**{**dict(args=['finish', '{st}', *(a or (path,))], files={**done()}, env={'KITCHEN_STEP_POLL_S': '0.05', 'KITCHEN_STEP_SLICE_S': '60'}), **kw})
        failing = lambda: {'briefs/002-y.md': self.launch_brief('002', 'y').replace('```bash\ntrue\n```', '```bash\necho before\nfalse\n```')}
        return {
            'a done report whose Verify commands pass': finish(),
            'a done report whose Verify commands pass, finished twice': finish(twice=True),
            'a done report whose Verify commands fail': finish(files={**done(), **failing()}),
            'a failing run reruns on the next finish': finish(files={**done(), **failing()}, twice=True),
            'Verify commands still running': finish(env={'KITCHEN_STEP_POLL_S': '0.05', 'KITCHEN_STEP_SLICE_S': '0'}, skip=['steps/jobs/']),
            'a partial report goes straight on': finish(files={'reports/002-y.md': '# R\n\nstatus: partial\nhead: {k1}\n'}),
            'a done report for another unit': finish('{st}/reports/001-x.md', files={'reports/001-x.md': '# R\n\nstatus: done\nhead: {u1}\n'}),
            'a done report with a head that is a placeholder': finish(files=done('<sha>')),
            'a done report with no head': finish(files={'reports/002-y.md': '# R\n\nstatus: done\n'}),
            'a head that is not a commit': finish(files=done('f' * 40)),
            'a checkout that is not at the head': finish(files=done('{u2}')),
            'a dirty checkout': finish(gitops=[*self.LAUNCH_OPS, ('write', 'src/app.py', 'y = 2\n')]),
            'an untracked file in the checkout': finish(gitops=[*self.LAUNCH_OPS, ('write', 'scratch.txt', 'y\n')]),
            'an untracked file and a brief that does not commit': finish(gitops=[*self.LAUNCH_OPS, ('write', 'scratch.txt', 'y\n')],
                                                                         files={**done(), 'briefs/002-y.md': self.launch_brief('002', 'y').replace('commit: yes', 'commit: no')}),
            'a checkout whose index git cannot read': finish(gitops=[*self.LAUNCH_OPS, ('write', '.git/index', 'garbage')]),
            'a brief stamped with a kitchen snapshot': finish(topfiles={'snap.toml': (self.LAUNCH_TOML, 0o644)},
                                                            files={**done(), 'briefs/002-y.md': self.launch_brief('002', 'y', 'kitchen: {T}/snap.toml\n')}),
            'no report file': finish(files={}),
            'one argument': finish('{st}'),
            'a report the pair would withhold': finish(files={**done(), 'notes/002-y-n1.md': '# N\n\nstatus: blocking\n'}),
            'no pair.json': finish(nopair=True),
            'outside herdr': finish(env={'HERDR_ENV': '0', 'KITCHEN_STEP_SLICE_S': '60'}),
        }

    def test_finish_matches_the_bash_at_the_phase_4_base(self):
        cases = self.finish_cases()
        sent = (0, 'prompted master m1')
        expect = {
            'a done report whose Verify commands pass': (0, 'verify: pass (1 commands from the brief)'),
            'a done report whose Verify commands pass, finished twice': (0, 'verify: pass (1 commands from the brief)'),
            'a done report whose Verify commands fail': (2, 'verify: FAIL (2 commands from the brief)'),
            'a failing run reruns on the next finish': (2, 'next: the master did not get this report'),
            'Verify commands still running': (4, "verify: the brief's Verify commands are still running at"),
            'a partial report goes straight on': sent,
            'a done report for another unit': sent,
            'a done report with a head that is a placeholder': (1, "a done report needs the report template's header"),
            'a done report with no head': (1, "a done report needs the report template's header"),
            'a head that is not a commit': (1, 'is not a commit; name the unit\'s last commit and finish again'),
            'a checkout that is not at the head': (1, "the checkout is at"),
            'a dirty checkout': (1, "the checkout holds work the unit's head does not:\n M src/app.py"),
            'an untracked file in the checkout': (1, '?? scratch.txt'),
            'an untracked file and a brief that does not commit': (0, 'verify: pass'),
            'a brief stamped with a kitchen snapshot': (0, 'verify: pass'),
            'a checkout whose index git cannot read': (128, 'index file smaller than expected'),
            'no report file': (1, 'the checkout is at'),
            'one argument': (1, 'report not found'),
            'a report the pair would withhold': (7, 'blocking review notes are still open on 002-y'),
            'no pair.json': (1, 'no pair.json in'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.phase4_cases('fin', cases, expect)
        events = lambda name: [e.split('\t')[:2] for e in out[name]['events']]
        self.assertIn(['sidekick', 'finish-verify-pass'], events('a done report whose Verify commands pass'))
        self.assertIn(['sidekick', 'finish-verify-fail'], events('a done report whose Verify commands fail'))
        # a failure is not kept: the second finish of a failing report runs the commands again and fails again; a pass is kept for that head and block
        self.assertEqual(out['a failing run reruns on the next finish']['second'][0], 2)
        self.assertEqual(events('a done report whose Verify commands pass, finished twice').count(['sidekick', 'finish-verify-pass']), 2)
        self.assertEqual(out['a done report whose Verify commands pass, finished twice']['second'][0], 0)
        # git failing in the dirty check ends finish at once: no job directory, no Verify run, no prompt to the master
        self.assertFalse([name for name in out['a checkout whose index git cannot read']['files'] if name.startswith('steps/jobs')])
        self.assertNotIn('prompted', out['a checkout whose index git cannot read']['stdout'])

    def quiet_wait_cases(self):
        T = int(time.time())
        at = lambda minutes: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(T - minutes * 60))
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': T + 3600}
        pair = lambda minutes=20, **kw: {'sent': sent, 'pending': ['{st}/briefs/001-x.md'], 'dispatch': {'brief': '{st}/briefs/001-x.md', 'at': at(minutes), 'head': '{c3}'}, **kw}
        brief = lambda risk='routine', box=45: {'briefs/001-x.md': f'# Brief\n\nrisk: {risk}\ntimebox: {box}\n\n## Scope\n\nmay write:\n- src/**\n\nmust not write:\n- lib/**\n'}
        fresh = {'progress/001-x.md': '10:00 one\n'}
        steps = {'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n2\t{c2}\t6\t\ttwo\n'}
        env = {'KITCHEN_QUIET_POLL_S': '0.0123', 'KITCHEN_QUIET_MAX_S': '3600'}
        working = {'s1': 'working claude'}
        # every poll's sleep moves the clock file on 1500 s, so the polls a wait makes before --max ends it are a fixed number
        clock = dict(topfiles={'clock': ('1000\n', 0o644)}, envfill={'KITCHEN_QUIET_CLOCK': '{T}/clock'}, tick=1500)
        base = dict(args=['wait', '{st}', '--timeout', '0'], pair=pair(), agents=working, files={**brief(), **fresh}, env=env, **clock,
                    mask=self.SETUP_MASK + [(r'<SK>[^\s"\\]*kitchen\.sh: line [0-9]+: 1 2 - start : arithmetic syntax error in expression \(error token is \\?"2 - start \\?"\)', r'pair.sh: 1 2: arithmetic syntax error in expression (error token is \\"2\\")'), (r'elapsed [0-9]+m', 'elapsed Nm'), (r'last -?[0-9]+m ago', 'last Nm ago'), (r'quiet: [0-9]+m', 'quiet: Nm')])

        def run(*a, **kw):
            spec = {**base, 'args': ['wait', '{st}', *(a if '--timeout' in a else ('--timeout', '0', *a))], **kw}
            # a log written in the future is never stale (--timeout 0 makes the check-in interval 0)
            if 'progress/001-x.md' in spec['files']:
                spec['mtimes'] = {'progress/001-x.md': T + 7200, **spec.get('mtimes', {})}
            return spec
        done = {'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}
        return {
            'a report at once': run(pair={'sent': {**sent, 'epoch': 1}}, files={**done, **brief()}),
            'a done report whose verify has not passed': run(pair={'sent': {**sent, 'epoch': 1}, 'dispatch': {'brief': '{st}/briefs/001-x.md'}}, files={**done, **brief()}),
            'steps wake an escalated brief': run(files={**steps, **fresh, **brief('escalated (a contract)')}),
            'steps do not wake a routine brief': run(files={**steps, **fresh, **brief()}),
            'a blocked sidekick': run(agents={'s1': 'blocked claude'}),
            'a quiet check-in, then max runs out': run('--max', '1'),
            'a quiet check-in, then the default max': run(env={**env, 'KITCHEN_QUIET_MAX_S': '2000'}),
            'a quiet check-in followed by a report': run(report_after='3 {st}/reports/001-x.md partial'),
            'a stale log': run(mtimes={'progress/001-x.md': 1000}),
            'no log at all': run(files=brief()),
            'a write outside Scope': run(gitops=[('write', 'lib/out.py', 'x')]),
            'a steer objected to': run(files={**brief(), **fresh, 'steers/001-x-s1.md': '# S\n', 'reports/001-x-s1.md': '# R\n\nstatus: object\n'}),
            'a blocking note open': run(files={**brief(), **fresh, **steps, 'notes/001-x-n1.md': '# N\n\nstatus: blocking\n'}),
            'a timebox overrun': run(pair=pair(70), files={**brief(box=10), **fresh}),
            'a paused store': run(files={**brief(), **fresh, 'paused': 'lunch\n'}),
            'an unowned first claim': run(report_after='3 {st}/reports/001-x.md partial', files={**brief(), **fresh, '.pair.lock/hold': ''}, env={**env, 'PAIR_LOCK_TRIES': '1', 'PAIR_LOCK_POLL_S': '0'},
                                          once={'wait': 'rm -rf "$FAKE/../st/.pair.lock"\n'}),
            'a wait a newer wait supersedes': run(once={'wait': 'printf "other\\n" >"$FAKE/../st/wait.pid"\n'}),
            'an inner wait that refuses': run('--bogus'),
            '--max with an expression': run('--max', '1+1'),
            '--max with no value': run('--max'),
            '--max that is a name': run('--max', 'abc'),
            '--max given twice': run('--max', '1', '--max', '2'),
            'a max that is no number': run(env={**env, 'KITCHEN_QUIET_MAX_S': 'abc'}),
            'a clock file that is not a number': run(topfiles={'clock': ('1 2\n', 0o644)}),
            'no store argument': dict(base, args=['wait']),
            'a store with no pair.json': run(nopair=True),
            'outside herdr': run(env={**env, 'HERDR_ENV': '0'}),
        }

    def test_the_quiet_wait_matches_the_bash_at_the_phase_4_base(self):
        cases = self.quiet_wait_cases()
        quiet = (4, 'quiet: Nm with nothing flagged; the sidekick works on')
        expect = {
            'a report at once': (4, "written but not ready: the brief's Verify commands have not passed"),
            'a done report whose verify has not passed': (4, "written but not ready: the brief's Verify commands have not passed"),
            'steps wake an escalated brief': (0, 'steps: 2 to review on 001-x'),
            'steps do not wake a routine brief': quiet,
            'a blocked sidekick': (3, 'state: blocked'),
            'a quiet check-in, then max runs out': quiet,
            'a quiet check-in, then the default max': quiet,
            'a quiet check-in followed by a report': (0, 'report_status: partial'),
            'a stale log': (4, 'STALE'),
            'no log at all': (4, 'progress: none yet'),
            'a write outside Scope': (4, 'outside scope: 1'),
            'a steer objected to': (4, '1 objected'),
            'a blocking note open': (4, '1 blocking notes open'),
            'a timebox overrun': (4, 'elapsed Nm of 10m'),
            'a paused store': (4, 'paused: lunch'),
            'an unowned first claim': (0, 'could not claim wait.pid'),
            'a wait a newer wait supersedes': (4, 'superseded: a newer wait owns this store'),
            'an inner wait that refuses': (1, 'unknown option --bogus'),
            '--max with an expression': quiet,
            '--max with no value': (1, '$2: unbound variable'),
            '--max that is a name': (1, 'abc: unbound variable'),
            '--max given twice': quiet,
            'a max that is no number': (4, '[: abc: integer expected'),
            'a clock file that is not a number': (1, 'arithmetic syntax error'),
            'no store argument': (1, 'usage: kitchen.sh wait <store>'),
            'a store with no pair.json': (2, 'no pair.json in'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.phase4_cases('qw', cases, expect)
        # the quiet wait polls (sleep moves the clock 1500 s each time) until --max ends it, then names the minutes it was quiet
        self.assertIn('quiet: Nm', out['--max given twice']['stdout'])

    def test_a_bash_quiet_wait_and_a_python_one_refuse_each_others_live_claim(self):
        """Compatibility across the port: a quiet wait of one build is running (its pid in wait.pid), and a wait of the other build starts on the same
        store. The newer one owns the store, and the older one, reading a pid that is not its own, ends with `superseded` as it would for its own
        build's newer wait. The newer wait is held inside its first herdr wait until the older one has ended, so the order is fixed."""
        cases = self.quiet_wait_cases()
        bases = self.phase4_skills()
        for older, newer, label in ((bases, root / 'skills', 'bash-then-python'), (root / 'skills', bases, 'python-then-bash')):
            spec = {k: v for k, v in cases['steps do not wake a routine brief'].items() if k not in ('tick', 'topfiles', 'envfill')}
            spec = {**spec, 'repo_files': {'.agents/kitchen.toml': self.KITCHEN_TOML}, 'env': {**spec['env'], 'KITCHEN_QUIET_POLL_S': '0.05'}, 'args': ['wait', '{st}', '--timeout', '0']}
            top, st, env, fill = self.ab_world(f'qwcompat-{label}', spec)
            args = [fill(a) for a in spec['args']]
            run = lambda skills, env, **kw: subprocess.Popen([str(skills / 'pstack-kitchen/scripts/kitchen.sh'), *args], cwd=top / 'repo', env=env, text=True,
                                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
            first = run(older, env)
            self.addCleanup(first.kill)
            for _ in range(600):
                if (st / 'wait.pid').exists():
                    break
                time.sleep(0.05)  # kitchen-allow: test-sleep (a bounded wait for the older wait's claim file; a passing run leaves at once)
            self.assertTrue((st / 'wait.pid').exists(), f'{label}: the older wait never claimed the store')
            held = top / 'held'
            held.mkdir()
            gate = top / 'older-ended'
            os.mkfifo(gate)
            # the newer wait's first herdr wait blocks reading the fifo until the test opens it for writing, once the older wait has ended
            (held / 'herdr').write_text(f'#!/bin/sh\nif [ "$1 $2" = "agent wait" ] && [ ! -e "{top}/released" ]; then cat "{gate}" >/dev/null; : >"{top}/released"; fi\nexec {root / "tests/pstack-pair/bin/herdr"} "$@"\n')
            (held / 'herdr').chmod(0o755)
            second = run(newer, {**env, 'PATH': f'{held}:{env["PATH"]}', 'KITCHEN_QUIET_MAX_S': '0'})
            self.addCleanup(second.kill)
            out, err = first.communicate(timeout=60)
            with open(gate, 'w'):
                pass
            newer_out, newer_err = second.communicate(timeout=60)
            self.assertEqual((first.returncode, 'superseded: a newer wait owns this store' in out), (4, True), f'{label}: {out}{err}')
            self.assertEqual(second.returncode, 4, f'{label}: {newer_out}{newer_err}')
            self.assertIn('check-in:', newer_out, label)
            self.assertNotIn('superseded', newer_out, label)
            self.assertFalse((st / 'wait.pid').exists(), f'{label}: the newer wait gives its claim back')

    def classify_cases(self):
        world = self.launch_world
        brief = self.launch_brief
        classify = lambda *a, **kw: world(args=['classify', '{st}', *a], **kw)
        path = '{st}/briefs/001-x.md'
        scoped = lambda *entries, extra='': brief('001', 'x', extra).replace('- src/**\n', ''.join(f'- {e}\n' for e in entries))
        plan = lambda risk: f'# Plan\n\nrisk: {risk}\nscale: small\n'
        host = ('home/.config/pstack/kitchens/github.com__Acme__Widget.toml', (self.LAUNCH_TOML, 0o644))
        return {
            'a routine brief': classify(path),
            'a brief that says risk: escalated': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated')}),
            'a brief whose reason after the class is kept': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated (contract X)')}),
            'a routine brief with a reason kept': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: routine (small)')}),
            'a brief whose class changes': classify(path, files={'briefs/001-x.md': scoped('contracts/**')}),
            'a brief with no risk line': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine\n', '')}),
            'a brief under an escalated plan': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('plan: none', 'plan: {st}/plans/001-p.md'), 'plans/001-p.md': plan('escalated (new contract)')}),
            'a brief under a routine plan': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('plan: none', 'plan: {st}/plans/001-p.md'), 'plans/001-p.md': plan('routine')}),
            'a brief under a plan that is not there': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('plan: none', 'plan: {st}/plans/none.md')}),
            'an escalated brief with a live consultant': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated')},
                                                                 pair={'consultant': {'name': 'c1', 'pane_id': 'p2'}}, agents={'m1': 'idle claude', 'c1': 'idle codex'}),
            'an escalated brief with a consultant that is gone': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated')},
                                                                         pair={'consultant': {'name': 'c1', 'pane_id': 'p2'}}),
            'an escalated brief with a consultant that is a string': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated')},
                                                                             pair={'consultant': 'c1'}),
            'a pair.json that is an array and an escalated brief': classify(path, nopair=True, files={'pair.json': '[]\n', 'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated')}),
            'a pair.json that does not parse and an escalated brief': classify(path, nopair=True, files={'pair.json': '{{\n', 'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: escalated')}),
            'a risk word with a no-break space after it': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: routine\u00a0(x)')}),
            'a brief whose Scope lists several paths': classify(path, files={'briefs/001-x.md': scoped('src/a.py', 'docs/guide.md', 'tests/**')}),
            'a brief whose Scope is outside the repo': classify(path, files={'briefs/001-x.md': scoped('/tmp/outside/file.txt')}),
            'a brief whose Scope matches no profile': classify(path, files={'briefs/001-x.md': scoped('nodir/new.py')}),
            'a brief with an empty Scope': classify(path, files={'briefs/001-x.md': scoped()}),
            'a brief that is a landing brief': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('playbook: feature', 'playbook: shipping')}),
            'a host kitchen takes a snapshot': classify(path, toml=None, topfiles=dict([host]), gitops=[*self.LAUNCH_OPS, ('git', 'remote', 'add', 'origin', 'git@github.com:Acme/Widget.git')]),
            'a brief stamped from a host kitchen, now under a repo kitchen': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: routine\nkitchen: /tmp/old.toml')}),
            'a brief with a kitchen line and a repo kitchen': classify(path, files={'briefs/001-x.md': brief('001', 'x').replace('risk: routine', 'risk: routine\nkitchen: repo')}),
            'a kitchen that does not parse': classify(path, toml='version = 1\n[profile.app]\npaths = 3\n'),
            'no kitchen at all': classify(path, toml=None, mask=[(r'repo-[0-9a-f]{8}', 'repo-H')]),
            'a brief that is not there': classify('{st}/briefs/009-none.md'),
            'a brief given relative to the store': classify('briefs/001-x.md'),
            'too few arguments': world(args=['classify', '{st}']),
            'too many arguments': classify(path, 'x'),
            'no pair.json': classify(path, nopair=True),
            'a pair.json that is an array': classify(path, nopair=True, files={'pair.json': '[]\n'}),
            'a git_root that is not a repository': classify(path, pair={'git_root': '/nonexistent/repo'}),
        }

    def test_classify_matches_the_bash_at_the_phase_4_base(self):
        cases = self.classify_cases()
        brief = 'briefs/001-x.md'
        expect = {
            'a routine brief': (0, 'risk: routine\nprofiles: app\nverify: batch by routine verifier\n'),
            'a brief that says risk: escalated': (0, 'escalate: the brief says risk: escalated'),
            'a brief whose reason after the class is kept': (0, 'risk: escalated\n'),
            'a routine brief with a reason kept': (0, 'risk: routine\n'),
            'a brief whose class changes': (0, 'escalate: escalate path: contracts/api.json'),
            'a brief with no risk line': (0, 'risk: routine\n'),
            'a brief under an escalated plan': (0, 'escalate: escalated plan: 001-p.md'),
            'a brief under a routine plan': (0, 'risk: routine\n'),
            'a brief under a plan that is not there': (0, 'risk: routine\n'),
            'an escalated brief with a live consultant': (0, 'escalate: the brief says risk: escalated'),
            'an escalated brief with a consultant that is gone': (0, 'next: kitchen.sh consultant <T>/st --reason "the brief says risk: escalated", then a plan round with it'),
            'an escalated brief with a consultant that is a string': (0, 'Cannot index string with string ("name")'),
            'a pair.json that is an array and an escalated brief': (0, 'next: kitchen.sh consultant <T>/st --reason "the brief says risk: escalated", then a plan round with it'),
            'a pair.json that does not parse and an escalated brief': (0, 'next: kitchen.sh consultant <T>/st --reason "the brief says risk: escalated", then a plan round with it'),
            'a risk word with a no-break space after it': (0, 'risk: routine\n'),
            'a brief whose Scope lists several paths': (0, 'profiles: app, docs'),
            'a brief whose Scope is outside the repo': (0, 'bookkeeping: the Scope is outside the repo; gates only, nothing to escalate'),
            'a brief whose Scope matches no profile': (0, 'escalate: 1 files no profile covers: nodir/new.py'),
            'a brief with an empty Scope': (1, "lists nothing under Scope's may write:"),
            'a brief that is a landing brief': (0, 'risk: routine\n'),
            'a host kitchen takes a snapshot': (0, 'risk: routine\n'),
            'a brief stamped from a host kitchen, now under a repo kitchen': (0, 'risk: routine\n'),
            'a brief with a kitchen line and a repo kitchen': (0, 'risk: routine\n'),
            'a kitchen that does not parse': (1, 'kitchen.py snapshot failed; run kitchen.py validate in the repo'),
            'no kitchen at all': (1, 'kitchen: no kitchen at <T>/repo/.agents/kitchen.toml or'),
            'a brief that is not there': (1, 'brief not found: <T>/st/briefs/009-none.md'),
            'a brief given relative to the store': (1, 'brief not found: briefs/001-x.md'),
            'too few arguments': (1, 'usage: kitchen.sh classify <store> <brief-path>'),
            'too many arguments': (1, 'usage: kitchen.sh classify <store> <brief-path>'),
            'no pair.json': (1, 'no pair.json in'),
            'a pair.json that is an array': (0, 'Cannot index array with string ("git_root")'),
            'a git_root that is not a repository': (1, 'kitchen: not a git repository: /nonexistent/repo'),
        }
        out = self.phase4_cases('classify', cases, expect)
        self.assertIn('risk: escalated', out['a brief whose reason after the class is kept']['files'][brief])
        self.assertIn('risk: escalated (contract X)', out['a brief whose reason after the class is kept']['files'][brief])
        self.assertIn('risk: routine (small)', out['a routine brief with a reason kept']['files'][brief])
        changed = out['a brief whose class changes']['files'][brief]
        self.assertRegex(changed, r'(?m)^risk: escalated$')
        self.assertIn('profiles: contracts', changed)
        self.assertIn('verify: unit by escalated verifier', changed)
        self.assertRegex(out['a brief with no risk line']['files'][brief], r'(?m)^profiles: app$')
        host = out['a host kitchen takes a snapshot']
        self.assertRegex(host['files'][brief], r'(?m)^kitchen: <T>/st/kitchens/[0-9a-f]+\.toml$')
        self.assertTrue(any(name.startswith('kitchens/') and name.endswith('.toml') for name in host['files']))
        self.assertRegex(out['a brief stamped from a host kitchen, now under a repo kitchen']['files'][brief], r'(?m)^kitchen: repo$')
        self.assertNotIn('kitchen:', out['a routine brief']['files'][brief])

    def test_classify_kpy_calls_leave_the_environment_alone(self):
        """classify reaches kitchen.py's snapshot, `--json classify` and `lessons stamp` in process; none of them writes os.environ (the writes are in
        `gate` and `brief-verify`, kitchen.py :745, :771, :1023, :1038)."""
        kpy = self.kitchen_core_module('kpy')
        top = self.path / 'kpycls'
        repo = top / 'repo'
        (repo / '.agents').mkdir(parents=True)
        (repo / '.agents/kitchen.toml').write_text(self.LAUNCH_TOML)
        env = {**self.env, **self.GIT}
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-q', '-m', 'c1']):
            subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True)
        brief = top / 'brief.md'
        brief.write_text(self.launch_brief('001', 'x'))
        before = dict(os.environ)
        with mock.patch.dict(os.environ, {'HOME': str(top), 'XDG_STATE_HOME': str(top / 'state')}):
            snapshot = dict(os.environ)
            for args in (['snapshot', str(top / 'kitchens')], ['--json', 'classify', '--paths', 'src/**'], ['lessons', 'stamp', str(brief)]):
                self.assertEqual(kpy.run(str(repo), args)[0], 0, args)
                self.assertEqual(dict(os.environ), snapshot, args)
        self.assertEqual(dict(os.environ), before)

    def test_a_child_killed_by_a_signal_has_the_status_a_shell_gives_it(self):
        """A `$( )` whose command is killed by signal N gives 128+N; waitpid and subprocess say -N, which `sys.exit` would turn into 256-N."""
        verify, jqx = self.kitchen_core_module('verify'), self.kitchen_core_module('jqx')
        for name, number, want in (('TERM', signal.SIGTERM, 143), ('KILL', signal.SIGKILL, 137), ('HUP', signal.SIGHUP, 129)):
            bash = subprocess.run(['bash', '-c', f'out="$(sh -c "kill -{name} \\$\\$")"; echo $?'], capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(bash, str(want))
            code, out = verify.in_subshell(lambda: os.kill(os.getpid(), number))
            self.assertEqual((code, out), (want, ''), name)
            sub = subprocess.run(['sh', '-c', f'kill -{name} $$'], check=False)
            self.assertEqual(jqx.exit_status(sub.returncode), want, name)
        self.assertEqual([jqx.exit_status(n) for n in (0, 1, 4, 255)], [0, 1, 4, 255])

    def test_step_kpy_call_leaves_the_environment_alone(self):
        """step reaches kitchen.py's `--json classify --base --head` in process (the gates, the policy check and brief-verify run in the detached scripts, as
        `python3 kitchen.py` children, so what they write to os.environ is theirs)."""
        kpy = self.kitchen_core_module('kpy')
        top = self.path / 'kpystep'
        repo = top / 'repo'
        (repo / '.agents').mkdir(parents=True)
        (repo / '.agents/kitchen.toml').write_text(self.LAUNCH_TOML)
        env = {**self.env, **self.GIT}
        shas = []
        for name in ('src/app.py', 'src/b.py'):
            (repo / name).parent.mkdir(exist_ok=True)
            (repo / name).write_text('x = 1\n')
            for args in (['add', '-A'], ['commit', '-q', '-m', name]) if shas else (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-q', '-m', name]):
                subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True)
            shas.append(subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], env=env, check=True, capture_output=True, text=True).stdout.strip())
        before = dict(os.environ)
        with mock.patch.dict(os.environ, {'HOME': str(top), 'XDG_STATE_HOME': str(top / 'state')}):
            snapshot = dict(os.environ)
            status, out, _ = kpy.run(str(repo), ['--json', 'classify', '--base', shas[0], '--head', shas[1]])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(out)['profiles'], ['app'])
            self.assertEqual(dict(os.environ), snapshot)
        self.assertEqual(dict(os.environ), before)

    def test_setup_kpy_calls_leave_the_environment_alone(self):
        """init, spawn, consultant and fleet reach kitchen.py's `--json roster`, `--json validate`, `setup --role sidekick` and `fleet` in process; none
        of them writes os.environ (the writes are in `gate` and `brief-verify`, kitchen.py :745, :771, :1023, :1038). `setup` runs the repo's commands
        as a child process with a copy of the environment."""
        kpy = self.kitchen_core_module('kpy')
        top = self.path / 'kpyset'
        repo = top / 'repo'
        (repo / '.agents').mkdir(parents=True)
        (repo / '.agents/kitchen.toml').write_text(self.LAUNCH_TOML + '\n[scratch]\nsetup = ["true"]\n')
        env = {**self.env, **self.GIT}
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-q', '-m', 'c1']):
            subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True)
        before = dict(os.environ)
        with mock.patch.dict(os.environ, {'HOME': str(top), 'XDG_STATE_HOME': str(top / 'state')}):
            snapshot = dict(os.environ)
            for repo_arg, args in ((None, ['--json', 'roster']), (str(repo), ['--json', 'validate']), (str(repo), ['setup', '--role', 'sidekick']), (None, ['fleet'])):
                self.assertEqual(kpy.run(repo_arg, args)[0], 0, args)
                self.assertEqual(dict(os.environ), snapshot, args)
        self.assertEqual(dict(os.environ), before)

    def test_verify_matches_the_bash_at_the_phase_4_base(self):
        cases = self.launch_cases()
        started = 'verifier v1 ('
        expect = {
            'a routine unit': (4, started + 'claude (model unspecified)) in p1 on'),
            'a batch of two units': (4, 'verifying: units 001 002 by claude'),
            'a fix covering the unit before it': (4, 'verifying: units 001 002 by claude'),
            'covers as a comma list': (4, 'verifying: units 001 002 003 004 by claude'),
            'a covered unit whose head the checked-out head does not contain': (2, "unit 002's head "),
            'a unit dispatched as escalated': (4, started + 'claude --model claude-opus-5-5) in p1 on'),
            'a unit whose diff touches the contracts': (4, started + 'claude --model claude-opus-5-5) in p1 on'),
            'a unit the docs profile verifies by gates': (0, 'status: clean (gates only; mode gates)'),
            '--kind codex': (4, started + 'codex --disable plugins) in p1 on'),
            '--kind claude': (4, started + 'claude (model unspecified)) in p1 on'),
            '--kind pi, whose session is a path': (4, started + 'pi (model unspecified)) in p1 on'),
            '--timeout': (4, 'deadline 20m (--timeout)'),
            'a tab launch, whose env words reach tab create': (4, started),
            'a tab launch whose master is a string, with a live sidekick pane': (5, 'Cannot index string with string ("pane_id")'),
            '--timeout as the sum 1+1': (4, 'deadline 1+1m (--timeout)'),
            '--timeout as the product 2*15': (4, 'deadline 2*15m (--timeout)'),
            '--timeout in octal': (4, 'deadline 010m (--timeout)'),
            'a roster that does not parse, with --kind': (2, 'Traceback'),
            '--base': (4, started),
            '--base that is no commit': (1, '--base nothere is not a commit'),
            'a unit with a recorded base behind where its branch leaves trunk': (4, "is behind where its branch leaves remotes/origin/main; measuring from"),
            'the landing': (4, 'landing base: merge-base of remotes/origin/main and HEAD ('),
            'the landing from a base': (4, 'verifying: units landing by claude'),
            'the landing with a parent': (4, 'landing base: recorded parent '),
            'the landing with the parent cleared': (4, 'landing base: merge-base of remotes/origin/main and HEAD ('),
            'the landing of a head': (4, 'landing base: merge-base of remotes/origin/main and HEAD ('),
            'the landing with a parent that is the head': (1, 'must be an ancestor of'),
            'the landing with a head that is no commit': (1, '--head nothere is not a commit'),
            'a verification already open': (5, 'a verification is open (001); kitchen.sh verify'),
            'an unread clean verdict is closed first': (4, 'closed: <T>/st/verdicts/001-x-v1.md (clean)'),
            'an unread rejected verdict is closed first': (2, 'closed: <T>/st/verdicts/001-x-v1.md (reject)'),
            'a provider error on the first verifier, with the sidekick\'s fallback': (2, 'retry: verifying again on the fallback, pi'),
            'a provider error on the first verifier, with none': (2, 'next: wait for the limit, or kitchen.sh verify'),
            'a verifier that cannot start': (5, 'verifier start failed in p1'),
            'a verifier that asks whether to trust the folder': (5, 'asks whether to trust'),
            'a prompt the verifier does not take': (4, started),
            'a unit never dispatched': (1, 'unit 001 was never dispatched'),
            'a unit with no report': (1, 'unit 001 has no report'),
            'a unit whose report is not done': (1, "unit 001's report is weird, not done, partial, blocked, or failed"),
            'a unit with no brief': (1, 'no brief for unit 009'),
            'no unit and no landing': (1, 'name at least one unit NNN, or --landing'),
            'an unknown option': (1, 'unknown option --bogus'),
            '--covers with no value': (1, '--covers takes a unit NNN or a comma list'),
            '--covers that is no unit': (1, "--covers takes unit numbers (NNN), one per flag or a comma list, not 'abc'"),
            '--every and --until-verdict together': (1, '--every and --until-verdict are alternatives'),
            '--landing with a unit': (1, '--landing verifies the whole stack; name no units'),
            '--head without --landing': (1, '--head goes with --landing'),
            '--parent without --landing': (1, '--parent goes with --landing'),
            '--cancel with --wait': (1, '--cancel takes no units, --landing or --wait'),
            '--wait with nothing open': (5, 'no verification is open; start one with kitchen.sh verify'),
            'too few arguments': (1, 'usage: kitchen.sh verify <store> <NNN>...'),
            'a pair.json that is an array': (5, 'Cannot index array with string ("verifying")'),
            'a verifier that is a string': (5, 'Cannot index string with string ("name")'),
            'a heads that is a string': (1, 'Cannot index string with string ("001-x")'),
            'a master that is a string': (5, 'Cannot index string with string ("pane_id")'),
            'a sidekick that is a string': (5, 'Cannot index string with string ("kind")'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.phase4_cases('launch', cases, expect)
        routine = out['a routine unit']
        packet = routine['files']['verdicts/001-x-v1-packet.md']
        self.assertIn('verifier-outcome: running', packet)
        self.assertIn('## Unit 001', packet)
        self.assertIn('"verifying"', routine['files']['pair.json'])
        self.assertIn('--permission-mode auto --strict-mcp-config', routine['calls'])
        self.assertIn('Command 1:', packet)
        landing = out['the landing']['files']['verdicts/landing-v1-packet.md']
        self.assertIn('## Stack', landing)
        self.assertIn('- <T>/st/briefs/001-x.md', landing)
        self.assertIn('"parent":', out['the landing with a parent']['files']['pair.json'])
        self.assertNotIn('"parent_ref": "rel"', out['the landing with the parent cleared']['files']['pair.json'])
        retried = out["a provider error on the first verifier, with the sidekick's fallback"]
        self.assertEqual(retried['stdout'].count('verifier v1 ('), 2)
        self.assertIn('agent start v1 --kind pi', retried['calls'])
        self.assertIn('verifier-outcome: provider', retried['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verdict: <T>/st/verdicts/003-docs-v1.md', out['a unit the docs profile verifies by gates']['stdout'])
        self.assertIn('003-docs', out['a unit the docs profile verifies by gates']['files']['verdicts/003-docs-v1.md'])
        self.assertIn('"session":', out['--kind pi, whose session is a path']['files']['pair.json'])
        tab = out['a tab launch, whose env words reach tab create']
        self.assertRegex(tab['calls'], r'tab create --workspace w1 --cwd \S+ --label demo-verifier --no-focus --env PSTACK_KITCHEN_ROLE=verifier --env PSTACK_KITCHEN_RUN=demo')
        self.assertNotIn('tab create', out['a tab launch whose master is a string, with a live sidekick pane']['calls'])
        self.assertNotIn('"verifying"', out['a roster that does not parse, with --kind']['files']['pair.json'])
        self.assertIn('deadline', out['--timeout as the sum 1+1']['stdout'])
        self.assertIn('0m of 2m', out['--timeout as the sum 1+1']['stdout'])
        self.assertIn('0m of 30m', out['--timeout as the product 2*15']['stdout'])
        self.assertIn('0m of 8m', out['--timeout in octal']['stdout'])

    def test_a_timeout_the_shell_cannot_read_and_a_flag_with_no_value_end_verify_as_the_bash_does(self):
        """Accepted (PARITY.md): `--timeout abc` is an unbound variable and `--timeout 1+` a syntax error to `$(( ... ))`, which runs after the scratch is
        made, and `--timeout` with no value is the shell's `$2: unbound variable`; all end with status 1 and the same store and calls. The shell names a
        line of the old kitchen.sh, we name the word."""
        cases = ((['001', '--timeout', 'abc', '--every', '0'], r'abc: unbound variable', 'abc: unbound variable'),
                 (['001', '--timeout', '1+', '--every', '0'], r'1\+: arithmetic syntax error: operand expected', '1+: arithmetic syntax error: operand expected'),
                 (['001', '--timeout'], r'\$2: unbound variable', '$2: unbound variable'))
        for n, (args, pattern, ours) in enumerate(cases):
            got, want = self.phase4_ab(f'vtimeout{n}', self.launch_world(args=['verify', '{st}', *args]))
            self.assertEqual({k: v for k, v in got.items() if k != 'stderr'}, {k: v for k, v in want.items() if k != 'stderr'})
            self.assertEqual(got['rc'], 1)
            self.assertIn(ours, got['stderr'])
            self.assertRegex(want['stderr'], rf'kitchen\.sh: line [0-9]+: {pattern}')

    def test_a_pane_that_will_not_split_ends_verify_with_the_core_s_message_where_the_bash_ended_silently(self):
        """Accepted (PARITY.md, D8): `new_pane_id="$(herdr pane split ... | jq ...)"` under errexit ended the bash with status 1 and no message when herdr
        failed; the core's new_pane dies with status 2 and says no pane id came back. The store, the files and the calls up to there are equal."""
        for n, fake in enumerate(({'split-fail': ''}, {'split-null': ''})):
            got, want = self.phase4_ab(f'vsplit{n}', self.launch_world(fakefiles=fake))
            self.assertEqual({k: v for k, v in got.items() if k not in ('stderr', 'rc')}, {k: v for k, v in want.items() if k not in ('stderr', 'rc')})
            self.assertEqual(got['rc'], 2)
            self.assertIn('pane split returned no pane id', got['stderr'])
            self.assertEqual(want['rc'], 2 if 'split-null' in fake else 1)

    def test_a_bash_waiter_and_a_python_closer_and_the_reverse_share_one_verification(self):
        """A6. A waiter of one tree is mid-wait (its first `agent get`) when the other tree cancels the verification, as another call would, and the
        next attempt's `.verifying` is written: the waiter says `verification closed elsewhere`, exits 5, and touches neither the new attempt nor the
        panes. The cancel runs from the fake herdr's once hook, so the interleaving is fixed, with no sleeps."""
        bases = self.phase4_skills() / 'pstack-kitchen/scripts/kitchen.sh'
        head = root / 'skills/pstack-kitchen/scripts/kitchen.sh'
        for n, (waiter, closer, label) in enumerate(((bases, head, 'a bash waiter and a Python closer'), (head, bases, 'a Python waiter and a bash closer'))):
            hook = ('cd "$FAKE/.." || exit 1\n'
                    f'{closer} verify "$FAKE/../st" --cancel >"$FAKE/closer.out" 2>&1\n'
                    'jq \'.verifying = {verdict: "/next/002-y-v1.md", units: "002", idle_since: 77, scratch: "002-y-v1", brief: "002-y", packet: "/next/p.md", unit: "002-y"}\' '
                    '"$FAKE/../st/pair.json" >"$FAKE/../st/pair.tmp" && mv "$FAKE/../st/pair.tmp" "$FAKE/../st/pair.json"\n')
            spec = self.wait_world('5', agents={'v1': 'working claude'}, once={'get': hook})
            spec = {**spec, 'repo_files': {'.agents/kitchen.toml': self.KITCHEN_TOML}}
            top, st, env, fill = self.ab_world(f'interop{n}', spec)
            r = subprocess.run([str(waiter), 'verify', str(st), '--wait', '--every', '5'], cwd=top / 'repo', env=env, capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual((r.returncode, r.stdout), (5, 'verification closed elsewhere\n'), (label, r.stderr))
            closer_out = (top / 'fake/closer.out').read_text()
            self.assertIn('cancelled: 001', closer_out, label)
            doc = json.loads((st / 'pair.json').read_text())
            self.assertEqual(doc['verifying']['verdict'], '/next/002-y-v1.md', label)
            self.assertEqual(doc['verifying']['idle_since'], 77, label)
            self.assertEqual((top / 'fake/calls.log').read_text().count('pane close'), 1, label)
            self.assertIn('verifier-outcome: cancelled', (st / 'verdicts/001-x-v1-packet.md').read_text(), label)

    def range_world(self, name):
        """The 025 unit world with the reports unit_range tells apart: statuses, a head that is not a commit, no head, a unit on a moved trunk."""
        st, repo, shas, stacks, recorded = self.units_world(name)
        report = lambda n, text, base=shas['base']: ((st / f'reports/{n}.md').write_text(text), (st / f'briefs/{n}.md').write_text(f'# Brief {n}\n\ncommit: yes\n'))
        heads = json.loads((st / 'pair.json').read_text())
        for n, text in {'011-partial': f'status: partial\nhead: {shas["u2"]}\n', '012-blocked': f'status: blocked\nhead: {shas["u2"]}\n',
                        '013-failed': f'status: failed\nhead: {shas["u2"]}\n', '014-nostatus': f'head: {shas["u2"]}\n', '015-weird': f'status: weird\nhead: {shas["u2"]}\n',
                        '016-onstack': f'status: done\nhead: {stacks["plain"][1]}\n', '017-nohead': 'status: done\n', '018-badhead': 'status: done\nhead: nothere\n',
                        '019-done': f'status: done\nhead: {shas["u1"]}\n'}.items():
            report(n, '# R\n\n' + text)
            heads['heads'][n] = shas['base']
        (st / 'pair.json').write_text(json.dumps(heads, indent=2) + '\n')
        return st, repo, shas, stacks

    def test_unit_range_equals_the_bash_at_the_phase_4_base(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks = self.range_world('urange')
        subprocess.run(['git', '-C', str(repo), 'checkout', '-q', 'main'], check=True, env={**self.env, **self.GIT})
        calls = [(['001'], [], ''), (['001', '002'], [], ''), (['002', '001'], [], ''), (['001', '019', '002'], [], ''), (['019', '001'], [], ''),
                 (['001', '006'], [], ''), (['001', '006'], ['006'], ''), (['006'], [], ''), (['001', '011'], [], ''), (['001', '012', '013'], [], ''),
                 (['001', '014'], [], ''), (['001', '015'], [], ''), (['001', '017'], [], ''), (['001', '018'], [], ''), (['011', '001'], ['011'], ''),
                 (['016'], [], ''), (['016', '001'], [], ''), (['001', '016'], [], ''), (['016'], [], shas['base']), (['001'], [], shas['trunk']),
                 (['001'], [], 'nothere'), (['005'], [], ''), (['009', '001'], [], ''), (['001', '010'], [], ''), (['001', '010'], ['010'], ''),
                 (['010', '001'], [], ''), (['008'], [], ''), (['001', '009'], [], '')]
        said = ''
        for n, (names, covered, range_base) in enumerate(calls):
            script = (f'covered=({" ".join(covered)}); range_base={range_base!r}; ( unit_range "$STORE" {" ".join(names)} ); echo "rc=$?"')
            rc, want, err = self.phase4_bash(st, script, cwd=repo)
            py = ('try:\n    base, head = units.unit_range(store, %r, %r, %r)\n    print(base, head)\n    code = 0\nexcept Die as e:\n    sys.stderr.write("pair.sh: %%s\\n" %% e.message)\n'
                  '    code = e.code\nprint("rc=%%d" %% code)' % (names, covered, range_base))
            prc, got, perr = self.kitchen_py(st, py, env={'PAIR_SHELL_PID': str(os.getpid())}, cwd=repo)
            self.assertEqual((got, perr), (want, err.replace('kitchen.sh: ', 'pair.sh: ')), (names, covered, range_base))
            said += want + err
        for text in ('rc=1', 'rc=0', 'was never dispatched', 'has no report', 'has no status:', 'not done, partial, blocked, or failed', 'has no head:', 'is not a commit',
                     "report is partial; checking the head it names", 'is behind where its branch leaves', 'no brief for unit'):
            self.assertIn(text, said)

    def test_landing_base_equals_the_bash_at_the_phase_4_base(self):
        st, repo, shas, stacks = self.range_world('ulbase')
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], check=True, env={**self.env, **self.GIT}, capture_output=True, text=True).stdout.strip()
        git('checkout', '-q', 'plain')
        tip, trunk = stacks['plain'][1], shas['trunk']
        side = git('rev-parse', 'unit')
        base_doc = json.loads((st / 'pair.json').read_text())
        cases = [('no parent recorded', {}, {}), ('a recorded parent that is an ancestor', {'landing': {'parent': trunk, 'parent_ref': 'rel'}}, {}),
                 ('a recorded parent that is not an ancestor', {'landing': {'parent': side, 'parent_ref': 'rel'}}, {}),
                 ('a recorded parent that is the head', {'landing': {'parent': tip, 'parent_ref': 'rel'}}, {}),
                 ('a recorded parent that is no commit', {'landing': {'parent': 'nothere', 'parent_ref': 'rel'}}, {}),
                 ('an override that is an ancestor', {}, {'override': trunk, 'label': 'given'}),
                 ('an override that is not an ancestor', {}, {'override': side, 'label': 'given'}),
                 ('an override that is the head', {}, {'override': tip, 'label': 'given'}),
                 ('none clears a recorded parent', {'landing': {'parent': trunk, 'parent_ref': 'rel'}}, {'label': 'none'}),
                 ('--base leaves the recorded parent unread', {'landing': {'parent': trunk, 'parent_ref': 'rel'}}, {'range_base': shas['base']}),
                 ('--base with an override that is not an ancestor', {}, {'override': side, 'label': 'given', 'range_base': shas['base']}),
                 ('landing a string', {'landing': 'x'}, {}), ('landing an array', {'landing': [1]}, {}), ('a parent that is a number', {'landing': {'parent': 5, 'parent_ref': 'r'}}, {}),
                 ('a parent with no ref', {'landing': {'parent': trunk}}, {})]
        said = ''
        for n, (name, landing, opts) in enumerate(cases):
            doc = {**base_doc, **landing}
            (st / 'pair.json').write_text(json.dumps(doc, indent=2) + '\n')
            env_set = f'landing_parent_override={opts.get("override", "")!r}; landing_parent_label={opts.get("label", "")!r}; range_base={opts.get("range_base", "")!r}; '
            rc, want, err = self.phase4_bash(st, env_set + f'( set -e; landing_base {repo} {tip} "$STORE"; echo "rev=$landing_base_rev" ); echo "rc=$?"', cwd=repo)
            py = ('try:\n    rev = units.landing_base(store, %r, %r, %r, %r, %r)\n    print("rev=" + rev)\n    code = 0\nexcept Die as e:\n    sys.stderr.write("pair.sh: %%s\\n" %% e.message)\n'
                  '    code = e.code\nprint("rc=%%d" %% code)' % (str(repo), tip, opts.get('override', ''), opts.get('label', ''), opts.get('range_base', '')))
            prc, got, perr = self.kitchen_py(st, py, cwd=repo)
            self.assertEqual((got, perr), (want, err.replace('kitchen.sh: ', 'pair.sh: ')), name)
            said += want + err
        # no trunk, and a head on the trunk
        git('remote', 'add', 'origin', str(repo))
        for text in ('recorded parent rel (', 'no longer an ancestor', 'merge-base of remotes/origin/main and HEAD', 'must be an ancestor of', 'rc=1', 'rc=0'):
            self.assertIn(text, said)
        git('update-ref', '-d', 'refs/remotes/origin/main')
        git('checkout', '-q', 'main')
        (st / 'pair.json').write_text(json.dumps(base_doc, indent=2) + '\n')
        main = git('rev-parse', 'main')
        for name, head in (('a head that is the trunk', main), ('no trunk at all', None)):
            if head is None:
                git('checkout', '-q', '--detach', tip)
                git('update-ref', '-d', 'refs/heads/main')
                head = tip
            rc, want, err = self.phase4_bash(st, f'( set -e; landing_base {repo} {head} "$STORE"; echo "rev=$landing_base_rev" ); echo "rc=$?"', cwd=repo)
            py = ('try:\n    print("rev=" + units.landing_base(store, %r, %r))\n    code = 0\nexcept Die as e:\n    sys.stderr.write("pair.sh: %%s\\n" %% e.message)\n'
                  '    code = e.code\nprint("rc=%%d" %% code)' % (str(repo), head))
            prc, got, perr = self.kitchen_py(st, py, cwd=repo)
            self.assertEqual((got, perr), (want, err.replace('kitchen.sh: ', 'pair.sh: ')), name)
            self.assertIn('rc=1', got)

    LAUNCH_TOML = ('version = 1\n\n[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["true"]\nbehavioral = ["true"]\ntests = ["tests/**"]\nsample = 0.2\n\n'
                   '[profile.docs]\npaths = ["docs/**"]\nfast = ["true"]\nverify = "gates"\nsample = 0.0\n\n[profile.contracts]\npaths = ["contracts/**"]\nfast = ["true"]\nverify = "unit"\n\n'
                   '[escalate]\npaths = ["contracts/**"]\n')
    LAUNCH_OPS = [('write', 'src/app.py', 'x = 1\n'), ('write', 'tests/test_app.py', 't = 1\n'), ('commit', 'u0'), ('write', 'src/a.py', 'a = 1\n'), ('commit', 'u1'),
                  ('write', 'src/b.py', 'b = 1\n'), ('commit', 'u2'), ('write', 'docs/guide.md', '# g\n'), ('commit', 'd1'), ('write', 'contracts/api.json', '{}\n'),
                  ('commit', 'k1'), ('git', 'update-ref', 'refs/remotes/origin/main', '{u0}')]

    def launch_brief(self, n, slug, extra=''):
        return (f'# Brief {n}: {slug}\n\nplaybook: feature\ntimebox: 30\ncommit: yes\nplan: none\nrisk: routine\n{extra}\n## Goal\n\nDo {slug}.\n\n## Scope\n\nmay write:\n- src/**\n\n'
                'must not write:\n- lib/**\n\n## Acceptance\n\n- works\n\n## Verify\n\n```bash\ntrue\n```\n')

    def launch_world(self, **extra):
        """Units 001 (u0..u1), 002 (u1..u2), 003 (docs only: gates) and 004 (contracts: escalated) dispatched and done on a repo with a kitchen, a
        master in p0, a verifier role named v1, and the fake herdr; `verify <store> 001 --every 0` by default."""
        brief, pair = self.launch_brief, {'slug': 'demo', 'verifier': {'name': 'v1', 'pane_id': None}, 'master': {'name': 'm1', 'pane_id': 'p0'},
                                          'heads': {'001-x': '{u0}', '002-y': '{u1}', '003-docs': '{u2}', '004-api': '{d1}'},
                                          **extra.pop('pair', {})}
        files = {'verdicts/.keep': '', 'briefs/001-x.md': brief('001', 'x'), 'briefs/002-y.md': brief('002', 'y'), 'briefs/003-docs.md': brief('003', 'docs'),
                 'briefs/004-api.md': brief('004', 'api'),
                 'reports/001-x.md': '# R\n\nstatus: done\nhead: {u1}\n', 'reports/002-y.md': '# R\n\nstatus: done\nhead: {u2}\n',
                 'reports/003-docs.md': '# R\n\nstatus: done\nhead: {d1}\n', 'reports/004-api.md': '# R\n\nstatus: done\nhead: {k1}\n',
                 'gates.tsv': '1\t003-docs\tabc\tpass\tdocs\n', **extra.pop('files', {})}
        files = {k: v for k, v in files.items() if v is not None}
        env = {'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0', 'KITCHEN_VERIFIER_EXIT_S': '0', 'KITCHEN_VERIFY_GRACE_POLL_S': '0',
               'PSTACK_KITCHEN_LOAD': '1 8', **extra.pop('env', {})}
        return {'args': ['verify', '{st}', '001', '--every', '0'], 'toml': self.LAUNCH_TOML, 'gitops': self.LAUNCH_OPS, 'files': files, 'pair': pair,
                'agents': extra.pop('agents', {'m1': 'idle claude'}), 'panes': extra.pop('panes', {'p0': 'm1'}), 'env': env, 'master': None, **extra}

    def launch_cases(self):
        world = self.launch_world
        verify = lambda *a, **kw: world(args=['verify', '{st}', *a], **kw)
        open_one = {'verdict': '{st}/verdicts/001-x-v1.md', 'packet': '{st}/verdicts/001-x-v1-packet.md', 'brief': '001-x', 'scratch': '001-x-verify', 'units': '001', 'risk': 'routine',
                    'sample': 0, 'unit': '001-x', 'started': int(time.time()) - 60, 'deadline': int(time.time()) + 3000, 'delivered': True, 'argv': ['001']}
        done_verdict = lambda status='clean': {'verdicts/001-x-v1.md': f'# V\n\nstatus: {status}\nunits: 001\n\n## Evidence\n\n```\n$ true\n```\n\n```\nok\n```\n'}
        rate = {'v1': 'Error: rate limit exceeded, try later'}
        return {
            'a routine unit': verify('001', '--every', '0'),
            'a batch of two units': verify('001', '002', '--every', '0'),
            'a fix covering the unit before it': verify('002', '--covers', '001', '--every', '0'),
            'covers as a comma list': verify('004', '--covers', '001,002,003', '--every', '0'),
            'a covered unit whose head the checked-out head does not contain': verify('001', '--covers', '002', '--every', '0'),
            'a unit dispatched as escalated': world(args=['verify', '{st}', '001', '--every', '0'], files={'briefs/001-x.md': self.launch_brief('001', 'x').replace('risk: routine', 'risk: escalated')}),
            'a unit whose diff touches the contracts': verify('004', '--every', '0'),
            'a unit the docs profile verifies by gates': verify('003', '--every', '0'),
            '--kind codex': verify('001', '--kind', 'codex', '--every', '0'),
            '--kind claude': verify('001', '--kind', 'claude', '--every', '0'),
            '--kind pi, whose session is a path': verify('001', '--kind', 'pi', '--every', '0', fake={'sessions/v1': '/tmp/pi-session.jsonl'}),
            '--timeout': verify('001', '--timeout', '20', '--every', '0'),
            '--base': verify('001', '--base', '{u0}', '--every', '0'),
            '--base that is no commit': verify('001', '--base', 'nothere', '--every', '0'),
            'a unit with a recorded base behind where its branch leaves trunk': world(args=['verify', '{st}', '002', '--every', '0'], pair={'heads': {'002-y': '{c1}'}}),
            'the landing': verify('--landing', '--every', '0'),
            'the landing from a base': verify('--landing', '--base', '{u0}', '--every', '0'),
            'the landing with a parent': verify('--landing', '--parent', '{u1}', '--every', '0'),
            'the landing with the parent cleared': verify('--landing', '--parent', 'none', '--every', '0', pair={'landing': {'parent': '{u0}', 'parent_ref': 'rel'}}),
            'the landing of a head': verify('--landing', '--head', '{k1}', '--every', '0'),
            'the landing with a parent that is the head': verify('--landing', '--parent', 'HEAD', '--every', '0'),
            'the landing with a head that is no commit': verify('--landing', '--head', 'nothere', '--every', '0'),
            'a verification already open': verify('002', '--every', '0', pair={'verifying': open_one}),
            'an unread clean verdict is closed first': verify('002', '--every', '0', files=done_verdict(), pair={'verifying': open_one}),
            'an unread rejected verdict is closed first': verify('002', '--every', '0', files=done_verdict('reject'), pair={'verifying': open_one}),
            'a provider error on the first verifier, with the sidekick\'s fallback': verify('001', '--every', '5', screens=rate,
                                                                                       pair={'sidekick': {'kind': 'claude', 'name': 's1', 'generation': 1, 'fallback': {'kind': 'pi', 'args': ['--model', 'm']}}}),
            'a provider error on the first verifier, with none': verify('001', '--every', '5', screens=rate),
            'a tab launch, whose env words reach tab create': verify('001', '--every', '0', pair={'placement': 'tab', 'master': {'name': 'm1', 'pane_id': 'w1:p0'}}),
            'a tab launch whose master is a string, with a live sidekick pane': verify('001', '--every', '0', agents={'m1': 'idle claude', 's1': 'idle claude'}, panes={'p0': 'm1', 'p1': 's1'},
                                                                                    pair={'placement': 'tab', 'master': 'x', 'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p1'}}),
            'a roster that does not parse, with --kind': verify('001', '--kind', 'claude', '--every', '0', topfiles={'home/.config/pstack/kitchen.toml': ('x = [\n', 0o644)}),
            '--timeout as the sum 1+1': verify('001', '--timeout', '1+1', '--every', '0'),
            '--timeout as the product 2*15': verify('001', '--timeout', '2*15', '--every', '0'),
            '--timeout in octal': verify('001', '--timeout', '010', '--every', '0'),
            'a verifier that cannot start': verify('001', '--every', '0', fakefiles={'start-fail': ''}),
            'a verifier that asks whether to trust the folder': verify('001', '--every', '0', fakefiles={'start-fail': '', 'trust-dialog': ''}),
            'a prompt the verifier does not take': verify('001', '--every', '0', promptfail=True),
            'a unit never dispatched': verify('001', '--every', '0', pair={'heads': {}}),
            'a unit with no report': verify('001', '--every', '0', files={'reports/001-x.md': None}),
            'a unit whose report is not done': verify('001', '--every', '0', files={'reports/001-x.md': '# R\n\nstatus: weird\nhead: {u1}\n'}),
            'a unit with no brief': verify('009', '--every', '0'),
            'no unit and no landing': verify('--every', '0'),
            'an unknown option': verify('001', '--bogus'),
            '--covers with no value': verify('001', '--covers'),
            '--covers that is no unit': verify('001', '--covers', 'abc'),
            '--every and --until-verdict together': verify('001', '--every', '1', '--until-verdict'),
            '--landing with a unit': verify('001', '--landing'),
            '--head without --landing': verify('001', '--head', 'HEAD'),
            '--parent without --landing': verify('001', '--parent', 'HEAD'),
            '--cancel with --wait': verify('--cancel', '--wait'),
            '--wait with nothing open': verify('--wait'),
            'too few arguments': world(args=['verify', '{st}']),
            'a pair.json that is an array': verify('001', '--every', '0', nopair=True, files={'pair.json': '[]\n'}),
            'a verifier that is a string': verify('001', '--every', '0', pair={'verifier': 'v1'}),
            'a heads that is a string': verify('001', '--every', '0', pair={'heads': 'x'}),
            'a master that is a string': verify('001', '--every', '0', pair={'master': 'm1'}),
            'a sidekick that is a string': verify('001', '--every', '0', pair={'sidekick': 'x'}),
            'outside herdr': verify('001', '--every', '0', env={'HERDR_ENV': '0'}),
        }


class BashReachTests(unittest.TestCase):
    """tests/bash_reach.py on small synthetic scripts."""

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
                 'an eval': ('lib', 'cmd_x() { eval "$x"; }\n'),
                 'declare -F outside pair_main': ('lib', 'cmd_x() { declare -F cmd_y; }\n'),
                 'a trap handler': ('lib', 'cmd_x() { trap cleanup EXIT; }\n'),
                 'a backtick substitution': ('lib', 'cmd_x() { v=`one`; }\n'),
                 'a source path it cannot resolve': ('entry', '. "$elsewhere/lib.sh"\n')}
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

    def test_a_prefix_does_not_hide_a_form_the_model_rejects_and_time_does_not_hide_a_call(self):
        for label, body in {'builtin eval': 'builtin eval "$x"', 'command eval': 'command eval "$x"', 'builtin source of a missing file': 'builtin source "$here/none.sh"',
                            'compgen -A function': 'compgen -A function', 'export -f': 'export -f cmd_y', 'coproc': 'coproc cmd_y'}.items():
            with self.subTest(label), self.assertRaises(bash_reach.ReachError):
                self.reach(lib=f'cmd_y() {{ :; }}\ncmd_x() {{ {body}; }}\n', entry=self.SOURCE_LIB)
        script = 'f() { :; }\ng() { :; }\ncmd_x() {\n\ttime -p f\n\ttime g\n\tcommand -v eval\n}\n'
        self.assertEqual(self.reach(lib=script, entry=self.SOURCE_LIB).dead(), set())

    def test_each_dynamic_form_is_pinned_to_the_command_it_models(self):
        ok = {'pycore': 'pycore() {\n\tlocal run=()\n\t${run[@]+"${run[@]}"} python3 -I x -- "$@"\n}\n',
              'pair_main': 'pair_main() {\n\tlocal fn=cmd_y\n\tif declare -F "$fn" >/dev/null; then\n\t\t"$fn" "$@"\n\tfi\n}\n'}
        for name, text in ok.items():
            with self.subTest(name):
                self.reach(lib='cmd_y() { :; }\n', entry=self.SOURCE_LIB + text)
        changed = {'pycore': ok['pycore'].replace('python3 -I x', 'helper'),
                   'pair_main': ok['pair_main'].replace('"$fn" "$@"', '"$fn" extra')}
        for name, text in changed.items():
            with self.subTest(f'changed {name}'), self.assertRaises(bash_reach.ReachError):
                self.reach(lib='cmd_y() { :; }\nhelper() { :; }\n', entry=self.SOURCE_LIB + text)


if __name__ == '__main__':
    unittest.main()
