08:56 006 step 1 committed (2e817eea: git.pin, pins at step, finish, verify), gate running; step 2 (bundle carries keep refs) in a scratch worktree meanwhile
09:00 006 step 2 committed (71562db: bundles carry keep refs), step 1 gate running; step 3 (land-check missing-evidence diagnostics) next in the scratch worktree
09:08 006 step 1 first gate failed one kitchen test that step 2 had already adjusted; amended step 1 with that test change (78ecd2c), rebased steps 2 and 3 (0c21a31, 6921d9f), rerunning step 1 gate
09:18 step 1 78ecd2c98: a step, a finish and a verification pin the commits they name
09:19 006 step 1 recorded (78ecd2c); step 2 (0c21a31) in its gate; step 3 (6921d9f) committed in the scratch worktree
09:29 step 2 0c21a3180: every message carries the run's evidence refs and the receiver keeps them
09:30 006 step 2 recorded (0c21a31); step 3 (6921d9f) in its gate, then the report
09:40 step 3 6921d9f08: land-check says which evidence of an unverified unit is gone and how to prove it again
