# Brief 009: git-store-default

playbook: docs
timebox: 30
commit: yes, one commit per step, each recorded with kitchen.sh step
plan: none
risk: routine
profiles: pstack-docs
verify: gates by routine verifier
review-engine: joo
steps: none
standing: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/standing-orders.md
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/009-git-store-default.md

## Goal

AGENTS.md says that kitchens in this repository run on the git store (`PSTACK_STORE=git`) by default, that pushing `kitchen/*` run branches to origin (br1anchen/open-pstack) is allowed without asking, and that a push to `main` still needs the owner's explicit authorization.

## Scope

may write:
- AGENTS.md
- docs/agents/amp-orb-environment.md

must not write:
- skills/**

## Steps

1. In `AGENTS.md`, section "Kitchen on Amp orbs" (the paragraph starting "Sustained or multi-unit work runs with the `pstack-kitchen` skill"), replace the clause that calls the git store opt-in ("the run store may instead live on a git branch, which is opt-in: set `PSTACK_STORE=git` at `init`, and note that ...") with: in this repository a kitchen runs on the git store by default (set `PSTACK_STORE=git` at `init`, or move a running local store with `kitchen.sh store-to-git`); the owner allows pushing `kitchen/*` run branches to origin, so a role publishes there without asking; the branch publishes the run's briefs, reports and thread ids to anyone who can read the repository; a push to `main` still needs the owner's explicit authorization in the thread. Keep the sentence about the local store in `.amp/in/orb-backup/pstack/` as the fallback when `PSTACK_STORE` is unset. In the Layout bullet that calls it "the opt-in git store" (about line 8), say "the git store, this repository's default for kitchens (`PSTACK_STORE=git`, ...)" and keep the rest. If `docs/agents/amp-orb-environment.md` lists the environment variables setup provides and `PSTACK_STORE` belongs there, add one line saying it is set per run at `init`; otherwise leave the file alone and say so in the report. Commit, `kitchen.sh step`.

## Context

- Owner decision, 2026-11-20, relayed in the master thread: publish the `kitchen/*` run branches to br1anchen/open-pstack and make the git store the documented default, so pushing run branches is allowed; pushes to main still need his approval.
- The skill text (skills/pstack-kitchen/SKILL.md, references/master.md) stays general: the git store remains opt-in for other repositories. Do not change it.

## Acceptance

- `rg -n "opt-in" AGENTS.md` prints nothing: AGENTS.md no longer describes the git store as opt-in for this repository.
- AGENTS.md states, in the "Kitchen on Amp orbs" section, that `kitchen/*` branch pushes to origin are allowed and that a push to `main` needs the owner's explicit authorization.
- `python3 scripts/check-pstack.py` passes.

## Verify

```bash
python3 scripts/check-pstack.py
rg -n "kitchen/\*" AGENTS.md
test -z "$(rg -n "opt-in" AGENTS.md)"
```

## Forbidden

- No change under skills/.

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
