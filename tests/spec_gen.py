#!/usr/bin/env python3
"""Generate tests/SPEC.md from the code, the docs and the tests (U0 of plan 043)."""
import ast, os, re, subprocess, sys, json, glob
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

def git_grep(pattern, *paths, flags='-nE'):
    r = subprocess.run(['git', 'grep', flags, pattern, '--', *paths], capture_output=True, text=True)
    return r.stdout.splitlines()

PROD = ['skills/pstack-pair/scripts', 'skills/pstack-pair-guided/scripts', 'skills/pstack-trio/scripts', 'skills/pstack-kitchen/scripts']
DOCS = ['skills/pstack-pair', 'skills/pstack-pair-guided', 'skills/pstack-trio', 'skills/pstack-kitchen']

# ---- test index -------------------------------------------------------------
AB_MARK = ('self.bash(', 'wait_signalled', 'DELETED_BASH', 'BashReach', 'skills3', 'bases[')
class T:  # one test
    pass
tests = []
for path in sorted(glob.glob('tests/test_pstack_*.py')):
    mod = os.path.basename(path)[:-3]
    src = open(path).read()
    tree = ast.parse(src)
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith('test_'):
                seg = ast.get_source_segment(src, fn) or ''
                t = T(); t.id = f'{mod}.{cls.name}.{fn.name}'; t.src = seg; t.path = path; t.line = fn.lineno
                asserts = []
                strings = []
                for n in ast.walk(fn):
                    if isinstance(n, ast.Assert) or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr.startswith(('assert', 'check_port', 'kitchen_cases'))):
                        asserts.append(ast.unparse(n))
                    if isinstance(n, ast.Constant) and isinstance(n.value, str):
                        strings.append(n.value)
                t.asserts = '\n'.join(asserts); t.has_assert = bool(asserts); t.strings = '\n'.join(strings)
                name = fn.name
                t.ab = (cls.name == 'BashReachTests' or any(m in seg for m in AB_MARK)
                        or re.search(r'(match|equal|same).*(bash|base)|bash|phase_[34]|_base', name) is not None)
                tests.append(t)
golden_tests = {'pair': 'test_pstack_pair.PairScriptTests.test_pair', 'guided': 'test_pstack_pair.PairScriptTests.test_guided', 'trio': 'test_pstack_pair.PairScriptTests.test_trio'}
golden = {k: open(f'tests/pstack-pair/golden/{k}.txt').read() for k in golden_tests}

def cover(*literals, in_golden=True):
    """Behaviour test asserting any literal, else 'A/B only', else 'missing'."""
    lits = [l for l in literals if l]
    beh, ab = [], []
    for t in tests:
        if not t.has_assert:
            continue
        hit = any(l in t.asserts or l in t.strings for l in lits)
        if hit:
            (ab if t.ab else beh).append(t.id)
    if in_golden:
        for k, text in golden.items():
            if any(l in text for l in lits):
                beh.insert(0, golden_tests[k])
    if beh:
        return beh[0] + (f' (+{len(beh) - 1})' if len(beh) > 1 else ''), 'tested'
    if ab:
        return 'A/B only', 'ab'
    return 'missing', 'missing'

counts = {}
def line(section, text, src, lits, in_golden=True):
    test, state = cover(*lits, in_golden=in_golden)
    counts.setdefault(section, {'tested': 0, 'ab': 0, 'missing': 0})[state] += 1
    return f'- {text} — {src} — {test}'

out = []
# ---- Commands --------------------------------------------------------------
def helptext(script):
    env = dict(os.environ, HERDR_ENV='1', PATH=f'{ROOT}/tests/pstack-pair/bin:' + os.environ['PATH'], XDG_STATE_HOME='/tmp/spec-gen-state')
    return subprocess.run(['bash', script, 'help'], capture_output=True, text=True, env=env).stdout.splitlines()

def parse_help(lines):
    cmds, cur = [], None
    for i, l in enumerate(lines, 1):
        m = re.match(r'^  ([a-z][a-z-]*)( .*)?$', l)
        if m and not l.startswith('   '):
            cur = [m.group(1), (m.group(2) or '').strip().split('  ')[0], i]
            cmds.append(cur)
    return cmds
sections = []
def invokes(src, name, kitchen=False):
    n = re.escape(name)
    pats = [r"\w+\(\s*f?['\"]" + n + r"['\"\s]", r"(?:kitchen|pair)\.sh['\"]?\)?,?\s*['\"]?" + n + r"\b", r"\.sh['\"],\s*['\"]" + n + r"['\"]"]
    if kitchen:
        return any(re.search(p, src) for p in pats) and 'kitchen' in src
    return any(re.search(p, src) for p in pats)
cmd_golden = {k: set(re.findall(r'^\$ pair\.sh ([a-z-]+)', golden[k], re.M)) for k in golden}
variants = [('pair.sh', 'pstack-pair', 'pair', 'skills/pstack-pair/scripts/pair.sh'), ('pair.sh (guided)', 'pstack-pair-guided', 'guided', 'skills/pstack-pair-guided/scripts/pair.sh'),
            ('pair.sh (trio)', 'pstack-trio', 'trio', 'skills/pstack-trio/scripts/pair.sh')]
cmd_lines = []
base_pair = {c[0] for c in parse_help(helptext('skills/pstack-pair/scripts/pair.sh'))}
for label, skill, gk, script in variants:
    cmds = parse_help(helptext(script))
    for name, args, i in cmds:
        if gk != 'pair' and name in base_pair and name not in ('discuss', 'dispatch', 'spawn', 'stop', 'status', 'init', 'notify', 'finish', 'queue', 'rotate', 'wait', 'steer'):
            pass
        golden_hit = name in cmd_golden[gk]
        # tests that run the command by name through the script
        lits = [f"'{name}'", f'"{name}"', f' {name} ', f'{name} {{']
        test = golden_tests[gk] if golden_hit else None
        state = 'tested'
        if not test:
            beh, ab = [], []
            for t in tests:
                if t.has_assert and invokes(t.src, name):
                    (ab if t.ab else beh).append(t.id)
            if beh: test = beh[0]
            elif ab: test, state = 'A/B only', 'ab'
            else: test, state = 'missing', 'missing'
        counts.setdefault('Commands', {'tested': 0, 'ab': 0, 'missing': 0})[state] += 1
        cmd_lines.append(f'- `{label} {name} {args}`'.rstrip() + f' — {script} help:{i} — {test}')
kit = helptext('skills/pstack-kitchen/scripts/kitchen.sh')
kit_cmds = parse_help(kit)
for name, args, i in kit_cmds:
    beh, ab = [], []
    for t in tests:
        if t.has_assert and invokes(t.src, name, True):
            (ab if t.ab else beh).append(t.id)
    beh.sort(key=lambda tid: name.replace('-', '_') not in tid)  # a test named for the command first
    state = 'tested' if beh else ('ab' if ab else 'missing')
    test = beh[0] if beh else ('A/B only' if ab else 'missing')
    counts.setdefault('Commands', {'tested': 0, 'ab': 0, 'missing': 0})[state] += 1
    cmd_lines.append(f'- `kitchen.sh {name} {args}`'.rstrip() + f' — skills/pstack-kitchen/scripts/kitchen.sh help:{i} — {test}')
# the pair commands kitchen.sh passes through unchanged (kitchen.sh help prose)
own = {c[0] for c in kit_cmds}
for name in "init rotate failover permission new-plan new-brief queue notes new-note note next progress new-steer steer report notify stop scratch pause resume status metrics new-consult consult advice".split():
    if name in own: continue
    beh = sorted((t.id for t in tests if t.has_assert and not t.ab and invokes(t.src, name, True)), key=lambda tid: name.replace('-', '_') not in tid)
    ab = [t.id for t in tests if t.has_assert and t.ab and invokes(t.src, name, True)]
    state = 'tested' if beh else ('ab' if ab else 'missing')
    test = beh[0] if beh else ('A/B only' if ab else 'missing')
    counts.setdefault('Commands', {'tested': 0, 'ab': 0, 'missing': 0})[state] += 1
    cmd_lines.append(f'- `kitchen.sh {name}` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — {test}')

# ---- Exit codes ------------------------------------------------------------
codes = {0: 'ok', 1: 'usage or precondition', 2: 'herdr or gate failure', 3: 'agent blocked', 4: 'no report, queue empty, check-in or still running', 5: 'wrong state', 6: 'plan not agreed', 7: 'steer, consult or ask cap', 8: 'kinds not diverse'}
exit_lines = []
def frag(msg):
    # the longest static run without a format field
    if msg.startswith('('):
        return ''
    parts = re.split(r'\{[^}]*\}|\$\{[^}]*\}|\$\([^)]*\)|\$\w+', msg)
    parts = [p for p in parts if len(p) >= 12] or parts
    return max(parts, key=len).strip().lstrip(':;,) ') if parts else ''

def static_text(node):
    """A message expression as text, each formatted field as {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(v.value if isinstance(v, ast.Constant) else '{}' for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return static_text(node.left) + static_text(node.right)
    return ''

def strings_in(node):
    return [static_text(n) for n in ast.walk(node) if isinstance(n, (ast.Constant, ast.JoinedStr)) and static_text(n)]

def py_exits(path):
    """Die(msg, N) and `return N` (N in 2..8) of a Python file, found by parsing the calls whole, so a code on a continuation line counts."""
    tree = ast.parse(open(path).read())
    func_of = {}
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(f):
                func_of[n] = f.name
    found = []
    def visit(body):
        prev = None
        for st in body:
            for n in ast.walk(st):
                if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == 'Die' and len(n.args) == 2 and isinstance(n.args[1], ast.Constant) and n.args[1].value in range(2, 9):
                    found.append((n.args[1].value, n.lineno, static_text(n.args[0]) or '(message built at runtime)'))
                elif isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and type(n.value.value) is int and n.value.value in range(2, 9):
                    ctx = [t for t in strings_in(prev) if len(t) >= 12] if isinstance(prev, ast.Expr) and isinstance(prev.value, ast.Call) else []
                    found.append((n.value.value, n.lineno, ctx[-1].strip() if ctx else f'(returns {n.value.value} in {func_of.get(n, "module")}())'))
            prev = st
            for field in ('body', 'orelse', 'finalbody', 'handlers'):
                sub = getattr(st, field, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    visit(sub)
                elif isinstance(sub, list):
                    for h in sub:
                        if isinstance(h, ast.ExceptHandler):
                            visit(h.body)
    visit(tree.body)
    return found

def sh_exits(path):
    """`die "msg" N` (the call may span lines) and `exit N` / `return N` of a bash file."""
    text = open(path).read()
    lines = text.splitlines()
    found = []
    for m in re.finditer(r'\bdie\s+"((?:[^"\\]|\\.)*)"\s+([2-8])\b', text, re.S):
        found.append((int(m.group(2)), text.count('\n', 0, m.start()) + 1, ' '.join(m.group(1).split())))
    for i, l in enumerate(lines):
        if re.match(r'^\s*#', l): continue
        m = re.search(r'(?:^|&&|\|\||;|\{)\s*(?:exit|return)\s+([2-8])\s*(?:;|\}|$)', l)
        if m:
            near = ''.join(re.findall(r"printf '([^']*)'|echo \"([^\"]*)\"", lines[i - 1])[0]) if i and re.search(r"printf '|echo \"", lines[i - 1]) else ''
            fn = next((re.match(r'^([A-Za-z_][\w-]*)\(\)', lines[j]).group(1) for j in range(i, -1, -1) if re.match(r'^[A-Za-z_][\w-]*\(\)', lines[j])), 'script')
            found.append((int(m.group(1)), i + 1, near.replace('\\n', '').strip() or f'(exits {m.group(1)} in {fn}())'))
    return found

manual_at = [('skills/pstack-pair/scripts/pstack_core/wait.py', 411), ('skills/pstack-pair/scripts/pstack_core/wait.py', 421), ('skills/pstack-pair/scripts/pstack_core/wait.py', 429),
             ('skills/pstack-pair/scripts/pstack_core/commands/report.py', 85), ('skills/pstack-pair/scripts/pstack_core/spawn.py', 128), ('skills/pstack-pair/scripts/pstack_core/spawn.py', 224),
             ('skills/pstack-pair/scripts/pstack_core/spawn.py', 235)]
exit_lines, seen = [], set()
for path in git_grep(r'.', *PROD, flags='-lE'):
    if '/tests/' in path or not path.endswith(('.py', '.sh')):
        continue
    for c, no, msg in (py_exits(path) if path.endswith('.py') else sh_exits(path)):
        if any(path == mp and abs(no - ml) <= 1 for mp, ml in manual_at):
            continue
        key = (path, no)
        if key in seen: continue
        seen.add(key)
        exit_lines.append((c, path, no, msg, frag(msg)))
exit_lines.sort(key=lambda x: (x[0], x[1], x[2]))
# lines whose code carries no message: the text a test must show for them
HINTS = {('skills/pstack-pair/scripts/pstack_core/commands/report.py', 89): ['queue: empty'],
         ('skills/pstack-pair/scripts/pstack_core/wait.py', 647): ['superseded: a newer wait owns this store'],
         ('skills/pstack-trio/scripts/consultant-core.sh', 285): ['advice_status: answered'],
         ('skills/pstack-trio/scripts/consultant-core.sh', 290): ['advice: missing'],
         ('skills/pstack-kitchen/scripts/kitchen_core/verify.py', 550): ['verifier start failed in']}
# behaviours no caller depends on (standing order 27): path:line -> why
NOT_CONTRACT = {
    ('skills/pstack-pair/scripts/pstack_core/commands/metrics.py', 240): 'the events log that init creates is unreadable; no caller sends it',
    ('skills/pstack-pair/scripts/pstack_core/commands/scratch.py', 98): 'git worktree add failing in a valid repo; the exit code is the same 2 as every herdr or git failure',
    ('skills/pstack-pair/scripts/pstack_core/commands/scratch.py', 101): 'jj workspace add failing in a valid repo; the exit code is the same 2 as every herdr or git failure',
    ('skills/pstack-pair/scripts/pstack_core/commands/notes.py', 113): 'the Prime daemon refusing a delivery; a caller reads the exit code 2, which the herdr prompt failure case shows for every other kind',
    ('skills/pstack-pair/scripts/pstack_core/spawn.py', 194): 'the Prime daemon failing after its preflight passed; exit 5 like every spawn refusal, the pane stays for the human',
    ('skills/pstack-kitchen/scripts/kitchen_core/discuss.py', 215): 'unreachable: the loop above leaves the consultant sent the plan or answered, or ends the round on its state, so a working sidekick never meets a consultant with nothing to do',
    ('skills/pstack-trio/scripts/consultant-core.sh', 53): 'unreachable: a consultant that is neither answered nor ready ends the plan round at line 48 first',
}
exit_out = []
for c, path, no, msg, f in exit_lines:
    text = f'exit {c} ({codes[c]}): {msg[:90].replace("`", chr(39)) or "returned"}'
    text = re.sub(r"\{[^}]*\}", "…", text)
    if (path, no) in NOT_CONTRACT:
        counts.setdefault('Exit codes', {'tested': 0, 'ab': 0, 'missing': 0})['excluded'] = counts['Exit codes'].get('excluded', 0) + 1
        exit_out.append(f'- {text} — {path}:{no} — not a caller contract: {NOT_CONTRACT[(path, no)]}')
        continue
    exit_out.append(line('Exit codes', text, f'{path}:{no}', HINTS.get((path, no), [f])))
manual_exit = [
    (4, 'skills/pstack-pair/scripts/pstack_core/wait.py:411', 'wait returns 4 with the report path already shown (review-delta printed)', ['review-delta:']),
    (3, 'skills/pstack-pair/scripts/pstack_core/wait.py:421', 'wait returns 3 when the sidekick is blocked', ['blocked']),
    (4, 'skills/pstack-pair/scripts/pstack_core/wait.py:429', 'wait returns 4 for a check-in digest or a missing report', ['report: missing']),
    (4, 'skills/pstack-pair/scripts/pstack_core/commands/report.py:85', 'next returns 4 with `queue: paused` or `queue: empty`', ['queue: empty', 'queue: paused']),
    (4, 'skills/pstack-pair/scripts/pstack_core/spawn.py:128', 'spawn returns 4 while the bootstrap READY report is missing', ['bootstrap still pending']),
    (3, 'skills/pstack-pair/scripts/pstack_core/spawn.py:224', 'spawn returns 3 when the bootstrap did not settle', ['did not settle']),
    (4, 'skills/pstack-pair/scripts/pstack_core/spawn.py:235', 'spawn returns 4 when the agent settled but READY is missing', ['is missing; state=']),
    (4, 'skills/pstack-kitchen/scripts/kitchen_core/step.py', 'kitchen step and finish return 4 with `gates: still running`', ['gates: still running']),
    (2, 'skills/pstack-kitchen/scripts/kitchen_core/step.py', 'kitchen step returns 2 when a gate or the policy check fails; the failing command log tail is printed', ['gate']),
    (4, 'skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py', 'verify returns 4 with progress at the interval, grace or stalled', ['verify:progress', 'progress:']),
]
for c, src, text, lits in manual_exit:
    exit_out.append(line('Exit codes', f'exit {c}: {text}', src, lits))

# ---- Messages ---------------------------------------------------------------
prefixes = ['PAUSE:', 'NOTE:', 'STEER:', 'REPORT', 'running:', 'steps:', 'idle:', 'queued:', 'rotated:', 'report_status:', 'review-delta:', 'provider_error:', 'editing:',
            'written but not ready', 'closed:', 'cancelled:', 'audit:', 'covered:', 'warning: unreviewed:', 'redelivered:', 'deadline ', 'advice:', 'advice_status:',
            'paused:', 'queue: empty', 'queue: paused', 'gates: still running', 'gates: queued', 'unowned:', 'ready:', 'superseded', 'sidekick_response:', 'report: missing', 'next:']
msg_out = []
for p in prefixes:
    hits = [l for l in git_grep(re.escape(p), *PROD) if not re.match(r'^[^:]+:\d+:\s*(#|""")', l)]
    prod = [h for h in hits if '/references/' not in h]
    if not prod:
        src = 'docs only'
    else:
        m = re.match(r'^([^:]+):(\d+):', prod[0]); src = f'{m.group(1)}:{m.group(2)}'
    docs = [l for l in git_grep(re.escape(p), *[d for d in DOCS], flags='-nE') if l.endswith('.md') is False and (':references/' in l or 'SKILL.md' in l or '/references/' in l)]
    dm = re.match(r'^([^:]+):(\d+):', docs[0]) if docs else None
    doc = f' (read by {dm.group(1)}:{dm.group(2)})' if dm else ''
    msg_out.append(line('Messages', f'`{p.strip()}`{doc}', src, [p]))

# ---- Store files ------------------------------------------------------------
store_out = []
seenf = set()
for skill in ('pstack-pair', 'pstack-pair-guided', 'pstack-trio', 'pstack-kitchen'):
    for i, l in enumerate(open(f'skills/{skill}/SKILL.md').read().splitlines(), 1):
        m = re.match(r'^\| `([^`]+)` \| ([^|]+)\| ([^|]+)\|', l)
        if not m or m.group(1) in seenf: continue
        f = m.group(1)
        if not re.search(r'[./]', f) or f.startswith('pstack-'): continue
        seenf.add(f)
        base = re.sub(r'<[^>]*>|NNN|\[k\]|-[a-z]<k>', '', f).split('/')[0].replace('.md', '')
        key = f.replace('NNN-<slug>', '').replace('<k>', '').replace('<id>', '').replace('<date>', '')
        lit = [f.split('/')[0] if '/' in f else f]
        store_out.append(line('Store files', f'`{f}` written by {m.group(2).strip()}: {m.group(3).strip()[:110]}', f'skills/{skill}/SKILL.md:{i}', lit, in_golden=True))
fields = {}
for l in git_grep(r"\.(sidekick|master|consultant|verifier|dispatch|sent|verifying|escalations|scratch|pending|heads|skill_builds)(\.[a-z_]+)?\b", 'skills/pstack-pair/scripts/pstack_core', 'skills/pstack-kitchen/scripts/kitchen_core'):
    for m in re.finditer(r'\.(sidekick|master|consultant|verifier|dispatch|sent|verifying)\.([a-z_]+)', l):
        fields.setdefault(f'{m.group(1)}.{m.group(2)}', l.split(':')[0] + ':' + l.split(':')[1])
for k, src in sorted(fields.items()):
    store_out.append(line('pair.json fields', f'`pair.json` `.{k}`', src, [k.split('.')[1] if False else f"'{k.split('.')[1]}'", f'"{k.split(".")[1]}"']))

# ---- Decisions of plan 043 ----------------------------------------------------
decided = [
    ('a damaged pair.json gives one message `pair.json is damaged: <reason>` and exit 1; an absent optional field is not damage', 'plan 043 (b)', 'decided, U3'),
    ('a brief Scope keeps `**` globs and backticked paths', 'plan 043 (c); skills/pstack-pair/scripts/pstack_core/checkin.py', None),
    ('delivery (dispatch, queue send, steer) protects TERM, INT and HUP until the message is in; detached gate and Verify jobs take their own session and outlive the caller', 'plan 043 (e); skills/pstack-pair/scripts/pstack_core/send.py:132', None),
    ('`--timeout` and `--every` take integers (ms or minutes as each help line says); zero is the documented immediate or none, a negative or non-integer value is a usage error exit 1', 'plan 043 (f)', 'decided, U5'),
    ('the verify job key and the audit sample draw use zlib.crc32; an old-format completed job neither counts as done nor collides with the new key', 'plan 043 (g), consultant objection 3', 'decided, U5'),
    ('`finish` refuses a report that is not ready (open blocking note, head not a commit) with exit 7 and names why', 'skills/pstack-pair/scripts/pstack_core/commands/report.py:129', None),
    ('a known limit of the revert scan: land-check and verify call a unit reverted when a later stack commit has the same no-context patch-id as the inverse of the unit\'s patch, even if no real revert happened (an identical one-line deletion elsewhere in the file); the answer fails closed', 'skills/pstack-kitchen/scripts/kitchen_core/units.py:rebased_in',
     'test_pstack_kitchen.KitchenScriptTests.test_an_unrelated_identical_one_line_deletion_is_called_reverted_by_the_u0_scan'),
    ('a close claim held by a live process of another user (kill -0 fails with EPERM) reads as a dead owner\'s and is taken over; a pid reused by another process holds the claim while that process lives', 'skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:alive',
     'test_pstack_core.CoreTests.test_claim_close_takes_a_claim_only_from_a_holder_that_is_gone'),
    ('`--every`, `--timeout` and `--max` words are read as shell arithmetic: `010` is octal 8, `0x1F` is 31, `1+1` is 2, a name, `1.5` or `08` ends the command with status 1 and a message naming the word (plan 043 (f) narrows this to integers in U5)', 'skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:Waiter.arithmetic',
     'test_pstack_core.CoreTests.test_the_interval_of_a_wait_is_read_as_the_shells_arithmetic_reads_it'),
    ('a landing verdict `landing-vN.md` whose N is not a plain decimal (`1x`, `1_0`, a non-ASCII digit) is taken as the newest', 'skills/pstack-kitchen/scripts/kitchen_core/landing.py:latest_landing_verdict',
     'test_pstack_core.CoreTests.test_a_landing_verdict_with_a_version_that_is_not_a_number_is_the_newest'),
]
dec_out = []
for text, src, state in decided:
    if state:
        counts.setdefault('Decided', {'tested': 0, 'ab': 0, 'missing': 0, 'decided': 0})['tested' if state.startswith('test_') else 'decided'] += 1
        dec_out.append(f'- {text} — {src} — {state}')
    else:
        explicit = {'globs': 'test_pstack_kitchen.GlobTests.test_globs_cross_directories_only_with_double_star; test_pstack_pair.ReadinessTests.test_a_scope_line_listing_several_paths_is_refused_at_dispatch_and_queue',
                    'TERM': 'test_pstack_pair.%s.test_a_timeout_cannot_cut_a_delivery_short (delivery); test_pstack_core.CoreTests.test_step_and_finish_outlive_the_shell_that_ran_them (detached jobs)' % 'PiSidekickTests',
                    'not ready': None}
        key = 'globs' if 'globs' in text else ('TERM' if 'TERM' in text else 'not ready')
        if explicit[key]:
            counts.setdefault('Decided', {'tested': 0, 'ab': 0, 'missing': 0})['tested'] += 1
            dec_out.append(f'- {text} — {src} — {explicit[key]}')
        else:
            dec_out.append(line('Decided', text, src, ['rewrite the report'], in_golden=False))

# ---- write ---------------------------------------------------------------------
hdr = f'''# Behaviour spec

What an agent, a skill doc or a script depends on in pair.sh (pair, guided, trio) and kitchen.sh. Plan 043's contract: the golden
transcripts plus this file; a behaviour no line names may change. Generated at {subprocess.run(["git","rev-parse","--short","HEAD"],capture_output=True,text=True).stdout.strip()} by the commands below; regenerate with `python3 tests/spec_gen.py`.

Line format: `- <behaviour> — <source file:line> — <test id | decided, U<n> | not a caller contract: reason>`.
- A test id counts only when the test asserts the behaviour: the literal appears in an assert expression or in a string the test holds next to an assert, or the item is a command that a golden transcript runs (`$ pair.sh <command>`). `(+n)` says how many other tests also do.
- The match is a literal-string heuristic. A line no test matches is counted in the table below and needs a test, a decision, or a `not a caller contract` entry with its reason.

## How this was built

```
# commands (help output of each entry script)
for s in pstack-pair pstack-pair-guided pstack-trio; do PATH=tests/pstack-pair/bin:$PATH HERDR_ENV=1 bash skills/$s/scripts/pair.sh help; done
PATH=tests/pstack-pair/bin:$PATH HERDR_ENV=1 bash skills/pstack-kitchen/scripts/kitchen.sh help
# exit codes: every Die(..., 2-8) and bash die "..." N in the runtime scripts
git grep -nE 'Die\\(|die ".*" [2-8]' -- skills/pstack-pair/scripts skills/pstack-pair-guided/scripts skills/pstack-trio/scripts skills/pstack-kitchen/scripts
git grep -nE 'return [2-8]$|exit [2-8]' -- skills/pstack-pair/scripts skills/pstack-kitchen/scripts
# messages: each prefix the reference docs tell an agent to look for
git grep -noE '`[A-Za-z_-]+( [a-z]+)?:`' -- 'skills/*.md'
git grep -nF '<prefix>' -- skills/pstack-pair/scripts skills/pstack-pair-guided/scripts skills/pstack-trio/scripts skills/pstack-kitchen/scripts
# store files: the `| \\`path\\` |` tables in each SKILL.md; pair.json fields
git grep -nE '^\\| `' -- skills/pstack-pair/SKILL.md skills/pstack-pair-guided/SKILL.md skills/pstack-trio/SKILL.md skills/pstack-kitchen/SKILL.md
git grep -noE '\\.(sidekick|master|consultant|verifier|dispatch|sent|verifying)\\.[a-z_]+' -- skills/pstack-pair/scripts/pstack_core skills/pstack-kitchen/scripts/kitchen_core
```

Exit codes callers branch on: {'; '.join(f'{k} {v}' for k, v in codes.items())}.
'''
summary = ['## Counts', '', '| section | tested | A/B only | missing | decided | not a contract |', '| --- | --- | --- | --- | --- | --- |']
tt = [0, 0, 0, 0, 0]
for sec, c in counts.items():
    summary.append(f'| {sec} | {c["tested"]} | {c["ab"]} | {c["missing"]} | {c.get("decided", 0)} | {c.get("excluded", 0)} |')
    tt[0] += c['tested']; tt[1] += c['ab']; tt[2] += c['missing']; tt[3] += c.get('decided', 0); tt[4] += c.get('excluded', 0)
summary.append(f'| total | {tt[0]} | {tt[1]} | {tt[2]} | {tt[3]} | {tt[4]} |')
body = [hdr, '\n'.join(summary), '', '## Commands', '', *cmd_lines, '', '## Exit codes', '', *exit_out, '', '## Messages agents read', '', *msg_out, '',
        '## Store files and fields', '', *store_out, '', '## Decided in plan 043', '', *dec_out, '']
open('tests/SPEC.md', 'w').write('\n'.join(body))
print('\n'.join(summary))
