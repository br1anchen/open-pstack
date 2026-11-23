# Brief 042: roster-relay-path

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
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/042-roster-relay-path.md

## Goal

The roster relay that unit 039 prints under the git store names a repo-relative upload destination, as `steer`'s relay does, and take-master on the next master finds the roster there whatever the store's absolute path on that orb is; the snapshot's `.gitignore` line matches exactly the paths the LOCAL rule classifies.

## Scope

may write:
- skills/pstack-kitchen/scripts/kitchen_core/master.py
- skills/pstack-pair/scripts/pstack_core/gitstore.py
- skills/pstack-kitchen/SKILL.md
- skills/pstack-kitchen/references/master.md
- tests/test_pstack_kitchen.py
- tests/test_pstack_core.py
- tests/SPEC.md

must not write:
- anything else

## Steps

Start from unit 039's head: `git checkout -B kitchen-followups-039 fc6d096b77343386ce9f5ee5b45320ab6abb35f7` (on origin as kitchen-code/kitchen-followups/unit-039).

1. `master.py` `relay_roster` (line ~534): the destination is repo-relative, `.git/pstack-roster-g<next>.toml` (the next generation's number), the pattern of `upload_to = f'.git/pstack-relay-{msg_id}.tgz'` in pstack_core/mux/amp.py; `path=` stays the absolute snapshot on this orb. `restore_roster` (line ~544), git store only: when `{store}/sessions/master-g<n>-roster.toml` is missing after `wait_for_old_master`, look for `<repo root>/.git/pstack-roster-g<n>.toml` (the repo root as the kitchen already finds it for the store's work repository; do not add a new way); if it is there, move it to the snapshot path (`os.replace`, same filesystem not assumed: copy then unlink if replace fails with EXDEV) and continue with 039's install/keep/differ logic; if neither exists, 039's `did not arrive` line. The `.git/` file never enters the store branch. Tests: extend 039's (git store, local store) x (present, missing) table with a git-store row where the roster sits only at `.git/pstack-roster-g<n>.toml` and the store lives at a different absolute path than the old master's: it is installed and the `.git` file is gone; and assert the printed relay line's destination is exactly `.git/pstack-roster-g<n>.toml`. Targeted check: `python3 tests/run.py -k roster` and `python3 tests/run.py -k take_master`.
2. `gitstore.py` (line ~97): the ignore line matches the regex exactly, `sessions/master-g*-roster.toml`, and the RULES test adds a path such as `sessions/other-roster.toml` that is MASTER and not ignored. Targeted check: `python3 tests/run.py -k gitstore`.
3. Docs: the SKILL.md and master.md sentences 039 wrote, and the relay's second printed line, name the `.git/pstack-roster-g<n>.toml` destination and say plainly that take-master waits at most `KITCHEN_TAKEOVER_WAIT_S` (30 s by default) for the old master's turn, so the old master uploads the file before it ends its turn or the next master keeps its own roster (verifier note, verdict 039-v1). Regenerate tests/SPEC.md. Targeted check: `python3 scripts/check-pstack.py` and `python3 tests/spec_gen.py --check`.

## Context

- Brian (2026-11-23): fix the 039 relay path before landing it.
- joo run 2026-11-23T20-14-04-846Z-ae62c835 on c40b7b0c..fc6d096b: master.py:539 medium (absolute destination on the old orb), gitstore.py:97 low (ignore glob wider than the LOCAL regex). followups.md, section 039.
- Report 039 and steer 039-s1; review 010 (verdict revise, the reason 039 exists).

## Acceptance

- Under the git store rotate-master's relay line names `destination=.git/pstack-roster-g<n>.toml`.
- take-master under the git store installs a roster found only at `.git/pstack-roster-g<n>.toml`, with the store at a different absolute path than on the old orb, and removes the `.git` file; with neither present it prints `did not arrive` and exits 0.
- The `.gitignore` line matches exactly the LOCAL rule's paths.
- A committed test asserts the git-store relay line (the verdict 039-v1 note: none did), and the docs and that line state the bounded take-over wait.
- 010's and 039's tests pass; check-pstack and spec_gen --check pass.

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
