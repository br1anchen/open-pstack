# Review 051: reply-names-its-base

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/051-reply-names-its-base.md
head: b131308e833d2eb556d3383db39fb484daac25a7
engine: joo 2026-11-24T16-54-00-542Z-d6ba01b6

## Amendments

none

## Checked

- joo 2026-11-24T16-54-00-542Z-d6ba01b6 (648f31bf..b131308e): 7/7 files, 2 lanes; 1 medium (found by both lanes), 6 lows. verdicts/051-reply-names-its-base-v1.md: clean for 047 049 051 (2051 tests, 0 failed; mutation probe shows the table catches the wrong base).
- Master read commands/relay.py:56-57 at b131308e: settle copies the relayed bundle only to `sent_path(thread)`, never under `sent/<thread>/<id>.tgz`. The medium holds.

## Blocking

- mux/amp.py:391/1415 (medium): a steer relay packs its own bundle with a fresh id and relay settle keeps it only as the legacy sent_path. The role records that id as applied and names it as `synced`; on the master `kept_sent_path` returns None, so every reply after a relayed steer is unbased: its own deletions are dropped and its own edits report as conflicts.

## Non-blocking (fixed in 052)

- bundle.py:950: an unknown base flags every changed own-role file as a conflict; limit the fallback to deletions.
- bundle.py:542: drop merge_store's unused `based` parameter.
- amp.py:396: pruning kept bundles races; tolerate a missing file. Give the kept-bundle count its own knob or say why it shares PSTACK_BUNDLE_KEEP.
- amp.py:595: `took_a_brief` raises DamagedStore on a damaged generation; `role_is_idle` should report "in use" rather than stop the archive sweep.
- test_pstack_kitchen.py:15844: add the row sidekick without generation, dispatch generation 0.
- SKILL.md:303: reword the stale "since ... newer" clause.

## Accepted (master g4, 2026-11-24)

The blocking findings were fixed in unit 052 (f117480b). Verdict 052-relay-keeps-bundle-id-v1 is clean for units 047, 049, 051 and 052, with no findings.
