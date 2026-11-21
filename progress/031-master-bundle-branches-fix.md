05:51 step 1 b18310c86: a hand-over imports every tip before it changes a ref, the checkout or the store
05:52 step 1 recorded (b18310c86: import before move, all or nothing); next: step 2 (sender's branch never dropped); fast gate takes ~12 min per step, so later steps are developed in a scratch worktree meanwhile
06:05 step 2 6536f9ba3: a hand-over keeps the sender's working branch when the checkout cannot move
06:05 step 2 recorded (6536f9b); steps 3-5 are committed in a scratch worktree and are fast-forwarded here one at a time; next: record step 3 (8fa4fa1)
06:15 step 3 8fa4fa15d: a checkout on another branch is never reset onto the sender's head
06:16 step 3 recorded (8fa4fa1); next: record step 4 (6cbfaf7), then step 5 (6557389)
06:38 step 4 5647fa09d: a hand-over bundle stays small when origin is slow or down
06:39 steps 3 and 4 recorded (8fa4fa1, 5647fa0; step 4's first gate failed the function-size guard on git.run's 9 parameters, fixed in 5647fa0); next: record step 5 (6dcc8e3), then the report
06:49 step 5 6dcc8e38a: apply prunes a sender's older bundle ref sets
