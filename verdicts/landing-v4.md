# Verdict landing: landing-rebase-4

status: clean
units: landing
range: 699837d853a868ed785fa843cb167ee3e559c3bc..fa6cd2a6f85c8329ad5dd52da01e64352daeb2dc
verifier: amp, anthropic/claude-opus-5-5
verifier-agent: claude --model claude-opus-5-5

## Findings

none

Notes (not findings):
- `git cherry` marks every pick `+` (not patch-identical) because each pick carries a regenerated tests/SPEC.md. Range-diff with SPEC.md excluded shows that all four of 046's picks are identical (`=`). Two of 040's picks differ only where they meet main: in `assess` (mux/amp.py), 040's nest-pending check now comes before main's `pending_to` check, and in master.md, 040's nest sentence now comes before main's snooze-due paragraph. Both merged results read correctly (Evidence 2).
- bug-fix step 1/5 for a landing: the "failure" is that main lacks units 040 and 046. `git cherry 699837d8` lists all 8 originals as not on origin/main (Evidence 3). Every commit that changes source also changes a test file in the same commit (Evidence 3). Each unit's own tests-before-fix order was settled at its review; this landing restages nothing.

## Evidence

1. Commits on the branch, by subject, none twice

```
$ git log --format='%h %s' 699837d8..fa6cd2a6
fa6cd2a6 docs(skills/pstack-kitchen): help lists snooze; every send to a snoozed thread refuses, and a refused --done says to undo the Amp snooze
ec82fa34 fix(skills/pstack-pair): snooze --done refusals name the update_thread undo and raise SnoozeRefused
c8d46d48 fix(skills/pstack-kitchen): dispatch refuses a snoozed sidekick before classify, the playbook and the archive sweep, unless it rotates to a fresh thread
f46f7c86 fix(skills/pstack-pair): stop and close refuse before they change anything when a role thread is snoozed, naming every snoozed thread
e4235601 docs(skills/pstack-kitchen): starts print the nest instructions; a pending row keeps a thread from the sweep; take-master nests the whole run; --done does not check the parent
942fe43f fix(skills/pstack-pair): take-master nests every thread of the run, archived ones and earlier masters included, under the new anchor
3b74f174 fix(skills/pstack-pair): the sweep keeps a thread whose nest row is pending; nest lists an archived one with the unarchive, parent, re-archive calls
5d054e1e fix(skills/pstack-pair): a start under Amp threads prints the nest instructions of the thread it started
$ git log --format=%s 699837d8..HEAD | sort | uniq -d
(no output)
$ git rev-parse origin/main
699837d853a868ed785fa843cb167ee3e559c3bc
```

The subjects match `git log eb8228f9..6b2551f9` (b6ff274e, b48455eb, 1b8c37d1, 6b2551f9) and `git log 699837d8..11d2874b` (b2d218d4, bbbdf939, 43e07850, 11d2874b) one for one, in order.

2. Patches against the originals, tests/SPEC.md excluded

```
$ git range-diff eb8228f9..6b2551f9 699837d8..e4235601 -- ':!tests/SPEC.md'
1:  b6ff274e = 1:  5d054e1e fix(skills/pstack-pair): a start under Amp threads prints ...
2:  b48455eb ! 2:  3b74f174 fix(skills/pstack-pair): the sweep keeps a thread whose nest row is pending; ...
    @@ skills/pstack-pair/scripts/pstack_core/mux/amp.py: def assess(...)
     +        return state, NEST_PENDING
          if why := amp_routing.kept_by_branch(store, thread, role, tip):
              return state, why
    -     sent = load_json(outbox_path()).get(thread)
    +     if why := pending_to(store, thread, since):
3:  1b8c37d1 = 3:  942fe43f fix(skills/pstack-pair): take-master nests every thread of the run, ...
4:  6b2551f9 ! 4:  e4235601 docs(skills/pstack-kitchen): starts print the nest instructions; ...
    (context only: main's snooze-due paragraph follows 040's rewritten nest sentence)
$ git range-diff 699837d8..11d2874b e4235601..fa6cd2a6 -- ':!tests/SPEC.md'
1:  b2d218d4 = 1:  f46f7c86 ...
2:  bbbdf939 = 2:  c8d46d48 ...
3:  43e07850 = 3:  ec82fa34 ...
4:  11d2874b = 4:  fa6cd2a6 ...
```

Merged `assess` at the head (mux/amp.py:597 onward), excerpt:

```
    if state not in SETTLED:
        return state, f'working ({state}): it is in a turn'
    if thread in {r.thread for r in nest_pending(store)}:
        return state, NEST_PENDING
    if why := amp_routing.kept_by_branch(store, thread, role, tip):
        return state, why
    if why := pending_to(store, thread, since):
        return state, why
```

master.md lines 85-105 at the head: 040's text ("Every command that starts a thread prints the same instruction ... a sweep keeps a thread (`kept: nest pending`) until it is done.") followed unchanged by main's "A `snooze-due: N role threads` line ..." paragraph, which then runs into 046's snoozed-refusal text.

3. Parent against head; tests travel with source

```
$ git cherry 699837d8 6b2551f9 eb8228f9
+ b6ff274e...  + b48455eb...  + 1b8c37d1...  + 6b2551f9...
$ git cherry 699837d8 11d2874b 699837d8
+ b2d218d4...  + bbbdf939...  + 43e07850...  + 11d2874b...
$ for c in 699837d8..HEAD: files changed
5d054e1e: nest.py mux/amp.py tests/SPEC.md tests/test_pstack_kitchen.py
3b74f174: nest.py mux/amp.py tests/SPEC.md tests/test_pstack_kitchen.py
942fe43f: nest.py tests/SPEC.md tests/test_pstack_kitchen.py
e4235601: SKILL.md references/master.md tests/SPEC.md
f46f7c86: kitchen_core/master.py variant.py commands/stop.py mux/amp.py tests/SPEC.md tests/test_pstack_core.py tests/test_pstack_kitchen.py
c8d46d48: kitchen_core/dispatch.py tests/SPEC.md tests/test_pstack_kitchen.py
ec82fa34: commands/snooze.py errors.py tests/SPEC.md tests/test_pstack_core.py tests/test_pstack_kitchen.py
fa6cd2a6: SKILL.md references/master.md variant.py tests/SPEC.md tests/test_pstack_pair.py
```

4. Full suite at the head (scratch worktree, PSTACK_KITCHEN_ROLE=verifier)

```
$ python3 tests/run.py
... (slowest list, 8 lines omitted)
2051 tests, 0 failed, 769s wall with 4 jobs
exit 0
```

5. Prove commands

```
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../repos/repo-f328a3c3/logs/setup-20261124T135644-4114.log)
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base 699837d853a868ed785fa843cb167ee3e559c3bc
pstack behavioral: pass
  exit 0 605.7s python3 tests/run.py  log: .../repos/repo-f328a3c3/logs/20261124T140946/pstack-behavioral-0.log
```

6. Static checks at the head

```
$ python3 tests/spec_gen.py --check
| total | 438 | 0 | 0 | 3 | 5 |
ok: tests/SPEC.md is current, 0 missing
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
$ uv lock --check
Resolved 3 packages in 39ms
$ uv run --frozen ruff format --check
112 files already formatted
$ uv run --frozen ruff check
All checks passed!
$ uv check --frozen
All checks passed!
```

7. Seam drive, 040 nest × 046 snooze, kitchen.sh against the fake amp (a scratch probe subclassing AmpKitchenTests, run on its own two methods and then deleted; the worktree is clean afterwards)

```
--- spawn output nest lines:
nest-pending: T-...0001 (sidekick) under T-aaaaaaaa-...0000
nest: call update_thread(thread=<T>, parent_thread=<anchor>) for each, then kitchen.sh nest <store> --done <thread>...
--- stop (snoozed, nest pending):           [exit 5, store snapshot unchanged]
kitchen.sh: snoozed: T-...0001 is snoozed; call update_thread(thread=T-...0001, snooze="off"), then kitchen.sh snooze <store> --wake T-...0001, then run this again
--- nest after the refused stop:
nest-pending: T-...0001 (sidekick) under T-aaaaaaaa-...0000
--- nest --done on the snoozed sidekick:
nested: T-...0001 under T-aaaaaaaa-...0000
--- stop after nest --done, still snoozed:  [exit 5]
kitchen.sh: snoozed: T-...0001 is snoozed; ...
--- stop after wake:
amp demo-sidekick (T-...0001): pstack-kitchen STOP <store>
sidekick_state: idle
--- take-master with a snoozed sidekick:
kept T-aaaaaaaa-...0000: nest pending (nest it first; update_thread cannot set a parent on an archived thread)
closed the old master demo-master (amp:T-aaaaaaaa-...0000)
master: demo-master in amp:T-...0002 (generation 2)
anchor: T-...0002 is the run's top-level anchor "fake title of T-aaaaaaaa-...0000"; run kitchen.sh nest <store>
archive: 0 archived, 1 kept, 2 in use
--- nest after take-master:
nest-pending: T-...0002 (master) top-level
nest-pending: T-...0001 (sidekick) under T-...0002
nest-pending: T-aaaaaaaa-...0000 (master) under T-...0002
Ran 2 tests in 4.323s
OK
```

## Flows driven

- pstack (skills/pstack-pair, skills/pstack-kitchen scripts): kitchen.sh spawn, stop, nest, nest --done, snooze --wake, rotate-master and take-master, driven against the fake amp. The seam between nest-pending (040) and snoozed (046) holds: a refused stop leaves the nest row, nesting a snoozed thread does not wake it, and take-master moves a snoozed sidekick's row and the old master's row under the new anchor (Evidence 7). Each unit's own flows are covered by its tests in the suite (Evidence 4, 5).
- pstack-docs (SKILL.md, master.md): read the merged master.md lines 85-105, where 040's and 046's texts meet main's (Evidence 2); check-pstack resolves the links (Evidence 6).

## Folded Acceptance

### Unit 048 landing-rebase-4

- `git log origin/main..HEAD` holds the 8 commits by subject (4 from 040, 4 from 046) plus any step 2 fix commits, none twice: held. There are 8 commits, no fix commits, and no duplicate subjects (Evidence 1). The patches match the originals apart from SPEC.md and the two resolved meeting points (Evidence 2).
- The full suite, check-pstack, spec_gen --check, ruff and uv check pass at the head: held (Evidence 4, 5, 6).

## Not covered

- Real Amp threads: I started no real role threads and made no real update_thread calls, because doing so would change shared thread state. Instead the nest and snooze flows were driven against the repo's fake amp (Evidence 7).
- Unit 047 is outside this landing, as the brief says.

## Lessons

none
