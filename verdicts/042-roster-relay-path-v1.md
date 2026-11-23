# Verdict 042: roster-relay-path

status: reject
units: 042
range: be88296826fbc6e1c7ab0ebf8a1dc26467c573cf..e9dc6fe01a2c069c49f9e30fc59c5c036061158d
verifier: amp (claude-opus-5-5)
verifier-agent: claude --model claude-opus-5-5

## Findings

1. Acceptance "The `.gitignore` line matches exactly the LOCAL rule's paths" is broken. The new line `sessions/master-g*-roster.toml` (72d188ab, gitstore.py) still ignores paths the LOCAL rule `sessions/master-g\d+-roster\.toml` does not classify. `sessions/master-gx-roster.toml` and `sessions/master-g-roster.toml` are ignored by git but `gitstore.classify` gives both to `master` ("the master's directories"). A master file at either path would be kept out of `git add` and never reach the branch. This is the same kind of over-match the brief fixed for `sessions/other-roster.toml`. `test_gitstore_ignores_exactly_the_local_class` checks only `other-roster.toml` on the not-ignored side, so it misses this (Evidence B). Smallest fix, proven with `git check-ignore` (Evidence C): make the rule's `ignore` the two lines `sessions/master-g[0-9]*-roster.toml` and `!sessions/master-g*[!0-9]*-roster.toml`. Then add `sessions/master-gx-roster.toml`, `sessions/master-g-roster.toml` and `sessions/master-g1x-roster.toml` to that test's not-ignored list.

Every other Acceptance line holds (see Evidence A, D, E, F, G).

Note, not a finding (outside the Acceptance as written): take-master looks for the upload under `bundle.repo_of(store)`, which is pair.json's `git_root` from the branch, meaning the old orb's checkout path. When the next master's checkout sits at another absolute path than the old one, the file uploaded to its own `.git/pstack-roster-g2.toml` is not found and take-master says `did not arrive` (Evidence E, first probe). When only the store moves, it installs (Evidence E, control). On Amp orbs the checkout is `/home/user/workspace/repo` everywhere, so this is latent. The steer relay uses the same lookup. Looking in the store worktree's own repository, or in `git.toplevel()` of the cwd, would remove the dependence.

## Evidence

A. Relay line, printed by rotate-master under the git store (scratch probe built on `AmpGitKitchenTests`, deleted after use):

```
$ python3 tests/probe_042.py Probe042.test_upload_to_the_new_orbs_checkout Probe042.test_neither_present
--- rotate-master relay lines
relay: upload_thread_file path=/tmp/tmp8h4_3t6v/state/pstack/kitchen/runs/demo/sessions/master-g2-roster.toml destination=.git/pstack-roster-g2.toml thread=T-00000000-0000-4000-8000-000000000002
relay: upload it before you end this turn; take-master on the next master waits at most 30 s for your turn to end, and keeps its own roster when the file is not there
...
--- take-master roster lines (nothing uploaded)
roster: /tmp/tmppvtkh1kt/master2-orb/runs/demo/sessions/master-g2-roster.toml did not arrive from the previous master; this orb keeps its own roster
installed: False
Ran 2 tests ... OK   (take-master exited 0: as_thread asserts code 0)
```

B. Ignore line against the LOCAL rule (fresh repo, `.gitignore` = `gitstore.gitignore_text()` at e9dc6fe0; rule column = `re.fullmatch` of the rule's regex):

```
$ bash /tmp/v042-ign.sh skills/pstack-pair/scripts
9:sessions/master-g*-roster.toml
sessions/master-g2-roster.toml: ignored, rule: LOCAL
sessions/master-g10-roster.toml: ignored, rule: LOCAL
sessions/other-roster.toml: tracked, rule: not LOCAL
sessions/master-gx-roster.toml: ignored, rule: not LOCAL
sessions/master-g-roster.toml: ignored, rule: not LOCAL
$ python3 -I -c '... gitstore.classify(p) ...'
sessions/master-gx-roster.toml -> master | the master's directories
sessions/master-g-roster.toml -> master | the master's directories
sessions/master-g2-roster.toml -> local | roster snapshots of a master hand-over (the host roster stays on the host)
```

C. Candidate exact ignore lines (`sessions/master-g[0-9]*-roster.toml` and `!sessions/master-g*[!0-9]*-roster.toml`):

```
candidate line: sessions/master-g2-roster.toml ignored
candidate line: sessions/master-g10-roster.toml ignored
candidate line: sessions/other-roster.toml tracked
candidate line: sessions/master-gx-roster.toml tracked
candidate line: sessions/master-g-roster.toml tracked
candidate line: sessions/master-g1x-roster.toml tracked
candidate line: sessions/master-g2-roster-roster.toml tracked
```

D. Committed tests and docs: `test_the_host_roster_snapshot_stays_off_the_branch_and_the_branch_ignores_it` asserts the git-store relay line exactly (tests/test_pstack_kitchen.py:18577-18584, `destination=.git/pstack-roster-g2.toml thread={amp_thread(2)}` and the bounded-wait text). `test_take_master_under_the_git_store_installs_a_roster_uploaded_to_the_repos_git_dir` (:19842) installs from `.git` only, removes the upload, and covers neither-present giving `did not arrive`. The docs state the bounded wait:

```
$ grep -n "pstack-roster-g\|KITCHEN_TAKEOVER_WAIT_S" skills/pstack-kitchen/SKILL.md skills/pstack-kitchen/references/master.md
SKILL.md:    repo-relative `.git/pstack-roster-g<n>.toml` (the next orb keeps its store
SKILL.md:    its turn. `take-master` waits at most `KITCHEN_TAKEOVER_WAIT_S` (30 s by
master.md:299:  path=... destination=.git/pstack-roster-g<n>.toml thread=...`; make that
master.md:301:  end your turn. `take-master` waits at most `KITCHEN_TAKEOVER_WAIT_S` (30 s by
```

E. take-master on a fresh orb (clone and store at other absolute paths, `fresh_orb` + `sync --branch` + `take-master`, PSTACK_KITCHEN_ROSTER pointing at an empty path):

```
old store: /tmp/tmp8h4_3t6v/state/pstack/kitchen/runs/demo  old repo: /tmp/tmp8h4_3t6v/repo
new store: /tmp/tmp8h4_3t6v/master2-orb/runs/demo  new clone: /tmp/tmp8h4_3t6v/master2-orb/work
pair.json git_root on the new orb: /tmp/tmp8h4_3t6v/repo
--- take-master roster lines   (upload at <new clone>/.git/pstack-roster-g2.toml)
roster: /tmp/tmp8h4_3t6v/master2-orb/runs/demo/sessions/master-g2-roster.toml did not arrive from the previous master; this orb keeps its own roster
installed: False bytes equal: False
upload removed: False

$ python3 tests/probe_042.py Probe042.test_control_upload_at_the_old_git_root
--- control: upload at the old git_root, new store path /tmp/tmp6gw35utt/master2-orb/runs/demo
roster: installed /tmp/tmp6gw35utt/orb2-roster.toml from /tmp/tmp6gw35utt/master2-orb/runs/demo/sessions/master-g2-roster.toml
installed: True upload removed: True
OK
```

F. Brief commands 1-3, 5-9 at e9dc6fe0 in the scratch, with PSTACK_KITCHEN_ROLE=verifier:

```
$ python3 tests/run.py -k roster
26 tests, 0 failed, 10s wall with 4 jobs
$ python3 tests/run.py -k take_master
16 tests, 0 failed, 13s wall with 4 jobs
$ python3 tests/run.py -k gitstore
56 tests, 0 failed, 21s wall with 4 jobs
$ python3 scripts/check-pstack.py
65 skills checked; all 48 previous skills covered
exit=0
$ python3 tests/spec_gen.py --check
ok: tests/SPEC.md is current, 0 missing
exit=0
$ uv run --frozen ruff format --check
111 files already formatted
exit=0
$ uv run --frozen ruff check
All checks passed!
exit=0
$ uv check --frozen
All checks passed!
exit=0
```

G. Prove block: setup and the pstack behavioral gate (the whole suite, brief command 4), which covers 010's and 039's tests:

```
$ python3 .../kitchen.py --repo <scratch> setup
setup: ok (1 commands, log .../logs/setup-20261123T212426-4192.log)
$ python3 .../kitchen.py --repo <scratch> gate pstack behavioral --role verifier --base a90bb4130393b864176dbeded9bb216fcb5f255c
pstack behavioral: pass
  exit 0 554.3s python3 tests/run.py  log: .../logs/20261123T212440/pstack-behavioral-0.log
(log tail) 2022 tests, 0 failed, 553s wall with 4 jobs
```

Range commits of the unit (review-fix step 2): 72d188ab (ignore line and its test), af4f0758 (relay destination, `.git` lookup, the relay-line assertion and the new take-master test), e9dc6fe0 (docs and SPEC.md). Each fix commit carries its test.

## Flows driven

- pstack (kitchen CLI): rotate-master under the git store, then sync and take-master from a fresh orb clone with its own state home and store path. The kitchen.sh command line ran against the fake amp (Evidence A, E).
- pstack-docs: read the SKILL.md and master.md lines against the relay text the command prints (Evidence D); no other user-facing flow.

## Folded Acceptance

none

## Not covered

- A real `upload_thread_file` between two Amp threads. The probe wrote the file at the destination by hand, as the committed test does.
- The test file mask/bound standing check: the range's new test code adds no regex masks, and its subprocesses go through `as_thread`/`run_sh`, which carry `timeout=120`.

## Lessons

none
