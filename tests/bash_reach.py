"""Which bash function definitions can run from an entry script.

A bounded model of the bash in skills/*/scripts, not a bash parser. It reads the entry scripts the way bash does at load time (source order,
the last definition of a name wins, `copy_fn A B` snapshots A's body under B), takes the names in command position as the calls a body makes,
and raises ReachError on anything it does not model: a dynamic command word, an `eval`, a `declare -f`, a `trap` handler, a `source` it cannot
resolve, a `copy_fn` of a name with no earlier definition.

    reach = Reach([skills / 'pstack-pair/scripts/pair.sh', ...], base=skills)
    reach.reachable(entry)   # keys ('<path from base>:<name>') of the definitions that can run from that entry
    reach.dead()             # keys of the definitions in any sourced file that run from no entry
"""
import re
from pathlib import Path


class ReachError(Exception):
    pass


NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_:.-]*\Z')
ASSIGN = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=')
ARRAY_PREFIX = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=\Z')
REDIRECT = re.compile(r'(\d+|&)?(<<<|<<-?|>>|>&|<&|&>>|&>|>\||<>|>|<)')
FN_PARENS = re.compile(r'[ \t]*\([ \t]*\)')
ARITH_COMMAND = re.compile(r'[ \t]*\(\(')
SPECIAL = '@*#?$!-0123456789'
# The only dynamic forms the scripts use, each pinned to the function and the exact command it models: pair_main runs `cmd_$1` (every final
# cmd_* is a root), copy_fn builds `$2()` from `declare -f $1`, and pycore puts the variant's launcher words (`${run[@]}`) before python3.
COPY_FN_EVAL = '"$2() $(declare -f "$1" | tail -n +2)"'


class Def:
    """One function definition, or the alias a `copy_fn` makes."""

    def __init__(self, key, name, calls, body, alias_of=None):
        self.key, self.name, self.calls, self.body, self.alias_of = key, name, calls, body, alias_of


class _Case:
    def __init__(self):
        self.mode = 'subject'
        self.start = False


class _Parser:
    def __init__(self, path, rel, text):
        self.path, self.rel, self.text, self.n = path, rel, text, len(text)
        self.events = []
        self.owner = None
        self.owner_depth = 0
        self.owner_start = 0
        self.defined = set()

    def fail(self, i, what):
        raise ReachError(f'{self.rel}:{self.line(i)}: {what}')

    def line(self, i):
        return self.text.count('\n', 0, i) + 1

    def call(self, name, i):
        if self.owner is not None:
            self.owner.calls.append(name)
        else:
            self.events.append(('call', name, self.line(i)))

    def run(self):
        end = self.parse(0, False)
        if self.owner is not None:
            self.fail(end, f'function {self.owner.name} is never closed')
        return self.events

    def subst(self, i):
        """i is just after `$(` or `<(`; returns the index after the closing parenthesis."""
        return self.parse(i, True)

    def arith(self, i, depth=2):
        """i is just after `$((` (depth 2) or at the first `(` of a `((` command (depth 0); returns the index after the closing `))`,
        running any `$( )` inside."""
        text = self.text
        while i < self.n:
            if text.startswith('$((', i):
                i = self.arith(i + 3)
                continue
            if text.startswith('$(', i):
                i = self.subst(i + 2)
                continue
            c = text[i]
            if c == '`':
                self.fail(i, 'backtick substitution is not modeled')
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        self.fail(i, 'unterminated (( ))')

    def param(self, i):
        """i is just after `${`; returns the index after the matching `}`."""
        depth = 1
        text = self.text
        while i < self.n:
            c = text[i]
            if c == '\\':
                i += 2
            elif c == "'":
                i = text.index("'", i + 1) + 1
            elif c == '"':
                i = self.dq(i + 1)[0]
            elif text.startswith('$(', i):
                i = self.subst(i + 2)
            elif text.startswith('${', i):
                depth += 1
                i += 2
            elif c == '}':
                depth -= 1
                i += 1
                if depth == 0:
                    return i
            else:
                i += 1
        self.fail(i, 'unterminated ${ }')

    def dq(self, i, until='"'):
        """i is just after the opening quote (or at the start of a heredoc body, with until=None); returns (end, literal text or None)."""
        text, out, literal = self.text, [], True
        limit = self.n
        while i < limit:
            c = text[i]
            if until and c == until:
                return i + 1, ''.join(out) if literal else None
            if c == '\\':
                literal = False
                i += 2
            elif c == '`':
                self.fail(i, 'backtick substitution is not modeled')
            elif text.startswith('$((', i):
                literal = False
                i = self.arith(i + 3)
            elif text.startswith('$(', i):
                literal = False
                i = self.subst(i + 2)
            elif text.startswith('${', i):
                literal = False
                i = self.param(i + 2)
            elif c == '$':
                literal = False
                i += 1
            else:
                out.append(c)
                i += 1
        if until:
            self.fail(i, 'unterminated double quote')
        return i, None

    def read_word(self, i, dbr=False):
        """Returns (raw text, literal value or None, end). The value is None when the word expands anything."""
        text, n = self.text, self.n
        start, out, literal = i, [], True
        if text[i] in '<>' and text.startswith('(', i + 1):
            end = self.subst(i + 2)
            return text[start:end], None, end
        while i < n:
            c = text[i]
            if c == '(' and ARRAY_PREFIX.match(text[start:i]):
                literal = False
                i += 1
                while True:
                    while text[i] in ' \t\n':
                        i += 1
                    if text[i] == ')':
                        i += 1
                        break
                    _, _, i = self.read_word(i)
                continue
            if c in ' \t\n;' or (not dbr and c in '&|()<>'):
                break
            if dbr and text.startswith(']]', i) and i == start:
                i += 2
                out.append(']]')
                break
            if c == '\\':
                if text[i + 1:i + 2] == '\n':
                    i += 2
                    continue
                literal = False
                i += 2
            elif c == "'":
                j = text.find("'", i + 1)
                if j < 0:
                    self.fail(i, 'unterminated single quote')
                out.append(text[i + 1:j])
                i = j + 1
            elif c == '"':
                i, value = self.dq(i + 1)
                if value is None:
                    literal = False
                else:
                    out.append(value)
            elif c == '`':
                self.fail(i, 'backtick substitution is not modeled')
            elif c == '$':
                literal = False
                nxt = text[i + 1:i + 2]
                if text.startswith('$((', i):
                    i = self.arith(i + 3)
                elif nxt == '(':
                    i = self.subst(i + 2)
                elif nxt == '{':
                    i = self.param(i + 2)
                elif nxt == "'":
                    j = i + 2
                    while text[j] != "'":
                        j += 2 if text[j] == '\\' else 1
                    i = j + 1
                elif nxt.isalpha() or nxt == '_':
                    i += 1
                    while i < n and (text[i].isalnum() or text[i] == '_'):
                        i += 1
                elif nxt and nxt in SPECIAL:
                    i += 2
                else:
                    i += 1
            else:
                out.append(c)
                i += 1
        return text[start:i], ''.join(out) if literal else None, i

    def heredocs(self, i, pending):
        for delim, strip, quoted in pending:
            body_start = i
            while True:
                if i >= self.n:
                    self.fail(body_start, f'heredoc {delim} is never closed')
                j = self.text.find('\n', i)
                j = self.n if j < 0 else j
                line = self.text[i:j]
                if (line.lstrip('\t') if strip else line) == delim:
                    body_end, i = i, j + 1
                    break
                i = j + 1
            if not quoted:
                self.dq_range(body_start, body_end)
        return i

    def dq_range(self, i, end):
        """Run the substitutions in an unquoted heredoc body; the body is not a word and has no closing quote."""
        text = self.text
        while i < end:
            if text[i] == '\\':
                i += 2
            elif text[i] == '`':
                self.fail(i, 'backtick substitution is not modeled')
            elif text.startswith('$((', i):
                i = self.arith(i + 3)
            elif text.startswith('$(', i):
                i = self.subst(i + 2)
            elif text.startswith('${', i):
                i = self.param(i + 2)
            else:
                i += 1

    def parse(self, i, close):
        text, n = self.text, self.n
        cmdpos, forhdr, dbr, after_time = True, False, False, False
        cases, pending, pending_fn = [], [], None
        parens = braces = 0
        cmd = None

        def end_cmd():
            nonlocal cmd
            c, cmd = cmd, None
            if not c:
                return
            name, at, args, dynamic = c
            owner = self.owner.name if self.owner else None
            raws = [a[0] for a in args]
            if dynamic:
                pair_main = owner == 'pair_main' and name == '"$fn"' and raws == ['"$@"']
                launcher = owner == 'pycore' and name.startswith('${run[@]+') and args and args[0][1] == 'python3'
                if not (pair_main or launcher):
                    self.fail(at, f'dynamic command word {name}')
                return
            while name in ('builtin', 'command') and args:
                if name == 'command' and raws[0] in ('-v', '-V'):
                    return
                while args and args[0][0].startswith('-'):
                    args = args[1:]
                if not args:
                    return
                if args[0][1] is None:
                    self.fail(at, f'dynamic command word after {name}')
                name, args = args[0][1], args[1:]
                raws = [a[0] for a in args]
            if name == 'eval' and not (owner == 'copy_fn' and raws == [COPY_FN_EVAL]):
                self.fail(at, 'eval is not modeled outside copy_fn')
            if name in ('declare', 'typeset') and any(re.match(r'-[a-zA-Z]*[fF]', r) for r in raws):
                if not ((owner == 'copy_fn' and raws == ['-f', '"$1"']) or (owner == 'pair_main' and raws == ['-F', '"$fn"'])):
                    self.fail(at, f'{name} -f/-F is not modeled outside copy_fn and pair_main')
            if name == 'export' and any(re.match(r'-[a-zA-Z]*f', r) for r in raws):
                self.fail(at, 'export -f is not modeled')
            if name == 'compgen' and any(a[1] == 'function' or a[0].startswith('-A') for a in args):
                self.fail(at, 'compgen -A function is not modeled')
            if name == 'trap' and args and args[0][1] not in ('', '-'):
                self.fail(at, 'a trap handler is not modeled')
            if name in ('.', 'source'):
                if owner is not None or close:
                    self.fail(at, 'source inside a function or a substitution is not modeled')
                if not args:
                    self.fail(at, 'source needs a path')
                self.events.append(('source', args[0][0], self.line(at)))
            elif name == 'copy_fn':
                if owner is not None or close:
                    self.fail(at, 'copy_fn is only modeled at the top level of a file')
                if len(args) != 2 or any(a[1] is None for a in args):
                    self.fail(at, 'copy_fn needs two literal names')
                self.events.append(('copy', args[0][1], args[1][1], self.line(at)))

        while i < n:
            c = text[i]
            if c in ' \t':
                i += 1
                continue
            if c == '\\' and text[i + 1:i + 2] == '\n':
                i += 2
                continue
            if c == '#':
                while i < n and text[i] != '\n':
                    i += 1
                continue
            if c == '\n':
                i += 1
                if pending:
                    i = self.heredocs(i, pending)
                    pending = []
                if (cases and cases[-1].mode != 'body') or dbr:
                    continue
                forhdr = False
                end_cmd()
                cmdpos = True
                continue
            is_procsub = c in '<>' and text.startswith('(', i + 1)
            m = None if is_procsub or dbr else REDIRECT.match(text, i)
            if m and not (m.group(1) == '&' and m.group(2) not in ('>', '>>')):
                i = m.end()
                while i < n and text[i] in ' \t':
                    i += 1
                if i >= n:
                    self.fail(i, 'a redirection with no target')
                raw, lit, i = self.read_word(i)
                if m.group(2) in ('<<', '<<-'):
                    pending.append((raw if lit is None else lit, m.group(2) == '<<-', lit is None or lit != raw))
                continue
            if not dbr and not is_procsub and c in ';&|()':
                two = text[i:i + 3]
                if two == ';;&':
                    op = ';;&'
                elif text[i:i + 2] in (';;', ';&', '&&', '||', '|&'):
                    op = text[i:i + 2]
                else:
                    op = c
                if cases and cases[-1].mode == 'pattern':
                    i += len(op)
                    if op == ')':
                        cases[-1].mode, cases[-1].start, cmdpos = 'body', False, True
                    continue
                if cases and cases[-1].mode == 'subject':
                    i += len(op)
                    continue
                if op in (';;', ';&', ';;&'):
                    i += len(op)
                    end_cmd()
                    if cases:
                        cases[-1].mode, cases[-1].start = 'pattern', True
                    cmdpos = False
                elif op == '(':
                    if pending_fn:
                        self.fail(i, f'{pending_fn} has a subshell body, which is not modeled')
                    if text.startswith('(', i + 1) and cmdpos:
                        i = self.arith(i, 0)
                        cmdpos = False
                    else:
                        parens += 1
                        i += 1
                        cmdpos = True
                elif op == ')':
                    i += 1
                    if parens > 0:
                        parens -= 1
                        cmdpos = False
                    elif close:
                        end_cmd()
                        return i
                    else:
                        self.fail(i - 1, 'a stray )')
                else:
                    i += len(op)
                    if op == ';':
                        forhdr = False
                    end_cmd()
                    cmdpos = True
                continue
            raw, lit, i = self.read_word(i, dbr)
            if dbr:
                if raw == ']]':
                    dbr, cmdpos = False, False
                continue
            if cases and cases[-1].mode == 'subject':
                if raw == 'in':
                    cases[-1].mode, cases[-1].start = 'pattern', True
                continue
            if cases and cases[-1].mode == 'pattern':
                if cases[-1].start and raw == 'esac':
                    cases.pop()
                    cmdpos = False
                else:
                    cases[-1].start = False
                continue
            if forhdr:
                continue
            if not cmdpos:
                if cmd is not None:
                    cmd[2].append((raw, lit))
                continue
            word_at = i - len(raw)
            if after_time:
                after_time = False
                if raw == '-p':
                    continue
            if raw == 'time':
                after_time = True
                continue
            if raw == 'coproc':
                self.fail(word_at, 'coproc is not modeled')
            if raw in ('if', 'then', 'elif', 'else', 'while', 'until', 'do', '!'):
                continue
            if raw in ('fi', 'done'):
                cmdpos = False
                continue
            if raw == 'esac':
                if cases:
                    cases.pop()
                cmdpos = False
                continue
            if raw == 'case':
                cases.append(_Case())
                cmdpos = False
                continue
            if raw in ('for', 'select'):
                if ARITH_COMMAND.match(text, i):
                    i = self.arith(text.index('(', i), 0)
                forhdr, cmdpos = True, False
                continue
            if raw == 'function':
                self.fail(word_at, 'the function keyword is not modeled')
            if raw == '[[':
                dbr, cmdpos = True, False
                continue
            if raw == '{':
                braces += 1
                cmdpos = True
                if pending_fn:
                    self.owner = Def(f'{self.rel}:{pending_fn}', pending_fn, [], '')
                    if pending_fn in self.defined:
                        self.fail(word_at, f'{pending_fn} is defined twice in this file')
                    self.defined.add(pending_fn)
                    self.owner_depth, self.owner_start = braces, i
                    self.events.append(('def', self.owner))
                    pending_fn = None
                continue
            if pending_fn:
                self.fail(word_at, f'{pending_fn} has a body that does not start with a brace')
            if raw == '}':
                if braces == 0:
                    self.fail(word_at, 'a } with no { to close')
                braces -= 1
                cmdpos = False
                if self.owner is not None and not close and braces < self.owner_depth:
                    self.owner.body = text[self.owner_start:word_at]
                    self.owner = None
                continue
            if lit is not None and NAME.match(lit) and FN_PARENS.match(text, i):
                if close or self.owner is not None:
                    self.fail(word_at, f'{lit} is defined inside a function or a substitution, which is not modeled')
                i = FN_PARENS.match(text, i).end()
                pending_fn = lit
                continue
            if ASSIGN.match(raw):
                continue
            if lit is not None:
                self.call(lit, word_at)
            cmd = [lit if lit is not None else raw, word_at, [], lit is None and '/' not in raw]
            cmdpos = False
        if close:
            self.fail(n, 'unterminated $( )')
        end_cmd()
        return n


class Reach:
    def __init__(self, entries, base):
        self.base = Path(base).resolve()
        self.entries = [Path(e).resolve() for e in entries]
        self.scans = {}
        self.aliases = {}
        self.tables, self.roots, self.nodes = {}, {}, {}
        for entry in self.entries:
            table, roots = {}, []
            self.run(entry, entry, table, roots, ())
            self.tables[entry], self.roots[entry] = table, roots

    def rel(self, path):
        try:
            return str(path.relative_to(self.base))
        except ValueError:
            return str(path)

    def scan(self, path):
        if path not in self.scans:
            self.scans[path] = _Parser(path, self.rel(path), path.read_text()).run()
        return self.scans[path]

    def resolve(self, raw, in_file, entry, line):
        word = raw[1:-1] if len(raw) > 1 and raw[0] == '"' and raw[-1] == '"' else raw
        known = {'here': entry.parent, 'skill_root': entry.parent.parent, 'pair_core_dir': in_file.parent}

        def sub(m):
            name = m.group(1) or m.group(2)
            if name not in known:
                raise ReachError(f'{self.rel(in_file)}:{line}: cannot resolve ${name} in the source path {raw}')
            return str(known[name])
        path = re.sub(r'\$(?:\{(\w+)\}|(\w+))', sub, word)
        if '$' in path or '`' in path:
            raise ReachError(f'{self.rel(in_file)}:{line}: cannot resolve the source path {raw}')
        target = Path(path).resolve()
        if not target.is_file():
            raise ReachError(f'{self.rel(in_file)}:{line}: sources {raw}, which is not a file')
        return target

    def run(self, path, entry, table, roots, stack):
        if path in stack:
            raise ReachError(f'{self.rel(path)} sources itself')
        for ev in self.scan(path):
            if ev[0] == 'def':
                table[ev[1].name] = ev[1]
                self.nodes[ev[1].key] = ev[1]
            elif ev[0] == 'copy':
                _, a, b, line = ev
                if a not in table:
                    raise ReachError(f'{self.rel(path)}:{line}: copy_fn {a} {b}: {a} has no earlier definition')
                src = table[a]
                alias = self.aliases.setdefault((path, line, src.key), Def(f'{self.rel(path)}:{b}', b, src.calls, src.body, src))
                table[b] = alias
                self.nodes[alias.key] = alias
            elif ev[0] == 'source':
                self.run(self.resolve(ev[1], path, entry, ev[2]), entry, table, roots, stack + (path,))
            else:
                roots.append((ev[1], table.get(ev[1])))

    def reachable(self, entry):
        entry = Path(entry).resolve()
        table = self.tables[entry]
        seen = {}
        work = [d for name, d in table.items() if name.startswith('cmd_')]
        for name, snapshot in self.roots[entry]:
            target = snapshot or table.get(name)
            if target:
                work.append(target)
        while work:
            d = work.pop()
            if d.key in seen:
                continue
            seen[d.key] = d
            work.extend(table[c] for c in d.calls if c in table)
        return set(seen)

    def dead(self):
        live = set()
        for entry in self.entries:
            live |= self.reachable(entry)
        return set(self.nodes) - live

    def final(self, entry, name):
        return self.tables[Path(entry).resolve()].get(name)
