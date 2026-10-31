# Master: landing

The detail behind step 8 of [master.md](master.md).

   A malformed verdict (no command output under Evidence, or a status other
   than clean, reject or inconclusive) covers nothing in land-check, and
   `revise` refuses it; read it before a fix brief.
   A squash or rebase step touches no profile, so before the landing brief,
   `kitchen.sh verify <store> --landing` gives the stack tip a fresh verifier
   of the escalated class: every behavioral gate the stack touches, and each touched
   profile's `landing` commands (the deadline adds 3 x their measured time, or
   ten minutes for a landing gate never measured, and prints that part), from where it
   leaves trunk, and the main flows its briefs describe. land-check reports
   its result without requiring it. A landing verdict older than HEAD is
   reported as `landing verdict <file> at <short>, N commits behind HEAD` when
   its head is in HEAD's history, and as `not in HEAD's history (rebased or
   another branch)` when it is not (a `--head` branch, a verdict from before a
   rebase).
   A unit with no clean verdict of its own is credited to the newest clean
   landing verdict whose range holds it (`covered: unit NNN by the clean landing
   verdict <file>`), so a later landing does not uncover units an earlier one
   proved; the newest landing verdict alone sets the `landing verification:`
   line and the manifest warning. A unit that rejected and has a verified fix
   is covered by it too (`covered: unit NNN by its verified fix, unit MMM`, MMM the fix with the clean verdict):
   land-check reads the fix brief's `- verdict:` line and its `- original brief:`
   line (a unit's own fix covers the unit of that line, when the rejected
   verdict named it) or its `- unit brief:` lines (a landing fix covers each
   unit listed, when the rejected verdict was a landing verdict), which `revise`
   writes. The fix needs a clean verdict, or a fix of its own that has one, within
   5 links; a cycle, a missing line or a gone verdict covers nothing. A fix that
   carries one unit's acceptance does not cover the other units its verdict named.
   The check that a rebased unit was not reverted later in the stack
   compares patches without context lines, so a commit that deletes an
   identical line elsewhere in the same file reads as a revert and leaves the
   unit uncovered (`land-check: note: unit NNN's commits match by patch-id but
   <sha> reverts them`). That is a known limitation, and it fails closed:
   `verify --landing` covers the unit.
   When the stack does not leave trunk where the merge-base says (stacked on
   another branch), `verify --landing --parent REF` records that parent in
   `pair.json` (`.landing.parent`); later landings start there while it is an ancestor of the head being
   verified and print `landing base: recorded parent <ref> (<short>)` (otherwise
   they say it is no longer an ancestor and use the trunk merge-base);
   `--parent` must be an ancestor of the head (also with `--base`, which still
   wins for the call) and is recorded only once the scratch worktree exists,
   `--parent none` clears it, and land-check reports a recorded parent that is
   no longer an ancestor of HEAD. The landing packet's `Stack` and `Goals` hold only the units the range
   contains, and land-check's `covered:` uses the same test: a unit landed before
   the verdict's base is neither (`land-check: note: unit NNN landed before the
   base of landing verdict ...; verify it on its own`). `verify --landing --head REV` proves that commit or branch
   instead of HEAD (the scratch checks out REV, the base comes from the same
   rule against REV, and the verdict's `range:` ends at REV); land-check
   counts that verdict for the units whose heads, or patch-ids, it holds. A unit rebased or cherry-picked into the stack counts as
   covered when every commit of its range has a patch-id among the stack's
   and no later stack commit reverts it (a commit with the patch-id of the
   inverse of the unit's net change or of one of its commits), so a later edit of
   its lines keeps it covered and a revert uncovers it (`land-check: note: unit
   NNN's commits match by patch-id but <short> reverts them`) (`covered: unit NNN (rebased as <short>)`; a range that
   holds a merge is never matched, and says so); `verify` accepts a rebased
   unit in its `--covers` list the same way and prints a `note:`; a squashed one prints a note and
   stays uncovered until the landing verification proves the stack. A unit built
   on top of a landing verdict's tip says it is newer than the verdict, and a
   verdict whose base does not resolve says so; the `(squashed?)` note is for
   neither. When the stack was rebased after the last landing verdict,
   land-check and `verify --landing` print `warning: dependency manifests
   changed since the verified base: <files>` for the kitchen's
   `escalate.manifests` that differ between the verdict's base
   (read, with its head, from its packet's `range:`, the same range coverage
   uses) and the current base, whichever side changed them; a verdict verified
   on a base inside the stack does not warn of the stack's own lower commits; re-verify the landing before trusting it. The landing's playbook (`opening-a-pr` or
   `shipping`) goes as far as `[landing].mode` allows. It stops before
   shipping's merge step (merging bottom-up, then freezing writes while the
   stack lands) whatever the playbook says, because `[landing].mode` caps it
   and the kitchen never merges. The human reviews and
   lands.
