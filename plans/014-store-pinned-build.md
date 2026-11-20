# Plan 014: store-pinned-build

round: 1
supersedes: none
contract: 2
store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
risk: escalated (build delivery contract to every role orb)

## Goal

Under Amp threads, a role orb installs pstack from the exact clean open-pstack commit the master executes, fetched from a ref that contains it, instead of a `pstack-build.tgz` attachment; the master re-pins and the role reinstalls when the master's executing build changes; a dirty or unfetchable build still travels as a content-addressed archive. Proven by tests that run the generated installer against a local bare pstack source repository distinct from the application repository and assert installed bytes and links.

## Design

Authoritative detail: `advice/011-git-store-default-on-c1.md` section "Unit 012 should pin the executable build, not the application checkout". Summary:

- Data: two build descriptions. `git`: source repository URL (credential-free), full commit OID, a fetchable ref containing it, and the build content identity (today's hash over the shipped paths). `archive`: content identity and attachment. Recorded where `seen.build` is recorded today (mux/amp.py L1074-1112); `.sidekick.delivered` stays about brief sections.
- Source: resolved from the executing skills directory and its installer (`SKILLS`, `INSTALLER`, amp.py L593-627), not from the application's origin. A cached install keeps a verified source description outside the shipped tree (beside it under `~/.local/share/pstack-builds/<id>/`).
- Eligibility for `git`: the shipped paths (`skills/`, `scripts/pstack-sync.py`) match the commit, including untracked additions and deletions (a dirty README does not count); and a freshly fetched advertised ref's tip contains the commit (`git fetch` into a separate pstack source cache, then `merge-base --is-ancestor`), checked for a new or changed build and for each new recipient's bootstrap; unchanged sends reuse the delivered record. A commit only inside a `code/*.bundle` is not eligible. Never push a branch to manufacture a pin.
- Receiver install line: fetch the named ref into a cache, resolve the exact OID, `git archive` it into `pstack-builds/<id>.partial`, verify the shipped content identity, write a completion marker, rename into place, then run `scripts/pstack-sync.py`. A moved or deleted ref fails visibly and asks for the archive; it never installs latest main.
- Re-pin when the build the master executes changes (not when the application HEAD moves).

## Alternatives and trade-offs

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| Verified git pin + content-addressed archive fallback | No routine attachment; dirty and unpushed builds exact | Provenance and receiver verification code | yes |
| Require the build to be on main | Simple | Couples experiments to main pushes Brian must approve | no |
| ls-remote tip match only | Cheap | Misses ancestors; stale tracking refs (consultant P6) | no |

## Steps

1. Build description and eligibility on the master side.
   playbook: feature
   - In mux/amp.py, compute the build description (git or archive) with the eligibility rules above; record it in place of the bare build id; tests in tests/test_pstack_kitchen.py AmpKitchenTests over: clean pin on a branch tip, ancestor pin, stale tracking ref after the remote branch was deleted, pin reachable only via a `kitchen-code/*` branch (eligible), commit only in a code bundle (archive), dirty shipped file (archive), unrelated dirty file (git).
2. Receiver install and re-pin.
   playbook: feature
   - Generate the git install line and keep the archive line as fallback; tests execute the generated line against a local bare source repo with a different application repo, assert installed bytes, links and the completion marker; an incomplete cache is reinstalled; a deleted ref fails with the archive request; a build change in a kept role session sends the new description once.
3. Docs.
   playbook: docs
   - skills/pstack-kitchen/SKILL.md "Amp threads" (the bullets on the build and bootstrap), skills/pstack-pair/references/amp-sidekick.md where it names pstack-build.tgz, AGENTS.md "Kitchen on Amp orbs" sentence on the build; tests/SPEC.md regenerated.

## Verification

The generated installer, executed in a test against a bare source repo, installs the pinned commit's exact shipped bytes; the full suite passes; in this run, the next role started after this lands receives no pstack-build.tgz attachment when the master's build is a clean pushed commit.

## Risks

- A ref can vanish between check and fetch: receiver verification and the archive retry cover it.
- Roles on an older build receive a description they do not understand: the first message to such a role keeps the archive.

## Open questions

- Where exactly is the master's skills source (this checkout via .agents/setup links, or a builds cache)? The sidekick confirms from the code.

## Disagreements resolved

none
