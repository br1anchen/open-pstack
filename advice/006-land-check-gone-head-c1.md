# Advice 006-c1: Preserve verification evidence across rebases

status: answered
consult: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/consults/006-land-check-gone-head-c1.md
kind: design
head: a90bb4130393b864176dbeded9bb216fcb5f255c
tree: clean
scratch: /home/user/workspace/repo/.amp/in/orb-backup/consult-scratch/runs-kitchen-followups/006-land-check-gone-head-c1 (removed before sending)

## Answer

Choose **(c), with explicit transport and receiver retention**, to prevent losing evidence. Keep a missing-evidence refusal and give it a recovery command that actually works. Do not make patch-ID membership on trunk discharge verification, and do not convert a missing head into a pass on the master's say-so. For an already damaged run, reverify the missing unit's Acceptance at a live head through the existing `verify <current-unit> --covers <missing-unit> --base <live-base>` path. The current plain `verify --landing` suggestion cannot recover that association.

There is a separate contract problem. The consult asks that a pass mean the verified code is what lands. **Current land-check does not guarantee that, even with every object present.** It checks historical unit coverage and reports landing freshness as a note. I reproduced a pass after changing behavior in an unverified commit after the landing verdict. If that stronger invariant is required, the brief must also require a clean verification of the actual final candidate, with a recorded target commit/tree checked again at the landing action. Evidence retention preserves the inputs to a check; it does not turn the existing historical check into a final-tip gate.

The Fix Root Causes principle changes the choice here. Store files keep commit names, but Git transport and reachability do not preserve the objects those names require. Fix that loss before adding a second, incomplete representation of Git history in step logs.

## Grounding

Read the consult, draft brief 006, standing orders, gap 5 in `notes/kitchen-gaps.md`, and the source and tests below at the head above. This was a design investigation, not implementation. No shared-tree changes, commits, pushes, or deployments were made.

- `units.py:148-196` reads dispatch and report/step heads. `unit_head` returns an empty string when the stored name no longer resolves. `units.py:244-321` performs inclusion and rebased matching using old objects, forward patch IDs, inverse patch IDs, and the inverse net diff.
- `landing.py:304-337,397-444,516-571` has two different proof paths. A well-formed clean verdict explicitly naming a unit needs no resolvable Git range. A landing verdict normally names only `landing`, so its coverage is recomputed from Git. An unresolved landing head never enters the ranges, and an unresolved unit head never matches any range.
- `verify_packet.py:220-237,354-397` chooses landing-covered briefs by the same inclusion function. Missing heads therefore remove their Acceptance from a newly generated landing packet too. A new landing verdict alone is not a repair.
- `landing.py:447-513` uses `landed_as` only for explanatory output. It checks patch-ID membership or a squash patch, without the revert scan used by `rebased_in`. `say_landing_verification` calls it after coverage decisions, not as proof for an uncovered unit.
- `tests/test_pstack_kitchen.py:12427-12645,12953-13040` tests rebased coverage, conservative revert checks, rewritten trees and landed diagnostics while old objects are still available. Those tests do not simulate losing the old objects in a new orb.
- `bundle.py:20-57,110-182,279-286` limits individual store files to 1 MiB, selects only HEAD and scratch tips for Git transport, and fetches their refs. It does not enumerate a new keep-ref namespace automatically.
- `steplog.py:14-43,117-133` has a shared five-column row format. Everything after column four is the summary. Appending patch IDs as new trailing columns is a schema change, not a harmless extension.

Ran `advice/006-land-check-gone-head-c1-probe.py` from consultant scratch with:

```sh
GIT_CONFIG_NOSYSTEM=1 PROBE_ROOT="$scratch" python3 "$store/advice/006-land-check-gone-head-c1-probe.py"
```

It exited 0 after these assertions against real disposable Git repositories and the existing kitchen commands. The verifier in the recovery case was the repository's fake Herdr hook, not a real agent proving application behavior.

```text
RETAINED REF: old unit and landing head survive GC; original CLI still passes
TRANSPORT GAP: ordinary bundle carries HEAD but not the retained evidence ref
EXPLICIT TRANSPORT: evidence tip fetched under a ref survives receiver GC
MISSING EVIDENCE: same landed code, old objects pruned, land-check exits 2
FRESH LANDING GAP: even a new verdict at the rewritten tip cannot associate the missing unit
CONTRACT ASYMMETRY: an explicitly named clean unit verdict passes with its objects missing
RECOVERY PATH: explicit --covers with a live report and --base sends the missing unit Acceptance at the current head; fake clean verdict clears it
STRICT CONTRACT GAP: changed unverified tip still passes with a behind-HEAD note
LANDED DIAGNOSTIC: landed_as returns rebased even after trunk reverts the change
SIZE LIMIT: >1 MiB store file omitted; >1 MiB git.bundle transported
```

The missing-evidence probe verifies the failure before and after deterministic reflog expiry and GC. The code on the rewritten branch is unchanged across that boundary. A retained ref prevents the failure. A separate fresh receiver proves that retaining the ref locally is insufficient unless its tip enters the bundle.

### Keep the original evidence, not only fingerprints

Use immutable, run-scoped keep refs whose names include the full object ID. A mutable `refs/pstack/keep/<unit>` can abandon the previous verified version after the next rewrite. Keep all versions while the run can still ask about them.

The kitchen owns selection of evidence. The generic Git transport owns carrying and retaining selected refs. No pair-core import of kitchen modules is needed.

- Pin a successful step's full commit before announcing it, and pin the resolved report head when finishing a unit. Pin the verification range's full base/head and the unit heads/bases used to construct its packet before launching the verifier. Pinning only the landing head misses old unit heads that matched by patch-ID but are not its ancestors. Pinning only at verification also misses a rewrite made between the last step and verification.
- Include this run's evidence tips in ordinary role bundles and master handoffs. This is a justified exception to advice 004's HEAD-plus-scratch rule for ordinary messages. These tips are dependencies of the store's reports and verdicts, not arbitrary unrelated branches. The proposed all-local-head master handoff alone does not carry private keep refs.
- Import evidence into the receiver's same immutable keep namespace. Do not rely solely on `refs/pstack/in/<sender>/head`, which a later message replaces. Repeated import of the same OID is a no-op. A failed required object import must not report a successful evidence transfer.
- Use fetchable bundle prerequisites, as advice 004 explains. `--not --remotes` can exclude evidence known only to a sender-only remote. A receiver that fetches origin cannot necessarily obtain it.
- Preserve the distinction between no verdict and unavailable evidence. Print which unit/report/verdict head is missing and how to restore its bundle or reverify its Acceptance. Do not silently claim that no clean verdict exists when the actual loss is its range objects.

The 1 MiB limit is not a reason to reject this design. It applies to store members, not the separately added `git.bundle`. The probe carried a Git bundle larger than 1 MiB while omitting a store log larger than 1 MiB. Total attachment and relay limits still matter. Store a raw evidence bundle as an ordinary local-store file and it will hit the per-file limit; carry evidence through the existing Git member instead.

The git-store backend needs its own durability check. `gitstore.py:954-1039` force-updates a writer-generation code ref or replaces that generation's fallback bundle; `fetch_code` fetches those current refs/bundles. Neither is an immutable archive of every verified head. A local keep ref does not survive a new orb under that backend. Before claiming both backends fixed, give the git-store path immutable published evidence refs or retained evidence bundles that a fresh reader fetches. That transport work can be a separate bounded step. Do not silently publish a new namespace or broaden the run's approved audience as part of the local fix.

### Recover an old run with fresh proof

No choice can reconstruct a lost commit's diff from its SHA. A patch-ID cache added now cannot help unit 017 if its objects were already absent when the cache was introduced.

The existing `--covers` path can request new proof without rewriting the old report or inventing a replacement SHA. Use a current unit with a real report head, explicitly cover the lost unit, and pass a live base if old dispatch bases no longer order or resolve. `units.py:324-357,498-560` allows a covered unit with no usable head and notes that inclusion was not checked. `verify_packet.py:443-471` still includes its Goal, Acceptance and Verify commands at the current head. The probe exercised that exact path with a fake verifier.

For the real recovery, require a real verifier to prove those obligations at the current candidate. The master records why it selected the current head and reviews the recovery evidence. Preserve the old report and verdict. Do not synthesize a historical clean unit verdict or edit the old report to make the association appear proven. A new recovery brief is appropriate when no existing current unit owns that proof.

### Do not promote landed diagnostics into verification

Keep patch-ID matching as an explanation or candidate match. Do not remove a unit from `unverified` merely because trunk's history contains its patches. The probe's `landed_as` returns `rebased` after trunk has already reverted the change. Stable patch IDs also ignore whitespace; advice 004's runnable probe shows different Python programs sharing a stable patch ID. Caching those IDs preserves the same ambiguity.

A historical `landed` fact can mean that a change once entered trunk, even if trunk later reverted it. That is different from saying the current candidate contains its verified behavior. Report the former without using it to assert the latter. A review note naming a landed commit is a pointer to inspect, not replacement proof.

If the final-tip invariant is part of 006, add it as an explicit acceptance item. Require a clean final verification associated with the actual candidate commit or exact recorded tree, including the relevant unit obligations, and check the candidate has not changed before landing. A rebase onto different upstream content changes that tree and needs fresh verification. Do not try to infer that verification from patch-ID membership. This would intentionally change today's behind-HEAD and no-landing-verdict behavior, so it deserves its own contract tests and cannot be described as only a missing-object fix.

## Options

| Option | Cost, correctness and limits |
| --- | --- |
| **(c) Immutable evidence refs plus transport, recommended** | Retains the original source for diff, tree, merge and inverse-patch checks. Needs ref capture and both sender/receiver handling. Uses more Git objects, but Git deduplicates them and the local-store 1 MiB limit does not apply. Prevents new loss; old damage needs recovery. Preserves existing coverage semantics rather than inventing stronger ones. |
| (a) Persist patch IDs at steps | Small data, but changes a shared row schema and does not describe the whole dispatched range, merge status, inverse net diff, or landing tree. A passing step is not a landing verification. Cannot repair already missing data or prove final code equivalence. |
| (a) Persist fingerprints at verification | Better boundary than steps for a cache. To preserve current behavior it needs full range identity, ordered forward IDs, inverse IDs, inverse net diff, merge markers, and exact trees where equality is used. A single list of IDs loses the current revert and merge protections. Still not a proof that the target contains the verified behavior. More duplicate state than retaining Git objects. |
| (b) Accept missing evidence with a warning | Smallest edit, but gives pass to unknown state. Reject under the consult's correctness requirement. An accepted review proves neither object availability nor current inclusion. |
| (d) Freeze covered units in a verification receipt | Reasonable if the master deliberately chooses historical coverage semantics. Record full unit head and verification range/tree with the packet before verification, then use it only with the matching clean verdict. Compact and portable, but it records what was proved then, not what lands now. It does not justify patch-ID retirement or silently backfill old receipts from guesses. |
| (d) Fresh explicit `--covers` verification | Recommended recovery for already lost objects. Uses the existing command and packet model. Costs a verifier run and requires actual current-head evidence, but makes no claim to have recovered the old diff. |

## Risks

Retaining objects grows local repositories and attachments. Keep refs are GC roots, so their lifecycle must outlast the last reader of a run. Do not add automatic cleanup in the same small fix; removal after close needs an explicit archive-retention policy. Pinning only latest heads or carrying only one mutable writer ref reintroduces the bug.

The strict final-tip invariant is wider than the current tests and implementation. None of (a), (b), or (c) alone provides it. The smallest behavior-preserving 006 is evidence retention, precise missing-evidence diagnostics, and the existing recovery path. A compact receipt is a smaller alternative only if historical coverage, not final-tip equivalence, is the intended contract.

Suggested acceptance should force old objects out of a fresh receiver, not just remove branch names while reflogs retain them. Cover old unit head gone, old landing head gone, missing base, later rewrite under the same unit name, repeated import, failed prerequisite fetch, and two successive bundles from the same sender followed by receiver GC. Keep the existing revert/merge tests unchanged. Add the recovery-packet assertion so the missing unit's Acceptance cannot disappear from the fresh proof request. The probe includes the required mechanism controls, but is not an implemented fix or a full test-suite run.

## Questions

None for the human. The master can choose the bounded retention/recovery fix. If it retains the consult's stronger final-tip requirement, the brief must name that behavior change separately rather than expect cached patch IDs to establish it.
