"""The one lexical reader for a command's arguments.

`read(args, spec)` splits the tokens into flags, their values, positionals and leftovers and refuses only what is lexically wrong: a valued flag
with no value after it (`<flag> needs a value`) and, when the spec refuses unknown tokens, a token no flag explains (`unknown option <token>`). What a
value means (a whole number, a choice, a comma list), which occurrence wins and what the flags exclude stay with the command that reads them.
"""
import re
from dataclasses import dataclass, field

from .store import Die


class ArgvError(Die):
    """A lexical refusal: a usage error, exit 1, like every other command-line mistake."""


@dataclass(frozen=True)
class Spec:
    """What one command's tail may hold.

    valued     flags that consume the next token, whatever it looks like (`--fallback-arg --x` has the value `--x`)
    flags      flags that stand alone
    dashdash   a standalone `--` ends the options; the tokens after it are `after_dashdash`
    unknown    'refuse' ends the command on a token no flag explains; 'collect' keeps it, in order, as a leftover
    positional a pattern; a token that matches it in full is a positional
    slots      {flag: index}: that flag counts only as the token at that index of `args`, and is an ordinary token anywhere else
    """
    valued: tuple = ()
    flags: tuple = ()
    dashdash: bool = False
    unknown: str = 'refuse'
    positional: str = ''
    slots: dict = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class Parsed:
    occurrences: tuple = ()
    positionals: tuple = ()
    leftovers: tuple = ()
    after_dashdash: tuple = ()
    spans: tuple = ()

    def present(self, flag):
        return any(name == flag for name, _ in self.occurrences)

    def last(self, flag, default=None):
        """The value of the last occurrence of `flag`; a flag without a value is True."""
        values = [value for name, value in self.occurrences if name == flag]
        if not values:
            return default
        return True if values[-1] is None else values[-1]

    def all(self, flag):
        return [value for name, value in self.occurrences if name == flag]

    def stripped(self, *flags):
        """The tokens as given, less every occurrence of the named flags and their values."""
        return [token for name, tokens in self.spans if name not in flags for token in tokens]

    def refuse_leftovers(self):
        if self.leftovers:
            raise ArgvError(f'unknown option {self.leftovers[0]}')


def read(args, spec=None):
    spec = spec or Spec()
    occurrences, positionals, leftovers, spans, after = [], [], [], [], []
    tokens = list(args)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        slot = spec.slots.get(token)
        recognised = slot is None or slot == i
        if recognised and token in spec.valued:
            if i + 1 >= len(tokens):
                raise ArgvError(f'{token} needs a value')
            occurrences.append((token, tokens[i + 1]))
            spans.append((token, tokens[i:i + 2]))
            i += 2
            continue
        if recognised and token in spec.flags:
            occurrences.append((token, None))
            spans.append((token, [token]))
        elif spec.dashdash and token == '--':
            after = tokens[i + 1:]
            break
        elif spec.positional and re.fullmatch(spec.positional, token):
            positionals.append(token)
            spans.append(('', [token]))
        elif spec.unknown == 'collect':
            leftovers.append(token)
            spans.append(('', [token]))
        else:
            raise ArgvError(f'unknown option {token}')
        i += 1
    return Parsed(tuple(occurrences), tuple(positionals), tuple(leftovers), tuple(after), tuple(spans))
