# Brief 044: roster-relay-repo

playbook: review-fix
timebox: 60
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack, pstack-docs
verify: batch by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/044-roster-relay-repo.md

## Goal

Under the git store, take-master finds the uploaded `.git/pstack-roster-g<n>.toml` in this orb's own work repository, found from the store worktree on this orb, never from `git_root` or `cwd` in the published pair.json, which hold the old master's paths; and the roster snapshot's LOCAL rule and its `.gitignore` line match exactly the same paths.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- anything else

## Steps

Start from unit 042's head: `git checkout -B kitchen-followups-042 e9dc6fe01a2c069c49f9e30fc59c5c036061158d` (on origin as kitchen-code/kitchen-followups/unit-042).

1. `master.py` (~line 559, where `restore_roster` calls `bundle.repo_of(store)` for the upload): under the git store, find the work repository's `.git` from the store worktree on this orb, `git -C <store> rev-parse --path-format=absolute --git-common-dir` (the store is a worktree of the work repository), and look for `pstack-roster-g<n>.toml` there. Leave `bundle.repo_of` and its other callers unchanged. Test: extend 042's git-store row so pair.json's `git_root` and `cwd` name a path that does not exist on the new orb (the old master's checkout) while the roster sits in the real repository's `.git`: take-master installs it and removes the upload; add the row where the old path still points at a different repository that holds no upload, and assert the real one is used. Then regenerate tests/SPEC.md. Targeted check: `python3 tests/run.py -k roster` and `python3 tests/run.py -k take_master`.

2. `gitstore.py`: the LOCAL rule's regex and its ignore line still differ (verdict 042-v1: `sessions/master-gx-roster.toml` and `sessions/master-g-roster.toml` are ignored by `sessions/master-g*-roster.toml` but classed MASTER). Make them agree by widening the regex to what the glob matches, `sessions/master-g[^/]*-roster\.toml`, and keep the ignore line `sessions/master-g*-roster.toml` (a `*` in .gitignore does not cross `/`). Test: extend `test_gitstore_ignores_exactly_the_local_class` with one table of paths (`sessions/master-g2-roster.toml`, `sessions/master-g12-roster.toml`, `sessions/master-gx-roster.toml`, `sessions/master-g-roster.toml`, `sessions/other-roster.toml`, `sessions/master-g2-handoff.md`, `sessions/a/master-g2-roster.toml`) asserting, for each, that `classify` says LOCAL exactly when `git check-ignore` against `gitignore_text()` in a temp repo says ignored. Targeted check: `python3 tests/run.py -k gitstore`.

## Context

- joo run 2026-11-23T21-21-49-638Z-cd5a1769 on fc6d096b..e9dc6fe0 (unit 042): master.py:559 medium, `repo_of(store)` reads the published pair.json's old paths, so on an orb with a different checkout path the upload is missed and take-master quietly keeps its own roster.
- verdicts/042-roster-relay-path-v1.md: reject on the glob/regex mismatch (step 2); its note confirms the repo lookup (step 1).
- Brian (2026-11-23): fix the relay path before landing 010/039; this is the receiving side of that fix.

## Acceptance

- Under the git store, with pair.json naming the old master's checkout path, take-master installs the roster uploaded to this orb's repository `.git` and removes the upload.
- For every path in step 2's table, `classify` says LOCAL exactly when git ignores it.
- 010's, 039's and 042's tests pass; spec_gen --check passes.

## Verify

```bash
python3 tests/run.py -k roster
python3 tests/run.py -k take_master
python3 tests/run.py -k gitstore
python3 tests/run.py
python3 scripts/check-pstack.py
python3 tests/spec_gen.py --check
uv run --frozen ruff format --check
uv run --frozen ruff check
uv check --frozen
```

## Forbidden

- The roster's contents never appear in output, events, decisions or the branch.
- No `time.sleep` in tests, and no elapsed-time asserts.
- Never end your turn while `kitchen.sh step` or `finish` prints `still running`; rerun it in the foreground.
- No push to main and no PR.

## Report

Work the Steps in order. End each in a commit and record it with
`kitchen.sh step /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <sha> "<what it does>"`: it runs the touched
profiles' fast gates and the repo's policy check, and records the step only
when they pass. When it exits 4 with `gates: still running`, the step is not
recorded: run the same command again at once, in the foreground, and keep
going until it records; never end your turn on it. On a failure, fix it in a
new commit and run `kitchen.sh step` again with that commit; it tells you when
two failures in a row make this a blocked report. At each step boundary run `kitchen.sh notes /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups` and fix
any open blocking note first, as a fixup commit recorded with `--resolves`.
Append one progress line per change of approach with
`kitchen.sh progress /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups "<line>"`. When any `kitchen.sh` command prints
`PAUSE:`, stop at that safe point: commit what is verified and report
`partial` with the next step. Before reporting done, run the review skills the
kitchen's `review.self` names on your diff. Write the report file named above
using the pstack-pair report template, then run
`~/.agents/skills/pstack-kitchen/scripts/kitchen.sh finish /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups <report path>`
and do what it prints. For a done report, finish first reruns the Verify
commands above at the report's head:, with the checkout at that commit and
nothing uncommitted, and sends the report only when every one exits 0; exit 4
means they are still running, so run the same finish again at once.

## Lessons

Trouble this area met in earlier runs (kitchen.py lessons). Follow each; the
verifier checks them and reports each one held or hit.

- L012 (gate-fail pstack "fast uv check --frozen", layer gate, in skills, active 2026-10-26T22:54:02Z): Check every Optional a stdlib call returns (re.match, spec_from_file_location, spec.loader) for None before use, and run uv check --frozen before kitchen.sh step.
