# Verdict 002: amp-idle-and-stop

status: inconclusive
units: 002
range: 7fca27105380693abb8741e0af8347d63c413f10..6ac18e8e557c12a97f8ff88524d57b4e19630bcd
verifier: amp anthropic/claude-sonnet-5-5 (a1.small)
verifier-agent: amp --model anthropic/claude-sonnet-5-5 --orb-size a1.small

## Findings

Inconclusive: the unit's code is not reachable from this orb, so no Acceptance line and no Brief command could be run at the unit's head.
The head 6ac18e8e557c12a97f8ff88524d57b4e19630bcd and the base 7fca27105380693abb8741e0af8347d63c413f10 are in no ref origin lists. `pair.sh sync --branch` announced no code to fetch, and the store branch holds no `code/*.bundle`. The packet's scratch worktree does not exist here. Routes tried: `git fetch origin <head sha>`, `git ls-remote origin` (no `kitchen-code/*` ref, only the heads below), re-running `pair.sh sync --branch` (store moved to 4e4a2e485, still no code), and a search of this orb for bundle files.
Needed from the master: publish the code so a verifier can fetch it (a push of a `kitchen-code/kitchen-followups/<role>-g<n>` branch, which standing order 25 allows for `kitchen/*` run branches, or a bundle on the store branch), then run `verify` again.

## Evidence

```
$ git cat-file -t 6ac18e8e557c12a97f8ff88524d57b4e19630bcd
fatal: remote error: upload-pack: not our ref 6ac18e8e557c12a97f8ff88524d57b4e19630bcd
$ git cat-file -t 7fca27105380693abb8741e0af8347d63c413f10
fatal: remote error: upload-pack: not our ref 7fca27105380693abb8741e0af8347d63c413f10
$ git ls-remote origin | grep -v refs/pull
a90bb4130393b864176dbeded9bb216fcb5f255c	HEAD
8a723f24827a0168b186fbaf3b04d81bd570f6b5	refs/heads/agency/herdr-core-mux
50d1572432c9b0ec427f88fc54129f084ac5b206	refs/heads/fix/kitchen-feedback
4e4a2e48561ecab9147e051b174bf3c886afa6b1	refs/heads/kitchen/kitchen-followups
a90bb4130393b864176dbeded9bb216fcb5f255c	refs/heads/main
0556ab208a67e294c7810c19f5abd75e31d9ea73	refs/heads/orb-joo-lazy-build
4e1f68ab0d8c6ada27e29f2df3cf872a9747d589	refs/heads/orb-pstack-kitchen
dc70e6e7ea05a2475c28dc38cdb88dd590a85bb7	refs/heads/python-port-2
$ ls /home/user/.local/state/pstack/scratch
ls: cannot access '/home/user/.local/state/pstack/scratch': No such file or directory
$ ls <store>/code
ls: cannot access 'code': No such file or directory
```

The master's events.tsv records `finish-verify-pass 002-amp-idle-and-stop 6ac18e8e5` from the sidekick, so the commit exists on the sidekick's orb only.

## Flows driven

None: no checkout at the head, so nothing could be driven.

## Folded Acceptance

none

## Not covered

All four Acceptance lines (threads --archive with `.verifying`, stop after an archived consultant, stop relay to a working sidekick versus CLI STOP to an idle one, Herdr/Rex stop and trio goldens unchanged) and Brief commands 1 to 3 (`-k threads_archive`, `-k stop`, `-k relay`) plus the Prove-block gate. The sidekick's report claims them; none is checked here.

## Lessons

none
