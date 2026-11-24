09:53 step 1 committed: kitchen stop is the trio's (out of scope), so kitchen_core.master.stop guards then delegates; close guards all role threads
10:03 step 1 b2d218d42: stop and close refuse snoozed roles before any change
10:07 step 2 committed: early dispatch guard; keep-or-rotate decided by a read-only classification since profiles are stamped only by classify
10:18 step 2 bbbdf9396: dispatch refuses a snoozed sidekick before classify and the sweep
10:20 step 3 committed: SnoozeRefused, undo text, candidates docstring (thread_rows already limits it to the master's thread)
10:30 step 3 43e07850b: snooze --done refusals name the undo and raise SnoozeRefused
10:33 step 4 committed: help line, docs; HELP golden in tests/test_pstack_pair.py needed the same line (outside Scope, flagged)
10:44 step 4 11d2874b6: help lists snooze; docs say every send refuses and how to undo
