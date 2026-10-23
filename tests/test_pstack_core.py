"""The Python core behind the pair scripts: die parity and the store helpers it duplicates from bash."""
import ast
import calendar
import functools
import contextlib
import inspect
import io
import json
import os
import re
import shlex
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zlib
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
scripts = root / 'skills/pstack-pair/scripts'
sys.path.insert(0, str(scripts))
sys.dont_write_bytecode = True
import run as run_module  # noqa: E402
from pstack_core import agents, argv, cli, exits, git, numbers, panes, permission, procinfo, providers, review, send, steplog, store, wait  # noqa: E402
from pstack_core.store import Context  # noqa: E402
from pstack_core.errors import (  # noqa: E402
    BadNumber, BadScratch, DamagedStore, Die, EmptyStepLog, Exit, FileFailed, LockHeld, MissingStore, NoBrief, NotInHerdr, PairError, PairJsonGone, Paused, PlanNotAgreed,
    StepLogUnreadable, ToolFailed, UnitNotCheckable, Unfilled, VerificationTaken, refuse_as, translate,
)
from pstack_core.variant import Variant, load_skill_variant  # noqa: E402

sys.path.insert(0, str(root / 'tests'))

LEAKED_PREFIXES = ('PSTACK_KITCHEN_', 'KITCHEN_', 'PAIR_')


def inherited_env():
    return {k: v for k, v in os.environ.items() if k != 'PSTACK_PLACEMENT' and not k.startswith(LEAKED_PREFIXES)}


def watch_jq(test, env):
    """Puts the failing jq of tests/run.py first on env's PATH, logging each call's arguments; when the test ends, any call fails it."""
    directory = Path(test.tmp.name) / 'jq-tripwire'
    directory.mkdir()
    run_module.write_tripwire_jq(directory)
    log = Path(test.tmp.name) / 'jq-tripwire.log'
    env.update({'PATH': f'{directory}:{env["PATH"]}', 'TRIPWIRE_LOG': str(log)})

    def check():
        calls = log.read_text().splitlines() if log.exists() else []
        test.assertEqual(calls, [], f'{test.id()}: jq was started')

    test.addCleanup(check)


def core_args(skill_root='/nowhere/pstack-pair'):
    return ['--skill-root', skill_root]


LEGACY_HEADER = 'ts\tepoch\tactor\tevent\tunit\tdetail'


def mask(text):
    """events.tsv without its ts and epoch columns."""
    return [row.split('\t', 2)[2] for row in text.splitlines()]


def event_log(*calls, dedupe='1'):
    """The rows (without their stamp) that store.event writes for (epoch, 'actor event unit detail') calls made at those epoch seconds."""
    with tempfile.TemporaryDirectory() as directory:
        for epoch, args in calls:
            with mock.patch('time.time', return_value=float(epoch)):
                store.event(directory, *args.split(' ', 3), dedupe=dedupe == '1')
        return [row.split('\t', 1)[1] for row in (Path(directory) / 'events.tsv').read_text().splitlines()]


def write_proc_tree(top, procs, uptime=100000):
    """A /proc of these processes {pid: dict(ppid, comm, argv, uid, elapsed, environ)}: what procinfo reads, for a root it is given."""
    top.mkdir(parents=True, exist_ok=True)
    (top / 'uptime').write_text(f'{uptime}.00 0.00\n')
    for pid, p in procs.items():
        d = top / str(pid)
        d.mkdir()
        started = (uptime - p.get('elapsed', 0)) * os.sysconf('SC_CLK_TCK')
        fields = ['S', str(p['ppid'])] + ['0'] * 17 + [str(started)]
        (d / 'stat').write_text(f"{pid} ({p['comm']}) {' '.join(fields)} 0 0\n")
        (d / 'comm').write_text(p['comm'][:15] + '\n')
        (d / 'cmdline').write_bytes(b''.join(a.encode() + b'\0' for a in p['argv']))
        uid = p.get('uid', os.getuid())
        (d / 'status').write_text(f'Name:\t{p["comm"]}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        (d / 'environ').write_bytes(b''.join(e.encode() + b'\0' for e in p.get('environ', [])))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.env = {**inherited_env(), 'PATH': f'{root / "tests/pstack-pair/bin"}:{os.environ["PATH"]}'}
        watch_jq(self, self.env)

    def test_the_jq_tripwire_prints_that_jq_was_started_and_exits_127_and_fails_the_test_that_called_it(self):
        r = subprocess.run(['jq', '.'], input='{}', env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (127, '', 'jq was started\n'))
        log = Path(self.env['TRIPWIRE_LOG'])
        self.assertEqual(log.read_text(), 'jq .\n')
        log.unlink()

    def test_every_test_process_of_run_py_finds_the_failing_jq_first(self):
        with tempfile.TemporaryDirectory() as directory:
            env = run_module.tripwire_env(directory)
            r = subprocess.run(['jq', '.'], input='{}', env=env, capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (127, '', 'jq was started\n'))

    def pair_py(self, *args):
        return subprocess.run([sys.executable, '-I', str(scripts / 'pair.py'), *core_args(), *args],
                              env=self.env, capture_output=True, text=True, check=False, timeout=120)

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

    def test_event_writes_one_row_per_call_with_the_unit_as_a_name(self):
        a = self.path / 'a'
        a.mkdir()
        for unit in ('-', 'reports/003-x.md', ''):
            store.event(str(a), 'master', 'log', unit, 'p: d')
        store.event(str(a), 'sidekick', 'step', '-', '')
        text = (a / 'events.tsv').read_text()
        self.assertEqual(mask(text), ['master\tlog\t-\tp: d', 'master\tlog\t003-x\tp: d', 'master\tlog\t-\tp: d', 'sidekick\tstep\t-\t'])

    def test_the_first_event_in_a_new_store_is_the_first_line_of_events_tsv(self):
        a = self.path / 'a'
        a.mkdir()
        store.event(str(a), 'master', 'init', '-', 'pstack-pair')
        rows = (a / 'events.tsv').read_text().splitlines()
        self.assertEqual([r.split('\t', 2)[2] for r in rows], ['master\tinit\t-\tpstack-pair'])

    def test_event_dedupes_a_repeated_master_wake_in_one_second(self):
        b = self.path / 'b'
        b.mkdir()
        with mock.patch('time.time', return_value=1000.0):
            for _ in range(2):
                store.event(str(b), 'master', 'wake', '001', 'r', dedupe=True)
            store.event(str(b), 'master', 'log', '-', 'r', dedupe=True)
        self.assertEqual(mask((b / 'events.tsv').read_text()), ['master\twake\t001\tr', 'master\tlog\t-\tr'])

    def test_header_field_is_the_first_matching_lines_value_without_its_leading_blanks(self):
        f = self.path / 'h.md'
        f.write_text('# T\n\nstatus:\t done | partial\nstatus: later\nempty:\nkind:   a  b \n')
        expected = {'status': 'done | partial', 'empty': '', 'kind': 'a  b ', 'missing': ''}
        self.assertEqual({key: store.header_field(str(f), key) for key in expected}, expected)

    def fixture_store(self, name='s', brief='001-one.md'):
        st = self.path / name
        for d in ('briefs', 'plans', 'reports', 'progress', 'steers'):
            (st / d).mkdir(parents=True)
        (st / 'pair.json').write_text(json.dumps({'dispatch': {'brief': f'{st}/briefs/{brief}'} if brief else None, 'notice_steers': []}))
        return st

    def ctx(self):
        return Context('pstack-pair', str(root / 'skills/pstack-pair'), '/s', 'Pair', ('sidekick',), ('plans',), 21, '')






    def test_header_field_is_silent_on_a_missing_or_empty_path(self):
        self.assertEqual((store.header_field('', 'status'), store.header_field(str(self.path / 'none.md'), 'status')), ('', ''))

    def test_event_detail_defaults_to_empty(self):
        store.event(str(self.path), 'sidekick', 'step', '-')
        self.assertTrue((self.path / 'events.tsv').read_text().endswith('\tsidekick\tstep\t-\t\n'))






    def commit(self, name):
        (self.path / 'repo' / name).write_text(name)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', name)
        return self.git('rev-parse', 'HEAD')





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



    SKILLS_WITH_A_VARIANT = ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen')
    # The whole commands each skill's variant runs in place of the core's, by name. Each table is the variant's own: a skill that extends another
    # lists what it chooses to keep.
    EFFECTIVE_COMMANDS = {'pstack-pair': (), 'pstack-pair-guided': ('answer', 'new-answer', 'new-direction'), 'pstack-trio': ('advice', 'consult', 'discuss', 'new-consult', 'spawn', 'stop'), 'pstack-kitchen': ('advice', 'catch', 'classify', 'close', 'consult', 'consultant', 'discuss', 'dispatch', 'feedback', 'finish', 'fleet', 'init', 'land-check', 'maintainer', 'new-consult', 'new-handoff', 'queue', 'retro', 'revise', 'rotate-master', 'spawn', 'step', 'stop', 'take-master', 'verify', 'wait')}

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
            cli.resolve(ctx, 'nothing-like-it')  # a name the core lacks is the variant's to supply, so the variant is asked
        ctx._variant = type('V', (), {'commands': {'answer': 'mine', 'status': 'not asked', 'permission': 'not asked'}})()
        self.assertEqual((cli.resolve(ctx, 'answer'), cli.resolve(ctx, 'status'), cli.resolve(ctx, 'permission')),
                         ('mine', cli.COMMANDS['status'], cli.COMMANDS['permission']))

    def test_a_name_the_core_lacks_asks_the_variant(self):
        class Mine:
            commands = {'catch': 'kitchen catch', 'bridge': 'kitchen bridge'}

        ctx = Context('pstack-pair', '/nowhere/pstack-pair', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        ctx._variant = Mine()
        self.assertEqual(cli.resolve(ctx, 'catch'), 'kitchen catch')
        self.assertEqual(cli.resolve(ctx, 'bridge'), 'kitchen bridge')
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
        pair_args = lambda skill, label: ['--skill-root', str(root / 'skills' / skill)]
        kitchen_args = ['--skill-root', str(root / 'skills/pstack-kitchen')]
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
                    ('pair', 'spawn'): (1, '--kind needs a value', 'stderr'), ('kitchen', 'status'): (0, '# Kitchen status', 'stdout'),
                    ('kitchen', 'catch'): (1, 'usage: kitchen.sh catch <store> <layer> <text> [<unit>]', 'stderr')}
        for key, (rc, line, stream) in expected.items():
            self.assertEqual(seen[key][0]['rc'], rc, (key, seen[key]))
            self.assertIn(line, seen[key][1] if stream == 'stdout' else seen[key][2], (key, seen[key]))
        self.assertEqual(seen['kitchen', 'stop'][0]['rc'], 0, seen['kitchen', 'stop'])
        self.assertIn('sidekick s1 is not live\nverifier is not live\nconsultant is not live\n', seen['kitchen', 'stop'][1])
        for name in ('status', 'stop'):
            info = seen['kitchen', name][0]
            self.assertTrue(info['kitchen_core'], (name, seen['kitchen', name]))  # the build stamp on a store named as the second word
        loaded = lambda key: any('/pstack-kitchen/scripts/' in o and 'variant' in o for o in seen[key][0]['opened'])
        self.assertTrue(loaded(('kitchen', 'stop')))
        self.assertTrue(loaded(('kitchen', 'status')), 'the variant gives every command its configuration')
        self.assertTrue(loaded(('kitchen', 'catch')))
        self.assertEqual(seen['kitchen', 'catch'][0]['kitchen_core'], True)
        self.assertIn('usage: kitchen.sh catch <store> <layer> <text> [<unit>]', seen['kitchen', 'catch'][2])

    def test_a_kitchen_command_run_from_the_filesystem_root_loads_kitchen_core(self):
        kitchen_args = ['--skill-root', str(root / 'skills/pstack-kitchen')]
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

    def test_a_skill_with_no_variant_file_runs_its_commands_on_the_base_variant(self):
        store = self.path / 'st'
        store.mkdir()
        (store / 'pair.json').write_text('{}')
        r = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from pstack_core import cli, variant; '
                            'rc = cli.main(sys.argv[2:]); print([m for m in sys.modules if m.startswith("pstack_variant_")]); sys.exit(rc)',
                            str(scripts), *core_args(), 'resume', str(store)], capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, f'resumed {store}\n[]\n', ''))

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

















    STEER_TEXT = '# Steer 001-x-s1\n\nkind: narrow\nscope effect: none\n\n## Direction\n\nuse the other helper\n'

    def steer_cases(self):
        """Cases for `steer`, through every entry script. A case's files are all old (see send_run): the objection report must not count as the
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
            'an idle sidekick whose unit failed and no objection': dict(args=run, agents={'s1': 'idle claude'}, files={**steer, 'reports/001-x.md': '# R\n\nstatus: failed\n'}),
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
















    READY_HOOK = ('#!/bin/sh\nst="$FAKE/../st"\ncase "$2" in Load*) case "$1" in c1) printf "# R\\n\\nstatus: answered\\n" >"$st/advice/000-ready.md" ;; '
                  '*) printf "# R\\n\\nstatus: done\\n" >"$st/reports/000-ready.md" ;; esac ;; esac\nexit 0\n')


    # spawn_role had no bash caller left once rotate and failover moved, so its bash shim is gone; the call forms it served are still run, against a
    # definition of the same shim in the test, so the Python entry is pinned the way the bash callers used it.














    def detect_in(self, procs, files=None, session=''):
        """permission.detect over a table {pid: (ppid, comm, args)} in place of ps, with HOME and cwd under the test's directory."""
        home, cwd = self.path / 'home', self.path / 'cwd'
        for rel, text in (files or {}).items():
            (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.path / rel).write_text(text)

        def read_proc(pid):
            row = procs.get(pid)
            return None if row is None else procinfo.Proc(pid, row[0], 0, row[1], tuple(row[2].split(' ')), 0)
        return permission.detect(100, str(cwd), str(home), session, read_proc)

    def tree(self, procs, **partial):
        """A synthetic /proc under the test's directory, with PAIR_PROC_ROOT pointing at it for the test; partial names pids written with only some files."""
        top = self.path / f'proc{len(list(self.path.glob("proc*")))}'
        write_proc_tree(top, procs)
        for pid, keep in partial.items():
            (top / pid).mkdir()
            for name in keep:
                (top / pid / name).write_text('')
        patcher = mock.patch.dict(os.environ, {'PAIR_PROC_ROOT': str(top)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return top

    def test_procinfo_reads_the_full_command_line_the_parent_the_user_and_the_seconds_running(self):
        long_args = ['/opt/a-long-agent-name/bin/agent', '--ask-for-approval', 'never', '--note', 'two words']
        top = self.tree({7: dict(ppid=3, comm='agent (x) y', argv=long_args, uid=1234, elapsed=321)})
        proc = procinfo.read(7, str(top))
        self.assertEqual(proc, procinfo.Proc(7, 3, 1234, 'agent (x) y', tuple(long_args), 321))
        self.assertEqual(proc.args, '/opt/a-long-agent-name/bin/agent --ask-for-approval never --note two words')
        self.assertEqual(procinfo.read(8, str(top)), None)

    def test_procinfo_leaves_out_a_process_that_vanishes_while_it_is_read(self):
        top = str(self.tree({1: dict(ppid=0, comm='init', argv=['init'], uid=0), 2: dict(ppid=1, comm='mine', argv=['mine'])}, **{'3': ['stat', 'comm']}))
        self.assertEqual(procinfo.read(3, top), None)
        self.assertEqual([p.pid for p in procinfo.of_user(os.getuid(), top)], [2])
        self.assertEqual(procinfo.environ(3, top), None)
        self.assertEqual(procinfo.environ(2, top), [''])

    def test_a_check_in_names_the_longest_running_command_in_the_pane_of_this_user_started_since_the_dispatch(self):
        from pstack_core import checkin
        here = ['HERDR_PANE_ID=p1', 'HERDR_SOCKET_PATH=/s']
        base = dict(ppid=1, argv=['x'])
        self.tree({10: dict(base, comm='bash', elapsed=500, environ=here),
                   11: dict(base, comm='sleep', elapsed=300, environ=here),
                   12: dict(base, comm='make', elapsed=200, environ=here),
                   13: dict(base, comm='sleep', elapsed=900, environ=here, uid=os.getuid() + 1),
                   14: dict(base, comm='vim', elapsed=800, environ=['HERDR_PANE_ID=p2', 'HERDR_SOCKET_PATH=/s']),
                   15: dict(base, comm='cargo', elapsed=700, environ=['HERDR_PANE_ID=p1', 'HERDR_SOCKET_PATH=/other']),
                   16: dict(base, comm='old', elapsed=2000, environ=here)}, **{'17': ['stat']})
        env = {'HERDR_SOCKET_PATH': '/s'}
        self.assertEqual(checkin.busy_command('p1', 1000, 2200, env), 'sleep for 5m')
        self.assertEqual(checkin.busy_command('p1', 250, 2200, env), 'sleep for 5m')
        self.assertEqual(checkin.busy_command('p1', 100, 2200, env), 'old for 33m')
        self.tree({10: dict(base, comm='bash', elapsed=500, environ=here), 11: dict(base, comm='zsh', elapsed=300, environ=here)})
        self.assertEqual(checkin.busy_command('p1', 0, 700, env), 'bash for 8m')
        self.assertEqual(checkin.busy_command('p3', 0, 2200, env), '')

    def test_an_agent_is_busy_when_herdr_says_working_or_its_pane_runs_a_command_it_started_since_the_dispatch(self):
        from pstack_core import checkin
        here = ['HERDR_PANE_ID=p1', 'HERDR_SOCKET_PATH=/s']
        self.tree({11: dict(ppid=1, argv=['x'], comm='sleep', elapsed=300, environ=here)})
        env = {'HERDR_SOCKET_PATH': '/s'}
        self.assertEqual(checkin.busy('working', 'p1', 1000, 2200, env), 'working')
        self.assertEqual(checkin.busy('working', 'p9', None, 2200, env), 'working')
        self.assertEqual(checkin.busy('idle', 'p1', 1000, 2200, env), 'sleep for 5m')
        self.assertEqual(checkin.busy('done', 'p1', 1000, 2200, env), 'sleep for 5m')
        self.assertEqual(checkin.busy('idle', 'p9', 1000, 2200, env), '')
        self.assertEqual(checkin.busy('idle', 'p1', 2000, 2200, env), '')
        self.assertEqual(checkin.busy('idle', 'p1', None, 2200, env), '')
        self.assertEqual(checkin.busy('blocked', 'p1', 1000, 2200, env), '')
        self.assertEqual(checkin.busy('unknown', 'p1', 1000, 2200, env), '')

    def test_the_approval_mode_comes_from_the_whole_command_line_not_the_name_ps_would_cut(self):
        home, cwd = str(self.path / 'home'), str(self.path / 'cwd')
        top = self.tree({100: dict(ppid=101, comm='bash', argv=['bash']),
                         101: dict(ppid=1, comm='codex', argv=['/opt/some-long-directory/codex', '--ask-for-approval', 'never'])})
        read_proc = lambda pid: procinfo.read(pid, str(top))
        self.assertEqual(permission.detect(100, cwd, home, '', read_proc), 'dontAsk')
        self.assertEqual(permission.detect(5, cwd, home, '', read_proc), 'auto')
        top = self.tree({100: dict(ppid=101, comm='bash', argv=['bash']),
                         101: dict(ppid=1, comm='node', argv=['/usr/local/lib/node_modules/@openai/codex-linux-x64-with-a-long-name/bin/codex-agent',
                                                               '--ask-for-approval=never'])})
        self.assertEqual(procinfo.read(101, str(top)).comm, 'node')
        self.assertEqual(permission.detect(100, cwd, home, '', read_proc), 'dontAsk')
        top = self.tree({100: dict(ppid=1, comm='a-very-long-executable-name', argv=['/x/a-very-long-executable-name', '--ask-for-approval=never'])})
        self.assertEqual(procinfo.read(100, str(top)).comm, 'a-very-long-exe')
        self.assertEqual(procinfo.read(100, str(top)).argv[0], '/x/a-very-long-executable-name')

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

    def test_a_settings_file_that_is_missing_unparsable_or_without_the_key_gives_no_mode_and_the_next_file_is_read(self):
        alone = {100: (1, 'bash', 'bash')}
        later = {'home/.claude/settings.json': '{"permissions": {"defaultMode": "plan"}}'}
        for name, first in (('a file that does not parse', '{not json'), ('a file with no permissions', '{}'), ('a file with no defaultMode', '{"permissions": {}}'),
                            ('a permissions that is a string', '{"permissions": "x"}'), ('a defaultMode that is a number', '{"permissions": {"defaultMode": 3}}')):
            self.assertEqual(self.detect_in(alone, files={'cwd/.claude/settings.local.json': first, **later}), 'plan', name)
        (self.path / 'home/.claude/settings.json').unlink()
        self.assertEqual(self.detect_in(alone, files={'cwd/.claude/settings.local.json': '{not json'}), 'auto')
        (self.path / 'cwd/.claude/settings.local.json').unlink()
        self.assertEqual(self.detect_in(alone), 'auto')



    SHELL = ('bash', 'bash /x/pair.sh permission')




    GIT = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
           'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z'}

    def script_world(self, label, spec):
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
        if spec.get('procs'):
            # processes (pid: dict(comm, elapsed, ...)) run from the sidekick's pane p1 on the world's herdr socket, as a harness's background command
            here = [f'HERDR_PANE_ID={spec.get("procs_pane", "p1")}', f'HERDR_SOCKET_PATH={top / "herdr.sock"}']
            write_proc_tree(top / 'procs', {pid: dict(ppid=1, argv=[row['comm']], environ=here, **row) for pid, row in spec['procs'].items()})
            env['PAIR_PROC_ROOT'] = str(top / 'procs')
        if spec.get('pstable'):
            rows = [line.split('\t', 3) for line in spec['pstable'].splitlines()]
            write_proc_tree(top / 'proc', {int(pid.replace('SELF', str(os.getpid()))): dict(ppid=int(ppid), comm=comm, argv=args.split(' '))
                                           for pid, ppid, comm, args in rows})
            env['PAIR_PROC_ROOT'] = str(top / 'proc')
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
            # a `herdr` that moves the KITCHEN_QUIET_CLOCK file on by this many seconds (when it holds a number) at each `agent wait`, then runs the real one, so a quiet wait's rounds end at a fixed count
            ticks = top / 'ticks'
            ticks.mkdir()
            real = shutil.which('herdr', path=env['PATH'])
            (ticks / 'herdr').write_text(f'#!/bin/sh\nif [ "$1" = agent ] && [ "$2" = wait ]; then\n  n=$(cat "$KITCHEN_QUIET_CLOCK" 2>/dev/null)\n  case "$n" in\n    ""|*[!0-9]*) ;;\n    *) echo $((n + {int(spec["tick"])})) >"$KITCHEN_QUIET_CLOCK" ;;\n  esac\nfi\nexec {real} "$@"\n')
            (ticks / 'herdr').chmod(0o755)
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

    def script_run(self, label, skills, skill, spec):
        top, st, env, fill = self.script_world(label, spec)
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















    def wait_signalled(self, skills, number, group, skill='pstack-pair', ignore_int=False, timeout_ms=20000, hold='wait', argv=None, world=None):
        """Run `pair.sh wait`, signal it while it sits in herdr's wait (the fake herdr's once hook holds it there on a pipe), and give
        (exit status, stdout, stderr). Nothing here can hang: each wait is bounded, the hook is released in a finally, and the
        whole process group is killed if the command outlives the bound."""
        top, st, env, fill = self.script_world(f'sig-{len(list(self.path.iterdir()))}', world or {'agents': {'s1': 'working claude'}, 'pair': {'sent': {'file': '{st}/briefs/001-x.md', 'epoch': 1}}})
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



    def own_world(self, label, once=None, skill='pstack-pair', **spec):
        """A world for the port alone (a behaviour the base never had) and a way to run its commands one after the other."""
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1}
        spec = {'agents': {'s1': 'working claude'}, 'pair': {'sent': sent, 'pending': ['{st}/briefs/001-x.md']},
                'files': {'reports/001-x.md': '# R\n\nstatus: done\nhead: {c3}\n'}, **spec}
        top, st, env, fill = self.script_world(label, spec)
        for name, script in (once or {}).items():
            (top / 'fake/once').mkdir(exist_ok=True)
            (top / 'fake/once' / name).write_text(script)
            (top / 'fake/once' / name).chmod(0o755)
        run = lambda *args, extra_env=None: subprocess.run([str(root / 'skills' / skill / 'scripts/pair.sh'), *(fill(a) for a in args)], cwd=top / 'repo', env={**env, **(extra_env or {})},
                                           capture_output=True, text=True, check=False, timeout=120)
        return top, st, run

    # Each template that declares a status: line, and the command its writer runs on the file. A new template must be added here.
    STATUS_READERS = {
        'report-template.md': 'finish', 'plan-response-template.md': 'finish', 'steer-response-template.md': 'finish', 'ask-template.md': 'finish',
        'advice-template.md': 'notify', 'plan-advice-template.md': 'notify',
        'note-template.md': None,  # pair.sh note reads it (blocking, clear)
        'verdict-template.md': None,  # kitchen.sh verify reads it (clean, reject, inconclusive)
    }

    def test_finish_and_notify_take_every_status_a_template_tells_a_role_to_write(self):
        sent = {}
        for template in sorted(root.glob('skills/*/references/*template*.md')):
            line = re.search(r'^status: *(.+)$', template.read_text(), re.M)
            if not line:
                continue
            self.assertIn(template.name, self.STATUS_READERS, f'{template.relative_to(root)} declares a status; name the command that reads it')
            command = self.STATUS_READERS[template.name]
            for value in re.split(r'\s*\|\s*', line.group(1).strip('{} ')):
                if command:
                    sent.setdefault((command, value), template.relative_to(root))
        self.assertIn(('finish', 'done'), sent, 'no report template found under skills/*/references')
        top, st, run = self.own_world('status-contract', files={})
        (st / 'contract').mkdir()
        refused = []
        for (command, value), template in sorted(sent.items()):
            path = st / 'contract' / f'{command}-{value}.md'
            path.write_text(f'# C\n\nstatus: {value}\n')
            r = run(command, '{st}', str(path))
            if r.returncode:
                refused.append(f'{command} refuses status: {value}, which {template} tells a role to write (exit {r.returncode}): {r.stderr.strip()}')
        if refused:
            self.fail('\n'.join(refused))


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
            with self.assertRaises(Unfilled, msg=label):
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
        for label, report, advice in (('done', 'done', 'write a fix brief'),
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

    SK = {'kind': 'claude', 'generation': 1, 'name': 's1', 'pane_id': 'p1'}
    DISPATCHED = {'brief': '{st}/briefs/001-x.md', 'at': '2026-01-01T00:00:00Z', 'generation': 1}
    STEER_001 = '# Steer 001-s1: x\n\nbrief: {st}/briefs/001-x.md\nsupersedes: none\nkind: scope\nscope effect: %s\n\n## Direction\n\nDo it.\n'

    def busy_world(self, label, status, procs=None, report=None, effect='none', brief=None, **spec):
        """A world whose sidekick s1 (pane p1) was dispatched 001-x and shows this herdr status; procs run in its pane, report is 001-x's report."""
        files = {} if report is None else {'reports/001-x.md': f'# R\n\nstatus: {report}\nhead: {{c3}}\n'}
        if brief is not None:
            files['briefs/001-x.md'] = brief
        sent = {'file': '{st}/briefs/001-x.md', 'kind': 'BRIEF', 'epoch': 1}
        top, st, run = self.own_world(label, files=files, agents={'s1': f'{status} claude'}, procs=procs,
                                      pair={'sidekick': self.SK, 'dispatch': self.DISPATCHED, 'sent': sent, 'pending': ['{st}/briefs/001-x.md']}, **spec)
        steer = st / 'steers/001-x-s1.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text((self.STEER_001 % effect).replace('{st}', str(st)))
        return top, st, run, steer

    def prompts(self, top):
        calls = (top / 'fake/calls.log').read_text() if (top / 'fake/calls.log').exists() else ''
        return [l for l in calls.splitlines() if l.startswith('agent prompt')]

    BG = {50: dict(comm='make', elapsed=300)}

    def test_a_steer_to_a_sidekick_idle_on_its_own_background_command_is_delivered_as_a_working_steer(self):
        # gotham-1095#1: the harness reports idle while its command still runs; the old refusal said "no objection is open".
        for status in ('idle', 'done'):
            top, st, run, steer = self.busy_world('bg-' + status, status, procs=self.BG, report='done')
            r = run('steer', '{st}', str(steer))
            self.assertEqual((r.returncode, r.stderr), (0, ''), status)
            self.assertIn(f'steered s1 with {steer}', r.stdout)
            self.assertEqual(self.prompts(top), [f'agent prompt s1 pstack-pair STEER {steer}'], status)

    def test_a_steer_to_an_idle_sidekick_whose_unit_has_no_report_is_delivered(self):
        # python-port-3#3, python-port-4(1020)#4: nothing to fold the steer into yet, so the sidekick gets it now.
        top, st, run, steer = self.busy_world('noreport', 'idle')
        r = run('steer', '{st}', str(steer))
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertIn(f'steered s1 with {steer}; unit 001 has no report yet', r.stdout)
        self.assertEqual(self.prompts(top), [f'agent prompt s1 pstack-pair STEER {steer} --wait --until working --timeout 30000'])

    def test_a_steer_to_a_blocked_agent_is_still_refused_even_with_a_command_in_its_pane(self):
        top, st, run, steer = self.busy_world('blockedbg', 'blocked', procs=self.BG)
        r = run('steer', '{st}', str(steer))
        self.assertEqual((r.returncode, r.stdout), (3, ''))
        self.assertEqual(r.stderr, 'pair.sh: sidekick s1 is blocked; inspect: herdr agent read s1 --source visible --lines 60\n')
        self.assertEqual(self.prompts(top), [])

    def test_queue_accepts_a_sidekick_idle_on_a_background_command_and_refuses_a_blocked_or_quiet_one(self):
        # gate-speed-1106#7
        for status, procs, code, said in (('idle', self.BG, 0, "queued {st}/briefs/002-y.md; it waits for the master's dispatch"),
                                          ('done', self.BG, 0, 'queued'), ('idle', None, 5, 'sidekick s1 is idle; dispatch instead'),
                                          ('blocked', self.BG, 3, 'sidekick s1 is blocked; resolve that before queueing')):
            top, st, run, steer = self.busy_world(f'q-{status}-{bool(procs)}', status, procs=procs, report='done')
            r = run('queue', '{st}', '{st}/briefs/002-y.md')
            self.assertEqual(r.returncode, code, (status, procs, r.stderr))
            self.assertIn(said.replace('{st}', str(st)), r.stdout + r.stderr)

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
            with self.assertRaises(Unfilled, msg=label):
                review.require_filled(ctx, str(f))

    def test_the_guided_unfilled_re_reads_prose_only(self):
        ctx = Context('pstack-pair-guided', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '^(decided by|status): .*\\|')
        f = self.path / 'resp.md'
        fence3 = '`' * 3
        f.write_text(f'# R\n\nstatus: agree\n\n{fence3}\nstatus: agree | object\n{fence3}\n')
        self.assertIsNone(review.require_filled(ctx, str(f)))
        f.write_text('# R\n\nstatus: agree | object\n')
        with self.assertRaises(Unfilled):
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
        # an -s<k> report that is not `object` beside a blocked unit report is not the unit's report: the blocked unit report decides, and resumes
        top, st, run = self.own_world('sc-s1', files={'reports/001-x.md': '# R\n\nstatus: blocked\n', 'reports/001-x-s1.md': '# R\n\nstatus: partial\n'},
                                      agents={'s1': 'idle claude'})
        steer = st / 'steers/001-x-s2.md'
        steer.parent.mkdir(exist_ok=True)
        steer.write_text('# Steer 001-s2: x\n\nbrief: x\nsupersedes: none\nkind: scope\nscope effect: none\n\n## Direction\n\nDo it.\n')
        r = run('steer', '{st}', str(steer))
        self.assertIn('resumed unit 001', r.stdout)

    BRIEF_001 = '# Brief 001-x\n\n## Scope\n\nmay write:\n- src/a.py\n\nmust not write:\n- docs/**\n\n## Goal\n\nx\n'

    def test_a_steer_to_a_blocked_or_partial_unit_resumes_it_with_the_scope_effect_in_the_brief_and_the_old_report_set_aside(self):
        # python-port-4(1020)#4: the steer used to be refused with the manual route; now it is that route.
        for report, effect, entry, heading in (('blocked', 'may write: src/b.py \u2014 the fix lives there', '- src/b.py', 'may write:'),
                                               ('partial', 'must not write: tests/golden/**', '- tests/golden/**', 'must not write:'),
                                               ('blocked', 'may write "src/c.py" and "src/d.py"', '- src/d.py', 'may write:')):
            label = f'resume-{report}-{heading[:3]}-{entry[-4:-3]}'
            top, st, run, steer = self.busy_world(label, 'idle', report=report, effect=effect, brief=self.BRIEF_001)
            pair = json.loads((st / 'pair.json').read_text())
            pair['heads'] = {'001-x': 'c1base'}
            (st / 'pair.json').write_text(json.dumps(pair))
            old = st / 'reports/001-x.md'
            os.utime(old, (1000, 1000))
            r = run('steer', '{st}', str(steer))
            self.assertIn(f'resumed unit 001: sent {st}/briefs/001-x.md again after {steer}', r.stdout, (label, r.stderr))
            brief = (st / 'briefs/001-x.md').read_text().split('\n')
            start = brief.index(heading)
            self.assertIn(entry, brief[start:start + 4], label)
            self.assertEqual(brief.count(entry), 1, label)
            prompts = self.prompts(top)
            self.assertEqual(len(prompts), 1, label)
            self.assertTrue(prompts[0].startswith(f'agent prompt s1 pstack-pair BRIEF {st}/briefs/001-x.md resume after STEER {steer}'), prompts)
            after = json.loads((st / 'pair.json').read_text())
            self.assertEqual(after['heads'], {'001-x': 'c1base'}, label)
            self.assertEqual(after['dispatch']['brief'], f'{st}/briefs/001-x.md', label)
            # the stale report is retired twice over: .sent.seen names it as already shown, and fresh_reply counts only reports written since .sent.epoch
            self.assertEqual(after['sent']['seen'], f'{old}:1000', label)
            self.assertGreater(after['sent']['epoch'], 1000, label)
            self.assertEqual(after['sent']['kind'], 'BRIEF', label)

    def test_resuming_twice_adds_the_scope_entry_once_and_a_brief_without_the_heading_gets_it(self):
        top, st, run, steer = self.busy_world('resume-twice', 'idle', report='partial', effect='may write: src/b.py', brief='# Brief 001-x\n\n## Scope\n\nmay write:\n- src/a.py\n\n## Goal\n\nx\n')
        for _ in range(2):
            run('steer', '{st}', str(steer))
        self.assertEqual((st / 'briefs/001-x.md').read_text().count('- src/b.py'), 1)
        top, st, run, steer = self.busy_world('resume-heading', 'idle', report='partial', effect='must not write: docs/x.md', brief='# Brief 001-x\n\n## Scope\n\nmay write:\n- src/a.py\n\n## Goal\n\nx\n')
        run('steer', '{st}', str(steer))
        self.assertEqual((st / 'briefs/001-x.md').read_text(), '# Brief 001-x\n\n## Scope\n\nmay write:\n- src/a.py\n\nmust not write:\n- docs/x.md\n\n## Goal\n\nx\n')

    def test_a_herdr_blocked_agent_is_not_resumed_and_its_brief_stays_as_written(self):
        top, st, run, steer = self.busy_world('resume-blocked', 'blocked', report='blocked', effect='may write: src/b.py', brief=self.BRIEF_001)
        r = run('steer', '{st}', str(steer))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual((st / 'briefs/001-x.md').read_text(), self.BRIEF_001)
        self.assertEqual(self.prompts(top), [])

    def test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line(self):
        top, st, run, draft = self.note_world('np', 'blocking', agents={'s1': 'working claude'})
        published = f'{st}/notes/001-x-n1.md'
        # a failing prompt: the published line first, then the error, exit 2
        (top / 'fake/prompt-fail').write_text('')
        r = run('note', '{st}', str(draft))
        self.assertEqual((r.returncode, r.stdout), (2, f'published {published} (blocking)\n'))
        self.assertIn('herdr prompt failed for s1', r.stderr)
        # any other failure of the delivery also comes after the published line: a lock that stays held while a Devin's next step is parked
        top, st, run, draft = self.note_world('npl', 'blocking', agents={'s1': 'working devin'}, pair={'sidekick': {'kind': 'devin', 'generation': 1, 'name': 's1'}})
        (st / '.pair.lock').mkdir()
        r = run('note', '{st}', str(draft), extra_env={'PAIR_LOCK_TRIES': '1'})
        self.assertEqual((r.returncode, r.stdout), (1, f'published {st}/notes/001-x-n1.md (blocking)\n'))
        self.assertEqual(r.stderr, f'pair.sh: pair.json lock held for 10s; remove {st}/.pair.lock if no pair.sh is running\n')
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

    def test_wait_for_keeps_its_outcome_when_the_claim_or_the_release_cannot_take_the_lock(self):
        ctx = Context('pstack-pair', '/x', '/s', 'Pair', ('sidekick',), ('plans',), 21, '')
        for fail_claim, fail_release in ((True, False), (False, True), (True, True)):
            err = tempfile.TemporaryFile('w+')
            self.addCleanup(err.close)
            tokens = []

            def claim(store, token):
                if fail_claim:
                    raise LockHeld('x')

            def release(store, token):
                raise LockHeld('x')

            def reply(ctx_, store, name, timeout, now=None, token=None):
                tokens.append(token)
                return wait.WaitResult(0, 'idle', '', '/s/reports/001-x.md')

            def finish(ctx_, store, interval, result, mode=None, now=None, quiet=False, out=None):
                return wait.WaitOutcome(0, 'report', digest=('report: /s/reports/001-x.md\n',))

            with mock.patch.object(wait, 'claim_wait', claim), mock.patch.object(wait, 'release_wait', release if fail_release else (lambda *a: None)), \
                    mock.patch.object(wait, 'wait_for_reply', reply), mock.patch.object(wait, 'finish_wait', finish), mock.patch.object(sys, 'stderr', err), \
                    mock.patch('sys.stdout', new_callable=io.StringIO) as out, mock.patch.dict(os.environ, {}, clear=False):
                outcome = wait.wait_for(ctx, '/s', timeout=1000, name='s1')
                code = wait.print_outcome(outcome)
            err.seek(0)
            text = err.read()
            self.assertEqual((code, out.getvalue(), outcome.claim_failed), (0, 'report: /s/reports/001-x.md\n', fail_claim), (fail_claim, fail_release))
            self.assertEqual(text.count('could not claim wait.pid'), 1 if fail_claim else 0, text)
            self.assertEqual(text.count('could not release wait.pid'), 1 if fail_release and not fail_claim else 0, text)
            self.assertEqual(tokens, [None] if fail_claim else [str(os.getpid())])

    def test_dispatch_to_a_store_with_no_pair_json_says_so_once_after_the_scope_warnings(self):
        top, st, run = self.own_world('onceline', files={})
        brief = top / 'outside.md'
        brief.write_text('# Brief 001: x\n\nplaybook: docs\nplan: none\n\n## Scope\n\nmay write:\n- src/**\n')
        r = run('dispatch', '{st}/nope', str(brief), '--timeout', '0')
        self.assertEqual((r.returncode, r.stdout), (2, ''))
        self.assertEqual(r.stderr, 'warning: Scope entry "src/**" matches no file or directory in ; is it relative to a neighbouring entry?\n'
                                   f'pair.sh: no pair.json in {st}/nope; run: pair.sh init <slug>\n')

    def test_finish_of_a_done_report_in_a_store_with_no_pair_json_says_so_once(self):
        top, st, run = self.own_world('oncefinish', files={})
        report = top / 'done.md'
        report.write_text('# R\n\nstatus: done\nhead: abc\n')
        r = run('finish', '{st}/nope', str(report))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (2, '', f'pair.sh: no pair.json in {st}/nope; run: pair.sh init <slug>\n'))

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



    KITCHEN_TOML = 'version = 1\n\n[profile.app]\npaths = ["src/**", "docs/**", "extra/**"]\nfast = ["true"]\n\n[escalate]\npaths = ["contracts/**"]\n'

    def queue_send_cases(self):
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
            'an unfilled brief': with_files(files={'briefs/003-z.md': brief_text('{{{{path}}}}')}),  # script_world formats file text: doubled braces stay literal
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









    VALUE_FLAGS = {
        'pstack-pair': [['spawn', '{st}', f] for f in ('--kind', '--fallback', '--permission', '--direction', '--pane', '--timeout', '--fallback-arg')]
        + [['init', 'demo', f] for f in ('--store', '--pane')]
        + [['step', '{st}', '{c2}', 'x', '--resolves'], ['new-note', '{st}', '001', '--through'], ['pause', '{st}', '--reason'],
           ['scratch', '{st}', '001-x', '--at'], ['failover', '{st}', '--reason'], ['new-steer', '{st}', '001', '--supersedes']]
        + [[command, '{st}', *target, f] for command, target in (('wait', []), ('stop', []), ('discuss', ['{st}/plans/001-x.md']), ('dispatch', ['{st}/briefs/001-x.md']),
                                                               ('steer', ['{st}/steers/001-x-s1.md'])) for f in ('--timeout', '--every')],
        'pstack-pair-guided': [['answer', '{st}', 'x', f] for f in ('--timeout', '--every')],
        'pstack-trio': [['stop', '{st}', f] for f in ('--only', '--timeout', '--every')] + [['spawn', '{st}', f] for f in ('--kind', '--sidekick', '--consultant', '--fallback', '--fallback-arg', '--only', '--permission', '--consultant-permission', '--timeout')],
        'pstack-kitchen': [['rotate-master', '{st}', '--model'], ['maintainer', 'on', '--pane']] + [['spawn', '{st}', f] for f in ('--kind', '--fallback')]
        + [['consultant', '{st}', f] for f in ('--reason', '--kind', '--permission')] + [['discuss', '{st}', '{st}/plans/001-x.md', f] for f in ('--timeout', '--every')]
        + [['wait', '{st}', f] for f in ('--max', '--timeout', '--every')]
        + [['verify', '{st}', '001', f] for f in ('--timeout', '--kind', '--retry-entry', '--every', '--base', '--parent', '--head', '--covers')],
    }

    def test_every_flag_that_takes_a_value_dies_naming_it_when_none_is_given(self):
        """Through the real pair.sh of each variant and kitchen.sh: the flag as the last argument ends the command with `<flag> needs a value` and
        status 1."""
        for skill, commands in self.VALUE_FLAGS.items():
            for n, args in enumerate(commands):
                label = f'value-{skill}-{n}'
                spec = dict(args=args, pair={'slug': 'demo', 'master': {'name': 'm1', 'pane_id': 'p0'}}, panes={'p0': 'm1'}, agents={'s1': 'working claude'}, files={'plans/001-x.md': '# Plan\n\nrisk: routine\n'})
                got = self.script_run(label, root / 'skills', skill, spec) if skill != 'pstack-kitchen' else self.kitchen_world(label, spec)
                self.assertEqual((got['rc'], got['stderr'].rstrip('\n').split('\n')[-1]), (1, f'pair.sh: {args[-1]} needs a value'), (skill, args))

    def test_step_a_missing_resolves_value_names_the_flag_through_pair_sh(self):
        got = self.script_run('resolves-value', root / 'skills', 'pstack-pair', dict(args=['step', '{st}', '{c2}', 'x', '--resolves'], agents={'s1': 'working claude'}))
        self.assertEqual((got['rc'], got['stdout'], got['stderr']), (1, '', 'pair.sh: --resolves needs a value\n'))









    def send_cases(self):
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


    OLD_MTIME = 1_000_000_000




    ROSTER_TOML = ('[sidekick]\nkind = "claude"\nargs = ["--model", "m1"]\n\n[sidekick.fallback]\nkind = "devin"\nargs = ["--fb", "x"]\n')


    def kitchen_world(self, label, spec):
        """One kitchen world run through the working tree's kitchen.sh: its outcome, the paths of its world masked."""
        toml = (spec.get('toml') or self.KITCHEN_TOML) + ('\n[review]\nengine = "joo"\n' if spec.get('review_engine') else '')
        spec = {**spec, 'repo_files': {**spec.get('repo_files', {}), **({} if spec.get('toml', 1) is None else {'.agents/kitchen.toml': toml})}}
        got = self.script_run(label, root / 'skills', 'pstack-kitchen', spec)
        got['state'] = {} if spec.get('skip') else self.state_tree(self.path / label / 'state', self.path / label)
        masked = json.loads(re.sub(r'(\.(?:closing|filing|maintainer)-)[A-Za-z0-9]{6}', r'\1<R>', json.dumps(got).replace(json.dumps(str(root / 'skills'))[1:-1], '<SK>')))
        # the scratch worktree is named for the store's directory, which carries the label
        masked = json.loads(re.sub(re.escape(label), 'X', json.dumps(masked)))
        for pattern, replacement in spec.get('mask', ()):
            masked = json.loads(re.sub(pattern, replacement, json.dumps(masked)))
        return masked

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

    def kitchen_cases(self, label, cases, expect):
        """Every case through kitchen.sh. `expect` maps every case to (rc, text), its exit status and a piece of its stdout or stderr. A case with no
        entry, or an entry with no case, fails the test, so a case whose setup stops it short (a dead world, a usage error) cannot pass as a
        failure that was never the one meant. Returns the outcomes by name."""
        self.assertEqual(sorted(expect), sorted(cases), f'{label}: expect names exactly the cases')
        outcomes = {}
        for n, (name, spec) in enumerate(cases.items()):
            got = self.kitchen_world(f'{label}{n}', spec)
            self.maxDiff = None
            rc, text = expect[name]
            self.assertEqual(got['rc'], rc, f'{name}: {got["stdout"]}{got["stderr"]}')
            self.assertIn(text, f'{got["stdout"]}\n{got["stderr"]}', name)
            if not text:
                self.assertEqual((got['stdout'], got['stderr']), ('', ''), f'{name}: an expectation of no text is for a run that prints nothing')
            outcomes[name] = got
        return outcomes

    def test_catch_behaviour(self):
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
        out = self.kitchen_cases('catch', cases, {
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

    def test_retro_behaviour(self):
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
        out = self.kitchen_cases('retro', cases, {
            'a store with briefs only': (0, 'run: 0 unit reports, 0 verified units (0 clean verdicts on the first try), 0 master w'),
            'units, verdicts, steers and events': (0, 'run demo: 1 unit reports, 2 verified units (1 clean verdicts on the first try), 0 master w'),
            'no verified unit': (0, 'run: 1 unit reports, 0 verified units (0 clean verdicts on the first try), 0 master w'),
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
        """catch, retro and new-handoff read pair.json once and die with `kitchen.sh: Expecting value...`, rc 1, writing nothing."""
        for args in (['catch', '{st}', 'gate', 'x'], ['retro', '{st}'], ['new-handoff', '{st}']):
            got = self.kitchen_world('unp' + args[0], dict(args=args, nopair=True, files={'pair.json': 'not json\n'}))
            self.assertEqual(got['rc'], 1, args[0])
            self.assertIn('pair.sh: pair.json is damaged: Expecting value: line 1 column 1 (char 0)\n', got['stderr'])
            files = lambda tree: {k: v for k, v in tree.items() if not k.endswith('/')}
            self.assertEqual(files(got['state']), {}, args[0])

    def test_feedback_on_an_unparseable_pair_json_dies_with_pythons_message(self):
        """feedback over a pair.json that does not parse: the damaged-store message, rc 1."""
        got = self.kitchen_world('fbunp', dict(args=['feedback', '{st}', '{st}/report.md'], nopair=True, files={'pair.json': 'not json\n', 'report.md': self.FEEDBACK_REPORT}))
        self.assertEqual(got['rc'], 1)
        self.assertIn('pair.sh: pair.json is damaged: Expecting value: line 1 column 1 (char 0)\n', got['stderr'])

    def test_new_handoff_behaviour(self):
        rich = self.kitchen_run_files()
        run = ['new-handoff', '{st}']
        pair = {'slug': 'demo', 'master': {'name': 'm1', 'generation': 3}, 'dispatch': {'brief': '{st}/briefs/002-y.md'}, 'verifying': {'unit': '001-x'}}
        cases = {
            'a store with units in every state': dict(args=run, files={**rich, 'queue': '{st}/briefs/003-z.md\n', 'briefs/003-z.md': '# B\n'}, pair=pair),
            'a store with briefs only': dict(args=run, pair={'slug': 'demo', 'dispatch': {}}),
            'an existing handoff is overwritten': dict(args=run, files={'session-handoff.md': 'old text\n', **rich}, pair=pair),
            'a master with no generation': dict(args=run, files=rich, pair={'slug': 'demo', 'master': {'name': 'm1'}}),
            'a master that is a string ends with the jq error': dict(args=run, files=rich, pair={'slug': 'demo', 'master': 'm1'}),
            'a dispatch that is a string is damage': dict(args=run, files=rich, pair={'slug': 'demo', 'dispatch': 'x'}),
            'a verifying that is an array': dict(args=run, files=rich, pair={'slug': 'demo', 'verifying': [1]}),
            'a dangling brief symlink is skipped': dict(args=run, files=rich, pair={'slug': 'demo'}, links={'st/briefs/009-z.md': '/nonexistent/brief.md'}),
            'no pair.json': dict(args=run, nopair=True),
            'too many arguments': dict(args=run + ['x']),
            'no arguments': dict(args=['new-handoff']),
        }
        out = self.kitchen_cases('handoff', cases, {
            'a store with units in every state': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'a store with briefs only': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'an existing handoff is overwritten': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'a master with no generation': (0, 'next: fill it (Goal, Decisions, Next; correct Units), then kitchen.sh rotate-master'),
            'a master that is a string ends with the jq error': (1, 'pair.json is damaged: master is string, not an object'),
            'a dispatch that is a string is damage': (1, 'pair.json is damaged: dispatch is string, not an object'),
            'a verifying that is an array': (1, 'pair.json is damaged: verifying is array, not an object'),
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
        self.assertEqual((string_master['rc'], 'session-handoff.md' in string_master['files']), (1, False))
        self.assertIn('pair.json is damaged: master is string, not an object', string_master['stderr'])
        self.assertIn('pair.json is damaged: dispatch is string, not an object', out['a dispatch that is a string is damage']['stderr'])
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

    def test_feedback_filing_behaviour(self):
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
            'a skill_builds entry that is a number stops before the lock is taken': {**base, 'pair': {'slug': 'demo', 'skill_builds': [1]}},
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
        out = self.kitchen_cases('fbfile', cases, {
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
            'a master that is a string': (1, 'pair.json is damaged: master is string, not an object'),
            'a master that is an array': (1, 'pair.json is damaged: master is array, not an object'),
            'a skill_builds entry that is a number stops before the lock is taken': (1, 'pair.json is damaged: skill_builds holds number, not an object'),
            'a skill_builds entry that is an array': (1, 'pair.json is damaged: skill_builds holds array, not an object'),
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
            self.assertEqual(out[name]['rc'], 1, name)
            self.assertIn('pair.json is damaged: master is', out[name]['stderr'], name)
            self.assertNotIn(f'pstack/feedback/inbox/{stem}.md', out[name]['state'], name)
        stopped = out['a skill_builds entry that is a number stops before the lock is taken']
        self.assertEqual(stopped['rc'], 1)
        self.assertNotIn('pstack/feedback/.lock/', stopped['state'])
        self.assertEqual(out['a dangling link at the name being allocated']['rc'], 0)

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
        out = self.kitchen_cases('fbinbox', cases, {
            'an empty inbox': (0, 'feedback inbox: empty'),
            'three reports of different ages': (0, '20261002-repo-c  runc  repoc          2  2d'),
            'a report that is also in done is not listed': (0, 'feedback inbox: empty'),
            'an inbox file with no gap heading ends --inbox with status 1': (1, ''),
            'a dangling link in the inbox is skipped': (0, '20261003-repo-a  runa  repoa  abc123  2  19d'),
            'a dangling link in done does not hide a report': (0, '20261003-repo-a  runa  repoa  abc123  2  19d'),
            'a close whose rm fails keeps the lock': (1, "/state/pstack/feedback/inbox/20261003-repo-a.md: Permission denied"),
            'a second close whose rm fails': (1, "/state/pstack/feedback/inbox/20261003-repo-a.md: Permission denied"),
            'closing a report': (0, 'closed: 20261003-repo-a (commit abc123)'),
            'closing with no done directory': (1, "/state/pstack/feedback/done/20261003-repo-a.md: No such file or directory"),
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
        self.assertEqual(out['a close whose rm fails keeps the lock']['stderr'], 'pair.sh: cannot remove <T>/state/pstack/feedback/inbox/20261003-repo-a.md: Permission denied\n')
        self.assertIn('pstack/feedback/.lock/', out['a close whose rm fails keeps the lock']['state'])
        self.assertNotIn('pstack/feedback/.lock/', out['closing a report']['state'])
        self.assertIn('pstack/feedback/.lock/', out['closing with no done directory']['state'])
        self.assertEqual(out['a second close whose rm fails']['rc'], 1)
        self.assertNotIn('already closed', out['a second close whose rm fails']['stdout'])

    def test_a_held_feedback_lock_ends_the_command_after_a_hundred_tries(self):
        """The one test that waits: the lock is fresh, so the command does not take it, and gives up after 100 tries at 0.1 s (about ten seconds)."""
        out = self.kitchen_cases('fblock', {'a held lock': dict(args=['feedback', '--close', 'x', 'none', 'n'], statefiles={'pstack/feedback/inbox/x.md': 'x\n'},
                                                                 statelocks={'pstack/feedback/.lock': 1})},
                                 {'a held lock': (1, 'the feedback inbox is locked')})
        self.assertRegex(out['a held lock']['stderr'], r'the feedback inbox is locked \(<T>/state/pstack/feedback/\.lock\)')

    def test_feedback_moves_a_file_into_place_or_names_the_move_it_could_not_make(self):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        from kitchen_core import feedback
        made = feedback.scratch_file(str(self.path), '.filing-')
        self.assertRegex(os.path.basename(made), r'^\.filing-.{8}$')
        self.assertEqual((os.path.getsize(made), os.stat(made).st_mode & 0o777), (0, 0o600))
        target = self.path / 'moved.md'
        feedback.move_into_place(made, str(target))
        self.assertEqual((os.path.exists(made), target.exists()), (False, True))
        with self.assertRaises(FileFailed) as caught:
            feedback.move_into_place(made, str(target))
        self.assertEqual(translate(caught.exception, 'pair.sh'), (f'pair.sh: cannot move {made} to {target}: No such file or directory\n', 1))
        self.assertEqual(feedback.aligned([['a', 'bb', 'c'], ['ddd', 'e']]), ['a    bb  c', 'ddd  e'])

    def test_init_and_stop_name_the_path_they_could_not_create_or_remove_in_their_own_words(self):
        from pstack_core.commands import init as init_command, stop as stop_command
        plain = self.path / 'a-file'
        plain.write_text('x')
        with self.assertRaises(FileFailed) as made:
            init_command.make_dir(str(plain / 'sub'))
        self.assertEqual(translate(made.exception, 'pair.sh'), (f'pair.sh: cannot create {plain}/sub: Not a directory\n', 1))
        store = self.path / 'stop-store'
        (store / 'queue').mkdir(parents=True)
        with self.assertRaises(FileFailed) as stuck:
            stop_command.clear_queue(str(store))
        self.assertEqual(translate(stuck.exception, 'pair.sh'), (f'pair.sh: cannot remove {store}/queue: Is a directory\n', 1))
        (store / 'queue').rmdir()
        skills = self.path / 'skill-root'
        (skills / 'references').mkdir(parents=True)
        orders = self.path / 'standing-orders.md'
        orders.mkdir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            init_command.install_standing_orders(Context('pstack-pair', str(skills), '/s', 'Pair', (), (), 21, ''), str(orders))
        source = f'{skills}/references/standing-orders-template.md'
        self.assertEqual(err.getvalue(), f'pair.sh: cannot copy {source} to {orders}: No such file or directory\n')
        (skills / 'references/standing-orders-template.md').write_text('t\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            init_command.install_standing_orders(Context('pstack-pair', str(skills), '/s', 'Pair', (), (), 21, ''), str(orders))
        self.assertEqual(err.getvalue(), f'pair.sh: cannot copy {source} to {orders}: Is a directory\n')
        orders.rmdir()
        init_command.install_standing_orders(Context('pstack-pair', str(skills), '/s', 'Pair', (), (), 21, ''), str(orders))
        self.assertEqual((orders.read_text(), os.stat(orders).st_mode & 0o200), ('t\n', 0o200))
        (store / 'queue').write_text('x')
        with self.assertRaises(PairJsonGone):
            stop_command.clear_queue(str(store))
        self.assertFalse((store / 'queue').exists())

    def test_the_kitchen_step_removes_what_a_rerun_leaves_and_repoints_its_log_link(self):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        from kitchen_core import step
        job = self.path / 'job'
        (job / 'tree' / 'deep').mkdir(parents=True)
        (job / 'tree' / 'deep' / 'f').write_text('x')
        (job / 'rc').write_text('0\n')
        step.remove_path(str(job / 'tree'))
        step.remove_path(str(job / 'rc'))
        step.remove_path(str(job / 'absent'))
        self.assertEqual(sorted(os.listdir(job)), [])
        bare = Context('pstack-pair', '/x', '/s', 'Pair', (), (), 21, '')
        step.point_at(bare, str(job / 'out'), 'out.1')
        step.point_at(bare, str(job / 'out'), 'out.2')
        self.assertEqual((os.readlink(job / 'out'), sorted(os.listdir(job))), ('out.2', ['out']))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            step.point_at(bare, str(job / 'missing' / 'out'), 'out.3')
        self.assertEqual(err.getvalue(), f'pair.sh: cannot point {job}/missing/out at out.3: No such file or directory\n')
        self.assertEqual(sorted(os.listdir(job)), ['out'])

    def test_the_kitchen_verify_block_names_a_brief_it_cannot_read_in_its_own_words(self):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            block = kitchen_variant.verify_block(Context('pstack-pair', '/x', '/s', 'Pair', (), (), 21, ''), str(self.path / 'no-brief.md'))
        self.assertEqual((block, err.getvalue()), ('', f'pair.sh: cannot read {self.path}/no-brief.md: No such file or directory\n'))

    def test_the_feedback_lock_is_a_directory_waited_on_taken_over_when_stale_and_given_up_after_a_hundred_tries(self):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        from kitchen_core import feedback
        fb = self.path / 'lockfb'
        fb.mkdir()
        # a lock held by another call (a directory): the command waits on it, then takes it when it goes, 0.1 s at a time
        (fb / '.lock').mkdir()
        slept = []

        def release(delay):
            slept.append(delay)
            if len(slept) == 3:
                (fb / '.lock').rmdir()

        feedback.fb_lock(str(fb), sleep=release)
        self.assertEqual(slept, [0.1, 0.1, 0.1])
        self.assertTrue((fb / '.lock').is_dir())
        # an old lock (over a minute) is taken over
        os.utime(fb / '.lock', (time.time() - 300, time.time() - 300))
        feedback.fb_lock(str(fb), tries=1, sleep=lambda delay: self.fail('slept on a stale lock'))
        # and gives up on the hundredth failure after ninety-nine waits
        fresh = []
        (fb / '.lock').rmdir() if (fb / '.lock').exists() else None
        (fb / '.lock').mkdir()
        with self.assertRaises(Exception) as caught:
            feedback.fb_lock(str(fb), sleep=fresh.append)
        self.assertEqual((len(fresh), caught.exception.message), (99, f'the feedback inbox is locked ({fb}/.lock)'))

    def test_maintainer_behaviour(self):
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
            'status when herdr answers a result that is a string': dict(args=['maintainer', 'status'], statefiles=registered, agent_get='{"result":"x"}\n'),
            'on when herdr answers an agent of null': dict(args=['maintainer', 'on', '--pane', 'p7'], agent_get='{"result":{"agent":null}}\n'),
            'no arguments': dict(args=['maintainer']),
            'an unknown subcommand': dict(args=['maintainer', 'stop']),
        }
        out = self.kitchen_cases('maint', cases, {
            'on with --pane': (0, 'maintainer: mt on p7'),
            'on with the pane of the shell': (0, 'maintainer: mt on p7'),
            'on again replaces the registration': (0, 'maintainer: mt on p7'),
            'on without herdr': (0, 'maintainer: mt on p7'),
            'on with no pane': (1, 'pair.sh: no pane: pass --pane ID'),
            'on with an unknown option': (1, 'pair.sh: unknown option --bogus'),
            'on for a pane with no agent': (1, 'pair.sh: herdr has no agent on pane p9'),
            'off': (0, 'maintainer: none'),
            'off with none': (0, 'maintainer: none'),
            'off whose rm fails': (1, "/state/pstack/feedback/maintainer.json: Is a directory"),
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
            'status when herdr answers a result that is a string': (0, ': agent mt moved off pane p7'),
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
            self.assertRegex(out[name]['stderr'], r'maintainer\.json is (not JSON|\w+, not an object)', name)
        self.assertEqual((out['off whose rm fails']['rc'], out['off whose rm fails']['stderr']),
                         (1, 'pair.sh: cannot remove <T>/state/pstack/feedback/maintainer.json: Is a directory\n'))
        self.assertIn('moved off pane p7', out['status when herdr answers an agent of null']['stdout'])
        self.assertIn('herdr reply is not JSON\n', out['status when herdr answers text that is not JSON']['stderr'])
        self.assertIn('herdr reply has no object on the way to result.agent.pane_id\n', out['status when herdr answers a result that is a string']['stderr'])
        self.assertIn('herdr reports no name for the agent on pane p7', out['on when herdr answers an agent of null']['stderr'])

    def test_the_maintainer_record_is_written_byte_for_byte(self):
        live = dict(args=['maintainer', 'on', '--pane', 'p7'], agents={'mt': 'idle claude'}, panes={'p7': 'mt'})
        held = dict(live, agent_get='{"result":{"agent":{"name":"mt","pane_id":"p7","agent_session":{"kind":"id","value":"s-1"}}}}\n')
        for n, (spec, session) in enumerate(((live, 'null'), (held, '"s-1"'))):
            got = self.kitchen_world(f'maintrecord{n}', spec)['state']['pstack/feedback/maintainer.json']
            got = re.sub(r'"since": "[0-9T:Z-]+"', '"since": "<TS>"', got)
            self.assertEqual(got, '{\n  "name": "mt",\n  "pane": "p7",\n  "session": ' + session + ',\n  "since": "<TS>"\n}\n')

    CLOSE_ROLES = {'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}, 'consultant': {'name': 'c1', 'pane_id': 'p2', 'kind': 'codex'},
                   'sidekick': {'name': 's1', 'pane_id': 'p1', 'kind': 'claude'}}

    def test_close_behaviour(self):
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
            'a Prime role whose session is not an object keeps its pane': dict(
                with_roles(verifier={**self.CLOSE_ROLES['verifier'], 'kind': 'prime-agent', 'session': 'sess-1'}), agents={'c1': 'idle codex', 's1': 'idle claude'}, args=run),
            'a role that does not exit keeps its pane': dict(live, args=run, noexit=True, clock=True),
            '--force is only read as the second argument': dict(live, args=['close', '--force', '{st}']),
            'a third argument is ignored': dict(live, args=run + ['--force', 'x']),
            'no arguments': dict(args=['close']),
            'no pair.json': dict(args=run, nopair=True),
            'outside herdr': dict(live, args=run, noenv=True),
        }
        out = self.kitchen_cases('close', cases, {
            'an idle run closes verifier, consultant and sidekick': (0, 'closed verifier v1 (p3)'),
            'an open verification refuses': (5, 'pair.sh: a verification is open (001); finish it (kitchen.sh verify'),
            'an open verification and --force': (0, 'closed verifier v1 (p3)'),
            'a working sidekick refuses': (5, 'pair.sh: the sidekick is working; kitchen.sh stop'),
            'a working sidekick and --force': (0, 'closed verifier v1 (p3)'),
            'a role with no pane is skipped': (0, 'closed sidekick s1 (p1)'),
            'a pane already gone': (0, 'closed verifier v1 (p3)'),
            'a role already closed is skipped': (0, 'closed consultant c1 (p2)'),
            'a consultant that is a string': (1, 'pair.json is damaged: consultant is string, not an object'),
            'a verifier that is an array': (1, 'pair.json is damaged: verifier is array, not an object'),
            'a sidekick that is false': (1, 'pair.json is damaged: sidekick is boolean, not an object'),
            'a verifying that is a string': (1, 'pair.json is damaged: verifying is string, not an object'),
            'a Prime role with a session': (5, 'closed consultant c1 (p2)'),
            'a Prime role whose session is not an object keeps its pane': (5, 'recorded Prime session for verifier is not an object; inspect its pane before cleanup'),
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
        kept = out['a Prime role whose session is not an object keeps its pane']
        self.assertNotIn('pane close p3', kept['calls'])
        self.assertEqual(json.loads(kept['files']['pair.json'])['verifier'], {**self.CLOSE_ROLES['verifier'], 'kind': 'prime-agent', 'session': 'sess-1'})
        self.assertEqual(out['an open verification and --force']['rc'], 0)
        self.assertIn('pair.json is damaged: consultant is string, not an object', out['a consultant that is a string']['stderr'])
        self.assertNotIn('closed consultant', out['a consultant that is a string']['stdout'])

    HANDOFF_FILLED = '# Handoff\n\n## Goal\n\nfinish the port\n\n## Next\n\nunit 025\n'

    def test_a_flag_with_no_value_dies_naming_the_flag(self):
        """`maintainer on --pane` and `rotate-master --model` with no value print `<flag> needs a value` and exit 1."""
        for args in (['rotate-master', '{st}', '--model'], ['maintainer', 'on', '--pane']):
            got = self.kitchen_world('unbound' + args[0], dict(args=args, pair={'slug': 'demo', 'master': {'name': 'm1', 'pane_id': 'p0'}},
                                                               files={'session-handoff.md': self.HANDOFF_FILLED}, panes={'p0': 'm1'}))
            self.assertEqual(got['rc'], 1, args)
            self.assertEqual(got['stderr'], f'pair.sh: {args[-1]} needs a value\n')

    def test_rotate_master_behaviour(self):
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
            'a master with no recorded pane that is not live': dict(with_master(pane_id=''), master=None),
            'a master with no recorded name that is not live': dict(with_master(name=''), master=None),
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
            'outside herdr': dict(world(), noenv=True, env={'HERDR_ENV': ''}),
        }
        # every case walks a fixed process chain: the real one changes under a detached gate, between the base's run and the port's
        out = self.kitchen_cases('rotm', {name: {'pstable': ps_none, **spec} for name, spec in cases.items()}, {
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
            'the next master does not start': (2, 'pair.sh: the next master did not start in p1; you stay the master (generation 1), so carry on and rotate at a later boundary'),
            'the next master asks for folder trust': (5, 'asks whether to trust'),
            'a hand-over already open': (5, 'pair.sh: a hand-over is open: m1 has not taken over yet; it runs kitchen.sh take-master'),
            'a master that is not live': (5, "pair.sh: master m1 is not live in p0; run this from the master's own session"),
            'a master with no recorded pane that is not live': (5, "pair.sh: master m1 is not live; run this from the master's own session"),
            'a master with no recorded name that is not live': (5, "pair.sh: master is not live in p0; run this from the master's own session"),
            'a master that is not Claude': (5, 'pair.sh: rotate-master restarts a Claude master; m1 is codex'),
            'an unfinished rotation keeps its generation': (0, '/st/sessions/master-4-handoff.md (generation 4, session sess-1)'),
            'sessions are replaced by the same session': (0, '/st/sessions/master-2-handoff.md (generation 2, session sess-2)'),
            'a master that is a string ends with the jq error': (1, 'pair.json is damaged: master is string, not an object'),
            'a master with sessions that is not a list': (1, 'pair.json is damaged: sessions is string, not an array'),
            'a master with sessions that is 0': (1, 'pair.json is damaged: sessions is number, not an array'),
            'a master with sessions that is an empty string': (1, 'pair.json is damaged: sessions is string, not an array'),
            'a master with sessions that is false': (0, '/st/sessions/master-1-handoff.md (generation 1, session sess-1)'),
            'a master with sessions that is null': (0, '/st/sessions/master-1-handoff.md (generation 1, session sess-1)'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
        })
        ok = out['a filled handoff']
        self.assertEqual(ok['rc'], 0, ok['stderr'])
        self.assertIn('next master: demo-master-g2 (generation 2) in ', ok['stdout'])
        self.assertIn('--permission-mode auto', ok['calls'])
        self.assertNotIn('--model', ok['calls'])
        self.assertIn('--remote-control demo-master-g2', ok['calls'])
        self.assertIn('--model opus-x', out['the model found in the process tree']['calls'])
        self.assertIn('--model sonnet-y', out['the model found behind node']['calls'])
        self.assertNotIn('--model', out['no claude in the process tree']['calls'])
        self.assertNotIn('--remote-control', out['remote control off']['calls'])
        self.assertEqual(out['the next master does not start']['rc'], 2)
        self.assertEqual(out['a handoff with a placeholder']['rc'], 1)
        self.assertEqual(out['a master that is a string ends with the jq error']['rc'], 1)
        self.assertIn('(generation 4, session sess-1)', out['an unfinished rotation keeps its generation']['stdout'])
        self.assertIn('master-4-handoff.md', out['an unfinished rotation keeps its generation']['stdout'])

    def test_take_master_behaviour(self):
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
        out = self.kitchen_cases('takem', cases, {
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
            'a master that is a string': (1, 'pair.json is damaged: master is string, not an object'),
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
        self.assertEqual(out['a master that is a string']['rc'], 1)


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











    def test_the_prime_smoke_script_drives_the_python_adapter_and_its_names_resolve(self):
        """tests/prime_herdr_smoke.py needs a real Prime, so only what can be proved here is: the Python function it calls to stop the role exists
        in the core."""
        text = (root / 'tests/prime_herdr_smoke.py').read_text()
        self.assertIn('exits.stop_prime_role', text)
        self.assertTrue(callable(exits.stop_prime_role))

    def test_update_waits_out_a_held_lock_and_dies_when_it_is_never_released(self):
        """The held lock is retried as the bash did: the lock is held when the call starts and released at the first poll. No real wait: the poll is the test's own function."""
        st = self.path / 'lockwait'
        st.mkdir()
        (st / 'pair.json').write_text('{"a": 1}\n')
        lock = st / '.pair.lock'
        lock.mkdir()
        polls = []

        def release(seconds):
            polls.append(seconds)
            lock.rmdir()

        with mock.patch.object(store.time, 'sleep', release), mock.patch.dict(os.environ, {'PAIR_LOCK_POLL_S': '0.25'}):
            store.update(str(st), lambda doc: doc.update(b=2))
        self.assertEqual(polls, [0.25])
        self.assertEqual(json.loads((st / 'pair.json').read_text()), {'a': 1, 'b': 2})
        self.assertFalse(lock.exists())
        lock.mkdir()
        with mock.patch.object(store.time, 'sleep', lambda s: None), mock.patch.dict(os.environ, {'PAIR_LOCK_TRIES': '3'}):
            with self.assertRaises(LockHeld) as caught:
                store.update(str(st), lambda doc: doc.update(c=3))
        self.assertIn('pair.json lock held for 10s', caught.exception.message)

    def test_the_helpers_a_mutate_reads_through_say_what_is_damaged_and_a_bug_in_a_mutate_propagates_and_writes_nothing(self):
        st = self.path / 'damage'
        st.mkdir()
        text = '{"a": "x", "b": {"c": 1}, "n": "7", "l": ["u", 3], "ok": {"k": [1]}, "z": 5}\n'
        (st / 'pair.json').write_text(text)
        for name, mutate, message in (('member of a string', lambda doc: store.member(doc, 'a'), 'a is string, not an object'),
                                      ('peek of a number', lambda doc: store.peek(doc, 'z'), 'z is number, not an object'),
                                      ('listing of an object', lambda doc: store.listing(doc, 'b'), 'b is object, not an array'),
                                      ('names holding a number', lambda doc: store.names(doc, 'l'), 'l holds number, not a string'),
                                      ('count of a string', lambda doc: store.count(doc, 'n'), 'n is string, not an integer'),
                                      ('count of a boolean', lambda doc: store.count({'n': True}, 'n'), 'n is boolean, not an integer')):
            with self.assertRaises(DamagedStore) as caught:
                store.update(str(st), mutate)
            self.assertEqual((caught.exception.code, caught.exception.message), (1, f'pair.json is damaged: {message}'), name)
            self.assertEqual((st / 'pair.json').read_text(), text, name)
            self.assertFalse((st / 'pair.json.tmp').exists(), name)
            self.assertFalse((st / '.pair.lock').exists(), name)
        self.assertEqual((store.peek({}, 'r'), store.peek({'r': None}, 'r'), store.listing({'x': False}, 'x'), store.count({}, 'n'), store.count({'n': False}, 'n')), ({}, {}, [], 0, 0))
        for name, mutate, error in (('a TypeError', lambda doc: doc['a'] + 1, TypeError), ('an AttributeError', lambda doc: doc['a'].get('k'), AttributeError),
                                    ('a KeyError', lambda doc: doc['b']['d'], KeyError)):
            with self.assertRaises(error):
                store.update(str(st), mutate)
            self.assertEqual((st / 'pair.json').read_text(), text, name)
            self.assertFalse((st / 'pair.json.tmp').exists(), name)
            self.assertFalse((st / '.pair.lock').exists(), name)

    def test_json_type_names_the_six_json_types(self):
        self.assertEqual([store.json_type(v) for v in (None, True, 3, 1.5, 'x', [], {})], ['null', 'boolean', 'number', 'number', 'string', 'array', 'object'])

    def test_a_wrong_typed_value_is_damage_through_a_real_read_command_and_a_real_write_command(self):
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = self.path / 'state/pstack/pair/demo'
        doc = json.loads((st / 'pair.json').read_text())
        doc['sidekick'] = 'x'
        (st / 'pair.json').write_text(json.dumps(doc))
        r = self.run_pair('failover', str(st))
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: pair.json is damaged: sidekick is string, not an object\n'))
        for role in ('master', 'sidekick'):
            doc['master'], doc['sidekick'] = {'name': 'm'}, {'name': 's'}
            doc[role] = [1]
            (st / 'pair.json').write_text(json.dumps(doc))
            r = self.run_pair('status', str(st))
            self.assertEqual((r.returncode, r.stderr), (1, f'pair.sh: pair.json is damaged: {role} is array, not an object\n'), role)
        doc['sidekick'], doc['master'] = {}, 'y'
        (st / 'pair.json').write_text(json.dumps(doc))
        before = (st / 'pair.json').read_bytes()
        r = self.run_pair('init', 'demo')
        self.assertEqual((r.returncode, r.stderr), (1, 'pair.sh: pair.json is damaged: master is string, not an object\n'))
        self.assertEqual((st / 'pair.json').read_bytes(), before)
        self.assertFalse((st / 'pair.json.tmp').exists())
        self.assertFalse((st / '.pair.lock').exists())

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
            'a claude sidekick': run(world('claude')),
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




















    def test_permission_reads_the_session_transcript_and_falls_back_to_auto_when_it_names_no_mode(self):
        """Through the real pair.sh, with a ps that finds no agent above the shell: the last permissionMode of the session's transcript, else the
        settings files, else auto. A transcript with no permissionMode used to end the command with status 1; it now falls through."""
        home, cwd, bin_dir = self.path / 'permhome', self.path / 'permcwd', self.path / 'permbin'
        for d in (home, cwd, bin_dir):
            d.mkdir()
        (self.path / 'noproc').mkdir()
        transcript = home / '.claude/projects' / re.sub(r'[/.]', '-', str(cwd)) / 's1.jsonl'
        transcript.parent.mkdir(parents=True)
        env = {**self.env, 'HOME': str(home), 'PWD': str(cwd), 'CLAUDE_CODE_SESSION_ID': 's1', 'PAIR_PROC_ROOT': str(self.path / 'noproc')}
        run = lambda: subprocess.run([str(scripts / 'pair.sh'), 'permission'], cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120)
        for text, want in (('{"type":"user"}\n', 'auto'), ('{"permissionMode":"plan"}\n{"permissionMode":"acceptEdits"}\n', 'acceptEdits')):
            transcript.write_text(text)
            got = run()
            self.assertEqual((got.returncode, got.stdout, got.stderr), (0, want + '\n', ''), text)

    def test_listings_go_in_code_point_order_and_ties_in_time_go_by_name_without_the_extension(self):
        """sort -u of the touched paths puts upper case before lower case and a non-ASCII letter after both; two files modified at the same time are
        newest-first by name without .md, so a unit's report comes before its steer response and before a later unit's."""
        from pstack_core import checkin
        self.assertEqual(checkin.sort_u(['b', 'a', 'B', 'a', '\u00e9', 'A', 'a-b', 'a.b', 'a_b']), ['A', 'B', 'a', 'a-b', 'a.b', 'a_b', 'b', '\u00e9'])
        top = self.path / 'ties'
        top.mkdir()
        for name in ('010-stop.md', '010-qseven.md', '003-build-s1.md', '003-build.md'):
            (top / name).write_text('x\n')
            os.utime(top / name, ns=(10**18, 10**18))
        got = [os.path.basename(p) for p in store.newest_first([str(p) for p in sorted(top.iterdir(), reverse=True)])]
        self.assertEqual(got, ['003-build.md', '003-build-s1.md', '010-qseven.md', '010-stop.md'])
        os.utime(top / '010-stop.md', ns=(10**18 + 1, 10**18 + 1))
        self.assertEqual(os.path.basename(store.newest([str(p) for p in top.iterdir()])), '010-stop.md')
        self.assertIsNone(store.newest([]))

    def test_an_error_message_is_cut_at_240_characters_not_bytes(self):
        """The pi session error, which the master reads as the provider's reason, keeps 240 characters of a message in any script."""
        session = self.path / 'pi.jsonl'
        message = '\u00e9' * 300
        session.write_text(json.dumps({'message': {'role': 'assistant', 'stopReason': 'error', 'errorMessage': message}}) + '\n', encoding='utf-8')
        self.assertEqual(providers.agent_session_error(str(session)), '\u00e9' * 240)

    def test_a_path_with_trailing_slashes_takes_its_name_from_before_them(self):
        """A plan or brief path given with trailing slashes is read as the name before them: its unit number is the first three characters of that
        name, and the marker the prompt waits to see leave the sidekick's input is that name. (pair.sh resolves a path before it gets there, so the
        slashes reach these two functions only when called as here.)"""
        from pstack_core import replies, send as send_module
        for path in ('/s/plans/012-x.md', '/s/plans/012-x.md/', '/s/plans/012-x.md//'):
            self.assertEqual(replies.first3(path), '012', path)
            markers = []
            with mock.patch.object(send_module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')), \
                    mock.patch.object(send_module, 'submit_typed', side_effect=lambda name, marker: markers.append(marker) or 0):
                send_module.prompt_sidekick('demo-sidekick', f'pstack-pair BRIEF {path}')
            self.assertEqual(markers, ['012-x.md'], path)

    def test_provider_error_context_matches_grep(self):
        texts = ['Error: Reached free model rate limit. Your limit will reset in 50 minutes.', 'nothing to see', 'x' * 100 + ' Rate-Limit ' + 'y' * 300,
                 'a rate limit then another RATE LIMIT and overloaded close by', 'Usage limit reached. wait for usage to reset at 5pm; billing_error',
                 '\u00e9' * 70 + ' capacity issues ' + '\u00e9' * 120, 'caf\u00e9 \u2192 unauthorized \u2192 forbidden \u2192 Too Many Requests',
                 'first line rate limit\nsecond line fetch failed\nthird', 'ECONNRESET', 'x rate_limit x', 'ratelimit', 'rate\tlimit rate  limit', '']
        for text in texts:
            for before, after in ((60, 100), (60, 60)):
                ours = providers.error_context(text, before, after)
                r = subprocess.run(['bash', '-c', f'{self.provider_re_line()}\nprintf "%s\\n" "$1" | grep -oiE ".{{0,{before}}}($provider_error_re).{{0,{after}}}" || true', 'x', text],
                                   env={**self.env, 'LC_ALL': 'en_US.UTF-8'}, capture_output=True, check=True, timeout=120)
                self.assertEqual('\n'.join(ours).encode('utf-8', 'surrogateescape') + (b'\n' if ours else b''), r.stdout, (text, before, after))

    def provider_re_line(self):
        return f"provider_error_re='{providers.PROVIDER_ERROR_RE}'"











    def test_whole_numbers_and_seconds_are_read_as_digits_and_nothing_else(self):
        for text, want in (('5', 5), ('0', 0), ('010', 10), ('007', 7), ('99999999999999999999', 99999999999999999999)):
            self.assertEqual(numbers.whole_number(text, '--timeout', 'milliseconds'), want, text)
        for text in ('', '-1', '+1', ' 5', '5 ', '1.5', '1+1', '0x10', '1e3', '\u0665', 'abc', '5_0'):
            with self.assertRaises(BadNumber) as caught:
                numbers.whole_number(text, '--timeout', 'milliseconds')
            self.assertEqual((caught.exception.code, caught.exception.message), (1, '--timeout must be a whole number of milliseconds'), repr(text))
        for name, text, want in (('N', '3', 3), ('N', '0', 0), ('N', '', 7)):
            with mock.patch.dict(os.environ, {name: text}):
                self.assertEqual(numbers.env_int(name, 7), want, text)
        for text in ('abc', '1.5', '-1', ' 5'):
            with mock.patch.dict(os.environ, {'N': text}), self.assertRaises(BadNumber) as caught:
                numbers.env_int('N', 7)
            self.assertEqual(caught.exception.message, 'N must be a whole number', text)
        for text, want in (('0.25', 0.25), ('2', 2.0), ('.5', 0.5), ('3.', 3.0), ('', 1.5)):
            with mock.patch.dict(os.environ, {'N': text}):
                self.assertEqual(numbers.env_float('N', 1.5), want, text)
        for text in ('abc', '-1', '1e3', 'nan', 'inf', '0x1', ' 1'):
            with mock.patch.dict(os.environ, {'N': text}), self.assertRaises(BadNumber) as caught:
                numbers.env_float('N', 1.5)
            self.assertEqual(caught.exception.message, 'N must be a number', text)

    def test_with_timeout_gives_the_commands_exit_124_when_it_runs_out_and_127_when_it_is_missing(self):
        from pstack_core import prime
        done = prime.with_timeout('5', ['sh', '-c', 'echo out; exit 3'])
        self.assertEqual((done.returncode, done.stdout), (3, 'out\n'))
        self.assertEqual(prime.with_timeout('0.2', ['tail', '-f', os.devnull]).returncode, 124)
        missing = prime.with_timeout('5', ['no-such-command-here'])
        self.assertEqual((missing.returncode, missing.stdout), (127, ''))
        self.assertEqual(prime.with_timeout('5', ['sh', '-c', 'kill -TERM $$']).returncode, 143)
        self.assertEqual(prime.with_timeout('5', ['sh', '-c', 'kill -HUP $$']).returncode, 129)

    def test_fresh_kind_is_devin_pi_and_prime_agent_only(self):
        for kind in ('devin', 'pi', 'prime-agent', 'claude', 'codex', 'null', '', 'Devin', 'pi '):
            self.assertEqual(send.fresh_kind(kind), kind in ('devin', 'pi', 'prime-agent'), kind)

    def test_load_variant_picks_the_skills_class_by_path(self):
        names = {}
        for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen'):
            ctx = Context(skill, str(root / 'skills' / skill), '/s', 'Pair', (), (), 21, '')
            self.assertIsNone(ctx._variant)
            names[skill] = [c.__name__ for c in type(ctx.variant).__mro__[:-1]]
            self.assertIs(ctx.variant, ctx.variant)
        self.assertEqual(names, {'pstack-pair': ['Variant'], 'pstack-pair-guided': ['GuidedVariant', 'Variant'],
                                 'pstack-trio': ['TrioVariant', 'Variant'], 'pstack-kitchen': ['KitchenVariant', 'TrioVariant', 'Variant']})


    def test_require_filled_refuses_template_text_and_a_guided_alternative(self):
        f = self.path / 'f.md'
        guided = '^(decided by|status): .*\\|'
        ctxs = {'plain': Context('x', '', '', '', (), (), 1, ''), 'guided': Context('x', '', '', '', (), (), 1, guided)}
        refused = {('status: done | partial\n', 'guided'), ('a {{b}} c\n', 'plain'), ('a {{b}} c\n', 'guided')}
        for text in ('status: done\n', 'status: done | partial\n', 'decided by: human\n', 'a {{b}} c\n', 'x\n'):
            f.write_text(text)
            for name, ctx in ctxs.items():
                if (text, name) in refused:
                    with self.assertRaises(Unfilled) as caught:
                        review.require_filled(ctx, str(f))
                    self.assertEqual((caught.exception.message, caught.exception.code), (f'{f} still has unfilled placeholders', 1), (text, name))
                else:
                    self.assertIsNone(review.require_filled(ctx, str(f)), (text, name))
        self.assertIsNone(review.require_filled(ctxs['guided'], str(self.path / 'missing.md')))

    def run_pair(self, *args, script=None, extra_env=None):
        env = {**self.env, 'FAKE': str(self.path), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0', 'HERDR_SOCKET_PATH': str(self.path / 'herdr.sock'),
               'XDG_STATE_HOME': str(self.path / 'state'), 'HOME': str(self.path), **(extra_env or {})}
        return subprocess.run([str(script or scripts / 'pair.sh'), *args], env=env, capture_output=True, text=True, check=False, timeout=120)

    def test_a_damaged_pair_json_is_one_message_and_exit_1_from_pair_sh_and_kitchen_sh(self):
        """Decision b: a pair.json that does not parse, or is not an object, is `pair.json is damaged: ...`, rc 1, whichever script reads it."""
        self.assertEqual(self.run_pair('init', 'demo').returncode, 0)
        st = self.path / 'state/pstack/pair/demo'
        kitchen = root / 'skills/pstack-kitchen/scripts/kitchen.sh'
        for text, reason in (('not json\n', 'Expecting value: line 1 column 1'), ('[1]\n', 'expected an object, found array')):
            (st / 'pair.json').write_text(text)
            for script, args in ((None, ('status', str(st))), (kitchen, ('new-handoff', str(st)))):
                r = self.run_pair(*args, script=script)
                self.assertEqual(r.returncode, 1, (text, args))
                self.assertIn(f'pair.json is damaged: {reason}', r.stderr, (text, args))

    def test_status_without_a_pair_json_says_so_and_exits_2(self):
        st = self.path / 'state/pstack/pair/nope'
        st.mkdir(parents=True)
        r = self.run_pair('status', str(st))
        self.assertEqual((r.returncode, r.stderr), (2, f'pair.sh: no pair.json in {st}; run: pair.sh init <slug>\n'))
        self.assertTrue(r.stdout.startswith('# Pair status\n'))

    def test_the_refusals_that_once_imitated_jq_grep_and_tee_say_what_failed_in_the_programs_own_words(self):
        """Each message names the command (pair.sh) and the path or value at fault; none leaves a pair.json.tmp behind."""
        # status.md that cannot be opened: the status text still prints
        from pstack_core.commands.status import Tee
        target = self.path / 'status-dir'
        target.mkdir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tee = Tee(Context('pstack-pair', '/x', '/s', 'Pair', (), (), 21, ''), str(target))
        self.assertEqual((err.getvalue(), tee.failed), (f'pair.sh: cannot write {target}: Is a directory\n', True))
        # stop on a store whose pair.json is gone
        from pstack_core.commands import stop as stop_command
        bare = self.path / 'bare'
        bare.mkdir()
        with self.assertRaises(PairJsonGone) as caught:
            stop_command.clear_queue(str(bare))
        self.assertEqual(translate(caught.exception, 'pair.sh'), (f'pair.sh: pair.json is missing from {bare}\n', 2))
        self.assertEqual(sorted(os.listdir(bare)), [])
        # a report that is gone when the wait reads its status
        gone = str(self.path / 'gone.md')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(wait.status_of(Context('pstack-pair', '/x', '/s', 'Pair', (), (), 21, ''), gone), '')
        self.assertEqual(err.getvalue(), f'pair.sh: cannot read {gone}: No such file or directory\n')
        # a covered last unit whose report is gone
        units = self.kitchen_core_module('units')
        err = io.StringIO()
        with mock.patch.object(units, 'brief_or_nothing', return_value='/b/001-x.md'), mock.patch.object(units, 'dispatch_head', return_value='abc'), \
                mock.patch.object(units, 'expected_report', return_value=gone), contextlib.redirect_stderr(err), self.assertRaises(UnitNotCheckable) as caught:
            units.unit_range(Context('pstack-pair', '/x', '/s', 'Pair', (), (), 21, ''), '/st', ['001'], covered=['001'])
        self.assertEqual((err.getvalue(), caught.exception.message), (f'pair.sh: cannot read {gone}: No such file or directory\n', "unit 001's report has no head:"))
        # a step row whose first column is not a JSON number
        spec = self.quiet_wait_cases()['steps wake an escalated brief']
        got = self.kitchen_world('badrow', {**spec, 'files': {**spec['files'], 'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n02\t{c2}\t6\t\ttwo\n'}})
        self.assertEqual((got['rc'], got['stderr']), (2, "pair.sh: the last step row does not start with a JSON step number: '02'\n"), got['stdout'])
        self.assertNotIn('pair.json.tmp', got['files'])
        # an unreadable steps file, and an unreadable steer
        top, st, run = self.own_world('unreadable', files={'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n'})
        steps = st / 'steps/001-x.tsv'
        steps.chmod(0)
        r = run('step', '{st}', '{c2}', 'two')
        steps.chmod(0o644)
        self.assertEqual((r.returncode, r.stderr), (2, f'pair.sh: cannot read {steps}: Permission denied\n'), r.stdout)
        steer = st / 'steers/001-x-s1.md'
        steer.write_text('## Direction\n\nx\n')
        steer.chmod(0)
        r = run('steer', '{st}', str(steer))
        steer.chmod(0o644)
        self.assertEqual(r.stderr, f'pair.sh: cannot read {steer}: Permission denied\npair.sh: not a steer file: {steer}\n', r.stdout)
        self.assertEqual(r.returncode, 1)

    def test_no_pair_command_starts_jq(self):
        """The failing jq of tests/run.py logs its arguments. The pair scenario drives every ported command but failover through the real
        pair.sh against the fake herdr, and failover is driven here: no call reaches jq."""
        directory = self.path / 'tripwire'
        directory.mkdir()
        run_module.write_tripwire_jq(directory)
        log = self.path / 'jq-calls.log'
        env = {**inherited_env(), 'PATH': f'{directory}:{os.environ["PATH"]}', 'TRIPWIRE_LOG': str(log)}
        out = subprocess.run([root / 'tests/pstack-pair/scenario.sh', root / 'skills', 'pair'], env=env, capture_output=True, text=True, check=False, timeout=600).stdout
        self.assertIn('$ pair.sh status', out)
        self.assertIn('$ pair.sh wait', out)
        calls = log.read_text().splitlines() if log.exists() else []
        failover = {**self.failover_cases()['an idle sidekick'], 'topfiles': {'trip/jq': (run_module.TRIPWIRE_JQ, 0o755)},
                    'envfill': {'PATH': '{T}/trip:' + self.env['PATH'], 'TRIPWIRE_LOG': '{st}/jq.log'}}
        got = self.script_run('tripwire-failover', root / 'skills', 'pstack-pair', failover)
        self.assertEqual(got['rc'], 0, got['stderr'])
        self.assertIn('failover: claude -> devin', got['stdout'])
        calls += got['files'].get('jq.log', '').splitlines()
        self.assertEqual(calls, [])

    def test_get_reads_a_dotted_path_with_a_default_and_env_int_refuses_a_non_integer(self):
        st = self.path / 'st'
        st.mkdir()
        (st / 'pair.json').write_text('{"sidekick": {"kind": "pi", "generation": 3}, "consultant": "gone"}')
        self.assertEqual(store.get(str(st), 'sidekick.kind'), 'pi')
        self.assertEqual(store.get(str(st), 'sidekick.generation'), 3)
        self.assertIsNone(store.get(str(st), 'sidekick.model'))
        self.assertEqual(store.get(str(st), 'sidekick.model', 'none'), 'none')
        self.assertEqual(store.get(str(st), 'consultant.name', 'x'), 'x')
        self.assertEqual(store.get(str(st), 'verifier.kind', 'x'), 'x')
        with mock.patch.dict(os.environ, {'X_KNOB': '12'}):
            self.assertEqual(numbers.env_int('X_KNOB', 5), 12)
        with mock.patch.dict(os.environ, {'X_KNOB': ''}):
            self.assertEqual(numbers.env_int('X_KNOB', 5), 5)
        with mock.patch.dict(os.environ, {'X_KNOB': '1.5'}):
            with self.assertRaises(BadNumber) as caught:
                numbers.env_int('X_KNOB', 5)
        self.assertEqual((caught.exception.message, caught.exception.code), ('X_KNOB must be a whole number', 1))

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
        # a path that names no steer (an empty word, a directory that is not there) ends the command with status 1, and writes no steer
        steers = sorted(p.name for p in (st / 'steers').iterdir())
        for unresolvable in ('', str(st / 'nodir/s1.md'), str(st / 'steers/002-one-s1.md/under-a-file')):
            got = out(self.run_pair('new-steer', str(st), '002', '--supersedes', unresolvable))
            self.assertEqual(got[:2], (1, ''), unresolvable)
            self.assertTrue(got[2].startswith('pair.sh: superseded steer not found: '), (unresolvable, got))
        self.assertEqual(sorted(p.name for p in (st / 'steers').iterdir()), steers)


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




    def fake_herdr_dir(self, label, state, busy):
        fake = self.path / f'fake-{label}'
        for d in ('agents', 'busy', 'screen', 'panes'):
            (fake / d).mkdir(parents=True)
        if state != 'absent':
            (fake / 'agents/w').write_text(f'{state} devin\n')
        if busy:
            (fake / 'busy/w').write_text('')
        return fake

    def test_agent_status_reads_every_state_and_a_busy_pane_makes_a_settled_agent_working(self):
        for i, (state, busy) in enumerate((('idle', False), ('working', False), ('done', True), ('done', False), ('idle', True),
                                           ('blocked', False), ('absent', False))):
            fake = self.fake_herdr_dir(f'py{i}', state, busy)
            with mock.patch.dict(os.environ, {**self.env, 'FAKE': str(fake)}):
                status, kind = panes.agent_status('w'), panes.agent_kind('w')
            self.assertEqual(status, 'working' if busy and state in ('idle', 'done') else state, (state, busy))
            self.assertEqual(kind, '' if state == 'absent' else 'devin', (state, busy))

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
            pair.write_text(json.dumps({**json.loads(pair.read_text()), 'second': True}))

        with mock.patch.object(store.time, 'sleep', holder_finishes):
            store.update(str(st), lambda doc: doc.update(py='1'))
        self.assertEqual(waits, [original])
        final = json.loads(pair.read_text())
        self.assertEqual((final['holder'], final['second'], final['py']), (1, True, '1'))
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
        with self.assertRaises(DamagedStore) as caught:
            store.update(str(st), lambda doc: None)
        self.assertEqual(caught.exception.message[:24], 'pair.json is damaged: Ex')
        self.assertEqual((st / 'pair.json').read_text(), '{"a": ')
        self.assertEqual(sorted(p.name for p in st.iterdir() if 'pair' in p.name), ['pair.json'])

    def test_update_gives_up_on_a_lock_that_stays_held_with_the_bash_message_and_leaves_that_lock(self):
        st = self.fixture_store()
        (st / '.pair.lock').mkdir()
        sleeps = []
        with mock.patch.dict(os.environ, {'PAIR_LOCK_TRIES': '3'}), mock.patch.object(store.time, 'sleep', sleeps.append):
            with self.assertRaises(LockHeld) as cm:
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

    @staticmethod
    def pretty(doc):
        """The bytes pair.json holds for doc: two-space indent, characters as they are, a closing newline."""
        return json.dumps(doc, indent=2, ensure_ascii=False) + '\n'

    def test_update_writes_pair_json_with_a_two_space_indent_and_a_closing_newline(self):
        for name, text in self.JQ_FIXTURES.items():
            st = self.fixture_store(name.replace(' ', '-'))
            (st / 'pair.json').write_text(text)
            store.update(str(st), lambda doc: None)
            want = '{\n  "b": "x\\u007fy"\n}\n' if name == 'del' else self.pretty(json.loads(text))
            self.assertEqual((st / 'pair.json').read_text(), want, name)

    def test_update_documents_where_its_number_spelling_differs_from_jq(self):
        # jq 1.8 keeps the spelling of a number it does not compute on, in the canonical form 1E+3 (1e3 -> 1E+3, 1.5e-7 -> 1.5E-7,
        # 1.10 stays 1.10, 1E2 -> 1E+2); Python parses it as a float and prints its own. No writer puts these in pair.json (counts and
        # epochs are integers, and sample comes from toml as 0.0, 0.25 or 1), and no reader treats the text as anything but JSON.
        for text, py in (('1e3', '1000.0'), ('-0', '0'), ('1.5e-7', '1.5e-07'), ('1.10', '1.1'), ('1E2', '100.0'), ('0.000001', '1e-06')):
            st = self.fixture_store('num-' + text)
            (st / 'pair.json').write_text(f'{{"n":{text}}}')
            store.update(str(st), lambda d: None)
            self.assertEqual((st / 'pair.json').read_text(), f'{{\n  "n": {py}\n}}\n', text)

    def test_scratch_mutations_remove_and_add_one_id(self):
        docs = [('{"a":1}', {'a': 1, 'scratch': []}, {'a': 1, 'scratch': ['x']}),
                ('{"a":1,"scratch":null}', {'a': 1, 'scratch': []}, {'a': 1, 'scratch': ['x']}),
                ('{"a":1,"scratch":false}', {'a': 1, 'scratch': []}, {'a': 1, 'scratch': ['x']}),
                ('{"a":1,"scratch":[]}', {'a': 1, 'scratch': []}, {'a': 1, 'scratch': ['x']}),
                ('{"scratch":["x","001-a","x"],"a":1}', {'scratch': ['001-a'], 'a': 1}, {'scratch': ['001-a', 'x'], 'a': 1}),
                ('{"scratch":["z","b","001-a","a"]}', {'scratch': ['z', 'b', '001-a', 'a']}, {'scratch': ['001-a', 'a', 'b', 'x', 'z']})]
        mutations = {
            'remove': (lambda doc: doc.update(scratch=[x for x in store.listing(doc, 'scratch') if x != 'x']), 1),
            'add': (lambda doc: doc.update(scratch=sorted({*store.listing(doc, 'scratch'), 'x'})), 2),
        }
        for name, (mutate, column) in mutations.items():
            for n, row in enumerate(docs):
                st = self.fixture_store(f'{name}-{n}')
                (st / 'pair.json').write_text(row[0])
                store.update(str(st), mutate)
                self.assertEqual((st / 'pair.json').read_text(), self.pretty(row[column]), (name, row[0]))

    def covered(self, outputs):
        seen = set()
        for out in outputs:
            seen.update(line.split(':')[0] for line in out.splitlines() if not line.startswith(' '))
            seen.update(w for w in (' (stopped)', ' (paused)', 'running:') if w in out)
        return seen






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

    def test_metrics_names_a_log_that_is_a_directory_and_one_it_cannot_read_in_its_own_words(self):
        self.env['PAIR_METRICS_NOW'] = '200'
        as_dir = self.path / 'log-dir'
        (as_dir / 'events.tsv').mkdir(parents=True)
        r = self.pair_py('metrics', str(as_dir))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, f'pair.sh: warning: {as_dir}/events.tsv is a directory: skipped\n')
        locked = self.path / 'log-locked'
        locked.mkdir()
        (locked / 'events.tsv').write_text(self.FIRST_EVENTS)
        (locked / 'events.tsv').chmod(0)
        self.addCleanup((locked / 'events.tsv').chmod, 0o600)
        if os.access(locked / 'events.tsv', os.R_OK):
            self.skipTest('a reader that ignores file modes (root)')
        r = self.pair_py('metrics', str(locked))
        self.assertEqual((r.returncode, r.stdout), (2, ''))
        self.assertEqual(r.stderr, f'pair.sh: cannot read {locked}/events.tsv: Permission denied\n')

    def test_metrics_counts_the_first_event_of_a_headerless_store(self):
        out = self.metrics_of(self.FIRST_EVENTS)
        self.assertIn('sidekick: busy 1m (100% of wall), idle 0m across 0 gaps; 1 briefs (median 1m), 0 plan responses (0m)\n', out)
        self.assertIn('  001-a 1m\n', out)

    # A note whose id is not a number: the earlier bash never returned on it (`master note 001-alpha a:blocking`):
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

    def brief_rows(self, *spans):
        rows = []
        for n, (start, end) in enumerate(spans):
            rows += [f'2026\t{start}\tmaster\tsend-brief\t00{n}-a\t', f'2026\t{end}\tsidekick\treport\t00{n}-a\t']
        return '\n'.join(rows) + '\n'

    def test_metrics_takes_the_lower_middle_brief_duration_as_the_median(self):
        odd = self.metrics_of(self.brief_rows((0, 60), (100, 220), (300, 600)))
        self.assertEqual(odd, (
            'wall: 10m from first event to last\n'
            'sidekick: busy 8m (80% of wall), idle 2m across 2 gaps; 3 briefs (median 2m), 0 plan responses (0m)\n'
            'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'verdicts: none\n'
            'briefs:\n  000-a 1m\n  001-a 2m\n  002-a 5m\n'))
        even = self.metrics_of(self.brief_rows((0, 60), (100, 220), (300, 600), (700, 1300)))
        self.assertEqual(even, (
            'wall: 22m from first event to last\n'
            'sidekick: busy 18m (83% of wall), idle 4m across 3 gaps; 4 briefs (median 2m), 0 plan responses (0m)\n'
            'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'verdicts: none\n'
            'briefs:\n  000-a 1m\n  001-a 2m\n  002-a 5m\n  003-a 10m\n'))
        self.assertEqual(self.metrics_of('2026\t100\tmaster\tqueue\t001-a\t\n'), (
            'wall: 0m from first event to last\n'
            'sidekick: busy 0m (0% of wall), idle 0m across 0 gaps; 0 briefs (median 0m), 0 plan responses (0m)\n'
            'queue: 1 briefs queued, 0 taken by the sidekick; 0 started the moment a report landed\n'
            'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'verdicts: none\n'))

    def test_metrics_counts_a_row_with_an_unusable_time_but_keeps_it_off_the_timeline(self):
        rows = self.FIRST_EVENTS + '2026\tabc\tmaster\tqueue\t002-b\t\n2026\t\tmaster\tsend-brief\t003-c\t\n2026\t-5\tmaster\tqueue\t004-d\t\n'
        self.assertEqual(self.metrics_of(rows), (
            'wall: 1m from first event to last\n'
            'sidekick: busy 1m (100% of wall), idle 0m across 0 gaps; 1 briefs (median 1m), 0 plan responses (0m)\n'
            'queue: 2 briefs queued, 0 taken by the sidekick; 0 started the moment a report landed\n'
            'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'verdicts: none\n'
            'briefs:\n  001-a 1m\n'))

    def test_metrics_drops_a_row_with_too_few_columns(self):
        rows = self.FIRST_EVENTS + '2026\t300\tmaster\tqueue\n\nshort\n'
        self.assertEqual(self.metrics_of(rows), (
            'wall: 1m from first event to last\n'
            'sidekick: busy 1m (100% of wall), idle 0m across 0 gaps; 1 briefs (median 1m), 0 plan responses (0m)\n'
            'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'verdicts: none\n'
            'briefs:\n  001-a 1m\n'))

    def test_metrics_counts_a_verdict_word_only_in_ascii_letters(self):
        log = '2026\t100\tmaster\tlog\t-\t'
        block = ('wall: 0m from first event to last\n'
                 'sidekick: busy 0m (0% of wall), idle 0m across 0 gaps; 0 briefs (median 0m), 0 plan responses (0m)\n'
                 'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n')
        self.assertEqual(self.metrics_of(f'{log}review: ACCEPTED\n'), block + 'verdicts: accept 1\n')
        self.assertEqual(self.metrics_of(f'{log}review: ac\u212aept acce\u017fpted\n'), block + 'verdicts: none\n')

    def test_metrics_pairs_a_note_with_its_step_only_for_a_whole_number_id(self):
        rows = ['0\tmaster\tsend-brief\t001-a\t', '60\tsidekick\treport\t001-a\t', '10\tsidekick\tstep\t001-a\t1',
                '20\tsidekick\tstep\t001-a\t3', '30\tsidekick\tstep\t001-a\t4', '100\tmaster\tnote\t001-a\t3.0:x',
                '110\tmaster\tnote\t001-a\t 4:x', '130\tmaster\tnote\t001-a\t1:x']
        self.assertEqual(self.metrics_of(''.join(f'2026\t{row}\n' for row in rows)), (
            'wall: 2m from first event to last\n'
            'sidekick: busy 1m (46% of wall), idle 0m across 0 gaps; 1 briefs (median 1m), 0 plan responses (0m)\n'
            'master: 0 wakes, 0 of them check-ins; review latency median 0m over 0 reviews\n'
            'steps: 3 committed, 3 notes (0 blocking); step to note median 2m\n'
            'verdicts: none\n'
            'briefs:\n  001-a 1m\n'))

    def test_metrics_refuses_a_clock_that_is_not_a_whole_number(self):
        st = self.path / 'clock'
        st.mkdir()
        (st / 'events.tsv').write_text(self.FIRST_EVENTS)
        self.env['PAIR_METRICS_NOW'] = 'soon'
        r = self.pair_py('metrics', str(st))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, '', 'pair.sh: PAIR_METRICS_NOW must be a whole number\n'))

    def test_metrics_gives_a_legacy_header_store_the_same_output_as_the_headerless_one(self):
        self.assertEqual(self.metrics_of(LEGACY_HEADER + '\n' + self.FIRST_EVENTS), self.metrics_of(self.FIRST_EVENTS))

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

    def kitchen_core_module(self, name):
        sys.path.insert(0, str(root / 'skills/pstack-kitchen/scripts'))
        try:
            import variant as kitchen_variant
        finally:
            sys.path.pop(0)
        kitchen_variant.load_kitchen_core()
        return __import__(f'kitchen_core.{name}', fromlist=[name])

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

    # Per stack, per unit: `NNN:rc[+][/why]`, rc 0 when the stack covers the unit, `+` when the covering commit is a rebased one, why the reason a
    # unit is not covered (a refused merge, or a revert).
    UNIT_MODEL = {
        'unit': (
            '001:0 002:0 003:1 004:1/merge 005:0 006:1 007:0 008:0 009:1',
            '001:0+ 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1'),
        'plain': (
            '001:0+ 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1',
            '001:0+ 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1'),
        'reverted': (
            '001:1/reverted 002:1/reverted 003:1 004:1/merge 005:1 006:1 007:1/reverted 008:1 009:1',
            '001:1/reverted 002:1/reverted 003:1 004:1/merge 005:1 006:1 007:1/reverted 008:1 009:1'),
        'nearby': (
            '001:1/reverted 002:1/reverted 003:1 004:1/merge 005:1 006:1 007:1/reverted 008:1 009:1',
            '001:1/reverted 002:1/reverted 003:1 004:1/merge 005:1 006:1 007:1/reverted 008:1 009:1'),
        'squash': (
            '001:1 002:1 003:1 004:1/merge 005:1 006:1 007:1 008:1 009:1',
            '001:1 002:1 003:1 004:1/merge 005:1 006:1 007:1 008:1 009:1'),
        'partial': (
            '001:1 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1',
            '001:1 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1'),
        'reapplied': (
            '001:1/reverted 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1',
            '001:1/reverted 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1'),
        'moved': (
            '001:0+ 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1',
            '001:0+ 002:0+ 003:1 004:1/merge 005:1 006:1 007:0+ 008:1 009:1'),
        'deletion': (
            '001:1 002:1 003:0+ 004:1/merge 005:1 006:1 007:1 008:1 009:1',
            '001:1 002:1 003:0+ 004:1/merge 005:1 006:1 007:1 008:1 009:1'),
        'merges': (
            '001:1 002:1 003:1 004:1/merge 005:1 006:1 007:1 008:1 009:1',
            '001:1 002:1 003:1 004:1/merge 005:1 006:1 007:1 008:1 009:1'),
    }

    def test_the_unit_model_says_which_units_a_stack_covers_and_why_not(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uworld')
        names = list(recorded)
        self.assertEqual(sorted(stacks), sorted(self.UNIT_MODEL))
        token = lambda n, row: f'{n[:3]}:{row[0]}' + ('+' if row[1] else '') + (f'/{row[2]}' if row[2] else '')
        for label, (stack_base, head) in stacks.items():
            index = units.StackIndex.build(str(repo), stack_base, head)
            rows = []
            for n in names:
                unit = units.RecordedUnit(str(st), f'{st}/briefs/{n}.md')
                rows.append((n, self.inclusion_row(unit, units.inclusion(unit, index)), self.inclusion_row(unit, units.rebased_in(unit, index))))
            got = (' '.join(token(n, r) for n, r, _ in rows), ' '.join(token(n, r) for n, _, r in rows))
            self.assertEqual(got, self.UNIT_MODEL[label], label)
            # a stack base of the empty string reads no commit as the stack's: nothing is covered
            empty = units.StackIndex.build(str(repo), '', head)
            self.assertEqual({self.inclusion_row(units.RecordedUnit(str(st), f'{st}/briefs/{n}.md'), units.rebased_in(units.RecordedUnit(str(st), f'{st}/briefs/{n}.md'), empty))[0] for n in names}, {1}, label)

    def test_the_patch_ids_the_unit_model_reads_are_git_patch_ids_and_the_inverse_is_the_reverts(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('upatch')
        git = lambda *a, **kw: subprocess.run(['git', '-C', str(repo), *a], capture_output=True, text=True, check=True, **kw).stdout
        commits = git('rev-list', '--all').split()
        for c in commits:
            diff = git('diff-tree', '-p', '--binary', '--full-index', c)
            want = subprocess.run(['git', 'patch-id', '--stable'], input=diff, capture_output=True, text=True, check=True).stdout.split(' ')[0]
            self.assertEqual(units.patch_id(str(repo), c), want, c)
        self.assertTrue(any(units.patch_id(str(repo), c) for c in commits))
        # the reverting commit of the `reverted` stack has the patch-id, with no context, of the inverse of the commit it reverts
        base, head = stacks['reverted']
        picked_u1, _, revert = git('rev-list', '--reverse', f'{base}..{head}').split()
        self.assertEqual(units.patch_id(str(repo), picked_u1, inverse=True, u0=True), units.patch_id(str(repo), revert, u0=True))
        self.assertNotEqual(units.patch_id(str(repo), picked_u1), units.patch_id(str(repo), revert))

    def test_the_stack_index_lists_the_stacks_commits_oldest_first_and_gives_a_shared_patch_id_to_the_newer_commit(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uindex')
        base, head = stacks['reapplied']
        index = units.StackIndex.build(str(repo), base, head).load()
        listed = subprocess.run(['git', '-C', str(repo), 'rev-list', '--reverse', '--no-merges', f'{base}..{head}'], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(index.commits, listed)
        self.assertEqual(index.position, {c: n for n, c in enumerate(listed)})
        self.assertEqual(index.id_of, {c: units.patch_id(str(repo), c) for c in listed})
        self.assertEqual(index.id0_of, {c: units.patch_id(str(repo), c, u0=True) for c in listed})
        # u1 was picked twice: its patch-id belongs to the newer pick
        self.assertEqual(len(listed), 4)
        self.assertEqual(index.id_of[listed[0]], index.id_of[listed[-1]])
        self.assertEqual(index.by_id[index.id_of[listed[0]]], listed[-1])
        self.assertEqual(sorted(index.by_id.values()), sorted(listed[1:]))

    # Per stack, for each list of units below: the exit status and what is said (note lines for a rebased unit or one with no usable head, a
    # death for a head the checked-out head lacks, a revert, or a missing brief).
    HEADS_IN = {
        'unit': ['0 ok', '0 ok', '2 notin', '0 ok', '0 nohead', '2 notin', '0 nohead', '2 notin', '0 ok', '1 nobrief'],
        'plain': ['0 rebased', '0 rebased', '2 notin', '0 rebased', '0 nohead', '2 notin', '0 nohead', '2 rebased+notin', '0 ok', '1 nobrief'],
        'reverted': ['2 reverted', '2 reverted', '2 notin', '2 reverted', '0 nohead', '2 notin', '0 nohead', '2 reverted', '0 ok', '1 nobrief'],
        'nearby': ['2 reverted', '2 reverted', '2 notin', '2 reverted', '0 nohead', '2 notin', '0 nohead', '2 reverted', '0 ok', '1 nobrief'],
        'squash': ['2 notin', '2 notin', '2 notin', '2 notin', '0 nohead', '2 notin', '0 nohead', '2 notin', '0 ok', '1 nobrief'],
        'partial': ['0 rebased', '2 notin', '2 notin', '0 rebased', '0 nohead', '2 notin', '0 nohead', '2 rebased+notin', '0 ok', '1 nobrief'],
        'reapplied': ['0 rebased', '2 reverted', '2 notin', '0 rebased', '0 nohead', '2 notin', '0 nohead', '2 rebased+notin', '0 ok', '1 nobrief'],
        'moved': ['0 rebased', '0 rebased', '2 notin', '0 rebased', '0 nohead', '2 notin', '0 nohead', '2 rebased+notin', '0 ok', '1 nobrief'],
        'deletion': ['2 notin', '2 notin', '0 rebased', '2 notin', '0 nohead', '2 notin', '0 nohead', '2 notin', '0 ok', '1 nobrief'],
        'merges': ['2 notin', '2 notin', '2 notin', '2 notin', '0 nohead', '2 notin', '0 nohead', '2 notin', '0 ok', '1 nobrief'],
    }
    HEADS_IN_LISTS = [['002', '001'], ['001', '002'], ['003', '001'], ['007', '001'], ['006', '001'], ['004', '001'], ['009', '001'], ['002', '003', '001'], ['001'], ['010', '001']]

    def test_heads_in_names_the_units_a_verifications_head_does_not_carry(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uheads')
        phrases = (('(rebased as', 'rebased'), ('has no usable head', 'nohead'), ('is not in the checked-out head', 'notin'), ('reverts them in the checked-out head', 'reverted'),
                   ('no brief for unit', 'nobrief'))
        said = ''
        for label, (stack_base, head) in stacks.items():
            subprocess.run(['git', '-C', str(repo), 'checkout', '-q', label], check=True, env={**self.env, **self.GIT})
            got = []
            for names in self.HEADS_IN_LISTS:
                buf, status = io.StringIO(), 0
                with contextlib.redirect_stderr(buf):
                    try:
                        units.heads_in(str(st), head, names)
                    except (Die, PairError) as e:
                        buf.write(f'kitchen.sh: {e.message}\n')
                        status = e.code
                kinds = [name for line in buf.getvalue().splitlines() for phrase, name in phrases if phrase in line]
                got.append(f'{status} {"+".join(kinds) or "ok"}')
                said += buf.getvalue()
            self.assertEqual(got, self.HEADS_IN[label], label)
        self.assertIn("unit 003's head ", said)
        self.assertIn('name the unit that carries all the others last', said)

    def test_unit_head_reads_the_report_then_the_last_step_and_unit_brief_finds_the_brief(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks, recorded = self.units_world('uhead')
        # a steps file whose last row is blank reads as no head; a last row with no tab is the whole row
        (st / 'steps/011-blank.tsv').write_text(f'1\t{shas["u1"]}\tfirst\n\n')
        (st / 'steps/012-notab.tsv').write_text(f'1\tabc\tfirst\n{shas["u2"]}\n')
        (st / 'steps/013-tabs.tsv').write_text(f'1\t{shas["u1"]}\n2\t\tx\n')
        recorded = {**recorded, '011-blank': None, '012-notab': None, '013-tabs': None}
        heads = {n: units.unit_head(str(st), f'{st}/briefs/{n}.md') for n in [*recorded, '010-nobrief']}
        self.assertEqual(heads, {'001-a': shas['u2'], '002-b': shas['u1'], '003-del': shas['del'], '004-mrg': shas['merged'], '005-nobase': shas['u2'],
                                 '006-nohead': '', '007-steps': shas['u1'], '008-badbase': shas['u2'], '009-badreport': '', '011-blank': '',
                                 '012-notab': shas['u2'], '013-tabs': '', '010-nobrief': ''})
        found = {}
        for n in ('001', '002', '009', '010', '011'):
            try:
                found[n] = units.unit_brief(str(st), n).replace(str(st), '<S>')
            except NoBrief as e:
                found[n] = f'kitchen.sh: {e.message}'
        self.assertEqual(found, {'001': '<S>/briefs/001-a.md', '002': '<S>/briefs/002-b.md', '009': '<S>/briefs/009-badreport.md',
                                 '010': 'kitchen.sh: no brief for unit 010', '011': 'kitchen.sh: no brief for unit 011'})

    def test_verdict_parse_reads_the_range_status_units_and_problem_of_a_verdict(self):
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
        raw = []
        for p in [*paths.values(), st / 'verdicts/missing.md']:
            v = units.Verdict.parse(str(p))
            raw.append(f'{v.range_base} {v.range_head}|{v.status}|{v.units}|{v.problem}')
        # the range halves are as printed: a range with no dots is its own base and head, and words after the head are cut
        self.assertEqual(raw, ['a b|clean|landing|', 'pppp qqqq|clean|landing 001|', 'abc abc|reject|001|', ' |reject|001|', ' bbb|clean|landing|',
                               'aaa |clean|landing|', 'a c|clean|landing|', 'a b|clean|landing|',
                               'a b|maybe|001|status is not clean, reject, or inconclusive', 'a b||001|status is not clean, reject, or inconclusive',
                               'a b|clean|001|no command output under Evidence', 'a b|clean|001|no command output under Evidence', ' |clean|001|',
                               ' |||status is not clean, reject, or inconclusive'])
        self.assertTrue(units.Verdict.parse(str(paths['words after the head'])).landing)
        self.assertFalse(units.Verdict.parse(str(paths['no dots'])).landing)
        self.assertEqual(units.Verdict.parse(str(paths['empty base'])).refs(), ('bbb', ''))
        # the packet beside a verdict wins
        self.assertEqual(raw[1].split('|')[0], 'pppp qqqq')
        self.assertEqual(raw[0].split('|')[0], 'a b')

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

    def test_land_check_behaviour(self):
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
            'a heads that is a string': (1, 'pair.json is damaged: heads is string, not an object'),
            'a dispatch that is a string': (0, 'land-check: pass'),
            'a landing that is a string': (1, 'pair.json is damaged: landing is string, not an object'),
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
            'a skip with a heads that is a string': (1, 'pair.json is damaged: heads is string, not an object'),
            'a skip with no head': (2, gap),
            'a skip with no proof': (2, 'unreviewed: unit 001 cites no joo run'),
            'a kitchen that does not validate': (2, 'kitchen: validate fails (kitchen.py validate shows why)'),
            'no pair.json': (1, 'pair.sh: no pair.json in'),
            'no arguments': (1, 'pair.sh: usage: kitchen.sh land-check <store>'),
            'two arguments': (1, 'pair.sh: usage: kitchen.sh land-check <store>'),
        }
        out = self.kitchen_cases('landck', cases, expect)
        self.assertRegex(out['a unit rebased into the stack']['stdout'], r'covered: unit 001 \(rebased as [0-9a-f]{9}\) by the clean landing verdict <T>/st/verdicts/landing-v1\.md')
        self.assertIn('reverts them', out['a rebased unit whose patch was reverted later']['stdout'])
        self.assertNotIn('covered', out['a unit squashed into the stack']['stdout'])
        self.assertIn('landing verification: clean (<T>/st/verdicts/landing-v10.md)'.replace('clean', 'reject'), out['the newest landing verdict counts']['stdout'])
        self.assertIn('is no longer an ancestor of', out['a recorded landing parent that is no longer an ancestor']['stderr'])
        self.assertIn('pair.json is damaged: landing is string, not an object', out['a landing that is a string']['stderr'])
        self.assertIn(gap, out['a skip over a dangling dispatch head']['stdout'])
        self.assertIn('unit 001 cites no joo run', out['a skip with no proof']['stdout'])

    def test_a_landing_verdict_with_a_version_that_is_not_a_number_is_the_newest(self):
        """`landing-v1x.md` matches the glob and is taken as the newest, and so is every later one. `1_0` (which Python's int() would accept) is such a
        version too: with a clean `landing-v1_0.md` beside a rejected `landing-v5.md` the newest is v5."""
        for n, version in enumerate(('1x', '1_0', '1\u0661')):
            spec = self.land_world(files={f'verdicts/landing-v{version}.md': self.lc_verdict('{c1}..{c3}'),
                                          'verdicts/landing-v5.md': self.lc_verdict('{c1}..{c3}', status='reject')})
            got = self.kitchen_world(f'landv{n}', spec)
            self.assertEqual(got['rc'], 2, version)
            self.assertIn('landing verification: reject (<T>/st/verdicts/landing-v5.md)', got['stdout'])
            self.assertEqual(got['stderr'], '')

    def test_land_check_over_a_damaged_pair_json_says_so_and_exits_1(self):
        """A pair.json that is an array, or that does not parse, is the one damaged-store message and exit 1."""
        for n, (label, text) in enumerate((('an array', '[]\n'), ('not JSON', 'not json\n'))):
            for kind, extra in (('no verdict', {}), ('a landing verdict', {'verdicts/landing-v1.md': self.lc_verdict('{c1}..{c3}')})):
                spec = self.land_world(nopair=True, files={'pair.json': text, **extra})
                got = self.kitchen_world(f'landnp{n}{len(extra)}', spec)
                self.assertEqual(got['rc'], 1, (label, kind))
                self.assertIn('pair.json is damaged: ', got['stderr'])

    def test_latest_landing_verdict_is_the_highest_version_and_is_landing_reads_the_playbook_or_the_header(self):
        landing = self.kitchen_core_module('landing')
        st, repo, shas, stacks, recorded = self.units_world('ulatest')
        names = ['landing-v1.md', 'landing-v2.md', 'landing-v10.md', 'landing-v3-packet.md', 'landing-v1_0.md', 'landing-v09.md']
        sets = [[], names[:1], names[:3], names[:4], names[:3] + names[4:5], names[:3] + names[5:], names[5:] + names[:1]]
        # the files go in code-point order, and a name that is no number is the newest until a later file in that order replaces it:
        # v1, v10, v1_0, v2 ends at v2
        latest = ['', 'landing-v1.md', 'landing-v10.md', 'landing-v10.md', 'landing-v2.md', 'landing-v10.md', 'landing-v09.md']
        briefs = {'a': 'playbook: opening-a-pr\n', 'b': 'playbook: shipping\n', 'c': 'landing: yes\n', 'd': 'landing: no\nplaybook: feature\n', 'e': '', 'f': 'landing: yes \n'}
        for key, text in briefs.items():
            (st / f'briefs/9{key}.md').write_text('# B\n\n' + text)
        for n, files in enumerate(sets):
            for f in (st / 'verdicts').glob('landing-*'):
                f.unlink()
            for f in files:
                (st / 'verdicts' / f).write_text('# V\n')
            self.assertEqual(landing.latest_landing_verdict(str(st)), f'{st}/verdicts/{latest[n]}' if latest[n] else '', files)
        got = {k: landing.is_landing(str(st / f"briefs/9{k}.md")) for k in briefs}
        self.assertEqual(got, {'a': True, 'b': True, 'c': True, 'd': False, 'e': False, 'f': False})

    def test_manifest_warning_names_the_manifests_changed_since_the_verified_base(self):
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
        warning = 'warning: dependency manifests changed since the verified base: requirements.txt\n'
        # a warning when the manifest differs between the two commits, in either order; none for a code-only change, an empty side, or the same commit
        expected = ['', warning, warning, '', warning, '', '', '', '', warning]
        for (verified, current, head), want in zip(calls, expected):
            self.assertEqual(landing.manifest_warning(str(st), verified, current, head or ''), want, (verified[:9], current[:9], head))

    def test_dispatch_of_a_landing_brief_goes_through_the_python_land_check(self):
        """cmd_dispatch's bash called land_check; it calls the Python land-check now. A landing brief with a unit that has no clean verdict is refused
        with the same message by both trees."""
        landing_brief = '# Brief 003-ship\n\nplaybook: shipping\ntimebox: 30\ncommit: no\n\n## Scope\n\nmay write:\n- src/a.py\n\nmust not write:\n- lib/**\n'
        spec = self.land_world(args=['dispatch', '{st}', '{st}/briefs/003-ship.md', '--timeout', '1000'], files={'briefs/003-ship.md': landing_brief},
                               repo_files={'src/a.py': 'x'})
        out = self.kitchen_cases('landdisp', {'a landing brief with an unverified unit': spec},
                                {'a landing brief with an unverified unit': (2, 'landing refused: kitchen.sh land-check')})
        self.assertIn('landing refused', out['a landing brief with an unverified unit']['stderr'])

    def test_land_check_hands_back_its_problem_lines_and_says_each_line_it_finds(self):
        landing = self.kitchen_core_module('landing')
        said = []
        line = 'kitchen: validate fails (kitchen.py validate shows why)\n'
        with mock.patch.object(landing, 'validated_engine', return_value=(False, '')):
            self.assertEqual(landing.land_check('/st', said.append), [line])
            self.assertEqual(landing.land_check_problems('/st'), [line])
        self.assertEqual(said, [line])

    def test_revise_behaviour(self):
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
        out = self.kitchen_cases('revise', cases, expect)
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

    def kitchen_py(self, store, code, env=None, cwd=None, pid=None, prog='pair.sh'):
        """Python run in a fresh interpreter with kitchen_core.verify_state loaded as `vs` and a context `ctx`: (status, stdout, stderr). pid, when
        given, is the process id verify_state takes for the running command."""
        pre = (f'import sys\nsys.path.insert(0, {str(scripts)!r})\nsys.path.insert(0, {str(root / "skills/pstack-kitchen/scripts")!r})\n'
               'import variant\nvariant.load_kitchen_core()\nfrom kitchen_core import units, verify, verify_launch, verify_packet, verify_state as vs\nfrom pstack_core.store import Context\n'
               'from pstack_core.errors import Die, Exit, PairError\n'
               f'ctx = Context("pstack-kitchen", {str(root / "skills/pstack-kitchen")!r}, "/s", "Kitchen", ("sidekick",), ("plans",), 20, "", {prog!r})\n'
               f'store = {str(store)!r}\n' + (f'import os\nos.getpid = lambda: {pid}\n' if pid else ''))
        r = subprocess.run([sys.executable, '-I', '-c', pre + code], env={k: v for k, v in {**self.env, **self.GIT, 'HOSTNAME': 'testhost', **(env or {})}.items() if v is not None}, capture_output=True,
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

    def test_claim_close_takes_a_claim_only_from_a_holder_that_is_gone(self):
        live, dead = self.spawn_live_and_dead_pids()
        base = {'verdict': '/v/001-x-v1.md', 'units': '001'}
        # (verifying document, the status claim_close ends with: 0 when the claim is taken)
        cases = {
            'a fresh claim': (base, 0),
            'a claim held by a live process': ({**base, 'closing': {'token': f'testhost:{live}:7'}}, 1),
            'a claim whose owner is gone': ({**base, 'closing': {'token': f'testhost:{dead}:7'}}, 0),
            'a claim from another host': ({**base, 'closing': {'token': f'elsewhere:{dead}:7'}}, 1),
            'a claim held by a process of another user (EPERM; pid 1)': ({**base, 'closing': {'token': 'testhost:1:7'}}, 0),
            'a token with no colon': ({**base, 'closing': {'token': 'garbage'}}, 1),
            'a token with a pid that is no number': ({**base, 'closing': {'token': 'testhost:abc:7'}}, 0),
            'a token with a pid int() would take (1_2345)': ({**base, 'closing': {'token': 'testhost:1_2345:7'}}, 0),
            'a token with a pid in other digits': ({**base, 'closing': {'token': 'testhost:\u0661\u0662:7'}}, 0),
            'an empty token': ({**base, 'closing': {'token': ''}}, 0),
            'a token that is a number': ({**base, 'closing': {'token': 5}}, 1),
            'a token that is false': ({**base, 'closing': {'token': False}}, 0),
            'closing that is a string': ({**base, 'closing': 'x'}, 'closing is string, not an object'),
            'closing that is an array': ({**base, 'closing': [1]}, 'closing is array, not an object'),
            'a verification that is another attempt': ({'verdict': '/v/001-x-v2.md', 'units': '001'}, 1),
            'a verification that is a string': ('x', 'verifying is string, not an object'),
            'a verification that is an array': ([1], 'verifying is array, not an object'),
            'no verification': (None, 1),
        }
        for n, (name, (verifying, status)) in enumerate(cases.items()):
            st = self.claim_store(f'claim{n}', verifying, {} if verifying is not None else {'verifying': None})
            before = (st / 'pair.json').read_text()
            rc, out, err = self.kitchen_py(st, 'print("rc=%d" % (0 if vs.claim_close(store, "/v/001-x-v1.md") else 1))', pid=os.getpid())
            if isinstance(status, str):
                self.assertEqual((rc, out), (1, ''), (name, err))
                self.assertIn(f'pair.json is damaged: {status}', err, name)
                self.assertEqual((st / 'pair.json').read_text(), before, name)
                continue
            self.assertEqual((rc, out), (0, f'rc={status}\n'), (name, err))
            after = json.loads((st / 'pair.json').read_text())
            if status == 0:
                self.assertRegex(after['verifying']['closing']['token'], rf'^testhost:{os.getpid()}:[0-9]+$', name)
            else:
                self.assertEqual((st / 'pair.json').read_text(), before, name)
            self.assertEqual(err, '', name)

    def test_update_verifying_drop_verifying_and_release_close_touch_only_the_verification_they_name(self):
        other_attempt = {'verdict': '/v/001-x-v2.md', 'units': '001', 'idle_since': 5}
        mine = {'verdict': '/v/001-x-v1.md', 'units': '001', 'idle_since': 5, 'closing': {'token': 'testhost:4242:9'}}
        write = 'vs.update_verifying(store, "/v/001-x-v1.md", lambda v: v.__setitem__("idle_since", 99))'
        # (document, call, verifying after)
        cases = {
            'a stale-attempt write after the next attempt started': (other_attempt, write, other_attempt),
            'a write on the verification it holds': (mine, write, {**mine, 'idle_since': 99}),
            'a drop on the verification it holds': (mine, 'vs.drop_verifying(store, "/v/001-x-v1.md")', None),
            'a stale drop': (other_attempt, 'vs.drop_verifying(store, "/v/001-x-v1.md")', other_attempt),
            'a release with its own token': (mine, 'vs.release_close(store, "/v/001-x-v1.md", "testhost:4242:9")', {k: v for k, v in mine.items() if k != 'closing'}),
            'a release with another token': (mine, 'vs.release_close(store, "/v/001-x-v1.md", "testhost:1:1")', mine),
            'a release on a stale verification': (other_attempt, 'vs.release_close(store, "/v/001-x-v1.md", "testhost:4242:9")', other_attempt),
        }
        for n, (name, (document, call, after)) in enumerate(cases.items()):
            st = self.claim_store(f'upd{n}', document)
            rc, out, err = self.kitchen_py(st, call + '\nprint("rc=0")')
            self.assertEqual((rc, out), (0, 'rc=0\n'), (name, err))
            self.assertEqual(json.loads((st / 'pair.json').read_text()).get('verifying'), after, name)
        st = self.claim_store('updclosing', {**mine, 'closing': 'x'})
        before = (st / 'pair.json').read_text()
        rc, out, err = self.kitchen_py(st, 'vs.release_close(store, "/v/001-x-v1.md", "testhost:4242:9")\nprint("rc=0")')
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('pair.json is damaged: closing is string, not an object', err)
        self.assertEqual((st / 'pair.json').read_text(), before)

    def test_my_claim_and_still_my_claim_say_whether_this_shell_holds_the_close(self):
        """A claim is this shell's when its token names this host and this shell's pid; still_my_claim ends the command with exit 5 otherwise."""
        pid = 4242
        cases = {'a claim of this process': (f'testhost:{pid}:9', ['mine', 'rc=0']), 'a claim of another process': (f'testhost:{pid + 1}:9', ['notmine', 'rc=5']),
                 'a claim of another host': (f'other:{pid}:9', ['notmine', 'rc=5']),
                 'a claim of this host with a longer pid': (f'testhost:{pid}0:9', ['notmine', 'rc=5']),
                 'no claim': ('', ['notmine', 'rc=5'])}
        for n, (name, (token, want)) in enumerate(cases.items()):
            st = self.claim_store(f'my{n}', {'verdict': 'v', **({'closing': {'token': token}} if token else {})})
            code = ('print("mine" if vs.my_claim(store) else "notmine")\ntry:\n    vs.still_my_claim(store)\n    print("rc=0")\n'
                    'except (Exit, PairError) as e:\n    print("rc=%d" % e.code)')
            prc, got, perr = self.kitchen_py(st, code, pid=pid)
            self.assertEqual(got.splitlines(), want, (name, perr))

    def test_a_wait_whose_verification_another_call_closed_says_so_to_its_sink_and_ends_with_5(self):
        verify_wait = self.kitchen_core_module('verify_wait')
        ctx = Context('pstack-kitchen', '/x', '/s', 'Kitchen', (), (), 21, '', 'kitchen.sh')
        said = []
        waiter = verify_wait.Waiter(ctx, '/st', 0, out=said.append)
        with mock.patch.object(verify_wait.Waiter, 'wait', side_effect=VerificationTaken()):
            self.assertEqual(waiter.run(), verify_wait.WaiterOutcome(5, closed_elsewhere=True))
        self.assertEqual(said, ['verification closed elsewhere\n'])

    def test_audit_due_follows_the_crc32_of_the_unit_and_the_sample_rate(self):
        vs = self.kitchen_core_module('verify_state')
        # a unit is audited when its stable draw (crc32 mod 1000) is under the sample rate times 1000
        samples = (0, 0.1, 0.5, 1, 2)
        drawn = {'001-x': 'nnnYY', '002-y': 'nnYYY', '003-z': 'nnnYY', 'landing': 'nnYYY', '010-fix': 'nYYYY'}
        for unit, row in drawn.items():
            self.assertEqual(''.join('Y' if vs.audit_due(unit, sample) else 'n' for sample in samples), row, unit)
            self.assertEqual(zlib.crc32(unit.encode()) % 1000 < 500, row[2] == 'Y', unit)

    def test_a_failed_cleanup_releases_the_claim_and_run_close_ends_with_the_status_of_the_scratch_removal(self):
        """close_verifier's last command is the scratch removal: an id the scratch command refuses ends the close with its status, and the claim,
        which this process took, is given back before the command ends."""
        verifying = {'verdict': '/v/001-x-v1.md', 'units': '001', 'scratch': 'bad id', 'brief': '001-x', 'packet': '/nonexistent/p.md'}
        extra = {'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}, 'git_root': str(self.path)}
        st = self.claim_store('rcpy', verifying, extra)
        rc, out, err = self.kitchen_py(st, 'try:\n    vs.run_close(ctx, store, "v1", "claude", "p3", "/v/001-x-v1.md")\nexcept (Exit, PairError) as e:\n    print("rc=%d" % e.code)\n',
                                       pid=os.getpid(), prog='kitchen.sh')
        self.assertEqual(out, 'rc=1\n')
        self.assertEqual(self.mask_token(err).replace(str(st), '<S>'), 'kitchen.sh: scratch id must look like NNN-<slug>, e.g. NNN-<slug>-c<k> or NNN-<slug>-review\n')
        self.assertNotIn('closing', (st / 'pair.json').read_text())

    def test_a_failing_scratch_removal_shows_its_error_in_the_failure_text_of_closing_an_unread_verdict(self):
        """close_unread's one sink holds the lines the wait said before the removal failed, then the removal's own message, exactly."""
        verdict = self.path / 'unread-v1.md'
        verdict.write_text('status: clean\nunits: 001\n')
        st = self.claim_store('unread', {'verdict': str(verdict), 'units': '001', 'scratch': '001-x-verify'}, {'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}})
        code = ('from unittest import mock\nfrom pstack_core.errors import MissingStore\n'
                'with mock.patch.object(vs, "close_verifier", side_effect=MissingStore("/w")):\n'
                '    try:\n        verify.close_unread(ctx, store)\n    except (Exit, PairError) as e:\n        print("rc=%d" % e.code)\n')
        rc, out, err = self.kitchen_py(st, code, env={'KITCHEN_VERDICT_SETTLE_S': '0'}, prog='kitchen.sh')
        self.assertEqual((out, err), ('rc=1\n', f'closing the unread verdict {verdict} failed:\nkitchen.sh: no pair.json in /w; run: pair.sh init <slug>\n'))

    def test_a_bug_in_the_scratch_removal_propagates_and_the_close_claim_is_released(self):
        verdict = '/v/001-x-v1.md'
        st = self.claim_store('buggy', {'verdict': verdict, 'units': '001', 'scratch': '001-x-verify'}, {'verifier': {'name': 'v1', 'pane_id': 'p3', 'kind': 'claude'}})
        code = ('from unittest import mock\n'
                'with mock.patch.object(vs, "agent_prompt"), mock.patch.object(vs, "agent_status", return_value="absent"), mock.patch.object(vs, "herdr"), \\\n'
                '        mock.patch.object(vs, "remove_scratch", side_effect=TypeError("bug")):\n'
                '    try:\n        vs.run_close(ctx, store, "v1", "claude", "p3", "/v/001-x-v1.md")\n    except TypeError as e:\n        print("raised", e)\n')
        rc, out, err = self.kitchen_py(st, code, pid=os.getpid(), prog='kitchen.sh')
        self.assertEqual((rc, out), (0, 'raised bug\n'), err)
        self.assertNotIn('closing', json.loads((st / 'pair.json').read_text())['verifying'])

    def test_a_claim_made_with_hostname_unset_names_the_machines_host(self):
        st = self.claim_store('nohostname', {'verdict': '/v/001-x-v1.md', 'units': '001'})
        self.assertEqual(self.kitchen_py(st, 'print(vs.claim_close(store, "/v/001-x-v1.md"))', env={'HOSTNAME': None}, pid=os.getpid())[1], 'True\n')
        token = json.loads((st / 'pair.json').read_text())['verifying']['closing']['token']
        self.assertEqual(token.split(':')[0], socket.gethostname())

    def test_a_claim_taken_by_a_closer_binds_the_next_closer_while_its_process_lives_and_no_longer_after(self):
        """A closer's token is read by the next closer as a live holder (while its process lives) and as a dead one (after); a closer releases a token
        only if it is its own."""
        live, dead = self.spawn_live_and_dead_pids()
        verdict = '/v/001-x-v1.md'
        st = self.claim_store('compat', {'verdict': verdict, 'units': '001'})
        closing = lambda: json.loads((st / 'pair.json').read_text())['verifying'].get('closing')
        claim = lambda pid: self.kitchen_py(st, f'print(vs.claim_close(store, {verdict!r}))', pid=pid)[1]
        self.assertEqual(claim(live), 'True\n')
        token = closing()['token']
        self.assertRegex(token, rf'^testhost:{live}:[0-9]+$')
        # the holder lives: another closer is refused and the token stays
        self.assertEqual(claim(os.getpid()), 'False\n')
        self.assertEqual(closing()['token'], token)
        # another closer's release does not take it; the holder's own does
        self.kitchen_py(st, f'vs.release_close(store, {verdict!r}, "testhost:1:1")')
        self.assertEqual(closing()['token'], token)
        self.kitchen_py(st, f'vs.release_close(store, {verdict!r}, {token!r})')
        self.assertIsNone(closing())
        # a closer whose process has gone: the next takes over, with a token of its own
        self.assertEqual(claim(dead), 'True\n')
        gone = closing()['token']
        self.assertEqual(claim(live), 'True\n')
        self.assertNotEqual(closing()['token'], gone)
        self.assertRegex(closing()['token'], rf'^testhost:{live}:')

    VERIFY_ENV = {'KITCHEN_VERIFY_POLL_S': '0', 'KITCHEN_VERIFY_GRACE_POLL_S': '0', 'KITCHEN_VERDICT_SETTLE_S': '0', 'KITCHEN_VERIFIER_EXIT_S': '0'}

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
        """The host a claim token names (HOSTNAME from the environment, else localhost), a pid that is alive for the whole test and one that is gone."""
        host = os.environ.get('HOSTNAME') or socket.gethostname()
        dead = subprocess.Popen(['true'])
        dead.wait()
        return host, os.getpid(), dead.pid

    def test_verify_cancel_behaviour(self):
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
            'a verifying that is a string': (1, 'pair.json is damaged: verifying is string, not an object'),
            'a verifier that is a string': (1, 'pair.json is damaged: verifier is string, not an object'),
            'a verifier that is false': (1, 'pair.json is damaged: verifier is boolean, not an object'),
            'a master that is a string': (0, 'cancelled: 001'),
            '--cancel with --wait': (1, '--cancel takes no units, --landing or --wait'),
            '--cancel with a unit': (1, '--cancel takes no units, --landing or --wait'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.kitchen_cases('vcancel', cases, expect)
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
        return [(r'grace [0-9]+s left', 'grace Ns left'), (r'\b[0-9]+m of 32m', 'Nm of 32m')]

    def test_the_verify_wait_hides_the_minute_count_it_prints(self):
        """`verifying: units 001 by claude, Nm of 32m` counts whole minutes since the verification started, and two runs can be a minute apart. The
        second run here starts one minute earlier (started and deadline both moved back 60 s, as a tick does), so the two raw outputs differ and the
        masked ones must not."""
        now = int(time.time())

        def spec(shift):
            world = self.wait_world('0', agents={'v1': 'working claude'}, verifying={'started': now - 150 - shift, 'deadline': now + 1800 - shift})
            return {**world, 'repo_files': {'.agents/kitchen.toml': world.get('toml') or self.KITCHEN_TOML}}
        first = self.script_run('vwait20-a', root / 'skills', 'pstack-kitchen', spec(0))
        second = self.script_run('vwait20-b', root / 'skills', 'pstack-kitchen', spec(60))
        self.assertIn('2m of 32m', first['stdout'])
        self.assertIn('3m of 32m', second['stdout'])
        masked = lambda text: functools.reduce(lambda t, rule: re.sub(rule[0], rule[1], t), self.wait_mask(), text)
        self.assertEqual(masked(second['stdout']), masked(first['stdout']))

    def test_verify_wait_behaviour_for_verdicts(self):
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
        out = self.kitchen_cases('vwait', {k: {**v, 'mask': self.wait_mask()} for k, v in cases.items()}, expect)
        clean = out['a clean verdict']
        self.assertNotIn('"verifying"', clean['files']['pair.json'])
        self.assertIn('verifier-outcome: clean', clean['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verify\t001-x\tclean', '\n'.join(clean['events']))
        self.assertIn('wake\t001-x\tverify:clean', '\n'.join(clean['events']))
        self.assertIn('"units_since_handoff": 1', clean['files']['pair.json'])
        self.assertIn('verifier-outcome: invalid', out['a verdict with no evidence']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('"verifying"', out['a verdict while another live call holds the claim']['files']['pair.json'])
        self.assertNotIn('"closing"', out['a verdict whose scratch the scratch command refuses']['files']['pair.json'])

    def test_pair_sh_refuses_a_timeout_that_is_no_whole_number_and_takes_a_fraction_for_a_poll(self):
        """Through the real command: `wait --timeout -5`, `--timeout 1.5`, `--timeout x` and `--every 1+1` end with status 1 naming the flag, before
        anything is claimed; PAIR_BUSY_POLL=0.2 is accepted and PAIR_BUSY_POLL=abc is a usage error."""
        hook = '#!/bin/sh\nprintf "# R\\n\\nstatus: partial\\n" >"$FAKE/../st/reports/001-x.md"\n'
        top, st, run = self.own_world('wnum', files={}, once={'wait': hook})
        for flag, word, unit in (('--timeout', '-5', 'milliseconds'), ('--timeout', '1.5', 'milliseconds'), ('--timeout', 'x', 'milliseconds'), ('--every', '1+1', 'minutes')):
            r = run('wait', '{st}', flag, word)
            self.assertEqual(r.returncode, 1, (flag, word, r.stderr))
            self.assertIn(f'{flag} must be a whole number of {unit}', r.stderr)
            self.assertFalse((st / 'wait.pid').exists(), (flag, word))
        r = run('wait', '{st}', '--timeout', '1000', extra_env={'PAIR_BUSY_POLL': 'abc'})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('PAIR_BUSY_POLL must be a number', r.stderr)
        r = run('wait', '{st}', '--timeout', '5000', extra_env={'PAIR_BUSY_POLL': '0.2'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'report: {st}/reports/001-x.md', r.stdout)

    def test_the_timeout_and_interval_of_a_wait_are_whole_numbers_and_a_float_knob_takes_a_fraction(self):
        """--timeout (milliseconds) and --every (minutes) take digits only: a sign, a fraction or an expression is a usage error with status 1 naming
        the flag. PAIR_BUSY_POLL takes a fraction, and a word there is a usage error too."""
        def timing(args):
            opts = argv.read(args, wait.TIMING)
            timeout = wait.timeout_ms(opts)
            return timeout, numbers.tdiv(timeout, 60000), opts.present('--send-only'), list(opts.leftovers)

        self.assertEqual(timing(['--timeout', '90000', '--send-only', 'x']), (90000, 1, True, ['x']))
        self.assertEqual(timing(['--every', '2']), (120000, 2, False, []))
        self.assertEqual(timing(['--timeout', '0']), (0, 0, False, []))
        for flag, unit in (('--timeout', 'milliseconds'), ('--every', 'minutes')):
            for word in ('-5', 'x', '1.5', '1+1', '', '0x10'):
                with self.assertRaises(BadNumber) as caught:
                    timing([flag, word])
                self.assertEqual((caught.exception.code, caught.exception.message), (1, f'{flag} must be a whole number of {unit}'), (flag, word))

    def test_verify_wait_behaviour_for_the_wait_itself(self):
        world, V = self.wait_world, self.vverdict
        flip = ('python3 -c \'import json, sys; p = sys.argv[1]; d = json.load(open(p)); d["verifying"]["verdict"] = "/elsewhere/other-v1.md"; json.dump(d, open(p, "w"))\' '
                '"$FAKE/../st/pair.json"\n')
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
            'a verifying argv that is a string': world(verifying={'argv': 'x'}),
            'a verifying entry whose fallback is a string': world(verifying={'entry': {'fallback': 'x'}}),
            'an interval that is no whole number': world('x', agents={'v1': 'working claude'}),
            'outside herdr': world(env={'HERDR_ENV': '0'}),
        }
        expect = {
            'a verifier still working': (4, 'verifying: units 001 by claude, Nm of 32m, verifier working'),
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
            'a verifying that is a string': (1, 'pair.json is damaged: verifying is string, not an object'),
            'a verifier that is a string': (1, 'pair.json is damaged: verifier is string, not an object'),
            'a verifying argv that is a string': (1, 'pair.json is damaged: argv is string, not an array'),
            'a verifying entry whose fallback is a string': (1, 'pair.json is damaged: verifying.entry.fallback is string, not an object'),
            'an interval that is no whole number': (1, '--every must be a whole number of minutes'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        host = self.live_and_dead_token_hosts()[0]
        out = self.kitchen_cases('vwait2', {k: {**v, 'mask': self.wait_mask()} for k, v in cases.items()}, {k: (rc, text.replace('{st}', '<T>/st')) for k, (rc, text) in expect.items()})
        self.assertIn('prompt v1 Deadline reached: write', out['a verifier past its deadline is prompted for what it proved']['calls'])
        self.assertIn('"grace_at":', out['a verifier past its deadline is prompted for what it proved']['files']['pair.json'])
        self.assertEqual(out['a grace already started is resumed without a prompt']['calls'].count('Deadline reached'), 0)
        self.assertIn('"grace_delivery": "skipped:devin-working"', out['a devin verifier at its deadline is only waited on']['files']['pair.json'])
        self.assertIn('verifier-outcome: provider', out['a provider error on the screen']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verifier-outcome: stalled', out['a verifier that sits idle past the limit']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('verifier-outcome: missing', out['a verifier gone and no verdict']['files']['verdicts/001-x-v1-packet.md'])
        self.assertIn('transcript: ', out['a provider error in the session log']['stdout'])
        self.assertIn('packet: never reached the verifier', out['a provider error in the session log']['stdout'])
        self.assertEqual(out['a verification closed elsewhere while waiting']['calls'].count('pane close'), 0)
        self.assertIn('agent prompt v1 ', out['a packet never delivered to an idle verifier']['calls'])

    def test_set_outcome_verify_prompt_and_handoff_due_say_what_a_verifier_and_a_master_read(self):
        st = self.claim_store('twin2', {'verdict': 'v'})
        packets = {'one outcome line': ('# P\n\nverifier-outcome: prepared\n\nverdict: x\n', '# P\n\nverifier-outcome: running (a|b)\n\nverdict: x\n'),
                   'two outcome lines': ('verifier-outcome: a\nbody\nverifier-outcome: b\n', 'verifier-outcome: running (a|b)\nbody\nverifier-outcome: running (a|b)\n'),
                   'none': ('# P\n\nverdict: x\n', '# P\n\nverdict: x\n'),
                   'indented': ('  verifier-outcome: no\nverifier-outcome:\n', '  verifier-outcome: no\nverifier-outcome: running (a|b)\n'),
                   'no trailing newline': ('x\nverifier-outcome: y', 'x\nverifier-outcome: running (a|b)'), 'empty': ('', '')}
        for n, (name, (text, want)) in enumerate(packets.items()):
            packet = st / f'py-{n}.md'
            packet.write_text(text)
            self.kitchen_py(st, f'vs.set_outcome({str(packet)!r}, "running (a|b)")')
            self.assertEqual(packet.read_text(), want, name)
        self.kitchen_py(st, 'vs.set_outcome(store + "/none.md", "x")')
        self.assertFalse((st / 'none.md').exists())
        got = self.kitchen_py(st, 'print(vs.verify_prompt(ctx, "/s/verdicts/001-x-v1-packet.md"), end="")')[1]
        self.assertIn('take the verifier role. pstack-kitchen VERIFY /s/verdicts/001-x-v1-packet.md', got)
        # a verified unit counts toward the master's handoff, a landing verification does not; the master hears of it every ROTATE_UNITS units (5 unless
        # the variable is a positive number), every unit when it is 0, and a count that is not a whole number ends the command
        note = lambda count, store: f'master: {count} units verified since your last handoff; rotate at this boundary: kitchen.sh new-handoff {store}, fill it, kitchen.sh rotate-master {store}\n'
        counts = {'no master': (None, 1), 'zero': ({'units_since_handoff': 0}, 1), 'four': ({'units_since_handoff': 4}, 5), 'nine': ({'units_since_handoff': 9}, 10),
                  'ten': ({'units_since_handoff': 10}, 11), 'null': ({'units_since_handoff': None}, 1), 'false': ({'units_since_handoff': False}, 1),
                  'an empty master': ({}, 1)}
        every = {'': 5, '2': 2, '0': 1}
        n = 0
        for name, (master, count) in counts.items():
            for setting, step in (*every.items(), (None, 5)):
                env = {} if setting is None else {'KITCHEN_MASTER_ROTATE_UNITS': setting}
                n += 1
                store = self.claim_store(f'ho{n}', {'verdict': 'v'}, {'master': master})
                rc, out, err = self.kitchen_py(store, 'vs.handoff_due(store, "001-x")\nprint("rc=0")', env=env)
                said = note(count, store) if (step == 1 or count % step == 0) else ''
                self.assertEqual((rc, out, err), (0, said + 'rc=0\n', ''), (name, setting))
                self.assertEqual(json.loads((store / 'pair.json').read_text())['master'], {'units_since_handoff': count}, (name, setting))
            store = self.claim_store(f'hol{n}', {'verdict': 'v'}, {'master': master})
            before = (store / 'pair.json').read_text()
            self.assertEqual(self.kitchen_py(store, 'vs.handoff_due(store, "landing")\nprint("rc=0")')[:2], (0, 'rc=0\n'), name)
            self.assertEqual((store / 'pair.json').read_text(), before, name)
        for name, master, word in (('a master that is a string', 'x', 'pair.json is damaged: master is string, not an object'),
                                  ('a count that is a string', {'units_since_handoff': 'a'}, 'pair.json is damaged: units_since_handoff is string, not an integer'),
                                   ('a count that is not whole', {'units_since_handoff': 1.5}, 'pair.json is damaged: units_since_handoff is number, not an integer')):
            store = self.claim_store(f'hoe{name[:8]}{len(name)}', {'verdict': 'v'}, {'master': master})
            before = (store / 'pair.json').read_text()
            rc, out, err = self.kitchen_py(store, 'vs.handoff_due(store, "001-x")\nprint("rc=0")')
            self.assertEqual((rc, out), (1, ''), name)
            self.assertIn(word, err, name)

    def test_a_bad_rotate_units_setting_leaves_pair_json_unchanged(self):
        """KITCHEN_MASTER_ROTATE_UNITS is read before the verified-units count is bumped: a word there ends the command with status 1 naming the
        variable and pair.json stays as it was."""
        store = self.claim_store('horot', {'verdict': 'v'}, {'master': {'units_since_handoff': 3}})
        before = (store / 'pair.json').read_text()
        rc, out, err = self.kitchen_py(store, 'vs.handoff_due(store, "001-x")\nprint("rc=0")', env={'KITCHEN_MASTER_ROTATE_UNITS': 'x'})
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('KITCHEN_MASTER_ROTATE_UNITS must be a whole number', err)
        self.assertEqual((store / 'pair.json').read_text(), before)

    def test_every_skill_script_compiles_under_python_3_11(self):
        """The floor is Python 3.11 (AGENTS.md): a 3.12-only construct, such as a quote reused inside an f-string's braces, fails to compile there."""
        python = shutil.which('python3.11') or shutil.which('python3.11', path=str(Path.home() / '.local/bin'))
        if python is None:
            self.skipTest('no python3.11 on PATH or in ~/.local/bin')
        files = sorted(str(p) for p in (root / 'skills').rglob('*.py'))
        code = 'import ast, sys\nfor f in sys.argv[1:]:\n    ast.parse(open(f, encoding="utf-8").read(), f)\n'
        r = subprocess.run([python, '-c', code, *files], capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ''))

    def test_a_close_or_a_wait_that_fails_inside_releases_its_claim_and_leaves_the_verification_open(self):
        """A damaged `KITCHEN_VERIFIER_EXIT_S`, a top-level `.scratch` that is a number (a traceback from the scratch command), a `.verifying.idle_since`
        or `.verifying.started` that is no whole number: the command ends with status 1 naming the word, the claim of the close is released,
        and the verification stays open."""
        world, V = self.wait_world, self.vverdict
        cases = {
            'a verifier exit wait of abc': (world(files={'verdicts/001-x-v1.md': V()}, env={'KITCHEN_VERIFIER_EXIT_S': 'abc'}), 'KITCHEN_VERIFIER_EXIT_S must be a whole number'),
            'a verifier exit wait of 1.5': (world(files={'verdicts/001-x-v1.md': V()}, env={'KITCHEN_VERIFIER_EXIT_S': '1.5'}), 'KITCHEN_VERIFIER_EXIT_S must be a whole number'),
            'a top-level scratch that is a number, at a close': (world(files={'verdicts/001-x-v1.md': V()}, pair={'scratch': 5}), 'pair.json is damaged: scratch is number, not an array'),
            'an idle_since of abc': (world(verifying={'idle_since': 'abc'}), 'verifying.idle_since is string, not a whole number'),
            'an idle_since of 1.5': (world(verifying={'idle_since': 1.5}), 'verifying.idle_since is number, not a whole number'),
            'an idle_since of true': (world(verifying={'idle_since': True}), 'verifying.idle_since is boolean, not a whole number'),
            'a started of abc': (world('0', agents={'v1': 'working claude'}, verifying={'started': 'abc'}), 'verifying.started is string, not a whole number'),
            'a started of 1.5': (world('0', agents={'v1': 'working claude'}, verifying={'started': 1.5}), 'verifying.started is number, not a whole number'),
            'a started of true': (world('0', agents={'v1': 'working claude'}, verifying={'started': True}), 'verifying.started is boolean, not a whole number'),
        }
        cancel = {
            'a cancel with a verifier exit wait of abc': (self.verify_world(env={'KITCHEN_VERIFIER_EXIT_S': 'abc'}), 'KITCHEN_VERIFIER_EXIT_S must be a whole number'),
            'a cancel with a top-level scratch that is a number': (self.verify_world(pair={'scratch': 5}), 'pair.json is damaged: scratch is number, not an array'),
        }
        for n, (name, (spec, word)) in enumerate({**cases, **cancel}.items()):
            got = self.kitchen_world(f'vfail{n}', spec)
            self.assertEqual(got['rc'], 1, (name, got['stderr']))
            self.assertIn(word, got['stderr'], name)
            if 'exit wait' in name or 'scratch' in name:
                self.assertNotIn('"closing"', got['files']['pair.json'], name)
                self.assertIn('"verifying"', got['files']['pair.json'], name)
            if 'idle_since' in name:
                self.assertNotIn('pane close', got['calls'], name)

    def test_a_verify_that_finds_the_open_verifications_close_held_by_a_live_call_waits_then_gives_up(self):
        """A new verify first closes the open verification whose verdict is written; when another live call holds the claim it says it is waiting for
        it and, after KITCHEN_VERIFY_CLOSE_WAIT_S seconds (120 by default), dies with status 5 naming the verdict and the command that reads it."""
        host, live, dead = self.live_and_dead_token_hosts()
        spec = self.wait_world('0', files={'verdicts/001-x-v1.md': self.vverdict()}, verifying={'closing': {'token': f'{host}:{live}:1'}},
                               env={'KITCHEN_VERIFY_CLOSE_WAIT_S': '0'})
        spec['args'] = ['verify', '{st}', '001', '--every', '0']
        got = self.kitchen_world('heldclose', spec)
        self.assertEqual(got['rc'], 5, got['stderr'])
        self.assertIn('closing <T>/st/verdicts/001-x-v1.md: another call is closing it; waiting for it', got['stdout'])
        self.assertIn('another call has held the close of <T>/st/verdicts/001-x-v1.md for 0s; kitchen.sh verify <T>/st --wait', got['stderr'].replace(str(self.path / 'heldclose0'), '<T>').replace('heldclose', '<T>'))
        self.assertIn('"verifying"', got['files']['pair.json'])

    def test_the_verify_helpers_name_the_verifier_fence_the_evidence_find_the_paths_a_command_names_and_build_the_verifier_command(self):
        """verifier_identity (each argument quoted so a shell would read it back), fence_for, names_path, next_index and verifier_args."""
        st = self.claim_store('vhelp', {'verdict': 'v'})
        words = ['--model', 'opus', 'a b', 'it\'s', '', 'tab\there', 'new\nline', '\u00e9', '~x', '#x', 'x#', 'a=b', 'a,b', '{x}', '*', '!x', '--mcp-config', 'f.json',
                 '--permission-mode', 'auto', '-a', 'never', '--approve', '--sandbox=read-only', '$HOME', '"q"', '\\', 'a;b', 'a&b', '<x>', '%s', '\x7f', '\x1b[0m']
        lists = [[], words[:2], words[2:12], words[12:22], words[22:], ['--model', 'x', '--permission-mode', 'y', '--approve', '--yolo']]
        want = ['claude (model unspecified)', 'claude --model opus', "claude a\\ b it\\'s '' $'tab\\there' $'new\\nline' \u00e9 \\~x \\#x x# a=b",
                'claude a\\,b \\{x\\} \\* \\!x', 'claude \\$HOME \\"q\\" \\\\ a\\;b a\\&b \\<x\\> %s $\'\\177\' $\'\\E[0m\'', 'claude --model x --yolo']
        code = ''.join(f'print(verify_launch.verifier_identity("claude", {args!r}))\n' for args in lists)
        self.assertEqual(self.kitchen_py(st, code)[1].splitlines(), want)
        # the connection flags (the model aside) belong to the kitchen, not to the identity
        self.assertEqual(self.kitchen_py(st, 'print(verify_launch.verifier_identity("pi", ["--model", "m", "--approve", "-a", "never"]))')[1], 'pi --model m\n')
        texts = ['', 'plain', 'with ``` fence', 'a ````` longer run', '`one`', '``two``', 'a\nb```c\n````']
        got = self.kitchen_py(st, ''.join(f'print(verify_packet.fence_for({t!r}))\n' for t in texts))[1].splitlines()
        self.assertEqual(got, ['```', '```', '````', '``````', '```', '```', '`````'])
        paths = ['/work/repo', '/tmp/x y', '/a.b', '/tmp/store']
        commands = {'cd /work/repo && ls': 'ynnn', 'cat /work/repo/x': 'ynnn', 'cat /work/repo2/x': 'nnnn', 'cat "/work/repo"': 'ynnn', "cat '/work/repo'": 'ynnn',
                    'x/work/repo': 'nnnn', 'cat ~/work/repo': 'nnnn', 'cat $HOME/w': 'nnnn', 'cat ${HOME}/w': 'nnnn', 'cat "$HOME/w"': 'nnnn', 'cat /tmp/x y/z': 'nynn',
                    'echo /a.b;': 'nnyn', 'echo /aXb': 'nnnn', 'echo /work/repo|wc': 'ynnn', 'echo /work/repo&': 'ynnn', 'echo=/work/repo': 'ynnn', 'a:/work/repo': 'ynnn',
                    '(/work/repo)': 'ynnn', 'multi\nline /work/repo\nend': 'ynnn', '/tmp/store': 'nnny', 'a /tmp/store/briefs/x.md': 'nnny'}
        code = ''.join(f'print("y" if verify_packet.names_path({c!r}, {p!r}) else "n", end="")\n' for c in commands for p in paths)
        out = self.kitchen_py(st, code, env={'HOME': '/work'})[1]
        self.assertEqual({c: out[n * 4:n * 4 + 4] for n, c in enumerate(commands)}, commands)
        directory = self.path / 'vidx'
        directory.mkdir()
        for name in ('x-v1.md', 'x-v1-packet.md', 'x-v7.md', 'x-v10.md', 'x-vx.md', 'x-v02.md', 'y-v3.md', 'landing-v4.md', 'landing-v12-packet.md'):
            (directory / name).write_text('')
        got = self.kitchen_py(st, ''.join(f'print(verify.next_index({str(directory)!r}, {prefix!r}, "v"))\n' for prefix in ('x', 'y', 'z', 'landing', 'x-v1')))[1]
        self.assertEqual(got.splitlines(), ['11', '4', '1', '13', '1'])
        entries = ['', '{"kind":"claude","args":["--model","m"]}', '{"kind":"pi","args":["--approve"]}', '{"kind":"codex","args":["-a","never"]}',
                   '{"kind":"claude","args":["--permission-mode","plan","--strict-mcp-config"]}']
        nomcp = '--strict-mcp-config --mcp-config ' + str(root / 'skills/pstack-pair/scripts/../references/no-mcp.json')
        expected = {
            'claude': [f'--permission-mode auto {nomcp}', f'--permission-mode auto {nomcp} --model m', f'--permission-mode auto {nomcp} --approve', f'{nomcp} -a never',
                       '--permission-mode plan --strict-mcp-config'],
            'pi': ['--approve', '--approve --model m', '--approve', '--approve -a never', '--approve --permission-mode plan --strict-mcp-config'],
            'codex': ['-a on-request -s workspace-write --disable plugins', '-a on-request -s workspace-write --disable plugins --model m',
                      '-a on-request -s workspace-write --disable plugins --approve', '--disable plugins -a never', '--permission-mode plan --strict-mcp-config'],
            'devin': ['--permission-mode dangerous --respect-workspace-trust false', '--permission-mode dangerous --respect-workspace-trust false --model m',
                      '--permission-mode dangerous --approve', '--respect-workspace-trust false -a never',
                      '--respect-workspace-trust false --permission-mode plan --strict-mcp-config'],
            'muse': ['--approval-mode never --disable-sandbox --trust-workspace', '--approval-mode never --disable-sandbox --trust-workspace --model m',
                     '--approval-mode never --disable-sandbox --approve', '--trust-workspace -a never', '--trust-workspace --permission-mode plan --strict-mcp-config'],
            'prime-agent': ['', '--model m', '--approve', '-a never', '--permission-mode plan --strict-mcp-config'],
        }
        for kind, rows in expected.items():
            code = ''.join(f'print(" ".join(verify_launch.verifier_args({kind!r}, {json.loads(e) if e else None!r}, "/tmp")))\n' for e in entries)
            got = self.kitchen_py(st, code, pid=os.getpid(), cwd='/tmp')[1].splitlines()
            self.assertEqual(got, rows, kind)

    def test_verify_kpy_calls_leave_the_environment_alone(self):
        """verify reaches kitchen.py's library functions in process. The queries it makes (roster, snapshot, classify, timing, brief-verify --list, lesson-packet)
        write nothing to os.environ."""
        kpy = self.kitchen_core_module('kpy')
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
            kp = kpy.kitchen_py
            inv = kp.Invocation(str(repo))
            calls = [('roster', lambda: kp.roster_data()), ('snapshot', lambda: kp.snapshot(inv, top / 'kitchens')),
                     ('classify', lambda: kp.classify_change(kp.Invocation(str(repo), None, 'HEAD'), base='HEAD', head='HEAD')),
                     ('deadline', lambda: kp.deadline(inv, ['app'], False)), ('timing', lambda: kp.timing(inv, ['app'], 'landing')),
                     ('brief-verify --list', lambda: kp.brief_verify_list(inv, brief)),
                     ('lesson-packet', lambda: kp.lesson_packet([brief, top / 'brief2.md']))]
            statuses = []
            for name, query in calls:
                query()
                statuses.append(name)
                self.assertEqual(dict(os.environ), snapshot, name)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(len(statuses), len(calls), statuses)

    def test_record_skill_build_stamps_a_later_skills_directory_on_a_stamped_run_only(self):
        """A later skills directory is stamped on a stamped run before any command: kitchen_core.setup.record_skill_build on pair.json files of every
        shape gives the document, the stderr and the status below."""
        stamp = lambda path='/skills', at='<TS>': {'path': path, 'at': at}
        documents = {
            'no builds': ({'master': {'name': 'm'}}, 0, 'ABSENT', ''),
            'an empty list': ({'skill_builds': []}, 0, [stamp()], ''),
            'the same path last': ({'skill_builds': [stamp(at='t')]}, 0, [stamp(at='t')], ''),
            'another path last': ({'skill_builds': [stamp('/old', 't')]}, 0, [stamp('/old', 't'), stamp()], ''),
            'two rows': ({'skill_builds': [stamp(at='t'), stamp('/old', 't')]}, 0, [stamp(at='t'), stamp('/old', 't'), stamp()], ''),
            'a string': ({'skill_builds': 'x'}, 1, 'x', 'pair.json is damaged: skill_builds is string, not an array'),
            'an object': ({'skill_builds': {}}, 1, {}, 'pair.json is damaged: skill_builds is object, not an array'),
            'a number': ({'skill_builds': 3}, 1, 3, 'pair.json is damaged: skill_builds is number, not an array'),
            'null': ({'skill_builds': None}, 0, None, ''),
            'false': ({'skill_builds': False}, 0, False, ''),
            'rows that are strings': ({'skill_builds': ['a']}, 1, ['a'], 'pair.json is damaged: skill_builds holds string, not an object'),
        }
        for n, (name, (doc, status, builds, said)) in enumerate(documents.items()):
            st = self.path / f'rsb{n}'
            st.mkdir()
            (st / 'pair.json').write_text(json.dumps(doc, indent=2) + '\n')
            code = ('import sys\nfrom kitchen_core import setup\nfrom pstack_core.errors import Die, PairError\ntry:\n    setup.record_skill_build(Ctx, store)\n    code = 0\n'
                    'except (Die, PairError) as e:\n    sys.stderr.write(e.message)\n    code = e.code\nprint("rc=%d" % code)')
            rc, out, err = self.kitchen_py(st, 'class Ctx:\n    skill_root = "/skills/pstack-kitchen"\n' + code)
            self.assertEqual((rc, out), (0, f'rc={status}\n'), name)
            self.assertIn(said, err, name)
            self.assertEqual(bool(said), bool(err), name)
            written = json.loads(re.sub(r'"at": "[0-9TZ:-]+"', '"at": "<TS>"', (st / 'pair.json').read_text()))
            self.assertEqual(written.get('skill_builds', 'ABSENT'), builds, name)

    def record_skill_build_cases(self):
        stamped = {'skill_builds': [{'path': '/elsewhere', 'at': '2020-01-01T00:00:00Z'}]}
        return {
            'a stamped run from another build': dict(args=['status', '{st}'], pair=stamped),
            'a run that was never stamped': dict(args=['status', '{st}']),
            'a stamped run, the command being help': dict(args=['help', '{st}'], pair=stamped),
            'a stamped run, the command being unknown': dict(args=['nonsense', '{st}'], pair=stamped),
            'a pair.json that does not parse': dict(args=['status', '{st}'], nopair=True, files={'pair.json': '{{\n'}),
            'a skill_builds that is a string': dict(args=['status', '{st}'], pair={'skill_builds': 'x'}),
            'a skill_builds row that is a string': dict(args=['status', '{st}'], pair={'skill_builds': ['x']}),
        }

    def test_every_kitchen_command_records_the_skills_build(self):
        cases = self.record_skill_build_cases()
        expect = {
            'a stamped run from another build': (0, '# Kitchen status'),
            'a run that was never stamped': (0, '# Kitchen status'),
            'a stamped run, the command being help': (0, 'usage: kitchen.sh'),
            'a stamped run, the command being unknown': (1, 'nonsense'),
            'a pair.json that does not parse': (1, 'pair.json is damaged: '),
            'a skill_builds that is a string': (1, 'pair.json is damaged: skill_builds is string, not an array'),
            'a skill_builds row that is a string': (1, 'pair.json is damaged: skill_builds holds string, not an object'),
        }
        out = self.kitchen_cases('rsb', cases, expect)
        self.assertRegex(out['a stamped run from another build']['files']['pair.json'], r'"path": "<SK>"')
        self.assertNotIn('<SK>', out['a run that was never stamped']['files']['pair.json'])
        for case in ('a stamped run, the command being help', 'a stamped run, the command being unknown'):
            self.assertRegex(out[case]['files']['pair.json'], r'"path": "<SK>"', case)

    def test_brief_kitchen_reads_the_kitchen_a_brief_names(self):
        st = self.claim_store('bk', {'verdict': 'v'})
        for n, (text, want) in enumerate((('', '[] 0\n'), ('kitchen: repo', '[] 0\n'), ('kitchen: /tmp/snap.toml', '[--config /tmp/snap.toml] 2\n'),
                                          ('kitchen:   /tmp/spaced.toml  ', '[--config /tmp/spaced.toml  ] 2\n'), ('risk: routine', '[] 0\n'))):
            brief = self.path / f'kb{n}.md'
            brief.write_text('# B\n\n' + text + '\n')
            got = self.kitchen_py(st, f'from kitchen_core import classify\ncfg = classify.brief_kitchen({str(brief)!r})\nprint("[%s] %d" % (" ".join(cfg), len(cfg)))')[1]
            self.assertEqual(got, want, text)

    def test_kitchen_snapshot_is_empty_for_the_repos_own_kitchen_and_dies_for_one_that_does_not_validate(self):
        st = self.claim_store('ksnap', {'verdict': 'v'})
        repo = self.path / 'ksnap-repo'
        (repo / '.agents').mkdir(parents=True)
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True, capture_output=True, env={**self.env, **self.GIT})
        (st / 'pair.json').write_text(json.dumps({'git_root': str(repo)}) + '\n')
        code = ('import sys\nfrom pstack_core.errors import Die, PairError\nfrom kitchen_core import classify\ntry:\n    print("[%s]" % classify.kitchen_snapshot(store))\n    code = 0\n'
                'except (Die, PairError) as e:\n    sys.stderr.write("pair.sh: %s\\n" % e.message)\n    code = e.code\nprint("rc=%d" % code)')
        (repo / '.agents/kitchen.toml').write_text(self.LAUNCH_TOML)
        prc, got, perr = self.kitchen_py(st, code, cwd=repo)
        self.assertEqual((got, perr), ('[]\nrc=0\n', ''))
        (repo / '.agents/kitchen.toml').write_text('version = 1\n[profile.app]\npaths = 3\n')
        prc, got, perr = self.kitchen_py(st, code, cwd=repo)
        self.assertEqual((got, perr.replace(str(repo), '<R>')),
                         ('rc=1\n', 'kitchen: profile.app.paths: expected a list of non-empty strings\npair.sh: kitchen.py snapshot failed; run kitchen.py validate in the repo\n'))

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
            '--every with an expression': dict(with_args(base, '--every', '1+1'), mask=self.SETUP_MASK),
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

    def test_discuss_behaviour(self):
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
            'an escalated plan with a consultant that is a string': (1, 'pair.json is damaged: consultant is string, not an object'),
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
            '--every with an expression': (1, '--every must be a whole number of minutes'),
            '--timeout with no value': (1, '--timeout needs a value'),
            '--every with no value': (1, '--every needs a value'),
            '--every with a name': (1, '--every must be a whole number of minutes'),
            '--timeout that is no number': (1, '--timeout must be a whole number of milliseconds'),
            '--timeout that is no number with nobody answering': (1, '--timeout must be a whole number of milliseconds'),
            '--timeout with an unknown option after': (1, 'unknown option --bogus'),
            'an unknown option': (1, 'unknown option --bogus'),
            'outside herdr': (1, 'not running inside Herdr (HERDR_ENV != 1)'),
            'a pair.json that is an array': (1, 'pair.json is damaged: expected an object'),
            'no pair.json': (5, 'no pair.json in <T>/st; run: pair.sh init <slug>'),
            'a sidekick that is a string': (1, 'pair.json is damaged: sidekick is string, not an object'),
            'a rotation that succeeds before a routine plan': (0, 'rotated: the pi sidekick starts this plan in a fresh session\nstate: idle\nreport: <T>/st/reports/001-x.md\n'),
            'a rotation flag that is the string true': (4, 'report: missing'),
            'a rotation that is not due': (4, 'state: idle\nreport: missing\n'),
            'a rotation whose exit prompt fails': (2, 'rotating the sidekick before this plan failed:\npair.sh: pi exit failed'),
            'a rotation that does not exit in time': (5, 'rotating the sidekick before this plan failed:\npair.sh: pi has not exited; inspect s1 before retrying rotate'),
            'a rotation whose spawn returns 4': (4, 'rotating the sidekick before this plan failed:\nreused pane p1\n'),
            'a rotation in a paused store': (5, 'rotating the sidekick before this plan failed:\npair.sh: the store is paused (lunch)'),
            'a rotation before an escalated plan': (0, 'rotated: the pi sidekick starts this plan in a fresh session\nsent PLAN 001-x.md to sidekick s1\n'),
            'a rotation that leaves the plan unanswered': (4, 'rotated: the pi sidekick starts this plan in a fresh session\n'),
            'a sidekick that is a string with a consultant due': (1, 'pair.json is damaged: sidekick is string, not an object'),
            'a prime-agent rotation': (4, 'rotated: the prime-agent sidekick starts this plan in a fresh session\n'),
        }
        self.kitchen_cases('disc', cases, expect)

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

    def test_queue_behaviour(self):
        cases = self.queue_cases()
        queued = "queued <T>/st/briefs/002-y.md; it waits for the master's dispatch, which rotates a due sidekick first\n"
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
            'too few arguments': (1, 'usage: kitchen.sh queue <store> <brief-path> [--replace] | kitchen.sh queue <store> --clear'),
            'no pair.json': (1, 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>\n'),
            'a sidekick that is a string': (1, 'pair.json is damaged: sidekick is string, not an object'),
            'an escalated brief and no pair.json': (1, 'no pair.json in <T>/st; run: pair.sh init <slug>'),
        }
        out = self.kitchen_cases('que', cases, expect)
        # the classify the queue runs is quiet on stdout and stamps the brief: the bytes are compared by the A/B, and the stamp is there
        self.assertIn('profiles: app', out['a brief queued behind a working sidekick']['files']['briefs/002-y.md'])
        self.assertNotIn('profiles:', out['a landing brief']['files']['briefs/002-y.md'])

    SETUP_HOOK = {'hook': READY_HOOK, 'env': {'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300', 'PAIR_PREFLIGHT_RETRY_S': '0'}}
    SETUP_MASK = [(r'<SK>[^\s"\\]*(pair|kitchen)\.sh: line [0-9]+: \[: ', 'pair.sh: [: '), (r'setup-[0-9]{8}T[0-9]{6}-[0-9]+\.log', 'setup-<LOG>.log'), (r'repo-[0-9a-f]{8}', 'repo-H')]

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
            'a fallback argument that starts with --': run('--kind', 'codex', '--fallback', 'pi', '--fallback-arg', '--kind', '--fallback-arg', 'x'),
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

    def test_init_behaviour(self):
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
            'a pair.json that is an array': (1, 'pair.json is damaged: expected an object'),
            'a main checkout with a setup': (0, 'store: <T>/st (re-registered master demo-master in p0)'),
            'a linked worktree with no setup': (0, 'store: <T>/new (master demo-master in p0)'),
            'a linked worktree with a setup': (0, 'store: <T>/new (master demo-master in p0)'),
            'a linked worktree whose setup fails': (2, 'pair.sh: this run is in a linked worktree and its [scratch].setup failed:'),
            'no slug': (1, 'pair.sh: usage: kitchen.sh init <slug> [--store DIR] [--pane ID]'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
            'an unknown option': (1, 'pair.sh: unknown option --force'),
            '--pane with no value': (1, 'pair.sh: --pane needs a value'),
            '--store with no value': (1, 'pair.sh: --store needs a value'),
            'a stale --pane': (2, 'pair.sh: pane gone2 is not a live Herdr pane; HERDR_PANE_ID is stale when a harness runs commands through a sh'),
            'a slug that is not one': (1, "pair.sh: slug must match [a-z][a-z0-9_-]{0,20} (so <slug>-consultant fits Herdr's 32-char name limit)"),
        }
        out = self.kitchen_cases('init', cases, expect)
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

    def test_spawn_behaviour(self):
        cases = self.spawn_command4_cases()
        expect = {
            'the roster default kind': (0, 'sidekick s1 (claude) started in p0'),
            'the roster default kind and a trailing native arg': (0, 'started in'),
            '--kind over the roster': (0, 'started in'),
            '--kind equal to the roster kind takes its fallback': (0, 'started in'),
            '--kind with an explicit fallback': (0, 'sidekick s1 (codex) started in p0'),
            '--kind with native arguments': (0, 'started in'),
            'a fallback argument that starts with --': (0, 'started in'),
            'the roster kind with a fallback of its own named': (0, 'started in'),
            'a roster with no fallback': (0, 'started in'),
            '--tab': (0, 'started in'),
            'an option for the core': (1, 'pair.sh: unknown option --bogus'),
            '--kind with no value': (1, 'pair.sh: --kind needs a value'),
            '--fallback with no value': (1, 'pair.sh: --fallback needs a value'),
            'an empty roster and no --kind': (1, 'pair.sh: no --kind and no [sidekick] kind in the host roster (~/.config/pstack/kitchen.toml)'),
            'an empty roster and a --kind': (0, 'started in'),
            'a roster that does not parse': (1, "kitchen: <T>/home/.config/pstack/kitchen.toml: Expected ']' at the end of a table declaration"),
            'a roster that does not parse and a --kind': (1, "kitchen: <T>/home/.config/pstack/kitchen.toml: Expected ']' at the end of a table declaration"),
            'no store argument': (1, 'pair.sh: usage: kitchen.sh spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [-- agent-arg'),
            'a store with no pair.json': (1, 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
            'a pi preflight that fails with the roster fallback': (0, 'pi failed preflight: pi 1.0.2 at'),
            'a bootstrap that returns 4': (4, 'sidekick settled but reports/000-ready.md is missing; state=idle. Read the pane before prompting again.'),
        }
        out = self.kitchen_cases('spawn', cases, expect)
        self.assertIn('"kind": "devin"', out['--kind equal to the roster kind takes its fallback']['files']['pair.json'])
        recorded = json.loads(out['a fallback argument that starts with --']['files']['pair.json'])['sidekick']['fallback']
        self.assertEqual((recorded['kind'], recorded['args']), ('pi', ['--kind', 'x']))
        self.assertIn('"kind": "codex"', out['--kind over the roster']['files']['pair.json'])
        self.assertIn('"kind": "devin"', out['a pi preflight that fails with the roster fallback']['files']['pair.json'])

    def test_consultant_behaviour(self):
        cases = self.consultant_command_cases()
        expect = {
            'a start that succeeds': (0, 'consultant c1 (codex) started in p1'),
            'a start anchored on the sidekick pane': (0, 'started in'),
            'a start whose anchor pane is gone': (0, 'started in'),
            'a master that is a string': (1, 'pair.json is damaged: master is string, not an object'),
            'a start that returns 4': (4, 'consultant settled but advice/000-ready.md is missing; state=idle. Read the pane before prompting again.'),
            'a start that returns 3': (3, 'consultant bootstrap did not settle: {"error":{"code":"prompt_failed"}}'),
            'a start that dies with 5': (5, 'pair.sh: devin task sessions start fresh; recover partial work from the store instead of passing --resume'),
            'a permission': (0, 'started in'),
            'native arguments after --': (0, 'started in'),
            'the roster kind and its native args': (0, 'consultant c1 (pi) started in p1'),
            'the roster kind failing its preflight takes the fallback': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a failover over earlier failovers': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a failover over failovers that are no array': (1, 'pair.json is damaged: failovers is string, not an array'),
            'the roster kind failing its preflight with no fallback': (2, 'pair.sh: consultant pi failed its preflight and the roster names no other fallback: pi 1.0.2 at'),
            'a fallback that is the kind itself': (2, 'pair.sh: consultant pi failed its preflight and the roster names no other fallback: pi 1.0.2 at'),
            'a failing preflight that says nothing': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a failing preflight with a long message': (0, 'failover: consultant pi -> codex (preflight: pi 1.0.2 at'),
            'a roster kind that returns 4 after its preflight': (4, 'consultant settled but advice/000-ready.md is missing; state=idle. Read the pane before prompting again.'),
            'a live consultant skips the preflight': (0, 'consultant c1 already live (idle)\n'),
            'a consultant that is a string': (1, 'pair.json is damaged: consultant is string, not an object'),
            'escalations already recorded': (0, 'started in'),
            'escalations that are no array': (1, 'pair.json is damaged: escalations is string, not an array'),
            'an empty roster and no --kind': (1, 'pair.sh: no --kind and no [consultant] kind in the host roster'),
            'a roster that does not parse': (1, "kitchen: <T>/home/.config/pstack/kitchen.toml: Expected ']' at the end of a table declaration"),
            'no reason': (1, 'pair.sh: --reason is required: the escalation that warrants the consultant'),
            '--reason with no value': (1, 'pair.sh: --reason needs a value'),
            '--kind with no value': (1, 'pair.sh: --kind needs a value'),
            'an unknown option': (1, 'pair.sh: unknown option --bogus'),
            'no store argument': (1, 'pair.sh: usage: kitchen.sh consultant <store> --reason TEXT [--kind KIND] [--permission MODE|none] [-- agent-a'),
            'a store with no pair.json': (1, 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>'),
            'outside herdr': (1, 'pair.sh: not running inside Herdr (HERDR_ENV != 1)'),
        }
        out = self.kitchen_cases('cons', cases, expect)
        self.assertIn('a contract', out['a start that succeeds']['files']['pair.json'])
        self.assertIn('master\tescalate', '\n'.join(out['a start that succeeds']['events']))
        self.assertNotIn('a contract', out['a start that returns 4']['files']['pair.json'])
        self.assertIn('"failovers"', out['the roster kind failing its preflight takes the fallback']['files']['pair.json'])
        self.assertIn('consultant:pi:codex', '\n'.join(out['the roster kind failing its preflight takes the fallback']['events']))
        self.assertNotIn('failover', ''.join(out['a live consultant skips the preflight']['stdout']))

    def test_fleet_behaviour(self):
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
        out = self.kitchen_cases('fleet', cases, expect)
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
            'a report that is already written': dispatch(path, '--timeout', '0', files={'reports/002-y.md': '# R\n\nstatus: partial\nhead: {u2}\n'}, mtimes={'reports/002-y.md': 4_000_000_000}),
            'a sidekick that is due a rotation': dispatch(path, '--timeout', '0', agents={'m1': 'idle claude', 's1': 'idle pi'}, panes={'p0': 'm1', 'p1': ''}, hook=self.READY_HOOK,
                                                          pair={'sidekick': {'name': 's1', 'kind': 'pi', 'generation': 1, 'pane_id': 'p1', 'rotation_required': True, 'start_args': ['--model', 'm']}},
                                                          env={**env, 'PAIR_SUBMIT_CHECK_S': '0', 'PAIR_FILE_POLL_MS': '100', 'PAIR_BOOTSTRAP_WAIT_MS': '300'}),
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

    def test_a_kitchen_command_over_a_store_with_no_pair_json_says_so_once(self):
        plan = lambda risk: {'plans/001-x.md': f'# Plan 001-x\n\nrisk: {risk}\n'}
        cases = {
            'dispatch': (['dispatch', '{st}', '{st}/briefs/002-y.md', '--timeout', '100'], {}, 2),
            'queue': (['queue', '{st}', '{st}/briefs/002-y.md'], {}, 1),
            'discuss an escalated plan': (['discuss', '{st}', '{st}/plans/001-x.md'], plan('escalated'), 5),
            'discuss a routine plan': (['discuss', '{st}', '{st}/plans/001-x.md'], plan('routine'), 2),
            'verify': (['verify', '{st}', '001'], {}, 1),
            'step': (['step', '{st}', 'abc', 'summary'], {}, 1),
            'land-check': (['land-check', '{st}'], {}, 1),
            'status': (['status', '{st}'], {}, 2),
            'new-handoff': (['new-handoff', '{st}'], {}, 1),
        }
        for n, (name, (args, files, rc)) in enumerate(cases.items()):
            got = self.kitchen_world(f'nopair{n}', self.launch_world(args=args, nopair=True, files=files))
            self.assertEqual(got['rc'], rc, name)
            if name == 'dispatch':
                self.assertEqual((got['stdout'], got['stderr']), ('', 'pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>\n'))
            self.assertEqual(got['stderr'].count('pair.sh: no pair.json in <T>/st; run: pair.sh init <slug>\n'), 1, f'{name}: {got["stderr"]}')

    def test_dispatch_behaviour(self):
        cases = self.dispatch_cases()
        quiet = (4, 'quiet: Nm with nothing flagged; the sidekick works on')
        expect = {
            'a brief and no pair.json': (2, 'no pair.json in'),
            'a quiet check-in, then the quiet wait': quiet,
            'a quiet check-in, then --max ends it': quiet,
            'a quiet check-in and --max with no value': (1, '--max needs a value'),
            'a stale log is not swallowed': (4, 'STALE'),
            'a brief given relative to the store': quiet,
            'send only': (0, 'sent: BRIEF'),
            'a report that is already written': (4, 'the reply to 002-y.md was already shown'),
            'a sidekick that is due a rotation': (4, 'rotated: the pi sidekick starts this brief in a fresh session'),
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
        out = self.kitchen_cases('disp', cases, expect)
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
            'a slice that is no whole number': step('{k1}', 'did a thing', env={'KITCHEN_STEP_SLICE_S': '1 ? 50 : 1'}),
            'a step already recorded': step('{k1}', 'again', files={'steps/002-y.tsv': '1\t{k1}\t5\t\tone\n'}),
            'a step whose gates are still running': step('{k1}', 'did a thing', env={'KITCHEN_STEP_SLICE_S': '0'}, skip=['steps/jobs/']),
            'a sha that is not HEAD': step('{u2}', 'x'),
            'a tracked file edited since': step('{k1}', 'x', gitops=[*self.LAUNCH_OPS, ('write', 'src/app.py', 'y = 2\n')]),
            'a sha that is not a commit': step('f' * 40, 'x'),
            'no brief dispatched': step('{k1}', 'x', pair={'dispatch': {}}),
            'no base for the unit': step('{k1}', 'x', pair={'heads': {}}),
            'a heads that is a string': step('{k1}', 'x', pair={'heads': 'x'}),
            'a step on the unit base, so an empty range': step('{k1}', 'x', pair={'heads': {'002-y': '{k1}'}}),
            'a step that resolves a note': step('{k1}', 'fix', '--resolves', 'n1', files={'notes/002-y-n1.md': '# N\n\nstatus: blocking\n'}),
            'a brief stamped with a kitchen snapshot': step('{k1}', 'x', topfiles={'snap.toml': (self.LAUNCH_TOML, 0o644)},
                                                          files={'briefs/002-y.md': self.launch_brief('002', 'y', 'kitchen: {T}/snap.toml\n')}),
            'a base behind where the branch leaves trunk': step('{k1}', 'x', pair={'heads': {'002-y': '{u0}'}}),
            'two arguments': step('{k1}'),
            'no pair.json': step('{k1}', 'x', nopair=True),
            'a pair.json that is an array': step('{k1}', 'x', nopair=True, files={'pair.json': '[]\n'}),
        }

    def test_step_behaviour(self):
        cases = self.step_cases()
        recorded = (0, 'step 1 recorded at')
        expect = {
            'a first step': (0, 'gates: pass (app,contracts,docs)'),
            'a step whose gate fails': (2, 'next: fix it in a new commit and run kitchen.sh step <T>/st <new-sha> "did a thing" again'),
            'a step whose gate fails twice in a row': (2, 'next: fix it in a new commit'),
            'a step no profile covers': (0, 'gates: none ran; no profile covers notes.txt'),
            'a gate that passed after a rerun under load': (0, 'flaky under load: app failed, then passed on a rerun'),
            'a slice that is no whole number': (1, 'KITCHEN_STEP_SLICE_S must be a whole number'),
            'a step already recorded': (0, 'already recorded'),
            'a step whose gates are still running': (4, 'gates: still running for'),
            'a sha that is not HEAD': (1, 'HEAD is'),
            'a tracked file edited since': (1, 'the tree has uncommitted changes to tracked files'),
            'a sha that is not a commit': (1, 'not a commit: ffffffffffffffffffffffffffffffffffffffff'),
            'no brief dispatched': (1, 'no brief dispatched; a step belongs to a running brief'),
            'no base for the unit': (1, 'no base for 002-y; was it dispatched?'),
            'a heads that is a string': (1, 'pair.json is damaged: heads is string, not an object'),
            'a step on the unit base, so an empty range': (0, 'gates: pass (contracts)'),
            'a step that resolves a note': recorded,
            'a brief stamped with a kitchen snapshot': recorded,
            'a base behind where the branch leaves trunk': recorded,
            'two arguments': (1, 'usage: kitchen.sh step <store> <sha> <summary>'),
            'no pair.json': (1, 'no pair.json in'),
            'a pair.json that is an array': (1, 'pair.json is damaged: expected an object'),
        }
        out = self.kitchen_cases('step', cases, expect)
        # the second of two failing steps counts the failures in a row; a step whose gates run on leaves a job directory with its log and a link to it
        self.assertIn('2 failed checks in a row; write the report as blocked', out['a step whose gate fails twice in a row']['second'][1])
        self.assertTrue(any(name.endswith('/out.1') for name in out['a first step']['files']))
        self.assertIn('gates: pass', out['a first step']['stdout'])
        self.assertEqual(out['a step whose gate fails']['events'][-1].split('\t')[:2], ['sidekick', 'gate-fail'])
        self.assertEqual(out['a gate that passed after a rerun under load']['events'][-2].split('\t')[:2], ['sidekick', 'gate-flaky'])
        self.assertTrue(any('002-y' in name and name.endswith('.tsv') for name in out['a first step']['files']))

    def test_the_detached_gate_and_verify_runs_ignore_sigint_and_sigquit(self):
        """A detached gate or Verify job starts with SIGINT and SIGQUIT ignored (SigIgn 0x6), read from /proc by the gate's own command and the brief's
        Verify command."""
        gate = self.LAUNCH_TOML.replace('[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["true"]', '[profile.app]\npaths = ["src/**", "tests/**"]\nfast = ["grep SigIgn /proc/self/status >../sigign"]')
        verify = self.launch_brief('002', 'y').replace('```bash\ntrue\n```', '```bash\ngrep SigIgn /proc/self/status >../sigign\n```')
        specs = {'step': self.step_world('{k1}', 'x', toml=gate, env={'PSTACK_KITCHEN_RETRY_LOAD': 'off'}),
                 'finish': self.step_world(files={'reports/002-y.md': '# R\n\nstatus: done\nhead: {k1}\n', 'briefs/002-y.md': verify}, args=['finish', '{st}', '{st}/reports/002-y.md'])}
        specs['finish']['args'] = ['finish', '{st}', '{st}/reports/002-y.md']
        found = {}
        for name, spec in specs.items():
            spec = {**spec, 'repo_files': {'.agents/kitchen.toml': spec.get('toml') or self.KITCHEN_TOML}}
            got = self.script_run(f'sig{name}', root / 'skills', 'pstack-kitchen', spec)
            self.assertEqual(got['rc'], 0, f'{name}: {got["stdout"]}{got["stderr"]}')
            found[name] = (self.path / f'sig{name}' / 'sigign').read_text().strip()
            self.assertEqual(int(found[name].split()[-1], 16) & 0x6, 0x6, f'{name}: SIGINT and SIGQUIT ignored')

    @staticmethod
    def kill_group(group):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)

    def end_with_parent_process(self, code, **popen):
        pre = f'import os, signal, sys\nsys.path.insert(0, {str(scripts)!r})\nfrom pstack_core.wait import end_with_parent\n'
        return subprocess.Popen([sys.executable, '-I', '-c', pre + code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **popen)

    def test_end_with_parent_makes_a_signal_end_the_command_untraced_and_leaves_an_ignored_one_ignored(self):
        """TERM, INT and HUP end the command with the signal as its status and nothing on stderr; a signal the parent ignored stays ignored."""
        ready = 'end_with_parent()\nprint("ready", flush=True)\nsignal.pause()\n'
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            # a gate that runs as a background job hands its children INT, TERM or HUP ignored; the signal under test must be deliverable
            proc = self.end_with_parent_process(ready, preexec_fn=lambda: [signal.signal(each, signal.SIG_DFL) for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)])
            self.addCleanup(proc.kill)
            self.assertEqual(proc.stdout.readline(), 'ready\n')
            proc.send_signal(number)
            out, err = proc.communicate(timeout=30)
            self.assertEqual((proc.returncode, err), (-number, ''), number.name)
        ignored = self.end_with_parent_process('end_with_parent()\nprint(signal.getsignal(signal.SIGINT) is signal.SIG_IGN, signal.getsignal(signal.SIGTERM) is signal.SIG_DFL)\n',
                                               preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
        self.assertEqual(ignored.communicate(timeout=30)[0], 'True True\n')

    def test_end_with_parent_ends_a_background_command_when_its_parent_dies(self):
        """The parent starts the command and is killed; the command, which holds the write end of a pipe, goes with it, so the pipe's reader sees the
        end of file. A command that did not end would hold the pipe open, and the poll for it gives up after 30 seconds."""
        read, write = os.pipe()
        child = f'import signal, sys\nsys.path.insert(0, {str(scripts)!r})\nfrom pstack_core.wait import end_with_parent\nend_with_parent()\nprint(1, flush=True)\nsignal.pause()\n'
        parent = ('import os, signal, subprocess, sys\n'
                  f'started = subprocess.Popen([sys.executable, "-I", "-c", {child!r}], stdout=subprocess.PIPE, pass_fds=({write},))\n'
                  'started.stdout.readline()\nprint(started.pid, flush=True)\nos.kill(os.getpid(), signal.SIGKILL)\n')
        proc = subprocess.Popen([sys.executable, '-I', '-c', parent], pass_fds=(write,), stdout=subprocess.PIPE, text=True)
        os.close(write)
        self.addCleanup(os.close, read)
        pid = int(proc.stdout.readline())

        def kill_command():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        self.addCleanup(kill_command)
        self.assertTrue(select.select([read], [], [], 30)[0], 'the command outlived its parent')
        self.assertEqual(os.read(read, 1), b'')
        proc.wait(timeout=30)

    def test_step_and_finish_outlive_the_shell_that_ran_them(self):
        """step and finish set no parent-death signal: `sh -c 'kitchen.sh step ...; :'` with the `sh` killed mid-gate still recorded the step. The gate (or the Verify command) blocks on a fifo the test opens after it killed the
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
            for tag, skills in (('port', root / 'skills'),):
                top, st, env, fill = self.script_world(f'orphan{name}-{tag}', spec)
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
            'a slice that is no whole number': finish(env={'KITCHEN_STEP_POLL_S': '0.05', 'KITCHEN_STEP_SLICE_S': '1 ? 50 : 1'}),
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
            'no report file': finish(files={'reports/002-y.md': None}),
            'one argument': finish(args=['finish', '{st}']),
            'a report the pair would withhold': finish(files={**done(), 'notes/002-y-n1.md': '# N\n\nstatus: blocking\n'}),
            'no pair.json': finish(nopair=True),
            'outside herdr': finish(env={'HERDR_ENV': '0', 'KITCHEN_STEP_SLICE_S': '60'}),
        }

    def test_finish_behaviour(self):
        cases = self.finish_cases()
        sent = (0, 'prompted master m1')
        expect = {
            'a done report whose Verify commands pass': (0, 'verify: pass (1 commands from the brief)'),
            'a done report whose Verify commands pass, finished twice': (0, 'verify: pass (1 commands from the brief)'),
            'a done report whose Verify commands fail': (2, 'verify: FAIL (2 commands from the brief)'),
            'a failing run reruns on the next finish': (2, 'next: the master did not get this report'),
            'Verify commands still running': (4, "verify: the brief's Verify commands are still running at"),
            'a slice that is no whole number': (1, 'KITCHEN_STEP_SLICE_S must be a whole number'),
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
            'no report file': (1, 'report not found: <T>/st/reports/002-y.md; write it first'),
            'one argument': (1, 'usage: kitchen.sh finish <store> <report-path>'),
            'a report the pair would withhold': (7, 'blocking review notes are still open on 002-y'),
            'no pair.json': (1, 'no pair.json in'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.kitchen_cases('fin', cases, expect)
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
            'a timebox past 64 bits': run(files={**brief(box=99999999999999999999), **fresh}),
            'a clock that is a directory': run(envfill={'KITCHEN_QUIET_CLOCK': '{T}'}),
            'no store argument': dict(base, args=['wait']),
            'a store with no pair.json': run(nopair=True),
            'outside herdr': run(env={**env, 'HERDR_ENV': '0'}),
        }

    def test_the_quiet_wait_behaviour(self):
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
            '--max with an expression': (1, '--max must be a whole number of minutes'),
            '--max with no value': (1, '--max needs a value'),
            '--max that is a name': (1, '--max must be a whole number of minutes'),
            '--max given twice': quiet,
            'a max that is no number': (1, 'KITCHEN_QUIET_MAX_S must be a whole number'),
            'a clock file that is not a number': (1, 'must hold a whole number of seconds'),
            'a timebox past 64 bits': (4, 'elapsed Nm of 99999999999999999999m'),
            'a clock that is a directory': (1, 'Is a directory'),
            'no store argument': (1, 'usage: kitchen.sh wait <store>'),
            'a store with no pair.json': (2, 'no pair.json in'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.kitchen_cases('qw', cases, expect)
        # the quiet wait polls (sleep moves the clock 1500 s each time) until --max ends it, then names the minutes it was quiet
        self.assertIn('quiet: Nm', out['--max given twice']['stdout'])

    def test_a_second_quiet_wait_takes_the_store_from_a_live_one_which_ends_superseded(self):
        """A quiet wait is running (its pid in wait.pid), and a second wait starts on the same store. The newer one owns the store, and the older one,
        reading a pid that is not its own, ends with `superseded`. The newer wait is held inside its first herdr wait until the older one has ended,
        so the order is fixed."""
        cases = self.quiet_wait_cases()
        for older, newer, label in ((root / 'skills', root / 'skills', 'second-wait'),):
            spec = {k: v for k, v in cases['steps do not wake a routine brief'].items() if k not in ('tick', 'topfiles', 'envfill')}
            spec = {**spec, 'repo_files': {'.agents/kitchen.toml': self.KITCHEN_TOML}, 'env': {**spec['env'], 'KITCHEN_QUIET_POLL_S': '0.05'}, 'args': ['wait', '{st}', '--timeout', '0'],
                    'once': {'wait': 'echo >"$FAKE/../claimed"\n'}}
            top, st, env, fill = self.script_world(f'qwcompat-{label}', spec)
            os.mkfifo(top / 'claimed')
            args = [fill(a) for a in spec['args']]
            run = lambda skills, env, **kw: subprocess.Popen([str(skills / 'pstack-kitchen/scripts/kitchen.sh'), *args], cwd=top / 'repo', env=env, text=True,
                                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
            first = run(older, env)
            self.addCleanup(first.kill)
            # the older wait's first herdr wait (after it claimed the store) runs the `once` hook, which writes to a fifo: the test reads it, bounded, so it
            # goes on at that fixed point and no clock is involved
            fd = os.open(top / 'claimed', os.O_RDONLY | os.O_NONBLOCK)
            ready = select.select([fd], [], [], 60)[0]
            os.close(fd)
            self.assertTrue(ready and (st / 'wait.pid').exists(), f'{label}: the older wait never claimed the store')
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

    def test_classify_behaviour(self):
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
            'an escalated brief with a consultant that is a string': (1, 'pair.json is damaged: consultant is string, not an object'),
            'a pair.json that is an array and an escalated brief': (1, 'pair.json is damaged: expected an object'),
            'a pair.json that does not parse and an escalated brief': (1, 'pair.json is damaged: '),
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
            'a pair.json that is an array': (1, 'pair.json is damaged: expected an object'),
            'a git_root that is not a repository': (1, 'kitchen: not a git repository: /nonexistent/repo'),
        }
        out = self.kitchen_cases('classify', cases, expect)
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
        """classify reaches kitchen.py's snapshot, classify and lessons stamp functions in process; none of them writes os.environ (the writes are in
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
            kp = kpy.kitchen_py
            inv = kp.Invocation(str(repo))
            for name, query in (('snapshot', lambda: kp.snapshot(inv, top / 'kitchens')), ('classify', lambda: kp.classify_change(inv, paths=['src/**'])),
                                ('lessons stamp', lambda: kp.lessons_stamp(inv, brief))):
                query()
                self.assertEqual(dict(os.environ), snapshot, name)
        self.assertEqual(dict(os.environ), before)

    def test_a_steer_scope_line_with_a_quote_and_no_quoted_string_adds_nothing_and_the_scan_goes_on(self):
        from pstack_core import checkin
        st = self.path / 'steerscope'
        (st / 'steers').mkdir(parents=True)
        (st / 'steers/001-x-s1.md').write_text('# S\n\nkind: scope\nscope effect: may write "\n')
        (st / 'steers/001-x-s2.md').write_text('# S\n\nkind: scope\nscope effect: may write `src/a.py`\n')
        (st / 'steers/001-x-s3.md').write_text('# S\n\nkind: scope\nscope effect: may write "docs/b.md" "docs/c.md" \u2014 a "note"\n')
        self.assertEqual(checkin.steer_scope_entries(str(st), '001'), ['src/a.py', 'docs/b.md', 'docs/c.md'])

    def test_notify_with_no_store_writes_nothing_to_the_working_directory(self):
        here = self.path / 'cwd-for-notify'
        here.mkdir()
        report = self.path / 'a-report.md'
        report.write_text('# R\n\nstatus: done\n')
        env = {**self.env, 'FAKE': str(self.path), 'HERDR_ENV': '1', 'HERDR_PANE_ID': 'p0', 'XDG_STATE_HOME': str(self.path / 'state'), 'HOME': str(self.path)}
        r = subprocess.run([str(scripts / 'pair.sh'), 'notify', '', str(report)], env=env, cwd=here, capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(sorted(p.name for p in here.iterdir()), [])

    def test_a_generation_or_shown_count_that_is_a_string_is_damage_and_changes_nothing(self):
        from pstack_core import variant as pair_variant
        st = self.claim_store('gen', None, {'sidekick': {'generation': 'x'}})
        before = (st / 'pair.json').read_text()
        with mock.patch.object(send.subprocess, 'run'), self.assertRaises(DamagedStore) as caught:
            send.record_dispatch(str(st), str(st / 'briefs/001-x.md'), now=0)
        self.assertEqual((caught.exception.code, caught.exception.message), (1, 'pair.json is damaged: generation is string, not an integer'))
        self.assertEqual((st / 'pair.json').read_text(), before)
        shown = self.claim_store('shown', None, {'shown': {'001-x': 'a'}})
        (shown / 'steps').mkdir()
        (shown / 'steps/001-x.tsv').write_text('1\tabc\t0\t\tdid it\n')
        before = (shown / 'pair.json').read_text()
        with mock.patch('pstack_core.replies.unreviewed_steps', return_value=[]), self.assertRaises(DamagedStore) as caught:
            pair_variant.steps_due_in(None, str(shown), str(shown / 'briefs/001-x.md'))
        self.assertEqual((caught.exception.code, caught.exception.message), (1, 'pair.json is damaged: 001-x is string, not an integer'))
        self.assertEqual((shown / 'pair.json').read_text(), before)

    def test_a_dispatch_to_a_store_with_no_cwd_records_an_empty_head_and_runs_no_git(self):
        st = self.claim_store('nocwd', None)
        with mock.patch.object(send.subprocess, 'run') as run:
            send.record_dispatch(str(st), str(st / 'briefs/001-x.md'), now=0)
        run.assert_not_called()
        self.assertEqual(json.loads((st / 'pair.json').read_text())['dispatch']['head'], '')

    def test_the_review_engine_read_fails_closed_unless_validate_succeeds(self):
        landing = self.kitchen_core_module('landing')
        kitchen_py = landing.kpy.kitchen_py
        found = lambda engine: kitchen_py.ValidateResult(kitchen_py.Source('repo', '.agents/kitchen.toml', ''), [], [], engine, 'commit', [], [])
        cases = {'an engine': (found('codex'), (True, 'codex')), 'no engine': (found(''), (True, '')),
                 'a failing validate': (kitchen_py.KitchenError('no kitchen at /repo/.agents/kitchen.toml'), (False, ''))}
        for name, (answer, want) in cases.items():
            effect = {'side_effect': answer} if isinstance(answer, Exception) else {'return_value': answer}
            with mock.patch.object(landing, 'repo_root', return_value='/repo'), mock.patch.object(kitchen_py, 'validate', **effect):
                self.assertEqual(landing.validated_engine('/st'), want, name)

    def test_the_modules_merged_into_their_callers_homes_are_gone_and_their_commands_still_resolve(self):
        pair, kitchen = root / 'skills/pstack-pair/scripts/pstack_core', root / 'skills/pstack-kitchen/scripts/kitchen_core'
        gone = [pair / f'{n}.py' for n in ('context', 'herdr', 'dispatch')]
        gone += [pair / 'commands' / f'{n}.py' for n in ('log', 'progress', 'pause', 'spawn', 'send', 'templates', 'queue', 'failover')]
        gone += [kitchen / f'{n}.py' for n in ('reviews', 'discuss')]
        self.assertEqual([str(p.relative_to(root)) for p in gone if p.exists()], [])
        dispatch = self.kitchen_core_module('dispatch')
        self.assertTrue(callable(dispatch.queue) and callable(dispatch.discuss))
        self.assertTrue(all(callable(command) for command in cli.COMMANDS.values()))
        self.assertEqual([cli.COMMANDS[n].__name__ for n in ('log', 'progress', 'pause', 'resume', 'queue', 'failover')], ['log', 'progress', 'pause', 'resume', 'queue', 'failover'])

    def test_last_line_reads_a_block_from_the_end_of_a_file_longer_than_a_block(self):
        f = self.path / 'events.tsv'
        for name, body, want in (('no newline at the end', 'a\n' * 9000 + 'last', 'last'), ('a newline at the end', 'a\n' * 9000 + 'last\n', 'last'),
                                 ('a blank line at the end', 'a\n' * 9000 + 'last\n\n', ''), ('a multibyte character across the block start', 'é' * 5000 + '\nx\tlast\n', 'x\tlast'),
                                 ('one line longer than a block', 'z' * 9000, 'z' * 9000), ('an empty file', '', ''), ('a lone newline', '\n', '')):
            f.write_bytes(body.encode())
            with mock.patch.object(store, 'read_text', side_effect=AssertionError('read the whole file')):
                self.assertEqual(store.last_line(str(f)), want, name)
        with self.assertRaises(FileNotFoundError):
            store.last_line(str(self.path / 'absent'))

    def test_a_child_killed_by_a_signal_ends_the_command_with_128_plus_the_signal(self):
        self.assertEqual([numbers.exit_code(n) for n in (0, 1, 4, 255, -15, -9, -1)], [0, 1, 4, 255, 143, 137, 129])
        for name, number, want in (('TERM', signal.SIGTERM, 143), ('KILL', signal.SIGKILL, 137), ('HUP', signal.SIGHUP, 129)):
            bash = subprocess.run(['bash', '-c', f'out="$(sh -c "kill -{name} \\$\\$")"; echo $?'], capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(bash, str(want))
            self.assertEqual(numbers.exit_code(-number), want)

    def test_a_message_names_the_role_alone_where_the_store_records_no_agent_name_and_a_grace_line_leaves_out_a_delivery_that_is_not_recorded(self):
        verify_wait = self.kitchen_core_module('verify_wait')
        self.assertEqual([store.who('verifier', ''), store.who('consultant', 'c1'), store.who('sidekick', None)], ['verifier', 'consultant c1', 'sidekick'])
        st = self.path / 'st'
        st.mkdir()
        (st / 'pair.json').write_text(json.dumps({'verifying': {'grace_delivery': 'prompted'}}))
        self.assertEqual(verify_wait.delivery_note(str(st)), ' (prompted)')
        (st / 'pair.json').write_text(json.dumps({'verifying': {}}))
        self.assertEqual(verify_wait.delivery_note(str(st)), '')

    def test_closing_a_verification_keeps_what_the_wait_said_on_either_stream_in_the_order_it_said_it_and_ends_a_failure_after_it(self):
        verify = self.kitchen_core_module('verify')
        ctx = Context('pstack-kitchen', '/x', '/s', 'Kitchen', (), (), 21, '', 'kitchen.sh')

        class Said:
            def __init__(self, *args, out, err, **kwargs):
                self.status = 'clean'
                self.out, self.err = out, err

            def run(self):
                self.out('verdict: v\n')
                self.err('scratch said so\n')
                self.out('status: clean\n')
                if fail:
                    raise ToolFailed(3, 'then it failed')
                return verify.WaiterOutcome(0, 'clean')

        for fail, want in ((False, (verify.WaiterOutcome(0, 'clean'), ['verdict: v\n', 'scratch said so\n', 'status: clean\n'])),
                           (True, (verify.WaiterOutcome(3, 'clean'), ['verdict: v\n', 'scratch said so\n', 'status: clean\n', 'kitchen.sh: then it failed\n']))):
            lines = []
            with mock.patch.object(verify, 'Waiter', Said):
                self.assertEqual((verify.close_wait(ctx, '/st', lines.append), lines), want, fail)

    def test_closing_keeps_the_direct_and_child_stderr_of_the_code_under_the_wait_in_the_order_it_was_written(self):
        """The old capture held fd 2 for the wait; now stderr written in process, and the stderr of the git child that the scratch removal runs, land
        in the closed text between the lines the wait said and the failure it ends with."""
        verify = self.kitchen_core_module('verify')
        from pstack_core.commands import scratch
        from pstack_core.errors import PrimeFailed
        ctx = Context('pstack-kitchen', '/x', '/s', 'Kitchen', (), (), 21, '', 'kitchen.sh')
        st = self.path / 'stderr-order'
        st.mkdir()
        (st / 'pair.json').write_text(json.dumps({'git_root': '/nonexistent-dir'}) + '\n')

        class Writes:
            def __init__(self, *args, out, err, **kwargs):
                self.status, self.out = '', out

            def run(self):
                self.out('a stdout line\n')
                sys.stderr.write('PY-STDERR prime reason\n')
                scratch.remove_scratch(ctx, str(st), '001-x-verify')
                raise PrimeFailed('Prime verifier cleanup failed')

        lines = []
        with mock.patch.object(verify, 'Waiter', Writes):
            ended = verify.close_wait(ctx, str(st), lines.append)
        self.assertEqual((ended.rc, lines[:2], lines[-1]), (5, ['a stdout line\n', 'PY-STDERR prime reason\n'], 'kitchen.sh: Prime verifier cleanup failed\n'))
        self.assertEqual(len(lines), 4, lines)
        self.assertRegex(lines[2], r"\Afatal: cannot change to '/nonexistent-dir'")

    def test_run_close_says_the_refusal_of_the_scratch_removal_to_the_sink_it_is_given(self):
        verify_state = self.kitchen_core_module('verify_state')
        world = self.path / 'world-sink'
        world.mkdir()
        ctx = Context('pstack-kitchen', str(root / 'skills/pstack-kitchen'), str(self.path / 'state'), 'Kitchen', ('sidekick',), (), 21, '', 'kitchen.sh')
        said = []
        with mock.patch.object(verify_state, 'close_verifier', side_effect=lambda *a: verify_state.remove_scratch(ctx, str(world), '001-x-verify')), \
                mock.patch.object(verify_state, 'my_claim', return_value=False):
            with self.assertRaises(ToolFailed) as ended:
                verify_state.run_close(ctx, str(world), 'v1', 'claude', 'p3', 'v', said.append)
        self.assertEqual((ended.exception.code, said), (1, [f'kitchen.sh: no pair.json in {world}; run: pair.sh init <slug>\n']))

    def test_removing_the_scratch_of_a_store_with_no_pair_json_raises_the_core_refusal_in_process(self):
        verify_state = self.kitchen_core_module('verify_state')
        world = self.path / 'world'
        world.mkdir()
        ctx = Context('pstack-kitchen', str(root / 'skills/pstack-kitchen'), str(self.path / 'state'), 'Kitchen', ('sidekick',), (), 21, '', 'kitchen.sh')
        with self.assertRaises(MissingStore) as refused:
            verify_state.remove_scratch(ctx, str(world), '001-x-verify')
        self.assertEqual(translate(refused.exception, ctx.prog), (f'kitchen.sh: no pair.json in {world}; run: pair.sh init <slug>\n', 1))

    def test_a_scratch_is_made_and_removed_in_process_and_each_returns_its_path(self):
        from pstack_core.commands import scratch
        ctx = Context('pstack-pair', '/x', '/s', 'Pair', ('sidekick',), (), 21, '')
        repo, st = self.path / 'scr-repo', self.path / 'scr-st'
        repo.mkdir()
        st.mkdir()
        env = {**self.env, **self.GIT}
        for args in (('init', '-q', '-b', 'main'), ('commit', '-q', '--allow-empty', '-m', 'one')):
            subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True)
        (st / 'pair.json').write_text(json.dumps({'git_root': str(repo)}) + '\n')
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], env=env, capture_output=True, text=True, check=True).stdout.strip()
        with mock.patch.dict(os.environ, env):
            path = scratch.make_scratch(ctx, str(st), '001-x', at=head)
            self.assertTrue(os.path.isdir(path))
            self.assertEqual(scratch.make_scratch(ctx, str(st), '001-x', at=head), path)
            self.assertEqual(json.loads((st / 'pair.json').read_text())['scratch'], ['001-x'])
            with self.assertRaises(BadScratch) as refused:
                scratch.make_scratch(ctx, str(st), 'bad id')
            self.assertEqual(translate(refused.exception, 'pair.sh'), ('pair.sh: scratch id must look like NNN-<slug>, e.g. NNN-<slug>-c<k> or NNN-<slug>-review\n', 1))
            with self.assertRaises(BadScratch) as refused:
                scratch.make_scratch(ctx, str(st), '002-y', at='0' * 40)
            self.assertEqual(translate(refused.exception, 'pair.sh'), (f'pair.sh: not a commit: {"0" * 40}\n', 1))
            self.assertEqual(scratch.remove_scratch(ctx, str(st), '001-x'), path)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(json.loads((st / 'pair.json').read_text())['scratch'], [])

    def test_spawn_options_read_the_words_after_the_store_with_the_last_of_a_repeated_option_winning(self):
        from pstack_core.spawn import SpawnOptions, spawn_options
        self.assertEqual(spawn_options([]), SpawnOptions())
        got = spawn_options(['--kind', 'a', '--kind', 'b', '--fallback', 'c', '--fallback-arg', '--x', '--fallback-arg', 'y', '--tab', '--split', '--pane', 'p9',
                             '--timeout', '5', '--', 'n1', '--n2'])
        self.assertEqual(got, SpawnOptions(kind='b', fallback='c', fallback_args=('--x', 'y'), pane='p9', timeout='5', placement='split', native=('n1', '--n2')))

    def test_translate_turns_a_failure_into_stderr_text_and_an_exit_code(self):
        cases = [(Exit(4, 'said\n'), 'pair.sh', ('said\n', 4)), (Exit(2), 'pair.sh', ('', 2)), (Die('refused', 5), 'pair.sh', ('pair.sh: refused\n', 5)),
                 (Die('refused'), 'kitchen.sh', ('kitchen.sh: refused\n', 1)), (PairError('plain'), 'pair.sh', ('pair.sh: plain\n', 1)),
                 (DamagedStore('a is string, not an object'), 'kitchen.sh', ('kitchen.sh: pair.json is damaged: a is string, not an object\n', 1)),
                 (MissingStore('/s'), 'pair.sh', ('pair.sh: no pair.json in /s; run: pair.sh init <slug>\n', 1)),
                 (Paused('paused'), 'pair.sh', ('pair.sh: paused\n', 5)), (PlanNotAgreed('no plan'), 'pair.sh', ('pair.sh: no plan\n', 6)),
                 (ToolFailed(7), 'pair.sh', ('', 7)), (ToolFailed(3, 'it said so'), 'pair.sh', ('pair.sh: it said so\n', 3)),
                 (OSError('disk'), 'pair.sh', ('pair.sh: disk\n', 1)), (ValueError('bad'), 'kitchen.sh', ('kitchen.sh: bad\n', 1))]
        for exc, prog, want in cases:
            self.assertEqual(translate(exc, prog), want, repr(exc))
        with self.assertRaises(KeyError):
            translate(KeyError('bug'), 'pair.sh')
        self.assertEqual(translate(refuse_as(MissingStore('/s'), 'pair.sh', 2), 'pair.sh'), ('pair.sh: no pair.json in /s; run: pair.sh init <slug>\n', 2))

    def test_a_damaged_field_ends_kitchen_wait_with_the_message_and_status_the_core_gives(self):
        st = self.path / 'capwait'
        st.mkdir()
        (st / 'pair.json').write_text(json.dumps({'master': {'name': 'm1'}, 'sidekick': 3, 'git_root': str(self.path)}) + '\n')
        code = ('import shutil\nfrom pstack_core import cli\nfrom kitchen_core import wait as kitchen_wait\n'
                'cli.shutil.which = lambda name: "/x/herdr"\n'
                f'rc = cli.main(["--skill-root", {str(root / "skills/pstack-kitchen")!r}, "wait", store, "--timeout", "0"])\n'
                'print("rc=%d" % rc)')
        prc, got, perr = self.kitchen_py(st, code, env={'HERDR_ENV': '1'})
        self.assertEqual(prc, 0, perr)
        self.assertEqual(got.splitlines(), ['', 'rc=1'])
        self.assertEqual(perr, 'kitchen.sh: pair.json is damaged: sidekick is number, not an object\n')

    def test_a_wait_that_fails_after_it_printed_shows_what_it_printed_before_its_message_in_the_pair_and_in_the_kitchen(self):
        """A step row with no JSON number ends the wait after its `state:`, `steps:`, `range:` and `next:` lines (the output at 61781042f)."""
        spec = self.quiet_wait_cases()['steps wake an escalated brief']
        spec = {**spec, 'files': {**spec['files'], 'steps/001-x.tsv': '1\t{c1}\t5\t\tone\n02\t{c2}\t6\t\ttwo\n'}}
        message = "pair.sh: the last step row does not start with a JSON step number: '02'\n"
        for label, got in (('kitchen', self.kitchen_world('badrow-out', spec)), ('pair', self.script_run('badrow-pair', root / 'skills', 'pstack-pair', spec))):
            self.assertRegex(got['stdout'], r'\Astate: working\nsteps: 2 to review on 001-x\n  1 [0-9a-f]{9} one\n  02 [0-9a-f]{9} two\n'
                                            r'range: [0-9a-f]{9}\.\.[0-9a-f]{9}\nnext: review the range, then pair.sh new-note <T>/st 001\n\Z', label)
            self.assertEqual((got['rc'], got['stderr']), (2, message), label)

    def test_the_quiet_wait_ends_with_the_status_its_outcome_carries_and_releases_its_claim(self):
        ctx = Context('pstack-kitchen', '/x', '/s', 'Kitchen', (), (), 21, '')
        kitchen_wait = self.kitchen_core_module('wait')
        for code in (0, 1, 2, 4):
            outcome = wait.WaitOutcome(code, 'missing')

            def waited(ctx_, store, *, timeout, owner, quiet, out):
                out('state: \n')
                return outcome

            with mock.patch.object(kitchen_wait, 'wait_target', return_value=('/st', 0)), mock.patch.object(kitchen_wait, 'wait_for', waited), \
                    mock.patch.object(kitchen_wait, 'release') as release, mock.patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(kitchen_wait.quiet_wait(ctx, '/st', []), code, ('wait', code))
            self.assertEqual((out.getvalue(), release.call_count), ('state: \n', 1), ('wait', code))

    def test_step_kpy_call_leaves_the_environment_alone(self):
        """step reaches kitchen.py's classify function in process (the gates, the policy check and brief-verify run in the detached scripts, as
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
            found = kpy.kitchen_py.classify_change(kpy.kitchen_py.Invocation(str(repo)), base=shas[0], head=shas[1])
            self.assertEqual(found['profiles'], ['app'])
            self.assertEqual(dict(os.environ), snapshot)
        self.assertEqual(dict(os.environ), before)

    def test_setup_kpy_calls_leave_the_environment_alone(self):
        """init, spawn, consultant and fleet reach kitchen.py's roster, validate and fleet functions and `setup --role sidekick` in process; none
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
            kp = kpy.kitchen_py
            for name, query in (('roster', lambda: kp.roster_data()), ('validate', lambda: kp.validate(kp.Invocation(str(repo)))),
                                ('fleet', lambda: kp.fleet(top / 'state/pstack/kitchen/runs', None))):
                query()
                self.assertEqual(dict(os.environ), snapshot, name)
            self.assertTrue(kp.run_setup(kp.Invocation(str(repo)), 'sidekick')['passed'])
            self.assertEqual(dict(os.environ), snapshot, 'setup')
        self.assertEqual(dict(os.environ), before)

    def test_a_kitchen_error_from_a_query_is_shown_as_the_command_line_prints_it_and_ends_the_command_with_its_code(self):
        kpy = self.kitchen_core_module('kpy')
        from pstack_core.errors import ToolFailed

        def refuse(*args, **kwargs):
            raise kpy.KitchenError('no kitchen at /repo/.agents/kitchen.toml', 3)
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out, mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            out.write('before ')
            with self.assertRaises(ToolFailed) as caught:
                kpy.call(refuse, 1, key=2)
        self.assertEqual((caught.exception.code, out.getvalue(), err.getvalue()), (3, 'before ', 'kitchen: no kitchen at /repo/.agents/kitchen.toml\n'))
        self.assertEqual(kpy.call(lambda *a, **k: (a, k), 1, key=2), ((1,), {'key': 2}))

    def test_verify_behaviour(self):
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
            'a tab launch whose master is a string, with a live sidekick pane': (1, 'pair.json is damaged: master is string, not an object'),
            '--timeout as the sum 1+1': (1, '--timeout must be a whole number of minutes'),
            '--timeout as the product 2*15': (1, '--timeout must be a whole number of minutes'),
            '--timeout with a leading zero': (4, 'deadline 10m (--timeout)'),
            'a roster that does not parse, with --kind': (2, "\nkitchen: <T>/home/.config/pstack/kitchen.toml: Invalid value (at end of document)\n"),
            'a retry entry that is not JSON': (2, 'verifier entry is not JSON'),
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
            '--covers with no value': (1, '--covers needs a value'),
            '--covers that is no unit': (1, "--covers takes unit numbers (NNN), one per flag or a comma list, not 'abc'"),
            '--every and --until-verdict together': (1, '--every and --until-verdict are alternatives'),
            '--landing with a unit': (1, '--landing verifies the whole stack; name no units'),
            '--head without --landing': (1, '--head goes with --landing'),
            '--parent without --landing': (1, '--parent goes with --landing'),
            '--cancel with --wait': (1, '--cancel takes no units, --landing or --wait'),
            '--wait with nothing open': (5, 'no verification is open; start one with kitchen.sh verify'),
            'too few arguments': (1, 'usage: kitchen.sh verify <store> <NNN>...'),
            'a pair.json that is an array': (1, 'pair.json is damaged: expected an object'),
            'a verifier that is a string': (1, 'pair.json is damaged: verifier is string, not an object'),
            'a heads that is a string': (1, 'pair.json is damaged: heads is string, not an object'),
            'a master that is a string': (1, 'pair.json is damaged: master is string, not an object'),
            'a sidekick that is a string': (1, 'pair.json is damaged: sidekick is string, not an object'),
            'outside herdr': (1, 'not running inside Herdr'),
        }
        out = self.kitchen_cases('launch', cases, expect)
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
        self.assertIn('0m of 10m', out['--timeout with a leading zero']['stdout'])

    def test_a_timeout_that_is_no_whole_number_and_a_flag_with_no_value_end_verify_naming_the_flag(self):
        """`--timeout -5`, `--timeout abc` and `--timeout 1.5` are usage errors naming the flag, and `--timeout` with no value is `--timeout needs a value`;
        all end with status 1 and open no verification."""
        cases = ((['001', '--timeout', 'abc', '--every', '0'], '--timeout must be a whole number of minutes'),
                 (['001', '--timeout', '-5', '--every', '0'], '--timeout must be a whole number of minutes'),
                 (['001', '--timeout', '1.5', '--every', '0'], '--timeout must be a whole number of minutes'),
                 (['001', '--every', 'x'], '--every must be a whole number of minutes'),
                 (['001', '--timeout'], '--timeout needs a value'))
        for n, (args, ours) in enumerate(cases):
            got = self.kitchen_world(f'vtimeout{n}', self.launch_world(args=['verify', '{st}', *args]))
            self.assertEqual(got['rc'], 1)
            self.assertIn(ours, got['stderr'])
            self.assertNotIn('"verifying"', got['files']['pair.json'])

    def test_a_pane_that_will_not_split_ends_verify_saying_no_pane_id_came_back(self):
        """When herdr fails to split a pane, or answers with none, verify dies with status 2 and says no pane id came back; no verification is open."""
        for n, fake in enumerate(({'split-fail': ''}, {'split-null': ''})):
            got = self.kitchen_world(f'vsplit{n}', self.launch_world(fakefiles=fake))
            self.assertEqual(got['rc'], 2)
            self.assertIn('pane split returned no pane id', got['stderr'])
            self.assertNotIn('"verifying"', got['files']['pair.json'])
            self.assertIn('pane split', got['calls'])

    def test_a_waiter_mid_wait_when_another_call_cancels_the_verification_says_it_was_closed_elsewhere(self):
        """A6. A waiter is mid-wait (its first `agent get`) when another call cancels the verification and the next attempt's `.verifying` is written:
        the waiter says `verification closed elsewhere`, exits 5, and touches neither the new attempt nor the panes. The cancel runs from the fake
        herdr's once hook, so the interleaving is fixed, with no sleeps."""
        head = root / 'skills/pstack-kitchen/scripts/kitchen.sh'
        hook = ('cd "$FAKE/.." || exit 1\n'
                f'{head} verify "$FAKE/../st" --cancel >"$FAKE/closer.out" 2>&1\n'
                'python3 -c \'import json, sys; p = sys.argv[1]; d = json.load(open(p)); '
                'd["verifying"] = {"verdict": "/next/002-y-v1.md", "units": "002", "idle_since": 77, "scratch": "002-y-v1", "brief": "002-y", "packet": "/next/p.md", "unit": "002-y"}; '
                'json.dump(d, open(p, "w"))\' "$FAKE/../st/pair.json"\n')
        spec = self.wait_world('5', agents={'v1': 'working claude'}, once={'get': hook})
        spec = {**spec, 'repo_files': {'.agents/kitchen.toml': self.KITCHEN_TOML}}
        top, st, env, fill = self.script_world('interop', spec)
        r = subprocess.run([str(head), 'verify', str(st), '--wait', '--every', '5'], cwd=top / 'repo', env=env, capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual((r.returncode, r.stdout), (5, 'verification closed elsewhere\n'), r.stderr)
        closer_out = (top / 'fake/closer.out').read_text()
        self.assertIn('cancelled: 001', closer_out)
        doc = json.loads((st / 'pair.json').read_text())
        self.assertEqual(doc['verifying']['verdict'], '/next/002-y-v1.md')
        self.assertEqual(doc['verifying']['idle_since'], 77)
        self.assertEqual((top / 'fake/calls.log').read_text().count('pane close'), 1)
        self.assertIn('verifier-outcome: cancelled', (st / 'verdicts/001-x-v1-packet.md').read_text())

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

    def test_unit_range_measures_from_the_first_units_base_to_the_last_units_head_and_says_why_it_cannot(self):
        units = self.kitchen_core_module('units')
        st, repo, shas, stacks = self.range_world('urange')
        subprocess.run(['git', '-C', str(repo), 'checkout', '-q', 'main'], check=True, env={**self.env, **self.GIT})
        plain = stacks['plain'][1]
        base, trunk, u1, u2 = shas['base'], shas['trunk'], shas['u1'], shas['u2']
        # (units, covered, range base) -> (the range printed or None, what is said on stderr)
        calls = [(['001'], [], '', (base, u2, '')), (['001', '002'], [], '', (base, u1, '')), (['002', '001'], [], '', (base, u2, '')),
                 (['001', '019', '002'], [], '', (base, u1, '')), (['019', '001'], [], '', (base, u2, '')),
                 (['001', '006'], [], '', 'unit 006 has no report\n'), (['001', '006'], ['006'], '', "unit 006's report has no head:\n"), (['006'], [], '', 'unit 006 has no report\n'),
                 (['001', '011'], [], '', (base, u2, "note: unit 011's report is partial; checking the head it names\n")),
                 (['001', '012', '013'], [], '', (base, u2, "note: unit 012's report is blocked; checking the head it names\nnote: unit 013's report is failed; checking the head it names\n")),
                 (['001', '014'], [], '', "unit 014's report has no status:\n"),
                 (['001', '015'], [], '', "unit 015's report is weird, not done, partial, blocked, or failed\n"),
                 (['001', '017'], [], '', "unit 017's report has no head:\n"), (['001', '018'], [], '', "unit 018's head is not a commit\n"),
                 (['011', '001'], ['011'], '', (base, u2, '')),
                 (['016'], [], '', (trunk, plain, f"note: unit 016's recorded base {base[:9]} is behind where its branch leaves remotes/origin/main; measuring from {trunk[:9]}\n")),
                 (['016', '001'], [], '', (base, u2, '')),
                 (['001', '016'], [], '', (trunk, plain, f"note: unit 001's recorded base {base[:9]} is behind where its branch leaves remotes/origin/main; measuring from {trunk[:9]}\n")),
                 (['016'], [], base, (base, plain, '')), (['001'], [], trunk, (trunk, u2, '')), (['001'], [], 'nothere', '--base nothere is not a commit\n'),
                 (['005'], [], '', 'unit 005 was never dispatched\n'), (['009', '001'], [], '', (base, u2, '')),
                 (['001', '010'], [], '', 'no brief for unit 010\n'), (['001', '010'], ['010'], '', 'no brief for unit 010\n'),
                 (['010', '001'], [], '', 'no brief for unit 010\n'), (['008'], [], '', ('nothere', u2, '')), (['001', '009'], [], '', "unit 009's head is not a commit\n")]
        for names, covered, range_base, want in calls:
            py = ('try:\n    base, head = units.unit_range(ctx, store, %r, %r, %r)\n    print(base, head)\n    code = 0\nexcept (Die, PairError) as e:\n    sys.stderr.write("pair.sh: %%s\\n" %% e.message)\n'
                  '    code = e.code\nprint("rc=%%d" %% code)' % (names, covered, range_base))
            prc, got, perr = self.kitchen_py(st, py, pid=os.getpid(), cwd=repo)
            self.assertEqual(prc, 0, perr)
            perr = perr.replace(str(st), '<S>')
            if isinstance(want, tuple):
                self.assertEqual((got, perr), (f'{want[0]} {want[1]}\nrc=0\n', want[2]), (names, covered, range_base))
            else:
                self.assertEqual(got, 'rc=1\n', (names, covered, range_base))
                self.assertIn('pair.sh: ' + want, perr, (names, covered, range_base))

    def test_landing_base_says_where_the_stack_starts_and_why(self):
        st, repo, shas, stacks = self.range_world('ulbase')
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], check=True, env={**self.env, **self.GIT}, capture_output=True, text=True).stdout.strip()
        git('checkout', '-q', 'plain')
        tip, trunk = stacks['plain'][1], shas['trunk']
        side = git('rev-parse', 'unit')
        short = lambda sha: sha[:9]
        merge_base = f'landing base: merge-base of remotes/origin/main and HEAD ({short(trunk)})\n'
        stale = lambda label, shown: f'landing base: recorded parent {label} ({shown}) is no longer an ancestor of {short(tip)}; using the trunk merge-base\n' + merge_base
        base_doc = json.loads((st / 'pair.json').read_text())
        # (name, pair.json additions, options, status, what is said on stdout (for status 0) or stderr (otherwise))
        cases = [('no parent recorded', {}, {}, 0, merge_base),
                 ('a recorded parent that is an ancestor', {'landing': {'parent': trunk, 'parent_ref': 'rel'}}, {}, 0, f'landing base: recorded parent rel ({short(trunk)})\n'),
                 ('a recorded parent that is not an ancestor', {'landing': {'parent': side, 'parent_ref': 'rel'}}, {}, 0, stale('rel', short(side))),
                 ('a recorded parent that is the head', {'landing': {'parent': tip, 'parent_ref': 'rel'}}, {}, 0, stale('rel', short(tip))),
                 ('a recorded parent that is no commit', {'landing': {'parent': 'nothere', 'parent_ref': 'rel'}}, {}, 0, stale('rel', 'nothere')),
                 ('an override that is an ancestor', {}, {'override': trunk, 'label': 'given'}, 0, f'landing base: recorded parent given ({short(trunk)})\n'),
                 ('an override that is not an ancestor', {}, {'override': side, 'label': 'given'}, 1,
                  f'--parent given ({short(side)}) must be an ancestor of {short(tip)} and not the same commit; name where the stack starts\n'),
                 ('an override that is the head', {}, {'override': tip, 'label': 'given'}, 1,
                  f'--parent given ({short(tip)}) must be an ancestor of {short(tip)} and not the same commit; name where the stack starts\n'),
                 ('none clears a recorded parent', {'landing': {'parent': trunk, 'parent_ref': 'rel'}}, {'label': 'none'}, 0, merge_base),
                 ('--base leaves the recorded parent unread', {'landing': {'parent': trunk, 'parent_ref': 'rel'}}, {'range_base': shas['base']}, 0, merge_base),
                 ('--base with an override that is not an ancestor', {}, {'override': side, 'label': 'given', 'range_base': shas['base']}, 0, merge_base),
                 ('landing a string', {'landing': 'x'}, {}, 1, 'pair.json is damaged: landing is string, not an object\n'),
                 ('landing an array', {'landing': [1]}, {}, 1, 'pair.json is damaged: landing is array, not an object\n'),
                 ('a parent that is a number', {'landing': {'parent': 5, 'parent_ref': 'r'}}, {}, 0, stale('r', '5')),
                 ('a parent with no ref', {'landing': {'parent': trunk}}, {}, 0, f'landing base: recorded parent  ({short(trunk)})\n')]
        for name, landing, opts, status, said in cases:
            (st / 'pair.json').write_text(json.dumps({**base_doc, **landing}, indent=2) + '\n')
            py = ('try:\n    rev = units.landing_base(store, %r, %r, %r, %r, %r)\n    print("rev=" + rev)\n    code = 0\nexcept (Die, PairError) as e:\n    sys.stderr.write("pair.sh: %%s\\n" %% e.message)\n'
                  '    code = e.code\nprint("rc=%%d" %% code)' % (str(repo), tip, opts.get('override', ''), opts.get('label', ''), opts.get('range_base', '')))
            prc, got, perr = self.kitchen_py(st, py, cwd=repo)
            if status == 0:
                self.assertEqual((got, perr), (said + f'rev={trunk}\nrc=0\n', ''), name)
            else:
                self.assertEqual((got, perr), ('rc=1\n', 'pair.sh: ' + said), name)
        # no trunk, and a head on the trunk
        git('remote', 'add', 'origin', str(repo))
        git('update-ref', '-d', 'refs/remotes/origin/main')
        git('checkout', '-q', 'main')
        (st / 'pair.json').write_text(json.dumps(base_doc, indent=2) + '\n')
        main = git('rev-parse', 'main')
        for name, head in (('a head that is the trunk', main), ('no trunk at all', None)):
            if head is None:
                git('checkout', '-q', '--detach', tip)
                git('update-ref', '-d', 'refs/heads/main')
                head = tip
            py = ('try:\n    print("rev=" + units.landing_base(store, %r, %r))\n    code = 0\nexcept (Die, PairError) as e:\n    sys.stderr.write("pair.sh: %%s\\n" %% e.message)\n'
                  '    code = e.code\nprint("rc=%%d" %% code)' % (str(repo), head))
            prc, got, perr = self.kitchen_py(st, py, cwd=repo)
            self.assertEqual((got, perr), ('rc=1\n', "pair.sh: cannot tell this repo's trunk; pass --base <trunk ref or commit>\n"), name)

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
            'a retry entry that is not JSON': verify('001', '--retry-entry', 'not json', '--every', '0'),
            '--timeout as the sum 1+1': verify('001', '--timeout', '1+1', '--every', '0'),
            '--timeout as the product 2*15': verify('001', '--timeout', '2*15', '--every', '0'),
            '--timeout with a leading zero': verify('001', '--timeout', '010', '--every', '0'),
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

    # Behaviour of the pair commands as an agent meets them: the exit code a caller branches on and the line it reads. Each case is a world
    # the tables above build; the port runs alone, against the fake herdr.
    port_runs = 0

    def check_port(self, cases, expected, skill='pstack-pair'):
        for name, (rc, text) in expected.items():
            with self.subTest(name):
                spec = cases[name]
                type(self).port_runs += 1
                got = self.script_run(f'port-{type(self).port_runs}', root / 'skills', skill, spec[0] if isinstance(spec, tuple) else spec)
                self.assertEqual(got['rc'], rc, got['stderr'])
                self.assertIn(text, got['stdout'] + got['stderr'])

    def test_failover_refuses_and_reports_what_a_caller_reads(self):
        self.check_port(self.failover_cases(), {
            'no fallback recorded': (5, 'no fallback recorded; spawn the sidekick with --fallback KIND'),
            'already on the fallback': (5, 'the sidekick already runs the fallback (devin)'),
            'a working sidekick without --force': (5, 'sidekick s1 is working; pair.sh stop <T>/st first, or pass --force to interrupt it'),
            'a prime-agent sidekick whose cleanup fails': (5, 'Prime session cleanup failed; no fallback was started'),
            'a sidekick that does not exit in time': (5, 'claude sidekick has not exited; close it by hand, then rerun failover'),
            'a spawn that dies': (2, 'agent start failed in p2; inspect: herdr pane read p2 --source visible'),
            'a spawn that returns 4': (4, 'sidekick settled but reports/000-ready.md is missing; state=idle'),
            'a spawn that returns 3': (3, 'sidekick bootstrap did not settle'),
        })

    def test_rotate_refuses_and_reports_what_a_caller_reads(self):
        self.check_port(self.rotate_cases(), {
            'no rotation required': (5, 'rotation requires a completed task; partial or blocked work keeps its session'),
            'no recorded start args': (5, 'no recorded startup arguments; exit the idle sidekick and spawn it with its original model and permission flags'),
            'a paused run': (5, 'the store is paused (lunch): start nothing new; record where the work stands and end your turn, or run pair.sh resume <T>/st'),
            'a working devin': (5, 'sidekick s1 is working; wait for its report and turn to end before rotating'),
            'a prime-agent sidekick whose cleanup fails': (5, 'Prime session cleanup failed; no new session was started'),
            'an untolerated error and an agent that leaves': (2, 'devin exit failed: {"error":{"code":"boom"}}'),
            'a sidekick that does not exit in time': (5, 'devin has not exited; inspect s1 before retrying rotate'),
        })

    def test_rotate_exits_and_respawns_every_kind_a_sidekick_can_be(self):
        for kind, exit_line in (('claude', '/exit'), ('codex', '/quit'), ('devin', '/exit'), ('pi', '/quit'), ('muse', '/quit'), ('prime-agent', '/quit')):
            with self.subTest(kind):
                type(self).port_runs += 1
                got = self.script_run(f'port-{type(self).port_runs}', root / 'skills', 'pstack-pair', {**self.rotation_world(kind), 'args': ['rotate', '{st}']})
                self.assertEqual(got['rc'], 0, got['stderr'])
                self.assertIn(f'sidekick s1 ({kind}) started in p1', got['stdout'])
                self.assertIn(f'agent start s1 --kind {kind} --pane p1', got['calls'])
                self.assertIn(f'agent prompt s1 {exit_line}', got['calls'])

    def test_steer_refuses_and_reports_what_a_caller_reads(self):
        cases = self.steer_cases()
        real = self.env['PATH']
        cases['a working sidekick whose esc cannot be sent'] = {
            **cases['a working sidekick with --interrupt that settles'],
            'topfiles': {'nokeys/herdr': (f'#!/bin/sh\n[ "$1 $2" != "agent send-keys" ] || exit 1\nexec {root / "tests/pstack-pair/bin/herdr"} "$@"\n', 0o755)},
            'envfill': {'PATH': '{T}/nokeys:' + real}}
        self.check_port(cases, {
            'a working sidekick and a prompt that fails': (2, 'herdr prompt failed for s1'),
            'a working prime sidekick whose daemon fails': (2, 'Prime steer failed for s1'),
            'a working sidekick whose esc cannot be sent': (2, 'herdr send-keys failed for s1'),
            'an unknown sidekick': (5, 'sidekick s1 is unknown; read the pane, then pass --force to steer anyway'),
            'an idle sidekick whose unit failed and no objection': (5, 'sidekick s1 is idle and no objection is open on unit 001; fold the steer into the next brief instead'),
            'the sidekick is blocked': (3, 'sidekick s1 is blocked; inspect: herdr agent read s1 --source visible --lines 60'),
        })

    def test_init_names_the_pane_flag_when_herdr_pane_id_is_stale(self):
        self.check_port(self.init_cases(), {'a stale HERDR_PANE_ID': (2, "pane gone1 is not a live Herdr pane; HERDR_PANE_ID is stale when a harness runs commands through a shared daemon (Codex's app-server). Pass your pane with --pane ID (herdr agent list shows each agent's pane)")})

    def test_stop_of_a_blocked_sidekick_names_what_to_inspect(self):
        self.check_port(self.stop_cases(), {'the sidekick is blocked': (3, 'sidekick s1 is blocked; inspect it before stopping')})

    def test_queue_and_dispatch_refuse_with_the_exit_code_a_caller_branches_on(self):
        queue = self.queue_send_cases()
        self.check_port(queue, {
            'the sidekick is blocked': (3, 'sidekick s1 is blocked; resolve that before queueing'),
            'an unknown option': (1, 'unknown option --force'),
            'an empty third argument': (1, 'unknown option '),
            'the sidekick is idle': (5, 'sidekick s1 is idle; dispatch instead: pair.sh dispatch <T>/st <T>/st/briefs/003-z.md'),
            'a paused store': (5, 'the store is paused (lunch): start nothing new; record where the work stands and end your turn, or run pair.sh resume <T>/st'),
            'a feature brief with no plan': (6, 'playbook feature needs an agreed plan; brief has plan: <missing>'),
            'a feature brief whose plan is missing': (6, 'plan not found: <T>/st/plans/009-none.md'),
            'a feature brief whose plan has no review': (6, 'plan <T>/st/plans/001-x.md has no review; run pair.sh discuss and write reviews/001-<slug>.md with verdict: agreed'),
            'a feature brief whose review is not agreed': (6, "plan <T>/st/plans/001-x.md review verdict is 'revise', not agreed"),
        })
        self.check_port(self.send_cases(), {
            'the sidekick is working': (5, 'sidekick s1 is working; run: pair.sh wait <T>/st'),
            'a devin sidekick that needs a rotation': (5, 'rotating the sidekick before this brief failed:\npair.sh: no recorded startup arguments'),
            'a dispatch while another brief is queued': (5, 'the queue holds <T>/st/briefs/002-y.md; dispatch that first, or: pair.sh queue <T>/st --clear'),
        })

    def test_report_next_and_finish_say_what_is_missing(self):
        self.check_port(self.AB_CASES, {
            'report with none': (4, 'no report found'),
            'next while paused': (5, 'a claude sidekick leaves the queue for the master to dispatch after a fresh session'),
            'next with nothing queued': (5, 'a claude sidekick leaves the queue for the master to dispatch after a fresh session'),
            'done report with a stale head': (7, "rewrite the report at the unit's last commit (head:), then finish again"),
            'done report with open notes': (7, 'blocking review notes are still open on 001-x:'),
        })

    def test_answer_names_the_state_that_blocks_it(self):
        self.check_port(self.answer_cases(), {
            'the sidekick is working': (5, 'sidekick s1 is working; run: pair.sh wait <T>/st'),
            'a devin that needs a rotation': (5, 'the devin sidekick needs a fresh session or a successful bootstrap before another task; inspect READY and run pair.sh rotate <T>/st after a completed task. The queue is preserved.'),
            'the sidekick is blocked': (3, 'sidekick s1 is blocked; inspect: herdr agent read s1 --source visible --lines 60'),
        }, 'pstack-pair-guided')

    def test_spawn_reports_the_failure_a_caller_branches_on(self):
        hook = self.spawn_command_cases()
        side = {'sidekick': {'name': 's1', 'kind': 'claude', 'generation': 1, 'pane_id': 'p1', 'bootstrap_pending': True}}
        hook['a live sidekick whose bootstrap is pending'] = {**hook['a plain spawn'], 'args': ['spawn', '{st}', '--kind', 'claude'], 'pair': side, 'agents': {'s1': 'idle claude'},
                                                              'hook': None}
        hook['a folder-trust dialog'] = {**hook['a plain spawn'], 'fake': {'trust-dialog': ''}}
        hook['a split that returns no pane'] = {**hook['a plain spawn'], 'fake': {'split-null': ''}}
        hook['a tab that returns no pane'] = {**hook['--tab'], 'fake': {'tab-null': ''}}
        self.check_port(hook, {
            'a pi preflight that fails with no fallback': (2, 'no fallback recorded for a failed pi; pass --fallback KIND or fix the agent'),
            'a bootstrap that returns 4': (4, 'sidekick settled but reports/000-ready.md is missing; state=idle'),
            'a bootstrap that returns 3': (3, 'sidekick bootstrap did not settle'),
            'a live sidekick whose bootstrap is pending': (4, 'sidekick bootstrap still pending: reports/000-ready.md is missing'),
            'a folder-trust dialog': (5, 'asks whether to trust <T>/repo, and that decision is yours: open claude there once and trust the folder (or pick another kind), then rerun the spawn'),
            'a split that returns no pane': (2, 'pane split returned no pane id'),
            'a tab that returns no pane': (2, 'tab create returned no pane id'),
        })

    def test_herdr_replies_are_read_as_plain_json_where_wait_decides_on_an_error_code_or_an_agent_status(self):
        cases = {
            'an error with a code': ('{"error":{"code":"timeout"}}', 'timeout', 'timeout'),
            'an error with no code': ('{"error":{"message":"x"}}', 'herdr_error', ''),
            'an error that is a string': ('{"error":"boom"}', 'herdr_error', ''),
            'a reply that is a string': ('"x"', 'herdr_error', ''),
            'a reply that is null': ('null', 'herdr_error', ''),
            'a reply with no error': ('{"result":{}}', 'herdr_error', ''),
            'text that is no JSON': ('not json', 'herdr_error', ''),
            'a false code': ('{"error":{"code":false}}', 'herdr_error', ''),
        }
        for name, (text, state, code) in cases.items():
            self.assertEqual((panes.error_state(text), panes.error_code(text)), (state, code), name)
        statuses = {
            'a status': ('{"result":{"agent":{"agent_status":"idle"}}}', 'idle', 'idle'),
            'no status': ('{"result":{"agent":{}}}', 'settled', ''),
            'a null agent': ('{"result":{"agent":null}}', 'settled', ''),
            'an agent that is a string': ('{"result":{"agent":"x"}}', 'settled', ''),
            'text that is no JSON': ('oops', 'settled', ''),
        }
        for name, (text, with_default, without) in statuses.items():
            self.assertEqual((panes.agent_state(text, 'settled'), panes.agent_state(text, None)), (with_default, without), name)

    def test_a_prime_steer_receipt_confirms_the_steer_lane_and_says_why_it_cannot_be_judged(self):
        from pstack_core import prime
        good = {'deliveryMode': 'steer', 'target': {'activeSessionId': 'live-1'}, 'deliveryStatus': 'queued'}
        cases = {
            'a queued steer to the session': (json.dumps(good), True, ''),
            'a delivered steer to the session': (json.dumps({**good, 'deliveryStatus': 'delivered'}), True, ''),
            'a steer to another session': (json.dumps({**good, 'target': {'activeSessionId': 'live-2'}}), False, ''),
            'a send that is not a steer': (json.dumps({**good, 'deliveryMode': 'send'}), False, ''),
            'a steer that failed': (json.dumps({**good, 'deliveryStatus': 'failed'}), False, ''),
            'a receipt with no target': (json.dumps({'deliveryMode': 'steer', 'deliveryStatus': 'queued'}), False, ''),
            'a receipt that is not JSON': ('queued', False, 'Prime steer receipt is not JSON\n'),
            'a receipt that is empty': ('', False, 'Prime steer receipt is empty\n'),
            'a receipt that is an array': ('[1]', False, 'Prime steer receipt is not an object\n'),
            'a target that is a string': (json.dumps({**good, 'target': 'live-1'}), False, 'Prime steer receipt has a target that is not an object\n'),
            'a good value then text that is no JSON': (json.dumps(good) + '\nqueued', False, 'Prime steer receipt is not JSON\n'),
        }
        for name, (receipt, want, said) in cases.items():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                got = prime.steer_confirmed(receipt, 'live-1')
            self.assertEqual((got, err.getvalue()), (want, said), name)

    def test_a_prime_session_list_that_is_not_JSON_says_so(self):
        from pstack_core import prime
        st = self.claim_store('primelist', {'verdict': 'v'}, {'sidekick': {'name': 's1'}})
        for out, want, said in (('not json', (True, None), 'Prime session list for sidekick is not JSON\n'), ('{"sessions": []}', (False, []), ''),
                                ('{"sessions": 3}', (True, None), ''), ('[]', (True, None), '')):
            err = io.StringIO()
            with mock.patch.object(prime, 'prime_command', return_value=(0, out)), contextlib.redirect_stderr(err):
                got = prime.prime_sessions(str(st), 'sidekick')
            self.assertEqual((got, err.getvalue()), (want, said), out)

    def test_a_recorded_prime_session_that_is_not_an_object_is_unknown_and_says_so(self):
        for n, session in enumerate(('sess-1', ['sess-1'], 3, True)):
            st = self.claim_store(f'primesession{n}', {'verdict': 'v'}, {'verifier': {'name': 'v1', 'kind': 'prime-agent', 'session': session}})
            rc, out, err = self.kitchen_py(st, 'from pstack_core import prime\nprint(repr(prime.prime_active_session(store, "verifier")))')
            self.assertEqual((rc, out, err), (0, 'None\n', 'recorded Prime session for verifier is not an object; inspect its pane before cleanup\n'), session)

    def test_a_prime_sidekick_that_reports_no_session_ends_spawn_with_5_and_keeps_its_pane_and_launch_record(self):
        spec = {**self.spawn_command_cases()['a plain spawn'], 'args': ['spawn', '{st}', '--kind', 'prime-agent'], 'fake': {'prime-no-session': ''}}
        got = self.script_run('primenosession', root / 'skills', 'pstack-pair', spec)
        self.assertEqual(got['rc'], 5, got['stderr'])
        self.assertIn('Prime did not report its session; inspect s1 before prompting', got['stderr'])
        sidekick = json.loads(got['files']['pair.json'])['sidekick']
        self.assertEqual((sidekick['kind'], sidekick['session'], sidekick['session_stopped'], sidekick['closed']), ('prime-agent', None, False, False))
        self.assertTrue(sidekick['launch_cwd'])
        self.assertNotIn('pane close', got['calls'])

    def test_trio_spawn_and_discuss_report_the_state_they_stop_on(self):
        trio = self.trio_spawn_cases()
        self.check_port(trio, {
            'all three kinds equal': (8, 'pstack-trio needs at least two distinct kinds (master claude, sidekick claude, consultant claude)'),
            'an unreadable master kind': (2, "cannot read the master's agent kind from herdr; run pair.sh init first"),
            'a consultant that dies with 5': (5, 'devin task sessions start fresh; recover partial work from the store instead of passing'),
        }, 'pstack-trio')
        discuss = self.discuss_cases()
        self.check_port(discuss, {
            'an escalated plan with no consultant': (2, 'consultant is not live; run: pair.sh spawn <T>/st --only consultant --consultant <kind>'),
            'a consultant that is blocked': (3, 'consultant c1 is blocked; inspect: herdr agent read c1 --source visible --lines 60'),
            'a consultant that is working': (5, 'consultant c1 is working; wait for it before a plan round'),
            'a prompt that fails': (2, 'herdr prompt failed for s1'),
            # a consultant whose last advice for the plan was its stop is asked again, and its new advice is the plan's answer
            'an escalated plan whose consultant only left a stop': (0, 'sent PLAN 001-x.md to consultant c1\nsidekick_state: idle\nsidekick_response: <T>/st/reports/001-x.md\nsidekick_status: agree\nconsultant_state: idle\nconsultant_response: <T>/st/advice/001-x.md\nconsultant_status: agree\n'),
            'a stop named for it with no kind line': (0, 'sent PLAN 001-x.md to consultant c1\nsidekick_state: idle\nsidekick_response: <T>/st/reports/001-x.md\nsidekick_status: agree\nconsultant_state: idle\nconsultant_response: <T>/st/advice/001-x.md\nconsultant_status: agree\n'),
            'a stop and an answer': (4, 'consultant_state: answered\nconsultant_response: <T>/st/advice/001-x.md\nconsultant_status: agree\n'),
        }, 'pstack-trio')

    def test_consult_and_advice_report_what_the_consultant_is_doing(self):
        consultant = {'consultant': {'name': 'c1', 'kind': 'codex', 'pane_id': 'p2'}}
        consult = '# C\n\nkind: finding\nsidekick state at send: x\n\n## Question\nq\n'
        run = ['consult', '{st}', '{st}/consults/001-x-c1.md', '--timeout', '3000']
        idle = {'s1': 'idle claude', 'c1': 'idle codex'}
        base = dict(files={'consults/001-x-c1.md': consult}, pair=consultant, agents=idle, env={'PAIR_FILE_POLL_MS': '1'})
        blocks = f'#!/bin/sh\nif [ "$1 $2" = "agent prompt" ]; then printf \'{{"result":{{"agent":{{"agent_status":"blocked"}}}}}}\\n\'; exit 0; fi\nexec {root / "tests/pstack-pair/bin/herdr"} "$@"\n'
        cases = {
            'no advice': dict(base, args=run),
            'a blocked consultant that never advises': dict(base, args=run, topfiles={'blocks/herdr': (blocks, 0o755)}, envfill={'PATH': '{T}/blocks:' + self.env['PATH']}),
            'an absent consultant': dict(base, args=run, agents={'s1': 'idle claude'}),
            'a consultant that is blocked': dict(base, args=run, agents={**idle, 'c1': 'blocked codex'}),
            'a consultant that is working': dict(base, args=run, agents={**idle, 'c1': 'working codex'}),
            'a blocked consultant that advised': dict(base, args=run, topfiles={'blocks/herdr': (blocks, 0o755)}, envfill={'PATH': '{T}/blocks:' + self.env['PATH']},
                                                      files={'consults/001-x-c1.md': consult, 'advice/001-x-c1.md': '# A\n\nstatus: answered\n'}),
            'a second glance': dict(base, args=['new-consult', '{st}', '001', '--kind', 'glance'], files={'consults/001-x-c1.md': '# C\n\nkind: glance\n'}),
            'a fourth consult': dict(base, args=['new-consult', '{st}', '001', '--kind', 'finding'], files={f'consults/001-x-c{k}.md': '# C\n\nkind: finding\n' for k in (1, 2, 3)}),
            'advice with none on file': dict(base, args=['advice', '{st}']),
        }
        self.check_port(cases, {
            'no advice': (4, 'advice: missing'),
            'a blocked consultant that never advises': (3, 'advice: missing'),
            'an absent consultant': (2, 'consultant c1 is not live; run: pair.sh spawn <T>/st --only consultant --consultant <kind>'),
            'a blocked consultant that advised': (3, 'advice_status: answered'),
            'a consultant that is blocked': (3, 'consultant c1 is blocked; inspect: herdr agent read c1 --source visible --lines 60'),
            'a consultant that is working': (5, 'consultant c1 is working; wait for its current advice first'),
            'a second glance': (7, 'unit 001 already had its glance; send a finding consult, or pass --force'),
            'a fourth consult': (7, 'unit 001 already has three consults; take the question to a plan round, or pass --force'),
            'advice with none on file': (4, 'no advice found'),
        }, 'pstack-trio')

    def test_a_trio_plan_round_and_the_consult_commands_run_with_no_jq(self):
        """The consultant's commands are Python: with a jq on PATH that fails, pair.sh discuss, new-consult, consult and advice still end as they do with one."""
        no_jq = dict(topfiles={'nojq/jq': ('#!/bin/sh\necho "jq was started" >&2\nexit 127\n', 0o755)}, envfill={'PATH': '{T}/nojq:' + self.env['PATH']})
        consultant = {'consultant': {'name': 'c1', 'kind': 'codex', 'pane_id': 'p2'}}
        consult = '# C\n\nkind: finding\nsidekick state at send: x\n\n## Question\nq\n'
        advised = dict(files={'consults/001-x-c1.md': consult, 'advice/001-x-c1.md': '# A\n\nstatus: answered\n'}, pair=consultant,
                       agents={'s1': 'idle claude', 'c1': 'idle codex'}, env={'PAIR_FILE_POLL_MS': '1'}, **no_jq)
        cases = {
            'a plan round': (dict(self.discuss_cases()['an escalated plan both roles answer'], **no_jq), 0, 'consultant_status: agree'),
            'a new consult': (dict(advised, args=['new-consult', '{st}', '001', '--kind', 'finding'], files={'briefs/001-x.md': '# B\n'}), 0,
                              '<T>/st/consults/001-x-c1.md'),
            'a consult': (dict(advised, args=['consult', '{st}', '{st}/consults/001-x-c1.md', '--timeout', '3000']), 0, 'advice_status: answered'),
            'advice': (dict(advised, args=['advice', '{st}']), 0, '<T>/st/advice/001-x-c1.md'),
        }
        for label, (spec, rc, line) in cases.items():
            got = self.script_run(f'nojq-{label.replace(" ", "-")}', root / 'skills', 'pstack-trio', spec)
            self.assertEqual((got['rc'], got['stderr']), (rc, ''), label)
            self.assertIn(line, got['stdout'], label)

    def test_a_plan_needs_the_approval_and_advice_its_variant_asks_for(self):
        def world(plan, review, **kw):
            brief = '# B\n\nplaybook: feature\nplan: {st}/plans/001-x.md\n'
            return dict(args=['dispatch', '{st}', '{st}/briefs/003-z.md'], agents={'s1': 'idle claude', 'm1': 'idle claude'}, **kw,
                        files={'plans/001-x.md': plan, 'reviews/001-x.md': review, 'briefs/003-z.md': brief})
        guided = {
            'a large plan posted but not approved': world('# Plan 001-x\n\nscale: large\n', '# R\n\nverdict: agreed\napproval: posted\n'),
            'a medium plan with no approval': world('# Plan 001-x\n\nscale: medium\n', '# R\n\nverdict: agreed\napproval: none\n'),
            'a plan with an unknown scale': world('# Plan 001-x\n\nscale: bogus\n', '# R\n\nverdict: agreed\n'),
            'a third ask': dict(args=['new-answer', '{st}', '001'], files={'reports/001-x-q3.md': '# R\n\nstatus: asking\n'}),
        }
        self.check_port(guided, {
            'a large plan posted but not approved': (6, "large plan needs approval: human in <T>/st/reviews/001-x.md (found 'posted'); post the direction summary and wait for the human"),
            'a medium plan with no approval': (6, "medium plan needs approval: posted or human in <T>/st/reviews/001-x.md (found 'none')"),
            'a plan with an unknown scale': (6, "plan <T>/st/plans/001-x.md has scale 'bogus'; set small, medium, or large"),
            'a third ask': (7, 'ask 3 exceeds the two-ask cap for unit 001; re-plan the unit, or pass --force to answer anyway'),
        }, 'pstack-pair-guided')
        consultant = {'consultant': {'name': 'c1', 'kind': 'codex', 'pane_id': 'p2'}}
        stopped = world('# Plan 001-x\n', '# R\n\nverdict: agreed\n', pair=consultant)
        stopped['files']['advice/001-x-stop.md'] = '# A\n\nkind: stop\n'
        trio = {'a plan with no consultant advice': world('# Plan 001-x\n', '# R\n\nverdict: agreed\n', pair=consultant),
                'a plan whose only advice is the consultant leaving': stopped}
        self.check_port(trio, {
            'a plan whose only advice is the consultant leaving': (6, 'has no consultant advice under advice/001-*; run pair.sh discuss so both agents respond'),
            'a plan with no consultant advice': (6, 'plan <T>/st/plans/001-x.md has no consultant advice under advice/001-*; run pair.sh discuss so both agents respond'),
        }, 'pstack-trio')


class GuidedCommandTests(unittest.TestCase):
    """new-direction and new-answer through the real guided pair.sh."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        self.store = self.dir / 'st'
        for d in ('plans', 'reports', 'answers'):
            (self.store / d).mkdir(parents=True)
        (self.store / 'pair.json').write_text('{}')
        self.refs = root / 'skills/pstack-pair-guided/references'

    def run_guided(self, *args):
        env = dict(inherited_env(), XDG_STATE_HOME=str(self.dir / 'state'))
        return subprocess.run([str(root / 'skills/pstack-pair-guided/scripts/pair.sh'), *args], capture_output=True, text=True, env=env)

    def test_new_direction_fills_the_template_and_a_second_call_prints_the_same_path(self):
        plan = self.store / 'plans/001-x.md'
        plan.write_text('# Plan 001-x\n\nscale: medium\n')
        got = self.run_guided('new-direction', str(self.store), str(plan))
        path = self.store / 'plans/001-x.direction.md'
        self.assertEqual((got.returncode, got.stdout, got.stderr), (0, f'{path}\n', ''))
        want = (self.refs / 'direction-template.md').read_text()
        for token, value in (('{{SLUG}}', 'x'), ('{{PLAN}}', str(plan)), ('{{STORE}}', str(self.store))):
            want = want.replace(token, value)
        want = re.sub(r'(?m)^scale: .*', 'scale: medium', want)
        self.assertEqual(path.read_text(), want)
        path.write_text('edited\n')
        again = self.run_guided('new-direction', str(self.store), str(plan))
        self.assertEqual((again.returncode, again.stdout, path.read_text()), (0, f'{path}\n', 'edited\n'))

    def test_new_direction_without_a_scale_keeps_the_placeholder_and_a_missing_plan_dies(self):
        plan = self.store / 'plans/002-y.md'
        plan.write_text('# Plan 002-y\n')
        self.assertEqual(self.run_guided('new-direction', str(self.store), str(plan)).returncode, 0)
        self.assertIn('scale: {{small | medium | large}}\n', (self.store / 'plans/002-y.direction.md').read_text())
        got = self.run_guided('new-direction', str(self.store), str(self.store / 'plans/009-z.md'))
        self.assertEqual((got.returncode, got.stderr), (1, f'pair.sh: plan not found: {self.store}/plans/009-z.md\n'))

    def test_new_answer_fills_the_template_for_the_newest_ask_and_is_idempotent(self):
        (self.store / 'reports/001-x-q1.md').write_text('# R\n\nstatus: asking\n')
        got = self.run_guided('new-answer', str(self.store), '001')
        path = self.store / 'answers/001-x-a1.md'
        self.assertEqual((got.returncode, got.stdout, got.stderr), (0, f'{path}\n', ''))
        want = (self.refs / 'answer-template.md').read_text()
        for token, value in (('NNN-<k>', '001-1'), ('NNN', '001'), ('<slug>', 'x'), ('<k>', '1'), ('<store>', str(self.store))):
            want = want.replace(token, value)
        self.assertEqual(path.read_text(), want)
        path.write_text('edited\n')
        again = self.run_guided('new-answer', str(self.store), '001')
        self.assertEqual((again.returncode, again.stdout, path.read_text()), (0, f'{path}\n', 'edited\n'))

    def test_new_answer_answers_the_newest_of_several_asks(self):
        old, new = self.store / 'reports/001-x-q1.md', self.store / 'reports/001-x-q2.md'
        for f in (old, new):
            f.write_text('# R\n\nstatus: asking\n')
        os.utime(old, (2000, 2000))
        os.utime(new, (1000, 1000))
        self.assertEqual(self.run_guided('new-answer', str(self.store), '001').stdout, f'{self.store}/answers/001-x-a1.md\n')

    def test_new_answer_refuses_an_ask_that_is_not_asking_and_a_third_ask_unless_forced(self):
        (self.store / 'reports/001-x-q1.md').write_text('# R\n\nstatus: done\n')
        got = self.run_guided('new-answer', str(self.store), '001')
        self.assertEqual((got.returncode, got.stderr), (1, f'pair.sh: {self.store}/reports/001-x-q1.md is not status: asking\n'))
        none = self.run_guided('new-answer', str(self.store), '007')
        self.assertEqual((none.returncode, none.stderr), (1, 'pair.sh: no ask report for unit 007 (expected reports/007-<slug>-q<k>.md)\n'))
        (self.store / 'reports/002-w-q3.md').write_text('# R\n\nstatus: asking\n')
        capped = self.run_guided('new-answer', str(self.store), '002')
        self.assertEqual((capped.returncode, capped.stderr),
                         (7, 'pair.sh: ask 3 exceeds the two-ask cap for unit 002; re-plan the unit, or pass --force to answer anyway\n'))
        forced = self.run_guided('new-answer', str(self.store), '002', '--force')
        self.assertEqual((forced.returncode, forced.stdout), (0, f'{self.store}/answers/002-w-a3.md\n'))


class ShellCallSiteTests(unittest.TestCase):
    """Where the four pair skills' Python starts a shell."""

    SKILLS = ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen')
    SHELLS = ('bash', 'sh')
    # kitchen.py runs the repo's own commands, which are shell text by contract; no module under pstack_core, kitchen_core or a variant.py is here.
    ALLOWED = {
        ('pstack-kitchen/scripts/kitchen.py', 'run_wrapped'): "a profile's gate command line, run by the repo's shell",
        ('pstack-kitchen/scripts/kitchen.py', 'run_setup'): "a profile's setup command, shell text from the repo's kitchen.toml",
        ('pstack-kitchen/scripts/kitchen.py', 'run_command'): "a brief's Verify command, shell text the brief wrote",
        ('pstack-kitchen/scripts/kitchen.py', '_complete'): 'bash -n, the syntax check that splits a Verify block into whole commands',
        ('pstack-kitchen/scripts/kitchen.py', 'run_brief_verify'): "the brief's Verify commands under the profile's wrapper",
        ('pstack-kitchen/scripts/kitchen.py', 'shell_builtins'): 'compgen, the list of shell builtins and keywords',
        ('pstack-kitchen/scripts/kitchen.py', 'doctor'): "doctor's probe of the repo's wrapper command",
    }

    def sites(self):
        """(path relative to skills/, function, line, call) for each shell start in the four skills' scripts."""
        found = []
        for skill in self.SKILLS:
            for path in sorted((root / 'skills' / skill / 'scripts').rglob('*.py')):
                tree = ast.parse(path.read_text(), str(path))
                rel = str(path.relative_to(root / 'skills'))

                def visit(node, function):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        function = node.name
                    if isinstance(node, ast.Call):
                        shell = any(k.arg == 'shell' and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords)
                        first = node.args[0] if node.args else None
                        argv_shell = (isinstance(first, (ast.List, ast.Tuple)) and first.elts and isinstance(first.elts[0], ast.Constant)
                                      and os.path.basename(str(first.elts[0].value)) in self.SHELLS)
                        if shell or argv_shell or ast.unparse(node.func) in ('os.system', 'os.popen'):
                            found.append((rel, function, node.lineno, ast.unparse(node)[:100]))
                    for child in ast.iter_child_nodes(node):
                        visit(child, function)
                visit(tree, '<module>')
        return found

    def test_only_the_kitchen_py_sites_that_run_the_repos_own_commands_start_a_shell(self):
        stray = [f'{rel}:{line} in {function}: {call}' for rel, function, line, call in self.sites() if (rel, function) not in self.ALLOWED]
        self.assertEqual(stray, [])
        self.assertEqual({(rel, function) for rel, function, _, _ in self.sites()}, set(self.ALLOWED), 'an allow-list entry names no shell start')


class HerdrCallTests(unittest.TestCase):
    """herdr is run from panes.py only; the argv it sends is fixed here."""

    def sent(self, call, **kwargs):
        with mock.patch.object(panes.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'out', 'err')) as run:
            call()
        argv, streams = run.call_args.args[0], run.call_args.kwargs
        return argv, {k: streams[k] for k in ('stdout', 'stderr')}

    def test_agent_prompt_sends_the_flags_it_was_given_in_the_order_herdr_was_always_called_with(self):
        self.assertEqual(self.sent(lambda: panes.agent_prompt('a', 'hi'))[0], ['herdr', 'agent', 'prompt', 'a', 'hi'])
        self.assertEqual(self.sent(lambda: panes.agent_prompt('a', 'hi', wait=True, timeout_ms=240000))[0],
                         ['herdr', 'agent', 'prompt', 'a', 'hi', '--wait', '--timeout', '240000'])
        self.assertEqual(self.sent(lambda: panes.agent_prompt('a', 'hi', wait=True, until='working', timeout_ms=30000))[0],
                         ['herdr', 'agent', 'prompt', 'a', 'hi', '--wait', '--until', 'working', '--timeout', '30000'])

    def test_agent_start_sends_the_kind_pane_timeout_and_the_agents_own_arguments_after_a_double_dash(self):
        argv, streams = self.sent(lambda: panes.agent_start('a', 'pi', 'w1:p2', ['--model', 'm'], '5000'))
        self.assertEqual(argv, ['herdr', 'agent', 'start', 'a', '--kind', 'pi', '--pane', 'w1:p2', '--timeout', '5000', '--', '--model', 'm'])
        self.assertEqual(streams, {'stdout': subprocess.DEVNULL, 'stderr': subprocess.PIPE})
        self.assertEqual(self.sent(lambda: panes.agent_start('a', 'pi', 'p', []))[0][8:], ['--timeout', '60000', '--'])

    def test_the_streams_are_captured_dropped_merged_or_left_on_the_terminal_as_the_caller_says(self):
        self.assertEqual(self.sent(lambda: panes.herdr('x'))[1], {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE})
        self.assertEqual(self.sent(lambda: panes.herdr_ok('x'))[1], {'stdout': subprocess.DEVNULL, 'stderr': None})
        self.assertEqual(self.sent(lambda: panes.herdr_json('x'))[1], {'stdout': subprocess.PIPE, 'stderr': None})
        self.assertEqual(self.sent(lambda: panes.agent_prompt('a', 't', stderr=panes.MERGED))[1], {'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT})

    def test_require_herdr_says_the_default_or_the_callers_message_outside_herdr(self):
        with mock.patch.dict(os.environ, {'HERDR_ENV': '0'}):
            with self.assertRaises(NotInHerdr) as caught:
                panes.require_herdr()
            self.assertEqual(str(caught.exception), 'not running inside Herdr (HERDR_ENV != 1)')
            with self.assertRaises(NotInHerdr) as caught:
                panes.require_herdr('maintainer on needs Herdr (HERDR_ENV=1)')
            self.assertEqual(str(caught.exception), 'maintainer on needs Herdr (HERDR_ENV=1)')
        with mock.patch.dict(os.environ, {'HERDR_ENV': '1'}):
            panes.require_herdr()

    def test_until_without_wait_is_refused_rather_than_sent(self):
        with mock.patch.object(panes.subprocess, 'run') as run, self.assertRaises(ValueError):
            panes.agent_prompt('a', 'hi', until='working')
        run.assert_not_called()

    @staticmethod
    def spells_agent_start_or_prompt(node):
        """A call herdr('agent', 'prompt'|'start', ...) or a list ['agent', 'prompt'|'start', ...] (with or without a leading 'herdr')."""
        if isinstance(node, ast.Call):
            parts = node.args
        elif isinstance(node, ast.List):
            parts = node.elts
        else:
            return False
        words = [p.value for p in parts[:3] if isinstance(p, ast.Constant)]
        return words[:2] in (['agent', 'prompt'], ['agent', 'start']) or words[:3] in (['herdr', 'agent', 'prompt'], ['herdr', 'agent', 'start'])

    def test_the_guard_sees_every_form_the_old_sites_used(self):
        for source in ("herdr('agent', 'prompt', n, t)", "run_herdr(['agent', 'start', n])", "['herdr', 'agent', 'prompt', n]", "subprocess.run(['herdr', 'agent', 'start'])"):
            self.assertTrue(any(self.spells_agent_start_or_prompt(n) for n in ast.walk(ast.parse(source))), source)
        for source in ("herdr('agent', 'get', n)", "['agent', 'wait', n]", "agent_prompt(n, t)"):
            self.assertFalse(any(self.spells_agent_start_or_prompt(n) for n in ast.walk(ast.parse(source))), source)

    def test_no_module_but_panes_builds_an_agent_start_or_prompt_argv(self):
        stray = []
        for skill in ('pstack-pair/scripts/pstack_core', 'pstack-kitchen/scripts/kitchen_core', 'pstack-pair-guided/scripts', 'pstack-trio/scripts', 'pstack-kitchen/scripts'):
            for path in sorted((root / 'skills' / skill).glob('*.py' if skill.endswith('/scripts') else '**/*.py')):
                if path.name in ('panes.py', 'kitchen.py'):
                    continue
                stray += [f'{path.relative_to(root)}:{node.lineno}' for node in ast.walk(ast.parse(path.read_text(), str(path))) if self.spells_agent_start_or_prompt(node)]
        self.assertEqual(stray, [])


class ReportReadyTests(unittest.TestCase):
    def test_a_done_report_is_withheld_when_the_steps_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(f'{tmp}/reports')
            os.makedirs(f'{tmp}/steps/001-x.tsv')
            report = f'{tmp}/reports/001-x.md'
            Path(report).write_text('# Report 001: x\n\nstatus: done\nhead: abcdef0\n')
            ready = Variant().report_ready(None, tmp, report)
        self.assertFalse(ready.ready)
        self.assertIn('the steps or notes cannot be read', ready.why)
        self.assertIn('steps/001-x.tsv', ready.why)

    def test_a_unit_with_no_steps_file_is_judged_on_notes_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(f'{tmp}/reports')
            report = f'{tmp}/reports/001-x.md'
            Path(report).write_text('# Report 001: x\n\nstatus: done\nhead: abcdef0\n')
            self.assertTrue(Variant().report_ready(None, tmp, report).ready)


class GitCallTests(unittest.TestCase):
    """git is run from git.py only; the argv and the streams each caller asked for are fixed here."""

    def sent(self, call):
        with mock.patch.object(git.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'out\n', '')) as run:
            result = call()
        return run.call_args.args[0], run.call_args.kwargs, result

    def test_a_root_becomes_dash_c_and_none_runs_git_where_it_stands(self):
        self.assertEqual(self.sent(lambda: git.run('/r', 'status'))[0], ['git', '-C', '/r', 'status'])
        self.assertEqual(self.sent(lambda: git.run(None, 'patch-id', '--stable', stdin='d'))[0], ['git', 'patch-id', '--stable'])

    def test_stderr_stays_on_the_terminal_unless_quiet_or_redirected(self):
        self.assertIsNone(self.sent(lambda: git.run('/r', 'x'))[1]['stderr'])
        self.assertEqual(self.sent(lambda: git.run('/r', 'x', quiet=True))[1]['stderr'], subprocess.DEVNULL)
        self.assertEqual(self.sent(lambda: git.run('/r', 'x', stderr=git.PIPE))[1]['stderr'], subprocess.PIPE)
        self.assertEqual(self.sent(lambda: git.ok('/r', 'x'))[1]['stderr'], subprocess.DEVNULL)

    def test_out_is_the_stdout_without_its_newline_and_none_when_git_fails(self):
        self.assertEqual(self.sent(lambda: git.out('/r', 'rev-parse', 'HEAD'))[2], 'out')
        with mock.patch.object(git.subprocess, 'run', return_value=subprocess.CompletedProcess([], 128, 'junk', '')):
            self.assertIsNone(git.out('/r', 'rev-parse', 'HEAD'))
            self.assertFalse(git.ok('/r', 'rev-parse', 'HEAD'))
            self.assertEqual(git.toplevel('/r'), '')

    def test_binary_leaves_the_input_and_the_output_as_bytes(self):
        argv, kwargs, _ = self.sent(lambda: git.run('/r', 'apply', stdin=b'd', binary=True))
        self.assertEqual(kwargs['input'], b'd')
        self.assertNotIn('text', kwargs)
        self.assertTrue(self.sent(lambda: git.run('/r', 'x'))[1]['text'])

    def test_toplevel_is_empty_when_git_is_missing(self):
        with mock.patch.object(git.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertEqual(git.toplevel(), '')

    def test_no_module_but_git_py_builds_a_git_c_argv(self):
        stray = []
        for skill in ('pstack-pair/scripts/pstack_core', 'pstack-kitchen/scripts/kitchen_core', 'pstack-pair-guided/scripts', 'pstack-trio/scripts'):
            for path in sorted((root / 'skills' / skill).glob('*.py' if skill.endswith('/scripts') else '**/*.py')):
                if path.name == 'git.py':
                    continue
                for node in ast.walk(ast.parse(path.read_text(), str(path))):
                    if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == 'git':
                        stray.append(f'{path.relative_to(root)}:{node.lineno}')
        self.assertEqual(stray, [])


class StepLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(f'{self.tmp.name}/steps')
        self.log = steplog.StepLog(self.tmp.name, '/x/briefs/001-a.md')

    def test_a_missing_or_empty_file_has_no_steps(self):
        self.assertEqual((self.log.rows(), self.log.last(), self.log.at(1), self.log.has('abc')), ([], None, None, False))
        Path(self.log.path).write_text('\n')
        self.assertEqual(self.log.rows(), [])

    def test_a_step_is_one_tab_separated_row_with_its_summary_on_one_line(self):
        self.assertEqual(self.log.next_number(), 1)
        step = self.log.append(1, 'abc', 1700000000, 'n1,n2', 'did\tit\nnow')
        self.assertEqual(Path(self.log.path).read_text(), '1\tabc\t1700000000\tn1,n2\tdid it now\n')
        self.assertEqual(step, steplog.Step('1', 'abc', 1700000000, ('n1', 'n2'), 'did it now'))
        self.assertEqual(self.log.next_number(), 2)
        self.log.append(2, 'def', 1700000001, '', 'two')
        self.assertEqual((self.log.last().sha, self.log.at(1).sha, self.log.has('def'), self.log.at(3)), ('def', 'abc', True, None))

    def test_a_row_that_is_a_bare_sha_names_that_sha_and_no_number(self):
        Path(self.log.path).write_text('abc123\n')
        self.assertEqual(self.log.last(), steplog.Step('abc123', 'abc123', None, (), ''))

    def test_a_file_with_only_blank_lines_ends_the_next_number_with_1_and_an_unreadable_one_with_2(self):
        Path(self.log.path).write_text('\n\n')
        with self.assertRaises(EmptyStepLog) as caught:
            self.log.next_number()
        self.assertEqual(translate(caught.exception, 'pair.sh'), ('', 1))
        with mock.patch.object(steplog, 'read_text', side_effect=PermissionError(13, 'Permission denied')), self.assertRaises(StepLogUnreadable) as caught:
            self.log.next_number()
        self.assertEqual(translate(caught.exception, 'pair.sh'), (f'pair.sh: cannot read {self.log.path}: Permission denied\n', 2))

    def test_resolves_join_the_row_of_a_recorded_commit_in_note_order_once(self):
        self.log.append(1, 'abc', 5, 'n10', 'one')
        self.log.append(2, 'def', 6, '', 'two')
        self.assertTrue(self.log.add_resolves('abc', 'n2,n10,001-n3'))
        self.assertEqual(Path(self.log.path).read_text().split('\n')[0], '1\tabc\t5\tn2,n10,001-n3\tone')
        self.assertTrue(self.log.add_resolves('def', ''))
        self.assertFalse(self.log.add_resolves('zzz', 'n1'))
        self.assertEqual(Path(self.log.path).read_text().count('\n'), 2)

    def test_all_lists_each_units_log_by_name(self):
        steplog.StepLog(self.tmp.name, '002-b').append(1, 'x', 1, 'n1', 's')
        self.log.append(1, 'y', 1, '', 's')
        self.assertEqual([(l.unit, [s.sha for s in l.rows()]) for l in steplog.StepLog.all(self.tmp.name)], [('001-a', ['y']), ('002-b', ['x'])])

    def test_no_module_but_steplog_names_a_steps_tsv(self):
        stray = []
        for skill in ('pstack-pair/scripts/pstack_core', 'pstack-kitchen/scripts/kitchen_core', 'pstack-pair-guided/scripts', 'pstack-trio/scripts', 'pstack-kitchen/scripts'):
            for path in sorted((root / 'skills' / skill).glob('*.py' if skill.endswith('/scripts') else '**/*.py')):
                if path.name == 'steplog.py':
                    continue
                for node in ast.walk(ast.parse(path.read_text(), str(path))):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r'steps.*\.tsv', node.value):
                        stray.append(f'{path.relative_to(root)}:{node.lineno}')
        self.assertEqual(stray, [])


class FillTemplateTests(unittest.TestCase):
    def test_a_braced_token_is_replaced_everywhere_in_the_order_given_and_a_bare_one_as_it_stands(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f'{tmp}/t.md'
            Path(path).write_text('{{A}} {{B}} {{A}} NNN-<k> NNN <k>\n')
            self.assertEqual(store.fill_template(path, {'A': '{{B}}', 'B': 'x'}), 'x x x NNN-<k> NNN <k>\n')
            self.assertEqual(store.fill_template(path, {'NNN-<k>': '001-2', 'NNN': '001', '<k>': '2'}, braced=False), '{{A}} {{B}} {{A}} 001-2 001 2\n')

    def test_the_handoff_and_the_guided_direction_and_answer_fill_through_it(self):
        for rel in ('pstack-kitchen/scripts/kitchen_core/handoff.py', 'pstack-pair-guided/scripts/variant.py'):
            source = (root / 'skills' / rel).read_text()
            self.assertIn('fill_template(', source)
            self.assertNotIn("text.replace(", source)


class ArgvReaderTests(unittest.TestCase):
    def test_valued_flags_take_the_next_token_whatever_it_looks_like_and_keep_their_order(self):
        spec = argv.Spec(valued=('--fallback-arg', '--kind'), flags=('--tab',))
        got = argv.read(['--kind', 'pi', '--fallback-arg', '--x', '--tab', '--fallback-arg', 'y', '--kind', 'codex'], spec)
        self.assertEqual(got.occurrences, (('--kind', 'pi'), ('--fallback-arg', '--x'), ('--tab', None), ('--fallback-arg', 'y'), ('--kind', 'codex')))
        self.assertEqual(got.all('--fallback-arg'), ['--x', 'y'])
        self.assertEqual((got.last('--kind'), got.last('--tab'), got.last('--missing', 'd')), ('codex', True, 'd'))
        self.assertTrue(got.present('--tab'))
        self.assertFalse(got.present('--missing'))

    def test_a_valued_flag_with_no_value_and_an_unknown_token_are_the_two_lexical_refusals(self):
        spec = argv.Spec(valued=('--timeout',))
        with self.assertRaises(Die) as caught:
            argv.read(['--timeout'], spec)
        self.assertEqual((caught.exception.code, caught.exception.message), (1, '--timeout needs a value'))
        with self.assertRaises(Die) as caught:
            argv.read(['--timeout', '1', '--bogus', '--timeout'], spec)
        self.assertEqual((caught.exception.code, caught.exception.message), (1, 'unknown option --bogus'))
        self.assertEqual(argv.read([], spec).occurrences, ())

    def test_collected_unknown_tokens_keep_their_order_and_can_be_refused_afterwards(self):
        spec = argv.Spec(valued=('--max',), unknown='collect')
        got = argv.read(['--timeout', '5', '--max', '9', 'x'], spec)
        self.assertEqual((got.leftovers, got.all('--max')), (('--timeout', '5', 'x'), ['9']))
        self.assertEqual(got.stripped('--max'), ['--timeout', '5', 'x'])
        self.assertEqual(got.stripped(), ['--timeout', '5', '--max', '9', 'x'])
        with self.assertRaises(Die) as caught:
            got.refuse_leftovers()
        self.assertEqual(caught.exception.message, 'unknown option --timeout')
        argv.read(['--max', '1'], spec).refuse_leftovers()

    def test_dashdash_ends_the_options_only_for_a_spec_that_declares_it(self):
        with_dash = argv.read(['--tab', '--', '--model', 'x', '--tab'], argv.Spec(flags=('--tab',), dashdash=True))
        self.assertEqual((with_dash.occurrences, with_dash.after_dashdash), ((('--tab', None),), ('--model', 'x', '--tab')))
        self.assertEqual(argv.read(['--'], argv.Spec(dashdash=True)).after_dashdash, ())
        with self.assertRaises(Die) as caught:
            argv.read(['--'], argv.Spec())
        self.assertEqual(caught.exception.message, 'unknown option --')

    def test_a_slot_flag_counts_only_at_its_slot(self):
        spec = argv.Spec(flags=('--force',), slots={'--force': 0}, unknown='collect')
        self.assertTrue(argv.read(['--force'], spec).present('--force'))
        late = argv.read(['x', '--force'], spec)
        self.assertEqual((late.present('--force'), late.leftovers), (False, ('x', '--force')))
        with self.assertRaises(Die):
            argv.read(['x', '--force'], argv.Spec(flags=('--force',), slots={'--force': 0}))

    def test_positionals_are_the_tokens_matching_the_pattern(self):
        spec = argv.Spec(valued=('--covers',), flags=('--wait',), positional=r'[0-9]{3}')
        got = argv.read(['001', '--covers', '002', '--wait', '003'], spec)
        self.assertEqual((got.positionals, got.all('--covers')), (('001', '003'), ['002']))
        with self.assertRaises(Die) as caught:
            argv.read(['12'], spec)
        self.assertEqual(caught.exception.message, 'unknown option 12')

    def test_a_dangling_valued_flag_is_dropped_only_where_the_spec_says_so(self):
        spec = argv.Spec(valued=('--model',), unknown='collect', dangling='drop')
        got = argv.read(['x', '--model', 'm', '--model'], spec)
        self.assertEqual((got.occurrences, got.leftovers), ((('--model', 'm'),), ('x',)))
        with self.assertRaises(Die):
            argv.read(['--model'], argv.Spec(valued=('--model',)))

    def test_the_muse_preflight_forwards_the_agents_own_flags_and_drops_a_trailing_one_with_no_value(self):
        seen = []

        def fake(timeout, command, **kw):
            seen.append(command)
            return subprocess.CompletedProcess(command, 0, stdout='OK\n', stderr='')

        with mock.patch.object(agents, 'with_timeout', fake):
            for given in (['--model', 'm', '--other', 'x', '--provider', 'p'], ['--model', 'm', '--model'], ['--model', '--provider', '--provider']):
                self.assertEqual(agents.preflight_muse(given), 0)
        self.assertEqual([c[2:-1] for c in seen], [['--model', 'm', '--provider', 'p'], ['--model', 'm'], ['--model', '--provider']])


ARGV_ROOTS = ('pstack-pair/scripts/pstack_core', 'pstack-kitchen/scripts/kitchen_core')
ARGV_FILES = ('pstack-kitchen/scripts/variant.py', 'pstack-trio/scripts/variant.py', 'pstack-pair-guided/scripts/variant.py')


def hand_rolled_option_reads(source):
    """Line numbers of each `while` loop that walks a token list by hand and names a flag in doing so: it pops index 0, reads `[i + 1]` (or
    `[1 + i]`), tests `len(x)` and indexes x by a variable, or drops the front of the list it tests (`x = x[2:]`). The names of the lists do not matter; a loop over text lines names no flag."""
    found = set()
    for loop in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.While)):
        if not any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith('--') for n in ast.walk(loop)):
            continue
        lengthed = {c.args[0].id for c in ast.walk(loop.test) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == 'len'
                    and c.args and isinstance(c.args[0], ast.Name)}
        for node in ast.walk(loop):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'pop'
                    and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0):
                found.add(node.lineno)
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.BinOp) and isinstance(node.slice.op, ast.Add):
                sides = (node.slice.left, node.slice.right)
                if any(isinstance(x, ast.Constant) and x.value == 1 for x in sides) and any(isinstance(x, ast.Name) for x in sides):
                    found.add(node.lineno)
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in lengthed and isinstance(node.slice, ast.Name):
                found.add(node.lineno)
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Subscript)
                  and isinstance(node.value.value, ast.Name) and node.value.value.id == node.targets[0].id and isinstance(node.value.slice, ast.Slice)
                  and isinstance(node.value.slice.lower, ast.Constant)):
                found.add(node.lineno)
    return sorted(found)


class OptionReaderGuardTests(unittest.TestCase):
    def test_the_guard_flags_each_hand_rolled_shape_whatever_the_names(self):
        shapes = {
            'pop': 'def f(tokens):\n    while tokens:\n        if tokens.pop(0) == "--x":\n            pass\n',
            'index': 'def f(opts, i):\n    while i < len(opts):\n        return opts[i] == "--x" and opts[i + 1]\n',
            'index reversed': 'def f(opts, i):\n    while opts:\n        return opts[1 + i] == "--x"\n',
            'slice': 'def f(rest):\n    while rest:\n        if rest[0] == "--x":\n            rest = rest[2:]\n',
            'length': 'def f(args, i):\n    while i < len(args):\n        i += 1\n        return args[i] == "--x"\n',
        }
        for name, source in shapes.items():
            self.assertEqual(hand_rolled_option_reads(source), {'length': [4], 'slice': [4]}.get(name, [3]), name)
        fine = ('def f(rest, queue):\n    first = rest[0]\n    value = rest.pop(0)\n    while queue:\n        queue = queue[1:]\n'
                '    while rest:\n        rest.pop(0)\n    return [first, value, rest[1 + 1]]\n')
        self.assertEqual(hand_rolled_option_reads(fine), [])

    def test_no_core_or_variant_reads_its_options_by_hand(self):
        paths = [p for r in ARGV_ROOTS for p in sorted((root / 'skills' / r).glob('**/*.py')) if p.name != 'argv.py']
        paths += [root / 'skills' / f for f in ARGV_FILES]
        stray = [f'{p.relative_to(root)}:{n}' for p in paths for n in hand_rolled_option_reads(p.read_text())]
        self.assertEqual(stray, [])


MAX_BODY_LINES = 80
MAX_PARAMETERS = 8


def oversized_functions(source):
    """(name, line, what) of each function whose body runs over MAX_BODY_LINES or that takes more than MAX_PARAMETERS parameters; self and cls do not
    count, keyword-only parameters do. A method is named for its class."""
    found = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f'{owner}{child.name}.')
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = child.args
                params = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, *filter(None, (a.vararg, a.kwarg)))]
                if isinstance(node, ast.ClassDef) and params[:1] in (['self'], ['cls']):
                    params = params[1:]
                body = child.end_lineno - child.body[0].lineno + 1
                if body > MAX_BODY_LINES:
                    found.append((f'{owner}{child.name}', child.lineno, f'{body} body lines'))
                if len(params) > MAX_PARAMETERS:
                    found.append((f'{owner}{child.name}', child.lineno, f'{len(params)} parameters'))
                visit(child, f'{owner}{child.name}.')
            else:
                visit(child, owner)
    visit(ast.parse(source), '')
    return found


class FunctionSizeGuardTests(unittest.TestCase):
    def test_the_guard_counts_body_lines_and_parameters_without_self_and_cls(self):
        wide = 'class K:\n    def m(self, a, b, c, d, e, f, g, *, h, i):\n        pass\n    @classmethod\n    def n(cls, a, b, c, d, e, f, g, h):\n        pass\n'
        self.assertEqual(oversized_functions(wide), [('K.m', 2, '9 parameters')])
        self.assertEqual(oversized_functions('def f(cls, a, b, c, d, e, f, g, h):\n    pass\n'), [('f', 1, '9 parameters')])
        nested = 'class K:\n    def m(self):\n        def inner(self, a, b, c, d, e, f, g, h):\n            pass\n'
        self.assertEqual(oversized_functions(nested), [('K.m.inner', 3, '9 parameters')])
        long_body = 'def f():\n    """doc"""\n' + '    x = 1\n' * (MAX_BODY_LINES - 1)
        self.assertEqual(oversized_functions(long_body), [])
        self.assertEqual(oversized_functions(long_body + '    x = 1\n'), [('f', 1, f'{MAX_BODY_LINES + 1} body lines')])

    def test_no_core_or_variant_function_runs_over_80_body_lines_or_takes_more_than_8_parameters(self):
        paths = [p for r in ARGV_ROOTS for p in sorted((root / 'skills' / r).glob('**/*.py'))]
        paths += [root / 'skills' / f for f in ARGV_FILES]
        stray = [f'{p.relative_to(root)}:{line} {name}: {what}' for p in paths for name, line, what in oversized_functions(p.read_text())]
        self.assertEqual(stray, [])


ENDS_ITSELF = ('Die', 'Exit', 'refuse_as')

# Functions that are not a registered command and still end it themselves, with the reason each may: the dispatcher, a command's argument reduction, and
# the subcommands a command hands its own arguments to.
BOUNDARIES = {
    ('pstack-pair/scripts/pstack_core/cli.py', 'parse'): 'reads the skill root before any variant is known',
    ('pstack-pair/scripts/pstack_core/cli.py', 'main'): 'the dispatcher: herdr missing and an unknown command end the process before a command runs',
    ('pstack-pair/scripts/pstack_core/commands/report.py', 'require_pair'): 'the check that gives a store with no pair.json the status 2 its commands pin',
    ('pstack-pair/scripts/pstack_core/consult.py', 'discuss_with_consultant'): 'the plan round that the trio and the kitchen discuss commands run with their own arguments',
    ('pstack-pair/scripts/pstack_core/commands/init.py', 'init_request'): 'the init command\'s own checks and arguments, which the kitchen\'s init reads too',
    ('pstack-pair/scripts/pstack_core/commands/init.py', 'init_store'): 'the init command\'s refusal of a pane herdr does not know, whoever registers the master',
    ('pstack-pair/scripts/pstack_core/send.py', 'dispatch_request'): 'the dispatch command\'s own checks, which the kitchen\'s dispatch runs before it sends',
    ('pstack-pair/scripts/pstack_core/send.py', 'dispatch_brief'): 'the dispatch command\'s refusal of a store with no pair.json, whoever sends the brief',
    ('pstack-pair/scripts/pstack_core/wait.py', 'wait_target'): 'the wait command\'s own arguments, which the kitchen\'s wait reads too',
    ('pstack-kitchen/scripts/kitchen_core/verify_options.py', 'parse_options'): 'reduces the verify command\'s own words to its options',
    ('pstack-kitchen/scripts/kitchen_core/feedback.py', 'close'): 'the feedback command\'s --close, which reads its own arguments',
    ('pstack-kitchen/scripts/kitchen_core/feedback.py', 'file_report'): 'the feedback command\'s filing, which reads its own arguments',
}


def registered_commands():
    """(file under skills, function name) of every command the cli can run: the core's table and each variant's, resolved from the tables themselves.
    The kitchen loads its commands on demand, so its wrappers name the module and function they run."""
    found = set()
    commands = [*cli.COMMANDS.values()]
    for skill in ('pstack-pair-guided', 'pstack-trio', 'pstack-kitchen'):
        commands += load_skill_variant(root / 'skills', skill).commands.values()
    for command in commands:
        module, name = getattr(command, 'command', (None, command.__name__))
        path = (f'pstack-kitchen/scripts/kitchen_core/{module}.py' if module else os.path.relpath(inspect.getsourcefile(command), root / 'skills'))
        found.add((path, name))
    return found


def raises_that_end_a_command(source):
    """(outermost function, line) of each `raise Die(...)`, `raise Exit(...)` or `raise refuse_as(...)`. A function defined inside another belongs to it;
    a method is named for its class."""
    found = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name + '.')
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, owner if '.' not in owner and owner else (owner or '') + child.name)
            else:
                if (isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call) and isinstance(child.exc.func, ast.Name)
                        and child.exc.func.id in ENDS_ITSELF):
                    found.append((owner, child.lineno))
                visit(child, owner)
    visit(ast.parse(source), '')
    return found


class EndsItselfGuardTests(unittest.TestCase):
    def test_the_guard_flags_a_helper_that_raises_die_or_exit_and_passes_a_command(self):
        source = ('from x import Die, Exit, refuse_as\n\ndef command(ctx, args):\n    def inner():\n        raise Exit(2)\n    raise Die("usage")\n\n'
                  'def helper(store):\n    raise Die("no")\n\nclass K:\n    def method(self):\n        raise refuse_as(1, 2, 3)\n\ndef quiet():\n    raise ValueError("x")\n')
        self.assertEqual(raises_that_end_a_command(source), [('command', 5), ('command', 6), ('helper', 9), ('K.method', 13)])

    def test_only_a_command_or_a_named_boundary_ends_itself_with_die_or_exit(self):
        commands = registered_commands()
        self.assertIn(('pstack-pair/scripts/pstack_core/commands/notes.py', 'new_brief'), commands)
        self.assertIn(('pstack-kitchen/scripts/kitchen_core/step.py', 'step'), commands)
        self.assertIn(('pstack-pair-guided/scripts/variant.py', 'answer'), commands)
        paths = [p for r in ARGV_ROOTS for p in sorted((root / 'skills' / r).glob('**/*.py')) if p.name != 'errors.py']
        paths += [root / 'skills' / f for f in ARGV_FILES]
        stray, used = [], set()
        for path in paths:
            rel = str(path.relative_to(root / 'skills'))
            for function, line in raises_that_end_a_command(path.read_text()):
                if (rel, function) in commands:
                    continue
                if (rel, function) in BOUNDARIES:
                    used.add((rel, function))
                    continue
                stray.append(f'{rel}:{line} in {function}')
        self.assertEqual(stray, [])
        self.assertEqual(sorted(set(BOUNDARIES) - used), [], 'a boundary that no longer ends itself leaves the list')


class DispatchedStore(unittest.TestCase):
    """A store with brief 001-x dispatched at EPOCH in a git repo of its own."""
    EPOCH = 1_700_000_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.cwd, self.store = self.path / 'cwd', self.path / 'st'
        for d in (self.cwd, self.store / 'briefs', self.store / 'reports'):
            d.mkdir(parents=True)
        self.git('init', '-q')
        (self.cwd / 'f.txt').write_text('x')
        self.git('add', 'f.txt')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'one')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.brief = self.store / 'briefs/001-x.md'
        self.brief.write_text('playbook: investigation\nplan: none\ntimebox: 10\n\n## Scope\n\nmay write:\n- f.txt\n')
        at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.EPOCH))
        (self.store / 'pair.json').write_text(json.dumps({'cwd': str(self.cwd), 'sidekick': {'name': 's', 'pane_id': 'p9'},
                                                            'dispatch': {'brief': str(self.brief), 'at': at, 'head': self.head}}))
        self.ctx = Context('pstack-pair', '/x', str(self.store), 'Pair', ('sidekick',), ('plans',), 21, '')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.cwd, capture_output=True, text=True, check=True).stdout


class CheckInFactTests(DispatchedStore):
    """CheckIn's flags come from the store's facts, not from reading the rendered digest."""

    def digest(self, minutes, interval=9):
        from pstack_core import checkin
        return checkin.checkin(self.ctx, str(self.store), interval, self.EPOCH + minutes * 60)

    def test_a_quiet_digest_inside_the_timebox_is_not_flagged(self):
        got = self.digest(5)
        self.assertEqual((got.elapsed_min, got.timebox_min, got.overrun, got.flagged), (5, 10, False, False))
        self.assertEqual(got.lines[0], 'check-in: 001-x.md  elapsed 5m of 10m\n')

    def test_the_timebox_overrun_is_flagged_from_the_elapsed_and_timebox_minutes(self):
        self.assertEqual((self.digest(10).overrun, self.digest(11).overrun, self.digest(11).flagged), (False, True, True))

    def test_a_write_outside_scope_is_counted_and_flagged(self):
        (self.cwd / 'other.txt').write_text('y')
        got = self.digest(1)
        self.assertEqual((got.outside_scope, got.flagged), (1, True))

    def test_an_objection_is_counted_and_flagged(self):
        (self.store / 'reports/001-x-s1.md').write_text('status: object\n')
        got = self.digest(1)
        self.assertEqual((got.objected, got.flagged), (1, True))

    def test_a_progress_log_nobody_touched_for_the_interval_is_stale_and_flagged(self):
        prog = self.store / 'progress/001-x.md'
        prog.parent.mkdir()
        prog.write_text('- started\n')
        os.utime(prog, (self.EPOCH, self.EPOCH))
        (self.cwd / 'f.txt').write_text('x')
        os.utime(self.cwd / 'f.txt', (self.EPOCH - 7200, self.EPOCH - 7200))
        got = self.digest(30)
        self.assertEqual((got.stale, got.flagged), (True, True))
        self.assertFalse(self.digest(1).stale)

    def test_two_objections_and_an_open_blocking_note_are_counted_and_flagged(self):
        for name in ('001-x-s1.md', '001-x-s2.md'):
            (self.store / 'reports' / name).write_text('status: object\n')
        self.assertEqual(self.digest(1).objected, 2)
        (self.store / 'notes').mkdir()
        (self.store / 'notes/001-x-n1.md').write_text('# Note\n\nstatus: blocking\n')
        (self.store / 'steps').mkdir()
        (self.store / 'steps/001-x.tsv').write_text(f'1\t{self.head}\t{self.EPOCH}\t\tone\n')
        got = self.digest(1)
        self.assertEqual((got.open_notes, got.flagged), (1, True))
        (self.store / 'reports/001-x-s1.md').unlink()
        (self.store / 'reports/001-x-s2.md').unlink()
        self.assertEqual((self.digest(1).objected, self.digest(1).flagged), (0, True), 'the open note alone flags it')
        (self.store / 'notes/001-x-n1.md').write_text('# Note\n\nstatus: resolved\n')
        self.assertFalse(self.digest(1).flagged)

    def test_a_brief_with_no_timebox_cannot_overrun(self):
        self.brief.write_text('playbook: investigation\nplan: none\n\n## Scope\n\nmay write:\n- f.txt\n')
        got = self.digest(999)
        self.assertEqual((got.timebox_min, got.overrun, got.flagged), (None, False, False))
        self.assertEqual(got.lines[0], 'check-in: 001-x.md  elapsed 999m of ?m\n')

    def test_words_in_the_progress_log_do_not_flag_the_digest(self):
        prog = self.store / 'progress/001-x.md'
        prog.parent.mkdir()
        prog.write_text('- STALE copy removed\n- 1 objected earlier\n- 2 blocking notes open before\n')
        os.utime(prog, (self.EPOCH, self.EPOCH))
        got = self.digest(1)
        self.assertTrue(any('STALE copy removed' in line for line in got.lines))
        self.assertEqual((got.stale, got.objected, got.open_notes, got.flagged), (False, 0, 0, False))

    def test_no_dispatched_brief_gives_no_digest(self):
        self.brief.unlink()
        self.assertIsNone(self.digest(1))


class WaitSeamTests(DispatchedStore):
    """What a wait says about how it ended, who owns the store while it waits, and which layer prints and records what."""
    kitchen_core_module = CoreTests.kitchen_core_module

    def events(self):
        path = self.store / 'events.tsv'
        return [line.split('\t')[2:] for line in path.read_text().splitlines()] if path.exists() else []

    def finish(self, agent, *, state='working', report='', mode='reply', quiet=False, activity=''):
        """finish_wait on the store; what it streamed to `out` is in self.printed."""
        self.printed = []
        with mock.patch.object(wait, 'agent_status', return_value=agent), mock.patch.object(wait, 'agent_state', return_value=state), \
                mock.patch.object(wait, 'sidekick_activity', return_value=activity), mock.patch('pstack_core.checkin.sidekick_activity', return_value=activity):
            return wait.finish_wait(self.ctx, str(self.store), 9, wait.WaitResult(0, '', '', report), mode, self.EPOCH + 60, quiet, self.printed.append)

    def test_a_working_sidekick_ends_the_wait_on_its_check_in_and_the_core_records_the_wake(self):
        got = self.finish('working')
        self.assertEqual((got.status, got.kind, got.unready, got.paused, got.flagged), (4, 'checkin', False, False, False))
        self.assertEqual(got.checkin.elapsed_min, 1)
        self.assertEqual(self.printed, ['state: working\n'])
        self.assertEqual(got.digest, ('report: missing\n', *got.checkin.lines))
        self.assertEqual(self.events(), [['master', 'wake', '001-x', 'checkin']])

    def test_a_quiet_wait_records_no_wake_for_the_check_in_it_returns(self):
        got = self.finish('working', quiet=True)
        self.assertEqual((got.kind, got.checkin is not None), ('checkin', True))
        self.assertEqual(self.events(), [])

    def test_a_pause_flags_the_outcome_though_its_digest_is_quiet(self):
        (self.store / 'paused').write_text('lunch\n')
        got = self.finish('working', quiet=True)
        self.assertEqual((got.paused, got.checkin.flagged, got.flagged), (True, False, True))

    def test_a_sidekick_that_is_not_working_leaves_the_wait_missing(self):
        got = self.finish('idle', state='settled')
        self.assertEqual((got.status, got.kind, got.checkin, got.digest), (4, 'missing', None, ()))
        self.assertEqual(self.printed[:2], ['state: settled\n', 'report: missing\n'])
        self.assertEqual(self.events(), [['master', 'wake', '001-x', 'missing']])

    def test_a_blocked_sidekick_without_a_report_ends_the_wait_blocked(self):
        got = self.finish('blocked', state='blocked')
        self.assertEqual((got.status, got.kind), (3, 'blocked'))
        self.assertEqual(self.printed[:2], ['state: blocked\n', 'report: missing\n'])
        self.assertEqual(self.events(), [['master', 'wake', '001-x', 'blocked']])

    def test_a_ready_report_ends_the_wait_with_the_report(self):
        report = self.store / 'reports/001-x.md'
        report.write_text('# Report 001\n\nstatus: done\nhead: abc\n')
        doc = json.loads((self.store / 'pair.json').read_text())
        (self.store / 'pair.json').write_text(json.dumps({**doc, 'sent': {'file': str(self.brief), 'kind': 'BRIEF'}}))
        with mock.patch.object(wait, 'withheld_report', return_value=None):
            got = self.finish('idle', state='settled', report=str(report), mode='reply')
        self.assertEqual((got.status, got.kind, got.checkin), (0, 'report', None))
        self.assertIn(f'report: {report}\n', self.printed)
        self.assertEqual(self.events()[-1], ['master', 'wake', '001-x', 'report:done'])

    def test_a_withheld_report_is_unready_and_takes_the_place_of_the_missing_line(self):
        with mock.patch.object(wait, 'withheld_report', return_value=('/s/reports/001-x.md', 'open blocking notes n1')):
            got = self.finish('working', quiet=True)
        self.assertEqual((got.kind, got.unready), ('checkin', True))
        self.assertEqual(self.printed, ['state: working\n', 'report: /s/reports/001-x.md written but not ready: open blocking notes n1\n'])
        self.assertEqual(got.digest, got.checkin.lines)

    def wait_in(self, owner, reply=None, **kw):
        """wait_for on the store with the reply the test names: the claim it saw while it waited, and its outcome."""
        seen = []

        def reply_(ctx, store, name, timeout, now=None, token=None):
            seen.append((token, wait.read_wait_owner(store)))
            return reply or wait.WaitResult(0, '', '', '')

        done = wait.WaitOutcome(4, 'missing')
        with mock.patch.object(wait, 'wait_for_reply', reply_), mock.patch.object(wait, 'finish_wait', return_value=done), \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            outcome = wait.wait_for(self.ctx, str(self.store), timeout=0, owner=owner, name='s', **kw)
        return seen, outcome, err.getvalue()

    def test_a_wait_with_no_owner_claims_the_store_under_its_pid_and_gives_it_back(self):
        seen, outcome, err = self.wait_in(None)
        self.assertEqual(seen, [(str(os.getpid()), str(os.getpid()))])
        self.assertEqual((outcome.claim_failed, wait.read_wait_owner(str(self.store)), err), (False, None, ''))

    def test_an_owner_claims_once_and_keeps_the_claim(self):
        owner = wait.WaitOwner('kitchen-1')
        seen, outcome, _ = self.wait_in(owner)
        self.assertEqual((seen, outcome.claim_failed, wait.read_wait_owner(str(self.store))), ([('kitchen-1', 'kitchen-1')], False, 'kitchen-1'))
        later = owner.after(outcome)
        self.assertEqual(later, wait.WaitOwner('kitchen-1', claim=False, checked=True))
        (self.store / 'wait.pid').write_text('someone-else\n')
        seen, outcome, _ = self.wait_in(later)
        self.assertEqual((seen, wait.read_wait_owner(str(self.store))), ([('kitchen-1', 'someone-else')], 'someone-else'), 'a later wait only checks the store')

    def test_an_owner_whose_claim_fails_waits_unowned_and_stays_unowned(self):
        owner = wait.WaitOwner('kitchen-1')
        with mock.patch.object(wait, 'claim_wait', side_effect=LockHeld('x')):
            seen, outcome, err = self.wait_in(owner)
        self.assertEqual((seen, outcome.claim_failed), ([(None, None)], True))
        self.assertIn('could not claim wait.pid', err)
        later = owner.after(outcome)
        self.assertEqual(later, wait.WaitOwner('kitchen-1', claim=False, checked=False))
        seen, outcome, err = self.wait_in(later)
        self.assertEqual((seen, outcome.claim_failed, err), ([(None, None)], False, ''), 'it neither claims nor checks')

    def test_a_wait_whose_store_a_newer_wait_took_ends_superseded(self):
        (self.store / 'wait.pid').write_text('newer\n')
        printed = []
        with mock.patch.object(wait, 'herdr_get', side_effect=AssertionError('no herdr call')):
            outcome = wait.wait_for(self.ctx, str(self.store), timeout=0, owner=wait.WaitOwner('old', claim=False), name='s', out=printed.append)
        self.assertEqual((outcome.status, outcome.kind, outcome.digest, printed), (4, 'superseded', (), [f'{wait.SUPERSEDED}\n']))
        self.assertEqual(wait.read_wait_owner(str(self.store)), 'newer')

    def test_release_gives_back_only_the_claim_it_holds(self):
        store = str(self.store)
        (self.store / 'wait.pid').write_text('kitchen-1\n')
        wait.release(store, wait.WaitOwner('other'))
        self.assertEqual(wait.read_wait_owner(store), 'kitchen-1')
        wait.release(store, wait.WaitOwner('kitchen-1'))
        self.assertIsNone(wait.read_wait_owner(store))
        wait.release(str(self.path / 'nowhere'), wait.WaitOwner('kitchen-1'))

    def dispatch_world(self, outcome, printed):
        """The kitchen's dispatch on the store, its core sending the brief and returning outcome after it streamed `printed`; the quiet wait it hands off
        to is a stub."""
        dispatch = self.kitchen_core_module('dispatch')
        handed = []

        def brief_sent(ctx, store, brief, *, timeout, send_only, owner=None, quiet=False, out=None):
            for chunk in printed:
                out(chunk)
            return send.DispatchResult((), outcome, outcome.status)

        request = send.DispatchRequest(str(self.store), str(self.brief), 0, False)
        with mock.patch.object(dispatch, 'require_pair'), mock.patch.object(dispatch, 'classify_brief'), mock.patch.object(dispatch, 'rotate_if_due'), \
                mock.patch.object(dispatch.send, 'dispatch_request', return_value=request), mock.patch.object(dispatch.send, 'dispatch_brief', brief_sent), \
                mock.patch.object(dispatch, 'quiet_wait', side_effect=lambda *a: handed.append(a) or 4), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            status = dispatch.dispatch(self.ctx, [str(self.store), str(self.brief)])
        return status, out.getvalue(), handed

    def test_a_dispatch_that_swallows_a_check_in_records_no_wake_and_the_wait_it_hands_off_to_records_one(self):
        quiet = self.finish('working', quiet=True)
        self.assertFalse(quiet.flagged)
        status, out, handed = self.dispatch_world(quiet, self.printed)
        self.assertEqual((status, out, len(handed)), (4, 'state: working\n', 1))
        self.assertEqual(self.events(), [], 'the dispatch records nothing for the check-in it swallowed')
        kitchen_wait = self.kitchen_core_module('wait')
        printed = self.printed

        def returns(ctx, store, *, timeout, owner, quiet, out):
            for chunk in printed:
                out(chunk)
            return self.quiet

        self.quiet = quiet
        with mock.patch.object(kitchen_wait, 'wait_target', return_value=(str(self.store), 0)), mock.patch.object(kitchen_wait, 'wait_for', returns), \
                mock.patch.object(kitchen_wait, 'release'), mock.patch.object(kitchen_wait, 'quiet_now', side_effect=[0, 10 ** 6, 10 ** 6]), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(kitchen_wait.quiet_wait(self.ctx, str(self.store), []), 4)
        self.assertEqual(self.events(), [['master', 'wake', '001-x', 'checkin']], 'the wait that returns records exactly one')

    def test_a_dispatch_that_swallows_a_check_in_still_prints_what_the_sidekick_runs_and_the_base_note(self):
        """The bytes the kitchen printed (kitchen_core 61781042f: the SWALLOWED filter kept every digest line it did not name) for a quiet check-in
        whose digest has a `running:` line and a `note:` line."""
        for name in ('trunk1.txt', 'trunk2.txt'):
            (self.cwd / name).write_text(name)
            self.git('add', name)
            self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', name)
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.brief.write_text(self.brief.read_text() + '- trunk1.txt\n- trunk2.txt\n')
        quiet = self.finish('working', quiet=True, activity='sleep for 3m')
        self.assertFalse(quiet.flagged, quiet.checkin.lines)
        status, out, handed = self.dispatch_world(quiet, self.printed)
        self.assertEqual(status, 4)
        self.assertRegex(out, rf'\Astate: working\nrunning: sleep for 3m\nnote: recorded base {self.head[:9]} is behind where its branch leaves '
                              r'remotes/origin/main; measuring from [0-9a-f]{9}\n\Z')

    def test_a_dispatch_that_returns_a_flagged_check_in_prints_it_and_records_one_wake(self):
        (self.cwd / 'other.txt').write_text('y')
        flagged = self.finish('working', quiet=True)
        self.assertTrue(flagged.flagged)
        status, out, handed = self.dispatch_world(flagged, self.printed)
        self.assertEqual((status, handed, out.count('check-in:'), out.count('outside scope: 1'), out.startswith('state: working\nreport: missing\ncheck-in:')),
                         (4, [], 1, 1, True))
        self.assertEqual(self.events(), [['master', 'wake', '001-x', 'checkin']])

    def test_a_quiet_wait_checks_the_store_at_every_poll_and_ends_with_the_refusal_the_pair_gives(self):
        """Kitchen wait at 61781042f, whose inner wait checked the store each poll: after a quiet check-in the store loses its pair.json, and the next poll ends 2
        with `state: ` on stdout and the pair's refusal on stderr."""
        kitchen_wait = self.kitchen_core_module('wait')
        quiet = self.finish('working', quiet=True)
        store, calls = str(self.store), []

        def returns(ctx, store_, *, timeout, owner, quiet, out):
            calls.append(owner)
            os.unlink(f'{store}/pair.json')
            return self.quiet

        self.quiet = quiet
        with mock.patch.object(wait, 'require_herdr'), mock.patch.object(wait, 'end_with_parent'), mock.patch.object(kitchen_wait, 'wait_for', returns), \
                mock.patch.object(kitchen_wait, 'quiet_now', return_value=0), mock.patch.object(kitchen_wait, 'poll_sleep'), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as out, mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            status = kitchen_wait.quiet_wait(self.ctx, store, [])
        self.assertEqual((status, len(calls), out.getvalue(), err.getvalue()), (2, 1, 'state: \n', f'pair.sh: no pair.json in {store}; run: pair.sh init <slug>\n'))


if __name__ == '__main__':
    unittest.main()
