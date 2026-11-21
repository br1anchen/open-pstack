07:06 022 step 1 committed (28b1cb0, migrated_at + local rule for pre-migration threads), gate running; next: step 2 nest --discover, developed in a scratch worktree meanwhile
07:16 step 1 28b1cb015: a thread from before store-to-git archives by the local rule
07:17 022 step 1 recorded (28b1cb0); step 2 (fe439ae nest --discover) in its gate; step 3 docs (fe7c54c) next
07:31 step 2 9ececb639: nest --discover records a run's unnested threads and its anchor
07:31 022 step 2 recorded (9ececb6; first gate failed a stale SPEC, fixed); step 3 docs (ece5785) in its gate, then the report
07:41 step 3 ece578550: document the migrated-thread archive rule and nest --discover
