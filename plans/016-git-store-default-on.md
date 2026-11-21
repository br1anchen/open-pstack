# Plan 016: git-store-default-on

round: 1
supersedes: none
contract: 2
store: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups
risk: escalated (default store contract for new runs)

## Goal

A new kitchen under Amp threads, or on an Amp orb under any multiplexer (Brian, 2026-11-21), with `PSTACK_STORE` unset starts on the git store, while one on a local machine (Herdr, Rex or subagents off an orb) keeps the directory store and publishes its complete initial run to `kitchen/<slug>` on origin; `PSTACK_STORE=bundle` opts out; existing stores keep their backend; an unavailable origin refuses rather than silently going local; and the docs say so, with this repository's publication authorization. Proven by tests that inspect the bare origin's tree and the store's backend.

## Design

Authoritative detail: `advice/011-git-store-default-on-c1.md` section "Unit 011 should finish a durable initialization". Summary:

- Selection at the kitchen init boundary, by the resolved multiplexer's capability (`remote_roles`, mux/__init__.py L57-85, amp.py L671-690), passed to core init as data, never by mutating `os.environ`. Rules: Amp + unset/empty → git; `git` → git; `bundle` → local; any other value is an error; an existing local store (including one with no `store_backend`) stays local with a line naming `store-to-git`; an existing or partial git store stays git. Herdr, Rex and subagents keep today's default on a local machine; on an Amp orb (the round names the one signal that says so, read once at init, for example the orb's environment marker) they default to git as Amp does (Brian, 2026-11-21). An explicit `PSTACK_STORE` always wins.
- No `origin`: refuse before registering or publishing, naming `PSTACK_STORE=bundle`. Push refused: keep the pending local git state, exit non-zero, and the same `init` resumes publication later (today both return 0 with an empty remote, consultant P1).
- One publication at the end of init, after the kitchen's own writes (skill stamp, alternate choice, standing orders; consultant P2), through the existing publish and scan; a rejected initial snapshot creates no remote branch (the generated `.gitignore` push moves into that first publish).
- Before the first push, print the backend, a credential-free repository id, the branch and its audience (anyone who can read the repository; `PSTACK_STORE=bundle` for a local run); on success name the published commit.
- Tests: pin bundle in `AmpKitchenTests.mux_env`; `AmpGitKitchenTests` keeps its override; fix `ready_path` (tests/test_pstack_kitchen.py L14189-14202) to read the store's backend; the migration fixture at L17388 sets `bundle`.
- Docs: fold brief 009 (AGENTS.md) in, without its "unset selects local" clause; skills/pstack-kitchen/SKILL.md and references/master.md describe the default for Amp kitchens and the opt-out generally; AGENTS.md alone carries this repository's authorization to publish `kitchen/*` and that a push to main needs the owner's approval.

## Alternatives and trade-offs

| Option | Pros | Cons | Chosen |
| --- | --- | --- | --- |
| Refuse unavailable git and resume the same pending store | A successful run has its remote source | Offline users must choose bundle | yes |
| Fall back to bundle automatically | More inits succeed | Durability depends on a transient error; ambiguous pushes unsafe | no |
| Default only for new Amp kitchens | No surprise for shared-checkout users | Two backends documented | yes |
| Default everywhere | One rule | Publishes previously local workflows unasked | no |

## Steps

1. Selection, refusal and resumable first publication.
   playbook: feature
   - one table-driven test over: unset under Amp (git, origin tree holds the complete init data), explicit bundle, unset under Herdr (local), legacy local store retained, unknown value refused, no origin refused, push refused then retried successfully, secret in the initial snapshot refused with no remote branch.
2. Fixture pins.
   playbook: refactoring
   - mux_env pin, ready_path, migration fixture; the suite passes unchanged otherwise.
3. Docs.
   playbook: docs
   - as above; brief 009's Acceptance (no "opt-in" for this repo in AGENTS.md) holds; tests/SPEC.md.

## Verification

A test init under Amp with PSTACK_STORE unset leaves a bare origin whose `kitchen/<slug>` tree holds pair.json with the skill stamp and the standing orders; the full suite passes.

## Risks

- Lands last (after 014 and 015), since the default relies on their hand-over safety.
- Changes init's exit status on push failure: existing tests change intentionally.

## Open questions

none

## Disagreements resolved

none
