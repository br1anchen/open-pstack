- 002 audit low: commands/stop.py:173 the pair's own stop no-thread path (an Amp.start entry before its first prompt) has no test.
- 017 joo lows: gitstore.py:1197 stale origin/HEAD never repaired after the fallback (extra fetch + ls-remote per sync); gitstore.py:1196 fallback also runs after a transient fetch failure of a trunk that still exists.
- 020 joo low: verify_packet.py:392 accepted_amendments shows Amendments from a review whose verdict is revise or reject; context_section checks verdict == accept.
- (023, joo low) send.py:181 `took_a_brief` keys on a brief dispatched to the generation, so a new generation busy on a discuss PLAN makes a later send poll up to PAIR_ROTATE_READY_S before refusing.
- (023, joo low) no test that a working sidekick whose generation already took a brief is refused at once without polling; a regression of `took_a_brief` to always-False would only add 60s waits.

- 027/029 (joo, low): nest.take_anchor's `old != thread` check is redundant; guard against a non-dict `.anchor` in pair.json.

- 004 (joo, low/note): move_checkout could pass whether the target is checked out instead of asking git again in diverged and recovery_recipe; make `exclude` a required argument of git_bundle.

- 030 (joo, low): the per-command context memo also caches a failed read; consider not caching a None so a transient amp failure does not blind later readers in the same command.
