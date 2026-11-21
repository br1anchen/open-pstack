# Verdict landing: the stack at be8829682

status: clean
units: landing
range: b681abc230e3b1061d76289b7e250d4790c9b776..be88296826fbc6e1c7ab0ebf8a1dc26467c573cf
verifier: amp (claude-opus-5-5)
verifier-agent: claude --model claude-opus-5-5

## Findings

none. Verdict per shipping: PASS+NOTES.

Notes (no finding):

1. The packet's scratch did not exist on this orb (the packet was written on the master's orb). `kitchen.sh scratch <store> landing-verify --at be88296` refused here because pair.json belongs to the master. I made the scratch at the packet's exact path with `git worktree add --detach <scratch> be88296826fb…` in this orb's checkout (Evidence E1). That registers a worktree in this orb's `.git`. The master's `kitchen.sh verify` cannot remove it from another orb. It is harmless and is left as the verifier rule says.
2. The packet's `## Goals` section is empty, and it folds no unit (`Verified on their own: none`, `Folded into this landing: none`), so there was no Acceptance to answer line by line. I read briefs 017 to 020 (the units whose commits make up the range) only to pick the flows to drive. I proved their Acceptance with parent-against-head runs (E3 to E5).
3. The commits that use the store-ownership guard (unit 019) are in this range, but this orb's installed pstack build is not this tip. Its `kitchen.sh scratch` from a role failed with `pair.json: belongs to the master`, the same error that unit 019's test gets at base (E4). That is the expected pre-landing state, not a regression of the tip.

## Evidence

E1: scratch at the unit's head
```
$ git worktree add --detach /home/user/.local/state/pstack/scratch/runs-kitchen-followups/000-landing-verify be88296826fbc6e1c7ab0ebf8a1dc26467c573cf
Preparing worktree (detached HEAD be882968)
HEAD is now at be882968 fix(skills/pstack-kitchen): a landing packet gives each folded unit its accepted amendments
$ git -C <scratch> rev-parse HEAD; git -C <scratch> rev-parse --is-inside-work-tree
be88296826fbc6e1c7ab0ebf8a1dc26467c573cf
true
```

E2: Prove block, run as written from the scratch root with PSTACK_KITCHEN_ROLE=verifier and PSTACK_KITCHEN_RUN=kitchen-followups exported
```
$ python3 …/kitchen.py --repo <scratch> setup
setup: ok (1 commands, log …/repos/repo-f328a3c3/logs/setup-20261121T130925-5062.log)
setup-exit=0
$ python3 …/kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base b681abc230e3b1061d76289b7e250d4790c9b776
pstack behavioral: pass
  exit 0 591.3s python3 tests/run.py  log: …/repos/repo-f328a3c3/logs/20261121T130930/pstack-behavioral-0.log
gate-exit=0
(tail of that log)
1948 tests, 0 failed, 590s wall with 4 jobs
```

E3: stale origin/HEAD drive (unit 017, fetch_code). The probe script builds a bare origin with `main` and a clone with `origin/HEAD -> origin/main`. Origin then renames `main` to `trunk` and points its HEAD at `trunk`. The probe calls `gitstore._fetch_trunk` on the clone, then moves origin away (unreadable) and calls it again. The run at head used `<scratch>/skills/pstack-pair/scripts`, and the run at base used `pstack_core` from `git archive b681abc`.
```
head:
local origin/HEAD: refs/remotes/origin/main
fetch_trunk -> 'refs/remotes/origin/trunk'
unreadable origin: fetch_trunk -> ''
base:
local origin/HEAD: refs/remotes/origin/main
fetch_trunk -> ''
unreadable origin: fetch_trunk -> ''
```
At head, a stale origin/HEAD falls back to origin's own HEAD. At base it counted as a failed read. A trunk that cannot be fetched still fails at head.

E4: parent against head. The range's new tests (at head) ran with `skills/` checked out from b681abc (`git checkout b681abc -- skills/`) and then restored (`git checkout HEAD -- skills/`; `git status --short` was empty afterwards and HEAD was be88296826fb…).
```
accepted_amendments exit=1 2 tests, 2 failed            (unit 020 landing packet, unit 003 covers packet)
stop_names_a_role_whose exit=1 1 tests, 1 failed        (unit 002)
stop_to_a_working_thread exit=1 1 tests, 1 failed, 121s  (base stop blocked on the working thread; I killed the test process after ~2 min)
stop_whose_relay_cannot exit=1 1 tests, 1 failed        (unit 018)
pair_stop_relays exit=1 1 tests, 1 failed, 49s           (base pair stop blocked as well; killed)
role_from_another_skill_build exit=1 1 tests, 1 failed  (unit 019)
fetch_code_is_false_and_sync_warns exit=1 1 tests, 1 failed (unit 017)
rollback_that_cannot_be_published exit=1 2 tests, 2 failed
spawn_returned_4 exit=1 1 tests, 1 failed               (unit 017)
rotation_that_spawned_still_raises exit=0 1 tests, 0 failed (behaviour the range keeps: spawn 0 + failed publish still raises)
freshness_is_unknown exit=1 2 tests, 1 failed
```
Base failure messages (excerpts). Each is an assertion about the old behaviour, not an import error:
```
role_from_another_skill_build: AssertionError: 1 != 0 : kitchen.sh: pair.json: belongs to the master (amp:T-aaaaaaaa-…), not to this thread (amp:T-00000000-…-000000000001)
stop_whose_relay_cannot [too large]: AssertionError: "over upload_thread_file's 4 MiB" not found in 'amp demo-sidekick (…): not sent again: the same message is still pending there …'
spawn_returned_4: AssertionError: 1 != 4 : reused pane amp:1
accepted_amendments: AssertionError: '…Acceptance one holds.\n\nAccepted by the master (reviews/001-util.md), whatever the Acceptance above says' not found in …
fetch_code_is_false_and_sync_warns: AssertionError: True is not false
```
All of these pass at head (E2: full suite, 0 failed).

E5: tests/run.py -k with a module that fails to import (commit da33b0b7). I wrote a temporary `tests/test_zz_broken_probe.py` containing `import no_such_module_xyz` and removed it afterwards.
```
$ python3 tests/run.py test_zz_broken_probe -k something_unrelated -j 2
FAILED (errors=1)
1 tests, 1 failed, 0s wall with 2 jobs
failed: unittest.loader._FailedTest.test_zz_broken_probe
exit=1
```
A keyword that matches nothing no longer hides an import failure.

E6: docs profile (skills/** touched)
```
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
check-exit=0
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
spec-exit=0
$ bash -n skills/pstack-kitchen/scripts/kitchen.sh skills/pstack-pair/scripts/pair.sh
bash-n ok
```

E7: unit 018 doc and message text at head
```
$ git diff b681abc HEAD -- skills/pstack-kitchen/SKILL.md skills/pstack-kitchen/references/verifier.md
-  it is in a turn, a message or steer relay to it is pending, it sent the master
+  it is in a turn, a message or relay to it is pending, its unit is under verification (`.verifying` in pair.json), it sent the master
+  `stop` to a working Amp role is relayed the same way and needs the same `relay-done`.
+   A unit's section may carry an `Accepted by the master` block: its review's
+   Amendments, which amend that unit's Acceptance. …
(relay.py print_relay prints "relay-pending: … / relay: upload_thread_file … / relay: send_thread_message … / relay: then run … relay-done": the word steer does not appear)
```

E8: standing test check (range touches tests)
```
$ git diff b681abc HEAD -- tests/test_pstack_kitchen.py tests/run.py | grep -nE '^\+.*(re\.(sub|search|match)|Popen|subprocess\.|assertAlmost|delta=|replace\()'
327:+        r = subprocess.run(       (… check=False, timeout=120)
```
No new mask or normalisation. The one new direct subprocess is bounded (timeout=120). The fake `amp` gains `$FAKE/amp-turn-ends/<T>`, which is documented in its header comment.

## Flows driven

- pstack (behavioral): the repo's full suite through the kitchen gate, which drives pair.sh and kitchen.sh against the fake herdr and amp (E2). The stale-origin/HEAD trunk fetch was driven on real git repositories at both head and base (E3). Every new contract was run parent against head through the same CLI-driving tests (E4), and `tests/run.py -k` was driven with an import-broken module (E5).
- pstack-docs: check-pstack, spec_gen --check, and bash -n (E6). I read the SKILL.md and verifier.md changes (E7).

## Folded Acceptance

none. The packet has no `## Acceptance: unit …` section, and it folds no unit.

## Not covered

- No flow ran on live Amp threads, such as a real `stop` relay through `send_thread_message` and `relay-done` on a working role thread. The fake amp covers it (E2, E4). A live drive would message the run's real role threads, which the verifier must not do.
- Landing mode is commit. Nothing was merged or pushed, and nothing needed to be.

## Lessons

none (the packet lists none).
