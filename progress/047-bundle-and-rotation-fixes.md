11:16 step 1 committed: reply keeps a local file the reply lacks
11:27 step 1 3e3b1df3e: a reply deletes nothing by omission
11:29 step 2 committed: fetch_origin helper; unreachable origin gives baseline ''
11:40 step 2 dabf4638c: unreachable origin: self-contained bundle; bounded origin fetches
11:42 step 3 committed: sends with keep refs use origin_baseline
11:53 step 3 42f676e9c: evidence survives an ordinary send
11:59 step 4 committed: rotation_required cleared at READY only; rotate allowed while bootstrap pending with no READY
12:11 step 4 gate failed on function size of rotate(); extracted require_rotatable
12:25 steer s1 applied: require_rotatable raises RotateRefused (code 5) instead of Die
12:36 step 4 ce25a435f: READY clears the rotation flag; an unready bootstrap can be rotated again
12:41 step 5 committed: recorded_start falls back to pair.json kind/start_args, says so; two core behaviour expectations changed
12:52 step 5 a1a7cca9f: spawn without a host roster starts the run's recorded entry
12:53 step 6 committed: docs
13:04 step 6 973660250: docs for the bundle and rotation fixes
