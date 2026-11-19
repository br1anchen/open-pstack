import sys

from .. import secrets
from ..errors import Die, SecretFound
from ..store import pair_file


def scan_secrets(ctx, args):
    """Exit 0 when no file of the store that a publish would send looks like a credential, else 1 with one `secret:` line per finding. Lines a
    `pstack-allow-secret:` marker exempts are listed on stderr for the review."""
    if len(args) != 1:
        raise Die(f'usage: {ctx.prog} scan-secrets <store>')
    store = args[0]
    pair_file(store)
    exempt = []
    found = secrets.scan_store(store, exempt=exempt)
    for e in exempt:
        sys.stderr.write(
            f'secret: {e.path}:{e.line}: {e.kind} allowed by pstack-allow-secret ({e.reason})\n'
        )
    if found:
        raise SecretFound(found)
    print(f'no secrets in {store}')
    return 0
