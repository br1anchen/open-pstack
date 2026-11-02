#!/usr/bin/env python3
"""The commands the Herdr plugin (plugins/herdr) and the Rex actions (plugins/rex) run. Stdlib only.

  sync                          link this checkout's skills into each agent runtime (scripts/pstack-sync.py --apply)
  prompt --target PANE TEXT     type TEXT into the agent in PANE (a Herdr pane or Rex block) and submit it
  fleet                         print `kitchen.sh fleet`, then wait for Enter (for a popup or a window)
  install-rex [--print | --uninstall]
                                add (or remove) the pstack actions to Rex's init.lua as one marked block, back the file up,
                                link the pi extension and reload Rex's config; --print shows the block and changes nothing

The multiplexer is pstack's own choice (PSTACK_MUX, HERDR_ENV, REX_BLOCK); the Rex actions set PSTACK_MUX=rex."""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '-- pstack begin (written by plugins/pstack-action.py install-rex; remove with --uninstall)'
END = '-- pstack end'


def core():
    sys.path.insert(0, str(ROOT / 'skills/pstack-pair/scripts'))
    from pstack_core import mux

    return mux


def sync(_):
    return subprocess.run(
        [sys.executable, str(ROOT / 'scripts/pstack-sync.py'), '--apply'], check=False
    ).returncode


def prompt(args):
    m = core().get()
    target = args.target or m.own_pane()
    if not target:
        sys.stderr.write(f'no pane: pass --target, or run inside {m.label}\n')
        return 1
    r = m.prompt(target, args.text, stderr=None)
    if r.ok:
        print(f'sent {args.text} to {target}')
    return 0 if r.ok else 1


def fleet(_):
    code = subprocess.run(
        [str(ROOT / 'skills/pstack-kitchen/scripts/kitchen.sh'), 'fleet'], check=False
    ).returncode
    if sys.stdin.isatty():
        input('\npress Enter to close ')
    return code


def rex_config():
    home = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return Path(os.environ.get('REX_CONFIG') or Path(home) / 'rex/init.lua')


def lua_string(text):
    """A Lua string literal for text."""
    return (
        '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    )


def rex_block():
    lua = ROOT / 'plugins/rex/pstack.lua'
    return f'{BEGIN}\ndofile({lua_string(str(lua))})({lua_string(str(ROOT))})\n{END}\n'


def without_block(text):
    """The file's text with the pstack block (and the blank line before it) taken out."""
    if BEGIN not in text:
        return text
    head, _, rest = text.partition(BEGIN)
    if END + '\n' not in rest:
        raise SystemExit(
            f'pstack: {rex_config()} has "{BEGIN}" but no "{END}" line after it; fix the block by hand'
        )
    _, _, tail = rest.partition(END + '\n')
    return head.rstrip('\n') + ('\n' if head.strip() else '') + tail


def pi_extension_link():
    return Path(os.path.expanduser('~/.pi/agent/extensions/pstack-program-status.ts'))


def install_rex(args):
    if args.print:
        sys.stdout.write(rex_block())
        return 0
    path = rex_config()
    old = path.read_text() if path.exists() else ''
    new = without_block(old)
    if not args.uninstall:
        new = new + ('\n' if new.strip() else '') + rex_block()
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_name(
                f'{path.name}.pstack-{time.strftime("%Y%m%d%H%M%S")}'
            )
            shutil.copy2(path, backup)
            print(f'backup: {backup}')
        path.write_text(new)
    print(f'{"removed from" if args.uninstall else "installed in"} {path}')
    link = pi_extension_link()
    if args.uninstall:
        if link.is_symlink():
            link.unlink()
    elif link.parent.is_dir() and not link.exists():
        if link.is_symlink():
            link.unlink()
        link.symlink_to(ROOT / 'plugins/rex/agents/pi-program-status.ts')
        print(f'pi extension: {link}')
    if shutil.which('rex'):
        return subprocess.run(['rex', 'config', 'reload'], check=False).returncode
    return 0


def main(argv):
    parser = argparse.ArgumentParser(
        prog='pstack-action.py', description=__doc__.split('\n')[0]
    )
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('sync').set_defaults(run=sync)
    p = sub.add_parser('prompt')
    p.add_argument('--target', default='')
    p.add_argument('text')
    p.set_defaults(run=prompt)
    sub.add_parser('fleet').set_defaults(run=fleet)
    r = sub.add_parser('install-rex')
    mode = r.add_mutually_exclusive_group()
    mode.add_argument('--print', action='store_true')
    mode.add_argument('--uninstall', action='store_true')
    r.set_defaults(run=install_rex)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
