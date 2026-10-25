> Historical session artifact, imported 2026-10-25. Read [the handoff](../../HANDOFF.md) first for current status and remote execution instructions. Host-local paths and workflow commands below describe the original run.

# Advice 019: context-migration (plan response)

status: object
plan: plan-019.md
head: 8b3d4b3f6c46a7ace805186216fc07652b8efac9
tree: clean
scratch: none

## Position

Keep the command family, focused module, heading-only cuts and staged validation. Before implementation, fix the reproducible baseline and rerun contract, enumerate proof transformations, and distinguish source anchors from destination anchors. These are requirements for the promised recovery and losslessness, not a request for broader migration features. The master can incorporate them as concrete brief amendments.

## Design critique

The plan follows A10's central boundaries: the tool performs deterministic moves and correspondence checks; setup chooses the module boundaries and reviews routing semantics. Reusing the selector implementation is appropriate. Foundational Thinking and Model the Domain support making a migration a function of an immutable baseline and explicit edits, rather than reconstructing old inputs from partially migrated files. Both principle skills were read earlier this session.

The existing reader supplies useful structure but not byte fidelity by itself. playbooks.py:402-407 uses text.split('\n'); heading_tree at :284-298 trims trailing blank lines; text_of at :446-447 rejoins selected ranges. Preserve original byte slices and separator ownership for migration output instead of feeding normalized excerpts to the proof. Exact file hashes and the map's whitespace-normalized 12-character digests are different contracts.

The CLI's delegation to context.py is consistent with playbooks_command. Keep resolution and rendering as pure functions over an explicit baseline/file map, with Git reads and file writes at the boundary. This permits preview and staged check to share the same render without accidentally consulting the live checkout during proof.

## Trade-offs

Heading-only moves are a useful first version. Heading promotion is acceptable as a declared syntactic transformation, provided only parsed heading markers change, never fenced examples. Nested cuts are justified for Rust under Typed evidence, but generated replacement stubs must be a separate proof category. Keeping references to the same effective canonical target matters more than preserving their textual spelling.

A mandatory baseline commit plus per-file hashes is simpler than supporting arbitrary uncommitted baseline blobs and recovery caches. It also aligns with a reviewed migration committed with its result. If arbitrary working inputs are needed later, add an explicit retained baseline artifact; do not infer it by searching Git history for a content hash.

## Objections

- Design/check baseline: sha256 is an integrity checksum, not a Git object locator. This checkout reports object format sha1, and ls-files shows a 40-character blob id for playbooks.py. Specify a baseline commit/tree reference in the migration, resolving each source and rewritten file with git show <base>:<path>, then verify its declared SHA-256. A bare --base override should be required when no recorded baseline exists, not silently change provenance. Validate the baseline before the first apply. Second apply and partial recovery must reconstruct outputs from that same immutable input, without first refusing because the source is now an index. Test an absent baseline and a missing source object with useful failures.
- Apply input state: record preconditions for every modified existing file, including rewrite targets such as kitchen.toml, not just source documents. Derive all expected post-images from the baseline. Before any write, require each planned file to equal its exact pre-image, exact post-image, or declared absent state. Complete only that recognizable partial state. Preserve unrelated changes outside the write set. An interrupted multi-file rename sequence is recoverable, not atomic; document that distinction and test it with a fixed failure hook. Detect a stale staged input before writing rather than overwriting an edit made after preview.
- Proof transformations: the plan already generates requires lines, parent and index forwarding stubs, and copied reference definitions. These cannot pass a proof allowing only heading shifts and link rewrites. Specify disjoint output categories: original byte ranges with explicit heading/link edits, generated index/prerequisite text, and intentional supporting-definition copies with provenance. Account for every original range once in its canonical location and every generated or duplicated range explicitly. Validate prerequisite paths exist in the result, reject cycles or define them, and emit links relative to each destination, not the repo-root paths in requires. Test a retained prefix, shared definition and nested extraction together.
- Anchor correspondence: retain a mapping from old source anchor to new destination anchor. A duplicate heading's suffix is document-scoped; after extraction its old foo-1 may become foo. Keeping heading lines in the index preserves the old anchor only if the original heading order and renderer semantics remain intact; it does not make foo-1 valid in the new file. Compute destination anchors after rendering, or add explicit supported destination anchors. Test duplicate titles, heading-marker examples in fences, and a local #fragment in moved text targeting another moved section. Rebase references by their resolved target in the migration graph, not just by directory arithmetic.
- Schema example: writes currently appears after [[keep]], making it a field of the final keep entry under TOML rules rather than a root field. Move it before the array tables or give it an explicit table. Define whether it is a literal complete may-write set or a glob authorization boundary. The safest first version uses exact paths; all outputs and rewrite targets must be enumerated and unique. Pin the documented sample with a schema-loading test.

## Risks

The review and policy source guarantee needs an explicit consumer check. Current kitchen.py validates policy structure and stores its source string, but that does not itself prove the referenced section still holds the rule. Use the new reference resolver to verify named policy/review targets, and exercise representative extraction, not only kitchen.py validate. A source pointer to a forwarding stub is structurally valid yet weaker evidence than a pointer to its canonical rule.

Inventory cannot place references it never recognizes into unresolved. State which reference syntaxes are supported and which file types were scanned, report recognized ambiguous references, and mark unscanned/unsupported cases as coverage limitations. Keep unrelated pre-existing broken references separate. A glob or code-generated path cannot always be interpreted as a document link. External references stay outside the local proof.

The setup sentence 'run inventory only when asked or when it flags a candidate' is circular. Inventory is read-only and may run during maintain; migration is opt-in when asked or when its concrete candidate proposal is accepted within the session's authorization. Do not add a redundant confirmation when the owner already approved the exact migration. Update the routing and overlay drafts as explicit semantic edits after the mechanical move, then run consumer checks again. Draft status should be explicit; do not rely on applies=[] alone to mean unactivated unless the overlay schema defines that behavior.

## Answers

The byte proof needs more than heading promotion as its allowed change list. Link rewrites remain allowed, but generated prerequisite/pointer blocks and duplicated reference definitions require explicit provenance categories rather than being waved through as original text. Reject unenumerated changes. Preserve CRLF, final-newline and separator bytes unless the reviewed plan explicitly allows their conversion. Keep heading ancestry as semantic metadata and dependency requirements; physical promotion to # necessarily changes the rendered outline.

The sidekick can answer the reader-end question from code. I observed that Heading.end is trimmed at :298, so it is not literally the next heading's line offset for owning trailing blank bytes. Also heading paths are not made duplicate-free: select at :426-433 deliberately raises for multiple suffix-path matches. Schema 1 can reject an ambiguous selector rather than promise every duplicate heading is independently movable. Unmoved duplicates still need anchor preservation.

Read the plan, accepted A10, current playbooks.py parser/selector/resolver, kitchen.py command and policy-source entry points, git status/head, object format and a tracked blob id. No tests, builds, prototypes or shared-tree writes ran. The future ChenChess pilot remains a separate run and is not evidence established by this plan review.
